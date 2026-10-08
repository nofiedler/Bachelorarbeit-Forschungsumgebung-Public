"""Local archive transfer and isolated, key-free recalculation views."""
from contextlib import closing
import hashlib
import hmac
import json
import re
from uuid import UUID, uuid4
import zipfile

from fastapi import Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from starlette.formparsers import MultiPartParser
from starlette.concurrency import run_in_threadpool

from . import exchange
from .analysis_reproduction import computation_compatible
from .analysis_presentation import cached_presentation_file, presentation_cache
from .artifacts import atomic_file
from .domain import AnalysisRun, canonical
from .import_progress import TransferProgress
from .register import GateError, Register


def latest_raw_export(register, analysis_id):
    """Show only a raw export containing the current explicit journal scope."""
    jobs = register.connection.execute(
        'SELECT * FROM exchange_job WHERE analysis_id=? ORDER BY rowid DESC',
        (str(analysis_id),)).fetchall()
    for row in jobs:
        job = dict(row)
        if job['status'] != 'ready':
            return job
        path = register.settings.staging / 'packages' / (job['id'] + '.zip')
        try:
            with zipfile.ZipFile(path) as archive:
                manifest = json.loads(archive.read('manifest.json'))
            if manifest.get('raw_data_schema') == 'research-series-raw-data-v1':
                return job
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            return {**job, 'status': 'failed', 'error': 'Die Exportdatei ist nicht lesbar. Bitte ein neues Rohdatenpaket erstellen.'}
    return None


def routes(app, settings, render, form):
    import_progress = TransferProgress()
    # Imported ZIPs are published read-only. A small cache avoids rehashing a
    # multi-GB archive for each inline chart; any filesystem change invalidates it.
    from functools import lru_cache
    def fingerprint(path):
        item = path.stat()
        return (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
    @lru_cache(maxsize=16)
    def verified_at(path, stamp):
        timeline = []
        manifest = exchange.verify(path, timeline=timeline)
        if fingerprint(path) != stamp:
            raise GateError('Importiertes Paket wurde während der Prüfung verändert')
        return manifest, timeline[0]
    def verified_import(path):
        if path.is_symlink():
            raise GateError('Importiertes Paket darf kein Link sein')
        return verified_at(path, fingerprint(path))

    @app.get('/analyses/{analysis_id}/code.zip')
    def analysis_code(request: Request, analysis_id: UUID):
        from .analysis_code_export import code_archive
        from .evidence_views import read_artifact
        with closing(Register(settings, readonly=True)) as register:
            analysis = register.get(analysis_id, AnalysisRun)
            binding = analysis.selected_inputs['_m7']
            snapshot = json.loads(read_artifact(register, UUID(binding['snapshot_id'])))
            result = json.loads(read_artifact(register, UUID(binding['outputs']['analysis.json'])))
        return Response(code_archive(snapshot, result, analysis.id), media_type='application/zip',
            headers={'Content-Disposition': 'attachment; filename="auswertungsskript-' + str(analysis_id) + '.zip"'})

    @app.get('/analyses/{analysis_id}/raw-data/status')
    def raw_data_status(request: Request, analysis_id: UUID):
        with closing(Register(settings, readonly=True)) as register:
            analysis = register.get(analysis_id, AnalysisRun)
            job = latest_raw_export(register, analysis_id)
        return render(request, 'analysis_raw_export.html', {'analysis': analysis, 'raw_export': job})

    @app.post('/analyses/{analysis_id}/raw-data')
    async def raw_data_export(request: Request, analysis_id: UUID):
        data = await form(request)
        if set(data) != {'idempotency_key'} or not data['idempotency_key']:
            raise GateError('Export benötigt einen eindeutigen Auftragsschlüssel')
        with closing(Register(settings)) as register:
            exchange.enqueue(register, analysis_id, data['idempotency_key'])
        return RedirectResponse('/analyses/' + str(analysis_id) + '#submission-downloads', status_code=303)

    @app.get('/packages/import/progress/{token}')
    def upload_progress(token: UUID):
        return import_progress.get(str(token))

    @app.get('/packages')
    def packages(request: Request):
        with closing(Register(settings, readonly=True)) as register:
            jobs = [dict(row) for row in register.connection.execute('SELECT * FROM exchange_job ORDER BY rowid DESC')]
        imports = []
        for path in sorted((settings.staging / 'imports').glob('*.zip')):
            with zipfile.ZipFile(path) as archive:
                imports.append(json.loads(archive.read('manifest.json')))
        return render(request, 'packages.html', {'jobs': jobs, 'imports': imports})

    @app.post('/packages/export')
    async def export(request: Request):
        data = await form(request)
        with closing(Register(settings)) as register:
            exchange.enqueue(register, UUID(data['analysis_id']), data['idempotency_key'])
        return RedirectResponse('/packages', status_code=303)

    @app.get('/packages/{job_id}/download')
    def download(request: Request, job_id: UUID):
        with closing(Register(settings, readonly=True)) as register:
            job = register.connection.execute('SELECT * FROM exchange_job WHERE id=?', (str(job_id),)).fetchone()
        if not job or job['status'] != 'ready':
            raise GateError('Studienpaket ist noch nicht bereit')
        path = settings.staging / 'packages' / (str(job_id) + '.zip')
        exchange.verify(path)
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != job['sha256']:
                raise GateError('Paketdatei nach Export verändert')
        filename = ('rohdaten-' + job['analysis_id'] + '-' if request.query_params.get('raw_data') == 'true' else 'study-') + str(job_id) + '.zip'
        return FileResponse(path, media_type='application/zip', filename=filename)

    @app.post('/packages/import')
    async def upload(request: Request):
        if request.headers.get('origin') != str(request.base_url).rstrip('/') or request.headers.get('sec-fetch-site') in ('cross-site', 'none'):
            raise GateError('Import benötigt dieselbe lokale Herkunft')
        async def bounded_stream():
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > exchange.MAX_ZIP + 65536:
                    raise GateError('Upload überschreitet 2 GiB')
                yield chunk
        data = await MultiPartParser(request.headers, bounded_stream(), max_files=1, max_fields=2).parse()
        try:
            csrf = data.get('csrf', '')
            if not isinstance(csrf, str) or not csrf or not hmac.compare_digest(csrf, request.cookies.get('research_csrf', '')):
                raise GateError('CSRF-Nachweis fehlt')
            if len(list(data.multi_items())) != len(data) or set(data) != {'csrf', 'archive'}:
                raise GateError('Import benötigt genau ein Archiv und CSRF')
            token = request.headers.get('x-import-progress')
            report = import_progress.start(str(UUID(token))) if token else None
            root = settings.staging / 'uploads'
            root.mkdir(exist_ok=True)
            path = root / (str(uuid4()) + '.zip')
            try:
                with path.open('xb') as target:
                    size = 0
                    while chunk := await data['archive'].read(1024 * 1024):
                        size += len(chunk)
                        if size > exchange.MAX_ZIP:
                            raise GateError('Upload überschreitet 2 GiB')
                        target.write(chunk)
                manifest = await run_in_threadpool(exchange.import_archive, settings, path, progress=report)
            except Exception:
                if report: report('Import fehlgeschlagen')
                raise
            finally:
                path.unlink(missing_ok=True)  # Only this request's temporary upload.
        finally:
            await data.close()
        location = '/packages/imports/' + manifest['package_id']
        if token:
            report('Import geprüft – Ansicht wird geöffnet', 1, 1, 'Schritt')
            return Response(status_code=204, headers={'HX-Redirect': location})
        return RedirectResponse(location, status_code=303)

    @app.get('/packages/imports/{package_id}')
    def imported(request: Request, package_id: UUID):
        path = settings.staging / 'imports' / (str(package_id) + '.zip')
        manifest, timeline = verified_import(path)
        with zipfile.ZipFile(path) as archive:
            result = json.loads(archive.read('analysis/analysis.json'))
            snapshot = json.loads(archive.read('analysis/input.json'))
        from .analysis_report_data import report_context
        directory = presentation_cache(settings, result)
        presentation = json.loads((directory / 'manifest.json').read_bytes())
        report = report_context(result, snapshot=snapshot, register=timeline, snapshot_id=timeline.snapshot_id,
            output_base='/packages/imports/' + str(package_id) + '/presentation/', manifest=presentation,
            cache_key=directory.name)
        return render(request, 'package.html', {'manifest': manifest, 'result': result, **report,
            'presentation_manifest': presentation,
            'compatible': computation_compatible(manifest['source_binding']),
            'recalculated': (settings.staging / 'derivations' / (str(package_id) + '.json')).exists()})

    @app.get('/packages/imports/{package_id}/code.zip')
    def imported_code(request: Request, package_id: UUID):
        from .analysis_code_export import code_archive
        path = settings.staging / 'imports' / (str(package_id) + '.zip')
        manifest, _ = verified_import(path)
        with zipfile.ZipFile(path) as archive:
            snapshot = json.loads(archive.read('analysis/input.json'))
            result = json.loads(archive.read('analysis/analysis.json'))
        return Response(code_archive(snapshot, result, manifest['analysis_id']), media_type='application/zip',
            headers={'Content-Disposition': 'attachment; filename="auswertungsskript-' + manifest['analysis_id'] + '.zip"'})

    @app.get('/packages/imports/{package_id}/download')
    def imported_archive(request: Request, package_id: UUID):
        path = settings.staging / 'imports' / (str(package_id) + '.zip')
        manifest, _ = verified_import(path)
        return FileResponse(path, media_type='application/zip',
            filename='studienpaket-' + manifest['analysis_id'] + '-' + str(package_id) + '.zip')

    @app.post('/packages/imports/{package_id}/recalculate')
    async def recalculate(request: Request, package_id: UUID):
        await form(request)
        path = settings.staging / 'imports' / (str(package_id) + '.zip')
        result = exchange.recalculate(path)
        root = settings.staging / 'derivations'
        content = canonical(result).encode()
        destination = root / (str(package_id) + '.json')
        if destination.exists():
            if destination.read_bytes() != content:
                raise GateError('Bestehende Ableitung weicht ab; Original bleibt erhalten')
        else:
            atomic_file(root, destination.name, content)
        return RedirectResponse('/packages/imports/' + str(package_id), status_code=303)

    @app.get('/packages/imports/{package_id}/outputs/{filename}')
    def imported_output(request: Request, package_id: UUID, filename: str):
        path = settings.staging / 'imports' / (str(package_id) + '.zip')
        manifest, _ = verified_import(path)
        if not re.fullmatch(r'[A-Za-z0-9_-]+\.(csv|json|png|svg|pdf)', filename) or 'analysis/' + filename not in manifest['files']:
            raise GateError('Unbekannte Ausgabedatei')
        with zipfile.ZipFile(path) as archive:
            content = archive.read('analysis/' + filename)
        # Only signature-checked PNG is displayed inline; active archive formats stay downloads.
        if request.query_params.get('inline') == 'true' and filename.endswith('.png'):
            if not content.startswith(b'\x89PNG\r\n\x1a\n'):
                raise GateError('Ungültige PNG-Grafik')
            return Response(content, media_type='image/png', headers={'X-Content-Type-Options': 'nosniff'})
        # Never render arbitrary archived HTML or SVG as an active same-origin page.
        return Response(content, media_type='application/octet-stream',
                        headers={'Content-Disposition': 'attachment; filename="' + filename.replace('"', '') + '"'})

    @app.get('/packages/imports/{package_id}/presentation/{filename}')
    def imported_presentation(request: Request, package_id: UUID, filename: str):
        if not re.fullmatch(r'[A-Za-z0-9_-]+\.(csv|json|png|svg|pdf|md)', filename):
            raise GateError('Unbekannte Darstellungsdatei')
        path = settings.staging / 'imports' / (str(package_id) + '.zip')
        manifest, timeline = verified_import(path)
        from .analysis_timeline_export import FILENAMES, timeline_files
        if filename in FILENAMES:
            with zipfile.ZipFile(path) as archive:
                snapshot = json.loads(archive.read('analysis/input.json'))
            content, mime = timeline_files(snapshot, timeline, timeline.snapshot_id)[filename]
            return Response(content, media_type=mime, headers={
                'Content-Disposition': 'attachment; filename="' + filename + '"',
                'X-Content-Type-Options': 'nosniff'})
        key = request.query_params.get('v')
        if not key:
            with zipfile.ZipFile(path) as archive:
                result = json.loads(archive.read('analysis/analysis.json'))
            key = presentation_cache(settings, result).name
        file, mime = cached_presentation_file(settings, key, filename, data_hash=manifest['input_hash'])
        content = file.read_bytes()
        if request.query_params.get('inline') == 'true' and filename.endswith('.png'):
            if not content.startswith(b'\x89PNG\r\n\x1a\n'):
                raise GateError('Ungültige PNG-Grafik')
            return Response(content, media_type='image/png', headers={'X-Content-Type-Options': 'nosniff'})
        return Response(content, media_type='application/octet-stream', headers={
            'Content-Disposition': 'attachment; filename="' + filename + '"',
            'X-Content-Type-Options': 'nosniff'})

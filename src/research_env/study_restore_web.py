"""Bounded UI upload and explicit URL namespaces for restored instances."""
from contextlib import closing
import hmac
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import Request
from fastapi.responses import RedirectResponse, PlainTextResponse
from starlette.formparsers import MultiPartParser

from . import study_restore
from .import_progress import RestoreProgress
from .exchange import MAX_ZIP
from .register import GateError, Register


class RestoredApp:
    def __init__(self, settings): self.settings, self.apps = settings, {}
    async def __call__(self, scope, receive, send):
        try:
            identity = str(UUID(scope['path_params']['copy_id']))
            with closing(Register(self.settings, readonly=True)) as r:
                row = r.connection.execute("SELECT * FROM study_restore WHERE id=? AND status='ready'", (identity,)).fetchone()
            if row is None: raise ValueError('Import nicht verfügbar')
            prefix = '/restored/' + identity
            if identity not in self.apps:
                from .web import create_app
                self.apps[identity] = create_app(study_restore.copy_settings(self.settings, identity), prefix=prefix,
                    restored_study=row['study_id'])
            # Child sees local paths; links/redirects are explicitly namespaced by create_app.
            child = dict(scope, path=scope['path'][len(prefix):] or '/', root_path='', path_params={})
            child['raw_path'] = child['path'].encode()
            await self.apps[identity](child, receive, send)
        except (ValueError, OSError) as exc:
            await PlainTextResponse(str(exc), status_code=404)(scope, receive, send)


def routes(app, settings, render, form, *, restored=False):
    progress = RestoreProgress(settings)
    if not restored: app.mount('/restored/{copy_id}', RestoredApp(settings))

    @app.post('/studies/import')
    async def upload(request: Request):
        if restored: raise GateError('Weitere Sicherungen über die Hauptübersicht importieren')
        if request.headers.get('origin') != str(request.base_url).rstrip('/') or request.headers.get('sec-fetch-site') in ('cross-site', 'none'):
            raise GateError('Import benötigt dieselbe lokale Herkunft')
        async def bounded_stream():
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_ZIP + 65536: raise GateError('Upload überschreitet 2 GiB')
                yield chunk
        data = await MultiPartParser(request.headers, bounded_stream(), max_files=1, max_fields=1).parse()
        try:
            csrf = data.get('csrf', '')
            if not isinstance(csrf, str) or not csrf or not hmac.compare_digest(csrf, request.cookies.get('research_csrf', '')):
                raise GateError('CSRF-Nachweis fehlt')
            if len(list(data.multi_items())) != len(data) or set(data) != {'csrf', 'archive'}:
                raise GateError('Import benötigt genau eine Sicherungsdatei')
            root = settings.staging / 'uploads'
            root.mkdir(exist_ok=True)
            path = root / (str(uuid4()) + '.zip')
            try:
                with path.open('xb') as target:
                    size = 0
                    while chunk := await data['archive'].read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_ZIP: raise GateError('Upload überschreitet 2 GiB')
                        target.write(chunk)
                with closing(Register(settings)) as register: identity = study_restore.enqueue(register, path)
            finally: path.unlink(missing_ok=True)
        finally: await data.close()
        return RedirectResponse('/study-imports/' + identity, status_code=303)

    @app.get('/study-imports/{identity}')
    def status(request: Request, identity: UUID):
        with closing(Register(settings, readonly=True)) as register:
            row = register.connection.execute('SELECT * FROM study_restore WHERE id=?', (str(identity),)).fetchone()
        if not row: raise GateError('Importauftrag fehlt')
        return render(request, 'study_import.html', {'copy': dict(row), 'progress': progress.get(dict(row)) if row['status'] in ('queued', 'restoring') else None})

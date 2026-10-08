#!/usr/bin/env python3
"""Standalone entry shipped as auswerten.py; no application database required."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
import zipfile


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_name(name):
    if (not name or PurePosixPath(name).is_absolute() or '\\' in name or ':' in name or
            any(part in ('', '.', '..') for part in name.split('/'))):
        raise ValueError('Unsicherer Dateipfad: ' + name)
    return name


def verify_code(root):
    raw = (root / 'manifest.json').read_bytes()
    manifest = json.loads(raw)
    if manifest.get('format') != 'research-analysis-code-v1' or raw != canonical(manifest).encode():
        raise ValueError('Unbekanntes oder nichtkanonisches Codepaket')
    hashes = {}
    for name, item in manifest['files'].items():
        path = root / safe_name(name)
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError('Codedatei fehlt oder ist verlinkt: ' + name)
        content = path.read_bytes()
        if sha(content) != item['sha256'] or len(content) != item['byte_count']:
            raise ValueError('Codedatei wurde verändert: ' + name)
        hashes[name] = item['sha256']
    hashes['manifest.json'] = sha(raw)
    expected = ''.join(value+'  '+name+'\n' for name, value in sorted(hashes.items())).encode()
    if (root / 'checksums.sha256').read_bytes() != expected:
        raise ValueError('Prüfsummenverzeichnis des Codepakets stimmt nicht')
    if sys.version_info[:2] != (3, 13):
        raise ValueError('Dieses Codepaket benötigt Python 3.13')
    if os.name != 'posix':
        raise ValueError('Dieses Codepaket benötigt macOS oder Linux (unter Windows z. B. WSL)')
    for name, version in manifest['requirements'].items():
        try:
            installed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            installed = 'nicht installiert'
        if installed != version:
            raise ValueError(f'{name}: benötigt {version}, vorhanden {installed}. Bibliotheken aus requirements.lock installieren.')
    return manifest


def verify_package(path, binding):
    """Stream-check every file; never extract or execute archived software."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024**3:
        raise ValueError('Studienpaket fehlt, ist verlinkt oder größer als 2 GiB')
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 250000 or sum(item.file_size for item in entries) > 16 * 1024**3:
            raise ValueError('Entpackgrenze des Studienpakets überschritten')
        names = set()
        for item in entries:
            safe_name(item.filename)
            if item.filename.casefold() in names:
                raise ValueError('Doppelter ZIP-Eintrag')
            names.add(item.filename.casefold())
            limit = 1024**3 if item.filename == 'registers/records.jsonl' else 256 * 1024**2
            if (item.is_dir() or stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG) or
                    item.flag_bits & 1 or item.extra or item.file_size > limit or
                    item.file_size > max(item.compress_size, 1) * 1000):
                raise ValueError('Unzulässiger ZIP-Eintrag: ' + item.filename)
        raw = archive.read('manifest.json')
        manifest = json.loads(raw)
        if (manifest.get('format') != 'research-study-package-v1' or manifest.get('package_version') != 1 or
                raw != canonical(manifest).encode()):
            raise ValueError('Unbekanntes Studienpaketformat')
        required = {'analysis/input.json', 'analysis/analysis.json', 'registers/records.jsonl',
                    'analysis/acknowledgement.json', 'freeze/manifest.json', 'README.md'}
        files = manifest['files']
        if not required <= set(files) or set(archive.namelist()) != set(files) | {'manifest.json','checksums.sha256'}:
            raise ValueError('Pflichtdateien fehlen oder zusätzliche Paketdateien vorhanden')
        if manifest['analysis_id'] != binding['analysis_id'] or manifest['input_hash'] != binding['input_hash']:
            raise ValueError('Das Studienpaket gehört zu einem anderen Analysestand')
        hashes = {}
        for name, expected in files.items():
            with archive.open(name) as stream:
                value = hashlib.file_digest(stream, 'sha256').hexdigest()
            if value != expected:
                raise ValueError('Dateiprüfsumme stimmt nicht: ' + name)
            hashes[name] = value
        hashes['manifest.json'] = sha(raw)
        checksums = ''.join(value+'  '+name+'\n' for name, value in sorted(hashes.items())).encode()
        if archive.read('checksums.sha256') != checksums:
            raise ValueError('Prüfsummenverzeichnis des Studienpakets stimmt nicht')
        return manifest, json.loads(archive.read('analysis/input.json')), json.loads(archive.read('analysis/analysis.json'))


class TimelineReader:
    def __init__(self, artifact, events):
        self.snapshot_id = artifact.id
        self.artifact = artifact
        self.events = events

    def get(self, identity, kind):
        from research_env.domain import Artifact
        if kind is not Artifact or identity != self.snapshot_id:
            raise ValueError('Unbekannter Eingabebeleg')
        return self.artifact

    def for_run(self, kind, run_id):
        from research_env.domain import Event
        if kind is not Event:
            raise ValueError('Zeitansicht liest ausschließlich Ereignisse')
        return self.events.get(run_id, ())


def package_timeline(path, manifest, snapshot):
    from research_env.domain import AnalysisRun, Artifact, Event, digest
    ids = {row['run_id'] for row in snapshot['rows'] if row.get('run_id')}
    artifacts, events, event_ids, seen, analysis = {}, {}, {}, set(), None
    with zipfile.ZipFile(path) as archive, archive.open('registers/records.jsonl') as records:
        for line in records:
            record = json.loads(line)
            kind, payload = record['kind'], record['payload']
            identity = payload['id']
            if identity in seen:
                raise ValueError('Doppelte Registeridentität im Studienpaket')
            seen.add(identity)
            cls = None
            if kind == 'AnalysisRun' and payload['id'] == manifest['analysis_id']:
                cls = AnalysisRun
            elif kind == 'Artifact' and payload.get('artifact_type') == 'analysis_input':
                cls = Artifact
            elif kind == 'Event' and payload.get('run_id') in ids:
                cls = Event
            if cls is None:
                continue
            entity = cls.model_validate(payload)
            if digest(entity) != record['sha256']:
                raise ValueError('Beschädigter Zeit-/Analysebeleg')
            if cls is AnalysisRun:
                if analysis is not None:
                    raise ValueError('Doppelte Analyseidentität')
                analysis = entity
            elif cls is Artifact:
                artifacts[str(entity.id)] = entity
            else:
                events.setdefault(entity.run_id, []).append(entity)
                event_ids[str(entity.id)] = entity
    if analysis is None or analysis.input_hash != manifest['input_hash']:
        raise ValueError('Bestätigter Analysebeleg fehlt')
    snapshot_id = analysis.selected_inputs['_m7']['snapshot_id']
    artifact = artifacts.get(snapshot_id)
    if artifact is None or artifact.sha256 != manifest['files']['analysis/input.json']:
        raise ValueError('Ursprünglicher Eingabebeleg für die Schrittzeiten fehlt')
    timeline_name = 'presentation/pipeline-steps-manifest.json'
    if timeline_name in manifest['files']:
        with zipfile.ZipFile(path) as archive:
            source = json.loads(archive.read(timeline_name))['source']
        if (source.get('data_hash') != manifest['input_hash'] or
                source.get('snapshot_artifact_id') != snapshot_id or
                source.get('snapshot_artifact_sha256') != artifact.sha256 or
                source.get('cutoff') != artifact.created_at.isoformat()):
            raise ValueError('Schrittzeitmanifest gehört nicht zum historischen Analysestand')
        hashes = source.get('event_hashes')
        if not isinstance(hashes, dict):
            raise ValueError('Ereignisbindung des Schrittzeitmanifests fehlt')
        for identity, expected_hash in hashes.items():
            event = event_ids.get(identity)
            if (event is None or digest(event) != expected_hash or
                    str(event.run_id) not in ids or event.created_at > artifact.created_at):
                raise ValueError('Schrittzeitmanifest verweist auf ein fremdes, späteres oder verändertes Ereignis')
    return TimelineReader(artifact, events)


def main():
    parser = argparse.ArgumentParser(description='Gebundene Bachelorarbeitsanalyse lokal nachrechnen; keine API-Aufrufe.')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--studienpaket', type=Path)
    source.add_argument('--eingabe', type=Path)
    parser.add_argument('--vergleich', type=Path, help='Bestätigte analysis.json; erforderlich mit --eingabe')
    parser.add_argument('--ausgabe', type=Path, required=True, help='Neuer Ausgabeordner; wird nicht überschrieben')
    args = parser.parse_args()
    if bool(args.eingabe) != bool(args.vergleich):
        parser.error('--vergleich ist genau zusammen mit --eingabe erforderlich')
    if args.ausgabe.exists():
        raise ValueError('Ausgabeordner besteht bereits; einen neuen Ordner wählen')
    root = Path(__file__).resolve().parent
    binding = verify_code(root)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root / 'src'))
    import research_env
    if Path(research_env.__file__).resolve() != (root / 'src/research_env/__init__.py').resolve():
        raise ValueError('Falsche installierte Analysebibliothek; gebündelte Quellen erforderlich')
    from research_env.analysis import compute
    from research_env.analysis_reproduction import computation_compatible
    from research_env.analysis_presentation import presentation_binding, presentation_files
    if args.studienpaket:
        package, snapshot, expected = verify_package(args.studienpaket, binding)
    else:
        package = None
        snapshot = json.loads(args.eingabe.read_bytes())
        expected = json.loads(args.vergleich.read_bytes())
    if (sha(canonical(snapshot).encode()) != binding['input_hash'] or
            sha(canonical(expected).encode()) != binding['original_result_sha256'] or
            snapshot['source_binding'] != binding['historical_source_binding'] or
            not computation_compatible(snapshot['source_binding'])):
        raise ValueError('Eingabe, Ergebnis oder historischer Rechenkern passt nicht zum Codepaket')
    result = compute(snapshot)
    if result != expected:
        raise ValueError('Nachrechnung weicht vom gesamten bestätigten Ergebnis ab')
    args.ausgabe.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.analyse-', dir=args.ausgabe.parent) as temporary:
        temporary = Path(temporary)
        os.environ['MPLCONFIGDIR'] = str(temporary / '.matplotlib')
        files = presentation_files(result)
        if package:
            from research_env.analysis_timeline_export import attach_timeline, timeline_files
            timeline = package_timeline(args.studienpaket, package, snapshot)
            files = attach_timeline(files, timeline_files(snapshot, timeline, timeline.snapshot_id))
        checked = []
        if package and 'presentation/manifest.json' in package['files']:
            with zipfile.ZipFile(args.studienpaket) as archive:
                saved = json.loads(archive.read('presentation/manifest.json'))
                if saved.get('presentation_binding') == presentation_binding():
                    for name, (content, _) in files.items():
                        if name != 'manifest.json' and name.endswith(('.csv','.json')) and 'presentation/'+name in package['files']:
                            if archive.read('presentation/'+name) != content:
                                raise ValueError('Nachgerechnete Tabelle/Punktdaten weichen ab: ' + name)
                            checked.append(name)
        destination = temporary / 'ergebnis'
        destination.mkdir()
        for name, (content, _) in files.items():
            if not re.fullmatch(r'[A-Za-z0-9_-]+\.(csv|json|png|svg|pdf|md)', name):
                raise ValueError('Unzulässiger Ausgabename')
            (destination / name).write_bytes(content)
        receipt = {'status': 'passed', 'analysis_id': binding['analysis_id'], 'input_hash': binding['input_hash'],
            'original_result_sha256': binding['original_result_sha256'], 'exact_result_match': True,
            'code_manifest_sha256': sha((root/'manifest.json').read_bytes()),
            'run_count': len(result['cells']), 'timeline_available': bool(package),
            'table_count': sum(name.endswith('.csv') for name in files),
            'figure_count': sum(name.endswith('.png') for name in files), 'exactly_compared_files': checked,
            'output_manifest_sha256': sha(files['manifest.json'][0]),
            'archive_checksums_verified': bool(package), 'api_calls': 0}
        (destination/'nachrechnung.json').write_text(canonical(receipt)+'\n')
        os.rename(destination, args.ausgabe)
    print('Nachrechnung erfolgreich. Tabellen, Abbildungen und Prüfbericht: ' + str(args.ausgabe))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, zipfile.BadZipFile) as error:
        print('Nachrechnung abgebrochen: ' + str(error), file=sys.stderr)
        sys.exit(1)

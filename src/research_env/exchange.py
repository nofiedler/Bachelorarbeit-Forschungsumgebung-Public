"""Read-only, checksummed research archives; no archived code is executed.

Imports stay outside the active register and all role inputs. Recalculation
uses the installed matching analysis, writes a new derivation, and never
changes the ZIP. Runtime image archives remain a separate disclosed component.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
from uuid import UUID, uuid4
import zipfile

from .analysis import compute
from .analysis_reproduction import computation_compatible, historical_computation_binding
from .artifacts import ArtifactStore, IntegrityError, relative_path
from .domain import AnalysisRun, Artifact, ENTITIES, Event, Freeze, Run, StudyPhase, canonical, digest
from .register import GateError, Register

FORMAT = 'research-study-package-v1'
MAX_ZIP = 2 * 1024 * 1024 * 1024
MAX_FILE = 256 * 1024 * 1024
# The register aggregates all records; it is not a single original object.
MAX_REGISTER_FILE = 1024 * 1024 * 1024
MAX_TOTAL = 16 * 1024 * 1024 * 1024
MAX_ENTRIES = 250000
OMITTED_KINDS = {'Backup', 'BackupReceipt', 'Package', 'PackageImport'}
ROOT = Path(__file__).resolve().parents[2]


class ArchiveTimeline:
    """Minimal read-only event reader; no imported Register is constructed."""
    def __init__(self, snapshot_artifact, events):
        self.snapshot_id = snapshot_artifact.id
        self.snapshot_artifact = snapshot_artifact
        self.events = {}
        for event in events:
            self.events.setdefault(event.run_id, []).append(event)

    def get(self, identity, kind):
        if kind is not Artifact or identity != self.snapshot_id:
            raise IntegrityError('Unbekannter historischer Zeitnachweis')
        return self.snapshot_artifact

    def for_run(self, kind, run_id):
        if kind is not Event:
            raise IntegrityError('Paket-Zeitansicht liest ausschließlich Ereignisse')
        return self.events.get(run_id, ())


def object_name(artifact):
    return 'objects/sha256/' + artifact.sha256


def selected_records(register, analysis):
    freeze = register.get(analysis.freeze_id, Freeze)
    phases = {freeze.phase_id}
    phases.update(register.get(run_id, Run).phase_id for run_id in freeze.pilot_run_ids)
    if any(register.get(pid, StudyPhase).purpose not in ('main', 'pilot', 'preparation') for pid in phases):
        raise GateError('Studienpaket darf keine freien Tests oder Demophasen aufnehmen')
    pending = [analysis.id]
    for record in register.connection.execute('SELECT id,kind FROM register_record').fetchall():
        if record['kind'] in OMITTED_KINDS or record['kind'] == 'AnalysisRun':
            continue
        entity = register.get(record['id'])
        if register._phase(entity) in phases:
            pending.append(entity.id)
    store = ArtifactStore(register.settings, register)
    binding = analysis.selected_inputs['_m7']
    pending.extend(UUID(x) for x in (binding['snapshot_id'], binding['acknowledgement_id']))
    selected = {}
    while pending:
        identity = str(pending.pop())
        if identity in selected:
            continue
        entity = register.get(identity)
        if isinstance(entity, Run) and entity.purpose not in ('main', 'pilot', 'preparation'):
            raise GateError('Fremder freier Lauf in Studienprovenienz; Paket wird nicht exportiert')
        selected[identity] = entity
        pending.extend(ref for _, ref in register._references(entity))
        if isinstance(entity, Artifact) and entity.artifact_type in ('dependency_manifest', 'candidate_manifest'):
            body = json.loads(store.read(entity.id))
            pending.extend(UUID(x) for x in body.get('artifact_ids', []))
    return selected


def export(register, analysis_id, destination, *, package_id=None):
    """Confirmed analysis and relevant immutable originals; never a DB dump."""
    analysis = register.get(analysis_id, AnalysisRun)
    store = ArtifactStore(register.settings, register)
    binding = analysis.selected_inputs['_m7']
    snapshot_bytes = store.read(UUID(binding['snapshot_id']))
    snapshot = json.loads(snapshot_bytes)
    if digest(snapshot) != analysis.input_hash:
        raise IntegrityError('Bestätigter Analyseeingang verändert')
    # Export observes a consistent read snapshot. It must neither request the
    # application's write barrier nor reserve SQLite's single writer slot.
    if register.connection.in_transaction:
        raise RuntimeError('Export benötigt eine eigene Lesetransaktion')
    register.connection.execute('BEGIN')
    try:
        records = selected_records(register, analysis)
        from .analysis_raw_journals import journal_files
        raw_files = journal_files(register, records)
    finally:
        register.connection.rollback()
    freeze = register.get(analysis.freeze_id, Freeze)
    result = json.loads(store.read(UUID(binding['outputs']['analysis.json'])))
    from .analysis_presentation import presentation_files
    from .analysis_timeline_export import attach_timeline, timeline_files
    presentation = attach_timeline(presentation_files(result),
        timeline_files(snapshot, register, binding['snapshot_id']))
    presentation_manifest = json.loads(presentation['manifest.json'][0])
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    file_hashes = {}
    with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        def add(name, data):
            relative_path(name)
            if name in file_hashes:
                if file_hashes[name] != hashlib.sha256(data).hexdigest():
                    raise IntegrityError('Paketpfadkollision')
                return
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFREG | 0o444) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
            file_hashes[name] = hashlib.sha256(data).hexdigest()
        add('analysis/input.json', snapshot_bytes)
        add('freeze/manifest.json', canonical(freeze).encode())
        add('registers/records.jsonl', ('\n'.join(canonical({'kind': type(obj).__name__, 'sha256': digest(obj),
            'payload': obj.model_dump(mode='json')}) for _, obj in sorted(records.items())) + '\n').encode())
        for entity in records.values():
            if isinstance(entity, Artifact):
                add(object_name(entity), store.read(entity.id))
        for name, identity in binding['outputs'].items():
            add('analysis/' + name, store.read(UUID(identity)))
        for name, (content, _) in presentation.items():
            add('presentation/' + name, content)
        for name, content in raw_files.items():
            add(name, content)
        add('analysis/acknowledgement.json', store.read(UUID(binding['acknowledgement_id'])))
        # Supplied for inspection only; importer never imports or executes it.
        for directory in ('src/research_env', 'assets/context', 'assets/study', 'evaluation', 'docs/vertraege', 'docker'):
            for path in sorted((ROOT / directory).rglob('*')):
                if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
                    add('software/' + str(path.relative_to(ROOT)), path.read_bytes())
        for name in ('requirements.lock', 'pyproject.toml', 'compose.yaml', 'compose.installed.yaml', 'start.sh'):
            add('software/' + name, (ROOT / name).read_bytes())
        manifest = {'format': FORMAT, 'package_id': str(package_id or uuid4()), 'package_version': 1,
            'raw_data_schema': 'research-series-raw-data-v1',
            'raw_data_scope': 'Registrierte Originaldaten und Adapterjournale der Hauptphase und referenzierten Pilotphasen zum Exportzeitpunkt; bestätigte Analyseauswahl unverändert.',
            'analysis_id': str(analysis.id), 'phase_id': str(analysis.phase_id), 'freeze_id': str(freeze.id),
            'data_origin': snapshot['data_origin'], 'input_hash': analysis.input_hash,
            'source_binding': snapshot['source_binding'], 'software_commit': analysis.software_commit,
            'computation_binding': historical_computation_binding(snapshot['source_binding']),
            'presentation_binding': presentation_manifest['presentation_binding'],
            'presentation_result_sha256': presentation_manifest['original_result_sha256'],
            'created_at': datetime.now(timezone.utc).isoformat(),
            'planned_ids': [row['id'] for row in freeze.matrix],
            'scientific_gaps': analysis.missing_decisions,
            'scope': 'Eingefrorene Hauptphase und referenzierte Pilotprovenienz; keine freien Tests',
            'runtime_images_included': False,
            'limitations': ['Docker-Images sind separat zu sichern; dies ist kein vollständiges Instanzbackup.',
                'Originale enthalten Freigabe-/Bewertungsnamen und dokumentierte Herkunft. Vor öffentlicher Weitergabe inhaltlich prüfen.',
                'Mitgelieferter Quellstand ist der Exportstand. Die historische Eingabe behält ihre vollständige Quellenbindung; Nachrechnung erfordert den exakt passenden Rechenkern und identische Ergebnisse.'],
            'files': dict(sorted(file_hashes.items()))}
        add('README.md', ('# Studienpaket\n\nDieses Archiv enthält Originale und den bestätigten Analysestand. '
            '`raw-data/README.md` erklärt Umfang und Zugänge zu den gesamten registrierten Rohdaten und ergänzenden Aufruf-/Transportjournalen. '
            'Historische Ausgaben liegen unverändert unter `analysis/`. Die aktuelle, separat versionierte Darstellung mit Tabellen, Abbildungen und Punktdaten liegt unter `presentation/`. '
            '`presentation/methods.md` ordnet die Rechenregeln den Forschungsfragen zu; `presentation/data-dictionary.json` beschreibt jede CSV-Spalte. '
            '`runs.csv` enthält jede geplante Lauf-ID; leere Werte sind keine Nullen. '
            'Die grafischen und tabellarischen Daten sind beschreibend. Die neue Darstellung ist keine neue Bestätigung oder Auswahl.\n\n'
            'Import über Auswertung → Studienpakete. Nachrechnung braucht keinen API-Schlüssel und führt keinen Kandidatencode aus. '
            'Originale bleiben unverändert; die Abhängigkeiten des installierten Rechenkerns müssen exakt passen und das gesamte Ergebnis muss identisch sein. '
            'Die Aufnahme von Docker-Images und ein physischer externer Restore sind damit nicht nachgewiesen.\n').encode())
        manifest['files'] = dict(sorted(file_hashes.items()))
        add('manifest.json', canonical(manifest).encode())
        checksums = ''.join(sha + '  ' + name + '\n' for name, sha in sorted(file_hashes.items()))
        add('checksums.sha256', checksums.encode())
    verify(destination)
    destination.chmod(0o444)
    return manifest


def verify(path, *, progress=None, timeline=None):
    """Validate all archive bytes and typed references before exposing an import."""
    report = progress or (lambda *args: None)
    report('Paketstruktur prüfen')
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ZIP:
        raise IntegrityError('Paketdatei fehlt, ist verlinkt oder überschreitet 2 GiB')
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ENTRIES or sum(x.file_size for x in entries) > MAX_TOTAL:
            raise IntegrityError('Entpackgrenze überschritten')
        names = set()
        for entry in entries:
            relative_path(entry.filename)
            if ':' in entry.filename or entry.filename.casefold() in names:
                raise IntegrityError('Doppelter oder unsicherer Paketpfad')
            names.add(entry.filename.casefold())
            mode = entry.external_attr >> 16
            file_limit = MAX_REGISTER_FILE if entry.filename == 'registers/records.jsonl' else MAX_FILE
            if (entry.is_dir() or stat.S_IFMT(mode) not in (0, stat.S_IFREG) or entry.flag_bits & 1 or
                entry.file_size > file_limit or entry.file_size > max(entry.compress_size, 1) * 1000 or entry.extra):
                raise IntegrityError('Unzulässiger ZIP-Eintrag oder Kompressionsgrenze')
        raw = archive.read('manifest.json')
        manifest = json.loads(raw)
        if manifest.get('format') != FORMAT or manifest.get('package_version') != 1 or raw != canonical(manifest).encode():
            raise IntegrityError('Unbekanntes oder nichtkanonisches Paketformat')
        UUID(manifest['package_id'])
        required = {'analysis/input.json', 'analysis/analysis.json', 'analysis/acknowledgement.json',
                    'freeze/manifest.json', 'registers/records.jsonl', 'README.md'}
        files = manifest['files']
        if not required <= set(files) or set(archive.namelist()) != set(files) | {'manifest.json', 'checksums.sha256'}:
            raise IntegrityError('Pflichtdateien fehlen oder zusätzliche Paketdateien vorhanden')
        hashes = {}
        checked = 0
        total_bytes = sum(archive.getinfo(name).file_size for name in files)
        report('Dateien und Prüfsummen prüfen', 0, total_bytes, 'Bytes')
        for name in files:
            hasher = hashlib.sha256()
            with archive.open(name) as stream:
                while chunk := stream.read(1024 * 1024):
                    hasher.update(chunk)
                    checked += len(chunk)
                    report('Dateien und Prüfsummen prüfen', checked, total_bytes, 'Bytes')
            hashes[name] = hasher.hexdigest()
        if hashes != files:
            raise IntegrityError('Paketprüfsumme stimmt nicht')
        hashes['manifest.json'] = hashlib.sha256(raw).hexdigest()
        if archive.read('checksums.sha256') != ''.join(sha + '  ' + name + '\n' for name, sha in sorted(hashes.items())).encode():
            raise IntegrityError('Prüfsummenverzeichnis stimmt nicht')
        records = {}
        checked = 0
        total_records_bytes = archive.getinfo('registers/records.jsonl').file_size
        report('Registerdatensätze prüfen', 0, total_records_bytes, 'Bytes')
        with archive.open('registers/records.jsonl') as stream:
            for line in stream:
                record = json.loads(line)
                entity = ENTITIES[record['kind']].model_validate(record['payload'])
                if digest(entity) != record['sha256'] or str(entity.id) in records:
                    raise IntegrityError('Beschädigter oder doppelter Registerdatensatz')
                if isinstance(entity, Run) and entity.purpose not in ('main', 'pilot', 'preparation'):
                    raise IntegrityError('Freie Test-/Demodaten in Studienpaket')
                records[str(entity.id)] = entity
                checked += len(line)
                report('Registerdatensätze prüfen', checked, total_records_bytes, 'Bytes')
                if isinstance(entity, Artifact):
                    name = object_name(entity)
                    if files.get(name) != entity.sha256 or archive.getinfo(name).file_size != entity.byte_count:
                        raise IntegrityError('Externer oder beschädigter Objektbezug')
                    if entity.artifact_type in ('dependency_manifest', 'candidate_manifest'):
                        body = json.loads(archive.read(name))
                        for identity in body.get('artifact_ids', []):
                            UUID(identity)  # Remaining IDs are checked below after complete parsing.
        resolver = Register.__new__(Register)
        report('Herkunft und Referenzen prüfen', 0, len(records), 'Datensätze')
        for index, entity in enumerate(records.values(), 1):
            refs = [str(identity) for _, identity in resolver._references(entity)]
            if isinstance(entity, Artifact) and entity.artifact_type in ('dependency_manifest', 'candidate_manifest'):
                refs += json.loads(archive.read(object_name(entity))).get('artifact_ids', [])
            if any(identity not in records for identity in refs):
                raise IntegrityError('Registerreferenz außerhalb des Pakets')
            report('Herkunft und Referenzen prüfen', index, len(records), 'Datensätze')
        report('Matrix und Analyseergebnisse abgleichen')
        analysis = records[manifest['analysis_id']]
        if not isinstance(analysis, AnalysisRun):
            raise IntegrityError('Analyseidentität ist kein bestätigter Analysestand')
        snapshot = json.loads(archive.read('analysis/input.json'))
        if digest(snapshot) != manifest['input_hash'] or digest(snapshot) != analysis.input_hash:
            raise IntegrityError('Analysehash stimmt nicht')
        freeze = Freeze.model_validate_json(archive.read('freeze/manifest.json'))
        if freeze != records[str(freeze.id)] or str(analysis.phase_id) != manifest['phase_id']:
            raise IntegrityError('Freeze oder Phase weicht vom Registeroriginal ab')
        if str(freeze.id) != str(analysis.freeze_id) or str(freeze.id) != manifest['freeze_id'] or manifest['planned_ids'] != [r['id'] for r in freeze.matrix]:
            raise IntegrityError('Eingefrorene Matrix stimmt nicht')
        if snapshot['source_binding'] != manifest['source_binding']:
            raise IntegrityError('Analyseversion verändert')
        expected = json.loads(archive.read('analysis/analysis.json'))
        binding = analysis.selected_inputs['_m7']
        aliases = {**binding['outputs'], 'input.json': binding['snapshot_id'],
                   'acknowledgement.json': binding['acknowledgement_id']}
        for name, identity in aliases.items():
            if archive.read('analysis/' + name) != archive.read(object_name(records[identity])):
                raise IntegrityError('Analyseausgabe stimmt nicht mit Original überein: ' + name)
        if expected['data_hash'] != analysis.input_hash:
            raise IntegrityError('Ausgabe gehört nicht zur Eingabe')
        presentation_names = {name.removeprefix('presentation/') for name in files if name.startswith('presentation/')}
        if presentation_names:
            if 'manifest.json' not in presentation_names:
                raise IntegrityError('Versionsbindung der Darstellung fehlt')
            presentation = json.loads(archive.read('presentation/manifest.json'))
            if (presentation.get('data_hash') != analysis.input_hash or
                    presentation.get('original_result_sha256') != hashlib.sha256(canonical(expected).encode()).hexdigest() or
                    set(presentation.get('files', {})) | {'manifest.json'} != presentation_names):
                raise IntegrityError('Darstellung gehört nicht zum bestätigten Ergebnis')
            for name, metadata in presentation['files'].items():
                if (metadata.get('sha256') != files['presentation/' + name] or
                        metadata.get('byte_count') != archive.getinfo('presentation/' + name).file_size):
                    raise IntegrityError('Darstellungsmanifest stimmt nicht mit Paketdatei überein')
            if 'pipeline-steps-manifest.json' in presentation_names:
                timeline_source = json.loads(archive.read('presentation/pipeline-steps-manifest.json'))['source']
                cutoff_artifact = records[binding['snapshot_id']]
                if (timeline_source.get('data_hash') != analysis.input_hash or
                        timeline_source.get('snapshot_artifact_id') != binding['snapshot_id'] or
                        timeline_source.get('snapshot_artifact_sha256') != cutoff_artifact.sha256 or
                        timeline_source.get('cutoff') != cutoff_artifact.created_at.isoformat()):
                    raise IntegrityError('Schrittzeiten gehören nicht zum historischen Analysestand')
                run_ids = {row['run_id'] for row in snapshot['rows'] if row.get('run_id')}
                for identity, sha in timeline_source.get('event_hashes', {}).items():
                    event = records.get(identity)
                    if (not isinstance(event, Event) or digest(event) != sha or
                            str(event.run_id) not in run_ids or event.created_at > cutoff_artifact.created_at):
                        raise IntegrityError('Schrittzeit verweist auf fremdes oder späteres Ereignis')
        if timeline is not None:
            run_ids = {row['run_id'] for row in snapshot['rows'] if row.get('run_id')}
            timeline.append(ArchiveTimeline(records[binding['snapshot_id']],
                (entity for entity in records.values() if isinstance(entity, Event) and str(entity.run_id) in run_ids)))
    return manifest


def recalculate(path):
    timeline = []
    manifest = verify(path, timeline=timeline)
    if not computation_compatible(manifest['source_binding']):
        raise GateError('Nachrechnung benötigt den exakt passenden installierten Rechenkern. Archivcode wird nicht ausgeführt.')
    with zipfile.ZipFile(path) as archive:
        snapshot = json.loads(archive.read('analysis/input.json'))
        result = compute(snapshot)
        if result != json.loads(archive.read('analysis/analysis.json')):
            raise IntegrityError('Nachrechnung weicht vom bestätigten Original ab')
        if 'presentation/manifest.json' in manifest['files']:
            from .analysis_presentation import presentation_binding, presentation_files
            presentation = json.loads(archive.read('presentation/manifest.json'))
            if presentation.get('presentation_binding') == presentation_binding():
                regenerated = presentation_files(result)
                if 'presentation/pipeline-steps-manifest.json' in manifest['files']:
                    from .analysis_timeline_export import attach_timeline, timeline_binding, timeline_files
                    old_timeline = json.loads(archive.read('presentation/pipeline-steps-manifest.json'))
                    if old_timeline['source']['source_binding'] == timeline_binding():
                        regenerated = attach_timeline(regenerated,
                            timeline_files(snapshot, timeline[0], timeline[0].snapshot_id))
                    else:
                        # Its historical column dictionary includes the old
                        # companion schema; do not claim that schema was replayed.
                        regenerated.pop('data-dictionary.json', None)
                for name, (content, _) in regenerated.items():
                    # Graphic point data are exact; raster/vector bytes additionally
                    # depend on the renderer/runtime and are hash-checked on import.
                    if name != 'manifest.json' and name.endswith(('.csv', '.json')):
                        if archive.read('presentation/' + name) != content:
                            raise IntegrityError('Nachgerechnete Darstellung weicht ab: ' + name)
    return result


def import_archive(settings, path, *, progress=None):
    report = progress or (lambda *args: None)
    manifest = verify(path, progress=progress)
    root = settings.staging / 'imports'
    root.mkdir(exist_ok=True)
    identity = manifest['package_id']
    target = root / (identity + '.zip')
    # Copy in bounded chunks; even a large archive never becomes a single
    # in-memory byte string. Hard-link publication cannot replace an original.
    temporary = root / ('.upload-' + str(uuid4()))
    try:
        with Path(path).open('rb') as source, temporary.open('xb') as destination:
            total = Path(path).stat().st_size
            copied = 0
            report('Studienpaket speichern', 0, total, 'Bytes')
            while chunk := source.read(1024 * 1024):
                destination.write(chunk)
                copied += len(chunk)
                report('Studienpaket speichern', copied, total, 'Bytes')
            destination.flush()
            os.fsync(destination.fileno())
            os.fchmod(destination.fileno(), 0o444)
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.is_symlink():
                raise IntegrityError('Paketablage ist ein Link')
            with target.open('rb') as old, temporary.open('rb') as new:
                if hashlib.file_digest(old, 'sha256').digest() != hashlib.file_digest(new, 'sha256').digest():
                    raise GateError('Paket-ID-Konflikt: anderes Archiv unter derselben ID')
        parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        temporary.unlink(missing_ok=True)
    return manifest


def enqueue(register, analysis_id, key):
    register.get(analysis_id, AnalysisRun)
    identity = str(uuid4())
    with register.transaction():
        old = register.connection.execute('SELECT * FROM exchange_job WHERE request_key=?', (key,)).fetchone()
        if old:
            if old['analysis_id'] != str(analysis_id):
                raise GateError('Export-Auftragsschlüssel gehört zu anderem Datenstand')
            return old['id']
        register.connection.execute('INSERT INTO exchange_job(id,analysis_id,request_key,status) VALUES(?,?,?,?)',
                                    (identity, str(analysis_id), key, 'queued'))
    return identity


def boot(register):
    with register.transaction():
        register.connection.execute("UPDATE exchange_job SET status='failed',error='Export unterbrochen. Neuen Exportauftrag starten; Originale erhalten.' WHERE status='running'")


def tick(register):
    row = register.connection.execute("SELECT * FROM exchange_job WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
    if not row:
        return False
    directory = register.settings.staging / 'packages'
    directory.mkdir(exist_ok=True)
    path = directory / (row['id'] + '.zip')
    with register.transaction():
        register.connection.execute("UPDATE exchange_job SET status='running' WHERE id=?", (row['id'],))
    try:
        export(register, UUID(row['analysis_id']), path, package_id=UUID(row['id']))
        with path.open('rb') as stream:
            sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        with register.transaction():
            register.connection.execute("UPDATE exchange_job SET status='ready',sha256=?,byte_count=? WHERE id=?", (sha, path.stat().st_size, row['id']))
    except Exception as exc:
        with register.transaction():
            register.connection.execute("UPDATE exchange_job SET status='failed',error=? WHERE id=?", (str(exc), row['id']))
    return True


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Schlüsselfreie Prüfung/Nachrechnung eines Studienpakets')
    parser.add_argument('command', choices=['verify', 'recalculate'])
    parser.add_argument('archive', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = verify(args.archive) if args.command == 'verify' else recalculate(args.archive)
    if args.output:
        with args.output.open('x') as stream:
            stream.write(canonical(result) + '\n')
    else:
        print(canonical(result))


if __name__ == '__main__':
    main()

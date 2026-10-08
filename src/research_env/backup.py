"""Persistent full-instance backup, separate target transfer and fresh restore.

Only cooperating trusted writers may write control/checkpoints. The advisory
barrier blocks those writers; repeated inventories also detect uncontrolled
changes. A local transfer test is never evidence of external hardware.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from uuid import UUID, uuid4

from .artifacts import ArtifactStore, IntegrityError, atomic_file, read_regular, relative_path
from .config import Settings
from .database import connect, migrate
from .domain import Artifact, Backup, BackupReceipt, CandidateSnapshot, Freeze, Job, canonical, digest
from .locks import write_barrier, worker_lock
from .register import GateError, Register
from .snapshots import inventory


def now():
    return datetime.now(timezone.utc)


def fingerprint(connection):
    """All immutable research contents/projections, not heartbeat/backup bookkeeping."""
    tables = ('register_record', 'register_reference', 'phase_state', 'run_binding', 'transport_state', 'freeze_binding', 'main_plan',
              'adapter_start_order', 'adapter_call', 'adapter_attempt', 'adapter_metadata',
              'pipeline_binding', 'pipeline_effect', 'pipeline_runner', 'pipeline_series',
              'evaluation_execution', 'evaluation_observation', 'evaluation_tool_correction',
              'static_execution', 'static_observation', 'static_tool_correction')
    return digest({table: [list(r) for r in connection.execute(f'SELECT * FROM {table} ORDER BY 1,2').fetchall()]
                   for table in tables})


def sqlite_snapshot(source, destination):
    with closing(sqlite3.connect(Path(source).absolute().as_uri() + '?mode=ro', uri=True, isolation_level=None)) as src, closing(sqlite3.connect(destination)) as dst:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or dst.execute('PRAGMA foreign_key_check').fetchall():
            raise IntegrityError('SQLite-Sicherung beschädigt')
    with open(destination, 'rb') as f:
        os.fsync(f.fileno())


def preserve_file(root, path, data, *, mode=0o444):
    # Deduplication never overwrites an existing backup component.
    try:
        existing, actual_mode = read_regular(root, path)
    except FileNotFoundError:
        atomic_file(root, path, data, mode=mode)
    else:
        if existing != data or actual_mode != mode:
            raise IntegrityError('Backupkollision oder manipuliertes vorhandenes Objekt')


def write_checkpoint(settings, path, data):
    """Required entry point for regular checkpoint writers; no in-place overwrite."""
    with write_barrier(settings):
        atomic_file(settings.checkpoints, path, data, mode=0o600, replace=True)


def checkpoint_writer(settings):
    """Hold this shared barrier around the entire SQLite checkpoint write/close."""
    return write_barrier(settings)


def checkpoint_inventory(settings):
    # Do not even read the independent copies: their writers hold separate locks.
    result = []
    for path in sorted(settings.checkpoints.iterdir()):
        if path.name == 'restored': continue
        if path.is_dir() and not path.is_symlink():
            result.extend(dict(entry, path=path.name+'/'+entry['path']) for entry in inventory(path))
        else:
            data, mode = read_regular(settings.checkpoints, path.name)
            if mode & 0o7000: raise IntegrityError('Sonderrechte in Checkpoint verboten')
            result.append({'path':path.name,'mode':mode,'size':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    return sorted(result, key=lambda x:x['path'])


def copy_checkpoints(settings, target):
    before = checkpoint_inventory(settings)
    source_paths = {x['path'] for x in before}
    for entry in before:
        path = entry['path']
        if path.endswith(('-wal', '-shm', '-journal')):
            if not any(path == x + suffix for x in source_paths for suffix in ('-wal', '-shm', '-journal') if x != path):
                raise IntegrityError('Checkpoint-Nebenfile ohne Datenbank')
            continue
        data, mode = read_regular(settings.checkpoints, path)
        if data.startswith(b'SQLite format 3\x00'):
            relative_path(path)
            output = target / path
            output.parent.mkdir(parents=True, exist_ok=True)
            # Opening even a read-only WAL database may create/change its SHM.
            # Open a private byte copy instead, under the existing writer barrier.
            with tempfile.TemporaryDirectory(dir=settings.staging, prefix='checkpoint-read-') as temporary:
                copied = Path(temporary) / 'checkpoint.sqlite3'
                copied.write_bytes(data)
                for suffix in ('-wal', '-journal'):
                    if path + suffix in source_paths:
                        raw, _ = read_regular(settings.checkpoints, path + suffix)
                        Path(str(copied) + suffix).write_bytes(raw)
                sqlite_snapshot(copied, output)
            os.chmod(output, mode)
        else:
            atomic_file(target, path, data, mode=mode)
    if before != checkpoint_inventory(settings):
        raise IntegrityError('Checkpointschreiber während Backup aktiv')
    return before, inventory(target)


def verify_package(package, *, expected_hash=None):
    package = Path(package)
    raw, _ = read_regular(package, 'manifest.json')
    body = json.loads(raw)
    if body.get('format') != 'instance-backup-v1' or canonical(body).encode() != raw:
        raise IntegrityError('Unbekanntes/nichtkanonisches Backupmanifest')
    sha = digest(body)
    if expected_hash is not None and sha != expected_hash:
        raise IntegrityError('Backupmanifesthash stimmt nicht')
    expected = body['files']
    if len({x['path'] for x in expected}) != len(expected):
        raise IntegrityError('Doppelter Backuppfad')
    actual = inventory(package)
    actual = [x for x in actual if x['path'] != 'manifest.json']
    if actual != expected:
        raise IntegrityError('Backupinhalt fehlt/verändert/zusätzlich')
    for entry in expected:
        relative_path(entry['path'])
    return body, sha


class Backups:
    def __init__(self, settings, register):
        self.settings, self.register = settings, register
        self.store = ArtifactStore(settings, register)

    def enqueue(self, freeze_id, *, idempotency_key):
        freeze = self.register.get(freeze_id, Freeze)
        with self.register.transaction():
            old = self.register.connection.execute('SELECT * FROM backup_request b JOIN job_state j USING(job_id) WHERE j.idempotency_key=?', (idempotency_key,)).fetchone()
            if old:
                if old['freeze_id'] != str(freeze_id):
                    raise GateError('Idempotenzschlüssel gehört anderem Freeze')
                return self.register.get(old['job_id'], Job)
            job = self.register._put(Job(code=f'BACKUPJOB-{uuid4()}', phase_id=freeze.phase_id, run_id=None, job_type='backup', idempotency_key=idempotency_key))
            self.register.connection.execute('INSERT INTO backup_request(job_id,freeze_id,status,substantive_revision) VALUES (?,?,?,?)',
                (str(job.id), str(freeze.id), 'requested', self.register.revision(freeze.phase_id)))
            return job

    def record_operation(self, job_id, operation, details):
        self.register.get(job_id, Job)
        value = {'job_id': str(job_id), 'operation': operation, 'at': now().isoformat(), 'details': details}
        # Web can write control records but deliberately cannot write artifacts.
        with self.register.transaction():
            self.register.connection.execute('INSERT INTO backup_operation_log VALUES (?,?,?)',
                (str(uuid4()), str(job_id), canonical(value)))
        return value

    def history(self, job_id):
        self.register.get(job_id, Job)
        rows = self.register.connection.execute("SELECT id FROM register_record WHERE kind='Artifact' AND json_extract(payload,'$.artifact_type')='backup_operation' AND json_extract(payload,'$.original_name')=? ORDER BY rowid", (f'backup-job-{job_id}',)).fetchall()
        values = []
        for row in rows:
            value = json.loads(self.store.read(row[0]))
            if value['job_id'] != str(job_id):
                raise IntegrityError('Fremde Backupoperationshistorie')
            values.append(dict(value, artifact_id=row[0]))
        values.extend(json.loads(row[0]) for row in self.register.connection.execute(
            'SELECT payload FROM backup_operation_log WHERE job_id=? ORDER BY rowid', (str(job_id),)))
        return sorted(values, key=lambda value: value['at'])

    def request(self, job_id):
        row = self.register.connection.execute('SELECT * FROM backup_request WHERE job_id=?', (str(job_id),)).fetchone()
        if not row:
            raise GateError('Kein Backupauftrag')
        return dict(row)

    def create(self, job_id, *, crash=None):
        request = self.request(job_id)
        if request['status'] != 'creating':
            raise GateError('Backupauftrag muss exklusiv geclaimt sein')
        freeze = self.register.get(request['freeze_id'], Freeze)
        self.record_operation(job_id, 'create_started', {'freeze_id': str(freeze.id)})
        backup_id = uuid4()
        root = self.settings.staging / 'backups'
        root.mkdir(parents=True, exist_ok=True)
        work = root / f'.creating-{backup_id}'
        final = root / str(backup_id)
        work.mkdir()
        (work / 'checkpoints').mkdir()
        with write_barrier(self.settings, exclusive=True):
            active = [r[0] for r in self.register.connection.execute("SELECT transport_id FROM transport_state WHERE status='dispatching'")]
            if self.register.connection.execute("SELECT 1 FROM adapter_metadata WHERE status='dispatching' LIMIT 1").fetchone():
                active.append('adapter_metadata')
            if active:
                raise GateError('Aktive Requests in der Instanz verhindern konsistentes Backup')
            if self.register.connection.execute("SELECT 1 FROM sandbox_execution WHERE status IN ('allocated','starting','running','recovery_required') LIMIT 1").fetchone():
                raise GateError('Aktive/verwaiste Sandbox verhindert konsistentes Backup; zuerst kontrolliert stoppen')
            if self.register.connection.execute("SELECT 1 FROM evaluation_execution WHERE status IN ('running','recovery_required') LIMIT 1").fetchone():
                raise GateError('Aktive/verwaiste Bewertung verhindert konsistentes Backup')
            if self.register.connection.execute("SELECT 1 FROM static_execution WHERE status IN ('running','recovery_required') LIMIT 1").fetchone():
                raise GateError('Aktive/verwaiste statische Messung verhindert konsistentes Backup')
            from .snapshots import Snapshots
            Snapshots(self.store).preserve_pending_copies()
            revision = self.register.revision(freeze.phase_id)
            for row in self.register.connection.execute('SELECT id FROM register_record').fetchall():
                self.register.get(row[0])  # Verify every immutable payload, not only object refs.
            before = fingerprint(self.register.connection)
            database_version = self.register.connection.execute('PRAGMA data_version').fetchone()[0]
            sqlite_snapshot(self.settings.database, work / 'control.sqlite3')
            checkpoint_source, checkpoint_files = copy_checkpoints(self.settings, work / 'checkpoints')
            objects = self.register.all(Artifact)
            revisions = [dict(r) for r in self.register.connection.execute('SELECT phase_id,substantive_revision FROM phase_state ORDER BY phase_id')]
            if before != fingerprint(self.register.connection) or database_version != self.register.connection.execute('PRAGMA data_version').fetchone()[0]:
                raise IntegrityError('Nicht kooperierender DBschreiber während Backup')
        # Objects are immutable. Long copying/transfer does not hold a DB transaction.
        for artifact in objects:
            data = self.store.read(artifact.id)
            path = self.store.object_path(artifact.sha256)
            if not (work / path).exists():
                atomic_file(work, path, data)
        if crash:
            crash('after_capture')
        with write_barrier(self.settings, exclusive=True):
            if before != fingerprint(self.register.connection) or checkpoint_source != checkpoint_inventory(self.settings):
                raise IntegrityError('Instanz während interner Sicherung verändert; erneut sichern')
            manifests = (
                {'database': next(x for x in inventory(work) if x['path'] == 'control.sqlite3')},
                {'objects': sorted({a.sha256 for a in objects})},
                {'checkpoints': checkpoint_files},
                {'barrier': 'exclusive advisory lock; all trusted register/checkpoint writers participate',
                 'active_request_ids': [], 'checkpoint_source': checkpoint_source, 'fingerprint': before})
            metadata = [self.store.json(m, artifact_type=t, original_name=t + '.json') for m, t in zip(manifests,
                ('backup_database_manifest', 'backup_object_manifest', 'backup_checkpoint_manifest', 'backup_barrier'), strict=True)]
            for artifact in metadata:
                preserve_file(work, self.store.object_path(artifact.sha256), self.store.read(artifact.id))
            fields = {'id': str(backup_id), 'code': f'BACKUP-{backup_id}', 'created_at': now().isoformat(), 'schema_version': 1,
                'phase_id': str(freeze.phase_id), 'freeze_id': str(freeze.id), 'substantive_revision': revision,
                'database_manifest_id': str(metadata[0].id), 'object_manifest_id': str(metadata[1].id),
                'checkpoint_manifest_id': str(metadata[2].id), 'write_barrier_evidence_id': str(metadata[3].id),
                'status': 'awaiting_confirmation', 'consistent': True, 'active_request_ids': []}
            body = {'format': 'instance-backup-v1', 'backup': fields, 'metadata_records': [a.model_dump(mode='json') for a in metadata],
                    'request_job_id': str(job_id), 'phase_revisions': revisions, 'scope': 'full_instance_with_separate_purposes', 'files': inventory(work)}
            sha = digest(body)
            atomic_file(work, 'manifest.json', canonical(body).encode())
            os.rename(work, final)
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            if crash:
                crash('before_backup_link')
            backup = self.register.add(Backup(**fields, manifest_hash=sha))
            with self.register.transaction():
                self.register.connection.execute("UPDATE backup_request SET status='internal_ready',substantive_revision=?,backup_id=?,package_path=?,error=NULL WHERE job_id=?", (revision, str(backup.id), str(final), str(job_id)))
            if crash:
                crash('after_backup_link')
            self.record_operation(job_id, 'captured', {'backup_id': str(backup.id), 'manifest_hash': sha, 'substantive_revision': revision})
            return backup

    def transfer(self, job_id, target, *, crash=None):
        request = self.request(job_id)
        if request['status'] not in ('internal_ready', 'transferred'):
            raise GateError('Keine abgeschlossene interne Sicherung zum Übertragen')
        details = {'target': str(target), 'backup_id': request['backup_id']}
        self.record_operation(job_id, 'transfer_started', details)
        try:
            backup = self.register.get(request['backup_id'], Backup)
            package = Path(request['package_path'])
            verify_package(package, expected_hash=backup.manifest_hash)
            target = Path(target).absolute()
            # Reject existing symlink ancestors before mkdir can follow them.
            if any(path.is_symlink() for path in (target, *target.parents)):
                raise IntegrityError('Sicherungsziel oder Elternpfad ist Symlink')
            resolved = target.resolve()
            for managed in (self.settings.control, self.settings.artifacts, self.settings.checkpoints, self.settings.staging):
                if resolved.is_relative_to(managed.resolve()) or managed.resolve().is_relative_to(resolved):
                    raise IntegrityError('Getrenntes Ziel außerhalb Instanzverzeichnissen erforderlich')
            target.mkdir(parents=True, exist_ok=True)
            final = target / str(backup.id)
            temporary = target / f'.transfer-{uuid4()}'
            details.update(target=str(final), manifest_hash=backup.manifest_hash,
                           substantive_revision=backup.substantive_revision)
            if final.exists():
                verify_package(final, expected_hash=backup.manifest_hash)
            else:
                temporary.mkdir()
                for entry in inventory(package):
                    data, mode = read_regular(package, entry['path'])
                    atomic_file(temporary, entry['path'], data, mode=mode)
                    if crash:
                        crash('during_transfer')
                verify_package(temporary, expected_hash=backup.manifest_hash)
                os.rename(temporary, final)
                fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            with self.register.transaction():
                self.register.connection.execute("UPDATE backup_request SET status='transferred',transfer_path=?,transfer_verified_at=?,error=NULL WHERE job_id=?", (str(final), now().isoformat(), str(job_id)))
            self.record_operation(job_id, 'transfer_succeeded', {'target': str(final), 'manifest_hash': backup.manifest_hash})
            return final
        except BaseException as exc:
            error = f'{type(exc).__name__}: {exc}'
            with self.register.transaction():
                self.register.connection.execute("UPDATE backup_request SET status='failed',error=? WHERE job_id=?", (f'Transfer: {error}', str(job_id)))
            self.record_operation(job_id, 'transfer_failed', dict(details, error=error))
            raise

    def confirm(self, job_id, *, person, external_medium, confirmed_at, synthetic=False):
        request = self.request(job_id)
        if request['status'] != 'transferred':
            raise GateError('Technisch geprüfte getrennte Übertragung fehlt')
        backup = self.register.get(request['backup_id'], Backup)
        if backup.substantive_revision != self.register.revision(backup.phase_id):
            raise GateError('Sicherung veraltet; aktuelle Inhalte erneut sichern')
        verify_package(request['transfer_path'], expected_hash=backup.manifest_hash)
        # The argument labels fixture usage. Receipt is always explicitly self-report.
        if synthetic and not external_medium.startswith('TECHNICAL-FIXTURE:'):
            raise GateError('Synthetisches Ziel ausdrücklich kennzeichnen')
        return self.register.add(BackupReceipt(code=f'RECEIPT-{uuid4()}', backup_id=backup.id,
            manifest_hash=backup.manifest_hash, person=person, external_medium=external_medium, confirmed_at=confirmed_at))

    def integrity(self):
        problems = []
        root = self.settings.staging / 'backups'
        known = {str(b.id): b for b in self.register.all(Backup)}
        if root.exists():
            for path in sorted(root.iterdir()):
                if path.name not in known:
                    problems.append({'kind': 'orphan_backup', 'path': path.name})
                else:
                    try:
                        verify_package(path, expected_hash=known[path.name].manifest_hash)
                    except (OSError, ValueError) as exc:
                        problems.append({'kind': 'damaged_backup', 'path': path.name, 'reason': str(exc)})
        for request in self.register.connection.execute('SELECT backup_id,package_path FROM backup_request WHERE backup_id IS NOT NULL'):
            if not request['package_path'] or not Path(request['package_path']).exists():
                problems.append({'kind': 'missing_backup', 'backup_id': request['backup_id']})
        return {'complete': not problems, 'problems': problems}

    def recover(self, job_id, *, decision):
        if not decision:
            raise GateError('Bewusste Recoveryentscheidung erforderlich')
        request = self.request(job_id)
        if request['status'] not in ('recovery_required', 'failed'):
            raise GateError('Kein unterbrochener/fehlgeschlagener Sicherungsauftrag')
        self.record_operation(job_id, 'recovery_decision', {'decision': decision, 'previous_error': request['error'], 'previous_status': request['status']})
        # Resume only safe transfer if an immutable linked snapshot exists.
        with self.register.transaction():
            if request['backup_id']:
                self.register.connection.execute("UPDATE backup_request SET status='internal_ready',error=? WHERE job_id=?", (decision, str(job_id)))
                self.register.connection.execute("UPDATE job_state SET status='completed',worker=NULL WHERE job_id=?", (str(job_id),))
            else:
                self.register.connection.execute("UPDATE backup_request SET status='requested',error=? WHERE job_id=?", (decision, str(job_id)))
                self.register.connection.execute("UPDATE job_state SET status='ready',worker=NULL WHERE job_id=?", (str(job_id),))


class BackupWorker:
    """Claims only backup jobs. A restart never executes preexisting pending work."""
    def __init__(self, backups, worker_id=None):
        self.backups, self.register = backups, backups.register
        self.worker_id = worker_id or str(uuid4())
        with worker_lock(backups.settings), self.register.transaction():
            self.register.connection.execute("UPDATE backup_request SET status='recovery_required',error='Worker restart: intentional recovery required' WHERE status IN ('requested','creating') OR job_id IN (SELECT job_id FROM job_state WHERE status='running')")
            self.register.connection.execute("UPDATE job_state SET status='stopped' WHERE job_id IN (SELECT job_id FROM backup_request WHERE status='recovery_required')")

    def tick(self, *, crash=None):
        with worker_lock(self.backups.settings):
            row = self.register.connection.execute("SELECT job_id FROM backup_request WHERE status='requested' ORDER BY rowid LIMIT 1").fetchone()
            if not row:
                return None
            job_id = row[0]
            with self.register.transaction():
                updated = self.register.connection.execute("UPDATE job_state SET status='running',worker=?,claimed_at=?,heartbeat_at=? WHERE job_id=? AND status='ready'", (self.worker_id, now().isoformat(), now().isoformat(), job_id)).rowcount
                if not updated:
                    return None
                self.register.connection.execute("UPDATE backup_request SET status='creating' WHERE job_id=?", (job_id,))
            try:
                result = self.backups.create(job_id, crash=crash)
            except Exception as exc:
                with self.register.transaction():
                    self.register.connection.execute("UPDATE backup_request SET status='failed',error=? WHERE job_id=?", (str(exc), job_id))
                    self.register.connection.execute("UPDATE job_state SET status='stopped' WHERE job_id=?", (job_id,))
                self.backups.record_operation(job_id, 'create_failed', {'error': str(exc)})
                raise
            with self.register.transaction():
                self.register.connection.execute("UPDATE job_state SET status='completed',heartbeat_at=? WHERE job_id=?", (now().isoformat(), job_id))
            return result


def restore(package, settings, *, expected_hash):
    """Fresh trusted instance only. Restore does not dispatch or claim any job."""
    body, sha = verify_package(package, expected_hash=expected_hash)
    roots = (settings.control, settings.artifacts, settings.checkpoints, settings.staging)
    if any(p.exists() and (p.is_symlink() or any(p.iterdir())) for p in roots):
        raise IntegrityError('Restore benötigt frische leere Instanz')
    if len({p.resolve() for p in roots}) != 4 or any(a.resolve().is_relative_to(b.resolve()) for a in roots for b in roots if a != b):
        raise IntegrityError('Getrennte Instanzverzeichnisse erforderlich')
    for path in roots:
        path.mkdir(parents=True, exist_ok=True)
    for entry in body['files']:
        path = entry['path']
        data, mode = read_regular(package, path)
        if path == 'control.sqlite3':
            atomic_file(settings.control, 'control.sqlite3', data, mode=0o600)
        elif path.startswith('objects/sha256/'):
            atomic_file(settings.artifacts, path, data, mode=mode)
        elif path.startswith('checkpoints/'):
            atomic_file(settings.checkpoints, path.removeprefix('checkpoints/'), data, mode=mode)
        else:
            raise IntegrityError('Unbekannter Bestandteil im Instanzbackup')
    migrate(settings)
    register = Register(settings)
    try:
        if register.connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or register.connection.execute('PRAGMA foreign_key_check').fetchall():
            raise IntegrityError('Restore-Datenbank beschädigt')
        for value in body['metadata_records']:
            register.add(Artifact.model_validate(value))
        backup = register.add(Backup(**body['backup'], manifest_hash=sha))
        with register.transaction():
            register.connection.execute("INSERT OR REPLACE INTO runtime_metadata VALUES ('seal_copy_origin',?)", (str(uuid4()),))
            register.connection.execute("UPDATE job_state SET status='stopped',worker=NULL WHERE status IN ('ready','running')")
            register.connection.execute("UPDATE backup_request SET status='recovery_required',error='Restored instance: intentional recovery required' WHERE status IN ('requested','creating')")
            register.connection.execute("INSERT INTO runtime_metadata VALUES ('restore_recovery_required',?)", (str(backup.id),))
        store = ArtifactStore(settings, register)
        from .snapshots import Snapshots
        snapshots = Snapshots(store)
        for candidate in register.all(CandidateSnapshot):
            if candidate.role == 'sealed' and register.state(candidate.run_id).seal == 'sealed':
                snapshots.restore(candidate.id, settings.artifacts / 'sealed' / str(candidate.id), readonly=True, resume=True)
        archive = settings.staging / 'backups' / str(backup.id)
        archive.mkdir(parents=True)
        for entry in inventory(package):
            data, mode = read_regular(package, entry['path'])
            atomic_file(archive, entry['path'], data, mode=mode)
        with register.transaction():
            register.connection.execute("UPDATE backup_request SET backup_id=?,substantive_revision=?,package_path=?,status='recovery_required',error='Restored backup: intentional recovery required' WHERE job_id=?",
                (str(backup.id), backup.substantive_revision, str(archive), body['request_job_id']))
        report = store.integrity()
        if not report['complete']:
            raise IntegrityError(f'Restoreobjekte beschädigt: {report}')
        return {'backup_id': str(backup.id), 'manifest_hash': sha, 'integrity': report, 'generation_started': False, 'recovery_required': True}
    finally:
        register.close()

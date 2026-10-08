"""Complete candidate trees with stored, shared dependency bytes.

The writer controller must enumerate every candidate/test process. Sandbox #7
will supply that controller; registered local processes and writer leases work
now. Sealed originals are reconstructed from immutable objects, never used as
writable workspaces. Host administration is outside this trust boundary.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
import platform
from pathlib import Path
import stat
from uuid import uuid4

from .artifacts import IntegrityError, atomic_file, directory, read_regular, relative_path
from .domain import Artifact, CandidateSnapshot, ConfigurationVersion, Event, ProcessInstance, Run, digest
from .locks import file_lock, write_barrier

EXCLUDED_DIRS = {'.git', '__pycache__', '.pytest_cache', 'node_modules', 'vendor', '.cache'}
CACHE_DIRS = {'storage/framework/cache', 'storage/framework/sessions', 'storage/framework/views', 'storage/logs', 'bootstrap/cache', 'storage/framework/testing'}


def excluded(path, *, is_directory=False):
    p = Path(path)
    return (any(x in EXCLUDED_DIRS for x in p.parts) or any(path.startswith(x + '/') for x in CACHE_DIRS) and not is_directory and p.name != '.gitignore'
            or p.name == '.env' or p.name.startswith('.env.') and p.name != '.env.example'
            or p.suffix.lower() in {'.pem', '.key', '.p12', '.pfx'} or p.name in {'auth.json', 'credentials.json'})


def inventory(root, *, project=False):
    root = Path(root)
    with directory(root):
        pass
    result = []
    def walk(parts):
        with directory(root, parts) as fd:
            for name in sorted(os.listdir(fd)):
                rel = '/'.join((*parts, name))
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if project and excluded(rel, is_directory=stat.S_ISDIR(info.st_mode)):
                    continue
                if stat.S_ISDIR(info.st_mode):
                    walk((*parts, name))
                elif stat.S_ISREG(info.st_mode):
                    data, mode = read_regular(root, rel)
                    if mode & 0o7000:
                        raise IntegrityError('Sonderrechte in Kandidat/Checkpoint verboten')
                    result.append({'path': rel, 'mode': mode, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
                else:
                    raise IntegrityError(f'Symlink/Spezialdatei verboten: {rel}')
    walk(())
    return sorted(result, key=lambda x: x['path'])


class ProcessWriters:
    """Explicit handles owned by the caller; never terminate foreign processes."""
    def __init__(self, processes=()):
        self.processes = tuple(processes)

    def stop_all(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
        for process in self.processes:
            process.wait()

    def assert_stopped(self):
        if any(p.poll() is None for p in self.processes):
            raise IntegrityError('Schreibprozess noch aktiv')


class _CopyAttempt:
    """Proven ownership is persisted before any temporary enters the original tree."""
    def __init__(self, snapshots, candidate, entry, crash):
        self.snapshots, self.candidate, self.entry, self.crash = snapshots, candidate, entry, crash
        self.id = str(uuid4())
        self.temporary = '.tmp-' + self.id

    def phase(self, name):
        if name == 'after_file_fsync':
            with self.snapshots.register.transaction():
                self.snapshots.register.connection.execute('UPDATE seal_copy_attempt SET synced_size=? WHERE id=?', (self.entry['size'], self.id))
        if self.crash:
            self.crash(name)

    def open(self, parent):
        s, c, entry = self.snapshots, self.candidate, self.entry
        with s.register.transaction():
            s.register.connection.execute('INSERT INTO seal_copy_attempt(id,origin,candidate_id,manifest_id,tree_hash,path,temporary,size,sha256,mode,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (self.id, s.copy_origin, str(c.id), str(c.file_manifest_id), c.tree_hash, entry['path'], self.temporary, entry['size'], entry['sha256'], entry['mode'] & ~0o222, 'planned', datetime.now(timezone.utc).isoformat()))
        # Allocation is outside the original, on the same artifact filesystem.
        # A crash before ownership recording cannot introduce unproven original bytes.
        with directory(s.settings.artifacts, ('seal-copy-attempts',), create=True) as staging:
            fd = os.open(self.temporary, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=staging)
            try:
                os.fsync(fd)
                os.fsync(staging)
                info = os.fstat(fd)
                with s.register.transaction():
                    s.register.connection.execute("UPDATE seal_copy_attempt SET device=?,inode=?,status='allocated' WHERE id=?", (info.st_dev, info.st_ino, self.id))
                try:
                    os.stat(self.temporary, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise IntegrityError('Fremdes vorhandenes Copytempziel')
                os.rename(self.temporary, self.temporary, src_dir_fd=staging, dst_dir_fd=parent)
                os.fsync(staging)
                os.fsync(parent)
                self.phase('after_temp_create')
                return self.temporary, fd
            except BaseException:
                os.close(fd)
                raise

    def finish(self):
        with self.snapshots.register.transaction():
            self.snapshots.register.connection.execute("UPDATE seal_copy_attempt SET status='completed' WHERE id=?", (self.id,))


class Snapshots:
    def __init__(self, store):
        self.store = store
        self.settings, self.register = store.settings, store.register
        with self.register.transaction():
            self.register.connection.execute("INSERT OR IGNORE INTO runtime_metadata VALUES ('seal_copy_origin',?)", (str(uuid4()),))
            self.copy_origin = self.register.connection.execute("SELECT value FROM runtime_metadata WHERE key='seal_copy_origin'").fetchone()[0]

    def writer_lease(self, workspace, *, exclusive=False, blocking=True):
        key = hashlib.sha256(str(Path(workspace).absolute()).encode()).hexdigest()
        return file_lock(self.settings.control / f'.candidate-{key}.lock', exclusive=exclusive, blocking=blocking)

    def _save_files(self, root, files, run_id=None):
        bindings = []
        for entry in files:
            data, mode = read_regular(root, entry['path'])
            if len(data) != entry['size'] or mode != entry['mode'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
                raise IntegrityError('Baum während Snapshot verändert')
            bindings.append(self.store.store(data, run_id=run_id, artifact_type='candidate_file' if run_id else 'dependency_file', original_name=entry['path']))
        return bindings

    def dependencies(self, root, *, source, runtime):
        """Save actual installed dependency contents, not a lock-only promise."""
        if not source or not runtime:
            raise IntegrityError('Dependency-Herkunft und Runtimebezug erforderlich')
        with write_barrier(self.settings):
            files = inventory(root)
            if not files:
                raise IntegrityError('Leerer Dependencybestand')
            artifacts = self._save_files(root, files)
            if files != inventory(root):
                raise IntegrityError('Dependencies während Sicherung verändert')
            body = {'format': 'dependency-v1', 'files': files, 'tree_hash': digest(files),
                    'artifact_ids': [str(a.id) for a in artifacts], 'source': source, 'runtime': runtime}
            return self.store.json(body, artifact_type='dependency_manifest', original_name='dependencies.json')

    def _dependency(self, artifact_id):
        a = self.register.get(artifact_id, Artifact)
        if a.artifact_type != 'dependency_manifest' or a.run_id:
            raise IntegrityError('Ungültiger gemeinsamer Dependencybestand')
        body = json.loads(self.store.read(artifact_id))
        if body['format'] != 'dependency-v1' or digest(body['files']) != body['tree_hash'] or len(body['files']) != len(body['artifact_ids']):
            raise IntegrityError('Dependency-Manifest beschädigt')
        for entry, aid in zip(body['files'], body['artifact_ids'], strict=True):
            relative_path(entry['path'])
            data = self.store.read(aid)
            if len(data) != entry['size'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
                raise IntegrityError('Dependencybezug beschädigt')
        return body

    def capture(self, workspace, *, run_id, role, scaffold_id, dependency_ids, internal_tests_hash,
                repair_count=0, rejected_changes_artifact_id=None, process_artifact_ids=(), crash=None):
        if role not in ('post_migrate', 'post_repair', 'sealed'):
            raise IntegrityError('Snapshotrolle ungültig')
        if len(dependency_ids) != 1:
            raise IntegrityError('Genau ein gespeicherter gemeinsamer vendor-Bestand erforderlich')
        with write_barrier(self.settings):
            deps = self._dependency(dependency_ids[0])
            files = inventory(workspace, project=True)
            if not files:
                raise IntegrityError('Kein Kandidat')
            artifacts = self._save_files(workspace, files, run_id)
            combined = sorted(files + [dict(x, path='vendor/' + x['path']) for x in deps['files']], key=lambda x: x['path'])
            body = {'format': 'candidate-v1', 'files': files, 'artifact_ids': [str(a.id) for a in artifacts],
                    'dependency_ids': [str(x) for x in dependency_ids], 'complete_tree': combined, 'tree_hash': digest(combined)}
            if files != inventory(workspace, project=True):
                raise IntegrityError('Kandidat während Snapshot verändert')
            if crash:
                crash('before_snapshot_link')
            if files != inventory(workspace, project=True):
                raise IntegrityError('Kandidat vor Registerlink verändert')
            manifest = self.store.json(body, run_id=run_id, artifact_type='candidate_manifest', original_name='candidate.json')
            return self.register.add(CandidateSnapshot(code=f'CANDIDATE-{uuid4()}', run_id=run_id, role=role,
                file_manifest_id=manifest.id, tree_hash=body['tree_hash'], scaffold_id=scaffold_id,
                dependency_ids=tuple(dependency_ids), artifact_ids=tuple(a.id for a in artifacts),
                internal_tests_hash=internal_tests_hash, accepted_files=tuple(x['path'] for x in files),
                rejected_changes_artifact_id=rejected_changes_artifact_id, process_artifact_ids=tuple(process_artifact_ids), repair_count=repair_count))

    def _recover_copy_attempts(self, snapshot, destination, expected, *, preserve_only=False):
        by_path = {x['path']: x for x in expected}
        rows = self.register.connection.execute("SELECT * FROM seal_copy_attempt WHERE candidate_id=? AND origin=? AND status IN ('planned','allocated') ORDER BY created_at,id", (str(snapshot.id), self.copy_origin)).fetchall()
        for row in rows:
            entry = by_path.get(row['path'])
            if row['manifest_id'] != str(snapshot.file_manifest_id) or row['tree_hash'] != snapshot.tree_hash or entry is None or (row['size'], row['sha256'], row['mode']) != (entry['size'], entry['sha256'], entry['mode']):
                raise IntegrityError('Copyjournal hat fremde Kandidaten-/Manifestbindung')
            if row['temporary'] != '.tmp-' + row['id']:
                raise IntegrityError('Copyjournal hat fremden Tempnamen')
            parts = relative_path(row['path'])
            original_temp = '/'.join((*parts[:-1], row['temporary']))
            staging_temp = 'seal-copy-attempts/' + row['temporary']
            locations = []
            for root, path in ((destination, original_temp), (self.settings.artifacts, staging_temp)):
                try:
                    data, mode = read_regular(root, path)
                except FileNotFoundError:
                    continue
                with directory(root, relative_path(path)[:-1]) as parent:
                    info = os.stat(relative_path(path)[-1], dir_fd=parent, follow_symlinks=False)
                if row['device'] is not None and (info.st_dev, info.st_ino) != (row['device'], row['inode']):
                    raise IntegrityError('Copytemp hat fremden Geräte-/Inodebesitz')
                if row['device'] is None and root == destination:
                    raise IntegrityError('Unbelegte Copytemp im Original')
                if mode not in (0o600, row['mode']):
                    raise IntegrityError('Copytemp hat unerlaubte Rechte')
                # Partial writes are valid only as exact prefixes of immutable source bytes.
                body = json.loads(self.store.read(snapshot.file_manifest_id))
                sources = dict(zip((x['path'] for x in body['files']), body['artifact_ids'], strict=True))
                for dep_id in snapshot.dependency_ids:
                    dep = self._dependency(dep_id)
                    sources.update(('vendor/' + x['path'], aid) for x, aid in zip(dep['files'], dep['artifact_ids'], strict=True))
                wanted = self.store.read(sources[row['path']])
                if len(data) < row['synced_size'] or len(data) > len(wanted) or not wanted.startswith(data):
                    raise IntegrityError('Copytemp manipuliert; kein erwarteter Schreibprefix')
                locations.append((root, path, data, mode, info))
            if len(locations) > 1:
                raise IntegrityError('Mehrere Dateien behaupten denselben Copyversuch')
            evidence_id = row['evidence_id']
            if locations:
                root, path, data, mode, info = locations[0]
                if evidence_id:
                    if self.store.read(evidence_id) != data:
                        raise IntegrityError('Copyrawbeleg passt nicht zum erhaltenen Temp')
                else:
                    evidence = self.store.store(data, artifact_type='seal_copy_interruption', original_name='seal-copy-' + row['id'])
                    evidence_id = str(evidence.id)
                    with self.register.transaction():
                        self.register.connection.execute('UPDATE seal_copy_attempt SET evidence_id=? WHERE id=?', (evidence_id, row['id']))
                if row['device'] is not None and not preserve_only:
                    # Only journal-proven own files may move; unknown allocation stays untouched outside original.
                    parts = relative_path(path)
                    with directory(root, parts[:-1]) as source, directory(self.settings.artifacts, ('seal-copy-evidence',), create=True) as retained:
                        if read_regular(root, path) != (data, mode):
                            raise IntegrityError('Copytemp während Recovery verändert')
                        current = os.stat(parts[-1], dir_fd=source, follow_symlinks=False)
                        if (current.st_dev, current.st_ino, current.st_nlink) != (info.st_dev, info.st_ino, 1):
                            raise IntegrityError('Copytempbesitz während Recovery verändert')
                        name = row['id'] + '.raw'
                        try:
                            os.stat(name, dir_fd=retained, follow_symlinks=False)
                        except FileNotFoundError:
                            pass
                        else:
                            raise IntegrityError('Fremde Datei im Copyrawziel')
                        os.rename(parts[-1], name, src_dir_fd=source, dst_dir_fd=retained)
                        os.fsync(source)
                        os.fsync(retained)
            if not preserve_only:
                with self.register.transaction():
                    self.register.connection.execute("UPDATE seal_copy_attempt SET status='retained' WHERE id=?", (row['id'],))

    def preserve_pending_copies(self):
        """Backup preparation: preserve raw bytes, do not finish or move an attempt."""
        for candidate in self.register.all(CandidateSnapshot):
            if candidate.role == 'sealed':
                body = json.loads(self.store.read(candidate.file_manifest_id))
                expected = [dict(x, mode=x['mode'] & ~0o222) for x in body['complete_tree']]
                self._recover_copy_attempts(candidate, self.settings.artifacts / 'sealed' / str(candidate.id), expected, preserve_only=True)

    def restore(self, candidate_id, destination, *, readonly=False, resume=False, crash=None):
        snapshot = self.register.get(candidate_id, CandidateSnapshot)
        body = json.loads(self.store.read(snapshot.file_manifest_id))
        if body['format'] != 'candidate-v1' or body['tree_hash'] != snapshot.tree_hash or digest(body['complete_tree']) != snapshot.tree_hash:
            raise IntegrityError('Kandidatenmanifest/Hash beschädigt')
        if tuple(body['dependency_ids']) != tuple(str(x) for x in snapshot.dependency_ids):
            raise IntegrityError('Fremder Dependencybezug')
        if tuple(body['artifact_ids']) != tuple(str(x) for x in snapshot.artifact_ids) or tuple(x['path'] for x in body['files']) != snapshot.accepted_files:
            raise IntegrityError('Kandidatenmanifest/Dateibindungen beschädigt')
        entries = list(zip(body['files'], body['artifact_ids'], strict=True))
        for dep_id in snapshot.dependency_ids:
            dep = self._dependency(dep_id)
            entries.extend((dict(x, path='vendor/' + x['path']), aid) for x, aid in zip(dep['files'], dep['artifact_ids'], strict=True))
        if sorted([x for x, _ in entries], key=lambda x: x['path']) != body['complete_tree']:
            raise IntegrityError('Unvollständiger Kandidatenbaum')
        destination = Path(destination)
        expected = [dict(x, mode=x['mode'] & ~0o222) for x in body['complete_tree']] if readonly else body['complete_tree']
        if resume and not readonly:
            raise IntegrityError('Fortsetzung nur für gespeichertes Readonlyoriginal')
        if resume:
            self._recover_copy_attempts(snapshot, destination, expected)
        if resume and destination.exists():
            actual = inventory(destination)
            expected_by_path = {x['path']: x for x in expected}
            if len(expected_by_path) != len(expected) or any(expected_by_path.get(x['path']) != x for x in actual):
                raise IntegrityError('Vorhandene Originalkopie verändert oder fremde Datei')
            # Only exact tree directories created by this reconstruction are allowed.
            directories = {'.'} | {str(parent) for x in expected for parent in Path(x['path']).parents}
            for folder, dirs, _ in os.walk(destination):
                rel = Path(folder).relative_to(destination)
                with directory(destination, rel.parts):
                    pass
                if str(rel) not in directories or stat.S_IMODE(Path(folder).lstat().st_mode) not in (0o700, 0o755, 0o555):
                    raise IntegrityError('Vorhandene Originalverzeichnisse verändert')
        else:
            destination.mkdir(parents=True, exist_ok=False)
        for entry, aid in entries:
            data = self.store.read(aid)
            if len(data) != entry['size'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
                raise IntegrityError('Dateimanifest/Objekt beschädigt')
            mode = entry['mode'] & ~0o222 if readonly else entry['mode']
            if resume:
                try:
                    existing, existing_mode = read_regular(destination, entry['path'])
                except FileNotFoundError:
                    atomic_file(destination, entry['path'], data, mode=mode, attempt=_CopyAttempt(self, snapshot, entry, crash))
                else:
                    if existing != data or existing_mode != mode:
                        raise IntegrityError('Vorhandene Originaldatei verändert')
            else:
                atomic_file(destination, entry['path'], data, mode=mode)
        actual = inventory(destination)
        if actual != expected:
            raise IntegrityError('Wiederhergestellter Baum stimmt nicht')
        if readonly:
            for folder, dirs, _ in os.walk(destination, topdown=False):
                os.chmod(folder, 0o555)
        return snapshot.tree_hash

    def seal(self, workspace, *, writers, copy_crash=None, **kwargs):
        try:
            writers.stop_all()
            writers.assert_stopped()
            workspace = Path(workspace)
            with self.writer_lease(workspace, exclusive=True, blocking=False), write_barrier(self.settings):
                writers.assert_stopped()
                existing = [x for x in self.register.all(CandidateSnapshot) if x.run_id == kwargs['run_id'] and x.role == 'sealed']
                if len(existing) > 1:
                    raise IntegrityError('Mehrere versiegelte Kandidaten für Lauf')
                if existing:
                    snapshot = existing[0]
                    bindings = {'scaffold_id': kwargs['scaffold_id'], 'dependency_ids': tuple(kwargs['dependency_ids']),
                                'internal_tests_hash': kwargs['internal_tests_hash'], 'repair_count': kwargs.get('repair_count', 0),
                                'rejected_changes_artifact_id': kwargs.get('rejected_changes_artifact_id'),
                                'process_artifact_ids': tuple(kwargs.get('process_artifact_ids', ()))}
                    if any(getattr(snapshot, key) != value for key, value in bindings.items()):
                        raise IntegrityError('Sealfortsetzung mit abweichenden Bindungen')
                    body = json.loads(self.store.read(snapshot.file_manifest_id))
                    if not workspace.exists() or inventory(workspace, project=True) != body['files']:
                        raise IntegrityError('Workspace seit Sealaufnahme verändert oder fehlt')
                elif not workspace.exists() or not inventory(workspace, project=True):
                    run_id = kwargs['run_id']
                    seq = 1 + max((e.sequence for e in self.register.all(Event) if e.run_id == run_id), default=0)
                    run = self.register.get(run_id, Run)
                    conf = self.register.get(run.configuration_version_id, ConfigurationVersion)
                    process = self.register.add(ProcessInstance(code=f'SEALPROCESS-{uuid4()}', worker='snapshot-seal',
                        software_commit=conf.settings.software_commit, platform=platform.platform(), started_at=datetime.now(timezone.utc),
                        clock_description='No duration measured by candidate_missing event'))
                    self.register.add(Event(code=f'MISSING-{uuid4()}', run_id=run_id, sequence=seq,
                        event_type='candidate_missing', process_id=process.id, interval_id=None,
                        happened_at=datetime.now(timezone.utc), evidence_ids=(), details={'reason': 'No regular candidate files'}))
                    self.register.set_state(run_id, self.register.state(run_id).model_copy(update={'seal': 'no_candidate'}), reason='candidate_missing')
                    return None
                before = inventory(workspace, project=True)
                if not existing:
                    snapshot = self.capture(workspace, role='sealed', **kwargs)
                if before != inventory(workspace, project=True):
                    raise IntegrityError('Manipulation während Seal')
                writers.assert_stopped()
                original = self.settings.artifacts / 'sealed' / str(snapshot.id)
                self.restore(snapshot.id, original, readonly=True, resume=True, crash=copy_crash)
                if before != inventory(workspace, project=True):
                    raise IntegrityError('Workspace während Sealfortsetzung verändert')
                writers.assert_stopped()
                state = self.register.state(snapshot.run_id)
                if state.seal != 'sealed' or state.candidate_id != snapshot.id:
                    self.register.set_state(snapshot.run_id, state.model_copy(update={'seal': 'sealed', 'candidate_id': snapshot.id}), reason='Last candidate saved and verified readonly')
                return snapshot

        except (IntegrityError, OSError) as exc:
            state = self.register.state(kwargs['run_id'])
            if state.seal != 'sealed':
                self.register.set_state(kwargs['run_id'], state.model_copy(update={'seal': 'integrity_error'}), reason=f'Seal prevented: {exc}')
            raise

    def inspection_copy(self, candidate_id, destination):
        snapshot = self.register.get(candidate_id, CandidateSnapshot)
        if snapshot.role != 'sealed' or self.register.state(snapshot.run_id).seal != 'sealed':
            raise IntegrityError('Nur gültiges Seal zur Inspektion/Messung')
        body = json.loads(self.store.read(snapshot.file_manifest_id))
        expected = [dict(x, mode=x['mode'] & ~0o222) for x in body['complete_tree']]
        original = self.settings.artifacts / 'sealed' / str(snapshot.id)
        if inventory(original) != expected:
            raise IntegrityError('Readonly Original verändert oder fehlt')
        return self.restore(candidate_id, destination)

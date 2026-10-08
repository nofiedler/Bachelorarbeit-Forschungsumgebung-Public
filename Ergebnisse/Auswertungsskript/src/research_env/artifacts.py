"""Durable content-addressed storage; file commit always precedes DB linkage."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from uuid import uuid4

from .domain import Artifact, canonical
from .locks import file_lock, write_barrier

HASH = re.compile(r'^[0-9a-f]{64}$')


class IntegrityError(ValueError):
    pass


def relative_path(value):
    p = PurePosixPath(value)
    if not value or p.is_absolute() or '\\' in value or any(x in ('', '.', '..') for x in value.split('/')):
        raise IntegrityError(f'Unsicherer relativer Pfad: {value!r}')
    return p.parts


@contextmanager
def directory(root, parts=(), *, create=False):
    """Anchor all subordinate operations using no-follow directory descriptors."""
    root = Path(root)
    if create:
        root.mkdir(parents=True, exist_ok=True)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts:
            if part in ('', '.', '..') or '/' in part:
                raise IntegrityError('Unsicheres Pfadsegment')
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def read_regular(root, path):
    parts = relative_path(path)
    with directory(root, parts[:-1]) as parent:
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise IntegrityError('Nur reguläre Dateien ohne Hardlinks zulässig')
            chunks = []
            while chunk := os.read(fd, 1024 * 1024):
                chunks.append(chunk)
            after = os.fstat(fd)
            linked = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_mode)
            if signature(before) != signature(after) or signature(after) != signature(linked):
                raise IntegrityError('Datei während Lesen verändert')
            return b''.join(chunks), stat.S_IMODE(before.st_mode)
        finally:
            os.close(fd)


def atomic_file(root, path, data, *, mode=0o444, replace=False, attempt=None):
    parts = relative_path(path)
    with directory(root, parts[:-1], create=True) as parent:
        if attempt is None:
            temporary = f'.tmp-{uuid4()}'
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        else:
            temporary, fd = attempt.open(parent)
        try:
            with os.fdopen(fd, 'wb') as output:
                if attempt is not None:
                    middle = max(1, len(data) // 2)
                    output.write(data[:middle])
                    output.flush()
                    attempt.phase('during_partial_write')
                    output.write(data[middle:])
                else:
                    output.write(data)
                output.flush()
                os.fsync(output.fileno())
                if attempt is not None:
                    attempt.phase('after_file_fsync')
                os.fchmod(output.fileno(), mode)
                if attempt is not None:
                    attempt.phase('after_fchmod')
            if not replace:
                try:
                    os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise IntegrityError('Ziel bereits vorhanden')
            if attempt is not None:
                attempt.phase('before_rename')
            os.rename(temporary, parts[-1], src_dir_fd=parent, dst_dir_fd=parent)
            if attempt is not None:
                attempt.phase('after_rename_before_directory_fsync')
            os.fsync(parent)
            if attempt is not None:
                attempt.finish()
        finally:
            # Journal-owned interrupted bytes stay for explicit verified recovery.
            if attempt is None:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass


class ArtifactStore:
    def __init__(self, settings, register):
        self.settings, self.register = settings, register
        self.root = settings.artifacts
        with directory(self.root, ('objects', 'sha256'), create=True):
            pass

    @staticmethod
    def object_path(sha):
        if not HASH.fullmatch(sha):
            raise IntegrityError('Ungültiger SHA-256')
        return f'objects/sha256/{sha[:2]}/{sha}'

    def put_object(self, data, *, crash=None):
        """Crash hooks are test instrumentation, never a completeness override."""
        if self.register.connection.in_transaction:
            raise RuntimeError('Dateiarbeit in DB-Transaktion verboten')
        sha = hashlib.sha256(data).hexdigest()
        path = self.object_path(sha)
        with write_barrier(self.settings), file_lock(self.settings.control / '.objects.lock', exclusive=True):
            parts = relative_path(path)
            with directory(self.root, parts[:-1], create=True) as parent:
                temporary = f'.tmp-{uuid4()}'
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                try:
                    with os.fdopen(fd, 'wb') as output:
                        output.write(data)
                        output.flush()
                        os.fsync(output.fileno())
                    written, _ = read_regular(self.root, '/'.join(parts[:-1]) + '/' + temporary)
                    if hashlib.sha256(written).hexdigest() != sha or written != data:
                        raise IntegrityError('Temporärer Inhalt stimmt nicht')
                    if crash:
                        crash('before_file_commit')
                    try:
                        existing, mode = read_regular(self.root, path)
                    except FileNotFoundError:
                        os.chmod(temporary, 0o444, dir_fd=parent, follow_symlinks=False)
                        os.rename(temporary, parts[-1], src_dir_fd=parent, dst_dir_fd=parent)
                        os.fsync(parent)
                    else:
                        if existing != data or mode & 0o222:
                            raise IntegrityError('Kollision oder manipuliertes Objekt')
                    if crash:
                        crash('after_file_commit')
                finally:
                    try:
                        os.unlink(temporary, dir_fd=parent)
                    except FileNotFoundError:
                        pass
        return sha

    def store(self, data, *, run_id=None, artifact_type='raw', producer='worker',
              original_name='raw', mime_type='application/octet-stream', crash=None, **bindings):
        with write_barrier(self.settings):
            sha = self.put_object(data, crash=crash)
            value = Artifact(code=f'ART-{uuid4()}', sha256=sha, byte_count=len(data),
                             run_id=run_id, artifact_type=artifact_type, producer=producer,
                             original_name=original_name, mime_type=mime_type, **bindings)
            if crash:
                crash('before_db_link')
            result = self.register.add(value)
            if crash:
                crash('after_db_link')
            return result

    def json(self, value, **kwargs):
        return self.store(canonical(value).encode(), mime_type='application/json', **kwargs)

    def read(self, artifact_id):
        value = self.register.get(artifact_id, Artifact)
        data, mode = read_regular(self.root, self.object_path(value.sha256))
        if len(data) != value.byte_count or hashlib.sha256(data).hexdigest() != value.sha256 or mode & 0o222:
            raise IntegrityError(f'Beschädigtes Objekt {value.id}')
        return data

    def integrity(self):
        references = {a.sha256: a for a in self.register.all(Artifact)}
        found = set()
        problems = []
        try:
            with directory(self.root, ('objects', 'sha256')) as base:
                prefixes = sorted(os.listdir(base))
        except OSError as exc:
            return {'complete': False, 'problems': [{'kind': 'unsafe_path', 'path': 'objects/sha256', 'reason': str(exc)}], 'references': len(references), 'objects': 0}
        for prefix in prefixes:
            try:
                with directory(self.root, ('objects', 'sha256', prefix)) as parent:
                    names = sorted(os.listdir(parent))
            except OSError:
                problems.append({'kind': 'unsafe_path', 'path': 'objects/sha256/' + prefix})
                continue
            for name in names:
                rel = f'objects/sha256/{prefix}/{name}'
                if not HASH.fullmatch(name) or prefix != name[:2]:
                    problems.append({'kind': 'uncommitted_file', 'path': rel})
                    continue
                found.add(name)
                try:
                    data, mode = read_regular(self.root, rel)
                    if hashlib.sha256(data).hexdigest() != name or mode & 0o222:
                        raise IntegrityError('Bytes/Rechte verändert')
                    if name in references and len(data) != references[name].byte_count:
                        raise IntegrityError('Bytezahl verändert')
                except (OSError, IntegrityError) as exc:
                    problems.append({'kind': 'modified_or_unsafe', 'path': rel, 'reason': str(exc)})
                if name not in references:
                    problems.append({'kind': 'orphan_file', 'path': rel})
        for sha in sorted(set(references) - found):
            problems.append({'kind': 'missing_file', 'sha256': sha})
        return {'complete': not problems, 'problems': problems, 'references': len(references), 'objects': len(found)}

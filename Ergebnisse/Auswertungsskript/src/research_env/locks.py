"""Local advisory locks. Every trusted register/checkpoint writer participates.

Locks are process-local filesystem controls, not protection against a privileged
host. No SQLite transaction is held while filesystem/external work runs.
"""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import threading

_local = threading.local()


@contextmanager
def file_lock(path: Path, *, exclusive=False, blocking=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    key = str(path.absolute())
    held = getattr(_local, 'held', {})
    _local.held = held
    if key in held:
        fd, mode, depth = held[key]
        if exclusive and not mode:
            raise RuntimeError('Keine Lockaufwertung während Schreiboperation')
        held[key] = (fd, mode, depth + 1)
        try:
            yield
        finally:
            held[key] = (fd, mode, depth)
        return
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) |
                    (0 if blocking else fcntl.LOCK_NB))
        held[key] = (fd, exclusive, 1)
        try:
            yield
        finally:
            del held[key]
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def write_barrier(settings, *, exclusive=False):
    return file_lock(settings.control / '.write-barrier.lock', exclusive=exclusive)


def worker_lock(settings):
    return file_lock(settings.control / '.worker.lock', exclusive=True, blocking=False)

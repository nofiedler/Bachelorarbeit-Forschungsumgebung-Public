"""Display-only progress for local imports; never mutates research records."""
from contextlib import closing
from datetime import datetime, timezone
from functools import lru_cache
import json
from pathlib import Path
import sqlite3
from threading import Lock
import time

from .artifacts import ArtifactStore, read_regular
from .study_restore import copy_settings


def duration_hint(seconds):
    if seconds is None:
        return 'Restzeit wird geschätzt, sobald genügend Fortschritt messbar ist.'
    if seconds < 60:
        return 'Voraussichtlich noch weniger als eine Minute für diesen Schritt.'
    low = max(1, round(seconds * .8 / 60))
    high = max(low + 1, round(seconds * 1.3 / 60))
    return f'Noch ungefähr {low}–{high} Minuten für diesen Schritt.'


def view(stage, *, done=None, total=None, unit='Dateien', seconds=None, step=None):
    measured = total is not None and total > 0 and done is not None
    return {'stage': stage, 'done': done, 'total': total, 'unit': unit, 'step': step,
            'percent': min(100, int(100 * done / total)) if measured else None,
            'remaining_seconds': seconds, 'eta': duration_hint(seconds)}


class TransferProgress:
    """Small per-web-process observations for the existing synchronous upload."""
    def __init__(self):
        self.lock = Lock()
        self.items = {}

    def start(self, identity):
        with self.lock:
            now = time.monotonic()
            self.items = {k: v for k, v in self.items.items() if now - v['updated'] < 7200}
            if identity in self.items:
                raise ValueError('Dieser Importauftrag ist bereits vergeben.')
            if len(self.items) >= 64:
                raise ValueError('Zu viele Importaufträge; bitte später erneut versuchen.')
            self.items[identity] = {'stage': '', 'started': now, 'updated': now,
                                    'view': view('Datei wird übernommen')}
        return lambda stage, done=None, total=None, unit='Dateien': self.update(identity, stage, done, total, unit)

    def update(self, identity, stage, done=None, total=None, unit='Dateien'):
        with self.lock:
            now = time.monotonic()
            item = self.items[identity]
            if stage != item['stage']:
                item['started'] = now
            elif now - item['updated'] < .5 and done != total:
                return
            elapsed = now - item['started']
            seconds = (total - done) * elapsed / done if total and done and elapsed >= 2 else None
            item.update(stage=stage, updated=now, view=view(stage, done=done, total=total, unit=unit, seconds=seconds))

    def get(self, identity):
        with self.lock:
            item = self.items.get(identity)
            return dict(item['view']) if item else view('Import wird vorbereitet')


@lru_cache(maxsize=16)
def _restore_total(database, artifacts, device, inode, origin):
    # Only the already validated destination database is inspected, not uploaded SQL.
    with closing(sqlite3.connect(Path(database).as_uri() + '?mode=ro', uri=True, timeout=.25)) as db:
        candidates = db.execute("""SELECT c.payload, a.payload FROM register_record c
            JOIN run_binding r ON r.run_id=json_extract(c.payload,'$.run_id')
            JOIN register_record a ON a.id=json_extract(c.payload,'$.file_manifest_id')
            WHERE c.kind='CandidateSnapshot' AND json_extract(c.payload,'$.role')='sealed'
              AND json_extract(r.state_json,'$.seal')='sealed'""").fetchall()
    total = 0
    for _, artifact in candidates:
        metadata = json.loads(artifact)
        data, _ = read_regular(Path(artifacts), ArtifactStore.object_path(metadata['sha256']))
        total += len(json.loads(data)['complete_tree'])
    return total


class RestoreProgress:
    """Observe existing copy journals, including imports begun by an older worker."""
    def __init__(self, settings):
        self.settings = settings
        self.lock = Lock()
        self.cache = {}

    def get(self, row):
        if row['status'] == 'queued':
            return view('Wartet auf den Import-Worker', step=1)
        target = copy_settings(self.settings, row['id'])
        with self.lock:
            cached = self.cache.get(row['id'])
            now = time.monotonic()
            if cached and now - cached[0] < 5:
                return cached[1]
            result = view('ZIP und Datenbank prüfen', step=1)
            try:
                info = target.database.stat()
                result = view('Originaldaten übernehmen', step=2)
                with closing(sqlite3.connect(target.database.as_uri() + '?mode=ro', uri=True, timeout=.25)) as db:
                    origin_row = db.execute("SELECT value FROM runtime_metadata WHERE key='seal_copy_origin'").fetchone()
                    if origin_row:
                        origin = origin_row[0]
                        done, first, last = db.execute("""SELECT count(*),min(created_at),max(created_at)
                            FROM seal_copy_attempt WHERE origin=? AND status='completed'""", (origin,)).fetchone()
                    else:
                        done, first, last = 0, None, None
                # Historical journals are retained in the backup but do not count as this import.
                if first and datetime.fromisoformat(first) > datetime.fromisoformat(row['created_at']):
                    total = _restore_total(str(target.database), str(target.artifacts), info.st_dev, info.st_ino, origin)
                    elapsed = (datetime.fromisoformat(last) - datetime.fromisoformat(first)).total_seconds()
                    stale = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() > 45
                    seconds = (total - done) * elapsed / done if 0 < done < total and elapsed >= 10 and not stale else None
                    if total and done >= total:
                        result = view('Abschließende Integritäts- und Referenzprüfung', step=4)
                        result['eta'] = 'Die Dateien sind kopiert. Der Import ist erst nach der Abschlussprüfung fertig.'
                    else:
                        result = view('Originaldateien wiederherstellen', done=done, total=total,
                                      seconds=seconds, step=3)
            except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
                # Progress is optional; a busy or not-yet-published copy must not fail the status page.
                pass
            if len(self.cache) >= 32:
                self.cache.clear()
            self.cache[row['id']] = (now, result)
            return result

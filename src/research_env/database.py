"""Small, atomic SQL migration entry point for the control database."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid

from .config import Settings
from .locks import write_barrier

MIGRATIONS = Path(__file__).with_name("migrations")


def connect(settings: Settings, *, readonly=False):
    path = settings.database.resolve()
    connection = sqlite3.connect(path.as_uri() + ("?mode=ro" if readonly else "?mode=rwc"),
                                 uri=True, isolation_level=None)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection
    except BaseException:
        # The caller's closing/Register context has not received this handle.
        connection.close()
        raise


def statements(source: str):
    pending = ""
    for line in source.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            yield pending
            pending = ""
    if pending.strip():
        raise ValueError("Unvollständige SQL-Migration")


def migrate(settings: Settings, migrations: Path = MIGRATIONS):
    settings.control.mkdir(parents=True, exist_ok=True)
    with write_barrier(settings), closing(connect(settings)) as connection:
        if connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
            raise RuntimeError("Kontrolldatenbank benötigt journal_mode=DELETE")
        connection.execute("BEGIN EXCLUSIVE")
        try:
            connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                               "(version TEXT PRIMARY KEY, sha256 TEXT NOT NULL, applied_at TEXT NOT NULL)")
            applied = dict(connection.execute("SELECT version, sha256 FROM schema_migrations"))
            available = {p.name: p for p in sorted(migrations.glob("*.sql"))}
            if set(applied) - set(available):
                raise RuntimeError("Datenbankschema neuer als Software; passendes Image verwenden")
            for name, path in available.items():
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if name in applied:
                    if applied[name] != digest:
                        raise RuntimeError(f"Veränderte bereits angewandte Migration: {name}")
                    continue
                for statement in statements(path.read_text()):
                    connection.execute(statement)
                connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?)",
                                   (name, digest, datetime.now(timezone.utc).isoformat()))
            connection.execute("INSERT OR IGNORE INTO runtime_metadata VALUES ('installation_id', ?)",
                               (str(uuid.uuid4()),))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


def save_worker_status(settings, components, state="running"):
    with write_barrier(settings), closing(connect(settings)) as connection:
        connection.execute("INSERT INTO worker_status VALUES (1, ?, ?, ?) "
                           "ON CONFLICT(id) DO UPDATE SET observed_at=excluded.observed_at, "
                           "state=excluded.state, components_json=excluded.components_json",
                           (time.time(), state, json.dumps(components)))


def set_diagnostic_marker(settings, marker):
    """CLI-only synthetic persistence probe; not a study or web action."""
    with write_barrier(settings), closing(connect(settings)) as connection:
        connection.execute("INSERT INTO runtime_metadata VALUES ('synthetic_persistence_marker', ?) "
                           "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (marker,))

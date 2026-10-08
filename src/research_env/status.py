from contextlib import closing
import json
import os
import platform
import shutil
import sqlite3
import time

from . import __version__
from .database import connect

# A diagnostic freshness interval only: it never cancels or restarts work.
HEARTBEAT_FRESH_SECONDS = 15


def storage_status(name, path, *, writable=False):
    try:
        free = shutil.disk_usage(path).free
        if not path.is_dir() or not os.access(path, os.R_OK | os.X_OK):
            raise OSError("not accessible")
        if writable and (not os.access(path, os.W_OK) or free == 0):
            raise OSError("not writable or full")
        return {"name": name, "state": "ok", "detail": f"Verfügbar; {free // (1024 ** 2)} MiB frei."}
    except OSError:
        return {"name": name, "state": "missing", "detail":
                "Speicher nicht erreichbar. Volume-Mount und freien Speicher prüfen; regulär neu starten, keine Volumes löschen."}


def get_status(settings):
    components = [storage_status(label, path, writable=writable) for label, path, writable in
                  (("Kontrollspeicher", settings.control, True), ("Artefakte", settings.artifacts, False),
                   ("Staging", settings.staging, True))]
    installation_id = None
    database_ok = False
    marker = None
    real_calls = None
    try:
        with closing(connect(settings, readonly=True)) as connection:
            mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            synchronous = connection.execute("PRAGMA synchronous").fetchone()[0]
            metadata = dict(connection.execute("SELECT key, value FROM runtime_metadata"))
            installation_id = metadata.get("installation_id")
            real_calls = connection.execute("SELECT count(*) FROM register_record c JOIN register_record m ON m.id=json_extract(c.payload,'$.model_package_id') WHERE c.kind='ModelCall' AND json_extract(m.payload,'$.endpoint') NOT LIKE 'mock://%'").fetchone()[0]
            marker = metadata.get("synthetic_persistence_marker")
            database_ok = mode == "delete" and synchronous == 2
            components.append({"name": "SQLite", "state": "ok" if database_ok else "error",
                               "detail": f"SQLite {sqlite3.sqlite_version}; Journal {mode.upper()}, synchronous={synchronous} (FULL=2)."})
            worker = connection.execute("SELECT * FROM worker_status WHERE id=1").fetchone()
        fresh = worker and worker["state"] == "running" and time.time() - worker["observed_at"] <= HEARTBEAT_FRESH_SECONDS
        stale_running = worker and worker['state'] == 'running' and not fresh
        components.append({"name": "Worker", "state": "ok" if fresh else "unknown" if stale_running else "missing", "detail":
                           "Lebenszeichen aktuell; persistente Aufträge und serielle Pipeline verfügbar." if fresh else
                           "Letztes Lebenszeichen meldete laufende Arbeit, seine Frische ist unbekannt. Längere Vorbereitung oder ein laufender Schritt können die Meldung verzögern. Persistierten Laufstand, letzte Aktivität und Diagnose prüfen; keine automatische Wiederaufnahme." if stale_running else
                           "Kein laufender Worker nachgewiesen. Gespeicherten Laufstand und Workerlogs prüfen; vorhandene Daten bleiben erhalten."})
        if fresh:
            components.extend(json.loads(worker["components_json"]))
        else:
            components.append({"name": "Worker-Komponenten", "state": "unknown", "detail":
                               "Docker, Checkpoints und Imagebestand ohne aktuelles Worker-Lebenszeichen nicht prüfbar."})
    except (sqlite3.Error, OSError, ValueError, KeyError):
        components.append({"name": "SQLite", "state": "error", "detail":
                           "Kontrolldatenbank nicht lesbar. Logs, Volume und Migrationen prüfen; vorhandene Daten nicht löschen."})
        components.append({"name": "Worker", "state": "unknown", "detail": "Ohne lesbare Kontrolldatenbank nicht prüfbar."})
    if settings.migration_failed:
        database_ok = False
        components.append({"name": "Migration", "state": "error", "detail":
                           "Datenbankmigration fehlgeschlagen. Diagnosemodus: Workerstart gesperrt. Logs, Datenbankzustand und Volume-Rechte prüfen; nach Behebung Webdienst neu starten. Vorhandene Daten nicht löschen."})
    return {"version": __version__, "python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
            "architecture": platform.machine(), "installation_id": installation_id,
            "synthetic_persistence_marker": marker, "database_ok": database_ok,
            "components": components, "model_access": "worker_only", "real_calls": real_calls,
            "scope": "M8: Vorbereitung und Laufsteuerung; technische Nachweise ersetzen keine menschlichen Studienfreigaben."}

"""Restore a frozen study into a separate namespace, preserving every original ID.

Archives are data only. SQL schemas must equal installed trusted migrations;
no uploaded source, jobs or checkpoints are executed by import.
"""
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
from uuid import UUID, uuid4
import zipfile

from .artifacts import ArtifactStore, IntegrityError, atomic_file, relative_path
from .backup import restore, verify_package
from .config import Settings
from .database import MIGRATIONS, statements
from .domain import Artifact, Freeze, Study, StudyPhase, canonical
from .exchange import MAX_ZIP, MAX_FILE, MAX_TOTAL, MAX_ENTRIES
from .register import GateError, Register

# The control database aggregates the whole study, unlike a single CAS object.
MAX_DATABASE_FILE = 2 * 1024 * 1024 * 1024


def copy_settings(settings, identity):
    identity = str(UUID(str(identity)))
    return replace(settings,
        control=settings.control / 'restored' / identity,
        artifacts=settings.artifacts / 'restored' / identity,
        checkpoints=settings.checkpoints / 'restored' / identity,
        staging=settings.staging / 'restored' / identity)


def rows(register):
    return [dict(r) for r in register.connection.execute('SELECT * FROM study_restore ORDER BY rowid DESC')]


def enqueue(register, upload):
    if upload.stat().st_size > MAX_ZIP: raise GateError('Sicherung überschreitet 2 GiB')
    with upload.open('rb') as stream: sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    old = register.connection.execute('SELECT id FROM study_restore WHERE archive_sha256=?', (sha,)).fetchone()
    if old:
        state=register.connection.execute('SELECT status FROM study_restore WHERE id=?',(old[0],)).fetchone()[0]
        if state=='failed':
            with register.transaction(): register.connection.execute("UPDATE study_restore SET status='queued',error=NULL WHERE id=?",(old[0],))
        return old[0]
    identity = str(uuid4())
    root = register.settings.staging / 'restore-uploads'
    root.mkdir(exist_ok=True)
    destination = root / (identity + '.zip')
    shutil.copyfile(upload, destination)
    destination.chmod(0o444)
    with register.transaction():
        register.connection.execute('INSERT INTO study_restore(id,archive_sha256,status,created_at) VALUES (?,?,?,?)',
            (identity, sha, 'queued', datetime.now(timezone.utc).isoformat()))
    return identity


def unpack(path, destination):
    if path.stat().st_size > MAX_ZIP: raise IntegrityError('Sicherung überschreitet 2 GiB')
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ENTRIES or sum(x.file_size for x in entries) > MAX_TOTAL:
            raise IntegrityError('Entpackgrenze überschritten')
        names = set()
        for entry in entries:
            relative_path(entry.filename)
            mode = entry.external_attr >> 16
            if (entry.filename.casefold() in names or ':' in entry.filename or entry.is_dir()
                or stat.S_IFMT(mode) not in (0, stat.S_IFREG) or mode & 0o7000 or entry.extra
                or entry.flag_bits & 1
                or entry.file_size > max(1, entry.compress_size) * 1000):
                raise IntegrityError('Unsicherer oder doppelter ZIP-Eintrag')
            file_limit = MAX_DATABASE_FILE if entry.filename == 'control.sqlite3' else MAX_FILE
            if entry.file_size > file_limit:
                raise IntegrityError(f'ZIP-Eintrag zu groß: {entry.filename} '
                                     f'({entry.file_size} Bytes; Grenze {file_limit} Bytes)')
            names.add(entry.filename.casefold())
        if 'manifest.json' not in names: raise IntegrityError('Sicherungsmanifest fehlt')
        for entry in entries:
            atomic_file(destination, entry.filename, archive.read(entry), mode=(entry.external_attr >> 16) & 0o777 or 0o444)
    return verify_package(destination)


def validate_database(path):
    # Never execute schema supplied by the archive. Compare it to trusted SQL first.
    with closing(sqlite3.connect(path.absolute().as_uri() + '?mode=ro&immutable=1', uri=True)) as db, closing(sqlite3.connect(':memory:')) as expected:
        versions = db.execute('SELECT version,sha256 FROM schema_migrations ORDER BY version').fetchall()
        available = sorted(MIGRATIONS.glob('*.sql'))
        if not versions or [v for v, _ in versions] != [p.name for p in available[:len(versions)]]:
            raise IntegrityError('Unbekannte Sicherungs-Schemaversion')
        expected.execute('CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, sha256 TEXT NOT NULL, applied_at TEXT NOT NULL)')
        for (version, sha), file in zip(versions, available):
            if hashlib.sha256(file.read_bytes()).hexdigest() != sha:
                raise IntegrityError('Migration stimmt nicht mit installierter Anwendung überein')
            for statement in statements(file.read_text()): expected.execute(statement)
        def schema(connection):
            return connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
        if schema(db) != schema(expected): raise IntegrityError('Sicherung enthält ein unbekanntes oder verändertes Datenbankschema')
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchall():
            raise IntegrityError('Sicherungsdatenbank beschädigt')


def boot(register):
    with register.transaction():
        register.connection.execute("UPDATE study_restore SET status='failed',error='Import durch Neustart unterbrochen; Sicherung erneut bewusst importieren.' WHERE status='restoring'")


def tick(register):
    row = register.connection.execute("SELECT * FROM study_restore WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
    if not row: return False
    identity = row['id']
    with register.transaction(): register.connection.execute("UPDATE study_restore SET status='restoring' WHERE id=?", (identity,))
    target = copy_settings(register.settings, identity)
    try:
        source = register.settings.staging / 'restore-uploads' / (identity + '.zip')
        with source.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != row['archive_sha256']:
                raise IntegrityError('Hochgeladene Sicherung verändert')
        # Retry only removes this import's unpublished partial copy, never an active study.
        for root in (target.control,target.artifacts,target.checkpoints,target.staging):
            if root.exists(): shutil.rmtree(root)
        with tempfile.TemporaryDirectory(dir=register.settings.staging, prefix='restore-check-') as temp:
            package = Path(temp)
            body, sha = unpack(source, package)
            validate_database(package / 'control.sqlite3')
            restore(package, target, expected_hash=sha)
        with closing(Register(target)) as copy:
            freeze = copy.get(body['backup']['freeze_id'], Freeze)
            phase = copy.get(freeze.phase_id, StudyPhase)
            study = copy.get(phase.study_id, Study)
            for record in copy.connection.execute('SELECT id FROM register_record').fetchall():
                value = copy.get(record[0])
                for _, ref in copy._references(value): copy.get(ref)
            if not ArtifactStore(target, copy).integrity()['complete']: raise IntegrityError('Sicherungsobjekte beschädigt')
            # Operational commands are never replayed, including automatic evaluation.
            with copy.transaction():
                copy.connection.execute("UPDATE ui_command SET status='recovery_required' WHERE status IN ('queued','processing')")
                copy.connection.execute("UPDATE pipeline_binding SET status='recovery_required',reason='Import: bewusste Fortsetzung erforderlich' WHERE status!='completed'")
                copy.connection.execute("UPDATE run_completion SET status='failed',reason='Import: Messung bewusst erneut anfordern' WHERE status IN ('running','retry_requested')")
                copy.connection.execute("INSERT OR IGNORE INTO run_completion(run_id,status,step,reason,updated_at) SELECT p.run_id,'failed','Importierter Stand','Messung bewusst erneut anfordern',? FROM pipeline_binding p JOIN run_binding r USING(run_id) WHERE p.status='completed' AND r.execution='terminal'", (datetime.now(timezone.utc).isoformat(),))
                copy.connection.execute("UPDATE exchange_job SET status='failed',error='Import: Export bei Bedarf neu anfordern' WHERE status IN ('queued','running')")
                copy.connection.execute("DELETE FROM study_restore")
                copy.connection.execute("UPDATE backup_download SET status='failed',error='Historischer Auftrag aus Sicherung; aktuellen Stand neu sichern',path=NULL")
                copy.connection.execute("UPDATE backup_request SET status='recovery_required',error='Import: aktuellen Stand als neue Sicherung vorbereiten',transfer_path=NULL,package_path=NULL,backup_id=NULL")
                copy.connection.execute("INSERT OR REPLACE INTO runtime_metadata VALUES ('imported_study_id',?)", (str(study.id),))
                copy.connection.execute("INSERT OR REPLACE INTO runtime_metadata VALUES ('import_namespace',?)", (identity,))
                # Separate installation identity prevents collisions with source containers.
                copy.connection.execute("INSERT OR REPLACE INTO runtime_metadata VALUES ('installation_id',?)", (str(uuid4()),))
            from .preparation import software_compatible
            compatible = all(software_compatible(copy.get(v).settings.software_commit) for v in freeze.configuration_version_ids.values())
        with register.transaction():
            register.connection.execute("UPDATE study_restore SET status='ready',title=?,study_id=?,freeze_id=?,source_manifest_hash=?,error=? WHERE id=?",
                (study.title, str(study.id), str(freeze.id), sha, None if compatible else 'Zum Fortsetzen ist der passende Softwarestand der fixierten Matrix erforderlich.', identity))
    except Exception as exc:
        with register.transaction(): register.connection.execute("UPDATE study_restore SET status='failed',error=? WHERE id=?", (str(exc), identity))
    return True


class RestoredWorkers:
    """Trusted installed worker per isolated copy. Import never loads archive code."""
    def __init__(self, settings): self.settings, self.processes = settings, {}
    def tick(self, register):
        for row in rows(register):
            if row['status'] != 'ready' or row['id'] in self.processes: continue
            settings = copy_settings(self.settings, row['id'])
            env = dict(os.environ, RESEARCH_RESTORED_WORKER='1')
            for key in ('control','artifacts','checkpoints','staging'):
                env['RESEARCH_' + key.upper() + '_DIR'] = str(getattr(settings, key))
            self.processes[row['id']] = subprocess.Popen([sys.executable, '-m', 'research_env.worker'], env=env)
    def close(self):
        for process in self.processes.values(): process.terminate()
        for process in self.processes.values():
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait()

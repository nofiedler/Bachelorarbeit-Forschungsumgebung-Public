import signal
from contextlib import closing
import sqlite3
import threading
import json
import os
from pathlib import Path

from .config import Settings
from .backup import Backups, BackupWorker
from .register import Register
from .locks import worker_lock
from .database import save_worker_status, connect
from .sandbox import docker_components
from .status import storage_status
from .adapter import CallJournal
from .artifacts import ArtifactStore
from .pipeline import Pipeline
from .pipeline_runner import InternalRunner
from .providers import OpenRouterAdapter
from .sandbox_runtime import RuntimeImages, Sandbox
from .scheduler import Scheduler, mark_worker_recovery
from .pipeline_mock import PipelineMockAdapter
from .domain import Run, ConfigurationVersion
from .preparation import boot_commands, process_command, software_identity


def pipeline_service(register):
    """Trusted worker alone holds key/socket. No HTTP-triggered background work."""
    import docker
    images = RuntimeImages(**json.loads(Path(__file__).with_name('pipeline_runtime.lock.json').read_text()))
    client = docker.from_env(timeout=None)
    sandbox = Sandbox(ArtifactStore(register.settings, register), client, images=images, assets=Path(__file__).resolve().parents[2])
    row = register.connection.execute("SELECT run_id FROM pipeline_binding WHERE status IN ('ready','running','pause_requested','abort_requested','resume_requested') ORDER BY rowid LIMIT 1").fetchone()
    if not row:
        raise ValueError('Kein aktiver Pipelineauftrag')
    run = register.get(row['run_id'], Run)
    conf = register.get(run.configuration_version_id, ConfigurationVersion)
    from .application_settings import model_key
    adapter = PipelineMockAdapter() if register._is_mock(conf) else OpenRouterAdapter(api_key=model_key(register.settings))
    runner=InternalRunner(sandbox)
    runner.software_identity=software_identity()
    pipeline = Pipeline(register, CallJournal(register,software_commit=runner.software_identity), adapter, runner)
    return Scheduler(pipeline)


def immediate_call_observer(settings, current_scheduler, stopped):
    """End this process's blocked call/job only after durable own stop evidence.

    Polling observes a manual action; it imposes no provider/job deadline. The
    remote generation may continue. os._exit preserves unknown dispatch and
    requires a fresh explicit action after the next worker boot.
    """
    while not stopped.wait(.05):
        # A separate own connection records controls even while a graph node
        # waits. It cannot execute/replay the graph or start a generation.
        try:
            with closing(Register(settings)) as control_register:
                process_command(control_register, controls=True)
        except (sqlite3.Error, OSError, ValueError):
            pass
        scheduler = current_scheduler()
        if scheduler is None:
            continue
        try:
            with closing(connect(settings, readonly=True)) as db:
                row = db.execute("SELECT run_id FROM pipeline_binding WHERE status='abort_requested' AND process_id=? ORDER BY rowid LIMIT 1", (str(scheduler.process.id),)).fetchone()
                if not row:
                    continue
                # A new request must wait for its new diagnosis/decision. An
                # old immediate decision cannot stop a later graceful request.
                event = db.execute("SELECT payload FROM register_record WHERE kind='Event' AND json_extract(payload,'$.run_id')=? AND json_extract(payload,'$.event_type') IN ('abort_requested','abort_decision') ORDER BY CAST(json_extract(payload,'$.sequence') AS INTEGER) DESC LIMIT 1", (row['run_id'],)).fetchone()
                body = json.loads(event['payload']) if event else {}
                if body.get('event_type') != 'abort_decision' or body.get('details', {}).get('immediate') is not True:
                    continue
                if body.get('details',{}).get('target_process_id')!=str(scheduler.process.id):
                    continue
                if not body.get('evidence_ids'):
                    continue
            save_worker_status(settings, [], state='stopped')
            # Only this trusted worker, never a PID obtained from user/model
            # data. Diagnosis/unknown journal were committed before the event.
            os._exit(80)
        except (sqlite3.Error, OSError, ValueError):
            continue


def run(settings):
    stopped = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())
    register = Register(settings)
    mark_worker_recovery(register)
    boot_commands(register)
    backup_worker = BackupWorker(Backups(settings, register))
    from . import backup_ui
    backup_ui.boot(register)
    from . import exchange
    exchange.boot(register)
    from . import study_restore
    study_restore.boot(register)
    restored_workers = study_restore.RestoredWorkers(settings) if not os.environ.get("RESEARCH_RESTORED_WORKER") else None
    import docker
    from .completion import CompletionWorker
    images=RuntimeImages(**json.loads(Path(__file__).with_name('pipeline_runtime.lock.json').read_text()))
    # Recovery and persisted controls remain available when Docker is offline.
    # Construct the client lazily, only for an actual evaluation request.
    class LazyDocker:
        client=None
        def __getattr__(self,name):
            if self.client is None:self.client=docker.from_env(timeout=None)
            return getattr(self.client,name)
        def close(self):
            if self.client is not None:self.client.close()
    measurement_client=LazyDocker()
    completion=CompletionWorker(register,Sandbox(ArtifactStore(settings,register),measurement_client,images=images,assets=Path(__file__).resolve().parents[2]))
    completion.boot()
    scheduler = None
    service_run_id = None
    observer = threading.Thread(target=immediate_call_observer, args=(settings, lambda: scheduler, stopped), daemon=True, name="pipeline-manual-stop")
    observer.start()
    while not stopped.is_set():
        components = docker_components() + [storage_status("Checkpoints", settings.checkpoints, writable=True)]
        try:
            save_worker_status(settings, components)
            backup_worker.tick()
            backup_ui.tick(register)
            exchange.tick(register)
            if restored_workers:
                study_restore.tick(register)
                restored_workers.tick(register)
            process_command(register)
            pending = register.connection.execute("SELECT run_id FROM pipeline_binding WHERE status IN ('ready','running','pause_requested','abort_requested','resume_requested') ORDER BY rowid LIMIT 1").fetchone()
            if pending:
                if scheduler is None or service_run_id != pending['run_id']:
                    if scheduler is not None:
                        scheduler.pipeline.close(); scheduler.pipeline.adapter.close(); scheduler.pipeline.runner.close()
                    scheduler = pipeline_service(register)
                    service_run_id = pending['run_id']
                scheduler.tick()
            else:
                completion.tick()
        except (sqlite3.Error, OSError, ValueError) as exc:
            print("Worker: Kontrollspeicher nicht beschreibbar; Volume, Berechtigung und Speicher prüfen.", flush=True)
        stopped.wait(2)
    stopped.set()
    observer.join()
    if restored_workers: restored_workers.close()
    register.close()
    measurement_client.close()
    if scheduler is not None:
        scheduler.pipeline.close()
        scheduler.pipeline.adapter.close()
        scheduler.pipeline.runner.close()
    try:
        save_worker_status(settings, [], state="stopped")
    except (sqlite3.Error, OSError):
        print("Worker: Stoppstatus konnte nicht gespeichert werden.", flush=True)


def main():
    settings = Settings.from_environment()
    with worker_lock(settings):
        run(settings)


if __name__ == "__main__":
    main()

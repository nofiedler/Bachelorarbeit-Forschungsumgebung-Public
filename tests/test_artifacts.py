"""Issue #6: actual bytes/processes/SQLite, cost-free synthetic component cases."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading
import tempfile
import stat
from uuid import uuid4

import pytest

import test_register as fixtures
from research_env.artifacts import ArtifactStore, IntegrityError, atomic_file, read_regular
from research_env.backup import Backups, BackupWorker, checkpoint_writer, preserve_file, restore, verify_package, write_checkpoint
from research_env.config import Settings
from research_env.database import connect, migrate
from research_env.domain import *
from research_env.locks import worker_lock, write_barrier
from research_env.register import GateError, Register
from research_env.snapshots import ProcessWriters, Snapshots, inventory


@pytest.fixture
def instance(tmp_path, monkeypatch):
    settings = Settings(*(tmp_path / 'instance' / p for p in ('control', 'artifacts', 'checkpoints', 'staging')))
    for p in (settings.control, settings.artifacts, settings.checkpoints, settings.staging):
        p.mkdir(parents=True)
    migrate(settings)
    register = Register(settings)
    store = ArtifactStore(settings, register)
    def artifact(r, run=None, **kwargs):
        return ArtifactStore(settings, r).store(b'TECHNICAL-FIXTURE: real synthetic raw bytes', run_id=run.id if run else None, **kwargs)
    monkeypatch.setattr(fixtures, 'artifact', artifact)
    fixture = fixtures.setup_study(register)
    freeze = register.freeze(fixtures.freeze_value(register, fixture))
    backups = Backups(settings, register)
    worker = BackupWorker(backups)
    yield register, settings, store, fixture, freeze, backups, worker
    register.close()


def secured(instance, target):
    r, settings, store, fixture, freeze, backups, worker = instance
    job = backups.enqueue(freeze.id, idempotency_key=str(uuid4()))
    value = worker.tick()
    assert value and value.substantive_revision == r.revision(freeze.phase_id)
    copied = backups.transfer(job.id, target)
    before = r.revision(freeze.phase_id)
    receipt = backups.confirm(job.id, person='TECHNICAL-FIXTURE: technical agent', external_medium='TECHNICAL-FIXTURE: local separate directory; no SSD claim', confirmed_at=fixtures.NOW, synthetic=True)
    assert r.revision(freeze.phase_id) == before and r.backup_status(freeze.id) == 'current'
    return job, value, copied, receipt


def workspace(instance, tmp_path):
    r, settings, store, fixture, freeze, backups, worker = instance
    secured(instance, tmp_path / 'target-freeze')
    run, _ = fixtures.start(r, freeze, fixture)
    tree = tmp_path / 'workspace'
    tree.mkdir()
    (tree / 'app').mkdir()
    (tree / 'app' / 'Module.php').write_text('<?php // last candidate')
    (tree / 'artisan').write_text('#!/usr/bin/env php\n')
    (tree / 'artisan').chmod(0o755)
    (tree / 'composer.lock').write_text('{"synthetic":"locked"}')
    (tree / '.env.example').write_text('APP_NAME=fixture')
    (tree / '.env').write_text('DO_NOT_ARCHIVE=secret-fixture')
    (tree / '.git').mkdir()
    (tree / '.git' / 'excluded').write_text('history fixture')
    deps = tmp_path / 'deps'
    deps.mkdir()
    (deps / 'autoload.php').write_text('<?php // saved dependency bytes')
    snapshots = Snapshots(store)
    dependency = snapshots.dependencies(deps, source={'kind': 'synthetic installed dependency fixture'}, runtime={'image': 'synthetic only'})
    args = dict(run_id=run.id, scaffold_id=fixture['settings'].scaffold_id, dependency_ids=(dependency.id,), internal_tests_hash=hashlib.sha256(b'unchanged internal tests').hexdigest())
    return run, tree, snapshots, args


@pytest.mark.parametrize('window,linked', [('before_file_commit', False), ('after_file_commit', False), ('before_db_link', False), ('after_db_link', True)])
def test_a01_actual_crash_windows(instance, window, linked):
    r, settings, store, *_ = instance
    data = ('crash-' + window).encode()
    sha = hashlib.sha256(data).hexdigest()
    def crash(where):
        if where == window:
            raise RuntimeError('injected crash')
    with pytest.raises(RuntimeError):
        store.store(data, crash=crash)
    refs = [a for a in r.all(Artifact) if a.sha256 == sha]
    assert bool(refs) == linked
    report = store.integrity()
    if window == 'before_file_commit' or linked:
        assert report['complete']
    else:
        assert any(p['kind'] == 'orphan_file' for p in report['problems'])


def test_a02_missing_tamper_and_collision(instance):
    r, settings, store, *_ = instance
    a = store.store(b'first')
    path = settings.artifacts / store.object_path(a.sha256)
    path.chmod(0o600)
    path.write_bytes(b'manipulated')
    with pytest.raises(IntegrityError):
        store.read(a.id)
    with pytest.raises(IntegrityError):
        store.store(b'first')
    assert any(p['kind'] == 'modified_or_unsafe' for p in store.integrity()['problems'])
    path.unlink()
    assert any(p['kind'] == 'missing_file' for p in store.integrity()['problems'])


@pytest.mark.parametrize('path', ['../escape', '/escape', 'foo//bar', 'foo/./bar', 'foo/../bar', 'back\\slash'])
def test_a03_path_traversal(instance, path):
    _, settings, *_ = instance
    with pytest.raises(IntegrityError):
        atomic_file(settings.checkpoints, path, b'data')


def test_a04_symlink_hardlink_special_modes(instance, tmp_path):
    _, settings, store, *_ = instance
    tree = tmp_path / 'unsafe'
    tree.mkdir()
    outside = tmp_path / 'outside'
    outside.write_bytes(b'do not follow')
    (tree / 'link').symlink_to(outside)
    with pytest.raises(IntegrityError):
        inventory(tree)
    (tree / 'link').unlink()
    os.link(outside, tree / 'hardlink')
    with pytest.raises(IntegrityError):
        inventory(tree)
    (tree / 'hardlink').unlink()
    # Docker Desktop bind filesystems may remove setuid bits. Check the
    # actual mode, then verify the unchanged rule on native Linux tmpfs.
    (tree / 'privileged').write_bytes(b'x')
    (tree / 'privileged').chmod(0o4755)
    bind_mode = stat.S_IMODE((tree / 'privileged').stat().st_mode)
    with tempfile.TemporaryDirectory(prefix='m3-mode-', dir='/tmp') as native:
        local = Path(native)
        (local / 'privileged').write_bytes(b'x')
        (local / 'privileged').chmod(0o4755)
        native_mode = stat.S_IMODE((local / 'privileged').stat().st_mode)
        assert native_mode == 0o4755  # Actual precondition, not an assumed bit.
        with pytest.raises(IntegrityError):
            inventory(local)
        (tmp_path / 'mode-probe.json').write_text(json.dumps({'bind_requested_mode':0o4755,'bind_actual_mode':bind_mode,'native_tmpfs_actual_mode':native_mode,'special_mode_rejected':True}))
    prefix = settings.artifacts / 'objects' / 'sha256' / 'ff'
    prefix.symlink_to(outside.parent, target_is_directory=True)
    assert any(p['kind'] == 'unsafe_path' for p in store.integrity()['problems'])


def test_a05_atomic_concurrent_objects(instance):
    _, settings, store, *_ = instance
    def writer(_):
        r = Register(settings)
        try:
            return ArtifactStore(settings, r).store(b'same concurrent bytes').sha256
        finally:
            r.close()
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(writer, range(8)))
    assert len(set(values)) == 1 and store.integrity()['complete']


def test_a06_no_transaction_during_filework(instance):
    r, _, store, *_ = instance
    with r.transaction():
        with pytest.raises(RuntimeError):
            store.store(b'not inside transaction')


def test_s01_last_regression_complete_restore_and_readonly(instance, tmp_path):
    r, settings, store, fixture, *_ = instance
    run, tree, snapshots, args = workspace(instance, tmp_path)
    before = snapshots.capture(tree, role='post_migrate', **args)
    (tree / 'app' / 'Module.php').write_text('<?php // REGRESSION preserved')
    rejected = store.store(b'raw rejected ../attempt.php', run_id=run.id, artifact_type='rejected_take')
    failure = store.store(b'first failed internal test preserved', run_id=run.id, artifact_type='failure')
    args.update(rejected_changes_artifact_id=rejected.id, process_artifact_ids=(failure.id,), repair_count=1)
    repair = snapshots.capture(tree, role='post_repair', **args)
    # Actual live writer is stopped before hashing.
    proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(3600)'])
    sealed = snapshots.seal(tree, writers=ProcessWriters((proc,)), **args)
    assert proc.poll() is not None and sealed.tree_hash == repair.tree_hash != before.tree_hash
    sealed_root = settings.artifacts / 'sealed' / str(sealed.id)
    assert not any(x['mode'] & 0o222 for x in inventory(sealed_root))
    shutil.rmtree(tree)
    restored = tmp_path / 'restored-candidate'
    assert snapshots.restore(sealed.id, restored) == sealed.tree_hash
    assert digest(inventory(restored)) == sealed.tree_hash
    assert (restored / 'artisan').stat().st_mode & 0o111
    assert (restored / 'composer.lock').exists() and (restored / 'vendor/autoload.php').exists()
    assert not (restored / '.env').exists() and not (restored / '.git').exists()
    copy = tmp_path / 'inspection'
    snapshots.inspection_copy(sealed.id, copy)
    (copy / 'app/Module.php').write_text('inspection mutation')
    assert b'REGRESSION' in (sealed_root / 'app/Module.php').read_bytes()
    assert store.read(rejected.id).startswith(b'raw rejected') and store.read(failure.id).startswith(b'first failed')
    with pytest.raises(GateError):
        snapshots.capture(restored, role='post_repair', **args)


def test_s02_identity_ignores_ids_times_but_tracks_mode(instance, tmp_path):
    run, tree, snapshots, args = workspace(instance, tmp_path)
    one = snapshots.capture(tree, role='post_migrate', **args)
    os.utime(tree / 'artisan', (1, 1))
    two = snapshots.capture(tree, role='post_migrate', **args)
    assert one.id != two.id and one.tree_hash == two.tree_hash
    (tree / 'artisan').chmod(0o644)
    three = snapshots.capture(tree, role='post_migrate', **args)
    assert three.tree_hash != one.tree_hash


def test_s03_writer_negative_and_manipulation(instance, tmp_path):
    run, tree, snapshots, args = workspace(instance, tmp_path)
    entered, release = threading.Event(), threading.Event()
    def writer():
        with snapshots.writer_lease(tree):
            entered.set()
            release.wait()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(writer)
        entered.wait()
        try:
            with pytest.raises(BlockingIOError):
                snapshots.seal(tree, writers=ProcessWriters(), **args)
        finally:
            release.set()
        future.result()
    def mutate(where):
        if where == 'before_snapshot_link':
            (tree / 'artisan').write_text('changed while sealing')
    # capture must also recheck at link boundary, even injected external mutation.
    with pytest.raises(IntegrityError):
        snapshots.capture(tree, role='post_migrate', crash=mutate, **args)
    assert not any(s.role == 'sealed' for s in snapshots.register.all(CandidateSnapshot))


def test_s04_missing_candidate_and_internal_test_immutability(instance, tmp_path):
    r, *_ = instance
    run, tree, snapshots, args = workspace(instance, tmp_path)
    snapshots.capture(tree, role='post_migrate', **args)
    with pytest.raises(GateError):
        snapshots.capture(tree, role='post_repair', **(args | {'internal_tests_hash': '0' * 64}))
    shutil.rmtree(tree)
    assert snapshots.seal(tree, writers=ProcessWriters(), **args) is None
    assert r.state(run.id).seal == 'no_candidate'
    assert any(e.event_type == 'candidate_missing' for e in r.all(Event))


def test_b01_firstfreeze_missing_transfer_and_nonrecursive_receipt(instance, tmp_path):
    r, settings, store, fixture, freeze, backups, worker = instance
    assert r.backup_status(freeze.id) == 'required'
    with pytest.raises(GateError):
        fixtures.start(r, freeze, fixture)
    job = backups.enqueue(freeze.id, idempotency_key='first-freeze')
    assert backups.enqueue(freeze.id, idempotency_key='first-freeze').id == job.id
    backup = worker.tick()
    assert r.backup_status(freeze.id) == 'awaiting_confirmation'
    with pytest.raises(GateError):
        backups.confirm(job.id, person='fixture', external_medium='fixture', confirmed_at=fixtures.NOW)
    with pytest.raises(GateError):
        fixtures.start(r, freeze, fixture)
    backups.transfer(job.id, tmp_path / 'synthetic-target')
    revision = r.revision(freeze.phase_id)
    backups.confirm(job.id, person='TECHNICAL-FIXTURE', external_medium='TECHNICAL-FIXTURE: directory, no hardware evidence', confirmed_at=fixtures.NOW, synthetic=True)
    assert r.revision(freeze.phase_id) == revision == backup.substantive_revision
    run, _ = fixtures.start(r, freeze, fixture)
    assert run.purpose == 'main' and r.backup_status(freeze.id) == 'stale'


def test_b02_roundtrip_without_workspace_preserves_versions_drafts_checkpoint(instance, tmp_path):
    r, settings, store, fixture, freeze, backups, worker = instance
    run, tree, snapshots, args = workspace(instance, tmp_path)
    candidate = snapshots.seal(tree, writers=ProcessWriters(), **args)
    r.set_state(run.id, r.state(run.id).model_copy(update={'execution': 'terminal', 'terminal_cause': 'finished', 'ended_at': fixtures.NOW, 'evaluation': 'manual_pending'}))
    comp = Compatibility(candidate_id=candidate.id, candidate_hash=candidate.tree_hash, phase_id=run.phase_id, contract_id=fixture['settings'].contract_id, suite_id=run.suite_id, tool_id=fixture['tool'].id)
    raw = store.store(b'failed original measurement raw', run_id=run.id)
    first = r.add(MeasurementAttempt(code=f'FAILED-MEASURE-{uuid4()}', run_id=run.id, measurement_key='functional', compatibility=comp, fixture_id=comp.suite_id, revision=1, predecessor_id=None, completion='completed', suite_kind='study_holdout', result=Observation(status='technical_missing',unit='result',reason='TECHNICAL-FIXTURE: evaluator failed; raw original retained'), raw_artifact_ids=(raw.id,), exit_code=fixtures.observed(1), reason='TECHNICAL-FIXTURE: failed evaluator attempt'))
    second = fixtures.measurement(r, run, comp, raw, prev=first)
    draft = fixtures.review(r, run, comp, raw, fixture['rubric'], (second.id,), completion='draft')
    write_checkpoint(settings, f'{run.id}/graph.json', canonical({'run': str(run.id), 'raw': str(raw.id), 'candidate': str(candidate.id)}).encode())
    with checkpoint_writer(settings), closing(sqlite3.connect(settings.checkpoints / 'graph.sqlite3')) as c:
        c.execute('CREATE TABLE checkpoint(id TEXT PRIMARY KEY, payload TEXT)')
        c.execute('INSERT INTO checkpoint VALUES (?,?)', (str(run.id), str(candidate.id)))
        c.commit()
    job, backup, package, receipt = secured(instance, tmp_path / 'current-target')
    post_capture_ids = {item['artifact_id'] for item in backups.history(job.id) if item['operation'] != 'create_started' and 'artifact_id' in item}
    source_records = [(row['id'], row['sha256']) for row in r.connection.execute('SELECT id,sha256 FROM register_record ORDER BY id') if row['id'] not in {str(backup.id), str(receipt.id), *post_capture_ids}]
    source_refs = [tuple(row) for row in r.connection.execute('SELECT * FROM register_reference WHERE owner_id NOT IN (?,?) ORDER BY 1,2,3', (str(backup.id), str(receipt.id)))]
    revision = r.revision(freeze.phase_id)
    shutil.rmtree(tree)
    restored_settings = Settings(*(tmp_path / 'fresh' / name for name in ('control', 'artifacts', 'checkpoints', 'staging')))
    result = restore(package, restored_settings, expected_hash=backup.manifest_hash)
    assert result['generation_started'] is False and result['integrity']['complete']
    restored = Register(restored_settings)
    try:
        restored_records = [(row['id'], row['sha256']) for row in restored.connection.execute('SELECT id,sha256 FROM register_record WHERE id!=? ORDER BY id', (str(backup.id),))]
        assert restored_records == source_records
        restored_refs = [tuple(row) for row in restored.connection.execute('SELECT * FROM register_reference WHERE owner_id!=? ORDER BY 1,2,3', (str(backup.id),))]
        assert restored_refs == source_refs
        assert restored.revision(freeze.phase_id) == revision
        assert restored.get(draft.id).completion == 'draft' and restored.get(first.id).result.status == 'technical_missing'
        assert restored.get(second.id).predecessor_id == first.id
        assert not restored.connection.execute("SELECT 1 FROM job_state WHERE status IN ('ready','running')").fetchone()
        assert (restored_settings.checkpoints / f'{run.id}/graph.json').read_bytes() == (settings.checkpoints / f'{run.id}/graph.json').read_bytes()
        with closing(sqlite3.connect(restored_settings.checkpoints / 'graph.sqlite3')) as c:
            assert c.execute('SELECT * FROM checkpoint').fetchone() == (str(run.id), str(candidate.id))
        Snapshots(ArtifactStore(restored_settings, restored)).inspection_copy(candidate.id, tmp_path / 'fresh-inspection')
        assert digest(inventory(tmp_path / 'fresh-inspection')) == candidate.tree_hash
    finally:
        restored.close()
    # A saved draft is sufficient: next fixed main ID can start after actual backup/receipt.
    next_run, _ = fixtures.start(r, freeze, fixture)
    assert next_run.id != run.id


def test_b03_failed_and_stale_transfer_race(instance, tmp_path):
    r, settings, store, fixture, freeze, backups, worker = instance
    run, tree, snapshots, args = workspace(instance, tmp_path)
    candidate = snapshots.seal(tree, writers=ProcessWriters(), **args)
    r.set_state(run.id, r.state(run.id).model_copy(update={'execution': 'terminal', 'terminal_cause': 'finished', 'ended_at': fixtures.NOW}))
    comp = Compatibility(candidate_id=candidate.id, candidate_hash=candidate.tree_hash, phase_id=run.phase_id, contract_id=fixture['settings'].contract_id, suite_id=run.suite_id, tool_id=fixture['tool'].id)
    raw = store.store(b'measurement', run_id=run.id)
    measure = fixtures.measurement(r, run, comp, raw)
    draft = fixtures.review(r, run, comp, raw, fixture['rubric'], (measure.id,), completion='draft')
    job = backups.enqueue(freeze.id, idempotency_key='race')
    backup = worker.tick()
    once = []
    def concurrent_change(where):
        if not once:
            once.append(fixtures.review(r, run, comp, raw, fixture['rubric'], (measure.id,), prev=draft))
    backups.transfer(job.id, tmp_path / 'slow-target', crash=concurrent_change)
    assert backup.substantive_revision < r.revision(freeze.phase_id)
    assert r.get(backup.id).manifest_hash == backup.manifest_hash
    with pytest.raises(GateError, match='veraltet'):
        backups.confirm(job.id, person='fixture', external_medium='fixture', confirmed_at=fixtures.NOW)
    with pytest.raises(GateError):
        fixtures.start(r, freeze, fixture)
    next_job = backups.enqueue(freeze.id, idempotency_key='failed-copy')
    worker.tick()
    def failure(where):
        raise OSError('synthetic target disk failure')
    with pytest.raises(OSError):
        backups.transfer(next_job.id, tmp_path / 'failed-target', crash=failure)
    assert r.backup_status(freeze.id) == 'failed'
    with pytest.raises(GateError):
        fixtures.start(r, freeze, fixture)
    backups.recover(next_job.id, decision='TECHNICAL-FIXTURE: conscious transfer retry, no generation')
    backups.transfer(next_job.id, tmp_path / 'retry-target')
    backups.confirm(next_job.id, person='fixture', external_medium='TECHNICAL-FIXTURE: retry local directory', confirmed_at=fixtures.NOW, synthetic=True)
    assert r.backup_status(freeze.id) == 'current'
    failures = [item for item in backups.history(next_job.id) if item['operation']=='transfer_failed']
    assert len(failures)==1 and 'synthetic target disk failure' in failures[0]['details']['error']
    assert failures[0]['details']['target'].startswith(str(tmp_path/'failed-target'))


def test_b04_global_active_request_negative(instance):
    r, settings, store, fixture, freeze, backups, worker = instance
    # Active request in PILOT area also blocks a full-instance MAIN freeze backup.
    pilot = fixture['pilot']
    raw = store.store(b'request', run_id=pilot.id)
    call = r.add(ModelCall(code='GLOBAL-ACTIVE', run_id=pilot.id, node='analyzer', sequence=1, model_package_id=fixture['settings'].model_a, request_hash=raw.sha256, messages_artifact_id=raw.id, parameters={'seed': 1}, input_artifact_ids=(), allowed_paths=(), tools=()))
    attempt = r.add(TransportAttempt(code='GLOBAL-TRANSPORT', call_id=call.id, number=1, send_status='prepared', request_id=None, generation_id=None, sent_at=None, ended_at=None, actual_model=fixtures.missing(), actual_provider=fixtures.missing(), response_artifact_id=None, error_artifact_id=None, format_status='pending', usage={}, billing_status='not_due'))
    r.add(TransportStateEvent(code='GLOBAL-DISPATCH', transport_id=attempt.id, sequence=1, status='dispatching', request_id=None, generation_id=None, response_artifact_id=None, error_artifact_id=None, actual_model=fixtures.missing(), actual_provider=fixtures.missing(), usage={}, billing_status='unresolved', format_status='pending'))
    job = backups.enqueue(freeze.id, idempotency_key='global-active')
    with pytest.raises(GateError, match='Aktive Requests'):
        worker.tick()
    assert backups.request(job.id)['status'] == 'failed'
    assert not r.all(Backup)


def test_b05_restart_jobs_explicit_recovery_no_generation(instance):
    r, settings, store, fixture, freeze, backups, worker = instance
    job = backups.enqueue(freeze.id, idempotency_key='restart-before-claim')
    restarted = BackupWorker(backups)
    assert backups.request(job.id)['status'] == 'recovery_required' and restarted.tick() is None
    with pytest.raises(GateError):
        backups.recover(job.id, decision='')
    backups.recover(job.id, decision='TECHNICAL-FIXTURE: explicitly resume backup only')
    backup = restarted.tick()
    assert backup and not r.all(ModelCall)
    assert len(r.all(Run)) == 1  # Existing fixture pilot, no extra generation.


def test_b06_backup_crash_restart_and_orphan_package(instance):
    r, settings, store, fixture, freeze, backups, worker = instance
    job = backups.enqueue(freeze.id, idempotency_key='crash-before-link')
    def crash(where):
        if where == 'before_backup_link':
            raise SystemExit('actual abrupt control window')
    with pytest.raises(SystemExit):
        worker.tick(crash=crash)
    assert backups.request(job.id)['status'] == 'creating' and not r.all(Backup)
    assert list((settings.staging / 'backups').glob('*/manifest.json'))
    assert any(p['kind']=='orphan_backup' for p in backups.integrity()['problems'])
    restarted = BackupWorker(backups)
    assert restarted.tick() is None
    assert backups.request(job.id)['status'] == 'recovery_required'
    backups.recover(job.id, decision='TECHNICAL-FIXTURE: rerun safe internal capture, no network')
    assert restarted.tick() is not None


def test_b07_barrier_and_two_workers(instance):
    r, settings, store, fixture, freeze, backups, worker = instance
    entered, finished = threading.Event(), threading.Event()
    def writer():
        other = Register(settings)
        entered.set()
        try:
            other.add(Study(code='CONCURRENT', title='synthetic', design_version='1', data_origin='synthetic', provenance='controlled writer'))
            write_checkpoint(settings, 'concurrent.json', b'checkpoint')
            finished.set()
        finally:
            other.close()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with write_barrier(settings, exclusive=True):
            future = pool.submit(writer)
            entered.wait()
            assert not finished.wait(0.1)
        future.result()
    assert finished.is_set()
    with worker_lock(settings):
        def contender():
            with pytest.raises(BlockingIOError):
                with worker_lock(settings):
                    pass
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(contender).result()


def test_b08_target_inside_instance_tamper_and_freshness(instance, tmp_path):
    r, settings, store, fixture, freeze, backups, worker = instance
    job = backups.enqueue(freeze.id, idempotency_key='unsafe-target')
    backup = worker.tick()
    with pytest.raises(IntegrityError, match='Getrenntes Ziel'):
        backups.transfer(job.id, settings.artifacts / 'same-volume')
    assert backups.request(job.id)['status'] == 'failed'
    backups.recover(job.id, decision='TECHNICAL-FIXTURE: reject unsafe target and select separated directory')
    package = backups.transfer(job.id, tmp_path / 'target')
    (package / 'control.sqlite3').write_bytes(b'corrupt')
    with pytest.raises(IntegrityError):
        verify_package(package, expected_hash=backup.manifest_hash)
    with pytest.raises(IntegrityError):
        backups.confirm(job.id, person='fixture', external_medium='fixture', confirmed_at=fixtures.NOW)
    with pytest.raises(IntegrityError):
        restore(package, settings, expected_hash=backup.manifest_hash)


@pytest.mark.parametrize('window', ['before_file_commit','after_file_commit','before_db_link','after_db_link'])
def test_a07_real_process_crash_retains_evidence(instance, window):
    r, settings, store, *_ = instance
    script = """import os,sys
from pathlib import Path
from research_env.config import Settings
from research_env.register import Register
from research_env.artifacts import ArtifactStore
settings=Settings(*(Path(x) for x in sys.argv[1:5]))
r=Register(settings)
def crash(where):
    if where==sys.argv[5]: os._exit(73)
ArtifactStore(settings,r).store(('actual-process-crash-'+sys.argv[5]).encode(),crash=crash)
"""
    process = subprocess.run([sys.executable, '-c', script, *(str(x) for x in (settings.control,settings.artifacts,settings.checkpoints,settings.staging)), window])
    assert process.returncode == 73
    report = store.integrity()
    if window == 'after_db_link':
        assert report['complete']
        assert any(a.sha256 == hashlib.sha256(('actual-process-crash-'+window).encode()).hexdigest() for a in r.all(Artifact))
    else:
        assert not report['complete']
        expected = 'uncommitted_file' if window=='before_file_commit' else 'orphan_file'
        assert any(p['kind']==expected for p in report['problems'])


def test_b09_after_link_crash_explicit_recovery(instance):
    r, settings, store, fixture, freeze, backups, worker = instance
    job = backups.enqueue(freeze.id,idempotency_key='crash-after-link')
    def crash(where):
        if where == 'after_backup_link': raise SystemExit('crash after complete immutable backup')
    with pytest.raises(SystemExit): worker.tick(crash=crash)
    assert backups.request(job.id)['backup_id']
    original = backups.request(job.id)['backup_id']
    restarted = BackupWorker(backups)
    assert backups.request(job.id)['status']=='recovery_required' and restarted.tick() is None
    backups.recover(job.id,decision='TECHNICAL-FIXTURE: reuse complete snapshot, do not recapture')
    assert backups.request(job.id)['backup_id']==original and backups.request(job.id)['status']=='internal_ready'
    assert len(r.all(Backup))==1


def test_b10_dedup_reuses_bytes_and_rejects_collision(instance, tmp_path):
    target = tmp_path/'dedup'
    target.mkdir()
    preserve_file(target,'object',b'original')
    preserve_file(target,'object',b'original')
    with pytest.raises(IntegrityError,match='Backupkollision'):
        preserve_file(target,'object',b'different')
    assert (target/'object').read_bytes()==b'original'
    (target/'object').chmod(0o600)
    with pytest.raises(IntegrityError,match='Backupkollision'):
        preserve_file(target,'object',b'original')


def test_s06_scaffold_cache_placeholders_preserved_generated_cache_excluded(instance, tmp_path):
    run, tree, snapshots, args = workspace(instance,tmp_path)
    cache=tree/'bootstrap/cache'
    cache.mkdir(parents=True)
    (cache/'.gitignore').write_text('*\n!.gitignore\n')
    (cache/'compiled.php').write_text('temporary generated cache')
    candidate=snapshots.seal(tree,writers=ProcessWriters(),**args)
    destination=tmp_path/'cache-restored'
    snapshots.restore(candidate.id,destination)
    assert (destination/'bootstrap/cache/.gitignore').exists()
    assert not (destination/'bootstrap/cache/compiled.php').exists()


def test_a08_object_base_symlink_not_traversed(instance, tmp_path):
    r,settings,store,*_=instance
    original=settings.artifacts/'objects/sha256'
    moved=settings.artifacts/'objects/saved'
    original.rename(moved)
    external=tmp_path/'external'
    external.mkdir()
    (external/'secret').write_text('should not be listed or read')
    original.symlink_to(external,target_is_directory=True)
    report=store.integrity()
    assert report['complete'] is False
    assert report['problems'][0]['path']=='objects/sha256'
    assert 'secret' not in json.dumps(report)


@pytest.mark.parametrize('case', ['file', 'parent_file', 'unavailable', 'readonly', 'symlink', 'ancestor_symlink', 'internal', 'conversion'])
def test_b11_transfer_preflight_failure_persisted_and_recovered(instance, tmp_path, case):
    r, settings, store, fixture, freeze, backups, worker = instance
    job = backups.enqueue(freeze.id, idempotency_key='preflight-' + case)
    backup = worker.tick()
    original = Path(backups.request(job.id)['package_path'])
    before = inventory(original)
    target = tmp_path / 'selected-target'
    if case == 'file':
        target.write_bytes(b'original target file')
        assert target.is_file() and not target.is_symlink()
    elif case == 'parent_file':
        parent = tmp_path / 'file-parent'
        parent.write_bytes(b'original parent file')
        target = parent / 'unavailable-directory'
        assert parent.is_file()
    elif case == 'unavailable':
        assert sys.platform == 'linux' and Path('/proc/self/status').is_file()
        target = Path('/proc/self/status/unavailable-directory')
    elif case == 'readonly':
        assert sys.platform == 'linux'
        target = Path('/sys') / ('research-backup-fixture-' + str(uuid4()))
        assert os.statvfs('/sys').f_flag & os.ST_RDONLY and not target.exists()
    elif case in ('symlink', 'ancestor_symlink'):
        real = tmp_path / 'real-target'
        real.mkdir()
        target.symlink_to(real, target_is_directory=True)
        assert target.is_symlink()
        if case == 'ancestor_symlink':
            target = target / 'child'
    elif case == 'internal':
        target = settings.artifacts / 'invalid-target'
        assert not target.exists()
    else:
        target = None
    expected = (OSError, IntegrityError, TypeError)
    with pytest.raises(expected):
        backups.transfer(job.id, target)
    request = backups.request(job.id)
    assert request['status'] == 'failed' and request['error'] and not request['transfer_path']
    failure = [x for x in backups.history(job.id) if x['operation'] == 'transfer_failed']
    assert len(failure) == 1 and failure[0]['details']['target'] == str(target)
    assert failure[0]['details']['backup_id'] == str(backup.id)
    assert failure[0]['job_id'] == str(job.id) and datetime.fromisoformat(failure[0]['at']).utcoffset().total_seconds() == 0
    assert inventory(original) == before and verify_package(original, expected_hash=backup.manifest_hash)
    if case == 'file':
        assert target.read_bytes() == b'original target file'
    if case == 'parent_file':
        assert parent.read_bytes() == b'original parent file'
    if case in ('symlink', 'ancestor_symlink'):
        assert list(real.iterdir()) == []
    if case == 'internal':
        assert not target.exists()
    with pytest.raises(GateError):
        backups.confirm(job.id, person='fixture', external_medium='fixture', confirmed_at=fixtures.NOW)
    with pytest.raises(GateError):
        fixtures.start(r, freeze, fixture)
    with pytest.raises(GateError):
        backups.transfer(job.id, tmp_path / 'retry-before-recovery')
    backups.recover(job.id, decision='TECHNICAL-FIXTURE: verified intact internal package, consciously choose new target')
    final = backups.transfer(job.id, tmp_path / 'consciously-selected-safe-target')
    assert verify_package(final, expected_hash=backup.manifest_hash)
    backups.confirm(job.id, person='fixture', external_medium='TECHNICAL-FIXTURE: separated local directory', confirmed_at=fixtures.NOW, synthetic=True)
    assert r.backup_status(freeze.id) == 'current'
    assert [x for x in backups.history(job.id) if x['operation'] == 'transfer_failed'] == failure
    assert r.get(backup.id).manifest_hash == backup.manifest_hash
    fixtures.start(r, freeze, fixture)


def test_b12_internal_package_verification_failure_persisted(instance, tmp_path):
    r, settings, store, fixture, freeze, backups, worker = instance
    job = backups.enqueue(freeze.id, idempotency_key='damaged-internal-preflight')
    backup = worker.tick()
    package = Path(backups.request(job.id)['package_path'])
    original, mode = read_regular(package, 'control.sqlite3')
    atomic_file(package, 'control.sqlite3', b'deliberately damaged fixture database', mode=mode, replace=True)
    damaged = inventory(package)
    with pytest.raises(IntegrityError):
        backups.transfer(job.id, tmp_path / 'target')
    assert backups.request(job.id)['status'] == 'failed' and inventory(package) == damaged
    failure = [x for x in backups.history(job.id) if x['operation'] == 'transfer_failed']
    assert len(failure) == 1 and failure[0]['details']['target'] == str(tmp_path / 'target')
    assert not (tmp_path / 'target').exists()
    # Deliberate fixture corruption is repaired from retained original bytes.
    atomic_file(package, 'control.sqlite3', original, mode=mode, replace=True)
    backups.recover(job.id, decision='TECHNICAL-FIXTURE: restore original fixture bytes and verify before retry')
    final = backups.transfer(job.id, tmp_path / 'retry')
    assert verify_package(final, expected_hash=backup.manifest_hash)
    assert [x for x in backups.history(job.id) if x['operation'] == 'transfer_failed'] == failure


@pytest.mark.parametrize('window', ['before_copy', 'during_copy', 'during_nested_copy', 'after_nested_directory', 'after_copy'])
def test_s07_seal_restart_resumes_same_candidate_partial_original(instance, tmp_path, monkeypatch, window):
    r, settings, store, fixture, freeze, backups, worker = instance
    run, tree, snapshots, args = workspace(instance, tmp_path)
    real_restore = snapshots.restore
    real_atomic = __import__('research_env.snapshots', fromlist=['atomic_file']).atomic_file
    def interrupted_restore(*a, **kw):
        if window == 'before_copy':
            raise OSError('controlled interruption before readonly copy')
        result = real_restore(*a, **kw)
        raise OSError('controlled interruption after readonly copy before state')
    calls = []
    def interrupted_atomic(*a, **kw):
        if window == 'after_nested_directory' and a[1] == 'app/Module.php':
            from research_env.artifacts import directory
            with directory(a[0], ('app',), create=True):
                pass
            raise OSError('controlled interruption after nested directory creation before file')
        result = real_atomic(*a, **kw)
        calls.append(a[1])
        if window == 'during_copy' or a[1] == 'app/Module.php':
            raise OSError('controlled interruption after atomically committed file')
        return result
    if window in ('during_copy', 'during_nested_copy', 'after_nested_directory'):
        monkeypatch.setattr('research_env.snapshots.atomic_file', interrupted_atomic)
    else:
        monkeypatch.setattr(snapshots, 'restore', interrupted_restore)
    with pytest.raises(OSError):
        snapshots.seal(tree, writers=ProcessWriters(), **args)
    candidates = [x for x in r.all(CandidateSnapshot) if x.run_id == run.id and x.role == 'sealed']
    assert len(candidates) == 1 and r.state(run.id).seal == 'integrity_error'
    candidate = candidates[0]
    original = settings.artifacts / 'sealed' / str(candidate.id)
    if window == 'before_copy':
        assert not original.exists()
    elif window == 'during_copy':
        assert len(inventory(original)) == 1 and len(calls) == 1
    elif window == 'during_nested_copy':
        assert len(inventory(original)) == 2 and len(calls) == 2
        assert stat.S_IMODE((original / 'app').stat().st_mode) == 0o700
    elif window == 'after_nested_directory':
        assert len(inventory(original)) == 1 and not (original / 'app/Module.php').exists()
        assert stat.S_IMODE((original / 'app').stat().st_mode) == 0o700
    else:
        assert len(inventory(original)) == len(json.loads(store.read(candidate.file_manifest_id))['complete_tree'])
    monkeypatch.setattr('research_env.snapshots.atomic_file', real_atomic)
    artifact_ids = {a.id for a in r.all(Artifact)}
    # Fresh DB connection and service emulate intentional restart, no reused service state.
    fresh = Register(settings)
    try:
        service = Snapshots(ArtifactStore(settings, fresh))
        completed = service.seal(tree, writers=ProcessWriters(), **args)
        assert completed.id == candidate.id and completed.tree_hash == candidate.tree_hash
        assert [x.id for x in fresh.all(CandidateSnapshot) if x.role == 'sealed'] == [candidate.id]
        assert fresh.state(run.id).seal == 'sealed' and fresh.state(run.id).candidate_id == candidate.id
        assert {a.id for a in fresh.all(Artifact)} == artifact_ids
        count = len(fresh.all(RunStateRevision)); revision = fresh.revision(freeze.phase_id)
        assert service.seal(tree, writers=ProcessWriters(), **args).id == candidate.id
        assert len(fresh.all(RunStateRevision)) == count and fresh.revision(freeze.phase_id) == revision
        assert service.inspection_copy(candidate.id, tmp_path / 'inspection-after-recovery') == candidate.tree_hash
        assert all(not x['mode'] & 0o222 for x in inventory(original))
    finally:
        fresh.close()


@pytest.mark.parametrize('window', ['before_copy', 'during_copy', 'during_nested_copy', 'after_nested_directory', 'after_copy'])
def test_s08_actual_process_crash_seal_intentional_restart(instance, tmp_path, window):
    r, settings, store, fixture, freeze, backups, worker = instance
    run, tree, snapshots, args = workspace(instance, tmp_path)
    script = """import os,sys,json
from pathlib import Path
from uuid import UUID
from research_env.config import Settings
from research_env.register import Register
from research_env.artifacts import ArtifactStore
from research_env.snapshots import Snapshots,ProcessWriters
import research_env.snapshots as module
settings=Settings(*(Path(x) for x in sys.argv[1:5]))
r=Register(settings); service=Snapshots(ArtifactStore(settings,r)); window=sys.argv[6]
args=json.loads(sys.argv[7]); args['run_id']=UUID(args['run_id']);args['scaffold_id']=UUID(args['scaffold_id']);args['dependency_ids']=tuple(UUID(x) for x in args['dependency_ids'])
real=service.restore
if window in ('before_copy','after_copy'):
 def interrupt(*a,**kw):
  if window=='after_copy': real(*a,**kw)
  os._exit(74)
 service.restore=interrupt
else:
 real_atomic=module.atomic_file
 def interrupt(*a,**kw):
  if window=='after_nested_directory' and a[1]=='app/Module.php':
   from research_env.artifacts import directory
   with directory(a[0],('app',),create=True): pass
   os._exit(74)
  result=real_atomic(*a,**kw)
  if window=='during_copy' or a[1]=='app/Module.php': os._exit(74)
  return result
 module.atomic_file=interrupt
service.seal(Path(sys.argv[5]),writers=ProcessWriters(),**args)
"""
    encoded = json.dumps(dict(args, run_id=str(args['run_id']), scaffold_id=str(args['scaffold_id']), dependency_ids=[str(x) for x in args['dependency_ids']]))
    process = subprocess.run([sys.executable, '-c', script, *(str(x) for x in (settings.control,settings.artifacts,settings.checkpoints,settings.staging)), str(tree), window, encoded])
    assert process.returncode == 74
    candidate = [c for c in r.all(CandidateSnapshot) if c.run_id == run.id and c.role == 'sealed'][0]
    assert r.state(run.id).seal != 'sealed'
    fresh = Register(settings)
    try:
        service = Snapshots(ArtifactStore(settings,fresh))
        recovered = service.seal(tree, writers=ProcessWriters(), **args)
        assert recovered.id == candidate.id and recovered.tree_hash == candidate.tree_hash
        assert [c.id for c in fresh.all(CandidateSnapshot) if c.role=='sealed'] == [candidate.id]
        assert fresh.state(run.id).seal == 'sealed'
        assert service.inspection_copy(candidate.id,tmp_path/'inspection-after-real-crash') == candidate.tree_hash
        (tmp_path/'actual-crash-result.json').write_text(json.dumps({'window':window,'exit_code':process.returncode,'candidate_id':str(candidate.id),'tree_hash':candidate.tree_hash,'intentional_restart':'fresh Register/Snapshots','inspection':'passed'}))
    finally:
        fresh.close()


@pytest.mark.parametrize('change', ['workspace','workspace_mode','workspace_missing','internal_tests','dependency_binding','scaffold_binding','repair_binding','rejected_binding','process_binding','original','original_mode','untrusted_directory_mode','foreign_directory','original_symlink','manifest_bytes','dependency_bytes'])
def test_s09_seal_recovery_rejects_changes_preserves_registered_candidate(instance, tmp_path, monkeypatch, change):
    r, settings, store, fixture, freeze, backups, worker = instance
    run, tree, snapshots, args = workspace(instance, tmp_path)
    real_restore = snapshots.restore
    def interruption(*a,**kw):
        real_restore(*a,**kw)
        raise OSError('controlled postcopy interruption')
    monkeypatch.setattr(snapshots,'restore',interruption)
    with pytest.raises(OSError):
        snapshots.seal(tree,writers=ProcessWriters(),**args)
    candidate = [c for c in r.all(CandidateSnapshot) if c.role=='sealed'][0]
    original = settings.artifacts/'sealed'/str(candidate.id)
    first = original/'app/Module.php'
    modified = dict(args)
    if change == 'workspace':
        (tree/'app/Module.php').write_bytes(b'changed current candidate')
    elif change == 'workspace_mode':
        (tree/'artisan').chmod(0o644)
    elif change == 'workspace_missing':
        shutil.rmtree(tree)
    elif change == 'internal_tests':
        modified['internal_tests_hash'] = hashlib.sha256(b'changed tests').hexdigest()
    elif change == 'dependency_binding':
        modified['dependency_ids'] = (uuid4(),)
    elif change == 'scaffold_binding':
        modified['scaffold_id'] = uuid4()
    elif change == 'repair_binding':
        modified['repair_count'] = 1
    elif change == 'rejected_binding':
        modified['rejected_changes_artifact_id'] = uuid4()
    elif change == 'process_binding':
        modified['process_artifact_ids'] = (uuid4(),)
    elif change == 'original':
        first.chmod(0o644);first.write_bytes(b'manipulated readonly copy');first.chmod(0o444)
    elif change == 'original_mode':
        first.chmod(0o644)
    elif change == 'untrusted_directory_mode':
        first.parent.chmod(0o711)
        assert stat.S_IMODE(first.parent.stat().st_mode) == 0o711
    elif change == 'foreign_directory':
        original.chmod(0o755);(original/'foreign-empty-directory').mkdir();original.chmod(0o555)
    elif change == 'original_symlink':
        parent=first.parent;parent.chmod(0o755);first.unlink();first.symlink_to(tree/'app/Module.php');parent.chmod(0o555)
    elif change == 'manifest_bytes':
        artifact = r.get(candidate.file_manifest_id,Artifact)
        path=settings.artifacts/store.object_path(artifact.sha256)
        path.chmod(0o644);path.write_bytes(b'manipulated manifest');path.chmod(0o444)
    else:
        body=json.loads(store.read(candidate.dependency_ids[0]))
        artifact=r.get(body['artifact_ids'][0],Artifact)
        path=settings.artifacts/store.object_path(artifact.sha256)
        path.chmod(0o644);path.write_bytes(b'manipulated dependency');path.chmod(0o444)
    registered = candidate.model_dump(mode='json')
    fresh=Register(settings)
    try:
        service=Snapshots(ArtifactStore(settings,fresh))
        with pytest.raises((IntegrityError,OSError)):
            service.seal(tree,writers=ProcessWriters(),**modified)
        assert fresh.state(run.id).seal == 'integrity_error'
        assert fresh.get(candidate.id,CandidateSnapshot).model_dump(mode='json') == registered
        assert [c.id for c in fresh.all(CandidateSnapshot) if c.role=='sealed'] == [candidate.id]
        if change=='foreign_directory':assert (original/'foreign-empty-directory').is_dir()
        if change=='original':assert first.read_bytes()==b'manipulated readonly copy'
        with pytest.raises(IntegrityError):
            service.inspection_copy(candidate.id,tmp_path/'invalid-inspection')
    finally:
        fresh.close()


def copy_crash_process(settings, tree, args, window):
    script = """import os,sys,json
from pathlib import Path
from uuid import UUID
from research_env.config import Settings
from research_env.register import Register
from research_env.artifacts import ArtifactStore
from research_env.snapshots import Snapshots,ProcessWriters
settings=Settings(*(Path(x) for x in sys.argv[1:5])); r=Register(settings)
service=Snapshots(ArtifactStore(settings,r)); window=sys.argv[6]
args=json.loads(sys.argv[7]);args['run_id']=UUID(args['run_id']);args['scaffold_id']=UUID(args['scaffold_id']);args['dependency_ids']=tuple(UUID(x) for x in args['dependency_ids'])
if window=='allocation_before_inode_journal':
 real_open=os.open
 def interrupted_open(*a,**kw):
  fd=real_open(*a,**kw)
  parent=kw.get('dir_fd')
  if parent is not None and str(a[0]).startswith('.tmp-') and os.readlink('/proc/self/fd/'+str(parent)).endswith('/seal-copy-attempts'):os._exit(76)
  return fd
 os.open=interrupted_open
def crash(where):
 if where==window:os._exit(76)
service.seal(Path(sys.argv[5]),writers=ProcessWriters(),copy_crash=crash,**args)
"""
    encoded=json.dumps(dict(args,run_id=str(args['run_id']),scaffold_id=str(args['scaffold_id']),dependency_ids=[str(x) for x in args['dependency_ids']]))
    return subprocess.run([sys.executable,'-c',script,*(str(x) for x in (settings.control,settings.artifacts,settings.checkpoints,settings.staging)),str(tree),window,encoded])


@pytest.mark.parametrize('window',['after_temp_create','during_partial_write','after_file_fsync','after_fchmod','before_rename','after_rename_before_directory_fsync','allocation_before_inode_journal'])
def test_s10_actual_atomic_copy_crash_owned_temps_restart(instance,tmp_path,window):
    r,settings,store,fixture,freeze,backups,worker=instance
    run,tree,snapshots,args=workspace(instance,tmp_path)
    assert copy_crash_process(settings,tree,args,window).returncode==76
    candidate=[x for x in r.all(CandidateSnapshot) if x.role=='sealed'][0]
    original=settings.artifacts/'sealed'/str(candidate.id)
    rows=[dict(x) for x in r.connection.execute('SELECT * FROM seal_copy_attempt')]
    assert len(rows)==1 and rows[0]['candidate_id']==str(candidate.id)
    temp_files=list(original.rglob('.tmp-*')) if original.exists() else []
    stage_files=list((settings.artifacts/'seal-copy-attempts').glob('.tmp-*'))
    retained={str(p):p.read_bytes() for p in (*temp_files,*stage_files)}
    before_phase=r.revision(freeze.phase_id)
    fresh=Register(settings)
    try:
        service=Snapshots(ArtifactStore(settings,fresh))
        recovered=service.seal(tree,writers=ProcessWriters(),**args)
        assert recovered.id==candidate.id and recovered.tree_hash==candidate.tree_hash
        assert fresh.state(run.id).seal=='sealed'
        assert not list(original.rglob('.tmp-*'))
        attempts=[dict(x) for x in fresh.connection.execute('SELECT * FROM seal_copy_attempt')]
        old=next(x for x in attempts if x['id']==rows[0]['id'])
        if retained:
            assert old['evidence_id'] and service.store.read(old['evidence_id'])==next(iter(retained.values()))
            if window=='allocation_before_inode_journal':
                # No ownership is claimed and no unproven file is moved/published.
                assert old['device'] is None and all(Path(p).read_bytes()==data for p,data in retained.items())
            else:
                raw=settings.artifacts/'seal-copy-evidence'/(old['id']+'.raw')
                assert raw.read_bytes()==next(iter(retained.values()))
        journal_count=len(attempts); phase=fresh.revision(freeze.phase_id); artifact_ids={a.id for a in fresh.all(Artifact)}
        assert phase==before_phase+1  # Only the actual sealed state changes research revision.
        assert service.seal(tree,writers=ProcessWriters(),**args).id==candidate.id
        assert fresh.revision(freeze.phase_id)==phase and len(list(fresh.connection.execute('SELECT * FROM seal_copy_attempt')))==journal_count
        assert {a.id for a in fresh.all(Artifact)}==artifact_ids
        assert service.inspection_copy(candidate.id,tmp_path/'inspection-after-temp-recovery')==candidate.tree_hash
        assert service.store.integrity()['complete'] and not fresh.all(ModelCall)
        (tmp_path/'copy-crash-result.json').write_text(json.dumps({'window':window,'exit_code':76,'candidate_id':str(candidate.id),'tree_hash':candidate.tree_hash,'source_attempt':old,'final_journal_count':journal_count,'raw_temp_sha256':[hashlib.sha256(data).hexdigest() for data in retained.values()],'inspection':'passed'}))
    finally:fresh.close()


@pytest.mark.parametrize('change',['unproven','mutated','truncated_synced','symlink','hardlink','replaced_inode','unproven_owner','journal_binding','journal_origin','temp_mode'])
def test_s11_copy_temp_provenance_negatives_leave_files_untouched(instance,tmp_path,change):
    r,settings,store,fixture,freeze,backups,worker=instance
    run,tree,snapshots,args=workspace(instance,tmp_path)
    assert copy_crash_process(settings,tree,args,'after_file_fsync').returncode==76
    candidate=[x for x in r.all(CandidateSnapshot) if x.role=='sealed'][0]
    original=settings.artifacts/'sealed'/str(candidate.id)
    row=dict(r.connection.execute('SELECT * FROM seal_copy_attempt').fetchone())
    temp=original/row['temporary'];initial=temp.read_bytes()
    assert temp.stat().st_ino==row['inode'] and len(initial)==row['synced_size']
    if change=='unproven':
        (original/'.tmp-unregistered').write_bytes(b'foreign bytes')
    elif change=='mutated':temp.write_bytes(b'X'+initial[1:])
    elif change=='truncated_synced':temp.write_bytes(initial[:len(initial)//2])
    elif change=='symlink':
        temp.rename(tmp_path/'preserved-actual-own-temp');temp.symlink_to(tree/'.env.example')
    elif change=='hardlink':os.link(temp,tmp_path/'foreign-hardlink')
    elif change=='replaced_inode':
        temp.rename(tmp_path/'preserved-actual-own-temp');temp.write_bytes(initial)
        assert temp.stat().st_ino!=row['inode']
    elif change=='unproven_owner':
        with r.transaction():r.connection.execute('UPDATE seal_copy_attempt SET device=NULL,inode=NULL WHERE id=?',(row['id'],))
    elif change=='journal_binding':
        with r.transaction():r.connection.execute('UPDATE seal_copy_attempt SET tree_hash=? WHERE id=?',('f'*64,row['id']))
    elif change=='journal_origin':
        with r.transaction():r.connection.execute('UPDATE seal_copy_attempt SET origin=? WHERE id=?',('foreign-source-origin',row['id']))
    else:
        temp.chmod(0o644);assert stat.S_IMODE(temp.stat().st_mode)==0o644
    before={str(p):os.readlink(p) if p.is_symlink() else p.read_bytes() for p in original.rglob('*') if p.is_symlink() or p.is_file()}
    fresh=Register(settings)
    try:
        service=Snapshots(ArtifactStore(settings,fresh))
        with pytest.raises((IntegrityError,OSError)):service.seal(tree,writers=ProcessWriters(),**args)
        after={str(p):os.readlink(p) if p.is_symlink() else p.read_bytes() for p in original.rglob('*') if p.is_symlink() or p.is_file()}
        # Valid own temps may be preserved separately before a foreign extra is found.
        if change=='unproven':assert (original/'.tmp-unregistered').read_bytes()==before[str(original/'.tmp-unregistered')]
        else:assert before==after
        assert [x.id for x in fresh.all(CandidateSnapshot) if x.role=='sealed']==[candidate.id]
        assert fresh.state(run.id).seal=='integrity_error'
        with pytest.raises(IntegrityError):service.inspection_copy(candidate.id,tmp_path/'invalid-inspection')
    finally:fresh.close()


def test_b13_backup_restore_pending_copy_journal_preserves_source_facts(instance,tmp_path):
    r,settings,store,fixture,freeze,backups,worker=instance
    run,tree,snapshots,args=workspace(instance,tmp_path)
    assert copy_crash_process(settings,tree,args,'during_partial_write').returncode==76
    candidate=[x for x in r.all(CandidateSnapshot) if x.role=='sealed'][0]
    row=dict(r.connection.execute('SELECT * FROM seal_copy_attempt').fetchone())
    source_temp=settings.artifacts/'sealed'/str(candidate.id)/row['temporary'];raw=source_temp.read_bytes()
    job=backups.enqueue(freeze.id,idempotency_key='backup-pending-copy')
    backup=worker.tick();captured_phase=r.revision(freeze.phase_id)
    source_row=dict(r.connection.execute('SELECT * FROM seal_copy_attempt').fetchone())
    assert source_row['evidence_id'] and store.read(source_row['evidence_id'])==raw and source_temp.read_bytes()==raw
    target=backups.transfer(job.id,tmp_path/'target-journal')
    new=Settings(*(tmp_path/'restored-journal'/x for x in ('control','artifacts','checkpoints','staging')))
    result=restore(target,new,expected_hash=backup.manifest_hash)
    assert not result['generation_started']
    fresh=Register(new)
    try:
        service=Snapshots(ArtifactStore(new,fresh));restored_row=dict(fresh.connection.execute('SELECT * FROM seal_copy_attempt').fetchone())
        assert restored_row==source_row  # Inode/device facts remain historical source evidence.
        assert service.copy_origin!=source_row['origin']
        assert fresh.revision(freeze.phase_id)==captured_phase and service.store.read(source_row['evidence_id'])==raw
        assert not (new.artifacts/'sealed'/str(candidate.id)).exists()
        shutil.rmtree(tree)  # Original workspace removed: reconstruct only from captured CAS.
        new_workspace=tmp_path/'reconstructed-journal-workspace'
        service.restore(candidate.id,new_workspace)
        completed=service.seal(new_workspace,writers=ProcessWriters(),**args)
        assert completed.id==candidate.id and completed.tree_hash==candidate.tree_hash
        old=dict(fresh.connection.execute('SELECT * FROM seal_copy_attempt WHERE id=?',(source_row['id'],)).fetchone())
        assert old==source_row and source_temp.read_bytes()==raw
        assert service.inspection_copy(candidate.id,tmp_path/'journal-restore-inspection')==candidate.tree_hash
        assert fresh.connection.execute("SELECT count(*) FROM job_state WHERE status IN ('ready','running')").fetchone()[0]==0
    finally:fresh.close()


def test_b14_actual_full_restore_original_copy_crash_intentional_recovery(instance,tmp_path):
    r,settings,store,fixture,freeze,backups,worker=instance
    run,tree,snapshots,args=workspace(instance,tmp_path)
    candidate=snapshots.seal(tree,writers=ProcessWriters(),**args)
    r.set_state(run.id,r.state(run.id).model_copy(update={'execution':'terminal','terminal_cause':'finished','ended_at':fixtures.NOW}))
    job=backups.enqueue(freeze.id,idempotency_key='full-restore-copy-crash');backup=worker.tick()
    package=backups.transfer(job.id,tmp_path/'separated-restore-copy-target')
    source_journal=[dict(x) for x in r.connection.execute('SELECT * FROM seal_copy_attempt ORDER BY id')]
    captured_phase=backup.substantive_revision
    with closing(sqlite3.connect(package/'control.sqlite3')) as db:
        captured_records={row[0]:row[1] for row in db.execute('SELECT id,sha256 FROM register_record')}
    shutil.rmtree(tree)
    new=Settings(*(tmp_path/'actual-interrupted-restore'/x for x in ('control','artifacts','checkpoints','staging')))
    script="""import sys,os
from pathlib import Path
from research_env.config import Settings
from research_env.backup import restore
settings=Settings(*(Path(x) for x in sys.argv[1:5]));real=os.rename
parent_marker=str(settings.artifacts/'sealed')
def crash(source,destination,*a,**kw):
 fd=kw.get('src_dir_fd')
 if fd is not None and parent_marker in os.readlink('/proc/self/fd/'+str(fd)) and str(source).startswith('.tmp-'):os._exit(77)
 return real(source,destination,*a,**kw)
os.rename=crash
restore(Path(sys.argv[5]),settings,expected_hash=sys.argv[6])
"""
    process=subprocess.run([sys.executable,'-c',script,*(str(x) for x in (new.control,new.artifacts,new.checkpoints,new.staging)),str(package),backup.manifest_hash])
    assert process.returncode==77
    fresh=Register(new)
    try:
        service=Snapshots(ArtifactStore(new,fresh))
        assert service.copy_origin!=snapshots.copy_origin
        assert fresh.revision(freeze.phase_id)==captured_phase
        for aid,sha in captured_records.items():
            assert fresh.connection.execute('SELECT sha256 FROM register_record WHERE id=?',(aid,)).fetchone()[0]==sha
        for row in source_journal:
            assert dict(fresh.connection.execute('SELECT * FROM seal_copy_attempt WHERE id=?',(row['id'],)).fetchone())==row
        local=[dict(x) for x in fresh.connection.execute('SELECT * FROM seal_copy_attempt WHERE origin=?',(service.copy_origin,))]
        assert len(local)==1 and local[0]['status']=='allocated'
        original=new.artifacts/'sealed'/str(candidate.id)
        temp=original/local[0]['temporary'];raw=temp.read_bytes()
        assert temp.stat().st_ino==local[0]['inode'] and not source_journal[0]['origin']==local[0]['origin']
        recovered_workspace=tmp_path/'recovered-from-captured-cas'
        service.restore(candidate.id,recovered_workspace)
        restored=service.seal(recovered_workspace,writers=ProcessWriters(),**args)
        assert restored.id==candidate.id and restored.tree_hash==candidate.tree_hash
        assert fresh.revision(freeze.phase_id)==captured_phase  # Existing successful source seal is not revised.
        old=dict(fresh.connection.execute('SELECT * FROM seal_copy_attempt WHERE id=?',(local[0]['id'],)).fetchone())
        assert old['evidence_id'] and service.store.read(old['evidence_id'])==raw
        assert (new.artifacts/'seal-copy-evidence'/(local[0]['id']+'.raw')).read_bytes()==raw
        assert not list(original.rglob('.tmp-*'))
        assert service.inspection_copy(candidate.id,tmp_path/'inspection-after-full-restore-crash')==candidate.tree_hash
        assert fresh.connection.execute("SELECT count(*) FROM job_state WHERE status IN ('ready','running')").fetchone()[0]==0
        assert not fresh.all(ModelCall)
        assert fresh.connection.execute("SELECT value FROM runtime_metadata WHERE key='restore_recovery_required'").fetchone()
        before=len(list(fresh.connection.execute('SELECT * FROM seal_copy_attempt')))
        assert service.seal(recovered_workspace,writers=ProcessWriters(),**args).id==candidate.id
        assert len(list(fresh.connection.execute('SELECT * FROM seal_copy_attempt')))==before and fresh.revision(freeze.phase_id)==captured_phase
        (tmp_path/'actual-full-restore-copy-crash.json').write_text(json.dumps({'exit_code':77,'candidate_id':str(candidate.id),'tree_hash':candidate.tree_hash,'captured_records_preserved':len(captured_records),'source_journal_rows_preserved':len(source_journal),'fresh_owned_attempt':old,'revision_preserved':captured_phase,'inspection':'passed','generation_started':False}))
    finally:fresh.close()

"""Nine #9 criteria: cost-free graph/journal/CAS/SQLite fixtures, no study approval."""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import signal
import time
from uuid import UUID, uuid4

import pytest

from research_env.adapter import CallJournal
from research_env.artifacts import ArtifactStore, IntegrityError
from research_env.domain import (Artifact, AssetVersion, CandidateSnapshot, Cell, Configuration,
    ConfigurationVersion, EffectiveSettings, Event, Job, ModelCall, ModelPackage, Observation, Run,
    RunState, Study, StudyPhase, TimeInterval, TransportAttempt, canonical)
from research_env.pipeline import Pipeline, provision, roles
from research_env.providers import MockAdapter, WireResponse, KnownTransient
from research_env.register import GateError, Register
from research_env.role_formats import validate, MAX_FILES, MAX_FILE_BYTES
from research_env.scheduler import Scheduler
from research_env.snapshots import ProcessWriters, Snapshots
from test_adapter import make_settings, response
from test_artifacts import instance, secured
import test_register as fixtures

ROOT = Path(__file__).parents[1]


def envelope(role, *, repair=False, review=False, broken=False):
    base = {'schema': 'roles-v1', 'role': role}
    if role in ('analyzer', 'planner'):
        base['analysis' if role == 'analyzer' else 'plan'] = role + ' synthetic producer artifact'
    elif role == 'review':
        base.update(changes_required=review, reason='Synthetic documented judgment', findings=[])
    else:
        base['files'] = [{'path': 'internal/check.php' if role == 'test' else 'routes/study.php',
                          'content': '<?php throw new RuntimeException("broken");' if broken else '<?php // ' + role}]
    return base


def wire(value, *, usage=True):
    return response(choices=[{'message': {'content': canonical(value)}, 'finish_reason': 'stop'}],
                    usage={'prompt_tokens': 11, 'completion_tokens': 13, 'cost': 0.1} if usage else None)


class MockRunner:
    """Only response execution is mocked; graph, calls, claims, CAS/Seal are real."""
    def __init__(self, results=('passed',), *, fail_preflight=False, on_execute=None):
        self.results = list(results)
        self.invocations = []
        self.fail_preflight = fail_preflight
        self.on_execute = on_execute

    def preflight(self, run_id):
        if self.fail_preflight:
            raise IntegrityError('Synthetic concrete image/isolation mismatch')

    def writers(self, run_id):
        return ProcessWriters()

    def close(self):
        pass

    def execute(self, job_id, candidate_id, tests_id, *, node, continuation=None):
        self.invocations.append((job_id, candidate_id, tests_id, node))
        if self.on_execute:
            self.on_execute()
        return {'classification': self.results.pop(0), 'internal_tests_id': str(tests_id),
                'observations': [{'source': 'synthetic internal only'}], 'suite_kind': 'development'}


def setup(root, *, planner=True, review=True, repair=False, result=('passed',), usage=True,
          outputs=None, runner=None, clock=None, crash=None, scaffold_path=None, vendor_path=None,
          package_bytes=None, cross_model=False, csrf_revision=False, source_commit=None, module="SQL", context="K0"):
    settings = make_settings(root)
    r = Register(settings)
    store = ArtifactStore(settings, r)
    shared = store.store(b'Synthetic explicit package/isolation/version fixture')
    study = r.add(Study(code=f'S-{uuid4()}', title='Synthetic pipeline', design_version='fixture-v1', data_origin='synthetic', provenance='No study/human permission'))
    phase = r.add(StudyPhase(code=f'P-{uuid4()}', study_id=study.id, purpose='free_test', provenance='Synthetic only'))
    obs = Observation(status='unresolved', unit='tokens', reason='Synthetic provider; not a real capacity')
    model = r.add(ModelPackage(code=f'M-{uuid4()}', exact_model_id='TECHNICAL-FIXTURE:no-network', endpoint='mock://local', upstream='mock', routing={}, fallback={}, supported_parameters=('seed',), effective_parameters={'seed': 1}, context_limit=obs, output_limit=obs, price=Observation(status='unresolved', unit='USD', reason='Synthetic only'), currency='USD', price_as_of=datetime.now(timezone.utc), metadata_evidence_ids=(shared.id,), version_uncertainty='Synthetic; no unchanged weights claim'))
    model_b = r.add(model.model_copy(update={'id': uuid4(), 'code': f'M-B-{uuid4()}', 'exact_model_id': 'TECHNICAL-FIXTURE:Verifier'})) if cross_model else model
    scaffold = r.add(AssetVersion(code=f'A-{uuid4()}', asset_type='scaffold', manifest_hash=shared.sha256, origin='synthetic', access_scope='shared_scaffold'))
    dev = r.add(AssetVersion(code=f'A-{uuid4()}', asset_type='fixture_suite', manifest_hash=shared.sha256, origin='synthetic', access_scope='public_development', suite_kind='development'))
    conf = r.add(Configuration(code=f'C-{uuid4()}', phase_id=phase.id))
    cell = Cell(module=module, context=context, producer='A', verifier='B' if cross_model else 'A', planner=planner, review=review)
    effective = EffectiveSettings(model_a=model.id, model_b=model_b.id, scaffold_id=scaffold.id, development_suite_id=dev.id, role_parameters={'all': {'seed': 1}}, retry_interval_seconds=Decimal('0.001'))
    context_level=context
    if csrf_revision:
        from research_env.context_assets import contract_binding
        from research_env.pipeline import role_assets
        locked=json.loads((ROOT/'src/research_env/pipeline_assets.lock.json').read_text())
        contract=r.add(AssetVersion(code=f'CSRF1-CONTRACT-{uuid4()}',asset_type='contract',manifest_hash=contract_binding(ROOT)['manifest_sha256'],origin='Explicitly approved CSRF1 interpretation',access_scope='public_development'))
        context=r.add(AssetVersion(code=f'CSRF1-CONTEXT-{uuid4()}',asset_type='source_context',manifest_hash=locked[f'assets/context/m2-v0.1-csrf1/{module}/{context}/manifest.json'],origin=f'Actual full revised {module}/{context_level} package',access_scope='role_package'))
        scaffold=r.add(AssetVersion(code=f'CSRF1-SCAFFOLD-{uuid4()}',asset_type='scaffold',manifest_hash=locked['assets/study/m2-v0.1/manifest.json'],origin='Unchanged actual scaffold',access_scope='shared_scaffold'))
        public=role_assets(store)
        effective=effective.model_copy(update={'contract_id':contract.id,'context_ids':{f'{module}-{context_level}':context.id},'scaffold_id':scaffold.id,
            'handoff_id':public['handoff'].id,'prompt_ids':(public['prompts'].id,),'software_commit':source_commit})
    version = r.version_configuration(conf.id, 'synthetic-custom', cell, effective)
    run, job = r.start_other(phase.id, version.id, decision='TECHNICAL-FIXTURE: deliberate mock start', technical_evidence_ids=(shared.id,), idempotency_key=str(uuid4()))
    adapter = MockAdapter(outputs if outputs is not None else [wire(envelope(x, review=repair), usage=usage) for x in roles(cell) if x != 'repair' or repair])
    if cross_model and outputs is None:
        adapter.responses = [(lambda request, output=envelope(x, review=repair): response(model=json.loads(request)['model'], choices=[{'message': {'content': canonical(output)}}], usage={'cost': 0.1})) for x in roles(cell) if x != 'repair' or repair]
    journal = CallJournal(r, **({'monotonic': clock} if clock else {}))
    preview = journal.preview(adapter, run.id, {'uncertainty': 'Synthetic fixture only', 'categories': {}, 'pilot_consumption': {'status': 'not_collected'}})
    order = journal.record_start_order(run.id, preview.id, person='TECHNICAL-FIXTURE: synthetic user', decision='Conscious mock start', roles=roles(cell), synthetic_fixture=True)
    tree = scaffold_path or root / 'scaffold'
    if scaffold_path is None:
        (tree / 'routes').mkdir(parents=True)
        (tree / 'routes/study.php').write_text('<?php // base scaffold')
        (tree / 'artisan').write_text('<?php // protected')
    deps = vendor_path or root / 'vendor-fixture'
    if vendor_path is None:
        deps.mkdir(); (deps / 'autoload.php').write_text('<?php // exact installed synthetic dependency')
    dependency = Snapshots(store).dependencies(deps, source={'synthetic': True}, runtime={'fixture': True})
    versions={'software_commit': 'synthetic-v1', 'isolation': str(shared.id)}
    if csrf_revision:
        versions={'software_commit':source_commit,'runtime_images':json.loads((ROOT/'src/research_env/pipeline_runtime.lock.json').read_text()),'platform':'cost-free public-revision harness','isolation':str(shared.id)}
    manifest = provision(store, run.id, package=package_bytes or b'FULL_SYNTHETIC_K0_WITH_PRIVATE_K1_EXCLUDED', scaffold=tree, dependency_id=dependency.id, proof_ids=(shared.id,), versions=versions, synthetic=not csrf_revision)
    runner = runner or MockRunner(result)
    if csrf_revision and isinstance(runner,MockRunner):runner.versions=versions['runtime_images']
    pipeline = Pipeline(r, journal, adapter, runner, crash=crash)
    pipeline.install(job.id, manifest.id, order)
    scheduler = Scheduler(pipeline)
    return r, settings, store, run, job, pipeline, scheduler, adapter, runner


def finish(scheduler, run, limit=30):
    # Test harness safety bound; never a production job time/cost cutoff.
    for _ in range(limit):
        scheduler.tick()
        if scheduler.pipeline.binding(run.id)['status'] == 'completed':
            return
        if scheduler.pipeline.binding(run.id)['status'] == 'recovery_required':
            pytest.fail(scheduler.pipeline.binding(run.id)['reason'])
    pytest.fail('Finite graph did not complete')


@pytest.mark.parametrize('planner,review', [(False, False), (True, False), (False, True), (True, True)])
@pytest.mark.parametrize('repair', [False, True])
def test_four_paths_exact_artifact_matrix_and_call_counts(tmp_path, planner, review, repair):
    result = ('candidate_failure', 'passed') if repair else ('passed',)
    r, settings, store, run, job, p, s, a, runner = setup(tmp_path, planner=planner, review=review, repair=repair, result=result)
    finish(s, run)
    calls = [x for x in r.all(ModelCall) if x.run_id == run.id]
    expected_roles = ['analyzer'] + (['planner'] if planner else []) + ['migrate', 'test'] + (['review'] if review else []) + (['repair'] if repair else [])
    assert [x.node for x in calls] == expected_roles
    assert a.send_count == 3 + int(planner) + int(review) + int(repair)
    expected_inputs = {'analyzer': [], 'planner': ['role_analyzer'], 'migrate': ['role_analyzer'] + (['role_planner'] if planner else []),
        'test': ['role_migrate', 'role_analyzer'] + (['role_planner'] if planner else []),
        'review': ['role_migrate', 'role_analyzer'] + (['role_planner'] if planner else []) + ['role_test', 'pipeline_internal_result'],
        'repair': ['role_migrate', 'role_analyzer'] + (['role_planner'] if planner else []) + ['role_test', 'pipeline_internal_result'] + (['role_review'] if review else [])}
    for call in calls:
        assert call.input_artifact_ids[:3] == calls[0].input_artifact_ids[:3]
        assert [r.get(x, Artifact).artifact_type for x in call.input_artifact_ids[3:]] == expected_inputs[call.node]
        request = json.loads(store.read(call.messages_artifact_id))
        assert request['messages'] and 'max_tokens' not in request and 'max_completion_tokens' not in request
        assert b'FULL_SYNTHETIC_K0_WITH_PRIVATE_K1_EXCLUDED' in store.read(call.messages_artifact_id)
    state = r.state(run.id)
    assert state.execution == 'terminal' and state.seal == 'sealed'
    sealed = r.get(state.candidate_id, CandidateSnapshot)
    assert sealed.repair_count == int(repair)
    if repair:
        assert runner.invocations[0][2] == runner.invocations[1][2]
    assert store.integrity()['problems'] == []
    assert s.tick() is None
    report = p.summary(run.id)
    assert report['timing']['active']['status'] == 'observed'
    assert report['costs']['evidence_revision'] and report['costs']['phase_revision'] == r.revision(run.phase_id)
    p.close(); r.close()


def test_regression_keeps_last_repaired_candidate(tmp_path):
    r, _, store, run, _, p, s, _, _ = setup(tmp_path, repair=True, result=('passed', 'candidate_failure'))
    finish(s, run)
    assert r.state(run.id).terminal_cause == 'content_failure'
    sealed = r.get(r.state(run.id).candidate_id, CandidateSnapshot)
    assert sealed.repair_count == 1
    manifest = json.loads(store.read(sealed.file_manifest_id))
    entry = next(x for x in manifest['files'] if x['path'] == 'routes/study.php')
    assert entry['sha256'] == hashlib.sha256(b'<?php // repair').hexdigest()
    assert len([x for x in r.all(ModelCall) if x.node == 'review']) == 1
    p.close(); r.close()


@pytest.mark.parametrize('bad', ['no json', {'schema': 'roles-v1', 'role': 'analyzer', 'analysis': ''}, {'schema': 'roles-v1', 'role': 'analyzer', 'analysis': 'a', 'extra': 'forbidden'}])
def test_unusable_format_terminal_without_rescue(tmp_path, bad):
    out = wire(bad) if not isinstance(bad, str) else response(choices=[{'message': {'content': bad}}])
    r, _, _, run, _, p, s, a, _ = setup(tmp_path, outputs=[out])
    finish(s, run)
    assert a.send_count == 1 and r.state(run.id).terminal_cause == 'content_failure'
    assert r.state(run.id).seal == 'no_candidate'
    assert any(x.artifact_type == 'pipeline_rejected_output' for x in r.all(Artifact))
    assert any(x.artifact_type == 'response_bytes' for x in r.all(Artifact))
    p.close(); r.close()


@pytest.mark.parametrize('files', [[], [{'path': 'app/Study/A.php', 'content': '<?php'}],
    [{'path': 'routes/study.php', 'content': 'a'}, {'path': 'routes/study.php', 'content': 'b'}],
    [{'path': 'routes/study.php', 'content': 'x' * (MAX_FILE_BYTES + 1)}]])
def test_missing_duplicate_oversized_file_set_rejected(tmp_path, files):
    r, _, _, run, _, p, s, a, _ = setup(tmp_path, planner=False, review=False,
        outputs=[wire(envelope('analyzer')), wire({'schema': 'roles-v1', 'role': 'migrate', 'files': files})])
    finish(s, run)
    assert a.send_count == 2 and r.state(run.id).terminal_cause == 'content_failure'
    p.close(); r.close()


@pytest.mark.parametrize('path', ['../secret', '/etc/passwd', 'artisan', '.git/config', 'routes\\study.php'])
def test_protected_paths_pause_phase_and_preserve_raw(tmp_path, path):
    r, _, _, run, _, p, s, a, _ = setup(tmp_path, planner=False, review=False,
        outputs=[wire(envelope('analyzer')), wire({'schema': 'roles-v1', 'role': 'migrate', 'files': [{'path': path, 'content': 'not applied'}]})])
    finish(s, run)
    assert a.send_count == 2 and r.state(run.id).terminal_cause == 'technical_failure'
    assert r.phase_state(run.phase_id).status == 'paused'
    p.close(); r.close()


def test_symlink_and_repair_file_set_blocked(tmp_path):
    (tmp_path / 'routes').mkdir(); (tmp_path / 'secret').write_text('protected')
    (tmp_path / 'routes/study.php').symlink_to(tmp_path / 'secret')
    with pytest.raises(IntegrityError):
        validate(canonical(envelope('migrate')), 'migrate', workspace=tmp_path)
    with pytest.raises(ValueError):
        validate(canonical(envelope('repair')), 'repair', expected_paths=('routes/study.php', 'app/Study/Missing.php'))


def test_preflight_failure_claim_is_durable_and_not_retried(tmp_path):
    r, _, _, run, job, p, s, a, _ = setup(tmp_path, runner=MockRunner(fail_preflight=True))
    s.tick()
    assert a.send_count == 0 and r.state(run.id).terminal_cause == 'technical_failure'
    assert p.binding(run.id)['status'] == 'completed'
    assert r.connection.execute('SELECT status FROM job_state WHERE job_id=?', (str(job.id),)).fetchone()[0] == 'completed'
    assert s.tick() is None
    p.close(); r.close()


@pytest.mark.parametrize('failure', [WireResponse(400, b'{"error":{"code":400}}', {}), WireResponse(503, b'{"error":{"code":503}}', {}), TimeoutError('synthetic unknown')])
def test_provider_failures_pause_without_generation(tmp_path, failure):
    r, _, _, run, _, p, s, a, _ = setup(tmp_path, outputs=[failure])
    finish(s, run)
    assert a.send_count == 1
    assert r.state(run.id).terminal_cause in ('technical_failure', 'outcome_unknown')
    assert r.phase_state(run.phase_id).status == 'paused'
    p.close(); r.close()


def test_one_retry_identical_and_missing_usage_bound_cost(tmp_path):
    outputs = [KnownTransient('synthetic')] + [wire(envelope(x), usage=False) for x in ('analyzer', 'migrate', 'test')]
    r, _, _, run, _, p, s, a, _ = setup(tmp_path, planner=False, review=False, outputs=outputs)
    sent = []; a.on_send = sent.append
    finish(s, run)
    assert a.send_count == 4 and sent[0] == sent[1]
    assert len(r.all(TransportAttempt)) == 4
    costs = p.summary(run.id)['costs']
    assert costs['status'] == 'unresolved' and costs['amount'] is None and costs['known_subtotal'] is None
    p.close(); r.close()


def test_pause_requested_during_call_finishes_call_and_counts_wait(tmp_path):
    ticks = iter(range(1000)); clock = lambda: next(ticks)
    r, _, _, run, _, p, s, a, _ = setup(tmp_path, planner=False, review=False, clock=clock)
    a.on_send = lambda _: s.request_pause(run.id, reason='Synthetic pause while provider active') if a.send_count == 1 else None
    s.tick()
    assert a.send_count == 1 and p.binding(run.id)['status'] == 'pause_requested'
    assert p.journal.diagnostic(r.all(ModelCall)[0].id)['attempts'][-1]['status'] == 'incorporated'
    s.tick()
    assert p.binding(run.id)['status'] == 'paused' and a.send_count == 1
    s.resume(run.id, reason='Conscious same-process continuation')
    finish(s, run)
    intervals = r.all(TimeInterval)
    assert [x.kind for x in intervals] == ['active', 'pause', 'active']
    assert p.summary(run.id)['timing']['active']['status'] == 'observed'
    p.close(); r.close()


@pytest.mark.parametrize('paused', [False, True])
def test_restart_requires_explicit_action_and_correct_time_gap(tmp_path, paused):
    r, settings, _, run, _, p, s, a, runner = setup(tmp_path, planner=False, review=False)
    s.tick()
    if paused:
        s.request_pause(run.id, reason='Synthetic actual pause before crash'); s.tick()
    p.close(); r.close()
    r = Register(settings)
    a2 = MockAdapter([wire(envelope('migrate')), wire(envelope('test'))])
    p2 = Pipeline(r, CallJournal(r), a2, runner); s2 = Scheduler(p2); s2.boot()
    assert p2.binding(run.id)['status'] == 'recovery_required' and s2.tick() is None and a2.send_count == 0
    s2.resume(run.id, reason='Explicit restart recovery')
    finish(s2, run)
    report = p2.summary(run.id)['timing']
    assert report['active']['status'] == ('observed' if paused else 'technical_missing')
    assert report['total']['status'] == 'technical_missing'
    assert report['known_partial_seconds']['active'] is not None
    p2.close(); r.close()


@pytest.mark.parametrize('boundary,expected_sends', [('analyzer:prepared', 1), ('analyzer:after_dispatch', 0), ('analyzer:after_raw', 0), ('effect:analyzer', 0), ('checkpoint', 0)])
def test_graph_crash_journal_recovery_never_resends_unknown(tmp_path, boundary, expected_sends):
    def crash(point):
        if point == boundary:
            raise RuntimeError('Synthetic abrupt boundary')
    r, settings, _, run, _, p, s, a, runner = setup(tmp_path, planner=False, review=False, crash=crash)
    with pytest.raises(RuntimeError):
        s.tick()
    p.close(); r.close()
    r = Register(settings)
    a2 = MockAdapter([wire(envelope(x)) for x in (('analyzer', 'migrate', 'test') if expected_sends else ('migrate', 'test'))])
    p2 = Pipeline(r, CallJournal(r), a2, runner); s2 = Scheduler(p2); s2.boot()
    assert s2.tick() is None
    s2.resume(run.id, reason='Explicit crash recovery')
    finish(s2, run)
    calls = [x for x in r.all(ModelCall) if x.run_id == run.id]
    analyzer = next(x for x in calls if x.node == 'analyzer')
    transports = [x for x in r.all(TransportAttempt) if x.call_id == analyzer.id]
    assert len(transports) == 1
    if boundary == 'analyzer:after_dispatch':
        assert a2.send_count == 0 and r.state(run.id).terminal_cause == 'outcome_unknown'
    else:
        assert a2.send_count == 2 + expected_sends and r.state(run.id).seal == 'sealed'
    p2.close(); r.close()


def test_double_claim_free_serial_and_no_export_or_relabel(tmp_path):
    r, _, _, run, job, p, s, a, _ = setup(tmp_path)
    assert r.claim(job.id, 'first') and not r.claim(job.id, 'second')
    with pytest.raises(sqlite3.IntegrityError):
        r.start_other(run.phase_id, run.configuration_version_id, decision='Synthetic other', technical_evidence_ids=run.technical_evidence_ids, idempotency_key=str(uuid4()))
    with pytest.raises(GateError):
        r.add(Job(code=f'J-{uuid4()}', phase_id=run.phase_id, run_id=run.id, job_type='export', idempotency_key=str(uuid4()), suite_id=run.suite_id))
    assert run.planned_run_id is None
    p.close(); r.close()


def test_main_single_series_share_next_id_backup_gate(instance, tmp_path):
    r, _, _, f, freeze, _, _ = instance
    # An already existing synthetic pilot is terminal. Actual backup transfer
    # and receipt are fixtures on a separate local dir, never claimed as SSD.
    secured(instance, tmp_path / 'freeze-copy')
    class Holder:
        register = r
        journal = type('J', (), {'process': None})()
        store = None
    s = Scheduler(Holder())
    series = s.start_series(freeze.id, decision='Synthetic deliberate series')
    next_id = r.next_id(freeze.id)
    key = str(uuid4())
    run, job = s.start_main(freeze.id, decision='Synthetic single', idempotency_key=key, technical_evidence_ids=(f['basic'].id,))
    same, samejob = s.start_main(freeze.id, decision='Duplicate', idempotency_key=key, technical_evidence_ids=(f['basic'].id,))
    assert same.id == run.id and samejob.id == job.id and run.planned_run_id == next_id
    r.set_state(run.id, RunState(execution='terminal', terminal_cause='content_failure', seal='no_candidate', ended_at=datetime.now(timezone.utc)), reason='Synthetic failed result without replacement')
    with pytest.raises(GateError, match='Sicherung'):
        s.start_main(freeze.id, decision='Synthetic series continue', idempotency_key=str(uuid4()), technical_evidence_ids=(f['basic'].id,), series_id=series.id)
    assert r.next_id(freeze.id) != run.planned_run_id
    secured(instance, tmp_path / 'after-run-copy')
    run2, _ = s.start_main(freeze.id, decision='Synthetic conscious continuation', idempotency_key=str(uuid4()), technical_evidence_ids=(f['basic'].id,), series_id=series.id)
    assert run2.actual_position == run.actual_position + 1
    assert tuple(series.order) == tuple(r.fq_ids(freeze.id))


def test_non_synthetic_wrong_package_and_scaffold_rejected(tmp_path):
    r, _, store, run, _, p, _, _, _ = setup(tmp_path)
    body = p.manifest(run.id)
    kwargs = dict(dependency_id=UUID(body['dependency_id']), proof_ids=tuple(UUID(x) for x in body['proof_ids']), versions={'software_commit': 'x'}, synthetic=False)
    with pytest.raises(IntegrityError, match='Paket'):
        provision(store, run.id, package=b'partial K0', scaffold=tmp_path / 'scaffold', **kwargs)
    package = (ROOT / 'assets/context/m2-v0.1/SQL/K0/package.txt').read_bytes()
    with pytest.raises(IntegrityError, match='Gerüst'):
        provision(store, run.id, package=package, scaffold=tmp_path / 'scaffold', **kwargs)
    p.close(); r.close()


@pytest.mark.parametrize('boundary', [None, 'claim:after_commit', 'analyzer:after_dispatch', 'analyzer:after_raw', 'effect:analyzer', 'files:migrate', 'completion:before_cas', 'completion:after_cas', 'completion:before_commit'])
def test_native_worker_process_crash_and_explicit_restart(tmp_path, boundary):
    r, settings, _, run, _, p, _, _, _ = setup(tmp_path, planner=False, review=False)
    p.close(); r.close()
    command = [sys.executable, str(Path(__file__).with_name('pipeline_worker_probe.py')), '--root', str(tmp_path)]
    initial = subprocess.run(command + (['--crash', boundary] if boundary else []), capture_output=True)
    (tmp_path / 'worker-initial.stdout').write_bytes(initial.stdout)
    (tmp_path / 'worker-initial.stderr').write_bytes(initial.stderr)
    assert initial.returncode == (79 if boundary else 0)
    if boundary:
        crashed = Register(settings)
        assert crashed.connection.execute('SELECT status FROM job_state').fetchone()[0] == 'running'
        assert crashed.connection.execute('SELECT status FROM pipeline_binding').fetchone()[0] != 'completed'
        assert not any(x.artifact_type == 'pipeline_summary' for x in crashed.all(Artifact))
        if boundary == 'claim:after_commit':
            assert crashed.state(run.id).execution == 'queued'
            assert crashed.connection.execute('SELECT process_id FROM pipeline_binding').fetchone()[0] is not None
        if boundary.startswith('completion:'):
            assert crashed.state(run.id).execution == 'terminal'
            assert crashed.connection.execute('SELECT clock_json FROM pipeline_binding').fetchone()[0] is None
        crashed.close()
        # Actual process boot marks an interrupted job and does no inference.
        recover = subprocess.run(command + ['--mode', 'recover'], capture_output=True)
        (tmp_path / 'worker-recover.stdout').write_bytes(recover.stdout)
        (tmp_path / 'worker-recover.stderr').write_bytes(recover.stderr)
        assert recover.returncode == 0
        # The action queued before a new boot is invalidated, even though it
        # followed the old crash. Fresh action must target the now-live worker.
        out = (tmp_path / 'worker-final.stdout').open('wb'); err = (tmp_path / 'worker-final.stderr').open('wb')
        final = subprocess.Popen(command, stdout=out, stderr=err)
        control = Register(settings)
        try:
            for _ in range(600):
                status = control.connection.execute('SELECT state FROM worker_status').fetchone()
                if status and status['state'] == 'running' and control.connection.execute("SELECT status FROM pipeline_binding WHERE run_id=?", (str(run.id),)).fetchone()[0] == 'recovery_required':
                    break
                assert final.poll() is None
                time.sleep(.1)
            else: pytest.fail('New worker did not await fresh action')
            cp = Pipeline(control, CallJournal(control), MockAdapter(), MockRunner()); cs = Scheduler(cp)
            before_sends = (tmp_path / 'worker-send.raw').read_bytes().splitlines() if (tmp_path / 'worker-send.raw').exists() else []
            assert len(before_sends) == (0 if boundary in ('claim:after_commit', 'analyzer:after_dispatch') else 1 if boundary in ('analyzer:after_raw', 'effect:analyzer') else 2 if boundary == 'files:migrate' else 3)
            cs.resume(run.id, reason='TECHNICAL-FIXTURE: fresh action after actual new worker boot')
            final.wait()
            cp.close()
            assert final.returncode == 0
        finally:
            if final.poll() is None: final.kill(); final.wait()
            control.close(); out.close(); err.close()
    r = Register(settings)
    state = r.state(run.id)
    assert state.execution == 'terminal'
    sends = (tmp_path / 'worker-send.raw').read_bytes().splitlines() if (tmp_path / 'worker-send.raw').exists() else []
    assert len(sends) == (0 if boundary == 'analyzer:after_dispatch' else 3)
    if boundary == 'analyzer:after_dispatch':
        assert state.terminal_cause == 'outcome_unknown'
    else:
        assert state.seal == 'sealed'
    assert r.connection.execute('SELECT state FROM worker_status').fetchone()[0] == 'stopped'
    assert r.connection.execute('SELECT status FROM job_state').fetchone()[0] == 'completed'
    summaries = [x for x in r.all(Artifact) if x.run_id == run.id and x.artifact_type == 'pipeline_summary']
    assert len(summaries) == 1
    check = Pipeline(r, CallJournal(r), MockAdapter(), MockRunner())
    assert check.read_summary(summaries[0].id)['cost_derivation_status'] == 'current'
    if boundary and boundary.startswith('completion:'):
        assert check.summary(run.id)['timing']['active']['status'] == 'observed'
        assert any(x.run_id == run.id and x.event_type == 'explicit_completion_recovery' for x in r.all(Event))
    check.close()
    assert not r.connection.in_transaction
    r.close()


def test_initial_empty_snapshot_then_first_test_binding_and_changed_test_rejected(tmp_path):
    r, _, _, run, _, p, s, _, _ = setup(tmp_path, planner=False, review=False)
    for _ in range(4): s.tick()
    snapshots = [x for x in r.all(CandidateSnapshot) if x.run_id == run.id]
    assert snapshots[0].role == 'post_migrate' and snapshots[0].internal_tests_hash == hashlib.sha256(b'').hexdigest()
    bound = [x for x in snapshots if x.internal_tests_hash != hashlib.sha256(b'').hexdigest()]
    assert bound and len({x.internal_tests_hash for x in bound}) == 1
    state = p.graph.get_state({'configurable': {'thread_id': str(run.id)}}).values
    kwargs = p._snapshot_args(state); kwargs['internal_tests_hash'] = hashlib.sha256(b'changed test bytes').hexdigest()
    with pytest.raises(GateError, match='Interne Tests'):
        p.snapshots.capture(p.workspace(run.id), role='post_repair', **kwargs)
    finish(s, run)
    p.close(); r.close()


def test_foreign_holdout_and_checkpoint_refs_never_enter_prompts(tmp_path):
    r, _, store, run, _, p, s, a, _ = setup(tmp_path, planner=False, review=False)
    with pytest.raises(GateError, match='geschützter'):
        store.store(b'REJECTED_FREE_HOLDOUT_MARKER', run_id=run.id, access_scope='trusted_evaluator', artifact_type='external_result')
    # A shared trusted evaluator artifact is valid provenance, but is never a
    # role/own checkpoint input. The free-run gate above stays unchanged.
    marker = store.store(b'PROTECTED_HOLDOUT_MARKER', access_scope='trusted_evaluator', artifact_type='external_result')
    with pytest.raises(IntegrityError):
        p._role_ref(run.id, marker.id)
    with pytest.raises(IntegrityError):
        p._checked_state({'run_id': str(run.id), 'config_hash': run.effective_hash, 'statuses': {}, 'refs': {'runner': str(marker.id)}})
    finish(s, run)
    calls = [x for x in r.all(ModelCall) if x.run_id == run.id]
    assert all(b'PROTECTED_HOLDOUT_MARKER' not in store.read(x.messages_artifact_id) for x in calls)
    assert r.get(run.suite_id, AssetVersion).suite_kind == 'development'
    assert not r.connection.execute('SELECT 1 FROM freeze_binding').fetchone()
    p.close(); r.close()


def test_producer_and_verifier_have_distinct_actual_mock_model_ids(tmp_path):
    r, _, _, run, _, p, s, _, _ = setup(tmp_path, cross_model=True, repair=True, result=('candidate_failure', 'passed'))
    finish(s, run)
    calls = [x for x in r.all(ModelCall) if x.run_id == run.id]
    package_ids = {x.node: x.model_package_id for x in calls}
    assert package_ids['analyzer'] == package_ids['planner'] == package_ids['migrate'] == package_ids['repair']
    assert package_ids['test'] == package_ids['review'] != package_ids['migrate']
    for call in calls:
        diagnosis = p.journal.diagnostic(call.id)
        assert diagnosis['attempts'][-1]['journal']['parsed_id']
        assert r.get(call.model_package_id, ModelPackage).exact_model_id in ('TECHNICAL-FIXTURE:no-network', 'TECHNICAL-FIXTURE:Verifier')
    p.close(); r.close()


def test_abort_preserves_candidate_and_diagnosis_without_repair(tmp_path):
    r, _, _, run, _, p, s, a, _ = setup(tmp_path, planner=False, review=False)
    s.tick(); s.tick()
    assert a.send_count == 2
    diagnosis = s.request_abort(run.id, reason='Conscious manual immediate stop', immediate=True)
    s.tick()
    assert r.state(run.id).terminal_cause == 'interrupted' and r.state(run.id).seal == 'sealed'
    assert a.send_count == 2 and diagnosis.artifact_type == 'pipeline_stop_diagnosis'
    assert s.tick() is None
    p.close(); r.close()


def test_identity_drift_ends_phase_and_keeps_not_started_ids(tmp_path):
    r, _, _, run, _, p, s, a, _ = setup(tmp_path, outputs=[response(model='wrong/identity')])
    finish(s, run)
    assert a.send_count == 1 and r.phase_state(run.phase_id).status == 'stopped'
    assert r.state(run.id).terminal_cause == 'technical_failure'
    p.close(); r.close()


def test_cost_summary_is_bound_and_later_receipt_marks_saved_derivation_stale(tmp_path):
    r, _, _, run, _, p, s, a, _ = setup(tmp_path, planner=False, review=False)
    finish(s, run)
    saved = next(x for x in r.all(Artifact) if x.run_id == run.id and x.artifact_type == 'pipeline_summary')
    before = p.journal.effective_costs(run.id)
    initial = p.read_summary(saved.id)
    assert initial['cost_derivation_status'] == 'current'
    assert initial['saved']['costs'] == before == initial['current_costs']
    first_call = next(x for x in r.all(ModelCall) if x.run_id == run.id)
    tid = p.journal._latest(first_call.id).id
    a.metadata = WireResponse(200, b'{"data":{"id":"synthetic-generation-1","total_cost":7}}', {})
    p.journal.fetch_metadata(a, tid)
    after = p.journal.effective_costs(run.id)
    assert before['evidence_revision'] != after['evidence_revision']
    assert before['phase_revision'] != after['phase_revision']
    assert after['status'] == 'partial' and p.read_summary(saved.id)['cost_derivation_status'] == 'stale'
    p.close(); r.close()


def test_cost_summary_phase_only_change_marks_saved_derivation_stale(tmp_path):
    r, _, _, run, _, p, s, _, _ = setup(tmp_path, planner=False, review=False)
    finish(s, run)
    saved = next(x for x in r.all(Artifact) if x.run_id == run.id and x.artifact_type == 'pipeline_summary')
    before = p.read_summary(saved.id)
    assert before['cost_derivation_status'] == 'current'
    p.event(run.id, 'synthetic_later_observation', {'reason': 'Later substantive data'})
    after = p.read_summary(saved.id)
    assert before['current_costs']['evidence'] == after['current_costs']['evidence']
    assert before['current_costs']['phase_revision'] != after['current_costs']['phase_revision']
    assert after['cost_derivation_status'] == 'stale'
    p.close(); r.close()


def test_cost_summary_final_revision_assertion_rolls_back_both_records(tmp_path, monkeypatch):
    r, _, _, run, _, p, s, _, _ = setup(tmp_path, planner=False, review=False)
    original = r._put
    def changed_revision(value):
        result = original(value)
        if isinstance(value, Event) and value.event_type == 'pipeline_completed':
            r.connection.execute('UPDATE phase_state SET substantive_revision=substantive_revision+1 WHERE phase_id=?', (str(run.phase_id),))
        return result
    monkeypatch.setattr(r, '_put', changed_revision)
    with pytest.raises(RuntimeError, match='finalen Registerrevisionen'):
        finish(s, run)
    assert not any(x.run_id == run.id and x.artifact_type == 'pipeline_summary' for x in r.all(Artifact))
    assert not any(x.run_id == run.id and x.event_type == 'pipeline_completed' for x in r.all(Event))
    assert p.binding(run.id)['status'] != 'completed'
    assert r.connection.execute('SELECT status FROM job_state').fetchone()[0] == 'running'
    assert any(x['kind'] == 'orphan_file' for x in p.store.integrity()['problems'])
    p.close(); r.close()


@pytest.mark.parametrize('state', ['queued', 'preflight'])
def test_legacy_claim_without_pipeline_process_stamp_requires_fresh_recovery(tmp_path, state):
    r, settings, _, run, job, p, s, a, runner = setup(tmp_path, planner=False, review=False)
    assert r.claim(job.id, 'synthetic-crashed-old-worker')
    if state == 'preflight':
        r.set_state(run.id, r.state(run.id).model_copy(update={'execution': state}), reason='Synthetic old partial startup')
    assert p.binding(run.id)['process_id'] is None
    p.close(); r.close()
    r = Register(settings); a = MockAdapter([wire(envelope(x)) for x in ('analyzer', 'migrate', 'test')])
    p = Pipeline(r, CallJournal(r), a, runner); s = Scheduler(p); s.boot()
    assert p.binding(run.id)['status'] == 'recovery_required'
    assert s.tick() is None and a.send_count == 0
    s.resume(run.id, reason='Fresh conscious recovery of legacy claimed startup')
    assert p.binding(run.id)['clock_json'] is None
    s._resume(run.id, 'Fresh conscious recovery of legacy claimed startup')
    assert p.binding(run.id)['clock_json'] is None and a.send_count == 0
    assert r.state(run.id).execution == 'running'
    s.tick()
    assert r.state(run.id).execution == 'running'
    assert a.send_count == 1
    finish(s, run)
    assert r.state(run.id).terminal_cause == 'finished' and a.send_count == 3
    p.close(); r.close()


def test_completion_cas_failure_remains_recoverable_without_resend(tmp_path, monkeypatch):
    r, settings, _, run, _, p, s, a, runner = setup(tmp_path, planner=False, review=False)
    original = p.store.put_object
    def unavailable(data, **kwargs):
        if data.startswith(b'{"costs":'):
            raise OSError('Synthetic completion CAS unavailable')
        return original(data, **kwargs)
    monkeypatch.setattr(p.store, 'put_object', unavailable)
    with pytest.raises(OSError, match='completion CAS'):
        finish(s, run)
    assert r.state(run.id).execution == 'terminal' and a.send_count == 3
    assert p.binding(run.id)['status'] == 'recovery_required'
    assert not any(x.artifact_type == 'pipeline_summary' for x in r.all(Artifact))
    seal_id = r.state(run.id).candidate_id
    active = p.summary(run.id)['timing']['active']
    assert s.tick() is None and a.send_count == 3
    assert any(x.run_id == run.id and x.event_type == 'completion_recovery_required' and 'completion CAS unavailable' in x.details['reason'] for x in r.all(Event))
    monkeypatch.setattr(p.store, 'put_object', original)
    s.resume(run.id, reason='Fresh conscious recovery after CAS restored, same worker')
    s.tick()
    assert p.binding(run.id)['status'] == 'completed' and a.send_count == 3
    assert r.state(run.id).candidate_id == seal_id
    assert p.summary(run.id)['timing']['active'] == active
    saved = next(x for x in r.all(Artifact) if x.artifact_type == 'pipeline_summary')
    assert p.read_summary(saved.id)['cost_derivation_status'] == 'current'
    p.close(); r.close()


def test_completion_recovery_cas_failure_preserves_original_cause_and_waits(tmp_path, monkeypatch):
    r, _, _, run, _, p, s, a, _ = setup(tmp_path, planner=False, review=False)
    s.tick(); s.tick()
    s.request_abort(run.id, reason='Original conscious immediate stop', immediate=True)
    original = p.store.put_object
    def unavailable(data, **kwargs):
        if data.startswith(b'{"costs":'):
            raise OSError('Synthetic persistent summary CAS failure')
        return original(data, **kwargs)
    monkeypatch.setattr(p.store, 'put_object', unavailable)
    # The protected Seal boundary persists recovery instead of leaking this
    # storage failure to the worker's generic retry loop.
    assert s.tick() == run.id
    state = r.state(run.id)
    active = p.summary(run.id)['timing']['active']
    assert state.terminal_cause == 'interrupted'
    assert p.binding(run.id)['status'] == 'recovery_required' and s.tick() is None
    s.resume(run.id, reason='First conscious completion recovery while CAS still fails')
    s.tick()
    assert p.binding(run.id)['status'] == 'recovery_required' and s.tick() is None
    assert r.state(run.id) == state and a.send_count == 2
    assert p.summary(run.id)['timing']['active'] == active
    monkeypatch.setattr(p.store, 'put_object', original)
    s.resume(run.id, reason='Fresh conscious completion recovery after CAS restored')
    s.tick()
    assert p.binding(run.id)['status'] == 'completed' and a.send_count == 2
    assert r.state(run.id) == state
    assert p.summary(run.id)['timing']['active'] == active
    saved = next(x for x in r.all(Artifact) if x.artifact_type == 'pipeline_summary')
    assert p.read_summary(saved.id)['cost_derivation_status'] == 'current'
    p.close(); r.close()


def test_completion_register_diagnosis_failure_is_shown_without_persistence_claim(tmp_path, monkeypatch, capsys):
    r, _, _, run, _, p, s, _, _ = setup(tmp_path, planner=False, review=False)
    original = p.store.put_object
    def unavailable(data, **kwargs):
        if data.startswith(b'{"costs":'):
            r.connection.execute('PRAGMA query_only=ON')
            raise OSError('Synthetic CAS failure followed by readonly register')
        return original(data, **kwargs)
    monkeypatch.setattr(p.store, 'put_object', unavailable)
    with pytest.raises(OSError, match='readonly register'):
        finish(s, run)
    output = capsys.readouterr().out
    assert 'Registerdiagnose konnte nicht gespeichert werden' in output
    assert 'readonly' in output and 'Synthetic CAS failure' in output
    assert p.binding(run.id)['status'] != 'completed'
    assert not any(x.event_type == 'completion_recovery_required' for x in r.all(Event))
    r.connection.execute('PRAGMA query_only=OFF')
    p.close(); r.close()


def test_failed_terminal_clock_boundary_is_missing_after_conscious_recovery(tmp_path, monkeypatch):
    r, _, _, run, _, p, s, a, _ = setup(tmp_path, planner=False, review=False)
    original = p.store.put_object
    def unavailable(data, **kwargs):
        if data.startswith(b'{"gap":') and json.loads(data).get('transition') is None:
            raise OSError('Synthetic failed terminal clock CAS boundary')
        return original(data, **kwargs)
    monkeypatch.setattr(p.store, 'put_object', unavailable)
    with pytest.raises(OSError, match='terminal clock CAS'):
        finish(s, run)
    state = r.state(run.id)
    assert state.execution == 'terminal' and p.binding(run.id)['status'] == 'recovery_required'
    assert p.binding(run.id)['clock_json'] is not None and s.tick() is None
    monkeypatch.setattr(p.store, 'put_object', original)
    s.resume(run.id, reason='Fresh recovery after missing measured terminal boundary')
    s.tick()
    assert r.state(run.id) == state and a.send_count == 3
    assert p.binding(run.id)['status'] == 'completed' and p.binding(run.id)['clock_json'] is None
    assert p.summary(run.id)['timing']['active']['status'] == 'technical_missing'
    assert [x for x in r.all(TimeInterval) if x.run_id == run.id][-1].kind == 'unknown_active'
    p.close(); r.close()


@pytest.mark.parametrize('candidate', [False, True])
def test_abort_completion_crash_recovers_only_evidence_without_active_reopening(tmp_path, candidate):
    def crash(point):
        if point == 'completion:after_cas':
            raise RuntimeError('Synthetic abort completion crash')
    r, settings, _, run, _, p, s, a, runner = setup(tmp_path, planner=False, review=False, crash=crash)
    if candidate:
        s.tick(); s.tick()
    s.request_abort(run.id, reason='Conscious immediate stop before completion crash', immediate=True)
    with pytest.raises(RuntimeError, match='abort completion crash'):
        s.tick()
    state = r.state(run.id)
    assert state.execution == 'terminal' and state.terminal_cause == 'interrupted'
    assert p.binding(run.id)['clock_json'] is None
    assert p.binding(run.id)['status'] != 'completed'
    assert a.send_count == (2 if candidate else 0)
    active = p.summary(run.id)['timing']['active']
    graph = p.graph.get_state({'configurable': {'thread_id': str(run.id)}})
    assert graph.next if candidate else not graph.values
    p.close(); r.close()
    r = Register(settings); adapter = MockAdapter([])
    p = Pipeline(r, CallJournal(r), adapter, runner); s = Scheduler(p); s.boot()
    assert s.tick() is None and adapter.send_count == 0
    s.resume(run.id, reason='Fresh explicit recovery of abort completion evidence')
    s.tick()
    assert p.binding(run.id)['status'] == 'completed' and adapter.send_count == 0
    assert r.state(run.id) == state
    assert p.summary(run.id)['timing']['active'] == active
    assert len([x for x in r.all(CandidateSnapshot) if x.run_id == run.id and x.role == 'sealed']) == (1 if candidate else 0)
    saved = next(x for x in r.all(Artifact) if x.artifact_type == 'pipeline_summary')
    assert p.read_summary(saved.id)['cost_derivation_status'] == 'current'
    p.close(); r.close()


@pytest.mark.parametrize('kind', ['provider', 'candidate', 'infrastructure'])
def test_native_controlled_hanger_diagnosis_pause_and_stop(tmp_path, kind):
    r, settings, _, run, _, p, _, _, _ = setup(tmp_path, planner=False, review=False)
    p.close(); r.close()
    command = [sys.executable, str(Path(__file__).with_name('pipeline_worker_probe.py')), '--root', str(tmp_path), '--hang', kind]
    child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    def await_condition(predicate):
        # Test observation bound only, never a candidate/provider/evaluator limit.
        for _ in range(600):
            if predicate():
                return
            if child.poll() is not None:
                pytest.fail('Native worker exited before control boundary')
            time.sleep(.1)
        pytest.fail('Native synthetic control boundary not reached')
    try:
        await_condition(lambda: (tmp_path / 'hang.marker').exists())
        r = Register(settings); p = Pipeline(r, CallJournal(r), MockAdapter(), MockRunner()); s = Scheduler(p)
        if kind == 'provider':
            s.request_pause(run.id, reason='Synthetic pause requested in hanging provider')
            assert p.binding(run.id)['status'] == 'pause_requested'
            os_signal = signal.SIGUSR1
            child.send_signal(os_signal)
            await_condition(lambda: p.binding(run.id)['status'] == 'paused')
            assert len(r.all(ModelCall)) == 1
        target_process = p.binding(run.id)['process_id']
        before_calls = tuple(x.id for x in r.all(ModelCall))
        before_dispatch = (tmp_path / 'worker-send.raw').read_bytes()
        # The provider fixture pauses after analyzer; no producer candidate
        # exists yet. Candidate/infrastructure fixtures have a saved tree.
        prior_candidates = [x for x in r.all(CandidateSnapshot) if x.run_id == run.id]
        assert bool(prior_candidates) == (kind != 'provider')
        diagnosis = s.request_abort(run.id, reason='Synthetic conscious immediate stop of ' + kind, immediate=True)
        if kind != 'provider':
            child.send_signal(signal.SIGUSR1)
        stdout, stderr = child.communicate()
        (tmp_path / 'hanger.stdout').write_bytes(stdout); (tmp_path / 'hanger.stderr').write_bytes(stderr)
        assert child.returncode == 80
        decision = next(x for x in reversed(r.all(Event)) if x.event_type == 'abort_decision')
        assert decision.run_id == run.id and decision.details['target_process_id'] == target_process
        assert decision.evidence_ids == (diagnosis.id,)
        assert r.get(diagnosis.id, Artifact).run_id == run.id
        assert p._effect(run.id, 'abort')['statuses']['cause'] == 'interrupted'
        body = json.loads(p.store.read(diagnosis.id))
        assert body['call_journals'] and body['missing'] and body['immediate']
        assert len(r.all(ModelCall)) == (1 if kind == 'provider' else 3)
        assert not any(x.node == 'repair' for x in r.all(ModelCall))
        assert r.connection.execute('SELECT state FROM worker_status').fetchone()[0] == 'stopped'
        p.close(); r.close()
        restart = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        r = Register(settings)
        try:
            for _ in range(600):
                row = r.connection.execute('SELECT state FROM worker_status').fetchone()
                if row and row['state'] == 'running' and r.connection.execute('SELECT status FROM pipeline_binding WHERE run_id=?', (str(run.id),)).fetchone()[0] == 'recovery_required':
                    break
                assert restart.poll() is None
                time.sleep(.1)
            else:
                pytest.fail('Fresh worker did not await explicit stored-abort recovery')
            assert tuple(x.id for x in r.all(ModelCall)) == before_calls
            assert (tmp_path / 'worker-send.raw').read_bytes() == before_dispatch
            p = Pipeline(r, CallJournal(r), MockAdapter(), MockRunner()); s = Scheduler(p)
            s.resume(run.id, reason='TECHNICAL-FIXTURE: fresh same-run recovery of persisted immediate abort')
            out, err = restart.communicate()
            (tmp_path / 'hanger-recovery.stdout').write_bytes(out)
            (tmp_path / 'hanger-recovery.stderr').write_bytes(err)
            assert restart.returncode == 0
            assert p.binding(run.id)['status'] == 'completed'
            assert r.state(run.id).terminal_cause == 'interrupted'
            assert r.state(run.id).seal == ('no_candidate' if kind == 'provider' else 'sealed')
            assert tuple(x.id for x in r.all(ModelCall)) == before_calls
            assert (tmp_path / 'worker-send.raw').read_bytes() == before_dispatch
            sealed = [x for x in r.all(CandidateSnapshot) if x.run_id == run.id and x.role == 'sealed']
            assert len(sealed) == (0 if kind == 'provider' else 1)
            assert all(r.get(x.id, CandidateSnapshot) == x for x in prior_candidates)
            assert {x.id for x in r.all(CandidateSnapshot) if x.run_id == run.id} == {x.id for x in prior_candidates + sealed}
            if sealed:
                assert sealed[0].tree_hash == prior_candidates[-1].tree_hash
            p.close()
        finally:
            if restart.poll() is None:
                restart.kill(); restart.communicate()
            r.close()
    finally:
        if child.poll() is None:
            child.kill(); child.communicate()


@pytest.mark.parametrize('context_version', ['m2-v0.1', 'm2-v0.1-csrf1'])
@pytest.mark.parametrize('wrong', [None, 'context', 'scaffold'])
def test_production_provision_binds_actual_public_asset_manifests(tmp_path, wrong, context_version):
    from research_env.pipeline import role_assets
    r, _, store, first, _, p, s, _, _ = setup(tmp_path, planner=False, review=False)
    finish(s, first)
    original = r.get(first.configuration_version_id, ConfigurationVersion)
    locked = json.loads((ROOT / 'src/research_env/pipeline_assets.lock.json').read_text())
    context = r.add(AssetVersion(code=f'CTX-{uuid4()}', asset_type='source_context',
        manifest_hash=locked[f'assets/context/{context_version}/SQL/K1/manifest.json' if wrong == 'context' else f'assets/context/{context_version}/SQL/K0/manifest.json'],
        origin='Actual public M2 manifest binding; synthetic technical fixture', access_scope='role_package'))
    scaffold = r.add(AssetVersion(code=f'SC-{uuid4()}', asset_type='scaffold',
        manifest_hash='a' * 64 if wrong == 'scaffold' else locked['assets/study/m2-v0.1/manifest.json'],
        origin='Actual public M2 scaffold manifest binding; synthetic technical fixture', access_scope='shared_scaffold'))
    assets = role_assets(store)
    from research_env.context_assets import contract_binding
    contract = r.add(AssetVersion(code=f'REVISION-{uuid4()}', asset_type='contract', manifest_hash=contract_binding(ROOT)['manifest_sha256'], origin='Approved limited CSRF interpretation', access_scope='public_development'))
    settings = original.settings.model_copy(update={'contract_id': contract.id if context_version=='m2-v0.1-csrf1' else original.settings.contract_id, 'scaffold_id': scaffold.id, 'context_ids': {'SQL-K0': context.id},
        'handoff_id': assets['handoff'].id, 'prompt_ids': (assets['prompts'].id,)})
    version = r.version_configuration(original.configuration_id, 'synthetic-production-gate', original.cell, settings)
    run, _ = r.start_other(first.phase_id, version.id, decision='TECHNICAL-FIXTURE: validate production input binding', technical_evidence_ids=first.technical_evidence_ids, idempotency_key=str(uuid4()))
    body = p.manifest(first.id)
    kwargs = dict(dependency_id=UUID(body['dependency_id']), proof_ids=first.technical_evidence_ids,
        versions={'software_commit': 'synthetic-v1', 'runtime_images': {}, 'platform': 'synthetic proof fixture', 'isolation': 'synthetic proof fixture'}, synthetic=False)
    package = (ROOT / f'assets/context/{context_version}/SQL/K0/package.txt').read_bytes()
    if wrong:
        with pytest.raises(IntegrityError, match='Kontextasset' if wrong == 'context' else 'Gerüstasset'):
            provision(store, run.id, package=package, scaffold=ROOT / 'assets/study/m2-v0.1/scaffold', **kwargs)
    else:
        manifest = provision(store, run.id, package=package, scaffold=ROOT / 'assets/study/m2-v0.1/scaffold', **kwargs)
        payload = json.loads(store.read(manifest.id))
        assert store.read(payload['package_id']) == package
        assert payload['context_asset_ids'] == {'SQL-K0': str(context.id)}
        assert payload['asset_hashes'][str(context.id)] == context.manifest_hash
        from research_env.sandbox_runtime import Sandbox, RuntimeImages
        sandbox = Sandbox(store, object(), images=RuntimeImages(*('sha256:'+'1'*64 for _ in range(4))), assets=ROOT)
        assert sandbox.package(run.id) == package
    p.close(); r.close()


def test_new_boot_invalidates_unconsumed_resume_and_runner_projection(tmp_path):
    from research_env.scheduler import mark_worker_recovery
    r, _, _, run, _, p, s, a, _ = setup(tmp_path, planner=False, review=False)
    s.tick(); s.request_pause(run.id, reason='Pause'); s.tick()
    s.resume(run.id, reason='Old process action not consumed')
    with r.transaction():
        r.connection.execute('INSERT INTO pipeline_runner VALUES (?,?,?,?,?,?)', (str(run.id), 'fixture', 'a'*64, 'b'*64, 'running', None))
    mark_worker_recovery(r)
    assert p.binding(run.id)['status'] == 'recovery_required'
    assert r.connection.execute("SELECT status FROM pipeline_runner WHERE node='fixture'").fetchone()[0] == 'recovery_required'
    assert s.tick() is None and a.send_count == 1
    s.resume(run.id, reason='Fresh conscious action after new boot')
    finish(s, run)
    assert a.send_count == 3
    p.close(); r.close()


def test_preflight_identity_drift_ends_phase_instead_of_pause(tmp_path):
    from research_env.pipeline import PhaseIdentityDrift
    class DriftRunner(MockRunner):
        def preflight(self, run_id):
            raise PhaseIdentityDrift('Explicit frozen version identity changed')
    r, _, _, run, _, p, s, a, _ = setup(tmp_path, runner=DriftRunner())
    s.tick()
    assert a.send_count == 0 and r.phase_state(run.phase_id).status == 'stopped'
    with pytest.raises(GateError): r.set_phase_status(run.phase_id, 'ready', reason='Old freeze cannot resume')
    assert p.binding(run.id)['status'] == 'completed'
    p.close(); r.close()


@pytest.mark.parametrize('prepared_resume', [False, True])
def test_native_immediate_local_http_stop_and_fresh_recovery_preserves_abort(tmp_path, prepared_resume):
    r, settings, store, run, _, p, _, _, _ = setup(tmp_path, planner=False, review=False)
    p.close(); r.close()
    command = [sys.executable, str(Path(__file__).with_name('pipeline_worker_probe.py')), '--root', str(tmp_path), '--hang', 'http']
    if prepared_resume:
        initial = subprocess.run(command[:-2] + ['--crash', 'test:prepared'], capture_output=True)
        (tmp_path / 'http-prepared.stdout').write_bytes(initial.stdout)
        (tmp_path / 'http-prepared.stderr').write_bytes(initial.stderr)
        assert initial.returncode == 79
        r = Register(settings)
        call = next(x for x in r.all(ModelCall) if x.node == 'test')
        journal = CallJournal(r)
        assert journal.diagnostic(call.id)['attempts'][-1]['status'] == 'prepared'
        assert len((tmp_path / 'worker-send.raw').read_bytes().splitlines()) == 2
        r.close()
    stdout = (tmp_path / 'http-worker.stdout').open('wb'); stderr = (tmp_path / 'http-worker.stderr').open('wb')
    child = subprocess.Popen(command, stdout=stdout, stderr=stderr)
    try:
        if prepared_resume:
            r = Register(settings)
            for _ in range(600):
                row = r.connection.execute('SELECT state FROM worker_status').fetchone()
                if row and row['state'] == 'running' and r.connection.execute('SELECT status FROM pipeline_binding WHERE run_id=?', (str(run.id),)).fetchone()[0] == 'recovery_required': break
                assert child.poll() is None
                time.sleep(.1)
            else: pytest.fail('Prepared HTTP recovery did not await fresh action')
            cp = Pipeline(r, CallJournal(r), MockAdapter(), MockRunner()); cs = Scheduler(cp)
            assert not (tmp_path / 'http-accepted.json').exists()
            assert len((tmp_path / 'worker-send.raw').read_bytes().splitlines()) == 2
            cs.resume(run.id, reason='Fresh explicit prepared-call HTTP recovery after actual boot')
            cp.close(); r.close()
        for _ in range(600):
            if (tmp_path / 'http-accepted.json').exists(): break
            assert child.poll() is None
            time.sleep(.1)
        else: pytest.fail('Actual local HTTP response wait not reached')
        r = Register(settings); cp = Pipeline(r, CallJournal(r), MockAdapter(), MockRunner()); cs = Scheduler(cp)
        live = cp.binding(run.id)
        clock = json.loads(live['clock_json'])
        assert live['status'] == 'running' and clock['kind'] == 'active'
        assert clock['start']['process_id'] == live['process_id']
        assert len([x for x in r.all(CandidateSnapshot) if x.run_id == run.id]) == 1
        diagnosis = cs.request_abort(run.id, reason='TECHNICAL-FIXTURE: explicit immediate local HTTP wait stop', immediate=True)
        child.wait()
        assert child.returncode == 80
        assert cp._effect(run.id, 'abort')['statuses']['cause'] == 'outcome_unknown'
        assert cp.store.read(diagnosis.id) and r.state(run.id).execution == 'running'
        cp.close(); r.close()
        # New worker cannot consume the old abort automatically. It first
        # records a new boot and waits; the separate control action then seals.
        restart_out = (tmp_path / 'http-restart.stdout').open('wb'); restart_err = (tmp_path / 'http-restart.stderr').open('wb')
        restart = subprocess.Popen(command, stdout=restart_out, stderr=restart_err)
        r = Register(settings)
        try:
            for _ in range(600):
                row = r.connection.execute('SELECT state FROM worker_status').fetchone()
                if row and row['state'] == 'running' and r.connection.execute('SELECT status FROM pipeline_binding WHERE run_id=?', (str(run.id),)).fetchone()[0] == 'recovery_required': break
                assert restart.poll() is None
                time.sleep(.1)
            else: pytest.fail('New worker did not await explicit HTTP abort recovery')
            cp = Pipeline(r, CallJournal(r), MockAdapter(), MockRunner()); cs = Scheduler(cp)
            assert len((tmp_path / 'worker-send.raw').read_bytes().splitlines()) == 3
            cs.resume(run.id, reason='TECHNICAL-FIXTURE: fresh conscious completion of stored abort after boot')
            restart.wait()
            assert restart.returncode == 0
            assert r.state(run.id).terminal_cause == 'outcome_unknown' and r.state(run.id).seal == 'sealed'
            assert len((tmp_path / 'worker-send.raw').read_bytes().splitlines()) == 3
            assert len([x for x in r.all(ModelCall) if x.run_id == run.id]) == 3
            assert cp.summary(run.id)['timing']['active']['status'] == 'technical_missing'
            (tmp_path / 'http-summary.json').write_text(json.dumps(cp.summary(run.id), indent=2) + '\n')
            cp.close()
        finally:
            if restart.poll() is None: restart.kill(); restart.wait()
            restart_out.close(); restart_err.close(); r.close()
    finally:
        if child.poll() is None: child.kill(); child.wait()
        stdout.close(); stderr.close()


def test_worker_factory_selects_operational_network_free_mock(tmp_path, monkeypatch):
    import docker
    from research_env import worker
    from research_env.pipeline_mock import PipelineMockAdapter
    class Client:
        def close(self): pass
    r, _, _, run, _, old, _, _, _ = setup(tmp_path, planner=False, review=False)
    old.close()
    monkeypatch.setattr(docker, 'from_env', lambda **_: Client())
    scheduler = worker.pipeline_service(r)
    actual_runner = scheduler.pipeline.runner
    assert isinstance(scheduler.pipeline.adapter, PipelineMockAdapter)
    assert actual_runner.__class__.__name__ == 'InternalRunner'
    scheduler.pipeline.runner = MockRunner()
    finish(scheduler, run)
    assert scheduler.pipeline.adapter.send_count == 3
    assert r.state(run.id).terminal_cause == 'finished'
    scheduler.pipeline.close(); scheduler.pipeline.adapter.close(); actual_runner.close(); r.close()


def test_preflight_drift_during_explicit_resume_stops_old_phase_and_seals(tmp_path):
    from research_env.pipeline import PhaseIdentityDrift
    r, _, _, run, _, p, s, a, _ = setup(tmp_path, planner=False, review=False)
    s.tick(); s.tick(); s.request_pause(run.id, reason='Pause existing candidate'); s.tick()
    r.set_phase_status(run.phase_id, 'paused', reason='Separate phase pause fixture')
    p.preflight = lambda _: (_ for _ in ()).throw(PhaseIdentityDrift('Frozen runtime identity changed'))
    s.resume(run.id, reason='Explicit resume must not reuse changed identity')
    s.tick()
    assert r.phase_state(run.phase_id).status == 'stopped'
    assert r.state(run.id).seal == 'sealed' and p.binding(run.id)['status'] == 'completed'
    assert a.send_count == 2
    assert [x for x in r.all(TimeInterval) if x.run_id == run.id][-1].kind == 'active'
    assert p.summary(run.id)['timing']['active']['status'] == 'observed'
    p.close(); r.close()


def test_normal_mock_factory_registers_revised_public_assets_before_configuration(tmp_path):
    r,settings,store,run,job,p,s,adapter,runner=setup(tmp_path,planner=False,review=False,
        scaffold_path=ROOT/'assets/study/m2-v0.1/scaffold',
        package_bytes=(ROOT/'assets/context/m2-v0.1-csrf1/SQL/K0/package.txt').read_bytes(),csrf_revision=True,source_commit='synthetic-component-pin')
    conf=r.get(run.configuration_version_id,ConfigurationVersion);body=p.manifest(run.id)
    assert not body['synthetic']
    assert body['source_binding']['package_path']=='assets/context/m2-v0.1-csrf1/SQL/K0/package.txt'
    p.preflight(run.id)
    finish(s,run)
    assert r.state(run.id).seal=='sealed'
    for call in r.all(ModelCall):
        if call.run_id==run.id:
            request=json.loads(store.read(call.messages_artifact_id))
            assert 'M2-v0.1-CSRF1' in json.dumps(request)
    assert conf.settings.contract_id and conf.settings.context_ids['SQL-K0']
    p.close();r.close()

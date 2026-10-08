#!/usr/bin/env python3
"""Actual fixed M4 internal jobs through a complete offline M5 graph.

Reads only public role/scaffold/development assets and the verified vendorroot.
No hidden suite/reference/evaluator contents or real model dispatch.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from uuid import UUID

import docker

ROOT = Path(__file__).parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
from research_env.artifacts import IntegrityError
from research_env.domain import Artifact, AssetVersion, CandidateSnapshot, ConfigurationVersion, ModelCall, RunState
from research_env.pipeline_runner import InternalRunner
from research_env.sandbox_runtime import RuntimeImages, Sandbox
from research_env.snapshots import inventory
from test_pipeline import setup, envelope, wire, finish
from research_env.pipeline import provision, roles
import test_register as fixtures

p = argparse.ArgumentParser()
p.add_argument('--output', type=Path, required=True)
p.add_argument('--vendor', type=Path, required=True)
p.add_argument('--case', choices=('regression', 'syntax'), default='regression')
p.add_argument('--main-scope', action='store_true', help='Separate synthetic Main syntax/handoff component; no Main model dispatch')
args = p.parse_args()
if args.output.exists():
    p.error('Belegordner existiert; Originale nicht überschreiben')
args.output.mkdir(parents=True)
expected = {'schema': 'm5-pipeline-native-v1', 'calls': ['analyzer', 'planner', 'migrate', 'test', 'review', 'repair'],
            'first_runner': 'passed', 'second_runner': 'candidate_failure', 'same_tests': True,
            'final_cause': 'content_failure', 'final_candidate': 'repair', 'external_suite': 'never allocated',
            'stage_order': ['syntax', 'boot', 'tests', 'syntax', 'boot', 'tests'], 'scope': 'cost-free technical fixture; no S1/pilot/study judgment'}
if args.case == 'syntax':
    expected.update(calls=['analyzer', 'migrate', 'test', 'repair'], first_runner='candidate_failure', second_runner='passed',
                    final_cause='finished', stage_order=['syntax', 'syntax', 'boot', 'tests'])
(args.output / 'expected.json').write_text(json.dumps(expected, indent=2) + '\n')
migrate = envelope('migrate')
migrate['files'][0]['content'] = '<?php\n// PUBLIC_INITIAL_MARKER\nuse Illuminate\\Support\\Facades\\Route;\nRoute::get("/study/SQL", fn () => response("public development fixture"));\n'
repair = envelope('repair')
repair['files'][0]['content'] = '<?php\n// REPAIRED_LAST_STAND\nuse Illuminate\\Support\\Facades\\Route;\nRoute::get("/study/SQL", fn () => response("regressed fixture"));\n'
test = envelope('test')
test['files'][0]['content'] = '<?php\nif (!str_contains(file_get_contents("/opt/study/routes/study.php"), "PUBLIC_INITIAL_MARKER")) { throw new RuntimeException("INTERNAL_UNCHANGED_TEST_REGRESSION"); }\necho "INTERNAL_PUBLIC_OK";\n'
outputs = [wire(envelope('analyzer')), wire(envelope('planner')), wire(migrate), wire(test),
           wire(envelope('review', review=True)), wire(repair)]
if args.case == 'syntax':
    migrate['files'][0]['content'] = '<?php broken syntax @@@;'
    test['files'][0]['content'] = '<?php if (!file_exists("/opt/study/routes/study.php")) { throw new RuntimeException("OWN_INTERNAL_MISSING"); } echo "OWN_INTERNAL_PASS";'
    outputs = [wire(envelope('analyzer')), wire(migrate), wire(test), wire(repair)]
records = inventory(args.vendor)
(args.output / 'vendor-input.json').write_text(json.dumps({'files': len(records), 'bytes': sum(x['size'] for x in records),
    'tree_hash': __import__('research_env.domain', fromlist=['digest']).digest(records), 'root': str(args.vendor), 'readonly': True}, indent=2) + '\n')
images = RuntimeImages(**json.loads((ROOT / 'src/research_env/pipeline_runtime.lock.json').read_text()))
runtime = docker.from_env(timeout=None)
versions = {key: {'reference': value, 'actual_id': runtime.images.get(value).id} for key, value in vars(images).items()}
(args.output / 'platform.json').write_text(json.dumps({'observed_at': datetime.now(timezone.utc).isoformat(),
    'docker': runtime.version(), 'images': versions}, indent=2) + '\n')
package = (ROOT / 'assets/context/m2-v0.1/SQL/K0/package.txt').read_bytes()
r, settings, store, run, job, pipeline, scheduler, adapter, _ = setup(args.output / 'instance',
    outputs=outputs, scaffold_path=ROOT / 'assets/study/m2-v0.1/scaffold', vendor_path=args.vendor,
    package_bytes=package, planner=args.case == 'regression', review=args.case == 'regression')
sandbox = Sandbox(store, runtime, images=images, assets=ROOT)
pipeline.runner = InternalRunner(sandbox)
results = []
try:
    finish(scheduler, run)
    calls = [x for x in r.all(ModelCall) if x.run_id == run.id]
    check = lambda name, value: results.append({'name': name, 'passed': bool(value)})
    check('calls', [x.node for x in calls] == expected['calls'] and adapter.send_count == len(expected['calls']))
    check('full_same_K0', all(json.loads(json.loads(store.read(c.messages_artifact_id))['messages'][1]['content'])['inputs'][0]['content'] == package.decode() for c in calls))
    # Compare actual decoded input payloads; JSON escapes are not package bytes.
    package_ids = [c.input_artifact_ids[0] for c in calls]
    check('same_full_package_artifact', len(set(package_ids)) == 1 and store.read(package_ids[0]) == package)
    receipts = [json.loads(store.read(x.id)) for x in r.all(Artifact) if x.run_id == run.id and x.artifact_type == 'internal_runner_receipt']
    check('runner_causes', [x['classification'] for x in receipts] == [expected['first_runner'], expected['second_runner']])
    check('same_unchanged_tests', len({x['internal_tests_id'] for x in receipts}) == 1 and len({x['tests_hash'] for x in receipts}) == 1)
    check('six_actual_stages', [x['stage'] for receipt in receipts for x in receipt['observations']] == expected['stage_order'])
    check('development_only', all(row['suite_kind'] == 'development' and row['profile'] != 'http' for row in r.connection.execute('SELECT * FROM sandbox_execution')))
    state = r.state(run.id)
    check('last_expected_stand_sealed', state.terminal_cause == expected['final_cause'] and state.seal == 'sealed')
    sealed = r.get(state.candidate_id, CandidateSnapshot)
    root = settings.artifacts / 'sealed' / str(sealed.id)
    check('readonly_last_stand', b'REPAIRED_LAST_STAND' in (root / 'routes/study.php').read_bytes() and not ((root / 'routes/study.php').stat().st_mode & 0o222))
    check('internal_logs_are_role_scoped', all(r.get(x['artifact_id'], Artifact).access_scope == 'public_development' for receipt in receipts for observation in receipt['observations'] for x in observation.get('logs', [])))
    check('CAS_integrity', store.integrity()['complete'])
    summary = pipeline.summary(run.id)
    check('UF5_measured', summary['timing']['active']['status'] == 'observed')
    check('manual_wait', scheduler.tick() is None)
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    (args.output / 'calls.json').write_text(json.dumps([x.model_dump(mode='json') for x in calls], indent=2) + '\n')
    (args.output / 'runner-receipts.json').write_text(json.dumps(receipts, indent=2) + '\n')
    (args.output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    if not all(x['passed'] for x in results):
        raise AssertionError('Native pipeline checks failed')
    if args.main_scope:
        # Synthetic Main metadata/gates only. Never open hidden fixture bytes.
        # Reuse this installation's verified dependency CAS, not foreign runs.
        old_artifact = fixtures.artifact
        fixtures.artifact = lambda register, run=None, **kw: store.store(b'TECHNICAL-FIXTURE: scoped Main metadata', run_id=run.id if run else None, **kw)
        try:
            fixture = fixtures.setup_study(r)
            freeze = r.freeze(fixtures.freeze_value(r, fixture))
            fixtures.backup(r, freeze, fixture['basic'])
            main, job = scheduler.start_main(freeze.id, decision='TECHNICAL-FIXTURE: Main syntax/handoff component only', idempotency_key='native-main-scope', technical_evidence_ids=(fixture['basic'].id,))
        finally:
            fixtures.artifact = old_artifact
        conf = r.get(main.configuration_version_id, ConfigurationVersion)
        assert r.get(main.suite_id, AssetVersion).suite_kind == 'study_holdout'
        main_package = (ROOT / f'assets/context/m2-v0.1/{conf.cell.module}/{conf.cell.context}/package.txt').read_bytes()
        dependency_id = UUID(pipeline.manifest(run.id)['dependency_id'])
        manifest = provision(store, main.id, package=main_package, scaffold=ROOT / 'assets/study/m2-v0.1/scaffold', dependency_id=dependency_id,
            proof_ids=(fixture['basic'].id,), versions={'synthetic': 'Main scope only'}, synthetic=True)
        preview = pipeline.journal.preview(adapter, main.id, {'uncertainty': 'Synthetic Main scope fixture; no actual approval'})
        order = pipeline.journal.record_start_order(main.id, preview.id, person='TECHNICAL-FIXTURE: control', decision='Scoped handoff check only', roles=roles(conf.cell), synthetic_fixture=True)
        pipeline.install(job.id, manifest.id, order)
        workspace = pipeline._restore_scaffold(main.id)
        (workspace / 'routes/study.php').write_text('<?php // PUBLIC_MAIN_SYNTAX_ONLY')
        candidate = pipeline.snapshots.capture(workspace, run_id=main.id, role='post_migrate', scaffold_id=conf.settings.scaffold_id,
            dependency_ids=(dependency_id,), internal_tests_hash=hashlib.sha256(b'').hexdigest())
        handle = sandbox.allocate(job.id, candidate.id, profile='syntax')
        handle.start()
        native = next(x for x in handle.containers if x.name.endswith('-syntax')).wait()
        diagnosis = handle.observe()
        handle.abort(reason='Main syntax scope component finished')
        main_logs = [x for x in r.all(Artifact) if x.run_id == main.id and x.artifact_type == 'sandbox_log']
        assert native['StatusCode'] == 0 and main_logs
        assert all(x.access_scope == 'public_development' for x in main_logs)
        actual_resources = json.loads(r.connection.execute('SELECT body FROM sandbox_execution WHERE id=?', (handle.id,)).fetchone()[0])['resources']
        assert not any('suite' in x['name'] or 'client-requests' in x['name'] for x in actual_resources)
        receipt = store.json({'schema': 'pipeline-internal-scope-fixture-v1', 'log_artifact_ids': [str(x.id) for x in main_logs],
            'logs': [pipeline._role_ref(main.id, x.id).decode() for x in main_logs]}, run_id=main.id, access_scope='role', artifact_type='pipeline_internal_result')
        protected = store.store(b'OWN_SYNTHETIC_EXTERNAL_MARKER_NEVER_ROLE_INPUT', run_id=main.id, access_scope='trusted_evaluator', artifact_type='external_result')
        state = {'run_id': str(main.id), 'config_hash': main.effective_hash, 'statuses': {}, 'refs': {'runner': str(receipt.id)}}
        ids, contents = pipeline._inputs(pipeline._checked_state(state), 'repair')
        assert str(receipt.id) in [str(x) for x in ids] and b'OWN_SYNTHETIC_EXTERNAL_MARKER' not in json.dumps(contents).encode()
        rejected = []
        for gate in ('role', 'checkpoint', 'handoff'):
            try:
                if gate == 'role': pipeline._role_ref(main.id, protected.id)
                elif gate == 'checkpoint': pipeline._checked_state(dict(state, refs={'runner': str(protected.id)}))
                else: pipeline._inputs(dict(state, refs={'runner': str(protected.id)}), 'repair')
            except IntegrityError:
                rejected.append(gate)
        assert rejected == ['role', 'checkpoint', 'handoff']
        assert not [x for x in r.all(ModelCall) if x.run_id == main.id]
        (args.output / 'main-scope.json').write_text(json.dumps({'run_id': str(main.id), 'planned_id': str(main.planned_run_id),
            'main_suite_kind': 'study_holdout', 'execution_id': handle.id, 'actual_internal_suite_kind': 'development',
            'diagnosis_id': str(diagnosis.id), 'actual_log_ids': [str(x.id) for x in main_logs], 'role_receipt_id': str(receipt.id),
            'protected_synthetic_id': str(protected.id), 'negative_gates': rejected, 'handoff_artifact_ids': [str(x) for x in ids],
            'resources': actual_resources, 'native_exit': native['StatusCode'], 'model_calls': 0,
            'scope': 'Synthetic Main metadata/backup only; actual syntax and role gates. No hidden fixture read/mount, Main model dispatch or study approval.'}, indent=2) + '\n')
        r.set_state(main.id, RunState(execution='terminal', terminal_cause='interrupted', candidate_id=candidate.id, ended_at=datetime.now(timezone.utc)), reason='Synthetic Main scope component intentionally stopped before graph execution/Seal')
        with r.transaction():
            r.connection.execute("UPDATE pipeline_binding SET status='completed',reason='Synthetic scope component only' WHERE run_id=?", (str(main.id),))

finally:
    (args.output / 'results.partial.json').write_text(json.dumps(results, indent=2) + '\n')
    pipeline.close(); r.close(); runtime.close()

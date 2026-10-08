#!/usr/bin/env python3
"""Normal operational worker factory, genuine M4 jobs, offline model roles."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

import docker

ROOT = Path(__file__).parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
from research_env.adapter import CallJournal
from research_env.domain import Artifact, ModelCall
from research_env.pipeline import Pipeline
from research_env.providers import MockAdapter
from research_env.register import Register
from research_env.sandbox_runtime import PREFIX
from test_pipeline import setup, MockRunner

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--vendor', type=Path, required=True)
parser.add_argument('--csrf-revision', action='store_true')
parser.add_argument('--source-commit')
args = parser.parse_args()
if args.output.exists(): parser.error('Original evidence already exists')
args.output.mkdir(parents=True)
(args.output / 'expected.json').write_text(json.dumps({'roles': ['analyzer', 'migrate', 'test'], 'terminal': 'finished', 'seal': 'sealed',
    'worker': 'stopped', 'stages': ['syntax', 'boot', 'internal'], 'factory': 'unchanged production pipeline_service; PipelineMockAdapter based on own mock config',
    'cleanup_status': 'interrupted', 'cleanup_contract': 'SandboxHandle.abort normal cleanup; unchanged M4 base763513aaeaa298fa3cf669e89db8d4baf7a84470',
    'receipt_classification': 'passed', 'receipt_stages': ['syntax', 'boot', 'tests'], 'stage_exit_codes': [0, 0, 0],
    'remaining_owned_containers': [], 'active_owned_processes': [],
    'scope': 'Offline technical fixture; no external model dispatch/capability/study approval'}, indent=2) + '\n')
root = args.output / 'instance'
r, settings, _, run, _, p, _, _, _ = setup(root, planner=False, review=False,
    scaffold_path=ROOT / 'assets/study/m2-v0.1/scaffold', vendor_path=args.vendor,
    package_bytes=(ROOT / ('assets/context/m2-v0.1-csrf1/SQL/K0/package.txt' if args.csrf_revision else 'assets/context/m2-v0.1/SQL/K0/package.txt')).read_bytes(),
    csrf_revision=args.csrf_revision, source_commit=args.source_commit)
if args.csrf_revision:
    from research_env.domain import Run, ConfigurationVersion, AssetVersion
    conf=r.get(run.configuration_version_id,ConfigurationVersion)
    binding=p.manifest(run.id)
    assert not binding['synthetic'] and binding['source_binding']['package_path']=='assets/context/m2-v0.1-csrf1/SQL/K0/package.txt'
    (args.output/'public-revision-binding.json').write_text(json.dumps({'configuration':conf.model_dump(mode='json'),'contract':r.get(conf.settings.contract_id,AssetVersion).model_dump(mode='json'),'context':r.get(conf.settings.context_ids['SQL-K0'],AssetVersion).model_dump(mode='json'),'provision':binding},indent=2)+'\n')
p.close(); r.close()
command = [sys.executable, str(ROOT / 'tests/pipeline_worker_probe.py'), '--root', str(root), '--operational']
(args.output / 'command.json').write_text(json.dumps(command, indent=2) + '\n')
with (args.output / 'worker.stdout').open('wb') as stdout, (args.output / 'worker.stderr').open('wb') as stderr:
    result = subprocess.run(command, stdout=stdout, stderr=stderr)
r = Register(settings); p = Pipeline(r, CallJournal(r), MockAdapter(), MockRunner())
try:
    calls = [x for x in r.all(ModelCall) if x.run_id == run.id]
    state = r.state(run.id)
    observations = [dict(x) for x in r.connection.execute('SELECT * FROM sandbox_execution WHERE run_id=?', (str(run.id),))]
    receipts = [json.loads(p.store.read(x.id)) for x in r.all(Artifact) if x.run_id == run.id and x.artifact_type == 'internal_runner_receipt']
    cleanup = {'recorded_containers': [], 'remaining_owned_containers': [], 'active_owned_processes': [],
               'worker_returncode': result.returncode, 'scope': 'Read-only Docker verification of own persisted instance/run/execution labels and IDs'}
    client = docker.from_env(timeout=None)
    try:
        for execution in observations:
            body = json.loads(execution['body'])
            labels = body['labels']
            assert labels[PREFIX + 'run'] == str(run.id) and labels[PREFIX + 'execution'] == execution['id']
            for resource in body['resources']:
                if resource['kind'] != 'container':
                    continue
                entry = {'execution_id': execution['id'], 'container_id': resource['id'], 'expected_labels': labels}
                try:
                    container = client.containers.get(resource['id'])
                except docker.errors.NotFound:
                    entry['removed'] = True
                else:
                    container.reload()
                    assert all(container.labels.get(key) == value for key, value in labels.items())
                    entry.update(removed=False, state=container.attrs['State'])
                cleanup['recorded_containers'].append(entry)
            filters = {'label': [PREFIX + 'instance=' + labels[PREFIX + 'instance'], PREFIX + 'run=' + str(run.id), PREFIX + 'execution=' + execution['id']]}
            for container in client.containers.list(all=True, filters=filters):
                container.reload()
                assert all(container.labels.get(key) == value for key, value in labels.items())
                entry = {'execution_id': execution['id'], 'container_id': container.id, 'state': container.attrs['State']}
                cleanup['remaining_owned_containers'].append(entry)
                native = entry['state']
                if native.get('Running') or native.get('Paused') or native.get('Restarting') or native.get('Pid', 0):
                    cleanup['active_owned_processes'].append(entry)
    finally:
        client.close()
    (args.output / 'cleanup.json').write_text(json.dumps(cleanup, indent=2) + '\n')
    summary = p.summary(run.id)
    values = {'exit_code': result.returncode, 'run_id': str(run.id), 'roles': [x.node for x in calls],
        'terminal': state.terminal_cause, 'seal': state.seal, 'worker': r.connection.execute('SELECT state FROM worker_status').fetchone()[0],
        'stages': [x['profile'] for x in observations], 'executions': observations, 'mock_config': True, 'usage': 'synthetic zero; not actual model consumption'}
    (args.output / 'results.json').write_text(json.dumps(values, indent=2) + '\n')
    (args.output / 'calls.json').write_text(json.dumps([x.model_dump(mode='json') for x in calls], indent=2) + '\n')
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    (args.output / 'runner-receipts.json').write_text(json.dumps(receipts, indent=2) + '\n')
    assert values['exit_code'] == 0 and values['roles'] == ['analyzer', 'migrate', 'test']
    assert values['terminal'] == 'finished' and values['seal'] == 'sealed' and values['worker'] == 'stopped'
    assert values['stages'] == ['syntax', 'boot', 'internal']
    # M4's persisted cleanup projection is interrupted, independently of the
    # successful stage exit/receipt and the already stopped worker process.
    assert all(x['suite_kind'] == 'development' and x['status'] == 'interrupted' for x in observations)
    assert len(receipts) == 1 and receipts[0]['classification'] == 'passed' and receipts[0]['run_id'] == str(run.id)
    stages = receipts[0]['observations']
    assert [x['stage'] for x in stages] == ['syntax', 'boot', 'tests']
    assert [x['execution_id'] for x in stages] == [x['id'] for x in observations]
    for stage in stages:
        native = stage['native_state']
        assert stage['exit_code'] == native['ExitCode'] == 0 and stage['suite_kind'] == 'development'
        assert not any(native.get(key) for key in ('Running', 'Paused', 'Restarting', 'OOMKilled', 'Error', 'Pid'))
        assert stage['diagnosis_id'] and stage['logs'] and all(x['artifact_id'] for x in stage['logs'])
    assert cleanup['recorded_containers'] and all(x['removed'] for x in cleanup['recorded_containers'])
    assert not cleanup['remaining_owned_containers'] and not cleanup['active_owned_processes']
finally:
    p.close(); r.close()

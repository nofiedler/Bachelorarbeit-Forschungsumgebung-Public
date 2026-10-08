#!/usr/bin/env python3
"""Two actual native processes: hung public internal job, scoped instant stop."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

import docker

ROOT = Path(__file__).parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
from research_env.adapter import CallJournal
from research_env.artifacts import ArtifactStore
from research_env.domain import Artifact, ModelCall, Run
from research_env.pipeline import Pipeline
from research_env.pipeline_runner import InternalRunner
from research_env.providers import MockAdapter
from research_env.register import Register
from research_env.sandbox_runtime import RuntimeImages, Sandbox, PREFIX
from research_env.scheduler import Scheduler
from test_pipeline import setup, envelope

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--vendor', type=Path, required=True)
args = parser.parse_args()
if args.output.exists():
    parser.error('Originalordner existiert')
args.output.mkdir(parents=True)
(args.output / 'expected.json').write_text(json.dumps({'calls': 3, 'repair': 0, 'stop': 'owned persisted candidate labels only',
    'terminal': 'interrupted', 'seal': 'sealed', 'worker': 'stopped', 'suite': 'development',
    'actual_job_hang': 'PHP loop; no time cutoff; explicit separate-control-process stop'}, indent=2) + '\n')
root = args.output / 'instance'
package = (ROOT / 'assets/context/m2-v0.1/SQL/K0/package.txt').read_bytes()
r, settings, store, run, _, p, _, _, _ = setup(root, planner=False, review=False,
    scaffold_path=ROOT / 'assets/study/m2-v0.1/scaffold', vendor_path=args.vendor, package_bytes=package)
model_outputs = {name: envelope(name) for name in ('analyzer', 'migrate', 'test')}
model_outputs['test']['files'][0]['content'] = '<?php echo "OWN_PUBLIC_HANGER_STARTED\\n"; flush(); while (true) { usleep(100000); }'
(root / 'native-role-outputs.json').write_text(json.dumps(model_outputs, indent=2) + '\n')
p.close(); r.close()
command = [sys.executable, str(ROOT / 'tests/pipeline_worker_probe.py'), '--root', str(root), '--native']
(args.output / 'worker-command.json').write_text(json.dumps(command, indent=2) + '\n')
stdout_file = (args.output / 'worker.stdout').open('wb')
stderr_file = (args.output / 'worker.stderr').open('wb')
child = subprocess.Popen(command, stdout=stdout_file, stderr=stderr_file)
client = docker.from_env(timeout=None)
images = RuntimeImages(**json.loads((ROOT / 'src/research_env/pipeline_runtime.lock.json').read_text()))
r = Register(settings); store = ArtifactStore(settings, r)
sandbox = Sandbox(store, client, images=images, assets=ROOT)
p = Pipeline(r, CallJournal(r), MockAdapter(), InternalRunner(sandbox)); scheduler = Scheduler(p)
try:
    execution = None
    while execution is None:
        if child.poll() is not None:
            raise AssertionError('Worker exited before native hanger')
        rows = r.connection.execute("SELECT * FROM sandbox_execution WHERE run_id=? AND profile='internal' AND status='running'", (str(run.id),)).fetchall()
        for row in rows:
            spool = settings.staging / 'sandbox' / row['id'] / 'candidate.raw'
            if spool.exists() and b'OWN_PUBLIC_HANGER_STARTED' in spool.read_bytes():
                execution = dict(row)
                break
        time.sleep(.1)  # Observation only; no runtime deadline.
    body = json.loads(execution['body'])
    (args.output / 'before-stop.json').write_text(json.dumps({'execution': execution,
        'labels': body['labels'], 'actual_running_candidate': True, 'own_run_label': body['labels'][PREFIX + 'run']}, indent=2) + '\n')
    diagnosis = scheduler.request_abort(run.id, reason='TECHNICAL-FIXTURE: explicit native separate-process immediate stop', immediate=True)
    child.wait()
    stdout_file.flush(); stderr_file.flush()
    state = r.state(run.id)
    calls = [x for x in r.all(ModelCall) if x.run_id == run.id]
    result = {'process_exit': child.returncode, 'execution_id': execution['id'], 'diagnosis_id': str(diagnosis.id),
        'run_id': str(run.id), 'calls': len(calls), 'repair': len([x for x in calls if x.node == 'repair']),
        'terminal': state.terminal_cause, 'seal': state.seal,
        'worker': r.connection.execute('SELECT state FROM worker_status').fetchone()[0],
        'recorded_stop_observations': [dict(x) for x in r.connection.execute("SELECT * FROM sandbox_observation WHERE execution_id=? AND kind='recorded_stop_requested'", (execution['id'],))],
        'remaining_active': [dict(x) for x in r.connection.execute("SELECT id,status FROM sandbox_execution WHERE run_id=? AND status IN ('allocated','starting','running','recovery_required')", (str(run.id),))]}
    (args.output / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    (args.output / 'summary.json').write_text(json.dumps(p.summary(run.id), indent=2) + '\n')
    (args.output / 'calls.json').write_text(json.dumps([x.model_dump(mode='json') for x in calls], indent=2) + '\n')
    (args.output / 'diagnosis.json').write_bytes(store.read(diagnosis.id))
    receipts = [json.loads(store.read(x.id)) for x in r.all(Artifact) if x.run_id == run.id and x.artifact_type == 'internal_runner_receipt']
    (args.output / 'runner-receipts.json').write_text(json.dumps(receipts, indent=2) + '\n')
    (args.output / 'sandbox-observations.json').write_text(json.dumps([dict(x) for x in r.connection.execute('SELECT * FROM sandbox_observation WHERE execution_id=?', (execution['id'],))], indent=2) + '\n')
    result['actual_mock_sends'] = len((root / 'worker-send.raw').read_bytes().splitlines())
    (args.output / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    assert result['actual_mock_sends'] == 3 and p.summary(run.id)['timing']['active']['status'] == 'observed'
    assert result['process_exit'] == 0 and result['calls'] == 3 and result['repair'] == 0
    assert result['terminal'] == 'interrupted' and result['seal'] == 'sealed' and result['worker'] == 'stopped'
    assert result['recorded_stop_observations'] and not result['remaining_active']
finally:
    if child.poll() is None:
        child.kill(); child.wait()
        sandbox.recover(decision='stop_owned', run_id=run.id)
    p.close(); r.close(); client.close(); stdout_file.close(); stderr_file.close()

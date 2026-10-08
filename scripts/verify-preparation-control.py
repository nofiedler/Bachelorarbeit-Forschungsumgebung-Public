#!/usr/bin/env python3
"""Real blocked offline subprocess + real HTTP commands, no paid/model API."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import signal
import subprocess
import sys
import time
from uuid import UUID
from fastapi.testclient import TestClient
from research_env.domain import Artifact,Event,ModelCall
from research_env.register import Register
from research_env.artifacts import ArtifactStore
from research_env.web import create_app
from test_pipeline import setup

p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--block',choices=('provider','candidate'),default='provider');a=p.parse_args()
expected_calls=1 if a.block=='provider' else 3
expected_cause='outcome_unknown' if a.block=='provider' else 'interrupted'
if a.output.exists():p.error('Fresh evidence destination required')
a.output.mkdir(parents=True)
expected={'block_marker':True,'pause':'pause_requested','worker_exit':80,'terminal_transport':expected_cause,'calls_after_stop':expected_calls,
 'diagnosis':True,'post_restart_auto_calls':expected_calls,'recovery_conscious':True,'total_calls_after_recovery':expected_calls,'scope':'Real worker subprocess, controlled offline blocked transport; not a real provider or study gate'}
(a.output/'expected.json').write_text(json.dumps(expected,indent=2)+'\n')
r,settings,store,run,job,pipeline,s,adapter,runner=setup(a.output/'instance',planner=False,review=False)
pipeline.close();r.close()
commands=[]
def execute(command,stem):
    before=datetime.now(timezone.utc).isoformat()
    with (a.output/(stem+'.stdout')).open('wb') as stdout,(a.output/(stem+'.stderr')).open('wb') as stderr:
        child=subprocess.Popen(command,stdout=stdout,stderr=stderr)
    return child,before

def wait_for(predicate,child=None):
    # Test harness bound only; production has no watchdog/cutoff.
    for _ in range(600):
        if predicate():return
        if child and child.poll() is not None:raise AssertionError('Child exited before expected persisted state: '+str(child.returncode))
        time.sleep(.05)
    raise AssertionError('Harness did not observe expected persisted state')

probe=Path(__file__).resolve().parents[1]/'tests/pipeline_worker_probe.py'
command=[sys.executable,str(probe),'--root',str(a.output/'instance'),'--hang',a.block]
child,before=execute(command,'blocked-worker')
try:
    wait_for(lambda:(a.output/'instance/hang.marker').exists(),child)
    r=Register(settings)
    with TestClient(create_app(settings)) as client:
        page=client.get('/runs/'+str(run.id));assert page.status_code==200
        (a.output/'blocked-detail.html').write_text(page.text)
        token=client.cookies['research_csrf']
        pause=client.post('/actions',data={'csrf':token,'action':'pause','target_id':str(run.id),'decision':'TECHNICAL-FIXTURE: pause during blocked offline call','idempotency_key':'pause-blocked'},headers={'Origin':'http://testserver'},follow_redirects=False)
        assert pause.status_code==303
        wait_for(lambda:r.connection.execute('SELECT status FROM pipeline_binding WHERE run_id=?',(str(run.id),)).fetchone()[0]=='pause_requested',child)
        assert (r.connection.execute('SELECT status FROM transport_state').fetchone()[0]=='dispatching') if a.block=='provider' else not any(x[0]=='dispatching' for x in r.connection.execute('SELECT status FROM transport_state'))
        assert not any(e.event_type=='actual_pause' for e in r.all(Event))
        stop=client.post('/actions',data={'csrf':token,'action':'abort','immediate':'true','target_id':str(run.id),'decision':'TECHNICAL-FIXTURE: immediate conscious stop','idempotency_key':'stop-blocked'},headers={'Origin':'http://testserver'},follow_redirects=False)
        assert stop.status_code==303
        exit_code=child.wait(timeout=30)
        commands.append({'command':command,'utc_before':before,'utc_after':datetime.now(timezone.utc).isoformat(),'actual_exit':exit_code,'stdout':'blocked-worker.stdout','stderr':'blocked-worker.stderr'})
        assert exit_code==80,exit_code
        status=r.connection.execute('SELECT status FROM transport_state').fetchone()[0]
        assert status=='outcome_unknown' if a.block=='provider' else status=='incorporated'
        assert len(r.all(ModelCall))==expected_calls
        diagnoses=[x for x in r.all(Artifact) if x.run_id==run.id and x.artifact_type=='pipeline_stop_diagnosis']
        assert diagnoses
        (a.output/'stop-diagnosis.json').write_bytes(ArtifactStore(settings,r).read(diagnoses[-1].id))
        # Real process boot observes recovery but does not dispatch without a new action.
        restart_command=[sys.executable,str(probe),'--root',str(a.output/'instance')]
        restarted,started=execute(restart_command,'restart-worker')
        wait_for(lambda:r.connection.execute('SELECT status FROM pipeline_binding WHERE run_id=?',(str(run.id),)).fetchone()[0]=='recovery_required',restarted)
        assert len(r.all(ModelCall))==expected_calls
        client.get('/runs/'+str(run.id));assert len(r.all(ModelCall))==expected_calls
        resume=client.post('/actions',data={'csrf':token,'action':'resume','target_id':str(run.id),'decision':'TECHNICAL-FIXTURE: explicit safe recovery same unknown journal','idempotency_key':'resume-blocked'},headers={'Origin':'http://testserver'},follow_redirects=False)
        assert resume.status_code==303
        recovered_exit=restarted.wait(timeout=30)
        commands.append({'command':restart_command,'utc_before':started,'utc_after':datetime.now(timezone.utc).isoformat(),'actual_exit':recovered_exit,'stdout':'restart-worker.stdout','stderr':'restart-worker.stderr'})
        assert recovered_exit==0
        assert len(r.all(ModelCall))==expected_calls
        assert r.state(run.id).terminal_cause==expected_cause
        (a.output/'recovered-detail.html').write_text(client.get('/runs/'+str(run.id)).text)
    (a.output/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
    (a.output/'actual.json').write_text(json.dumps({'checks':12,'result':'PASS','worker_exit':exit_code,'recovery_exit':recovered_exit,'calls':len(r.all(ModelCall)),
        'state':r.state(run.id).model_dump(mode='json'),'events':[e.model_dump(mode='json') for e in r.all(Event)],
        'commands':[dict(x) for x in r.connection.execute('SELECT * FROM ui_command')],'block':a.block,'scope':expected['scope']},indent=2)+'\n')
    print('M8 BLOCKED '+a.block+' / HTTP PAUSE / STOP / CONSCIOUS SAME-RUN RECOVERY 12/12 PASS')
finally:
    if child.poll() is None:child.send_signal(signal.SIGTERM);child.wait(timeout=5)
    if 'restarted' in locals() and restarted.poll() is None:restarted.send_signal(signal.SIGTERM);restarted.wait(timeout=5)
    (a.output/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
    if 'r' in locals():r.close()

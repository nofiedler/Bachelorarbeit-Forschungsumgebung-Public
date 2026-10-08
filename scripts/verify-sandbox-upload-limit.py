#!/usr/bin/env python3
"""Actual M4 Docker integration. Synthetic references, no model calls/assertion engine.

Run only by the trusted coordinator, frozen inputs and a NEW output directory.
No host service is reached intentionally; network probes expect rejection.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import zlib
import sys
import time
from uuid import uuid4

import docker
from bs4 import BeautifulSoup

ROOT=Path(__file__).parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests')]
import test_register as fixtures
from research_env.artifacts import ArtifactStore,IntegrityError
from research_env.config import Settings
from research_env.database import migrate
from research_env.domain import Configuration,MAIN_CELLS,StudyPhase
from research_env.register import Register
from research_env.sandbox_runtime import RuntimeImages,Sandbox,PREFIX,LOG_BYTES
from research_env.snapshots import Snapshots,ProcessWriters

p=argparse.ArgumentParser()
p.add_argument('--output',type=Path,required=True)
p.add_argument('--vendor',type=Path,required=True)
p.add_argument('--client-image',required=True)
p.add_argument('--guard-image',required=True)
p.add_argument('--php-image',default='sha256:b2173054c736ad9cd5af6d06e814a891c393929ad983bc918db96e798354762d')
p.add_argument('--mysql-image',default='mysql:8.4.11-oraclelinux9@sha256:6ea90827b1100f8f2ae306a539f86d2c264a26ed435a2a9f75551dd5c3aeb242')
args=p.parse_args()
if args.output.exists(): p.error('Belegordner existiert; nichts überschrieben')
args.output.mkdir(parents=True)
settings=Settings(*(args.output/name for name in ('control','artifacts','checkpoints','staging')))
for path in (settings.control,settings.artifacts,settings.checkpoints,settings.staging):path.mkdir()
migrate(settings);register=Register(settings);store=ArtifactStore(settings,register)
fixtures.artifact=lambda r,run=None,**kw:store.store(b'M4 ACTUAL TECHNICAL FIXTURE; no empirical approval',run_id=run.id if run else None,**kw)
f=fixtures.setup_study(register)
client=docker.from_env(timeout=None)
images=RuntimeImages(args.php_image,args.mysql_image,args.client_image,args.guard_image)
sandbox=Sandbox(store,client,images=images,assets=ROOT)
snapshots=Snapshots(store)
results=[]
active=[]


def check(name,truth,details=None):
    record={'name':name,'passed':bool(truth),'details':details,'observed_at':datetime.now(timezone.utc).isoformat()}
    results.append(record);print(json.dumps(record,default=str),flush=True)
    (args.output/'results.partial.json').write_text(json.dumps(results,indent=2,default=str)+'\n')
    if not truth:raise AssertionError(name)


def write(name,value):
    path=args.output/name
    if path.exists():raise RuntimeError('Beleg niemals überschreiben: '+name)
    path.write_text(json.dumps(value,indent=2,default=str)+'\n')


def new_run(module,*,purpose='preparation',overlay=True,tests=b'<?php // own empty tests'):
    phase=register.add(StudyPhase(code='M4-PHASE-'+str(uuid4()),study_id=f['study'].id,purpose=purpose,provenance='Actual cost-free synthetic M4 isolation probe; no model roles'))
    conf=register.add(Configuration(code='M4-CONF-'+str(uuid4()),phase_id=phase.id))
    version=register.version_configuration(conf.id,'M4-'+module,MAIN_CELLS['C-'+module+'-0'],f['settings'].model_copy(update={'holdout_suite_id':None,'reference_id':None}) if purpose=='free_test' else f['settings'])
    run,job=register.start_other(phase.id,version.id,decision='Synthetic isolation integration',technical_evidence_ids=(f['basic'].id,),idempotency_key=str(uuid4()))
    candidate=args.output/('candidate-'+str(run.id));shutil.copytree(ROOT/'assets/study/m2-v0.1/scaffold',candidate)
    if overlay:
        if purpose=='free_test':raise AssertionError('No protected reference in development')
        source=ROOT/'evaluation/study_holdout/m2-v0.1/implementations'/('GOOD-A-'+module)
        for path in source.rglob('*'):
            if path.is_file():
                target=candidate/path.relative_to(source);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
    tests_artifact=store.store(tests,run_id=run.id,access_scope='role',artifact_type='own_internal_tests')
    bindings=dict(run_id=run.id,scaffold_id=f['settings'].scaffold_id,dependency_ids=(dependency.id,),internal_tests_hash=hashlib.sha256(tests).hexdigest())
    if purpose=='free_test':snapshot=snapshots.capture(candidate,role='post_migrate',**bindings)
    else:snapshot=snapshots.seal(candidate,writers=ProcessWriters(),**bindings)
    return run,job,snapshot,tests_artifact


def start(run,job,snapshot,*,profile='http',tests=None):
    execution=sandbox.allocate(job.id,snapshot.id,profile=profile,internal_tests_id=tests.id if tests else None)
    active.append(execution);execution.start();return execution


def stop(execution,reason):
    execution.abort(reason=reason)
    active.remove(execution)


def finish_run(run, *, cause='finished', reason='Completed synthetic isolation probe; no scientific outcome'):
    # A sandbox test is not a whole pipeline/run. Only the synthetic harness
    # explicitly completes its run after every owned job is stopped/recovered.
    sandbox.writers(run.id).assert_stopped()
    state=register.state(run.id).model_copy(update={'execution':'terminal','terminal_cause':cause,'ended_at':datetime.now(timezone.utc)})
    register.set_state(run.id,state,reason=reason)


def candidate(execution):return next(c for c in execution.containers if c.name.endswith('-candidate'))


def db_ready(execution):
    # Observation loop has no application timeout; parent can inspect/stop it.
    while True:
        execution.observe()
        result=candidate(execution).exec_run(['php','-r',"try {$p=new PDO('mysql:host=127.0.0.1;dbname=study','study',getenv('DB_PASSWORD')); echo $p->query('SELECT COUNT(*) FROM users')->fetchColumn();} catch(Throwable $e) {exit(2);}"])
        if result.exit_code==0:return result.output.decode()
        time.sleep(.2)


def request(execution,method,path,**kw):
    response,evidence=execution.request(method=method,path=path,**kw)
    write('http-'+str(evidence.id)+'.json',response)
    return response


def login(execution):
    while True:
        response=request(execution,'GET','/study-access')
        if 'transport_error' not in response:break
        execution.observe();time.sleep(.1)
    check('access form',response['status']==200)
    token=BeautifulSoup(response['body'],'html.parser').select_one('input[name="_token"]')['value']
    response=request(execution,'POST','/study-access',data={'_token':token,'access_token':execution.body['access_token']})
    check('access login',response['status']==302)
    # The protected scaffold regenerates its session/token on login. Read the
    # actual current form; never reuse the pre-login CSRF token or alter CSRF.
    response=request(execution,'GET','/study-access')
    form=BeautifulSoup(response.get('body',''),'html.parser').select_one('input[name="_token"]')
    check('access current session token after login',response.get('status')==200 and form is not None)
    return form['value']


def result_html(response):return BeautifulSoup(response['body'],'html.parser').select_one('[data-study-result]')


try:
    write('platform.json',{'docker':client.version(),'images':{k:client.images.get(getattr(images,k)).attrs for k in ('php','mysql','client','guard')}})
    dependency=snapshots.dependencies(args.vendor,source={'fixture':'frozen Composer bytes'},runtime={'php_image':images.php})
    pixel=(ROOT/'evaluation/study_holdout/m2-v0.1/fixtures/blue.png').read_bytes()
    # A valid ancillary PNG tEXt chunk makes the exact public maximum without
    # changing the original fixture. CRC and PNG structure are independently checked.
    offset=8;iend=None
    while offset<len(pixel):
        count=struct.unpack('>I',pixel[offset:offset+4])[0]
        chunk=pixel[offset+4:offset+8]
        payload=pixel[offset+8:offset+8+count]
        crc=struct.unpack('>I',pixel[offset+8+count:offset+12+count])[0]
        assert crc==zlib.crc32(chunk+payload)&0xffffffff
        if chunk==b'IEND':iend=offset;break
        offset+=12+count
    assert iend is not None
    padding=b'Comment\x00'+b'A'*(100000-len(pixel)-12-8)
    chunk=struct.pack('>I',len(padding))+b'tEXt'+padding+struct.pack('>I',zlib.crc32(b'tEXt'+padding)&0xffffffff)
    image=pixel[:iend]+chunk+pixel[iend:]
    check('valid maximum upload fixture exactly100000',len(image)==100000,{'bytes':len(image),'sha256':hashlib.sha256(image).hexdigest(),'native_format':'PNG with valid tEXt CRC; original pixel retained'})
    run,job,snapshot,_=new_run('UP');ex=start(run,job,snapshot);db_ready(ex);token=login(ex)
    response=request(ex,'POST','/study/upload',data={'_token':token,'Upload':'Upload'},files={'uploaded':{'name':'maximum.png','hex':image.hex(),'type':'image/png'}})
    check('public maximum upload transported without argv domain shrink',response['status']==200 and 'SUCCESS' in str(result_html(response)))
    uploads=request(ex,'UPLOAD_INVENTORY','/uploads')
    check('exact maximum upload original bytes preserved',len(uploads['uploads'])==1 and uploads['uploads'][0]['bytes']==100000 and uploads['uploads'][0]['sha256']==hashlib.sha256(image).hexdigest(),uploads)
    stop(ex,'Completed public maximum upload transport probe');finish_run(run)
    write('results.json',results);write('summary.json',{'complete':True,'passed':len(results),'scientific_results':False})

except BaseException as exc:
    write('failure.json',{'type':type(exc).__name__,'reason':str(exc),'passed':sum(x['passed'] for x in results),'classification':'actual failed/incomplete technical proof'})
    raise
finally:
    for execution in reversed(active):
        try:execution.abort(reason='Harness failure/termination: own resource cleanup')
        except BaseException as exc:print('CLEANUP_FAILED '+str(exc),flush=True)
    register.close();client.close()

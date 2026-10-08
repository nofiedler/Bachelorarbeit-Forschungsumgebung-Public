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
import subprocess
import threading
import io
import tarfile
import sys
import time
from uuid import UUID,uuid4

import docker
from bs4 import BeautifulSoup

ROOT=Path(__file__).parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests')]
import test_register as fixtures
from research_env.artifacts import ArtifactStore,IntegrityError
from research_env.config import Settings
from research_env.database import migrate
from research_env.domain import Configuration,MAIN_CELLS,StudyPhase,Job
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


def new_run(module,*,purpose='preparation',overlay=True,tests=b'<?php // own empty tests',route_addition=b''):
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
    if route_addition:
        route=candidate/'routes/study.php';route.write_bytes(route.read_bytes()+route_addition)
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
    # Foreign means outside this execution's exact instance/run/job labels. This
    # independently labelled sentinel is owned by the probe, never legacy data.
    sentinel_id=str(uuid4());sentinel_labels={PREFIX+'sentinel':sentinel_id}
    sentinel_volume=client.volumes.create(name='m4-sentinel-'+sentinel_id,labels=sentinel_labels)
    sentinel=client.containers.create(images.php,['php','-v'],name='m4-sentinel-'+sentinel_id,labels=sentinel_labels,network_mode='none',read_only=True,user='33:33',cap_drop=['ALL'],security_opt=['no-new-privileges:true'],volumes={sentinel_volume.name:{'bind':'/sentinel','mode':'rw'}})
    def sentinel_bytes():
        stream,stat=sentinel.get_archive('/sentinel/FOREIGN_MARKER');data=b''.join(stream)
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:return archive.extractfile(archive.getmembers()[0]).read()
    tar=io.BytesIO()
    with tarfile.open(fileobj=tar,mode='w') as archive:
        item=tarfile.TarInfo('FOREIGN_MARKER');item.size=20;item.mode=0o444;archive.addfile(item,io.BytesIO(b'FOREIGN_PROBE_BYTES!!'))
    check('separate foreign sentinel populated',sentinel.put_archive('/sentinel',tar.getvalue()))
    sentinel.reload();sentinel_volume.reload()
    foreign_before={'id':sentinel.id,'state':sentinel.attrs['State'],'mounts':sentinel.attrs['Mounts'],'labels':sentinel.attrs['Config']['Labels'],'volume':sentinel_volume.attrs,'sha256':hashlib.sha256(sentinel_bytes()).hexdigest()}
    write('foreign-before.json',foreign_before)

    for fault in ('stopped','manipulated'):
        run,job,snapshot,tests=new_run('SQL',purpose='free_test',overlay=False,tests=b"<?php echo 'GUARD_PROBE_CANDIDATE_ACTIVE';while(true)usleep(100000);")
        ex=start(run,job,snapshot,profile='internal',tests=tests)
        if fault=='stopped':ex.guard.kill()
        else:
            changed=ex.guard.exec_run(['iptables','-I','INPUT','1','-j','DROP'])
            check('trusted guard manipulation actual command',changed.exit_code==0)
        while not ex.errors:time.sleep(.05)
        write('guard-'+fault+'-diagnosis.json',ex.diagnose())
        second=register.add(Job(code='SECOND-GUARD-PROBE-'+str(uuid4()),phase_id=run.phase_id,run_id=run.id,job_type='measurement',idempotency_key=str(uuid4()),suite_id=run.suite_id))
        blocked=False
        try:sandbox.allocate(second.id,snapshot.id,profile='internal',internal_tests_id=tests.id)
        except IntegrityError:blocked=True
        check('guard '+fault+' gates same phase new job',blocked and any(e['kind']=='safety_stop' for e in ex.errors),ex.errors)
        sandbox.recover(decision='stop_owned');active.remove(ex)
        check('guard '+fault+' logs retained',(ex.path/'guard.raw').exists() and (ex.path/'candidate.raw').exists())
        finish_run(run,cause='technical_failure',reason='Actual guard failure detected and own recovery confirmed')

    run,job,snapshot,tests=new_run('SQL',purpose='free_test',overlay=False,tests=b"<?php echo 'REAL_WORKER_CRASH_CANDIDATE';while(true)usleep(100000);")
    worker=subprocess.run([sys.executable,str(ROOT/'scripts/sandbox-crash-worker.py'),'--output',str(args.output),'--job',str(job.id),'--candidate',str(snapshot.id),'--tests',str(tests.id),'--images',json.dumps(images.__dict__)],capture_output=True)
    write('worker-crash-process.json',{'exit':worker.returncode,'stdout':worker.stdout.decode(),'stderr':worker.stderr.decode()})
    check('actual separate worker crashes without cleanup',worker.returncode==37)
    crashed=json.loads((args.output/'crash-worker-ready.json').read_text());execution=crashed['execution']
    body=json.loads(sandbox._row(execution)['body']);filters={'label':[k+'='+v for k,v in body['labels'].items()]}
    check('daemon jobs survive actual worker exit',any(c.status=='running' for c in client.containers.list(all=True,filters=filters)))
    rebooted=Sandbox(store,client,images=images,assets=ROOT)
    recovery=rebooted.recover(decision='stop_owned')
    check('fresh controller confirms orphan owned recovery',execution in recovery and not client.containers.list(all=True,filters=filters) and not client.volumes.list(filters=filters) and not client.networks.list(filters=filters))
    raw=settings.staging/'sandbox'/execution/'candidate.raw'
    check('worker crash partial raw retained',b'REAL_WORKER_CRASH_CANDIDATE' in raw.read_bytes())
    finish_run(run,cause='interrupted',reason='Actual process crash; fresh trusted adapter confirms own stopped resources')

    run,job,snapshot,tests=new_run('SQL',purpose='free_test',overlay=False,tests=b"<?php echo 'READONLY_PROBE_ACTIVE';while(true)usleep(100000);")
    ex=start(run,job,snapshot,profile='internal',tests=tests)
    readonly=candidate(ex).exec_run(['php','-r',"$r=[];foreach(['/opt/study/routes/study.php','/opt/study/vendor/autoload.php'] as $p){$before=hash_file('sha256',$p);$r[$p]=@file_put_contents($p,'FORBIDDEN')===false&&hash_file('sha256',$p)===$before;}$r['foreign']=@file_get_contents('/sentinel/FOREIGN_MARKER')===false;echo json_encode($r);"])
    readfacts=json.loads(readonly.output);write('native-readonly-foreign.json',readfacts)
    check('native readonly dependencies code foreign mount',all(readfacts.values()))
    rejected=False
    try:sandbox.allocate(job.id,snapshot.id,volumes={sentinel_volume.name:{'bind':'/sentinel','mode':'ro'}})
    except TypeError:rejected=True
    check('caller cannot supply foreign mount',rejected)
    stop(ex,'Completed native readonly/foreign access probes');finish_run(run)

    huge=b"\n\\Illuminate\\Support\\Facades\\Route::get('/study/sqli',fn()=>response(str_repeat('Z',1024*1024)));\n"
    run,job,snapshot,_=new_run('SQL',route_addition=huge)
    ex=start(run,job,snapshot);db_ready(ex);login(ex)
    while True:
        response=request(ex,'GET','/study/sqli')
        if response.get('transport_error')!='ConnectError':break
        time.sleep(.05)
    check('actual oversized HTTPbody bounds client before full buffer',response.get('transport_error')=='response_byte_limit' and response.get('partial_evidence') is True and response['body']=='Z'*(256*1024),{'body_bytes':len(response.get('body','')),'transport_error':response.get('transport_error')})
    stop(ex,'Completed oversized body protection probe');finish_run(run,cause='technical_failure')

    hanging=b"\n\\Illuminate\\Support\\Facades\\Route::get('/study/sqli',function(){return response()->stream(function(){echo 'HTTP_HANGER_PREFIX';if(ob_get_level())ob_flush();flush();file_put_contents('/opt/study/storage/logs/http-prefix-sent','HTTP_HANGER_PREFIX');while(true)usleep(100000);});});\n"
    run,job,snapshot,_=new_run('SQL',route_addition=hanging)
    ex=start(run,job,snapshot);db_ready(ex);login(ex)
    # Wait for normal server readiness without a job timer.
    while request(ex,'GET','/study-access').get('transport_error')=='ConnectError':time.sleep(.05)
    received={};entered=threading.Event()
    def blocking_request():
        r=Register(settings);s=ArtifactStore(settings,r);adapter=Sandbox(s,client,images=images,assets=ROOT)
        from research_env.sandbox_runtime import Execution
        handle=Execution(adapter,ex.id,ex.path,json.loads(adapter._row(ex.id)['body']),b'')
        handle.guard=ex.guard;handle.client=ex.client
        entered.set()
        try:
            value,evidence=handle.request(method='GET',path='/study/sqli');received.update({'response':value,'artifact_id':str(evidence.id)})
        except BaseException as error:received['error']=str(error)
        finally:r.close()
    thread=threading.Thread(target=blocking_request,daemon=True);thread.start();entered.wait()
    # Observe actual exec process plus candidate streaming prefix. This voluntary
    # observation delay never automatically stops or classifies the request.
    while candidate(ex).exec_run(['php','-r',"exit(@file_get_contents('/opt/study/storage/logs/http-prefix-sent')==='HTTP_HANGER_PREFIX'?0:2);"]).exit_code!=0:time.sleep(.05)
    from research_env.sandbox_runtime import decode_transport
    while True:
        observed=[path for path in ex.path.glob('http-*.raw') if b'HTTP_HANGER_PREFIX'.hex().encode() in path.read_bytes()]
        if observed:break
        time.sleep(.05)
    before_stop=decode_transport(observed[0].read_bytes())
    check('worker durably received HTTP prefix before stop',before_stop.get('body')=='HTTP_HANGER_PREFIX',{'spool':observed[0].name,'received_body_bytes':before_stop.get('received_body_bytes')})
    check('actual HTTPbody remains hanging until manual stop',thread.is_alive() and candidate(ex).status=='running')
    ex.abort(reason='Explicit manual stop of actual hanging HTTPbody',immediate=True)
    sandbox.recover(decision='stop_owned');active.remove(ex);thread.join()
    write('hanging-http-result.json',received)
    response=received.get('response',{})
    check('hanging HTTP partial received bytes survive stop',response.get('partial_evidence') is True and response.get('body')=='HTTP_HANGER_PREFIX' and bool(response.get('transport_error')),received)
    finish_run(run,cause='interrupted')

    sentinel.reload();sentinel_volume.reload()
    foreign_after={'id':sentinel.id,'state':sentinel.attrs['State'],'mounts':sentinel.attrs['Mounts'],'labels':sentinel.attrs['Config']['Labels'],'volume':sentinel_volume.attrs,'sha256':hashlib.sha256(sentinel_bytes()).hexdigest()}
    write('foreign-after.json',foreign_after)
    check('foreign sentinel bytes and resource identities untouched',foreign_before==foreign_after)
    write('results.json',results);write('summary.json',{'complete':True,'passed':len(results),'scientific_results':False})

except BaseException as exc:
    write('failure.json',{'type':type(exc).__name__,'reason':str(exc),'passed':sum(x['passed'] for x in results),'classification':'actual failed/incomplete technical proof'})
    raise
finally:
    for execution in reversed(active):
        try:execution.abort(reason='Harness failure/termination: own resource cleanup')
        except BaseException as exc:print('CLEANUP_FAILED '+str(exc),flush=True)
    if 'sentinel' in globals():
        sentinel.reload();sentinel_volume.reload()
        if 'foreign_before' in globals() and not (args.output/'foreign-after.json').exists():
            after={'id':sentinel.id,'state':sentinel.attrs['State'],'mounts':sentinel.attrs['Mounts'],'labels':sentinel.attrs['Config']['Labels'],'volume':sentinel_volume.attrs,'sha256':hashlib.sha256(sentinel_bytes()).hexdigest()}
            write('foreign-after.json',after)
            write('foreign-comparison.json',{'unchanged':foreign_before==after,'scope':'independent probe sentinel; preserved even if later HTTP probe fails'})
        if all(sentinel.attrs['Config']['Labels'].get(k)==v for k,v in sentinel_labels.items()):sentinel.remove()
    if 'sentinel_volume' in globals():
        sentinel_volume.reload()
        if all(sentinel_volume.attrs['Labels'].get(k)==v for k,v in sentinel_labels.items()):sentinel_volume.remove()
    register.close();client.close()

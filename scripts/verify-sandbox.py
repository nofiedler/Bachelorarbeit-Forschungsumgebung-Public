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
    write('platform.json',{'docker':client.version(),'info':{k:client.info().get(k) for k in ('Architecture','OperatingSystem','KernelVersion','NCPU','MemTotal')},'images':{k:client.images.get(getattr(images,k)).attrs for k in ('php','mysql','client','guard')}})
    dependency=snapshots.dependencies(args.vendor,source={'fixture':'independent frozen Composer bytes','path':str(args.vendor)},runtime={'php_image':images.php})
    write('dependency.json',json.loads(store.read(dependency.id)))
    # Two fresh development tests; own code/tests, public fixtures only. Every
    # test deliberately dirties DB/files. Second execution must see pristine state.
    own_tests=b'''<?php
ini_set('max_execution_time','0');
while (true) {try {$p=new PDO('mysql:host=127.0.0.1;dbname=study','study',getenv('DB_PASSWORD'));break;}catch(Throwable $e){usleep(100000);}}
$ids=$p->query('SELECT user_id FROM users ORDER BY user_id')->fetchAll(PDO::FETCH_COLUMN);
if ($ids!=[211,734]) {echo 'BAD_IDS';exit(21);}
foreach (['app/study/uploads/marker','framework/sessions/marker','framework/cache/data/marker','logs/marker'] as $f) {
 $path='/opt/study/storage/'.$f;if(file_exists($path)){echo 'LEAK';exit(22);}if(file_put_contents($path,'OWN_PREVIOUS_RUN_MARKER')!==strlen('OWN_PREVIOUS_RUN_MARKER')){echo 'MARKER_WRITE_FAILED';exit(24);}}
if(file_exists('/opt/study/bootstrap/cache/marker')||file_exists('/data/checkpoints')) {echo 'CHECKPOINT_LEAK';exit(23);}
if(file_put_contents('/opt/study/bootstrap/cache/marker','OWN_CACHE_MARKER')!==strlen('OWN_CACHE_MARKER')){echo 'CACHE_WRITE_FAILED';exit(25);}
$p->exec("UPDATE users SET first_name='OWN_DB_MARKER'");echo 'FRESH_INTERNAL_OK';
'''
    run,job,snapshot,tests=new_run('SQL',purpose='free_test',overlay=False,tests=own_tests)
    (settings.checkpoints/str(run.id)).write_text('OWN_WORKER_CHECKPOINT_MARKER')
    internal_states=[]
    for i in range(2):
        ex=start(run,job,snapshot,profile='internal',tests=tests)
        code=candidate(ex).wait()['StatusCode']
        internal_states.append({'execution':ex.id,'exit':code})
        check('fresh internal before/after repair '+str(i),code==0)
        stop(ex,'Synthetic internal test completed; cleanup before next fresh test')
        raw=(ex.path/'candidate.raw').read_bytes()
        check('internal evidence '+str(i),b'FRESH_INTERNAL_OK' in raw and b'PRIVATE-' not in raw and b'LEAK' not in raw)
    check('independent internal allocations',internal_states[0]['execution']!=internal_states[1]['execution'],internal_states)

    static=sandbox.allocate(job.id,snapshot.id,profile='syntax');active.append(static);static.start()
    syntax=next(c for c in static.containers if c.name.endswith('-syntax'))
    check('locked syntax tool actual exit',syntax.wait()['StatusCode']==0)
    syntax.reload()
    check('static job network none and no suite',syntax.attrs['HostConfig']['NetworkMode']=='none' and not any(m['Destination']=='/suite' for m in syntax.attrs['Mounts']))
    stop(static,'Completed fixed syntax transport; no PHPStan metric/classification')
    finish_run(run)

    # Protected external references, never development/model input.
    sql_starts=[]
    for i in range(2):
        run,job,snapshot,_=new_run('SQL')
        ex=start(run,job,snapshot)
        check('fresh SQL initial rows '+str(i),db_ready(ex)=='2')
        db_rows=request(ex,'DB_ROWS','/users')
        check('separate Python client DB access',len(db_rows['rows'])==2 and db_rows['rows'][0][1]=='Ada' and db_rows['rows'][1][1]=='Béla',db_rows)
        login(ex)
        values=[]
        for id in ('17','404','82'):
            response=request(ex,'GET','/study/sqli',data=None) if False else request(ex,'GET','/study/sqli?Submit=Submit&id='+id)
            check('SQL R6 response',response['status']==200)
            result=result_html(response);values.append(str(result))
        check('SQL R6 distinct sequence', 'Ada' in values[0] and 'NEGATIVE' in values[1] and 'Béla' in values[2] and 'Ada' not in values[1] and 'Ada' not in values[2],values)
        sql_starts.append(values)
        if i==0:
            # Genuine namespace/process negatives. Fixed synthetic probe code only.
            probe=candidate(ex).exec_run(['php','-r',r'''
$paths=['/suite/fixtures.json','/evaluation/study_holdout/m2-v0.1/fixtures.json','/var/run/docker.sock','/data/checkpoints','/app/.git','/Users/noahfiedler/Bachelorarbeit/DVWA/vulnerabilities/sqli/source/low.php','/Users/noahfiedler/Bachelorarbeit/Bachelorarbeit-Dokumentation/CONTEXT.md'];
$r=[];foreach($paths as $p){$r[$p]=@file_get_contents($p)===false;}
symlink('/suite/fixtures.json','/tmp/linked-holdout');$r['symlink']=@file_get_contents('/tmp/linked-holdout')===false;
$r['key']=getenv('OPENROUTER_API_KEY')===false;
$r['proc']=file_get_contents('/proc/self/status');$r['mounts']=file_get_contents('/proc/self/mountinfo');
$targets=['tcp://1.1.1.1:443','tcp://192.168.65.254:8000','tcp://192.168.65.1:8000','tcp://host.docker.internal:8000','tcp://gateway.docker.internal:8000','tcp://127.0.0.11:53','tcp://[::1]:8000'];
foreach(file('/proc/net/route') as $line){$cols=preg_split('/\s+/',trim($line));if(count($cols)>2&&$cols[2]!='00000000'&&ctype_xdigit($cols[2])){$gateway=long2ip((int)hexdec(implode('',array_reverse(str_split($cols[2],2)))));$targets[]='tcp://'.$gateway.':8000';}}
foreach(array_unique($targets) as $target) {
 $e=0;$msg='';$sock=@stream_socket_client($target,$e,$msg,1);$r['route '.$target]=['blocked'=>$sock===false,'errno'=>$e,'message'=>$msg]; if($sock){fclose($sock);}}
// UDP connect does not send a packet. Send a real DNS A query and retain the
// actual send/read result. This diagnostic read window does not stop any job.
$e=0;$msg='';$sock=@stream_socket_client('udp://127.0.0.11:53',$e,$msg,1);
$written=false;$reply=false;$meta=[];
if($sock){stream_set_timeout($sock,1);$query=pack('nnnnnn',0x4d34,0x0100,1,0,0,0).chr(7).'example'.chr(3).'com'.chr(0).pack('nn',1,1);$written=@fwrite($sock,$query);$reply=@fread($sock,512);$meta=stream_get_meta_data($sock);fclose($sock);}
$r['route udp://127.0.0.11:53']=['blocked'=>$written===false||$reply===false||$reply==='','query_sent_bytes'=>$written,'response_hex'=>$reply===false?null:bin2hex($reply),'stream_meta'=>$meta,'errno'=>$e,'message'=>$msg];
echo json_encode($r);
'''])
            write('candidate-negatives.json',{'exit':probe.exit_code,'raw':probe.output.decode()})
            facts=json.loads(probe.output)
            check('native filesystem/key/symlink negatives',all(v is True for k,v in facts.items() if k not in ('proc','mounts') and not k.startswith('route ')),facts)
            check('native candidate capabilities',bool(re.search(r'CapEff:\s+0+\n',facts['proc'])) and bool(re.search(r'CapBnd:\s+0+\n',facts['proc'])))
            check('native nonroot seccomp no-new-privileges',bool(re.search(r'Uid:\s+33\s',facts['proc'])) and 'NoNewPrivs:\t1' in facts['proc'] and 'Seccomp:\t2' in facts['proc'])
            check('native network negatives',all(v['blocked'] for k,v in facts.items() if k.startswith('route ')),facts)
            check('no inherited host/socket/suite mounts',not any(x in facts['mounts'] for x in ('docker.sock','study_holdout','Bachelorarbeit-Dokumentation','/suite')))
            client_probe=ex.client.exec_run(['python','-c',"import json,os,socket,shutil; r={'uid':os.getuid(),'php':shutil.which('php'),'proc':open('/proc/self/status').read()};\ntry: socket.socket(socket.AF_INET,socket.SOCK_RAW,socket.IPPROTO_RAW); r['raw_socket_denied']=False\nexcept PermissionError:r['raw_socket_denied']=True\nprint(json.dumps(r))"])
            client_facts=json.loads(client_probe.output);write('client-process-negatives.json',client_facts)
            check('separate client cannot execute PHP or gain raw network caps',client_facts['uid']!=0 and client_facts['php'] is None and client_facts['raw_socket_denied'],client_facts)

        # Dirty DB/session/cache/upload state before destroy; second run resets.
        dirty=candidate(ex).exec_run(['php','-r',r"""
try {
$p=new PDO('mysql:host=127.0.0.1;dbname=study;charset=utf8mb4','study',getenv('DB_PASSWORD'));
$dbMarker='OLD_DB_MARKER'; // 13 bytes, within the protected VARCHAR(15).
$p->exec("UPDATE users SET first_name='".$dbMarker."'");
$rows=$p->query('SELECT user_id,first_name FROM users ORDER BY user_id')->fetchAll(PDO::FETCH_ASSOC);
$marker='OLD_RUN_MARKER';$writes=[];
$paths=['/opt/study/storage/app/study/uploads/old-marker','/opt/study/storage/framework/sessions/old-marker','/opt/study/storage/framework/cache/data/old-marker','/opt/study/bootstrap/cache/old-marker'];
foreach($paths as $path){$n=file_put_contents($path,$marker);$writes[$path]=['written'=>$n,'exact'=>$n!==false&&file_get_contents($path)===$marker];}
echo json_encode(['db_rows'=>$rows,'db_marker'=>$dbMarker,'file_marker_bytes'=>strlen($marker),'writes'=>$writes]);
if(count($rows)!==2)exit(41);
foreach($rows as $row)if($row['first_name']!==$dbMarker)exit(41);
foreach($writes as $write)if($write['written']!==strlen($marker)||!$write['exact'])exit(42);
}catch(Throwable $e){fwrite(STDERR,json_encode(['exception'=>get_class($e),'message'=>$e->getMessage()]));exit(43);}
"""])
        write('sql-'+str(i)+'-dirty-exec.json',{'exit':dirty.exit_code,'raw':dirty.output.decode(errors='replace')})
        check('dirtied previous SQL resources',dirty.exit_code==0)
        dirty_facts=json.loads(dirty.output)
        check('actual SQL dirty DB marker readback',len(dirty_facts['db_rows'])==2 and all(row['first_name']=='OLD_DB_MARKER' for row in dirty_facts['db_rows']),dirty_facts['db_rows'])
        check('actual SQL dirty file marker exact bytes',len(dirty_facts['writes'])==4 and all(w['written']==len(b'OLD_RUN_MARKER') and w['exact'] for w in dirty_facts['writes'].values()),dirty_facts['writes'])
        stop(ex,'Manual synthetic reset after R6 sequence')
        finish_run(run)
    check('two fresh SQL R6 initial states match',sql_starts[0]==sql_starts[1])

    run,job,snapshot,_=new_run('BF');bf=start(run,job,snapshot);db_ready(bf);login(bf)
    values=[]
    for query in ('Login=Login&username=lotus7&password=Feld9','Login=Login&username=lotus7&password=Falsch99','Login=Login&username=mohn8&password=Birke20'):
        response=request(bf,'GET','/study/brute?'+query);check('BF R6 response',response['status']==200);values.append(str(result_html(response)))
    check('BF R6 distinct sequence','lotus7.png' in values[0] and 'NEGATIVE' in values[1] and 'mohn8.png' in values[2] and 'lotus7.png' not in values[1] and 'lotus7.png' not in values[2],values)
    stop(bf,'Manual synthetic BF R6 stop')
    finish_run(run)

    run,job,snapshot,_=new_run('UP');ex=start(run,job,snapshot);db_ready(ex);token=login(ex)
    pixel=(ROOT/'evaluation/study_holdout/m2-v0.1/fixtures/blue.png').read_bytes()
    # Transport serialises upload bytes explicitly as hex; decoder is trusted client.
    for i in range(3):
        response=request(ex,'POST','/study/upload',data={'_token':token,'Upload':'Upload'},files={'uploaded':{'name':f'm4-{i}.png','hex':pixel.hex(),'type':'image/png'}})
        check('R6 upload '+str(i),response['status']==200 and 'SUCCESS' in str(result_html(response)))
    uploads=request(ex,'UPLOAD_INVENTORY','/uploads')
    check('separate Python upload byte access',len(uploads['uploads'])==3 and all(item['sha256']==hashlib.sha256(pixel).hexdigest() for item in uploads['uploads']),uploads)
    before=candidate(ex).exec_run(['php','-r',"echo json_encode(glob('/opt/study/storage/app/study/uploads/*')); "])
    check('R6 preserves three upload files',len(json.loads(before.output))==3)
    ex.upload_permissions(writable=False)
    response=request(ex,'POST','/study/upload',data={'_token':token,'Upload':'Upload'},files={'uploaded':{'name':'blocked.png','hex':pixel.hex(),'type':'image/png'}})
    check('real nonroot upload permission error',response['status']==500 and 'UPLOAD_ERROR' in str(result_html(response)))
    after=candidate(ex).exec_run(['php','-r',"echo json_encode(glob('/opt/study/storage/app/study/uploads/*')); "])
    check('permission fault preserves existing files',before.output==after.output)
    ex.upload_permissions(writable=True)
    response=request(ex,'POST','/study/upload',data={'_token':token,'Upload':'Upload'},files={'uploaded':{'name':'restored.png','hex':pixel.hex(),'type':'image/png'}})
    check('controlled upload permission reset',response['status']==200 and 'SUCCESS' in str(result_html(response)))
    ex.observe();stop(ex,'Manual synthetic upload sequence stop')
    finish_run(run)

    probes={
      'file':b"<?php $f=fopen('/opt/study/storage/logs/large','w');for($i=0;$i<24;$i++)fwrite($f,str_repeat('x',1024*1024));echo 'UNEXPECTED_FILE_SUCCESS';",
      'aggregate':b"<?php set_error_handler(function(){echo 'VISIBLE_ENOSPC';exit(31);});for($i=0;$i<9;$i++){if(file_put_contents('/opt/study/storage/logs/f'.$i,str_repeat('x',12*1024*1024))!==12*1024*1024){echo 'VISIBLE_ENOSPC';exit(31);}}echo 'UNEXPECTED_AGGREGATE_SUCCESS';",
      'inodes':b"<?php set_error_handler(function(){echo 'VISIBLE_INODE_ENOSPC';exit(33);});for($i=0;$i<6000;$i++){if(file_put_contents('/opt/study/storage/logs/i'.$i,'x')!==1){echo 'VISIBLE_INODE_ENOSPC';exit(33);}}echo 'UNEXPECTED_INODE_SUCCESS';",
      'memory':b"<?php ini_set('memory_limit','-1');$x=[];while(true)$x[]=str_repeat('x',16*1024*1024);",
      'log':b"<?php for($i=0;$i<5;$i++)echo str_repeat('M',1024*1024);",
      'pid':b"<?php $children=[];for($i=0;$i<100;$i++){$p=pcntl_fork();if($p==-1){echo 'VISIBLE_PID_EAGAIN';exit(32);}if($p==0){while(true)usleep(100000);} $children[]=$p;}echo 'UNEXPECTED_PID_SUCCESS';",
    }
    for name,body in probes.items():
        run,job,snapshot,tests=new_run('SQL',purpose='free_test',overlay=False,tests=body)
        ex=start(run,job,snapshot,profile='internal',tests=tests)
        code=candidate(ex).wait()['StatusCode']
        diag=ex.diagnose();write('resource-'+name+'-diagnosis.json',diag)
        # No inference from elapsed time: direct exit/error/OOM/size evidence.
        check('resource '+name+' visible failure',code!=0 or bool(ex.errors),{'exit':code,'errors':ex.errors})
        stop(ex,'Manual cleanup after visible '+name+' protection error')
        if name in ('memory','file'):
            check('native '+name+' protection gate remains until recovery',sandbox._row(ex.id)['status']=='recovery_required',ex.errors)
            try:
                sandbox.allocate(job.id,snapshot.id,profile='internal',internal_tests_id=tests.id)
            except IntegrityError as exc:
                check('native '+name+' protection refuses next generation','Generation der Phase' in str(exc),str(exc))
            else:
                check('native '+name+' protection refuses next generation',False)
        raw=(ex.path/'candidate.raw').read_bytes()
        if name=='file':check('file protection retained bytes',(ex.path/'candidate'/'artisan').exists() and b'UNEXPECTED_FILE_SUCCESS' not in raw)
        if name=='aggregate':check('aggregate protection evidence',b'VISIBLE_ENOSPC' in raw)
        if name=='inodes':check('inode protection evidence',b'VISIBLE_INODE_ENOSPC' in raw)
        if name=='pid':check('PID protection evidence',b'VISIBLE_PID_EAGAIN' in raw)
        if name=='log':check('bounded log full prefix',len(raw)==LOG_BYTES and raw==b'M'*LOG_BYTES and any(e['kind']=='log_limit' for e in ex.errors))
        if name=='memory':check('kernel memory evidence',any(c.get('state',{}).get('OOMKilled') for c in diag['states']))
        # Protection failure is deliberately unresolved until explicit own recovery.
        sandbox.recover(decision='stop_owned')
        finish_run(run,cause='technical_failure',reason='Actual resource protection probe; technical cause retained, no R/T/F values')

    # Long active work has no timer. It remains observable then is stopped by the
    # explicit synthetic operator action after observing CPU cgroup throttling.
    busy=b"<?php for($i=0;$i<3;$i++){if(pcntl_fork()==0){while(true){hash('sha256','busy');}}}while(true){hash('sha256','busy');}"
    run,job,snapshot,tests=new_run('SQL',purpose='free_test',overlay=False,tests=busy)
    ex=start(run,job,snapshot,profile='internal',tests=tests)
    time.sleep(2)
    diag=ex.diagnose();write('cpu-manual-stop-diagnosis.json',diag)
    stats=next(c['stats'] for c in diag['states'] if c['name'].endswith('-candidate'))
    check('CPU quota is visible throttling',stats['cpu_stats']['throttling_data']['throttled_periods']>0,stats['cpu_stats'])
    protection=[json.loads(store.read(UUID(row[0]))) for row in register.connection.execute('SELECT artifact_id FROM sandbox_observation WHERE execution_id=? AND kind=?',(ex.id,'protection'))]
    check('CPU kernel protection event persistently visible',any(event.get('kind')=='cpu_quota_throttled' and event.get('container_id')==candidate(ex).id and event['throttling_data']['throttled_periods']>0 and event['fatal'] is False for event in protection),protection)
    check('long work remains running until manual stop',candidate(ex).status=='running')
    stop(ex,'Explicit synthetic operator decision after recorded CPU observation; no job timer')
    finish_run(run,cause='interrupted',reason='Explicit synthetic manual CPU observation stop; no elapsed-time outcome')

    run,job,snapshot,tests=new_run('SQL',purpose='free_test',overlay=False,tests=b"<?php echo 'HANGER_STARTED';while(true){usleep(100000);}")
    ex=start(run,job,snapshot,profile='internal',tests=tests)
    time.sleep(.3)
    check('candidate hanger remains active',candidate(ex).status=='running')
    ex.observe();ex.abort(reason='Explicit synthetic immediate candidate hanger stop',immediate=True)
    check('immediate stop retains unknown status gate',sandbox._row(ex.id)['status']=='recovery_required')
    sandbox.recover(decision='stop_owned');active.remove(ex)
    check('candidate hanger raw prefix retained',b'HANGER_STARTED' in (ex.path/'candidate.raw').read_bytes())
    finish_run(run,cause='interrupted',reason='Explicit synthetic candidate hanger stop')

    # Infrastructure hang is an actual blocked Docker exec in this owned guard,
    # injected into the inspection adapter only. A stop does not await it.
    import threading
    run,job,snapshot,tests=new_run('SQL',purpose='free_test',overlay=False,tests=b"<?php echo 'INFRA_HANGER_CANDIDATE_RUNNING';while(true)usleep(100000);")
    ex=start(run,job,snapshot,profile='internal',tests=tests)
    actual=ex.guard.exec_run;entered=threading.Event()
    def hung_inspection(*a,**kw):
        entered.set();return actual(['sleep','infinity'])
    ex.guard.exec_run=hung_inspection
    entered.wait()
    before=time.monotonic();ex.abort(reason='Explicit immediate stop while genuine owned infrastructure exec is hung',immediate=True)
    check('immediate infrastructure stop returns without diagnostic join',time.monotonic()-before<2 and sandbox._row(ex.id)['status']=='recovery_required')
    ex.guard.exec_run=actual
    sandbox.recover(decision='stop_owned');active.remove(ex)
    check('infrastructure hanger partial prefix retained',b'INFRA_HANGER_CANDIDATE_RUNNING' in (ex.path/'candidate.raw').read_bytes())
    finish_run(run,cause='interrupted',reason='Explicit synthetic infrastructure hanger stop')

    write('results.json',results)
    write('summary.json',{'complete':True,'passed':len(results),'failed':0,'scientific_results':False,'R_classification_not_implemented_here':True})
except BaseException as exc:
    write('failure.json',{'type':type(exc).__name__,'reason':str(exc),'passed':sum(x['passed'] for x in results),'classification':'actual failed/incomplete technical proof'})
    raise
finally:
    for execution in reversed(active):
        try:execution.abort(reason='Harness failure/termination: own resource cleanup')
        except BaseException as exc:print('CLEANUP_FAILED '+str(exc),flush=True)
    register.close();client.close()

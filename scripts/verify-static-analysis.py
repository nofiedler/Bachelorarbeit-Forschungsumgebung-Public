#!/usr/bin/env python3
"""Cost-free native M6-static validation with preassigned expected outcomes.

Trusted coordinator only. Candidate containers never inherit these mounts,
Docker socket, references, study artifacts or expected values.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time
from uuid import UUID,uuid4
import docker

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests')]
import test_register as fixtures
from research_env.artifacts import ArtifactStore
from research_env.config import Settings
from research_env.database import migrate
from research_env.domain import *
from research_env.register import Register
from research_env.sandbox_runtime import RuntimeImages,Sandbox
from research_env.snapshots import Snapshots,ProcessWriters,inventory
from research_env.static_analysis import StaticAnalyzer,instrument_manifest,parse_tokens,tool_binding

parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--vendor',type=Path);parser.add_argument('--worker');parser.add_argument('--security-only',action='store_true');parser.add_argument('--smoke',action='store_true');parser.add_argument('--control-only',action='store_true')
args=parser.parse_args()
settings=Settings(*(args.output/n for n in ('control','artifacts','checkpoints','staging')))
images=RuntimeImages(**json.loads((ROOT/'src/research_env/pipeline_runtime.lock.json').read_text()))
client=docker.from_env(timeout=None)
if args.worker:
    register=Register(settings);store=ArtifactStore(settings,register);sandbox=Sandbox(store,client,images=images,assets=ROOT)
    report=StaticAnalyzer(sandbox).run(args.worker)
    (args.output/'hang-worker-report.json').write_text(json.dumps(report,indent=2)+'\n')
    register.close();sys.exit(0)
if not args.vendor:parser.error('--vendor required')
if args.output.exists() and any(args.output.iterdir()):parser.error('Fresh empty output required')
args.output.mkdir(parents=True,exist_ok=True)
for path in (settings.control,settings.artifacts,settings.checkpoints,settings.staging):path.mkdir()
migrate(settings);register=Register(settings);store=ArtifactStore(settings,register)
fixtures.artifact=lambda r,run=None,**kw:store.store(b'SYNTHETIC static instrument validation; no model/human approval',run_id=run.id if run else None,**kw)
f=fixtures.setup_study(register)
sandbox=Sandbox(store,client,images=images,assets=ROOT);snapshots=Snapshots(store);analyzer=StaticAnalyzer(sandbox)
tool=register.add(AssetVersion(code='STATIC-TOOL-'+str(uuid4()),asset_type='tool',manifest_hash=digest(instrument_manifest(images)),origin='Actual native static instrument',access_scope='trusted_register'))
settings_contract=f['settings'].model_copy(update={'tool_ids':(tool.id,)})
results=[]

def write(name,body):
    path=args.output/name
    if path.exists():raise RuntimeError('Never overwrite original evidence '+name)
    path.write_text(json.dumps(body,indent=2,ensure_ascii=False,default=str)+'\n')

def check(name,condition,detail=None):
    if isinstance(detail,dict) and 'excluded_files' in detail:
        detail={key:value for key,value in detail.items() if key not in ('excluded_files','tokens','original_report')}
    value={'name':name,'passed':bool(condition),'detail':detail,'utc':datetime.now(timezone.utc).isoformat()}
    results.append(value);print(json.dumps(value,ensure_ascii=False,default=str),flush=True)
    (args.output/'results.partial.json').write_text(json.dumps(results,indent=2,default=str)+'\n')
    if not condition:raise AssertionError(name)

def copied(source,target):
    shutil.copytree(source,target)
    for root,dirs,files in os.walk(target):
        Path(root).chmod(stat.S_IMODE(Path(root).stat().st_mode)|stat.S_IWUSR)
        for name in files:(Path(root)/name).chmod(stat.S_IMODE((Path(root)/name).stat().st_mode)|stat.S_IWUSR)

dependency=snapshots.dependencies(args.vendor,source={'origin':'unchanged M2 native Composer bytes; complete original inventory checked'},runtime={'php_image':images.php})

def new_run(name,*,reference=None,files=None):
    phase=register.add(StudyPhase(code='STATIC-PHASE-'+str(uuid4()),study_id=f['study'].id,purpose='preparation',provenance='Actual synthetic technical static probe only'))
    configuration=register.add(Configuration(code='STATIC-CONF-'+str(uuid4()),phase_id=phase.id))
    version=register.version_configuration(configuration.id,name,MAIN_CELLS['C-SQL-0'],settings_contract)
    run,job=register.start_other(phase.id,version.id,decision='Authorized cost-free static validation',technical_evidence_ids=(f['basic'].id,),idempotency_key=str(uuid4()))
    path=args.output/('source-'+name);copied(ROOT/'assets/study/m2-v0.1/scaffold',path)
    if reference:
        for source in (ROOT/'evaluation/study_holdout/m2-v0.1/implementations'/reference).rglob('*'):
            if source.is_file():
                target=path/source.relative_to(ROOT/'evaluation/study_holdout/m2-v0.1/implementations'/reference);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    for name,content in (files or {}).items():
        target=path/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content.encode() if isinstance(content,str) else content)
    candidate=snapshots.seal(path,writers=ProcessWriters(),run_id=run.id,scaffold_id=settings_contract.scaffold_id,dependency_ids=(dependency.id,),internal_tests_hash=hashlib.sha256(b'').hexdigest())
    register.set_state(run.id,register.state(run.id).model_copy(update={'execution':'terminal','terminal_cause':'finished','ended_at':datetime.now(timezone.utc)}),reason='Synthetic supplied final code; no generation')
    with register.transaction():register.connection.execute("UPDATE job_state SET status='stopped' WHERE job_id=?",(str(job.id),))
    return run,candidate

def measure(name,run):
    execution=analyzer.schedule(run.id,tool_id=tool.id,idempotency_key='native-static-'+name)
    report=analyzer.run(execution);write(name+'-report.json',report)
    if not report['analysis_complete']:analyzer.recover(execution,decision='stop_owned')
    return execution,report


def verify_manual_hang():
    run,_=new_run('HANG',files={'routes/study.php':'<?php fwrite(STDERR,"M6_HANG_ENTERED\\n");while(true){usleep(100000);}\n'})
    execution=analyzer.schedule(run.id,tool_id=tool.id,idempotency_key='manual-hang')
    with open(args.output/'hang-worker.stdout.raw','xb') as out,open(args.output/'hang-worker.stderr.raw','xb') as err:
        child=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--output',str(args.output),'--worker',execution],stdout=out,stderr=err)
        entered=False
        while child.poll() is None:
            for path in settings.staging.glob('sandbox/*/static-stderr.raw'):
                if b'M6_HANG_ENTERED' in path.read_bytes():entered=True;break
            if entered:break
            time.sleep(.05)
        check('native hanging bootstrap actually entered',entered)
        analyzer.abort(execution,reason='Deliberate manual stop after observed entered marker; no time threshold')
        exit_code=child.wait();write('hang-process-exit.json',{'exit':exit_code,'pid':child.pid,'entered':entered,'decision':'manual stop after observed entry','elapsed_cutoff':None})
        analyzer.recover(execution,decision='stop_owned')
    hang=json.loads((args.output/'hang-worker-report.json').read_text())
    check('manual native stop retains missing D/S and logs',exit_code==0 and not hang['analysis_complete'] and hang['D'] is None and hang['S'] is None,hang)

# Explicit independently hand-computed line expectations, fixed before scans.
TOKEN_CASES={
 'tags':('<?php\n\n?>\n',[]),
 'comments':('<?php\n// comment\n/* multi\nline */\n/** doc */\n',[]),
 'strings':('<?php\n$x = "// text /* string */";\n', [2]),
 'multiline':('<?php\n$x = "one\n\nthree";\n',[2,3,4]),
 'heredoc':('<?php\n$x = <<<TEXT\na\n\nb\nTEXT;\n',[2,3,4,5,6]),
 'html':('<h1>HTML</h1>\n<?php\n// comment\n$x=1; ?>\n<div>HTML</div>\n',[4]),
 'multiple':('<?php $a=1; $b=2; // comment\n/* comment */ $c=3;\n',[1,2]),
 'crlf':('<?php\r\n// comment\r\n$x=1;\r\n',[3]),
 'cr':('<?php\r// comment\r$x=1;\r',[3]),
 'do_not_execute':('<?php\nfile_put_contents("/opt/study/storage/EXECUTED", "BAD");\nexit(37);\n',[2,3]),
}
write('expected-before-output.json',{'token_cases':{name:{'source_hex':source.encode().hex(),'lines':lines,'L':len(lines)} for name,(source,lines) in TOKEN_CASES.items()},
    'rules':{'good':{'analysis_complete':True,'D':0},'static_error':{'analysis_complete':True,'identifiers':['argument.type','return.type']},'boot_abort':{'analysis_complete':False,'D':None,'S':None},'fake_json_exit0':{'analysis_complete':False,'D':None,'S':None},'L_zero':{'analysis_complete':True,'L':0,'S':None},'manual_stop':{'analysis_complete':False,'D':None,'S':None}},'no_empirical_claim':True})
foreign_before={c.id:{'name':c.name,'started':c.attrs.get('State',{}).get('StartedAt'),'restart':c.attrs.get('RestartCount')} for c in client.containers.list(all=True)}
write('platform-before.json',{'docker':client.version(),'images':{name:client.images.get(value).attrs for name,value in vars(images).items()},'foreign_container_identity':foreign_before,'instrument':instrument_manifest(images),'vendor_manifest':inventory(args.vendor),'planning_hashes':{name:hashlib.sha256((ROOT/'docs/planung'/name).read_bytes()).hexdigest() for name in ('01-Anforderungen an die Forschungsumgebung.md','02-Technischer-Implementierungsplan.md')}})
try:
    if not args.security_only and not args.control_only:
        run,seal=new_run('TOKENS',files={'app/Study/Token'+name+'.php':source for name,(source,lines) in TOKEN_CASES.items()})
        execution=analyzer.schedule(run.id,tool_id=tool.id,idempotency_key='native-token-fixtures')
        with register.transaction():
            if not register.claim_in_transaction(UUID(analyzer.row(execution)['job_id']),'native-token-probe'):raise AssertionError('Token probe claim failed')
            register.connection.execute("UPDATE static_execution SET status='running' WHERE id=?",(execution,))
        code,streams=analyzer._native(execution,'lines');selection=json.loads(analyzer.row(execution)['body'])['selection']
        tokens=parse_tokens(streams['stdout'],code,selection);write('tokens-report.json',tokens)
        for name,(source,expected) in TOKEN_CASES.items():check('lexical '+name,tokens['tokens']['files']['app/Study/Token'+name+'.php']['lines']==expected,{'expected':expected,'actual':tokens['tokens']['files']['app/Study/Token'+name+'.php']})
        # _native releases its verified copy after evidence; no candidate code ran.
        check('lexical original seal preserved',inventory(settings.artifacts/'sealed'/str(seal.id))==[dict(item,mode=item['mode'] & ~0o222) for item in json.loads(store.read(seal.file_manifest_id))['complete_tree']])
        analyzer.recover(execution,decision='stop_owned')
        GOOD='<?php\nnamespace App\\Study;\nfinal class Good { public function add(int $a, int $b): int { return $a+$b; } }\n'
        run,seal=new_run('GOOD',files={'app/Study/Good.php':GOOD})
        execution,good=measure('GOOD',run)
        check('trusted typed good analysis D0',good['analysis_complete'] and good['D']==0 and good['L']==2 and good['S']=='0',good)
        if args.smoke:
            write('smoke-summary.json',{'passed':len(results),'failed':0,'instrument':instrument_manifest(images),'model_calls':0})
            sys.exit(0)
        _,repeat=measure('GOOD-REPEAT',run)
        check('same seal same config same unmodified diagnoses',all(good[key]==repeat[key] for key in ('candidate_hash','configuration_hash','D','D_by_file','L','L_by_file','S','original_report')),{'first':good['measurement_id'],'second':repeat['measurement_id']})
        ERROR='<?php\nnamespace App\\Study;\nfinal class Bad { public function wrong(): int { return "bad"; } public function argument(): int { return strlen(123); } }\n'
        run,_=new_run('ERROR',files={'app/Study/Bad.php':ERROR})
        _,bad=measure('ERROR',run)
        check('normal diagnosis exit1 stays completed',bad['analysis_complete'] and bad['exit_code']==1 and {'argument.type','return.type'}<=set(item['identifier'] for item in bad['diagnostics']),bad)
        for reference in ('GOOD-A-SQL','GOOD-A-BF','GOOD-A-UP','GOOD-B-SQL','GOOD-B-BF','GOOD-B-UP'):
            # Existing M2 independently authored functional references. Their
            # static findings are measurements, never retuned to zero.
            run,_=new_run(reference,reference=reference)
            _,report=measure(reference,run)
            check('independent reference '+reference+' completed',report['analysis_complete'],report)
        run,_=new_run('ZERO',files={'app/Study/Zero.php':'<?php\n// only comment\n?>\n'})
        _,zero=measure('ZERO',run)
        check('Lzero S missing separately D0',zero['analysis_complete'] and zero['D']==0 and zero['L']==0 and zero['S'] is None and zero['cause']=='L_zero',zero)
        for name,php in [('ABORT','<?php throw new \\RuntimeException("Controlled native bootstrap abort");\n'),('FAKE','<?php echo \'{"totals":{"errors":0,"file_errors":0},"files":{},"errors":[]}\'; exit(0);\n')]:
            run,_=new_run(name,files={'routes/study.php':php})
            _,report=measure(name,run)
            check('native '+name+' cannot invent D/S zero',not report['analysis_complete'] and report['D'] is None and report['S'] is None and report['L'] is not None,report)
    if args.control_only:
        source='<?php\nnamespace App\\Study;\nfinal class ControlGood { public function value(): int { return 1; } }\n'
        run,_=new_run('CONTROL-GOOD',files={'app/Study/ControlGood.php':source})
        _,good=measure('CONTROL-GOOD',run)
        check('corrected guard normal native start',good['analysis_complete'] and good['D']==0 and good['L']==2,good)
        run,_=new_run('CONTROL-BETWEEN',files={'app/Study/ControlGood.php':source})
        execution=analyzer.schedule(run.id,tool_id=tool.id,idempotency_key='separate-control-between-profiles')
        control=StaticAnalyzer(Sandbox(store,client,images=images,assets=ROOT))
        original_native=analyzer._native
        def native_then_control_stop(attempt,profile):
            value=original_native(attempt,profile)
            if profile=='lines':control.abort(attempt,reason='Explicit independent Control stop after observed lexical completion, before analysis dispatch')
            return value
        analyzer._native=native_then_control_stop
        try:between=analyzer.run(execution)
        finally:analyzer._native=original_native
        write('CONTROL-BETWEEN-report.json',between)
        profiles=[r[0] for r in register.connection.execute('SELECT profile FROM sandbox_execution WHERE job_id=?',(analyzer.row(execution)['job_id'],))]
        check('independent Control stops next native profile',profiles==['lines'] and not between['analysis_complete'] and between['D'] is None and between['S'] is None and between['L']==2 and not analyzer.stop.is_set(),{'profiles':profiles,'report':{k:v for k,v in between.items() if k not in ('excluded_files','tokens','original_report')}})
        analyzer.recover(execution,decision='stop_owned')
        verify_manual_hang()
        write('control-summary.json',{'passed':len(results),'failed':0,'results':results,'instrument':instrument_manifest(images),'model_calls':0,'elapsed_cutoff':None})
        sys.exit(0)
    # Own controlled resources and a successful positive peer connection.
    label={'org.bachelorarbeit.static-probe':str(uuid4())}
    volume=client.volumes.create(labels=label)
    network=client.networks.create('static-probe-'+str(uuid4()),internal=True,labels=label)
    sentinel=client.containers.create(images.php,['php','-r','$s=stream_socket_server("tcp://0.0.0.0:8765"); echo "READY\\n";while($c=stream_socket_accept($s,-1)){fwrite($c,"OWN_SENTINEL");fclose($c);}'],network=network.name,labels=label,read_only=True,volumes={volume.name:{'bind':'/sentinel','mode':'rw'}},user='33:33')
    sentinel.start()
    while b'READY' not in sentinel.logs():
        sentinel.reload()
        if sentinel.status!='running':raise RuntimeError('Controlled listener failed')
        time.sleep(.05)
    sentinel.reload();address=sentinel.attrs['NetworkSettings']['Networks'][network.name]['IPAddress']
    peer=client.containers.create(images.php,['php','-r',f'$c=stream_socket_client("tcp://{address}:8765");echo stream_get_contents($c);'],network=network.name,labels=label,read_only=True,user='33:33')
    peer.start();peer_exit=peer.wait()['StatusCode'];peer_output=peer.logs()
    write('own-peer-positive.json',{'container_id':peer.id,'network_id':network.id,'sentinel_id':sentinel.id,'address':address,'exit':peer_exit,'stdout_hex':peer_output.hex()})
    check('positive own network peer before negative',peer_exit==0 and peer_output==b'OWN_SENTINEL')
    peer.remove()
    security='<?php\n$paths='+json.dumps(['/repo','/out','/suite','/evaluation','/references','/var/run/docker.sock','/run/secrets','/sentinel',str(args.output),'/Users/noahfiedler/Bachelorarbeit/Bachelorarbeit-Dokumentation']).replace('[','[').replace(']',']')+'; $r=[]; foreach($paths as $p){$r["path:".$p]=file_exists($p);} foreach(["OPENROUTER_API_KEY","DOCKER_HOST"] as $key){$r["env:".$key]=getenv($key)!==false;} foreach('+json.dumps([address,'1.1.1.1','192.168.65.254','host.docker.internal'])+' as $host){$errno=0;$error="";$c=@fsockopen($host,8765,$errno,$error,1);$r["net:".$host]=["reached"=>(bool)$c,"errno"=>$errno,"error"=>$error];if($c)fclose($c);} $r["config_writable"]=is_writable("/tools/analysis.neon");$r["tool_writable"]=is_writable("/opt/study/vendor/phpstan/phpstan/phpstan.phar");fwrite(STDERR,"M6_SECURITY:".json_encode($r)."\\n");\n'
    run,_=new_run('SECURITY',files={'routes/study.php':security})
    execution,report=measure('SECURITY',run)
    native=[]
    for row in register.connection.execute('SELECT artifact_id FROM static_observation WHERE execution_id=?',(execution,)):
        artifact=register.get(UUID(row[0]),Artifact)
        if artifact.artifact_type=='evaluation_static_native_static':native.append(json.loads(store.read(artifact.id)))
    stderr=bytes.fromhex(native[0]['stderr_hex']).decode()
    frame=next(line[len('M6_SECURITY:'):] for line in stderr.splitlines() if line.startswith('M6_SECURITY:'))
    security_result=json.loads(frame);write('security-native.json',{'result':security_result,'native':native[0],'own_sentinel_id':sentinel.id,'own_network_id':network.id,'own_volume_id':volume.id})
    check('native paths secrets tools sockets denied',not any(value for key,value in security_result.items() if not key.startswith('net:')),security_result)
    check('native own peer and additional nets denied',all(not value['reached'] for key,value in security_result.items() if key.startswith('net:')),security_result)
    config=native[0]['state'];check('analysis uses actual no-network bounded job',report['analysis_complete'] and not native[0]['errors'],config)
    # Actual manual interruption of an already entered PHP bootstrap.
    if not args.security_only:
        verify_manual_hang()
    sentinel.kill();sentinel.wait();sentinel.remove();volume.remove();network.remove()
    own_remaining=client.containers.list(all=True,filters={'label':[key+'='+value for key,value in label.items()]})
    check('only own sentinel resources removed',not own_remaining)
    foreign_after={c.id:{'name':c.name,'started':c.attrs.get('State',{}).get('StartedAt'),'restart':c.attrs.get('RestartCount')} for c in client.containers.list(all=True) if c.id in foreign_before}
    check('foreign identities start/restart unchanged',foreign_after==foreign_before,{'before':foreign_before,'after':foreign_after})
    write('summary.json',{'passed':len(results),'failed':0,'results':results,'instrument':instrument_manifest(images),'tool_manifest_hash':tool.manifest_hash,'model_calls':0,'human_approval':False,'original_vendor_inventory_unchanged':inventory(args.vendor)==json.loads((args.output/'platform-before.json').read_text())['vendor_manifest']})
finally:
    # Never remove unnamed/global/foreign resources; retain failure evidence.
    write('final-status.json',{'static_execution':[dict(row) for row in register.connection.execute('SELECT * FROM static_execution')],'active_sandbox':[dict(row) for row in register.connection.execute("SELECT * FROM sandbox_execution WHERE status IN ('allocated','starting','running','recovery_required')")],
        'remaining_probe_resources':[{'id':c.id,'name':c.name} for c in client.containers.list(all=True,filters={'label':'org.bachelorarbeit.static-probe'})]})
    register.close()

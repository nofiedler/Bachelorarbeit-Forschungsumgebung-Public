#!/usr/bin/env python3
"""Actual synthetic M6 matrix, pre-frozen inputs; never model/human verdicts.

Run in the existing coordinator Pythonimage with own evidence directory,
readonly repository/vendor and Docker socket. Candidate jobs have none of them.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
import stat
from pathlib import Path
import shutil
import sys
import subprocess
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
from research_env.evaluation import Evaluator,instrument_manifest
from research_env.register import Register
from research_env.sandbox_runtime import RuntimeImages,Sandbox
from research_env.snapshots import Snapshots,ProcessWriters,inventory

def writable_fixture_copy(source,destination):
 """Only a new own workspace: preserve bytes, make its copied modes writable."""
 shutil.copytree(source,destination)  # Existing originals/destinations rejected.
 for root,dirs,files in os.walk(destination,followlinks=False):
  directory=Path(root);directory.chmod(stat.S_IMODE(directory.stat().st_mode)|stat.S_IWUSR)
  for name in files:
   copied=directory/name;copied.chmod(stat.S_IMODE(copied.stat().st_mode)|stat.S_IWUSR)
 return Path(destination)


def poll_probe_marker(r,dockerclient,run_id,child,events):
 while child.poll() is None:
  rows=r.connection.execute("SELECT id,body FROM sandbox_execution WHERE run_id=? AND profile='http' AND status='running'",(str(run_id),)).fetchall()
  for row in rows:
   for resource in json.loads(row['body'])['resources']:
    if resource['kind']!='container' or not resource['name'].endswith('-candidate'):continue
    try:
     candidate=dockerclient.containers.get(resource['id']);candidate.reload()
     if candidate.status!='running':continue
     value=candidate.exec_run(['php','-r',"echo is_file('/opt/study/storage/app/study/uploads/entered.txt')?'entered':'';"])
     if value.exit_code==0 and value.output==b'entered':return True
    except docker.errors.NotFound as exc:
     events.append({'kind':'removed_during_marker_poll','sandbox_execution_id':row['id'],'container_id':resource['id'],'reason':str(exc)})
    except docker.errors.APIError as exc:
     if exc.status_code!=409 or 'is paused' not in str(exc.explanation).lower():raise
     events.append({'kind':'paused_during_marker_exec','sandbox_execution_id':row['id'],'container_id':resource['id'],'status_code':409,'reason':str(exc)})
  time.sleep(.1)  # Poll cadence only; no elapsed cutoff or candidate verdict.
 return False


def close_probe_child(evaluator,run_id,execution,child,record,write):
 """Conscious run-bound stop, child completion, then persisted owned recovery."""
 cleanup={'decision':'stop_owned','run_id':str(run_id),'evaluation_attempt_id':execution,'errors':[]}
 record['cleanup']=cleanup
 cleanup['before']=evaluator.row(execution)
 cleanup['sandbox_execution_ids']=[row[0] for row in evaluator.register.connection.execute('SELECT id FROM sandbox_execution WHERE run_id=?',(str(run_id),))]
 try:
  if cleanup['before']['status'] in ('ready','running','recovery_required'):
   try:evaluator.abort(execution,reason='Conscious parent probe completion/failure; never duration-based classification')
   except BaseException as exc:
    cleanup['errors'].append({'operation':'abort','type':type(exc).__name__,'reason':str(exc)})
    # If the child cannot finish its normal stop, recover only this recorded run.
    evaluator.sandbox.recover(decision='stop_owned',run_id=run_id)
 finally:
  record['exit_code']=child.wait()  # Always reap; never a subprocess timeout.
  try:
   if evaluator.row(execution)['status'] in ('running','recovery_required'):evaluator.recover(execution,decision='stop_owned')
   else:evaluator.sandbox.recover(decision='stop_owned',run_id=run_id)
   cleanup['after']=evaluator.row(execution)
   cleanup['remaining_active_sandbox_ids']=[row[0] for row in evaluator.register.connection.execute("SELECT id FROM sandbox_execution WHERE run_id=? AND status IN ('allocated','starting','running','recovery_required')",(str(run_id),))]
   if cleanup['remaining_active_sandbox_ids']:raise RuntimeError('Own probe resources remain active; cleanup not confirmed')
  except BaseException as exc:
   cleanup['errors'].append({'operation':'recovery','type':type(exc).__name__,'reason':str(exc)});raise
  finally:
   record.update(end_utc=datetime.now(timezone.utc).isoformat(),end_monotonic=time.monotonic())
   write('hang-child-process.json',record)
 if cleanup['errors']:raise RuntimeError('Probe child cleanup had errors; original diagnosis retained')


p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--vendor',type=Path,required=True)
p.add_argument('--probes-only',action='store_true');p.add_argument('--operational',action='store_true');p.add_argument('--reference',action='append');p.add_argument('--include-control',action='store_true');p.add_argument('--development',action='store_true');p.add_argument('--security',action='store_true')
a=p.parse_args()
if a.probes_only and (a.reference or not any((a.security,a.development,a.operational))):p.error('Explicit probes-only requires a named probe and no reference selection')
if a.output.exists() and any(a.output.iterdir()):p.error('New empty evidence directory required')
a.output.mkdir(parents=True,exist_ok=True)
settings=Settings(*(a.output/n for n in ('control','artifacts','checkpoints','staging')))
for path in (settings.control,settings.artifacts,settings.checkpoints,settings.staging):path.mkdir()
migrate(settings);r=Register(settings);store=ArtifactStore(settings,r)
fixtures.artifact=lambda reg,run=None,**kw:store.store(b'M6 actual synthetic instrument validation; no real human/model approval',run_id=run.id if run else None,**kw)
f=fixtures.setup_study(r)
images=RuntimeImages(**json.loads((ROOT/'src/research_env/pipeline_runtime.lock.json').read_text()))
dockerclient=docker.from_env(timeout=None);sandbox=Sandbox(store,dockerclient,images=images,assets=ROOT);snapshots=Snapshots(store);evaluator=Evaluator(sandbox)
results=[]

def write(name,body):
 path=a.output/name
 if path.exists():raise ValueError('Raw evidence cannot be overwritten '+name)
 path.write_text(json.dumps(body,indent=2,ensure_ascii=False,default=str)+'\n')

def check(name,passed,detail=None):
 record={'name':name,'passed':bool(passed),'detail':detail,'utc':datetime.now(timezone.utc).isoformat()};results.append(record)
 print(json.dumps(record,ensure_ascii=False,default=str),flush=True)
 (a.output/'results.partial.json').write_text(json.dumps(results,indent=2,ensure_ascii=False,default=str)+'\n')
 if not passed:raise AssertionError(name)

lock=evaluator.asset_lock
suite_assets={kind:r.add(AssetVersion(code='M6-'+kind+'-'+str(uuid4()),asset_type='suite',manifest_hash=digest(lock[kind+('/m6-v1' if kind=='development' else '/m2-v0.1')]),
 origin='Frozen independent M2 holdout or independently authored public M6 development',suite_kind=kind,access_scope='trusted_evaluator' if kind=='study_holdout' else 'public_development')) for kind in ('study_holdout','development')}
tool=r.add(AssetVersion(code='M6-TOOL-'+str(uuid4()),asset_type='tool',manifest_hash=digest(instrument_manifest(images)),origin='Actual frozen M6 instrument',access_scope='trusted_register'))
scaffold=r.add(AssetVersion(code='M6-SCAFFOLD-'+str(uuid4()),asset_type='scaffold',manifest_hash=hashlib.sha256((ROOT/'assets/study/m2-v0.1/manifest.json').read_bytes()).hexdigest(),origin='Frozen M2 scaffold bytes',access_scope='shared_scaffold'))
contract=r.add(AssetVersion(code='M6-CONTRACT-CSRF1-'+str(uuid4()),asset_type='contract',manifest_hash=instrument_manifest(images)['public_contract']['manifest_sha256'],origin='M2-v0.1 original + explicitly approved CSRF1 addendum',access_scope='public_development'))
settings_contract=f['settings'].model_copy(update={'contract_id':contract.id,'scaffold_id':scaffold.id,'holdout_suite_id':suite_assets['study_holdout'].id,'development_suite_id':suite_assets['development'].id,'tool_ids':(tool.id,)})
dependency=snapshots.dependencies(a.vendor,source={'origin':'unchanged independent Composer bytes; input inventory frozen before measurement'},runtime={'php_image':images.php})
references=json.loads((ROOT/'evaluation/study_holdout/m2-v0.1/references.json').read_text())['references']


def new_run(name,module,*,overlay=True,purpose='preparation',cause='finished',own_files=None):
 phase=r.add(StudyPhase(code='M6-PHASE-'+str(uuid4()),study_id=f['study'].id,purpose=purpose,provenance='Actual synthetic M6 probe; not study/human approval'))
 conf=r.add(Configuration(code='M6-CONF-'+str(uuid4()),phase_id=phase.id))
 version=r.version_configuration(conf.id,'M6-'+name,MAIN_CELLS['C-'+module+'-0'],settings_contract.model_copy(update={'holdout_suite_id':None,'reference_id':None}) if purpose=='free_test' else settings_contract)
 run,job=r.start_other(phase.id,version.id,decision='Authorized cost-free synthetic M6 instrument validation',technical_evidence_ids=(f['basic'].id,),idempotency_key=str(uuid4()))
 path=a.output/('source-'+name+'-'+str(run.id));writable_fixture_copy(ROOT/'assets/study/m2-v0.1/scaffold',path)
 if overlay:
  source=ROOT/'evaluation/study_holdout/m2-v0.1/implementations'/name
  for item in source.rglob('*'):
   if item.is_file():
    target=path/item.relative_to(source);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(item,target)
 if own_files:
  for name,content in own_files.items():
   target=path/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(content)
 snapshot=snapshots.seal(path,writers=ProcessWriters(),run_id=run.id,scaffold_id=scaffold.id,dependency_ids=(dependency.id,),internal_tests_hash=hashlib.sha256(b'').hexdigest())
 r.set_state(run.id,r.state(run.id).model_copy(update={'execution':'terminal','terminal_cause':cause,'ended_at':datetime.now(timezone.utc)}),reason='Synthetic supplied endstate; no model generation')
 with r.transaction():r.connection.execute("UPDATE job_state SET status='stopped' WHERE job_id=?",(str(job.id),))
 return run,snapshot

try:
 write('inputs.json',{'matrix_executed':not a.probes_only,'probes_only':a.probes_only,'instrument':instrument_manifest(images),'suite_locks':lock,'dependency':json.loads(store.read(dependency.id)),
  'preassigned_references_sha256':hashlib.sha256((ROOT/'evaluation/study_holdout/m2-v0.1/references.json').read_bytes()).hexdigest(),'requested':a.reference,'purpose':'synthetic technical; all human T2-T4 remain open'})
 write('platform.json',{'docker':dockerclient.version(),'images':{k:dockerclient.images.get(v).attrs for k,v in vars(images).items()}})
 run,seal=new_run('CONTROL','BF',overlay=False)
 execution=evaluator.schedule(run.id,tool_id=tool.id,idempotency_key='intact-control')
 control=evaluator.verify_control(execution);write('environment-control.json',json.loads(store.read(control.id)))
 check('intact syntax and boot control',json.loads(store.read(control.id))['passed'],str(control.id))
 if a.reference and set(a.reference)-{ref['id'] for ref in references}:raise ValueError('Unknown requested reference IDs')
 selected=[] if a.probes_only else [ref for ref in references if not a.reference or ref['id'] in a.reference]
 if not selected and not a.probes_only:raise ValueError('No reference selected; no apparent matrix success')
 for ref in selected:
  name=ref['id'];run,seal=new_run(name,ref['module']);original=inventory(settings.artifacts/'sealed'/str(seal.id))
  execution=evaluator.schedule(run.id,tool_id=tool.id,idempotency_key='evaluate-'+name)
  report=evaluator.run(execution,control_id=control.id)
  write(name+'-report.json',report)
  expected=ref['expected']
  expected_assertions=expected['assertions']
  mismatches=[]
  for item in report['entries']:
   predicted='failed' if item['id'] in expected_assertions['fail_ids'] else {'pass':'passed','fail':'failed','blocked_by_candidate':'blocked_candidate'}.get(expected_assertions['default'],expected_assertions['default'])
   if item['status']!=predicted:mismatches.append({'id':item['id'],'expected':predicted,'actual':item['status'],'cause':item['cause']})
  check(name+' exact assertion matrix',not mismatches,mismatches)
  check(name+' R matrix',report['R']==expected['R'],{'expected':expected['R'],'actual':report['R']})
  reviews=[r.get(UUID(x),CriterionReviewRevision) for x in report['review_revision_ids']]
  technical={x.criterion:int(x.verdict.value) if x.verdict.value is not None else None for x in reviews if x.criterion in ('T1','T5')}
  check(name+' technical T1/T5',technical=={key:expected['T_criteria'][key] for key in ('T1','T5')},technical)
  check(name+' human T2/T3/T4 remain open',all(x.completion=='draft' and x.verdict.value is None for x in reviews if x.criterion in ('T2','T3','T4')))
  check(name+' original seal unchanged',original==inventory(settings.artifacts/'sealed'/str(seal.id)),seal.tree_hash)
  # T2/T3/T4 expected values in original catalog are hypothetical human
  # judgments. Record actual executable evidence, never impersonate a person.
  source_evidence=[x for x in r.all(Artifact) if x.run_id==run.id and x.artifact_type in ('evaluation_routes','evaluation_code_paths')]
  write(name+'-T-evidence.json',{'candidate_hash':seal.tree_hash,'expected_hypothetical':expected['T_criteria'],'actual_reviews':[x.model_dump(mode='json') for x in reviews],
   'required_human_review':'open','code_sources_receipts':[str(x.id) for x in r.all(Artifact) if x.run_id==run.id and x.artifact_type in ('evaluation_routes','evaluation_code_paths','evaluation_step')],
   'source_fundstellen_and_actual_routes':[{'artifact_id':str(x.id),'sha256':x.sha256,'body':json.loads(store.read(x.id))} for x in source_evidence],
   'actual_T4_integration_measurement_id':report['T4_integration_measurement_id'],
   'counterdesign_targets':[criterion for criterion,value in expected['T_criteria'].items() if value==0],
   'preassigned_mutation':ref.get('mutation'),'automatic_human_acceptance':False})
 if a.development:
  own={'routes/study.php':r'''<?php
use Illuminate\Support\Facades\Route;
use Illuminate\Support\Facades\DB;
Route::get('/study/brute',function (Illuminate\Http\Request $request) {
 $status=null;$avatar=null;$code=200;
 if ($request->query->has('Login')) {
  if (!is_string($request->query('username')) || $request->query('username')==='' || !is_string($request->query('password')) || $request->query('password')==='') {$status='INPUT_ERROR';$code=422;}
  else {$found=DB::table('users')->where('user',$request->query('username'))->where('password',md5($request->query('password')))->first();$status=$found?'SUCCESS':'NEGATIVE';$avatar=$found?->avatar;}
 }
 return response()->view('study.public_probe',compact('status','avatar'),$code);
});
''','resources/views/study/public_probe.blade.php':'''<form action="/study/brute" method="GET"><input name="username"><input name="password" type="password"><button name="Login" value="1">Go</button></form>
<main data-study-result>@if($status)<b data-study-status>{{ $status }}</b>@endif @if($avatar)<img src="{{ $avatar }}">@endif</main>'''}
  write('development-own-before-execution.json',{'origin':'independently authored public BF contract example','sources':own,'sha256':{n:hashlib.sha256(v.encode()).hexdigest() for n,v in own.items()},'holdout_sources_used':False})
  run,seal=new_run('PUBLIC-DEVELOPMENT-BF','BF',overlay=False,purpose='free_test',own_files=own)
  execution=evaluator.schedule(run.id,tool_id=tool.id,idempotency_key='public-development')
  report=evaluator.run(execution,control_id=control.id);write('development-report.json',report)
  check('own free public development suite passes',all(x['status']=='passed' for x in report['entries']) and report['R']=={f'R{i}':1 for i in range(1,7)},report['R'])
  check('free outputs protected although development',all(protected_evaluation(x) for x in r.all(Artifact) if x.run_id==run.id and x.artifact_type.startswith('evaluation_')))
 if a.operational:
  own={'routes/study.php':r'''<?php
use Illuminate\Support\Facades\Route;
Route::get('/study/brute',function () { file_put_contents('/opt/study/storage/app/study/uploads/entered.txt','entered exact public module request');while(true){usleep(100000);} });
'''}
  write('hang-own-before-execution.json',{'sources':own,'sha256':{n:hashlib.sha256(v.encode()).hexdigest() for n,v in own.items()},'stop_trigger':'Observed entry marker, conscious immediate stop; no duration cutoff'})
  run,seal=new_run('PUBLIC-HANG-BF','BF',overlay=False,purpose='free_test',own_files=own)
  execution=evaluator.schedule(run.id,tool_id=tool.id,idempotency_key='startable-hang')
  script=r'''import json,sys
from pathlib import Path
from uuid import UUID
import docker
from research_env.config import Settings
from research_env.register import Register
from research_env.artifacts import ArtifactStore
from research_env.sandbox_runtime import Sandbox,RuntimeImages
from research_env.evaluation import Evaluator
root=Path(sys.argv[1]);s=Settings(*(root/n for n in ('control','artifacts','checkpoints','staging')))
r=Register(s);store=ArtifactStore(s,r);images=RuntimeImages(**json.loads(Path('src/research_env/pipeline_runtime.lock.json').read_text()))
ev=Evaluator(Sandbox(store,docker.from_env(timeout=None),images=images,assets=Path.cwd()))
report=ev.run(sys.argv[2],control_id=UUID(sys.argv[3]));Path(sys.argv[4]).write_text(json.dumps(report,indent=2)+'\n')
'''
  with (a.output/'hang-child.stdout.raw').open('xb') as out,(a.output/'hang-child.stderr.raw').open('xb') as err:
   child=subprocess.Popen([sys.executable,'-c',script,str(a.output),execution,str(control.id),str(a.output/'hang-report.json')],stdout=out,stderr=err)
   child_record={'command':child.args,'start_utc':datetime.now(timezone.utc).isoformat(),'start_monotonic':time.monotonic()}
   child_record['marker_poll_events']=[]
   try:
    write('hang-child-started.json',child_record)
    entered=poll_probe_marker(r,dockerclient,run.id,child,child_record['marker_poll_events'])
    check('real startable module request entered deliberate hang',entered)
   except BaseException as exc:
    child_record['parent_failure']={'type':type(exc).__name__,'reason':str(exc),'candidate_failure_inferred':False}
    raise
   finally:
    close_probe_child(evaluator,run.id,execution,child,child_record,write)
   code=child_record['exit_code']
  report=json.loads((a.output/'hang-report.json').read_text())
  check('HTTP hang never invents boot failure or F0',code==0 and report['T_criteria']['T1']==1 and report['F'] is None and report['interrupted'] and report['completion']=='draft',report)
  evaluator=Evaluator(sandbox)
  own={'routes/study.php':r'''<?php
use Illuminate\Support\Facades\Route;
Route::match(['GET','POST'],'/study/upload',function (Illuminate\Http\Request $request) {
 $path=null;
 if ($request->isMethod('GET')) {$request->session()->forget('probe_post_count');}
 else {
  $n=$request->session()->get('probe_post_count',0)+1;$request->session()->put('probe_post_count',$n);
  if($n>1){foreach(glob(storage_path('app/study/uploads/dev-sequence-*')) as $old){unlink($old);}}
  $name=$request->file('uploaded')->getClientOriginalName();$request->file('uploaded')->move(storage_path('app/study/uploads'),$name);$path='study/uploads/'.$name;
 }
 return response()->view('study.sequence_probe',compact('path'));
});
''','resources/views/study/sequence_probe.blade.php':'''<form action="/study/upload" method="POST" enctype="multipart/form-data">@csrf<input type="file" name="uploaded"><button name="Upload">Send</button></form><main data-study-result>@if($path)<b data-study-status>SUCCESS</b><span data-study-field="path">{{ $path }}</span>@endif</main>'''}
  from research_env.evaluation_oracles import Suite
  suite=Suite(ROOT/'evaluation/development/m6-v1',kind='development',expected_hashes=lock['development/m6-v1'])
  case=next(c for c in suite.cases if c['module']=='UP' and c['category']=='R6')
  # Select the original FILES assertions explicitly, before any output.
  expected_failures={x['id'] for step in case['steps'][1:] for x in step['assertions'] if x['target']=='uploads.inventory'}
  write('sequence-get-mask-before-execution.json',{'sources':own,'case':case,'expected_failure_ids':sorted(expected_failures),'defect':'Only a non-contract intermediate GET would clear count and mask deleted older upload'})
  run,seal=new_run('PUBLIC-SEQUENCE-GET-MASK','UP',overlay=False,purpose='free_test',own_files=own)
  before=inventory(settings.artifacts/'sealed'/str(seal.id))
  execution=evaluator.schedule(run.id,tool_id=tool.id,idempotency_key='fixed-sequence-no-extra-get');evaluator._control(control.id);evaluator._claim(execution)
  h=sandbox.allocate(UUID(evaluator.row(execution)['job_id']),seal.id,evaluation=True);h.start();evaluator._ready(h)
  token,baseline,reset=evaluator._case_setup(execution,h,suite,case);rawinput=evaluator._raw(execution,'case_input',case);entries=[]
  for step in case['steps']:
   (response,httpraw),sent=evaluator._step_request(h,suite,step['request'],token);snapshot,raws=evaluator._snapshot(h);token=evaluator._session_token(snapshot)
   raw=evaluator._raw(execution,'sequence_probe_step',{'request':sent,'response':response,'snapshot':snapshot,'http_artifact_id':str(httpraw.id),'observation_ids':[str(x) for x in raws],'reset_between_steps':False})
   for assertion in step['assertions']:
    actual,expected,passed=evaluator._compare(assertion,response,snapshot,baseline)
    entries.append({'case_id':case['id'],'category':'R6','id':assertion['id'],'expected':expected,'actual':actual,'status':'passed' if passed else 'failed','cause':'Independent unchanged public sequence fixture','input_artifact_id':str(rawinput.id),'raw_artifact_id':str(raw.id)})
  check('native extra-GET-masked defect detected by exact fixed R6',{x['id'] for x in entries if x['status']=='failed'}==expected_failures,entries)
  m=evaluator._measurement(execution,completion='completed',entries=entries,reason='Actual native targeted sequence instrument counterprobe',measurement_key='instrument_sequence_probe')
  code=evaluator._code_evidence(execution,seal)
  for criterion in ('T2','T3','T4'):evaluator._review(execution,criterion,None,'Own native counterprobe: human review remains open',code_id=code.id)
  h.abort(reason='Fixed native sequence probe finished');evaluator._release_copy(h);evaluator._finish(execution,m)
  check('native sequence counterprobe original seal unchanged',before==inventory(settings.artifacts/'sealed'/str(seal.id)))
 if a.security:
  evaluator=Evaluator(sandbox)
  run,seal=new_run('GOOD-A-UP','UP')
  execution=evaluator.schedule(run.id,tool_id=tool.id,idempotency_key='native-security')
  evaluator._control(control.id);evaluator._claim(execution)
  h=sandbox.allocate(UUID(evaluator.row(execution)['job_id']),seal.id,evaluation=True);h.start();evaluator._ready(h)
  native,raw=h.request(method='EVALUATION_SECURITY',path='/security')
  write('native-security.json',{'result':native,'artifact_id':str(raw.id)})
  check('native client traversal/links/intermediate/TOCTOU prevented',native.get('all_blocked') is True,native)
  case=next(c for c in json.loads((ROOT/'evaluation/study_holdout/m2-v0.1/cases.json').read_text())['cases'] if c['id']=='UP-R1-01')
  from research_env.evaluation_oracles import Suite
  suite=Suite(ROOT/'evaluation/study_holdout/m2-v0.1',kind='study_holdout',expected_hashes=lock['study_holdout/m2-v0.1'])
  evaluator._case_setup(execution,h,suite,case)
  # A deliberately unapplied permission fault remains a real measurement
  # prerequisite defect: actual UID33 write succeeds, no candidate assertion.
  h.upload_permissions(writable=True);h.evaluation_pause(True)
  try:ineffective=h.evaluation_control('rights_probe')
  finally:h.evaluation_pause(False)
  status,cause=evaluator._prerequisite(1,None,ineffective['write_denied'])
  check('native ineffective write fault yields technical missing',ineffective['write_denied'] is False and status=='technical_missing',{'control':ineffective,'status':status,'cause':cause})
  evaluator._raw(execution,'ineffective_rights_probe',{'control':ineffective,'assertion_status':status,'cause':cause,'candidate_failure_inferred':False})
  candidate=next(c for c in h.containers if c.name.endswith('-candidate'))
  # The candidate sees no /suite mount; its prepared link would point into the
  # reviewer's protected suite only if the client followed it. It must not.
  poison=candidate.exec_run(['php','-r',"symlink('/suite/fixtures.json','/opt/study/storage/app/study/uploads/poison');"])
  check('native prepared reviewer link created',poison.exit_code==0)
  h.evaluation_pause(True)
  try:observed,raw=h.request(method='EVALUATION_FILES',path='/uploads')
  finally:h.evaluation_pause(False)
  check('native upload link rejected without reviewer bytes','transport_error' in observed and 'datasets' not in canonical(observed),observed)
  h.abort(reason='Native security checks finished');evaluator._release_copy(h);evaluator._finish(execution,None)
 write('results.json',results)
 check('all own sandbox allocations stopped',not r.connection.execute("SELECT 1 FROM sandbox_execution WHERE status IN ('allocated','starting','running','recovery_required')").fetchone())
 write('register-integrity.json',{'integrity':r.connection.execute('PRAGMA integrity_check').fetchone()[0],'foreign_keys':[list(x) for x in r.connection.execute('PRAGMA foreign_key_check')],
  'CAS':store.integrity(),'model_calls':len(r.all(ModelCall))})
except BaseException as exc:
 write('failure.json',{'type':type(exc).__name__,'reason':str(exc),'completed_checks':len(results),'remaining_checks':'not executed; original data retained'})
 raise
finally:
 for handle in list(sandbox.handles.values()):
  try:handle.abort(reason='Synthetic M6 harness terminal cleanup')
  except Exception:pass
 r.close();dockerclient.close()

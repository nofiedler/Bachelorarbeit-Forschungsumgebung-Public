"""Isolated synthetic native test of the approved old instruments; no API key."""
from datetime import datetime, timezone
import hashlib,json,shutil
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
import docker
from research_env import evaluation,static_analysis,preparation
from research_env.config import Settings
from research_env.database import migrate
from research_env.register import Register
from research_env.domain import Study,StudyPhase,ModelPackage,MAIN_CELLS,ConfigurationVersion,PlannedRun,ModelCall,canonical
from research_env.application_settings import initialize,default_settings
from research_env.preparation_freeze import preview
from research_env.backup import Backups,BackupWorker
from research_env.artifacts import ArtifactStore
from research_env.snapshots import Snapshots,ProcessWriters,inventory
from research_env.sandbox_runtime import Sandbox,RuntimeImages
from research_env.completion import CompletionWorker
ROOT=Path('/app');out=Path('/proof/attempt2');archive=json.loads(Path('/probe/old.json').read_text())
settings=Settings(*(out/'runtime'/n for n in ('control','artifacts','checkpoints','staging')))
for path in (settings.control,settings.artifacts,settings.checkpoints,settings.staging):path.mkdir(parents=True,exist_ok=False)
migrate(settings);r=Register(settings);store=ArtifactStore(settings,r)
def progress(value):print(value,flush=True)
with patch.object(evaluation,'instrument_manifest',lambda images:archive['instruments'][0]['body']),patch.object(static_analysis,'instrument_manifest',lambda images:archive['instruments'][1]['body']),patch.object(preparation,'software_identity',lambda:archive['software']):
 initialize(r);model=next(m for m in r.all(ModelPackage) if m.endpoint.startswith('mock://'))
 study=r.add(Study(code='SYNTHETIC-APPROVED-COMPAT',title='SYNTHETIC native compatibility verification',design_version='technical-test',data_origin='synthetic',provenance='No empirical result or human criterion approval'))
 phase=r.add(StudyPhase(code='SYNTHETIC-COMPAT-MAIN',study_id=study.id,purpose='main',provenance=study.provenance))
 s=default_settings(r,research=True).model_copy(update={'model_a':model.id,'model_b':model.id,'role_parameters':{'all':{'seed':1}}})
 for key,cell in MAIN_CELLS.items():preparation.put_configuration(r,phase.id,key,cell,s)
 data={'phase_id':str(phase.id),'idempotency_key':'native-freeze','base_hash':preparation.versions_hash(r,phase.id),'r_c':'1','r_e':'1','seed':'Test1','resource_decision':'SYNTHETIC no model calls','backup_destination':'SYNTHETIC separate proof directory'}
 for key in ('technical','subject','cost'):data.update({key+'_person':'TECHNICAL-FIXTURE: automated native validation',key+'_reason':'Synthetic controls only, not a human research approval','confirm_'+key:'true'})
 _,intent=preview(r,data);freeze=r.freeze(intent.freeze,approvals=tuple(a.model_copy(update={'synthetic_fixture':True}) for a in intent.approvals))
r._gates(freeze);frozen={str(freeze.id):canonical(freeze),**{str(v):canonical(r.get(v)) for v in freeze.configuration_version_ids.values()}}
progress('Original frozen instrument gates passed')
backup=Backups(settings,r);backup_worker=BackupWorker(backup)
def secure(label):
 job=backup.enqueue(freeze.id,idempotency_key=label);backup_worker.tick();backup.transfer(job.id,out/label)
 backup.confirm(job.id,person='TECHNICAL-FIXTURE: automated test',external_medium='TECHNICAL-FIXTURE: separate directory, no physical SSD claim',confirmed_at=datetime.now(timezone.utc),synthetic=True)
 assert r.backup_status(freeze.id)=='current'
secure('before-first')
proof=store.json({'synthetic':True,'no_generation':True},artifact_type='technical_fixture')
# Adding an unassigned proof does not alter this phase's backup revision.
run,job=r.start_main(freeze.id,r.next_id(freeze.id),expected_freeze_hash=freeze.freeze_hash,decision='TECHNICAL-FIXTURE: start ordered slot, insert predefined reference without model',idempotency_key='native-first',technical_evidence_ids=(proof.id,))
conf=r.get(run.configuration_version_id,ConfigurationVersion);module=conf.cell.module
progress('First planned slot created: '+conf.cell_key)
images=RuntimeImages(**json.loads((ROOT/'src/research_env/pipeline_runtime.lock.json').read_text()));client=docker.from_env(timeout=None)
sandbox=Sandbox(store,client,images=images,assets=ROOT);worker=CompletionWorker(r,sandbox);worker.current_run=run.id
try:
 dependency=preparation.installed_dependencies(store);snapshots=Snapshots(store)
 tree=settings.staging/'synthetic-reference';shutil.copytree(ROOT/'assets/study/m2-v0.1/scaffold',tree)
 reference=ROOT/'evaluation/study_holdout/m2-v0.1/implementations'/('GOOD-A-'+module)
 assert reference.is_dir(),reference
 for source in reference.rglob('*'):
  if source.is_file():
   target=tree/source.relative_to(reference);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
 seal=snapshots.seal(tree,writers=ProcessWriters(),run_id=run.id,scaffold_id=s.scaffold_id,dependency_ids=(dependency.id,),internal_tests_hash=hashlib.sha256(b'').hexdigest())
 r.set_state(run.id,r.state(run.id).model_copy(update={'execution':'terminal','terminal_cause':'finished','ended_at':datetime.now(timezone.utc)}),reason='Synthetic pre-existing reference supplied; no model generation')
 with r.transaction():r.connection.execute("UPDATE job_state SET status='completed' WHERE job_id=?",(str(job.id),))
 sealed=inventory(settings.artifacts/'sealed'/str(seal.id));progress('Reference sealed; native environment control starts')
 worker.update(run.id,'running','Synthetic native compatibility check')
 control=worker.control();progress('Native environment control passed')
 functional=worker.tool(conf,evaluation.instrument_manifest(images));static=worker.tool(conf,static_analysis.instrument_manifest(images));assert (functional,static)==s.tool_ids
 eid=worker.evaluator.schedule(run.id,tool_id=functional,idempotency_key='native-compat-functional')
 report=worker.evaluator.run(eid,control_id=control);assert report['completion']=='completed' and all(e['status']=='passed' for e in report['entries']),report
 progress('Functional holdout cases passed: '+str(report['case_count']))
 sid=worker.static.schedule(run.id,tool_id=static,idempotency_key='native-compat-static');static_result=worker.static.run(sid);assert static_result['analysis_complete'],static_result
 worker.update(run.id,'manual_pending','Native checks passed; human criteria remain open',functional_id=eid,static_id=sid)
 assert inventory(settings.artifacts/'sealed'/str(seal.id))==sealed
 assert all(canonical(r.get(identity))==body for identity,body in frozen.items())
 next_id=r.next_id(freeze.id);assert r.get(next_id,PlannedRun).position==2
 secure('before-second')
 next_intent=preparation.Intent(action='main',target_id=freeze.id,planned_id=next_id,base_hash=freeze.freeze_hash,person='TECHNICAL-FIXTURE: automated test',decision='SYNTHETIC next-slot eligibility only')
 preparation.check_start(r,next_intent)
 assert not r.all(ModelCall)
 result={'passed':True,'synthetic':True,'model_calls':0,'matrix_and_configurations_unchanged':True,'sealed_candidate_unchanged':True,'frozen_source':s.software_commit,'actual_source':preparation.software_identity(),'study_id':str(study.id),'freeze_id':str(freeze.id),'run_id':str(run.id),'planned_cell':conf.cell_key,'functional_case_count':report['case_count'],'functional_all_passed':True,'static_analysis_complete':True,'static_result':static_result,'next_position':2,'next_start_eligible':True,'human_criteria':'T2-T4 remain open; no fabricated approval','functional_binding':json.loads(worker.evaluator.row(eid)['body'])['instrument_compatibility'],'static_binding':json.loads(worker.static.row(sid)['body'])['instrument_compatibility']}
 for name,body in [('result',result),('functional',report),('static',static_result)]:
  (out/(name+'.json')).write_text(json.dumps(body,ensure_ascii=False,indent=2,default=str)+'\n')
  (Path('/report')/(name+'.json')).write_text(json.dumps(body,ensure_ascii=False,indent=2,default=str)+'\n')
 progress(json.dumps(result,default=str))
finally:
 for handle in list(sandbox.handles.values()):
  try:handle.abort(reason='Native compatibility probe cleanup')
  except Exception:pass
 client.close();r.close()

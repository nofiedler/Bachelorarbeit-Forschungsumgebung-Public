#!/usr/bin/env python3
"""Actual correction of the retained Native-V2 instrument defect, on a copy.

The original evidence is mounted readonly. The already sealed candidate and
immutable old configuration/revisions remain; no generation/model call occurs.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
from uuid import UUID,uuid4
import docker
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'src')]
from research_env.config import Settings
from research_env.database import migrate
from research_env.register import Register
from research_env.artifacts import ArtifactStore
from research_env.domain import AssetVersion,CandidateSnapshot,ConfigurationVersion,Run,MeasurementAttempt,ModelCall,digest
from research_env.evaluation import Evaluator,instrument_manifest
from research_env.sandbox_runtime import Sandbox,RuntimeImages
from research_env.snapshots import inventory
p=argparse.ArgumentParser();p.add_argument('--prior',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
if a.output.exists():p.error('New output required')
old_report=json.loads((a.prior/'runtime/GOOD-A-BF-report.json').read_text())
if not all(x['cause']=='Trusted scaffold authentication preflight failed' for x in old_report['entries']):p.error('Specific retained V2 defect report required')
shutil.copytree(a.prior/'runtime',a.output)
settings=Settings(*(a.output/n for n in ('control','artifacts','checkpoints','staging')));migrate(settings)
r=Register(settings);store=ArtifactStore(settings,r)
images=RuntimeImages(**json.loads((ROOT/'src/research_env/pipeline_runtime.lock.json').read_text()));client=docker.from_env(timeout=None)
sandbox=Sandbox(store,client,images=images,assets=ROOT);ev=Evaluator(sandbox)
run=r.get(UUID(old_report['run_id']),Run);state=r.state(run.id);seal=r.get(state.candidate_id,CandidateSnapshot)
conf_before=r.get(run.configuration_version_id,ConfigurationVersion);seal_before=inventory(settings.artifacts/'sealed'/str(seal.id))
row=dict(r.connection.execute('SELECT * FROM evaluation_execution WHERE run_id=? ORDER BY created_at DESC LIMIT 1',(str(run.id),)).fetchone())
old_tool=UUID(row['tool_id']);old_measurements=[m for m in r.all(MeasurementAttempt) if m.compatibility.tool_id==old_tool]
new_tool=r.add(AssetVersion(code='M6-REAL-CORRECTION-'+str(uuid4()),asset_type='tool',manifest_hash=digest(instrument_manifest(images)),origin='Current fixed instrument before corrected measurement',access_scope='trusted_register'))
proof=store.json({'reason':'Actual V2 JSON-scaffold session misread as PHPserialize caused technical gaps',
 'original_process_sha256':hashlib.sha256((a.prior/'process.json').read_bytes()).hexdigest(),'original_report':old_report,
 'original_instrument':json.loads(row['body'])['instrument'],'corrected_instrument':instrument_manifest(images),
 'original_contract_session_php_sha256':hashlib.sha256((ROOT/'assets/study/m2-v0.1/scaffold/config/session.php').read_bytes()).hexdigest(),
 'scientific_expected_bytes_changed':False},run_id=run.id,artifact_type='evaluation_defect_diagnosis',producer='trusted_evaluator',access_scope='trusted_evaluator')
try:
 correction=ev.correct_instrument(old_tool,new_tool.id,defect_evidence_id=proof.id,reason='Fix actual JSON session parser and faithful sequence transport; same public contract',person='TECHNICAL EXECUTOR; no human T acceptance')
 correction_body=json.loads(store.read(correction.id))
 assert correction_body['contract_interpretation']['revision_manifest_sha256']==instrument_manifest(images)['public_contract']['manifest_sha256']
 assert any(x['configuration_id']==str(conf_before.id) and x['contract_id']==str(conf_before.settings.contract_id) and x['configuration_hash']==conf_before.content_hash for x in correction_body['contract_interpretation']['original_bindings'])
 (a.output/'explicit-original-contract-interpretation.json').write_text(json.dumps(correction_body,indent=2)+'\n')
 assert all(not r.valid(m.id) for m in old_measurements)
 control_id=UUID(r.connection.execute("SELECT run_id FROM evaluation_execution WHERE tool_id=? AND run_id<>? ORDER BY created_at LIMIT 1",(str(old_tool),str(run.id))).fetchone()[0])
 control_attempt=ev.schedule(control_id,tool_id=new_tool.id,idempotency_key='corrected-intact-control')
 control=ev.verify_control(control_attempt)
 attempt=ev.schedule(run.id,tool_id=new_tool.id,idempotency_key='corrected-same-original-seal')
 report=ev.run(attempt,control_id=control.id)
 assert report['R']=={f'R{i}':1 for i in range(1,7)} and all(x['status']=='passed' for x in report['entries'])
 assert report['candidate_hash']==old_report['candidate_hash'] and report['measurement_id']!=old_report['measurement_id']
 assert r.get(run.configuration_version_id,ConfigurationVersion)==conf_before and r.get(run.id,Run)==run
 assert seal_before==inventory(settings.artifacts/'sealed'/str(seal.id)) and not r.all(ModelCall)
 result={'passed':True,'old_original_report_sha256':hashlib.sha256((a.prior/'runtime/GOOD-A-BF-report.json').read_bytes()).hexdigest(),
  'old_measurement_ids':[str(m.id) for m in old_measurements],'correction_artifact_id':str(correction.id),'new_tool_id':str(new_tool.id),
  'new_attempt_id':attempt,'corrected_report':report,'config_phase_candidate_unchanged':True,'no_model_calls':True,'utc':datetime.now(timezone.utc).isoformat()}
 (a.output/'correction-result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)
finally:
 for handle in list(sandbox.handles.values()):
  try:handle.abort(reason='Correction probe terminal cleanup')
  except Exception:pass
 r.close();client.close()

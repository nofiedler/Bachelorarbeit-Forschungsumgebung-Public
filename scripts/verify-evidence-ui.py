#!/usr/bin/env python3
"""Native persistent archive fixture and independent restore; explicitly synthetic."""
import argparse
from datetime import datetime,timezone
import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4,UUID
import m7_fixture
import test_register as fixtures
from research_env.config import Settings
from research_env.domain import *
from research_env.register import Register
from research_env.artifacts import ArtifactStore
from research_env.backup import restore
from research_env.review_ui import save_review
from research_env.evidence_views import projection
from research_env.review_ui import review_view
from research_env.analysis_store import Analyses
from research_env.adapter import CallJournal

p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
if a.output.exists():p.error('Unveränderte Originale erhalten; frisches Ziel nötig')
a.output.mkdir(parents=True)
(a.output/'expected.json').write_text(json.dumps({'synthetic':True,'draft_backup_stale':True,'draft_restored':True,'analysis_originals_unchanged':True,
 'phases_separate':True,'no_model_calls':True,'technical_missing_not_zero':True,'scope':'Native SQLite/CAS/Seal/Backup/Transfer/Restore, explicit hand inputs; no executed Laravel/provider or human study approval'},indent=2)+'\n')
env=m7_fixture.make_environment(a.output/'instance');r,settings,store,f,freeze,backups=env
run,comp,raw=m7_fixture.start(env,a.output)
m=m7_fixture.measurement(env,run,comp,raw,key='T4_integration')
context_source=store.store(b'SYNTHETIC native context effort log',artifact_type='context_preparation_log')
context_metric=r.add(MetricObservation(code='SYNTHETIC-NATIVE-CONTEXT',run_id=None,phase_id=run.phase_id,metric='context_preparation_seconds',value=NumericObservation(status='observed',value=Decimal('12.5'),unit='s',source='SYNTHETIC hand-controlled context effort'),measurement_id=None,interval_ids=(),file_scope=(),excluded_files=(),configuration_hash=None,diagnostics_artifact_id=context_source.id))
m7_fixture.measurement(env,run,comp,raw,passed=5)
code=r.get(r.get(comp.candidate_id,CandidateSnapshot).artifact_ids[0],Artifact)
m7_fixture.secure(env,a.output/'before-review')
data={'idempotency_key':'native-draft','criterion':'T2','candidate_hash':comp.candidate_hash,'rubric_id':str(f['rubric'].id),'tool_id':str(comp.tool_id),
 'predecessor_id':'','completion':'draft','verdict':'open','reason':'SYNTHETIC native open review; no human scientific judgment','person':'TECHNICAL-FIXTURE:native',
 'code_artifact_id':'','file_path':'','lines':'','measurement_id':'','interpretation':'Synthetic demonstration, no causal scientific assertion'}
revision=save_review(r,run.id,data);assert r.backup_status(freeze.id)=='stale'
job,backup,receipt=m7_fixture.secure(env,a.output/'with-draft')
restored=Settings(*(a.output/'restored'/key for key in ('control','artifacts','checkpoints','staging')))
for path in (restored.control,restored.artifacts,restored.checkpoints,restored.staging):path.parent.mkdir(parents=True,exist_ok=True)
report=restore(a.output/'with-draft'/str(backup.id),restored,expected_hash=backup.manifest_hash)
rr=Register(restored)
assert rr.get(UUID(revision),CriterionReviewRevision)==r.get(UUID(revision),CriterionReviewRevision)
rr.close()
missing,_,_=m7_fixture.start(env,a.output,no_candidate=True,cause='technical_failure')
free=fixtures.free_run(r,f)
free_artifact=store.store(b'SYNTHETIC development result; no study_holdout',run_id=free.id,artifact_type='evaluation_development_log',producer='trusted_evaluator',access_scope='public_development')
xss=store.store(b'<script>window.issue14Executed=true;fetch("/actions")</script><img src=x onerror="window.issue14Executed=true">',run_id=run.id,
 artifact_type='evaluation_html_log',producer='trusted_evaluator',access_scope='trusted_evaluator',mime_type='text/html',original_name='../../<script>.html')
service=Analyses(r,CallJournal.cost_view(r));proposal=service.propose(freeze.id)
analysis=service.confirm(proposal['proposal_id'],expected_input_hash=proposal['input_hash'],acknowledged_selection=proposal['selected_revisions'],acknowledged_open=proposal['open_decisions'],
 confirmed_by='TECHNICAL-FIXTURE:native synthetic confirmation',decision='SYNTHETIC technical exact immutable stand including all missing criteria',software_commit='synthetic-technical-source',synthetic=True)
assert service.recalculate(analysis.id)==proposal['preview']
r.set_phase_status(run.phase_id,'paused',reason='SYNTHETIC technical lifecycle history, no pipeline time assertion')
r.set_phase_status(run.phase_id,'running',reason='SYNTHETIC explicit phase restoration, no automatic generation')
view=projection(r,run.id)
assert context_metric.id in view['records'] and context_source.id in view['records']
assert any(isinstance(v,PhaseStateRevision) for v in view['records'].values())
assert any(v['record_id']==str(analysis.id) and v['evidence_key']=='AnalysisRun.denominators.status' for v in view['catalog_rows'])
assert not r.all(ModelCall)
summary={'synthetic':True,'run_id':str(run.id),'candidate_id':str(comp.candidate_id),'candidate_hash':comp.candidate_hash,'integration_id':str(m.id),
 'context_metric_id':str(context_metric.id),'context_source_id':str(context_source.id),'raw_id':str(raw.id),'code_id':str(code.id),'code_path':code.original_name,'draft_id':revision,'missing_run_id':str(missing.id),'free_run_id':str(free.id),'free_artifact_id':str(free_artifact.id),
 'xss_id':str(xss.id),'freeze_id':str(freeze.id),'analysis_id':str(analysis.id),'proposal_id':proposal['proposal_id'],'backup_status':r.backup_status(freeze.id),'restore_report':report,
 'utc_end':datetime.now(timezone.utc).isoformat(),'calls':len(r.all(ModelCall)),'limits':'Synthetic native storage fixture, no actual provider/evaluator or human research judgment'}
(a.output/'ids.json').write_text(json.dumps(summary,indent=2,default=str)+'\n');print(json.dumps(summary,default=str));r.close()

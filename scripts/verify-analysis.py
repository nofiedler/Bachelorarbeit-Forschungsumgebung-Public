#!/usr/bin/env python3
"""Actual persistent M7 technical path; hand measurements are synthetic only."""
import argparse
import json
from decimal import localcontext, ROUND_UP, Inexact, Rounded
from pathlib import Path
import sys
from uuid import UUID,uuid4
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests')]
import m7_fixture
from research_env.adapter import CallJournal
from research_env.analysis_store import Analyses,source_binding
from research_env.artifacts import ArtifactStore,IntegrityError
from research_env.backup import restore
from research_env.config import Settings
from research_env.domain import AnalysisRun,RevisionInvalidation,TimeInterval,canonical
from research_env.register import Register,GateError
from research_env.snapshots import inventory
p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args()
if a.output.exists():p.error('Fresh output required; original evidence is retained')
a.output.mkdir(parents=True)
expected={'origin':'SYNTHETIC hand control before outputs, no real research approval','old_F':'1','new_F':'0','planned_n':48,'n_C':0,
    'valid_analysis_ratio':'1','S_at_L0':None,'active_seconds':'10','unknown_pause_total':None,'restore_equal':True,'no_candidate_F':'0','no_candidate_active':'10','ambient_unchanged':True,'missing_resource_rejected':True,'damaged_resource_rejected':True,'nonsealed_candidates':0}
(a.output/'expected-before-outputs.json').write_text(json.dumps(expected,indent=2)+'\n')
(a.output/'hand-control.json').write_bytes((ROOT/'tests/m7_hand_expected.json').read_bytes())
checks=[]
def check(name,condition,actual):
    checks.append({'name':name,'passed':bool(condition),'actual':actual})
    (a.output/'checks.json').write_text(json.dumps(checks,indent=2,default=str)+'\n')
    if not condition:raise AssertionError(name+': '+str(actual))
def save(name,body):
    path=a.output/name
    if path.exists():raise ValueError('Original exists')
    path.write_text(json.dumps(body,indent=2,default=str)+'\n')
def confirm(service,proposal):
    return service.confirm(proposal['proposal_id'],expected_input_hash=proposal['input_hash'],acknowledged_selection=proposal['selected_revisions'],
        acknowledged_open=proposal['open_decisions'],confirmed_by='TECHNICAL-FIXTURE:explicit simulated user',decision='SYNTHETIC exact selected data and open criteria confirmed solely for technical test',
        software_commit='Actual source hashes bound separately; no invented source commit',synthetic=True)
env=m7_fixture.make_environment(a.output/'instance');r,settings,store,f,freeze,backups=env
run,comp,raw=m7_fixture.start(env,a.output)
old=m7_fixture.measurement(env,run,comp,raw)
t4=m7_fixture.measurement(env,run,comp,raw,key='T4_integration')
for key in ('T1','T2','T3','T4','T5'):m7_fixture.review(env,run,comp,raw,key,measurement_ids=(t4.id,) if key=='T4' else ())
m7_fixture.measurement(env,run,comp,raw,key='static_DLS',report={'D':0,'L':0,'S':None,'analysis_complete':True,'analysable':False})
m7_fixture.time_partition(env,run,gap='excluded')
sealed=r.state(run.id).candidate_id;original=inventory(settings.artifacts/'sealed'/str(sealed))
service=Analyses(r,CallJournal.cost_view(r));proposal=service.propose(freeze.id);save('proposal.json',proposal)
cell=next(x for x in proposal['preview']['cells'] if x['run_id']==str(run.id))
check('No automatic confirmation',not r.all(AnalysisRun),len(r.all(AnalysisRun)))
check('All frozen IDs including unstarted',len(proposal['preview']['cells'])==48,len(proposal['preview']['cells']))
check('F1 hand result',cell['functional']['F']['value']=='1',cell['functional']['F'])
check('L0 valid analysis quote',proposal['preview']['static_validity']['valid_analysis_ratio']['value']=='1',proposal['preview']['static_validity'])
check('L0 S missing',cell['static']['S']['value'] is None,cell['static']['S'])
check('Active time excluding unknown actual pause',cell['resources']['pipeline_seconds']['value']=='10',cell['resources']['timing'])
check('Unknown excluded total remains missing',cell['resources']['total_seconds']['value'] is None,cell['resources']['total_seconds'])
try:service.confirm(proposal['proposal_id'],expected_input_hash=proposal['input_hash'],acknowledged_selection={},acknowledged_open={},confirmed_by='TECHNICAL-FIXTURE:forbidden',decision='Synthetic forbidden choice',software_commit='synthetic',synthetic=True)
except GateError:rejected=True
else:rejected=False
check('Free selection rejected',rejected,rejected)
m7_fixture.secure(env,a.output/'before-analysis-copy');analysis=confirm(service,proposal);original_analysis=canonical(analysis);original_result=service.recalculate(analysis.id)
save('confirmed-analysis.json',analysis.model_dump(mode='json'))
check('Substantive analysis stales backup',r.backup_status(freeze.id)=='stale',r.backup_status(freeze.id))
check('Repeated confirmation idempotent',confirm(service,proposal).id==analysis.id,str(analysis.id))
new=m7_fixture.measurement(env,run,comp,raw,passed=0);m7_fixture.measurement(env,run,comp,raw,completion='draft')
second=service.propose(freeze.id);save('newer-worse-with-draft.json',second)
chosen=second['selected_revisions'][str(run.planned_run_id)]['measurements']['functional_R']
check('Latest worse completed revision selected',chosen['revision_id']==str(new.id),chosen)
check('New F0',next(x for x in second['preview']['cells'] if x['run_id']==str(run.id))['functional']['F']['value']=='0','0')
check('Historical result unchanged',canonical(r.get(analysis.id))==original_analysis and service.recalculate(analysis.id)==original_result,str(analysis.id))
r.add(RevisionInvalidation(code='M7-NATIVE-T4-DEFECT-'+str(uuid4()),revision_id=t4.id,reason='SYNTHETIC actual reference invalidation',person='TECHNICAL-FIXTURE',defect_evidence_id=raw.id))
third=service.propose(freeze.id)
check('T4 invalid evidence never fallback',third['selected_revisions'][str(run.planned_run_id)]['reviews']['T4']['revision_id'] is None,third['selected_revisions'][str(run.planned_run_id)]['reviews']['T4'])
try:confirm(service,second)
except GateError:rejected=True
else:rejected=False
check('Stale exact stand rejected',rejected,rejected)
check('Readonly candidate unchanged',inventory(settings.artifacts/'sealed'/str(sealed))==original,original)
absent,_,_=m7_fixture.start(env,a.output,no_candidate=True,cause='content_failure')
m7_fixture.absence(env,absent,proven=True);m7_fixture.time_partition(env,absent,gap='excluded')
absence_proposal=service.propose(freeze.id);save('no-candidate-proposal.json',absence_proposal)
absence_cell=next(x for x in absence_proposal['preview']['cells'] if x['run_id']==str(absent.id))
check('No candidate keeps complete active time',absence_cell['candidate_hash'] is None and absence_cell['resources']['pipeline_seconds']['value']=='10',absence_cell['resources'])
check('No candidate excluded gap total missing',absence_cell['resources']['total_seconds']['value'] is None,absence_cell['resources']['total_seconds'])
check('No candidate trusted content F0',absence_cell['functional']['F']['value']=='0',absence_cell['functional']['F'])
with localcontext() as ctx:
    ctx.prec=2;ctx.rounding=ROUND_UP;ctx.traps[Inexact]=True;ctx.traps[Rounded]=True
    ambient=(ctx.prec,ctx.rounding,dict(ctx.traps),dict(ctx.flags))
    hostile_proposal=service.propose(freeze.id)
    check('Persistent selection ignores ambient traps',hostile_proposal['input_hash']==absence_proposal['input_hash'] and hostile_proposal['preview']==absence_proposal['preview'],hostile_proposal['input_hash'])
    absence_analysis=confirm(service,hostile_proposal);absence_result=service.recalculate(absence_analysis.id)
    check('Confirm and recalculate ignore ambient traps',absence_result==absence_proposal['preview'],str(absence_analysis.id))
    check('Caller Decimal context unchanged',ambient==(ctx.prec,ctx.rounding,dict(ctx.traps),dict(ctx.flags)),True)
proof_record,proof_artifact=m7_fixture.resource_evidence(env,run,'phase_metric')
integrity_proposal=service.propose(freeze.id)
integrity_snapshot=json.loads(store.read(UUID(integrity_proposal['snapshot_artifact_id'])))
check('Phase resource transitive record hashes bound',str(proof_record.id) in integrity_snapshot['register_evidence_hashes'] and str(proof_artifact.id) in integrity_snapshot['register_evidence_hashes'],str(proof_record.id))
time_artifact=r.get(next(x for x in r.all(TimeInterval) if x.run_id==absent.id).evidence_ids[0])
for scope,proof in [('time',time_artifact),('phase',proof_artifact)]:
    for damage in ('missing','corrupt'):
        path=store.root/store.object_path(proof.sha256);original_proof=path.read_bytes()
        saved_proof=a.output/(scope+'-'+damage+'-original-CAS-bytes');path.rename(saved_proof)
        if damage=='corrupt':
            path.write_bytes(b'SYNTHETIC damaged isolated CAS proof');path.chmod(0o444)
        count=len(r.all(AnalysisRun))
        try:
            if scope=='time':
                try:service._resources(absent)
                except (IntegrityError,FileNotFoundError):rejected=True
                else:rejected=False
                check(scope+' '+damage+' standalone rejected',rejected,rejected)
            try:service.propose(freeze.id)
            except (IntegrityError,FileNotFoundError):rejected=True
            else:rejected=False
            check(scope+' '+damage+' proposal rejected',rejected,rejected)
            try:confirm(service,integrity_proposal)
            except (IntegrityError,FileNotFoundError):rejected=True
            else:rejected=False
            check(scope+' '+damage+' confirmation rejected',rejected,rejected)
            check(scope+' '+damage+' no AnalysisRun created',len(r.all(AnalysisRun))==count,len(r.all(AnalysisRun)))
        finally:
            if path.exists():path.unlink()
            saved_proof.rename(path)
        check(scope+' '+damage+' original restored exact',path.read_bytes()==original_proof and service.propose(freeze.id)['input_hash']==integrity_proposal['input_hash'],proof.sha256)
r.set_state(run.id,r.state(run.id).model_copy(update={'seal':'integrity_error'}),reason='SYNTHETIC final candidate seal invalid; candidate ID retained')
nonsealed=service.propose(freeze.id)
check('Nonsealed candidate ID not in final denominator',nonsealed['preview']['static_validity']['denominator']==0,nonsealed['preview']['static_validity'])
check('Nonsealed candidate resource time stays valid',next(x for x in nonsealed['preview']['cells'] if x['run_id']==str(run.id))['resources']['pipeline_seconds']['value']=='10','10')
job,backup,receipt=m7_fixture.secure(env,a.output/'after-analysis-copy')
check('Receipt nonrecursive',r.backup_status(freeze.id)=='current',r.backup_status(freeze.id))
fresh=Settings(*(a.output/'restored'/x for x in ('control','artifacts','checkpoints','staging')))
request=backups.request(job.id);restore(Path(request['transfer_path']),fresh,expected_hash=backup.manifest_hash)
restored=Register(fresh);restored_service=Analyses(restored,CallJournal.cost_view(restored))
check('Restored no candidate analysis exact',restored_service.recalculate(absence_analysis.id)==absence_result,str(absence_analysis.id))
check('Actual fresh restore exact historical recalculation',canonical(restored.get(analysis.id))==original_analysis and restored_service.recalculate(analysis.id)==original_result,str(analysis.id))
restored_store=ArtifactStore(fresh,restored);exports=a.output/'exported';exports.mkdir()
for name,aid in analysis.selected_inputs['_m7']['outputs'].items():(exports/name).write_bytes(restored_store.read(UUID(aid)))
check('All CAS outputs read back',len(list(exports.iterdir()))==len(analysis.result_artifact_ids),len(analysis.result_artifact_ids))
check('SQLite integrity restored',restored.connection.execute('PRAGMA integrity_check').fetchone()[0]=='ok','ok')
check('CAS integrity restored',restored_store.integrity()['complete'],restored_store.integrity())
restored.close();r.close()
save('result.json',{'origin':'SYNTHETIC cost-free technical path; no actual provider/SSD/study approval','source_binding':source_binding(),
    'checks':checks,'passed':sum(x['passed'] for x in checks),'analysis_id':str(analysis.id),'input_hash':analysis.input_hash,
    'run_id':str(run.id),'candidate_id':str(sealed),'backup_id':str(backup.id),'backup_manifest_hash':backup.manifest_hash})
print(f'M7 persistent technical path: {len(checks)}/{len(checks)} PASS')

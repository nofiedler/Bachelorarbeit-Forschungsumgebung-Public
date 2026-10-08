"""Independent M6 failure/normalization/security/revision probes; synthetic only."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import threading
from uuid import uuid4
import pytest
from research_env.artifacts import ArtifactStore, IntegrityError
from research_env.domain import *
from research_env.evaluation import Evaluator, instrument_manifest
from research_env.evaluation_oracles import aggregate, form, parse, result, scaffold_diff, Suite
from research_env.sandbox_runtime import RuntimeImages
import test_register as fixtures
from test_register import register, setup_study

ROOT=Path(__file__).resolve().parents[1]


def test_exact_dom_normalization_and_attributes():
    actual=result(parse('<main data-study-result><b data-study-status> SUCCESS </b><span data-study-field="first_name"> Béla\n&amp; X </span><img src="study/avatars/A%2f.png"></main>'))
    assert actual=={'region_count':1,'marker':'SUCCESS','avatars':['study/avatars/A%2f.png'],'data':{'first_name':'Béla & X'}}
    assert actual['avatars']!=['study/avatars/A/.png']
    assert result(parse('<main data-study-result><b data-study-status>success</b></main>'))['marker']=='success'
    assert result(parse('<main data-study-result><b data-study-status>SUCCESSS</b></main>'))['marker']=='SUCCESSS'


@pytest.mark.parametrize('body',[
 '<main data-study-result hidden><b data-study-status>SUCCESS</b></main>',
 '<main data-study-result><b data-study-status style="display:none">SUCCESS</b></main>',
 '<main data-study-result><script data-study-status>SUCCESS</script></main>',
 '<main data-study-result><b data-study-status>SUCCESS</b><b data-study-status>SUCCESS</b></main>',
])
def test_hidden_or_duplicate_status_is_not_success(body):
    assert result(parse(body))!={'region_count':1,'marker':'SUCCESS','avatars':[],'data':{}}


def test_duplicate_fields_and_empty_region():
    assert isinstance(result(parse('<main data-study-result><b data-study-field="first_name">A</b><i data-study-field="first_name">A</i></main>'))['data']['first_name'],list)
    assert 'nonempty_display_region' in result(parse('<main data-study-result>unannounced success</main>'))


def test_form_free_names_and_password_semantics():
    expected={'action':'/study/brute','method':'GET','fields':['username','password','Login'],'password_type':'password','file_type':None,'encoding':None}
    html='<form action="/study/brute" method="get"><input id="whatever" name="username"><input name="password" type="password"><button name="Login">Different text</button></form>'
    assert form(parse(html),expected)==expected
    assert form(parse(html.replace('type="password"','type="text"')),expected)!=expected
    assert form(parse(html.replace('name="username"','disabled name="username"')),expected)!=expected


def test_equal_weight_and_failure_beats_gap():
    entries=[{'category':f'R{i}','status':'passed'} for i in range(1,7)]
    entries+= [{'category':'R3','status':'failed'},{'category':'R3','status':'technical_missing'}]*5
    x=aggregate(entries,{f'T{i}':1 for i in range(1,6)})
    assert x['F']=={'numerator':5,'denominator':6}
    assert x['R']['R3']==0
    assert aggregate(entries,{'T1':0,'T2':None,'T3':None,'T4':None,'T5':1})['F']=={'numerator':0,'denominator':6}
    assert aggregate(entries,{'T1':1,'T2':None,'T3':None,'T4':None,'T5':1})['F'] is None


def test_scaffold_diff_no_layout_convention():
    m={'files':{'config/x.php':{'sha256':'a'},'routes/study.php':{'sha256':'b'}}}
    files=[{'path':'config/x.php','sha256':'a'},{'path':'routes/study.php','sha256':'c'},{'path':'app/Study/Anything.php','sha256':'d'},{'path':'resources/views/study/deep/elsewhere.blade.php','sha256':'e'}]
    assert not any(scaffold_diff(m,files).values())
    files[0]['sha256']='e';assert scaffold_diff(m,files)['protected_changed_or_missing']==['config/x.php']


def load_transport():
    spec=importlib.util.spec_from_file_location('m6_transport',ROOT/'src/research_env/evaluation_transport.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def test_upload_regular_reader_rejects_links_and_race(tmp_path,monkeypatch):
    module=load_transport();secret=tmp_path/'secret';secret.write_bytes(b'PRIVATE')
    uploads=tmp_path/'uploads';uploads.mkdir();(uploads/'safe').write_bytes(b'own')
    (uploads/'sym').symlink_to(secret);os.link(secret,uploads/'hard')
    fd=os.open(uploads,os.O_RDONLY|os.O_DIRECTORY)
    try:
        assert module.regular(fd,'safe')==b'own'
        for name in ('sym','hard','../secret','/secret'):
            with pytest.raises((OSError,ValueError)):module.regular(fd,name)
        original=os.read;swapped=False
        def racing(file,n):
            nonlocal swapped
            data=original(file,n)
            if not swapped:
                swapped=True;(uploads/'safe').unlink();(uploads/'safe').symlink_to(secret)
            return data
        monkeypatch.setattr(os,'read',racing)
        with pytest.raises(ValueError,match='TOCTOU'):module.regular(fd,'safe')
    finally:os.close(fd)


def test_suite_frozen_bytes_detect_changed_inputs(tmp_path):
    (tmp_path/'cases.json').write_text(json.dumps({'cases':[]}));(tmp_path/'fixtures.json').write_text('{}')
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()}
    assert Suite(tmp_path,kind='development',expected_hashes=hashes).cases==[]
    (tmp_path/'fixtures.json').write_text('{"changed":true}')
    with pytest.raises(IntegrityError):Suite(tmp_path,kind='development',expected_hashes=hashes)


def test_trusted_development_evaluation_is_protected_from_roles():
    a=Artifact(code='EVAL',sha256='a'*64,byte_count=0,mime_type='application/json',artifact_type='evaluation_receipt',producer='trusted_evaluator',run_id=None,original_name='receipt',access_scope='trusted_register')
    assert protected_evaluation(a)
    assert not protected_evaluation(a.model_copy(update={'artifact_type':'own_internal_tests','producer':'role','access_scope':'role'}))

from types import SimpleNamespace
from test_artifacts import instance, workspace, secured
from research_env.snapshots import ProcessWriters
from research_env.register import GateError


def component_attempt(instance,tmp_path):
    r,settings,store,f,freeze,*_=instance
    run,tree,snapshots,bindings=workspace(instance,tmp_path)
    seal=snapshots.seal(tree,writers=ProcessWriters(),**bindings)
    r.set_state(run.id,r.state(run.id).model_copy(update={'execution':'terminal','terminal_cause':'finished','ended_at':fixtures.NOW}))
    images=RuntimeImages(**json.loads((ROOT/'src/research_env/pipeline_runtime.lock.json').read_text()))
    sandbox=SimpleNamespace(store=store,register=r,settings=settings,images=images,assets=ROOT)
    ev=Evaluator(sandbox);execution=str(uuid4())
    job=r.add(Job(code='M6-UNIT-JOB-'+execution,phase_id=run.phase_id,run_id=run.id,job_type='measurement',idempotency_key=execution,suite_id=run.suite_id))
    with r.transaction():r.connection.execute('INSERT INTO evaluation_execution VALUES(?,?,?,?,?,?,?,?,?,?)',(execution,str(job.id),str(run.id),str(seal.id),str(run.suite_id),str(f['tool'].id),'ready',None,'{}',fixtures.NOW.isoformat()))
    raw=ev._raw(execution,'unit_inputs',{'kind':'synthetic component control; not actual HTTP'})
    entries=[{'id':f'UNIT-R{i}','case_id':f'UNIT-CASE-R{i}','category':f'R{i}','expected':{'value':1},'actual':{'value':1},'status':'passed','cause':'synthetic predetermined value','input_artifact_id':str(raw.id),'raw_artifact_id':str(raw.id)} for i in range(1,7)]
    return ev,execution,run,seal,raw,entries


def test_measurement_attempt_draft_latest_valid_and_joint_T4_invalidation(instance,tmp_path):
    ev,execution,run,seal,raw,entries=component_attempt(instance,tmp_path)
    draft=ev._measurement(execution,completion='draft',entries=[],reason='Observed ongoing synthetic measurement')
    assert not ev.register.valid(draft.id)
    old=ev._measurement(execution,completion='completed',entries=entries,reason='Synthetic completed technical observations')
    review=ev._review(execution,'T4',1,'SYNTHETIC human criterion control; no real approval',code_id=raw.id,measurement_ids=(old.id,),completion='completed',person='SYNTHETIC-ONLY-PERSON',origin='human')
    assert ev.register.valid(review.id)
    late=ev._measurement(execution,completion='draft',entries=[],reason='Open remeasurement cannot replace valid completed evidence')
    assert ev.register.select_revision(run.id,'measurement','functional_R',old.compatibility).id==old.id
    ev._review(execution,'T4',None,'Open saved reviewer draft',code_id=raw.id,measurement_ids=(old.id,),person='SYNTHETIC-ONLY-PERSON',origin='human')
    assert ev.register.select_revision(run.id,'review','T4',old.compatibility).id==review.id
    before=ev.register.get(seal.id,CandidateSnapshot).tree_hash
    ev.invalidate((old.id,),defect_evidence_id=raw.id,reason='Deliberate synthetic evaluator defect; old values unusable',person='TECHNICAL TEST')
    assert ev.register.select_revision(run.id,'measurement','functional_R',old.compatibility) is None
    assert ev.register.select_revision(run.id,'review','T4',old.compatibility) is None
    new=ev._measurement(execution,completion='completed',entries=entries,reason='Independent corrected synthetic remeasurement')
    assert new.id!=old.id and new.compatibility.candidate_hash==before
    assert ev.register.select_revision(run.id,'measurement','functional_R',old.compatibility).id==new.id
    assert ev.register.get(old.id,MeasurementAttempt).compatibility.candidate_hash==before
    assert len([x for x in ev.register.all(TestResult) if x.measurement_id==new.id])==6


def test_later_substantive_review_stales_backup_open_draft_does_not_forbid_next_run(instance,tmp_path):
    ev,execution,run,seal,raw,entries=component_attempt(instance,tmp_path)
    m=ev._measurement(execution,completion='completed',entries=entries,reason='Completed synthetic control')
    ev._review(execution,'T2',None,'Open justified review saved before backup',code_id=raw.id,person='SYNTHETIC-PERSON',origin='human')
    # Operational job is completed to exercise normal next-run backup gate.
    ev._finish(execution,m)
    r,settings,store,f,freeze,*_=instance
    secured(instance,tmp_path/'backup-after-open-review')
    assert r.backup_status(freeze.id)=='current'
    ev._review(execution,'T2',None,'Changed substantive open review',code_id=raw.id,person='SYNTHETIC-PERSON',origin='human')
    assert r.backup_status(freeze.id)=='stale'
    secured(instance,tmp_path/'backup-after-review-revision')
    next_run,_=fixtures.start(r,freeze,f)
    assert next_run.id!=run.id


def test_model_call_rejects_trusted_development_provenance(instance,tmp_path):
    ev,execution,run,seal,raw,entries=component_attempt(instance,tmp_path)
    r=ev.register;conf=r.get(run.configuration_version_id,ConfigurationVersion)
    # Development evaluation has trusted_register scope; producer remains a
    # protected origin. Even a caller explicitly attempting reuse is rejected.
    protected=ev.store.json({'external':'evaluation'},run_id=run.id,artifact_type='evaluation_receipt',producer='trusted_evaluator',access_scope='trusted_register')
    prompt=ev.store.json({'messages':[]},run_id=run.id,artifact_type='model_request',producer='adapter',access_scope='role')
    with pytest.raises(GateError,match='Geschützter'):
        r.add(ModelCall(code='LEAK-CHECK',run_id=run.id,node='analyzer',sequence=1,model_package_id=conf.settings.model_a,request_hash=prompt.sha256,
            messages_artifact_id=prompt.id,parameters=r.resolve_call_parameters(conf,'analyzer'),input_artifact_ids=(protected.id,),allowed_paths=(),tools=()))


def test_schema_preflight_rejects_every_ddl_difference():
    ev=object.__new__(Evaluator);ev.control_schema='INDEPENDENT DDL exact bytes'
    rows=[{'user_id':1,'first_name':'Own'}]
    modes=sorted(('ONLY_FULL_GROUP_BY','STRICT_TRANS_TABLES','NO_ZERO_IN_DATE','NO_ZERO_DATE','ERROR_FOR_DIVISION_BY_ZERO','NO_ENGINE_SUBSTITUTION'))
    db={'rows':[{'user_id':'1','first_name':'Own'}],'tables':[['users','BASE TABLE']],'settings':['utf8mb4','utf8mb4_bin','+00:00',modes,'utf8mb4','utf8mb4_bin','+00:00',modes],
        'ddl':ev.control_schema,'reader_grants':['GRANT SELECT ON study.* TO reviewer']}
    ev._db_preflight(db,rows)
    for change in ('missing column','wrong length','wrong NULL/default','wrong UNIQUE key','wrong CHECK','wrong collation'):
        with pytest.raises(IntegrityError):ev._db_preflight(dict(db,ddl=change),rows)


def test_stylesheet_hidden_status_and_hidden_controls():
    body='<style>.quiet {display:none}</style><main data-study-result><strong class="quiet" data-study-status>SUCCESS</strong></main>'
    assert result(parse(body))['marker'] is None
    expected={'action':'/study/brute','method':'GET','fields':['username','password','Login'],'password_type':'password','file_type':None,'encoding':None}
    body='<form action="/study/brute"><input type="hidden" name="username"><input name="password" type="password"><button name="Login">Go</button></form>'
    assert form(parse(body),expected)!=expected


def test_instrument_manifest_binds_critical_connections_and_versions():
    images=RuntimeImages(**json.loads((ROOT/'src/research_env/pipeline_runtime.lock.json').read_text()))
    manifest=instrument_manifest(images)
    assert set(('sandbox_runtime.py','sandbox_client.py','snapshots.py','artifacts.py','register.py','domain.py','migrations/008_evaluation.sql'))<=set(manifest['sources'])
    assert manifest['libraries']['beautifulsoup4']=='4.15.0'
    assert manifest['runtime_images']['php']==images.php


def test_distributed_stop_state_observed_without_timer(instance,tmp_path):
    ev,execution,run,seal,raw,entries=component_attempt(instance,tmp_path)
    ev.active_execution=execution
    assert not ev._stopped()
    ev._update(execution,'recovery_required')
    assert ev._stopped()


def test_interrupted_remeasurement_draft_never_replaces_old_valid(instance,tmp_path):
    ev,execution,run,seal,raw,entries=component_attempt(instance,tmp_path)
    completed=ev._measurement(execution,completion='completed',entries=entries,reason='Valid original synthetic measurement')
    missing=[dict(e,status='technical_missing',actual=None,cause='manual_abort') for e in entries]
    interrupted=ev._measurement(execution,completion='draft',entries=missing,reason='Voluntary interrupted remeasurement; partial raw evidence retained')
    assert ev.register.select_revision(run.id,'measurement','functional_R',completed.compatibility).id==completed.id
    assert not ev.register.valid(interrupted.id)


def test_actual_scaffold_json_session_and_trusted_csrf(tmp_path,monkeypatch):
    module=load_transport();path=tmp_path/'sessions';path.mkdir()
    (path/'session-original').write_text(json.dumps({'_token':'original-CSRF','login_web_hash':'scaffold-operator'}))
    monkeypatch.setattr(module,'directory',lambda parts:os.open(path,os.O_RDONLY|os.O_DIRECTORY))
    value=module.session()
    assert value=={'sessions':[{'session_id':'session-original','auth_identity':['scaffold-operator']}],'csrf_tokens':{'session-original':'original-CSRF'}}
    assert Evaluator._session_token(value)=='original-CSRF'


def test_correction_schedule_keeps_original_config_phase_and_seal(instance,tmp_path):
    ev,execution,run,seal,raw,entries=component_attempt(instance,tmp_path)
    original=ev._measurement(execution,completion='completed',entries=entries,reason='Actual stored old-version component attempt')
    ev._finish(execution,original)
    r=ev.register;old_conf=r.get(run.configuration_version_id,ConfigurationVersion)
    newer=r.add(AssetVersion(code='CORRECTED-INSTRUMENT',asset_type='tool',manifest_hash=digest(instrument_manifest(ev.sandbox.images)),origin='Actual corrected instrument bytes',access_scope='trusted_register'))
    with pytest.raises(IntegrityError,match='not frozen'):
        ev.schedule(run.id,tool_id=newer.id,idempotency_key='unbound-drift')
    evidence=ev.correct_instrument(original.compatibility.tool_id,newer.id,defect_evidence_id=raw.id,reason='Synthetic attributed instrument defect',person='TECHNICAL TEST')
    assert not r.valid(original.id)
    assert r.compatible_evaluation_tool(run.id,newer.id)
    assert r.get(run.configuration_version_id,ConfigurationVersion)==old_conf
    assert r.state(run.id).candidate_id==seal.id
    assert r.get(seal.id,CandidateSnapshot).tree_hash==original.compatibility.candidate_hash
    comp=original.compatibility.model_copy(update={'tool_id':newer.id})
    r._compatibility(run.id,comp)
    assert r.select_revision(run.id,'measurement','functional_R',comp) is None
    with pytest.raises(Exception,match='Immutable'):
        with r.transaction():r.connection.execute('UPDATE evaluation_tool_correction SET candidate_hash=?',('0'*64,))
    assert json.loads(ev.store.read(evidence.id))['scientific_contract_changed'] is False


def test_real_role_prepare_provision_and_checkpoint_reject_external_development(tmp_path):
    from test_pipeline import setup
    from research_env.pipeline import provision
    r,settings,store,run,job,p,s,a,runner=setup(tmp_path/'pipeline')
    protected=store.json({'external':'never in prompt'},run_id=run.id,artifact_type='evaluation_receipt',producer='trusted_evaluator',access_scope='trusted_register')
    try:
        binding=p.binding(run.id)
        with pytest.raises(GateError,match='geschützter'):
            p.journal.prepare(a,run.id,'analyzer',order_id=UUID(binding['order_id']),messages=[{'role':'user','content':'Synthetic only'}],input_artifact_ids=(protected.id,))
        assert not r.all(ModelCall) and a.send_count==0
        with pytest.raises(IntegrityError,match='geschützter'):
            p._checked_state({'run_id':str(run.id),'config_hash':run.effective_hash,'statuses':{},'refs':{'test':str(protected.id)}})
        manifest=p.manifest(run.id)
        with pytest.raises(GateError,match='geschützt'):
            provision(store,run.id,package=b'Synthetic own public package',scaffold=tmp_path/'pipeline/scaffold',dependency_id=UUID(manifest['dependency_id']),proof_ids=(protected.id,),versions={'test':'synthetic'},synthetic=True)
    finally:r.close()


def scheduled_correction_attempt(instance,tmp_path,*,extra_tool_id=None):
    ev,execution,unused_run,unused_seal,raw,entries=component_attempt(instance,tmp_path)
    r=ev.register;ev._finish(execution,None)
    old_conf=r.get(unused_run.configuration_version_id,ConfigurationVersion)
    suite=r.add(AssetVersion(code='ACTUAL-SUITE-FOR-CORRECTION',asset_type='suite',manifest_hash=digest(ev.asset_lock['study_holdout/m2-v0.1']),origin='Actual independently frozen original suite hashes',suite_kind='study_holdout',access_scope='trusted_evaluator'))
    phase=r.add(StudyPhase(code='CORRECTION-PREPARATION',study_id=r.get(unused_run.phase_id,StudyPhase).study_id,purpose='preparation',provenance='Synthetic exact schedule test'))
    conf=r.add(Configuration(code='CORRECTION-CONFIG',phase_id=phase.id))
    version=r.version_configuration(conf.id,'original frozen scientific settings',old_conf.cell,old_conf.settings.model_copy(update={'holdout_suite_id':suite.id, 'tool_ids':old_conf.settings.tool_ids+((extra_tool_id,) if extra_tool_id else ())}))
    run,job=r.start_other(phase.id,version.id,decision='Synthetic own component validation',technical_evidence_ids=(instance[3]['basic'].id,),idempotency_key='correction-original-run')
    tree=tmp_path/'actual-original-candidate';(tree/'routes').mkdir(parents=True);(tree/'routes/study.php').write_text('<?php // unchanged original candidate')
    from research_env.snapshots import Snapshots
    seal=Snapshots(ev.store).seal(tree,writers=ProcessWriters(),run_id=run.id,scaffold_id=old_conf.settings.scaffold_id,dependency_ids=unused_seal.dependency_ids,internal_tests_hash=hashlib.sha256(b'').hexdigest())
    r.set_state(run.id,r.state(run.id).model_copy(update={'execution':'terminal','terminal_cause':'finished','ended_at':fixtures.NOW}))
    oldtool=old_conf.settings.tool_ids[0]
    original_execution=str(uuid4());j=r.add(Job(code='OLD-REAL-SCHEDULE-JOB',phase_id=phase.id,run_id=run.id,job_type='measurement',idempotency_key=original_execution,suite_id=suite.id))
    with r.transaction():r.connection.execute('INSERT INTO evaluation_execution VALUES(?,?,?,?,?,?,?,?,?,?)',(original_execution,str(j.id),str(run.id),str(seal.id),str(suite.id),str(oldtool),'completed',None,'{}',fixtures.NOW.isoformat()))
    newer=r.add(AssetVersion(code='ACTUAL-CORRECTED-SCHEDULE-TOOL',asset_type='tool',manifest_hash=digest(instrument_manifest(ev.sandbox.images)),origin='Current real instrument hash',access_scope='trusted_register'))
    proof=ev.store.json({'reason':'Synthetic technical old-tool defect'},run_id=run.id,artifact_type='evaluation_defect_diagnosis',producer='trusted_evaluator',access_scope='trusted_evaluator')
    ev.correct_instrument(oldtool,newer.id,defect_evidence_id=proof.id,reason='Explicit actual-schedule component correction',person='TECHNICAL TEST')
    attempt=ev.schedule(run.id,tool_id=newer.id,idempotency_key='corrected-real-schedule')
    assert ev.row(attempt)['candidate_id']==str(seal.id)
    assert ev.row(attempt)['tool_id']==str(newer.id)
    assert r.get(run.id,Run).configuration_version_id==version.id
    assert newer.id not in version.settings.tool_ids
    assert r.get(run.id,Run).phase_id==phase.id
    assert r.get(seal.id,CandidateSnapshot).tree_hash==seal.tree_hash
    return ev,attempt,run,seal,newer,version


def test_schedule_after_bound_correction_reuses_actual_original_seal(instance,tmp_path):
    scheduled_correction_attempt(instance,tmp_path)


def test_ineffective_fault_is_measurement_gap_without_candidate_zero():
    assert Evaluator._prerequisite(1,None,False)==('technical_missing','Ineffective write fault')
    assert Evaluator._prerequisite(1,None,True)==(None,None)
    assert Evaluator._prerequisite(None,'Infrastructure failure',True)==('technical_missing','Infrastructure failure')


def save_absence_proof(ev,report):
    """Retain actual API/CAS bytes when the host proof wrapper provides output."""
    if not os.environ.get('M6_PROBE_EVIDENCE_DIR'):return
    output=Path(os.environ['M6_PROBE_EVIDENCE_DIR'])/report['run_id'];output.mkdir(parents=True,exist_ok=False)
    artifacts=[x for x in ev.register.all(Artifact) if str(x.run_id)==report['run_id']]
    for a in artifacts:(output/(str(a.id)+'.raw')).write_bytes(ev.store.read(a.id))
    (output/'manifest.json').write_text(json.dumps({'run':ev.register.get(UUID(report['run_id']),Run).model_dump(mode='json'),'state':ev.register.state(UUID(report['run_id'])).model_dump(mode='json'),'artifacts':[a.model_dump(mode='json') for a in artifacts],'receipt':report},indent=2)+'\n')


def absent_attempt(instance,cause):
    r,settings,store,f,freeze,*_=instance
    secured(instance,settings.control.parent.parent/'absence-backup')
    run,generation_job=fixtures.start(r,freeze,f)
    r.set_state(run.id,r.state(run.id).model_copy(update={'execution':'terminal','terminal_cause':cause,'ended_at':fixtures.NOW,'seal':'no_candidate'}),reason='Preassigned synthetic absence terminal state; no fake candidate')
    with r.transaction():r.connection.execute("UPDATE job_state SET status='stopped' WHERE job_id=?",(str(generation_job.id),))
    images=RuntimeImages(**json.loads((ROOT/'src/research_env/pipeline_runtime.lock.json').read_text()))
    ev=Evaluator(SimpleNamespace(store=store,register=r,settings=settings,images=images,assets=ROOT))
    execution=str(uuid4());job=r.add(Job(code='ABSENCE-JOB-'+execution,phase_id=run.phase_id,run_id=run.id,job_type='measurement',idempotency_key=execution,suite_id=run.suite_id))
    with r.transaction():r.connection.execute('INSERT INTO evaluation_execution VALUES(?,?,?,?,?,?,?,?,?,?)',(execution,str(job.id),str(run.id),None,str(run.suite_id),str(f['tool'].id),'ready',None,json.dumps({'revision':1}),fixtures.NOW.isoformat()))
    return ev,execution,run


@pytest.mark.parametrize('cause,classification',[
 ('content_failure','provider_or_infrastructure_no_candidate'),
 ('interrupted','manual_abort'),('outcome_unknown','unclear_api_outcome'),
 ('technical_failure','provider_or_infrastructure_no_candidate'),
])
def test_absence_terminal_status_without_attribution_never_invents_zero(instance,cause,classification):
    ev,execution,run=absent_attempt(instance,cause)
    result=ev.run_absent(execution)
    save_absence_proof(ev,result)
    assert result['entries'] and all(x['status']=='technical_missing' and x['z'] is None for x in result['entries'])
    assert result['cause']==classification
    assert result['T'] is None and result['F'] is None and all(v is None for v in result['R'].values())
    assert result['candidate_hash'] is None and ev.register.state(run.id).candidate_id is None
    receipts=[x for x in ev.register.all(Artifact) if x.run_id==run.id and x.artifact_type=='evaluation_absence_receipt']
    assert len(receipts)==1 and json.loads(ev.store.read(receipts[0].id))==result
    assert not ev.register.all(CandidateSnapshot) and not ev.register.all(CriterionReviewRevision)


def test_absence_requires_independent_same_run_completed_failure_proof(instance):
    ev,execution,run=absent_attempt(instance,'content_failure')
    source=ev.store.json({'synthetic_generation_completed':True,'received_output':'No migrated module code','cause':'Deliberate fixed content failure fixture'},run_id=run.id,artifact_type='generation_failure_detail',producer='trusted_register',access_scope='trusted_register')
    proof=ev.store.json({'cause':'proven_generation_failure','generation_completed':True,'source_artifact_ids':[str(source.id)],'reason':'SYNTHETIC preassigned completed content failure; no human study verdict'},run_id=run.id,artifact_type='candidate_absence_diagnosis',producer='trusted_evaluator',access_scope='trusted_evaluator')
    result=ev.run_absent(execution,cause_evidence_id=proof.id)
    save_absence_proof(ev,result)
    assert result['entries'] and all(x['status']=='blocked_candidate' and x['z']==0 for x in result['entries'])
    assert result['status']=='blocked_by_candidate' and result['T']==0 and result['F']=={'numerator':0,'denominator':6}
    assert result['R']=={f'R{i}':0 for i in range(1,7)} and result['complete']==0
    assert result['attributed_cause_evidence_id']==str(proof.id) and result['candidate_hash'] is None
    assert all(result['T_criteria'][k] is None for k in ('T2','T3','T4'))
    assert not ev.register.all(CandidateSnapshot) and not ev.register.all(CriterionReviewRevision)


def test_absence_foreign_attribution_rejected_before_claim(instance):
    ev,execution,run=absent_attempt(instance,'content_failure')
    foreign=ev.store.json({'cause':'proven_generation_failure','generation_completed':True},artifact_type='candidate_absence_diagnosis',producer='trusted_evaluator',access_scope='trusted_evaluator')
    with pytest.raises(IntegrityError,match='same-run'):
        ev.run_absent(execution,cause_evidence_id=foreign.id)
    assert ev.row(execution)['status']=='ready'
    assert not any(x.artifact_type=='evaluation_absence_receipt' for x in ev.register.all(Artifact))


def test_provider_absence_with_actual_synthetic_diagnostic_stays_missing(instance):
    ev,execution,run=absent_attempt(instance,'technical_failure')
    source=ev.store.json({'synthetic_provider_status':503,'received_candidate':False,'outcome':'provider transport failure'},run_id=run.id,artifact_type='provider_failure_diagnostic',producer='trusted_register',access_scope='trusted_register')
    diagnosis=ev.store.json({'cause':'provider_failure','generation_completed':False,'source_artifact_ids':[str(source.id)],'reason':'Preassigned synthetic provider failure, not model content'},run_id=run.id,artifact_type='candidate_absence_diagnosis',producer='trusted_evaluator',access_scope='trusted_evaluator')
    report=ev.run_absent(execution,cause_evidence_id=diagnosis.id)
    save_absence_proof(ev,report)
    assert report['T'] is None and report['F'] is None and report['attributed_cause_evidence_id']==str(diagnosis.id)
    assert report['attribution']['cause']=='provider_failure'


def test_actual_native_hang_child_script_compiles_before_docker():
    import ast
    tree=ast.parse((ROOT/'scripts/verify-evaluation.py').read_text())
    scripts=[ast.literal_eval(node.value) for node in ast.walk(tree) if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='script' for t in node.targets)]
    assert len(scripts)==1
    compile(scripts[0],'<actual native separate control child>','exec')


def test_explicit_recovery_new_claim_same_seal_does_not_inherit_stop(instance,tmp_path):
    ev,first,run,seal,tool,config=scheduled_correction_attempt(instance,tmp_path)
    ev.sandbox.stop_recorded=lambda run_id,reason:None
    ev.sandbox.recover=lambda decision,run_id:None
    ev._claim(first)
    ev.abort(first,reason='Explicit synthetic manual controller stop; no native resource in this register fixture')
    assert ev.row(first)['status']=='recovery_required' and ev._stopped()
    evidence_before={a.id:ev.store.read(a.id) for a in ev.register.all(Artifact) if a.run_id==run.id}
    ev.recover(first,decision='stop_owned')
    assert ev.row(first)['status']=='interrupted'
    second=ev.schedule(run.id,tool_id=tool.id,idempotency_key='explicit-fresh-attempt-after-stop')
    assert second!=first and ev.row(second)['job_id']!=ev.row(first)['job_id']
    ev._claim(second)
    assert ev.active_execution==second and not ev._stopped()
    with pytest.raises(IntegrityError):ev._claim(first)
    assert ev.active_execution==second and not ev._stopped()
    assert all(ev.store.read(aid)==data for aid,data in evidence_before.items())
    assert ev.register.state(run.id).candidate_id==seal.id
    assert ev.register.get(run.configuration_version_id,ConfigurationVersion)==config
    assert ev.register.get(seal.id,CandidateSnapshot).tree_hash==seal.tree_hash


@pytest.mark.parametrize('dom_token',['current-session-token','wrong-nonempty-token','stale-session-token',''])
def test_upload_form_csrf_matches_actual_current_scaffold_session(dom_token):
    expected={'action':'/study/upload','method':'POST','fields':['uploaded','Upload'],'password_type':None,'file_type':'file','encoding':'multipart/form-data','valid_csrf_token':True}
    html='<form action="/study/upload" method="post" enctype="multipart/form-data"><input name="_token" type="hidden" value="'+dom_token+'"><input name="uploaded" type="file"><button name="Upload">Upload</button></form>'
    # Exercise the actual assertion path and receipt's dynamic expected bytes.
    ev=object.__new__(Evaluator)
    actual,bound_expected,passed=ev._compare({'target':'dom.form','expected':expected},{'body':html},{'csrf_tokens':{'authenticated-session':'current-session-token'}},{})
    assert bound_expected=={**expected,'csrf_token':'current-session-token'}
    assert actual['csrf_token']==dom_token
    assert passed==(dom_token=='current-session-token')
    assert actual['valid_csrf_token']==passed


def test_schedule_idempotency_requires_exact_original_binding(instance,tmp_path):
    r=instance[0]
    images=RuntimeImages(**json.loads((ROOT/'src/research_env/pipeline_runtime.lock.json').read_text()))
    alternative=r.add(AssetVersion(code='ALTERNATIVE-LEGITIMATE-INSTRUMENT-ID',asset_type='tool',manifest_hash=digest(instrument_manifest(images)),origin='Same actual instrument, separately frozen identity',access_scope='trusted_register'))
    ev,attempt,run,seal,tool,config=scheduled_correction_attempt(instance,tmp_path,extra_tool_id=alternative.id)
    before=(len(r.all(Job)),len(r.all(Artifact)),r.connection.execute('SELECT count(*) FROM evaluation_execution').fetchone()[0])
    assert ev.schedule(run.id,tool_id=tool.id,idempotency_key='corrected-real-schedule')==attempt
    assert before==(len(r.all(Job)),len(r.all(Artifact)),r.connection.execute('SELECT count(*) FROM evaluation_execution').fetchone()[0])
    with pytest.raises(IntegrityError,match='different run/candidate/suite/tool binding'):
        ev.schedule(run.id,tool_id=alternative.id,idempotency_key='corrected-real-schedule')
    other_conf=r.add(Configuration(code='OTHER-IDEMPOTENCY-CONFIG',phase_id=run.phase_id))
    version=r.version_configuration(other_conf.id,'Separate legitimate unchanged scientific inputs',config.cell,config.settings.model_copy(update={'tool_ids':(tool.id,alternative.id)}))
    other,job=r.start_other(run.phase_id,version.id,decision='Synthetic foreign-run idempotency test',technical_evidence_ids=(instance[3]['basic'].id,),idempotency_key='other-legitimate-generation')
    tree=tmp_path/'other-sealed-candidate';(tree/'routes').mkdir(parents=True);(tree/'routes/study.php').write_text('<?php // unchanged original candidate')
    from research_env.snapshots import Snapshots
    other_seal=Snapshots(ev.store).seal(tree,writers=ProcessWriters(),run_id=other.id,scaffold_id=config.settings.scaffold_id,dependency_ids=seal.dependency_ids,internal_tests_hash=hashlib.sha256(b'').hexdigest())
    r.set_state(other.id,r.state(other.id).model_copy(update={'execution':'terminal','terminal_cause':'finished','ended_at':fixtures.NOW}))
    with pytest.raises(IntegrityError,match='different run/candidate/suite/tool binding'):
        ev.schedule(other.id,tool_id=tool.id,idempotency_key='corrected-real-schedule')
    assert ev.row(attempt)['run_id']==str(run.id) and ev.row(attempt)['candidate_id']==str(seal.id)
    assert r.state(other.id).candidate_id==other_seal.id


def test_actual_native_fixture_copy_overlay_and_own_files_preserve_readonly_original(tmp_path):
    import ast
    import shutil
    import stat
    from research_env.snapshots import inventory
    # Use the actual harness function without starting its Docker controller.
    tree=ast.parse((ROOT/'scripts/verify-evaluation.py').read_text())
    definition=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='writable_fixture_copy')
    namespace={'shutil':shutil,'os':os,'stat':stat,'Path':Path}
    exec(compile(ast.Module(body=[definition],type_ignores=[]),'<actual native fixture materializer>','exec'),namespace)
    copy=namespace['writable_fixture_copy']
    source=tmp_path/'immutable-scaffold'
    (source/'routes').mkdir(parents=True);(source/'routes/study.php').write_bytes(b'original fixed route')
    (source/'resources/views/study').mkdir(parents=True);(source/'resources/views/study/original.blade.php').write_bytes(b'original fixed view')
    (source/'app/Http/Controllers/Study').mkdir(parents=True)
    (source/'executable').write_bytes(b'original fixed executable')
    for f in source.rglob('*'):
        if f.is_file():f.chmod(0o555 if f.name=='executable' else 0o444)
    directories=[source]+[d for d in source.rglob('*') if d.is_dir()]
    for d in directories:d.chmod(0o555)
    before=inventory(source);directory_modes={str(d.relative_to(source)):stat.S_IMODE(d.stat().st_mode) for d in directories}
    destination=copy(source,tmp_path/'fresh-own-workspace')
    assert all(stat.S_IMODE(d.stat().st_mode)&stat.S_IWUSR for d in [destination]+[d for d in destination.rglob('*') if d.is_dir()])
    assert all(stat.S_IMODE(f.stat().st_mode)&stat.S_IWUSR for f in destination.rglob('*') if f.is_file())
    assert stat.S_IMODE((destination/'executable').stat().st_mode)==0o755
    overlay=tmp_path/'independent-overlay.php';overlay.write_bytes(b'predetermined overlay route')
    shutil.copyfile(overlay,destination/'routes/study.php')
    (destination/'app/Http/Controllers/Study/Independent.php').write_bytes(b'predetermined new overlay controller')
    # Own public hang/sequence sources use write_text in the same copied tree.
    (destination/'routes/study.php').write_text('predetermined own public route')
    (destination/'resources/views/study/new.blade.php').write_text('predetermined new own public view')
    assert (destination/'routes/study.php').read_text()=='predetermined own public route'
    assert (destination/'resources/views/study/original.blade.php').read_bytes()==b'original fixed view'
    assert inventory(source)==before
    assert {str(d.relative_to(source)):stat.S_IMODE(d.stat().st_mode) for d in directories}==directory_modes
    assert (source/'routes/study.php').stat().st_ino!=(destination/'routes/study.php').stat().st_ino
    with pytest.raises(FileExistsError):copy(source,source)
    with pytest.raises(FileExistsError):copy(source,destination)
    assert inventory(source)==before


def marker_poll_function():
    import ast
    script=ast.parse((ROOT/'scripts/verify-evaluation.py').read_text())
    functions=[n for n in script.body if isinstance(n,ast.FunctionDef) and n.name=='poll_probe_marker']
    if functions:definition=functions[0]
    else:
        # Red reproduction uses the actual original inline poll, not a imitation.
        loop=next(n for n in ast.walk(script) if isinstance(n,ast.While) and ast.unparse(n.test)=='child.poll() is None and (not entered)')
        definition=ast.parse('def poll_probe_marker(r,dockerclient,run_id,child,events):\n run=SimpleNamespace(id=run_id)\n entered=False\n return entered').body[0]
        definition.body.insert(2,loop)
    namespace={'json':json,'docker':__import__('docker'),'time':SimpleNamespace(sleep=lambda _:None),'SimpleNamespace':SimpleNamespace}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[definition],type_ignores=[])),'<actual harness marker poll>','exec'),namespace)
    return namespace['poll_probe_marker']


@pytest.mark.parametrize('status,explanation,expected',[(409,'Container is paused, unpause the container before exec',True),(409,'Container has an unrelated conflict',False),(500,'Daemon unavailable',False)])
def test_marker_poll_running_to_paused_race_and_other_api_errors(status,explanation,expected):
    import docker
    from requests import Response
    response=Response();response.status_code=status
    error=docker.errors.APIError('Actual mocked SDK exec error',response=response,explanation=explanation)
    class Candidate:
        status='running';calls=0
        def reload(self):self.status='running'
        def exec_run(self,command):
            self.calls+=1
            if self.calls==1:self.status='paused';raise error
            return SimpleNamespace(exit_code=0,output=b'entered')
    candidate=Candidate();child=SimpleNamespace(poll=lambda:None)
    body={'resources':[{'kind':'container','name':'own-candidate','id':'own-id'}]}
    r=SimpleNamespace(connection=SimpleNamespace(execute=lambda *args:SimpleNamespace(fetchall=lambda:[{0:json.dumps(body),'id':'own-http-job','body':json.dumps(body)}])))
    client=SimpleNamespace(containers=SimpleNamespace(get=lambda _:candidate));events=[]
    if expected:
        assert marker_poll_function()(r,client,'own-run',child,events)
        assert candidate.calls==2 and events[0]['kind']=='paused_during_marker_exec' and events[0]['status_code']==409
    else:
        with pytest.raises(docker.errors.APIError) as raised:marker_poll_function()(r,client,'own-run',child,events)
        assert raised.value is error and not events


def test_parent_probe_exception_reaps_child_and_recovers_only_recorded_run(instance,tmp_path):
    import ast
    from datetime import datetime,timezone
    import time
    script=ast.parse((ROOT/'scripts/verify-evaluation.py').read_text())
    definition=next(n for n in script.body if isinstance(n,ast.FunctionDef) and n.name=='close_probe_child')
    namespace={'datetime':datetime,'timezone':timezone,'time':time}
    exec(compile(ast.Module(body=[definition],type_ignores=[]),'<actual trusted probe child cleanup>','exec'),namespace)
    close=namespace['close_probe_child']
    ev,attempt,run,seal,tool,config=scheduled_correction_attempt(instance,tmp_path);ev._claim(attempt)
    r=ev.register
    foreign=dict(r.connection.execute('SELECT * FROM evaluation_execution WHERE run_id<>? LIMIT 1',(str(run.id),)).fetchone())
    for id,job,owner in [('owned-http',ev.row(attempt)['job_id'],str(run.id)),('foreign-http',foreign['job_id'],foreign['run_id'])]:
        with r.transaction():r.connection.execute('INSERT INTO sandbox_execution VALUES(?,?,?,?,?,?,?,?,?)',(id,job,owner,'fixture-instance','study_holdout','http','running',json.dumps({'resources':[{'kind':'container','id':id+'-candidate','name':id+'-candidate'}]}),fixtures.NOW.isoformat()))
    operations=[]
    def stop_recorded(run_id,reason):operations.append(('stop',str(run_id)))
    def recover(decision,run_id):
        operations.append(('recover',str(run_id)))
        assert decision=='stop_owned' and str(run_id)==str(run.id)
        with r.transaction():r.connection.execute("UPDATE sandbox_execution SET status='interrupted' WHERE run_id=?",(str(run_id),))
    ev.sandbox.stop_recorded=stop_recorded;ev.sandbox.recover=recover
    class Child:
        waited=False
        def wait(self):
            assert operations[0]==('stop',str(run.id))
            self.waited=True;operations.append(('wait',str(run.id)));return 1
    child=Child();saved={};record={'marker_poll_events':[]}
    import docker
    from requests import Response
    response=Response();response.status_code=500
    parent_error=docker.errors.APIError('Synthetic actual parent exec failure',response=response,explanation='Unrelated daemon error')
    def poll_failure(*args):raise parent_error
    # Execute the actual parent try/except/finally, so an unexpected poll error
    # really invokes cleanup before the original exception propagates.
    supervision=next(n for n in ast.walk(script) if isinstance(n,ast.Try) and any(isinstance(item,ast.Expr) and isinstance(item.value,ast.Call) and isinstance(item.value.func,ast.Name) and item.value.func.id=='close_probe_child' for item in n.finalbody))
    def save(name,body):saved[name]=json.loads(json.dumps(body))
    namespace.update(r=r,dockerclient=None,run=run,execution=attempt,evaluator=ev,child=child,child_record=record,write=save,poll_probe_marker=poll_failure,close_probe_child=close)
    with pytest.raises(docker.errors.APIError) as raised:
        exec(compile(ast.Module(body=[supervision],type_ignores=[]),'<actual parent failure supervision>','exec'),namespace)
    assert raised.value is parent_error
    assert child.waited and operations==[('stop',str(run.id)),('wait',str(run.id)),('recover',str(run.id))]
    receipt=saved['hang-child-process.json'];assert receipt['exit_code']==1
    assert receipt['parent_failure']['candidate_failure_inferred'] is False
    assert receipt['cleanup']['sandbox_execution_ids']==['owned-http'] and not receipt['cleanup']['remaining_active_sandbox_ids']
    assert ev.row(attempt)['status']=='interrupted' and r.state(run.id).candidate_id==seal.id
    assert r.get(run.configuration_version_id,ConfigurationVersion)==config
    assert r.connection.execute("SELECT status FROM sandbox_execution WHERE id='foreign-http'").fetchone()[0]=='running'
    assert any(a.artifact_type=='evaluation_stop_requested' for a in r.all(Artifact))
    assert any(a.artifact_type=='evaluation_recovery' for a in r.all(Artifact))

@pytest.mark.parametrize('module,condition',[(m,k) for m in ('BF','SQL','UP') for k in ('K0','K1')])
def test_approved_csrf_revision_selects_exact_public_package_and_preserves_original(instance,module,condition):
    from research_env.context_assets import package_path, contract_binding
    r,settings,store,f,*_=instance
    locked=json.loads((ROOT/'src/research_env/pipeline_assets.lock.json').read_text())
    contract=r.add(AssetVersion(code='PUBLIC-CSRF1-'+str(uuid4()),asset_type='contract',manifest_hash=contract_binding(ROOT)['manifest_sha256'],origin='Explicitly approved limited revision',access_scope='public_development'))
    context=r.add(AssetVersion(code='PUBLIC-CONTEXT-CSRF1-'+str(uuid4()),asset_type='source_context',manifest_hash=locked[f'assets/context/m2-v0.1-csrf1/{module}/{condition}/manifest.json'],origin='Exact public revised package',access_scope='public_development'))
    conf=r.get(f['versions'][f'C-{module}-{condition[-1]}'].id,ConfigurationVersion)
    revised=conf.model_copy(update={'settings':conf.settings.model_copy(update={'contract_id':contract.id,'context_ids':{f'{module}-{condition}':context.id}})})
    expected=f'assets/context/m2-v0.1-csrf1/{module}/{condition}/package.txt'
    assert package_path(r,revised,strict=True)==expected
    payload=(ROOT/expected).read_bytes()
    assert (ROOT/'docs/vertraege/m2-v0.1-csrf1/addendum.md').read_bytes() in payload
    original=ROOT/f'assets/context/m2-v0.1/{module}/{condition}'
    for e in json.loads((original/'manifest.json').read_text())['files']:
        assert (original/e['path']).read_bytes()==(ROOT/expected).parent.joinpath(e['path']).read_bytes()
    bad=revised.model_copy(update={'settings':revised.settings.model_copy(update={'contract_id':f['settings'].contract_id})})
    with pytest.raises(IntegrityError,match='dieselbe Vertragsrevision'):package_path(r,bad,strict=True)
    old=r.add(AssetVersion(code='PUBLIC-ORIGINAL-CONTEXT-'+str(uuid4()),asset_type='source_context',manifest_hash=locked[f'assets/context/m2-v0.1/{module}/{condition}/manifest.json'],origin='Unchanged original package',access_scope='public_development'))
    historic=conf.model_copy(update={'settings':conf.settings.model_copy(update={'context_ids':{f'{module}-{condition}':old.id}})})
    assert package_path(r,historic,strict=True)==f'assets/context/m2-v0.1/{module}/{condition}/package.txt'
    cross=historic.model_copy(update={'settings':historic.settings.model_copy(update={'contract_id':contract.id})})
    with pytest.raises(IntegrityError,match='Kontextpaket mit öffentlichem Addendum'):package_path(r,cross,strict=True)
    with pytest.raises(IntegrityError,match='Kontextpaket mit öffentlichem Addendum'):package_path(r,cross)
    with pytest.raises(IntegrityError,match='kein gebundenes'):package_path(r,conf,strict=True)


def test_csrf_contract_input_drift_rejected_before_measurement(instance,tmp_path):
    from research_env.context_assets import contract_binding
    import shutil
    assets=tmp_path/'public-inputs'
    for rel in contract_binding(ROOT)['files']:
        target=assets/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/rel,target)
    assert contract_binding(assets)==instrument_manifest(RuntimeImages(*('sha256:'+'1'*64 for _ in range(4))))['public_contract']
    target=assets/'docs/vertraege/m2-v0.1-csrf1/addendum.md';target.write_bytes(target.read_bytes()+b'\nchanged token origin')
    with pytest.raises(IntegrityError,match='Vertrag verändert'):contract_binding(assets)


def test_measurement_configuration_must_bind_actual_approved_contract(instance,tmp_path):
    ev,attempt,original,seal,tool,version=scheduled_correction_attempt(instance,tmp_path)
    r=ev.register
    conf=r.add(Configuration(code='WRONG-CONTRACT-CONFIG',phase_id=original.phase_id))
    bad=r.version_configuration(conf.id,'old contract never retagged',version.cell,version.settings.model_copy(update={'contract_id':instance[3]['settings'].contract_id,'tool_ids':(tool.id,)}))
    run,job=r.start_other(original.phase_id,bad.id,decision='Synthetic wrong contract negative',technical_evidence_ids=(instance[3]['basic'].id,),idempotency_key='wrong-contract-run')
    from research_env.snapshots import Snapshots
    snapshots=Snapshots(ev.store)
    tree=tmp_path/'wrong-contract-source';tree.mkdir();snapshots.restore(seal.id,tree/'candidate')
    copied=snapshots.seal(tree/'candidate',writers=ProcessWriters(),run_id=run.id,scaffold_id=bad.settings.scaffold_id,dependency_ids=seal.dependency_ids,internal_tests_hash=hashlib.sha256(b'').hexdigest())
    r.set_state(run.id,r.state(run.id).model_copy(update={'execution':'terminal','terminal_cause':'finished','ended_at':fixtures.NOW}))
    before=len(r.all(MeasurementAttempt));old_body=ev.row(attempt)['body']
    with pytest.raises(IntegrityError,match='aktive öffentliche Vertragsrevision'):ev.schedule(run.id,tool_id=tool.id,idempotency_key='wrong-contract-attempt')
    assert not r.connection.execute('SELECT id FROM evaluation_execution WHERE run_id=?',(str(run.id),)).fetchall()
    assert len(r.all(MeasurementAttempt))==before and ev.row(attempt)['body']==old_body
    body=json.loads(old_body)
    assert body['contract_interpretation']['mode']=='explicit_same_seal_correction'
    assert body['contract_interpretation']['original_binding']['contract_manifest_hash']==r.get(version.settings.contract_id,AssetVersion).manifest_hash
    assert body['contract_manifest_sha256']==instrument_manifest(ev.sandbox.images)['public_contract']['manifest_sha256']
    ev._finish(attempt,None) # Close the first scheduled component attempt before the separate direct-run positive.
    contract=r.add(AssetVersion(code='DIRECT-ACTIVE-CONTRACT',asset_type='contract',manifest_hash=body['contract_manifest_sha256'],origin='Actual approved interpretation manifest',access_scope='public_development'))
    good=r.version_configuration(conf.id,'new direct explicitly bound configuration',bad.cell,bad.settings.model_copy(update={'contract_id':contract.id}))
    direct,_=r.start_other(original.phase_id,good.id,decision='Synthetic actual active contract positive',technical_evidence_ids=(instance[3]['basic'].id,),idempotency_key='direct-contract-run')
    direct_seal=snapshots.seal(tree/'candidate',writers=ProcessWriters(),run_id=direct.id,scaffold_id=good.settings.scaffold_id,dependency_ids=seal.dependency_ids,internal_tests_hash=hashlib.sha256(b'').hexdigest())
    r.set_state(direct.id,r.state(direct.id).model_copy(update={'execution':'terminal','terminal_cause':'finished','ended_at':fixtures.NOW}))
    direct_attempt=ev.schedule(direct.id,tool_id=tool.id,idempotency_key='direct-contract-attempt')
    assert json.loads(ev.row(direct_attempt)['body'])['contract_interpretation']['mode']=='direct'
    assert ev._compatibility(ev.row(direct_attempt)).contract_id==contract.id
    ev._finish(direct_attempt,None)


def test_unused_keyframes_in_laravel_error_page_are_not_dom_selectors():
    body='<style>@keyframes pulse { 75%,to {opacity:0} } .animate-pulse {animation:pulse 1s}</style><h1>404</h1>'
    assert result(parse(body))['region_count']==0
    visible='<main data-study-result><b data-study-status style="opacity:0.95;font-size:0.875rem">SUCCESS</b></main>'
    assert result(parse(visible))['marker']=='SUCCESS'
    assert result(parse(visible.replace('opacity:0.95','opacity:0.0')))['marker'] is None


def test_applied_animation_keeps_visibility_unknown_in_static_instrument():
    body='<style>@keyframes pulse {to {opacity:0}} .result{animation:pulse 1s}</style><main class="result" data-study-result>SUCCESS</main>'
    with pytest.raises(IntegrityError,match='dynamic visibility'):parse(body)


def test_protected_routes_detect_auth_override_but_ignore_module_routes():
    from research_env.evaluation import protected_routes
    base=[{'method':'GET|HEAD','uri':'study-access','action':'Access@show','middleware':['web']},
          {'method':'POST','uri':'study-access','action':'Access@enter','middleware':['web']}]
    assert protected_routes(json.dumps(base))==protected_routes(json.dumps(base+[{'method':'GET|HEAD','uri':'study/sql','action':'Own@show','middleware':['web','auth']}]))
    broken=[{**row,'middleware':['web','auth']} for row in base]
    assert protected_routes(json.dumps(broken))!=protected_routes(json.dumps(base))
    for malformed in ('not json','[]','{}'):
        with pytest.raises((ValueError,IntegrityError)): protected_routes(malformed)


def test_candidate_access_block_is_not_an_infrastructure_zero():
    assert Evaluator._prerequisite(1,None,True,access_fault=True)[0]=='blocked_candidate'
    assert Evaluator._prerequisite(1,'Transport failed',True)[0]=='technical_missing'
    assert Evaluator._prerequisite(1,None,True)==(None,None)

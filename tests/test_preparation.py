"""M8 independent behavior contracts; all gates/providers are explicit fixtures."""
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import json
import sqlite3
from pathlib import Path
from uuid import UUID,uuid4

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
import pytest

from research_env.artifacts import ArtifactStore,IntegrityError
from research_env.domain import (Approval,AssetVersion,CandidateSnapshot,Cell,ConfigurationVersion,Event,Freeze,ModelCall,Run,RunState,Study,StudyPhase,REQUIRED_GATES)
from research_env.preparation import (software_identity,Intent,submit,process_command,own_draft,versions_hash,latest_versions,boot_commands,control)
from research_env.preparation_forms import from_fields
from research_env.preparation_freeze import preview
from research_env.preparation_views import runs,free_catalog
from research_env.register import GateError,Register
from research_env.web import create_app
from test_register import register
import test_register as fixture
from test_pipeline import setup,finish
from test_sandbox import prepared
from test_artifacts import instance


def client(r):
    c=TestClient(create_app(r.settings));c.get('/');return c


def post(c,data,path='/actions'):
    return c.post(path,data={'csrf':c.cookies['research_csrf'],**data},headers={'Origin':'http://testserver'},follow_redirects=False)


@pytest.mark.parametrize('bad',[{}, {'Origin':'http://evil.example'},{'Origin':'null'}])
def test_csrf_origin_rejects_before_command(register,bad):
    r,_=register;c=client(r)
    response=c.post('/actions',data={'csrf':c.cookies['research_csrf'],'action':'draft','idempotency_key':'a'},headers=bad)
    assert response.status_code==409 and r.connection.execute('SELECT count(*) FROM ui_command').fetchone()[0]==0


def test_doubleclick_reload_is_one_persisted_command(register):
    r,_=register;c=client(r)
    data={'idempotency_key':'draft-1','action':'draft','title':'Own draft'}
    first=post(c,data);second=post(c,data)
    assert first.headers['location']==second.headers['location']
    process_command(r)
    study=r.all(Study)[0];before=hashlib.sha256(r.settings.database.read_bytes()).hexdigest()
    for route in ('/','/studies','/studies/'+str(study.id),'/free-tests','/runs',first.headers['location']):assert c.get(route).status_code==200
    assert hashlib.sha256(r.settings.database.read_bytes()).hexdigest()==before
    assert len(r.all(Study))==1 and len(r.all(ConfigurationVersion))==12 and not r.all(ModelCall)
    changed=post(c,{**data,'title':'Changed'})
    assert changed.status_code==409


def test_template_has_no_model_or_human_approval(register):
    r,_=register;result=own_draft(r,'Draft')
    values=latest_versions(r,r.all(StudyPhase)[0].id)
    assert len(values)==12 and all(v.settings.model_a is None for v in values.values())
    assert not r.all(Approval) and not r.all(Freeze) and not r.all(Run)
    html=client(r).get(result['url']).text
    assert 'Menschliche Fachabnahme' in html and 'Gespeicherten Umfang prüfen und Matrix fixieren' in html


def test_demo_binds_actual_instrument_for_review_and_reuses_metadata(register):
    from research_env.evaluation import instrument_manifest
    from research_env.sandbox_runtime import RuntimeImages
    from research_env.domain import digest
    from research_env.preparation import imported_catalog
    r,settings=register
    result=own_draft(r,'TECHNICAL-FIXTURE: personal demo',demo=True)
    version=r.get(UUID(result['version_id']),ConfigurationVersion)
    assert len(version.settings.tool_ids)==1
    tool=r.get(version.settings.tool_ids[0],AssetVersion)
    images=RuntimeImages(**json.loads((Path(__file__).parents[1]/'src/research_env/pipeline_runtime.lock.json').read_text()))
    instrument=instrument_manifest(images)
    assert tool.manifest_hash==digest(instrument)
    assert json.loads(ArtifactStore(settings,r).read(tool.artifact_ids[0]))==instrument
    assert tool.access_scope=='trusted_register'
    assert imported_catalog(r)[0]['tool'].id==tool.id
    assert not version.settings.holdout_suite_id and not version.settings.reference_id
    assert not r.all(ModelCall) and not r.all(Approval)


def test_atomic_draft_creation_rollback(register,monkeypatch):
    r,_=register
    actual=r._put
    def fail(value):
        if isinstance(value,ConfigurationVersion):raise OSError('explicit fixture crash')
        return actual(value)
    monkeypatch.setattr(r,'_put',fail)
    with pytest.raises(OSError):own_draft(r,'Crash draft')
    assert not r.all(Study) and not r.all(StudyPhase)


def test_central_diff_updates_all_preserves_earlier_versions(register):
    r,_=register;own_draft(r,'Draft');phase=r.all(StudyPhase)[0]
    versions=latest_versions(r,phase.id);settings=next(iter(versions.values())).settings.model_copy(update={'hardware':'Visible explicit platform'})
    old={k:v.model_dump(mode='json') for k,v in versions.items()}
    key=submit(r,'central-1',Intent(action='central',target_id=phase.id,base_hash=versions_hash(r,phase.id),settings=settings))
    process_command(r)
    assert r.connection.execute('SELECT status FROM ui_command WHERE id=?',(key,)).fetchone()[0]=='completed'
    assert all(v.parent_id==versions[k].id and v.settings.hardware=='Visible explicit platform' for k,v in latest_versions(r,phase.id).items())
    assert all(r.get(versions[k].id).model_dump(mode='json')==value for k,value in old.items())
    with pytest.raises(GateError,match='verändert'):submit(r,'central-stale',Intent(action='central',target_id=phase.id,base_hash='old',settings=settings))


def test_free_form_without_demo_uses_source_metadata_and_public_suite(register):
    r,_=register;own_draft(r,'Draft');catalog=free_catalog(r);base=catalog['base'];model=catalog['models'][0]
    data={'idempotency_key':'free1','source_version_id':str(catalog['default_version'].id),'model_a':str(model.id),'model_b':str(model.id),
      'prompt_id':base['prompt_ids'][0],'handoff_id':base['handoff_id'],'context_asset_id':base['context_ids']['SQL-K0'],
      'scaffold_id':base['scaffold_id'],'contract_id':base['contract_id'],'rubric_id':base['rubric_id'],
      'development_suite_id':base['development_suite_id'],'module':'SQL','context':'K0','role_parameters':'{"all":{"seed":1}}','retry_interval_seconds':'1'}
    key,intent=from_fields(r,data,free=True)
    assert intent.settings.dvwa_commit=='b496a5d3de6b967410155e1b7d3e51e9d035eb22' and intent.settings.software_commit.startswith('source-tree-sha256:')
    assert intent.settings.holdout_suite_id is None and intent.settings.reference_id is None
    submit(r,key,intent);process_command(r)
    phase=[p for p in r.all(StudyPhase) if p.purpose=='free_test'][0]
    assert len(latest_versions(r,phase.id))==1
    assert not any(a.suite_kind=='study_holdout' for a in free_catalog(r)['assets'])


def test_free_records_have_their_own_area_and_cannot_become_main(register):
    r,_=register;own_draft(r,'Research draft');catalog=free_catalog(r);v=catalog['default_version']
    settings=v.settings.model_copy(update={'model_a':catalog['models'][0].id,'model_b':catalog['models'][0].id,
        'holdout_suite_id':None,'reference_id':None,'role_parameters':{'all':{'seed':1}},'software_commit':software_identity()})
    key=submit(r,'free-area',Intent(action='free',title='TECHNICAL-FIXTURE Free area',settings=settings,cell=Cell(module='SQL',context='K0',producer='A',verifier='A',planner=False,review=False)))
    process_command(r)
    phase=[p for p in r.all(StudyPhase) if p.purpose=='free_test'][0];cv=next(iter(latest_versions(r,phase.id).values()))
    proof=ArtifactStore(r.settings,r).json({'synthetic':True},artifact_type='technical_fixture')
    run,_=r.start_other(phase.id,cv.id,decision='TECHNICAL-FIXTURE: register-only isolation probe, no generation',technical_evidence_ids=(proof.id,),idempotency_key='free-isolation')
    r.set_state(run.id,RunState(execution='terminal',terminal_cause='interrupted'))
    c=client(r);before=hashlib.sha256(r.settings.database.read_bytes()).hexdigest()
    assert 'TECHNICAL-FIXTURE Free area' not in c.get('/studies').text
    assert str(run.id) not in c.get('/runs').text and str(run.id) not in c.get('/runs?purpose=free_test').text
    free=c.get('/free-tests');assert str(run.id) in free.text and 'TECHNICAL-FIXTURE Free area' in free.text
    assert str(run.id) in c.get('/free-tests?q='+str(run.id)).text
    response=c.get('/studies/'+str(phase.study_id),follow_redirects=False)
    assert response.status_code==303 and response.headers['location']=='/free-tests'
    assert 'name="holdout_suite_id"' not in free.text and 'name="reference_id"' not in free.text
    assert hashlib.sha256(r.settings.database.read_bytes()).hexdigest()==before
    rejected=post(c,{'idempotency_key':'convert-free','action':'new_phase','target_id':str(phase.id)})
    assert rejected.status_code==409 and 'Umwidmung' in rejected.text
    assert len([p for p in r.all(StudyPhase) if p.study_id==phase.study_id])==1


@pytest.mark.parametrize('field',['holdout_suite_id','reference_id','development_suite_id','context_ids','prompt_ids'])
def test_free_holdout_unavailable_via_api(register,field):
    r,_=register;own_draft(r,'Draft');catalog=free_catalog(r);base=next(iter(r.all(ConfigurationVersion))).settings
    hidden=next(a for a in r.all(AssetVersion) if a.suite_kind=='study_holdout')
    updates={'holdout_suite_id':None,'reference_id':None,field:hidden.id if field not in ('context_ids','prompt_ids') else {'SQL-K0':hidden.id} if field=='context_ids' else (hidden.id,)}
    with pytest.raises(GateError):submit(r,str(uuid4()),Intent(action='free',settings=base.model_copy(update=updates),cell=Cell(module='SQL',context='K0',producer='A',verifier='A',planner=False,review=False)))


def source_bound_fixture(r):
    f=fixture.setup_study(r)
    settings=f['settings'].model_copy(update={'software_commit':software_identity()})
    f['settings']=settings;f['versions']=r.central_update(f['phase'].id,settings)
    pilot_phase=r.get(f['pilot'].phase_id,StudyPhase)
    pv=next(v for v in r.all(ConfigurationVersion) if r._phase(v)==pilot_phase.id)
    new=r.version_configuration(pv.configuration_id,pv.cell_key,pv.cell,settings,parent_id=pv.id)
    f['pilot'],_=r.start_other(pilot_phase.id,new.id,decision='TECHNICAL-FIXTURE: current source pilot binding only',technical_evidence_ids=(f['basic'].id,),idempotency_key=str(uuid4()))
    r.set_state(f['pilot'].id,RunState(execution='terminal',terminal_cause='finished'))
    f['consumption']=fixture.artifact(r,f['pilot'])
    return f


def test_main_next_only_and_backup_gate_no_generation(register):
    r,_=register;f=source_bound_fixture(r);freeze=r.freeze(fixture.freeze_value(r,f));c=client(r)
    first=r.next_id(freeze.id);wrong=UUID(freeze.matrix[1]['id'])
    for planned in (wrong,first):
        response=post(c,{'action':'main','target_id':str(freeze.id),'planned_id':str(planned),'idempotency_key':str(planned),'person':'TECHNICAL-FIXTURE','decision':'synthetic decision'})
        assert response.status_code==409
    assert not [x for x in r.all(Run) if x.purpose=='main']
    fixture.backup(r,freeze,f['basic'])
    good=post(c,{'action':'main','target_id':str(freeze.id),'planned_id':str(first),'idempotency_key':str(first),'person':'TECHNICAL-FIXTURE','decision':'synthetic decision'})
    assert good.status_code==303
    duplicate=post(c,{'action':'main','target_id':str(freeze.id),'planned_id':str(first),'idempotency_key':str(first),'person':'TECHNICAL-FIXTURE','decision':'synthetic decision'})
    assert duplicate.headers['location']==good.headers['location']


def test_filters_include_unstarted_failures_and_do_not_change_fq(register):
    r,_=register;f=fixture.setup_study(r);freeze=r.freeze(fixture.freeze_value(r,f));fixture.backup(r,freeze,f['basic']);run,_=fixture.start(r,freeze,f)
    r.set_state(run.id,RunState(execution='terminal',terminal_cause='content_failure'))
    before=tuple(r.fq_ids(freeze.id));all_rows=runs(r,{})
    assert all_rows['total']==49 and len(runs(r,{'status':'not_started'})['rows'])==47  # 48 Main plus separately visible fixture pilot
    assert len(runs(r,{'status':'content_failure'})['rows'])==1
    assert len(runs(r,{'q':str(run.planned_run_id)})['rows'])==1
    for key in ('phase','config','module','status','purpose'):
        rows=runs(r,{key:all_rows['rows'][0][key]})['rows'];assert rows
    assert tuple(r.fq_ids(freeze.id))==before


def test_free_repeated_conscious_start_is_distinct_reload_is_readonly(tmp_path):
    r,settings,store,run,job,p,s,a,runner=setup(tmp_path,planner=False,review=False)
    finish(s,run);c=client(r)
    r.connection.execute("INSERT INTO run_completion(run_id,status,step,updated_at) VALUES(?,?,?,?)",(str(run.id),'manual_pending','Automatische Prüfungen beendet',run.started_at.isoformat()))
    html=c.get('/runs/'+str(run.id)).text
    assert 'Weiteren Testlauf starten' not in html
    assert 'repeat-'+str(run.id) not in html
    count=len(r.all(ModelCall));c.get('/runs/'+str(run.id));assert len(r.all(ModelCall))==count
    p.close();r.close()


def test_persisted_pause_request_and_actual_pause_separate(tmp_path):
    r,settings,store,run,job,p,s,a,runner=setup(tmp_path,planner=False,review=False)
    s.tick();s.tick()
    command=submit(r,'pause1',Intent(action='pause',target_id=run.id,decision='Explicit fixture pause'))
    process_command(r,controls=True)
    assert p.binding(run.id)['status']=='pause_requested'
    html=client(r).get('/runs/'+str(run.id)).text;assert 'Pause angefordert' in html
    s.tick();assert p.binding(run.id)['status']=='paused'
    submit(r,'resume1',Intent(action='resume',target_id=run.id,decision='Explicit fixture resume'));process_command(r,controls=True)
    finish(s,run);assert len(r.all(ModelCall))==3
    assert any(e.event_type=='actual_pause' for e in r.all(Event))
    p.close();r.close()


def test_saved_call_restart_recovery_no_regeneration(tmp_path):
    r,settings,store,run,job,p,s,a,runner=setup(tmp_path,planner=False,review=False)
    s.tick();s.tick()
    # a saved incorporated call remains journal-bound; restarting only marks recovery.
    count=a.send_count;s.boot()
    assert p.binding(run.id)['status']=='recovery_required'
    submit(r,'resume1',Intent(action='resume',target_id=run.id,decision='Explicit restart recovery'));process_command(r,controls=True)
    finish(s,run)
    assert a.send_count==3 and count==2
    p.close();r.close()


def test_commands_restart_does_not_autorepeat(register):
    r,_=register;key=submit(r,'one',Intent(action='draft',title='One'))
    r.connection.execute("UPDATE ui_command SET status='processing' WHERE id=?",(key,))
    boot_commands(r);assert process_command(r) is None
    assert r.connection.execute('SELECT status FROM ui_command WHERE id=?',(key,)).fetchone()[0]=='recovery_required'
    assert not r.all(Study)


@pytest.mark.parametrize('state',['queued','processing','failed','recovery_required'])
def test_command_poll_does_not_destroy_recovery_input(register,state):
    r,_=register;result=own_draft(r,'TECHNICAL-FIXTURE',demo=True)
    key=submit(r,'stable-recovery-form',Intent(action='demo',target_id=UUID(result['phase_id']),version_id=UUID(result['version_id']),person='TECHNICAL-FIXTURE',decision='Offline'))
    r.connection.execute('UPDATE ui_command SET status=? WHERE id=?',(state,key))
    page=BeautifulSoup(client(r).get('/commands/'+key).text,'html.parser')
    poll=page.find(attrs={'hx-trigger':'every 2s'})
    assert bool(poll)==(state in ('queued','processing'))
    assert bool(page.find('input',attrs={'name':'decision'}))==(state in ('failed','recovery_required'))


def test_scoped_log_lookup_retains_hash_owner_and_protected_scope(register):
    from research_env.preparation import run_records,diagnostic
    from research_env.preparation_views import run_detail
    from research_env.domain import Artifact
    r,settings=register;result=own_draft(r,'TECHNICAL-FIXTURE scoped logs',demo=True);store=ArtifactStore(settings,r)
    proof=store.json({'synthetic':True},artifact_type='technical_fixture')
    run,_=r.start_other(UUID(result['phase_id']),UUID(result['version_id']),decision='TECHNICAL-FIXTURE: registry-only log isolation',technical_evidence_ids=(proof.id,),idempotency_key='scoped-log-fixture')
    own=store.json({'synthetic':True},run_id=run.id,artifact_type='technical_log')
    protected=store.json({'synthetic':True,'protected_fixture':True},run_id=run.id,artifact_type='technical_log',access_scope='trusted_evaluator')
    global_log=store.json({'synthetic':True},artifact_type='technical_log')
    selected=run_records(r,Artifact,run.id)
    assert own in selected and protected in selected and global_log not in selected
    assert str(own.id) in json.dumps(diagnostic(r,run.id)) and str(protected.id) not in json.dumps(diagnostic(r,run.id))
    assert protected not in run_detail(r,run.id)['artifacts']
    # A corrupted selected own record must still fail before diagnosis/rendering.
    r.connection.execute('DROP TRIGGER register_immutable_update')
    payload=own.model_dump(mode='json');payload['original_name']='TECHNICAL-FIXTURE corruption without hash update'
    r.connection.execute('UPDATE register_record SET payload=? WHERE id=?',(json.dumps(payload),str(own.id)))
    with pytest.raises(GateError,match='Beschädigter Datensatz'):run_records(r,Artifact,run.id)
    with pytest.raises(GateError,match='Beschädigter Datensatz'):diagnostic(r,run.id)


def test_preparation_recovery_decision_is_append_only(register):
    from research_env.preparation import recover_command
    import sqlite3
    r,_=register; result=own_draft(r,'Draft',demo=True)
    key=submit(r,'recover-start',Intent(action='demo',target_id=UUID(result['phase_id']),version_id=UUID(result['version_id']),
        person='TECHNICAL-FIXTURE',decision='Explicit offline start'))
    r.connection.execute("UPDATE ui_command SET status='failed' WHERE id=?",(key,))
    recover_command(r,key,'TECHNICAL-FIXTURE: conscious same preparation after inspected failure')
    row=r.connection.execute('SELECT * FROM ui_command_recovery').fetchone()
    assert row['command_id']==key and 'inspected failure' in row['reason']
    assert not r.all(Run) and not r.all(ModelCall)
    with pytest.raises(sqlite3.IntegrityError):r.connection.execute('UPDATE ui_command_recovery SET reason=?',('changed',))


def test_malicious_title_and_error_are_escaped(register):
    r,_=register;result=own_draft(r,'<script>globalThis.BAD=1</script>')
    html=client(r).get(result['url']).text
    assert '<script>globalThis.BAD=1</script>' not in html and '&lt;script&gt;' in html


def test_real_start_requires_concrete_paid_checkbox(tmp_path,monkeypatch):
    from research_env.preparation import check_start
    r,_,_,run,_,p,_,_,_=setup(tmp_path,planner=False,review=False)
    r.connection.execute("UPDATE pipeline_binding SET status='completed'")
    r.set_state(run.id,RunState(execution='terminal',terminal_cause='finished'))
    conf=r.get(run.configuration_version_id,ConfigurationVersion)
    monkeypatch.setattr(r,'_other_gates',lambda *args:None)  # explicit isolation of UI consent gate; no real approval claim
    monkeypatch.setattr(r,'_is_mock',lambda *args:False)
    with pytest.raises(GateError,match='bezahlten Start'):
        check_start(r,Intent(action='demo',target_id=run.phase_id,version_id=conf.id,person='TECHNICAL-FIXTURE',decision='Unit consent control',paid_consent=False))
    assert not [e for e in r.all(Event) if e.event_type=='ui_real_start']
    p.close();r.close()


@pytest.mark.parametrize('use_saved_scope',[False,True])
def test_freeze_preview_full_matrix_no_persisted_approvals_until_confirmation(register,use_saved_scope):
    r,_=register;f=fixture.setup_study(r);proof=fixture.asset(r)
    data={'phase_id':str(f['phase'].id),'idempotency_key':'freeze-ui','base_hash':versions_hash(r,f['phase'].id),
        'r_c':'2','r_e':'1','seed':'explicit-ui-seed','consumption_id':str(f['consumption'].id),'pilot_ids':str(f['pilot'].id),
        'resource_decision':'TECHNICAL-FIXTURE: synthetic only','estimated_cost':'0','estimated_time':'2','estimate_source':'TECHNICAL-FIXTURE: not actual estimate',
        'backup_destination':'TECHNICAL-FIXTURE: no external platform',
        **{prefix+'_person':'TECHNICAL-FIXTURE: no real human' for prefix in ('technical','subject','cost')},
        **{prefix+'_reason':'TECHNICAL-FIXTURE: explicit synthetic control only' for prefix in ('technical','subject','cost')},
        **{'confirm_'+prefix:'true' for prefix in ('technical','subject','cost')},**{'proof_'+key:str(proof.id) for key in REQUIRED_GATES}}
    if use_saved_scope:
        from research_env.domain import digest
        r.connection.execute('INSERT INTO matrix_draft_preferences VALUES(?,?,?,?)',(str(f['phase'].id),2,1,'explicit-ui-seed'))
        scope=dict(r.connection.execute('SELECT * FROM matrix_draft_preferences WHERE phase_id=?',(str(f['phase'].id),)).fetchone())
        data={key:value for key,value in data.items() if key not in ('r_c','r_e','seed')}
        data['scope_token']=digest(scope)
        with pytest.raises(GateError,match='gespeicherte Umfang'):
            preview(r,{**data,'scope_token':'outdated'})
        with pytest.raises(GateError,match='ausschließlich'):
            preview(r,{**data,'r_c':'99'})
        assert not r.all(Approval) and not r.all(Freeze)
    key,intent=preview(r,data)
    assert len(intent.freeze.matrix)==18 and not r.all(Approval) and not r.all(Freeze)
    # Genuine technical fixture markers are assigned by this test, never inferred by UI.
    approvals=tuple(a.model_copy(update={'synthetic_fixture':True}) for a in intent.approvals)
    r.freeze(intent.freeze,approvals=approvals)
    assert r.backup_status(intent.freeze.id)=='required'
    with pytest.raises(GateError):r.central_update(f['phase'].id,f['settings'])


def test_actual_source_drift_blocks_start_before_generation(register,monkeypatch):
    r,_=register;f=source_bound_fixture(r);freeze=r.freeze(fixture.freeze_value(r,f));fixture.backup(r,freeze,f['basic'])
    monkeypatch.setattr('research_env.preparation.software_identity',lambda:'source-tree-sha256:'+('0'*64))
    with pytest.raises(GateError,match='Softwarestand'):
        submit(r,'stale-source',Intent(action='main',target_id=freeze.id,planned_id=r.next_id(freeze.id),person='TECHNICAL-FIXTURE',decision='Fixture stale start'))
    assert not [x for x in r.all(Run) if x.purpose=='main'] and not r.all(ModelCall)


def test_free_form_wrong_existing_ref_and_missing_fields_are_4xx(register):
    r,_=register;own_draft(r,'Draft');c=client(r);asset=r.all(AssetVersion)[0]
    response=post(c,{'source_version_id':str(asset.id),'idempotency_key':'wrong'},path='/free-configurations')
    assert response.status_code==409 and 'Falscher Referenztyp' in response.text
    missing=post(c,{},path='/free-configurations')
    assert missing.status_code==422


@pytest.mark.parametrize('path', ['/free-configurations','/settings/preview','/freeze/preview'])
def test_malformed_decimal_is_readable_422_without_mutation(register,path):
    r,_=register;own_draft(r,'TECHNICAL-FIXTURE decimal validation')
    phase=r.all(StudyPhase)[0];version=next(iter(latest_versions(r,phase.id).values()))
    data={'idempotency_key':'malformed-number','source_version_id':str(version.id),'retry_interval_seconds':'not-a-number'}
    if path=='/settings/preview':data={'idempotency_key':'malformed-number','phase_id':str(phase.id),'base_hash':versions_hash(r,phase.id),'retry_interval_seconds':'not-a-number'}
    if path=='/freeze/preview':
        proof=ArtifactStore(r.settings,r).json({'synthetic':True,'no_real_approval':True},artifact_type='technical_fixture')
        data={'idempotency_key':'malformed-number','phase_id':str(phase.id),'base_hash':versions_hash(r,phase.id),
            'confirm_technical':'true','confirm_subject':'true','confirm_cost':'true','r_c':'1','r_e':'1','seed':'TECHNICAL-FIXTURE',
            'consumption_id':str(proof.id),'pilot_ids':'','resource_decision':'TECHNICAL-FIXTURE','estimated_cost':'not-a-number','estimated_time':'1','estimate_source':'Synthetic only','backup_destination':'Synthetic only'}
    c=client(r);before=hashlib.sha256(r.settings.database.read_bytes()).hexdigest()
    response=post(c,data,path=path)
    assert response.status_code==422 and 'Ungültiger Zahlenwert' in response.text
    assert hashlib.sha256(r.settings.database.read_bytes()).hexdigest()==before


def test_completed_htmx_command_uses_persistent_location_without_generation(register):
    r,_=register;key=submit(r,'htmx',Intent(action='draft'));process_command(r);c=client(r)
    before=len(r.all(Study));response=c.get('/commands/'+key,headers={'HX-Request':'true'})
    assert response.status_code==200 and response.headers['HX-Redirect'].startswith('/studies/')
    assert len(r.all(Study))==before


def test_unbound_preparation_blocks_other_starts_and_can_be_consciously_closed(tmp_path):
    from research_env.preparation import active_generation
    r,settings,store,run,job,p,s,a,runner=setup(tmp_path,planner=False,review=False)
    # Controlled native boundary: Run/Job exist; pipeline installation not yet committed.
    r.connection.execute('DELETE FROM pipeline_binding WHERE run_id=?',(str(run.id),))
    assert active_generation(r)
    submit(r,'close-unbound',Intent(action='abort',target_id=run.id,decision='TECHNICAL-FIXTURE: explicitly close unstarted preparation'))
    process_command(r,controls=True)
    assert r.state(run.id).execution=='terminal' and r.state(run.id).terminal_cause=='interrupted'
    assert active_generation(r) is None and not r.all(ModelCall)
    assert any(e.event_type=='preparation_aborted' for e in r.all(Event))
    p.close();r.close()


def test_public_diagnostic_redacts_sandbox_credentials_without_mutating_original(register):
    from research_env.preparation import diagnostic
    r,settings=register;result=own_draft(r,'TECHNICAL-FIXTURE credential projection',demo=True)
    proof=ArtifactStore(settings,r).json({'synthetic':True},artifact_type='technical_fixture')
    run,job=r.start_other(UUID(result['phase_id']),UUID(result['version_id']),decision='TECHNICAL-FIXTURE: registry-only diagnosis',technical_evidence_ids=(proof.id,),idempotency_key='credential-projection')
    body=json.dumps({'access_token':'TECHNICAL-FIXTURE-TOKEN','synthetic_database_password':'TECHNICAL-FIXTURE-PASSWORD',
        'nested':{'authorization':'TECHNICAL-FIXTURE-AUTH'},'last_activity':'known','tokens':12,'resources':[{'kind':'container','id':'own-fixture'}]})
    r.connection.execute('INSERT INTO sandbox_execution VALUES(?,?,?,?,?,?,?,?,?)',('credential-projection',str(job.id),str(run.id),'own-fixture','development','internal','interrupted',body,fixture.NOW.isoformat()))
    c=client(r)
    page=c.get('/runs/'+str(run.id)).text
    public=json.dumps(diagnostic(r,run.id))
    artifact=ArtifactStore(settings,r).json({'recorded':json.loads(body)},run_id=run.id,artifact_type='pipeline_stop_diagnosis')
    download=c.get('/artifacts/'+str(artifact.id))
    assert download.status_code==200 and '-public.txt' in download.headers['content-disposition']
    assert json.loads(download.content)['original_sha256']==artifact.sha256
    assert ArtifactStore(settings,r).read(artifact.id)==(json.dumps({'recorded':json.loads(body)},sort_keys=True,separators=(',',':')).encode())
    for secret in ('TECHNICAL-FIXTURE-TOKEN','TECHNICAL-FIXTURE-PASSWORD','TECHNICAL-FIXTURE-AUTH'):
        assert secret not in page and secret not in public and secret not in download.text
    assert 'own-fixture' in public and 'known' in public and '12' in public
    assert r.connection.execute('SELECT body FROM sandbox_execution WHERE id=?',('credential-projection',)).fetchone()[0]==body


@pytest.mark.parametrize('failure',[IntegrityError('TECHNICAL-FIXTURE read changed'),OSError('TECHNICAL-FIXTURE unavailable'),sqlite3.OperationalError('TECHNICAL-FIXTURE locked')])
def test_abort_seal_failure_waits_for_conscious_same_run_recovery(tmp_path,monkeypatch,failure):
    r,settings,store,run,job,p,s,adapter,runner=setup(tmp_path,planner=False,review=False)
    s.tick();s.tick()
    sent=adapter.send_count
    s.request_abort(run.id,reason='TECHNICAL-FIXTURE manual stop',immediate=True)
    original=p._seal
    attempts=[]
    def fail(state):
        attempts.append(dict(state));raise failure
    monkeypatch.setattr(p,'_seal',fail)
    assert s.tick()==run.id
    assert p.binding(run.id)['status']=='recovery_required'
    assert s.tick() is None and len(attempts)==1 and adapter.send_count==sent
    monkeypatch.setattr(p,'_seal',original)
    s.resume(run.id,reason='TECHNICAL-FIXTURE conscious recovery after inspected failure')
    s.tick();s.tick()
    assert p.binding(run.id)['status']=='completed'
    assert r.state(run.id).terminal_cause=='interrupted' and adapter.send_count==sent
    assert len([c for c in r.all(CandidateSnapshot) if c.run_id==run.id and c.role=='sealed'])==1
    p.close();r.close()


def test_failed_connect_closes_real_sqlite_connection_and_descriptor(register,monkeypatch):
    import sqlite3
    from research_env import database
    r,settings=register
    writer=sqlite3.connect(settings.database,isolation_level=None)
    writer.execute('BEGIN EXCLUSIVE')
    original=sqlite3.connect;opened=[]
    def retained(*args,**kwargs):
        value=original(*args,**kwargs);opened.append(value);return value
    def descriptors():
        if not Path('/proc/self/fd').is_dir():return None
        return sum(1 for p in Path('/proc/self/fd').iterdir() if p.is_symlink() and p.readlink()==settings.database)
    baseline=descriptors()
    monkeypatch.setattr(database.sqlite3,'connect',retained)
    try:
        with pytest.raises(sqlite3.OperationalError,match='locked'):database.connect(settings,readonly=True)
        assert len(opened)==1
        with pytest.raises(sqlite3.ProgrammingError,match='closed'):opened[0].execute('SELECT 1')
        # SQLite may defer OS descriptor close while another local handle owns
        # a POSIX lock. Verify release after that actual writer is rolled back.
        writer.rollback()
        assert descriptors()==baseline
    finally:
        for connection in opened:connection.close()
        writer.rollback();writer.close()
    # A real later writer can commit after the failed open and release.
    with r.transaction():r.connection.execute("INSERT INTO runtime_metadata VALUES ('TECHNICAL-FIXTURE-connect-released','yes')")


def test_internal_runner_does_not_dispatch_next_stage_after_persisted_abort(tmp_path):
    from types import SimpleNamespace
    from research_env.pipeline_runner import InternalRunner
    from research_env.sandbox_runtime import RuntimeImages
    r,settings,store,run,job,p,s,adapter,old_runner=setup(tmp_path,planner=False,review=False)
    s.tick();s.tick()
    candidate=next(c for c in r.all(CandidateSnapshot) if c.run_id==run.id)
    tests=store.store(b'<?php // TECHNICAL-FIXTURE',run_id=run.id,access_scope='role')
    observed=store.json({'synthetic':True},run_id=run.id)
    dispatched=[]
    def allocate(*args,profile,**kwargs):
        dispatched.append(profile)
        container=SimpleNamespace(name='fixture-syntax' if profile=='syntax' else 'fixture-candidate',id='own-fixture',attrs={'State':{}},wait=lambda:{'StatusCode':0},reload=lambda:None)
        def abort(**kwargs):
            r.connection.execute("UPDATE pipeline_binding SET status='abort_requested' WHERE run_id=?",(str(run.id),))
        return SimpleNamespace(id='own-fixture',containers=[container],errors=[],requested_stops=set(),start=lambda:None,observe=lambda:observed,abort=abort)
    sandbox=SimpleNamespace(register=r,store=store,images=RuntimeImages(*('sha256:'+'1'*64 for _ in range(4))),allocate=allocate)
    result=InternalRunner(sandbox).execute(job.id,candidate.id,tests.id,node='runner')
    assert dispatched==['syntax'] and result['classification'] in ('protection_failure','technical_failure')
    assert r.connection.execute('SELECT status FROM pipeline_runner WHERE run_id=?',(str(run.id),)).fetchone()[0]=='recovery_required'
    p.close();r.close()


def test_late_sandbox_resource_after_persisted_stop_is_retained_and_not_dispatched(prepared):
    from types import SimpleNamespace
    sandbox,run,job,candidate,*_=prepared
    handle=sandbox.allocate(job.id,candidate.id,profile='syntax')
    resource=SimpleNamespace(id='own-late-volume',name='own-late-volume')
    def create(**kwargs):
        sandbox._update(handle.id,status='recovery_required')
        return resource
    sandbox.docker=SimpleNamespace(volumes=SimpleNamespace(create=create))
    with pytest.raises(IntegrityError,match='Persistenter Sandboxstopp'):handle._volume('code')
    row=sandbox._row(handle.id)
    assert row['status']=='recovery_required'
    assert json.loads(row['body'])['resources']==[{'kind':'volume','id':'own-late-volume','name':'own-late-volume'}]
    # The race-created resource remains bound to this execution for recovery.
    assert handle.volumes==[resource]
    with pytest.raises(IntegrityError,match='Persistenter Sandboxstopp'):handle._dispatch_allowed()


@pytest.mark.parametrize('drift',[False,True])
def test_ui_abort_decision_survives_worker_boot_and_conscious_resume_without_calls(tmp_path,monkeypatch,drift):
    r,settings,store,run,job,p,s,adapter,runner=setup(tmp_path,planner=False,review=False)
    s.tick();s.tick()
    sent=adapter.send_count
    control(r,Intent(action='abort',target_id=run.id,decision='TECHNICAL-FIXTURE conscious UI stop',immediate=False))
    assert p._effect(run.id,'abort') is None
    decision=next(e for e in r.all(Event) if e.event_type=='abort_decision')
    assert decision.details['target_process_id']==str(s.process.id)
    s.boot();assert s.tick() is None
    s.resume(run.id,reason='TECHNICAL-FIXTURE explicit same-run abort seal recovery')
    if drift:
        from research_env.pipeline import PhaseIdentityDrift
        def changed(_):raise PhaseIdentityDrift('TECHNICAL-FIXTURE actual source drift')
        monkeypatch.setattr(p,'preflight',changed)
    s.tick();s.tick()
    assert p.binding(run.id)['status']=='completed' and r.state(run.id).terminal_cause=='interrupted'
    assert adapter.send_count==sent
    events=[e for e in r.all(Event) if e.run_id==run.id]
    assert any(e.event_type=='explicit_stored_abort_recovery' for e in events)
    p.close();r.close()


def test_abort_recovery_rejects_latest_foreign_diagnosis_without_graph_replay(tmp_path):
    r,settings,store,run,job,p,s,adapter,runner=setup(tmp_path,planner=False,review=False)
    s.tick();s.tick();sent=adapter.send_count
    # Explicitly synthetic registry adversarial fixture, outside the normal UI.
    foreign=store.json({'synthetic':True},artifact_type='technical_fixture')
    event=r.add(Event(code='TECHNICAL-FIXTURE invalid latest abort proof',run_id=run.id,sequence=1+max(e.sequence for e in r.all(Event)),
        event_type='abort_decision',interval_id=None,process_id=s.process.id,happened_at=datetime.now(timezone.utc),evidence_ids=(foreign.id,),details={'immediate':True}))
    s.boot();s.resume(run.id,reason='TECHNICAL-FIXTURE inspect invalid abort proof')
    s.tick()
    assert p.binding(run.id)['status']=='recovery_required' and adapter.send_count==sent
    assert p._effect(run.id,'abort') is None and s.tick() is None
    p.close();r.close()


@pytest.mark.parametrize('existing_abort',[False,True])
def test_preflight_seal_failure_waits_for_conscious_recovery_preserving_abort(tmp_path,monkeypatch,existing_abort):
    from research_env.pipeline import PhaseIdentityDrift
    r,settings,store,run,job,p,s,adapter,runner=setup(tmp_path,planner=False,review=False)
    if existing_abort:
        s.tick();s.tick()
        s.request_abort(run.id,reason='TECHNICAL-FIXTURE original immediate stop',immediate=True)
        s.boot();s.resume(run.id,reason='TECHNICAL-FIXTURE original conscious abort recovery')
    sent=adapter.send_count;original=p._seal;attempts=[]
    def preflight(_):raise PhaseIdentityDrift('TECHNICAL-FIXTURE actual identity mismatch')
    def missing(state):attempts.append(dict(state));raise OSError('TECHNICAL-FIXTURE failed preflight Seal')
    monkeypatch.setattr(p,'preflight',preflight);monkeypatch.setattr(p,'_seal',missing)
    assert s.tick()==run.id and p.binding(run.id)['status']=='recovery_required'
    assert s.tick() is None and len(attempts)==1 and adapter.send_count==sent
    monkeypatch.setattr(p,'_seal',original)
    s.resume(run.id,reason='TECHNICAL-FIXTURE renewed conscious same-run completion')
    s.tick()
    assert p.binding(run.id)['status']=='completed' and adapter.send_count==sent
    assert r.state(run.id).terminal_cause==('interrupted' if existing_abort else 'technical_failure')
    p.close();r.close()


def test_progress_poll_refreshes_persisted_diagnosis_and_logs_without_swapping_forms(tmp_path):
    r,settings,store,run,job,p,s,adapter,runner=setup(tmp_path,planner=False,review=False)
    c=client(r)
    initial=BeautifulSoup(c.get('/runs/'+str(run.id)).text,'html.parser')
    controls=initial.find_all('form')
    assert controls and all(f.find_parent(id='progress') is None for f in controls)
    p.event(run.id,'TECHNICAL-FIXTURE-current-node',{'node':'TECHNICAL-FIXTURE persisted current node'})
    log=store.json({'synthetic':True},run_id=run.id,artifact_type='technical_log')
    before=hashlib.sha256(settings.database.read_bytes()).hexdigest()
    fragment=BeautifulSoup(c.get('/runs/'+str(run.id)+'/progress').text,'html.parser')
    refreshed=fragment.find(id='progress')
    assert 'Diagnose für Unterbrechungen' in refreshed.get_text()
    assert 'TECHNICAL-FIXTURE persisted current node' in refreshed.get_text()
    assert refreshed.find('a',href='/artifacts/'+str(log.id))
    assert not refreshed.find('form') and refreshed.get('hx-swap')=='outerHTML'
    assert refreshed.get('hx-trigger')=='every 3s'
    assert hashlib.sha256(settings.database.read_bytes()).hexdigest()==before
    p.close();r.close()

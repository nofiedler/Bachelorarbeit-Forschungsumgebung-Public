"""Explicit synthetic UI contracts; no scientific or human judgment is asserted."""
import hashlib
import json
from decimal import Decimal
from uuid import UUID,uuid4
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
import pytest
import m7_fixture
from research_env.domain import *
from research_env.web import create_app
from research_env.evidence_views import projection

@pytest.fixture
def env(tmp_path):
    value=m7_fixture.make_environment(tmp_path/'instance')
    yield value
    value[0].close()

def client(env):
    c=TestClient(create_app(env[1]));c.get('/');return c

def post(c,path,data):
    return c.post(path,data={'csrf':c.cookies['research_csrf'],**data},headers={'Origin':'http://testserver'},follow_redirects=False)

def completed(c,env,response):
    """Ordinary HTTP submits; trusted persistent worker owns CAS writes."""
    from research_env.preparation import process_command
    assert response.status_code==303
    process_command(env[0])
    return c.get(response.headers['location'],follow_redirects=False)


def setup(env,tmp_path):
    run,comp,raw=m7_fixture.start(env,tmp_path)
    m=m7_fixture.measurement(env,run,comp,raw,key='T4_integration')
    return run,comp,raw,m

def fields(env,run,comp,raw,criterion='T2',**changes):
    return {'idempotency_key':str(uuid4()),'criterion':criterion,'candidate_hash':comp.candidate_hash,
        'rubric_id':str(env[3]['rubric'].id),'tool_id':str(comp.tool_id),'predecessor_id':'','completion':'draft',
        'verdict':'open','reason':'SYNTHETIC open reason','person':'TECHNICAL-FIXTURE:simulated','code_artifact_id':'',
        'file_path':'','lines':'','measurement_id':'','interpretation':'',**changes}

def test_ui_smoke_catalog_planned_and_readonly(env):
    r,settings,store,f,freeze,_=env;c=client(env);pid=freeze.matrix[0]['id']
    before=hashlib.sha256(settings.database.read_bytes()).hexdigest()
    response=c.get('/runs/'+pid+'/evidence')
    assert response.status_code==200 and 'Noch nicht gestartet' in response.text
    assert c.get('/analyses').status_code==200
    assert before==hashlib.sha256(settings.database.read_bytes()).hexdigest()
    assert not r.all(AnalysisRun) and not r.all(ModelCall)

def test_review_positive_without_evidence_draft_and_correction(env,tmp_path):
    r,settings,store,f,freeze,_=env;run,comp,raw,m=setup(env,tmp_path);c=client(env);url=f'/runs/{run.id}/review'
    assert c.get(url).status_code==200
    missing=fields(env,run,comp,raw,completion='completed',verdict='1')
    assert post(c,url,missing).status_code==422 and not r.all(CriterionReviewRevision)
    draft=fields(env,run,comp,raw);first=post(c,url,draft)
    assert first.status_code==303 and post(c,url,draft).headers['location']==first.headers['location']
    original=r.all(CriterionReviewRevision)[0];assert original.completion=='draft'
    complete=fields(env,run,comp,raw,predecessor_id=str(original.id),completion='completed',verdict='0',code_artifact_id=str(raw.id),reason='SYNTHETIC observed negative')
    assert post(c,url,complete).status_code==303
    assert len(r.all(CriterionReviewRevision))==2 and r.get(original.id)==original
    assert post(c,url,{**draft,'reason':'collision'}).status_code==409
    assert post(c,url,fields(env,run,comp,raw,predecessor_id=str(original.id))).status_code==409

def test_T4_needs_independent_integration_and_invalidations(env,tmp_path):
    r,settings,store,f,freeze,_=env;run,comp,raw,m=setup(env,tmp_path);c=client(env);url=f'/runs/{run.id}/review'
    data=fields(env,run,comp,raw,'T4',completion='completed',verdict='1',code_artifact_id=str(raw.id))
    assert post(c,url,data).status_code==422
    assert post(c,url,{**data,'measurement_id':str(m.id)}).status_code==303
    review=r.all(CriterionReviewRevision)[0];assert r.valid(review.id)
    r.add(RevisionInvalidation(code='SYNTHETIC-DEFECT',revision_id=m.id,reason='SYNTHETIC confirmed measurement defect',person='TECHNICAL-FIXTURE:test',defect_evidence_id=raw.id))
    assert not r.valid(review.id)
    html=c.get(url).text;assert 'Belegstatus: ungültig oder noch offen' in html
    assert r.analysis_inputs(freeze.id)[str(run.planned_run_id)]['reviews']['T4']['revision_id'] is None

def test_safe_archives_ids_xss_csrf_and_no_get_work(env,tmp_path):
    r,settings,store,f,freeze,_=env;run,comp,raw,m=setup(env,tmp_path)
    a=store.store(b'<script>fetch("/actions")</script><img src=x onerror=alert(1)>',run_id=run.id,artifact_type='evaluation_html',producer='trusted_evaluator',access_scope='trusted_evaluator',original_name='../../<img>.html',mime_type='text/html')
    c=client(env);base=f'/runs/{run.id}/evidence';before=hashlib.sha256(settings.database.read_bytes()).hexdigest()
    text=c.get(f'{base}/artifacts/{a.id}').text
    assert '&lt;script&gt;' in text and '<script>fetch' not in text
    response=c.get(f'{base}/artifacts/{a.id}?download=true');assert response.content==store.read(a.id) and response.headers['content-type'].startswith('text/plain')
    assert c.get(f'{base}/artifacts/{uuid4()}').status_code==409
    other,_,_=m7_fixture.start(env,tmp_path)
    assert c.get(f'/runs/{other.id}/evidence/artifacts/{a.id}').status_code==409
    checkpoint=hashlib.sha256(settings.database.read_bytes()).hexdigest()
    for path in (base,f'{base}/records/{m.id}',f'{base}/snapshots/{comp.candidate_id}',f'/runs/{run.id}/review','/analyses'):
        assert c.get(path).status_code==200
    assert checkpoint==hashlib.sha256(settings.database.read_bytes()).hexdigest()
    data=fields(env,run,comp,raw)
    assert c.post(f'/runs/{run.id}/review',data=data,headers={'Origin':'http://evil.example'}).status_code==409
    assert not r.all(CriterionReviewRevision)

def test_backup_draft_stale_and_current_does_not_block_open(env,tmp_path):
    r,settings,store,f,freeze,_=env;run,comp,raw,m=setup(env,tmp_path);c=client(env)
    m7_fixture.secure(env,tmp_path/'current');assert r.backup_status(freeze.id)=='current'
    assert post(c,f'/runs/{run.id}/review',fields(env,run,comp,raw)).status_code==303
    assert r.backup_status(freeze.id)=='stale'
    assert 'Sicherung veraltet' in c.get(f'/runs/{run.id}/review').text
    m7_fixture.secure(env,tmp_path/'draft-secure');assert r.backup_status(freeze.id)=='current'
    next_id=r.next_id(freeze.id)
    assert next_id and c.get(f'/runs/{run.id}/review').status_code==200

def test_analysis_fixed_proposal_confirmation_historical_downloads(env,tmp_path):
    r,settings,store,f,freeze,_=env;run,comp,raw,m=setup(env,tmp_path);c=client(env)
    older=m7_fixture.review(env,run,comp,raw,'T2',verdict=0)
    draft=m7_fixture.review(env,run,comp,raw,'T2',completion='draft')
    request={'idempotency_key':'proposal-one','freeze_id':str(freeze.id)}
    response=post(c,'/analyses/propose',request);assert response.status_code==303
    assert post(c,'/analyses/propose',request).headers['location']==response.headers['location']
    assert not r.connection.execute('SELECT 1 FROM analysis_selection').fetchone()
    response=completed(c,env,response)
    html=c.get(response.headers['location']).text
    assert str(older.id) in html and str(draft.id) in html and 'Entwurf' in html
    soup=BeautifulSoup(html,'html.parser');action=soup.find('form')['action']
    data={x['name']:x.get('value','') for x in soup.find('form').find_all('input') if x.get('name')}
    data.update(confirm='true',person='TECHNICAL-FIXTURE:ui',decision='SYNTHETIC exact IDs and missing values acknowledged')
    queued=post(c,action,data);assert queued.status_code==303
    assert post(c,action,data).headers['location']==queued.headers['location']
    assert not r.all(AnalysisRun)
    first=completed(c,env,queued)
    analysis=r.all(AnalysisRun)[0];assert len(r.all(AnalysisRun))==1
    originals={a.id:store.read(a.id) for a in r.all(Artifact) if a.id in analysis.result_artifact_ids}
    html=c.get(first.headers['location']);assert html.status_code==200
    archive=BeautifulSoup(html.text,'html.parser').select_one('.analysis-historical-exports')
    assert archive is not None and archive.find('h2').get_text(strip=True)=='Archivierte Ausgaben'
    assert {a['href'] for a in archive.select('a[download]')}=={
        f'/analyses/{analysis.id}/outputs/{aid}' for aid in originals}
    for aid,body in originals.items():assert c.get(f'/analyses/{analysis.id}/outputs/{aid}').content==body
    assert c.get(f'/analyses/{analysis.id}/outputs/{raw.id}').status_code==409
    m7_fixture.review(env,run,comp,raw,'T2',verdict=1)
    assert all(store.read(aid)==body for aid,body in originals.items())
    assert post(c,'/analyses/propose',{**request,'idempotency_key':'illegal','revision_id':str(older.id)}).status_code==409

def test_catalog_all_keys_observation_leaves_and_disabled_roles(env,tmp_path):
    from research_env.evidence import catalog
    r,settings,store,f,freeze,_=env;run,comp,raw,m=setup(env,tmp_path)
    m7_fixture.review(env,run,comp,raw,'T2',completion='draft')
    view=projection(r,run.id)
    assert {v['evidence_key'] for v in view['catalog_rows']}=={e['evidence_key'] for e in catalog()['entries']}
    leaf=[v for v in view['catalog_rows'] if v['evidence_key']=='CriterionReviewRevision.verdict.value' and v['record_id']]
    assert leaf and all(v['status']=='problematisch' and 'pending' in v['reason'] for v in leaf)
    absent=[v for v in view['catalog_rows'] if v.get('scope')=='repair']
    assert absent and all(v['status']=='nicht anwendbar' and not v['required'] for v in absent)
    # Main C-SQL-1 has both optional roles enabled. Planned IDs have no due gaps.
    planned=projection(r,UUID(freeze.matrix[1]['id']))
    assert not any(v['status']=='fehlt' for v in planned['catalog_rows'])


def test_code_line_binding_foreign_candidate_and_modeltext_rejected(env,tmp_path):
    r,settings,store,f,freeze,_=env;run,comp,raw,m=setup(env,tmp_path);c=client(env);url=f'/runs/{run.id}/review'
    candidate=r.get(comp.candidate_id,CandidateSnapshot);code=r.get(candidate.artifact_ids[0],Artifact)
    good=fields(env,run,comp,raw,completion='completed',verdict='1',code_artifact_id=str(code.id),file_path=code.original_name,lines='1')
    assert post(c,url,{**good,'lines':'99'}).status_code==409
    assert post(c,url,{**good,'file_path':'../escape'}).status_code==409
    assert post(c,url,{**good,'candidate_hash':'0'*64}).status_code==409
    model=store.store(b'SYNTHETIC model says pass',run_id=run.id,artifact_type='model_response')
    assert post(c,url,{**good,'code_artifact_id':str(model.id)}).status_code==409
    assert post(c,url,good).status_code==303
    assert r.all(CriterionReviewRevision)[0].file_path==code.original_name


def test_stale_proposal_no_silent_analysis_or_free_revision_choice(env,tmp_path):
    r,settings,store,f,freeze,_=env;run,comp,raw,m=setup(env,tmp_path);c=client(env)
    response=post(c,'/analyses/propose',{'idempotency_key':'old','freeze_id':str(freeze.id)})
    response=completed(c,env,response)
    soup=BeautifulSoup(c.get(response.headers['location']).text,'html.parser');form=soup.find('form');data={x['name']:x.get('value','') for x in form.find_all('input')}
    data.update(confirm='true',person='TECHNICAL-FIXTURE:test',decision='SYNTHETIC outdated proposal')
    m7_fixture.review(env,run,comp,raw,'T3',verdict=0)
    queued=post(c,form['action'],data);assert queued.status_code==303
    failed=completed(c,env,queued)
    assert failed.status_code==200 and 'stale' in failed.text
    assert not r.all(AnalysisRun)


def test_free_development_archive_and_no_holdout_no_fq(env,tmp_path):
    import test_register as fixtures
    r,settings,store,f,freeze,_=env
    run=fixtures.free_run(r,f)
    a=store.store(b'SYNTHETIC development result only',run_id=run.id,artifact_type='evaluation_development_result',producer='trusted_evaluator',access_scope='public_development')
    c=client(env);html=c.get(f'/runs/{run.id}/evidence')
    assert html.status_code==200 and 'Kein Forschungsdatensatz' in html.text
    assert c.get(f'/runs/{run.id}/evidence/artifacts/{a.id}').status_code==200
    holdout=r.get(f['settings'].holdout_suite_id,AssetVersion)
    for aid in holdout.artifact_ids:assert c.get(f'/runs/{run.id}/evidence/artifacts/{aid}').status_code==409
    assert str(run.id) not in {str(v) for v in r.fq_ids(freeze.id)}
    assert post(c,'/analyses/propose',{'idempotency_key':'free','freeze_id':str(run.phase_id)}).status_code==409


def test_damaged_artifact_visible_and_unsafe_download_denied(env,tmp_path):
    from research_env.artifacts import ArtifactStore
    r,settings,store,f,freeze,_=env;run,comp,raw,m=setup(env,tmp_path);c=client(env)
    path=settings.artifacts/ArtifactStore.object_path(raw.sha256)
    original=path.read_bytes();path.chmod(0o600);path.write_bytes(b'damaged')
    view=projection(r,run.id)
    assert any(v['record_id']==str(raw.id) and v['status']=='problematisch' and 'damaged_artifact' in v['reason'] for v in view['catalog_rows'])
    assert c.get(f'/runs/{run.id}/evidence/artifacts/{raw.id}?download=true').status_code==422
    path.write_bytes(original);path.chmod(0o444)

def test_saved_analysis_catalog_uses_shared_dict_model_traversal(env,tmp_path):
    from research_env.evidence import validate_entry,catalog
    from research_env.analysis_store import Analyses
    from research_env.adapter import CallJournal
    r,settings,store,f,freeze,_=env;run,comp,raw,m=setup(env,tmp_path)
    service=Analyses(r,CallJournal.cost_view(r));proposal=service.propose(freeze.id)
    analysis=service.confirm(proposal['proposal_id'],expected_input_hash=proposal['input_hash'],acknowledged_selection=proposal['selected_revisions'],acknowledged_open=proposal['open_decisions'],confirmed_by='TECHNICAL-FIXTURE:shared traversal',decision='SYNTHETIC exact immutable stand',software_commit='synthetic-test',synthetic=True)
    assert validate_entry('AnalysisRun.denominators.status',analysis.model_dump(mode='json'))==['observed']*5
    # Every original catalog path must traverse every real containing record.
    view=projection(r,run.id)
    for row in view['catalog_rows']:
        if row['record_id']==str(analysis.id):assert row['status']!='fehlt'
    assert client(env).get(f'/runs/{run.id}/evidence').status_code==200


def test_phase_owned_effort_history_and_source_navigation(env,tmp_path):
    r,settings,store,f,freeze,_=env;run,comp,raw,m=setup(env,tmp_path)
    source=store.store(b'SYNTHETIC measured phase preparation source',artifact_type='context_preparation_log')
    metric=r.add(MetricObservation(code='SYNTHETIC-CONTEXT-EFFORT',run_id=None,phase_id=run.phase_id,metric='context_preparation_seconds',value=NumericObservation(status='observed',value=Decimal('12.5'),unit='s',source='SYNTHETIC independent context effort fixture'),measurement_id=None,interval_ids=(),file_scope=(),excluded_files=(),configuration_hash=None,diagnostics_artifact_id=source.id))
    history=r.set_phase_status(run.phase_id,'paused',reason='SYNTHETIC explicit phase lifecycle fixture')
    view=projection(r,run.id)
    assert view['records'][history.id]==history
    assert view['records'][metric.id]==metric and source.id in view['records']
    assert any(isinstance(v,PhaseStateRevision) for v in view['records'].values())
    matching=[v for v in view['catalog_rows'] if v['record_id']==str(metric.id) and v['evidence_key']=='MetricObservation.value.value']
    assert matching and matching[0]['value']==['12.5'] and matching[0]['status']=='gespeichert'
    c=client(env)
    html=c.get(f'/runs/{run.id}/evidence/records/{metric.id}').text
    assert f'/artifacts/{source.id}' in html
    assert c.get(f'/runs/{run.id}/evidence/artifacts/{source.id}').status_code==200


def test_current_integrity_propagates_to_measurement_T4_and_open_queue(env,tmp_path):
    from research_env.review_ui import review_view
    r,settings,store,f,freeze,_=env;run,comp,raw,m=setup(env,tmp_path)
    review=m7_fixture.review(env,run,comp,raw,'T4',verdict=1,measurement_ids=(m.id,))
    assert review_view(r,run.id)['validity'][str(review.id)]
    path=settings.artifacts/store.object_path(raw.sha256);original=path.read_bytes();path.chmod(0o600);path.write_bytes(b'SYNTHETIC damaged log')
    view=projection(r,run.id)
    for rid in (raw.id,m.id,review.id):
        rows=[v for v in view['catalog_rows'] if v['record_id']==str(rid)]
        assert rows and all(v['status']=='problematisch' for v in rows)
    form=review_view(r,run.id)
    assert not form['validity'][str(review.id)] and 'T4' in form['open_reviews'] and not form['integration']
    c=client(env)
    assert 'Beleg beschädigt' in c.get(f'/runs/{run.id}/review').text
    assert 'T2, T3, T4' in c.get('/analyses').text
    assert r.get(review.id)==review and r.valid(review.id)  # Persisted original is unchanged.
    path.write_bytes(original);path.chmod(0o444)
    assert review_view(r,run.id)['validity'][str(review.id)]

@pytest.mark.parametrize('entrypoint',('review','analysis'))
def test_new_correction_with_intact_proofs_after_damaged_predecessor(env,tmp_path,entrypoint):
    from research_env.review_ui import review_view,save_review
    from research_env.analysis_store import Analyses
    from research_env.adapter import CallJournal
    r,settings,store,f,freeze,_=env;run,comp,oldraw,oldintegration=setup(env,tmp_path)
    oldfunctional=m7_fixture.measurement(env,run,comp,oldraw)
    oldreview=m7_fixture.review(env,run,comp,oldraw,'T4',verdict=1,measurement_ids=(oldintegration.id,))
    service=Analyses(r,CallJournal.cost_view(r));oldproposal=service.propose(freeze.id)
    oldanalysis=service.confirm(oldproposal['proposal_id'],expected_input_hash=oldproposal['input_hash'],acknowledged_selection=oldproposal['selected_revisions'],acknowledged_open=oldproposal['open_decisions'],confirmed_by='TECHNICAL-FIXTURE:old stand',decision='SYNTHETIC old exact stand',software_commit='synthetic-test',synthetic=True)
    oldoutputs={aid:store.read(aid) for aid in oldanalysis.result_artifact_ids}
    defect=store.store(b'SYNTHETIC confirmed historical raw defect; replacement instrument unchanged',run_id=run.id,artifact_type='evaluation_defect_log',producer='trusted_evaluator',access_scope='trusted_evaluator')
    for value in (oldintegration,oldfunctional):r.add(RevisionInvalidation(code='SYNTHETIC-DEFECT-'+str(uuid4()),revision_id=value.id,reason='SYNTHETIC old raw proof damaged',person='TECHNICAL-FIXTURE:simulated defect decision',defect_evidence_id=defect.id))
    oldpath=settings.artifacts/store.object_path(oldraw.sha256);oldbytes=oldpath.read_bytes();oldpath.chmod(0o600);oldpath.write_bytes(b'SYNTHETIC damaged historical bytes')
    newraw=store.json({'origin':'SYNTHETIC new independent intact proof','revision':2},run_id=run.id,artifact_type='evaluation_hand_evidence',producer='trusted_evaluator',access_scope='trusted_evaluator')
    newintegration=m7_fixture.measurement(env,run,comp,newraw,key='T4_integration')
    newfunctional=m7_fixture.measurement(env,run,comp,newraw)
    assert newintegration.predecessor_id==oldintegration.id
    if entrypoint=='analysis':service.propose(freeze.id)
    view=review_view(r,run.id)
    assert newintegration.id in {m.id for m in view['integration']}
    candidate=r.get(comp.candidate_id,CandidateSnapshot);code=r.get(candidate.artifact_ids[0],Artifact)
    data=fields(env,run,comp,newraw,'T4',completion='completed',verdict='0',predecessor_id=str(oldreview.id),code_artifact_id=str(code.id),file_path=code.original_name,lines='1',measurement_id=str(newintegration.id),reason='SYNTHETIC corrected current judgment with intact new proof')
    newreview=r.get(UUID(save_review(r,run.id,data)),CriterionReviewRevision)
    assert newreview.predecessor_id==oldreview.id and review_view(r,run.id)['validity'][str(newreview.id)]
    proposal=service.propose(freeze.id)
    selected=proposal['selected_revisions'][str(run.planned_run_id)]
    assert selected['measurements']['T4_integration']['revision_id']==str(newintegration.id)
    assert selected['measurements']['functional_R']['revision_id']==str(newfunctional.id)
    assert selected['reviews']['T4']['revision_id']==str(newreview.id)
    confirmed=service.confirm(proposal['proposal_id'],expected_input_hash=proposal['input_hash'],acknowledged_selection=proposal['selected_revisions'],acknowledged_open=proposal['open_decisions'],confirmed_by='TECHNICAL-FIXTURE:new stand',decision='SYNTHETIC exact correction stand',software_commit='synthetic-test',synthetic=True)
    assert confirmed.id!=oldanalysis.id and all(store.read(aid)==body for aid,body in oldoutputs.items())
    assert r.get(oldanalysis.id)==oldanalysis and r.get(oldreview.id)==oldreview
    archived=projection(r,run.id)
    assert oldintegration.id in archived['records'] and oldraw.id in archived['records']
    assert any(v['record_id']==str(oldintegration.id) and v['status']=='problematisch' for v in archived['catalog_rows'])
    # The correction's OWN current proof remains mandatory.
    newpath=settings.artifacts/store.object_path(newraw.sha256);newbytes=newpath.read_bytes();newpath.chmod(0o600);newpath.write_bytes(b'SYNTHETIC damaged CURRENT proof')
    assert not review_view(r,run.id)['validity'][str(newreview.id)]
    with pytest.raises((OSError,ValueError)):service.propose(freeze.id)
    newpath.write_bytes(newbytes);newpath.chmod(0o444);oldpath.write_bytes(oldbytes);oldpath.chmod(0o444)


def test_guided_review_resolves_code_path_and_keeps_evidence_checks(env,tmp_path):
    r,settings,store,f,freeze,_=env;run,comp,raw,m=setup(env,tmp_path);c=client(env)
    from research_env.review_ui import review_view
    code=review_view(r,run.id)['code_files'][0]
    data=fields(env,run,comp,raw,completion='completed',verdict='0',code_artifact_id=str(code.id),lines='1')
    data.pop('file_path')
    response=post(c,f'/runs/{run.id}/review-guided',data)
    assert response.status_code==303
    review=r.all(CriterionReviewRevision)[-1]
    assert review.file_path==code.original_name and review.code_artifact_id==code.id
    assert post(c,f'/runs/{run.id}/review-guided',data).headers['location']==response.headers['location']


def test_guided_review_has_one_decision_and_consistent_run_navigation(env,tmp_path):
    r,settings,store,fixtures,freeze,_=env;run,comp,raw,m=setup(env,tmp_path);c=client(env)
    prefix=f'/runs/{run.id}'
    original=hashlib.sha256(settings.database.read_bytes()).hexdigest()
    destinations=set()
    for suffix in ('/review','/code','/configuration','/results','/evidence'):
        response=c.get(prefix+suffix)
        assert response.status_code==200,response.text
        soup=BeautifulSoup(response.text,'html.parser')
        links={a['href'] for a in soup.select('nav[aria-label="Bereiche dieses Laufs"] a')}
        assert len(links)==6
        if destinations:assert destinations==links
        destinations=links
        assert len(soup.select('nav[aria-label="Bereiche dieses Laufs"] [aria-current="page"]'))==1
        assert 'P · Paket' in soup.select_one('.config-chips').text
    page=BeautifulSoup(c.get(prefix+'/review').text,'html.parser')
    assert len(page.select('form[action$="review-guided"]'))==1
    assert page.select_one('input[name="criterion"]')['value']=='T2'
    assert len(page.select('input[name="verdict"]:checked'))==1
    assert page.select_one('input[name="verdict"]:checked')['value']=='open'
    assert not page.select('pre')
    assert c.get(prefix+'/review?criterion=not-real').status_code==409
    assert 'T4' in c.get(prefix+'/review?criterion=T4').text
    assert original==hashlib.sha256(settings.database.read_bytes()).hexdigest()
    payload=fields(env,run,comp,raw,completion='completed',verdict='0',code_artifact_id=str(raw.id),person='Codex Test',reason='SYNTHETIC test only')
    payload.pop('file_path')
    response=post(c,prefix+'/review-guided',payload)
    assert response.status_code==303
    next_page=BeautifulSoup(c.get(response.headers['location']).text,'html.parser')
    assert next_page.select_one('input[name="criterion"]')['value']=='T3'
    assert r.all(CriterionReviewRevision)[0].person=='TECHNICAL-FIXTURE: Codex Test'


def test_guided_review_validation_keeps_human_input(env,tmp_path):
    r,settings,store,f,freeze,_=env;run,comp,raw,m=setup(env,tmp_path);c=client(env)
    from research_env.review_ui import review_view
    code=review_view(r,run.id)['code_files'][0]
    data=fields(env,run,comp,raw,completion='completed',verdict='1',code_artifact_id=str(code.id),lines='9999',reason='Meine konkrete Beobachtung bleibt erhalten')
    data.pop('file_path')
    response=post(c,f'/runs/{run.id}/review-guided',data)
    assert response.status_code==409
    soup=BeautifulSoup(response.text,'html.parser')
    assert soup.select_one('textarea[name=reason]').text==data['reason']
    assert soup.select_one('input[name=lines]')['value']=='9999'
    assert soup.select_one('.form-error:not([hidden])')
    assert soup.select_one('select[name=code_artifact_id] option[selected]')['value']==str(code.id)
    assert not r.all(CriterionReviewRevision)

"""Selection removal and bounded reading must preserve scientific history."""
from uuid import UUID,uuid4
from bs4 import BeautifulSoup
import pytest
from test_register import register
from test_preparation import client,post
from research_env.application_settings import initialize
from research_env.domain import ModelPackage,ConfigurationVersion,Study,StudyPhase,Run,Artifact
from research_env.catalog_ui import available
from research_env.preparation_forms import simple_free
from research_env.preparation import submit,process_command,own_draft


def test_remove_model_and_configuration_preserves_originals(register):
    r,_=register;initialize(r);model=r.all(ModelPackage)[0]
    key,intent=simple_free(r,{'idempotency_key':'select','title':'My choices','module':'SQL','context':'K0',
        'producer':'A','verifier':'A','model_a':str(model.id),'model_b':str(model.id)})
    submit(r,key,intent);process_command(r)
    version=r.all(ConfigurationVersion)[-1];before=version.model_dump_json();c=client(r)
    assert post(c,{},f'/catalog/{version.id}/remove').status_code==303
    assert not available(r,ConfigurationVersion)
    assert r.get(version.id).model_dump_json()==before
    assert c.get(f'/configurations/{version.id}').status_code==200
    assert post(c,{},f'/catalog/{model.id}/remove').status_code==303
    assert r.get(model.id)==model and not available(r,ModelPackage)
    soup=BeautifulSoup(c.get('/free-tests').text,'html.parser')
    assert not soup.select('select[name=model_a] option')
    with pytest.raises(ValueError,match='entfernt'):simple_free(r,{'idempotency_key':'stale','title':'Old selection','module':'SQL','context':'K0','model_a':str(model.id),'model_b':str(model.id)})


def test_study_removal_requires_exact_name_and_preserves_matrix(register):
    r,_=register;result=own_draft(r,'A deliberate name');sid=UUID(result['study_id']);c=client(r)
    versions=r.all(ConfigurationVersion)
    assert post(c,{'confirmation':'wrong'},f'/catalog/{sid}/remove').status_code==409
    assert len(available(r,Study))==1
    assert post(c,{'confirmation':'A deliberate name'},f'/catalog/{sid}/remove').status_code==303
    assert not available(r,Study) and r.get(sid).title=='A deliberate name'
    assert r.all(ConfigurationVersion)==versions
    assert c.get('/studies/'+str(sid)).status_code==200


def test_study_scope_is_explicit_and_validated(register):
    r,_=register;result=own_draft(r,'Scope');sid=UUID(result['study_id']);phase=r.all(StudyPhase)[0];c=client(r)
    data={'phase_id':str(phase.id),'r_c':'2','r_e':'3','seed':'chosen-by-user'}
    assert post(c,data,f'/studies/{sid}/scope').status_code==409
    assert post(c,{**data,'r_e':'1'},f'/studies/{sid}/scope').status_code==303
    page=c.get('/studies/'+str(sid));assert page.status_code==200
    assert '18 geplante Hauptläufe' in page.text
    assert len(r.all(ConfigurationVersion))==12 and not r.all(Run)
    soup=BeautifulSoup(page.text,'html.parser')
    form=soup.select_one('form[action="/freeze/preview"]')
    assert not form.select('[name=r_c], [name=r_e], [name=seed]')
    assert form.select_one('input[name=scope_token]')['value']


@pytest.mark.parametrize('completion,label', [('manual_pending','Bereit zur Bewertung'),
    ('running','Automatische Prüfung läuft'),('failed','Prüfung unvollständig'),('complete','Abgeschlossen')])
def test_run_filters_use_displayed_status_and_configuration(tmp_path,completion,label):
    from test_pipeline import setup,finish
    r,settings,store,run,job,pipeline,scheduler,adapter,runner=setup(tmp_path,planner=True,review=False)
    try:
        finish(scheduler,run);c=client(r)
        r.connection.execute('INSERT INTO run_completion(run_id,status,step,updated_at) VALUES(?,?,?,?)',
            (str(run.id),completion,'TECHNICAL-FIXTURE',run.started_at.isoformat()))
        page=BeautifulSoup(c.get('/free-tests').text,'html.parser')
        options={x.get_text(strip=True):x['value'] for x in page.select('.filters select[name=status] option')}
        assert label in options
        assert not page.select('.filters [name=phase], .filters [name=config], .filters [name=purpose]')
        model=page.select_one('.filters select[name=model] option[value]:not([value=""])')['value']
        version=r.get(run.configuration_version_id,ConfigurationVersion)
        query={'status':options[label],'planner':'on','review':'off','module':version.cell.module,'model':model,'q':str(run.id)}
        result=BeautifulSoup(c.get('/free-tests',params=query).text,'html.parser')
        assert [x.get_text(strip=True) for x in result.select('tbody .status-badge')]==[label]
        assert result.select_one('.filters [name=planner] option[selected]')['value']=='on'
        for field,value in [('planner','off'),('review','on'),('model','missing-model'),('q','no-such-run')]:
            negative=BeautifulSoup(c.get('/free-tests',params={**query,field:value}).text,'html.parser')
            assert not negative.select('tbody .status-badge')
    finally:
        pipeline.close();r.close()


def test_evidence_index_never_reads_all_artifacts_and_is_bounded(tmp_path,monkeypatch):
    from test_pipeline import setup,finish
    from research_env import evidence_views
    r,settings,store,run,job,pipeline,scheduler,adapter,runner=setup(tmp_path)
    finish(scheduler,run)
    for i in range(83):store.store(f'entry {i}'.encode(),run_id=run.id,artifact_type='ui_test_log',original_name=f'bounded-{i}.txt')
    monkeypatch.setattr(evidence_views,'read_artifact',lambda *args:pytest.fail('Overview must not read file bytes'))
    c=client(r);page=c.get(f'/runs/{run.id}/evidence?category=artifacts&q=bounded-')
    assert page.status_code==200
    soup=BeautifulSoup(page.text,'html.parser')
    assert len(soup.select('tbody tr'))==40
    assert '83 Einträge' in page.text and len(page.content)<70000
    assert c.get(f'/runs/{run.id}/evidence?category=artifacts&q=bounded-&page=3').text.count('ui_test_log')==3
    pipeline.close();r.close()


def test_historical_configuration_explains_without_future_start_warning(tmp_path):
    from test_pipeline import setup,finish
    r,settings,store,run,job,pipeline,scheduler,adapter,runner=setup(tmp_path)
    finish(scheduler,run);c=client(r)
    page=c.get(f'/runs/{run.id}/configuration');assert page.status_code==200
    assert 'seit dem Speichern aktualisiert' not in page.text
    assert 'Mit genau diesen Bedingungen' in page.text
    assert 'Produzent' in page.text or 'P ·' in page.text
    pipeline.close();r.close()


def test_tiered_prices_keep_base_input_price(register,monkeypatch):
    import json
    from types import SimpleNamespace
    from research_env.application_settings import model_prices
    from research_env.artifacts import ArtifactStore
    r,_=register
    model=SimpleNamespace(id=uuid4(),metadata_evidence_ids=(uuid4(),))
    raw={'pricing':{'completion':'0.000015','overrides':[{'min_prompt_tokens':200000,'prompt':'0.000006'}],'prompt':'0.000003','web_search':'0.01'}}
    monkeypatch.setattr(ArtifactStore,'read',lambda self,identity:json.dumps(raw).encode())
    price=model_prices(r,[model])[str(model.id)]
    assert price['prompt']=='3' and price['completion']=='15'
    assert price['tiers']==[{'threshold':200000,'prompt':'6'}]
    assert 'web_search' not in price


def test_each_configuration_view_allows_a_new_explicit_start_without_starting(register):
    r,_=register;initialize(r);model=r.all(ModelPackage)[0]
    key,intent=simple_free(r,{'idempotency_key':'repeat-ui','title':'Repeat choice','module':'SQL','context':'K0','model_a':str(model.id),'model_b':str(model.id)})
    submit(r,key,intent);process_command(r);version=r.all(ConfigurationVersion)[-1];c=client(r)
    keys=[]
    for _ in range(2):
        page=BeautifulSoup(c.get(f'/configurations/{version.id}').text,'html.parser')
        keys.append(page.select_one('form[action="/actions"] input[name=idempotency_key]')['value'])
    assert keys[0]!=keys[1]
    assert not r.all(Run)

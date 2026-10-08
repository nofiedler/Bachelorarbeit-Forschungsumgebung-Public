"""User workflow contracts: choices, secret isolation and exactly-once measurements."""
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from bs4 import BeautifulSoup

from research_env.application_settings import initialize, default_settings, save_key, model_key, secret_path, lookup_model, import_model
from research_env.domain import AssetVersion, ModelPackage, Run, Study
from research_env.preparation_forms import simple_free
from research_env.preparation import submit, process_command
from test_register import register
from test_preparation import client, post


def test_structured_role_contracts_are_frozen_for_new_configurations_only(register):
    from research_env.application_settings import preferences
    from research_env.role_formats import structured_parameters
    from research_env.domain import canonical
    r, _ = register
    initialize(r)
    previous = default_settings(r)
    before = canonical(previous)
    params = structured_parameters({'all': {'temperature': 0, 'response_format': {'type': 'json_object'}},
                                    'test': {'seed': 42}})
    assert params['all'] == {'temperature': 0}
    assert params['test']['seed'] == 42
    for role in ('analyzer', 'planner', 'migrate', 'test', 'review', 'repair'):
        response = params[role]['response_format']
        assert response['type'] == 'json_schema' and response['json_schema']['strict'] is True
        schema = response['json_schema']['schema']
        assert schema['properties']['role']['enum'] == [role]
        assert schema['properties']['schema']['enum'] == ['roles-v1']
        assert schema['additionalProperties'] is False
        assert set(schema['required']) == set(schema['properties'])
    import re
    path_rule = params['test']['response_format']['json_schema']['schema']['properties']['files']['items']['properties']['path']['pattern']
    assert re.fullmatch(path_rule, 'internal/check.php')
    for rejected in ('routes/study.php', 'app/Http/Controllers/Study/SqlController.php',
                     'resources/views/study/sqli.blade.php', 'internal/../routes/study.php'):
        assert not re.fullmatch(path_rule, rejected)
    c = client(r)
    assert post(c, {}, '/settings/structured-outputs').status_code == 303
    assert preferences(r)['role_parameters']['test']['response_format']['type'] == 'json_schema'
    assert canonical(previous) == before


def test_simple_form_resolves_all_six_packages_and_keeps_constants(register):
    r,_=register
    initialize(r)
    defaults=default_settings(r)
    model=r.all(ModelPackage)[0]
    for module in ('BF','SQL','UP'):
        for context in ('K0','K1'):
            key,intent=simple_free(r,{'idempotency_key':str(uuid4()),'title':'Mein Test','module':module,'context':context,
                'model_a':str(model.id),'model_b':str(model.id),'producer':'B','verifier':'A','planner':'true'})
            assert intent.settings.context_ids=={f'{module}-{context}':defaults.context_ids[f'{module}-{context}']}
            assert not intent.settings.holdout_suite_id and not intent.settings.reference_id
            assert len(intent.settings.tool_ids)==2
            command=submit(r,key,intent);process_command(r)
            assert r.connection.execute('SELECT status FROM ui_command WHERE id=?',(command,)).fetchone()[0]=='completed'
    c=client(r)
    page=c.get('/free-tests')
    assert page.status_code==200
    fields={x.get('name') for x in BeautifulSoup(page.text,'html.parser').select('form[action="/test-configurations"] [name]')}
    assert fields=={'csrf','idempotency_key','title','module','context','model_a','model_b','producer','verifier','planner','review'}
    assert c.get('/settings').status_code==200


def test_secret_never_enters_database_or_page(register):
    r,_=register;c=client(r)
    secret='sk-or-v1-'+'TESTsecret0'*8
    result=post(c,{'api_key':secret},'/settings/key')
    assert result.status_code==303
    assert model_key(r.settings)==secret
    assert secret_path(r.settings).stat().st_mode & 0o777==0o600
    assert secret.encode() not in r.settings.database.read_bytes()
    assert secret not in c.get('/settings').text
    assert post(c,{},'/settings/key/remove').status_code==303
    assert not secret_path(r.settings).exists()


def test_provider_metadata_is_versioned_and_exact(register):
    r,_=register
    endpoint={'model_id':'example/model-2026','provider_name':'Example','tag':'example/eu','context_length':4000,'max_completion_tokens':2000,
        'pricing':{'prompt':'0.000001','completion':'0.000002'},'supported_parameters':['temperature']}
    transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'data':{'id':'example/model-2026','endpoints':[endpoint]}}))
    with httpx.Client(transport=transport) as c:lookup_model(r,'example/model-2026',client=c)
    import_model(r,'example/model-2026','example/eu',{'temperature':0})
    model=r.all(ModelPackage)[0]
    assert model.routing=={'only':['example/eu'],'order':['example/eu'],'allow_fallbacks':False,'require_parameters':True}
    assert model.price.status=='unresolved'
    with pytest.raises(ValueError):import_model(r,'example/model-2026','example',{})
    with pytest.raises(ValueError):import_model(r,'example/model-2026','example/eu',{'max_tokens':20})
    assert len(r.all(ModelPackage))==1


def test_completion_runs_only_after_seal_and_never_repeats(tmp_path,monkeypatch):
    from test_pipeline import setup,finish
    from research_env.completion import CompletionWorker
    import research_env.completion as module
    r,settings,store,run,job,pipeline,scheduler,adapter,runner=setup(tmp_path)
    sandbox=SimpleNamespace(register=r,store=store,images=SimpleNamespace())
    worker=CompletionWorker(r,sandbox)
    assert worker.tick() is False
    finish(scheduler,run)
    monkeypatch.setattr(module,'functional_manifest',lambda images:{})
    monkeypatch.setattr(module,'static_manifest',lambda images:{})
    monkeypatch.setattr(worker,'tool',lambda *args:uuid4())
    monkeypatch.setattr(worker,'control',lambda:uuid4())
    actions=[]
    class Functional:
        def schedule(self,*args,**kwargs):actions.append('functional schedule');return str(uuid4())
        def run(self,*args,**kwargs):actions.append('functional run');return {}
    class Static:
        def schedule(self,*args,**kwargs):actions.append('static schedule');return str(uuid4())
        def run(self,*args,**kwargs):actions.append('static run');return {'analysis_complete':True}
    worker.evaluator=Functional();worker.static=Static()
    sent=adapter.send_count
    assert worker.tick() is True
    assert r.state(run.id).evaluation=='manual_pending'
    assert worker.tick() is False
    assert actions==['functional schedule','functional run','static schedule','static run']
    assert adapter.send_count==sent
    pipeline.close();r.close()


def test_completion_restart_keeps_interrupted_attempt_without_retry(tmp_path):
    from test_pipeline import setup,finish
    from research_env.completion import CompletionWorker
    r,settings,store,run,job,pipeline,scheduler,adapter,runner=setup(tmp_path)
    finish(scheduler,run)
    worker=CompletionWorker(r,SimpleNamespace(register=r,store=store,images=SimpleNamespace()))
    worker.update(run.id,'running','Funktionale Anforderungen prüfen',functional_id='original-attempt')
    worker.boot()
    assert worker.tick() is False
    row=r.connection.execute('SELECT * FROM run_completion WHERE run_id=?',(str(run.id),)).fetchone()
    assert row['functional_id']=='original-attempt' and row['status']=='failed'
    assert r.state(run.id).evaluation=='measurement_error'
    pipeline.close();r.close()


@pytest.mark.parametrize('planner,review,repair,results',[
    (True,True,False,('passed',)),
    (False,False,True,('candidate_failure','candidate_failure')),
])
def test_diagram_distinguishes_optional_skips_content_failure_and_seal(tmp_path,planner,review,repair,results):
    from test_pipeline import setup,finish
    from research_env.preparation_views import run_detail
    r,settings,store,run,job,pipeline,scheduler,adapter,runner=setup(tmp_path,planner=planner,review=review,repair=repair,result=results)
    finish(scheduler,run)
    view=run_detail(r,run.id)
    nodes={node['key']:node for node in view['pipeline_nodes']}
    assert nodes['planner']['status']==('done' if planner else 'skipped')
    assert nodes['review']['status']==('done' if review else 'skipped')
    assert nodes['repair']['status']==('done' if repair else 'skipped')
    assert nodes['rerunner']['status']==('warning' if repair else 'skipped')
    assert nodes['seal']['status']=='done'  # Seal must never hide the failed internal tests.
    assert bool(view['pipeline_failure'])==repair
    assert all(n['seconds'] is None or n['seconds']>=0 for n in view['pipeline_nodes'])
    c=client(r)
    response=c.get('/runs/'+str(run.id))
    assert response.status_code==200,response.text
    page=BeautifulSoup(response.text,'html.parser')
    assert page.h1.text=='Synthetic pipeline'
    assert page.title.text=='Synthetic pipeline · Laufstatus'
    assert len(page.select('.pipeline-flow[aria-label="Agenten und Codesicherung"] > li'))==9
    assert page.select_one('details summary').text
    assert c.get('/runs/'+str(run.id)+'/results').status_code==200
    export=c.get('/runs/'+str(run.id)+'/results/export/json')
    assert export.status_code==409  # The current result must be explicitly closed first.
    assert c.get('/runs/'+str(run.id)+'/results/export/csv').status_code==409
    pipeline.close();r.close()


def test_running_diagram_becomes_interrupted_without_false_success(tmp_path):
    from test_pipeline import setup
    from research_env.preparation_views import run_detail
    r,settings,store,run,job,pipeline,scheduler,adapter,runner=setup(tmp_path)
    pipeline.event(run.id,'node_started',{'node':'analyzer'})
    view=run_detail(r,run.id)
    assert view['pipeline_nodes'][0]['status']=='running'
    assert view['pipeline_nodes'][0]['started']
    r.connection.execute("UPDATE pipeline_binding SET status='recovery_required' WHERE run_id=?",(str(run.id),))
    view=run_detail(r,run.id)
    assert view['pipeline_nodes'][0]['status']=='paused'
    assert view['pipeline_nodes'][0]['started'] is None
    assert view['tone']=='warning'
    pipeline.close();r.close()


def test_run_closure_binds_exact_result_and_does_not_rewrite_generation(tmp_path,monkeypatch):
    from test_pipeline import setup,finish
    from research_env.domain import Event,RunStateRevision
    import research_env.workflow_views as views
    r,settings,store,run,job,pipeline,scheduler,adapter,runner=setup(tmp_path)
    finish(scheduler,run)
    saved=r.state(run.id);sent=adapter.send_count
    data={'functional':{'F':{'value':'1'},'T':{'value':'1'},'R':{f'R{i}':{'value':'1'} for i in range(1,7)}},
        'static':{'analysis_complete':True},'open_reviews':[],'absence':None,'can_close':True,
        'selected_measurements':{'functional_R':str(uuid4())},'selected_reviews':{'T2':str(uuid4())}}
    monkeypatch.setattr(views,'results',lambda *args:data)
    payload={'idempotency_key':'close-test','result_hash':views.result_binding(data),'person':'TECHNICAL-FIXTURE:review','confirm':'true'}
    closure=views.close_run(r,run.id,payload)
    assert views.close_run(r,run.id,payload)==closure
    assert len([e for e in r.all(Event) if e.event_type=='run_closed'])==1
    assert r.state(run.id).evaluation=='complete' and r.state(run.id).candidate_id==saved.candidate_id
    assert r.state(run.id).ended_at==saved.ended_at and adapter.send_count==sent
    assert views.closure_view(r,run.id)['current']
    data['selected_reviews']['T2']=str(uuid4())
    assert not views.closure_view(r,run.id)['current']
    with pytest.raises(ValueError):views.close_run(r,run.id,{**payload,'idempotency_key':'stale'})
    data['open_reviews']=['T3'];data['can_close']=False
    with pytest.raises(ValueError):views.close_run(r,run.id,{**payload,'idempotency_key':'missing','result_hash':views.result_binding(data)})
    pipeline.close();r.close()


def test_inspection_export_retains_original_and_never_starts_code(tmp_path):
    import io,zipfile
    from test_pipeline import setup,finish
    from research_env.inspection import prepare,latest
    from research_env.snapshots import inventory
    r,settings,store,run,job,pipeline,scheduler,adapter,runner=setup(tmp_path)
    finish(scheduler,run)
    from dataclasses import replace
    from research_env.inspection import local_copy
    local=tmp_path/'local-inspections';local.mkdir()
    r.settings=replace(settings,inspections=local,inspections_host=str(local))
    original=settings.artifacts/'sealed'/str(r.state(run.id).candidate_id)
    before=inventory(original);sent=adapter.send_count
    result=prepare(r,run.id);artifact=latest(r,run.id)
    assert prepare(r,run.id)==result and latest(r,run.id).id==artifact.id
    with zipfile.ZipFile(io.BytesIO(store.read(artifact.id))) as z:
        assert z.read('laravel/routes/study.php')==b'<?php // migrate'
        assert z.read('laravel/vendor/autoload.php')==b'<?php // exact installed synthetic dependency'
        compose=json.loads(z.read('compose.json'))
        assert compose['networks']['inspection']['internal'] is True
        assert compose['services']['gateway']['ports']==['127.0.0.1:8160:8000']
        assert 'ports' not in compose['services']['app']
        assert compose['services']['app']['networks']==['inspection']
        assert '$$p' in compose['services']['app']['entrypoint'][-1]
        assert compose['services']['app']['volumes']==['./laravel:/opt/study:ro']
        assert 'OPENROUTER' not in z.read('compose.json').decode()
    assert inventory(original)==before and adapter.send_count==sent
    copied=local_copy(r,run.id)
    assert Path(copied['project']).is_dir()
    assert inventory(Path(copied['project']))==json.loads(store.read(r.get(r.state(run.id).candidate_id).file_manifest_id))['complete_tree']
    page=client(r)
    assert page.get('/runs/'+str(run.id)+'/inspection.zip').content==store.read(artifact.id)
    pipeline.close();r.close()


def test_context_preview_is_exact_public_source_and_explains_each_role(register):
    from research_env.context_views import package
    from research_env.artifacts import IntegrityError
    r,_=register;c=client(r)
    for module in ('BF','SQL','UP'):
        baseline=package(module,'K0');extended=package(module,'K1')
        assert {f['path'] for f in baseline['files']} < {f['path'] for f in extended['files']}
        assert 'legacy/low.php' in {f['path'] for f in baseline['files']}
        assert len(baseline['roles'])==6
        response=c.get('/settings/context',params={'module':module,'context':'K1'})
        assert response.status_code==200 and 'integration/' in response.text
        assert 'Zusätzlicher Kontext' in response.text
    assert c.get('/settings/context?module=../../secrets').status_code==409


def test_multiple_models_feed_free_roles_and_fixed_research_matrix(register):
    from research_env.preparation_forms import simple_study
    from research_env.domain import ConfigurationVersion,MAIN_CELLS
    r,_=register;initialize(r)
    for name in ('first','second'):
        identity='example/'+name
        endpoint={'model_id':identity,'provider_name':'Example','tag':'example','context_length':4000,
                  'max_completion_tokens':2000,'pricing':{'prompt':'0.000001','completion':'0.000002'},'supported_parameters':['temperature']}
        with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'data':{'id':identity,'endpoints':[endpoint]}}))) as c:
            lookup_model(r,identity,client=c)
        import_model(r,identity,'example',{})
    first,second=[m for m in r.all(ModelPackage) if m.exact_model_id.startswith('example/')]
    data={'title':'Research matrix','idempotency_key':'matrix-create','model_a':str(first.id),'model_b':str(second.id)}
    key,intent=simple_study(r,data)
    command=submit(r,key,intent);process_command(r)
    state=r.connection.execute('select status from ui_command where id=?',(command,)).fetchone()
    assert state[0]=='completed'
    versions=r.all(ConfigurationVersion)
    assert len(versions)==12 and {v.cell_key for v in versions}==set(MAIN_CELLS)
    assert all(v.settings.model_a==first.id and v.settings.model_b==second.id for v in versions)
    c=client(r)
    for path in ('/free-tests','/studies'):
        soup=BeautifulSoup(c.get(path).text,'html.parser')
        for selector in ('select[name="model_a"]','select[name="model_b"]'):
            assert {str(first.id),str(second.id)} <= {o.get('value') for o in soup.select(selector+' option')}
    with pytest.raises(ValueError,match='unterschiedliche'):simple_study(r,{**data,'model_b':str(first.id)})
    for producer,verifier in (('A','B'),('A','A'),('B','A'),('B','B')):
        _,free=simple_free(r,{**data,'module':'SQL','context':'K0','producer':producer,'verifier':verifier})
        version=versions[0].model_copy(update={'cell':free.cell,'settings':free.settings})
        assert r.resolve_call_model(version,'analyzer').id=={'A':first.id,'B':second.id}[producer]
        assert r.resolve_call_model(version,'test').id=={'A':first.id,'B':second.id}[verifier]


def test_failed_free_run_can_close_without_fake_code_or_human_reviews(register):
    from research_env.domain import ConfigurationVersion, CriterionReviewRevision
    from research_env.artifacts import ArtifactStore
    from research_env.evaluation import Evaluator
    from research_env.sandbox_runtime import RuntimeImages
    from research_env.workflow_views import results, close_run, result_binding
    from datetime import datetime, timezone
    r,_=register;initialize(r)
    model=r.all(ModelPackage)[0]
    key,intent=simple_free(r,{'idempotency_key':'absence-conf','title':'Failed technical test','module':'SQL','context':'K0',
        'model_a':str(model.id),'model_b':str(model.id),'producer':'A','verifier':'A'})
    submit(r,key,intent);process_command(r)
    conf=r.all(ConfigurationVersion)[0];store=ArtifactStore(r.settings,r)
    proof=store.json({'purpose':'Synthetic failed generation'},artifact_type='technical_fixture')
    run,job=r.start_other(r._phase(conf),conf.id,decision='Synthetic only',technical_evidence_ids=(proof.id,),idempotency_key='absence-start')
    r.set_state(run.id,r.state(run.id).model_copy(update={'execution':'terminal','terminal_cause':'content_failure','seal':'no_candidate','ended_at':datetime.now(timezone.utc)}),reason='Synthetic rejected output')
    with r.transaction():r.connection.execute("UPDATE job_state SET status='stopped' WHERE job_id=?",(str(job.id),))
    root=Path(__file__).parents[1]
    images=RuntimeImages(**json.loads((root/'src/research_env/pipeline_runtime.lock.json').read_text()))
    ev=Evaluator(SimpleNamespace(register=r,store=store,settings=r.settings,images=images,assets=root))
    attempt=ev.schedule(run.id,tool_id=conf.settings.tool_ids[0],idempotency_key='absence-measure')
    ev.run_absent(attempt)
    from research_env.analysis_absence import receipt_binding
    from research_env.domain import Artifact
    absence_artifact=next(a for a in r.all(Artifact) if a.run_id==run.id and a.artifact_type=='evaluation_absence_receipt')
    receipt_binding(r,absence_artifact,main_only=False)
    result=results(r,run.id)
    assert result['absence'] and result['can_close'] and result['open_reviews']==[]
    assert result['functional']['F']['value'] is None and not result['static']['analysis_complete']
    close_run(r,run.id,{'idempotency_key':'absence-close','result_hash':result_binding(result),'person':'TECHNICAL-FIXTURE:review','confirm':'true'})
    for format in ('json','csv'):
        exported=client(r).get(f'/runs/{run.id}/results/export/{format}')
        assert exported.status_code==200,exported.text
    assert r.state(run.id).evaluation=='complete' and r.state(run.id).result.status=='technical_missing'
    assert r.state(run.id).candidate_id is None and not r.all(CriterionReviewRevision)
    page=client(r).get('/runs/'+str(run.id)+'/results')
    assert page.status_code==200 and 'keinen Laravel-Kandidaten' in page.text


def test_current_prompt_versions_are_selected_after_upgrade(register):
    from research_env.domain import digest
    from research_env.role_formats import PROMPTS, PROMPTS_HASH
    from research_env.artifacts import ArtifactStore
    r,_=register
    old=ArtifactStore(r.settings,r).json({'schema':'old-prompt'},artifact_type='pipeline_prompts')
    r.add(AssetVersion(code='PIPE-old-prompts',asset_type='prompt',manifest_hash=old.sha256,artifact_ids=(old.id,),origin='Synthetic old version',access_scope='trusted_register'))
    initialize(r);configured=default_settings(r)
    assert all(r.get(ref,AssetVersion).manifest_hash==PROMPTS_HASH for ref in configured.prompt_ids)
    assert digest(PROMPTS)==PROMPTS_HASH


def test_conscious_measurement_retry_keeps_failure_and_never_calls_model(tmp_path,monkeypatch):
    from test_pipeline import setup,finish
    from research_env.completion import CompletionWorker,request_retry
    from research_env.artifacts import ArtifactStore
    from research_env.domain import Artifact
    import research_env.completion as module
    r,settings,store,run,job,pipeline,scheduler,adapter,runner=setup(tmp_path)
    finish(scheduler,run)
    worker=CompletionWorker(r,SimpleNamespace(register=r,store=store,images=SimpleNamespace()))
    monkeypatch.setattr(module,'functional_manifest',lambda images:{})
    monkeypatch.setattr(module,'static_manifest',lambda images:{})
    monkeypatch.setattr(worker,'tool',lambda *args:uuid4())
    monkeypatch.setattr(worker,'control',lambda:uuid4())
    keys=[]
    class Functional:
        def schedule(self,*args,**kwargs):keys.append(kwargs['idempotency_key']);return str(uuid4())
        def run(self,*args,**kwargs):
            if len(keys)==1:raise ValueError('TECHNICAL-FIXTURE: unterbrochene Messung')
            return {}
    class Static:
        def schedule(self,*args,**kwargs):return str(uuid4())
        def run(self,*args,**kwargs):return {'analysis_complete':True}
    worker.evaluator=Functional();worker.static=Static()
    worker.tick()
    original=dict(r.connection.execute('SELECT * FROM run_completion WHERE run_id=?',(str(run.id),)).fetchone())
    assert original['status']=='failed'
    data={'person':'TECHNICAL-FIXTURE','reason':'Messumgebung wieder verfügbar','confirm':'true','idempotency_key':str(uuid4())}
    sent=adapter.send_count
    proof=request_retry(r,run.id,data)
    assert request_retry(r,run.id,data)==proof
    assert json.loads(store.read(proof))['previous']==original
    row=r.connection.execute('SELECT * FROM run_completion WHERE run_id=?',(str(run.id),)).fetchone()
    assert row['status']=='retry_requested' and row['attempt']==1
    assert client(r).get('/runs/'+str(run.id)).status_code==200
    worker.tick()
    row=r.connection.execute('SELECT * FROM run_completion WHERE run_id=?',(str(run.id),)).fetchone()
    assert row['status']=='manual_pending' and row['functional_id']!=original['functional_id']
    assert keys==['auto-functional-'+str(run.id),'auto-functional-'+str(run.id)+'-retry-1']
    assert adapter.send_count==sent and worker.tick() is False
    pipeline.close();r.close()


def test_missing_repair_file_is_named_and_original_candidate_survives(tmp_path):
    from test_pipeline import setup,finish,envelope,wire
    from research_env.domain import ModelCall
    from research_env.preparation_views import run_detail
    migration=envelope('migrate')
    migration['files'].append({'path':'resources/views/study/sqli.blade.php','content':'<p>Original Blade</p>'})
    r,settings,store,run,job,pipeline,scheduler,adapter,runner=setup(tmp_path,planner=False,review=False,
        outputs=[wire(envelope('analyzer')),wire(migration),wire(envelope('test')),wire(envelope('repair'))],result=('candidate_failure',))
    finish(scheduler,run)
    repair=next(c for c in r.all(ModelCall) if c.node=='repair')
    request=json.loads(store.read(repair.messages_artifact_id))
    user=json.loads(next(m['content'] for m in request['messages'] if m['role']=='user'))
    assert user['required_output_paths']==[x['path'] for x in migration['files']]
    assert r.state(run.id).terminal_cause=='content_failure' and r.state(run.id).seal=='sealed'
    assert adapter.send_count==4
    view=run_detail(r,run.id)
    assert 'Fehlend: resources/views/study/sqli.blade.php' in view['pipeline_failure']
    assert 'vorherige Code bleibt erhalten' in view['pipeline_failure']
    html=client(r).get('/runs/'+str(run.id)).text
    assert 'Originalen Fehlerbeleg öffnen' in html
    assert 'Fehlend: resources/views/study/sqli.blade.php' in html
    from research_env.completion import CompletionWorker
    worker=CompletionWorker(r,SimpleNamespace(register=r,store=store,images=SimpleNamespace()))
    worker.update(run.id,'manual_pending','Deine Bewertung')
    view=run_detail(r,run.id)
    assert view['heading']=='Mit Fehlern beendet · Bewertung möglich' and view['tone']=='warning'
    pipeline.close();r.close()


def test_measurement_retry_displays_new_attempt_instead_of_old_end(tmp_path):
    from test_pipeline import setup,finish
    from research_env.completion import CompletionWorker
    from research_env.preparation_views import run_detail
    r,settings,store,run,job,pipeline,scheduler,adapter,runner=setup(tmp_path)
    finish(scheduler,run)
    step='Funktionale Anforderungen prüfen'
    pipeline.event(run.id,'check_started',{'step':step})
    pipeline.event(run.id,'check_finished',{'step':step,'status':'failed'})
    pipeline.event(run.id,'check_started',{'step':step})
    worker=CompletionWorker(r,SimpleNamespace(register=r,store=store,images=SimpleNamespace()))
    worker.update(run.id,'running',step)
    view=run_detail(r,run.id)
    check=next(item for item in view['check_nodes'] if item['key']==step)
    assert check['status']=='running' and check['started']
    assert check['duration']=='Zeit nicht erfasst'
    pipeline.close();r.close()


def test_test_agent_cannot_replace_application_and_ui_explains_it(tmp_path):
    from test_pipeline import setup,finish,envelope,wire
    from research_env.domain import ModelCall
    from research_env.preparation_views import run_detail
    wrong=envelope('migrate');wrong['role']='test'
    r,settings,store,run,job,pipeline,scheduler,adapter,runner=setup(tmp_path,planner=False,review=False,
        outputs=[wire(envelope('analyzer')),wire(envelope('migrate')),wire(wrong)])
    finish(scheduler,run)
    call=next(c for c in r.all(ModelCall) if c.node=='test')
    request=json.loads(store.read(call.messages_artifact_id))
    user=json.loads(next(m['content'] for m in request['messages'] if m['role']=='user'))
    assert user['allowed_output_paths']==['internal/**/*.php']
    system=next(m['content'] for m in request['messages'] if m['role']=='system')
    assert 'NEVER return routes, controllers, views' in system
    assert 'required_output_paths' not in system  # Repair instructions cannot become the test task.
    view=run_detail(r,run.id)
    assert 'Anwendungscode statt erlaubter interner PHP-Tests' in view['pipeline_failure']
    assert 'Nicht übernommen: routes/study.php' in view['pipeline_failure']
    assert r.state(run.id).seal=='sealed' and adapter.send_count==3
    assert not any(c.node=='repair' for c in r.all(ModelCall))
    pipeline.close();r.close()


def test_invalid_generated_test_is_explained_without_claiming_application_failure(tmp_path):
    from test_pipeline import setup,finish,envelope,wire,MockRunner
    from research_env.preparation_views import run_detail
    from research_env.completion import CompletionWorker
    class InvalidTestRunner(MockRunner):
        def execute(self,*args,**kwargs):
            report=super().execute(*args,**kwargs)
            report['node']=kwargs['node']
            report['observations']=[{'stage':'tests','exit_code':1,'logs':[{'content':
                'PHP Fatal error: PIPELINE_INTERNAL_THROWABLE ParseError: syntax error, unexpected character 0x00\n'}]}]
            return report
    test=envelope('test');test['files'][0]['content']='<?php $request = \x00Illuminate\\Http\\Request::create("/");'
    r,settings,store,run,job,pipeline,scheduler,adapter,runner=setup(tmp_path,planner=False,review=False,
        outputs=[wire(envelope('analyzer')),wire(envelope('migrate')),wire(test),wire(envelope('repair'))],
        runner=InvalidTestRunner(('candidate_failure','candidate_failure')))
    finish(scheduler,run)
    worker=CompletionWorker(r,SimpleNamespace(register=r,store=store,images=SimpleNamespace()))
    worker.update(run.id,'running','Messumgebung prüfen')
    view=run_detail(r,run.id)
    assert 'Testskript' in view['pipeline_failure'] and 'Nullzeichen' in view['pipeline_failure']
    assert 'internal/check.php' in view['pipeline_failure']
    assert 'Antwortformat' not in view['pipeline_failure']
    assert view['failure_evidence'] and view['rejected_answer'] is None
    for key in ('runner','rerunner'):
        node=next(n for n in view['pipeline_nodes'] if n['key']==key)
        assert 'Testskript' in node['note'] and 'Fehler im erzeugten Code' not in node['note']
    html=client(r).get('/runs/'+str(run.id)).text
    assert 'Originalen Fehlerbeleg öffnen' in html and 'Nullzeichen' in html
    assert adapter.send_count==4 and r.state(run.id).terminal_cause=='content_failure'
    pipeline.close();r.close()


@pytest.mark.parametrize('mysql_ready,expect_race',[
    ('2026-10-06T03:52:34.663985Z',True),
    ('2026-10-06T03:52:29.663985Z',False),
])
def test_database_race_uses_original_timestamps_not_just_connection_error(tmp_path,mysql_ready,expect_race):
    from test_pipeline import setup,finish,MockRunner,wire,envelope
    from research_env.preparation_views import run_detail
    class ConnectionRunner(MockRunner):
        def execute(self,*args,**kwargs):
            report=super().execute(*args,**kwargs);report['node']=kwargs['node']
            report['observations']=[{'stage':'tests','exit_code':1,
                'native_state':{'FinishedAt':'2026-10-06T03:52:30.751946632Z'},
                'logs':[{'content':mysql_ready+' [System] /usr/sbin/mysqld: ready for connections. port: 3306'},
                    {'content':'PHP Fatal error: PIPELINE_INTERNAL_THROWABLE PDOException: SQLSTATE[HY000] [2002] Connection refused'}]}]
            return report
    r,settings,store,run,job,pipeline,scheduler,adapter,runner=setup(tmp_path,planner=False,review=False,
        outputs=[wire(envelope(role)) for role in ('analyzer','migrate','test','repair')],
        runner=ConnectionRunner(('candidate_failure','candidate_failure')))
    finish(scheduler,run)
    view=run_detail(r,run.id)
    assert ('Umgebungsfehler' in view['pipeline_failure']) is expect_race
    if expect_race:
        assert 'bevor MySQL bereit war' in view['pipeline_failure']
        assert 'Modellqualität' in view['pipeline_failure']
        page=client(r).get('/runs/'+str(run.id)+'/results')
        assert page.status_code==200 and 'Umgebungsfehler' in page.text
    assert r.state(run.id).terminal_cause=='content_failure'  # Historical evidence is immutable.
    pipeline.close();r.close()


def test_results_distinguish_pending_running_failed_and_completed_analysis(tmp_path, monkeypatch):
    from test_pipeline import setup, finish
    from research_env.completion import CompletionWorker
    from research_env.analysis import static_profile
    import research_env.workflow_views as views
    r, settings, store, run, job, pipeline, scheduler, adapter, runner = setup(tmp_path)
    finish(scheduler, run)
    worker = CompletionWorker(r, SimpleNamespace(register=r, store=store, images=SimpleNamespace()))
    c = client(r)
    url = f'/runs/{run.id}/results'
    original_results = views.results
    try:
        for status, step, label in (
            ('running', 'Funktionale Anforderungen prüfen', 'Noch ausstehend'),
            ('running', 'PHPStan / Larastan ausführen', 'Wird analysiert'),
            ('failed', 'Messung benötigt Aufmerksamkeit', 'Nicht abgeschlossen'),
        ):
            worker.update(run.id, status, step)
            page = BeautifulSoup(c.get(url).text, 'html.parser')
            section = page.find('h2', string='3. Statische Codequalität').parent
            assert label in section.get_text()
            assert '<pre>null</pre>' not in str(section)
            live = page.select_one('#results-progress')
            assert bool(live.get('hx-trigger')) == (status == 'running')
            if status == 'running':
                assert live['hx-get'] == url and live['hx-select'] == '#results-progress'
                assert not live.select('input, textarea, select')
        # Same GET once the independent analysis is complete: show real zero,
        # stop polling, and make the completion form available without creating calls.
        result = original_results(r, run.id)
        report = {'analysis_complete': True, 'D': 0, 'L': 33, 'file_scope': ['app/Http/Controllers/Study/SqliController.php', 'routes/study.php'], 'L_by_file': {'app/Http/Controllers/Study/SqliController.php': 30, 'routes/study.php': 3}, 'D_by_file': {'app/Http/Controllers/Study/SqliController.php': 0, 'routes/study.php': 0}}
        result.update(static=static_profile(report), static_report=report)
        monkeypatch.setattr(views, 'results', lambda *args: result)
        worker.update(run.id, 'manual_pending', 'Automatische Prüfungen beendet')
        sent = adapter.send_count
        page = BeautifulSoup(c.get(url).text, 'html.parser')
        section = page.find('h2', string='3. Statische Codequalität').parent
        assert [x.text for x in section.select('.metric strong')] == ['0', '33', '0']
        assert section.select_one('details pre')
        assert 'SqliController.php' in section.get_text() and 'routes/study.php' in section.get_text()
        assert [cell.text.strip() for cell in section.select('tbody td')] == ['app/Http/Controllers/Study/SqliController.php', '30', '0', 'routes/study.php', '3', '0']
        assert 'Blade' in section.get_text() and 'Kommentare' in section.get_text()
        assert not page.select_one('#results-progress').get('hx-trigger')
        assert page.select_one('form[action$="/close"]')
        assert adapter.send_count == sent
    finally:
        pipeline.close()
        r.close()

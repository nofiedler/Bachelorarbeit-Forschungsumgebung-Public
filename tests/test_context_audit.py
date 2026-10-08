"""Public context intervention and actual role requests, without model services."""
import hashlib
import json
from pathlib import Path
import pytest
from bs4 import BeautifulSoup
from research_env.domain import Artifact, ModelCall, canonical
from research_env.context_views import package
from research_env.role_formats import CONTRACT, PROMPTS
from research_env.preparation import software_identity
from test_pipeline import setup, finish
from test_register import register
from test_preparation import client

ROOT=Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('module', ['BF','SQL','UP'])
def test_context_intervention_preserves_public_contract_and_adds_only_source_evidence(module):
    baseline=package(module,'K0');extended=package(module,'K1')
    a={f['path']:f['content'] for f in baseline['files']};b={f['path']:f['content'] for f in extended['files']}
    assert set(b)-set(a)=={'integration/excerpts.md','integration/mapping.md'}
    assert all(b[key]==value for key,value in a.items())
    assert 'contract/rubrik.md' in a and 'contract/vertrag.md' in a
    assert not any(key.startswith(('integration/','evaluation/','reference/')) for key in a)
    for data in (baseline,extended):
        assert hashlib.sha256(data['package_text'].encode()).hexdigest()==data['manifest']['package_sha256']
        assert data['manifest']['human_review']=='open'
        assert data['manifest']['tokens'] is None
        assert {r['id']:r['prompt'] for r in data['roles']}==PROMPTS['roles']
    assert 'K1-SOURCE-'+module in b['integration/excerpts.md']

@pytest.mark.parametrize('module', ['BF','SQL','UP'])
@pytest.mark.parametrize('context', ['K0','K1'])
def test_every_role_receives_exact_assigned_package_and_separate_envelope(tmp_path,module,context):
    data=package(module,context)
    r,_,store,run,_,pipeline,scheduler,adapter,_=setup(tmp_path,module=module,context=context,repair=True,result=("passed","passed"),
        scaffold_path=ROOT/'assets/study/m2-v0.1/scaffold',package_bytes=data['package_text'].encode(),
        csrf_revision=True,source_commit=software_identity())
    try:
        finish(scheduler,run)
        calls=[c for c in r.all(ModelCall) if c.run_id==run.id]
        assert [c.node for c in calls]==['analyzer','planner','migrate','test','review','repair']
        for call in calls:
            wire=json.loads(store.read(call.messages_artifact_id))
            assert wire['messages'][0]['content']==PROMPTS['roles'][call.node]
            request=json.loads(wire['messages'][1]['content'])
            assert (request['module'],request['context'])==(module,context)
            assert request['inputs'][0]['content']==data['package_text']
            assert json.loads(request['inputs'][2]['content'])==json.loads(canonical(CONTRACT))
            assert [r.get(a,Artifact).artifact_type for a in call.input_artifact_ids[:3]]==['pipeline_package','pipeline_scaffold','pipeline_role_contract']
            assert ('K1-SOURCE-'+module in canonical(request))==(context=='K1')
            assert 'PROTECTED_HOLDOUT_MARKER' not in canonical(request)
            scaffold=json.loads(request['inputs'][1]['content'])
            assert all('K1-SOURCE-' not in f['content'] and 'K1-MAPPING-' not in f['content'] for f in scaffold['files'])
        assert adapter.send_count==6 and r.state(run.id).seal=='sealed'
    finally:
        pipeline.close();r.close()

@pytest.mark.parametrize('module', ['BF','SQL','UP'])
@pytest.mark.parametrize('context', ['K0','K1'])
def test_context_page_explains_public_requirements_holdout_and_distinct_contracts(register,module,context):
    r,_=register;response=client(r).get('/settings/context',params={'module':module,'context':context})
    assert response.status_code==200
    page=BeautifulSoup(response.text,'html.parser')
    assert page.select_one('#context-comparison')
    text=page.get_text(' ',strip=True)
    for phrase in ('Öffentliche Anforderungen sind kein Test-Leakage','Zielvertrag und Antwortformat','Tokenzahl noch nicht bestimmt','Referenzimplementierungen','integration/excerpts.md','integration/mapping.md'):
        assert phrase in text
    assert str(package(module,context)['manifest']['bytes']) in text


def test_historical_context_ui_approval_is_unchanged_and_rejects_current_sources():
    from research_env.domain import digest
    from research_env.preparation import software_compatible
    before=json.loads((ROOT/'tests/fixtures/context-ui-original-instruments.json').read_text())
    policy_path=ROOT/'src/research_env/execution_compatibility.json'
    # These are the committed historical approvals, not approval of today's
    # source tree. Keep them exact instead of silently rebinding old runs.
    assert hashlib.sha256(policy_path.read_bytes()).hexdigest()=='535abe17dbb3e8d2f62f5510c61ff48e93db505d982c0fa71d4f7cf86c11abb5'
    policy=json.loads(policy_path.read_text())
    guard=policy['releases'][before['software']]
    assert digest(guard)=='42444da1fc2aef265e808c3073f7ab2ab388e8acd2387fcc4fc29080322e8235'
    presentation_assets={str(p.relative_to(ROOT/'src/research_env'))
                         for folder in ('static/logos','static/fonts')
                         for p in (ROOT/'src/research_env'/folder).iterdir() if p.is_file()}
    presentation_assets.add('templates/pipeline_diagram.html')
    assert set(guard)==(set(before['files'])-{'execution_compatibility.json'})|presentation_assets
    for name in presentation_assets:
        assert hashlib.sha256((ROOT/'src/research_env'/name).read_bytes()).hexdigest()==guard[name]
    for name,old_hash in before['files'].items():
        if name=='execution_compatibility.json':continue
        if name in {'templates/context.html','templates/settings.html','templates/index.html','static/style.css'}:
            assert guard[name]!=old_hash
        else:
            assert guard[name]==old_hash
    assert policy['instruments'][before['software']]=={
        digest(m):digest({k:v for k,v in m.items() if k!='sources'}) for m in before['instruments']}
    assert software_identity()!=before['software']
    assert not software_compatible(before['software'])

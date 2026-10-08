"""M5 deterministic synthetic components. No real model calls/human approvals.

HTTPX MockTransport only serves locally; the container test uses --network none.
Subprocess cases kill Python with os._exit, then read the same SQLite/CAS state.
"""
from datetime import datetime, timezone
from decimal import Decimal
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID, uuid4

import httpx
import pytest
import test_register as register_fixtures
from test_artifacts import instance, secured
from research_env.backup import restore

from research_env.adapter import CallJournal
from research_env.artifacts import ArtifactStore, IntegrityError
from research_env.config import Settings
from research_env.database import migrate
from research_env.domain import (AssetVersion, Configuration, CostEntry, EffectiveSettings, MAIN_CELLS,
    ModelCall, ModelPackage, Observation, Run, Study, StudyPhase, TransportAttempt, canonical)
from research_env.providers import (CHAT_URL, METADATA_URL, KnownTransient, MockAdapter, OpenRouterAdapter,
    WireResponse, resource_estimate, safe_bytes)
from research_env.register import GateError, Register

NOW = datetime(2026,10,4,tzinfo=timezone.utc)
KEY = 'sk-or-v1-artificial-key-never-valid'


def response(**changes):
    data = {'id':'synthetic-generation-1','model':'TECHNICAL-FIXTURE:no-network','provider':'mock',
            'system_fingerprint':'synthetic-fingerprint',
            'choices':[{'message':{'content':'{"text":"synthetic"}'},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':100,'completion_tokens':200,'cost':123456.789,
                     'prompt_tokens_details':{'cached_tokens':50,'cache_write_tokens':10},
                     'completion_tokens_details':{'reasoning_tokens':20}}}
    data.update(changes)
    return WireResponse(200, canonical(data).encode(), {'x-request-id':'request-1','authorization':KEY,'set-cookie':KEY})


def make_settings(root):
    settings=Settings(*(root/name for name in ('control','artifacts','checkpoints','staging')))
    for path in (settings.control,settings.artifacts,settings.checkpoints,settings.staging): path.mkdir(parents=True,exist_ok=True)
    migrate(settings)
    return settings


def fixture(root, *, real_protocol=False, clock=None, sleep=None, fingerprint=None):
    settings=make_settings(root)
    r=Register(settings)
    store=ArtifactStore(settings,r)
    shared=store.store(b'{}',artifact_type='technical_fixture')
    def obs(value, unit='tokens'):
        return Observation(status='observed',value=Decimal(value),unit=unit,source='TECHNICAL-FIXTURE: synthetic')
    evidence=store.json({'schema':'endpoint-v1','model_id':'fixture/model-20261004',
          'endpoint_slug':'fixture/region-v1','provider_name':'Fixture Provider','endpoint_is_complete':True,
          'supported_parameters':['seed'],'limits_defaults':{'context':'synthetic','output':'synthetic'},
          'required_parameters':{},'source_url':'https://fixture.invalid/metadata','retrieved_at':NOW.isoformat(),'synthetic':True,
          'expected_fingerprint':fingerprint})
    routing={'only':['fixture/region-v1'],'order':['fixture/region-v1'],'allow_fallbacks':False,'require_parameters':True}
    model=r.add(ModelPackage(code=f'MODEL-{uuid4()}',exact_model_id='fixture/model-20261004' if real_protocol else 'TECHNICAL-FIXTURE:no-network',
        endpoint='fixture/region-v1' if real_protocol else 'mock://local',upstream='Fixture Provider' if real_protocol else 'mock',
        routing=routing if real_protocol else {},fallback={},supported_parameters=('seed',),effective_parameters={'seed':1},
        context_limit=obs(100000000),output_limit=obs(100000000),price=obs('999999','USD'),currency='USD',price_as_of=NOW,
        metadata_evidence_ids=(evidence.id,),version_uncertainty='TECHNICAL-FIXTURE; does not prove real capabilities/unchanged weights'))
    study=r.add(Study(code=f'STUDY-{uuid4()}',title='M5 fixture',design_version='synthetic-v1',data_origin='synthetic',provenance='Synthetic only'))
    phase=r.add(StudyPhase(code=f'PHASE-{uuid4()}',study_id=study.id,purpose='free_test',provenance='Synthetic only'))
    dev=r.add(AssetVersion(code=f'ASSET-{uuid4()}',asset_type='fixture_suite',manifest_hash=shared.sha256,artifact_ids=(shared.id,),
        origin='synthetic',access_scope='public_development',suite_kind='development'))
    conf=r.add(Configuration(code=f'CONF-{uuid4()}',phase_id=phase.id))
    version=r.version_configuration(conf.id,'fixture',MAIN_CELLS['C-SQL-1'],EffectiveSettings(model_a=model.id,model_b=model.id,
        development_suite_id=dev.id,role_parameters={'all':{'seed':1}},retry_interval_seconds=Decimal('0.01')))
    # Formal technical fixture seeds a Run through the same relation validators;
    # bypasses aggregate start gates ONLY for real-protocol fake fixtures, never a human approval.
    if real_protocol:
        with r.transaction():
            run=r._put(Run(code=f'FREE_TEST-{uuid4()}',phase_id=phase.id,purpose='free_test',planned_run_id=None,
                configuration_version_id=version.id,effective_hash=version.content_hash,suite_id=dev.id,
                technical_evidence_ids=(shared.id,),start_decision='TECHNICAL-FIXTURE: offline protocol fixture, no actual permission',started_at=NOW))
    else:
        run,_=r.start_other(phase.id,version.id,decision='TECHNICAL-FIXTURE: conscious mock start',technical_evidence_ids=(shared.id,),idempotency_key=str(uuid4()))
    journal=CallJournal(r, **({'monotonic':clock} if clock else {}), **({'sleep':sleep} if sleep else {}))
    return r,settings,journal,run,model


def prepared(root, adapter=None, *, real_protocol=False, clock=None, sleep=None, fingerprint=None):
    r,settings,j,run,model=fixture(root,real_protocol=real_protocol,clock=clock,sleep=sleep,fingerprint=fingerprint)
    a=adapter or MockAdapter([response()])
    preview=j.preview(a,run.id,{'currency':'USD','price_as_of':NOW.isoformat(),
        'uncertainty':'synthetic fixture only','categories':{'output':{'expected_units':'1000000000','price_per_unit':'100','source':'synthetic'}},
        'eur_basis':{'rate':'0.9','source':'synthetic','as_of':NOW.isoformat(),'buffer_fraction':'0.25','buffer_reason':'synthetic uncertainty fixture'},
        'pilot_consumption':{'status':'not_collected','reason':'no real pilot'}})
    order=j.record_start_order(run.id,preview.id,person='TECHNICAL-FIXTURE: simulated user',decision='Synthetic conscious start',
             roles=('analyzer','migrate'),paid_consent=real_protocol,synthetic_fixture=True)
    call=j.prepare(a,run.id,'analyzer',order_id=order,messages=[{'role':'user','content':'synthetic role input'}])
    return r,settings,j,a,call,order


def test_success_persistence_and_idempotent_incorporation(tmp_path):
    r,settings,j,a,call,order=prepared(tmp_path)
    output=a.execute_attempt(j,call.id)
    assert output['attempts'][0]['status']=='validated'
    assert output['cost']['known_subtotal']=='123456.789'
    assert len(r.all(TransportAttempt))==1
    assert not r.connection.in_transaction
    parsed=j.incorporate(call.id,output_validator=json.loads)
    assert json.loads(parsed['content'])=={'text':'synthetic'}
    assert j.incorporate(call.id,output_validator=lambda _:pytest.fail('must reuse'))==parsed
    a.execute_attempt(j,call.id)
    assert a.send_count==1
    assert j.prepare(a,call.run_id,'analyzer',order_id=order,messages=[{'role':'user','content':'synthetic role input'}]).id==call.id
    assert ArtifactStore(settings,r).integrity()['problems']==[]
    r.close()


@pytest.mark.parametrize('failure',[KnownTransient('synthetic'),WireResponse(429,b'{"error":{"code":429,"message":"rate limit"}}',{})])
def test_identical_retry_and_unknown_first_cost(tmp_path,failure):
    waits=[]
    r,_,j,a,call,_=prepared(tmp_path,MockAdapter([failure,response()]),sleep=waits.append)
    sent=[]; a.on_send=sent.append
    output=a.execute_attempt(j,call.id)
    assert a.send_count==2 and sent[0]==sent[1]
    assert waits==[0.01]
    assert [x['status'] for x in output['attempts']]==['failed','validated']
    assert output['cost']['status']=='partial' and output['cost']['known_subtotal']=='123456.789'
    assert len(r.all(CostEntry))==1
    a.execute_attempt(j,call.id)
    assert a.send_count==2
    r.close()


@pytest.mark.parametrize('failure',[
    TimeoutError(KEY),httpx.ReadTimeout(KEY),httpx.WriteError(KEY),RuntimeError(KEY),
    WireResponse(503,b'{"error":{"code":503}}',{}),WireResponse(429,b'{"id":"possibly-created","error":{"code":429}}',{})])
def test_unclear_or_permanent_has_no_retry(tmp_path,failure):
    r,_,j,a,call,_=prepared(tmp_path,MockAdapter([failure,response()]))
    out=a.execute_attempt(j,call.id)
    assert a.send_count==1 and len(out['attempts'])==1
    assert out['attempts'][0]['status'] in ('outcome_unknown','failed')
    a.execute_attempt(j,call.id,continuation='TECHNICAL-FIXTURE: manual recovery')
    assert a.send_count==1
    r.close()


def test_two_transients_no_third_generation(tmp_path):
    r,_,j,a,call,_=prepared(tmp_path,MockAdapter([KnownTransient(),KnownTransient(),response()]))
    out=a.execute_attempt(j,call.id)
    assert [x['status'] for x in out['attempts']]==['failed','failed']
    assert a.send_count==2
    r.close()


@pytest.mark.parametrize('bad',[
    WireResponse(200,b'not-json',{}), response(choices=[]),response(model='wrong/model'),response(provider='wrong-provider'),
    response(choices=[{'message':{'content':'x','tool_calls':[{}]}}]), WireResponse(400,b'{"error":{"code":400}}',{})])
def test_format_drift_provider_error_no_rescue(tmp_path,bad):
    r,_,j,a,call,_=prepared(tmp_path,MockAdapter([bad,response()]))
    out=a.execute_attempt(j,call.id)
    assert out['attempts'][0]['status']=='failed' and a.send_count==1
    assert out['attempts'][0]['journal']['raw_id']
    a.execute_attempt(j,call.id)
    assert a.send_count==1
    r.close()


def test_missing_usage_not_zero_and_metadata_dedup(tmp_path):
    metadata=WireResponse(200,b'{"data":{"id":"synthetic-generation-1","model":"TECHNICAL-FIXTURE:no-network","provider_name":"mock","total_cost":0.1234567890123456789,"native_tokens_prompt":99}}',{})
    r,_,j,a,call,_=prepared(tmp_path,MockAdapter([response(usage=None)],metadata=metadata))
    out=a.execute_attempt(j,call.id)
    assert out['cost']['known_subtotal'] is None and len(r.all(CostEntry))==0
    tid=out['attempts'][0]['transport_id']
    out=a.fetch_usage_metadata(j,tid)
    assert out['cost']['known_subtotal']=='0.1234567890123456789'
    a.fetch_usage_metadata(j,tid)
    assert len(r.all(CostEntry))==1 and a.send_count==1 and a.metadata_count==2
    r.close()


def test_response_and_metadata_conflict_visible(tmp_path):
    metadata=WireResponse(200,b'{"data":{"id":"synthetic-generation-1","total_cost":2}}',{})
    r,_,j,a,call,_=prepared(tmp_path,MockAdapter([response(usage={'cost':1})],metadata=metadata))
    out=a.execute_attempt(j,call.id); tid=out['attempts'][0]['transport_id']
    out=a.fetch_usage_metadata(j,tid)
    assert out['cost']['status']=='unresolved' and out['attempts'][0]['billing']['conflict']
    assert len(r.all(CostEntry))==1
    r.close()


def test_role_validator_failure_preserves_and_never_regenerates(tmp_path):
    r,_,j,a,call,_=prepared(tmp_path)
    a.execute_attempt(j,call.id)
    with pytest.raises(GateError): j.incorporate(call.id,output_validator=lambda _:(_ for _ in ()).throw(ValueError(KEY)))
    out=a.execute_attempt(j,call.id)
    assert out['attempts'][0]['journal']['stop_reason']=='role_format_or_candidate_rejection'
    assert a.send_count==1
    r.close()


def test_actual_httpx_wire_without_network_and_secret_redaction(tmp_path):
    sent=[]
    def handle(req):
        sent.append(req)
        assert req.url==CHAT_URL and req.extensions['timeout']=={'connect':None,'read':None,'write':None,'pool':None}
        data=json.loads(req.content)
        assert data['provider']=={'only':['fixture/region-v1'],'order':['fixture/region-v1'],'allow_fallbacks':False,'require_parameters':True}
        assert data['stream'] is False and 'max_tokens' not in data
        return httpx.Response(200,content=response(model='fixture/model-20261004',provider='Fixture Provider',
            choices=[{'message':{'content':KEY}}]).body,headers={'x-request-id':KEY,'authorization':KEY})
    a=OpenRouterAdapter(api_key=KEY,transport=httpx.MockTransport(handle))
    r,settings,j,a,call,_=prepared(tmp_path,a,real_protocol=True)
    out=a.execute_attempt(j,call.id)
    assert out['attempts'][0]['journal']['stop_reason']=='secret_exposure_redacted'
    assert len(sent)==1
    for p in settings.artifacts.rglob('*'):
        if p.is_file(): assert KEY.encode() not in p.read_bytes()
    assert KEY not in canonical(out)
    assert KEY.encode() not in settings.database.read_bytes()
    a.close();r.close()


def test_secret_in_input_and_error_header_blocked(tmp_path):
    r,_,j,a,call,order=prepared(tmp_path)
    with pytest.raises(GateError): j.prepare(a,call.run_id,'migrate',order_id=order,messages=[{'role':'user','content':KEY}])
    assert a.send_count==0
    r.close()


def test_missing_foreign_start_and_integrity_before_send(tmp_path):
    r,settings,j,a,call,_=prepared(tmp_path)
    with pytest.raises(GateError): j.prepare(a,call.run_id,'migrate',order_id=uuid4(),messages=[{'role':'user','content':'x'}])
    art=r.get(call.messages_artifact_id)
    p=settings.artifacts/ArtifactStore.object_path(art.sha256)
    p.chmod(0o644);p.write_bytes(b'tamper')
    with pytest.raises(IntegrityError): a.execute_attempt(j,call.id)
    assert a.send_count==0 and j._state(j._latest(call.id).id)=='prepared'
    r.close()


def test_high_tokens_cost_long_duration_no_cap(tmp_path):
    ticks=iter([0,99999999999])
    r,_,j,a,call,_=prepared(tmp_path,MockAdapter([response(usage={'prompt_tokens':10**15,'completion_tokens':10**15,'cost':10**20})]),clock=lambda:next(ticks))
    out=a.execute_attempt(j,call.id)
    assert out['attempts'][0]['status']=='validated'
    assert out['active_time']['known_subtotal_seconds']=='99999999999'
    assert Decimal(out['cost']['known_subtotal'])==Decimal(10**20)
    r.close()


def test_manual_stop_during_call_and_pause_request_remains_active(tmp_path):
    r,_,j,a,call,_=prepared(tmp_path)
    def on_send(_):
        assert not r.connection.in_transaction
        out=j.diagnostic(call.id)
        assert out['attempts'][0]['status']=='dispatching'
        j.stop(call.id,reason='TECHNICAL-FIXTURE: immediate user stop')
    a.on_send=on_send
    out=a.execute_attempt(j,call.id)
    assert out['attempts'][0]['status']=='outcome_unknown'
    assert out['attempts'][0]['journal']['raw_id']
    a.execute_attempt(j,call.id,continuation='conscious recovery')
    assert a.send_count==1
    r.close()


@pytest.mark.parametrize('field,value',[
    ('exact_model_id','openrouter/auto'),('exact_model_id','fixture/latest'),
    ('routing',{'only':['fixture/region-v1','other'],'order':['fixture/region-v1'],'allow_fallbacks':False,'require_parameters':True}),
    ('fallback',{'models':['other/model']}),('effective_parameters',{'max_tokens':10}),
    ('routing',{'only':['fixture/region-v1'],'order':['fixture/region-v1'],'allow_fallbacks':True,'require_parameters':True}),
    ('routing',{'only':['fixture/region-v1'],'order':['fixture/region-v1'],'allow_fallbacks':False,'require_parameters':False})])
def test_invalid_model_routing_parameters(tmp_path,field,value):
    r,_,j,_,model=fixture(tmp_path,real_protocol=True)
    a=OpenRouterAdapter(api_key=KEY,transport=httpx.MockTransport(lambda _:pytest.fail('no send')))
    bad=model.model_copy(update={field:value})
    with pytest.raises(GateError): a.build_request(bad,[{'role':'user','content':'x'}],bad.effective_parameters,j._endpoint_evidence(model))
    a.close();r.close()


def test_exact_decimal_estimate_missing_basis_and_hand_result():
    basis={'currency':'USD','price_as_of':'2026-10-04','categories':{'input':{'expected_units':'3','price_per_unit':'0.1','source':'synthetic'},'output':{'expected_units':'7','price_per_unit':'0.02','source':'synthetic'}},
           'eur_basis':{'rate':'0.9','source':'synthetic','as_of':'2026-10-04','buffer_fraction':'0.25','buffer_reason':'synthetic'}}
    result=resource_estimate(basis)
    assert result['amount']=='0.44' and result['eur']=='0.49500'
    assert resource_estimate({})['amount'] is None
    with pytest.raises(ValueError): resource_estimate({**basis,'categories':{'x':{'expected_units':1,'price_per_unit':0.1,'source':'x'}}})


@pytest.mark.parametrize('window,expected_send,expected_state',[
    ('before_send',0,'validated'),('after_dispatch',0,'outcome_unknown'),
    ('after_raw',1,'validated'),('before_incorporation',1,'incorporated')])
def test_process_crash_restart(tmp_path,window,expected_send,expected_state):
    script=Path(__file__).with_name('adapter_crash_probe.py')
    env={'PATH':os.environ.get('PATH',''),'PYTHONPATH':str(Path(__file__).parents[1]/'src')+os.pathsep+str(Path(__file__).parent)}
    first=subprocess.run([sys.executable,str(script),str(tmp_path),window,'crash'],env=env,capture_output=True,text=True)
    assert first.returncode==77,first.stdout+first.stderr
    resumed=subprocess.run([sys.executable,str(script),str(tmp_path),window,'resume'],env=env,capture_output=True,text=True)
    assert resumed.returncode==0,resumed.stdout+resumed.stderr
    data=json.loads(resumed.stdout)
    assert data['state']==expected_state
    # before_send sends exactly once on consciously resumed prepared; dispatch orphan sends never.
    assert data['total_send']==(1 if window=='before_send' else expected_send)


@pytest.mark.parametrize('change',[{'usage':[]},{'usage':{'prompt_tokens_details':[],'completion_tokens_details':None}},
                                 {'provider':123},{'model':None},{'usage':{'cost':-1}}])
def test_malformed_native_fields_raw_is_bound(tmp_path,change):
    r,_,j,a,call,_=prepared(tmp_path,MockAdapter([response(**change)]))
    out=a.execute_attempt(j,call.id)
    assert out['attempts'][0]['journal']['raw_id']
    assert out['attempts'][0]['status'] in ('validated','failed')
    assert a.send_count==1
    if change.get('usage')=={'cost':-1}: assert out['cost']['known_subtotal'] is None
    r.close()


@pytest.mark.parametrize('failure',[response(model='wrong/model'),TimeoutError(),response(provider='wrong')])
def test_other_role_cannot_escape_stop(tmp_path,failure):
    r,_,j,a,call,order=prepared(tmp_path,MockAdapter([failure,response()]))
    a.execute_attempt(j,call.id)
    with pytest.raises(GateError): j.prepare(a,call.run_id,'migrate',order_id=order,messages=[{'role':'user','content':'x'}])
    assert a.send_count==1
    r.close()


def test_metadata_only_get_after_terminal_run(tmp_path):
    sent=[]
    def handle(req):
        sent.append((req.method,str(req.url)))
        if req.method=='POST':
            return httpx.Response(200,content=response(model='fixture/model-20261004',provider='Fixture Provider',usage=None).body)
        assert req.url.params['id']=='synthetic-generation-1'
        return httpx.Response(200,content=b'{"data":{"id":"synthetic-generation-1","model":"fixture/model-20261004","provider_name":"Fixture Provider","total_cost":99.123456789012345678901}}')
    a=OpenRouterAdapter(api_key=KEY,transport=httpx.MockTransport(handle))
    r,_,j,a,call,_=prepared(tmp_path,a,real_protocol=True)
    out=a.execute_attempt(j,call.id)
    from research_env.domain import RunState
    r.set_state(call.run_id,RunState(execution='terminal',terminal_cause='finished'))
    out=a.fetch_usage_metadata(j,out['attempts'][0]['transport_id'])
    assert out['cost']['known_subtotal']=='99.123456789012345678901'
    assert [x[0] for x in sent]==['POST','GET']
    assert sent[-1][1].startswith(METADATA_URL+'?id=')
    a.close();r.close()


@pytest.mark.parametrize('data',[
    {'id':'synthetic-generation-1','model':'other/model','total_cost':1},
    {'id':'synthetic-generation-1','provider_name':'other','total_cost':1},
    {'id':'not-the-known-generation','total_cost':1}])
def test_metadata_drift_or_generation_mismatch(tmp_path,data):
    r,_,j,a,call,order=prepared(tmp_path,MockAdapter([response()],metadata=WireResponse(200,canonical({'data':data}).encode(),{})))
    out=a.execute_attempt(j,call.id)
    out=a.fetch_usage_metadata(j,out['attempts'][0]['transport_id'])
    if data['id']=='synthetic-generation-1':
        assert out['stop_reason']=='reported_identity_drift'
        with pytest.raises(GateError): j.prepare(a,call.run_id,'migrate',order_id=order,messages=[{'role':'user','content':'x'}])
    else: assert out['attempts'][0]['journal']['metadata_issue']=='metadata_invalid_or_unavailable'
    assert a.send_count==1
    r.close()


def test_metadata_unavailable_is_missing_not_pipeline_stop(tmp_path):
    r,_,j,a,call,order=prepared(tmp_path,MockAdapter([response()]))
    out=a.execute_attempt(j,call.id)
    a.fetch_usage_metadata(j,out['attempts'][0]['transport_id'])
    assert j.diagnostic(call.id)['stop_reason'] is None
    second=j.prepare(a,call.run_id,'migrate',order_id=order,messages=[{'role':'user','content':'x'}])
    a.execute_attempt(j,second.id)
    assert a.send_count==2
    r.close()


def test_no_metadata_for_missing_generation(tmp_path):
    r,_,j,a,call,_=prepared(tmp_path,MockAdapter([TimeoutError()]))
    out=a.execute_attempt(j,call.id)
    with pytest.raises(GateError): a.fetch_usage_metadata(j,out['attempts'][0]['transport_id'])
    assert a.metadata_count==0 and a.send_count==1
    r.close()


def test_saved_metadata_same_billing_no_double_count(tmp_path):
    r,_,j,a,call,_=prepared(tmp_path,MockAdapter([response(usage={'cost':1})],metadata=WireResponse(200,b'{"data":{"id":"synthetic-generation-1","total_cost":1}}',{})))
    out=a.execute_attempt(j,call.id)
    out=a.fetch_usage_metadata(j,out['attempts'][0]['transport_id'])
    assert out['cost']['known_subtotal']=='1' and len(r.all(CostEntry))==1
    assert len(out['attempts'][0]['billing']['evidence_ids'])==2
    r.close()


def test_persisted_order_scope_and_response_references_immutable(tmp_path):
    import sqlite3
    r,_,j,a,call,order=prepared(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        with r.transaction(): r.connection.execute('UPDATE adapter_start_order SET body=? WHERE id=?',('{}',order))
    a.execute_attempt(j,call.id)
    tid=j._latest(call.id).id
    body=j._attempt(tid);body['raw_id']=str(uuid4())
    with pytest.raises(sqlite3.IntegrityError):
        with r.transaction(): j._save_attempt(tid,body)
    assert a.send_count==1
    r.close()


def test_foreign_order_role_and_input_isolation(tmp_path):
    r,_,j,a,call,order=prepared(tmp_path)
    with pytest.raises(GateError): j.prepare(a,call.run_id,'repair',order_id=order,messages=[{'role':'user','content':'x'}])
    from research_env.domain import Artifact
    # Use a protected shared artifact; no real holdout fixture is loaded.
    protected=ArtifactStore(r.settings,r).store(b'SYNTHETIC-HOLDOUT-MARKER',access_scope='trusted_evaluator')
    with pytest.raises(GateError): j.prepare(a,call.run_id,'migrate',order_id=order,messages=[{'role':'user','content':'x'}],input_artifact_ids=(protected.id,))
    assert a.send_count==0
    r.close()


def test_reentry_other_inputs_cannot_mutate_request(tmp_path):
    r,_,j,a,call,order=prepared(tmp_path)
    a.execute_attempt(j,call.id)
    with pytest.raises(GateError): j.prepare(a,call.run_id,'analyzer',order_id=order,messages=[{'role':'user','content':'different'}])
    assert a.send_count==1
    r.close()


def test_pause_requested_does_not_cut_current_provider_interval(tmp_path):
    ticks=iter([0,40])
    r,_,j,a,call,order=prepared(tmp_path,clock=lambda:next(ticks))
    run=r.get(call.run_id,Run)
    r.set_phase_status(run.phase_id,'ready',reason='TECHNICAL-FIXTURE')
    r.set_phase_status(run.phase_id,'running',reason='TECHNICAL-FIXTURE')
    def send(_): r.set_phase_status(run.phase_id,'pause_requested',reason='TECHNICAL-FIXTURE: conscious pause request')
    a.on_send=send
    out=a.execute_attempt(j,call.id)
    assert out['active_time']['known_subtotal_seconds']=='40' and out['attempts'][0]['status']=='validated'
    second=j.prepare(a,call.run_id,'migrate',order_id=order,messages=[{'role':'user','content':'x'}])
    with pytest.raises(GateError): a.execute_attempt(j,second.id)
    assert a.send_count==1
    r.close()


def test_generic_artifact_paths_reject_key_before_storage(tmp_path):
    a=OpenRouterAdapter(api_key=KEY,transport=httpx.MockTransport(lambda _:pytest.fail('no send')))
    r,_,j,run,_=fixture(tmp_path,real_protocol=True)
    with pytest.raises(GateError): j.preview(a,run.id,{'currency':'USD','price_as_of':NOW.isoformat(),'uncertainty':KEY})
    assert KEY.encode() not in r.settings.database.read_bytes()
    a.close();r.close()


@pytest.mark.parametrize('window,expected', [('retry_wait_started','validated'),('retry_after_dispatch','outcome_unknown')])
def test_real_retry_crash_windows(tmp_path,window,expected):
    script=Path(__file__).with_name('adapter_crash_probe.py')
    env={'PATH':os.environ.get('PATH',''),'PYTHONPATH':str(Path(__file__).parents[1]/'src')+os.pathsep+str(Path(__file__).parent)}
    first=subprocess.run([sys.executable,str(script),str(tmp_path),window,'crash'],env=env,capture_output=True,text=True)
    assert first.returncode==77,first.stdout+first.stderr
    resumed=subprocess.run([sys.executable,str(script),str(tmp_path),window,'resume'],env=env,capture_output=True,text=True)
    assert resumed.returncode==0,resumed.stdout+resumed.stderr
    data=json.loads(resumed.stdout)
    assert data['state']==expected
    assert data['total_send']==(2 if window=='retry_wait_started' else 1)
    assert len(data['diagnostic']['attempts'])==2
    if window=='retry_wait_started':
        assert data['diagnostic']['active_time']['status']=='technical_missing'
        assert data['diagnostic']['attempts'][1]['journal']['interrupted_retry_waits']


def test_ongoing_hanger_diagnosis_duplicate_prevention_immediate_stop(tmp_path):
    import time
    script=Path(__file__).with_name('adapter_crash_probe.py')
    env={'PATH':os.environ.get('PATH',''),'PYTHONPATH':str(Path(__file__).parents[1]/'src')+os.pathsep+str(Path(__file__).parent)}
    child=subprocess.Popen([sys.executable,str(script),str(tmp_path),'hang','crash'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        # This deadline belongs only to fixture startup, never to the application call.
        deadline=time.monotonic()+15
        while not (tmp_path/'sends').exists() and child.poll() is None and time.monotonic()<deadline: time.sleep(0.01)
        assert (tmp_path/'sends').exists()
        r=Register(make_settings(tmp_path));j=CallJournal(r)
        call=r.get((tmp_path/'call_id').read_text(),ModelCall)
        a=MockAdapter([response()])
        assert j.diagnostic(call.id)['attempts'][0]['status']=='dispatching'
        with pytest.raises(BlockingIOError): a.execute_attempt(j,call.id,continuation='TECHNICAL-FIXTURE: duplicate')
        out=j.stop(call.id)
        assert out['attempts'][0]['status']=='outcome_unknown'
        assert out['active_time']['status']=='technical_missing' and out['cost']['known_subtotal'] is None
        assert out['active_time']['known_subtotal_seconds'] is None
        child.kill();child.communicate()
        assert child.returncode==-9
        a.execute_attempt(j,call.id,continuation='TECHNICAL-FIXTURE: deliberate recovery')
        assert a.send_count==0 and len((tmp_path/'sends').read_bytes().splitlines())==1
        r.close()
    finally:
        if child.poll() is None: child.kill();child.communicate()


def test_validated_restart_needs_conscious_continuation(tmp_path):
    r,settings,j,a,call,_=prepared(tmp_path)
    a.execute_attempt(j,call.id);r.close()
    r=Register(settings);j2=CallJournal(r)
    with pytest.raises(GateError): j2.incorporate(call.id,output_validator=json.loads)
    with pytest.raises(GateError): a.execute_attempt(j2,call.id)
    a.execute_attempt(j2,call.id,continuation='TECHNICAL-FIXTURE: conscious safe recovery')
    j2.incorporate(call.id,output_validator=json.loads)
    assert a.send_count==1
    r.close()


def backup_call(instance, tmp_path, adapter):
    r,settings,store,fixture,freeze,backups,worker=instance
    secured(instance,tmp_path/'freeze-copy')
    run,_=register_fixtures.start(r,freeze,fixture)
    j=CallJournal(r)
    preview=j.preview(adapter,run.id,{'currency':'USD','price_as_of':NOW.isoformat(),'uncertainty':'TECHNICAL-FIXTURE'})
    order=j.record_start_order(run.id,preview.id,person='TECHNICAL-FIXTURE: technical agent',
        decision='TECHNICAL-FIXTURE: component only',roles=('analyzer',),synthetic_fixture=True)
    call=j.prepare(adapter,run.id,'analyzer',order_id=order,messages=[{'role':'user','content':'synthetic'}])
    return j,call


def test_journal_and_raw_response_survive_consistent_backup_restore(instance,tmp_path):
    from research_env.domain import RunState
    r,settings,store,fixture,freeze,backups,worker=instance
    a=MockAdapter([response()])
    j,call=backup_call(instance,tmp_path,a)
    a.execute_attempt(j,call.id)
    j.incorporate(call.id,output_validator=json.loads)
    r.set_state(call.run_id,RunState(execution='terminal',terminal_cause='interrupted',ended_at=NOW))
    expected=j.diagnostic(call.id)
    job=backups.enqueue(freeze.id,idempotency_key='M5-journal-backup')
    saved=worker.tick()
    assert saved
    package=backups.request(job.id)['package_path']
    target=Settings(*(tmp_path/'restored'/name for name in ('control','artifacts','checkpoints','staging')))
    report=restore(package,target,expected_hash=saved.manifest_hash)
    assert report['generation_started'] is False and report['integrity']['complete']
    restored=Register(target);recovered=CallJournal(restored)
    assert recovered.diagnostic(call.id)==expected
    assert recovered.store.read(expected['attempts'][0]['journal']['raw_id'])==response().body
    assert recovered.incorporate(call.id,output_validator=json.loads)['content']=='{"text":"synthetic"}'
    assert a.send_count==1
    restored.close()


def test_active_metadata_request_blocks_whole_instance_backup(instance,tmp_path):
    r,settings,store,fixture,freeze,backups,worker=instance
    a=MockAdapter([response(usage={})])
    j,call=backup_call(instance,tmp_path,a)
    a.execute_attempt(j,call.id)
    def metadata(_):
        job=backups.enqueue(freeze.id,idempotency_key='M5-active-metadata')
        with pytest.raises(GateError,match='Aktive Requests'): worker.tick()
        assert backups.request(job.id)['status']=='failed'
        return WireResponse(200,b'{"data":{"id":"synthetic-generation-1","total_cost":0.00000001}}',{})
    a.metadata=metadata
    a.fetch_usage_metadata(j,j._latest(call.id).id)
    assert j.diagnostic(call.id)['cost']['known_subtotal']=='1E-8'


@pytest.mark.parametrize('change',['terminal','phase','other_call'])
def test_dispatch_claim_rechecks_current_gates_atomically(tmp_path,change):
    from research_env.domain import RunState
    r,_,j,a,call,order=prepared(tmp_path)
    def race(point):
        if point!='before_send': return
        if change=='terminal':
            r.set_state(call.run_id,RunState(execution='terminal',terminal_cause='interrupted',ended_at=NOW))
        elif change=='phase':
            run=r.get(call.run_id,Run)
            r.set_phase_status(run.phase_id,'stopped',reason='TECHNICAL-FIXTURE: concurrent stop')
        else:
            other=j.prepare(a,call.run_id,'migrate',order_id=order,messages=[{'role':'user','content':'synthetic'}])
            # Simulate another current recorded stop in the last gap before dispatch.
            j.stop(other.id)
    with pytest.raises(GateError): a.execute_attempt(j,call.id,crash=race)
    assert a.send_count==0 and j.diagnostic(call.id)['attempts'][0]['status']=='prepared'
    r.close()


def test_effective_cost_view_invalidates_conflicting_historical_entry(tmp_path):
    r,_,j,a,call,_=prepared(tmp_path)
    a.execute_attempt(j,call.id)
    before=j.effective_costs(call.run_id)
    assert before['status']=='known' and before['amount']=='123456.789'
    a.metadata=WireResponse(200,b'{"data":{"id":"synthetic-generation-1","total_cost":7.1}}',{})
    a.fetch_usage_metadata(j,j._latest(call.id).id)
    after=j.effective_costs(call.run_id)
    assert after['status']=='unresolved' and after['amount'] is None and after['known_subtotal'] is None
    assert after['evidence_revision']!=before['evidence_revision'] and after['phase_revision']>before['phase_revision']
    assert after['evidence'][0]['attempts'][0]['billing']['conflict']
    assert after['historical_cost_entries']==before['historical_cost_entries']
    assert len(r.all(CostEntry))==1 and r.all(CostEntry)[0].amount.value==Decimal('123456.789')
    r.close()


@pytest.mark.parametrize('reported,expected_status',[('expected-fingerprint','validated'),('changed-fingerprint','failed')])
def test_fingerprint_drift_cannot_validate_already_parsed_response(tmp_path,reported,expected_status):
    a=MockAdapter([response(system_fingerprint=reported)])
    r,_,j,a,call,_=prepared(tmp_path,a,fingerprint='expected-fingerprint')
    out=a.execute_attempt(j,call.id)
    assert out['attempts'][0]['status']==expected_status and a.send_count==1
    assert j.store.read(out['attempts'][0]['journal']['raw_id'])==response(system_fingerprint=reported).body
    if expected_status=='failed':
        assert out['stop_reason']=='reported_identity_drift' and out['attempts'][0]['journal']['parsed_id'] is None
        with pytest.raises(GateError):j.incorporate(call.id,output_validator=json.loads)
        with pytest.raises(GateError):j.prepare(a,call.run_id,'migrate',order_id=out['order_id'],messages=[{'role':'user','content':'synthetic'}])
        a.execute_attempt(j,call.id)
        assert a.send_count==1 and len(r.all(TransportAttempt))==1
    else:
        assert j.incorporate(call.id,output_validator=json.loads)['fingerprint']==reported
    r.close()


@pytest.mark.parametrize('responses,ticks,expected,status',[
    ([httpx.ReadTimeout('TECHNICAL-FIXTURE')],[7],None,'technical_missing'),
    ([response()],[7,7],'0','observed'),
    ([KnownTransient(),httpx.ReadTimeout('TECHNICAL-FIXTURE')],[10,12,12,15,15],'5','technical_missing'),
])
def test_known_active_time_distinguishes_none_zero_and_partial(tmp_path,responses,ticks,expected,status):
    values=iter(ticks)
    r,_,j,a,call,_=prepared(tmp_path,MockAdapter(responses),clock=lambda:next(values),sleep=lambda _:None)
    out=a.execute_attempt(j,call.id)
    assert out['active_time']['status']==status
    assert out['active_time']['known_subtotal_seconds']==expected
    r.close()


def encoded_key(kind):
    if kind=='literal':return KEY
    if kind=='full':return ''.join('\\u%04x'%ord(c) for c in KEY)
    if kind=='partial':return ''.join(c if i%2 else '\\u%04x'%ord(c) for i,c in enumerate(KEY))
    return ''.join('\\\\u%04x'%ord(c) for c in KEY)


EXACT_COST=b'0.12345678901234567890123456789'


@pytest.mark.parametrize('kind',['literal','full','partial','nested'])
def test_semantic_response_secret_redacted_before_cas_without_number_roundtrip(tmp_path,kind):
    original=(b'{"id":"synthetic-generation-1","model":"TECHNICAL-FIXTURE:no-network","provider":"mock",'
        b'"choices":[{"message":{"content":"'+encoded_key(kind).encode()+b'"},"finish_reason":"stop"}],'
        b'"usage":{"cost":'+EXACT_COST+b',"prompt_tokens":1,"completion_tokens":2}}')
    r,_,j,a,call,_=prepared(tmp_path,MockAdapter([WireResponse(200,original,{})]))
    out=a.execute_attempt(j,call.id)
    body=out['attempts'][0]['journal'];saved=j.store.read(body['raw_id'])
    envelope=json.loads(j.store.read(body['envelope_id']))
    assert out['stop_reason']=='secret_exposure_redacted' and out['attempts'][0]['status']=='failed'
    assert envelope['redacted'] and envelope['body_redacted'] and envelope['redaction_reason']=='secret_exposure_redacted'
    assert envelope['original_sha256']==hashlib.sha256(original).hexdigest()
    assert json.loads(saved)['choices'][0]['message']['content']=='[REDACTED]'
    assert b'"cost":'+EXACT_COST in saved and out['cost']['known_subtotal']==EXACT_COST.decode()
    assert KEY.encode() not in r.settings.database.read_bytes()
    assert all(KEY.encode() not in p.read_bytes() and safe_bytes(p.read_bytes(),KEY)[1] is False
        for p in r.settings.artifacts.glob('objects/sha256/*/*') if p.is_file())
    assert a.send_count==1
    r.close()


@pytest.mark.parametrize('kind',['literal','full','partial','nested'])
def test_semantic_metadata_secret_has_redacted_provenance_no_inference(tmp_path,kind):
    original=b'{"data":{"id":"synthetic-generation-1","total_cost":'+EXACT_COST+b',"extra":"'+encoded_key(kind).encode()+b'"}}'
    r,_,j,a,call,_=prepared(tmp_path,MockAdapter([response(usage={})],metadata=WireResponse(200,original,{})))
    out=a.execute_attempt(j,call.id)
    a.fetch_usage_metadata(j,out['attempts'][0]['transport_id'])
    row=j.db.execute('SELECT status,artifact_id FROM adapter_metadata').fetchone()
    env=json.loads(j.store.read(row['artifact_id']));saved=j.store.read(env['raw_id'])
    assert row['status']=='failed' and env['redacted'] and env['redaction_reason']=='secret_exposure_redacted'
    assert env['original_sha256']==hashlib.sha256(original).hexdigest()
    assert json.loads(saved)['data']['extra']=='[REDACTED]' and b'"total_cost":'+EXACT_COST in saved
    assert j.diagnostic(call.id)['cost']['known_subtotal'] is None
    assert j.diagnostic(call.id)['stop_reason']=='secret_exposure_redacted'
    with pytest.raises(GateError):j.prepare(a,call.run_id,'migrate',order_id=out['order_id'],messages=[{'role':'user','content':'synthetic'}])
    assert a.send_count==1 and a.metadata_count==1
    r.close()


@pytest.mark.parametrize('kind',['literal','full','partial','nested'])
@pytest.mark.parametrize('channel',['response','metadata'])
def test_allowed_headers_redact_semantic_secret_with_original_hash(tmp_path,kind,channel):
    original=encoded_key(kind)
    a=MockAdapter([response()])
    if channel=='response':a.responses=[WireResponse(200,response().body,{'x-request-id':original})]
    else:a.metadata=WireResponse(200,b'{"data":{"id":"synthetic-generation-1"}}',{'x-request-id':original})
    r,_,j,a,call,_=prepared(tmp_path,a)
    out=a.execute_attempt(j,call.id)
    if channel=='response':
        env=json.loads(j.store.read(out['attempts'][0]['journal']['envelope_id']))
        assert out['stop_reason']=='secret_exposure_redacted'
    else:
        a.fetch_usage_metadata(j,out['attempts'][0]['transport_id'])
        row=j.db.execute('SELECT status,artifact_id FROM adapter_metadata').fetchone()
        assert row['status']=='failed'
        env=json.loads(j.store.read(row['artifact_id']))
    assert env['headers']['x-request-id']=='[REDACTED]' and env['body_redacted'] is False
    assert env['header_redactions']['x-request-id']=={'original_sha256':hashlib.sha256(original.encode()).hexdigest(),'reason':'secret_exposure_redacted'}
    assert env['redacted'] and KEY.encode() not in r.settings.database.read_bytes()
    assert j.diagnostic(call.id)['stop_reason']=='secret_exposure_redacted'
    with pytest.raises(GateError):j.prepare(a,call.run_id,'migrate',order_id=out['order_id'],messages=[{'role':'user','content':'synthetic'}])
    assert a.send_count==1
    r.close()


@pytest.mark.parametrize('kind',['literal','full','partial','nested'])
def test_semantic_request_and_generic_journal_input_rejected_before_persistence(tmp_path,kind):
    a=OpenRouterAdapter(api_key=KEY,transport=httpx.MockTransport(lambda _:pytest.fail('no send')))
    r,_,j,run,model=fixture(tmp_path,real_protocol=True)
    preview=j.preview(a,run.id,{'currency':'USD','price_as_of':NOW.isoformat(),'uncertainty':'TECHNICAL-FIXTURE'})
    order=j.record_start_order(run.id,preview.id,person='TECHNICAL-FIXTURE: synthetic',decision='Synthetic only',roles=('analyzer',),paid_consent=True,synthetic_fixture=True)
    with pytest.raises(GateError):j.prepare(a,run.id,'analyzer',order_id=order,messages=[{'role':'user','content':encoded_key(kind)}])
    with pytest.raises(GateError):j.preview(a,run.id,{'currency':'USD','price_as_of':NOW.isoformat(),'uncertainty':encoded_key(kind)})
    assert not r.all(ModelCall) and KEY.encode() not in r.settings.database.read_bytes()
    a.close();r.close()


def test_nonsecret_response_and_metadata_bytes_numbers_and_escapes_remain_exact(tmp_path):
    original=(b'{ "id":"synthetic-generation-1", "model":"TECHNICAL-FIXTURE:no-network", "provider":"mock",'
        b'"choices":[{"message":{"content":"normal \\u00e4 / \\u005c path"},"finish_reason":"stop"}],'
        b'"usage":{"cost":'+EXACT_COST+b'}}')
    metadata=b'{ "data": {"id":"synthetic-generation-1","total_cost":'+EXACT_COST+b',"extra":"\\u00f6"}}'
    assert safe_bytes(original,KEY)==(original,False) and safe_bytes(metadata,KEY)==(metadata,False)
    r,_,j,a,call,_=prepared(tmp_path,MockAdapter([WireResponse(200,original,{})],metadata=WireResponse(200,metadata,{})))
    out=a.execute_attempt(j,call.id);tid=out['attempts'][0]['transport_id']
    assert out['attempts'][0]['status']=='validated' and j.store.read(out['attempts'][0]['journal']['raw_id'])==original
    a.fetch_usage_metadata(j,tid)
    env=json.loads(j.store.read(j.db.execute('SELECT artifact_id FROM adapter_metadata').fetchone()[0]))
    assert j.store.read(env['raw_id'])==metadata and not env['redacted']
    assert len(r.all(CostEntry))==1 and r.all(CostEntry)[0].amount.value==Decimal(EXACT_COST.decode())
    r.close()


@pytest.mark.parametrize('amounts,status,expected',[
    ((EXACT_COST,b'0.00000000000000000000000000001'),'known','0.12345678901234567890123456790'),
    ((b'1000000000000000000000000000000',b'0.00000000000000000000000000001'),'known','1000000000000000000000000000000.00000000000000000000000000001'),
    ((b'0',b'0'),'known','0'),
    ((EXACT_COST,None),'partial',EXACT_COST.decode()),
    ((None,None),'unresolved',None),
])
def test_effective_costs_exact_multicall_sum_zero_missing_and_partial(tmp_path,amounts,status,expected):
    replies=[]
    for i,amount in enumerate(amounts):
        if amount is None:replies.append(response(id=f'synthetic-generation-{i+1}',usage={}))
        else:replies.append(WireResponse(200,response(id=f'synthetic-generation-{i+1}').body.replace(b'123456.789',amount),{}))
    r,_,j,a,call,order=prepared(tmp_path,MockAdapter(replies))
    a.execute_attempt(j,call.id)
    second=j.prepare(a,call.run_id,'migrate',order_id=order,messages=[{'role':'user','content':'second synthetic role'}])
    a.execute_attempt(j,second.id)
    view=j.effective_costs(call.run_id)
    assert view['status']==status and view['known_subtotal']==expected
    assert view['amount']==(expected if status=='known' else None)
    assert len(view['historical_cost_entries'])==sum(amount is not None for amount in amounts)
    assert a.send_count==2
    r.close()

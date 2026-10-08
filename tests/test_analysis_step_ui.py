from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from research_env.analysis_step_ui import elapsed, steps, event_times


def event(sequence, kind, seconds, process='p1'):
    return SimpleNamespace(id=uuid4(), sequence=sequence, event_type=kind,
        happened_at=datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(seconds=seconds),
        process_id=process, details={'node':'analyzer','status':'complete'})


def test_elapsed_never_subtracts_across_clocks_and_exposes_partial_attempts():
    events=[event(1,'node_started',0),event(2,'node_finished',5),
            event(3,'node_started',10),event(4,'node_finished',20,'p2')]
    result=elapsed(events,'analyzer')
    assert result['status']=='partial' and result['value'] is None
    assert result['known_subtotal']=='5.0'
    assert elapsed(events[:2],'analyzer')['value']=='5.0'
    assert elapsed(events[:1],'analyzer')['status']=='not_collected'


def test_step_tokens_and_costs_include_retries_without_adding_token_subcategories():
    def attempt(identity, total, amount):
        return {'transport_id':identity,'billing':{'evidence_ids':[], 'native_usage_evidence':[
            {'kind':'response','native':{'total_tokens':total,'prompt_tokens':total-10,
               'completion_tokens':10,'completion_tokens_details':{'reasoning_tokens':4}}}]}}
    row={'plan':{'cell_key':'E-P0R0'},'state':{'execution':'terminal'},'resources':{
        'model_calls':[{'id':'call1','node':'analyzer','model_package_id':'model1'}],
        'costs':{'evidence':[{'call_id':'call1','attempts':[attempt('a',100,'0.1'),attempt('b',200,'0.2')],
                            'cost':{'status':'known','currency':'USD','known_subtotal':'0.3'}}]}},
        'models':{'A':{'id':'model1','exact_model_id':'model/example'}}}
    rows,checks=steps(row,[event(1,'node_started',0),event(2,'node_finished',5)])
    analyzer=rows[0]
    assert analyzer['calls']==1 and analyzer['transports']==2
    assert analyzer['tokens']['total']['value']=='300'
    assert analyzer['tokens']['reasoning']['value']=='8'
    assert analyzer['cost']['amount']=='0.3'
    assert rows[1]['status']=='In Konfiguration aus'
    assert rows[4]['model_step'] is False
    assert all(check['timing']['value'] is None for check in checks)


def test_supplementary_events_are_cut_off_at_saved_snapshot():
    now=datetime.now(timezone.utc)
    old=SimpleNamespace(created_at=now-timedelta(seconds=1))
    new=SimpleNamespace(created_at=now+timedelta(seconds=1))
    r=SimpleNamespace(get=lambda *args: SimpleNamespace(created_at=now),for_run=lambda *args:[old,new])
    rid=str(uuid4())
    assert event_times(r,{'rows':[{'run_id':rid}]},uuid4())=={rid:[old]}

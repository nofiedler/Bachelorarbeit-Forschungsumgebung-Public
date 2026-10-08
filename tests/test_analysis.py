"""Independent hand fixtures first fixed in m7_hand_expected.json; synthetic only."""
from copy import deepcopy
from decimal import Decimal, localcontext
from fractions import Fraction
import csv
from io import StringIO
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from research_env.analysis import compute, describe, functional, value, VERSION, INPUT_SCHEMA
from research_env.analysis_export import render
from research_env.analysis_resources import tokens, resource_profile
from research_env.domain import *
from research_env.matrix import matrix

EXPECTED = json.loads(Path(__file__).with_name('m7_hand_expected.json').read_text())
CASES = [{'id': 'c'+str(i), 'category': 'R'+str(i), 'module': m, 'assertions': ['a'+str(i)]} for m in ('BF','SQL','UP') for i in range(1,7)]


def result(i, status='passed', **kwargs):
    return {'test_id': 'c'+str(i), 'assertion_id': 'a'+str(i), 'r_category': 'R'+str(i),
        'status': status, 'cause': 'Synthetic predetermined '+status,
        'input_artifact_id': str(uuid4()), 'raw_artifact_id': str(uuid4()), **kwargs}


def review(number=1):
    return {'verdict': value(number, unit='binary'), 'code_artifact_id': None, 'measurement_ids': []}


def hand_snapshot(r_c=3, r_e=1):
    fid = uuid4()
    plans = matrix(fid,r_c,r_e,'synthetic-hand-v1')
    rows = []
    for plan in plans:
        cell = MAIN_CELLS[plan['cell_key']]
        rows.append({'plan': plan, 'run_id': str(uuid4()), 'purpose': 'main', 'candidate_hash': 'a'*64,
            'configuration_id': str(uuid4()), 'configuration_hash': 'b'*64, 'state': {'execution': 'terminal'},
            'cases': [c for c in CASES if c['module']==cell.module], 'test_results': [], 'reviews': {},
            'static_report': None, 'resources': {}, 'selection': {'measurements': {}, 'reviews': {}}, 'missing': None, 'missing_status': 'not_collected'})
    return {'schema': INPUT_SCHEMA,'analysis_version': VERSION,'freeze_id': str(fid),'phase_id': str(uuid4()),'data_origin':'synthetic',
        'r_c': r_c,'r_e': r_e,'seed':'synthetic-hand-v1','rows':rows,'resource_areas':{},'phase_resources':[]}


def set_f(row, f):
    if f is None:
        row['reviews'] = {}
        return
    numerator = int(Fraction(f)*6)
    assert Fraction(numerator,6)==Fraction(f)
    row['reviews'] = {key: review() for key in ('T1','T2','T3','T4','T5')}
    row['test_results'] = [result(i, 'passed' if i<=numerator else 'failed') for i in range(1,7)]


def filled_snapshot():
    s = hand_snapshot()
    for row in s['rows']:
        p=row['plan'];cell=p['cell_key'];b=p['block_index']
        if cell.startswith('C-'):
            f=EXPECTED['kernel_blocks'][str(b)][MAIN_CELLS[cell].module][int(cell[-1])]
        else:
            f={'E-AB':'1/2','E-BA':'1/3','E-BB':'0','E-P0R0':'0','E-P0R1':'1/6','E-P1R0':'1/3'}[cell]
        set_f(row,f)
    return s


def test_hand_complete_incomplete_blocks_and_both_quartets():
    output=compute(filled_snapshot())
    assert output['n_C']==2 and output['core']['mean']['value']==EXPECTED['delta']
    assert output['core']['sample_sd']['value']==EXPECTED['sd']
    assert [x['value'] for x in output['core']['values']]==EXPECTED['g']
    assert output['core']['leave_one_out']['min']['value']==EXPECTED['leave_min']
    assert output['core']['leave_one_out']['max']['value']==EXPECTED['leave_max']
    assert output['core']['leave_one_out']['sign_change']==EXPECTED['leave_sign_change']
    assert [output['missing_bounds'][x]['value'] for x in ('lower','upper')]==EXPECTED['bounds_r3']
    assert output['module_pairs']['BF']['n']==EXPECTED['BF_n']
    assert output['module_pairs']['BF']['mean']['value']==EXPECTED['BF_all_pairs_mean']
    assert output['module_on_B_C']['BF']['n']==2
    assert [v['mean']['value'] for v in output['UF3']['contrasts'].values()]==EXPECTED['UF3']
    assert [v['mean']['value'] for v in output['UF4']['contrasts'].values()]==EXPECTED['UF4']
    assert output['UF3']['quartets'][0]['reference_id']==output['UF4']['quartets'][0]['reference_id']
    assert len(output['cells'])==24 and output['missing_bounds']['denominator']==9


@pytest.mark.parametrize('complete_blocks',[0,1,2])
def test_n_zero_one_two_rules_for_core_and_quartets(complete_blocks):
    s=hand_snapshot(2,2)
    for row in s['rows']:
        if row['plan']['block_index']<=complete_blocks:set_f(row,'1')
    output=compute(s)
    assert output['n_C']==complete_blocks
    assert (output['core']['mean']['value'] is None)==(complete_blocks==0)
    for stats in [output['core'], *output['UF3']['contrasts'].values(), *output['UF4']['contrasts'].values()]:
        assert stats['n']==complete_blocks
        assert (stats['sample_sd']['value'] is None)==(complete_blocks<2)
        assert (stats['leave_one_out']['min']['value'] is None)==(complete_blocks<2)


def test_frozen_cases_assertions_failure_missing_and_T_logic():
    cases=[c for c in CASES if c['module']=='BF']
    entries=[result(i) for i in range(1,6)]+[result(6,'technical_missing')]
    reviews={k:review() for k in ('T1','T2','T3','T4','T5')}
    profile=functional(cases,entries,reviews)
    assert profile['passed_cases']==5 and profile['frozen_case_count']==6 and profile['missing_case_count']==1
    assert profile['raw_pass_ratio']['value']=='5/6' and profile['F']['value'] is None
    reviews['T2']=review(0);reviews.pop('T3')
    profile=functional(cases,entries,reviews)
    assert profile['T']['value']=='0' and profile['F_sixths']==0 and profile['F']['value']=='0'
    reviews.pop('T2');assert functional(cases,entries,reviews)['F']['value'] is None
    cases[0]={**cases[0],'assertions':['a1','extra']}
    entries[0]=result(1,'failed')
    profile=functional(cases,entries,reviews)
    assert profile['R']['R1']['value']=='0' and profile['missing_assertion_count']==2
    entries[0]=result(1)
    assert functional(cases,entries,reviews)['R']['R1']['value'] is None
    with pytest.raises(ValueError):functional(cases,entries+[entries[0]],reviews)


def test_missing_bounds_include_unstarted_and_no_failure_from_status():
    s=hand_snapshot(1,1)
    for row in s['rows']:
        row.update(run_id=None,candidate_hash=None,state=None,missing='not_started')
    output=compute(s)
    assert output['missing_bounds']['lower']['value']=='-1' and output['missing_bounds']['upper']['value']=='1'
    assert output['n_C']==0 and all(r['functional']['F']['value'] is None for r in output['cells'])
    assert output['static_validity']['denominator']==0


def test_static_completed_L_zero_has_D_and_own_valid_sets():
    s=hand_snapshot(1,1)
    s['rows'][0]['static_report']={'D':0,'L':0,'analysis_complete':True,'analysable':False}
    s['rows'][1]['static_report']={'D':1,'L':2,'analysis_complete':True,'analysable':True}
    o=compute(s)
    assert o['static_validity']['valid_analysis_ratio']['value']=='1/6'
    assert len(o['static_validity']['valid_sets']['D'])==2 and len(o['static_validity']['valid_sets']['S'])==1
    assert o['cells'][0]['static']['S']['value'] is None and o['cells'][1]['static']['S']['value']=='50'


def test_decimal_context_does_not_change_functional_descriptives():
    s=filled_snapshot()
    with localcontext() as ctx:
        ctx.prec=6;a=compute(s)
    with localcontext() as ctx:
        ctx.prec=70;b=compute(s)
    assert a==b


def test_foreign_scope_duplicates_and_missing_planned_rows_rejected():
    s=hand_snapshot()
    s['rows'][0]['purpose']='free_test'
    with pytest.raises(ValueError):compute(s)
    s=hand_snapshot();s['rows'][1]['run_id']=s['rows'][0]['run_id']
    with pytest.raises(ValueError):compute(s)
    s=hand_snapshot();s['rows'].pop()
    with pytest.raises(ValueError):compute(s)


def test_exports_plot_data_exact_core_and_repeatable_bytes():
    o=compute(filled_snapshot());first=render(o);second=render(o)
    assert first==second
    plot=json.loads(first['plot-data.json'][0]);assert len(plot['points'])==24 and plot['core']==o['core']
    table=list(csv.DictReader(StringIO(first['cells.csv'][0].decode('utf-8'))))
    assert len(table)==24 and all(r['data_hash']==o['data_hash'] for r in table)
    manifest=json.loads(first['manifest.json'][0])
    for name,item in manifest['files'].items():
        assert item['data_hash']==o['data_hash'] and item['analysis_version']==VERSION
        assert 'unit' in item and ('denominator_sets' in item or 'denominator_ids' in item)
    assert first['kontext-verteilungen.svg'][0].startswith(b'<?xml') and first['kontext-verteilungen.png'][0][:8]==b'\x89PNG\r\n\x1a\n'


def test_tokens_alternative_usage_never_doubled_and_conflict_missing():
    native={'prompt_tokens':10,'completion_tokens':2,'total_tokens':12}
    billing={'native_usage_evidence':[{'kind':'response','native':native},{'kind':'metadata','native':{'tokens_prompt':10,'tokens_completion':2}}], 'evidence_ids':['r','m']}
    cost={'evidence':[{'call_id':'c','attempts':[{'transport_id':'t','billing':billing}]}]}
    result=tokens(cost)
    assert result['summary']['input']['value']=='10' and result['summary']['output']['value']=='2'
    assert result['summary']['reasoning']['value'] is None
    billing['native_usage_evidence'][1]['native']['tokens_prompt']=11
    assert tokens(cost)['summary']['input']['value'] is None

from research_env.analysis_store import Analyses
from research_env.adapter import CallJournal
from research_env.artifacts import IntegrityError
from research_env.register import GateError, Register
import m7_fixture


@pytest.fixture
def stored(tmp_path):
    env=m7_fixture.make_environment(tmp_path/'instance')
    yield env
    env[0].close()


def confirm(service, proposal):
    return service.confirm(proposal['proposal_id'],expected_input_hash=proposal['input_hash'],acknowledged_selection=proposal['selected_revisions'],
        acknowledged_open=proposal['open_decisions'],confirmed_by='TECHNICAL-FIXTURE:simulated M7 confirmer',decision='SYNTHETIC explicit confirmation of these exact IDs/open criteria',software_commit='synthetic-test-source-binding',synthetic=True)


def test_real_selection_confirmation_CAS_historical_analysis_and_backup_gate(stored,tmp_path):
    env=stored;r,settings,store,f,freeze,backups=env
    run,comp,raw=m7_fixture.start(env,tmp_path)
    first=m7_fixture.measurement(env,run,comp,raw,passed=6)
    integration=m7_fixture.measurement(env,run,comp,raw,key='T4_integration')
    for criterion in ('T1','T2','T3','T4','T5'):m7_fixture.review(env,run,comp,raw,criterion,measurement_ids=(integration.id,) if criterion=='T4' else ())
    static=m7_fixture.measurement(env,run,comp,raw,key='static_DLS',report={'D':0,'L':0,'S':None,'analysis_complete':True,'analysable':False})
    service=Analyses(r,CallJournal.cost_view(r))
    proposal=service.propose(freeze.id)
    assert not r.all(AnalysisRun)
    assert proposal['selected_revisions'][str(run.planned_run_id)]['measurements']['functional_R']['revision_id']==str(first.id)
    assert proposal['preview']['static_validity']['valid_analysis_ratio']['value']=='1'
    with pytest.raises(GateError):service.confirm(proposal['proposal_id'],expected_input_hash=proposal['input_hash'],acknowledged_selection={},acknowledged_open={},confirmed_by='test',decision='test',software_commit='test',synthetic=True)
    m7_fixture.secure(env,tmp_path/'before-analysis')
    # New backup/receipt are nonsubstantive; proposing/confirming must preserve exact raw inputs.
    analysis=confirm(service,proposal)
    original=canonical(analysis)
    assert r.backup_status(freeze.id)=='stale'
    assert service.recalculate(analysis.id)==proposal['preview']
    assert confirm(service,proposal).id==analysis.id
    newer=m7_fixture.measurement(env,run,comp,raw,passed=0)
    m7_fixture.measurement(env,run,comp,raw,completion='draft')
    current=service.propose(freeze.id)
    chosen=current['selected_revisions'][str(run.planned_run_id)]['measurements']['functional_R']
    assert chosen['revision_id']==str(newer.id)
    selected=next(x for x in current['preview']['cells'] if x['run_id']==str(run.id))
    assert selected['functional']['F']['value']=='0'
    assert canonical(r.get(analysis.id))==original and service.recalculate(analysis.id)==proposal['preview']
    r.add(RevisionInvalidation(code='M7-T4-INVALID-'+str(uuid4()),revision_id=integration.id,reason='Synthetic T4 defect',person='TECHNICAL-FIXTURE',defect_evidence_id=raw.id))
    assert service.propose(freeze.id)['selected_revisions'][str(run.planned_run_id)]['reviews']['T4']['revision_id'] is None
    for m in (first,newer):r.add(RevisionInvalidation(code='M7-ALL-INVALID-'+str(uuid4()),revision_id=m.id,reason='Synthetic instrument defect',person='TECHNICAL-FIXTURE',defect_evidence_id=raw.id))
    invalid=service.propose(freeze.id)
    assert invalid['selected_revisions'][str(run.planned_run_id)]['measurements']['functional_R']['revision_id'] is None
    with pytest.raises(GateError):confirm(service,current)
    assert r.connection.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


@pytest.mark.parametrize('cause,proven',[('content_failure',True),('technical_failure',False),('interrupted',False),('outcome_unknown',False)])
def test_actual_absence_receipt_selected_and_invalidated(stored,tmp_path,cause,proven):
    r,settings,store,f,freeze,backups=stored
    run,_,_=m7_fixture.start(stored,tmp_path,no_candidate=True,cause=cause)
    receipt=m7_fixture.absence(stored,run,proven=proven)
    service=Analyses(r,CallJournal.cost_view(r));proposal=service.propose(freeze.id)
    selected=next(x for x in proposal['preview']['cells'] if x['run_id']==str(run.id))
    assert selected['candidate_hash'] is None
    assert selected['functional']['F']['value']==('0' if proven else None)
    assert selected['functional']['T_criteria']['T2']['value'] is None
    r.add(RevisionInvalidation(code='M7-ABSENCE-INVALID-'+str(uuid4()),revision_id=receipt.id,reason='Synthetic receipt defect',person='TECHNICAL-FIXTURE',defect_evidence_id=receipt.id))
    current=service.propose(freeze.id)
    assert current['selected_revisions'][str(run.planned_run_id)]['measurements']['candidate_absence']['revision_id'] is None
    assert next(x for x in current['preview']['cells'] if x['run_id']==str(run.id))['functional']['F']['value'] is None


def test_exact_source_bytes_required_for_old_recalculation(stored,tmp_path):
    r,settings,store,f,freeze,backups=stored
    service=Analyses(r,CallJournal.cost_view(r));proposal=service.propose(freeze.id);analysis=confirm(service,proposal)
    artifact=r.get(UUID(analysis.selected_inputs['_m7']['outputs']['analysis.json']),Artifact)
    path=settings.artifacts/store.object_path(artifact.sha256);path.chmod(0o600);path.write_bytes(b'{}')
    with pytest.raises(IntegrityError):service.recalculate(analysis.id)

from datetime import datetime, timezone, timedelta
from research_env.analysis_resources import area_costs
from test_adapter import prepared, response
from research_env.providers import MockAdapter, WireResponse


@pytest.mark.parametrize('gap',['none','excluded','possible'])
def test_time_partition_actual_pause_provider_wait_and_process_restart(gap):
    run,p1,p2=uuid4(),uuid4(),uuid4();evidence=(uuid4(),)
    def segment(sequence,kind,process,start,end,connection=None):
        return TimeInterval(code='SEG-'+str(uuid4()),run_id=run,sequence=sequence,kind=kind,process_id=process,end_process_id=process,
            monotonic_start=Decimal(start) if start is not None else None,monotonic_end=Decimal(end) if end is not None else None,
            started_at=None,ended_at=None,evidence_ids=evidence,reason='SYNTHETIC known interval attribution',connection=connection)
    first=segment(1,'active',p1,100,106) # Includes provider/tool/wait and merely requested pause.
    if gap=='none':
        pause=segment(2,'pause',p1,106,109);outage=segment(3,'outage',p1,109,111)
        second=segment(4,'active',p1,111,115);parts=[first,pause,outage,second]
    else:
        pause=segment(2,'excluded_gap' if gap=='excluded' else 'unknown_active',None,None,None,
            IntervalConnection(previous_interval_id=first.id,relation='contiguous',evidence_ids=evidence))
        second=segment(3,'active',p2,4,8,IntervalConnection(previous_interval_id=pause.id,relation='contiguous',evidence_ids=evidence))
        parts=[first,pause,second]
    costs={'evidence':[],'currency':None,'known_subtotal':None,'amount':None,'status':'unresolved'}
    raw={'costs':costs,'intervals':[x.model_dump(mode='json') for x in parts],'coverage_complete':True,'resource_records':[],'metric_observations':[],'model_calls':[]}
    result=resource_profile(raw)
    assert result['pipeline_seconds']['value']==('10' if gap!='possible' else None)
    assert result['timing']['known_partial_seconds']['active']=='10'
    assert result['total_seconds']['value']==('15' if gap=='none' else None)
    if gap=='none':
        assert result['pause_seconds']['value']=='3' and result['outage_seconds']['value']=='2'
    else:
        assert result['pause_seconds']['value'] is None


def test_effective_billing_actual_journal_alternative_metadata_correction(tmp_path):
    metadata=[WireResponse(200,canonical({'data':{'id':'synthetic-generation-1','total_cost':'0.25','tokens_prompt':10,'tokens_completion':2}}).encode(),{}),
              WireResponse(200,canonical({'data':{'id':'synthetic-generation-1','total_cost':'0.30','tokens_prompt':10,'tokens_completion':2}}).encode(),{})]
    adapter=MockAdapter([response(usage={'prompt_tokens':10,'completion_tokens':2,'cost':'0.25'})],metadata=lambda generation: metadata.pop(0))
    r,settings,j,a,call,order=prepared(tmp_path,adapter)
    a.execute_attempt(j,call.id)
    before=j.effective_costs(call.run_id);tid=UUID(before['evidence'][0]['attempts'][0]['transport_id'])
    j.fetch_metadata(a,tid)
    same=j.effective_costs(call.run_id)
    assert before['amount']==same['amount']=='0.25' and tokens(same)['summary']['input']['value']=='10'
    j.fetch_metadata(a,tid)
    conflict=j.effective_costs(call.run_id)
    assert conflict['amount'] is None and conflict['status']=='unresolved' and len(r.all(CostEntry))==1
    assert before['evidence_revision']!=same['evidence_revision']!=conflict['evidence_revision']
    with localcontext() as ctx:
        ctx.prec=2;ctx.rounding=ROUND_UP;ctx.traps[Inexact]=True;ctx.traps[Rounded]=True
        untouched=(ctx.prec,ctx.rounding,dict(ctx.traps),dict(ctx.flags))
        assert j.effective_costs(call.run_id)==conflict
        assert untouched==(ctx.prec,ctx.rounding,dict(ctx.traps),dict(ctx.flags))
    r.close()


def test_operating_area_partial_estimated_missing_costs_and_own_denominators():
    def resource(run_id,amount,partial=None,status='known',estimate=None):
        costs={'status':status,'amount':amount,'known_subtotal':partial if partial is not None else amount,'currency':'USD',
            'evidence':[],'evidence_revision':'a'*64,'phase_revision':2}
        result=resource_profile({'costs':costs,'intervals':[],'coverage_complete':False,'resource_records':[],
            'metric_observations':([{'metric':'cost_estimate','value':{'status':'estimated','value':estimate,'unit':'USD','source':'SYNTHETIC'}}] if estimate else []),'model_calls':[]})
        return result
    rows=[{'run_id':'main1','purpose':'main','resources':resource('main1','0.25')},
          {'run_id':'main2','purpose':'main','resources':resource('main2',None,'0.50','partial',estimate='2.0')},
          {'run_id':'free1','purpose':'free_test','resources':resource('free1','99')},
          {'run_id':'pilot1','purpose':'pilot','resources':resource('pilot1','1.0')}]
    areas=area_costs(rows);main=areas['main']['currency_groups']['USD']
    assert main['amount'] is None and main['known_subtotal']=='0.75' and main['estimated_subtotal']=='2.0'
    assert main['run_ids']==['main1','main2'] and main['missing_run_ids']==['main2']
    assert areas['free_test']['currency_groups']['USD']['amount']=='99' and not areas['free_test']['research_cost_scope']
    assert areas['pilot']['currency_groups']['USD']['amount']=='1.0'


def test_static_raw_partial_L_never_enters_valid_FQ_set():
    s=hand_snapshot(1,1);s['rows'][0]['static_report']={'D':None,'L':5,'analysis_complete':False,'cause':'Incomplete raw static attempt'}
    o=compute(s)
    assert o['cells'][0]['static']['raw_report']['L']==5
    assert o['cells'][0]['static']['L']['value'] is None and not o['static_validity']['valid_sets']['L']


def test_saved_proposal_cannot_change_exact_raw_observations(stored):
    r,settings,store,f,freeze,backups=stored
    service=Analyses(r,CallJournal.cost_view(r));p=service.propose(freeze.id)
    changed=deepcopy(p['selected_revisions']);pid=next(iter(changed));changed[pid]['missing']='favorable alternative'
    with pytest.raises(GateError):service.confirm(p['proposal_id'],expected_input_hash=p['input_hash'],acknowledged_selection=changed,
        acknowledged_open=p['open_decisions'],confirmed_by='TECHNICAL-FIXTURE:forbidden alternative',decision='Synthetic forbidden free revision choice',software_commit='synthetic',synthetic=True)
    assert not r.all(AnalysisRun)


@pytest.mark.parametrize('gap',['none','excluded','possible'])
def test_real_SQLite_interval_partition_reaches_analysis(stored,tmp_path,gap):
    r,settings,store,f,freeze,backups=stored
    run,comp,raw=m7_fixture.start(stored,tmp_path)
    m7_fixture.time_partition(stored,run,gap=gap)
    p=Analyses(r,CallJournal.cost_view(r)).propose(freeze.id)
    resource=next(x['resources'] for x in p['preview']['cells'] if x['run_id']==str(run.id))
    assert resource['pipeline_seconds']['value']==('10' if gap!='possible' else None)
    assert resource['timing']['known_partial_seconds']['active']=='10'
    assert resource['total_seconds']['value']==('15' if gap=='none' else None)


def test_cost_statistics_remain_decimal_and_context_independent():
    s=hand_snapshot(2,1)
    for row in s['rows']:
        set_f(row,'1')
        amount='0.25' if row['plan']['block_index']==1 else '0.50'
        row['resources']['costs']={'status':'known','amount':amount,'known_subtotal':amount,'currency':'USD'}
    with localcontext() as ctx:
        ctx.prec=6;low=compute(s)
    with localcontext() as ctx:
        ctx.prec=70;high=compute(s)
    assert low==high
    stats=low['configurations']['C-BF-0']['profiles']['cost:USD']
    assert stats['mean']['value']=='0.375' and stats['values'][0]['value']=='0.25'

from decimal import ROUND_DOWN, ROUND_UP, Inexact, Rounded
from research_env.numeric import sum_exact


@pytest.mark.parametrize('rounding',[ROUND_DOWN,ROUND_UP,'ROUND_HALF_EVEN'])
def test_entire_M7_decimal_profiles_ignore_ambient_rounding_precision_traps(rounding):
    s=filled_snapshot()
    for row in s['rows']:
        row['resources']['costs']={'status':'known','amount':'0.1234567890123456789012345678901','known_subtotal':'0.1234567890123456789012345678901','currency':'USD'}
    reference=compute(s)
    with localcontext() as ctx:
        ctx.prec=6;ctx.rounding=rounding;ctx.Emax=2;ctx.Emin=-2
        ctx.traps[Inexact]=True;ctx.traps[Rounded]=True
        assert compute(s)==reference
        assert sum_exact([Decimal('0.1234567890123456789012345678901'),Decimal('0.0000000000000000000000000000009')])==Decimal('0.1234567890123456789012345678910')
        # Export bytes are also generated from identical explicit numeric values.
        assert render(reference)==render(compute(s))


@pytest.mark.parametrize('cause',['content_failure','technical_failure','interrupted','outcome_unknown'])
@pytest.mark.parametrize('gap',['none','excluded','possible'])
def test_real_main_no_candidate_resource_time_independent_of_F(stored,tmp_path,cause,gap):
    r,settings,store,f,freeze,backups=stored
    run,_,_=m7_fixture.start(stored,tmp_path,no_candidate=True,cause=cause)
    m7_fixture.absence(stored,run,proven=cause=='content_failure')
    m7_fixture.time_partition(stored,run,gap=gap)
    service=Analyses(r,CallJournal.cost_view(r));reference=service.propose(freeze.id)
    cell=next(x for x in reference['preview']['cells'] if x['run_id']==str(run.id))
    assert cell['candidate_hash'] is None and cell['resources']['pipeline_seconds']['value']==('10' if gap!='possible' else None)
    assert cell['resources']['total_seconds']['value']==('15' if gap=='none' else None)
    assert cell['functional']['F']['value']==('0' if cause=='content_failure' else None)
    with localcontext() as ctx:
        ctx.prec=2;ctx.rounding=ROUND_UP;ctx.traps[Inexact]=True;ctx.traps[Rounded]=True
        untouched=(ctx.prec,ctx.rounding,dict(ctx.traps),dict(ctx.flags))
        again=service.propose(freeze.id)
        assert untouched==(ctx.prec,ctx.rounding,dict(ctx.traps),dict(ctx.flags))
    assert reference['input_hash']==again['input_hash'] and reference['preview']==again['preview']


def test_real_M5_format_failure_no_candidate_keeps_complete_UF5(tmp_path):
    from test_pipeline import setup, finish
    bad=response(choices=[{'message':{'content':'no json'},'finish_reason':'stop'}])
    r,settings,store,run,job,pipeline,scheduler,adapter,runner=setup(tmp_path,outputs=[bad])
    finish(scheduler,run)
    assert r.state(run.id).seal=='no_candidate' and r.state(run.id).execution=='terminal'
    assert pipeline.binding(run.id)['status']=='completed'
    service=Analyses(r,CallJournal.cost_view(r));profile=service._resources(run)
    assert profile['pipeline_seconds']['status']=='observed' and profile['pipeline_seconds']['value']==pipeline.summary(run.id)['timing']['active']['value']
    with localcontext() as ctx:
        ctx.prec=2;ctx.rounding=ROUND_DOWN;ctx.traps[Inexact]=True;ctx.traps[Rounded]=True
        assert service._resources(run)==profile
    assert adapter.send_count==1 and not r.all(AnalysisRun)
    pipeline.close();r.close()


@pytest.mark.parametrize('kind',['interval','connection','metric','phase_metric','pilot_metric','call_message','call_input','resource_profile'])
@pytest.mark.parametrize('damage',['missing','corrupt'])
@pytest.mark.parametrize('stage',['propose','confirm'])
def test_transitive_resource_CAS_integrity_before_proposal_and_confirmation(stored,tmp_path,kind,damage,stage):
    r,settings,store,f,freeze,backups=stored
    run,_,_=m7_fixture.start(stored,tmp_path,no_candidate=True,cause='technical_failure')
    m7_fixture.time_partition(stored,run,gap='none')
    record,artifact=m7_fixture.resource_evidence(stored,run,kind)
    service=Analyses(r,CallJournal.cost_view(r));proposal=service.propose(freeze.id)
    snapshot=json.loads(store.read(UUID(proposal['snapshot_artifact_id'])))
    assert str(record.id) in snapshot['register_evidence_hashes'] and str(artifact.id) in snapshot['register_evidence_hashes']
    target=store.root/store.object_path(artifact.sha256);original=target.read_bytes()
    saved=tmp_path/'saved-original-CAS-bytes';target.rename(saved)
    if damage=='corrupt':
        target.write_bytes(b'SYNTHETIC deliberately damaged isolated fixture');target.chmod(0o444)
    try:
        # Only run-owned records are exported by standalone _resources.
        if kind!='phase_metric':
            with pytest.raises((FileNotFoundError,IntegrityError)):
                service._resources(f['pilot'] if kind=='pilot_metric' else run)
        with pytest.raises((FileNotFoundError,IntegrityError)):
            service.propose(freeze.id) if stage=='propose' else confirm(service,proposal)
        assert not r.all(AnalysisRun) and r.connection.execute('SELECT count(*) FROM analysis_confirmation').fetchone()[0]==0
    finally:
        if target.exists():target.unlink()
        saved.rename(target)
    assert target.read_bytes()==original and service.propose(freeze.id)['input_hash']==proposal['input_hash']


@pytest.mark.parametrize('seal',['pending','integrity_error'])
def test_nonsealed_candidate_id_never_enters_final_candidate_denominator(stored,tmp_path,seal):
    r,settings,store,f,freeze,backups=stored
    run,_,_=m7_fixture.start(stored,tmp_path);m7_fixture.time_partition(stored,run,gap='none')
    service=Analyses(r,CallJournal.cost_view(r));before=service.propose(freeze.id)
    assert before['preview']['static_validity']['denominator']==1
    r.set_state(run.id,r.state(run.id).model_copy(update={'seal':seal}),reason='SYNTHETIC final seal is not valid')
    proposal=service.propose(freeze.id);cell=next(x for x in proposal['preview']['cells'] if x['run_id']==str(run.id))
    assert cell['state']['candidate_id'] is not None and cell['candidate_hash'] is None
    assert proposal['preview']['static_validity']['denominator']==0 and cell['functional']['F']['value'] is None
    assert cell['resources']['pipeline_seconds']['value']=='10'
    confirmed=confirm(service,proposal)
    assert confirmed.denominators['candidates'].value==0

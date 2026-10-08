"""Membership witnesses are separate from calculations and their denominators."""
from copy import deepcopy

import pytest

from research_env.analysis import compute, value
from research_env.analysis_report_data import report_context
from test_analysis import set_f
from test_analysis_report_tables import fixture


def context(snapshot):
    result = compute(snapshot)
    before = deepcopy(result)
    report = report_context(result)['report']
    assert result == before
    return result, report['table_population'], report


def ids(group):
    return {run['id'] for run in group['runs']}


def keys(group):
    return {run['cell_key'] for run in group['runs']}


def target(snapshot, key, block):
    return next(row for row in snapshot['rows'] if row['plan']['cell_key'] == key and row['plan']['block_index'] == block)


def test_core_membership_excludes_exploratory_runs_and_incomplete_blocks():
    snapshot = fixture()
    set_f(target(snapshot, 'C-UP-0', 2), None)
    result, pop, _ = context(snapshot)
    group = pop['core']['groups'][0]
    assert group['n'] == 2 and group['run_count'] == 12
    assert all(row['cell_key'].startswith('C-') and row['block_index'] in (1,3) for row in group['runs'])
    assert pop['module_pairs']['BF']['groups'][0]['n'] == 3
    assert pop['module_common']['BF']['groups'][0]['n'] == 2
    assert pop['module_common']['BF']['groups'][0]['run_count'] == 4
    assert pop['module_common']['BF']['groups'][1]['run_count'] == 12
    assert group['n'] == result['core']['n']


@pytest.mark.parametrize('question,name,expected', [
    ('UF3','AB-AA', {'C-SQL-1','E-AB'}), ('UF3','BA-BB', {'E-BA','E-BB'}),
    ('UF3','BA-AA', {'E-BA','C-SQL-1'}), ('UF3','BB-AB', {'E-BB','E-AB'}),
    ('UF4','Planner11-01', {'C-SQL-1','E-P0R1'}),
    ('UF4','Planner10-00', {'E-P1R0','E-P0R0'}),
    ('UF4','Review11-10', {'C-SQL-1','E-P1R0'}),
    ('UF4','Review01-00', {'E-P0R1','E-P0R0'}),
    ('UF4','Interaction', {'C-SQL-1','E-P0R0','E-P0R1','E-P1R0'}),
])
def test_quartet_eligibility_and_arithmetic_members_are_distinct(question, name, expected):
    _, pop, _ = context(fixture())
    groups = pop['exploration'][question]['F'][name]['groups']
    assert keys(groups[0]) == expected
    assert groups[0]['n'] == 2
    assert groups[0]['run_count'] == 2 * len(expected)
    assert all(row['block_index'] in (1,2) for group in groups for row in group['runs'])
    if len(expected) == 2:
        assert groups[1]['role'] == 'eligibility' and groups[1]['run_count'] == 8
    else:
        assert len(groups) == 1


def test_quartet_missing_unused_cell_still_excludes_block_from_contrast():
    snapshot = fixture()
    set_f(target(snapshot, 'E-BB', 2), None)
    _, pop, _ = context(snapshot)
    contrast = pop['exploration']['UF3']['F']['AB-AA']['groups'][0]
    assert contrast['n'] == 1 and {r['block_index'] for r in contrast['runs']} == {1}
    ref = pop['exploratory_profiles']['UF3']['C-SQL-1']['F:ratio']['groups'][0]
    assert ref['n'] == 2 and {r['block_index'] for r in ref['runs']} == {1,2}


def test_timing_static_and_status_rows_have_their_own_members():
    snapshot = fixture()
    row = target(snapshot, 'C-BF-0', 1)
    row['resources']['pipeline_seconds'] = value(unit='s', status='technical_missing', reason='Missing interval')
    row['static_report'] = {'analysis_complete': True, 'D': 0, 'L': 0}
    result, pop, report = context(snapshot)
    planned_id = row['plan']['id']
    assert planned_id not in ids(pop['profiles']['C-BF-0']['pipeline_seconds']['groups'][0])
    assert planned_id not in ids(pop['profiles']['C-BF-0']['S']['groups'][0])
    assert planned_id in ids(pop['profiles']['C-BF-0']['D']['groups'][0])
    assert pop['metrics']['S']['blocks']['groups'][0]['n'] == 2
    assert pop['metrics']['D']['blocks']['groups'][0]['n'] == 3
    status = next(r for r in report['extra']['coverage'] if r['scope']=='configuration'
        and r['group']=='C-BF-0' and r['metric']=='pipeline_seconds' and r['status']=='technical_missing')
    assert ids(status['population']['groups'][0]) == {planned_id}
    assert status['population']['groups'][0]['role'] == 'status'


def test_omitted_blocks_and_planned_bounds_are_not_conflated():
    result, pop, _ = context(fixture())
    omitted = result['core']['ids'][0]
    group = pop['sensitivity'][omitted]['groups'][0]
    assert group['n'] == 2 and group['run_count'] == 12
    assert all(r['block_id'] != omitted for r in group['runs'])
    bounds = pop['bounds']['groups'][0]
    assert bounds['role'] == 'planned' and bounds['n'] == 9 and bounds['run_count'] == 18
    assert all(r['cell_key'].startswith('C-') for r in bounds['runs'])


def test_external_resource_runs_do_not_receive_guessed_plan_positions():
    snapshot = fixture()
    snapshot['resource_areas']['pilot'] = {'n_started':1, 'run_ids':['external-pilot'],
        'tokens':{'total':{'status':'complete','valid_run_ids':['external-pilot']}},
        'currency_groups':{'USD':{'status':'complete','complete_run_ids':['external-pilot'],'partial_run_ids':[]}}}
    _, pop, _ = context(snapshot)
    for group in pop['areas']['pilot']['groups']:
        for run in group['runs']:
            assert run['run_id'] == 'external-pilot' and run['position'] is None and run['id'] is None


def test_render_has_run_links_without_changing_numeric_table_cells():
    from test_analysis_readability import report as render_report
    result = compute(fixture())
    page = render_report(result)
    assert len(page.select('.table-population')) > 30
    links = page.select('.table-population-runs a')
    anchor_ids = {tag['id'] for tag in page.select('[id]')}
    assert links and all(a['href'][1:] in anchor_ids for a in links)
    row = page.select_one('[data-metric="D"]')
    assert [td.get_text(' ', strip=True) for td in row.select('td')] == ['3','0','0','0','0','0','1, 2, 3']

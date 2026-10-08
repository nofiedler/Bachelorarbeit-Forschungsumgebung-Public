"""Figure populations and display data must match the fixed analysis exactly."""
from fractions import Fraction

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pytest

from research_env.analysis import compute, observed, value
from research_env.analysis_charts import figures
from test_analysis import filled_snapshot, hand_snapshot, set_f


@pytest.fixture
def chart_set():
    snapshot = filled_snapshot()
    for row in snapshot['rows']:
        row['static_report'] = {'D': 2, 'L': 37, 'analysis_complete': True}
        row['resources'] = {'pipeline_seconds': value('1.125', unit='s'),
                            'tokens': {'summary': {'total': {'status': 'complete', 'value': '123456'}}},
                            'costs': {'status': 'known', 'amount': '0.0123456789', 'currency': 'USD'}}
    # Partial data may carry a subtotal, but no chart may silently plot it as complete.
    selected = next(row for row in snapshot['rows'] if row['plan']['cell_key'] == 'C-BF-0'
                    and row['plan']['block_index'] == 1)
    selected['resources']['tokens']['summary']['total'] = {'status': 'partial', 'value': None, 'known_subtotal': '7'}
    selected['resources']['pipeline_seconds'] = value(unit='s', status='technical_missing')
    selected['resources']['costs'] = {'status': 'partial', 'amount': None, 'known_subtotal': '0.01', 'currency': 'USD'}
    result = compute(snapshot)
    charts = {name: (fig, info) for name, fig, info in figures(result)}
    yield result, charts, selected['plan']['id']
    for fig, _ in charts.values():
        plt.close(fig)


def test_paired_chart_preserves_every_exact_value_and_only_complete_pairs(chart_set):
    result, charts, _ = chart_set
    _, info = charts['kontext-verteilungen']
    cells = {r['id']: r for r in result['cells']}
    expected = {r['id']: str(observed(r['functional']['F'])) for r in result['cells']
                if r['cell_key'].startswith('C-') and observed(r['functional']['F']) is not None}
    assert {p['planned_id']: p['value'] for p in info['figure_data']['points']} == expected
    for pair in info['figure_data']['pairs']:
        first, second = (cells[pid] for pid in pair['planned_ids'])
        assert first['block_id'] == second['block_id'] == pair['block_id']
        assert first['cell_key'] == 'C-' + pair['module'] + '-0'
        assert second['cell_key'] == 'C-' + pair['module'] + '-1'
        assert first['id'] in expected and second['id'] in expected
    assert info['denominator_sets']['BF']['complete_pair_block_ids'] == result['module_pairs']['BF']['ids']
    assert info['n'] == len(expected)
    for module, data in info['denominator_sets'].items():
        assert len(data['planned_ids']) == 2 * result['r_C']
        assert set(data['observed_ids']).isdisjoint(data['missing_ids'])
        assert set(data['observed_ids'] + data['missing_ids']) == set(data['planned_ids'])


def test_resource_figures_exclude_partial_subtotals_and_keep_precise_coordinates(chart_set):
    result, charts, partial_id = chart_set
    for name in ('F-zeit', 'F-tokens', 'F-kosten-1'):
        fig, info = charts[name]
        assert partial_id not in info['denominator_ids']
        points = info['figure_data']['points']
        assert info['n'] == len(points)
        assert partial_id in info['denominator_sets']['BF']['excluded_incomplete_pair_ids']
        assert len({ax.get_xlim() for ax in fig.axes}) == 1
    cost = charts['F-kosten-1'][1]['figure_data']['points'][0]
    assert Fraction(cost['x_value']) == Fraction('0.0123456789')
    assert charts['F-tokens'][1]['figure_data']['points'][0]['x_value'] == '123456'
    for name in ('F-zeit', 'F-tokens', 'F-kosten-1'):
        points = charts[name][1]['figure_data']['points']
        for point in points:
            tied = [p for p in points if p['planned_id'] in point['coincident_planned_ids']]
            assert len({p['display_offset_points'] for p in tied}) == len(tied)
            assert sum(p['display_offset_points'] for p in tied) == 0
            assert len({(p['x_value'], p['value']) for p in tied}) == 1


def test_block_and_sensitivity_metadata_separate_observation_units(chart_set):
    result, charts, _ = chart_set
    _, blocks = charts['blockunterschiede']
    assert blocks['denominator_ids'] == result['B_C']
    assert blocks['denominator_sets']['module_pairs']['BF'] == result['module_pairs']['BF']['ids']
    assert {p['block_id']: Fraction(p['value']) for p in blocks['figure_data']['points'] if p['panel'] == 'core'} == {
        row['block_id']: observed(row['g'])*100 for row in result['blocks'] if row['in_B_C']}
    _, sensitivity = charts['sensitivitaet']
    for point in sensitivity['figure_data']['points']:
        assert point['omitted_block_id'] not in point['retained_block_ids']
        assert len(point['retained_block_ids']) == result['n_C'] - 1
        included = [observed(row['g']) for row in result['blocks'] if row['block_id'] in point['retained_block_ids']]
        assert Fraction(point['value']) == sum(included) / len(included) * 100
    assert Fraction(sensitivity['figure_data']['lower_bound']) == observed(result['missing_bounds']['lower']) * 100
    assert Fraction(sensitivity['figure_data']['upper_bound']) == observed(result['missing_bounds']['upper']) * 100
    assert sensitivity['denominator_sets']['bounds_pair_denominator'] == 3 * result['r_C']


def test_exploratory_only_complete_B_E_quartets_are_connected():
    snapshot = hand_snapshot(3, 2)
    for row in snapshot['rows']:
        set_f(row, '1/2')
    missing = next(r for r in snapshot['rows'] if r['plan']['cell_key'] == 'E-AB'
                   and r['plan']['block_index'] == 1)
    set_f(missing, None)
    result = compute(snapshot)
    charts = {name: (fig, info) for name, fig, info in figures(result)}
    try:
        for name, question in (('modellzuordnung', 'UF3'), ('pipeline-varianten', 'UF4')):
            info = charts[name][1]
            data = info['figure_data']
            assert {q['block_id'] for q in data['quartets']} == set(result[question]['complete_block_ids'])
            assert all(p['block_index'] <= 2 for p in data['points'])
            assert info['n'] == len(data['points'])
            assert data['n_complete_quartets'] == result[question]['n']
            assert len(info['denominator_sets']['cells']['C-SQL-1']['planned_ids']) == 2
            # The common F=1/2 test case must still display every block separately.
            assert len({(p['display_x'], p['value']) for p in data['points']}) == len(data['points'])
        assert charts['modellzuordnung'][1]['figure_data']['n_complete_quartets'] == 1
        assert charts['pipeline-varianten'][1]['figure_data']['n_complete_quartets'] == 2
        incomplete = [p for p in charts['modellzuordnung'][1]['figure_data']['points'] if p['block_index'] == 1]
        assert len(incomplete) == 3 and not any(p['in_complete_quartet'] for p in incomplete)
    finally:
        for fig, _ in charts.values():
            plt.close(fig)


def test_module_differences_expose_common_and_additional_pairs_with_exact_run_ids():
    snapshot = hand_snapshot(3, 2)
    for row in snapshot['rows']:
        set_f(row, '1/2')
        if row['plan']['cell_key'] == 'C-SQL-0' and row['plan']['block_index'] == 2:
            set_f(row, None)
        if row['plan']['cell_key'] == 'C-BF-1' and row['plan']['block_index'] == 3:
            set_f(row, '5/6')
    result = compute(snapshot)
    charts = {name: (fig, info) for name, fig, info in figures(result)}
    try:
        assert len(charts) == 13
        info = charts['modul-kontextunterschiede'][1]
        points = info['figure_data']['points']
        assert len(points) == 8
        assert len({(p['display_x'], p['value']) for p in points}) == len(points)
        lookup = {(r['block_id'], r['cell_key']): r for r in result['cells']}
        for point in points:
            pair = [lookup[(point['block_id'], f'C-{point["module"]}-{k}')] for k in ('0', '1')]
            assert point['run_ids'] == [r['run_id'] for r in pair]
            assert point['planned_ids'] == [r['id'] for r in pair]
            assert Fraction(point['value']) == (observed(pair[1]['functional']['F']) -
                                                observed(pair[0]['functional']['F'])) * 100
            assert point['in_B_C'] == (point['block_id'] in result['B_C'])
        additional = [p for p in points if not p['in_B_C']]
        assert {(p['module'], p['block_index']) for p in additional} == {('BF', 2), ('UP', 2)}
        assert next(p['value'] for p in points if p['module'] == 'BF' and p['block_index'] == 3) == '100/3'
        for module, group in info['denominator_sets'].items():
            assert group['complete_pair_block_ids'] == result['module_pairs'][module]['ids']
            assert set(group['B_C_block_ids'] + group['additional_pair_block_ids']) == set(group['complete_pair_block_ids'])
    finally:
        for fig, _ in charts.values():
            plt.close(fig)


def test_repeatability_all_observations_exact_median_and_observed_range():
    snapshot = hand_snapshot(3, 2)
    for row in snapshot['rows']:
        set_f(row, '1/2')
        if row['plan']['cell_key'] == 'C-BF-0':
            set_f(row, {1: '1/6', 2: '1/2', 3: '5/6'}[row['plan']['block_index']])
        if row['plan']['cell_key'] == 'C-SQL-0' and row['plan']['block_index'] == 2:
            set_f(row, None)
    result = compute(snapshot)
    charts = {name: (fig, info) for name, fig, info in figures(result)}
    try:
        info = charts['streuung-einzelwerte'][1]
        points = info['figure_data']['points']
        expected = {r['id']: str(observed(r['functional']['F'])) for r in result['cells']
                    if r['cell_key'].startswith('C-') and observed(r['functional']['F']) is not None}
        assert {p['planned_id']: p['value'] for p in points} == expected
        assert len(points) == len({(p['display_x'], p['value']) for p in points}) == 17
        summaries = {s['cell_key']: s for s in info['figure_data']['summaries']}
        assert len(summaries) == 6
        assert (summaries['C-BF-0']['minimum'], summaries['C-BF-0']['median'], summaries['C-BF-0']['maximum']) == ('1/6', '1/2', '5/6')
        assert summaries['C-SQL-0']['n'] == 2
        assert summaries['C-SQL-0']['median'] == '1/2'
        assert len(info['denominator_sets']['C-SQL-0']['missing_ids']) == 1
        # Presentation positions depend only on fixed block order, not global random state.
        for fig, _ in charts.values():
            plt.close(fig)
        second = figures(result)
        try:
            repeat = next(data for name, _, data in second if name == 'streuung-einzelwerte')
            assert repeat['figure_data'] == info['figure_data']
        finally:
            for _, fig, _ in second:
                plt.close(fig)
    finally:
        for fig, _ in charts.values():
            plt.close(fig)


@pytest.mark.parametrize('n', [0, 1])
def test_empty_and_single_block_have_no_invented_leave_one_out(n):
    snapshot = hand_snapshot(1, 1)
    if n:
        for row in snapshot['rows']:
            set_f(row, '1/2')
    result = compute(snapshot)
    for name, fig, info in figures(result):
        try:
            if name == 'sensitivitaet':
                assert info['n'] == 0
                assert info['figure_data']['points'] == []
                assert info['figure_data']['lower_bound'] == ('0' if n else '-100')
                assert info['figure_data']['upper_bound'] == ('0' if n else '100')
            assert fig.get_facecolor() == (1., 1., 1., 1.)
            assert 'Konfidenzintervalle' not in info['title']
        finally:
            plt.close(fig)

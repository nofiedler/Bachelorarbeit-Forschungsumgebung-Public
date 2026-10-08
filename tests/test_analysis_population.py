"""Provenance follows existing plotting ID sets, never a new evidence selection."""
from copy import deepcopy
import csv
from io import StringIO
import json

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pytest

from research_env.analysis import compute, value
from research_env.analysis_charts import figures
from research_env.analysis_population import figure_population, population_rows
from test_analysis import filled_snapshot, hand_snapshot, set_f


@pytest.fixture(scope='module')
def chart_source():
    snapshot = filled_snapshot()
    for row in snapshot['rows']:
        if row['plan']['cell_key'] == 'C-SQL-1' and row['plan']['block_index'] == 3:
            set_f(row, '1/2')  # An observed point whose K0 partner is missing.
        row['static_report'] = {'D': 2, 'L': 37, 'analysis_complete': True}
        row['resources'] = {'pipeline_seconds': value('1.125', unit='s'),
            'tokens': {'summary': {'total': {'status': 'complete', 'value': '123456'}}},
            'costs': {'status': 'known', 'amount': '0.0123', 'currency': 'USD'}}
    excluded = next(r for r in snapshot['rows'] if r['plan']['cell_key'] == 'C-BF-0'
                    and r['plan']['block_index'] == 1)
    excluded['resources']['tokens']['summary']['total'] = {
        'status': 'partial', 'value': None, 'known_subtotal': '7'}
    excluded['resources']['costs']['currency'] = 'EUR'
    result = compute(snapshot)
    charts = {name: (fig, info) for name, fig, info in figures(result)}
    for fig, _ in charts.values():
        plt.close(fig)
    yield result, charts, excluded['plan']['id']


def groups(population):
    return {group['key']: group for group in population['groups']}


def test_all_figures_preserve_data_and_use_only_actual_matrix_positions(chart_source):
    result, charts, _ = chart_source
    before = deepcopy(result)
    lookup = {row['id']: row for row in result['cells']}
    for name, (_, info) in charts.items():
        original = deepcopy(info)
        population = figure_population(result, name, info)
        assert info == original
        assert len({row['planned_id'] for row in population['rows']}) == population['planned_run_count']
        for row in population['rows']:
            source = lookup[row['planned_id']]
            for key in ('run_id', 'position', 'cell_key', 'block_id', 'configuration_id', 'candidate_hash'):
                assert row[key] == source[key]
        used = {row['run_id'] for row in population['rows']
                if row['run_id'] and any(m['role'] == 'used' for m in row['memberships'])}
        assert population['used_run_count'] == len(used)
    assert result == before


def test_individual_points_and_pair_lines_have_separate_populations(chart_source):
    result, charts, _ = chart_source
    info = charts['kontext-verteilungen'][1]
    population = figure_population(result, 'kontext-verteilungen', info)
    by_group = groups(population)
    for module, source in info['denominator_sets'].items():
        values, pairs = by_group['values:' + module], by_group['paired-lines:' + module]
        assert set(values['used_planned_ids']) == set(source['observed_ids'])
        assert pairs['observation_count'] == len(source['complete_pair_block_ids'])
        assert all(len(pair['planned_ids']) == len(pair['positions']) == 2 for pair in pairs['observations'])
    # A measured failure F=0 is a real observation, never silently excluded.
    zero_ids = {p['planned_id'] for p in info['figure_data']['points'] if p['value'] == '0'}
    assert zero_ids
    assert zero_ids <= {pid for g in population['groups'] for pid in g['used_planned_ids']}
    assert any(set(by_group['values:' + m]['used_planned_ids']) -
               set(by_group['paired-lines:' + m]['used_planned_ids']) for m in ('BF', 'SQL', 'UP'))


def test_block_and_module_populations_never_expand_to_experimental_rows(chart_source):
    result, charts, _ = chart_source
    lookup = {row['id']: row for row in result['cells']}
    for name in ('blockunterschiede', 'modul-kontextunterschiede'):
        population = figure_population(result, name, charts[name][1])
        assert all(row['cell_key'].startswith('C-') for row in population['rows'])
        by_group = groups(population)
        for module in ('BF', 'SQL', 'UP'):
            group = by_group['pairs:' + module]
            assert group['observation_count'] == result['module_pairs'][module]['n']
            assert len(group['used_planned_ids']) == 2 * group['observation_count']
            assert all(lookup[pid]['block_id'] in result['B_C'] for pid in group['common_core_planned_ids'])
            assert all(lookup[pid]['block_id'] not in result['B_C'] for pid in group['additional_pair_planned_ids'])
        if name == 'blockunterschiede':
            group = by_group['core-blocks']
            assert group['observation_count'] == result['n_C']
            assert len(group['used_planned_ids']) == 6 * result['n_C']
            assert all(len(item['planned_ids']) == 6 for item in group['observations'])


def test_resource_populations_are_metric_and_currency_specific(chart_source):
    result, charts, excluded_id = chart_source
    time_group = groups(figure_population(result, 'F-zeit', charts['F-zeit'][1]))['values:BF']
    tokens_group = groups(figure_population(result, 'F-tokens', charts['F-tokens'][1]))['values:BF']
    assert excluded_id in time_group['used_planned_ids']
    assert excluded_id not in tokens_group['used_planned_ids']
    for name, (_, info) in charts.items():
        if not name.startswith('F-kosten'):
            continue
        population = figure_population(result, name, info)
        row = next(row for row in population['rows'] if row['planned_id'] == excluded_id)
        assert (row['memberships'][0]['role'] == 'used') == (info['figure_data']['currency'] == 'EUR')
        if row['memberships'][0]['role'] == 'not_used':
            assert 'fehlend' not in row['memberships'][0]['status'].lower()


def test_exploration_retains_individual_values_outside_complete_quartets():
    snapshot = hand_snapshot(3, 2)
    for row in snapshot['rows']:
        set_f(row, '1/2')
        if row['plan']['cell_key'] == 'E-AB' and row['plan']['block_index'] == 1:
            set_f(row, None)
    result = compute(snapshot)
    charts = figures(result)
    try:
        info = next(info for name, _, info in charts if name == 'modellzuordnung')
        population = figure_population(result, 'modellzuordnung', info)
        by_group = groups(population)
        assert all(row['block_index'] <= 2 for row in population['rows'])
        assert by_group['quartets']['observation_count'] == 1
        assert len(by_group['quartets']['used_planned_ids']) == 4
        individual = {pid for key, group in by_group.items() if key.startswith('values:') for pid in group['used_planned_ids']}
        assert len(individual - set(by_group['quartets']['used_planned_ids'])) == 3
        assert len({row['planned_id'] for row in population['rows'] if row['cell_key'] == 'C-SQL-1'}) == 2
    finally:
        for _, fig, _ in charts:
            plt.close(fig)


@pytest.mark.parametrize('complete', [0, 1, 2])
def test_sensitivity_uses_explicit_retained_blocks_and_separate_missing_bounds(complete):
    snapshot = hand_snapshot(2, 2)
    for row in snapshot['rows']:
        if row['plan']['block_index'] <= complete:
            set_f(row, '1/2')
    result = compute(snapshot)
    charts = figures(result)
    try:
        info = next(info for name, _, info in charts if name == 'sensitivitaet')
        population = figure_population(result, 'sensitivitaet', info)
        by_group = groups(population)
        assert len(population['rows']) == 12
        assert all(row['cell_key'].startswith('C-') for row in population['rows'])
        assert by_group['primary-reference']['used_run_count'] == 6 * complete
        bound = by_group['missing-bounds']
        assert bound['observation_count'] == 6
        assert len(bound['used_planned_ids']) == 6 * complete
        assert len(bound['bounded_missing_planned_ids']) == 6 * (2 - complete)
        omissions = [group for key, group in by_group.items() if key.startswith('leave-one-out:')]
        assert len(omissions) == (2 if complete == 2 else 0)
        for group in omissions:
            assert len(group['used_planned_ids']) == 6
            assert len(group['omitted_planned_ids']) == 6
            assert set(group['used_planned_ids']).isdisjoint(group['omitted_planned_ids'])
    finally:
        for _, fig, _ in charts:
            plt.close(fig)


def test_export_embeds_population_without_changing_plot_points(chart_source, monkeypatch):
    import research_env.analysis_charts as chart_module
    from research_env.analysis_export import render
    from research_env.analysis_presentation import data_dictionary, PRESENTATION_FILES
    result, charts, _ = chart_source
    name = 'kontext-verteilungen'
    fig, info = charts[name]
    monkeypatch.setattr(chart_module, 'figures', lambda _: [(name, fig, info)])
    before = deepcopy(info['figure_data'])
    files = render(result)
    exported = json.loads(files[name + '-data.json'][0])
    assert exported['figure_data'] == before
    assert exported['population'] == figure_population(result, name, info)
    source_name = exported['population']['csv_filename']
    rows = list(csv.DictReader(StringIO(files[source_name][0].decode())))
    expected = population_rows(exported['population'])
    assert {(r['planned_id'], r['group_key'], r['role']) for r in rows} == {
        (r['planned_id'], r['group_key'], r['role']) for r in expected}
    manifest = json.loads(files['manifest.json'][0])
    assert manifest['files'][name + '.png']['population'] == exported['population']
    assert manifest['files'][source_name]['row_count'] == len(expected)
    assert 'Teilgruppe' in data_dictionary(result, files)['tables'][source_name]['description']
    assert {'analysis_population.py', 'analysis_table_population.py'} <= set(PRESENTATION_FILES)


def test_unknown_source_ids_are_rejected(chart_source):
    result, charts, _ = chart_source
    info = deepcopy(charts['kontext-verteilungen'][1])
    info['denominator_sets']['BF']['observed_ids'].append('unbekannte-plan-id')
    with pytest.raises(ValueError, match='unbekannte Plan-IDs'):
        figure_population(result, 'kontext-verteilungen', info)

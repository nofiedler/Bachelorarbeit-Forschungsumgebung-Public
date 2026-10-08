"""Scientific report keeps distinct populations and missing observations visible."""
from copy import deepcopy
from pathlib import Path

from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader
import pytest

from research_env.analysis import compute, value
from research_env.analysis_report_data import report_context
from research_env.analysis_ui import metric_label, report_number, unit_label
from test_analysis import hand_snapshot, set_f


def render(result):
    context = report_context(result, output_base='/read-only-report/')
    env = Environment(loader=FileSystemLoader(Path(__file__).parents[1] / 'src/research_env/templates'), autoescape=True)
    env.filters.update(report_number=report_number, metric_label=metric_label, unit_label=unit_label)
    return context, BeautifulSoup(env.get_template('analysis_report.html').render(result=result, **context), 'html.parser')


def snapshot(complete_blocks=3):
    data = hand_snapshot(3, 2)
    for row in data['rows']:
        block = row['plan']['block_index']
        if block > complete_blocks:
            row.update(run_id=None, state=None, candidate_hash=None, missing='not_started')
            continue
        set_f(row, '1/2' if block < 3 else '1')
        row['static_report'] = {'analysis_complete': True, 'D': 0, 'L': 20}
        row['resources'] = {'pipeline_seconds': value('1.5', unit='s'), 'call_count': 0,
            'transport_count': 0, 'repair_count': 0, 'tokens': {'summary': {
                'total': {'status': 'complete', 'value': '0', 'unit': 'tokens'}}},
            'costs': {'status': 'known', 'amount': '0', 'currency': 'USD'}}
    return data


@pytest.mark.parametrize('complete', [0, 1, 2])
def test_incomplete_main_matrix_n_and_undefined_spread_are_not_invented(complete):
    result = compute(snapshot(complete))
    saved = deepcopy(result)
    context, page = render(result)
    assert result == saved
    assert context['report']['run_count'] == 30
    assert context['report']['f_count'] == (12 * complete if complete <= 2 else 30)
    assert result['n_C'] == complete
    if complete < 2:
        assert result['core']['sample_sd']['value'] is None
        assert not result['core']['leave_one_out']['values']
    assert len(page.select('#runs tr[data-planned-id]')) == 30
    assert 'Nicht berechenbar' in page.get_text(' ', strip=True)
    assert page.select_one('#context') and page.select_one('#resources') and page.select_one('#exploration')
    # Every data row remains aligned with its declared columns, even for gaps.
    for table in page.select('table'):
        head = table.select_one('thead tr')
        if head is None:
            continue
        width = sum(int(cell.get('colspan', 1)) for cell in head.find_all(['th', 'td'], recursive=False))
        for row in table.select('tbody tr'):
            assert sum(int(cell.get('colspan', 1)) for cell in row.find_all(['th', 'td'], recursive=False)) == width


def test_exploratory_reference_population_is_not_the_full_core_configuration():
    result = compute(snapshot())
    context, _ = render(result)
    whole = next(c for c in context['report']['configs'] if c['key'] == 'C-SQL-1')
    assert whole['profiles']['F']['mean']['value'] == '2/3'
    for question in ('UF3', 'UF4'):
        reference = next(c for c in context['report']['exploratory_profiles'][question] if c['key'] == 'C-SQL-1')
        assert reference['planned'] == 2
        assert reference['metrics']['F']['n'] == 2
        assert reference['metrics']['F']['mean']['value'] == '1/2'
    all_reference_ids = set(whole['profiles']['F']['ids'])
    assert set(reference['metrics']['F']['ids']) < all_reference_ids


def test_measured_zero_missing_time_and_zero_L_have_distinct_report_status():
    data = snapshot()
    target = next(row for row in data['rows'] if row['plan']['cell_key'] == 'C-BF-0' and row['plan']['block_index'] == 1)
    target['resources']['pipeline_seconds'] = value(unit='s', status='technical_missing', reason='Unknown active interval')
    target['static_report'] = {'analysis_complete': True, 'D': 0, 'L': 0}
    result = compute(data)
    original = deepcopy(result)
    context, page = render(result)
    configuration = next(c for c in context['report']['configs'] if c['key'] == 'C-BF-0')
    assert configuration['profiles']['tokens.total']['n'] == 3
    assert configuration['profiles']['tokens.total']['mean']['value'] == '0'
    assert configuration['profiles']['pipeline_seconds']['n'] == 2
    assert configuration['profiles']['D']['n'] == 3
    assert configuration['profiles']['S']['n'] == 2
    assert 'Unknown active interval' in page.select_one('#coverage').get_text(' ', strip=True)
    assert 'technisch fehlende' in page.select_one('#coverage').get_text(' ', strip=True)
    assert result == original

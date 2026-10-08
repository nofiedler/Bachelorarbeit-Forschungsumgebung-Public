"""Displayed units, missing values and downloadable archived figures use real results."""
from copy import deepcopy
import csv
import hashlib
from io import StringIO
import json
from pathlib import Path
import zipfile

from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader
import pytest

from research_env.analysis import compute
from research_env.analysis_report_data import report_context
from research_env.analysis_ui import metric, report_number, metric_label, unit_label, plot_help
from test_analysis import hand_snapshot, set_f
from test_exchange import exported
from test_preparation import client


def report(result):
    templates = Path(__file__).parents[1] / 'src/research_env/templates'
    env = Environment(loader=FileSystemLoader(templates), autoescape=True)
    env.filters.update(metric=metric, report_number=report_number, metric_label=metric_label,
                       unit_label=unit_label, plot_help=plot_help, jsonpretty=lambda x: json.dumps(x))
    return BeautifulSoup(env.get_template('analysis_report.html').render(
        result=result, imported_package=False, **report_context(result)), 'html.parser')


@pytest.mark.parametrize('complete', [0, 1, 2])
def test_explanation_keeps_zero_mean_and_spread_distinct(complete):
    snapshot = hand_snapshot(3, 1)
    for row in snapshot['rows']:
        plan = row['plan']; block = plan['block_index']; key = plan['cell_key']
        if block > complete:
            row.update(run_id=None, state=None)
            continue
        set_f(row, '1/3' if key.endswith('-0') else '2/3' if block == 1 else '1/2')
        d = 1 if block == 1 else 3
        if key.startswith('C-') and key.endswith('-1'):
            d += ({'BF':4, 'SQL':2, 'UP':-1} if block == 1 else {'BF':0, 'SQL':-3, 'UP':-2})[key.split('-')[1]]
        row['static_report'] = {'D': d, 'L': 100, 'analysis_complete': True}
    result = compute(snapshot)
    original = deepcopy(result)
    page = report(result)
    row = page.select_one('[data-metric="D"]')
    # n, all descriptive summaries and the actual block numbers are explicit.
    cells = [c.get_text(' ', strip=True) for c in row.select('td')]
    assert 'PHPStan-Diagnosen D' in row.select_one('th').get_text(' ', strip=True)
    assert 'Diagnosen' in row.select_one('th').get_text(' ', strip=True)
    stats = result['metric_comparisons']['D']['complete_block_differences']
    numbers = row.select('.report-number')
    if complete == 2:
        assert cells == ['2', '0', '0', '-1,67', '1,67', '2,36', '1, 2']
        assert numbers[0]['title'] == 'Exakt: 0 diagnoses_difference'
        assert numbers[2]['title'] == 'Exakt: -5/3 diagnoses_difference'
        assert numbers[3]['title'] == 'Exakt: 5/3 diagnoses_difference'
        assert numbers[4]['title'] == 'Exakt: ' + stats['sample_sd']['value'] + ' diagnoses_difference'
        assert stats['sample_sd']['value'].startswith('2.357022603955158414')
        context = page.select_one('#context')
        assert '11,79' in [span.get_text() for span in page.select('#repetition .report-number')]
        assert '33,33' in [span.get_text() for span in context.select('.report-number')]
        assert any('Prozentpunkten' in p.get_text() for p in page.select('#reading-guide dd'))
    elif complete == 1:
        assert cells == ['1', '1,67', '1,67', '1,67', '1,67', 'Nicht berechenbar', '1']
        assert numbers[0]['title'] == 'Exakt: 5/3 diagnoses_difference'
        assert 'title' not in numbers[-1].attrs
    else:
        assert cells == ['0', 'Nicht berechenbar', 'Nicht berechenbar', 'Nicht berechenbar',
                         'Nicht berechenbar', 'Nicht berechenbar', 'Keine']
        assert not any('title' in number.attrs for number in numbers)
    static_text = page.select_one('#static-resources').get_text(' ', strip=True)
    assert 'keine absoluten Messwerte' in static_text
    assert 'nicht eine Diagnoseanzahl von null' in static_text
    assert 'Nicht berechenbar s' not in page.get_text()
    assert 'tokens.input' not in page.select_one('#resources').get_text()
    assert len(page.select('#runs tr[data-planned-id]')) == 24
    assert page.select_one('#context.report-primary')
    assert [section['data-question'] for section in page.select('[data-question]')] == ['UF2','UF1','UF5','UF6','UF3','UF4']
    assert [a['href'] for a in page.select('.report-question-nav a')] == [
        '#context','#module-comparison','#resources','#repetition','#uf3','#uf4']
    assert all(section.select_one('.report-question-reading') and section.select_one('.report-question-wording') for section in page.select('[data-question]'))
    assert result == original, 'Rendering must not change immutable analysis results'


def test_import_and_active_figure_downloads_preserve_all_bytes(exported):
    env, analysis, path, manifest = exported
    original_archive_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    c = client(env[0]); c.get('/packages')
    response = c.post('/packages/import', data={'csrf': c.cookies['research_csrf']},
        files={'archive': ('synthetic.zip', path.read_bytes(), 'application/zip')},
        headers={'origin': 'http://testserver'}, follow_redirects=False)
    assert response.status_code == 303
    with zipfile.ZipFile(path) as archive:
        original_result = archive.read('analysis/analysis.json')
        assert archive.read('presentation/analysis.json') == original_result
        for url in [response.headers['location'], '/analyses/' + str(analysis.id)]:
            page = c.get(url)
            assert page.status_code == 200
            soup = BeautifulSoup(page.text, 'html.parser')
            assert soup.select_one('#reading-guide') and soup.select_one('#coverage')
            cards = soup.select('.scientific-report .plot-card')
            assert len(cards) == 13
            assert all(card.select_one('.report-figure-title p') and 'So liest du es:' in card.get_text() and 'Beachte:' in card.get_text() for card in cards)
            assert {card['data-figure'] for card in cards} >= {
                'kontext-verteilungen', 'sensitivitaet', 'blockunterschiede', 'F-tokens',
                'modellzuordnung', 'pipeline-varianten'}
            card = next(card for card in cards if card['data-figure'] == 'statisch-S')
            # Definitions belong to the measurement section; plot captions explain marks and n.
            assert 'Bei L = 0' in soup.select_one('#static-resources').get_text()
            assert 'Fehlende Werte sind keine Nullwerte' in card.get_text()
            for ext, signature in [('png', b'\x89PNG\r\n\x1a\n'), ('svg', b'<?xml'), ('pdf', b'%PDF-')]:
                link = card.select_one('a[download="statisch-S.' + ext + '"]')
                assert '/presentation/' in link['href']
                download = c.get(link['href'])
                assert download.status_code == 200
                assert download.headers['content-disposition'] == 'attachment; filename="statisch-S.' + ext + '"'
                assert download.content.startswith(signature)
                assert download.content == archive.read('presentation/statisch-S.' + ext)
            for ext in ('csv', 'json'):
                link = card.select_one('a[download="statisch-S-data.' + ext + '"]')
                assert link is not None
                download = c.get(link['href'])
                assert download.status_code == 200
                assert download.content == archive.read('presentation/statisch-S-data.' + ext)
            preview = c.get(card.select_one('a[target="_blank"]')['href'])
            assert preview.headers['content-type'] == 'image/png'
            assert preview.content == archive.read('presentation/statisch-S.png')
            result_link = soup.select_one('#downloads a[download="analysis.json"]')
            assert c.get(result_link['href']).content == original_result
            # The report must expose complete tabular data, including exact metric summaries.
            table_link = soup.select_one('#downloads a[download="metric-contrasts.csv"]')
            download = c.get(table_link['href'])
            assert download.content == archive.read('presentation/metric-contrasts.csv')
            rows = list(csv.DictReader(StringIO(download.content.decode())))
            d = next(row for row in rows if row['metric'] == 'D')
            assert d['complete_block_differences.mean.value'] == ''
            assert d['complete_block_differences.sample_sd.value'] == ''
            assert d['complete_block_differences.n'] == '0'
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original_archive_sha

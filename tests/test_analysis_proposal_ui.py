"""The approval page exposes the saved resource values without changing them."""
from copy import deepcopy
from uuid import uuid4

from bs4 import BeautifulSoup
import pytest
import m7_fixture
from test_preparation import client
from research_env import review_ui


@pytest.fixture
def proposal_page(tmp_path, monkeypatch):
    env = m7_fixture.make_environment(tmp_path / 'instance')
    proposal_id = str(uuid4())
    row = {'plan': {'position': 1, 'cell_key': 'C-BF-1'}, 'run_id': str(uuid4()),
           'missing': None, 'resources': {}, 'selection': {'measurements': {
               'functional_R': {'missing': None, 'revision_id': str(uuid4())}},
               'reviews': {'T1': {'missing': None, 'revision_id': str(uuid4())}}}}
    view = {'snapshot': {'data_origin': 'own', 'rows': [row], 'r_c': 1, 'r_e': 0,
                       'selected_revisions': {}, 'phase_id': str(uuid4()),
                       'freeze_id': str(uuid4()), 'selection_rule': 'fixed'},
            'proposal': {'proposal_id': proposal_id, 'input_hash': 'unchanged'},
            'open_decisions': [], 'changes': [], 'confirmed': None}
    monkeypatch.setattr(review_ui, 'proposal_view', lambda *args: view)
    monkeypatch.setattr(review_ui, 'selection_history', lambda *args: [])
    c = client(env[0])
    def render(resources, *, started=True, **fields):
        row.update(fields)
        row['resources'] = resources
        if not started:
            row['run_id'] = None
        before = deepcopy(view)
        response = c.get('/analyses/proposals/' + proposal_id)
        assert response.status_code == 200
        assert view == before, 'Rendering must not change the saved approval input'
        return BeautifulSoup(response.text, 'html.parser')
    yield render
    env[0].close()


def test_saved_pipeline_tokens_costs_and_details_are_visible(proposal_page):
    page = proposal_page({
        'pipeline_seconds': {'status': 'observed', 'value': '671.353462761', 'unit': 's'},
        'tokens': {'summary': {'total': {'status': 'complete', 'value': '928671'},
                              'input': {'status': 'complete', 'value': '875156'},
                              'reasoning': {'status': 'complete', 'value': '44072'}}},
        'costs': {'status': 'known', 'amount': '0.1368419752', 'currency': 'USD'},
        'call_count': 6, 'transport_count': 7, 'repair_count': 1,
        'metric_observations': [{'metric': 'cpu_seconds', 'value': {
            'status': 'observed', 'value': '12.5', 'unit': 's'}}],
    })
    summary = page.select_one('.proposal-run > summary').get_text(' ', strip=True)
    assert '671,35 s' in summary and '928.671' in summary and '0,136842 USD' in summary
    assert '2 / 2 ausgewählte Prüf- und Bewertungsnachweise vorhanden' in page.get_text(' ', strip=True)
    assert 'Bewertungen T1–T5' in summary
    details = page.select_one('.proposal-run-body').get_text(' ', strip=True)
    assert '875.156' in details and '44.072' in details and '12,5 s' in details
    assert 'Modellaufrufe' in details and 'Transportversuche' in details
    assert page.select_one('[title="Originalwert: 671.353462761 s"]')
    assert page.select_one('input[name=input_hash]')['value'] == 'unchanged'


def test_partial_totals_stay_partial_and_zero_stays_observed(proposal_page):
    page = proposal_page({
        'pipeline_seconds': {'status': 'observed', 'value': '0', 'unit': 's'},
        'tokens': {'summary': {'total': {'status': 'partial', 'value': None,
                                       'known_subtotal': '100'}}},
        'costs': {'status': 'partial', 'amount': None, 'known_subtotal': '0.01', 'currency': 'USD'},
        'repair_count': 0,
    })
    summary = page.select_one('.proposal-run > summary').get_text(' ', strip=True)
    assert '0 s' in summary
    assert '100' in summary and '0,01 USD' in summary
    assert summary.count('Teilwert') == 2
    assert 'Nicht erhoben' in page.select_one('.proposal-run-body').get_text()


def test_unstarted_run_does_not_invent_resource_zeros(proposal_page):
    page = proposal_page({}, started=False)
    summary = page.select_one('.proposal-run > summary').get_text(' ', strip=True)
    assert 'Nicht gestartet' in summary
    assert '0 s' not in summary and '0 USD' not in summary
    assert page.select_one('.proposal-summary-top').get_text(' ', strip=True).count('Nicht erhoben') == 3


def test_measured_segments_do_not_become_a_complete_pipeline_time(proposal_page):
    page = proposal_page({
        'pipeline_seconds': {'status': 'technical_missing', 'value': None, 'unit': 's'},
        'timing': {'known_partial_seconds': {'active': '120.57489889'}, 'partition_verified': False},
    })
    summary = page.select_one('.proposal-run > summary').get_text(' ', strip=True)
    assert 'Technisch unvollständig' in summary and '120,57' not in summary
    details = page.select_one('.proposal-run-body').get_text(' ', strip=True)
    assert '120,57 s Teilwert' in details
    assert 'überlappungsfrei' in details
    assert '0 / 1' in page.select_one('.proposal-coverage').get_text(' ', strip=True)


def test_summary_shows_results_not_just_presence_and_missing_times_are_folded(proposal_page):
    page = proposal_page({}, cases=[{'id':'case-1','category':'R1','assertions':['a']}],
        test_results=[{'test_id':'case-1','assertion_id':'a','r_category':'R1','status':'failed','cause':'mismatch'}],
        reviews={'T1':{'verdict':{'status':'observed','value':'1'},'reason':'boot verified'},
                 'T2':{'verdict':{'status':'observed','value':'0'},'reason':'controller missing'}},
        static_report={'analysis_complete':True,'D':3,'L':120})
    summary = page.select_one('.proposal-run > summary').get_text(' ',strip=True)
    assert '0 / 1 bestanden' in summary and 'PHPStan' in summary
    assert all(value in summary for value in ('D 3','L 120','S 2,5','✓ T1','× T2','? T3'))
    assert 'controller missing' in page.select_one('.proposal-review-table').get_text()
    collapsed = next(d for d in page.select('.proposal-subdetails') if 'Nicht separat erhobene' in d.summary.text)
    assert not collapsed.has_attr('open') and 'Inferenzzeit' in collapsed.get_text()
    assert 'Pipeline: Zeit und Verbrauch je Schritt' in page.get_text()

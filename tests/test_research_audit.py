"""Independent counterexamples to research-plan coverage, not model outcomes."""
import csv
from io import StringIO
import json

from test_analysis import hand_snapshot, set_f
from test_register import register
from research_env.analysis import compute
from research_env.analysis_export import render
from research_env.analysis_store import Analyses
from research_env.adapter import CallJournal
from research_env.domain import AssetVersion, digest


def test_real_holdout_catalog_resolves_from_installed_project(register):
    from pathlib import Path
    import research_env.analysis_store as module
    r, _ = register
    lock = json.loads(Path(module.__file__).with_name('evaluation_assets.lock.json').read_text())
    asset = r.add(AssetVersion(code='AUDIT-HOLDOUT', asset_type='suite', suite_kind='study_holdout',
        manifest_hash=digest(lock['study_holdout/m2-v0.1']), origin='Installed exact case catalog', access_scope='trusted_evaluator'))
    service = Analyses(r, CallJournal.cost_view(r))
    for name, count in [('BF', 16), ('SQL', 10), ('UP', 9)]:
        cases = service._cases(asset.id, name, synthetic=False)
        assert len(cases) == count
        assert {x['category'] for x in cases} == {'R1', 'R2', 'R3', 'R4', 'R5', 'R6'}


def counterexample():
    snapshot = hand_snapshot(3, 1)
    for row in snapshot['rows']:
        p = row['plan']
        set_f(row, 1 if p['cell_key'].endswith('1') else 0)
        # Later AA values must not enter the exploratory baseline.
        if p['cell_key'] == 'C-SQL-1' and p['block_index'] > 1:
            set_f(row, 0)
        row['static_report'] = {'analysis_complete': True, 'D': 2 if p['cell_key'] == 'E-AB' else 0, 'L': 10}
        row['resources'] = {'call_count': 5, 'transport_count': 5,
            'repair_count': 1 if p['cell_key'].endswith('1') else 0,
            'tokens': {'summary': {'input': {'status': 'complete', 'value': '120' if p['cell_key'].endswith('1') else '100'}}}}
    return snapshot


def test_exploration_figures_use_only_prefrozen_reference_blocks():
    output = compute(counterexample())
    manifest = json.loads(render(output)['manifest.json'][0])
    for name in ('modellzuordnung.png', 'pipeline-varianten.png'):
        ids = manifest['files'][name]['denominator_ids']
        assert len(ids) == 4
        assert all(row['in_b_e'] for row in output['cells'] if row['id'] in ids)


def test_secondary_contrasts_and_resource_profiles_use_own_denominators():
    snapshot = counterexample()
    # A failed S denominator removes one quartet for S only, not D or F.
    next(row for row in snapshot['rows'] if row['plan']['cell_key'] == 'E-BB')['static_report']['L'] = 0
    output = compute(snapshot)
    assert output['exploratory_metrics']['UF3']['D']['contrasts']['AB-AA']['mean']['value'] == '2'
    assert output['exploratory_metrics']['UF3']['S']['n'] == 0
    assert output['UF3']['n'] == 1
    assert output['metric_comparisons']['tokens.input']['module_pairs']['BF']['mean']['value'] == '20'
    assert output['metric_comparisons']['repair_count']['module_pairs']['BF']['mean']['value'] == '1'
    assert output['configuration_endpoints']['C-BF-1']['full_success']['mean']['value'] == '1'
    assert output['module_contrasts']['BF-SQL']['mean']['value'] == '2/3'


def test_flat_export_has_one_row_per_planned_run_and_explicit_metrics():
    output = compute(counterexample())
    files = render(output)
    rows = list(csv.DictReader(StringIO(files['runs.csv'][0].decode())))
    assert len(rows) == 24 and len({row['planned_id'] for row in rows}) == 24
    assert all(row['module'] and row['context'] and row['configuration_id'] for row in rows)
    assert all(row['F'] in ('0', '1') for row in rows)
    assert all('tokens.input.status' in row and 'repair_count' in row for row in rows)
    summaries = list(csv.DictReader(StringIO(files['summaries.csv'][0].decode())))
    assert any(row['comparison'] == 'UF3:AB-AA' and row['metric'] == 'D' and row['mean.value'] == '2' for row in summaries)
    assert 'sensitivity.csv' in files


def test_repair_counts_require_dispatch_and_do_not_count_retry_twice():
    from research_env.analysis_resources import resource_profile
    raw = {'costs': {'evidence': []}, 'intervals': [], 'coverage_complete': False,
        'resource_records': [], 'metric_observations': [],
        'model_calls': [{'id': 'repair', 'node': 'repair'}],
        'transports': [{'id': 'first', 'call_id': 'repair'}, {'id': 'retry', 'call_id': 'repair'}],
        'dispatches': []}
    assert resource_profile(raw)['repair_count'] == 0
    raw['dispatches'] = [{'transport_id': 'first'}, {'transport_id': 'retry'}]
    assert resource_profile(raw)['repair_count'] == 1
    assert resource_profile(raw)['prepared_repair_count'] == 1
    del raw['dispatches']
    assert resource_profile(raw)['repair_count'] is None

"""Reporting scope and denominator invariants; synthetic hand-computed data."""
from copy import deepcopy
from fractions import Fraction

from research_env.analysis import compute, value
from research_env.analysis_report_tables import tables
from test_analysis import hand_snapshot, set_f


def fixture():
    snapshot = hand_snapshot(3, 2)
    for row in snapshot['rows']:
        set_f(row, '1' if row['plan']['block_index'] < 3 else '0')
        row['static_report'] = {'analysis_complete': True, 'D': 0, 'L': 10}
        row['resources'] = {'pipeline_seconds': value(10, unit='s'), 'repair_count': 0,
            'call_count': 5, 'transport_count': 5,
            'tokens': {'summary': {'total': {'status': 'complete', 'value': '20', 'unit': 'tokens'}}},
            'costs': {'status': 'known', 'amount': '0.125', 'currency': 'USD'}}
    return snapshot


def test_shared_reference_exploratory_profile_uses_only_prespecified_blocks():
    computed = compute(fixture())
    assert computed['configurations']['C-SQL-1']['profiles']['F']['mean']['value'] == '2/3'
    result = tables(computed)
    refs = [row for row in result['exploratory-profiles']
            if row['cell_key'] == 'C-SQL-1' and row['metric'] == 'F']
    assert len(refs) == 2
    assert {row['question'] for row in refs} == {'UF3', 'UF4'}
    assert all(row['mean_exact'] == '1' and row['planned_n'] == row['observed_n'] == 2 for row in refs)
    members = [row for row in result['denominator-members'] if row['comparison'] == 'UF3:cell:C-SQL-1' and row['metric'] == 'F']
    assert {row['block_index'] for row in members} == {1, 2}


def test_missing_timing_and_static_L_zero_keep_distinct_denominators_and_causes():
    snapshot = fixture()
    target = next(row for row in snapshot['rows'] if row['plan']['cell_key'] == 'C-BF-0' and row['plan']['block_index'] == 1)
    target['resources']['pipeline_seconds'] = value(unit='s', status='technical_missing', reason='Unknown active interval')
    target['static_report'] = {'analysis_complete': True, 'D': 0, 'L': 0}
    result = tables(compute(snapshot))
    coverage = [row for row in result['coverage'] if row['scope'] == 'configuration' and row['group'] == 'C-BF-0' and row['metric'] == 'pipeline_seconds']
    assert sum(row['status_n'] for row in coverage) == 3
    assert all(row['planned_n'] == 3 and row['observed_n'] == 2 and row['missing_n'] == 1 for row in coverage)
    assert any(row['cause'] == 'Unknown active interval' and row['status'] == 'technical_missing' for row in coverage)
    static = next(row for row in result['static-coverage'] if row['scope'] == 'configuration' and row['group'] == 'C-BF-0')
    assert static['completed_analysis_n'] == static['sealed_candidate_n'] == 3
    assert static['valid_analysis_ratio_exact'] == '1'
    assert static['D_observed_n'] == static['L_observed_n'] == 3
    assert static['S_observed_n'] == 2


def test_complete_quartet_contrasts_and_observed_cell_profiles_have_own_denominators():
    snapshot = fixture()
    target = next(row for row in snapshot['rows'] if row['plan']['cell_key'] == 'E-AB' and row['plan']['block_index'] == 2)
    set_f(target, None)
    computed = compute(snapshot)
    result = tables(computed)
    contrasts = [row for row in result['contrast-values'] if row['comparison'] == 'UF3:AB-AA' and row['metric'] == 'F']
    assert len(contrasts) == 1 and contrasts[0]['observed_n'] == 1 and contrasts[0]['planned_n'] == 2
    assert contrasts[0]['block_index'] == 1
    reference = next(row for row in result['exploratory-profiles'] if row['comparison'] == 'UF3:cell:C-SQL-1' and row['metric'] == 'F')
    assert reference['observed_n'] == 2


def test_flat_exact_exports_leave_original_result_unchanged():
    computed = compute(fixture())
    before = deepcopy(computed)
    result = tables(computed)
    assert computed == before
    for records in result.values():
        for row in records:
            assert all(not isinstance(value, (dict, list, tuple)) for value in row.values())
    for row in result['contrast-values']:
        if row['exact'] is not None:
            assert abs(Fraction(row['value']) - Fraction(row['exact'])) < Fraction(1, 10**38)
    loo = [row for row in result['report-sensitivity'] if row['kind'] == 'leave_one_out']
    assert {row['omitted_block_index'] for row in loo} == {1, 2, 3}
    assert all(row['denominator'] == 2 for row in loo)
    bounds = [row for row in result['report-sensitivity'] if row['kind'] == 'missing_bound']
    assert all(row['denominator'] == 9 for row in bounds)


def test_configuration_matrix_binds_real_model_ids_and_optional_roles():
    snapshot = fixture()
    for row in snapshot['rows']:
        key = row['plan']['cell_key']
        row['configuration_id'] = 'configuration-' + key
        row['configuration_hash'] = 'hash-' + key
        row['models'] = {'A': {'id': 'package-a', 'exact_model_id': 'vendor/model-a', 'endpoint': 'ea', 'upstream': 'pa'},
                         'B': {'id': 'package-b', 'exact_model_id': 'vendor/model-b', 'endpoint': 'eb', 'upstream': 'pb'}}
    result = tables(compute(snapshot))
    rows = {row['cell_key']: row for row in result['configurations']}
    assert len(rows) == 12
    assert sum(row['planned_n'] for row in rows.values()) == 30
    assert rows['E-AB']['producer_exact_model_id'] == 'vendor/model-a'
    assert rows['E-AB']['verifier_exact_model_id'] == 'vendor/model-b'
    assert rows['E-BA']['producer_model_package_id'] == 'package-b'
    assert rows['E-P0R0']['producer_roles'] == 'analyzer; migrate; repair_if_required'
    assert rows['E-P0R0']['verifier_roles'] == 'test'
    assert rows['C-SQL-1']['planned_n'] == 3 and rows['C-SQL-1']['planned_in_B_E_n'] == 2
    assert rows['C-SQL-1']['shared_reference'] is True
    assert all(row['binding_status'] == 'complete' for row in rows.values())
    assert all(row['r_C'] == 3 and row['r_E'] == 2 for row in rows.values())


def test_configuration_export_does_not_choose_among_conflicting_model_bindings():
    snapshot = fixture()
    selected = [row for row in snapshot['rows'] if row['plan']['cell_key'] == 'C-BF-0']
    selected[0]['models'] = {'A': {'exact_model_id': 'vendor/model-a'}}
    selected[1]['models'] = {'A': {'exact_model_id': 'vendor/model-b'}}
    result = tables(compute(snapshot))
    row = next(row for row in result['configurations'] if row['cell_key'] == 'C-BF-0')
    assert row['producer_exact_model_id'] is None
    assert row['producer_exact_model_id_distinct_n'] == 2
    assert row['producer_exact_model_id_missing_n'] == 1
    assert row['binding_status'] == 'conflicting_recorded_bindings'

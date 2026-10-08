"""Relational CSV tables alongside the lossless nested JSON result."""
from decimal import Decimal, localcontext
from fractions import Fraction

from .analysis import R_KEYS, T_KEYS
from .analysis_metrics import COUNTS, TOKENS
from .domain import MAIN_CELLS
from .numeric import context


def number(value):
    if value is None:
        return None
    fraction = Fraction(value)
    with localcontext(context()) as ctx:
        ctx.prec = 40
        return str(Decimal(fraction.numerator) / Decimal(fraction.denominator))


def observation(target, key, item):
    item = item or {}
    target[key] = number(item.get('value'))
    target[key + '.exact'] = item.get('value')
    target[key + '.status'] = item.get('status', 'not_collected')
    target[key + '.unit'] = item.get('unit')
    target[key + '.reason'] = item.get('reason')


def tables(result):
    output = {key: [] for key in ('runs', 'calls', 'tokens', 'intervals', 'cases', 'assertions',
                                  'reviews', 'static-files', 'summaries', 'sensitivity')}
    for row in result['cells']:
        cell = MAIN_CELLS[row['cell_key']]
        identity = {'planned_id': row['id'], 'run_id': row['run_id'], 'cell_key': row['cell_key'],
            'configuration_id': row['configuration_id'], 'configuration_hash': row['configuration_hash'],
            'block_id': row['block_id'], 'block_index': row['block_index'], 'position': row['position'],
            'in_b_e': row['in_b_e'], 'candidate_hash': row['candidate_hash'],
            'data_origin': result['data_origin'], 'phase_id': result['phase_id'], **cell.model_dump()}
        resources, functional = row['resources'], row['functional']
        record = {**identity, 'state': row['state'], 'missing': row['missing'],
                  'models': row.get('models', {}), 'settings': row.get('settings', {}),
                  'selection': row['selection']}
        for key in ('F', 'T', 'full_success', 'raw_pass_ratio'):
            observation(record, key, functional[key])
        for key, item in {**functional['R'], **functional['T_criteria']}.items():
            observation(record, key, item)
        for key in ('D', 'L', 'S'):
            observation(record, key, row['static'][key])
        for key in ('pipeline_seconds', 'setup_seconds', 'evaluation_seconds', 'manual_review_seconds',
                    'context_preparation_seconds', 'pause_seconds', 'outage_seconds', 'total_seconds'):
            observation(record, key, resources.get(key))
        for key in COUNTS:
            record[key] = resources.get(key)
        for key in TOKENS:
            item = resources.get('tokens', {}).get('summary', {}).get(key, {})
            observation(record, 'tokens.' + key, item)
            record['tokens.' + key + '.known_subtotal'] = item.get('known_subtotal')
        cost = resources.get('costs') or {}
        record.update(cost=cost.get('amount'), cost_known_subtotal=cost.get('known_subtotal'),
                      cost_status=cost.get('status', 'not_collected'), currency=cost.get('currency'))
        output['runs'].append(record)
        for call in resources.get('model_calls', []):
            output['calls'].append({**identity, **call,
                'accounting': resources.get('by_role_model', {}).get(call['id'])})
        for interval in resources.get('intervals', []):
            output['intervals'].append({**identity, **interval})
        for attempt in resources.get('tokens', {}).get('attempts', []):
            for category, item in attempt['tokens'].items():
                output['tokens'].append({**identity, 'call_id': attempt['call_id'],
                    'transport_id': attempt['transport_id'], 'category': category,
                    'evidence_ids': attempt['evidence_ids'], **item})
        for case in functional['case_rows']:
            output['cases'].append({**identity, **case})
        for assertion in functional['test_results']:
            output['assertions'].append({**identity, **assertion})
        for key, review in functional['review_revisions'].items():
            output['reviews'].append({**identity, 'criterion': key, **review})
        report = row['static']['raw_report']
        for item in report.get('file_scope', []):
            output['static-files'].append({**identity, 'file': item})

    def summary(comparison, metric, stats, **metadata):
        output['summaries'].append({'comparison': comparison, 'metric': metric, **metadata, **stats})
    summary('core', 'F', result['core'], planned_n=result['r_C'])
    for module, stats in result['module_on_B_C'].items():
        summary('UF1:common:' + module, 'F', stats)
    for module, stats in result['module_pairs'].items():
        summary('UF1:all_pairs:' + module, 'F', stats)
    for modules, stats in result['module_contrasts'].items():
        summary('UF1:' + modules, 'F', stats)
    for question in ('UF3', 'UF4'):
        for name, stats in result[question]['contrasts'].items():
            summary(question + ':' + name, 'F', stats, planned_n=result['r_E'])
        for metric, item in result['exploratory_metrics'][question].items():
            for name, stats in item['contrasts'].items():
                summary(question + ':' + name, metric, stats, planned_n=result['r_E'])
    for metric, item in result['metric_comparisons'].items():
        summary('core', metric, item['complete_block_differences'])
        for module, stats in item['module_pairs'].items():
            summary('context:' + module, metric, stats)
    for cell, item in result['configurations'].items():
        for metric, stats in {**item['profiles'], **result['configuration_endpoints'][cell]}.items():
            summary('configuration:' + cell, metric, stats, planned_n=item['planned_n'])
    for module, profiles in result['criterion_pairs'].items():
        for metric, stats in profiles.items():
            summary('criterion_pairs:' + module, metric, stats)
    for bound in ('lower', 'upper'):
        output['sensitivity'].append({'kind': 'missing_bound', 'name': bound,
            'planned_denominator': result['missing_bounds']['denominator'], **result['missing_bounds'][bound]})
    leave = result['core']['leave_one_out']
    for block_id, item in zip(leave['omitted_block_ids'], leave['values']):
        output['sensitivity'].append({'kind': 'leave_one_out', 'omitted_block_id': block_id, **item})
    return output

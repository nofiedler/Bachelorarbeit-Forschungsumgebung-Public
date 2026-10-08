"""Additional endpoints specified in Operationalisierung §§6–8.

Each metric uses its own observed pairs/quartets. Token subcategories overlap
and are never added to input/output totals. Missing usage is never zero.
"""
from fractions import Fraction
from itertools import combinations

from .analysis import MODULES, R_KEYS, T_KEYS, describe, observed, value

COUNTS = ('call_count', 'transport_count', 'repair_count')
TOKENS = ('input', 'output', 'total', 'cache_read', 'cache_write', 'reasoning', 'other')
EXPLORATIONS = {
    'UF3': (('C-SQL-1', 'E-AB', 'E-BA', 'E-BB'),
            {'AB-AA': (-1, 1, 0, 0), 'BA-BB': (0, 0, 1, -1),
             'BA-AA': (-1, 0, 1, 0), 'BB-AB': (0, -1, 0, 1)}),
    'UF4': (('E-P0R0', 'E-P0R1', 'E-P1R0', 'C-SQL-1'),
            {'Planner11-01': (0, -1, 0, 1), 'Planner10-00': (-1, 0, 1, 0),
             'Review11-10': (0, 0, -1, 1), 'Review01-00': (-1, 1, 0, 0),
             'Interaction': (1, -1, -1, 1)}),
}


def resource_value(row, metric):
    resources = row['resources']
    if metric in COUNTS:
        number = resources.get(metric)
        return Fraction(number) if type(number) is int and number >= 0 else None
    token = resources.get('tokens', {}).get('summary', {}).get(metric.removeprefix('tokens.'), {})
    return Fraction(token['value']) if token.get('status') == 'complete' and token.get('value') is not None else None


def resource_getters():
    return {key: (lambda row, metric=key: resource_value(row, metric),
                  'count' if key in COUNTS else 'tokens')
            for key in (*COUNTS, *('tokens.' + token for token in TOKENS))}


def supplement(cells, common_blocks):
    blocks = {}
    for row in cells:
        blocks.setdefault(row['block_id'], {})[row['cell_key']] = row
    output = {'module_contrasts': {}, 'exploratory_metrics': {}, 'configuration_endpoints': {}}
    for first, second in combinations(MODULES, 2):
        differences = []
        for bid in common_blocks:
            block = blocks[bid]
            deltas = [observed(block[f'C-{module}-1']['functional']['F']) -
                      observed(block[f'C-{module}-0']['functional']['F']) for module in (first, second)]
            differences.append(deltas[0] - deltas[1])
        output['module_contrasts'][first + '-' + second] = describe(
            differences, unit='ratio_difference', ids=common_blocks)
    getters = {metric: (lambda row, key=metric: observed(row['static'][key]), unit)
               for metric, unit in (('D', 'diagnoses'), ('L', 'lines'), ('S', 'diagnoses_per_100_lines'))}
    for question, (keys, formulas) in EXPLORATIONS.items():
        metrics = {}
        for metric, (getter, unit) in getters.items():
            ids, values = [], {name: [] for name in formulas}
            for bid, block in blocks.items():
                if not next(iter(block.values()))['in_b_e']:
                    continue
                xs = [getter(block[key]) for key in keys]
                if any(x is None for x in xs):
                    continue
                ids.append(bid)
                for name, weights in formulas.items():
                    values[name].append(sum((weight * x for weight, x in zip(weights, xs)), Fraction(0)))
            metrics[metric] = {'unit': unit, 'complete_block_ids': ids, 'n': len(ids),
                'contrasts': {name: describe(xs, unit=unit + '_difference', ids=ids) for name, xs in values.items()}}
        output['exploratory_metrics'][question] = metrics
    for key in dict.fromkeys(row['cell_key'] for row in cells):
        selected = [row for row in cells if row['cell_key'] == key]
        endpoints = {}
        for metric in ('T', 'full_success', 'raw_pass_ratio', *R_KEYS, *T_KEYS):
            pairs = []
            for row in selected:
                group = row['functional']
                observation = group['R'][metric] if metric in R_KEYS else group['T_criteria'][metric] if metric in T_KEYS else group[metric]
                number = observed(observation)
                if number is not None:
                    pairs.append((row['id'], number))
            endpoints[metric] = describe([x for _, x in pairs], unit='ratio' if metric == 'raw_pass_ratio' else 'binary', ids=[pid for pid, _ in pairs])
        for metric, (getter, unit) in resource_getters().items():
            pairs = [(row['id'], getter(row)) for row in selected if getter(row) is not None]
            endpoints[metric] = describe([x for _, x in pairs], unit=unit, ids=[pid for pid, _ in pairs])
        output['configuration_endpoints'][key] = endpoints
    return output

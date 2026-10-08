"""M7 deterministic arithmetic. No register, model, clock or network access.

Rational F values remain exact (sixths); observations and interpretations are
separate. Decimal calculations use a fixed local precision, never ambient state.
"""
from decimal import Decimal, localcontext
from fractions import Fraction
from uuid import UUID

from .domain import MAIN_CELLS, digest
from .matrix import matrix
from .numeric import context

VERSION = 'M7-analysis-v5'
INPUT_SCHEMA = 'M7-input-v1'
R_KEYS = tuple(f'R{i}' for i in range(1, 7))
T_KEYS = tuple(f'T{i}' for i in range(1, 6))
MODULES = ('BF', 'SQL', 'UP')


def value(number=None, *, unit='ratio', status='observed', reason=None, source='M7 exact derivation'):
    if number is None:
        return {'status': status if status != 'observed' else 'not_collected', 'value': None,
                'unit': unit, 'reason': reason or 'No valid observation'}
    status = 'estimated' if status == 'estimated' else 'observed'
    if isinstance(number, Fraction):
        return {'status': status, 'value': str(number), 'numerator': number.numerator,
                'denominator': number.denominator, 'unit': unit, 'source': source}
    return {'status': status, 'value': str(number), 'unit': unit, 'source': source}


def observed(item):
    return Fraction(item['value']) if item and item['status'] == 'observed' and item['value'] is not None else None


def describe(items, *, unit, ids):
    """Only valid observed values; own explicit denominator for every metric."""
    xs = [Fraction(x) for x in items]
    n = len(xs)
    if len(ids) != n or len(set(ids)) != n:
        raise ValueError('Descriptive denominator needs exact unique observation IDs')
    ordered = sorted(xs)
    mean = sum(xs, Fraction(0)) / n if n else None
    median = (ordered[n // 2] if n % 2 else (ordered[n // 2-1] + ordered[n // 2]) / 2) if n else None
    sd = None
    if n >= 2:
        variance = sum(((x - mean) ** 2 for x in xs), Fraction(0)) / (n-1)
        with localcontext(context()) as ctx:
            ctx.prec = 80
            sd = (Decimal(variance.numerator) / Decimal(variance.denominator)).sqrt()
            ctx.prec = 40
            sd = +sd
    return {'n': n, 'ids': list(ids), 'unit': unit, 'values': [value(x, unit=unit) for x in xs],
            'mean': value(mean, unit=unit, reason='n=0'), 'median': value(median, unit=unit, reason='n=0'),
            'min': value(min(xs) if n else None, unit=unit, reason='n=0'),
            'max': value(max(xs) if n else None, unit=unit, reason='n=0'),
            'sample_sd': value(sd, unit=unit, reason='n<2')}


def describe_decimal(items, *, unit, ids):
    """Costs stay Decimal; precision/rounding fixed and independent of context."""
    xs = [Decimal(x) for x in items]
    if len(xs)!=len(ids) or len(set(ids))!=len(ids) or any(not x.is_finite() for x in xs):
        raise ValueError('Finite cost values and exact unique denominator required')
    n=len(xs);ordered=sorted(xs)
    mean=median=sd=None
    with localcontext(context()) as ctx:
        ctx.prec=max(80,sum(len(x.as_tuple().digits)+abs(x.as_tuple().exponent) for x in xs)+10)
        if n:
            mean=sum(xs,Decimal(0))/Decimal(n)
            median=ordered[n//2] if n%2 else (ordered[n//2-1]+ordered[n//2])/2
        if n>=2:sd=(sum(((x-mean)**2 for x in xs),Decimal(0))/(n-1)).sqrt()
        ctx.prec=40
        mean=+mean if mean is not None else None
        sd=+sd if sd is not None else None
    return {'n':n,'ids':list(ids),'unit':unit,'values':[value(x,unit=unit) for x in xs],
        'mean':value(mean,unit=unit,reason='n=0'),'median':value(median,unit=unit,reason='n=0'),
        'min':value(min(xs) if n else None,unit=unit,reason='n=0'),'max':value(max(xs) if n else None,unit=unit,reason='n=0'),
        'sample_sd':value(sd,unit=unit,reason='n<2')}


def sign(x):
    return (x > 0) - (x < 0)


def contrast(items, ids):
    result = describe(items, unit='ratio_difference', ids=ids)
    n = len(items)
    omitted = [sum((Fraction(x) for j, x in enumerate(items) if j != i), Fraction(0)) / (n-1)
               for i in range(n)] if n >= 2 else []
    mean = observed(result['mean'])
    result['leave_one_out'] = {'omitted_block_ids': list(ids) if omitted else [],
        'values': [value(x, unit='ratio_difference') for x in omitted],
        'min': value(min(omitted) if omitted else None, unit='ratio_difference', reason='n<2'),
        'max': value(max(omitted) if omitted else None, unit='ratio_difference', reason='n<2'),
        'sign_change': (any(sign(x) != sign(mean) for x in omitted) if omitted else None),
        'strict_positive_negative': (min(omitted) < 0 < max(omitted) if omitted else None)}
    result['percentage_points'] = value(mean * 100 if mean is not None else None, unit='percentage_points', reason='n=0')
    return result


def functional(cases, results, reviews, *, missing='not_collected'):
    expected = {}
    for case in cases:
        if case['category'] not in R_KEYS or case['id'] in expected or not case['assertions']:
            raise ValueError('Frozen case IDs/categories/assertions invalid')
        if len(case['assertions']) != len(set(case['assertions'])):
            raise ValueError('Duplicate frozen assertion')
        expected[case['id']] = case
    by_case = {case_id: {} for case_id in expected}
    for result in results:
        case = expected.get(result['test_id'])
        if not case or result['assertion_id'] not in case['assertions'] or result['r_category'] != case['category']:
            raise ValueError('Result outside exact frozen case/assertion/category set')
        target = by_case[result['test_id']]
        if result['assertion_id'] in target or result['status'] not in ('passed', 'failed', 'blocked_candidate', 'technical_missing'):
            raise ValueError('Duplicate/invalid assertion result')
        target[result['assertion_id']] = result
    case_rows = []
    for case_id, case in expected.items():
        assertions = by_case[case_id]
        statuses = [assertions[aid]['status'] if aid in assertions else 'not_collected' for aid in case['assertions']]
        status = ('failed' if 'failed' in statuses else 'blocked_candidate' if 'blocked_candidate' in statuses
                  else 'passed' if all(s == 'passed' for s in statuses) else 'technical_missing')
        case_rows.append({'case_id': case_id, 'category': case['category'], 'status': status,
            'assertion_count': len(statuses), 'missing_assertions': sum(s in ('technical_missing', 'not_collected') for s in statuses),
            'missing_causes': [assertions[aid]['cause'] if aid in assertions else missing for aid in case['assertions']
                               if aid not in assertions or assertions[aid]['status'] == 'technical_missing']})
    categories = {}
    for key in R_KEYS:
        members = [row for row in case_rows if row['category'] == key]
        z = (0 if any(c['status'] in ('failed', 'blocked_candidate') for c in members)
             else 1 if members and all(c['status'] == 'passed' for c in members) else None)
        categories[key] = value(z, unit='binary', status='technical_missing' if results else missing, reason='Incomplete R cases/assertions')
    t_values = {key: (reviews[key]['verdict'] if reviews.get(key) else value(unit='binary', status='pending', reason='Open criterion: '+key)) for key in T_KEYS}
    t_numbers = [observed(x) for x in t_values.values()]
    t = 0 if Fraction(0) in t_numbers else 1 if all(x == 1 for x in t_numbers) else None
    z_values = [observed(categories[k]) for k in R_KEYS]
    f = Fraction(0) if t == 0 else sum(z_values, Fraction(0))/6 if t == 1 and all(x is not None for x in z_values) else None
    passed = sum(c['status'] == 'passed' for c in case_rows)
    gaps = [c for c in case_rows if c['missing_assertions']]
    return {'R': categories, 'T_criteria': t_values, 'T': value(t, unit='binary', status='pending', reason='Open T criteria'),
        'F': value(f, status='pending' if t is None else 'technical_missing', reason='T or R incomplete'),
        'F_sixths': int(f*6) if f is not None else None, 'full_success': value(int(f == 1) if f is not None else None, unit='binary', status='pending', reason='F missing'),
        'raw_pass_ratio': value(Fraction(passed, len(cases)) if cases and results else None, status=missing, reason='No valid case measurements'),
        'passed_cases': passed, 'frozen_case_count': len(cases), 'case_ids': list(expected),
        'missing_case_count': len(gaps), 'missing_assertion_count': sum(c['missing_assertions'] for c in case_rows),
        'case_rows': case_rows, 'test_results': results, 'review_revisions': reviews}


def static_profile(report, *, missing_status='technical_missing'):
    absent = report is None
    report = report or {}
    status = missing_status if absent else 'technical_missing'
    complete = report.get('analysis_complete') is True
    d, l = report.get('D'), report.get('L')
    for x in (d, l):
        if x is not None and (type(x) is not int or x < 0):
            raise ValueError('D/L need nonnegative integer observations')
    return {'analysis_complete': complete, 'D': value(d if complete else None, unit='diagnoses', status=status, reason=report.get('cause')),
        'L': value(l if complete else None, unit='lines', status=status, reason=report.get('cause')),
        'S': value(Fraction(100*d, l) if complete and d is not None and l else None, unit='diagnoses_per_100_lines', status=status, reason='Analysis incomplete or L=0/missing'),
        'file_scope': report.get('file_scope', []), 'excluded_files': report.get('excluded_files', {}), 'raw_report': report}


def metric_comparisons(cells):
    from .analysis_metrics import resource_getters
    output = {}
    getters = {key: (lambda r, k=key: observed(r['static'][k]), unit) for key,unit in
        (('D','diagnoses'),('L','lines'),('S','diagnoses_per_100_lines'))}
    for key in ('pipeline_seconds','setup_seconds','evaluation_seconds','context_preparation_seconds'):
        getters[key] = (lambda r, k=key: observed(r['resources'].get(k)), 's')
    getters.update(resource_getters())
    currencies = sorted({r['resources']['costs']['currency'] for r in cells if r['resources'].get('costs') and r['resources']['costs']['currency']})
    for currency in currencies:
        getters['cost:'+currency] = (lambda r, c=currency: Decimal(r['resources']['costs']['amount']) if r['resources'].get('costs') and r['resources']['costs']['status']=='known' and r['resources']['costs']['currency']==c else None, currency)
    blocks = {}
    for r in cells:
        blocks.setdefault(r['block_id'], {})[r['cell_key']] = r
    for metric,(getter,unit) in getters.items():
        pairs = {m: [] for m in MODULES};shared=[]
        summarizer = describe_decimal if metric.startswith('cost:') else describe
        for bid,block in blocks.items():
            differences=[]
            for module in MODULES:
                a,b = [getter(block[f'C-{module}-{k}']) for k in (0,1)]
                with localcontext(context()) as ctx:
                    ctx.prec=max(80,sum(len(x.as_tuple().digits)+abs(x.as_tuple().exponent) for x in (a,b) if isinstance(x,Decimal))+10)
                    difference=b-a if a is not None and b is not None else None
                if difference is not None:pairs[module].append((bid,difference))
                differences.append(difference)
            if all(x is not None for x in differences):
                with localcontext(context()) as ctx:
                    ctx.prec=max(80,sum(len(x.as_tuple().digits)+abs(x.as_tuple().exponent) for x in differences if isinstance(x,Decimal))+10)
                    shared.append((bid,sum(differences,Decimal(0) if metric.startswith('cost:') else Fraction(0))/3))
        output[metric] = {'unit': unit, 'valid_planned_ids': [r['id'] for r in cells if getter(r) is not None],
            'complete_six_cell_block_ids': [bid for bid,_ in shared],
            'complete_block_differences': summarizer([x for _,x in shared], unit=unit+'_difference', ids=[bid for bid,_ in shared]),
            'module_pairs': {m: summarizer([x for _,x in xs], unit=unit+'_difference', ids=[bid for bid,_ in xs]) for m,xs in pairs.items()}}
    return output


def criterion_pairs(cells):
    output = {}
    by_block = {}
    for row in cells:by_block.setdefault(row['block_id'], {})[row['cell_key']] = row
    for module in MODULES:
        profiles = {}
        for key in (*R_KEYS,*T_KEYS,'T'):
            xs,ids = [],[]
            for bid,block in by_block.items():
                rows = [block[f'C-{module}-{k}']['functional'] for k in (0,1)]
                values = [observed(row['R'][key] if key in R_KEYS else row['T_criteria'][key] if key in T_KEYS else row[key]) for row in rows]
                if all(x is not None for x in values):xs.append(values[1]-values[0]);ids.append(bid)
            profiles[key] = describe(xs, unit='binary_difference', ids=ids)
        output[module] = profiles
    return output


def compute(snapshot):
    if snapshot['schema'] != INPUT_SCHEMA or snapshot['analysis_version'] != VERSION:
        raise ValueError('Incompatible analysis input/version')
    planned = matrix(UUID(snapshot['freeze_id']), snapshot['r_c'], snapshot['r_e'], snapshot['seed'])
    rows = snapshot['rows']
    if [row['plan'] for row in rows] != list(planned):
        raise ValueError('Analysis requires exactly the complete immutable frozen main matrix')
    if len({row['run_id'] for row in rows if row['run_id']}) != sum(bool(row['run_id']) for row in rows):
        raise ValueError('Duplicate independent run ID')
    cells = []
    for row in rows:
        if row['run_id'] and row['purpose'] != 'main':
            raise ValueError('Only planned main observations are FQ data')
        profile = functional(row['cases'], row['test_results'], row['reviews'], missing=row['missing_status'])
        static = static_profile(row['static_report'], missing_status=row['missing_status'])
        cells.append({**row['plan'], 'run_id': row['run_id'], 'candidate_hash': row['candidate_hash'],
            'configuration_id': row['configuration_id'], 'configuration_hash': row['configuration_hash'],
            'models': row.get('models', {}), 'settings': row.get('settings', {}),
            'selection': row['selection'], 'state': row['state'], 'functional': profile, 'static': static,
            'resources': row['resources'], 'missing': row['missing']})
    blocks = {}
    for row in cells:
        blocks.setdefault(row['block_id'], {})[row['cell_key']] = row
    bc, block_rows = [], []
    pairs = {m: [] for m in MODULES}
    common = {m: [] for m in MODULES}
    g_values = []
    lower, upper = Fraction(0), Fraction(0)
    for bid, block in blocks.items():
        ds = {}
        for module in MODULES:
            zero, one = [observed(block[f'C-{module}-{k}']['functional']['F']) for k in (0, 1)]
            diff = one-zero if zero is not None and one is not None else None
            ds[module] = diff
            if diff is not None:
                pairs[module].append((bid, diff))
            lower += (one if one is not None else 0) - (zero if zero is not None else 1)
            upper += (one if one is not None else 1) - (zero if zero is not None else 0)
        g = sum(ds.values(), Fraction(0))/3 if all(d is not None for d in ds.values()) else None
        if g is not None:
            bc.append(bid);g_values.append(g)
            for module in MODULES:
                common[module].append(ds[module])
        block_rows.append({'block_id': bid, 'index': next(iter(block.values()))['block_index'],
            'planned_ids': [x['id'] for x in block.values()], 'in_B_C': g is not None,
            'module_differences': {m: value(d, unit='ratio_difference', reason='Incomplete module pair') for m, d in ds.items()},
            'g': value(g, unit='ratio_difference', reason='Incomplete six-cell block')})
    extra = {}
    definitions = {'UF3': (('C-SQL-1', 'E-AB', 'E-BA', 'E-BB'), {'AB-AA': (1,0), 'BA-BB': (2,3), 'BA-AA': (2,0), 'BB-AB': (3,1)}),
        'UF4': (('E-P0R0', 'E-P0R1', 'E-P1R0', 'C-SQL-1'), {'Planner11-01': (3,1), 'Planner10-00': (2,0), 'Review11-10': (3,2), 'Review01-00': (1,0)})}
    for question, (keys, formulas) in definitions.items():
        complete_ids, quartets = [], []
        values = {name: [] for name in formulas}
        if question == 'UF4':
            values['Interaction'] = []
        for bid, block in blocks.items():
            if not next(iter(block.values()))['in_b_e']:
                continue
            xs = [observed(block[key]['functional']['F']) for key in keys]
            complete = all(x is not None for x in xs)
            q = {'block_id': bid, 'run_ids': {key: block[key]['run_id'] for key in keys}, 'planned_ids': {key: block[key]['id'] for key in keys},
                 'reference_id': block['C-SQL-1']['id'], 'complete': complete}
            if complete:
                complete_ids.append(bid)
                for name, (a,b) in formulas.items():
                    values[name].append(xs[a]-xs[b])
                if question == 'UF4':
                    values['Interaction'].append(xs[3]-xs[1]-xs[2]+xs[0])
            quartets.append(q)
        extra[question] = {'planned_B_E': [b for b, x in blocks.items() if next(iter(x.values()))['in_b_e']],
            'complete_block_ids': complete_ids, 'n': len(complete_ids), 'quartets': quartets,
            'contrasts': {key: contrast(xs, complete_ids) for key,xs in values.items()}}
    configurations = {}
    for cell in MAIN_CELLS:
        selected = [row for row in cells if row['cell_key'] == cell]
        profiles = {}
        for metric, unit in (('F','ratio'), ('D','diagnoses'), ('L','lines'), ('S','diagnoses_per_100_lines')):
            valid = [(r['id'], observed(r['functional' if metric == 'F' else 'static'][metric])) for r in selected]
            valid = [(pid, x) for pid,x in valid if x is not None]
            profiles[metric] = describe([x for _,x in valid], unit=unit, ids=[pid for pid,_ in valid])
        for metric in ('pipeline_seconds','setup_seconds','evaluation_seconds','manual_review_seconds','context_preparation_seconds'):
            valid = [(r['id'], observed(r['resources'].get(metric))) for r in selected]
            valid = [(pid, x) for pid,x in valid if x is not None]
            profiles[metric] = describe([x for _,x in valid], unit='s', ids=[pid for pid,_ in valid])
        for currency in sorted({r['resources']['costs']['currency'] for r in selected if r['resources'].get('costs') and r['resources']['costs']['currency']}):
            valid = [(r['id'], Decimal(r['resources']['costs']['amount'])) for r in selected if r['resources'].get('costs') and r['resources']['costs']['status']=='known' and r['resources']['costs']['currency']==currency]
            profiles['cost:'+currency] = describe_decimal([x for _,x in valid], unit=currency, ids=[pid for pid,_ in valid])
        hashes = [r['candidate_hash'] for r in selected if r['candidate_hash']]
        configurations[cell] = {'planned_n': len(selected), 'planned_ids': [r['id'] for r in selected],
            'started_n': sum(bool(r['run_id']) for r in selected), 'candidate_n': len(hashes),
            'candidate_hashes': hashes, 'distinct_candidate_hash_n': len(set(hashes)),
            'distinct_F_n': len({str(observed(r['functional']['F'])) for r in selected if observed(r['functional']['F']) is not None}),
            'profiles': profiles}
    candidates = [r for r in cells if r['candidate_hash']]
    valid_analysis = [r['id'] for r in candidates if r['static']['analysis_complete']]
    static_sets = {key: [r['id'] for r in cells if observed(r['static'][key]) is not None] for key in ('D','L','S')}
    errors = []
    for row in cells:
        for result in row['functional']['test_results']:
            if result['status'] != 'passed':
                errors.append({'planned_id': row['id'], 'run_id': row['run_id'], 'candidate_hash': row['candidate_hash'],
                    'category': result['r_category'], 'observation': result, 'evidence_ids': [result['input_artifact_id'], result['raw_artifact_id']], 'interpretation': None})
        for key, review in row['functional']['review_revisions'].items():
            if observed(review['verdict']) == 0:
                errors.append({'planned_id': row['id'], 'run_id': row['run_id'], 'candidate_hash': row['candidate_hash'], 'category': key,
                    'observation': review, 'evidence_ids': ([review['code_artifact_id']] if review.get('code_artifact_id') else []) + review.get('measurement_ids', []), 'interpretation': None})
    data_hash = digest(snapshot)
    from .analysis_metrics import supplement
    return {'analysis_version': VERSION, 'data_hash': data_hash, 'data_origin': snapshot['data_origin'],
        'test_warning': 'Synthetic technical recalculation; no study result or human research approval' if snapshot['data_origin']=='synthetic' else None,
        'phase_id': snapshot['phase_id'], 'freeze_id': snapshot['freeze_id'], 'r_C': snapshot['r_c'], 'r_E': snapshot['r_e'],
        'cells': cells, 'blocks': block_rows, 'B_C': bc, 'n_C': len(bc), 'core': contrast(g_values, bc),
        'module_means_B_C': {m: {f'K{k}': describe([observed(blocks[bid][f'C-{m}-{k}']['functional']['F']) for bid in bc], unit='ratio', ids=bc) for k in (0,1)} for m in MODULES},
        'metric_comparisons': metric_comparisons(cells), 'criterion_pairs': criterion_pairs(cells),
        'module_on_B_C': {m: contrast(xs,bc) for m,xs in common.items()},
        'module_pairs': {m: contrast([x for _,x in pairs[m]],[bid for bid,_ in pairs[m]]) for m in MODULES},
        'missing_bounds': {'lower': value(lower/(3*snapshot['r_c']), unit='ratio_difference'),
            'upper': value(upper/(3*snapshot['r_c']), unit='ratio_difference'), 'denominator': 3*snapshot['r_c'],
            'planned_core_ids': [r['id'] for r in cells if r['cell_key'].startswith('C-')], 'meaning': 'Conservative missing-value bounds'},
        **extra, 'configurations': configurations, 'static_validity': {'planned_ids': [r['id'] for r in cells],
            'candidate_ids': [r['id'] for r in candidates], 'candidate_hashes': [r['candidate_hash'] for r in candidates],
            'analysis_complete_ids': valid_analysis, 'valid_analysis_ratio': value(Fraction(len(valid_analysis),len(candidates)) if candidates else None),
            'denominator': len(candidates), 'definition': 'Valid compatible completed analysis / actually present sealed candidates; L>0 not required',
            'valid_sets': static_sets}, 'resource_areas': snapshot['resource_areas'], 'phase_resources': snapshot['phase_resources'],
        **supplement(cells, bc), 'errors': errors, 'interpretation': None}

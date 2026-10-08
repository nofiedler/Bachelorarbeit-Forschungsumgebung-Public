"""Resource derivation from current effective billing and explicit intervals."""
from decimal import Decimal
import json

from .analysis import value
from .domain import TimeInterval
from .timing import summarize
from .numeric import sum_exact

def exact_decimal_sum(values):
    return str(sum_exact(values))


TOKEN_KEYS = ('input','output','cache_read','cache_write','reasoning','total','other')


def token_fields(native, kind):
    if not isinstance(native, dict):
        return {}
    prompt = native.get('prompt_tokens_details') or {}
    completion = native.get('completion_tokens_details') or {}
    return {'input': native.get('prompt_tokens') if kind=='response' else native.get('native_tokens_prompt', native.get('tokens_prompt')),
        'output': native.get('completion_tokens') if kind=='response' else native.get('native_tokens_completion', native.get('tokens_completion')),
        'cache_read': prompt.get('cached_tokens') if kind=='response' else native.get('native_tokens_cached'),
        'cache_write': prompt.get('cache_write_tokens') if kind=='response' else native.get('native_tokens_cache_write'),
        'reasoning': completion.get('reasoning_tokens') if kind=='response' else native.get('native_tokens_reasoning'),
        'total': native.get('total_tokens'), 'other': native.get('other_tokens')}


def tokens(costs):
    attempts = []
    for call in costs['evidence']:
        for attempt in call['attempts']:
            evidence = attempt['billing']['native_usage_evidence']
            fields = {key: set() for key in TOKEN_KEYS}
            for source in evidence:
                native = json.loads(source['native']) if isinstance(source['native'], str) else source['native']
                for key, number in token_fields(native, source['kind']).items():
                    if type(number) is int and number >= 0:
                        fields[key].add(number)
            observations = {key: value(next(iter(xs)) if len(xs)==1 else None, unit='tokens', status='unresolved' if len(xs)>1 else 'not_collected', reason='Conflicting alternative evidence' if len(xs)>1 else 'Usage category not reported') for key,xs in fields.items()}
            attempts.append({'transport_id': attempt['transport_id'], 'call_id': call['call_id'], 'tokens': observations,
                'evidence_ids': attempt['billing']['evidence_ids']})
    summary = {}
    for key in TOKEN_KEYS:
        known = [int(x['tokens'][key]['value']) for x in attempts if x['tokens'][key]['status']=='observed']
        complete = bool(attempts) and len(known)==len(attempts)
        summary[key] = {'status': 'complete' if complete else 'partial' if known else 'missing',
            'value': str(sum(known)) if complete else None, 'known_subtotal': str(sum(known)) if known else None,
            'unit': 'tokens', 'denominator_transport_ids': [x['transport_id'] for x in attempts],
            'valid_transport_ids': [x['transport_id'] for x in attempts if x['tokens'][key]['status']=='observed']}
    return {'attempts': attempts, 'summary': summary}


def resource_profile(raw):
    costs = raw['costs']
    repairs = {c['id'] for c in raw['model_calls'] if c['node'] == 'repair'}
    # Preparing a request is not executing it. A durable dispatch event proves
    # dispatch began, including failed/uncertain delivery; retries count once.
    sent_ids = {e['transport_id'] for e in raw.get('dispatches', [])}
    sent_repairs = {t['call_id'] for t in raw.get('transports', [])
        if t['id'] in sent_ids and t['call_id'] in repairs}
    repair_count = len(sent_repairs) if 'dispatches' in raw else (None if repairs else 0)
    intervals = tuple(TimeInterval.model_validate(x) for x in raw['intervals'])
    times = summarize(intervals, coverage_complete=raw['coverage_complete'])
    output = {'costs': costs, 'tokens': tokens(costs), 'intervals': raw['intervals'],
        'timing': {k: x.model_dump(mode='json') if hasattr(x,'model_dump') else x for k,x in times.items()},
        'pipeline_seconds': times['active'].model_dump(mode='json'),
        'pause_seconds': times['pause'].model_dump(mode='json'), 'outage_seconds': times['outage'].model_dump(mode='json'),
        'total_seconds': times['total'].model_dump(mode='json'), 'resource_records': raw['resource_records'],
        'metric_observations': raw['metric_observations'], 'model_calls': raw['model_calls'],
        'call_count': len(raw['model_calls']), 'transport_count': sum(len(c['attempts']) for c in costs['evidence']),
        'repair_count': repair_count, 'prepared_repair_count': len(repairs),
        'repair_dispatch_call_ids': sorted(sent_repairs),
        'dispatch_evidence': raw.get('dispatches', [])}
    # No revision contract exists for arbitrary manual resource observations.
    # Preserve all; expose a value only for a single unambiguous evidence record.
    for key in ('setup_seconds','evaluation_seconds','manual_review_seconds','context_preparation_seconds','load_seconds','inference_seconds','retry_seconds','abort_seconds'):
        candidates = [r[key] for r in raw['resource_records']]
        candidates += [r['value'] for r in raw['metric_observations'] if r['metric']==key]
        output[key] = candidates[0] if len(candidates)==1 else value(unit='s', status='unresolved' if candidates else 'not_collected', reason='Multiple unversioned observations; no arbitrary latest choice' if candidates else 'Not collected')
    roles = {}
    for call in raw['model_calls']:
        current = next((c for c in costs['evidence'] if c['call_id']==call['id']), None)
        if current:
            roles[call['id']] = {'role': call['node'], 'model_package_id': call['model_package_id'],
                'cost': current['cost'], 'tokens': tokens({'evidence': [current]}),
                'transport_ids': [a['transport_id'] for a in current['attempts']]}
    output['by_role_model'] = roles
    output['estimated_costs'] = [x for x in raw['metric_observations'] if x['metric']=='cost_estimate' and x['value']['status']=='estimated']
    return output


def area_costs(rows):
    output = {}
    for purpose in ('main','pilot','preparation','demo','free_test'):
        members = [r for r in rows if r['purpose']==purpose]
        currencies = sorted({r['resources']['costs']['currency'] for r in members if r['resources']['costs']['currency']} |
            {x['value']['unit'] for r in members for x in r['resources'].get('estimated_costs', [])})
        groups = {}
        for currency in currencies:
            selected = [r for r in members if r['resources']['costs']['currency']==currency]
            known = [Decimal(r['resources']['costs']['known_subtotal']) for r in selected if r['resources']['costs']['known_subtotal'] is not None]
            complete = bool(selected) and len(selected)==len(members) and all(r['resources']['costs']['status']=='known' for r in selected)
            subtotal = exact_decimal_sum(known) if known else None
            estimates = [x for r in members for x in r['resources'].get('estimated_costs', []) if x['value']['unit']==currency]
            groups[currency] = {'status': 'complete' if complete else 'partial' if known else 'missing',
                'amount': subtotal if complete else None, 'known_subtotal': subtotal, 'unit': currency,
                'run_ids': [r['run_id'] for r in members], 'complete_run_ids': [r['run_id'] for r in selected if r['resources']['costs']['status']=='known'],
                'partial_run_ids': [r['run_id'] for r in selected if r['resources']['costs']['status']=='partial'],
                'missing_run_ids': [r['run_id'] for r in members if r['resources']['costs']['amount'] is None],
                'estimated_observations': estimates, 'estimated_subtotal': exact_decimal_sum([Decimal(x['value']['value']) for x in estimates]) if estimates else None, 'cost_bindings': [{'run_id': r['run_id'], 'evidence_revision': r['resources']['costs']['evidence_revision'], 'phase_revision': r['resources']['costs']['phase_revision']} for r in members]}
        area_tokens = {}
        for key in TOKEN_KEYS:
            categories = [(r['run_id'], r['resources']['tokens']['summary'][key]) for r in members]
            known = [int(x['known_subtotal']) for _,x in categories if x['known_subtotal'] is not None]
            complete = bool(members) and all(x['status']=='complete' for _,x in categories)
            area_tokens[key] = {'status': 'complete' if complete else 'partial' if known else 'missing',
                'value': str(sum(known)) if complete else None, 'known_subtotal': str(sum(known)) if known else None, 'unit': 'tokens',
                'run_ids': [pid for pid,_ in categories], 'valid_run_ids': [pid for pid,x in categories if x['status']=='complete']}
        output[purpose] = {'tokens': area_tokens,'run_ids': [r['run_id'] for r in members], 'n_started': len(members), 'currency_groups': groups,
            'unresolved_currency_run_ids': [r['run_id'] for r in members if not r['resources']['costs']['currency']],
            'research_cost_scope': purpose in ('main','pilot','preparation')}
    return output

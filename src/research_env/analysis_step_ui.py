"""Read-only display of historical step events and frozen resource evidence."""
from decimal import Decimal
from uuid import UUID

from .analysis_resources import tokens
from .domain import Artifact, Event, MAIN_CELLS
from .numeric import sum_exact
from .workflow_views import NODES, CHECKS, observed_span


def event_times(register, snapshot, snapshot_id):
    """Restrict supplementary timeline evidence to the saved proposal's cutoff."""
    if register is None or not snapshot_id:
        return {}
    cutoff = register.get(UUID(str(snapshot_id)), Artifact).created_at
    return {row['run_id']: [e for e in register.for_run(Event, UUID(row['run_id']))
                           if e.created_at <= cutoff]
            for row in snapshot['rows'] if row.get('run_id')}


def elapsed(events, key, *, check=False):
    start_type, end_type = ('check_started', 'check_finished') if check else ('node_started', 'node_finished')
    history = sorted((e for e in events if e.details.get('step' if check else 'node') == key
                      and e.event_type in (start_type, end_type)), key=lambda e: e.sequence)
    start, portions, incomplete, last_status = None, [], False, None
    for event in history:
        if event.event_type == start_type:
            incomplete |= start is not None
            start = event
        else:
            seconds = observed_span(start, event)
            if seconds is None:
                incomplete = True
            else:
                portions.append(Decimal(str(seconds)))
            start, last_status = None, event.details.get('status')
    incomplete |= start is not None
    return {'status': 'partial' if portions and incomplete else 'observed' if portions else 'not_collected',
            'value': str(sum_exact(portions)) if portions and not incomplete else None,
            'known_subtotal': str(sum_exact(portions)) if portions else None, 'unit': 's',
            'started': bool(history), 'result': last_status,
            'event_ids': [str(e.id) for e in history]}


def combined_cost(evidence):
    known = [e['cost'] for e in evidence if e['cost'].get('known_subtotal') is not None]
    currencies = {e['cost'].get('currency') for e in evidence}
    if len(currencies) != 1 or None in currencies:
        return {'status': 'unresolved' if evidence else 'not_collected'}
    complete = bool(evidence) and all(e['cost']['status'] == 'known' for e in evidence)
    subtotal = str(sum_exact(Decimal(e['known_subtotal']) for e in known)) if known else None
    return {'status': 'known' if complete else 'partial' if known else 'not_collected',
            'amount': subtotal if complete else None, 'known_subtotal': subtotal,
            'currency': next(iter(currencies))}


def steps(row, events):
    resources = row.get('resources') or {}
    all_calls = resources.get('model_calls', [])
    evidence = {item['call_id']: item for item in resources.get('costs', {}).get('evidence', [])}
    models = {m['id']: m['exact_model_id'] for m in row.get('models', {}).values()}
    cell = MAIN_CELLS.get(row['plan']['cell_key'])
    result = []
    for key, title, _ in NODES:
        calls = [c for c in all_calls if c['node'] == key]
        usage = [evidence.get(c['id'], {'call_id': c['id'], 'attempts': [],
                                       'cost': {'status': 'not_collected', 'known_subtotal': None}}) for c in calls]
        timing = elapsed(events, key)
        model_step = key not in ('runner', 'rerunner', 'seal')
        status = ('Abgeschlossen' if timing['result'] else 'Zeitnachweis unvollständig' if timing['started'] else
                  'In Konfiguration aus' if cell and key in ('planner', 'review') and not getattr(cell, key) else
                  'Nicht ausgeführt' if not calls and (row.get('state') or {}).get('execution') == 'terminal' else 'Nicht erhoben')
        if timing['result'] in ('failed', 'no_candidate'): status = 'Fehlgeschlagen'
        if timing['result'] in ('candidate_failure', 'changes_required'): status = 'Befund vorhanden'
        result.append({'key': key, 'title': title, 'status': status, 'timing': timing,
                       'model_step': model_step, 'models': list(dict.fromkeys(models.get(c['model_package_id'], c['model_package_id']) for c in calls)),
                       'calls': len(calls), 'transports': sum(len(e['attempts']) for e in usage),
                       'tokens': tokens({'evidence': usage})['summary'], 'cost': combined_cost(usage),
                       'call_ids': [c['id'] for c in calls]})
    checks = [{'title': title, 'timing': elapsed(events, key, check=True)} for key, title, _ in CHECKS]
    return result, checks

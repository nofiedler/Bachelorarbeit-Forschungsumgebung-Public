"""Presentation of an already saved proposal; no queries or new measurements."""
from decimal import Decimal, localcontext
from fractions import Fraction

from .analysis_ui import metric_label
from .numeric import context


TIME_LABELS = {
    'pipeline_seconds': 'Aktive Pipelinezeit', 'total_seconds': 'Gesamte erfasste Laufzeit',
    'pause_seconds': 'Pausen', 'outage_seconds': 'Ausfallzeit',
    'setup_seconds': 'Einrichtungszeit', 'evaluation_seconds': 'Unabhängige Prüfzeit',
    'manual_review_seconds': 'Manuelle Bewertungszeit',
    'context_preparation_seconds': 'Kontextvorbereitung', 'load_seconds': 'Ladezeit',
    'inference_seconds': 'Inferenzzeit', 'retry_seconds': 'Wiederholungszeit',
    'abort_seconds': 'Abbruchzeit',
}
TOKEN_LABELS = {
    'total': 'Tokens gesamt', 'input': 'Eingabe', 'output': 'Ausgabe',
    'reasoning': 'Reasoning', 'cache_read': 'Cache gelesen',
    'cache_write': 'Cache geschrieben', 'other': 'Weitere Tokens',
}
STATUS_LABELS = {
    'not_collected': 'Nicht erhoben', 'missing': 'Nicht erhoben',
    'pending': 'Noch offen', 'unresolved': 'Ungeklärt', 'unknown': 'Ungeklärt',
    'technical_missing': 'Technisch unvollständig', 'not_applicable': 'Nicht anwendbar',
}


def number(raw, places=2):
    fraction = Fraction(str(raw))
    with localcontext(context()):
        value = Decimal(fraction.numerator) / Decimal(fraction.denominator)
        rendered = f'{value:,.{places}f}'
        if value > 0 and Decimal(f'{value:.{places}f}') == 0:
            return '< ' + f'{Decimal(10) ** -places:.{places}f}'.replace('.', ',')
    if places:
        rendered = rendered.rstrip('0').rstrip('.')
    return rendered.translate(str.maketrans({',': '.', '.': ','}))


def observation(item=None, *, unit=None, places=2, cost=False):
    item = item or {}
    status = item.get('status', 'not_collected')
    raw = item.get('amount' if cost else 'value')
    unit = (item.get('currency') if cost else item.get('unit', unit)) or unit or ''
    partial = status == 'partial'
    if partial:
        raw = item.get('known_subtotal')
    present = raw is not None and status in ('observed', 'complete', 'known', 'partial', 'estimated')
    if present:
        try:
            display = number(raw, places)
        except (ValueError, TypeError, ZeroDivisionError):
            display = str(raw)
    return {'text': (display + (' ' + unit if unit else '')) if present else STATUS_LABELS.get(status, 'Nicht verfügbar'),
            'exact': (str(raw) + (' ' + unit if unit else '')) if present else '',
            'note': 'Teilwert' if partial else 'Geschätzt' if status == 'estimated' else '',
            'complete': present and status in ('observed', 'complete', 'known'),
            'tone': 'ok' if present and status in ('observed', 'complete', 'known') else 'warning',
            'reason': item.get('reason') or ''}


def proposal_resources(snapshot, *, register=None, snapshot_id=None):
    from .analysis_step_ui import event_times, steps
    events = event_times(register, snapshot, snapshot_id)
    rows = []
    for row in snapshot['rows']:
        resources = row.get('resources') or {}
        tokens = resources.get('tokens', {}).get('summary', {})
        times = {key: observation(resources.get(key), unit='s') for key in TIME_LABELS}
        token_values = {key: observation(tokens.get(key), places=0) for key in TOKEN_LABELS}
        costs = observation(resources.get('costs'), places=6, cost=True)
        timing = resources.get('timing', {})
        partial_times = []
        for key, total_key, label in (('active', 'pipeline_seconds', 'Gemessene aktive Segmente'),
                                     ('pause', 'pause_seconds', 'Gemessene Pausensegmente'),
                                     ('outage', 'outage_seconds', 'Gemessene Ausfallsegmente')):
            subtotal = timing.get('known_partial_seconds', {}).get(key)
            if subtotal is not None and not times[total_key]['complete']:
                partial_times.append({'label': label, 'value': observation({
                    'status': 'partial', 'known_subtotal': subtotal, 'unit': 's'})})
        groups = []
        for key, label in (('measurements', 'Automatische Prüfungen'), ('reviews', 'Bewertungen T1–T5')):
            entries = [{'label': {'T4_integration': 'Integrationstest', 'functional_R': 'Funktionstests',
                                  'static_DLS': 'PHPStan', 'candidate_absence': 'Kandidat-Abwesenheitsnachweis'}.get(k, k),
                        'present': bool(v.get('revision_id')) and not v.get('missing')}
                       for k, v in row['selection'][key].items()]
            groups.append({'label': label, 'entries': entries})
        entries = [item for group in groups for item in group['entries']]
        extra = [{'label': metric_label(item['metric']), 'value': observation(item.get('value'))}
                 for item in resources.get('metric_observations', [])]
        counts = [{'label': label, 'value': observation({'status': 'observed', 'value': resources.get(key)}, places=0)}
                  for key, label in (('call_count', 'Modellaufrufe'), ('transport_count', 'Transportversuche'),
                                     ('repair_count', 'Ausgeführte Repair-Schritte'),
                                     ('prepared_repair_count', 'Vorbereitete Repair-Schritte'))]
        pipeline_steps, checks = steps(row, events.get(row.get('run_id'), []))
        for step in pipeline_steps:
            step['duration'] = observation(step['timing'])
            step['costs'] = observation(step['cost'], places=6, cost=True)
            step['token_values'] = {k: observation(v, places=0) for k, v in step['tokens'].items()}
        for check in checks:
            check['duration'] = observation(check['timing'])
        shown_times = [{'label': label, 'value': times[key]} for key, label in TIME_LABELS.items()
                       if key in ('pipeline_seconds', 'total_seconds', 'pause_seconds', 'outage_seconds') or times[key]['exact']]
        unavailable_times = [label for key, label in TIME_LABELS.items()
                             if key not in ('pipeline_seconds', 'total_seconds', 'pause_seconds', 'outage_seconds') and not times[key]['exact']]
        rows.append({**result_details(row), 'configuration': configuration(row),
                     'pipeline_steps': pipeline_steps, 'checks': checks, 'unavailable_times': unavailable_times,
                     'position': row['plan']['position'], 'cell': row['plan']['cell_key'],
                     'planned_id': row['plan'].get('id'),
                     'run_id': row.get('run_id'), 'missing': row.get('missing'),
                     'pipeline': times['pipeline_seconds'], 'tokens': token_values['total'], 'costs': costs,
                     'times': shown_times,
                     'partial_times': partial_times, 'time_partition_verified': timing.get('partition_verified', False),
                     'token_categories': [{'label': label, 'value': token_values[key]} for key, label in TOKEN_LABELS.items()],
                     'counts': counts, 'extra': extra, 'groups': groups,
                     'present': sum(item['present'] for item in entries), 'expected': len(entries)})
    return {'rows': rows, 'coverage': [
        {'label': label, 'count': sum(row[key]['complete'] for row in rows)}
        for key, label in (('pipeline', 'Pipelinezeit'), ('tokens', 'Tokens gesamt'), ('costs', 'API-Kosten'))]}


CRITERIA = {'T1': 'Ausführbarkeit', 'T2': 'Controller und Routen', 'T3': 'Blade-Views',
            'T4': 'Laravel-Schnittstellen', 'T5': 'Grundlagen unverändert'}


def verdict(item):
    display = observation(item)
    if display['complete'] and item.get('value') in ('1', 1):
        display.update(text='Erfüllt', tone='ok', symbol='✓')
    elif display['complete'] and item.get('value') in ('0', 0):
        display.update(text='Nicht erfüllt', tone='failed', symbol='×')
    else:
        display['symbol'] = '?'
    return display


def result_details(row):
    from .analysis import functional, static_profile
    profile = functional(row.get('cases', []), row.get('test_results', []), row.get('reviews', {}),
                         missing=row.get('missing_status', 'not_collected'))
    static = static_profile(row.get('static_report'), missing_status=row.get('missing_status', 'not_collected'))
    categories = [{'label': k, 'value': verdict(v)} for k, v in profile['R'].items()]
    criteria = [{'label': k, 'title': title, 'value': verdict(profile['T_criteria'][k]),
                 'reason': row.get('reviews', {}).get(k, {}).get('reason'),
                 'person': row.get('reviews', {}).get(k, {}).get('person'),
                 'revision': row.get('reviews', {}).get(k, {}).get('revision'),
                 'reviewed_at': row.get('reviews', {}).get(k, {}).get('reviewed_at')}
                for k, title in CRITERIA.items()]
    measured = bool(row.get('test_results'))
    passed = profile['passed_cases']
    total = profile['frozen_case_count']
    functional_text = f'{passed} / {total} bestanden' if measured else 'Nicht erhoben'
    cases = [{**c, 'label': {'passed': 'Bestanden', 'failed': 'Nicht bestanden',
              'blocked_candidate': 'Durch Code blockiert', 'technical_missing': 'Technisch unvollständig'}[c['status']]}
             for c in profile['case_rows']]
    report = row.get('static_report') or {}
    files = [{'path': path, 'lines': report.get('L_by_file', {}).get(path),
              'diagnoses': report.get('D_by_file', {}).get(path)} for path in static['file_scope']]
    return {'functional': {'text': functional_text, 'missing_cases': profile['missing_case_count'],
                          'missing_assertions': profile['missing_assertion_count'],
                          'tone': 'warning' if not measured or profile['missing_case_count'] else 'ok' if passed == total else 'failed',
                          'categories': categories, 'cases': cases, 'score': observation(profile['F'])},
            'criteria': criteria, 'static': {k: observation({**static[k], 'unit': ''}, places=0 if k in ('D', 'L') else 2)
                                            for k in ('D', 'L', 'S')},
            'static_files': files, 'diagnostics': report.get('diagnostics', [])}


def configuration(row):
    from .domain import MAIN_CELLS
    cell = MAIN_CELLS.get(row['plan']['cell_key'])
    if not cell: return {}
    names = {'BF': 'Brute Force', 'SQL': 'SQL Injection', 'UP': 'File Upload'}
    models = row.get('models', {})
    return {'module': names[cell.module], 'context': cell.context,
            'planner': 'an' if cell.planner else 'aus', 'review': 'an' if cell.review else 'aus',
            'producer': models.get(cell.producer, {}).get('exact_model_id', 'Nicht erhoben'),
            'verifier': models.get(cell.verifier, {}).get('exact_model_id', 'Nicht erhoben'),
            'hash': row.get('configuration_hash'), 'id': row.get('configuration_id')}

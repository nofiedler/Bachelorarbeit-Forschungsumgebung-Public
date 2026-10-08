"""Evidence-backed partition: cross-process ordering is never inferred from indices."""
from decimal import Decimal, localcontext

from .numeric import context, exact_precision

from .domain import Observation, TimeInterval


def summarize(intervals: tuple[TimeInterval, ...], *, coverage_complete: bool):
    values = tuple(x for i in intervals for x in (i.monotonic_start, i.monotonic_end) if x is not None)
    with localcontext(context(exact_precision(values))):
        return _summarize(intervals, coverage_complete=coverage_complete)


def _summarize(intervals, *, coverage_complete):
    ordered = sorted(intervals, key=lambda i: i.sequence)
    if [i.sequence for i in ordered] != list(range(1,len(ordered)+1)):
        raise ValueError('Lückenlose Intervallfolge erforderlich; Lücken explizit speichern')
    if len({i.run_id for i in ordered}) > 1:
        raise ValueError('Ein Lauf je Zeitpartition')
    previous = None
    previous_utc = None
    previous_mono = {}
    unresolved_boundary = False
    boundary_excluded = False
    totals = {k: Decimal(0) for k in ('active','pause','outage')}
    counts = {k: 0 for k in totals}
    for i in ordered:
        if i.connection:
            if previous is None or i.connection.previous_interval_id != previous.id:
                raise ValueError('Verknüpfungsbeleg muss unmittelbar vorheriges Intervall binden')
            unresolved_boundary |= i.connection.relation == 'unknown_active'
            boundary_excluded |= i.connection.relation == 'excluded_gap'
        if i.started_at and previous_utc is not None and i.started_at < previous_utc:
            raise ValueError('Überlappende oder umgekehrt geordnete Zeitintervalle')
        if previous is not None:
            utc_contiguous = bool(previous.ended_at and i.started_at and previous.ended_at == i.started_at)
            mono_contiguous = (previous.process_id is not None and previous.process_id == i.process_id
                and previous.monotonic_end is not None and previous.monotonic_end == i.monotonic_start)
            # An index, coverage flag or two different monotonic clocks is no boundary proof.
            same_clock = previous.process_id is not None and previous.process_id == i.process_id and previous.monotonic_end is not None and i.monotonic_start is not None
            mono_gap = same_clock and i.monotonic_start > previous.monotonic_end
            if mono_gap and (utc_contiguous or i.connection and i.connection.relation == 'contiguous'):
                raise ValueError('Kontiguitätsbeleg/UTC widerspricht monotoner Zeitlücke')
            utc_gap = bool(previous.ended_at and i.started_at and i.started_at > previous.ended_at)
            if utc_gap and mono_contiguous:
                raise ValueError('Monotone Kontiguität widerspricht UTC-Zeitlücke')
            if not utc_contiguous and not mono_contiguous and i.connection is None:
                unresolved_boundary = True
            if previous.ended_at and i.started_at and i.started_at > previous.ended_at and i.connection and i.connection.relation == 'contiguous':
                raise ValueError('Kontiguitätsbeleg widerspricht UTC-Zeitlücke')
        if i.started_at:
            previous_utc = i.ended_at
        if i.monotonic_start is not None:
            old = previous_mono.get(i.process_id)
            if old is not None and i.monotonic_start < old:
                raise ValueError('Monotone Intervalle überlappen')
            previous_mono[i.process_id] = i.monotonic_end
            totals[i.kind] += i.monotonic_end-i.monotonic_start
            counts[i.kind] += 1
        previous = i
    active_unknown = not coverage_complete or unresolved_boundary or any(i.kind == 'unknown_active' for i in ordered)
    excluded_unknown = boundary_excluded or any(i.kind == 'excluded_gap' for i in ordered)
    source = ','.join(str(i.id) for i in ordered) or 'explicitly documented empty partition'
    output = {'known_partial_seconds': {k: str(v) if counts[k] else None for k,v in totals.items()},
        'interval_ids': [str(i.id) for i in ordered], 'partition_verified': not unresolved_boundary,
        'partial_sum_scope': 'measured segment durations; disjointness unverified' if unresolved_boundary else 'verified disjoint measured segments'}
    for key in totals:
        missing = active_unknown if key == 'active' else (active_unknown or excluded_unknown)
        output[key] = Observation(status='technical_missing',unit='s',reason='Zeitanteil/Verknüpfung unvollständig') if missing else Observation(status='observed',value=totals[key],unit='s',source=source)
    output['total'] = Observation(status='technical_missing',unit='s',reason='Gesamtdauer/Verknüpfung unvollständig') if active_unknown or excluded_unknown else Observation(status='observed',value=sum(totals.values()),unit='s',source=source)
    return output

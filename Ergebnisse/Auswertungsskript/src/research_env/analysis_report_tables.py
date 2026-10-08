"""Flat reporting tables derived only from an immutable M7 result.

Existing result arithmetic stays unchanged. Additional summaries explicitly
identify their population; the shared SQL reference is restricted to B_E in
both exploratory matrices. Membership tables retain the actual denominators.
"""
from collections import Counter
from decimal import Decimal
from fractions import Fraction

from .analysis import R_KEYS, T_KEYS, describe, describe_decimal, observed, value
from .analysis_metrics import COUNTS, TOKENS, EXPLORATIONS
from .domain import MAIN_CELLS

TIMES = ('pipeline_seconds', 'total_seconds', 'pause_seconds', 'outage_seconds',
         'setup_seconds', 'evaluation_seconds', 'manual_review_seconds',
         'context_preparation_seconds', 'load_seconds', 'inference_seconds',
         'retry_seconds', 'abort_seconds')

TABLE_DESCRIPTIONS = {
    'configurations': 'Eine Zeile je eingefrorener Konfiguration; Faktoren, Modellbindungen und geplanter Umfang. C-SQL-1 bleibt eine einzige Konfiguration.',
    'coverage': 'Häufigkeiten je ausdrücklich benannter Gruppierung, Kennzahl, Status und Ursache. Verschiedene scope-Werte sind getrennte Gruppierungen und dürfen nicht addiert werden.',
    'static-coverage': 'Gültige abgeschlossene statische Analysen relativ zu versiegelten Kandidaten; separate D/L/S-Verfügbarkeit. L=0 lässt nur S fehlen.',
    'exploratory-profiles': 'Zellstatistiken ausschließlich innerhalb der vorab bestimmten B_E-Blöcke, einschließlich beobachteter Zellen aus unvollständigen Quartetten.',
    'contrast-values': 'Eine Zeile je gültigem Blockkontrast einer Kennzahl, mit exaktem Wert und tatsächlicher Vergleichsmenge.',
    'denominator-members': 'Eine Zeile je tatsächlichem Mitglied einer Vergleichsmenge; membership_kind unterscheidet Lauf- und Blockeinheiten.',
    'report-sensitivity': 'Weglassen eines vollständigen Blocks sowie konservative Fehlwertgrenzen für den geplanten Umfang; keine Konfidenzintervalle.',
}
FIELD_DESCRIPTIONS = {
    'scope': 'Explizite Population/Gruppierung dieser Zeile; all_planned_main_runs, B_E-Zellen und vollständige Vergleichsmengen bleiben getrennt.',
    'group': 'Schlüssel der durch scope benannten Gruppe.',
    'cell_key': 'Fixierte Konfigurationskennung aus der vollständigen Hauptmatrix.',
    'module': 'Fester Migrationsfall: BF=Brute Force, SQL=SQL Injection, UP=File Upload.',
    'context': 'Zugewiesenes Kontextpaket K0 oder K1.',
    'producer': 'Modellplatz A/B der Produzentenrollengruppe.',
    'verifier': 'Modellplatz A/B der Verifikationsrollengruppe.',
    'producer_roles': 'Aktive Produzentenrollen; repair_if_required bezeichnet die höchstens einmalige bedingte Reparaturmöglichkeit, keinen ausgeführten Repair.',
    'verifier_roles': 'Aktive Verifikationsrollen; test erzeugt interne Tests, review ist optional.',
    'planner': 'Planner in dieser Konfiguration eingeschaltet (true/false).',
    'review': 'Review in dieser Konfiguration eingeschaltet (true/false).',
    'producer_exact_model_id': 'Eindeutig dokumentierte genaue Anbieter-Modellkennung des zugewiesenen Produzentenmodells.',
    'verifier_exact_model_id': 'Eindeutig dokumentierte genaue Anbieter-Modellkennung des zugewiesenen Verifikationsmodells.',
    'producer_model_package_id': 'Unveränderliche Paket-ID des Produzentenmodells.',
    'verifier_model_package_id': 'Unveränderliche Paket-ID des Verifikationsmodells.',
    'producer_endpoint': 'Dokumentierter Providerendpunkt des Produzentenmodells.',
    'verifier_endpoint': 'Dokumentierter Providerendpunkt des Verifikationsmodells.',
    'producer_upstream': 'Dokumentierter Upstreamanbieter des Produzentenmodells.',
    'verifier_upstream': 'Dokumentierter Upstreamanbieter des Verifikationsmodells.',
    'configuration_id': 'Eindeutige unveränderliche Konfigurationsversion; bleibt bei widersprüchlichen Bindungen leer.',
    'configuration_hash': 'Eindeutiger Inhaltshash der Konfigurationsversion.',
    'binding_status': 'complete=alle aufgelisteten Bindungen dokumentiert; partial_metadata=Metadaten fehlen; conflicting_recorded_bindings=mehrere widersprechende Werte, kein Wert willkürlich ausgewählt.',
    'shared_reference': 'C-SQL-1 dient in B_E beiden Zusatzmatrizen als bereits vorhandene Referenz, ohne zusätzlichen Lauf.',
    'planned_n': 'Geplante Anzahl Einheiten genau dieser Population; Einheit durch Tabelle/scope definiert.',
    'planned_in_B_E_n': 'Anzahl dieser Konfiguration in den vorab bestimmten Zusatzblöcken.',
    'r_C': 'Vorab fixierte geplante Zahl Kernwiederholungen.',
    'r_E': 'Vorab fixierte geplante Zahl explorativer Wiederholungen; erste r_E Blöcke bilden B_E.',
    'observed_n': 'Anzahl vollständig beobachteter Werte oder Kontraste dieser Kennzahl und Population.',
    'missing_n': 'Geplante minus vollständig beobachtete Werte in genau dieser Population.',
    'status_n': 'Anzahl Beobachtungen mit dieser Kombination aus Status und Ursache innerhalb der Gruppe.',
    'cause': 'Gespeicherte oder aus der fehlenden Beobachtung abgeleitete Begründung; keine Interpretation eines Modellfehlers.',
    'started_n': 'Anzahl tatsächlich begonnener Läufe der benannten Gruppe.',
    'sealed_candidate_n': 'Anzahl tatsächlich vorhandener versiegelter Kandidaten der Gruppe.',
    'completed_analysis_n': 'Anzahl gültiger abgeschlossener statischer Analysen unter diesen versiegelten Kandidaten.',
    'valid_analysis_ratio': 'completed_analysis_n / sealed_candidate_n; bei keinem Kandidaten nicht berechenbar.',
    'valid_analysis_ratio_exact': 'Exakter Bruch der Quote gültiger abgeschlossener statischer Analysen.',
    'ratio_denominator': 'Name des Felds, das den tatsächlichen Nenner der Analysequote enthält.',
    'D_observed_n': 'Anzahl gültiger Diagnoseanzahlen D.',
    'L_observed_n': 'Anzahl gültiger PHP-Codeumfänge L; ein gemessenes L=0 bleibt gültig.',
    'S_observed_n': 'Anzahl gültiger Dichten S=100D/L; L=0 ist ausgeschlossen.',
    'comparison': 'Eindeutiger Name des Vergleichs bzw. Zellprofils; Subtraktionsrichtung ist im Schlüssel dokumentiert.',
    'question': 'Zuordnung UF3 oder UF4 für die ergänzende explorative Tabelle.',
    'metric': 'Name der Messgröße; Einheiten stehen getrennt in unit.',
    'membership_kind': 'Einheit des Nenner-Mitglieds: planned_run oder block.',
    'block_index': 'Lesbare vorab festgelegte Wiederholungsblocknummer.',
    'block_id': 'Eindeutige Kennung des Wiederholungsblocks.',
    'planned_id': 'Vorab festgelegte Lauf-ID; bleibt auch ohne gestarteten Lauf vorhanden.',
    'run_id': 'Kennung des tatsächlich gestarteten Laufs, sofern vorhanden.',
    'value': 'Dezimale Darstellung des Werts; das Feld exact bewahrt die exakte Zahl.',
    'exact': 'Exakter rationaler Wert oder dokumentierte Dezimalzahl ohne Darstellungsrundung.',
    'status': 'Erhebungs-/Ableitungsstatus; fehlende Werte werden nicht als null ausgegeben.',
    'unit': 'Mess- bzw. Differenzeinheit dieses Datensatzes.',
    'reason': 'Begründung für nicht verfügbare oder nicht definierte Werte.',
    'denominator': 'Für genau diese Berechnung verwendeter Nenner, erklärt durch denominator_definition.',
    'denominator_definition': 'Weglassanalyse: vollständiges n_C−1; Fehlwertgrenzen: 3×geplantes r_C.',
    'omitted_block_id': 'Beim Weglasswert entfernter vollständiger Kernblock.',
    'omitted_block_index': 'Lesbare Blocknummer des beim Weglasswert entfernten Kernblocks.',
    'planned_core_blocks': 'Geplanter Kernumfang r_C der Fehlwertgrenzen.',
    'complete_core_blocks': 'Vollständige Kernblöcke n_C der beobachteten Hauptanalyse.',
    'kind': 'Art der Sensitivitätsrechnung: leave_one_out oder missing_bound.',
    'name': 'Untere (lower) oder obere (upper) konservative Fehlwertgrenze.',
}


def number(raw):
    # Imported at call time so analysis_tables may include these supplements.
    from .analysis_tables import number as decimal_display
    return decimal_display(raw)


def observations(row):
    """Value/status pairs without interpreting an absent usage category as zero."""
    functional, resources = row['functional'], row['resources']
    metrics = {key: functional[key] for key in ('F', 'T', 'full_success', 'raw_pass_ratio')}
    metrics.update(functional['R'])
    metrics.update(functional['T_criteria'])
    metrics.update({key: row['static'][key] for key in ('D', 'L', 'S')})
    metrics.update({key: resources.get(key, value(unit='s')) for key in TIMES})
    metrics.update({key: value(resources.get(key), unit='count') for key in COUNTS})
    for key in TOKENS:
        token = resources.get('tokens', {}).get('summary', {}).get(key, {})
        metrics['tokens.' + key] = {
            **token, 'unit': 'tokens',
            'status': 'observed' if token.get('status') == 'complete' else token.get('status', 'not_collected'),
        }
    cost = resources.get('costs') or {}
    metrics['cost'] = {
        'value': cost.get('amount') if cost.get('status') == 'known' else None,
        'status': 'observed' if cost.get('status') == 'known' else cost.get('status', 'not_collected'),
        'unit': cost.get('currency'), 'known_subtotal': cost.get('known_subtotal'),
        'reason': None if cost.get('status') == 'known' else 'Actual billing incomplete or unavailable',
    }
    return metrics


def _value_columns(item):
    return {'value': number(item.get('value')), 'exact': item.get('value'),
            'status': item.get('status', 'not_collected'), 'unit': item.get('unit'),
            'reason': item.get('reason')}


def _stats_columns(stats):
    record = {'observed_n': stats['n'], 'unit': stats['unit']}
    for key in ('mean', 'median', 'min', 'max', 'sample_sd'):
        record[key] = number(stats[key].get('value'))
        record[key + '_exact'] = stats[key].get('value')
    return record


def _groups(cells):
    """Disjoint populations within each named grouping, never pooled as one N."""
    yield {'scope': 'all_main', 'group': 'all_main'}, cells
    for key in MAIN_CELLS:
        yield {'scope': 'configuration', 'group': key, 'cell_key': key}, [r for r in cells if r['cell_key'] == key]
    core = [r for r in cells if r['cell_key'].startswith('C-')]
    for module in ('BF', 'SQL', 'UP'):
        for context in ('K0', 'K1'):
            members = [r for r in core if MAIN_CELLS[r['cell_key']].module == module and MAIN_CELLS[r['cell_key']].context == context]
            identity = {'module': module, 'context': context}
            yield {'scope': 'core_module_context', 'group': module + ':' + context, **identity}, members
            for block_index in sorted({r['block_index'] for r in members}):
                block = [r for r in members if r['block_index'] == block_index]
                yield {'scope': 'core_module_context_block', 'group': f'{module}:{context}:{block_index}',
                       **identity, 'block_index': block_index, 'block_id': block[0]['block_id']}, block
    for block_index in sorted({r['block_index'] for r in cells}):
        block = [r for r in cells if r['block_index'] == block_index]
        yield {'scope': 'block_all_main', 'group': str(block_index), 'block_index': block_index,
               'block_id': block[0]['block_id']}, block


def configurations(result):
    """One row per frozen configuration; conflicting bindings are not guessed."""
    rows = []
    for key, cell in MAIN_CELLS.items():
        selected = [row for row in result['cells'] if row['cell_key'] == key]
        record = {'cell_key': key, **cell.model_dump(), 'planned_n': len(selected),
            'r_C': result['r_C'], 'r_E': result['r_E'],
            'scope': 'all_planned_main_runs', 'phase_id': result['phase_id'],
            'freeze_id': result['freeze_id'], 'data_hash': result['data_hash'],
            'shared_reference': key == 'C-SQL-1',
            'planned_in_B_E_n': sum(row['in_b_e'] for row in selected),
            'producer_roles': 'analyzer; ' + ('planner; ' if cell.planner else '') + 'migrate; repair_if_required',
            'verifier_roles': 'test' + ('; review' if cell.review else '')}
        fields = {name: [row.get(name) for row in selected]
                  for name in ('configuration_id', 'configuration_hash')}
        for role, slot in (('producer', cell.producer), ('verifier', cell.verifier)):
            for field in ('id', 'exact_model_id', 'endpoint', 'upstream'):
                label = role + ('_model_package_id' if field == 'id' else '_' + field)
                fields[label] = [row.get('models', {}).get(slot, {}).get(field) for row in selected]
        conflicts = False
        for name, values in fields.items():
            known = sorted({str(item) for item in values if item is not None})
            record[name] = known[0] if len(known) == 1 else None
            record[name + '_distinct_n'] = len(known)
            record[name + '_missing_n'] = sum(item is None for item in values)
            conflicts |= len(known) > 1
        record['binding_status'] = ('conflicting_recorded_bindings' if conflicts else
            'complete' if all(item is not None for values in fields.values() for item in values) else 'partial_metadata')
        rows.append(record)
    return rows


def tables(result):
    """Return CSV-ready scalar records, with no writes or register access."""
    output = {key: [] for key in ('configurations', 'coverage', 'static-coverage', 'exploratory-profiles',
                                 'contrast-values', 'denominator-members', 'report-sensitivity')}
    cells = result['cells']
    metrics = {row['id']: observations(row) for row in cells}
    planned = {row['id']: row for row in cells}
    blocks = {row['block_id']: row for row in result['blocks']}
    output['configurations'] = configurations(result)

    for identity, members in _groups(cells):
        for metric in metrics[cells[0]['id']] if cells else ():
            counts = Counter()
            valid_n = 0
            for row in members:
                observation = metrics[row['id']][metric]
                valid = observed(observation) is not None
                valid_n += valid
                reason = None if valid else observation.get('reason') or row.get('missing') or 'No complete observation'
                counts[(observation.get('status', 'not_collected'), str(reason) if reason else None)] += 1
            for (status, reason), n in counts.items():
                output['coverage'].append({**identity, 'metric': metric, 'planned_n': len(members),
                    'observed_n': valid_n, 'missing_n': len(members) - valid_n,
                    'status': status, 'cause': reason, 'status_n': n})
        candidates = [r for r in members if r['candidate_hash']]
        analyses = [r for r in candidates if r['static']['analysis_complete']]
        ratio = value(Fraction(len(analyses), len(candidates)) if candidates else None)
        output['static-coverage'].append({**identity, 'planned_n': len(members),
            'started_n': sum(bool(r['run_id']) for r in members), 'sealed_candidate_n': len(candidates),
            'completed_analysis_n': len(analyses), 'valid_analysis_ratio': number(ratio['value']),
            'valid_analysis_ratio_exact': ratio['value'], 'ratio_denominator': 'sealed_candidate_n',
            **{key + '_observed_n': sum(observed(r['static'][key]) is not None for r in members) for key in ('D', 'L', 'S')}})

    # Each exploratory cell is described over its prespecified B_E, including
    # observed cells whose other quartet members are missing for a metric.
    for question, (keys, _) in EXPLORATIONS.items():
        for key in keys:
            selected = [r for r in cells if r['cell_key'] == key and r['in_b_e']]
            for metric in metrics[cells[0]['id']] if cells else ():
                units = sorted({metrics[r['id']][metric].get('unit') or 'unknown' for r in selected})
                for unit in units:
                    pairs = [(r, metrics[r['id']][metric]) for r in selected
                             if (metrics[r['id']][metric].get('unit') or 'unknown') == unit
                             and observed(metrics[r['id']][metric]) is not None]
                    numbers = [Decimal(item['value']) if metric == 'cost' else observed(item) for _, item in pairs]
                    ids = [r['id'] for r, _ in pairs]
                    describe_metric = describe_decimal if metric == 'cost' else describe
                    stats = describe_metric(numbers, unit=unit, ids=ids)
                    comparison = question + ':cell:' + key
                    output['exploratory-profiles'].append({'comparison': comparison, 'question': question,
                        'scope': 'prespecified_B_E', 'cell_key': key, 'metric': metric,
                        'planned_n': len(selected), 'missing_n': len(selected) - len(ids),
                        'shared_reference': key == 'C-SQL-1', **_stats_columns(stats)})
                    for row, _ in pairs:
                        output['denominator-members'].append({'comparison': comparison,
                            'metric': metric, 'unit': unit, 'scope': 'prespecified_B_E',
                            'membership_kind': 'planned_run', 'planned_id': row['id'],
                            'run_id': row['run_id'], 'block_id': row['block_id'], 'block_index': row['block_index']})

    def contrast_rows(comparison, metric, stats, *, scope, planned_n):
        for identifier, item in zip(stats['ids'], stats['values']):
            block = blocks.get(identifier)
            row = planned.get(identifier)
            identity = {'comparison': comparison, 'scope': scope, 'metric': metric,
                'planned_n': planned_n, 'observed_n': stats['n'],
                'block_id': identifier if block else row['block_id'] if row else None,
                'block_index': block['index'] if block else row['block_index'] if row else None,
                'planned_id': row['id'] if row else None, 'run_id': row['run_id'] if row else None}
            output['contrast-values'].append({**identity, **_value_columns(item)})
            output['denominator-members'].append({**identity, 'membership_kind': 'block' if block else 'planned_run',
                'unit': stats['unit']})

    contrast_rows('core', 'F', result['core'], scope='complete_B_C', planned_n=result['r_C'])
    for module, stats in result['module_on_B_C'].items():
        contrast_rows('UF1:common:' + module, 'F', stats, scope='complete_B_C', planned_n=result['r_C'])
    for module, stats in result['module_pairs'].items():
        contrast_rows('UF1:all_pairs:' + module, 'F', stats, scope='complete_module_pairs', planned_n=result['r_C'])
    for name, stats in result['module_contrasts'].items():
        contrast_rows('UF1:' + name, 'F', stats, scope='complete_B_C', planned_n=result['r_C'])
    for question in ('UF3', 'UF4'):
        for name, stats in result[question]['contrasts'].items():
            contrast_rows(question + ':' + name, 'F', stats, scope='complete_quartets_in_B_E', planned_n=result['r_E'])
        for metric, values in result['exploratory_metrics'][question].items():
            for name, stats in values['contrasts'].items():
                contrast_rows(question + ':' + name, metric, stats, scope='metric_complete_quartets_in_B_E', planned_n=result['r_E'])
    for metric, item in result['metric_comparisons'].items():
        contrast_rows('context:all_modules', metric, item['complete_block_differences'],
                      scope='metric_complete_core_blocks', planned_n=result['r_C'])
        for module, stats in item['module_pairs'].items():
            contrast_rows('context:' + module, metric, stats, scope='metric_complete_module_pairs', planned_n=result['r_C'])
    for module, profiles in result['criterion_pairs'].items():
        for metric, stats in profiles.items():
            contrast_rows('criterion_pairs:' + module, metric, stats, scope='metric_complete_module_pairs', planned_n=result['r_C'])

    for bound in ('lower', 'upper'):
        output['report-sensitivity'].append({'kind': 'missing_bound', 'name': bound,
            'planned_core_blocks': result['r_C'], 'complete_core_blocks': result['n_C'],
            'denominator': result['missing_bounds']['denominator'], 'denominator_definition': '3 * planned r_C',
            **_value_columns(result['missing_bounds'][bound])})
    leave = result['core']['leave_one_out']
    for block_id, item in zip(leave['omitted_block_ids'], leave['values']):
        output['report-sensitivity'].append({'kind': 'leave_one_out', 'omitted_block_id': block_id,
            'omitted_block_index': blocks[block_id]['index'], 'denominator': result['n_C'] - 1,
            'denominator_definition': 'complete n_C - 1', **_value_columns(item)})
    return output

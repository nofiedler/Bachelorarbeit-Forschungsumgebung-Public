"""Read-only run membership for report tables; no replacement of result arithmetic.

Statistics name their own IDs. Block IDs are expanded only to the configurations
actually used by that row; eligibility sets are labelled separately.
"""
from .analysis import observed
from .analysis_metrics import EXPLORATIONS
from .analysis_report_tables import observations

CORE = tuple('C-' + module + '-' + str(context) for module in ('BF', 'SQL', 'UP') for context in (0, 1))


def table_populations(result, extra):
    cells = result['cells']
    by_run = {row['run_id']: row for row in cells if row.get('run_id')}
    obs = {row['id']: observations(row) for row in cells}

    def select(*, ids=None, blocks=None, keys=None, be=False):
        return [row for row in cells if (ids is None or row['id'] in ids)
                and (blocks is None or row['block_id'] in blocks)
                and (keys is None or row['cell_key'] in keys) and (not be or row['in_b_e'])]

    def member(row):
        return {key: row.get(key) for key in ('position', 'id', 'run_id', 'cell_key', 'block_id', 'block_index', 'configuration_id')}

    def population(label, rows, *, n=None, unit='Laufwerte', note='', role='used'):
        ordered = sorted(rows, key=lambda row: (row.get('position') is None, row.get('position') or 0, row.get('run_id') or ''))
        return {'label': label, 'n': len(rows) if n is None else n, 'unit': unit,
                'run_count': len(rows), 'started_count': sum(bool(r.get('run_id')) for r in rows),
                'runs': [member(r) for r in ordered], 'note': note, 'role': role}

    def profile(key, metric, stats):
        return population(metric, select(ids=stats['ids'], keys=[key]), n=stats['n'])

    def block_population(label, ids, keys=CORE, unit='vollständige Kernblöcke', note='', role='used'):
        return population(label, select(blocks=ids, keys=keys), n=len(ids), unit=unit, note=note, role=role)

    def bundle(*groups):
        return {'groups': list(groups)}

    output = {'configurations': {}, 'profiles': {}, 'metrics': {}, 'module_common': {},
              'module_pairs': {}, 'module_contrasts': {}, 'criteria': {}, 'blocks': {},
              'exploration': {}, 'exploratory_profiles': {}, 'sensitivity': {}, 'areas': {}}
    for key, item in result['configurations'].items():
        rows = select(keys=[key])
        planned = population('Geplante Wiederholungen', rows, unit='Planplätze', role='planned',
                             note='Bezugsmenge der Konfiguration, nicht der Nenner jeder Messgröße.')
        stats = {**item['profiles'], **result['configuration_endpoints'][key]}
        output['profiles'][key] = {metric: bundle(profile(key, metric, stat)) for metric, stat in stats.items()}
        output['configurations'][key] = bundle(planned,
            population('Davon in B_E vorgesehen', select(keys=[key], be=True), unit='Planplätze', role='planned'))
        output['profiles'][key]['repetition'] = bundle(profile(key, 'F', stats['F']),
            population('Versiegelte Kandidaten für die Codehash-Anzahl', [r for r in rows if r.get('candidate_hash')], unit='Kandidaten'))
        output['profiles'][key]['coverage'] = bundle(planned, *[
            profile(key, metric, stats[metric]) for metric in ('F','D','L','S','pipeline_seconds','tokens.total')],
            *[profile(key, metric, stat) for metric, stat in stats.items() if metric.startswith('cost:')])
        output['profiles'][key]['functional'] = bundle(*[profile(key, metric, stats[metric])
            for metric in ('T','full_success','raw_pass_ratio','R1','R2','R3','R4','R5','R6')])
        output['profiles'][key]['targets'] = bundle(*[profile(key, metric, stats[metric]) for metric in ('T1','T2','T3','T4','T5')])

    output['core'] = bundle(block_population('Primärer Kontextvergleich B_C', result['core']['ids'],
        note='n zählt Blockmittel. Jeder verwendete Block umfasst sechs Kernläufe; Zusatzbedingungen gehen nicht ein.'))
    for block in result['blocks']:
        groups = []
        for module, metric in block['module_differences'].items():
            ids = [block['block_id']] if observed(metric) is not None else []
            groups.append(block_population(module + ': K1−K0', ids,
                keys=[f'C-{module}-0', f'C-{module}-1'], unit='vollständige Paare'))
        groups.append(block_population('Blockmittel g_b', [block['block_id']] if block['in_B_C'] else [],
            note='Nur bei sechs vorhandenen F-Werten berechnet.'))
        output['blocks'][block['block_id']] = bundle(*groups)
    for module, stats in result['module_on_B_C'].items():
        keys = [f'C-{module}-0', f'C-{module}-1']
        output['module_common'][module] = bundle(
            block_population('K0/K1-Mittel und Paardifferenz auf B_C', stats['ids'], keys, 'Paare in B_C'),
            block_population('Auswahlbasis: vollständige B_C', stats['ids'], role='eligibility', note='Die anderen Module bestimmen nur die gemeinsame Blockauswahl.'))
        pair_stats = result['module_pairs'][module]
        output['module_pairs'][module] = bundle(block_population('Ergänzend: alle gültigen Modulpaare', pair_stats['ids'], keys, 'vollständige Paare'))
    for name, stats in result['module_contrasts'].items():
        keys = [f'C-{module}-{context}' for module in name.split('-') for context in (0,1)]
        output['module_contrasts'][name] = bundle(block_population(name, stats['ids'], keys),
            block_population('Auswahlbasis: vollständige B_C', stats['ids'], role='eligibility', note='Die übrigen Kernläufe bestimmen nur die gemeinsame Blockauswahl.'))
    for metric, data in result['metric_comparisons'].items():
        output['metrics'][metric] = {'blocks': bundle(block_population(metric, data['complete_block_differences']['ids'],
            note='Eigene vollständige Blockmenge dieser Messgröße; nicht automatisch die F-Blockmenge B_C.')),
            'pairs': {module: bundle(block_population(metric, stats['ids'],
                [f'C-{module}-0', f'C-{module}-1'], 'vollständige Paare')) for module, stats in data['module_pairs'].items()}}
    for module, metrics in result['criterion_pairs'].items():
        output['criteria'][module] = {metric: bundle(block_population(metric, stats['ids'],
            [f'C-{module}-0', f'C-{module}-1'], 'vollständige Paare')) for metric, stats in metrics.items()}

    for question, (keys, formulas) in EXPLORATIONS.items():
        output['exploration'][question] = {}
        output['exploratory_profiles'][question] = {}
        for metric, data in {'F': result[question], **result['exploratory_metrics'][question]}.items():
            output['exploration'][question][metric] = {}
            for name, stats in data['contrasts'].items():
                # Every exploratory contrast is selected by a complete quartet,
                # even when its arithmetic uses only two of its four values.
                weights = formulas.get(name)
                used_keys = [key for key, weight in zip(keys, weights) if weight] if weights else keys
                used = select(blocks=stats['ids'], keys=used_keys, be=True)
                eligibility = select(blocks=stats['ids'], keys=keys, be=True)
                groups = [population(name, used, n=stats['n'], unit='vollständige Vierergruppen',
                    note='Rechnerisch verwendete Laufwerte; n zählt die zugrunde liegenden Vierergruppen.')]
                if set(used_keys) != set(keys):
                    groups.append(population('Auswahlbasis: vollständige Vierergruppen', eligibility,
                        n=stats['n'], unit='Vierergruppen', role='eligibility',
                        note='Alle vier Bedingungen müssen gültig sein. Die zusätzliche Auswahlbasis ist kein weiterer Summand.'))
                output['exploration'][question][metric][name] = bundle(*groups)

    # Supplementary table rows retain exactly the population used by their builder.
    for row in extra['exploratory-profiles']:
        memberships = [item['planned_id'] for item in extra['denominator-members']
            if item.get('membership_kind') == 'planned_run' and item.get('comparison') == row['comparison']
            and item['metric'] == row['metric'] and item['unit'] == row['unit']]
        row['population'] = bundle(population(row['metric'], select(ids=memberships), n=row['observed_n'],
            note='Einzelwerte dieser Bedingung innerhalb B_E, auch aus unvollständigen Vierergruppen.'))
        output['exploratory_profiles'][row['question']].setdefault(row['cell_key'], {})[row['metric'] + ':' + row['unit']] = row['population']
    for row in extra['coverage']:
        if row['scope'] != 'configuration': continue
        members = []
        for cell in select(keys=[row['group']]):
            item = obs[cell['id']][row['metric']]
            reason = None if observed(item) is not None else item.get('reason') or cell.get('missing') or 'No complete observation'
            if item.get('status','not_collected') == row['status'] and (str(reason) if reason else None) == row['cause']:
                members.append(cell)
        row['population'] = bundle(population(row['metric'] + ' · ' + row['status'], members,
            n=row['status_n'], unit='Statusfälle', role='status',
            note='Genau diese Status-/Ursachengruppe; keine Liste gültiger Messwerte.'))
    for row in extra['static-coverage']:
        if row['scope'] != 'configuration': continue
        members = select(keys=[row['cell_key']])
        candidates = [r for r in members if r.get('candidate_hash')]
        row['population'] = bundle(population('Nenner der Analysequote', candidates, unit='versiegelte Kandidaten'),
            population('Zähler der Analysequote', [r for r in candidates if r['static']['analysis_complete']], unit='abgeschlossene Analysen'),
            *[population(metric, [r for r in members if observed(r['static'][metric]) is not None]) for metric in ('D','L','S')])
    for omitted in result['core']['leave_one_out']['omitted_block_ids']:
        ids = [bid for bid in result['core']['ids'] if bid != omitted]
        output['sensitivity'][omitted] = bundle(block_population('Verbleibende vollständige Kernblöcke', ids,
            note='Der in dieser Zeile weggelassene Block und seine sechs Kernläufe gehen nicht ein.'))
    output['bounds'] = bundle(population('Bezugsmenge der Fehlwertgrenzen', select(ids=result['missing_bounds']['planned_core_ids']),
        n=result['missing_bounds']['denominator'], unit='geplante Modulpaare (3 × r_C)', role='planned',
        note='Alle geplanten Kernläufe: vorhandene F-Werte bleiben erhalten, fehlende werden nur für diese Grenzen mit 0 oder 1 angesetzt. Keine Liste vollständig beobachteter Werte.'))

    def area_members(ids):
        return [by_run.get(identity, {'run_id': identity}) for identity in ids]

    for name, area in result['resource_areas'].items():
        total = area.get('tokens', {}).get('total', {})
        groups = [population('Bereichsumfang', area_members(area.get('run_ids', [])), n=area['n_started'], unit='gestartete Läufe', role='reference',
            note='Bereichsbilanz; Läufe außerhalb der Planmatrix erhalten keine erfundene Laufnummer.'),
            population('Tokens gesamt: vollständig dokumentierte Laufwerte', area_members(total.get('valid_run_ids', [])),
                note='Status der angezeigten Summe: ' + total.get('status', 'nicht dokumentiert') + '. Bei unvollständiger Bilanz nur vollständige Laufbeiträge zur bekannten Teilsumme.'),
            population('Weitere Läufe im Bereich ohne vollständigen Tokenwert',
                area_members([identity for identity in area.get('run_ids', []) if identity not in total.get('valid_run_ids', [])]),
                unit='Läufe ohne vollständigen Tokenwert', role='partial',
                note='Bekannte Teilbeiträge sind möglich. Ihr genauer Anteil ist aus diesem Summenstand nicht zuordenbar; diese Läufe zählen nicht als vollständige Tokenwerte.')]
        for currency, cost in area.get('currency_groups', {}).items():
            groups += [population('API-Kosten ' + currency + ': vollständig dokumentiert', area_members(cost.get('complete_run_ids', [])),
                note='Status der angezeigten Summe: ' + cost.get('status', 'nicht dokumentiert') + '.'),
                population('API-Kosten ' + currency + ': nur teilweise dokumentiert', area_members(cost.get('partial_run_ids', [])),
                    unit='Läufe mit Teilkosten', role='partial', note='Nur bekannte Teilbeträge, keine vollständigen Laufkosten.')]
        output['areas'][name] = bundle(*groups)
    return output

"""Run provenance of plotted observations, derived only from saved ID sets.

A figure may contain several populations. Their union is an inventory of source
runs, never a replacement for a pair, block or metric-specific denominator.
"""
MODULES = {'BF': 'Brute Force', 'SQL': 'SQL Injection', 'UP': 'File Upload'}
ROLES = {'used': 'Messwert verwendet', 'not_used': 'Nicht in dieser dargestellten Teilmenge',
         'bounded_missing': 'Kein beobachteter F-Wert; für Grenzen mit [0, 1] berücksichtigt',
         'omitted': 'In dieser Weglassrechnung ausgelassen'}


def figure_population(result, name, info):
    cells = sorted(result['cells'], key=lambda row: (row['position'], row['id']))
    lookup = {row['id']: row for row in cells}
    core_keys = {row['cell_key'] for row in cells if row['cell_key'].startswith('C-')}
    data = info.get('figure_data', {})
    points = data.get('points', [])
    sets = info.get('denominator_sets', {})
    groups = []

    def ordered(ids):
        ids = set(ids)
        unknown = ids - lookup.keys()
        if unknown:
            raise ValueError('Diagrammherkunft verweist auf unbekannte Plan-IDs')
        return [row['id'] for row in cells if row['id'] in ids]

    def blocks(ids, keys=core_keys):
        ids = set(ids)
        known = {row['block_id'] for row in cells}
        if ids - known:
            raise ValueError('Diagrammherkunft verweist auf unbekannte Block-IDs')
        return [row['id'] for row in cells if row['block_id'] in ids and row['cell_key'] in keys]

    def add(key, label, planned, used, *, unit, count, explanation, omitted=(), bounded=(),
            not_used='Nicht in dieser dargestellten Teilmenge', observations=()):
        planned, used = ordered(planned), ordered(used)
        omitted, bounded = ordered(omitted), ordered(bounded)
        if (set(used + omitted + bounded) - set(planned) or
                set(used) & set(omitted + bounded) or set(omitted) & set(bounded)):
            raise ValueError('Widersprüchliche Diagrammherkunft')
        groups.append({'key': key, 'label': label, 'explanation': explanation,
            'observation_unit': unit, 'observation_count': count,
            'planned_ids': planned, 'used_planned_ids': used,
            'omitted_planned_ids': omitted, 'bounded_missing_planned_ids': bounded,
            'planned_run_count': len(planned),
            'used_run_count': len({lookup[pid]['run_id'] for pid in used if lookup[pid]['run_id']}),
            'not_used_status': not_used,
            'observations': [{'planned_ids': ordered(item['planned_ids']),
                              'positions': [lookup[pid]['position'] for pid in ordered(item['planned_ids'])],
                              **{field: item[field] for field in ('block_id', 'module') if field in item}}
                             for item in observations]})

    def pair_groups(module_sets, *, block_figure=False):
        for module, label in MODULES.items():
            keys = {f'C-{module}-0', f'C-{module}-1'}
            planned_blocks = sets['planned_block_ids'] if block_figure else module_sets[module]['planned_block_ids']
            chosen = [p for p in points if p.get('metric') == 'd_mb' and p.get('module') == module]
            used = [pid for point in chosen for pid in point['planned_ids']]
            add('pairs:' + module, label + ' · vollständige Modulpaare', blocks(planned_blocks, keys), used,
                unit='complete_module_pair', count=len(chosen), observations=chosen,
                explanation='Jeder Unterschied verwendet genau den K0- und K1-Lauf desselben Moduls und Blocks. '
                            'Die beiden Laufnummern jeder Paarung stehen in den Beobachtungsgruppen.',
                not_used='Kein dargestelltes vollständiges Modulpaar')
            groups[-1]['common_core_planned_ids'] = ordered(
                [pid for point in chosen if point.get('in_B_C') for pid in point['planned_ids']])
            groups[-1]['additional_pair_planned_ids'] = ordered(
                [pid for point in chosen if not point.get('in_B_C') for pid in point['planned_ids']])

    if name == 'sensitivitaet':
        planned = sets['planned_core_ids']
        common = blocks(sets['B_C'])
        add('primary-reference', 'Referenzlinie: primärer Mittelwert Δ_C', planned,
            common if data.get('primary_mean') is not None else [],
            unit='complete_core_block', count=len(sets['B_C']) if data.get('primary_mean') is not None else 0,
            explanation='Referenz sind ausschließlich die vorhandenen vollständigen Kernblöcke B_C; '
                        'je Block gehen sechs Kernläufe in drei Modulpaare ein.',
            not_used='Nicht Bestandteil eines vollständigen Kernblocks B_C')
        for point in points:
            retained = blocks(point['retained_block_ids'])
            omitted = blocks([point['omitted_block_id']])
            add('leave-one-out:' + point['omitted_block_id'],
                'Weglassrechnung ohne Block ' + str(point['omitted_block_index']), common, retained,
                omitted=omitted, unit='retained_complete_core_block', count=len(point['retained_block_ids']),
                explanation='Dieser dargestellte Mittelwert verwendet nur die verbleibenden vollständigen '
                            'Kernblöcke. Die sechs Läufe des genannten Blocks werden ausgelassen.')
        # These IDs already identify observed F values in the confirmed result.
        # No new evidence selection or functional-score calculation is performed.
        valid_f = {pid for config in result['configurations'].values()
                   for pid in config['profiles']['F']['ids']}
        used = [pid for pid in planned if pid in valid_f]
        bounded = [pid for pid in planned if pid not in valid_f]
        add('missing-bounds', 'Fehlwertgrenzen: alle geplanten Kernpaare', planned, used, bounded=bounded,
            unit='planned_module_pair_for_bounds', count=sets['bounds_pair_denominator'],
            explanation='Die Bezugsmenge umfasst sämtliche geplanten Kernpaare. Beobachtete F-Werte bleiben '
                        'fest; fehlende Werte werden nur zur Grenzberechnung innerhalb [0, 1] variiert. '
                        'Diese Grenzen sind keine zusätzlichen beobachteten Läufe.')
    elif name == 'blockunterschiede':
        pair_groups(sets['module_pairs'], block_figure=True)
        chosen = [point for point in points if point.get('metric') == 'g_b']
        add('core-blocks', 'Rechtes Panel: Kernblockmittel g_b und Δ_C', blocks(sets['planned_block_ids']),
            [pid for point in chosen for pid in point['planned_ids']],
            unit='complete_core_block', count=len(chosen), observations=chosen,
            explanation='Ein Punkt ist der Mittelwert dreier Modulpaare aus genau sechs Kernläufen '
                        'desselben vollständigen Blocks B_C. Δ_C verwendet diese Blockmittel.',
            not_used='Nicht Bestandteil eines dargestellten vollständigen Kernblocks')
    elif name == 'modul-kontextunterschiede':
        pair_groups(sets)
    else:
        individual_sets = sets.get('cells', sets)
        for key, group in individual_sets.items():
            if not isinstance(group, dict) or 'planned_ids' not in group:
                raise ValueError('Unbekannte Bezugsmenge für Diagrammherkunft')
            used = group.get('observed_ids', group.get('complete_F_resource_ids'))
            if used is None:
                raise ValueError('Beobachtungs-IDs für Diagrammherkunft fehlen')
            resource = 'complete_F_resource_ids' in group
            label = MODULES.get(key, key)
            metric = next((point['metric'] for point in points if point.get('planned_id') in group['planned_ids']),
                          name.removeprefix('statisch-') if name.startswith('statisch-') else 'F')
            resource_label = ('aktive Pipelinezeit' if name == 'F-zeit' else
                              'Tokens gesamt' if name == 'F-tokens' else 'API-Kosten in ' + str(data.get('currency', '')))
            add('values:' + key, label + (' · F und ' + resource_label if resource else ' · ' + metric + '-Einzelwerte'),
                group['planned_ids'], used, unit='planned_run', count=len(used),
                explanation=('Ein Punkt verwendet F und den vollständig erfassten Ressourcenwert desselben Laufs. '
                             'Unvollständige Werte oder andere Währungen werden hier nicht als Punkte verwendet.' if resource else
                             'Gezeigt werden die vorhandenen Einzelwerte dieser Teilgruppe. '
                             'Fehlende Werte sind nicht als Null eingetragen.'),
                not_used='Kein dargestelltes vollständiges Wertepaar' if resource else 'Kein dargestellter gültiger Einzelwert')
            paired = [pair for pair in data.get('pairs', []) if pair['module'] == key]
            if 'pairs' in data:
                add('paired-lines:' + key, label + ' · verbundene K0/K1-Paare', group['planned_ids'],
                    [pid for pair in paired for pid in pair['planned_ids']],
                    unit='complete_module_pair', count=len(paired), observations=paired,
                    explanation='Die Verbindungslinie verbindet genau zwei vorhandene Werte desselben Moduls '
                                'und Blocks. Ein einzelner vorhandener Wert bleibt als Punkt sichtbar.',
                    not_used='Nicht Teil eines vollständig verbundenen K0/K1-Paars')
        if 'quartets' in data:
            quartets = data['quartets']
            add('quartets', 'Verbundene vollständige Vierergruppen',
                [pid for group in individual_sets.values() for pid in group['planned_ids']],
                [pid for quartet in quartets for pid in quartet['planned_ids']],
                unit='complete_exploratory_quartet', count=len(quartets), observations=quartets,
                explanation='Nur vollständige Vierergruppen werden verbunden. Die A/A-Referenz C-SQL-1 '
                            'ist derselbe bereits vorhandene Kernlauf und wird nicht als zusätzlicher Lauf gezählt.',
                not_used='Nicht Teil einer dargestellten vollständigen Vierergruppe')

    planned_union = set(pid for group in groups for pid in group['planned_ids'])
    used_union = set(pid for group in groups for pid in group['used_planned_ids'])
    rows = []
    for row in cells:
        pid = row['id']
        if pid not in planned_union:
            continue
        memberships = []
        for group in groups:
            if pid not in group['planned_ids']:
                continue
            role = ('used' if pid in group['used_planned_ids'] else
                    'omitted' if pid in group['omitted_planned_ids'] else
                    'bounded_missing' if pid in group['bounded_missing_planned_ids'] else 'not_used')
            status = group['not_used_status'] if role == 'not_used' else ROLES[role]
            if pid in group.get('common_core_planned_ids', []):
                status += ' · gemeinsamer Kernblock B_C'
            elif pid in group.get('additional_pair_planned_ids', []):
                status += ' · zusätzliches Modulpaar außerhalb B_C'
            memberships.append({'group_key': group['key'], 'group_label': group['label'], 'role': role, 'status': status})
        rows.append({'position': row['position'], 'planned_id': pid,
            **{key: row.get(key) for key in ('run_id', 'cell_key', 'block_id', 'block_index',
                'configuration_id', 'configuration_hash', 'candidate_hash')}, 'memberships': memberships})
    used_runs = {lookup[pid]['run_id'] for pid in used_union if lookup[pid]['run_id']}
    return {'schema': 'research-figure-population-v1', 'observation_unit': info['observation_unit'],
            'planned_run_count': len(planned_union), 'used_run_count': len(used_runs),
            'summary': f'Messwerte aus {len(used_runs)} Läufen · {len(planned_union)} geplante Laufpositionen. '
                       'Die genaue Auswahl steht je Teilansicht unten.',
            'groups': groups, 'rows': rows, 'csv_filename': name + '-population.csv'}


def population_rows(population):
    """Flat CSV: one planned run per explicit subgroup, not one fictitious n."""
    groups = {group['key']: group for group in population['groups']}
    output = []
    for row in population['rows']:
        for membership in row['memberships']:
            group = groups[membership['group_key']]
            output.append({**{key: value for key, value in row.items() if key != 'memberships'},
                **membership, 'used_as_observation': membership['role'] == 'used',
                'group_observation_unit': group['observation_unit'],
                'group_observation_count': group['observation_count'],
                'group_planned_run_count': group['planned_run_count'],
                'group_used_run_count': group['used_run_count']})
    return output

"""Publication figures from the fixed analysis result, with exact figure data.

Only display coordinates become floats. Missing observations are not plotted as
zero; every plot declares its own population and the IDs behind its marks.
"""
from fractions import Fraction

from .analysis import observed

COLORS = {'K0': '#0072B2', 'K1': '#D55E00'}
MARKERS = {'K0': 'o', 'K1': 's'}
MODULES = {'BF': 'Brute Force', 'SQL': 'SQL Injection', 'UP': 'File Upload'}
STYLE = {
    'font.family': 'DejaVu Sans', 'font.size': 9,
    'axes.titlesize': 10, 'axes.labelsize': 9, 'axes.linewidth': .6,
    'axes.edgecolor': '#333333', 'axes.labelcolor': '#222222',
    'text.color': '#222222', 'xtick.color': '#333333', 'ytick.color': '#333333',
    'xtick.labelsize': 8, 'ytick.labelsize': 8, 'legend.fontsize': 8,
    'figure.facecolor': 'white', 'axes.facecolor': 'white', 'savefig.facecolor': 'white',
    'pdf.fonttype': 42, 'ps.fonttype': 42, 'svg.fonttype': 'none',
}


def _number(value):
    return f'{float(value):g}'.replace('.', ',')



def _axis_number(value, step):
    """Readable German tick labels without offset notation or lost cost precision."""
    from decimal import Decimal
    # Tick subtraction has binary noise; the locator itself uses decimal steps.
    decimal_step = Decimal(format(abs(step), '.12g')).normalize()
    places = max(0, -decimal_step.as_tuple().exponent)
    if abs(value) < abs(step) * 1e-8:
        value = 0  # Avoid a spurious minus sign at the zero tick.
    return format(value, f',.{places}f').translate(str.maketrans({',': '.', '.': ','}))


def _resource_axis(ax, *, integer=False, scale=1):
    """Common resource ticks; count axes never imply fractional tokens."""
    from matplotlib.ticker import AutoMinorLocator, FuncFormatter, MaxNLocator, NullLocator
    min_ticks = 4
    if integer:
        from math import ceil, floor
        lower, upper = ax.get_xlim()
        if upper - lower < 2:
            lower = max(0, floor(lower))
            ax.set_xlim(lower, max(lower + 2, ceil(upper)))
        lower, upper = ax.get_xlim()
        min_ticks = min(4, max(2, floor(upper) - ceil(lower) + 1))
    ax.xaxis.set_major_locator(MaxNLocator(
        nbins=5, min_n_ticks=min_ticks,
        steps=[1, 2, 2.5, 5, 10], integer=integer))
    ticks = ax.get_xticks()
    step = float(ticks[1] - ticks[0])
    ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: _axis_number(value / scale, step / scale)))
    if max(len(_axis_number(value / scale, step / scale)) for value in ticks) > 7:
        ax.tick_params(axis='x', labelrotation=60)
        for label in ax.get_xticklabels():
            label.set_horizontalalignment('right')
    # Half-step marks refine interpolation; for counts they must also be integers.
    subdivisions = 2 if not integer or step % 2 == 0 else (5 if step % 5 == 0 else 0)
    ax.xaxis.set_minor_locator(AutoMinorLocator(subdivisions) if subdivisions else NullLocator())
    ax.tick_params(axis='x', which='major', length=4, width=.6)
    ax.tick_params(axis='x', which='minor', length=2.5, width=.5, color='#777777')
    ax.grid(axis='x', which='major', color='#dddddd', linewidth=.5)
    ax.grid(axis='x', which='minor', color='#eeeeee', linewidth=.35)


def _point(row, number, metric, **extra):
    return {'planned_id': row['id'], 'run_id': row['run_id'],
            'block_id': row['block_id'], 'block_index': row['block_index'],
            'cell_key': row['cell_key'], 'candidate_hash': row['candidate_hash'],
            'metric': metric, 'value': str(number), **extra}


def figures(result):
    import matplotlib as mpl
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.ticker import FuncFormatter, MaxNLocator

    output = []
    block_indices = {b['block_id']: b['index'] for b in result['blocks']}
    core_rows = [r for r in result['cells'] if r['cell_key'].startswith('C-')]
    core_lookup = {(r['block_id'], r['cell_key']): r for r in core_rows}

    def pair_point(block, module, number, **extra):
        pair = [core_lookup[(block['block_id'], f'C-{module}-{context}')]
                for context in ('0', '1')]
        return {'block_id': block['block_id'], 'block_index': block['index'],
                'module': module, 'metric': 'd_mb', 'value': str(number * 100),
                'unit': 'percentage_points', 'in_B_C': block['block_id'] in result['B_C'],
                'planned_ids': [r['id'] for r in pair],
                'run_ids': [r['run_id'] for r in pair],
                'candidate_hashes': [r['candidate_hash'] for r in pair],
                'K0': _point(pair[0], observed(pair[0]['functional']['F']), 'F'),
                'K1': _point(pair[1], observed(pair[1]['functional']['F']), 'F'), **extra}

    def style_axis(ax, *, f_score=False):
        ax.spines[['top', 'right']].set_visible(False)
        ax.grid(axis='y', color='#dddddd', linewidth=.5)
        ax.set_axisbelow(True)
        if f_score:
            ax.set_ylim(-.05, 1.05)
            ax.set_yticks([i / 6 for i in range(7)], ['0', '1/6', '1/3', '1/2', '2/3', '5/6', '1'])
        else:
            ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: _number(x)))

    def legend_context(fig):
        fig.legend(handles=[Line2D([], [], color=COLORS[k], marker=MARKERS[k],
                                  linestyle='none', markersize=5, label=k)
                            for k in ('K0', 'K1')], loc='upper right',
                   bbox_to_anchor=(.98, .94), ncol=2, frameon=False)

    def finish(fig, name, title, caption, *, questions, points, ids,
               observation_unit, unit, denominator_sets, **data):
        fig.suptitle(title, x=.025, y=.99, ha='left', fontsize=10, fontweight='normal')
        if result['data_origin'] == 'synthetic':
            fig.text(.025, .008, 'SYNTHETISCHE TESTDATEN · keine Studienergebnisse',
                     fontsize=8, color='#555555')
        fig.tight_layout(rect=(0, .04, 1, .90), pad=1.2, w_pad=1.6)
        output.append((name, fig, {
            'n': len(ids), 'denominator_ids': list(ids),
            'observation_unit': observation_unit, 'unit': unit,
            'denominator_sets': denominator_sets, 'caption': caption,
            'research_questions': questions, 'title': title,
            'plot_data': 'manifest.json',
            'figure_data': {'points': points, **data},
            'uncertainty': 'Descriptive observations only; no confidence intervals or inferential tests',
            'rendering': {'width_inches': float(fig.get_figwidth()),
                          'height_inches': float(fig.get_figheight()),
                          'source_values': 'exact rational or recorded decimal; float only for plot coordinates'},
        }))

    def paired(metric, name, title, ylabel, unit):
        fig, axes = plt.subplots(1, 3, figsize=(7.4, 3.8), sharey=True)
        points, pairs, sets = [], [], {}
        for ax, (module, module_label) in zip(axes, MODULES.items()):
            rows = [r for r in core_rows if r['cell_key'].startswith('C-' + module + '-')]
            by_block = {}
            for row in rows:
                source = row['functional'] if metric == 'F' else row['static']
                number = observed(source.get(metric))
                by_block.setdefault(row['block_id'], {})[row['cell_key'][-1]] = (row, number)
            valid_ids = []
            for bid, members in sorted(by_block.items(), key=lambda item: block_indices[item[0]]):
                positions = []
                for k in ('0', '1'):
                    row, number = members[k]
                    if number is None:
                        continue
                    context = 'K' + k
                    x = row['block_index'] + (-.12 if k == '0' else .12)
                    ax.plot(x, float(number), marker=MARKERS[context], linestyle='none',
                            color=COLORS[context], markersize=5, markeredgewidth=.7, zorder=3)
                    points.append(_point(row, number, metric, context=context, display_x=x))
                    valid_ids.append(row['id'])
                    positions.append((x, float(number)))
                if len(positions) == 2:
                    ax.plot(*zip(*positions), color='#777777', linewidth=.8, zorder=2)
                    pairs.append({'block_id': bid, 'module': module,
                                  'planned_ids': [members[k][0]['id'] for k in ('0', '1')],
                                  'run_ids': [members[k][0]['run_id'] for k in ('0', '1')]})
            planned = [r['id'] for r in rows]
            sets[module] = {'planned_ids': planned, 'observed_ids': valid_ids,
                            'missing_ids': [pid for pid in planned if pid not in valid_ids],
                            'complete_pair_block_ids': [p['block_id'] for p in pairs if p['module'] == module]}
            style_axis(ax, f_score=metric == 'F')
            ax.set_title(f'{module_label}\nn = {len(valid_ids)}/{len(rows)} Werte', pad=8)
            ax.set_xticks(list(block_indices.values()))
            ax.set_xlabel('Wiederholungsblock')
            ax.set_xlim(.6, max(block_indices.values(), default=1) + .4)
            if not valid_ids:
                ax.text(.5, .5, 'Keine gültigen Werte', transform=ax.transAxes, ha='center', fontsize=8)
        if metric != 'F':
            maximum = max((float(Fraction(p['value'])) for p in points), default=0)
            axes[0].set_ylim(-.04 * max(maximum, 1), max(maximum, 1) * 1.1)
            if metric in ('D', 'L'):
                axes[0].yaxis.set_major_locator(MaxNLocator(nbins=5, integer=True))
        axes[0].set_ylabel(ylabel)
        legend_context(fig)
        finish(fig, name, title,
               'Jeder Punkt ist ein Lauf. K0 (Kreis, blau) und K1 (Quadrat, orange) desselben '
               'Moduls und Blocks sind verbunden, sofern beide Werte vorliegen. n zählt gültige '
               'Einzelwerte / geplante Einzelwerte je Modul. Fehlende Werte sind keine Nullwerte.',
               questions=['UF1', 'UF2', 'UF6'] if metric != 'F' else ['UF1', 'UF2', 'UF6', 'H-K'],
               points=points, ids=[p['planned_id'] for p in points], observation_unit='planned_run',
               unit=unit, denominator_sets=sets, pairs=pairs)

    def module_differences():
        fig, ax = plt.subplots(figsize=(7.4, 4.1))
        points, sets = [], {}
        ordered = sorted(result['blocks'], key=lambda row: row['index'])
        for position, (module, label) in enumerate(MODULES.items()):
            module_points = []
            for index, block in enumerate(ordered):
                number = observed(block['module_differences'][module])
                if number is None:
                    continue
                offset = (index - (len(ordered) - 1) / 2) * min(.12, .48 / max(len(ordered) - 1, 1))
                x = position + offset
                common = block['block_id'] in result['B_C']
                ax.plot(x, float(number) * 100, marker='o', linestyle='none', markersize=5,
                        markerfacecolor='#333333' if common else 'white',
                        markeredgecolor='#333333', markeredgewidth=.9, zorder=3)
                ax.annotate(str(block['index']), (x, float(number) * 100),
                            xytext=(0, 6), textcoords='offset points',
                            ha='center', va='bottom', fontsize=7, color='#444444')
                module_points.append(pair_point(block, module, number, display_x=x))
            points.extend(module_points)
            present = [p['block_id'] for p in module_points]
            sets[module] = {'planned_block_ids': [b['block_id'] for b in ordered],
                            'complete_pair_block_ids': present,
                            'B_C_block_ids': [bid for bid in present if bid in result['B_C']],
                            'additional_pair_block_ids': [bid for bid in present if bid not in result['B_C']],
                            'incomplete_pair_block_ids': [b['block_id'] for b in ordered if b['block_id'] not in present]}
        style_axis(ax)
        ax.axhline(0, linewidth=.7, color='#888888', zorder=1)
        if not points:
            ax.text(.5, .5, 'Keine vollständigen Modulpaarungen', transform=ax.transAxes,
                    ha='center', fontsize=8)
        maximum = max((abs(float(Fraction(p['value']))) for p in points), default=0)
        ax.set_ylim(-max(maximum * 1.3, 5), max(maximum * 1.3, 5))
        ax.set_xticks(range(3), [f'{label}\nn = {len(sets[module]["complete_pair_block_ids"])}/{len(ordered)} Paare'
                                for module, label in MODULES.items()])
        ax.set_xlim(-.55, 2.55)
        ax.set_ylabel('F-Unterschied K1 − K0 (Prozentpunkte)')
        fig.legend(handles=[
            Line2D([], [], marker='o', markerfacecolor='#333333', markeredgecolor='#333333',
                   linestyle='none', markersize=5, label='Gemeinsamer Kernblock B_C'),
            Line2D([], [], marker='o', markerfacecolor='white', markeredgecolor='#333333',
                   linestyle='none', markersize=5, label='Zusätzliches vollständiges Modulpaar')],
            loc='upper right', bbox_to_anchor=(.98, .94), ncol=2, frameon=False, fontsize=7)
        finish(fig, 'modul-kontextunterschiede', 'Kontextunterschied je Modul und Wiederholungsblock',
               'Jeder Punkt zeigt d_mb = F(K1) − F(K0) aus zwei Läufen desselben Moduls und Blocks; '
               'die Zahl am Punkt ist die Blocknummer. Gefüllte Kreise gehören zum gemeinsamen B_C '
               'mit allen sechs gültigen Kernläufen. Offene Kreise zeigen zusätzliche vollständige '
               'Modulpaare außerhalb B_C. Der horizontale Versatz trennt Einzelwerte; er ist keine '
               'Messgröße. n zählt vollständige / geplante Modulpaare. Die Nullinie bedeutet gleiche '
               'F-Werte. Unterschiedliche Modulnenner sind beim Vergleich zu beachten; dargestellt '
               'werden beobachtete Differenzen, keine Konfidenzintervalle.',
               questions=['UF1', 'UF2', 'UF6', 'H-K'], points=points,
               ids=[f'{p["module"]}:{p["block_id"]}' for p in points],
               observation_unit='complete_module_pair', unit='percentage_points',
               denominator_sets=sets, common_B_C=result['B_C'])

    def repeatability():
        fig, ax = plt.subplots(figsize=(7.4, 4.1))
        keys = [f'C-{module}-{context}' for module in MODULES for context in ('0', '1')]
        points, summaries, sets = [], [], {}
        for position, key in enumerate(keys):
            rows = sorted((r for r in core_rows if r['cell_key'] == key),
                          key=lambda r: (r['block_index'], r['id']))
            valid = [(row, observed(row['functional']['F'])) for row in rows]
            valid = [(row, number) for row, number in valid if number is not None]
            context = 'K' + key[-1]
            values = sorted(number for _, number in valid)
            median = (values[len(values) // 2] if len(values) % 2 else
                      (values[len(values) // 2 - 1] + values[len(values) // 2]) / 2) if values else None
            # The summary sits beside the observations, so a zero range never covers a point.
            summary_x = position + .28
            if values:
                ax.plot([summary_x, summary_x], [float(values[0]), float(values[-1])],
                        color='#333333', linewidth=1, zorder=2)
                ax.plot([summary_x - .055, summary_x + .055], [float(median)] * 2,
                        color='#111111', linewidth=1.8, zorder=3)
            for row, number in valid:
                index = next(i for i, candidate in enumerate(rows) if candidate['id'] == row['id'])
                x = position - .06 + (index - (len(rows) - 1) / 2) * min(.085, .34 / max(len(rows) - 1, 1))
                ax.plot(x, float(number), marker=MARKERS[context], linestyle='none',
                        color=COLORS[context], markersize=5, markeredgewidth=.6,
                        markeredgecolor='#333333', zorder=3)
                points.append(_point(row, number, 'F', context=context, display_x=x))
            observed_ids = [row['id'] for row, _ in valid]
            sets[key] = {'planned_ids': [r['id'] for r in rows], 'observed_ids': observed_ids,
                         'missing_ids': [r['id'] for r in rows if r['id'] not in observed_ids]}
            summaries.append({'cell_key': key, 'n': len(values), 'planned_n': len(rows),
                              'median': str(median) if median is not None else None,
                              'minimum': str(values[0]) if values else None,
                              'maximum': str(values[-1]) if values else None,
                              'unit': 'ratio', 'planned_ids': observed_ids,
                              'display_x': summary_x})
        style_axis(ax, f_score=True)
        ax.set_xticks(range(len(keys)), [f'{key}\nn = {len(sets[key]["observed_ids"])}/{len(sets[key]["planned_ids"])}'
                                        for key in keys])
        ax.set_xlim(-.5, len(keys) - .5)
        ax.set_ylabel('Funktionaler Anforderungsscore F')
        for boundary in (1.5, 3.5):
            ax.axvline(boundary, color='#eeeeee', linewidth=.8, zorder=1)
        if not points:
            ax.text(.5, .5, 'Keine gültigen F-Werte', transform=ax.transAxes, ha='center', fontsize=8)
        fig.legend(handles=[
            Line2D([], [], marker=MARKERS[k], color=COLORS[k], linestyle='none', markersize=5, label=k)
            for k in ('K0', 'K1')] + [
            Line2D([], [], color='#111111', linewidth=1.8, label='Medianstrich'),
            Line2D([], [], color='#333333', marker='|', linestyle='none', markersize=11,
                   label='Spanne Minimum–Maximum')],
            loc='upper right', bbox_to_anchor=(.98, .94), ncol=4, frameon=False, fontsize=7)
        finish(fig, 'streuung-einzelwerte', 'Wiederholungsstreuung: alle funktionalen Einzelwerte',
               'Jeder Punkt ist ein gültiger F-Wert einer Kernkonfiguration; die Reihenfolge des '
               'kleinen horizontalen Versatzes folgt den Blocknummern und verändert keinen F-Wert. '
               'Rechts neben den Punkten stehen der Medianstrich und die beobachtete Spanne vom '
               'Minimum bis zum Maximum. Bei identischen Werten fällt die Spanne zusammen. n zählt '
               'gültige / geplante Läufe. Fehlende Werte werden nicht ersetzt. Median und Spanne '
               'beschreiben diese wenigen Wiederholungen; die Spanne ist kein Konfidenzintervall '
               'und belegt keine Streuung in einer größeren Grundgesamtheit.',
               questions=['UF6'], points=points, ids=[p['planned_id'] for p in points],
               observation_unit='planned_run', unit='ratio', denominator_sets=sets,
               summaries=summaries)

    def exploration(question, name, keys, labels, title):
        fig, ax = plt.subplots(figsize=(7.4, 4.1))
        info = result[question]
        selected = [r for r in result['cells'] if r['in_b_e'] and r['cell_key'] in keys]
        complete = set(info['complete_block_ids'])
        points, quartets, sets = [], [], {}
        markers = ('o', 's', '^', 'D', 'v', 'P', 'X')
        bids = sorted(info['planned_B_E'], key=block_indices.get)
        for block_no, bid in enumerate(bids):
            block = {r['cell_key']: r for r in selected if r['block_id'] == bid}
            offset = (block_no - (len(bids)-1)/2) * min(.07, .35/max(len(bids), 1))
            coordinates = []
            for i, key in enumerate(keys):
                row = block[key]
                number = observed(row['functional']['F'])
                if number is None:
                    continue
                x = i + offset
                ax.plot(x, float(number), linestyle='none', marker=markers[block_no % len(markers)],
                        markerfacecolor=COLORS['K0'] if bid in complete else 'white',
                        markeredgecolor=COLORS['K0'] if bid in complete else '#777777',
                        markersize=5, markeredgewidth=.8, zorder=3)
                points.append(_point(row, number, 'F', display_x=x,
                                     in_complete_quartet=bid in complete))
                coordinates.append((x, float(number)))
            if bid in complete:
                ax.plot(*zip(*coordinates), color='#999999', linewidth=.7, zorder=2)
                quartets.append({'block_id': bid, 'planned_ids': [block[k]['id'] for k in keys],
                                 'reference_id': block['C-SQL-1']['id']})
        for key in keys:
            rows = [r for r in selected if r['cell_key'] == key]
            valid = [p['planned_id'] for p in points if p['cell_key'] == key]
            sets[key] = {'planned_ids': [r['id'] for r in rows], 'observed_ids': valid,
                         'missing_ids': [r['id'] for r in rows if r['id'] not in valid]}
        ax.set_xticks(range(len(keys)), [label + f'\nn = {len(sets[key]["observed_ids"])}/{len(sets[key]["planned_ids"])}'
                                        for key, label in zip(keys, labels)])
        ax.set_xlim(-.4, len(keys)-.6)
        ax.set_ylabel('Funktionaler Anforderungsscore F')
        style_axis(ax, f_score=True)
        label_n = 'n_EM' if question == 'UF3' else 'n_EP'
        ax.set_title(f'Vollständige F-Quartette: {label_n} = {len(complete)}/{len(bids)} geplante B_E-Blöcke',
                     loc='left', fontsize=9, pad=10)
        handles = [Line2D([], [], marker=markers[i % len(markers)], color='#555555',
                          linestyle='none', markersize=4, label=f'Block {block_indices[bid]}')
                   for i, bid in enumerate(bids)]
        if handles:
            fig.legend(handles=handles, loc='upper right', bbox_to_anchor=(.98, .94),
                       ncol=min(len(handles), 5), frameon=False)
        finish(fig, name, title,
               'Alle gültigen F-Einzelwerte aus den geplanten B_E-Blöcken. Nur vollständige '
               'Quartette sind durch Linien verbunden (gefüllte Punkte); beobachtete Werte aus '
               'unvollständigen Quartetten sind hohl und bleiben unverbunden. Die Form bezeichnet '
               'den Block. C-SQL-1 ist die gemeinsam verwendete Referenz; sie ist kein zusätzlicher '
               'unabhängiger Kontrolllauf. Zell-n zählt gültige / geplante Einzelwerte.',
               questions=[question], points=points, ids=[p['planned_id'] for p in points],
               observation_unit='planned_run', unit='ratio', denominator_sets={
                   'cells': sets, 'planned_B_E': bids, 'complete_quartet_block_ids': info['complete_block_ids']},
               quartets=quartets, shared_reference_cell='C-SQL-1', n_complete_quartets=len(complete))

    def blocks():
        fig, axes = plt.subplots(1, 2, figsize=(7.4, 3.9), sharey=True)
        points = []
        for module, marker, color, offset in zip(MODULES, ('o', 's', '^'),
                                                ('#0072B2', '#D55E00', '#555555'), (-.14, 0, .14)):
            xs, ys = [], []
            for row in result['blocks']:
                number = observed(row['module_differences'][module])
                xs.append(row['index'] + offset); ys.append(float(number)*100 if number is not None else float('nan'))
                if number is not None:
                    points.append(pair_point(row, module, number, panel='module',
                                             display_x=row['index'] + offset))
            axes[0].plot(xs, ys, marker=marker, color=color, linewidth=.8, markersize=4, label=MODULES[module])
        g_points = []
        for row in result['blocks']:
            number = observed(row['g'])
            if number is not None:
                g_points.append({'block_id': row['block_id'], 'block_index': row['index'],
                                 'panel': 'core', 'metric': 'g_b', 'value': str(number*100),
                                 'planned_ids': [r['id'] for r in core_rows if r['block_id'] == row['block_id']],
                                 'run_ids': [r['run_id'] for r in core_rows if r['block_id'] == row['block_id']]})
                axes[1].plot(row['index'], float(number)*100, marker='D', markersize=5, color='#222222')
        mean = observed(result['core']['mean'])
        if mean is not None:
            axes[1].axhline(float(mean)*100, color='#0072B2', linestyle='--', linewidth=1,
                           label='Mittelwert Δ_C = ' + _number(mean*100) + ' pp')
            axes[1].legend(loc='best', frameon=False)
        for ax in axes:
            style_axis(ax)
            ax.axhline(0, color='#888888', linewidth=.7)
            ax.set_xticks(list(block_indices.values()))
            ax.set_xlim(.6, max(block_indices.values(), default=1)+.4)
            ax.set_xlabel('Wiederholungsblock')
        if not points:
            axes[0].text(.5, .5, 'Keine vollständigen Modulpaarungen',
                         transform=axes[0].transAxes, ha='center', fontsize=8)
        if not g_points:
            axes[1].text(.5, .5, 'Kein vollständiger Kernblock',
                         transform=axes[1].transAxes, ha='center', fontsize=8)
        axes[0].set_title('a  Modulpaarungen: d_mb')
        axes[1].set_title(f'b  Vollständige Kernblöcke: n_C = {result["n_C"]}/{result["r_C"]}')
        axes[0].set_ylabel('F-Unterschied K1 − K0 (Prozentpunkte)')
        axes[0].legend(loc='best', frameon=False, fontsize=7)
        sets = {'module_pairs': {m: result['module_pairs'][m]['ids'] for m in MODULES},
                'B_C': result['B_C'], 'planned_block_ids': list(block_indices)}
        finish(fig, 'blockunterschiede', 'Kontextunterschiede nach Wiederholungsblock',
               'Links: alle messbaren Modulpaarungen, mit Unterbrechung bei fehlenden Paaren. '
               'Ein kleiner horizontaler Versatz trennt die drei Module desselben Blocks. '
               'Rechts: g_b als gleich gewichteter Mittelwert der drei Modulunterschiede; '
               'Δ_C verwendet ausschließlich vollständige Sechserblöcke B_C. Die Panels besitzen '
               'unterschiedliche Nenner. Die Blockreihenfolge folgt der Erhebung.',
               questions=['UF1', 'UF2', 'UF6', 'H-K'], points=points+g_points,
               ids=result['B_C'], observation_unit='complete_core_block_for_primary_contrast',
               unit='percentage_points', denominator_sets=sets,
               primary_mean=str(mean*100) if mean is not None else None)

    def sensitivity():
        fig, axes = plt.subplots(1, 2, figsize=(7.4, 3.8), gridspec_kw={'width_ratios': [1.25, 1]})
        loo = result['core']['leave_one_out']
        points = []
        for bid, item in zip(loo['omitted_block_ids'], loo['values']):
            number = observed(item)
            if number is not None:
                axes[0].plot(block_indices[bid], float(number)*100, marker='o', color='#0072B2', markersize=5)
                points.append({'omitted_block_id': bid, 'omitted_block_index': block_indices[bid],
                               'metric': 'leave_one_block_out', 'value': str(number*100),
                               'retained_block_ids': [other for other in result['B_C'] if other != bid]})
        mean = observed(result['core']['mean'])
        if mean is not None:
            axes[0].axhline(float(mean)*100, color='#555555', linewidth=.8, linestyle='--', label='Δ_C mit allen B_C-Blöcken')
            axes[0].legend(loc='best', frameon=False, fontsize=7)
        if not points:
            axes[0].text(.5, .5, 'Nicht definiert: n_C < 2', transform=axes[0].transAxes, ha='center', fontsize=8)
        axes[0].set_xticks(list(block_indices.values()))
        axes[0].set_xlim(.6, max(block_indices.values(), default=1)+.4)
        axes[0].set_xlabel('Weggelassener Block')
        axes[0].set_ylabel('Mittlerer F-Unterschied (Prozentpunkte)')
        axes[0].set_title('a  Einen B_C-Block weglassen')
        bounds = result['missing_bounds']
        low, high = (observed(bounds[key]) for key in ('lower', 'upper'))
        if low is not None and high is not None:
            axes[1].plot([0, 0], [float(low)*100, float(high)*100], color='#D55E00', linewidth=2)
            axes[1].plot([0, 0], [float(low)*100, float(high)*100], linestyle='none',
                         marker='_', markersize=16, color='#D55E00', markeredgewidth=1.5)
            for value_, label, align in ((high, 'Obergrenze', 'bottom'), (low, 'Untergrenze', 'top')):
                axes[1].annotate(label + ': ' + f'{float(value_*100):.2f}'.rstrip('0').rstrip('.').replace('.', ',') + ' pp',
                                 (0, float(value_)*100), xytext=(12, 4 if align == 'bottom' else -4),
                                 textcoords='offset points', va=align, fontsize=8)
        axes[1].set_title('b  Grenzen für alle geplanten Blöcke')
        axes[1].set_xlim(-.35, .8)
        axes[1].set_xticks([0], [f'Geplante Kernblöcke: r_C = {result["r_C"]}'])
        for ax in axes:
            style_axis(ax)
            ax.axhline(0, color='#aaaaaa', linewidth=.6)
        # Common scale keeps the two kinds of sensitivity visually comparable.
        limits = [ax.get_ylim() for ax in axes]
        bottom, top = min(a for a, _ in limits), max(b for _, b in limits)
        for ax in axes:
            ax.set_ylim(bottom, top)
        finish(fig, 'sensitivitaet', 'Sensitivität des primären Kontextvergleichs',
               'Links: Δ_C nach Weglassen jeweils eines vollständigen Blocks; jedes Mittel '
               'verwendet n_C − 1 Blöcke. Rechts: konservative Grenzen für alle geplanten '
               'Kernblöcke, indem fehlende F-Werte innerhalb [0, 1] variieren. Ohne Fehlwerte '
               'fallen beide Grenzen zusammen. Weder diese Grenzen noch die Weglasswerte '
               'sind Konfidenzintervalle; die Grenzen ersetzen keine beobachtete Schätzung.',
               questions=['UF2', 'UF6', 'H-K'], points=points,
               ids=loo['omitted_block_ids'], observation_unit='omitted_complete_core_block',
               unit='percentage_points', denominator_sets={'B_C': result['B_C'],
                   'planned_core_ids': bounds['planned_core_ids'], 'bounds_pair_denominator': bounds['denominator']},
               lower_bound=str(low*100) if low is not None else None,
               upper_bound=str(high*100) if high is not None else None,
               primary_mean=str(mean*100) if mean is not None else None)

    def resource(metric, name, xlabel, unit, currency=None):
        from .analysis_metrics import resource_value
        from matplotlib.transforms import ScaledTranslation
        fig, axes = plt.subplots(1, 3, figsize=(7.4, 3.8), sharey=True, sharex=True)
        points, sets = [], {}
        for ax, (module, label) in zip(axes, MODULES.items()):
            rows = [r for r in core_rows if r['cell_key'].startswith('C-' + module + '-')]
            coordinates = []
            for row in sorted(rows, key=lambda r: (r['block_index'], r['cell_key'], r['id'])):
                f = observed(row['functional']['F'])
                resources = row['resources']
                if metric == 'cost':
                    cost = resources.get('costs')
                    x = Fraction(cost['amount']) if cost and cost['status'] == 'known' and cost['currency'] == currency else None
                elif metric == 'tokens.total':
                    x = resource_value(row, metric)
                else:
                    x = observed(resources.get(metric))
                if f is None or x is None:
                    continue
                coordinates.append((row, x, f))
            ties = {}
            for row, x, f in coordinates:
                ties.setdefault((x, f), []).append(row['id'])
            for row, x, f in coordinates:
                context = 'K' + row['cell_key'][-1]
                group = ties[(x, f)]
                offset = (group.index(row['id']) - (len(group) - 1) / 2) * 6.5
                transform = ax.transData + ScaledTranslation(offset / 72, 0, fig.dpi_scale_trans)
                if offset:
                    ax.annotate('', xy=(float(x), float(f)), xytext=(offset, 0),
                                textcoords='offset points',
                                arrowprops={'arrowstyle': '-', 'linewidth': .5, 'color': '#888888',
                                            'shrinkA': 0, 'shrinkB': 0}, zorder=2)
                if len(group) > 1 and row['id'] == group[0]:
                    ax.plot(float(x), float(f), marker='|', markersize=9, markeredgewidth=.6,
                            color='#444444', linestyle='none', zorder=2)
                ax.scatter(float(x), float(f), color=COLORS[context], marker=MARKERS[context],
                           s=25, linewidths=.7, edgecolors='#333333', alpha=.9, zorder=3,
                           transform=transform)
                points.append(_point(row, f, 'F', x_metric=metric, x_value=str(x),
                                     x_unit=unit, context=context, currency=currency,
                                     coincident_planned_ids=group, display_offset_points=offset,
                                     display_offset_axis='horizontal_screen_points_only',
                                     leader_to_exact_position=len(group) > 1))
            valid = [p['planned_id'] for p in points if p['cell_key'].startswith('C-' + module + '-')]
            sets[module] = {'planned_ids': [r['id'] for r in rows], 'complete_F_resource_ids': valid,
                            'excluded_incomplete_pair_ids': [r['id'] for r in rows if r['id'] not in valid]}
            ax.set_title(f'{label}\nn = {len(valid)}/{len(rows)} Wertepaare', pad=8)
            style_axis(ax, f_score=True)
            ax.set_xlabel(xlabel)
            if not valid:
                ax.text(.5, .5, 'Keine vollständigen\nWertepaare', transform=ax.transAxes,
                        ha='center', fontsize=8)
        coordinates = [float(Fraction(p['x_value'])) for p in points]
        minimum, maximum = min(coordinates, default=0), max(coordinates, default=1)
        span = maximum - minimum or max(abs(maximum)*.1, .001)
        axes[0].set_xlim(max(0, minimum-span*.12), maximum+span*.12)
        scale = 1000 if metric == 'tokens.total' and maximum >= 10000 else 1
        for ax in axes:
            _resource_axis(ax, integer=metric == 'tokens.total', scale=scale)
            if scale != 1:
                ax.set_xlabel(xlabel + '\n(in Tausend)')
        axes[0].set_ylabel('Funktionaler Anforderungsscore F')
        legend_context(fig)
        finish(fig, name, 'Funktionales Ergebnis und ' + {'cost': 'API-Kosten',
               'pipeline_seconds': 'aktive Pipelinezeit', 'tokens.total': 'Tokenverbrauch'}[metric],
               'Ein Punkt verbindet F und den vollständig erfassten Ressourcenwert desselben '
               'Kernlaufs. n zählt gültige Wertepaare / geplante Kernläufe je Modul. Unvollständige '
               'Zeit-, Token- oder Kostenwerte werden nicht durch Teilsummen ersetzt. Die Achsen '
               'sind innerhalb der Abbildung gleich skaliert. Nur exakt deckungsgleiche '
               'Wertepaare werden zur Sichtbarkeit um wenige Bildpunkte horizontal versetzt; '
               'dünne Linien führen zur mit einem senkrechten Strich markierten Originalposition. '
               'Dieser Versatz zeigt keine zusätzliche Ressourcenstreuung. Die exakten '
               'Ressourcenwerte bleiben in den Diagrammdaten erhalten. '
               'Es werden keine Trends oder Zusammenhänge geschätzt.',
               questions=['UF5'], points=points, ids=[p['planned_id'] for p in points],
               observation_unit='planned_run_with_complete_F_and_resource', unit=unit+' / ratio',
               denominator_sets=sets, currency=currency)

    with mpl.rc_context(STYLE):
        paired('F', 'kontext-verteilungen', 'Kontextvergleich: funktionale Einzelwerte und Blockpaarung',
               'Funktionaler Anforderungsscore F', 'ratio')
        module_differences()
        blocks()
        repeatability()
        sensitivity()
        for metric, label, unit in (('D', 'PHPStan-Diagnosen D', 'diagnoses'),
                                    ('L', 'PHP-Codezeilen L', 'lines'),
                                    ('S', 'Diagnosen je 100 PHP-Zeilen S', 'diagnoses_per_100_lines')):
            paired(metric, 'statisch-'+metric, 'Statische Codeanalyse: '+label, label, unit)
        exploration('UF3', 'modellzuordnung', ['C-SQL-1', 'E-AB', 'E-BA', 'E-BB'],
                    ['P=A / V=A\nReferenz', 'P=A / V=B', 'P=B / V=A', 'P=B / V=B'],
                    'Modellzuordnung: SQL Injection, K1, vollständige Pipeline')
        exploration('UF4', 'pipeline-varianten', ['E-P0R0', 'E-P0R1', 'E-P1R0', 'C-SQL-1'],
                    ['Planner aus\nReview aus', 'Planner aus\nReview an',
                     'Planner an\nReview aus', 'Planner an\nReview an · Referenz'],
                    'Pipelinekomponenten: SQL Injection, K1, Modellzuordnung A/A')
        resource('pipeline_seconds', 'F-zeit', 'Aktive Pipelinezeit (s)', 's')
        resource('tokens.total', 'F-tokens', 'Tokens gesamt', 'tokens')
        currencies = sorted({r['resources']['costs']['currency'] for r in core_rows
                             if r['resources'].get('costs') and r['resources']['costs']['currency']})
        for index, currency in enumerate(currencies or [None]):
            resource('cost', 'F-kosten-'+str(index+1), 'API-Kosten ('+(currency or 'nicht erhoben')+')',
                     currency or 'unknown_currency', currency)
    return output

"""CSV/JSON/plot bytes from the same M7 result; no new scientific decisions."""
import csv
from io import BytesIO, StringIO
import json

from .analysis import VERSION, observed
from .domain import canonical


def flatten(value, prefix=''):
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            output.update(flatten(item, f'{prefix}.{key}' if prefix else key))
        return output
    if isinstance(value, list):
        return {prefix: canonical(value)}
    return {prefix: value}


def csv_bytes(rows, *, result, unit, denominators):
    data = [flatten({**row, 'data_hash': result['data_hash'], 'analysis_version': result['analysis_version'],
                       'table_unit': unit, 'denominator_sets': denominators}) for row in rows]
    if not data:
        data = [flatten({'data_hash': result['data_hash'], 'analysis_version': result['analysis_version'],
                         'table_unit': unit, 'denominator_sets': denominators, 'n': 0})]
    # Equivalent JSON dictionaries may arrive in insertion or canonical order.
    # CSV row order must survive package round trips; keep runs in matrix order.
    data.sort(key=lambda row: (row.get('position') or 0, row.get('block_index') or row.get('index') or 0,
                               row.get('step_index') or 0, canonical(row)))
    fields = sorted({key for row in data for key in row})
    target = StringIO(newline='')
    writer = csv.DictWriter(target, fieldnames=fields, lineterminator='\n')
    writer.writeheader();writer.writerows(data)
    return target.getvalue().encode('utf-8')


def plot_data(result):
    points = []
    for row in result['cells']:
        f = observed(row['functional']['F'])
        timing = observed(row['resources'].get('pipeline_seconds'))
        cost = row['resources'].get('costs')
        points.append({'planned_id': row['id'], 'run_id': row['run_id'], 'candidate_hash': row['candidate_hash'],
            'cell': row['cell_key'], 'F': str(f) if f is not None else None,
            'pipeline_seconds': str(timing) if timing is not None else None,
            'cost': cost['amount'] if cost and cost['status']=='known' else None,
            'currency': cost['currency'] if cost else None})
    return {'data_hash': result['data_hash'], 'analysis_version': VERSION, 'points': points,
        'core': result['core'], 'F_unit': 'ratio', 'time_unit': 's',
        'planned_ids': [r['id'] for r in result['cells']], 'B_C': result['B_C'],
        'F_valid_ids': [p['planned_id'] for p in points if p['F'] is not None],
        'F_time_ids': [p['planned_id'] for p in points if p['F'] is not None and p['pipeline_seconds'] is not None],
        'F_cost_ids': {currency: [p['planned_id'] for p in points if p['F'] is not None and p['cost'] is not None and p['currency']==currency]
                       for currency in sorted({p['currency'] for p in points if p['currency']})}}


def cell_summary(row):
    """One compact matrix row; raw logs and file inventories stay in JSON.

    Repeating large vendor exclusion lists in a CSV field obscures rather than
    exposes observations. Dedicated calls/cases/reviews/static tables retain the
    relevant detail and IDs; analysis.json remains lossless.
    """
    from .analysis_metrics import COUNTS
    resources = row['resources']
    return {**{key: row.get(key) for key in ('id', 'run_id', 'cell_key', 'block_id', 'block_index',
        'position', 'in_b_e', 'candidate_hash', 'configuration_id', 'configuration_hash', 'state', 'missing')},
        'functional': {key: row['functional'][key] for key in ('F', 'T', 'full_success', 'R', 'T_criteria')},
        'static': {key: row['static'][key] for key in ('D', 'L', 'S', 'analysis_complete')},
        'resources': {**{key: item for key, item in resources.items() if key.endswith('_seconds') or key in COUNTS},
            'tokens': resources.get('tokens', {}).get('summary', {}),
            'costs': {key: (resources.get('costs') or {}).get(key) for key in ('amount', 'known_subtotal', 'status', 'currency')}}}


def render(result):
    """Stable bytes; UTC generation/confirmation time belongs to a separate receipt."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from fractions import Fraction
    plots = plot_data(result)
    tables = {
        'cells.csv': ([cell_summary(row) for row in result['cells']], 'mixed: explicit per observation', {'planned_ids': plots['planned_ids']}),
        'blocks.csv': (result['blocks'], 'ratio_difference', {'B_C': result['B_C']}),
        'contrasts.csv': ([{'comparison': 'core', **result['core']}] +
            [{'comparison': 'module_B_C:'+key, **item} for key,item in result['module_on_B_C'].items()] +
            [{'comparison': 'module_pairs:'+key, **item} for key,item in result['module_pairs'].items()] +
            [{'comparison': question+':'+key, **item} for question in ('UF3','UF4') for key,item in result[question]['contrasts'].items()],
            'ratio_difference / percentage_points', {'B_C': result['B_C'], 'B_EM': result['UF3']['complete_block_ids'], 'B_EP': result['UF4']['complete_block_ids']}),
        'metric-contrasts.csv': ([{'metric': metric, **item} for metric,item in result['metric_comparisons'].items()], 'explicit per metric', {metric:item['valid_planned_ids'] for metric,item in result['metric_comparisons'].items()}),
        'criterion-pairs.csv': ([{'module': module, 'criterion': criterion, **item} for module,profiles in result['criterion_pairs'].items() for criterion,item in profiles.items()], 'binary_difference', {module:{key:item['ids'] for key,item in profiles.items()} for module,profiles in result['criterion_pairs'].items()}),
        'profiles.csv': ([{'cell': cell, **item} for cell,item in result['configurations'].items()], 'mixed: explicit per metric', {'planned_ids': plots['planned_ids']}),
        'resources.csv': ([{'area': area, **item} for area,item in result['resource_areas'].items()], 'currency / tokens / s per observation', {key:item['run_ids'] for key,item in result['resource_areas'].items()}),
        'errors.csv': (result['errors'], 'evidence observations; interpretation separate', {'planned_ids': plots['planned_ids']}),
    }
    from .analysis_tables import tables as relational_tables
    for name, rows in relational_tables(result).items():
        tables[name+'.csv'] = (rows, 'explicit per metric; exact fractions and decimal display',
                               {'planned_ids': plots['planned_ids']})
    from .analysis_report_tables import tables as report_tables
    for name, rows in report_tables(result).items():
        tables[name+'.csv'] = (rows, 'explicit in unit columns; exact fractions retained',
                               {'planned_ids': plots['planned_ids']})
    files = {'analysis.json': (canonical(result).encode(), 'application/json'),
        'plot-data.json': (canonical(plots).encode(), 'application/json')}
    manifests = {}
    for name, (rows, unit, ids) in tables.items():
        files[name] = (csv_bytes(rows, result=result, unit=unit, denominators=ids), 'text/csv; charset=utf-8')
        manifests[name] = {'data_hash': result['data_hash'], 'analysis_version': VERSION,
                           'unit': unit, 'denominator_sets': ids, 'row_count': len(rows)}
    from .analysis_charts import figures, STYLE
    from .analysis_population import figure_population, population_rows
    with matplotlib.rc_context({**STYLE, 'svg.hashsalt':result['data_hash']}):
        for name,fig,info in figures(result):
            population = figure_population(result, name, info)
            info = {**info, 'population': population}
            source_rows = population_rows(population)
            source_name = population['csv_filename']
            source_unit = 'planned_run_per_population_group'
            source_sets = {group['key']: {key: group[key] for key in
                ('planned_ids', 'used_planned_ids', 'omitted_planned_ids', 'bounded_missing_planned_ids')}
                for group in population['groups']}
            files[source_name] = (csv_bytes(source_rows, result=result,
                unit=source_unit, denominators=source_sets), 'text/csv; charset=utf-8')
            manifests[source_name] = {'data_hash': result['data_hash'],
                'analysis_version': result['analysis_version'], 'unit': source_unit,
                'denominator_sets': source_sets, 'row_count': len(source_rows),
                'population': population}
            figure_data = info.get('figure_data')
            if figure_data:
                data_name = name+'-data.json'
                files[data_name] = (canonical({'data_hash': result['data_hash'],
                    'analysis_version': result['analysis_version'], **info}).encode(), 'application/json')
                csv_name = name+'-data.csv'
                denominators = info.get('denominator_sets', info.get('denominator_ids', {}))
                files[csv_name] = (csv_bytes(figure_data['points'], result=result,
                    unit=info.get('unit', 'explicit per point'), denominators=denominators),
                    'text/csv; charset=utf-8')
                for filename in (data_name, csv_name):
                    manifests[filename] = {'data_hash': result['data_hash'],
                        'analysis_version': result['analysis_version'],
                        'row_count': len(figure_data['points']), **info}
            for extension in ('svg','png','pdf'):
                target=BytesIO();metadata=({'Date':None} if extension=='svg' else
                    {'CreationDate':None,'ModDate':None,'Creator':VERSION} if extension=='pdf' else {'Software':VERSION})
                fig.savefig(target,format=extension,metadata=metadata,dpi=300)
                filename=name+'.'+extension
                files[filename]=(target.getvalue(),{'svg':'image/svg+xml','png':'image/png','pdf':'application/pdf'}[extension])
                manifests[filename]={'data_hash':result['data_hash'],'analysis_version':VERSION,**info}
            plt.close(fig)
    import hashlib
    for name,(data,mime) in files.items():
        manifests.setdefault(name, {'data_hash': result['data_hash'], 'analysis_version': VERSION,
            'unit': 'mixed: each metric explicit', 'denominator_sets': {'planned_ids': plots['planned_ids'], 'B_C': result['B_C']}})
        manifests[name].update(sha256=hashlib.sha256(data).hexdigest(), byte_count=len(data), mime_type=mime)
    manifest = {'data_hash': result['data_hash'], 'analysis_version': VERSION, 'source_module': 'research_env.analysis_export',
        'volatile_generation_timestamp': 'Recorded separately in analysis acknowledgement', 'files': manifests}
    files['manifest.json'] = (canonical(manifest).encode(), 'application/json')
    return files

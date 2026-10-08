"""Read-only reporting views of an immutable result and its selected inputs."""
from .analysis import observed, describe
from .analysis_metrics import resource_value
from .domain import MAIN_CELLS

MODULES = {'BF': 'Brute Force', 'SQL': 'SQL Injection', 'UP': 'File Upload'}
COMPARISONS = {
 'AB-AA': 'Verifikation B − A bei Produzent A', 'BA-BB': 'Verifikation A − B bei Produzent B',
 'BA-AA': 'Produzent B − A bei Verifikation A', 'BB-AB': 'Produzent B − A bei Verifikation B',
 'cross_minus_self': 'Cross − Selbst (ergänzendes Mittel)',
 'Planner11-01': 'Planner an − aus, Review an', 'Planner10-00': 'Planner an − aus, Review aus',
 'Review11-10': 'Review an − aus, Planner an', 'Review01-00': 'Review an − aus, Planner aus',
 'Interaction': '(P1R1 − P0R1) − (P1R0 − P0R0)',
}
TABLE_LABELS = {
 'configurations': 'Vollständige Konfigurationsmatrix',
 'runs': 'Einzelwerte aller geplanten Läufe', 'summaries': 'Alle Lage- und Streuungsmaße',
 'coverage': 'Datenabdeckung und Fehlwertursachen', 'static-coverage': 'Statische Auswertbarkeit',
 'exploratory-profiles': 'Zellprofile der Zusatzvergleiche', 'contrast-values': 'Einzelne Kontrastwerte je Block',
 'denominator-members': 'Verwendete Lauf- und Blockmengen', 'report-sensitivity': 'Weglasswerte und Fehlwertgrenzen',
 'calls': 'Modellaufrufe mit Rollen und Herkunft', 'tokens': 'Tokenarten je Aufrufversuch',
 'intervals': 'Gemessene Zeitintervalle', 'cases': 'Unabhängige Testfälle', 'assertions': 'Einzelne Testaussagen',
 'reviews': 'Ausgewählte Bewertungen T1–T5', 'static-files': 'Analysierte PHP-Dateien',
 'blocks': 'Kontextunterschiede je Kernblock', 'contrasts': 'Funktionale Kontraste',
 'metric-contrasts': 'Kontextunterschiede aller Messgrößen', 'criterion-pairs': 'R-/T-Unterschiede je Modul',
 'profiles': 'Vollständige Konfigurationsprofile', 'resources': 'Studienaufwand nach Bereich',
 'errors': 'Belegte Fehler und offene Befunde', 'sensitivity': 'Sensitivität (Originalformat)',
 'cells': 'Vollständige geplante Matrix',
}


def metric_value(row, metric):
    if metric == 'F': return observed(row['functional']['F'])
    if metric in ('D','L','S'): return observed(row['static'][metric])
    if metric.endswith('_seconds'): return observed(row['resources'].get(metric))
    return resource_value(row, metric)


def report_context(result, *, snapshot=None, register=None, snapshot_id=None, output_base='', manifest=None, cache_key=None):
    from .analysis_proposal_ui import proposal_resources
    from .analysis_report_tables import tables
    added = tables(result)
    from .analysis_table_population import table_populations
    table_population = table_populations(result, added)
    indices = {b['block_id']: b['index'] for b in result['blocks']}
    def block_numbers(ids):
        return ', '.join(str(indices.get(identity, identity)) for identity in ids) or 'Keine'
    configs=[]
    for key, cell in MAIN_CELLS.items():
        item=result['configurations'][key]
        profiles={**item['profiles'], **result['configuration_endpoints'][key]}
        configs.append({'key':key,'cell':cell,'module':MODULES[cell.module], 'planned':item['planned_n'],
            'profiles':profiles,'distinct_f':item['distinct_F_n'], 'distinct_code':item['distinct_candidate_hash_n'],
            'candidates':item['candidate_n']})
    metrics=[]
    for key,item in result['metric_comparisons'].items():
        stats=item['complete_block_differences']
        metrics.append({'key':key,'unit':item['unit'],'stats':stats,'blocks':block_numbers(stats['ids']),
                        'pairs':[{'module_key':m,'module':MODULES[m],'stats':s,'blocks':block_numbers(s['ids'])} for m,s in item['module_pairs'].items()]})
    contrasts={}
    for q in ('UF3','UF4'):
        contrasts[q]=[{'key':k,'label':COMPARISONS.get(k,k),'stats':s,'blocks':block_numbers(s['ids'])}
                      for k in COMPARISONS if k in result[q]['contrasts'] for s in [result[q]['contrasts'][k]]]
    profiles={}
    for q,keys in [('UF3',['C-SQL-1','E-AB','E-BA','E-BB']),('UF4',['C-SQL-1','E-P0R1','E-P1R0','E-P0R0'])]:
        profiles[q]=[]
        for key in keys:
            rows=[r for r in result['cells'] if r['cell_key']==key and r['in_b_e']]
            cells={}
            for metric in ('F','D','L','S','pipeline_seconds','tokens.total','call_count','repair_count'):
                vals=[(r,metric_value(r,metric)) for r in rows]
                valid=[(r,v) for r,v in vals if v is not None]
                cells[metric]=describe([v for _,v in valid], unit={'F':'ratio','D':'diagnoses','L':'lines','S':'diagnoses_per_100_lines','pipeline_seconds':'s','tokens.total':'tokens'}.get(metric,'count'), ids=[r['id'] for r,_ in valid])
            profiles[q].append({'key':key,'planned':len(rows),'metrics':cells})
    metadata=(manifest or {}).get('files',{})
    def file_url(name, inline=False):
        query=[]
        if inline: query.append('inline=true')
        if cache_key: query.append('v='+cache_key)
        return output_base+name+('?'+'&'.join(query) if query else '')
    downloads=[{'name':name,'label':TABLE_LABELS.get(name.removesuffix('.csv'),name),
                'url':file_url(name), 'bytes':info.get('byte_count',0)}
               for name,info in metadata.items() if name.endswith('.csv') and not name.endswith(('-data.csv', '-population.csv'))]
    plots={}
    for name,info in metadata.items():
        if not name.endswith('.png'):continue
        stem=name[:-4]
        plots[stem]={'label':stem,'inline':file_url(name,True),
            'png':file_url(name),'svg':file_url(stem+'.svg'),'pdf':file_url(stem+'.pdf'),
            'csv':file_url(stem+'-data.csv') if stem+'-data.csv' in metadata else None,
            'population':info.get('population'),
            'population_csv':file_url(info['population']['csv_filename']) if (info.get('population') or {}).get('csv_filename') else None,
            'json':file_url(stem+'-data.json') if stem+'-data.json' in metadata else None,
            'caption':info.get('caption',''),'questions':info.get('research_questions',[])}
    report={'modules':MODULES,'configs':configs,'metric_rows':metrics,'contrasts':contrasts,
            'exploratory_profiles':profiles,'extra':added,'block_labels':indices,'core_blocks':block_numbers(result['B_C']),
            'table_population':table_population, 'plots':plots,'downloads':downloads,'output_base':output_base,'cache_key':cache_key,
            'started':sum(bool(r['run_id']) for r in result['cells']),
            'f_count':sum(observed(r['functional']['F']) is not None for r in result['cells']),
            'unmeasured_metrics':[m for m in metrics if m['stats']['n']==0],
            'measured_metrics':[m for m in metrics if m['stats']['n']>0],
            'run_count':len(result['cells'])}
    context={'report':report}
    if snapshot is not None:
        context['resource_view']=proposal_resources(snapshot,register=register,snapshot_id=snapshot_id)
    return context

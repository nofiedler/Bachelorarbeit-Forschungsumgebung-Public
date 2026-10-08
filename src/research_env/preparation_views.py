"""Read persisted research state. No graph, live job, provider or hidden evaluator reads."""
import json
from uuid import UUID

from .domain import (Approval, Artifact, AssetVersion, ConfigurationVersion, Freeze, ModelPackage,
    Block, PlannedRun, Run, Series, Study, StudyPhase, TimeInterval, canonical, digest)
from .preparation import diagnostic, latest_versions, versions_hash, software_identity, software_compatible, run_records
from .register import GateError
from .timing import summarize
from .evidence import catalog
from .catalog_ui import available, hidden


def overview(register):
    phases=register.all(StudyPhase)
    free_studies={p.study_id for p in phases if p.purpose=='free_test'} | {s.id for s in register.all(Study) if s.code.startswith('SYSTEM-CONTROL-')}
    from .study_restore import rows
    imported=register.connection.execute("SELECT value FROM runtime_metadata WHERE key='imported_study_id'").fetchone()
    return {'studies':[s for s in available(register,Study) if s.id not in free_studies and (not imported or str(s.id)==imported[0])],
        'restored_copies':[] if imported else rows(register),
        'phases':[p for p in phases if p.study_id not in free_studies],
        'commands':[dict(x) for x in register.connection.execute('SELECT * FROM ui_command ORDER BY rowid DESC LIMIT 20')]}



def configuration(register, version_id, *, historical=False):
    version=register.get(version_id,ConfigurationVersion)
    phase=register.get(register._phase(version),StudyPhase)
    resolved={}
    problems=[]
    for role in ('analyzer','planner','migrate','test','review','repair'):
        if role=='planner' and not version.cell.planner or role=='review' and not version.cell.review:continue
        try:
            model=register.resolve_call_model(version,role)
            resolved[role]={'model':model.model_dump(mode='json'),'parameters':register.resolve_call_parameters(version,role)}
        except ValueError as exc:problems.append(str(exc))
    try:
        from .context_assets import package_path
        from .role_formats import CONTRACT_HASH, PROMPTS_HASH
        package_path(register,version,strict=True)
        for key,expected in (('handoff_id',CONTRACT_HASH),):
            ref=getattr(version.settings,key)
            if not ref or register.get(ref,AssetVersion).manifest_hash!=expected:problems.append('Übergabeversion ist nicht mit roles-v1 kompatibel')
        if len(version.settings.prompt_ids)!=1 or register.get(version.settings.prompt_ids[0],AssetVersion).manifest_hash!=PROMPTS_HASH:
            problems.append('Promptversion ist nicht mit dieser Pipeline kompatibel')
    except ValueError as exc:problems.append(str(exc))
    outdated = not software_compatible(version.settings.software_commit)
    if outdated and not historical:problems.append('Die Anwendung wurde seit dem Speichern aktualisiert. Übernimm diese Bedingungen in eine neue Konfiguration, bevor du erneut startest.')
    if historical: problems = []
    from .application_settings import preferences
    return {'version':version,'phase':phase,'title':register.get(phase.study_id,Study).title,
        'outdated':outdated,'tags':condition_tags(register,version),'removed':str(version.id) in hidden(register),
        'preferences':preferences(register),'is_mock':all(role['model']['endpoint'].startswith('mock://') for role in resolved.values()) if resolved else False,
        'effective':json.dumps({'cell':version.cell.model_dump(mode='json'),
        'settings':version.settings.model_dump(mode='json'),'resolved_roles':resolved},ensure_ascii=False,indent=2),
        'references':[(path,register.get(ref)) for path,ref in register._references(version.settings)],
        'open_fields':version.settings.open_fields(),'problems':list(dict.fromkeys(problems)), 'roles':resolved,
        'runtime':json.loads(__import__('pathlib').Path(__file__).with_name('pipeline_runtime.lock.json').read_text()),
        'security_resources':__import__('research_env.sandbox_runtime',fromlist=['LIMITS']).LIMITS}


def navigation(register, run_id):
    """One stable identity and set of destinations across all pages of a run."""
    run=register.get(run_id,Run)
    version=register.get(run.configuration_version_id,ConfigurationVersion)
    phase=register.get(run.phase_id,StudyPhase)
    models={}
    for label,role in (('P','analyzer'),('V','test')):
        model=register.resolve_call_model(version,role)
        models[label]={'name':'Kostenfreie Demo' if model.endpoint.startswith('mock://') else model.exact_model_id,
                       'package':version.cell.producer if label=='P' else version.cell.verifier}
    return {'id':run.id,'title':register.get(phase.study_id,Study).title,'cell':version.cell,'models':models,
        'is_mock':all(m['name']=='Kostenfreie Demo' for m in models.values()),
        'overview':'/free-tests' if run.purpose=='free_test' else '/studies/'+str(phase.study_id),
        'overview_label':'Alle Testläufe' if run.purpose=='free_test' else 'Zur Studie',
        'purpose':run.purpose}


def study(register, study_id):
    item=register.get(study_id,Study)
    phases=[]
    for phase in register.all(StudyPhase):
        if phase.study_id!=item.id:continue
        versions=latest_versions(register,phase.id)
        freezes=[f for f in register.all(Freeze) if f.phase_id==phase.id]
        approvals=[a for a in register.all(Approval) if a.phase_id==phase.id]
        phase_freezes=[]
        for freeze in freezes:
            error=None
            try:register._gates(freeze)
            except ValueError as exc:error=str(exc)
            phase_freezes.append({'value':freeze,'gate_error':error,'backup':register.backup_status(freeze.id),
                'backup_jobs':[dict(x) for x in register.connection.execute('SELECT b.job_id,b.status FROM backup_request b JOIN backup_download d USING(job_id) WHERE b.freeze_id=? ORDER BY b.rowid DESC',(str(freeze.id),))],
                'next':register.next_id(freeze.id),
                'next_plan':register.get(register.next_id(freeze.id),PlannedRun) if register.next_id(freeze.id) else None,
                'next_block':register.get(register.get(register.next_id(freeze.id),PlannedRun).block_id,Block).index if register.next_id(freeze.id) else None,
                'next_tags':condition_tags(register,register.get(register.get(register.next_id(freeze.id),PlannedRun).configuration_version_id,ConfigurationVersion)) if register.next_id(freeze.id) else None,'series':[s for s in register.all(Series) if s.freeze_id==freeze.id]})
        settings=next(iter(versions.values())).settings if versions else None
        from .matrix_readiness import checks
        readiness=checks(register,versions) if not freezes else []
        phases.append({'readiness':readiness,'ready':all(c['ok'] for c in readiness),'phase':phase,'state':register.phase_state(phase.id),'versions':versions,'freezes':phase_freezes,
            'approvals':approvals,'base_hash':versions_hash(register,phase.id),'current_software_identity':software_identity(),
            'settings_json':json.dumps(settings.model_dump(mode='json'),ensure_ascii=False,indent=2) if settings else '{}',
            'open_fields':settings.open_fields() if settings else (),
            'open_evidence':[entry for entry in catalog()['entries'] if settings and entry['evidence_key'] in {'ConfigurationVersion.settings.'+field for field in settings.open_fields()}],
            'tags':{str(v.id):condition_tags(register,v) for v in versions.values()},
            'scope':dict(register.connection.execute('SELECT * FROM matrix_draft_preferences WHERE phase_id=?',(str(phase.id),)).fetchone() or {}),
            'planned_rows':runs(register,{'phase':str(phase.id)})['rows'] if freezes else [],'frozen':bool(freezes)})
    from .matrix import matrix
    for row in phases:
        scope=row['scope']
        row['scope_token']=digest(scope) if scope else None
        row['preview']=matrix(row['phase'].id,scope['r_c'],scope['r_e'],scope['seed']) if scope else []
    return {'study':item,'phase_rows':phases}


def runs(register, filters, *, area='research'):
    values=[]
    actual={str(r.planned_run_id):r for r in register.all(Run) if r.planned_run_id}
    for plan in register.all(PlannedRun):
        freeze=register.get(plan.freeze_id,Freeze)
        version=register.get(plan.configuration_version_id,ConfigurationVersion)
        run=actual.get(str(plan.id))
        state=register.state(run.id) if run else None
        values.append({'id':str(run.id) if run else str(plan.id),'planned_id':str(plan.id),'run':run,'phase':str(freeze.phase_id),
            'purpose':'main','config':version.cell_key,'module':version.cell.module,'status':state.execution if state else 'not_started',
            'state':state,'position':plan.position,'code':plan.code,'version':str(version.id)})
    for run in register.all(Run):
        if run.planned_run_id:continue
        version=register.get(run.configuration_version_id,ConfigurationVersion)
        state=register.state(run.id)
        values.append({'id':str(run.id),'planned_id':None,'run':run,'phase':str(run.phase_id),'purpose':run.purpose,
            'config':version.cell_key,'module':version.cell.module,'status':state.execution,'state':state,'position':None,
            'code':run.code,'version':str(version.id)})
    filtered=[]
    for row in values:
        phase=register.get(row['phase'],StudyPhase)
        study=register.get(phase.study_id,Study)
        if study.code.startswith('SYSTEM-CONTROL-'):continue
        if (row['purpose']=='free_test')!=(area=='free'):continue
        version=register.get(row['version'],ConfigurationVersion)
        completion=register.connection.execute('SELECT status FROM run_completion WHERE run_id=?',(row['id'],)).fetchone() if row['run'] else None
        row.update(tags=condition_tags(register,version),title=study.title,context=version.cell.context,planner=version.cell.planner,review=version.cell.review,
            status_label={'queued':'Wartet auf Start','preflight':'Wird vorbereitet','running':'Migration läuft','terminal':'Migration beendet','not_started':'Noch nicht gestartet'}[row['status']])
        if completion:row['status_label']={'retry_requested':'Messung angefordert','running':'Automatische Prüfung läuft','manual_pending':'Bereit zur Bewertung','failed':'Prüfung unvollständig','complete':'Abgeschlossen'}[completion['status']]
        if row['state'] and row['state'].terminal_cause and row['state'].terminal_cause!='finished':row['status_label']='Mit Befund beendet'
        if row['state'] and row['state'].evaluation=='complete':
            from .workflow_views import closure_view
            closure=closure_view(register,row['run'].id)
            if closure:row['status_label']='Abgeschlossen' if closure['current'] else 'Bewertungsstand geändert'
        row['tone']='ok' if row['status_label']=='Abgeschlossen' else 'error' if row['status_label'] in ('Mit Befund beendet','Prüfung unvollständig') else 'warning' if row['status_label'] in ('Bereit zur Bewertung','Bewertungsstand geändert') else 'running' if row['status'] in ('running','preflight') else 'neutral'
        row['models']=[tag[4:] for tag in row['tags'][4:]]
        filtered.append(row)
    values=sorted(filtered,key=lambda row: row["run"].started_at,reverse=True) if area=="free" else filtered
    rows=values
    for key in ('phase','config','module','purpose','context'):
        if filters.get(key):rows=[r for r in rows if filters[key]==r[key]]
    if filters.get('status'):
        # UI and filtering share the displayed completion/closure status. Keep old bookmarked technical filters readable.
        rows=[r for r in rows if filters['status'] in (r['status_label'],r['status'],r['state'].terminal_cause if r['state'] else None)]
    for key in ('planner','review'):
        if filters.get(key):rows=[r for r in rows if filters[key]==('on' if r[key] else 'off')]
    if filters.get('model'):rows=[r for r in rows if filters['model'] in r['models']]
    if filters.get('q'):
        q=filters['q'].casefold()
        rows=[r for r in rows if q in canonical({k:v for k,v in r.items() if k not in ('run','state')}).casefold()]
    choices={key:sorted({r[key] for r in values}) for key in ('phase','config','module','purpose','context')}
    choices.update(status=sorted({r['status_label'] for r in values}),planner=['on','off'],review=['on','off'],
        model=sorted({model for r in values for model in r['models']}))
    return {'rows':rows,'total':len(values),'filters':filters,'choices':choices}


def run_detail(register, run_id):
    from .domain import Event
    run=register.get(run_id,Run)
    binding=register.connection.execute('SELECT * FROM pipeline_binding WHERE run_id=?',(str(run.id),)).fetchone()
    state=register.state(run.id)
    intervals=tuple(x for x in register.all(TimeInterval) if x.run_id==run.id)
    timing=summarize(intervals,coverage_complete=state.execution=='terminal' and bool(binding) and binding['clock_json'] is None and bool(intervals))
    from .adapter import CallJournal
    from .domain import protected_evaluation
    artifacts=[a for a in run_records(register,Artifact,run.id) if not protected_evaluation(a)]
    files=[a for a in artifacts if a.artifact_type in ('pipeline_scaffold_file','pipeline_dependency_file')]
    from .workflow_views import summary
    from .inspection import local_copy
    costs=CallJournal.cost_view(register).effective_costs(run.id)
    diagnosis=diagnostic(register,run.id)
    return {**summary(register,run.id,costs,diagnosis),'run':run,'state':state,'binding':dict(binding) if binding else None,
        'config':configuration(register,run.configuration_version_id,historical=True),'diagnostic':diagnosis,'inspection_local':local_copy(register,run.id),
        'timing':{k:v.model_dump(mode='json') if hasattr(v,'model_dump') else v for k,v in timing.items()},
        'costs':costs,
        'events':[e for e in register.all(Event) if e.run_id==run.id],
        'artifacts':[a for a in artifacts if a.artifact_type not in ('pipeline_scaffold_file','pipeline_dependency_file')], 'file_artifact_count':len(files)}


def free_catalog(register):
    assets=[a for a in register.all(AssetVersion) if a.suite_kind!='study_holdout' and a.access_scope!='trusted_evaluator']
    internal={s.id for s in register.all(Study) if s.code.startswith('SYSTEM-CONTROL-')}
    phases=[p for p in register.all(StudyPhase) if p.purpose in ('demo','free_test') and p.study_id not in internal]
    versions=[v for v in available(register,ConfigurationVersion) if register._phase(v) in {p.id for p in phases}]
    defaults=versions[-1] if versions else next(iter(register.all(ConfigurationVersion)),None)
    phase_map={p.id:p for p in phases}
    titles={str(v.id):register.get(phase_map[register._phase(v)].study_id,Study).title for v in versions}
    return {'version_tags':{str(v.id):condition_tags(register,v) for v in versions},'models':available(register,ModelPackage),'assets':assets,'versions':versions,'version_titles':titles,'default_version':defaults,'current_software_identity':software_identity(),
        'base':defaults.settings.model_dump(mode='json') if defaults else {}}


def condition_tags(register, version):
    cell=version.cell
    tags=[{'BF':'Brute Force','SQL':'SQL Injection','UP':'File Upload'}[cell.module],
          cell.context+' · '+('Basiskontext' if cell.context=='K0' else 'Erweiterter Kontext'),
          'Planner '+('an' if cell.planner else 'aus'),'Review '+('an' if cell.review else 'aus')]
    for label,role in (('P','analyzer'),('V','test')):
        try:
            model=register.resolve_call_model(version,role)
            name='Kostenfreie Demo' if model.endpoint.startswith('mock://') else model.exact_model_id
        except (ValueError,KeyError):name='Modell noch auswählen'
        tags.append(label+' · '+name)
    return tags

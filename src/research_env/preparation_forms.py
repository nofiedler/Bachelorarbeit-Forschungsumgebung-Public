"""Normal selection forms build the same typed settings as the register API."""
from decimal import Decimal
import json
from uuid import UUID

from .domain import Cell, ConfigurationVersion, EffectiveSettings, StudyPhase
from .preparation import Intent, latest_versions
from .register import GateError
from .catalog_ui import require_available


def parameters(text):
    result=json.loads(text or '{}')
    if not isinstance(result,dict):raise GateError('Modellparameter benötigen ein JSONobjekt')
    return result


def from_fields(register, data, *, free=False):
    data=dict(data)
    permitted={'csrf','idempotency_key','phase_id','base_hash','title','model_a','model_b','prompt_id','handoff_id',
        'role_parameters','retry_interval_seconds','module','context','producer','verifier','planner','review',
        'context_asset_id','scaffold_id','contract_id','rubric_id','development_suite_id','source_version_id',
        'holdout_suite_id','reference_id','analysis_id','tool_ids','working_copy_evidence_id','dvwa_commit','software_commit','hardware',
        'lockfile_hashes','container_hashes','provider_limits_defaults','security_resources',
        *(f'context_{m}_K{k}' for m in ('BF','SQL','UP') for k in (0,1))}
    if free and any(data.get(key) for key in ('holdout_suite_id','reference_id')):
        raise GateError('study_holdout/Referenzen sind auch über das freie Formular nicht auswählbar')
    if set(data)-permitted:raise GateError('Unbekannte Formularfelder: '+', '.join(sorted(set(data)-permitted)))
    def selected(key):return UUID(data[key]) if data.get(key) else None
    if free:
        base=register.get(selected('source_version_id'),ConfigurationVersion).settings if data.get('source_version_id') else EffectiveSettings()
    else:
        phase=register.get(selected('phase_id'),StudyPhase)
        versions=latest_versions(register,phase.id)
        if not versions:raise GateError('Phase hat keine Konfigurationen')
        base=next(iter(versions.values())).settings
    values={'model_a':selected('model_a'),'model_b':selected('model_b'),'prompt_ids':(selected('prompt_id'),) if selected('prompt_id') else (),
        'handoff_id':selected('handoff_id'),'role_parameters':parameters(data.get('role_parameters')),
        'retry_interval_seconds':Decimal(data['retry_interval_seconds']) if data.get('retry_interval_seconds') else None}
    if free:
        from .preparation import software_identity
        values.update(dvwa_commit=base.dvwa_commit or 'b496a5d3de6b967410155e1b7d3e51e9d035eb22',
            software_commit=software_identity())
        module=data.get('module'); context=data.get('context')
        values.update(context_ids={f'{module}-{context}':selected('context_asset_id')} if selected('context_asset_id') else {},
            scaffold_id=selected('scaffold_id'),contract_id=selected('contract_id'),rubric_id=selected('rubric_id'),
            development_suite_id=selected('development_suite_id'),holdout_suite_id=None,reference_id=None)
        cell=Cell(module=module,context=context,producer=data.get('producer','A'),verifier=data.get('verifier','A'),
            planner=data.get('planner')=='true',review=data.get('review')=='true')
        return data['idempotency_key'],Intent(action='free',title=data.get('title','Eigener Entwicklungstest'),cell=cell,
            settings=EffectiveSettings.model_validate({**base.model_dump(mode='json'),**values}))
    for key in ('contract_id','rubric_id','scaffold_id','development_suite_id','holdout_suite_id','reference_id','analysis_id','working_copy_evidence_id'):
        if key in data:values[key]=selected(key)
    for key in ('dvwa_commit','software_commit','hardware'):
        if key in data:values[key]=data[key] or None
    for key in ('lockfile_hashes','container_hashes','provider_limits_defaults','security_resources'):
        if key in data:values[key]=parameters(data[key])
    if 'tool_ids' in data:values['tool_ids']=tuple(UUID(v.strip()) for v in data['tool_ids'].split(',') if v.strip())
    if any('context_'+m+'_K'+str(k) in data for m in ('BF','SQL','UP') for k in (0,1)):
        values['context_ids']={f'{m}-K{k}':selected(f'context_{m}_K{k}') for m in ('BF','SQL','UP') for k in (0,1) if selected(f'context_{m}_K{k}')}
    return data['idempotency_key'],Intent(action='central',target_id=phase.id,base_hash=data.get('base_hash'),
        settings=EffectiveSettings.model_validate({**base.model_dump(mode='json'),**values}))


def simple_free(register, data):
    """Only experimental choices come from the run form. Resolve constants together."""
    from .application_settings import default_settings
    from .domain import ModelPackage
    allowed = {'idempotency_key','title','module','context','model_a','model_b','producer','verifier','planner','review'}
    if set(data)-allowed: raise GateError('Technische Einstellungen gehören in den Einstellungsbereich')
    base = default_settings(register)
    cell = Cell(module=data.get('module'),context=data.get('context'),producer=data.get('producer','A'),
        verifier=data.get('verifier','A'),planner=data.get('planner')=='true',review=data.get('review')=='true')
    models = [register.get(UUID(data[k]),ModelPackage) for k in ('model_a','model_b')]
    require_available(register, *(m.id for m in models))
    mock = [m.endpoint.startswith('mock://') for m in models]
    if any(mock) and not all(mock): raise GateError('Kostenfreie Demo und reale Modelle bitte in getrennten Testläufen verwenden')
    params = {'all': {'seed':1}} if all(mock) else base.role_parameters
    settings = base.model_copy(update={'model_a':models[0].id,'model_b':models[1].id,'role_parameters':params,
        'context_ids':{f'{cell.module}-{cell.context}':base.context_ids[f'{cell.module}-{cell.context}']}})
    title = data.get('title','').strip()
    if not title or len(title)>200: raise GateError('Bitte einen Titel mit höchstens 200 Zeichen eingeben')
    return data['idempotency_key'], Intent(action='free',title=title,cell=cell,settings=settings)


def simple_study(register,data):
    """Prepare the fixed twelve conditions with shared, explicitly selected A/B."""
    from .application_settings import default_settings
    from .domain import ModelPackage
    from .domain import MAIN_CELLS
    allowed={'idempotency_key','title','model_a','model_b'}
    if set(data)-allowed-{'condition_'+key for key in MAIN_CELLS} or not allowed.issubset(data):raise GateError('Für die Matrix sind Titel und die beiden Modellpakete erforderlich')
    title=data['title'].strip()
    if not title or len(title)>200:raise GateError('Bitte einen Titel mit höchstens 200 Zeichen eingeben')
    models=[register.get(UUID(data[key]),ModelPackage) for key in ('model_a','model_b')]
    require_available(register, *(m.id for m in models))
    if any(m.endpoint.startswith('mock://') for m in models):raise GateError('Demos gehören zu den Testläufen; für die Forschungsplanung zwei reale Modelle wählen')
    if models[0].exact_model_id==models[1].exact_model_id:raise GateError('Die Forschungsmatrix benötigt zwei unterschiedliche Modelle A und B. A/A-Läufe sind darin bereits vorgesehen.')
    for key,cell in MAIN_CELLS.items():
        if not data.get('condition_'+key):continue
        version=register.get(UUID(data['condition_'+key]),ConfigurationVersion)
        phase=register.get(register._phase(version),StudyPhase)
        if phase.purpose not in ('free_test','demo') or version.cell!=cell:
            raise GateError('Gewählte Konfiguration passt nicht zur vorgesehenen Bedingung '+key)
        if version.settings.model_a!=models[0].id or version.settings.model_b!=models[1].id:
            raise GateError('Die ausgewählten Konfigurationen müssen dieselben Modellpakete A und B verwenden')
        require_available(register,version.id)
    settings=default_settings(register,research=True).model_copy(update={'model_a':models[0].id,'model_b':models[1].id})
    return data['idempotency_key'],Intent(action='draft',title=title,settings=settings)

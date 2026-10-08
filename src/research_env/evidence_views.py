"""Read-only catalog projection and strictly run-bound archive navigation."""
from collections import Counter
from functools import lru_cache
import difflib
import hashlib
import json
from uuid import UUID
from pydantic import BaseModel
from .artifacts import ArtifactStore, IntegrityError, read_regular
from .domain import *
from .evidence import catalog, field_values, field_contexts
from .preparation import redact_credentials
from .register import GateError


def read_artifact(register, identity):
    artifact=register.get(identity, Artifact)
    data,mode=read_regular(register.settings.artifacts,ArtifactStore.object_path(artifact.sha256))
    if len(data)!=artifact.byte_count or hashlib.sha256(data).hexdigest()!=artifact.sha256 or mode & 0o222:
        raise IntegrityError('Beschädigtes Artefakt '+str(identity))
    return data


def owner(register,value):
    if hasattr(value,'run_id'):return value.run_id
    if isinstance(value,(TransportAttempt,TransportStateEvent,RetryEvidence)):
        return owner(register,register.get(value.call_id if isinstance(value,TransportAttempt) else value.transport_id))
    if isinstance(value,(TestResult,StaticProfile,FunctionalProfile)):return owner(register,register.get(value.measurement_id))
    return None


def archive(register,identity):
    item=register.get(identity)
    if isinstance(item,PlannedRun):
        row=register.connection.execute('SELECT run_id FROM run_binding WHERE planned_id=?',(str(item.id),)).fetchone()
        if row:return archive(register,UUID(row[0]))
        plan,run=item,None
        freeze=register.get(plan.freeze_id,Freeze)
        phase=register.get(freeze.phase_id,StudyPhase)
        version=register.get(plan.configuration_version_id,ConfigurationVersion)
        state=None
    elif isinstance(item,Run):
        run,plan=item,register.get(item.planned_run_id,PlannedRun) if item.planned_run_id else None
        phase=register.get(run.phase_id,StudyPhase)
        version=register.get(run.configuration_version_id,ConfigurationVersion)
        state=register.state(run.id)
        freeze=register.get(plan.freeze_id,Freeze) if plan else None
    else:raise GateError('Nur Lauf oder geplante Main-ID öffnen')
    seeds=[x.id for x in (run,plan,phase,version,freeze) if x]
    if run:
        # Indexed owner selection avoids parsing every foreign run and vendor file.
        related = register.connection.execute("""SELECT id FROM register_record WHERE json_extract(payload,'$.run_id')=?
            UNION SELECT transport_id FROM transport_binding WHERE call_id IN (SELECT call_id FROM call_binding WHERE run_id=?)
            UNION SELECT event_id FROM transport_event_binding WHERE transport_id IN
                (SELECT transport_id FROM transport_binding WHERE call_id IN (SELECT call_id FROM call_binding WHERE run_id=?))
            UNION SELECT r.id FROM register_record r JOIN revision_binding b
                ON json_extract(r.payload,'$.measurement_id')=b.revision_id WHERE b.run_id=?""", (str(run.id),)*4)
        seeds += [row[0] for row in related]
        seeds += [x.id for x in register.all(AnalysisRun) if x.phase_id==phase.id]
        seeds += [x.id for x in register.all(Backup) if x.phase_id==phase.id]
        seeds += [x.id for x in register.all(BackupReceipt) if register.get(x.backup_id,Backup).phase_id==phase.id]
    seeds += [x.id for x in register.all(MetricObservation) if x.phase_id==phase.id and x.run_id is None]
    seeds += [x.id for x in register.all(PhaseStateRevision) if x.phase_id==phase.id]
    invalidations={}
    for value in register.all(RevisionInvalidation):invalidations.setdefault(value.revision_id,[]).append(value.id)
    records,pending={},list(seeds)
    while pending:
        rid=UUID(str(pending.pop()))
        if rid in records:continue
        obj=register.get(rid)
        own=owner(register,obj)
        if own and (not run or own!=run.id):continue
        if phase.purpose=='free_test' and isinstance(obj,AssetVersion) and obj.suite_kind=='study_holdout':
            raise GateError('Holdout nicht im freien Archiv')
        records[rid]=obj
        pending.extend(invalidations.get(rid,[]))
        pending.extend(row[0] for row in register.connection.execute('SELECT target_id FROM register_reference WHERE owner_id=?',(str(rid),)))
    if state:records['state']=state
    records['phase_state']=register.phase_state(phase.id)
    if run:
        for value in tuple(records.values()):
            if isinstance(value,Job):
                row=register.connection.execute('SELECT * FROM job_state WHERE job_id=?',(str(value.id),)).fetchone()
                if row:
                    records['claim-'+str(value.id)]=JobClaim.model_validate({key:row[key] for key in JobClaim.model_fields})
            if isinstance(value,TransportAttempt):
                row=register.connection.execute('SELECT * FROM transport_state WHERE transport_id=?',(str(value.id),)).fetchone()
                if row:records['transport-'+str(value.id)]=TransportState.model_validate(dict(row))
    return {'identity':identity,'run':run,'plan':plan,'phase':phase,'version':version,'state':state,'freeze':freeze,'records':records,
        'study':register.get(phase.study_id,Study),
        'candidate':register.get(state.candidate_id,CandidateSnapshot) if state and state.candidate_id else None,
        'backup':register.backup_status(freeze.id) if freeze else 'not_applicable'}


def artifact_for(register,run_id,artifact_id):
    view=archive(register,run_id)
    value=view['records'].get(artifact_id)
    if not isinstance(value,Artifact):raise GateError('Artefakt gehört nicht zu dieser Laufansicht')
    if view['phase'].purpose=='free_test' and value.run_id!=run_id and protected_evaluation(value):
        raise GateError('Keine fremde Bewertung im freien Test')
    return value


@lru_cache(maxsize=1)
def catalog_entries():
    return catalog()['entries']


def integrity_problem(register, value, cache=None, visiting=None):
    """Current transitively referenced bytes; immutable historical judgments remain."""
    cache=cache if cache is not None else {}
    visiting=visiting if visiting is not None else set()
    identity=getattr(value,'id',None)
    if identity in cache:return cache[identity]
    if identity in visiting:return None
    visiting.add(identity)
    problem=None
    try:
        if isinstance(value,Artifact):read_artifact(register,value.id)
        elif identity:
            for target_id in register.proof_references(value):
                target=register.get(target_id)
                problem=integrity_problem(register,target,cache,visiting)
                if problem:break
    except (OSError,ValueError,KeyError,TypeError) as exc:
        problem='damaged_artifact / reference '+str(identity)+': '+str(exc)
    visiting.discard(identity)
    cache[identity]=problem
    return problem


def current_valid(register,value,cache=None):
    return register.valid(value.id) and integrity_problem(register,value,cache) is None


def observation_context(data,path):
    statuses=[]
    for value,ancestors in field_contexts(data,path):
        for node in reversed(ancestors+(value,)):
            if isinstance(node,dict) and 'status' in node and 'unit' in node and 'value' in node:
                statuses.append((node['status'],node.get('reason')))
                break
    return next((v for v in statuses if v[0] not in ('observed','estimated')),(None,None))


def projection(register,identity):
    view=archive(register,identity)
    entries=catalog_entries();grouped={};rows=[]
    for value in view['records'].values():grouped.setdefault(type(value).__name__,[]).append(value)
    documents={id(value):value.model_dump(mode='json') for value in view['records'].values()}
    artifact_problems={}
    integrity_cache={}
    for a in grouped.get('Artifact',[]):
        try:read_artifact(register,a.id)
        except (OSError,ValueError) as exc:artifact_problems[a.id]='damaged_artifact: '+str(exc)
    for entry in entries:
        entity=entry['validator']['contract']
        for obj in grouped.get(entity,[]):
            # Register.get/contract projections have already validated the complete
            # containing immutable document. Read catalog paths from those bytes.
            values=list(field_values(documents[id(obj)],entry['validator']['field_path'].split('.')))
            reason=None;status='gespeichert'
            for val in values:
                if isinstance(val,BaseModel):val=val.model_dump(mode='json')
                if isinstance(val,dict) and val.get('status') in ('pending','not_collected','technical_missing','unresolved','not_applicable'):
                    status='nicht anwendbar' if val['status']=='not_applicable' else 'problematisch'
                    reason=val.get('reason') or val['status']
            path=entry['validator']['field_path'].split('.')
            observation_status,observation_reason=observation_context(documents[id(obj)],path)
            if observation_status and observation_status not in ('observed','estimated'):
                status='nicht anwendbar' if observation_status=='not_applicable' else 'problematisch'
                reason=observation_status+': '+str(observation_reason)
            elif not values or values==[None]:
                status,reason='nicht anwendbar','Optionales Feld; fällige Abwesenheit wird im enthaltenden Vertrag/Status explizit erklärt'
            if isinstance(obj,CriterionReviewRevision) and obj.completion=='draft':
                status,reason='problematisch','pending: gespeicherter Entwurf; manuelles Urteil noch nicht abgeschlossen'
            if isinstance(obj,MeasurementAttempt) and obj.completion=='draft':
                status,reason='problematisch','pending: unvollständiger Messentwurf'
            if isinstance(obj,(MeasurementAttempt,CriterionReviewRevision,TestResult,StaticProfile,FunctionalProfile,ResourceProfile,MetricObservation,CandidateSnapshot,ModelCall,TransportAttempt,TransportStateEvent)):
                problem=integrity_problem(register,obj,integrity_cache)
                if problem:status,reason='problematisch',problem
                elif isinstance(obj,(MeasurementAttempt,CriterionReviewRevision)) and obj.completion=='completed' and not register.valid(obj.id):
                    status,reason='problematisch','invalidated_revision: Revision oder abhängiger Messbeleg ungültig'
            if isinstance(obj,Artifact) and obj.id in artifact_problems:status,reason='problematisch',artifact_problems[obj.id]
            rows.append({**entry,'record_id':str(getattr(obj,'id',view['run'].id if view['run'] else identity)),
                'value':redact_credentials([v.model_dump(mode='json') if isinstance(v,BaseModel) else v for v in values]),
                'status':status,'reason':reason,'required':status not in ('nicht anwendbar','noch nicht fällig')})
    expected={'CandidateSnapshot':[None],'MeasurementAttempt':['functional_R','static_DLS'],'CriterionReviewRevision':['T2','T3','T4']}
    expected['ModelCall']=['analyzer','planner','migrate','test','review','repair']
    # Instance applicability is a storage/lifecycle adapter; field definitions,
    # validators and units continue to come exclusively from evidence_schema.
    for entry in entries:
        entity=entry['validator']['contract']
        if entity not in grouped and entity not in expected:expected[entity]=[None]
    for entity,scopes in expected.items():
        for scope in scopes:
            attr={'CriterionReviewRevision':'criterion','MeasurementAttempt':'measurement_key','ModelCall':'node'}.get(entity)
            if any(not attr or getattr(v,attr,None)==scope for v in grouped.get(entity,[])):continue
            disabled=entity=='ModelCall' and ((scope=='planner' and not view['version'].cell.planner) or (scope=='review' and not view['version'].cell.review))
            repair_absent=entity=='ModelCall' and scope=='repair' and not any(v.repair_count for v in grouped.get('CandidateSnapshot',[]))
            if disabled:status,reason='nicht anwendbar','disabled_role: '+scope
            elif repair_absent:status,reason='nicht anwendbar','repair_not_triggered: kein ausgelöster Repair'
            elif not view['run']:status,reason='noch nicht fällig','run_not_started: Lauf nicht gestartet'
            elif entity in ('CriterionReviewRevision','MeasurementAttempt') and not view['candidate']:status,reason='noch nicht fällig','candidate_missing: kein versiegelter Kandidat'
            elif view['state'].execution!='terminal':status,reason='noch nicht fällig','pending: technischer Abschluss ausstehend'
            elif entity=='ModelCall' and view['state'].terminal_cause!='finished':
                status,reason='noch nicht fällig','not_due: nicht ausgeführte Rolle nach terminalem '+str(view['state'].terminal_cause)
            elif entity=='CandidateSnapshot' and view['state'].seal=='no_candidate':status,reason='nicht anwendbar','candidate_missing: '+str(view['state'].terminal_cause)
            elif entity in ('AnalysisRun','Package','RevisionInvalidation','RetryEvidence','Series','Block','PlannedRun','Freeze','Approval','ModelPackage'):
                status,reason='noch nicht fällig','not_due: keine zugehörige Instanz / gesonderte Aktion in diesem Kontext'
            elif entity in ('Backup','BackupReceipt') and not view['freeze']:
                status,reason='nicht anwendbar','not_applicable: kein Main-Backupgate'
            elif entity in ('TransportAttempt','TransportState','TransportStateEvent','CostEntry') and not grouped.get('ModelCall'):
                status,reason='noch nicht fällig','not_due: keine zugehörigen logischen Aufrufe/Transportversuche'
            elif entity in ('TestResult','FunctionalProfile','StaticProfile') and not grouped.get('MeasurementAttempt'):
                status,reason='noch nicht fällig','pending: zugehörige Messung noch nicht erhoben'
            else:status,reason='fehlt','not_collected: '+str(scope or entity)
            for entry in entries:
                if entry['validator']['contract']==entity:
                    rows.append({**entry,'record_id':None,'value':None,'status':status,'reason':reason,'scope':scope,'required':status=='fehlt'})
    view['catalog_rows']=rows;view['counts']=dict(Counter(row['status'] for row in rows));view['due_count']=sum(row['required'] for row in rows)
    types=(MeasurementAttempt,CriterionReviewRevision,RevisionInvalidation,TestResult,FunctionalProfile,StaticProfile,ModelCall,TransportAttempt,TransportStateEvent,Event,TimeInterval,ResourceProfile,MetricObservation,PhaseStateRevision,RunStateRevision)
    view['history']=[v for v in view['records'].values() if isinstance(v,types)]
    view['artifacts']=[v for v in view['records'].values() if isinstance(v,Artifact)]
    view['analyses']=[v for v in view['records'].values() if isinstance(v,AnalysisRun)]
    return view


def snapshot_diff(register,run_id,snapshot_id):
    view=archive(register,run_id);snapshot=view['records'].get(snapshot_id)
    if not isinstance(snapshot,CandidateSnapshot):raise GateError('Snapshot gehört nicht zu diesem Lauf')
    previous=[v for v in view['records'].values() if isinstance(v,CandidateSnapshot) and v.created_at<snapshot.created_at]
    previous=max(previous,key=lambda v:v.created_at) if previous else None
    def files(value):
        if value is None:return {a.original_name:a.id for a in view['records'].values() if isinstance(a,Artifact) and a.artifact_type=='pipeline_scaffold_file'}
        body=json.loads(read_artifact(register,value.file_manifest_id))
        return dict(zip((v['path'] for v in body['files']),(UUID(v) for v in body['artifact_ids']),strict=True))
    before,after=files(previous),files(snapshot);output=[]
    for name in sorted(set(before)|set(after)):
        old=read_artifact(register,before[name]).decode('utf-8',errors='replace').splitlines(keepends=True) if name in before else []
        new=read_artifact(register,after[name]).decode('utf-8',errors='replace').splitlines(keepends=True) if name in after else []
        output.extend(difflib.unified_diff(old,new,fromfile='vorher/'+name,tofile='nachher/'+name))
    return {'snapshot':snapshot,'previous':previous,'diff':''.join(output),'basis':'Vorheriger Snapshot' if previous else 'Archivierte Gerüstdateien; fehlt eine Datei, ist die Vergleichsbasis unvollständig'}


def overview(register, identity, *, category='code', page=1, query=''):
    """Bounded archive index. Reading it does not rehash every dependency byte."""
    item=register.get(identity)
    if isinstance(item,PlannedRun):
        actual=register.connection.execute('SELECT run_id FROM run_binding WHERE planned_id=?',(str(identity),)).fetchone()
        if actual:return overview(register,UUID(actual[0]),category=category,page=page,query=query)
        phase=register.get(register.get(item.freeze_id,Freeze).phase_id,StudyPhase)
        return {'identity':identity,'run':None,'plan':item,'phase':phase,'entries':[],'total':0,'page':1,'pages':1,'category':category,'query':query}
    if not isinstance(item,Run):raise GateError('Nur Lauf oder geplante Lauf-ID öffnen')
    categories={'code':("kind='CandidateSnapshot'",()),
        'measurements':("kind IN ('MeasurementAttempt','CriterionReviewRevision','TestResult')",()),
        'artifacts':("kind='Artifact' AND json_extract(payload,'$.artifact_type') NOT IN ('pipeline_dependency_file','pipeline_scaffold_file')",()),
        'events':("kind IN ('Event','ModelCall','TimeInterval','ResourceProfile')",())}
    if category not in categories:raise GateError('Unbekannte Nachweisgruppe')
    page=max(1,int(page)); query=query[:200]
    where=categories[category][0]+" AND json_extract(payload,'$.run_id')=?"
    args=[str(item.id)]
    if query:
        where+=" AND (code LIKE ? ESCAPE '!' OR json_extract(payload,'$.original_name') LIKE ? ESCAPE '!')"
        term='%'+query.replace('!','!!').replace('%','!%').replace('_','!_')+'%';args += [term,term]
    total=register.connection.execute('SELECT COUNT(*) FROM register_record WHERE '+where,args).fetchone()[0]
    pages=max(1,(total+39)//40);page=min(page,pages)
    rows=register.connection.execute('SELECT id FROM register_record WHERE '+where+' ORDER BY rowid DESC LIMIT 40 OFFSET ?',(*args,(page-1)*40))
    entries=[]
    for row in rows:
        value=register.get(row[0])
        route='snapshots' if isinstance(value,CandidateSnapshot) else 'artifacts' if isinstance(value,Artifact) else 'records'
        name=(value.original_name or value.artifact_type) if isinstance(value,Artifact) else value.role if isinstance(value,CandidateSnapshot) else getattr(value,'criterion',None) or getattr(value,'measurement_key',None) or getattr(value,'node',None) or value.code
        entries.append({'id':value.id,'name':{'sealed':'Versiegelter Endstand','post_migrate':'Stand nach der Migration','post_repair':'Stand nach der Überarbeitung'}.get(name,name),'type':type(value).__name__,'route':route,
            'detail':getattr(value,'artifact_type',''),'revision':getattr(value,'revision',None)})
    return {'identity':identity,'run':item,'plan':None,'phase':register.get(item.phase_id,StudyPhase),
        'entries':entries,'total':total,'page':page,'pages':pages,'category':category,'query':query}

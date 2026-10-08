"""Explicit N07 scope/matrix preview and separate documented decision records."""
from decimal import Decimal
from uuid import UUID, uuid4

from .domain import (Approval, Artifact, AssetVersion, EvidenceProof, Freeze, FreezeResources, HUMAN_GATES, REQUIRED_GATES,
    RESOURCE_GATES, TECHNICAL_GATES, NonnegativeObservation, StudyPhase, digest)
from .matrix import ALGORITHM, matrix
from .preparation import Intent, latest_versions, versions_hash
from .register import GateError


def preview(register, data):
    data=dict(data)
    if 'scope_token' in data:
        scope=dict(register.connection.execute('SELECT * FROM matrix_draft_preferences WHERE phase_id=?',(data['phase_id'],)).fetchone() or {})
        if not scope or data.pop('scope_token')!=digest(scope):
            raise GateError('Der gespeicherte Umfang wurde geändert. Lade die Versuchsreihe neu und prüfe Anzahl und Reihenfolge erneut.')
        if any(key in data for key in ('r_c','r_e','seed')):
            raise GateError('Wiederholungen und Reihenfolge werden ausschließlich aus dem gespeicherten Entwurf übernommen')
        data.update({key:str(scope[key]) for key in ('r_c','r_e','seed')})
    allowed={'phase_id','idempotency_key','base_hash','r_c','r_e','seed','consumption_id','pilot_ids','resource_decision',
        'estimated_cost','estimated_time','estimate_source','backup_destination','technical_person','technical_reason',
        'subject_person','subject_reason','cost_person','cost_reason','confirm_technical','confirm_subject','confirm_cost',
        *('proof_'+key for key in REQUIRED_GATES)}
    if set(data)-allowed:raise GateError('Unbekannte Freezeangaben')
    for key in ('confirm_technical','confirm_subject','confirm_cost'):
        if data.get(key)!='true':raise GateError('Technische Prüfung, Fachabnahme und konkrete bezahlte Hauptfreigabe müssen getrennt ausdrücklich dokumentiert sein')
    phase=register.get(UUID(data['phase_id']),StudyPhase)
    if phase.purpose!='main':raise GateError('Freeze nur für Mainphase')
    if data['base_hash']!=versions_hash(register,phase.id):raise GateError('Entwurf verändert; aktuelle gemeinsame Versionen erneut prüfen')
    r_c,r_e=int(data['r_c']),int(data['r_e'])
    if r_c<1 or r_e<1 or r_e>r_c:raise GateError('r_C/r_E müssen manuell positiv gewählt sein; r_E≤r_C')
    if not data['seed'].strip():raise GateError('Bewusst festgelegter Seed erforderlich')
    legacy='consumption_id' in data
    if legacy:
        consumption=register.get(UUID(data['consumption_id']),Artifact)
        resources=FreezeResources(resource_decision=data['resource_decision'],consumption_evidence_id=consumption.id,
            consumption_evidence_hash=consumption.sha256,pilot_run_ids=tuple(UUID(v.strip()) for v in data['pilot_ids'].split(',') if v.strip()),
            estimated_cost=NonnegativeObservation(status='estimated',value=Decimal(data['estimated_cost']),unit='USD',source=data['estimate_source']),
            estimated_time=NonnegativeObservation(status='estimated',value=Decimal(data['estimated_time']),unit='s',source=data['estimate_source']),
            backup_destination=data['backup_destination'])
    else:
        from .matrix_readiness import require_ready
        require_ready(register, latest_versions(register,phase.id))
        resources=FreezeResources(resource_decision=data['resource_decision'].strip(),consumption_evidence_id=None,
            consumption_evidence_hash=None,pilot_run_ids=(),
            estimated_cost=NonnegativeObservation(status='not_collected',unit='USD',reason='Keine Pilotierung oder Aufwandsschätzung vorgesehen'),
            estimated_time=NonnegativeObservation(status='not_collected',unit='s',reason='Keine Pilotierung oder Aufwandsschätzung vorgesehen'),
            backup_destination=data['backup_destination'].strip())
    versions={k:v.id for k,v in latest_versions(register,phase.id).items()}
    freeze_id=uuid4()
    scope=register.freeze_scope(phase.id,versions,r_c,r_e,data['seed'],freeze_id,resources=resources)
    proofs=tuple(EvidenceProof(key=key,asset_id=UUID(data['proof_'+key]),
        asset_hash=register.get(UUID(data['proof_'+key]),AssetVersion).manifest_hash,scope_hash=scope) for key in REQUIRED_GATES) if legacy else ()
    approvals=[]
    for kind,prefix,keys in (('technical','technical',TECHNICAL_GATES),('subject','subject',HUMAN_GATES),('cost','cost',RESOURCE_GATES)):
        approvals.append(Approval(schema_version=1 if legacy else 2,code=f'UI-{kind}-{uuid4()}',phase_id=phase.id,kind=kind,scope_hash=scope,
            person=data[prefix+'_person'],reason=data[prefix+'_reason'],evidence=tuple(p for p in proofs if p.key in keys),
            paid_calls_consent=kind=='cost',synthetic_fixture=False))
    body=dict(id=freeze_id,code=f'FREEZE-{freeze_id}',phase_id=phase.id,configuration_version_ids=versions,r_c=r_c,r_e=r_e,
        seed=data['seed'],algorithm=ALGORITHM,matrix=matrix(freeze_id,r_c,r_e,data['seed']),scope_hash=scope,
        evidence=proofs,approval_ids=tuple(a.id for a in approvals),selection_rule='last_completed_valid_compatible-v1',**{name:getattr(resources,name) for name in FreezeResources.model_fields})
    # Include generated timestamp/schema before hashing the full immutable record.
    from datetime import datetime,timezone
    body.update(created_at=datetime.now(timezone.utc),schema_version=1 if legacy else 2)
    normalized=Freeze.model_construct(**body,freeze_hash='0'*64).model_dump(mode='json',exclude={'freeze_hash'})
    normalized['freeze_hash']=digest(normalized)
    freeze=Freeze.model_validate(normalized)
    return data['idempotency_key'],Intent(action='freeze',target_id=phase.id,base_hash=data['base_hash'],
        freeze=freeze,approvals=tuple(approvals),decision='Bewusste Erfassung der konkret referenzierten getrennten Entscheidungen')

"""Actual SQLite/CAS synthetic setup, isolated files; no human research approval."""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import test_register as fixtures
from research_env.adapter import CallJournal
from research_env.providers import MockAdapter
from research_env.artifacts import ArtifactStore
from research_env.backup import Backups, BackupWorker
from research_env.config import Settings
from research_env.database import migrate
from research_env.domain import *
from research_env.register import Register
from research_env.snapshots import Snapshots, ProcessWriters

INSTRUMENT = {'synthetic': 'M7 hand-frozen absence instrument; no native evaluator or study claim'}


def make_environment(root):
    settings=Settings(*(Path(root)/p for p in ('control','artifacts','checkpoints','staging')))
    for path in (settings.control,settings.artifacts,settings.checkpoints,settings.staging):path.mkdir(parents=True)
    migrate(settings);r=Register(settings);store=ArtifactStore(settings,r)
    old_asset=fixtures.asset
    def asset(reg, kind='tool', suite=None, **kwargs):
        if suite=='study_holdout' and kind=='suite':
            cases=[{'id':m+'-c'+str(i),'category':'R'+str(i),'module':m,'assertions':[m+'-a'+str(i)]} for m in ('BF','SQL','UP') for i in range(1,7)]
            raw=store.json({'data_origin':'synthetic','cases':cases},artifact_type='evaluation_case_catalog',producer='trusted_evaluator',access_scope='trusted_evaluator')
            return reg.add(AssetVersion(code='M7-SYNTHETIC-SUITE-'+str(uuid4()),asset_type=kind,suite_kind=suite,
                manifest_hash=raw.sha256,artifact_ids=(raw.id,),origin='SYNTHETIC frozen hand-control catalog; not empirical suite',access_scope='trusted_evaluator'))
        if kind=='tool' and suite is None:
            return reg.add(AssetVersion(code='M7-SYNTHETIC-TOOL-'+str(uuid4()),asset_type='tool',manifest_hash=digest(INSTRUMENT),
                origin='SYNTHETIC hand-binding only',access_scope='trusted_register'))
        return old_asset(reg,kind,suite,**kwargs)
    def artifact(reg, run=None, **kwargs):
        return store.store(b'SYNTHETIC real CAS input; no empirical evidence',run_id=run.id if run else None,**kwargs)
    with patch.object(fixtures,'asset',asset), patch.object(fixtures,'artifact',artifact):
        f=fixtures.setup_study(r);freeze=r.freeze(fixtures.freeze_value(r,f))
    return r,settings,store,f,freeze,Backups(settings,r)


def secure(env,target):
    r,settings,store,f,freeze,backups=env
    worker=BackupWorker(backups)
    job=backups.enqueue(freeze.id,idempotency_key=str(uuid4()));value=worker.tick()
    backups.transfer(job.id,target)
    before=r.revision(freeze.phase_id)
    receipt=backups.confirm(job.id,person='TECHNICAL-FIXTURE:M7 local test',external_medium='TECHNICAL-FIXTURE: local separate directory; no SSD',confirmed_at=fixtures.NOW,synthetic=True)
    assert before==r.revision(freeze.phase_id)
    return job,value,receipt


def start(env, root, *, no_candidate=False, cause='finished'):
    r,settings,store,f,freeze,backups=env
    secure(env,Path(root)/('backup-start-'+str(uuid4())))
    run,job=fixtures.start(r,freeze,f)
    if no_candidate:
        r.set_state(run.id,RunState(execution='terminal',terminal_cause=cause,seal='no_candidate',ended_at=fixtures.NOW),reason='Synthetic controlled absence')
        return run,None,None
    tree=Path(root)/('tree-'+str(uuid4()));tree.mkdir();(tree/'app').mkdir();(tree/'app/Module.php').write_text('<?php // unchanged synthetic candidate\n')
    deps=Path(root)/('dependencies-'+str(uuid4()));deps.mkdir();(deps/'autoload.php').write_text('<?php // synthetic dependencies\n')
    snapshots=Snapshots(store);dependency=snapshots.dependencies(deps,source={'origin':'Synthetic'},runtime={'runtime':'synthetic'})
    seal=snapshots.seal(tree,writers=ProcessWriters(),run_id=run.id,scaffold_id=f['settings'].scaffold_id,dependency_ids=(dependency.id,),internal_tests_hash=hashlib.sha256(b'').hexdigest())
    r.set_state(run.id,r.state(run.id).model_copy(update={'execution':'terminal','terminal_cause':'finished','ended_at':fixtures.NOW}),reason='SYNTHETIC supplied hand-control candidate; no generation')
    raw=store.json({'origin':'SYNTHETIC predetermined assertion/review evidence'},run_id=run.id,producer='trusted_evaluator',access_scope='trusted_evaluator',artifact_type='evaluation_hand_evidence')
    comp=Compatibility(candidate_id=seal.id,candidate_hash=seal.tree_hash,phase_id=run.phase_id,contract_id=f['settings'].contract_id,suite_id=run.suite_id,tool_id=f['tool'].id)
    return run,comp,raw


def measurement(env,run,comp,raw,*,passed=6,completion='completed',key='functional_R',report=None):
    r,settings,store,f,freeze,backups=env
    previous=[m for m in r.all(MeasurementAttempt) if m.run_id==run.id and m.measurement_key==key]
    prev=max(previous,key=lambda m:m.revision) if previous else None
    m=r.add(MeasurementAttempt(code='M7-MEASURE-'+str(uuid4()),run_id=run.id,measurement_key=key,compatibility=comp,
        fixture_id=run.suite_id,revision=prev.revision+1 if prev else 1,predecessor_id=prev.id if prev else None,completion=completion,suite_kind='study_holdout',
        result=Observation(status='observed',value=canonical(report or {'assertions':6}),unit='report',source='SYNTHETIC hand-control') if completion=='completed' else Observation(status='pending',unit='report',reason='SYNTHETIC draft'),
        raw_artifact_ids=(raw.id,),exit_code=IntegerObservation(status='observed',value=0,unit='exit',source='SYNTHETIC'),reason='SYNTHETIC predetermined measurement'))
    module=r.get(run.configuration_version_id,ConfigurationVersion).cell.module
    if key=='functional_R' and completion=='completed':
        for i in range(1,7):
            r.add(TestResult(code='M7-RESULT-'+str(uuid4()),measurement_id=m.id,test_id=module+'-c'+str(i),assertion_id=module+'-a'+str(i),r_category='R'+str(i),fixture_id=run.suite_id,
                input_artifact_id=raw.id,expected='1',actual=Observation(status='observed',value='1' if i<=passed else '0',unit='oracle',source='SYNTHETIC'),status='passed' if i<=passed else 'failed',cause='SYNTHETIC predetermined behavioral case',raw_artifact_id=raw.id))
    return m


def review(env,run,comp,raw,criterion,*,verdict=1,completion='completed',measurement_ids=()):
    r,settings,store,f,freeze,backups=env
    previous=[x for x in r.all(CriterionReviewRevision) if x.run_id==run.id and x.criterion==criterion]
    prev=max(previous,key=lambda x:x.revision) if previous else None
    return r.add(CriterionReviewRevision(code='M7-REVIEW-'+str(uuid4()),run_id=run.id,criterion=criterion,compatibility=comp,rubric_id=f['rubric'].id,
        revision=prev.revision+1 if prev else 1,predecessor_id=prev.id if prev else None,completion=completion,
        verdict=BinaryObservation(status='observed',value=verdict,unit='binary',source='SYNTHETIC explicit simulated human judgment') if completion=='completed' else BinaryObservation(status='pending',unit='binary',reason='Open synthetic draft'),
        reason='SYNTHETIC test only; no human scientific approval',file_path=None,lines=None,code_artifact_id=raw.id,
        measurement_ids=measurement_ids,person='TECHNICAL-FIXTURE:simulated human',reviewer_origin='human',reviewed_at=fixtures.NOW))


def absence(env,run,*,proven):
    r,settings,store,f,freeze,backups=env
    execution=str(uuid4());revision=1+len(r.connection.execute('SELECT id FROM evaluation_execution WHERE run_id=?',(str(run.id),)).fetchall())
    job=r.add(Job(code='M7-ABSENCE-JOB-'+execution,phase_id=run.phase_id,run_id=run.id,job_type='measurement',idempotency_key=execution,suite_id=run.suite_id))
    inputs={'candidate_hash':None,'revision':revision,'instrument':INSTRUMENT,'suite_hash':r.get(run.suite_id,AssetVersion).manifest_hash}
    with r.transaction():r.connection.execute('INSERT INTO evaluation_execution VALUES(?,?,?,?,?,?,?,?,?,?)',(execution,str(job.id),str(run.id),None,str(run.suite_id),str(f['tool'].id),'completed',None,canonical(inputs),fixtures.NOW.isoformat()))
    raw=store.json(inputs,run_id=run.id,artifact_type='evaluation_inputs',producer='trusted_evaluator',access_scope='trusted_evaluator')
    source=store.store(b'SYNTHETIC generation completion failure evidence',run_id=run.id,artifact_type='generation_failure_source')
    diagnosis={'cause':'proven_generation_failure','generation_completed':True,'source_artifact_ids':[str(source.id)],'reason':'SYNTHETIC attributed generation content failure'}
    proof=store.json(diagnosis,run_id=run.id,artifact_type='candidate_absence_diagnosis',producer='trusted_evaluator',access_scope='trusted_evaluator') if proven else None
    module=r.get(run.configuration_version_id,ConfigurationVersion).cell.module
    caseraw=store.json({'frozen_module':module},run_id=run.id,artifact_type='evaluation_absence_case_input',producer='trusted_evaluator',access_scope='trusted_evaluator')
    receipt={'entries':[{'case_id':module+'-c'+str(i),'id':module+'-a'+str(i),'category':'R'+str(i),'expected':1,'actual':None,
        'status':'blocked_candidate' if proven else 'technical_missing','cause':'proven_generation_failure' if proven else 'provider_or_infrastructure_no_candidate','input_artifact_id':str(caseraw.id)} for i in range(1,7)],
        'run_id':str(run.id),'candidate_hash':None,'absence_revision':revision,'T':0 if proven else None,'F':{'numerator':0,'denominator':6} if proven else None,
        'T_criteria':{'T1':0 if proven else None,'T2':None,'T3':None,'T4':None,'T5':None},'cause':'proven_generation_failure' if proven else 'provider_or_infrastructure_no_candidate',
        'generation_technical_evidence_ids':[str(x) for x in run.technical_evidence_ids],'attributed_cause_evidence_id':str(proof.id) if proof else None,'attribution':diagnosis if proven else None}
    artifact=store.json(receipt,run_id=run.id,artifact_type='evaluation_absence_receipt',producer='trusted_evaluator',access_scope='trusted_evaluator')
    with r.transaction():
        for aid in (raw.id,artifact.id):r.connection.execute('INSERT INTO evaluation_observation(execution_id,happened_at,artifact_id) VALUES(?,?,?)',(execution,fixtures.NOW.isoformat(),str(aid)))
    return artifact


def time_partition(env,run,*,gap='none'):
    """Persist explicit hand-controlled intervals and a synthetic completed binding.

    No actual graph/tool run is asserted; it tests real register/CAS derivation.
    """
    r,settings,store,f,freeze,backups=env
    from research_env.providers import MockAdapter
    j=CallJournal(r);adapter=MockAdapter()
    preview=j.preview(adapter,run.id,{'currency':'USD','price_as_of':fixtures.NOW.isoformat(),'uncertainty':'SYNTHETIC',
        'categories':{'output':{'expected_units':'1','price_per_unit':'0','source':'SYNTHETIC'}},'pilot_consumption':{'status':'not_collected','reason':'Synthetic'}})
    order=j.record_start_order(run.id,preview.id,person='TECHNICAL-FIXTURE:simulated',decision='SYNTHETIC interval binding',roles=('analyzer',),synthetic_fixture=True)
    manifest=store.json({'schema':'pipeline-v1','run_id':str(run.id),'config_hash':run.effective_hash,'synthetic':True},run_id=run.id,artifact_type='pipeline_manifest')
    job=r.connection.execute("SELECT job_id FROM job_state JOIN register_record ON id=job_id WHERE json_extract(payload,'$.run_id')=? AND json_extract(payload,'$.job_type')='generation'",(str(run.id),)).fetchone()[0]
    with r.transaction():r.connection.execute('INSERT INTO pipeline_binding VALUES (?,?,?,?,?,?,?,?,?,?)',(str(run.id),job,str(manifest.id),manifest.sha256,order,'completed',str(j.process.id),None,None,'SYNTHETIC hand-controlled partition; no executed graph claim'))
    evidence=store.json({'origin':'SYNTHETIC independently hand-assigned time partition','requested_pause_in_active':True,'kind':gap},run_id=run.id,artifact_type='synthetic_interval_evidence')
    processes=[j.process,r.add(ProcessInstance(code='M7-RESTART-'+str(uuid4()),worker='synthetic-restart',software_commit='synthetic',platform='synthetic fixture',started_at=fixtures.NOW,clock_description='Independent process monotonic clock'))]
    parts=[]
    specifications=[('active',0,'100','106'),('pause',0,'106','109'),('outage',0,'109','111'),('active',0,'111','115')] if gap=='none' else [('active',0,'100','106'),('excluded_gap' if gap=='excluded' else 'unknown_active',None,None,None),('active',1,'4','8')]
    for index,(kind,pid,start,end) in enumerate(specifications,1):
        connection = IntervalConnection(previous_interval_id=parts[-1].id,relation='contiguous',evidence_ids=(evidence.id,)) if parts and (pid is None or parts[-1].process_id!=processes[pid].id) else None
        process=processes[pid].id if pid is not None else None
        parts.append(r.add(TimeInterval(code='M7-INTERVAL-'+str(uuid4()),run_id=run.id,sequence=index,kind=kind,process_id=process,end_process_id=process,
            monotonic_start=Decimal(start) if start else None,monotonic_end=Decimal(end) if end else None,started_at=None,ended_at=None,
            evidence_ids=(evidence.id,),reason='SYNTHETIC known disjoint active/pause/outage boundary evidence',connection=connection)))
    return parts


def resource_evidence(env, run, kind):
    """Real additive register/CAS edges for synthetic integrity counterchecks.

    The interval controls add exactly one known active second. No actual tool,
    provider response or scientific measurement is claimed.
    """
    r,settings,store,f,freeze,backups=env
    owner = f['pilot'] if kind=='pilot_metric' else run
    artifact = store.json({'origin':'SYNTHETIC resource integrity proof','kind':kind,'nonce':str(uuid4())},
        run_id=None if kind=='phase_metric' else owner.id, artifact_type='pipeline_time_boundary' if kind in ('interval','connection') else 'synthetic_resource_evidence')
    if kind in ('interval','connection'):
        previous=max((x for x in r.all(TimeInterval) if x.run_id==run.id),key=lambda x:x.sequence)
        record=r.add(previous.model_copy(update={'id':uuid4(),'code':'M7-PROOF-INTERVAL-'+str(uuid4()),'sequence':previous.sequence+1,
            'monotonic_start':previous.monotonic_end,'monotonic_end':previous.monotonic_end+Decimal(1),
            'evidence_ids':(artifact.id,) if kind=='interval' else previous.evidence_ids,
            'connection':IntervalConnection(previous_interval_id=previous.id,relation='contiguous',evidence_ids=(artifact.id,) if kind=='connection' else previous.evidence_ids)}))
    elif kind in ('metric','phase_metric','pilot_metric'):
        record=r.add(MetricObservation(code='M7-PROOF-METRIC-'+str(uuid4()),run_id=None if kind=='phase_metric' else owner.id,
            phase_id=owner.phase_id,metric='context_preparation_seconds',value=NumericObservation(status='observed',value=Decimal('0.25'),unit='s',source='SYNTHETIC independent hand observation'),
            measurement_id=None,interval_ids=(),file_scope=('synthetic',),excluded_files=(),configuration_hash=None if kind=='phase_metric' else owner.effective_hash,diagnostics_artifact_id=artifact.id))
    else:
        journal=CallJournal(r);adapter=MockAdapter()
        order=r.connection.execute('SELECT order_id FROM pipeline_binding WHERE run_id=?',(str(run.id),)).fetchone()[0]
        call=journal.prepare(adapter,run.id,'analyzer',order_id=order,messages=[{'role':'user','content':'SYNTHETIC prepared only; no inference'}],
            input_artifact_ids=(artifact.id,) if kind=='call_input' else ())
        if kind=='call_message':
            artifact=r.get(call.messages_artifact_id,Artifact);record=call
        elif kind=='call_input':
            record=call
        elif kind=='resource_profile':
            transport=r.all(TransportAttempt)[-1]
            cost=r.add(CostEntry(code='M7-PROOF-COST-'+str(uuid4()),run_id=run.id,transport_id=transport.id,operating_area=run.purpose,
                amount=NonnegativeObservation(status='estimated',value=Decimal('0.25'),unit='USD',source='SYNTHETIC hand estimate, no provider cost'),
                currency='USD',price_evidence_id=artifact.id,billing_evidence_ids=(),uncertainty='Synthetic integrity fixture only'))
            unknown=NonnegativeObservation(status='not_collected',unit='s',reason='Synthetic not collected')
            usage=TokenUsage(**{k:CountObservation(status='not_collected',unit='tokens',reason='Synthetic not collected') for k in
                ('input_tokens','output_tokens','cache_read_tokens','cache_write_tokens','reasoning_tokens','other_tokens')},completeness='missing')
            record=r.add(ResourceProfile(code='M7-PROOF-PROFILE-'+str(uuid4()),run_id=run.id,phase_id=run.phase_id,operating_area=run.purpose,
                token_usage=usage,transport_ids=(transport.id,),cost_entry_ids=(cost.id,),interval_ids=(),context_preparation_completeness='missing',
                repair_count=BinaryObservation(status='not_collected',unit='count',reason='Synthetic not collected'),
                **{k:unknown for k in ResourceProfile.model_fields if k.endswith('_seconds')}))
        else:
            raise ValueError('Unknown synthetic resource proof kind')
    return record,artifact

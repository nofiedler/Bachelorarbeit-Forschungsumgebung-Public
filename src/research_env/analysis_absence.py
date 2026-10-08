"""Bridge M6's actual immutable no-candidate receipts into M7 selection.

The receipt is a measurement of attributed absence, not a candidate or a human
T2–T4 review. Its invalidation uses the existing additive revision event contract.
"""
from .numeric import deterministic
import json
from uuid import UUID

from .artifacts import ArtifactStore, IntegrityError
from .domain import Artifact, AssetVersion, ConfigurationVersion, Run, digest


@deterministic
def receipt_binding(register, artifact, *, main_only=True):
    if artifact.artifact_type!='evaluation_absence_receipt' or artifact.producer!='trusted_evaluator' or not artifact.run_id:
        raise IntegrityError('Not a trusted main absence measurement')
    run = register.get(artifact.run_id, Run)
    expected_scope='trusted_evaluator' if register.get(run.suite_id,AssetVersion).suite_kind=='study_holdout' else 'trusted_register'
    if artifact.access_scope!=expected_scope or (main_only and expected_scope!='trusted_evaluator'):
        raise IntegrityError('Absence receipt has wrong evaluation scope')
    state = register.state(run.id)
    conf = register.get(run.configuration_version_id, ConfigurationVersion)
    if (main_only and run.purpose!='main') or state.seal!='no_candidate' or state.execution!='terminal' or state.candidate_id:
        raise IntegrityError('Receipt incompatible with actual main absence state')
    store = ArtifactStore(register.settings, register)
    body = json.loads(store.read(artifact.id))
    executions = register.connection.execute('SELECT e.* FROM evaluation_execution e JOIN evaluation_observation o ON e.id=o.execution_id WHERE o.artifact_id=?', (str(artifact.id),)).fetchall()
    if len(executions)!=1:
        raise IntegrityError('Receipt needs exact evaluator execution binding')
    execution = executions[0]
    if execution['status']!='completed' or execution['candidate_id'] or execution['run_id']!=str(run.id) or execution['suite_id']!=str(run.suite_id) or not register.compatible_evaluation_tool(run.id,UUID(execution['tool_id'])):
        raise IntegrityError('Invalid completed absence execution')
    inputs = [register.get(row[0], Artifact) for row in register.connection.execute('SELECT artifact_id FROM evaluation_observation WHERE execution_id=?', (execution['id'],)) if register.get(row[0], Artifact).artifact_type=='evaluation_inputs']
    if len(inputs)!=1:
        raise IntegrityError('Exact immutable evaluator input missing')
    original = json.loads(store.read(inputs[0].id))
    suite = register.get(run.suite_id, AssetVersion)
    tool = register.get(UUID(execution['tool_id']),AssetVersion)
    if original!=json.loads(execution['body']) or original['candidate_hash'] is not None or original['suite_hash']!=suite.manifest_hash or digest(original['instrument'])!=tool.manifest_hash:
        raise IntegrityError('Mutable projection/input/suite/tool drift')
    if type(original['revision']) is not int or original['revision']<1 or body['absence_revision']!=original['revision'] or body['run_id']!=str(run.id) or body['candidate_hash'] is not None:
        raise IntegrityError('Immutable absence sequence or identity mismatch')
    # A declared defect of this tool invalidates all its absence observations.
    for evidence in register.all(Artifact):
        if evidence.artifact_type=='evaluation_instrument_correction' and evidence.producer=='trusted_evaluator':
            correction = json.loads(store.read(evidence.id))
            if correction.get('old_tool_id')==str(tool.id):
                raise IntegrityError('Affected absence tool invalidated by declared correction')
    if body['cause']=='proven_generation_failure':
        aid = body.get('attributed_cause_evidence_id')
        if state.terminal_cause!='content_failure' or not aid:
            raise IntegrityError('F0 requires actual attributed content failure')
        proof = register.get(UUID(aid),Artifact)
        diagnosis = json.loads(store.read(proof.id))
        if proof.run_id!=run.id or proof.producer!='trusted_evaluator' or proof.artifact_type!='candidate_absence_diagnosis' or diagnosis!=body['attribution'] or diagnosis.get('cause')!='proven_generation_failure' or diagnosis.get('generation_completed') is not True or not diagnosis.get('reason') or not diagnosis.get('source_artifact_ids'):
            raise IntegrityError('Independent actual same-run absence proof missing')
        for source_id in diagnosis['source_artifact_ids']:
            source = register.get(UUID(source_id),Artifact)
            if source.run_id!=run.id:
                raise IntegrityError('Foreign absence source')
            store.read(source.id)
        if body['T']!=0 or body['F']!={'numerator':0,'denominator':6} or body['T_criteria']['T1']!=0 or any(body['T_criteria'][k] is not None for k in ('T2','T3','T4','T5')):
            raise IntegrityError('Absence does not authorize artificial human judgments')
        if any(x['status']!='blocked_candidate' or x['cause']!='proven_generation_failure' for x in body['entries']):
            raise IntegrityError('Attributed absence needs uniformly blocked candidate assertions')
    elif body['T'] is not None or body['F'] is not None:
        raise IntegrityError('Technical/manual/unknown absence cannot imply F0')
    elif any(x['status']!='technical_missing' for x in body['entries']):
        raise IntegrityError('Unproven absence cannot imply R0')
    if body['generation_technical_evidence_ids']!=[str(x) for x in run.technical_evidence_ids]:
        raise IntegrityError('Absence generation evidence mismatch')
    return {'revision': original['revision'], 'receipt': body, 'input_id': str(inputs[0].id),
            'tool_id': str(tool.id), 'configuration_id': str(conf.id), 'configuration_hash': conf.content_hash,
            'phase_id': str(run.phase_id), 'suite_id': str(run.suite_id)}


@deterministic
def select_absence(register, run, *, main_only=True):
    candidates = []
    for artifact in register.all(Artifact):
        if artifact.run_id!=run.id or artifact.artifact_type!='evaluation_absence_receipt' or not register.valid(artifact.id):
            continue
        try:
            binding = receipt_binding(register, artifact, main_only=main_only)
        except (IntegrityError, KeyError, ValueError, TypeError):
            continue
        candidates.append((artifact, binding))
    revisions = [x[1]['revision'] for x in candidates]
    if len(revisions)!=len(set(revisions)):
        raise IntegrityError('Duplicate absence measurement revision')
    chosen = max(candidates, key=lambda x:x[1]['revision']) if candidates else None
    return {'revision_id': str(chosen[0].id) if chosen else None, 'valid_at_confirmation': bool(chosen),
            'missing': None if chosen else 'no_valid_completed_compatible_absence_receipt'}

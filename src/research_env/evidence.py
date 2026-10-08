"""The single executable evidence catalog, derived from the persisted contracts.

Each field and nested observation is individually mapped. UI/export consume catalog()
and validate_document(); they must not maintain a second checklist. Generated JSON
is a versioned build artifact; assert_catalog_current detects contract/schema drift.
"""
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
from typing import Annotated, get_args, get_origin
from uuid import UUID

from pydantic import BaseModel, TypeAdapter

from .domain import ENTITIES, JobClaim, Observation, RunState, PhaseState, TransportState, canonical

SCHEMA_VERSION = 'M3-evidence-v1'
CONTRACTS = {**ENTITIES, 'RunState': RunState, 'JobClaim': JobClaim, 'PhaseState': PhaseState, 'TransportState': TransportState}
SOURCES = {
    'Study': (23, 35), 'StudyPhase': (23, 35), 'AssetVersion': (23, 26, 28, 30),
    'ModelPackage': (23, 24, 25), 'Configuration': (23, 46), 'ConfigurationVersion': (23, 46),
    'Approval': (23, 32), 'Freeze': (23, 32, 33), 'Series': (23, 49), 'Block': (23, 35), 'PlannedRun': (23, 35),
    'Run': (23, 24, 35), 'RunState': (23, 26, 28), 'ModelCall': (24, 25), 'TransportAttempt': (24, 25),
    'TransportStateEvent': (24, 25), 'Event': (24, 25), 'Job': (24, 32), 'JobClaim': (24, 32),
    'Artifact': (26, 32), 'CandidateSnapshot': (26, 32), 'MeasurementAttempt': (28, 30, 32, 34),
    'TestResult': (28, 32), 'CriterionReviewRevision': (28, 32, 34, 50), 'RevisionInvalidation': (32, 34, 50),
    'MetricObservation': (25, 30, 31, 32), 'CostEntry': (25, 32), 'TimeInterval': (25, 32),
    'Backup': (32, 33), 'BackupReceipt': (33,), 'AnalysisRun': (34, 35, 50), 'Package': (32, 35),
    'ResourceProfile': (25, 31, 32), 'StaticProfile': (30, 31, 32), 'FunctionalProfile': (28, 32, 34),
    'ProcessInstance': (24, 25), 'RunStateRevision': (23, 24, 26, 28, 32), 'RetryEvidence': (24,25), 'PhaseState': (23,32,49), 'PhaseStateRevision': (23,24,32,49), 'TransportState': (24,25,32),
}
DUE = {'Study': 'preparation', 'StudyPhase': 'preparation', 'Configuration': 'preparation',
       'ConfigurationVersion': 'configuration', 'AssetVersion': 'preparation', 'ModelPackage': 'before_paid_call',
       'Approval': 'before_start', 'Freeze': 'freeze', 'Series': 'freeze', 'Block': 'freeze', 'PlannedRun': 'freeze',
       'Run': 'start', 'RunState': 'execution', 'Job': 'job_creation', 'JobClaim': 'claim', 'ModelCall': 'before_dispatch',
       'TransportAttempt': 'before_dispatch', 'TransportStateEvent': 'transport_progress', 'Event': 'event',
       'Artifact': 'artifact_registration', 'CandidateSnapshot': 'snapshot', 'MeasurementAttempt': 'measurement',
       'TestResult': 'measurement', 'CriterionReviewRevision': 'review', 'RevisionInvalidation': 'defect_confirmation',
       'MetricObservation': 'measurement', 'CostEntry': 'billing', 'TimeInterval': 'interval_completion',
       'Backup': 'before_first_and_each_next_main', 'BackupReceipt': 'before_first_and_each_next_main',
       'AnalysisRun': 'analysis_confirmation', 'Package': 'export', 'ResourceProfile': 'resource_collection',
       'StaticProfile': 'static_measurement', 'FunctionalProfile': 'external_measurement', 'ProcessInstance': 'process_start', 'RunStateRevision': 'status_transition', 'RetryEvidence': 'before_identical_transport_retry', 'PhaseState': 'phase_lifecycle', 'PhaseStateRevision': 'phase_status_transition', 'TransportState': 'transport_lifecycle'}


# Storage adapter mapping, not a second checklist: points solely to catalog contracts.
SQL_PROJECTIONS = {
    'phase_state': {'phase_id':'PhaseState.phase_id', 'status':'PhaseState.status',
        'substantive_revision':'PhaseState.substantive_revision', 'reason':'PhaseState.reason'},
    'run_binding': {'run_id':'Run.id', 'phase_id':'Run.phase_id', 'purpose':'Run.purpose',
        'planned_id':'Run.planned_run_id', 'execution':'RunState.execution', 'state_json':'$contract:RunState'},
    'job_state': {'job_id':'Job.id', 'idempotency_key':'Job.idempotency_key', 'status':'JobClaim.status',
        'worker':'JobClaim.worker', 'claimed_at':'JobClaim.claimed_at', 'heartbeat_at':'JobClaim.heartbeat_at'},
    'transport_state': {'transport_id':'TransportState.transport_id', 'status':'TransportState.status', 'sequence':'TransportState.sequence'},
}


def assert_projection_coverage(connection):
    keys = {entry['evidence_key'] for entry in catalog()['entries']}
    for table, columns in SQL_PROJECTIONS.items():
        actual = {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}
        if actual != set(columns):
            raise ValueError(f'Unmapped SQL projection columns: {table}: {actual ^ set(columns)}')
        for target in columns.values():
            if target.startswith('$contract:'):
                if target.split(':',1)[1] not in CONTRACTS:
                    raise ValueError(f'Unknown projection contract: {target}')
            elif target not in keys:
                raise ValueError(f'Uncatalogued SQL projection: {target}')
    return True


def _walk(cls, prefix=''):
    for name, field in cls.model_fields.items():
        path = f'{prefix}.{name}' if prefix else name
        annotation = Annotated[field.annotation, *field.metadata] if field.metadata else field.annotation
        yield path, annotation, field
        candidates = [field.annotation, *get_args(field.annotation)]
        for item in candidates:
            if isinstance(item, type) and issubclass(item, BaseModel):
                yield from _walk(item, path)
                break
        if get_origin(field.annotation) is dict:
            value_type = get_args(field.annotation)[1]
            yield path + '.*', value_type, field
            if isinstance(value_type, type) and issubclass(value_type, BaseModel):
                yield from _walk(value_type, path + '.*')
        if get_origin(field.annotation) is tuple:
            value_type = get_args(field.annotation)[0]
            if value_type is not Ellipsis:
                yield path + '.*', value_type, field
                if isinstance(value_type, type) and issubclass(value_type, BaseModel):
                    yield from _walk(value_type, path + '.*')


def _unit(entity, path):
    leaf = path.split('.')[-1]
    parents = path.split('.')
    if any('seconds' in p or 'monotonic' in p for p in parents):
        return 's' if leaf in ('value', '*') or leaf == parents[-1] else 'status/source of seconds observation'
    if any('tokens' in p for p in parents):
        return 'token (provider-specific category)'
    if entity == 'StaticProfile':
        if parents[0] == 'd':
            return 'diagnostic count'
        if parents[0] in ('l', 'l_by_file'):
            return 'non-comment physical code lines (NCLOC), fixed tokenizer rule'
        if parents[0] == 's':
            return 'diagnostics per 100 NCLOC'
    if entity == 'FunctionalProfile' and parents[0] in ('t1','t2','t3','t4','t5','t','r1','r2','r3','r4','r5','r6','full_success'):
        return 'binary 0/1'
    if entity == 'FunctionalProfile' and parents[0] in ('r','f','raw_pass_ratio'):
        return 'fraction 0..1'
    if entity == 'CostEntry' and parents[0] == 'amount' or entity == 'ModelPackage' and parents[0] == 'price' or entity == 'Freeze' and parents[0] == 'estimated_cost':
        return 'Decimal amount in linked currency; price basis explicitly recorded'
    if leaf.endswith('_at'):
        return 'UTC ISO8601 with timezone'
    if leaf.endswith('_hash') or leaf in ('sha256','asset_hash','scope_hash'):
        return 'SHA-256'
    if leaf == 'byte_count':
        return 'byte'
    if leaf.endswith('_id') or leaf == 'id' or leaf.endswith('_ids'):
        return 'UUID (immutable reference)'
    if leaf in ('revision','sequence','position','actual_position','index','version','r_c','r_e','repair_count','number'):
        return 'integer count/index'
    return 'dimensionless / literal text or explicitly linked Observation.unit'


def _due(entity, path):
    first = path.split('.')[0]
    if entity == 'TransportAttempt':
        if first in ('ended_at','actual_model','actual_provider','response_artifact_id','error_artifact_id','usage','billing_status','format_status','generation_id','request_id'):
            return 'transport_result_or_explicit_missing; collected by immutable TransportStateEvent'
        if first == 'sent_at':
            return 'dispatch_journal'
    if entity == 'RunState':
        if first in ('ended_at','terminal_cause'):
            return 'terminal'
        if first in ('candidate_id','seal'):
            return 'seal_or_reasoned_no_candidate'
        if first in ('evaluation','result'):
            return 'external_and_manual_evaluation; pending explicitly allowed'
    if entity == 'CandidateSnapshot' and first == 'repair_count':
        return 'seal; observed 0/1 from actual node journal'
    if entity == 'Package' and first == 'zip_hash':
        return 'package_written'
    return DUE[entity]


def _source(entity, path):
    if entity in ('StaticProfile', 'MeasurementAttempt', 'TestResult'):
        return f'independent evaluator raw log/fixture/tool output linked by candidate and attempt; field {path}'
    if entity in ('CriterionReviewRevision', 'RevisionInvalidation'):
        return f'dated reviewer/defect decision plus concrete code or measurement artifacts; field {path}'
    if entity in ('TransportAttempt', 'TransportStateEvent', 'ModelCall'):
        return f'persisted pre-dispatch request journal and provider raw response/metadata, never inferred missing usage; field {path}'
    if entity in ('TimeInterval', 'ResourceProfile', 'ProcessInstance'):
        return f'process-local monotonic measurement and separate UTC/event evidence; excluded gaps explicit; field {path}'
    if entity in ('CostEntry', 'ModelPackage'):
        return f'provider usage/billing or explicitly labeled estimate with dated native price/metadata evidence; field {path}'
    if entity == 'BackupReceipt':
        return f'dated human self-report bound to exact backup hash, no hardware attestation; field {path}'
    return f'persisted {entity} contract / immutable record or operational projection; field {path}'


def _applicability(entity, path, field):
    if entity in ('ModelCall', 'TransportAttempt', 'TransportStateEvent'):
        return 'Started run and active configured role; never-started: not_collected; disabled role: not_applicable'
    if entity == 'CriterionReviewRevision':
        return 'Sealed candidate, T1–T5; T2–T4 human required; draft reason required; completed verdict/evidence required'
    if entity in ('MeasurementAttempt', 'TestResult', 'FunctionalProfile', 'StaticProfile'):
        return 'Compatible sealed candidate; study_holdout for study; development only for free_test'
    if entity == 'CandidateSnapshot' and 'repair' in path:
        return 'Repair count 0/1; post_repair only when triggered; same internal tests'
    if entity in ('Backup', 'BackupReceipt'):
        return 'Freeze before first Main; current substantive run/measurement/review revision before each subsequent Main'
    if entity in ('AnalysisRun', 'Package', 'Block', 'PlannedRun', 'Freeze', 'Series'):
        return 'Frozen main phase only; free_test excluded; pilot/preparation only explicit separate provenance'
    return 'Required when entity exists' if field.is_required() else 'Optional or pending in draft; enclosing contract decides applicability'


def catalog():
    entries = []
    for entity, cls in CONTRACTS.items():
        for path, annotation, field in _walk(cls):
            adapter = TypeAdapter(annotation)
            entries.append({'evidence_key': f'{entity}.{path}', 'label': f'{entity}: {path}',
                'path': f'{entity}.{path}', 'sql_sources': [f'{table}.{column}' for table, columns in SQL_PROJECTIONS.items() for column, target in columns.items() if target == f'{entity}.{path}' or target == f'$contract:{entity}'], 'type': adapter.json_schema() or {'type': ['array', 'boolean', 'null', 'number', 'object', 'string'], 'description': 'Provider-specific JSON; canonical finite JSON is validated by containing contract'}, 'unit': _unit(entity, path),
                'source': _source(entity, path),
                'applicability': _applicability(entity, path, field), 'duephase': _due(entity, path),
                'validator': {'function': 'research_env.evidence.validate_entry', 'contract': entity, 'field_path': path,
                              'whole_contract_validator': 'research_env.evidence.validate_document'},
                'missingreasons': ['not_due', 'disabled_role', 'repair_not_triggered', 'run_not_started', 'candidate_missing',
                    'not_collected', 'technical_missing', 'not_applicable', 'pending', 'unresolved',
                    'invalidated_revision', 'incompatible_version', 'damaged_artifact', 'redacted_with_reason'],
                'mapping': {'plan_4_1': f'{entity} entity/field {path}', 'plan_8_2': 'individual required field catalog',
                            'operationalization_9': 'data and reproducibility / linked dataset',
                            'requirements': list(SOURCES[entity])}})
    return {'schema_version': SCHEMA_VERSION, 'sql_projections': SQL_PROJECTIONS, 'entries': entries}


def validate_document(entity: str, document: dict):
    validated = CONTRACTS[entity].model_validate(document).model_dump(mode='json')
    canonical(validated)  # Reject non-JSON data and nonfinite values even inside provider-specific maps.
    return validated


def validate_entry(key: str, document: dict):
    """Validate actual containing data and every wildcard leaf, not decorative strings."""
    entity, path = key.split('.', 1)
    validated = validate_document(entity, document)
    field_map = {p: annotation for p, annotation, _ in _walk(CONTRACTS[entity])}
    if path not in field_map:
        raise KeyError(key)
    parts = path.split('.')
    adapter = TypeAdapter(field_map[path])
    return [adapter.validate_python(v) for v in field_values(validated, parts)]


def field_contexts(node, parts, ancestors=()):
    """Catalog paths, including its implicit tuple/dictionary model projections.

    Ancestors retain containing Observations for faithful status/reason display.
    The caller validates the enclosing contract and catalog key before projection.
    """
    if not parts:
        yield node, ancestors
    elif node is None:
        return
    elif parts[0] == '*':
        for item in node.values() if isinstance(node, dict) else node:
            yield from field_contexts(item, parts[1:], ancestors+(node,))
    elif isinstance(node, list) or isinstance(node, dict) and parts[0] not in node:
        for item in node.values() if isinstance(node, dict) else node:
            yield from field_contexts(item, parts, ancestors+(node,))
    else:
        yield from field_contexts(node[parts[0]], parts[1:], ancestors+(node,))


def field_values(node, parts):
    for value, _ in field_contexts(node, parts):
        yield value


def assert_catalog_current(path: Path):
    if json.loads(path.read_text()) != catalog():
        raise ValueError('evidence_schema drift: regenerate from contracts')


if __name__ == '__main__':
    import sys
    Path(sys.argv[1]).write_text(json.dumps(catalog(), ensure_ascii=False, indent=2) + '\n')

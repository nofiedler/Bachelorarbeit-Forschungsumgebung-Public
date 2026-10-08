"""M3 immutable data contracts. No model, object-store or evaluator execution."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Zeitpunkt benötigt Zeitzone')
    return value.astimezone(timezone.utc)


UTC = Annotated[datetime, AfterValidator(utc)]
SHA256 = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
Positive = Annotated[int, Field(strict=True, gt=0)]
Nonnegative = Annotated[int, Field(strict=True, ge=0)]
Origin = Literal['empirical', 'synthetic']
Purpose = Literal['preparation', 'pilot', 'main', 'demo', 'free_test']
SuiteKind = Literal['development', 'study_holdout']
ValueStatus = Literal['observed', 'estimated', 'not_applicable', 'not_collected',
                      'technical_missing', 'pending', 'unresolved']


def canonical(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode='json')
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False)


class Observation(Contract):
    status: ValueStatus
    value: Decimal | str | bool | None = None
    value_kind: Literal['decimal', 'text', 'boolean'] | None = None
    unit: str
    source: str | None = None
    reason: str | None = None

    @model_validator(mode='before')
    @classmethod
    def preserve_value_kind(cls, data):
        if isinstance(data, BaseModel):
            data = data.model_dump(mode='python')
        if not isinstance(data, dict):
            return data
        data = dict(data)
        value = data.get('value')
        kind = data.get('value_kind')
        if value is None:
            if kind is not None:
                raise ValueError('Fehlwert hat keine Wertart')
        elif kind == 'decimal':
            if isinstance(value, bool) or isinstance(value, float) or not isinstance(value, (str, int, Decimal)):
                raise ValueError('Decimal ohne Boolean/Float-Konvertierung erforderlich')
            data['value'] = Decimal(value)
        elif kind is None:
            if isinstance(value, bool):
                data['value_kind'] = 'boolean'
            elif isinstance(value, (Decimal, int)):
                data['value_kind'] = 'decimal'
                data['value'] = Decimal(value)
            elif isinstance(value, str):
                data['value_kind'] = 'text'
            else:
                raise ValueError('Wert benötigt Decimal, Text oder Boolean; keine Float-Präzisionsverluste')
        return data

    @model_validator(mode='after')
    def evidence(self):
        if self.status in ('observed', 'estimated'):
            if self.value is None or not self.source:
                raise ValueError('Wert benötigt belegte Mess-/Schätzquelle')
        elif self.value is not None or not self.reason:
            raise ValueError('Fehlwert benötigt Grund und value=null')
        if self.value is not None:
            expected = {'decimal': Decimal, 'text': str, 'boolean': bool}.get(self.value_kind)
            if expected is None or type(self.value) is not expected:
                raise ValueError('Wertart und tatsächlicher Wert passen nicht')
        return self


class NumericObservation(Observation):
    value: Decimal | None = None
    value_kind: Literal['decimal'] | None = None

    @model_validator(mode='after')
    def numeric(self):
        if self.value is not None and (type(self.value) is not Decimal or not self.value.is_finite()):
            raise ValueError('Endlicher Decimalwert erforderlich')
        return self


class TextObservation(Observation):
    value: str | None = None
    value_kind: Literal['text'] | None = None


class IntegerObservation(NumericObservation):
    @model_validator(mode='after')
    def integer(self):
        if self.value is not None and self.value != self.value.to_integral_value():
            raise ValueError('Ganzzahliger Wert erforderlich')
        return self


class NonnegativeObservation(NumericObservation):
    value: Annotated[Decimal, Field(ge=0)] | None = None

    @model_validator(mode='after')
    def nonnegative(self):
        if self.value is not None and self.value < 0:
            raise ValueError('Nichtnegativer Wert erforderlich')
        return self


class CountObservation(NonnegativeObservation):
    value: Annotated[Decimal, Field(ge=0, multiple_of=1)] | None = None

    @model_validator(mode='after')
    def count(self):
        if self.value is not None and self.value != self.value.to_integral_value():
            raise ValueError('Ganzzahliger Count erforderlich')
        return self


class BinaryObservation(NumericObservation):
    value: Annotated[Decimal, Field(ge=0, le=1, multiple_of=1)] | None = None

    @model_validator(mode='after')
    def binary(self):
        if self.value is not None and self.value not in (Decimal(0), Decimal(1)):
            raise ValueError('Binäres Urteil 0/1 erforderlich')
        if self.status == 'estimated':
            raise ValueError('Kriterienurteil darf keine Schätzung sein')
        return self


class FractionObservation(NumericObservation):
    value: Annotated[Decimal, Field(ge=0, le=1)] | None = None

    @model_validator(mode='after')
    def fraction(self):
        if self.value is not None and not Decimal(0) <= self.value <= Decimal(1):
            raise ValueError('Anteil 0..1 erforderlich')
        return self


class Entity(Contract):
    id: UUID = Field(default_factory=uuid4)
    code: str = Field(min_length=1)
    created_at: UTC = Field(default_factory=lambda: datetime.now(timezone.utc))
    schema_version: Literal[1] = 1


class Study(Entity):
    title: str
    design_version: str
    data_origin: Origin
    provenance: str


class StudyPhase(Entity):
    study_id: UUID
    purpose: Purpose
    provenance: str
    predecessor_id: UUID | None = None


class PhaseState(Contract):
    phase_id: UUID
    status: Literal['draft', 'ready', 'running', 'pause_requested', 'paused', 'completed', 'stopped']
    substantive_revision: Nonnegative
    reason: str


class PhaseStateRevision(Entity):
    phase_id: UUID
    predecessor_id: UUID | None
    old_state: PhaseState
    new_state: PhaseState
    reason: str


class AssetVersion(Entity):
    asset_type: Literal['contract', 'scaffold', 'source_context', 'prompt', 'handoff',
                        'suite', 'fixture_suite', 'reference', 'tool', 'analysis', 'dependency']
    manifest_hash: SHA256
    artifact_ids: tuple[UUID, ...] = ()
    origin: str
    access_scope: Literal['role_package', 'shared_scaffold', 'public_development', 'trusted_evaluator', 'trusted_register']
    suite_kind: SuiteKind | None = None
    human_review: Literal['open', 'approved'] = 'open'

    @model_validator(mode='after')
    def access(self):
        if self.suite_kind == 'study_holdout' and self.access_scope != 'trusted_evaluator':
            raise ValueError('Holdout bleibt im vertrauenswürdigen Evaluator')
        if self.suite_kind == 'development' and self.access_scope != 'public_development':
            raise ValueError('Development benötigt öffentlichen getrennten Scope')
        return self


class ModelPackage(Entity):
    exact_model_id: str
    endpoint: str
    upstream: str
    routing: dict[str, Any]
    fallback: dict[str, Any]
    supported_parameters: tuple[str, ...]
    effective_parameters: dict[str, Any]
    context_limit: CountObservation
    output_limit: CountObservation
    price: NonnegativeObservation
    currency: str
    price_as_of: UTC
    metadata_evidence_ids: tuple[UUID, ...]
    version_uncertainty: str


class Configuration(Entity):
    phase_id: UUID


class Cell(Contract):
    module: Literal['BF', 'SQL', 'UP']
    context: Literal['K0', 'K1']
    producer: Literal['A', 'B']
    verifier: Literal['A', 'B']
    planner: bool
    review: bool


MAIN_CELLS = {
    **{f'C-{m}-{k}': Cell(module=m, context=f'K{k}', producer='A', verifier='A', planner=True, review=True)
       for m in ('BF', 'SQL', 'UP') for k in (0, 1)},
    **{f'E-{p}{v}': Cell(module='SQL', context='K1', producer=p, verifier=v, planner=True, review=True)
       for p, v in (('A', 'B'), ('B', 'A'), ('B', 'B'))},
    **{f'E-P{p}R{r}': Cell(module='SQL', context='K1', producer='A', verifier='A', planner=bool(p), review=bool(r))
       for p, r in ((0, 1), (1, 0), (0, 0))},
}


class EffectiveSettings(Contract):
    model_a: UUID | None = None
    model_b: UUID | None = None
    prompt_ids: tuple[UUID, ...] = ()
    handoff_id: UUID | None = None
    contract_id: UUID | None = None
    scaffold_id: UUID | None = None
    rubric_id: UUID | None = None
    context_ids: dict[str, UUID] = Field(default_factory=dict)
    development_suite_id: UUID | None = None
    holdout_suite_id: UUID | None = None
    reference_id: UUID | None = None
    tool_ids: tuple[UUID, ...] = ()
    analysis_id: UUID | None = None
    role_parameters: dict[str, dict[str, Any]] | None = None
    provider_limits_defaults: dict[str, Any] | None = None
    security_resources: dict[str, Any] | None = None
    retry_interval_seconds: Decimal | None = Field(default=None, ge=0)
    dvwa_commit: str | None = None
    working_copy_evidence_id: UUID | None = None
    lockfile_hashes: dict[str, SHA256] = Field(default_factory=dict)
    software_commit: str | None = None
    hardware: str | None = None
    container_hashes: dict[str, SHA256] = Field(default_factory=dict)

    def open_fields(self) -> tuple[str, ...]:
        return tuple(k for k, v in self.model_dump().items() if v is None or v == () or v == {})


class ConfigurationVersion(Entity):
    configuration_id: UUID
    parent_id: UUID | None
    parent_hash: SHA256 | None
    cell_key: str
    cell: Cell
    settings: EffectiveSettings
    content_hash: SHA256
    change_diff: dict[str, Any]

    @model_validator(mode='after')
    def hashes(self):
        if self.content_hash != digest({'cell': self.cell.model_dump(mode='json'), 'settings': self.settings.model_dump(mode='json')}):
            raise ValueError('Konfigurationshash passt nicht')
        if (self.parent_id is None) != (self.parent_hash is None):
            raise ValueError('Eltern-ID und Elternhash zusammen')
        return self


class EvidenceProof(Contract):
    key: str
    asset_id: UUID
    asset_hash: SHA256
    scope_hash: SHA256


# Complete start requirements are separately scoped. No score or budget formula.
TECHNICAL_GATES = ('reference_counterexamples', 'suite_validation', 'package_information', 'isolation', 'mock_validation')
HUMAN_GATES = ('contract_review', 'instrument_review', 'feasibility', 'freeze_review')
RESOURCE_GATES = ('pilot_consumption', 'resource_estimate', 'scope_decision', 'backup_destination')
REQUIRED_GATES = TECHNICAL_GATES + HUMAN_GATES + RESOURCE_GATES


class Approval(Entity):
    schema_version: Literal[1, 2] = 1
    phase_id: UUID
    kind: Literal['technical', 'subject', 'cost']
    scope_hash: SHA256
    person: str
    reason: str
    evidence: tuple[EvidenceProof, ...]
    paid_calls_consent: bool = False
    synthetic_fixture: bool = False


class FreezeResources(Contract):
    resource_decision: str
    consumption_evidence_id: UUID | None
    consumption_evidence_hash: SHA256 | None
    pilot_run_ids: tuple[UUID, ...]
    estimated_cost: NonnegativeObservation
    estimated_time: NonnegativeObservation
    backup_destination: str


class Freeze(Entity):
    schema_version: Literal[1, 2] = 1
    phase_id: UUID
    configuration_version_ids: dict[str, UUID]
    r_c: Positive
    r_e: Positive
    seed: str
    algorithm: Literal['sha256-fisher-yates-v1']
    matrix: tuple[dict[str, Any], ...]
    scope_hash: SHA256
    evidence: tuple[EvidenceProof, ...]
    approval_ids: tuple[UUID, ...]
    selection_rule: Literal['last_completed_valid_compatible-v1']
    resource_decision: str
    consumption_evidence_id: UUID | None
    consumption_evidence_hash: SHA256 | None
    pilot_run_ids: tuple[UUID, ...]
    estimated_cost: NonnegativeObservation
    estimated_time: NonnegativeObservation
    backup_destination: str
    freeze_hash: SHA256

    @model_validator(mode='after')
    def validate_freeze(self):
        if self.r_e > self.r_c or set(self.configuration_version_ids) != set(MAIN_CELLS):
            raise ValueError('Zwölf Zellen und r_E≤r_C erforderlich')
        if not self.resource_decision or not self.backup_destination:
            raise ValueError('Manuelle Umfangs-/Sicherungsentscheidung fehlt')
        if self.schema_version == 1:
            if not self.pilot_run_ids or not self.consumption_evidence_id or not self.consumption_evidence_hash:
                raise ValueError('Historischer Freeze benötigt Pilotverbrauch')
            if self.estimated_cost.status != 'estimated' or self.estimated_time.status != 'estimated':
                raise ValueError('Aufwand vor Freeze ausdrücklich Schätzung')
        elif self.pilot_run_ids or self.consumption_evidence_id or self.consumption_evidence_hash:
            raise ValueError('Vereinfachter Freeze enthält keine Pflichtpilotbelege')
        elif any(v.status != 'not_collected' for v in (self.estimated_cost, self.estimated_time)):
            raise ValueError('Ohne Pilotierung wird keine Aufwandsschätzung behauptet')
        payload = self.model_dump(mode='json', exclude={'freeze_hash'})
        if self.freeze_hash != digest(payload):
            raise ValueError('Freezehash passt nicht')
        return self


class Series(Entity):
    freeze_id: UUID
    mode: Literal['single', 'series']
    order: tuple[UUID, ...]


class Block(Entity):
    freeze_id: UUID
    index: Positive
    in_b_e: bool


class PlannedRun(Entity):
    freeze_id: UUID
    block_id: UUID
    configuration_version_id: UUID
    cell_key: str
    position: Positive


class Run(Entity):
    phase_id: UUID
    purpose: Purpose
    planned_run_id: UUID | None = None
    configuration_version_id: UUID
    effective_hash: SHA256
    suite_id: UUID
    technical_evidence_ids: tuple[UUID, ...]
    start_decision: str
    started_at: UTC
    actual_position: Positive | None = None


class RunState(Contract):
    execution: Literal['queued', 'preflight', 'running', 'terminal']
    terminal_cause: Literal['finished', 'content_failure', 'technical_failure', 'interrupted', 'outcome_unknown'] | None = None
    seal: Literal['pending', 'sealed', 'no_candidate', 'integrity_error'] = 'pending'
    evaluation: Literal['not_due', 'queued', 'automatic_running', 'manual_pending', 'complete', 'measurement_error'] = 'not_due'
    result: Observation = Field(default_factory=lambda: Observation(status='pending', unit='result', reason='Noch nicht bewertet'))
    candidate_id: UUID | None = None
    ended_at: UTC | None = None

    @model_validator(mode='after')
    def terminal(self):
        if (self.execution == 'terminal') != (self.terminal_cause is not None):
            raise ValueError('Terminale Ursache erforderlich')
        if self.seal == 'sealed' and self.candidate_id is None:
            raise ValueError('Seal benötigt Kandidat')
        return self


class RunStateRevision(Entity):
    run_id: UUID
    predecessor_id: UUID | None
    old_state: RunState
    new_state: RunState
    reason: str


class ModelCall(Entity):
    run_id: UUID
    node: Literal['analyzer', 'planner', 'migrate', 'test', 'review', 'repair']
    sequence: Positive
    model_package_id: UUID
    request_hash: SHA256
    messages_artifact_id: UUID
    parameters: dict[str, Any]
    input_artifact_ids: tuple[UUID, ...]
    allowed_paths: tuple[str, ...]
    tools: tuple[str, ...]


class TransportAttempt(Entity):
    call_id: UUID
    number: Literal[1, 2]
    send_status: Literal['prepared', 'dispatching', 'response_saved', 'validated', 'incorporated', 'outcome_unknown', 'failed']
    request_id: str | None
    generation_id: str | None
    sent_at: UTC | None
    ended_at: UTC | None
    actual_model: TextObservation
    actual_provider: TextObservation
    response_artifact_id: UUID | None
    error_artifact_id: UUID | None
    format_status: Literal['pending', 'valid', 'invalid', 'missing']
    usage: dict[str, CountObservation]
    billing_status: Literal['known', 'partial', 'unresolved', 'not_due']
    transient_retry_evidence_id: UUID | None = None


class Event(Entity):
    run_id: UUID
    sequence: Positive
    event_type: str
    process_id: UUID
    interval_id: UUID | None
    happened_at: UTC
    evidence_ids: tuple[UUID, ...]
    details: dict[str, Any]


class Job(Entity):
    phase_id: UUID
    run_id: UUID | None
    job_type: Literal['generation', 'measurement', 'backup', 'analysis', 'export', 'import_recalculation', 'free_test']
    idempotency_key: str
    suite_id: UUID | None = None


class Artifact(Entity):
    sha256: SHA256
    byte_count: Nonnegative
    mime_type: str
    artifact_type: str
    producer: str
    run_id: UUID | None
    call_id: UUID | None = None
    measurement_id: UUID | None = None
    original_name: str
    access_scope: Literal['role', 'public_development', 'trusted_evaluator', 'trusted_register'] = 'trusted_register'


class CandidateSnapshot(Entity):
    run_id: UUID
    role: Literal['post_migrate', 'post_repair', 'sealed']
    file_manifest_id: UUID
    tree_hash: SHA256
    scaffold_id: UUID
    dependency_ids: tuple[UUID, ...]
    artifact_ids: tuple[UUID, ...]
    internal_tests_hash: SHA256
    accepted_files: tuple[str, ...]
    rejected_changes_artifact_id: UUID | None
    process_artifact_ids: tuple[UUID, ...]
    repair_count: Literal[0, 1]


class Compatibility(Contract):
    candidate_id: UUID
    candidate_hash: SHA256
    phase_id: UUID
    contract_id: UUID
    suite_id: UUID
    tool_id: UUID


class MeasurementAttempt(Entity):
    run_id: UUID
    measurement_key: str
    compatibility: Compatibility
    fixture_id: UUID
    revision: Positive
    predecessor_id: UUID | None
    completion: Literal['draft', 'completed']
    suite_kind: SuiteKind
    result: Observation
    raw_artifact_ids: tuple[UUID, ...]
    exit_code: IntegerObservation
    reason: str


class TestResult(Entity):
    measurement_id: UUID
    test_id: str
    assertion_id: str
    r_category: Literal['R1', 'R2', 'R3', 'R4', 'R5', 'R6']
    fixture_id: UUID
    input_artifact_id: UUID
    expected: str
    actual: Observation
    status: Literal['passed', 'failed', 'blocked_candidate', 'technical_missing']
    cause: str
    raw_artifact_id: UUID


class CriterionReviewRevision(Entity):
    run_id: UUID
    criterion: Literal['T1', 'T2', 'T3', 'T4', 'T5']
    compatibility: Compatibility
    rubric_id: UUID
    revision: Positive
    predecessor_id: UUID | None
    completion: Literal['draft', 'completed']
    verdict: BinaryObservation
    reason: str
    file_path: str | None
    lines: str | None
    code_artifact_id: UUID | None
    measurement_ids: tuple[UUID, ...]
    person: str
    reviewer_origin: Literal['human', 'automatic']
    reviewed_at: UTC

    @model_validator(mode='after')
    def completion_evidence(self):
        if not self.reason:
            raise ValueError('Auch Entwurf braucht Begründung')
        if self.completion == 'completed':
            if self.criterion in ('T2','T3','T4') and self.reviewer_origin != 'human':
                raise ValueError('T2–T4-Abschluss benötigt menschliche Prüfung; automatische Vorbefunde nur Entwurf')
            if self.verdict.status != 'observed' or not self.person or not (self.code_artifact_id or self.measurement_ids):
                raise ValueError('Abschluss benötigt Urteil/Person/Code- oder Messbeleg')
            if self.criterion == 'T4' and not self.measurement_ids:
                raise ValueError('T4 benötigt konkrete Integrationsmessbelege')
        return self


class RevisionInvalidation(Entity):
    revision_id: UUID
    reason: str
    person: str
    defect_evidence_id: UUID


class MetricObservation(Entity):
    run_id: UUID | None
    phase_id: UUID
    metric: str
    value: NumericObservation
    measurement_id: UUID | None
    interval_ids: tuple[UUID, ...]
    file_scope: tuple[str, ...]
    excluded_files: tuple[str, ...]
    configuration_hash: SHA256 | None
    diagnostics_artifact_id: UUID | None


class CostEntry(Entity):
    run_id: UUID
    transport_id: UUID
    operating_area: Purpose
    amount: NonnegativeObservation
    currency: str
    price_evidence_id: UUID
    billing_evidence_ids: tuple[UUID, ...]
    uncertainty: str

    @model_validator(mode='after')
    def cost(self):
        if self.amount.value is not None:
            if not isinstance(self.amount.value, Decimal) or self.amount.value < 0:
                raise ValueError('Kosten sind nichtnegative Decimalwerte')
        return self


class IntervalConnection(Contract):
    previous_interval_id: UUID
    relation: Literal['contiguous', 'excluded_gap', 'unknown_active']
    evidence_ids: tuple[UUID, ...] = Field(min_length=1)


class TimeInterval(Entity):
    run_id: UUID
    sequence: Positive
    kind: Literal['active', 'pause', 'outage', 'unknown_active', 'excluded_gap']
    connection: IntervalConnection | None = None
    process_id: UUID | None
    monotonic_start: Decimal | None
    monotonic_end: Decimal | None
    end_process_id: UUID | None
    started_at: UTC | None
    ended_at: UTC | None
    evidence_ids: tuple[UUID, ...]
    reason: str

    @model_validator(mode='after')
    def interval(self):
        known = self.kind in ('active', 'pause', 'outage')
        if known:
            if not self.process_id or self.process_id != self.end_process_id:
                raise ValueError('Monotone Uhr nur innerhalb desselben Prozesses')
            if self.monotonic_start is None or self.monotonic_end is None or self.monotonic_end < self.monotonic_start:
                raise ValueError('Monotone Intervallgrenzen fehlen/verkehrt')
        elif self.monotonic_start is not None or self.monotonic_end is not None:
            raise ValueError('Unbekannte Dauer nicht als Uhrmessung angeben')
        if not self.evidence_ids or not self.reason:
            raise ValueError('Zeitzuordnung braucht Beleg und Grund')
        if (self.started_at is None) != (self.ended_at is None):
            raise ValueError('UTC-Grenzen zusammen oder beide unbekannt')
        if self.started_at and self.ended_at < self.started_at:
            raise ValueError('UTC-Intervall verkehrt')
        return self


class Backup(Entity):
    phase_id: UUID
    freeze_id: UUID
    substantive_revision: Nonnegative
    manifest_hash: SHA256
    database_manifest_id: UUID
    object_manifest_id: UUID
    checkpoint_manifest_id: UUID
    status: Literal['creating', 'awaiting_confirmation', 'failed']
    consistent: bool
    active_request_ids: tuple[UUID, ...] = ()
    write_barrier_evidence_id: UUID

    @model_validator(mode='after')
    def consistency(self):
        if self.consistent and self.active_request_ids:
            raise ValueError('Aktive Requests verhindern konsistentes Backup')
        return self


class BackupReceipt(Entity):
    backup_id: UUID
    manifest_hash: SHA256
    person: str
    external_medium: str
    confirmed_at: UTC
    self_report: Literal[True] = True


class AnalysisRun(Entity):
    phase_id: UUID
    freeze_id: UUID
    input_hash: SHA256
    selected_inputs: dict[str, Any]
    selection_rule: Literal['last_completed_valid_compatible-v1']
    software_commit: str
    analysis_asset_id: UUID
    confirmed_by: str
    confirmed_at: UTC
    missing_decisions: dict[str, str]
    excluded_ids: tuple[UUID, ...]
    block_sets: dict[str, tuple[UUID, ...]]
    denominators: dict[str, CountObservation]
    result_artifact_ids: tuple[UUID, ...]


class Package(Entity):
    phase_id: UUID
    freeze_id: UUID
    version: Positive
    predecessor_id: UUID | None
    format_version: Literal[1]
    manifest_hash: SHA256
    zip_hash: SHA256 | None
    analysis_id: UUID | None
    software_commit: str
    measurement_commit: str
    analysis_commit: str
    export_status: Literal['pending', 'complete', 'incomplete', 'failed']
    import_origin: str | None
    integrity_report_id: UUID | None
    runtime_platforms: tuple[str, ...]
    omitted_fields: dict[str, str]
    planned_run_ids: tuple[UUID, ...]
    provenance_run_ids: tuple[UUID, ...]



class RetryEvidence(Entity):
    transport_id: UUID
    error_artifact_id: UUID
    failure_kind: Literal['transient_no_response', 'format_error', 'content_error', 'outcome_unknown', 'permanent']
    no_usable_response: bool
    dispatch_outcome: Literal['known_failed', 'unknown']
    reason: str


class TransportState(Contract):
    transport_id: UUID
    status: Literal['prepared', 'dispatching', 'response_saved', 'validated', 'incorporated', 'outcome_unknown', 'failed']
    sequence: Nonnegative


class TransportStateEvent(Entity):
    transport_id: UUID
    sequence: Positive
    status: Literal['dispatching', 'response_saved', 'validated', 'incorporated', 'outcome_unknown', 'failed']
    request_id: str | None
    generation_id: str | None
    response_artifact_id: UUID | None
    error_artifact_id: UUID | None
    actual_model: TextObservation
    actual_provider: TextObservation
    usage: dict[str, CountObservation]
    billing_status: Literal['known', 'partial', 'unresolved', 'not_due']
    format_status: Literal['pending', 'valid', 'invalid', 'missing']


class TokenUsage(Contract):
    input_tokens: CountObservation
    output_tokens: CountObservation
    cache_read_tokens: CountObservation
    cache_write_tokens: CountObservation
    reasoning_tokens: CountObservation
    other_tokens: CountObservation
    completeness: Literal['complete', 'partial', 'missing']


class ResourceProfile(Entity):
    run_id: UUID | None
    phase_id: UUID
    operating_area: Purpose
    token_usage: TokenUsage
    transport_ids: tuple[UUID, ...]
    cost_entry_ids: tuple[UUID, ...]
    setup_seconds: NonnegativeObservation
    pipeline_seconds: NonnegativeObservation
    evaluation_seconds: NonnegativeObservation
    manual_review_seconds: NonnegativeObservation
    total_seconds: NonnegativeObservation
    pause_seconds: NonnegativeObservation
    outage_seconds: NonnegativeObservation
    load_seconds: NonnegativeObservation
    inference_seconds: NonnegativeObservation
    retry_seconds: NonnegativeObservation
    abort_seconds: NonnegativeObservation
    context_preparation_seconds: NonnegativeObservation
    context_preparation_completeness: Literal['complete', 'partial', 'missing']
    interval_ids: tuple[UUID, ...]
    repair_count: BinaryObservation


class StaticProfile(Entity):
    measurement_id: UUID
    configuration_hash: SHA256
    file_manifest_id: UUID
    excluded_files: tuple[str, ...]
    diagnostics_artifact_id: UUID
    d: CountObservation
    l_by_file: dict[str, CountObservation]
    l: CountObservation
    s: NonnegativeObservation
    exit_code: IntegerObservation
    failure_reason: str | None


class FunctionalProfile(Entity):
    measurement_id: UUID
    t1: BinaryObservation
    t2: BinaryObservation
    t3: BinaryObservation
    t4: BinaryObservation
    t5: BinaryObservation
    r1: BinaryObservation
    r2: BinaryObservation
    r3: BinaryObservation
    r4: BinaryObservation
    r5: BinaryObservation
    r6: BinaryObservation
    t: BinaryObservation
    r: FractionObservation
    f: FractionObservation
    full_success: BinaryObservation
    raw_pass_ratio: FractionObservation
    review_revision_ids: tuple[UUID, ...]
    test_result_ids: tuple[UUID, ...]


class ProcessInstance(Entity):
    worker: str
    software_commit: str
    platform: str
    started_at: UTC
    clock_description: str


class JobClaim(Contract):
    status: Literal['ready', 'running', 'completed', 'stopped']
    worker: str | None
    claimed_at: UTC | None
    heartbeat_at: UTC | None


ENTITIES = {c.__name__: c for c in (Study, StudyPhase, AssetVersion, ModelPackage, Configuration,
    ConfigurationVersion, Approval, Freeze, Series, Block, PlannedRun, Run, ModelCall,
    TransportAttempt, Event, Job, Artifact, CandidateSnapshot, MeasurementAttempt, TestResult,
    CriterionReviewRevision, RevisionInvalidation, MetricObservation, CostEntry, TimeInterval,
    Backup, BackupReceipt, AnalysisRun, Package, TransportStateEvent, ResourceProfile, StaticProfile, FunctionalProfile, ProcessInstance, RunStateRevision, RetryEvidence, PhaseStateRevision)}


def protected_evaluation(artifact):
    """Both holdout scope and trusted development-evaluator provenance are closed to roles."""
    return artifact.access_scope == 'trusted_evaluator' or artifact.producer == 'trusted_evaluator' or artifact.artifact_type.startswith('evaluation_')

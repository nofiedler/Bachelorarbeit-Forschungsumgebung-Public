#!/usr/bin/env python3
"""#15 explicit synthetic preparation/readback, never a replacement UI driver.

Prepare only after the normal entrypoint migration, while the own instance is idle. Browser starts still go
through normal commands, worker, pipeline and Docker. Receipt is the explicitly
permitted #6 gate fixture, not proof of an independent physical backup medium.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src')]
from research_env.artifacts import ArtifactStore, read_regular
from research_env.config import Settings
from research_env.domain import (Approval, Artifact, AssetVersion, Backup, BackupReceipt, Configuration, ConfigurationVersion, ModelPackage, Study,
    EvidenceProof, Freeze, FreezeResources, HUMAN_GATES, MAIN_CELLS, Observation,
    REQUIRED_GATES, RESOURCE_GATES, RunState, StudyPhase, TECHNICAL_GATES, digest)
from research_env.analysis_store import source_binding
from research_env.matrix import matrix, reference_views, ALGORITHM
from research_env.preparation import imported_catalog, software_identity, active_generation
from research_env.register import GateError, Register
from research_env.locks import write_barrier
from research_env.sandbox_runtime import RuntimeImages
from research_env.template import draft


PROVENANCE = 'Predetermined technical gate preparation only; no empirical study/human approval'


def prepare(r):
    if active_generation(r):
        raise ValueError('Own preparation requires an idle instance')
    store = ArtifactStore(r.settings, r)
    f = draft(r, ROOT, title='SYNTHETIC #15 integrated 5/3 fixture', data_origin='synthetic',
              provenance=PROVENANCE)
    assets, model = imported_catalog(r)
    fid = uuid4()
    installation = r.connection.execute("SELECT value FROM runtime_metadata WHERE key='installation_id'").fetchone()[0]
    proof = store.json({'schema': 'issue15-fixture-v1', 'synthetic': True, 'expected_matrix': 48,
                        'installation_id': installation, 'study_id': str(f['study'].id),
                        'phase_id': str(f['phase'].id), 'freeze_id': str(fid),
                        'source': software_identity(), 'human_approval': False}, artifact_type='issue15_preparation')
    images = RuntimeImages(**json.loads((ROOT / 'src/research_env/pipeline_runtime.lock.json').read_text()))
    tool = assets['tool']
    analysis = source_binding()
    araw = store.json(analysis, artifact_type='issue15_analysis_binding')
    aid = r.add(AssetVersion(code='ISSUE15-ANALYSIS-' + str(uuid4()), asset_type='analysis',
                            manifest_hash=digest(analysis), artifact_ids=(araw.id,),
                            origin='Actual installed M7 source binding', access_scope='trusted_register'))
    # Predetermined small synthetic assertions, independent of candidate output.
    cases = [{'id': m + '-c' + str(i), 'category': 'R' + str(i), 'module': m,
              'assertions': [m + '-a' + str(i)]} for m in ('BF', 'SQL', 'UP') for i in range(1, 7)]
    catalog = store.json({'data_origin': 'synthetic', 'cases': cases}, artifact_type='evaluation_case_catalog',
                         producer='trusted_evaluator', access_scope='trusted_evaluator')
    suite = r.add(AssetVersion(code='ISSUE15-SYNTHETIC-CASES-' + str(uuid4()), asset_type='suite',
                              suite_kind='study_holdout', manifest_hash=catalog.sha256, artifact_ids=(catalog.id,),
                              origin='Predetermined synthetic technical catalog; no native holdout results', access_scope='trusted_evaluator'))
    settings = f['settings'].model_copy(update={
        'model_a': model.id, 'model_b': model.id, 'contract_id': assets['contract-current'].id,
        'context_ids': {f'{m}-K{k}': assets[f'current-{m}-K{k}'].id for m in ('BF', 'SQL', 'UP') for k in (0, 1)},
        'prompt_ids': (assets['prompts'].id,), 'handoff_id': assets['handoff'].id,
        'holdout_suite_id': suite.id, 'tool_ids': (tool.id,), 'analysis_id': aid.id,
        'role_parameters': {'all': {'seed': 1}}, 'retry_interval_seconds': Decimal('1'),
        'provider_limits_defaults': {'mock': 'SYNTHETIC deterministic adapter; no provider capability claim'},
        'security_resources': {'contract': 'Existing fixed M4 CPU/RAM/PID/file protections'},
        'working_copy_evidence_id': proof.id, 'software_commit': software_identity(),
        'lockfile_hashes': {'requirements.lock': hashlib.sha256((ROOT / 'requirements.lock').read_bytes()).hexdigest()},
        'container_hashes': {k: v.rsplit('sha256:', 1)[-1] for k, v in vars(images).items()},
        'hardware': 'SYNTHETIC preparation; actual host observations separately preserved'})
    versions = r.central_update(f['phase'].id, settings)
    pilot = r.add(StudyPhase(code='ISSUE15-PILOT-' + str(uuid4()), study_id=f['study'].id, purpose='pilot',
                            provenance='SYNTHETIC formal prerequisite placeholder; no executed pilot'))
    config = r.add(Configuration(code='ISSUE15-PILOT-CONF-' + str(uuid4()), phase_id=pilot.id))
    pv = r.version_configuration(config.id, 'pilot-fixture', MAIN_CELLS['C-SQL-1'], settings)
    run, _ = r.start_other(pilot.id, pv.id, decision='TECHNICAL-FIXTURE: placeholder only, no calls',
                           technical_evidence_ids=(proof.id,), idempotency_key=str(uuid4()))
    r.set_state(run.id, RunState(execution='terminal', terminal_cause='finished'))
    consumption = store.json({'synthetic': True, 'not_measured': True}, run_id=run.id, artifact_type='issue15_pilot_placeholder')
    resources = FreezeResources(resource_decision='5/3 predetermined technical fixture; no study decision',
        consumption_evidence_id=consumption.id, consumption_evidence_hash=consumption.sha256,
        pilot_run_ids=(run.id,), estimated_cost=Observation(status='estimated', value=Decimal('1'), unit='USD', source='SYNTHETIC'),
        estimated_time=Observation(status='estimated', value=Decimal('2'), unit='s', source='SYNTHETIC'),
        backup_destination='SYNTHETIC #6 gate fixture on same SSD, not independent medium')
    ids = {k: v.id for k, v in versions.items()}
    scope = r.freeze_scope(f['phase'].id, ids, 5, 3, 'issue15-fixed', fid, resources=resources)
    pa = r.add(AssetVersion(code='ISSUE15-GATE-' + str(uuid4()), asset_type='tool', manifest_hash=proof.sha256,
                            artifact_ids=(proof.id,), origin='Explicit synthetic gate proof, not approval', access_scope='trusted_register'))
    proofs = tuple(EvidenceProof(key=k, asset_id=pa.id, asset_hash=pa.manifest_hash, scope_hash=scope) for k in REQUIRED_GATES)
    approvals = tuple(r.add(Approval(code='ISSUE15-APPROVAL-' + str(uuid4()), phase_id=f['phase'].id, kind=kind,
        scope_hash=scope, person='TECHNICAL-FIXTURE:no human approval', reason='Explicit synthetic prerequisite only',
        evidence=tuple(p for p in proofs if p.key in keys), paid_calls_consent=kind == 'cost', synthetic_fixture=True))
        for kind, keys in (('technical', TECHNICAL_GATES), ('subject', HUMAN_GATES), ('cost', RESOURCE_GATES)))
    body = dict(id=fid, code='ISSUE15-FREEZE-' + str(fid), created_at=datetime.now(timezone.utc), schema_version=1,
        phase_id=f['phase'].id, configuration_version_ids=ids, r_c=5, r_e=3, seed='issue15-fixed', algorithm=ALGORITHM,
        matrix=matrix(fid, 5, 3, 'issue15-fixed'), scope_hash=scope, evidence=proofs,
        approval_ids=tuple(a.id for a in approvals), selection_rule='last_completed_valid_compatible-v1',
        **resources.model_dump(exclude={'estimated_cost', 'estimated_time'}),
        estimated_cost=resources.estimated_cost, estimated_time=resources.estimated_time)
    normalized = Freeze.model_construct(**body, freeze_hash='0' * 64).model_dump(mode='json', exclude={'freeze_hash'})
    freeze = r.freeze(Freeze(**body, freeze_hash=digest(normalized)))
    assert len(freeze.matrix) == len({x['id'] for x in freeze.matrix}) == 48
    references = reference_views(freeze.matrix)
    assert references['model_assignment'] == references['components'] and len(references['components']) == 3
    assert r.backup_status(freeze.id) != 'current'
    return {'synthetic': True, 'study_id': str(f['study'].id), 'freeze_id': str(freeze.id),
            'phase_id': str(f['phase'].id), 'first_id': str(r.next_id(freeze.id)),
            'matrix': freeze.matrix, 'references': references, 'gate_without_receipt': r.backup_status(freeze.id),
            'scope': 'Technical prerequisite preparation only; browser must still start normal Main'}


def own_fixture(r, freeze_id):
    """Read-only boundary: this installation's marked #15 freeze, exclusively mocks."""
    if active_generation(r):
        raise GateError('Receipt fixture requires own idle instance')
    f = r.get(freeze_id, Freeze)
    phase = r.get(f.phase_id, StudyPhase); study = r.get(phase.study_id, Study)
    if (study.data_origin != 'synthetic' or study.provenance != PROVENANCE or
            phase.purpose != 'main' or phase.provenance != PROVENANCE or
            f.code != 'ISSUE15-FREEZE-' + str(f.id) or
            (f.r_c, f.r_e, f.seed) != (5, 3, 'issue15-fixed') or len(f.matrix) != 48):
        raise GateError('Receipt is restricted to own synthetic #15 fixture')
    versions = [r.get(vid, ConfigurationVersion) for vid in f.configuration_version_ids.values()]
    proof_ids = {v.settings.working_copy_evidence_id for v in versions}
    if len(versions) != 12 or len(proof_ids) != 1 or None in proof_ids:
        raise GateError('Own #15 fixture binding missing')
    proof = r.get(next(iter(proof_ids)), Artifact)
    if proof.artifact_type != 'issue15_preparation':
        raise GateError('Own #15 preparation proof missing')
    # No ArtifactStore constructor here: a rejected foreign instance stays unwritten.
    raw, mode = read_regular(r.settings.artifacts, ArtifactStore.object_path(proof.sha256))
    if mode & 0o222 or len(raw) != proof.byte_count or hashlib.sha256(raw).hexdigest() != proof.sha256:
        raise GateError('Own #15 proof integrity failed')
    marker = json.loads(raw)
    installation = r.connection.execute("SELECT value FROM runtime_metadata WHERE key='installation_id'").fetchone()[0]
    expected = {'schema': 'issue15-fixture-v1', 'synthetic': True, 'human_approval': False,
                'expected_matrix': 48, 'installation_id': installation, 'study_id': str(study.id),
                'phase_id': str(phase.id), 'freeze_id': str(f.id), 'source': software_identity()}
    if marker != expected:
        raise GateError('Foreign or unbound #15 fixture proof')
    for v in versions:
        if r.get(v.configuration_id, Configuration).phase_id != phase.id:
            raise GateError('Foreign #15 configuration')
        for mid in (v.settings.model_a, v.settings.model_b):
            model = r.get(mid, ModelPackage)
            if (model.code, model.exact_model_id, model.endpoint, model.upstream) != (
                    'M8-OFFLINE-v1', 'TECHNICAL-FIXTURE:no-network', 'mock://local', 'mock'):
                raise GateError('Receipt fixture permits exclusively the own offline Mock package')
    if any(not r.get(aid, Approval).synthetic_fixture for aid in f.approval_ids):
        raise GateError('Receipt fixture cannot stand in for human approval')
    return f


def receipt(r, freeze_id):
    own_fixture(r, freeze_id)  # Reject before even acquiring a potentially new lock file.
    # Existing write barrier keeps the checked provenance/models fixed through all writes.
    with write_barrier(r.settings, exclusive=True):
        f = own_fixture(r, freeze_id); revision = r.revision(f.phase_id)
        proof = ArtifactStore(r.settings, r).json({'synthetic': True, 'revision': revision,
            'scope': 'Prepared #6 backup gate only; full backup UI is #16'}, artifact_type='issue15_receipt_fixture')
        own_fixture(r, freeze_id)
        b = r.add(Backup(code='ISSUE15-BACKUP-' + str(uuid4()), phase_id=f.phase_id, freeze_id=f.id,
            substantive_revision=revision, manifest_hash=proof.sha256, database_manifest_id=proof.id,
            object_manifest_id=proof.id, checkpoint_manifest_id=proof.id, write_barrier_evidence_id=proof.id,
            status='awaiting_confirmation', consistent=True))
        own_fixture(r, freeze_id)
        r.add(BackupReceipt(code='ISSUE15-RECEIPT-' + str(uuid4()), backup_id=b.id, manifest_hash=b.manifest_hash,
            person='TECHNICAL-FIXTURE: simulated external confirmation', external_medium='Same SSD synthetic gate only',
            confirmed_at=datetime.now(timezone.utc)))
        assert revision == r.revision(f.phase_id) and r.backup_status(f.id) == 'current'
        return {'synthetic': True, 'backup_id': str(b.id), 'substantive_revision': revision, 'gate': 'current',
                'physical_independent_backup': False, 'next_id': str(r.next_id(f.id))}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--instance', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True); parser.add_argument('--mode', choices=('prepare', 'receipt'), required=True)
    parser.add_argument('--freeze', type=UUID)
    args = parser.parse_args()
    if args.mode == 'receipt' and args.freeze is None: parser.error('Receipt requires the exact freeze ID')
    if args.output.exists(): parser.error('Preserve original output; use a fresh version')
    settings = Settings(*(args.instance / name for name in ('control', 'artifacts', 'checkpoints', 'staging')))
    if args.mode == 'receipt':
        if not settings.database.is_file():
            raise GateError('Receipt requires an existing own #15 fixture database')
        preflight = Register(settings, readonly=True)
        try: own_fixture(preflight, args.freeze)
        finally: preflight.close()
    r = Register(settings)
    try:
        result = prepare(r) if args.mode == 'prepare' else receipt(r, args.freeze)
        args.output.write_text(json.dumps(result, indent=2) + '\n'); print(json.dumps(result))
    finally: r.close()

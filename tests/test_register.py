"""Technical synthetic fixtures; no person, instrument, paid-call or study approval."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from uuid import uuid4

import pytest
from pydantic import ValidationError

from research_env.config import Settings
from research_env.database import MIGRATIONS, connect, migrate
from research_env.domain import *
TestResult.__test__ = False
from research_env.evidence import CONTRACTS, catalog, validate_entry, validate_document
from research_env.matrix import matrix, reference_views
from research_env.register import GateError, Register
from research_env.template import draft
from research_env.timing import summarize

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 3, tzinfo=timezone.utc)
HASH = 'a' * 64


def observed(value=1, unit='count'):
    return Observation(status='observed', value=Decimal(value) if isinstance(value, (int, Decimal)) or isinstance(value, str) and value.replace('.', '', 1).isdigit() else value, unit=unit, source='TECHNICAL-FIXTURE: control observation')


def missing(status='pending'):
    return Observation(status=status, unit='count', reason='TECHNICAL-FIXTURE: missing control')


def artifact(r, run=None, **kwargs):
    return r.add(Artifact(code=f'ART-{uuid4()}', sha256=HASH, byte_count=1, mime_type='application/json', artifact_type='technical_fixture', producer='TECHNICAL-FIXTURE', run_id=run.id if run else None, original_name='synthetic', **kwargs))


def asset(r, kind='tool', suite=None, **kwargs):
    return r.add(AssetVersion(code=f'ASSET-{uuid4()}', asset_type=kind, manifest_hash=HASH,
        origin='TECHNICAL-FIXTURE: synthetic validation only', suite_kind=suite,
        access_scope='trusted_evaluator' if suite == 'study_holdout' else 'public_development' if suite else 'trusted_register', **kwargs))


@pytest.fixture
def register(tmp_path):
    settings = Settings(*(tmp_path / name for name in ('control', 'artifacts', 'checkpoints', 'staging')))
    for path in (settings.control, settings.artifacts, settings.checkpoints, settings.staging):
        path.mkdir()
    migrate(settings)
    r = Register(settings)
    yield r, settings
    r.close()


def setup_study(r):
    study = r.add(Study(code=f'STUDY-{uuid4()}', title='TECHNICAL-FIXTURE', design_version='synthetic-v1', data_origin='synthetic', provenance='No empirical or human approval'))
    main = r.add(StudyPhase(code=f'MAINPHASE-{uuid4()}', study_id=study.id, purpose='main', provenance='Technical synthetic main fixture'))
    basic = artifact(r)
    model = r.add(ModelPackage(code=f'MODEL-{uuid4()}', exact_model_id='TECHNICAL-FIXTURE:no-network', endpoint='mock://local', upstream='mock', routing={}, fallback={}, supported_parameters=('seed',), effective_parameters={'seed': 1}, context_limit=observed(100), output_limit=observed(10), price=observed(0, 'USD'), currency='USD', price_as_of=NOW, metadata_evidence_ids=(basic.id,), version_uncertainty='synthetic mock; no actual provider'))
    contract = asset(r, 'contract')
    scaffold = asset(r, 'scaffold')
    dev = asset(r, 'fixture_suite', 'development')
    holdout = asset(r, 'suite', 'study_holdout')
    reference = asset(r, 'reference', 'study_holdout')
    tool = asset(r)
    rubric = asset(r, 'contract')
    settings = EffectiveSettings(model_a=model.id, model_b=model.id, prompt_ids=(asset(r, 'prompt').id,), handoff_id=asset(r, 'handoff').id,
        contract_id=contract.id, scaffold_id=scaffold.id, rubric_id=rubric.id, context_ids={f'{m}-K{k}': asset(r, 'source_context').id for m in ('BF', 'SQL', 'UP') for k in (0, 1)},
        development_suite_id=dev.id, holdout_suite_id=holdout.id, reference_id=reference.id, tool_ids=(tool.id,), analysis_id=asset(r, 'analysis').id,
        role_parameters={'all': {'seed': 1}}, provider_limits_defaults={'mock': 'No provider'}, security_resources={'memory': 'synthetic fixture'}, retry_interval_seconds=Decimal(1),
        dvwa_commit='synthetic', working_copy_evidence_id=basic.id, lockfile_hashes={'synthetic': HASH}, software_commit='fdc1357344332232ab1dce3b317ad7c09feb45e8', hardware='synthetic fixture; actual platform in report', container_hashes={'synthetic': HASH})
    versions = {}
    for key, cell in MAIN_CELLS.items():
        conf = r.add(Configuration(code=f'CONF-{uuid4()}', phase_id=main.id))
        versions[key] = r.version_configuration(conf.id, key, cell, settings)
    pilot = r.add(StudyPhase(code=f'PILOTPHASE-{uuid4()}', study_id=study.id, purpose='pilot', provenance='Formal pilot control fixture; not real approval'))
    pc = r.add(Configuration(code=f'CONF-{uuid4()}', phase_id=pilot.id))
    pv = r.version_configuration(pc.id, 'pilot-fixture', MAIN_CELLS['C-SQL-1'], settings)
    pilot_run, _ = r.start_other(pilot.id, pv.id, decision='TECHNICAL-FIXTURE: synthetic pilot', technical_evidence_ids=(basic.id,), idempotency_key=f'pilot-{uuid4()}')
    r.set_state(pilot_run.id, RunState(execution='terminal', terminal_cause='finished', ended_at=NOW))
    consumption = artifact(r, pilot_run)
    return {'study': study, 'phase': main, 'settings': settings, 'versions': versions, 'basic': basic,
            'pilot': pilot_run, 'consumption': consumption, 'tool': tool, 'rubric': rubric}


def freeze_value(r, f, **overrides):
    fid = uuid4()
    ids = {k: v.id for k, v in f['versions'].items()}
    pilot_ids = tuple(run.id for run in f['pilot_runs']) if 'pilot_runs' in f else (f['pilot'].id,)
    resources = FreezeResources(resource_decision='5/3 technical fixture only; not actual study scope',
        consumption_evidence_id=f['consumption'].id,consumption_evidence_hash=f['consumption'].sha256,pilot_run_ids=pilot_ids,
        estimated_cost=Observation(status='estimated',value=Decimal('1'),unit='USD',source='synthetic uncertainty fixture'),
        estimated_time=Observation(status='estimated',value=Decimal('2'),unit='s',source='synthetic uncertainty fixture'),backup_destination='TECHNICAL-FIXTURE: no real medium')
    scope = r.freeze_scope(f['phase'].id, ids, 5, 3, 'M3-control-seed', fid,resources=resources)
    proof_asset = asset(r)
    proofs = tuple(EvidenceProof(key=k, asset_id=proof_asset.id, asset_hash=proof_asset.manifest_hash, scope_hash=scope) for k in REQUIRED_GATES)
    approvals = []
    for kind, keys in (('technical', TECHNICAL_GATES), ('subject', HUMAN_GATES), ('cost', RESOURCE_GATES)):
        approvals.append(r.add(Approval(code=f'APPROVAL-{uuid4()}', phase_id=f['phase'].id, kind=kind, scope_hash=scope,
            person='TECHNICAL-FIXTURE:no real person/approval', reason='Synthetic technical acceptance test only', evidence=tuple(p for p in proofs if p.key in keys), paid_calls_consent=kind == 'cost', synthetic_fixture=True)))
    body = dict(id=fid, code=f'FREEZE-{fid}', created_at=NOW, schema_version=1, phase_id=f['phase'].id,
        configuration_version_ids=ids, r_c=5, r_e=3, seed='M3-control-seed', algorithm='sha256-fisher-yates-v1',
        matrix=matrix(fid, 5, 3, 'M3-control-seed'), scope_hash=scope, evidence=proofs, approval_ids=tuple(a.id for a in approvals),
        selection_rule='last_completed_valid_compatible-v1', resource_decision='5/3 technical fixture only; not actual study scope',
        consumption_evidence_id=f['consumption'].id, consumption_evidence_hash=f['consumption'].sha256,pilot_run_ids=pilot_ids, estimated_cost=Observation(status='estimated', value=Decimal('1'), unit='USD', source='synthetic uncertainty fixture'),
        estimated_time=Observation(status='estimated', value=Decimal('2'), unit='s', source='synthetic uncertainty fixture'), backup_destination='TECHNICAL-FIXTURE: no real medium')
    body.update(overrides)
    normalized = json.loads(canonical(body)) if False else json.loads(Freeze.model_construct(**body, freeze_hash=HASH).model_dump_json(exclude={'freeze_hash'}))
    return Freeze(**body, freeze_hash=digest(normalized))


def backup(r, f, fixture):
    rev = r.revision(f.phase_id)
    b = r.add(Backup(code=f'BACKUP-{uuid4()}', phase_id=f.phase_id, freeze_id=f.id, substantive_revision=rev,
        manifest_hash=HASH, database_manifest_id=fixture.id, object_manifest_id=fixture.id, checkpoint_manifest_id=fixture.id,
        write_barrier_evidence_id=fixture.id, status='awaiting_confirmation', consistent=True))
    r.add(BackupReceipt(code=f'RECEIPT-{uuid4()}', backup_id=b.id, manifest_hash=b.manifest_hash,
        person='TECHNICAL-FIXTURE', external_medium='Synthetic metadata only, no hardware proof', confirmed_at=NOW))
    assert r.revision(f.phase_id) == rev
    return b


def start(r, fr, f, *, key=None):
    return r.start_main(fr.id, r.next_id(fr.id), expected_freeze_hash=fr.freeze_hash,
        decision='TECHNICAL-FIXTURE: conscious single start', idempotency_key=key or str(uuid4()), technical_evidence_ids=(f['basic'].id,))


def seal(r, run, f):
    basic = artifact(r, run)
    snap = r.add(CandidateSnapshot(code=f'SNAPSHOT-{uuid4()}', run_id=run.id, role='sealed', file_manifest_id=basic.id,
        tree_hash=HASH, scaffold_id=f['settings'].scaffold_id, dependency_ids=(), artifact_ids=(basic.id,), internal_tests_hash=HASH,
        accepted_files=('app/Module.php',), rejected_changes_artifact_id=None, process_artifact_ids=(basic.id,), repair_count=0))
    r.set_state(run.id, RunState(execution='terminal', terminal_cause='finished', seal='sealed', candidate_id=snap.id,
        evaluation='manual_pending', ended_at=NOW))
    comp = Compatibility(candidate_id=snap.id, candidate_hash=HASH, phase_id=run.phase_id, contract_id=f['settings'].contract_id, suite_id=run.suite_id, tool_id=f['tool'].id)
    return snap, basic, comp


def measurement(r, run, comp, basic, *, prev=None, completion='completed', value=1):
    return r.add(MeasurementAttempt(code=f'MEASURE-{uuid4()}', run_id=run.id, measurement_key='functional', compatibility=comp,
        fixture_id=comp.suite_id, revision=prev.revision + 1 if prev else 1, predecessor_id=prev.id if prev else None,
        completion=completion, suite_kind=r.get(comp.suite_id, AssetVersion).suite_kind, result=observed(value), raw_artifact_ids=(basic.id,), exit_code=observed(0), reason='Technical measurement control'))


def review(r, run, comp, basic, rubric, mids, *, prev=None, completion='completed', value=1):
    return r.add(CriterionReviewRevision(code=f'REVIEW-{uuid4()}', run_id=run.id, criterion='T4', compatibility=comp,
        rubric_id=rubric.id, revision=prev.revision + 1 if prev else 1, predecessor_id=prev.id if prev else None,
        completion=completion, verdict=observed(value) if completion == 'completed' else missing(), reason='Technical T4 control' if completion == 'completed' else 'Integration review pending, saved reason',
        file_path='app/Module.php', lines='1', code_artifact_id=basic.id, measurement_ids=mids,
        person='TECHNICAL-FIXTURE', reviewer_origin='human', reviewed_at=NOW))


def test_m3_01_matrix_reproducibility_and_reference_reuse():
    fid = uuid4()
    rows = matrix(fid, 5, 3, 'seed')
    assert rows == matrix(fid, 5, 3, 'seed')
    assert len(rows) == len({r['id'] for r in rows}) == 48
    assert set(r['cell_key'] for r in rows) == set(MAIN_CELLS)
    assert [sum(r['block_index'] == b for r in rows) for b in range(1, 6)] == [12, 12, 12, 6, 6]
    views = reference_views(rows)
    assert views['model_assignment'] == views['components']
    assert len(views['components']) == 3
    assert [r['position'] for r in rows] == list(range(1, 49))
    assert matrix(fid, 5, 3, 'different') != rows
    for c, e in ((0, 1), (2, 3), (True, 1), (2, 0)):
        with pytest.raises(ValueError):
            matrix(fid, c, e, 'seed')


def test_m3_02_draft_assets_and_central_versions(register):
    r, _ = register
    d = draft(r, ROOT, title='technical draft', data_origin='synthetic', provenance='Technical fixture')
    assert len(d['versions']) == 12
    assert len(r.all(AssetVersion)) == 12
    assert 'model_a' in d['open_fields'] and 'r_C' in d['open_decisions']
    assert set(d['asset_review'].values()) == {'open'}
    before = {k: r.get(v.id).model_dump(mode='json') for k, v in d['versions'].items()}
    settings = d['settings'].model_copy(update={'role_parameters': {'all': {'seed': 19}}})
    changed = r.central_update(d['phase'].id, settings)
    for k, v in changed.items():
        assert v.parent_id == d['versions'][k].id
        assert v.parent_hash == d['versions'][k].content_hash
        assert v.change_diff['settings.role_parameters']['after'] == {'all': {'seed': 19}}
        assert r.get(d['versions'][k].id).model_dump(mode='json') == before[k]
    # A nested mutation cannot silently change stored versions.
    settings.role_parameters['all']['seed'] = 20
    assert next(iter(changed.values())).content_hash != digest({'cell': next(iter(changed.values())).cell.model_dump(mode='json'), 'settings': settings.model_dump(mode='json')})
    assert r.get(next(iter(changed.values())).id).settings.role_parameters['all']['seed'] == 19


def test_m3_03_freeze_gates_and_immutable_main(register):
    r, _ = register
    f = setup_study(r)
    valid = freeze_value(r, f)
    for change in ({'evidence': valid.evidence[:-1]}, {'approval_ids': valid.approval_ids[:2]}, {'scope_hash': 'b' * 64}):
        body = valid.model_dump(mode='json', exclude={'freeze_hash'})
        body.update(json.loads(Freeze.model_construct(**change).model_dump_json(exclude_unset=True)))
        bad = Freeze(**body, freeze_hash=digest(body))
        with pytest.raises(GateError):
            r.freeze(bad)
        assert not r.all(Freeze)
    original = f['versions']['C-SQL-1']
    altered = r.version_configuration(original.configuration_id, 'C-SQL-1', MAIN_CELLS['E-AB'], f['settings'], original.id)
    f['versions']['C-SQL-1'] = altered
    with pytest.raises(GateError, match='Unzulässige'):
        r.freeze(freeze_value(r, f))
    f['versions']['C-SQL-1'] = original
    r.freeze(valid)
    assert len(r.fq_ids(valid.id)) == 48
    with pytest.raises(GateError, match='neue Erhebungsphase'):
        r.central_update(f['phase'].id, f['settings'])
    assert r.get(original.id).content_hash == original.content_hash
    with pytest.raises(GateError, match='Freezehash'):
        r.start_main(valid.id, r.next_id(valid.id), expected_freeze_hash=HASH, decision='synthetic', idempotency_key='bad-hash', technical_evidence_ids=(f['basic'].id,))


def test_m3_04_backup_first_subsequent_draft_stale_nonrecursive(register):
    r, _ = register
    f = setup_study(r)
    fr = r.freeze(freeze_value(r, f))
    single = r.add(Series(code='SINGLE-ORDER', freeze_id=fr.id, mode='single', order=tuple(r.fq_ids(fr.id))))
    series = r.add(Series(code='SERIES-ORDER', freeze_id=fr.id, mode='series', order=single.order))
    assert single.order == series.order
    with pytest.raises(GateError, match='Hauptliste'):
        r.add(series.model_copy(update={'id': uuid4(), 'code': 'SHUFFLED-SERIES', 'order': tuple(reversed(series.order))}))
    assert r.backup_status(fr.id) == 'required'
    with pytest.raises(GateError, match='Sicherung'):
        start(r, fr, f)
    backup(r, fr, f['basic'])
    run, job = start(r, fr, f, key='first')
    same, same_job = r.start_main(fr.id, run.planned_run_id, expected_freeze_hash=fr.freeze_hash, decision='duplicate click', idempotency_key='first', technical_evidence_ids=(f['basic'].id,))
    assert (same.id, same_job.id) == (run.id, job.id)
    snap, basic, comp = seal(r, run, f)
    first = measurement(r, run, comp, basic)
    draft_review = review(r, run, comp, basic, f['rubric'], (first.id,), completion='draft')
    assert r.backup_status(fr.id) == 'stale'
    with pytest.raises(GateError, match='Sicherung'):
        start(r, fr, f)
    backup(r, fr, basic)
    next_run, _ = start(r, fr, f)
    assert next_run.actual_position == 2 and next_run.id != run.id
    assert r.state(run.id).evaluation == 'manual_pending'
    r.set_state(next_run.id, RunState(execution='terminal', terminal_cause='content_failure', ended_at=NOW))
    backup(r, fr, basic)
    review(r, run, comp, basic, f['rubric'], (first.id,), prev=draft_review)
    assert r.backup_status(fr.id) == 'stale'
    assert len(r.fq_ids(fr.id)) == 48
    assert len(r.all(Run)) == 3  # pilot + two main, no fabricated generation
    record = r.set_phase_status(fr.phase_id, 'stopped', reason='Technical fixture: manually stop remaining planned IDs')
    assert r.phase_state(fr.phase_id).reason == record.reason
    assert r.phase_state(fr.phase_id).status == 'stopped'
    assert len(r.fq_ids(fr.id)) == 48
    with pytest.raises(GateError, match='Phase'):
        start(r, fr, f)


def test_m3_05_free_test_no_holdout_fq_or_export(register):
    r, _ = register
    f = setup_study(r)
    fr = r.freeze(freeze_value(r, f))
    free = r.add(StudyPhase(code='FREEPHASE', study_id=f['study'].id, purpose='free_test', provenance='Technical free fixture'))
    conf = r.add(Configuration(code='FREECONF', phase_id=free.id))
    with pytest.raises(GateError, match='Holdout'):
        r.version_configuration(conf.id, 'free', MAIN_CELLS['C-SQL-1'], f['settings'])
    settings = f['settings'].model_copy(update={'holdout_suite_id': None, 'reference_id': None})
    v = r.version_configuration(conf.id, 'free', MAIN_CELLS['E-P0R0'], settings)
    run, _ = r.start_other(free.id, v.id, decision='Technical free start', technical_evidence_ids=(f['basic'].id,), idempotency_key='free')
    assert run.planned_run_id is None
    assert r.get(run.suite_id, AssetVersion).suite_kind == 'development'
    with pytest.raises(GateError, match='Holdout'):
        r.add(Job(code='BAD-FREE-JOB', phase_id=free.id, run_id=run.id, job_type='measurement', idempotency_key='badfree', suite_id=f['settings'].holdout_suite_id))
    assert str(run.id) not in json.dumps(r.analysis_inputs(fr.id))
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        r.connection.execute("UPDATE register_record SET payload='{}' WHERE id=?", (str(run.id),))
    with pytest.raises(sqlite3.IntegrityError, match='immutable run provenance'):
        r.connection.execute('UPDATE run_binding SET purpose=? WHERE run_id=?', ('main',str(run.id)))
    payload = dict(code='PACKAGE', phase_id=fr.phase_id, freeze_id=fr.id, version=1, predecessor_id=None, format_version=1,
        manifest_hash=HASH, zip_hash=None, analysis_id=None, software_commit='synthetic', measurement_commit='synthetic', analysis_commit='synthetic',
        export_status='incomplete', import_origin=None, integrity_report_id=None, runtime_platforms=('synthetic',), omitted_fields={'all': 'Technical fixture only'}, planned_run_ids=tuple(r.fq_ids(fr.id)), provenance_run_ids=(f['pilot'].id,))
    good = r.add(Package(**payload))
    assert good.provenance_run_ids == (f['pilot'].id,)
    payload['code'] = 'BAD-PACKAGE'
    payload['provenance_run_ids'] = (run.id,)
    with pytest.raises(GateError, match='Provenienz'):
        r.add(Package(**payload))
    payload['provenance_run_ids'] = ()
    payload['planned_run_ids'] += (run.id,)
    with pytest.raises(GateError, match='Hauptmatrix'):
        r.add(Package(**payload))


def test_m3_06_two_connections_claims_concurrent_starts_and_fk(register):
    r, settings = register
    f = setup_study(r)
    fr = r.freeze(freeze_value(r, f))
    backup(r, fr, f['basic'])
    pid = r.next_id(fr.id)
    def concurrent_start(i):
        other = Register(settings)
        try:
            return other.start_main(fr.id, pid, expected_freeze_hash=fr.freeze_hash, decision='concurrent technical start',
                idempotency_key='same-click', technical_evidence_ids=(f['basic'].id,))[1].id
        finally:
            other.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(concurrent_start, (1, 2)))
    assert jobs[0] == jobs[1]
    def claim(i):
        other = Register(settings)
        try:
            success = other.claim(jobs[0], f'worker-{i}')
            assert not other.connection.in_transaction
            return success
        finally:
            other.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(claim, (1, 2))) == [False, True]
    with pytest.raises(sqlite3.IntegrityError, match='immutable identity'):
        r.connection.execute('UPDATE main_plan SET position=99 WHERE planned_id=?',(str(pid),))
    with pytest.raises(sqlite3.IntegrityError):
        r.connection.execute('INSERT INTO register_reference VALUES (?,?,?)', (str(fr.id), 'bad-FK', str(uuid4())))
    with closing(connect(settings)) as c:
        assert c.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        assert c.execute('PRAGMA busy_timeout').fetchone()[0] == 5000
        assert c.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
        assert c.execute('PRAGMA synchronous').fetchone()[0] == 2
        assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert c.execute('PRAGMA foreign_key_check').fetchall() == []


def test_m3_07_crash_rollback_m1_upgrade_and_failed_migration(register, tmp_path):
    r, settings = register
    identity = r.connection.execute("SELECT value FROM runtime_metadata WHERE key='installation_id'").fetchone()[0]
    program = 'import sqlite3,os,sys;c=sqlite3.connect(sys.argv[1]);c.execute("BEGIN IMMEDIATE");c.execute("INSERT INTO runtime_metadata VALUES (\\\"crash\\\",\\\"must rollback\\\")");os._exit(19)'
    crash = subprocess.run([sys.executable, '-c', program, str(settings.database)], capture_output=True, text=True)
    assert crash.returncode == 19, crash.stderr
    assert r.connection.execute("SELECT value FROM runtime_metadata WHERE key='crash'").fetchone() is None
    with pytest.raises(RuntimeError):
        with r.transaction():
            r._put(Study(code='ROLLBACK-STUDY', title='Synthetic', design_version='1', data_origin='synthetic', provenance='rollback'))
            raise RuntimeError('synthetic abort')
    assert not any(s.code == 'ROLLBACK-STUDY' for s in r.all(Study))
    m1 = tmp_path / 'm1-only'
    m1.mkdir()
    (m1 / '001_runtime.sql').write_bytes((MIGRATIONS / '001_runtime.sql').read_bytes())
    legacy = Settings(*(tmp_path / f'old-{n}' for n in ('control', 'artifacts', 'checkpoints', 'staging')))
    migrate(legacy, m1)
    with closing(connect(legacy)) as c:
        old_id = c.execute("SELECT value FROM runtime_metadata WHERE key='installation_id'").fetchone()[0]
        c.execute("INSERT INTO runtime_metadata VALUES ('m1-marker','preserve')")
    migrate(legacy)
    with closing(connect(legacy)) as c:
        assert c.execute("SELECT value FROM runtime_metadata WHERE key='installation_id'").fetchone()[0] == old_id
        assert c.execute("SELECT value FROM runtime_metadata WHERE key='m1-marker'").fetchone()[0] == 'preserve'
        assert c.execute('SELECT count(*) FROM schema_migrations').fetchone()[0] == len(list(MIGRATIONS.glob('*.sql')))
    assert identity == r.connection.execute("SELECT value FROM runtime_metadata WHERE key='installation_id'").fetchone()[0]
    bad = tmp_path / 'bad-migration'
    bad.mkdir()
    for p in MIGRATIONS.glob('*.sql'):
        (bad / p.name).write_bytes(p.read_bytes())
    (bad / '003_failure.sql').write_text('CREATE TABLE failed_extra (id INTEGER);\nINVALID SQL;\n')
    with pytest.raises(sqlite3.Error):
        migrate(settings, bad)
    assert not r.connection.execute("SELECT name FROM sqlite_master WHERE name='failed_extra'").fetchone()
    assert not r.connection.execute("SELECT version FROM schema_migrations WHERE version='003_failure.sql'").fetchone()


def test_m3_08_values_zero_missing_and_utc():
    for status in ('pending', 'not_collected', 'technical_missing', 'not_applicable', 'unresolved'):
        value = missing(status)
        assert value.value is None and value.status == status
        with pytest.raises(ValidationError):
            Observation(status=status, value=0, unit='tokens', reason='missing usage')
    assert observed(0).value == Decimal(0)
    with pytest.raises(ValidationError):
        Observation(status='observed', value=0, unit='USD')
    assert Observation(status='estimated', value=Decimal(0), unit='USD', source='explicit zero estimate').status == 'estimated'
    with pytest.raises(ValidationError):
        Study(code='BADTIME', title='Synthetic', design_version='1', data_origin='synthetic', provenance='fixture', created_at=datetime(2026, 10, 3))
    utc = Study(code='UTC', title='Synthetic', design_version='1', data_origin='synthetic', provenance='fixture', created_at=datetime(2026, 10, 3, 2, tzinfo=timezone(timedelta(hours=2))))
    assert utc.created_at == NOW


def test_m3_09_revision_selection_invalid_t4_and_historical_analysis(register):
    r, _ = register
    f = setup_study(r)
    fr = r.freeze(freeze_value(r, f))
    backup(r, fr, f['basic'])
    run, _ = start(r, fr, f)
    _, basic, comp = seal(r, run, f)
    m1 = measurement(r, run, comp, basic, value=0)
    t1 = review(r, run, comp, basic, f['rubric'], (m1.id,), value=0)
    m2 = measurement(r, run, comp, basic, prev=m1, completion='draft')
    t2 = review(r, run, comp, basic, f['rubric'], (m1.id,), prev=t1, completion='draft')
    assert r.select_revision(run.id, 'measurement', 'functional', comp).id == m1.id
    assert r.select_revision(run.id, 'review', 'T4', comp).id == t1.id
    inputs = r.analysis_inputs(fr.id)
    a = r.add(AnalysisRun(code='HISTORICAL-ANALYSIS', phase_id=fr.phase_id, freeze_id=fr.id, input_hash=digest(inputs), selected_inputs=inputs,
        selection_rule='last_completed_valid_compatible-v1', software_commit='synthetic', analysis_asset_id=f['settings'].analysis_id,
        confirmed_by='TECHNICAL-FIXTURE', confirmed_at=NOW, missing_decisions={}, excluded_ids=(), block_sets={}, denominators={}, result_artifact_ids=()))
    old = r.get(a.id).model_dump(mode='json')
    r.add(RevisionInvalidation(code='INVALID-M1', revision_id=m1.id, reason='controlled instrument defect', person='TECHNICAL-FIXTURE', defect_evidence_id=basic.id))
    assert r.select_revision(run.id, 'measurement', 'functional', comp) is None
    assert r.select_revision(run.id, 'review', 'T4', comp) is None
    assert r.analysis_inputs(fr.id)[str(run.planned_run_id)]['reviews']['T4']['revision_id'] is None
    assert r.get(a.id).model_dump(mode='json') == old
    m3 = measurement(r, run, comp, basic, prev=m2)
    t3 = review(r, run, comp, basic, f['rubric'], (m3.id,), prev=t2)
    m4 = measurement(r, run, comp, basic, prev=m3, value=0)
    r.add(RevisionInvalidation(code='INVALID-M4', revision_id=m4.id, reason='defect in newer measurement', person='TECHNICAL-FIXTURE', defect_evidence_id=basic.id))
    assert r.select_revision(run.id, 'measurement', 'functional', comp).id == m3.id
    assert r.select_revision(run.id, 'review', 'T4', comp).id == t3.id
    picked = a.model_copy(update={'id': uuid4(), 'code': 'PICKED-BAD', 'selected_inputs': inputs})
    with pytest.raises(GateError, match='freie Wahl'):
        r.add(picked)
    with pytest.raises(GateError, match='Folge'):
        measurement(r, run, comp, basic, prev=m1)


def interval(kind, seq, proc, start, end, *, utc_start=None, utc_end=None):
    return TimeInterval(code=f'TIME-{uuid4()}', run_id=UUID('00000000-0000-0000-0000-000000000001'), sequence=seq,
        kind=kind, process_id=proc, end_process_id=proc, monotonic_start=Decimal(start) if start is not None else None,
        monotonic_end=Decimal(end) if end is not None else None, started_at=utc_start, ended_at=utc_end,
        evidence_ids=(uuid4(),), reason='controlled time partition')


def test_m3_10_disjoint_intervals_pause_restart_unknown_active_and_excluded_gap():
    p1, p2 = uuid4(), uuid4()
    # Pause requested while call runs stays in active time; actual pause is a separate interval.
    times = (interval('active',1,p1,0,10,utc_start=NOW,utc_end=NOW+timedelta(seconds=10)),
        interval('pause',2,p1,10,15,utc_start=NOW+timedelta(seconds=10),utc_end=NOW+timedelta(seconds=15)),
        interval('pause',3,p2,0,4,utc_start=NOW+timedelta(seconds=15),utc_end=NOW+timedelta(seconds=19)),
        interval('active',4,p2,4,10,utc_start=NOW+timedelta(seconds=19),utc_end=NOW+timedelta(seconds=25)))
    out = summarize(times, coverage_complete=True)
    assert out['active'].value == 16 and out['pause'].value == 9 and out['outage'].value == 0 and out['total'].value == 25
    gap = interval('excluded_gap', 2, None, None, None)
    first_active=interval('active',1,p1,0,10)
    gap=gap.model_copy(update={'connection':IntervalConnection(previous_interval_id=first_active.id,relation='contiguous',evidence_ids=(uuid4(),))})
    final_active=interval('active',3,p2,0,6).model_copy(update={'connection':IntervalConnection(previous_interval_id=gap.id,relation='contiguous',evidence_ids=(uuid4(),))})
    active=(first_active,gap,final_active)
    out = summarize(active, coverage_complete=True)
    assert out['active'].value == 16 and out['active'].status == 'observed'
    assert out['total'].status == out['pause'].status == 'technical_missing'
    unknown = (active[0], gap.model_copy(update={'kind': 'unknown_active'}), active[2])
    out = summarize(unknown, coverage_complete=True)
    assert out['active'].status == 'technical_missing' and out['known_partial_seconds']['active'] == '16'
    with pytest.raises(ValidationError, match='Prozesses'):
        times[0].model_copy(update={'end_process_id': p2}).__class__.model_validate(times[0].model_dump() | {'end_process_id': p2})
    with pytest.raises(ValueError, match='überlappen'):
        summarize((times[0], interval('outage', 2, p1, 5, 12)), coverage_complete=True)
    with pytest.raises(ValueError, match='Überlappende'):
        summarize((interval('pause', 1, p1, 0, 10, utc_start=NOW, utc_end=NOW + timedelta(seconds=10)),
                   interval('outage', 2, p2, 0, 8, utc_start=NOW + timedelta(seconds=5), utc_end=NOW + timedelta(seconds=13))), coverage_complete=True)


def test_m3_11_catalog_individual_fields_and_executable_validation():
    from research_env.evidence import assert_catalog_current
    assert_catalog_current(ROOT / 'src/research_env/evidence_schema.json')
    c = catalog()
    entries = {e['evidence_key']: e for e in c['entries']}
    for cls_name, cls in CONTRACTS.items():
        for name in cls.model_fields:
            assert f'{cls_name}.{name}' in entries
    required = ('ModelCall.messages_artifact_id', 'TransportAttempt.usage.*.status', 'Freeze.estimated_cost.source',
        'ResourceProfile.token_usage.reasoning_tokens.value', 'StaticProfile.l_by_file.*.value', 'FunctionalProfile.t4.value',
        'TimeInterval.process_id', 'CriterionReviewRevision.measurement_ids.*', 'BackupReceipt.external_medium', 'AnalysisRun.input_hash')
    assert set(required) <= set(entries)
    for e in entries.values():
        assert all(e[k] for k in ('label', 'path', 'type', 'unit', 'source', 'applicability', 'duephase', 'validator', 'missingreasons', 'mapping'))
    assert {23, 24, 25, 26, 28, 30, 31, 32, 33, 34, 35, 50} <= {n for e in entries.values() for n in e['mapping']['requirements']}
    data = Study(code='CATALOG', title='Synthetic', design_version='1', data_origin='synthetic', provenance='fixture').model_dump(mode='json')
    assert validate_entry('Study.data_origin', data) == ['synthetic']
    with pytest.raises(ValidationError):
        validate_entry('Study.data_origin', data | {'data_origin': 'freepromoted'})
    assert validate_document('Study', data)['title'] == 'Synthetic'


def test_m3_12_logical_calls_transports_retry_unknown_and_no_open_tx(register):
    r, _ = register
    f = setup_study(r)
    fr = r.freeze(freeze_value(r, f))
    backup(r, fr, f['basic'])
    run, _ = start(r, fr, f)
    basic = artifact(r, run)
    call = r.add(ModelCall(code='CALL', run_id=run.id, node='analyzer', sequence=1, model_package_id=f['settings'].model_a,
        request_hash=basic.sha256, messages_artifact_id=basic.id, parameters={'seed': 1}, input_artifact_ids=(basic.id,), allowed_paths=('app/Module.php',), tools=()))
    with pytest.raises(sqlite3.IntegrityError):
        r.add(call.model_copy(update={'id': uuid4(), 'code': 'DUPLICATE-CALL'}))
    one = r.add(TransportAttempt(code='TRANSPORT-1', call_id=call.id, number=1, send_status='prepared', request_id=None, generation_id=None,
        sent_at=None, ended_at=None, actual_model=missing(), actual_provider=missing(), response_artifact_id=None, error_artifact_id=None,
        format_status='pending', usage={}, billing_status='not_due'))
    assert not r.connection.in_transaction
    # External work boundary: dispatch journal committed before hypothetical external operation.
    def progress(status, seq):
        return r.add(TransportStateEvent(code=f'PROGRESS-{seq}', transport_id=one.id, sequence=seq, status=status,
            request_id=None, generation_id=None, response_artifact_id=None, error_artifact_id=None, actual_model=missing(),
            actual_provider=missing(), usage={}, billing_status='unresolved', format_status='pending'))
    progress('dispatching', 1)
    assert not r.connection.in_transaction
    progress('outcome_unknown', 2)
    with pytest.raises(GateError, match='fortschritt'):
        progress('dispatching', 3)
    with pytest.raises(GateError, match='Retry'):
        r.add(one.model_copy(update={'id': uuid4(), 'code': 'BAD-RETRY', 'number': 2, 'transient_retry_evidence_id': basic.id}))
    with pytest.raises(ValidationError):
        TransportAttempt.model_validate(one.model_dump(mode='json') | {'number': 3})
    with pytest.raises(sqlite3.IntegrityError):
        r.connection.execute('INSERT INTO transport_binding VALUES (?,?,?)', (str(uuid4()), str(call.id), 3))
    assert len(r.all(TransportAttempt)) == 1


def test_m3_13_real_models_need_cost_scope_and_formal_pilot_gates(register):
    r, _ = register
    f = setup_study(r)
    old = r.get(f['settings'].model_a, ModelPackage)
    real = r.add(old.model_copy(update={'id': uuid4(), 'code': 'REAL-CONTRACT-NO-CALL', 'endpoint': 'https://provider.invalid'}))
    settings = f['settings'].model_copy(update={'model_a': real.id, 'model_b': real.id, 'holdout_suite_id': None, 'reference_id': None})
    free = r.add(StudyPhase(code='REAL-FREE-PHASE', study_id=f['study'].id, purpose='free_test', provenance='No actual request'))
    conf = r.add(Configuration(code='REAL-FREE-CONF', phase_id=free.id))
    version = r.version_configuration(conf.id, 'free-real-contract', MAIN_CELLS['C-SQL-1'], settings)
    with pytest.raises(GateError, match='Kostenfreigabe'):
        r.start_other(free.id, version.id, decision='would be real', technical_evidence_ids=(f['basic'].id,), idempotency_key='no-cost')
    proof_asset = asset(r)
    scope = r.configuration_scope(free.id, version.id)
    proof = EvidenceProof(key='paid_free_test', asset_id=proof_asset.id, asset_hash=proof_asset.manifest_hash, scope_hash=scope)
    r.add(Approval(code='FREE-REAL-COST', phase_id=free.id, kind='cost', scope_hash=scope, person='TECHNICAL-TEST-DECLARATION',
        reason='Synthetic repository gate declaration; never execute this invalid endpoint', evidence=(proof,), paid_calls_consent=True, synthetic_fixture=False))
    run, _ = r.start_other(free.id, version.id, decision='technical contract only, no execution', technical_evidence_ids=(f['basic'].id,), idempotency_key='with-cost')
    assert run.purpose == 'free_test'
    r.set_state(run.id, RunState(execution='terminal', terminal_cause='interrupted', ended_at=NOW))
    pilot = r.add(StudyPhase(code='REAL-PILOT-PHASE', study_id=f['study'].id, purpose='pilot', provenance='No real approval'))
    conf = r.add(Configuration(code='REAL-PILOT-CONF', phase_id=pilot.id))
    v = r.version_configuration(conf.id, 'pilot-real', MAIN_CELLS['C-SQL-1'], f['settings'].model_copy(update={'model_a': real.id, 'model_b': real.id}))
    scope = r.configuration_scope(pilot.id, v.id)
    proof = proof.model_copy(update={'scope_hash': scope})
    r.add(Approval(code='PILOT-COST-ONLY', phase_id=pilot.id, kind='cost', scope_hash=scope, person='TECHNICAL-TEST-DECLARATION',
        reason='Synthetic repository declaration only', evidence=(proof,), paid_calls_consent=True))
    with pytest.raises(GateError, match='Instrument-/Fachabnahme'):
        r.start_other(pilot.id, v.id, decision='technical', technical_evidence_ids=(f['basic'].id,), idempotency_key='no-pilot-gates')


def test_m3_14_foreign_artifacts_model_types_and_saved_response_identity(register):
    r, _ = register
    f = setup_study(r)
    fr = r.freeze(freeze_value(r, f))
    backup(r, fr, f['basic'])
    run, _ = start(r, fr, f)
    basic = artifact(r, run)
    payload = dict(code='TYPEDCALL', run_id=run.id, node='analyzer', sequence=1, model_package_id=f['basic'].id,
        request_hash=basic.sha256, messages_artifact_id=basic.id, parameters={'seed': 1}, input_artifact_ids=(), allowed_paths=(), tools=())
    with pytest.raises(GateError, match='Referenztyp'):
        r.add(ModelCall(**payload))
    payload['model_package_id'] = f['settings'].model_a
    payload['messages_artifact_id'] = f['consumption'].id  # another pilot run
    with pytest.raises(GateError, match='fremdem Lauf'):
        r.add(ModelCall(**payload))
    payload['messages_artifact_id'] = basic.id
    call = r.add(ModelCall(**payload))
    attempt = r.add(TransportAttempt(code='TYPEDATTEMPT', call_id=call.id, number=1, send_status='prepared', request_id=None,
        generation_id=None, sent_at=None, ended_at=None, actual_model=missing(), actual_provider=missing(), response_artifact_id=None,
        error_artifact_id=None, format_status='pending', usage={}, billing_status='not_due'))
    def progress(status, sequence, response):
        return r.add(TransportStateEvent(code=f'SAVEDRESPONSE-{sequence}', transport_id=attempt.id, sequence=sequence, status=status,
            request_id='fixed-request', generation_id='fixed-generation', response_artifact_id=response, error_artifact_id=None,
            actual_model=observed('synthetic'), actual_provider=observed('mock'), usage={}, billing_status='known', format_status='valid'))
    progress('dispatching', 1, None)
    progress('response_saved', 2, basic.id)
    other = artifact(r, run)
    with pytest.raises(GateError, match='austauschen'):
        progress('validated', 3, other.id)
    progress('validated', 3, basic.id)
    progress('incorporated', 4, basic.id)
    assert r.connection.execute('SELECT status FROM transport_state WHERE transport_id=?', (str(attempt.id),)).fetchone()[0] == 'incorporated'
    assert not r.connection.in_transaction


def test_m3_15_active_request_backup_and_process_interval_register(register):
    r, _ = register
    f = setup_study(r)
    fr = r.freeze(freeze_value(r, f))
    backup(r, fr, f['basic'])
    run, _ = start(r, fr, f)
    basic = artifact(r, run)
    call = r.add(ModelCall(code='ACTIVE-CALL', run_id=run.id, node='analyzer', sequence=1, model_package_id=f['settings'].model_a,
        request_hash=basic.sha256, messages_artifact_id=basic.id, parameters={'seed': 1}, input_artifact_ids=(), allowed_paths=(), tools=()))
    attempt = r.add(TransportAttempt(code='ACTIVE-ATTEMPT', call_id=call.id, number=1, send_status='prepared', request_id=None,
        generation_id=None, sent_at=None, ended_at=None, actual_model=missing(), actual_provider=missing(), response_artifact_id=None,
        error_artifact_id=None, format_status='pending', usage={}, billing_status='not_due'))
    r.add(TransportStateEvent(code='ACTIVE-DISPATCH', transport_id=attempt.id, sequence=1, status='dispatching', request_id=None,
        generation_id=None, response_artifact_id=None, error_artifact_id=None, actual_model=missing(), actual_provider=missing(), usage={}, billing_status='unresolved', format_status='pending'))
    with pytest.raises(GateError, match='Aktiver Request'):
        backup(r, fr, basic)
    proc = r.add(ProcessInstance(code='PROCESS', worker='technical-test', software_commit='synthetic', platform='synthetic', started_at=NOW, clock_description='process-local monotonic'))
    time = interval('active', 1, proc.id, 0, 10).model_copy(update={'run_id': run.id, 'evidence_ids': (basic.id,)})
    r.add(time)
    r.add(Event(code='PAUSE-REQUEST', run_id=run.id, sequence=1, event_type='pause_requested', process_id=proc.id,
        interval_id=time.id, happened_at=NOW, evidence_ids=(basic.id,), details={'note': 'still active until actual pause begins'}))
    assert r.get(time.id).process_id == proc.id
    nonexistent = uuid4()
    with pytest.raises(GateError, match='Fehlende Referenz'):
        r.add(time.model_copy(update={'id': uuid4(), 'code': 'BAD-PROCESS', 'sequence': 2, 'process_id': nonexistent, 'end_process_id': nonexistent}))


def test_m3_16_retry_requires_known_transient_failure_without_saved_response(register):
    r, _ = register
    f = setup_study(r)
    fr = r.freeze(freeze_value(r, f))
    backup(r, fr, f['basic'])
    run, _ = start(r, fr, f)
    basic, error = artifact(r, run), artifact(r, run)
    def make_call(node, seq):
        return r.add(ModelCall(code=f'RETRY-CALL-{node}', run_id=run.id, node=node, sequence=seq, model_package_id=f['settings'].model_a,
            request_hash=basic.sha256, messages_artifact_id=basic.id, parameters={'seed': 1}, input_artifact_ids=(), allowed_paths=(), tools=()))
    def make_attempt(call):
        return r.add(TransportAttempt(code=f'RETRY-ATTEMPT-{call.node}', call_id=call.id, number=1, send_status='prepared', request_id=None,
            generation_id=None, sent_at=None, ended_at=None, actual_model=missing(), actual_provider=missing(), response_artifact_id=None,
            error_artifact_id=None, format_status='pending', usage={}, billing_status='not_due'))
    def progress(attempt, seq, status, response=None):
        return r.add(TransportStateEvent(code=f'RETRY-STEP-{attempt.id}-{seq}', transport_id=attempt.id, sequence=seq, status=status,
            request_id=None, generation_id=None, response_artifact_id=response, error_artifact_id=error.id if status == 'failed' else None,
            actual_model=missing(), actual_provider=missing(), usage={}, billing_status='unresolved', format_status='invalid' if response else 'pending'))
    first = make_attempt(make_call('analyzer', 1))
    progress(first, 1, 'dispatching')
    progress(first, 2, 'failed')
    proof = r.add(RetryEvidence(code='KNOWN-TRANSIENT', transport_id=first.id, error_artifact_id=error.id, failure_kind='transient_no_response', no_usable_response=True, dispatch_outcome='known_failed', reason='controlled known transient no response'))
    second = r.add(first.model_copy(update={'id': uuid4(), 'code': 'ALLOWED-RETRY-2', 'number': 2, 'transient_retry_evidence_id': proof.id}))
    assert second.call_id == first.call_id and r.get(first.call_id).request_hash == r.get(second.call_id).request_hash
    with pytest.raises(sqlite3.IntegrityError):
        r.add(second.model_copy(update={'id': uuid4(), 'code': 'DUP-RETRY-2'}))
    formatted = make_attempt(make_call('planner', 2))
    progress(formatted, 1, 'dispatching')
    progress(formatted, 2, 'response_saved', basic.id)
    progress(formatted, 3, 'failed', basic.id)
    wrong = r.add(RetryEvidence(code='FORMAT-RETRY-EVIDENCE', transport_id=formatted.id, error_artifact_id=error.id,
        failure_kind='format_error', no_usable_response=False, dispatch_outcome='known_failed', reason='Format validation failed after response'))
    with pytest.raises(GateError, match='transienter'):
        r.add(formatted.model_copy(update={'id': uuid4(), 'code': 'FORMAT-RETRY', 'number': 2, 'transient_retry_evidence_id': wrong.id}))
    forged = r.add(wrong.model_copy(update={'id': uuid4(), 'code': 'FALSE-TRANSIENT-DECLARATION', 'failure_kind': 'transient_no_response', 'no_usable_response': True}))
    with pytest.raises(GateError, match='gesicherter Antwort'):
        r.add(formatted.model_copy(update={'id': uuid4(), 'code': 'STILL-NO-RETRY', 'number': 2, 'transient_retry_evidence_id': forged.id}))


def test_m3_17_harness_refuses_existing_evidence(tmp_path):
    marker = tmp_path / 'report.json'
    marker.write_text('historical failed proof must remain')
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/verify_m3.py'), str(tmp_path)], capture_output=True, text=True)
    assert result.returncode != 0 and 'Nichtleeres Belegverzeichnis' in result.stderr
    assert marker.read_text() == 'historical failed proof must remain'


def test_m3_18_catalog_against_independent_source_minimums():
    expected = json.loads((ROOT / 'tests/m3_required_evidence.json').read_text())
    keys = {e['evidence_key']: e for e in catalog()['entries']}
    for source, requirements in expected['sources'].items():
        for key in requirements:
            assert key in keys, (source, key)
            assert keys[key]['mapping']['operationalization_9']
    assert keys['StaticProfile.d.value']['unit'] == 'diagnostic count'
    assert 'NCLOC' in keys['StaticProfile.l.value']['unit']
    assert keys['StaticProfile.s.value']['unit'] == 'diagnostics per 100 NCLOC'
    assert keys['ResourceProfile.pipeline_seconds.value']['unit'] == 's'
    assert keys['TransportAttempt.ended_at']['duephase'] != 'before_dispatch'



def test_m3_19_actual_sql_projection_catalog_coverage(register):
    r, _ = register
    from research_env.evidence import SQL_PROJECTIONS, assert_projection_coverage
    assert assert_projection_coverage(r.connection)
    assert set(SQL_PROJECTIONS) == {'phase_state','run_binding','job_state','transport_state'}
    for table, mapping in SQL_PROJECTIONS.items():
        assert {row[1] for row in r.connection.execute(f'PRAGMA table_info({table})')} == set(mapping)
    r.connection.execute('ALTER TABLE job_state ADD COLUMN unregistered_status_detail TEXT')
    with pytest.raises(ValueError, match='Unmapped SQL projection'):
        assert_projection_coverage(r.connection)


def free_run(r, f, label='REVIEW-FREE'):
    phase = r.add(StudyPhase(code=f'{label}-{uuid4()}',study_id=f['study'].id,purpose='free_test',provenance='TECHNICAL-FIXTURE: review regression'))
    conf = r.add(Configuration(code=f'{label}-CONF-{uuid4()}',phase_id=phase.id))
    settings = f['settings'].model_copy(update={'holdout_suite_id':None,'reference_id':None})
    v = r.version_configuration(conf.id,'review-free',MAIN_CELLS['C-SQL-1'],settings)
    return r.start_other(phase.id,v.id,decision='TECHNICAL-FIXTURE: no network',technical_evidence_ids=(f['basic'].id,),idempotency_key=str(uuid4()))[0]


def call_attempt(r, run, f, basic, *, params=None):
    call = r.add(ModelCall(code=f'REVIEW-CALL-{uuid4()}',run_id=run.id,node='analyzer',sequence=1,
        model_package_id=r.get(run.configuration_version_id,ConfigurationVersion).settings.model_a,
        request_hash=basic.sha256,messages_artifact_id=basic.id,parameters=params or {'seed':1},input_artifact_ids=(basic.id,),allowed_paths=(),tools=()))
    attempt = r.add(TransportAttempt(code=f'REVIEW-TRANSPORT-{uuid4()}',call_id=call.id,number=1,send_status='prepared',request_id=None,
        generation_id=None,sent_at=None,ended_at=None,actual_model=missing(),actual_provider=missing(),response_artifact_id=None,error_artifact_id=None,
        format_status='pending',usage={},billing_status='not_due'))
    return call, attempt


def test_m3_20_approval_scope_binds_every_resource_and_pilot_field(register):
    r, _ = register
    f = setup_study(r)
    fr = freeze_value(r,f)
    other = r.add(Artifact(code='REPLACEMENT-CONSUMPTION',run_id=f['pilot'].id,sha256='b'*64,
        byte_count=1,mime_type='application/json',artifact_type='technical_fixture',producer='TECHNICAL-FIXTURE',original_name='replacement'))
    changes = ({'resource_decision':'different resources'}, {'backup_destination':'different external medium'},
        {'estimated_cost':Observation(status='estimated',value=Decimal('3.123'),unit='USD',source='different cost estimate')},
        {'estimated_time':Observation(status='estimated',value=Decimal('4.5'),unit='s',source='different time estimate')},
        {'consumption_evidence_id':other.id,'consumption_evidence_hash':other.sha256}, {'pilot_run_ids':()})
    for change in changes:
        body=fr.model_dump(mode='json',exclude={'freeze_hash'})
        for key,value in change.items():
            body[key]=value.model_dump(mode='json') if isinstance(value,Observation) else [str(i) for i in value] if isinstance(value,tuple) else str(value)
        if not body['pilot_run_ids']:
            with pytest.raises(ValidationError): Freeze(**body,freeze_hash=digest(body))
            continue
        bad=Freeze(**body,freeze_hash=digest(body))
        with pytest.raises(GateError,match='Scope'): r.freeze(bad)
        resources=FreezeResources(**{name:getattr(bad,name) for name in FreezeResources.model_fields})
        new_scope=r.freeze_scope(fr.phase_id,fr.configuration_version_ids,fr.r_c,fr.r_e,fr.seed,fr.id,resources=resources)
        assert new_scope != fr.scope_hash
        # Recomputing scope still cannot reuse old Approval/evidence scopes.
        body=bad.model_dump(mode='json',exclude={'freeze_hash'}) | {'scope_hash':new_scope}
        with pytest.raises(GateError,match='Scope'): r.freeze(Freeze(**body,freeze_hash=digest(body)))
    assert not r.all(Freeze)
    r.freeze(fr)


def test_m3_21_real_freeze_rejects_mock_pilot_as_model_access(register):
    r, _ = register
    f=setup_study(r)
    old=r.get(f['settings'].model_a,ModelPackage)
    real=r.add(old.model_copy(update={'id':uuid4(),'code':'DECLARED-REAL-NO-REQUEST','endpoint':'https://provider.invalid','upstream':'declared-provider'}))
    second=r.add(real.model_copy(update={'id':uuid4(),'code':'DECLARED-DISTINCT-B','exact_model_id':'TECHNICAL-FIXTURE:distinct-B'}))
    settings=f['settings'].model_copy(update={'model_a':real.id,'model_b':second.id})
    f['versions']=r.central_update(f['phase'].id,settings)
    f['settings']=settings
    fr=freeze_value(r,f)
    approvals=[]
    for a in r.all(Approval):
        if a.id in fr.approval_ids:
            approvals.append(r.add(a.model_copy(update={'id':uuid4(),'code':f'DECLARATION-{uuid4()}','synthetic_fixture':False})))
    body=fr.model_dump(mode='json',exclude={'freeze_hash'}) | {'approval_ids':[str(a.id) for a in approvals]}
    with pytest.raises(GateError,match='Pilotbedingungen'):
        r.freeze(Freeze(**body,freeze_hash=digest(body)))
    # Merely pointing a terminal pilot at a real model contract is also insufficient.
    pc=r.add(Configuration(code='REAL-PILOT-DECLARATION',phase_id=f['pilot'].phase_id))
    pv=r.version_configuration(pc.id,'pilot-real',MAIN_CELLS['C-SQL-1'],settings)
    scope=r.configuration_scope(f['pilot'].phase_id,pv.id)
    proofs=tuple(EvidenceProof(key=k,asset_id=f['tool'].id,asset_hash=f['tool'].manifest_hash,scope_hash=scope) for k in REQUIRED_GATES)
    for kind,keys in (('technical',TECHNICAL_GATES),('subject',HUMAN_GATES),('cost',RESOURCE_GATES)):
        r.add(Approval(code=f'PILOT-GATE-DECLARATION-{uuid4()}',phase_id=f['pilot'].phase_id,kind=kind,scope_hash=scope,
            person='TECHNICAL-FIXTURE: no actual person or approval',reason='Controlled gate declaration, no network operation',
            evidence=tuple(p for p in proofs if p.key in keys),paid_calls_consent=kind=='cost',synthetic_fixture=False))
    pilot,_=r.start_other(f['pilot'].phase_id,pv.id,decision='TECHNICAL-FIXTURE: declared real endpoint is never called',
        technical_evidence_ids=(f['basic'].id,),idempotency_key='declared-real-pilot')
    r.set_state(pilot.id,RunState(execution='terminal',terminal_cause='finished',ended_at=NOW))
    f['pilot']=pilot; f['consumption']=artifact(r,pilot)
    fr=freeze_value(r,f)
    approvals=[r.add(r.get(a,Approval).model_copy(update={'id':uuid4(),'code':f'REAL-APPROVAL-DECLARATION-{uuid4()}','synthetic_fixture':False})) for a in fr.approval_ids]
    body=fr.model_dump(mode='json',exclude={'freeze_hash'}) | {'approval_ids':[str(a.id) for a in approvals]}
    with pytest.raises(GateError,match='Pilot-Callbelege'): r.freeze(Freeze(**body,freeze_hash=digest(body)))
    # Even an incorporated response expressly originating from mock is insufficient.
    basic=artifact(r,pilot);_,attempt=call_attempt(r,pilot,f,basic)
    for seq,status in enumerate(('dispatching','response_saved','validated','incorporated'),1):
        r.add(TransportStateEvent(code=f'MOCK-PILOT-EVENT-{seq}',transport_id=attempt.id,sequence=seq,status=status,
            request_id='TECHNICAL-FIXTURE',generation_id='TECHNICAL-FIXTURE',response_artifact_id=None if seq==1 else basic.id,
            error_artifact_id=None,actual_model=observed(real.exact_model_id),actual_provider=observed('mock'),usage={},
            billing_status='not_due',format_status='pending' if seq==1 else 'valid'))
    with pytest.raises(GateError,match='Modell/Upstream'): r.freeze(Freeze(**body,freeze_hash=digest(body)))
    assert not r.all(Freeze)  # No actual provider access or approval was claimed.


def test_m3_22_calls_use_central_resolved_parameters(register):
    r,_=register;f=setup_study(r)
    run=free_run(r,f);basic=artifact(r,run)
    version=r.get(run.configuration_version_id,ConfigurationVersion)
    assert r.resolve_call_parameters(version,'analyzer') == {'seed':1}
    call=ModelCall(code='CENTRAL-PARAMETERS',run_id=run.id,node='analyzer',sequence=1,model_package_id=version.settings.model_a,
        request_hash=basic.sha256,messages_artifact_id=basic.id,parameters={'seed':1},input_artifact_ids=(),allowed_paths=(),tools=())
    for params in ({},{'seed':2},{'seed':True},{'seed':1,'hidden_limit':4}):
        with pytest.raises(GateError,match='Callparameter'):
            r.add(call.model_copy(update={'id':uuid4(),'code':f'WRONG-PARAM-{uuid4()}','parameters':params}))
    assert r.add(call).parameters == {'seed':1}
    # Explicit role overrides are versioned and resolved, never added on dispatch.
    r.set_state(run.id,RunState(execution='terminal',terminal_cause='finished',ended_at=NOW))
    settings=f['settings'].model_copy(update={'role_parameters':{'all':{'seed':2},'analyzer':{'seed':3}}})
    f['settings']=settings
    second=free_run(r,f,'ROLE-PARAM');second_basic=artifact(r,second)
    cv=r.get(second.configuration_version_id,ConfigurationVersion)
    assert r.resolve_call_parameters(cv,'analyzer') == {'seed':3}
    assert r.resolve_call_parameters(cv,'migrate') == {'seed':2}
    _, attempt=call_attempt(r,second,f,second_basic,params={'seed':3})
    assert r.get(attempt.call_id,ModelCall).parameters == {'seed':3}


def test_m3_23_measurement_test_and_profile_links_reject_foreign_evidence(register):
    r,_=register;f=setup_study(r)
    run=free_run(r,f);_,basic,comp=seal(r,run,f)
    m=measurement(r,run,comp,basic)
    other_run=free_run(r,f,'OTHER-FREE');_,other_basic,other_comp=seal(r,other_run,f)
    other_measure=measurement(r,other_run,other_comp,other_basic)
    result=TestResult(code='BOUND-RESULT',measurement_id=m.id,test_id='technical',assertion_id='technical',r_category='R1',
        fixture_id=m.fixture_id,input_artifact_id=basic.id,expected='control',actual=observed('control'),status='passed',cause='TECHNICAL-FIXTURE',raw_artifact_id=basic.id)
    for change in ({'measurement_id':basic.id},{'fixture_id':f['settings'].holdout_suite_id},{'input_artifact_id':other_basic.id},{'raw_artifact_id':other_basic.id}):
        with pytest.raises(GateError): r.add(result.model_copy(update={'id':uuid4(),'code':str(uuid4()),**change}))
    good=r.add(result)
    foreign_result=r.add(result.model_copy(update={'id':uuid4(),'code':'OTHER-RESULT','measurement_id':other_measure.id,
        'fixture_id':other_measure.fixture_id,'input_artifact_id':other_basic.id,'raw_artifact_id':other_basic.id}))
    functional=FunctionalProfile(code='BOUND-FUNCTIONAL',measurement_id=m.id,
        **{k:observed(1) for k in ('t1','t2','t3','t4','t5','r1','r2','r3','r4','r5','r6','t','full_success')},
        r=observed('0.5'),f=observed('0.5'),raw_pass_ratio=observed('0.5'),review_revision_ids=(),test_result_ids=(good.id,))
    r.add(functional)
    for change in ({'measurement_id':basic.id},{'test_result_ids':(foreign_result.id,)},{'review_revision_ids':(good.id,)}):
        with pytest.raises(GateError): r.add(functional.model_copy(update={'id':uuid4(),'code':str(uuid4()),**change}))
    static=StaticProfile(code='BOUND-STATIC',measurement_id=m.id,configuration_hash=f['tool'].manifest_hash,file_manifest_id=basic.id,
        excluded_files=(),diagnostics_artifact_id=basic.id,d=observed(1),l_by_file={'app/Module.php':observed(100)},l=observed(100),s=observed(1),exit_code=observed(0),failure_reason=None)
    r.add(static)
    for change in ({'measurement_id':basic.id},{'configuration_hash':'b'*64},{'file_manifest_id':other_basic.id},{'diagnostics_artifact_id':other_basic.id}):
        with pytest.raises(GateError): r.add(static.model_copy(update={'id':uuid4(),'code':str(uuid4()),**change}))
    restricted=artifact(r,run=None,access_scope='trusted_evaluator')
    with pytest.raises(GateError):
        measurement(r,run,comp,restricted,prev=m)
    with pytest.raises(ValidationError,match='Development'):
        AssetVersion(code='DEV-NAME-HOLDOUT-PROTECTION',asset_type='fixture_suite',manifest_hash=HASH,
            origin='TECHNICAL-FIXTURE',suite_kind='development',access_scope='trusted_evaluator')
    protected=asset(r,'fixture_suite','development')
    with pytest.raises(GateError):
        r.add(m.model_copy(update={'id':uuid4(),'code':'WRONG-FIXTURE','revision':2,'predecessor_id':m.id,'fixture_id':protected.id}))
    assert r.get(good.id,TestResult).fixture_id==run.suite_id


def test_m3_24_decimal_cost_and_numeric_profiles_persist_exactly(register):
    r,_=register;f=setup_study(r);run=free_run(r,f);basic=artifact(r,run)
    _,attempt=call_attempt(r,run,f,basic)
    exact=Decimal('0.123456789012345678901234567890123456789')
    amount=observed(exact,'USD')
    assert Observation.model_validate(amount.model_dump(mode='json')).value==exact
    assert Observation.model_validate(Observation(status='observed',value='12345',unit='provider id',source='TECHNICAL-FIXTURE').model_dump(mode='json')).value=='12345'
    cost=r.add(CostEntry(code='EXACT-DECIMAL-COST',run_id=run.id,transport_id=attempt.id,operating_area='free_test',amount=amount,
        currency='USD',price_evidence_id=f['basic'].id,billing_evidence_ids=(basic.id,),uncertainty='TECHNICAL-FIXTURE no paid call'))
    loaded=r.get(cost.id,CostEntry)
    assert type(loaded.amount.value) is Decimal and loaded.amount.value==exact
    usage=TokenUsage(**{name:missing() for name in ('input_tokens','output_tokens','cache_read_tokens','cache_write_tokens','reasoning_tokens','other_tokens')},completeness='missing')
    resource=ResourceProfile(code='EXACT-DECIMAL-RESOURCE',run_id=run.id,phase_id=run.phase_id,operating_area='free_test',token_usage=usage,
        transport_ids=(attempt.id,),cost_entry_ids=(cost.id,),interval_ids=(),repair_count=observed(0),context_preparation_completeness='missing',
        **{k:observed(exact,'s') for k in ResourceProfile.model_fields if k.endswith('_seconds')})
    resource=r.add(resource)
    assert r.get(resource.id,ResourceProfile).pipeline_seconds.value==exact
    metric=r.add(MetricObservation(code='EXACT-METRIC',run_id=run.id,phase_id=run.phase_id,metric='technical decimal value',value=observed(exact),
        measurement_id=None,interval_ids=(),file_scope=(),excluded_files=(),configuration_hash=None,diagnostics_artifact_id=basic.id))
    assert type(r.get(metric.id,MetricObservation).value.value) is Decimal and r.get(metric.id,MetricObservation).value.value==exact
    for change in ({'phase_id':f['phase'].id},{'transport_ids':(basic.id,)},{'cost_entry_ids':(basic.id,)},{'interval_ids':(basic.id,)}):
        with pytest.raises(GateError): r.add(resource.model_copy(update={'id':uuid4(),'code':str(uuid4()),**change}))
    for change in ({'phase_id':f['phase'].id},{'measurement_id':basic.id},{'diagnostics_artifact_id':f['consumption'].id}):
        with pytest.raises(GateError): r.add(metric.model_copy(update={'id':uuid4(),'code':str(uuid4()),**change}))


def test_m3_25_manual_review_and_numeric_value_domains(register):
    r,_=register;f=setup_study(r);run=free_run(r,f);_,basic,comp=seal(r,run,f);m=measurement(r,run,comp,basic)
    auto=CriterionReviewRevision(code='AUTOMATIC-DRAFT',run_id=run.id,criterion='T4',compatibility=comp,rubric_id=f['rubric'].id,revision=1,
        predecessor_id=None,completion='draft',verdict=missing(),reason='TECHNICAL-FIXTURE automatic preliminary finding',file_path='app/Module.php',lines='1',
        code_artifact_id=basic.id,measurement_ids=(m.id,),person='TECHNICAL-FIXTURE: no human review',reviewer_origin='automatic',reviewed_at=NOW)
    auto=r.add(auto)
    assert not r.valid(auto.id) and r.select_revision(run.id,'review','T4',comp) is None
    for criterion in ('T2','T3','T4'):
        with pytest.raises(ValidationError,match='menschliche'):
            CriterionReviewRevision.model_validate(auto.model_dump(mode='json') | {'criterion':criterion,'completion':'completed','verdict':observed(1).model_dump(mode='json')})
    human=review(r,run,comp,basic,f['rubric'],(m.id,),prev=auto)
    assert r.valid(human.id) and r.select_revision(run.id,'review','T4',comp).id==human.id
    for cls,values in ((BinaryObservation,(2,-1,'text',True,0.5)),(FractionObservation,(-1,2,'text',False)),
        (CountObservation,(-1,Decimal('1.5'),'text',True)),(NonnegativeObservation,(-1,'text',False)),(NumericObservation,('text',True,0.12))):
        for value in values:
            with pytest.raises(ValidationError): cls(status='observed',value=value,unit='technical',source='TECHNICAL-FIXTURE invalid value')
    with pytest.raises(ValidationError): BinaryObservation(status='estimated',value=Decimal(0),unit='binary',source='explicit estimate is not a criterion verdict')
    assert NonnegativeObservation(status='estimated',value=Decimal(0),unit='USD',source='explicitly costfree technical fixture').value==0
    with pytest.raises(ValidationError): NumericObservation(status='technical_missing',value=Decimal(0),unit='s',reason='Missing is not zero')
    entries={e['evidence_key']:e for e in catalog()['entries']}
    encoded=canonical(entries['CostEntry.amount.value']['type'])
    assert 'boolean' not in encoded and 'number' in encoded
    verdict=human.model_dump(mode='json')
    for value in (Decimal(2),'wrong',True):
        bad=observed(value).model_dump(mode='json') if not isinstance(value,bool) else Observation(status='observed',value=True,unit='binary',source='TECHNICAL-FIXTURE').model_dump(mode='json')
        with pytest.raises(ValidationError): validate_entry('CriterionReviewRevision.verdict.value',verdict | {'verdict':bad})


def test_m3_26_time_partition_requires_cross_process_boundary_proof():
    p1,p2=uuid4(),uuid4()
    one=interval('active',1,p1,0,10);two=interval('active',2,p2,0,6)
    out=summarize((one,two),coverage_complete=True)
    assert out['active'].status==out['total'].status=='technical_missing'
    assert out['known_partial_seconds']['active']=='16' and not out['partition_verified']
    linked=two.model_copy(update={'connection':IntervalConnection(previous_interval_id=one.id,relation='contiguous',evidence_ids=(uuid4(),))})
    assert summarize((one,linked),coverage_complete=True)['total'].value==16
    excluded=two.model_copy(update={'connection':IntervalConnection(previous_interval_id=one.id,relation='excluded_gap',evidence_ids=(uuid4(),))})
    out=summarize((one,excluded),coverage_complete=True)
    assert out['active'].value==16 and out['total'].status=='technical_missing'
    unknown=two.model_copy(update={'connection':IntervalConnection(previous_interval_id=one.id,relation='unknown_active',evidence_ids=(uuid4(),))})
    assert summarize((one,unknown),coverage_complete=True)['active'].status=='technical_missing'
    with pytest.raises(ValueError,match='Verknüpfungsbeleg'):
        summarize((one,linked.model_copy(update={'connection':linked.connection.model_copy(update={'previous_interval_id':uuid4()})})),coverage_complete=True)
    same_process_gap=interval('active',2,p1,20,26)
    assert summarize((one,same_process_gap),coverage_complete=True)['active'].status=='technical_missing'


def declared_model_pair(r,f,*,identical=False):
    """No actual model access: immutable synthetic declarations exercise real-branch gates."""
    old=r.get(f['settings'].model_a,ModelPackage)
    models=[r.add(old.model_copy(update={'id':uuid4(),'code':f'DECLARED-MODEL-{letter}-{uuid4()}',
        'exact_model_id':'TECHNICAL-FIXTURE:model-'+('A' if identical else letter),'endpoint':'https://provider.invalid/'+letter,
        'upstream':'TECHNICAL-FIXTURE:declared-provider-'+letter})) for letter in ('A','B')]
    f['settings']=f['settings'].model_copy(update={'model_a':models[0].id,'model_b':models[1].id})
    f['versions']=r.central_update(f['phase'].id,f['settings'])
    return models


def declared_pilot(r,f,key,*,missing_node=None,foreign_provider=False,wrong_model=False,repair=False,changed_assets=False,disordered=False):
    """Pure stored fixtures: no provider, pipeline, human judgment or financial approval executed."""
    conf=r.add(Configuration(code=f'FULL-PILOT-CONF-{key}-{uuid4()}',phase_id=f['pilot'].phase_id))
    settings=f['settings'].model_copy(update={'handoff_id':asset(r,'handoff').id}) if changed_assets else f['settings']
    v=r.version_configuration(conf.id,key,MAIN_CELLS[key],settings)
    scope=r.configuration_scope(f['pilot'].phase_id,v.id)
    proofs=tuple(EvidenceProof(key=k,asset_id=f['tool'].id,asset_hash=f['tool'].manifest_hash,scope_hash=scope) for k in REQUIRED_GATES)
    for kind,keys in (('technical',TECHNICAL_GATES),('subject',HUMAN_GATES),('cost',RESOURCE_GATES)):
        r.add(Approval(code=f'DECLARED-PILOT-GATE-{uuid4()}',phase_id=f['pilot'].phase_id,kind=kind,scope_hash=scope,
            person='TECHNICAL-FIXTURE: no real human decision',reason='Stored synthetic gate declaration; zero external requests',
            evidence=tuple(p for p in proofs if p.key in keys),paid_calls_consent=kind=='cost',synthetic_fixture=False))
    run,_=r.start_other(f['pilot'].phase_id,v.id,decision='TECHNICAL-FIXTURE no actual model call',technical_evidence_ids=(f['basic'].id,),idempotency_key=str(uuid4()))
    raw=artifact(r,run)
    nodes=['analyzer']+(['planner'] if v.cell.planner else [])+['migrate','test']+(['review'] if v.cell.review else [])
    if repair:
        nodes.append('repair')  # Fixture explicitly simulates a test/review-triggered repair branch.
    if disordered:
        nodes[0],nodes[1]=nodes[1],nodes[0]
    for sequence,node in enumerate([n for n in nodes if n!=missing_node],1):
        model=r.resolve_call_model(v,node)
        call=r.add(ModelCall(code=f'FULL-PILOT-CALL-{uuid4()}',run_id=run.id,node=node,sequence=sequence,model_package_id=model.id,
            request_hash=raw.sha256,messages_artifact_id=raw.id,parameters=r.resolve_call_parameters(v,node),input_artifact_ids=(),allowed_paths=(),tools=()))
        attempt=r.add(TransportAttempt(code=f'FULL-PILOT-TRANSPORT-{uuid4()}',call_id=call.id,number=1,send_status='prepared',request_id=None,
            generation_id=None,sent_at=None,ended_at=None,actual_model=missing(),actual_provider=missing(),response_artifact_id=None,error_artifact_id=None,
            format_status='pending',usage={},billing_status='not_due'))
        for seq,status in enumerate(('dispatching','response_saved','validated','incorporated'),1):
            r.add(TransportStateEvent(code=f'FULL-PILOT-EVENT-{uuid4()}',transport_id=attempt.id,sequence=seq,status=status,
                request_id=f'TECHNICAL-FIXTURE:{call.id}',generation_id=f'TECHNICAL-FIXTURE:{attempt.id}',response_artifact_id=None if seq==1 else raw.id,
                error_artifact_id=None,actual_model=observed('TECHNICAL-FIXTURE:wrong-model' if wrong_model else model.exact_model_id),
                actual_provider=observed('TECHNICAL-FIXTURE:foreign-provider' if foreign_provider else model.upstream),usage={},
                billing_status='not_due',format_status='pending' if seq==1 else 'valid'))
    r.set_state(run.id,RunState(execution='terminal',terminal_cause='finished',ended_at=NOW))
    return run


def declared_freeze(r,f,pilots):
    f['pilot_runs']=tuple(pilots)
    f['consumption']=artifact(r,pilots[0])
    fr=freeze_value(r,f)
    approvals=[r.add(r.get(a,Approval).model_copy(update={'id':uuid4(),'code':f'DECLARED-MAIN-GATE-{uuid4()}','synthetic_fixture':False})) for a in fr.approval_ids]
    body=fr.model_dump(mode='json',exclude={'freeze_hash'}) | {'approval_ids':[str(a.id) for a in approvals]}
    return Freeze(**body,freeze_hash=digest(body))


def test_m3_27_measured_gap_cannot_be_overruled_by_contiguity():
    p=uuid4();one=interval('active',1,p,0,10);two=interval('active',2,p,20,26)
    link=IntervalConnection(previous_interval_id=one.id,relation='contiguous',evidence_ids=(uuid4(),))
    with pytest.raises(ValueError,match='monotoner Zeitlücke'):
        summarize((one,two.model_copy(update={'connection':link})),coverage_complete=True)
    utc_one=one.model_copy(update={'started_at':NOW,'ended_at':NOW+timedelta(seconds=10)})
    utc_two=two.model_copy(update={'started_at':NOW+timedelta(seconds=10),'ended_at':NOW+timedelta(seconds=16)})
    with pytest.raises(ValueError,match='monotoner Zeitlücke'):
        summarize((utc_one,utc_two),coverage_complete=True)
    adjacent=two.model_copy(update={'monotonic_start':Decimal(10),'monotonic_end':Decimal(16),'connection':link})
    result=summarize((one,adjacent),coverage_complete=True)
    assert result['active'].value==result['total'].value==16 and result['partition_verified']
    excluded=two.model_copy(update={'connection':link.model_copy(update={'relation':'excluded_gap'})})
    result=summarize((one,excluded),coverage_complete=True)
    assert result['active'].value==16 and result['total'].status=='technical_missing'
    assert result['known_partial_seconds']['active']=='16'
    impossible_utc=adjacent.model_copy(update={'started_at':NOW+timedelta(seconds=20),'ended_at':NOW+timedelta(seconds=26)})
    with pytest.raises(ValueError,match='UTC-Zeitlücke'):
        summarize((utc_one,impossible_utc),coverage_complete=True)


def test_m3_28_complete_twelve_cell_role_paths_accept_declared_fixture(register):
    r,_=register;f=setup_study(r);declared_model_pair(r,f)
    pilots=[declared_pilot(r,f,key,repair=key=='E-AB') for key in MAIN_CELLS]
    calls=[c for c in r.all(ModelCall) if c.run_id in {p.id for p in pilots}]
    assert len(pilots)==12 and len(calls)==57  # 56 base roles plus one explicitly triggered technical repair.
    assert sum(c.node=='repair' for c in calls)==1
    fr=r.freeze(declared_freeze(r,f,pilots))
    assert len(fr.pilot_run_ids)==12 and len(r.fq_ids(fr.id))==48
    assert f['study'].data_origin=='synthetic'  # Metadata gate acceptance is no claim of real model execution.


@pytest.mark.parametrize('case',('only_E_AB','missing_planner','foreign_upstream','wrong_model','changed_assets','disordered_path'))
def test_m3_29_incomplete_or_incompatible_real_pilot_paths_rejected(register,case):
    r,_=register;f=setup_study(r);declared_model_pair(r,f)
    keys=['E-AB'] if case=='only_E_AB' else list(MAIN_CELLS)
    pilots=[]
    for key in keys:
        bad=key=='E-AB'
        pilots.append(declared_pilot(r,f,key,missing_node='planner' if bad and case=='missing_planner' else None,
            foreign_provider=bad and case=='foreign_upstream',wrong_model=bad and case=='wrong_model',
            changed_assets=bad and case=='changed_assets',disordered=bad and case=='disordered_path'))
    expected='Modell/Upstream' if case in ('foreign_upstream','wrong_model') else 'Pilotbedingungen' if case=='changed_assets' else 'Pilot-Callbelege'
    with pytest.raises(GateError,match=expected): r.freeze(declared_freeze(r,f,pilots))
    assert not r.all(Freeze)


def test_m3_30_real_a_b_need_different_exact_ids_but_shared_mock_remains(register):
    r,_=register;f=setup_study(r);models=declared_model_pair(r,f,identical=True)
    assert models[0].id!=models[1].id and models[0].exact_model_id==models[1].exact_model_id
    with pytest.raises(GateError,match='verschieden identifizierte'):
        r.freeze(declared_freeze(r,f,[f['pilot']]))
    assert not r.all(Freeze)
    # Existing M3_03/M3_04 and all costfree S1-style fixtures retain the common pure mock.


def test_m3_31_process_event_interval_references_are_typed_and_run_bound(register):
    r,_=register;f=setup_study(r);run=free_run(r,f);raw=artifact(r,run)
    proc=r.add(ProcessInstance(code='TYPED-PROCESS',worker='TECHNICAL-FIXTURE',software_commit='synthetic',platform='synthetic',started_at=NOW,clock_description='process-local monotonic'))
    restarted=r.add(proc.model_copy(update={'id':uuid4(),'code':'TYPED-RESTART-PROCESS'}))
    first=interval('active',1,proc.id,0,10).model_copy(update={'run_id':run.id,'evidence_ids':(raw.id,)})
    for change in ({'process_id':raw.id,'end_process_id':raw.id},{'run_id':raw.id}):
        with pytest.raises(GateError): r.add(first.model_copy(update={'id':uuid4(),'code':str(uuid4()),**change}))
    # Unknown durations may carry an end-process reference; it must also be typed.
    bad_end=interval('excluded_gap',1,None,None,None).model_copy(update={'run_id':run.id,'end_process_id':raw.id,'evidence_ids':(raw.id,)})
    with pytest.raises(GateError): r.add(bad_end)
    first=r.add(first)
    diagnostic=Event(code='RESTART-DIAGNOSIS',run_id=run.id,sequence=1,event_type='prior_interval_diagnosis',process_id=restarted.id,
        interval_id=first.id,happened_at=NOW,evidence_ids=(raw.id,),details={'fixture':'Synthetic new emitter diagnoses prior process interval'})
    for change in ({'process_id':raw.id},{'interval_id':raw.id},{'run_id':raw.id}):
        with pytest.raises(GateError): r.add(diagnostic.model_copy(update={'id':uuid4(),'code':str(uuid4()),**change}))
    diagnostic=r.add(diagnostic)
    assert r.get(diagnostic.id,Event).process_id != r.get(first.id,TimeInterval).process_id
    # Legitimate artifact and event evidence stay usable without a one-type restriction.
    link=IntervalConnection(previous_interval_id=first.id,relation='contiguous',evidence_ids=(raw.id,diagnostic.id))
    second=r.add(interval('active',2,proc.id,10,15).model_copy(update={'run_id':run.id,'evidence_ids':(raw.id,diagnostic.id),'connection':link}))
    r.set_state(run.id,RunState(execution='terminal',terminal_cause='finished',ended_at=NOW))
    other=free_run(r,f,'OTHER-PROCESS-RUN');other_raw=artifact(r,other)
    foreign=r.add(interval('active',1,restarted.id,0,5).model_copy(update={'run_id':other.id,'evidence_ids':(other_raw.id,)}))
    for change in ({'interval_id':foreign.id},{'run_id':other.id}):
        with pytest.raises(GateError,match='fremdem Lauf'):
            r.add(diagnostic.model_copy(update={'id':uuid4(),'code':str(uuid4()),'sequence':2,**change}))
    third=interval('active',3,proc.id,15,20).model_copy(update={'run_id':run.id,'evidence_ids':(diagnostic.id,),
        'connection':IntervalConnection(previous_interval_id=second.id,relation='contiguous',evidence_ids=(diagnostic.id,))})
    for wrong_previous in (raw.id,foreign.id,first.id):
        with pytest.raises(GateError):
            r.add(third.model_copy(update={'id':uuid4(),'code':str(uuid4()),'connection':third.connection.model_copy(update={'previous_interval_id':wrong_previous})}))
    third=r.add(third)
    assert r.get(third.id,TimeInterval).connection.previous_interval_id==second.id
    assert r.get(third.id,TimeInterval).evidence_ids==(diagnostic.id,)
    assert r.connection.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert not r.connection.execute('PRAGMA foreign_key_check').fetchall()

"""#15 receipt boundary; synthetic records only, never provider/empirical execution."""
import hashlib
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from research_env.config import Settings
from research_env.artifacts import ArtifactStore
from research_env.domain import (Approval, AssetVersion, Backup, BackupReceipt, ConfigurationVersion,
    Freeze, ModelCall, ModelPackage, Study, StudyPhase)
from research_env.preparation import imported_catalog
from research_env.register import GateError
from test_register import register
import test_register as fixture

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('issue15_fixture', ROOT / 'scripts/verify-integrated-mock.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def unchanged_files(settings):
    """Whole instance bytes/modes, including the SQLite database and CAS originals."""
    return {str(p.relative_to(settings.control.parent)): (hashlib.sha256(p.read_bytes()).hexdigest(),
            p.stat().st_mode & 0o7777) for root in (settings.control, settings.artifacts,
            settings.checkpoints, settings.staging) for p in root.rglob('*') if p.is_file()}


def reject_unchanged(r, settings, fid):
    before = unchanged_files(settings)
    revision = r.revision(r.get(fid, Freeze).phase_id)
    with pytest.raises(GateError):
        helper.receipt(r, fid)
    assert unchanged_files(settings) == before
    assert r.revision(r.get(fid, Freeze).phase_id) == revision
    assert not r.all(Backup) and not r.all(BackupReceipt) and not r.all(ModelCall)
    assert r.backup_status(fid) != 'current'


def test_own_bound_mock_receipt_and_shared_actual_tool(register):
    r, settings = register
    result = helper.prepare(r); fid = UUID(result['freeze_id']); f = r.get(fid, Freeze)
    tool = imported_catalog(r)[0]['tool']
    assert all(r.get(v, ConfigurationVersion).settings.tool_ids == (tool.id,)
               for v in f.configuration_version_ids.values())
    assert not any(a.code.startswith('ISSUE15-EVALUATOR-') for a in r.all(AssetVersion))
    assert len(f.matrix) == 48 and not r.all(ModelCall)
    revision = r.revision(f.phase_id)
    receipt = helper.receipt(r, fid)
    assert receipt['gate'] == 'current' and receipt['physical_independent_backup'] is False
    assert r.revision(f.phase_id) == revision and not r.all(ModelCall)
    assert len(r.all(Backup)) == len(r.all(BackupReceipt)) == 1
    # A normal persisted phase transition changes the revision; renewing stays in own scope.
    r.set_phase_status(f.phase_id, 'paused', reason='SYNTHETIC technical idle pause')
    assert r.backup_status(fid) == 'stale'
    assert helper.receipt(r, fid)['gate'] == 'current'
    assert len(r.all(Backup)) == len(r.all(BackupReceipt)) == 2


def test_foreign_valid_synthetic_freeze_rejected_without_write(register):
    r, settings = register
    other = fixture.setup_study(r); f = r.freeze(fixture.freeze_value(r, other))
    assert r.get(other['study'].id, Study).data_origin == 'synthetic'
    reject_unchanged(r, settings, f.id)


def controlled_scope_reads(r, monkeypatch, values):
    """Typed negative read inputs only; never overwrite immutable register originals.

    The valid foreign-freeze and installation cases use actual persistent inputs.
    These additional unit controls isolate the other guard predicates without
    constructing/approving an empirical study or weakening SQLite immutability.
    """
    original = r.get
    substitutes = {v.id: v for v in values}
    monkeypatch.setattr(r, 'get', lambda entity_id, cls=None:
        substitutes.get(entity_id) or original(entity_id, cls))


@pytest.mark.parametrize('boundary', ('empirical_scope', 'foreign_installation', 'foreign_study',
                                     'nonmock_endpoint', 'unbound_preparation', 'human_approval'))
def test_marked_fixture_rejects_foreign_or_real_scope_before_writes(register, monkeypatch, boundary):
    r, settings = register
    result = helper.prepare(r); fid = UUID(result['freeze_id']); f = r.get(fid, Freeze)
    phase = r.get(f.phase_id, StudyPhase); study = r.get(phase.study_id, Study)
    version = r.get(next(iter(f.configuration_version_ids.values())), ConfigurationVersion)
    values = []
    if boundary == 'empirical_scope':
        values.append(study.model_copy(update={'data_origin': 'empirical'}))
    elif boundary == 'foreign_installation':
        r.connection.execute("UPDATE runtime_metadata SET value=? WHERE key='installation_id'", (str(uuid4()),))
    elif boundary == 'foreign_study':
        other = r.add(Study(code='FOREIGN-' + str(uuid4()), title='SYNTHETIC foreign',
            design_version='negative-fixture', data_origin='synthetic', provenance=helper.PROVENANCE))
        values.append(phase.model_copy(update={'study_id': other.id}))
    elif boundary == 'nonmock_endpoint':
        model = r.get(version.settings.model_b, ModelPackage)
        values.append(model.model_copy(update={'endpoint': 'https://invalid.example/no-calls'}))
    elif boundary == 'unbound_preparation':
        old = ArtifactStore(settings, r).json({'synthetic': True, 'expected_matrix': 48,
            'source': helper.software_identity(), 'human_approval': False}, artifact_type='issue15_preparation')
        for vid in f.configuration_version_ids.values():
            v = r.get(vid, ConfigurationVersion)
            values.append(v.model_copy(update={'settings': v.settings.model_copy(
                update={'working_copy_evidence_id': old.id})}))
    else:
        a = r.get(f.approval_ids[0], Approval)
        values.append(a.model_copy(update={'synthetic_fixture': False}))
    controlled_scope_reads(r, monkeypatch, values)
    reject_unchanged(r, settings, fid)


@pytest.mark.parametrize('boundary', ('missing_database', 'foreign_freeze', 'own_fixture'))
def test_receipt_cli_preflight_rejects_before_writable_register(register, tmp_path, boundary):
    r, settings = register
    if boundary == 'missing_database':
        settings = Settings(*(tmp_path / 'foreign-no-database' / name
                              for name in ('control', 'artifacts', 'checkpoints', 'staging')))
        for path in (settings.control, settings.artifacts, settings.checkpoints, settings.staging):
            path.mkdir(parents=True)
        fid = uuid4()
    elif boundary == 'foreign_freeze':
        other = fixture.setup_study(r); fid = r.freeze(fixture.freeze_value(r, other)).id
    else:
        fid = UUID(helper.prepare(r)['freeze_id'])
    before = unchanged_files(settings)
    output = tmp_path / 'cli-receipt-result.json'
    command = [sys.executable, str(ROOT / 'scripts/verify-integrated-mock.py'), '--instance',
               str(settings.control.parent), '--output', str(output), '--mode', 'receipt', '--freeze', str(fid)]
    start = datetime.now(timezone.utc).isoformat()
    result = subprocess.run(command, capture_output=True)
    (tmp_path / 'cli.stdout.raw').write_bytes(result.stdout)
    (tmp_path / 'cli.stderr.raw').write_bytes(result.stderr)
    (tmp_path / 'cli.execution.json').write_text(json.dumps({'actualCommand': command,
        'actualExit': result.returncode, 'utc_before': start,
        'utc_after': datetime.now(timezone.utc).isoformat()}, indent=2) + '\n')
    if boundary == 'own_fixture':
        assert result.returncode == 0 and json.loads(output.read_text())['gate'] == 'current'
        assert r.backup_status(fid) == 'current' and not r.all(ModelCall)
    else:
        assert result.returncode != 0 and not output.exists()
        assert unchanged_files(settings) == before
        assert not r.all(Backup) and not r.all(BackupReceipt) and not r.all(ModelCall)
        if boundary == 'missing_database':
            assert not settings.database.exists()
        else:
            assert r.backup_status(fid) != 'current'

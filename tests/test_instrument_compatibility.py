"""Explicit approved old instrument identities, no providers or native execution."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from uuid import uuid4

import pytest
from research_env import preparation, evaluation, static_analysis
from research_env.application_settings import default_settings
from research_env.artifacts import ArtifactStore, IntegrityError
from research_env.completion import CompletionWorker
from research_env.domain import AssetVersion, ConfigurationVersion, StudyPhase, ModelCall, canonical, digest
from research_env.matrix_readiness import require_ready
from research_env.preparation import latest_versions, put_configuration
from research_env.preparation_freeze import preview
from research_env.sandbox_runtime import RuntimeImages, Sandbox
from research_env.snapshots import Snapshots, ProcessWriters
from test_register import register
from test_simple_matrix import draft, fields

ROOT=Path(__file__).resolve().parents[1]
ARCHIVE=json.loads((Path(__file__).parent/'fixtures/ui-backup-original-instruments.json').read_text())
OLD=ARCHIVE['software']


@pytest.fixture
def legacy_matrix(register,monkeypatch):
    r,settings=register
    images=RuntimeImages(**json.loads((ROOT/'src/research_env/pipeline_runtime.lock.json').read_text()))
    # The before/after environment is identical. On other test machines only
    # native environment metadata is adapted; archived old source hashes remain.
    # The deployed real policy is additionally checked against the user's matrix.
    previous=[]
    for archived,factory in zip(ARCHIVE['instruments'],(evaluation.instrument_manifest,static_analysis.instrument_manifest)):
        previous.append({**factory(images),'sources':archived['body']['sources']})
    policy_path=ROOT/'src/research_env/execution_compatibility.json'
    policy=json.loads(policy_path.read_text())
    policy['releases'][OLD]={name:hashlib.sha256((policy_path.parent/name).read_bytes()).hexdigest() for name in policy['releases'][OLD]}
    policy['instruments'][OLD]={digest(body):digest({k:v for k,v in body.items() if k!='sources'}) for body in previous}
    read_text=Path.read_text
    monkeypatch.setattr(Path,'read_text',lambda path,*args,**kwargs:json.dumps(policy) if path==policy_path else read_text(path,*args,**kwargs))
    # Synthetic isolated approvals never modify the shipped release policy.
    with monkeypatch.context() as old:
        old.setattr(evaluation,'instrument_manifest',lambda images:deepcopy(previous[0]))
        old.setattr(static_analysis,'instrument_manifest',lambda images:deepcopy(previous[1]))
        old.setattr(preparation,'software_identity',lambda:OLD)
        study,phase,s=draft(r)
        _,intent=preview(r,fields(r,phase))
        freeze=r.freeze(intent.freeze,approvals=tuple(a.model_copy(update={'synthetic_fixture':True}) for a in intent.approvals))
    return r,study,phase,freeze,s


def test_fixed_matrix_is_ready_after_approved_ui_update_without_rewriting(legacy_matrix):
    r,study,phase,freeze,s=legacy_matrix
    before={row['id']:row['payload'] for row in r.connection.execute('SELECT id,payload FROM register_record')}
    require_ready(r,latest_versions(r,phase.id))
    r._gates(freeze)
    from research_env.preparation_views import study as view
    assert view(r,study.id)['phase_rows'][0]['freezes'][0]['gate_error'] is None
    assert before=={row['id']:row['payload'] for row in r.connection.execute('SELECT id,payload FROM register_record')}
    assert not r.all(ModelCall)


def test_both_real_measurement_schedulers_keep_frozen_tools_and_record_actual_manifest(legacy_matrix,tmp_path):
    r,study,phase,freeze,s=legacy_matrix
    conf=r.get(freeze.configuration_version_ids['C-SQL-1'],ConfigurationVersion)
    images=RuntimeImages(**json.loads((ROOT/'src/research_env/pipeline_runtime.lock.json').read_text()))
    store=ArtifactStore(r.settings,r);sandbox=Sandbox(store,None,images=images,assets=ROOT)
    worker=CompletionWorker(r,sandbox)
    functional=worker.tool(conf,evaluation.instrument_manifest(images))
    static=worker.tool(conf,static_analysis.instrument_manifest(images))
    assert (functional,static)==s.tool_ids
    # A synthetic preparation run exercises the actual scheduling gates.
    testphase=r.add(StudyPhase(code='COMPAT-PREPARATION',study_id=study.id,purpose='preparation',provenance='Synthetic scheduling test; no model execution'))
    version=put_configuration(r,testphase.id,'CHECK',conf.cell,s)
    proof=store.json({'synthetic':True},artifact_type='technical_fixture')
    run,job=r.start_other(testphase.id,version.id,decision='Synthetic scheduling only',technical_evidence_ids=(proof.id,),idempotency_key='compat-run')
    tree=tmp_path/'candidate';shutil.copytree(ROOT/'assets/study/m2-v0.1/scaffold',tree)
    deps=tmp_path/'deps';deps.mkdir();(deps/'autoload.php').write_text('<?php // SYNTHETIC')
    snapshots=Snapshots(store);dependency=snapshots.dependencies(deps,source={'synthetic':True},runtime={'synthetic':True})
    snapshots.seal(tree,writers=ProcessWriters(),run_id=run.id,scaffold_id=s.scaffold_id,dependency_ids=(dependency.id,),internal_tests_hash=hashlib.sha256(b'').hexdigest())
    r.set_state(run.id,r.state(run.id).model_copy(update={'execution':'terminal','terminal_cause':'finished','ended_at':datetime.now(timezone.utc)}))
    saved=canonical(version)
    for service,tool,manifest in [(worker.evaluator,functional,evaluation.instrument_manifest(images)),(worker.static,static,static_analysis.instrument_manifest(images))]:
        eid=service.schedule(run.id,tool_id=tool,idempotency_key='compat-'+str(tool))
        row=service.row(eid)
        assert row['tool_id']==str(tool)
        assert json.loads(row['body'])['instrument']==manifest
        binding=json.loads(row['body'])['instrument_compatibility']
        assert binding=={'frozen_manifest_sha256':r.get(tool,AssetVersion).manifest_hash,'actual_manifest_sha256':digest(manifest),'mode':'approved-ui-backup-update-2026-10-06'}
        assert digest(manifest)!=r.get(tool,AssetVersion).manifest_hash
    assert canonical(r.get(version.id,ConfigurationVersion))==saved
    assert not r.all(ModelCall)


@pytest.mark.parametrize('index,field',[(0,'public_contract'),(0,'libraries'),(0,'runtime_images'),(0,'parser'),(0,'normalization'),(1,'measure'),(1,'tool'),(1,'images'),(1,'phpstan_args')])
def test_changed_scientific_or_runtime_parameter_is_rejected(legacy_matrix,index,field):
    from research_env.instrument_compatibility import instrument_compatible
    r,_,_,_,s=legacy_matrix
    images=RuntimeImages(**json.loads((ROOT/'src/research_env/pipeline_runtime.lock.json').read_text()))
    manifest=(evaluation.instrument_manifest,static_analysis.instrument_manifest)[index](images)
    assert instrument_compatible(r,s.tool_ids[index],manifest,OLD)
    manifest[field]={'SYNTHETIC_CHANGED_PARAMETER':True}
    assert not instrument_compatible(r,s.tool_ids[index],manifest,OLD)


@pytest.mark.parametrize('name',['evaluation_oracles.py','role_formats.py','static_tokens.php','pipeline.py'])
def test_changed_protected_implementation_is_rejected(legacy_matrix,monkeypatch,name):
    from research_env.instrument_compatibility import instrument_compatible
    r,_,_,_,s=legacy_matrix
    images=RuntimeImages(**json.loads((ROOT/'src/research_env/pipeline_runtime.lock.json').read_text()))
    manifest=evaluation.instrument_manifest(images)
    original=Path.read_bytes
    def changed(path):
        value=original(path)
        return value+b'\nSYNTHETIC MUTATION' if path.name==name else value
    monkeypatch.setattr(Path,'read_bytes',changed)
    assert not preparation.software_compatible(OLD)
    assert not instrument_compatible(r,s.tool_ids[0],manifest,OLD)


def test_unknown_source_or_instrument_never_inherits_approval(legacy_matrix):
    from research_env.instrument_compatibility import instrument_compatible
    r,_,_,_,s=legacy_matrix
    images=RuntimeImages(**json.loads((ROOT/'src/research_env/pipeline_runtime.lock.json').read_text()))
    manifest=evaluation.instrument_manifest(images)
    assert not instrument_compatible(r,s.tool_ids[0],manifest,'source-tree-sha256:'+'0'*64)
    foreign=r.add(AssetVersion(code='UNAPPROVED-INSTRUMENT',asset_type='tool',manifest_hash='1'*64,origin='Synthetic negative fixture',access_scope='trusted_register'))
    assert not instrument_compatible(r,foreign.id,manifest,OLD)



def test_shipped_historical_policy_is_unchanged_and_rejects_current_sources():
    path=ROOT/'src/research_env/execution_compatibility.json'
    policy=json.loads(path.read_text())
    # Preserve the exact reviewed historical release. Later source changes do
    # not inherit that approval, even when only the reporting UI is intended.
    assert digest(policy['releases'][OLD])=='956295f77353b770b230c0074e0a8ab3eb665019afb2c53bab6f22e6eb3eb591'
    assert set(policy['instruments'][OLD])=={item['hash'] for item in ARCHIVE['instruments']}
    for item in ARCHIVE['instruments']:
        assert digest(item['body'])==item['hash']
        assert policy['instruments'][OLD][item['hash']]==digest({k:v for k,v in item['body'].items() if k!='sources'})
    assert policy['releases'][OLD]['pipeline_runtime.lock.json']==ARCHIVE['instruments'][0]['body']['sources']['pipeline_runtime.lock.json']
    assert preparation.software_identity()!=OLD
    assert not preparation.software_compatible(OLD)

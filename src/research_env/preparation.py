"""M8 typed, durable UI commands. External work is exclusively worker-owned."""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import platform
import tarfile
from typing import Literal
from uuid import UUID, uuid4


from .artifacts import ArtifactStore, IntegrityError
from .domain import (Approval, Artifact, AssetVersion, Cell, Configuration, ConfigurationVersion,
    Contract, EffectiveSettings, Event, Freeze, ModelPackage, Observation, ProcessInstance, Run,
    Series, Study, StudyPhase, canonical, digest)
from .register import GateError

ROOT = Path(__file__).resolve().parents[2]


class Intent(Contract):
    action: Literal['draft', 'demo', 'free', 'central', 'new_phase', 'main', 'series', 'pause', 'abort', 'resume', 'freeze', 'analysis_propose', 'analysis_confirm', 'initialize', 'model_lookup', 'model_import', 'inspection_prepare']
    target_id: UUID | None = None
    version_id: UUID | None = None
    planned_id: UUID | None = None
    series_id: UUID | None = None
    title: str = 'Eigene Studie'
    person: str = ''
    decision: str = ''
    paid_consent: bool = False
    immediate: bool = False
    settings: EffectiveSettings | None = None
    cell: Cell | None = None
    base_hash: str | None = None
    freeze: Freeze | None = None
    approvals: tuple[Approval, ...] = ()
    model_id: str = ''
    endpoint: str = ''
    model_parameters: dict = {}


def latest_versions(register, phase_id):
    return {v.cell_key: v for v in register.all(ConfigurationVersion) if register._phase(v) == phase_id}


def versions_hash(register, phase_id):
    return digest({k: str(v.id) for k, v in latest_versions(register, phase_id).items()})


def active_generation(register):
    return register.connection.execute("SELECT r.run_id FROM run_binding r WHERE r.execution!='terminal' OR EXISTS (SELECT 1 FROM pipeline_binding p WHERE p.run_id=r.run_id AND p.status!='completed') LIMIT 1").fetchone()


def check_start(register, intent):
    """Read-only preliminary check; the worker/Register repeat all authoritative gates."""
    if not intent.person.strip() or not intent.decision.strip():
        raise GateError('Name und bewusste konkrete Startentscheidung fehlen')
    if active_generation(register):
        raise GateError('Ein Auftrag ist aktiv/pausiert/ungeklärt. Erst denselben Auftrag abschließen oder sicher wiederaufnehmen.')
    if register.connection.execute("SELECT 1 FROM run_completion WHERE status IN ('running','retry_requested')").fetchone():
        raise GateError('Die automatische Auswertung läuft noch. Anschließend kann der nächste Lauf starten.')
    if intent.action == 'main':
        freeze = register.get(intent.target_id, Freeze)
        register._gates(freeze)
        if register.next_id(freeze.id) != intent.planned_id:
            raise GateError('Nur die aktuell nächste eingefrorene ID ist zulässig')
        if register.backup_status(freeze.id) != 'current':
            raise GateError('Aktuelle bestätigte externe Sicherung fehlt. In der Laufmatrix „Sicherung vorbereiten“ wählen, ZIP auf deinem Datenträger speichern und die tatsächliche Ablage bestätigen.')
        conf = register.get(freeze.configuration_version_ids[register.get(intent.planned_id).cell_key], ConfigurationVersion)
        if intent.series_id:
            series = register.get(intent.series_id, Series)
            if series.freeze_id != freeze.id or series.mode != 'series':
                raise GateError('Serie gehört nicht zu diesem Freeze')
    else:
        conf = register.get(intent.version_id, ConfigurationVersion)
        phase = register.get(intent.target_id, StudyPhase)
        if register._phase(conf) != phase.id or phase.purpose == 'main':
            raise GateError('Freier/Pilot-/Demostart gehört nicht zur gewählten Phase')
        # A free technical run records this form's explicit cost decision in the
        # worker before Register.start_other. Formal pilots retain all gates.
        if not (phase.purpose=='free_test' and intent.paid_consent and not register._is_mock(conf)):
            register._other_gates(phase, conf)
    if not register._is_mock(conf) and not intent.paid_consent:
        raise GateError('Finanzen manuell prüfen und diesen konkreten bezahlten Start ausdrücklich bestätigen')
    if not software_compatible(conf.settings.software_commit):
        raise GateError('Softwarestand der Konfiguration stimmt nicht mit der tatsächlich laufenden Sourcebindung überein. Neue Konfigurationsversion bzw. bei Freeze neue Erhebungsphase erforderlich; kein stilles Überschreiben.')
    return conf


def submit(register, key, intent):
    if not key.strip() or len(key) > 200:
        raise GateError('Gültiger Idempotenzschlüssel erforderlich')
    payload = canonical(intent)
    with register.transaction():
        old = register.connection.execute('SELECT * FROM ui_command WHERE idempotency_key=?', (key,)).fetchone()
        if old:
            if old['payload_hash'] != digest(intent):
                raise GateError('Idempotenzschlüssel gehört zu anderem unveränderlichem Auftrag')
            return old['id']
        if intent.action in ('main',) or intent.action == 'demo' and intent.target_id:
            check_start(register, intent)
        if intent.action in ('main', 'demo'):
            if active_generation(register) or register.connection.execute("SELECT 1 FROM ui_command WHERE kind IN ('main','demo') AND status IN ('queued','processing','recovery_required')").fetchone():
                raise GateError('Erst bestehenden Startauftrag bearbeiten; keine zusätzliche Generation')
        if intent.action=='analysis_propose':
            freeze=register.get(intent.target_id,Freeze)
            if register.get(freeze.phase_id,StudyPhase).purpose!='main':raise GateError('Analyse nur eingefrorene Mainphase')
        if intent.action=='analysis_confirm':
            if not register.connection.execute('SELECT 1 FROM analysis_selection WHERE proposal_id=? AND input_hash=?',(str(intent.target_id),intent.base_hash)).fetchone():raise GateError('Exakter Analysevorschlag fehlt')
            if not intent.person.strip() or not intent.decision.strip():raise GateError('Analysebestätigung benötigt Person und konkrete Entscheidung')
        if intent.action == 'freeze' and any(a.synthetic_fixture for a in intent.approvals):
            raise GateError('Reale Oberfläche erfasst keine synthetischen Freigabefixtures als menschliche Zustimmung')
        if intent.action == 'free':
            validate_free(register, intent)
        if intent.action == 'central':
            validate_central(register, intent)
        if intent.action == 'new_phase':
            phase=register.get(intent.target_id,StudyPhase)
            if phase.purpose!='main' or not register.connection.execute('SELECT 1 FROM freeze_binding WHERE phase_id=?',(str(phase.id),)).fetchone():
                raise GateError('Neue Erhebungsphase ausschließlich aus eingefrorener Mainphase; keine Umwidmung freier Tests, Demos oder Piloten')
        if intent.action in ('pause', 'abort', 'resume'):
            register.get(intent.target_id, Run)
            row = register.connection.execute('SELECT status FROM pipeline_binding WHERE run_id=?', (str(intent.target_id),)).fetchone()
            unbound_abort=not row and intent.action=='abort' and register.state(intent.target_id).execution!='terminal' and not register.connection.execute('SELECT 1 FROM call_binding WHERE run_id=?',(str(intent.target_id),)).fetchone()
            if not unbound_abort and (not row or row[0]=='completed'):
                raise GateError('Kein steuerbarer Pipelineauftrag')
            if intent.action == 'resume' and row[0] not in ('paused', 'recovery_required'):
                raise GateError('Nur pausierter oder nach Neustart ungeklärter Auftrag kann bewusst fortgesetzt werden')
            if not intent.decision.strip():
                raise GateError('Entscheidung benötigt einen Grund')
        command_id = str(uuid4())
        register.connection.execute('INSERT INTO ui_command VALUES (?,?,?,?,?,?,?,?,?)',
            (command_id, key, intent.action, payload, digest(intent), 'queued', datetime.now(timezone.utc).isoformat(), None, 'Bewusster Bedienauftrag; Worker ausstehend'))
        return command_id


def validate_central(register, intent):
    phase = register.get(intent.target_id, StudyPhase)
    if intent.settings is None or versions_hash(register, phase.id) != intent.base_hash:
        raise GateError('Entwurfsstand verändert; aktuellen Versionsdiff erneut prüfen')
    if register.connection.execute('SELECT 1 FROM freeze_binding WHERE phase_id=?', (str(phase.id),)).fetchone():
        raise GateError('Freeze ist unveränderlich. Neue Erhebungsphase mit neuen Nachweisen erforderlich.')
    register._validate_settings(intent.settings, complete=False)
    if phase.purpose == 'free_test':
        validate_free_settings(register, intent.settings)


def validate_free_settings(register, settings):
    if settings.holdout_suite_id or settings.reference_id:
        raise GateError('study_holdout und geschützte Referenzen sind im freien Bereich nicht verfügbar')
    for _, ref in register._references(settings):
        value = register.get(ref)
        if isinstance(value, AssetVersion) and (value.suite_kind == 'study_holdout' or value.access_scope == 'trusted_evaluator'):
            raise GateError('Geschützte Assets sind im freien Bereich nicht auswählbar')
    register._validate_settings(settings, complete=False)
    if not settings.development_suite_id:
        raise GateError('Öffentliche development-Suite erforderlich')


def validate_free(register, intent):
    if intent.settings is None or intent.cell is None:
        raise GateError('Wirksame freie Einstellungen/Modulfaktoren fehlen')
    validate_free_settings(register, intent.settings)


def boot_commands(register):
    with register.transaction():
        register.connection.execute("UPDATE ui_command SET status='recovery_required',reason='Workerneustart während Vorbereitung: ausdrücklich denselben Auftrag fortsetzen; keine automatische Wiederholung' WHERE status='processing'")


def recover_command(register, command_id, reason):
    if not reason.strip():
        raise GateError('Konkrete Wiederaufnahmeentscheidung erforderlich')
    with register.transaction():
        row = register.connection.execute('SELECT * FROM ui_command WHERE id=?', (str(command_id),)).fetchone()
        if not row or row['status'] not in ('failed', 'recovery_required') or row['kind'] not in ('main', 'demo'):
            raise GateError('Nur derselbe Startauftrag kann sicher erneut vorbereitet werden')
        intent = Intent.model_validate_json(row['payload'])
        # Repeat only preparation of the existing idempotent Run; never replay calls.
        if not intent.target_id:
            raise GateError('Abgebrochene Entwurfsanlage wird nicht automatisch wiederholt')
        register.connection.execute('INSERT INTO ui_command_recovery VALUES (?,?,?,?)',
            (str(uuid4()),row['id'],reason,datetime.now(timezone.utc).isoformat()))
        register.connection.execute("UPDATE ui_command SET status='queued',reason='Ausdrückliche Fortsetzung derselben Vorbereitung; Journal wird geprüft' WHERE id=?", (row['id'],))


def imported_catalog(register, root=ROOT):
    from .template import available_assets
    from .context_assets import contract_binding
    from .pipeline import role_assets
    store = ArtifactStore(register.settings, register)
    assets = available_assets(register, root)
    locked = json.loads((Path(__file__).parent / 'pipeline_assets.lock.json').read_text())
    existing = {a.code: a for a in register.all(AssetVersion)}
    for key, path, kind in [('contract-current', 'docs/vertraege/m2-v0.1-csrf1/manifest.json', 'contract'),
                           *[(f'current-{m}-K{k}', f'assets/context/m2-v0.1-csrf1/{m}/K{k}/manifest.json', 'source_context') for m in ('BF', 'SQL', 'UP') for k in (0, 1)]]:
        expected = contract_binding(root)['manifest_sha256'] if kind == 'contract' else locked[path]
        if hashlib.sha256((root / path).read_bytes()).hexdigest() != expected:
            raise IntegrityError('Gebundenes öffentliches Asset verändert: ' + path)
        code = 'M8-' + key
        assets[key] = existing.get(code) or register.add(AssetVersion(code=code, asset_type=kind, manifest_hash=expected,
            origin='Versionierte öffentliche M2-v0.1-CSRF1-Datei: ' + path, access_scope='role_package'))
    from .role_formats import PROMPTS_HASH, CONTRACT_HASH
    roles = {a.asset_type: a for a in existing.values() if a.code.startswith('PIPE-') and
             a.manifest_hash == {'prompt': PROMPTS_HASH, 'handoff': CONTRACT_HASH}.get(a.asset_type)}
    if not {'prompt', 'handoff'} <= set(roles):
        roles = {a.asset_type: a for a in role_assets(store).values()}
    assets.update(prompts=roles['prompt'], handoff=roles['handoff'])
    # Bind the actual installed instrument, including runtime and source bytes.
    # This is version metadata, not a measurement or technical acceptance.
    from .evaluation import instrument_manifest
    from .sandbox_runtime import RuntimeImages
    instrument = instrument_manifest(RuntimeImages(**json.loads(Path(__file__).with_name('pipeline_runtime.lock.json').read_text())))
    instrument_hash = digest(instrument)
    code = 'M8-EVALUATOR-' + instrument_hash
    tool = existing.get(code)
    if tool is None:
        proof = store.json(instrument, artifact_type='evaluation_instrument_metadata')
        tool = register.add(AssetVersion(code=code, asset_type='tool', manifest_hash=instrument_hash,
            artifact_ids=(proof.id,), origin='Tatsächlich installierte M6-Instrumentversion; keine Messung oder Fachabnahme',
            access_scope='trusted_register'))
    assets['tool'] = tool
    models = [m for m in register.all(ModelPackage) if m.code == 'M8-OFFLINE-v1']
    if not models:
        proof = store.json({'schema':'m8-offline-v1','synthetic':True,'provider':'mock://local', 'claim':'Deterministische Technikdemo; keine Modellfähigkeit, Preise oder Fachfreigabe'}, artifact_type='m8_offline_metadata')
        unknown = Observation(status='unresolved', unit='tokens', reason='Synthetischer Adapter; keine reale Kapazität')
        models = [register.add(ModelPackage(code='M8-OFFLINE-v1', exact_model_id='TECHNICAL-FIXTURE:no-network', endpoint='mock://local',
            upstream='mock', routing={}, fallback={}, supported_parameters=('seed',), effective_parameters={'seed':1},
            context_limit=unknown, output_limit=unknown, price=Observation(status='not_applicable', unit='USD', reason='Kostenfreie synthetische Demo'),
            currency='USD', price_as_of=datetime.now(timezone.utc), metadata_evidence_ids=(proof.id,), version_uncertainty='Keine reale Modellversion/Studie'))]
    return assets, models[0]


def put_configuration(register, phase_id, cell_key, cell, settings):
    configuration=register._put(Configuration(code=f'CONF-{uuid4()}',phase_id=phase_id))
    return register._put(ConfigurationVersion(code=f'CV-{uuid4()}',configuration_id=configuration.id,
        parent_id=None,parent_hash=None,cell_key=cell_key,cell=cell,settings=settings,
        content_hash=digest({'cell':cell.model_dump(mode='json'),'settings':settings.model_dump(mode='json')}),
        change_diff={'initial':settings.model_dump(mode='json')}))


def own_draft(register, title, *, demo=False, settings=None):
    from .domain import MAIN_CELLS
    assets, model = imported_catalog(register)
    settings = settings or EffectiveSettings(contract_id=assets['contract-current'].id,rubric_id=assets['rubric'].id,
        scaffold_id=assets['scaffold'].id,development_suite_id=assets['development'].id,
        holdout_suite_id=assets['holdout_catalog'].id,reference_id=assets['references'].id,
        context_ids={f'{m}-K{k}':assets[f'current-{m}-K{k}'].id for m in ('BF','SQL','UP') for k in (0,1)},
        prompt_ids=(assets['prompts'].id,),handoff_id=assets['handoff'].id,
        tool_ids=(assets['tool'].id,),
        dvwa_commit='b496a5d3de6b967410155e1b7d3e51e9d035eb22')
    provenance='M8-v1 persönliche kostenfreie synthetische Demo; keine Fachabnahme' if demo else 'M8-v1 eigene Studienvorlage; sämtliche menschlichen Entscheidungen offen'
    # One SQL aggregate: a crash cannot expose a half-created study/phase.
    with register.transaction():
        study=register._put(Study(code=f'STUDY-{uuid4()}',title=title,design_version='M8-draft-v1',
            data_origin='synthetic' if demo else 'empirical',provenance=provenance))
        phase=register._put(StudyPhase(code=f'PHASE-{uuid4()}',study_id=study.id,purpose='demo' if demo else 'main',provenance=provenance))
        if not demo:
            for key,cell in MAIN_CELLS.items():put_configuration(register,phase.id,key,cell,settings)
            return {'url':'/studies/'+str(study.id),'study_id':str(study.id)}
        settings=settings.model_copy(update={'model_a':model.id,'model_b':model.id,'holdout_suite_id':None,
            'reference_id':None,'role_parameters':{'all':{'seed':1}},'retry_interval_seconds':Decimal('1'),'software_commit':software_identity()})
        version=put_configuration(register,phase.id,'DEMO',Cell(module='SQL',context='K0',producer='A',verifier='A',planner=False,review=False),settings)
        return {'url':'/configurations/'+str(version.id),'study_id':str(study.id),'phase_id':str(phase.id),'version_id':str(version.id)}


def software_identity():
    files = sorted(p for p in Path(__file__).parent.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc' and p.name!='.DS_Store')
    return 'source-tree-sha256:' + digest({str(p.relative_to(Path(__file__).parent)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files})


def software_compatible(expected, *, actual=None):
    current = software_identity()
    actual = actual or current
    if expected == actual: return True
    if actual != current: return False
    path = Path(__file__).with_name('execution_compatibility.json')
    releases = json.loads(path.read_text())['releases'] if path.exists() else {}
    files = releases.get(expected)
    return bool(files) and all((path.parent / name).is_file() and hashlib.sha256((path.parent / name).read_bytes()).hexdigest() == sha for name, sha in files.items())


def installed_dependencies(store):
    """Read only the pinned prepared study image; no build/download/custom mount."""
    from .snapshots import Snapshots
    images = json.loads(Path(__file__).with_name('pipeline_runtime.lock.json').read_text())
    old = [a for a in store.register.all(Artifact) if a.artifact_type == 'dependency_manifest' and a.run_id is None]
    snapshots = Snapshots(store)
    for item in reversed(old):
        body = snapshots._dependency(item.id)
        if body['source'] == {'image':images['php'],'path':'/opt/study/vendor'}:
            return item
    import docker
    client = docker.from_env(timeout=None)
    container = None
    temporary = store.settings.staging / ('dependencies-' + str(uuid4()))
    temporary.mkdir()
    try:
        client.images.get(images['php'])
        container = client.containers.create(images['php'], command=['true'], network_disabled=True,
            labels={'org.bachelorarbeit.component':'m8-dependency-read'}, read_only=True)
        stream, _ = container.get_archive('/opt/study/vendor')
        with tarfile.open(fileobj=io.BytesIO(b''.join(stream))) as archive:
            for entry in archive.getmembers():
                p = Path(entry.name)
                if p.is_absolute() or '..' in p.parts or not p.parts or p.parts[0] != 'vendor' or not (entry.isdir() or entry.isfile()):
                    raise IntegrityError('Unsicheres Dependencyarchiv aus gebundenem Image')
            archive.extractall(temporary, filter='data')
        return snapshots.dependencies(temporary / 'vendor', source={'image':images['php'],'path':'/opt/study/vendor'}, runtime=images)
    finally:
        if container is not None:
            container.remove()
        client.close()
        import shutil
        shutil.rmtree(temporary)


def prepare_start(register, intent, key):
    """Idempotent Run creation and safe preparation; never repeats a dispatched call."""
    from .adapter import CallJournal
    from .pipeline import Pipeline, provision, roles
    from .pipeline_mock import PipelineMockAdapter
    from .pipeline_runner import InternalRunner
    from .providers import OpenRouterAdapter
    from .sandbox_runtime import RuntimeImages, Sandbox
    from .context_assets import package_path
    import docker
    import os
    existing = register.connection.execute('SELECT job_id FROM job_state WHERE idempotency_key=?', (key,)).fetchone()
    store = ArtifactStore(register.settings, register)
    proof = None
    if not existing:
        conf = check_start(register, intent)
        from .application_settings import model_key
        if not register._is_mock(conf) and not model_key(register.settings):
            raise GateError('OpenRouter-Schlüssel fehlt. Bitte zuerst in den Einstellungen speichern.')
        proof = store.json({'schema':'m8-start-technical-v1','software_identity':software_identity(),
            'config_hash':conf.content_hash, 'note':'Technische Eingangsbindung; keine erfundene menschliche Zustimmung'}, artifact_type='m8_start_technical')
    journal = CallJournal(register, software_commit=software_identity(), platform_description=platform.platform())
    if existing:
        from .domain import Job
        job = register.get(existing[0], Job)
        run = register.get(job.run_id, Run)
        conf = register.get(run.configuration_version_id, ConfigurationVersion)
        if register.state(run.id).execution=='terminal' and not register.connection.execute('SELECT 1 FROM pipeline_binding WHERE run_id=?',(str(run.id),)).fetchone():
            raise GateError('Manuell beendete Vorbereitung darf keine neue Generation auslösen')
        if not software_compatible(conf.settings.software_commit):
            raise GateError('Vorhandener Startauftrag gehört zu anderem Softwarestand; neue Version/Phase statt stiller Recovery')
        if register.connection.execute('SELECT 1 FROM pipeline_binding WHERE run_id=?',(str(run.id),)).fetchone():
            return {'url':'/runs/'+str(run.id), 'run_id':str(run.id)}
        if register.connection.execute('SELECT 1 FROM call_binding WHERE run_id=?',(str(run.id),)).fetchone():
            raise GateError('Calljournal ohne eindeutige Pipelinebindung; keine Neugeneration erlaubt')
    else:
        # Register repeats all phase/configuration/backup gates atomically.
        if intent.action == 'main':
            freeze=register.get(intent.target_id,Freeze)
            run, job=register.start_main(freeze.id,intent.planned_id,expected_freeze_hash=freeze.freeze_hash,
                decision=intent.decision,idempotency_key=key,technical_evidence_ids=(proof.id,))
        else:
            phase=register.get(intent.target_id,StudyPhase)
            if phase.purpose=='free_test' and intent.paid_consent and not register._is_mock(conf):
                from .domain import EvidenceProof
                scope=register.configuration_scope(phase.id,conf.id)
                consent=store.json({'person':intent.person,'decision':intent.decision,'paid_calls_consent':True,
                    'configuration_id':str(conf.id),'scope_hash':scope,'command_key':key},artifact_type='free_start_cost_consent')
                asset=register.add(AssetVersion(code='COST-CONSENT-'+str(uuid4()),asset_type='tool',manifest_hash=consent.sha256,
                    artifact_ids=(consent.id,),origin='Ausdrückliche lokale Kostenentscheidung für diesen freien Start',access_scope='trusted_register'))
                register.add(Approval(code='FREE-COST-'+str(uuid4()),phase_id=phase.id,kind='cost',scope_hash=scope,
                    person=intent.person,reason=intent.decision,paid_calls_consent=True,
                    evidence=(EvidenceProof(key='paid_start',asset_id=asset.id,asset_hash=asset.manifest_hash,scope_hash=scope),)))
            run, job=register.start_other(intent.target_id,intent.version_id,decision=intent.decision,
                idempotency_key=key,technical_evidence_ids=(proof.id,))
    from .application_settings import model_key
    adapter = PipelineMockAdapter() if register._is_mock(conf) else OpenRouterAdapter(api_key=model_key(register.settings))
    client=docker.from_env(timeout=None)
    runtime=json.loads(Path(__file__).with_name('pipeline_runtime.lock.json').read_text())
    runner=InternalRunner(Sandbox(store,client,images=RuntimeImages(**runtime),assets=ROOT))
    runner.software_identity=software_identity()
    pipeline=Pipeline(register,journal,adapter,runner)
    try:
        dependency=installed_dependencies(store)
        if register.state(run.id).execution=='terminal':raise GateError('Vorbereitung bewusst beendet; kein Start/Versand zulässig')
        preview=journal.preview(adapter,run.id,{'uncertainty':'Manuelle Kostenprüfung; Schätzung ist kein Limit/Garantie', 'categories':{},'pilot_consumption':{'status':'not_collected'}})
        order=journal.record_start_order(run.id,preview.id,person=intent.person,decision=intent.decision,roles=roles(conf.cell),paid_consent=intent.paid_consent,synthetic_fixture=False)
        manifest=provision(store,run.id,package=(ROOT/package_path(register,conf,strict=True)).read_bytes(),
            scaffold=ROOT/'assets/study/m2-v0.1/scaffold',dependency_id=dependency.id,proof_ids=run.technical_evidence_ids,
            versions={'software_commit':conf.settings.software_commit,'runtime_images':runtime,'platform':platform.platform(),
                'isolation':'M4 fixed runtime/mount contracts; normal preflight repeated',
                'runtime_software_identity':software_identity()},synthetic=False)
        if register.state(run.id).execution=='terminal':raise GateError('Vorbereitung bewusst beendet; kein Start/Versand zulässig')
        pipeline.install(job.id,manifest.id,order)
        if intent.series_id:
            with register.transaction():
                register.connection.execute("UPDATE pipeline_series SET status='waiting',reason=? WHERE id=?",(intent.decision,str(intent.series_id)))
        return {'url':'/runs/'+str(run.id),'run_id':str(run.id)}
    finally:
        pipeline.close(); adapter.close(); runner.close()


def control(register, intent):
    """Persisted control only. Safe during a blocked worker node, no graph replay."""
    from .adapter import CallJournal
    run=register.get(intent.target_id,Run)
    journal=CallJournal.cost_view(register)
    journal._secret=None
    diagnostics=diagnostic(register,run.id)
    evidence=()
    if intent.action == 'abort':
        diagnostics['decision']={'immediate':intent.immediate,'reason':intent.decision}
        if intent.immediate:
            diagnostics['missing'].append('Sofortstopp: aktive Endgrenze/Liveinfrastrukturdiagnose möglicherweise unvollständig; Remoteinferenz kann weiterlaufen')
            for row in register.connection.execute('SELECT call_id FROM call_binding WHERE run_id=?',(str(run.id),)).fetchall():
                journal.stop(row[0],reason=intent.decision)
        proof=journal.store.json(diagnostics,run_id=run.id,artifact_type='pipeline_stop_diagnosis')
        evidence=(proof.id,)
    process=register.add(ProcessInstance(code=f'UI-CONTROL-{uuid4()}',worker='persistent-control-observer',software_commit=software_identity(),
        platform=platform.platform(),started_at=datetime.now(timezone.utc),clock_description='No duration inference; persisted control only'))
    binding=register.connection.execute('SELECT status FROM pipeline_binding WHERE run_id=?',(str(run.id),)).fetchone()
    if not binding:
        with register.transaction():
            binding=register.connection.execute('SELECT status FROM pipeline_binding WHERE run_id=?',(str(run.id),)).fetchone()
            if not binding:
                if intent.action!='abort' or register.connection.execute('SELECT 1 FROM call_binding WHERE run_id=?',(str(run.id),)).fetchone():
                    raise GateError('Vorbereitung ohne eindeutigen Journalbefund nicht steuerbar')
                from .domain import RunState
                register.set_state_in_transaction(run.id,RunState(execution='terminal',terminal_cause='interrupted',seal='no_candidate',ended_at=datetime.now(timezone.utc)),reason='Bewusste Beendigung unvollständiger Vorbereitung ohne versandten Call: '+intent.decision)
                register.connection.execute("UPDATE job_state SET status='stopped' WHERE job_id IN (SELECT id FROM register_record WHERE kind='Job' AND json_extract(payload,'$.run_id')=?)",(str(run.id),))
                sequence=1+max((e.sequence for e in register.all(Event) if e.run_id==run.id),default=0)
                register._put(Event(code=f'UI-EVENT-{uuid4()}',run_id=run.id,sequence=sequence,event_type='preparation_aborted',process_id=process.id,
                    interval_id=None,happened_at=datetime.now(timezone.utc),evidence_ids=evidence,details={'reason':intent.decision,'no_call_journal':True}))
                return {'url':'/runs/'+str(run.id),'run_id':str(run.id)}
    status={'pause':'pause_requested','abort':'abort_requested','resume':'resume_requested'}[intent.action]
    with register.transaction():
        row=register.connection.execute('SELECT status,process_id FROM pipeline_binding WHERE run_id=?',(str(run.id),)).fetchone()
        if not row or row[0]=='completed':raise GateError('Terminaler Auftrag nicht steuerbar')
        if intent.action=='resume' and row[0] not in ('paused','recovery_required'):raise GateError('Bewusste Fortsetzung nur bei Pause/Recovery')
        sequence=1+max((e.sequence for e in register.all(Event) if e.run_id==run.id),default=0)
        register.connection.execute('UPDATE pipeline_binding SET status=?,reason=?,continuation=? WHERE run_id=?',
            (status,intent.decision,intent.decision if intent.action=='resume' else None,str(run.id)))
        register._put(Event(code=f'UI-EVENT-{uuid4()}',run_id=run.id,sequence=sequence,event_type=status,process_id=process.id,
            interval_id=None,happened_at=datetime.now(timezone.utc),evidence_ids=evidence,details={'reason':intent.decision}))
        if intent.action=='abort':
            register._put(Event(code=f'UI-EVENT-{uuid4()}',run_id=run.id,sequence=sequence+1,event_type='abort_decision',process_id=process.id,
                interval_id=None,happened_at=datetime.now(timezone.utc),evidence_ids=evidence,details={'reason':intent.decision,'immediate':intent.immediate,'target_process_id':row['process_id']}))
    if intent.action=='abort' and intent.immediate:
        from .sandbox_runtime import Sandbox, RuntimeImages
        import docker
        client=docker.from_env(timeout=None)
        try:
            sandbox=Sandbox(journal.store,client,images=RuntimeImages(**json.loads(Path(__file__).with_name('pipeline_runtime.lock.json').read_text())),assets=ROOT)
            sandbox.stop_recorded(run.id,reason=intent.decision)
        finally:client.close()
    return {'url':'/runs/'+str(run.id),'run_id':str(run.id)}


def run_records(register, cls, run_id):
    """Select only this run, retaining immutable hash/type/owner validation."""
    run_id=UUID(str(run_id))
    rows=register.connection.execute("SELECT id FROM register_record WHERE kind=? AND json_extract(payload,'$.run_id')=? ORDER BY rowid",(cls.__name__,str(run_id))).fetchall()
    values=[register.get(row['id'],cls) for row in rows]
    if any(value.run_id!=run_id for value in values):
        raise IntegrityError('Laufbezug der ausgewählten Nachweise passt nicht')
    return values


def redact_credentials(value):
    """Public derived values; the original journal/CAS bytes remain immutable."""
    if isinstance(value,dict):
        return {key:'<REDACTED>' if key.lower() in {'access_token','password','authorization','api_key','secret','app_key'} or key.lower().endswith(('_password','_api_key','_access_token')) else redact_credentials(item) for key,item in value.items()}
    if isinstance(value,list):return [redact_credentials(item) for item in value]
    return value


def public_sandbox(row):
    """Readable persisted job diagnosis; runtime credentials stay in the journal."""
    result=dict(row)
    try:result['body']=redact_credentials(json.loads(result['body']))
    except (ValueError,TypeError):result['body']={'missing':'Gespeicherte Sandboxdiagnose nicht lesbar'}
    return result


def diagnostic(register, run_id):
    from .adapter import CallJournal
    from .domain import CandidateSnapshot, ModelCall
    run=register.get(run_id,Run)
    events=[e.model_dump(mode='json') for e in register.all(Event) if e.run_id==run.id]
    calls=[c for c in register.all(ModelCall) if c.run_id==run.id]
    states=[s.model_dump(mode='json') for s in register.all(CandidateSnapshot) if s.run_id==run.id]
    from .domain import protected_evaluation
    artifacts=[a.model_dump(mode='json') for a in run_records(register,Artifact,run.id) if not protected_evaluation(a) and ('log' in a.artifact_type or 'diagnos' in a.artifact_type)]
    binding=register.connection.execute('SELECT * FROM pipeline_binding WHERE run_id=?',(str(run.id),)).fetchone()
    node=next((e['details']['node'] for e in reversed(events) if 'node' in e['details']),calls[-1].node if calls else None)
    return {'run_id':str(run.id),'node':node,'last_activity':events[-1] if events else None,
        'binding':dict(binding) if binding else None, 'call_journals':[CallJournal.cost_view(register).diagnostic(c.id) for c in calls],
        'jobs':[dict(j) for j in register.connection.execute('SELECT j.* FROM job_state j JOIN register_record r ON r.id=j.job_id WHERE json_extract(r.payload,\'$.run_id\')=?',(str(run.id),))],
        'sandbox':[public_sandbox(j) for j in register.connection.execute('SELECT * FROM sandbox_execution WHERE run_id=?',(str(run.id),))],
        'last_saved_candidate':states[-1] if states else None,'logs':artifacts,
        'missing':['Nur persistierte Beobachtung; kein Beweis über laufende Remoteinferenz','Live-Dockerdiagnose erst im kontrollierten Worker-Stopp']}


def process_command(register, *, controls=False):
    kinds="kind IN ('pause','abort','resume')" if controls else "kind NOT IN ('pause','abort','resume')"
    with register.transaction():
        row=register.connection.execute("SELECT * FROM ui_command WHERE status='queued' AND "+kinds+' ORDER BY rowid LIMIT 1').fetchone()
        if not row:return None
        register.connection.execute("UPDATE ui_command SET status='processing',reason='Persistenter Worker bearbeitet Auftrag' WHERE id=?",(row['id'],))
    try:
        intent=Intent.model_validate_json(row['payload'])
        if digest(intent)!=row['payload_hash']:raise IntegrityError('Bedienauftrag beschädigt')
        if intent.action in ('initialize','model_lookup','model_import'):
            from .application_settings import initialize, lookup_model, import_model
            if intent.action=='initialize':result=initialize(register)
            elif intent.action=='model_lookup':result=lookup_model(register,intent.model_id)
            else:result=import_model(register,intent.model_id,intent.endpoint,intent.model_parameters)
        elif intent.action=='inspection_prepare':
            from .inspection import prepare
            result=prepare(register,intent.target_id)
        elif intent.action in ('pause','abort','resume'):result=control(register,intent)
        elif intent.action=='draft' or intent.action=='demo' and not intent.target_id:result=own_draft(register,intent.title,demo=intent.action=='demo',settings=intent.settings)
        elif intent.action in ('main','demo'):result=prepare_start(register,intent,row['idempotency_key'])
        elif intent.action=='freeze':
            if not intent.freeze or intent.freeze.phase_id!=intent.target_id or intent.base_hash!=versions_hash(register,intent.target_id):
                raise GateError('Freezevorschau verändert/veraltet; erneut prüfen')
            scope=register.connection.execute('SELECT r_c,r_e,seed FROM matrix_draft_preferences WHERE phase_id=?',(str(intent.target_id),)).fetchone()
            if scope and (scope['r_c'],scope['r_e'],scope['seed'])!=(intent.freeze.r_c,intent.freeze.r_e,intent.freeze.seed):
                raise GateError('Umfang oder Seed nach Vorschau geändert; vollständige Matrix erneut prüfen')
            register.freeze(intent.freeze,approvals=intent.approvals)
            phase=register.get(intent.target_id,StudyPhase)
            result={'url':'/studies/'+str(phase.study_id),'freeze_id':str(intent.freeze.id)}
        elif intent.action=='central':
            validate_central(register,intent)
            versions=register.central_update(intent.target_id,intent.settings)
            phase=register.get(intent.target_id,StudyPhase)
            result={'url':'/studies/'+str(phase.study_id),'versions':[str(v.id) for v in versions.values()]}
        elif intent.action=='new_phase':
            old=register.get(intent.target_id,StudyPhase)
            if old.purpose!='main' or not register.connection.execute('SELECT 1 FROM freeze_binding WHERE phase_id=?',(str(old.id),)).fetchone():
                raise GateError('Keine Umwidmung; neue Erhebungsphase erfordert eingefrorene Mainphase')
            with register.transaction():
                phase=register._put(StudyPhase(code=f'PHASE-{uuid4()}',study_id=old.study_id,purpose='main',predecessor_id=old.id,
                    provenance='Bewusst neue Entwurfsphase; keine übernommenen Freigaben'))
                for v in latest_versions(register,old.id).values():
                    put_configuration(register,phase.id,v.cell_key,v.cell,v.settings)
            result={'url':'/studies/'+str(old.study_id),'phase_id':str(phase.id)}
        elif intent.action=='free':
            validate_free(register,intent)
            with register.transaction():
                study=register._put(Study(code=f'FREE-{uuid4()}',title=intent.title,design_version='M8-free-v1',data_origin='synthetic' if all(register.get(mid,ModelPackage).endpoint.startswith('mock://') for mid in (intent.settings.model_a,intent.settings.model_b) if mid) else 'empirical',provenance='F01 freier Entwicklungstest; niemals FQ/Studienexport'))
                phase=register._put(StudyPhase(code=f'FREE-PHASE-{uuid4()}',study_id=study.id,purpose='free_test',provenance=study.provenance))
                v=put_configuration(register,phase.id,'FREE',intent.cell,intent.settings)
            result={'url':'/configurations/'+str(v.id),'version_id':str(v.id)}
        elif intent.action in ('analysis_propose','analysis_confirm'):
            from .review_ui import process_analysis
            result=process_analysis(register,row['idempotency_key'],intent)
        elif intent.action=='series':
            freeze=register.get(intent.target_id,Freeze)
            if not intent.decision.strip():raise GateError('Gesonderte bewusste Serienentscheidung erforderlich')
            series=register.add(Series(code=f'SERIES-{uuid4()}',freeze_id=freeze.id,mode='series',order=tuple(register.fq_ids(freeze.id))))
            with register.transaction():register.connection.execute('INSERT INTO pipeline_series VALUES (?,?,?)',(str(series.id),'waiting',intent.decision))
            result={'url':'/series/'+str(series.id),'series_id':str(series.id)}
        else:raise GateError('Unbekannter Auftrag')
        with register.transaction():register.connection.execute("UPDATE ui_command SET status='completed',result=?,reason='Auftrag dauerhaft gespeichert' WHERE id=?",(canonical(result),row['id']))
    except Exception as exc:
        with register.transaction():register.connection.execute("UPDATE ui_command SET status='failed',reason=? WHERE id=?",(str(exc),row['id']))
    return row['id']

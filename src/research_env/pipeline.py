"""Bounded persistent graph. Checkpoints are progress, CAS/journals are evidence.

One scheduler tick executes one explicit graph node. Reentry first consults
durable effects and the adapter journal; imported analysis never calls this API.
No state/store, files, prompts or tool memory is shared between run threads.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import TypedDict
from uuid import UUID, uuid4

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver

from .artifacts import ArtifactStore, IntegrityError, atomic_file, read_regular
from .domain import (protected_evaluation, Artifact, AssetVersion, CandidateSnapshot, ConfigurationVersion,
                     Event, Job, ModelCall, Run, StudyPhase, canonical, digest)
from .context_assets import package_path as context_package_path
from .locks import write_barrier
from .register import GateError
from .role_formats import (CONTRACT, CONTRACT_HASH, MODULE_PATHS, TEST_PATHS,
                           PROMPTS, PROMPTS_HASH, validate, test_bundle)
from .snapshots import Snapshots, ProcessWriters, inventory
from .domain import TimeInterval


class PhaseIdentityDrift(IntegrityError):
    """A changed frozen identity cannot resume in the old phase."""


class GraphState(TypedDict):
    run_id: str
    config_hash: str
    statuses: dict[str, str]
    refs: dict[str, str]


class BarrierSaver(SqliteSaver):
    def __init__(self, connection, settings):
        self.settings = settings
        super().__init__(connection)

    def setup(self):
        with write_barrier(self.settings):
            super().setup()

    def put(self, *args, **kwargs):
        with write_barrier(self.settings):
            return super().put(*args, **kwargs)

    def put_writes(self, *args, **kwargs):
        with write_barrier(self.settings):
            return super().put_writes(*args, **kwargs)


def roles(cell):
    return ('analyzer',) + (('planner',) if cell.planner else ()) + ('migrate', 'test') + (('review',) if cell.review else ()) + ('repair',)


def role_assets(store):
    """Versioned editable configuration references, not a subject approval."""
    result = {}
    for name, kind, value in (('handoff', 'handoff', CONTRACT), ('prompts', 'prompt', PROMPTS)):
        artifact = store.json(value, artifact_type='pipeline_' + name)
        result[name] = store.register.add(AssetVersion(code=f'PIPE-{name}-{uuid4()}', asset_type=kind,
            manifest_hash=artifact.sha256, artifact_ids=(artifact.id,), origin='Pipeline technical format v1; human subject review open', access_scope='trusted_register'))
    return result


def provision(store, run_id, *, package, scaffold, dependency_id, proof_ids,
              versions, synthetic=False):
    """Trusted local preparation; never accept paths/contents from model/UI.

Archive a complete base package and the actual shared scaffold before any call.
Synthetic callers must explicitly label fixtures; production binds all input
asset manifests and preflight proofs through the configuration below.
"""
    r = store.register
    run = r.get(run_id, Run)
    conf = r.get(run.configuration_version_id, ConfigurationVersion)
    if not conf.settings.scaffold_id:
        raise GateError('Versionierter Gerüstbezug fehlt')
    files = inventory(scaffold, project=True)
    if not files or not package or not proof_ids or not versions:
        raise GateError('Vollständiges Paket/Gerüst/Versions-/Isolationsbelege fehlen')
    deps = Snapshots(store)._dependency(dependency_id)
    source_binding = None
    if not synthetic:
        catalog = Path(__file__).with_name('sandbox_assets.lock.json')
        locked = json.loads(catalog.read_text())
        package_path = context_package_path(r, conf)
        expected_scaffold = locked['assets/study/m2-v0.1/scaffold']
        if hashlib.sha256(package).hexdigest() != locked[package_path]['sha256']:
            raise IntegrityError('Paket vertauscht/verkürzt: vollständiges zugewiesenes K0/K1 erforderlich')
        if {f['path']: f['sha256'] for f in files} != expected_scaffold:
            raise IntegrityError('Gemeinsames Gerüst nicht bytegleich mit gebundenem Katalog')
        asset_lock = json.loads(Path(__file__).with_name('pipeline_assets.lock.json').read_text())
        context_package_path(r, conf, strict=True)
        context_id = conf.settings.context_ids.get(f'{conf.cell.module}-{conf.cell.context}')
        if not context_id or r.get(context_id, AssetVersion).manifest_hash != asset_lock[package_path.replace('package.txt', 'manifest.json')]:
            raise IntegrityError('Zugewiesenes Kontextasset ist nicht das vollständige Modul-/K0-/K1-M2-Manifest')
        if r.get(conf.settings.scaffold_id, AssetVersion).manifest_hash != asset_lock['assets/study/m2-v0.1/manifest.json']:
            raise IntegrityError('Zugewiesenes Gerüstasset ist nicht das gemeinsame M2-Manifest')
        source_binding = {'catalog_sha256': hashlib.sha256(catalog.read_bytes()).hexdigest(),
                          'package_path': package_path, 'package_sha256': hashlib.sha256(package).hexdigest(),
                          'scaffold_files': expected_scaffold}
        if not conf.settings.handoff_id or len(conf.settings.prompt_ids) != 1:
            raise GateError('Versionierte Rollen-/Promptassets fehlen')
        for asset_id, expected in ((conf.settings.handoff_id, CONTRACT_HASH), (conf.settings.prompt_ids[0], PROMPTS_HASH)):
            asset = r.get(asset_id, AssetVersion)
            if asset.manifest_hash != expected or len(asset.artifact_ids) != 1 or hashlib.sha256(store.read(asset.artifact_ids[0])).hexdigest() != expected:
                raise IntegrityError('Rollen-/Promptversion nicht bytegleich mit implementiertem Vertrag')
    package_artifact = store.store(package, run_id=run.id, artifact_type='pipeline_package', access_scope='role')
    entries = []
    for f in files:
        data, _ = read_regular(scaffold, f['path'])
        a = store.store(data, run_id=run.id, artifact_type='pipeline_scaffold_file', access_scope='role', original_name=f['path'])
        entries.append({**f, 'artifact_id': str(a.id)})
    scaffold_artifact = store.json({'schema': 'pipeline-scaffold-v1', 'files': entries}, run_id=run.id,
                                 artifact_type='pipeline_scaffold', access_scope='role')
    format_artifact = store.json(CONTRACT, run_id=run.id, artifact_type='pipeline_role_contract', access_scope='role')
    for aid in proof_ids:
        a = r.get(aid, Artifact)
        if protected_evaluation(a) or a.run_id not in (None, run.id):
            raise GateError('Vorflightbeleg geschützt/fremd')
        store.read(aid)
    bindings = {key: str(value) for key, value in conf.settings.context_ids.items() if key == f'{conf.cell.module}-{conf.cell.context}'}
    manifest = {'schema': 'pipeline-v1', 'run_id': str(run.id), 'config_hash': run.effective_hash,
                'package_id': str(package_artifact.id), 'scaffold_id': str(scaffold_artifact.id),
                'role_contract_id': str(format_artifact.id), 'role_contract_hash': CONTRACT_HASH, 'prompts_hash': PROMPTS_HASH,
                'dependency_id': str(dependency_id), 'dependency_tree_hash': deps['tree_hash'],
                'scaffold_asset_id': str(conf.settings.scaffold_id), 'context_asset_ids': bindings,
                'proof_ids': [str(x) for x in proof_ids], 'versions': versions,
                'synthetic': synthetic, 'source_binding': source_binding, 'suite_kind': 'development',
                'asset_hashes': {str(x): r.get(x, AssetVersion).manifest_hash for x in
                                 (conf.settings.scaffold_id, *[UUID(x) for x in bindings.values()],
                                  *((conf.settings.handoff_id, *conf.settings.prompt_ids) if not synthetic else ()))}}
    if not synthetic and (not bindings or not {'software_commit', 'runtime_images', 'platform', 'isolation'} <= set(versions)):
        raise GateError('Konkrete Software-/Runtime-/Paket-/Isolationsbindung fehlt')
    return store.json(manifest, run_id=run.id, artifact_type='pipeline_manifest')


class Pipeline:
    def __init__(self, register, journal, adapter, runner, *, crash=None):
        self.register, self.journal, self.adapter, self.runner = register, journal, adapter, runner
        self.store = ArtifactStore(register.settings, register)
        self.snapshots = Snapshots(self.store)
        self.crash = crash or (lambda _: None)
        self.continuation = None
        self.active_call = None
        self.before_call = lambda _: None
        self.connection = sqlite3.connect(register.settings.checkpoints / 'pipeline.sqlite3', check_same_thread=False)
        self.saver = BarrierSaver(self.connection, register.settings)
        graph = StateGraph(GraphState)
        for node in ('analyzer', 'planner', 'migrate', 'test', 'runner', 'review', 'repair', 'rerunner', 'seal'):
            graph.add_node(node, self._node(node))
        graph.add_edge(START, 'analyzer')
        for node in ('analyzer', 'planner', 'migrate', 'test', 'runner', 'review', 'repair', 'rerunner'):
            graph.add_conditional_edges(node, lambda s, n=node: self._route(n, s))
        graph.add_edge('seal', END)
        self.graph = graph.compile(checkpointer=self.saver, interrupt_before=list(graph.nodes))

    def close(self):
        self.connection.close()

    def binding(self, run_id):
        row = self.register.connection.execute('SELECT * FROM pipeline_binding WHERE run_id=?', (str(run_id),)).fetchone()
        if not row:
            raise GateError('Pipelineauftrag fehlt')
        return dict(row)

    def manifest(self, run_id):
        row = self.binding(run_id)
        a = self.register.get(row['manifest_id'], Artifact)
        if a.sha256 != row['manifest_hash'] or a.run_id != UUID(str(run_id)):
            raise IntegrityError('Pipelineauftrag beschädigt')
        return json.loads(self.store.read(a.id))

    def install(self, job_id, manifest_id, order_id):
        job = self.register.get(job_id, Job)
        if job.job_type not in ('generation', 'free_test') or job.run_id is None:
            raise GateError('Archivnachrechnung/andere Jobs dürfen keinen Graph starten')
        run = self.register.get(job.run_id, Run)
        a = self.register.get(manifest_id, Artifact)
        body = json.loads(self.store.read(a.id))
        if a.run_id != run.id or body.get('schema') != 'pipeline-v1' or body.get('run_id') != str(run.id) or body.get('config_hash') != run.effective_hash:
            raise GateError('Pipelineinput gehört nicht zum Lauf')
        self.journal._order(order_id, run, 'analyzer', self.adapter)
        with self.register.transaction():
            if self.register.state(run.id).execution=='terminal':
                raise GateError('Terminaler Lauf erhält keine neue Pipelinebindung')
            old = self.register.connection.execute('SELECT * FROM pipeline_binding WHERE run_id=?', (str(run.id),)).fetchone()
            if old:
                if (old['job_id'], old['manifest_id'], old['order_id']) != (str(job.id), str(a.id), str(order_id)):
                    raise GateError('Pipeline bereits anders gebunden')
                return
            self.register.connection.execute('INSERT INTO pipeline_binding VALUES (?,?,?,?,?,?,?,?,?,?)',
                (str(run.id), str(job.id), str(a.id), a.sha256, str(order_id), 'ready', None, None, None, 'Bewusster Start',))

    def preflight(self, run_id):
        run = self.register.get(run_id, Run)
        conf = self.register.get(run.configuration_version_id, ConfigurationVersion)
        body = self.manifest(run_id)
        if body['config_hash'] != conf.content_hash or body['role_contract_hash'] != CONTRACT_HASH or body['prompts_hash'] != PROMPTS_HASH:
            raise PhaseIdentityDrift('Design-/Rollenformatwechsel: neuer Freeze erforderlich')
        actual_software=getattr(self.runner,'software_identity',None)
        from .preparation import software_compatible
        if actual_software and not body['synthetic'] and (not software_compatible(body['versions'].get('runtime_software_identity'),actual=actual_software) or not software_compatible(conf.settings.software_commit,actual=actual_software)):
            raise PhaseIdentityDrift('Tatsächlicher Worker-Softwarestand geändert: neue Version/Erhebungsphase erforderlich; kein Wiederholungsversand')
        if body['suite_kind'] != 'development':
            raise IntegrityError('Interne Pipeline darf keinen Holdout starten')
        if body['synthetic'] and not self.register._is_mock(conf):
            raise GateError('Synthetischer Vorflight autorisiert keine echten Calls')
        if not body['synthetic']:
            if body['versions']['software_commit'] != conf.settings.software_commit:
                raise PhaseIdentityDrift('Softwarestand weicht von Konfiguration ab')
            runtime = getattr(self.runner, 'versions', None)
            if runtime != body['versions']['runtime_images']:
                raise PhaseIdentityDrift('Runtimeimages nicht versionsgleich')
            locked = json.loads(Path(__file__).with_name('sandbox_assets.lock.json').read_text())
            source = body['source_binding']
            asset_lock = json.loads(Path(__file__).with_name('pipeline_assets.lock.json').read_text())
            context_id = conf.settings.context_ids.get(f'{conf.cell.module}-{conf.cell.context}')
            package_path = context_package_path(self.register, conf, strict=True)
            if not source or source['package_path'] != package_path or self.register.get(body['package_id'], Artifact).sha256 != locked[package_path]['sha256']:
                raise PhaseIdentityDrift('Paketwechsel: neuer Freeze erforderlich')
            if not context_id or self.register.get(context_id, AssetVersion).manifest_hash != asset_lock[package_path.replace('package.txt', 'manifest.json')]:
                raise PhaseIdentityDrift('Kontextassetwechsel: neuer Freeze erforderlich')
            if self.register.get(conf.settings.scaffold_id, AssetVersion).manifest_hash != asset_lock['assets/study/m2-v0.1/manifest.json']:
                raise PhaseIdentityDrift('Gerüstassetwechsel: neuer Freeze erforderlich')
            files = json.loads(self.store.read(body['scaffold_id']))['files']
            if {f['path']: f['sha256'] for f in files} != locked['assets/study/m2-v0.1/scaffold']:
                raise PhaseIdentityDrift('Gerüstwechsel: neuer Freeze erforderlich')
        for aid, sha in body['asset_hashes'].items():
            if self.register.get(aid, AssetVersion).manifest_hash != sha:
                raise PhaseIdentityDrift('Upstream-/Gerüst-/Paketwechsel: neuer Freeze erforderlich')
        deps = self.snapshots._dependency(UUID(body['dependency_id']))
        if deps['tree_hash'] != body['dependency_tree_hash']:
            raise IntegrityError('Dependencybestand verändert')
        for key in ('package_id', 'scaffold_id', 'role_contract_id'):
            self._role_ref(run.id, body[key])
        for aid in body['proof_ids']:
            self.store.read(aid)
        if run.purpose == 'main':
            f = self.register.connection.execute('SELECT freeze_id FROM main_plan WHERE planned_id=?', (str(run.planned_run_id),)).fetchone()
            from .domain import Freeze
            self.register._gates(self.register.get(f[0], Freeze))
        else:
            self.register._other_gates(self.register.get(run.phase_id, StudyPhase), conf)
        for role in roles(conf.cell):
            self.journal._order(self.binding(run.id)['order_id'], run, role, self.adapter)
            model = self.register.resolve_call_model(conf, role)
            self.adapter.validate_capabilities(model, self.register.resolve_call_parameters(conf, role), self.journal._endpoint_evidence(model))
        self.runner.preflight(run.id)
        return body

    def _role_ref(self, run_id, aid):
        a = self.register.get(aid, Artifact)
        if a.access_scope not in ('role', 'public_development') or a.run_id not in (None, run_id):
            raise IntegrityError('Fremder/geschützter Rolleninput')
        return self.store.read(aid)

    def _effect(self, run_id, node):
        row = self.register.connection.execute('SELECT artifact_id,sha256 FROM pipeline_effect WHERE run_id=? AND node=?', (str(run_id), node)).fetchone()
        if not row:
            return None
        a = self.register.get(row['artifact_id'], Artifact)
        if a.sha256 != row['sha256'] or a.run_id != UUID(str(run_id)):
            raise IntegrityError('Nodebeleg beschädigt')
        return json.loads(self.store.read(a.id))

    def _save_effect(self, state, node):
        a = self.store.json(state, run_id=UUID(state['run_id']), artifact_type='pipeline_node')
        with self.register.transaction():
            self.register.connection.execute('INSERT INTO pipeline_effect VALUES (?,?,?,?)', (state['run_id'], node, str(a.id), a.sha256))
        self.crash('effect:' + node)
        return state

    def event(self, run_id, kind, details, evidence_ids=()):
        seq = 1 + max((x.sequence for x in self.register.all(Event) if x.run_id == run_id), default=0)
        return self.register.add(Event(code=f'PIPE-EVENT-{uuid4()}', run_id=run_id, sequence=seq,
            event_type=kind, process_id=self.journal.process.id, interval_id=None,
            happened_at=datetime.now(timezone.utc), evidence_ids=tuple(evidence_ids), details=details))

    def _node(self, node):
        def execute(state):
            state = self._checked_state(state)
            if node == 'seal':
                abort_state = self._effect(state['run_id'], 'abort')
                if abort_state is not None:
                    state = self._checked_state(abort_state)
            old = self._effect(state['run_id'], node)
            if old is not None:
                return self._checked_state(old)
            self.event(UUID(state['run_id']), 'node_started', {'node': node})
            try:
                if node == 'seal':
                    self._seal(state)
                elif node in ('runner', 'rerunner'):
                    self._run_tests(state, node)
                else:
                    self._call(state, node)
            except (GateError, IntegrityError, ValueError, OSError) as error:
                if node == 'seal':
                    raise
                self._failure(state, node, error)
            self.event(UUID(state['run_id']), 'node_finished', {'node': node, 'status': state['statuses'].get(node)})
            return self._save_effect(state, node)
        return execute

    def _checked_state(self, state):
        if set(state) != {'run_id', 'config_hash', 'statuses', 'refs'}:
            raise IntegrityError('Unerlaubter Graphstate')
        run = self.register.get(state['run_id'], Run)
        if state['config_hash'] != run.effective_hash:
            raise IntegrityError('Graphkonfiguration verändert')
        for aid in state['refs'].values():
            a = self.register.get(aid, Artifact)
            if a.run_id != run.id or protected_evaluation(a):
                raise IntegrityError('Fremder/geschützter Checkpointref')
            self.store.read(aid)
        return {'run_id': state['run_id'], 'config_hash': state['config_hash'],
                'statuses': dict(state['statuses']), 'refs': dict(state['refs'])}

    def _inputs(self, state, role):
        base = self.manifest(state['run_id'])
        keys = {'analyzer': (), 'planner': ('analyzer',), 'migrate': ('analyzer', 'planner'),
                'test': ('candidate', 'analyzer', 'planner'),
                'review': ('candidate', 'analyzer', 'planner', 'test', 'runner'),
                'repair': ('candidate', 'analyzer', 'planner', 'test', 'runner', 'review')}[role]
        ids = [base['package_id'], base['scaffold_id'], base['role_contract_id']]
        ids += [state['refs'][k] for k in keys if k in state['refs']]
        contents = []
        for index, aid in enumerate(ids):
            data = self._role_ref(UUID(state['run_id']), aid)
            if index == 1:
                scaffold = json.loads(data)
                data = canonical({'schema': scaffold['schema'], 'files': [{'path': f['path'], 'content': self._role_ref(UUID(state['run_id']), f['artifact_id']).decode()} for f in scaffold['files']]}).encode()
            contents.append({'artifact_id': aid, 'content': data.decode('utf-8')})
        return tuple(UUID(x) for x in ids), contents

    def workspace(self, run_id):
        return self.register.settings.staging / 'pipeline' / str(run_id) / 'candidate'

    def _restore_scaffold(self, run_id):
        root = self.workspace(run_id)
        body = json.loads(self.store.read(self.manifest(run_id)['scaffold_id']))
        root.mkdir(parents=True, exist_ok=True)
        for f in body['files']:
            if not (root / f['path']).exists():
                atomic_file(root, f['path'], self.store.read(f['artifact_id']), mode=f['mode'])
        return root

    def _apply(self, state, node, output):
        root = self._restore_scaffold(state['run_id'])
        with self.snapshots.writer_lease(root):
            for entry in output['files']:
                atomic_file(root, entry['path'], entry['content'].encode(), mode=0o644, replace=True)
            # A crash midwrite repeats exact immutable contents, then recaptures.
            self.crash('files:' + node)
            snapshot = self.snapshots.capture(root, role='post_repair' if node == 'repair' else 'post_migrate',
                                             **self._snapshot_args(state))
        state['refs']['snapshot'] = str(snapshot.file_manifest_id)

    def _snapshot_args(self, state):
        body = self.manifest(state['run_id'])
        tests_hash = self.register.get(state['refs']['internal_tests'], Artifact).sha256 if 'internal_tests' in state['refs'] else hashlib.sha256(b'').hexdigest()
        return {'run_id': UUID(state['run_id']), 'scaffold_id': UUID(body['scaffold_asset_id']),
                'dependency_ids': (UUID(body['dependency_id']),), 'internal_tests_hash': tests_hash,
                'repair_count': int('repair' in state['refs']),
                'process_artifact_ids': tuple(UUID(x) for k, x in state['refs'].items() if k != 'snapshot')}

    def _call(self, state, role):
        ids, contents = self._inputs(state, role)
        run = self.register.get(UUID(state['run_id']), Run)
        cell = self.register.get(run.configuration_version_id, ConfigurationVersion).cell
        expected = None
        paths = TEST_PATHS if role == 'test' else MODULE_PATHS if role in ('migrate', 'repair') else ()
        request = {'role': role, 'module': cell.module, 'context': cell.context, 'inputs': contents,
                   'allowed_output_paths': paths}
        if role == 'repair':
            expected = [x['path'] for x in json.loads(self.store.read(state['refs']['migrate']))['files']]
            request['required_output_paths'] = expected
        messages = [{'role': 'system', 'content': PROMPTS['roles'][role]},
                    {'role': 'user', 'content': canonical(request)}]
        row = self.binding(state['run_id'])
        self.before_call(UUID(state['run_id']))
        call = self.journal.prepare(self.adapter, UUID(state['run_id']), role, order_id=row['order_id'],
                                    messages=messages, input_artifact_ids=ids, allowed_paths=paths,
                                    crash=lambda p: self.crash(role + ':' + p))
        self.active_call = call.id
        try:
            report = self.journal.execute(self.adapter, call.id, continuation=self.continuation,
                                          crash=lambda p: self.crash(role + ':' + p))
        finally:
            self.active_call = None
        if report['attempts'][-1]['status'] not in ('validated', 'incorporated'):
            raise GateError('Adapter stop: ' + str(report['stop_reason']))
        rejection = []
        def output_validator(content):
            try:
                return validate(content, role, expected_paths=expected, workspace=self.workspace(state['run_id']))
            except (ValueError, IntegrityError) as error:
                rejection.append(error)
                self.store.json({'role': role, 'reason': str(error), 'exception': type(error).__name__, 'raw_parsed_id': self.journal._attempt(self.journal._latest(call.id).id)['parsed_id']},
                                run_id=call.run_id, call_id=call.id, artifact_type='pipeline_rejected_output')
                raise
        try:
            self.journal.incorporate(call.id, output_validator=output_validator,
                                    crash=lambda p: self.crash(role + ':' + p))
        except GateError:
            if rejection:
                raise rejection[0]
            raise
        # Revalidate even on incorporated reentry; validator is deterministic.
        parsed = self.journal._attempt(self.journal._latest(call.id).id)['parsed_id']
        output = validate(json.loads(self.store.read(parsed))['content'], role, expected_paths=expected, workspace=self.workspace(state['run_id']))
        saved = self.store.json(output, run_id=UUID(state['run_id']), call_id=call.id,
                                artifact_type='role_' + role, access_scope='role')
        state['refs'][role] = str(saved.id)
        state['statuses'][role] = 'changes_required' if role == 'review' and output['changes_required'] else 'complete'
        if role in ('migrate', 'repair'):
            state['refs']['candidate'] = str(saved.id)
            self._apply(state, role, output)
        elif role == 'test':
            a = self.store.store(test_bundle(output), run_id=UUID(state['run_id']), artifact_type='own_internal_tests', access_scope='role')
            state['refs']['internal_tests'] = str(a.id)

    def _candidate(self, state):
        return self.snapshots.capture(self.workspace(state['run_id']),
                                     role='post_repair' if 'repair' in state['refs'] else 'post_migrate', **self._snapshot_args(state))

    def _run_tests(self, state, node):
        candidate = self._candidate(state)
        row = self.binding(state['run_id'])
        report = self.runner.execute(UUID(row['job_id']), candidate.id, UUID(state['refs']['internal_tests']),
                                     node=node, continuation=self.continuation)
        if report.get('classification') not in ('passed', 'candidate_failure', 'technical_failure', 'protection_failure'):
            raise IntegrityError('Runnerursache fehlt')
        a = self.store.json(report, run_id=UUID(state['run_id']), artifact_type='pipeline_internal_result', access_scope='role')
        state['refs'][node] = str(a.id)
        if report['classification'] in ('technical_failure', 'protection_failure'):
            raise IntegrityError('Interner Runner: ' + report['classification'])
        state['statuses'][node] = report['classification']

    def _failure(self, state, node, error):
        calls = [x for x in self.register.all(ModelCall) if str(x.run_id) == state['run_id'] and x.node == node]
        stop = self.journal.diagnostic(calls[-1].id) if calls else None
        reason = stop['stop_reason'] if stop else None
        content = (reason == 'role_format_or_candidate_rejection' and not isinstance(error, IntegrityError)) or isinstance(error, ValueError) and not isinstance(error, (GateError, IntegrityError))
        unknown = bool(stop and stop['attempts'][-1]['status'] == 'outcome_unknown')
        state['statuses'][node] = 'failed'
        state['statuses']['cause'] = 'content_failure' if content else 'outcome_unknown' if unknown else 'technical_failure'
        a = self.store.json({'node': node, 'cause': state['statuses']['cause'], 'reason': reason or str(error),
                             'call_id': str(calls[-1].id) if calls else None},
                            run_id=UUID(state['run_id']), artifact_type='pipeline_failure')
        state['refs']['failure'] = str(a.id)
        if not content:
            run = self.register.get(state['run_id'], Run)
            drift = isinstance(error, PhaseIdentityDrift) or reason == 'reported_identity_drift'
            self.register.set_phase_status(run.phase_id, 'stopped' if drift else 'paused', reason=reason or str(error))

    def _route(self, node, state):
        if 'cause' in state['statuses']:
            return 'seal'
        conf = self.register.get(self.register.get(state['run_id'], Run).configuration_version_id, ConfigurationVersion)
        if node == 'analyzer':
            return 'planner' if conf.cell.planner else 'migrate'
        if node == 'planner':
            return 'migrate'
        if node == 'migrate':
            return 'test'
        if node == 'test':
            return 'runner'
        if node == 'runner':
            return 'review' if conf.cell.review else 'repair' if state['statuses']['runner'] == 'candidate_failure' else 'seal'
        if node == 'review':
            return 'repair' if state['statuses']['runner'] == 'candidate_failure' or state['statuses']['review'] == 'changes_required' else 'seal'
        if node == 'repair':
            return 'rerunner'
        return 'seal'

    def _seal(self, state):
        run_id = UUID(state['run_id'])
        saved = None
        if 'candidate' in state['refs']:
            writers = self.runner.writers(run_id)
            saved = self.snapshots.seal(self.workspace(run_id), writers=writers, **self._snapshot_args(state),
                                        copy_crash=lambda p: self.crash('seal:' + p))
            state['refs']['seal'] = str(saved.file_manifest_id)
        state['statuses']['seal'] = 'sealed' if saved else 'no_candidate'
        cause = state['statuses'].get('cause', 'content_failure' if state['statuses'].get('rerunner', state['statuses'].get('runner')) == 'candidate_failure' else 'finished')
        old = self.register.state(run_id)
        if old.execution != 'terminal':
            self.register.set_state(run_id, old.model_copy(update={'execution': 'terminal', 'terminal_cause': cause,
                'seal': 'sealed' if saved else 'no_candidate', 'candidate_id': saved.id if saved else None,
                'ended_at': datetime.now(timezone.utc)}), reason='Pipeline Seal: ' + cause)

    def step(self, run_id, *, continuation=None):
        self.continuation = continuation
        config = {'configurable': {'thread_id': str(run_id)}}
        snapshot = self.graph.get_state(config)
        if not snapshot.values:
            run = self.register.get(run_id, Run)
            self.graph.invoke({'run_id': str(run.id), 'config_hash': run.effective_hash, 'statuses': {}, 'refs': {}}, config)
            snapshot = self.graph.get_state(config)
        if snapshot.next:
            self.graph.invoke(None, config)
        self.crash('checkpoint')
        return self.graph.get_state(config)

    def summary(self, run_id):
        intervals = tuple(i for i in self.register.all(TimeInterval) if i.run_id == run_id)
        from .timing import summarize
        binding, state = self.binding(run_id), self.register.state(run_id)
        timing = summarize(intervals, coverage_complete=state.execution == 'terminal' and binding['clock_json'] is None and bool(intervals))
        return {'run_id': str(run_id), 'timing': {k: v.model_dump(mode='json') if hasattr(v, 'model_dump') else v for k, v in timing.items()},
                'costs': self.journal.effective_costs(run_id), 'state': state.model_dump(mode='json')}

    def read_summary(self, artifact_id):
        artifact = self.register.get(artifact_id, Artifact)
        if artifact.artifact_type != 'pipeline_summary' or artifact.run_id is None:
            raise GateError('Keine gespeicherte Pipelineableitung')
        saved = json.loads(self.store.read(artifact.id))
        current = self.journal.effective_costs(artifact.run_id)
        valid = all(saved['costs'].get(k) == current[k] for k in ('evidence_revision', 'phase_revision'))
        return {'saved': saved, 'cost_derivation_status': 'current' if valid else 'stale', 'current_costs': current}

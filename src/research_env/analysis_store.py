"""M7 exact selection -> explicit acknowledgement -> immutable analysis_run.

Only trusted register/CAS readers are used. The arithmetic module knows neither
this storage layer nor the adapter. No model or evaluator is called here.
"""
from .numeric import deterministic
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

from .analysis import INPUT_SCHEMA, VERSION, compute, value
from .analysis_resources import area_costs, resource_profile
from .artifacts import ArtifactStore, IntegrityError
from .domain import (AnalysisRun, Artifact, AssetVersion, CandidateSnapshot,
    ConfigurationVersion, CountObservation, CriterionReviewRevision, Freeze,
    MeasurementAttempt, MetricObservation, ModelCall, ModelPackage, ResourceProfile, Run,
    Study, StudyPhase, TestResult, TimeInterval, TransportAttempt, TransportStateEvent, canonical, digest)
from .evaluation_oracles import Suite
from .register import GateError

SOURCE_FILES = ('analysis_charts.py','analysis_absence.py','analysis.py','analysis_resources.py','analysis_store.py','analysis_export.py','analysis_metrics.py','analysis_tables.py',
    'numeric.py','providers.py','adapter.py','register.py','domain.py','matrix.py','timing.py','artifacts.py','locks.py','evaluation_oracles.py',
    'evaluation_assets.lock.json','migrations/011_analysis.sql')


def source_binding():
    root = Path(__file__).parent
    return {'version': VERSION, 'schema': INPUT_SCHEMA, 'files': {name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in SOURCE_FILES}}


def missing_decisions(snapshot):
    output = {}
    for row in snapshot['rows']:
        pid = row['plan']['id']
        if row['missing']:
            output[pid] = row['missing']
        for group in ('measurements','reviews'):
            for key, selected in row['selection'][group].items():
                if selected['missing']:
                    output[f'{pid}:{group}:{key}'] = selected['missing']
    result = compute(snapshot)
    for row in result['cells']:
        pid = row['id']
        for key, observation in {**row['functional']['R'], **row['functional']['T_criteria'], 'F': row['functional']['F'], **{k:row['static'][k] for k in ('D','L','S')}}.items():
            if observation['value'] is None:
                output[f'{pid}:value:{key}'] = observation['status']+': '+observation['reason']
        for key in ('pipeline_seconds','setup_seconds','evaluation_seconds','context_preparation_seconds'):
            observation = row['resources'].get(key)
            if not observation or observation['value'] is None:
                output[f'{pid}:resource:{key}'] = observation['reason'] if observation else 'Not collected'
        costs = row['resources'].get('costs')
        if not costs or costs['status']!='known':
            output[f'{pid}:cost'] = 'Not collected' if not costs else costs['status']
    return output


class Analyses:
    def __init__(self, register, cost_view, *, assets=None):
        if cost_view.register is not register:
            raise GateError('Cost reader must share the same register snapshot')
        self.register = register
        self.store = ArtifactStore(register.settings, register)
        self.cost_view = cost_view
        self.assets = Path(assets) if assets else Path(__file__).resolve().parents[2]
        self._proof_cache = None

    def _cases(self, suite_id, module, *, synthetic):
        asset = self.register.get(suite_id, AssetVersion)
        if asset.suite_kind != 'study_holdout':
            raise GateError('Main analysis needs frozen study_holdout cases')
        catalogs = [self.register.get(aid, Artifact) for aid in asset.artifact_ids
                    if self.register.get(aid, Artifact).artifact_type=='evaluation_case_catalog']
        if catalogs:
            if len(catalogs)!=1 or not synthetic:
                raise GateError('Synthetic case catalog cannot replace empirical frozen suite')
            artifact = catalogs[0]
            if artifact.sha256 != asset.manifest_hash or artifact.producer!='trusted_evaluator' or artifact.access_scope!='trusted_evaluator' or artifact.run_id:
                raise GateError('Synthetic frozen case binding invalid')
            body = json.loads(self.store.read(artifact.id))
            if body.get('data_origin')!='synthetic':
                raise GateError('Case fixture needs explicit synthetic provenance')
            return [c for c in body['cases'] if c['module']==module]
        lock = json.loads(Path(__file__).with_name('evaluation_assets.lock.json').read_text())
        relative = 'study_holdout/m2-v0.1'
        if asset.manifest_hash != digest(lock[relative]):
            raise GateError('Frozen suite hash does not match supported exact case bytes')
        suite = Suite(self.assets/'evaluation'/relative, kind='study_holdout', expected_hashes=lock[relative])
        return [{'id': c['id'], 'category': c['category'], 'module': c['module'],
            'assertions': [a['id'] for s in c['steps'] for a in s['assertions']]} for c in suite.cases_for(module)]

    def _read_evidence(self, value):
        """Every current proof CAS artifact must have exact bytes.

        Revision predecessors remain historical provenance, independent of the
        new correction's current raw/code/integration proof dependencies.
        """
        pending = [value.id]
        visited = set()
        cache = self._proof_cache if self._proof_cache is not None else {}
        while pending:
            identity = pending.pop()
            if identity in visited:
                continue
            visited.add(identity)
            if identity not in cache:
                obj = self.register.get(identity)
                if isinstance(obj, Artifact):
                    self.store.read(obj.id)
                sha = self.register.connection.execute('SELECT sha256 FROM register_record WHERE id=?', (str(identity),)).fetchone()[0]
                cache[identity] = (sha, tuple(self.register.proof_references(obj)))
            pending.extend(cache[identity][1])
        return {str(identity): cache[identity][0] for identity in sorted(visited, key=str)}

    @deterministic
    def _resources(self, run):
        records = {
            'intervals': self.register.for_run(TimeInterval, run.id),
            'resource_records': self.register.for_run(ResourceProfile, run.id),
            'metric_observations': [x for x in self.register.for_run(MetricObservation, run.id) if x.measurement_id is None],
            'model_calls': self.register.for_run(ModelCall, run.id)}
        call_ids = {x.id for x in records['model_calls']}
        records['transports'] = [x for x in self.register.all(TransportAttempt) if x.call_id in call_ids]
        transport_ids = {x.id for x in records['transports']}
        records['dispatches'] = [x for x in self.register.all(TransportStateEvent)
            if x.transport_id in transport_ids and x.status == 'dispatching']
        evidence = {}
        for group in records.values():
            for record in group:
                evidence.update(self._read_evidence(record))
        intervals = [x.model_dump(mode='json') for x in records['intervals']]
        pipeline = self.register.connection.execute('SELECT * FROM pipeline_binding WHERE run_id=?', (str(run.id),)).fetchone()
        state = self.register.state(run.id)
        if pipeline:
            manifest = self.register.get(pipeline['manifest_id'], Artifact)
            evidence.update(self._read_evidence(manifest))
            body = json.loads(self.store.read(manifest.id))
            if manifest.sha256!=pipeline['manifest_hash'] or manifest.run_id!=run.id or body.get('run_id')!=str(run.id) or body.get('config_hash')!=run.effective_hash:
                raise IntegrityError('Pipeline resource binding drift')
        raw = {'costs': self.cost_view.effective_costs(run.id), 'intervals': intervals,
            'coverage_complete': bool(intervals) and bool(pipeline) and pipeline['clock_json'] is None and pipeline['status']=='completed' and state.execution=='terminal',
            **{key: [x.model_dump(mode='json') for x in records[key]] for key in ('resource_records','metric_observations','model_calls','transports','dispatches')}}
        profile = resource_profile(raw)
        profile['register_evidence_hashes'] = evidence
        return profile

    @deterministic
    def snapshot(self, freeze_id):
        """Call inside one transaction; no free revision selection parameters."""
        if not self.register.connection.in_transaction:
            raise RuntimeError('Analysis input requires one consistent register transaction')
        # Only share verified graph nodes within this exact snapshot. A later
        # proposal/confirmation must read and hash every referenced object anew.
        previous = self._proof_cache
        self._proof_cache = {}
        try:
            return self._snapshot(freeze_id)
        finally:
            self._proof_cache = previous

    def _snapshot(self, freeze_id):
        f = self.register.get(freeze_id, Freeze)
        phase = self.register.get(f.phase_id, StudyPhase)
        if phase.purpose != 'main':
            raise GateError('Only frozen main phase supports research analysis')
        study = self.register.get(phase.study_id, Study)
        selections = self.register.analysis_inputs(f.id)
        rows, evidence = [], self._read_evidence(f)
        for plan in f.matrix:
            selected = selections[plan['id']]
            conf = self.register.get(f.configuration_version_ids[plan['cell_key']], ConfigurationVersion)
            row = {'plan': plan, 'run_id': selected['run_id'], 'purpose': 'main', 'configuration_id': str(conf.id),
                'settings': conf.settings.model_dump(mode='json'),
                'models': {slot: self.register.get(identity, ModelPackage).model_dump(mode='json')
                           for slot, identity in (('A', conf.settings.model_a), ('B', conf.settings.model_b)) if identity},
                'configuration_hash': conf.content_hash, 'cases': self._cases(conf.settings.holdout_suite_id, conf.cell.module, synthetic=study.data_origin=='synthetic'),
                'selection': selected, 'candidate_hash': None, 'state': None, 'reviews': {}, 'test_results': [], 'static_report': None,
                'resources': {}, 'missing': selected['missing'], 'missing_status': 'not_collected' if not selected['run_id'] else 'pending'}
            if selected['run_id']:
                run = self.register.get(UUID(selected['run_id']), Run)
                if run.purpose!='main' or run.phase_id!=phase.id or str(run.planned_run_id)!=plan['id'] or run.configuration_version_id!=conf.id:
                    raise GateError('Run outside exact frozen phase/configuration/planned identity')
                state = self.register.state(run.id)
                row['state'] = state.model_dump(mode='json')
                row['missing_status'] = 'unresolved' if state.terminal_cause=='outcome_unknown' else 'technical_missing' if state.terminal_cause in ('technical_failure','interrupted') else 'pending'
                if state.candidate_id and state.seal=='sealed':
                    candidate = self.register.get(state.candidate_id, CandidateSnapshot)
                    row['candidate_hash'] = candidate.tree_hash
                    evidence.update(self._read_evidence(candidate))
                row['resources'] = self._resources(run)
                evidence.update(row['resources']['register_evidence_hashes'])
                evidence.update(self._read_evidence(run))
                for key, selection in selected['measurements'].items():
                    if not selection['revision_id']:
                        continue
                    if key=='candidate_absence':
                        from .analysis_absence import receipt_binding
                        artifact = self.register.get(UUID(selection['revision_id']), Artifact)
                        binding = receipt_binding(self.register, artifact)
                        report = binding['receipt']
                        evidence.update(self._read_evidence(artifact))
                        self.store.read(UUID(binding['input_id']))
                        row['absence_binding'] = binding
                        row['test_results'] = [{'test_id': x['case_id'], 'assertion_id': x['id'], 'r_category': x['category'],
                            'status': x['status'], 'cause': x['cause'], 'expected': canonical(x['expected']), 'actual': value(unit='oracle', status='technical_missing', reason=x['cause']),
                            'input_artifact_id': x['input_artifact_id'], 'raw_artifact_id': str(artifact.id)} for x in report['entries']]
                        for x in row['test_results']:
                            self.store.read(UUID(x['input_artifact_id']))
                        if report['T']==0:
                            row['reviews']['T1'] = {'verdict': value(0, unit='binary', source=str(artifact.id)), 'origin': 'automatic attributed absence',
                                'code_artifact_id': None, 'measurement_ids': [str(artifact.id)]}
                        continue
                    m = self.register.get(UUID(selection['revision_id']), MeasurementAttempt)
                    evidence.update(self._read_evidence(m))
                    if key=='functional_R':
                        ids = self.register.connection.execute("SELECT id FROM register_record WHERE kind='TestResult' AND json_extract(payload,'$.measurement_id')=? ORDER BY rowid", (str(m.id),)).fetchall()
                        row['test_results'] = [self.register.get(x[0], TestResult).model_dump(mode='json') for x in ids]
                        for item in row['test_results']:
                            evidence.update(self._read_evidence(self.register.get(UUID(item['id']), TestResult)))
                    if key=='static_DLS':
                        row['static_report'] = json.loads(m.result.value) if m.result.status=='observed' else None
                for key, selection in selected['reviews'].items():
                    if selection['revision_id']:
                        review = self.register.get(UUID(selection['revision_id']), CriterionReviewRevision)
                        row['reviews'][key] = review.model_dump(mode='json')
                        evidence.update(self._read_evidence(review))
            else:
                row['resources'] = {'pipeline_seconds': value(unit='s', status='not_collected', reason='Not started')}
            rows.append(row)
        # Preserve separate operational areas from the same study. No FQ cells
        # or denominator is taken from these records.
        costs = []
        for run in sorted(self.register.all(Run), key=lambda x: str(x.id)):
            run_phase = self.register.get(run.phase_id, StudyPhase)
            if run_phase.study_id!=study.id or run.purpose=='main' and run.phase_id!=phase.id:
                continue
            resources = next((row['resources'] for row in rows if row['run_id']==str(run.id)), None)
            if resources is None:
                resources = self._resources(run)
            evidence.update(resources['register_evidence_hashes'])
            costs.append({'run_id': str(run.id), 'purpose': run.purpose, 'resources': resources})
        phase_resources = [x for x in self.register.for_run(MetricObservation, None) if x.phase_id==phase.id]
        for observation in phase_resources:
            evidence.update(self._read_evidence(observation))
        return {'schema': INPUT_SCHEMA, 'analysis_version': VERSION, 'source_binding': source_binding(),
            'data_origin': study.data_origin, 'phase_id': str(phase.id), 'freeze_id': str(f.id), 'freeze_hash': f.freeze_hash,
            'phase_revision': self.register.revision(phase.id), 'r_c': f.r_c, 'r_e': f.r_e, 'seed': f.seed,
            'selection_rule': f.selection_rule, 'selected_revisions': selections, 'rows': rows,
            'resource_areas': area_costs(costs), 'operational_resources': costs,
            'phase_resources': [x.model_dump(mode='json') for x in phase_resources],
            'register_evidence_hashes': evidence, 'excluded_run_ids': [str(run.id) for run in sorted(self.register.all(Run), key=lambda x:str(x.id))
                if self.register.get(run.phase_id, StudyPhase).study_id==study.id and (run.purpose!='main' or run.phase_id!=phase.id)]}

    @deterministic
    def propose(self, freeze_id):
        with self.register.transaction():
            snapshot = self.snapshot(freeze_id)
        artifact = self.store.json(snapshot, artifact_type='analysis_input', producer=VERSION, access_scope='trusted_register')
        proposal_id = str(uuid4())
        with self.register.transaction():
            if self.snapshot(freeze_id)!=snapshot:
                raise GateError('Data changed while proposal was saved; request fresh proposal')
            self.register.connection.execute('INSERT INTO analysis_selection VALUES(?,?,?,?,?,?,?)',
                (proposal_id, snapshot['phase_id'], str(freeze_id), str(artifact.id), digest(snapshot), VERSION, snapshot['phase_revision']))
        return {'proposal_id': proposal_id, 'input_hash': digest(snapshot), 'snapshot_artifact_id': str(artifact.id),
            'selected_revisions': snapshot['selected_revisions'], 'open_decisions': missing_decisions(snapshot),
            'data_origin': snapshot['data_origin'], 'confirmed': False, 'preview': compute(snapshot)}

    @deterministic
    def confirm(self, proposal_id, *, expected_input_hash, acknowledged_selection, acknowledged_open,
                confirmed_by, decision, software_commit, synthetic=False):
        if not confirmed_by.strip() or not decision.strip() or not software_commit.strip():
            raise GateError('Explicit person/decision/software binding required')
        record = self.register.connection.execute('SELECT * FROM analysis_selection WHERE proposal_id=?', (str(proposal_id),)).fetchone()
        if not record:
            raise GateError('Unknown proposal')
        snapshot = json.loads(self.store.read(UUID(record['snapshot_id'])))
        if expected_input_hash!=record['input_hash'] or digest(snapshot)!=record['input_hash'] or acknowledged_selection!=snapshot['selected_revisions'] or acknowledged_open!=missing_decisions(snapshot):
            raise GateError('Acknowledge exact immutable inputs, revisions and open criteria')
        if synthetic != (snapshot['data_origin']=='synthetic') or synthetic and not confirmed_by.startswith('TECHNICAL-FIXTURE:'):
            raise GateError('Synthetic confirmation must be explicitly marked; no research approval')
        previous = self.register.connection.execute('SELECT analysis_id FROM analysis_confirmation WHERE proposal_id=?', (str(proposal_id),)).fetchone()
        if previous:
            saved = self.register.get(previous[0], AnalysisRun)
            original_ack = json.loads(self.store.read(UUID(saved.selected_inputs['_m7']['acknowledgement_id'])))
            if saved.confirmed_by!=confirmed_by or original_ack['decision']!=decision or saved.software_commit!=software_commit:
                raise GateError('Confirmation identity collision')
            return saved
        from .analysis_export import render
        results = compute(snapshot)
        files = render(results)
        artifacts = {name: self.store.store(data, artifact_type='analysis_output', producer=VERSION, access_scope='trusted_register', original_name=name,
                     mime_type=mime) for name,(data,mime) in files.items()}
        acknowledgement = self.store.json({'proposal_id': str(proposal_id), 'input_hash': expected_input_hash,
            'selected_revisions': acknowledged_selection, 'open_decisions': acknowledged_open, 'person': confirmed_by,
            'decision': decision, 'synthetic': synthetic, 'confirmed_at': datetime.now(timezone.utc).isoformat()},
            artifact_type='analysis_acknowledgement', producer=VERSION, access_scope='trusted_register')
        with self.register.transaction():
            if self.snapshot(UUID(record['freeze_id']))!=snapshot:
                raise GateError('Proposal is stale; explicitly confirm a new exact data stand')
            asset = self.register._put(AssetVersion(code='M7-CODE-'+str(uuid4()), asset_type='analysis', manifest_hash=digest(source_binding()),
                origin='Actual versioned analysis sources; no study approval', access_scope='trusted_register'))
            selected = {**snapshot['selected_revisions'], '_m7': {'proposal_id': str(proposal_id), 'snapshot_id': record['snapshot_id'],
                'acknowledgement_id': str(acknowledgement.id), 'version': VERSION, 'outputs': {name: str(a.id) for name,a in artifacts.items()}}}
            n = {'n_C': results['n_C'], 'n_EM': results['UF3']['n'], 'n_EP': results['UF4']['n'], 'planned': len(results['cells']), 'candidates': results['static_validity']['denominator']}
            analysis = self.register._put(AnalysisRun(code='M7-ANALYSIS-'+str(uuid4()), phase_id=UUID(snapshot['phase_id']), freeze_id=UUID(snapshot['freeze_id']),
                input_hash=expected_input_hash, selected_inputs=selected, selection_rule=snapshot['selection_rule'], software_commit=software_commit,
                analysis_asset_id=asset.id, confirmed_by=confirmed_by, confirmed_at=datetime.fromisoformat(json.loads(self.store.read(acknowledgement.id))['confirmed_at']),
                missing_decisions=missing_decisions(snapshot), excluded_ids=tuple(UUID(x) for x in snapshot['excluded_run_ids']), block_sets={'B_C': tuple(UUID(x) for x in results['B_C']),
                    'B_EM': tuple(UUID(x) for x in results['UF3']['complete_block_ids']), 'B_EP': tuple(UUID(x) for x in results['UF4']['complete_block_ids'])},
                denominators={key: CountObservation(status='observed', value=number, unit='count', source='Exact immutable input IDs') for key,number in n.items()},
                result_artifact_ids=tuple(a.id for a in artifacts.values())))
            self.register.connection.execute('INSERT INTO analysis_confirmation VALUES(?,?,?)', (str(analysis.id), str(proposal_id), str(acknowledgement.id)))
        return analysis

    @deterministic
    def recalculate(self, analysis_id):
        saved = self.register.get(analysis_id, AnalysisRun)
        binding = saved.selected_inputs.get('_m7')
        if not binding or binding['version']!=VERSION:
            raise GateError('Unsupported historical analysis version')
        snapshot = json.loads(self.store.read(UUID(binding['snapshot_id'])))
        from .analysis_reproduction import computation_compatible
        if digest(snapshot)!=saved.input_hash or not computation_compatible(snapshot['source_binding']):
            raise IntegrityError('Historical data or computation source mismatch; use the saved computation version')
        result = compute(snapshot)
        expected = json.loads(self.store.read(UUID(binding['outputs']['analysis.json'])))
        if result!=expected:
            raise IntegrityError('Recalculation differs from stored result')
        return result


@deterministic
def validate_analysis_record(register, v):
    """Register insertion gate; fabricated M7 records cannot bypass confirmation."""
    binding = v.selected_inputs['_m7']
    proposal = register.connection.execute('SELECT * FROM analysis_selection WHERE proposal_id=?', (binding['proposal_id'],)).fetchone()
    if not proposal or proposal['snapshot_id']!=binding['snapshot_id'] or proposal['input_hash']!=v.input_hash or proposal['phase_id']!=str(v.phase_id) or proposal['freeze_id']!=str(v.freeze_id):
        raise GateError('Unbound M7 proposal')
    store = ArtifactStore(register.settings,register)
    snapshot = json.loads(store.read(UUID(binding['snapshot_id'])))
    acknowledgement = json.loads(store.read(UUID(binding['acknowledgement_id'])))
    expected = {key: item for key,item in v.selected_inputs.items() if key!='_m7'}
    if expected!=register.analysis_inputs(v.freeze_id) or snapshot['selected_revisions']!=expected or digest(snapshot)!=v.input_hash or snapshot['phase_revision']!=register.revision(v.phase_id):
        raise GateError('No free analysis revision choice or stale confirmation')
    if acknowledgement['input_hash']!=v.input_hash or acknowledgement['selected_revisions']!=expected or acknowledgement['open_decisions']!=v.missing_decisions or acknowledgement['person']!=v.confirmed_by or not acknowledgement['decision']:
        raise GateError('Exact explicit acknowledgement required')
    from .adapter import CallJournal
    if Analyses(register, CallJournal.cost_view(register)).snapshot(v.freeze_id)!=snapshot:
        raise GateError('Snapshot is not the exact current immutable register/CAS data')
    result = compute(snapshot)
    expected_blocks = {'B_C': tuple(UUID(x) for x in result['B_C']), 'B_EM': tuple(UUID(x) for x in result['UF3']['complete_block_ids']), 'B_EP': tuple(UUID(x) for x in result['UF4']['complete_block_ids'])}
    if v.block_sets!=expected_blocks or tuple(str(x) for x in v.excluded_ids)!=tuple(snapshot['excluded_run_ids']):
        raise GateError('Analysis block/exclusion denominator mismatch')
    if register.get(v.analysis_asset_id, AssetVersion).manifest_hash!=digest(snapshot['source_binding']):
        raise GateError('Analysis source asset mismatch')
    if json.loads(store.read(UUID(binding['outputs']['analysis.json'])))!=result or set(binding['outputs'].values())!={str(x) for x in v.result_artifact_ids}:
        raise GateError('Analysis output mismatch')

    from .analysis_export import render
    expected_files = render(result)
    if set(binding['outputs'])!=set(expected_files):
        raise GateError('Complete core outputs required')
    for name,(content,mime) in expected_files.items():
        artifact = register.get(UUID(binding['outputs'][name]), Artifact)
        if store.read(artifact.id)!=content or artifact.mime_type!=mime or artifact.original_name!=name:
            raise GateError('Export is not derived from exact core: '+name)

"""Short explicit SQL transactions; every external operation occurs outside this API.

Immutable records retain provenance and data contracts. Mutable projections contain
only operational state. This module never opens network/Docker/object-store handles.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import UUID, uuid4

from .database import connect
from .locks import write_barrier
from .domain import *
from .matrix import ALGORITHM, matrix


class GateError(ValueError):
    pass


class Register:
    def __init__(self, settings, *, readonly=False):
        self.settings = settings
        self.connection = connect(settings, readonly=readonly)

    def close(self):
        self.connection.close()

    @contextmanager
    def transaction(self):
        if self.connection.in_transaction:
            raise RuntimeError('Keine geschachtelte/offene externe Transaktion')
        with write_barrier(self.settings):
            self.connection.execute('BEGIN IMMEDIATE')
            try:
                yield
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise

    def _get(self, entity_id, cls=None):
        row = self.connection.execute('SELECT * FROM register_record WHERE id=?', (str(entity_id),)).fetchone()
        if row is None:
            raise GateError(f'Fehlende Referenz: {entity_id}')
        if cls is not None and row['kind'] != cls.__name__:
            raise GateError(f'Falscher Referenztyp: {row["kind"]} statt {cls.__name__}')
        data = json.loads(row['payload'])
        if digest(data) != row['sha256']:
            raise GateError(f'Beschädigter Datensatz: {entity_id}')
        return ENTITIES[row['kind']].model_validate(data)

    def get(self, entity_id, cls=None):
        return self._get(entity_id, cls)

    def all(self, cls):
        return [self._get(r[0], cls) for r in self.connection.execute('SELECT id FROM register_record WHERE kind=? ORDER BY rowid', (cls.__name__,)).fetchall()]

    def for_run(self, cls, run_id):
        """Select indexed ownership before decoding and validating large payloads."""
        rows = self.connection.execute("SELECT id FROM register_record WHERE kind=? AND json_extract(payload,'$.run_id') IS ? ORDER BY rowid",
                                       (cls.__name__, str(run_id) if run_id is not None else None)).fetchall()
        return [self._get(row[0], cls) for row in rows]

    def proof_references(self, value):
        """Current proof edges; revision predecessors are historical provenance.

        Historical edges remain immutable and accessible in register_reference.
        Only the declared Measurement/Review predecessor relation is excluded;
        own candidate, suite, tool, raw, code and T4 measurement proofs stay due.
        """
        for row in self.connection.execute('SELECT path,target_id FROM register_reference WHERE owner_id=?', (str(value.id),)):
            if isinstance(value, (MeasurementAttempt, CriterionReviewRevision)) and row['path'] == 'predecessor_id':
                continue
            yield UUID(row['target_id'])

    def _phase(self, value):
        if isinstance(value, StudyPhase):
            return value.id
        if hasattr(value, 'phase_id'):
            return value.phase_id
        if hasattr(value, 'run_id') and value.run_id:
            return self._get(value.run_id, Run).phase_id
        if isinstance(value, ConfigurationVersion):
            return self._get(value.configuration_id, Configuration).phase_id
        if isinstance(value, CandidateSnapshot):
            return self._get(value.run_id, Run).phase_id
        if isinstance(value, TransportAttempt):
            return self._phase(self._get(value.call_id, ModelCall))
        if isinstance(value, (TransportStateEvent, RetryEvidence)):
            return self._phase(self._get(value.transport_id, TransportAttempt))
        if isinstance(value, (TestResult, StaticProfile, FunctionalProfile)):
            return self._phase(self._get(value.measurement_id, MeasurementAttempt))
        if isinstance(value, RevisionInvalidation):
            return self._phase(self._get(value.revision_id))
        return None

    def _references(self, value, prefix=''):
        if isinstance(value, BaseModel):
            value = value.model_dump(mode='python')
        if isinstance(value, UUID):
            yield prefix, value
        elif isinstance(value, dict):
            for k, v in value.items():
                if not prefix and k == 'id':
                    continue
                yield from self._references(v, f'{prefix}.{k}' if prefix else k)
        elif isinstance(value, (tuple, list)):
            for i, v in enumerate(value):
                yield from self._references(v, f'{prefix}.{i}')

    def _put(self, value):
        # Revalidation makes nested mutable dictionaries and model_copy bypasses safe.
        value = type(value).model_validate(value.model_dump(mode='json'))
        self._validate_relations(value)
        self.connection.execute('INSERT INTO register_record VALUES (?,?,?,?,?)',
                                (str(value.id), type(value).__name__, value.code, canonical(value), digest(value)))
        for path, target in self._references(value):
            self.connection.execute('INSERT INTO register_reference VALUES (?,?,?)', (str(value.id), path, str(target)))
        self._project(value)
        if isinstance(value, (Run, ModelCall, TransportAttempt, TransportStateEvent, Event, Artifact,
                              CandidateSnapshot, MeasurementAttempt, TestResult, CriterionReviewRevision,
                              RevisionInvalidation, MetricObservation, CostEntry, TimeInterval,
                              ResourceProfile, StaticProfile, FunctionalProfile, RunStateRevision, RetryEvidence, PhaseStateRevision, AnalysisRun)):
            phase_id = self._phase(value)
            if phase_id:
                self.connection.execute('UPDATE phase_state SET substantive_revision=substantive_revision+1 WHERE phase_id=?', (str(phase_id),))
        return value

    def add(self, value):
        # Freeze/Planned/Block/Run are created only via the gated aggregate methods.
        if isinstance(value, (Freeze, Block, PlannedRun, Run, ConfigurationVersion)):
            raise GateError('Aggregat nur über version_configuration/freeze/start erzeugen')
        with self.transaction():
            return self._put(value)

    def _validate_relations(self, v):
        # Resolve all references with integrity checks, then apply semantic contracts.
        for _, ref in self._references(v):
            self._get(ref)
        if isinstance(v, StudyPhase):
            self._get(v.study_id, Study)
            if v.predecessor_id:
                p = self._get(v.predecessor_id, StudyPhase)
                if p.study_id != v.study_id:
                    raise GateError('Vorherige Phase gehört zur selben Studie')
        if isinstance(v, Configuration):
            self._get(v.phase_id, StudyPhase)
        if isinstance(v, ConfigurationVersion):
            conf = self._get(v.configuration_id, Configuration)
            if self.connection.execute('SELECT 1 FROM freeze_binding WHERE phase_id=?', (str(conf.phase_id),)).fetchone():
                raise GateError('Gefrorene Bedingungen: neue Erhebungsphase erforderlich')
            if v.parent_id:
                parent = self._get(v.parent_id, ConfigurationVersion)
                if parent.configuration_id != v.configuration_id or v.parent_hash != parent.content_hash:
                    raise GateError('Falsche Elternversion/Hash')
            self._validate_settings(v.settings, complete=False)
            if self._get(conf.phase_id, StudyPhase).purpose == 'free_test':
                if v.settings.holdout_suite_id or v.settings.reference_id:
                    raise GateError('Freie Konfiguration darf keinen Holdout auswählen')
                for _, rid in self._references(v.settings):
                    asset = self._get(rid)
                    if isinstance(asset, AssetVersion) and asset.suite_kind == 'study_holdout':
                        raise GateError('Geschütztes Asset in freier Konfiguration')
        if isinstance(v, Approval):
            phase = self._get(v.phase_id, StudyPhase)
            study = self._get(phase.study_id, Study)
            if not v.person.strip() or not v.reason.strip() or (v.schema_version == 1 and not v.evidence):
                raise GateError('Freigabe braucht Person, Grund und Belege')
            if v.synthetic_fixture and (study.data_origin != 'synthetic' or not v.person.startswith('TECHNICAL-FIXTURE:')):
                raise GateError('Synthetische Freigabe ist ausschließlich technische Fixture')
            if v.kind == 'cost' and not v.paid_calls_consent:
                raise GateError('Gesonderte explizite Kostenfreigabe fehlt')
            self._proofs(v.evidence, v.scope_hash)
        if isinstance(v, Series):
            self._get(v.freeze_id, Freeze)
            if v.order != tuple(self.fq_ids(v.freeze_id)):
                raise GateError('Einzel/Serie müssen dieselbe unveränderte Hauptliste verwenden')
        if isinstance(v, Run):
            phase = self._get(v.phase_id, StudyPhase)
            conf = self._get(v.configuration_version_id, ConfigurationVersion)
            if v.purpose != phase.purpose or self._phase(conf) != v.phase_id or v.effective_hash != conf.content_hash:
                raise GateError('Herkunft/effektive Bedingungen passen nicht')
            suite = self._get(v.suite_id, AssetVersion)
            if v.purpose == 'free_test' and (suite.suite_kind != 'development' or v.suite_id != conf.settings.development_suite_id):
                raise GateError('Freie Tests ausschließlich Development')
            if v.purpose != 'main' and v.planned_run_id is not None:
                raise GateError('Keine Umwidmung zu Hauptdaten')
            if not v.start_decision or not v.technical_evidence_ids:
                raise GateError('Bewusster Start und technische Belege erforderlich')
        if isinstance(v, Job):
            phase = self._get(v.phase_id, StudyPhase)
            if v.run_id:
                run = self._get(v.run_id, Run)
                if run.phase_id != v.phase_id:
                    raise GateError('Job in falscher Phase')
            if phase.purpose == 'free_test':
                if v.job_type not in ('free_test', 'generation', 'measurement'):
                    raise GateError('Freie Tests kein Import-/Export-/Analyseweg')
                if v.suite_id is None or self._get(v.suite_id, AssetVersion).suite_kind != 'development':
                    raise GateError('Holdout nicht in freien Jobs')
            if v.suite_id and v.run_id and v.suite_id != self._get(v.run_id, Run).suite_id:
                raise GateError('Job-Suite weicht vom Lauf ab')
        if isinstance(v, ModelCall):
            self._get(v.model_package_id, ModelPackage)
            run = self._get(v.run_id, Run)
            conf = self._get(run.configuration_version_id, ConfigurationVersion)
            if v.node == 'planner' and not conf.cell.planner or v.node == 'review' and not conf.cell.review:
                raise GateError('Deaktivierte Rolle')
            if v.model_package_id != self.resolve_call_model(conf,v.node).id:
                raise GateError('Modellpaket weicht von wirksamer Rollenbedingung ab')
            if canonical(v.parameters) != canonical(self.resolve_call_parameters(conf, v.node)):
                raise GateError('Callparameter weichen von zentral aufgelösten wirksamen Parametern ab')
            prompt = self._artifact_for(v.messages_artifact_id, v.run_id, shared=False)
            if protected_evaluation(prompt):
                raise GateError('Geschützter Evaluatorbeleg kein gesendeter Prompt')
            for aid in v.input_artifact_ids:
                a = self._artifact_for(aid, v.run_id)
                if protected_evaluation(a):
                    raise GateError('Geschützter Evaluatorbeleg kein Rolleninput')
            if v.request_hash != self._get(v.messages_artifact_id, Artifact).sha256:
                raise GateError('Request/Prompt-Hash passt nicht')
        if isinstance(v, TransportAttempt):
            if v.send_status != 'prepared':
                raise GateError('Transport zuerst prepared; Fortschritt als Journalereignis')
            if v.number == 2:
                previous = self.connection.execute('SELECT transport_id FROM transport_binding WHERE call_id=? AND number=1', (str(v.call_id),)).fetchone()
                if not previous or not v.transient_retry_evidence_id:
                    raise GateError('Retry braucht ersten Versuch und transienten Fehlerbeleg')
                state = self.connection.execute('SELECT status FROM transport_state WHERE transport_id=?', (previous[0],)).fetchone()[0]
                if state != 'failed':
                    raise GateError('Kein Retry nach unklarem/erfolgreichem Versand')
                evidence = self._get(v.transient_retry_evidence_id, RetryEvidence)
                events = [e for e in self.all(TransportStateEvent) if str(e.transport_id) == previous[0]]
                if evidence.transport_id != UUID(previous[0]) or evidence.failure_kind != 'transient_no_response' or not evidence.no_usable_response or evidence.dispatch_outcome != 'known_failed' or not evidence.reason:
                    raise GateError('Kein eindeutig belegter transienter Fehler ohne Antwort')
                if any(e.response_artifact_id or e.status in ('response_saved','validated','incorporated','outcome_unknown') for e in events):
                    raise GateError('Kein Retry nach gesicherter Antwort/Formatfehler/unklarem Ausgang')
                if not any(e.status == 'failed' and e.error_artifact_id == evidence.error_artifact_id for e in events):
                    raise GateError('Retryfehlerbeleg passt nicht zum Fehlerjournal')
        if isinstance(v, TransportStateEvent):
            call = self._get(self._get(v.transport_id, TransportAttempt).call_id, ModelCall)
            for aid in (v.response_artifact_id, v.error_artifact_id):
                if aid:
                    self._artifact_for(aid, call.run_id, shared=False)
            events = [e for e in self.all(TransportStateEvent) if e.transport_id == v.transport_id]
            saved = next((e.response_artifact_id for e in events if e.response_artifact_id), None)
            if saved and v.response_artifact_id != saved:
                raise GateError('Gesicherte Antwort niemals austauschen')
            for field in ('request_id', 'generation_id'):
                known = next((getattr(e, field) for e in events if getattr(e, field)), None)
                if known and getattr(v, field) != known:
                    raise GateError('Request-/Generationidentität unveränderlich')
            old = self.connection.execute('SELECT status,sequence FROM transport_state WHERE transport_id=?', (str(v.transport_id),)).fetchone()
            allowed = {'prepared': ('dispatching',), 'dispatching': ('response_saved', 'outcome_unknown', 'failed'),
                       'response_saved': ('validated', 'failed'), 'validated': ('incorporated',),
                       'incorporated': (), 'outcome_unknown': (), 'failed': ()}
            if not old or v.sequence != old['sequence'] + 1 or v.status not in allowed[old['status']]:
                raise GateError('Ungültiger Transportfortschritt/erneuter Versand')
            if v.status in ('response_saved', 'validated', 'incorporated') and not v.response_artifact_id:
                raise GateError('Gesicherte Antwort fehlt')
        if isinstance(v, RetryEvidence):
            previous = self._get(v.transport_id, TransportAttempt)
            call = self._get(previous.call_id, ModelCall)
            self._artifact_for(v.error_artifact_id, call.run_id, shared=False)
            if not v.reason:
                raise GateError('Retrybeleg braucht Ursache')
        if isinstance(v, CandidateSnapshot):
            self._artifact_for(v.file_manifest_id, v.run_id, shared=False)
            for aid in v.artifact_ids + v.process_artifact_ids:
                self._artifact_for(aid, v.run_id)
            if v.rejected_changes_artifact_id:
                self._artifact_for(v.rejected_changes_artifact_id, v.run_id, shared=False)
            conf = self._get(self._get(v.run_id, Run).configuration_version_id, ConfigurationVersion)
            if v.scaffold_id != conf.settings.scaffold_id:
                raise GateError('Kandidat hat falsches Gerüst')
            earlier = [s for s in self.all(CandidateSnapshot) if s.run_id == v.run_id]
            if any(s.role == 'sealed' for s in earlier):
                raise GateError('Versiegelter Kandidat unveränderlich')
            # The initial post-Migrate snapshot precedes the single Test call.
            # Its explicit empty hash is not a generated test set. Bind the first
            # generated set at post-Migrate, then preserve it through Repair/Seal.
            empty_tests = __import__('hashlib').sha256(b'').hexdigest()
            bound_tests = [s.internal_tests_hash for s in earlier if s.internal_tests_hash != empty_tests]
            if any(value != v.internal_tests_hash for value in bound_tests) or bound_tests and v.internal_tests_hash == empty_tests:
                raise GateError('Interne Tests nach Repair verändert')
            if earlier and v.internal_tests_hash != empty_tests and not bound_tests and v.role != 'post_migrate':
                raise GateError('Erste interne Tests müssen vor Repair/Seal gebunden werden')
        if isinstance(v, (MeasurementAttempt, CriterionReviewRevision)):
            self._compatibility(v.run_id, v.compatibility)
            key = v.measurement_key if isinstance(v, MeasurementAttempt) else v.criterion
            kind = 'measurement' if isinstance(v, MeasurementAttempt) else 'review'
            prev = self.connection.execute('SELECT revision_id,number FROM revision_binding WHERE run_id=? AND kind=? AND field_key=? ORDER BY number DESC LIMIT 1', (str(v.run_id), kind, key)).fetchone()
            if v.revision != (prev['number'] + 1 if prev else 1) or str(v.predecessor_id) != (prev['revision_id'] if prev else 'None'):
                raise GateError('Revision braucht unveränderliche Folge/Vorgänger')
            if isinstance(v, MeasurementAttempt):
                fixture = self._get(v.fixture_id, AssetVersion)
                if fixture.suite_kind != v.suite_kind or v.fixture_id != v.compatibility.suite_id:
                    raise GateError('Fixture aus falschem Suitebereich')
                for aid in v.raw_artifact_ids:
                    self._measurement_artifact(aid,v)
                suite = self._get(v.compatibility.suite_id, AssetVersion)
                if suite.suite_kind != v.suite_kind:
                    raise GateError('Falscher Suitekind')
                if self._get(v.run_id, Run).purpose == 'free_test' and v.suite_kind != 'development':
                    raise GateError('Holdout nicht in freien Messungen')
            else:
                settings = self._get(self._get(v.run_id, Run).configuration_version_id, ConfigurationVersion).settings
                if v.rubric_id != settings.rubric_id:
                    raise GateError('Rubrik passt nicht zur gefrorenen Bedingung')
                self._get(v.rubric_id, AssetVersion)
                if v.code_artifact_id:
                    self._artifact_for(v.code_artifact_id, v.run_id, shared=False)
                for mid in v.measurement_ids:
                    m = self._get(mid, MeasurementAttempt)
                    if m.run_id != v.run_id or m.compatibility != v.compatibility:
                        raise GateError('Review-Messbeleg inkompatibel')
        if isinstance(v, RevisionInvalidation):
            revision = self._get(v.revision_id)
            absence = isinstance(revision, Artifact) and revision.artifact_type == 'evaluation_absence_receipt' and revision.producer == 'trusted_evaluator'
            if not (isinstance(revision, (MeasurementAttempt, CriterionReviewRevision)) or absence) or not v.reason or not v.person:
                raise GateError('Invalidierung braucht Revision, Grund, Person')
        if isinstance(v, Event):
            self._get(v.run_id,Run)
            self._get(v.process_id,ProcessInstance)  # Emitter may diagnose an interval from a prior process.
            if v.interval_id and self._get(v.interval_id,TimeInterval).run_id != v.run_id:
                raise GateError('Ereignisintervall gehört zu fremdem Lauf')
        if isinstance(v, TimeInterval):
            self._get(v.run_id,Run)
            for process_id in (v.process_id,v.end_process_id):
                if process_id:
                    self._get(process_id,ProcessInstance)
            if v.connection:
                previous = self._get(v.connection.previous_interval_id,TimeInterval)
                if previous.run_id != v.run_id or previous.sequence != v.sequence-1:
                    raise GateError('Intervallverknüpfung braucht unmittelbaren Vorgänger desselben Laufs')
            old = [i for i in self.all(TimeInterval) if i.run_id == v.run_id]
            from .timing import summarize
            summarize(tuple(old + [v]), coverage_complete=False)
        if isinstance(v, CostEntry):
            if self._get(v.run_id, Run).purpose != v.operating_area:
                raise GateError('Kosten im falschen Betriebsbereich')
            if self._get(self._get(v.transport_id, TransportAttempt).call_id, ModelCall).run_id != v.run_id:
                raise GateError('Fremder Kostenversuch')
            self._get(v.price_evidence_id,Artifact)
            for aid in v.billing_evidence_ids:
                self._artifact_for(aid,v.run_id,shared=False)
            if any(e.transport_id == v.transport_id for e in self.all(CostEntry)):
                raise GateError('Abrechnung einmal je Transport; ergänzende Belege keine zweite Kostenposition')
        if isinstance(v, Artifact) and v.run_id:
            run = self._get(v.run_id, Run)
            if run.purpose == 'free_test' and v.access_scope == 'trusted_evaluator':
                raise GateError('Freies Artefakt darf kein geschützter Evaluatorbeleg sein')
            if v.call_id and self._get(v.call_id, ModelCall).run_id != run.id:
                raise GateError('Artefakt aus fremdem Call')
            if v.measurement_id and self._get(v.measurement_id, MeasurementAttempt).run_id != run.id:
                raise GateError('Artefakt aus fremder Messung')
        if isinstance(v, TestResult):
            m = self._get(v.measurement_id, MeasurementAttempt)
            fixture = self._get(v.fixture_id, AssetVersion)
            if v.fixture_id != m.fixture_id or fixture.suite_kind != m.suite_kind:
                raise GateError('Testfixture gehört nicht zur Messung/Suite')
            for aid in (v.input_artifact_id,v.raw_artifact_id):
                self._measurement_artifact(aid,m)
        if isinstance(v, StaticProfile):
            m = self._get(v.measurement_id, MeasurementAttempt)
            if v.configuration_hash != self._get(m.compatibility.tool_id,AssetVersion).manifest_hash:
                raise GateError('Statische Profilkonfiguration passt nicht zum Messinstrument')
            for aid in (v.file_manifest_id,v.diagnostics_artifact_id):
                self._measurement_artifact(aid,m)
        if isinstance(v, FunctionalProfile):
            m = self._get(v.measurement_id, MeasurementAttempt)
            for tid in v.test_result_ids:
                if self._get(tid,TestResult).measurement_id != m.id:
                    raise GateError('Fremdes Testergebnis im Funktionsprofil')
            for rid in v.review_revision_ids:
                review = self._get(rid,CriterionReviewRevision)
                if review.run_id != m.run_id or review.compatibility != m.compatibility:
                    raise GateError('Fremde/inkompatible Reviewrevision im Funktionsprofil')
        if isinstance(v, (ResourceProfile,MetricObservation)):
            phase = self._get(v.phase_id,StudyPhase)
            run = self._get(v.run_id,Run) if v.run_id else None
            if run and run.phase_id != phase.id:
                raise GateError('Profilmetrik in fremder Phase')
            if isinstance(v,ResourceProfile) and v.operating_area != (run.purpose if run else phase.purpose):
                raise GateError('Ressourcen im falschen Betriebsbereich')
            for iid in v.interval_ids:
                if self._get(iid,TimeInterval).run_id != v.run_id:
                    raise GateError('Fremdes Zeitintervall im Profil')
            if isinstance(v,ResourceProfile):
                for tid in v.transport_ids:
                    if self._get(self._get(tid,TransportAttempt).call_id,ModelCall).run_id != v.run_id:
                        raise GateError('Fremder Transport im Ressourcenprofil')
                for cid in v.cost_entry_ids:
                    cost = self._get(cid,CostEntry)
                    if cost.run_id != v.run_id or cost.operating_area != v.operating_area or cost.transport_id not in v.transport_ids:
                        raise GateError('Fremde Abrechnung im Ressourcenprofil')
            else:
                if v.measurement_id:
                    m = self._get(v.measurement_id,MeasurementAttempt)
                    if m.run_id != v.run_id:
                        raise GateError('Fremde Messung in Profilmetrik')
                    if v.configuration_hash and v.configuration_hash != self._get(m.compatibility.tool_id,AssetVersion).manifest_hash:
                        raise GateError('Metrikkonfiguration inkompatibel')
                    if v.diagnostics_artifact_id:
                        self._measurement_artifact(v.diagnostics_artifact_id,m)
                elif v.diagnostics_artifact_id:
                    self._artifact_for(v.diagnostics_artifact_id,v.run_id,shared=False)

        if isinstance(v, Backup):
            freeze = self._get(v.freeze_id, Freeze)
            if freeze.phase_id != v.phase_id or v.substantive_revision != self.revision(v.phase_id):
                raise GateError('Backup muss aktuellen fachlichen Stand referenzieren')
            active = self.connection.execute('SELECT 1 FROM transport_state t JOIN transport_binding tb ON tb.transport_id=t.transport_id JOIN call_binding cb ON cb.call_id=tb.call_id JOIN run_binding rb ON rb.run_id=cb.run_id WHERE rb.phase_id=? AND t.status=? LIMIT 1', (str(v.phase_id), 'dispatching')).fetchone()
            if v.consistent and active:
                raise GateError('Aktiver Request verhindert konsistentes Backup')
            if v.status == 'awaiting_confirmation' and not v.consistent:
                raise GateError('Inkonsistentes Backup nicht bestätigbar')
        if isinstance(v, BackupReceipt):
            backup = self._get(v.backup_id, Backup)
            if backup.status != 'awaiting_confirmation' or not backup.consistent or v.manifest_hash != backup.manifest_hash or not v.person or not v.external_medium:
                raise GateError('Receipt: Backup/Hash/Person/externer Datenträger fehlen')
            transfer = self.connection.execute('SELECT status FROM backup_request WHERE backup_id=?', (str(backup.id),)).fetchone()
            if transfer and transfer[0] == 'internal_ready':
                download=self.connection.execute("SELECT d.status FROM backup_download d JOIN backup_request b USING(job_id) WHERE b.backup_id=?",(str(backup.id),)).fetchone()
                if not download or download[0]!='confirmed':raise GateError('Receipt: ausdrückliche Ablagebestätigung zur geprüften Browser-Sicherungsdatei fehlt')
            elif transfer and transfer[0] != 'transferred':
                raise GateError('Receipt: technisch geprüfte getrennte Übertragung fehlt')
            if transfer and backup.substantive_revision != self.revision(backup.phase_id):
                raise GateError('Receipt: gesicherter Stand veraltet')
        if isinstance(v, AnalysisRun):
            if '_m7' in v.selected_inputs:
                from .analysis_store import validate_analysis_record
                validate_analysis_record(self, v)
            else:
                expected = self.analysis_inputs(v.freeze_id)
                if expected != v.selected_inputs or v.input_hash != digest(expected):
                    raise GateError('Keine freie Wahl von Analyserevisionen')
            if not v.confirmed_by or v.phase_id != self._get(v.freeze_id, Freeze).phase_id:
                raise GateError('Analysebestätigung/Phase fehlt')
        if isinstance(v, Package):
            ids = self.fq_ids(v.freeze_id)
            if tuple(ids) != v.planned_run_ids or v.phase_id != self._get(v.freeze_id, Freeze).phase_id:
                raise GateError('Studienpaket enthält genau geplante Hauptmatrix')
            for rid in v.provenance_run_ids:
                run = self._get(rid, Run)
                if run.purpose not in ('pilot', 'preparation') or self._get(run.phase_id, StudyPhase).study_id != self._get(v.phase_id, StudyPhase).study_id:
                    raise GateError('Provenienz nur konkrete getrennte Pilot-/Vorbereitungsreferenz')
            if v.predecessor_id:
                previous = self._get(v.predecessor_id, Package)
                if previous.phase_id != v.phase_id or previous.version + 1 != v.version:
                    raise GateError('Paketversion braucht unmittelbaren Vorgänger')

    def _measurement_artifact(self, aid, measurement):
        a = self._artifact_for(aid,measurement.run_id,shared=False)
        if measurement.suite_kind == 'development' and a.access_scope == 'trusted_evaluator':
            raise GateError('Development darf keine geschützten Roh-/Eingabebelege referenzieren')
        if a.measurement_id and a.measurement_id != measurement.id:
            raise GateError('Rohbeleg aus fremder Messung')
        return a

    def _artifact_for(self, aid, run_id, *, shared=True):
        a = self._get(aid, Artifact)
        if a.run_id != run_id and not (shared and a.run_id is None):
            raise GateError('Artefakt gehört zu fremdem Lauf')
        if run_id and self._get(run_id,Run).purpose == 'free_test' and a.access_scope == 'trusted_evaluator':
            raise GateError('Freier Lauf darf keinen geschützten Evaluatorbeleg referenzieren')
        return a

    def resolve_call_model(self, version, node):
        if node not in ('analyzer','planner','migrate','test','review','repair'):
            raise GateError('Unbekannte Rolle')
        cell = version.cell
        letter = cell.producer if node in ('analyzer','planner','migrate','repair') else cell.verifier
        mid = version.settings.model_a if letter == 'A' else version.settings.model_b
        return self._get(mid, ModelPackage)

    def resolve_call_parameters(self, version, node):
        model = self.resolve_call_model(version,node)
        role_parameters = version.settings.role_parameters
        if role_parameters is None or set(role_parameters) - {'all','analyzer','planner','migrate','test','review','repair'}:
            raise GateError('Zentrale Rollenparameter fehlen/ungültig')
        resolved = {**model.effective_parameters, **role_parameters.get('all',{}), **role_parameters.get(node,{})}
        if set(resolved) - set(model.supported_parameters):
            raise GateError('Nicht unterstützter Modellparameter')
        canonical(resolved)
        return resolved

    def _validate_settings(self, s, *, complete):
        typed = {'contract_id': 'contract', 'scaffold_id': 'scaffold', 'handoff_id': 'handoff', 'analysis_id': 'analysis',
                 'reference_id': 'reference', 'rubric_id': 'contract'}
        for field, kind in typed.items():
            rid = getattr(s, field)
            if rid and self._get(rid, AssetVersion).asset_type != kind:
                raise GateError(f'Assettyp {field} passt nicht')
        for mid in (s.model_a, s.model_b):
            if mid:
                self._get(mid, ModelPackage)
        for field, kind in (('development_suite_id', 'development'), ('holdout_suite_id', 'study_holdout')):
            rid = getattr(s, field)
            if rid and self._get(rid, AssetVersion).suite_kind != kind:
                raise GateError(f'Suitekind {field} passt nicht')
        for rid in s.prompt_ids:
            if self._get(rid, AssetVersion).asset_type != 'prompt':
                raise GateError('Prompttyp passt nicht')
        for rid in s.tool_ids:
            if self._get(rid, AssetVersion).asset_type != 'tool':
                raise GateError('Tooltyp passt nicht')
        for rid in s.context_ids.values():
            if self._get(rid, AssetVersion).asset_type != 'source_context':
                raise GateError('Quellpakettyp passt nicht')
        if complete:
            if s.open_fields():
                raise GateError('Offene Freezeangaben: ' + ', '.join(s.open_fields()))
            if set(s.context_ids) != {f'{m}-K{k}' for m in ('BF', 'SQL', 'UP') for k in (0, 1)}:
                raise GateError('Alle sechs Kontextpakete erforderlich')

    def _proofs(self, proofs, scope):
        for proof in proofs:
            asset = self._get(proof.asset_id, AssetVersion)
            if proof.scope_hash != scope or proof.asset_hash != asset.manifest_hash:
                raise GateError(f'Nachweis {proof.key}: Hash/Scope passen nicht')

    def _project(self, v):
        c = self.connection
        if isinstance(v, StudyPhase):
            c.execute('INSERT INTO phase_state(phase_id) VALUES (?)', (str(v.id),))
        elif isinstance(v, Freeze):
            c.execute('INSERT INTO freeze_binding VALUES (?,?,?,?)', (str(v.id), str(v.phase_id), v.scope_hash, v.freeze_hash))
        elif isinstance(v, PlannedRun):
            c.execute('INSERT INTO main_plan VALUES (?,?,?,?,?,?)', tuple(str(x) for x in (v.id, v.freeze_id, v.configuration_version_id, v.block_id, v.cell_key, v.position)))
        elif isinstance(v, Run):
            state = RunState(execution='queued')
            c.execute('INSERT INTO run_binding VALUES (?,?,?,?,?,?)', (str(v.id), str(v.phase_id), v.purpose, str(v.planned_run_id) if v.planned_run_id else None, state.execution, canonical(state)))
        elif isinstance(v, Job):
            c.execute('INSERT INTO job_state(job_id,idempotency_key,status) VALUES (?, ?, ?)', (str(v.id), v.idempotency_key, 'ready'))
        elif isinstance(v, ModelCall):
            c.execute('INSERT INTO call_binding VALUES (?,?,?,?)', (str(v.id), str(v.run_id), v.node, v.sequence))
        elif isinstance(v, TransportAttempt):
            c.execute('INSERT INTO transport_binding VALUES (?,?,?)', (str(v.id), str(v.call_id), v.number))
            c.execute('INSERT INTO transport_state(transport_id,status) VALUES (?,?)', (str(v.id), v.send_status))
        elif isinstance(v, TransportStateEvent):
            c.execute('INSERT INTO transport_event_binding VALUES (?,?,?)', (str(v.id), str(v.transport_id), v.sequence))
            c.execute('UPDATE transport_state SET status=?, sequence=? WHERE transport_id=?', (v.status, v.sequence, str(v.transport_id)))
        elif isinstance(v, Event):
            c.execute('INSERT INTO event_binding VALUES (?,?,?)', (str(v.id), str(v.run_id), v.sequence))
        elif isinstance(v, (MeasurementAttempt, CriterionReviewRevision)):
            kind = 'measurement' if isinstance(v, MeasurementAttempt) else 'review'
            key = v.measurement_key if kind == 'measurement' else v.criterion
            c.execute('INSERT INTO revision_binding VALUES (?,?,?,?,?,?)', (str(v.id), str(v.run_id), kind, key, v.revision, str(v.predecessor_id) if v.predecessor_id else None))
        elif isinstance(v, TimeInterval):
            c.execute('INSERT INTO interval_binding VALUES (?,?,?)', (str(v.id), str(v.run_id), v.sequence))

    def version_configuration(self, configuration_id, cell_key, cell, settings, parent_id=None):
        with self.transaction():
            parent = self._get(parent_id, ConfigurationVersion) if parent_id else None
            body = {'cell': cell.model_dump(mode='json'), 'settings': settings.model_dump(mode='json')}
            before = {'cell': parent.cell.model_dump(mode='json'), 'settings': parent.settings.model_dump(mode='json')} if parent else {}
            diff = _diff(before, body)
            return self._put(ConfigurationVersion(code=f'CV-{uuid4()}', configuration_id=configuration_id,
                parent_id=parent_id, parent_hash=parent.content_hash if parent else None, cell_key=cell_key,
                cell=cell, settings=settings, content_hash=digest(body), change_diff=diff))

    def central_update(self, phase_id, settings):
        versions = [v for v in self.all(ConfigurationVersion) if self._phase(v) == phase_id]
        latest = {v.configuration_id: v for v in versions}
        with self.transaction():
            result = {}
            for old in latest.values():
                body = {'cell': old.cell.model_dump(mode='json'), 'settings': settings.model_dump(mode='json')}
                before = {'cell': old.cell.model_dump(mode='json'), 'settings': old.settings.model_dump(mode='json')}
                new = self._put(ConfigurationVersion(code=f'CV-{uuid4()}', configuration_id=old.configuration_id,
                    parent_id=old.id, parent_hash=old.content_hash, cell_key=old.cell_key, cell=old.cell,
                    settings=settings, content_hash=digest(body), change_diff=_diff(before, body)))
                result[new.cell_key] = new
            return result

    def phase_state(self, phase_id):
        row = self.connection.execute('SELECT * FROM phase_state WHERE phase_id=?', (str(phase_id),)).fetchone()
        if row is None:
            raise GateError('Phase fehlt')
        return PhaseState.model_validate(dict(row))

    def set_phase_status(self, phase_id, status, *, reason):
        with self.transaction():
            if not reason:
                raise GateError('Phasenübergang braucht dokumentierte Ursache')
            old = self.phase_state(phase_id)
            allowed = {'draft': ('ready','stopped'), 'ready': ('running','paused','stopped'),
                'running': ('pause_requested','paused','completed','stopped'), 'pause_requested': ('paused','stopped'),
                'paused': ('ready','running','stopped'), 'completed': (), 'stopped': ()}
            if status not in allowed[old.status]:
                raise GateError('Unzulässiger Phasenübergang')
            new = PhaseState(phase_id=phase_id,status=status,substantive_revision=old.substantive_revision+1,reason=reason)
            previous = [x for x in self.all(PhaseStateRevision) if x.phase_id == phase_id]
            record = self._put(PhaseStateRevision(code=f'PHASESTATE-{uuid4()}',phase_id=phase_id,
                predecessor_id=previous[-1].id if previous else None,old_state=old,new_state=new,reason=reason))
            self.connection.execute('UPDATE phase_state SET status=?,reason=? WHERE phase_id=?',(status,reason,str(phase_id)))
            return record

    def revision(self, phase_id):
        row = self.connection.execute('SELECT substantive_revision FROM phase_state WHERE phase_id=?', (str(phase_id),)).fetchone()
        if row is None:
            raise GateError('Phase fehlt')
        return row[0]

    def freeze_scope(self, phase_id, version_ids, r_c, r_e, seed, freeze_id, *, resources: FreezeResources):
        versions = {k: self._get(v, ConfigurationVersion) for k, v in version_ids.items()}
        resources = FreezeResources.model_validate(resources.model_dump(mode='json'))
        if resources.consumption_evidence_id:
            consumption = self._get(resources.consumption_evidence_id, Artifact)
            if consumption.sha256 != resources.consumption_evidence_hash:
                raise GateError('Verbrauchsbeleg-/Hash passen nicht')
        elif resources.consumption_evidence_hash:
            raise GateError('Verbrauchshash ohne Beleg')
        return digest({'resources': resources.model_dump(mode='json'), 'phase_id': str(phase_id), 'versions': {k: {'id': str(v.id), 'hash': v.content_hash} for k, v in versions.items()},
                       'r_c': r_c, 'r_e': r_e, 'seed': seed, 'algorithm': ALGORITHM,
                       'matrix': matrix(freeze_id, r_c, r_e, seed), 'selection_rule': 'last_completed_valid_compatible-v1'})

    def _gates(self, f):
        scope = self.freeze_scope(f.phase_id, f.configuration_version_ids, f.r_c, f.r_e, f.seed, f.id, resources=FreezeResources(**{name: getattr(f,name) for name in FreezeResources.model_fields}))
        if scope != f.scope_hash or f.matrix != matrix(f.id, f.r_c, f.r_e, f.seed):
            raise GateError('Freeze Scope/Matrix manipuliert')
        for key, vid in f.configuration_version_ids.items():
            v = self._get(vid, ConfigurationVersion)
            if v.cell_key != key or v.cell != MAIN_CELLS[key] or self._phase(v) != f.phase_id:
                raise GateError('Unzulässige Hauptkonfiguration/Phase')
            self._validate_settings(v.settings, complete=f.schema_version == 1)
            for node in ('analyzer','planner','migrate','test','review','repair'):
                self.resolve_call_parameters(v,node)
        settings_hashes = {digest(self._get(vid, ConfigurationVersion).settings) for vid in f.configuration_version_ids.values()}
        if len(settings_hashes) != 1:
            raise GateError('Versteckte zentrale Overrides')
        self._proofs(f.evidence, scope)
        if f.schema_version == 1 and (len({p.key for p in f.evidence}) != len(f.evidence) or not set(REQUIRED_GATES) <= {p.key for p in f.evidence}):
            raise GateError('Pflicht-Gatenachweise fehlen/doppelt')
        approvals = [self._get(aid, Approval) for aid in f.approval_ids]
        for a in approvals:
            if a.phase_id != f.phase_id or a.scope_hash != scope:
                raise GateError('Freigabe in falschem Scope')
            self._proofs(a.evidence, scope)
        by_kind = {a.kind: a for a in approvals}
        if not {'technical', 'subject', 'cost'} <= set(by_kind):
            raise GateError('Technische/fachliche/Kostenfreigabe getrennt erforderlich')
        for kind, keys in (('technical', TECHNICAL_GATES), ('subject', HUMAN_GATES), ('cost', RESOURCE_GATES)):
            if f.schema_version == 1 and not set(keys) <= {p.key for p in by_kind[kind].evidence}:
                raise GateError(f'{kind}: Freigabeumfang unvollständig')
        real_models = any(not self._is_mock(self._get(vid, ConfigurationVersion)) for vid in f.configuration_version_ids.values())
        if real_models and any(a.synthetic_fixture for a in approvals):
            raise GateError('Technische Freigabefixture erlaubt keinen realen Modellzugang')
        if not by_kind['cost'].paid_calls_consent:
            raise GateError('Kostenfreigabe fehlt')
        study = self._get(self._get(f.phase_id,StudyPhase).study_id,Study)
        if study.data_origin == 'empirical' and not real_models:
            raise GateError('Empirischer Modell-Freeze darf keinen Mockzugang behaupten')
        covered_cells = set()
        shared_settings = self._get(next(iter(f.configuration_version_ids.values())),ConfigurationVersion).settings
        if real_models:
            a,b = (self._get(mid,ModelPackage) for mid in (shared_settings.model_a,shared_settings.model_b))
            if not a.exact_model_id.strip() or not b.exact_model_id.strip() or a.exact_model_id.strip() == b.exact_model_id.strip():
                raise GateError('Reale A/B benötigen zwei verschieden identifizierte Modelle')
            if any(package.endpoint.startswith('mock://') or not package.upstream.strip() or package.upstream.lower() in ('mock','synthetic') for package in (a,b)):
                raise GateError('Reale A/B benötigen echte fest bezeichnete Modell-/Upstreampakete')
        if f.schema_version == 2:
            if len(approvals) != 3 or any(a.schema_version != 2 for a in approvals):
                raise GateError('Genau drei Entscheidungen für diesen Matrixstand erforderlich')
            from .matrix_readiness import require_ready
            require_ready(self, {k:self._get(vid,ConfigurationVersion) for k,vid in f.configuration_version_ids.items()})
            return
        for pilot_id in f.pilot_run_ids:
            pilot = self._get(pilot_id,Run)
            if pilot.purpose != 'pilot' or self._get(pilot.phase_id,StudyPhase).study_id != study.id:
                raise GateError('Pilotbezug gehört nicht zur Studie/formalen Pilotphase')
            pv = self._get(pilot.configuration_version_id,ConfigurationVersion)
            if digest(pv.settings) != digest(shared_settings):
                raise GateError('Pilotbedingungen passen nicht zum Modell-/Parameter-/Asset-Freeze')
            if real_models:
                if self._is_mock(pv) or self.state(pilot.id).execution != 'terminal':
                    raise GateError('Mock/offener Pilot kein echter Modellnachweis')
                cells = {key for key,vid in f.configuration_version_ids.items() if self._get(vid,ConfigurationVersion).cell == pv.cell}
                if not cells:
                    raise GateError('Pilotzelle gehört nicht zu eingefrorenen Konfigurationen')
                calls = {call.node:call for call in self.all(ModelCall) if call.run_id == pilot.id}
                path = ['analyzer']
                if pv.cell.planner:
                    path.append('planner')
                path.extend(('migrate','test'))
                if pv.cell.review:
                    path.append('review')
                if 'repair' in calls:
                    path.append('repair')  # Optional; never fabricated to complete a pilot.
                actual_path = [call.node for call in sorted(calls.values(),key=lambda call:call.sequence)]
                required = set(path)
                completed = set()
                for node,call in calls.items():
                    package = self.resolve_call_model(pv,node)
                    if call.model_package_id != package.id or canonical(call.parameters) != canonical(self.resolve_call_parameters(pv,node)):
                        raise GateError('Pilot-Callbedingung weicht von eingefrorener Rolle ab')
                    transports = {t.id for t in self.all(TransportAttempt) if t.call_id == call.id}
                    entries = [e for e in self.all(TransportStateEvent) if e.transport_id in transports and e.status == 'incorporated']
                    for e in entries:
                        if e.actual_model.status != 'observed' or e.actual_model.value != package.exact_model_id or e.actual_provider.status != 'observed' or e.actual_provider.value != package.upstream:
                            raise GateError('Pilotantwort weicht von eingefrorenem Modell/Upstream ab')
                        if e.request_id and e.generation_id and e.response_artifact_id and e.format_status == 'valid':
                            completed.add(node)
                if actual_path == path and required <= completed:
                    covered_cells.update(cells)
        if real_models and set(f.configuration_version_ids) != covered_cells:
            raise GateError('Tatsächliche Pilot-Callbelege für zwölf eingefrorene Zellen/vollständige Rollenpfade fehlen: ' + ', '.join(sorted(set(f.configuration_version_ids)-covered_cells)))
        consumption = self._get(f.consumption_evidence_id, Artifact)
        if consumption.run_id not in f.pilot_run_ids or self._get(consumption.run_id, Run).purpose != 'pilot':
            raise GateError('Tatsächlicher Pilotverbrauch separat referenzieren')
        if self._get(self._get(consumption.run_id, Run).phase_id, StudyPhase).study_id != self._get(f.phase_id, StudyPhase).study_id:
            raise GateError('Pilotverbrauch anderer Studie')

    def freeze(self, value: Freeze, *, approvals=()):
        with self.transaction():
            value = Freeze.model_validate(value.model_dump(mode='json'))
            if self._get(value.phase_id, StudyPhase).purpose != 'main':
                raise GateError('Freeze ausschließlich Hauptphase')
            for approval in approvals:
                if approval.id not in value.approval_ids:
                    raise GateError('Freigabe gehört nicht zum konkreten Freeze')
                self._put(approval)
            self._gates(value)
            self._put(value)
            blocks = {}
            for row in value.matrix:
                bid = UUID(row['block_id'])
                if bid not in blocks:
                    blocks[bid] = self._put(Block(id=bid, code=f'BLOCK-{bid}', freeze_id=value.id, index=row['block_index'], in_b_e=row['in_b_e']))
                self._put(PlannedRun(id=UUID(row['id']), code=f'MAIN-{row["id"]}', freeze_id=value.id, block_id=bid,
                    configuration_version_id=value.configuration_version_ids[row['cell_key']], cell_key=row['cell_key'], position=row['position']))
            self.connection.execute('UPDATE phase_state SET status=?,reason=? WHERE phase_id=?', ('ready', 'Validated freeze; external backup required', str(value.phase_id)))
        return value

    def backup_status(self, freeze_id):
        f = self._get(freeze_id, Freeze)
        revision = self.revision(f.phase_id)
        backups = [b for b in self.all(Backup) if b.freeze_id == f.id]
        receipts = self.all(BackupReceipt)
        request = self.connection.execute('SELECT status,substantive_revision FROM backup_request WHERE freeze_id=? ORDER BY rowid DESC LIMIT 1', (str(f.id),)).fetchone()
        if request and request['substantive_revision'] == revision:
            if request['status'] == 'failed':
                return 'failed'
            if request['status'] in ('requested', 'creating'):
                return 'creating'
            if request['status'] == 'recovery_required':
                return 'required'
        for b in reversed(backups):
            if b.substantive_revision == revision:
                if b.status == 'failed':
                    return 'failed'
                if b.status == 'creating':
                    return 'creating'
                if b.consistent and any(r.backup_id == b.id and r.manifest_hash == b.manifest_hash for r in receipts):
                    return 'current'
                return 'awaiting_confirmation'
        return 'stale' if backups else 'required'

    def next_id(self, freeze_id):
        row = self.connection.execute('SELECT p.planned_id FROM main_plan p LEFT JOIN run_binding r ON r.planned_id=p.planned_id WHERE p.freeze_id=? AND r.run_id IS NULL ORDER BY p.position LIMIT 1', (str(freeze_id),)).fetchone()
        return UUID(row[0]) if row else None

    def start_main(self, freeze_id, planned_id, *, expected_freeze_hash, decision, idempotency_key, technical_evidence_ids):
        with self.transaction():
            f = self._get(freeze_id, Freeze)
            self._gates(f)
            if self.phase_state(f.phase_id).status not in ('ready','running'):
                raise GateError('Phase gestoppt/pausiert/abgeschlossen; bewusste zulässige Fortsetzung erforderlich')
            if expected_freeze_hash != f.freeze_hash:
                raise GateError('Freezehash verändert')
            existing = self.connection.execute('SELECT job_id FROM job_state WHERE idempotency_key=?', (idempotency_key,)).fetchone()
            if existing:
                job = self._get(existing[0], Job)
                old_run = self._get(job.run_id, Run) if job.run_id else None
                if not old_run or old_run.planned_run_id != planned_id:
                    raise GateError('Idempotenzschlüssel gehört zu anderem Auftrag')
                return old_run, job
            if self.next_id(f.id) != planned_id:
                raise GateError('Nur nächste eingefrorene Haupt-ID startbar')
            if self.backup_status(f.id) != 'current':
                raise GateError('Aktuelle bestätigte externe Sicherung fehlt')
            p = self._get(planned_id, PlannedRun)
            version = self._get(p.configuration_version_id, ConfigurationVersion)
            run = self._put(Run(code=f'RUN-{uuid4()}', phase_id=f.phase_id, purpose='main', planned_run_id=p.id,
                configuration_version_id=version.id, effective_hash=version.content_hash, suite_id=version.settings.holdout_suite_id,
                technical_evidence_ids=technical_evidence_ids, start_decision=decision, started_at=datetime.now(timezone.utc), actual_position=p.position))
            job = self._put(Job(code=f'JOB-{uuid4()}', phase_id=f.phase_id, run_id=run.id, job_type='generation',
                                idempotency_key=idempotency_key, suite_id=run.suite_id))
            self.connection.execute('UPDATE phase_state SET status=?,reason=? WHERE phase_id=?', ('running', decision, str(f.phase_id)))
            return run, job

    def start_other(self, phase_id, version_id, *, decision, technical_evidence_ids, idempotency_key):
        with self.transaction():
            phase = self._get(phase_id, StudyPhase)
            if self.phase_state(phase.id).status in ('stopped','completed','paused','pause_requested'):
                raise GateError('Phase nicht bereit für neuen Start')
            if phase.purpose == 'main':
                raise GateError('Hauptlauf nur über eingefrorene Matrix')
            old = self.connection.execute('SELECT job_id FROM job_state WHERE idempotency_key=?', (idempotency_key,)).fetchone()
            if old:
                job = self._get(old[0], Job)
                run = self._get(job.run_id, Run)
                if run.phase_id != phase.id or run.configuration_version_id != version_id:
                    raise GateError('Idempotenzschlüssel gehört zu anderem Auftrag')
                return run, job
            v = self._get(version_id, ConfigurationVersion)
            self._other_gates(phase, v)
            suite_id = v.settings.development_suite_id if phase.purpose in ('free_test', 'demo') else v.settings.holdout_suite_id
            run = self._put(Run(code=f'{phase.purpose.upper()}-{uuid4()}', phase_id=phase.id, purpose=phase.purpose,
                configuration_version_id=v.id, effective_hash=v.content_hash, suite_id=suite_id,
                technical_evidence_ids=technical_evidence_ids, start_decision=decision, started_at=datetime.now(timezone.utc)))
            job = self._put(Job(code=f'JOB-{uuid4()}', phase_id=phase.id, run_id=run.id,
                job_type='free_test' if phase.purpose == 'free_test' else 'generation', idempotency_key=idempotency_key, suite_id=suite_id))
            return run, job

    def configuration_scope(self, phase_id, version_id):
        v = self._get(version_id, ConfigurationVersion)
        return digest({'phase_id': str(phase_id), 'configuration_version_id': str(v.id), 'effective_hash': v.content_hash})

    def _is_mock(self, v):
        required = {v.settings.model_a if k == 'A' else v.settings.model_b for k in (v.cell.producer, v.cell.verifier)}
        if None in required:
            raise GateError('Wirksame Modelle fehlen')
        return all(self._get(mid, ModelPackage).endpoint.startswith('mock://') for mid in required)

    def _other_gates(self, phase, v):
        study = self._get(phase.study_id, Study)
        mock = self._is_mock(v)
        if mock and study.data_origin == 'synthetic':
            return
        scope = self.configuration_scope(phase.id, v.id)
        approvals = [a for a in self.all(Approval) if a.phase_id == phase.id and a.scope_hash == scope]
        by_kind = {a.kind: a for a in approvals if not a.synthetic_fixture}
        if not mock and ('cost' not in by_kind or not by_kind['cost'].paid_calls_consent):
            raise GateError('Realer Modellzugang braucht passende ausdrückliche Kostenfreigabe')
        if phase.purpose == 'pilot':
            if not {'technical', 'subject'} <= set(by_kind):
                raise GateError('Formaler Pilot braucht Instrument-/Fachabnahme')
            if not set(TECHNICAL_GATES) <= {p.key for p in by_kind['technical'].evidence}:
                raise GateError('Pilot: technische Nachweise unvollständig')
            if not {'contract_review', 'instrument_review'} <= {p.key for p in by_kind['subject'].evidence}:
                raise GateError('Pilot: fachliche Nachweise unvollständig')
        for a in by_kind.values():
            self._proofs(a.evidence, scope)

    def state(self, run_id):
        row = self.connection.execute('SELECT state_json FROM run_binding WHERE run_id=?', (str(run_id),)).fetchone()
        if not row:
            raise GateError('Lauf fehlt')
        return RunState.model_validate_json(row[0])

    def set_state(self, run_id, state: RunState, *, reason="Repository status transition"):
        with self.transaction():
            return self.set_state_in_transaction(run_id,state,reason=reason)

    def set_state_in_transaction(self, run_id, state: RunState, *, reason):
        if not self.connection.in_transaction:
            raise RuntimeError('Statusübergang braucht Registertransaktion')
        state = RunState.model_validate(state.model_dump(mode='json'))
        old = self.state(run_id)
        allowed = {'queued': ('preflight', 'terminal'), 'preflight': ('running', 'terminal'), 'running': ('terminal',), 'terminal': ()}
        if state.execution != old.execution and state.execution not in allowed[old.execution]:
            raise GateError('Unzulässiger Ausführungsübergang')
        if old.execution == 'terminal' and (state.terminal_cause != old.terminal_cause or state.ended_at != old.ended_at):
            raise GateError('Terminalen Verlauf nicht umschreiben')
        if old.seal == 'sealed' and state.candidate_id != old.candidate_id:
            raise GateError('Kandidatenwechsel nach Seal')
        if state.candidate_id:
            candidate = self._get(state.candidate_id, CandidateSnapshot)
            if candidate.run_id != run_id or state.seal == 'sealed' and candidate.role != 'sealed':
                raise GateError('Falscher versiegelter Kandidat')
        self.connection.execute('UPDATE run_binding SET execution=?,state_json=? WHERE run_id=?', (state.execution, canonical(state), str(run_id)))
        previous = [x for x in self.all(RunStateRevision) if x.run_id == run_id]
        self._put(RunStateRevision(code=f'STATE-{uuid4()}', run_id=run_id,
            predecessor_id=previous[-1].id if previous else None, old_state=old, new_state=state, reason=reason))

    def claim(self, job_id, worker):
        with self.transaction():
            return self.claim_in_transaction(job_id, worker)

    def claim_in_transaction(self, job_id, worker):
        """Claim inside an existing atomic trusted ownership operation."""
        if not self.connection.in_transaction:
            raise RuntimeError('Claim braucht Registertransaktion')
        if not worker:
            raise GateError('Workerkennung erforderlich')
        now = datetime.now(timezone.utc).isoformat()
        return self.connection.execute('UPDATE job_state SET status=?,worker=?,claimed_at=?,heartbeat_at=? WHERE job_id=? AND status=?',
            ('running', worker, now, now, str(job_id), 'ready')).rowcount == 1

    def compatible_evaluation_tool(self, run_id, tool_id):
        """Frozen original tools or an explicit same-seal technical correction."""
        run = self._get(run_id, Run)
        conf = self._get(run.configuration_version_id, ConfigurationVersion)
        if tool_id in conf.settings.tool_ids:
            return True
        static = self.connection.execute('SELECT * FROM static_tool_correction WHERE run_id=? AND new_tool_id=?', (str(run_id), str(tool_id))).fetchone()
        state = self.state(run_id)
        if static and state.seal == 'sealed':
            candidate = self._get(state.candidate_id, CandidateSnapshot)
            if static['candidate_id'] == str(candidate.id) and static['candidate_hash'] == candidate.tree_hash:
                return True
        binding = self.connection.execute('SELECT * FROM evaluation_tool_correction WHERE run_id=? AND new_tool_id=?', (str(run_id), str(tool_id))).fetchone()
        state = self.state(run_id)
        if not binding or state.seal != 'sealed':
            return False
        candidate = self._get(state.candidate_id, CandidateSnapshot)
        return (binding['candidate_id'] == str(candidate.id) and binding['candidate_hash'] == candidate.tree_hash
                and binding['contract_id'] == str(conf.settings.contract_id) and binding['suite_id'] == str(run.suite_id)
                and binding['rubric_id'] == str(conf.settings.rubric_id))

    def _compatibility(self, run_id, comp):
        run = self._get(run_id, Run)
        state = self.state(run_id)
        candidate = self._get(comp.candidate_id, CandidateSnapshot)
        conf = self._get(run.configuration_version_id, ConfigurationVersion)
        if state.seal != 'sealed' or state.candidate_id != comp.candidate_id or candidate.tree_hash != comp.candidate_hash or candidate.run_id != run_id:
            raise GateError('Messung/Review nur passender versiegelter Kandidat')
        if comp.phase_id != run.phase_id or comp.contract_id != conf.settings.contract_id or comp.suite_id != run.suite_id or not self.compatible_evaluation_tool(run_id, comp.tool_id):
            raise GateError('Falscher Vertrag/Suite/Tool/Phase')

    def valid(self, revision_id):
        value = self._get(revision_id)
        if any(i.revision_id == revision_id for i in self.all(RevisionInvalidation)):
            return False
        if isinstance(value, Artifact) and value.artifact_type == 'evaluation_absence_receipt':
            from .analysis_absence import receipt_binding
            try:
                receipt_binding(self, value, main_only=False)
                return True
            except (ValueError, KeyError, TypeError):
                return False
        if not isinstance(value, (MeasurementAttempt, CriterionReviewRevision)) or value.completion != 'completed':
            return False
        if isinstance(value, CriterionReviewRevision):
            if value.criterion in ('T2','T3','T4') and value.reviewer_origin != 'human':
                return False
            return all(self.valid(mid) for mid in value.measurement_ids)
        return True

    def select_revision(self, run_id, kind, key, compatibility):
        rows = self.connection.execute('SELECT revision_id FROM revision_binding WHERE run_id=? AND kind=? AND field_key=? ORDER BY number DESC', (str(run_id), kind, key)).fetchall()
        for row in rows:
            value = self._get(row[0])
            if value.compatibility == compatibility and self.valid(value.id):
                return value
        return None

    def fq_ids(self, freeze_id):
        f = self._get(freeze_id, Freeze)
        # Verify immutable planning, retain never-started IDs, never append free/pilot.
        if f.matrix != matrix(f.id, f.r_c, f.r_e, f.seed):
            raise GateError('Ungültige Matrix')
        return [UUID(row['id']) for row in f.matrix]

    def analysis_inputs(self, freeze_id):
        f = self._get(freeze_id, Freeze)
        output = {}
        for pid in self.fq_ids(f.id):
            row = self.connection.execute('SELECT run_id FROM run_binding WHERE planned_id=?', (str(pid),)).fetchone()
            if not row:
                output[str(pid)] = {'run_id': None, 'missing': 'not_started', 'measurements': {}, 'reviews': {}}
                continue
            run = self._get(row[0], Run)
            state = self.state(run.id)
            selected = {'run_id': str(run.id), 'missing': None, 'measurements': {}, 'reviews': {}}
            if state.seal != 'sealed':
                selected['missing'] = 'no_compatible_sealed_candidate'
                if state.seal == 'no_candidate':
                    from .analysis_absence import select_absence
                    selected['measurements']['candidate_absence'] = select_absence(self, run)
            else:
                candidate = self._get(state.candidate_id, CandidateSnapshot)
                v = self._get(run.configuration_version_id, ConfigurationVersion)
                for kind, cls, attr in (('measurement', MeasurementAttempt, 'measurement_key'), ('review', CriterionReviewRevision, 'criterion')):
                    revisions = self.for_run(cls, run.id)
                    keys = {getattr(x, attr) for x in revisions}
                    if kind == 'measurement':
                        keys |= {'functional_R', 'static_DLS'}
                    if kind == 'review':
                        keys |= {'T1', 'T2', 'T3', 'T4', 'T5'}
                    for key in sorted(keys):
                        # Tool compatibility is part of the frozen configuration, not result-based selection.
                        compatible = [x for x in revisions if getattr(x, attr) == key
                            and x.compatibility.candidate_id == candidate.id and x.compatibility.candidate_hash == candidate.tree_hash
                            and x.compatibility.phase_id == run.phase_id and x.compatibility.contract_id == v.settings.contract_id
                            and x.compatibility.suite_id == run.suite_id and self.compatible_evaluation_tool(run.id, x.compatibility.tool_id) and self.valid(x.id)
                            and (kind != 'review' or x.rubric_id == v.settings.rubric_id)]
                        chosen = max(compatible, key=lambda x: x.revision) if compatible else None
                        selected['measurements' if kind == 'measurement' else 'reviews'][key] = {'revision_id': str(chosen.id) if chosen else None,
                            'valid_at_confirmation': bool(chosen), 'missing': None if chosen else 'no_valid_completed_compatible_revision'}
            output[str(pid)] = selected
        for selected in output.values():
            for group, keys in (('measurements', ('functional_R', 'static_DLS')), ('reviews', ('T1', 'T2', 'T3', 'T4', 'T5'))):
                for key in keys:
                    selected[group].setdefault(key, {'revision_id': None, 'valid_at_confirmation': False,
                        'missing': selected['missing'] or 'no_valid_completed_compatible_revision'})
        return output


def _diff(old, new, prefix=''):
    result = {}
    for key in sorted(set(old) | set(new)):
        path = f'{prefix}.{key}' if prefix else key
        if isinstance(old.get(key), dict) and isinstance(new.get(key), dict):
            result.update(_diff(old[key], new[key], path))
        elif old.get(key) != new.get(key):
            result[path] = {'before': old.get(key), 'after': new.get(key)}
    return result

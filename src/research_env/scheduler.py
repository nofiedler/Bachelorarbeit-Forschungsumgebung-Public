"""Persistent serial command service, independent of HTTP request lifetimes.

Starts always use Register gates. Series wait after each MainID, and only a
fresh conscious action can select the next existing frozen ID. No autopilot.
"""
from datetime import datetime, timezone
from decimal import Decimal
import json
import sqlite3
from uuid import UUID, uuid4

from .domain import (Artifact, CandidateSnapshot, Event, Freeze, IntervalConnection, Job, Run, Series,
                     TimeInterval, ProcessInstance, canonical, digest)
from .locks import worker_lock, write_barrier
from .sandbox_runtime import PREFIX
from .pipeline import PhaseIdentityDrift
from .register import GateError


class CompletionPersistenceError(OSError):
    """Technical completion evidence is missing; conscious recovery required."""


def mark_worker_recovery(register):
    """One durable boot transition; never consume a previous process's intent."""
    with register.transaction():
        register.connection.execute("UPDATE sandbox_execution SET status='recovery_required' WHERE status IN ('allocated','starting','running')")
        register.connection.execute("UPDATE pipeline_binding SET status='recovery_required',reason='Prozessneustart: frische bewusste Fortsetzung erforderlich' WHERE status != 'completed' AND NOT (status='ready' AND process_id IS NULL AND EXISTS (SELECT 1 FROM job_state j WHERE j.job_id=pipeline_binding.job_id AND j.status='ready' AND j.worker IS NULL))")
        register.connection.execute("UPDATE pipeline_runner SET status='recovery_required' WHERE status='running'")


class Scheduler:
    def __init__(self, pipeline):
        self.pipeline, self.register = pipeline, pipeline.register
        self.journal, self.store = pipeline.journal, pipeline.store
        self.process = self.journal.process
        self.pipeline.before_call = self._ensure_active

    def _ensure_active(self, run_id):
        saved = self.pipeline.binding(run_id)['clock_json']
        clock = json.loads(saved) if saved else None
        if not clock or clock['kind'] != 'active':
            self._clock(run_id, 'active', gap=bool(clock and clock['start']['process_id'] != str(self.process.id)))

    def boot(self):
        # Called under the single worker processlock. Boot neither resumes a
        # call nor touches owned/foreign Docker resources. Explicit action only.
        mark_worker_recovery(self.register)

    def start_main(self, freeze_id, *, decision, idempotency_key, technical_evidence_ids, series_id=None):
        freeze = self.register.get(freeze_id, Freeze)
        if series_id:
            series = self.register.get(series_id, Series)
            if series.freeze_id != freeze.id or series.mode != 'series':
                raise GateError('Serie gehört zu anderer eingefrorener Liste')
        # Idempotent duplicate returns the exact old planned ID instead of
        # accidentally choosing the next ID after the first click bound a run.
        old = self.register.connection.execute('SELECT job_id FROM job_state WHERE idempotency_key=?', (idempotency_key,)).fetchone()
        planned = self.register.get(self.register.get(old[0], Job).run_id, Run).planned_run_id if old else self.register.next_id(freeze.id)
        if planned is None:
            raise GateError('Eingefrorene Hauptliste vollständig gestartet')
        run, job = self.register.start_main(freeze.id, planned, expected_freeze_hash=freeze.freeze_hash,
                decision=decision, idempotency_key=idempotency_key, technical_evidence_ids=technical_evidence_ids)
        if series_id:
            with self.register.transaction():
                self.register.connection.execute("UPDATE pipeline_series SET status='waiting',reason=? WHERE id=?", (decision, str(series_id)))
        return run, job

    def start_series(self, freeze_id, *, decision):
        if not decision.strip():
            raise GateError('Bewusster gesonderter Serienstart erforderlich')
        value = self.register.add(Series(code=f'SERIES-{uuid4()}', freeze_id=freeze_id, mode='series', order=tuple(self.register.fq_ids(freeze_id))))
        with self.register.transaction():
            self.register.connection.execute('INSERT INTO pipeline_series VALUES (?,?,?)', (str(value.id), 'waiting', decision))
        return value

    def start_other(self, phase_id, version_id, **kwargs):
        return self.register.start_other(phase_id, version_id, **kwargs)

    def request_pause(self, run_id, *, reason):
        self._request(run_id, 'pause_requested', reason)

    def request_abort(self, run_id, *, reason, immediate=False):
        self._request(run_id, 'abort_requested', reason)
        run = self.register.get(run_id, Run)
        diagnostics = {'immediate': immediate, 'reason': reason, 'call_journals': [], 'sandbox': [], 'missing': []}
        graph = self.pipeline.graph.get_state({'configurable': {'thread_id': str(run.id)}})
        candidates = [x for x in self.register.all(__import__('research_env.domain', fromlist=['CandidateSnapshot']).CandidateSnapshot) if x.run_id == run.id]
        diagnostics['graph_next'] = list(graph.next)
        diagnostics['latest_candidate_id'] = str(candidates[-1].id) if candidates else None
        rows = self.register.connection.execute('SELECT call_id FROM call_binding WHERE run_id=? ORDER BY sequence', (str(run_id),)).fetchall()
        for row in rows:
            diagnostics['call_journals'].append(self.journal.stop(row[0], reason=reason) if immediate else self.journal.diagnostic(row[0]))
        if hasattr(self.pipeline.runner, 'sandbox'):
            sandbox = self.pipeline.runner.sandbox
            for handle in tuple(sandbox.handles.values()):
                if handle.labels.get(PREFIX + 'run') == str(run.id):
                    if not immediate:
                        diagnostics['sandbox'].append(handle.diagnose())
                    handle.abort(reason=reason, immediate=immediate)
            recorded = self.register.connection.execute("SELECT id,status,body FROM sandbox_execution WHERE run_id=? AND status IN ('allocated','starting','running','recovery_required')", (str(run.id),)).fetchall()
            for row in recorded:
                if row['id'] not in sandbox.handles:
                    diagnostics['sandbox'].append({'execution_id': row['id'], 'status': row['status'], 'recorded': json.loads(row['body']),
                        'diagnostic_missing': 'Nach Prozessneustart kein Handle; bewusste own Recovery erforderlich'})
            if immediate and any(row['id'] not in sandbox.handles for row in recorded):
                diagnostics['recorded_stop_ids'] = sandbox.stop_recorded(run.id, reason=reason)
        if immediate:
            diagnostics['missing'].append('Sofortstopp: Diagnose und aktive Endgrenze möglicherweise unvollständig')
        a = self.store.json(diagnostics, run_id=run.id, artifact_type='pipeline_stop_diagnosis')
        if immediate and self.pipeline._effect(run.id, 'abort') is None:
            snapshot = self.pipeline.graph.get_state({'configurable': {'thread_id': str(run.id)}})
            state = dict(snapshot.values) if snapshot.values else {'run_id': str(run.id), 'config_hash': run.effective_hash, 'statuses': {}, 'refs': {}}
            unknown = any(x['attempts'][-1]['status'] == 'outcome_unknown' for x in diagnostics['call_journals'])
            state['statuses'] = {**state['statuses'], 'cause': 'outcome_unknown' if unknown else 'interrupted'}
            self.pipeline._save_effect(state, 'abort')
        self.pipeline.event(run.id, 'abort_decision', {'reason': reason, 'immediate': immediate,'target_process_id':self.pipeline.binding(run.id)['process_id']}, (a.id,))
        return a

    def _request(self, run_id, status, reason):
        if not reason.strip():
            raise GateError('Manuelle Entscheidung braucht Grund')
        with self.register.transaction():
            row = self.pipeline.binding(run_id)
            if row['status'] == 'completed':
                raise GateError('Terminaler Auftrag nicht steuerbar')
            self.register.connection.execute('UPDATE pipeline_binding SET status=?,reason=? WHERE run_id=?', (status, reason, str(run_id)))
        self.pipeline.event(UUID(str(run_id)), status, {'reason': reason})

    def _stamp(self):
        return {'process_id': str(self.process.id), 'monotonic': str(Decimal(str(self.journal.monotonic()))),
                'utc': datetime.now(timezone.utc).isoformat()}

    def _interval(self, run_id, kind, start, end, evidence, *, relation=None):
        old = sorted([x for x in self.register.all(TimeInterval) if x.run_id == run_id], key=lambda x: x.sequence)
        connection = IntervalConnection(previous_interval_id=old[-1].id, relation=relation or ('excluded_gap' if old[-1].kind == 'excluded_gap' else 'contiguous' if start and old[-1].process_id == UUID(start['process_id']) else 'unknown_active'), evidence_ids=(evidence.id,)) if old else None
        return self.register._put(TimeInterval(code=f'PIPE-TIME-{uuid4()}', run_id=run_id, sequence=len(old)+1,
            kind=kind, connection=connection, process_id=UUID(start['process_id']) if start else None,
            end_process_id=UUID(end['process_id']) if end else None,
            monotonic_start=Decimal(start['monotonic']) if start else None,
            monotonic_end=Decimal(end['monotonic']) if end else None,
            started_at=datetime.fromisoformat(start['utc']) if start else None,
            ended_at=datetime.fromisoformat(end['utc']) if end else None, evidence_ids=(evidence.id,),
            reason='Gemessener Pipelineanteil; unbekannte Prozessgrenze separat' if start else 'Prozessgrenze ohne monotone Subtraktion'))

    def _clock(self, run_id, next_kind, *, gap=False):
        row = self.pipeline.binding(run_id)
        clock = json.loads(row['clock_json']) if row['clock_json'] else None
        stamp = self._stamp()
        a = self.store.json({'previous': clock, 'transition': next_kind, 'observed': stamp, 'gap': gap}, run_id=run_id, artifact_type='pipeline_time_boundary')
        with self.register.transaction():
            if clock:
                if clock['start']['process_id'] == str(self.process.id) and not gap:
                    self._interval(run_id, clock['kind'], clock['start'], stamp, a)
                else:
                    self._interval(run_id, 'excluded_gap' if clock['kind'] == 'pause' else 'unknown_active', None, None, a,
                                   relation='excluded_gap' if clock['kind'] == 'pause' else 'unknown_active')
            new = {'kind': next_kind, 'start': stamp} if next_kind else None
            self.register.connection.execute('UPDATE pipeline_binding SET clock_json=? WHERE run_id=?', (canonical(new) if new else None, str(run_id)))

    def resume(self, run_id, *, reason):
        if not reason.strip():
            raise GateError('Explizite Nutzerfortsetzung mit Grund erforderlich')
        row = self.pipeline.binding(run_id)
        if row['status'] not in ('paused', 'recovery_required'):
            raise GateError('Auftrag nicht in bewusster Fortsetzung')
        with self.register.transaction():
            self.register.connection.execute("UPDATE pipeline_binding SET status='resume_requested',continuation=?,reason=? WHERE run_id=?", (reason, reason, str(run_id)))
        self.pipeline.event(UUID(str(run_id)), 'resume_requested', {'reason': reason})

    def _recover_abort_effect(self,run,snapshot,reason):
        if self.pipeline._effect(run.id,'abort') is not None:return
        row=self.register.connection.execute("SELECT id FROM register_record WHERE kind='Event' AND json_extract(payload,'$.run_id')=? AND json_extract(payload,'$.event_type')='abort_decision' ORDER BY CAST(json_extract(payload,'$.sequence') AS INTEGER) DESC LIMIT 1",(str(run.id),)).fetchone()
        if not row:return
        event=self.register.get(row['id'],Event)
        if event.run_id!=run.id or not event.evidence_ids:
            raise GateError('Gespeicherte Abortentscheidung ohne eigenen Diagnosebeleg')
        for aid in event.evidence_ids:
            proof=self.register.get(aid,Artifact)
            if proof.run_id!=run.id or proof.artifact_type!='pipeline_stop_diagnosis':
                raise GateError('Abortdiagnose gehört nicht zu diesem Lauf')
            self.store.read(proof.id)
        process=self.register.get(event.process_id,ProcessInstance)
        stopped=[self.journal.stop(row[0],reason=reason) for row in self.register.connection.execute('SELECT call_id FROM call_binding WHERE run_id=? ORDER BY sequence',(str(run.id),)).fetchall()]
        state=dict(snapshot.values) if snapshot.values else {'run_id':str(run.id),'config_hash':run.effective_hash,'statuses':{},'refs':{}}
        unknown=any(value['attempts'] and value['attempts'][-1]['status']=='outcome_unknown' for value in stopped)
        state['statuses']={**state['statuses'],'cause':'outcome_unknown' if unknown else 'interrupted'}
        self.pipeline._save_effect(state,'abort')
        self.pipeline.event(run.id,'explicit_stored_abort_recovery',{'abort_decision_id':str(event.id),'abort_process_id':str(process.id),'reason':reason},event.evidence_ids)

    def _resume(self, run_id, reason):
        row = self.pipeline.binding(run_id)
        run = self.register.get(run_id, Run)
        snapshot = self.pipeline.graph.get_state({'configurable': {'thread_id': str(run_id)}})
        terminal = self.register.state(run_id)
        if terminal.execution == 'terminal':
            # Seal and its measured active end are already durable. Recover only
            # missing completion evidence, without replay or reopening UF5.
            try:
                previous = snapshot.values or self.pipeline._effect(run.id, 'abort')
                if previous:
                    self.pipeline._checked_state(previous)
                if terminal.candidate_id:
                    sealed = self.register.get(terminal.candidate_id, CandidateSnapshot)
                    if sealed.run_id != run.id or sealed.role != 'sealed':
                        raise GateError('Terminaler Kandidat kein eigener Seal')
                    self.store.read(sealed.file_manifest_id)
                if row['clock_json'] is not None:
                    # A failed end-boundary write cannot turn storage downtime
                    # into measured active work after Seal. Preserve it as missing.
                    self._clock(run.id, None, gap=True)
                self.pipeline.event(run.id, 'explicit_completion_recovery', {'reason': reason, 'graph_next': list(snapshot.next)})
            except (OSError, ValueError, sqlite3.Error) as failure:
                self._completion_failed(run_id, failure)
            self._complete(run_id)
            return
        try:
            self._recover_abort_effect(run,snapshot,reason)
            if self.pipeline._effect(run.id,'abort') is not None and hasattr(self.pipeline.runner,'sandbox'):
                self.pipeline.runner.sandbox.recover(decision='stop_owned',run_id=run.id)
        except (ValueError,OSError,sqlite3.Error) as error:
            self._pipeline_failed(run.id,error)
            return
        self.pipeline.preflight(run.id)
        if self.pipeline._effect(run.id, 'abort') is not None:
            self._clock(run.id, 'active', gap=row['process_id'] != str(self.process.id))
            with self.register.transaction():
                self.register.connection.execute("UPDATE pipeline_binding SET status='abort_requested',process_id=?,continuation=?,reason=? WHERE run_id=?", (str(self.process.id), reason, reason, str(run.id)))
            self.pipeline.event(run.id, 'explicit_abort_recovery', {'reason': reason})
            return
        # Check every recorded call before selecting any graph work. Unknown
        # dispatch is marked unknown by the adapter and can never be sent again.
        calls = self.register.connection.execute('SELECT call_id FROM call_binding WHERE run_id=? ORDER BY sequence', (str(run_id),)).fetchall()
        if calls:
            self._clock(run.id, 'active', gap=row['process_id'] != str(self.process.id))
        self._activate_run(run_id)
        with self.register.transaction():
            self.register.connection.execute("UPDATE pipeline_binding SET status='running',process_id=?,continuation=?,reason=? WHERE run_id=?",
                (str(self.process.id), reason, reason, str(run.id)))
        for call in calls:
            self.pipeline.active_call = UUID(call[0])
            try:
                self.journal.execute(self.pipeline.adapter, UUID(call[0]), continuation=reason)
            finally:
                self.pipeline.active_call = None
            diagnosis = self.journal.diagnostic(call[0])
            if diagnosis['attempts'][-1]['status'] in ('outcome_unknown', 'failed') or self.pipeline.binding(run_id)['status'] in ('pause_requested', 'abort_requested'):
                # Preserve/Seal available state via the pending node; never clear
                # journal failure to force a call. Only the pending node reenters.
                break
        self.pipeline.event(run.id, 'explicit_resume', {'reason': reason})

    def _activate_run(self, run_id):
        old = self.register.state(run_id)
        if old.execution == 'queued':
            self.register.set_state(run_id, old.model_copy(update={'execution': 'preflight'}), reason='Konkreter Vorflight')
            old = self.register.state(run_id)
        if old.execution == 'preflight':
            self.register.set_state(run_id, old.model_copy(update={'execution': 'running'}), reason='Vorflight bestanden')
        run = self.register.get(run_id, Run)
        status = self.register.phase_state(run.phase_id).status
        if status == 'draft':
            self.register.set_phase_status(run.phase_id, 'ready', reason='Technisch gebundener Start')
            status = 'ready'
        if status == 'ready':
            self.register.set_phase_status(run.phase_id, 'running', reason='Bewusster einzelner Start')

    def _preflight_failure(self,run_id,error):
        try:
            self._persist_preflight_failure(run_id,error)
        except (ValueError,OSError,sqlite3.Error) as failure:
            self._pipeline_failed(run_id,failure)

    def _persist_preflight_failure(self, run_id, error):
        run = self.register.get(run_id, Run)
        status = self.register.phase_state(run.phase_id).status
        drift = isinstance(error, PhaseIdentityDrift)
        if status == 'draft' and not drift:
            self.register.set_phase_status(run.phase_id, 'ready', reason='Vorflight konnte nicht abgeschlossen werden')
            status = 'ready'
        target = 'stopped' if drift else 'paused'
        if status not in ('stopped', 'completed', target):
            self.register.set_phase_status(run.phase_id, target, reason='Vorflightfehler: ' + str(error))
        binding = self.pipeline.binding(run.id)
        if binding['clock_json']:
            clock = json.loads(binding['clock_json'])
            if clock['kind'] == 'pause' or binding['process_id'] != str(self.process.id):
                self._clock(run.id, 'active', gap=binding['process_id'] != str(self.process.id))
        snapshot = self.pipeline.graph.get_state({'configurable': {'thread_id': str(run_id)}})
        abort = self.pipeline._effect(run.id, 'abort')
        state = abort or (dict(snapshot.values) if snapshot.values else {'run_id': str(run_id), 'config_hash': run.effective_hash, 'statuses': {}, 'refs': {}})
        state['statuses'] = {**state['statuses'], 'cause': state['statuses']['cause'] if abort else 'technical_failure'}
        self.pipeline.event(run.id, 'preflight_failure', {'reason': str(error), 'identity_drift': drift, 'stored_abort_preserved': bool(abort)})
        self.pipeline._seal(state)
        self._complete(run_id)

    def tick(self):
        # Same lock as the process worker and backupworker; direct native callers
        # cannot race the running service, and the SQL Claim is still atomic.
        with worker_lock(self.register.settings):
            rows = self.register.connection.execute("SELECT * FROM pipeline_binding WHERE status IN ('ready','running','pause_requested','abort_requested','resume_requested') ORDER BY rowid LIMIT 1").fetchall()
            if not rows:
                return None
            row = dict(rows[0])
            run_id = UUID(row['run_id'])
            if row['status'] == 'resume_requested':
                try:
                    self._resume(run_id, row['continuation'])
                except CompletionPersistenceError:
                    return run_id
                except (ValueError, OSError) as error:
                    self._preflight_failure(run_id, error)
                    return run_id
                if self.pipeline.binding(run_id)['status'] in ('pause_requested', 'abort_requested', 'recovery_required', 'completed'):
                    return run_id
            elif row['status'] == 'ready':
                with self.register.transaction():
                    if not self.register.claim_in_transaction(UUID(row['job_id']), str(self.process.id)):
                        raise GateError('Auftrag bereits von anderem Worker beansprucht')
                    self.register.connection.execute('UPDATE pipeline_binding SET process_id=? WHERE run_id=?', (str(self.process.id), str(run_id)))
                self.pipeline.crash('claim:after_commit')
                try:
                    self.pipeline.preflight(run_id)
                except (ValueError, OSError) as error:
                    self._preflight_failure(run_id, error)
                    return run_id
                self._activate_run(run_id)
                with self.register.transaction():
                    self.register.connection.execute("UPDATE pipeline_binding SET status='running',process_id=? WHERE run_id=?", (str(self.process.id), str(run_id)))
            elif row['status'] == 'pause_requested':
                self._clock(run_id, 'pause')
                with self.register.transaction():
                    self.register.connection.execute("UPDATE pipeline_binding SET status='paused' WHERE run_id=?", (str(run_id),))
                self.pipeline.event(run_id, 'actual_pause', {'reason': row['reason']})
                return run_id
            elif row['status'] == 'abort_requested':
                try:
                    clock = json.loads(row['clock_json']) if row['clock_json'] else None
                    if clock and clock['kind'] == 'pause':
                        self._clock(run_id, 'active')
                    snapshot = self.pipeline.graph.get_state({'configurable': {'thread_id': str(run_id)}})
                    state = dict(snapshot.values) if snapshot.values else {'run_id': str(run_id), 'config_hash': self.register.get(run_id, Run).effective_hash, 'statuses': {}, 'refs': {}}
                    unknown = any(self.journal.diagnostic(x[0])['attempts'][-1]['status'] == 'outcome_unknown' for x in self.register.connection.execute('SELECT call_id FROM call_binding WHERE run_id=?', (str(run_id),)).fetchall())
                    state['statuses'] = {**state['statuses'], 'cause': 'outcome_unknown' if unknown else 'interrupted'}
                    if self.pipeline._effect(run_id, 'abort') is None:
                        self.pipeline._save_effect(state, 'abort')
                    self.pipeline._seal(state)
                    self._complete(run_id)
                except (ValueError, OSError, sqlite3.Error) as error:
                    self._pipeline_failed(run_id,error)
                return run_id
            try:
                snapshot = self.pipeline.step(run_id, continuation=row['continuation'])
            except (ValueError, OSError, sqlite3.Error) as error:
                self._pipeline_failed(run_id,error)
                return run_id
            if not snapshot.next:
                self._complete(run_id)
            return run_id

    def _pipeline_failed(self,run_id,error):
        # Failed Seal/checkpoint/storage work needs a fresh conscious action.
        with self.register.transaction():
            self.register.connection.execute("UPDATE pipeline_binding SET status='recovery_required',reason=? WHERE run_id=?", (str(error), str(run_id)))
        self.pipeline.event(run_id, 'pipeline_recovery_required', {'reason': str(error),'failure_type':type(error).__name__})

    def _complete(self, run_id):
        try:
            return self._persist_completion(run_id)
        except (OSError, ValueError, sqlite3.Error) as failure:
            self._completion_failed(run_id, failure)

    def _completion_failed(self, run_id, failure):
        reason = f'Technischer Abschlussbeleg fehlt: {type(failure).__name__}: {failure}'
        try:
            with self.register.transaction():
                self.register.connection.execute("UPDATE pipeline_binding SET status='recovery_required',reason=? WHERE run_id=?", (reason, str(run_id)))
            self.pipeline.event(run_id, 'completion_recovery_required', {'reason': reason, 'failure_type': type(failure).__name__})
        except (OSError, ValueError, sqlite3.Error) as diagnostic_failure:
            print(f'{reason}; Registerdiagnose konnte nicht gespeichert werden: {type(diagnostic_failure).__name__}: {diagnostic_failure}', flush=True)
        raise CompletionPersistenceError(reason) from failure

    def _persist_completion(self, run_id):
        if self.pipeline.binding(run_id)['clock_json'] is not None:
            self._clock(run_id, None)
        with write_barrier(self.register.settings, exclusive=True):
            report = self.pipeline.summary(run_id)
            # These two substantive records each advance the phase once. Hold
            # other writers out while preparing CAS, then append exactly this
            # fixed set atomically and verify the actual final derivation. CAS
            # work must precede the SQL transaction; a rollback retains bytes.
            completion_types = (Artifact, Event)
            report['costs']['phase_revision'] += len(completion_types)
            report['costs']['evidence_revision'] = digest({key: report['costs'][key] for key in ('run_id', 'phase_revision', 'evidence')})
            data = canonical(report).encode()
            self.pipeline.crash('completion:before_cas')
            sha = self.store.put_object(data)
            self.pipeline.crash('completion:after_cas')
            artifact = Artifact(code=f'ART-{uuid4()}', sha256=sha, byte_count=len(data),
                run_id=run_id, artifact_type='pipeline_summary', producer='worker',
                original_name='raw', mime_type='application/json')
            sequence = 1 + max((x.sequence for x in self.register.all(Event) if x.run_id == run_id), default=0)
            event = Event(code=f'PIPE-EVENT-{uuid4()}', run_id=run_id, sequence=sequence,
                event_type='pipeline_completed', process_id=self.process.id, interval_id=None,
                happened_at=datetime.now(timezone.utc), evidence_ids=(artifact.id,),
                details={'manual_next_start_required': True})
            records = (artifact, event)
            with self.register.transaction():
                if tuple(type(value) for value in records) != completion_types:
                    raise RuntimeError('Abschlussrecordmenge verändert')
                for value in records:
                    self.register._put(value)
                self.register.connection.execute("UPDATE pipeline_binding SET status='completed',reason='Terminaler Stand; Warten auf bewussten nächsten Start' WHERE run_id=?", (str(run_id),))
                self.register.connection.execute("UPDATE job_state SET status='completed' WHERE job_id=?", (self.pipeline.binding(run_id)['job_id'],))
                if self.journal.costs_in_transaction(run_id) != report['costs']:
                    raise RuntimeError('Abschlussableitung passt nicht zu finalen Registerrevisionen')
                self.pipeline.crash('completion:before_commit')

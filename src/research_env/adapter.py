"""Persistent call journal: dispatch precedes I/O, recovery never regenerates.

#9 owns graph/pause/phase orchestration. This component exposes durable status,
stop reasons, evidence and active time portions; it never invents F/T judgments.
"""
from .numeric import deterministic
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import time
from uuid import UUID, uuid4

import httpx

from .artifacts import ArtifactStore, IntegrityError
from .domain import (protected_evaluation, Artifact, ConfigurationVersion, CostEntry, ModelCall, ModelPackage,
                     NonnegativeObservation, ProcessInstance, RetryEvidence, Run,
                     StudyPhase, TransportAttempt, TransportStateEvent, canonical, digest)
from .locks import file_lock, write_barrier
from .providers import (KnownTransient, WireResponse, decode,
                        decimal_value, export_json, safe_bytes, safe_headers, sum_exact)
from .register import GateError


def utc():
    return datetime.now(timezone.utc).isoformat()


def missing(unit='text'):
    return {'status': 'unresolved', 'unit': unit, 'reason': 'No complete provider evidence'}


def observed(value, unit='text', source='adapter raw evidence'):
    return {'status': 'observed', 'value': value, 'unit': unit, 'source': source}


def usage_observations(data):
    data = data if isinstance(data, dict) else {}
    prompt = data.get('prompt_tokens_details')
    prompt = prompt if isinstance(prompt,dict) else {}
    completion = data.get('completion_tokens_details')
    completion = completion if isinstance(completion,dict) else {}
    fields = {'input': data.get('prompt_tokens'), 'output': data.get('completion_tokens'),
              'cache_read': prompt.get('cached_tokens'),
              'cache_write': prompt.get('cache_write_tokens'),
              'reasoning': completion.get('reasoning_tokens'),
              'total': data.get('total_tokens')}
    result = {}
    for key, value in fields.items():
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            result[key] = observed(value, 'tokens')
        else:
            result[key] = missing('tokens')
    return result


class CallJournal:
    def __init__(self, register, *, monotonic=time.monotonic, sleep=time.sleep, software_commit='unknown', platform_description='not collected'):
        self.register = register
        self.store = ArtifactStore(register.settings, register)
        self.monotonic, self.sleep = monotonic, sleep
        self._secret = None
        self.process = register.add(ProcessInstance(code=f'PROC-{uuid4()}',
                              started_at=datetime.now(timezone.utc), worker='adapter', software_commit=software_commit, platform=platform_description, clock_description='time.monotonic; process-local; no cross-process subtraction'))

    @classmethod
    def cost_view(cls, register):
        """Read-only current billing view; no process, model or dispatch created."""
        view = cls.__new__(cls)
        view.register = register
        view.store = ArtifactStore(register.settings, register)
        return view

    @property
    def db(self):
        return self.register.connection

    def _artifact(self, data, run_id, kind, *, call_id=None):
        if safe_bytes(data, self._secret)[1]:
            raise GateError('Secret verhindert Artefaktspeicherung')
        return self.store.store(data, run_id=run_id, call_id=call_id, artifact_type=kind,
                                producer='adapter', original_name=kind, access_scope='trusted_register')

    def _json(self, value, run_id, kind, *, call_id=None):
        return self._artifact(canonical(export_json(value)).encode(), run_id, kind, call_id=call_id)

    def _row(self, table, key, value):
        row = self.db.execute(f'SELECT * FROM {table} WHERE {key}=?', (str(value),)).fetchone()
        if not row:
            raise GateError('Adapterjournal fehlt')
        body = json.loads(row['body'])
        if digest(body) != row['sha256']:
            raise IntegrityError('Adapterjournal beschädigt')
        return dict(row), body

    def _attempt(self, tid):
        return self._row('adapter_attempt', 'transport_id', tid)[1]

    def _save_attempt(self, tid, body):
        self.db.execute('UPDATE adapter_attempt SET body=?,sha256=? WHERE transport_id=?',
                        (canonical(body), digest(body), str(tid)))

    def preview(self, adapter, run_id, basis):
        self._secret = adapter._key
        run = self.register.get(run_id, Run)
        conf = self.register.get(run.configuration_version_id, ConfigurationVersion)
        packages = {str(mid): self.register.get(mid, ModelPackage).model_dump(mode='json')
                    for mid in (conf.settings.model_a, conf.settings.model_b) if mid}
        estimate = adapter.estimate_expected_resources(basis)
        result = {'schema': 'paid-preview-v1', 'run_id': str(run.id), 'phase_id': str(run.phase_id),
                  'purpose': run.purpose, 'configuration_id': str(conf.id), 'effective_hash': conf.content_hash,
                  'model_packages': packages, 'estimate': estimate,
                  'pilot_consumption': basis.get('pilot_consumption'), 'basis': basis,
                  'manual_financial_review': True, 'synthetic': adapter.synthetic or adapter.offline,
                  'warning': 'Estimate is not a limit or guarantee; missing usage/costs remain open'}
        return self._json(result, run.id, 'start_preview')

    def record_start_order(self, run_id, preview_id, *, person, decision, roles,
                           paid_consent=False, synthetic_fixture=False):
        """Call only following the conscious start action. No UI/human approval is inferred."""
        run = self.register.get(run_id, Run)
        conf = self.register.get(run.configuration_version_id, ConfigurationVersion)
        preview = json.loads(self.store.read(preview_id))
        artifact = self.register.get(preview_id, Artifact)
        if artifact.run_id != run.id or preview.get('schema') != 'paid-preview-v1' or preview.get('effective_hash') != run.effective_hash:
            raise GateError('Startvorschau gehört nicht zur wirksamen Konfiguration')
        if not person.strip() or not decision.strip() or not roles or set(roles) - {'analyzer','planner','migrate','test','review','repair'}:
            raise GateError('Person, Entscheidung und konkrete Rollen erforderlich')
        if synthetic_fixture and not person.startswith('TECHNICAL-FIXTURE:'):
            raise GateError('Synthetischer Startauftrag klar kennzeichnen')
        if not preview['synthetic'] and (synthetic_fixture or not paid_consent):
            raise GateError('Bewusster bezahlter Startauftrag erforderlich')
        body = {'id': str(uuid4()), 'run_id': str(run.id), 'phase_id': str(run.phase_id),
                'configuration_id': str(conf.id), 'effective_hash': conf.content_hash,
                'purpose': run.purpose, 'person': person, 'happened_at': utc(),
                'decision': decision, 'roles': sorted(set(roles)), 'paid_consent': paid_consent,
                'synthetic_fixture': synthetic_fixture, 'preview_id': str(preview_id)}
        if safe_bytes(canonical(body).encode())[1]:
            raise GateError('Secret im Startauftrag')
        saved = self._json(body, run.id, 'start_order')
        with self.register.transaction():
            self.db.execute('INSERT INTO adapter_start_order VALUES (?,?,?,?,?)',
                            (body['id'], str(run.id), str(saved.id), canonical(body), digest(body)))
        return body['id']

    def _order(self, order_id, run, node, adapter):
        row, body = self._row('adapter_start_order', 'id', order_id)
        if json.loads(self.store.read(row['artifact_id'])) != body:
            raise IntegrityError('Startauftragbeleg beschädigt')
        if any(body.get(k) != v for k, v in {'run_id':str(run.id), 'phase_id':str(run.phase_id),
                    'configuration_id':str(run.configuration_version_id), 'effective_hash':run.effective_hash,
                    'purpose':run.purpose}.items()) or node not in body['roles']:
            raise GateError('Startauftrag fehlt/veraltet/fremder Geltungsbereich')
        preview = json.loads(self.store.read(body['preview_id']))
        if preview['effective_hash'] != run.effective_hash or preview['run_id'] != str(run.id):
            raise GateError('Startvorschau veraltet')
        if not adapter.synthetic:
            if not body['paid_consent'] or body['synthetic_fixture'] and not adapter.offline:
                raise GateError('Gültiger bewusster bezahlter Start fehlt')
            if not adapter._key:
                raise GateError('Worker-Modellschlüssel fehlt')
        return body

    def _endpoint_evidence(self, package):
        evidence = None
        for aid in package.metadata_evidence_ids:
            data = self.store.read(aid)
            try:
                candidate = json.loads(data)
            except (ValueError, UnicodeError):
                continue
            if isinstance(candidate, dict) and candidate.get('schema') == 'endpoint-v1':
                if evidence is not None:
                    raise GateError('Mehrdeutiger Endpointbeleg')
                evidence = candidate
        return evidence

    def prepare(self, adapter, run_id, node, *, order_id, messages, input_artifact_ids=(),
                allowed_paths=(), crash=None):
        run = self.register.get(run_id, Run)
        conf = self.register.get(run.configuration_version_id, ConfigurationVersion)
        package = self.register.resolve_call_model(conf, node)
        parameters = self.register.resolve_call_parameters(conf, node)
        self._secret = adapter._key
        self._order(order_id, run, node, adapter)
        self._run_barrier(run.id, node=node)
        data = adapter.build_request(package, messages, parameters, self._endpoint_evidence(package))
        for aid in input_artifact_ids:
            a = self.register.get(aid, Artifact)
            if protected_evaluation(a) or a.run_id not in (None, run.id):
                raise GateError('Fremder/geschützter Rolleninput')
            self.store.read(aid)
        existing = self.db.execute('SELECT call_id FROM call_binding WHERE run_id=? AND node=?', (str(run.id), node)).fetchone()
        if existing:
            call = self.register.get(existing[0], ModelCall)
            row, _ = self._row('adapter_call', 'call_id', call.id)
            if row['order_id'] != str(order_id) or self.store.read(call.messages_artifact_id) != data or tuple(input_artifact_ids) != call.input_artifact_ids or tuple(allowed_paths) != call.allowed_paths:
                raise GateError('Logischer Call bereits mit anderen Eingaben vorbereitet')
            return call
        request = self._artifact(data, run.id, 'request')
        with self.register.transaction():
            number = self.db.execute('SELECT COUNT(*) FROM call_binding WHERE run_id=?', (str(run.id),)).fetchone()[0] + 1
            call = self.register._put(ModelCall(code=f'CALL-{uuid4()}', run_id=run.id, node=node, sequence=number,
                model_package_id=package.id, request_hash=request.sha256, messages_artifact_id=request.id,
                parameters=parameters, input_artifact_ids=tuple(input_artifact_ids), allowed_paths=tuple(allowed_paths), tools=()))
            body = {'call_id':str(call.id), 'order_id':str(order_id), 'created_process':str(self.process.id),
                    'request_artifact_id':str(request.id), 'request_hash':request.sha256}
            self.db.execute('INSERT INTO adapter_call VALUES (?,?,?,?,?,?,?)',
                            (str(call.id), str(order_id), str(request.id), request.sha256, str(self.process.id), canonical(body), digest(body)))
            self._new_attempt(call, 1)
        if crash:
            crash('prepared')
        return call

    def _new_attempt(self, call, number, retry_id=None):
        transport = self.register._put(TransportAttempt(code=f'TRANSPORT-{uuid4()}', call_id=call.id,
            number=number, send_status='prepared', request_id=None, generation_id=None,
            sent_at=None, ended_at=None, actual_model=missing(), actual_provider=missing(),
            response_artifact_id=None, error_artifact_id=None, format_status='pending', usage={},
            billing_status='unresolved', transient_retry_evidence_id=retry_id))
        body = {'transport_id':str(transport.id), 'request_hash':call.request_hash,
                'created_process':str(self.process.id), 'activity_at':utc(), 'raw_id':None,
                'envelope_id':None, 'parsed_id':None, 'retry_id':str(retry_id) if retry_id else None,
                'retry_wait':None, 'interrupted_retry_waits':[], 'recovery_process':None, 'dispatch':None, 'completed':None, 'stop_reason':None}
        self.db.execute('INSERT INTO adapter_attempt VALUES (?,?,?)',
                        (str(transport.id), canonical(body), digest(body)))
        return transport

    def _latest(self, call_id):
        row = self.db.execute('SELECT transport_id FROM transport_binding WHERE call_id=? ORDER BY number DESC LIMIT 1', (str(call_id),)).fetchone()
        if not row:
            raise GateError('Transportversuch fehlt')
        return self.register.get(row[0], TransportAttempt)

    def _state(self, tid):
        return self.db.execute('SELECT status FROM transport_state WHERE transport_id=?', (str(tid),)).fetchone()[0]

    def _event(self, transport, status, *, raw=None, error=None, parsed=None):
        previous = self.db.execute('SELECT sequence FROM transport_state WHERE transport_id=?', (str(transport.id),)).fetchone()[0]
        parsed = parsed or {}
        generation_id = parsed.get('generation_id')
        request_id = parsed.get('request_id')
        events = [e for e in self.register.all(TransportStateEvent) if e.transport_id == transport.id]
        if events:
            generation_id = generation_id or events[-1].generation_id
            request_id = request_id or events[-1].request_id
        return self.register._put(TransportStateEvent(code=f'TEVENT-{uuid4()}', transport_id=transport.id,
            sequence=previous+1, status=status, request_id=request_id, generation_id=generation_id,
            response_artifact_id=raw, error_artifact_id=error,
            actual_model=observed(parsed['model']) if parsed.get('model') else missing(),
            actual_provider=observed(parsed['provider']) if parsed.get('provider') else missing(),
            usage=usage_observations(parsed.get('native_usage')), billing_status='unresolved',
            format_status='valid' if status in ('validated','incorporated') else 'invalid' if status=='failed' and raw else 'pending'))

    def _run_barrier(self, run_id, *, node=None):
        for row in self.db.execute('SELECT call_id FROM call_binding WHERE run_id=?',(str(run_id),)):
            call = self.register.get(row[0],ModelCall)
            if call.node == node:
                continue
            transport = self._latest(call.id)
            state = self._state(transport.id)
            body = self._attempt(transport.id)
            if state in ('dispatching','outcome_unknown','failed') or body['stop_reason']:
                raise GateError('Vorheriger Rollenaufruf ungeklärt/gestoppt; kein weiterer Versand')

    def _check_send(self, adapter, call, *, inference=True):
        row, body = self._row('adapter_call', 'call_id', call.id)
        run = self.register.get(call.run_id, Run)
        conf = self.register.get(run.configuration_version_id, ConfigurationVersion)
        self._secret = adapter._key
        if inference:
            self._run_barrier(run.id,node=call.node)
        if inference and self.register.state(run.id).execution == 'terminal':
            raise GateError('Terminaler Lauf nicht sendbar')
        phase_status = self.register.phase_state(run.phase_id).status
        if inference and (phase_status in ('paused','stopped','completed') or phase_status=='pause_requested' and self._latest(call.id).number==1):
            raise GateError('Phase nicht sendbar')
        if conf.content_hash != run.effective_hash:
            raise IntegrityError('Konfigurationshash beschädigt')
        self._order(row['order_id'], run, call.node, adapter)
        package = self.register.resolve_call_model(conf, call.node)
        parameters = self.register.resolve_call_parameters(conf, call.node)
        if call.model_package_id != package.id or canonical(call.parameters) != canonical(parameters):
            raise GateError('Callidentität/Parameter stimmen nicht')
        request = self.store.read(call.messages_artifact_id)
        if hashlib.sha256(request).hexdigest() != call.request_hash or row['request_hash'] != call.request_hash or body['request_hash'] != call.request_hash:
            raise IntegrityError('Requesthash beschädigt')
        parsed = decode(request)
        rebuilt = adapter.build_request(package, parsed['messages'], parameters, self._endpoint_evidence(package))
        if rebuilt != request:
            raise IntegrityError('Requestvertrag/Parameter beschädigt')
        for aid in call.input_artifact_ids:
            self.store.read(aid)
        return request, package

    def _timing_start(self):
        return {'process_id':str(self.process.id), 'utc_start':utc(), 'monotonic_start':str(Decimal(str(self.monotonic())))}

    def _timing_end(self, start):
        return {**start, 'utc_end':utc(), 'monotonic_end':str(Decimal(str(self.monotonic())))}

    def execute(self, adapter, call_id, *, continuation=None, crash=None):
        """One logical call. Node reentry reads evidence; crash recovery needs a conscious reason."""
        call = self.register.get(call_id, ModelCall)
        with file_lock(self.register.settings.control / f'.adapter-{call.run_id}.lock', exclusive=True, blocking=False):
            self._row('adapter_call', 'call_id', call.id)
            transport = self._latest(call.id)
            state = self._state(transport.id)
            body = self._attempt(transport.id)
            if state == 'dispatching':
                self._unknown(transport, 'orphaned_dispatch')
                return self.diagnostic(call.id)
            if state in ('outcome_unknown','failed','incorporated'):
                return self.diagnostic(call.id)
            restarted = body['created_process'] != str(self.process.id)
            if restarted and not continuation:
                raise GateError('Bewusste Nutzerfortsetzung mit Grund erforderlich')
            if continuation:
                saved = self._json({'action':'continue', 'reason':continuation, 'process':str(self.process.id), 'at':utc()}, call.run_id, 'recovery_action', call_id=call.id)
                with self.register.transaction():
                    body['recovery_process'] = str(self.process.id)
                    body['recovery_artifact_id'] = str(saved.id)
                    self._save_attempt(transport.id,body)
            if state == 'validated':
                return self.diagnostic(call.id)
            if state == 'response_saved':
                self._validate(adapter, call, transport)
                return self.diagnostic(call.id)
            for _ in range(2):
                transport = self._latest(call.id)
                body = self._attempt(transport.id)
                # No DB transaction is open while waiting or performing network I/O.
                request, _ = self._check_send(adapter, call)
                if transport.number == 2:
                    timing = body['retry_wait']
                    if timing and 'monotonic_end' not in timing:
                        body['interrupted_retry_waits'].append(timing)
                        body['retry_wait'] = None
                    if body['retry_wait'] is None:
                        conf = self.register.get(self.register.get(call.run_id, Run).configuration_version_id, ConfigurationVersion)
                        delay = conf.settings.retry_interval_seconds
                        if delay is None:
                            raise GateError('Konfiguriertes festes Retryintervall fehlt')
                        body['retry_wait'] = self._timing_start()
                        with self.register.transaction():
                            self._save_attempt(transport.id, body)
                        if crash:
                            crash('retry_wait_started')
                        self.sleep(float(delay))
                        body['retry_wait'] = self._timing_end(body['retry_wait'])
                        with self.register.transaction():
                            self._save_attempt(transport.id, body)
                        request, _ = self._check_send(adapter, call)
                if crash:
                    crash('before_send')
                with self.register.transaction():
                    if self._state(transport.id) != 'prepared':
                        raise GateError('Journal verhindert erneuten Versand')
                    # Recheck the current run/phase/other-call gates atomically with
                    # the durable claim. Local CAS reads are allowed; no network I/O.
                    request, _ = self._check_send(adapter, call)
                    body = self._attempt(transport.id)
                    if body.get('stop_reason'):
                        raise GateError('Manueller Stopp verhindert Versand')
                    body['dispatch'] = self._timing_start()
                    body['activity_at'] = utc()
                    self._event(transport, 'dispatching')
                    self._save_attempt(transport.id, body)
                if crash:
                    crash('retry_after_dispatch' if transport.number==2 else 'after_dispatch')
                try:
                    response = adapter._send(request)
                except (httpx.ConnectError, KnownTransient) as error:
                    if isinstance(error, KnownTransient) and not adapter.synthetic:
                        self._unknown(transport, 'untrusted_transient_claim')
                        break
                    self._failure(transport, 'transient_connection_failure', known_transient=True)
                    if transport.number == 1:
                        self._prepare_retry(call, transport)
                        continue
                    break
                except Exception:
                    # Do not serialize exception messages/requests/headers; those may contain the key.
                    self._unknown(transport, 'transport_outcome_unknown')
                    break
                retryable = self._save_response(adapter, call, transport, response)
                if crash:
                    crash('after_raw')
                if retryable and transport.number == 1:
                    self._prepare_retry(call, transport)
                    continue
                if self._state(transport.id) == 'response_saved':
                    self._validate(adapter, call, transport)
                break
            return self.diagnostic(call.id)

    def _failure(self, transport, kind, *, known_transient=False):
        call = self.register.get(transport.call_id, ModelCall)
        error = self._json({'kind':kind, 'known_failed':known_transient, 'at':utc()}, call.run_id, 'transport_error', call_id=call.id)
        with self.register.transaction():
            if self._state(transport.id) != 'dispatching':
                return
            body = self._attempt(transport.id)
            body['completed'] = self._timing_end(body['dispatch'])
            body['stop_reason'] = kind
            body['activity_at'] = utc()
            self._event(transport, 'failed', error=error.id)
            if known_transient:
                evidence = self.register._put(RetryEvidence(code=f'RETRY-{uuid4()}', transport_id=transport.id,
                    error_artifact_id=error.id, failure_kind='transient_no_response', no_usable_response=True,
                    dispatch_outcome='known_failed', reason=kind))
                body['retry_id'] = str(evidence.id)
            self._save_attempt(transport.id, body)

    def _prepare_retry(self, call, transport):
        with self.register.transaction():
            if self._latest(call.id).id != transport.id or transport.number != 1:
                raise GateError('Höchstens ein identischer Retry')
            body = self._attempt(transport.id)
            if not body['retry_id']:
                raise GateError('Belegter transienter Fehler fehlt')
            self._new_attempt(call, 2, UUID(body['retry_id']))

    def _save_response(self, adapter, call, transport, response):
        clean, body_secret = safe_bytes(response.body, adapter._key)
        headers, header_redactions = safe_headers(response.headers,adapter._key)
        secret_found = body_secret or bool(header_redactions)
        raw = self._artifact(clean, call.run_id, 'response_bytes', call_id=call.id)
        body = self._attempt(transport.id)
        completed = self._timing_end(body['dispatch'])
        envelope = self._json({'status':response.status, 'headers':headers, 'raw_id':str(raw.id),
                    'timing':completed, 'received_at':utc(), 'redacted':secret_found,
                    'body_redacted':body_secret, 'header_redactions':header_redactions,
                    'redaction_reason':'secret_exposure_redacted' if secret_found else None,
                    'original_sha256':hashlib.sha256(response.body).hexdigest()}, call.run_id, 'response_envelope', call_id=call.id)
        retryable = False
        native = {}
        try:
            native = decode(clean)
            if not isinstance(native, dict):
                native = {}
            err = native.get('error')
            retryable = (response.status == 429 and isinstance(err, dict) and err.get('code') == 429
                         and set(native) == {'error'} and not secret_found)
        except (ValueError, UnicodeError):
            pass
        with self.register.transaction():
            body = self._attempt(transport.id)
            body.update(raw_id=str(raw.id), envelope_id=str(envelope.id), completed=completed, activity_at=utc())
            self._save_attempt(transport.id, body)
            if self._state(transport.id) != 'dispatching':
                # A manual stop may race a late response. Preserve evidence, never undo the stop.
                return False
            if retryable:
                self._event(transport, 'failed', error=envelope.id)
                evidence = self.register._put(RetryEvidence(code=f'RETRY-{uuid4()}', transport_id=transport.id,
                    error_artifact_id=envelope.id, failure_kind='transient_no_response', no_usable_response=True,
                    dispatch_outcome='known_failed', reason='Complete HTTP429 rejection, no generation/output/usage'))
                body['retry_id'] = str(evidence.id)
                body['stop_reason'] = 'rate_limit'
                self._save_attempt(transport.id, body)
            else:
                p = {'generation_id':native.get('id') if isinstance(native.get('id'),str) else None,
                     'request_id':headers.get('x-request-id'),
                     'model':native.get('model') if isinstance(native.get('model'),str) else None,
                     'provider':native.get('provider') if isinstance(native.get('provider'),str) else None,
                     'native_usage':export_json(native.get('usage'))}
                self._event(transport, 'response_saved', raw=raw.id, parsed=p)
                if secret_found:
                    body['stop_reason'] = 'secret_exposure_redacted'
                    self._save_attempt(transport.id, body)
        return retryable

    def _validate(self, adapter, call, transport):
        body = self._attempt(transport.id)
        envelope = json.loads(self.store.read(body['envelope_id']))
        raw = self.store.read(body['raw_id'])
        package = self.register.get(call.model_package_id, ModelPackage)
        reason = body['stop_reason']
        parsed = None
        if not reason:
            try:
                if envelope['status'] != 200:
                    reason = 'provider_limit_or_error' if envelope['status'] in (400,413,422) else 'provider_error'
                else:
                    parsed = adapter.parse_response(WireResponse(200, raw, envelope['headers']), package)
                    parsed['request_id'] = envelope['headers'].get('x-request-id')
                    endpoint = self._endpoint_evidence(package)
                    expected = endpoint.get('expected_fingerprint') if endpoint else None
                    if expected and parsed.get('fingerprint') and expected != parsed['fingerprint']:
                        raise GateError('reported_identity_drift')
                    if parsed.get('finish_reason') == 'length':
                        # Provider truncation is technical missing output, even
                        # when the remaining text happens to be valid role JSON.
                        reason = 'provider_output_limit'
            except Exception as error:
                reason = 'reported_identity_drift' if isinstance(error, GateError) and error.args == ('reported_identity_drift',) else 'response_format_error'
        if parsed and not reason:
            saved = self._json(parsed, call.run_id, 'parsed_response', call_id=call.id)
            with self.register.transaction():
                body = self._attempt(transport.id)
                body['parsed_id'] = str(saved.id)
                self._event(transport, 'validated', raw=UUID(body['raw_id']), parsed=parsed)
                self._save_attempt(transport.id, body)
        else:
            error = self._json({'kind':reason, 'at':utc()}, call.run_id, 'validation_error', call_id=call.id)
            with self.register.transaction():
                body = self._attempt(transport.id)
                body['stop_reason'] = reason
                self._event(transport, 'failed', raw=UUID(body['raw_id']), error=error.id)
                self._save_attempt(transport.id, body)
        self._book_cost(transport)

    def _unknown(self, transport, reason):
        call = self.register.get(transport.call_id, ModelCall)
        error = self._json({'kind':reason, 'at':utc(), 'inference_may_still_run':True}, call.run_id, 'unknown_dispatch', call_id=call.id)
        with self.register.transaction():
            if self._state(transport.id) == 'dispatching':
                body = self._attempt(transport.id)
                body['stop_reason'] = reason
                body['activity_at'] = utc()
                self._event(transport, 'outcome_unknown', error=error.id)
                self._save_attempt(transport.id, body)

    def stop(self, call_id, *, reason='manual immediate stop'):
        reason = safe_bytes((reason or 'manual immediate stop').encode(),self._secret)[0].decode(errors='replace')
        call = self.register.get(call_id, ModelCall)
        self._json({'action':'manual_stop', 'reason':reason, 'at':utc()}, call.run_id, 'manual_stop', call_id=call.id)
        transport = self._latest(call.id)
        if self._state(transport.id) == 'dispatching':
            self._unknown(transport, 'manual_stop_unknown_inference')
        else:
            with self.register.transaction():
                body = self._attempt(transport.id)
                body['stop_reason'] = 'manual_stop'
                self._save_attempt(transport.id, body)
        return self.diagnostic(call.id)

    def incorporate(self, call_id, *, output_validator, crash=None):
        """Deterministic #9 handoff. Validation is supplied by the frozen role schema.

No filesystem/graph side effect runs inside this API. #9 uses parsed_id as its
idempotent input key and records its own deterministic snapshot/checkpoint.
"""
        call = self.register.get(call_id, ModelCall)
        with file_lock(self.register.settings.control / f'.adapter-{call.run_id}.lock', exclusive=True, blocking=False):
            transport = self._latest(call.id)
            body = self._attempt(transport.id)
            if self._state(transport.id) == 'incorporated':
                return json.loads(self.store.read(body['parsed_id']))
            if body['created_process'] != str(self.process.id) and body.get('recovery_process') != str(self.process.id):
                raise GateError('Bewusste Nutzerfortsetzung vor Übernahme erforderlich')
            if self._state(transport.id) != 'validated' or body['stop_reason']:
                raise GateError('Kein gültiger übernehmbarer Output')
            parsed = json.loads(self.store.read(body['parsed_id']))
            try:
                output_validator(parsed['content'])
            except Exception:
                error = self._json({'kind':'role_format_or_candidate_rejection', 'at':utc()}, call.run_id, 'rejected_output', call_id=call.id)
                with self.register.transaction():
                    body['stop_reason'] = 'role_format_or_candidate_rejection'
                    body['rejection_id'] = str(error.id)
                    self._save_attempt(transport.id, body)
                raise GateError('Rollenformat/Kandidat abgewiesen; keine Rettungsgeneration') from None
            if crash:
                crash('before_incorporation')
            with self.register.transaction():
                body = self._attempt(transport.id)
                if body['stop_reason'] or self._state(transport.id) != 'validated':
                    raise GateError('Stopp verhindert Übernahme')
                self._event(transport, 'incorporated', raw=UUID(body['raw_id']), parsed=parsed)
            return parsed

    def fetch_metadata(self, adapter, transport_id):
        transport = self.register.get(transport_id, TransportAttempt)
        call = self.register.get(transport.call_id, ModelCall)
        body = self._attempt(transport.id)
        events = [e for e in self.register.all(TransportStateEvent) if e.transport_id == transport.id]
        generation = next((e.generation_id for e in reversed(events) if e.generation_id), None)
        if not generation and body['raw_id']:
            try:
                candidate = decode(self.store.read(body['raw_id'])).get('id')
                generation = candidate if isinstance(candidate,str) and candidate else None
            except (ValueError,UnicodeError,AttributeError):
                pass
        if not generation:
            raise GateError('Nur vorhandene Generation-ID abfragen')
        if safe_bytes(generation.encode(), adapter._key)[1]:
            raise GateError('Secret in Generation-ID')
        self._check_send(adapter, call, inference=False)
        mid = str(uuid4())
        mb = {'generation_id':generation, 'at':utc(), 'process_id':str(self.process.id)}
        with self.register.transaction():
            self.db.execute('INSERT INTO adapter_metadata VALUES (?,?,?,?,?,?,?)',
                            (mid, str(transport.id), generation, 'dispatching', None, canonical(mb), digest(mb)))
        try:
            response = adapter._metadata(generation)
        except Exception:
            with self.register.transaction():
                self.db.execute('UPDATE adapter_metadata SET status=? WHERE id=?', ('outcome_unknown', mid))
            return self.diagnostic(call.id)
        clean, body_secret = safe_bytes(response.body, adapter._key)
        headers, header_redactions = safe_headers(response.headers,adapter._key)
        redacted = body_secret or bool(header_redactions)
        saved = self._artifact(clean, call.run_id, 'generation_metadata_bytes', call_id=call.id)
        envelope = self._json({'status':response.status, 'redacted':redacted, 'raw_id':str(saved.id),
                'body_redacted':body_secret,'header_redactions':header_redactions,
                'redaction_reason':'secret_exposure_redacted' if redacted else None,
                'original_sha256':hashlib.sha256(response.body).hexdigest(),
                'at':utc(), 'headers':headers}, call.run_id, 'generation_metadata_envelope', call_id=call.id)
        status, reason = 'saved', None
        try:
            native = decode(clean)['data']
            if redacted or response.status != 200 or native.get('id') != generation:
                raise ValueError()
            package = self.register.get(call.model_package_id, ModelPackage)
            if native.get('model') and native['model'] != package.exact_model_id or native.get('provider_name') and native['provider_name'] != package.upstream:
                reason = 'reported_identity_drift'
        except Exception:
            status, reason = 'failed', 'secret_exposure_redacted' if redacted else 'metadata_invalid_or_unavailable'
        with self.register.transaction():
            self.db.execute('UPDATE adapter_metadata SET status=?,artifact_id=? WHERE id=?', (status,str(envelope.id),mid))
            if reason:
                body = self._attempt(transport.id)
                body['metadata_issue'] = reason
                if reason in ('reported_identity_drift','secret_exposure_redacted'):
                    body['stop_reason'] = reason
                self._save_attempt(transport.id,body)
        self._book_cost(transport)
        return self.diagnostic(call.id)

    def recover_metadata(self, metadata_id, *, reason):
        row, body = self._row('adapter_metadata','id',metadata_id)
        if not reason.strip() or body['process_id'] == str(self.process.id):
            raise GateError('Nur belegte bewusste Recovery eines verwaisten Metadatenabrufs')
        transport = self.register.get(row['transport_id'],TransportAttempt)
        call = self.register.get(transport.call_id,ModelCall)
        self._json({'action':'metadata_recovery','metadata_id':metadata_id,'reason':reason,'at':utc()},call.run_id,'metadata_recovery',call_id=call.id)
        with self.register.transaction():
            if row['status'] == 'dispatching':
                self.db.execute('UPDATE adapter_metadata SET status=? WHERE id=?',('outcome_unknown',metadata_id))
        return self.diagnostic(call.id)

    @deterministic
    def billing(self, transport_id):
        body = self._attempt(transport_id)
        sources = []
        if body['raw_id']:
            sources.append(('response', body['raw_id'], self.store.read(body['raw_id'])))
        for row in self.db.execute("SELECT artifact_id FROM adapter_metadata WHERE transport_id=? AND status='saved' ORDER BY rowid", (str(transport_id),)):
            env = json.loads(self.store.read(row[0]))
            sources.append(('metadata', env['raw_id'], self.store.read(env['raw_id'])))
        costs, usages, evidence_ids = [], [], []
        for kind, aid, raw in sources:
            evidence_ids.append(aid)
            try:
                data = decode(raw)
                raw_usage = data.get('usage')
                value = raw_usage.get('cost') if kind=='response' and isinstance(raw_usage,dict) else data['data'].get('total_cost') if kind=='metadata' else None
                usage = data.get('usage') if kind=='response' else data['data']
                usages.append({'kind':kind, 'evidence_id':aid, 'native':export_json(usage)})
                if value is not None:
                    costs.append(decimal_value(value))
            except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError):
                continue
        unique = set(costs)
        call = self.register.get(self.register.get(transport_id,TransportAttempt).call_id,ModelCall)
        package = self.register.get(call.model_package_id,ModelPackage)
        currency = package.currency
        call_row,_ = self._row('adapter_call','call_id',call.id)
        _,order = self._row('adapter_start_order','id',call_row['order_id'])
        preview = json.loads(self.store.read(order['preview_id']))
        return {'status':'known' if len(unique)==1 else 'unresolved',
                'amount':str(next(iter(unique))) if len(unique)==1 else None,
                'currency':currency, 'price_as_of':package.price_as_of.isoformat(),
                'price_evidence_id':order['preview_id'], 'eur_basis':preview['basis'].get('eur_basis'),
                'pilot_consumption':preview.get('pilot_consumption'), 'conflict':len(unique)>1, 'evidence_ids':evidence_ids,
                'native_usage_evidence':usages,
                'uncertainty':'Native evidence only; synthetic fixtures are not provider prices; conflicts/missing values remain open'}

    def _book_cost(self, transport):
        billing = self.billing(transport.id)
        if billing['status'] != 'known':
            return
        call = self.register.get(transport.call_id, ModelCall)
        run = self.register.get(call.run_id,Run)
        row, _ = self._row('adapter_call','call_id',call.id)
        _, order = self._row('adapter_start_order','id',row['order_id'])
        with self.register.transaction():
            if any(c.transport_id==transport.id for c in self.register.all(CostEntry)):
                return
            self.register._put(CostEntry(code=f'COST-{uuid4()}',run_id=run.id,transport_id=transport.id,
                  operating_area=run.purpose, amount=NonnegativeObservation(status='observed',value=Decimal(billing['amount']),unit=billing['currency'],source='native response/metadata'),
                  currency=billing['currency'],price_evidence_id=UUID(order['preview_id']),
                  billing_evidence_ids=tuple(UUID(a) for a in billing['evidence_ids']), uncertainty=billing['uncertainty']))

    @deterministic
    def diagnostic(self, call_id):
        call = self.register.get(call_id,ModelCall)
        row, _ = self._row('adapter_call','call_id',call.id)
        attempts, amounts, known_active = [], [], Decimal(0)
        timing_complete, known_portion = True, False
        for item in self.db.execute('SELECT transport_id FROM transport_binding WHERE call_id=? ORDER BY number', (str(call.id),)):
            tid = item[0]
            body = self._attempt(tid)
            billing = self.billing(tid)
            if billing['amount'] is not None:
                amounts.append(Decimal(billing['amount']))
            portions = []
            if body.get('interrupted_retry_waits'):
                timing_complete = False
                portions.extend({'kind':'retry','seconds':None,'status':'unknown_active','evidence':t} for t in body['interrupted_retry_waits'])
            for kind, timing in (('provider',body['completed']),('retry',body['retry_wait'])):
                if timing and 'monotonic_end' in timing:
                    duration = sum_exact((Decimal(timing['monotonic_end']), Decimal(timing['monotonic_start']).copy_negate()))
                    known_active = sum_exact((known_active, duration))
                    known_portion = True
                    portions.append({'kind':kind,'seconds':str(duration),'evidence':timing})
                elif timing or kind=='provider' and body['dispatch']:
                    timing_complete = False
                    portions.append({'kind':kind,'seconds':None,'status':'unknown_active'})
            attempts.append({'transport_id':tid,'status':self._state(tid),'journal':body,'billing':billing,'active_portions':portions})
        package = self.register.get(call.model_package_id,ModelPackage)
        return {'call_id':str(call.id),'run_id':str(call.run_id),'node':call.node,
                'request_hash':call.request_hash,'request_artifact_id':row['request_artifact_id'],
                'order_id':row['order_id'],'attempts':attempts,
                'stop_reason':attempts[-1]['journal']['stop_reason'],
                'operating_area':self.register.get(call.run_id,Run).purpose,
                'cost':{'status':'known' if len(amounts)==len(attempts) else 'partial' if amounts else 'unresolved',
                        'known_subtotal':str(sum_exact(amounts)) if amounts else None,'currency':package.currency},
                'active_time':{'status':('observed' if any(a['active_portions'] for a in attempts) else 'not_collected') if timing_complete else 'technical_missing',
                               'known_subtotal_seconds':str(known_active) if known_portion else None},
                'coordination_limit':'#9 owns actual pauses/outages/full pipeline intervals; requested pause does not close a call interval'}

    @deterministic
    def effective_costs(self, run_id):
        """Authoritative current cost view for #9/#14; CostEntry is historical evidence.

        Bind exported measurements to evidence_revision and phase_revision. A later
        metadata receipt changes that binding, including a known-to-conflict change.
        """
        if self.db.in_transaction:
            return self.costs_in_transaction(run_id)
        with self.register.transaction():
            return self.costs_in_transaction(run_id)

    @deterministic
    def costs_in_transaction(self, run_id):
        """Same derivation for an existing atomic trusted register operation."""
        if not self.db.in_transaction:
            raise RuntimeError('Kostenableitung braucht Registertransaktion')
        run = self.register.get(run_id,Run)
        calls = [self.diagnostic(row[0]) for row in self.db.execute(
            'SELECT call_id FROM call_binding WHERE run_id=? ORDER BY sequence',(str(run.id),))]
        phase_revision = self.register.revision(run.phase_id)
        currencies = {c['cost']['currency'] for c in calls}
        complete = bool(calls) and all(c['cost']['status']=='known' for c in calls) and len(currencies)==1
        amounts = [Decimal(c['cost']['known_subtotal']) for c in calls if c['cost']['known_subtotal'] is not None]
        evidence = [{'call_id':c['call_id'],'cost':c['cost'],
            'attempts':[{'transport_id':a['transport_id'],'billing':a['billing']} for a in c['attempts']]} for c in calls]
        binding = {'run_id':str(run.id),'phase_revision':phase_revision,'evidence':evidence}
        return {**binding,'evidence_revision':digest(binding),
            'status':'known' if complete else 'partial' if amounts and len(currencies)==1 else 'unresolved',
            'amount':str(sum_exact(amounts)) if complete else None,
            'known_subtotal':str(sum_exact(amounts)) if amounts and len(currencies)==1 else None,
            'currency':next(iter(currencies)) if len(currencies)==1 else None,
            'historical_cost_entries':[str(c.id) for c in self.register.all(CostEntry) if c.run_id==run.id]}

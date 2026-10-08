"""One post-Seal measurement sequence per normal run; never feeds generation."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from uuid import UUID, uuid4

from .artifacts import ArtifactStore
from .domain import (AssetVersion, Cell, ConfigurationVersion, Event, ProcessInstance, Run, Study, StudyPhase, digest)
from .evaluation import Evaluator, instrument_manifest as functional_manifest
from .static_analysis import StaticAnalyzer, instrument_manifest as static_manifest
from .register import GateError


def now(): return datetime.now(timezone.utc).isoformat()


def request_retry(register, run_id, data):
    """Queue only a failed post-Seal check, never a new migration/model call."""
    from .review_ui import once, receipt
    if set(data)!={'person','reason','confirm','idempotency_key'} or data['confirm']!='true' or not data['person'].strip() or not data['reason'].strip():
        raise GateError('Name, Grund und bewusste Bestätigung fehlen')
    def perform(fingerprint):
        from .preparation import active_generation
        row=register.connection.execute('SELECT * FROM run_completion WHERE run_id=?',(str(run_id),)).fetchone()
        if not row or row['status']!='failed':raise GateError('Nur unvollständige automatische Prüfungen können erneut ausgeführt werden')
        if active_generation(register) or register.connection.execute("SELECT 1 FROM run_completion WHERE status IN ('running','retry_requested')").fetchone():
            raise GateError('Zuerst die laufende Arbeit abschließen')
        if register.state(run_id).seal not in ('sealed','no_candidate'):raise GateError('Gesicherter Abschlussbefund erforderlich')
        proof=ArtifactStore(register.settings,register).json({'previous':dict(row),'person':data['person'].strip(),
            'reason':data['reason'].strip(),'generation_restarted':False,'new_measurement_ids':True},run_id=run_id,artifact_type='measurement_retry_decision',producer='trusted_evaluator',access_scope='trusted_register')
        with register.transaction():
            register.connection.execute("UPDATE run_completion SET status='retry_requested',step='Neue Messung angefordert',reason=?,attempt=attempt+1,updated_at=? WHERE run_id=?",
                (data['reason'].strip(),now(),str(run_id)))
            receipt(register,data['idempotency_key'],fingerprint,'measurement_retry',proof.id)
        return str(proof.id)
    return once(register,data['idempotency_key'],'measurement_retry',{'run_id':str(run_id),**data},perform)


class CompletionWorker:
    def __init__(self, register, sandbox):
        self.register = register
        self.sandbox = sandbox
        self.evaluator = Evaluator(sandbox)
        self.static = StaticAnalyzer(sandbox)
        self.control_id = None
        from .preparation import software_identity
        self.process_id = register.add(ProcessInstance(code='CHECK-PROCESS-'+str(uuid4()),
            worker='automatic_evaluation',software_commit=software_identity(),platform='local worker',
            started_at=datetime.now(timezone.utc),clock_description='UTC display timestamps; not UF5')).id

    def boot(self):
        # Interrupted external checks never silently become a fresh attempt.
        rows=self.register.connection.execute("SELECT run_id FROM run_completion WHERE status='running'").fetchall()
        for row in rows:
            self.update(UUID(row[0]),'failed','Wiederaufnahme nötig','Messung durch Worker-Neustart unterbrochen; Originalversuch bleibt erhalten.')

    def update(self, run_id, status, step, reason=None, **ids):
        with self.register.transaction():
            previous=self.register.connection.execute('SELECT * FROM run_completion WHERE run_id=?',(str(run_id),)).fetchone()
            if not previous or previous['step']!=step or previous['status']!=status:
                events=[e for e in self.register.all(Event) if e.run_id==run_id]
                sequence=max((e.sequence for e in events),default=0)
                def event(kind, details):
                    nonlocal sequence
                    sequence+=1
                    self.register._put(Event(code='CHECK-EVENT-'+str(uuid4()),run_id=run_id,sequence=sequence,
                        event_type=kind,process_id=self.process_id,interval_id=None,
                        happened_at=datetime.now(timezone.utc),evidence_ids=(),details=details))
                if previous and previous['status']=='running':
                    event('check_finished',{'step':previous['step'],'status':'failed' if status=='failed' else 'done'})
                if status=='running':event('check_started',{'step':step})
            self.register.connection.execute('INSERT INTO run_completion(run_id,status,step,reason,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET status=excluded.status,step=excluded.step,reason=excluded.reason,updated_at=excluded.updated_at',
                (str(run_id),status,step,reason,now()))
            for field in ('functional_id','static_id'):
                if ids.get(field):self.register.connection.execute(f'UPDATE run_completion SET {field}=? WHERE run_id=?',(str(ids[field]),str(run_id)))
        state=self.register.state(run_id)
        evaluation={'running':'automatic_running','manual_pending':'manual_pending','failed':'measurement_error','complete':'complete'}[status]
        if state.evaluation!=evaluation:
            self.register.set_state(run_id,state.model_copy(update={'evaluation':evaluation}),reason=step+((': '+reason) if reason else ''))

    def tool(self, conf, manifest):
        from .instrument_compatibility import instrument_compatible
        found=[x for x in conf.settings.tool_ids if instrument_compatible(self.register,x,manifest,conf.settings.software_commit)]
        if len(found)!=1: raise GateError('Aktuelles Messinstrument fehlt in dieser Konfiguration. Neue Konfiguration vorbereiten; ältere Läufe werden nicht umgebunden.')
        return found[0]

    def control(self):
        if self.control_id:return self.control_id
        from .application_settings import default_settings, installed_tools
        from .preparation import imported_catalog, put_configuration, installed_dependencies
        from .snapshots import Snapshots, ProcessWriters
        r=self.register;store=ArtifactStore(r.settings,r)
        _,mock=imported_catalog(r)
        installed_tools(r)
        settings=default_settings(r).model_copy(update={'model_a':mock.id,'model_b':mock.id,'role_parameters':{'all':{'seed':1}}})
        proof=store.json({'purpose':'Isolierte native Kontrolle des unveränderten Laravel-Gerüsts; kein Modelllauf und kein Forschungsergebnis'},artifact_type='environment_control_intent')
        with r.transaction():
            study=r._put(Study(code='SYSTEM-CONTROL-'+str(uuid4()),title='Systemprüfung der Messumgebung',design_version='control-v1',data_origin='synthetic',provenance='Automatische separate Umgebungsprüfung, keine Modellgenerierung'))
            phase=r._put(StudyPhase(code='CONTROL-PHASE-'+str(uuid4()),study_id=study.id,purpose='demo',provenance=study.provenance))
            version=put_configuration(r,phase.id,'CONTROL',Cell(module='SQL',context='K0',producer='A',verifier='A',planner=False,review=False),settings)
        run,job=r.start_other(phase.id,version.id,decision='Kostenfreie Kontrolle vor unabhängiger Messung',technical_evidence_ids=(proof.id,),idempotency_key='control-'+str(uuid4()))
        workspace=r.settings.staging/('environment-control-'+str(run.id))
        shutil.copytree(self.sandbox.assets/'assets/study/m2-v0.1/scaffold',workspace)
        dependency=installed_dependencies(store)
        Snapshots(store).seal(workspace,writers=ProcessWriters(),run_id=run.id,scaffold_id=settings.scaffold_id,
            dependency_ids=(dependency.id,),internal_tests_hash=hashlib.sha256(b'').hexdigest())
        r.set_state(run.id,r.state(run.id).model_copy(update={'execution':'terminal','terminal_cause':'finished','ended_at':datetime.now(timezone.utc)}),reason='Unverändertes Gerüst für separate native Systemkontrolle versiegelt')
        with r.transaction():r.connection.execute("UPDATE job_state SET status='completed' WHERE job_id=?",(str(job.id),))
        tool=self.tool(version,functional_manifest(self.sandbox.images))
        with r.transaction():r.connection.execute('UPDATE run_completion SET control_run_id=? WHERE run_id=?',(str(run.id),str(self.current_run)))
        execution=self.evaluator.schedule(run.id,tool_id=tool,idempotency_key='control-measure-'+str(run.id))
        control=self.evaluator.verify_control(execution)
        self.evaluator._control(control.id)  # Failed control never authorizes candidate verdicts.
        self.control_id=control.id
        return control.id

    def tick(self):
        retry=self.register.connection.execute("SELECT run_id,attempt FROM run_completion WHERE status='retry_requested' ORDER BY updated_at LIMIT 1").fetchone()
        row=retry or self.register.connection.execute("SELECT p.run_id FROM pipeline_binding p JOIN run_binding r ON p.run_id=r.run_id LEFT JOIN run_completion c ON c.run_id=r.run_id WHERE p.status='completed' AND r.execution='terminal' AND c.run_id IS NULL ORDER BY p.rowid LIMIT 1").fetchone()
        if not row:return False
        run_id=UUID(row[0]);r=self.register
        self.current_run=run_id
        self.update(run_id,'running','Messungen vorbereiten')
        suffix='-retry-'+str(retry['attempt']) if retry else ''
        try:
            if retry:
                control_row=r.connection.execute('SELECT control_run_id FROM run_completion WHERE run_id=?',(str(run_id),)).fetchone()
                recovery_ids=[str(run_id)]+([control_row[0]] if control_row and control_row[0] else [])
                for table,service in [('evaluation_execution',self.evaluator),('static_execution',self.static)]:
                    interrupted=r.connection.execute(f"SELECT id,status FROM {table} WHERE run_id IN ({','.join('?' for _ in recovery_ids)}) AND status IN ('ready','running','recovery_required')",recovery_ids).fetchall()
                    for old in interrupted:
                        if table=='evaluation_execution' and old['status']=='ready':
                            service._raw(old['id'],'recovery',{'decision':'stop_owned','new_measurement_requires_new_id':True})
                            service._update(old['id'],'interrupted')
                            with r.transaction():r.connection.execute("UPDATE job_state SET status='stopped' WHERE job_id=?",(service.row(old['id'])['job_id'],))
                        else:service.recover(old['id'],decision='stop_owned')
            run=r.get(run_id,Run);conf=r.get(run.configuration_version_id,ConfigurationVersion)
            functional=self.tool(conf,functional_manifest(self.sandbox.images))
            static=self.tool(conf,static_manifest(self.sandbox.images))
            state=r.state(run_id)
            if state.seal not in ('sealed','no_candidate'):raise GateError('Seal unvollständig oder Integritätsfehler; keine Ergebniswerte ableiten')
            if state.seal=='sealed':
                self.update(run_id,'running','Messumgebung prüfen')
                control=self.control()
            else:control=None
            self.update(run_id,'running','Funktionale Anforderungen prüfen')
            execution=self.evaluator.schedule(run_id,tool_id=functional,idempotency_key='auto-functional-'+str(run_id)+suffix)
            self.update(run_id,'running','Funktionale Anforderungen prüfen',functional_id=execution)
            result=self.evaluator.run(execution,control_id=control) if control else self.evaluator.run_absent(execution)
            if state.seal=='sealed':
                self.update(run_id,'running','PHPStan / Larastan ausführen')
                attempt=self.static.schedule(run_id,tool_id=static,idempotency_key='auto-static-'+str(run_id)+suffix)
                self.update(run_id,'running','PHPStan / Larastan ausführen',static_id=attempt)
                static_result=self.static.run(attempt)
                missing=(not static_result['analysis_complete'] or result.get('interrupted') or
                    any(e['status'] in ('technical_missing','unclear') for e in result.get('entries',[])))
                self.update(run_id,'failed' if missing else 'manual_pending','Automatische Prüfungen beendet',
                    'Mindestens eine Messung ist unvollständig. Fehlwerte und Originalbefunde sind gespeichert.' if missing else None)
            else:
                self.update(run_id,'complete','Ohne prüfbaren Kandidaten beendet','Kein Laravel-Kandidat vorhanden; funktionale Nichtprüfbarkeit und statische Fehlwerte bleiben dokumentiert.')
        except Exception as exc:
            # No prompt, credential or request body enters the public failure string.
            reason=str(exc)
            from .providers import safe_bytes
            reason=safe_bytes(reason.encode(),None)[0].decode(errors='replace')
            self.update(run_id,'failed','Messung benötigt Aufmerksamkeit',reason)
        return True

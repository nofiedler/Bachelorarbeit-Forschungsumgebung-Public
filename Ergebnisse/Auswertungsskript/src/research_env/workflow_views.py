"""Readable projections of original run evidence; never fabricates observations."""
from datetime import datetime, timezone
import json
import re
from pathlib import Path
from uuid import UUID

from .domain import (Artifact, AssetVersion, CandidateSnapshot, ConfigurationVersion, CriterionReviewRevision,
    MeasurementAttempt, ModelCall, ModelPackage, Run, TestResult, Event)
from .artifacts import ArtifactStore
from .analysis_resources import tokens

# Order follows the actual graph, including its optional single repair branch.
NODES = (
    ('analyzer', 'Analyzer', 'Versteht den PHP-Ausgangscode und die Anforderungen.'),
    ('planner', 'Planner', 'Plant die Umsetzung in Laravel.'),
    ('migrate', 'Migration', 'Erstellt den Laravel-Code.'),
    ('test', 'Test-Agent', 'Erstellt interne Tests für diesen Lauf.'),
    ('runner', 'Interne Tests', 'Führt die erstellten Tests am Code aus.'),
    ('review', 'Review', 'Prüft Code und interne Testergebnisse.'),
    ('repair', 'Überarbeitung', 'Korrigiert den Code bei Bedarf genau einmal.'),
    ('rerunner', 'Tests wiederholen', 'Prüft den überarbeiteten Code erneut.'),
    ('seal', 'Code versiegeln', 'Sichert den letzten Code unveränderlich für die Bewertung.'),
)
ROLE_LABELS = {key: title for key, title, _ in NODES}
STATES = {'waiting': 'Wartet', 'running': 'Läuft gerade', 'done': 'Erledigt',
          'warning': 'Befund vorhanden', 'failed': 'Fehlgeschlagen', 'skipped': 'Übersprungen',
          'paused': 'Unterbrochen', 'manual': 'Deine Bewertung fehlt', 'missing': 'Nicht abgeschlossen'}
CHECKS = (
    ('Messumgebung prüfen', 'Messumgebung', 'Prüft zuerst die unabhängige Testumgebung.'),
    ('Funktionale Anforderungen prüfen', 'Funktionale Tests', 'Prüft die sechs Anforderungskategorien R1–R6 am versiegelten Code.'),
    ('PHPStan / Larastan ausführen', 'Statische Analyse', 'Misst PHP-Diagnosen und Codeumfang mit PHPStan / Larastan.'),
)


def duration(seconds):
    if seconds is None: return 'Zeit nicht erfasst'
    if seconds < 1: return '< 1 s'
    seconds = int(seconds)
    if seconds < 60: return f'{seconds} s'
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60: return f'{minutes} min {seconds:02d} s'
    hours, minutes = divmod(minutes, 60)
    return f'{hours} h {minutes:02d} min'


def observed_span(start, end):
    # Wall-clock display only; research UF5 uses the separate monotonic intervals.
    if not start or not end or start.process_id != end.process_id: return None
    value = (end.happened_at - start.happened_at).total_seconds()
    return value if value >= 0 else None


def provider_output_limit(register, run_id):
    """Explain native truncation evidence, including older misclassified runs."""
    store = ArtifactStore(register.settings, register)
    rows = register.connection.execute("SELECT id FROM register_record WHERE kind='Artifact' AND json_extract(payload,'$.run_id')=? AND json_extract(payload,'$.artifact_type')='response_bytes' ORDER BY rowid DESC", (str(run_id),)).fetchall()
    for row in rows:
        try:
            native = json.loads(store.read(row[0]))
            if not any(choice.get('finish_reason') == 'length' for choice in native.get('choices', []) if isinstance(choice, dict)):
                continue
            artifact = register.get(UUID(row[0]), Artifact)
            call = register.get(artifact.call_id, ModelCall)
            return {'node': call.node, 'label': ROLE_LABELS.get(call.node, call.node),
                'reason': 'Der Anbieter hat die Antwort am Ausgabelimit abgeschnitten (finish_reason=length). '
                          'Die Antwort ist laut Anbieter unvollständig. Dies ist ein technischer Anbieterbefund, '
                          'kein Nachweis fachlich falschen Anwendungscodes. Bereits gesicherter Code wird separat bewertet.',
                'artifact_id': row[0]}
        except (ValueError, KeyError, TypeError, AttributeError, OSError):
            continue
    return None


def rejected_answer(register, run_id):
    limit = provider_output_limit(register, run_id)
    if limit:
        return limit
    rows=register.connection.execute("SELECT id FROM register_record WHERE kind='Artifact' AND json_extract(payload,'$.run_id')=? AND json_extract(payload,'$.artifact_type')='pipeline_rejected_output' ORDER BY rowid DESC LIMIT 1",(str(run_id),)).fetchall()
    if not rows:return None
    store=ArtifactStore(register.settings,register)
    try:
        body=json.loads(store.read(rows[0][0]));role=body['role'];reason=str(body['reason'])
        if reason.startswith('Repair verändert die deklarierte Dateimenge'):
            effect=register.connection.execute("SELECT artifact_id FROM pipeline_effect WHERE run_id=? AND node='migrate'",(str(run_id),)).fetchone()
            state=json.loads(store.read(effect[0]));migration=json.loads(store.read(state['refs']['migrate']))
            expected={f['path'] for f in migration['files']}
            parsed=json.loads(store.read(body['raw_parsed_id']));answer=json.loads(parsed['content'])
            actual={f['path'] for f in answer['files']}
            missing=sorted(expected-actual);extra=sorted(actual-expected)
            reason='Die Antwort enthält nicht alle erforderlichen Dateien.'
            if missing:reason+=' Fehlend: '+', '.join(missing)+'.'
            if extra:reason+=' Zusätzliche Dateien: '+', '.join(extra)+'.'
            reason+=' Die Überarbeitung wurde nicht übernommen. Der vorherige Code bleibt erhalten.'
        elif reason.startswith('Geschützter Pfad außerhalb Rollenallowlist'):
            from .role_formats import permitted
            parsed=json.loads(store.read(body['raw_parsed_id']));answer=json.loads(parsed['content'])
            forbidden=[f['path'] for f in answer['files'] if not permitted(f['path'],role)]
            reason=('Der Test-Agent hat Anwendungscode statt erlaubter interner PHP-Tests zurückgegeben. Er darf nur Testdateien unter internal/ erzeugen.' if role=='test' else 'Die Antwort enthält Dateien außerhalb des erlaubten Bereichs dieser Rolle.')
            reason+=' Nicht übernommen: '+', '.join(forbidden)+'. Die Antwort wurde abgewiesen; bisheriger Code bleibt erhalten.'
        elif body.get('exception')=='JSONDecodeError':
            reason='Die Antwort war kein gültiges JSON und konnte deshalb nicht übernommen werden. '+reason
        return {'node':role,'label':ROLE_LABELS.get(role,role),'reason':reason[:1600],'artifact_id':rows[0][0]}
    except (ValueError,KeyError,TypeError,IndexError,OSError):
        return {'node':'unknown','label':'Agentenantwort','reason':'Der gespeicherte Fehlerbeleg konnte nicht vollständig gelesen werden. Öffne die Originalnachweise.','artifact_id':rows[0][0]}


def internal_test_findings(register, run_id):
    """Explain recorded runner findings without changing or rejudging results."""
    rows=register.connection.execute("SELECT id FROM register_record WHERE kind='Artifact' AND json_extract(payload,'$.run_id')=? AND json_extract(payload,'$.artifact_type')='pipeline_internal_result' ORDER BY rowid",(str(run_id),)).fetchall()
    store=ArtifactStore(register.settings,register)
    test_row=register.connection.execute("SELECT id FROM register_record WHERE kind='Artifact' AND json_extract(payload,'$.run_id')=? AND json_extract(payload,'$.artifact_type')='role_test' ORDER BY rowid DESC LIMIT 1",(str(run_id),)).fetchone()
    null_files=[]
    if test_row:
        try:
            null_files=[f['path'] for f in json.loads(store.read(test_row[0]))['files'] if '\x00' in f['content']]
        except (ValueError,KeyError,TypeError,OSError):pass
    findings={}
    for row in rows:
        try:
            report=json.loads(store.read(row[0]));node=report['node']
            if report['classification']!='candidate_failure':continue
            note='Die interne Prüfung meldet einen Befund; Ursache kann im Anwendungscode oder im erzeugten Test liegen.'
            reason=note
            for observation in report.get('observations',[]):
                if observation.get('exit_code') in (0,None):continue
                for log in observation.get('logs',[]):
                    content=log.get('content','')
                    if observation.get('stage')=='tests' and null_files and 'ParseError' in content and '0x00' in content:
                        note='Testskript nicht ausführbar: ungültiges Nullzeichen in '+', '.join(null_files)+'.'
                        reason=('Der Test-Agent hat ein nicht ausführbares PHP-Testskript erzeugt: ungültiges Nullzeichen (0x00) in '+', '.join(null_files)+'. '
                            'Die Überarbeitung darf nur Anwendungscode ändern. Der Testfehler bleibt deshalb beim Wiederholen bestehen. '
                            'Die unabhängige Bewertung prüft den gesicherten Anwendungscode separat.')
                        break
                    line=next((line for line in content.splitlines() if 'PIPELINE_INTERNAL_THROWABLE' in line or line.startswith(('PHP Parse error','PHP Fatal error'))),None)
                    if line:
                        note='Interne Prüfung fehlgeschlagen. Den konkreten Befund findest du oben im Laufstatus.'
                        reason='Die interne Prüfung ist fehlgeschlagen: '+line[:650]+'. Der Befund allein unterscheidet nicht sicher zwischen einem Fehler im Anwendungscode und einem Fehler im erzeugten Test.'
                text='\n'.join(log.get('content','') for log in observation.get('logs',[]))
                ended=observation.get('native_state',{}).get('FinishedAt')
                ready=re.findall(r'^(\d{4}-\d\d-\d\dT[\d:.]+Z).*ready for connections.*port: 3306\b',text,re.MULTILINE)
                if ended and ready and 'Connection refused' in text and '[2002]' in text:
                    try:
                        late=all(datetime.fromisoformat(t)>datetime.fromisoformat(ended) for t in ready)
                    except (ValueError,TypeError):late=False
                    if late:
                        note='Umgebungsfehler: Test gestartet, bevor MySQL bereit war.'
                        reason=('Umgebungsfehler: Der interne Test wurde gestartet, bevor MySQL bereit war. '
                            'Die gespeicherten Zeitstempel belegen den verfrühten Start und die abgewiesene Verbindung. '
                            'Dieser Befund darf nicht als Fehler des KI-Codes oder als Vergleich der Modellqualität gewertet werden. '
                            'Die ursprüngliche Fehlerzuordnung bleibt im Protokoll erhalten; der Startablauf ist inzwischen korrigiert.')
            findings[node]={'reason':reason,'note':note,'artifact_id':row[0]}
        except (ValueError,KeyError,TypeError,OSError):continue
    return findings


def diagram(register, run, conf, state, binding, completion, rejected=None, internal=None):
    events = sorted((e for e in register.all(Event) if e.run_id == run.id), key=lambda e: e.sequence)
    nodes = []
    stopped = binding and binding['status'] in ('paused', 'recovery_required')
    for key, title, description in NODES:
        history = [e for e in events if e.details.get('node') == key]
        start = next((e for e in reversed(history) if e.event_type == 'node_started'), None)
        end = next((e for e in reversed(history) if e.event_type == 'node_finished'), None)
        if start and end and end.sequence < start.sequence: end = None
        raw = end.details.get('status') if end else None
        status, note = 'waiting', None
        if end:
            status = ('failed' if raw in ('failed', 'no_candidate') else
                      'warning' if raw in ('candidate_failure', 'changes_required') else 'done')
            if raw == 'candidate_failure': note = (internal or {}).get(key,{}).get('note','Die interne Prüfung meldet einen Befund im Anwendungscode oder im erzeugten Test.')
            if raw == 'changes_required': note = 'Review hat Änderungen angefordert.'
            if raw == 'no_candidate': note = 'Es wurde kein versiegelbarer Code erzeugt.'
            if raw == 'failed': note = rejected['reason'] if rejected and rejected['node']==key else 'Dieser Schritt konnte nicht abgeschlossen werden. Der Befund bleibt gespeichert.'
        elif start:
            status = 'paused' if stopped or state.execution == 'terminal' else 'running'
        elif key in ('planner', 'review') and not getattr(conf.cell, key):
            status, note = 'skipped', 'In dieser Konfiguration ausgeschaltet.'
        elif state.execution == 'terminal' or any(e.details.get('node') == 'seal' for e in events):
            status = 'skipped'
            note = ('Nicht erforderlich.' if key in ('repair', 'rerunner') and state.terminal_cause in (None, 'finished')
                    else 'Nach dem vorherigen Laufende nicht mehr ausgeführt.')
        elif key in ('repair', 'rerunner'):
            note = 'Nur bei Fehlern in den internen Tests oder Änderungsbedarf im Review.'
        seconds = observed_span(start, end)
        model = None
        if key in ('analyzer','planner','migrate','test','review','repair'):
            package = register.resolve_call_model(conf,key)
            model = 'Kostenfreie Demo' if package.endpoint.startswith('mock://') else package.exact_model_id
        nodes.append({'key': key, 'title': title, 'description': description, 'status': status,
                      'start_at':start.happened_at.isoformat() if start else None,'end_at':end.happened_at.isoformat() if end else None,
                      'label': STATES[status], 'note': note, 'model': model,
                      'started': start.happened_at.isoformat() if start and status == 'running' else None,
                      'duration': duration(seconds) if start else '—', 'seconds': seconds})
    checks = []
    for step, title, description in CHECKS:
        start = next((e for e in reversed(events) if e.event_type == 'check_started' and e.details.get('step') == step), None)
        end = next((e for e in reversed(events) if e.event_type == 'check_finished' and e.details.get('step') == step), None)
        if start and end and end.sequence < start.sequence: end = None
        status = end.details['status'] if end else 'running' if start else 'waiting'
        if completion and completion['status'] not in ('running','retry_requested') and status == 'running': status = 'failed'
        # Old evidence has no per-check timestamps. Do not invent durations/success.
        if not start and completion and completion['status'] in ('manual_pending', 'complete'):
            status = 'done' if completion['status'] == 'manual_pending' else 'skipped'
        if not start and completion and completion['status'] == 'failed': status = 'missing'
        checks.append({'key': step, 'title': title, 'description': description, 'status': status,
                       'start_at':start.happened_at.isoformat() if start else None,'end_at':end.happened_at.isoformat() if end else None,
                      'label': STATES[status], 'note': None, 'model': None,
                       'started': start.happened_at.isoformat() if start and status == 'running' else None,
                       'seconds':observed_span(start,end),'duration': duration(observed_span(start, end)) if start else '—'})
    active = next((n for n in nodes + checks if n['status'] in ('running','paused')), None)
    return nodes, checks, active, events[-1].happened_at if events else None


def summary(register, run_id, costs, diagnostic):
    run = register.get(run_id, Run)
    conf = register.get(run.configuration_version_id, ConfigurationVersion)
    complete = register.connection.execute('SELECT * FROM run_completion WHERE run_id=?', (str(run_id),)).fetchone()
    complete = dict(complete) if complete else None
    calls = {str(c.id): c for c in register.all(ModelCall) if c.run_id == run_id}
    from .application_settings import model_prices
    tariffs=model_prices(register,[register.get(identity,ModelPackage) for identity in {c.model_package_id for c in calls.values()}])
    rows = []
    for item in costs['evidence']:
        call = calls.get(item['call_id'])
        if not call: continue
        use = tokens({'evidence': [item]})['summary']
        journal = next((c for c in diagnostic['call_journals'] if c['call_id'] == item['call_id']), {})
        rows.append({'role': ROLE_LABELS.get(call.node, call.node),
            'model': register.get(call.model_package_id, ModelPackage).exact_model_id,
            'input': use['input'], 'output': use['output'], 'reasoning': use['reasoning'], 'cache': use['cache_read'], 'cache_write':use['cache_write'],
            'tariff':tariffs[str(call.model_package_id)], 'cost': item['cost'], 'seconds': journal.get('active_time', {}), 'call_id': str(call.id)})
    state = register.state(run_id)
    rejected = rejected_answer(register,run_id)
    internal = internal_test_findings(register,run_id)
    nodes, checks, active, last_activity = diagram(register, run, conf, state, diagnostic['binding'], complete,rejected,internal)
    tone, heading = 'running', active['title'] if active else 'Lauf wird vorbereitet'
    message = active['description'] if active else 'Der Worker bereitet den nächsten Schritt vor.'
    if active and active['status'] == 'paused':
        tone, heading, message = 'warning', 'Lauf unterbrochen', 'Der letzte Stand ist gespeichert. Prüfe unten die Laufsteuerung.'
    elif complete:
        if complete['status'] in ('running','retry_requested'):
            heading, message = complete['step'], 'Die Generierung ist beendet. Jetzt wird der versiegelte Code unabhängig geprüft.'
        elif complete['status'] == 'manual_pending':
            tone, heading, message = 'manual', 'Bereit für deine Bewertung', 'Code und automatische Ergebnisse sind gespeichert. Prüfe jetzt die Laravel-Kriterien.'
            current=results(register,run_id)
            if current['functional'] and current['functional']['missing_case_count']:
                tone,heading,message='warning','Messungen sind unvollständig','Der Code ist gespeichert. In den automatischen Tests fehlen technische Befunde. Du kannst den Code bereits manuell prüfen; der Lauf ist noch nicht vollständig auswertbar.'
                for check in checks:
                    if check['key']=='Funktionale Anforderungen prüfen':check.update(status='warning',label='Messwerte fehlen')
        elif complete['status'] == 'failed':
            tone, heading, message = 'failed', 'Automatische Prüfung unterbrochen', 'Die Migration ist beendet. Eine Messung konnte nicht vollständig abgeschlossen werden.'
        else:
            tone, heading, message = 'done', 'Lauf beendet', complete['reason'] or 'Alle Ergebnisse sind gespeichert.'
    elif state.execution == 'terminal':
        heading, message = 'Migration beendet', 'Die automatischen Prüfungen werden vorbereitet.'
    closure = closure_view(register,run_id) if state.evaluation=='complete' else None
    if closure:
        if closure['current']:
            tone, heading, message = 'done', 'Lauf abgeschlossen', 'Deine Bewertung und der zugehörige Ergebnisstand sind unveränderlich dokumentiert.'
        else:
            tone, heading, message = 'warning', 'Bewertungsstand geändert', 'Seit dem letzten Abschluss wurden Nachweise geändert. Prüfe die aktuelle Ergebnisübersicht erneut.'
    failure = None
    if state.terminal_cause and state.terminal_cause != 'finished':
        failure = {'content_failure': 'Der erzeugte Code oder eine Agentenantwort erfüllte die Vorgaben nicht.',
                   'technical_failure': 'Die Pipeline wurde durch einen technischen Fehler beendet.',
                   'interrupted': 'Dieser Lauf wurde abgebrochen.',
                   'outcome_unknown': 'Der Ausgang eines Modellaufrufs ist unklar. Er wird nicht automatisch wiederholt.'}.get(state.terminal_cause)
    failure_evidence = None
    if state.terminal_cause=='content_failure' and internal:
        failure_evidence=next(reversed(internal.values()))
        failure=failure_evidence['reason']
    if rejected:
        failure=rejected['label']+': '+rejected['reason']
        failure_evidence=rejected
    if failure and tone == 'manual':
        tone,heading,message='warning','Mit Fehlern beendet · Bewertung möglich','Die Pipeline konnte nicht alle Schritte erfolgreich abschließen. Der gesicherte Code und die automatischen Befunde stehen für deine Bewertung bereit.'
    total_end = datetime.fromisoformat(complete['updated_at']) if complete and complete['status'] not in ('running','retry_requested') else None
    elapsed = (total_end - run.started_at).total_seconds() if total_end else None
    return {'completion': complete, 'role_rows': rows, 'token_totals': tokens(costs)['summary'],
        'pipeline_nodes': nodes, 'check_nodes': checks, 'heading': heading, 'tone': tone, 'message': message,
        'pipeline_failure': failure, 'rejected_answer': rejected, 'failure_evidence':failure_evidence, 'last_activity': last_activity, 'current_step': heading,
        'role_labels': ROLE_LABELS, 'finished': bool(complete and complete['status'] not in ('running','retry_requested')),
        'elapsed': duration(elapsed), 'elapsed_start': None if total_end else run.started_at.isoformat(), 'closure': closure}


def results(register,run_id):
    """Select last completed valid compatible revisions, just like main analysis."""
    from .analysis import functional, static_profile
    from .evaluation_oracles import Suite
    from .evidence_views import current_valid
    run=register.get(run_id,Run);conf=register.get(run.configuration_version_id,ConfigurationVersion)
    state=register.state(run_id);store=ArtifactStore(register.settings,register)
    cache={}
    def compatible(value):
        comp=value.compatibility
        return (state.candidate_id and comp.candidate_id==state.candidate_id
            and comp.phase_id==run.phase_id and comp.contract_id==conf.settings.contract_id
            and comp.suite_id==run.suite_id and register.compatible_evaluation_tool(run_id,comp.tool_id)
            and current_valid(register,value,cache))
    measurements=[m for m in register.for_run(MeasurementAttempt,run_id) if compatible(m)]
    selected={k:max((m for m in measurements if m.measurement_key==k),key=lambda m:m.revision,default=None) for k in ('functional_R','static_DLS')}
    revisions=register.for_run(CriterionReviewRevision,run_id)
    reviews={k:max((v for v in revisions if v.criterion==k and compatible(v)),key=lambda v:v.revision,default=None) for k in ('T1','T2','T3','T4','T5')}
    f=None;error=None
    try:
        suite_asset=register.get(run.suite_id,AssetVersion)
        relative='development/m6-v1' if suite_asset.suite_kind=='development' else 'study_holdout/m2-v0.1'
        lock=json.loads(Path(__file__).with_name('evaluation_assets.lock.json').read_text())[relative]
        from .domain import digest
        if suite_asset.manifest_hash!=digest(lock):raise ValueError('Historischer Testsatz passt nicht zur installierten Auswertung')
        suite=Suite(Path(__file__).resolve().parents[2]/'evaluation'/relative,kind=suite_asset.suite_kind,expected_hashes=lock)
        cases=[{'id':c['id'],'category':c['category'],'assertions':[a['id'] for step in c['steps'] for a in step['assertions']]} for c in suite.cases_for(conf.cell.module)]
        ids=register.connection.execute("SELECT id FROM register_record WHERE kind='TestResult' AND json_extract(payload,'$.measurement_id')=? ORDER BY rowid",(str(selected['functional_R'].id),)).fetchall() if selected['functional_R'] else []
        test_results=[register.get(row[0],TestResult).model_dump(mode='json') for row in ids]
        f=functional(cases,test_results,{k:v.model_dump(mode='json') for k,v in reviews.items() if v})
    except (OSError,ValueError) as exc:error=str(exc)
    static=selected['static_DLS']
    report=json.loads(static.result.value) if static and static.result.status=='observed' else None
    absence=None
    if state.seal=='no_candidate':
        from .analysis_absence import select_absence,receipt_binding
        chosen=select_absence(register,run,main_only=False)
        if chosen['revision_id']:
            proof=register.get(UUID(chosen['revision_id']),Artifact)
            absence={'id':str(proof.id),**receipt_binding(register,proof,main_only=False)['receipt']}
            if not error:
                from .analysis import value
                absence_results=[{'test_id':x['case_id'],'assertion_id':x['id'],'r_category':x['category'],'status':x['status'],'cause':x['cause']} for x in absence['entries']]
                absence_reviews={'T1':{'verdict':value(0,unit='binary',source=str(proof.id))}} if absence['T']==0 else {}
                f=functional(cases,absence_results,absence_reviews,missing='technical_missing')
    profile=static_profile(report)
    open_reviews=[k for k in ('T2','T3','T4') if not reviews[k]] if state.seal!='no_candidate' else []
    can_close=bool(absence) if state.seal=='no_candidate' else bool(not open_reviews and f and f['T']['value'] is not None and all(v['value'] is not None for v in f['R'].values()) and profile['analysis_complete'])
    return {'functional':f,'static':profile,'result_error':error,'absence':absence,'can_close':can_close,
        'open_reviews':open_reviews,
        'selected_measurements':{k:str(v.id) if v else None for k,v in selected.items()},
        'selected_reviews':{k:str(v.id) if v else None for k,v in reviews.items()},'static_report':report}


def result_binding(result):
    from .domain import digest
    return digest({key: result[key] for key in ('selected_measurements','selected_reviews','functional','static','absence')})


def closure_view(register,run_id,result=None):
    event=max((e for e in register.for_run(Event,run_id) if e.event_type=='run_closed'),key=lambda e:e.sequence,default=None)
    if not event:return None
    result=result if result is not None else results(register,run_id)
    return {'id':str(event.id),'person':event.details['person'],'at':event.happened_at,
        'current':event.details['result_hash']==result_binding(result),'result_hash':event.details['result_hash']}


def close_run(register,run_id,data):
    from .domain import Observation, ProcessInstance, Study, StudyPhase, canonical
    from .register import GateError
    from .review_ui import once,receipt
    from uuid import uuid4
    if set(data)!={'idempotency_key','result_hash','person','confirm'} or data['confirm']!='true' or not data['person'].strip():
        raise GateError('Bestätigung und prüfende Person fehlen')
    def perform(fingerprint):
        current=results(register,run_id)
        if data['result_hash']!=result_binding(current):raise GateError('Bewertungsstand hat sich geändert; Ergebnisse neu prüfen')
        functional=current['functional']
        if not current['can_close']:
            raise GateError('Pflichtbewertungen oder automatische Messungen fehlen noch')
        state=register.state(run_id)
        if state.execution!='terminal' or state.seal not in ('sealed','no_candidate'):raise GateError('Nur einen beendeten Lauf mit gesichertem Abschlussbefund abschließen')
        run=register.get(run_id,Run)
        study=register.get(register.get(run.phase_id,StudyPhase).study_id,Study)
        if study.data_origin=='synthetic' and not data['person'].startswith('TECHNICAL-FIXTURE:'):
            raise GateError('Demos ausdrücklich mit TECHNICAL-FIXTURE: kennzeichnen')
        from .preparation import software_identity
        with register.transaction():
            process=register._put(ProcessInstance(code='CLOSE-PROCESS-'+str(uuid4()),worker='local_human_form',
                software_commit=software_identity(),platform='local browser form',started_at=datetime.now(timezone.utc),
                clock_description='UTC observation; not a runtime measurement'))
            event=register._put(Event(code='RUN-CLOSED-'+str(uuid4()),run_id=run_id,
                sequence=1+max((e.sequence for e in register.all(Event) if e.run_id==run_id),default=0),
                event_type='run_closed',process_id=process.id,interval_id=None,happened_at=datetime.now(timezone.utc),
                evidence_ids=(),details={'person':data['person'],'result_hash':data['result_hash'],
                    'measurements':current['selected_measurements'],'reviews':current['selected_reviews'],'F':functional['F'],
                    'absence_receipt_id':current['absence']['id'] if current['absence'] else None}))
            register.set_state_in_transaction(run_id,state.model_copy(update={'evaluation':'complete',
                'result':Observation(status='observed',value=functional['F']['value'],unit='F',source='F derived from result binding '+data['result_hash']) if functional['F']['value'] is not None else Observation(status='technical_missing',unit='F',reason='Abgeschlossen ohne Kandidaten; gespeicherter Fehlbefund '+current['absence']['id'])}),
                reason='Bewertungsstand ausdrücklich abgeschlossen: '+str(event.id))
            receipt(register,data['idempotency_key'],fingerprint,'close_run',event.id)
        return str(event.id)
    return once(register,data['idempotency_key'],'close_run',{'run_id':str(run_id),**data},perform)


# Operationalisierung §2.3: descriptions, not new criteria or scoring rules.
REQUIREMENTS = {
 'BF':dict(zip(('R1','R2','R3','R4','R5','R6'),('Formular mit Benutzername, Passwort und Login','Gültige Zugangsdaten: Erfolg und zugehöriger Avatar','Falsches Passwort oder unbekannter Benutzer: kein Erfolg und kein Avatar','Fehlende oder leere Eingaben: definierte negative Antwort','Verschiedene Benutzer korrekt zuordnen; Datenbestand unverändert','Aufeinanderfolgende positive und negative Anfragen ohne Ergebnisübernahme'))),
 'SQL':dict(zip(('R1','R2','R3','R4','R5','R6'),('Formular mit ID und Submit','Vorhandene ID: richtigen Vor- und Nachnamen anzeigen','Unbekannte gültige ID: keine fremden Benutzerdaten','Fehlende oder leere ID: definierte negative Antwort','Verschiedene IDs korrekt zuordnen; Datenbestand unverändert','Wiederholte Abfragen ohne Ergebnisübernahme'))),
 'UP':dict(zip(('R1','R2','R3','R4','R5','R6'),('Multipart-Formular mit Datei und Upload','Gültige Bilddatei speichern und Erfolg melden','Kontrollierter Speicherfehler: Fehler statt falscher Erfolgsmeldung','Fehlende Datei: definierte negative Antwort','Inhalt unverändert speichern und Namen/Pfad korrekt zurückmelden','Mehrere Uploads erhalten zuvor gespeicherte Dateien')))
}

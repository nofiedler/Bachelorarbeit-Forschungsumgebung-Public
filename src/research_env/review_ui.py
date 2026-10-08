"""Human review revisions. No generated judgment and no feedback to roles."""
from datetime import datetime, timezone
import json
from uuid import UUID,uuid4
from .domain import *
from .register import GateError
from .locks import file_lock
from .evidence_views import archive,artifact_for,read_artifact,current_valid,integrity_problem


def once(register,key,action,payload,perform):
    if not key.strip() or len(key)>200:raise GateError('Gültiger Idempotenzschlüssel fehlt')
    fingerprint=digest({'action':action,'payload':payload})
    with file_lock(register.settings.control/'.evidence-ui.lock',exclusive=True):
        old=register.connection.execute('SELECT * FROM evidence_ui_action WHERE key=?',(key,)).fetchone()
        if old:
            if old['request_hash']!=fingerprint:raise GateError('Idempotenzschlüssel mit anderem Inhalt verwendet')
            return old['result_id']
        return perform(fingerprint)


def receipt(register,key,fingerprint,action,result):
    register.connection.execute('INSERT INTO evidence_ui_action VALUES(?,?,?,?)',(key,fingerprint,action,str(result)))


def review_view(register,run_id):
    view=archive(register,run_id)
    if not view['run']:raise GateError('Lauf noch nicht gestartet; keine Kriterienurteile')
    reviews=[v for v in view['records'].values() if isinstance(v,CriterionReviewRevision)]
    view['reviews']=reviews
    view['latest']={key:max((v for v in reviews if v.criterion==key),key=lambda v:v.revision,default=None) for key in ('T2','T3','T4','T5')}
    view['measurements']=[m for m in view['records'].values() if isinstance(m,MeasurementAttempt)]
    integrity_cache={}
    view['integration']=[m for m in view['measurements'] if m.measurement_key=='T4_integration' and current_valid(register,m,integrity_cache)]
    view['code_files']=[a for a in view['records'].values() if isinstance(a,Artifact) and a.artifact_type=='candidate_file' and view['candidate'] and a.id in view['candidate'].artifact_ids]
    view['logs']=[a for a in view['records'].values() if isinstance(a,Artifact) and a.run_id==run_id and a.artifact_type!='candidate_file']
    view['open_reviews']=[key for key in ('T2','T3','T4') if not any(v.criterion==key and current_valid(register,v,integrity_cache) for v in reviews)]
    view['validity']={str(v.id):current_valid(register,v,integrity_cache) for v in reviews}
    view['integrity_reasons']={str(v.id):integrity_problem(register,v,integrity_cache) for v in reviews}
    return view


def save_review(register,run_id,data):
    allowed={'idempotency_key','criterion','candidate_hash','rubric_id','tool_id','predecessor_id','completion','verdict','reason','person','code_artifact_id','file_path','lines','measurement_id','interpretation'}
    if set(data)-allowed:raise GateError('Unbekannte Reviewfelder')
    key=data['idempotency_key']
    def perform(fingerprint):
        view=review_view(register,run_id);candidate=view['candidate'];settings=view['version'].settings
        if not candidate or view['state'].seal!='sealed':raise GateError('Manuelle Prüfung benötigt einen versiegelten Kandidaten')
        if data['candidate_hash']!=candidate.tree_hash or data['rubric_id']!=str(settings.rubric_id):raise GateError('Kandidatenhash oder Rubrik verändert')
        criterion=data['criterion']
        if criterion not in ('T2','T3','T4','T5'):raise GateError('Menschliches Formular ausschließlich T2–T5')
        previous=view['latest'][criterion]
        if data.get('predecessor_id','')!=(str(previous.id) if previous else ''):raise GateError('Reviewstand geändert; neue Revision auf aktuellem Vorgänger erfassen')
        if data['completion'] not in ('draft','completed') or data['verdict'] not in ('0','1','open'):raise GateError('Ungültiger Abschluss/Urteilsstatus')
        if not data['reason'].strip():raise GateError('Begründung/Offengrund erforderlich')
        person=data.get('person','').strip()
        phase=register.get(view['phase'].study_id,Study)
        if phase.data_origin=='synthetic' and person and not person.startswith('TECHNICAL-FIXTURE:'):raise GateError('Synthetische Prüfung ausdrücklich mit TECHNICAL-FIXTURE: kennzeichnen')
        comp=Compatibility(candidate_id=candidate.id,candidate_hash=candidate.tree_hash,phase_id=run_id and view['run'].phase_id,
            contract_id=settings.contract_id,suite_id=view['run'].suite_id,tool_id=UUID(data['tool_id']))
        register._compatibility(run_id,comp)
        code_id=UUID(data['code_artifact_id']) if data.get('code_artifact_id') else None
        path=data.get('file_path','').strip() or None;lines=data.get('lines','').strip() or None
        if code_id:
            artifact=artifact_for(register,run_id,code_id);read_artifact(register,code_id)
            if artifact.run_id!=run_id:raise GateError('Reviewbeleg muss zum eigenen Lauf gehören')
            if artifact.artifact_type!='candidate_file' and not (protected_evaluation(artifact) or any(word in artifact.artifact_type for word in ('log','diagnostic','test','trace'))):
                raise GateError('Beleg ist weder versiegelter Code noch konkreter Prüf-/Prozesslog')
            if artifact.artifact_type=='candidate_file':
                if code_id not in candidate.artifact_ids or path!=artifact.original_name or not lines:raise GateError('Codebeleg benötigt exakten versiegelten Dateipfad und konkrete Zeilen')
                import re
                if not re.fullmatch(r'[1-9][0-9]*(?:-[1-9][0-9]*)?',lines):raise GateError('Zeilen als positive Nummer oder Bereich angeben')
                limits=[int(n) for n in lines.split('-')]
                if limits[-1]<limits[0] or limits[-1]>len(read_artifact(register,code_id).splitlines()):raise GateError('Codezeile außerhalb des versiegelten Inhalts')
        mids=(UUID(data['measurement_id']),) if data.get('measurement_id') else ()
        for mid in mids:
            m=register.get(mid,MeasurementAttempt)
            if m.run_id!=run_id or m.compatibility!=comp or not current_valid(register,m):raise GateError('Ungültiger/fremder/inkompatibler Messbeleg')
            for aid in m.raw_artifact_ids:read_artifact(register,aid)
            if criterion=='T4' and (m.measurement_key!='T4_integration' or not m.raw_artifact_ids or
                any(register.get(aid,Artifact).producer!='trusted_evaluator' for aid in m.raw_artifact_ids)):
                raise GateError('T4 benötigt unabhängige T4_integration mit Eingaben und Logs')
        if data['completion']=='completed' and integrity_problem(register,candidate):raise GateError('Versiegelter Kandidatenbeleg beschädigt; Abschluss benötigt intakte Quellen')
        if data['completion']=='completed' and data['verdict']=='open':raise GateError('Offenes Urteil nur begründet als Entwurf speichern')
        if data['completion']=='completed' and criterion=='T4' and not code_id:raise GateError('T4 benötigt zusätzlich konkreten Code-/Logbeleg')
        verdict=BinaryObservation(status='observed',value=int(data['verdict']),unit='binary',source='Menschliche lokale Selbstauskunft') if data['verdict']!='open' else BinaryObservation(status='pending',unit='binary',reason=data['reason'])
        # Interpretation explicitly identified inside the existing reason contract, never a new truth catalog.
        reason=data['reason'].strip()
        if data.get('interpretation','').strip():reason+='\nInterpretation (vermutete Ursache): '+data['interpretation'].strip()
        revision=CriterionReviewRevision(code='HUMAN-REVIEW-'+str(uuid4()),run_id=run_id,criterion=criterion,compatibility=comp,rubric_id=settings.rubric_id,
            revision=previous.revision+1 if previous else 1,predecessor_id=previous.id if previous else None,completion=data['completion'],verdict=verdict,
            reason=reason,file_path=path,lines=lines,code_artifact_id=code_id,measurement_ids=mids,person=person,reviewer_origin='human',reviewed_at=datetime.now(timezone.utc))
        with register.transaction():
            register._put(revision)
            state=register.state(run_id)
            if state.evaluation=='complete':
                register.set_state_in_transaction(run_id,state.model_copy(update={'evaluation':'manual_pending',
                    'result':Observation(status='pending',unit='F',reason='Bewertung nach Abschluss ergänzt; neue Bestätigung erforderlich')}),
                    reason='Neue menschliche Bewertungsrevision: '+str(revision.id))
            receipt(register,key,fingerprint,'review',revision.id)
        return str(revision.id)
    return once(register,key,'review',{'run_id':str(run_id),**data},perform)


def save_guided_review(register,run_id,data):
    """Resolve fixed technical fields from selected evidence, never a human verdict."""
    view=review_view(register,run_id)
    data=dict(data)
    if view['study'].data_origin=='synthetic' and data.get('person','').strip() and not data['person'].startswith('TECHNICAL-FIXTURE:'):
        data['person']='TECHNICAL-FIXTURE: '+data['person'].strip()
    if data.get('code_artifact_id'):
        artifact=artifact_for(register,run_id,UUID(data['code_artifact_id']))
        if artifact.artifact_type=='candidate_file':data['file_path']=artifact.original_name
    return save_review(register,run_id,data)


def proposal_view(register,proposal_id):
    from .analysis_store import missing_decisions
    row=register.connection.execute('SELECT * FROM analysis_selection WHERE proposal_id=?',(str(proposal_id),)).fetchone()
    if not row:raise GateError('Analysevorschlag fehlt')
    snapshot=json.loads(read_artifact(register,UUID(row['snapshot_id'])))
    if digest(snapshot)!=row['input_hash']:raise GateError('Analyseeingabe beschädigt')
    previous=[a for a in register.all(AnalysisRun) if a.phase_id==UUID(snapshot['phase_id'])]
    previous=max(previous,key=lambda a:a.confirmed_at) if previous else None
    changes=[]
    for pid,selection in snapshot['selected_revisions'].items():
        old=previous.selected_inputs.get(pid) if previous else None
        if old!=selection:changes.append({'planned_id':pid,'previous_analysis_id':str(previous.id) if previous else None,'before':old,'proposed':selection})
    return {'proposal':dict(row),'snapshot':snapshot,'changes':changes,'open_decisions':missing_decisions(snapshot),'confirmed':register.connection.execute('SELECT analysis_id FROM analysis_confirmation WHERE proposal_id=?',(str(proposal_id),)).fetchone()}


def selection_history(register,snapshot):
    histories=[]
    integrity_cache={}
    for row in snapshot['rows']:
        for group,cls,attr in (('measurements',MeasurementAttempt,'measurement_key'),('reviews',CriterionReviewRevision,'criterion')):
            revisions=register.for_run(cls,row['run_id']) if row['run_id'] else []
            for key,selected in row['selection'][group].items():
                sequence=[]
                for value in revisions:
                    if getattr(value,attr)!=key:continue
                    valid=current_valid(register,value,integrity_cache)
                    run=register.get(value.run_id,Run)
                    version=register.get(run.configuration_version_id,ConfigurationVersion)
                    compatible=(value.compatibility.candidate_hash==row['candidate_hash'] and value.compatibility.phase_id==UUID(snapshot['phase_id'])
                        and value.compatibility.contract_id==version.settings.contract_id and value.compatibility.suite_id==run.suite_id
                        and register.compatible_evaluation_tool(run.id,value.compatibility.tool_id)
                        and (not isinstance(value,CriterionReviewRevision) or value.rubric_id==version.settings.rubric_id))
                    reason='ausgewählt: letzte abgeschlossene gültige kompatible Revision' if str(value.id)==selected['revision_id'] else 'Entwurf' if value.completion!='completed' else 'ungültig oder abhängiger Beleg invalidiert' if not valid else 'inkompatibel' if not compatible else 'ältere gültige Revision; feste Regel'
                    sequence.append({'id':str(value.id),'number':value.revision,'reason':reason,'valid':valid,'compatibility':value.compatibility.model_dump(mode='json')})
                histories.append({'planned_id':row['plan']['id'],'run_id':row['run_id'],'group':group,'key':key,'selected':selected,'sequence':sequence})
    return histories


def process_analysis(register,key,intent):
    """Worker-only CAS writes. The ordinary web mount remains read-only."""
    from .analysis_store import Analyses
    from .adapter import CallJournal
    from .preparation import software_identity
    def perform(fingerprint):
        service=Analyses(register,CallJournal.cost_view(register))
        if intent.action=='analysis_propose':
            result=service.propose(intent.target_id)
            result_id=result['proposal_id']
        else:
            view=proposal_view(register,intent.target_id)
            result=service.confirm(intent.target_id,expected_input_hash=intent.base_hash,
                acknowledged_selection=view['snapshot']['selected_revisions'],acknowledged_open=view['open_decisions'],
                confirmed_by=intent.person,decision=intent.decision,software_commit=software_identity(),synthetic=view['snapshot']['data_origin']=='synthetic')
            result_id=str(result.id)
        with register.transaction():receipt(register,key,fingerprint,intent.action,result_id)
        return result_id
    result_id=once(register,key,intent.action,intent.model_dump(mode='json'),perform)
    return {'url':('/analyses/proposals/' if intent.action=='analysis_propose' else '/analyses/')+result_id}


GUIDES = {
    'T2': {'title':'Controller und Routen', 'question':'Laufen alle Modulaktionen über einen eigenen Controller?',
           'steps':['Öffne routes/study.php. Suche dort die Routen deines Moduls.',
                    'Folge jeder Route in die angegebene Controller-Methode.',
                    'Prüfe, ob dort die Modulaktion verarbeitet wird. Ein bloßes include der ausführbaren Legacy-PHP-Datei reicht nicht.'],
           'pass':'Jede Modulaktion führt über eine registrierte Web-Route zu einer Methode eines modulspezifischen Controllers. Der Controller darf nicht nur ein ausführbares Legacy-Skript einbinden.',
           'example':'Beschreibe, welche Route welche Methode aufruft. Nenne die Route und den zugehörigen Controller mit Zeilen.'},
    'T3': {'title':'Blade-Views', 'question':'Werden Formular und Ergebnis tatsächlich mit Blade dargestellt?',
           'steps':['Öffne den Controller und suche den Aufruf der View.',
                    'Öffne die zugehörige Datei unter resources/views/study/.',
                    'Prüfe beide Ausgaben: das Formular und die Anzeige des Ergebnisses. Vollständig direkt erzeugtes HTML reicht nicht.'],
           'pass':'Der Controller verwendet Blade für das Formular und für das Ergebnis. Layout und konkrete Dateinamen werden nicht bewertet.',
           'example':'Nenne den View-Aufruf im Controller und die Blade-Datei. Beschreibe, wo Formular und Ergebnis entstehen.'},
    'T4': {'title':'Laravel-Schnittstellen', 'question':'Verwendet das Modul die vorgesehenen Laravel-Schnittstellen?',
           'steps':['Lies im Controller, wie Daten eingelesen und verarbeitet werden.',
                    'Prüfe die Laravel-Datenbankanbindung; bei File Upload die Request-/Dateischnittstellen und den vereinbarten Speicher.',
                    'Öffne den unabhängigen Integrationsbefund unten. Gleiche die wechselnden Eingaben und Ergebnisse mit dem Codepfad ab.'],
           'pass':'Die vorgesehene Laravel-Anbindung wird tatsächlich verwendet. Die Antworten sind nicht fest eingebaut. Eloquent und Query Builder sind gleichwertig. Eine eigene globale mysqli-/SQLite-Verbindung erfüllt dieses Kriterium nicht.',
           'example':'Nenne die verwendeten Laravel-Schnittstellen und die Codezeilen. Beschreibe zusätzlich den zugehörigen Integrationsbefund.'},
    'T5': {'title':'Grundlagen unverändert', 'question':'Sind die gemeinsamen Grundlagen unverändert geblieben?',
           'steps':['Öffne das Dateimanifest und den Vergleich mit dem Gerüst.',
                    'Prüfe gemeinsame Konfiguration, Schema, Zugang und Abhängigkeiten.',
                    'Kläre den konkreten semantischen Zweifel: Liegt die Modullogik im erlaubten Bereich und bleiben interne Tests getrennt?'],
           'pass':'Gemeinsame Grundlagen sind unverändert; Modullogik liegt ausschließlich im erlaubten Bereich.',
           'example':'Beschreibe den konkreten Zweifel und den Vergleich, mit dem du ihn geprüft hast.'}
}


def guide(register,view,criterion=None):
    """Navigation and reading aids derived from the fixed rubric; never verdicts."""
    if criterion is not None and criterion not in GUIDES:raise GateError('Unbekanntes Prüfkriterium')
    selected=criterion or next(iter(view['open_reviews']),None)
    files=view['code_files']
    def relevant(artifact):
        path=artifact.original_name or ''
        if path in ('app/Http/Controllers/Controller.php','app/Http/Controllers/StudyAccessController.php'):return False
        if selected=='T2':return path=='routes/study.php' or path.startswith('app/Http/Controllers/')
        if selected=='T3':return path.startswith(('app/Http/Controllers/','resources/views/study/'))
        if selected=='T4':return path=='routes/study.php' or path.startswith(('app/Http/Controllers/','app/Models/','config/filesystems'))
        return True
    useful=sorted((a for a in files if relevant(a)),key=lambda a:a.original_name or '')
    # The incomplete source can itself be inspected; absence of an expected file
    # is not silently replaced with a reference implementation.
    other=sorted((a for a in files if a not in useful and (a.original_name or '').endswith('.php')),key=lambda a:a.original_name or '')
    def line_count(artifact):
        try:return len(read_artifact(register,artifact.id).splitlines())
        except (OSError,ValueError):return None
    return {'criterion':selected,'guide':GUIDES.get(selected),'guides':GUIDES,
        'suggested_files':useful,'other_files':other,'file_lines':{str(a.id):line_count(a) for a in useful+other},'person_hint':__import__('research_env.application_settings',fromlist=['preferences']).preferences(register).get('person','')}

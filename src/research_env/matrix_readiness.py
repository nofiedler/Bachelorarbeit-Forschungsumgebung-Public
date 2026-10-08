"""Concrete, read-only preparation checks; no inferred human approval or model calls."""
import hashlib
import json
from pathlib import Path
from .domain import AssetVersion, ModelPackage, MAIN_CELLS, digest
from .register import GateError


def checks(register, versions):
    from .preparation import ROOT, software_identity, software_compatible
    from .application_settings import default_settings
    from .context_assets import package_path, contract_binding
    from .role_formats import PROMPTS_HASH, CONTRACT_HASH
    rows=[]
    def check(label, action):
        try: action(); rows.append({'label':label,'ok':True,'detail':'Geprüft'})
        except (ValueError, OSError, KeyError, TypeError) as exc: rows.append({'label':label,'ok':False,'detail':str(exc)})
    def demand(condition, message):
        if not condition: raise GateError(message)
    if not versions: return [{'label':'Konfigurationen','ok':False,'detail':'Matrixentwurf fehlt'}]
    s=next(iter(versions.values())).settings
    check('Zwölf gemeinsame Bedingungen',lambda: demand(set(versions)==set(MAIN_CELLS) and len({digest(v.settings) for v in versions.values()})==1,'Die zwölf Bedingungen müssen dieselben gemeinsamen Einstellungen verwenden'))
    for field,label in (('model_a','Modellpaket A'),('model_b','Modellpaket B')):
        check(label,lambda field=field,label=label: demand(getattr(s,field) is not None,label+' fehlt. Wähle das Modell oben in den gemeinsamen Einstellungen.'))
    def models():
        packages=[register.get(mid,ModelPackage) for mid in (s.model_a,s.model_b) if mid]
        demand(len(packages)==2,'Modellpakete A und B auswählen')
        if any(not m.endpoint.startswith('mock://') for m in packages):
            demand(all(not m.endpoint.startswith('mock://') and m.upstream.strip() for m in packages),'Beide Modelle benötigen einen festen realen Anbieter')
            demand(packages[0].exact_model_id!=packages[1].exact_model_id,'Zwei unterschiedliche Modelle A und B auswählen')
        for v in versions.values():
            register._validate_settings(v.settings,complete=False)
            for role in ('analyzer','planner','migrate','test','review','repair'): register.resolve_call_parameters(v,role)
    check('Modelle und Rollenparameter',models)
    def assets():
        demand(bool(s.retry_interval_seconds is not None),'Intervall für einen technischen Wiederholungsversuch fehlt')
        expected=default_settings(register,research=True,compatible_with=s)
        for key,label in (('contract_id','Öffentlicher Vertrag'),('rubric_id','Bewertungsregeln'),('scaffold_id','Laravel-Grundgerüst'),('development_suite_id','Entwicklungstests'),('holdout_suite_id','Zurückgehaltene Akzeptanztests'),('reference_id','Referenzlösungen'),('dvwa_commit','DVWA-Version')):
            demand(getattr(s,key)==getattr(expected,key),label+' fehlt oder ist veraltet. Installierte Grundlagen für diesen Entwurf aktualisieren.')
        demand(s.context_ids==expected.context_ids,'Die sechs Kontextpakete fehlen oder sind veraltet. Installierte Grundlagen aktualisieren.')
        contract_binding(ROOT)
        lock=json.loads(Path(__file__).with_name('pipeline_assets.lock.json').read_text())
        for relative,sha in lock.items():
            demand(hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()==sha,'Installierte Datei verändert: '+relative)
        for v in versions.values(): package_path(register,v,strict=True)
        demand(len(s.prompt_ids)==1 and register.get(s.prompt_ids[0],AssetVersion).manifest_hash==PROMPTS_HASH,'Rollenanweisungen fehlen oder sind veraltet')
        demand(s.handoff_id is not None and register.get(s.handoff_id,AssetVersion).manifest_hash==CONTRACT_HASH,'Rollenübergaben fehlen oder sind veraltet')
    check('Vertrag, Grundgerüst und Kontextpakete',assets)
    def instruments():
        expected=default_settings(register,research=True,compatible_with=s)
        demand(set(s.tool_ids)==set(expected.tool_ids),'Messinstrument nach Softwareupdate veraltet. Installierte Grundlagen für diesen Entwurf aktualisieren.')
        demand(software_compatible(s.software_commit),'Softwarestand des Entwurfs veraltet. Installierte Grundlagen für diesen Entwurf aktualisieren.')
        lock=json.loads(Path(__file__).with_name('evaluation_assets.lock.json').read_text())
        for relative,files in lock.items():
            for name,sha in files.items():
                demand(hashlib.sha256((ROOT/'evaluation'/relative/name).read_bytes()).hexdigest()==sha,'Testdatei verändert: '+relative+'/'+name)
    check('Messinstrumente und Softwarestand',instruments)
    def isolation():
        for field in ('holdout_suite_id','reference_id'):
            demand(getattr(s,field) is not None,'Zurückgehaltene Tests oder Referenzlösungen fehlen')
            demand(register.get(getattr(s,field),AssetVersion).access_scope=='trusted_evaluator','Tests und Referenzlösungen müssen ausschließlich dem Evaluator zugänglich sein')
        public=(*s.context_ids.values(),*s.prompt_ids,s.handoff_id,s.contract_id,s.scaffold_id)
        demand(not any(register.get(ref,AssetVersion).access_scope=='trusted_evaluator' for ref in public if ref),'Geschützte Bewertung darf nicht als Modellkontext verwendet werden')
    check('Trennung von Modellkontext und Bewertung',isolation)
    return rows


def require_ready(register, versions):
    errors=[row['detail'] for row in checks(register,versions) if not row['ok']]
    if errors: raise GateError('Voraussetzungen offen: '+'; '.join(errors))

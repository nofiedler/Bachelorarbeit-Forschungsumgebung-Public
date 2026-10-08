"""Local setup and versioned public model metadata, outside model role access."""
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import tempfile
from uuid import UUID

import httpx

from .artifacts import ArtifactStore
from .domain import AssetVersion, EffectiveSettings, ModelPackage, Observation, canonical, digest
from .register import GateError


def preferences(register):
    from .role_formats import structured_parameters
    row = register.connection.execute('SELECT body FROM application_settings WHERE id=1').fetchone()
    return {'person': '', 'dvwa_path': '', 'context_version': 'm2-v0.1-csrf1',
            'role_parameters': structured_parameters(), 'retry_interval_seconds': '1',
            **(json.loads(row[0]) if row else {})}


def save_preferences(register, data):
    allowed = {'person', 'dvwa_path', 'context_version', 'role_parameters', 'retry_interval_seconds'}
    if set(data) != allowed:
        raise GateError('Unbekannte oder fehlende Einstellungsfelder')
    values = dict(data)
    values['role_parameters'] = json.loads(values['role_parameters'])
    from .providers import PARAMETERS
    params = values['role_parameters']
    if not isinstance(params, dict) or set(params) - {'all', 'analyzer', 'planner', 'migrate', 'test', 'review', 'repair'}:
        raise GateError('Modellparameter benötigen all oder bekannte Rollennamen')
    if any(not isinstance(v, dict) or set(v) - PARAMETERS for v in params.values()):
        raise GateError('Unbekannte Modellparameter; Zugangswerte gehören ausschließlich ins Schlüsselfeld')
    if any(set(v) & {'max_tokens', 'max_completion_tokens'} for v in params.values()):
        raise GateError('Anwendungseigene Tokenlimits sind im Versuchsplan nicht vorgesehen')
    if 'sk-or-' in canonical(values) or re.search(r'bearer\s+', canonical(values), re.I):
        raise GateError('Keine Zugangswerte in allgemeinen Einstellungen speichern')
    interval = Decimal(values['retry_interval_seconds'])
    if not interval.is_finite() or interval < 0:
        raise GateError('Retryintervall muss eine nichtnegative Zahl sein')
    if values['context_version'] != 'm2-v0.1-csrf1':
        raise GateError('Nur die installierte, geprüfte Kontextversion kann ausgewählt werden')
    if values['dvwa_path'] and not Path(values['dvwa_path']).is_absolute():
        raise GateError('DVWA benötigt einen absoluten lokalen Quellpfad')
    with register.transaction():
        register.connection.execute('INSERT INTO application_settings VALUES(1,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body', (canonical(values),))


def secret_path(settings):
    # A separate volume, never the database, CAS, checkpoint or backup trees.
    root = settings.secrets or settings.control.parent / 'secrets'
    for protected in (settings.control, settings.artifacts, settings.checkpoints, settings.staging):
        if root.resolve().is_relative_to(protected.resolve()):
            raise GateError('Schlüsselspeicher muss außerhalb der Forschungsdaten liegen')
    return root / 'openrouter.key'


def key_present(settings):
    path = secret_path(settings)
    return path.is_file() and path.stat().st_size > 0


def save_key(settings, value):
    value = value.strip()
    if not re.fullmatch(r'sk-or-v1-[A-Za-z0-9_-]{16,}', value):
        raise GateError('Bitte einen gültigen OpenRouter-Schlüssel eingeben')
    path = secret_path(settings)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.key-')
    try:
        with os.fdopen(fd, 'w') as file:
            file.write(value)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def model_key(settings):
    path = secret_path(settings)
    if path.is_file():
        if path.is_symlink(): raise GateError('Schlüsselspeicher darf kein Symlink sein')
        return path.read_text().strip()
    return os.environ.get('OPENROUTER_API_KEY')


def installed_tools(register):
    from .evaluation import instrument_manifest as functional
    from .static_analysis import instrument_manifest as static
    from .sandbox_runtime import RuntimeImages
    images = RuntimeImages(**json.loads(Path(__file__).with_name('pipeline_runtime.lock.json').read_text()))
    existing = {a.code: a for a in register.all(AssetVersion)}
    result = []
    store = ArtifactStore(register.settings, register)
    for label, manifest in [('functional', functional(images)), ('static', static(images))]:
        sha = digest(manifest)
        code = 'WORKFLOW-' + label + '-' + sha
        value = existing.get(code)
        if value is None:
            proof = store.json(manifest, artifact_type='installed_measurement_instrument', producer='trusted_evaluator', access_scope='trusted_register')
            value = register.add(AssetVersion(code=code, asset_type='tool', manifest_hash=sha,
                artifact_ids=(proof.id,), origin='Installiertes Messinstrument; technische Bindung, menschliche Fachabnahme offen', access_scope='trusted_register'))
        result.append(value.id)
    return tuple(result)


def default_settings(register, *, research=False, compatible_with=None):
    assets = {a.code: a for a in register.all(AssetVersion)}
    def asset(code):
        if code not in assets: raise GateError('Einrichtung noch nicht abgeschlossen. Unter Einstellungen die Grundausstattung vorbereiten.')
        return assets[code].id
    prefs = preferences(register)
    # Tools are looked up by their current hash, so a software update cannot silently
    # rebind an existing run. initialize() creates them in the trusted worker.
    from .evaluation import instrument_manifest as functional
    from .static_analysis import instrument_manifest as static
    from .sandbox_runtime import RuntimeImages
    images = RuntimeImages(**json.loads(Path(__file__).with_name('pipeline_runtime.lock.json').read_text()))
    if compatible_with is None:
        tool_ids = tuple(asset('WORKFLOW-' + label + '-' + digest(factory(images))) for label, factory in [('functional', functional), ('static', static)])
    else:
        # Read-only validation of existing frozen identities; never register or
        # silently select replacement tools while viewing/starting an old matrix.
        from .instrument_compatibility import instrument_compatible
        matches = [tuple(tool for tool in compatible_with.tool_ids
                         if instrument_compatible(register, tool, factory(images), compatible_with.software_commit))
                   for factory in (functional, static)]
        if len(compatible_with.tool_ids) != 2 or any(len(found) != 1 for found in matches):
            raise GateError('Die fixierten Messinstrumente passen nicht zum installierten Softwarestand. Passenden Softwarestand verwenden; die Matrix bleibt unverändert.')
        tool_ids = tuple(found[0] for found in matches)
    from .preparation import software_identity
    from .role_formats import PROMPTS_HASH, CONTRACT_HASH
    def role_asset(kind, sha):
        found = [a for a in assets.values() if a.asset_type == kind and a.manifest_hash == sha]
        if not found: raise GateError('Rollenanweisungen aktualisiert. Grundausstattung unter Einstellungen vorbereiten.')
        return found[-1].id
    return EffectiveSettings(contract_id=asset('M8-contract-current'), rubric_id=asset('M2-v0.1-rubric'),
        scaffold_id=asset('M2-v0.1-scaffold'), development_suite_id=asset('WORKFLOW-development-v1'),
        holdout_suite_id=asset('WORKFLOW-study_holdout-v1') if research else None,
        reference_id=asset('M2-v0.1-references') if research else None,
        context_ids={f'{m}-K{k}':asset(f'M8-current-{m}-K{k}') for m in ('BF','SQL','UP') for k in (0,1)},
        prompt_ids=(role_asset('prompt', PROMPTS_HASH),),
        handoff_id=role_asset('handoff', CONTRACT_HASH),
        tool_ids=tool_ids, role_parameters=prefs['role_parameters'], retry_interval_seconds=Decimal(prefs['retry_interval_seconds']),
        dvwa_commit='b496a5d3de6b967410155e1b7d3e51e9d035eb22', software_commit=software_identity())


def initialize(register):
    from .preparation import imported_catalog
    imported_catalog(register)
    lock=json.loads(Path(__file__).with_name('evaluation_assets.lock.json').read_text())
    existing={a.code:a for a in register.all(AssetVersion)}
    for kind,relative in [('development','development/m6-v1'),('study_holdout','study_holdout/m2-v0.1')]:
        code='WORKFLOW-'+kind+'-v1'
        if code not in existing:
            register.add(AssetVersion(code=code,asset_type='suite',manifest_hash=digest(lock[relative]),
                origin='Installierter unabhängiger Evaluator: evaluation/'+relative,suite_kind=kind,
                access_scope='public_development' if kind=='development' else 'trusted_evaluator'))
        elif existing[code].manifest_hash!=digest(lock[relative]):
            raise GateError('Testsatz verändert; neue registrierte Version erforderlich')
    installed_tools(register)
    return {'url':'/settings?ready=1'}


def lookup_model(register, model_id, *, client=None):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.:-]+', model_id):
        raise GateError('Modell-ID im Format anbieter/modell eingeben')
    url = 'https://openrouter.ai/api/v1/models/' + model_id + '/endpoints'
    owned = client is None
    client = client or httpx.Client(timeout=30, follow_redirects=False, trust_env=False)
    try:
        response = client.get(url)
        if response.status_code != 200:
            raise GateError(f'Modellmetadaten nicht verfügbar (HTTP {response.status_code}); kein Modellaufruf durchgeführt')
        body = response.json()
        if body.get('data', {}).get('id') != model_id or not body['data'].get('endpoints'):
            raise GateError('Keine eindeutigen Endpunkte für diese Modell-ID verfügbar')
        retrieved = datetime.now(timezone.utc).isoformat()
        proof = ArtifactStore(register.settings, register).store(response.content, artifact_type='openrouter_endpoint_source',
            mime_type='application/json', original_name='openrouter-endpoints.json')
        with register.transaction():
            register.connection.execute('INSERT INTO model_catalog VALUES(?,?,?) ON CONFLICT(model_id) DO UPDATE SET artifact_id=excluded.artifact_id,retrieved_at=excluded.retrieved_at', (model_id,str(proof.id),retrieved))
        return {'url':'/settings#models'}
    except httpx.HTTPError:
        raise GateError('OpenRouter-Metadaten konnten nicht geladen werden. Verbindung prüfen; kein Modellaufruf durchgeführt.') from None
    finally:
        if owned: client.close()


def catalog(register):
    store = ArtifactStore(register.settings, register)
    return [{**dict(row), 'data':json.loads(store.read(UUID(row['artifact_id'])))['data']} for row in register.connection.execute('SELECT * FROM model_catalog ORDER BY retrieved_at DESC')]


def import_model(register, model_id, endpoint, parameters):
    row = register.connection.execute('SELECT * FROM model_catalog WHERE model_id=?',(model_id,)).fetchone()
    if not row: raise GateError('Zuerst aktuelle Modellmetadaten laden')
    store = ArtifactStore(register.settings, register)
    data = json.loads(store.read(UUID(row['artifact_id'])))['data']
    choices = [e for e in data['endpoints'] if e.get('tag') == endpoint and e.get('model_id') == model_id]
    if len(choices) != 1: raise GateError('Gewählten Provider-Endpunkt nicht eindeutig gefunden')
    value = choices[0]
    evidence = {'schema':'endpoint-v1', 'model_id':model_id, 'endpoint_slug':endpoint,
        'provider_name':value['provider_name'], 'endpoint_is_complete':True,
        'source_url':'https://openrouter.ai/api/v1/models/'+model_id+'/endpoints', 'retrieved_at':row['retrieved_at'],
        'source_artifact_id':row['artifact_id'], 'supported_parameters':value.get('supported_parameters',[]),
        'limits_defaults':{'context_length':value.get('context_length'), 'max_completion_tokens':value.get('max_completion_tokens'),
            'defaults':'Vom Endpointkatalog nicht vollständig angegeben; nicht gesetzte Parameter verwenden Anbieterdefaults.'},
        'required_parameters':{}, 'pricing':value.get('pricing',{}), 'native_endpoint':value}
    def limit(key):
        n=value.get(key)
        return Observation(status='observed',value=n,unit='tokens',source=evidence['source_url']) if isinstance(n,int) and n>=0 else Observation(status='unresolved',unit='tokens',reason='Nicht im Endpointkatalog angegeben')
    uncertainty = 'Modell-ID und Provider-Endpunkt fest; unveränderte serverseitige Gewichte sind nicht nachweisbar. Technische Eignung noch nicht pilotiert.'
    proof = store.json(evidence, artifact_type='openrouter_endpoint_metadata')
    package = ModelPackage(code='OR-'+digest({'evidence':evidence,'parameters':parameters}), exact_model_id=model_id,
        endpoint=endpoint,upstream=value['provider_name'],routing={'only':[endpoint],'order':[endpoint],'allow_fallbacks':False,'require_parameters':True},
        fallback={}, supported_parameters=tuple(value.get('supported_parameters',[])), effective_parameters=parameters,
        context_limit=limit('context_length'),output_limit=limit('max_completion_tokens'),
        price=Observation(status='unresolved',unit='USD',reason='Kategoriepreise im Metadatenbeleg; keine pauschale Gebühr pro Lauf'),
        currency='USD',price_as_of=datetime.fromisoformat(row['retrieved_at']),metadata_evidence_ids=(proof.id,),version_uncertainty=uncertainty)
    from .providers import OpenRouterAdapter
    adapter=OpenRouterAdapter(api_key=None)
    try: adapter.validate_capabilities(package,parameters,evidence)
    finally: adapter.close()
    old=next((m for m in register.all(ModelPackage) if m.code==package.code),None)
    if old is None: register.add(package)
    else:
        with register.transaction():register.connection.execute('DELETE FROM catalog_visibility WHERE record_id=?',(str(old.id),))
    return {'url':'/settings#models'}


def model_prices(register, models):
    """Display archived token tariffs, retaining tier thresholds and unknowns."""
    prices={};store=ArtifactStore(register.settings,register)
    keys=('prompt','completion','input_cache_read','input_cache_write','input_cache_write_1h')
    def token_rates(pricing):
        rates={}
        for key in keys:
            try:
                number=Decimal(str(pricing[key]))
                if number.is_finite():
                    text=format(number*1000000,'f');rates[key]=text.rstrip('0').rstrip('.') if '.' in text else text
            except (ValueError,KeyError,TypeError,ArithmeticError):continue
        return rates
    for model in models:
        value={}
        for identity in model.metadata_evidence_ids:
            try:
                evidence=json.loads(store.read(identity));pricing=evidence.get('pricing',{})
                value.update(token_rates(pricing))
                value['tiers']=[{'threshold':tier.get('min_prompt_tokens'),**token_rates(tier)} for tier in pricing.get('overrides',[]) if isinstance(tier,dict)]
            except (ValueError,OSError,KeyError,TypeError,ArithmeticError):continue
        prices[str(model.id)]=value
    return prices

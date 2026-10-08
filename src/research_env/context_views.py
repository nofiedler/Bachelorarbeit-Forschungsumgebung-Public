"""Read the exact public package; never include holdout or other run data."""
import hashlib
import json
from pathlib import Path
from .artifacts import IntegrityError,read_regular
from .register import GateError
from .role_formats import PROMPTS

ROOT=Path(__file__).resolve().parents[2]
ROLES=[('analyzer','Analyzer','Keine vorherigen Agentenausgaben.'),
       ('planner','Planner','Analyse des Analyzers. Entfällt bei Planner aus.'),
       ('migrate','Migration','Analyse und – falls aktiviert – der Plan.'),
       ('test','Test-Agent','Erzeugter Code, Analyse und vorhandener Plan.'),
       ('review','Review','Erzeugter Code, Analyse, vorhandener Plan, interne Tests und deren Ergebnis. Entfällt bei Review aus.'),
       ('repair','Überarbeitung','Erzeugter Code, Analyse, vorhandener Plan, interne Tests, deren Ergebnis und vorhandenes Review. Nur bei Bedarf, höchstens einmal.')]


def package(module,context,version='m2-v0.1-csrf1'):
    if module not in ('BF','SQL','UP') or context not in ('K0','K1') or version not in ('m2-v0.1','m2-v0.1-csrf1'):
        raise GateError('Unbekanntes Modul oder Kontextpaket')
    folder=f'assets/context/{version}/{module}/{context}'
    manifest_raw=read_regular(ROOT,folder+'/manifest.json')[0]
    locked=json.loads(Path(__file__).with_name('pipeline_assets.lock.json').read_text())
    if hashlib.sha256(manifest_raw).hexdigest()!=locked[folder+'/manifest.json']:raise IntegrityError('Kontextmanifest verändert')
    manifest=json.loads(manifest_raw)
    content=read_regular(ROOT,folder+'/package.txt')[0]
    if hashlib.sha256(content).hexdigest()!=manifest['package_sha256']:raise IntegrityError('Kontextpaket verändert')
    files=[]
    for item in manifest['files']:
        raw=read_regular(ROOT,folder+'/'+item['path'])[0]
        if hashlib.sha256(raw).hexdigest()!=item['sha256']:raise IntegrityError('Kontextdatei verändert')
        files.append({**item,'content':raw.decode()})
    files.sort(key=lambda item:(0 if item['path']=='legacy/low.php' else 1 if item['path'].startswith('integration/') else 2,item['path']))
    return {'module':module,'context':context,'context_version':version,'manifest':manifest,'package_text':content.decode(),
            'files':files,'roles':[{'id':key,'name':label,'inputs':inputs,'prompt':PROMPTS['roles'][key]} for key,label,inputs in ROLES]}

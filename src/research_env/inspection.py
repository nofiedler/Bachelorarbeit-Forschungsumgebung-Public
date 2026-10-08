"""Portable, isolated inspection of a copy; never runs or rewrites the sealed code."""
import base64
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4
import zipfile

from .artifacts import ArtifactStore, read_regular
from .domain import Artifact, CandidateSnapshot, Run, canonical
from .register import GateError
from .snapshots import Snapshots, inventory

EXPORT_VERSION='v3'

def folder_name(run_id):return 'laravel-'+str(run_id)+'-'+EXPORT_VERSION


def local_copy(register,run_id):
    settings=register.settings
    if not settings.inspections or not settings.inspections_host:return None
    folder=folder_name(run_id)
    path=settings.inspections/folder
    if path.is_symlink() or not (path/'inspection-manifest.json').is_file():return None
    manifest=json.loads(read_regular(path,'inspection-manifest.json')[0])
    if manifest['run_id']!=str(run_id):raise GateError('Prüfkopie gehört nicht zu diesem Lauf')
    return {'project':str(Path(settings.inspections_host)/folder/'laravel'),
            'folder':str(Path(settings.inspections_host)/folder),'candidate_hash':manifest['tree_hash']}


def publish_local(register,run_id,content):
    """Unpack the verified own export into the separately mounted host folder."""
    base=register.settings.inspections
    if not base:return
    if base.is_symlink() or not base.is_dir():raise GateError('Lokaler Prüfkopienordner ist nicht korrekt eingebunden')
    destination=base/folder_name(run_id)
    if destination.exists():
        if destination.is_symlink():raise GateError('Prüfkopienordner darf kein Symlink sein')
        return  # Never overwrite an existing human inspection copy.
    temporary=Path(tempfile.mkdtemp(prefix='.prepare-',dir=base))
    try:
        from .artifacts import relative_path
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            for info in archive.infolist():
                relative_path(info.filename)
                target=temporary/info.filename
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(archive.read(info))
                target.chmod((info.external_attr>>16)&0o777)
        temporary.chmod(0o755)
        os.rename(temporary,destination)
    finally:
        if temporary.exists():shutil.rmtree(temporary)


def latest(register, run_id):
    values=[a for a in register.all(Artifact) if a.run_id==run_id and a.artifact_type=='laravel_inspection_zip' and a.original_name==folder_name(run_id)+'.zip']
    return values[-1] if values else None


def prepare(register, run_id):
    register.get(run_id,Run)
    state=register.state(run_id)
    if state.seal!='sealed' or not state.candidate_id:raise GateError('Prüfkopie benötigt einen versiegelten Kandidaten')
    store=ArtifactStore(register.settings,register)
    old=latest(register,run_id)
    if old:
        publish_local(register,run_id,store.read(old.id))
        return {'url':'/runs/'+str(run_id)+'/code#inspection'}
    candidate=register.get(state.candidate_id,CandidateSnapshot)
    root=Path(tempfile.mkdtemp(prefix='inspection-',dir=register.settings.staging))
    try:
        project=root/'laravel'
        Snapshots(store).inspection_copy(candidate.id,project)
        runtime=json.loads(Path(__file__).with_name('pipeline_runtime.lock.json').read_text())
        # These credentials are fresh local demo values, never the provider key.
        access=uuid4().hex;password=uuid4().hex
        env={'APP_KEY':'base64:'+base64.b64encode(os.urandom(32)).decode(),'APP_ENV':'local','APP_DEBUG':'false',
             'APP_URL':'http://127.0.0.1:8160','DB_CONNECTION':'mysql','DB_HOST':'db','DB_DATABASE':'study',
             'DB_USERNAME':'study','DB_PASSWORD':password,'STUDY_ACCESS_TOKEN':access,'LOG_CHANNEL':'stderr'}
        setup="foreach(['app/study/uploads','framework/sessions','framework/cache/data','framework/cache/locks','framework/views','framework/testing','logs'] as $p) { if (!is_dir('/opt/study/storage/'.$p) && !mkdir('/opt/study/storage/'.$p,0755,true)) exit(71); } pcntl_exec('/usr/local/bin/php',['artisan','serve','--host=0.0.0.0','--port=8000','--no-reload']);"
        common={'read_only':True,'cap_drop':['ALL'],'security_opt':['no-new-privileges:true'],
                'pids_limit':128,'networks':['inspection'],'tmpfs':['/tmp:rw,nosuid,nodev,size=64m']}
        compose={'name':'inspection-'+str(run_id)[:8],'services':{
            'app':{**common,'image':runtime['php'],'user':'33:33','working_dir':'/opt/study',
                'entrypoint':['php','-d','max_execution_time=0','-r',setup.replace('$','$$')],'environment':env,
                'volumes':['./laravel:/opt/study:ro'],
                'tmpfs':['/tmp:rw,nosuid,nodev,size=64m','/opt/study/storage:rw,nosuid,nodev,size=128m,uid=33,gid=33','/opt/study/bootstrap/cache:rw,nosuid,nodev,size=16m,uid=33,gid=33'],
                'mem_limit':'768m','depends_on':{'db':{'condition':'service_healthy'}}},
            'db':{**common,'image':runtime['mysql'],'user':'999:999','mem_limit':'768m',
                'command':['--socket=/tmp/mysql.sock','--pid-file=/tmp/mysql.pid','--mysqlx=OFF','--character-set-server=utf8mb4','--collation-server=utf8mb4_bin','--default-time-zone=+00:00'],
                'environment':{'MYSQL_DATABASE':'study','MYSQL_USER':'study','MYSQL_PASSWORD':password,'MYSQL_ROOT_PASSWORD':uuid4().hex},
                'volumes':['./laravel/database/users.sql:/docker-entrypoint-initdb.d/01-schema.sql:ro','./laravel/database/development-users.sql:/docker-entrypoint-initdb.d/02-users.sql:ro'],
                'tmpfs':['/tmp:rw,nosuid,nodev,size=64m,uid=999,gid=999','/var/lib/mysql:rw,nosuid,nodev,size=256m,uid=999,gid=999'],
                'healthcheck':{'test':['CMD','mysqladmin','ping','--socket=/tmp/mysql.sock'],'interval':'2s','start_period':'30s','retries':30}}},
            'networks':{'inspection':{'internal':True},'local_browser':{}}}
        compose['services']['gateway']={**common,'image':runtime['client'],'user':'65532:65532',
            'entrypoint':['python','-B','/opt/inspection-proxy.py'],'volumes':['./inspection-proxy.py:/opt/inspection-proxy.py:ro'],
            'ports':['127.0.0.1:8160:8000'],'networks':['inspection','local_browser'],'mem_limit':'128m','depends_on':['app']}
        (root/'inspection-proxy.py').write_bytes(Path(__file__).with_name('inspection_proxy.py').read_bytes())
        (root/'compose.json').write_text(json.dumps(compose,ensure_ascii=False,indent=2))
        manifest={'kind':'inspection-copy-'+EXPORT_VERSION,'run_id':str(run_id),'candidate_id':str(candidate.id),
                  'tree_hash':candidate.tree_hash,'created_at':datetime.now(timezone.utc).isoformat(),
                  'project_files':inventory(project),'runtime_images':runtime,
                  'purpose':'Separate manuelle Ansicht mit öffentlichen Entwicklungsdaten, keine neue Messung oder Modellgenerierung'}
        (root/'inspection-manifest.json').write_text(canonical(manifest))
        (root/'START.md').write_text(f'''# Laravel-Prüfkopie

Lauf: {run_id}
Kandidat: {candidate.id}
Versiegelter Baum: {candidate.tree_hash}

1. ZIP in einen eigenen lokalen Ordner entpacken. Der Laravel-Projektpfad ist darin `laravel/`.
2. Docker Desktop starten. Im entpackten Ordner ausführen:

   ```sh
   docker compose -f compose.json up -d
   ```

3. http://127.0.0.1:8160/study-access öffnen. Zugang für diese lokale Kopie: `{access}`.
4. Modul über die Startseite aufrufen. Für die Bewertung Code unter `laravel/` lesen.
5. Im Bewertungsformular der Forschungsumgebung Befund, Datei und Zeilen speichern.
6. Nach der Prüfung diese Kopie stoppen:

   ```sh
   docker compose -f compose.json down
   ```

Der Port 8160 ist nur lokal über einen festen HTTP-Zugang erreichbar. Laravel und Datenbank bleiben im internen Netz ohne Internetzugang. Code ist im Container schreibgeschützt; Datenbank, Session und Uploads dieser Ansicht sind temporär. Die drei Images sind dieselben festgelegten Laufzeitversionen wie in der Forschungsumgebung. Mit bereits installierten Images wird kein Internet benötigt. Keine Abhängigkeiten nachinstallieren, keine Modell-API verwenden. Originalcode und Messergebnisse bleiben unverändert. Verwende die gespeicherten unabhängigen Integrationsbefunde im Bewertungsformular; die öffentliche Entwicklungsansicht ersetzt diese Messungen nicht. Bei Code-/Startfehlern den Befund festhalten, den Kandidaten nicht reparieren.
''')
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
            for item in inventory(root):
                data,mode=read_regular(root,item['path'])
                info=zipfile.ZipInfo(item['path']);info.external_attr=(0o100000|mode)<<16
                info.compress_type=zipfile.ZIP_DEFLATED
                archive.writestr(info,data)
        store.store(output.getvalue(),run_id=run_id,artifact_type='laravel_inspection_zip',
                    original_name=folder_name(run_id)+'.zip',mime_type='application/zip')
        publish_local(register,run_id,output.getvalue())
        return {'url':'/runs/'+str(run_id)+'/code#inspection'}
    finally:
        shutil.rmtree(root)

"""M4 trusted Docker lifecycle; callers submit identities, never Docker arguments.

Daemon privilege and administrator edits are outside the untrusted-job boundary.
All candidate/test processes must be tracked here before Snapshots.seal().
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import base64
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import tarfile
import threading
from uuid import UUID, uuid4

from .artifacts import IntegrityError, directory, read_regular
from .domain import AssetVersion, CandidateSnapshot, ConfigurationVersion, Job, Run, StudyPhase
from .context_assets import package_path
from .locks import write_barrier
from .snapshots import Snapshots, inventory
from .domain import Artifact

PREFIX = 'org.bachelorarbeit.sandbox.'
ACTIVE = ('allocated', 'starting', 'running', 'recovery_required')


class DockerTransactionError(RuntimeError):
    """External I/O must wait until the owner's short transaction is closed."""


def utc():
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class RuntimeImages:
    php: str
    mysql: str
    client: str
    guard: str

    def __post_init__(self):
        for image in (self.php, self.mysql, self.client, self.guard):
            if not re.fullmatch(r'sha256:[a-f0-9]{64}|[^\s]+@sha256:[a-f0-9]{64}', image):
                raise IntegrityError('Nur unveränderliche vorbereitete Imageidentitäten')


# Initial technical profile. Actual fit/exceedance proofs and rationale belong to
# docs/pruefungen/m4, not a claim of scientifically adequate minimum resources.
LIMITS = {
    'analysis': dict(mem_limit='768m', memswap_limit='768m', nano_cpus=1_000_000_000, pids_limit=64),
    'candidate': dict(mem_limit='256m', memswap_limit='256m', nano_cpus=1_000_000_000, pids_limit=64),
    'mysql': dict(mem_limit='1024m', memswap_limit='1024m', nano_cpus=1_000_000_000, pids_limit=128),
    'client': dict(mem_limit='256m', memswap_limit='256m', nano_cpus=1_000_000_000, pids_limit=64),
    'guard': dict(mem_limit='64m', memswap_limit='64m', nano_cpus=100_000_000, pids_limit=16),
}
LOG_BYTES = 2 * 1024 * 1024
FILE_BYTES = 16 * 1024 * 1024
INPUT_BYTES = 128 * 1024 * 1024


def archive_tree(root, expected=None):
    """Only verified regular files; no symlinks, hardlinks, devices or host binds."""
    root = Path(root)
    entries = inventory(root)
    if expected is not None and entries != expected:
        raise IntegrityError('Eingabebaum/Manifest stimmt nicht')
    if sum(e['size'] for e in entries) > INPUT_BYTES:
        raise IntegrityError('Dateimengenschutz für Eingabekopie überschritten')
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as tar:
        directories = sorted({str(p) for e in entries for p in Path(e['path']).parents if str(p) != '.'})
        for name in directories:
            item = tarfile.TarInfo(name); item.type = tarfile.DIRTYPE; item.mode = 0o755
            tar.addfile(item)
        for entry in entries:
            data, mode = read_regular(root, entry['path'])
            if hashlib.sha256(data).hexdigest() != entry['sha256'] or mode != entry['mode']:
                raise IntegrityError('Eingabe während Kopie verändert')
            item = tarfile.TarInfo(entry['path']); item.size = len(data)
            item.mode = mode & ~0o222; item.uid = 0; item.gid = 0
            tar.addfile(item, io.BytesIO(data))
    if inventory(root) != entries:
        raise IntegrityError('Eingabebaum während Kopie verändert')
    return stream.getvalue(), entries


def decode_transport(raw, *, stream_error=None):
    """Only received complete frames supply HTTP evidence; no invented response."""
    from .sandbox_client import RESPONSE_BYTES
    headers=None;body=bytearray();result=None
    lines=raw.splitlines()
    for index,line in enumerate(lines):
        try:event=json.loads(line)
        except json.JSONDecodeError:
            if index==len(lines)-1 and not raw.endswith(b'\n'):continue
            raise IntegrityError('Beschädigter geschützter Transportframe')
        if not isinstance(event,dict):raise IntegrityError('Ungültiger geschützter Transportframe')
        kind,value=event.get('event'),event.get('value')
        if kind=='headers':headers=value
        elif kind=='body':
            data=bytes.fromhex(value)
            if len(body)+len(data)>RESPONSE_BYTES:raise IntegrityError('Clientbody überschreitet geschütztes Protokoll')
            body.extend(data)
        elif kind=='result':result=value
        else:raise IntegrityError('Unbekannter geschützter Transportframe')
    response=dict(headers or {})
    if result is not None:response.update(result)
    if headers is not None or body or result is None:
        response['body']=bytes(body).decode('utf-8','replace');response['received_body_bytes']=len(body)
    if result is None:
        response.update(transport_error=stream_error or 'transport_interrupted',partial_evidence=True,
                        diagnostic_missing='No completed client result; only received raw frames retained')
    elif stream_error:response['stream_warning']=stream_error
    return response


class Sandbox:
    def __init__(self, store, docker, *, images, assets):
        self.store, self.register, self.settings = store, store.register, store.settings
        self.docker, self.images, self.assets = docker, images, Path(assets)
        self.snapshots = Snapshots(store)
        self.instance = self.register.connection.execute("SELECT value FROM runtime_metadata WHERE key='installation_id'").fetchone()[0]
        self.handles = {}
        self.asset_lock=json.loads(Path(__file__).with_name('sandbox_assets.lock.json').read_text())

    def _asset_tree(self, relative):
        root=self.assets/relative
        actual={e['path']:e['sha256'] for e in inventory(root)}
        if actual!=self.asset_lock[relative]: raise IntegrityError('Gesperrtes Asset verändert: '+relative)
        return root

    def package(self, run_id):
        run=self.register.get(run_id,Run)
        conf=self.register.get(run.configuration_version_id,ConfigurationVersion)
        relative=package_path(self.register, conf)
        data,_=read_regular(self.assets,relative)
        if hashlib.sha256(data).hexdigest()!=self.asset_lock[relative]['sha256']:
            raise IntegrityError('Kontextpaket verändert')
        return data

    def _external(self):
        if self.register.connection.in_transaction:
            raise DockerTransactionError('Dockeraufruf in offener DBtransaktion verboten')

    def _labels(self, execution, run, job):
        phase = self.register.get(run.phase_id, StudyPhase)
        return {PREFIX + 'instance': self.instance, PREFIX + 'study': str(phase.study_id),
                PREFIX + 'run': str(run.id), PREFIX + 'job': str(job.id), PREFIX + 'execution': execution}

    def _row(self, execution):
        row = self.register.connection.execute('SELECT * FROM sandbox_execution WHERE id=? AND instance_id=?', (str(execution), self.instance)).fetchone()
        if not row:
            raise IntegrityError('Fremde/unbekannte Sandboxausführung')
        return dict(row)

    def _update(self, execution, *, status=None, body=None):
        old = self._row(execution)
        with self.register.transaction():
            self.register.connection.execute('UPDATE sandbox_execution SET status=?,body=? WHERE id=?',
                (status or old['status'], json.dumps(body if body is not None else json.loads(old['body']), sort_keys=True), execution))

    def _observe(self, execution, kind, value):
        row = self._row(execution)
        external = json.loads(row['body']).get('evaluation', False)
        scope = 'trusted_evaluator' if row['suite_kind'] == 'study_holdout' else 'trusted_register' if external else 'public_development'
        evidence = self.store.json(value, run_id=UUID(row['run_id']), artifact_type='sandbox_'+kind,
            producer='trusted_evaluator' if external else 'trusted_sandbox', original_name=execution+'-'+kind+'.json', access_scope=scope)
        with self.register.transaction():
            self.register.connection.execute('INSERT INTO sandbox_observation(execution_id,happened_at,kind,artifact_id) VALUES(?,?,?,?)',
                                            (execution, utc(), kind, str(evidence.id)))
        return evidence

    def allocate(self, job_id, candidate_id, *, profile='http', internal_tests_id=None, evaluation=False):
        """An existing register job binds suite/run. Every test/repair/case uses a new UUID."""
        if profile not in ('http', 'internal', 'syntax', 'boot', 'static', 'lines'):
            raise IntegrityError('Unbekannte feste Jobvorlage')
        job = self.register.get(job_id, Job)
        if job.run_id is None:
            raise IntegrityError('Sandboxauftrag braucht eigenen Lauf')
        run = self.register.get(job.run_id, Run)
        candidate = self.register.get(candidate_id, CandidateSnapshot)
        suite = self.register.get(run.suite_id, AssetVersion)
        # All pre-Seal intervention jobs are public development jobs. Main's
        # held-out suite belongs only to the independent post-Seal evaluator.
        kind = 'development' if not evaluation and profile in ('internal', 'syntax', 'boot') else suite.suite_kind
        if kind not in ('development','study_holdout') or run.purpose in ('free_test','demo') and kind != 'development':
            raise IntegrityError('Geschützte Suite in freiem Test/Demo verboten')
        if evaluation and (job.job_type != 'measurement' or profile == 'internal'):
            raise IntegrityError('Externer Prüfer braucht eigenständigen Messauftrag')
        if candidate.run_id != run.id or (profile == 'http' or evaluation) and candidate.role != 'sealed':
            raise IntegrityError('Fremder Kandidat oder externe Messung vor Seal')
        if (profile == 'http' or evaluation) and self.register.state(run.id).seal != 'sealed':
            raise IntegrityError('Gültiges Seal fehlt')
        if profile == 'internal' and internal_tests_id is None:
            raise IntegrityError('Interner Testbestand fehlt')
        if self.register.connection.execute("SELECT 1 FROM sandbox_execution s JOIN run_binding r ON s.run_id=r.run_id WHERE r.phase_id=? AND s.status IN ('allocated','starting','running','recovery_required') LIMIT 1",(str(run.phase_id),)).fetchone():
            raise IntegrityError('Aktive/verwaiste Ausführung oder Reset-/Integritätsfehler sperrt neue Generation der Phase')
        if any(r['status'] in ACTIVE for r in self.register.connection.execute('SELECT status FROM sandbox_execution WHERE job_id=?', (str(job.id),))):
            raise IntegrityError('Aktive/verwaiste Sandbox sperrt neue Ausführung')
        execution = str(uuid4())
        labels = self._labels(execution, run, job)
        body = {'labels':labels, 'candidate_id':str(candidate.id), 'resources':[], 'log_errors':[],
                'last_activity':None, 'node':profile, 'evaluation':evaluation, 'internal_tests_id':str(internal_tests_id) if internal_tests_id else None}
        with self.register.transaction():
            self.register.connection.execute('INSERT INTO sandbox_execution VALUES(?,?,?,?,?,?,?,?,?)',
                (execution,str(job.id),str(run.id),self.instance,kind,profile,'allocated',json.dumps(body),utc()))
        try:
            path = self.settings.staging / 'sandbox' / execution
            path.mkdir(parents=True, exist_ok=False)
            if profile == 'http' or evaluation:
                self.snapshots.inspection_copy(candidate.id, path / 'candidate')
            else:
                self.snapshots.restore(candidate.id, path / 'candidate')
            archive, entries = archive_tree(path / 'candidate')
            if not evaluation or profile in ('static', 'lines'):
                self._validate_candidate(path / 'candidate', entries)
        except BaseException as exc:
            self._update(execution,status='recovery_required')
            self._observe(execution,'allocation_failure',{'reason':str(exc),'generation_blocked':True})
            raise
        handle = Execution(self, execution, path, body, archive)
        self.handles[execution] = handle
        if internal_tests_id is not None:
            tests = self.store.read(internal_tests_id)
            artifact = self.register.get(internal_tests_id)
            if artifact.run_id != run.id or hashlib.sha256(tests).hexdigest() != candidate.internal_tests_hash or artifact.access_scope not in ('role','public_development'):
                self._update(execution,status='recovery_required')
                raise IntegrityError('Fremde/veränderte/geschützte interne Tests')
            handle.tests = tests
        self._observe(execution,'allocated', {'candidate_hash':candidate.tree_hash,'source_files':len(entries),
                'profile':profile,'suite_kind':kind,'fresh_resources':True,'no_checkpoint_mount':True})
        return handle

    def _validate_candidate(self, root, entries):
        # Preserve protected M2 scaffold, including the exact public allowlist.
        scaffold = self._asset_tree('assets/study/m2-v0.1/scaffold')
        original = {e['path']:e for e in inventory(scaffold)}
        actual = {e['path']:e for e in entries}
        def allowed(path):
            return path == 'routes/study.php' or ((path.startswith('app/Study/') or path.startswith('app/Http/Controllers/Study/')) and path.endswith('.php')) or path.startswith('resources/views/study/') and path.endswith('.blade.php')
        for name, entry in original.items():
            if not allowed(name) and (name not in actual or any(actual[name][k] != entry[k] for k in ('sha256','size'))):
                raise IntegrityError('Geschütztes Gerüst verändert: '+name)
        for name in actual:
            if name not in original and not allowed(name) and not name.startswith('vendor/'):
                raise IntegrityError('Datei außerhalb öffentlicher Kandidatenallowlist: '+name)
        # The shared dependency identity was verified by Snapshots.restore().

    def owned(self, resource, labels):
        attrs = resource.attrs
        found = attrs.get('Config',{}).get('Labels',attrs.get('Labels',{})) or {}
        return all(found.get(k) == v for k,v in labels.items())

    def _recover_spools(self, execution):
        row=self._row(execution)
        root=self.settings.staging/'sandbox'/execution
        scope='trusted_evaluator' if row['suite_kind']=='study_holdout' else 'public_development'
        try:
            with directory(root) as fd:
                names=set(os.listdir(fd))
                for name in sorted(n for n in names if n in {'candidate.raw','mysql.raw','client.raw','guard.raw','runtime-init.raw','syntax.raw','protection.raw','static-stdout.raw','static-stderr.raw','lines-stdout.raw','lines-stderr.raw'} or re.fullmatch(r'http-[a-f0-9-]{36}\.raw',n)):
                    info=os.stat(name,dir_fd=fd,follow_symlinks=False)
                    if info.st_size>LOG_BYTES:raise IntegrityError('Wiederanlaufspool verletzt Bytegrenze')
                    data,_=read_regular(root,name)
                    static=row['profile'] in ('static','lines')
                    artifact=self.store.store(data,run_id=UUID(row['run_id']),artifact_type='sandbox_recovery_partial_log',producer='trusted_evaluator' if static else 'trusted_sandbox_recovery',original_name=name,access_scope='trusted_register' if static and row['suite_kind']=='development' else scope)
                    self._observe(execution,'recovered_partial_log',{'name':name,'artifact_id':str(artifact.id),'bytes':len(data),'stream_complete':'unknown after worker crash'})
        except FileNotFoundError:
            self._observe(execution,'recovery_log_missing',{'reason':'No surviving spool; no invented complete logs'})

    def recover(self, *, decision, run_id=None):
        """Explicit restart cleanup, never resume or generate. No global prune/down."""
        self._external()
        if decision != 'stop_owned':
            raise IntegrityError('Bewusste lokale Recoveryentscheidung erforderlich')
        results = []
        rows = self.register.connection.execute('SELECT id FROM sandbox_execution WHERE instance_id=? AND status IN (?,?,?,?)' + (' AND run_id=?' if run_id else ''), (self.instance,*ACTIVE,*((str(run_id),) if run_id else ()))).fetchall()
        for row in rows:
            execution = row[0]
            handle = self.handles.get(execution)
            if handle:
                handle.abort(reason='Bewusste Recovery nach Neustart')
                self._observe(execution,'recovery',{'reason':'explicit stop_owned','generation_resumed':False,'previous_errors':handle.errors})
                self._update(execution,status='interrupted')
            else:
                body = json.loads(self._row(execution)['body'])
                filters = {'label':[k+'='+v for k,v in body['labels'].items()]}
                self._external()
                containers = self.docker.containers.list(all=True, filters=filters)
                evidence = []
                for c in containers:
                    c.reload()
                    if not self.owned(c,body['labels']):
                        raise IntegrityError('Fremde Containerzuordnung')
                    evidence.append({'id':c.id,'state':c.attrs.get('State',{}),'partial_log':'raw spool retained; previous stream completion unknown'})
                    if c.status in ('running','paused','restarting'):
                        c.kill()
                    if c.status!='created': c.wait()
                    c.remove()
                self._recover_spools(execution)
                self._observe(execution,'recovery', {'reason':'explicit stop_owned','containers':evidence,'generation_resumed':False})
                for collection in (self.docker.volumes,self.docker.networks):
                    for resource in collection.list(filters=filters):
                        resource.reload()
                        if not self.owned(resource,body['labels']):
                            raise IntegrityError('Fremde Ressourcenzuordnung')
                        resource.remove()
                self._update(execution,status='interrupted')
            results.append(execution)
        return results

    def writers(self, run_id):
        return SandboxWriters(self, str(run_id))

    def stop_recorded(self, run_id, *, reason):
        """Fast trusted stop from another control process, using persisted labels.

Kill only the active candidate/syntax process first. The original controller
then retains streams and stops its guard/database normally. If that controller
is gone, explicit run-scoped recovery retains spools and completes cleanup.
No inferred stillstand or process-time measurement is reported here.
"""
        rows = self.register.connection.execute('SELECT id,body FROM sandbox_execution WHERE instance_id=? AND run_id=? AND status IN (?,?,?,?)',
            (self.instance, str(run_id), *ACTIVE)).fetchall()
        for row in rows:
            body = json.loads(row['body'])
            self._update(row['id'], status='recovery_required')
            self._observe(row['id'], 'recorded_stop_requested', {'reason': reason, 'labels': body['labels'],
                'resources': body['resources'], 'stillstand': 'not yet verified',
                'diagnostic_missing': 'Immediate separate-process stop; native completion/streams follow from controller or conscious recovery'})
            for entry in body['resources']:
                if entry['kind'] != 'container' or not entry['name'].endswith(('-candidate', '-syntax', '-static', '-lines')):
                    continue
                def kill_owned(resource_id=entry['id'], labels=body['labels']):
                    try:
                        container = self.docker.containers.get(resource_id)
                        container.reload()
                        if self.owned(container, labels) and container.status in ('running', 'paused', 'restarting'):
                            container.kill()
                    except Exception:
                        # The durable stop request explicitly leaves completion
                        # unknown; never claim success from this daemon thread.
                        pass
                threading.Thread(target=kill_owned, daemon=True, name='pipeline-stop-' + entry['id']).start()
        return [row['id'] for row in rows]


class Execution:
    def __init__(self, sandbox, execution, path, body, archive):
        self.sandbox, self.id, self.path, self.body = sandbox,execution,path,body
        self.archive, self.tests = archive,None
        self.containers, self.volumes, self.networks, self.streams = [],[],[],[]
        self.guard = None
        self.monitor = None
        self.errors = []
        self.kill_threads = []
        self.stopped = threading.Event()
        self.requested_stops = set()
        self.protection_events = {}
        self.protection_persisted = set()
        self.protection_lock = threading.Lock()

    @property
    def labels(self):
        return self.body['labels']

    def _dispatch_allowed(self):
        self.sandbox._external()
        row=self.sandbox._row(self.id)
        binding=self.sandbox.register.connection.execute('SELECT status FROM pipeline_binding WHERE run_id=? AND job_id=?',(self.labels[PREFIX+'run'],self.labels[PREFIX+'job'])).fetchone()
        if self.stopped.is_set() or row['status'] not in ('allocated','starting','running') or binding and binding['status'] in ('abort_requested','recovery_required','completed'):
            raise IntegrityError('Persistenter Sandboxstopp: kein weiterer Dispatch')

    def _resource(self, kind, resource):
        self.body['resources'].append({'kind':kind,'id':resource.id,'name':resource.name})
        self.sandbox._update(self.id,body=self.body)
        self._dispatch_allowed()
        return resource

    def _volume(self, purpose, *, size=None, uid=33):
        self._dispatch_allowed()
        opts = {'type':'tmpfs','device':'tmpfs','o':f'size={size},nr_inodes=4096,uid={uid},gid={uid},mode=0755'} if size else None
        volume = self.sandbox.docker.volumes.create(name='m4-'+self.id+'-'+purpose,labels=self.labels,driver_opts=opts)
        self.volumes.append(volume)
        return self._resource('volume',volume)

    def _options(self, name, *, kind='candidate', network='none', volumes=None):
        from docker.types import LogConfig, Ulimit
        return dict(name='m4-'+self.id+'-'+name,labels=self.labels,user='999:999' if kind=='mysql' else '33:33' if kind in ('candidate','client','analysis') else '10001:10001',
            network_mode=network,read_only=True,cap_drop=['ALL'],security_opt=['no-new-privileges:true'],
            init=True,restart_policy={'Name':'no'},**LIMITS[kind],
            ulimits=[Ulimit(name='fsize',soft=512*1024*1024 if kind=='mysql' else FILE_BYTES,hard=512*1024*1024 if kind=='mysql' else FILE_BYTES),Ulimit(name='nofile',soft=1024,hard=1024)],
            tmpfs={'/tmp':'rw,noexec,nosuid,nodev,size=32m,mode=1777'},volumes=volumes or {},
            log_config=LogConfig(type='none'),detach=True)

    def _create(self, image, command, name, **kwargs):
        self._dispatch_allowed()
        container = self.sandbox.docker.containers.create(image,command,**self._options(name,**kwargs))
        self.containers.append(container); self._resource('container',container)
        return container

    def _fill(self, volume, archive, purpose):
        # Stopped daemon copy carrier only; no source code executes and no host bind.
        carrier = self._create(self.sandbox.images.php,['php','-v'],'copy-'+purpose,
            volumes={volume.name:{'bind':'/payload','mode':'rw'}})
        if not carrier.put_archive('/payload',archive):
            raise IntegrityError('Daemonkopie fehlgeschlagen')
        carrier.remove(); self.containers.remove(carrier)

    def _stream(self, container, name):
        stream = container.attach(stream=True,logs=False,stdout=True,stderr=True)
        output = self.path / (name+'.raw')
        def collect():
            count = 0
            try:
                with open(output,'xb',buffering=0) as raw:
                    for chunk in stream:
                        available = max(0,LOG_BYTES-count)
                        raw.write(chunk[:available]); count += len(chunk)
                        if count > LOG_BYTES:
                            self.errors.append({'kind':'log_limit','container':container.id,'retained_bytes':LOG_BYTES,'observed_bytes':count})
                            container.kill(); break
                    os.fsync(raw.fileno())
            except Exception as exc:
                self.errors.append({'kind':'log_capture_error','container':container.id,'reason':str(exc)})
                try: container.kill()
                except Exception: pass
            finally:
                stream.close()
        thread = threading.Thread(target=collect,daemon=True,name='m4-log-'+name)
        thread.start(); self.streams.append((thread,output,name))

    def _start(self, container, name):
        self._dispatch_allowed()
        if self.guard is not None:
            self._guard_ok()
        self._stream(container,name); container.start()
        self._dispatch_allowed()

    def _wait_mysql(self, database, password):
        """Require the final TCP server and initialized fixtures before candidate code."""
        self.sandbox._observe(self.id,'mysql_waiting',{'protocol':'TCP','database':'study'})
        attempts=0
        while True:
            self._dispatch_allowed()
            self._guard_ok()
            database.reload()
            if database.status!='running' or self.errors:
                raise RuntimeError('MySQL-Vorbereitung fehlgeschlagen; Anwendungscode wurde noch nicht gestartet')
            # The entrypoint's temporary initialization server has networking
            # disabled. A socket ping would incorrectly accept that server.
            # Use the same account/database/TCP address as candidate code and
            # require the fixture table. Never put the password in argv/logs.
            result=database.exec_run(['mysql','--protocol=TCP','--host=127.0.0.1',
                '--user=study','--database=study','--batch','--skip-column-names',
                '--execute=SELECT COUNT(*) FROM users'],environment={'MYSQL_PWD':password})
            attempts+=1
            self._dispatch_allowed()
            if result.exit_code==0:
                self.sandbox._observe(self.id,'mysql_ready',{'protocol':'TCP','database':'study',
                    'fixture_table':'users','exit_code':0,'attempts':attempts,'observed_at':utc()})
                return
            # Readiness polling is interruptible and has no study time cutoff.
            self.stopped.wait(.2)

    def _analysis_stream(self, container, name):
        """Preserve distinct original stdout/stderr with one combined byte guard."""
        stream = container.attach(stream=True, logs=False, stdout=True, stderr=True, demux=True)
        outputs = [self.path / (name+'-'+channel+'.raw') for channel in ('stdout','stderr')]
        def collect():
            count=0
            try:
                with open(outputs[0],'xb',buffering=0) as out, open(outputs[1],'xb',buffering=0) as err:
                    for pair in stream:
                        for chunk,destination in zip(pair,(out,err),strict=True):
                            if chunk is None:continue
                            available=max(0,LOG_BYTES-count);destination.write(chunk[:available]);count+=len(chunk)
                            os.fsync(destination.fileno())
                            if count>LOG_BYTES:
                                self.errors.append({'kind':'log_limit','container':container.id,'retained_bytes':LOG_BYTES,'observed_bytes':count})
                                container.kill();return
            except Exception as exc:
                self.errors.append({'kind':'log_capture_error','container':container.id,'reason':str(exc)})
                try:container.kill()
                except Exception:pass
            finally:stream.close()
        thread=threading.Thread(target=collect,daemon=True,name='m6-static-log-'+name)
        thread.start()
        self.streams.extend((thread,path,name+'-'+channel) for path,channel in zip(outputs,('stdout','stderr'),strict=True))

    def _static_dispatch_allowed(self):
        # Only a registered StaticAnalyzer job has this extra persistent gate;
        # generic M4 static/lexical probes retain their own sandbox lifecycle.
        job_id=self.labels[PREFIX+'job']
        from contextlib import closing
        from .database import connect
        self.sandbox._external()
        with closing(connect(self.sandbox.settings,readonly=True)) as connection:
            row=connection.execute('SELECT s.status,j.status AS job_status FROM static_execution s JOIN job_state j ON s.job_id=j.job_id WHERE s.job_id=?',(job_id,)).fetchone()
        if row and (row['status']!='running' or row['job_status']!='running'):
            raise IntegrityError('Persistent static Control stop; dispatch forbidden')

    def _analysis_start(self, code, storage, cache, profile):
        from .static_analysis import select_files, tool_binding, scaffold_manifest
        binding=tool_binding()
        if self.sandbox.images.php!=binding['image']:raise IntegrityError('Statisches Runtimeimage verändert')
        entries=inventory(self.path/'candidate')
        vendor={e['path'][7:]:e['sha256'] for e in entries if e['path'].startswith('vendor/')}
        from .domain import digest
        if len(vendor)!=binding['vendor_file_count'] or digest(vendor)!=binding['vendor_sha256']:
            raise IntegrityError('Statische Dependencies abweichend vom eingefrorenen Toolbestand')
        original=scaffold_manifest(self.sandbox.assets)['files']
        selection=select_files(original,entries)
        directory=self.path/'static-tools';directory.mkdir()
        for source,target in (('static_tokens.php','tokens.php'),('static_launcher.php','launcher.php'),('static_analysis.neon','analysis.neon')):
            (directory/target).write_bytes(Path(__file__).with_name(source).read_bytes())
        (directory/'files.json').write_text(json.dumps(selection['included']))
        (directory/'tool.json').write_text(json.dumps(binding))
        archive,_=archive_tree(directory);tool=self._volume('static-tools');self._fill(tool,archive,'static-tools')
        command=['php','-d','max_execution_time=0','-d','memory_limit=512M','-d','phpstan.restarted=1','/tools/'+('tokens.php' if profile=='lines' else 'launcher.php')]
        env={'APP_ENV':'local','APP_DEBUG':'false','LOG_CHANNEL':'stderr','APP_KEY':'base64:'+base64.b64encode(os.urandom(32)).decode(),'DB_CONNECTION':'sqlite','DB_DATABASE':':memory:'}
        container=self._create_env(self.sandbox.images.php,command,profile,env,kind='analysis',volumes={code.name:{'bind':'/opt/study','mode':'ro'},tool.name:{'bind':'/tools','mode':'ro'},storage.name:{'bind':'/opt/study/storage','mode':'rw'},cache.name:{'bind':'/opt/study/bootstrap/cache','mode':'rw'}})
        self._analysis_stream(container,profile)
        self._dispatch_allowed();self._static_dispatch_allowed();container.start()
        self._dispatch_allowed()
        self._static_dispatch_allowed()
        self.body['static_selection']=selection
        self.body['last_activity']={'kind':'static_started','profile':profile,'utc':utc()}

    def _guard_ok(self):
        self.sandbox._external()
        self.guard.reload()
        if self.guard.status != 'running':
            raise IntegrityError('Netzguard fehlt/gestoppt; Start gesperrt')
        self.sandbox._external()
        process=self.guard.exec_run(['cat','/proc/1/status'])
        if process.exit_code!=0:raise IntegrityError('Guardprozessinspektion nicht prüfbar/unterbrochen: exit '+str(process.exit_code))
        if not all(re.search(r'^'+cap+r':\s+0+$',process.output.decode(),re.M) for cap in ('CapEff','CapPrm','CapBnd','CapAmb','CapInh')):
            raise IntegrityError('Guardhauptprozess behält Initialisierungsrechte')
        for family in ('iptables-save','ip6tables-save'):
            self.sandbox._external()
            result = self.guard.exec_run([family])
            # Counters can vary; restore serialisation itself is deterministic.
            if result.exit_code!=0:raise IntegrityError('Guardpolicyinspektion '+family+' nicht prüfbar/unterbrochen: exit '+str(result.exit_code))
            rules = '\n'.join(x for x in result.output.decode().splitlines() if not x.startswith('#'))
            expected = self.body.get(family)
            if expected is not None and rules != expected:
                raise IntegrityError('Netzguardpolicy verändert')
            if expected is None:
                if ':OUTPUT DROP' not in rules or '-A OUTPUT -j REJECT' not in rules:
                    raise IntegrityError('Netzguard nicht fail-closed bereit')
                self.body[family] = rules

    def start(self):
        self.sandbox._external()
        row = self.sandbox._row(self.id)
        if row['status'] != 'allocated':
            raise IntegrityError('Keine automatische erneute Ausführung')
        self.sandbox._update(self.id,status='starting')
        try:
            self._dispatch_allowed()
            if row['profile'] not in ('static','lines'):
                self.sandbox._asset_tree('evaluation/'+row['suite_kind']+'/m2-v0.1')
            code = self._volume('code'); self._fill(code,self.archive,'code')
            self.archive=b''  # CAS/staging retain inputs; no 83MB tar held across jobs.
            storage = self._volume('storage',size='64m')
            cache = self._volume('cache',size='16m')
            if row['profile'] in ('static','lines'):
                self._analysis_start(code,storage,cache,row['profile'])
            elif row['profile'] == 'syntax':
                tool=self._volume('locked-syntax-tool')
                tool_dir=self.path/'tools';tool_dir.mkdir()
                (tool_dir/'syntax.php').write_bytes(Path(__file__).with_name('sandbox_syntax.php').read_bytes())
                tool_archive,_=archive_tree(tool_dir);self._fill(tool,tool_archive,'syntax-tool')
                command = ['php','-d','max_execution_time=0','/tools/syntax.php']
                php = self._create(self.sandbox.images.php,command,'syntax',volumes={tool.name:{'bind':'/tools','mode':'ro'},code.name:{'bind':'/opt/study','mode':'ro'},storage.name:{'bind':'/opt/study/storage','mode':'rw'},cache.name:{'bind':'/opt/study/bootstrap/cache','mode':'rw'}})
                self._start(php,'syntax')
            else:
                net = self.sandbox.docker.networks.create('m4-'+self.id,internal=True,labels=self.labels,enable_ipv6=False)
                self.networks.append(net); self._resource('network',net)
                opts = self._options('guard',kind='guard',network=net.name)
                opts.update(user='0:0',init=False,cap_add=['NET_ADMIN','SETUID','SETGID','SETPCAP'])
                guard = self.sandbox.docker.containers.create(self.sandbox.images.guard,**opts)
                self.containers.append(guard); self._resource('container',guard)
                # Guard boot is observed, not cut off by a timer. Main worker can
                # inspect or abort at any point; jobs are not started until ready.
                self._dispatch_allowed();self._stream(guard,'guard'); guard.start(); self.guard = guard
                self._dispatch_allowed()
                while True:
                    self._dispatch_allowed()
                    guard.reload()
                    if guard.status != 'running': raise IntegrityError('Netzguardboot fehlgeschlagen')
                    result = guard.exec_run(['test','-f','/tmp/guard-ready'])
                    if result.exit_code == 0: break
                    self.stopped.wait(.05)
                    if self.stopped.is_set(): raise IntegrityError('Manueller Abbruch vor Guardbereitschaft')
                self._guard_ok(); self.sandbox._update(self.id,body=self.body)
                namespace = 'container:'+guard.id
                database = self._volume('mysql',size='1536m',uid=999)
                fixture = self._volume('fixtures')
                fixture_root = self.path/'fixtures'; fixture_root.mkdir()
                schema,_=read_regular(self.sandbox.assets/'assets/study/m2-v0.1/scaffold/database','users.sql')
                (fixture_root/'01-users.sql').write_bytes(b'SET NAMES utf8mb4;\n'+schema)
                if row['suite_kind']=='development':
                    data,_=read_regular(self.sandbox.assets/'evaluation/development/m2-v0.1','users.sql')
                else:
                    raw,_=read_regular(self.sandbox.assets/'evaluation/study_holdout/m2-v0.1','fixtures.json')
                    users=json.loads(raw)['datasets']['DB-A']
                    fields=('user_id','first_name','last_name','user','password','avatar','last_login','failed_login','role','account_enabled')
                    def literal(v):
                        if v is None: return 'NULL'
                        if isinstance(v,int): return str(v)
                        return "'"+v.replace("'","''")+"'"
                    data=(''.join('INSERT INTO users ('+','.join(fields)+') VALUES ('+','.join(literal(u[f]) for f in fields)+');\n' for u in users)).encode()
                (fixture_root/'02-users.sql').write_bytes(b'SET NAMES utf8mb4;\n'+data)
                fixture_archive,_ = archive_tree(fixture_root); self._fill(fixture,fixture_archive,'fixtures')
                password = uuid4().hex
                root_password = uuid4().hex
                review_password = uuid4().hex
                self.body['synthetic_database_password'] = password
                db = self._create_env(self.sandbox.images.mysql,['--socket=/tmp/mysql.sock','--pid-file=/tmp/mysql.pid','--bind-address=127.0.0.1','--mysqlx=OFF','--sql-mode=ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION','--character-set-server=utf8mb4','--collation-server=utf8mb4_bin','--default-time-zone=+00:00'],'mysql',{'MYSQL_DATABASE':'study','MYSQL_USER':'study','MYSQL_PASSWORD':password,'MYSQL_ROOT_PASSWORD':root_password},kind='mysql',network=namespace,
                    volumes={database.name:{'bind':'/var/lib/mysql','mode':'rw'},fixture.name:{'bind':'/docker-entrypoint-initdb.d','mode':'ro'}})
                self._start(db,'mysql')
                self._wait_mysql(db,password)
                env = {'APP_KEY':'base64:'+__import__('base64').b64encode(os.urandom(32)).decode(),'APP_ENV':'local','APP_DEBUG':'false','APP_URL':'http://127.0.0.1:8000',
                    'DB_CONNECTION':'mysql','DB_HOST':'127.0.0.1','DB_DATABASE':'study','DB_USERNAME':'study','DB_PASSWORD':password,
                    'STUDY_ACCESS_TOKEN':uuid4().hex,'SESSION_COOKIE':'m4_session','LOG_CHANNEL':'stderr'}
                self.body['access_token'] = env['STUDY_ACCESS_TOKEN']
                mounts = {code.name:{'bind':'/opt/study','mode':'ro'},storage.name:{'bind':'/opt/study/storage','mode':'rw'},cache.name:{'bind':'/opt/study/bootstrap/cache','mode':'rw'}}
                if row['profile'] == 'internal':
                    from .role_formats import materialize_tests
                    tests_volume = self._volume('internal-tests'); test_dir=self.path/'internal-tests'; test_dir.mkdir()
                    entrypoint = materialize_tests(self.tests, test_dir)
                    test_archive,_=archive_tree(test_dir); self._fill(tests_volume,test_archive,'internal-tests')
                    mounts[tests_volume.name]={'bind':str(PurePosixPath(entrypoint).parent),'mode':'ro'}
                    command=['php','-d','max_execution_time=0',entrypoint]
                elif row['profile'] == 'boot':
                    command=['php','-d','max_execution_time=0','-r',"chdir('/opt/study');require '/opt/study/vendor/autoload.php';$app=require '/opt/study/bootstrap/app.php';$app->make(Illuminate\\Contracts\\Console\\Kernel::class)->bootstrap();echo 'PIPELINE_BOOT_OK';"]
                else:
                    command=['php','-d','max_execution_time=0','artisan','serve','--host=127.0.0.1','--port=8000','--no-reload']
                # A local-driver tmpfs loses bytes on its final unmount. Create
                # the tree inside the same container that subsequently executes
                # candidate code; no exit/unmount gap or privileged writer.
                setup="foreach(['app/study/uploads','framework/sessions','framework/cache/data','framework/cache/locks','framework/views','framework/testing','logs'] as $p) { if (!is_dir('/opt/study/storage/'.$p) && !mkdir('/opt/study/storage/'.$p,0755,true)) exit(71); }"
                # JSON double quotes are PHP interpolation syntax. Decode an inert
                # base64 payload so boot's $app/backslashes reach argv unchanged.
                argv = base64.b64encode(json.dumps(command[1:]).encode()).decode()
                setup+="pcntl_exec('/usr/local/bin/php',json_decode(base64_decode('"+argv+"'),true));fwrite(STDERR,'Candidate exec failed');exit(72);"
                command=['php','-d','max_execution_time=0','-r',setup]
                php=self._create_env(self.sandbox.images.php,command,'candidate',env,network=namespace,volumes=mounts)
                self._start(php,'candidate')
                if row['profile']=='http':
                    suite_volume=self._volume('suite'); suite_archive,_=archive_tree(self.sandbox._asset_tree(f'evaluation/{row["suite_kind"]}/m2-v0.1'))
                    self._fill(suite_volume,suite_archive,'suite')
                    requests_volume=self._volume('client-requests',size='16m',uid=33)
                    self.client=self._create_env(self.sandbox.images.client,['python','-c','import signal; signal.pause()'],'client',{'DB_PASSWORD':password,'DB_REVIEW_PASSWORD':review_password},kind='client',network=namespace,volumes={suite_volume.name:{'bind':'/suite','mode':'ro'},storage.name:{'bind':'/runtime','mode':'ro'},requests_volume.name:{'bind':'/requests','mode':'rw'}})
                    self._start(self.client,'client')
                    if self.body.get('evaluation'):
                        self.body['evaluation_root_password'] = root_password
                        self.body['evaluation_review_password'] = review_password
                        self._install_evaluation_transport()
            self._monitor()
            with self.sandbox.register.transaction():
                changed=self.sandbox.register.connection.execute("UPDATE sandbox_execution SET status='running',body=? WHERE id=? AND status='starting' AND NOT EXISTS (SELECT 1 FROM pipeline_binding WHERE run_id=? AND job_id=? AND status IN ('abort_requested','recovery_required','completed'))",(json.dumps(self.body,sort_keys=True),self.id,self.labels[PREFIX+'run'],self.labels[PREFIX+'job'])).rowcount
                if changed!=1:raise IntegrityError('Persistenter Sandboxstopp vor Startabschluss')
            self.sandbox._observe(self.id,'started',self.diagnose())
            return self
        except BaseException as exc:
            self.errors.append({'kind':'start_failure','reason':str(exc)})
            self.abort(reason=str(exc))
            raise

    def _install_evaluation_transport(self):
        """Frozen trusted code only; never mounts evaluator files in candidate."""
        data=Path(__file__).with_name('evaluation_transport.py').read_bytes()
        control=Path(__file__).with_name('evaluation_control.py').read_bytes()
        stream=io.BytesIO()
        with tarfile.open(fileobj=stream,mode='w') as tar:
            item=tarfile.TarInfo('m6-transport.py');item.size=len(data);item.mode=0o400;item.uid=33;item.gid=33
            tar.addfile(item,io.BytesIO(data))
            item=tarfile.TarInfo('m6-control.py');item.size=len(control);item.mode=0o444;item.uid=33;item.gid=33
            tar.addfile(item,io.BytesIO(control))
        if not self.client.put_archive('/requests',stream.getvalue()):raise IntegrityError('Evaluatortransportkopie fehlgeschlagen')
        self.sandbox._observe(self.id,'evaluation_transport',{'sha256':hashlib.sha256(data).hexdigest(),'control_sha256':hashlib.sha256(control).hexdigest(),'candidate_mount':False})

    def evaluation_control(self, action, payload=None):
        if not self.body.get('evaluation'):raise IntegrityError('Nur externer Messauftrag')
        if action not in ('reset','rights_probe'):raise IntegrityError('Unbekannte feste Prüferaktion')
        candidate=next(c for c in self.containers if c.name.endswith('-candidate'))
        candidate.reload()
        if candidate.status!='paused':raise IntegrityError('Kandidat vor konsistentem Prüferzugriff stillsetzen')
        storage=next(v for v in self.volumes if v.name.endswith('-storage'))
        requests=next(v for v in self.volumes if v.name.endswith('-client-requests'))
        encoded=base64.b64encode(json.dumps(payload or {}).encode()).decode()
        tool=self._create(self.sandbox.images.client,['python','/requests/m6-control.py',action,encoded],
            'evaluation-control-'+str(uuid4()),kind='client',
            volumes={storage.name:{'bind':'/runtime','mode':'rw'},requests.name:{'bind':'/requests','mode':'ro'}})
        self._start(tool,'evaluation-control-'+str(uuid4()))
        code=tool.wait()['StatusCode']
        # attach stream has retained original stdout even after tool removal.
        thread,path,name=self.streams[-1];thread.join()
        data=path.read_bytes()
        self.sandbox._observe(self.id,'evaluation_control',{'action':action,'exit_code':code,'stdout':data.decode('utf8','replace')})
        if code!=0:raise IntegrityError('Prüferkontrolle fehlgeschlagen: '+data.decode('utf8','replace'))
        return json.loads(data)

    def evaluation_reader(self):
        if not self.body.get('evaluation'):raise IntegrityError('Nur externer Messauftrag')
        database=next(c for c in self.containers if c.name.endswith('-mysql'))
        password=self.body['evaluation_review_password']
        sql="CREATE USER IF NOT EXISTS 'study_review'@'%' IDENTIFIED BY '"+password+"'; GRANT SELECT ON study.* TO 'study_review'@'%';"
        result=database.exec_run(['sh','-c','exec mysql --socket=/tmp/mysql.sock -uroot -p"$MYSQL_ROOT_PASSWORD" -e "$1"','m6-reader',sql],user='999:999')
        if result.exit_code!=0:
            self.sandbox._observe(self.id,'evaluation_reader_pending',{'exit_code':result.exit_code,'stdout_stderr':result.output.decode('utf8','replace')})
            return False
        return True

    def evaluation_pause(self, paused):
        if not self.body.get('evaluation'):raise IntegrityError('Nur externer Messauftrag')
        candidate=next(c for c in self.containers if c.name.endswith('-candidate'))
        candidate.reload()
        if paused and candidate.status=='running':candidate.pause()
        elif not paused and candidate.status=='paused':candidate.unpause()
        elif candidate.status not in ('running','paused'):raise IntegrityError('Kandidat nicht für Dateisnapshot verfügbar')
        candidate.reload()
        if candidate.status!=('paused' if paused else 'running'):raise IntegrityError('Konsistenter Kandidatenstillstand nicht bestätigt')

    def _protection(self, event, *, fatal=False):
        """Bounded durable observation queue; the monitor never writes SQLite."""
        key=event['kind']+':'+event['container_id']
        with self.protection_lock:
            if key in self.protection_events:return
            event={**event,'observed_at':utc(),'fatal':fatal,'classification':'technical kernel/process observation; no R/T/F inference'}
            encoded=(json.dumps(event,sort_keys=True)+'\n').encode()
            path=self.path/'protection.raw'
            if len(encoded)+(path.stat().st_size if path.exists() else 0)>LOG_BYTES:
                raise IntegrityError('Schutzereignisspool überschreitet Bytegrenze')
            with open(path,'ab',buffering=0) as output:
                output.write(encoded);os.fsync(output.fileno())
            self.protection_events[key]=event
            if fatal:self.errors.append(event)

    def _native_state(self, container, state, stats=None):
        if container.id not in self.requested_stops:
            oom=state.get('OOMKilled') is True
            code=state.get('ExitCode')
            if oom or (state.get('Status') in ('exited','dead') and code not in (None,0)):
                unresolved_exit=code==153  # Also possible from ordinary PHP exit(153).
                self._protection({'kind':'memory_oom' if oom else 'unresolved_process_exit' if unresolved_exit else 'process_exit',
                    'container_id':container.id,'container_name':container.name,
                    'state':json.loads(json.dumps(state)),'exit_code':code,
                    'reason':'Kernel OOMKilled' if oom else 'Native exit153; cause unknown, no independently identified signal or resource violation' if unresolved_exit else 'Nonzero candidate/test exit; no kernel protection or R/T/F inference'},fatal=oom or unresolved_exit)
        cpu=(stats or {}).get('cpu_stats',{}).get('throttling_data',{})
        if cpu.get('throttled_periods',0)>0:
            self._protection({'kind':'cpu_quota_throttled','container_id':container.id,
                'container_name':container.name,'throttling_data':cpu,
                'nano_cpus':container.attrs.get('HostConfig',{}).get('NanoCpus'),
                'protective_action':'kernel CFS throttling; work remains active, no time cutoff'},fatal=False)

    def _persist_protection(self):
        # Called only by the controller thread after external operations finish.
        with self.protection_lock:pending=list(self.protection_events.items())
        for key,event in pending:
            if key not in self.protection_persisted:
                self.sandbox._observe(self.id,'protection',event)
                self.protection_persisted.add(key)

    def _monitor(self):
        def monitor():
            while not self.stopped.wait(.2):
                try:
                    if self.body['node'] in ('static','lines'):self._static_dispatch_allowed()
                    if self.guard is not None: self._guard_ok()
                    for c in tuple(self.containers):
                        self.sandbox._external()
                        c.reload()
                        self.sandbox._external()
                        stats=c.stats(stream=False) if c.status=='running' else None
                        self._native_state(c,c.attrs.get('State',{}),stats)
                    if self.errors:
                        confirmed=any(e['kind'] in ('memory_oom','log_limit') for e in self.errors)
                        unresolved=any(e['kind']=='unresolved_process_exit' for e in self.errors)
                        raise IntegrityError('Sichtbarer Ressourcen-/Logschutzfehler' if confirmed else 'Ungeklärter Prozessausgang; Ursache nicht identifiziert' if unresolved else 'Integritäts-/Infrastrukturfehler')
                except DockerTransactionError:
                    # A short owner transaction is not a changed policy or a
                    # resource error. Retry inspection after it has closed.
                    continue
                except Exception as exc:
                    self.errors.append({'kind':'safety_stop','reason':str(exc),'observed_at':utc()})
                    for c in tuple(self.containers):
                        try:
                            self.sandbox._external()
                            c.reload()
                            if c.status in ('running','paused','restarting'):
                                self.requested_stops.add(c.id);c.kill()
                        except Exception: pass
                    break
        self.monitor=threading.Thread(target=monitor,daemon=True,name='m4-safety-'+self.id)
        self.monitor.start()

    def _create_env(self, image, command, name, environment, **kwargs):
        self._dispatch_allowed()
        options=self._options(name,**kwargs); options['environment']=environment
        container=self.sandbox.docker.containers.create(image,command,**options)
        self.containers.append(container); self._resource('container',container)
        return container

    def diagnose(self):
        self.sandbox._external()
        states=[]
        for c in self.containers:
            try:
                c.reload()
                self.sandbox._external()
                stats=c.stats(stream=False) if c.status=='running' else None
                state=c.attrs.get('State',{})
                self._native_state(c,state,stats)
                states.append({'id':c.id,'name':c.name,'state':state,'limits':c.attrs.get('HostConfig',{}),'stats':stats})
            except DockerTransactionError:raise
            except Exception as exc: states.append({'id':c.id,'diagnostic_missing':str(exc)})
        self._persist_protection()
        return {'job_id':self.labels[PREFIX+'job'],'run_id':self.labels[PREFIX+'run'],'execution_id':self.id,
                'node':self.body['node'],'observed_at':utc(),'last_activity':self.body['last_activity'],
                'states':states,'errors':list(self.errors),'latest_candidate_id':self.body['candidate_id'],
                'classification':'no R/T/F inference from duration or stop'}

    def observe(self):
        self.sandbox._external()
        try:
            if self.guard is not None: self._guard_ok()
        except Exception as exc:
            self.errors.append({'kind':'guard_integrity','reason':str(exc)})
            self.abort(reason=str(exc)); raise
        return self.sandbox._observe(self.id,'diagnosis',self.diagnose())

    def request(self, *, method, path, data=None, files=None):
        """Fixed UUID/file input; durable bounded output while HTTP is still active."""
        self.sandbox._external();self._guard_ok()
        request={'method':method,'path':path}
        if data is not None:request['data']=data
        if files is not None:request['files']=files
        identifier=str(uuid4());payload=json.dumps(request).encode()
        if len(payload)>INPUT_BYTES:raise IntegrityError('Geschützte Requestdatei überschreitet Eingabebytegrenze')
        archive=io.BytesIO()
        with tarfile.open(fileobj=archive,mode='w') as tar:
            item=tarfile.TarInfo('m4-request-'+identifier+'.json');item.size=len(payload);item.mode=0o400;item.uid=33;item.gid=33
            tar.addfile(item,io.BytesIO(payload))
        if not self.client.put_archive('/requests',archive.getvalue()):raise IntegrityError('Eigene Requestdateikopie fehlgeschlagen')
        # Linux argv has a per-string limit; only a fixed command plus UUID is
        # passed. A legal 100000-byte upload never enters an argv string.
        command = ['python', '/requests/m6-transport.py', '--request', identifier] if self.body.get('evaluation') else ['python','-m','research_env.sandbox_client','--request',identifier]
        self.body['last_activity']={'kind':'request_started','request_id':identifier,'method':method,'path':path,'utc':utc()}
        self.sandbox._update(self.id,body=self.body)
        self.sandbox._observe(self.id,'request_started',{'request_id':identifier,'request':request,'ongoing':True,'no_elapsed_cutoff':True})
        result=self.client.exec_run(command,stream=True)
        output=bytearray();exceeded=False;stream_error=None
        spool=self.path/('http-'+identifier+'.raw')
        with open(spool,'xb',buffering=0) as raw:
            try:
                for part in result.output:
                    remaining=LOG_BYTES-len(output);retained=part[:remaining]
                    output.extend(retained);raw.write(retained);os.fsync(raw.fileno())
                    if len(part)>remaining:
                        exceeded=True
                        self.errors.append({'kind':'client_output_limit','retained_bytes':len(output)})
                        self.client.kill();break
            except Exception as exc:stream_error=type(exc).__name__+': '+str(exc)
            finally:
                try:result.output.close()
                except Exception as exc:stream_error=stream_error or 'Stream close: '+str(exc)
                os.fsync(raw.fileno())
        evidence=self.sandbox.store.store(bytes(output),run_id=UUID(self.labels[PREFIX+'run']),artifact_type='sandbox_http',producer='trusted_httpx',original_name=spool.name,access_scope='trusted_evaluator' if self.sandbox._row(self.id)['suite_kind']=='study_holdout' else 'public_development')
        self.body['last_activity']=utc();self.sandbox._update(self.id,body=self.body)
        if exceeded:raise IntegrityError('HTTPXtransport Dateischutz überschritten; Rohbeleg '+str(evidence.id))
        return decode_transport(bytes(output),stream_error=stream_error),evidence

    def upload_permissions(self, *, writable):
        """Controlled nonroot chmod of own upload directory, no file removal."""
        self.sandbox._external()
        self._guard_ok()
        candidate=next(c for c in self.containers if c.name.endswith('-candidate'))
        result=candidate.exec_run(['php','-r',"$p='/opt/study/storage/app/study/uploads'; if (is_link($p)||!is_dir($p)||!chmod($p,"+('0755' if writable else '0555')+")) exit(1); clearstatcache(); echo decoct(fileperms($p)&0777);"],user='33:33')
        if result.exit_code != 0: raise IntegrityError('Uploadrechtesperre/Rücknahme fehlgeschlagen')
        return self.sandbox._observe(self.id,'upload_permissions',{'writable':writable,'actual_mode':result.output.decode()})

    def abort(self, *, reason, immediate=False):
        """Manual stop always possible; no timed graceful-stop watchdog."""
        self.sandbox._external()
        self.stopped.set()
        if immediate:
            self.sandbox._update(self.id,status='recovery_required',body=self.body)
            self.sandbox._observe(self.id,'immediate_stop_requested',{
                'reason':reason,'node':self.body['node'],'last_activity':self.body['last_activity'],
                'latest_candidate_id':self.body['candidate_id'],'known_resources':self.body['resources'],
                'diagnostic_missing':'Immediate stop: no dependency on possibly hung monitor/daemon diagnostics',
                'stillstand':'unknown until explicit recovery verifies all owned processes',
                'raw_spools':'retained at own execution path; possibly partial, not silently deleted'})
            for container in tuple(self.containers):
                # IDs were returned by this trusted creation path and labels were
                # fixed at creation; no lookup/global cleanup/foreign target.
                def kill_owned(c=container):
                    try:
                        c.reload()
                        if not self.sandbox.owned(c,self.labels):raise IntegrityError('Fremde Containerzuordnung beim Sofortstopp')
                        if c.status in ('running','paused','restarting'):
                            self.requested_stops.add(c.id);c.kill()
                    except Exception as exc:self.errors.append({'kind':'immediate_kill_unconfirmed','id':c.id,'reason':str(exc)})
                thread=threading.Thread(target=kill_owned,daemon=True,name='m4-immediate-kill-'+container.id)
                thread.start();self.kill_threads.append(thread)
            return
        if self.monitor is not None: self.monitor.join()
        for thread in self.kill_threads:thread.join()

        if not immediate:
            try: self.sandbox._observe(self.id,'before_stop',self.diagnose())
            except Exception as exc: self.errors.append({'kind':'diagnostic_missing','reason':str(exc)})
        failures=[]
        self.sandbox._external()
        for c in reversed(self.containers):
            try:
                c.reload()
                if not self.sandbox.owned(c,self.labels): raise IntegrityError('Fremder Container')
                if c.status in ('running','paused','restarting'):
                    self.requested_stops.add(c.id);c.kill()
                if c.status != 'created': c.wait()
            except Exception as exc: failures.append({'id':c.id,'reason':str(exc)})
        for thread,path,name in self.streams:
            thread.join()
            if path.exists():
                scope='trusted_evaluator' if self.sandbox._row(self.id)['suite_kind']=='study_holdout' else 'public_development'
                static=self.sandbox._row(self.id)['profile'] in ('static','lines')
                artifact=self.sandbox.store.store(path.read_bytes(),run_id=UUID(self.labels[PREFIX+'run']),artifact_type='sandbox_log',producer='trusted_evaluator' if static else 'trusted_sandbox',original_name=name+'.raw',access_scope='trusted_register' if static and self.sandbox._row(self.id)['suite_kind']=='development' else scope)
                self.sandbox._observe(self.id,'log_retained',{'name':name,'artifact_id':str(artifact.id),'stream_complete':not failures,'bytes':path.stat().st_size})
        self.sandbox._observe(self.id,'stopped',{'reason':reason,'immediate':immediate,'errors':self.errors,'stop_failures':failures,'classification':'interrupted or technical evidence only'})
        if failures:
            self.sandbox._update(self.id,status='recovery_required',body=self.body)
            raise IntegrityError('Stillstand nicht nachgewiesen; neue Generation gesperrt')
        for c in reversed(self.containers): c.remove()
        for v in reversed(self.volumes): v.remove()
        for n in reversed(self.networks): n.remove()
        self.containers.clear(); self.volumes.clear(); self.networks.clear(); self.streams.clear()
        self.archive=b''
        self.sandbox.handles.pop(self.id,None)
        self.sandbox._update(self.id,status='recovery_required' if self.errors else 'interrupted',body=self.body)


class SandboxWriters:
    def __init__(self,sandbox,run_id): self.sandbox,self.run_id=sandbox,run_id
    def stop_all(self):
        rows=self.sandbox.register.connection.execute('SELECT id FROM sandbox_execution WHERE run_id=? AND status IN (?,?,?,?)',(self.run_id,*ACTIVE)).fetchall()
        for row in rows:
            handle=self.sandbox.handles.get(row[0])
            if handle is None: raise IntegrityError('Verwaister Sandboxschreiber: bewusste Recovery erforderlich')
            handle.abort(reason='Alle bekannten Kandidaten-/Testprozesse vor Seal stoppen')
    def assert_stopped(self):
        rows=self.sandbox.register.connection.execute('SELECT id FROM sandbox_execution WHERE run_id=? AND status IN (?,?,?,?)',(self.run_id,*ACTIVE)).fetchall()
        if rows: raise IntegrityError('Sandboxschreiber nicht nachweislich gestoppt')

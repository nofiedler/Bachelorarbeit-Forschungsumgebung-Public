"""Isolated immutable D/L/S measurements. Candidate PHP never runs in Python.

Larastan executes application PHP inside the separate contained job. Its
in-process semantics remain untrusted; protocol validation is not a security
or correctness proof of arbitrary booted code.
"""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import threading
from uuid import UUID, uuid4

from .artifacts import IntegrityError
from .domain import (Artifact, AssetVersion, CandidateSnapshot, Compatibility,
    ConfigurationVersion, CountObservation, IntegerObservation, Job,
    MeasurementAttempt, MetricObservation, NonnegativeObservation, Observation,
    RevisionInvalidation, Run, StaticProfile, canonical, digest, protected_evaluation)
from .snapshots import inventory

VERSION='M6-static-v1'
PACKAGE=Path(__file__).parent
SOURCES=('static_analysis.py','static_tokens.php','static_launcher.php',
    'static_analysis.neon','static_tool.lock.json','sandbox_runtime.py',
    'register.py','domain.py','snapshots.py','artifacts.py','backup.py','pipeline_assets.lock.json',
    'migrations/010_static_analysis.sql')


def now():return datetime.now(timezone.utc)


def tool_binding():
    binding = json.loads((PACKAGE/'static_tool.lock.json').read_text())
    # An immutable image ID is installation-specific (including architecture).
    # Freeze the installed runtime into the instrument; retain the independent
    # PHP/PHPStan/Larastan and vendor-byte checks below that boundary.
    binding['image'] = json.loads((PACKAGE/'pipeline_runtime.lock.json').read_text())['php']
    return binding


def scaffold_manifest(assets):
    relative='assets/study/m2-v0.1/manifest.json'
    data=(Path(assets)/relative).read_bytes()
    lock=json.loads((PACKAGE/'pipeline_assets.lock.json').read_text())
    if hashlib.sha256(data).hexdigest()!=lock[relative]:raise IntegrityError('Frozen original scaffold manifest changed')
    return json.loads(data)


def measure_binding():
    """Scientific scale; implementation fixes retain this exact binding."""
    tool=tool_binding()
    return {'version':tool['measure_version'],'php':tool['php'],'phpstan':tool['phpstan'],
        'larastan':tool['larastan'],'vendor_sha256':tool['vendor_sha256'],
        'configuration_sha256':hashlib.sha256((PACKAGE/'static_analysis.neon').read_bytes()).hexdigest(),
        'D':'all unchanged file diagnostics of completed analysis in selected module PHP',
        'L':'physical lines intersecting a non-comment PHP token; tags/whitespace/HTML excluded; once per line',
        'scope':'changed/added module PHP from union of diff and complete manifest; unchanged scaffold, vendor, cache, generated, tests, Blade excluded',
        'S':'100*D/L only completed valid analysis and L>0'}


def instrument_manifest(images):
    return {'version':VERSION,'measure':measure_binding(),'tool':tool_binding(),
        'images':dict(vars(images)),
        'sources':{name:hashlib.sha256((PACKAGE/name).read_bytes()).hexdigest() for name in SOURCES},
        'command':['php','-d','max_execution_time=0','-d','memory_limit=512M','-d','phpstan.restarted=1','/tools/launcher.php'],
        'phpstan_args':['analyse','--configuration=/tools/analysis.neon','--no-progress','--no-ansi','--error-format=json','--debug','--memory-limit=512M'],
        'network':'none','model_feedback':False,'elapsed_cutoff':None}


def select_files(original, entries, diff_paths=()):
    """Union independent full manifest comparison with supplied diff names.

    Missing/deleted files remain explicitly inventoried; never silently replace
    them with unchanged scaffold files. Public M2 module allowlist is the scope.
    """
    actual={item['path']:item for item in entries}
    if len(actual)!=len(entries):raise IntegrityError('Duplicate complete manifest path')
    for name in set(actual)|set(original)|set(diff_paths):
        p=PurePosixPath(name)
        if p.is_absolute() or '..' in p.parts or str(p)!=name or '\\' in name:
            raise IntegrityError('Unsafe manifest/diff path')
    changed={name for name,item in actual.items() if name not in original or item['sha256']!=original[name]['sha256']}
    deleted=sorted(set(original)-set(actual))
    union=changed|set(diff_paths)
    included=[];excluded={}
    for name in sorted(set(actual)|set(diff_paths)):
        parts=PurePosixPath(name).parts
        module=name=='routes/study.php' or name.startswith(('app/Study/','app/Http/Controllers/Study/'))
        restricted=any(part.lower() in ('vendor','cache','generated','tests','test','internal_tests','external_tests','evaluation') for part in parts)
        if name not in actual:excluded[name]='deleted or absent from complete manifest'
        elif not name.endswith('.php') or name.endswith('.blade.php'):excluded[name]='not module PHP or Blade'
        elif restricted:excluded[name]='dependency/cache/generated/test area'
        elif not module:excluded[name]='outside public module PHP scope'
        elif name not in union or name in original and actual[name]['sha256']==original[name]['sha256']:excluded[name]='unchanged scaffold'
        else:included.append(name)
    return {'included':included,'excluded':excluded,'deleted':deleted,'diff_union':sorted(union),
        'complete_manifest':list(entries),'selected_manifest':[actual[name] for name in included]}


def parse_diagnostics(stdout, stderr, exit_code, files):
    """Strict frozen JSON + debug path + completion validation; raw bytes stay."""
    if exit_code not in (0,1):raise IntegrityError('Tool/process abort: exit '+str(exit_code))
    begins=re.findall(rb'^M6_STATIC_BEGIN:([a-f0-9]{64})$',stderr,re.M)
    ends=re.findall(rb'^M6_STATIC_END:([a-f0-9]{64}):([01])$',stderr,re.M)
    if len(begins)!=1 or ends!=[(begins[0],str(exit_code).encode())]:
        raise IntegrityError('No unique matching completed PHPStan invocation')
    # --debug disables result cache, parallel workers and their processTimeout.
    prefix=''.join('/opt/study/'+name+'\n' for name in files).encode()
    if not stdout.startswith(prefix):raise IntegrityError('Incomplete/unexpected analysed file trace')
    try:
        report=unique_json(stdout[len(prefix):])
    except (ValueError,UnicodeError) as exc:raise IntegrityError('Malformed/mixed analysis output') from exc
    if not isinstance(report,dict) or set(report)!={'totals','files','errors'}:raise IntegrityError('Unknown diagnostic schema')
    if report['errors']!=[] or not isinstance(report['files'],(dict,list)):raise IntegrityError('Global/internal errors; incomplete analysis')
    if isinstance(report['files'],list) and report['files']!=[]:raise IntegrityError('Malformed diagnostic files')
    by_file={name:[] for name in files};diagnostics=[]
    for path,entry in dict(report['files']).items():
        if path not in {'/opt/study/'+name for name in files}:raise IntegrityError('Diagnostic outside frozen scope')
        if not isinstance(entry,dict) or set(entry)!={'errors','messages'} or type(entry['errors']) is not int or entry['errors']!=len(entry['messages']):raise IntegrityError('Diagnostic count mismatch')
        for message in entry['messages']:
            if (not isinstance(message,dict) or not isinstance(message.get('message'),str)
                or type(message.get('line')) is not int or message['line']<1
                or not isinstance(message.get('identifier'),str) or not message['identifier']
                or type(message.get('ignorable')) is not bool):raise IntegrityError('Missing file/line/identifier diagnosis')
            # Complete message objects retained unchanged, including duplicates.
            by_file[path[11:]].append(message)
            diagnostics.append({'file':path,**message})
    d=len(diagnostics)
    if (not isinstance(report['totals'],dict) or set(report['totals'])!={'errors','file_errors'}
        or any(type(value) is not int for value in report['totals'].values())
        or report['totals']!={'errors':0,'file_errors':d} or exit_code!=(1 if d else 0)):raise IntegrityError('Totals/exit mismatch')
    return {'D':d,'D_by_file':{name:len(messages) for name,messages in by_file.items()},'diagnostics':diagnostics,'original_report':report}


def parse_tokens(stdout, exit_code, selection):
    if exit_code!=0:raise IntegrityError('Lexical count failed')
    try:result=unique_json(stdout)
    except (ValueError,UnicodeError) as exc:raise IntegrityError('Invalid token output') from exc
    if result.get('version')!='M6-static-token-v1' or set(result.get('files',{}))!=set(selection['included']):raise IntegrityError('Token file manifest mismatch')
    expected={e['path']:e['sha256'] for e in selection['selected_manifest']}
    for name,item in result['files'].items():
        lines=item.get('lines')
        if not isinstance(lines,list) or any(type(i) is not int or i<1 for i in lines) or lines!=sorted(set(lines)) or type(item.get('L')) is not int or item['L']!=len(lines) or item.get('sha256')!=expected[name]:raise IntegrityError('Token/hash/physical-line mismatch')
    return {'L':sum(item['L'] for item in result['files'].values()),'L_by_file':{name:item['L'] for name,item in result['files'].items()},'tokens':result}


def unique_json(data):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise IntegrityError('Duplicate JSON key; no silent diagnostic loss')
            result[key]=value
        return result
    return json.loads(data,object_pairs_hook=unique)


def count(value,reason=None,unit='count'):
    return CountObservation(status='observed',value=value,unit=unit,source=VERSION) if value is not None else CountObservation(status='technical_missing',unit=unit,reason=reason)


class StaticAnalyzer:
    def __init__(self,sandbox):
        self.sandbox,self.register,self.store=sandbox,sandbox.register,sandbox.store
        self.active=None;self.handle=None;self.stop=threading.Event()

    def row(self,execution):
        row=self.register.connection.execute('SELECT * FROM static_execution WHERE id=?',(str(execution),)).fetchone()
        if not row:raise IntegrityError('Unknown static attempt')
        return dict(row)

    def _raw(self,execution,kind,body):
        row=self.row(execution);run=self.register.get(UUID(row['run_id']),Run)
        suite=self.register.get(run.suite_id,AssetVersion)
        artifact=self.store.json(body,run_id=run.id,artifact_type='evaluation_static_'+kind,producer='trusted_evaluator',
            original_name=execution+'-'+kind+'.json',access_scope='trusted_evaluator' if suite.suite_kind=='study_holdout' else 'trusted_register')
        with self.register.transaction():self.register.connection.execute('INSERT INTO static_observation(execution_id,happened_at,artifact_id) VALUES(?,?,?)',(execution,now().isoformat(),str(artifact.id)))
        return artifact

    def schedule(self,run_id,*,tool_id,idempotency_key):
        run=self.register.get(run_id,Run);state=self.register.state(run.id)
        tool=self.register.get(tool_id,AssetVersion)
        if state.execution!='terminal' or state.seal!='sealed':raise IntegrityError('Static analysis requires final readonly seal')
        from .instrument_compatibility import instrument_compatible, instrument_binding
        conf=self.register.get(run.configuration_version_id,ConfigurationVersion)
        if not instrument_compatible(self.register,tool.id,instrument_manifest(self.sandbox.images),conf.settings.software_commit) or not self.register.compatible_evaluation_tool(run.id,tool.id):raise IntegrityError('Static instrument not frozen/compatible')
        candidate=self.register.get(state.candidate_id,CandidateSnapshot)
        manifest=json.loads(self.store.read(candidate.file_manifest_id))
        original=scaffold_manifest(self.sandbox.assets)
        self.sandbox._asset_tree('assets/study/m2-v0.1/scaffold')
        selection=select_files(original['files'],manifest['complete_tree'])
        execution=str(uuid4());body={'version':VERSION,'candidate_hash':candidate.tree_hash,'instrument':instrument_manifest(self.sandbox.images),'instrument_compatibility':instrument_binding(self.register,tool.id,instrument_manifest(self.sandbox.images)),'selection':selection,
            'run_configuration_hash':self.register.get(run.configuration_version_id,ConfigurationVersion).content_hash,'generation_restarted':False}
        with self.register.transaction():
            old=self.register.connection.execute('SELECT s.* FROM static_execution s JOIN job_state j ON s.job_id=j.job_id WHERE j.idempotency_key=?',(idempotency_key,)).fetchone()
            if old:
                if (old['run_id'],old['candidate_id'],old['tool_id'])!=(str(run.id),str(candidate.id),str(tool.id)):raise IntegrityError('Idempotency binding collision')
                return old['id']
            job=self.register._put(Job(code='M6-STATIC-JOB-'+execution,phase_id=run.phase_id,run_id=run.id,job_type='measurement',idempotency_key=idempotency_key,suite_id=run.suite_id))
            self.register.connection.execute('INSERT INTO static_execution VALUES(?,?,?,?,?,?,?,?,?)',(execution,str(job.id),str(run.id),str(candidate.id),str(tool.id),'ready',None,canonical(body),now().isoformat()))
        self._raw(execution,'inputs',body)
        return execution

    def _compatibility(self,row):
        run=self.register.get(UUID(row['run_id']),Run);candidate=self.register.get(UUID(row['candidate_id']),CandidateSnapshot)
        conf=self.register.get(run.configuration_version_id,ConfigurationVersion)
        return Compatibility(candidate_id=candidate.id,candidate_hash=candidate.tree_hash,phase_id=run.phase_id,contract_id=conf.settings.contract_id,suite_id=run.suite_id,tool_id=UUID(row['tool_id']))

    def _measurement(self,execution,*,completed,reason,exit_code=None,report=None):
        row=self.row(execution);run=self.register.get(UUID(row['run_id']),Run)
        previous=self.register.connection.execute("SELECT revision_id,number FROM revision_binding WHERE run_id=? AND kind='measurement' AND field_key='static_DLS' ORDER BY number DESC LIMIT 1",(str(run.id),)).fetchone()
        evidence=tuple(UUID(r[0]) for r in self.register.connection.execute('SELECT artifact_id FROM static_observation WHERE execution_id=? ORDER BY id',(execution,)))
        return self.register.add(MeasurementAttempt(code='M6-STATIC-'+str(uuid4()),run_id=run.id,measurement_key='static_DLS',compatibility=self._compatibility(row),fixture_id=run.suite_id,
            revision=previous['number']+1 if previous else 1,predecessor_id=UUID(previous['revision_id']) if previous else None,completion='completed' if completed else 'draft',suite_kind=self.register.get(run.suite_id,AssetVersion).suite_kind,
            result=Observation(status='observed',value=canonical(report),unit='report',source=VERSION) if completed else Observation(status='technical_missing',unit='report',reason=reason),raw_artifact_ids=evidence,
            exit_code=IntegerObservation(status='observed',value=exit_code,unit='exit',source=VERSION) if exit_code is not None else IntegerObservation(status='technical_missing',unit='exit',reason=reason),reason=reason))

    def _release_copy(self,h):
        path=h.path/'candidate';candidate=self.register.get(UUID(h.body['candidate_id']),CandidateSnapshot)
        expected=json.loads(self.store.read(candidate.file_manifest_id))['complete_tree']
        if inventory(path)!=expected:raise IntegrityError('Static inspection copy changed; retain diagnosis')
        self.sandbox._observe(h.id,'staging_copy_released',{'candidate_hash':candidate.tree_hash,'manifest_id':str(candidate.file_manifest_id),'all_bytes_read_back':True,'original_seal_and_CAS_retained':True})
        for root,dirs,files in os.walk(path):
            os.chmod(root,0o700)
            for name in files:os.chmod(Path(root)/name,0o600)
        shutil.rmtree(path)

    def _dispatch_allowed(self,execution):
        row=self.row(execution)
        job=self.register.connection.execute('SELECT status FROM job_state WHERE job_id=?',(row['job_id'],)).fetchone()
        if self.stop.is_set() or row['status']!='running' or not job or job['status']!='running':
            raise IntegrityError('Persistent/manual stop before native dispatch')
        return row

    def _native(self,execution,profile):
        row=self._dispatch_allowed(execution);h=self.sandbox.allocate(UUID(row['job_id']),UUID(row['candidate_id']),profile=profile,evaluation=True)
        self.handle=h
        try:
            self._dispatch_allowed(execution)
            h.start();container=next(c for c in h.containers if c.name.endswith('-'+profile))
            native=container.wait();container.reload();state=container.attrs.get('State',{});diagnosis=h.observe()
            h.abort(reason='Static '+profile+' completed; evidence preserved')
            channels={channel:(h.path/(profile+'-'+channel+'.raw')).read_bytes() for channel in ('stdout','stderr')}
            raw=self._raw(execution,'native_'+profile,{'profile':profile,'sandbox_execution_id':h.id,'exit':native,'state':state,'errors':h.errors,'diagnosis_id':str(diagnosis.id),
                'stdout_hex':channels['stdout'].hex(),'stderr_hex':channels['stderr'].hex(),'stream_complete':not h.errors,'selection':h.body['static_selection']})
            self._release_copy(h)
            if self.stop.is_set() or self.row(execution)['status']=='recovery_required' or state.get('OOMKilled') or state.get('Error') or h.errors:raise IntegrityError('Interrupted/protection/infrastructure native job; raw '+str(raw.id))
            return native['StatusCode'],channels
        except BaseException:
            if self.sandbox._row(h.id)['status'] in ('allocated','starting','running'):h.abort(reason='Failed static job; preserve raw evidence')
            self._raw(execution,'native_failure',{'profile':profile,'sandbox_execution_id':h.id,'status':self.sandbox._row(h.id),'diagnostic_missing':'Native job incomplete; no invented D/S or T/F'})
            raise
        finally:self.handle=None

    def run(self,execution):
        row=self.row(execution)
        if json.loads(row['body'])['instrument']!=instrument_manifest(self.sandbox.images):raise IntegrityError('Instrument changed after scheduling')
        with self.register.transaction():
            if row['status']!='ready' or not self.register.claim_in_transaction(UUID(row['job_id']),VERSION):raise IntegrityError('No automatic retry/resume')
            self.register.connection.execute("UPDATE static_execution SET status='running' WHERE id=?",(execution,))
        self.active=execution;self.stop.clear()
        self._measurement(execution,completed=False,reason='Static measurement started; immutable draft')
        selection=json.loads(row['body'])['selection'];report={'D':None,'D_by_file':{name:None for name in selection['included']},'L':None,'L_by_file':{},'S':None,'analysis_complete':False,'analysable':False,'cause':None,'exit_code':None}
        try:
            code,streams=self._native(execution,'lines');report.update(parse_tokens(streams['stdout'],code,selection))
            if not selection['included']:raise IntegrityError('Empty changed module PHP scope; no valid analysis')
            code,streams=self._native(execution,'static');report['exit_code']=code
            report.update(parse_diagnostics(streams['stdout'],streams['stderr'],code,selection['included']))
            report['analysis_complete']=True;report['analysable']=report['L']>0
            report['S']=str(Decimal(100)*Decimal(report['D'])/Decimal(report['L'])) if report['analysable'] else None
            report['cause']=None if report['analysable'] else 'L_zero'
        except Exception as exc:
            report['cause']='manual_abort' if self.stop.is_set() or self.row(execution)['status']=='recovery_required' else type(exc).__name__+': '+str(exc)
        # Publish validity only after original seal and immutable CAS readback.
        # An integrity failure must never leave selectable completed values.
        try:self._check_original(row)
        except Exception as exc:
            report.update(D=None,D_by_file={name:None for name in selection['included']},L=None,L_by_file={},S=None,analysis_complete=False,analysable=False,cause=type(exc).__name__+': '+str(exc))
            self._raw(execution,'original_integrity_failure',{'cause':report['cause'],'candidate_id':row['candidate_id'],'no_T_F_inference':True})
        report.update(version=VERSION,candidate_hash=json.loads(row['body'])['candidate_hash'],configuration_hash=self.register.get(UUID(row['tool_id']),AssetVersion).manifest_hash,file_scope=selection['included'],excluded_files=selection['excluded'],
            run_configuration_hash=json.loads(row['body'])['run_configuration_hash'],no_T_F_inference=True)
        raw=self._raw(execution,'report',report)
        manifest=self._raw(execution,'file_manifest',selection)
        measurement=self._measurement(execution,completed=report['analysis_complete'],reason=report['cause'] or 'Completed native static analysis',exit_code=report['exit_code'],report=report)
        run=self.register.get(UUID(row['run_id']),Run);confhash=self.register.get(UUID(row['tool_id']),AssetVersion).manifest_hash
        missing=report['cause'] or 'not observed'
        profile=self.register.add(StaticProfile(code='M6-STATIC-PROFILE-'+str(uuid4()),measurement_id=measurement.id,configuration_hash=confhash,file_manifest_id=manifest.id,
            excluded_files=tuple(selection['excluded']),diagnostics_artifact_id=raw.id,d=count(report['D'],missing),l_by_file={name:count(value) for name,value in report['L_by_file'].items()},l=count(report['L'],missing),
            s=NonnegativeObservation(status='observed',value=Decimal(report['S']),unit='diagnoses_per_100_lines',source=VERSION) if report['S'] is not None else NonnegativeObservation(status='technical_missing',unit='diagnoses_per_100_lines',reason=missing),exit_code=measurement.exit_code,failure_reason=report['cause']))
        metrics={'D':profile.d,'L':profile.l,'S':profile.s,'analysable':count(int(report['analysable']),unit='binary'),'analysis_complete':count(int(report['analysis_complete']),unit='binary')}
        metrics.update({'D:'+name:count(value,missing) for name,value in report['D_by_file'].items()})
        metrics.update({'L:'+name:count(value,missing) for name,value in report['L_by_file'].items()})
        for name,value in metrics.items():
            self.register.add(MetricObservation(code='M6-STATIC-METRIC-'+str(uuid4()),run_id=run.id,phase_id=run.phase_id,metric=name,value=value,measurement_id=measurement.id,interval_ids=(),file_scope=tuple(selection['included']),excluded_files=tuple(selection['excluded']),configuration_hash=confhash,diagnostics_artifact_id=raw.id))
        with self.register.transaction():
            self.register.connection.execute('UPDATE static_execution SET status=?,measurement_id=? WHERE id=?',('completed' if report['analysis_complete'] else 'interrupted',str(measurement.id),execution))
            self.register.connection.execute('UPDATE job_state SET status=?,heartbeat_at=? WHERE job_id=?',('completed' if report['analysis_complete'] else 'stopped',now().isoformat(),row['job_id']))
        self.active=None
        return {**report,'measurement_id':str(measurement.id),'profile_id':str(profile.id),'report_artifact_id':str(raw.id)}

    def _check_original(self,row):
        from .snapshots import Snapshots
        seal=self.register.get(UUID(row['candidate_id']),CandidateSnapshot)
        body=json.loads(self.store.read(seal.file_manifest_id))
        if body['format']!='candidate-v1' or body['tree_hash']!=seal.tree_hash or digest(body['complete_tree'])!=seal.tree_hash:
            raise IntegrityError('Original candidate manifest/hash damaged')
        if tuple(body['artifact_ids'])!=tuple(str(x) for x in seal.artifact_ids) or tuple(body['dependency_ids'])!=tuple(str(x) for x in seal.dependency_ids):
            raise IntegrityError('Original artifact/dependency binding damaged')
        entries=list(zip(body['files'],body['artifact_ids'],strict=True))
        for dep_id in seal.dependency_ids:
            dep=Snapshots(self.store)._dependency(dep_id)
            entries.extend((dict(e,path='vendor/'+e['path']),aid) for e,aid in zip(dep['files'],dep['artifact_ids'],strict=True))
        if sorted([e for e,_ in entries],key=lambda e:e['path'])!=body['complete_tree']:
            raise IntegrityError('Original complete manifest damaged')
        for entry,aid in entries:
            data=self.store.read(aid)
            if len(data)!=entry['size'] or hashlib.sha256(data).hexdigest()!=entry['sha256']:
                raise IntegrityError('Original CAS binding damaged')
        original=self.store.settings.artifacts/'sealed'/str(seal.id)
        expected=[dict(entry,mode=entry['mode'] & ~0o222) for entry in body['complete_tree']]
        if inventory(original)!=expected:raise IntegrityError('Original seal changed')

    def abort(self,execution,*,reason,immediate=False):
        row=self.row(execution)
        if not reason or row['status'] not in ('ready','running','recovery_required'):raise IntegrityError('Attributed active measurement stop required')
        self.stop.set();self._raw(execution,'manual_stop',{'reason':reason,'immediate':immediate,'last_activity':self.handle.body.get('last_activity') if self.handle else None,'diagnostic_missing':'Immediate stop can retain partial logs only' if immediate else None,'no_T_F_inference':True})
        # Publish the durable Control decision before any external stop work.
        with self.register.transaction():
            self.register.connection.execute("UPDATE static_execution SET status='recovery_required' WHERE id=?",(execution,))
            self.register.connection.execute("UPDATE job_state SET status='stopped' WHERE job_id=?",(row['job_id'],))
        if self.handle:self.handle.abort(reason=reason,immediate=immediate)
        else:self.sandbox.stop_recorded(UUID(row['run_id']),reason=reason)

    def recover(self,execution,*,decision):
        row=self.row(execution)
        if decision!='stop_owned' or row['status'] not in ('ready','running','recovery_required','interrupted'):raise IntegrityError('Conscious stop_owned recovery required')
        results=self.sandbox.recover(decision=decision,run_id=UUID(row['run_id']))
        self._raw(execution,'recovery',{'decision':decision,'sandbox_execution_ids':results,'generation_restarted':False,'new_measurement_requires_new_id':True})
        with self.register.transaction():
            self.register.connection.execute("UPDATE static_execution SET status='interrupted' WHERE id=?",(execution,))
            self.register.connection.execute("UPDATE job_state SET status='stopped' WHERE job_id=?",(row['job_id'],))
        return results

    def correct_instrument(self,old_tool_id,new_tool_id,*,defect_evidence_id,reason,person):
        """All old static attempts invalidated, unchanged seals queued for new measurements.

        A rule/tool/scale change is rejected; use a new phase for that change.
        Correction registration is explicit and precedes corrected outputs.
        """
        if not reason or not person:raise IntegrityError('Attributed demonstrated defect required')
        defect=self.register.get(defect_evidence_id,Artifact)
        if not protected_evaluation(defect):raise IntegrityError('Trusted defect evidence required')
        self.store.read(defect.id)
        old=self.register.get(old_tool_id,AssetVersion);new=self.register.get(new_tool_id,AssetVersion)
        current=instrument_manifest(self.sandbox.images)
        if new.asset_type!='tool' or old.asset_type!='tool' or old.manifest_hash==new.manifest_hash or new.manifest_hash!=digest(current):raise IntegrityError('Actual distinct corrected static instrument required')
        rows=self.register.connection.execute('SELECT * FROM static_execution WHERE tool_id=?',(str(old_tool_id),)).fetchall()
        if not rows:raise IntegrityError('No affected static attempts')
        if self.register.connection.execute("SELECT 1 FROM static_execution WHERE status IN ('ready','running','recovery_required')").fetchone():raise IntegrityError('Stop affected measurements before correction')
        if any(digest(json.loads(row['body'])['instrument'])!=old.manifest_hash or json.loads(row['body'])['instrument']['measure']!=current['measure'] for row in rows):raise IntegrityError('Scientific measure drift; new phase required')
        revisions=[m for m in self.register.all(MeasurementAttempt) if m.measurement_key=='static_DLS' and m.compatibility.tool_id==old_tool_id]
        scope={row['run_id']:dict(row) for row in rows}
        evidence=self.store.json({'old_tool_id':str(old_tool_id),'new_tool_id':str(new_tool_id),'old_configuration_hash':old.manifest_hash,'new_configuration_hash':new.manifest_hash,'new_instrument':current,'measure_hash':digest(current['measure']),
            'defect_evidence_id':str(defect.id),'reason':reason,'person':person,'affected_revisions':[str(m.id) for m in revisions],'unchanged_seals':[{'run_id':row['run_id'],'candidate_id':row['candidate_id'],'candidate_hash':json.loads(row['body'])['candidate_hash']} for row in scope.values()],
            'generation_restarted':False,'scientific_measure_changed':False,'remeasurement_required':True},artifact_type='evaluation_static_correction',producer='trusted_evaluator',access_scope='trusted_evaluator')
        with self.register.transaction():
            for m in revisions:self.register._put(RevisionInvalidation(code='M6-STATIC-INVALID-'+str(uuid4()),revision_id=m.id,reason=reason,person=person,defect_evidence_id=evidence.id))
            for row in scope.values():
                run=self.register.get(UUID(row['run_id']),Run);candidate=self.register.get(UUID(row['candidate_id']),CandidateSnapshot)
                if self.register.state(run.id).candidate_id!=candidate.id or not self.register.compatible_evaluation_tool(run.id,old_tool_id):raise IntegrityError('Affected original binding changed')
                self.register.connection.execute('INSERT INTO static_tool_correction VALUES(?,?,?,?,?,?,?)',(str(run.id),str(candidate.id),candidate.tree_hash,str(old_tool_id),str(new_tool_id),digest(current['measure']),str(evidence.id)))
        return evidence

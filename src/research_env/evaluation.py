"""Independent M6 evaluator over readonly seals, with immutable M3 revisions.

Trusted Python callers schedule measurement jobs; this module has no model
transport, repair, prompt, or candidate-write interface. No elapsed-time cutoff.
"""
import base64
from datetime import datetime, timezone
import hashlib
import json
import re
import sys
import importlib.metadata
from pathlib import Path
import threading
import os
import shutil
from urllib.parse import urlencode
from uuid import UUID, uuid4

from .artifacts import IntegrityError, read_regular
from .domain import (Artifact, AssetVersion, BinaryObservation, CandidateSnapshot,
    Compatibility, ConfigurationVersion, CriterionReviewRevision, IntegerObservation,
    Job, MeasurementAttempt, Observation, RevisionInvalidation, Run, TestResult,
    canonical, digest, protected_evaluation)
from .context_assets import contract_binding
from .evaluation_oracles import PARSER, Suite, aggregate, csrf, form, parse, result, scaffold_diff

VERSION='M6-evaluator-v1'
PACKAGE=Path(__file__).parent
SOURCES=tuple(sorted(str(p.relative_to(PACKAGE)) for pattern in ('*.py','*.json','migrations/*.sql','sandbox_syntax.php') for p in PACKAGE.glob(pattern)))


def now():return datetime.now(timezone.utc)


def instrument_manifest(images):
    return {'version':VERSION,'parser':PARSER,'python':sys.version,
        'public_contract':contract_binding(PACKAGE.parents[1]),
        'libraries':{name:importlib.metadata.version(name) for name in ('beautifulsoup4','soupsieve','httpx','pymysql','pydantic','docker')},'runtime_images':dict(vars(images)),
        'sources':{name:hashlib.sha256((PACKAGE/name).read_bytes()).hexdigest() for name in SOURCES},
        'normalization':'entities/whitespace only; exact attribute values','model_feedback':False}


def observation(value=None, *, status='observed', reason=None, unit='binary'):
    return Observation(status=status,value=value,unit=unit,source=VERSION if status=='observed' else None,reason=reason)


def protected_routes(output):
    """Effective routing for the shared access contract, independent of source style."""
    rows=json.loads(output)
    if not isinstance(rows,list): raise IntegrityError('Unusable native route table')
    result=[]
    for row in rows:
        if not isinstance(row,dict): raise IntegrityError('Unusable native route entry')
        if row.get('uri') not in ('/','study-access','study-exit'): continue
        if not all(key in row for key in ('method','uri','action','middleware')): raise IntegrityError('Incomplete native access route')
        result.append({key:row.get(key) for key in ('method','uri','action','middleware','domain')})
    if not any(row['uri']=='study-access' and 'GET' in row['method'].split('|') for row in result):
        raise IntegrityError('Native access route table missing')
    return sorted(result,key=canonical)


class Evaluator:
    def __init__(self, sandbox):
        self.sandbox,self.register,self.store=sandbox,sandbox.register,sandbox.store
        self.stop=threading.Event();self.handle=None;self.active_execution=None
        self.asset_lock=json.loads(Path(__file__).with_name('evaluation_assets.lock.json').read_text())

    def _scope(self, run):
        return 'trusted_evaluator' if self.register.get(run.suite_id,AssetVersion).suite_kind=='study_holdout' else 'trusted_register'

    def _raw(self, execution, kind, body):
        row=self.row(execution);run=self.register.get(UUID(row['run_id']),Run)
        a=self.store.json(body,run_id=run.id,artifact_type='evaluation_'+kind,producer='trusted_evaluator',
                          original_name=execution+'-'+kind+'.json',access_scope=self._scope(run))
        with self.register.transaction():
            self.register.connection.execute('INSERT INTO evaluation_observation(execution_id,happened_at,artifact_id) VALUES(?,?,?)',
                (execution,now().isoformat(),str(a.id)))
        return a

    def row(self, execution):
        r=self.register.connection.execute('SELECT * FROM evaluation_execution WHERE id=?',(str(execution),)).fetchone()
        if not r:raise IntegrityError('Unknown evaluation attempt')
        return dict(r)

    def _update(self, execution, status, *, measurement_id=None, body=None):
        with self.register.transaction():
            self.register.connection.execute('UPDATE evaluation_execution SET status=?,measurement_id=COALESCE(?,measurement_id),body=COALESCE(?,body) WHERE id=?',
                (status,str(measurement_id) if measurement_id else None,canonical(body) if body is not None else None,str(execution)))

    def _contract_interpretation(self, run, conf, tool_id, binding):
        original={'run_id':str(run.id),'configuration_id':str(conf.id),'configuration_hash':conf.content_hash,
                  'contract_id':str(conf.settings.contract_id),
                  'contract_manifest_hash':self.register.get(conf.settings.contract_id,AssetVersion).manifest_hash}
        if original['contract_manifest_hash']==binding['manifest_sha256']:
            return {'mode':'direct','original_binding':original,'revision_manifest_sha256':binding['manifest_sha256']}
        correction=self.register.connection.execute('SELECT evidence_id FROM evaluation_tool_correction WHERE run_id=? AND new_tool_id=?',
                    (str(run.id),str(tool_id))).fetchone()
        if correction and self.register.compatible_evaluation_tool(run.id,tool_id):
            artifact=self.register.get(UUID(correction['evidence_id']),Artifact)
            body=json.loads(self.store.read(artifact.id))
            interpretation=body.get('contract_interpretation',{})
            if (protected_evaluation(artifact) and artifact.artifact_type=='evaluation_instrument_correction'
                and body.get('new_instrument')==instrument_manifest(self.sandbox.images)
                and interpretation.get('revision_manifest_sha256')==binding['manifest_sha256']
                and original in interpretation.get('original_bindings',[])):
                return {'mode':'explicit_same_seal_correction','original_binding':original,
                        'revision_manifest_sha256':binding['manifest_sha256'],'correction_evidence_id':str(artifact.id)}
        raise IntegrityError('Messkonfiguration bindet nicht die aktive öffentliche Vertragsrevision oder ausdrückliche Same-Seal-Auslegung')

    def schedule(self, run_id, *, tool_id, idempotency_key):
        """An atomic explicit new measurement, never an implicit retry/generation."""
        run=self.register.get(run_id,Run);state=self.register.state(run_id)
        conf=self.register.get(run.configuration_version_id,ConfigurationVersion)
        suite=self.register.get(run.suite_id,AssetVersion)
        if run.purpose in ('free_test','demo') and suite.suite_kind!='development':raise IntegrityError('Free tests/demo never read study_holdout')
        if state.execution!='terminal' or state.seal not in ('sealed','no_candidate'):raise IntegrityError('Generation/Seal not complete')
        from .instrument_compatibility import instrument_compatible, instrument_binding
        if not self.register.compatible_evaluation_tool(run_id,tool_id) or not instrument_compatible(self.register,tool_id,instrument_manifest(self.sandbox.images),conf.settings.software_commit):
            raise IntegrityError('Evaluator instrument is not frozen in configuration')
        previous=self.register.connection.execute('SELECT e.* FROM evaluation_execution e JOIN job_state j ON e.job_id=j.job_id WHERE j.idempotency_key=?',(idempotency_key,)).fetchone()
        if previous and tuple(previous[key] for key in ('run_id','candidate_id','suite_id','tool_id'))!=(str(run.id),str(state.candidate_id) if state.candidate_id else None,str(run.suite_id),str(tool_id)):
            raise IntegrityError('Evaluation idempotency key belongs to a different run/candidate/suite/tool binding')
        binding=contract_binding(self.sandbox.assets)
        interpretation=self._contract_interpretation(run,conf,tool_id,binding)
        relative=suite.suite_kind+('/m6-v1' if suite.suite_kind=='development' else '/m2-v0.1')
        expected=self.asset_lock[relative]
        for name,sha in expected.items():
            data,_=read_regular(self.sandbox.assets/'evaluation'/relative,name)
            if hashlib.sha256(data).hexdigest()!=sha:raise IntegrityError('Frozen suite input changed: '+name)
        # Development has its own cases; never derive them from holdout files.
        if suite.manifest_hash!=digest(expected):raise IntegrityError('Registered suite does not match frozen independent bytes')
        execution=str(uuid4())
        body={'version':VERSION,'candidate_hash':self.register.get(state.candidate_id,CandidateSnapshot).tree_hash if state.candidate_id else None,
              'revision':1+len(self.register.connection.execute('SELECT id FROM evaluation_execution WHERE run_id=?',(str(run.id),)).fetchall()),
              'instrument':instrument_manifest(self.sandbox.images),'instrument_compatibility':instrument_binding(self.register,tool_id,instrument_manifest(self.sandbox.images)),'suite_hash':suite.manifest_hash,
              'contract_manifest_sha256':binding['manifest_sha256'],'contract_interpretation':interpretation,'generation_restarted':False}
        with self.register.transaction():
            old=self.register.connection.execute('SELECT e.* FROM evaluation_execution e JOIN job_state j ON e.job_id=j.job_id WHERE j.idempotency_key=?',(idempotency_key,)).fetchone()
            if old:
                binding=(str(run.id),str(state.candidate_id) if state.candidate_id else None,str(run.suite_id),str(tool_id))
                if tuple(old[key] for key in ('run_id','candidate_id','suite_id','tool_id'))!=binding:
                    raise IntegrityError('Evaluation idempotency key belongs to a different run/candidate/suite/tool binding')
                return old['id']
            job=self.register._put(Job(code='M6-JOB-'+execution,phase_id=run.phase_id,run_id=run.id,job_type='measurement',idempotency_key=idempotency_key,suite_id=run.suite_id))
            self.register.connection.execute('INSERT INTO evaluation_execution VALUES(?,?,?,?,?,?,?,?,?,?)',
                (execution,str(job.id),str(run.id),str(state.candidate_id) if state.candidate_id else None,str(run.suite_id),str(tool_id),'ready',None,canonical(body),now().isoformat()))
        self._raw(execution,'inputs',body)
        return execution

    def _compatibility(self,row):
        run=self.register.get(UUID(row['run_id']),Run);candidate=self.register.get(UUID(row['candidate_id']),CandidateSnapshot)
        conf=self.register.get(run.configuration_version_id,ConfigurationVersion)
        return Compatibility(candidate_id=candidate.id,candidate_hash=candidate.tree_hash,phase_id=run.phase_id,
            contract_id=conf.settings.contract_id,suite_id=run.suite_id,tool_id=UUID(row['tool_id']))

    def _revision(self,run_id,kind,key):
        old=self.register.connection.execute('SELECT revision_id,number FROM revision_binding WHERE run_id=? AND kind=? AND field_key=? ORDER BY number DESC LIMIT 1',(str(run_id),kind,key)).fetchone()
        return (old['number']+1,UUID(old['revision_id'])) if old else (1,None)

    def _measurement(self, execution, *, completion, entries, reason, measurement_key='functional_R'):
        row=self.row(execution);run=self.register.get(UUID(row['run_id']),Run)
        evidence=tuple(UUID(r[0]) for r in self.register.connection.execute('SELECT artifact_id FROM evaluation_observation WHERE execution_id=? ORDER BY id',(execution,)))
        revision, predecessor=self._revision(run.id,'measurement',measurement_key)
        value=MeasurementAttempt(code='M6-MEASUREMENT-'+str(uuid4()),run_id=run.id,measurement_key=measurement_key,compatibility=self._compatibility(row),
            fixture_id=run.suite_id,revision=revision,predecessor_id=predecessor,completion=completion,suite_kind=self.register.get(run.suite_id,AssetVersion).suite_kind,
            result=observation(canonical({'assertions':len(entries)}),unit='report') if completion=='completed' else observation(status='pending',reason=reason,unit='report'),
            raw_artifact_ids=evidence,exit_code=IntegerObservation(status='observed',value=0,unit='exit',source=VERSION) if completion=='completed' else IntegerObservation(status='pending',unit='exit',reason=reason),reason=reason)
        with self.register.transaction():
            m=self.register._put(value)
            for item in entries:
                actual=observation(canonical(item.get('actual')),unit='oracle') if item['status'] in ('passed','failed','blocked_candidate') else observation(status='technical_missing',reason=item['cause'],unit='oracle')
                self.register._put(TestResult(code='M6-ASSERTION-'+str(uuid4()),measurement_id=m.id,test_id=item['case_id'],assertion_id=item['id'],
                    r_category=item['category'],fixture_id=run.suite_id,input_artifact_id=UUID(item['input_artifact_id']),expected=canonical(item['expected']),
                    actual=actual,status=item['status'],cause=item['cause'],raw_artifact_id=UUID(item['raw_artifact_id'])))
        return m

    def _review(self,execution,criterion,value,reason,*,code_id=None,measurement_ids=(),completion=None,person=VERSION,origin='automatic'):
        row=self.row(execution);run=self.register.get(UUID(row['run_id']),Run);conf=self.register.get(run.configuration_version_id,ConfigurationVersion)
        revision,predecessor=self._revision(run.id,'review',criterion)
        return self.register.add(CriterionReviewRevision(code='M6-'+criterion+'-'+str(uuid4()),run_id=run.id,criterion=criterion,compatibility=self._compatibility(row),
            rubric_id=conf.settings.rubric_id,revision=revision,predecessor_id=predecessor,completion=completion or ('completed' if value is not None and criterion in ('T1','T5') else 'draft'),
            verdict=BinaryObservation(status='observed',value=value,unit='binary',source=VERSION) if value is not None else BinaryObservation(status='pending',unit='binary',reason=reason),
            reason=reason,file_path=None,lines=None,code_artifact_id=code_id,measurement_ids=measurement_ids,person=person,reviewer_origin=origin,reviewed_at=now()))

    def _release_copy(self,h):
        """Drop only verified reconstructable staging code; CAS/seal/raw stay."""
        from .snapshots import inventory
        path=h.path/'candidate'
        candidate=self.register.get(UUID(h.body['candidate_id']),CandidateSnapshot)
        expected=json.loads(self.store.read(candidate.file_manifest_id))['complete_tree']
        if inventory(path)!=expected:raise IntegrityError('Temporary inspection copy changed; preserve for diagnosis')
        self.sandbox._observe(h.id,'staging_copy_released',{'candidate_hash':candidate.tree_hash,'manifest_id':str(candidate.file_manifest_id),
            'path':str(path),'all_bytes_read_back':True,'original_seal_and_CAS_retained':True})
        for root,dirs,files in os.walk(path):
            os.chmod(root,0o700)
            for name in files:os.chmod(Path(root)/name,0o600)
        shutil.rmtree(path)

    def _native(self,execution,profile):
        row=self.row(execution);h=self.sandbox.allocate(UUID(row['job_id']),UUID(row['candidate_id']),profile=profile,evaluation=True)
        self.handle=h;h.start()
        c=next(c for c in h.containers if c.name.endswith('-syntax' if profile=='syntax' else '-candidate'))
        native=c.wait();c.reload();state=c.attrs.get('State',{});diagnosis=h.observe()
        h.abort(reason='M6 '+profile+' native check completed')
        self.handle=None
        self._release_copy(h)
        logs=[]
        for link in self.register.connection.execute("SELECT artifact_id FROM sandbox_observation WHERE execution_id=? AND kind='log_retained'",(h.id,)):
            meta=json.loads(self.store.read(link[0]));a=self.register.get(UUID(meta['artifact_id']),Artifact)
            logs.append({'artifact_id':str(a.id),'sha256':a.sha256,'content':self.store.read(a.id).decode('utf8','replace')})
        technical=bool(state.get('OOMKilled') or state.get('Error') or h.errors or self._stopped() or native['StatusCode']>=128 and native['StatusCode']!=255)
        combined='\n'.join(x['content'] for x in logs)
        # Fixed syntax checker never executes candidate PHP. Boot failures
        # require a concrete changed module source location, not generic fatal
        # text from infrastructure/dependencies/DB or the trusted wrapper.
        changed=json.loads(self.store.read(self.register.get(UUID(row['candidate_id']),CandidateSnapshot).file_manifest_id))['files']
        paths=[e['path'] for e in changed if e['path']=='routes/study.php' or e['path'].startswith(('app/Study/','app/Http/Controllers/Study/'))]
        located=any('/opt/study/'+p in combined for p in paths)
        infrastructure=any(x in combined for x in ('SQLSTATE[','Connection refused','No such file or directory','Permission denied','Allowed memory size','in Command line code'))
        failure=located and not infrastructure and (profile=='syntax' and native['StatusCode']==1 and ('Parse error' in combined or 'Errors parsing' in combined) or profile=='boot' and any(x in combined for x in ('Fatal error:','Uncaught ','PHP Fatal error')))
        verdict=1 if native['StatusCode']==0 and not technical else 0 if failure and not technical else None
        a=self._raw(execution,'native_'+profile,{'profile':profile,'execution_id':h.id,'exit':native,'state':state,'diagnosis_id':str(diagnosis.id),'logs':logs,'verdict':verdict})
        return verdict,a

    def verify_control(self,execution):
        """Native syntax+boot of an exactly intact sealed scaffold, before outputs."""
        row=self.row(execution);candidate=self.register.get(UUID(row['candidate_id']),CandidateSnapshot)
        self.sandbox._asset_tree('assets/study/m2-v0.1/scaffold')
        schema=(self.sandbox.assets/'assets/study/m2-v0.1/scaffold/database/users.sql').read_bytes()
        if schema!=(self.sandbox.assets/'docs/vertraege/m2-v0.1/users.sql').read_bytes():raise IntegrityError('Independent schema source differs from original users.sql')
        expected_manifest=json.loads((PACKAGE/'pipeline_assets.lock.json').read_text())['assets/study/m2-v0.1/manifest.json']
        if hashlib.sha256((self.sandbox.assets/'assets/study/m2-v0.1/manifest.json').read_bytes()).hexdigest()!=expected_manifest:raise IntegrityError('Original scaffold manifest changed')
        manifest=json.loads((self.sandbox.assets/'assets/study/m2-v0.1/manifest.json').read_text())
        entries=json.loads(self.store.read(candidate.file_manifest_id))['files']
        actual={e['path']:e['sha256'] for e in entries}
        if actual!={p:f['sha256'] for p,f in manifest['files'].items()}:raise IntegrityError('Environment control must be exactly intact scaffold')
        self._claim(execution)
        observations=[]
        for profile in ('syntax','boot'):
            verdict,raw=self._native(execution,profile);observations.append({'profile':profile,'verdict':verdict,'artifact_id':str(raw.id)})
        h=self.sandbox.allocate(UUID(row['job_id']),candidate.id,evaluation=True);self.handle=h
        h.start();self._ready(h)
        routes=self._routes(execution,h)
        database,dbraw=self._request(h,method='EVALUATION_DB',path='/users')
        h.abort(reason='Intact independent schema control completed');self.handle=None;self._release_copy(h)
        a=self._raw(execution,'environment_control',{'database':database,'schema_observation_id':str(dbraw.id),
            'users_sql_sha256':hashlib.sha256((self.sandbox.assets/'docs/vertraege/m2-v0.1/users.sql').read_bytes()).hexdigest(),'version':VERSION,'passed':all(x['verdict']==1 for x in observations),
            'scaffold_manifest_sha256':hashlib.sha256((self.sandbox.assets/'assets/study/m2-v0.1/manifest.json').read_bytes()).hexdigest(),
            'instrument':instrument_manifest(self.sandbox.images),'observations':observations,'protected_routes':routes})
        self._finish(execution,None)
        return a

    def _claim(self, execution):
        row=self.row(execution)
        with self.register.transaction():
            if row['status']!='ready' or not self.register.claim_in_transaction(UUID(row['job_id']),VERSION):raise IntegrityError('Measurement already claimed; recovery must be conscious')
            changed=self.register.connection.execute("UPDATE evaluation_execution SET status='running' WHERE id=? AND status='ready'",(execution,)).rowcount
            if changed!=1:raise IntegrityError('Attempt stopped before successful claim')
        self.active_execution=execution
        self.stop.clear()  # Explicit successful new attempt; old receipts stay.


    def _finish(self,execution,measurement):
        with self.register.transaction():
            self.register.connection.execute("UPDATE evaluation_execution SET status='completed',measurement_id=? WHERE id=?",(str(measurement.id) if measurement else None,execution))
            self.register.connection.execute("UPDATE job_state SET status='completed',heartbeat_at=? WHERE job_id=?",(now().isoformat(),self.row(execution)['job_id']))

    def _control(self,artifact_id):
        a=self.register.get(artifact_id,Artifact);body=json.loads(self.store.read(a.id))
        if a.producer!='trusted_evaluator' or a.artifact_type!='evaluation_environment_control' or not body.get('passed') or body.get('instrument')!=instrument_manifest(self.sandbox.images):
            raise IntegrityError('No matching completed intact environment control')
        manifest=self.sandbox.assets/'assets/study/m2-v0.1/manifest.json'
        if body['scaffold_manifest_sha256']!=hashlib.sha256(manifest.read_bytes()).hexdigest():raise IntegrityError('Control scaffold version changed')
        if body['users_sql_sha256']!=hashlib.sha256((self.sandbox.assets/'docs/vertraege/m2-v0.1/users.sql').read_bytes()).hexdigest():raise IntegrityError('Control users.sql binding changed')
        self.control_schema=body['database']['ddl']
        return body

    def _request(self,h,**request):
        response,a=h.request(**request)
        if response.get('transport_error'):raise IntegrityError('Measurement transport incomplete: '+str(response['transport_error']))
        return response,a

    def _stopped(self):
        if self.active_execution and self.row(self.active_execution)['status']=='recovery_required':self.stop.set()
        return self.stop.is_set()

    def _routes(self,execution,h):
        native=next(c for c in h.containers if c.name.endswith('-candidate')).exec_run(['php','-d','max_execution_time=0','artisan','route:list','--json'])
        body=native.output.decode('utf8','replace')
        self._raw(execution,'routes',{'exit':native.exit_code,'stdout':body,'human_path_review':True})
        if native.exit_code!=0:raise IntegrityError('Native route inspection failed; no inferred candidate failure')
        return protected_routes(body)

    def _ready(self,h):
        while not self._stopped():
            h.observe()
            if h.evaluation_reader():break
            self.stop.wait(.1)
        if self._stopped():raise InterruptedError('Voluntary stop before DB readiness')
        while not self._stopped():
            response,a=h.request(method='GET',path='/study-access')
            if 'transport_error' not in response:
                if response.get('status')!=200:raise IntegrityError('Scaffold access/HTTP prerequisite failed')
                return response
            h.observe();self.stop.wait(.1)
        raise InterruptedError('Voluntary stop before HTTP readiness')

    def _login(self,h):
        r,_=self._request(h,method='GET',path='/study-access');token=csrf(parse(r['body']))
        r,_=self._request(h,method='POST',path='/study-access',data={'_token':token,'access_token':h.body['access_token']})
        if r.get('status')!=302:raise IntegrityError('Fresh authenticated scaffold session unavailable')
        r,_=self._request(h,method='GET',path='/study-access')
        return csrf(parse(r['body']))

    def _snapshot(self,h):
        h.evaluation_pause(True)
        try:
            db,dbraw=self._request(h,method='EVALUATION_DB',path='/users')
            files,filesraw=self._request(h,method='EVALUATION_FILES',path='/uploads')
            session,sessionraw=self._request(h,method='EVALUATION_SESSION',path='/session')
            return {'database':db,'uploads':files['uploads'],'session':session['sessions'],'csrf_tokens':session['csrf_tokens']},(dbraw.id,filesraw.id,sessionraw.id)
        finally:h.evaluation_pause(False)

    def _db_preflight(self,db,rows):
        expected=[{k:str(v) if v is not None else None for k,v in row.items()} for row in sorted(rows,key=lambda r:r['user_id'])]
        modes=sorted(('ONLY_FULL_GROUP_BY','STRICT_TRANS_TABLES','NO_ZERO_IN_DATE','NO_ZERO_DATE','ERROR_FOR_DIVISION_BY_ZERO','NO_ENGINE_SUBSTITUTION'))
        settings=['utf8mb4','utf8mb4_bin','+00:00',modes,'utf8mb4','utf8mb4_bin','+00:00',modes]
        if db['rows']!=expected or db['tables']!=[['users','BASE TABLE']] or db['settings']!=settings or db['ddl']!=self.control_schema:
            raise IntegrityError('Independent DB fixture/table/settings preflight failed')
        if any('ALL PRIVILEGES' in grant or 'INSERT' in grant or 'UPDATE' in grant or 'DELETE' in grant for grant in db['reader_grants']):
            raise IntegrityError('Evaluator observation credential is not read-only')

    def _case_setup(self,execution,h,suite,case):
        files={name:base64.b64encode(suite.file(fixture)[0]).decode() for name,fixture in suite.fixtures['initial_uploads'].items()}
        h.evaluation_pause(True)
        try:reset=h.evaluation_control('reset',{'files':files})
        finally:h.evaluation_pause(False)
        rows=suite.fixtures['datasets'][case['setup']['database']]
        self._request(h,method='EVALUATION_FIXTURE',path='/users',data={'rows':rows})
        token=self._login(h)
        baseline,raws=self._snapshot(h);self._db_preflight(baseline['database'],rows)
        if baseline['uploads']!={name:{'size':suite.fixtures['files'][f]['size'],'sha256':suite.fixtures['files'][f]['sha256']} for name,f in suite.fixtures['initial_uploads'].items()}:
            raise IntegrityError('Independent upload reset preflight failed')
        if len(baseline['session'])!=1 or baseline['session'][0]['auth_identity']!=['scaffold-operator']:
            raise IntegrityError('Trusted scaffold authentication preflight failed')
        control=self._raw(execution,'reset',{'case_id':case['id'],'reset':reset,'baseline':baseline,'observation_ids':[str(x) for x in raws],
            'fresh_case':True,'reset_between_steps':False,'execution_id':h.id})
        return self._session_token(baseline),baseline,control

    @staticmethod
    def _session_token(snapshot):
        tokens=snapshot['csrf_tokens']
        if len(tokens)!=1:raise IntegrityError('One authenticated scaffold CSRF session required')
        return next(iter(tokens.values()))

    def _step_request(self,h,suite,spec,token):
        fields=dict(spec['fields'])
        if '_token' in fields:fields['_token']=token
        request={'method':spec['method'],'path':spec['path']}
        if spec['encoding']=='query':
            if fields:request['path']+='?'+urlencode(fields)
        else:
            request['data']=fields
            file=spec.get('file')
            if file and file['mode']=='present':
                data,kind=suite.file(file['fixture']);request['files']={'uploaded':{'name':file['filename'],'hex':data.hex(),'type':kind}}
            elif file and file['mode']=='empty':request['files']={'uploaded':{'name':'','hex':'','type':'application/octet-stream'}}
            else:
                # Force multipart even for absent file part, without inventing a
                # zero-byte named file in the legitimate upload domain.
                request['files']={'_m6_boundary':{'name':'','hex':'','type':'application/octet-stream'}}
        return self._request(h,**request),request

    @staticmethod
    def _prerequisite(t1,gap,rights_effective,*,access_fault=False):
        if access_fault:return 'blocked_candidate','Candidate overrides protected access routes; intact environment route control differs'
        if t1==0:return 'blocked_candidate','Proven candidate syntax/boot failure with intact environment control'
        if t1 is None or gap or not rights_effective:
            return 'technical_missing',gap or ('Ineffective write fault' if not rights_effective else 'Technical native prerequisite missing')
        return None,None

    def _compare(self, assertion, response, snapshot, baseline):
        expected=assertion['expected'];target=assertion['target']
        soup=parse(response.get('body',''))
        if target=='response.status':
            actual=response.get('status')
            content_type=response.get('headers',{}).get('content-type','').lower()
            if 'text/html' not in content_type or 'charset=utf-8' not in content_type:actual={'status':actual,'invalid_content_type':content_type}
        elif target=='dom.result':actual=result(soup)
        elif target=='dom.form':
            token=self._session_token(snapshot) if expected.get('valid_csrf_token') else None
            if expected.get('valid_csrf_token'):expected={**expected,'csrf_token':token}
            actual=form(soup,expected,trusted_token=token)
        elif target=='database.full_schema_and_rows':actual=snapshot['database'];expected=baseline['database']
        elif target=='uploads.inventory':actual=snapshot['uploads']
        elif target=='session.auth_identity_and_id':actual=snapshot['session'];expected=baseline['session']
        else:raise IntegrityError('Unknown frozen assertion target '+target)
        return actual,expected,actual==expected

    def _code_evidence(self,execution,candidate):
        manifest=json.loads(self.store.read(candidate.file_manifest_id));sources=[]
        for entry,artifact_id in zip(manifest['files'],manifest['artifact_ids'],strict=True):
            if entry['path'].endswith(('.php','.blade.php')) and (entry['path']=='routes/study.php' or entry['path'].startswith(('app/Http/Controllers/Study/','app/Study/','resources/views/study/'))):
                data=self.store.read(UUID(artifact_id))
                sources.append({'path':entry['path'],'sha256':entry['sha256'],'lines':[{'line':i,'source':v} for i,v in enumerate(data.decode('utf8','replace').splitlines(),1)]})
        return self._raw(execution,'code_paths',{'sources':sources,'T2_T3_T4':'human review required; no filename/style inference','candidate_hash':candidate.tree_hash})

    def run(self,execution,*,control_id,semantic_t5_doubt=False,cause_evidence_id=None):
        row=self.row(execution);run=self.register.get(UUID(row['run_id']),Run)
        if row['candidate_id'] is None:return self.run_absent(execution,cause_evidence_id=cause_evidence_id)
        control=self._control(control_id);self._claim(execution)
        candidate=self.register.get(UUID(row['candidate_id']),CandidateSnapshot)
        kind=self.register.get(run.suite_id,AssetVersion).suite_kind;relative=kind+('/m6-v1' if kind=='development' else '/m2-v0.1')
        suite=Suite(self.sandbox.assets/'evaluation'/relative,kind=kind,expected_hashes=self.asset_lock[relative])
        module=self.register.get(run.configuration_version_id,ConfigurationVersion).cell.module
        cases=suite.cases_for(module);entries=[];t1=None;t5=None;h=None;interrupted=False;access_fault=False;access_evidence=None
        self._measurement(execution,completion='draft',entries=(),reason='Independent evaluation started; original draft remains')
        code=self._code_evidence(execution,candidate)
        original=json.loads((self.sandbox.assets/'assets/study/m2-v0.1/manifest.json').read_text())
        diff=scaffold_diff(original,json.loads(self.store.read(candidate.file_manifest_id))['files'])
        t5=0 if any(diff.values()) else None if semantic_t5_doubt else 1
        self._raw(execution,'T5_diff',{'candidate_hash':candidate.tree_hash,'diff':diff,'semantic_doubt':semantic_t5_doubt})
        native_evidence=code
        try:
            syntax,syntaxraw=self._native(execution,'syntax')
            native_evidence=syntaxraw
            if syntax==0:t1=0
            elif syntax==1:
                boot,bootraw=self._native(execution,'boot');t1=boot;native_evidence=bootraw
            if t1==1:
                h=self.sandbox.allocate(UUID(row['job_id']),candidate.id,evaluation=True);self.handle=h;h.start()
                routes=self._routes(execution,h)
                expected=control.get('protected_routes')
                if not expected:raise IntegrityError('Intact control has no native protected route table; repeat environment control')
                access_fault=routes!=expected
                access_evidence=self._raw(execution,'protected_routes',{'candidate_hash':candidate.tree_hash,'control_id':str(control_id),
                    'expected':expected,'actual':routes,'changed':access_fault})
                if access_fault:t5=0
                else:self._ready(h)
            for case in cases:
                if self._stopped():raise InterruptedError('Immediate voluntary stop')
                baseline=None;token=None;rights_effective=True;gap=None
                inputraw=self._raw(execution,'case_input',case)
                if t1==1 and not access_fault:
                    try:
                        token,baseline,reset=self._case_setup(execution,h,suite,case)
                        if case['setup']['fault']:
                            h.upload_permissions(writable=False);h.evaluation_pause(True)
                            try:control=h.evaluation_control('rights_probe')
                            finally:h.evaluation_pause(False)
                            self._raw(execution,'rights_control',{'case_id':case['id'],'control':control})
                            rights_effective=control['write_denied']
                    except Exception as exc:gap=str(exc)
                for step in case['steps']:
                    response=None;snapshot=None;stepraw=None
                    if t1==1 and not access_fault and not gap and rights_effective:
                        try:
                            (response,httpraw),sent=self._step_request(h,suite,step['request'],token)
                            snapshot,raws=self._snapshot(h)
                            token=self._session_token(snapshot)
                            stepraw=self._raw(execution,'step',{'case_id':case['id'],'step':step['number'],'sent':sent,'response':response,'snapshot':snapshot,
                                'http_artifact_id':str(httpraw.id),'observation_ids':[str(x) for x in raws],'same_execution':h.id,'reset_between_steps':False})
                        except Exception as exc:gap=str(exc)
                    for assertion in step['assertions']:
                        cause='independent public-contract comparison';actual=None;resolved_expected=assertion['expected']
                        status,prerequisite_cause=self._prerequisite(t1,gap,rights_effective,access_fault=access_fault)
                        if status:cause=prerequisite_cause
                        else:
                            try:actual,resolved_expected,passed=self._compare(assertion,response,snapshot,baseline);status='passed' if passed else 'failed'
                            except Exception as exc:status='technical_missing';cause=str(exc)
                        entry={'case_id':case['id'],'category':case['category'],'id':assertion['id'],'expected':resolved_expected,'expected_contract_selector':assertion['expected'],'actual':actual,'status':status,'cause':cause,
                               'input_artifact_id':str(inputraw.id),'raw_artifact_id':str((access_evidence if access_fault else stepraw or inputraw).id)}
                        entries.append(entry)
                        self._raw(execution,'assertion',entry)
                if h and case['setup']['fault']:
                    h.upload_permissions(writable=True)
        except (Exception,KeyboardInterrupt) as exc:
            interrupted=True
            if isinstance(exc,(KeyboardInterrupt,InterruptedError)):self.stop.set()
            self._raw(execution,'interruption',{'reason':str(exc),'manual_abort':self.stop.is_set(),'diagnostic_missing':'Only completed observations are available; no elapsed-duration inference'})
        finally:
            if h:
                try:h.abort(reason='M6 evaluation completed or consciously interrupted')
                except Exception as exc:self._raw(execution,'cleanup_failure',{'reason':str(exc)});interrupted=True
                self.handle=None
                self._release_copy(h)
        # Keep all intended assertions in the denominator, including interrupted
        # cases; no unexecuted case quietly disappears and no gap becomes zero.
        known={e['id'] for e in entries}
        for case in cases:
            for step in case['steps']:
                for a in step['assertions']:
                    if a['id'] not in known:
                        raw=self._raw(execution,'missing_assertion',{'id':a['id'],'reason':'unexecuted after interrupted measurement'})
                        entries.append({'case_id':case['id'],'category':case['category'],'id':a['id'],'expected':a['expected'],'actual':None,'status':'technical_missing',
                            'cause':'manual_abort' if self.stop.is_set() else 'incomplete_measurement','input_artifact_id':str(raw.id),'raw_artifact_id':str(raw.id)})
        m=self._measurement(execution,completion='draft' if interrupted else 'completed',entries=entries,reason='Interrupted immutable draft; previous valid completed evidence remains selectable' if interrupted else 'Completed evidence record; missing observations remain missing')
        integration=self._measurement(execution,completion='draft' if interrupted else 'completed',entries=[e for e in entries if e['category']=='R5'],
            reason='Independent varying DB/file fixtures for human T4 integration review',measurement_key='T4_integration')
        reviews=[self._review(execution,'T1',t1,'Native syntax/boot plus independent intact scaffold control',code_id=native_evidence.id),
            self._review(execution,'T5',t5,'Protected scaffold/allowlist and effective access routes against intact control; other semantic doubt stays open',code_id=access_evidence.id if access_fault else code.id)]
        for criterion in ('T2','T3','T4'):
            reviews.append(self._review(execution,criterion,None,'Human code/path review required for every existing candidate, including T=0',code_id=code.id,measurement_ids=(integration.id,) if criterion=='T4' else ()))
        values={'T1':t1,'T2':None,'T3':None,'T4':None,'T5':t5}
        report=aggregate(entries,values);report.update(T_criteria=values,version=VERSION,run_id=str(run.id),candidate_hash=candidate.tree_hash,measurement_id=str(m.id),
            review_revision_ids=[str(x.id) for x in reviews],manual_T2_T3_T4='open',T4_integration_measurement_id=str(integration.id),interrupted=interrupted,completion=m.completion,entries=entries,
            case_count=len(cases),raw_pass_ratio={'numerator':sum(all(e['status']=='passed' for e in entries if e['case_id']==c['id']) for c in cases),'denominator':len(cases)})
        raw=self._raw(execution,'receipt',report)
        report['receipt_artifact_id']=str(raw.id)
        self._finish(execution,m)
        # Hash-verified original seal is checked again; measurement never writes it.
        original_path=self.store.settings.artifacts/'sealed'/str(candidate.id)
        from .snapshots import inventory
        expected=[dict(e,mode=e['mode']&~0o222) for e in json.loads(self.store.read(candidate.file_manifest_id))['complete_tree']]
        if inventory(original_path)!=expected:raise IntegrityError('Original sealed candidate changed during evaluation')
        return report

    def run_absent(self,execution,*,cause_evidence_id=None):
        row=self.row(execution);run=self.register.get(UUID(row['run_id']),Run);state=self.register.state(run.id)
        if row['candidate_id'] or state.seal!='no_candidate':raise IntegrityError('Absence classification requires real recorded absence')
        diagnosis=None
        if cause_evidence_id:
            proof=self.register.get(cause_evidence_id,Artifact)
            if proof.run_id!=run.id or proof.artifact_type!='candidate_absence_diagnosis' or proof.producer!='trusted_evaluator':raise IntegrityError('Absence proof must be an independently attributed same-run diagnosis')
            diagnosis=json.loads(self.store.read(proof.id))
            for aid in diagnosis.get('source_artifact_ids',[]):
                source=self.register.get(UUID(aid),Artifact)
                if source.run_id!=run.id:raise IntegrityError('Foreign absence evidence')
                self.store.read(source.id)
        self._claim(execution)
        proven=bool(state.terminal_cause=='content_failure' and diagnosis and diagnosis.get('cause')=='proven_generation_failure' and diagnosis.get('generation_completed') is True and diagnosis.get('source_artifact_ids') and diagnosis.get('reason'))
        cause='proven_generation_failure' if proven else 'manual_abort' if state.terminal_cause=='interrupted' else 'unclear_api_outcome' if state.terminal_cause=='outcome_unknown' else 'provider_or_infrastructure_no_candidate'
        suite_kind=self.register.get(run.suite_id,AssetVersion).suite_kind
        relative=suite_kind+('/m6-v1' if suite_kind=='development' else '/m2-v0.1')
        suite=Suite(self.sandbox.assets/'evaluation'/relative,kind=suite_kind,expected_hashes=self.asset_lock[relative])
        module=self.register.get(run.configuration_version_id,ConfigurationVersion).cell.module
        cases=suite.cases_for(module);entries=[]
        for case in cases:
            inputraw=self._raw(execution,'absence_case_input',case)
            for step in case['steps']:
                for assertion in step['assertions']:
                    entries.append({'case_id':case['id'],'category':case['category'],'step':step['number'],'id':assertion['id'],
                        'status':'blocked_candidate' if proven else 'technical_missing','z':0 if proven else None,'cause':cause,
                        'expected':assertion['expected'],'actual':None,'comparison_executed':False,'input_artifact_id':str(inputraw.id)})
        report={'entries':entries,'case_count':len(cases),'assertions':len(entries),'version':VERSION,'run_id':str(run.id),'candidate_hash':None,'R':{f'R{i}':0 if proven else None for i in range(1,7)},
            'T':0 if proven else None,'F':{'numerator':0,'denominator':6} if proven else None,'complete':0 if proven else None,
            'status':'blocked_by_candidate' if proven else 'unclear' if state.terminal_cause=='outcome_unknown' else 'technical_missing','cause':cause,
            'T_criteria':{'T1':0 if proven else None, 'T2':None,'T3':None,'T4':None,'T5':None},
            'generation_technical_evidence_ids':[str(x) for x in run.technical_evidence_ids],
            'attributed_cause_evidence_id':str(cause_evidence_id) if cause_evidence_id else None,'attribution':diagnosis,
            'absence_revision':json.loads(row['body'])['revision'],'no_fabricated_candidate_or_human_verdict':True}
        self._raw(execution,'absence_receipt',report);self._finish(execution,None);return report

    def abort(self,execution,*,reason,immediate=True):
        """Voluntary stop from this controller or a separate trusted control process."""
        self.stop.set();row=self.row(execution)
        self._raw(execution,'stop_requested',{'reason':reason,'immediate':immediate,'duration_based_classification':False})
        self._update(execution,'recovery_required')
        self.sandbox.stop_recorded(UUID(row['run_id']),reason=reason)

    def recover(self,execution,*,decision):
        if decision!='stop_owned':raise IntegrityError('Conscious stop/recovery required')
        row=self.row(execution)
        if row['status'] not in ('running','recovery_required'):raise IntegrityError('No orphaned active measurement')
        self.sandbox.recover(decision=decision,run_id=UUID(row['run_id']))
        self._raw(execution,'recovery',{'decision':decision,'generation_resumed':False,'new_measurement_requires_new_id':True})
        self._update(execution,'interrupted')
        with self.register.transaction():self.register.connection.execute("UPDATE job_state SET status='stopped' WHERE job_id=?",(row['job_id'],))

    def invalidate(self,measurement_ids,*,defect_evidence_id,reason,person):
        """Invalidate every affected old observation, including dependent reviews."""
        if not reason or not person:raise IntegrityError('Defect attribution required')
        affected=[]
        with self.register.transaction():
            for mid in measurement_ids:
                m=self.register.get(mid,MeasurementAttempt)
                affected.append(self.register._put(RevisionInvalidation(code='M6-INVALID-'+str(uuid4()),revision_id=m.id,reason=reason,person=person,defect_evidence_id=defect_evidence_id)))
            # Register.valid jointly checks T4 integration references, so no
            # dependent invalidated verdict can become a fallback for analysis.
        return affected

    def invalidate_instrument_defect(self,tool_id,*,defect_evidence_id,reason,person,measurement_keys=('functional_R','T4_integration')):
        affected=tuple(m.id for m in self.register.all(MeasurementAttempt)
                       if m.compatibility.tool_id==tool_id and m.measurement_key in measurement_keys and m.completion=='completed')
        return self.invalidate(affected,defect_evidence_id=defect_evidence_id,reason=reason,person=person)

    def correct_instrument(self,old_tool_id,new_tool_id,*,defect_evidence_id,reason,person):
        """Bind correction before outputs and invalidate all old instrument results.

        The candidate, phase, contract, suite, rubric and configuration remain
        byte-identical. No caller can silently authorize arbitrary tool drift.
        """
        if not reason or not person:raise IntegrityError('Attributed technical defect required')
        defect=self.register.get(defect_evidence_id,Artifact)
        if not protected_evaluation(defect):raise IntegrityError('Trusted defect evidence required')
        self.store.read(defect.id)
        old=self.register.get(old_tool_id,AssetVersion);new=self.register.get(new_tool_id,AssetVersion)
        current=instrument_manifest(self.sandbox.images)
        if old.asset_type!='tool' or new.asset_type!='tool' or old.manifest_hash==new.manifest_hash or new.manifest_hash!=digest(current):
            raise IntegrityError('Distinct old and actual corrected instrument versions required')
        executions=self.register.connection.execute('SELECT DISTINCT run_id,candidate_id FROM evaluation_execution WHERE tool_id=? AND candidate_id IS NOT NULL',(str(old_tool_id),)).fetchall()
        if not executions:raise IntegrityError('No actual affected sealed evaluations')
        if self.register.connection.execute("SELECT 1 FROM evaluation_execution WHERE status IN ('ready','running','recovery_required')").fetchone():raise IntegrityError('Finish or consciously stop affected measurements first')
        measurements=[m for m in self.register.all(MeasurementAttempt) if m.compatibility.tool_id==old_tool_id]
        reviews=[m for m in self.register.all(CriterionReviewRevision) if m.compatibility.tool_id==old_tool_id]
        public=contract_binding(self.sandbox.assets)
        original_bindings=[]
        for item in executions:
            run=self.register.get(UUID(item['run_id']),Run);conf=self.register.get(run.configuration_version_id,ConfigurationVersion)
            original_bindings.append({'run_id':str(run.id),'configuration_id':str(conf.id),'configuration_hash':conf.content_hash,
                'contract_id':str(conf.settings.contract_id),'contract_manifest_hash':self.register.get(conf.settings.contract_id,AssetVersion).manifest_hash})
        evidence=self.store.json({'reason':reason,'person':person,'defect_evidence_id':str(defect.id),
                'old_tool_id':str(old_tool_id),'old_manifest_hash':old.manifest_hash,'new_tool_id':str(new_tool_id),'new_instrument':current,
                'contract_interpretation':{'revision_manifest_sha256':public['manifest_sha256'],'original_bindings':original_bindings,
                    'scope':'Explicitly attributed evaluator correction; R/T, rubric, suites and original configurations unchanged'},
                'affected_revisions':[str(m.id) for m in measurements+reviews], 'scope':[dict(x) for x in executions],
                'scientific_contract_changed':False,'generation_restarted':False},artifact_type='evaluation_instrument_correction',producer='trusted_evaluator',access_scope='trusted_evaluator')
        with self.register.transaction():
            for m in measurements+reviews:
                self.register._put(RevisionInvalidation(code='M6-CORRECTION-INVALID-'+str(uuid4()),revision_id=m.id,reason=reason,person=person,defect_evidence_id=defect.id))
            for item in executions:
                run=self.register.get(UUID(item['run_id']),Run);conf=self.register.get(run.configuration_version_id,ConfigurationVersion)
                candidate=self.register.get(UUID(item['candidate_id']),CandidateSnapshot)
                if not self.register.compatible_evaluation_tool(run.id,old_tool_id) or self.register.state(run.id).candidate_id!=candidate.id:raise IntegrityError('Affected original binding no longer compatible')
                self.register.connection.execute('INSERT INTO evaluation_tool_correction VALUES(?,?,?,?,?,?,?,?,?)',
                    (str(run.id),str(candidate.id),candidate.tree_hash,str(old_tool_id),str(new_tool_id),str(conf.settings.contract_id),str(run.suite_id),str(conf.settings.rubric_id),str(evidence.id)))
        return evidence

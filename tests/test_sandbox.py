"""M4 component negatives; actual Docker proofs are separate, not simulated here."""
import hashlib
import io
import json
from pathlib import Path
import shutil
import tarfile
from uuid import uuid4

import pytest
import test_register as fixtures
from test_artifacts import instance
from research_env.artifacts import IntegrityError
from research_env.domain import Configuration, Job, MAIN_CELLS, StudyPhase
from research_env.sandbox_runtime import RuntimeImages, Sandbox, archive_tree
from research_env.snapshots import ProcessWriters, Snapshots, inventory

ROOT=Path(__file__).parents[1]
IMAGES=RuntimeImages(*('sha256:'+'1'*64 for _ in range(4)))


@pytest.fixture
def prepared(instance,tmp_path):
    register,settings,store,f,*_=instance
    phase=register.add(StudyPhase(code='M4-FREE',study_id=f['study'].id,purpose='free_test',provenance='Component fixture; no model inputs'))
    conf=register.add(Configuration(code='M4-CONF',phase_id=phase.id))
    version=register.version_configuration(conf.id,'M4-CELL',MAIN_CELLS['C-SQL-0'],f['settings'].model_copy(update={'holdout_suite_id':None,'reference_id':None}))
    run,job=register.start_other(phase.id,version.id,decision='Synthetic component test',technical_evidence_ids=(f['basic'].id,),idempotency_key=str(uuid4()))
    candidate=tmp_path/'candidate';shutil.copytree(ROOT/'assets/study/m2-v0.1/scaffold',candidate)
    vendor=tmp_path/'vendor';vendor.mkdir();(vendor/'autoload.php').write_text('<?php // COMPONENT ONLY')
    snapshots=Snapshots(store);dep=snapshots.dependencies(vendor,source={'synthetic':True},runtime={'not_executed':True})
    args=dict(run_id=run.id,scaffold_id=f['settings'].scaffold_id,dependency_ids=(dep.id,),internal_tests_hash=hashlib.sha256(b'<?php // own tests').hexdigest())
    sealed=snapshots.seal(candidate,writers=ProcessWriters(),**args)
    sandbox=Sandbox(store,object(),images=IMAGES,assets=ROOT)
    return sandbox,run,job,sealed,candidate,args


def test_fresh_execution_ids_identical_candidate_inputs(prepared):
    sandbox,run,job,sealed,*_=prepared
    a=sandbox.allocate(job.id,sealed.id)
    # Allocation alone is a live/recovery state; generation must not overwrite it.
    with pytest.raises(IntegrityError,match='Aktive'): sandbox.allocate(job.id,sealed.id)
    sandbox._update(a.id,status='interrupted')
    b=sandbox.allocate(job.id,sealed.id)
    assert a.id!=b.id and a.archive==b.archive
    assert not (b.path/'candidate'/'checkpoint').exists()
    assert a.labels['org.bachelorarbeit.sandbox.run']==str(run.id)
    assert a.labels['org.bachelorarbeit.sandbox.job']==str(job.id)


@pytest.mark.parametrize('name',['.git/leak','Vault/leak','storage/private-secret.key','foreign_run/leak','study_holdout/leak'])
def test_extra_sensitive_files_rejected_without_docker(prepared,name):
    sandbox,run,job,sealed,*_=prepared
    own=sandbox.settings.staging/'malicious';own.mkdir()
    sandbox.snapshots.restore(sealed.id,own/'candidate')
    path=own/'candidate'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('SECRET')
    with pytest.raises(IntegrityError): sandbox._validate_candidate(own/'candidate',inventory(own/'candidate'))


@pytest.mark.parametrize('target',['/var/run/docker.sock','/private/vault','../foreign','../../evaluation/study_holdout'])
def test_tar_links_are_rejected_before_any_daemon_copy(tmp_path,target):
    (tmp_path/'link').symlink_to(target)
    with pytest.raises(IntegrityError,match='Symlink'): archive_tree(tmp_path)


def test_tar_preserves_only_bytes_with_readonly_regular_files(tmp_path):
    (tmp_path/'foo').write_bytes(b'UNCHANGED')
    raw,entries=archive_tree(tmp_path)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        members=archive.getmembers()
        assert len(members)==1 and members[0].isreg() and members[0].mode&0o222==0
        assert archive.extractfile(members[0]).read()==b'UNCHANGED'
    assert entries[0]['sha256']==hashlib.sha256(b'UNCHANGED').hexdigest()


def test_asset_manipulation_and_inherited_files_are_rejected(prepared,tmp_path):
    sandbox,*_=prepared
    assets=tmp_path/'assets';shutil.copytree(ROOT/'evaluation/development/m2-v0.1',assets/'evaluation/development/m2-v0.1')
    sandbox.assets=assets
    (assets/'evaluation/development/m2-v0.1'/'private-oracle').write_text('HOLDOUT')
    with pytest.raises(IntegrityError,match='Asset'): sandbox._asset_tree('evaluation/development/m2-v0.1')


def test_k0_package_has_no_k1_or_private_marker(prepared):
    sandbox,run,*_=prepared
    data=sandbox.package(run.id)
    for marker in (b'K1-MAPPING',b'K1-SOURCE',b'PRIVATE-STUDY-HOLDOUT',b'PRIVATE-REFERENCES'):
        assert marker not in data
    with pytest.raises(Exception): sandbox.package(uuid4())


@pytest.mark.parametrize('image',['php:latest','research-env:0.1','sha256:bad','image;rm'])
def test_mutable_or_shell_images_rejected(image):
    with pytest.raises(IntegrityError): RuntimeImages(image,IMAGES.mysql,IMAGES.client,IMAGES.guard)


def test_protected_scaffold_change_rejected(prepared):
    sandbox,run,job,sealed,candidate,*_=prepared
    (candidate/'bootstrap/app.php').write_text('<?php // malicious bootstrap')
    with pytest.raises(IntegrityError,match='Geschütztes'): sandbox._validate_candidate(candidate,inventory(candidate))


def test_open_db_transaction_prevents_docker_and_unknown_profiles(prepared):
    sandbox,run,job,sealed,*_=prepared
    with sandbox.register.transaction():
        with pytest.raises(RuntimeError,match='DBtransaktion'): sandbox._external()
    with pytest.raises(IntegrityError,match='Jobvorlage'): sandbox.allocate(job.id,sealed.id,profile='custom-shell')


def test_internal_tests_bind_exact_artifact_and_hash(prepared):
    sandbox,run,job,sealed,*_=prepared
    artifact=sandbox.store.store(b'<?php // changed',run_id=run.id,access_scope='role')
    with pytest.raises(IntegrityError,match='interne Tests'): sandbox.allocate(job.id,sealed.id,profile='internal',internal_tests_id=artifact.id)


def test_recovery_decision_and_seal_refuse_unknown_writer(prepared):
    sandbox,run,job,sealed,*_=prepared
    a=sandbox.allocate(job.id,sealed.id)
    del sandbox.handles[a.id]
    with pytest.raises(IntegrityError,match='Recoveryentscheidung'): sandbox.recover(decision='resume')
    with pytest.raises(IntegrityError,match='Verwaister'): sandbox.writers(run.id).stop_all()
    with pytest.raises(IntegrityError,match='gestoppt'): sandbox.writers(run.id).assert_stopped()


def test_active_or_monitor_crash_window_blocks_other_job_of_same_phase(prepared):
    sandbox,run,job,sealed,*_=prepared
    sandbox.allocate(job.id,sealed.id)
    second=sandbox.register.add(Job(code='SECOND-M4-JOB',phase_id=run.phase_id,run_id=run.id,job_type='measurement',idempotency_key=str(uuid4()),suite_id=run.suite_id))
    with pytest.raises(IntegrityError,match='Generation der Phase'):
        sandbox.allocate(second.id,sealed.id)


def test_interrupted_http_transport_retains_only_received_complete_frames():
    from research_env.sandbox_runtime import decode_transport
    raw=(json.dumps({'event':'headers','value':{'status':200,'headers':{'content-type':'text/plain'}}})+'\n'+json.dumps({'event':'body','value':b'ACTUAL_RECEIVED_PREFIX'.hex()})+'\n').encode()+b'{"event":"body","value":"6162'
    response=decode_transport(raw)
    assert response['body']=='ACTUAL_RECEIVED_PREFIX' and response['received_body_bytes']==len(b'ACTUAL_RECEIVED_PREFIX')
    assert response['partial_evidence'] is True and response['transport_error']=='transport_interrupted'
    assert response['diagnostic_missing']


def test_split_http_utf8_bytes_are_reassembled_before_decoding():
    from research_env.sandbox_runtime import decode_transport
    events=[{'event':'headers','value':{'status':200,'headers':{}}},
            {'event':'body','value':b'B\xc3'.hex()},{'event':'body','value':b'\xa9la'.hex()},
            {'event':'result','value':{'status':200}}]
    response=decode_transport(('\n'.join(json.dumps(x) for x in events)+'\n').encode())
    assert response['body']=='Béla' and response['received_body_bytes']==5
    assert 'transport_error' not in response


def test_corrupted_completed_frame_is_an_integrity_failure():
    from research_env.sandbox_runtime import decode_transport
    with pytest.raises(IntegrityError,match='Beschädigter'):
        decode_transport(b'not-json\n')


def test_transport_body_byte_protection_and_untrusted_frames():
    from research_env.sandbox_client import RESPONSE_BYTES
    from research_env.sandbox_runtime import decode_transport
    with pytest.raises(IntegrityError,match='überschreitet'):
        decode_transport((json.dumps({'event':'body','value':(b'x'*(RESPONSE_BYTES+1)).hex()})+'\n').encode())
    with pytest.raises(IntegrityError,match='Unbekannter'):
        decode_transport(b'{"event":"candidate_command","value":"evil"}\n')


@pytest.mark.parametrize('path',['start','abort','immediate_abort','recover','request','observe','diagnose','upload_permissions','guard'])
def test_public_docker_paths_reject_open_transaction_before_external_io_or_state_change(prepared,path):
    # Real SQLite transaction plus an explicitly mocked SDK boundary. This
    # verifies the component gate, not native Docker behavior.
    sandbox,run,job,sealed,*_=prepared
    execution=sandbox.allocate(job.id,sealed.id)
    calls=[]
    class SDKBoundary:
        def __getattr__(self,name):
            calls.append(name)
            raise AssertionError('External SDK access before transaction refusal: '+name)
    execution.guard=SDKBoundary();execution.containers=[SDKBoundary()]
    sandbox.docker=SDKBoundary()
    invoke={
        'start':execution.start,
        'abort':lambda:execution.abort(reason='Component transaction negative'),
        'immediate_abort':lambda:execution.abort(reason='Component transaction negative',immediate=True),
        'recover':lambda:sandbox.recover(decision='stop_owned'),
        'request':lambda:execution.request(method='GET',path='/study-access'),
        'observe':execution.observe,
        'diagnose':execution.diagnose,
        'upload_permissions':lambda:execution.upload_permissions(writable=False),
        'guard':execution._guard_ok,
    }[path]
    with sandbox.register.transaction():
        with pytest.raises(RuntimeError,match='DBtransaktion'):
            invoke()
        assert sandbox._row(execution.id)['status']=='allocated'
        assert not execution.stopped.is_set() and execution.errors==[]
        assert calls==[]


@pytest.mark.parametrize('state',[
    {'Status':'exited','OOMKilled':True,'ExitCode':137},
    {'Status':'exited','OOMKilled':False,'ExitCode':153},
])
def test_native_failure_is_persisted_before_remove_and_gate_requires_explicit_recovery(prepared,state):
    from types import SimpleNamespace
    sandbox,run,job,sealed,*_=prepared
    execution=sandbox.allocate(job.id,sealed.id)
    removed=[]
    container=SimpleNamespace(id='own-component-container',name='own-candidate',status='exited',
        attrs={'State':state,'HostConfig':{},'Config':{'Labels':execution.labels}},
        reload=lambda:None,wait=lambda:{'StatusCode':state['ExitCode']},remove=lambda:removed.append(True))
    execution.containers=[container]
    execution.abort(reason='Component cleanup after already exited native failure')
    assert removed==[True] and sandbox._row(execution.id)['status']=='recovery_required'
    assert execution.errors and execution.errors[0]['exit_code']==state['ExitCode']
    retained=[json.loads(line) for line in (execution.path/'protection.raw').read_bytes().splitlines()]
    assert retained[0]['state']==state and retained[0]['fatal'] is True
    assert retained[0]['kind']==('memory_oom' if state['OOMKilled'] else 'unresolved_process_exit')
    assert sandbox.register.connection.execute('SELECT COUNT(*) FROM sandbox_observation WHERE execution_id=? AND kind=?',(execution.id,'protection')).fetchone()[0]==1
    second=sandbox.register.add(Job(code='AFTER-PROTECTION',phase_id=run.phase_id,run_id=run.id,job_type='measurement',idempotency_key=str(uuid4()),suite_id=run.suite_id))
    with pytest.raises(IntegrityError,match='Generation der Phase'):
        sandbox.allocate(second.id,sealed.id)
    # Explicit recovery finds no remaining mocked daemon resources. It preserves
    # the original cause and partial raw, and deliberately releases the gate.
    sandbox.docker=SimpleNamespace(containers=SimpleNamespace(list=lambda **kw:[]),
        volumes=SimpleNamespace(list=lambda **kw:[]),networks=SimpleNamespace(list=lambda **kw:[]))
    assert sandbox.recover(decision='stop_owned')==[execution.id]
    assert sandbox._row(execution.id)['status']=='interrupted'
    assert (execution.path/'protection.raw').read_bytes().splitlines()
    sandbox.allocate(second.id,sealed.id)


def test_actual_cpu_stats_create_durable_protection_event_without_stop_or_fatal_error(prepared):
    from types import SimpleNamespace
    sandbox,run,job,sealed,*_=prepared
    execution=sandbox.allocate(job.id,sealed.id)
    throttling={'periods':9,'throttled_periods':7,'throttled_time':1234567}
    container=SimpleNamespace(id='own-component-cpu',name='own-candidate',status='running',
        attrs={'State':{'Status':'running','OOMKilled':False,'ExitCode':0},'HostConfig':{'NanoCpus':1000000000}},
        reload=lambda:None,stats=lambda **kw:{'cpu_stats':{'throttling_data':throttling}})
    execution.containers=[container]
    execution.diagnose();execution.diagnose()
    assert execution.errors==[] and not execution.stopped.is_set()
    assert sandbox._row(execution.id)['status']=='allocated'
    events=[json.loads(line) for line in (execution.path/'protection.raw').read_bytes().splitlines()]
    assert len(events)==1 and events[0]['kind']=='cpu_quota_throttled' and events[0]['fatal'] is False
    assert events[0]['throttling_data']==throttling and events[0]['nano_cpus']==1000000000
    assert sandbox.register.connection.execute('SELECT COUNT(*) FROM sandbox_observation WHERE execution_id=? AND kind=?',(execution.id,'protection')).fetchone()[0]==1


def test_monitor_defers_open_owner_transaction_without_false_security_stop(prepared):
    from types import SimpleNamespace
    import threading
    sandbox,run,job,sealed,*_=prepared
    execution=sandbox.allocate(job.id,sealed.id)
    attempted=threading.Event();inspected=threading.Event();calls=[]
    # Explicit SDK mock; actual short owner SQLite transaction is used. Event
    # waits only bound this component test, never production job runtime.
    status='\n'.join(cap+':\t0000000000000000' for cap in ('CapEff','CapPrm','CapBnd','CapAmb','CapInh'))
    def reload():
        assert not sandbox.register.connection.in_transaction
        calls.append('reload');inspected.set()
    execution.guard=SimpleNamespace(status='running',reload=reload,
        exec_run=lambda command:SimpleNamespace(exit_code=0,output=(status if command[0]=='cat' else ':OUTPUT DROP\n-A OUTPUT -j REJECT').encode()))
    original=execution._guard_ok
    def guard():attempted.set();return original()
    execution._guard_ok=guard
    try:
        with sandbox.register.transaction():
            execution._monitor()
            assert attempted.wait(2)
            assert calls==[] and execution.errors==[]
        assert inspected.wait(2)
        assert execution.errors==[]
    finally:
        execution.stopped.set();execution.monitor.join(2)
    assert not execution.monitor.is_alive()


@pytest.mark.parametrize('code,own_stop',[(1,False),(137,True)])
def test_ordinary_assertion_exit_and_known_manual_kill_do_not_fake_resource_failure(prepared,code,own_stop):
    from types import SimpleNamespace
    sandbox,run,job,sealed,*_=prepared
    execution=sandbox.allocate(job.id,sealed.id)
    state={'Status':'exited','OOMKilled':False,'ExitCode':code}
    container=SimpleNamespace(id='own-normal-exit',name='own-candidate',status='exited',
        attrs={'State':state,'HostConfig':{},'Config':{'Labels':execution.labels}},
        reload=lambda:None,wait=lambda:{'StatusCode':code},remove=lambda:None)
    execution.containers=[container]
    if own_stop:execution.requested_stops.add(container.id)
    execution.abort(reason='Ordinary assertion result or explicit own manual stop')
    assert execution.errors==[] and sandbox._row(execution.id)['status']=='interrupted'
    if own_stop:
        assert execution.protection_events=={}
    else:
        assert next(iter(execution.protection_events.values()))['kind']=='process_exit'
        assert next(iter(execution.protection_events.values()))['fatal'] is False
    sandbox.allocate(job.id,sealed.id)


def test_explicit_php_exit153_pauses_with_unknown_cause_without_fake_resource_failure(prepared):
    from types import SimpleNamespace
    sandbox,run,job,sealed,*_=prepared
    execution=sandbox.allocate(job.id,sealed.id)
    # Explicit input is ordinary exit(), not an allocation/file/CPU violation.
    # SDK state is mocked; this test does not claim native PHP execution.
    php_fixture=b'<?php exit(153);'
    (execution.path/'explicit-exit153.php').write_bytes(php_fixture)
    state={'Status':'exited','OOMKilled':False,'ExitCode':153}
    container=SimpleNamespace(id='own-explicit-exit153',name='own-candidate',status='exited',
        attrs={'State':state,'HostConfig':{},'Config':{'Labels':execution.labels}},
        reload=lambda:None,wait=lambda:{'StatusCode':153},remove=lambda:None)
    execution.containers=[container]
    execution._monitor();execution.monitor.join(2)
    assert not execution.monitor.is_alive()
    assert execution.errors[0]['kind']=='unresolved_process_exit'
    assert execution.errors[0]['state']==state and execution.errors[0]['exit_code']==153
    assert 'cause unknown' in execution.errors[0]['reason']
    assert execution.errors[1]['kind']=='safety_stop'
    assert execution.errors[1]['reason']=='Ungeklärter Prozessausgang; Ursache nicht identifiziert'
    assert not any(e['kind'] in ('memory_oom','file_size_signal_exit','log_limit') for e in execution.errors)
    execution.abort(reason='Explicit ordinary exit153 fixture; cause unknown')
    assert sandbox._row(execution.id)['status']=='recovery_required'
    retained=[json.loads(line) for line in (execution.path/'protection.raw').read_bytes().splitlines()]
    assert retained[0]['kind']=='unresolved_process_exit' and retained[0]['state']==state
    assert (execution.path/'explicit-exit153.php').read_bytes()==php_fixture
    with pytest.raises(IntegrityError,match='Generation der Phase'):
        sandbox.allocate(job.id,sealed.id)


@pytest.mark.parametrize('outcome',['ready','database_exited','stopped'])
def test_candidate_waits_for_database_before_execution(prepared,monkeypatch,outcome):
    """Exercise start(), including the real dispatch ordering, with a delayed DB."""
    from types import SimpleNamespace
    sandbox,run,job,sealed,*_=prepared
    execution=sandbox.allocate(job.id,sealed.id,profile='boot')
    events=[]
    class Container:
        def __init__(self,name):
            self.id=self.name=name;self.status='created';self.attrs={'State':{}}
            self.calls=0
        def start(self):self.status='running';events.append(self.name+' started')
        def reload(self):pass
        def exec_run(self,command,**kwargs):
            if self.name!='mysql':return SimpleNamespace(exit_code=0,output=b'')
            self.calls+=1;events.append('database probe')
            assert '--protocol=TCP' in command and '--database=study' in command
            assert 'MYSQL_PWD' in kwargs['environment']
            assert kwargs['environment']['MYSQL_PWD'] not in ' '.join(command)
            if self.calls==1:
                if outcome=='database_exited':self.status='exited'
                if outcome=='stopped':execution.stopped.set()
                return SimpleNamespace(exit_code=1,output=b'not ready')
            events.append('database ready')
            return SimpleNamespace(exit_code=0,output=b'2\n')
    guard=Container('guard')
    sandbox.docker=SimpleNamespace(networks=SimpleNamespace(create=lambda *a,**kw:SimpleNamespace(id='network',name='network')),
        containers=SimpleNamespace(create=lambda *a,**kw:guard))
    monkeypatch.setattr(execution,'_volume',lambda name,**kw:SimpleNamespace(name=name))
    monkeypatch.setattr(execution,'_fill',lambda *a:None)
    monkeypatch.setattr(execution,'_stream',lambda *a:None)
    monkeypatch.setattr(execution,'_guard_ok',lambda:None)
    monkeypatch.setattr(execution,'_monitor',lambda:None)
    monkeypatch.setattr(execution,'diagnose',lambda:{})
    monkeypatch.setattr(execution,'abort',lambda **kw:events.append('aborted'))
    def create(image,command,name,environment,**kwargs):
        c=Container(name);execution.containers.append(c);return c
    monkeypatch.setattr(execution,'_create_env',create)
    if outcome=='ready':
        execution.start()
        assert 'database ready' in events,events
        assert events.index('database ready')<events.index('candidate started')
        assert sandbox.register.connection.execute("SELECT count(*) FROM sandbox_observation WHERE execution_id=? AND kind='mysql_ready'",(execution.id,)).fetchone()[0]==1
    else:
        with pytest.raises((RuntimeError,IntegrityError)):
            execution.start()
        assert 'candidate started' not in events

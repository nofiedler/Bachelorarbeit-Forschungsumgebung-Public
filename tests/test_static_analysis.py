"""Predetermined scope/protocol/revision counterexamples, no study results."""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID,uuid4

import pytest
from research_env.artifacts import IntegrityError
from research_env.domain import *
from research_env.static_analysis import (StaticAnalyzer, instrument_manifest,
    parse_diagnostics, parse_tokens, select_files)
from research_env.sandbox_runtime import RuntimeImages
from test_artifacts import instance,secured
from test_evaluation import component_attempt
import test_register as fixtures

ROOT=Path(__file__).resolve().parents[1]
NONCE='a'*64


def stream(files, messages=()):
    report={'totals':{'errors':0,'file_errors':len(messages)},'files':{},'errors':[]}
    if messages:report['files']['/opt/study/'+files[0]]={'errors':len(messages),'messages':list(messages)}
    stdout=(''.join('/opt/study/'+p+'\n' for p in files)+json.dumps(report)).encode()
    code=int(bool(messages));stderr=f'M6_STATIC_BEGIN:{NONCE}\nM6_STATIC_END:{NONCE}:{code}\n'.encode()
    return stdout,stderr,code,files


def test_union_complete_manifest_added_helper_route_and_exclusions():
    original={'routes/study.php':{'sha256':'a'},'app/Study/Unchanged.php':{'sha256':'a'}}
    names=['routes/study.php','app/Study/Unchanged.php','app/Study/New.php','app/Http/Controllers/Study/C.php','vendor/foo.php','resources/views/study/x.blade.php','app/Study/tests/t.php','app/Study/cache/c.php','app/Study/generated/G.php','tests/internal.php','evaluation/suite.php','config/app.php']
    entries=[{'path':n,'sha256':'a' if 'Unchanged' in n else 'b'} for n in names]
    report=select_files(original,entries,diff_paths=['routes/study.php'])
    assert report['included']==['app/Http/Controllers/Study/C.php','app/Study/New.php','routes/study.php']
    assert set(report['excluded'])==set(names)-set(report['included'])
    assert report['complete_manifest']==entries


@pytest.mark.parametrize('name',['../foo.php','/foo.php','app//Study/x.php','app/Study/../x.php','app\\Study\\x.php'])
def test_unsafe_scope_rejected(name):
    with pytest.raises(IntegrityError):select_files({},[{'path':name,'sha256':'a'}])


def test_deleted_unchanged_diff_not_counted_duplicate_manifest_rejected():
    report=select_files({'routes/study.php':{'sha256':'a'}},[],['routes/study.php'])
    assert report['deleted']==['routes/study.php'] and not report['included']
    with pytest.raises(IntegrityError):select_files({},[{'path':'a.php','sha256':'a'}]*2)


def test_diagnosis_exit1_unchanged_duplicates_identifier_and_full_object():
    message={'message':'Original full\ntext','line':3,'identifier':'return.type','ignorable':True,'tip':'keep extra field'}
    args=stream(['app/Study/A.php','routes/study.php'],[message,message])
    report=parse_diagnostics(*args)
    assert report['D']==2 and report['D_by_file']=={'app/Study/A.php':2,'routes/study.php':0}
    assert report['original_report']['files']['/opt/study/app/Study/A.php']['messages']==[message,message]


def test_clean_analysis_d0_and_json_empty_files():
    report=parse_diagnostics(*stream(['routes/study.php']))
    assert report['D']==0 and report['D_by_file']=={'routes/study.php':0}


@pytest.mark.parametrize('change',['no_end','wrong_nonce','wrong_exit','fake_json_only','extra_output','bad_total','global_error','missing_identifier','foreign_file','negative_line'])
def test_incomplete_or_manipulated_diagnostics_missing(change):
    message={'message':'diagnostic','line':3,'identifier':'return.type','ignorable':True}
    out,err,code,files=stream(['routes/study.php'],[message]);report=json.loads(out.split(b'\n',1)[1])
    if change=='no_end':err=err.splitlines(keepends=True)[0]
    elif change=='wrong_nonce':err=err.replace(b'M6_STATIC_END:a',b'M6_STATIC_END:b')
    elif change=='wrong_exit':code=0
    elif change=='fake_json_only':out=json.dumps(report).encode();err=b''
    elif change=='extra_output':out=b'fake boot message'+out
    elif change=='bad_total':report['totals']['file_errors']=0
    elif change=='global_error':report['errors']=['incomplete analysis']
    elif change=='missing_identifier':report['files']['/opt/study/routes/study.php']['messages'][0].pop('identifier')
    elif change=='foreign_file':report['files']['/opt/study/vendor/foreign.php']=report['files'].pop('/opt/study/routes/study.php')
    elif change=='negative_line':report['files']['/opt/study/routes/study.php']['messages'][0]['line']=-1
    if change in ('bad_total','global_error','missing_identifier','foreign_file','negative_line'):out=b'/opt/study/routes/study.php\n'+json.dumps(report).encode()
    with pytest.raises(IntegrityError):parse_diagnostics(out,err,code,files)


@pytest.mark.parametrize('exit_code',[2,37,137,153,255])
def test_abnormal_exit_is_not_d0(exit_code):
    out,err,_,files=stream(['routes/study.php'])
    with pytest.raises(IntegrityError):parse_diagnostics(out,err,exit_code,files)


def test_token_manifest_hash_lines_and_zero():
    selection={'included':['routes/study.php'],'selected_manifest':[{'path':'routes/study.php','sha256':'a'*64}]}
    body={'version':'M6-static-token-v1','files':{'routes/study.php':{'L':0,'lines':[],'sha256':'a'*64}}}
    assert parse_tokens(json.dumps(body).encode(),0,selection)['L']==0
    body['files']['routes/study.php']['sha256']='b'*64
    with pytest.raises(IntegrityError):parse_tokens(json.dumps(body).encode(),0,selection)


def component(instance,tmp_path):
    ev,_,run,seal,raw,_=component_attempt(instance,tmp_path)
    static=StaticAnalyzer(ev.sandbox);execution=str(uuid4())
    job=ev.register.add(Job(code='STATIC-UNIT-JOB-'+execution,phase_id=run.phase_id,run_id=run.id,job_type='measurement',idempotency_key=execution,suite_id=run.suite_id))
    body={'candidate_hash':seal.tree_hash,'instrument':instrument_manifest(ev.sandbox.images),'run_configuration_hash':ev.register.get(run.configuration_version_id,ConfigurationVersion).content_hash,'selection':{'included':[],'excluded':{},'selected_manifest':[],'complete_manifest':[]}}
    with ev.register.transaction():ev.register.connection.execute('INSERT INTO static_execution VALUES(?,?,?,?,?,?,?,?,?)',(execution,str(job.id),str(run.id),str(seal.id),str(seal.tree_hash and instance[3]['tool'].id),'ready',None,canonical(body),fixtures.NOW.isoformat()))
    static._raw(execution,'inputs',body)
    return static,execution,run,seal


def test_static_revisions_last_valid_not_latest_draft_and_all_invalidated(instance,tmp_path):
    analyzer,execution,run,seal=component(instance,tmp_path)
    old=analyzer._measurement(execution,completed=True,reason='Synthetic completed protocol',exit_code=0,report={'D':0,'L':2,'S':'0'})
    analyzer._measurement(execution,completed=False,reason='Uncompleted newer attempt')
    assert analyzer.register.select_revision(run.id,'measurement','static_DLS',old.compatibility).id==old.id
    evidence=analyzer._raw(execution,'defect',{'detected':'synthetic controlled defect'})
    analyzer.register.add(RevisionInvalidation(code='STATIC-INVALID-'+str(uuid4()),revision_id=old.id,reason='Synthetic defect',person='technical test',defect_evidence_id=evidence.id))
    assert analyzer.register.select_revision(run.id,'measurement','static_DLS',old.compatibility) is None
    new=analyzer._measurement(execution,completed=True,reason='Corrected same frozen scale',exit_code=0,report={'D':0,'L':2,'S':'0'})
    assert analyzer.register.select_revision(run.id,'measurement','static_DLS',old.compatibility).id==new.id
    assert new.compatibility.candidate_hash==seal.tree_hash


def test_static_draft_revision_stales_external_backup(instance,tmp_path):
    analyzer,execution,run,_=component(instance,tmp_path)
    with analyzer.register.transaction():
        analyzer.register.connection.execute("UPDATE static_execution SET status='interrupted' WHERE id=?",(execution,))
        analyzer.register.connection.execute("UPDATE job_state SET status='stopped' WHERE job_id=?",(analyzer.row(execution)['job_id'],))
    secured(instance,tmp_path/'before-static')
    assert analyzer.register.backup_status(instance[4].id)=='current'
    analyzer._measurement(execution,completed=False,reason='New substantive static attempt')
    assert analyzer.register.backup_status(instance[4].id)=='stale'


def test_static_raws_are_protected_even_development(instance,tmp_path):
    analyzer,execution,run,_=component(instance,tmp_path)
    raw=analyzer._raw(execution,'diagnosis',{'unmodified':'external static evidence'})
    assert protected_evaluation(raw) and raw.producer=='trusted_evaluator'


def test_immutable_scale_does_not_include_source_implementation():
    from research_env.static_analysis import measure_binding
    assert 'sources' not in measure_binding()
    assert instrument_manifest(RuntimeImages(**json.loads((ROOT/'src/research_env/pipeline_runtime.lock.json').read_text())))['measure']==measure_binding()


def test_original_integrity_failure_cannot_publish_completed_revision(instance,tmp_path,monkeypatch):
    """Passive final inventory read failure, without modifying any original."""
    import research_env.static_analysis as module
    analyzer,execution,run,seal=component(instance,tmp_path)
    selection={'included':['routes/study.php'],'excluded':{},'selected_manifest':[{'path':'routes/study.php','sha256':'a'*64}],'complete_manifest':[]}
    row=analyzer.row(execution);body=json.loads(row['body']);body['selection']=selection
    with analyzer.register.transaction():analyzer.register.connection.execute('UPDATE static_execution SET body=? WHERE id=?',(canonical(body),execution))
    def native(attempt,profile):
        if profile=='lines':return 0,{'stdout':json.dumps({'version':'M6-static-token-v1','files':{'routes/study.php':{'L':1,'lines':[2],'sha256':'a'*64}}}).encode(),'stderr':b''}
        out,err,code,_=stream(['routes/study.php']);return code,{'stdout':out,'stderr':err}
    monkeypatch.setattr(analyzer,'_native',native)
    original=analyzer.store.settings.artifacts/'sealed'/str(seal.id)
    actual_inventory=module.inventory
    def failed_read(path):
        if Path(path)==original:raise IntegrityError('Controlled final original inventory read failure')
        return actual_inventory(path)
    monkeypatch.setattr(module,'inventory',failed_read)
    report=analyzer.run(execution)
    measurement=analyzer.register.get(UUID(report['measurement_id']),MeasurementAttempt)
    assert report['analysis_complete'] is False and report['D'] is None and report['S'] is None
    assert measurement.completion=='draft' and not analyzer.register.valid(measurement.id)
    assert analyzer.register.select_revision(run.id,'measurement','static_DLS',measurement.compatibility) is None
    assert analyzer.row(execution)['status']=='interrupted'
    assert actual_inventory(original) # preserved original bytes


@pytest.mark.parametrize('stop_at',['between_profiles','after_allocation'])
def test_separate_control_stop_prevents_next_native_dispatch(instance,tmp_path,monkeypatch,stop_at):
    """Persisted Control decision; Worker has a distinct local Event."""
    analyzer,execution,run,seal=component(instance,tmp_path)
    control=StaticAnalyzer(analyzer.sandbox)
    allocations=[];starts=[]
    monkeypatch.setattr(analyzer.sandbox,'stop_recorded',lambda *a,**kw:[],raising=False)
    monkeypatch.setattr(analyzer.sandbox,'_row',lambda *a:{'status':'allocated'},raising=False)
    def allocate(*args,**kwargs):
        allocations.append(kwargs['profile'])
        if stop_at=='after_allocation':control.abort(execution,reason='Independent persisted Control stop during allocation')
        def start():
            starts.append(kwargs['profile'])
            raise IntegrityError('Unexpected native dispatch after Control stop')
        return SimpleNamespace(id='PASSIVE-OWN-HANDLE',start=start,abort=lambda **kw:None)
    monkeypatch.setattr(analyzer.sandbox,'allocate',allocate,raising=False)
    row=analyzer.row(execution);body=json.loads(row['body'])
    body['selection']={'included':['routes/study.php'],'excluded':{},'selected_manifest':[{'path':'routes/study.php','sha256':'a'*64}],'complete_manifest':[]}
    with analyzer.register.transaction():analyzer.register.connection.execute('UPDATE static_execution SET body=? WHERE id=?',(canonical(body),execution))
    def native(attempt,profile):
        if profile=='lines':
            if stop_at=='between_profiles':control.abort(execution,reason='Independent persisted Control stop after lines')
            return 0,{'stdout':json.dumps({'version':'M6-static-token-v1','files':{'routes/study.php':{'L':1,'lines':[2],'sha256':'a'*64}}}).encode(),'stderr':b''}
        return StaticAnalyzer._native(analyzer,attempt,profile)
    monkeypatch.setattr(analyzer,'_native',native)
    report=analyzer.run(execution)
    assert not analyzer.stop.is_set() and control.stop.is_set()
    assert allocations==([] if stop_at=='between_profiles' else ['static'])
    assert starts==[]
    assert not report['analysis_complete'] and report['D'] is None and report['S'] is None
    measurement=analyzer.register.get(UUID(report['measurement_id']),MeasurementAttempt)
    assert measurement.completion=='draft' and not analyzer.register.valid(measurement.id)

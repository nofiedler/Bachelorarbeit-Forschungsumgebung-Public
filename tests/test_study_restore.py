"""Real archive/database roundtrips; synthetic values, no provider calls."""
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import zipfile
from uuid import UUID

import pytest
from research_env import backup_ui, study_restore
from research_env.artifacts import ArtifactStore, IntegrityError
from research_env.backup import copy_checkpoints
from research_env.domain import Freeze, ModelCall, PlannedRun, Run, Study
from research_env.register import Register
from test_artifacts import instance, secured
from test_preparation import client, post
import test_register as fixture


def archive(instance):
    r,settings,store,setup,freeze,backups,worker=instance
    job=backup_ui.enqueue(r,freeze.id,'restore-test')
    worker.tick(); assert backup_ui.tick(r)
    return backup_ui.verified_download(r,job),job


def test_web_backup_operations_never_write_readonly_artifact_volume(instance, monkeypatch):
    path,job=archive(instance)
    r=instance[0];c=client(r)
    def readonly(*args,**kwargs): raise OSError(30,'Read-only file system')
    monkeypatch.setattr(ArtifactStore,'put_object',readonly)
    response=post(c,{'person':'TECHNICAL-FIXTURE: reviewer','external_medium':'TECHNICAL-FIXTURE: separate directory',
        'confirm':'true','idempotency_key':'readonly-confirm'},f'/backups/{job}/confirm')
    assert response.status_code==303,response.text
    assert r.backup_status(instance[4].id)=='current'
    assert instance[5].history(job)[-1]['operation']=='external_storage_self_report'
    # Failed backup recovery also uses only control storage.
    with r.transaction():r.connection.execute("UPDATE backup_request SET status='failed',error='synthetic' WHERE job_id=?",(str(job),))
    assert post(c,{'decision':'TECHNICAL-FIXTURE: retry'},f'/backups/{job}/recover').status_code==303


def test_checkpoint_reader_does_not_mutate_live_wal_or_shm(instance, tmp_path):
    _,settings,*_=instance
    source=settings.checkpoints/'graph.sqlite3'
    db=sqlite3.connect(source);db.execute('PRAGMA journal_mode=WAL');db.execute('CREATE TABLE test (value TEXT)');db.execute("INSERT INTO test VALUES ('latest committed WAL value')");db.commit()
    # sqlite read-only open changes SHM lock/readmark bytes on real SQLite.
    before={p.name:p.read_bytes() for p in settings.checkpoints.iterdir()}
    target=tmp_path/'captured';target.mkdir()
    copy_checkpoints(settings,target)
    assert before=={p.name:p.read_bytes() for p in settings.checkpoints.iterdir()}
    with sqlite3.connect(target/'graph.sqlite3') as captured:
        assert captured.execute('SELECT value FROM test').fetchone()[0]=='latest committed WAL value'
    db.close()


def test_backup_import_isolated_frozen_copy_keeps_ids_status_bytes_and_next(instance,tmp_path):
    r,settings,store,setup,freeze,backups,worker=instance
    secured(instance,tmp_path/'synthetic-target')
    run,_=fixture.start(r,freeze,setup)
    proof=store.store(b'SYNTHETIC ORIGINAL RESULT',run_id=run.id,artifact_type='raw')
    before={x[0]:x[1] for x in r.connection.execute('SELECT id,payload FROM register_record')}
    path,job=archive(instance)
    c=client(r)
    response=c.post('/studies/import',data={'csrf':c.cookies['research_csrf']},files={'archive':('backup.zip',path.read_bytes(),'application/zip')},headers={'Origin':'http://testserver'},follow_redirects=False)
    assert response.status_code==303,response.text
    identity=response.headers['location'].split('/')[-1]
    assert study_restore.tick(r)
    row=r.connection.execute('SELECT * FROM study_restore WHERE id=?',(identity,)).fetchone()
    assert row['status']=='ready',row['error']
    assert len(study_restore.rows(r))==1
    with closing(Register(study_restore.copy_settings(settings,identity))) as copied:
        assert copied.get(freeze.id,Freeze)==freeze
        assert copied.all(PlannedRun)==r.all(PlannedRun)
        assert copied.all(Run)==r.all(Run)
        assert copied.next_id(freeze.id)==r.next_id(freeze.id)
        assert copied.state(run.id)==r.state(run.id)
        assert ArtifactStore(copied.settings,copied).read(proof.id)==b'SYNTHETIC ORIGINAL RESULT'
        assert copied.all(ModelCall)==r.all(ModelCall)
        assert copied.connection.execute("SELECT count(*) FROM job_state WHERE status IN ('ready','running')").fetchone()[0]==0
        for key,payload in before.items():
            assert copied.connection.execute('SELECT payload FROM register_record WHERE id=?',(key,)).fetchone()[0]==payload
    assert all(r.connection.execute('SELECT payload FROM register_record WHERE id=?',(key,)).fetchone()[0]==payload for key,payload in before.items())
    assert 'importierte Sicherung' in c.get('/studies').text
    prefix='/restored/'+identity
    page=c.get(prefix+'/studies/'+row['study_id'])
    assert page.status_code==200,page.text
    assert 'Fixierte Versuchsreihe' in page.text and 'Als Nächstes: Lauf 2' in page.text
    assert 'Neue Entwurfsphase' not in page.text
    assert 'Wiederholungsblock 1' in page.text
    assert f'action="{prefix}/backups"' in page.text
    assert f'src="{prefix}/static/run-progress.js"' in page.text
    assert c.get(prefix+'/static/run-progress.js').status_code==200
    next_config=c.get(prefix+'/configurations/'+str(freeze.configuration_version_ids['C-BF-0']))
    assert next_config.status_code==200
    # A new backup command lands only in the restored register; redirect remains scoped.
    result=post(c,{'freeze_id':str(freeze.id),'idempotency_key':'copy-only'},prefix+'/backups')
    assert result.headers['location'].startswith(prefix+'/backups/')
    assert not r.connection.execute("SELECT 1 FROM job_state WHERE idempotency_key='copy-only'").fetchone()
    assert study_restore.enqueue(r,path)==identity  # duplicate upload is idempotent


@pytest.mark.parametrize('name',['../escape','/outside','objects/link'])
def test_zip_path_and_symlink_rejected(tmp_path,name):
    path=tmp_path/'bad.zip'
    with zipfile.ZipFile(path,'w') as z:
        entry=zipfile.ZipInfo(name)
        if name=='objects/link':entry.external_attr=0o120777<<16
        z.writestr(entry,b'not a backup')
    with pytest.raises(IntegrityError):study_restore.unpack(path,tmp_path/'unpacked')
    assert not (tmp_path/'escape').exists()


def test_unknown_database_trigger_rejected(instance,tmp_path):
    path,_=archive(instance)
    root=tmp_path/'unpacked';study_restore.unpack(path,root)
    db=root/'control.sqlite3';db.chmod(0o600)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TRIGGER surprise AFTER INSERT ON runtime_metadata BEGIN DELETE FROM runtime_metadata; END")
    with pytest.raises(IntegrityError,match='Datenbankschema'):study_restore.validate_database(db)


def test_parent_backup_never_reads_independent_copy_checkpoints(instance,tmp_path):
    _,settings,*_=instance
    child=settings.checkpoints/'restored'/'other-copy';child.mkdir(parents=True)
    (child/'busy-or-unsafe').symlink_to('/missing')
    (settings.checkpoints/'own.json').write_text('own checkpoint')
    target=tmp_path/'copy';target.mkdir()
    copy_checkpoints(settings,target)
    assert (target/'own.json').read_text()=='own checkpoint'
    assert not (target/'restored').exists()


def test_aggregate_database_can_exceed_single_object_limit(instance, tmp_path, monkeypatch):
    path, _ = archive(instance)
    with zipfile.ZipFile(path) as z:
        database = z.read('control.sqlite3')
        limit = max(x.file_size for x in z.infolist() if x.filename != 'control.sqlite3')
        assert len(database) > limit
    monkeypatch.setattr(study_restore, 'MAX_FILE', limit)
    target = tmp_path / 'valid-large-database'
    study_restore.unpack(path, target)
    study_restore.validate_database(target / 'control.sqlite3')
    assert (target / 'control.sqlite3').read_bytes() == database


@pytest.mark.parametrize('name,size', [
    ('control.sqlite3', 21), ('objects/large', 11), ('Control.sqlite3', 11),
    ('checkpoints/control.sqlite3', 11),
])
def test_database_exception_retains_file_limits(tmp_path, monkeypatch, name, size):
    monkeypatch.setattr(study_restore, 'MAX_FILE', 10)
    monkeypatch.setattr(study_restore, 'MAX_DATABASE_FILE', 20, raising=False)
    path = tmp_path / 'oversized.zip'
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr(name, b'x' * size)
    destination = tmp_path / 'unpacked'
    with pytest.raises(IntegrityError, match='ZIP-Eintrag zu groß'):
        study_restore.unpack(path, destination)
    assert not destination.exists()


def test_duplicate_database_name_is_still_rejected(tmp_path):
    path = tmp_path / 'duplicate.zip'
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('control.sqlite3', b'one')
        z.writestr('CONTROL.sqlite3', b'two')
    with pytest.raises(IntegrityError, match='Unsicherer oder doppelter'):
        study_restore.unpack(path, tmp_path / 'unpacked')

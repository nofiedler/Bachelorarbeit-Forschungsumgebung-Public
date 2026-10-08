"""Progress reflects actual import work and never changes saved research data."""
from datetime import datetime, timedelta, timezone
import hashlib
from uuid import uuid4

from research_env import exchange, import_progress
from research_env.snapshots import ProcessWriters
from test_artifacts import instance, workspace
from test_exchange import exported
from test_preparation import client


def test_restore_progress_counts_current_copy_only_without_writes(instance, tmp_path, monkeypatch):
    r, settings, *_ = instance
    run, tree, snapshots, args = workspace(instance, tmp_path)
    snapshots.seal(tree, writers=ProcessWriters(), **args)
    count = r.connection.execute('SELECT count(*) FROM seal_copy_attempt').fetchone()[0]
    # A pending file is not complete. Historical source attempts must not count again.
    with r.transaction():
        r.connection.execute("UPDATE seal_copy_attempt SET status='allocated' WHERE id=(SELECT id FROM seal_copy_attempt LIMIT 1)")
    monkeypatch.setattr(import_progress, 'copy_settings', lambda *args: settings)
    row = {'id': str(uuid4()), 'status':'restoring',
           'created_at':(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()}
    before = hashlib.sha256(settings.database.read_bytes()).hexdigest()
    measured = import_progress.RestoreProgress(settings).get(row)
    assert measured['step'] == 3
    assert measured['done'] == count - 1 and measured['total'] == count
    assert measured['percent'] < 100
    assert hashlib.sha256(settings.database.read_bytes()).hexdigest() == before
    # Same journal copied out of an older backup does not mean the new import is finished.
    row['created_at'] = (datetime.now(timezone.utc)+timedelta(minutes=1)).isoformat()
    historical = import_progress.RestoreProgress(settings).get(row)
    assert historical['step'] == 2 and historical['percent'] is None
    with r.transaction():r.connection.execute("UPDATE seal_copy_attempt SET status='completed'")
    row['created_at'] = (datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
    complete_files = import_progress.RestoreProgress(settings).get(row)
    assert complete_files['step'] == 4 and complete_files['percent'] is None
    assert 'erst nach der Abschlussprüfung' in complete_files['eta']


def test_restore_progress_missing_target_does_not_create_it(instance):
    r,settings,*_=instance
    row={'id':str(uuid4()),'status':'restoring','created_at':datetime.now(timezone.utc).isoformat()}
    result=import_progress.RestoreProgress(settings).get(row)
    assert result['percent'] is None and result['step']==1
    assert not (settings.control/'restored').exists()


def test_package_progress_covers_checks_and_preserves_exact_archive(exported, tmp_path):
    env, _, source, manifest=exported
    events=[]
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    result=exchange.import_archive(env[0].settings,source,progress=lambda *event:events.append(event))
    assert result==manifest
    stages={event[0] for event in events}
    assert {'Dateien und Prüfsummen prüfen','Registerdatensätze prüfen',
            'Herkunft und Referenzen prüfen','Matrix und Analyseergebnisse abgleichen','Studienpaket speichern'}<=stages
    assert all(0<=e[1]<=e[2] for e in events if len(e)>2)
    target=env[0].settings.staging/'imports'/(manifest['package_id']+'.zip')
    assert hashlib.sha256(source.read_bytes()).hexdigest()==hashlib.sha256(target.read_bytes()).hexdigest()==before


def test_package_ui_progress_response_and_legacy_fallback(exported):
    env,_,path,manifest=exported
    c=client(env[0]);token=str(uuid4())
    response=c.post('/packages/import',data={'csrf':c.cookies['research_csrf']},
                    files={'archive':('study.zip',path.read_bytes(),'application/zip')},
                    headers={'origin':'http://testserver','X-Import-Progress':token},follow_redirects=False)
    assert response.status_code==204,response.text
    assert response.headers['hx-redirect']=='/packages/imports/'+manifest['package_id']
    status=c.get('/packages/import/progress/'+token).json()
    assert status['percent']==100 and 'geprüft' in status['stage']
    assert 'data-server-progress' in c.get('/packages').text
    assert 'data-import-form' in c.get('/studies').text
    assert c.get('/static/import-progress.js').status_code==200

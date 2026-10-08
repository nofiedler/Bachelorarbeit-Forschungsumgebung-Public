"""Synthetic browser backup roundtrip; never a physical external-disk claim."""
import hashlib
import io
import zipfile

import pytest

from research_env import backup_ui
from research_env.backup import BackupWorker
from research_env.domain import BackupReceipt, ModelCall
from test_artifacts import instance
from test_preparation import client, post
import test_register as fixtures


def test_browser_backup_download_confirmation_and_stale_gate(instance):
    r,settings,store,fixture,freeze,backups,worker=instance
    c=client(r);calls=len(r.all(ModelCall));revision=r.revision(freeze.phase_id)
    response=post(c,{'freeze_id':str(freeze.id),'idempotency_key':'browser-backup'},'/backups')
    assert response.status_code==303
    location=response.headers['location'];job_id=location.rsplit('/',1)[-1]
    assert 'Sicherung wird erstellt' in c.get(location).text
    assert c.get(location+'/download').status_code==409
    worker.tick();assert backup_ui.tick(r)
    page=c.get(location);assert page.status_code==200 and 'Sicherung als ZIP herunterladen' in page.text
    response=c.get(location+'/download');assert response.status_code==200
    view=backup_ui.view(r,job_id)
    assert hashlib.sha256(response.content).hexdigest()==view['download']['sha256']
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert {'manifest.json','control.sqlite3'} <= set(archive.namelist())
        assert not any('openrouter.key' in name for name in archive.namelist())
    data={'person':'TECHNICAL-FIXTURE: reviewer','external_medium':'TECHNICAL-FIXTURE: synthetic separate folder; not external hardware',
        'confirm':'true','idempotency_key':'browser-confirm'}
    confirmed=post(c,data,location+'/confirm');assert confirmed.status_code==303,confirmed.text
    assert post(c,data,location+'/confirm').status_code==303
    assert len(r.all(BackupReceipt))==1 and r.all(BackupReceipt)[0].self_report
    assert r.backup_status(freeze.id)=='current' and r.revision(freeze.phase_id)==revision
    assert len(r.all(ModelCall))==calls
    before=r.connection.total_changes;c.get(location);c.get(location+'/download');assert r.connection.total_changes==before
    fixtures.start(r,freeze,fixture)
    assert r.backup_status(freeze.id)!='current'
    assert post(c,{**data,'idempotency_key':'stale-confirm'},location+'/confirm').status_code==409


def test_browser_backup_restart_and_tampered_zip_rejected(instance):
    r,settings,store,fixture,freeze,backups,worker=instance
    job_id=backup_ui.enqueue(r,freeze.id,'interrupted-backup')
    restarted=BackupWorker(backups)
    assert restarted.tick() is None
    backup_ui.recover(r,job_id,'TECHNICAL-FIXTURE: resume requested backup')
    restarted.tick();backup_ui.tick(r)
    path=backup_ui.verified_download(r,job_id)
    path.chmod(0o644);path.write_bytes(b'incomplete download')
    with pytest.raises(ValueError,match='verändert'):backup_ui.verified_download(r,job_id)
    assert not r.all(BackupReceipt)

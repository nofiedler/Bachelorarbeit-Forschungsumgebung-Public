"""Persisted browser download and explicitly self-reported external storage."""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
from uuid import UUID, uuid4
import zipfile

from .artifacts import IntegrityError, read_regular
from .backup import Backups, verify_package
from .domain import Backup, BackupReceipt, Freeze, Study, StudyPhase
from .register import GateError


def sha256(path):
    if path.is_symlink() or not path.is_file(): raise IntegrityError('Sicherungsdatei fehlt oder ist kein reguläres ZIP')
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def enqueue(register, freeze_id, key):
    job=Backups(register.settings,register).enqueue(freeze_id,idempotency_key=key)
    with register.transaction():
        register.connection.execute("INSERT OR IGNORE INTO backup_download(job_id,status) VALUES(?,'requested')",(str(job.id),))
    return job.id


def boot(register):
    with register.transaction():
        register.connection.execute("UPDATE backup_download SET status='failed',error='ZIP-Erstellung durch Neustart unterbrochen. Bewusst erneut vorbereiten.' WHERE status='creating'")


def tick(register):
    row=register.connection.execute("SELECT d.job_id FROM backup_download d JOIN backup_request b USING(job_id) WHERE d.status='requested' AND b.status IN ('internal_ready','transferred') ORDER BY d.rowid LIMIT 1").fetchone()
    if not row:return False
    job_id=row['job_id'];backups=Backups(register.settings,register)
    request=backups.request(job_id);backup=register.get(request['backup_id'],Backup)
    with register.transaction():register.connection.execute("UPDATE backup_download SET status='creating' WHERE job_id=?",(job_id,))
    directory=register.settings.staging/'backup-downloads';directory.mkdir(exist_ok=True)
    temporary=directory/(job_id+'.partial');destination=directory/(job_id+'.zip')
    try:
        package=Path(request['package_path']);manifest,_=verify_package(package,expected_hash=backup.manifest_hash)
        with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,allowZip64=True) as archive:
            for entry in [*manifest['files'],{'path':'manifest.json'}]:
                data,mode=read_regular(package,entry['path'])
                info=zipfile.ZipInfo(entry['path']);info.external_attr=(0o100000|mode)<<16
                info.compress_type=zipfile.ZIP_DEFLATED
                archive.writestr(info,data)
        verify_package(package,expected_hash=backup.manifest_hash)
        digest=sha256(temporary);count=temporary.stat().st_size
        temporary.chmod(0o444);os.replace(temporary,destination)
        backups.record_operation(job_id,'browser_archive_created',{'sha256':digest,'bytes':count,'manifest_hash':backup.manifest_hash,'physical_external_storage_verified':False})
        with register.transaction():register.connection.execute("UPDATE backup_download SET status='ready',path=?,sha256=?,byte_count=?,error=NULL WHERE job_id=?",(str(destination),digest,count,job_id))
    except Exception as exc:
        with register.transaction():register.connection.execute("UPDATE backup_download SET status='failed',error=? WHERE job_id=?",(str(exc),job_id))
    return True


def view(register,job_id):
    backups=Backups(register.settings,register);request=backups.request(job_id)
    freeze=register.get(request['freeze_id'],Freeze);phase=register.get(freeze.phase_id,StudyPhase)
    row=register.connection.execute('SELECT * FROM backup_download WHERE job_id=?',(str(job_id),)).fetchone()
    if not row:raise GateError('Dieser Sicherungsauftrag wurde nicht für einen Browserdownload angelegt')
    download=dict(row)
    backup=register.get(request['backup_id'],Backup) if request['backup_id'] else None
    receipts=[r for r in register.all(BackupReceipt) if backup and r.backup_id==backup.id]
    status=request['status'] if request['status'] in ('failed','recovery_required','creating','requested') else download['status']
    return {'backup_request':request,'download':download,'backup':backup,'freeze':freeze,
        'study':register.get(phase.study_id,Study),'receipt':receipts[-1] if receipts else None,
        'backup_current':register.backup_status(freeze.id)=='current','busy':status in ('requested','creating'),
        'backup_status':status,'backup_error':request['error'] or download['error']}


def verified_download(register,job_id):
    view_data=view(register,job_id);entry=view_data['download']
    if entry['status'] not in ('ready','confirmed'):raise GateError('Die ZIP-Datei ist noch nicht fertig')
    path=Path(entry['path'])
    if path.parent!=register.settings.staging/'backup-downloads' or sha256(path)!=entry['sha256'] or path.stat().st_size!=entry['byte_count']:
        raise IntegrityError('ZIP-Datei verändert; erneut vorbereiten')
    return path


def recover(register,job_id,decision):
    if not decision.strip():raise GateError('Grund für die erneute Vorbereitung fehlt')
    backups=Backups(register.settings,register);request=backups.request(job_id)
    if request['status'] in ('failed','recovery_required'):backups.recover(job_id,decision=decision)
    elif view(register,job_id)['download']['status']!='failed':raise GateError('Kein fehlgeschlagener Sicherungsauftrag')
    with register.transaction():register.connection.execute("UPDATE backup_download SET status='requested',error=NULL WHERE job_id=?",(str(job_id),))


def confirm(register,job_id,data):
    if set(data)!={'person','external_medium','confirm','idempotency_key'} or data['confirm']!='true' or not data['person'].strip() or not data['external_medium'].strip():
        raise GateError('Name, tatsächlicher Ablageort und ausdrückliche Bestätigung erforderlich')
    from .review_ui import once,receipt
    def perform(fingerprint):
        details=view(register,job_id);backups=Backups(register.settings,register)
        verified_download(register,job_id)
        backup=details['backup']
        if backup.substantive_revision!=register.revision(backup.phase_id):raise GateError('Diese Sicherung ist veraltet. Zuerst den aktuellen Stand sichern.')
        verify_package(details['backup_request']['package_path'],expected_hash=backup.manifest_hash)
        if details['study'].data_origin=='synthetic' and not data['external_medium'].startswith('TECHNICAL-FIXTURE:'):
            raise GateError('Synthetische Testablage mit TECHNICAL-FIXTURE: kennzeichnen')
        with register.transaction():
            register.connection.execute("UPDATE backup_download SET status='confirmed' WHERE job_id=?",(str(job_id),))
            saved=register._put(BackupReceipt(code='BROWSER-RECEIPT-'+str(uuid4()),backup_id=backup.id,
                manifest_hash=backup.manifest_hash,person=data['person'].strip(),external_medium=data['external_medium'].strip(),confirmed_at=datetime.now(timezone.utc)))
            receipt(register,data['idempotency_key'],fingerprint,'backup_confirmation',saved.id)
        backups.record_operation(job_id,'external_storage_self_report',{'receipt_id':str(saved.id),'archive_sha256':details['download']['sha256'],'physical_external_storage_verified':False})
        return str(saved.id)
    return once(register,data['idempotency_key'],'backup_confirmation',{'job_id':str(job_id),**data},perform)

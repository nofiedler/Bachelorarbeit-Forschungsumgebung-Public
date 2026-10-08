"""Frozen M6 client extension, staged only inside the trusted evaluator client.

No PHP or candidate modules are imported. Read-only DB credentials observe
full schema/table/row state. Fixture writes are a separate trusted setup verb.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import tempfile
import sys
from uuid import UUID
import pymysql
import research_env.sandbox_client as base

FIELDS=('user_id','first_name','last_name','user','password','avatar','last_login','failed_login','role','account_enabled')
MODES={'ONLY_FULL_GROUP_BY','STRICT_TRANS_TABLES','NO_ZERO_IN_DATE','NO_ZERO_DATE','ERROR_FOR_DIVISION_BY_ZERO','NO_ENGINE_SUBSTITUTION'}
FILE_BYTES=16*1024*1024


def connect(*, write=False):
    c=pymysql.connect(host='127.0.0.1',user='study' if write else 'study_review',
        password=os.environ['DB_PASSWORD' if write else 'DB_REVIEW_PASSWORD'],database='study',
        charset='utf8mb4',defer_connect=True,read_timeout=None,write_timeout=None)
    c.connect(socket.create_connection(('127.0.0.1',3306),timeout=None))
    with c.cursor() as q:
        q.execute('SET SESSION collation_connection=utf8mb4_bin')
        q.execute("SET SESSION time_zone='+00:00'")
        q.execute('SET SESSION sql_mode=%s',(','.join(sorted(MODES)),))
    return c


def db_state():
    c=connect()
    try:
        with c.cursor() as q:
            q.execute('SHOW FULL TABLES');tables=sorted([list(x) for x in q.fetchall()])
            q.execute('SHOW CREATE TABLE users');ddl=q.fetchone()[1]
            q.execute('SELECT '+','.join(FIELDS)+' FROM users ORDER BY user_id')
            rows=[{key:str(value) if value is not None else None for key,value in zip(FIELDS,row)} for row in q.fetchall()]
            q.execute('SELECT @@global.character_set_server,@@global.collation_server,@@global.time_zone,@@global.sql_mode,@@session.character_set_connection,@@session.collation_connection,@@session.time_zone,@@session.sql_mode')
            settings=list(q.fetchone());settings[3]=sorted(settings[3].split(','));settings[7]=sorted(settings[7].split(','))
            q.execute('SHOW GRANTS FOR CURRENT_USER()');grants=[x[0] for x in q.fetchall()]
        return {'tables':tables,'ddl':ddl,'rows':rows,'settings':settings,'reader_grants':grants}
    finally:c.close()


def regular(parent,name):
    # Same descriptor anchors check+open+read. Symlinks never followed; hardlinks
    # checked before and after. Candidate is frozen before this snapshot verb.
    if name in ('','.','..') or '/' in name or '\\' in name:raise ValueError('Unsafe upload name')
    fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_size>FILE_BYTES:
            raise ValueError('Non-regular/link/oversized evidence')
        data=bytearray()
        while chunk:=os.read(fd,min(65536,FILE_BYTES+1-len(data))):
            data.extend(chunk)
            if len(data)>FILE_BYTES:raise ValueError('Evidence changed beyond byte protection')
        after=os.fstat(fd);linked=os.stat(name,dir_fd=parent,follow_symlinks=False)
        signature=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_mode,s.st_nlink)
        if signature(before)!=signature(after) or signature(after)!=signature(linked):raise ValueError('TOCTOU evidence rejected')
        return bytes(data)
    finally:os.close(fd)


def directory(parts):
    fd=os.open('/runtime',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for part in parts:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=child
        return fd
    except BaseException:os.close(fd);raise


def inventory():
    fd=directory(('app','study','uploads'))
    try:
        before=sorted(os.listdir(fd));values={}
        for name in before:
            data=regular(fd,name)
            if name=='.gitignore':continue
            values[name]={'size':len(data),'sha256':hashlib.sha256(data).hexdigest()}
        if before!=sorted(os.listdir(fd)):raise ValueError('Upload inventory changed during snapshot')
        return values
    finally:os.close(fd)


def session():
    fd=directory(('framework','sessions'))
    try:
        entries=[];tokens={}
        for name in sorted(os.listdir(fd)):
            if name=='.gitignore':continue
            data=regular(fd,name)
            # The versioned scaffold explicitly uses JSON serialization.
            # Never execute or deserialize candidate PHP objects.
            decoded=json.loads(data)
            if not isinstance(decoded,dict):raise ValueError('Invalid scaffold session JSON')
            auth=[str(value) for key,value in decoded.items() if key.startswith('login_web_')]
            if auth:
                entries.append({'session_id':name,'auth_identity':auth})
                token=decoded.get('_token')
                if isinstance(token,str) and token:tokens[name]=token
        return {'sessions':entries,'csrf_tokens':tokens}
    finally:os.close(fd)


def security_probe():
    with tempfile.TemporaryDirectory(dir='/requests') as path:
        root=Path(path);secret=root/'reviewer-secret';secret.write_bytes(b'M6 PRIVATE REVIEWER FILE NEVER DISCLOSED')
        uploads=root/'uploads';uploads.mkdir();(uploads/'own').write_bytes(b'candidate bytes')
        (uploads/'symlink').symlink_to(secret);os.link(secret,uploads/'hardlink')
        fd=os.open(uploads,os.O_RDONLY|os.O_DIRECTORY);checks={}
        try:
            for name in ('symlink','hardlink','../reviewer-secret','/reviewer-secret'):
                try:regular(fd,name);checks[name]=False
                except (OSError,ValueError):checks[name]=True
            original=os.read;swapped=False
            def race(file,n):
                nonlocal swapped
                data=original(file,n)
                if not swapped:
                    swapped=True;(uploads/'own').unlink();(uploads/'own').symlink_to(secret)
                return data
            os.read=race
            try:
                try:regular(fd,'own');checks['TOCTOU']=False
                except (OSError,ValueError):checks['TOCTOU']=True
            finally:os.read=original
            (root/'intermediate').symlink_to(uploads,target_is_directory=True)
            try:
                x=os.open(root/'intermediate',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);os.close(x);checks['intermediate_symlink']=False
            except OSError:checks['intermediate_symlink']=True
            return {'checks':checks,'all_blocked':all(checks.values()),'private_file_disclosed':False,
                    'private_file_sha256':hashlib.sha256(secret.read_bytes()).hexdigest(),'trusted_client_uid':os.getuid()}
        finally:os.close(fd)


def perform(client,request,emit=None):
    verb=request.get('method')
    if verb=='EVALUATION_SECURITY' and request=={'method':verb,'path':'/security'}:return security_probe()
    if verb=='EVALUATION_DB' and request=={'method':verb,'path':'/users'}:return db_state()
    if verb=='EVALUATION_FILES' and request=={'method':verb,'path':'/uploads'}:return {'uploads':inventory()}
    if verb=='EVALUATION_SESSION' and request=={'method':verb,'path':'/session'}:return session()
    if verb=='EVALUATION_FIXTURE' and set(request)=={'method','path','data'} and request['path']=='/users':
        rows=request['data']['rows'];c=connect(write=True)
        try:
            with c.cursor() as q:
                q.execute('DELETE FROM users')
                for row in rows:q.execute('INSERT INTO users ('+','.join(FIELDS)+') VALUES ('+','.join('%s' for _ in FIELDS)+')',tuple(row[k] for k in FIELDS))
            c.commit()
        finally:c.close()
        return {'fixture_rows_written':len(rows)}
    if verb not in ('GET','POST') or set(request)-{'method','path','data','files'} or request['path'].split('?',1)[0] not in base.ROUTES:
        raise ValueError('Invalid frozen public HTTP transport')
    files=request.get('files')
    if files is not None:
        if set(files) not in ({'uploaded'},{'_m6_boundary'}):raise ValueError('Unknown fixed multipart field')
        files={key:(value['name'],bytes.fromhex(value['hex']),value['type']) for key,value in files.items()}
    with client.stream(verb,request['path'],data=request.get('data'),files=files,headers={'Accept-Encoding':'identity'}) as response:
        metadata={'status':response.status_code,'headers':dict(response.headers)}
        if emit:emit('headers',metadata)
        body=bytearray();error=None
        try:
            for part in response.iter_raw():
                remaining=base.RESPONSE_BYTES-len(body);retained=part[:remaining];body.extend(retained)
                if retained and emit:emit('body',retained.hex())
                if len(part)>remaining:error='response_byte_limit';break
        except Exception as exc:error=type(exc).__name__+': '+str(exc)
        result=dict(metadata)
        if not emit:result['body']=body.decode('utf8','replace')
        if error:result.update(transport_error=error,partial_evidence=True)
        return result


original=base.perform
base.perform=perform
if __name__=='__main__':base.main()

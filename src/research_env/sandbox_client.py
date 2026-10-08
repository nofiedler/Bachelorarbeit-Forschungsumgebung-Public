"""Fixed HTTPX transport in a separate Python-only process; no PHP import.

M4 records transport, #10 supplies assertions. There are no HTTP/job timers.
Finite response bytes protect the client; exceeded output retains partial data.
"""
import hashlib
import json
import os
import socket
from pathlib import Path
import sys
from uuid import UUID
import httpx

RESPONSE_BYTES=256*1024
ROUTES=('/study-access','/study-exit','/study/brute','/study/sqli','/study/upload','/')


def perform(client,request,emit=None):
    if request.get('method')=='DB_ROWS' and request.get('path')=='/users' and set(request)=={'method','path'}:
        import pymysql
        connection=pymysql.connect(host='127.0.0.1',user='study',password=os.environ['DB_PASSWORD'],database='study',defer_connect=True,read_timeout=None,write_timeout=None)
        # Already-created blocking socket bypasses the library's default connect
        # timer; OS transport failures remain actual platform behaviour.
        connection.connect(socket.create_connection(('127.0.0.1',3306),timeout=None))
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT user_id,first_name,last_name,user,password,avatar,last_login,failed_login,role,account_enabled FROM users ORDER BY user_id')
                return {'rows':[[str(x) if x is not None else None for x in row] for row in cursor.fetchall()]}
        finally:connection.close()
    if request.get('method')=='UPLOAD_INVENTORY' and request.get('path')=='/uploads' and set(request)=={'method','path'}:
        from .artifacts import directory,read_regular
        root=Path('/runtime');parts=('app','study','uploads')
        files=[]
        with directory(root,parts) as fd:
            for name in sorted(os.listdir(fd)):
                if name=='.gitignore':continue
                info=os.stat(name,dir_fd=fd,follow_symlinks=False)
                if info.st_size>16*1024*1024:raise ValueError('Upload file byte protection')
                data,mode=read_regular(root,'/'.join((*parts,name)))
                files.append({'name':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
        return {'uploads':files}
    if set(request)-{'method','path','data','files'} or request.get('method') not in ('GET','POST'):
        raise ValueError('Invalid fixed transport request')
    path=request['path']
    if not isinstance(path,str) or path.split('?',1)[0] not in ROUTES:
        raise ValueError('Unknown public contract route')
    files=request.get('files')
    if files is not None:
        if set(files)!={'uploaded'} or set(files['uploaded'])!={'name','hex','type'}:
            raise ValueError('Invalid public upload transport')
        file=files['uploaded'];data=bytes.fromhex(file['hex'])
        files={'uploaded':(file['name'],data,file['type'])}
    # Raw transfer bytes avoid an unbounded decompression expansion. Contract
    # responses normally use identity; encoded responses retain their real header
    # and bytes for a subsequent evaluator, without interpreting compressed data.
    with client.stream(request['method'],path,data=request.get('data'),files=files,headers={'Accept-Encoding':'identity'}) as response:
        metadata={'status':response.status_code,'headers':dict(response.headers)}
        if emit:emit('headers',metadata)
        body=bytearray();error=None
        try:
            for part in response.iter_raw():
                remaining=RESPONSE_BYTES-len(body)
                retained=part[:remaining];body.extend(retained)
                if retained and emit:emit('body',retained.hex())
                if len(part)>remaining:error='response_byte_limit';break
        except Exception as exc:error=type(exc).__name__+': '+str(exc)
        result=dict(metadata)
        if not emit:result['body']=body.decode('utf-8','replace')
        if error:result.update(transport_error=error,partial_evidence=True)
        return result


def frame(event,value):
    print(json.dumps({'event':event,'value':value}),flush=True)


def main():
    with httpx.Client(base_url='http://127.0.0.1:8000',timeout=None,trust_env=False,follow_redirects=False) as client:
        jar=Path('/tmp/m4-cookies.json')
        if jar.exists():
            for item in json.loads(jar.read_text()):
                client.cookies.set(item['name'],item['value'],domain=item['domain'],path=item['path'])
        # Only the trusted controller supplies a UUID, never a path. Each request
        # has its own private file and cannot overwrite another pending input.
        if len(sys.argv)!=3 or sys.argv[1]!='--request':raise ValueError('Fixed request UUID required')
        identifier=str(UUID(sys.argv[2]))
        source=Path('/requests')/('m4-request-'+identifier+'.json')
        try:request=json.loads(source.read_text())
        finally:source.unlink(missing_ok=True)
        try:
            result=perform(client,request,emit=frame)
            jar.write_text(json.dumps([{'name':c.name,'value':c.value,'domain':c.domain,'path':c.path} for c in client.cookies.jar]))
            frame('result',result)
        except Exception as exc:frame('result',{'transport_error':type(exc).__name__,'reason':str(exc)})


if __name__=='__main__':main()

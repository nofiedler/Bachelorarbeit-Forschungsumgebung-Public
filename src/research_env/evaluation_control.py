"""Trusted fixed fixture/reset/rechte tool; only the own storage volume is RW.

Run by the controller while the candidate is paused. No candidate import and
no recursive path following, including during cleanup of malicious leftovers.
"""
import base64
import hashlib
import json
import os
import stat
import sys
from uuid import uuid4


def clear(fd):
    for name in os.listdir(fd):
        info=os.stat(name,dir_fd=fd,follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            try:os.fchmod(child,0o755);clear(child)
            finally:os.close(child)
            os.rmdir(name,dir_fd=fd)
        else:os.unlink(name,dir_fd=fd)


def child(root,parts):
    fd=os.dup(root)
    try:
        for part in parts:
            try:os.mkdir(part,0o755,dir_fd=fd)
            except FileExistsError:pass
            nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=nxt
        return fd
    except BaseException:os.close(fd);raise


def perform(action,payload):
    root=os.open('/runtime',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        if action=='reset':
            clear(root)
            for path in ('app/study/uploads','framework/sessions','framework/cache/data','framework/cache/locks','framework/views','framework/testing','logs'):
                fd=child(root,path.split('/'));os.close(fd)
            fd=child(root,('app','study','uploads'))
            try:
                for name,encoded in payload['files'].items():
                    if not name or name in ('.','..') or '/' in name or '\\' in name:raise ValueError('Unsafe fixture file')
                    data=base64.b64decode(encoded,validate=True)
                    target=os.open(name,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o644,dir_fd=fd)
                    with os.fdopen(target,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
            finally:os.close(fd)
            return {'reset':True,'session':'empty','cache':'empty','uploads':sorted(payload['files']),'uid':os.getuid(),'gid':os.getgid()}
        if action=='rights_probe':
            fd=child(root,('app','study','uploads'))
            try:
                info=os.fstat(fd);name='.trusted-write-probe-'+uuid4().hex
                denied=False
                try:
                    target=os.open(name,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600,dir_fd=fd)
                    os.close(target);os.unlink(name,dir_fd=fd)
                except PermissionError:denied=True
                return {'write_denied':denied,'uid':os.getuid(),'gid':os.getgid(),'owner_uid':info.st_uid,'owner_gid':info.st_gid,
                        'mode':oct(stat.S_IMODE(info.st_mode)),'mounts':open('/proc/self/mountinfo').read(),
                        'posix_acl':os.listxattr('/runtime/app/study/uploads'),'inventory_names':sorted(os.listdir(fd))}
            finally:os.close(fd)
        raise ValueError('Unknown fixed control action')
    finally:os.close(root)


if __name__=='__main__':
    action=sys.argv[1];payload=json.loads(base64.b64decode(sys.argv[2]))
    print(json.dumps(perform(action,payload)),flush=True)

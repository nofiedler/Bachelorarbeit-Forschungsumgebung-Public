#!/usr/bin/env python3
"""Small genuine DB/HTTP/command-worker smoke; offline fixture, no human gates."""
import argparse
import json
from pathlib import Path
import platform
import sys
from uuid import uuid4
from fastapi.testclient import TestClient
from research_env.config import Settings
from research_env.database import migrate
from research_env.domain import Study,ConfigurationVersion
from research_env.preparation import Intent,submit,process_command
from research_env.register import Register
from research_env.web import create_app

p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--smoke',action='store_true');a=p.parse_args()
a.output.mkdir(parents=True)
settings=Settings(*(a.output/name for name in ('control','artifacts','checkpoints','staging')))
for path in (settings.control,settings.artifacts,settings.checkpoints,settings.staging):path.mkdir()
migrate(settings);r=Register(settings)
with TestClient(create_app(settings)) as client:
    response=client.get('/');assert response.status_code==200
    token=client.cookies['research_csrf']
    result=client.post('/actions',data={'csrf':token,'idempotency_key':'smoke-demo','action':'demo','title':'TECHNICAL-FIXTURE: personal demo'},headers={'Origin':'http://testserver'},follow_redirects=False)
    assert result.status_code==303,result.text
    command_id=result.headers['location'].split('/')[-1]
    process_command(r)
    row=r.connection.execute('SELECT * FROM ui_command WHERE id=?',(command_id,)).fetchone()
    assert row['status']=='completed',dict(row)
    data=json.loads(row['result'])
    demo=client.get(data['url']);assert demo.status_code==200,demo.text
    duplicate=client.post('/actions',data={'csrf':token,'idempotency_key':'smoke-demo','action':'demo','title':'TECHNICAL-FIXTURE: personal demo'},headers={'Origin':'http://testserver'},follow_redirects=False)
    assert duplicate.headers['location']==result.headers['location']
    assert len(r.all(Study))==1 and len(r.all(ConfigurationVersion))==1
    (a.output/'demo.html').write_text(demo.text)
    for route in ('/studies','/free-tests','/runs','/api/status'):
        check=client.get(route);assert check.status_code==200,(route,check.text)
(a.output/'result.json').write_text(json.dumps({'expected':'one personal synthetic demo; immutable command; no model calls; HTML and select forms render','actual':'PASS','checks':9,'python':platform.python_version(),'platform':platform.platform(),'limits':'TestClient HTTP plus actual command-worker service; not yet browser or Docker execution'},indent=2)+'\n')
print('M8 DB/HTTP/PERSISTED COMMAND SMOKE 9/9 PASS');r.close()

"""Separate executable process fixture; durable sends are local append-only bytes."""
import json
import os
from pathlib import Path
import sys
import time
from research_env.adapter import CallJournal
from research_env.domain import ModelCall
from research_env.providers import MockAdapter
from research_env.register import Register
from test_adapter import make_settings, prepared, response

root,window,mode=Path(sys.argv[1]),sys.argv[2],sys.argv[3]
def sent(_):
    with open(root/'sends','ab') as f:
        f.write(b'SEND\n'); f.flush(); os.fsync(f.fileno())
    if window=='hang':
        while True: time.sleep(0.1)
def crash(point):
    if point==window: os._exit(77)
if mode=='crash':
    from research_env.providers import KnownTransient
    responses=[KnownTransient(),response()] if window.startswith('retry_') else [response()]
    r,settings,j,a,call,_=prepared(root,MockAdapter(responses,on_send=sent))
    (root/'call_id').write_text(str(call.id))
    if window=='before_incorporation':
        a.execute_attempt(j,call.id)
        j.incorporate(call.id,output_validator=json.loads,crash=crash)
    else: a.execute_attempt(j,call.id,crash=crash)
else:
    settings=make_settings(root);r=Register(settings);j=CallJournal(r)
    a=MockAdapter([response()],on_send=sent)
    call=r.get((root/'call_id').read_text(),ModelCall)
    out=a.execute_attempt(j,call.id,continuation='TECHNICAL-FIXTURE: conscious restart')
    if window=='before_incorporation': j.incorporate(call.id,output_validator=json.loads)
    out=j.diagnostic(call.id)
    print(json.dumps({'state':out['attempts'][-1]['status'],'total_send':len((root/'sends').read_bytes().splitlines()) if (root/'sends').exists() else 0,'diagnostic':out}))
    r.close()

#!/usr/bin/env python3
"""Trusted actual process-crash probe, no candidate-selected commands."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).parents[1]/'src'))
import docker
from research_env.artifacts import ArtifactStore
from research_env.config import Settings
from research_env.register import Register
from research_env.sandbox_runtime import RuntimeImages,Sandbox
p=argparse.ArgumentParser()
for field in ('output','job','candidate','tests','images'):p.add_argument('--'+field,required=True)
a=p.parse_args();root=Path(a.output)
settings=Settings(*(root/name for name in ('control','artifacts','checkpoints','staging')))
r=Register(settings);store=ArtifactStore(settings,r);client=docker.from_env(timeout=None)
sandbox=Sandbox(store,client,images=RuntimeImages(**json.loads(a.images)),assets=Path(__file__).parents[1])
ex=sandbox.allocate(a.job,a.candidate,profile='internal',internal_tests_id=a.tests);ex.start()
raw=ex.path/'candidate.raw'
while not raw.exists() or b'REAL_WORKER_CRASH_CANDIDATE' not in raw.read_bytes():time.sleep(.05)
ready=root/'crash-worker-ready.json'
with open(ready,'x') as f:
    json.dump({'execution':ex.id,'candidate_raw':str(raw),'worker_pid':os.getpid(),'intentional_crash_exit':37},f);f.flush();os.fsync(f.fileno())
# Actual abrupt worker exit: no finally, no SDK cleanup, no graceful stop.
os._exit(37)

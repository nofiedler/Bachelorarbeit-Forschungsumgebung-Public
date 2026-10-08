#!/usr/bin/env python3
"""Host proof wrapper: immutable input readback, original streams, native exit.

No process, job, evaluator or HTTP time limit. Reads only declared local inputs
and owns one named cost-free coordinator; no daemon-wide cleanup is performed.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time
from uuid import uuid4

p=argparse.ArgumentParser();p.add_argument('--inputs',type=Path,required=True);p.add_argument('--manifest',type=Path,required=True)
p.add_argument('--output',type=Path,required=True);p.add_argument('--kind',choices=('components','native','correction'),required=True)
p.add_argument('--probes-only',action='store_true');p.add_argument('--development',action='store_true');p.add_argument('--operational',action='store_true');p.add_argument('--vendor-manifest',type=Path,default=Path('/tmp/issue-6-composer-dependencies-v1-root-manifest.json'));p.add_argument('--security',action='store_true');p.add_argument('--vendor',type=Path);p.add_argument('--reference',action='append');p.add_argument('--docker',default='/usr/local/bin/docker')
p.add_argument('--prior',type=Path);p.add_argument('--socket',default='/Users/noahfiedler/.docker/run/docker.sock')
a=p.parse_args()
if a.output.exists():p.error('New output directory required; old evidence never overwritten')
a.output.mkdir(parents=True)
image='sha256:2ffd3236561b05e7ee97ee23ae69671f9ea5062aedcb6bbce73190dac59e97cc'
manifest=json.loads(a.manifest.read_text())

def readback():
    differences=[]
    actual_paths={str(p.relative_to(a.inputs)) for p in a.inputs.rglob('*') if p.is_file() or p.is_symlink()}
    if actual_paths!=set(manifest):differences.append({'missing_paths':sorted(set(manifest)-actual_paths),'extra_paths':sorted(actual_paths-set(manifest))})
    expected_dirs={str(parent) for name in manifest for parent in Path(name).parents if str(parent)!='.'}
    extra_dirs={str(p.relative_to(a.inputs)) for p in a.inputs.rglob('*') if p.is_dir()}-expected_dirs
    if extra_dirs:differences.append({'extra_directories':sorted(extra_dirs)})
    for name,expected in manifest.items():
        path=a.inputs/name
        if not path.is_file() or path.is_symlink() or path.stat().st_nlink!=1:differences.append({'path':name,'missing_or_link':True});continue
        actual={'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        if actual!=expected:differences.append({'path':name,'expected':expected,'actual':actual})
    return differences

def vendor_readback():
    if a.kind!='native':return None
    original=json.loads(a.vendor_manifest.read_text())
    expected={e['path']:e for e in original['entries']}
    actual_paths={str(p.relative_to(a.vendor)) for p in a.vendor.rglob('*') if p.is_file() or p.is_symlink()}
    differences=[]
    if actual_paths!=set(expected):differences.append({'missing_paths':sorted(set(expected)-actual_paths),'extra_paths':sorted(actual_paths-set(expected))})
    for name,e in expected.items():
        path=a.vendor/name
        if path.is_symlink() or not path.is_file() or path.stat().st_nlink!=1:differences.append({'path':name,'missing_or_link':True});continue
        actual={'size':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        if actual!={k:e[k] for k in actual}:differences.append({'path':name,'actual':actual})
    return {'manifest':str(a.vendor_manifest.resolve()),'manifest_sha256':hashlib.sha256(a.vendor_manifest.read_bytes()).hexdigest(),'files':len(expected),'bytes':sum(e['size'] for e in expected.values()),'differences':differences}

def containers():
    r=subprocess.run([a.docker,'ps','-a','--format','{{.ID}} {{.Names}}'],capture_output=True,text=True)
    return {'exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr}

before=readback();vendor_before=vendor_readback()
if vendor_before and vendor_before['differences']:raise ValueError('Frozen vendor differs before execution')
if before:raise ValueError('Inputs differ before execution: '+json.dumps(before))
prior_before=None
if a.kind=='correction':
    if not a.prior:p.error('Correction requires retained prior evidence')
    prior_before={str(p.relative_to(a.prior)):{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(a.prior.rglob('*')) if p.is_file()}
    (a.output/'prior-before.manifest.json').write_text(json.dumps(prior_before,indent=2)+'\n')
name='m6-proof-'+uuid4().hex
command=[a.docker,'run','--rm','--name',name,'--network','none','--entrypoint','python','-e','PYTHONPATH=/work/src:/work/tests','-e','M6_PROBE_EVIDENCE_DIR=/evidence/component-probes',
         '-v',str(a.inputs.resolve())+':/work:ro','-v',str(a.output.resolve())+':/evidence','-w','/work']
if a.kind in ('native','correction'):
    command+=['-v',a.socket+':/var/run/docker.sock']
if a.kind=='correction':command+=['-v',str(a.prior.resolve())+':/prior:ro']
if a.kind=='native':
    if not a.vendor:p.error('Native requires frozen standalone vendor')
    command+=['-v',str(a.vendor.resolve())+':/vendor:ro']
command+=[image]
if a.kind=='components':command+=['-m','pytest','-p','no:cacheprovider','--junitxml=/evidence/junit.xml']
elif a.kind=='correction':command+=['scripts/verify-evaluation-correction.py','--prior','/prior','--output','/evidence/runtime']
else:
    command+=['scripts/verify-evaluation.py','--output','/evidence/runtime','--vendor','/vendor']
    for ref in a.reference or ():command+=['--reference',ref]
    if a.probes_only:command+=['--probes-only']
    if a.security:command+=['--security']
    if a.development:command+=['--development']
    if a.operational:command+=['--operational']
record={'command':command,'image':image,'coordinator_name':name,'input_manifest':str(a.manifest.resolve()),'input_manifest_sha256':hashlib.sha256(a.manifest.read_bytes()).hexdigest(),
        'prior_manifest_sha256':hashlib.sha256((a.output/'prior-before.manifest.json').read_bytes()).hexdigest() if prior_before else None,'inputs':len(manifest),'pre_vendor':vendor_before,'pre_readback':before,'pre_containers':containers(),'start_utc':datetime.now(timezone.utc).isoformat(),'start_monotonic':time.monotonic(),
        'no_model_call':True,'no_elapsed_cutoff':True}
(a.output/'process.started.json').write_text(json.dumps(record,indent=2)+'\n')
with open(a.output/'stdout.raw','xb') as stdout,open(a.output/'stderr.raw','xb') as stderr:
    process=subprocess.Popen(command,stdout=stdout,stderr=stderr)
    try:code=process.wait()
    except KeyboardInterrupt:
        # A deliberate local interrupt is observable; no remote cancellation
        # guarantee or candidate failure is inferred from it.
        process.send_signal(2);code=process.wait();record['manual_interrupt']=True
prior_differences=None
if prior_before is not None:
    prior_after={str(p.relative_to(a.prior)):{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(a.prior.rglob('*')) if p.is_file()}
    prior_differences=[name for name in set(prior_before)|set(prior_after) if prior_before.get(name)!=prior_after.get(name)]
record.update(prior_post_differences=prior_differences,end_utc=datetime.now(timezone.utc).isoformat(),end_monotonic=time.monotonic(),exit_code=code,post_readback=readback(),post_vendor=vendor_readback(),post_containers=containers())
(a.output/'process.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps({'exit_code':code,'inputs_unchanged':not record['post_readback'],'seconds':record['end_monotonic']-record['start_monotonic'],'output':str(a.output.resolve())}),flush=True)
raise SystemExit(code if not record['post_readback'] and not prior_differences and not (record['post_vendor'] and record['post_vendor']['differences']) else 2)

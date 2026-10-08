#!/usr/bin/env python3
"""Immutable M8 test inputs, complete channels and actual execution provenance."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
IMAGE='sha256:2ffd3236561b05e7ee97ee23ae69671f9ea5062aedcb6bbce73190dac59e97cc'
p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--mode',choices=('smoke','targeted','regression','native','build'),required=True);p.add_argument('--filter',default='')
p.add_argument('--test-file',default='tests/test_evidence_ui.py');p.add_argument('--image-tag',default='research-env-m8:issue14-v1')
a=p.parse_args()
if a.mode=='targeted' and (Path(a.test_file).parent!=Path('tests') or not a.test_file.endswith('.py') or not (ROOT/a.test_file).is_file()):p.error('Gezielter Test muss eine bestehende tests/*.py-Datei sein')
info=subprocess.check_output(['diskutil','info','-plist','/Volumes/PortableSSD'])
import plistlib
volume=plistlib.loads(info)
if volume.get('VolumeUUID')!='F7DE0164-2C56-494B-8A24-33450FE49BE4' or not Path('/Volumes/PortableSSD').is_mount():p.error('Erwartete SSD fehlt; kein interner Rückfall')
if not a.output.resolve().is_relative_to(Path('/Volumes/PortableSSD/Bachelorarbeit-Forschungsumgebung/tmp')):p.error('Ausgaben ausschließlich direkt auf SSD')
if a.mode=='build':
    actual=subprocess.check_output(['docker','image','inspect','research-env-m4-python-base:2ffd3236561b05e7ee97ee23ae69671f9ea5062aedcb6bbce73190dac59e97cc','--format','{{.Id}}'],text=True).strip()
    if actual!=IMAGE:p.error('Prepared dependency image changed')
if a.output.exists():p.error('Unveränderte Originale erhalten; neues Versionsziel nötig')
a.output.mkdir(parents=True);bundle=a.output/'input';bundle.mkdir();manifest=[]
for path in sorted(ROOT.rglob('*')):
    if not path.is_file() or path.is_symlink():continue
    relative=path.relative_to(ROOT)
    if any(part in {'.git','.pytest_cache','.DS_Store','__pycache__','.cache','secrets','node_modules'} for part in relative.parts) or relative.parts[:2]==('docs','pruefungen'):continue
    if path.name=='.env' or path.name.startswith('.env.') and path.name!='.env.example' or path.suffix.lower() in {'.pem','.key','.p12','.pfx'} or path.name in {'auth.json','credentials.json'}:continue
    root_allow={'AGENTS.md','CONTEXT.md','README.md','pyproject.toml','requirements.lock','.dockerignore','.gitignore','compose.yaml','compose.assets.yaml','compose.study.yaml'}
    if len(relative.parts)==1 and relative.name not in root_allow:continue
    if len(relative.parts)>1 and relative.parts[0] not in ('src','tests','scripts','assets','evaluation','docs','docker'):continue
    target=bundle/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
    manifest.append({'path':str(relative),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size,'mode':path.stat().st_mode & 0o7777})
(a.output/'input-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');shutil.copyfile(__file__,a.output/'original-driver.py')
command=['docker','run','--entrypoint','python','--rm','--network','none','--read-only','--tmpfs','/tmp:rw,nosuid,nodev','-e','PYTHONDONTWRITEBYTECODE=1','-e','PYTHONPATH=/input/src:/input/tests','-e','MPLCONFIGDIR=/tmp/matplotlib','--mount',f'type=bind,src={bundle},dst=/input,readonly','--mount',f'type=bind,src={a.output},dst=/output','--workdir','/input']
# Evidence UI native path does not need Docker-socket access.
command+=[IMAGE]
if a.mode=='build':command=['docker','build','--network=none','--progress=plain','--build-arg','RESEARCH_DEPENDENCY_IMAGE=research-env-m4-python-base:2ffd3236561b05e7ee97ee23ae69671f9ea5062aedcb6bbce73190dac59e97cc','-f',str(bundle/'docker/python.Dockerfile'),'-t',a.image_tag,str(bundle)]
elif a.mode=='smoke':command+=['scripts/verify-preparation.py','--output','/output/smoke','--smoke']
elif a.mode=='native':command+=['scripts/verify-evidence-ui.py','--output','/output/native']
else:
    command+=['-m','pytest','-p','no:cacheprovider','--basetemp=/output/pytest','-q']
    if a.mode=='targeted':command+=[a.test_file]
    if a.filter:command+=['-k',a.filter]
before=datetime.now(timezone.utc).isoformat()
with (a.output/'stdout.raw').open('wb') as stdout,(a.output/'stderr.raw').open('wb') as stderr:result=subprocess.run(command,stdout=stdout,stderr=stderr)
after=datetime.now(timezone.utc).isoformat()
record={'actualCommand':command,'actualExit':result.returncode,'utc_before_subprocess':before,'utc_after_subprocess':after,
 'driver_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'image':IMAGE,'ssd_volume_uuid':volume['VolumeUUID'],
 'input_manifest_sha256':hashlib.sha256((a.output/'input-manifest.json').read_bytes()).hexdigest(),
 'source_base':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'mode':a.mode,'filter':a.filter,
 'stdout_sha256':hashlib.sha256((a.output/'stdout.raw').read_bytes()).hexdigest(),'stderr_sha256':hashlib.sha256((a.output/'stderr.raw').read_bytes()).hexdigest(),
 'synthetic':True,'limits':'Technische Fixture; keine Fachfreigabe/Studie/reale Providerplattform'}
(a.output/'execution.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record));print((a.output/'stdout.raw').read_text());print((a.output/'stderr.raw').read_text(),file=sys.stderr);sys.exit(result.returncode)

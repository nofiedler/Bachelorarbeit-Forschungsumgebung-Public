#!/usr/bin/env python3
"""Preserve actual subprocess command, driver, complete input bytes and channels."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'sha256:2ffd3236561b05e7ee97ee23ae69671f9ea5062aedcb6bbce73190dac59e97cc'
p = argparse.ArgumentParser();p.add_argument('--output', type=Path, required=True);p.add_argument('--mode', choices=('smoke','targeted','regression','native'), required=True);p.add_argument('--filter', default='')
a = p.parse_args()
if a.output.exists():
    p.error('Fresh output only; preserve all original attempts')
a.output.mkdir(parents=True)
bundle = a.output/'input';bundle.mkdir()
paths = []
for path in sorted(ROOT.rglob('*')):
    if not path.is_file() or path.is_symlink():
        continue
    relative = path.relative_to(ROOT)
    if relative.parts[0] in ('.git','.pytest_cache','.DS_Store') or '__pycache__' in relative.parts or relative.parts[:2]==('docs','pruefungen'):
        continue
    if relative.parts[0] not in ('src','tests','scripts','assets','evaluation','docs','docker') and len(relative.parts)>1:
        continue
    target = bundle/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
    paths.append({'path':str(relative),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size})
(a.output/'input-manifest.json').write_text(json.dumps(paths,indent=2)+'\n')
shutil.copyfile(__file__, a.output/'original-driver.py')
base = ['docker','run','--entrypoint','/usr/local/bin/python','--rm','--network','none','--read-only','--tmpfs','/tmp:rw,nosuid,nodev', '-e','PYTHONDONTWRITEBYTECODE=1','-e','PYTHONPATH=/input/src:/input/tests','-e','MPLCONFIGDIR=/tmp/matplotlib','--mount',f'type=bind,src={bundle},dst=/input,readonly','--mount',f'type=bind,src={a.output},dst=/output','--workdir','/input',IMAGE]
if a.mode=='smoke':
    code = "from pathlib import Path; import sys, matplotlib, pydantic; from io import BytesIO; from research_env import analysis,analysis_store,analysis_export; from research_env.config import Settings; from research_env.database import migrate; s=Settings(*(Path('/output/smoke')/p for p in ('control','artifacts','checkpoints','staging'))); migrate(s); import matplotlib.pyplot as plt; f,a=plt.subplots(); a.plot([0,1],[0,1]); f.savefig('/output/smoke.svg'); f.savefig('/output/smoke.png'); print(sys.version,pydantic.__version__,matplotlib.__version__); print('M7 IMPORT/SCHEMA/SVG/PNG SMOKE PASS')"
    command = base+['-c',code]
elif a.mode=='native':
    command = base+['scripts/verify-analysis.py','--output','/output/native']
else:
    command = base+['-m','pytest','-p','no:cacheprovider','--basetemp=/output/pytest','-q']
    if a.mode=='targeted':
        command += ['tests/test_analysis.py']
    if a.filter:
        command += ['-k',a.filter]
before = datetime.now(timezone.utc).isoformat()
with (a.output/'stdout.txt').open('wb') as stdout, (a.output/'stderr.txt').open('wb') as stderr:
    run = subprocess.run(command, stdout=stdout, stderr=stderr)
after = datetime.now(timezone.utc).isoformat()
record = {'actualCommand':command,'exit':run.returncode,'utc_before_subprocess':before,'utc_after_subprocess':after,
    'driver_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'image':IMAGE,'input_manifest_sha256':hashlib.sha256((a.output/'input-manifest.json').read_bytes()).hexdigest(),
    'source_base':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'mode':a.mode,'filter':a.filter,
    'stdout_sha256':hashlib.sha256((a.output/'stdout.txt').read_bytes()).hexdigest(),'stderr_sha256':hashlib.sha256((a.output/'stderr.txt').read_bytes()).hexdigest()}
(a.output/'execution.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record));print((a.output/'stdout.txt').read_text());print((a.output/'stderr.txt').read_text(),file=sys.stderr)
sys.exit(run.returncode)

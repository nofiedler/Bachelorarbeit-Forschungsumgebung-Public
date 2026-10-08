import pathlib,subprocess,json,datetime,uuid
root=pathlib.Path(__file__).resolve().parents[1]
logs=root/'docs/pruefungen/m1/logs'/('resolve-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]);logs.mkdir(parents=True,exist_ok=False)
images=json.loads((root/'docker/images.lock.json').read_text())['images']
def run(name,command):
 if command[:2]==['docker','run']:
  command=command[:2]+['--name','research-env-m1-'+name,'--label','org.bachelorarbeit.scope=m1']+command[2:]
 print(name,flush=True)
 with (logs/(name+'.log')).open('w') as f:
  f.write(datetime.datetime.now(datetime.timezone.utc).isoformat()+'\n'+repr(command)+'\n');f.flush()
  result=subprocess.run(command,cwd=root,stdout=f,stderr=subprocess.STDOUT)
 print(name,'exit',result.returncode,flush=True)
 if result.returncode: print((logs/(name+'.log')).read_text()[-12000:],flush=True)
 return result.returncode
r=run('python-lock',['docker','run','--rm','--platform','linux/arm64','-v',str(root)+':/project','-w','/project',images['python']['reference'],'sh','-c','python -m pip install pip-tools==7.6.1 && python -m piptools compile --resolver=backtracking --generate-hashes --strip-extras --output-file=requirements.lock pyproject.toml'])
if r:raise SystemExit(r)
r=run('composer-lock',['docker','run','--rm','--platform','linux/arm64','-v',str(root/'assets/laravel')+':/app','-w','/app','-e','COMPOSER_ALLOW_SUPERUSER=1','-e','COMPOSER_PROCESS_TIMEOUT=0',images['composer']['reference'],'composer','update','--no-interaction','--prefer-dist','--no-install','--no-scripts','--no-plugins'])
if r:raise SystemExit(r)
run('php-upstream-extensions',['docker','run','--rm','--platform','linux/arm64',images['php-cli']['reference'],'php','-m'])

#!/usr/bin/env python3
"""M2 asset boot/build and public development integration, NOT the holdout evaluator."""
import argparse,base64,datetime,hashlib,http.client,http.cookiejar,json,os,secrets,shutil,subprocess,tempfile,time,urllib.request,urllib.error,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PROJECT='research-env-issue4-assets'
class Probe:
 def __init__(self,out,project,port):
  self.out=out;self.project=project;self.port=port;out.mkdir(parents=True,exist_ok=False);self.seq=0;self.records=[]
  self.env=dict(os.environ,M2_APP_KEY='base64:'+base64.b64encode(secrets.token_bytes(32)).decode(),M2_DB_PASSWORD=secrets.token_hex(16),M2_DB_ROOT_PASSWORD=secrets.token_hex(16),M2_ACCESS_TOKEN=secrets.token_hex(16),M2_TEST_PORT=str(port))
  self.compose=['docker','compose','-p',self.project,'-f','compose.study.yaml']
 def command(self,args,*,input=None,check=True):
  self.seq+=1;p=subprocess.run(args,cwd=ROOT,env=self.env,input=input,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
  name=f'{self.seq:03d}.log';(self.out/name).write_text(p.stdout)
  self.records.append(dict(command=args,log=name,exit_code=p.returncode))
  if check and p.returncode:raise RuntimeError(f'Command failed: {args}; {name}')
  return p
 def c(self,args,**kw):return self.command(self.compose+args,**kw)
 def request(self,opener,path,data=None,headers=None):
  req=urllib.request.Request(f'http://127.0.0.1:{self.port}'+path,data=data,headers=headers or {})
  try:
   with opener.open(req) as response:status=response.status;body=response.read();url=response.url
  except urllib.error.HTTPError as e:status=e.code;body=e.read();url=e.url
  self.seq+=1;name=f'{self.seq:03d}-http.html';(self.out/name).write_bytes(body)
  self.records.append(dict(request=dict(path=path,method=req.get_method(),input_sha256=hashlib.sha256(data or b'').hexdigest()),status=status,response=name,response_sha256=hashlib.sha256(body).hexdigest(),url=url))
  return status,body.decode()
 def opener(self):
  jar=http.cookiejar.CookieJar();return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)),jar
 def access(self):
  opener,jar=self.opener();status,body=self.request(opener,'/study-access');assert status==200
  token=re.search(r'name="_token" value="([^"]+)"',body)[1]
  from urllib.parse import urlencode
  status,body=self.request(opener,'/study-access',urlencode(dict(_token=token,access_token=self.env['M2_ACCESS_TOKEN'])).encode())
  assert status==200 and 'Session angemeldet' in body
  return opener,jar
 def db(self):
  return self.c(['exec','-T','mysql','sh','-c','exec mysql -u study -p"$MYSQL_PASSWORD" --batch --skip-column-names study'],input='SHOW TABLES; SELECT * FROM users ORDER BY user_id; SHOW CREATE TABLE users;').stdout
 def overlay(self,path=None):
  # Preserve previous process logs, then use a fresh own PHP container.
  # No root reset/extra capabilities; PHP always remains image USER www-data.
  self.c(['logs','--no-color','study'])
  self.c(['rm','-s','-f','study'])
  self.c(['create','--no-build','study'])
  if path is not None: self.c(['cp',str(path)+'/.' ,'study:/opt/study/'])
  self.c(['start','study'])
  self.wait_ready()
 def wait_ready(self):
  # Test deployment readiness only, never a candidate score or request retry.
  for readiness_attempt in range(1,91):
   try:
    opener,jar=self.opener();status,body=self.request(opener,'/study-access')
    if status==200:return
   except (urllib.error.URLError,http.client.RemoteDisconnected,ConnectionResetError) as error:
    self.records.append(dict(readiness_probe=readiness_attempt,status='technical_wait',error_type=type(error).__name__,error=str(error),at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),scope='GET access readiness only; no candidate/measurement/generation retry'))
   time.sleep(1)
  raise RuntimeError('Test deployment not ready; technical prerequisite failure')
 def run(self):
  # No clobber of an existing project/volume: reserve a brand new project.
  ps=self.command(['docker','ps','-a','--filter',f'label=com.docker.compose.project={self.project}','--format','{{.ID}}']).stdout
  vols=self.command(['docker','volume','ls','--filter',f'label=com.docker.compose.project={self.project}','--format','{{.Name}}']).stdout
  if ps.strip() or vols.strip():raise RuntimeError('Existing issue4 project/volume; choose a new explicit project before rerunning, no reset/destruction')
  self.command(['docker','version','--format','{{json .}}']);self.command(['docker','compose','version']);self.command(['docker','image','inspect','research-env-study:m2-v0.1']);self.command(['uname','-a'])
  self.c(['up','-d','--no-build'])
  self.wait_ready()
  self.c(['exec','-T','study','sh','-c','php -v; composer --version; php artisan --version; php -m; id'])
  # Laravel connection session vs server: no candidate assertion used as evidence.
  php=r'''$app = require 'bootstrap/app.php'; $app->make(\Illuminate\Contracts\Console\Kernel::class)->bootstrap(); echo json_encode(\Illuminate\Support\Facades\DB::select('SELECT @@GLOBAL.sql_mode AS server_mode, @@SESSION.sql_mode AS session_mode, @@GLOBAL.collation_server AS server_collation, @@SESSION.collation_connection AS session_collation, @@SESSION.character_set_connection AS session_charset, @@GLOBAL.time_zone AS server_timezone, @@SESSION.time_zone AS session_timezone'), JSON_PRETTY_PRINT); echo "\n"; echo \Illuminate\Support\Facades\DB::select('SHOW CREATE TABLE users')[0]->{'Create Table'};'''
  # php -r needs autoload when not artisan.
  self.c(['exec','-T','study','php','-r',"require 'vendor/autoload.php'; "+php])
  before=self.db()
  unauth,_=self.opener();status,b=self.request(unauth,'/');assert status==200 and 'Zugangstoken' in b
  from urllib.parse import urlencode
  status,b=self.request(unauth,'/study-access');assert status==200
  login_csrf=re.search(r'name="_token" value="([^"]+)"',b)[1]
  status,b=self.request(unauth,'/study-access',urlencode(dict(access_token=self.env['M2_ACCESS_TOKEN'])).encode());assert status==419
  status,b=self.request(unauth,'/study-access',urlencode(dict(_token='invalid-csrf',access_token=self.env['M2_ACCESS_TOKEN'])).encode());assert status==419
  status,b=self.request(unauth,'/study-access',urlencode(dict(_token=login_csrf,access_token='wrong-access')).encode());assert status==403
  op,jar=self.access();status,b=self.request(op,'/study/brute');assert status==404
  base_db=self.db();assert before==base_db
  # All reference overlays boot in isolated build-only containers without suite mounts.
  manifest=json.loads((ROOT/'evaluation/study_holdout/m2-v0.1/implementations/manifest.json').read_text())
  build_results=[]
  for ref in manifest['references']:
   overlay=ROOT/'evaluation/study_holdout/m2-v0.1/implementations'/ref['overlay']
   p=self.command(['docker','run','--rm','--network','none','--cap-drop','ALL','--security-opt','no-new-privileges:true','-v',str(overlay)+':/input:ro','--entrypoint','sh','research-env-study:m2-v0.1','-c','set -e; cp -R /opt/study /tmp/candidate; cp -R /input/. /tmp/candidate/; cd /tmp/candidate; find app routes -name "*.php" -print0 | xargs -0 -n1 php -l; php artisan route:list --path=study --json'],check=False)
   expected=not ref['deliberate_syntax_failure'];assert (p.returncode==0)==expected,(ref['id'],p.stdout)
   build_results.append(dict(id=ref['id'],expected_boot=expected,exit_code=p.returncode,matched=True,overlay_hash=ref['tree_sha256']))
  # Identical outer requests on all six valid private structures, with public dev data.
  # This is a narrow integration smoke, not all holdout R/T judgments.
  from urllib.parse import urlencode
  integrations=[]
  for variant in ['A','B']:
   for mod,path in [('BF','brute'),('SQL','sqli'),('UP','upload')]:
    ident=f'GOOD-{variant}-{mod}';self.overlay(ROOT/'evaluation/study_holdout/m2-v0.1/implementations'/ident)
    op,jar=self.access();start_session=[c.value for c in jar if c.name=='m2_study_session']
    before=self.db();status,b=self.request(op,'/study/'+path);assert status==200 and 'data-study-result' in b and 'data-study-status' not in b
    assert self.db()==before
    if mod=='BF':
     requests=[dict(username='cedar41',password='CedarPass9',Login=''),dict(username='cedar41',password='Wrong3',Login=''),dict(username='fern62',password='FernPass8',Login='')]
     for params,expected in zip(requests,['SUCCESS','NEGATIVE','SUCCESS']):
      status,b=self.request(op,'/study/'+path+'?'+urlencode(params));assert status==200 and f'>{expected}</span>' in b
      if expected=='SUCCESS':assert f'src="/dev-images/{params["username"]}.png"' in b
      assert self.db()==before
     status,b=self.request(op,'/study/'+path+'?Login=&username=cedar41');assert status==422 and '>INPUT_ERROR</span>' in b;assert self.db()==before
     # Encrypted cookie changes on every response; compare actual server auth/session ID separately in future #7.
    elif mod=='SQL':
     for id_,expected,names in [('211','SUCCESS',['Cedar','Brook']),('999998','NEGATIVE',[]),('734','SUCCESS',['Fern','Vale'])]:
      status,b=self.request(op,'/study/'+path+'?'+urlencode(dict(id=id_,Submit='')));assert status==200 and f'>{expected}</span>' in b
      for name in names:assert f'>{name}</span>' in b
      assert self.db()==before
     status,b=self.request(op,'/study/'+path+'?Submit=&id=');assert status==422 and '>INPUT_ERROR</span>' in b;assert self.db()==before
    else:
     token=re.search(r'name="_token" value="([^"]+)"',b)[1]
     status,b=self.request(op,'/study/upload',urlencode(dict(_token=token,Upload='')).encode());assert status==422 and '>INPUT_ERROR</span>' in b;assert self.db()==before
     blob=(ROOT/'evaluation/development/m2-v0.1/dev-pixel.png').read_bytes();filename=f'dev-{variant}.png';boundary='DevelopmentFixtureBoundary'
     data=(f'--{boundary}\r\nContent-Disposition: form-data; name="_token"\r\n\r\n{token}\r\n--{boundary}\r\nContent-Disposition: form-data; name="Upload"\r\n\r\n\r\n--{boundary}\r\nContent-Disposition: form-data; name="uploaded"; filename="{filename}"\r\nContent-Type: image/png\r\n\r\n').encode()+blob+f'\r\n--{boundary}--\r\n'.encode()
     status,b=self.request(op,'/study/upload',data,{'Content-Type':'multipart/form-data; boundary='+boundary});assert status==200 and '>SUCCESS</span>' in b and f'>study/uploads/{filename}</span>' in b
     digest=self.c(['exec','-T','study','sha256sum','storage/app/study/uploads/'+filename]).stdout.split()[0];assert digest==hashlib.sha256(blob).hexdigest();assert self.db()==before
    integrations.append(dict(id=ident,status='pass',scope='public development interface smoke only; human T reviews and holdout classification open'))
  # Restore bare scaffold for independent browser access demonstration.
  self.overlay();self.c(['exec','-T','study','php','artisan','view:clear'])
  self.c(['logs','--no-color']);self.c(['ps','--format','json'])
  secret_file=Path(tempfile.mkdtemp(prefix='m2-browser-'))/'access-token.txt';secret_file.write_text(self.env['M2_ACCESS_TOKEN']);secret_file.chmod(0o600)
  return dict(reference_builds=build_results,development_integrations=integrations,project=self.project,port=self.port,browser_token_file=str(secret_file),runtime_status='running bare scaffold for Root browser probe',holdout_validation='not executed',sandbox_reset_and_upload_permission_classification='not executed; issues 7/10',human_review='open')
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('--output',type=Path,required=True);a.add_argument('--project',default=PROJECT);a.add_argument('--port',type=int,default=8004);args=a.parse_args();probe=Probe(args.output,args.project,args.port)
 try:
  result=probe.run();status='pass'
 except Exception as e:
  result=dict(error=str(e),project=probe.project);status='failed';raise
 finally:
  (probe.out/'results.json').write_text(json.dumps(dict(status=status,result=result,commands=probe.records,verifier_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),asset_manifest_sha256=hashlib.sha256((ROOT/'assets/study/m2-v0.1/manifest.json').read_bytes()).hexdigest() if (ROOT/'assets/study/m2-v0.1/manifest.json').exists() else None),indent=2)+'\n')

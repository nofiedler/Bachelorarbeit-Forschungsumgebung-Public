#!/usr/bin/env python3
"""Static source/asset integrity and information boundary proofs, not runtime isolation."""
import argparse,hashlib,importlib.util,json,re,shutil,subprocess,tempfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def sha(b):return hashlib.sha256(b).hexdigest()
def load_script(path):
 spec=importlib.util.spec_from_file_location('context_builder',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def verify(root,dvwa):
 builder=load_script(root/'scripts/build-context-assets.py');objects=builder.validate(dvwa)
 study=root/'assets/study/m2-v0.1';context=root/'assets/context/m2-v0.1';scaffold=study/'scaffold'
 m=json.loads((study/'manifest.json').read_text());assert m['dependencies']['M1_locks_unchanged']
 actual={str(p.relative_to(scaffold)) for p in scaffold.rglob('*') if p.is_file()}
 assert actual==set(m['files'])
 for rel,e in m['files'].items():assert sha((scaffold/rel).read_bytes())==e['sha256']
 assert not (scaffold/'app/Http/Controllers/Study').exists() and not (scaffold/'app/Study').exists()
 assert (scaffold/'database/users.sql').read_bytes()==(root/'docs/vertraege/m2-v0.1/users.sql').read_bytes()
 assert (scaffold/'composer.json').read_bytes()==(root/'assets/laravel/composer.json').read_bytes()
 assert (scaffold/'composer.lock').read_bytes()==(root/'assets/laravel/composer.lock').read_bytes()
 counts=[]
 for mod,legacy in builder.MODULES.items():
  common=[]
  for condition in ['K0','K1']:
   base=context/mod/condition;meta=json.loads((base/'manifest.json').read_text());payload=(base/'package.txt').read_bytes()
   assert sha(payload)==meta['package_sha256'] and len(payload)==meta['bytes'] and len(payload.decode())==meta['characters'] and meta['tokens'] is None
   for e in meta['files']:
    b=(base/e['path']).read_bytes();assert sha(b)==e['sha256'] and len(b)==e['bytes'] and len(b.decode())==e['characters']
   for name in ['README.md','vertrag.md','rubrik.md','users.sql']:assert (base/'contract'/name).read_bytes()==(root/'docs/vertraege/m2-v0.1'/name).read_bytes()
   assert (base/'legacy/low.php').read_bytes()==objects[f'vulnerabilities/{legacy}/source/low.php']
   assert (base/'LICENSE-DVWA.txt').read_bytes()==objects['COPYING.txt']
   if condition=='K0':common=meta['files'];assert b'K1-SOURCE-' not in payload and b'K1-MAPPING-' not in payload and b'dvwaPage.inc.php:' not in payload
   else:
    for e in common:assert (context/mod/'K0'/e['path']).read_bytes()==(base/e['path']).read_bytes()
    expected=builder.excerpts(f'vulnerabilities/{legacy}/index.php',objects[f'vulnerabilities/{legacy}/index.php'],builder.INDEX_RANGES[mod])+builder.excerpts(builder.PAGE,objects[builder.PAGE],builder.PAGE_RANGES+builder.DB_RANGES)
    assert (base/'integration/excerpts.md').read_text()==f'<!-- K1-SOURCE-{mod}-M2-v0.1 -->\n'+expected
   counts.append(dict(module=mod,condition=condition,bytes=meta['bytes'],characters=meta['characters'],tokens=None))
 hold=root/'evaluation/study_holdout/m2-v0.1';ref=json.loads((hold/'implementations/manifest.json').read_text());catalog=json.loads((hold/'references.json').read_text())
 assert len(ref['references'])==42 and ref['source_catalog_sha256']==sha((hold/'references.json').read_bytes())
 for original,real in zip(catalog['references'],ref['references']):
  assert original['id']==real['id'] and original['expected']==real['expected']
  base=hold/'implementations'/real['overlay'];assert {str(p.relative_to(base)) for p in base.rglob('*') if p.is_file()}==set(real['files'])
  for rel,digest in real['files'].items():assert sha((base/rel).read_bytes())==digest
 # Private synthetic markers/credentials/fixture file names absent from ALL role-readable surfaces.
 fixtures=json.loads((hold/'fixtures.json').read_text())
 # Determine marker strings directly from protected fixture values without writing them to public results.
 def strings(obj):
  if isinstance(obj,dict):return [x for v in obj.values() for x in strings(v)]
  if isinstance(obj,list):return [x for v in obj for x in strings(v)]
  return [obj] if isinstance(obj,str) else []
 sensitive=sorted(set([str(row[key]) for rows in fixtures['datasets'].values() for row in rows for key in ['first_name','last_name','user','password','avatar']]+[value for dataset in fixtures['credentials'].values() for value in dataset.values()]+fixtures['unknown_users']+[fixtures['wrong_password']]+list(fixtures['files'])+[record['sha256'] for record in fixtures['files'].values()]))
 revision_result=load_script(root/'scripts/verify-csrf-contract-revision.py').verify(root)
 public=[p for base in (context,root/'assets/context/m2-v0.1-csrf1') for p in base.rglob('*') if p.is_file() and p.name not in ['information-manifest.json']]+[p for p in scaffold.rglob('*') if p.is_file()]+[study/'interfaces.md',study/'tools.json']
 for p in public:
  data=p.read_bytes();assert not p.is_symlink() and p.stat().st_nlink==1
  for marker in sensitive+['PRIVATE-REFERENCES-M2-v0.1','PRIVATE-STUDY-HOLDOUT-M2-v0.1','GOOD-A-BF','BAD-BF-T4']:
   assert not re.search(r'(?<!\w)'+re.escape(marker)+r'(?!\w)',data.decode(errors='replace')),(p,'private marker')
  if p.is_relative_to(scaffold) or p.name in ['interfaces.md','tools.json']:
   assert b'K1-SOURCE-' not in data and b'K1-MAPPING-' not in data and b'dvwaPage.inc.php' not in data
 dev=root/'evaluation/development/m2-v0.1';dm=json.loads((dev/'manifest.json').read_text());assert dm['suite_kind']=='development'
 for rel,e in dm['files'].items():assert sha((dev/rel).read_bytes())==e['sha256']
 assert (dev/'dev-pixel.png').read_bytes() not in [(p.read_bytes()) for p in (hold/'fixtures').iterdir() if p.is_file()]
 assert {u['user'] for u in json.loads((dev/'fixtures.json').read_text())['users']}.isdisjoint(set(sensitive))
 mounts=json.loads((study/'mounts.json').read_text());assert mounts['profiles']['development_test']['suite_kind']=='development'
 assert all('study_holdout' not in p for p in mounts['profiles']['development_test']['read'])
 # Actual working-copy mutation proof in independent trusted source clone; original untouched.
 before=subprocess.check_output(['git','-C',str(dvwa),'status','--porcelain']).decode()
 with tempfile.TemporaryDirectory(prefix='m2-source-negative-') as temp:
  copy=Path(temp)/'dvwa';subprocess.run(['git','clone','--quiet','--no-hardlinks',str(dvwa),str(copy)],check=True)
  target=copy/'vulnerabilities/brute/source/low.php';target.write_bytes(target.read_bytes()+b'\n// unexpected test mutation\n')
  try:builder.validate(copy)
  except ValueError as e:negative=dict(status='pass',actual=str(e),original_preserved=True)
  else:raise AssertionError('Changed source accepted')
 assert before==subprocess.check_output(['git','-C',str(dvwa),'status','--porcelain']).decode()
 return dict(csrf_revision=revision_result,status='pass',scope='static integrity and information boundaries only',packages=counts,references=42,reference_expected_unchanged=True,source_negative=negative,checked_public_surfaces=len(public),private_marker_probe_count=len(sensitive)+4,dependency_locks_identical=True,development_holdout_mount_lists_separate=True,model_tokenizer='open',human_review='open',runtime_leakage_and_reference_classification='not tested; issues 7/10',manifest_sha256=sha((study/'manifest.json').read_bytes()),checker_sha256=sha(Path(__file__).read_bytes()),python=sys.version,platform=sys.platform)
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('--dvwa',type=Path,required=True);a.add_argument('--output',type=Path);args=a.parse_args();result=verify(ROOT,args.dvwa)
 if args.output:
  args.output.parent.mkdir(parents=True,exist_ok=True)
  with args.output.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
 print(json.dumps(result,indent=2))

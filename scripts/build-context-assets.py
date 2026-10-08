#!/usr/bin/env python3
"""Controlled Git-object packaging; does not import private evaluation assets."""
import argparse, hashlib, json, subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
COMMIT='b496a5d3de6b967410155e1b7d3e51e9d035eb22'
MODULES={'BF':'brute','SQL':'sqli','UP':'upload'}
PAGE='dvwa/includes/dvwaPage.inc.php'
# Selection rule fixed from public contract and original source, before execution.
PAGE_RANGES=[(1,18),(102,105),(108,114),(124,155),(179,190),(281,281),(389,394),(425,431)]
INDEX_RANGES={'BF':[(1,10),(13,15),(18,19),(33,48),(53,56),(65,65)],'SQL':[(1,10),(14,16),(19,20),(34,40),(45,48),(56,59),(64,65),(67,69),(80,80)],'UP':[(1,10),(14,14),(18,19),(32,32),(44,46),(50,56),(61,64),(73,73)]}
DB_RANGES=[(562,575)] # MySQL init only: PDO-impossible and SQLite omitted.
def sha(b):return hashlib.sha256(b).hexdigest()
def dump(p,o):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(o,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
def git(source,*args):return subprocess.check_output(['git','-C',str(source),*args])
def validate(source):
 if git(source,'rev-parse','HEAD').decode().strip()!=COMMIT:raise ValueError('DVWA HEAD differs from fixed source commit')
 if git(source,'diff','--name-only',COMMIT).strip() or git(source,'diff','--cached','--name-only').strip():raise ValueError('Unexpected tracked DVWA working-copy change')
 files=['COPYING.txt',PAGE,'dvwa/includes/DBMS/MySQL.php']+[f'vulnerabilities/{m}/{f}' for m in MODULES.values() for f in ['index.php','source/low.php']]
 objects={p:git(source,'show',f'{COMMIT}:{p}') for p in files}
 for p,b in objects.items():
  if not (source/p).is_file() or (source/p).read_bytes()!=b:raise ValueError(f'Working-copy bytes differ: {p}')
 return objects

def excerpts(path,b,ranges):
 lines=b.decode().splitlines()
 return '\n'.join(f'### {path}:{a}–{z}\n```php\n'+ '\n'.join(f'{i}: {lines[i-1]}' for i in range(a,z+1))+'\n```' for a,z in ranges)+'\n'

def build(source):
 objects=validate(source); dest=ROOT/'assets/context/m2-v0.1';dest.mkdir(parents=True,exist_ok=True)
 common={p.name:p.read_bytes() for p in (ROOT/'docs/vertraege/m2-v0.1').iterdir() if p.is_file()}
 assert set(common)=={'README.md','vertrag.md','rubrik.md','users.sql'}
 interfaces=(ROOT/'assets/study/m2-v0.1/interfaces.md').read_bytes()
 tools=(ROOT/'assets/study/m2-v0.1/tools.json').read_bytes()
 comparisons=[]
 for mod,legacy in MODULES.items():
  low=f'vulnerabilities/{legacy}/source/low.php'; idx=f'vulnerabilities/{legacy}/index.php'
  mapping={
   'BF':f'Formularfelder/GET ({idx}:42–48) → low.php:3–9/12–28 (MD5 und DB-Prüfung) → Avatar aus DB (low.php:15–22) → $html in Modulbody ({idx}:55), weiter Seitenausgabe ({PAGE}:425–431). Vorgeschalteter Zugang ({idx}:6; {PAGE}:124–155) ist von der Modulprüfung getrennt; MySQL-Verbindung ({idx}:13; {PAGE}:562–575).',
   'SQL':f'Formular id/Submit ({idx}:46–59) → low.php:3–8 → MySQL-Abfrage low.php:10–26 → first_name/last_name in $html (low.php:15–22) → Modulbody ({idx}:68), Seitenausgabe ({PAGE}:425–431). Verbindung wird vorher hergestellt ({idx}:14; {PAGE}:562–575). Das MySQL-Ziel ist bereits K0 bekannt.',
   'UP':f'Multipart/form und uploaded/Upload ({idx}:51–56) → low.php:3–6 → DVWA_WEB_PAGE_TO_ROOT ({idx}:3) plus hackable/uploads und Originalbasename (low.php:5–6) → move_uploaded_file (low.php:9) → $html (low.php:11–15), Modulbody ({idx}:63), Seitenausgabe ({PAGE}:425–431). Sessionzugang wird vorher geprüft ({idx}:6; {PAGE}:124–155); DB-Aufruf ({idx}:14) ist vorgeschalteter Seitenaufbau, keine Upload-Datenverarbeitung.'}[mod]
  for condition in ['K0','K1']:
   p=dest/mod/condition;p.mkdir(parents=True,exist_ok=True)
   contents={f'contract/{k}':b for k,b in common.items()}
   contents.update({'interfaces.md':interfaces,'tools.json':tools,'legacy/low.php':objects[low],'LICENSE-DVWA.txt':objects['COPYING.txt']})
   if mod=='SQL':contents['mysql-target.md']='Ziel ist ausschließlich der MySQL-Zweig der vollständigen low.php, Zeilen 8–26. SQLite wird nicht migriert.\n'.encode()
   if condition=='K1':
    contents['integration/excerpts.md']=(f'<!-- K1-SOURCE-{mod}-M2-v0.1 -->\n'+excerpts(idx,objects[idx],INDEX_RANGES[mod])+excerpts(PAGE,objects[PAGE],PAGE_RANGES+DB_RANGES)).encode()
    contents['integration/mapping.md']=(f'<!-- K1-MAPPING-{mod}-M2-v0.1 -->\n# Zuordnung am Originalcode\n\n{mapping}\n\nEigene explizitere Aufbereitung, kein zusätzliches Sollwissen. Technische Agentenquellenprüfung erfolgt; menschliche Prüfung offen.\n').encode()
   entries=[]
   for rel,b in sorted(contents.items()):
    f=p/rel;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(b)
    entries.append(dict(path=rel,sha256=sha(b),bytes=len(b),characters=len(b.decode()),category='zusätzlicher Quellbeleg' if rel.endswith('excerpts.md') else 'explizitere Aufbereitung' if rel.endswith('mapping.md') else 'gemeinsam/bereits K0'))
   # Canonical concatenation is exact complete model input; boundaries retained.
   payload=b''.join(f'\n===== {e["path"]} =====\n'.encode()+(p/e['path']).read_bytes() for e in entries)
   (p/'package.txt').write_bytes(payload)
   dump(p/'manifest.json',dict(asset_version='M2-CONTEXT-v0.1',schema_version=1,type='source_context',access_scope='only assigned module/condition role input',module=mod,condition=condition,source_commit=COMMIT,files=entries,package_sha256=sha(payload),bytes=len(payload),characters=len(payload.decode()),tokens=None,token_status='unbestimmt: Modell A/Tokenizer nicht gewählt; keine Schätzung vorgenommen',provider_context_and_defaults='open',human_review='open',model_input='package.txt',follow_links=False))
  a=json.loads((dest/mod/'K0/manifest.json').read_text());b=json.loads((dest/mod/'K1/manifest.json').read_text())
  comparisons.append(dict(module=mod,common_files_identical=all((dest/mod/'K0'/x['path']).read_bytes()==(dest/mod/'K1'/x['path']).read_bytes() for x in a['files']),additional_bytes=b['bytes']-a['bytes'],additional_characters=b['characters']-a['characters'],additional_tokens=None,mapping_sources=[dict(path=low,lines='whole'),dict(path=idx,lines=INDEX_RANGES[mod]),dict(path=PAGE,lines=PAGE_RANGES+DB_RANGES)]))
 # provenance is outside role packages and records omitted line ranges exactly.
 selection=[]
 for mod,m in MODULES.items():
  idx=f'vulnerabilities/{m}/index.php';ranges=INDEX_RANGES[mod]
  included={i for a,z in ranges for i in range(a,z+1)}
  selection.append(dict(module=mod,path=idx,included=ranges,excluded_lines=[i for i in range(1,len(objects[idx].splitlines())+1) if i not in included],reason='Include only low wiring, form, result and startup calls; omit other levels, level selection, help, layout and warnings irrelevant to low processing. Discontinuous numbered excerpts are evidence, not executable PHP.'))
 dump(dest/'information-manifest.json',dict(asset_version='M2-CONTEXT-v0.1',source=dict(repository='https://github.com/digininja/DVWA',commit=COMMIT,license='GPL-3.0; COPYING.txt copied unchanged',objects={p:dict(sha256=sha(b),bytes=len(b)) for p,b in objects.items()},untracked_excluded=git(source,'ls-files','--others','--exclude-standard').decode().splitlines()),selection_rule='Fixed semantic selection from public contract and source before reference execution; same inclusion rule for all modules; no holdout optimization',index_selection=selection,page_selection=dict(path=PAGE,included=PAGE_RANGES+DB_RANGES,excluded_lines=[i for i in range(1,len(objects[PAGE].splitlines())+1) if not any(a<=i<=z for a,z in PAGE_RANGES+DB_RANGES)],reason='Only config/$html, low-session branch, access/page initialization, relevant echo and MySQL global connection. Omit high/impossible security behavior, menus/help/source, alternative DB and unrelated modules.'),comparisons=comparisons,scaffold_and_tool_matrix=[dict(surface='scaffold',origin='M1 skeleton plus public M2 interfaces',category='gemeinsam/bereits K0',allowed_for=['K0','K1'],legacy_integration=False),dict(surface='tools.json',origin='fixed development commands on own scaffold/candidate',category='gemeinsam/bereits K0',allowed_for=['K0','K1'],legacy_integration=False),dict(surface='integration/*',origin='selected original source and cited mapping',category='K1 additional evidence/explicit mapping',allowed_for=['K1'])],human_holdout_rule='No protected cases/results/reference code may guide models, prompts, packages or model choice; technical reference building uses protected catalog only for instrument preparation.',human_review='open'))
 return comparisons
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('--dvwa',required=True,type=Path);args=a.parse_args();print(json.dumps(build(args.dvwa),indent=2))

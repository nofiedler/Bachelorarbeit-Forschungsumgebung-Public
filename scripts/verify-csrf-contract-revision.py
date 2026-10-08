#!/usr/bin/env python3
"""Verify original plus additive interpretation and exact public package payloads."""
import hashlib
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
REVISION='m2-v0.1-csrf1'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def verify(root=ROOT):
    public=root/'docs/vertraege'/REVISION
    manifest=json.loads((public/'manifest.json').read_text())
    assert manifest['version']=='M2-v0.1-CSRF1' and manifest['criteria_requestcases_fixtures_references_unchanged'] is True
    for rel,h in manifest['original_files'].items():assert sha(root/rel)==h
    addendum=root/manifest['addendum']['path'];assert sha(addendum)==manifest['addendum']['sha256']
    h=sha(public/'manifest.json')
    binding=json.loads((root/'src/research_env/contract_assets.lock.json').read_text())
    assert binding['manifest_sha256']==h
    assert binding['files']==dict(manifest['original_files'],**{str((public/'manifest.json').relative_to(root)):h,manifest['addendum']['path']:sha(addendum)})
    index=json.loads((root/'assets/study'/REVISION/'asset-index.json').read_text())
    assert index['contract']['manifest_sha256']==h
    assert sha(root/index['original_asset_index']['path'])==index['original_asset_index']['sha256']
    pipeline=json.loads((root/'src/research_env/pipeline_assets.lock.json').read_text())
    sandbox=json.loads((root/'src/research_env/sandbox_assets.lock.json').read_text())
    packages=[]
    for item in index['contexts']:
        base=root/item['artifact_root'];old=root/'assets/context/m2-v0.1'/base.parent.name/base.name
        meta=json.loads((base/'manifest.json').read_text());original=json.loads((old/'manifest.json').read_text())
        assert sha(base/'manifest.json')==item['manifest_sha256']==pipeline[item['manifest']]
        assert sha(old/'manifest.json')==meta['original_manifest_sha256']==item['original_manifest_sha256']
        assert meta['contract_manifest_sha256']==item['contract_manifest_sha256']==h
        assert {e['path'] for e in meta['files']}=={e['path'] for e in original['files']}|{'contract/00-csrf-revision.md'}
        assert {str(p.relative_to(base)) for p in base.rglob('*') if p.is_file()}=={e['path'] for e in meta['files']}|{'manifest.json','package.txt'}
        for e in original['files']:assert (old/e['path']).read_bytes()==(base/e['path']).read_bytes()
        assert (base/'contract/00-csrf-revision.md').read_bytes()==addendum.read_bytes()
        for e in meta['files']:
            data=(base/e['path']).read_bytes();assert sha(base/e['path'])==e['sha256'] and len(data)==e['bytes'] and len(data.decode())==e['characters']
        payload=b''.join(f'\n===== {e["path"]} =====\n'.encode()+(base/e['path']).read_bytes() for e in meta['files'])
        assert (base/'package.txt').read_bytes()==payload
        assert sha(base/'package.txt')==meta['package_sha256']==sandbox[str((base/'package.txt').relative_to(root))]['sha256']
        assert len(payload)==meta['bytes'] and len(payload.decode())==meta['characters'] and meta['tokens'] is None
        packages.append({'module':meta['module'],'condition':meta['condition'],'manifest_sha256':sha(base/'manifest.json'),'package_sha256':sha(base/'package.txt')})
    assert {(p['module'],p['condition']) for p in packages}=={(m,k) for m in ('BF','SQL','UP') for k in ('K0','K1')}
    return {'status':'pass','contract_version':manifest['version'],'contract_manifest_sha256':h,'original_and_addendum_bound':True,'packages':packages,'scope':'Static integrity; no native execution, pilot or study approval'}
if __name__=='__main__':print(json.dumps(verify(),indent=2))

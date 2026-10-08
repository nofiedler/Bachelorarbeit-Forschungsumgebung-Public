#!/usr/bin/env python3
"""Additive public CSRF interpretation; never rewrite the original M2 assets."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVISION = 'm2-v0.1-csrf1'

def sha(data): return hashlib.sha256(data).hexdigest()

def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != data:
        raise ValueError('Version already exists with different bytes: ' + str(path))
    if not path.exists(): path.write_bytes(data)

def encoded(obj): return (json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)+'\n').encode()

def build(root=ROOT):
    original = root/'docs/vertraege/m2-v0.1'
    revision = root/'docs/vertraege'/REVISION
    addendum = (revision/'addendum.md').read_bytes()
    public = {str((original/name).relative_to(root)): sha((original/name).read_bytes())
              for name in ('README.md','vertrag.md','rubrik.md','users.sql')}
    manifest = {'version':'M2-v0.1-CSRF1','type':'contract','original_version':'M2-v0.1',
                'rubric_version':'RT-v0.1','original_files':public,
                'addendum':{'path':str((revision/'addendum.md').relative_to(root)),'sha256':sha(addendum)},
                'decision':{'date':'2026-10-05','reply':'ja','scope':'CSRF token origin only; no pilot/study approval'},
                'criteria_requestcases_fixtures_references_unchanged':True,'remaining_human_review':'open'}
    manifest_data=encoded(manifest);save(revision/'manifest.json',manifest_data)
    entries=[]
    for mod in ('BF','SQL','UP'):
        for condition in ('K0','K1'):
            old=root/'assets/context/m2-v0.1'/mod/condition
            new=root/'assets/context'/REVISION/mod/condition
            meta=json.loads((old/'manifest.json').read_text())
            contents={e['path']:(old/e['path']).read_bytes() for e in meta['files']}
            contents['contract/00-csrf-revision.md']=addendum
            files=[]
            for rel,data in sorted(contents.items()):
                save(new/rel,data)
                files.append({'path':rel,'sha256':sha(data),'bytes':len(data),'characters':len(data.decode()),
                              'category':'gemeinsam/bereits K0' if rel=='contract/00-csrf-revision.md' else next(e['category'] for e in meta['files'] if e['path']==rel)})
            payload=b''.join(f'\n===== {e["path"]} =====\n'.encode()+contents[e['path']] for e in files)
            save(new/'package.txt',payload)
            meta.update(asset_version='M2-CONTEXT-v0.1-CSRF1',files=files,package_sha256=sha(payload),bytes=len(payload),characters=len(payload.decode()),
                        contract_manifest_sha256=sha(manifest_data),original_manifest_sha256=sha((old/'manifest.json').read_bytes()))
            data=encoded(meta);save(new/'manifest.json',data)
            entries.append({'id':f'context-{mod}-{condition}','type':'source_context','manifest':str((new/'manifest.json').relative_to(root)),
                            'manifest_sha256':sha(data),'artifact_root':str(new.relative_to(root)),'access_scope':'assigned module/condition package.txt only',
                            'contract_manifest_sha256':sha(manifest_data),'original_manifest_sha256':meta['original_manifest_sha256']})
    index={'asset_version':'M2-ASSET-INDEX-v0.1-CSRF1','original_asset_index':{'path':'assets/study/m2-v0.1/asset-index.json','sha256':sha((root/'assets/study/m2-v0.1/asset-index.json').read_bytes())},
           'contract':{'manifest':str((revision/'manifest.json').relative_to(root)),'manifest_sha256':sha(manifest_data)},'contexts':entries,
           'scaffold_suite_reference_assets':'Unchanged original asset index entries','scope':'trusted register metadata; no parent role mount'}
    save(root/'assets/study'/REVISION/'asset-index.json',encoded(index))
    return index

if __name__=='__main__':print(json.dumps(build(),indent=2))

"""Real #4 scaffold/vendor reconstruction probe, no PHP/model execution."""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import stat
import sys
from uuid import uuid4

sys.path.insert(0, '/app/src')
sys.path.insert(0, '/app/tests')
import test_register as fixtures
from research_env.artifacts import ArtifactStore
from research_env.config import Settings
from research_env.database import migrate
from research_env.domain import digest
from research_env.register import Register
from research_env.snapshots import ProcessWriters, Snapshots, inventory

result_dir = Path('/results')
result_dir.mkdir(exist_ok=True)
work = result_dir / 'real-laravel-reconstruction'
work.mkdir()
settings = Settings(*(work / 'instance' / x for x in ('control','artifacts','checkpoints','staging')))
for p in (settings.control,settings.artifacts,settings.checkpoints,settings.staging):
    p.mkdir(parents=True)
migrate(settings)
r = Register(settings)
store = ArtifactStore(settings,r)
def artifact(register, run=None, **kwargs):
    return store.store(b'TECHNICAL-FIXTURE: raw control',run_id=run.id if run else None,**kwargs)
fixtures.artifact=artifact
original_asset=fixtures.asset
def asset(register, kind='tool', suite=None, **kwargs):
    if kind != 'scaffold':
        return original_asset(register,kind,suite,**kwargs)
    from research_env.domain import AssetVersion
    raw=Path('/source-tools/manifest.json').read_bytes()
    saved=store.store(raw,artifact_type='scaffold_manifest',mime_type='application/json')
    return register.add(AssetVersion(code='ACTUAL-M2-SCAFFOLD-v0.1',asset_type='scaffold',
        manifest_hash=hashlib.sha256(raw).hexdigest(),artifact_ids=(saved.id,),origin='Actual #4 shared scaffold; human subject review still open',access_scope='shared_scaffold'))
fixtures.asset=asset
f=fixtures.setup_study(r)
freeze=r.freeze(fixtures.freeze_value(r,f))
# Real content reconstruction is a candidate-store probe. Start fixture uses
# explicitly synthetic #5 metadata, not a claim of external hardware.
fixtures.backup(r,freeze,f['basic'])
run,_=fixtures.start(r,freeze,f)
source=Path('/source-scaffold')
vendor=Path('/source-vendor')
# Independent oracle: the pre-existing #4 asset manifest and Root's exported
# vendor manifest, not the snapshot module's exclusion/hash implementation.
scaffold_manifest=json.loads(Path('/source-tools/manifest.json').read_text())
raw_vendor_manifest=Path('/app/probe-inputs/vendor-root-manifest.json').read_bytes()
assert hashlib.sha256(raw_vendor_manifest).hexdigest()=='844c880cef69d00c52af88b1f99fc84a0e146c8ce861ee07e65bd343b6ba6754'
root_vendor_manifest=json.loads(raw_vendor_manifest)
source_before=[]
for name, entry in sorted(scaffold_manifest['files'].items()):
    path=source/name
    info=path.lstat()
    assert stat.S_ISREG(info.st_mode)
    data=path.read_bytes()
    assert len(data)==entry['bytes'] and hashlib.sha256(data).hexdigest()==entry['sha256']
    source_before.append({'path':name,'mode':stat.S_IMODE(info.st_mode),'size':len(data),'sha256':entry['sha256']})
assert len(source_before)==49
actual_source_names=sorted(path.relative_to(source).as_posix() for path in source.rglob('*') if path.is_file())
assert actual_source_names==sorted(scaffold_manifest['files'])
vendor_before=sorted(root_vendor_manifest['entries'],key=lambda x:x['path'])
assert len(vendor_before)==7912 and sum(x['size'] for x in vendor_before)==82680090
assert inventory(vendor)==vendor_before
shutil.copytree(source,work/'workspace')
snapshots=Snapshots(store)
dependency=snapshots.dependencies(vendor,source={'kind':'actual #4 installed Composer vendor',
    'container':'research-env-issue4-assets-v4-study-1','image_id':'sha256:b2173054c736ad9cd5af6d06e814a891c393929ad983bc918db96e798354762d',
    'export_operation':'Root: docker cp research-env-issue4-assets-v4-study-1:/opt/study/vendor /tmp/issue-6-composer-dependencies-v1',
    'root_inventory_sha256':'844c880cef69d00c52af88b1f99fc84a0e146c8ce861ee07e65bd343b6ba6754',
    'scaffold_repository_base':'7508be685c090aad2ee27eb862b20b44590b291b'},
    runtime={'study_image':'sha256:b2173054c736ad9cd5af6d06e814a891c393929ad983bc918db96e798354762d',
             'declaration':json.loads(Path('/source-tools/tools.json').read_text()),
             'probe_scope':'byte reconstruction; PHP/container boot separately evidenced in #4'})
candidate=snapshots.seal(work/'workspace',writers=ProcessWriters(),run_id=run.id,
    scaffold_id=f['settings'].scaffold_id,dependency_ids=(dependency.id,),internal_tests_hash=hashlib.sha256(b'fixture internal tests').hexdigest())
shutil.rmtree(work/'workspace')
# Original source-vendor is inaccessible to restoration code: rename writable
# local workspaces removed, actual restore reads only object IDs in the store.
restored=work/'restored'
restored_hash=snapshots.restore(candidate.id,restored)
actual=inventory(restored)
expected=sorted(source_before+[dict(x,path='vendor/'+x['path']) for x in vendor_before],key=lambda x:x['path'])
assert actual==expected
assert digest(actual)==candidate.tree_hash==restored_hash
assert inventory(source,project=True)==source_before and inventory(vendor)==vendor_before
assert (restored/'artisan').exists() and (restored/'composer.lock').exists() and (restored/'vendor/autoload.php').exists()
report={'scenario':'M3-A-S05','status':'passed','synthetic_control_metadata':True,'physical_external_hardware':'not_run: SSD unavailable',
    'platform':{'system':platform.system(),'machine':platform.machine(),'python':sys.version,'sqlite':sqlite3.sqlite_version},
    'runtime_image':'sha256:2ffd3236561b05e7ee97ee23ae69671f9ea5062aedcb6bbce73190dac59e97cc',
    'independent_oracle':'existing #4 scaffold manifest and unchanged Root vendor manifest',
    'permitted_scaffold_exclusions':[], 'input_scaffold_files':len(source_before),'input_vendor_files':len(vendor_before),'input_vendor_bytes':sum(x['size'] for x in vendor_before),
    'candidate_id':str(candidate.id),'dependency_id':str(dependency.id),'candidate_tree_hash':candidate.tree_hash,
    'restored_tree_hash':restored_hash,'restored_files':len(actual),'removed_workspace':True,
    'objects_integrity':store.integrity(),'source_unchanged':True,'technical_actor':'implementation agent; Docker execution Root',
    'limits':'Bytes and modes verified; no Laravel execution in this probe, runtime originally in #4; no S1/G10 hardware claim'}
(result_dir/'dependency-probe.json').write_text(json.dumps(report,indent=2)+'\n')
(result_dir/'candidate-tree.json').write_text(json.dumps(actual,indent=2)+'\n')
print(json.dumps(report,indent=2))
r.close()

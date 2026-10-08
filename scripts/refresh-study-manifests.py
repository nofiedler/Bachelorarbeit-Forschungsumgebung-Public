#!/usr/bin/env python3
"""Build-time asset inventory, no approval creation and no model defaults."""
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def inventory(path):return {str(p.relative_to(path)):dict(sha256=sha(p),bytes=p.stat().st_size) for p in sorted(path.rglob('*')) if p.is_file() and p.name!='manifest.json' and '__pycache__' not in str(p)}
def dump(p,obj):p.write_text(json.dumps(obj,indent=2,ensure_ascii=False,sort_keys=True)+'\n')
study=ROOT/'assets/study/m2-v0.1'
scaffold=inventory(study/'scaffold')
allow=['routes/study.php','app/Http/Controllers/Study/**/*.php','app/Study/**/*.php','resources/views/study/**/*.blade.php']
dump(study/'allowlist.json',dict(asset_version='M2-ALLOWLIST-v0.1',schema_version=1,paths=allow,glob_semantics='** includes direct directory level',file_type='regular file only; no links/traversal',execution_writable=['storage','bootstrap/cache','own temporary area'],protected='all scaffold files except routes/study.php; no new files outside explicit allowlist'))
dump(study/'manifest.json',dict(asset_version='M2-SCAFFOLD-v0.1',schema_version=1,type='scaffold',access_scope='shared role input; injected credentials excluded',files=scaffold,scaffold_tree_sha256=hashlib.sha256(json.dumps(scaffold,sort_keys=True).encode()).hexdigest(),interfaces_sha256=sha(study/'interfaces.md'),tools_sha256=sha(study/'tools.json'),allowlist_sha256=sha(study/'allowlist.json'),dependencies=dict(composer_json_sha256=sha(study/'scaffold/composer.json'),composer_lock_sha256=sha(study/'scaffold/composer.lock'),M1_locks_unchanged=sha(study/'scaffold/composer.lock')==sha(ROOT/'assets/laravel/composer.lock'),images_lock_sha256=sha(ROOT/'docker/images.lock.json'),study_dockerfile_sha256=sha(ROOT/'docker/study.Dockerfile'),additional_extension='mysqli compiled from fixed PHP source for private counterdesigns'),technical_status='asset construction; runtime evidence separate',human_review='open',approval=None))
dev=ROOT/'evaluation/development/m2-v0.1'
dump(dev/'manifest.json',dict(asset_version='M2-DEV-v0.1',schema_version=1,type='fixture_suite',suite_kind='development',access_scope='public development only',files=inventory(dev),origin='independent synthetic examples from public contract, not copied/derived from holdout',human_review='open'))
mounts=dict(asset_version='M2-MOUNTS-v0.1',schema_version=1,type='asset_access_contract',enforcement='prepared contract; actual mounts, networks, negative access/reset proofs in issue 7',profiles={
 'role_K0':{'read':['assets/context/m2-v0.1/<module>/K0/package.txt','assets/study/m2-v0.1/scaffold','own_role_artifacts'],'suite_kind':None},
 'role_K1':{'read':['assets/context/m2-v0.1/<module>/K1/package.txt','assets/study/m2-v0.1/scaffold','own_role_artifacts'],'suite_kind':None},
 'development_test':{'read':['own_candidate','own_internal_tests','evaluation/development/m2-v0.1'],'suite_kind':'development'},
 'study_candidate':{'read':['sealed_candidate','injected_runtime_fixture_state'],'suite_kind':'study_holdout','note':'no suite/reference/oracle mount into candidate'},
 'study_evaluator':{'read':['evaluation/study_holdout/m2-v0.1'],'suite_kind':'study_holdout','access_scope':'trusted external evaluator only'},
 'static_analysis':{'read':['own_candidate','frozen_analysis_tools'],'suite_kind':None}},
 forbidden_role_inputs=['Vault','full_DVWA','git_history','study_holdout','references','external_measurements','foreign_runs','credentials'],leakage_markers={'K1':'K1-SOURCE-<module>-M2-v0.1 / K1-MAPPING-<module>-M2-v0.1','private_reference':'PRIVATE-REFERENCES-M2-v0.1','suite':'PRIVATE-STUDY-HOLDOUT-M2-v0.1'},human_holdout_rule='No human feedback from protected cases/results into models/prompts/packages/choice, including before freeze')
dump(study/'mounts.json',mounts)
print(json.dumps(dict(scaffold_files=len(scaffold),manifest_sha256=sha(study/'manifest.json'),development_manifest_sha256=sha(dev/'manifest.json'))))

# Registry input for #5; trusted metadata, not an additional role mount.
entries=[]
for key,path,kind,scope,origin,suite in [
 ('scaffold',study/'manifest.json','scaffold','shared scaffold only','M1 skeleton + public M2 contract',None),
 ('development',dev/'manifest.json','fixture_suite','public development','independent synthetic public-contract examples','development'),
 ('references',ROOT/'evaluation/study_holdout/m2-v0.1/implementations/manifest.json','reference','trusted evaluator only','unchanged M2 reference catalog materialized','study_holdout'),
 ('holdout_catalog',ROOT/'evaluation/study_holdout/m2-v0.1/manifest.json','suite','trusted evaluator only','integrated issue3 catalog, unchanged','study_holdout')]:
 entries.append(dict(id=key,type=kind,schema_version=1,manifest=str(path.relative_to(ROOT)),manifest_sha256=sha(path),artifact_root=str(path.parent.relative_to(ROOT)),origin=origin,access_scope=scope,suite_kind=suite,human_review='open',approval=None))
for path in sorted((ROOT/'assets/context/m2-v0.1').glob('*/K*/manifest.json')):
 meta=json.loads(path.read_text());entries.append(dict(id='context-'+meta['module']+'-'+meta['condition'],type='source_context',schema_version=1,manifest=str(path.relative_to(ROOT)),manifest_sha256=sha(path),artifact_root=str(path.parent.relative_to(ROOT)),origin=dict(repository='digininja/DVWA',commit=meta['source_commit']),access_scope='assigned module/condition package.txt only',suite_kind=None,human_review='open',approval=None))
dump(study/'asset-index.json',dict(asset_version='M2-ASSET-INDEX-v0.1',schema_version=1,access_scope='trusted register metadata; never mount whole asset parent into roles',assets=entries))

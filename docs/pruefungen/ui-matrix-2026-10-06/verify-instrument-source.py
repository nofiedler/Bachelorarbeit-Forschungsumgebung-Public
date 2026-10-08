import hashlib,json
from pathlib import Path
from research_env.preparation import software_identity
from research_env.config import Settings
from research_env.register import Register
from research_env.study_restore import copy_settings
root=Path('/app/src/research_env')
files={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
policy=json.loads((root/'execution_compatibility.json').read_text())
old=next(iter(policy['releases']))
protected=policy['releases'][old]
r=Register(Settings.from_environment(),readonly=True)
c=Register(copy_settings(r.settings,'3237bd9b-ac29-4f3d-a0af-4b9fd34f7696'),readonly=True)
result={'runtime_source_binding':software_identity(),'runtime_files':files,'protected_files_match':all(files.get(k)==v for k,v in protected.items()),'protected_file_count':len(protected),'active':{}}
for label,reg in [('original',r),('copy',c)]:
 result['active'][label]={'generation':reg.connection.execute("SELECT COUNT(*) FROM run_binding WHERE execution!='terminal'").fetchone()[0],'completion':reg.connection.execute("SELECT COUNT(*) FROM run_completion WHERE status IN ('running','retry_requested')").fetchone()[0]}
 reg.close()
print(json.dumps(result,ensure_ascii=False,indent=2))

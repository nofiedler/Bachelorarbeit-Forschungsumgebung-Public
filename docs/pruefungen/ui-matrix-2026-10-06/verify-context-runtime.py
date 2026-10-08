import json,hashlib
from pathlib import Path
from research_env.config import Settings
from research_env.register import Register
from research_env.domain import ConfigurationVersion,Freeze,PlannedRun,Run,ModelCall,canonical
from research_env.preparation import software_identity
from research_env.sandbox_runtime import RuntimeImages
from research_env.evaluation import instrument_manifest as functional
from research_env.static_analysis import instrument_manifest as static
root=Path('/app/src/research_env');images=RuntimeImages(**json.loads((root/'pipeline_runtime.lock.json').read_text()))
r=Register(Settings.from_environment(),readonly=True)
result={'software':software_identity(),'files':{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc' and p.name!='.DS_Store'},'instruments':[functional(images),static(images)],'data_hashes':{cls.__name__:hashlib.sha256(canonical([x.model_dump(mode="json") for x in r.all(cls)]).encode()).hexdigest() for cls in (ConfigurationVersion,Freeze,PlannedRun,Run,ModelCall)},'model_calls':len(r.all(ModelCall)),'states':{str(run.id):canonical(r.state(run.id)) for run in r.all(Run)}}
print(json.dumps(result,ensure_ascii=False,indent=2));r.close()

import json
from research_env.config import Settings
from research_env.register import Register
from research_env.domain import Freeze,ConfigurationVersion,ModelCall
from research_env.preparation import software_identity
r=Register(Settings.from_environment(),readonly=True);items=[]
for f in r.all(Freeze):
 r._gates(f)
 items.append({'freeze_id':str(f.id),'freeze_hash':f.freeze_hash,'technical_gates':'passed','backup':r.backup_status(f.id),'next_id':str(r.next_id(f.id)) if r.next_id(f.id) else None})
active={'generation':r.connection.execute("SELECT COUNT(*) FROM run_binding WHERE execution!='terminal'").fetchone()[0],'evaluation':r.connection.execute("SELECT COUNT(*) FROM run_completion WHERE status IN ('running','retry_requested')").fetchone()[0]}
print(json.dumps({'software':software_identity(),'freezes':items,'model_calls':len(r.all(ModelCall)),'active':active},indent=2));assert not any(active.values());r.close()

import json
from research_env.config import Settings
from research_env.register import Register
from research_env.study_restore import copy_settings
from research_env.domain import Freeze,PlannedRun,Run,ModelCall,ConfigurationVersion,AssetVersion
from research_env.preparation import software_identity
from research_env.preparation_views import study
r=Register(Settings.from_environment());copyid='3237bd9b-ac29-4f3d-a0af-4b9fd34f7696'; c=Register(copy_settings(r.settings,copyid)); fid='2c997e37-1279-4d3c-9baa-6db4f76ed446';sid='daf090e0-1666-4c2a-9195-c27c7d91cf6e'
f=r.get(fid,Freeze); result={'copy_id':copyid,'study_id':sid,'freeze_id':fid,'freeze_hash':f.freeze_hash,'installed_source':software_identity(),'freeze_identical':f==c.get(fid,Freeze),'planned_runs_identical':r.all(PlannedRun)==c.all(PlannedRun),'runs_identical':r.all(Run)==c.all(Run),'model_calls_identical':r.all(ModelCall)==c.all(ModelCall),'model_call_count':len(r.all(ModelCall)),'states_identical':all(r.state(x.id)==c.state(x.id) for x in r.all(Run)),'next_original':str(r.next_id(fid)),'next_copy':str(c.next_id(fid)),'backup_status_original':r.backup_status(fid),'backup_status_copy':c.backup_status(fid),'next_block':study(c,sid)['phase_rows'][0]['freezes'][0]['next_block'],'gate_error_copy':study(c,sid)['phase_rows'][0]['freezes'][0]['gate_error']}
conf=r.get(next(iter(f.configuration_version_ids.values())),ConfigurationVersion);result['frozen_source']=conf.settings.software_commit;result['frozen_instrument_hashes']=[r.get(t,AssetVersion).manifest_hash for t in conf.settings.tool_ids]
print(json.dumps(result,ensure_ascii=False,indent=2))

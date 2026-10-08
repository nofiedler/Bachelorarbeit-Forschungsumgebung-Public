"""Independent static measurement probe; synthetic inputs, no model feedback."""
import sys,json,hashlib,shutil
from pathlib import Path
from datetime import datetime,timezone
from uuid import uuid4
sys.path.insert(0,'/app/tests')
import docker
import test_register as fixtures
from research_env.config import Settings
from research_env.register import Register
from research_env.artifacts import ArtifactStore
from research_env.domain import AssetVersion,StudyPhase,Configuration,MAIN_CELLS,ModelCall,digest
from research_env.preparation import installed_dependencies
from research_env.sandbox_runtime import Sandbox,RuntimeImages
from research_env.snapshots import Snapshots,ProcessWriters,inventory
from research_env.static_analysis import StaticAnalyzer,instrument_manifest,tool_binding
ROOT=Path('/app');out=Path('/audit/context-static2');out.mkdir()
settings=Settings(*(out/name for name in ('control','artifacts','checkpoints','staging')))
for p in (settings.control,settings.artifacts,settings.checkpoints,settings.staging):p.mkdir()
from research_env.database import migrate
migrate(settings)
r=Register(settings);store=ArtifactStore(settings,r)
fixtures.artifact=lambda reg,run=None,**kw:store.store(b'SYNTHETIC context/static configuration audit; not research data',run_id=run.id if run else None,**kw)
f=fixtures.setup_study(r)
images=RuntimeImages(**json.loads((ROOT/'src/research_env/pipeline_runtime.lock.json').read_text()))
client=docker.from_env(timeout=None);sandbox=Sandbox(store,client,images=images,assets=ROOT);analyzer=StaticAnalyzer(sandbox);snapshots=Snapshots(store)
tool=r.add(AssetVersion(code='SYNTHETIC-STATIC-AUDIT-'+str(uuid4()),asset_type='tool',manifest_hash=digest(instrument_manifest(images)),origin='Actual installed static instrument',access_scope='trusted_register'))
effective=f['settings'].model_copy(update={'tool_ids':(tool.id,)})
cases={
 'LaravelGood':{'source':'<?php\nnamespace App\\Study;\nfinal class LaravelGood { public function size(): int { return collect([1, 2])->count(); } }\n','D':0,'L':2},
 'TypeError':{'source':'<?php\nnamespace App\\Study;\nfinal class TypeError { public function value(): int { return "bad"; } public function size(): int { return strlen(123); } }\n','identifiers':['argument.type','return.type']},
 'LaravelRule':{'source':'<?php\nnamespace App\\Study;\nfinal class LaravelRule { public function value(): mixed { return env("SYNTHETIC_AUDIT_SETTING"); } }\n','larastan_env_rule':True},
}
reportdir=Path('/report');reportdir.mkdir(exist_ok=True)
(reportdir/'context-static-expected.json').write_text(json.dumps(cases,indent=2)+'\n')
results=[]
try:
 dependency=installed_dependencies(store);print('Pinned native dependencies verified',flush=True)
 for name,case in cases.items():
  phase=r.add(StudyPhase(code='SYNTHETIC-AUDIT-'+str(uuid4()),study_id=f['study'].id,purpose='preparation',provenance='Synthetic known static check, no human research approval'))
  conf=r.add(Configuration(code='SYNTHETIC-CONF-'+str(uuid4()),phase_id=phase.id))
  version=r.version_configuration(conf.id,name,MAIN_CELLS['C-SQL-0'],effective)
  run,job=r.start_other(phase.id,version.id,decision='Cost-free technical reference check',technical_evidence_ids=(f['basic'].id,),idempotency_key=str(uuid4()))
  tree=out/name;shutil.copytree(ROOT/'assets/study/m2-v0.1/scaffold',tree);(tree/'app/Study').mkdir(exist_ok=True)
  (tree/'app/Study'/f'{name}.php').write_text(case['source'])
  seal=snapshots.seal(tree,writers=ProcessWriters(),run_id=run.id,scaffold_id=effective.scaffold_id,dependency_ids=(dependency.id,),internal_tests_hash=hashlib.sha256(b'').hexdigest())
  before=inventory(settings.artifacts/'sealed'/str(seal.id))
  r.set_state(run.id,r.state(run.id).model_copy(update={'execution':'terminal','terminal_cause':'finished','ended_at':datetime.now(timezone.utc)}),reason='Supplied synthetic fixture, no generation')
  with r.transaction():r.connection.execute("UPDATE job_state SET status='stopped' WHERE job_id=?",(str(job.id),))
  execution=analyzer.schedule(run.id,tool_id=tool.id,idempotency_key=name);report=analyzer.run(execution)
  (reportdir/(name+'-report.json')).write_text(json.dumps(report,indent=2)+'\n')
  assert report['analysis_complete'],report['cause']
  if 'D' in case:assert report['D']==case['D'] and report['L']==case['L']
  if 'identifiers' in case:assert set(case['identifiers'])<={d['identifier'] for d in report['diagnostics']}
  if case.get('larastan_env_rule'):assert report['D']>0 and any('env' in d['message'].lower() for d in report['diagnostics'])
  assert inventory(settings.artifacts/'sealed'/str(seal.id))==before
  result={'case':name,'D':report['D'],'L':report['L'],'S':report['S'],'identifiers':[d['identifier'] for d in report['diagnostics']],'seal_unchanged':True,'completed':True}
  results.append(result);print(json.dumps(result),flush=True)
 assert not r.all(ModelCall)
 (reportdir/'context-static-summary.json').write_text(json.dumps({'passed':True,'synthetic':True,'model_calls':0,'cases':results,'instrument':instrument_manifest(images),'tool':tool_binding()},indent=2)+'\n')
finally:
 for handle in list(sandbox.handles.values()):
  try:handle.abort(reason='Synthetic static audit cleanup')
  except Exception:pass
 client.close();r.close()

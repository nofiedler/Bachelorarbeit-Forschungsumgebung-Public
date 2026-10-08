#!/usr/bin/env python3
"""Native known instrumentation defect, correction before new same-seal outputs.

Use setup with an independently frozen instrument containing the deliberate
counter defect; correct with the real fixed instrument. No generation/rewrite.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
from uuid import UUID,uuid4
import docker

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests')]
import test_register as fixtures
from research_env.artifacts import ArtifactStore
from research_env.config import Settings
from research_env.database import migrate
from research_env.domain import *
from research_env.register import Register
from research_env.sandbox_runtime import Sandbox,RuntimeImages
from research_env.snapshots import Snapshots,ProcessWriters,inventory
from research_env.static_analysis import StaticAnalyzer,instrument_manifest

p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--stage',choices=('setup','correct'),required=True);p.add_argument('--vendor',type=Path);a=p.parse_args()
settings=Settings(*(a.output/name for name in ('control','artifacts','checkpoints','staging')))
images=RuntimeImages(**json.loads((ROOT/'src/research_env/pipeline_runtime.lock.json').read_text()))
client=docker.from_env(timeout=None)

def write(name,body):
    path=a.output/name
    if path.exists():raise ValueError('Raw evidence already exists')
    path.write_text(json.dumps(body,indent=2,default=str)+'\n')

if a.stage=='setup':
    if not a.vendor or a.output.exists() and any(a.output.iterdir()):p.error('Setup requires fresh output and vendor')
    a.output.mkdir(parents=True,exist_ok=True)
    for path in (settings.control,settings.artifacts,settings.checkpoints,settings.staging):path.mkdir()
    migrate(settings);r=Register(settings);store=ArtifactStore(settings,r)
    fixtures.artifact=lambda reg,run=None,**kw:store.store(b'SYNTHETIC native known static defect validation; not study',run_id=run.id if run else None,**kw)
    f=fixtures.setup_study(r);tool=r.add(AssetVersion(code='STATIC-DEFECT-OLD-'+str(uuid4()),asset_type='tool',manifest_hash=digest(instrument_manifest(images)),origin='Actual frozen deliberate known lexical counter defect',access_scope='trusted_register'))
    sandbox=Sandbox(store,client,images=images,assets=ROOT);snapshots=Snapshots(store);analyzer=StaticAnalyzer(sandbox)
    dependency=snapshots.dependencies(a.vendor,source={'origin':'Unmodified M2 dependencies'},runtime={'php_image':images.php})
    runs=[]
    # Hand-computed expected L=2 for both; deliberately defective counter misses
    # the final physical PHP code line, but its internal line/count consistency
    # means the independent fixture check is needed to establish the defect.
    source='<?php\nnamespace App\\Study;\nfinal class Probe { public function value(): int { return 1; } }\n'
    write('expected-before-defective-measurement.json',{'source_hex':source.encode().hex(),'L':2,'line_numbers':[2,3],'D':0,'S':'0','defect':'Known injected omission of final counted line, same unchanged measure definition'})
    for number in (1,2):
        phase=r.add(StudyPhase(code='STATIC-DEFECT-PHASE-'+str(uuid4()),study_id=f['study'].id,purpose='preparation',provenance='Actual controlled native measurement-defect fixture'))
        configuration=r.add(Configuration(code='STATIC-DEFECT-CONF-'+str(uuid4()),phase_id=phase.id))
        version=r.version_configuration(configuration.id,'DEFECT-'+str(number),MAIN_CELLS['C-SQL-0'],f['settings'].model_copy(update={'tool_ids':(tool.id,)}))
        run,job=r.start_other(phase.id,version.id,decision='Authorized cost-free known-defect probe',technical_evidence_ids=(f['basic'].id,),idempotency_key=str(uuid4()))
        path=a.output/('source-'+str(number));shutil.copytree(ROOT/'assets/study/m2-v0.1/scaffold',path)
        for root,dirs,files in os.walk(path):
            Path(root).chmod(stat.S_IMODE(Path(root).stat().st_mode)|stat.S_IWUSR)
            for name in files:(Path(root)/name).chmod(stat.S_IMODE((Path(root)/name).stat().st_mode)|stat.S_IWUSR)
        (path/'app/Study').mkdir(parents=True);(path/'app/Study/Probe.php').write_text(source)
        seal=snapshots.seal(path,writers=ProcessWriters(),run_id=run.id,scaffold_id=version.settings.scaffold_id,dependency_ids=(dependency.id,),internal_tests_hash=hashlib.sha256(b'').hexdigest())
        r.set_state(run.id,r.state(run.id).model_copy(update={'execution':'terminal','terminal_cause':'finished','ended_at':datetime.now(timezone.utc)}),reason='Supplied fixture, no generation')
        with r.transaction():r.connection.execute("UPDATE job_state SET status='stopped' WHERE job_id=?",(str(job.id),))
        execution=analyzer.schedule(run.id,tool_id=tool.id,idempotency_key='defective-'+str(number));report=analyzer.run(execution)
        if not report['analysis_complete'] or report['D']!=0 or report['L']!=1:raise AssertionError('Known native defect not observed as planned '+json.dumps(report))
        runs.append({'run_id':str(run.id),'seal_id':str(seal.id),'candidate_hash':seal.tree_hash,'configuration_id':str(version.id),'configuration_hash':version.content_hash,'measurement_id':report['measurement_id'],'report':report,'readonly_original':inventory(settings.artifacts/'sealed'/str(seal.id))})
        write('old-report-'+str(number)+'.json',report)
    defect=store.json({'observed':[item['report']['L'] for item in runs],'expected':2,'cause':'Deliberately omitted final PHP line in frozen tokenizer implementation; independent preassigned fixture demonstrates measurement defect','old_instrument':instrument_manifest(images)},artifact_type='evaluation_static_defect',producer='trusted_evaluator',access_scope='trusted_evaluator')
    write('setup-binding.json',{'old_tool_id':str(tool.id),'defect_evidence_id':str(defect.id),'old_instrument':instrument_manifest(images),'runs':runs,'model_calls':0})
    r.close();print('Native controlled defective measurements completed',flush=True)
else:
    r=Register(settings);store=ArtifactStore(settings,r);sandbox=Sandbox(store,client,images=images,assets=ROOT);analyzer=StaticAnalyzer(sandbox)
    original=json.loads((a.output/'setup-binding.json').read_text())
    tool=r.add(AssetVersion(code='STATIC-DEFECT-FIXED-'+str(uuid4()),asset_type='tool',manifest_hash=digest(instrument_manifest(images)),origin='Actual corrected frozen implementation of same static measure',access_scope='trusted_register'))
    correction=analyzer.correct_instrument(UUID(original['old_tool_id']),tool.id,defect_evidence_id=UUID(original['defect_evidence_id']),reason='Independent hand-calculated fixture proves omitted final PHP line; correct all affected candidates unchanged',person='Technical synthetic validation; human approval remains open')
    write('correction-before-new-output.json',json.loads(store.read(correction.id)))
    results=[]
    for number,item in enumerate(original['runs'],1):
        run_id=UUID(item['run_id']);seal_id=UUID(item['seal_id'])
        old=r.get(UUID(item['measurement_id']),MeasurementAttempt)
        if r.select_revision(run_id,'measurement','static_DLS',old.compatibility) is not None:raise AssertionError('Invalid old fallback still selectable')
        execution=analyzer.schedule(run_id,tool_id=tool.id,idempotency_key='corrected-'+str(number));report=analyzer.run(execution)
        m=r.get(UUID(report['measurement_id']),MeasurementAttempt)
        conf=r.get(UUID(item['configuration_id']),ConfigurationVersion)
        checks={'completed':report['analysis_complete'],'L_corrected':report['L']==2,'D_preserved':report['D']==0,'same_candidate_hash':report['candidate_hash']==item['candidate_hash'],'same_original_configuration_hash':conf.content_hash==item['configuration_hash'],
            'readonly_original_unchanged':inventory(settings.artifacts/'sealed'/str(seal_id))==item['readonly_original'],
            'all_old_revisions_invalid':all(not r.valid(value.id) for value in r.all(MeasurementAttempt) if value.compatibility.tool_id==UUID(original['old_tool_id'])),
            'corrected_selected':r.select_revision(run_id,'measurement','static_DLS',m.compatibility).id==m.id,
            'same_scientific_measure':original['old_instrument']['measure']==instrument_manifest(images)['measure']}
        if not all(checks.values()):raise AssertionError(json.dumps(checks))
        write('corrected-report-'+str(number)+'.json',{'report':report,'checks':checks});results.append(checks)
    write('summary.json',{'results':results,'passed':sum(len(x) for x in results),'failed':0,'corrected_instrument':instrument_manifest(images),'correction_artifact_id':str(correction.id),'no_new_generation':True,'model_calls':0})
    r.close();print('All affected native candidates remeasured with unchanged seals and scale',flush=True)

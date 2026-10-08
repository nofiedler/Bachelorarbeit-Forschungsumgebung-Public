"""Create a genuine study ZIP from synthetic registered measurements; never calls a model.

Run from the repository: python scripts/create-demo-study-package.py --output /absolute/new/directory
The existing test fixture provides explicitly simulated approvals; not valid for an empirical study.
"""
import argparse
import json
from fractions import Fraction
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'), str(ROOT/'tests')]
import m7_fixture
from test_analysis import confirm
from research_env.adapter import CallJournal
from research_env.analysis_store import Analyses
from research_env import exchange

COUNTS={
 1:{'C-BF-0':2,'C-BF-1':5,'C-SQL-0':3,'C-SQL-1':4,'C-UP-0':4,'C-UP-1':6,
    'E-AB':2,'E-BA':5,'E-BB':1,'E-P0R0':4,'E-P0R1':5,'E-P1R0':6},
 2:{'C-BF-0':4,'C-BF-1':3,'C-SQL-0':1,'C-SQL-1':4,'C-UP-0':3,'C-UP-1':5,
    'E-AB':6,'E-BA':2,'E-BB':3,'E-P0R0':0,'E-P0R1':2,'E-P1R0':1}}
EXPECTED={'data_origin':'synthetic','planned':48,'observed':24,'n_C':2,'delta':'5/18',
          'lower':'-22/45','upper':'32/45','UF3_AB_AA':'0','UF3_BA_BB':'1/4','UF4_Interaction':'-1/6'}

def create(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    (output/'erwartete-werte.json').write_text(json.dumps(EXPECTED,indent=2)+'\n')
    env=m7_fixture.make_environment(output/'synthetische-instanz')
    r,settings,store,f,freeze,backups=env
    identities=[]
    for plan in freeze.matrix:
        if plan['block_index']>2:break
        run,comp,raw=m7_fixture.start(env,output)
        assert str(run.planned_run_id)==plan['id']
        count=COUNTS[plan['block_index']][plan['cell_key']]
        measure=m7_fixture.measurement(env,run,comp,raw,passed=count)
        # A newer draft must not override the completed result.
        if plan['position']==1:m7_fixture.measurement(env,run,comp,raw,completion='draft')
        for criterion in ('T1','T2','T3','T4','T5'):
            m7_fixture.review(env,run,comp,raw,criterion,measurement_ids=(measure.id,) if criterion=='T4' else ())
        d=plan['position']%5
        length=0 if plan['cell_key']=='E-BB' and plan['block_index']==2 else 100+plan['position']
        m7_fixture.measurement(env,run,comp,raw,key='static_DLS',report={'analysis_complete':True,'D':d,'L':length})
        m7_fixture.time_partition(env,run,gap='none')
        identities.append({'planned_id':plan['id'],'run_id':str(run.id),'configuration_id':str(run.configuration_version_id),
            'position':plan['position'],'block':plan['block_index'],'cell':plan['cell_key'],'F':str(Fraction(count,6)),'D':d,'L':length})
        print('Synthetic run',plan['position'],plan['cell_key'],flush=True)
    service=Analyses(r,CallJournal.cost_view(r));proposal=service.propose(freeze.id)
    result=proposal['preview']
    assert len(result['cells'])==48 and result['n_C']==2
    assert result['core']['mean']['value']==EXPECTED['delta']
    assert result['missing_bounds']['lower']['value']==EXPECTED['lower']
    assert result['missing_bounds']['upper']['value']==EXPECTED['upper']
    assert result['UF3']['contrasts']['AB-AA']['mean']['value']==EXPECTED['UF3_AB_AA']
    assert result['UF3']['contrasts']['BA-BB']['mean']['value']==EXPECTED['UF3_BA_BB']
    assert result['UF4']['contrasts']['Interaction']['mean']['value']==EXPECTED['UF4_Interaction']
    by_id={x['id']:x for x in result['cells']}
    for identity in identities:
        cell=by_id[identity['planned_id']]
        assert cell['run_id']==identity['run_id']
        assert cell['functional']['F']['value']==identity['F']
        assert cell['static']['D']['value']==str(identity['D'])
        assert cell['static']['L']['value']==str(identity['L'])
    for cell in result['cells'][24:]:assert cell['run_id'] is None and cell['functional']['F']['value'] is None
    analysis=confirm(service,proposal)
    package=output/'synthetische-versuchsserie.zip'
    manifest=exchange.export(r,analysis.id,package)
    assert exchange.recalculate(package)==result
    (output/'laufzuordnung-soll.json').write_text(json.dumps(identities,indent=2)+'\n')
    (output/'pruefergebnis.json').write_text(json.dumps({'passed':True,'expected':EXPECTED,'package_id':manifest['package_id'],
        'analysis_id':str(analysis.id),'study_id':str(f['study'].id),'freeze_id':str(freeze.id),'checks':'24 exact run/plan/F/D/L bindings, 24 missing rows, all hand contrasts, complete ZIP roundtrip'},indent=2)+'\n')
    r.close()
    print(package,flush=True)
    return package

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args();create(args.output)

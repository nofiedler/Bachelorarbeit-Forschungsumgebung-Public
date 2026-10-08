"""No-network regression for the matrix workflow without pilot prerequisites."""
from uuid import uuid4
from bs4 import BeautifulSoup
import pytest
from research_env.application_settings import initialize, default_settings
from research_env.domain import Study, StudyPhase, ModelPackage, MAIN_CELLS, Run, Freeze, Approval
from research_env.preparation import put_configuration, versions_hash
from research_env.preparation_freeze import preview
from research_env.register import GateError
from test_register import register
from test_preparation import client, post


def draft(r):
    initialize(r)
    model = next(m for m in r.all(ModelPackage) if m.endpoint.startswith('mock://'))
    study = r.add(Study(code='SYNTHETIC-MATRIX', title='SYNTHETIC matrix without pilots', design_version='test', data_origin='synthetic', provenance='Automated fixture, no human approval'))
    phase = r.add(StudyPhase(code='SYNTHETIC-MAIN', study_id=study.id, purpose='main', provenance='Automated fixture'))
    settings = default_settings(r, research=True).model_copy(update={'model_a':model.id, 'model_b':model.id, 'role_parameters':{'all':{'seed':1}}})
    for key, cell in MAIN_CELLS.items(): put_configuration(r, phase.id, key, cell, settings)
    return study, phase, settings


def fields(r, phase):
    return {'phase_id':str(phase.id), 'idempotency_key':str(uuid4()), 'base_hash':versions_hash(r,phase.id),
        'r_c':'2','r_e':'1','seed':'no-pilot-test', 'resource_decision':'SYNTHETIC fixed extent before results; no estimate',
        'backup_destination':'SYNTHETIC separate test disk',
        **{p+'_person':'TECHNICAL-FIXTURE: automated test' for p in ('technical','subject','cost')},
        **{p+'_reason':'Synthetic decision fixture, not a human approval' for p in ('technical','subject','cost')},
        **{'confirm_'+p:'true' for p in ('technical','subject','cost')}}


def test_preview_freeze_without_pilots_or_report_ids_and_complete_order(register,tmp_path):
    r,_=register; study,phase,_=draft(r); c=client(r)
    data=fields(r,phase)
    page=post(c,data,'/freeze/preview')
    assert page.status_code==200, page.text
    soup=BeautifulSoup(page.text,'html.parser')
    assert len(soup.select('table.freeze-matrix tbody tr'))==18
    assert not r.all(Freeze) and not r.all(Approval) and not r.all(Run)
    _, intent=preview(r,data)
    assert intent.freeze.schema_version==2 and not intent.freeze.pilot_run_ids
    assert intent.freeze.estimated_cost.status=='not_collected'
    approvals=tuple(a.model_copy(update={'synthetic_fixture':True}) for a in intent.approvals)
    freeze=r.freeze(intent.freeze,approvals=approvals)
    assert len(r.fq_ids(freeze.id))==18 and r.backup_status(freeze.id)=='required'
    html=c.get('/studies/'+str(study.id)).text
    assert len(BeautifulSoup(html,'html.parser').select('table.run-table tbody tr'))==18
    assert not r.all(Run)
    # The new no-pilot format must also survive the actual export/recalculation path.
    from research_env.analysis_store import Analyses
    from research_env.adapter import CallJournal
    from research_env.exchange import export, recalculate
    from test_analysis import confirm
    analyses=Analyses(r,CallJournal.cost_view(r)); proposal=analyses.propose(freeze.id)
    analysis=confirm(analyses,proposal)
    package=tmp_path/'no-pilot.zip';export(r,analysis.id,package)
    assert recalculate(package)==proposal['preview']


def test_form_removes_pilot_and_report_selectors_and_names_real_missing_field(register):
    r,_=register;study,phase,settings=draft(r)
    r.central_update(phase.id,settings.model_copy(update={'model_a':None}))
    html=client(r).get('/studies/'+str(study.id)).text
    assert 'Modellpaket A fehlt' in html
    assert 'maschinenlesbaren Nachweiskatalog' not in html
    assert 'name="pilot_ids"' not in html and 'name="consumption_id"' not in html
    assert 'name="estimated_cost"' not in html and 'name="proof_' not in html
    with pytest.raises(GateError,match='Modellpaket A fehlt'): preview(r,fields(r,phase))


def test_preview_still_requires_decisions_and_current_versions(register):
    r,_=register;_,phase,settings=draft(r);data=fields(r,phase)
    with pytest.raises(GateError):preview(r,{**data,'confirm_subject':'false'})
    r.central_update(phase.id,settings.model_copy(update={'model_b':None}))
    with pytest.raises(GateError,match='Entwurf verändert'):preview(r,data)


def test_worker_rejects_scope_changed_after_preview(register):
    from research_env.preparation import submit, process_command
    r,_=register;_,phase,_=draft(r)
    _,intent=preview(r,fields(r,phase))
    r.connection.execute('INSERT INTO matrix_draft_preferences VALUES(?,?,?,?)',(str(phase.id),3,1,'changed-seed'))
    command=submit(r,'stale-scope',intent)
    process_command(r)
    row=r.connection.execute('SELECT status,reason FROM ui_command WHERE id=?',(command,)).fetchone()
    assert row['status']=='failed' and 'Umfang oder Seed' in row['reason']
    assert not r.all(Freeze) and not r.all(Approval)

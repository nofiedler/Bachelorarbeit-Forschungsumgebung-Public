"""Regression checks for the user's matrix -> results -> imported report workflow."""
from bs4 import BeautifulSoup
import pytest
from research_env.domain import Run
from test_exchange import exported
from test_preparation import client, post


def test_import_displays_full_report_and_all_planned_identities(exported):
    env, analysis, path, manifest = exported
    c = client(env[0]); c.get('/packages')
    run_ids = {str(run.planned_run_id):str(run.id) for run in env[0].all(Run)
               if str(run.planned_run_id) in manifest['planned_ids']}
    assert len(run_ids) == 1  # Only the actually started fixture run.
    def identities(page):
        rows = page.select('#runs [data-planned-id]')
        assert len(rows) == len(manifest['planned_ids'])
        assert {row['data-planned-id'] for row in rows} == set(manifest['planned_ids'])
        for row in rows:
            planned_id = row['data-planned-id']
            provenance = row.select_one('.proposal-run-provenance')
            assert provenance is not None
            text = provenance.get_text(' ', strip=True)
            assert 'Geplante Lauf-ID: ' + planned_id in text
            assert 'Tatsächliche Lauf-ID: ' + run_ids.get(planned_id, 'Nicht gestartet') in text
        return rows
    active = BeautifulSoup(c.get('/analyses/' + str(analysis.id)).text, 'html.parser')
    identities(active)
    assert {a['href'] for a in active.select('#runs a[href^="/runs/"]')} == {
        f'/runs/{run_id}/{suffix}' for run_id in run_ids.values() for suffix in ('results','evidence')}
    response = c.post('/packages/import', data={'csrf': c.cookies['research_csrf']},
        files={'archive': ('synthetic.zip', path.read_bytes(), 'application/zip')},
        headers={'origin': 'http://testserver'}, follow_redirects=False)
    assert response.status_code == 303
    page = c.get(response.headers['location'])
    soup = BeautifulSoup(page.text, 'html.parser')
    assert soup.select_one('#context'), 'Imported report must show the same context analysis as the live report'
    identities(soup)
    assert not soup.select('a[href^="/runs/"]'), 'Imported IDs must never link into unrelated active data'
    figures = soup.select('.scientific-report .plot-card img')
    assert figures, 'Imported figures must be visible, not only downloadable'
    assert len(figures) == len(active.select('.scientific-report .plot-card img'))
    for img in figures:
        image = c.get(img['src'])
        assert image.status_code == 200 and image.headers['content-type'] == 'image/png'
        assert image.content.startswith(b'\x89PNG\r\n\x1a\n')
    assert post(c, {}, response.headers['location'] + '/recalculate').status_code == 303
    assert 'stimmt exakt' in c.get(response.headers['location']).text


def test_study_run_table_retains_frozen_order_status_and_navigation(exported):
    env, _, _, _ = exported
    r, _, _, f, freeze, _ = env
    c = client(r)
    soup = BeautifulSoup(c.get('/studies/' + str(f['study'].id)).text, 'html.parser')
    rows = soup.select('table.run-table tbody tr')
    assert len(rows) == len(freeze.matrix)
    for row, plan in zip(rows, freeze.matrix):
        assert row.select_one('strong').text == 'Lauf ' + str(plan['position'])
        assert row.select_one('.status-badge').text.strip()
        assert row.select_one('a[href^="/configurations/"]')['href'] == '/configurations/' + str(freeze.configuration_version_ids[plan['cell_key']])
    run_id = rows[0].select_one('.run-open a')['href'].split('/')[2]
    for suffix in ('', '/results'):
        page = BeautifulSoup(c.get('/runs/' + run_id + suffix).text, 'html.parser')
        assert '<' not in page.title.text
        assert page.select_one('a.back-link')['href'] == '/studies/' + str(f['study'].id)


def test_next_main_requires_current_backup_and_cannot_skip(exported, tmp_path):
    import pytest
    import m7_fixture
    from uuid import UUID
    from test_register import start
    from research_env.register import GateError
    env, _, _, _ = exported
    r, _, _, f, freeze, _ = env
    expected = r.next_id(freeze.id)
    with pytest.raises(GateError):
        start(r, freeze, f)  # Previous measurement/analysis stand is not backed up.
    m7_fixture.secure(env, tmp_path / 'separate-synthetic-backup')
    with pytest.raises(GateError):
        r.start_main(freeze.id, UUID(freeze.matrix[2]['id']), expected_freeze_hash=freeze.freeze_hash,
            decision='SYNTHETIC attempt to skip run 2', idempotency_key='skip-rejected', technical_evidence_ids=(f['basic'].id,))
    run, _ = start(r, freeze, f, key='single-next-start')
    assert str(run.planned_run_id) == str(expected)
    same, _ = r.start_main(freeze.id, expected, expected_freeze_hash=freeze.freeze_hash,
        decision='TECHNICAL-FIXTURE: conscious single start', idempotency_key='single-next-start', technical_evidence_ids=(f['basic'].id,))
    assert same.id == run.id
    page = BeautifulSoup(client(r).get('/studies/' + str(f['study'].id)).text, 'html.parser')
    rows = page.select('table.run-table tbody tr')
    assert rows[1].select_one('.run-open a')['href'] == '/runs/' + str(run.id)
    assert 'Lauf 2' in rows[1].text


@pytest.mark.parametrize("content", ['{"files":[', '{"schema":"roles-v1","role":"test","files":[{"path":"internal/check.php","content":"<?php // partial"}]}'])
def test_provider_output_limit_is_technical_and_keeps_costs_and_candidate(tmp_path, content):
    from test_pipeline import setup, finish, wire, envelope, response
    from research_env.domain import Artifact, ModelCall
    from research_env.workflow_views import rejected_answer
    truncated = response(choices=[{'message': {'content': content}, 'finish_reason': 'length'}],
                         usage={'prompt_tokens': 50, 'completion_tokens': 100, 'cost': 0.25})
    r, _, store, run, _, pipeline, scheduler, adapter, runner = setup(tmp_path,
        planner=False, review=False,
        outputs=[wire(envelope('analyzer')), wire(envelope('migrate')), truncated])
    finish(scheduler, run)
    assert adapter.send_count == 3 and not runner.invocations
    assert r.state(run.id).terminal_cause == 'technical_failure'
    assert r.state(run.id).seal == 'sealed'  # The earlier application candidate survives.
    assert pipeline.journal.diagnostic(r.all(ModelCall)[-1].id)['stop_reason'] == 'provider_output_limit'
    assert not [a for a in r.all(Artifact) if a.artifact_type == 'role_test']
    assert 'Ausgabelimit' in rejected_answer(r, run.id)['reason']
    costs = pipeline.summary(run.id)['costs']
    assert any(item['cost']['status'] == 'known' and item['cost']['known_subtotal'] == '0.25' for item in costs['evidence'])
    pipeline.close(); r.close()

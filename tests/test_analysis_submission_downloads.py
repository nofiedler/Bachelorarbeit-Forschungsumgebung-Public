"""Submission downloads remain bound to one analysis and preserve original data."""
import hashlib
import io
import json
from uuid import uuid4
import zipfile

from bs4 import BeautifulSoup

from research_env import exchange
from research_env.domain import ModelCall
from test_exchange import exported
from test_preparation import client, post


def test_submission_download_flow_is_bound_idempotent_and_read_only(exported):
    env, analysis, _, _ = exported
    register = env[0]
    c = client(register)
    url = '/analyses/' + str(analysis.id)
    before = dict(register.connection.execute('SELECT id,sha256 FROM register_record').fetchall())
    call_count = len(register.all(ModelCall))
    page = c.get(url)
    assert page.status_code == 200
    soup = BeautifulSoup(page.text, 'html.parser')
    assert len(soup.select('#submission-downloads .submission-download-card')) == 2
    assert soup.select_one('a[href="' + url + '/code.zip"]')
    assert soup.select_one('form[action="' + url + '/raw-data"]')
    assert not soup.select_one('#analysis-raw-export[hx-trigger]')
    code = c.get(url + '/code.zip')
    assert code.status_code == 200
    assert str(analysis.id) in code.headers['content-disposition']
    with zipfile.ZipFile(io.BytesIO(code.content)) as archive:
        assert 'auswerten.py' in archive.namelist()
        assert 'README.md' in archive.namelist()
        assert not any(name.startswith(('objects/', 'registers/', 'raw-data/')) for name in archive.namelist())

    key = str(uuid4())
    for _ in range(2):
        response = post(c, {'idempotency_key': key}, url + '/raw-data')
        assert response.status_code == 303
        assert response.headers['location'] == url + '#submission-downloads'
    rows = register.connection.execute('SELECT * FROM exchange_job WHERE analysis_id=?', (str(analysis.id),)).fetchall()
    assert len(rows) == 1
    job = dict(rows[0])
    pending = c.get(url + '/raw-data/status')
    assert pending.status_code == 200 and 'hx-trigger="every 3s"' in pending.text
    assert '<h1>' not in pending.text  # Poll only the small state fragment.
    assert exchange.tick(register)
    ready = c.get(url + '/raw-data/status')
    assert ready.status_code == 200 and 'hx-trigger' not in ready.text
    assert 'Rohdatenpaket herunterladen' in ready.text
    download = c.get('/packages/' + job['id'] + '/download?raw_data=true')
    assert download.status_code == 200 and 'rohdaten-' + str(analysis.id) in download.headers['content-disposition']
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['analysis_id'] == str(analysis.id)
        assert manifest['raw_data_schema'] == 'research-series-raw-data-v1'
        assert 'raw-data/manifest.json' in manifest['files']
        assert hashlib.sha256(archive.read('raw-data/manifest.json')).hexdigest() == manifest['files']['raw-data/manifest.json']
    assert before == dict(register.connection.execute('SELECT id,sha256 FROM register_record').fetchall())
    assert len(register.all(ModelCall)) == call_count


def test_submission_export_requires_csrf_and_never_accepts_a_different_analysis(exported):
    env, analysis, _, _ = exported
    register = env[0]; c = client(register)
    url = '/analyses/' + str(analysis.id)
    c.get(url)
    response = c.post(url + '/raw-data', data={'idempotency_key': str(uuid4())}, headers={'origin': 'http://testserver'})
    assert response.status_code == 409
    assert register.connection.execute('SELECT count(*) FROM exchange_job').fetchone()[0] == 0
    assert c.get('/analyses/' + str(uuid4()) + '/code.zip').status_code != 200
    assert c.get('/analyses/' + str(uuid4()) + '/raw-data/status').status_code != 200


def test_imported_submission_downloads_preserve_original_archive(exported):
    env, analysis, path, _ = exported
    c = client(env[0]); c.get('/packages')
    response = c.post('/packages/import', data={'csrf': c.cookies['research_csrf']},
        files={'archive': ('study.zip', path.read_bytes(), 'application/zip')},
        headers={'origin': 'http://testserver'}, follow_redirects=False)
    assert response.status_code == 303
    url = response.headers['location']
    page = c.get(url)
    assert 'Auswertungsskript herunterladen' in page.text
    assert c.get(url + '/download').content == path.read_bytes()
    code = c.get(url + '/code.zip')
    assert code.status_code == 200 and str(analysis.id) in code.headers['content-disposition']

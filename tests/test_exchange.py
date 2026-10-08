import hashlib
import io
import json
from pathlib import Path
import stat
import zipfile

import pytest

import m7_fixture
from test_analysis import confirm
from test_preparation import client, post
from research_env import exchange
from research_env.adapter import CallJournal
from research_env.analysis_store import Analyses
from research_env.config import Settings
from research_env.domain import ModelCall


@pytest.fixture
def exported(tmp_path):
    env = m7_fixture.make_environment(tmp_path / 'origin')
    register = env[0]
    m7_fixture.start(env, tmp_path)
    service = Analyses(register, CallJournal.cost_view(register))
    analysis = confirm(service, service.propose(env[4].id))
    path = tmp_path / 'study.zip'
    manifest = exchange.export(register, analysis.id, path)
    yield env, analysis, path, manifest
    register.close()


def test_archive_roundtrip_fresh_instance_exact_ids_raw_bytes_and_no_model_calls(exported, tmp_path):
    env, analysis, path, manifest = exported
    r = env[0]
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    calls = len(r.all(ModelCall))
    settings = Settings(*(tmp_path / 'fresh' / part for part in ('control', 'artifacts', 'checkpoints', 'staging')))
    settings.staging.mkdir(parents=True)
    first = exchange.import_archive(settings, path)
    assert exchange.import_archive(settings, path) == first
    original = settings.staging / 'imports' / (manifest['package_id'] + '.zip')
    result = exchange.recalculate(original)
    assert result['data_hash'] == analysis.input_hash
    assert {row['id'] for row in result['cells']} == set(manifest['planned_ids'])
    assert len(list((settings.staging / 'imports').iterdir())) == 1
    assert hashlib.sha256(original.read_bytes()).hexdigest() == before
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert len(r.all(ModelCall)) == calls
    assert not settings.database.exists()  # Nothing became an active study or role input.
    with zipfile.ZipFile(path) as archive:
        assert b'openrouter.key' not in archive.read('checksums.sha256')
        assert 'analysis/runs.csv' in archive.namelist()
        assert result['n_C'] == 0 and manifest['scientific_gaps']
        assert 'presentation/runs.csv' in archive.namelist()
        assert 'presentation/methods.md' in archive.namelist()
        derived = json.loads(archive.read('presentation/manifest.json'))
        assert derived['data_hash'] == analysis.input_hash
        assert derived['original_result_sha256'] == hashlib.sha256(archive.read('analysis/analysis.json')).hexdigest()
        assert archive.read('presentation/analysis.json') == archive.read('analysis/analysis.json')
        snapshot = json.loads(archive.read('analysis/input.json'))
        companion = json.loads(archive.read('presentation/pipeline-steps.json'))
        assert len(companion['rows']) == len(snapshot['rows']) * 12
        assert companion['source']['snapshot_artifact_id'] == analysis.selected_inputs['_m7']['snapshot_id']
        assert companion['source']['data_hash'] == analysis.input_hash
    from research_env.analysis_step_ui import event_times
    timeline = []
    exchange.verify(original, timeline=timeline)
    assert event_times(timeline[0], snapshot, timeline[0].snapshot_id) == event_times(
        r, snapshot, analysis.selected_inputs['_m7']['snapshot_id'])


@pytest.mark.parametrize('name,mode', [('../escape', stat.S_IFREG), ('/absolute', stat.S_IFREG),
    ('C:/drive', stat.S_IFREG), ('a/../b', stat.S_IFREG), ('link', stat.S_IFLNK), ('device', stat.S_IFCHR)])
def test_unsafe_archive_entries_rejected_before_reading_metadata(tmp_path, name, mode):
    path = tmp_path / 'bad.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        info = zipfile.ZipInfo(name); info.external_attr = (mode | 0o444) << 16
        archive.writestr(info, b'bad')
    with pytest.raises(ValueError):
        exchange.verify(path)


def test_missing_required_file_and_changed_original_rejected(exported, tmp_path):
    _, _, source, _ = exported
    for mode in ('missing', 'changed', 'duplicate'):
        path = tmp_path / (mode + '.zip')
        with zipfile.ZipFile(source) as original, zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as target:
            for entry in original.infolist():
                if mode == 'missing' and entry.filename == 'analysis/input.json':
                    continue
                data = original.read(entry)
                target.writestr(entry, b'{}' if mode == 'changed' and entry.filename == 'analysis/input.json' else data)
            if mode == 'duplicate':
                target.writestr('ANALYSIS/input.json', b'{}')
        with pytest.raises(ValueError):
            exchange.verify(path)


def test_ui_persistent_export_download_import_and_recalculation(exported):
    env, analysis, path, manifest = exported
    r = env[0]; c = client(r)
    response = post(c, {'analysis_id': str(analysis.id), 'idempotency_key': 'audit-export'}, '/packages/export')
    assert response.status_code == 303
    assert exchange.tick(r)
    job = r.connection.execute("SELECT * FROM exchange_job WHERE request_key='audit-export'").fetchone()
    assert job['status'] == 'ready', job['error']
    response = c.get('/packages/' + job['id'] + '/download')
    assert response.status_code == 200
    c.get('/packages')
    response = c.post('/packages/import', data={'csrf': c.cookies['research_csrf']},
                      files={'archive': ('study.zip', response.content, 'application/zip')},
                      headers={'origin': 'http://testserver'}, follow_redirects=False)
    assert response.status_code == 303, response.text
    location = response.headers['location']
    assert c.get(location).status_code == 200
    assert post(c, {}, location + '/recalculate').status_code == 303
    page = c.get(location)
    assert page.status_code == 200, page.text[:2500]
    assert 'stimmt exakt' in page.text


def test_bulk_register_can_exceed_single_object_limit(exported, monkeypatch, tmp_path):
    from uuid import uuid4
    from research_env.domain import Artifact, canonical, digest
    _, _, source, manifest = exported
    with zipfile.ZipFile(source) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    name = 'registers/records.jsonl'
    original = next(json.loads(line) for line in contents[name].splitlines()
                    if json.loads(line)['kind'] == 'Artifact')
    # Many individually small, valid records exceed the limit in aggregate.
    extra = []
    largest_other_file = max(len(data) for key, data in contents.items() if key != name)
    for _ in range(max(6000, largest_other_file // len(canonical(original)) + 20)):
        entity = Artifact.model_validate({**original['payload'], 'id': str(uuid4())})
        extra.append(canonical({'kind': 'Artifact', 'sha256': digest(entity),
                                'payload': entity.model_dump(mode='json')}).encode() + b'\n')
    contents[name] += b''.join(extra)
    manifest['files'][name] = hashlib.sha256(contents[name]).hexdigest()
    contents['manifest.json'] = canonical(manifest).encode()
    hashes = {key: hashlib.sha256(data).hexdigest() for key, data in contents.items()
              if key != 'checksums.sha256'}
    contents['checksums.sha256'] = ''.join(sha + '  ' + key + '\n'
        for key, sha in sorted(hashes.items())).encode()
    limit = max(len(data) for key, data in contents.items() if key != name)
    assert len(contents[name]) > limit
    path = tmp_path / 'large-register.zip'
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for key, data in contents.items():
            archive.writestr(key, data)
    monkeypatch.setattr(exchange, 'MAX_FILE', limit)
    assert exchange.verify(path) == manifest


@pytest.mark.parametrize('name,size', [
    ('registers/records.jsonl', 21), ('objects/sha256/example', 11),
    ('registers/other.jsonl', 11),
])
def test_bulk_register_exception_retains_bounded_limits(tmp_path, monkeypatch, name, size):
    monkeypatch.setattr(exchange, 'MAX_FILE', 10)
    monkeypatch.setattr(exchange, 'MAX_REGISTER_FILE', 20, raising=False)
    path = tmp_path / 'oversized.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr(name, b'x' * size)
    with pytest.raises(ValueError, match='Unzulässiger ZIP-Eintrag'):
        exchange.verify(path)


def test_compressed_archive_size_limit_is_enforced(exported, monkeypatch):
    _, _, path, manifest = exported
    monkeypatch.setattr(exchange, 'MAX_ZIP', path.stat().st_size)
    assert exchange.verify(path) == manifest
    monkeypatch.setattr(exchange, 'MAX_ZIP', path.stat().st_size - 1)
    with pytest.raises(ValueError, match='Paketdatei fehlt'):
        exchange.verify(path)

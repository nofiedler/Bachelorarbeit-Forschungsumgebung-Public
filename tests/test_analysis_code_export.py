"""Run the submitted script outside the app and without its PYTHONPATH."""
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from research_env.analysis_code_export import code_archive, REQUIREMENTS
from research_env.analysis_reproduction import COMPUTATION_FILES
from test_exchange import exported


@pytest.fixture
def code_bundle(exported, tmp_path):
    _, analysis, study, _ = exported
    with zipfile.ZipFile(study) as archive:
        snapshot = json.loads(archive.read('analysis/input.json'))
        result = json.loads(archive.read('analysis/analysis.json'))
    content = code_archive(snapshot, result, analysis.id)
    folder = tmp_path / 'submitted-code'
    with zipfile.ZipFile(BytesIO(content)) as archive:
        archive.extractall(folder)  # This is our freshly generated trusted ZIP.
    return folder, content, snapshot, result, study, analysis


def run_script(folder, tmp_path, arguments):
    cwd = tmp_path / 'independent-cwd'
    cwd.mkdir(exist_ok=True)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('RESEARCH_', 'OPENAI_', 'OPENROUTER_', 'PYTHON'))}
    env['PYTHONPATH'] = '/intentionally-unavailable-application'
    return subprocess.run([sys.executable, '-I', str(folder / 'auswerten.py'), *arguments],
                          cwd=cwd, env=env, text=True, capture_output=True, timeout=90)


def test_code_bundle_contains_exact_core_no_raw_records_and_pinned_dependencies(code_bundle):
    folder, content, snapshot, result, _, analysis = code_bundle
    assert code_archive(snapshot, result, analysis.id) == content
    manifest = json.loads((folder / 'manifest.json').read_bytes())
    assert manifest['contains_research_data'] is False
    assert manifest['input_hash'] == analysis.input_hash
    assert set(manifest['requirements']) == REQUIREMENTS
    with zipfile.ZipFile(BytesIO(content)) as archive:
        assert not any(name.startswith(('objects/','registers/','analysis/')) for name in archive.namelist())
        assert not any(name.endswith(('.sqlite3','.env','.key')) for name in archive.namelist())
        assert 'src/research_env/web.py' not in archive.namelist()
        for name in COMPUTATION_FILES:
            assert hashlib.sha256(archive.read('src/research_env/'+name)).hexdigest() == snapshot['source_binding']['files'][name]
        for name, item in manifest['files'].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == item['sha256']


def test_standalone_script_replays_full_package_without_original_app_environment(code_bundle, tmp_path):
    folder, _, _, result, study, _ = code_bundle
    before = hashlib.sha256(study.read_bytes()).hexdigest()
    destination = tmp_path / 'reproduced'
    completed = run_script(folder, tmp_path, ['--studienpaket', str(study), '--ausgabe', str(destination)])
    assert completed.returncode == 0, completed.stdout + completed.stderr
    receipt = json.loads((destination / 'nachrechnung.json').read_bytes())
    assert receipt['exact_result_match'] is True
    assert receipt['run_count'] == len(result['cells'])
    assert receipt['figure_count'] == 13
    assert receipt['timeline_available'] is True
    assert 'pipeline-steps.csv' in receipt['exactly_compared_files']
    assert (destination / 'kontext-verteilungen.pdf').is_file()
    assert json.loads((destination / 'analysis.json').read_bytes()) == result
    assert hashlib.sha256(study.read_bytes()).hexdigest() == before
    assert not list(destination.rglob('*.sqlite3'))


def test_standalone_json_mode_and_tampered_code_fail_closed(code_bundle, tmp_path):
    folder, _, snapshot, result, _, _ = code_bundle
    input_path, expected_path = tmp_path/'input.json', tmp_path/'analysis.json'
    input_path.write_text(json.dumps(snapshot))
    expected_path.write_text(json.dumps(result))
    destination = tmp_path/'json-result'
    args = ['--eingabe',str(input_path),'--vergleich',str(expected_path),'--ausgabe',str(destination)]
    completed = run_script(folder, tmp_path, args)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads((destination/'nachrechnung.json').read_bytes())['timeline_available'] is False
    assert not (destination/'pipeline-steps.csv').exists()
    core = folder/'src/research_env/analysis.py'
    core.write_bytes(core.read_bytes()+b'\n# changed\n')
    destination2 = tmp_path/'must-not-be-created'
    failed = run_script(folder, tmp_path, args[:-1]+[str(destination2)])
    assert failed.returncode != 0 and 'Codedatei wurde verändert' in failed.stderr
    assert not destination2.exists()


def test_code_export_refuses_changed_input_or_arithmetic_source(code_bundle):
    _, _, snapshot, result, _, analysis = code_bundle
    snapshot = json.loads(json.dumps(snapshot))
    snapshot['seed'] += '-changed'
    with pytest.raises(ValueError, match='gehören nicht zusammen'):
        code_archive(snapshot, result, analysis.id)


def test_standalone_rejects_rehashed_inconsistent_timeline_and_duplicate_records(code_bundle, tmp_path):
    """Valid ZIP-level hashes do not excuse contradictions in time provenance."""
    from uuid import uuid4
    from datetime import datetime, timedelta
    from research_env.domain import Event, digest
    from research_env.analysis_code_runner import canonical, package_timeline, verify_package
    folder, _, snapshot, _, study, _ = code_bundle
    binding = json.loads((folder / 'manifest.json').read_bytes())
    with zipfile.ZipFile(study) as archive:
        original = {name: archive.read(name) for name in archive.namelist()}

    def write_variant(name, change):
        members = dict(original)
        change(members)
        presentation = json.loads(members['presentation/manifest.json'])
        for filename, metadata in presentation['files'].items():
            content = members['presentation/' + filename]
            metadata.update(sha256=hashlib.sha256(content).hexdigest(), byte_count=len(content))
        members['presentation/manifest.json'] = canonical(presentation).encode()
        manifest = json.loads(members['manifest.json'])
        manifest['files'] = {filename: hashlib.sha256(members[filename]).hexdigest()
                             for filename in manifest['files']}
        members['manifest.json'] = canonical(manifest).encode()
        hashes = {**manifest['files'], 'manifest.json': hashlib.sha256(members['manifest.json']).hexdigest()}
        members['checksums.sha256'] = ''.join(value+'  '+key+'\n' for key, value in sorted(hashes.items())).encode()
        path = tmp_path / (name + '.zip')
        with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for filename, content in members.items():
                archive.writestr(filename, content)
        verified, _, _ = verify_package(path, binding)
        return path, verified

    def change_source(field, value):
        def change(members):
            name = 'presentation/pipeline-steps-manifest.json'
            timeline = json.loads(members[name])
            timeline['source'][field] = value
            members[name] = canonical(timeline).encode()
        return change

    for field, value in (('cutoff', '2000-01-01T00:00:00+00:00'),
                         ('snapshot_artifact_sha256', '0' * 64),
                         ('snapshot_artifact_id', str(uuid4())),
                         ('event_hashes', {str(uuid4()): '0' * 64})):
        path, manifest = write_variant(field, change_source(field, value))
        with pytest.raises(ValueError, match='Schrittzeitmanifest'):
            package_timeline(path, manifest, snapshot)

    def changed_event(mode):
        def change(members):
            name = 'presentation/pipeline-steps-manifest.json'
            timeline = json.loads(members[name])
            cutoff = datetime.fromisoformat(timeline['source']['cutoff'])
            event = Event(code='SYNTHETIC-standalone-time-proof',
                run_id=next(row['run_id'] for row in snapshot['rows'] if row.get('run_id')),
                sequence=9999, event_type='node_finished', process_id=uuid4(), interval_id=None,
                created_at=cutoff + timedelta(seconds=1 if mode == 'later' else -1),
                happened_at=cutoff - timedelta(seconds=1), evidence_ids=(),
                details={'node': 'analyzer', 'status': 'done'})
            members['registers/records.jsonl'] += (canonical({'kind': 'Event', 'sha256': digest(event),
                'payload': event.model_dump(mode='json')}) + '\n').encode()
            timeline['source']['event_hashes'][str(event.id)] = digest(event) if mode == 'later' else '0' * 64
            members[name] = canonical(timeline).encode()
        return change
    for mode in ('later', 'wrong-event-hash'):
        path, manifest = write_variant(mode, changed_event(mode))
        with pytest.raises(ValueError, match='Schrittzeitmanifest'):
            package_timeline(path, manifest, snapshot)

    def duplicate(members):
        name = 'registers/records.jsonl'
        members[name] += members[name].splitlines()[0] + b'\n'
    path, manifest = write_variant('duplicate-register-id', duplicate)
    with pytest.raises(ValueError, match='Doppelte Registeridentität'):
        package_timeline(path, manifest, snapshot)

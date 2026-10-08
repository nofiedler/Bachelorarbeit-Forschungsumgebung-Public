"""Reporting keeps immutable selections and exact arithmetic separately bound."""
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from research_env import analysis_presentation as presentation
from research_env.analysis import compute
from research_env.analysis_reproduction import COMPUTATION_FILES, computation_binding, computation_compatible
from research_env.analysis_store import source_binding
from research_env.artifacts import IntegrityError
from research_env.config import Settings
from research_env.domain import canonical
from test_analysis import filled_snapshot


def test_replay_requires_every_arithmetic_dependency_but_not_a_historical_renderer():
    historical = source_binding()
    original = deepcopy(historical)
    historical['files']['analysis_charts.py'] = 'a' * 64
    historical['files']['analysis_store.py'] = 'b' * 64
    assert computation_compatible(historical)
    assert historical != original  # Historical full binding is not rewritten.
    for name in COMPUTATION_FILES:
        changed = deepcopy(historical)
        changed['files'][name] = '0' * 64
        assert not computation_compatible(changed), name
        changed['files'].pop(name)
        assert not computation_compatible(changed), name
    for key in ('version', 'schema'):
        changed = deepcopy(historical)
        changed[key] = 'other'
        assert not computation_compatible(changed)
    assert not computation_compatible({})


def test_declared_arithmetic_files_cover_the_actual_relative_import_closure():
    root = Path(__import__('research_env.analysis', fromlist=['compute']).__file__).parent
    pending, reached = ['analysis.py'], set()
    while pending:
        name = pending.pop()
        if name in reached:
            continue
        reached.add(name)
        for node in ast.walk(ast.parse((root / name).read_text())):
            if isinstance(node, ast.ImportFrom) and node.level:
                assert node.level == 1
                dependency = node.module.replace('.', '/') + '.py'
                assert dependency in COMPUTATION_FILES, dependency
                pending.append(dependency)
    assert reached == set(COMPUTATION_FILES)


def test_report_preserves_confirmed_result_and_exports_every_figure_point():
    result = compute(filled_snapshot())
    before = canonical(result)
    files = presentation.presentation_files(result)
    assert canonical(result) == before
    assert files['analysis.json'][0] == before.encode()
    manifest = json.loads(files['manifest.json'][0])
    assert manifest['original_result_sha256'] == hashlib.sha256(before.encode()).hexdigest()
    assert manifest['presentation_binding'] == presentation.presentation_binding()
    dictionary = json.loads(files['data-dictionary.json'][0])
    assert dictionary['metrics']['R1-R6']['unit'] == 'binary'
    assert dictionary['tables']['runs.csv']['row_count'] == len(result['cells'])
    for name, item in manifest['files'].items():
        assert hashlib.sha256(files[name][0]).hexdigest() == item['sha256']
        if name.endswith('.png'):
            stem = name[:-4]
            assert stem + '-data.csv' in files
            points = json.loads(files[stem + '-data.json'][0])
            assert points['figure_data'] == item['figure_data']
            assert points['data_hash'] == result['data_hash']
    assert 'UF2 / H-K' in files['methods.md'][0].decode()
    assert dictionary['csv']['missing'] == 'empty field; inspect associated status/reason'


def test_cached_download_checks_only_requested_file_and_rejects_wrong_data_and_corruption(tmp_path, monkeypatch):
    settings = Settings(*(tmp_path / key for key in ('control','artifacts','checkpoints','staging')))
    result = {'data_hash': 'a' * 64, 'analysis_version': 'test'}
    builds = []
    def render(result, **kwargs):
        builds.append(1)
        content = b'planned_id,value\nexample,1\n'
        manifest = {'data_hash': result['data_hash'],
            'original_result_sha256': hashlib.sha256(canonical(result).encode()).hexdigest(),
            'presentation_binding': presentation.presentation_binding(),
            'files': {'runs.csv': {'sha256': hashlib.sha256(content).hexdigest(),
                                   'mime_type': 'text/csv', 'byte_count': len(content)}}}
        return {'runs.csv': (content, 'text/csv'), 'manifest.json': (canonical(manifest).encode(), 'application/json')}
    monkeypatch.setattr(presentation, 'presentation_files', render)
    first = presentation.presentation_cache(settings, result)
    assert presentation.presentation_cache(settings, result) == first
    assert builds == [1]
    path, mime = presentation.cached_presentation_file(settings, first.name, 'runs.csv', data_hash=result['data_hash'])
    assert mime == 'text/csv' and path.read_bytes().endswith(b'example,1\n')
    assert not settings.database.exists()
    with pytest.raises(IntegrityError):
        presentation.cached_presentation_file(settings, first.name, 'runs.csv', data_hash='b' * 64)
    with pytest.raises(IntegrityError):
        presentation.cached_presentation_file(settings, first.name, '../runs.csv', data_hash=result['data_hash'])
    path.write_bytes(b'changed')
    with pytest.raises(IntegrityError, match='verändert'):
        presentation.cached_presentation_file(settings, first.name, 'runs.csv', data_hash=result['data_hash'])

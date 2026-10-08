"""Regression for the cross-model run's lost helper-file path."""
import json

import pytest

from research_env.role_formats import materialize_tests, test_bundle as bundle
from research_env.run_export import records


def test_test_files_keep_paths_bytes_and_require_once(tmp_path):
    output = {'schema': 'roles-v1', 'role': 'test', 'files': [
        {'path': 'internal/check.php', 'content': "<?php declare(strict_types=1); require_once __DIR__.'/helpers/check.php'; verify();"},
        {'path': 'internal/helpers/check.php', 'content': '<?php function verify(): void {}'}]}
    assert materialize_tests(bundle(output), tmp_path) == '/role-tests/runner.php'
    for item in output['files']:
        assert (tmp_path/item['path']).read_bytes() == item['content'].encode()
    runner = (tmp_path/'runner.php').read_text()
    assert runner.count('require_once base64_decode') == 2
    assert 'eval(' not in runner and 'catch (\\Throwable' in runner


def test_historical_test_script_and_mount_stay_unchanged(tmp_path):
    original = b'<?php // historical wrapper'
    assert materialize_tests(original, tmp_path) == '/internal/tests.php'
    assert (tmp_path/'tests.php').read_bytes() == original


def test_test_bundle_cannot_escape_or_replace_runner(tmp_path):
    for path in ('internal/../../escape.php', 'runner.php'):
        content = json.dumps({'schema':'internal-tests-v2', 'output':{'schema':'roles-v1',
            'role':'test','files':[{'path':path,'content':'<?php'}]}}).encode()
        with pytest.raises(ValueError):
            materialize_tests(content, tmp_path)
    assert not list(tmp_path.iterdir())


def test_csv_record_appendix_preserves_missing_types_and_nested_paths():
    source = {'model':'A/B', 'cases':[{'value':None,'reason':'line1\nline2'},
        {'value':0,'reason':''}], 'a/b~c':[], 'enabled':False}
    rows = {path: json.loads(value) for path,value,_ in records(source)}
    assert rows == {'/model':'A/B','/cases/0/value':None,'/cases/0/reason':'line1\nline2',
        '/cases/1/value':0,'/cases/1/reason':'','/a~1b~0c':[],'/enabled':False}


def test_static_instrument_uses_installed_image_without_relaxing_vendor_lock(tmp_path, monkeypatch):
    from pathlib import Path
    import research_env.static_analysis as module
    locked = json.loads((module.PACKAGE/'static_tool.lock.json').read_text())
    installed = 'sha256:' + 'a' * 64
    (tmp_path/'static_tool.lock.json').write_text(json.dumps(locked))
    (tmp_path/'pipeline_runtime.lock.json').write_text(json.dumps({'php':installed}))
    monkeypatch.setattr(module, 'PACKAGE', tmp_path)
    binding = module.tool_binding()
    assert binding['image'] == installed
    assert {k:v for k,v in binding.items() if k!='image'} == {k:v for k,v in locked.items() if k!='image'}

"""Deterministic, versioned role contract. No model-based repair of envelopes.

Security file bounds are not token/time/cost limits. The entire output is either
accepted or rejected; syntactic/semantic PHP errors remain runner inputs.
"""
import json
import base64
from copy import deepcopy
from pathlib import PurePosixPath

from .artifacts import IntegrityError, relative_path
from .domain import canonical, digest

FORMAT = 'roles-v1'
MAX_FILES = 128
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MODULE_PATHS = ('routes/study.php', 'app/Http/Controllers/Study/**/*.php',
                'app/Study/**/*.php', 'resources/views/study/**/*.blade.php')
TEST_PATHS = ('internal/**/*.php',)
TEXT_FIELDS = {'analyzer': 'analysis', 'planner': 'plan'}
CONTRACT = {'schema': FORMAT, 'max_files': MAX_FILES, 'max_file_bytes': MAX_FILE_BYTES,
            'max_total_bytes': MAX_TOTAL_BYTES, 'module_paths': MODULE_PATHS,
            'test_paths': TEST_PATHS, 'text_fields': TEXT_FIELDS,
            'review': ['changes_required', 'reason', 'findings'],
            'file_roles': ['migrate', 'test', 'repair'], 'strict_keys': True,
            'migrate_required_paths': ['routes/study.php'],
            'file_set': 'migrate full declared module set; repair exactly same set; tests immutable',
            'format_error': 'terminal content failure; no rescue call'}
CONTRACT_HASH = digest(CONTRACT)
EXAMPLES = {
    'analyzer': {'schema': FORMAT, 'role': 'analyzer', 'analysis': 'Your complete analysis as a string.'},
    'planner': {'schema': FORMAT, 'role': 'planner', 'plan': 'Your complete plan as a string.'},
    **{role: {'schema': FORMAT, 'role': role, 'files': [{'path': 'internal/check.php' if role == 'test' else 'routes/study.php', 'content': '<?php /* replace with actual implementation */'}]} for role in ('migrate','test','repair')},
    'review': {'schema': FORMAT, 'role': 'review', 'changes_required': False, 'reason': 'Your reason.', 'findings': []},
}
ROLE_TASKS = {
    'analyzer': 'Analyze the selected legacy module and its migration requirements. Return your analysis, not implementation files.',
    'planner': 'Plan the selected module migration using the analysis. Return your implementation plan, not implementation files.',
    'migrate': 'Implement the selected module in Laravel. Return complete application files within allowed_output_paths, including routes/study.php.',
    'test': 'Generate independent internal PHP checks of the existing migrated module against the public contract. The migrated application is read-only input. Return ONLY test files under internal/, for example internal/check.php. NEVER return routes, controllers, views or repaired application files. Tests must be directly executable PHP that throws exceptions when an assertion fails; the trusted runner already loads Laravel and its autoloader. Do not use PHPUnit test classes or report unexecuted tests as passed.',
    'review': 'Review the existing migrated code and recorded internal test results. Return changes_required, a reason and findings. Do not return files or claim to have executed additional tests.',
    'repair': 'Repair the migrated application using the allowed own-run findings. Return exactly every path listed in required_output_paths, once each, with complete contents. Include unchanged files too; copy their existing full contents. Never return only changed files or a patch. Before replying, compare your output paths with required_output_paths. Do not modify the internal tests.',
}
PROMPTS = {'schema': 'pipeline-prompts-v4', 'roles': {role: (
    'You are the ' + role + '. ' + ROLE_TASKS[role] + ' '
    'Follow roles-v1 exactly. Use only the complete attached base package, shared scaffold and listed own run artifacts. '
    'Return one JSON envelope, no markdown. Every response MUST contain the exact top-level fields '
    '"schema":"roles-v1" and "role":"' + role + '". '
    'Use exactly the following JSON shape, replacing the example values with your work: ' + json.dumps(EXAMPLES[role]) + '. '
    'Do not omit fields or add other top-level keys. Text fields must be strings, not objects. '
    'Work only on the selected module named in the user message. The shared public contract covers all three modules; '
    'apply its requirements for the selected module. It defines the target behavior; do not invent requirements from the legacy vulnerabilities. '
    'The output paths must match allowed_output_paths for your role. Other role outputs are read-only inputs, not a template for your own output. '
    'Do not use tools, research, external results, secrets or hidden tests.'
    ) for role in ('analyzer', 'planner', 'migrate', 'test', 'review', 'repair')}}

PROMPTS_HASH = digest(PROMPTS)


def structured_parameters(parameters=None):
    """Freeze provider-enforced role envelopes into new configuration settings.

    This preserves roles-v1 and the independent local validator. Schemas constrain
    the envelope and writable paths, not PHP correctness or complete repair sets.
    """
    result = deepcopy(parameters or {})
    result.setdefault('all', {}).pop('response_format', None)
    module_path = (r'^(routes/study\.php|'
                   r'(app/Http/Controllers/Study/|app/Study/)([A-Za-z_][A-Za-z0-9_-]*/)*[A-Za-z_][A-Za-z0-9_-]*\.php|'
                   r'resources/views/study/([A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.blade\.php)$')
    test_path = r'^internal/([A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.php$'
    def object_schema(properties):
        return {'type': 'object', 'properties': properties,
                'required': list(properties), 'additionalProperties': False}
    for role in ROLE_TASKS:
        properties = {'schema': {'type': 'string', 'enum': [FORMAT]},
                      'role': {'type': 'string', 'enum': [role]}}
        text = {'type': 'string', 'pattern': r'\S'}
        if role in TEXT_FIELDS:
            properties[TEXT_FIELDS[role]] = dict(text)
        elif role == 'review':
            properties.update(changes_required={'type': 'boolean'}, reason=dict(text),
                              findings={'type': 'array', 'items': dict(text)})
        else:
            properties['files'] = {'type': 'array', 'minItems': 1, 'maxItems': MAX_FILES,
                'items': object_schema({'path': {'type': 'string', 'pattern': test_path if role == 'test' else module_path},
                                        'content': {'type': 'string', 'description': 'Complete file contents, never a patch.'}})}
        result.setdefault(role, {})['response_format'] = {'type': 'json_schema', 'json_schema': {
            'name': 'roles_v1_' + role, 'strict': True, 'schema': object_schema(properties)}}
    return result


def permitted(path, role):
    relative_path(path)
    if role == 'test':
        return path.startswith('internal/') and path.endswith('.php')
    return (path == 'routes/study.php' or
            path.startswith(('app/Http/Controllers/Study/', 'app/Study/')) and path.endswith('.php') or
            path.startswith('resources/views/study/') and path.endswith('.blade.php'))


def validate(content, role, *, expected_paths=None, workspace=None):
    def pairs(items):
        data = {}
        for key, value in items:
            if key in data:
                raise ValueError('Doppelter JSONschlüssel')
            data[key] = value
        return data
    output = json.loads(content, object_pairs_hook=pairs)
    if not isinstance(output, dict) or output.get('schema') != FORMAT or output.get('role') != role:
        raise ValueError('Versionierter Rollenumschlag fehlt')
    if role in TEXT_FIELDS:
        field = TEXT_FIELDS[role]
        if set(output) != {'schema', 'role', field} or not isinstance(output[field], str) or not output[field].strip():
            raise ValueError('Vollständiges Textfeld fehlt')
    elif role == 'review':
        if (set(output) != {'schema', 'role', 'changes_required', 'reason', 'findings'} or
                type(output['changes_required']) is not bool or not isinstance(output['reason'], str) or
                not output['reason'].strip() or not isinstance(output['findings'], list) or
                any(not isinstance(x, str) or not x.strip() for x in output['findings'])):
            raise ValueError('Begründetes Reviewurteil fehlt')
    elif role in ('migrate', 'test', 'repair'):
        if set(output) != {'schema', 'role', 'files'} or not isinstance(output['files'], list):
            raise ValueError('Dateiliste fehlt')
        files = output['files']
        if not 1 <= len(files) <= MAX_FILES:
            raise ValueError('Unzulässige Dateimenge')
        seen, size = set(), 0
        for entry in files:
            if not isinstance(entry, dict) or set(entry) != {'path', 'content'} or not all(isinstance(v, str) for v in entry.values()):
                raise ValueError('path/content Paar erforderlich')
            path, data = entry['path'], entry['content'].encode('utf-8')
            if not permitted(path, role):
                raise IntegrityError('Geschützter Pfad außerhalb Rollenallowlist: '+path)
            if path in seen:
                raise ValueError('Doppelter Pfad')
            seen.add(path)
            if len(data) > MAX_FILE_BYTES:
                raise ValueError('Dateischutzgröße überschritten')
            size += len(data)
            if workspace is not None:
                current = workspace
                for part in PurePosixPath(path).parts:
                    current = current / part
                    if current.is_symlink():
                        raise IntegrityError('Symlink in Übernahmepfad')
                    if current.exists() and current != workspace / path and not current.is_dir():
                        raise IntegrityError('Nichtverzeichnis in Übernahmepfad')
        if size > MAX_TOTAL_BYTES:
            raise ValueError('Gesamte Dateischutzgröße überschritten')
        if role == 'migrate' and 'routes/study.php' not in seen:
            raise ValueError('Erforderliche routes/study.php fehlt')
        if expected_paths is not None and seen != set(expected_paths):
            missing=sorted(set(expected_paths)-seen); extra=sorted(seen-set(expected_paths))
            raise ValueError('Repair verändert die deklarierte Dateimenge. Fehlend: '+(', '.join(missing) or 'keine')+'; zusätzlich: '+(', '.join(extra) or 'keine'))
    else:
        raise ValueError('Unbekannte Rolle')
    return output


def test_script(output):
    # An explicit catch owns exit status before Laravel's global handler can
    # render an uncaught exception and return 0. Real files preserve __DIR__,
    # relative includes and strict_types. require_once also avoids executing
    # a helper twice when another generated file has already included it.
    prelude = ("<?php\ntry {\nchdir('/opt/study');\nrequire '/opt/study/vendor/autoload.php';\n"
               "$app = require '/opt/study/bootstrap/app.php';\n"
               "$app->make(Illuminate\\Contracts\\Console\\Kernel::class)->bootstrap();\n")
    programs = ''.join("require_once base64_decode('" + base64.b64encode(('/role-tests/' + f['path']).encode()).decode() + "');\n"
                       for f in sorted(output['files'], key=lambda x: x['path']))
    epilogue = ("} catch (\\Throwable $failure) {\n"
                "fwrite(STDERR, 'PHP Fatal error: PIPELINE_INTERNAL_THROWABLE ' . get_class($failure) . ': ' . $failure->getMessage() . \"\\n\");\n"
                "exit(1);\n}\n")
    return (prelude + programs + epilogue).encode()


def test_bundle(output):
    validate(canonical(output), 'test')
    return canonical({'schema': 'internal-tests-v2', 'output': output}).encode()


def materialize_tests(content, root):
    """Trusted staging before the test volume is mounted read-only."""
    if content.lstrip().startswith(b'<?php'):
        # Historical artifacts retain their original execution semantics.
        (root / 'tests.php').write_bytes(content)
        return '/internal/tests.php'
    bundle = json.loads(content)
    if set(bundle) != {'schema', 'output'} or bundle['schema'] != 'internal-tests-v2':
        raise IntegrityError('Unbekanntes internes Testpaket')
    output = validate(canonical(bundle['output']), 'test', workspace=root)
    for entry in output['files']:
        path = root / entry['path']
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(entry['content'], encoding='utf-8')
    (root / 'runner.php').write_bytes(test_script(output))
    return '/role-tests/runner.php'

"""Cost-free evidence harness. Output belongs to this invocation, not study data."""
import hashlib
import json
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

import pydantic
import pytest

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(sys.argv[1])
if OUTPUT.exists() and any(OUTPUT.iterdir()):
    raise SystemExit('Nichtleeres Belegverzeichnis: keine historischen Outputs überschreiben')
OUTPUT.mkdir(parents=True, exist_ok=True)
from research_env.evidence import catalog, CONTRACTS

files = sorted([*ROOT.glob('src/research_env/**/*.py'), *ROOT.glob('src/research_env/migrations/*.sql'), *ROOT.glob('src/research_env/*.json'),
                *ROOT.glob('tests/*.py'), *ROOT.glob('tests/*.json'), ROOT / 'scripts/verify_m3.py', ROOT / 'pyproject.toml', ROOT / 'requirements.lock'])
hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
process = subprocess.run([sys.executable, '-m', 'pytest', '-p', 'no:cacheprovider', '-vv',
    '--basetemp', str(OUTPUT / 'pytest-tmp'), '--junitxml', str(OUTPUT / 'junit.xml'), str(ROOT / 'tests')],
    cwd=ROOT, text=True, capture_output=True)
(OUTPUT / 'pytest.log').write_text(process.stdout + process.stderr)
(OUTPUT / 'evidence_schema.json').write_text(json.dumps(catalog(), ensure_ascii=False, indent=2) + '\n')
(OUTPUT / 'domain_json_schemas.json').write_text(json.dumps({k: c.model_json_schema() for k, c in CONTRACTS.items()}, ensure_ascii=False, indent=2) + '\n')
report = {'proof_kind': 'synthetic technical component tests; no scientific/personal/cost approval',
    'at': datetime.now(timezone.utc).isoformat(), 'platform': platform.platform(), 'machine': platform.machine(),
    'python': sys.version, 'sqlite': sqlite3.sqlite_version, 'pydantic': pydantic.__version__, 'pytest': pytest.__version__,
    'source_hashes': hashes, 'software_tree_sha256': hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
    'asset_index_sha256': hashlib.sha256((ROOT / 'assets/study/m2-v0.1/asset-index.json').read_bytes()).hexdigest(),
    'command': process.args, 'returncode': process.returncode, 'status': 'passed' if process.returncode == 0 else 'failed',
    'stdout_file': 'pytest.log', 'junit_file': 'junit.xml'}
(OUTPUT / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(process.stdout)
print(process.stderr)
print(json.dumps(report, indent=2))
raise SystemExit(process.returncode)

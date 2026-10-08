"""Exact instrument identities or one explicitly approved UI/backup release.

Frozen records are never rebound. Measurement inputs retain the complete actual
installed manifest, plus the exact old/new hashes used by this compatibility rule.
"""
import json
from pathlib import Path
from .domain import AssetVersion, digest


def instrument_compatible(register, tool_id, current_manifest, software_commit):
    tool = register.get(tool_id, AssetVersion)
    if tool.asset_type != 'tool': return False
    if tool.manifest_hash == digest(current_manifest): return True
    from .preparation import software_compatible
    if not software_compatible(software_commit): return False
    policy = json.loads(Path(__file__).with_name('execution_compatibility.json').read_text())
    expected = policy.get('instruments', {}).get(software_commit, {}).get(tool.manifest_hash)
    # Old hash is an exact archived instrument, never a broad version prefix.
    # Images, libraries, public contract and scientific scales must match exactly.
    parameters = {key: value for key, value in current_manifest.items() if key != 'sources'}
    return expected is not None and digest(parameters) == expected


def instrument_binding(register, tool_id, current_manifest):
    frozen = register.get(tool_id, AssetVersion).manifest_hash
    actual = digest(current_manifest)
    return {'frozen_manifest_sha256': frozen, 'actual_manifest_sha256': actual,
            'mode': 'exact' if frozen == actual else 'approved-ui-backup-update-2026-10-06'}

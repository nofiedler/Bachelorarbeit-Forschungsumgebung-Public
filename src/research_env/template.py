"""Trusted metadata import and draft template. Never expose asset roots to roles."""
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

from .domain import AssetVersion, Configuration, EffectiveSettings, MAIN_CELLS, Study, StudyPhase

SCOPES = {'scaffold': 'shared_scaffold', 'fixture_suite': 'public_development',
          'reference': 'trusted_evaluator', 'suite': 'trusted_evaluator', 'source_context': 'role_package'}


def available_assets(register, repository_root: Path):
    index_path = repository_root / 'assets/study/m2-v0.1/asset-index.json'
    index = json.loads(index_path.read_text())
    result = {}
    existing = {a.code: a for a in register.all(AssetVersion)}
    for row in index['assets']:
        path = (repository_root / row['manifest']).resolve()
        if not path.is_relative_to(repository_root.resolve()) or sha256(path.read_bytes()).hexdigest() != row['manifest_sha256']:
            raise ValueError(f'Manifest beschädigt/außerhalb Repository: {row["id"]}')
        code = f'M2-v0.1-{row["id"]}'
        if code in existing:
            asset = existing[code]
            if asset.manifest_hash != row['manifest_sha256']:
                raise ValueError('Assetversion geändert; neue Version erforderlich')
        else:
            asset = register.add(AssetVersion(code=code, asset_type=row['type'], manifest_hash=row['manifest_sha256'],
                origin=json.dumps(row['origin'], sort_keys=True), suite_kind=row['suite_kind'], access_scope=SCOPES[row['type']], human_review='open'))
        result[row['id']] = asset
    # Existing public contract/rubric are versioned files, not invented assets.
    for key, filename in (('contract','vertrag.md'), ('rubric','rubrik.md')):
        path = repository_root / 'docs/vertraege/m2-v0.1' / filename
        content_hash = sha256(path.read_bytes()).hexdigest()
        code = f'M2-v0.1-{key}'
        if code in existing:
            asset = existing[code]
            if asset.manifest_hash != content_hash:
                raise ValueError('Vertrag/Rubrik geändert: neue Assetversion erforderlich')
        else:
            asset = register.add(AssetVersion(code=code, asset_type='contract', manifest_hash=content_hash,
                origin=f'versioned public M2 file docs/vertraege/m2-v0.1/{filename}', access_scope='role_package', human_review='open'))
        result[key] = asset
    return result


def draft(register, repository_root: Path, *, title, data_origin, provenance):
    assets = available_assets(register, repository_root)
    study = register.add(Study(code=f'STUDY-{uuid4()}', title=title, design_version='M3-draft-v1', data_origin=data_origin, provenance=provenance))
    phase = register.add(StudyPhase(code=f'PHASE-{uuid4()}', study_id=study.id, purpose='main', provenance=provenance))
    settings = EffectiveSettings(contract_id=assets['contract'].id, rubric_id=assets['rubric'].id, scaffold_id=assets['scaffold'].id, development_suite_id=assets['development'].id,
        holdout_suite_id=assets['holdout_catalog'].id, reference_id=assets['references'].id,
        context_ids={f'{m}-K{k}': assets[f'context-{m}-K{k}'].id for m in ('BF', 'SQL', 'UP') for k in (0, 1)},
        dvwa_commit='b496a5d3de6b967410155e1b7d3e51e9d035eb22')
    versions = {}
    for key, cell in MAIN_CELLS.items():
        conf = register.add(Configuration(code=f'CONF-{uuid4()}', phase_id=phase.id))
        versions[key] = register.version_configuration(conf.id, key, cell, settings)
    return {'study': study, 'phase': phase, 'versions': versions, 'settings': settings,
            'open_fields': settings.open_fields(), 'open_decisions': ('r_C', 'r_E', 'seed', 'pilot_consumption',
                'estimated_cost_and_time', 'manual_feasibility', 'technical_approvals', 'subject_approvals',
                'paid_calls_consent', 'external_backup_destination'),
            'asset_review': {k: v.human_review for k, v in assets.items()}}

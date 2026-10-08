"""Select only the two explicitly versioned public M2 package identities."""
import hashlib
import json
from pathlib import Path
from .artifacts import IntegrityError, read_regular
from .domain import AssetVersion

PACKAGE = Path(__file__).parent
REVISION = 'm2-v0.1-csrf1'

def contract_binding(assets):
    locked=json.loads((PACKAGE/'contract_assets.lock.json').read_text())
    for relative,expected in locked['files'].items():
        data,_=read_regular(assets,relative)
        if hashlib.sha256(data).hexdigest()!=expected:
            raise IntegrityError('Versionierter öffentlicher Vertrag verändert: '+relative)
    return locked

def package_path(register, conf, *, strict=False):
    key=f'{conf.cell.module}-{conf.cell.context}'
    context_id=conf.settings.context_ids.get(key)
    locked=json.loads((PACKAGE/'pipeline_assets.lock.json').read_text())
    actual=register.get(context_id,AssetVersion).manifest_hash if context_id else None
    approved=json.loads((PACKAGE/'contract_assets.lock.json').read_text())['manifest_sha256']
    contract_hash=register.get(conf.settings.contract_id,AssetVersion).manifest_hash if conf.settings.contract_id else None
    for version in ('m2-v0.1',REVISION):
        relative=f'assets/context/{version}/{conf.cell.module}/{conf.cell.context}/manifest.json'
        if actual==locked[relative]:
            if version=='m2-v0.1' and contract_hash==approved:
                raise IntegrityError('Neue Vertragsrevision verlangt Kontextpaket mit öffentlichem Addendum')
            if version==REVISION:
                if contract_hash!=approved:
                    raise IntegrityError('Revidiertes Kontextpaket verlangt dieselbe Vertragsrevision')
            return relative.replace('manifest.json','package.txt')
    if strict or contract_hash==approved or not register._is_mock(conf):raise IntegrityError('Zugewiesenes Paket/Kontextasset ist kein gebundenes Modul-/K0-/K1-Manifest')
    # Existing synthetic M4 fixtures have no real context manifests. They keep
    # their original package; production provisioning always uses strict=True.
    return f'assets/context/m2-v0.1/{conf.cell.module}/{conf.cell.context}/package.txt'

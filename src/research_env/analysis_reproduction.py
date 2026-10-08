"""Compatibility of arithmetic over an already fixed input, not input selection.

The historical source binding is preserved in full. Only the dependency closure
of ``analysis.compute`` is needed to replay that pure calculation. A changed
selector or renderer cannot silently become a new historical selection/result.
"""
import hashlib
from pathlib import Path

from .analysis import INPUT_SCHEMA, VERSION


COMPUTATION_FILES = ('analysis.py', 'analysis_metrics.py', 'domain.py', 'matrix.py', 'numeric.py')


def computation_binding():
    root = Path(__file__).parent
    return {'version': VERSION, 'schema': INPUT_SCHEMA,
            'files': {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                      for name in COMPUTATION_FILES}}


def computation_compatible(historical):
    """Fail closed on every arithmetic dependency and input/version boundary."""
    current = computation_binding()
    if not isinstance(historical, dict):
        return False
    return (historical.get('version') == current['version'] and
            historical.get('schema') == current['schema'] and
            isinstance(historical.get('files'), dict) and
            all(historical['files'].get(name) == sha for name, sha in current['files'].items()))


def historical_computation_binding(historical):
    return {'version': historical.get('version'), 'schema': historical.get('schema'),
            'files': {name: historical.get('files', {}).get(name) for name in COMPUTATION_FILES}}

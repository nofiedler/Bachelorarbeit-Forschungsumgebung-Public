"""Portable frozen ordering; independent of Python's random implementation."""
from hashlib import sha256
from uuid import UUID, uuid5

from .domain import MAIN_CELLS

ALGORITHM = 'sha256-fisher-yates-v1'


def matrix(freeze_id: UUID, r_c: int, r_e: int, seed: str):
    if type(r_c) is not int or type(r_e) is not int or not 0 < r_e <= r_c or not seed:
        raise ValueError('Positive r_C/r_E mit r_E≤r_C und Seed erforderlich')
    result = []
    for b in range(1, r_c + 1):
        cells = sorted(k for k in MAIN_CELLS if k.startswith('C-') or b <= r_e)
        # SHA256 seed UTF8 + NUL + decimal block + NUL + decimal step.
        # Digest interpreted big endian; modulo(i+1) defines v1 precisely.
        for step, i in enumerate(range(len(cells) - 1, 0, -1)):
            entropy = sha256(f'{seed}\0{b}\0{step}'.encode()).digest()
            j = int.from_bytes(entropy, 'big') % (i + 1)
            cells[i], cells[j] = cells[j], cells[i]
        block_id = uuid5(freeze_id, f'block:{b}')
        for cell in cells:
            result.append({'id': str(uuid5(block_id, cell)), 'block_id': str(block_id),
                           'block_index': b, 'in_b_e': b <= r_e, 'cell_key': cell,
                           'position': len(result) + 1})
    return tuple(result)


def reference_views(rows):
    """Both explorations reuse the same planned control ID, never new observations."""
    controls = {r['block_id']: r['id'] for r in rows if r['in_b_e'] and r['cell_key'] == 'C-SQL-1'}
    return {'model_assignment': controls, 'components': dict(controls)}

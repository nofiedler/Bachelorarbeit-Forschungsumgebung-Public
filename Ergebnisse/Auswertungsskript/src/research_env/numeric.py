"""Explicit reproducible Decimal context and exact finite sums.

No ambient precision, rounding, exponent limits, flags or traps are inherited.
Exact addition derives only the precision needed by the finite operands.
"""
from functools import wraps
from decimal import localcontext
from decimal import (Context, Decimal, DivisionByZero, InvalidOperation,
    Overflow, ROUND_HALF_EVEN, MAX_EMAX, MIN_EMIN)


def context(precision=80):
    return Context(prec=precision, rounding=ROUND_HALF_EVEN, Emin=MIN_EMIN,
        Emax=MAX_EMAX, capitals=1, clamp=0, flags=[],
        traps=[InvalidOperation, DivisionByZero, Overflow])


def deterministic(function):
    """Isolate a persistent numeric operation, including model validation."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        with localcontext(context()):
            return function(*args, **kwargs)
    return wrapped


def exact_precision(values):
    values = tuple(values)
    if any(not isinstance(x, Decimal) or not x.is_finite() for x in values):
        raise ValueError('Finite Decimal operands required')
    if not values:
        return 1
    scale = min(x.as_tuple().exponent for x in values)
    digits = max(len(x.as_tuple().digits)+x.as_tuple().exponent-scale for x in values)
    return max(1, digits + len(str(len(values))) + 1)


def sum_exact(values):
    values = tuple(values)
    with localcontext(context(exact_precision(values))):
        return sum(values, Decimal(0))

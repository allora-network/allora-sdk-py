"""
Canonical decimal serialization matching the chain's signature-verification format.

The Allora chain stores inference values as `math.Dec` (a wrapper around
`cockroachdb/apd` v3). When the chain re-marshals a worker data bundle to
verify the signature, it calls `Dec.Marshal()`, which delegates to apd's
`MarshalText()` — equivalent to apd's `Text('G')` formatter.

If the SDK signs a payload containing the value as `"1.2345e-07"` (Python's
`str()` of `1.2345e-7`), the chain re-emits it as `"1.2345E-7"` and the
signature fails. This module produces the exact `Text('G')` form that the
chain expects, so the bytes the worker signs match the bytes the chain
verifies.

Reference: `cockroachdb/apd/v3/format.go` (Append/fmtE/fmtF) and
`allora-network/allora-chain/math/dec.go` (Dec.Marshal).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Union

DecInput = Union[float, str, Decimal, int]

# Matches apd's `adjExponentLimit` for 'g'/'G' format.
_ADJ_EXPONENT_LIMIT = -6

# Matches apd's `lowestZeroNegativeCoefficientCockroach`. Below this, apd
# falls back to exponent form for zero values rather than padding with zeros.
_LOWEST_ZERO_NEG_COEFF = -2000


def canonicalize_dec(value: DecInput) -> str:
    """
    Convert a numeric value to the canonical string form the chain uses for signing.

    Matches `apd.Decimal.MarshalText()` → `Text('G')` byte-for-byte for any
    finite decimal that apd can parse.

    Args:
        value: The value to canonicalize. Floats are routed through `str()`
            to avoid binary-float artifacts; strings are parsed directly.

    Returns:
        The canonical string. Use this as the `value` field of an Inference
        before signing.

    Raises:
        ValueError: if the input is non-finite (NaN, +/-Infinity) or unparseable.
    """
    d = _to_decimal(value)

    if not d.is_finite():
        raise ValueError(f"non-finite decimal not allowed: {value!r}")

    sign, digits_tuple, exponent = d.as_tuple()
    # `exponent` is `int` for finite decimals (the type union with strings is
    # only for special values, which we've excluded).
    assert isinstance(exponent, int)

    digits = "".join(str(x) for x in digits_tuple)
    digit_len = len(digits)
    is_zero = all(x == 0 for x in digits_tuple)

    # apd's 'G' formatter pads digit_len for zero values with negative
    # exponents (down to a floor), so they can be rendered as "0.000..."
    # in fmtF rather than "0E-N". See apd format.go.
    if is_zero and exponent < 0 and exponent >= _LOWEST_ZERO_NEG_COEFF:
        digit_len += -exponent

    adj = exponent + digit_len - 1
    sign_str = "-" if sign else ""

    if exponent <= 0 and adj >= _ADJ_EXPONENT_LIMIT:
        return sign_str + _fmt_f(digits, exponent)
    return sign_str + _fmt_e(digits, exponent)


def _to_decimal(value: DecInput) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        # str(float) avoids leaking the binary representation that
        # Decimal(float) would expose (e.g., Decimal(0.1) ≠ Decimal("0.1")).
        try:
            return Decimal(str(value))
        except InvalidOperation as e:
            raise ValueError(f"cannot canonicalize float {value!r}: {e}") from e
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation as e:
            raise ValueError(f"cannot parse decimal string {value!r}: {e}") from e
    raise TypeError(f"unsupported type for canonicalize_dec: {type(value).__name__}")


def _fmt_f(digits: str, exponent: int) -> str:
    """Replicate apd's fmtF (decimal-point form, no exponent)."""
    if exponent >= 0:
        return digits + "0" * exponent
    left = -exponent - len(digits)
    if left >= 0:
        return "0." + "0" * left + digits
    offset = -left
    return digits[:offset] + "." + digits[offset:]


def _fmt_e(digits: str, exponent: int) -> str:
    """Replicate apd's fmtE (scientific form with uppercase E and explicit sign)."""
    adj = exponent + len(digits) - 1
    body = digits[0]
    if len(digits) > 1:
        body += "." + digits[1:]
    if adj < 0:
        return f"{body}E-{-adj}"
    return f"{body}E+{adj}"

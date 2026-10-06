"""Double formatting used by the CARD4L XML and JSON.

The metadata embeds doubles whose string form is fixed by the CARD4L output
format and differs from Python's ``repr``:

* decimal notation only when ``1e-3 <= |value| < 1e7``,
* scientific notation (``d.dddEnn``, uppercase ``E``) otherwise,
* always at least one fractional digit,
* the shortest digit sequence that round-trips.

So ``5405000454.33435`` renders as ``5.40500045433435E9`` rather than
``5405000454.33435``.
"""

from __future__ import annotations

import math
from decimal import Decimal


def format_double(x: float) -> str:
    """Render a finite double in the format described above."""
    if x != x:  # NaN
        return "NaN"
    if x == math.inf:
        return "Infinity"
    if x == -math.inf:
        return "-Infinity"

    if x == 0.0:
        # Distinguish +0.0 from -0.0.
        return "-0.0" if math.copysign(1.0, x) < 0 else "0.0"

    negative = x < 0
    d = Decimal(repr(abs(x)))
    _sign, digits, exp = d.as_tuple()
    digit_str = "".join(str(dig) for dig in digits)

    # Normalize: drop trailing zeros from the significant digits (keeping the
    # value unchanged by raising the exponent), giving the shortest mantissa
    # (e.g. 1.2345678E7, not 1.23456780E7).
    stripped = digit_str.rstrip("0")
    if stripped == "":
        stripped = "0"
    exp += len(digit_str) - len(stripped)
    digit_str = stripped
    n = len(digit_str)

    # Scientific exponent E: value = d0.d1d2... * 10^E.
    sci_exp = (n - 1) + exp

    if -3 <= sci_exp < 7:
        text = _to_plain(digit_str, sci_exp)
    else:
        text = _to_scientific(digit_str, sci_exp)

    return "-" + text if negative else text


def _to_plain(digit_str: str, sci_exp: int) -> str:
    n = len(digit_str)
    if sci_exp >= 0:
        int_len = sci_exp + 1
        if n <= int_len:
            int_part = digit_str + "0" * (int_len - n)
            frac_part = "0"
        else:
            int_part = digit_str[:int_len]
            frac_part = digit_str[int_len:]
    else:
        int_part = "0"
        frac_part = "0" * (-sci_exp - 1) + digit_str
    return int_part + "." + frac_part


def _to_scientific(digit_str: str, sci_exp: int) -> str:
    first = digit_str[0]
    rest = digit_str[1:] or "0"
    return "%s.%sE%d" % (first, rest, sci_exp)

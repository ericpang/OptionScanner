"""Small numeric helpers. Every helper returns None instead of guessing when inputs are missing."""
import math
import re
from typing import Iterable, Optional

SYMBOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_]{0,14}(\.[A-Za-z])?\.[A-Za-z]{2,3}$")  # allows share classes: BRK.B.US


def num(x) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return None if (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else float(x)
    if isinstance(x, str):
        s = x.strip().replace(",", "").replace("%", "")
        if s in ("", "-", "--", "N/A", "n/a", "null", "None"):
            return None
        mult = 1.0
        if s[-1:] in "KMBT" and len(s) > 1:
            mult = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[s[-1]]
            s = s[:-1]
        try:
            return float(s) * mult
        except ValueError:
            return None
    return None


def sdiv(a, b) -> Optional[float]:
    a, b = num(a), num(b)
    if a is None or b is None or b == 0:
        return None
    return a / b


def growth(prev, cur) -> Optional[float]:
    """Simple growth; None when the base is zero or negative (not meaningful)."""
    prev, cur = num(prev), num(cur)
    if prev is None or cur is None or prev <= 0:
        return None
    return cur / prev - 1


def cagr(first, last, years) -> Optional[float]:
    first, last = num(first), num(last)
    if first is None or last is None or first <= 0 or last <= 0 or years <= 0:
        return None
    return (last / first) ** (1 / years) - 1


def vals(xs: Iterable) -> list:
    return [v for v in (num(x) for x in xs) if v is not None]


def mean(xs: Iterable) -> Optional[float]:
    v = vals(xs)
    return sum(v) / len(v) if v else None


def avg2(a, b) -> Optional[float]:
    a, b = num(a), num(b)
    if a is None:
        return b
    if b is None:
        return a
    return (a + b) / 2


def norm_key(k: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(k).lower())


def r(x, nd=4):
    x = num(x)
    return None if x is None else round(x, nd)


def g(d: Optional[dict], key: str):
    """Safe get on a possibly-None dict."""
    return None if not d else d.get(key)

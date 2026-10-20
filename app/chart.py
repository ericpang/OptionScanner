"""EMA 20/50/200, volume profile (POC, value area) and chart signals. Computed server-side."""
from typing import Optional

import numpy as np

from . import config

RANGES = {"6m": 126, "1y": 252, "2y": 504, "5y": 1260}


def ema(values: list[float], n: int) -> list[Optional[float]]:
    """EMA seeded with the SMA of the first n values; None before that."""
    out: list[Optional[float]] = [None] * len(values)
    if len(values) < n:
        return out
    k = 2 / (n + 1)
    e = float(np.mean(values[:n]))
    out[n - 1] = e
    for i in range(n, len(values)):
        e = values[i] * k + e * (1 - k)
        out[i] = e
    return out


def volume_profile(bars: list[dict], bins: int = config.PROFILE_BINS, area: float = config.VALUE_AREA) -> dict:
    """Spread each bar's volume evenly over the price bins between its low and high."""
    lo = min(b["l"] for b in bars)
    hi = max(b["h"] for b in bars)
    if hi <= lo:
        return {"bins": [], "poc": None, "vah": None, "val": None}
    edges = np.linspace(lo, hi, bins + 1)
    vol = np.zeros(bins)
    for b in bars:
        i0 = min(bins - 1, max(0, int(np.searchsorted(edges, b["l"], side="right") - 1)))
        i1 = min(bins - 1, max(0, int(np.searchsorted(edges, b["h"], side="left") - 1)))
        i1 = max(i0, i1)
        vol[i0:i1 + 1] += b["v"] / (i1 - i0 + 1)
    poc = int(vol.argmax())
    total = vol.sum()
    a = b_ = poc
    got = vol[poc]
    while got < area * total and (a > 0 or b_ < bins - 1):
        up = vol[b_ + 1] if b_ < bins - 1 else -1
        dn = vol[a - 1] if a > 0 else -1
        if up >= dn:
            b_ += 1
            got += vol[b_]
        else:
            a -= 1
            got += vol[a]
    mid = lambda i: float((edges[i] + edges[i + 1]) / 2)
    return {
        "bins": [{"price_low": float(edges[i]), "price_high": float(edges[i + 1]), "volume": float(vol[i]),
                  "in_value_area": a <= i <= b_} for i in range(bins)],
        "poc": mid(poc), "vah": float(edges[b_ + 1]), "val": float(edges[a]),
    }


def build(klines: list[dict], range_: str = "1y", bins: int = config.PROFILE_BINS) -> dict:
    n = RANGES.get(range_, 252)
    closes = [k["c"] for k in klines]
    emas = {p: ema(closes, p) for p in config.EMA_PERIODS}
    start = max(0, len(klines) - n)
    vis = klines[start:]
    prof = volume_profile(vis, bins) if vis else {"bins": [], "poc": None, "vah": None, "val": None}
    px = closes[-1]

    def above(p):
        e = emas[p][-1]
        return None if e is None else px > e

    cross = "none"
    e50, e200 = emas[50], emas[200]
    for i in range(len(closes) - 1, max(len(closes) - 61, 200), -1):  # last ~60 sessions
        if None in (e50[i], e200[i], e50[i - 1], e200[i - 1]):
            break
        if e50[i - 1] <= e200[i - 1] and e50[i] > e200[i]:
            cross = "golden"
            break
        if e50[i - 1] >= e200[i - 1] and e50[i] < e200[i]:
            cross = "death"
            break
    pva = None
    if prof["vah"] is not None:
        pva = "above" if px > prof["vah"] else "below" if px < prof["val"] else "inside"
    return {
        "range": range_, "approx_profile": "profile estimated from daily bars",
        "candles": [{"t": k["t"], "o": k["o"], "h": k["h"], "l": k["l"], "c": k["c"], "v": k["v"]} for k in vis],
        "ema": {str(p): [None if x is None else round(x, 4) for x in emas[p][start:]] for p in config.EMA_PERIODS},
        "volume_profile": prof,
        "signals": {"above_ema": {str(p): above(p) for p in config.EMA_PERIODS},
                    "cross_50_200": cross, "price_vs_value_area": pva,
                    "ema200_available": emas[200][-1] is not None},
    }

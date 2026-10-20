"""Option chain parsing, OCC symbols, Black-Scholes price and Greeks, and the IV environment.

The Longbridge CLI gives last prices and IV per strike but no bid/ask, no Greeks and no reliable open interest,
so Greeks here are Black-Scholes values computed from each contract's own IV (European-style: early exercise and
discrete dividends are ignored). They are model outputs and are labelled that way in the UI.
"""
import json
import math
import re
from datetime import date, datetime
from typing import Optional

from . import config
from .metrics import hv
from .util import num

_SQRT2 = math.sqrt(2.0)
IV_HISTORY = config.DATA_DIR / "iv_history.json"


# --------------------------------------------------------------------------------------------- symbols
def occ_symbol(underlying: str, expiry: str, kind: str, strike: float) -> str:
    """AAPL.US, 2026-11-20, C, 327.5 -> AAPL261120C327500.US (strike x 1000, no zero padding, .US suffix)."""
    ticker, _, market = underlying.rpartition(".")      # BRK.B.US -> ticker BRK.B, market US
    ticker = ticker.replace(".", "")                      # contracts on share classes drop the dot: BRKB261120C500000.US
    yy, mm, dd = expiry[2:4], expiry[5:7], expiry[8:10]
    return f"{ticker}{yy}{mm}{dd}{kind.upper()}{int(round(strike * 1000))}.{market or 'US'}"


def parse_occ(occ: str) -> Optional[dict]:
    """AAPL261120C327500.US -> {symbol: AAPL.US, expiry: 2026-11-20, kind: C, strike: 327.5}."""
    m = re.match(r"^([A-Z][A-Z0-9]{0,5})(\d{6})([CP])(\d{1,9})\.([A-Z]{2,3})$", occ or "")
    if not m:
        return None
    t, ymd, kind, k, mk = m.groups()
    try:
        expiry = date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:])).isoformat()
    except ValueError:
        return None
    return {"symbol": f"{t}.{mk}", "expiry": expiry, "kind": kind, "strike": int(k) / 1000}


def dte(expiry: str, today: Optional[date] = None) -> int:
    return (date.fromisoformat(expiry) - (today or date.today())).days


# --------------------------------------------------------------------------------------------- Black-Scholes
def _cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


def _pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def years(days: float) -> float:
    return max(days, 0.5) / 365.0


def bs_price(S: float, K: float, T: float, r: float, q: float, sigma: float, kind: str) -> float:
    if T <= 0 or sigma <= 0:
        return max(S - K, 0.0) if kind == "C" else max(K - S, 0.0)
    sq = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / sq
    d2 = d1 - sq
    if kind == "C":
        return S * math.exp(-q * T) * _cdf(d1) - K * math.exp(-r * T) * _cdf(d2)
    return K * math.exp(-r * T) * _cdf(-d2) - S * math.exp(-q * T) * _cdf(-d1)


def greeks(S: float, K: float, T: float, r: float, q: float, sigma: float, kind: str) -> dict:
    """delta, gamma, theta (per calendar day), vega (per 1 IV point) for ONE share of the underlying."""
    if T <= 0 or sigma <= 0:
        itm = (S > K) if kind == "C" else (S < K)
        return {"delta": (1.0 if kind == "C" else -1.0) if itm else 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    sq = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / sq
    d2 = d1 - sq
    eq, er = math.exp(-q * T), math.exp(-r * T)
    gamma = eq * _pdf(d1) / (S * sq)
    vega = S * eq * _pdf(d1) * math.sqrt(T) / 100.0
    common = -S * eq * _pdf(d1) * sigma / (2 * math.sqrt(T))
    if kind == "C":
        delta = eq * _cdf(d1)
        theta = common - r * K * er * _cdf(d2) + q * S * eq * _cdf(d1)
    else:
        delta = -eq * _cdf(-d1)
        theta = common + r * K * er * _cdf(-d2) - q * S * eq * _cdf(-d1)
    return {"delta": delta, "gamma": gamma, "theta": theta / 365.0, "vega": vega}


def lognormal_cdf(x: float, S: float, T: float, r: float, q: float, sigma: float) -> float:
    """P(S_T <= x) under the risk-neutral lognormal model."""
    if x <= 0:
        return 0.0
    if x == math.inf:
        return 1.0
    m = (r - q - 0.5 * sigma * sigma) * T
    return _cdf((math.log(x / S) - m) / (sigma * math.sqrt(T)))


# --------------------------------------------------------------------------------------------- chain rows
def parse_chain_rows(raw: list) -> list[dict]:
    """Normalise `option chain --date` rows to floats."""
    rows = []
    for r in raw or []:
        if not isinstance(r, dict):
            continue
        k = num(r.get("strike"))
        if k is None:
            continue
        rows.append({
            "strike": k,
            "call_last": num(r.get("call_last")), "call_iv": num(r.get("call_iv")), "call_vol": num(r.get("call_vol")) or 0,
            "put_last": num(r.get("put_last")), "put_iv": num(r.get("put_iv")), "put_vol": num(r.get("put_vol")) or 0,
            "standard": str(r.get("standard", "true")).lower() != "false",
        })
    rows.sort(key=lambda r: r["strike"])
    return rows


def row_problem(row: dict, kind: str) -> Optional[str]:
    """Why a side of a chain row cannot be used for a leg (None if it is usable)."""
    side = "call" if kind == "C" else "put"
    last, iv, vol = row.get(f"{side}_last"), row.get(f"{side}_iv"), row.get(f"{side}_vol") or 0
    if not row.get("standard", True):
        return "non_standard"
    if last is None or last < config.OPT_MIN_PRICE:
        return "price_below_min"
    if iv is None or not (config.OPT_IV_RANGE[0] <= iv <= config.OPT_IV_RANGE[1]):
        return "iv_out_of_range"
    if vol < config.OPT_MIN_VOLUME:
        return "no_volume"
    return None


def usable(rows: list[dict], kind: str, dropped: Optional[dict] = None) -> list[dict]:
    out = []
    for r in rows:
        why = row_problem(r, kind)
        if why is None:
            out.append(r)
        elif dropped is not None:
            dropped[why] = dropped.get(why, 0) + 1
    return out


def atm_iv(rows: list[dict], spot: float) -> Optional[float]:
    """IV at the money: average of valid call and put IV at the strikes bracketing spot, interpolated to spot.
    Quiet strikes carry stale IV, so strikes with some volume are preferred."""
    def side_iv(r):
        vs = [r[f"{s}_iv"] for s in ("call", "put")
              if r.get(f"{s}_iv") and config.OPT_IV_RANGE[0] <= r[f"{s}_iv"] <= config.OPT_IV_RANGE[1]
              and (r.get(f"{s}_last") or 0) >= config.OPT_MIN_PRICE]
        return sum(vs) / len(vs) if vs else None

    pts = [(r["strike"], side_iv(r), (r["call_vol"] or 0) + (r["put_vol"] or 0)) for r in rows]
    pts = [(k, iv, v) for k, iv, v in pts if iv is not None]
    traded = [p for p in pts if p[2] > 0]
    pts = traded if len(traded) >= 2 else pts
    below = [p for p in pts if p[0] <= spot]
    above = [p for p in pts if p[0] >= spot]
    if not below and not above:
        return None
    lo = max(below, key=lambda p: p[0]) if below else None
    hi = min(above, key=lambda p: p[0]) if above else None
    if lo and hi and hi[0] > lo[0]:
        w = (spot - lo[0]) / (hi[0] - lo[0])
        return lo[1] * (1 - w) + hi[1] * w
    return (lo or hi)[1]


def skew(rows: list[dict], spot: float, T: float, r: float, q: float) -> dict:
    """OTM put IV minus ATM IV, and 25-delta put IV minus 25-delta call IV (delta from Black-Scholes)."""
    atm = atm_iv(rows, spot)
    best_p = best_c = None
    points = []
    for row in rows:
        k = row["strike"]
        if abs(k / spot - 1) > 0.25:
            continue
        if k < spot and row_problem(row, "P") is None:
            iv = row["put_iv"]
            points.append({"strike": k, "iv": iv, "side": "put"})
            d = abs(greeks(spot, k, T, r, q, iv, "P")["delta"])
            if best_p is None or abs(d - 0.25) < abs(best_p[0] - 0.25):
                best_p = (d, iv, k)
        if k > spot and row_problem(row, "C") is None:
            iv = row["call_iv"]
            points.append({"strike": k, "iv": iv, "side": "call"})
            d = greeks(spot, k, T, r, q, iv, "C")["delta"]
            if best_c is None or abs(d - 0.25) < abs(best_c[0] - 0.25):
                best_c = (d, iv, k)
    otm_put = None
    if atm is not None:
        puts = [p["iv"] for p in points if p["side"] == "put" and 0.05 <= 1 - p["strike"] / spot <= 0.15]
        otm_put = (sum(puts) / len(puts) - atm) if puts else None
    p25c25 = (best_p[1] - best_c[1]) if best_p and best_c and abs(best_p[0] - 0.25) < 0.12 \
        and abs(best_c[0] - 0.25) < 0.12 else None
    return {"otm_put_minus_atm": None if otm_put is None else round(otm_put, 4),
            "put_25d_minus_call_25d": None if p25c25 is None else round(p25c25, 4),
            "points": sorted(points, key=lambda p: p["strike"])}


# --------------------------------------------------------------------------------------------- IV environment
def regime(iv_hv: Optional[float]) -> str:
    if iv_hv is None:
        return "unknown"
    return "rich" if iv_hv > config.IV_RICH else "cheap" if iv_hv < config.IV_CHEAP else "fair"


def pick_expiries(expiries: list[str], dmin: int, dmax: int, today: Optional[date] = None) -> list[str]:
    """Up to OPT_MAX_EXPIRIES expiries: spread across the window plus two further out for the term structure."""
    today = today or date.today()
    dated = sorted((dte(e, today), e) for e in expiries if dte(e, today) >= config.OPT_MIN_DTE_ANY)
    if not dated:
        return []
    mid = (dmin + dmax) // 2
    targets = [dmin, mid, dmax, dmax + 30, dmax + 60]
    chosen: list[str] = []
    for t in targets:
        d, e = min(dated, key=lambda x: abs(x[0] - t))
        if e not in chosen and (abs(d - t) <= max(10, t * 0.35)):
            chosen.append(e)
    return sorted(chosen)[: config.OPT_MAX_EXPIRIES]


def load_iv_history() -> dict:
    try:
        return json.loads(IV_HISTORY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def record_iv(symbol: str, atm: Optional[float]) -> dict:
    """Store today's ATM IV so an IV percentile can be shown once enough observations exist."""
    hist = load_iv_history()
    series = hist.get(symbol, {})
    if atm is not None:
        series[date.today().isoformat()] = round(atm, 4)
        hist[symbol] = dict(sorted(series.items())[-400:])
        try:
            IV_HISTORY.parent.mkdir(parents=True, exist_ok=True)
            tmp = IV_HISTORY.with_suffix(".tmp")
            tmp.write_text(json.dumps(hist), encoding="utf-8")
            tmp.replace(IV_HISTORY)
        except OSError:
            pass
    return hist.get(symbol, {})


def iv_percentile(series: dict, current: Optional[float]) -> dict:
    n = len(series)
    if current is None or n < config.IV_HISTORY_MIN_OBS:
        return {"value": None, "observations": n, "note": f"needs {config.IV_HISTORY_MIN_OBS} stored daily observations "
                                                          f"({n} so far)"}
    vals = list(series.values())
    return {"value": round(100 * sum(1 for v in vals if v < current) / n), "observations": n, "note": ""}


def put_call(daily: Optional[list]) -> dict:
    """Latest put/call volume and open-interest ratios plus a short history, from `option volume daily`."""
    rows = []
    for d in daily or []:
        if not isinstance(d, dict):
            continue
        ts = num(d.get("timestamp"))
        rows.append({"date": datetime.utcfromtimestamp(ts).date().isoformat() if ts else str(d.get("date", ""))[:10],
                     "pc_volume": num(d.get("put_call_volume_ratio")), "pc_oi": num(d.get("put_call_open_interest_ratio")),
                     "volume": num(d.get("total_volume"))})
    rows.sort(key=lambda x: x["date"])
    last = rows[-1] if rows else {}
    return {"latest": last or None, "history": rows}


def realized_vol(klines: list) -> dict:
    return {"hv20": hv(klines, 20), "hv60": hv(klines, 60)}

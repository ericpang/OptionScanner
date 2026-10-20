"""Single-stock option scan: market view from the analysis, IV environment, candidate trades, filters and flags."""
from datetime import date, datetime
from typing import Optional

from . import config, options as O, strategies as ST
from .data import Data, DataError, InputError
from .util import num

VIEWS = ("auto", "bullish", "bearish", "neutral")


# --------------------------------------------------------------------------------------------- view
def derive_view(analysis: Optional[dict], chart_signals: dict, flow_label: Optional[str]) -> dict:
    """Automatic market view: a visible tally of four inputs. >= +3 bullish, <= -3 bearish, else neutral."""
    inputs = []
    total = 0

    def add(name, pts, detail):
        nonlocal total
        total += pts
        inputs.append({"name": name, "points": pts, "detail": detail})

    if analysis and not analysis.get("halted") and "bull" in analysis:
        nb, nr = len(analysis["bull"]), len(analysis["bear"])
        add("Thesis: bull minus bear points", max(-2, min(2, round((nb - nr) / 2))), f"{nb} bull, {nr} bear")
    else:
        inputs.append({"name": "Thesis: bull minus bear points", "points": 0,
                       "detail": "not available (analysis halted or failed); fundamentals not used"})
    above200 = (chart_signals.get("above_ema") or {}).get("200")
    if above200 is not None:
        add("Price vs EMA 200", 1 if above200 else -1, "above" if above200 else "below")
    cross = chart_signals.get("cross_50_200")
    if cross in ("golden", "death"):
        add("EMA 50/200 cross (last ~60 sessions)", 1 if cross == "golden" else -1, cross + " cross")
    if flow_label in ("accumulation", "distribution"):
        add("Institutional flow read", 1 if flow_label == "accumulation" else -1, flow_label)
    if analysis and not analysis.get("halted") and analysis.get("quality", {}).get("applicable"):
        q = analysis["quality"]
        share = q["passing"] / q["applicable"]
        add("Quality scorecards passing", 1 if share >= 0.6 else -1 if share <= 0.3 else 0,
            f"{q['passing']} of {q['applicable']}")
    value = "bullish" if total >= config.VIEW_THRESHOLD else "bearish" if total <= -config.VIEW_THRESHOLD else "neutral"
    return {"value": value, "score": total, "inputs": inputs,
            "rule": f"score >= +{config.VIEW_THRESHOLD} bullish, <= -{config.VIEW_THRESHOLD} bearish, otherwise neutral"}


# --------------------------------------------------------------------------------------------- scan
def scan(d: Data, symbol: str, quote: dict, klines: list, analysis: Optional[dict], chart_signals: dict,
         flow_label: Optional[str], view: str = "auto", dte_min: int = config.OPT_DTE_MIN,
         dte_max: int = config.OPT_DTE_MAX, defined_risk: bool = True, shares: int = 0, include_credit: bool = True,
         min_credit: float = config.OPT_MIN_CREDIT_PCT_WIDTH,
         today: Optional[date] = None) -> dict:
    today = today or date.today()
    if view not in VIEWS:
        raise InputError(f"view must be one of {', '.join(VIEWS)}")
    if dte_min < config.OPT_MIN_DTE_ANY or dte_max < dte_min or dte_max > 400:
        raise InputError(f"expiry window must satisfy {config.OPT_MIN_DTE_ANY} <= dte_min <= dte_max <= 400")
    if not symbol.endswith(".US"):
        return {"symbol": symbol, "supported": False,
                "message": "Option scanning is US-only for now (option volume is documented as US-only and other "
                           "markets were not tested)."}
    spot = num(quote.get("price"))
    r, q = config.RISK_FREE, num(quote.get("dividend_yield")) or 0.0

    expiries = d.call("option_expiries", symbol, optional=False)
    chosen = O.pick_expiries(expiries or [], dte_min, dte_max, today)
    if not chosen:
        return {"symbol": symbol, "supported": True, "candidates": [], "message":
                "No listed expiry within the window (or this stock has no listed options)."}

    eds: dict = {}
    for e in chosen:
        rows = O.parse_chain_rows(d.call("option_chain", symbol, e, default=[]))
        if len(rows) < 5:
            continue  # too thin to build a smile (options-volatility.md)
        n = O.dte(e, today)
        eds[e] = {"rows": rows, "T": O.years(n), "dte": n, "atm_iv": O.atm_iv(rows, spot)}
    if not eds:
        return {"symbol": symbol, "supported": True, "candidates": [],
                "message": "Chains returned fewer than 5 strikes: not enough liquidity to build a volatility smile."}
    ctx = {"symbol": symbol, "spot": spot, "r": r, "q": q, "expiries": eds}

    # ---- IV environment
    hvs = O.realized_vol(klines)
    in_win = [e for e, x in eds.items() if dte_min <= x["dte"] <= dte_max and x["atm_iv"]]
    focus = min(in_win or [e for e, x in eds.items() if x["atm_iv"]],
                key=lambda e: abs(eds[e]["dte"] - (dte_min + dte_max) // 2), default=None)
    atm = eds[focus]["atm_iv"] if focus else None
    hv_ref = hvs["hv60"] or hvs["hv20"]
    iv_hv = atm / hv_ref if atm and hv_ref else None
    reg = O.regime(iv_hv)
    if d.mode == "demo":
        series = {}
    elif d.was_cached("option_chain", symbol, focus):
        series = O.load_iv_history().get(symbol, {})   # recorded when the chain was fetched: not stamped with today again
    else:
        series = O.record_iv(symbol, atm)
    pct = O.iv_percentile(series, atm)
    term = [{"expiry": e, "dte": x["dte"], "atm_iv": None if x["atm_iv"] is None else round(x["atm_iv"], 4)}
            for e, x in sorted(eds.items())]
    sk = O.skew(eds[focus]["rows"], spot, eds[focus]["T"], r, q) if focus else None
    pc = O.put_call(d.call("option_volume_daily", symbol, 30, default=[]))

    # ---- view
    auto = derive_view(analysis, chart_signals, flow_label)
    view_out = {**auto, "mode": "auto" if view == "auto" else "override",
                "value": auto["value"] if view == "auto" else view, "auto_value": auto["value"]}

    # ---- candidates
    dropped: dict = {}
    raw: list = []
    for e in in_win:
        raw += ST.build_for_expiry(view_out["value"], reg, e, eds[e], ctx, not defined_risk, shares, dropped,
                                  include_credit)
    if view_out["value"] == "neutral":  # matrix: neutral view and near-term IV above far-term IV
        raw += ST.build_calendars(ctx, (dte_min, dte_max), dropped)

    seen, uniq = set(), []   # a coarse strike grid can make the narrow and the wide wing the same trade
    for item in raw:
        key = (item[0], tuple((l["occ"], l["side"], l["qty"]) for l in item[1]))
        if key not in seen:
            seen.add(key)
            uniq.append(item)
    raw = uniq

    # ---- confirm every leg exists with a quote; refresh last/IV; flag stale quotes
    occs = sorted({l["occ"] for _, legs, _ in raw for l in legs if l["occ"]})
    quotes: dict = {}
    for i in range(0, len(occs), 30):
        quotes.update(d.call("option_quotes", tuple(occs[i:i + 30]), default={}) or {})
    last_bar = klines[-1]["t"] if klines else None
    out = []
    for name, legs, extra in raw:
        ok = True
        stale = False
        for l in legs:
            if not l["occ"]:
                continue
            qd = quotes.get(l["occ"])
            if not qd:
                dropped["contract_not_found"] = dropped.get("contract_not_found", 0) + 1
                ok = False
                break
            if num(qd.get("last")) and qd["last"] > 0:
                l["last"] = qd["last"]
            if num(qd.get("iv")) and config.OPT_IV_RANGE[0] <= qd["iv"] <= config.OPT_IV_RANGE[1]:
                l["iv"] = qd["iv"]
            l["open_interest"] = qd.get("open_interest")
            l["quote_time"] = qd.get("timestamp")
            if last_bar and qd.get("timestamp") and str(qd["timestamp"])[:10] < last_bar:
                stale = True
        if not ok:
            continue
        c = ST.describe(name, legs, ctx, extra, today)
        if not ST.passes(c, dropped, min_credit):
            continue
        if defined_risk and not c["defined_risk"]:
            dropped["undefined_risk"] = dropped.get("undefined_risk", 0) + 1
            continue
        if stale:
            c["flags"].append("Stale quote: at least one leg has not traded since before the last session, so its "
                              "last price may be out of date.")
        c["flags"].append("Fills at the last price are not guaranteed: the CLI gives no bid or ask.")
        c["payoff"] = payoff_curve(legs, spot, r, q, today=today)
        out.append(c)

    # ---- earnings inside the window
    earn = d.call("earnings_date", symbol)
    for c in out:
        exp = c["far_expiry"] or c["expiry"]
        if earn is None:
            c["flags"].append("Earnings date unknown: check it before trading.")
        elif today.isoformat() <= earn <= exp:
            c["flags"].append(f"Earnings on {earn} fall before expiry: expect an IV drop and a large move.")
    for c in out:
        c.pop("_width", None)
        c.pop("_extra", None)
    out.sort(key=lambda c: (-(c["pop_model"] or 0), c["strategy"]))

    return {
        "symbol": symbol, "supported": True, "spot": spot, "dividend_yield": q, "risk_free": r,
        "view": view_out, "defined_risk_only": defined_risk, "include_credit": include_credit, "min_credit": min_credit, "window": {"dte_min": dte_min, "dte_max": dte_max},
        "iv": {"atm_iv": None if atm is None else round(atm, 4), "focus_expiry": focus,
               "hv20": None if hvs["hv20"] is None else round(hvs["hv20"], 4),
               "hv60": None if hvs["hv60"] is None else round(hvs["hv60"], 4),
               "iv_hv": None if iv_hv is None else round(iv_hv, 2), "regime": reg,
               "thresholds": {"rich": config.IV_RICH, "cheap": config.IV_CHEAP},
               "percentile": pct["value"], "percentile_note": pct["note"], "observations": pct["observations"],
               "term_structure": term, "skew": sk, "put_call": pc},
        "candidates": out, "dropped": dropped, "earnings_date": earn,
        "notes": ["Greeks, probability of profit and expected move are Black-Scholes model outputs, not market data.",
                  "Prices are last trades: no bid or ask is available from Longbridge."],
    }


def payoff_curve(legs: list[dict], spot: float, r: float, q: float, span: float = 0.3, n: int = 61,
                 today: Optional[date] = None) -> dict:
    """Payoff at the nearest expiry and the theoretical value today, per contract set in dollars."""
    from .options import bs_price, dte, years
    xs = [spot * (1 - span + 2 * span * i / (n - 1)) for i in range(n)]
    at_exp = [round(ST.pnl_at(legs, x, spot, r, q, today=today) * ST.MULT, 2) for x in xs]
    qty = max((l["qty"] for l in legs if l["kind"] != "S"), default=1)
    now = []
    for x in xs:
        tot = 0.0
        for l in legs:
            if l["kind"] == "S":
                tot += l["qty"] * (x - spot)
                continue
            v = bs_price(x, l["strike"], years(dte(l["expiry"], today)), r, q, l["iv"], l["kind"])
            tot += (1 if l["side"] == "buy" else -1) * l["qty"] * (v - l["last"])
        now.append(round(tot * ST.MULT, 2))
    return {"prices": [round(x, 2) for x in xs], "at_expiry": at_exp, "today_model": now, "contracts": qty}

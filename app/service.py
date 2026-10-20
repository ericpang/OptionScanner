"""Orchestrates data fetching and the calculators for each API endpoint."""
import re

from . import buffett, chart, config, flows, graham, metrics as M, news, quality as Q, reconcile, thesis, valuation
from .data import Data, DataError, InputError
from .util import num

SYMBOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_]{0,14}(\.[A-Za-z])?\.[A-Za-z]{2,3}$")  # allows share classes: BRK.B.US
KLINE_COUNT = 1500


def check_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if not SYMBOL_RE.match(s):
        raise InputError("Use the CODE.MARKET format, for example AAPL.US, 700.HK, 600519.SH, D05.SG")
    return s


def _klines(d: Data, sym: str):
    return d.call("kline", sym, KLINE_COUNT, optional=False)


def _snapshot(quote: dict) -> dict:
    keys = ("symbol", "name", "currency", "price", "market_cap", "pe", "pb", "high52", "low52", "industry")
    return {k: quote.get(k) for k in keys}


def get_chart(d: Data, sym: str, range_: str, bins: int) -> dict:
    out = chart.build(_klines(d, sym), range_, max(10, min(bins, 120)))
    out["symbol"] = sym
    return out


def get_flows(d: Data, sym: str) -> dict:
    k = _klines(d, sym)
    out = flows.build(k, d.call("insider", sym), d.call("ownership", sym), d.call("capital_flow", sym, default=[]))
    out["symbol"] = sym
    out["coverage_note"] = ("13F data covers US-listed stocks only; other markets rely on capital flow, the HK broker "
                            "queue and the volume indicators." if not sym.endswith(".US") else "")
    return out


def get_news(d: Data, sym: str, limit: int) -> dict:
    limit = max(1, min(limit, config.NEWS_MAX))
    items = d.call("news", sym, limit, default=[]) or []
    for it in items[:limit]:
        if not it.get("summary") and hasattr(d.p, "news_detail") and it.get("id"):
            txt = d.call("news_detail", it["id"], default="", quiet=True) or ""  # a deleted article is not a page warning
            txt = " ".join(txt.split())
            it["summary"] = txt if len(txt) <= 240 else txt[:240].rsplit(" ", 1)[0] + "..."
    mood = {
        "analyst_consensus": M._ratings(d.call("analyst", sym), (d.call("quote", sym) or {}).get("price") or 0),
        "market_temperature": d.call("market_temp", sym),
        "community": d.call("topics", sym, default={"posts": None, "volume_vs_30d": None, "latest": []}),
        "short_interest_pct": d.call("short_interest", sym),
    }
    out = news.build(items, d.call("filings", sym, default=[]) or [], mood, limit)
    if not out["news"]:
        out["note"] = "No news returned. Longbridge may have none for this symbol; the app does not invent any."
    out["symbol"] = sym
    return out


def get_options(d: Data, sym: str, view: str = "auto", dte_min: int = config.OPT_DTE_MIN,
                dte_max: int = config.OPT_DTE_MAX, defined_risk: bool = True, shares: int = 0,
                include_credit: bool = True, min_credit: float = config.OPT_MIN_CREDIT_PCT_WIDTH) -> dict:
    from . import option_scan
    quote = d.call("quote", sym, optional=False)
    klines = _klines(d, sym)
    try:  # the view uses the thesis when the analysis is available; otherwise trend and flow only
        analysis = analyze(d, sym)
    except Exception as e:  # any failure in the fundamentals must not take the option scan down with it
        analysis = None
        d.warnings.append(f"analysis unavailable for the market view ({type(e).__name__}: {e}); trend and flow only")
    signals = chart.build(klines)["signals"]
    label = get_flows(d, sym)["net_read"]["label"]
    out = option_scan.scan(d, sym, quote, klines, analysis, signals, label, view, dte_min, dte_max, defined_risk,
                           shares, include_credit, min_credit)
    out["warnings"] = list(d.warnings)
    out["mode"] = d.mode
    return out


def get_payoff(d: Data, sym: str, occs: list, sides: list, qty: int, span: float) -> dict:
    from . import option_scan, options as O, strategies as ST
    if not occs or len(occs) != len(sides) or len(occs) > 6 or any(s not in ("buy", "sell") for s in sides):
        raise InputError("give 1-6 occ values with a matching buy/sell side for each")
    quote = d.call("quote", sym, optional=False)
    quotes = d.call("option_quotes", tuple(occs), default={}) or {}
    legs = []
    for occ, side in zip(occs, sides):
        p, qd = O.parse_occ(occ), quotes.get(occ)
        if not p or p["symbol"].replace(".", "") != sym.replace(".", "") or not qd or not qd.get("iv") or not qd.get("last"):
            raise InputError(f"contract not found or not for {sym}: {occ}")
        legs.append({"side": side, "kind": p["kind"], "strike": p["strike"], "expiry": p["expiry"], "qty": qty,
                     "last": qd["last"], "iv": qd["iv"], "vol": qd.get("volume") or 0, "occ": occ})
    q = num(quote.get("dividend_yield")) or 0.0
    return option_scan.payoff_curve(legs, quote["price"], config.RISK_FREE, q, span)


def analyze(d: Data, sym: str) -> dict:
    quote = d.call("quote", sym, optional=False)
    stm = d.call("statements", sym, optional=False)
    klines = _klines(d, sym)
    if not quote.get("high52") and len(klines) >= 20:  # the quote has no 52-week range: derive it from daily bars
        yr = klines[-252:]
        quote = {**quote, "high52": max(k["h"] for k in yr), "low52": min(k["l"] for k in yr)}
    analyst = d.call("analyst", sym)
    divs = d.call("dividends", sym)
    insiders = d.call("insider", sym)   # None when the feed failed: unavailable is not the same as "no trades"
    ownership = d.call("ownership", sym)
    cap_flow = d.call("capital_flow", sym, default=[])
    peers = d.call("peers", sym, default=[])
    short = d.call("short_interest", sym)
    _fill_shares(d, stm, quote)
    _check_currency(d, sym, stm)
    fetched = d.fetched_at()   # the cache file's time when the data came from it, so the appendix stays truthful
    period = stm["annual"][-1].get("period")

    base = {"symbol": sym, "mode": d.mode, "snapshot": _snapshot(quote), "fetched_at": fetched,
            "warnings": list(d.warnings)}
    rec = reconcile.run(stm, quote)
    base["reconciliation"] = rec
    if rec["status"] == "fail":
        base.update(halted=True, message="Reconciliation failed, so no metrics or scores are shown. " + rec["summary"])
        return base

    m = M.compute(stm, quote, analyst, divs, insiders, klines, short)
    ch = chart.build(klines)
    fl = flows.build(klines, insiders, ownership, cap_flow)
    rh = Q.roic_vs_wacc(stm, quote, m["roic"])
    pz, az, bm = Q.piotroski(stm), Q.altman(stm, quote), Q.beneish(stm, quote)
    sl, mf = Q.sloan(stm), Q.magic_formula(stm, quote, peers)
    bf = buffett.analyze(stm, quote, m, insiders, rh["spread"])
    gh = graham.analyze(stm, quote, m, insiders)

    checks = [  # (name, applicable, passes)
        ("Piotroski F", pz["score"] is not None, pz["score"] is not None and pz["score"] >= 7),
        ("Altman Z", az["score"] is not None, az.get("zone") == "safe"),
        ("Beneish M", bm["score"] is not None, bm.get("flag") is False),
        ("Sloan accruals", sl["ratio"] is not None, sl["ratio"] is not None and abs(sl["ratio"]) <= 0.10),
        ("Magic Formula", mf["rank_ey"] is not None and mf["rank_roc"] is not None,
         (mf["rank_ey"] or 0) >= 50 and (mf["rank_roc"] or 0) >= 50),
        ("Graham", bool(gh.get("applicable")), bool(gh.get("applicable")) and not gh.get("value_trap") and gh.get("adjusted_score", 0) >= 60),
        ("Buffett", bf["stars"]["moat"] is not None, (bf["stars"]["moat"] or 0) >= 4 and (bf["stars"]["financials"] or 0) >= 4),
        ("ROIC vs WACC", rh["spread"] is not None, (rh["spread"] or -1) > 0),
    ]
    qual = {"piotroski": pz, "altman_z": az, "beneish_m": bm, "sloan_accruals": sl, "magic_formula": mf,
            "graham": gh, "buffett": bf, "roic_vs_wacc": rh,
            "passing": sum(1 for _, a, p in checks if a and p), "applicable": sum(1 for _, a, _ in checks if a),
            "checks": [{"name": n, "applicable": a, "pass": bool(a and p)} for n, a, p in checks],
            "pass_rules": "Piotroski >= 7; Altman safe zone; Beneish below -1.78; |Sloan| <= 10%; Magic Formula both "
                          "peer ranks >= 50 (needs peers); Graham adjusted >= 60 and no value trap; Buffett moat and "
                          "financial health >= 4 stars; ROIC above estimated WACC. Graham and Buffett cut-offs are "
                          "this app's own."}
    val = valuation.build(stm, quote, m, qual)
    th = thesis.build(m, qual, bf, gh, ch, fl, quote, val)
    base.update(
        metrics=m, quality=qual, valuation=val, bull=th["bull"], bear=th["bear"], levels=th["levels"], verdict=th["verdict"],
        sources=_sources(period, fetched, d.mode),
        narrative=thesis.narrate({"symbol": sym, "bull": th["bull"], "bear": th["bear"], "verdict": th["verdict"]}),
        warnings=list(d.warnings))
    return base


MARKET_CCY = {"US": "USD", "HK": "HKD", "SH": "CNY", "SZ": "CNY", "SG": "SGD"}


def _check_currency(d: Data, sym: str, stm: dict) -> None:
    """Ratios mix statement figures with the quoted price, so both must be in the same currency."""
    want = MARKET_CCY.get(sym.rsplit(".", 1)[-1])
    have = stm.get("currency")
    if want and have and want != have:
        msg = (f"currency: statements are in {have} but {sym} trades in {want}; ratios that mix price and "
               f"statement figures (EV/EBITDA, FCF yield, NCAV per share, Altman, Magic Formula) are unreliable")
        if msg not in d.warnings:
            d.warnings.append(msg)


def _fill_shares(d: Data, stm: dict, quote: dict) -> None:
    """If the statements carry no share count, derive one for the latest periods from market cap / price.
    This is an approximation (current shares, not weighted average) and is disclosed as a warning."""
    mc, px = num(quote.get("market_cap")), num(quote.get("price"))
    if not mc or not px:
        return
    filled = False
    for key in ("annual", "quarterly"):
        rows = stm.get(key) or []
        if rows and num(rows[-1].get("shares")) is None:
            rows[-1]["shares"] = mc / px
            filled = True
    if filled:
        msg = "shares: statements had no share count; latest period uses market cap / price (approximate)"
        if msg not in d.warnings:
            d.warnings.append(msg)


def _sources(period, fetched, mode) -> list:
    src = "demo generator" if mode == "demo" else "Longbridge CLI"
    rows = [
        ("P/E (TTM)", "price / sum of last 4 quarters diluted EPS", "quote; financial-statement IS qf"),
        ("PEG", "P/E / EPS growth % (forward consensus, else 3y EPS CAGR) - derived", "institution-rating, forecast-eps; financial-statement IS af"),
        ("FCF growth", "FCF = operating CF - capex; YoY and CAGR - derived", "financial-statement CF"),
        ("Debt/EBITDA", "(ST + LT debt) / (operating income + D&A, TTM) - derived", "financial-statement BS, IS, CF"),
        ("Operating margin", "operating income / revenue", "financial-statement IS"),
        ("ROIC", "operating income x (1 - tax rate) / average (equity + debt - cash) - derived", "financial-statement IS, BS"),
        ("ROE", "net income / average equity", "financial-statement IS, BS"),
        ("Net income growth", "YoY, 3y and 5y CAGR (None when base <= 0)", "financial-statement IS"),
        ("Fair value (app estimate)", "DCF of free cash flow to equity with growth fade; earnings power value; Graham number. "
                                      "Not GuruFocus's GF Value - derived", "financial-statement CF, IS, BS; quote; consensus"),
        ("Quality scorecards", "Piotroski 2000, Altman 1968, Beneish 1999, Sloan 1996, Greenblatt - derived", "financial-statement BS, IS, CF"),
        ("EMA / volume profile", "EMA seeded with SMA; profile spreads each bar's volume across its range", "kline day"),
        ("Flow indicators", "OBV, A/D line, CMF(20), up/down volume - derived from daily bars", "kline day"),
        ("Insider / holders", "Form 4 open-market buys and sells; institutional shareholder list", "insider-trades; shareholder"),
        ("Capital flow", "net flow by order size", "capital --flow"),
        ("Short interest", "short-positions rate (US and HK only)", "short-positions"),
    ]
    return [{"field": f, "formula": fm, "source": f"{src}: {s}", "fetched_at": fetched, "period": period or "n/a"}
            for f, fm, s in rows]

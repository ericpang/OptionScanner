"""The nine key metrics plus the additional ones. Missing inputs give None (shown as n/a), never an estimate."""
import math
from datetime import date, timedelta
from typing import Optional

import numpy as np

from . import config
from .util import avg2, cagr, growth, mean, num, r, sdiv, vals


def latest(stm: dict) -> dict:
    return stm["annual"][-1]


def latest_bs(stm: dict) -> dict:
    """The most recent balance sheet: the latest quarter when it has one, else the last fiscal year. Anything that
    combines a balance sheet with today's price or trailing-twelve-month flows must use this, not the fiscal year,
    or it mixes dates (the fiscal year can be three quarters old)."""
    q = stm.get("quarterly") or []
    return q[-1] if q and q[-1].get("total_assets") else latest(stm)


def prior(stm: dict, n=1) -> Optional[dict]:
    a = stm["annual"]
    return a[-1 - n] if len(a) > n else None


def ttm(stm: dict, key: str):
    """(value, basis). Sum of the last 4 quarters when all present, else the latest fiscal year."""
    q = stm.get("quarterly") or []
    if len(q) >= 4:
        v = [num(x.get(key)) for x in q[-4:]]
        if all(x is not None for x in v):
            return sum(v), "ttm"
    a = num(latest(stm).get(key))
    return a, "fy" if a is not None else None


def fcf_of(p: Optional[dict]) -> Optional[float]:
    if not p:
        return None
    o, c = num(p.get("ocf")), num(p.get("capex"))
    return None if o is None or c is None else o - abs(c)


def debt_of(p: dict) -> Optional[float]:
    s, l = num(p.get("st_debt")), num(p.get("lt_debt"))
    if s is None and l is None:
        return None
    return (s or 0) + (l or 0)


def series(stm: dict, fn) -> list:
    return [fn(p) for p in stm["annual"]]


def tax_rate(p: dict) -> float:
    t = sdiv(p.get("tax"), p.get("pretax_income"))
    if t is None or num(p.get("pretax_income")) is None or p["pretax_income"] <= 0:
        return config.DEFAULT_TAX
    return min(max(t, 0.0), 0.4)


def invested_capital(p: dict) -> Optional[float]:
    eq, d, c = num(p.get("equity")), debt_of(p), num(p.get("cash"))
    if eq is None or d is None:
        return None
    return eq + d - (c or 0)


def roic(stm: dict) -> Optional[float]:
    p, pp = latest(stm), prior(stm)
    oi = num(p.get("operating_income"))
    ic = avg2(invested_capital(p), invested_capital(pp) if pp else None)
    if oi is None or not ic or ic <= 0:
        return None
    return oi * (1 - tax_rate(p)) / ic


def hv(klines: list, n: int) -> Optional[float]:
    c = np.array([k["c"] for k in klines[-(n + 1):]], dtype=float)
    if len(c) < n + 1:
        return None
    ret = np.diff(np.log(c))
    return float(ret.std(ddof=1) * math.sqrt(252))


def max_drawdown(klines: list, n=252) -> Optional[float]:
    c = np.array([k["c"] for k in klines[-n:]], dtype=float)
    if len(c) < 20:
        return None
    peak = np.maximum.accumulate(c)
    return float((c / peak - 1).min())


def ret_over(klines: list, days: int) -> Optional[float]:
    if len(klines) <= days:
        return None
    return klines[-1]["c"] / klines[-1 - days]["c"] - 1


def insider_net(trades: Optional[list], months: int):
    """Net open-market buying minus selling over `months`. None when the insider feed is unavailable (trades is
    None), which is different from 0 (the feed worked and there were no trades)."""
    if trades is None:
        return None
    cutoff = (date.today() - timedelta(days=int(months * 30.4))).isoformat()
    buy = sum(num(t.get("value")) or 0 for t in trades if t["type"] == "buy" and t["date"] >= cutoff)
    sell = sum(num(t.get("value")) or 0 for t in trades if t["type"] == "sell" and t["date"] >= cutoff)
    return buy - sell


def insider_partial_from(trades: list, months: int = 12) -> Optional[str]:
    """If the Form 4 feed was cut off (more filings exist than were fetched) and does not reach back `months`,
    return the date the data starts from, so the figure can be labelled partial. Otherwise None."""
    if not trades or not trades[0].get("feed_truncated"):
        return None
    oldest = trades[0].get("feed_oldest")
    cutoff = (date.today() - timedelta(days=int(months * 30.4))).isoformat()
    return oldest if oldest and oldest > cutoff else None


def compute(stm: dict, quote: dict, analyst: Optional[dict], divs: Optional[dict], insiders: list,
            klines: list, short_interest=None) -> dict:
    A, P = latest(stm), prior(stm)
    price = quote["price"]
    mcap = num(quote.get("market_cap"))
    ann = stm["annual"]

    eps_ttm, eps_basis = ttm(stm, "eps")
    pe = sdiv(price, eps_ttm) if eps_ttm and eps_ttm > 0 else None
    if pe is None and eps_ttm is None:
        pe = num(quote.get("pe"))

    # PEG: forward consensus growth first, else 3y historical EPS CAGR (labelled)
    # Growth is always a FRACTION here (1.16 = +116%). The P/E in the numerator must be on the same EPS basis as
    # the growth, so with consensus data it is price / trailing EPS on that basis, not the quoted GAAP P/E.
    a_ = analyst or {}
    g_fwd = num(a_.get("eps_growth_forward"))
    basis, gr, pe_peg, pe_basis, peg_note = None, None, pe, "quoted TTM P/E", None
    if g_fwd is not None and g_fwd > 0:
        gr = g_fwd * 100
        basis = "forward_consensus"
        ttm_b = num(a_.get("eps_ttm_basis"))
        if ttm_b:
            pe_peg, pe_basis = sdiv(price, ttm_b), f"P/E on trailing {a_.get('eps_growth_basis') or 'consensus'}"
        peg_note = (f"consensus {a_.get('eps_growth_basis') or 'EPS'}, next {a_.get('eps_growth_quarters') or '?'} "
                    f"quarters vs the same quarters a year earlier")
    else:
        eps_s = [num(p.get("eps")) for p in ann]
        if len(eps_s) >= 4 and eps_s[-1] is not None:
            c = cagr(eps_s[-4], eps_s[-1], 3)
            if c:
                basis, gr = "historical_3y", c * 100
        if gr is None and len(ann) >= 4:  # statements carry no EPS: use net income growth, and say so
            c = cagr(num(ann[-4].get("net_income")), num(ann[-1].get("net_income")), 3)
            if c:
                basis, gr = "historical_3y_net_income", c * 100
    peg = sdiv(pe_peg, gr) if pe_peg and gr and gr > 0 else None

    fcf_now, fcf_prev = fcf_of(A), fcf_of(P)
    fcf3 = fcf_of(ann[-4]) if len(ann) >= 4 else None

    ebitda = None
    oi_t, _ = ttm(stm, "operating_income")
    da_t, _ = ttm(stm, "da")
    if oi_t is not None and da_t is not None:
        ebitda = oi_t + da_t
    bs = latest_bs(stm)
    debt, cash = debt_of(bs), num(bs.get("cash"))
    d_e = sdiv(debt, ebitda) if ebitda and ebitda > 0 else None
    nd_e = sdiv((debt - (cash or 0)) if debt is not None else None, ebitda) if ebitda and ebitda > 0 else None

    rev_t, _ = ttm(stm, "revenue")
    opm_hist = series(stm, lambda p: sdiv(p.get("operating_income"), p.get("revenue")))
    gm_hist = series(stm, lambda p: sdiv(p.get("gross_profit"), p.get("revenue")))
    roe_hist = []
    for i, p in enumerate(ann):
        eq = avg2(p.get("equity"), ann[i - 1].get("equity") if i else None)
        roe_hist.append(sdiv(p.get("net_income"), eq))
    ni_s = [num(p.get("net_income")) for p in ann]
    rev_s = [num(p.get("revenue")) for p in ann]
    q = stm.get("quarterly") or []
    q_yoy = growth(q[-5].get("net_income"), q[-1].get("net_income")) if len(q) >= 5 else None

    ev = (mcap + (debt or 0) - (cash or 0)) if mcap else None
    ni_t, _ = ttm(stm, "net_income")
    ebt_t, _ = ttm(stm, "pretax_income")
    int_t, _ = ttm(stm, "interest_expense")
    fcf_t = None
    o_t, c_t = ttm(stm, "ocf")[0], ttm(stm, "capex")[0]
    if o_t is not None and c_t is not None:
        fcf_t = o_t - abs(c_t)
    ca, cl = num(bs.get("current_assets")), num(bs.get("current_liabilities"))
    inv = num(bs.get("inventory")) or 0
    div_paid = abs(num(A.get("dividends_paid")) or 0)
    sh_3 = num(ann[-4].get("shares")) if len(ann) >= 4 else None
    bb = abs(num(A.get("buybacks")) or 0)
    c_ema200 = None
    if len(klines) >= 200:
        k = 2 / 201
        e = float(np.mean([x["c"] for x in klines[:200]]))
        for x in klines[200:]:
            e = x["c"] * k + e * (1 - k)
        c_ema200 = price / e - 1
    h52, l52 = num(quote.get("high52")), num(quote.get("low52"))
    t_mean = num((analyst or {}).get("target_mean"))
    surprises = (analyst or {}).get("surprises") or []

    return {
        "pe_ttm": {"value": r(pe, 2), "basis": eps_basis},
        "peg": {"value": r(peg, 2), "growth_basis": basis, "growth_pct": r(gr, 1), "pe_used": r(pe_peg, 1),
                "pe_basis": pe_basis, "note": peg_note},
        "fcf_growth": {"yoy": r(growth(fcf_prev, fcf_now)), "cagr_3y": r(cagr(fcf3, fcf_now, 3)),
                       "fcf": r(fcf_now, 0), "series": [r(fcf_of(p), 0) for p in ann]},
        "debt_to_ebitda": {"gross": r(d_e, 2), "net": r(nd_e, 2), "net_cash": bool(debt is not None and cash is not None and cash > debt),
                           "ebitda": r(ebitda, 0)},
        "analyst_ratings": _ratings(analyst, price),
        "operating_margin": {"ttm": r(sdiv(oi_t, rev_t)), "history": [r(x) for x in opm_hist]},
        "roic": r(roic(stm)),
        "roe": {"latest": r(roe_hist[-1]), "history": [r(x) for x in roe_hist]},
        "net_income_growth": {"yoy": r(growth(ni_s[-2], ni_s[-1])) if len(ni_s) > 1 else None,
                              "cagr_3y": r(cagr(ni_s[-4], ni_s[-1], 3)) if len(ni_s) >= 4 else None,
                              "cagr_5y": r(cagr(ni_s[-6], ni_s[-1], 5)) if len(ni_s) >= 6 else None,
                              "latest_quarter_yoy": r(q_yoy), "series": [r(x, 0) for x in ni_s]},
        "extra": {
            "revenue_growth": {"yoy": r(growth(rev_s[-2], rev_s[-1])) if len(rev_s) > 1 else None,
                               "cagr_3y": r(cagr(rev_s[-4], rev_s[-1], 3)) if len(rev_s) >= 4 else None,
                               "cagr_5y": r(cagr(rev_s[-6], rev_s[-1], 5)) if len(rev_s) >= 6 else None},
            "eps_revisions": {"forward_vs_90d": r(growth((analyst or {}).get("eps_forward_90d"),
                                                         (analyst or {}).get("eps_forward"))),
                              "surprises_last4": [r(x) for x in surprises[:4]]},
            "gross_margin": {"latest": r(gm_hist[-1]), "history": [r(x) for x in gm_hist]},
            "non_operating_share": r(sdiv((ebt_t - oi_t) if ebt_t is not None and oi_t is not None else None, ebt_t)
                                     if ebt_t and ebt_t > 0 else None),
            "net_margin": r(sdiv(ni_t, rev_t)),
            "fcf_margin": r(sdiv(fcf_t, rev_t)),
            "cash_conversion": r(sdiv(fcf_t, ni_t) if ni_t and ni_t > 0 else None, 2),
            "ev_ebitda": r(sdiv(ev, ebitda) if ebitda and ebitda > 0 else None, 1),
            "ps": r(num(quote.get("ps")) or (sdiv(mcap, rev_t) if rev_t else None), 2), "pb": r(quote.get("pb"), 2),
            "fcf_yield": r(sdiv(fcf_t, mcap)),
            "interest_coverage": r(sdiv(oi_t, abs(int_t)) if int_t else None, 1),
            "current_ratio": r(sdiv(ca, cl), 2),
            "quick_ratio": r(sdiv((ca - inv) if ca is not None else None, cl), 2),
            "net_debt_equity": r(sdiv((debt - (cash or 0)) if debt is not None else None, bs.get("equity")), 2),
            "dividend": {"yield": r(quote.get("dividend_yield")),
                         "payout": r(sdiv(div_paid, A.get("net_income")) if A.get("net_income") else None),
                         "years_growth": (divs or {}).get("years_growth")},
            "share_count_change_3y": r(growth(sh_3, A.get("shares"))),
            "buyback_yield": r(sdiv(bb, mcap)),
            "insider_net": {"m3": r(insider_net(insiders, 3), 0), "m6": r(insider_net(insiders, 6), 0),
                            "m12": r(insider_net(insiders, 12), 0), "m12_partial_from": insider_partial_from(insiders)},
            "short_interest_pct": short_interest,
            "beta": r(quote.get("beta"), 2),
            "hv20": r(hv(klines, 20)), "hv60": r(hv(klines, 60)), "max_drawdown_1y": r(max_drawdown(klines)),
            "returns": {"m3": r(ret_over(klines, 63)), "m6": r(ret_over(klines, 126)), "m12": r(ret_over(klines, 252))},
            "from_52w_high": r(price / h52 - 1) if h52 else None,
            "from_52w_low": r(price / l52 - 1) if l52 else None,
            "from_ema200": r(c_ema200),
            "target_upside": r(t_mean / price - 1) if t_mean else None,
        },
    }


def _ratings(analyst: Optional[dict], price: float) -> dict:
    a = analyst or {}
    b, h, s = (num(a.get(k)) for k in ("buy", "hold", "sell"))
    tot = sum(x for x in (b, h, s) if x is not None)
    cons = None
    if tot:
        sc = ((b or 0) * 1 + (h or 0) * 0 + (s or 0) * -1) / tot
        cons = "Buy" if sc > 0.35 else "Sell" if sc < -0.35 else "Hold"
    t = num(a.get("target_mean"))
    return {"buy": b, "hold": h, "sell": s, "consensus": cons, "target_mean": r(t, 2),
            "upside_pct": r(t / price - 1) if t and price else None}

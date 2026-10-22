"""Published quality scorecards: Piotroski F, Altman Z, Beneish M, Sloan accruals, Magic Formula, ROIC vs WACC.

Formulas and thresholds follow the original papers. Missing inputs give None for that item (n/a).
"""
from typing import Optional

from . import config
from .metrics import debt_of, invested_capital, latest, latest_bs, ttm
from .util import avg2, num, r, sdiv

# Longbridge gives the industry's name in Chinese on some accounts (半导体厂商, 多元化银行), so the words are matched in both
# languages: 银行 bank, 保险 insurance, 经纪 brokerage, 金融 finance, 资本市场 capital markets, 资产管理 asset management. A
# REIT is "房地产投资信托（REITs）", which "reit" already matches.
FIN_WORDS = ("bank", "insur", "reit", "broker", "financ", "capital markets", "asset management",
             "银行", "保险", "经纪", "金融", "资本市场", "资产管理")


def is_financial(quote: dict) -> bool:
    s = str(quote.get("industry") or "").lower()
    return any(w in s for w in FIN_WORDS)


def piotroski_annual(stm: dict) -> dict:
    a = stm["annual"]
    if len(a) < 2:
        return {"score": None, "tests": {}, "band": "n/a", "note": "needs 2 fiscal years", "basis": "latest fiscal year"}
    c, p = a[-1], a[-2]
    pp = a[-3] if len(a) > 2 else {}

    def roa(x, prev):
        return sdiv(x.get("net_income"), avg2(x.get("total_assets"), prev.get("total_assets")) if prev else x.get("total_assets"))

    def lev(x):
        return sdiv(x.get("lt_debt"), x.get("total_assets"))

    def cr(x):
        return sdiv(x.get("current_assets"), x.get("current_liabilities"))

    def gm(x):
        return sdiv(x.get("gross_profit"), x.get("revenue"))

    def at(x):
        return sdiv(x.get("revenue"), x.get("total_assets"))

    def gt(x, y):
        return None if x is None or y is None else x > y

    roa_c, roa_p = roa(c, p), roa(p, pp)
    ocf, ni = num(c.get("ocf")), num(c.get("net_income"))
    tests = {
        "roa_positive": None if roa_c is None else roa_c > 0,
        "ocf_positive": None if ocf is None else ocf > 0,
        "roa_improving": gt(roa_c, roa_p),
        "ocf_exceeds_net_income": gt(ocf, ni),
        # debt-free in both years counts as met: there is no leverage to reduce (strictly "fell" would fail them)
        "leverage_down": None if lev(c) is None or lev(p) is None else (lev(c) < lev(p) or lev(c) == lev(p) == 0),
        "current_ratio_up": gt(cr(c), cr(p)),
        "no_new_shares": None if num(c.get("shares")) is None or num(p.get("shares")) is None
        else c["shares"] <= p["shares"] * 1.005,
        "gross_margin_up": gt(gm(c), gm(p)),
        "asset_turnover_up": gt(at(c), at(p)),
    }
    known = [v for v in tests.values() if v is not None]
    score = sum(1 for v in known if v)
    band = "n/a" if len(known) < 9 else "strong" if score >= 8 else "weak" if score <= 2 else "average"
    note = "" if len(known) == 9 else f"{9 - len(known)} of 9 tests missing inputs; score is out of {len(known)}"
    return {"score": score, "tests": tests, "band": band, "of": len(known), "note": note,
            "basis": "latest fiscal year vs the year before"}


def piotroski_quarterly(stm: dict) -> dict:
    """The same nine tests on the latest quarter against the same quarter a year earlier (so seasonality cancels).
    Annual figures lag by up to three quarters, which matters for a company whose business changed quickly."""
    from datetime import date
    q = stm.get("quarterly") or []
    none = {"score": None, "tests": {}, "band": "n/a", "basis": "latest quarter vs the same quarter a year earlier"}
    if len(q) < 5:
        return {**none, "note": "needs 5 quarters of statements"}
    c, p = q[-1], q[-5]
    try:
        gap = (date.fromisoformat(c["period"][:10]) - date.fromisoformat(p["period"][:10])).days
    except (KeyError, ValueError):
        gap = None
    if gap is None or not 340 <= gap <= 390:
        return {**none, "note": "the quarters are not a year apart"}

    ta_c, ta_p = num(c.get("total_assets")), num(p.get("total_assets"))
    ni_c, oc_c = num(c.get("net_income")), num(c.get("ocf"))
    ni_p = num(p.get("net_income"))

    def per_assets(flow, assets):  # annualised quarterly flow over period-end assets, the same way in both quarters
        return None if flow is None or not assets else flow * 4 / assets

    def gt(x, y):
        return None if x is None or y is None else x > y

    def lev(x):
        return sdiv(x.get("lt_debt"), x.get("total_assets"))

    def cr(x):
        return sdiv(x.get("current_assets"), x.get("current_liabilities"))

    def gm(x):
        return sdiv(x.get("gross_profit"), x.get("revenue"))

    roa_c, roa_p = per_assets(ni_c, ta_c), per_assets(ni_p, ta_p)
    cf_c = per_assets(oc_c, ta_c)
    tests = {
        "roa_positive": None if roa_c is None else roa_c > 0,
        "ocf_positive": None if cf_c is None else cf_c > 0,
        "roa_improving": gt(roa_c, roa_p),
        "ocf_exceeds_net_income": gt(oc_c, ni_c),
        "leverage_down": None if lev(c) is None or lev(p) is None else (lev(c) < lev(p) or lev(c) == lev(p) == 0),
        "current_ratio_up": gt(cr(c), cr(p)),
        "no_new_shares": None if num(c.get("shares")) is None or num(p.get("shares")) is None
        else c["shares"] <= p["shares"] * 1.005,
        "gross_margin_up": gt(gm(c), gm(p)),
        "asset_turnover_up": gt(per_assets(num(c.get("revenue")), ta_c), per_assets(num(p.get("revenue")), ta_p)),
    }
    known = [v for v in tests.values() if v is not None]
    score = sum(1 for v in known if v)
    band = "n/a" if len(known) < 9 else "strong" if score >= 8 else "weak" if score <= 2 else "average"
    note = "" if len(known) == 9 else f"{9 - len(known)} of 9 tests missing inputs; score is out of {len(known)}"
    return {"score": score, "tests": tests, "band": band, "of": len(known), "note": note,
            "basis": "latest quarter vs the same quarter a year earlier", "latest": c["period"], "year_ago": p["period"]}


def piotroski(stm: dict) -> dict:
    """The score the page uses: the latest-quarter comparison when it can be built, else the fiscal year. Both are
    returned. GuruFocus uses trailing twelve months against the year before, which needs eight quarters; Longbridge
    returns five, so that exact comparison cannot be built and the two scores can differ from GuruFocus's."""
    ann, qtr = piotroski_annual(stm), piotroski_quarterly(stm)
    use = qtr if qtr["score"] is not None and qtr.get("of", 0) >= 8 else ann
    keep = ("score", "of", "band", "tests", "basis", "note")
    return {**use, "annual": {k: ann.get(k) for k in keep}, "quarterly": {k: qtr.get(k) for k in keep + ("latest", "year_ago")}}


def altman(stm: dict, quote: dict) -> dict:
    if is_financial(quote):
        return {"score": None, "zone": "n/a", "note": "not applicable to financials"}
    mc = num(quote.get("market_cap"))

    def z_of(bs, ebit, sales):
        ta = num(bs.get("total_assets"))
        wc = None if num(bs.get("current_assets")) is None or num(bs.get("current_liabilities")) is None \
            else bs["current_assets"] - bs["current_liabilities"]
        parts = [sdiv(wc, ta), sdiv(bs.get("retained_earnings"), ta), sdiv(ebit, ta),
                 sdiv(mc, bs.get("total_liabilities")), sdiv(sales, ta)]
        if any(p is None for p in parts):
            return None
        return 1.2 * parts[0] + 1.4 * parts[1] + 3.3 * parts[2] + 0.6 * parts[3] + 1.0 * parts[4]

    # Prefer the latest balance sheet with trailing-twelve-month EBIT and sales, to match today's market cap.
    bs, A = latest_bs(stm), latest(stm)
    z, basis = z_of(bs, ttm(stm, "operating_income")[0], ttm(stm, "revenue")[0]), "latest quarter and trailing twelve months"
    if z is None:
        z, basis = z_of(A, A.get("operating_income"), A.get("revenue")), "latest fiscal year"
    if z is None:
        return {"score": None, "zone": "n/a", "note": "missing inputs"}
    zone = "safe" if z > 2.99 else "distress" if z < 1.81 else "grey"
    return {"score": r(z, 2), "zone": zone, "basis": basis,
            "note": f"original manufacturing formula; {basis}"}


def beneish(stm: dict, quote: dict) -> dict:
    a = stm["annual"]
    if is_financial(quote) or len(a) < 2:
        return {"score": None, "flag": None, "note": "not applicable or needs 2 fiscal years"}
    t, p = a[-1], a[-2]

    def ratio(f):
        try:
            return f()
        except (TypeError, ZeroDivisionError, KeyError):  # missing line item or zero denominator -> n/a
            return None

    def gmr(x):
        return x["gross_profit"] / x["revenue"]

    comps = {
        "DSRI": ratio(lambda: (t["receivables"] / t["revenue"]) / (p["receivables"] / p["revenue"])),
        "GMI": ratio(lambda: gmr(p) / gmr(t)),
        "AQI": ratio(lambda: (1 - (t["current_assets"] + t["ppe"]) / t["total_assets"]) /
                     (1 - (p["current_assets"] + p["ppe"]) / p["total_assets"])),
        "SGI": ratio(lambda: t["revenue"] / p["revenue"]),
        "DEPI": ratio(lambda: (p["da"] / (p["da"] + p["ppe"])) / (t["da"] / (t["da"] + t["ppe"]))),
        "SGAI": ratio(lambda: (t["sga"] / t["revenue"]) / (p["sga"] / p["revenue"])),
        "LVGI": ratio(lambda: ((t["current_liabilities"] + t["lt_debt"]) / t["total_assets"]) /
                      ((p["current_liabilities"] + p["lt_debt"]) / p["total_assets"])),
        "TATA": ratio(lambda: (t["net_income"] - t["ocf"]) / t["total_assets"]),
    }
    if any(v is None for v in comps.values()):
        miss = [k for k, v in comps.items() if v is None]
        return {"score": None, "flag": None, "note": "missing inputs for " + ", ".join(miss)}
    c = comps
    coef = {"DSRI": 0.920, "GMI": 0.528, "AQI": 0.404, "SGI": 0.892, "DEPI": 0.115, "SGAI": -0.172, "TATA": 4.679,
            "LVGI": -0.327}
    m = -4.84 + sum(coef[k] * c[k] for k in coef)
    labels = {"DSRI": "receivables growing faster than sales", "GMI": "gross margin falling", "AQI": "more soft assets",
              "SGI": "sales growth", "DEPI": "slower depreciation", "SGAI": "overhead falling",
              "TATA": "profit above operating cash flow (accruals)", "LVGI": "leverage"}
    neutral = {k: (0.0 if k == "TATA" else 1.0) for k in coef}
    # how many score points each ingredient adds above its neutral value; the biggest two explain a flag
    pts = {k: coef[k] * (c[k] - neutral[k]) for k in coef}
    drivers = [{"name": k, "label": labels[k], "value": r(c[k], 2), "points": r(pts[k], 2)}
               for k in sorted(pts, key=lambda k: -pts[k])[:2] if pts[k] > 0.05]
    return {"score": r(m, 2), "flag": m > -1.78, "drivers": drivers, "basis": "latest fiscal year vs the year before",
            "note": "above -1.78 flags possible manipulation; fast-growing companies and companies with non-cash "
                    "investment gains often trip it, so treat a flag as a prompt to look, not proof"}


def sloan(stm: dict) -> dict:
    """(net income - operating cash flow) / average assets. Trailing twelve months against the balance sheets of
    the latest quarter and the same quarter a year earlier when both exist; otherwise the last fiscal year."""
    from datetime import date
    q = stm.get("quarterly") or []
    ni, ocf = ttm(stm, "net_income")[0], ttm(stm, "ocf")[0]
    basis, avg_ta = "latest fiscal year", None
    if len(q) >= 5 and ni is not None and ocf is not None and ttm(stm, "net_income")[1] == "ttm" \
            and ttm(stm, "ocf")[1] == "ttm":
        try:
            gap = (date.fromisoformat(q[-1]["period"][:10]) - date.fromisoformat(q[-5]["period"][:10])).days
        except (KeyError, ValueError):
            gap = None
        if gap is not None and 340 <= gap <= 390 and num(q[-1].get("total_assets")) and num(q[-5].get("total_assets")):
            avg_ta, basis = avg2(q[-1]["total_assets"], q[-5]["total_assets"]), "trailing twelve months"
    if avg_ta is None:
        a = stm["annual"]
        c = a[-1]
        avg_ta = avg2(c.get("total_assets"), a[-2].get("total_assets") if len(a) > 1 else None)
        ni, ocf = num(c.get("net_income")), num(c.get("ocf"))
    ratio = sdiv(ni - ocf, avg_ta) if ni is not None and ocf is not None else None
    return {"ratio": r(ratio), "flag": None if ratio is None else abs(ratio) > 0.25,
            "watch": None if ratio is None else abs(ratio) > 0.10, "basis": basis}


def magic_formula(stm: dict, quote: dict, peers: list) -> dict:
    A = latest_bs(stm)   # the same date as the trailing-twelve-month EBIT and today's market cap
    oi, _ = ttm(stm, "operating_income")
    mc, debt, cash = num(quote.get("market_cap")), debt_of(A), num(A.get("cash"))
    ev = (mc + (debt or 0) - (cash or 0)) if mc else None
    ey = sdiv(oi, ev)
    nwc = None
    if all(num(A.get(k)) is not None for k in ("current_assets", "current_liabilities", "cash", "ppe")):
        nwc = (A["current_assets"] - A["cash"]) - (A["current_liabilities"] - (num(A.get("st_debt")) or 0))
    roc = sdiv(oi, nwc + A["ppe"]) if nwc is not None and nwc + A["ppe"] > 0 else None

    def rank(v, key):
        pv = [p[key] for p in peers if num(p.get(key)) is not None]
        return None if v is None or not pv else round(100 * sum(1 for x in pv if x < v) / len(pv))

    return {"earnings_yield": r(ey), "return_on_capital": r(roc),
            "rank_ey": rank(ey, "earnings_yield"), "rank_roc": rank(roc, "roc"), "peers": len(peers)}


def roic_history(stm: dict) -> list:
    a = stm["annual"]
    out = []
    for i, p in enumerate(a):
        ic = avg2(invested_capital(p), invested_capital(a[i - 1]) if i else None)
        oi = num(p.get("operating_income"))
        from .metrics import tax_rate
        out.append(oi * (1 - tax_rate(p)) / ic if oi is not None and ic and ic > 0 else None)
    return out


def roic_vs_wacc(stm: dict, quote: dict, roic: Optional[float]) -> dict:
    beta = num(quote.get("beta"))
    est = beta is None
    beta = 1.0 if beta is None else beta
    ke = config.RISK_FREE + beta * config.EQUITY_PREMIUM
    A = latest(stm)
    mc, debt = num(quote.get("market_cap")), debt_of(A) or 0
    if not mc or roic is None:
        return {"wacc": None, "spread": None, "note": "needs market cap and ROIC"}
    kd = config.COST_OF_DEBT * (1 - config.DEFAULT_TAX)
    wacc = (mc * ke + debt * kd) / (mc + debt)
    return {"wacc": r(wacc), "spread": r(roic - wacc), "beta": beta,
            "note": f"WACC estimated: rf {config.RISK_FREE:.1%}, ERP {config.EQUITY_PREMIUM:.1%}, "
                    f"cost of debt {config.COST_OF_DEBT:.1%}" + (", beta assumed 1.0" if est else "")}

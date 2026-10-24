"""The Two-Stage Stock Filter (Reset & Invest, v2.1): a personal checklist applied to one company. Server-side, no I/O.

Stage one is three pass/fail checks (can it survive, does it make cash, is the price fair under the checklist). Stage two
is eight questions worth one point each, half for a partial, and 7 of 8 makes the list. The rules are the checklist's own:
nothing here is loosened to let a company through, and nothing is estimated. A figure that cannot be verified is UNKNOWN
and says what is missing; a company the checklist does not fit (a bank, say) has the affected tests marked NOT APPLICABLE
and the screen INCOMPLETE.

What Longbridge cannot supply is typed in by the user: the stock's own five-year average forward P/E and a three-to-five-year
expected EPS growth for the PEG test, the thesis for question 4 and the ratings for the judgment questions 5 to 8. Until they
are, those tests stay UNKNOWN and the page says what would settle them.

`build(facts, inputs)` takes what service.get_filter fetched and returns the whole scorecard as JSON-ready data; the page only
draws it. Comparisons use the unrounded figures, and "above" and "under" are strict, "at least" and "at or below" include equality.
"""
import math
from datetime import date
from typing import Optional

from . import chart, config, pricehist
from .metrics import latest_bs
from .quality import is_financial
from .util import num, r

PASS, FAIL, REVIEW, UNKNOWN, NA, NOTE = "PASS", "FAIL", "REVIEW", "UNKNOWN", "NOT APPLICABLE", "NOTE"
PASS_REVIEW = "PASS WITH REVIEW"
NOTICE = ("Research notice: This applies a personal checklist to public information. It is not a buy/sell recommendation. "
          "Verify the underlying figures before relying on the score.")
CLOSING = ("Before you trust this: verify the inputs in section 5, then confirm or rewrite the thesis and invalidation "
           "conditions in question 4 if stage two was run.")

# Longbridge names the industry in Chinese on some accounts (硬件、存储及外设), so the words are matched in both languages.
SOFTWARE_WORDS = ("software", "saas", "软件")
HARDWARE_WORDS = ("hardware", "semiconductor", "electronic equipment", "electronic component", "communications equipment",
                  "computer", "peripheral", "硬件", "半导体", "电子设备", "电子元件", "通信设备", "外设")

SOURCE = {"BS": "Longbridge financial-statement BS", "IS": "Longbridge financial-statement IS", "CF": "Longbridge financial-statement CF",
          "REPORT": "Longbridge financial-report (quarterly summary)", "KLINE": "Longbridge kline (daily bars)",
          "SHORT": "Longbridge short-positions and static", "EPS": "Longbridge forecast-eps (consensus mean)"}

WHERE = {   # where to check each decisive figure: the filing statement or note, or the market-data field
    "survive": "Balance sheet (10-Q or 10-K): cash and short-term investments, short- and long-term borrowings; the lease note, to split "
               "finance leases (debt) from operating leases (not debt). Income statement and cash flow statement for EBITDA.",
    "fcf": "Cash flow statement: net cash from operating activities, and purchases of property and equipment plus any capitalized "
           "software; revenue from the income statement. Add the last four quarters, or the last year plus year to date less the "
           "prior year to date.",
    "roic": "Income statement: operating income, income before taxes and the provision for income taxes. Balance sheet: total equity, "
            "borrowings, finance-lease liabilities and cash and short-term investments, for the latest quarter and the quarter a year earlier.",
    "sbc": "Cash flow statement, operating activities: stock-based compensation; revenue from the income statement.",
    "fwd_pe": "Market data, not a filing: the consensus EPS for the next fiscal year (a data vendor such as FactSet, Zacks, Yahoo Finance) "
              "with its fiscal year, whether it is GAAP or adjusted, and the date; and the close on the same date.",
    "avg_fwd_pe": "Market data, not a filing: the company's forward P/E history over five years from a data vendor. Note the vendor, how "
                  "it samples, and that it uses the same forward horizon and EPS basis as the figure above.",
    "eps_cagr": "Market data, not a filing: the analysts' three-to-five-year EPS growth estimate from a data vendor. Note its horizon and basis.",
    "price_rise": "Exchange or market-data closing prices on the two dates, adjusted for splits only (not dividends).",
    "growth": "Income statements: revenue for the last four quarters against the four before them; gross profit and operating income for the last four.",
    "short": "FINRA (US) or HKEX short-position reports: the latest settlement date's shares short, and the float from the company or a data vendor.",
    "tsr": "A total-return series (dividends reinvested) for the stock and for the S&P 500 over the same dates; the diluted weighted-average "
           "share count in the income statement, split-adjusted, for the latest twelve months and the twelve months five years earlier.",
}


# ---------------------------------------------------------------- comparing with a cut-off
# The checklist's words are exact: "above" and "under" exclude the cut-off, "at least" and "at or below" include it. Floating point
# can put a figure that is exactly on its cut-off a hair to either side (440 / 400 - 1 is 0.10000000000000009), so a tie is a
# difference of a billionth. That is a guard against arithmetic noise, not rounding: a figure that only rounds to its cut-off is not on it.
def _tie(x: float, cut: float) -> bool:
    return abs(x - cut) <= 1e-9 * max(abs(x), abs(cut), 1e-9)


def above(x: float, cut: float) -> bool:
    return x > cut and not _tie(x, cut)


def at_least(x: float, cut: float) -> bool:
    return x > cut or _tie(x, cut)


def under(x: float, cut: float) -> bool:
    return x < cut and not _tie(x, cut)


def at_most(x: float, cut: float) -> bool:
    return x < cut or _tie(x, cut)


# ---------------------------------------------------------------- formatting
def _money(x: Optional[float]) -> str:
    """A statement figure at a glance: 12.34B, -560.00M. The currency is named once, in the page header."""
    if x is None:
        return "n/a"
    a, s = abs(x), "-" if x < 0 else ""
    for div, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            return f"{s}{a / div:.2f}{suffix}"
    return f"{s}{a:.0f}"


def _pct(x: Optional[float], nd: int = 1) -> str:
    return "n/a" if x is None else f"{x * 100:.{nd}f}%"


def _x(x: Optional[float], nd: int = 2) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}x"


def _px(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x:.2f}"


def _pp(a: float, b: float) -> str:
    """The gap between two fractions in percentage points."""
    return f"{abs(a - b) * 100:.1f} percentage points"


def _measure(value: float, cut: float, shown: str, where: str) -> dict:
    """What a row or condition needs to be ranked by how close it sits to its cut-off (verify_first)."""
    return {"value": r(value, 6), "cut": cut, "shown": shown, "where": where}


def _row(rid: str, label: str, figure: str, threshold: str, status: str, kind: str, source: str, period: str = "", **extra) -> dict:
    return {"id": rid, "label": label, "figure": figure, "period": period, "threshold": threshold, "status": status,
            "kind": kind, "source": source, **extra}


def _cond(cid: str, label: str, figure: str, threshold: str, status: str, gap: Optional[str] = None, **extra) -> dict:
    return {"id": cid, "label": label, "figure": figure, "threshold": threshold, "status": status, "gap": gap, **extra}


# ---------------------------------------------------------------- reading the statements
def _quarter(p: dict) -> str:
    return f"Q{p['fp']} {p['fy']}" if p.get("fy") and p.get("fp") else ""


def _consecutive(ps: list) -> bool:
    """Whether the periods follow one another: fiscal quarters numbered in sequence, else period ends about three months apart."""
    try:
        idx = [p["fy"] * 4 + p["fp"] for p in ps]
        return all(b - a == 1 for a, b in zip(idx, idx[1:]))
    except (KeyError, TypeError):
        pass
    try:
        days = [(date.fromisoformat(b["period"][:10]) - date.fromisoformat(a["period"][:10])).days for a, b in zip(ps, ps[1:])]
        return all(75 <= d <= 110 for d in days)
    except (KeyError, TypeError, ValueError):
        return False


def window(stm: dict, keys: tuple) -> Optional[dict]:
    """The trailing twelve months of each of `keys`, all over the same periods: the last four quarters when each has every
    key and they run on from one another, else the last fiscal year. {values, basis, end, label}, or None when neither has
    them all. Taking some keys from one window and some from another would divide a trailing figure by an annual one."""
    q = (stm.get("quarterly") or [])[-4:]
    if len(q) == 4 and _consecutive(q) and all(num(p.get(k)) is not None for p in q for k in keys):
        end = q[-1]
        tag = _quarter(end)
        return {"values": {k: sum(num(p[k]) for p in q) for k in keys}, "basis": "ttm", "end": end.get("period"),
                "label": f"TTM to {end.get('period')}" + (f" ({tag})" if tag else "")}
    a = (stm.get("annual") or [None])[-1]
    if a and all(num(a.get(k)) is not None for k in keys):
        return {"values": {k: num(a[k]) for k in keys}, "basis": "fy", "end": a.get("period"),
                "label": f"fiscal year to {a.get('period')} (four quarters were not available)"}
    return None


def _year_apart(a: dict, b: dict) -> bool:
    """Whether period `a` ended about twelve months before period `b`."""
    try:
        return (b["fy"] * 4 + b["fp"]) - (a["fy"] * 4 + a["fp"]) == 4
    except (KeyError, TypeError):
        pass
    try:
        return 335 <= (date.fromisoformat(b["period"][:10]) - date.fromisoformat(a["period"][:10])).days <= 400
    except (KeyError, TypeError, ValueError):
        return False


def balances(stm: dict) -> tuple[dict, Optional[dict]]:
    """The latest balance sheet and the one twelve months before it: the quarter four back (Longbridge returns five), or for
    a company whose latest is a fiscal year, the year before. The second is None when it is not there."""
    q, now = stm.get("quarterly") or [], latest_bs(stm)
    quarterly = bool(q) and now is q[-1]
    pool = q if quarterly else stm["annual"]
    prev = pool[-5] if quarterly and len(pool) >= 5 else pool[-2] if not quarterly and len(pool) >= 2 else None
    return now, (prev if prev is not None and _year_apart(prev, now) else None)


def debt_split(p: Optional[dict]) -> Optional[dict]:
    """The debt on a balance sheet as two amounts. `borrowings` is interest-bearing debt: short-term borrowings, the current
    portion of long-term debt and long-term debt. `leases` is every lease line, plus any part of the current portion that
    the feed does not split. They are kept apart because the checklist counts finance leases as debt and not operating
    leases, and Longbridge's standardized balance sheet holds a company's finance leases in its lease lines (MSFT's 69B) and,
    in some layouts, its operating leases too (AMZN's combined lease line). The data cannot say which, so the filter
    evaluates the debt both ways and calls the result UNKNOWN when the answer depends on it."""
    if not p:
        return None
    parts = ("borrow_st", "borrow_cp", "borrow_lt", "lease_cp", "lease_lt")
    if all(num(p.get(k)) is None for k in parts):   # statements without the split lines: the combined ones, leases unknown
        s, lt = num(p.get("st_debt")), num(p.get("lt_debt"))
        return None if s is None and lt is None else {"borrowings": (s or 0) + (lt or 0), "leases": 0.0,
                                                       "lines": {"short-term and current debt": s or 0, "long-term debt": lt or 0}}
    g = lambda k: num(p.get(k)) or 0.0
    unsplit = max(0.0, g("cp_total") - g("borrow_cp") - g("lease_cp"))
    return {"borrowings": g("borrow_st") + g("borrow_cp") + g("borrow_lt"), "leases": g("lease_cp") + g("lease_lt") + unsplit,
            "lines": {"short-term borrowings": g("borrow_st"), "current portion of long-term debt": g("borrow_cp"), "long-term debt": g("borrow_lt")}}


def _agree(a: str, b: str) -> str:
    """Two statuses of the same test under the two ways of counting debt: the answer if they agree, else UNKNOWN."""
    return a if a == b else UNKNOWN


def _all_pass(statuses: list) -> str:
    """A check whose conditions must all pass: FAIL if any fails, PASS if all pass, PASS WITH REVIEW when the only thing short
    of a pass is the PEG review band, else UNKNOWN. A NOTE row (a disclosure) takes no part."""
    statuses = [s for s in statuses if s != NOTE]
    if FAIL in statuses:
        return FAIL
    if all(s == PASS for s in statuses):
        return PASS
    if all(s in (PASS, REVIEW) for s in statuses):
        return PASS_REVIEW
    return UNKNOWN


# ---------------------------------------------------------------- the framework's fit
def classify(industry: str, override: str = "auto") -> dict:
    """Software or hardware for question 1's gross-margin cut-off: the user's choice, else the industry's name when it names
    exactly one of them. A business that fits neither (or both) is left unresolved rather than given the easier threshold."""
    ind = (industry or "").lower()
    if override in ("software", "hardware"):
        return {"value": override, "source": "your choice", "industry": industry}
    sw, hw = any(w in ind for w in SOFTWARE_WORDS), any(w in ind for w in HARDWARE_WORDS)
    if sw != hw:
        return {"value": "software" if sw else "hardware", "source": "the industry's name", "industry": industry}
    return {"value": None, "source": "unresolved", "industry": industry}


def framework_fit(f: dict, inputs: dict) -> dict:
    """Whether the checklist fits this business, and the notes that say where it does not."""
    quote, stm, notes = f["quote"], f["stm"], []
    w = window(stm, ("revenue",))
    financial = is_financial(quote)
    pre_revenue = (w is None or w["values"]["revenue"] <= 0) and not financial
    category = classify(quote.get("industry") or "", inputs.get("category", "auto"))
    if financial:
        notes.append("Banks, insurers and REITs fund themselves with debt as part of the business, so cash against debt, EBITDA, free "
                     "cash flow, invested capital and gross margin do not mean what the checklist assumes. Those tests are NOT APPLICABLE "
                     "and the screen is INCOMPLETE; the price tests and the market questions still run.")
    if pre_revenue:
        notes.append("There is no trailing revenue, so margins and returns on revenue are undefined. The tests that need it are NOT "
                     "APPLICABLE and the screen is INCOMPLETE.")
    if category["value"] is None and not financial:
        notes.append(f"The industry ({category['industry'] or 'unknown'}) is not clearly software or hardware, the only two gross-margin "
                     "cut-offs the checklist has, so question 1's gross-margin test is NOT APPLICABLE and stage two is INCOMPLETE. "
                     "Choose a category in your inputs if you can justify one from the company's segment revenue.")
    return {"financial": financial, "pre_revenue": pre_revenue, "category": category, "notes": notes}


# ---------------------------------------------------------------- stage one, check 1: can it survive?
def route_cash(cash: Optional[float], debt: Optional[float]) -> str:
    """The cash pool strictly exceeds debt."""
    return UNKNOWN if cash is None or debt is None else PASS if above(cash, debt) else FAIL


def route_leverage(cash: Optional[float], debt: Optional[float], ebitda: Optional[float]) -> str:
    """Net debt over positive TTM EBITDA is strictly below the limit. With EBITDA at zero or below the route fails (a company that
    is net cash passes by the other route)."""
    if cash is None or debt is None or ebitda is None:
        return UNKNOWN
    if ebitda <= 0:
        return FAIL
    return PASS if under((debt - cash) / ebitda, config.TS_NET_DEBT_EBITDA_MAX) else FAIL


def survive_status(cash, debt, ebitda) -> tuple:
    """(check status, cash route, leverage route) for one way of counting debt. Either verified route is enough to pass; the
    check fails only when both do."""
    a, b = route_cash(cash, debt), route_leverage(cash, debt, ebitda)
    if PASS in (a, b):
        return PASS, a, b
    return (FAIL if a == FAIL and b == FAIL else UNKNOWN), a, b


def check_survive(f: dict, fit: dict) -> dict:
    stm = f["stm"]
    now, _ = balances(stm)
    title, label = "Check 1: Can it survive?", "Cash pool above debt, or net debt / EBITDA under 1.5x"
    src_bs = f"{SOURCE['BS']} {'qf' if now is (stm.get('quarterly') or [None])[-1] else 'af'}"
    period = f"balance sheet {now.get('period')}"
    out = {"id": "survive", "title": title, "workings": [], "where": WHERE["survive"]}
    if fit["financial"]:
        return {**out, "status": NA, "rows": [_row("survive", label, "not applicable to a bank, insurer or REIT", "either route", NA,
                                                   "ANALYST JUDGMENT", "framework fit")]}
    cash, d = num(now.get("cash")), debt_split(now)
    if d is None or cash is None:
        why = "no debt lines on the balance sheet" if d is None else "no cash and short-term investments line"
        return {**out, "status": UNKNOWN, "rows": [_row("survive", label, f"UNKNOWN: {why}", "either route", UNKNOWN, "REPORTED", src_bs, period)]}
    w = window(stm, ("operating_income", "da"))
    ebitda = None if w is None else w["values"]["operating_income"] + w["values"]["da"]
    low, high = d["borrowings"], d["borrowings"] + d["leases"]
    variants = [survive_status(cash, debt, ebitda) for debt in ((low, high) if d["leases"] else (low,))]
    status, route_a, route_b = (variants[0] if len(variants) == 1 else tuple(_agree(a, b) for a, b in zip(*variants)))
    depends = len(variants) == 2 and variants[0][0] != variants[1][0]
    debt_text = _money(low) if not d["leases"] else f"{_money(low)} (borrowings) to {_money(high)} (with all lease lines)"
    lev = [(debt - cash) / ebitda for debt in (low, high)] if ebitda and ebitda > 0 else None
    lev_text = "n/a" if lev is None else _x(lev[0]) if not d["leases"] else f"{_x(lev[0])} to {_x(lev[1])}"
    leverage_gap = None
    if route_b == FAIL:
        leverage_gap = "EBITDA is not positive" if ebitda is not None and ebitda <= 0 else f"{_x(lev[0] - config.TS_NET_DEBT_EBITDA_MAX)} above the limit" if lev else None
    extra_b = _measure(lev[0], config.TS_NET_DEBT_EBITDA_MAX, _x(lev[0]), WHERE["survive"]) if lev and not d["leases"] else {}
    rows = [
        _row("cash_over_debt", "Cash pool exceeds debt", f"cash pool {_money(cash)} vs debt {debt_text}", "cash pool > debt", route_a, "REPORTED", src_bs, period,
             gap=f"cash pool is {_money(low - cash)} short of borrowings" + (f" ({_money(high - cash)} with the lease lines)" if d["leases"] else "")
             if route_a == FAIL else None),
        _row("net_debt_ebitda", "Net debt / positive TTM EBITDA is under 1.5x", f"{lev_text} (EBITDA {_money(ebitda)})",
             f"< {_x(config.TS_NET_DEBT_EBITDA_MAX, 1)}, EBITDA positive", route_b, "CALCULATED", f"{src_bs}; IS and CF qf", w["label"] if w else period,
             gap=leverage_gap, **extra_b),
    ]
    if status == PASS:   # either route is enough, so a route that fails here costs nothing
        for x in rows:
            if x["status"] == FAIL:
                x["note"] = "Not needed: either route is enough, and the other one passes."
    if d["leases"]:
        rows.append(_row("leases", "Lease liabilities (disclosed separately)", f"{_money(d['leases'])} in the lease lines",
                         "finance leases are debt, operating leases are not", NOTE, "REPORTED", src_bs, period,
                         note="Longbridge's balance sheet does not separate finance from operating leases, so the check is run both ways. "
                              + ("The result depends on it: read the company's lease note." if depends else "The result is the same either way.")))
    lines = [f"Cash pool = cash and short-term investments {_money(cash)} ({now.get('period')}); restricted cash and long-term investments are excluded.",
             "Debt = " + " + ".join(f"{k} {_money(v)}" for k, v in d["lines"].items()) + f" = {_money(low)}"
             + (f"; lease lines {_money(d['leases'])} are reported separately." if d["leases"] else ".")]
    if w:
        lines.append(f"EBITDA = operating income {_money(w['values']['operating_income'])} + D&A {_money(w['values']['da'])} = {_money(ebitda)} ({w['label']}).")
    else:
        lines.append("EBITDA is UNKNOWN: operating income or depreciation and amortization is missing.")
    return {**out, "status": status, "rows": rows, "workings": lines}


# ---------------------------------------------------------------- stage one, check 2: does it make cash?
def roic_of(op_income, pretax, tax, ic_now, ic_prev) -> dict:
    """TTM NOPAT over average invested capital: NOPAT = operating income x (1 - effective tax rate), the rate being tax expense over
    pretax income, and the capital the average of the latest balance and the one twelve months earlier. The formula is NOT
    MEANINGFUL, and says why, when pretax income or the average capital is not positive or the rate is outside 0-100%."""
    if None in (op_income, pretax):
        return {"roic": None, "why": "operating income or pretax income is missing"}
    if pretax <= 0:   # said before the tax line is asked for: a company making a loss has none
        return {"roic": None, "why": f"pretax income is not positive ({_money(pretax)})"}
    if tax is None:
        return {"roic": None, "why": "the tax line is missing"}
    rate = tax / pretax
    if not 0 <= rate <= 1:
        return {"roic": None, "why": f"the effective tax rate is {_pct(rate)}, outside 0-100%"}
    if ic_now is None or ic_prev is None:
        return {"roic": None, "why": "the invested capital of the latest balance sheet or the one a year earlier is missing"}
    avg = (ic_now + ic_prev) / 2
    if avg <= 0:
        return {"roic": None, "why": f"average invested capital is not positive ({_money(avg)})"}
    nopat = op_income * (1 - rate)
    return {"roic": nopat / avg, "rate": rate, "nopat": nopat, "avg": avg}


def _invested(p: Optional[dict], debt: Optional[float]) -> Optional[float]:
    """Total equity + debt - cash pool, for one balance sheet."""
    if not p or debt is None:
        return None
    eq, cash = num(p.get("equity")), num(p.get("cash"))
    return None if eq is None or cash is None else eq + debt - cash


def check_cash(f: dict, fit: dict) -> dict:
    stm = f["stm"]
    out = {"id": "cash", "title": "Check 2: Does it make cash?", "where": WHERE["fcf"]}
    if fit["financial"] or fit["pre_revenue"]:
        why = "a bank, insurer or REIT" if fit["financial"] else "a company with no trailing revenue"
        rows = [_row(i, label, f"not applicable to {why}", thr, NA, "ANALYST JUDGMENT", "framework fit") for i, label, thr in
                (("fcf_margin", "TTM free cash flow margin", ">= 15%"), ("roic", "TTM ROIC", "> 15%"), ("sbc", "TTM stock-based compensation / revenue", "< 10%"))]
        return {**out, "status": NA, "rows": rows, "workings": []}
    rows, lines = [], []
    # free cash flow margin
    w = window(stm, ("ocf", "capex", "revenue"))
    if w and w["values"]["revenue"] > 0:
        v = w["values"]
        fcf = v["ocf"] - abs(v["capex"])
        margin = fcf / v["revenue"]
        st = PASS if at_least(margin, config.TS_FCF_MARGIN_MIN) else FAIL
        rows.append(_row("fcf_margin", "TTM free cash flow margin", f"{_pct(margin)} (FCF {_money(fcf)} / revenue {_money(v['revenue'])})", ">= 15%", st,
                         "CALCULATED", f"{SOURCE['CF']} qf; IS qf", w["label"], gap=None if st == PASS else f"{_pp(margin, config.TS_FCF_MARGIN_MIN)} below 15%",
                         **_measure(margin, config.TS_FCF_MARGIN_MIN, _pct(margin), WHERE["fcf"])))
        lines.append(f"FCF = operating cash flow {_money(v['ocf'])} - capex {_money(abs(v['capex']))} = {_money(fcf)}; margin = {_money(fcf)} / "
                     f"{_money(v['revenue'])} = {_pct(margin, 2)}. Capitalized software counts only if it is inside the capex line.")
    else:
        rows.append(_row("fcf_margin", "TTM free cash flow margin", "UNKNOWN: operating cash flow, capex or revenue is missing", ">= 15%", UNKNOWN,
                         "CALCULATED", f"{SOURCE['CF']} qf; IS qf"))
    # ROIC. Debt is counted both ways when there are lease lines (see debt_split); the result stands only if both ways agree.
    now, prev = balances(stm)
    dn, dp = debt_split(now), debt_split(prev)
    wr = window(stm, ("operating_income", "pretax_income", "tax")) or window(stm, ("operating_income", "pretax_income"))   # no tax line: a loss-maker's
    ways = [lambda d: d["borrowings"]]
    if (dn and dn["leases"]) or (dp and dp["leases"]):
        ways.append(lambda d: d["borrowings"] + d["leases"])
    results = []
    if wr and dn:
        v = wr["values"]
        results = [roic_of(v["operating_income"], v["pretax_income"], v.get("tax"), _invested(now, debt(dn)), _invested(prev, debt(dp) if dp else None))
                   for debt in ways]
    src = f"{SOURCE['IS']} qf; {SOURCE['BS']} qf"
    if results and all(x["roic"] is not None for x in results):
        sts = [PASS if above(x["roic"], config.TS_ROIC_MIN) else FAIL for x in results]
        st = sts[0] if len(sts) == 1 else _agree(*sts)
        lo, hi = min(x["roic"] for x in results), max(x["roic"] for x in results)
        x = results[0]
        flag = ""
        if not config.TS_TAX_LOW <= x["rate"] <= config.TS_TAX_HIGH:
            flag = (f"Tax flag: the effective rate is {_pct(x['rate'])}, far from a typical statutory rate, so NOPAT is "
                    f"{'flattered' if x['rate'] < config.TS_TAX_LOW else 'depressed'} by it.")
        depends = len(results) == 2 and st == UNKNOWN
        extra = _measure(lo, config.TS_ROIC_MIN, _pct(lo), WHERE["roic"]) if len(results) == 1 else {}
        rows.append(_row("roic", "TTM ROIC", f"{_pct(lo) if lo == hi else f'{_pct(lo)} to {_pct(hi)}'} (NOPAT {_money(x['nopat'])} / average invested capital "
                         f"{_money(x['avg'])}{'' if len(results) == 1 else ' to ' + _money(results[1]['avg'])})", "> 15%", st, "CALCULATED", src, wr["label"],
                         gap=f"{_pp(hi, config.TS_ROIC_MIN)} below 15%" if st == FAIL else None,
                         note=" ".join(filter(None, (flag, "The result depends on whether the lease lines are finance leases (debt) or operating "
                                                           "leases (not debt)." if depends else ""))) or None, **extra))
        lines.append(f"ROIC: tax rate = {_money(wr['values']['tax'])} / {_money(wr['values']['pretax_income'])} = {_pct(x['rate'], 1)}; NOPAT = "
                     f"{_money(wr['values']['operating_income'])} x (1 - {_pct(x['rate'], 1)}) = {_money(x['nopat'])}; invested capital = total equity + debt - cash "
                     f"pool, averaged over {now.get('period')} and {prev.get('period') if prev else 'n/a'}." + (f" {flag}" if flag else ""))
    else:
        why = next((x["why"] for x in results if x["roic"] is None), "an input is missing (operating income, pretax income, tax, equity, debt or cash)")
        rows.append(_row("roic", "TTM ROIC", f"{'NOT MEANINGFUL' if results else 'UNKNOWN'}: {why}", "> 15%", UNKNOWN, "CALCULATED", src,
                         wr["label"] if wr else ""))
        lines.append("ROIC is not shown as a figure: the vendor's own ROIC is never substituted for the checklist's formula.")
    # stock-based compensation
    ws = window(stm, ("sbc", "revenue"))
    if ws and ws["values"]["revenue"] > 0:
        v = ws["values"]
        ratio = v["sbc"] / v["revenue"]
        st = PASS if under(ratio, config.TS_SBC_MAX) else FAIL
        rows.append(_row("sbc", "TTM stock-based compensation / revenue", f"{_pct(ratio)} (SBC {_money(v['sbc'])} / revenue {_money(v['revenue'])})", "< 10%", st,
                         "CALCULATED", f"{SOURCE['CF']} qf; IS qf", ws["label"], gap=None if st == PASS else f"{_pp(ratio, config.TS_SBC_MAX)} above 10%",
                         **_measure(ratio, config.TS_SBC_MAX, _pct(ratio), WHERE["sbc"])))
        lines.append(f"SBC / revenue = {_money(v['sbc'])} / {_money(v['revenue'])} = {_pct(ratio, 2)}. SBC is not subtracted from free cash flow here.")
    else:
        rows.append(_row("sbc", "TTM stock-based compensation / revenue", "UNKNOWN: Longbridge's cash flow statement has no stock-based compensation "
                         "line for this company", "< 10%", UNKNOWN, "CALCULATED", f"{SOURCE['CF']} qf; IS qf",
                         note="Read the cash flow statement's operating-activities adjustments."))
    return {**out, "status": _all_pass([x["status"] for x in rows]), "rows": rows, "workings": lines}


# ---------------------------------------------------------------- stage one, check 3: is the price fair?
def peg_status(fwd_pe: float, growth: float) -> tuple:
    """(status, PEG) with growth as a percentage number, 12 for 12% a year: 30 / 20 is 1.5, not 30 / 0.20. At or below 1.5 passes,
    above it and up to 2.0 is a review (it does not fail the check on its own), above 2.0 fails, and growth that is not positive
    fails as not meaningful rather than producing an artificially cheap multiple."""
    if growth <= 0:
        return FAIL, None
    peg = fwd_pe / growth
    return (PASS if at_most(peg, config.TS_PEG_MAX) else REVIEW if at_most(peg, config.TS_PEG_REVIEW_MAX) else FAIL), peg


def price_rise(f: dict) -> dict:
    """The three-calendar-month price rise on closes adjusted for splits alone: the latest completed session against the session on or
    just before three months earlier. {status, rise, now, then, splits}, or {status: UNKNOWN, why} when the unadjusted bars, the splits
    or the history are missing, or when the split-adjusted prices disagree with Longbridge's own adjustment by more than dividends could."""
    raw, splits, fwd, end_date = f.get("raw"), f.get("splits"), f["klines"], f["end_date"]
    if not raw or splits is None:
        return {"status": UNKNOWN, "why": "the unadjusted price history or the list of splits could not be fetched"}
    bars = pricehist.split_adjust(raw, splits)
    j, target = pricehist.last_on_or_before(bars, end_date), pricehist.months_back(end_date, 3)
    i = pricehist.last_on_or_before(bars, target)
    if j is None or bars[j]["t"] != end_date or i is None or bars[0]["t"] > target:
        return {"status": UNKNOWN, "why": f"the daily history does not reach back three months from {end_date}"}
    by_date = {b["t"]: b for b in fwd}
    a, z = by_date.get(bars[i]["t"]), by_date.get(end_date)
    if not a or not z or abs(z["c"] / bars[j]["c"] - 1) > 0.001 or not pricehist.adjustments_agree(bars[i]["c"], a["c"], 0.85):
        return {"status": UNKNOWN, "why": "the split-adjusted prices do not match Longbridge's adjusted series by dividends alone, so something "
                                         "other than a split (a bonus or rights issue) may be missing"}
    rise = bars[j]["c"] / bars[i]["c"] - 1
    return {"status": PASS if under(rise, config.TS_PRICE_RISE_MAX) else FAIL, "rise": rise, "now": {"date": end_date, "close": bars[j]["c"]},
            "then": {"date": bars[i]["t"], "close": bars[i]["c"]}, "splits": [s for s in splits if bars[i]["t"] < s["date"] <= end_date]}


def check_price(f: dict, inputs: dict) -> dict:
    price, price_date = f["price"], f["end_date"]
    an = f.get("analyst") or {}
    eps = num(an.get("eps_forward"))
    eps_date = an.get("eps_forward_date") or "date not given"
    fwd_pe = price / eps if eps and eps > 0 and price else None
    avg, g = inputs.get("avg_fwd_pe"), inputs.get("eps_cagr")
    src_eps = f"{SOURCE['EPS']}; price {SOURCE['KLINE']}"
    fy_note = ("Longbridge does not say which fiscal year this EPS covers (its current forecast year) or whether it is GAAP or adjusted; "
               "the checklist asks for next fiscal year's diluted EPS.")
    reports_in, trades_in = f["stm"].get("currency") or "", f["quote"].get("currency") or ""
    if reports_in and trades_in and reports_in != trades_in:
        fy_note += f" The statements are in {reports_in} and the stock trades in {trades_in}: confirm the consensus EPS is in {trades_in}, or the multiple is wrong."
    pe_text = f"forward P/E {_x(fwd_pe, 1)} (close {_px(price)} on {price_date} / consensus EPS {_px(eps)}, estimate dated {eps_date})" if fwd_pe else None
    rows, lines = [], []
    # 3a: forward P/E against its own five-year average
    measure_a = {}
    if eps is None or (eps > 0 and fwd_pe is None):
        st_a, fig_a, note_a = UNKNOWN, "UNKNOWN: " + ("no consensus EPS from Longbridge" if eps is None else "no closing price"), None
    elif eps <= 0:
        st_a, fig_a, note_a = FAIL, f"consensus EPS {_px(eps)} is not positive: forward P/E is not meaningful for this filter", None
    elif avg is None:
        st_a, fig_a = UNKNOWN, f"{pe_text}; five-year average forward P/E: not provided"
        note_a = (f"Longbridge has no five-year history of forward P/E (its valuation history is trailing P/E, which the checklist does not allow). "
                  f"Enter the average from a data vendor in your inputs: this passes if it is at least {_x(fwd_pe, 1)}.")
    else:
        st_a = PASS if at_most(fwd_pe, avg) else FAIL
        fig_a, note_a = f"{pe_text}; five-year average forward P/E {_x(avg, 1)} (yours)", fy_note
        measure_a = _measure(fwd_pe, avg, _x(fwd_pe, 1), WHERE["fwd_pe"])
    rows.append(_row("fwd_pe", "Forward P/E at or below its own five-year average forward P/E", fig_a, "<= its 5-year average", st_a,
                     "CONSENSUS ESTIMATE" if avg is None else "USER INPUT",
                     src_eps + (f"; average: {inputs.get('avg_fwd_pe_src') or 'source not given'}" if avg is not None else ""), f"close {price_date}",
                     gap=f"{_x(fwd_pe - avg, 1)} above its average" if st_a == FAIL and fwd_pe and avg else None, note=note_a,
                     need=None if fwd_pe is None else r(fwd_pe, 2), need_input="avg_fwd_pe", **measure_a))
    # 3b: PEG
    measure_b = {}
    if eps is None or (eps > 0 and fwd_pe is None):
        st_b, fig_b, note_b = UNKNOWN, "UNKNOWN: " + ("no consensus EPS from Longbridge" if eps is None else "no closing price"), None
    elif eps <= 0:
        st_b, fig_b, note_b = FAIL, "consensus EPS is not positive: PEG is not meaningful for this filter", None
    elif g is None:
        st_b, fig_b = UNKNOWN, f"{pe_text}; expected EPS growth: not provided"
        note_b = (f"Longbridge has no three-to-five-year EPS growth estimate, and a one-year rebound or revenue growth may not be substituted. Enter "
                  f"the analysts' figure in your inputs: PEG is at most {config.TS_PEG_MAX} if growth is at least {fwd_pe / config.TS_PEG_MAX:.1f}% a year.")
    else:
        st_b, peg = peg_status(fwd_pe, g)
        fig_b = f"PEG = {_x(fwd_pe, 1)} / {g:g} = {peg:.2f}" if peg is not None else f"expected EPS growth {g:g}% is not positive: PEG is not meaningful for this filter"
        note_b = None
        if st_b == REVIEW:
            note_b = ("PEG is in the review band (above 1.5, up to 2.0): it does not fail the check on its own. ANALYST JUDGMENT, yours to make: does "
                      "the growth justify the price, for instance very durable earnings at a large, slower-growing company, or a recent run-up that "
                      "pushed the multiple up?")
        if peg is not None:
            nearest = min((config.TS_PEG_MAX, config.TS_PEG_REVIEW_MAX), key=lambda c: abs(peg - c))
            measure_b = _measure(peg, nearest, f"{peg:.2f}", WHERE["eps_cagr"])
    rows.append(_row("peg", "PEG at or below 1.5 (1.5 to 2.0 is a review)", fig_b, "<= 1.5; review to 2.0", st_b,
                     "CONSENSUS ESTIMATE" if g is None else "USER INPUT",
                     src_eps + (f"; growth: {inputs.get('eps_cagr_src') or 'source not given'}" if g is not None else ""), f"close {price_date}",
                     gap=f"PEG {fwd_pe / g:.2f} is above 2.0" if st_b == FAIL and fwd_pe and g and g > 0 else None, note=note_b,
                     need=None if fwd_pe is None else r(fwd_pe / config.TS_PEG_MAX, 2), need_input="eps_cagr", **measure_b))
    # 3c: the three-month rise
    pr = price_rise(f)
    src_px = f"{SOURCE['KLINE']}, unadjusted, with Longbridge corp-action splits"
    if pr["status"] == UNKNOWN:
        rows.append(_row("price_rise", "Price rise over three calendar months under 50%", f"UNKNOWN: {pr['why']}", "< 50%", UNKNOWN, "REPORTED", src_px))
    else:
        sp = pr["splits"]
        rows.append(_row("price_rise", "Price rise over three calendar months under 50%",
                         f"{'+' if pr['rise'] >= 0 else ''}{_pct(pr['rise'])} (close {_px(pr['now']['close'])} on {pr['now']['date']} vs {_px(pr['then']['close'])} on {pr['then']['date']})",
                         "< 50%", pr["status"], "CALCULATED", src_px, f"{pr['then']['date']} to {pr['now']['date']}",
                         gap=None if pr["status"] == PASS else f"{_pp(pr['rise'], config.TS_PRICE_RISE_MAX)} above 50%",
                         note="Split-adjusted closes, dividends not added back" + (f"; adjusted for {len(sp)} split{'s' if len(sp) > 1 else ''} in the window" if sp else ""),
                         **_measure(pr["rise"], config.TS_PRICE_RISE_MAX, _pct(pr["rise"]), WHERE["price_rise"])))
    if fwd_pe:
        lines.append(f"Forward P/E = close {_px(price)} / consensus EPS {_px(eps)} = {fwd_pe:.2f}. {fy_note}")
    return {"id": "price", "title": "Check 3: Is the price fair under this checklist?", "status": _all_pass([x["status"] for x in rows]),
            "rows": rows, "workings": lines, "where": WHERE["fwd_pe"]}


def stage_one_status(checks: list) -> str:
    """Any FAIL fails the stage. With no FAIL, any check that is UNKNOWN or NOT APPLICABLE leaves it INCOMPLETE. Otherwise it passes,
    and the PEG review flag travels with the result."""
    sts = [c["status"] for c in checks]
    if FAIL in sts:
        return "FAIL"
    if any(s in (UNKNOWN, NA) for s in sts):
        return "INCOMPLETE"
    return "PASS WITH PEG REVIEW" if PASS_REVIEW in sts else "PASS"


# ---------------------------------------------------------------- stage two: the questions
GROWTH_TABLE = {3: 1.0, 2: 0.5, 1: 0.0, 0: 0.0}      # question 1: all three conditions 1, exactly two 0.5, one or none 0
HALVES_TABLE = {2: 1.0, 1: 0.5, 0: 0.0}              # question 3: both conditions 1, one 0.5, neither 0
RATING_POINTS = {3: 1.0, 2: 0.5, 1: 0.0}             # the judgment questions: a rating of 3 is a point, 2 half, 1 none
NEAR = 0.30                                          # a figure within this share of its cut-off is one "to verify first"
JUDGMENT = {   # question -> (title, what to assess)
    5: ("A moat, and a CEO you would trust with the cash?",
        "Barriers competitors cannot readily replicate, management's capital allocation, and demand resilience or a durable secular driver. "
        "Assess all three; the weakest supported one sets the rating."),
    6: ("Is the trend behind it years from done?",
        "The adoption runway that remains, the competitive threats, and whether this company can capture the value. A growing market alone is not a 3."),
    7: ("Is the culture strong?",
        "Repeated execution, governance, incentives and credible organizational evidence. Say what employee reviews and company self-reporting cannot show."),
    8: ("Does it keep the best people?",
        "Disclosed turnover, leadership and technical-team continuity, credible reports of key departures. Pay alone does not establish retention; "
        "do not count the same vague evidence here as in question 7."),
}


def _scored(statuses: list, table: dict) -> tuple:
    """(points, low, high) for a question scored by how many conditions hold. A condition that is UNKNOWN or NOT APPLICABLE is
    not counted either way: the points are None (not scored) and the range runs from none of them holding to all of them."""
    held = sum(1 for s in statuses if s == PASS)
    open_ = sum(1 for s in statuses if s in (UNKNOWN, NA))
    low, high = table[held], table[held + open_]
    return (low if not open_ else None), low, high


def _question(n: int, title: str, kind: str, points: Optional[float], low: float, high: float, evidence: str, source: str,
              conditions: Optional[list] = None, period: str = "", **extra) -> dict:
    return {"n": n, "title": title, "kind": kind, "status": "SCORED" if points is not None else "UNKNOWN", "points": points,
            "low": low, "high": high, "evidence": evidence, "source": source, "period": period, "conditions": conditions or [], **extra}


def _summary(conds: list, low: float, high: float, points: Optional[float], of: int) -> str:
    held, open_ = sum(1 for c in conds if c["status"] == PASS), sum(1 for c in conds if c["status"] in (UNKNOWN, NA))
    return f"{held} of {of} conditions hold" + ("" if points is not None else f"; {open_} cannot be checked, so {low:g} to {high:g} points")


def ttm_growth(report: Optional[dict]) -> Optional[dict]:
    """Revenue over the last four quarters against the four before them, from the quarterly summary (eight consecutive quarters)."""
    rows = [x for x in (report or {}).get("quarterly", []) if x.get("revenue") is not None and x.get("fp")]
    if len(rows) < 8 or not _consecutive(rows[-8:]):
        return None
    prior, now = sum(x["revenue"] for x in rows[-8:-4]), sum(x["revenue"] for x in rows[-4:])
    if prior <= 0:
        return None
    return {"growth": now / prior - 1, "now": now, "prior": prior, "end": rows[-1]["period"]}


def q_growth(f: dict, fit: dict) -> dict:
    stm, cat = f["stm"], fit["category"]
    g = ttm_growth(f.get("report"))
    conds = []
    if g is None:
        conds.append(_cond("growth", "Revenue growth over 10%", "UNKNOWN: eight consecutive quarters of revenue are not available", "> 10%", UNKNOWN))
    else:
        st = PASS if above(g["growth"], config.TS_GROWTH_MIN) else FAIL
        conds.append(_cond("growth", "Revenue growth over 10%", f"{_pct(g['growth'])} (TTM {_money(g['now'])} vs {_money(g['prior'])}, TTM to {g['end']})", "> 10%", st,
                           None if st == PASS else f"{_pp(g['growth'], config.TS_GROWTH_MIN)} below 10%",
                           **_measure(g["growth"], config.TS_GROWTH_MIN, _pct(g["growth"]), WHERE["growth"])))
    wg = window(stm, ("gross_profit", "revenue"))
    gm_label, gm_thr = "Gross margin over the cut-off for its category", "software > 50%, hardware > 35%"
    if fit["financial"] or fit["pre_revenue"] or cat["value"] is None:
        conds.append(_cond("gross_margin", gm_label, "NOT APPLICABLE: " + (
            "a bank, insurer or REIT" if fit["financial"] else "no trailing revenue" if fit["pre_revenue"] else
            f"the industry ({cat['industry'] or 'unknown'}) is neither clearly software nor hardware"), gm_thr, NA))
    elif wg is None or wg["values"]["revenue"] <= 0:
        conds.append(_cond("gross_margin", gm_label, "UNKNOWN: gross profit or revenue is missing", gm_thr, UNKNOWN))
    else:
        gm = wg["values"]["gross_profit"] / wg["values"]["revenue"]
        cut = config.TS_GM_SOFTWARE if cat["value"] == "software" else config.TS_GM_HARDWARE
        st = PASS if above(gm, cut) else FAIL
        conds.append(_cond("gross_margin", f"Gross margin over {cut:.0%} ({cat['value']}, from {cat['source']})", f"{_pct(gm)} ({wg['label']})", f"> {cut:.0%}", st,
                           None if st == PASS else f"{_pp(gm, cut)} below {cut:.0%}", **_measure(gm, cut, _pct(gm), WHERE["growth"])))
    wo = window(stm, ("operating_income", "revenue"))
    if fit["financial"] or fit["pre_revenue"]:
        conds.append(_cond("op_margin", "Operating margin over 20%", "NOT APPLICABLE", "> 20%", NA))
    elif wo is None or wo["values"]["revenue"] <= 0:
        conds.append(_cond("op_margin", "Operating margin over 20%", "UNKNOWN: operating income or revenue is missing", "> 20%", UNKNOWN))
    else:
        om = wo["values"]["operating_income"] / wo["values"]["revenue"]
        st = PASS if above(om, config.TS_OPM_MIN) else FAIL
        conds.append(_cond("op_margin", "Operating margin over 20%", f"{_pct(om)} ({wo['label']})", "> 20%", st,
                           None if st == PASS else f"{_pp(om, config.TS_OPM_MIN)} below 20%", **_measure(om, config.TS_OPM_MIN, _pct(om), WHERE["growth"])))
    pts, low, high = _scored([c["status"] for c in conds], GROWTH_TABLE)
    return _question(1, "Growing, with strong margins?", "number", pts, low, high, _summary(conds, low, high, pts, 3),
                     f"{SOURCE['REPORT']}; {SOURCE['IS']} qf", conds)


def q_short(f: dict) -> dict:
    s, title = f.get("short"), "Few investors betting against it?"
    if not s or s.get("rate") is None:
        why = ("Longbridge publishes short interest for US and Hong Kong stocks only" if not f["symbol"].endswith((".US", ".HK"))
               else "the short-interest report could not be fetched")
        return _question(2, title, "number", None, 0.0, 1.0, f"UNKNOWN: {why}", SOURCE["SHORT"],
                         [_cond("short", "Short interest under 5% of float", f"UNKNOWN: {why}", "< 5%", UNKNOWN)])
    try:
        age = (date.fromisoformat(f["today"]) - date.fromisoformat(s["date"])).days
    except (KeyError, TypeError, ValueError):
        age = None
    if age is None or age > config.TS_SHORT_MAX_AGE_DAYS:
        why = (f"the newest short-interest report Longbridge has for this stock is dated {s.get('date') or 'unknown'}"
               + (f", {age} days old" if age is not None else "") + f", which is not the latest reported (the limit is {config.TS_SHORT_MAX_AGE_DAYS} days)")
        return _question(2, title, "number", None, 0.0, 1.0, f"UNKNOWN: {why}", SOURCE["SHORT"],
                         [_cond("short", "Short interest under 5% of float", f"UNKNOWN: {why}", "< 5%", UNKNOWN)], s.get("date") or "")
    rate = s["rate"]
    pts = 1.0 if under(rate, config.TS_SHORT_MAX) else 0.0
    sh, fl = s.get("shares_short"), s.get("float_shares")
    fig = f"{_pct(rate, 2)} of float ({f'{sh:,.0f} shares short' if sh else 'shares short n/a'}" + (f" over a float of {fl:,.0f}" if fl else "") + f"), settlement date {s.get('date') or 'n/a'}"
    cond = _cond("short", "Short interest under 5% of float", fig, "< 5%", PASS if pts else FAIL, None if pts else f"{_pp(rate, config.TS_SHORT_MAX)} above 5%",
                 **_measure(rate, config.TS_SHORT_MAX, _pct(rate, 2), WHERE["short"]))
    return _question(2, title, "number", pts, pts, pts, fig, SOURCE["SHORT"], [cond], s.get("date") or "",
                     note="The float is Longbridge's circulating-shares figure and the report is dated by its settlement date, which lags the market by "
                          "about two weeks. Short-sale trading volume is not short interest.")


def share_counts(report: Optional[dict], splits: Optional[list] = None) -> Optional[dict]:
    """Diluted shares for the latest twelve months and for the twelve months ending exactly five years earlier, as net profit over EPS
    summed across the four quarters (EPS arrives adjusted for later splits, so the two are in the same shares). `tol` is the most
    the rounding of per-share figures can move the comparison. A company reports EPS to the cent in the shares of its day, and the
    split adjustment is applied afterwards, so a quarter before a 10-for-1 split carries a tenth of the rounding error its adjusted
    figure suggests (NVDA's old $1.54 is 0.385 here, good to a tenth of a cent). `splits` gives the factor for each quarter."""
    by = {x["fy"] * 4 + x["fp"]: x for x in (report or {}).get("quarterly", []) if x.get("fp") and x.get("net_income") is not None and x.get("eps")}
    if not by:
        return None
    last = max(by)
    now, then = [by.get(last - k) for k in range(4)], [by.get(last - 4 * config.TS_TSR_YEARS - k) for k in range(4)]
    if None in now or None in then:
        return None

    def factor(x):   # shares today per share then: the splits since the quarter ended
        f = 1.0
        for s in splits or ():
            if x.get("end") and s["date"] > x["end"]:
                f *= s["ratio"]
        return f

    def shares(rows):
        ni, eps = sum(x["net_income"] for x in rows), sum(x["eps"] for x in rows)
        return (ni / eps, sum(0.005 / factor(x) for x in rows) / abs(eps)) if eps and ni / eps > 0 else (None, None)

    (n, en), (t, et) = shares(now), shares(then)
    if n is None or t is None:
        return None
    return {"now": n, "then": t, "change": n / t - 1, "tol": en + et, "now_end": now[0]["period"], "then_end": then[0]["period"]}


def q_returns(f: dict) -> dict:
    title, quote, end = "Rewarding shareholders?", f["quote"], f["end_date"]
    start = pricehist.months_back(end, 12 * config.TS_TSR_YEARS)
    conds = []
    tr, spy = pricehist.return_between(f["klines"], start, end), f.get("spy") or {}
    s0, s1 = spy.get("start"), spy.get("end")
    label = "Five-year total return (dividends reinvested) exceeds the S&P 500's over the same dates"
    if tr is None:
        conds.append(_cond("tsr", label, f"UNKNOWN: less than five years of daily history (the period asked for starts {start})", "> S&P 500", UNKNOWN))
    elif quote.get("currency") != "USD":
        conds.append(_cond("tsr", label, f"UNKNOWN: the S&P 500 is in USD and this stock trades in {quote.get('currency') or 'another currency'}; "
                                         "the app does not convert currencies", "> S&P 500", UNKNOWN))
    elif not s0 or not s1:
        conds.append(_cond("tsr", label, "UNKNOWN: the S&P 500 proxy's prices could not be fetched", "> S&P 500", UNKNOWN))
    else:
        bench = s1["close"] / s0["close"] - 1
        diff = tr["return"] - bench
        st = UNKNOWN if abs(diff) < config.TS_TSR_MARGIN else PASS if diff > 0 else FAIL
        conds.append(_cond("tsr", label, f"{'+' if tr['return'] >= 0 else ''}{_pct(tr['return'], 0)} vs S&P 500 {'+' if bench >= 0 else ''}{_pct(bench, 0)} "
                           f"({tr['start']['date']} to {tr['end']['date']}; S&P 500 {s0['date']} to {s1['date']})", "> S&P 500", st,
                           f"{_pp(tr['return'], bench)} behind" if st == FAIL else None,
                           note="The S&P 500 is measured by SPY's forward-adjusted close, which includes its distributions: a proxy for the index's total "
                                "return (the index itself is price-only), about 0.1 point a year below it."
                                + (" The two are within 1 point of each other, too close to call with a proxy." if st == UNKNOWN else "")))
    sc = share_counts(f.get("report"), f.get("splits"))
    label = "Diluted shares (TTM weighted average) no higher than five years earlier, split-adjusted"
    if sc is None:
        conds.append(_cond("shares", label, "UNKNOWN: net profit and EPS for the latest four quarters and the four ending five years earlier are not all available",
                           "<= five years ago", UNKNOWN))
    else:
        st = UNKNOWN if abs(sc["change"]) < sc["tol"] else PASS if sc["change"] < 0 else FAIL
        conds.append(_cond("shares", label, f"{_money(sc['now'])} now (TTM to {sc['now_end']}) vs {_money(sc['then'])} five years earlier (TTM to {sc['then_end']}): "
                           f"{'+' if sc['change'] >= 0 else ''}{_pct(sc['change'])}", "<= five years ago", st,
                           f"{_pct(sc['change'])} more shares" if st == FAIL else None,
                           note="Diluted shares are net profit divided by diluted EPS, summed over four quarters."
                                + (f" The change is within the rounding of per-share figures (about {_pct(sc['tol'])}), so it is not called." if st == UNKNOWN else "")))
    pts, low, high = _scored([c["status"] for c in conds], HALVES_TABLE)
    return _question(3, title, "number", pts, low, high, _summary(conds, low, high, pts, 2),
                     f"{SOURCE['KLINE']}; SPY forward-adjusted close; {SOURCE['REPORT']}", conds)


def q_thesis(confirmed: bool) -> dict:
    title = "Written down what would change your mind?"
    if confirmed:
        return _question(4, title, "pending", 1.0, 1.0, 1.0, "CONFIRMED by you: a thesis and its invalidation conditions are written down.",
                         "your own words, kept in this browser")
    q = _question(4, title, "pending", None, 0.0, 1.0, "PENDING: it scores once you confirm or rewrite a thesis and its invalidation conditions.", "your own words")
    q["status"] = "PENDING"
    return q


def q_judgment(n: int, rating: Optional[int]) -> dict:
    title, what = JUDGMENT[n]
    if rating in RATING_POINTS:
        p = RATING_POINTS[rating]
        return _question(n, title, "judgment", p, p, p, f"Your rating: {rating} of 3 ({'strong' if rating == 3 else 'mixed' if rating == 2 else 'adverse'})",
                         "ANALYST JUDGMENT (yours)", assess=what)
    return _question(n, title, "judgment", None, 0.0, 1.0, "UNKNOWN: not rated. Insufficient evidence is UNKNOWN, not a neutral 2.", "ANALYST JUDGMENT (yours)", assess=what)


def totals(questions: list, stage_one_ok: bool) -> dict:
    """The points and what they make. Question 4 left PENDING is left out of the sum and the others are read as if it will score: of 7,
    6 or more makes the list once it is confirmed, 5.5 is a close call, 5 or less misses (confirming cannot lift it to 7). Confirmed, of 8:
    7 or more makes the list, 6.5 is a close call, 6 or less misses. Any other question left UNKNOWN makes the result PROVISIONAL /
    INCOMPLETE: the confirmed points and the range are shown, nothing is awarded on a guess, and the denominator stays."""
    q4 = next(q for q in questions if q["n"] == 4)
    pending = q4["status"] == "PENDING"
    rest = [q for q in questions if q["n"] != 4]
    unknown = [q["n"] for q in rest if q["points"] is None]
    known = sum(q["points"] for q in rest if q["points"] is not None)
    base = known + (0.0 if pending else 1.0)
    high = base + sum(q["high"] for q in rest if q["points"] is None) + (1.0 if pending else 0.0)
    out = {"confirmed": base, "out_of": 7 if pending else 8, "low": base, "high": high, "unknown": unknown, "pending_q4": pending, "complete": not unknown}
    if unknown:
        many = len(unknown) > 1
        out.update(label="PROVISIONAL / INCOMPLETE", tone="warn")
        out["note"] = (f"{base:g} confirmed point{'' if base == 1 else 's'}; between {base:g} and {high:g} of 8 once question{'s' if many else ''} "
                       f"{', '.join(map(str, unknown))} {'are' if many else 'is'} settled" + (" and question 4 is confirmed" if pending else "") + ". "
                       + ("Even full marks on every open question cannot reach 6.5, so this misses." if high < config.TS_CLOSE_CALL else
                          "Even full marks on every open question cannot reach 7." if high < config.TS_LIST_AT else
                          "Nothing is awarded on a guess, and it is not called a listing until the open questions are settled."))
        return out
    if pending:
        s = known
        label, tone = (("MAKES THE LIST ONCE QUESTION 4 IS CONFIRMED", "good") if s >= config.TS_LIST_AT - 1 else
                       ("CLOSE CALL ONCE QUESTION 4 IS CONFIRMED", "warn") if s >= config.TS_CLOSE_CALL - 1 else ("MISSES", "bad"))
        out["note"] = (f"{s:g} of 7 before question 4; confirming it adds one point, {s + 1:g} of 8." if label != "MISSES" else
                       f"{s:g} of 7 before question 4; confirming it cannot lift this to 7.")
    else:
        s = base
        label, tone = (("MAKES THE LIST", "good") if s >= config.TS_LIST_AT else ("CLOSE CALL", "warn") if s >= config.TS_CLOSE_CALL else ("MISSES", "bad"))
        out["note"] = (f"The list needs {config.TS_LIST_AT:g} or more of 8, and {config.TS_CLOSE_CALL:g} is a close call. \"Makes the list\" means it "
                       "qualifies for further research under this checklist, not a recommendation or a forecast.")
    if not stage_one_ok:
        out["note"] += " Stage two was run on request: it does not change the stage-one result or make the stock eligible for the list."
        label, tone = f"{label} (NOT ELIGIBLE: STAGE ONE DID NOT PASS)", "warn"
    out.update(label=label, tone=tone)
    return out


# ---------------------------------------------------------------- what to read next
def points_lost(checks: list, questions: list) -> dict:
    """Section 4: each failed condition and each question below full points, with the shortfall; the unknowns apart from the failures."""
    failed, below, unknown = [], [], []
    for c in checks:
        where = c["title"].split(":")[0]   # "Check 3"
        for x in c["rows"]:
            if x["status"] == FAIL:
                if c["status"] != PASS:   # a route that failed while the other passed (check 1) cost nothing
                    failed.append({"where": where, "label": x["label"], "figure": x["figure"], "gap": x.get("gap")})
            elif x["status"] in (UNKNOWN, NA):
                unknown.append({"where": where, "label": x["label"], "why": x["figure"]})
    for q in questions:
        for c in q["conditions"]:
            if c["status"] == FAIL:
                failed.append({"where": f"Question {q['n']}", "label": c["label"], "figure": c["figure"], "gap": c.get("gap")})
            elif c["status"] in (UNKNOWN, NA):
                unknown.append({"where": f"Question {q['n']}", "label": c["label"], "why": c["figure"]})
        if q["points"] is not None and q["points"] < 1:
            below.append({"n": q["n"], "title": q["title"], "points": q["points"], "evidence": q["evidence"]})
        elif q["points"] is None and not q["conditions"]:
            unknown.append({"where": f"Question {q['n']}", "label": q["title"], "why": q["evidence"]})
    return {"failed": failed, "below_full": below, "unknown": unknown}


def verify_first(checks: list, questions: list) -> list:
    """Section 5: the (at most) three inputs most likely to change the result. A figure the result is waiting for comes first, then the
    decisive figures that sit closest to their cut-off, nearest first. Each says the value used, why it matters and where to look."""
    cands = []
    rows = {x["id"]: x for c in checks for x in c["rows"]}
    for rid, title, why, where in (
            ("fwd_pe", "Five-year average forward P/E", "The price check cannot be settled without the company's own five-year average forward P/E.", WHERE["avg_fwd_pe"]),
            ("peg", "Expected EPS growth, 3 to 5 years", "The PEG test cannot be settled without a three-to-five-year expected EPS growth.", WHERE["eps_cagr"])):
        if rows.get(rid, {}).get("status") == UNKNOWN and rows[rid].get("need") is not None:
            cands.append((-1.0, {"title": title, "value": "not provided", "why": why, "where": where}))
    survive = next((c for c in checks if c["id"] == "survive"), None)
    if survive and survive["status"] == UNKNOWN and any(x["id"] == "leases" for x in survive["rows"]):
        cands.append((-1.0, {"title": "Lease liabilities: finance or operating?", "value": next(x["figure"] for x in survive["rows"] if x["id"] == "leases"),
                             "why": "Whether the lease lines are finance leases (debt) or operating leases (not debt) decides check 1.", "where": WHERE["survive"]}))
    measured = [(x, x["label"]) for c in checks for x in c["rows"]] + [(c, f"Question {q['n']}: {c['label']}") for q in questions for c in q["conditions"]]
    for x, title in measured:
        if x.get("value") is not None and x.get("cut") and x["status"] in (PASS, FAIL, REVIEW):
            dist = abs(x["value"] - x["cut"]) / abs(x["cut"])
            if dist > NEAR:   # a figure far from its cut-off is not one a small error could flip
                continue
            cands.append((dist, {"title": title, "value": x["shown"], "where": x["where"],
                                 "why": f"It sits {dist:.1%} from its cut-off ({x['threshold']}), so a small error in a filing line or a data field could flip it."}))
    cands.sort(key=lambda c: c[0])
    return [c[1] for c in cands[:3]]


def timing(f: dict) -> dict:
    """Section 6, only for a stock that has passed both stages: the latest completed upward swing in the last twelve months and its
    61.8% to 78.6% retracement zone, the 200-day EMA of split-adjusted closes, and the next earnings date. Reference points, never
    predicted support, an entry instruction or advice."""
    note = "Reference points only: not predicted support, an entry point or advice."
    raw, splits, end = f.get("raw"), f.get("splits"), f["end_date"]
    if not raw or splits is None:
        return {"available": False, "reason": "the unadjusted daily history or the list of splits could not be fetched", "note": note}
    bars = [b for b in pricehist.split_adjust(raw, splits) if b["t"] <= end]
    swing = pricehist.latest_swing(bars, pricehist.months_back(end, 12))
    closes = [b["c"] for b in bars]
    out = {"available": True, "note": note, "as_of": end, "swing": swing, "ema200": None}
    if swing is None:
        out["swing_note"] = ("No completed upward swing (a confirmed pivot high, with a confirmed pivot low before it) can be verified in the last "
                             "twelve months, so no retracement zone is shown.")
    if len(closes) >= 504:
        out["ema200"] = {"value": chart.ema(closes, 200)[-1], "date": bars[-1]["t"]}
    else:
        out["ema_note"] = f"The 200-day EMA is computed on at least two years of daily history; {len(closes)} sessions are available."
    if f.get("earnings"):
        out["earnings"] = {"date": f["earnings"], "label": "from Longbridge's earnings calendar, which does not say whether the company has confirmed "
                                                           "it: treat it as estimated until the company announces it"}
    return out


def proposal(f: dict, checks: list, growth: Optional[dict]) -> Optional[dict]:
    """Question 4's draft: a one-sentence thesis and up to two measurable invalidation conditions, built from the measures that now meet
    their cut-offs. Proposed, not fact: it scores nothing until the user confirms or rewrites it."""
    rows = {x["id"]: x for c in checks for x in c["rows"]}
    pillars = []
    if rows.get("fcf_margin", {}).get("status") == PASS:
        pillars.append(("converts at least 15% of its revenue into free cash flow",
                        f"TTM free cash flow margin falls below 15% at two consecutive quarterly reports (now {rows['fcf_margin']['shown']})"))
    if growth and growth.get("status") == PASS:
        pillars.append(("grows revenue by more than 10% a year",
                        f"TTM revenue growth falls to 10% or below at two consecutive quarterly reports (now {growth['shown']})"))
    if rows.get("roic", {}).get("status") == PASS and rows["roic"].get("shown"):
        pillars.append(("earns more than 15% on its invested capital",
                        f"TTM ROIC, by this checklist's formula, falls to 15% or below at two consecutive quarterly reports (now {rows['roic']['shown']})"))
    if not pillars:
        return None
    names = [p[0] for p in pillars]
    return {"thesis": f"{f['quote'].get('name') or f['symbol']} " + (names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]) + ".",
            "conditions": [p[1] for p in pillars[:2]],
            "note": "Proposed from the figures above. It is not a fact or your view until you confirm or rewrite it."}


def clean_inputs(avg_fwd_pe=None, avg_fwd_pe_src="", eps_cagr=None, eps_cagr_src="", category="auto", stage2=False, q4=False,
                 q5=None, q6=None, q7=None, q8=None) -> dict:
    """The user's inputs in the form `build` reads: numbers that are finite (and, for a P/E, positive), text trimmed, ratings 1 to 3."""
    def number(x, positive=False):
        x = num(x)
        return None if x is None or not math.isfinite(x) or (positive and x <= 0) else x
    return {"avg_fwd_pe": number(avg_fwd_pe, True), "avg_fwd_pe_src": str(avg_fwd_pe_src or "").strip()[:200],
            "eps_cagr": number(eps_cagr), "eps_cagr_src": str(eps_cagr_src or "").strip()[:200],
            "category": category if category in ("auto", "software", "hardware") else "auto", "stage2": bool(stage2), "q4": bool(q4),
            "ratings": {n: (q if q in RATING_POINTS else None) for n, q in zip((5, 6, 7, 8), (q5, q6, q7, q8))}}


def build(f: dict, inputs: dict) -> dict:
    """The whole scorecard. `f` is what service.get_filter fetched; `inputs` is what the user typed in (see clean_inputs)."""
    fit = framework_fit(f, inputs)
    checks = [check_survive(f, fit), check_cash(f, fit), check_price(f, inputs)]
    s1 = stage_one_status(checks)
    s1_ok = s1 in ("PASS", "PASS WITH PEG REVIEW")
    run_two = s1_ok or bool(inputs.get("stage2"))
    ratings = inputs.get("ratings") or {}
    qs, tot = [], None
    if run_two:
        qs = [q_growth(f, fit), q_short(f), q_returns(f), q_thesis(bool(inputs.get("q4"))), *(q_judgment(n, ratings.get(n)) for n in (5, 6, 7, 8))]
        tot = totals(qs, s1_ok)
    eligible = bool(s1_ok and tot and tot["label"].startswith("MAKES THE LIST"))
    growth = next((c for c in qs[0]["conditions"] if c["id"] == "growth"), None) if qs else None
    quote = f["quote"]
    w = window(f["stm"], ("revenue",))
    two = ("not run" if not run_two else
           f"{tot['label']} ({tot['confirmed']:g} of {tot['out_of']})" if tot["complete"] else
           f"{tot['label']} ({tot['confirmed']:g} confirmed, {tot['low']:g} to {tot['high']:g} of 8 possible)")
    return {
        "symbol": f["symbol"], "status": "ok", "mode": f.get("mode"), "notice": NOTICE, "closing": CLOSING,
        "company": {"name": quote.get("name") or f["symbol"], "industry": quote.get("industry") or "", "currency": quote.get("currency") or ""},
        "dates": {"research": f["today"], "financials": w["label"] if w else "n/a", "market": f"{f['end_date']} close", "fetched_at": f.get("fetched_at")},
        "fit": {"notes": fit["notes"], "category": fit["category"]},
        "verdict": {"line": f"{f['symbol']} ({quote.get('name') or f['symbol']}): stage one {s1}; stage two {two}.", "stage_one": s1, "stage_two": two,
                    "tone": {"FAIL": "bad", "INCOMPLETE": "warn"}.get(s1, "good")},
        "stage_one": {"status": s1, "checks": checks,
                      "decision": {"FAIL": "A check failed, so stage two is skipped unless you ask for it.",
                                   "INCOMPLETE": "Nothing failed, but something could not be verified or does not apply, so the screen is INCOMPLETE and stage two "
                                                 "is skipped unless you ask for it.",
                                   "PASS": "All three checks pass.", "PASS WITH PEG REVIEW": "All three checks pass; the PEG sits in the review band."}[s1]},
        "stage_two": {"run": run_two, "on_request": run_two and not s1_ok, "questions": qs, "total": tot},
        "points_lost": points_lost(checks, qs),
        "verify": verify_first(checks, qs),
        "timing": timing(f) if eligible else None,
        "proposal": proposal(f, checks, growth),
        "judgment": [{"n": n, "title": title, "assess": what} for n, (title, what) in JUDGMENT.items()],   # for the form, whether or not stage two runs
        "inputs": {**inputs, "ratings": {str(n): v for n, v in ratings.items()}},   # JSON keys are text
    }

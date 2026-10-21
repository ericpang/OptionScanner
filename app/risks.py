"""The critic: a broader list of dangers and concerns than the bear case, each with the facts and figures behind it.

Everything here is computed from the statements, quote, flows and chart the app already holds, and every entry names the
figures that triggered it. Risks that live in a company's own disclosures (customer concentration, supplier dependence,
regulation, litigation) are not in these data sources, so they are listed as "not measured" with the latest annual and
quarterly filings to read, rather than guessed at. The thresholds are this app's own.
"""
from typing import Optional

from .metrics import fcf_of, latest, latest_bs
from .util import growth, num, sdiv

SEVERITY = {"high": 0, "medium": 1, "low": 2}
CATEGORIES = ("Valuation", "Growth and earnings", "Business quality", "Balance sheet", "Cash flow and earnings quality", "Capital allocation",
              "Price action and sentiment")
NOT_MEASURED = (
    "Customer concentration: whether a few customers make up a large share of revenue",
    "Supplier, manufacturing and geographic dependence",
    "Competition, technology change and disruption",
    "Regulation, litigation and tax exposure",
    "Management and governance: key-person risk, related-party dealings",
)
# Where a rule-based bear point (thesis.py) that the critic has no counterpart for is filed.
BEAR_CATEGORY = {"PEG": "Valuation", "Fair value": "Valuation", "FCF growth": "Cash flow and earnings quality",
                 "Volume profile": "Price action and sentiment"}
ANNUAL_FILINGS = ("10-K", "10-Q", "20-F", "40-F", "6-K", "ANNUAL", "ANNUAL REPORT", "INTERIM")


def _p(x, d=1):
    return "n/a" if x is None else f"{x:.{d}%}"


def _big(x):
    if x is None:
        return "n/a"
    a, sign = abs(x), "-" if x < 0 else ""
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if a >= div:
            return f"{sign}{a / div:.2f}{suf}"
    return f"{sign}{a:.0f}"


def build(stm: dict, quote: dict, metrics: dict, quality: dict, flows: dict, chart: dict, valuation: Optional[dict],
          filings: Optional[list], bear: Optional[list] = None) -> dict:
    items = []

    def add(cat, sev, title, *facts, source):
        items.append({"category": cat, "severity": sev, "title": title, "facts": [f for f in facts if f], "source": source})

    ex, price = metrics["extra"], num(quote.get("price"))
    ann = stm["annual"]
    A, prev = latest(stm), (ann[-2] if len(ann) > 1 else None)
    BS = latest_bs(stm)

    # ---- valuation
    pe, fy, peg = metrics["pe_ttm"]["value"], ex["fcf_yield"], metrics["peg"]["value"]
    if pe and pe > 35:
        add("Valuation", "high" if pe > 60 else "medium", "Expensive on earnings", f"P/E (TTM) {pe:.1f}",
            f"FCF yield {_p(fy)}" if fy is not None else "", f"PEG {peg}" if peg is not None else "", source="P/E, PEG")
    elif fy is not None and 0 < fy < 0.03:
        add("Valuation", "medium", "Thin free-cash-flow yield", f"FCF yield {_p(fy)}", f"P/E (TTM) {pe}" if pe else "", source="FCF yield")
    hl = (valuation or {}).get("headline")
    if hl and hl["price_to_fair"] >= 1.15:
        rev = ((valuation.get("reverse_dcf") or {}).get("text") or "")
        add("Valuation", "high" if hl["price_to_fair"] >= 1.4 else "medium", "Price is above the app's DCF value",
            f"Price {price:.2f} vs DCF {hl['value']:.2f} (range {hl['low']:.2f} to {hl['high']:.2f})", rev, source="Fair value")
    ar = metrics["analyst_ratings"]
    if ar.get("target_mean") and price and price > ar["target_mean"]:
        add("Valuation", "medium", "Price is above the analyst mean target", f"Price {price:.2f} vs mean target {ar['target_mean']}",
            f"{ar['buy']:.0f} Buy / {ar['hold']:.0f} Hold / {ar['sell']:.0f} Sell" if None not in (ar["buy"], ar["hold"], ar["sell"]) else "",
            source="Analyst ratings")

    # ---- growth and earnings
    rg = ex["revenue_growth"]
    if rg["yoy"] is not None and (rg["yoy"] < 0 or (rg["cagr_3y"] is not None and rg["yoy"] < rg["cagr_3y"] - 0.05)):
        add("Growth and earnings", "high" if rg["yoy"] < 0 else "medium", "Revenue growth is slowing" if rg["yoy"] >= 0 else "Revenue is shrinking",
            f"Revenue {_p(rg['yoy'])} YoY vs {_p(rg['cagr_3y'])} 3-year CAGR", source="Revenue growth")
    ni = metrics["net_income_growth"]
    if ni["yoy"] is not None and ni["yoy"] < 0 or (ni["cagr_3y"] is not None and ni["cagr_3y"] < 0):
        add("Growth and earnings", "high", "Earnings are falling", f"Net income {_p(ni['yoy'])} YoY, {_p(ni['cagr_3y'])} 3-year CAGR", source="Net income growth")
    gm = [x for x in ex["gross_margin"]["history"] if x is not None]
    if len(gm) >= 3 and gm[-1] < max(gm[-4:]) - 0.03:
        add("Growth and earnings", "medium", "Gross margin is below its recent peak",
            f"Gross margin {_p(gm[-1])} vs {_p(max(gm[-4:]))} peak of the last {min(4, len(gm))} years", source="Gross margin")
    om = [x for x in metrics["operating_margin"]["history"] if x is not None]
    if len(om) >= 3 and om[-1] < om[-2] < om[-3]:
        add("Growth and earnings", "medium", "Operating margin has contracted two years running", " -> ".join(_p(x) for x in om[-3:]), source="Operating margin")
    rev90 = ex["eps_revisions"]
    if rev90.get("forward_vs_90d") is not None and rev90["forward_vs_90d"] < 0:
        add("Growth and earnings", "medium", "Forward EPS estimates have been cut", f"Forward EPS {_p(rev90['forward_vs_90d'])} vs 90 days ago", source="EPS revisions")
    miss = [s for s in (rev90.get("surprises_last4") or []) if s is not None and s < 0]
    if miss:
        add("Growth and earnings", "low", f"Missed EPS estimates in {len(miss)} of the last 4 quarters",
            "Surprises: " + ", ".join(_p(s) for s in rev90["surprises_last4"] if s is not None), source="EPS surprises")

    # ---- balance sheet
    d = metrics["debt_to_ebitda"]
    if not d["net_cash"] and d["gross"] is not None and d["gross"] > 3:
        add("Balance sheet", "high" if d["gross"] >= 4 else "medium", "Heavy debt load", f"Debt/EBITDA {d['gross']}x",
            f"Net debt/equity {ex['net_debt_equity']}" if ex["net_debt_equity"] is not None else "", source="Debt/EBITDA")
    ic = ex["interest_coverage"]
    if ic is not None and ic < 5:
        add("Balance sheet", "high" if ic < 3 else "medium", "Thin interest coverage", f"EBIT covers interest {ic}x", source="Interest coverage")
    cr, cash, std = ex["current_ratio"], num(BS.get("cash")), num(BS.get("st_debt"))
    if cr is not None and cr < 1:
        add("Balance sheet", "high" if cr < 0.8 else "medium", "Short-term obligations exceed short-term assets", f"Current ratio {cr}",
            f"Short-term debt {_big(std)} vs cash {_big(cash)}" if std is not None and cash is not None else "", source="Current ratio")
    elif std is not None and cash is not None and std > cash:
        add("Balance sheet", "medium", "Short-term debt is larger than cash", f"Short-term debt {_big(std)} vs cash {_big(cash)}", source="Balance sheet")
    liab = sdiv(num(BS.get("total_liabilities")), num(BS.get("total_assets")))
    if liab is not None and liab > 0.7:
        add("Balance sheet", "medium", "Liabilities are a large share of assets", f"Liabilities are {_p(liab, 0)} of assets", source="Balance sheet")
    az = quality["altman_z"]
    if az.get("score") is not None and az["zone"] != "safe":
        add("Balance sheet", "high" if az["zone"] == "distress" else "medium", f"Altman Z-score in the {az['zone']} zone", f"Z {az['score']}", source="Altman Z")

    # ---- cash flow and earnings quality
    fcf = fcf_of(A)
    if fcf is not None and fcf < 0:
        add("Cash flow and earnings quality", "high", "Free cash flow is negative", f"FCF {_big(fcf)} in the latest fiscal year", source="Cash flow")
    cc = ex["cash_conversion"]
    if cc is not None and cc < 0.8:
        add("Cash flow and earnings quality", "medium", "Weak cash conversion", f"FCF is {cc}x net income", source="Cash conversion")
    bm, sl = quality["beneish_m"], quality["sloan_accruals"]
    if bm.get("flag"):
        add("Cash flow and earnings quality", "medium", "Earnings-quality screen flags (Beneish)", f"M-score {bm['score']} (above -1.78)",
            "Drivers: " + "; ".join(f"{x['label']} ({x['name']} {x['value']})" for x in bm.get("drivers", [])) if bm.get("drivers") else "",
            source="Beneish M")
    if sl.get("flag"):
        add("Cash flow and earnings quality", "medium", "High accruals", f"Accruals are {_p(sl['ratio'])} of assets", source="Sloan accruals")
    nonop = ex.get("non_operating_share")
    if nonop is not None and nonop >= 0.10:
        add("Cash flow and earnings quality", "low", "Part of profit is not operating", f"{_p(nonop, 0)} of pre-tax income is investment gains or interest", source="Income statement")
    if prev:
        g_rev = growth(num(prev.get("revenue")), num(A.get("revenue")))
        for key, label in (("receivables", "Receivables"), ("inventory", "Inventory")):
            g = growth(num(prev.get(key)), num(A.get(key)))
            if g is not None and g_rev is not None and g - g_rev > 0.10 and (num(A.get(key)) or 0) > 0:
                add("Cash flow and earnings quality", "medium", f"{label} are growing faster than revenue",
                    f"{label} {_p(g)} vs revenue {_p(g_rev)} in the latest fiscal year ({_big(num(A.get(key)))})", source="Balance sheet")
    ocf, capex = num(A.get("ocf")), num(A.get("capex"))
    if ocf and capex is not None and ocf > 0 and abs(capex) / ocf > 0.6:
        add("Cash flow and earnings quality", "medium", "Capital-intensive: capex takes most of operating cash flow",
            f"Capex {_big(abs(capex))} is {_p(abs(capex) / ocf, 0)} of operating cash flow {_big(ocf)}", source="Cash flow")

    # ---- capital allocation
    sh = ex["share_count_change_3y"]
    if sh is not None and sh > 0.03:
        add("Capital allocation", "medium", "Shareholders are being diluted", f"Diluted shares {_p(sh)} over 3 years", source="Share count")
    paid = sum(abs(num(A.get(k)) or 0) for k in ("dividends_paid", "buybacks"))
    if fcf and fcf > 0 and paid > fcf:
        add("Capital allocation", "medium", "Payouts exceed free cash flow", f"Dividends and buybacks {_big(paid)} vs FCF {_big(fcf)}", source="Cash flow")
    ins = flows["reported"]["insider"]
    mcap = num(quote.get("market_cap"))
    if ins["net_12m"] is not None and ins["net_12m"] < 0:
        share = -ins["net_12m"] / mcap if mcap else None
        part = f"; 12-month data only from {ins['net_12m_partial_from']}" if ins.get("net_12m_partial_from") else ""
        add("Capital allocation", "medium" if share and share > 0.001 else "low", "Insiders are net sellers",
            f"Net insider selling {_big(-ins['net_12m'])} over 12 months" + (f" ({_p(share, 3)} of market cap)" if share else "") + part,
            "Executive sales are often routine (vesting, tax cover, pre-set plans), so this is weaker evidence than a cluster of buying", source="Insider trades")

    # ---- price action and sentiment
    dd, beta, hv = ex["max_drawdown_1y"], ex["beta"], ex["hv60"]
    if dd is not None and dd <= -0.30:
        add("Price action and sentiment", "medium", "Deep drawdown in the last year", f"Maximum drawdown {_p(dd)}; now {_p(ex['from_52w_high'])} from the 52-week high", source="Price history")
    if (beta is not None and beta > 1.5) or (hv is not None and hv > 0.5):
        add("Price action and sentiment", "medium", "High volatility", f"Beta {beta}" if beta is not None else "",
            f"60-day realised volatility {_p(hv, 0)}" if hv is not None else "", source="Price history")
    sg = chart["signals"]
    if sg["above_ema"].get("200") is False:
        add("Price action and sentiment", "medium", "Price is below the 200-day EMA", f"{_p(ex['from_ema200'])} vs EMA 200", source="Chart")
    if sg["cross_50_200"] == "death":
        add("Price action and sentiment", "medium", "Recent death cross", "EMA 50 crossed below EMA 200 in the last ~60 sessions", source="Chart")
    si = ex["short_interest_pct"]
    if si is not None and si >= 5:
        add("Price action and sentiment", "high" if si >= 10 else "medium", "Heavily shorted", f"Short interest {si}% of float", source="Short interest")
    nr = flows["net_read"]
    if nr["label"] == "distribution":
        add("Price action and sentiment", "medium", "Flow indicators show distribution", "; ".join(nr["signals_against"]), source="Institutional flows")
    tot = sum(x for x in (ar["buy"], ar["hold"], ar["sell"]) if x)
    if tot and (ar["buy"] or 0) / tot < 0.5:
        add("Price action and sentiment", "low", "Analysts are cautious",
            f"{ar['buy'] or 0:.0f} Buy / {ar['hold'] or 0:.0f} Hold / {ar['sell'] or 0:.0f} Sell", source="Analyst ratings")

    # The bear case and the critic are one list: rule-based bear points the critic has no entry for (weak ROIC or ROE,
    # a low Piotroski score, a Graham value trap, no moat, a falling FCF) join it, and where both cover the same metric
    # the critic's entry, which carries more figures and a rating, is the one shown.
    covered = {t.strip() for x in items for t in x["source"].split(",")}
    for b in bear or []:
        if b["source"] not in covered:
            items.append({"category": BEAR_CATEGORY.get(b["source"], "Business quality"), "severity": "medium",
                          "title": b["title"], "facts": [b["evidence"]], "source": b["source"]})
    items.sort(key=lambda x: (SEVERITY[x["severity"]], CATEGORIES.index(x["category"])))
    counts = {s: sum(1 for x in items if x["severity"] == s) for s in SEVERITY}
    reports = [{"type": f["type"], "filed_at": f["filed_at"], "url": f["url"]} for f in (filings or [])
               if str(f.get("type", "")).upper() in ANNUAL_FILINGS and f.get("url")][:3]
    return {"items": items, "counts": counts, "not_measured": list(NOT_MEASURED), "filings": reports,
            "note": "Built from the statements, quote, flows and price history; thresholds are this app's own. "
                    "A concern is a prompt to look, not a verdict."}

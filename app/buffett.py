"""Buffett five-dimension moat diagnostic (1-5 stars each).

The skill's scoring rubric file (references/scoring.md) is not shipped in this repo, so the star thresholds
below are this app's own reading of the skill's descriptions. Quality and price are scored separately, as
the skill requires ("a great business at a fair price...").
"""
from .metrics import debt_of, latest
from .quality import roic_history
from .util import mean, num, sdiv, vals


def _stars(v, cuts, higher_better=True):
    """cuts = 4 ascending thresholds -> 1..5 stars."""
    if v is None:
        return None
    s = 1 + sum(1 for c in cuts if (v >= c if higher_better else v <= c))
    return s


def _avg_stars(*xs):
    v = [x for x in xs if x is not None]
    return int(sum(v) / len(v) + 0.5) if v else None   # half up; round() would send 4.5 to 4 and 3.5 to 4


def analyze(stm: dict, quote: dict, metrics: dict, insiders: list, wacc_spread) -> dict:
    ann = stm["annual"]
    A = latest(stm)
    ex = metrics["extra"]
    ev: dict = {}

    # 1. Moat: margin level and stability + returns on capital
    gms = vals(ex["gross_margin"]["history"])
    gm_avg = mean(gms)
    gm_star = _stars(gm_avg, (0.20, 0.30, 0.40, 0.50))
    if gm_star and gms and (max(gms) - min(gms)) > 0.08:
        gm_star = max(1, gm_star - 1)
    rh = vals(roic_history(stm))
    roic_star = _stars(mean(rh), (0.06, 0.10, 0.15, 0.20))
    moat = _avg_stars(gm_star, roic_star)
    ev["moat"] = [f"Average gross margin {gm_avg:.0%} over {len(gms)} years" if gm_avg is not None else "Gross margin n/a",
                  f"Average ROIC {mean(rh):.0%}" if rh else "ROIC n/a"]

    # 2. Financial health: five yes/no tests -> stars
    roes = vals(metrics["roe"]["history"])
    ta, tl = num(A.get("total_assets")), num(A.get("total_liabilities"))
    fcfs = vals(metrics["fcf_growth"]["series"])
    tests = {
        "ROE at least 15% in most years": bool(roes) and sum(1 for x in roes if x >= 0.15) >= max(1, len(roes) * 0.6),
        "Free cash flow positive every year": bool(fcfs) and all(x > 0 for x in fcfs),
        "Liabilities at most 50% of assets": (sdiv(tl, ta) or 9) <= 0.5,
        "Debt/EBITDA under 2x (or net cash)": metrics["debt_to_ebitda"]["net_cash"] or
        (metrics["debt_to_ebitda"]["gross"] is not None and metrics["debt_to_ebitda"]["gross"] < 2),
        "Gross margin at least 30%": gm_avg is not None and gm_avg >= 0.30,
    }
    fin = max(1, sum(tests.values()))
    ev["financials"] = [f"{'PASS' if v else 'fail'}: {k}" for k, v in tests.items()]

    # 3. Management / capital allocation
    sc = ex["share_count_change_3y"]
    ins12 = ex["insider_net"]["m12"]
    mt = {  # None = inputs missing (shown as n/a, not counted as a pass)
        "Share count shrinking over 3 years": None if sc is None else sc < 0,
        "Pays a dividend or buys back stock": (ex["dividend"]["payout"] or 0) > 0 or (ex["buyback_yield"] or 0) > 0,
        "Insiders not net sellers (12 months)": None if ins12 is None else ins12 >= 0,
        "ROIC above cost of capital": None if wacc_spread is None else wacc_spread > 0,
    }
    mgmt = 1 + sum(1 for v in mt.values() if v is True)
    ev["management"] = [f"{'n/a' if v is None else 'PASS' if v else 'fail'}: {k}" for k, v in mt.items()]

    # 4. Valuation (price) -- kept separate from business quality
    pe, fy = metrics["pe_ttm"]["value"], ex["fcf_yield"]
    val = _avg_stars(_stars(fy, (0.02, 0.03, 0.04, 0.06)), _stars(pe, (15, 20, 25, 35), higher_better=False)
                     if pe and pe > 0 else None)
    ev["valuation"] = [f"P/E (TTM) {pe}" if pe else "P/E n/a", f"FCF yield {fy:.1%}" if fy is not None else "FCF yield n/a"]

    # 5. Long-term visibility proxies (growth consistency + margin stability)
    rg = ex["revenue_growth"]["cagr_5y"] or ex["revenue_growth"]["cagr_3y"]
    opm = vals(metrics["operating_margin"]["history"])
    rng = (max(opm) - min(opm)) if opm else None
    vis = _avg_stars(_stars(rg, (0.0, 0.03, 0.06, 0.10)), _stars(rng, (0.15, 0.10, 0.06, 0.03), higher_better=False))
    ev["visibility"] = [f"Revenue CAGR {rg:.1%}" if rg is not None else "Revenue CAGR n/a",
                        f"Operating margin range {rng:.1%}" if rng is not None else "Margin range n/a",
                        "Industry runway and disruption risk are qualitative and are not scored."]

    stars = {"moat": moat, "financials": fin, "management": mgmt, "valuation": val, "visibility": vis}
    hold = {5: "5 years or more", 4: "3 to 5 years", 3: "1 to 3 years"}.get(moat or 0, "not a Buffett candidate")
    if moat is None:
        tag = "insufficient data"
    elif moat >= 4 and val is not None and val <= 2:
        tag = "Great business, wait for price"
    elif moat <= 2 and val is not None and val >= 4:
        tag = "Cheap but no moat"
    elif moat >= 4 and val is not None and val >= 3:
        tag = "Quality at a reasonable price"
    else:
        tag = "Mixed"
    return {"stars": stars, "evidence": ev, "holding_period": hold, "tag": tag,
            "moat_width": "wide" if (moat or 0) >= 4 else "narrow" if moat == 3 else "none or weak",
            "note": "Star thresholds are this app's own rubric; the skill's scoring file was not available."}

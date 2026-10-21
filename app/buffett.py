"""Buffett five-dimension moat diagnostic (1-5 stars each).

The skill's scoring rubric file (references/scoring.md) is not shipped in this repo, so the star thresholds
below are this app's own reading of the skill's descriptions. Quality and price are scored separately, as
the skill requires ("a great business at a fair price...").
"""
from .metrics import debt_of, latest
from .quality import roic_history
from .util import mean, num, sdiv, vals


# Executives at large companies sell stock steadily (vesting awards, tax cover, pre-set 10b5-1 plans), so "any net
# selling" fails almost every large company. Only selling above this share of market cap over 12 months counts against it.
INSIDER_SELL_MAX = 0.001
INSIDER_TEST = "Insider net selling under 0.1% of market cap (12 months)"


def _stars(v, cuts, higher_better=True):
    """cuts = 4 ascending thresholds -> 1..5 stars."""
    if v is None:
        return None
    s = 1 + sum(1 for c in cuts if (v >= c if higher_better else v <= c))
    return s


def _avg_stars(*xs):
    v = [x for x in xs if x is not None]
    return int(sum(v) / len(v) + 0.5) if v else None   # half up; round() would send 4.5 to 4 and 3.5 to 4


def _scaled(tests: dict, n: int, base: int):
    """base + passes, with the passes scaled up to `n` when some tests have no data, so a missing input neither counts
    as a fail nor lowers the ceiling. None when no test could be run."""
    known = [v for v in tests.values() if v is not None]
    if not known:
        return None
    return base + int(sum(1 for v in known if v) * n / len(known) + 0.5)


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
    de = metrics["debt_to_ebitda"]
    liab = sdiv(tl, ta)
    tests = {  # None = inputs missing (shown as n/a, left out of the score)
        "ROE at least 15% in most years": sum(1 for x in roes if x >= 0.15) >= max(1, len(roes) * 0.6) if roes else None,
        "Free cash flow positive every year": all(x > 0 for x in fcfs) if fcfs else None,
        "Liabilities at most 50% of assets": None if liab is None else liab <= 0.5,
        "Debt/EBITDA under 2x (or net cash)": True if de["net_cash"] else None if de["gross"] is None else de["gross"] < 2,
        "Gross margin at least 30%": None if gm_avg is None else gm_avg >= 0.30,
    }
    fin = _scaled(tests, 5, base=0)
    if fin is not None:
        fin = max(1, fin)
    ev["financials"] = [f"{'n/a' if v is None else 'PASS' if v else 'fail'}: {k}" for k, v in tests.items()]

    # 3. Management / capital allocation
    sc = ex["share_count_change_3y"]
    ins12, mcap = ex["insider_net"]["m12"], num(quote.get("market_cap"))
    mt = {  # None = inputs missing (shown as n/a, not counted as a pass)
        "Share count shrinking over 3 years": None if sc is None else sc < 0,
        "Pays a dividend or buys back stock": (ex["dividend"]["payout"] or 0) > 0 or (ex["buyback_yield"] or 0) > 0,
        INSIDER_TEST: None if ins12 is None else ins12 >= 0 or (None if not mcap else -ins12 <= INSIDER_SELL_MAX * mcap),
        "ROIC above cost of capital": None if wacc_spread is None else wacc_spread > 0,
    }
    mgmt = _scaled(mt, 4, base=1)
    ev["management"] = [f"{'n/a' if v is None else 'PASS' if v else 'fail'}: {k}" for k, v in mt.items()]
    if ins12 is not None and ins12 < 0:
        share = f"{-ins12 / mcap:.3%} of market cap" if mcap else "market cap unknown"
        ev["management"].append(f"Insiders sold a net ${-ins12 / 1e6:,.1f}M over 12 months ({share}). Routine sales by "
                                f"executives are common, so only selling above {INSIDER_SELL_MAX:.1%} of market cap fails.")

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

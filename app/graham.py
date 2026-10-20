"""Graham cigar-butt analysis: NCAV, 100-point static score, dynamic adjustments, value-trap rules.

Point splits and haircuts are this app's defaults (the skill's scoring.md was not available). Wording of the
summary is neutral and factual, as the skill's compliance note requires.
"""
from .metrics import insider_net, latest, ttm
from .util import num, r, sdiv

HAIRCUT = {"cash": 1.0, "receivables": 0.8, "inventory": 0.5, "other_current_assets": 0.5}


def ncav(p: dict):
    ca, tl, sh = num(p.get("current_assets")), num(p.get("total_liabilities")), num(p.get("shares"))
    if ca is None or tl is None or not sh:
        return None, None
    classic = (ca - tl) / sh
    parts = {k: num(p.get(k)) for k in HAIRCUT}
    if any(v is None for v in parts.values()):
        return classic, None
    adj_ca = sum(parts[k] * HAIRCUT[k] for k in HAIRCUT)
    return classic, (adj_ca - tl) / sh


def analyze(stm: dict, quote: dict, metrics: dict, insiders: list) -> dict:
    from .quality import is_financial
    if is_financial(quote):
        return {"applicable": False, "note": "NCAV does not fit banks, insurers or REITs."}
    ann, qs = stm["annual"], stm.get("quarterly") or []
    A = latest(stm)
    px = quote["price"]
    ex = metrics["extra"]
    classic, adj = ncav(qs[-1] if qs and qs[-1].get("current_assets") else A)

    pts, detail = {}, {}
    base = adj if adj is not None else classic
    if base is not None and base > 0:
        ratio = px / base
        pts["NCAV"] = 25 if ratio <= 0.67 else 18 if ratio <= 1 else 8 if ratio <= 1.5 else 0
        detail["NCAV"] = f"Price is {ratio:.2f}x NCAV per share"
    else:
        pts["NCAV"] = 0
        detail["NCAV"] = "NCAV per share is zero or negative, or inputs are missing"
    pe, pb = metrics["pe_ttm"]["value"], quote.get("pb")
    pts["PE"] = 0 if not pe or pe <= 0 else 15 if pe <= 10 else 11 if pe <= 15 else 6 if pe <= 20 else 0
    detail["PE"] = f"P/E {pe}" if pe else "P/E n/a or earnings not positive"
    pb = num(pb)
    pts["PB"] = 0 if pb is None else 15 if pb <= 1 else 11 if pb <= 1.5 else 6 if pb <= 2.5 else 0
    detail["PB"] = f"P/B {pb:.2f}" if pb is not None else "P/B n/a"
    dy = ex["dividend"]["yield"]
    pts["Dividend"] = 0 if not dy else 15 if dy >= 0.03 else 10 if dy >= 0.02 else 5
    detail["Dividend"] = f"Yield {dy:.1%}" if dy else "No dividend (waiting cost is high)"
    cr, de = ex["current_ratio"], ex["net_debt_equity"]
    from .metrics import latest_bs
    B = latest_bs(stm)   # same date as the NCAV and the current ratio
    debt_total = (num(B.get("st_debt")) or 0) + (num(B.get("lt_debt")) or 0)
    dte = sdiv(debt_total, B.get("equity"))
    pts["Debt cover"] = (0 if cr is None else 8 if cr >= 2 else 5 if cr >= 1.5 else 2 if cr >= 1 else 0) + \
        (0 if dte is None else 7 if dte <= 1 else 3 if dte <= 2 else 0)
    detail["Debt cover"] = f"Current ratio {cr}, debt/equity {dte:.2f}" if dte is not None else f"Current ratio {cr}"
    nis = [num(p.get("net_income")) for p in ann[-5:] if num(p.get("net_income")) is not None]
    pts["Earnings stability"] = round(15 * sum(1 for x in nis if x > 0) / len(nis)) if nis else 0
    detail["Earnings stability"] = f"{sum(1 for x in nis if x > 0)} profitable years of {len(nis)}"
    static = sum(pts.values())

    # dynamic adjustments
    adjs, notes = 0, []
    ncav_hist = [(num(q.get("current_assets")) - num(q.get("total_liabilities")))
                 for q in qs[-5:] if num(q.get("current_assets")) is not None and num(q.get("total_liabilities")) is not None]
    shrinking = len(ncav_hist) >= 3 and all(ncav_hist[i] < ncav_hist[i - 1] for i in range(len(ncav_hist) - 2, len(ncav_hist)))
    if len(ncav_hist) >= 2 and ncav_hist[0] > 0:
        chg = ncav_hist[-1] / ncav_hist[0] - 1
        if chg < -0.10:
            adjs -= 10
        elif chg > 0:
            adjs += 3
        notes.append(f"Total NCAV changed {chg:+.0%} over the last {len(ncav_hist)} quarters")
    eps_g = None
    if len(qs) >= 8:
        a, b = sum(q.get("eps") or 0 for q in qs[-4:]), sum(q.get("eps") or 0 for q in qs[-8:-4])
        eps_g = a / b - 1 if b > 0 else None
        if eps_g is not None:
            adjs += 3 if eps_g > 0 else -5
            notes.append(f"EPS trailing four quarters vs prior four: {eps_g:+.0%}")
    net6 = insider_net(insiders, 6) if insiders else None
    if net6:
        adjs += 3 if net6 > 0 else -5
        notes.append("Insiders net " + ("buyers" if net6 > 0 else "sellers") + " over 6 months")
    if insiders is None:
        notes.append("Insider data unavailable (the Form 4 feed failed), so the insider adjustment is not applied.")
    notes.append("Industry cycle (PMI, inventory cycle) is not assessed: no data source is wired in.")
    adjusted = max(0, min(100, static + adjs))

    # value-trap rules (2 of 5 trips => trap)
    neg_ocf = sum(1 for q in qs[-4:] if num(q.get("ocf")) is not None and q["ocf"] < 0)
    oi_t, _ = ttm(stm, "operating_income")
    int_t, _ = ttm(stm, "interest_expense")
    trips = {
        "NCAV shrinking two quarters running": shrinking,
        "Insiders net selling (6 months)": bool(net6 is not None and net6 < 0),
        "EPS declining year on year": eps_g is not None and eps_g < 0,
        "Operating cash flow negative in 2 of the last 4 quarters": neg_ocf >= 2,
        "Interest coverage under 2x": bool(oi_t is not None and int_t and sdiv(oi_t, abs(int_t)) is not None
                                           and sdiv(oi_t, abs(int_t)) < 2),
    }
    trap = sum(trips.values()) >= 2

    buy_line = r(adj * 0.67, 2) if adj is not None else None
    tier = "value trap" if trap else "green" if adjusted >= 70 else "yellow" if adjusted >= 50 else "orange" if adjusted >= 30 else "red"
    hold = ("Exit or avoid: value-trap rules tripped." if trap else
            "Waiting cost is low (dividend at or above 3%)." if dy and dy >= 0.03 else
            "No dividend or a small one, so the wait is paid for by the price alone.")
    px_line = (f"Current price is {px / base:.2f}x per-share NCAV" if base and base > 0
               else "Per-share NCAV is not positive, so the NCAV test does not apply")
    return {
        "applicable": True, "static_score": static, "adjustment": adjs, "adjusted_score": adjusted, "tier": tier,
        "breakdown": [{"name": k, "points": v, "detail": detail[k]} for k, v in pts.items()],
        "dynamic_notes": notes, "value_trap": trap, "trap_rules": [{"rule": k, "tripped": v} for k, v in trips.items()],
        "ncav_per_share": r(classic, 2), "ncav_adjusted_per_share": r(adj, 2), "buy_line": buy_line,
        "haircuts": HAIRCUT, "holding_view": hold,
        "summary": [f"Valuation: {px_line}.",
                    f"Adjusted score {adjusted} of 100 (static {static}, adjustment {adjs:+d}).",
                    "Value-trap rules tripped." if trap else "No value-trap pattern detected (reference only)."],
    }

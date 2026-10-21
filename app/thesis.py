"""Builds the bull / bear lists from the computed metrics (rule table in the README), plus the neutral verdict.

Every entry cites the metric it came from. News never changes these lists. The optional Gemini narrative only
rephrases the facts below; it does not add data or score anything.
"""
import json
from typing import Optional

from . import config

LOW, HIGH = 0, 1  # indexes into config.T pairs: (bull threshold, bear threshold)


def _big(x):
    if x is None:
        return "n/a"
    a, sign = abs(x), "-" if x < 0 else ""
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if a >= div:
            return f"{sign}{a / div:.2f}{suf}"
    return f"{sign}{a:.0f}"


def _roic(x):
    """Above 100% the capital base is mostly cash (a net-cash company), so the exact figure is not meaningful."""
    return ">100%" if x is not None and x > 1 else _pct(x)


def _nonop(ex: dict) -> str:
    """Say so when a tenth or more of pre-tax income is not operating (investment gains, interest)."""
    s = ex.get("non_operating_share")
    return f"; {s:.0%} of pre-tax income is non-operating (investment gains, interest), which flatters net income" \
        if s is not None and s >= 0.10 else ""


def _cnt(x):
    return "n/a" if x is None else f"{x:.0f}"


def _pct(x):
    return "n/a" if x is None else f"{x:.1%}"


def summary(n_bull: int, n_bear: int, quality: dict) -> str:
    return (f"{n_bull} supporting points and {n_bear} cautionary points from the metrics; "
            f"{quality['passing']} of {quality['applicable']} quality scorecards pass. Reference only, not advice.")


def build(metrics: dict, quality: dict, buffett: dict, graham: dict, chart: dict, flows: dict, quote: dict,
          valuation: Optional[dict] = None) -> dict:
    bull, bear = [], []
    T = config.T

    def add(side, title, evidence, source):
        (bull if side == "bull" else bear).append({"title": title, "evidence": evidence, "source": source})

    ex = metrics["extra"]
    peg = metrics["peg"]["value"]
    if peg is not None:
        if peg < T["peg"][0]:
            add("bull", "Growth looks cheap versus its multiple", f"PEG {peg} ({metrics['peg']['growth_basis']} growth)", "PEG")
        elif peg > T["peg"][1]:
            add("bear", "Multiple is rich for the growth", f"PEG {peg} ({metrics['peg']['growth_basis']} growth)", "PEG")
    f = metrics["fcf_growth"]
    if f["cagr_3y"] is not None and f["cagr_3y"] > 0 and (f["yoy"] or 0) > 0:
        add("bull", "Free cash flow is growing", f"FCF growth {_pct(f['yoy'])} YoY, {_pct(f['cagr_3y'])} 3-year CAGR", "FCF growth")
    elif (f["yoy"] is not None and f["yoy"] < 0) or (f["fcf"] is not None and f["fcf"] < 0):
        add("bear", "Free cash flow is falling or negative", f"FCF growth {_pct(f['yoy'])} YoY; FCF {_big(f['fcf'])}", "FCF growth")
    d = metrics["debt_to_ebitda"]
    if d["net_cash"]:
        add("bull", "Net cash balance sheet", "Cash exceeds total debt", "Debt/EBITDA")
    elif d["gross"] is not None:
        if d["gross"] < T["debt_ebitda"][0]:
            add("bull", "Low leverage", f"Debt/EBITDA {d['gross']}x", "Debt/EBITDA")
        elif d["gross"] > T["debt_ebitda"][1]:
            add("bear", "High leverage", f"Debt/EBITDA {d['gross']}x", "Debt/EBITDA")
    ar = metrics["analyst_ratings"]
    tot = sum(x for x in (ar["buy"], ar["hold"], ar["sell"]) if x)
    if tot and ar["upside_pct"] is not None:
        buy_share = (ar["buy"] or 0) / tot
        if buy_share > 0.5 and ar["upside_pct"] > 0:
            add("bull", "Analysts lean positive", f"{_cnt(ar['buy'])} Buy / {_cnt(ar['hold'])} Hold / {_cnt(ar['sell'])} Sell; mean target {_pct(ar['upside_pct'])} above price", "Analyst ratings")
        elif buy_share < 0.5 or ar["upside_pct"] < 0:
            add("bear", "Analysts are cautious", f"{_cnt(ar['buy'])} Buy / {_cnt(ar['hold'])} Hold / {_cnt(ar['sell'])} Sell; mean target {_pct(ar['upside_pct'])} vs price", "Analyst ratings")
    om = [x for x in metrics["operating_margin"]["history"] if x is not None]
    if len(om) >= 3:
        if om[-1] < om[-2] < om[-3]:
            add("bear", "Operating margin has contracted two years running", " -> ".join(_pct(x) for x in om[-3:]), "Operating margin")
        elif om[-1] >= om[-2] >= om[-3]:
            add("bull", "Operating margin stable or expanding", " -> ".join(_pct(x) for x in om[-3:]), "Operating margin")
    roic = metrics["roic"]
    sp = quality["roic_vs_wacc"]["spread"]
    if roic is not None:
        if roic >= T["roic"][0] and (sp is None or sp > 0):
            add("bull", "High return on invested capital", f"ROIC {_roic(roic)}; spread over estimated WACC {_roic(sp)}", "ROIC")
        elif roic < T["roic"][1] or (sp is not None and sp < 0):
            add("bear", "Returns on capital are weak", f"ROIC {_roic(roic)}; spread over estimated WACC {_roic(sp)}", "ROIC")
    roe = metrics["roe"]["latest"]
    if roe is not None:
        if roe >= T["roe"][0]:
            add("bull", "Strong return on equity", f"ROE {_pct(roe)}", "ROE")
        elif roe < T["roe"][1]:
            add("bear", "Low return on equity", f"ROE {_pct(roe)}", "ROE")
    ni = metrics["net_income_growth"]
    if ni["cagr_3y"] is not None:
        if ni["cagr_3y"] >= T["ni_growth"][0]:
            add("bull", "Earnings growing at a double-digit pace", f"Net income {_pct(ni['yoy'])} YoY, {_pct(ni['cagr_3y'])} 3-year CAGR" + _nonop(ex), "Net income growth")
        elif ni["cagr_3y"] < T["ni_growth"][1]:
            add("bear", "Earnings are shrinking", f"Net income {_pct(ni['yoy'])} YoY, {_pct(ni['cagr_3y'])} 3-year CAGR", "Net income growth")
    cc = ex["cash_conversion"]
    if cc is not None:
        if cc >= T["cash_conversion"][0]:
            add("bull", "Earnings are backed by cash", f"FCF is {cc}x net income", "Cash conversion")
        else:
            add("bear", "Weak cash conversion", f"FCF is only {cc}x net income", "Cash conversion")
    ic = ex["interest_coverage"]
    if ic is not None and ic < T["interest_cov"][1]:
        add("bear", "Thin interest coverage", f"EBIT covers interest {ic}x", "Interest coverage")
    pz, az = quality["piotroski"], quality["altman_z"]
    if pz["score"] is not None and pz["band"] != "n/a":
        if pz["score"] >= T["piotroski"][0]:
            add("bull", "Strong fundamentals checklist", f"Piotroski F-score {pz['score']}/{pz.get('of', 9)} ({pz.get('basis', '')})", "Piotroski")
        elif pz["score"] <= T["piotroski"][1]:
            add("bear", "Weak fundamentals checklist", f"Piotroski F-score {pz['score']}/{pz.get('of', 9)} ({pz.get('basis', '')})", "Piotroski")
    if az.get("score") is not None and az["zone"] == "distress":
        add("bear", "Altman Z-score in the distress zone", f"Z {az['score']} (below 1.81)", "Altman Z")
    bm = quality["beneish_m"]
    if bm.get("flag"):
        why = "; ".join(f"{d['label']} ({d['name']} {d['value']})" for d in bm.get("drivers", []))
        add("bear", "Earnings-quality screen flags (Beneish)",
            f"M-score {bm['score']} (above -1.78)" + (f"; main drivers: {why}" if why else "") +
            ". Fast growth and non-cash investment gains often trip this screen: a prompt to look, not proof", "Beneish M")
    sh = ex["share_count_change_3y"]
    if sh is not None:
        if sh < -0.03:
            add("bull", "Share count is shrinking", f"Diluted shares {_pct(sh)} over 3 years", "Share count")
        elif sh > 0.03:
            add("bear", "Shareholders are being diluted", f"Diluted shares {_pct(sh)} over 3 years", "Share count")
    ins = flows["reported"]["insider"]
    insider_listed = False  # insiders already have their own point, so the flow point must not repeat them
    if ins["cluster_buy"]:
        add("bull", "Cluster of insider buying", "3 or more insiders bought within 30 days", "Insider trades")
        insider_listed = True
    elif ins["net_12m"] is not None and ins["net_12m"] < 0 and ins["net_6m"] is not None and ins["net_6m"] < 0:
        part = f", 12m data only from {ins['net_12m_partial_from']}" if ins.get("net_12m_partial_from") else ""
        add("bear", "Insiders are net sellers", f"Net insider flow {_big(ins['net_6m'])} (6m){part}", "Insider trades")
        insider_listed = True
    nr = flows["net_read"]

    def _flow_text(items):
        kept = [x for x in items if not (insider_listed and x.startswith("Insiders"))]
        return "; ".join(kept) + (" (insider activity is listed separately)" if len(kept) < len(items) else "")

    if nr["label"] == "accumulation":
        add("bull", "Flow indicators show accumulation", _flow_text(nr["signals_for"]), "Institutional flows")
    elif nr["label"] == "distribution":
        add("bear", "Flow indicators show distribution", _flow_text(nr["signals_against"]), "Institutional flows")
    sg = chart["signals"]
    e200 = sg["above_ema"].get("200")
    if e200 is True:
        add("bull", "Price above the 200-day EMA", f"{_pct(ex['from_ema200'])} above EMA 200", "Chart")
    elif e200 is False:
        add("bear", "Price below the 200-day EMA", f"{_pct(ex['from_ema200'])} vs EMA 200", "Chart")
    if sg["cross_50_200"] == "golden":
        add("bull", "Recent golden cross", "EMA 50 crossed above EMA 200 in the last ~60 sessions", "Chart")
    elif sg["cross_50_200"] == "death":
        add("bear", "Recent death cross", "EMA 50 crossed below EMA 200 in the last ~60 sessions", "Chart")
    if sg["price_vs_value_area"] == "above" and e200:
        add("bull", "Price above the volume-profile value area", "Trading above the range holding 70% of volume", "Volume profile")
    elif sg["price_vs_value_area"] == "below" and e200 is False:
        add("bear", "Price below the volume-profile value area", "Trading below the range holding 70% of volume", "Volume profile")
    if graham.get("applicable") and graham["value_trap"]:
        add("bear", "Graham value-trap rules tripped", ", ".join(r["rule"] for r in graham["trap_rules"] if r["tripped"]), "Graham")
    stars = buffett["stars"]
    if (stars["moat"] or 0) >= 4:
        add("bull", "Wide economic moat (app rubric)", f"Moat {stars['moat']}/5 stars; holding view {buffett['holding_period']}", "Buffett")
    elif stars["moat"] is not None and stars["moat"] <= 2:
        add("bear", "Little evidence of a moat (app rubric)", f"Moat {stars['moat']}/5 stars", "Buffett")
    if (stars["valuation"] or 0) <= 2 and stars["valuation"] is not None:
        add("bear", "Price is rich versus earnings and cash flow", f"Valuation {stars['valuation']}/5 stars", "Buffett")
    elif (stars["valuation"] or 0) >= 4:
        add("bull", "Attractive price versus earnings and cash flow", f"Valuation {stars['valuation']}/5 stars", "Buffett")

    hl = (valuation or {}).get("headline")
    if hl:
        rev = ((valuation.get("reverse_dcf") or {}).get("text") or "")
        if hl["price_to_fair"] <= 0.80:
            add("bull", "Price is well below the DCF fair value (app model)",
                f"Price {quote['price']:.2f} vs DCF {hl['value']:.2f} (range {hl['low']:.2f} to {hl['high']:.2f}); "
                f"margin of safety {hl['margin_of_safety']:.0%}", "Fair value")
        elif hl["price_to_fair"] >= 1.40:
            add("bear", "Price is well above the DCF fair value (app model)",
                f"Price {quote['price']:.2f} vs DCF {hl['value']:.2f} (range {hl['low']:.2f} to {hl['high']:.2f}). {rev}",
                "Fair value")

    px = quote["price"]
    levels = []
    if ar["target_mean"]:
        levels.append({"label": "Analyst mean target", "price": ar["target_mean"]})
    vp = chart["volume_profile"]
    for lab, key in (("Value area high", "vah"), ("Point of control", "poc"), ("Value area low", "val")):
        if vp.get(key):
            levels.append({"label": lab, "price": round(vp[key], 2)})
    if (graham.get("buy_line") or 0) > 0:
        levels.append({"label": "Graham buy line (0.67 x adjusted NCAV)", "price": graham["buy_line"]})
    if hl:
        levels.append({"label": "DCF fair value (app model)", "price": hl["value"]})
    if quote.get("high52"):
        levels.append({"label": "52-week high", "price": quote["high52"]})
    if quote.get("low52"):
        levels.append({"label": "52-week low", "price": quote["low52"]})
    levels.sort(key=lambda x: -x["price"])

    verdict = {
        "tag": buffett["tag"], "holding_period": buffett["holding_period"],
        "summary": summary(len(bull), len(bear), quality),
    }
    return {"bull": bull, "bear": bear, "levels": levels, "verdict": verdict, "price": px}


def narrate(payload: dict) -> Optional[str]:
    """Optional written summary from Gemini (REST API). Rephrases the supplied facts only."""
    if not config.GEMINI_API_KEY:
        return None
    try:
        import httpx
        r = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent",
            headers={"x-goog-api-key": config.GEMINI_API_KEY, "Content-Type": "application/json"},
            json={
                "systemInstruction": {"parts": [{"text":
                    "You summarise a stock analysis. Use ONLY the facts in the JSON. Do not add data, news, or "
                    "predictions. Write a balanced 2-paragraph summary: the bull case, then the bear case. "
                    "No buy or sell recommendation. Plain language."}]},
                "contents": [{"role": "user", "parts": [{"text": json.dumps(payload)[:12000]}]}],
                # headroom: some Gemini models spend output tokens on internal reasoning before the answer
                "generationConfig": {"temperature": 0.2, "maxOutputTokens": 2000},
            },
            timeout=30)
        r.raise_for_status()
        parts = r.json()["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip() or None
    except Exception:  # narrative is optional; never break the analysis
        return None

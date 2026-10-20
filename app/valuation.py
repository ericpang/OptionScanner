"""Fair-value estimate built by this app. It is NOT GuruFocus's GF Value (a proprietary number that needs a paid
GuruFocus API token); it is three transparent methods computed from the statements and the quote.

  1. DCF on free cash flow to equity, with a growth fade and a sensitivity grid, plus a REVERSE DCF that shows
     the growth today's price implies (this one needs no growth assumption from us).
  2. Earnings power value (zero-growth value of normalised operating earnings, plus cash less debt).
  3. Graham number, sqrt(22.5 x EPS x book value per share), taken from the quoted P/E and P/B.

Fair value per share = price x (equity value / market cap), so the share count cancels and is not needed.
Every input is returned so the page can show it. Missing inputs give n/a with the reason, never a guess.
"""
import math
from typing import Optional

from . import config
from .metrics import debt_of, latest, ttm
from .quality import is_financial
from .util import num, r

BANDS = [  # price / fair value  ->  wording. Cut-offs are this app's own.
    (0.80, "undervalued"), (0.95, "modestly undervalued"), (1.15, "roughly fair"), (1.40, "modestly overvalued"),
    (math.inf, "overvalued")]


def band(ratio: Optional[float]) -> Optional[str]:
    if ratio is None:
        return None
    return next(label for cut, label in BANDS if ratio <= cut)


def dcf_equity_value(fcf0: float, g1: float, ke: float, gt: float, stage1: int = 5, fade: int = 5) -> float:
    """PV of FCF: `stage1` years at g1, then growth fading linearly to gt over `fade` years, then a terminal value."""
    pv, f = 0.0, fcf0
    for t in range(1, stage1 + fade + 1):
        g = g1 if t <= stage1 else g1 + (gt - g1) * (t - stage1) / fade
        f *= 1 + g
        pv += f / (1 + ke) ** t
    tv = f * (1 + gt) / (ke - gt)
    return pv + tv / (1 + ke) ** (stage1 + fade)


def implied_growth(fcf0: float, mcap: float, ke: float, gt: float) -> Optional[float]:
    """Stage-1 growth that makes the DCF equal today's market cap (bisection). None if no value in range works."""
    lo, hi = -0.15, 0.80
    if dcf_equity_value(fcf0, lo, ke, gt) > mcap or dcf_equity_value(fcf0, hi, ke, gt) < mcap:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if dcf_equity_value(fcf0, mid, ke, gt) < mcap:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _median(xs):
    xs = sorted(xs)
    return None if not xs else xs[len(xs) // 2] if len(xs) % 2 else (xs[len(xs) // 2 - 1] + xs[len(xs) // 2]) / 2


def _vs(value: Optional[float], price: float) -> Optional[float]:
    return None if value is None or not price else r(value / price - 1)


def build(stm: dict, quote: dict, metrics: dict, quality: dict) -> dict:
    price, mcap = num(quote.get("price")), num(quote.get("market_cap"))
    if is_financial(quote):
        return {"applicable": False, "note": "Cash-flow and earnings-power models do not fit banks, insurers or REITs."}
    if not price or not mcap:
        return {"applicable": False, "note": "Needs a price and a market cap."}

    beta = num(quote.get("beta"))
    ke = config.RISK_FREE + (1.0 if beta is None else beta) * config.EQUITY_PREMIUM
    gt = min(config.TERMINAL_GROWTH, config.RISK_FREE)
    methods = []

    # ---- 1. DCF -----------------------------------------------------------------------------------------------
    o, c = ttm(stm, "ocf")[0], ttm(stm, "capex")[0]
    fcf0 = (o - abs(c)) if o is not None and c is not None else None
    dcf = {"name": "Discounted cash flow", "key": "dcf", "value": None, "vs_price": None}
    if fcf0 is None or fcf0 <= 0:
        dcf["note"] = "Free cash flow is missing or not positive, so a cash-flow model cannot be built."
        reverse = None
    else:
        fg, ng, rg = metrics["fcf_growth"]["cagr_3y"], metrics["net_income_growth"]["cagr_3y"], \
            metrics["extra"]["revenue_growth"]["cagr_3y"]
        pg = metrics["peg"]["growth_pct"] / 100 if metrics["peg"].get("growth_basis") == "forward_consensus" \
            and metrics["peg"].get("growth_pct") is not None else None
        cands = {"FCF 3y CAGR": fg, "Net income 3y CAGR": ng, "Revenue 3y CAGR": rg, "Consensus EPS growth": pg}
        used = {k: v for k, v in cands.items() if v is not None}
        raw = _median(list(used.values()))
        g1 = None if raw is None else min(max(raw, 0.0), config.DCF_MAX_GROWTH)
        if g1 is None:
            dcf["note"] = "No growth inputs were available."
            reverse = None
        else:
            base = dcf_equity_value(fcf0, g1, ke, gt)
            ratio = base / mcap
            grid = [{"growth": r(g1 + dg), "ke": r(ke + dk),
                     "fair_value": r(price * dcf_equity_value(fcf0, max(g1 + dg, -0.05), ke + dk, gt) / mcap, 2)}
                    for dk in (-0.01, 0.0, 0.01) for dg in (-0.05, 0.0, 0.05)]
            fair = price * ratio
            low = min(x["fair_value"] for x in grid)
            high = max(x["fair_value"] for x in grid)
            ig = implied_growth(fcf0, mcap, ke, gt)
            dcf.update(value=r(fair, 2), vs_price=_vs(fair, price), low=low, high=high, grid=grid,
                       inputs={"fcf_ttm": r(fcf0, 0), "stage1_growth": r(g1), "stage1_years": 5, "fade_years": 5,
                               "terminal_growth": r(gt), "discount_rate": r(ke), "growth_candidates": {
                                   k: (None if v is None else r(v)) for k, v in cands.items()},
                               "growth_median": None if raw is None else r(raw),
                               "growth_cap": config.DCF_MAX_GROWTH, "beta": 1.0 if beta is None else beta,
                               "beta_assumed": beta is None},
                       note="Free cash flow to equity, discounted at the cost of equity (risk-free + beta x premium). "
                            f"Growth is the median of the available growth rates, floored at 0 and capped at "
                            f"{config.DCF_MAX_GROWTH:.0%}. Free cash flow is before stock-based compensation, which flatters "
                            "software and chip companies, and beta is assumed 1.0 when the data has none.")
            reverse = {"implied_stage1_growth": None if ig is None else r(ig),
                       "text": ("No growth rate between -15% and +80% reproduces today's price." if ig is None else
                                f"Today's price is what a DCF gives if free cash flow grows about {ig:.0%} a year for "
                                f"5 years, then fades to {gt:.0%}. Compare that with the {g1:.0%} used above and the "
                                f"company's own record in the inputs.")}
    methods.append(dcf)

    # ---- 2. earnings power value ---------------------------------------------------------------------------------
    epv = {"name": "Earnings power value", "key": "epv", "value": None, "vs_price": None}
    rev_t = ttm(stm, "revenue")[0]
    margins = [x for x in metrics["operating_margin"]["history"] if x is not None]
    wacc = (quality.get("roic_vs_wacc") or {}).get("wacc")
    A = latest(stm)
    bs = (stm.get("quarterly") or [A])[-1] if (stm.get("quarterly") or [{}])[-1].get("total_assets") else A
    cash, debt = num(bs.get("cash")), debt_of(bs)
    if not rev_t or not margins or not wacc or cash is None or debt is None:
        epv["note"] = "Needs revenue, operating-margin history, a cost of capital, cash and debt."
    else:
        avg_m = sum(margins) / len(margins)
        nopat = rev_t * avg_m * (1 - config.DEFAULT_TAX)
        if nopat <= 0:
            epv["note"] = "Normalised operating earnings are not positive."
        else:
            eq = nopat / wacc + cash - debt
            fair = price * eq / mcap
            epv.update(value=r(fair, 2) if eq > 0 else None, vs_price=_vs(fair, price) if eq > 0 else None,
                       inputs={"revenue_ttm": r(rev_t, 0), "avg_operating_margin": r(avg_m, 3),
                               "nopat_normalised": r(nopat, 0), "wacc": r(wacc), "cash": r(cash, 0), "debt": r(debt, 0)},
                       note="Zero-growth value: normalised after-tax operating earnings divided by the cost of capital, "
                            "plus cash, less debt. It is a floor-style value that gives no credit for growth.")
    methods.append(epv)

    # ---- 3. Graham number ------------------------------------------------------------------------------------------
    gn = {"name": "Graham number", "key": "graham", "value": None, "vs_price": None}
    pe, pb = num(quote.get("pe")), num(quote.get("pb"))
    if pe and pb and pe > 0 and pb > 0:
        eps, bvps = price / pe, price / pb
        v = math.sqrt(22.5 * eps * bvps)
        gn.update(value=r(v, 2), vs_price=_vs(v, price),
                  inputs={"eps_ttm": r(eps, 2), "book_value_per_share": r(bvps, 2), "pe": r(pe, 2), "pb": r(pb, 2)},
                  note="sqrt(22.5 x EPS x book value per share), from the quoted P/E and P/B. Built for steady, "
                       "asset-backed companies, so it is usually far below the price of a growth stock.")
    else:
        gn["note"] = "Needs a positive P/E and P/B."
    methods.append(gn)

    head = dcf if dcf.get("value") else None
    ratio = (price / head["value"]) if head else None
    return {
        "applicable": True, "disclaimer": "App estimate. This is not GuruFocus's GF Value, which is proprietary.",
        "price": price, "methods": methods, "headline": None if not head else {
            "method": "dcf", "value": head["value"], "low": head["low"], "high": head["high"],
            "price_to_fair": r(ratio, 2), "margin_of_safety": r(1 - ratio, 3), "rating": band(ratio),
            "bands": [{"up_to": None if c == math.inf else c, "label": lab} for c, lab in BANDS]},
        "reverse_dcf": reverse, "discount_rate": r(ke), "terminal_growth": r(gt),
    }

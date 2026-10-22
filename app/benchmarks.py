"""The company against its industry, in two ways.

P/E, P/B and P/S are set against the industry median from Longbridge (`industry-valuation dist`): the median and range
of the multiple over the companies in the stock's industry that have a positive value, and the stock's rank among
them. The company side is the figure on the metric card, so "times the median" is always that card's value divided by
the median beside it.

Everything else (margins, returns, growth, leverage) is set against its peers (peers.py): the companies in the industry
closest to it in size, each measured by the same metric code as the company. That is `peer_values` and `compare_peers`.

Missing inputs give None (shown as n/a), never an estimate.
"""
from typing import Optional

import numpy as np

from . import config
from .metrics import latest, latest_bs
from .util import num, r, sdiv

MULTIPLES = ("pe", "pb", "ps")


def _compare(b: Optional[dict], mine) -> Optional[dict]:
    """One multiple. `b` is the provider's block for it and `mine` the figure on the metric card."""
    median = num((b or {}).get("median"))
    if median is None:
        return None
    mine = num(mine)
    if mine is not None and mine <= 0:
        mine = None   # a loss or negative equity gives a multiple with nothing to compare
    rank, total, theirs = b.get("rank"), b.get("total"), num(b.get("value"))
    # The rank and the range flag are Longbridge's, for Longbridge's own figure for the company. They describe the
    # card's figure only when the two agree (a different EPS basis, say, would not), so otherwise they are left out.
    same = mine is not None and theirs is not None and abs(theirs / mine - 1) <= config.BENCH_VALUE_TOLERANCE
    ranked = bool(same and rank and total and total > 1)
    ratio = r(sdiv(mine, median), 2)
    return {
        "median": r(median, 2), "low": r(b.get("low"), 2), "high": r(b.get("high"), 2),
        "company": r(mine, 2), "times_median": ratio,
        # Better or worse than the median, for the page's colours. A lower multiple is the cheaper one, so the better
        # (as in the README's bull and bear table). None when it is equal to the median or there is nothing to compare.
        "better": None if ratio is None or ratio == 1 else ratio < 1,
        "percentile": r((rank - 1) / (total - 1), 2) if ranked else None,   # share of the other companies it is above
        "rank": rank if same else None, "n": total,
        "outside": b.get("outside") if same else None,   # "above" or "below" the industry's range, which has no rank
        "thin": total is not None and total < config.BENCH_MIN_COMPANIES,
    }


def build(industry: Optional[dict], m: dict) -> Optional[dict]:
    """The comparison for each multiple the industry feed covers, or None when it gave nothing (an ETF, or a stock
    Longbridge does not place in an industry)."""
    if not industry:
        return None
    x = m["extra"]
    mine = {"pe": m["pe_ttm"]["value"], "pb": x["pb"], "ps": x["ps"]}
    out = {k: _compare(industry.get(k), mine[k]) for k in MULTIPLES}
    return {k: v for k, v in out.items() if v} or None


# ------------------------------------------------------------------------------------------------------ peers
# The figures compared with peers, in the order the page lists them. Each is read from the metrics the app computes
# for a company, so a peer is measured by the same code, over the same periods, as the company itself. P/E, P/B and P/S
# are not here: the industry median above covers them over the whole industry.
PEER_METRICS = {
    "operating_margin": lambda m: m["operating_margin"]["ttm"],
    "roic": lambda m: m["roic"],
    "roe": lambda m: m["roe"]["latest"],
    "debt_to_ebitda": lambda m: m["debt_to_ebitda"]["gross"],
    "fcf_growth": lambda m: m["fcf_growth"]["yoy"],
    "net_income_growth": lambda m: m["net_income_growth"]["yoy"],
    "revenue_growth": lambda m: m["extra"]["revenue_growth"]["yoy"],
    "gross_margin": lambda m: m["extra"]["gross_margin"]["latest"],
    "net_margin": lambda m: m["extra"]["net_margin"],
    "fcf_margin": lambda m: m["extra"]["fcf_margin"],
    "fcfe_margin": lambda m: m["extra"]["fcfe"]["margin"],
    "cash_conversion": lambda m: m["extra"]["cash_conversion"],
    "ev_ebitda": lambda m: m["extra"]["ev_ebitda"],
    "fcf_yield": lambda m: m["extra"]["fcf_yield"],
    "fcfe_yield": lambda m: m["extra"]["fcfe"]["yield"],
    "interest_coverage": lambda m: m["extra"]["interest_coverage"],
    "current_ratio": lambda m: m["extra"]["current_ratio"],
    "quick_ratio": lambda m: m["extra"]["quick_ratio"],
    "net_debt_equity": lambda m: m["extra"]["net_debt_equity"],
}
MIXES_PRICE = ("ev_ebitda", "fcf_yield", "fcfe_yield")   # use the share price and the statements together: meaningless in two currencies
NEEDS_EQUITY = ("roe", "net_debt_equity")  # meaningless when equity is zero or negative (Dell's ROE reads -300%)

# Which way is better for each figure: True when a higher one is. This is the app's own call, the same one its green,
# amber and red card tags and the README's bull and bear table already make: more margin, return, growth, cash and cover
# is better; less debt is better; and cheaper is better, so a lower EV / EBITDA but a higher FCF yield. A simplification,
# as any single direction is (a high ROE can come from debt, a low multiple from trouble), but it is stated, not hidden.
HIGHER_IS_BETTER = {
    "operating_margin": True, "roic": True, "roe": True, "debt_to_ebitda": False, "fcf_growth": True,
    "net_income_growth": True, "revenue_growth": True, "gross_margin": True, "net_margin": True, "fcf_margin": True,
    "fcfe_margin": True, "cash_conversion": True, "ev_ebitda": False, "fcf_yield": True, "fcfe_yield": True,
    "interest_coverage": True, "current_ratio": True, "quick_ratio": True, "net_debt_equity": False,
}


def peer_values(m: dict, stm: dict, same_currency: bool = True) -> dict:
    """The compared figures for one company, the stock itself or a peer, from the metrics computed for it."""
    out = {key: r(get(m)) for key, get in PEER_METRICS.items()}
    if not same_currency:
        for key in MIXES_PRICE:
            out[key] = None
    if any(e is not None and e <= 0 for e in (num(p.get("equity")) for p in (latest(stm), latest_bs(stm)))):
        for key in NEEDS_EQUITY:
            out[key] = None
    return out


def compare_peers(mine: dict, peers: list) -> dict:
    """Each compared figure against the peers that have one: the median and the middle half of them, how the company
    stands (the gap, how many peers it is above, and times the median for a multiple) and how many peers there are.
    `better` says whether the company is better or worse than the median (HIGHER_IS_BETTER decides which way is
    better); None when it equals the median or there is nothing to compare. A median needs BENCH_MIN_PEERS peers with
    the figure, else it is None."""
    out = {}
    for key in PEER_METRICS:
        higher = HIGHER_IS_BETTER[key]
        theirs = [v for v in (num(p["values"].get(key)) for p in peers) if v is not None]
        c = num(mine.get(key))
        item = {"company": r(c), "n": len(theirs), "median": None, "q1": None, "q3": None,
                "diff": None, "ratio": None, "above": None, "better": None, "higher_is_better": higher}
        if len(theirs) >= config.BENCH_MIN_PEERS:
            q1, med, q3 = (float(x) for x in np.percentile(theirs, [25, 50, 75]))
            item.update(median=r(med), q1=r(q1), q3=r(q3))
            if c is not None:
                item.update(diff=r(c - med), above=sum(1 for v in theirs if v < c),
                            ratio=r(c / med, 2) if c > 0 and med > 0 else None)
                if item["diff"] != 0:
                    item["better"] = (item["diff"] > 0) == higher
        out[key] = item
    return out

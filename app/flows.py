"""Institutional buying/selling indicators: reported activity + volume-based flow indicators + a tally."""
from typing import Optional

import numpy as np

from .metrics import insider_net
from .util import mean, num, r


def obv(k: list[dict]) -> list[float]:
    out, t = [0.0], 0.0
    for i in range(1, len(k)):
        t += k[i]["v"] if k[i]["c"] > k[i - 1]["c"] else -k[i]["v"] if k[i]["c"] < k[i - 1]["c"] else 0
        out.append(t)
    return out


def ad_line(k: list[dict]) -> list[float]:
    out, t = [], 0.0
    for b in k:
        rng = b["h"] - b["l"]
        mfm = ((b["c"] - b["l"]) - (b["h"] - b["c"])) / rng if rng else 0.0
        t += mfm * b["v"]
        out.append(t)
    return out


def cmf(k: list[dict], n=20) -> Optional[float]:
    w = k[-n:]
    if len(w) < n:
        return None
    num_ = den = 0.0
    for b in w:
        rng = b["h"] - b["l"]
        mfm = ((b["c"] - b["l"]) - (b["h"] - b["c"])) / rng if rng else 0.0
        num_ += mfm * b["v"]
        den += b["v"]
    return num_ / den if den else None


def updown(k: list[dict], n: int) -> Optional[float]:
    w = k[-(n + 1):]
    if len(w) < n + 1:
        return None
    up = sum(w[i]["v"] for i in range(1, len(w)) if w[i]["c"] > w[i - 1]["c"])
    dn = sum(w[i]["v"] for i in range(1, len(w)) if w[i]["c"] < w[i - 1]["c"])
    return up / dn if dn else None


def high_volume_days(k: list[dict], lookback=60) -> list[dict]:
    out = []
    for i in range(max(50, len(k) - lookback), len(k)):
        avg = np.mean([b["v"] for b in k[i - 50:i]])
        b = k[i]
        if b["v"] > 2 * avg and b["h"] > b["l"]:
            pos = (b["c"] - b["l"]) / (b["h"] - b["l"])
            if b["c"] > k[i - 1]["c"] and pos >= 0.66:
                out.append({"date": b["t"], "type": "accumulation", "volume_x": r(b["v"] / avg, 1)})
            elif b["c"] < k[i - 1]["c"] and pos <= 0.34:
                out.append({"date": b["t"], "type": "distribution", "volume_x": r(b["v"] / avg, 1)})
    return out


def build(klines: list[dict], insiders: Optional[list], ownership: Optional[dict], cap_flow: list) -> dict:
    ob, ad = obv(klines), ad_line(klines)
    c20, ud20, ud50 = cmf(klines), updown(klines, 20), updown(klines, 50)
    large20, window = None, "20d"
    cf = [x for x in cap_flow if num(x.get("large")) is not None][-20:]
    if len(cf) >= 10:
        large20 = sum(x["large"] for x in cf)
    elif cf:  # Longbridge gives only the latest session's split by order size
        large20, window = cf[-1]["large"], "latest session"

    all_holders = (ownership or {}).get("top_holders") or []
    holders = all_holders[:10]  # shown in the table; the signal uses the whole list (up to 20)
    h_change = mean([h.get("change_pct") for h in all_holders])
    shares_chg = [num(h.get("change_shares")) for h in all_holders if num(h.get("change_shares")) is not None]
    inst_change = num((ownership or {}).get("change_qoq"))
    q13 = inst_change if inst_change is not None else h_change
    if q13 is None and shares_chg:
        q13 = sum(shares_chg)  # net shares added by the listed holders (sign is what matters)
    breadth = {"listed": len(all_holders), "increased": sum(1 for c in shares_chg if c > 0),
               "decreased": sum(1 for c in shares_chg if c < 0), "net_shares": r(sum(shares_chg), 0) if shares_chg else None}
    from .metrics import insider_partial_from
    ins = {"net_3m": r(insider_net(insiders, 3), 0), "net_6m": r(insider_net(insiders, 6), 0),
           "net_12m": r(insider_net(insiders, 12), 0), "net_12m_partial_from": insider_partial_from(insiders)}
    recent = [t for t in (insiders or []) if t["type"] == "buy"]
    recent.sort(key=lambda t: t["date"])
    cluster = False
    for i, t in enumerate(recent):
        names = {u["name"] for u in recent[i:] if u["date"] <= _plus30(t["date"])}
        if len(names) >= 3:
            cluster = True
            break

    # Each signal: (value, text if it points to accumulation, text if it points to distribution, neutral name).
    # The wording follows the direction, so "against" never lists a phrase that reads as bullish.
    def tri(v, up, down):
        return None if v is None else (1 if up(v) else -1 if down(v) else 0)

    spec = [
        (tri(q13, lambda x: x > 0, lambda x: x < 0), "Top holders net adding (13F)", "Top holders net trimming (13F)",
         "Top-holder changes (13F)"),
        (None if not insiders else tri(ins["net_6m"], lambda x: x > 0, lambda x: x < 0),  # unavailable or none: missing
         "Insiders net buying (6m)", "Insiders net selling (6m)", "Insider trades (6m)"),
        (tri(large20, lambda x: x > 0, lambda x: x < 0), f"Large-order net inflow ({window})",
         f"Large-order net outflow ({window})", f"Large-order flow ({window})"),
        (None if len(ob) < 22 else tri(ob[-1] - ob[-21], lambda x: x > 0, lambda x: x < 0),
         "OBV rising (20d)", "OBV falling (20d)", "OBV trend (20d)"),
        (tri(c20, lambda x: x > 0, lambda x: x < 0), "Chaikin Money Flow above 0", "Chaikin Money Flow below 0",
         "Chaikin Money Flow (20)"),
        (tri(ud20, lambda x: x > 1.2, lambda x: x < 1 / 1.2), "Up-volume exceeds down-volume (20d)",
         "Down-volume exceeds up-volume (20d)", "Up/down volume ratio (20d)"),
    ]
    pos = sum(1 for v, *_ in spec if v == 1)
    neg = sum(1 for v, *_ in spec if v == -1)
    label = "accumulation" if pos >= 3 and pos > neg else "distribution" if neg >= 3 and neg > pos else "neutral"
    missing = [n for v, _, _, n in spec if v is None]
    neutral = [n for v, _, _, n in spec if v == 0]
    sig_for = [up for v, up, _, _ in spec if v == 1]
    sig_against = [down for v, _, down, _ in spec if v == -1]
    return {
        "reported": {"institutional_ownership_pct": r((ownership or {}).get("inst_pct")),
                     "change_qoq_pct": r(inst_change), "as_of": (ownership or {}).get("as_of"),
                     "top_holders": holders, "holder_breadth": breadth, "insider": {**ins, "cluster_buy": cluster}},
        "flow": {"capital_flow": cf, "large_order_net_20d": r(large20, 0), "large_order_window": window,
                 "obv": [round(x) for x in ob[-260:]], "ad_line": [round(x) for x in ad[-260:]],
                 "dates": [b["t"] for b in klines[-260:]],
                 "cmf_20": r(c20), "updown_volume_20d": r(ud20, 2), "updown_volume_50d": r(ud50, 2),
                 "high_volume_days": high_volume_days(klines)},
        "net_read": {"label": label, "signals_for": sig_for, "signals_against": sig_against,
                     "signals_neutral": neutral, "signals_missing": missing, "positive": pos, "negative": neg},
    }


def _plus30(d: str) -> str:
    from datetime import date, timedelta
    try:
        return (date.fromisoformat(d) + timedelta(days=30)).isoformat()
    except ValueError:
        return d

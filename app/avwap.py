"""Anchored VWAP (AVWAP) over the last N trading days, and the price/volume matrix that reads it. Computed server-side.

The anchor is the first of the last N closed sessions. From there the running sums
    sum(typical price x volume) / sum(volume),   typical price = (high + low + close) / 3
give the AVWAP of each later session. At the latest closed session it is the plain N-day volume-weighted average price;
what anchoring changes is the AVWAP of the sessions before it, which is what a "cross" is judged against. Nothing is
drawn on the chart: the page shows only the read of the latest closed session.
"""
from typing import NamedTuple, Optional

from . import config


class Rule(NamedTuple):
    id: str
    label: str
    bias: str
    tone: str      # the colour class the page uses: good / warn / bad / info
    strong: bool   # a strong call is drawn solid, the others tinted

    @property
    def text(self) -> str:
        return f"{self.label} ({self.bias})"


# In the order the framework lists them. decide() tests the chop zone and the breakout first (see there).
RULES = (
    Rule("accumulation", "Strong Institutional Accumulation", "Strong Bullish", "good", True),
    Rule("exhaustion", "Exhaustion / Liquidity Trap", "Caution / Neutral", "warn", False),
    Rule("distribution", "Aggressive Institutional Distribution", "Strong Bearish", "bad", True),
    Rule("fade", "Passive Fade / Lack of Interest", "Neutral / Bearish", "bad", False),
    Rule("breakout", "Trend Breakout/Breakdown Point", "Directional Trigger", "info", False),
    Rule("chop", "Chop Zone / Equilibrium", "Sit on Hands", "warn", False),
)
BY_ID = {r.id: r for r in RULES}


def _when(rule_id: str, days: int) -> str:
    """What each rule tests, in words, for the page."""
    avg = f"its {days}-day average"
    return {
        "accumulation": f"Close above the AVWAP and volume above {avg}",
        "exhaustion": f"Close above the AVWAP and volume below {avg}",
        "distribution": f"Close below the AVWAP and volume above {avg}",
        "fade": f"Close below the AVWAP and volume below {avg}",
        "breakout": f"Close crossed the AVWAP (it was on the other side the session before) and volume above {avg}",
        "chop": (f"The last {config.AVWAP_CHOP_BARS} closes span no more than {config.AVWAP_CHOP_ATR:g} x ATR({config.AVWAP_ATR_DAYS}) "
                 f"and the latest is within that distance of the AVWAP, and volume is within {config.AVWAP_FLAT_VOLUME:.0%} of {avg}"),
    }[rule_id]


def typical(b: dict) -> float:
    return (b["h"] + b["l"] + b["c"]) / 3


def anchored_vwap(bars: list[dict], anchor: int, end: int) -> list[Optional[float]]:
    """The AVWAP of bars[anchor:i+1] for each i from the anchor to `end`; None elsewhere, and None for as long as no
    shares have traded since the anchor."""
    out: list[Optional[float]] = [None] * len(bars)
    pv = vol = 0.0
    for i in range(anchor, end + 1):
        pv += typical(bars[i]) * bars[i]["v"]
        vol += bars[i]["v"]
        out[i] = pv / vol if vol > 0 else None
    return out


def avg_true_range(bars: list[dict], end: int, n: int) -> float:
    """Mean true range of the n bars ending at `end` (fewer if the history is shorter)."""
    trs = []
    for i in range(max(0, end - n + 1), end + 1):
        b = bars[i]
        tr = b["h"] - b["l"]
        if i > 0:
            pc = bars[i - 1]["c"]
            tr = max(tr, abs(b["h"] - pc), abs(b["l"] - pc))
        trs.append(tr)
    return sum(trs) / len(trs)


def side(close: float, line: float) -> str:
    """above / below / at. 'At' only ever means equal to within float noise, as on a series that never moves."""
    if abs(close - line) <= 1e-9 * abs(line):
        return "at"
    return "above" if close > line else "below"


def decide(here: str, rel_volume: float, crossed: Optional[str], consolidating: bool) -> str:
    """The matrix: which rule the latest closed session falls under.

    Rules 5 and 6 are special cases of rules 1-4 (a cross is a close on one side of the AVWAP, and hugging it is too), so
    they are tested first or they could never be reached. The chop zone goes before the breakout: price sitting on the
    AVWAP on ordinary volume wanders across it all the time, and each wander would otherwise read as a breakout. A cross
    on less than average volume is no breakout; it falls through to the quadrant it landed in.
    """
    flat = abs(rel_volume - 1) <= config.AVWAP_FLAT_VOLUME
    if here == "at" or (consolidating and flat):
        return "chop"
    if crossed and rel_volume > 1:
        return "breakout"
    if here == "above":
        return "accumulation" if rel_volume > 1 else "exhaustion"
    return "distribution" if rel_volume > 1 else "fade"


def _px(x: float) -> str:
    return f"{x:.2f}" if abs(x) >= 1 else f"{x:.4f}"


def _summary(rule_id: str, days: int, here: str, close: float, line: float, rel: float, crossed: Optional[str], k: int) -> str:
    """Two plain sentences: the measured facts, then what the matrix reads into them."""
    where = (f"Close {_px(close)} is at the {days}-day AVWAP ({_px(line)})" if here == "at" else
             f"Close {_px(close)} is {abs(close / line - 1):.1%} {here} the {days}-day AVWAP ({_px(line)})")
    facts = f"{where}; volume is {rel:.2f}x its {days}-day average."
    cost = f"the volume-weighted average price of the last {days} sessions"
    reading = {   # restates what was measured; the rule's own label carries the interpretation
        "accumulation": f"Price is above {cost} on more volume than usual.",
        "exhaustion": f"Price is above {cost} on less volume than usual.",
        "distribution": f"Price is below {cost} on more volume than usual.",
        "fade": f"Price is below {cost} on less volume than usual.",
        "breakout": (f"Price closed {'above' if crossed == 'up' else 'below'} the AVWAP after closing "
                     f"{'below' if crossed == 'up' else 'above'} it the session before, on more volume than usual."),
        "chop": ("Price and the volume-weighted average cost are level." if here == "at" else
                 f"Price is consolidating at the AVWAP: the last {k} closes span no more than {config.AVWAP_CHOP_ATR:g} x "
                 f"ATR({config.AVWAP_ATR_DAYS}), the latest within that distance of it, and volume is close to its average."),
    }[rule_id]
    # The matrix splits volume at exactly its average, so a session just either side of it gets a different label.
    marginal = (f" Volume is within {config.AVWAP_FLAT_VOLUME:.0%} of its average, so the volume side of this call is marginal."
                if rule_id != "chop" and abs(rel - 1) <= config.AVWAP_FLAT_VOLUME else "")
    return f"{facts} {reading}{marginal}"


def matrix(days: int) -> list[dict]:
    return [{"id": r.id, "label": r.label, "bias": r.bias, "text": r.text, "tone": r.tone, "strong": r.strong,
             "when": _when(r.id, days)} for r in RULES]


def build(klines: list[dict], days: int, final: bool = True) -> dict:
    """The AVWAP block of the chart response: the read of the latest closed session.

    `final` is False when the newest bar was still forming when the data was fetched. Its volume is a fraction of a
    day's, so the read is made on the session before it.
    """
    n = len(klines)
    end = n - 1 if final else n - 2
    out = {
        "days": days, "min_days": config.AVWAP_MIN_DAYS, "max_days": config.AVWAP_MAX_DAYS,
        "status": "ok", "message": None, "last_bar_final": final, "newest_bar": klines[-1]["t"] if klines else None,
        "as_of": None, "anchor": None, "latest": None, "conclusion": None, "matrix": matrix(days),
        "rules": {"flat_volume": config.AVWAP_FLAT_VOLUME, "chop_bars": config.AVWAP_CHOP_BARS,
                  "chop_atr": config.AVWAP_CHOP_ATR, "atr_days": config.AVWAP_ATR_DAYS},
    }
    anchor = end - days + 1
    if anchor < 0:
        out.update(status="insufficient_history", message=f"A {days}-day AVWAP needs {days} closed trading days of "
                   f"history; {max(end + 1, 0)} are available.")
        return out
    line = anchored_vwap(klines, anchor, end)
    if line[end] is None:
        out.update(status="no_volume", message=f"No shares traded in the last {days} sessions, so a volume-weighted "
                   "average cannot be computed.")
        return out

    window = klines[anchor:end + 1]
    avg_volume = sum(b["v"] for b in window) / len(window)
    rel = klines[end]["v"] / avg_volume
    close = klines[end]["c"]
    here = side(close, line[end])
    crossed = None
    prev = end - 1
    if prev >= anchor and line[prev] is not None:
        before = side(klines[prev]["c"], line[prev])
        crossed = "up" if here == "above" and before != "above" else "down" if here == "below" and before != "below" else None
    # Consolidating at the AVWAP: a tight run of closes (they span no more than one ATR) that sits near it. Tightness
    # is judged on the closes alone, not on their distance to the AVWAP, because for a short anchor the AVWAP is the
    # average of those same closes and every run would count as hugging it.
    atr = avg_true_range(klines, end, config.AVWAP_ATR_DAYS)
    k = min(config.AVWAP_CHOP_BARS, len(window))
    recent = [b["c"] for b in klines[end - k + 1:end + 1]]
    reach = config.AVWAP_CHOP_ATR * atr
    consolidating = max(recent) - min(recent) <= reach and abs(close - line[end]) <= reach

    rule = BY_ID[decide(here, rel, crossed, consolidating)]
    out.update(
        as_of=klines[end]["t"], anchor={"date": klines[anchor]["t"]},
        latest={"close": close, "avwap": round(line[end], 4), "distance": close / line[end] - 1, "side": here,
                "volume": klines[end]["v"], "avg_volume": avg_volume, "rel_volume": rel, "crossed": crossed,
                "consolidating": consolidating, "atr": atr},
        conclusion={"id": rule.id, "label": rule.label, "bias": rule.bias, "text": rule.text, "tone": rule.tone,
                    "strong": rule.strong, "direction": crossed if rule.id == "breakout" else None,
                    "summary": _summary(rule.id, days, here, close, line[end], rel, crossed, k)})
    return out

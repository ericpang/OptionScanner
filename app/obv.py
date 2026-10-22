"""On-Balance Volume (OBV) insights: the price / volume / OBV matrix and divergence between price and OBV. Server-side.

OBV is a running total of volume: a session's volume is added on an up close and subtracted on a down close. Its level
means nothing, only its direction does, so the read looks at the last N closed sessions (config.OBV_DAYS):
    price   rising / falling / flat   the net change in the close, against ATR
    volume  increasing / flat / decreasing   average volume over those sessions, against the N before them
    OBV     rising / falling / flat   the net change in OBV, as a share of the volume traded over those sessions
and looks the three up in RULES: one row for each of the 27 combinations. Thirteen rows are the framework's own; the other
fourteen were written for the combinations it does not list, and are marked (framework=False).
Divergence is a separate test: price makes a new high (low) over a longer window that OBV does not confirm.
"""
from typing import NamedTuple, Optional

from . import config
from .avwap import avg_true_range
from .flows import obv as obv_series


class Rule(NamedTuple):
    id: str
    price: str              # rising / falling / flat
    volume: str             # increasing / flat / decreasing
    obv: str                # rising / falling / flat
    label: str
    dynamic: str            # what is happening underneath
    action: str
    tone: str               # the colour class the page uses: good / warn / bad / info
    strong: bool            # a strong call is drawn solid, the others tinted
    side: Optional[str]     # "bull" / "bear": the list in the thesis it adds a point to; None adds none
    point: Optional[str]    # that point's title
    framework: bool = True  # False: written for a combination the framework's table does not list

    @property
    def text(self) -> str:
        return f"{self.label} ({self.action})"


# One row for each price / volume / OBV combination: price rising, falling, flat; within each, volume increasing,
# decreasing, flat; within each, OBV rising, falling, flat. The ones marked framework=False are this app's own readings.
RULES = (
    # ---- price rising
    Rule("strong_bullish", "rising", "increasing", "rising", "Strong Bullish Trend",
         "Institutional accumulation; buyers in full control.", "Buy / Hold longs", "good", True,
         "bull", "Volume backs the uptrend: price and OBV are rising on rising volume"),
    Rule("distribution_into_strength", "rising", "increasing", "falling", "Distribution Into Strength",
         "Heavy volume lifts the price, yet net volume is leaving; sellers are using the rally to unload.",
         "Take profits / Tighten stops", "bad", False,
         "bear", "Price is rising on heavy volume but OBV is falling (distribution into strength)", False),
    Rule("churning_rally", "rising", "increasing", "flat", "Churning Rally",
         "Price rises on heavy volume but net buying and selling cancel out; the advance is not being absorbed.",
         "Hold with tight stops / Wait for confirmation", "warn", False, None, None, False),
    Rule("low_volume_float", "rising", "decreasing", "rising", "Low-Volume Float",
         "Price drifts up on low liquidity; lack of selling pressure.", "Hold longs / Trail stops", "warn", False, None, None),
    Rule("bearish_divergence", "rising", "decreasing", "falling", "Bearish Divergence",
         "Smart money is quietly exiting; retail chasing a hollow rally.", "Exit longs / Look to Short", "bad", True,
         "bear", "Price is rising but OBV is falling on thinner volume (bearish divergence)"),
    Rule("exhausted_rally", "rising", "decreasing", "flat", "Exhausted Rally",
         "Price ticks higher on alternating low-vol days; no buying backing.", "Take partial profits", "warn", False, None, None),
    Rule("steady_advance", "rising", "flat", "rising", "Steady Advance",
         "Price climbs on unchanged volume with buying running ahead of selling.", "Hold longs", "good", False,
         "bull", "Price and OBV are rising together on steady volume", False),
    Rule("unconfirmed_rally", "rising", "flat", "falling", "Unconfirmed Rally",
         "Price climbs on unchanged volume while net volume leaves; the rise has no buying behind it.",
         "Take partial profits / Tighten stops", "bad", False,
         "bear", "Price is rising but OBV is falling on steady volume (unconfirmed rally)", False),
    Rule("idle_advance", "rising", "flat", "flat", "Idle Advance",
         "Price edges up on unchanged volume with no net buying or selling behind it.", "Hold longs / Trail stops", "warn", False,
         None, None, False),
    # ---- price falling
    Rule("absorbed_selling", "falling", "increasing", "rising", "Absorbed Selling",
         "Heavy volume pushes the price down, yet net volume is arriving; buyers are soaking up the selling.",
         "Look to Buy reversals / Wait for confirmation", "good", False,
         "bull", "Price is falling on heavy volume but OBV is rising (selling is being absorbed)", False),
    Rule("strong_bearish", "falling", "increasing", "falling", "Strong Bearish Trend",
         "Institutional distribution; aggressive liquidation.", "Avoid buying / Short", "bad", True,
         "bear", "Volume backs the decline: price and OBV are falling on rising volume"),
    Rule("churning_decline", "falling", "increasing", "flat", "Churning Decline",
         "Price falls on heavy volume but net buying and selling cancel out; sellers have not taken control and buyers have not stepped in.",
         "Avoid buying / Wait for base", "warn", False, None, None, False),
    Rule("bullish_divergence", "falling", "decreasing", "rising", "Bullish Divergence",
         "Smart money is absorbing panic selling and accumulating dips.", "Look to Buy reversals", "good", True,
         "bull", "Price is falling but OBV is rising on thinner volume (bullish divergence)"),
    Rule("selling_exhaustion", "falling", "decreasing", "falling", "Selling Exhaustion",
         "Price drops but sellers are running out of shares/conviction.", "Watch for baseline support", "warn", False, None, None),
    Rule("illiquid_drift", "falling", "decreasing", "flat", "Illiquid Drift",
         "Price bleeds down purely due to a lack of buyers, not heavy selling.", "Stay sidelines / Wait for base", "warn", False, None, None),
    Rule("dip_buying", "falling", "flat", "rising", "Dip Buying",
         "Price slips on unchanged volume while net volume builds; the dips are being bought.", "Watch for a reversal", "good", False,
         "bull", "Price is slipping on steady volume while OBV rises (dips are being bought)", False),
    Rule("steady_decline", "falling", "flat", "falling", "Steady Decline",
         "Price falls on unchanged volume with selling running ahead of buying.", "Avoid buying / Stay out", "bad", False,
         "bear", "Price and OBV are falling together on steady volume", False),
    Rule("idle_decline", "falling", "flat", "flat", "Idle Decline",
         "Price slips on unchanged volume with no net buying or selling behind it.", "Stay sidelines / Wait for base", "warn", False,
         None, None, False),
    # ---- price flat
    Rule("silent_accumulation", "flat", "increasing", "rising", "Silent Accumulation",
         "Large blocks are bought inside a range without spiking price.", "Buy the upside breakout", "good", False,
         "bull", "Quiet accumulation: OBV is rising while price goes sideways on rising volume"),
    Rule("silent_distribution", "flat", "increasing", "falling", "Silent Distribution",
         "Large blocks are sold inside a range without crashing price.", "Short the downside breakdown", "bad", False,
         "bear", "Quiet distribution: OBV is falling while price goes sideways on rising volume"),
    Rule("high_volume_churn", "flat", "increasing", "flat", "High-Volume Churn",
         "Massive volume trading hands, but net buying/selling cancels out.", "Wait for a breakout candle", "warn", False, None, None),
    Rule("thin_accumulation", "flat", "decreasing", "rising", "Thin-Volume Accumulation",
         "Net buying edges ahead while volume dries up and price holds its range; a quiet build.", "Watch for the upside breakout",
         "good", False, None, None, False),
    Rule("thin_distribution", "flat", "decreasing", "falling", "Thin-Volume Distribution",
         "Net selling edges ahead while volume dries up and price holds its range.", "Watch for the downside breakdown",
         "bad", False, None, None, False),
    Rule("market_apathy", "flat", "decreasing", "flat", "Market Apathy",
         "No institutional interest; dead liquidity and zero momentum.", "Stay cash / Avoid", "warn", False, None, None),
    Rule("slow_accumulation", "flat", "flat", "rising", "Slow Accumulation",
         "Buying steadily outweighs selling on unchanged volume while price holds its range.", "Watch for the upside breakout",
         "good", False, None, None, False),
    Rule("slow_distribution", "flat", "flat", "falling", "Slow Distribution",
         "Selling steadily outweighs buying on unchanged volume while price holds its range.", "Watch for the downside breakdown",
         "bad", False, None, None, False),
    Rule("absolute_stagnation", "flat", "flat", "flat", "Absolute Stagnation",
         "Complete market freeze. Asset is heavily balanced or illiquid.", "Ignore ticker / Stay cash", "warn", False, None, None),
)
BY_ID = {r.id: r for r in RULES}
BY_PATTERN = {(r.price, r.volume, r.obv): r for r in RULES}

DIVERGENCE = {   # kind -> (side, thesis title)
    "bearish": ("bear", "Price made a new high that OBV did not confirm (bearish divergence)"),
    "bullish": ("bull", "Price made a new low that OBV did not confirm (selling pressure is fading)"),
}


def _px(x: float) -> str:
    return f"{x:.2f}" if abs(x) >= 1 else f"{x:.4f}"


def matrix() -> list[dict]:
    return [{"id": r.id, "price": r.price, "volume": r.volume, "obv": r.obv, "label": r.label, "dynamic": r.dynamic,
             "action": r.action, "text": r.text, "tone": r.tone, "strong": r.strong, "framework": r.framework} for r in RULES]


def price_state(change: float, atr: float) -> str:
    return "flat" if abs(change) <= config.OBV_FLAT_ATR * atr else "rising" if change > 0 else "falling"


def obv_state(share: float) -> str:
    return "flat" if abs(share) <= config.OBV_FLAT_SHARE else "rising" if share > 0 else "falling"


def volume_state(ratio: float) -> str:
    """Flat when the average is within OBV_FLAT_VOLUME of the earlier period's."""
    return "flat" if abs(ratio - 1) <= config.OBV_FLAT_VOLUME else "increasing" if ratio > 1 else "decreasing"


def decide(price: str, volume: str, obv: str) -> Rule:
    return BY_PATTERN[(price, volume, obv)]


def moving_average(values: list[float], n: int) -> list[Optional[float]]:
    """Simple moving average of the last n values; None before n are available."""
    out: list[Optional[float]] = [None] * len(values)
    run = 0.0
    for i, v in enumerate(values):
        run += v
        if i >= n:
            run -= values[i - n]
        if i >= n - 1:
            out[i] = run / n
    return out


def series(klines: list[dict], visible: int) -> dict:
    """OBV and its moving average for the last `visible` bars, lined up with the chart's candles. Both are shifted so
    OBV is 0 at the first of them: the running total starts at the beginning of the history, which is arbitrary."""
    ob = obv_series(klines)
    ma = moving_average(ob, config.OBV_MA_DAYS)
    start = max(0, len(klines) - visible)
    base = ob[start] if ob else 0.0
    return {"ma_days": config.OBV_MA_DAYS, "from": klines[start]["t"] if klines else None,
            "obv": [round(x - base) for x in ob[start:]],
            "ma": [None if x is None else round(x - base) for x in ma[start:]]}


def divergence(klines: list[dict], ob: list[float], end: int) -> dict:
    """Price against OBV over the last OBV_DIVERGENCE_DAYS sessions ending at `end`. A bearish divergence is a recent
    close above every close earlier in the window while OBV's recent peak stays below its earlier peak; a bullish one is
    the same upside down. The recent sessions are the last OBV_DIVERGENCE_RECENT."""
    span, recent = config.OBV_DIVERGENCE_DAYS, config.OBV_DIVERGENCE_RECENT
    out = {"status": "ok", "message": None, "kind": None, "window": span, "recent": recent, "side": None, "title": None,
           "evidence": None, "price": None, "obv": None,
           "summary": f"No divergence: in the last {recent} sessions price made no new {span}-session high or low that OBV failed to confirm."}
    if end + 1 < span:
        out.update(status="insufficient_history", summary=None,
                   message=f"Divergence looks at {span} sessions and needs {span} closed trading days of history; {max(end + 1, 0)} are available.")
        return out
    first, split = end - span + 1, end - recent + 1   # earlier sessions are [first, split), recent ones [split, end]
    earlier, now = range(first, split), range(split, end + 1)
    total = sum(klines[i]["v"] for i in range(first, end + 1))
    gap = config.OBV_DIVERGENCE_GAP * total
    c = lambda i: klines[i]["c"]
    p_hi, p_lo = max(now, key=c), min(now, key=c)
    e_hi, e_lo = max(earlier, key=c), min(earlier, key=c)
    o_hi, o_lo = max(ob[i] for i in earlier), min(ob[i] for i in earlier)
    n_hi, n_lo = max(ob[i] for i in now), min(ob[i] for i in now)
    found = {}   # kind -> (recent price bar, earlier price bar, recent OBV extreme, earlier OBV extreme)
    if c(p_hi) > c(e_hi) and n_hi < o_hi - gap:
        found["bearish"] = (p_hi, e_hi, n_hi, o_hi)
    if c(p_lo) < c(e_lo) and n_lo > o_lo + gap:
        found["bullish"] = (p_lo, e_lo, n_lo, o_lo)
    if not found:
        return out
    # Both can hold only after a very wide swing; the one whose price extreme is the more recent is the live one.
    kind = max(found, key=lambda k: found[k][0])
    p, e, n, o = found[kind]
    short = abs(o - n) / total if total else 0.0
    side, title = DIVERGENCE[kind]
    what, extreme, price_vs, obv_vs = (("high", "peak", "above", "below") if kind == "bearish" else ("low", "trough", "below", "above"))
    seen = f"the recent OBV {extreme} is {short:.1%} of the window's volume {obv_vs} its earlier one"
    out.update(
        kind=kind, side=side, title=title,
        price={"recent": c(p), "recent_date": klines[p]["t"], "earlier": c(e), "earlier_date": klines[e]["t"]},
        obv={"recent": n, "earlier": o, "shortfall_share": short},
        evidence=f"close {_px(c(p))} ({klines[p]['t']}) is {price_vs} the earlier {what} {_px(c(e))} ({klines[e]['t']}), but {seen}",
        summary=(f"Price closed at a new {span}-session {what} ({_px(c(p))} on {klines[p]['t']}, against {_px(c(e))} on "
                 f"{klines[e]['t']}) but OBV did not follow: {seen}. "
                 + ("The rally is not backed by net buying volume." if kind == "bearish" else
                    "Net volume is not confirming the decline: sellers may be losing force.")))
    return out


def _summary(rule: Rule, days: int, chg: float, ratio: float, share: float, p: str, v: str, o: str) -> str:
    moved = {"rising": "rose", "falling": "fell", "flat": "went sideways"}[p]
    obv_ = {"rising": "rose", "falling": "fell", "flat": "was flat"}[o]
    facts = (f"Over the last {days} sessions the close {moved} ({chg:+.1%}), average volume is {ratio:.2f}x that of the {days} "
             f"before, and OBV {obv_} (net {share:+.0%} of the volume traded).")
    own = " This row is the app's own reading; the framework's table does not list this combination." if not rule.framework else ""
    return f"{facts} {rule.dynamic}{own}"


def build(klines: list[dict], final: bool = True) -> dict:
    """The OBV block of the chart response: the read of the latest closed session, and the divergence test.

    `final` is False when the newest bar was still forming when the data was fetched. Its volume is a fraction of a
    day's, so the read is made on the session before it.
    """
    n, days = len(klines), config.OBV_DAYS
    end = n - 1 if final else n - 2
    out = {
        "days": days, "status": "ok", "message": None, "last_bar_final": final,
        "newest_bar": klines[-1]["t"] if klines else None, "as_of": None, "latest": None, "conclusion": None,
        "divergence": None, "matrix": matrix(),
        "rules": {"flat_atr": config.OBV_FLAT_ATR, "atr_days": config.AVWAP_ATR_DAYS, "flat_share": config.OBV_FLAT_SHARE,
                  "flat_volume": config.OBV_FLAT_VOLUME, "ma_days": config.OBV_MA_DAYS,
                  "divergence_gap": config.OBV_DIVERGENCE_GAP},
    }
    ob = obv_series(klines)
    out["divergence"] = divergence(klines, ob, end)
    if end + 1 < 2 * days:
        out.update(status="insufficient_history", message=f"The OBV read compares the last {days} sessions with the {days} before "
                   f"them and needs {2 * days} closed trading days of history; {max(end + 1, 0)} are available.")
        return out
    window = klines[end - days + 1:end + 1]
    prior = klines[end - 2 * days + 1:end - days + 1]
    vol, vol_before = sum(b["v"] for b in window), sum(b["v"] for b in prior)
    if vol <= 0 or vol_before <= 0:
        out.update(status="no_volume", message=f"No shares traded over the last {days} sessions or the {days} before them, "
                   "so volume cannot be compared.")
        return out
    base = klines[end - days]["c"]
    change = klines[end]["c"] - base
    atr = avg_true_range(klines, end, config.AVWAP_ATR_DAYS)
    ratio = vol / vol_before
    share = (ob[end] - ob[end - days]) / vol
    p, v, o = price_state(change, atr), volume_state(ratio), obv_state(share)
    rule = decide(p, v, o)
    chg = change / base
    ma = moving_average(ob, config.OBV_MA_DAYS)[end]
    vs_ma = None if ma is None else "above" if ob[end] > ma else "below" if ob[end] < ma else "at"
    out.update(
        as_of=klines[end]["t"],
        latest={"price_change": chg, "price_state": p, "atr": atr, "flat_band": config.OBV_FLAT_ATR * atr / base,
                "volume_ratio": ratio, "volume_state": v, "avg_volume": vol / days, "prior_avg_volume": vol_before / days,
                "obv_share": share, "obv_state": o, "obv_vs_ma": vs_ma},
        conclusion={"id": rule.id, "label": rule.label, "dynamic": rule.dynamic, "action": rule.action, "text": rule.text,
                    "tone": rule.tone, "strong": rule.strong, "side": rule.side, "title": rule.point,
                    "evidence": f"close {chg:+.1%}, volume {ratio:.2f}x the {days} sessions before, OBV net {share:+.0%} of the volume traded, over {days} sessions",
                    "summary": _summary(rule, days, chg, ratio, share, p, v, o)})
    return out


def points(read: Optional[dict]) -> list[dict]:
    """The points the OBV read adds to the bull and bear lists: [{side, title, evidence}], at most one per side. The
    matrix's call and the divergence test can agree (a bearish divergence read twice); they are then one point."""
    found: dict[str, list[tuple[str, str]]] = {}
    if read:
        c = read.get("conclusion")
        if read.get("status") == "ok" and c and c["side"]:
            found.setdefault(c["side"], []).append((c["title"], c["evidence"]))
        d = read.get("divergence")
        if d and d.get("kind"):
            found.setdefault(d["side"], []).append((d["title"], d["evidence"]))
    return [{"side": side, "title": items[0][0], "evidence": "; ".join(e for _, e in items)} for side, items in found.items()]

"""Price-history helpers for the Two-Stage filter: calendar windows, splits, returns between two dates, swings. No I/O.

Longbridge's forward-adjusted bars fold dividends into old prices, which makes them a total-return series: right for a
five-year return, wrong for the filter's price tests, which ask for prices adjusted for splits alone. So those tests take
the bars as they traded and apply the splits here. A bar is a dict with t (YYYY-MM-DD), o, h, l, c, v; lists are oldest first.
"""
import calendar
from datetime import date
from typing import Optional

from . import config


def months_back(day: str, months: int) -> str:
    """The date `months` calendar months before `day`, held to the end of the month it lands in: three months before
    31 May is 28 February (29 in a leap year), and five years before 29 February is 28 February."""
    d = date.fromisoformat(day)
    year, month = divmod(d.year * 12 + d.month - 1 - months, 12)
    month += 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1])).isoformat()


def last_on_or_before(bars: list, day: str) -> Optional[int]:
    """Index of the last bar dated on or before `day`, or None when the history starts after it."""
    found = None
    for i, b in enumerate(bars):
        if b["t"] > day:
            break
        found = i
    return found


def split_adjust(bars: list, splits: list) -> list:
    """The bars adjusted for splits alone. A bar dated before a split's ex-date is divided by its ratio (10 for a 10-for-1
    split, 0.1 for a 1-for-10 reverse split), so that every price is in today's shares. Volume is left as it traded."""
    out = []
    for b in bars:
        f = 1.0
        for s in splits:
            if s["date"] > b["t"]:
                f *= s["ratio"]
        out.append({**b, "o": b["o"] / f, "h": b["h"] / f, "l": b["l"] / f, "c": b["c"] / f})
    return out


def adjustments_agree(split_only: float, forward: float, floor: float) -> bool:
    """Whether a bar's split-only close and its forward-adjusted close differ by dividends alone. The forward-adjusted
    price is the lower of the two by the dividends paid since, so the ratio sits between `floor` and 1. Outside that, the
    price history was adjusted for something the splits do not account for (a bonus issue, a rights issue), and a return
    taken from it cannot be trusted."""
    return split_only > 0 and floor <= forward / split_only <= 1.001


def return_between(bars: list, start_day: str, end_day: str) -> Optional[dict]:
    """The close-to-close return from the last session on or before `start_day` to the last on or before `end_day`:
    {start, end, return}, where start and end are {date, close}. None when the history starts after `start_day`, since a
    shorter period would not be the one asked for."""
    if not bars or bars[0]["t"] > start_day:
        return None
    i, j = last_on_or_before(bars, start_day), last_on_or_before(bars, end_day)
    if i is None or j is None or j <= i or not bars[i]["c"]:
        return None
    a, b = bars[i], bars[j]
    return {"start": {"date": a["t"], "close": a["c"]}, "end": {"date": b["t"], "close": b["c"]}, "return": b["c"] / a["c"] - 1}


def pivots(bars: list, side: int = config.TS_PIVOT_SIDE) -> tuple[list, list]:
    """Indices of the confirmed pivot highs and lows. A pivot high has a daily high strictly above the `side` highs on
    each side of it, a pivot low a daily low strictly below the `side` lows on each side. The last `side` bars cannot be
    pivots: they have not yet been followed by the sessions that confirm one."""
    highs, lows = [], []
    for i in range(side, len(bars) - side):
        around = [i + k for k in range(-side, side + 1) if k]
        if all(bars[i]["h"] > bars[j]["h"] for j in around):
            highs.append(i)
        if all(bars[i]["l"] < bars[j]["l"] for j in around):
            lows.append(i)
    return highs, lows


def latest_swing(bars: list, start_day: str, side: int = config.TS_PIVOT_SIDE) -> Optional[dict]:
    """The latest completed upward swing since `start_day`: the latest confirmed pivot high, and the confirmed pivot low
    immediately before it, both dated on or after `start_day`. The retracement zone is 61.8% to 78.6% of the way back down
    from the high to the low. None when there is no such pair."""
    highs, lows = pivots(bars, side)
    highs = [i for i in highs if bars[i]["t"] >= start_day]
    if not highs:
        return None
    ih = highs[-1]
    before = [j for j in lows if j < ih and bars[j]["t"] >= start_day]
    if not before:
        return None
    il = before[-1]
    low, high = bars[il]["l"], bars[ih]["h"]
    if high <= low:
        return None
    span = high - low
    return {"low": {"price": low, "date": bars[il]["t"]}, "high": {"price": high, "date": bars[ih]["t"]},
            "zone": [high - 0.786 * span, high - 0.618 * span]}

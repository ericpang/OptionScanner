"""Exchange clocks: the local trading date of a daily bar, and whether its session had ended when the data was fetched.

Longbridge stamps a daily bar with local midnight written in UTC: the Hong Kong bar for Wednesday 21 October 2026 is
"2026-10-20T16:00:00Z". Taking the date off the front of that string gives Tuesday, so every Asian bar was labelled a
day early and each Monday bar landed on a Sunday, which the chart hides. US bars (04:00Z or 05:00Z) were never affected.
"""
import functools
from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Market suffix -> (time zone, regular close). Half days are not modelled: on those the latest bar is treated as
# unfinished for a few extra hours, so the read uses the session before.
MARKETS = {
    "US": ("America/New_York", time(16, 0)),
    "HK": ("Asia/Hong_Kong", time(16, 0)),    # closing auction until 16:10
    "SH": ("Asia/Shanghai", time(15, 0)),
    "SZ": ("Asia/Shanghai", time(15, 0)),
    "SG": ("Asia/Singapore", time(17, 0)),    # closing routine until 17:06
}
SETTLE = timedelta(minutes=15)   # after the close: the closing auction and the last prints


@functools.lru_cache(maxsize=None)
def _market(suffix: str) -> Optional[tuple[ZoneInfo, time]]:
    spec = MARKETS.get(suffix)
    if spec is None:
        return None
    try:
        return ZoneInfo(spec[0]), spec[1]
    except ZoneInfoNotFoundError:   # no time zone database (Windows without the tzdata package): behave as before
        return None


def _suffix(symbol: str) -> str:
    return symbol.rsplit(".", 1)[-1].upper()


def trading_date(symbol: str, stamp) -> str:
    """The local trading date (YYYY-MM-DD) of a daily bar from its timestamp. Anything that is not a UTC timestamp of
    a known market (a bare date, an unknown market, no time zone database) keeps the text before the 'T'."""
    text = str(stamp)
    market = _market(_suffix(symbol))
    if market is None or "T" not in text:
        return text[:10]
    try:
        t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:10]
    return text[:10] if t.tzinfo is None else t.astimezone(market[0]).date().isoformat()


def is_final(symbol: str, bar_date: str, fetched_at: datetime) -> bool:
    """False when the bar's session may still have been running when the data was fetched. Such a bar is partial, and
    its volume cannot be compared with a full day's. `fetched_at` must carry a time zone. An unknown market counts as
    final, which is how the rest of the app treats every bar."""
    market = _market(_suffix(symbol))
    if market is None:
        return True
    zone, close = market
    local = fetched_at.astimezone(zone)
    if bar_date != local.date().isoformat():
        return True   # a bar from an earlier day: its session is over
    return local >= datetime.combine(local.date(), close, tzinfo=zone) + SETTLE

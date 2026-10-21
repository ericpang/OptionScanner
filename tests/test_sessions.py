import os

os.environ["DATA_MODE"] = "demo"

from datetime import datetime, timezone

import pytest

from app import sessions
from app.data import CliProvider

UTC = timezone.utc


# ---------------------------------------------------------------- which day a bar belongs to
@pytest.mark.parametrize("symbol,stamp,want", [
    ("700.HK", "2026-10-15T16:00:00Z", "2026-10-16"),    # Hong Kong: local midnight is 16:00 UTC the day before
    ("700.HK", "2026-10-19T16:00:00Z", "2026-10-20"),    # (the 19th was a holiday there, so there is no bar for the Monday)
    ("700.HK", "2026-10-20T16:00:00Z", "2026-10-21"),
    ("600519.SH", "2026-10-20T16:00:00Z", "2026-10-21"),
    ("000001.SZ", "2026-10-20T16:00:00Z", "2026-10-21"),
    ("D05.SG", "2026-10-20T16:00:00Z", "2026-10-21"),
    ("AAPL.US", "2026-10-16T04:00:00Z", "2026-10-16"),    # New York in summer time: local midnight is 04:00 UTC
    ("AAPL.US", "2026-11-02T05:00:00Z", "2026-11-02"),    # ... and in winter time 05:00 UTC
    ("BRK.B.US", "2026-10-16T04:00:00Z", "2026-10-16"),   # a share class has a dot in the symbol
    ("aapl.us", "2026-10-16T04:00:00Z", "2026-10-16"),
    ("X.XX", "2026-10-20T16:00:00Z", "2026-10-20"),       # a market it does not know keeps the old reading
    ("700.HK", "2026-10-20", "2026-10-20"),               # a bare date is already a local date
    ("700.HK", "2026-10-20T00:00:00", "2026-10-20"),      # no time zone: not a UTC stamp
    ("700.HK", "TTTT-garbage", "TTTT-garba"),             # never raises
    ("700.HK", None, "None"),
])
def test_a_daily_bar_is_dated_by_its_local_trading_day(symbol, stamp, want):
    assert sessions.trading_date(symbol, stamp) == want


def test_bars_from_the_cli_carry_their_local_trading_dates():
    """The three Hong Kong bars and three US bars the CLI returned on 21 October 2026, as it sent them."""
    def raw(*stamps):
        return [{"close": "10", "high": "11", "low": "9", "open": "10", "time": t, "turnover": "1", "volume": "5"} for t in stamps]
    hk = CliProvider._bars(raw("2026-10-15T16:00:00Z", "2026-10-19T16:00:00Z", "2026-10-20T16:00:00Z"), "700.HK")
    assert [b["t"] for b in hk] == ["2026-10-16", "2026-10-20", "2026-10-21"]   # Friday, Tuesday, Wednesday
    us = CliProvider._bars(raw("2026-10-16T04:00:00Z", "2026-10-19T04:00:00Z", "2026-10-20T04:00:00Z"), "AAPL.US")
    assert [b["t"] for b in us] == ["2026-10-16", "2026-10-19", "2026-10-20"]   # unchanged
    assert hk[0] == {"t": "2026-10-16", "o": 10.0, "h": 11.0, "l": 9.0, "c": 10.0, "v": 5.0}


def test_no_hong_kong_bar_is_dated_on_a_weekend():
    """A bar labelled Sunday is hidden by the chart's weekend gap, which is how every Monday candle went missing."""
    from datetime import date, timedelta
    stamps, d = [], date(2026, 9, 1)
    while d < date(2026, 10, 1):   # every weekday's bar, as stamped: local midnight, written in UTC
        if d.weekday() < 5:
            stamps.append(f"{(d - timedelta(days=1)).isoformat()}T16:00:00Z")
        d += timedelta(days=1)
    raw = [{"close": "1", "high": "1", "low": "1", "open": "1", "time": s, "volume": "1"} for s in stamps]
    dates = [date.fromisoformat(b["t"]) for b in CliProvider._bars(raw, "700.HK")]
    assert len(dates) == 22 and all(x.weekday() < 5 for x in dates)


# ---------------------------------------------------------------- whether a bar is a finished session
def utc(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


@pytest.mark.parametrize("symbol,bar,at,want", [
    # New York, summer time (UTC-4): the session ends 16:00 and the bar is trusted 15 minutes later
    ("AAPL.US", "2026-10-21", utc(2026, 10, 21, 13, 31), False),   # 09:31, just after the open
    ("AAPL.US", "2026-10-21", utc(2026, 10, 21, 19, 59), False),   # 15:59
    ("AAPL.US", "2026-10-21", utc(2026, 10, 21, 20, 14), False),   # 16:14: the closing auction may still be printing
    ("AAPL.US", "2026-10-21", utc(2026, 10, 21, 20, 15), True),    # 16:15
    ("AAPL.US", "2026-10-20", utc(2026, 10, 21, 13, 31), True),    # yesterday's bar, today's open: that session is over
    # the same UTC time is an hour earlier on the clock in winter, so a bar that is final in summer is still forming
    ("AAPL.US", "2026-01-12", utc(2026, 1, 12, 20, 30), False),    # 15:30 EST
    ("AAPL.US", "2026-01-12", utc(2026, 1, 12, 21, 15), True),     # 16:15 EST
    ("AAPL.US", "2026-10-21", utc(2026, 10, 21, 20, 30), True),    # 16:30 EDT
    # Hong Kong closes 16:00, Shanghai and Shenzhen 15:00, Singapore 17:00 (all UTC+8)
    ("700.HK", "2026-10-21", utc(2026, 10, 21, 7, 59), False),     # 15:59
    ("700.HK", "2026-10-21", utc(2026, 10, 21, 8, 15), True),      # 16:15
    ("700.HK", "2026-10-20", utc(2026, 10, 21, 1, 0), True),
    ("600519.SH", "2026-10-21", utc(2026, 10, 21, 7, 14), False),
    ("600519.SH", "2026-10-21", utc(2026, 10, 21, 7, 15), True),
    ("D05.SG", "2026-10-21", utc(2026, 10, 21, 9, 14), False),
    ("D05.SG", "2026-10-21", utc(2026, 10, 21, 9, 15), True),
    # the local date can differ from the UTC date: 02:00 Hong Kong on the 22nd is 18:00 UTC on the 21st
    ("700.HK", "2026-10-21", utc(2026, 10, 21, 18, 0), True),
    ("X.XX", "2026-10-21", utc(2026, 10, 21, 1, 0), True),        # an unknown market counts as final, as every bar always did
])
def test_a_bar_is_final_once_its_session_has_ended(symbol, bar, at, want):
    assert sessions.is_final(symbol, bar, at) is want


def test_the_clock_follows_the_time_zone_database_not_a_fixed_offset():
    # US summer time began on Sunday 8 March 2026, so 20:30 UTC is 16:30 on the Monday but 15:30 on the Friday before
    assert sessions.is_final("AAPL.US", "2026-03-09", utc(2026, 3, 9, 20, 30)) is True
    assert sessions.is_final("AAPL.US", "2026-03-06", utc(2026, 3, 6, 20, 30)) is False


def test_without_a_time_zone_database_the_old_behaviour_is_kept(monkeypatch):
    from zoneinfo import ZoneInfoNotFoundError

    def missing(key):
        raise ZoneInfoNotFoundError(key)
    sessions._market.cache_clear()
    monkeypatch.setattr(sessions, "ZoneInfo", missing)
    try:
        assert sessions.trading_date("700.HK", "2026-10-20T16:00:00Z") == "2026-10-20"
        assert sessions.is_final("AAPL.US", "2026-10-21", utc(2026, 10, 21, 14, 0)) is True
    finally:
        sessions._market.cache_clear()   # do not leave the failed lookups cached for the other tests

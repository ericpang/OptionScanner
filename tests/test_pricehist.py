import os

os.environ["DATA_MODE"] = "demo"

import pytest

from app import pricehist as P


def bar(t, c, h=None, lo=None):
    return {"t": t, "o": c, "h": c if h is None else h, "l": c if lo is None else lo, "c": c, "v": 1.0}


# ---------------------------------------------------------------- calendar months
def test_months_back_holds_to_the_end_of_the_month_it_lands_in():
    assert P.months_back("2026-10-24", 3) == "2026-07-24"
    assert P.months_back("2026-05-31", 3) == "2026-02-28"            # there is no 31 February
    assert P.months_back("2024-05-31", 3) == "2024-02-29"            # ... except in a leap year
    assert P.months_back("2026-01-15", 3) == "2025-10-15"            # across a year end
    assert P.months_back("2026-10-24", 12) == "2025-10-24"
    assert P.months_back("2024-02-29", 60) == "2019-02-28"           # five years before a leap day


def test_the_last_session_on_or_before_a_date_may_be_the_day_itself_or_an_earlier_one():
    bars = [bar("2026-01-02", 1), bar("2026-01-05", 2), bar("2026-01-06", 3)]
    assert P.last_on_or_before(bars, "2026-01-04") == 0              # a Sunday: the Friday before
    assert P.last_on_or_before(bars, "2026-01-05") == 1              # the day itself
    assert P.last_on_or_before(bars, "2030-01-01") == 2
    assert P.last_on_or_before(bars, "2026-01-01") is None           # the history starts after it


# ---------------------------------------------------------------- splits
def test_a_split_divides_every_earlier_price_and_leaves_later_ones_alone():
    bars = [bar("2024-06-07", 1200.0), bar("2024-06-10", 121.0), bar("2024-06-11", 122.0)]
    out = P.split_adjust(bars, [{"date": "2024-06-10", "ratio": 10.0}])
    assert [round(b["c"], 2) for b in out] == [120.0, 121.0, 122.0]
    assert [b["c"] for b in bars] == [1200.0, 121.0, 122.0]          # the bars passed in are not changed
    assert out[0]["h"] == out[0]["l"] == out[0]["o"] == pytest.approx(120.0)


def test_a_reverse_split_multiplies_earlier_prices_and_two_splits_compound():
    out = P.split_adjust([bar("2023-08-23", 0.5), bar("2023-08-24", 5.0)], [{"date": "2023-08-24", "ratio": 0.1}])
    assert [round(b["c"], 6) for b in out] == [5.0, 5.0]             # 1-for-10: the old price is ten times as many dollars per new share
    both = P.split_adjust([bar("2021-01-04", 4000.0), bar("2021-08-02", 800.0), bar("2024-07-01", 120.0)],
                          [{"date": "2021-07-20", "ratio": 4.0}, {"date": "2024-06-10", "ratio": 10.0}])
    assert [round(b["c"], 2) for b in both] == [100.0, 80.0, 120.0]  # 4000 / 40, 800 / 10, and the last is in today's shares


def test_prices_that_differ_by_dividends_alone_agree_and_anything_else_does_not():
    assert P.adjustments_agree(100.0, 100.0, 0.85)                   # no dividends in the window
    assert P.adjustments_agree(100.0, 98.5, 0.85)                    # the forward-adjusted price is lower by the dividends paid since
    assert not P.adjustments_agree(100.0, 101.0, 0.85)               # it can never be higher
    assert not P.adjustments_agree(100.0, 50.0, 0.85)                # a missing 2-for-1 split, say
    assert not P.adjustments_agree(0.0, 0.0, 0.85)


# ---------------------------------------------------------------- returns between two dates
def test_a_return_runs_between_the_sessions_on_or_before_the_two_dates():
    bars = [bar("2021-10-22", 100.0), bar("2021-10-25", 110.0), bar("2026-10-23", 250.0)]
    r = P.return_between(bars, "2021-10-23", "2026-10-24")           # a Saturday for both: the Fridays before
    assert r["start"] == {"date": "2021-10-22", "close": 100.0} and r["end"] == {"date": "2026-10-23", "close": 250.0}
    assert r["return"] == pytest.approx(1.5)


def test_a_return_needs_the_history_to_reach_the_start_of_the_period_asked_for():
    bars = [bar("2022-03-01", 100.0), bar("2026-10-23", 250.0)]
    assert P.return_between(bars, "2021-10-23", "2026-10-23") is None    # a shorter period would be a different answer
    assert P.return_between([], "2021-10-23", "2026-10-23") is None
    assert P.return_between([bar("2021-10-22", 100.0)], "2021-10-23", "2026-10-23") is None   # start and end are the same session


# ---------------------------------------------------------------- swings
def path(highs, start="2026-01-01"):
    """Bars whose highs are `highs` and whose lows are one below, on consecutive days from `start`."""
    from datetime import date, timedelta
    d0 = date.fromisoformat(start)
    return [bar((d0 + timedelta(days=i)).isoformat(), h - 0.5, h, h - 1) for i, h in enumerate(highs)]


def test_a_pivot_is_strictly_beyond_the_two_sessions_on_each_side_and_the_last_two_cannot_be_pivots():
    bars = path([10, 9, 8, 7, 8, 9, 10, 11, 12, 11, 10, 9, 13, 14])
    highs, lows = P.pivots(bars)
    assert highs == [8]                                              # the 13 and the 14 at the end have not been followed by two sessions
    assert lows == [3, 11]                                           # the dip to 9 before the climb is a pivot low: it has two higher sessions after it
    flat = path([5, 5, 5, 5, 5, 5, 5])
    assert P.pivots(flat) == ([], [])                                # a tie is not "strictly" above
    assert P.pivots(path([1, 2, 1])) == ([], [])                     # too short to have two sessions on each side


def test_the_swing_is_the_latest_pivot_high_and_the_pivot_low_just_before_it():
    bars = path([10, 9, 8, 7, 8, 9, 10, 11, 12, 11, 10, 9])
    s = P.latest_swing(bars, "2026-01-01")
    assert s["low"] == {"price": 6, "date": bars[3]["t"]} and s["high"] == {"price": 12, "date": bars[8]["t"]}
    lo, hi = s["zone"]
    assert lo == pytest.approx(12 - 0.786 * 6) and hi == pytest.approx(12 - 0.618 * 6)   # 7.284 to 8.292


def test_a_swing_needs_both_pivots_inside_the_window_and_a_low_below_the_high():
    bars = path([10, 9, 8, 7, 8, 9, 10, 11, 12, 11, 10, 9])
    assert P.latest_swing(bars, bars[5]["t"]) is None                # the pivot low is before the window opens
    assert P.latest_swing(path([9, 8, 7, 6, 5, 4, 3, 2, 1, 2, 3, 4]), "2026-01-01") is None   # a decline: no pivot high with a low before it
    assert P.latest_swing([], "2026-01-01") is None

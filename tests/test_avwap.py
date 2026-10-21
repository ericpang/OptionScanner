import os

os.environ["DATA_MODE"] = "demo"

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import avwap, config, service
from app.config import _clamp_int
from app.data import Data
from app.main import app
from app.snapshot import SnapshotStore


def bars(closes, vols, spread=0.01, start=date(2026, 1, 5)):
    """Weekday bars; the high and low sit `spread` either side of each close, so the typical price is the close."""
    out, d = [], start
    for c, v in zip(closes, vols):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append({"t": d.isoformat(), "o": c, "h": c * (1 + spread), "l": c * (1 - spread), "c": c, "v": float(v)})
        d += timedelta(days=1)
    return out


N, DAYS = 40, 10   # forty sessions, a ten-day anchor: small enough to read, long enough that nothing is near an edge


def ramp(up: bool, last_volume: float):
    """A steady 1% a day with level volume, and a last session on `last_volume`: price is on one side of the line, never crossing it."""
    closes = [100 * (1.01 if up else 0.99) ** i for i in range(N)]
    return bars(closes, [1000] * (N - 1) + [last_volume])


def jump(up: bool, last_volume: float):
    """A slow drift one way, then a last session that gaps the other way through the AVWAP."""
    closes = [100 * (0.995 if up else 1.005) ** i for i in range(N - 1)]
    closes.append(closes[-1] * (1.2 if up else 0.8))
    return bars(closes, [1000] * (N - 1) + [last_volume])


def wobble(last_volume: float):
    """Closes alternating 0.2% either side of 100 on level volume: tight, on the line, crossing it every session."""
    return bars([100 + (0.2 if i % 2 else -0.2) for i in range(N)], [1000] * (N - 1) + [last_volume])


SPEC = {
    "accumulation": "Strong Institutional Accumulation (Strong Bullish)",
    "exhaustion": "Exhaustion / Liquidity Trap (Caution / Neutral)",
    "distribution": "Aggressive Institutional Distribution (Strong Bearish)",
    "fade": "Passive Fade / Lack of Interest (Neutral / Bearish)",
    "breakout": "Trend Breakout/Breakdown Point (Directional Trigger)",
    "chop": "Chop Zone / Equilibrium (Sit on Hands)",
}

SCENARIOS = {   # rule -> the bars that should land on it
    "accumulation": lambda: ramp(True, 2000),
    "exhaustion": lambda: ramp(True, 500),
    "distribution": lambda: ramp(False, 2000),
    "fade": lambda: ramp(False, 500),
    "breakout": lambda: jump(True, 3000),
    "chop": lambda: wobble(1000),
}


def read(b, days=DAYS, final=True):
    return avwap.build(b, days, final)


# ---------------------------------------------------------------- the AVWAP
def test_avwap_is_the_running_volume_weighted_typical_price_from_the_anchor():
    b = [{"t": "a", "h": 12, "l": 8, "c": 10, "v": 100}, {"t": "b", "h": 13, "l": 9, "c": 12, "v": 200},
         {"t": "c", "h": 16, "l": 10, "c": 13, "v": 300}]   # typical prices 10, 34/3, 13
    got = avwap.anchored_vwap(b, 0, 2)
    assert got == pytest.approx([10, (1000 + 200 * 34 / 3) / 300, (1000 + 200 * 34 / 3 + 3900) / 600])
    later = avwap.anchored_vwap(b, 1, 2)   # anchored one session later: nothing before it exists, and it forgets the first bar
    assert later[0] is None and later[1] == pytest.approx(34 / 3) and later[2] == pytest.approx((200 * 34 / 3 + 3900) / 500)


def test_the_latest_value_is_the_plain_n_day_volume_weighted_average_price():
    rng = np.random.default_rng(7)
    closes = 100 + np.cumsum(rng.normal(0, 1, 120))
    b = [{"t": str(i), "o": c, "h": c + abs(rng.normal(0, 1)), "l": c - abs(rng.normal(0, 1)), "c": c, "v": float(rng.uniform(1e6, 5e6))}
         for i, c in enumerate(closes)]
    for days in (5, 20, 60):
        w = b[-days:]
        want = sum((x["h"] + x["l"] + x["c"]) / 3 * x["v"] for x in w) / sum(x["v"] for x in w)
        assert avwap.build(b, days)["latest"]["avwap"] == pytest.approx(want, abs=1e-4)


def test_the_series_starts_at_the_anchor_and_the_read_ignores_everything_before_it():
    b = wobble(1500)
    out = read(b, days=20)
    anchor = N - 20
    line = avwap.anchored_vwap(b, anchor, N - 1)
    assert out["anchor"] == {"date": b[anchor]["t"]}
    assert all(v is None for v in line[:anchor]) and all(v is not None for v in line[anchor:])
    assert line[anchor] == pytest.approx((b[anchor]["h"] + b[anchor]["l"] + b[anchor]["c"]) / 3)   # the anchor bar's own typical price
    assert out["latest"]["avwap"] == pytest.approx(line[-1], abs=1e-4)
    other = [dict(x) for x in b]
    for x in other[:anchor]:
        x["c"], x["h"], x["l"], x["v"] = 500.0, 510.0, 490.0, 9e9   # wild bars before the anchor
    again = read(other, days=20)
    assert again["conclusion"] == out["conclusion"] and again["latest"] == out["latest"]


@pytest.mark.parametrize("days", [5, 10, 20, 35])
def test_the_number_of_days_moves_the_anchor(days):
    b = ramp(True, 1000)
    out = read(b, days=days)
    assert out["days"] == days and out["anchor"]["date"] == b[N - days]["t"]
    assert sum(v is not None for v in avwap.anchored_vwap(b, N - days, N - 1)) == days   # exactly the last `days` sessions


# ---------------------------------------------------------------- the matrix
def test_the_six_outputs_read_exactly_as_the_framework_words_them():
    assert {r.id: r.text for r in avwap.RULES} == SPEC
    tones = {r.id: (r.tone, r.strong) for r in avwap.RULES}
    assert tones["accumulation"] == ("good", True) and tones["distribution"] == ("bad", True)   # green and red, drawn solid
    assert tones["exhaustion"][0] == tones["chop"][0] == "warn"                                  # yellow for the trap and the chop zone
    assert [m["text"] for m in avwap.matrix(20)] == list(SPEC.values())


@pytest.mark.parametrize("rule", list(SPEC))
def test_each_rule_is_reached_from_price_and_volume(rule):
    out = read(SCENARIOS[rule]())
    assert out["status"] == "ok" and out["conclusion"]["id"] == rule and out["conclusion"]["text"] == SPEC[rule], out["latest"]


def test_a_cross_is_a_close_on_the_other_side_of_the_line_from_the_session_before():
    up, down = read(jump(True, 3000)), read(jump(False, 3000))
    assert up["latest"]["crossed"] == "up" and up["conclusion"]["direction"] == "up"
    assert down["latest"]["crossed"] == "down" and down["conclusion"]["direction"] == "down" and down["conclusion"]["id"] == "breakout"
    assert read(ramp(True, 3000))["latest"]["crossed"] is None   # already above the session before: no cross


def test_a_cross_on_less_than_average_volume_is_no_breakout():
    out = read(jump(True, 500))
    assert out["latest"]["crossed"] == "up" and out["conclusion"]["id"] == "exhaustion"   # it lands in the above / low-volume quadrant
    assert read(jump(False, 500))["conclusion"]["id"] == "fade"


def test_the_chop_zone_wins_over_a_breakout_when_price_is_sitting_on_the_line():
    out = read(wobble(1100))   # crosses the line, on slightly more than average volume, but the volume is flat and the closes are tight
    assert out["latest"]["crossed"] and out["latest"]["rel_volume"] > 1 and out["latest"]["consolidating"]
    assert out["conclusion"]["id"] == "chop"
    assert read(wobble(3000))["conclusion"]["id"] == "breakout"   # the same wobble on heavy volume is a real trigger


def test_closes_that_swing_wider_than_an_average_day_are_not_consolidating_even_next_to_the_line():
    out = read(bars([100.0] * 35 + [100.0, 104.0, 100.0, 104.0, 100.0], [1000] * N))   # a quiet spell, then a whipsaw week on flat volume
    last = out["latest"]
    reach = config.AVWAP_CHOP_ATR * last["atr"]
    assert abs(last["close"] - last["avwap"]) <= reach and 104.0 - 100.0 > reach   # near the line, but the week's closes span more than the reach
    assert last["consolidating"] is False and out["conclusion"]["id"] == "fade"    # so flat volume alone does not make it a chop zone


@pytest.mark.parametrize("here,rel,crossed,tight,want", [
    ("above", 1.5, None, False, "accumulation"), ("above", 0.5, None, False, "exhaustion"),
    ("below", 1.5, None, False, "distribution"), ("below", 0.5, None, False, "fade"),
    ("above", 1.5, "up", False, "breakout"), ("below", 1.5, "down", False, "breakout"),
    ("above", 0.5, "up", False, "exhaustion"), ("below", 0.5, "down", False, "fade"),
    ("above", 1.0, None, False, "exhaustion"), ("below", 1.0, None, False, "fade"),      # exactly average is not above average
    ("above", 1.19, None, True, "chop"), ("above", 1.21, None, True, "accumulation"),    # flat volume is within 20% of the average
    ("below", 0.81, None, True, "chop"), ("below", 0.79, None, True, "fade"),
    ("above", 1.1, "up", True, "chop"), ("above", 1.1, "up", False, "breakout"),
    ("at", 3.0, None, False, "chop"), ("at", 0.2, "up", False, "chop"),                  # equal to the line is equilibrium, whatever the volume
])
def test_the_decision_table(here, rel, crossed, tight, want):
    assert avwap.decide(here, rel, crossed, tight) == want


def test_a_series_that_never_moves_sits_exactly_on_its_line():
    b = bars([100.0] * N, [1000] * (N - 1) + [3000])
    out = read(b)
    assert out["latest"]["side"] == "at" and out["conclusion"]["id"] == "chop"


def test_the_summary_states_the_facts_and_flags_a_marginal_volume_call():
    out = read(ramp(True, 2000))
    s = out["conclusion"]["summary"]
    assert f"{DAYS}-day AVWAP" in s and "above" in s and "x its 10-day average" in s and "marginal" not in s
    near = read(ramp(True, 1050))   # 1.05x the average: above it, but only just
    assert near["conclusion"]["id"] == "accumulation" and "marginal" in near["conclusion"]["summary"]


# ---------------------------------------------------------------- when there is nothing to read
def test_too_little_history_gives_a_reason_not_a_guess():
    out = read(bars([100.0] * 8, [1000] * 8))
    assert out["status"] == "insufficient_history" and "10 closed trading days" in out["message"] and "8 are available" in out["message"]
    assert out["conclusion"] is None and out["latest"] is None and out["anchor"] is None
    assert read(bars([100.0] * 10, [1000] * 10))["status"] == "ok"   # exactly enough


def test_a_window_with_no_volume_has_no_volume_weighted_average():
    out = read(bars([100.0] * 30, [0] * 30))
    assert out["status"] == "no_volume" and out["conclusion"] is None and out["latest"] is None


def test_a_bar_that_is_still_forming_is_left_out_of_the_read():
    b = ramp(True, 2000) + bars([300.0], [1e9], start=date(2026, 3, 2))   # an absurd, partial last bar
    forming = avwap.build(b, DAYS, final=False)
    settled = avwap.build(b[:-1], DAYS, final=True)
    assert forming["last_bar_final"] is False and forming["newest_bar"] == b[-1]["t"] and forming["as_of"] == b[-2]["t"]
    assert forming["latest"] == settled["latest"] and forming["conclusion"] == settled["conclusion"]
    assert forming["anchor"] == settled["anchor"]


# ---------------------------------------------------------------- through the API
def demo_bars(sym="AAPL.US"):
    return Data().call("kline", sym, 1500)


def test_chart_api_returns_the_same_read_for_every_range_and_nothing_to_draw():
    c = TestClient(app)
    k = demo_bars()
    w = k[-30:]
    want = sum((x["h"] + x["l"] + x["c"]) / 3 * x["v"] for x in w) / sum(x["v"] for x in w)
    reads = []
    for rng in ("6m", "1y", "2y", "5y"):
        j = c.get(f"/api/chart?symbol=AAPL.US&range={rng}&days=30").json()
        a = j["avwap"]
        assert a["days"] == 30 and a["status"] == "ok" and a["anchor"]["date"] == k[-30]["t"] and a["as_of"] == k[-1]["t"], rng
        assert a["latest"]["avwap"] == pytest.approx(want, abs=1e-3)
        assert a["latest"]["avg_volume"] == pytest.approx(sum(x["v"] for x in w) / 30)
        assert "values" not in a and "avg_volume_values" not in a   # the read is a few numbers: no line is drawn for it
        reads.append(a)
    assert all(r == reads[0] for r in reads)   # the read does not depend on how much of the chart is showing


def test_chart_api_defaults_to_the_configured_days_and_rejects_values_outside_the_limits():
    c = TestClient(app)
    assert c.get("/api/chart?symbol=AAPL.US").json()["avwap"]["days"] == config.AVWAP_DAYS
    lo, hi = config.AVWAP_MIN_DAYS, config.AVWAP_MAX_DAYS
    assert c.get(f"/api/chart?symbol=AAPL.US&days={lo}").status_code == 200 and c.get(f"/api/chart?symbol=AAPL.US&days={hi}").status_code == 200
    for bad in (lo - 1, hi + 1, 0, -5):
        assert c.get(f"/api/chart?symbol=AAPL.US&days={bad}").status_code == 422, bad
    a = c.get("/api/chart?symbol=AAPL.US").json()["avwap"]
    assert (a["min_days"], a["max_days"]) == (lo, hi) and len(a["matrix"]) == 6 and a["rules"]["chop_bars"] == config.AVWAP_CHOP_BARS


def test_a_long_anchor_reaches_back_past_the_visible_chart():
    j = TestClient(app).get("/api/chart?symbol=AAPL.US&range=6m&days=400").json()
    a = j["avwap"]
    assert a["status"] == "ok" and a["anchor"]["date"] == demo_bars()[-400]["t"] and a["anchor"]["date"] not in [x["t"] for x in j["candles"]]


def test_service_clamps_the_days_it_is_given():
    d = Data()
    assert service.get_chart(d, "AAPL.US", "1y", 50, days=1)["avwap"]["days"] == config.AVWAP_MIN_DAYS
    assert service.get_chart(d, "AAPL.US", "1y", 50, days=10 ** 6)["avwap"]["days"] == config.AVWAP_MAX_DAYS
    assert service.get_chart(d, "AAPL.US", "1y", 50)["avwap"]["days"] == config.AVWAP_DAYS


def test_the_newest_bar_is_judged_against_the_session_it_belongs_to(monkeypatch):
    seen = []
    real = service.sessions.is_final

    def spy(sym, bar_date, fetched_at):
        seen.append((sym, bar_date, fetched_at))
        return False
    monkeypatch.setattr(service.sessions, "is_final", spy)
    d = Data()
    a = service.get_chart(d, "AAPL.US", "1y", 50)["avwap"]
    k = demo_bars()
    (sym, bar_date, fetched), = seen
    assert (sym, bar_date) == ("AAPL.US", k[-1]["t"])
    assert fetched.tzinfo is not None and abs((datetime.now(timezone.utc) - fetched).total_seconds()) < 5   # live data: fetched just now
    assert a["last_bar_final"] is False and a["as_of"] == k[-2]["t"]
    monkeypatch.setattr(service.sessions, "is_final", real)
    assert service.get_chart(d, "AAPL.US", "1y", 50)["avwap"]["as_of"] == k[-1]["t"]


class Intraday:
    """A source whose newest bar is the session of 20 October 2026, a Tuesday."""
    name = "cli"

    def kline(self, symbol, count):
        out, d = [], date(2026, 10, 20)
        while len(out) < 300:
            if d.weekday() < 5:
                out.append({"t": d.isoformat(), "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.0, "v": 1000.0})
            d -= timedelta(days=1)
        return out[::-1]


def test_a_cached_page_judges_the_newest_bar_by_when_the_data_was_fetched_not_by_now(tmp_path):
    tz = timezone(timedelta(hours=8))
    clock = type("Clock", (), {"t": datetime(2026, 10, 20, 22, 0, tzinfo=tz), "__call__": lambda self: self.t})()   # 10:00 in New York
    store = SnapshotStore(tmp_path, 24 * 3600, clock)
    first = Data(Intraday(), store).session("AAPL.US")
    service.get_chart(first, "AAPL.US", "1y", 50)
    first.save_snapshot()
    clock.t = datetime(2026, 10, 21, 5, 30, tzinfo=tz)   # 17:30 in New York: the session has ended, but the file holds a mid-session bar
    cached = service.get_chart(Data(Intraday(), store).session("AAPL.US"), "AAPL.US", "1y", 50)["avwap"]
    assert cached["last_bar_final"] is False and cached["newest_bar"] == "2026-10-20" and cached["as_of"] == "2026-10-19"


# ---------------------------------------------------------------- settings and page
@pytest.mark.parametrize("raw,want", [("45", 45), ("30.7", 30), ("3", 5), ("9999", 500), ("abc", 20), ("", 20), (None, 20), ("nan", 20), ("inf", 20)])
def test_the_default_days_setting_is_read_safely(raw, want):
    assert _clamp_int(raw, 20, 5, 500) == want


def test_the_page_has_the_days_box_and_the_panel_but_draws_no_lines_on_the_chart():
    c = TestClient(app)
    html, js = c.get("/").text, c.get("/static/app.js").text
    assert 'id="avwapdays"' in html and 'id="avwapinsight"' in html
    assert "function renderAvwap(" in js and "/static/styles.css?v=" in html
    # the AVWAP and average volume lines were tried and dropped: nothing may draw them again
    assert "avwapTraces" not in js and "Avg volume" not in js
    assert 'name: "AVWAP anchor"' not in js and "name: `AVWAP " not in js   # the trace definitions, not the prose around them
    assert "--avwap" not in c.get("/static/styles.css").text

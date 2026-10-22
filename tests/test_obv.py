import os

os.environ["DATA_MODE"] = "demo"

import itertools
import json
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import config, flows, obv, service
from app.data import Data
from app.main import app

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
DAYS = config.OBV_DAYS


def bars(closes, vols, spread=0.01, start=date(2026, 1, 5)):
    """Weekday bars; the high and low sit `spread` either side of each close."""
    out, d = [], start
    for c, v in zip(closes, vols):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append({"t": d.isoformat(), "o": c, "h": c * (1 + spread), "l": c * (1 - spread), "c": c, "v": float(v)})
        d += timedelta(days=1)
    return out


def scenario(price, volume, obv_dir, lead=DAYS):
    """`lead` + DAYS flat sessions at 100 on 1000 shares, then DAYS sessions that alternate an up day and a down day.
    The two steps of a pair set where price ends up. The two volumes set which way OBV goes: for a rising OBV the up day
    trades 1.5 x the mean and the down day 0.5 x, for a falling one the other way round, for a flat one both the mean.
    The mean is 1500 for increasing volume (against 1000 before), 500 for decreasing and 1000 for flat."""
    a, b = {"rising": (0.03, 0.01), "falling": (0.01, 0.03), "flat": (0.01, 0.01 / 1.01)}[price]
    mean = {"increasing": 1500, "decreasing": 500, "flat": 1000}[volume]
    vu, vd = {"rising": (1.5 * mean, 0.5 * mean), "falling": (0.5 * mean, 1.5 * mean), "flat": (mean, mean)}[obv_dir]
    closes, vols = [100.0] * (lead + DAYS), [1000.0] * (lead + DAYS)
    for i in range(DAYS):
        closes.append(closes[-1] * (1 + a if i % 2 == 0 else 1 - b))
        vols.append(vu if i % 2 == 0 else vd)
    return bars(closes, vols)


SPEC = {   # the framework's rows: id -> (price, volume, OBV, label, dynamic, action)
    "strong_bullish": ("rising", "increasing", "rising", "Strong Bullish Trend", "Institutional accumulation; buyers in full control.", "Buy / Hold longs"),
    "low_volume_float": ("rising", "decreasing", "rising", "Low-Volume Float", "Price drifts up on low liquidity; lack of selling pressure.", "Hold longs / Trail stops"),
    "bearish_divergence": ("rising", "decreasing", "falling", "Bearish Divergence", "Smart money is quietly exiting; retail chasing a hollow rally.", "Exit longs / Look to Short"),
    "exhausted_rally": ("rising", "decreasing", "flat", "Exhausted Rally", "Price ticks higher on alternating low-vol days; no buying backing.", "Take partial profits"),
    "strong_bearish": ("falling", "increasing", "falling", "Strong Bearish Trend", "Institutional distribution; aggressive liquidation.", "Avoid buying / Short"),
    "selling_exhaustion": ("falling", "decreasing", "falling", "Selling Exhaustion", "Price drops but sellers are running out of shares/conviction.", "Watch for baseline support"),
    "bullish_divergence": ("falling", "decreasing", "rising", "Bullish Divergence", "Smart money is absorbing panic selling and accumulating dips.", "Look to Buy reversals"),
    "illiquid_drift": ("falling", "decreasing", "flat", "Illiquid Drift", "Price bleeds down purely due to a lack of buyers, not heavy selling.", "Stay sidelines / Wait for base"),
    "silent_accumulation": ("flat", "increasing", "rising", "Silent Accumulation", "Large blocks are bought inside a range without spiking price.", "Buy the upside breakout"),
    "silent_distribution": ("flat", "increasing", "falling", "Silent Distribution", "Large blocks are sold inside a range without crashing price.", "Short the downside breakdown"),
    "high_volume_churn": ("flat", "increasing", "flat", "High-Volume Churn", "Massive volume trading hands, but net buying/selling cancels out.", "Wait for a breakout candle"),
    "market_apathy": ("flat", "decreasing", "flat", "Market Apathy", "No institutional interest; dead liquidity and zero momentum.", "Stay cash / Avoid"),
    "absolute_stagnation": ("flat", "flat", "flat", "Absolute Stagnation", "Complete market freeze. Asset is heavily balanced or illiquid.", "Ignore ticker / Stay cash"),
}
ADDED = {   # written for the combinations the framework's table does not list: id -> (price, volume, OBV, label, side)
    "distribution_into_strength": ("rising", "increasing", "falling", "Distribution Into Strength", "bear"),
    "churning_rally": ("rising", "increasing", "flat", "Churning Rally", None),
    "steady_advance": ("rising", "flat", "rising", "Steady Advance", "bull"),
    "unconfirmed_rally": ("rising", "flat", "falling", "Unconfirmed Rally", "bear"),
    "idle_advance": ("rising", "flat", "flat", "Idle Advance", None),
    "absorbed_selling": ("falling", "increasing", "rising", "Absorbed Selling", "bull"),
    "churning_decline": ("falling", "increasing", "flat", "Churning Decline", None),
    "dip_buying": ("falling", "flat", "rising", "Dip Buying", "bull"),
    "steady_decline": ("falling", "flat", "falling", "Steady Decline", "bear"),
    "idle_decline": ("falling", "flat", "flat", "Idle Decline", None),
    "thin_accumulation": ("flat", "decreasing", "rising", "Thin-Volume Accumulation", None),
    "thin_distribution": ("flat", "decreasing", "falling", "Thin-Volume Distribution", None),
    "slow_accumulation": ("flat", "flat", "rising", "Slow Accumulation", None),
    "slow_distribution": ("flat", "flat", "falling", "Slow Distribution", None),
}
ALL = {**{k: v[:4] for k, v in SPEC.items()}, **{k: v[:4] for k, v in ADDED.items()}}
COMBOS = list(itertools.product(("rising", "falling", "flat"), ("increasing", "decreasing", "flat"), ("rising", "falling", "flat")))


# ---------------------------------------------------------------- the table
def test_every_combination_of_price_volume_and_obv_has_exactly_one_row():
    assert len(COMBOS) == 27 and len(obv.RULES) == 27
    assert sorted((r.price, r.volume, r.obv) for r in obv.RULES) == sorted(COMBOS)
    assert len({r.id for r in obv.RULES}) == len({r.label for r in obv.RULES}) == 27
    for t in COMBOS:
        assert (obv.decide(*t).price, obv.decide(*t).volume, obv.decide(*t).obv) == t


def test_the_frameworks_rows_are_word_for_word_and_the_rest_are_marked_as_the_apps_own():
    by_id = {r.id: r for r in obv.RULES}
    assert {r.id for r in obv.RULES if r.framework} == set(SPEC) and {r.id for r in obv.RULES if not r.framework} == set(ADDED)
    for rid, (price, volume, obv_dir, label, dynamic, action) in SPEC.items():
        r = by_id[rid]
        assert (r.price, r.volume, r.obv, r.label, r.dynamic, r.action) == (price, volume, obv_dir, label, dynamic, action), rid
        assert r.text == f"{label} ({action})"
    for rid, (price, volume, obv_dir, label, side) in ADDED.items():
        assert (by_id[rid].price, by_id[rid].volume, by_id[rid].obv, by_id[rid].label) == (price, volume, obv_dir, label), rid
    m = {x["id"]: x for x in obv.matrix()}
    assert [x["id"] for x in obv.matrix()] == [r.id for r in obv.RULES] and all(m[i]["framework"] for i in SPEC) and not any(m[i]["framework"] for i in ADDED)


def test_the_bullish_and_bearish_rows_feed_the_thesis_and_the_cautious_ones_do_not():
    sides = {r.id: r.side for r in obv.RULES}
    want = {"strong_bullish": "bull", "bearish_divergence": "bear", "strong_bearish": "bear", "bullish_divergence": "bull",
            "silent_accumulation": "bull", "silent_distribution": "bear", **{k: v[4] for k, v in ADDED.items()}}
    assert sides == {rid: want.get(rid) for rid in ALL}
    assert all(r.point for r in obv.RULES if r.side) and not any(r.point for r in obv.RULES if not r.side)
    tones = {r.id: (r.tone, r.strong) for r in obv.RULES}
    assert tones["strong_bullish"] == ("good", True) and tones["strong_bearish"] == ("bad", True)
    assert tones["bearish_divergence"] == ("bad", True) and tones["bullish_divergence"] == ("good", True)
    assert tones["market_apathy"] == ("warn", False) and tones["absolute_stagnation"] == ("warn", False)
    assert not any(r.strong for r in obv.RULES if not r.framework)   # the app's own rows never claim a strong call
    for r in obv.RULES:   # the colour agrees with the direction of the row's side, where it has one
        assert r.tone == {"bull": "good", "bear": "bad", None: r.tone}[r.side], r.id


@pytest.mark.parametrize("rule_id", list(ALL))
def test_each_row_is_reached_from_its_price_volume_and_obv(rule_id):
    price, volume, obv_dir, label = ALL[rule_id]
    out = obv.build(scenario(price, volume, obv_dir))
    assert out["status"] == "ok", out["message"]
    got = out["latest"]
    assert (got["price_state"], got["volume_state"], got["obv_state"]) == (price, volume, obv_dir)
    c = out["conclusion"]
    assert c["id"] == rule_id and c["label"] == label
    if rule_id in SPEC:
        assert c["text"] == f"{label} ({SPEC[rule_id][5]})" and c["dynamic"] == SPEC[rule_id][4]
        assert "own reading" not in c["summary"]
    else:
        assert "this combination" in c["summary"] and "own reading" in c["summary"]


# ---------------------------------------------------------------- the three readings and their edges
def test_price_is_flat_within_the_atr_band_and_only_then():
    assert obv.price_state(2.0, 2.0) == "flat" and obv.price_state(-2.0, 2.0) == "flat" and obv.price_state(0, 0) == "flat"
    assert obv.price_state(2.01, 2.0) == "rising" and obv.price_state(-2.01, 2.0) == "falling"


def test_obv_is_flat_within_the_share_of_volume():
    assert obv.obv_state(config.OBV_FLAT_SHARE) == "flat" and obv.obv_state(-config.OBV_FLAT_SHARE) == "flat"
    assert obv.obv_state(config.OBV_FLAT_SHARE + 0.001) == "rising" and obv.obv_state(-config.OBV_FLAT_SHARE - 0.001) == "falling"


def test_volume_is_flat_within_its_band_of_the_period_before():
    band = config.OBV_FLAT_VOLUME
    assert obv.volume_state(1.0) == obv.volume_state(1 + band - 0.001) == obv.volume_state(1 - band + 0.001) == "flat"
    assert obv.volume_state(1 + band + 0.001) == "increasing" and obv.volume_state(1 - band - 0.001) == "decreasing"


def test_the_numbers_on_the_card_are_the_ones_measured():
    k = scenario("rising", "increasing", "rising")
    out = obv.build(k)
    ob = flows.obv(k)
    win, before = k[-DAYS:], k[-2 * DAYS:-DAYS]
    l = out["latest"]
    assert l["price_change"] == pytest.approx(k[-1]["c"] / k[-1 - DAYS]["c"] - 1)
    assert l["volume_ratio"] == pytest.approx(sum(b["v"] for b in win) / sum(b["v"] for b in before))
    assert l["obv_share"] == pytest.approx((ob[-1] - ob[-1 - DAYS]) / sum(b["v"] for b in win))
    assert l["avg_volume"] == pytest.approx(1500) and l["prior_avg_volume"] == pytest.approx(1000)
    assert out["as_of"] == k[-1]["t"] and out["days"] == DAYS


def test_a_volume_within_ten_percent_of_before_reads_as_flat():
    k = scenario("rising", "increasing", "rising")
    for b in k[-DAYS:]:
        b["v"] *= 1050 / 1500   # mean 1050 against 1000 before
    out = obv.build(k)
    assert out["latest"]["volume_state"] == "flat" and out["conclusion"]["id"] == "steady_advance"
    for b in k[-DAYS:]:
        b["v"] *= 1.2   # mean 1260
    assert obv.build(k)["latest"]["volume_state"] == "increasing"


def test_obv_against_its_moving_average():
    assert obv.build(scenario("rising", "increasing", "rising"))["latest"]["obv_vs_ma"] == "above"
    assert obv.build(scenario("falling", "increasing", "falling"))["latest"]["obv_vs_ma"] == "below"


def test_a_forming_bar_is_left_out_of_the_read():
    k = scenario("rising", "increasing", "rising")
    forming = obv.build(k + [{**k[-1], "t": "2030-01-01", "v": 1.0}], final=False)
    settled = obv.build(k)
    assert forming["last_bar_final"] is False and forming["as_of"] == k[-1]["t"] and forming["newest_bar"] == "2030-01-01"
    assert forming["conclusion"] == settled["conclusion"] and forming["latest"] == settled["latest"]


def test_too_little_history_or_no_volume_gives_a_message_not_a_read():
    short = obv.build(bars([100.0] * 10, [1000.0] * 10))
    assert short["status"] == "insufficient_history" and short["conclusion"] is None and str(2 * DAYS) in short["message"]
    assert len(short["matrix"]) == 27   # the table is still there
    dead = obv.build(bars([100.0] * 3 * DAYS, [0.0] * 3 * DAYS))
    assert dead["status"] == "no_volume" and dead["conclusion"] is None and dead["divergence"]["kind"] is None
    only_now = obv.build(bars([100.0] * 3 * DAYS, [0.0] * (2 * DAYS) + [10.0] * DAYS))   # nothing traded in the earlier window
    assert only_now["status"] == "no_volume"


def test_a_series_that_never_moves_is_flat_all_round():
    out = obv.build(bars([100.0] * 3 * DAYS, [1000.0] * 3 * DAYS))
    assert out["status"] == "ok"
    assert out["latest"]["price_state"] == "flat" and out["latest"]["obv_state"] == "flat" and out["latest"]["volume_state"] == "flat"
    assert out["conclusion"]["id"] == "absolute_stagnation" and out["divergence"]["kind"] is None


# ---------------------------------------------------------------- divergence
def trend(n, step, vol=1000.0, start=100.0):
    return [start * (1 + step) ** i for i in range(n)], [vol] * n


def then(trend_, moves):
    """The trend followed by sessions given as (move, volume)."""
    closes, vols = list(trend_[0]), list(trend_[1])
    for move, v in moves:
        closes.append(closes[-1] * (1 + move))
        vols.append(v)
    return bars(closes, vols)


def test_a_new_high_that_obv_does_not_confirm_is_a_bearish_divergence():
    # fifty sessions up on steady volume: OBV climbs the whole way. Ten more take price to a higher high, but the
    # down days carry far more volume than the up days
    k = then(trend(50, 0.005), [(-0.003, 6000), (0.012, 100)] * 5)
    d = obv.build(k)["divergence"]
    assert d["status"] == "ok" and d["kind"] == "bearish" and d["side"] == "bear"
    assert d["price"]["recent"] > d["price"]["earlier"] and d["obv"]["recent"] < d["obv"]["earlier"]
    assert d["obv"]["shortfall_share"] > config.OBV_DIVERGENCE_GAP
    assert d["price"]["earlier"] == pytest.approx(max(b["c"] for b in k[-60:-10]))
    assert "new 60-session high" in d["summary"] and "did not follow" in d["summary"]


def test_a_new_high_that_obv_confirms_is_no_divergence():
    d = obv.build(then(trend(50, 0.005), [(0.012, 1500), (-0.003, 100)] * 5))["divergence"]
    assert d["kind"] is None and d["side"] is None and d["summary"].startswith("No divergence")


def test_a_new_low_that_obv_does_not_confirm_is_a_bullish_divergence():
    k = then(trend(50, -0.005), [(0.003, 6000), (-0.012, 100)] * 5)
    d = obv.build(k)["divergence"]
    assert d["kind"] == "bullish" and d["side"] == "bull" and d["price"]["recent"] < d["price"]["earlier"]
    assert d["obv"]["recent"] > d["obv"]["earlier"] and "new 60-session low" in d["summary"]


def test_an_obv_shortfall_smaller_than_the_gap_does_not_count():
    # OBV's recent peak sits 300 shares under its earlier one, on 52,000 traded
    k = then(trend(50, 0.005), [(-0.002, 1200)] + [(0.012, 100)] * 9)
    ob = flows.obv(k)
    short = max(ob[-60:-10]) - max(ob[-10:])
    assert 0 < short < config.OBV_DIVERGENCE_GAP * sum(b["v"] for b in k[-60:])
    assert obv.build(k)["divergence"]["kind"] is None


def test_no_new_extreme_in_price_means_no_divergence_whatever_obv_does():
    k = then(trend(50, 0.005), [(-0.01, 6000), (0.002, 100)] * 5)   # price stays below its high while OBV collapses
    assert max(b["c"] for b in k[-10:]) < max(b["c"] for b in k[-60:-10])
    assert obv.build(k)["divergence"]["kind"] is None


def test_divergence_needs_its_own_history_and_does_not_hide_the_matrix():
    out = obv.build(scenario("rising", "increasing", "rising", lead=0))   # 40 sessions: enough for the read, short of 60
    assert out["status"] == "ok" and out["divergence"]["status"] == "insufficient_history" and out["divergence"]["kind"] is None
    assert str(config.OBV_DIVERGENCE_DAYS) in out["divergence"]["message"]


# ---------------------------------------------------------------- series for the chart
def test_the_chart_series_is_obv_and_its_moving_average_shifted_to_start_at_zero():
    k = scenario("rising", "increasing", "rising", lead=60)
    n = 30
    s = obv.series(k, n)
    ob = np.array(flows.obv(k))
    ma = np.convolve(ob, np.ones(config.OBV_MA_DAYS) / config.OBV_MA_DAYS, mode="valid")   # ma[i] ends at bar i + OBV_MA_DAYS - 1
    assert len(s["obv"]) == len(s["ma"]) == n and s["from"] == k[-n]["t"] and s["ma_days"] == config.OBV_MA_DAYS
    assert s["obv"][0] == 0 and s["obv"][-1] == round(ob[-1] - ob[-n])
    assert s["ma"][-1] == round(ma[-1] - ob[-n]) and s["ma"][0] is not None   # averaged over the history, not just the visible bars
    assert obv.series(k[:5], 252)["ma"] == [None] * 5   # fewer bars than the average needs


def test_moving_average():
    assert obv.moving_average([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]
    assert obv.moving_average([1, 2], 3) == [None, None]


# ---------------------------------------------------------------- the thesis and the critic
def test_points_follow_the_tables_sides():
    for r in obv.RULES:
        got = obv.points(obv.build(scenario(r.price, r.volume, r.obv, lead=40)))
        want = [{"side": r.side, "title": r.point}] if r.side else []
        assert [{"side": p["side"], "title": p["title"]} for p in got] == want, r.id


def test_the_matrix_and_the_divergence_test_agreeing_make_one_point():
    # price edges up to a new high on thin volume while the down days are heavy: the table says Bearish Divergence and so
    # does the divergence test
    read = obv.build(then(trend(40, 0.005, vol=3000), [(-0.002, 3000), (0.012, 100)] * 10))
    assert read["conclusion"]["id"] == "bearish_divergence" and read["divergence"]["kind"] == "bearish"
    pts = obv.points(read)
    assert len(pts) == 1 and pts[0]["side"] == "bear"
    assert pts[0]["title"] == next(r.point for r in obv.RULES if r.id == "bearish_divergence")
    assert read["conclusion"]["evidence"] in pts[0]["evidence"] and read["divergence"]["evidence"] in pts[0]["evidence"]


def test_a_matrix_call_and_a_divergence_that_disagree_are_both_kept():
    read = {"status": "ok", "conclusion": {"side": "bull", "title": "T-bull", "evidence": "e1"},
            "divergence": {"kind": "bearish", "side": "bear", "title": "T-bear", "evidence": "e2"}}
    assert {(p["side"], p["title"]) for p in obv.points(read)} == {("bull", "T-bull"), ("bear", "T-bear")}


def test_points_survive_a_missing_or_failed_read():
    assert obv.points(None) == [] and obv.points({}) == []
    assert obv.points({"status": "insufficient_history", "conclusion": None, "divergence": {"kind": None}}) == []
    only_div = {"status": "no_volume", "conclusion": None, "divergence": {"kind": "bullish", "side": "bull", "title": "t", "evidence": "e"}}
    assert [(p["side"], p["evidence"]) for p in obv.points(only_div)] == [("bull", "e")]


def _analysis(monkeypatch, read):
    monkeypatch.setattr(service.obv, "build", lambda klines, final=True: read)
    return service.analyze(Data(), "AAPL.US")


def test_a_bearish_obv_read_adds_one_cautionary_point_and_one_risk_but_no_supporting_point(monkeypatch):
    a = _analysis(monkeypatch, obv.build(scenario("rising", "decreasing", "falling", lead=40)))
    assert not [x for x in a["bull"] if x["source"] == "OBV"]
    risks = [x for x in a["risks"]["items"] if x["source"] == "OBV"]
    assert len(risks) == 1 and risks[0]["category"] == "Price action and sentiment" and risks[0]["severity"] == "medium"
    assert risks[0]["title"] == next(r.point for r in obv.RULES if r.id == "bearish_divergence")


def test_a_bullish_obv_read_adds_a_supporting_point_and_no_risk(monkeypatch):
    a = _analysis(monkeypatch, obv.build(scenario("rising", "increasing", "rising", lead=40)))
    assert [x["title"] for x in a["bull"] if x["source"] == "OBV"] == [next(r.point for r in obv.RULES if r.id == "strong_bullish")]
    assert not [x for x in a["risks"]["items"] if x["source"] == "OBV"] and not [x for x in a["bear"] if x["source"] == "OBV"]


def test_a_neutral_obv_read_adds_nothing(monkeypatch):
    a = _analysis(monkeypatch, obv.build(scenario("flat", "decreasing", "flat", lead=40)))
    assert not [x for x in a["bull"] + a["bear"] + a["risks"]["items"] if x["source"] == "OBV"]


def test_the_analysis_uses_the_demo_read_without_a_stub():
    a = TestClient(app).get("/api/analyze?symbol=AAPL.US").json()
    assert a["verdict"]["summary"] and "series" not in json.dumps(a["risks"])   # the series is for the chart only


# ---------------------------------------------------------------- the API and the page
def test_the_chart_api_returns_the_read_and_a_series_lined_up_with_the_candles():
    c = TestClient(app)
    reads = []
    for rng, n in (("6m", 126), ("1y", 252), ("2y", 504)):
        j = c.get(f"/api/chart?symbol=AAPL.US&range={rng}").json()
        o = j["obv"]
        assert len(j["candles"]) == len(o["series"]["obv"]) == len(o["series"]["ma"]) == n, rng
        assert o["series"]["from"] == j["candles"][0]["t"] and o["series"]["obv"][0] == 0
        assert o["status"] == "ok" and o["days"] == config.OBV_DAYS and len(o["matrix"]) == 27
        assert o["conclusion"]["id"] in obv.BY_ID and o["rules"]["divergence_gap"] == config.OBV_DIVERGENCE_GAP
        reads.append({k: v for k, v in o.items() if k != "series"})
    assert all(r == reads[0] for r in reads)   # the read does not depend on how much of the chart is showing


def test_the_page_has_the_panel_and_an_obv_pane_on_the_chart():
    c = TestClient(app)
    html, js = c.get("/").text, c.get("/static/app.js").text
    assert 'id="obvinsight"' in html and "function renderObv(" in js
    assert 'yaxis: "y3"' in js and "OBV ${ob.ma_days}-day average" in js


def _render(tmp_path, read):
    f = tmp_path / "chart.json"
    f.write_text(json.dumps({"obv": read}), encoding="utf-8")
    done = subprocess.run([NODE, str(ROOT / "tests" / "obv_card.js"), str(ROOT / "web" / "app.js"), str(f)],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)["html"]


@needs_node
@pytest.mark.parametrize("rule_id", list(ALL))
def test_the_panel_names_the_conclusion_and_marks_its_row_in_the_table(tmp_path, rule_id):
    price, volume, obv_dir, label = ALL[rule_id]
    html = _render(tmp_path, obv.build(scenario(price, volume, obv_dir, lead=40)))
    rule = next(r for r in obv.RULES if r.id == rule_id)
    assert label in html and f"({rule.action})" in html
    assert html.count('<tr class="on">') == 1 and html.count("<tr") == 28   # 27 rows and the header
    row = html.split('<tr class="on">')[1].split("</tr>")[0]
    assert label in row and rule.dynamic in row and rule.action in row
    assert ("&dagger;" in row) == (rule_id in ADDED)


@needs_node
def test_the_panel_explains_the_rows_that_are_the_apps_own_and_only_those(tmp_path):
    own = _render(tmp_path, obv.build(scenario("rising", "increasing", "falling", lead=40)))
    assert own.count("&dagger;") == 14 + 1   # a mark on each of the 14 rows the framework lacks, and the note that explains it
    assert "this app's own reading" in own


@needs_node
def test_the_panel_shows_divergence_short_history_and_a_forming_bar(tmp_path):
    div = _render(tmp_path, obv.build(then(trend(50, 0.005), [(-0.003, 6000), (0.012, 100)] * 5)))
    assert "bearish divergence" in div and "did not follow" in div
    short = _render(tmp_path, obv.build(bars([100.0] * 10, [1000.0] * 10)))
    assert "needs 40 closed trading days" in short and "<table>" not in short
    k = scenario("rising", "increasing", "rising", lead=40)
    assert "was still forming" in _render(tmp_path, obv.build(k + [{**k[-1], "t": "2030-01-01"}], final=False))
    assert "was still forming" not in _render(tmp_path, obv.build(k))

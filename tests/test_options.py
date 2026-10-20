import os

os.environ["DATA_MODE"] = "demo"

import math
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import option_scan, options as O, service, strategies as ST
from app.data import Data
from app.main import app


# ---------------------------------------------------------------- symbols
def test_occ_format_matches_real_cli_behaviour():
    # verified against the CLI: .US suffix, strike x 1000, no zero padding
    assert O.occ_symbol("AAPL.US", "2026-11-20", "C", 327.5) == "AAPL261120C327500.US"
    assert O.occ_symbol("AAPL.US", "2026-11-20", "P", 100) == "AAPL261120P100000.US"
    assert O.parse_occ("AAPL261120C327500.US") == {"symbol": "AAPL.US", "expiry": "2026-11-20", "kind": "C", "strike": 327.5}
    assert O.parse_occ("AAPL261120C00327500") is None and O.parse_occ("../etc") is None


# ---------------------------------------------------------------- Black-Scholes
def test_black_scholes_known_values_and_parity():
    c = O.bs_price(100, 100, 1, 0.05, 0.0, 0.2, "C")
    assert c == pytest.approx(10.4506, abs=1e-3)
    p = O.bs_price(100, 100, 1, 0.05, 0.0, 0.2, "P")
    assert c - p == pytest.approx(100 - 100 * math.exp(-0.05), abs=1e-6)  # put-call parity
    g = O.greeks(100, 100, 1, 0.05, 0.0, 0.2, "C")
    assert g["delta"] == pytest.approx(0.6368, abs=1e-3) and g["vega"] == pytest.approx(0.3752, abs=1e-3)
    assert g["gamma"] > 0 and g["theta"] < 0
    assert O.greeks(100, 100, 1, 0.05, 0.0, 0.2, "P")["delta"] == pytest.approx(0.6368 - 1, abs=1e-3)


# ---------------------------------------------------------------- legs and payoff
def _row(k, last, iv=0.3, vol=50):
    return {"strike": k, "call_last": last, "call_iv": iv, "call_vol": vol, "put_last": last, "put_iv": iv,
            "put_vol": vol, "standard": True}


def _leg(side, kind, k, last, exp="2026-12-18", iv=0.3):
    return {"side": side, "kind": kind, "strike": k, "expiry": exp, "qty": 1, "last": last, "iv": iv, "vol": 50,
            "occ": "X"}


TODAY = date(2026, 10, 20)


def test_bull_call_spread_payoff():
    legs = [_leg("buy", "C", 100, 6.0), _leg("sell", "C", 110, 2.0)]
    a = ST.analyze_payoff(legs, 100, 0.043, 0.0, TODAY)
    assert a["max_profit"] == pytest.approx((10 - 4) * 100) and a["max_loss"] == pytest.approx(4 * 100)
    assert a["breakevens"] == [104.0] and not a["unlimited_profit"] and not a["unlimited_loss"]


def test_long_call_is_unlimited_profit_and_loss_is_the_premium():
    a = ST.analyze_payoff([_leg("buy", "C", 100, 5.0)], 100, 0.043, 0.0, TODAY)
    assert a["unlimited_profit"] and a["max_profit"] is None and a["max_loss"] == pytest.approx(500)
    assert a["breakevens"] == [105.0]


def test_short_naked_call_has_unlimited_loss():
    a = ST.analyze_payoff([_leg("sell", "C", 100, 5.0)], 100, 0.043, 0.0, TODAY)
    assert a["unlimited_loss"] and a["max_loss"] is None and a["max_profit"] == pytest.approx(500)


def test_iron_condor_payoff():
    legs = [_leg("sell", "P", 90, 1.5), _leg("buy", "P", 85, 0.5), _leg("sell", "C", 110, 1.5), _leg("buy", "C", 115, 0.5)]
    a = ST.analyze_payoff(legs, 100, 0.043, 0.0, TODAY)
    assert a["max_profit"] == pytest.approx(200) and a["max_loss"] == pytest.approx((5 - 2) * 100)
    assert a["breakevens"] == [88.0, 112.0]


def test_pop_matches_the_lognormal_formula_for_a_long_call():
    S, K, prem, iv = 100.0, 100.0, 5.0, 0.3
    legs = [_leg("buy", "C", K, prem, iv=iv)]
    T = O.years(O.dte("2026-12-18", TODAY))
    expected = 1 - O.lognormal_cdf(K + prem, S, T, 0.043, 0.0, iv)
    assert ST.model_pop(legs, S, 0.043, 0.0, TODAY) == pytest.approx(expected, abs=1e-3)


def test_net_greeks_of_a_spread_are_the_difference_of_the_legs():
    legs = [_leg("buy", "C", 100, 6.0), _leg("sell", "C", 110, 2.0)]
    g = ST.net_greeks(legs, 100, 0.043, 0.0, TODAY)
    T = O.years(O.dte("2026-12-18", TODAY))
    d1, d2 = O.greeks(100, 100, T, 0.043, 0, 0.3, "C")["delta"], O.greeks(100, 110, T, 0.043, 0, 0.3, "C")["delta"]
    assert g["delta"] == pytest.approx((d1 - d2) * 100, abs=0.1)


# ---------------------------------------------------------------- chain handling
def test_atm_iv_interpolates_between_bracketing_strikes():
    rows = [_row(95, 6, iv=0.30), _row(100, 3, iv=0.28), _row(105, 1, iv=0.26)]
    assert O.atm_iv(rows, 102.5) == pytest.approx(0.27, abs=1e-6)


def test_row_filters_reject_junk_rows():
    assert O.row_problem(_row(100, 0.01), "C") == "price_below_min"
    assert O.row_problem(_row(100, 5, iv=2.0), "C") == "iv_out_of_range"   # deep in-the-money rows carry nonsense IV
    assert O.row_problem(_row(100, 5, vol=0), "C") == "no_volume"
    assert O.row_problem(_row(100, 5), "C") is None


def test_expiries_in_the_past_or_too_close_are_dropped():
    ex = ["2026-10-14", "2026-10-21", "2026-11-20", "2026-12-18", "2027-01-15", "2027-02-19"]
    chosen = O.pick_expiries(ex, 21, 60, TODAY)
    assert "2026-10-14" not in chosen and "2026-10-21" not in chosen
    assert chosen == sorted(chosen) and len(chosen) <= 5


def test_regime_thresholds():
    assert O.regime(1.4) == "rich" and O.regime(0.7) == "cheap" and O.regime(1.0) == "fair" and O.regime(None) == "unknown"


def test_iv_percentile_needs_enough_history():
    assert O.iv_percentile({"2026-01-01": 0.2}, 0.3)["value"] is None
    series = {f"2026-{i // 28 + 1:02d}-{i % 28 + 1:02d}": 0.2 + i / 1000 for i in range(60)}
    assert O.iv_percentile(series, 0.23)["value"] == 50   # 30 of 60 stored values are below 0.23


# ---------------------------------------------------------------- view
def test_automatic_view_tally():
    bull = {"bull": [1] * 6, "bear": [1], "quality": {"passing": 6, "applicable": 7}}
    v = option_scan.derive_view(bull, {"above_ema": {"200": True}, "cross_50_200": "golden"}, "accumulation")
    assert v["value"] == "bullish" and v["score"] == 6                       # 2 + 1 + 1 + 1 + 1
    bear = {"bull": [1], "bear": [1] * 6, "quality": {"passing": 1, "applicable": 7}}
    assert option_scan.derive_view(bear, {"above_ema": {"200": False}}, "distribution")["value"] == "bearish"
    mixed = {"bull": [1] * 3, "bear": [1] * 3, "quality": {"passing": 4, "applicable": 7}}
    assert option_scan.derive_view(mixed, {"above_ema": {"200": True}}, "neutral")["value"] == "neutral"
    # no analysis: fundamentals are not used and the page says so
    v = option_scan.derive_view(None, {"above_ema": {"200": True}}, None)
    assert v["score"] == 1 and "not available" in v["inputs"][0]["detail"]


# ---------------------------------------------------------------- end to end (demo chain)
def _scan(view="bullish", **kw):
    d = Data()
    return service.get_options(d, "AAPL.US", view, **kw)


def test_candidates_are_internally_consistent():
    for view in ("bullish", "bearish", "neutral"):
        o = _scan(view)
        assert o["candidates"], view
        for c in o["candidates"]:
            assert c["dte"] >= 7 and c["legs"] and c["breakevens"] is not None
            assert c["defined_risk"]                         # defined-risk only by default
            if c["strategy"] in ("bull put spread", "bear call spread"):
                width = abs(c["legs"][0]["strike"] - c["legs"][1]["strike"])
                assert c["net"]["type"] == "credit"
                assert c["max_loss"] == pytest.approx((width - c["net"]["per_share"]) * 100, abs=0.5)
                assert c["max_profit"] == pytest.approx(c["net"]["per_share"] * 100, abs=0.5)
                assert c["credit_pct_width"] >= 0.25         # the README filter
            if c["strategy"] == "long call":
                assert c["unlimited_profit"] and c["max_loss"] == pytest.approx(c["net"]["dollars"], abs=0.5)
            assert 0 <= c["pop_model"] <= 1
            assert c["payoff"]["at_expiry"] and len(c["payoff"]["prices"]) == len(c["payoff"]["at_expiry"])
            assert any("last price" in f for f in c["flags"])   # fills are not guaranteed: no bid/ask


def test_strategy_matrix_follows_the_view():
    assert {c["strategy"] for c in _scan("bullish")["candidates"]} <= {"long call", "bull call spread", "bull put spread"}
    assert {c["strategy"] for c in _scan("bearish")["candidates"]} <= {"long put", "bear put spread", "bear call spread"}
    neutral = {c["strategy"] for c in _scan("neutral")["candidates"]}
    assert neutral <= {"iron condor", "long straddle", "long strangle", "calendar spread"}


def test_undefined_risk_only_when_allowed():
    safe = {c["strategy"] for c in _scan("neutral", defined_risk=True)["candidates"]}
    assert "short strangle" not in safe
    for c in _scan("neutral", defined_risk=False)["candidates"]:
        if c["strategy"] == "short strangle":
            assert not c["defined_risk"] and any("UNDEFINED RISK" in f for f in c["flags"])


def test_covered_call_needs_shares_and_includes_the_stock_leg():
    assert not any(c["strategy"] == "covered call" for c in _scan("bullish")["candidates"])
    cc = [c for c in _scan("bullish", shares=200)["candidates"] if c["strategy"] == "covered call"]
    assert cc and cc[0]["legs"][0]["kind"] == "S" and cc[0]["legs"][1]["qty"] == 2 and cc[0]["greeks"]["delta"] > 0


def test_options_endpoint_validation_and_scope():
    c = TestClient(app)
    assert c.get("/api/options?symbol=AAPL.US").status_code == 200
    assert c.get("/api/options?symbol=AAPL.US&view=sideways").status_code == 422
    assert c.get("/api/options?symbol=AAPL.US&dte_min=3").status_code == 422
    assert c.get("/api/options?symbol=AAPL.US&dte_min=50&dte_max=30").status_code == 400
    hk = c.get("/api/options?symbol=700.HK").json()
    assert hk["supported"] is False and "US-only" in hk["message"]


def test_payoff_endpoint_prices_arbitrary_legs():
    c = TestClient(app)
    o = c.get("/api/options?symbol=AAPL.US&view=bullish").json()
    leg = o["candidates"][0]["legs"][0]
    r = c.get(f"/api/options/payoff?symbol=AAPL.US&occ={leg['occ']}&side={leg['side']}")
    assert r.status_code == 200 and len(r.json()["prices"]) == len(r.json()["at_expiry"]) == 61
    assert c.get("/api/options/payoff?symbol=AAPL.US&occ=bogus&side=buy").status_code == 400
    assert c.get(f"/api/options/payoff?symbol=AAPL.US&occ={leg['occ']}&side=hold").status_code == 400


def test_rationale_does_not_claim_an_iv_edge_when_iv_is_fair():
    assert "rich IV" in ST._rationale("iron condor", {})
    r = ST._rationale("iron condor", {"marginal": True})
    assert "rich IV" not in r and "IV is fair" in r


def test_share_class_tickers_drop_the_dot_in_contract_symbols():
    # verified against the CLI: BRKB261120C500000.US returns a quote; BRK.B... and BRK... return nothing
    assert O.occ_symbol("BRK.B.US", "2026-11-20", "C", 500) == "BRKB261120C500000.US"
    assert O.occ_symbol("AAPL.US", "2026-11-20", "C", 500) == "AAPL261120C500000.US"


def test_share_class_symbols_pass_validation_and_injection_is_still_rejected():
    for ok in ("BRK.B.US", "BF.B.US", "AAPL.US", "700.HK"):
        assert service.check_symbol(ok) == ok
    for bad in ("--help.US", "AAPL", "A.B.C.US", "BRK.BB.US", "a b.US", "../x.US"):
        with pytest.raises(Exception):
            service.check_symbol(bad)


def _scan_fair(monkeypatch, view="bullish", **kw):
    monkeypatch.setattr(O, "regime", lambda iv_hv: "fair")        # IV neither rich nor cheap, as for NVDA
    return service.get_options(Data(), "AAPL.US", view, **kw)


def test_credit_spreads_are_included_when_iv_is_fair_but_flagged_as_having_no_iv_edge(monkeypatch):
    on = _scan_fair(monkeypatch, include_credit=True, min_credit=0.0)
    credit = [c for c in on["candidates"] if c["net"]["type"] == "credit"]
    assert credit and all(c["strategy"] == "bull put spread" for c in credit)
    for c in credit:
        assert any("no IV edge" in f for f in c["flags"]) and "rich IV" not in c["rationale"]
    off = _scan_fair(monkeypatch, include_credit=False)
    assert not [c for c in off["candidates"] if c["net"]["type"] == "credit"]       # the matrix alone: debit only
    assert {c["strategy"] for c in off["candidates"]} <= {"long call", "bull call spread"}


def test_credit_spreads_in_rich_iv_carry_no_warning_and_need_no_toggle(monkeypatch):
    monkeypatch.setattr(O, "regime", lambda iv_hv: "rich")
    o = service.get_options(Data(), "AAPL.US", "bullish", include_credit=False, min_credit=0.0)
    credit = [c for c in o["candidates"] if c["net"]["type"] == "credit"]
    assert credit and not any("no IV edge" in f for c in credit for f in c["flags"])


def test_bearish_view_gets_bear_call_spreads_too(monkeypatch):
    o = _scan_fair(monkeypatch, "bearish", include_credit=True, min_credit=0.0)
    assert any(c["strategy"] == "bear call spread" and c["net"]["type"] == "credit" for c in o["candidates"])


def test_min_credit_filters_credit_spreads_and_reports_what_it_hid(monkeypatch):
    loose = _scan_fair(monkeypatch, min_credit=0.0)
    strict = _scan_fair(monkeypatch, min_credit=0.45)
    n_loose = sum(c["net"]["type"] == "credit" for c in loose["candidates"])
    n_strict = sum(c["net"]["type"] == "credit" for c in strict["candidates"])
    assert n_loose >= n_strict and strict["min_credit"] == 0.45
    for c in strict["candidates"]:
        if c["net"]["type"] == "credit":
            assert c["credit_pct_width"] >= 0.45
    if n_loose > n_strict:
        assert strict["dropped"].get("credit_too_small", 0) >= n_loose - n_strict


def test_narrow_and_wide_wings_are_offered_without_duplicates(monkeypatch):
    o = _scan_fair(monkeypatch, min_credit=0.0)
    keys = [(c["strategy"], tuple((l["occ"], l["side"]) for l in c["legs"])) for c in o["candidates"]]
    assert len(keys) == len(set(keys))
    widths = {(c["expiry"], abs(c["legs"][0]["strike"] - c["legs"][1]["strike"])) for c in o["candidates"] if c["strategy"] == "bull put spread"}
    assert len({w for _, w in widths}) >= 2                       # more than one wing width is on offer


def test_min_credit_parameter_is_validated():
    c = TestClient(app)
    assert c.get("/api/options?symbol=AAPL.US&min_credit=0.9").status_code == 422
    assert c.get("/api/options?symbol=AAPL.US&min_credit=-0.1").status_code == 422
    assert c.get("/api/options?symbol=AAPL.US&min_credit=0.1&include_credit=false").status_code == 200

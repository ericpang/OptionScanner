import os

os.environ["DATA_MODE"] = "demo"

import math

import pytest
from fastapi.testclient import TestClient

from app import service, valuation as V
from app.data import Data
from app.main import app


def test_dcf_with_constant_growth_equals_the_gordon_formula():
    # growth the same in every year and at the end: the value must be fcf1 / (ke - g)
    assert V.dcf_equity_value(100, 0.03, 0.09, 0.03) == pytest.approx(100 * 1.03 / (0.09 - 0.03), rel=1e-9)


def test_dcf_rises_with_growth_and_falls_with_the_discount_rate():
    base = V.dcf_equity_value(100, 0.10, 0.09, 0.03)
    assert V.dcf_equity_value(100, 0.15, 0.09, 0.03) > base > V.dcf_equity_value(100, 0.05, 0.09, 0.03)
    assert V.dcf_equity_value(100, 0.10, 0.08, 0.03) > base > V.dcf_equity_value(100, 0.10, 0.10, 0.03)


def test_reverse_dcf_recovers_the_growth_that_produced_the_price():
    mcap = V.dcf_equity_value(100, 0.12, 0.093, 0.03)
    assert V.implied_growth(100, mcap, 0.093, 0.03) == pytest.approx(0.12, abs=1e-4)
    assert V.implied_growth(100, 1e12, 0.093, 0.03) is None      # no growth in range explains an absurd price


def test_rating_bands_follow_price_to_fair_value():
    assert V.band(0.5) == "undervalued" and V.band(0.9) == "modestly undervalued" and V.band(1.0) == "roughly fair"
    assert V.band(1.3) == "modestly overvalued" and V.band(3.0) == "overvalued" and V.band(None) is None


def _build(d, mutate=None):
    stm, quote = d.call("statements", "AAPL.US"), dict(d.call("quote", "AAPL.US"))
    a = service.analyze(d, "AAPL.US")
    if mutate:
        mutate(stm, quote)
    return V.build(stm, quote, a["metrics"], a["quality"]), quote


def test_the_three_methods_and_their_inputs_are_reported():
    v, quote = _build(Data())
    assert v["applicable"] and "not GuruFocus" in v["disclaimer"]
    keys = {m["key"]: m for m in v["methods"]}
    assert keys["dcf"]["value"] and keys["epv"]["value"] and keys["graham"]["value"]
    g = keys["graham"]["inputs"]                                   # Graham number straight from P/E and P/B
    assert keys["graham"]["value"] == pytest.approx(math.sqrt(22.5 * g["eps_ttm"] * g["book_value_per_share"]), rel=1e-3)
    grid = keys["dcf"]["grid"]
    assert len(grid) == 9 and grid[4]["fair_value"] == pytest.approx(keys["dcf"]["value"], rel=1e-3)  # centre = base case
    assert keys["dcf"]["low"] <= keys["dcf"]["value"] <= keys["dcf"]["high"]
    h = v["headline"]
    assert h["price_to_fair"] == pytest.approx(quote["price"] / h["value"], abs=0.01) and h["rating"]
    assert v["reverse_dcf"]["implied_stage1_growth"] is not None


def test_growth_is_capped_and_never_negative():
    v, _ = _build(Data())
    g1 = next(m for m in v["methods"] if m["key"] == "dcf")["inputs"]["stage1_growth"]
    assert 0 <= g1 <= 0.20


def test_negative_free_cash_flow_means_no_dcf_but_the_other_methods_still_run():
    def burn(stm, quote):
        for p in stm["annual"] + stm["quarterly"]:
            p["ocf"] = -abs(p["ocf"])
    v, _ = _build(Data(), burn)
    keys = {m["key"]: m for m in v["methods"]}
    assert keys["dcf"]["value"] is None and "not positive" in keys["dcf"]["note"]
    assert v["headline"] is None and keys["graham"]["value"]


def test_banks_are_not_valued_with_cash_flow_models():
    v, _ = _build(Data(), lambda stm, q: q.update(industry="Diversified Banks"))
    assert v["applicable"] is False and "banks" in v["note"]


def test_nothing_is_invented_when_inputs_are_missing():
    v, _ = _build(Data(), lambda stm, q: q.update(pe=None, pb=None, market_cap=None))
    assert v["applicable"] is False


def test_valuation_is_in_the_api_and_feeds_the_thesis():
    c = TestClient(app)
    a = c.get("/api/analyze?symbol=MSFT.US").json()
    assert a["valuation"]["applicable"] and a["valuation"]["headline"]["value"] > 0
    assert any("Fair value" in s["field"] for s in a["sources"])
    hl = a["valuation"]["headline"]
    titles = [x["title"] for x in a["bull"] + a["bear"] if x["source"] == "Fair value"]
    if hl["price_to_fair"] <= 0.8:
        assert titles == ["Price is well below the DCF fair value (app model)"]
    elif hl["price_to_fair"] >= 1.4:
        assert titles == ["Price is well above the DCF fair value (app model)"]
    else:
        assert titles == []                                            # no point is added in the middle bands

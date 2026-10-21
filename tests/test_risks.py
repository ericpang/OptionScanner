import os

os.environ["DATA_MODE"] = "demo"

import copy

from app import risks, service
from app.data import Data


def _inputs():
    d = Data()
    a = service.analyze(d, "AAPL.US")
    st = d.call("statements", "AAPL.US")
    return d, a, st, dict(d.call("quote", "AAPL.US"))


def test_the_analysis_carries_a_critic_with_facts_for_every_entry():
    _, a, _, _ = _inputs()
    r = a["risks"]
    assert set(r) == {"items", "counts", "not_measured", "filings", "note"}
    assert r["counts"] == {s: sum(1 for x in r["items"] if x["severity"] == s) for s in ("high", "medium", "low")}
    for x in r["items"]:
        assert x["facts"] and x["source"] and x["severity"] in risks.SEVERITY and x["category"] in risks.CATEGORIES
    order = [(risks.SEVERITY[x["severity"]], risks.CATEGORIES.index(x["category"])) for x in r["items"]]
    assert order == sorted(order)                                   # worst first, then by theme
    assert any("customer" in x.lower() for x in r["not_measured"])  # what the data cannot show is said, not guessed


def _build(a, st, quote, **over):
    m, q = copy.deepcopy(a["metrics"]), copy.deepcopy(a["quality"])
    for path, v in over.items():
        node = m if path.startswith("m_") else q
        keys = path[2:].split("__")
        for k in keys[:-1]:
            node = node[k]
        node[keys[-1]] = v
    return risks.build(st, quote, m, q, {"reported": {"insider": {"net_12m": None}}, "net_read": {"label": "mixed", "signals_against": []}},
                       {"signals": {"above_ema": {}, "cross_50_200": None}}, a["valuation"], [])


def test_each_concern_quotes_the_figures_that_triggered_it():
    _, a, st, quote = _inputs()
    r = _build(a, st, quote, m_extra__interest_coverage=2.0, m_extra__current_ratio=0.7, m_extra__cash_conversion=0.4)
    by = {x["title"]: x for x in r["items"]}
    assert by["Thin interest coverage"]["severity"] == "high" and "2.0x" in by["Thin interest coverage"]["facts"][0]
    assert by["Short-term obligations exceed short-term assets"]["facts"][0] == "Current ratio 0.7"
    assert "0.4x" in by["Weak cash conversion"]["facts"][0]


def test_clean_inputs_raise_no_balance_sheet_alarm():
    _, a, st, quote = _inputs()
    r = _build(a, st, quote, m_extra__interest_coverage=30.0, m_extra__current_ratio=2.0)
    titles = [x["title"] for x in r["items"]]
    assert "Thin interest coverage" not in titles and "Heavy debt load" not in titles


def test_filings_offered_for_reading_are_only_annual_and_quarterly_reports():
    _, a, st, quote = _inputs()
    m, q = a["metrics"], a["quality"]
    fl = [{"type": "8-K", "filed_at": "2026-01-01", "url": "u1"}, {"type": "10-K", "filed_at": "2026-02-01", "url": "u2"},
          {"type": "10-Q", "filed_at": "2026-03-01", "url": ""}]
    r = risks.build(st, quote, m, q, {"reported": {"insider": {"net_12m": None}}, "net_read": {"label": "mixed", "signals_against": []}},
                    {"signals": {"above_ema": {}, "cross_50_200": None}}, None, fl)
    assert [f["type"] for f in r["filings"]] == ["10-K"]            # not the 8-K, and not one with no link


def test_bear_case_and_critic_are_one_list_without_repeats():
    _, a, st, quote = _inputs()
    m, q = a["metrics"], a["quality"]
    fl = {"reported": {"insider": {"net_12m": None}}, "net_read": {"label": "mixed", "signals_against": []}}
    sg = {"signals": {"above_ema": {}, "cross_50_200": None}}
    bear = [{"title": "Returns on capital are weak", "evidence": "ROIC 4.0%", "source": "ROIC"},      # the critic has no ROIC entry
            {"title": "High leverage", "evidence": "Debt/EBITDA 5x", "source": "Debt/EBITDA"}]       # the critic covers this one
    m = copy.deepcopy(m)
    m["debt_to_ebitda"].update(gross=5.0, net_cash=False)
    r = risks.build(st, quote, m, q, fl, sg, None, [], bear)
    titles = [x["title"] for x in r["items"]]
    assert "Returns on capital are weak" in titles and "High leverage" not in titles
    assert "Heavy debt load" in titles                                                              # the critic's version stays
    joined = next(x for x in r["items"] if x["title"] == "Returns on capital are weak")
    assert joined["facts"] == ["ROIC 4.0%"] and joined["category"] == "Business quality"

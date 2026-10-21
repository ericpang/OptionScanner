import os

os.environ["DATA_MODE"] = "demo"

import copy
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.data as D
from app import benchmarks, config, service
from app.data import Data, DataError
from app.demo import DemoProvider
from app.main import app

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")


def _dist(value, median, rank, total, low, high, ranking):
    """One multiple as `longbridge industry-valuation dist` returns it: every number is a string."""
    return {"high": high, "low": low, "median": median, "rank_index": rank, "rank_total": total, "ranking": ranking, "value": value}


# What the CLI returned on 2026-10-21. AAPL.US sits inside its industry's range for all three multiples.
AAPL_DIST = {
    "pb": _dist("46.0818834635416664", "2.1444893459616698", "32", "33", "0.0473716533592767", "70.6112198603322564", "0.96875"),
    "pe": _dist("38.4295672845730242", "20.7098608805755397", "15", "20", "0.7786361245479965", "52.4921944116548734", "0.7368421052631579"),
    "ps": _dist("10.6137103570303948", "1.2624280954524989", "35", "39", "0.0186025956810536", "45.9879838424016789", "0.8947368421052632"),
}
# QUBT.US loses money and its P/S is above every peer's: those come with no rank, and the "ranking" is a word.
QUBT_DIST = {
    "pb": _dist("0.9241328388518364", "2.1444893459616698", "6", "33", "0.0473716533592767", "70.6112198603322564", "0.15625"),
    "pe": _dist("-98.2342656406489449", "20.7098608805755397", "", "", "0.7786361245479965", "52.4921944116548734", "LowerLimitExceeded"),
    "ps": _dist("149.7612577870521869", "1.2624280954524989", "", "", "0.0186025956810536", "45.9879838424016789", "UpperLimitExceeded"),
}


def block(median=20.0, value=40.0, rank=15, total=20, low=1.0, high=60.0, outside=None):
    """One multiple as the provider hands it over."""
    return {"median": median, "low": low, "high": high, "value": value, "rank": rank, "total": total, "outside": outside}


def metrics(pe=40.0, pb=4.0, ps=3.0):
    """The figures on the metric cards."""
    return {"pe_ttm": {"value": pe}, "extra": {"pb": pb, "ps": ps}}


def cli(payload):
    """A CliProvider whose `industry-valuation dist` call returns `payload`; the arguments it was called with are kept."""
    p = D.CliProvider.__new__(D.CliProvider)
    p.exe, p.calls = "x", []
    p.run = lambda *args: p.calls.append(args) or payload
    return p


# ---------------------------------------------------------------- the comparison
def test_the_company_is_set_against_the_industry_median_and_ranked():
    out = benchmarks.build({"pe": block(), "pb": block(median=2.0, value=4.0, rank=10, total=33),
                            "ps": block(median=1.0, value=3.0, rank=39, total=39)}, metrics(pe=40.0, pb=4.0, ps=3.0))
    pe = out["pe"]
    assert (pe["median"], pe["company"], pe["times_median"]) == (20.0, 40.0, 2.0)
    assert pe["percentile"] == pytest.approx(14 / 19, abs=0.005) and (pe["rank"], pe["n"]) == (15, 20)
    assert pe["outside"] is None and pe["thin"] is False
    assert out["pb"]["times_median"] == 2.0 and out["ps"]["times_median"] == 3.0


def test_the_percentile_is_the_share_of_the_other_companies_the_company_is_above():
    assert benchmarks.build({"pe": block(rank=1, total=20)}, metrics())["pe"]["percentile"] == 0.0     # the lowest of all
    assert benchmarks.build({"pe": block(rank=20, total=20)}, metrics())["pe"]["percentile"] == 1.0    # above every other
    assert benchmarks.build({"pe": block(rank=1, total=1)}, metrics())["pe"]["percentile"] is None     # nobody to be above


@pytest.mark.parametrize("mine", [None, 0, -98.2])
def test_a_multiple_that_is_not_positive_gets_the_median_but_no_comparison(mine):
    pe = benchmarks.build({"pe": block(value=-98.2, rank=None, total=None, outside="below")}, metrics(pe=mine))["pe"]
    assert pe["median"] == 20.0 and pe["company"] is None and pe["times_median"] is None
    assert pe["percentile"] is None and pe["rank"] is None and pe["outside"] is None


def test_a_multiple_outside_the_industrys_range_keeps_the_ratio_but_has_no_rank():
    ps = benchmarks.build({"ps": block(median=1.26, value=149.76, rank=None, total=None, outside="above")},
                          metrics(ps=149.76))["ps"]
    assert ps["outside"] == "above" and ps["times_median"] == pytest.approx(118.86, abs=0.01)
    assert ps["percentile"] is None and ps["rank"] is None and ps["n"] is None


def test_a_rank_for_a_different_figure_than_the_card_shows_is_left_out():
    # Longbridge ranks its own P/E of 44.2 (last fiscal year's EPS); the card shows 38.4 (trailing twelve months)
    far = benchmarks.build({"pe": block(value=44.2)}, metrics(pe=38.4))["pe"]
    assert far["percentile"] is None and far["rank"] is None
    assert far["times_median"] == pytest.approx(38.4 / 20.0, abs=0.01)    # the ratio is the card's own figure over the median
    flagged = benchmarks.build({"pe": block(value=44.2, rank=None, total=None, outside="above")}, metrics(pe=38.4))["pe"]
    assert flagged["outside"] is None                                      # the range flag belongs to that other figure too
    near = benchmarks.build({"pe": block(value=40.4)}, metrics(pe=38.4))["pe"]   # 5% apart: the same figure, repriced
    assert near["percentile"] is not None and near["rank"] == 15
    assert benchmarks.build({"pe": block(value=None)}, metrics(pe=38.4))["pe"]["percentile"] is None   # nothing to check against


def test_a_median_over_few_companies_is_flagged_thin():
    few = config.BENCH_MIN_COMPANIES - 1
    assert benchmarks.build({"pe": block(rank=2, total=few)}, metrics())["pe"]["thin"] is True
    assert benchmarks.build({"pe": block(rank=2, total=config.BENCH_MIN_COMPANIES)}, metrics())["pe"]["thin"] is False


def test_a_multiple_below_the_median_is_better_and_above_it_worse():
    pe = lambda mine, **kw: benchmarks.build({"pe": block(median=20.0, value=mine, **kw)}, metrics(pe=mine))["pe"]
    assert pe(10.0)["better"] is True and pe(40.0)["better"] is False       # a lower multiple is the cheaper one, so the better
    assert pe(20.0)["better"] is None and pe(20.05)["better"] is None       # equal to the median, as it reads (1.0x): neither
    none = benchmarks.build({"pe": block(value=-98.2, rank=None, total=None, outside="below")}, metrics(pe=None))["pe"]
    assert none["better"] is None                                            # nothing to compare
    ps = benchmarks.build({"ps": block(median=1.26, value=149.76, rank=None, total=None, outside="above")}, metrics(ps=149.76))["ps"]
    assert ps["better"] is False                                             # above the industry's whole range: worse


@pytest.mark.parametrize("industry", [None, {}, {"pe": None, "pb": {}, "ps": {"median": None}}])
def test_an_empty_industry_feed_gives_no_comparison(industry):
    assert benchmarks.build(industry, metrics()) is None


def test_only_the_multiples_the_feed_covers_are_compared():
    assert list(benchmarks.build({"pe": block(), "pb": None}, metrics())) == ["pe"]


# ---------------------------------------------------------------- the provider
def test_the_provider_reads_the_distribution_the_cli_returns():
    p = cli(AAPL_DIST)
    out = p.industry_valuation("AAPL.US")
    assert p.calls == [("industry-valuation", "dist", "AAPL.US")]
    pe = out["pe"]
    assert pe["median"] == pytest.approx(20.70986, abs=1e-4) and pe["value"] == pytest.approx(38.42957, abs=1e-4)
    assert pe["low"] == pytest.approx(0.77864, abs=1e-4) and pe["high"] == pytest.approx(52.49219, abs=1e-4)
    assert (pe["rank"], pe["total"], pe["outside"]) == (15, 20, None)
    assert isinstance(pe["rank"], int) and (out["pb"]["rank"], out["pb"]["total"]) == (32, 33) and out["ps"]["total"] == 39
    assert json.loads(json.dumps(out)) == out            # the cache file has to read it back exactly


def test_a_company_outside_the_industrys_range_comes_back_flagged_without_a_rank():
    out = cli(QUBT_DIST).industry_valuation("QUBT.US")
    assert (out["pe"]["rank"], out["pe"]["total"], out["pe"]["outside"]) == (None, None, "below")
    assert (out["ps"]["rank"], out["ps"]["total"], out["ps"]["outside"]) == (None, None, "above")
    assert (out["pb"]["rank"], out["pb"]["outside"]) == (6, None)         # still inside the range for P/B
    assert out["pe"]["median"] == pytest.approx(20.70986, abs=1e-4)       # the industry's own figures are still there


def test_a_stock_with_no_industry_raises_and_a_partial_answer_keeps_what_it_has():
    with pytest.raises(DataError, match="SPY.US"):
        cli({"pb": None, "pe": None, "ps": None}).industry_valuation("SPY.US")    # what the CLI returns for an ETF
    with pytest.raises(DataError):
        cli([]).industry_valuation("X.US")                                         # not even an object
    assert list(cli({"pe": None, "pb": AAPL_DIST["pb"], "ps": "n/a"}).industry_valuation("AAPL.US")) == ["pb"]
    beyond = {"pe": {**AAPL_DIST["pe"], "rank_index": "21", "rank_total": "20"}}
    assert cli(beyond).industry_valuation("AAPL.US")["pe"]["rank"] is None         # a rank past the total is not trusted


def test_a_rate_limited_call_is_retried_like_every_other(monkeypatch):
    """This endpoint answers a call made too soon after another with code 429002 ("retry in 0.6 s"), which the retry
    in CliProvider.run already recognises by the 429."""
    seen = []

    class R:
        def __init__(self, code, out, err):
            self.returncode, self.stdout, self.stderr = code, out, err

    replies = [R(1, "", "Error: openapi error: code=429002: rate limited, retry in 0.6 s"), R(0, json.dumps(AAPL_DIST), "")]
    monkeypatch.setattr(D.subprocess, "run", lambda *a, **k: replies.pop(0))
    monkeypatch.setattr(D.time, "sleep", seen.append)
    p = D.CliProvider.__new__(D.CliProvider)
    p.exe = "x"
    assert p.industry_valuation("AAPL.US")["pe"]["rank"] == 15 and seen == [1.0]


# ---------------------------------------------------------------- the analysis
def test_the_analysis_carries_the_industry_comparison():
    a = service.analyze(Data(), "AAPL.US")
    m, b = a["metrics"], a["benchmarks"]
    assert set(b) == {"pe", "pb", "ps"}
    for key, mine in (("pe", m["pe_ttm"]["value"]), ("pb", m["extra"]["pb"]), ("ps", m["extra"]["ps"])):
        assert b[key]["company"] == pytest.approx(mine, abs=0.01)
        assert b[key]["times_median"] == pytest.approx(mine / b[key]["median"], abs=0.01)
        assert b[key]["n"] == 25 and 0 <= b[key]["percentile"] <= 1      # the demo industry has 25 companies
    json.dumps(a)                                                        # the response has to serialise
    assert any("Industry median" in s["field"] for s in a["sources"])


def test_a_failed_industry_feed_leaves_the_rest_of_the_analysis_alone(monkeypatch):
    d = Data()

    def down(symbol):
        raise DataError("industry-valuation failed")
    monkeypatch.setattr(d.p, "industry_valuation", down)
    a = service.analyze(d, "AAPL.US")
    assert a["benchmarks"] is None and a["metrics"]["pe_ttm"]["value"] and a["bull"] is not None
    assert "industry_valuation: industry-valuation failed" in a["warnings"]   # the page says why there is no comparison


def test_a_halted_analysis_does_not_ask_for_the_industry(monkeypatch):
    d, asked = Data(), []
    real = d.p.statements

    def broken(sym):
        s = real(sym)
        s["annual"][-1]["current_assets"] *= 3
        return s
    monkeypatch.setattr(d.p, "statements", broken)
    monkeypatch.setattr(d.p, "industry_valuation", lambda sym: asked.append(sym) or {})
    a = service.analyze(d, "AAPL.US")
    assert a["halted"] and "benchmarks" not in a and asked == []


def test_the_demo_industry_is_consistent_with_the_stock():
    p = DemoProvider()
    q, ind = p.quote("MSFT.US"), p.industry_valuation("MSFT.US")
    assert p.industry_valuation("MSFT.US") == ind                        # deterministic, like the rest of the demo
    for key in ("pe", "pb", "ps"):
        b = ind[key]
        assert b["value"] == pytest.approx(q[key]) and 1 <= b["rank"] <= b["total"] == 25
        assert b["low"] <= b["median"] <= b["high"] and b["outside"] is None
        assert type(b["rank"]) is int and type(b["median"]) is float
    assert json.loads(json.dumps(ind)) == ind


# ---------------------------------------------------------------- the page
def test_the_page_hands_the_comparison_to_the_metric_cards():
    js = TestClient(app).get("/static/app.js").text
    assert "renderMetrics(a.metrics, a.snapshot, a.benchmarks, current.peers)" in js and "function industryLine(" in js


def page(tmp_path, response, peers=None):
    """What the page draws for an /api/analyze response (and, when given, an /api/peers one), by running the real
    app.js under Node: the metric cards by label, and the Peer group panel as HTML and as text."""
    args = [NODE, str(ROOT / "tests" / "cards.js"), str(ROOT / "web" / "app.js")]
    for name, doc in (("response.json", response), ("peers.json", peers)):
        if doc is not None:
            (tmp_path / name).write_text(json.dumps(doc), encoding="utf-8")
            args.append(str(tmp_path / name))
    done = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout)
    out["cards"] = {c["label"]: c for c in out["cards"]}
    return out


def cards(tmp_path, response, peers=None):
    return page(tmp_path, response, peers)["cards"]


@pytest.fixture(scope="module")
def analysis():
    return service.analyze(Data(), "AAPL.US")


@needs_node
def test_each_multiple_card_shows_the_industry_median_and_where_the_company_stands(tmp_path, analysis):
    c = cards(tmp_path, analysis)
    assert "P/S, P/B" not in c and {"P/E (TTM)", "P/S", "P/B"} <= set(c)    # P/S and P/B each have a line of their own
    for label in ("P/E (TTM)", "P/S", "P/B"):
        sub = c[label]["sub"]
        assert "Industry median" in sub and "(25 companies)" in sub and "x the median" in sub and "percentile" in sub
        assert ("▲" in sub) != ("▼" in sub)                                  # above or below the median, never both
        assert c[label]["tips"] and "of the other companies" in c[label]["tips"][0]
    assert "Industry median" not in c["EV / EBITDA"]["sub"]                  # only the three multiples are compared


@needs_node
def test_the_cards_still_draw_without_a_comparison(tmp_path, analysis):
    c = cards(tmp_path, {**analysis, "benchmarks": None})
    assert len(c) == len(cards(tmp_path, analysis)) and not any("Industry median" in x["sub"] for x in c.values())


@needs_node
def test_a_card_says_when_there_is_nothing_to_compare_no_rank_or_too_few_companies(tmp_path, analysis):
    b = analysis["benchmarks"]
    odd = {"pe": {**b["pe"], "company": None, "times_median": None, "percentile": None, "rank": None},
           "ps": {**b["ps"], "percentile": None, "rank": None, "n": None, "outside": "above"},
           "pb": {**b["pb"], "times_median": 0.45, "thin": True}}
    c = cards(tmp_path, {**analysis, "benchmarks": odd})
    assert "no comparison without a positive figure" in c["P/E (TTM)"]["sub"] and "x the median" not in c["P/E (TTM)"]["sub"]
    ps = c["P/S"]["sub"]
    assert "above the industry's range" in ps and "percentile" not in ps and "companies)" not in ps
    assert "thin sample" in c["P/B"]["sub"] and "▼ 0.45x the median" in c["P/B"]["sub"]   # below 1: arrow down, two decimals


# ---------------------------------------------------------------- the page: peers
PEER_CARDS = {   # card label -> the API's key for its peer figure
    "Operating margin": "operating_margin", "ROIC": "roic", "ROE": "roe", "Debt / EBITDA": "debt_to_ebitda",
    "FCF growth": "fcf_growth", "Net income growth": "net_income_growth", "Revenue growth": "revenue_growth",
    "Gross margin": "gross_margin", "Net margin": "net_margin", "FCF margin": "fcf_margin",
    "Cash conversion": "cash_conversion", "EV / EBITDA": "ev_ebitda", "FCF yield": "fcf_yield",
    "Interest coverage": "interest_coverage", "Current ratio": "current_ratio", "Quick ratio": "quick_ratio",
    "Net debt / equity": "net_debt_equity",
}


@pytest.fixture(scope="module")
def peer_data():
    return service.get_peers(Data(), "AAPL.US")


@needs_node
def test_every_compared_metric_card_shows_the_peer_median_and_where_the_company_stands(tmp_path, analysis, peer_data):
    c = cards(tmp_path, analysis, peer_data)
    assert not {"Net / FCF margin", "Current / quick ratio"} & set(c)          # one card per figure, each with its own line
    assert {"Net margin", "FCF margin", "Current ratio", "Quick ratio"} <= set(c)
    full = 0
    for label, key in PEER_CARDS.items():
        sub, b = c[label]["sub"], peer_data["metrics"][key]
        assert "Peer median" in sub and f"({b['n']} peers)" in sub, label
        if b["diff"] is not None:
            assert f"above {b['above']} of {b['n']}" in sub and any(a in sub for a in "▲▼="), label
            assert c[label]["tips"] and "Middle half of the peers" in c[label]["tips"][-1]
            full += 1
    assert full >= 15                                                          # the demo company has every figure
    assert c["Operating margin"]["sub"].startswith("5y:")                      # what the card said before is still there
    for label in ("PEG", "Analyst ratings", "Dividend", "P/E (TTM)"):
        assert "Peer median" not in c[label]["sub"]                            # no peer figure for these


@needs_node
def test_the_cards_draw_without_peers_and_while_the_comparison_is_unavailable(tmp_path, analysis):
    plain = cards(tmp_path, analysis)
    for doc in (None, {"status": "unavailable", "message": "no peers", "excluded": []}):
        c = cards(tmp_path, analysis, doc)
        assert set(c) == set(plain) and not any("Peer median" in x["sub"] for x in c.values())


@needs_node
def test_a_peer_line_reads_right_for_each_kind_of_gap_and_for_missing_figures(tmp_path, analysis, peer_data):
    m = copy.deepcopy(peer_data["metrics"])
    m["operating_margin"].update(median=0.0986, diff=0.1234, above=7, n=10)               # a gap in percentage points
    m["net_margin"].update(median=0.0756, diff=-0.0456, above=2, n=10)
    m["debt_to_ebitda"].update(median=1.34, diff=-0.84, above=2, n=10)                    # a gap in the figure's own unit
    m["current_ratio"].update(median=1.415, diff=0.4, above=7, n=10)
    m["ev_ebitda"].update(median=16.5, diff=13.1, ratio=1.79, above=8, n=10)              # a multiple: times the median
    m["roic"].update(median=None, q1=None, q3=None, n=2, diff=None, above=None, ratio=None)   # too few peers have it
    m["roe"].update(company=None, diff=None, above=None, ratio=None)                      # nothing to compare for the company
    m["gross_margin"].update(diff=0.0, above=3)                                           # exactly the median
    m["fcf_yield"].update(median=0.0329, diff=-0.01, ratio=None, above=1, n=10)
    c = cards(tmp_path, analysis, {**peer_data, "metrics": m})
    assert "Peer median 9.9% (10 peers)" in c["Operating margin"]["sub"]
    assert "▲ 12.3 pts above the median, above 7 of 10" in c["Operating margin"]["sub"]
    assert "▼ 4.6 pts below the median, above 2 of 10" in c["Net margin"]["sub"]
    assert "▼ 0.84x below the median" in c["Debt / EBITDA"]["sub"] and "▲ 0.40 above the median" in c["Current ratio"]["sub"]
    assert "▲ 1.8x the median, above 8 of 10" in c["EV / EBITDA"]["sub"]
    assert "Peer median n/a: only 2 peers have this figure" in c["ROIC"]["sub"] and not any(a in c["ROIC"]["sub"] for a in "▲▼")
    assert "no figure for AAPL.US to compare" in c["ROE"]["sub"] and "Peer median" in c["ROE"]["sub"]
    assert "= equal to the median, above 3 of" in c["Gross margin"]["sub"]
    assert "▼ 1.0 pts below the median" in c["FCF yield"]["sub"]


@needs_node
def test_the_peer_group_panel_lists_the_peers_the_figures_and_what_was_left_out(tmp_path, analysis, peer_data):
    left = {"symbol": "DELL.US", "name": "Dell", "reason": "its statements fail this app's checks: BS: current assets sum off by -15.6% (tolerance 2%)"}
    out = page(tmp_path, analysis, {**peer_data, "excluded": [left]})
    html, text = out["peersHtml"], out["peersText"]
    assert f"{len(peer_data['peers'])} peers" in text and "Show every figure for every peer" in text
    assert all(f">{x['symbol']}</th>" in html for x in peer_data["peers"])                # a column for each peer
    assert "Peer median" in text and "Middle half" in text and "Operating margin" in text and "Net debt / equity" in text
    assert "Left out" in text and "DELL.US" in text and "current assets sum off by -15.6%" in text
    assert f"reused for {peer_data['ttl_days']:g} days" in text and f"A median needs {peer_data['min_peers']} peers" in text
    rows = re.findall(r"<tr><td>([^<]+)</td>", html)
    assert rows == [label for label, _ in sorted(PEER_CARDS.items(), key=lambda kv: list(peer_data["metrics"]).index(kv[1]))]


@needs_node
def test_an_unavailable_comparison_says_why_in_the_panel(tmp_path, analysis):
    doc = {"symbol": "AAPL.US", "status": "unavailable", "excluded": [{"symbol": "X.US", "name": "X", "reason": "timed out"}],
           "message": "Only 2 of the 14 closest companies could be used (at least 3 are needed)."}
    out = page(tmp_path, analysis, doc)
    assert "Only 2 of the 14 closest" in out["peersText"] and "Tried and left out" in out["peersText"]
    assert "X.US" in out["peersText"] and "timed out" in out["peersText"]


def test_the_page_has_a_place_for_the_peer_group_and_starts_it_beside_the_other_panels():
    c = TestClient(app)
    html, js = c.get("/").text, c.get("/static/app.js").text
    assert 'id="peers"' in html and "async function loadPeers(" in js and "/api/peers?symbol=" in js
    assert "{ loadPeers(symbol); }" in js     # braces, so the search does not wait a minute for the promise
    assert "forcePeers = true;" in js          # "Refresh data" measures the peers again


def test_the_page_and_the_api_name_the_same_peer_figures_in_the_same_order():
    js = TestClient(app).get("/static/app.js").text
    start = js.index("const PEER_FIG = {")
    block = js[start:js.index("};", start)]
    assert re.findall(r"^\s+(\w+): \[", block, re.M) == list(benchmarks.PEER_METRICS)


@needs_node
def test_the_page_loads_the_peers_without_holding_up_the_search_and_ignores_stale_or_failed_ones(tmp_path, analysis, peer_data):
    files = []
    for name, doc in (("analyze.json", analysis), ("peers.json", peer_data)):
        (tmp_path / name).write_text(json.dumps(doc), encoding="utf-8")
        files.append(str(tmp_path / name))
    done = subprocess.run([NODE, str(ROOT / "tests" / "peers_flow.js"), str(ROOT / "web" / "app.js"), *files],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout)
    n = out["normal"]
    assert n["urls"] == ["/api/peers?symbol=AAPL.US"]
    assert n["loading"].startswith("Peer groupComparing AAPL.US with the companies in its industry")   # said while it is measured
    assert n["metricsHavePeerLines"] and n["peersSet"] and n["panel"].startswith("Peer group 10 peers")  # then the cards and panel
    assert out["refresh"] == {"urls": ["/api/peers?symbol=AAPL.US&refresh=true"], "flagAfter": False}     # "Refresh data", once
    assert out["stale"] == {"metricsHavePeerLines": False, "peersSet": False}   # the user searched for something else meanwhile
    assert out["error"]["panel"] == "Peer groupThe peer comparison failed: boom"
    assert out["halted"]["urls"] == [] and out["noAnalysis"]["urls"] == []     # no analysis, nothing to compare
    assert out["search"] == {"done": "finished", "peersAsked": True, "filingsAsked": True}   # a search does not wait for the peers


@needs_node
def test_a_line_is_blue_when_the_company_is_better_red_when_worse_and_grey_otherwise(tmp_path, analysis, peer_data):
    bm = copy.deepcopy(analysis["benchmarks"])
    bm["pe"]["better"], bm["ps"]["better"], bm["pb"]["better"] = True, False, None
    pm = copy.deepcopy(peer_data["metrics"])
    pm["operating_margin"].update(better=True, higher_is_better=True)
    pm["net_margin"].update(better=False, higher_is_better=True)
    pm["debt_to_ebitda"].update(better=True, higher_is_better=False)       # lower is better: under the median is blue
    pm["roic"].update(better=None)                                         # equal to the median, or nothing to compare
    c = cards(tmp_path, {**analysis, "benchmarks": bm}, {**peer_data, "metrics": pm})
    html = lambda label: c[label]["subHtml"]
    assert '<span class="bench better">Industry median' in html("P/E (TTM)")             # after the card's own "basis" line
    assert '<span class="bench worse">Industry median' in html("P/S")
    assert '<span class="bench">Industry median' in html("P/B")                          # no verdict: no colour class
    assert '<br><span class="bench better">Peer median' in html("Operating margin")      # under the card's own "5y" line
    assert html("Net margin").startswith('<span class="bench worse">Peer median')
    assert '<span class="bench better">Peer median' in html("Debt / EBITDA")
    assert '<span class="bench">Peer median' in html("ROIC")
    for label in ("P/S", "Net margin", "Debt / EBITDA"):                                 # the arrow takes the line's colour
        before, _, inside = html(label).partition('<span class="bench')
        assert any(a in inside for a in "▲▼") and not any(a in before for a in "▲▼"), label
    assert "Better than the peer median (lower is better for this figure)" in c["Debt / EBITDA"]["tips"][-1]
    assert "Worse than the peer median (higher is better for this figure)" in c["Net margin"]["tips"][-1]
    assert "Worse than the industry median (a lower multiple is better)" in c["P/S"]["tips"][0]
    assert not any("than the peer median" in t for t in c["ROIC"]["tips"])              # no verdict, none claimed


@needs_node
def test_every_card_is_coloured_by_the_servers_verdict(tmp_path, analysis, peer_data):
    c = cards(tmp_path, analysis, peer_data)
    colour = lambda better: "bench better" if better is True else "bench worse" if better is False else "bench"
    for label, key in PEER_CARDS.items():
        assert f'<span class="{colour(peer_data["metrics"][key]["better"])}">Peer median' in c[label]["subHtml"], label
    for label, key in (("P/E (TTM)", "pe"), ("P/S", "ps"), ("P/B", "pb")):
        assert f'<span class="{colour(analysis["benchmarks"][key]["better"])}">Industry median' in c[label]["subHtml"], label
    assert {v["better"] for v in peer_data["metrics"].values()} >= {True, False}         # the demo company is better at some, worse at others
    for label in ("PEG", "Analyst ratings", "Dividend", "Insider net (3m / 6m / 12m)"):
        assert 'class="bench' not in c[label]["subHtml"], label                          # cards with no comparison have nothing coloured
    assert not any('class="bench' in x["subHtml"] for x in cards(tmp_path, analysis).values() if x["label"] in PEER_CARDS)   # no peers yet


def test_better_is_blue_and_worse_is_red_in_both_themes_and_no_verdict_keeps_the_cards_grey():
    css = TestClient(app).get("/static/styles.css").text
    assert re.search(r"\.bench\.better\s*\{\s*color:\s*var\(--accent\);\s*\}", css)
    assert re.search(r"\.bench\.worse\s*\{\s*color:\s*var\(--bad\);\s*\}", css)
    assert not re.search(r"\.bench\s*\{", css)                                          # a line with no verdict is not coloured
    channels = lambda hex_: [int(hex_[i:i + 2], 16) for i in (0, 2, 4)]
    for hex_ in re.findall(r"--accent:\s*#([0-9a-fA-F]{6})", css)[:2]:                  # the light theme's, then the dark theme's
        r, g, b = channels(hex_)
        assert b > r and b > g, hex_                                                    # blue is the strongest channel in both
    for hex_ in re.findall(r"--bad:\s*#([0-9a-fA-F]{6})", css)[:2]:
        r, g, b = channels(hex_)
        assert r > g and r > b, hex_                                                    # and red is, for the worse ones

import os

os.environ["DATA_MODE"] = "demo"

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import chart, flows, news, normalize, reconcile, service
from app.data import Data, InputError
from app.main import app
from app.util import cagr, growth


def test_ema_matches_manual():
    v = [float(x) for x in range(1, 31)]
    e = chart.ema(v, 10)
    assert e[8] is None and e[9] == pytest.approx(5.5)
    k = 2 / 11
    assert e[10] == pytest.approx(11 * k + 5.5 * (1 - k))


def test_volume_profile_conserves_volume_and_value_area():
    bars = [{"l": 10 + i % 5, "h": 12 + i % 5, "v": 100.0} for i in range(40)]
    p = chart.volume_profile(bars, bins=20)
    assert sum(b["volume"] for b in p["bins"]) == pytest.approx(4000)
    inside = sum(b["volume"] for b in p["bins"] if b["in_value_area"])
    assert inside >= 0.7 * 4000
    assert p["val"] < p["poc"] < p["vah"]


def test_growth_rules():
    assert growth(-5, 10) is None and growth(0, 1) is None
    assert cagr(100, 200, 1) == pytest.approx(1.0) and cagr(-1, 5, 3) is None


def test_obv_and_cmf_directions():
    up = [{"t": str(i), "o": i, "h": i + 1, "l": i - 1, "c": i + 0.9, "v": 10} for i in range(1, 60)]
    assert flows.obv(up)[-1] > 0 and flows.cmf(up) > 0
    assert flows.updown(up, 20) is None  # no down days: undefined, not infinity


def test_news_dedupe_and_stale():
    items = [{"title": "Apple posts record Q3 revenue", "published_at": "2026-10-18"},
             {"title": "Apple posts record Q3 revenue!", "published_at": "2026-10-18"},
             {"title": "Old story", "published_at": "2020-01-01"}]
    out = news.build(items, [], {}, 10)["news"]
    assert len(out) == 2 and out[-1]["stale"] is True


def test_normalize_both_layouts():
    per_period = [{"date": "2023", "Total Revenue": "1,000", "Net Income": 100}, {"date": "2024", "Total Revenue": 1200, "Net Income": 150}]
    p = normalize.parse_statement({"list": per_period})
    assert [x["revenue"] for x in p] == [1000, 1200]
    rows = {"periods": ["2023", "2024"], "items": [{"name": "Total Assets", "values": [5, 6]}]}
    assert [x["total_assets"] for x in normalize.parse_statement(rows)] == [5, 6]


def test_reconcile_catches_bad_data():
    d = Data()
    stm = d.call("statements", "AAPL.US")
    q = d.call("quote", "AAPL.US")
    assert reconcile.run(stm, q)["status"] == "pass"
    bad = {"annual": [dict(p) for p in stm["annual"]], "quarterly": stm["quarterly"]}
    bad["annual"][-1]["current_assets"] *= 3
    assert reconcile.run(bad, q)["status"] == "fail"


def test_endpoints_and_validation():
    c = TestClient(app)
    a = c.get("/api/analyze?symbol=MSFT.US").json()
    assert a["reconciliation"]["status"] == "pass" and a["quality"]["applicable"] >= 5
    assert a["bull"] and a["bear"] is not None and a["sources"]
    assert 2 <= len(c.get("/api/news?symbol=MSFT.US&limit=25").json()["news"]) <= 25
    assert c.get("/api/analyze?symbol=--help").status_code == 400
    assert c.get("/api/chart?symbol=MSFT.US&range=5y").json()["candles"]


def test_analyze_halts_on_reconciliation_failure(monkeypatch):
    d = Data()
    real = d.p.statements

    def broken(sym):
        s = real(sym)
        s["annual"][-1]["current_assets"] *= 3
        return s
    monkeypatch.setattr(d.p, "statements", broken)
    a = service.analyze(d, "AAPL.US")
    assert a["halted"] and "metrics" not in a and "quality" not in a


def test_demo_is_deterministic():
    d1, d2 = Data(), Data()
    assert d1.call("quote", "X.US")["price"] == d2.call("quote", "X.US")["price"]


def test_retained_earnings_check_is_advisory_only():
    d = Data()
    stm = d.call("statements", "AAPL.US")
    q = d.call("quote", "AAPL.US")
    bad = {"annual": [dict(p) for p in stm["annual"]], "quarterly": stm["quarterly"]}
    bad["annual"][-1]["net_income"] *= 3  # RE roll-forward is now far off, but that check never stops scoring
    out = reconcile.run(bad, q)
    assert out["status"] == "pass"
    assert any(c["status"] == "warn" for c in out["checks"])


def test_field_code_statement_format():
    raw = [{"fp_end": "2025-09-27", "fields": [
        {"field": "total_assets", "value": "100"}, {"field": "st_debt", "value": "5"}, {"field": "current_port", "value": "7"},
        {"field": "capex", "value": "-3"}, {"field": "", "value": ""}]},
        {"fp_end": "2024-09-28", "fields": [{"field": "total_assets", "value": "90"}]}]
    p = normalize.parse_statement(raw)
    assert [x["period"] for x in p] == ["2024-09-28", "2025-09-27"]  # oldest first
    assert p[1]["total_assets"] == 100 and p[1]["st_debt"] == 12  # short-term debt + current portion are summed


def _q(fy, fp, eps_a, eps_e, n_a, n_e):
    f = lambda v: "" if v is None else str(v)
    return {"fiscal_year": fy, "fiscal_period": str(fp), "details": [
        {"key": "eps", "actual": f(eps_a), "estimate": f(eps_e)},
        {"key": "normalized_eps", "actual": f(n_a), "estimate": f(n_e)}]}


def test_consensus_growth_uses_one_eps_basis():
    from app.data import _consensus_growth
    # GAAP eps lags non-GAAP; growth must be computed within one basis, preferring non-GAAP
    per = [_q(2025, 4, 1.0, 1.0, 2.0, 2.0), _q(2026, 1, 1.0, 1.0, 2.0, 2.0), _q(2026, 2, 1.0, 1.0, 2.0, 2.0),
           _q(2026, 3, 1.0, 1.0, 2.0, 2.0), _q(2026, 4, None, 2.0, None, 3.0), _q(2027, 1, None, 2.0, None, 3.0)]
    g = _consensus_growth(per)
    assert g["basis"].startswith("normalized") and g["quarters"] == 2
    assert g["growth"] == pytest.approx(0.5)          # (3+3)/(2+2) - 1, not mixed with GAAP
    assert g["ttm_eps"] == pytest.approx(8.0)


def test_peg_growth_over_100_percent_is_not_misread_as_percent():
    # regression: growth of 116% was treated as "1.16%", giving PEG ~36 instead of ~0.36
    from app import metrics as M
    d = Data()
    stm, quote = d.call("statements", "AAPL.US"), d.call("quote", "AAPL.US")
    analyst = {"eps_growth_forward": 1.16, "eps_growth_basis": "normalized (non-GAAP) EPS", "eps_growth_quarters": 3,
               "eps_ttm_basis": quote["price"] / 40}
    m = M.compute(stm, quote, analyst, None, [], d.call("kline", "AAPL.US", 300))
    assert m["peg"]["growth_pct"] == pytest.approx(116.0) and m["peg"]["value"] == pytest.approx(40 / 116, abs=0.01)


def test_flow_signal_wording_follows_direction_and_holders_sorted_by_size():
    d = Data()
    k = d.call("kline", "AAPL.US", 300)
    own = {"top_holders": [{"name": "A", "pct": 5.0, "change_shares": -10.0, "change_pct": None},
                           {"name": "B", "pct": 3.0, "change_shares": -5.0, "change_pct": None}]}
    out = flows.build(k, [{"date": "2099-01-01", "name": "X", "type": "sell", "shares": 1, "value": 1000.0}], own, [])
    nr = out["net_read"]
    assert "Top holders net trimming (13F)" in nr["signals_against"]
    assert "Insiders net selling (6m)" in nr["signals_against"]
    assert not any("net adding" in s or "net buying" in s for s in nr["signals_against"])  # never bullish wording
    assert out["reported"]["holder_breadth"] == {"listed": 2, "increased": 0, "decreased": 2, "net_shares": -15.0}


def test_insider_selling_is_not_counted_twice_in_the_thesis():
    from app import thesis
    d = Data()
    stm, quote = d.call("statements", "AAPL.US"), d.call("quote", "AAPL.US")
    k = d.call("kline", "AAPL.US", 300)
    sells = [{"date": "2099-01-01", "name": "X", "type": "sell", "shares": 1, "value": 1e6,
              "feed_oldest": "2020-01-01", "feed_truncated": False}]
    fl = flows.build(k, sells, {"top_holders": []}, [{"date": "d", "large": -5.0, "medium": 0, "small": 0}])
    fl["net_read"].update(label="distribution", signals_against=["Insiders net selling (6m)", "OBV falling (20d)"])
    a = service.analyze(d, "AAPL.US")
    th = thesis.build(a["metrics"], a["quality"], a["quality"]["buffett"], a["quality"]["graham"],
                      {"signals": {"above_ema": {}, "cross_50_200": "none", "price_vs_value_area": None},
                       "volume_profile": {}}, fl, quote)
    mine = [b for b in th["bear"] if b["source"] in ("Insider trades", "Institutional flows")]
    assert sum("Insiders net selling" in b["evidence"] for b in mine) == 0 or \
        sum(b["title"] == "Insiders are net sellers" for b in mine) == 0
    flow = next(b for b in mine if b["source"] == "Institutional flows")
    assert "OBV falling" in flow["evidence"]


def test_insider_12m_figure_is_marked_partial_when_the_feed_is_cut_off():
    from app import metrics as M
    from datetime import date, timedelta
    recent = (date.today() - timedelta(days=100)).isoformat()
    cut = [{"date": recent, "type": "sell", "value": 1.0, "feed_oldest": recent, "feed_truncated": True}]
    assert M.insider_partial_from(cut) == recent                      # feed starts 100 days ago: a 12m total is partial
    full = [{**cut[0], "feed_truncated": False}]
    assert M.insider_partial_from(full) is None                       # feed was not cut off: nothing is missing
    old = [{**cut[0], "feed_oldest": (date.today() - timedelta(days=400)).isoformat()}]
    assert M.insider_partial_from(old) is None                        # feed already reaches back past 12 months


def test_concurrent_identical_calls_share_one_fetch_and_failures_are_cached():
    import threading, time as _t
    from app.data import DataError as DE

    class P:
        name = "x"
        calls = {"ok": 0, "bad": 0}

        def ok(self, a):
            self.calls["ok"] += 1
            _t.sleep(0.2)
            return a * 2

        def bad(self, a):
            self.calls["bad"] += 1
            raise DE("Error: SEC EDGAR returned HTTP 429 Too Many Requests")

    d = Data(P())
    out = []
    ts = [threading.Thread(target=lambda: out.append(d.call("ok", 3))) for _ in range(6)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert out == [6] * 6 and P.calls["ok"] == 1                      # six panels, one fetch
    for _ in range(4):
        assert d.call("bad", 1, default=[]) == []
    assert P.calls["bad"] == 1                                         # the failure is remembered, not retried 4 times
    assert d.warnings == ["bad: Error: SEC EDGAR returned HTTP 429 Too Many Requests"]  # and reported once
    try:
        d.call("bad", 1, optional=False)
        assert False
    except DE:
        pass


def test_sec_rate_limit_is_not_retried(monkeypatch):
    import app.data as D

    calls = []

    class R:
        returncode, stdout, stderr = 1, "", "Error: SEC EDGAR returned HTTP 429 Too Many Requests: https://www.sec.gov/x"

    monkeypatch.setattr(D.subprocess, "run", lambda *a, **k: calls.append(1) or R())
    monkeypatch.setattr(D.time, "sleep", lambda s: calls.append("slept"))
    p = D.CliProvider.__new__(D.CliProvider)
    p.exe = "x"
    try:
        p.run("insider-trades", "AVGO.US")
        assert False
    except D.DataError:
        pass
    assert calls == [1]          # one attempt, no sleeping


def test_page_and_assets_are_never_served_from_a_stale_cache():
    c = TestClient(app)
    page = c.get("/")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    assert "/static/app.js?v=" in page.text and "/static/styles.css?v=" in page.text
    assert "__BUILD__" not in page.text and "UI build " in page.text
    assert 'id="options"' in page.text
    for path in ("/static/app.js", "/static/styles.css"):
        assert c.get(path).headers["cache-control"] == "no-cache"      # revalidate every time
    etag = c.get("/static/app.js").headers["etag"]
    assert c.get("/static/app.js", headers={"If-None-Match": etag}).status_code == 304  # revalidation is cheap


def test_news_endpoint_survives_a_failed_quote(monkeypatch):
    import app.demo as demo
    from app.data import DataError as DE
    import app.data as D

    def boom(self, symbol):
        raise DE("quote failed")
    monkeypatch.setattr(demo.DemoProvider, "quote", boom)
    D._instance = None
    r = TestClient(app, raise_server_exceptions=False).get("/api/news?symbol=AVGO.US")
    D._instance = None
    assert r.status_code == 200


def _debt_free_statements(d):
    """What Longbridge returns for a company with no debt: the debt line items are simply absent."""
    import copy
    base = d.p.statements("AAPL.US")
    st = copy.deepcopy(base)
    for p in st["annual"] + st["quarterly"]:
        p["total_liabilities"] -= (p.pop("st_debt", 0) or 0) + (p.pop("lt_debt", 0) or 0)  # keep the balance sheet consistent
    return st


def test_a_debt_free_company_does_not_crash_the_analysis_or_the_option_scan(monkeypatch):
    """Regression: ANET.US returned HTTP 500 because Beneish indexed a missing 'lt_debt' line item."""
    d = Data()
    from app import normalize
    st = _debt_free_statements(d)
    for key in ("annual", "quarterly"):
        st[key] = [normalize.finalize(p) for p in st[key]]   # the CLI path runs every period through finalize()
    d.p.statements = lambda sym: __import__("copy").deepcopy(st)
    a = service.analyze(d, "AAPL.US")
    m = a["metrics"]["debt_to_ebitda"]
    assert m["gross"] == 0 and m["net_cash"] is True      # no debt line on a normal balance sheet means no debt
    o = service.get_options(d, "AAPL.US", "bullish")
    assert o["supported"] and o["candidates"]


def test_any_single_missing_statement_field_never_crashes_the_analysis():
    import copy
    d0 = Data()
    base = d0.p.statements("AAPL.US")
    keys = sorted({k for p in base["annual"] + base["quarterly"] for k in p if k != "period"})
    for k in keys:
        for mode in (("missing", "none") if k in ("lt_debt", "st_debt", "net_income", "revenue", "total_assets", "ocf", "equity") else ("missing",)):
            st = copy.deepcopy(base)
            for p in st["annual"] + st["quarterly"]:
                p.pop(k, None) if mode == "missing" else p.__setitem__(k, None)
            d = Data()
            d.p.statements = lambda sym, st=st: copy.deepcopy(st)
            service.analyze(d, "AAPL.US")                   # must not raise; unavailable figures show as n/a


def test_missing_analyst_counts_do_not_crash_the_thesis():
    d = Data()
    orig = d.p.analyst
    d.p.analyst = lambda sym: {**orig(sym), "hold": None}
    assert service.analyze(d, "AAPL.US")["bull"] is not None


def test_piotroski_counts_zero_debt_in_both_years_as_met():
    from app import quality
    d = Data()
    st = _debt_free_statements(d)
    for p in st["annual"]:
        p["lt_debt"] = 0.0
    assert quality.piotroski(st)["tests"]["leverage_down"] is True


def test_beneish_reports_what_drives_the_score():
    from app import quality
    d = Data()
    st, quote = d.call("statements", "AAPL.US"), d.call("quote", "AAPL.US")
    b = quality.beneish(st, quote)
    assert b["score"] is not None and "drivers" in b
    pts = [x["points"] for x in b["drivers"]]
    assert pts == sorted(pts, reverse=True) and all(p > 0.05 for p in pts) and len(b["drivers"]) <= 2
    # make profit far exceed operating cash flow (non-cash gains): accruals must show up as a driver
    import copy
    st2 = copy.deepcopy(st)
    st2["annual"][-1]["net_income"] = st2["annual"][-1]["ocf"] + 0.3 * st2["annual"][-1]["total_assets"]
    b2 = quality.beneish(st2, quote)
    assert b2["flag"] is True and b2["drivers"][0]["name"] == "TATA"


def test_non_operating_share_of_pretax_income():
    d = Data()
    a = service.analyze(d, "AAPL.US")
    s = a["metrics"]["extra"]["non_operating_share"]
    stm = d.call("statements", "AAPL.US")
    q4 = stm["quarterly"][-4:]
    ebt, oi = sum(q["pretax_income"] for q in q4), sum(q["operating_income"] for q in q4)
    assert s == pytest.approx((ebt - oi) / ebt, abs=1e-3)


def _pq(period, ni, ocf, assets, lt_debt, ca, cl, gp, rev, shares):
    return {"period": period, "net_income": ni, "ocf": ocf, "total_assets": assets, "lt_debt": lt_debt,
            "current_assets": ca, "current_liabilities": cl, "gross_profit": gp, "revenue": rev, "shares": shares}


def _quarters(now, then):
    filler = [_pq(f"2025-{m:02d}-28", 1, 1, 100, 1, 2, 1, 1, 2, 10) for m in (10, 12)]
    return {"annual": [{"period": "2024-12-31", "net_income": 1, "total_assets": 1}] * 2,
            "quarterly": [then] + filler + [_pq("2026-03-28", 1, 1, 100, 1, 2, 1, 1, 2, 10), now]}


def test_quarterly_piotroski_compares_the_latest_quarter_with_the_same_quarter_a_year_earlier():
    from app import quality
    then = _pq("2025-06-28", 10, 12, 100, 20, 50, 25, 40, 100, 11)
    better = _pq("2026-06-27", 14, 16, 100, 10, 60, 25, 50, 110, 10)   # every test should pass
    pz = quality.piotroski(_quarters(better, then))
    assert pz["basis"].startswith("latest quarter") and pz["score"] == 9 and pz["of"] == 9
    assert pz["quarterly"]["latest"] == "2026-06-27" and pz["quarterly"]["year_ago"] == "2025-06-28"
    worse = _pq("2026-06-27", 8, 6, 100, 30, 40, 25, 35, 90, 12)
    w = quality.piotroski(_quarters(worse, then))["tests"]
    assert w["roa_improving"] is False and w["ocf_exceeds_net_income"] is False and w["leverage_down"] is False
    assert w["current_ratio_up"] is False and w["no_new_shares"] is False and w["gross_margin_up"] is False
    assert w["asset_turnover_up"] is False and w["roa_positive"] is True


def test_piotroski_falls_back_to_the_fiscal_year_when_quarters_are_not_a_year_apart():
    from app import quality
    st = _quarters(_pq("2026-06-27", 1, 1, 1, 1, 1, 1, 1, 1, 1), _pq("2025-12-28", 1, 1, 1, 1, 1, 1, 1, 1, 1))
    pz = quality.piotroski(st)
    assert pz["basis"].startswith("latest fiscal year") and pz["quarterly"]["score"] is None


def test_share_counts_are_derived_from_consensus_net_income_over_eps(monkeypatch):
    import app.data as D

    def det(ni, eps):
        return [{"key": "net_income", "actual": str(ni), "estimate": ""}, {"key": "eps", "actual": str(eps), "estimate": ""}]
    cons = {"list": [{"fiscal_year": 2026, "fiscal_period": str(q), "details": det(24.0e9 + q * 1e8, 1.0)} for q in (1, 2, 3, 4)] +
            [{"fiscal_year": 2027, "fiscal_period": "1", "details": det(30e9, 2.0)},
             {"fiscal_year": 2027, "fiscal_period": "2", "details": det(0, 0)}]}   # no EPS: must be ignored
    p = D.CliProvider.__new__(D.CliProvider)
    p.exe = "x"
    monkeypatch.setattr(p, "run", lambda *a, **k: cons)
    stm = {"annual": [{"fy": 2026, "fp": 4}], "quarterly": [{"fy": 2026, "fp": 4}, {"fy": 2027, "fp": 1}, {"fy": 2027, "fp": 2}]}
    p._add_shares("X.US", stm)
    assert stm["quarterly"][0]["shares"] == pytest.approx(24.4e9)       # 24.4B net income / 1.00 EPS
    assert stm["quarterly"][1]["shares"] == pytest.approx(15e9) and stm["quarterly"][2].get("shares") is None
    assert stm["annual"][0]["shares"] == pytest.approx((24.1e9 + 24.2e9 + 24.3e9 + 24.4e9) / 4)   # mean of the 4 quarters


def test_unavailable_insider_data_is_not_treated_as_no_selling():
    """A failed Form 4 lookup (SEC rate limit) returned [] and read as 'no insider selling': Graham lost its
    penalty and the Buffett 'not net sellers' test passed. Unavailable (None) must stay unavailable."""
    from app import metrics as M
    assert M.insider_net(None, 6) is None and M.insider_net([], 6) == 0           # failed feed vs genuinely no trades
    import app.demo as demo
    from app.data import DataError as DE

    def boom(self, symbol):
        raise DE("Error: SEC EDGAR returned HTTP 429 Too Many Requests")
    d = Data()
    d.p.insider = lambda sym: boom(d.p, sym)
    a = service.analyze(d, "AAPL.US")
    ins = a["metrics"]["extra"]["insider_net"]
    assert ins["m3"] is None and ins["m6"] is None and ins["m12"] is None
    assert any("Insider data unavailable" in n for n in a["quality"]["graham"]["dynamic_notes"])
    mg = a["quality"]["buffett"]["evidence"]["management"]
    assert any(x.startswith("n/a") and "Insiders" in x for x in mg)               # not a PASS
    assert service.get_flows(d, "AAPL.US")["reported"]["insider"]["net_6m"] is None
    # and when the feed works but there were no trades, that is a real zero
    d2 = Data()
    d2.p.insider = lambda sym: []
    assert service.analyze(d2, "AAPL.US")["metrics"]["extra"]["insider_net"]["m6"] == 0


def test_buffett_star_average_rounds_half_up():
    from app.buffett import _avg_stars
    assert _avg_stars(4, 5) == 5 and _avg_stars(2, 3) == 3 and _avg_stars(3, 4) == 4 and _avg_stars(4, 4) == 4
    assert _avg_stars(None, None) is None


def test_scorecards_use_the_latest_balance_sheet_not_a_stale_fiscal_year():
    import copy
    from app import quality
    d = Data()
    st, quote = d.call("statements", "AAPL.US"), dict(d.call("quote", "AAPL.US"))
    a = copy.deepcopy(st)
    a["quarterly"][-1]["total_liabilities"] *= 5          # only the latest quarter's balance sheet changes
    assert quality.altman(a, quote)["score"] < quality.altman(st, quote)["score"]
    assert quality.magic_formula(a, quote, [])["earnings_yield"] is not None
    b = copy.deepcopy(st)
    b["quarterly"][-1]["cash"] = 0.0
    assert quality.magic_formula(b, quote, [])["earnings_yield"] < quality.magic_formula(st, quote, [])["earnings_yield"]

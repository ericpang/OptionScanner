import os

os.environ["DATA_MODE"] = "demo"

import json
import math
import re
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import data as D, normalize, pricehist, service, twostage as T
from app.data import Data, DataError
from app.main import app

END = "2026-10-23"
ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")


# ---------------------------------------------------------------- fixtures: a healthy company, one figure at a time changed
def weekdays(end, n):
    out, d = [], date.fromisoformat(end)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return out[::-1]


def series(n=1500, end=END, fn=lambda i, n: 100.0):
    bars = []
    for i, t in enumerate(weekdays(end, n)):
        c = fn(i, n)
        bars.append({"t": t, "o": c, "h": c * 1.01, "l": c * 0.99, "c": c, "v": 1e6})
    return bars


def wave(i, n):
    """A rise to 100 over the first years, then a gentle wave around 100 for the last 700 sessions (so there are swings)."""
    return 40 + 60 * i / (n - 700) if i < n - 700 else 100 + 10 * math.sin(i / 9)


BS = dict(cash=200.0, equity=500.0, borrow_st=0.0, borrow_cp=0.0, borrow_lt=100.0, lease_cp=0.0, lease_lt=0.0, cp_total=0.0,
          st_debt=0.0, lt_debt=100.0, total_assets=900.0, current_assets=300.0)
FLOW = dict(revenue=100.0, gross_profit=60.0, operating_income=30.0, pretax_income=30.0, tax=6.0, net_income=24.0, da=5.0,
            ocf=35.0, capex=-5.0, sbc=3.0)
LAST = 2026 * 4 + 3   # fiscal Q3 2026


def fyfp(q):
    return (q - 1) // 4, (q - 1) % 4 + 1


def qlist(n=5, **over):
    """n consecutive fiscal quarters ending Q3 2026, each with the same figures; `over` replaces any of them."""
    out = []
    for q in range(LAST - n + 1, LAST + 1):
        fy, fp = fyfp(q)
        out.append({**FLOW, **BS, "period": f"{fy}-{fp * 3:02d}-28", "fy": fy, "fp": fp, **over})
    return out


def stm(quarterly=None, **over):
    annual = [{**{k: v * 4 for k, v in FLOW.items()}, **BS, "period": f"{y}-12-28", "fy": y, "fp": 4} for y in (2024, 2025)]
    return {"annual": annual, "quarterly": qlist(5, **over) if quarterly is None else quarterly}


def report(n=26, rev=lambda k: 100.0, shares=lambda k: 100.0):
    """The quarterly summary: n quarters ending Q3 2026, oldest first, k=0 the oldest. Net profit 240 a quarter."""
    rows = []
    for k, q in enumerate(range(LAST - n + 1, LAST + 1)):
        fy, fp = fyfp(q)
        rows.append({"period": f"Q{fp} {fy}", "fy": fy, "fp": fp, "end": f"{fy}-{fp * 3:02d}-28", "revenue": rev(k), "net_income": 240.0,
                     "eps": 240.0 / shares(k), "operating_income": 30.0, "gross_margin": 0.6})
    return {"quarterly": rows, "annual": []}


def facts(**over):
    bars = series(fn=wave)
    f = {"symbol": "TEST.US", "mode": "demo", "today": "2026-10-24", "fetched_at": "2026-10-24T01:00:00+00:00",
         "quote": {"name": "Test Co", "industry": "Software", "currency": "USD", "price": 100.0},
         "stm": stm(), "klines": bars, "end_date": END, "price": 100.0, "raw": bars[-700:], "splits": [],
         "analyst": {"eps_forward": 4.0, "eps_forward_date": "2026-10-23"},
         "report": report(rev=lambda k: 100 * 1.03 ** k, shares=lambda k: 120 - k * 0.5),
         "short": {"rate": 0.02, "shares_short": 2e6, "date": "2026-10-09", "float_shares": 1e8},
         "spy": {"start": {"date": "2021-10-22", "close": 100.0}, "end": {"date": END, "close": 150.0}}, "earnings": "2026-11-05"}
    f.update(over)
    return f


def inputs(**kw):
    return T.clean_inputs(**kw)


def stm_without(key):
    s = stm()
    for p in s["quarterly"] + s["annual"]:
        p.pop(key, None)
    return s


def by_id(check, rid):
    return next(x for x in check["rows"] if x["id"] == rid)


GOOD = dict(avg_fwd_pe=30, eps_cagr=20)   # forward P/E is 25 (100 / 4): under its average, and a PEG of 1.25


# ---------------------------------------------------------------- reading the statements
def lb(fp_end, **fields):
    return {"fp_end": fp_end, "report_txt": fp_end, "fields": [{"field": k, "value": None if v is None else str(v)} for k, v in fields.items()]}


def test_the_debt_lines_are_kept_apart_and_the_combined_ones_are_unchanged():
    """AMZN's balance sheet: 18.26B current portion is 5.29B of debt and 12.97B of leases. The filter counts them apart; the
    existing debt figure (Debt / EBITDA, the scorecards) still adds them as before."""
    raw = [lb("2026-06-30", total_assets=682, total_ca=191, st_debt=0.2, current_port=18.3, current_port_debt=5.3, current_port_leases=13.0,
              lt_debt=57.9, capital_leases=83.2, stock_based_cf=4.0, cash_oper=30)]
    p = normalize.finalize(normalize.parse_statement(raw)[0])
    assert (p["borrow_st"], p["borrow_cp"], p["borrow_lt"], p["lease_cp"], p["lease_lt"], p["cp_total"]) == (0.2, 5.3, 57.9, 13.0, 83.2, 18.3)
    assert p["st_debt"] == pytest.approx(18.5) and p["lt_debt"] == pytest.approx(141.1)
    assert p["sbc"] == 4.0


def test_a_missing_debt_line_on_a_normal_balance_sheet_is_none_but_a_missing_sbc_line_is_not_zero():
    p = normalize.finalize(normalize.parse_statement([lb("2026-06-30", total_assets=100, total_ca=30, lt_debt=5, cash_oper=9)])[0])
    assert (p["borrow_st"], p["lease_lt"], p["cp_total"]) == (0.0, 0.0, 0.0)      # Longbridge leaves out a line that is empty
    assert "sbc" not in p                                                       # but "no line" is not "no stock compensation": unknown
    bs_less = normalize.finalize(normalize.parse_statement([lb("2026-06-30", cash_oper=9)])[0])
    assert "borrow_lt" not in bs_less                                           # no balance sheet, so no debt lines


def test_debt_is_split_into_borrowings_and_lease_lines_and_an_unsplit_current_portion_counts_as_leases():
    d = T.debt_split({"borrow_st": 1.0, "borrow_cp": 2.0, "borrow_lt": 3.0, "lease_cp": 4.0, "lease_lt": 5.0, "cp_total": 6.0})
    assert (d["borrowings"], d["leases"]) == (6.0, 9.0)
    parent_only = T.debt_split({"borrow_st": 1.0, "borrow_cp": 0.0, "borrow_lt": 3.0, "lease_cp": 0.0, "lease_lt": 0.0, "cp_total": 7.0})
    assert (parent_only["borrowings"], parent_only["leases"]) == (4.0, 7.0)      # the feed gave the parent only: its parts are unknown
    old = T.debt_split({"st_debt": 5.0, "lt_debt": 20.0})
    assert (old["borrowings"], old["leases"]) == (25.0, 0.0)                    # statements from another source: the combined lines
    assert T.debt_split({"cash": 1.0}) is None and T.debt_split(None) is None


def test_the_trailing_window_needs_four_quarters_that_run_on_and_never_mixes_it_with_a_year():
    q = qlist(5)
    w = T.window({"annual": [], "quarterly": q}, ("revenue", "ocf"))
    assert w["basis"] == "ttm" and w["values"] == {"revenue": 400.0, "ocf": 140.0} and "Q3 2026" in w["label"]
    gap = q[:2] + q[3:]                                                          # a quarter missing from the middle
    assert T.window({"annual": stm()["annual"], "quarterly": gap}, ("revenue",))["basis"] == "fy"
    one_short = [{**x, "ocf": None} if i == 4 else x for i, x in enumerate(q)]
    w = T.window({"annual": stm()["annual"], "quarterly": one_short}, ("revenue", "ocf"))
    assert w["basis"] == "fy" and w["values"]["revenue"] == 400.0                # both figures from the year: never revenue from four quarters and cash flow from a year
    assert T.window({"annual": [], "quarterly": []}, ("revenue",)) is None


def test_the_year_ago_balance_sheet_is_the_quarter_four_back():
    now, prev = T.balances(stm())
    assert now["period"] == "2026-09-28" and prev["period"] == "2025-09-28"          # Q3 2026 and Q3 2025
    assert T.balances({"annual": stm()["annual"], "quarterly": qlist(3)})[1] is None      # fewer than five quarters: no balance sheet a year earlier
    only_years = {"annual": stm()["annual"], "quarterly": []}
    now, prev = T.balances(only_years)
    assert (now["period"], prev["period"]) == ("2025-12-28", "2024-12-28")


# ---------------------------------------------------------------- reading what Longbridge returns
def test_the_quarterly_summary_is_read_into_rows_oldest_first_with_gross_margin_as_a_fraction():
    def acc(field, rows):
        return {"field": field, "values": [{"period": p, "value": v, "fp_end": "1782000000"} for p, v in rows]}
    raw = {"list": {"IS": {"indicators": [
        {"accounts": [acc("EPS", [("Q3 2026", "2.0200"), ("Q2 2026", "2.0100"), ("garbage", "9")])]},
        {"accounts": [acc("NetProfit", [("Q3 2026", "29789000000.0000"), ("Q2 2026", "")]), acc("GrossMgn", [("Q3 2026", "50.0562")])]},
        {"accounts": [acc("ROEDf", [("Q3 2026", "111.0")])]}]}}}
    rows = D._report_rows(raw)
    assert [x["period"] for x in rows] == ["Q2 2026", "Q3 2026"]                 # oldest first; the unreadable label and the empty value are skipped
    last = rows[-1]
    assert (last["fy"], last["fp"], last["eps"], last["net_income"]) == (2026, 3, 2.02, 29789000000.0)
    assert last["gross_margin"] == pytest.approx(0.500562) and "roe" not in last
    assert json.loads(json.dumps(rows)) == rows                                 # it must survive the per-ticker cache file
    assert D._report_rows({}) == [] and D._report_rows({"list": {"BS": {}}}) == []


def cli(payload_for):
    """A CliProvider whose commands answer from `payload_for(args)`; the commands it ran are kept."""
    p = D.CliProvider.__new__(D.CliProvider)
    p.exe, p.calls = "x", []
    p.run = lambda *args: p.calls.append(args) or payload_for(args)
    return p


def test_splits_and_reverse_splits_are_read_from_the_corporate_actions():
    items = [{"action": "ReportDate", "act_desc": "2027 财年第 3 季度业绩披露", "date": "20261125"},
             {"action": "StockSplit", "act_desc": "1 股拆分为 10 股", "date": "20240610"},
             {"action": "ReverseStockSplit", "act_desc": "10 股合并为 1 股", "date": "20230824"},
             {"action": "DividendExDate", "act_desc": "每股派 0.25 美元", "date": "20260101"}]
    p = cli(lambda a: {"items": items})
    assert p.splits("X.US") == [{"date": "2023-08-24", "ratio": 0.1}, {"date": "2024-06-10", "ratio": 10.0}]
    assert p.calls[0][:2] == ("corp-action", "X.US") and "--all" in p.calls[0]       # the default 30 records would not reach back far enough
    assert "zh-CN" in p.calls[0]                                                    # one wording whatever the system language
    assert cli(lambda a: {"items": []}).splits("X.US") == []                         # no splits is an answer, not a failure


def test_a_split_that_cannot_be_read_is_an_error_not_a_guess():
    p = cli(lambda a: {"items": [{"action": "StockSplit", "act_desc": "stock split", "date": "20240610"}]})
    with pytest.raises(DataError, match="could not be read"):
        p.splits("X.US")


def test_short_interest_comes_with_its_settlement_date_and_the_float_it_is_measured_against():
    us = {"data": [{"current_shares_short": "116327753", "rate": "0.0080", "timestamp": "1786680000", "days_to_cover": "2.53"},
                   {"current_shares_short": "120763539", "rate": "0.0083", "timestamp": "1790740800", "days_to_cover": "3.03"}]}
    p = cli(lambda a: us if a[0] == "short-positions" else [{"circ._shares": "14569071452", "total_shares": "14594180000"}])
    out = p.short_interest_detail("AAPL.US")
    assert out["rate"] == 0.0083 and out["shares_short"] == 120763539 and out["float_shares"] == 14569071452
    assert out["date"] == "2026-09-30"                                              # the newer of the two reports
    hk = {"data": [{"amount": "81361623", "balance": "34562417450", "rate": "0.0089", "timestamp": "1791475200"}]}
    out = cli(lambda a: hk if a[0] == "short-positions" else [{"circ._shares": "9092644829"}]).short_interest_detail("700.HK")
    assert out["rate"] == 0.0089 and out["shares_short"] == 81361623 and out["date"] == "2026-10-09"   # Hong Kong's local midnight, stamped in UTC the day before
    assert cli(lambda a: us).short_interest_detail("600519.SH") is None             # Longbridge publishes it for US and Hong Kong only
    assert json.loads(json.dumps(out)) == out


def test_the_float_is_optional_and_the_adjusted_close_is_the_last_session_on_or_before_the_date():
    def answer(a):
        if a[0] == "static":
            raise DataError("no static data")
        if a[0] == "short-positions":
            return {"data": [{"current_shares_short": "5", "rate": "0.01", "timestamp": "1790740800"}]}
        return [{"time": "2021-10-21T04:00:00Z", "open": "1", "high": "1", "low": "1", "close": "418.1", "volume": "5"},
                {"time": "2021-10-22T04:00:00Z", "open": "1", "high": "1", "low": "1", "close": "419.7", "volume": "5"}]
    p = cli(answer)
    assert p.short_interest_detail("X.US")["float_shares"] is None
    assert p.adj_close("SPY.US", "2021-10-24") == {"date": "2021-10-22", "close": 419.7}     # a Sunday: the Friday before
    assert p.calls[-1][:3] == ("kline", "history", "SPY.US") and "forward" in p.calls[-1]
    with pytest.raises(DataError):
        cli(lambda a: []).adj_close("SPY.US", "2021-10-24")


def test_the_summary_history_queues_for_the_same_slots_as_the_statements(monkeypatch):
    """The peer comparison fills the statements endpoint's allowance while the filter loads beside it, so the filter's summary calls
    take their turn in that line instead of colliding with it (code 429002)."""
    now, slept = [100.0], []
    monkeypatch.setattr(D.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(D.time, "sleep", lambda s: slept.append(round(s, 3)))
    monkeypatch.setattr(D.CliProvider, "_statement_next", 0.0)

    class Reply:
        returncode, stdout, stderr = 0, "{}", ""
    monkeypatch.setattr(D.subprocess, "run", lambda *a, **k: Reply())
    p = D.CliProvider.__new__(D.CliProvider)
    p.exe = "x"
    p.run("financial-statement", "AAPL.US", "--kind", "BS", "--report", "af")
    p.run("financial-report", "AAPL.US", "--report", "qf")                       # right behind it: it waits for the next slot
    p.run("quote", "AAPL.US")                                                    # anything else does not wait
    assert slept == [pytest.approx(D.CliProvider.STATEMENT_GAP)]


def test_the_summary_history_needs_at_least_one_period():
    with pytest.raises(DataError):
        cli(lambda a: {"list": {"IS": {"indicators": []}}}).report_history("X.US")


# ---------------------------------------------------------------- check 1: can it survive?
def test_cash_must_strictly_exceed_debt_and_leverage_must_be_strictly_under_the_limit():
    assert T.route_cash(100, 99.99) == T.PASS and T.route_cash(100, 100) == T.FAIL        # equal is not "exceeds"
    assert T.route_leverage(0, 149.99, 100) == T.PASS and T.route_leverage(0, 150, 100) == T.FAIL   # 1.5x exactly is not "strictly below"
    assert T.route_leverage(0, 10, 0) == T.FAIL and T.route_leverage(0, 10, -5) == T.FAIL            # no positive EBITDA, not net cash: fails
    assert T.route_leverage(0, 10, None) == T.UNKNOWN and T.route_cash(None, 10) == T.UNKNOWN


def test_either_route_passes_the_check_and_it_fails_only_when_both_fail():
    assert T.survive_status(100, 100, 100)[0] == T.PASS      # not net cash, but net debt of zero over positive EBITDA is under 1.5x
    assert T.survive_status(10, 500, 100)[0] == T.FAIL        # both fail
    assert T.survive_status(10, 500, None)[0] == T.UNKNOWN    # leverage cannot be judged: not a fail
    assert T.survive_status(300, 100, -50)[0] == T.PASS       # net cash passes whatever the EBITDA


def test_a_healthy_balance_sheet_passes_check_one_with_its_workings_shown():
    f = facts()
    c = T.check_survive(f, T.framework_fit(f, inputs()))
    assert c["status"] == T.PASS and [x["status"] for x in c["rows"]] == [T.PASS, T.PASS]
    assert "cash pool 200 vs debt 100" in by_id(c, "cash_over_debt")["figure"]
    assert any("EBITDA = operating income 120 + D&A 20 = 140" in w for w in c["workings"])


def test_lease_lines_are_counted_both_ways_and_the_check_is_unknown_when_the_answer_depends_on_it():
    # borrowings 100 against cash 200 (net cash); with 300 of lease lines the debt is 400
    leased = dict(lease_lt=300.0, operating_income=10.0, da=1.0)     # EBITDA of 44: 400 of debt would be 4.5x
    f = facts(stm=stm(**leased))
    c = T.check_survive(f, T.framework_fit(f, inputs()))
    assert c["status"] == T.UNKNOWN
    row = by_id(c, "leases")
    assert row["status"] == T.NOTE and "depends on it" in row["note"]
    assert "100 (borrowings) to 400 (with all lease lines)" in by_id(c, "cash_over_debt")["figure"]
    # with EBITDA large enough, both ways pass and the lease lines are only disclosed
    f = facts(stm=stm(lease_lt=300.0))
    c = T.check_survive(f, T.framework_fit(f, inputs()))
    assert c["status"] == T.PASS and "same either way" in by_id(c, "leases")["note"]


def test_a_route_that_fails_while_the_other_passes_costs_nothing():
    f = facts(stm=stm(cash=50.0))                       # cash 50 against borrowings 100, but net debt of 50 over EBITDA of 140 is 0.36x
    c = T.check_survive(f, T.framework_fit(f, inputs()))
    assert c["status"] == T.PASS and by_id(c, "cash_over_debt")["status"] == T.FAIL and by_id(c, "net_debt_ebitda")["status"] == T.PASS
    assert "Not needed" in by_id(c, "cash_over_debt")["note"]
    lost = run(**GOOD, stm=stm(cash=50.0))["points_lost"]["failed"]
    assert not any(x["label"] == "Cash pool exceeds debt" for x in lost)             # a failed route is not a lost point when the check passed


def test_check_one_is_unknown_without_debt_lines_and_not_applicable_to_a_bank():
    bare = {"annual": [{"period": "2025", "cash": 5.0, "total_assets": 9.0}], "quarterly": []}
    f = facts(stm=bare)
    assert T.check_survive(f, T.framework_fit(f, inputs()))["status"] == T.UNKNOWN
    bank = facts(quote={"name": "Big Bank", "industry": "多元化银行", "currency": "USD", "price": 100.0})
    fit = T.framework_fit(bank, inputs())
    assert fit["financial"] and T.check_survive(bank, fit)["status"] == T.NA and T.check_cash(bank, fit)["status"] == T.NA
    assert "Banks, insurers and REITs" in fit["notes"][0]


# ---------------------------------------------------------------- check 2: does it make cash?
def cash_check(**over):
    f = facts(stm=stm(**over))
    return T.check_cash(f, T.framework_fit(f, inputs()))


def test_the_free_cash_flow_margin_is_inclusive_at_fifteen_percent():
    c = cash_check(ocf=20.0, capex=-5.0)                  # 15 of 100, to the unit
    assert by_id(c, "fcf_margin")["status"] == T.PASS
    c = cash_check(ocf=19.99, capex=-5.0)
    row = by_id(c, "fcf_margin")
    assert row["status"] == T.FAIL and "below 15%" in row["gap"]


def test_roic_must_be_strictly_above_fifteen_percent_by_the_checklists_own_formula():
    assert T.roic_of(120, 120, 24, 640, 640)["roic"] == pytest.approx(0.15)                  # 120 x 0.8 / 640
    c = cash_check(equity=640.0, borrow_lt=100.0, cash=100.0)                                # invested capital 640: exactly 15%
    assert by_id(c, "roic")["status"] == T.FAIL
    c = cash_check(equity=639.0, borrow_lt=100.0, cash=100.0)
    assert by_id(c, "roic")["status"] == T.PASS
    healthy = by_id(cash_check(), "roic")
    assert healthy["status"] == T.PASS and healthy["figure"].startswith("24.0%")             # 96 / 400


@pytest.mark.parametrize("over, why", [({"pretax_income": -5.0, "tax": 0.0}, "pretax income is not positive"),
                                       ({"tax": 40.0}, "outside 0-100%"), ({"tax": -5.0}, "outside 0-100%"),
                                       ({"equity": 50.0}, "average invested capital is not positive")])
def test_roic_is_not_meaningful_when_the_formula_breaks_and_the_vendors_figure_is_never_used(over, why):
    row = by_id(cash_check(**over), "roic")
    assert row["status"] == T.UNKNOWN and row["figure"].startswith("NOT MEANINGFUL") and why in row["figure"]


def test_a_loss_maker_with_no_tax_line_is_not_meaningful_because_of_its_loss_not_because_a_line_is_missing():
    """Longbridge leaves out a line that is empty, and a company making a loss pays no tax (QUBT)."""
    s = stm_without("tax")
    for p in s["quarterly"]:
        p["pretax_income"] = -5.0
    f = facts(stm=s)
    row = by_id(T.check_cash(f, T.framework_fit(f, inputs())), "roic")
    assert row["status"] == T.UNKNOWN and row["figure"].startswith("NOT MEANINGFUL: pretax income is not positive (-20")
    profitable = stm_without("tax")          # profitable and no tax line: the rate cannot be known, so that is a missing input
    f = facts(stm=profitable)
    assert "the tax line is missing" in by_id(T.check_cash(f, T.framework_fit(f, inputs())), "roic")["figure"]


def test_a_tax_rate_far_from_normal_is_flagged_not_adjusted():
    row = by_id(cash_check(tax=1.5), "roic")                     # 5% effective rate
    assert "Tax flag" in row["note"] and "flattered" in row["note"]
    assert "Tax flag" not in (by_id(cash_check(), "roic")["note"] or "")


def test_stock_compensation_must_be_strictly_under_ten_percent_of_revenue_and_a_missing_line_is_unknown():
    assert by_id(cash_check(sbc=9.99), "sbc")["status"] == T.PASS
    assert by_id(cash_check(sbc=10.0), "sbc")["status"] == T.FAIL                 # 10% exactly is not "under"
    f = facts(stm=stm_without("sbc"))
    row = by_id(T.check_cash(f, T.framework_fit(f, inputs())), "sbc")
    assert row["status"] == T.UNKNOWN and "no stock-based compensation line" in row["figure"]


def test_check_two_fails_on_one_failed_condition_and_is_unknown_when_one_cannot_be_checked():
    assert cash_check(sbc=20.0)["status"] == T.FAIL
    f = facts(stm=stm_without("sbc"))
    assert T.check_cash(f, T.framework_fit(f, inputs()))["status"] == T.UNKNOWN
    assert cash_check()["status"] == T.PASS


# ---------------------------------------------------------------- check 3: is the price fair?
def test_peg_uses_growth_as_a_percentage_number_and_has_a_review_band():
    assert T.peg_status(30, 20) == (T.PASS, 1.5)                                  # 30 / 20, not 30 / 0.20
    assert T.peg_status(30.0001, 20)[0] == T.REVIEW and T.peg_status(40, 20)[0] == T.REVIEW      # above 1.5 up to and including 2.0
    assert T.peg_status(40.0001, 20)[0] == T.FAIL
    assert T.peg_status(30, 0.20)[0] == T.FAIL                                    # the 30 / 0.20 slip would read 150 and fail, not pass
    assert T.peg_status(30, 0) == (T.FAIL, None) and T.peg_status(30, -5) == (T.FAIL, None)     # no growth: not meaningful, a fail


def price_check(**kw):
    f = facts(**{k: kw.pop(k) for k in list(kw) if k in facts()})
    return T.check_price(f, inputs(**kw))


def test_a_company_that_reports_in_another_currency_than_it_trades_in_is_told_to_check_the_eps_currency():
    c = price_check(avg_fwd_pe=25.0, eps_cagr=20, stm={**stm(), "currency": "CNY"})
    assert "statements are in CNY and the stock trades in USD" in by_id(c, "fwd_pe")["note"]
    assert "statements are in" not in (by_id(price_check(avg_fwd_pe=25.0, eps_cagr=20, stm={**stm(), "currency": "USD"}), "fwd_pe")["note"] or "")


def test_forward_pe_is_inclusive_against_its_own_average_and_unknown_without_one():
    c = price_check(avg_fwd_pe=25.0, eps_cagr=20)                                   # 100 / 4 = 25: equal passes
    assert by_id(c, "fwd_pe")["status"] == T.PASS
    assert by_id(price_check(avg_fwd_pe=24.99, eps_cagr=20), "fwd_pe")["status"] == T.FAIL
    row = by_id(price_check(), "fwd_pe")
    assert row["status"] == T.UNKNOWN and row["need"] == 25.0 and "at least 25.0x" in row["note"]   # what would settle it
    assert "trailing P/E" in row["note"]                                           # and why the vendor's history is not used instead


def test_peg_needs_a_sourced_growth_and_says_what_growth_would_pass():
    row = by_id(price_check(avg_fwd_pe=30), "peg")
    assert row["status"] == T.UNKNOWN and row["need"] == pytest.approx(16.67, abs=0.01) and "at least 16.7% a year" in row["note"]
    assert by_id(price_check(eps_cagr=20), "peg")["status"] == T.PASS
    row = by_id(price_check(eps_cagr=15), "peg")                                   # 25 / 15 = 1.67
    assert row["status"] == T.REVIEW and "ANALYST JUDGMENT" in row["note"]


def test_negative_forward_earnings_fail_both_valuation_tests_as_not_meaningful():
    c = price_check(avg_fwd_pe=30, eps_cagr=20, analyst={"eps_forward": -1.0, "eps_forward_date": END})
    assert by_id(c, "fwd_pe")["status"] == T.FAIL and by_id(c, "peg")["status"] == T.FAIL
    c = price_check(avg_fwd_pe=30, eps_cagr=20, analyst={})
    assert by_id(c, "fwd_pe")["status"] == T.UNKNOWN and by_id(c, "peg")["status"] == T.UNKNOWN


def test_check_three_passes_with_review_when_only_the_peg_is_in_the_review_band():
    assert price_check(avg_fwd_pe=30, eps_cagr=20)["status"] == T.PASS
    assert price_check(avg_fwd_pe=30, eps_cagr=15)["status"] == T.PASS_REVIEW
    assert price_check(avg_fwd_pe=30, eps_cagr=10)["status"] == T.FAIL                # 25 / 10 = 2.5
    assert price_check(avg_fwd_pe=30)["status"] == T.UNKNOWN
    assert price_check(avg_fwd_pe=20, eps_cagr=15)["status"] == T.FAIL                # a failed condition outweighs the review


def rising(last, n=700, at=None):
    """Closes of 100 throughout and `last` on the final session."""
    return series(n=n, fn=lambda i, m: last if i == m - 1 else 100.0)


@pytest.mark.parametrize("last, status", [(149.99, T.PASS), (150.0, T.FAIL), (80.0, T.PASS)])
def test_the_three_month_rise_must_be_strictly_under_fifty_percent(last, status):
    bars = rising(last)
    pr = T.price_rise(facts(klines=bars, raw=bars))
    assert pr["status"] == status and pr["rise"] == pytest.approx(last / 100 - 1)
    assert pr["then"]["date"] == "2026-07-23" and pr["now"]["date"] == END             # three calendar months before the last session


def test_the_rise_is_measured_on_prices_adjusted_for_splits_and_not_for_dividends():
    ex = "2026-09-01"
    adjusted = series(n=700, fn=lambda i, n: 100.0 if i < n - 1 else 120.0)
    raw = [{**b, **{k: b[k] * (10 if b["t"] < ex else 1) for k in ("o", "h", "l", "c")}} for b in adjusted]   # traded at 1000 before a 10-for-1 split
    split = [{"date": ex, "ratio": 10.0}]
    pr = T.price_rise(facts(klines=adjusted, raw=raw, splits=split))
    assert pr["status"] == T.PASS and pr["rise"] == pytest.approx(0.20) and len(pr["splits"]) == 1       # +20%, not -88%
    # Longbridge's own series also folds in dividends: old closes are lower by 2%. The filter still reads the split-only prices.
    forward = [{**b, **{k: b[k] * 0.98 for k in ("o", "h", "l", "c")}} if b["t"] < "2026-10-01" else b for b in adjusted]
    pr = T.price_rise(facts(klines=forward, raw=raw, splits=split))
    assert pr["rise"] == pytest.approx(0.20)


def test_a_split_the_list_does_not_know_about_is_caught_rather_than_read_as_a_crash():
    ex = "2026-09-01"
    adjusted = series(n=700, fn=lambda i, n: 100.0)
    raw = [{**b, **{k: b[k] * (10 if b["t"] < ex else 1) for k in ("o", "h", "l", "c")}} for b in adjusted]
    pr = T.price_rise(facts(klines=adjusted, raw=raw, splits=[]))
    assert pr["status"] == T.UNKNOWN and "bonus or rights issue" in pr["why"]


def test_the_rise_is_unknown_when_its_inputs_are_missing_or_the_history_is_too_short():
    for kw in ({"raw": None}, {"splits": None}, {"raw": series(n=30), "klines": series(n=30)}):
        assert T.price_rise(facts(**kw))["status"] == T.UNKNOWN
    assert "could not be fetched" in T.price_rise(facts(splits=None))["why"]


def test_stage_one_fails_on_any_fail_is_incomplete_on_any_unknown_and_otherwise_passes_with_the_review_flag():
    mk = lambda *s: [{"status": x} for x in s]
    assert T.stage_one_status(mk(T.PASS, T.FAIL, T.UNKNOWN)) == "FAIL"
    assert T.stage_one_status(mk(T.PASS, T.UNKNOWN, T.PASS)) == "INCOMPLETE"
    assert T.stage_one_status(mk(T.PASS, T.NA, T.PASS)) == "INCOMPLETE"
    assert T.stage_one_status(mk(T.PASS, T.PASS_REVIEW, T.PASS)) == "PASS WITH PEG REVIEW"
    assert T.stage_one_status(mk(T.PASS, T.PASS, T.PASS)) == "PASS"


# ---------------------------------------------------------------- stage two
def test_software_and_hardware_are_told_apart_in_english_and_chinese_and_a_doubtful_one_is_left_unresolved():
    assert T.classify("Application Software")["value"] == "software" and T.classify("系统软件")["value"] == "software"
    assert T.classify("硬件、存储及外设")["value"] == "hardware" and T.classify("Semiconductors")["value"] == "hardware"
    assert T.classify("Software and Computer Hardware")["value"] is None       # both: left unresolved, not given the easier cut-off
    assert T.classify("Retail")["value"] is None and T.classify("")["value"] is None
    assert T.classify("Retail", "hardware") == {"value": "hardware", "source": "your choice", "industry": "Retail"}
    assert T.classify("Software", "auto")["source"] == "the industry's name"


def q1(**kw):
    f = facts(**{k: kw.pop(k) for k in list(kw) if k in facts()})
    return T.q_growth(f, T.framework_fit(f, inputs(**kw)))


def test_question_one_scores_one_for_three_conditions_half_for_two_and_nothing_for_one_or_none():
    assert q1()["points"] == 1.0                                       # growth 12.6%, gross margin 60% (software), operating margin 30%
    assert q1(stm=stm(operating_income=19.99))["points"] == 0.5        # operating margin just under 20%
    assert q1(stm=stm(operating_income=20.0))["points"] == 0.5          # exactly 20% is not "over"
    assert q1(stm=stm(operating_income=10.0, gross_profit=49.0))["points"] == 0.0      # growth only


def test_revenue_growth_compares_two_consecutive_trailing_years_and_must_be_over_ten_percent():
    flat = report(rev=lambda k: 100.0)
    assert q1(report=flat)["conditions"][0]["status"] == T.FAIL
    just = report(rev=lambda k: 100.0 * (1.1 if k >= 22 else 1.0))       # the latest four quarters are exactly 10% above the four before
    assert q1(report=just)["conditions"][0]["status"] == T.FAIL         # 10.00% is not "over 10%"
    over = report(rev=lambda k: 100.0 * (1.1001 if k >= 22 else 1.0))
    assert q1(report=over)["conditions"][0]["status"] == T.PASS
    gappy = report()
    del gappy["quarterly"][-3]                                          # a quarter missing: the eight are not consecutive
    assert q1(report=gappy)["conditions"][0]["status"] == T.UNKNOWN
    assert q1(report=None)["conditions"][0]["status"] == T.UNKNOWN


def test_the_gross_margin_cut_off_follows_the_category_and_an_unresolved_category_leaves_the_question_unscored():
    hw = q1(quote={"name": "X", "industry": "Hardware", "currency": "USD"}, stm=stm(gross_profit=40.0))     # 40% is over hardware's 35%
    assert hw["conditions"][1]["status"] == T.PASS and hw["points"] == 1.0
    sw = q1(stm=stm(gross_profit=40.0))                                                                       # but under software's 50%
    assert sw["conditions"][1]["status"] == T.FAIL and sw["points"] == 0.5
    retail = q1(quote={"name": "X", "industry": "Retail", "currency": "USD"})
    assert retail["conditions"][1]["status"] == T.NA
    assert retail["points"] is None and (retail["low"], retail["high"]) == (0.5, 1.0)      # growth and margin hold: 0.5, or 1 if the gross margin does
    chosen = q1(quote={"name": "X", "industry": "Retail", "currency": "USD"}, category="hardware")
    assert chosen["points"] == 1.0 and "your choice" in chosen["conditions"][1]["label"]


@pytest.mark.parametrize("rate, points", [(0.0499, 1.0), (0.05, 0.0), (0.0501, 0.0), (0.0, 1.0)])
def test_short_interest_scores_only_under_five_percent_with_no_partial_credit(rate, points):
    q = T.q_short(facts(short={"rate": rate, "shares_short": 1.0, "date": "2026-10-09", "float_shares": 100.0}))
    assert q["points"] == points and f"{rate * 100:.2f}%" in q["evidence"] and "2026-10-09" in q["evidence"]


@pytest.mark.parametrize("settled, scored", [("2026-08-25", True), ("2026-08-24", False), ("2022-06-15", False), (None, False)])
def test_a_short_interest_report_must_be_recent_to_be_the_latest_reported(settled, scored):
    """Longbridge's history for JPM and KO stops at 2022-06-15. Today is 2026-10-24: 60 days back is 2026-08-25."""
    q = T.q_short(facts(short={"rate": 0.0064, "shares_short": 1.0, "date": settled, "float_shares": 100.0}))
    assert (q["points"] is not None) == scored
    if not scored:
        assert "not the latest reported" in q["evidence"] and (q["low"], q["high"]) == (0.0, 1.0) and q["conditions"][0]["status"] == T.UNKNOWN


def test_short_interest_is_unknown_where_longbridge_does_not_publish_it_or_the_feed_failed():
    assert "US and Hong Kong stocks only" in T.q_short(facts(symbol="600519.SH", short=None))["evidence"]
    q = T.q_short(facts(short=None))
    assert q["points"] is None and "could not be fetched" in q["evidence"] and (q["low"], q["high"]) == (0.0, 1.0)


def q3(**kw):
    return T.q_returns(facts(**kw))


def test_question_three_scores_one_for_both_conditions_half_for_one_and_zero_for_neither():
    assert q3()["points"] == 1.0                                          # the stock about doubles from 2021 against SPY +50%; shares fall about 10%
    behind = {"start": {"date": "2021-10-22", "close": 100.0}, "end": {"date": END, "close": 900.0}}
    assert q3(spy=behind)["points"] == 0.5
    more = report(rev=lambda k: 100.0, shares=lambda k: 100 + k * 0.5)    # shares rise over the years
    assert q3(spy=behind, report=more)["points"] == 0.0
    assert q3(report=more)["points"] == 0.5


def test_five_year_total_return_is_unknown_without_history_in_dollars_or_a_clear_gap_to_the_proxy():
    c = q3(klines=series(n=600, fn=wave))["conditions"][0]
    assert c["status"] == T.UNKNOWN and "less than five years" in c["figure"]
    c = q3(quote={"name": "X", "industry": "Software", "currency": "HKD", "price": 1.0})["conditions"][0]
    assert c["status"] == T.UNKNOWN and "does not convert currencies" in c["figure"]
    assert q3(spy=None)["conditions"][0]["status"] == T.UNKNOWN
    own = pricehist.return_between(facts()["klines"], "2021-10-23", END)["return"]
    tied = q3(spy={"start": {"date": "2021-10-22", "close": 100.0}, "end": {"date": END, "close": 100 * (1 + own + 0.005)}})   # the proxy is half a point ahead
    assert tied["conditions"][0]["status"] == T.UNKNOWN and "too close to call" in tied["conditions"][0]["note"]


def test_diluted_shares_are_net_profit_over_eps_over_four_quarters_five_years_apart_within_the_rounding_of_eps():
    sc = T.share_counts(report(shares=lambda k: 120 - k * 0.5))        # 120 shares at the oldest quarter, falling half a share a quarter
    hm = lambda xs: len(xs) / sum(1 / x for x in xs)                 # net profit over summed EPS is the harmonic mean of the four
    assert sc["now"] == pytest.approx(hm([109, 108.5, 108, 107.5]))   # the latest four quarters
    assert sc["then"] == pytest.approx(hm([119, 118.5, 118, 117.5]))  # the four ending exactly five years (20 quarters) earlier
    assert sc["change"] < -0.08 and sc["now_end"] == "Q3 2026" and sc["then_end"] == "Q3 2021"
    assert T.share_counts(report(n=20)) is None                           # fewer than six years of quarters
    assert T.share_counts(None) is None
    flat = T.share_counts(report(shares=lambda k: 100.0))
    assert flat["change"] == pytest.approx(0.0)
    q = q3(report=report(shares=lambda k: 100.0))
    assert q["conditions"][1]["status"] == T.UNKNOWN and "rounding" in q["conditions"][1]["note"]    # unchanged is within the data's own error


def test_the_rounding_allowance_shrinks_for_quarters_reported_before_a_split():
    """Companies report EPS to the cent in the shares of the day and the split adjustment comes afterwards: NVDA's old $1.54 is 0.385
    here, good to a tenth of a cent, not to a cent. Treating adjusted EPS as rounded to the cent called a 3.2% fall in shares unknowable."""
    r = report(shares=lambda k: 1000.0)                                         # EPS of 0.24 a quarter: 0.96 over four
    plain = T.share_counts(r)
    assert plain["tol"] == pytest.approx(2 * 0.02 / 0.96)                         # a cent either way, summed over four quarters, in both periods
    split = T.share_counts(r, [{"date": "2024-06-10", "ratio": 10.0}])          # the quarters five years ago were reported before the split
    assert split["tol"] == pytest.approx(0.02 / 0.96 + 0.002 / 0.96)
    reverse = T.share_counts(r, [{"date": "2024-06-10", "ratio": 0.1}])         # a reverse split makes the old figures coarser, not finer
    assert reverse["tol"] > plain["tol"]


def test_a_nonpositive_share_count_cannot_be_derived():
    r = report()
    for row in r["quarterly"]:
        row["eps"] = -abs(row["eps"])        # a profit with a negative EPS: not a share count
    assert T.share_counts(r) is None


def mk(p1, p2, p3, q4, p5, p6, p7, p8):
    def q(n, p):
        return {"n": n, "points": p, "high": 1.0, "status": "SCORED" if p is not None else "UNKNOWN"}
    four = {"n": 4, "points": None, "high": 1.0, "status": "PENDING"} if q4 == "pending" else q(4, 1.0 if q4 else None)
    return [q(1, p1), q(2, p2), q(3, p3), four, q(5, p5), q(6, p6), q(7, p7), q(8, p8)]


def test_with_question_four_pending_six_of_seven_makes_the_list_once_confirmed():
    t = T.totals(mk(1, 1, 1, "pending", 1, 1, 1, 0), True)             # 6 of 7
    assert t["label"] == "MAKES THE LIST ONCE QUESTION 4 IS CONFIRMED" and t["out_of"] == 7 and t["confirmed"] == 6
    t = T.totals(mk(1, 1, 1, "pending", 1, 1, .5, 0), True)             # 5.5
    assert t["label"] == "CLOSE CALL ONCE QUESTION 4 IS CONFIRMED"
    t = T.totals(mk(1, 1, 1, "pending", 1, 1, 0, 0), True)              # 5
    assert t["label"] == "MISSES" and "cannot lift this to 7" in t["note"]
    assert T.totals(mk(1, 1, 1, "pending", 1, 1, 1, 1), True)["label"].startswith("MAKES THE LIST ONCE")


def test_confirmed_the_list_is_seven_of_eight_with_six_and_a_half_a_close_call():
    assert T.totals(mk(1, 1, 1, True, 1, 1, 1, 0), True)["label"] == "MAKES THE LIST"        # 6 and the point for question 4: 7
    assert T.totals(mk(1, 1, 1, True, 1, 1, .5, 0), True)["label"] == "CLOSE CALL"           # 6.5
    t = T.totals(mk(1, 1, 1, True, 1, 1, 0, 0), True)                                        # 6
    assert t["label"] == "MISSES" and t["out_of"] == 8


def test_an_unknown_question_makes_the_result_provisional_with_a_range_and_never_awards_a_guess():
    t = T.totals(mk(1, 1, 1, True, 1, 1, None, None), True)          # 6 confirmed (incl. question 4), two open
    assert t["label"] == "PROVISIONAL / INCOMPLETE" and t["unknown"] == [7, 8] and t["out_of"] == 8
    assert (t["confirmed"], t["low"], t["high"]) == (6, 6, 8)
    assert "questions 7, 8 are settled" in t["note"]
    t = T.totals(mk(1, 1, 1, "pending", 1, 1, None, None), True)      # pending adds its possible point to the range
    assert (t["confirmed"], t["low"], t["high"]) == (5, 5, 8) and "and question 4 is confirmed" in t["note"]
    t = T.totals(mk(0, 0, 0, "pending", 0, 0, None, None), True)      # cannot reach 6.5 even if everything left scores
    assert t["high"] == 3 and "cannot reach 6.5" in t["note"]


def test_stage_two_run_on_request_never_makes_the_stock_eligible():
    t = T.totals(mk(1, 1, 1, True, 1, 1, 1, 1), False)
    assert t["label"].startswith("MAKES THE LIST (NOT ELIGIBLE") and "does not change the stage-one result" in t["note"]


# ---------------------------------------------------------------- the scorecard
def run(**kw):
    f = facts(**{k: kw.pop(k) for k in list(kw) if k in facts()})
    return T.build(f, inputs(**kw))


def test_a_company_that_clears_every_stage_one_check_goes_on_to_stage_two_and_is_eligible_for_the_timing_points():
    out = run(**GOOD, q5=3, q6=3, q7=3, q8=3)
    assert out["stage_one"]["status"] == "PASS" and out["stage_two"]["run"] and not out["stage_two"]["on_request"]
    tot = out["stage_two"]["total"]
    assert tot["label"] == "MAKES THE LIST ONCE QUESTION 4 IS CONFIRMED" and tot["confirmed"] == 7 and tot["out_of"] == 7
    assert out["verdict"]["line"].startswith("TEST.US (Test Co): stage one PASS; stage two MAKES THE LIST ONCE QUESTION 4 IS CONFIRMED (7 of 7)")
    tm = out["timing"]
    assert tm["available"] and tm["swing"]["low"]["price"] < tm["swing"]["high"]["price"] and tm["ema200"]["date"] == END
    assert tm["earnings"]["date"] == "2026-11-05" and "not predicted support" in tm["note"]
    confirmed = run(**GOOD, q4=True, q5=3, q6=3, q7=3, q8=3)
    assert confirmed["stage_two"]["total"]["label"] == "MAKES THE LIST" and confirmed["stage_two"]["total"]["confirmed"] == 8


def test_timing_points_appear_only_for_a_listing_stock():
    assert run(**GOOD, q5=3, q6=3, q7=3, q8=3)["timing"] is not None
    assert run(**GOOD, q5=1, q6=1, q7=1, q8=1)["timing"] is None             # stage two misses
    assert run(avg_fwd_pe=20, eps_cagr=20, q5=3, q6=3, q7=3, q8=3, stage2=True)["timing"] is None   # stage one fails, stage two only on request
    assert run(**GOOD)["timing"] is None                                       # open questions: provisional, not a listing


def test_stage_one_incomplete_or_failed_skips_stage_two_unless_asked():
    out = run()                                                                  # no five-year average, no growth: the price check is open
    assert out["stage_one"]["status"] == "INCOMPLETE" and not out["stage_two"]["run"] and out["stage_two"]["questions"] == []
    assert "stage two not run" in out["verdict"]["line"]
    on_request = run(stage2=True, q5=3)
    assert on_request["stage_two"]["run"] and on_request["stage_two"]["on_request"]
    assert "NOT ELIGIBLE" in on_request["stage_two"]["total"]["label"] or on_request["stage_two"]["total"]["label"].startswith("PROVISIONAL")
    failed = run(avg_fwd_pe=20, eps_cagr=20)
    assert failed["stage_one"]["status"] == "FAIL" and not failed["stage_two"]["run"]


def test_the_peg_review_flag_travels_to_the_verdict():
    out = run(avg_fwd_pe=30, eps_cagr=15, q5=3, q6=3, q7=3, q8=3)
    assert out["stage_one"]["status"] == "PASS WITH PEG REVIEW" and "stage one PASS WITH PEG REVIEW" in out["verdict"]["line"]
    assert out["stage_two"]["run"]


def test_a_bank_is_incomplete_not_failed_and_the_price_tests_still_run():
    out = run(quote={"name": "Big Bank", "industry": "Banks", "currency": "USD", "price": 100.0}, avg_fwd_pe=30, eps_cagr=20)
    s = {c["id"]: c["status"] for c in out["stage_one"]["checks"]}
    assert s == {"survive": T.NA, "cash": T.NA, "price": T.PASS} and out["stage_one"]["status"] == "INCOMPLETE"
    assert out["fit"]["notes"] and "NOT APPLICABLE" in out["fit"]["notes"][0]


def test_a_company_with_no_revenue_is_not_scored_on_margins():
    quarters = [{**x, "revenue": 0.0} for x in qlist(5)]
    out = run(stm=stm(quarterly=quarters))
    assert [c["status"] for c in out["stage_one"]["checks"]][:2] == [T.PASS, T.NA]
    assert any("no trailing revenue" in n for n in out["fit"]["notes"])


def test_points_lost_lists_failures_with_their_shortfall_apart_from_unknowns():
    out = run(avg_fwd_pe=20, eps_cagr=20, stage2=True, q5=3, q6=2)
    lost = out["points_lost"]
    pe = next(x for x in lost["failed"] if x["label"].startswith("Forward P/E"))
    assert pe["gap"] == "5.0x above its average" and pe["where"].startswith("Check 3")
    assert not any(x["label"].startswith("Forward P/E") for x in lost["unknown"])
    assert [x["n"] for x in lost["below_full"]] == [6] and lost["below_full"][0]["points"] == 0.5
    assert {x["where"] for x in lost["unknown"]} >= {"Question 4", "Question 7", "Question 8"}


def test_verify_first_names_what_the_result_waits_for_then_the_figures_nearest_their_cut_off():
    out = run()
    assert [v["title"] for v in out["verify"][:2]] == ["Five-year average forward P/E", "Expected EPS growth, 3 to 5 years"]
    assert all(v["where"] and v["why"] and v["value"] for v in out["verify"]) and len(out["verify"]) <= 3
    out = run(**GOOD, stm=stm(ocf=20.5, capex=-5.0))                              # a 15.5% free cash flow margin sits closest to its cut-off
    assert out["verify"][0]["title"] == "TTM free cash flow margin" and out["verify"][0]["value"] == "15.5%"
    assert "3.3% from its cut-off" in out["verify"][0]["why"]


def test_a_figure_far_from_its_cut_off_is_not_offered_as_one_a_small_error_could_flip():
    titles = [v["title"] for v in run(**GOOD)["verify"]]
    assert "TTM ROIC" not in titles and "TTM stock-based compensation / revenue" not in titles       # 24% against 15%, 3% against 10%
    assert any("Gross margin" in t for t in titles)                                                   # 60% against 50%: within 30% of it
    assert "Nothing stands out" not in json.dumps(titles)


def test_the_draft_for_question_four_is_built_from_measures_that_pass_and_is_labelled_proposed():
    out = run(**GOOD, stm=stm(ocf=20.5, capex=-5.0))
    p = out["proposal"]
    assert p["thesis"].startswith("Test Co converts at least 15% of its revenue") and "greater than 10%" not in p["thesis"]
    assert len(p["conditions"]) == 2 and "falls below 15% at two consecutive quarterly reports (now 15.5%)" in p["conditions"][0]
    assert "not a fact or your view" in p["note"]
    nothing = run(stm=stm(ocf=1.0, capex=-5.0, operating_income=5.0, pretax_income=5.0), report=report())
    assert nothing["proposal"] is None


def test_inputs_are_cleaned_before_they_are_used():
    i = T.clean_inputs(avg_fwd_pe=-3, eps_cagr="abc", category="bogus", avg_fwd_pe_src="  vendor " + "x" * 400, q5=4, q6=3, q7="2")
    assert i["avg_fwd_pe"] is None and i["eps_cagr"] is None and i["category"] == "auto"       # a P/E must be positive; text that is not a number is dropped
    assert i["avg_fwd_pe_src"].startswith("vendor") and len(i["avg_fwd_pe_src"]) == 200
    assert i["ratings"] == {5: None, 6: 3, 7: None, 8: None}                                    # a rating is 1, 2 or 3
    assert T.clean_inputs(avg_fwd_pe=float("inf"))["avg_fwd_pe"] is None and T.clean_inputs(eps_cagr=-5)["eps_cagr"] == -5.0


def test_the_whole_scorecard_is_plain_json():
    out = run(**GOOD, q4=True, q5=3, q6=3, q7=2, q8=3)
    assert json.loads(json.dumps(out)) == out
    assert out["notice"].startswith("Research notice: This applies a personal checklist") and out["closing"].startswith("Before you trust this")


# ---------------------------------------------------------------- the endpoint and the service, on demo data
def test_the_endpoint_runs_the_filter_and_echoes_the_inputs_it_used():
    c = TestClient(app)
    out = c.get("/api/filter?symbol=MSFT.US&avg_fwd_pe=25&eps_cagr=12&eps_cagr_src=vendor&category=software&stage2=true&q5=3&q6=2").json()
    assert out["status"] == "ok" and out["symbol"] == "MSFT.US" and out["stage_two"]["run"]
    assert out["inputs"]["avg_fwd_pe"] == 25 and out["inputs"]["eps_cagr_src"] == "vendor" and out["inputs"]["ratings"]["5"] == 3
    assert [q["n"] for q in out["stage_two"]["questions"]] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert out["fit"]["category"]["value"] == "software" and out["fit"]["category"]["source"] == "your choice"


@pytest.mark.parametrize("query", ["avg_fwd_pe=0", "avg_fwd_pe=-4", "q5=4", "q8=0", "category=biotech", "eps_cagr=900", "avg_fwd_pe=abc"])
def test_the_endpoint_rejects_inputs_outside_their_range(query):
    assert TestClient(app).get(f"/api/filter?symbol=MSFT.US&{query}").status_code == 422


def test_the_endpoint_checks_the_symbol_like_the_others_do():
    assert TestClient(app).get("/api/filter?symbol=--help").status_code == 400


def test_a_failed_optional_feed_leaves_only_its_own_tests_unknown(monkeypatch):
    d = Data()

    def boom(*a):
        raise DataError("service down")
    monkeypatch.setattr(d.p, "report_history", boom)
    monkeypatch.setattr(d.p, "short_interest_detail", boom)
    out = service.get_filter(d, "AAPL.US", inputs(**GOOD, stage2=True))
    q = {x["n"]: x for x in out["stage_two"]["questions"]}
    assert q[1]["conditions"][0]["status"] == T.UNKNOWN and q[2]["points"] is None and q[3]["conditions"][1]["status"] == T.UNKNOWN
    assert q[3]["conditions"][0]["status"] != T.UNKNOWN or "S&P 500" in q[3]["conditions"][0]["figure"]      # the price side still ran
    assert out["stage_one"]["checks"][0]["status"] in (T.PASS, T.FAIL, T.UNKNOWN)


def test_a_stock_in_another_currency_is_not_compared_with_the_s_and_p_500(monkeypatch):
    d = Data()
    real = d.p.quote
    monkeypatch.setattr(d.p, "quote", lambda s: {**real(s), "currency": "HKD"})
    called = []
    monkeypatch.setattr(d.p, "adj_close", lambda *a: called.append(a))
    out = service.get_filter(d, "700.HK", inputs(stage2=True))
    assert called == [] and out["stage_two"]["questions"][2]["conditions"][0]["status"] == T.UNKNOWN


def test_a_quote_without_a_currency_trades_in_its_markets_currency(monkeypatch):
    """Longbridge's quote has an empty currency (AAPL, Tencent); a US listing trades in dollars and is compared with the S&P 500."""
    d = Data()
    real = d.p.quote
    monkeypatch.setattr(d.p, "quote", lambda s: {**real(s), "currency": ""})
    asked = []
    real_close = d.p.adj_close
    monkeypatch.setattr(d.p, "adj_close", lambda sym, on: asked.append((sym, on)) or real_close(sym, on))
    out = service.get_filter(d, "AAPL.US", inputs(stage2=True))
    assert [a[0] for a in asked] == ["SPY.US", "SPY.US"] and out["company"]["currency"] == "USD"
    assert out["stage_two"]["questions"][2]["conditions"][0]["status"] != T.UNKNOWN
    asked.clear()
    assert service.get_filter(d, "700.HK", inputs(stage2=True))["company"]["currency"] == "HKD" and asked == []


def test_the_filter_stops_when_the_statements_fail_reconciliation(monkeypatch):
    d = Data()
    real = d.p.statements

    def broken(sym):
        s = real(sym)
        s["annual"][-1]["current_assets"] *= 3
        return s
    monkeypatch.setattr(d.p, "statements", broken)
    out = service.get_filter(d, "AAPL.US", inputs())
    assert out["status"] == "unavailable" and "reconciliation" in out["message"] and "stage_one" not in out


def test_the_demo_provider_serves_everything_the_filter_asks_for():
    d = Data()
    s = service.get_filter(d, "NVDA.US", inputs(**GOOD, stage2=True, category="hardware"))
    assert s["status"] == "ok" and s["stage_one"]["checks"][0]["rows"][0]["status"] != T.UNKNOWN
    rep = d.call("report_history", "NVDA.US")
    assert len(rep["quarterly"]) == 24 and rep["quarterly"][-1]["fp"] in (1, 2, 3, 4) and rep["annual"][0]["fp"] is None
    assert d.call("splits", "NVDA.US") == [] and d.call("kline_raw", "NVDA.US", 600)[-1] == d.call("kline", "NVDA.US", 1500)[-1]
    assert d.call("adj_close", "SPY.US", "2000-01-01") is None      # before the series starts: an error the data layer turns into None


# ---------------------------------------------------------------- the page, as the real app.js draws it (under Node)
def page(tmp_path, response, **scenario):
    """The Two-stage filter section from running web/app.js: {html, query, conc}."""
    (tmp_path / "scenario.json").write_text(json.dumps({"symbol": "TEST.US", "filter": response, **scenario}), encoding="utf-8")
    done = subprocess.run([NODE, str(ROOT / "tests" / "filter_card.js"), str(ROOT / "web" / "app.js"), str(tmp_path / "scenario.json")],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def plain(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))


def buffett():
    return {"quality": {"buffett": {"stars": {"moat": 4, "management": 3, "visibility": 5},
                                    "evidence": {"moat": ["Average gross margin 46% over 5 years"], "management": ["PASS: Share count shrinking over 3 years"],
                                                 "visibility": ["Revenue CAGR 8.1%"]}}}}


@needs_node
def test_the_page_lays_out_all_six_sections_for_a_company_that_passes(tmp_path):
    out = page(tmp_path, run(**GOOD, q5=3, q6=2, q7=3, q8=3), analysis=buffett())
    t = plain(out["html"])
    assert "1. TEST.US (Test Co): stage one PASS; stage two MAKES THE LIST ONCE QUESTION 4 IS CONFIRMED (6.5 of 7)" in t
    assert "Research notice: This applies a personal checklist to public information. It is not a buy/sell recommendation." in t
    for title in ("Check 1: Can it survive?", "Check 2: Does it make cash?", "Check 3: Is the price fair under this checklist?"):
        assert title in t
    for q in ("1. Growing, with strong margins?", "2. Few investors betting against it?", "3. Rewarding shareholders?",
              "4. Written down what would change your mind?", "5. A moat, and a CEO you would trust with the cash?",
              "6. Is the trend behind it years from done?", "7. Is the culture strong?", "8. Does it keep the best people?"):
        assert q in t
    for head in ("2. Stage one", "3. Stage two", "4. Where the points went", "5. What to verify first", "6. Timing reference points"):
        assert head in t
    assert "Before you trust this: verify the inputs in section 5" in t and "Reference points only: not predicted support" in t
    assert 'class="tag good">PASS<' in out["html"]


@needs_node
def test_an_incomplete_company_shows_what_is_missing_and_what_would_settle_it(tmp_path):
    out = page(tmp_path, run())
    t = plain(out["html"])
    assert "stage one INCOMPLETE; stage two not run" in t and "Run stage two anyway" in t
    assert "Passes if it is at least 25.0" in t and "PEG is at most 1.5 if it is at least 16.7" in t        # the hints beside the two inputs
    assert 'class="tag warn">UNKNOWN<' in out["html"] and "Unknown or not applicable (not failures)" in t
    assert "Five-year average forward P/E: not provided" in t                                              # first in what to verify
    assert "Questions 5 to 8: your ratings" in t and "not rated" in t


@needs_node
def test_a_failure_is_shown_with_its_shortfall(tmp_path):
    t = plain(page(tmp_path, run(avg_fwd_pe=20, eps_cagr=20))["html"])
    assert "stage one FAIL; stage two not run" in t and "Shortfall: 5.0x above its average" in t
    assert "A failed stage one ends the screen." in t


@needs_node
def test_what_the_user_has_kept_becomes_the_request_and_question_four_needs_both_texts(tmp_path):
    kept = {"avg": "24.5", "avgSrc": "Vendor, 2026-10-20", "cagr": "12", "cagrSrc": "Vendor 5y", "category": "software", "stage2": True,
            "thesis": "It keeps compounding.", "conditions": "FCF margin under 15% twice.", "q4": True, "r5": "3", "r6": "2"}
    q = dict(x.split("=", 1) for x in page(tmp_path, run(), stored=kept)["query"].split("&"))
    assert q["avg_fwd_pe"] == "24.5" and q["eps_cagr"] == "12" and q["category"] == "software" and q["stage2"] == "true"
    assert q["q4"] == "true" and q["q5"] == "3" and q["q6"] == "2" and "q7" not in q and q["symbol"] == "TEST.US"
    assert q["avg_fwd_pe_src"] == "Vendor%2C+2026-10-20"
    half = dict(x.split("=", 1) for x in page(tmp_path, run(), stored={**kept, "conditions": "  "})["query"].split("&"))
    assert "q4" not in half                                                   # confirmed, but one of the two texts is empty: not confirmed
    assert page(tmp_path, run())["query"] == "symbol=TEST.US&category=auto"   # nothing kept: nothing sent


@needs_node
def test_the_form_remembers_what_was_kept(tmp_path):
    kept = {"avg": "24.5", "cagr": "12", "category": "hardware", "stage2": True, "thesis": "My thesis", "r5": "3"}
    html = page(tmp_path, run(), stored=kept)["html"]
    assert 'name="avg" type="number" min="0" step="0.1" value="24.5"' in html and 'name="cagr" type="number" step="0.1" value="12"' in html
    assert '<option value="hardware" selected>' in html and 'name="stage2" type="checkbox" checked' in html
    assert re.search(r'name="r5" value="3" checked', html) and not re.search(r'name="r6" value="\d" checked', html)
    assert ">My thesis</textarea>" in html


@needs_node
def test_question_four_shows_the_proposal_until_it_is_confirmed_and_then_the_users_own_words(tmp_path):
    pending = page(tmp_path, run(**GOOD, stm=stm(ocf=20.5, capex=-5.0), stage2=True))
    t = plain(pending["html"])
    assert "PENDING" in t and "Proposed, not yet yours:" in t and "Test Co converts at least 15% of its revenue" in t
    assert "Use the proposed text" in t and 'data-tsf="propose"' in pending["html"]
    done = page(tmp_path, run(**GOOD, q4=True, stm=stm(ocf=20.5, capex=-5.0)), stored={"thesis": "Mine.", "conditions": "Line 1\nLine 2", "q4": True})
    t = plain(done["html"])
    assert "CONFIRMED by you" in t and "Yours: Mine. Line 1 Line 2" in t and "Proposed, not yet yours" not in t
    assert "Line 1<br>Line 2" in done["html"]


@needs_node
def test_the_judgment_questions_carry_the_apps_evidence_but_no_rating_of_their_own(tmp_path):
    out = page(tmp_path, run(avg_fwd_pe=20, eps_cagr=20, stage2=True), analysis=buffett())
    t = plain(out["html"])
    assert "Buffett read: moat ★★★★☆" in t and "Average gross margin 46% over 5 years" in t and "Revenue CAGR 8.1%" in t
    assert "Nothing in this app's data measures culture" in t and "Nothing in this app's data measures turnover or retention" in t
    assert "UNKNOWN: not rated. Insufficient evidence is UNKNOWN, not a neutral 2." in t


@needs_node
def test_nothing_from_the_server_or_the_user_is_injected_as_html(tmp_path):
    r = run(**GOOD)
    r["verdict"]["line"] = '<img src=x onerror="alert(1)">'
    r["stage_one"]["checks"][0]["rows"][0]["figure"] = "<script>alert(2)</script>"
    html = page(tmp_path, r, stored={"thesis": "<b>bold</b>", "avgSrc": '"><script>x</script>'})["html"]
    assert "<img" not in html and "<script" not in html and "<b>bold</b>" not in html
    assert "&lt;img src=x" in html and "&lt;script&gt;alert(2)" in html and "&lt;b&gt;bold&lt;/b&gt;" in html


@needs_node
def test_the_concentration_flag_marks_a_sentence_giving_a_share_over_twenty_percent(tmp_path):
    filing = {"status": "ok", "concentration": [{"text": "One customer accounted for 24% of net sales.", "form": "10-K", "filed": "2026-02-01"},
                                                {"text": "No other customer was above 10% of revenue.", "form": "10-K", "filed": "2026-02-01"}]}
    out = page(tmp_path, run(), filing=filing)
    assert out["conc"].count("over 20%") == 1 and "One customer accounted for 24% of net sales." in out["conc"] and "confirm in the filing" in out["conc"]
    assert out["conc"].index("over 20%") < out["conc"].index("No other customer")      # the tag belongs to the first sentence only
    assert "not proof that none exists" in page(tmp_path, run(), filing={"status": "ok", "concentration": []})["conc"]
    off = page(tmp_path, run(), filing={"status": "not_configured", "message": "Set SEC_USER_AGENT in .env."})["conc"]
    assert "Set SEC_USER_AGENT in .env." in off and "Not checking is not proof" in off
    assert "still being read" in page(tmp_path, run())["conc"]


@needs_node
def test_an_unavailable_filter_says_why_and_nothing_else(tmp_path):
    out = page(tmp_path, {"symbol": "TEST.US", "status": "unavailable", "message": "The statements fail the reconciliation checks."})
    assert plain(out["html"]).strip() == "Two-stage filter The statements fail the reconciliation checks."

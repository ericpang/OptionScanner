import os

os.environ["DATA_MODE"] = "demo"

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app import metrics as M, service
from app.data import Data
from app.demo import DemoProvider
from app.normalize import finalize, parse_statement
from app.quality import is_financial

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
TECH = {"industry": "Technology"}


def per(ocf=30.0, capex=-5.0, issued=0.0, repaid=-4.0, rev=100.0, **kw):
    """One period of statements as the calculators see them (outflows negative)."""
    return {"ocf": ocf, "capex": capex, "debt_issued": issued, "debt_repaid": repaid, "revenue": rev, **kw}


def stm(quarters, annual=None):
    return {"annual": annual or [per(ocf=100.0, capex=-20.0, issued=10.0, repaid=-40.0, rev=400.0)], "quarterly": quarters}


# ---------------------------------------------------------------- reading the statements
def lb(fp_end, **fields):
    """One period as `longbridge financial-statement` returns it: a list of {field, value} with the value a string."""
    return {"fp_end": fp_end, "report_txt": fp_end,
            "fields": [{"field": k, "value": None if v is None else str(v)} for k, v in fields.items()]}


def test_the_debt_totals_are_read_without_counting_their_short_and_long_term_parts_again():
    # Apple's fiscal 2025 as Longbridge returns it; the parts are listed beside the totals they add up to
    raw = [lb("2025-09-27", cash_oper=111482, capex=-12715, total_debt_issued=4481, st_debt_issued=None, lt_debt_issued=4481,
              total_debt_repaid=-12964, st_debt_repaid=-2032, lt_debt_repaid=-10932)]
    p = finalize(parse_statement(raw)[0])
    assert (p["debt_issued"], p["debt_repaid"]) == (4481, -12964)


def test_a_period_with_a_cash_flow_statement_but_no_debt_lines_issued_and_repaid_nothing():
    # Longbridge leaves an empty line out, so a quiet quarter has none: that is a zero, or a year of quarters could not be summed
    raw = [lb("2025-12-27", cash_oper=53925, capex=-2373), lb("2026-03-28", cash_oper=1, total_debt_repaid=-5751)]
    quiet, repaid = (finalize(p) for p in parse_statement(raw))
    assert (quiet["debt_issued"], quiet["debt_repaid"]) == (0.0, 0.0)
    assert (repaid["debt_issued"], repaid["debt_repaid"]) == (0.0, -5751)       # one of the two lines is enough


def test_a_period_with_no_cash_flow_statement_has_no_debt_figures_at_all():
    p = finalize(parse_statement([lb("2025-12-27", total_assets=5, total_ca=2)])[0])
    assert "debt_issued" not in p and "debt_repaid" not in p                       # unknown is not zero
    assert M.fcfe_of(p) is None


def test_a_debt_line_keeps_the_sign_it_came_with_so_a_reversal_nets_off():
    """Tencent's quarterly statements are derived from half-year reports, so one quarter comes back with negative debt
    issued and positive debt repaid (a reversal of the half-year's gross figures). These are Longbridge's numbers for the
    four quarters to 2026-06-30, in HKD millions. Forcing every repayment negative made the year's net borrowing -198,827
    instead of +73,121 and turned a free cash flow of 187,000 into a loss."""
    quarters = [("2025-09-30", 93252, -15312, -192815, 135974), ("2025-12-31", 73981, -31068, 257315, -206206),
                ("2026-03-31", 115206, -42058, None, None), ("2026-06-30", 60882, -67703, 111259, -32406)]
    raw = [lb(end, cash_oper=o, capex=c, total_debt_issued=i, total_debt_repaid=r) for end, o, c, i, r in quarters]
    parsed = [finalize(p) for p in parse_statement(raw)]
    assert (parsed[0]["debt_issued"], parsed[0]["debt_repaid"]) == (-192815, 135974)
    assert (parsed[2]["debt_issued"], parsed[2]["debt_repaid"]) == (0.0, 0.0)             # a quarter with no lines
    f = M.fcfe_block({"annual": [parsed[-1]], "quarterly": parsed}, TECH, None)
    assert f["basis"] == "ttm" and f["net_borrowing"] == 73121 == 175759 - 102638
    assert f["ttm"] == 343321 - 156141 + 73121 and f["ttm"] > 0


# ---------------------------------------------------------------- the figure
def test_fcfe_is_operating_cash_flow_less_capex_plus_net_borrowing():
    assert M.fcfe_of(per()) == 21                                                  # 30 - 5 + 0 - 4: paying debt down costs equity cash
    assert M.fcf_of(per()) == 25 and M.fcfe_of(per()) < M.fcf_of(per())
    assert M.fcfe_of(per(issued=10.0, repaid=0.0)) == 35                          # borrowing adds to it
    assert M.fcfe_of(per(issued=10.0, repaid=-10.0)) == M.fcf_of(per())           # refinancing changes nothing
    assert M.fcfe_of(per(capex=5.0)) == 21                                         # capex counts as an outflow whichever sign it has
    assert M.fcfe_of({"ocf": 30.0, "capex": -5.0, "debt_repaid": -4.0}) == 21     # one of the two debt lines is enough


@pytest.mark.parametrize("p", [None, {}, {"capex": -5.0, "debt_repaid": -4.0}, {"ocf": 30.0, "debt_repaid": -4.0},
                               {"ocf": 30.0, "capex": -5.0}])
def test_fcfe_needs_operating_cash_flow_capex_and_at_least_one_debt_line(p):
    assert M.fcfe_of(p) is None


def test_net_borrowing_is_what_was_issued_less_what_was_repaid():
    assert M.net_borrowing_of({"debt_issued": 10.0, "debt_repaid": -25.0}) == -15
    assert M.net_borrowing_of({"debt_issued": 10.0}) == 10 and M.net_borrowing_of({"debt_repaid": -3.0}) == -3
    assert M.net_borrowing_of({"debt_issued": 0.0, "debt_repaid": 0.0}) == 0           # a real zero is not unknown
    assert M.net_borrowing_of({}) is None and M.net_borrowing_of(None) is None


# ---------------------------------------------------------------- the trailing twelve months
def test_the_last_four_quarters_add_up_and_margin_and_yield_cover_the_same_months():
    quarters = [per() for _ in range(5)]
    quarters[0].update(ocf=999.0, capex=-999.0, rev=999.0)       # the fifth quarter back is not part of the year
    f = M.fcfe_block(stm(quarters), TECH, 1000.0)
    assert f["basis"] == "ttm" and f["note"] is None and f["applicable"] is True
    assert f["ttm"] == 84 == 4 * (30 - 5 - 4)
    assert (f["ocf"], f["capex"], f["debt_issued"], f["debt_repaid"], f["net_borrowing"]) == (120, -20, 0, -16, -16)
    assert f["ocf"] + f["capex"] + f["debt_issued"] + f["debt_repaid"] == f["ttm"]    # signed parts that sum to the whole
    assert f["margin"] == 0.21 and f["yield"] == 0.084                                # 84 / 400 revenue, 84 / 1000 market cap


def test_the_history_is_the_fiscal_years_and_a_year_without_the_figures_is_a_gap():
    annual = [per(ocf=50.0, capex=-10.0, issued=5.0, repaid=-15.0), {"ocf": 60.0}, per(ocf=70.0, capex=-10.0, issued=0.0, repaid=-20.0)]
    f = M.fcfe_block(stm([per() for _ in range(4)], annual), TECH, 1000.0)
    assert f["history"] == [30, None, 40]


def test_without_four_quarters_it_is_the_last_fiscal_year():
    for quarters in ([], [per() for _ in range(3)]):
        f = M.fcfe_block(stm(quarters), TECH, 1000.0)
        assert f["basis"] == "fy" and f["ttm"] == 50 == 100 - 20 + 10 - 40
        assert (f["ocf"], f["capex"], f["debt_issued"], f["debt_repaid"]) == (100, -20, 10, -40)
        assert f["margin"] == 0.125 and f["yield"] == 0.05                         # 50 / 400, 50 / 1000


def test_a_quarter_missing_a_figure_sends_the_whole_sum_back_to_the_fiscal_year():
    quarters = [per() for _ in range(5)]
    del quarters[2]["capex"]                                      # the four quarters must all have it, or none are used
    f = M.fcfe_block(stm(quarters), TECH, 1000.0)
    assert f["basis"] == "fy" and f["ttm"] == 50


def test_no_figure_is_made_up_when_the_inputs_are_missing():
    f = M.fcfe_block({"annual": [{"ocf": 1.0}], "quarterly": []}, TECH, 1000.0)
    assert f["ttm"] is None and f["ocf"] is None and f["margin"] is None and f["yield"] is None
    assert f["history"] == [None] and "Needs operating cash flow" in f["note"] and f["applicable"] is True
    year = [per() for _ in range(4)]
    assert M.fcfe_block(stm(year), TECH, None)["yield"] is None            # no market cap: no yield, the rest stands
    assert M.fcfe_block(stm(year), TECH, None)["ttm"] == 84


# ---------------------------------------------------------------- who it is for
@pytest.mark.parametrize("industry", ["Diversified Banks", "Property & Casualty Insurance", "Equity REITs", "Capital Markets"])
def test_banks_insurers_and_reits_get_no_fcfe(industry):
    f = M.fcfe_block(stm([per() for _ in range(4)]), {"industry": industry}, 1000.0)
    assert f["applicable"] is False and f["ttm"] is None and f["margin"] is None and f["yield"] is None
    assert "banks, insurers and REITs" in f["note"] and f["history"] == []


@pytest.mark.parametrize("industry", ["多元化银行", "投资银行和经纪", "人寿和健康保险", "财产和意外保险", "消费金融", "金融交易所和数据",
                                      "房地产投资信托（REITs）"])
def test_financials_are_recognised_by_the_names_longbridge_gives_in_chinese(industry):
    """The quotes this app cached on 2026-10-22 carry the industry in Chinese (半导体厂商 for NVDA), so a check that
    only read English words never fired: banks and insurers got a DCF, an Altman score and an FCFE."""
    assert is_financial({"industry": industry})
    assert M.fcfe_block(stm([per() for _ in range(4)]), {"industry": industry}, 1000.0)["applicable"] is False


@pytest.mark.parametrize("industry", ["半导体厂商", "零售商", "互联网内容与信息", "通信设备", "硬件、存储及外设", "系统软件", "水和软饮料",
                                      "酿酒业", "电信服务", "Technology", "Semiconductors", "", None])
def test_other_industries_are_not_taken_for_financials_in_either_language(industry):
    assert not is_financial({"industry": industry})


# ---------------------------------------------------------------- in the analysis
def test_the_demo_company_pays_its_debt_down_and_its_cash_still_rolls_forward_exactly():
    s = DemoProvider().statements("AAPL.US")
    assert s["annual"][-1]["lt_debt"] < s["annual"][0]["lt_debt"]
    for p in s["quarterly"] + s["annual"]:
        assert p["debt_issued"] > 0 > p["debt_repaid"]
        assert p["net_cash_change"] == pytest.approx(p["ocf"] + p["capex"] + p["dividends_paid"] + p["buybacks"]
                                                     + p["debt_issued"] + p["debt_repaid"])


def test_the_analysis_carries_fcfe_and_its_parts_add_up():
    a = service.analyze(Data(), "AAPL.US")
    x = a["metrics"]["extra"]
    f = x["fcfe"]
    assert f["applicable"] and f["basis"] == "ttm" and f["net_borrowing"] < 0 and f["ttm"] > 0
    assert f["ocf"] + f["capex"] + f["debt_issued"] + f["debt_repaid"] == pytest.approx(f["ttm"], abs=1)
    assert f["margin"] < x["fcf_margin"] and f["yield"] < x["fcf_yield"]            # paying debt down leaves less than FCF
    assert f["history"][-1] == f["ttm"] and len(f["history"]) == 6                   # the demo's last fiscal year is its last four quarters
    assert any(s["field"] == "Free cash flow to equity" and "debt repaid" in s["formula"] for s in a["sources"])
    json.dumps(a)


# ---------------------------------------------------------------- the page
def cards(tmp_path, response):
    """The metric cards by label, from running the real app.js under Node."""
    (tmp_path / "response.json").write_text(json.dumps(response), encoding="utf-8")
    done = subprocess.run([NODE, str(ROOT / "tests" / "cards.js"), str(ROOT / "web" / "app.js"), str(tmp_path / "response.json")],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return {c["label"]: c for c in json.loads(done.stdout)["cards"]}


@pytest.fixture(scope="module")
def analysis():
    return service.analyze(Data(), "AAPL.US")


def with_fcfe(a, **changes):
    out = json.loads(json.dumps(a))
    out["metrics"]["extra"]["fcfe"].update(changes)
    return out


@needs_node
def test_the_card_shows_the_figure_and_how_it_is_made_up(tmp_path, analysis):
    c = cards(tmp_path, with_fcfe(analysis, ocf=146.7e9, capex=-10.0e9, debt_issued=0, debt_repaid=-17.3e9, net_borrowing=-17.3e9,
                                  ttm=119.4e9, history=[105.6e9, 111.3e9, 89.7e9, 102.8e9, 90.3e9]))
    card = c["Free cash flow to equity"]
    assert card["value"] == "119.40B" and card["cls"] == "good"
    assert "trailing 12 months: operating CF 146.70B − capex 10.00B − debt paid down 17.30B" in card["sub"]
    assert "debt issued 0, repaid 17.30B" in card["sub"] and "history: 105.60B 111.30B 89.70B 102.80B 90.30B" in card["sub"]
    assert card["sub"].count("\n") == 2                                              # the build-up, the gross flows, the history
    assert c["FCFE margin"]["value"].endswith("%") and "FCFE / revenue" in c["FCFE margin"]["sub"]
    assert c["FCFE yield"]["value"].endswith("%") and "FCFE / market cap" in c["FCFE yield"]["sub"]


@needs_node
def test_the_card_says_borrowing_when_the_company_borrowed_and_nothing_when_it_did_not(tmp_path, analysis):
    up = cards(tmp_path, with_fcfe(analysis, ocf=134.4e9, capex=-7.4e9, debt_issued=24.9e9, debt_repaid=0, net_borrowing=24.9e9, ttm=151.9e9))
    assert "operating CF 134.40B − capex 7.40B + net borrowing 24.90B" in up["Free cash flow to equity"]["sub"]
    flat = cards(tmp_path, with_fcfe(analysis, ocf=100e9, capex=-20e9, debt_issued=5e9, debt_repaid=-5e9, net_borrowing=0, ttm=80e9))
    assert "− capex 20.00B no net borrowing" in flat["Free cash flow to equity"]["sub"]


@needs_node
def test_a_negative_figure_is_red_and_the_fiscal_year_basis_is_shown(tmp_path, analysis):
    card = cards(tmp_path, with_fcfe(analysis, ttm=-11.6e9, basis="fy"))["Free cash flow to equity"]
    assert card["cls"] == "bad" and card["value"] == "-11.60B" and "last fiscal year: operating CF" in card["sub"]


@needs_node
def test_a_company_it_does_not_fit_shows_n_a_and_why(tmp_path, analysis):
    note = "Not meaningful for banks, insurers and REITs, whose debt is part of the business rather than a way of funding it."
    c = cards(tmp_path, with_fcfe(analysis, applicable=False, ttm=None, basis=None, ocf=None, capex=None, debt_issued=None,
                                  debt_repaid=None, net_borrowing=None, margin=None, history=[], note=note, **{"yield": None}))
    card = c["Free cash flow to equity"]
    assert card["value"] == "n/a" and card["cls"] == "" and card["sub"] == note
    assert c["FCFE margin"]["value"] == "n/a" and c["FCFE yield"]["value"] == "n/a"

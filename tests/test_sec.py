import os

os.environ["DATA_MODE"] = "demo"

from fastapi.testclient import TestClient

import app.data as D
from app import config, sec, service
from app.data import Data
from app.main import app

FILING = """<html><body>
<ix:header><div>hidden XBRL junk 99% of customers revenue accounted for</div></ix:header>
<p>Table of Contents</p><p>Summary of Risk Factors</p><p>Item 1A. Risk Factors</p>
<p>PART I</p><p>Item 1. Business</p>
<p>Summary of Risk Factors</p>
<ul><li>We depend on a small number of customers for a significant portion of our revenue.</li>
<li>Our supply chain depends on a single foundry in one geographic region.</li><li>Short.</li></ul>
<p>Item 1A. Risk Factors</p>
<p>Direct sales to one distributor accounted for 42% of our net revenue for the fiscal year. Our customers are many.
Sales to our top five end customers represented approximately 45% of net revenue. The weather was 30% cloudier.</p>
<p>Direct sales to one distributor accounted for 42% of our net revenue for the fiscal year.</p>
</body></html>"""


def test_text_skips_the_hidden_header_and_keeps_one_line_per_block():
    lines = sec.to_lines(FILING)
    assert not any("hidden XBRL" in ln for ln in lines) and "Summary of Risk Factors" in lines


def test_the_risk_summary_is_the_bullets_not_the_table_of_contents_entry():
    got = sec.risk_summary(sec.to_lines(FILING))
    assert got == ["We depend on a small number of customers for a significant portion of our revenue.",
                   "Our supply chain depends on a single foundry in one geographic region."]


def test_concentration_sentences_need_a_customer_a_percentage_and_a_revenue_base_and_are_not_repeated():
    got = sec.concentration(sec.to_lines(FILING))
    assert got == ["Direct sales to one distributor accounted for 42% of our net revenue for the fiscal year.",
                   "Sales to our top five end customers represented approximately 45% of net revenue."]


def test_the_newest_quarterly_report_is_used_only_if_filed_after_the_annual_one():
    recent = {"form": ["10-Q", "8-K", "10-K", "10-Q"], "filingDate": ["2026-09-10", "2026-08-01", "2025-12-18", "2025-09-10"],
              "accessionNumber": ["a", "b", "c", "d"], "primaryDocument": ["q.htm", "e.htm", "k.htm", "q0.htm"]}
    annual, quarter = sec.pick_filings(recent)
    assert annual["doc"] == "k.htm" and quarter["doc"] == "q.htm"
    recent["filingDate"][0] = "2025-11-01"                       # the "10-Q" is older than the 10-K
    assert sec.pick_filings(recent)[1] is None


def test_filing_text_says_why_it_is_missing(monkeypatch):
    assert service.get_filing_text(Data(), "AAPL.US")["status"] == "demo"
    d = Data()
    monkeypatch.setattr(D.Data, "mode", property(lambda self: "cli"))
    assert service.get_filing_text(d, "700.HK")["status"] == "unsupported"
    monkeypatch.setattr(config, "SEC_USER_AGENT", "")
    r = service.get_filing_text(d, "AAPL.US")
    assert r["status"] == "not_configured" and "SEC_USER_AGENT" in r["message"]
    monkeypatch.setattr(config, "SEC_USER_AGENT", "Test User test@example.com")
    monkeypatch.setattr(d.p, "sec_disclosures", lambda s: {"risk_summary": ["x"], "concentration": [], "filings": []}, raising=False)
    assert service.get_filing_text(d, "AAPL.US") == {"symbol": "AAPL.US", "status": "ok", "risk_summary": ["x"], "concentration": [], "filings": []}


def test_the_filings_endpoint_exists():
    r = TestClient(app).get("/api/filings", params={"symbol": "AAPL.US"})
    assert r.status_code == 200 and r.json()["status"] == "demo"

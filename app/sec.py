"""Reads a US company's own filings from SEC EDGAR for the risks the numbers cannot show.

From the latest annual report (10-K) it takes the "Summary of Risk Factors" bullets, and from that report and any newer
10-Q it takes the sentences that put a percentage on customers (customer concentration). Everything is returned as
verbatim text with a link to the filing: nothing is scored or paraphrased.

The SEC refuses requests that do not identify who is asking, so this needs SEC_USER_AGENT (your name and email) in .env.
"""
import json
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

from . import config

ANNUAL = ("10-K", "10-K405", "20-F", "40-F")
QUARTERLY = ("10-Q",)
MAX_BYTES = 20_000_000
TICKER_MAP_AGE = 7 * 86400
BLOCKS = {"p", "div", "br", "tr", "li", "table", "h1", "h2", "h3", "h4", "h5", "h6", "section", "ul", "ol"}
CUSTOMER = re.compile(r"\b(customers?|distributors?|resellers?|channel partners?|OEMs?)\b", re.I)
PERCENT = re.compile(r"\b\d{1,3}(?:\.\d+)?\s?%")
BASIS = re.compile(r"\b(revenues?|sales|receivables?)\b", re.I)
VERB = re.compile(r"\b(accounted for|account for|represented|represent|comprised|made up|constituted|contributed)\b", re.I)


class DataError(Exception):
    pass


class _Text(HTMLParser):
    """HTML to plain text with one line per block. The hidden inline-XBRL header, scripts and styles are skipped."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "ix:header"):
            self.skip += 1
        elif tag in BLOCKS:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "ix:header"):
            self.skip = max(0, self.skip - 1)
        elif tag in BLOCKS:
            self.out.append("\n")
        elif tag in ("td", "th"):
            self.out.append(" ")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def to_lines(html: str) -> list[str]:
    p = _Text()
    p.feed(html)
    lines = (re.sub(r"[ \t\r\f\v\xa0​]+", " ", ln).strip() for ln in "".join(p.out).split("\n"))
    return [ln for ln in lines if ln]


def risk_summary(lines: list[str], limit: int = 12) -> list[str]:
    """The bullets of the filing's "Summary of Risk Factors". The table of contents names it too, so the occurrence
    with the most text after it, before the business description starts, is the one used."""
    stop = re.compile(r"^(part\s+i\b|item\s+1[a-b]?\b|item\s+1\.)", re.I)
    best: list[str] = []
    for i, ln in enumerate(lines):
        if re.fullmatch(r"(summary of (the )?(principal |material |key )?risk factors|risk factor summary)\.?", ln, re.I):
            block = []
            for nxt in lines[i + 1:i + 60]:
                if stop.match(nxt):
                    break
                if 40 <= len(nxt) <= 500:
                    block.append(nxt)
            if len(block) > len(best):
                best = block
    return best[:limit]


def concentration(lines: list[str], limit: int = 6) -> list[str]:
    """Sentences that say a customer or group of customers is a stated share of revenue or receivables."""
    found, seen = [], set()
    for ln in lines:
        for s in re.split(r"(?<=[.;])\s+(?=[A-Z])", ln):
            s = s.strip()
            if not 40 <= len(s) <= 600 or not (CUSTOMER.search(s) and PERCENT.search(s) and BASIS.search(s) and VERB.search(s)):
                continue
            key = re.sub(r"\W+", "", s.lower())[:120]
            if key not in seen:
                seen.add(key)
                found.append(s)
    # sentences naming a share of one's own revenue first, boilerplate about "our customers" in general last
    found.sort(key=lambda s: (0 if re.search(r"(?:accounted for|represented|comprised)\D{0,80}\d", s, re.I) else 1))
    return found[:limit]


def _get(client, url: str):
    """One GET. A 429 is the SEC's per-IP rate limit (10 requests a second, shared with anything else on this machine
    that talks to EDGAR, such as the insider-trades lookup); a short wait clears a brief breach, but once the SEC
    blocks the address it stays blocked for around ten minutes."""
    for attempt in range(3):
        try:
            r = client.get(url)
        except Exception as e:  # noqa: BLE001
            raise DataError(f"SEC EDGAR could not be reached: {e}")
        if r.status_code != 429 or attempt == 2:
            break
        time.sleep(2 + 3 * attempt)
    if r.status_code == 429:
        raise DataError("the SEC's rate limit is blocking this computer's address for now (it counts every request to "
                        "EDGAR, including the insider-trades lookup). It usually clears in about ten minutes; then press "
                        "Refresh data")
    if r.status_code == 403:
        raise DataError("SEC EDGAR refused the request (HTTP 403); check that SEC_USER_AGENT in .env is your name and email")
    if r.status_code != 200:
        raise DataError(f"SEC EDGAR returned HTTP {r.status_code} for {url.rsplit('/', 1)[-1]}")
    if len(r.content) > MAX_BYTES:
        raise DataError("the filing is too large to read")
    return r


def _cik(client, symbol: str) -> int:
    """Ticker to CIK, from the SEC's list, which is kept in the data folder for a week."""
    ticker = symbol.rsplit(".", 1)[0].upper().replace(".", "-")
    path = Path(config.DATA_DIR) / "sec_tickers.json"
    table = None
    try:
        if path.exists() and time.time() - path.stat().st_mtime < TICKER_MAP_AGE:
            table = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        table = None
    if table is None:
        raw = _get(client, "https://www.sec.gov/files/company_tickers.json").json()
        table = {v["ticker"].upper(): v["cik_str"] for v in raw.values()}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(table), encoding="utf-8")
        except OSError:
            pass
    if ticker not in table:
        raise DataError(f"{ticker} is not in the SEC's company list")
    return int(table[ticker])


def pick_filings(recent: dict) -> tuple[Optional[dict], Optional[dict]]:
    """The latest annual report, and the latest 10-Q if it was filed after it. `recent` is the submissions table."""
    rows = [dict(form=f, filed=d, acc=a, doc=p) for f, d, a, p in
            zip(recent["form"], recent["filingDate"], recent["accessionNumber"], recent["primaryDocument"])]
    annual = next((r for r in rows if r["form"] in ANNUAL), None)
    quarter = next((r for r in rows if r["form"] in QUARTERLY), None)
    if quarter and annual and quarter["filed"] <= annual["filed"]:
        quarter = None
    return annual, quarter


def disclosures(symbol: str) -> Optional[dict]:
    """Risk-factor summary and customer-concentration sentences from the latest filings, or None when the company
    files no annual report with the SEC. Raises DataError when EDGAR cannot be read."""
    import httpx
    ua = config.SEC_USER_AGENT
    if not ua:
        raise DataError("SEC_USER_AGENT is not set")
    with httpx.Client(headers={"User-Agent": ua, "Accept-Encoding": "gzip, deflate"}, timeout=60, follow_redirects=True) as c:
        cik = _cik(c, symbol)
        subs = _get(c, f"https://data.sec.gov/submissions/CIK{cik:010d}.json").json()
        annual, quarter = pick_filings(subs["filings"]["recent"])
        if annual is None:
            return None
        out = {"risk_summary": [], "concentration": [], "filings": []}
        for kind, f in (("annual", annual), ("quarter", quarter)):
            if f is None:
                continue
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{f['acc'].replace('-', '')}/{f['doc']}"
            lines = to_lines(_get(c, url).text)
            out["filings"].append({"form": f["form"], "filed": f["filed"], "url": url})
            if kind == "annual":
                out["risk_summary"] = risk_summary(lines)
            out["concentration"] += [{"text": s, "form": f["form"], "filed": f["filed"], "url": url}
                                     for s in concentration(lines)]
        return out

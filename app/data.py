"""Data layer: runs the Longbridge CLI (or the demo provider), parses it, and caches results.

Every provider method returns normalized data or raises DataError. `Data` wraps a provider, records
warnings for anything that failed (so the UI can show "n/a: reason"), and caches by (method, args): in memory
for a few minutes, and, per ticker, in a file in the data folder (see snapshot.py).
"""
import copy
import json
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from . import config, sec, sessions
from .demo import DemoProvider
from .normalize import finalize, merge_periods, parse_statement
from .peerstore import PeerStore
from .snapshot import MISS, Failure, Snapshot, SnapshotStore
from .util import SYMBOL_RE, norm_key, num


class DataError(Exception):
    pass


class InputError(DataError):
    """Bad user input (HTTP 400), not a data-source failure."""


def _find(obj: Any, *names: str):
    """First value whose normalized key matches one of names, searching nested dicts."""
    want = {norm_key(n) for n in names}
    stack = [obj]
    while stack:
        cur = stack.pop(0)
        if isinstance(cur, dict):
            for k, v in cur.items():
                if norm_key(k) in want and v not in (None, "", []):
                    return v
            stack.extend(v for v in cur.values() if isinstance(v, (dict, list)))
        elif isinstance(cur, list):
            stack.extend(v for v in cur[:50] if isinstance(v, (dict, list)))
    return None


def _consensus_growth(periods: list) -> dict:
    """Forward EPS growth from the consensus table, using ONE EPS basis for every number.

    Each future quarter that has an estimate is compared with the actual of the same fiscal quarter a year earlier;
    growth = sum(estimates) / sum(year-ago actuals) - 1. `ttm_eps` is the sum of the last four reported quarters on
    the same basis. Non-GAAP (`normalized_eps`) is preferred because that is what analyst estimates are written
    in; GAAP `eps` is the fallback. Mixing a non-GAAP forecast with GAAP trailing EPS badly overstates growth.
    """
    for key, label in (("normalized_eps", "normalized (non-GAAP) EPS"), ("eps", "GAAP EPS")):
        rows = {}
        for per in periods:
            try:
                fy, fp = int(per.get("fiscal_year")), int(per.get("fiscal_period"))
            except (TypeError, ValueError):
                continue
            for d in per.get("details", []):
                if d.get("key") == key:
                    rows[(fy, fp)] = (num(d.get("actual")), num(d.get("estimate")))
        reported = sorted(k for k, (a, _) in rows.items() if a is not None)
        future = sorted(k for k, (a, e) in rows.items() if a is None and e is not None)
        pairs = [(rows[k][1], rows[(k[0] - 1, k[1])][0]) for k in future
                 if (k[0] - 1, k[1]) in rows and rows[(k[0] - 1, k[1])][0] is not None][:4]
        if len(pairs) < 2:
            continue
        base = sum(b for _, b in pairs)
        if base <= 0:
            continue
        ttm = sum(rows[k][0] for k in reported[-4:]) if len(reported) >= 4 else None
        return {"growth": sum(e for e, _ in pairs) / base - 1, "basis": label, "quarters": len(pairs),
                "ttm_eps": ttm if ttm and ttm > 0 else None}
    return {}


def _pct_to_frac(x):
    """Longbridge percent fields come as 2.5 or "2.5%"; the app uses fractions (0.025)."""
    v = num(x)
    return None if v is None else v / 100


def _items(raw: Any) -> list:
    for _ in range(4):
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict):
            nxt = next((raw[k] for k in ("list", "items", "data", "articles", "news", "topics", "records",
                                         "filings", "result") if isinstance(raw.get(k), (list, dict))), None)
            if nxt is None:
                return []
            raw = nxt
    return []


def _find_cli():
    """PATH first, then LONGBRIDGE_BIN, then the Windows installer's default folder (PATH changes only reach
    new terminals, so a freshly installed CLI may not be on this process's PATH yet)."""
    import os
    from pathlib import Path
    exe = shutil.which("longbridge") or os.environ.get("LONGBRIDGE_BIN")
    if exe and Path(exe).exists():
        return exe
    la = os.environ.get("LOCALAPPDATA")
    if la:
        cand = Path(la) / "Programs" / "longbridge" / "longbridge.exe"
        if cand.exists():
            return str(cand)
    return None


class CliProvider:
    name = "cli"

    # `financial-statement` allows about two calls a second to the whole account and answers a third with code 429002
    # ("retry in 0.6 s"); measured on real peers, every third statement call failed and the retry slept for 1 to 2
    # seconds. Its calls are spaced out here, across every thread of the process, so the page's panels and the peer
    # comparison share that allowance instead of colliding. Other commands are not limited this tightly.
    STATEMENT_GAP = 0.55
    _statement_lock = threading.Lock()
    _statement_next = 0.0   # when the next statement call may start (time.monotonic)

    def __init__(self):
        self.exe = _find_cli()

    @classmethod
    def _pace_statements(cls) -> None:
        """Reserve the next free slot for a statement call and wait for it. The slot is taken under the lock but the
        wait is not, so threads queue for their own slots rather than behind each other's sleeps."""
        with cls._statement_lock:
            now = time.monotonic()
            start = max(now, cls._statement_next)
            cls._statement_next = start + cls.STATEMENT_GAP
        if start > now:
            time.sleep(start - now)

    def run(self, *args: str) -> Any:
        if not self.exe:
            raise DataError("longbridge CLI not found. Install it (see README), run `longbridge auth login`, "
                            "or set DATA_MODE=demo.")
        cmd = [self.exe, *args, "--format", "json"]
        err = ""
        for attempt in range(3):  # transient failures (rate limit, empty reply) are retried
            if args and args[0] == "financial-statement":
                self._pace_statements()
            try:
                p = subprocess.run(cmd, capture_output=True, text=True, timeout=config.CLI_TIMEOUT, encoding="utf-8")
            except subprocess.TimeoutExpired:
                raise DataError(f"`longbridge {' '.join(args)}` timed out")
            if p.returncode != 0:
                msg = (p.stderr or p.stdout).strip()
                low = msg.lower()
                if "not logged in" in low or "unauthorized" in low or "not authenticated" in low:
                    raise DataError(msg + "  -> run `longbridge auth login`")
                err = msg or f"longbridge {args[0]} failed"
                if "sec edgar" in low:
                    raise DataError(err)  # SEC throttling lasts far longer than a retry delay
                if any(w in low for w in ("too many requests", "rate limit", "frequen", "429")) and attempt < 2:
                    time.sleep(1.0 + attempt)
                    continue
                raise DataError(err)
            try:
                return json.loads(p.stdout)
            except json.JSONDecodeError:
                err = f"`longbridge {args[0]}` returned non-JSON output"
                if attempt < 2:
                    time.sleep(0.7)
                    continue
        raise DataError(err)

    def sec_disclosures(self, symbol):
        """Risk-factor summary and customer-concentration sentences from the latest 10-K and 10-Q (SEC EDGAR)."""
        try:
            return sec.disclosures(symbol)
        except sec.DataError as e:
            raise DataError(str(e))

    # --- statements -------------------------------------------------------------------------------
    def statements(self, symbol):
        # `financial-statement` returns full IS/BS/CF; `financial-report` returns only a summary on US accounts.
        out = {}
        for key, rep in (("annual", "af"), ("quarterly", "qf")):
            parts = []
            for kind in ("BS", "IS", "CF"):
                try:
                    raw = self.run("financial-statement", symbol, "--kind", kind, "--report", rep)
                    if isinstance(raw, dict) and raw.get("currency") and not out.get("currency"):
                        out["currency"] = raw["currency"]
                    parts.append(parse_statement(raw))
                except DataError:
                    if kind == "BS" and key == "annual":
                        raise
                    parts.append([])
            out[key] = [finalize(p) for p in merge_periods(*[p for p in parts if p])]
        if not out.get("annual"):
            raise DataError("no annual statements parsed; run `python -m app.probe SYMBOL` and extend "
                            "app/normalize.py ALIASES")
        self._add_shares(symbol, out)
        return out

    def _add_shares(self, symbol, stm):
        """The statements carry no share count, but the consensus table gives each reported quarter's net income
        and EPS, so shares = net income / EPS (diluted, GAAP). A quarter takes its own figure; a fiscal year takes
        the mean of its quarters (at least 3). Failure here only leaves shares unset."""
        try:
            cons = self.run("consensus", symbol)
        except DataError:
            return
        per_q = {}
        for per in _items(cons.get("list") if isinstance(cons, dict) else cons):
            if not isinstance(per, dict):
                continue
            try:
                key = (int(per["fiscal_year"]), int(per["fiscal_period"]))
            except (KeyError, TypeError, ValueError):
                continue
            vals = {d.get("key"): num(d.get("actual")) for d in per.get("details", []) if isinstance(d, dict)}
            ni, eps = vals.get("net_income"), vals.get("eps")
            if ni and eps and ni / eps > 0:
                per_q[key] = ni / eps
        for p in stm.get("quarterly", []):
            if p.get("shares") is None and (p.get("fy"), p.get("fp")) in per_q:
                p["shares"] = per_q[(p["fy"], p["fp"])]
        for p in stm.get("annual", []):
            qs = [per_q[(p["fy"], k)] for k in (1, 2, 3, 4) if p.get("fy") is not None and (p["fy"], k) in per_q]
            if p.get("shares") is None and len(qs) >= 3:
                p["shares"] = sum(qs) / len(qs)

    KLINE_MAX = 1000  # the API rejects more than 1000 candles per request (code 301607)

    @staticmethod
    def _bars(raw, symbol) -> list:
        out = []
        for b in _items(raw):
            if not isinstance(b, dict):
                continue
            t = _find(b, "time", "timestamp", "date", "t")
            row = {"t": sessions.trading_date(symbol, t), "o": num(_find(b, "open", "o")), "h": num(_find(b, "high", "h")),
                   "l": num(_find(b, "low", "l")), "c": num(_find(b, "close", "c")),
                   "v": num(_find(b, "volume", "v"))}
            if None not in row.values():
                out.append(row)
        return out

    def kline(self, symbol, count):
        """Daily bars, forward-adjusted so splits and dividends do not distort EMAs or the profile.
        Over 1000 bars are fetched as the latest 1000 plus older date-range chunks."""
        from datetime import date, timedelta
        bars = self._bars(self.run("kline", symbol, "--period", "day", "--adjust", "forward",
                                   "--count", str(min(count, self.KLINE_MAX))), symbol)
        for _ in range(6):  # each chunk reaches ~900 calendar days further back
            if len(bars) >= count or not bars:
                break
            oldest = date.fromisoformat(min(b["t"] for b in bars))
            end, start = oldest - timedelta(days=1), oldest - timedelta(days=900)
            older = self._bars(self.run("kline", "history", symbol, "--period", "day", "--adjust", "forward",
                                        "--start", start.isoformat(), "--end", end.isoformat()), symbol)
            if not older:
                break  # reached the start of the stock's history
            have = {b["t"] for b in bars}
            bars += [b for b in older if b["t"] not in have]
        bars.sort(key=lambda x: x["t"])
        if not bars:
            raise DataError("kline returned no usable bars")
        return bars[-count:]

    def _industry(self, symbol, prof):
        try:
            name = (self._rating(symbol).get("analyst") or {}).get("industry_name")
        except DataError:
            name = None
        return name or _find(prof, "industry", "sector", "industryname") or ""

    @staticmethod
    def _extended(q) -> Optional[dict]:
        """The most recent pre-market / post-market / overnight print in a quote, or None when it is over 18h old."""
        best, best_t = None, None
        for key, label in (("pre_market", "pre-market"), ("post_market", "post-market"), ("overnight", "overnight")):
            sec = q.get(key) if isinstance(q, dict) else None
            px, ts = num((sec or {}).get("last")), (sec or {}).get("timestamp")
            try:
                t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            except ValueError:
                continue
            if px and (best_t is None or t > best_t):
                best, best_t = {"price": px, "session": label, "timestamp": str(ts)}, t
        if best_t is None or (datetime.now(timezone.utc) - best_t).total_seconds() > 18 * 3600:
            return None
        return best

    def quote(self, symbol):
        q = self.run("quote", symbol)
        q = q[0] if isinstance(q, list) and q else q
        ci = {}
        try:
            ci = self.run("calc-index", symbol, "--fields", "pe,pb,dps_rate,mktcap")
            ci = ci[0] if isinstance(ci, list) and ci else ci
        except DataError:
            pass
        price = num(_find(q, "last", "lastdone", "price", "last_done"))
        if price is None:
            raise DataError("quote returned no price")
        prof = {}
        try:
            prof = self.run("company", symbol)
        except DataError:
            pass
        mcap = num(_find(ci, "mktcap", "totalmarketvalue", "marketcap"))
        return {
            "symbol": symbol, "name": prof.get("name") or prof.get("company_name") or symbol,
            "currency": _find(q, "currency") or "", "price": price, "extended": self._extended(q),
            "industry": self._industry(symbol, prof),
            "market_cap": mcap, "shares": None,  # not derived from mcap/price: that would make the market-cap check circular
            "pe": num(_find(ci, "pe", "pettm")), "pb": num(_find(ci, "pb")), "ps": None,
            "dividend_yield": _pct_to_frac(_find(ci, "dpsrate", "dividendyield")),
            "high52": num(_find(q, "fiftytwoweekhigh", "high52w", "yearhigh")),
            "low52": num(_find(q, "fiftytwoweeklow", "low52w", "yearlow")),
            "beta": num(_find(ci, "beta")),
        }

    def _rating(self, symbol):
        """institution-rating, memoised per symbol (used for ratings and for the industry name)."""
        if not hasattr(self, "_rating_cache"):
            self._rating_cache = {}
        if symbol not in self._rating_cache:
            self._rating_cache[symbol] = self.run("institution-rating", symbol)
        return self._rating_cache[symbol]

    def analyst(self, symbol):
        rt = self._rating(symbol)
        ev = (rt.get("instratings") or {}).get("evaluate") or {}
        buy = (num(ev.get("strong_buy")) or 0) + (num(ev.get("buy")) or 0)
        sell = (num(ev.get("sell")) or 0) + (num(ev.get("under")) or 0)
        target = num((rt.get("instratings") or {}).get("target"))
        fwd = fwd90 = None
        surprises = []
        try:  # forecast-eps is a time series of the consensus mean EPS (current fiscal year estimate)
            items = [i for i in _items(self.run("forecast-eps", symbol)) if isinstance(i, dict)]
            items.sort(key=lambda i: num(i.get("forecast_start_date")) or 0)
            if items:
                fwd = num(items[-1].get("forecast_eps_mean"))
                t90 = (num(items[-1].get("forecast_start_date")) or 0) - 90 * 86400
                older = [i for i in items if (num(i.get("forecast_start_date")) or 0) <= t90]
                fwd90 = num(older[-1].get("forecast_eps_mean")) if older else None
        except DataError:
            pass
        growth = {}
        try:  # consensus: per quarter, GAAP `eps` and non-GAAP `normalized_eps`, each with actual and estimate
            cons = self.run("consensus", symbol)
            periods = [p for p in _items(cons.get("list") if isinstance(cons, dict) else cons) if isinstance(p, dict)]
            for per in periods:
                for d in per.get("details", []):
                    if d.get("key") == "eps" and num(d.get("actual")) is not None and num(d.get("estimate")):
                        surprises.append((num(d["actual"]) - num(d["estimate"])) / abs(num(d["estimate"])))
            growth = _consensus_growth(periods)
        except DataError:
            pass
        return {"buy": buy, "hold": num(ev.get("hold")), "sell": sell, "target_mean": target,
                "eps_growth_forward": growth.get("growth"), "eps_growth_basis": growth.get("basis"),
                "eps_growth_quarters": growth.get("quarters"), "eps_ttm_basis": growth.get("ttm_eps"),
                "eps_forward": fwd, "eps_forward_90d": fwd90, "surprises": surprises[:4]}

    def dividends(self, symbol):
        items = _items(self.run("dividend", symbol))
        years = sorted({str(_find(i, "exdate", "date", "year") or "")[:4] for i in items if isinstance(i, dict)})
        return {"years_growth": None, "records": len(items), "years": [y for y in years if y]}

    INSIDER_COUNT = 100  # Form 4 filings requested; 40 only reached back ~9 months for an active seller

    def insider(self, symbol):
        if symbol.rsplit(".", 1)[-1].upper() != "US":
            return []  # Form 4 is US-only
        try:
            rows = _items(self.run("insider-trades", symbol, "--count", str(self.INSIDER_COUNT)))
        except DataError as e:
            if "no transactions" in str(e).lower():
                return []
            raise
        out = []
        dates = [str(i.get("date") or "")[:10] for i in rows if isinstance(i, dict) and i.get("date")]
        oldest, truncated = (min(dates) if dates else None), len(rows) >= self.INSIDER_COUNT
        for i in rows:
            if not isinstance(i, dict):
                continue
            kind = str(_find(i, "transactiontype", "type", "side", "transactioncode") or "").upper()
            if kind in ("BUY", "P"):
                typ = "buy"
            elif kind in ("SELL", "S"):
                typ = "sell"
            else:
                continue  # GRANT / DISP / TAX / EXERCISE / GIFT are not open-market signals
            out.append({"date": str(_find(i, "date", "transactiondate", "filingdate") or "")[:10],
                        "name": i.get("owner") or _find(i, "name", "insider") or "", "type": typ,
                        "shares": num(_find(i, "shares", "quantity")), "value": num(_find(i, "value", "amount")),
                        "feed_oldest": oldest, "feed_truncated": truncated})
        return out

    def ownership(self, symbol):
        # The default sort is by change, largest increase first, which would make every holder look like a buyer.
        # Sort by holdings instead so the list is the biggest holders whatever they did last quarter.
        raw = self.run("shareholder", symbol, "--sort", "owned", "--order", "desc", "--count", "20")
        holders, dates = [], []
        for h in (raw.get("shareholder_list") if isinstance(raw, dict) else _items(raw)) or []:
            if isinstance(h, dict):
                dates.append(str(h.get("report_date") or ""))
                holders.append({"name": h.get("shareholder_name") or "", "pct": num(h.get("percent_of_shares")),
                                "shares": None, "change_shares": num(h.get("shares_changed")), "change_pct": None})
        holders.sort(key=lambda x: -(x["pct"] or 0))
        return {"inst_pct": None, "change_qoq": None, "as_of": max(dates) if dates else None,
                "top_holders": holders, "sorted_by": "holdings"}

    def capital_flow(self, symbol):
        """`capital` gives the latest session's inflow/outflow by order size (no multi-day history)."""
        raw = self.run("capital", symbol)
        cin, cout = raw.get("capital_in") or {}, raw.get("capital_out") or {}
        if not cin and not cout:
            return []
        net = {k: (num(cin.get(k)) or 0) - (num(cout.get(k)) or 0) for k in ("large", "medium", "small")}
        return [{"date": str(raw.get("timestamp") or "")[:10], **net}]

    def peers(self, symbol):
        return []  # Magic Formula peer ranks are optional; shown as n/a without peers

    def industry_valuation(self, symbol):
        """Where the stock's P/E, P/B and P/S sit among the companies of its industry (`industry-valuation dist`): the
        industry median and range, and the stock's rank among the companies that have a positive value. A stock outside
        that range (a loss, or a multiple above every peer) comes with no rank, only `outside`: "above" or "below".
        Not `valuation`: its `industry_median` counts loss-makers too (0.78 for AAPL's P/E, where this gives 20.7)."""
        raw = self.run("industry-valuation", "dist", symbol)
        out = {}
        for key in ("pe", "pb", "ps"):
            d = raw.get(key) if isinstance(raw, dict) else None
            median = num(d.get("median")) if isinstance(d, dict) else None
            if median is None:
                continue
            rank, total = num(d.get("rank_index")), num(d.get("rank_total"))
            ranked = bool(rank and total and 1 <= rank <= total)
            flag = str(d.get("ranking") or "")   # a share (0 to 1), or "UpperLimitExceeded" / "LowerLimitExceeded"
            out[key] = {"median": median, "low": num(d.get("low")), "high": num(d.get("high")), "value": num(d.get("value")),
                        "rank": int(rank) if ranked else None, "total": int(total) if ranked else None,
                        "outside": "above" if flag == "UpperLimitExceeded" else "below" if flag == "LowerLimitExceeded" else None}
        if not out:
            raise DataError(f"{symbol} has no industry valuation (an ETF or fund, or a stock Longbridge does not group)")
        return out

    def industry_members(self, symbol):
        """The companies in the stock's industry with their market caps, largest first: the `symbols` of `valuation`,
        which lists the whole industry (43 companies for AAPL, the stock itself included). Its figures are not used,
        only the list. Symbols that do not look like CODE.MARKET are dropped, since each one later becomes an argument
        to the CLI."""
        raw = self.run("valuation", symbol)
        rows = raw.get("symbols") if isinstance(raw, dict) else None
        out = []
        for sym, v in (rows.items() if isinstance(rows, dict) else ()):
            cap = num(v.get("market_cap")) if isinstance(v, dict) else None
            if cap and cap > 0 and SYMBOL_RE.match(str(sym)):
                out.append({"symbol": str(sym), "name": str(v.get("name") or ""), "market_cap": cap})
        if not out:
            raise DataError(f"{symbol} has no list of the companies in its industry (an ETF or fund, or a stock "
                            f"Longbridge does not group)")
        return sorted(out, key=lambda x: -x["market_cap"])

    def news(self, symbol, limit):
        out = []
        for a in _items(self.run("news", symbol, "--count", str(max(limit * 2, 20)))):
            if not isinstance(a, dict):
                continue
            title = _find(a, "title", "headline")
            if not title:
                continue
            out.append({"id": str(_find(a, "id", "newsid") or ""), "title": str(title),
                        "publisher": _find(a, "source", "publisher", "author") or "Longbridge",
                        "published_at": str(_find(a, "publishedat", "publishtime", "time", "createdat", "date") or ""),
                        "url": _find(a, "url", "link", "detailurl") or "",
                        "summary": _find(a, "summary", "description") or "", "language": ""})
        return out

    def news_detail(self, nid):
        raw = self.run("news", "detail", nid)
        txt = _find(raw, "content", "body", "text", "summary") or ""
        return str(txt)

    def filings(self, symbol):
        out = []
        for f in _items(self.run("filing", symbol, "--count", "8")):
            if isinstance(f, dict):
                title = str(f.get("title") or "")
                m = re.search(r"\|\s*([A-Za-z0-9/-]+)\s+-", title)
                urls = f.get("file_urls") or []
                out.append({"type": m.group(1) if m else title[:40],
                            "filed_at": str(f.get("publish_at") or "")[:10], "title": title,
                            "url": urls[0] if urls else ""})
        return out

    def topics(self, symbol):
        items = _items(self.run("topic", symbol, "--count", "20"))
        seen, latest = set(), []
        for t in items:
            if isinstance(t, dict) and t.get("id") not in seen:
                seen.add(t.get("id"))
                latest.append({"title": _find(t, "title", "name", "description") or "",
                               "url": _find(t, "url", "link") or ""})
        return {"posts": len(seen) or len(items), "volume_vs_30d": None, "latest": latest[:5]}

    def market_temp(self, symbol):
        market = symbol.rsplit(".", 1)[-1].upper()
        market = {"SH": "CN", "SZ": "CN"}.get(market, market)
        if market not in ("HK", "US", "CN", "SG"):
            return None
        raw = self.run("market-temp", market)
        for row in _items(raw):
            if isinstance(row, dict) and str(row.get("field", "")).lower() == "temperature":
                return num(row.get("value"))
        return num(_find(raw, "temperature"))

    # --- options -------------------------------------------------------------------------------------
    def option_expiries(self, symbol):
        raw = self.run("option", "chain", symbol)
        return sorted({str(x["expiry_date"]) for x in _items(raw) if isinstance(x, dict) and x.get("expiry_date")})

    def option_chain(self, symbol, expiry):
        return _items(self.run("option", "chain", symbol, "--date", expiry))

    def option_quotes(self, occs):
        out = {}
        for x in _items(self.run("option", "quote", *occs)):
            if isinstance(x, dict) and x.get("symbol"):
                out[x["symbol"]] = {"last": num(x.get("last")), "iv": num(x.get("implied_volatility")),
                                    "volume": num(x.get("volume")), "open_interest": num(x.get("open_interest")),
                                    "timestamp": x.get("timestamp"), "hv": num(x.get("historical_volatility"))}
        return out

    def option_volume_daily(self, symbol, count):
        raw = self.run("option", "volume", "daily", symbol, "--count", str(count))
        return (raw.get("stats") if isinstance(raw, dict) else _items(raw)) or []

    def earnings_date(self, symbol):
        """Next earnings date (YYYY-MM-DD) within 150 days, or None."""
        from datetime import date, timedelta
        today = date.today()
        raw = self.run("finance-calendar", "report", "--symbol", symbol, "--start", today.isoformat(),
                       "--end", (today + timedelta(days=150)).isoformat())
        for day in (raw.get("list") if isinstance(raw, dict) else []) or []:
            for info in day.get("infos", []):
                if info.get("symbol") == symbol and "earning" in str(info.get("content", "")).lower():
                    m = re.match(r"(\d{4})\.(\d{2})\.(\d{2})", str(info.get("date", "")))
                    if m and "-".join(m.groups()) >= today.isoformat():
                        return "-".join(m.groups())
        return None

    def short_interest(self, symbol):
        if symbol.rsplit(".", 1)[-1].upper() not in ("US", "HK"):
            return None
        raw = self.run("short-positions", symbol, "--count", "4")
        rows = [r for r in (raw.get("data") if isinstance(raw, dict) else _items(raw)) or [] if isinstance(r, dict)]
        if not rows:
            return None
        latest = max(rows, key=lambda r: num(r.get("timestamp")) or 0)
        rate = num(latest.get("rate"))  # a fraction of float (0.0088 = 0.88%)
        return None if rate is None else round(rate * 100, 2)


class Data:
    """Provider wrapper: caching + warnings."""

    def __init__(self, provider=None, store: Optional[SnapshotStore] = None, peer_store: Optional[PeerStore] = None):
        if provider is None:
            provider = DemoProvider() if config.DATA_MODE == "demo" else CliProvider()
        self.p = provider
        self.cache: dict[tuple, tuple[float, tuple, float]] = {}  # key -> (time, ("ok", value) | ("err", msg), ttl)
        self.warnings: list[str] = []
        self._locks: dict[tuple, threading.Lock] = {}
        self._glock = threading.Lock()
        self.store = store                      # the per-ticker disk cache, or None
        self.snap: Optional[Snapshot] = None    # this request's view of the ticker's cache file, set by session()
        # what was computed for companies used as peers, kept for days (peerstore.py); in memory unless one is given
        self.peer_store = peer_store if peer_store is not None else PeerStore(None, config.PEER_CACHE_SECONDS)

    @property
    def mode(self) -> str:
        return self.p.name

    # --- per-ticker disk cache -----------------------------------------------------------------------------
    def session(self, symbol: str) -> "Data":
        """This request's view of the data for one ticker: it reads from, and records into, the ticker's cache file.
        The provider, the in-memory cache, the locks and the warnings are shared with the parent, so panels loading at
        the same time still share one fetch per call. Without a disk cache this is the Data itself."""
        if self.store is None:
            return self
        view = copy.copy(self)   # a shallow copy: every shared member above is the same object
        view.snap = self.store.open(symbol)
        return view

    def save_snapshot(self) -> None:
        """Merge what this request fetched live into the ticker's cache file."""
        if self.store is not None and self.snap is not None:
            self.store.save(self.snap)

    def refresh(self, symbol: str) -> None:
        """Forget everything held for the ticker, in memory and on disk, so the next request fetches live."""
        with self._glock:
            for key in [k for k in self.cache if symbol in k[1:]]:
                del self.cache[key]
        if self.store is not None:
            self.store.discard(symbol)
        self.peer_store.discard(symbol)   # and the figures kept for it as somebody else's peer

    def cache_info(self) -> Optional[dict]:
        """Where this request's data came from, for the page; None when there is no disk cache."""
        return None if self.store is None or self.snap is None else self.store.describe(self.snap)

    def fetched_at(self) -> str:
        """When the data was fetched: the cache file's time if the file supplied any of it, otherwise now."""
        t = self.snap.stamp if self.snap is not None and self.snap.from_cache else datetime.now(timezone.utc)
        return t.astimezone(timezone.utc).isoformat(timespec="seconds")

    def was_cached(self, name: str, *args) -> bool:
        """True when this call was answered from the ticker's cache file instead of being fetched."""
        return self.snap is not None and (name, *args) in self.snap.served

    def _memory_ttl(self) -> float:
        """How long the in-memory cache keeps a value. It follows the disk cache's lifetime, so lowering
        DISK_CACHE_MINUTES (or turning it off) is never undercut by an older copy in memory. Live data with no disk
        cache is kept only briefly, enough for concurrent panels to share a fetch."""
        if self.store is not None:
            return min(config.MEMORY_TTL, self.store.ttl)
        return config.MEMORY_TTL if self.p.name == "demo" else config.DEDUPE_TTL

    def _lock(self, key: tuple) -> threading.Lock:
        with self._glock:
            return self._locks.setdefault(key, threading.Lock())

    def _warn(self, name: str, err: str, quiet: bool) -> None:
        msg = f"{name}: {err}"
        if not quiet and msg not in self.warnings:
            self.warnings.append(msg)

    def call(self, name: str, *args, optional=True, default=None, quiet=False):
        """Cached provider call. The ticker's cache file answers first; a call it does not hold is fetched and added to
        it. Concurrent identical calls share one fetch (the page asks for the same data from several endpoints at
        once), and a failure is cached briefly so a rate-limited source is not retried by every panel in turn.
        Failures are never written to the cache file."""
        key = (name, *args)
        if self.snap is not None:
            val = self.snap.get(key)
            if val is not MISS:
                if isinstance(val, Failure):   # a harmless failure remembered from the fetch that made the file
                    if not optional:
                        raise DataError(val.message)
                    self._warn(name, val.message, quiet)
                    return default
                return val
        with self._lock(key):
            hit = self.cache.get(key)
            if hit and time.time() - hit[0] < hit[2]:
                kind, val = hit[1]
                if kind == "ok":
                    return val
                if not optional:
                    raise DataError(val)
                self._warn(name, val, quiet)
                return default
            try:
                val = getattr(self.p, name)(*args)
            except DataError as e:
                self.cache[key] = (time.time(), ("err", str(e)), config.FAIL_TTL)
                if self.snap is not None and quiet:
                    self.snap.add_failure(key, str(e))   # expected and silent, so not worth trying again on every search
                if not optional:
                    raise
                self._warn(name, str(e), quiet)
                return default
            if self.snap is not None:
                self.snap.add(key, val)   # copied here, before any caller can change `val` in place
            self.cache[key] = (time.time(), ("ok", val), self._memory_ttl())
            return val


_instance: Data | None = None


def get_data() -> Data:
    global _instance
    if _instance is None:
        # Demo data is synthetic and free to regenerate, so only live data is worth keeping on disk.
        disk = config.DATA_MODE != "demo" and config.DISK_CACHE_SECONDS > 0
        peers_on_disk = config.DATA_MODE != "demo" and config.PEER_CACHE_SECONDS > 0
        _instance = Data(store=SnapshotStore(config.DATA_DIR, config.DISK_CACHE_SECONDS) if disk else None,
                         peer_store=PeerStore(config.DATA_DIR / "peers.json" if peers_on_disk else None,
                                              config.PEER_CACHE_SECONDS))
    return _instance

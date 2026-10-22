"""Deterministic synthetic data (DATA_MODE=demo) so the app can be tried without a Longbridge login.

Statements are generated quarter by quarter with exact roll-forwards (retained earnings, cash), so the
reconciliation gate passes the same way it would on clean real data. None of this is real market data.
"""
import functools
import hashlib
from datetime import date, timedelta

import numpy as np


def _rng(symbol: str) -> np.random.Generator:
    return np.random.default_rng(int(hashlib.sha256(symbol.encode()).hexdigest()[:8], 16))


def _statements(symbol: str):
    rng = _rng(symbol + "fin")
    rev0 = float(rng.uniform(8e9, 80e9)) / 4
    growth_y = float(rng.uniform(0.02, 0.14))
    gm = float(rng.uniform(0.28, 0.62))
    opm = gm - float(rng.uniform(0.10, 0.22))
    shares = float(rng.uniform(4e8, 3e9))
    cash, debt_lt, debt_st = rev0 * 1.5, rev0 * 1.2, rev0 * 0.2
    re_, paid_in = rev0 * 5, rev0 * 3
    ppe, other_nc = rev0 * 3, rev0 * 6
    other_liab = rev0 * 1.5
    quarters = []
    nq = 24
    for i in range(nq):
        rev = rev0 * (1 + growth_y) ** (i / 4) * (1 + rng.normal(0, 0.015))
        gp = rev * (gm + rng.normal(0, 0.004))
        da = rev * 0.035
        opi = rev * (opm + rng.normal(0, 0.006))
        interest = (debt_lt + debt_st) * 0.045 / 4
        pretax = opi - interest
        tax = max(pretax, 0) * 0.19
        ni = pretax - tax
        sga = gp - opi - da * 0.3
        ocf = ni + da + rev * rng.normal(0.004, 0.01)
        capex = -rev * 0.05
        div = ni * 0.22
        buyback = ni * 0.18
        shares *= 1 - 0.0025
        ar, inv = rev * 0.45, rev * 0.18
        other_ca = rev * 0.2
        issued, repaid = debt_lt * 0.01, -debt_lt * 0.02   # a slow pay-down: 2% of the long-term debt repaid, 1% borrowed anew
        d_debt = issued + repaid
        debt_lt += d_debt
        d_cash = ocf + capex - div - buyback + d_debt
        cash += d_cash
        re_ += ni - div
        paid_in -= buyback
        ppe += -capex - da
        equity = paid_in + re_
        total_liab = debt_lt + debt_st + other_liab
        total_assets = equity + total_liab
        cur_assets = cash + ar + inv + other_ca
        other_nc_plug = total_assets - cur_assets - ppe
        quarters.append({
            "period": f"Q{i % 4 + 1}-Y{i // 4 + 1}",
            "revenue": rev, "gross_profit": gp, "operating_income": opi, "net_income": ni,
            "eps": ni / shares, "interest_expense": interest, "pretax_income": pretax, "tax": tax,
            "da": da, "sga": sga, "shares": shares,
            "total_assets": total_assets, "current_assets": cur_assets,
            "current_liabilities": rev * 0.9, "cash": cash, "receivables": ar, "inventory": inv,
            "other_current_assets": other_ca, "ppe": ppe, "other_noncurrent": other_nc_plug,
            "total_liabilities": total_liab, "st_debt": debt_st, "lt_debt": debt_lt,
            "other_liabilities": other_liab, "equity": equity, "retained_earnings": re_,
            "ocf": ocf, "capex": capex, "dividends_paid": -div, "buybacks": -buyback,
            "debt_issued": issued, "debt_repaid": repaid, "net_cash_change": d_cash,
        })
    flow = ["revenue", "gross_profit", "operating_income", "net_income", "eps", "interest_expense",
            "pretax_income", "tax", "da", "sga", "ocf", "capex", "dividends_paid", "buybacks",
            "debt_issued", "debt_repaid", "net_cash_change"]
    annual = []
    for y in range(nq // 4):
        grp = quarters[y * 4:(y + 1) * 4]
        a = dict(grp[-1])
        a["period"] = f"FY{y + 1}"
        for k in flow:
            a[k] = sum(q[k] for q in grp)
        annual.append(a)
    return {"annual": annual, "quarterly": quarters[-8:]}


def _kline(symbol: str, count: int):
    return list(_kline_cached(symbol, count))  # bars are read-only elsewhere; the cache avoids regenerating them


@functools.lru_cache(maxsize=32)
def _kline_cached(symbol: str, count: int):
    rng = _rng(symbol + "px")
    px = float(rng.uniform(30, 400))
    out, d = [], date.today() - timedelta(days=int(count * 1.45))
    drift = float(rng.uniform(-0.0001, 0.0009))
    while len(out) < count:
        d += timedelta(days=1)
        if d.weekday() >= 5:
            continue
        o = px
        c = o * (1 + rng.normal(drift, 0.016))
        h = max(o, c) * (1 + abs(rng.normal(0, 0.006)))
        lo = min(o, c) * (1 - abs(rng.normal(0, 0.006)))
        v = float(rng.uniform(0.6, 1.6)) * 3e7 * (1 + 8 * abs(c / o - 1))
        out.append({"t": d.isoformat(), "o": o, "h": h, "l": lo, "c": c, "v": v})
        px = c
    return out


class DemoProvider:
    name = "demo"

    def __init__(self):
        self.warnings: list[str] = []

    def statements(self, symbol):
        return _statements(symbol)

    def kline(self, symbol, count):
        return _kline(symbol, count)

    def quote(self, symbol):
        st = _statements(symbol)
        k = _kline(symbol, 1500)
        last = st["annual"][-1]
        px = k[-1]["c"]
        ttm_eps = sum(q["eps"] for q in st["quarterly"][-4:])
        return {
            "symbol": symbol, "name": symbol.split(".")[0] + " (demo)", "currency": "USD", "industry": "Technology",
            "price": px, "shares": last["shares"], "market_cap": px * last["shares"],
            "pe": px / ttm_eps if ttm_eps else None,
            "pb": px * last["shares"] / last["equity"],
            "ps": px * last["shares"] / last["revenue"],
            "dividend_yield": -last["dividends_paid"] / (px * last["shares"]),
            "high52": max(x["h"] for x in k[-252:]), "low52": min(x["l"] for x in k[-252:]),
            "beta": float(_rng(symbol + "b").uniform(0.7, 1.5)),
        }

    def analyst(self, symbol):
        rng = _rng(symbol + "an")
        px = _kline(symbol, 1500)[-1]["c"]
        return {
            "buy": int(rng.integers(8, 30)), "hold": int(rng.integers(3, 15)), "sell": int(rng.integers(0, 6)),
            "target_mean": px * float(rng.uniform(0.9, 1.3)),
            "eps_growth_forward": float(rng.uniform(0.04, 0.20)),
            "eps_forward": None, "eps_forward_90d": None,
            "surprises": [float(x) for x in rng.normal(0.03, 0.04, 4)],
        }

    def dividends(self, symbol):
        return {"years_growth": int(_rng(symbol + "dv").integers(0, 15))}

    def sec_disclosures(self, symbol):
        return None   # synthetic data has no filings

    def insider(self, symbol):
        rng = _rng(symbol + "in")
        out, d = [], date.today()
        for i in range(int(rng.integers(3, 12))):
            out.append({"date": (d - timedelta(days=int(rng.integers(1, 360)))).isoformat(),
                        "name": f"Insider {i + 1}", "type": "buy" if rng.random() < 0.4 else "sell",
                        "shares": int(rng.integers(500, 50000)), "value": float(rng.uniform(5e4, 5e6))})
        return out

    def ownership(self, symbol):
        rng = _rng(symbol + "ow")
        return {"inst_pct": float(rng.uniform(0.45, 0.85)), "change_qoq": float(rng.normal(0.003, 0.01)),
                "as_of": (date.today() - timedelta(days=60)).isoformat(),
                "top_holders": [{"name": n, "shares": int(rng.integers(1e7, 9e7)),
                                 "change_pct": float(rng.normal(0.01, 0.05))}
                                for n in ("Vanguard Group", "BlackRock", "State Street", "Fidelity", "T. Rowe Price")]}

    def capital_flow(self, symbol):
        rng = _rng(symbol + "cf")
        d = date.today()
        return [{"date": (d - timedelta(days=30 - i)).isoformat(), "large": float(rng.normal(2e6, 2e7)),
                 "medium": float(rng.normal(0, 1e7)), "small": float(rng.normal(-1e6, 8e6))} for i in range(30)]

    def peers(self, symbol):
        rng = _rng(symbol + "pe")
        return [{"symbol": f"PEER{i}.US", "earnings_yield": float(rng.uniform(0.02, 0.09)),
                 "roc": float(rng.uniform(0.05, 0.35))} for i in range(8)]

    def industry_valuation(self, symbol):
        """A made-up industry of 25 companies centred near the stock's own multiples: the median and range, and the
        stock's rank among them, in the shape the CLI provider returns."""
        rng = _rng(symbol + "iv")
        q = self.quote(symbol)
        out = {}
        for key in ("pe", "pb", "ps"):
            own = q[key]
            ok = own is not None and own > 0
            centre = float(own) * float(rng.uniform(0.6, 1.5)) if ok else float(rng.uniform(8, 25))
            peers = [float(centre * np.exp(rng.normal(0, 0.5))) for _ in range(24)]
            every = sorted(peers + [float(own)]) if ok else sorted(peers)
            out[key] = {"median": float(np.median(every)), "low": every[0], "high": every[-1],
                        "value": None if own is None else float(own),
                        "rank": 1 + sum(1 for v in peers if v < own) if ok else None, "total": len(every) if ok else None,
                        "outside": "below" if own is not None and own <= 0 else None}
        return out

    def industry_members(self, symbol):
        """A made-up industry: the stock and 14 other companies, their sizes spread around its own."""
        rng = _rng(symbol + "im")
        own = float(self.quote(symbol)["market_cap"])
        out = [{"symbol": symbol, "name": symbol.split(".")[0] + " (demo)", "market_cap": own}]
        for i in range(14):
            out.append({"symbol": f"PEER{chr(65 + i)}.US", "name": f"Peer {chr(65 + i)} (demo)",
                        "market_cap": float(own * np.exp(rng.normal(0, 1.2)))})
        return sorted(out, key=lambda x: -x["market_cap"])

    def news(self, symbol, limit):
        d = date.today()
        base = symbol.split(".")[0]
        heads = ["announces quarterly results", "raises full-year outlook", "faces analyst downgrade",
                 "expands into new market", "names new chief financial officer", "announces share buyback",
                 "supplier agreement renewed", "regulator opens inquiry", "launches new product line",
                 "investor day scheduled", "dividend declared", "patent ruling issued"]
        return [{"id": f"demo{i}", "title": f"{base} {heads[i % len(heads)]} (demo item)", "publisher": "Demo Wire",
                 "published_at": (d - timedelta(days=i * 2)).isoformat(), "url": "https://example.com/demo",
                 "summary": "Synthetic placeholder text generated for demo mode.", "language": "en"}
                for i in range(limit)]

    def filings(self, symbol):
        d = date.today()
        return [{"type": t, "filed_at": (d - timedelta(days=x)).isoformat(), "url": "https://example.com/filing"}
                for t, x in (("8-K", 12), ("10-Q", 40), ("8-K", 70), ("10-K", 160), ("8-K", 200))]

    def topics(self, symbol):
        return {"posts": 38, "volume_vs_30d": 1.2,
                "latest": [{"title": "Demo community post", "url": "https://example.com/post"}]}

    def market_temp(self, symbol):
        return 58.0

    # --- options (synthetic chain priced with Black-Scholes; a smile and a term structure) ----------------
    def _opt_params(self, symbol):
        rng = _rng(symbol + "opt")
        return {"base": float(rng.uniform(0.22, 0.42)), "skew": float(rng.uniform(0.15, 0.4)),
                "term": float(rng.uniform(-0.06, 0.03))}

    def _iv(self, symbol, spot, strike, days):
        p = self._opt_params(symbol)
        m = strike / spot - 1
        return max(0.08, p["base"] + p["term"] * (days / 365 - 0.1) * 4 - p["skew"] * m + 1.2 * m * m)

    def option_expiries(self, symbol):
        d, out = date.today(), []
        while len(out) < 26:
            d += timedelta(days=1)
            if d.weekday() == 4:
                out.append(d.isoformat())
        return out

    def option_chain(self, symbol, expiry):
        from .options import bs_price, dte, years
        spot = _kline(symbol, 1500)[-1]["c"]
        step = 5.0 if spot > 150 else 2.5 if spot > 50 else 1.0
        rng = _rng(symbol + expiry)
        days = dte(expiry)
        rows = []
        k = round(spot * 0.6 / step) * step
        while k <= spot * 1.4:
            iv = self._iv(symbol, spot, k, days)
            cp = bs_price(spot, k, years(days), 0.043, 0.005, iv, "C")
            pp = bs_price(spot, k, years(days), 0.043, 0.005, iv, "P")
            near = max(0.0, 1 - abs(k / spot - 1) * 4)
            rows.append({"strike": str(k), "call_last": f"{max(cp, 0.01):.2f}", "call_iv": f"{iv:.3f}",
                         "call_vol": str(int(near * rng.integers(5, 400))), "put_last": f"{max(pp, 0.01):.2f}",
                         "put_iv": f"{iv:.3f}", "put_vol": str(int(near * rng.integers(5, 400))), "standard": "true"})
            k += step
        return rows

    def option_quotes(self, occs):
        import re
        from .options import bs_price, dte, years
        out = {}
        for s in occs:
            m = re.match(r"^([A-Z]+)(\d{6})([CP])(\d+)\.(\w+)$", s)
            if not m:
                continue
            t, ymd, kind, k, mk = m.groups()
            expiry = f"20{ymd[:2]}-{ymd[2:4]}-{ymd[4:]}"
            strike = int(k) / 1000
            sym = f"{t}.{mk}"
            spot = _kline(sym, 1500)[-1]["c"]
            days = dte(expiry)
            iv = self._iv(sym, spot, strike, days)
            out[s] = {"last": round(max(bs_price(spot, strike, years(days), 0.043, 0.005, iv, kind), 0.01), 2),
                      "iv": round(iv, 3), "volume": 50, "open_interest": 500,
                      "timestamp": date.today().isoformat() + "T20:00:00Z", "hv": 0.2}
        return out

    def option_volume_daily(self, symbol, count):
        rng = _rng(symbol + "ov")
        d = date.today()
        return [{"date": (d - timedelta(days=count - i)).isoformat(),
                 "put_call_volume_ratio": f"{float(rng.uniform(0.5, 1.2)):.3f}",
                 "put_call_open_interest_ratio": f"{float(rng.uniform(0.6, 1.1)):.3f}",
                 "total_volume": str(int(rng.integers(5e5, 2e6)))} for i in range(count)]

    def earnings_date(self, symbol):
        return (date.today() + timedelta(days=35)).isoformat()

    def short_interest(self, symbol):
        return None

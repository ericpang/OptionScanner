"""Turns raw Longbridge CLI JSON into the canonical statement dicts the calculators use.

The CLI's exact JSON layout was not available when this was written (it needs a logged-in account), so this
module is deliberately tolerant: it accepts either "one object per period" or "one row per line item"
layouts and matches field names through the alias table below. If a field you need shows up as n/a, run
`python -m app.probe AAPL.US` to see the real JSON and add the label to ALIASES.
"""
from typing import Any, Optional

from .util import norm_key, num

# Longbridge `financial-statement` returns one object per period: {fp_end, report_txt, fields: [{field, value, ...}]}.
# Each line item has a stable `field` code. A canonical key may be the SUM of several codes (e.g. debt).
FIELD_MAP: dict[str, list[str]] = {
    # income statement
    "revenue": ["total_rev"], "cogs": ["cogs"], "gross_profit": ["gp"], "operating_income": ["oper_inc"],
    "sga": ["sga"], "pretax_income": ["ebt"], "tax": ["inc_tax"], "net_income": ["ni"],
    "interest_expense": ["interest_exp"],
    # balance sheet
    "total_assets": ["total_assets"], "current_assets": ["total_ca"], "cash": ["cash_st_invest"],
    "cash_equiv": ["cash_equiv"], "receivables": ["total_receiv"], "inventory": ["inventory"],
    "other_current_assets": ["other_ca"], "ppe": ["nppe"], "total_liabilities": ["total_liab"],
    "current_liabilities": ["total_cl"], "total_ncl": ["total_non_cl"],
    "st_debt": ["st_debt", "current_port"],          # short-term borrowings + current portion of LT debt/leases
    "lt_debt": ["lt_debt", "capital_leases"],        # long-term debt + long-term leases
    "equity": ["total_equity"], "retained_earnings": ["re"],
    # The same debt lines kept apart, for the Two-Stage filter, which counts borrowings as debt and treats lease liabilities
    # separately. `current_port` is the parent of current_port_debt + current_port_leases (AMZN: 18.26B = 5.29B + 12.97B).
    "borrow_st": ["st_debt"], "borrow_cp": ["current_port_debt"], "borrow_lt": ["lt_debt"],
    "lease_cp": ["current_port_leases"], "lease_lt": ["capital_leases"], "cp_total": ["current_port"],
    # cash flow
    "ocf": ["cash_oper"], "da": ["da_cf"], "capex": ["capex"], "dividends_paid": ["total_div_paid_cf"],
    "buybacks": ["common_rep"], "net_cash_change": ["net_change"], "sbc": ["stock_based_cf"],
    # the totals only: their short- and long-term lines are sometimes empty while the total is filled (AAPL's quarters)
    "debt_issued": ["total_debt_issued"], "debt_repaid": ["total_debt_repaid"],
}
_FIELD_TO_CANON: dict[str, list[str]] = {}
for _canon, _codes in FIELD_MAP.items():
    for _c in _codes:
        _FIELD_TO_CANON.setdefault(_c, []).append(_canon)


def _parse_field_periods(raw: list) -> list[dict]:
    """Parse the `list` of period objects described above; returns oldest-first canonical period dicts."""
    out = []
    for per in raw:
        p: dict = {"period": str(per.get("fp_end") or per.get("report_txt") or "")}
        try:  # fiscal year and fiscal period (1-4) as Longbridge labels them; the consensus table uses the same
            p["fy"], p["fp"] = int(per["ff_year"]), int(per["ff_period"])
        except (KeyError, TypeError, ValueError):
            pass
        acc: dict[str, list[float]] = {}
        for f in per.get("fields") or []:
            code, v = f.get("field"), num(f.get("value"))
            if code and v is not None:
                for canon in _FIELD_TO_CANON.get(code, []):
                    acc.setdefault(canon, []).append(v)
        for canon, vs in acc.items():
            p[canon] = sum(vs)
        out.append(p)
    out.sort(key=lambda x: x["period"])
    return out


ALIASES: dict[str, list[str]] = {
    "revenue": ["revenue", "totalrevenue", "operatingrevenue", "sales", "netsales", "totaloperatingrevenue"],
    "gross_profit": ["grossprofit", "grossincome"],
    "cogs": ["costofrevenue", "costofgoodssold", "cogs", "operatingcost", "costofsales"],
    "operating_income": ["operatingincome", "operatingprofit", "ebit", "incomefromoperations"],
    "net_income": ["netincome", "netprofit", "netincomeattributable", "profitattributabletoshareholders",
                   "netincomecommonstockholders"],
    "eps": ["eps", "dilutedeps", "epsdiluted", "basiceps", "earningspershare"],
    "interest_expense": ["interestexpense", "financecost", "financecosts", "interestexpensenonoperating"],
    "pretax_income": ["pretaxincome", "incomebeforetax", "profitbeforetax", "incomebeforeincometaxes"],
    "tax": ["incometax", "taxprovision", "incometaxexpense", "taxexpense", "provisionforincometaxes"],
    "da": ["depreciationandamortization", "depreciationamortization", "da", "depreciation"],
    "sga": ["sellinggeneralandadministrative", "sga", "sellinggeneraladministrativeexpenses"],
    "shares": ["dilutedshares", "weightedaveragedilutedshares", "sharesoutstanding", "totalshares",
               "weightedaveragesharesdiluted", "ordinarysharesnumber"],
    "total_assets": ["totalassets"],
    "current_assets": ["totalcurrentassets", "currentassets"],
    "current_liabilities": ["totalcurrentliabilities", "currentliabilities"],
    "cash": ["cashandcashequivalents", "cash", "cashandequivalents", "cashcashequivalents"],
    "receivables": ["accountsreceivable", "receivables", "netreceivables", "accountsreceivablenet"],
    "inventory": ["inventory", "inventories"],
    "other_current_assets": ["othercurrentassets"],
    "ppe": ["netppe", "propertyplantandequipment", "ppe", "fixedassets", "netpropertyplantequipment"],
    "total_liabilities": ["totalliabilities", "totalliabilitiesnetminorityinterest"],
    "st_debt": ["shorttermdebt", "currentdebt", "shorttermborrowings", "currentportionoflongtermdebt",
                "shorttermloans"],
    "lt_debt": ["longtermdebt", "longtermborrowings", "noncurrentdebt", "longtermloans", "bondspayable"],
    "other_liabilities": ["otherliabilities", "othernoncurrentliabilities"],
    "equity": ["totalequity", "shareholdersequity", "totalshareholdersequity", "stockholdersequity",
               "totalstockholdersequity", "commonstockequity"],
    "retained_earnings": ["retainedearnings", "retainedprofit", "retainedearningsaccumulateddeficit"],
    "ocf": ["operatingcashflow", "netcashfromoperatingactivities", "cashfromoperatingactivities",
            "netcashprovidedbyoperatingactivities", "netoperatingcashflow", "cashflowfromoperations"],
    "capex": ["capitalexpenditure", "capex", "purchaseofppe", "purchaseofpropertyplantandequipment",
              "purchaseofpropertyandequipment", "paymentsforacquisitionofppe"],
    "dividends_paid": ["dividendspaid", "cashdividendspaid", "commonstockdividendpaid", "paymentofdividends"],
    "buybacks": ["repurchaseofcapitalstock", "stockrepurchase", "buybacks", "repurchaseofcommonstock",
                 "paymentsforrepurchaseofcommonstock", "commonstockpayments"],
    "net_cash_change": ["netchangeincash", "changeincash", "netincreaseincash", "netincreaseindecreaseincash",
                        "changeincashandcashequivalents"],
    "debt_issued": ["totaldebtissued", "debtissued", "issuanceofdebt", "proceedsfromissuanceofdebt"],
    "debt_repaid": ["totaldebtrepaid", "debtrepaid", "repaymentofdebt", "repaymentsofdebt"],
}

_LOOKUP = {norm_key(a): canon for canon, al in ALIASES.items() for a in al}
_NAME_KEYS = ("name", "label", "title", "item", "account", "key", "field", "indicator")
_VALUE_KEYS = ("values", "data", "periods", "series", "list", "items", "history", "vals")
_DATE_KEYS = ("period", "date", "enddate", "periodend", "fiscalperiod", "reportdate", "year", "fiscaldate",
              "reportperiod", "asof")


def unwrap(raw: Any) -> Any:
    """Peel common wrapper keys ({"data": ...}, {"list": ...})."""
    for _ in range(4):
        if isinstance(raw, dict) and len(raw) <= 4:
            for k in ("data", "list", "items", "result", "statements", "reports"):
                if k in raw and isinstance(raw[k], (list, dict)):
                    raw = raw[k]
                    break
            else:
                return raw
        else:
            return raw
    return raw


def _date_of(d: dict) -> Optional[str]:
    for k, v in d.items():
        if norm_key(k) in _DATE_KEYS and v not in (None, ""):
            return str(v)
    return None


def _flatten_period(d: dict, out: dict) -> None:
    for k, v in d.items():
        if isinstance(v, dict):
            _flatten_period(v, out)
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            for row in v:
                _row_into(row, out)
        else:
            canon = _LOOKUP.get(norm_key(k))
            n = num(v)
            if canon and n is not None and canon not in out:
                out[canon] = n


def _row_into(row: dict, out: dict) -> None:
    label = next((row[k] for k in row if norm_key(k) in _NAME_KEYS and isinstance(row[k], str)), None)
    if label:
        canon = _LOOKUP.get(norm_key(label))
        if canon:
            v = next((row[k] for k in ("value", "val", "amount") if k in row), None)
            n = num(v)
            if n is not None and canon not in out:
                out[canon] = n
    for v in row.values():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            for sub in v:
                _row_into(sub, out)


def _periods_from_rows(rows: list[dict], headers: Optional[list]) -> list[dict]:
    series: dict[str, list] = {}
    for row in rows:
        label = next((row[k] for k in row if norm_key(k) in _NAME_KEYS and isinstance(row[k], str)), None)
        canon = _LOOKUP.get(norm_key(label)) if label else None
        vs = next((row[k] for k in row if norm_key(k) in _VALUE_KEYS and isinstance(row[k], list)), None)
        if canon and vs is not None and canon not in series:
            series[canon] = [num(x.get("value") if isinstance(x, dict) else x) for x in vs]
    n = max((len(s) for s in series.values()), default=0)
    periods = [{} for _ in range(n)]
    for canon, s in series.items():
        for i, v in enumerate(s):
            if v is not None:
                periods[i][canon] = v
    for i, p in enumerate(periods):
        p["period"] = str(headers[i]) if headers and i < len(headers) else f"P{i}"
    return periods


def parse_statement(raw: Any) -> list[dict]:
    """Return period dicts (each with canonical keys + 'period'), oldest first."""
    raw = unwrap(raw)
    periods: list[dict] = []
    if isinstance(raw, list) and raw and isinstance(raw[0], dict) and "fields" in raw[0] and "fp_end" in raw[0]:
        return _parse_field_periods(raw)
    if isinstance(raw, dict):
        headers = next((v for k, v in raw.items() if norm_key(k) in ("periods", "dates", "headers", "columns")
                        and isinstance(v, list)), None)
        rows = next((v for k, v in raw.items() if isinstance(v, list) and v and isinstance(v[0], dict)
                     and any(norm_key(k2) in _NAME_KEYS for k2 in v[0])), None)
        if rows is not None:
            periods = _periods_from_rows(rows, headers)
        else:
            raw = [raw]
    if isinstance(raw, list) and not periods:
        if raw and isinstance(raw[0], dict) and any(norm_key(k) in _NAME_KEYS for k in raw[0]) \
                and any(norm_key(k) in _VALUE_KEYS for k in raw[0]):
            periods = _periods_from_rows(raw, None)
        else:
            for item in raw:
                if isinstance(item, dict):
                    p: dict = {}
                    _flatten_period(item, p)
                    p["period"] = _date_of(item) or f"P{len(periods)}"
                    if len(p) > 1:
                        periods.append(p)
    labels = [p["period"] for p in periods]
    if labels == sorted(labels, reverse=True) and labels != sorted(labels):
        periods.reverse()  # newest-first -> oldest-first
    return periods


def merge_periods(*statements: list[dict]) -> list[dict]:
    """Merge BS / IS / CF period lists. Aligns on period label when shared, else from the newest end."""
    if not statements:
        return []
    n = max(len(s) for s in statements)
    merged = [{} for _ in range(n)]
    label_maps = [{p["period"]: p for p in s} for s in statements]
    shared = bool(label_maps) and all(
        set(label_maps[0]) & set(m) for m in label_maps[1:]) and len(statements) > 1
    if shared:
        keys = sorted(set().union(*[set(m) for m in label_maps]))
        out = []
        for k in keys:
            p: dict = {"period": k}
            for m in label_maps:
                p.update({a: b for a, b in m.get(k, {}).items() if a != "period"})
            out.append(p)
        return out
    for s in statements:
        off = n - len(s)
        for i, p in enumerate(s):
            merged[off + i].update({a: b for a, b in p.items() if a != "period" or "period" not in merged[off + i]})
    return merged


def finalize(p: dict) -> dict:
    """Fill derivable fields (never invents data that is not derivable)."""
    if p.get("gross_profit") is None and p.get("revenue") is not None and p.get("cogs") is not None:
        p["gross_profit"] = p["revenue"] - p["cogs"]
    if p.get("capex") is not None:
        p["capex"] = -abs(p["capex"])
    for k in ("dividends_paid", "buybacks"):
        if p.get(k) is not None:
            p[k] = -abs(p[k])
    if p.get("total_liabilities") is None and p.get("total_assets") is not None and p.get("equity") is not None:
        p["total_liabilities"] = p["total_assets"] - p["equity"]
    # Longbridge omits line items that are empty. On a balance sheet that has a current-assets section (a normal
    # corporate layout, not a bank) a missing debt line means the company has none, so debt-free names such as
    # ANET get 0 instead of n/a. Banks and insurers have no such section and keep n/a.
    if p.get("current_assets") is not None and p.get("total_assets") is not None:
        for k in ("st_debt", "lt_debt", "borrow_st", "borrow_cp", "borrow_lt", "lease_cp", "lease_lt", "cp_total"):
            p.setdefault(k, 0.0)
    # The same goes for the debt a period issued or repaid: on a cash flow statement (one with operating cash flow) a
    # missing line means there was none. Without the zero a quiet quarter would stop the last four from being summed.
    if p.get("ocf") is not None:
        for k in ("debt_issued", "debt_repaid"):
            p.setdefault(k, 0.0)
    return p

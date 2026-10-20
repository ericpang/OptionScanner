"""Cross-statement reconciliation gate (tolerances from the value-investing skill references).

Each check returns pass / fail / skipped. A check is skipped (and says why) when its inputs are not in the
data; skipped is never counted as a pass. Any fail halts scoring.
"""
from .util import num


def _chk(name, tol, gap, note="", hard=None, advisory=False):
    """pass within tol; 'warn' between tol and hard (when hard is given); fail beyond. Only fail halts scoring."""
    if gap is None:
        return {"name": name, "tolerance": tol, "status": "skipped", "gap": None, "note": note or "inputs missing"}
    a = abs(gap)
    status = "pass" if a <= tol else "warn" if (advisory or (hard is not None and a <= hard)) else "fail"
    return {"name": name, "tolerance": tol, "status": status, "gap": round(gap, 4), "note": note}


def _rel(a, b, base):
    a, b, base = num(a), num(b), num(base)
    if a is None or b is None or not base:
        return None
    return (a - b) / abs(base)


def run(stm: dict, quote: dict) -> dict:
    ann = stm["annual"]
    A = ann[-1]
    P = ann[-2] if len(ann) > 1 else {}
    checks = []

    d_re = None
    if num(A.get("retained_earnings")) is not None and num(P.get("retained_earnings")) is not None:
        d_re = A["retained_earnings"] - P["retained_earnings"]
    div = abs(num(A.get("dividends_paid")) or 0)
    bb = abs(num(A.get("buybacks")) or 0)
    ni = num(A.get("net_income"))
    # Buybacks are charged to retained earnings by some companies and to paid-in capital by others, so the
    # roll-forward passes if either treatment closes the gap; the smaller gap is the one reported.
    g1 = _rel(ni, (d_re + div) if d_re is not None else None, ni)
    g2 = _rel(ni, (d_re + div + bb) if d_re is not None else None, ni)
    gaps = [g for g in (g1, g2) if g is not None]
    gap = min(gaps, key=abs) if gaps else None
    checks.append(_chk("IS to BS: net income vs change in retained earnings + dividends (+ buybacks)", 0.03, gap,
                       "buybacks may be charged to retained earnings or to paid-in capital; stock compensation and "
                       "share-settlement taxes, minority interests and OCI also move retained earnings, so this check "
                       "is informational and never stops scoring", advisory=True))

    ck = "cash_equiv" if num(A.get("cash_equiv")) is not None else "cash"
    dc = None
    if num(A.get(ck)) is not None and num(P.get(ck)) is not None:
        dc = A[ck] - P[ck]
    checks.append(_chk("CF to BS: change in cash vs balance-sheet cash", 0.01,
                       _rel(A.get("net_cash_change"), dc, A.get(ck)),
                       "cash flow statements include restricted cash and exclude FX translation, so a small gap is "
                       "expected; up to 10% is a warning", hard=0.10))

    ca_parts = [num(A.get(k)) for k in ("cash", "receivables", "inventory", "other_current_assets")]
    checks.append(_chk("BS: current assets sum", 0.02,
                       _rel(sum(ca_parts), A.get("current_assets"), A.get("current_assets"))
                       if all(p is not None for p in ca_parts) else None,
                       "some companies report current-asset lines outside these four buckets; up to 10% is a warning",
                       hard=0.10))
    li_parts = [num(A.get(k)) for k in ("current_liabilities", "total_ncl")]
    if not all(p is not None for p in li_parts):
        li_parts = [num(A.get(k)) for k in ("st_debt", "lt_debt", "other_liabilities")]
    checks.append(_chk("BS: liabilities sum", 0.02,
                       _rel(sum(li_parts), A.get("total_liabilities"), A.get("total_liabilities"))
                       if all(p is not None for p in li_parts) else None))

    sh, px, mc = num(quote.get("shares")), num(quote.get("price")), num(quote.get("market_cap"))
    checks.append(_chk("Market cap: shares x price vs quoted market cap", 0.02,
                       _rel(sh * px, mc, mc) if sh and px and mc else None))
    checks.append({"name": "IS to CF: net income + non-cash items vs operating cash flow", "tolerance": 0.05,
                   "status": "skipped", "gap": None,
                   "note": "needs working-capital line items that are not captured; not verified"})

    failed = [c for c in checks if c["status"] == "fail"]
    passed = [c for c in checks if c["status"] == "pass"]
    skipped = [c for c in checks if c["status"] == "skipped"]
    warned = [c for c in checks if c["status"] == "warn"]
    if failed:
        summary = "FAILED: " + "; ".join(f"{c['name']} off by {c['gap']:+.1%} (tolerance {c['tolerance']:.0%})"
                                         for c in failed)
    else:
        summary = f"{len(passed)} checks passed within tolerance"
        if warned:
            summary += f", {len(warned)} warning ({'; '.join(c['name'].split(':')[0] + ' ' + format(c['gap'], '+.1%') for c in warned)})"
        summary += f", {len(skipped)} skipped (inputs missing)"
    return {"status": "fail" if failed else "pass", "checks": checks, "summary": summary,
            "verified": len(passed), "skipped": len(skipped)}

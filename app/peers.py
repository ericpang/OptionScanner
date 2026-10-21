"""The peer group: the companies in a stock's industry closest to it in size, measured by this app's own metric code.

Longbridge publishes an industry median for P/E, P/B and P/S only (benchmarks.py). For margins, returns, growth and
leverage each peer's statements are run through metrics.compute, the code that measures the company itself, so a peer's
figure is defined exactly like the company's, and a peer whose statements fail the reconciliation gate is left out as
the company would be. A peer costs about a dozen CLI calls and the statements endpoint allows about two a second, so ten
peers take about 40 seconds. What is computed is kept for days (peerstore.py), and the work runs on its own request,
which the page makes as soon as the analysis is up, so that nothing else waits for it.
"""
import copy
import math
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from . import benchmarks, config, metrics as M, reconcile
from .data import Data, DataError
from .util import num

# "-R", "-WR", "-A", "-C": a share class or counter, not part of the company's name
_CLASS_MARK = re.compile(r"\s*[-–]\s*[A-Za-z]{1,3}$")


def _base_name(name: str) -> str:
    """A company's name without its share-class mark, so that its other lines compare equal to it."""
    return _CLASS_MARK.sub("", (name or "").strip()).strip().lower()


def pick(members: list, company: str, name: str, cap: Optional[float], limit: int) -> list:
    """The members to try as peers, nearest to the company in market cap first (the largest first when its cap is not
    known). Left out: the company itself, its other lines (Hong Kong lists an RMB counter beside the HKD one, and many
    companies have share classes), and any company already chosen under another line."""
    seen = {_base_name(name)} - {""}
    ranked = sorted((m for m in members if m["symbol"] != company and (num(m.get("market_cap")) or 0) > 0),
                    key=lambda m: abs(math.log(num(m["market_cap"]) / cap)) if cap and cap > 0 else -num(m["market_cap"]))
    out = []
    for m in ranked:
        base = _base_name(m.get("name"))
        if base and base in seen:
            continue
        if base:
            seen.add(base)
        out.append(m)
        if len(out) == limit:
            break
    return out


def same_currency(symbol: str, stm: dict) -> bool:
    """False when the statements are reported in another currency than the shares trade in (an ADR, say), which makes a
    figure that mixes the share price with the statements meaningless."""
    want, have = config.MARKET_CURRENCY.get(symbol.rsplit(".", 1)[-1]), stm.get("currency")
    return not (want and have and want != have)


def compute_peer(d: Data, cand: dict) -> dict:
    """What the comparison needs from one candidate, or why it cannot be used. A DataError (the data could not be
    fetched) is not caught here: that is worth another try, so it is not kept."""
    sym = cand["symbol"]
    quote = d.call("quote", sym, optional=False)
    stm = d.call("statements", sym, optional=False)
    entry = {"symbol": sym, "name": cand.get("name") or "", "market_cap": cand["market_cap"]}
    gate = reconcile.run(stm, quote)
    if gate["status"] == "fail":
        return {**entry, "status": "excluded",
                "reason": "its statements fail this app's checks: " + gate["summary"].removeprefix("FAILED: ")}
    m = M.compute(stm, quote, None, None, [], [], None)   # the figures compared need no analyst, bar or insider data
    return {**entry, "status": "ok", "values": benchmarks.peer_values(m, stm, same_currency(sym, stm))}


def gather(d: Data, candidates: list, refresh: bool = False) -> dict:
    """Measure the candidates, nearest first, until BENCH_PEERS of them are usable or the list runs out; a candidate
    that cannot be used is replaced by the next one. A peer whose figures are in the store is not fetched again, unless
    `refresh`. Returns {"usable": [...], "excluded": [...]}, the usable ones largest first."""
    view = copy.copy(d)
    view.snap = None      # a peer's raw data does not belong in the company's cache file
    view.warnings = []    # and a peer's failures are not the company's warnings

    failed = []   # candidates that could not be fetched this time

    def one(cand: dict) -> dict:
        sym = cand["symbol"]
        if not refresh:
            kept = d.peer_store.get(sym)
            if kept is not None:
                return {**kept, "name": cand.get("name") or kept.get("name", ""), "market_cap": cand["market_cap"]}
        unusable = {"symbol": sym, "name": cand.get("name") or "", "market_cap": cand["market_cap"], "status": "unavailable"}
        try:
            entry = compute_peer(view, cand)
        except DataError as e:   # without the developer's hint ("run `python -m app.probe ...`") that some messages end with
            entry = {**unusable, "reason": str(e).split("; run `")[0]}
        except Exception as e:  # noqa: BLE001 - one company's odd data must not take the whole comparison down
            entry = {**unusable, "reason": f"{type(e).__name__}: {e}"}
        if entry["status"] == "unavailable":
            failed.append(entry)
            return entry
        return d.peer_store.put(sym, entry)   # figures, or the verdict that its statements fail the checks

    usable, excluded = [], []
    queue = list(candidates)
    with ThreadPoolExecutor(max_workers=max(1, config.BENCH_WORKERS)) as pool:
        while queue and len(usable) < config.BENCH_PEERS:
            need = config.BENCH_PEERS - len(usable)
            wave, queue = queue[:need], queue[need:]
            for entry in pool.map(one, wave):
                (usable if entry["status"] == "ok" else excluded).append(entry)
    if len(usable) >= config.BENCH_MIN_PEERS:
        # The data service works, so these are the peers' own problems (SK Hynix's ADR has no statements at all). Note
        # them for a while, or every repeat search would spend seconds trying them again. When too few peers worked the
        # fault may be the service (not logged in, no network), which must not be remembered.
        for entry in failed:
            d.peer_store.put(entry["symbol"], {**entry, "keep_seconds": config.PEER_RETRY_SECONDS})
    usable.sort(key=lambda e: -e["market_cap"])
    return {"usable": usable, "excluded": excluded}

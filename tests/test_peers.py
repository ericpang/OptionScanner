import os

os.environ["DATA_MODE"] = "demo"

import collections
import copy
import json
import threading
import time
import types
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.data as D
from app import benchmarks, config, metrics as M, peers, peerstore, service
from app.data import Data, DataError
from app.demo import DemoProvider
from app.main import app
from app.peerstore import PeerStore
from app.snapshot import SnapshotStore

DAY = 86400
T0 = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


class Provider(DemoProvider):
    """The demo provider with failures on demand. `down` symbols cannot be fetched, `broken` ones come back with
    statements that fail the reconciliation gate (as DELL's and HPE's balance sheets do), `odd` ones with statements the
    code was not written for. `members` replaces the industry list."""

    def __init__(self, down=(), broken=(), odd=(), members=None):
        super().__init__()
        self.down, self.broken, self.odd, self.members = set(down), set(broken), set(odd), members
        self.calls = collections.Counter()   # statements asked for, by symbol

    def statements(self, symbol):
        self.calls[symbol] += 1
        if symbol in self.down:
            raise DataError(f"statements for {symbol} timed out")
        if symbol in self.odd:
            raise KeyError("lt_debt")
        s = super().statements(symbol)
        if symbol in self.broken:
            s["annual"][-1]["current_assets"] *= 3
        return s

    def industry_members(self, symbol):
        return self.members if self.members is not None else super().industry_members(symbol)


def mem(symbol, name, cap):
    return {"symbol": symbol, "name": name, "market_cap": cap}


def candidates(sym="AAPL.US"):
    """The demo company's candidate peers, nearest in size first: all 14 of them."""
    members = Data().call("industry_members", sym)
    me = next(m for m in members if m["symbol"] == sym)
    return peers.pick(members, sym, me["name"], me["market_cap"], config.BENCH_PEERS + config.BENCH_SPARES)


# ---------------------------------------------------------------- the figures
def test_a_companys_compared_figures_are_the_ones_on_its_cards():
    d = Data()
    a = service.analyze(d, "AAPL.US")
    vals = benchmarks.peer_values(a["metrics"], d.call("statements", "AAPL.US"))
    m, x = a["metrics"], a["metrics"]["extra"]
    assert list(vals) == list(benchmarks.PEER_METRICS) and all(v is not None for v in vals.values()), vals
    assert vals["operating_margin"] == m["operating_margin"]["ttm"] and vals["roic"] == m["roic"] and vals["roe"] == m["roe"]["latest"]
    assert vals["debt_to_ebitda"] == m["debt_to_ebitda"]["gross"] and vals["net_income_growth"] == m["net_income_growth"]["yoy"]
    assert vals["gross_margin"] == x["gross_margin"]["latest"] and vals["current_ratio"] == x["current_ratio"]
    assert vals["ev_ebitda"] == x["ev_ebitda"] and vals["net_debt_equity"] == x["net_debt_equity"]


def test_zero_or_negative_equity_leaves_out_roe_and_net_debt_to_equity():
    d = Data()
    stm, q = d.call("statements", "AAPL.US"), d.call("quote", "AAPL.US")
    ok = benchmarks.peer_values(M.compute(stm, q, None, None, [], [], None), stm)
    assert ok["roe"] is not None and ok["net_debt_equity"] is not None
    for where in ("annual", "quarterly"):           # the fiscal year's equity, or the latest balance sheet's
        for equity in (0.0, -5.0):
            bad = copy.deepcopy(stm)
            bad[where][-1]["equity"] = equity
            got = benchmarks.peer_values(M.compute(bad, q, None, None, [], [], None), bad)
            assert got["roe"] is None and got["net_debt_equity"] is None, (where, equity)
            assert got["operating_margin"] == ok["operating_margin"] and got["gross_margin"] == ok["gross_margin"]


def test_statements_in_another_currency_drop_the_figures_that_use_the_share_price():
    d = Data()
    stm, q = d.call("statements", "AAPL.US"), d.call("quote", "AAPL.US")
    m = M.compute(stm, q, None, None, [], [], None)
    ok, apart = benchmarks.peer_values(m, stm), benchmarks.peer_values(m, stm, same_currency=False)
    assert ok["ev_ebitda"] is not None and ok["fcf_yield"] is not None
    assert apart["ev_ebitda"] is None and apart["fcf_yield"] is None
    assert {k: v for k, v in apart.items() if k not in benchmarks.MIXES_PRICE} == {k: v for k, v in ok.items() if k not in benchmarks.MIXES_PRICE}
    assert peers.same_currency("CAJPY.US", {"currency": "JPY"}) is False       # an ADR of a Japanese company
    assert peers.same_currency("AAPL.US", {"currency": "USD"}) is True and peers.same_currency("AAPL.US", {}) is True
    assert peers.same_currency("X.XX", {"currency": "JPY"}) is True            # an unknown market: no claim either way


def peers_with(key, *vals):
    return [{"values": {key: v}} for v in vals]


def test_the_median_the_middle_half_and_the_companys_standing_among_peers():
    out = benchmarks.compare_peers({"operating_margin": 0.45}, peers_with("operating_margin", 0.1, 0.2, 0.3, 0.4, 0.5))
    assert list(out) == list(benchmarks.PEER_METRICS)
    b = out["operating_margin"]
    assert (b["median"], b["q1"], b["q3"], b["n"], b["company"]) == (0.3, 0.2, 0.4, 5, 0.45)
    assert b["diff"] == pytest.approx(0.15) and b["above"] == 4 and b["ratio"] == 1.5


def test_a_median_needs_enough_peers_with_the_figure():
    few = peers_with("roic", 0.1, 0.2) + [{"values": {"roic": None}}, {"values": {}}]     # two peers have it
    b = benchmarks.compare_peers({"roic": 0.3}, few)["roic"]
    assert b["n"] == 2 < config.BENCH_MIN_PEERS and b["company"] == 0.3
    assert b["median"] is None and b["q1"] is None and b["diff"] is None and b["above"] is None and b["ratio"] is None
    enough = peers_with("roic", *[0.1 * i for i in range(1, config.BENCH_MIN_PEERS + 1)])
    assert benchmarks.compare_peers({"roic": 0.3}, enough)["roic"]["median"] is not None


def test_the_gap_and_standing_need_a_company_figure_and_a_ratio_needs_positive_ones():
    growth = peers_with("fcf_growth", -0.2, 0.1, 0.4)
    none = benchmarks.compare_peers({}, growth)["fcf_growth"]
    assert none["company"] is None and none["median"] == 0.1 and none["diff"] is None and none["above"] is None
    loss = benchmarks.compare_peers({"fcf_growth": -0.5}, growth)["fcf_growth"]
    assert loss["diff"] == pytest.approx(-0.6) and loss["above"] == 0 and loss["ratio"] is None
    tie = benchmarks.compare_peers({"fcf_growth": 0.1}, growth)["fcf_growth"]
    assert tie["diff"] == 0 and tie["above"] == 1                     # only -0.2 is below it; a tie is not "above"


def test_a_direction_is_set_for_every_compared_figure_and_only_leverage_and_valuation_are_lower_is_better():
    assert list(benchmarks.HIGHER_IS_BETTER) == list(benchmarks.PEER_METRICS)
    assert {k for k, up in benchmarks.HIGHER_IS_BETTER.items() if not up} == {"debt_to_ebitda", "ev_ebitda", "net_debt_equity"}


@pytest.mark.parametrize("key", list(benchmarks.PEER_METRICS))
def test_better_and_worse_follow_each_figures_direction(key):
    up = benchmarks.HIGHER_IS_BETTER[key]
    ps = peers_with(key, 0.1, 0.2, 0.3, 0.4, 0.5)                            # a median of 0.3
    at = lambda c: benchmarks.compare_peers({key: c}, ps)[key]
    assert at(0.45)["better"] is up and at(0.45)["higher_is_better"] is up    # above the median
    assert at(0.15)["better"] is (not up)                                    # below it
    assert at(0.30)["better"] is None                                        # equal: neither
    assert benchmarks.compare_peers({}, ps)[key]["better"] is None           # no figure for the company
    assert benchmarks.compare_peers({key: 0.45}, ps[:2])[key]["better"] is None   # too few peers for a median


# ---------------------------------------------------------------- choosing the peers
def test_peers_are_the_members_nearest_in_market_cap_and_never_the_company_itself():
    members = [mem("ME.US", "Me", 100), mem("A.US", "A", 2000), mem("B.US", "B", 130), mem("C.US", "C", 60),
               mem("D.US", "D", 10), mem("E.US", "E", 90), mem("F.US", "F", 400)]
    got = peers.pick(members, "ME.US", "Me", 100, 4)
    assert [m["symbol"] for m in got] == ["E.US", "B.US", "C.US", "F.US"]    # |ln(cap / 100)|: 0.11, 0.26, 0.51, 1.39


def test_without_the_companys_cap_the_largest_come_first_and_members_without_a_cap_are_skipped():
    members = [mem("A.US", "A", 5), mem("B.US", "B", 50), mem("C.US", "C", None), mem("D.US", "D", 0), mem("ME.US", "Me", 9)]
    assert [m["symbol"] for m in peers.pick(members, "ME.US", "Me", None, 5)] == ["B.US", "A.US"]


def test_other_lines_of_the_same_company_are_not_peers():
    # Longbridge's list for 700.HK: an RMB counter beside the HKD one, and two lines each for Baidu and Kuaishou
    members = [mem("700.HK", "腾讯控股", 3.58e12), mem("80700.HK", "腾讯控股-R", 3.05e12), mem("9888.HK", "百度集团-W", 2.11e11),
               mem("89888.HK", "百度集团-WR", 1.8e11), mem("1024.HK", "快手-W", 1.21e11), mem("81024.HK", "快手-WR", 1.03e11)]
    got = peers.pick(members, "700.HK", "腾讯控股", 3.58e12, 10)
    assert [m["symbol"] for m in got] == ["9888.HK", "1024.HK"]     # 80700.HK is Tencent itself; the others count once


def test_names_compare_without_their_share_class_mark_but_keep_a_hyphen_that_belongs_to_them():
    assert peers._base_name("腾讯控股-R") == peers._base_name("腾讯控股") == "腾讯控股"
    assert peers._base_name("Alphabet-A") == peers._base_name("Alphabet-C") == "alphabet"
    assert peers._base_name("Coca-Cola") == "coca-cola" and peers._base_name("Rolls-Royce") == "rolls-royce"
    assert peers._base_name(None) == "" and peers._base_name("  ") == ""


def test_companies_with_no_name_are_never_dropped_as_duplicates_of_each_other():
    members = [mem("ME.US", "", 100), mem("A.US", "", 90), mem("B.US", "", 80)]
    assert [m["symbol"] for m in peers.pick(members, "ME.US", "", 100, 5)] == ["A.US", "B.US"]


# ---------------------------------------------------------------- the store
def test_the_store_stamps_what_it_keeps_and_hands_back_copies():
    s = PeerStore(None, 7 * DAY, Clock())
    stamped = s.put("STX.US", {"status": "ok", "values": {"roic": 0.5}})
    assert stamped["fetched_at"] == "2026-10-21T12:00:00+00:00"
    got = s.get("STX.US")
    assert got == stamped
    got["values"]["roic"] = 99
    assert s.get("STX.US")["values"]["roic"] == 0.5                   # a copy: the caller cannot change what is kept
    assert s.get("WDC.US") is None


def test_a_figure_is_fresh_just_under_its_lifetime_and_stale_at_it_and_a_future_time_is_not_trusted():
    clock = Clock()
    s = PeerStore(None, 7 * DAY, clock)
    s.put("STX.US", {"status": "ok"})
    clock.advance(seconds=7 * DAY - 1)
    assert s.get("STX.US") is not None
    clock.advance(seconds=1)
    assert s.get("STX.US") is None
    clock.advance(days=-30)                                            # the clock now reads before it was written
    assert s.get("STX.US") is None


def test_figures_survive_a_restart_and_what_has_expired_is_dropped_when_the_file_is_written(tmp_path):
    clock, path = Clock(), tmp_path / "peers.json"
    s = PeerStore(path, 7 * DAY, clock)
    s.put("OLD.US", {"status": "ok"})
    clock.advance(days=8)
    s.put("NEW.US", {"status": "ok", "values": {"roic": 0.1}})
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert list(doc["peers"]) == ["NEW.US"] and doc["version"] == peerstore.VERSION
    again = PeerStore(path, 7 * DAY, clock)                           # a restarted server
    assert again.get("NEW.US")["values"]["roic"] == 0.1 and again.get("OLD.US") is None


def test_a_note_with_its_own_shorter_lifetime_expires_by_it_and_a_junk_one_is_ignored():
    clock = Clock()
    s = PeerStore(None, 7 * DAY, clock)
    s.put("A.US", {"status": "unavailable", "keep_seconds": 3600})
    s.put("B.US", {"status": "ok", "keep_seconds": 30 * DAY})                 # longer than the store's lifetime: that wins
    s.put("C.US", {"status": "ok", "keep_seconds": "soon"})                   # junk: not fresh, and nothing raises
    clock.advance(seconds=3599)
    assert s.get("A.US") is not None
    clock.advance(seconds=1)
    assert s.get("A.US") is None and s.get("B.US") is not None and s.get("C.US") is None
    clock.advance(days=7)
    assert s.get("B.US") is None


@pytest.mark.parametrize("content", ["", "not json", "[]", '{"version": 999, "peers": {}}', '{"version": 1}',
                                     '{"version": 1, "peers": []}', '{"version": 1, "peers": {"A.US": 5}}'])
def test_an_unreadable_file_or_one_from_another_version_is_ignored_and_replaced(tmp_path, content):
    path = tmp_path / "peers.json"
    path.write_text(content, encoding="utf-8")
    s = PeerStore(path, 7 * DAY, Clock())
    assert s.get("A.US") is None
    s.put("B.US", {"status": "ok"})
    assert json.loads(path.read_text(encoding="utf-8"))["peers"].keys() == {"B.US"}


def test_a_folder_that_cannot_be_written_never_breaks_the_page(tmp_path, caplog):
    blocker = tmp_path / "data"
    blocker.write_text("a file where the folder should be")
    s = PeerStore(blocker / "peers.json", 7 * DAY, Clock())
    stamped = s.put("STX.US", {"status": "ok"})                        # nothing raises
    assert s.get("STX.US") == stamped and "could not save" in caplog.text   # and this run still has it


def test_a_lifetime_of_zero_keeps_nothing_and_no_path_never_touches_disk(tmp_path):
    never = PeerStore(tmp_path / "peers.json", 0, Clock())
    assert never.put("STX.US", {"status": "ok"})["fetched_at"] and never.get("STX.US") is None
    memory = PeerStore(None, 7 * DAY, Clock())
    memory.put("STX.US", {"status": "ok"})
    assert memory.get("STX.US") is not None and list(tmp_path.iterdir()) == []


def test_discard_forgets_one_symbol_and_rewrites_the_file(tmp_path):
    path = tmp_path / "peers.json"
    s = PeerStore(path, 7 * DAY, Clock())
    s.put("A.US", {"status": "ok"})
    s.put("B.US", {"status": "ok"})
    s.discard("A.US")
    s.discard("NOT.US")                                               # nothing to forget is not an error
    assert s.get("A.US") is None and s.get("B.US") is not None
    assert json.loads(path.read_text(encoding="utf-8"))["peers"].keys() == {"B.US"}


def test_figures_stored_by_several_threads_at_once_all_reach_the_file(tmp_path):
    path = tmp_path / "peers.json"
    s = PeerStore(path, 7 * DAY)
    syms = [f"P{i}.US" for i in range(30)]
    threads = [threading.Thread(target=s.put, args=(x, {"status": "ok", "values": {"roic": i / 100}})) for i, x in enumerate(syms)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(json.loads(path.read_text(encoding="utf-8"))["peers"]) == sorted(syms) and not list(tmp_path.glob("*.tmp"))


def test_each_data_has_its_own_memory_store_and_the_live_one_is_a_file_unless_switched_off(monkeypatch, tmp_path):
    a, b = Data(), Data()
    a.peer_store.put("STX.US", {"status": "ok"})
    assert b.peer_store.get("STX.US") is None                         # no sharing between instances
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    for mode, seconds, on_disk in (("demo", 7 * DAY, False), ("cli", 7 * DAY, True), ("cli", 0, False)):
        monkeypatch.setattr(D, "_instance", None)
        monkeypatch.setattr(config, "DATA_MODE", mode)
        monkeypatch.setattr(config, "PEER_CACHE_SECONDS", seconds)
        store = D.get_data().peer_store
        assert (store.path == tmp_path / "peers.json") is on_disk and store.ttl == seconds, (mode, seconds)
    monkeypatch.setattr(D, "_instance", None)


def test_refresh_forgets_the_figures_kept_for_the_ticker_but_not_for_other_companies():
    d = Data()
    d.peer_store.put("AAPL.US", {"status": "ok"})
    d.peer_store.put("STX.US", {"status": "ok"})
    d.refresh("AAPL.US")
    assert d.peer_store.get("AAPL.US") is None and d.peer_store.get("STX.US") is not None


# ---------------------------------------------------------------- measuring a peer
def test_the_demo_industry_lists_the_company_and_14_others_by_size():
    p = DemoProvider()
    out = p.industry_members("AAPL.US")
    assert len(out) == 15 and out == p.industry_members("AAPL.US")    # deterministic
    caps = [m["market_cap"] for m in out]
    assert caps == sorted(caps, reverse=True) and all(D.SYMBOL_RE.match(m["symbol"]) for m in out)
    assert next(m for m in out if m["symbol"] == "AAPL.US")["market_cap"] == pytest.approx(p.quote("AAPL.US")["market_cap"])
    assert json.loads(json.dumps(out)) == out


def test_a_peer_is_measured_by_the_same_code_as_the_company():
    d, cand = Data(), candidates()[0]
    entry = peers.compute_peer(d, cand)
    stm, q = d.call("statements", cand["symbol"]), d.call("quote", cand["symbol"])
    assert entry["status"] == "ok" and entry["symbol"] == cand["symbol"] and entry["market_cap"] == cand["market_cap"]
    assert entry["values"] == benchmarks.peer_values(M.compute(stm, q, None, None, [], [], None), stm)


def test_a_peer_whose_statements_fail_the_reconciliation_gate_is_excluded_not_measured():
    cand = candidates()[0]
    entry = peers.compute_peer(Data(Provider(broken=[cand["symbol"]])), cand)
    assert entry["status"] == "excluded" and "values" not in entry
    assert entry["reason"].startswith("its statements fail this app's checks: ") and "current assets sum" in entry["reason"]
    assert "FAILED" not in entry["reason"]


def test_a_peer_that_cannot_be_fetched_raises_so_that_gather_can_decide():
    cand = candidates()[0]
    with pytest.raises(DataError, match="timed out"):
        peers.compute_peer(Data(Provider(down=[cand["symbol"]])), cand)


# ---------------------------------------------------------------- gathering the group
def test_a_candidate_that_cannot_be_used_is_replaced_by_the_next_and_each_verdict_is_kept_as_long_as_it_deserves():
    cands = candidates()
    down, broken, odd = (cands[i]["symbol"] for i in (0, 1, 2))
    d = Data(Provider(down=[down], broken=[broken], odd=[odd]))
    got = peers.gather(d, cands)
    assert len(got["usable"]) == config.BENCH_PEERS
    assert [e["symbol"] for e in got["usable"]] == [c["symbol"] for c in sorted(cands[3:13], key=lambda c: -c["market_cap"])]
    status = {e["symbol"]: (e["status"], e["reason"]) for e in got["excluded"]}
    assert status[down][0] == "unavailable" and "timed out" in status[down][1]
    assert status[broken][0] == "excluded" and "reconciliation" not in status[broken][1] and "current assets sum" in status[broken][1]
    assert status[odd] == ("unavailable", "KeyError: 'lt_debt'")      # odd data is reported, not raised
    assert d.p.calls[cands[13]["symbol"]] == 0                        # enough peers: the last candidate is never fetched
    assert d.peer_store.get(broken)["status"] == "excluded" and "keep_seconds" not in d.peer_store.get(broken)   # for as long as figures
    for failed in (down, odd):                                        # a failure is noted for an hour only
        assert d.peer_store.get(failed)["status"] == "unavailable" and d.peer_store.get(failed)["keep_seconds"] == config.PEER_RETRY_SECONDS
    assert all(d.peer_store.get(e["symbol"])["status"] == "ok" for e in got["usable"])


def test_a_peer_that_cannot_be_fetched_is_not_tried_again_for_an_hour_but_then_is():
    cands, clock = candidates(), Clock()
    store, down = PeerStore(None, 7 * DAY, clock), candidates()[0]["symbol"]
    first = Provider(down=[down])
    peers.gather(Data(first, peer_store=store), cands)
    assert first.calls[down] == 1
    second = Provider(down=[down])
    got = peers.gather(Data(second, peer_store=store), cands)
    assert second.calls[down] == 0 and sum(second.calls.values()) == 0         # a repeat search spends nothing on it
    assert [e["symbol"] for e in got["excluded"]] == [down] and "timed out" in got["excluded"][0]["reason"]
    clock.advance(seconds=config.PEER_RETRY_SECONDS)
    third = Provider()
    peers.gather(Data(third, peer_store=store), cands)
    assert third.calls[down] == 1 and store.get(down)["status"] == "ok"        # an hour on it is tried again, and now works


def test_failures_are_not_remembered_when_too_few_peers_worked_because_the_service_may_be_down():
    cands = candidates()
    store = PeerStore(None, 7 * DAY, Clock())
    got = peers.gather(Data(Provider(down=[c["symbol"] for c in cands]), peer_store=store), cands)
    assert got["usable"] == [] and all(store.get(c["symbol"]) is None for c in cands)   # nothing was kept
    p = Provider()                                                            # and once it is back, they are tried at once
    assert len(peers.gather(Data(p, peer_store=store), cands)["usable"]) == config.BENCH_PEERS and sum(p.calls.values()) > 0


def test_a_reason_shown_to_the_user_does_not_carry_the_developers_hint():
    cand = candidates()[0]

    class NoStatements(Provider):
        def statements(self, symbol):
            raise DataError("no annual statements parsed; run `python -m app.probe SYMBOL` and extend app/normalize.py ALIASES")
    got = peers.gather(Data(NoStatements()), [cand])
    assert [e["reason"] for e in got["excluded"]] == ["no annual statements parsed"]


def test_a_peer_whose_figures_are_in_the_store_is_not_fetched_again_even_by_a_restarted_server():
    cands, store = candidates(), PeerStore(None, 7 * DAY, Clock())
    first = peers.gather(Data(Provider(broken=[cands[0]["symbol"]]), peer_store=store), cands)
    p2 = Provider(broken=[cands[0]["symbol"]])
    second = peers.gather(Data(p2, peer_store=store), cands)
    assert sum(p2.calls.values()) == 0                                # the verdict on the broken one is kept too
    for key in ("usable", "excluded"):
        assert [(e["symbol"], e.get("values"), e.get("reason")) for e in second[key]] == \
               [(e["symbol"], e.get("values"), e.get("reason")) for e in first[key]]


def test_refresh_measures_the_peers_again_and_otherwise_the_old_figures_stand():
    cands, clock = candidates(), Clock()
    store = PeerStore(None, 7 * DAY, clock)
    peers.gather(Data(Provider(), peer_store=store), cands)
    clock.advance(hours=1)
    kept = peers.gather(Data(Provider(), peer_store=store), cands)
    assert {e["fetched_at"] for e in kept["usable"]} == {"2026-10-21T12:00:00+00:00"}
    p = Provider()
    fresh = peers.gather(Data(p, peer_store=store), cands, refresh=True)
    assert {e["fetched_at"] for e in fresh["usable"]} == {"2026-10-21T13:00:00+00:00"} and sum(p.calls.values()) >= config.BENCH_PEERS


def test_the_peers_raw_data_and_failures_stay_out_of_the_companys_cache_file_and_warnings(tmp_path):
    cands = candidates()
    d = Data(Provider(down=[cands[0]["symbol"]]), SnapshotStore(tmp_path, 1800))
    view = d.session("AAPL.US")
    out = service.get_peers(view, "AAPL.US")
    view.save_snapshot()
    assert out["status"] == "ok" and [e["symbol"] for e in out["excluded"]] == [cands[0]["symbol"]]
    saved = json.loads(next(tmp_path.glob("AAPL.US-*.json")).read_text(encoding="utf-8"))["calls"]
    assert {(c["call"], tuple(c["args"])) for c in saved} == {("quote", ("AAPL.US",)), ("statements", ("AAPL.US",)),
                                                              ("industry_members", ("AAPL.US",))}
    assert d.warnings == []                                           # a peer that failed is not a warning on the company's page


# ---------------------------------------------------------------- the CLI provider
def cli(payload):
    """A CliProvider whose `valuation` call returns `payload`; the arguments it was called with are kept."""
    p = D.CliProvider.__new__(D.CliProvider)
    p.exe, p.calls = "x", []
    p.run = lambda *args: p.calls.append(args) or payload
    return p


def test_the_provider_lists_the_industry_largest_first():
    row = lambda cap, name: {"market_cap": cap, "name": name}
    # what `longbridge valuation AAPL.US` returned: the whole industry under `symbols`, with the stock itself in it
    payload = {"symbols": {"BOXL.US": row("1940827.4100", "Boxlight"), "AAPL.US": row("4954724110000.0000", "苹果"),
                           "DELL.US": row("293465732890.0000", "戴尔科技-C")}, "history": {"ignored": 1}}
    p = cli(payload)
    out = p.industry_members("AAPL.US")
    assert p.calls == [("valuation", "AAPL.US")]
    assert [m["symbol"] for m in out] == ["AAPL.US", "DELL.US", "BOXL.US"]
    assert out[0] == {"symbol": "AAPL.US", "name": "苹果", "market_cap": 4954724110000.0}
    assert json.loads(json.dumps(out)) == out                         # the cache file has to read it back exactly


def test_symbols_that_are_not_codes_and_members_without_a_size_are_dropped():
    row = lambda cap: {"market_cap": cap, "name": "x"}
    payload = {"symbols": {"OK.US": row("5"), "--help": row("9"), "bad symbol.US": row("9"), "NOCAP.US": {"name": "x"},
                           "ZERO.US": row("0"), "STR.US": "text", "BRK.B.US": row("7")}}
    assert [m["symbol"] for m in cli(payload).industry_members("OK.US")] == ["BRK.B.US", "OK.US"]   # a share class is a code


@pytest.mark.parametrize("payload", [{}, {"symbols": {}}, {"symbols": None}, [], {"symbols": {"X.US": {"market_cap": ""}}}])
def test_a_stock_with_no_industry_list_raises(payload):
    with pytest.raises(DataError, match="industry"):
        cli(payload).industry_members("SPY.US")


@pytest.fixture
def clock(monkeypatch):
    now, slept = [100.0], []
    monkeypatch.setattr(D.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(D.time, "sleep", lambda s: slept.append(round(s, 3)))
    monkeypatch.setattr(D.CliProvider, "_statement_next", 0.0)
    return types.SimpleNamespace(now=now, slept=slept)


def test_statement_calls_take_successive_slots_and_a_late_one_waits_for_nothing(clock):
    gap = D.CliProvider.STATEMENT_GAP
    for _ in range(3):                                                # three callers at the same instant
        D.CliProvider._pace_statements()
    assert clock.slept == [pytest.approx(gap), pytest.approx(2 * gap)]    # the first goes straight away
    clock.now[0] += 10
    clock.slept.clear()
    D.CliProvider._pace_statements()
    assert clock.slept == []


def test_only_statement_calls_wait_for_a_slot(clock, monkeypatch):
    class Reply:
        returncode, stdout, stderr = 0, "{}", ""
    monkeypatch.setattr(D.subprocess, "run", lambda *a, **k: Reply())
    p = D.CliProvider.__new__(D.CliProvider)
    p.exe = "x"
    p.run("quote", "AAPL.US")
    p.run("quote", "AAPL.US")
    assert clock.slept == []
    for _ in range(2):
        p.run("financial-statement", "AAPL.US", "--kind", "BS", "--report", "af")
    assert clock.slept == [pytest.approx(D.CliProvider.STATEMENT_GAP)]    # the second one waited for its slot


def test_threads_asking_together_each_get_a_slot_of_their_own(monkeypatch):
    monkeypatch.setattr(D.CliProvider, "STATEMENT_GAP", 0.05)
    monkeypatch.setattr(D.CliProvider, "_statement_next", 0.0)
    times, lock = [], threading.Lock()

    def ask():
        D.CliProvider._pace_statements()
        with lock:
            times.append(time.monotonic())
    threads = [threading.Thread(target=ask) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    times.sort()
    assert all(b - a >= 0.02 for a, b in zip(times, times[1:])) and times[-1] - times[0] >= 0.2   # spread out, not at once


# ---------------------------------------------------------------- the comparison a page asks for
def test_get_peers_compares_the_company_with_its_peers_by_the_same_figures_as_its_cards():
    d = Data()
    a = service.analyze(d, "AAPL.US")
    p = service.get_peers(d, "AAPL.US")
    assert p["status"] == "ok" and len(p["peers"]) == config.BENCH_PEERS and p["excluded"] == []
    assert p["company"] == benchmarks.peer_values(a["metrics"], d.call("statements", "AAPL.US"))   # the cards' own figures
    assert list(p["metrics"]) == list(benchmarks.PEER_METRICS) and p["min_peers"] == config.BENCH_MIN_PEERS
    assert p["ttl_days"] == round(config.PEER_CACHE_SECONDS / 86400, 2) and p["as_of"]
    caps = [x["market_cap"] for x in p["peers"]]
    assert caps == sorted(caps, reverse=True) and "AAPL.US" not in [x["symbol"] for x in p["peers"]]
    assert set(p["peers"][0]) == {"symbol", "name", "market_cap", "fetched_at", "values"}
    assert p["metrics"]["roic"]["n"] == config.BENCH_PEERS
    json.dumps(p)                                                     # the response has to serialise


def test_a_second_request_reuses_the_peers_figures():
    d = Data(Provider())
    first = service.get_peers(d, "AAPL.US")
    d.cache.clear()                                                   # the memory cache must not be what answers it
    before = sum(d.p.calls.values())
    second = service.get_peers(d, "AAPL.US")
    assert sum(d.p.calls.values()) - before == 1                      # only the company's own statements, no peer's
    assert second["metrics"] == first["metrics"] and second["as_of"] == first["as_of"]


def test_it_says_so_when_nobody_else_is_in_the_industry():
    p = service.get_peers(Data(Provider(members=[mem("AAPL.US", "AAPL (demo)", 1e12)])), "AAPL.US")
    assert p["status"] == "unavailable" and "no other company" in p["message"] and p["excluded"] == []


def test_it_says_so_when_too_few_peers_can_be_used():
    cands = candidates()
    d = Data(Provider(down=[c["symbol"] for c in cands[2:]]))         # all but the two nearest are unreachable
    p = service.get_peers(d, "AAPL.US")
    assert p["status"] == "unavailable" and "Only 2 of the 14 closest companies" in p["message"]
    assert len(p["excluded"]) == 12 and all("timed out" in e["reason"] for e in p["excluded"])


def test_it_says_why_when_the_industry_cannot_be_listed_and_leaves_no_warning_behind():
    class NoList(Provider):
        def industry_members(self, symbol):
            raise DataError("valuation failed")
    d = Data(NoList())
    p = service.get_peers(d, "AAPL.US")
    assert p["status"] == "unavailable" and "could not be listed: valuation failed" in p["message"]
    assert d.warnings == []


def test_a_company_whose_own_statements_fail_the_checks_is_not_compared():
    p = service.get_peers(Data(Provider(broken=["AAPL.US"])), "AAPL.US")
    assert p["status"] == "unavailable" and "reconciliation" in p["message"]


def test_the_peers_endpoint_and_its_refresh_option(monkeypatch):
    monkeypatch.setattr(D, "_instance", Data())
    c = TestClient(app)
    first = c.get("/api/peers?symbol=MSFT.US")
    assert first.status_code == 200 and first.json()["status"] == "ok" and len(first.json()["peers"]) == config.BENCH_PEERS
    again = c.get("/api/peers", params={"symbol": "msft.us", "refresh": "true"})
    assert again.status_code == 200 and again.json()["symbol"] == "MSFT.US" and again.json()["as_of"] >= first.json()["as_of"]
    assert c.get("/api/peers?symbol=--help").status_code == 400
    monkeypatch.setattr(D, "_instance", None)

import os

os.environ["DATA_MODE"] = "demo"

import collections
import json
import shutil
import subprocess
import threading
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.data as D
import app.options as O
from app import config, service, snapshot
from app.data import Data, DataError
from app.demo import DemoProvider
from app.main import app
from app.snapshot import Snapshot, SnapshotStore

SGT = timezone(timedelta(hours=8))
TTL = 30 * 60   # the shipped default (DISK_CACHE_MINUTES); every time offset below is relative to it
HOUR, WEEK = 3600, 7 * 24 * 3600   # what this feature is for: market data refetched after an hour, fundamentals kept for a week
ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")


class Provider:
    """The demo provider standing in for live data: counts every call and fails the ones it is told to. It reports
    itself as the CLI, because demo mode is deliberately never cached on disk."""
    name = "cli"

    def __init__(self, fail=None):
        self.inner, self.n, self.fail = DemoProvider(), collections.Counter(), collections.Counter(fail or {})

    def __getattr__(self, attr):
        f = getattr(self.inner, attr)   # AttributeError for what the demo lacks (news_detail), as the real check expects

        def call(*args):
            self.n[attr] += 1
            if self.fail[attr] > 0:
                self.fail[attr] -= 1
                raise DataError(f"{attr} failed")
            return f(*args)
        return call


class Clock:
    def __init__(self):
        self.t = datetime(2026, 10, 20, 14, 0, 0, tzinfo=SGT)

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(O, "IV_HISTORY", tmp_path / "iv_history.json")   # an option scan must not touch the real one
    clock = Clock()
    return types.SimpleNamespace(folder=tmp_path, clock=clock, store=SnapshotStore(tmp_path, TTL, clock))


@pytest.fixture
def weekly(tmp_path, monkeypatch):
    """A cache file that lives for a week, with market data that is refetched after an hour."""
    monkeypatch.setattr(O, "IV_HISTORY", tmp_path / "iv_history.json")
    clock = Clock()
    return types.SimpleNamespace(folder=tmp_path, clock=clock, store=SnapshotStore(tmp_path, WEEK, clock, live_ttl=HOUR))


def server(env, **kw):
    """A freshly started server: its own memory cache, the same cache folder."""
    return Data(Provider(**kw), env.store)


PANELS = (("analyze", service.analyze, ()), ("chart", service.get_chart, ("1y", 50)), ("flows", service.get_flows, ()),
          ("news", service.get_news, (10,)), ("options", service.get_options, ()))


def search(d, sym="AAPL.US", panels=PANELS):
    """The requests the page makes for a search, each in its own session as the API does."""
    out = {}
    for name, fn, args in panels:
        view = d.session(sym)
        try:
            out[name] = fn(view, sym, *args)
        finally:
            view.save_snapshot()
        out[name + "_cache"] = view.cache_info()
    return out


def scrub(x):
    """Drop the one field that is allowed to differ between a live page and a cached one: when it says it was fetched."""
    if isinstance(x, dict):
        return {k: scrub(v) for k, v in x.items() if k != "fetched_at"}
    return [scrub(i) for i in x] if isinstance(x, list) else x


def files(env, sym="AAPL.US"):
    return sorted(p.name for p in env.folder.glob(f"{sym}-*.json"))


def calls_in(path):
    return [(c["call"], c["args"]) for c in json.loads(path.read_text(encoding="utf-8"))["calls"]]


def named(env, ago=timedelta(0), sym="AAPL.US"):
    """The file name for a fetch made `ago` before the test clock's present time."""
    return snapshot.file_name(sym, env.clock() - ago)


# ---------------------------------------------------------------- file names
def test_file_name_is_ticker_then_local_time_with_its_offset():
    t = datetime(2026, 10, 20, 14, 32, 10, tzinfo=SGT)
    assert snapshot.file_name("AAPL.US", t) == "AAPL.US-20261020T143210+0800.json"
    assert snapshot.file_name("BRK.B.US", t) == "BRK.B.US-20261020T143210+0800.json"
    with pytest.raises(ValueError):
        snapshot.file_name("../x", t)                      # the name is built from the symbol: no path characters
    with pytest.raises(ValueError):
        snapshot.file_name("AAPL.US", t.replace(tzinfo=None))   # a time without an offset would not be unambiguous


def test_store_finds_only_this_tickers_files_and_orders_them_by_instant(tmp_path):
    for name in ("AAPL.US-20261020T020000-0500.json",      # 07:00 UTC
                 "AAPL.US-20261020T140000+0800.json",      # 06:00 UTC
                 "AAPL.US.B-20261020T140000+0800.json",    # another ticker that shares the prefix
                 "MSFT.US-20261020T140000+0800.json", "AAPL.US-latest.json",
                 "AAPL.US-20261020T140000+0800.json.123.tmp", "AAPL.US-99999999T999999+9999.json"):
        (tmp_path / name).write_text("{}")
    got = SnapshotStore(tmp_path, TTL)._files("AAPL.US")
    assert [p.name for _, p in got] == ["AAPL.US-20261020T020000-0500.json", "AAPL.US-20261020T140000+0800.json"]


# ---------------------------------------------------------------- the three behaviours
def test_a_first_search_fetches_live_and_saves_a_file_named_for_the_ticker_and_time(env):
    p = Provider()
    out = search(Data(p, env.store))
    assert files(env) == ["AAPL.US-20261020T140000+0800.json"]
    assert sum(p.n.values()) > 0
    assert out["analyze_cache"]["from_cache"] is False and out["analyze_cache"]["live_calls"] > 0


def test_a_search_while_the_file_is_fresh_makes_no_provider_calls_and_builds_the_same_page(env):
    live = search(server(env))
    env.clock.advance(seconds=TTL // 2)
    d2 = server(env)
    cached = search(d2)
    assert sum(d2.p.n.values()) == 0, d2.p.n                    # nothing was fetched: every call came from the file
    for panel in ("analyze", "chart", "flows", "news", "options"):
        assert scrub(cached[panel]) == scrub(live[panel]), panel   # the same page, panel by panel
    info = cached["analyze_cache"]
    assert info["from_cache"] is True and info["live_calls"] == 0
    assert info["as_of"] == "2026-10-20T06:00:00+00:00" and info["age_seconds"] == TTL // 2 and info["ttl_seconds"] == TTL
    # the appendix says when the data was fetched, not when the page was built
    assert {s["fetched_at"] for s in cached["analyze"]["sources"]} == {"2026-10-20T06:00:00+00:00"}
    assert files(env) == ["AAPL.US-20261020T140000+0800.json"]   # nothing new to save: same file, same time


def test_the_cache_is_fresh_just_under_its_lifetime_and_stale_at_it(env):
    search(server(env), panels=PANELS[:1])
    env.clock.advance(seconds=TTL - 1)
    d = server(env)
    search(d, panels=PANELS[:1])
    assert sum(d.p.n.values()) == 0                             # a second short of the lifetime: still fresh
    env.clock.advance(seconds=1)                                # now exactly as old as the lifetime
    d = server(env)
    search(d, panels=PANELS[:1])
    assert d.p.n["quote"] == 1 and d.p.n["statements"] == 1     # stale: fetched again
    assert files(env) == [named(env)]                           # overwritten: one file, named with the new time


def test_an_old_file_is_replaced_and_renamed_with_the_current_time(env):
    search(server(env), panels=PANELS[:1])
    old = named(env, timedelta(seconds=TTL + 60))               # the user made it a minute too old by renaming it
    (env.folder / "AAPL.US-20261020T140000+0800.json").rename(env.folder / old)
    d = server(env)
    out = search(d, panels=PANELS[:1])
    assert out["analyze_cache"]["from_cache"] is False and d.p.n["quote"] == 1
    assert files(env) == ["AAPL.US-20261020T140000+0800.json"]


def test_a_time_in_the_future_is_not_trusted(env):
    search(server(env), panels=PANELS[:1])
    (env.folder / "AAPL.US-20261020T140000+0800.json").rename(env.folder / "AAPL.US-20261021T140000+0800.json")
    d = server(env)
    assert search(d, panels=PANELS[:1])["analyze_cache"]["from_cache"] is False
    assert files(env) == ["AAPL.US-20261020T140000+0800.json"]


def test_a_call_the_file_does_not_hold_is_fetched_live_and_added_without_renaming_it(env):
    search(server(env))
    env.clock.advance(seconds=TTL // 3)
    d = server(env)
    more = d.session("AAPL.US")                                 # "Show more" asks for 25 news items, not 10
    assert service.get_news(more, "AAPL.US", 25)["news"]
    more.save_snapshot()
    assert d.p.n == {"news": 1}                                 # only the call the file lacked was fetched
    assert more.cache_info()["from_cache"] and more.cache_info()["live_calls"] == 1
    assert files(env) == ["AAPL.US-20261020T140000+0800.json"]  # same file, same time
    assert ("news", ["AAPL.US", 25]) in calls_in(env.folder / files(env)[0])
    d3 = server(env)
    service.get_news(d3.session("AAPL.US"), "AAPL.US", 25)
    assert sum(d3.p.n.values()) == 0                            # and now it is in the file


# ---------------------------------------------------------------- fidelity and robustness
def test_an_ordinary_failed_call_is_not_saved_so_a_transient_failure_is_not_frozen_in(env):
    d = server(env, fail={"insider": 1})                        # e.g. SEC EDGAR rate limit
    search(d, panels=PANELS[:1])
    assert "insider" not in [c for c, _ in calls_in(env.folder / files(env)[0])]
    d2 = server(env)
    search(d2, panels=PANELS[:1])
    assert d2.p.n == {"insider": 1}                             # only the failed call is tried again
    assert "insider" in [c for c, _ in calls_in(env.folder / files(env)[0])]
    d3 = server(env)
    search(d3, panels=PANELS[:1])
    assert sum(d3.p.n.values()) == 0


class Unreadable(Provider):
    """News items without a summary whose detail cannot be read: the CLI answers `news detail` in Markdown, not JSON,
    so each one fails after its retries. The app calls these quiet, since a missing summary is not worth a warning."""

    def news(self, symbol, limit):
        self.n["news"] += 1
        return [{**it, "summary": ""} for it in DemoProvider().news(symbol, limit)]

    def news_detail(self, nid):
        self.n["news_detail"] += 1
        raise DataError("`longbridge news` returned non-JSON output")


def test_a_quiet_failure_is_remembered_so_it_is_not_repeated_on_every_search(env):
    live_server = Data(Unreadable(), env.store)
    live = search(live_server, panels=PANELS[3:4])["news"]
    assert live_server.p.n["news_detail"] == 10 and all(not i["summary"] for i in live["news"])
    errors = [c for c in json.loads((env.folder / files(env)[0]).read_text(encoding="utf-8"))["calls"] if "error" in c]
    assert len(errors) == 10 and all("data" not in c and c["call"] == "news_detail" for c in errors)
    d = Data(Unreadable(), env.store)
    cached = search(d, panels=PANELS[3:4])
    assert d.p.n == {} and d.warnings == []                     # nothing was tried again, and a quiet failure stays quiet
    assert scrub(cached["news"]) == scrub(live) and cached["news_cache"]["live_calls"] == 0


def test_a_remembered_failure_raises_for_a_required_call_and_returns_the_default_for_an_optional_one(env):
    snap = env.store.open("AAPL.US")
    snap.add_failure(("quote", "AAPL.US"), "quote failed last time")
    env.store.save(snap)
    d = Data(Provider(), env.store).session("AAPL.US")
    with pytest.raises(DataError, match="quote failed last time"):
        d.call("quote", "AAPL.US", optional=False)
    assert d.call("quote", "AAPL.US", default="fallback") == "fallback"
    assert d.warnings == ["quote: quote failed last time"]      # an optional, non-quiet caller is still told
    assert d.p.n == {}


class NoShares(Provider):
    def statements(self, symbol):
        st = DemoProvider().statements(symbol)
        for key in ("annual", "quarterly"):
            st[key][-1].pop("shares")                           # the case _fill_shares repairs by editing the data in place
        return st


def test_the_file_holds_what_the_source_returned_not_what_the_analysis_later_edited(env):
    view = Data(NoShares(), env.store).session("AAPL.US")
    live = service.analyze(view, "AAPL.US")
    view.save_snapshot()
    note = next(w for w in live["warnings"] if w.startswith("shares:"))
    saved = json.loads((env.folder / files(env)[0]).read_text(encoding="utf-8"))["calls"]
    stm = next(c["data"] for c in saved if c["call"] == "statements")
    assert "shares" not in stm["annual"][-1]                    # the repair was made on a copy, not on what was saved
    cached = service.analyze(Data(NoShares(), env.store).session("AAPL.US"), "AAPL.US")
    assert note in cached["warnings"] and scrub(cached) == scrub(live)   # so a cached page repeats the warning


def _doc(at):
    """A file of the current version whose one call carries this time."""
    return json.dumps({"version": snapshot.VERSION, "symbol": "AAPL.US", "fetched_at": "2026-10-20T06:00:00+00:00",
                       "calls": [{"call": "quote", "args": ["AAPL.US"], "at": at, "data": {}}]})


@pytest.mark.parametrize("content", ["not json {", '{"version": 999, "symbol": "AAPL.US", "calls": []}', "[]", '{"version":1}',
                                     _doc("yesterday"), _doc("2026-10-20T14:00:00")])   # a time that is not one, and one without its offset
def test_an_unreadable_file_or_one_from_another_version_is_ignored_and_replaced(env, content):
    bad = env.folder / named(env, timedelta(seconds=TTL // 3))  # a third of the lifetime old, so it looks fresh by name
    bad.write_text(content, encoding="utf-8")
    d = server(env)
    assert search(d, panels=PANELS[:1])["analyze_cache"]["from_cache"] is False and d.p.n["quote"] == 1
    assert files(env) == ["AAPL.US-20261020T140000+0800.json"]
    assert calls_in(env.folder / files(env)[0])


def test_a_cache_folder_that_cannot_be_written_never_breaks_the_page(tmp_path, caplog):
    blocked = tmp_path / "data"
    blocked.write_text("a file where the folder should be")
    d = Data(Provider(), SnapshotStore(blocked, TTL, Clock()))
    out = search(d, panels=PANELS[:1])
    assert out["analyze"]["bull"] is not None
    assert "could not save" in caplog.text


def test_values_that_do_not_read_back_exactly_are_not_saved_and_the_copy_is_independent():
    snap = Snapshot("AAPL.US", Clock()())
    for key, bad in (("int_keys", {1: "a"}), ("tuple", (1, 2)), ("nan", float("nan")), ("set", {1})):
        snap.add((key,), bad)
    assert snap.added == {}
    v = {"a": [1, 2.5, None, True]}
    snap.add(("ok",), v)
    v["a"].append(99)                                           # a caller editing the value afterwards
    assert snap.added[("ok",)] == {"a": [1, 2.5, None, True]}


def test_replaying_from_the_cache_does_not_stamp_todays_date_on_old_iv_data(env):
    search(server(env), panels=PANELS[:1] + PANELS[4:])
    hist = json.loads(O.IV_HISTORY.read_text())
    assert list(hist["AAPL.US"]) == [date.today().isoformat()]  # a live scan records the IV
    O.IV_HISTORY.write_text(json.dumps({"AAPL.US": {"1999-01-01": 0.11}}))
    env.clock.advance(seconds=TTL // 2)
    cached = search(server(env), panels=PANELS[:1] + PANELS[4:])
    assert cached["options_cache"]["from_cache"]
    assert json.loads(O.IV_HISTORY.read_text()) == {"AAPL.US": {"1999-01-01": 0.11}}   # untouched by the replay
    assert cached["options"]["iv"]["observations"] == 1         # the percentile still reads the stored series
    env.clock.advance(seconds=TTL)                              # stale: fetched live again, so recorded again
    search(server(env), panels=PANELS[:1] + PANELS[4:])
    assert len(json.loads(O.IV_HISTORY.read_text())["AAPL.US"]) == 2


def test_session_without_a_disk_cache_is_the_data_itself():
    d = Data()
    assert d.session("AAPL.US") is d and d.cache_info() is None and d.was_cached("quote", "AAPL.US") is False
    d.save_snapshot()                                           # a no-op, not an error


# ---------------------------------------------------------------- through the API
def test_api_responses_say_where_their_data_came_from(env, monkeypatch):
    monkeypatch.setattr(D, "_instance", Data(Provider(), env.store))
    c = TestClient(app)
    urls = ("/api/analyze?symbol=AAPL.US", "/api/chart?symbol=AAPL.US", "/api/flows?symbol=AAPL.US",
            "/api/news?symbol=AAPL.US&limit=10")
    first = c.get("/api/analyze?symbol=aapl.us").json()         # the symbol is normalised, then fetched live
    assert first["cache"]["from_cache"] is False and first["cache"]["symbol"] == "AAPL.US"
    for url in urls[1:]:
        c.get(url)                                              # the rest of the page, so the file holds all four
    env.clock.advance(seconds=TTL // 2)
    for url in urls:
        info = c.get(url).json()["cache"]
        assert info == {"symbol": "AAPL.US", "from_cache": True, "as_of": "2026-10-20T06:00:00+00:00",
                        "age_seconds": TTL // 2, "ttl_seconds": TTL, "market_as_of": "2026-10-20T06:00:00+00:00",
                        "market_age_seconds": TTL // 2, "market_ttl_seconds": TTL, "live_calls": 0}, url
    assert files(env) == ["AAPL.US-20261020T140000+0800.json"]
    assert c.get("/api/analyze?symbol=../x").status_code == 400 and files(env, "*") == ["AAPL.US-20261020T140000+0800.json"]


def test_requests_arriving_together_for_a_new_ticker_leave_one_complete_file(env, monkeypatch):
    monkeypatch.setattr(D, "_instance", Data(Provider(), env.store))
    urls = ["/api/analyze?symbol=AAPL.US", "/api/chart?symbol=AAPL.US&range=1y", "/api/flows?symbol=AAPL.US",
            "/api/news?symbol=AAPL.US&limit=10", "/api/options?symbol=AAPL.US"]
    bad = []

    def hit(url):
        r = TestClient(app).get(url)
        if r.status_code != 200:
            bad.append((url, r.status_code, r.text[:200]))
    threads = [threading.Thread(target=hit, args=(u,)) for u in urls]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not bad
    assert files(env) == ["AAPL.US-20261020T140000+0800.json"] and not list(env.folder.glob("*.tmp"))
    p = Provider()                                              # a restarted server answers all five from that one file
    monkeypatch.setattr(D, "_instance", Data(p, env.store))
    for u in urls:
        assert TestClient(app).get(u).json()["cache"]["from_cache"] is True
    assert sum(p.n.values()) == 0, p.n


def test_the_page_has_a_place_for_the_cache_note():
    c = TestClient(app)
    assert 'id="cachenote"' in c.get("/").text and "function noteCache(" in c.get("/static/app.js").text


# ---------------------------------------------------------------- the memory cache follows the disk cache
def test_memory_lifetime_never_exceeds_the_disk_lifetime_and_live_data_without_a_disk_cache_is_held_only_briefly(tmp_path):
    long_store, short_store = SnapshotStore(tmp_path, TTL), SnapshotStore(tmp_path, 60)
    assert Data(Provider(), long_store)._memory_ttl("quote") == config.MEMORY_TTL     # 30 min disk: memory keeps its own cap
    assert Data(Provider(), short_store)._memory_ttl("quote") == 60                   # shorter disk lifetime wins
    assert Data(Provider())._memory_ttl("quote") == config.DEDUPE_TTL                 # live data, disk cache off
    assert Data(DemoProvider())._memory_ttl("quote") == config.MEMORY_TTL             # synthetic data is free to keep


def test_without_a_disk_cache_a_later_search_fetches_live_again(monkeypatch):
    p, now = Provider(), [1000.0]
    monkeypatch.setattr(D.time, "time", lambda: now[0])
    d = Data(p)
    d.call("quote", "AAPL.US"); d.call("quote", "AAPL.US")
    assert p.n["quote"] == 1                                                   # concurrent/immediate repeats share one fetch
    now[0] += config.DEDUPE_TTL + 1
    d.call("quote", "AAPL.US")
    assert p.n["quote"] == 2                                                   # a new search is not answered from memory


# ---------------------------------------------------------------- forcing a refresh
def test_refresh_drops_the_tickers_file_and_memory_so_the_next_search_fetches_live(env):
    d = server(env)
    s = d.session("AAPL.US"); service.analyze(s, "AAPL.US"); s.save_snapshot()
    assert env.store._files("AAPL.US")
    first = sum(d.p.n.values())
    d.refresh("AAPL.US")
    assert not env.store._files("AAPL.US") and not any("AAPL.US" in k[1:] for k in d.cache)
    s = d.session("AAPL.US"); service.analyze(s, "AAPL.US")
    assert sum(d.p.n.values()) > first                               # fetched again, not answered from either cache


def test_refresh_endpoint_leaves_other_tickers_alone(env, monkeypatch):
    d = Data(Provider(), env.store)
    monkeypatch.setattr(D, "_instance", d)
    for sym in ("AAPL.US", "MSFT.US"):
        s = d.session(sym); service.get_flows(s, sym); s.save_snapshot()
    r = TestClient(app).post("/api/refresh", params={"symbol": "aapl.us"})
    assert r.status_code == 200 and r.json()["symbol"] == "AAPL.US"
    assert not env.store._files("AAPL.US") and env.store._files("MSFT.US")


# ---------------------------------------------------------------- when the cache is switched on
def test_demo_mode_and_a_zero_lifetime_never_use_the_disk_cache(monkeypatch):
    monkeypatch.setattr(D, "_instance", None)
    assert D.get_data().store is None                           # the suite runs in demo mode: synthetic data is not cached
    for mode, seconds, expected in (("cli", 0, False), ("cli", TTL, True), ("demo", TTL, False)):
        monkeypatch.setattr(D, "_instance", None)
        monkeypatch.setattr(config, "DATA_MODE", mode)
        monkeypatch.setattr(config, "DISK_CACHE_SECONDS", seconds)
        monkeypatch.setattr(config, "LIVE_CACHE_SECONDS", 600)
        store = D.get_data().store
        assert (store is not None) is expected
        if store:
            assert store.folder == config.DATA_DIR and store.ttl == TTL and store.live_ttl == 600


# ---------------------------------------------------------------- market data and fundamentals have their own lifetimes
def at_times(env):
    """{call: the time of its first row} in the ticker's file."""
    rows = json.loads((env.folder / files(env)[0]).read_text(encoding="utf-8"))["calls"]
    return {c["call"]: c["at"] for c in reversed(rows)}


def test_every_data_call_is_market_data_or_a_fundamental_and_never_both():
    methods = {n for n in dir(D.CliProvider) if not n.startswith("_") and callable(getattr(D.CliProvider, n))} - {"run", "forget"}
    assert not config.LIVE_CALLS & config.STATIC_CALLS
    assert methods == config.LIVE_CALLS | config.STATIC_CALLS, "put each provider call in config.LIVE_CALLS or config.STATIC_CALLS"
    assert {n for n in dir(DemoProvider) if not n.startswith("_") and callable(getattr(DemoProvider, n))} <= methods


def test_a_call_in_neither_list_counts_as_market_data():
    assert config.is_live_call("some_new_call") is True      # a new data call is never frozen for a week by accident
    assert config.is_live_call("quote") is True and config.is_live_call("statements") is False


def test_the_live_lifetime_is_never_longer_than_the_files(tmp_path):
    assert SnapshotStore(tmp_path, WEEK, live_ttl=HOUR).live_ttl == HOUR
    assert SnapshotStore(tmp_path, 600, live_ttl=HOUR).live_ttl == 600     # DISK_CACHE_MINUTES caps it
    assert SnapshotStore(tmp_path, 600).live_ttl == 600                    # none of its own: as long as the fundamentals
    assert SnapshotStore(tmp_path, WEEK, live_ttl=0).live_ttl == 0
    store = SnapshotStore(tmp_path, WEEK, live_ttl=HOUR)
    assert store.lifetime("quote") == HOUR and store.lifetime("statements") == WEEK and store.lifetime("some_new_call") == HOUR


def test_memory_keeps_each_call_no_longer_than_its_disk_lifetime(tmp_path):
    d = Data(Provider(), SnapshotStore(tmp_path, WEEK, live_ttl=60))
    assert d._memory_ttl("quote") == 60 and d._memory_ttl("statements") == config.MEMORY_TTL
    never = Data(Provider(), SnapshotStore(tmp_path, WEEK, live_ttl=0))
    assert never._memory_ttl("quote") == config.DEDUPE_TTL                 # panels loading together still share one fetch


def test_after_an_hour_only_market_data_is_fetched_again_and_the_week_old_file_is_kept(weekly):
    live = search(server(weekly))
    made = files(weekly)
    weekly.clock.advance(hours=3)
    d = server(weekly)
    again = search(d)
    refetched = set(d.p.n)
    assert {"quote", "kline", "capital_flow", "news", "option_chain", "option_quotes", "industry_valuation"} <= refetched
    assert all(config.is_live_call(n) for n in refetched), refetched       # nothing that changes by the quarter went back to the source
    for panel in ("analyze", "chart", "flows", "news", "options"):
        assert scrub(again[panel]) == scrub(live[panel]), panel
    assert files(weekly) == made                                           # the same file, with its own time
    at = at_times(weekly)
    assert at["statements"] == at["analyst"] == "2026-10-20T06:00:00+00:00"   # as they were fetched
    assert at["quote"] == at["kline"] == "2026-10-20T09:00:00+00:00"          # replaced by the new fetch


def test_a_search_within_the_hour_fetches_nothing_even_though_the_file_lives_for_a_week(weekly):
    search(server(weekly))
    weekly.clock.advance(seconds=HOUR - 1)
    d = server(weekly)
    out = search(d)
    assert sum(d.p.n.values()) == 0, d.p.n
    info = out["analyze_cache"]
    assert info["from_cache"] and info["live_calls"] == 0
    assert info["as_of"] == info["market_as_of"] == "2026-10-20T06:00:00+00:00"
    assert info["market_age_seconds"] == HOUR - 1 and info["market_ttl_seconds"] == HOUR and info["ttl_seconds"] == WEEK
    weekly.clock.advance(seconds=1)                                        # the market data is now exactly as old as its lifetime
    d = server(weekly)
    search(d, panels=PANELS[:1])
    assert d.p.n["quote"] == 1 and d.p.n["statements"] == 0 and d.p.n["analyst"] == 0


def test_the_whole_file_is_replaced_when_the_week_is_over(weekly):
    search(server(weekly), panels=PANELS[:1])
    weekly.clock.advance(seconds=WEEK)
    d = server(weekly)
    search(d, panels=PANELS[:1])
    assert d.p.n["statements"] == 1 and d.p.n["quote"] == 1                # the fundamentals too
    assert files(weekly) == [named(weekly)]                                # in a new file, named with the new time


def test_market_data_nobody_can_use_any_more_does_not_pile_up_in_the_file(weekly):
    view = server(weekly).session("AAPL.US")
    service.get_options(view, "AAPL.US")                                   # the chains of the default expiry window
    view.save_snapshot()
    before = [a for c, a in calls_in(weekly.folder / files(weekly)[0]) if c == "option_chain"]
    weekly.clock.advance(hours=3)
    d = server(weekly)
    view = d.session("AAPL.US")
    service.get_options(view, "AAPL.US", dte_min=8, dte_max=14)            # another window asks for other expiries
    view.save_snapshot()
    rows = calls_in(weekly.folder / files(weekly)[0])
    assert len(before) >= 2 and d.p.n["option_chain"] >= 1
    assert len([1 for c, _ in rows if c == "option_chain"]) == d.p.n["option_chain"]   # only the chains fetched just now are left
    assert "statements" in [c for c, _ in rows]                            # and the fundamentals stay


def test_each_row_of_the_sources_table_shows_when_its_own_calls_were_fetched(weekly):
    search(server(weekly), panels=PANELS[:1])
    weekly.clock.advance(hours=3)
    when = {s["field"]: s["fetched_at"] for s in search(server(weekly), panels=PANELS[:1])["analyze"]["sources"]}
    old, new = "2026-10-20T06:00:00+00:00", "2026-10-20T09:00:00+00:00"
    assert when["Operating margin"] == when["Insider / holders"] == when["PEG"] == old   # from the file made three hours ago
    assert when["P/E (TTM)"] == old                                        # a price over a statement is as old as the statement
    assert when["EMA / volume profile"] == when["Capital flow"] == when["Industry median (P/E, P/B, P/S)"] == new


class Intraday:
    """A source whose newest bar is the session of Tuesday 20 October 2026."""
    name = "cli"

    def kline(self, symbol, count):
        out, d = [], date(2026, 10, 20)
        while len(out) < 300:
            if d.weekday() < 5:
                out.append({"t": d.isoformat(), "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.0, "v": 1000.0})
            d -= timedelta(days=1)
        return out[::-1]


def test_a_bar_refreshed_mid_session_is_judged_by_that_time_not_by_when_the_file_was_made(tmp_path):
    clock = Clock()
    clock.t = datetime(2026, 10, 19, 8, 0, tzinfo=SGT)           # Sunday evening in New York: the file is made
    store = SnapshotStore(tmp_path, WEEK, clock, live_ttl=HOUR)
    first = Data(Intraday(), store).session("AAPL.US")
    service.get_chart(first, "AAPL.US", "1y", 50)
    first.save_snapshot()
    clock.t = datetime(2026, 10, 20, 22, 0, tzinfo=SGT)          # Tuesday 10:00 in New York, the session is open: the bars are a day old
    second = Data(Intraday(), store).session("AAPL.US")
    service.get_chart(second, "AAPL.US", "1y", 50)               # so they are fetched again, now with a bar that is still forming
    second.save_snapshot()
    clock.t = datetime(2026, 10, 20, 22, 30, tzinfo=SGT)
    cached = service.get_chart(Data(Intraday(), store).session("AAPL.US"), "AAPL.US", "1y", 50)["avwap"]
    assert cached["last_bar_final"] is False and cached["newest_bar"] == "2026-10-20"   # it was fetched before the close
    assert sorted(p.name for p in tmp_path.glob("*.json")) == ["AAPL.US-20261019T080000+0800.json"]   # while the file is from Sunday


def test_a_remembered_failure_outlives_the_hour_so_unreadable_articles_are_not_tried_again(weekly):
    first = Data(Unreadable(), weekly.store)
    search(first, panels=PANELS[3:4])
    assert first.p.n["news_detail"] == 10
    weekly.clock.advance(hours=3)
    d = Data(Unreadable(), weekly.store)
    out = search(d, panels=PANELS[3:4])
    assert d.p.n["news"] == 1 and d.p.n["news_detail"] == 0               # fresh headlines, and the ten that cannot be read are left alone
    assert out["news"]["news"] and d.warnings == []


def test_a_file_without_per_call_times_is_read_as_it_always_was(weekly):
    search(server(weekly), panels=PANELS[:1])
    path = weekly.folder / files(weekly)[0]
    doc = json.loads(path.read_text(encoding="utf-8"))
    for c in doc["calls"]:
        del c["at"]                                              # a file written before the times were stored
    path.write_text(json.dumps(doc), encoding="utf-8")
    weekly.clock.advance(minutes=30)
    d = server(weekly)
    search(d, panels=PANELS[:1])
    assert sum(d.p.n.values()) == 0                              # every call is as old as the file, half an hour: all fresh
    weekly.clock.advance(hours=2)
    d = server(weekly)
    search(d, panels=PANELS[:1])
    assert d.p.n["quote"] == 1 and d.p.n["statements"] == 0      # the market data has gone stale; the fundamentals have not


def test_a_call_dated_in_the_future_is_not_trusted(weekly):
    search(server(weekly), panels=PANELS[:1])
    path = weekly.folder / files(weekly)[0]
    doc = json.loads(path.read_text(encoding="utf-8"))
    for c in doc["calls"]:
        if c["call"] == "quote":
            c["at"] = "2026-10-21T06:00:00+00:00"                # a day after the test clock's present
    path.write_text(json.dumps(doc), encoding="utf-8")
    d = server(weekly)
    search(d, panels=PANELS[:1])
    assert d.p.n == {"quote": 1}                                 # only that call is fetched again


def test_api_responses_report_the_age_of_the_fundamentals_and_of_the_market_data_apart(weekly, monkeypatch):
    monkeypatch.setattr(D, "_instance", Data(Provider(), weekly.store))
    c = TestClient(app)
    c.get("/api/analyze?symbol=AAPL.US")
    weekly.clock.advance(hours=3)
    monkeypatch.setattr(D, "_instance", Data(Provider(), weekly.store))    # three hours on, the server's memory holds nothing of it
    first = c.get("/api/analyze?symbol=AAPL.US").json()["cache"]
    assert first["from_cache"] and first["as_of"] == "2026-10-20T06:00:00+00:00" and first["age_seconds"] == 3 * HOUR
    assert first["ttl_seconds"] == WEEK and first["market_ttl_seconds"] == HOUR
    assert first["market_as_of"] is None and first["live_calls"] > 0       # three hours old, so it was all fetched for this response
    weekly.clock.advance(minutes=10)
    again = c.get("/api/chart?symbol=AAPL.US").json()["cache"]             # ten minutes later the bars come from the file
    assert again["market_as_of"] == "2026-10-20T09:00:00+00:00" and again["market_age_seconds"] == 600 and again["live_calls"] == 0


# ---------------------------------------------------------------- what the provider itself holds
def test_the_provider_does_not_hold_the_analyst_ratings_for_as_long_as_the_server_runs(monkeypatch):
    p, now, ran = D.CliProvider.__new__(D.CliProvider), [100.0], []
    monkeypatch.setattr(D.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(p, "run", lambda *a, **k: ran.append(a) or {"fetch": len(ran)})
    assert p._rating("AAPL.US") == {"fetch": 1} and p._rating("AAPL.US") == {"fetch": 1}   # the quote and the analyst call of one search share a fetch
    now[0] += config.RATING_TTL
    assert p._rating("AAPL.US") == {"fetch": 2}                  # a later search asks again, so a cache that refetches gets fresh ratings
    p.forget("AAPL.US")
    assert p._rating("AAPL.US") == {"fetch": 3}


def test_refresh_also_clears_what_the_provider_holds(env):
    forgotten, p = [], Provider()
    p.forget = forgotten.append
    Data(p, env.store).refresh("AAPL.US")
    assert forgotten == ["AAPL.US"]


# ---------------------------------------------------------------- the note on the page
def cache_block(**kw):
    """The `cache` block of a response whose file is two days old, in a cache that keeps a week and an hour."""
    return {"symbol": "AAPL.US", "from_cache": True, "as_of": "2026-10-22T13:35:00+00:00", "age_seconds": 2 * 86400 + 60, "ttl_seconds": WEEK,
            "market_as_of": None, "market_age_seconds": None, "market_ttl_seconds": HOUR, "live_calls": 0, **kw}


def notes(tmp_path, blocks):
    f = tmp_path / "caches.json"
    f.write_text(json.dumps(blocks), encoding="utf-8")
    done = subprocess.run([NODE, str(ROOT / "tests" / "cache_note.js"), str(ROOT / "web" / "app.js"), str(f)],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@needs_node
def test_the_note_gives_the_age_of_the_fundamentals_and_of_the_market_data_apart(tmp_path):
    fetched, served = notes(tmp_path, [cache_block(live_calls=20),
                                       cache_block(market_as_of="2026-10-24T04:49:00+00:00", market_age_seconds=12 * 60)])
    assert "Fundamentals (statements, ratings, holders) as of" in fetched["text"] and "(2 days ago)" in fetched["text"]
    assert "fetched just now" in fetched["text"] and "fetched again once it is 1 h old" in fetched["text"]
    assert "(12 min ago)" in served["text"] and "fetched just now" not in served["text"]   # another panel's response had it from the file
    assert 'id="refresh2"' in served["html"]


@needs_node
def test_the_note_shows_the_oldest_time_any_panel_used_and_ignores_other_tickers_and_uncached_responses(tmp_path):
    got = notes(tmp_path, [
        cache_block(market_as_of="2026-10-24T04:49:00+00:00", market_age_seconds=300),
        cache_block(market_as_of="2026-10-24T04:40:00+00:00", market_age_seconds=840),      # an older time wins
        cache_block(market_as_of="2026-10-24T04:52:00+00:00", market_age_seconds=120),      # a newer one does not
        cache_block(market_as_of=None),                                                     # a panel that fetched its own hides nothing
        cache_block(symbol="MSFT.US", market_as_of="2026-10-24T03:00:00+00:00", market_age_seconds=6000),   # a search for something else
        cache_block(from_cache=False, market_as_of="2026-10-24T02:00:00+00:00", market_age_seconds=9000)])  # a response with no cache in it
    assert "(5 min ago)" in got[0]["text"]
    assert all("(14 min ago)" in n["text"] for n in got[1:])


@needs_node
def test_the_note_reads_days_for_a_long_age_and_every_search_for_a_zero_lifetime(tmp_path):
    week, always = notes(tmp_path, [cache_block(age_seconds=6 * 86400 + 3600), cache_block(market_ttl_seconds=0)])
    assert "(6 days ago)" in week["text"]
    assert "fetched again on every search" in always["text"] and "once it is" not in always["text"]


# ---------------------------------------------------------------- the settings
@pytest.mark.parametrize("raw,want", [
    ("10080", 10080), ("60*24*7", 10080), (" 60 * 24 * 7 ", 10080), ("(60+30)*2", 180), ("1.5", 1.5), ("1/4", 0.25), ("-5", 0), ("0", 0),
    (None, 30), ("", 30), ("  ", 30),                                                       # not set: the default
    ("abc", 30), ("7s", 30), ("60*", 30), ("1/0", 30), ("2**8", 30), ("1e999", 30), ("__import__('os').getcwd()", 30)])   # not minutes: never evaluated
def test_a_lifetime_in_minutes_is_a_number_or_plain_arithmetic_and_anything_else_is_the_default(monkeypatch, raw, want):
    if raw is None:
        monkeypatch.delenv("CACHE_TEST_MINUTES", raising=False)
    else:
        monkeypatch.setenv("CACHE_TEST_MINUTES", raw)
    assert config._minutes("CACHE_TEST_MINUTES", 30) == want


def test_a_setting_that_is_not_minutes_is_reported_not_silently_ignored(monkeypatch, capsys):
    monkeypatch.setenv("CACHE_TEST_MINUTES", "a week")
    assert config._minutes("CACHE_TEST_MINUTES", 30) == 30
    assert "CACHE_TEST_MINUTES" in capsys.readouterr().err

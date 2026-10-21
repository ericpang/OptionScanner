import os

os.environ["DATA_MODE"] = "demo"

import collections
import json
import threading
import types
from datetime import date, datetime, timedelta, timezone

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


@pytest.mark.parametrize("content", ["not json {", '{"version": 999, "symbol": "AAPL.US", "calls": []}', "[]", '{"version":1}'])
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
                        "age_seconds": TTL // 2, "ttl_seconds": TTL, "live_calls": 0}, url
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
    assert Data(Provider(), long_store)._memory_ttl() == config.MEMORY_TTL     # 30 min disk: memory keeps its own cap
    assert Data(Provider(), short_store)._memory_ttl() == 60                   # shorter disk lifetime wins
    assert Data(Provider())._memory_ttl() == config.DEDUPE_TTL                 # live data, disk cache off
    assert Data(DemoProvider())._memory_ttl() == config.MEMORY_TTL             # synthetic data is free to keep


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
        store = D.get_data().store
        assert (store is not None) is expected
        if store:
            assert store.folder == config.DATA_DIR and store.ttl == TTL

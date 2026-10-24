"""Per-ticker disk cache: what was fetched for a ticker, saved in the data folder so a repeat search can skip the fetch.

One file per ticker, named TICKER-YYYYMMDDTHHMMSS+HHMM.json after the time the file was made, in local time with
its UTC offset, for example AAPL.US-20261020T143210+0800.json. The file is a log of the data-layer calls made for that
ticker (`quote`, `kline`, `option_chain`, ...), the data each returned and the time it was fetched (`at`). Replaying it
through `Data.call` rebuilds every panel with the same code that builds it from live data, so a page built from the cache
is the same page, not a second implementation.

The calls have two lifetimes (config.LIVE_CALLS and config.STATIC_CALLS). Fundamentals (statements, ratings, holders, filing
text: what changes a quarter at a time) live as long as the file does. Market data (prices, bars, option chains, news:
what changes by the hour) lives LIVE_CACHE_MINUTES, never longer than the file, so a file kept for a week still has
current prices.

- Fresh: the file is younger than the TTL (DISK_CACHE_MINUTES, 30 by default). Its fundamentals are used and nothing is
  fetched for those calls. Its market data is used too while it is younger than the live lifetime; a call that is older is
  fetched again and replaces its row in the file, which keeps its name and time.
- Not in the file: a call the file does not hold (new option controls, "show more" news) is fetched live and added to
  the file, which keeps its time.
- Stale or absent: everything is fetched live and saved to a new file named with the current time; the old file goes.

The time in the file name is the only clock for the fundamentals, so renaming a file makes it older or younger. Market data
is judged by the time stored beside it; a file written before those times were stored gives every call the file's own time.

A failed call is not stored, so a transient failure such as a rate limit is tried again on the next search instead of
being frozen in. The one exception is a call the app has marked `quiet` (its failure is expected and not worth a
warning, such as a news article that cannot be read): its failure is stored too, or every search would repeat it.
"""
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from . import config

log = logging.getLogger(__name__)

VERSION = 4   # bump when a provider method changes what it returns: files written by older code are then ignored
STAMP = "%Y%m%dT%H%M%S%z"   # 20261020T143210+0800: local time plus its UTC offset, so it is unambiguous across DST
_SYMBOL = re.compile(r"[A-Za-z0-9_.]+")
_FILE = re.compile(r"(?P<symbol>[A-Za-z0-9_.]+)-(?P<stamp>\d{8}T\d{6}[+-]\d{4})\.json")
MISS = object()   # "not in the snapshot", which is not the same as a stored None


def local_now() -> datetime:
    return datetime.now().astimezone()   # timezone-aware, in this machine's local time zone


def utc_iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def file_name(symbol: str, stamp: datetime) -> str:
    if not _SYMBOL.fullmatch(symbol):   # the name is built from the symbol, so it may hold no path characters
        raise ValueError(f"not a ticker: {symbol!r}")
    if stamp.tzinfo is None:
        raise ValueError("the time in a cache file name must carry its UTC offset")
    return f"{symbol}-{stamp.strftime(STAMP)}.json"


def _key(call: dict) -> tuple:
    """(name, *args) as Data.call builds it. JSON has no tuples, so a list argument (option_quotes takes a tuple of
    contract symbols) is turned back into one, or the call would never match its key."""
    def freeze(x):
        return tuple(freeze(i) for i in x) if isinstance(x, list) else x
    return (call["call"], *freeze(call["args"]))


def _when(text: Any, default: datetime) -> datetime:
    """The time stored beside a call: ISO text with its UTC offset. A file written before the times were stored has
    none, and its calls are as old as the file. Text that is not a time raises, and the file is then ignored as unreadable."""
    if text is None:
        return default
    t = datetime.fromisoformat(text)
    if t.tzinfo is None:
        raise ValueError(f"a time without its UTC offset: {text!r}")
    return t


class Failure:
    """A call that failed in a way the app treats as harmless, remembered so that it is not tried on every search."""

    def __init__(self, message: str):
        self.message = message


class Snapshot:
    """One request's view of a ticker's cache: what the file already held, plus what this request fetched live."""

    def __init__(self, symbol: str, opened: datetime, stamp: Optional[datetime] = None, loaded: Optional[dict] = None,
                 loaded_at: Optional[dict] = None, clock: Callable[[], datetime] = local_now):
        self.symbol = symbol
        self.opened = opened                 # when this request opened the snapshot
        self.stamp = stamp or opened         # when the file was made: its time, or `opened` if new
        self.loaded = loaded or {}           # key -> data (or a Failure) read from the file and still within its lifetime
        self.loaded_at = loaded_at or {}     # key -> when that data was fetched
        self.added: dict = {}                # key -> data (or a Failure) this request fetched live, to merge into the file
        self.added_at: dict = {}             # key -> when
        self.served: set = set()             # keys answered from the file
        self.seen: dict = {}                 # key -> when the data this request used for it was fetched (a Failure is no data)
        self._clock = clock

    @property
    def from_cache(self) -> bool:
        return bool(self.served)

    def get(self, key: tuple):
        val = self.loaded.get(key, MISS)
        if val is not MISS:
            self.served.add(key)
            if not isinstance(val, Failure):
                self.saw(key, self.loaded_at.get(key, self.stamp))
        return val

    def saw(self, key: tuple, when: datetime) -> None:
        """Note that this request used data for `key` that was fetched at `when`. The oldest use counts, so the time
        stays true for the whole request."""
        self.seen[key] = min(when, self.seen.get(key, when))

    def add(self, key: tuple, value: Any) -> None:
        """Remember a result fetched live. The copy is taken now, before a caller can change `value` in place (the
        statements are, to fill in share counts), so the file holds what the source returned. Only data that reads
        back exactly as it was fetched is kept, so a cached page cannot differ from a live one."""
        now = self._clock()
        self.saw(key, now)   # this request used it, saved or not
        try:
            pristine = json.loads(json.dumps(value))
        except (TypeError, ValueError) as e:
            log.warning("cache: %s for %s cannot be saved (%s)", key[0], self.symbol, e)
            return
        if pristine != value:   # tuples, non-string dict keys or NaN do not survive the round trip
            log.warning("cache: %s for %s is not saved: it does not read back as fetched", key[0], self.symbol)
            return
        self.added[key], self.added_at[key] = pristine, now

    def add_failure(self, key: tuple, message: str) -> None:
        self.added[key], self.added_at[key] = Failure(message), self._clock()


class SnapshotStore:
    """The cache folder: finds a ticker's fresh file, and merges what a request fetched into it."""

    def __init__(self, folder: Path, ttl: float, clock: Callable[[], datetime] = local_now, live_ttl: Optional[float] = None):
        self.folder, self.ttl, self._now = Path(folder), ttl, clock
        # Market data is never kept longer than the file; without a lifetime of its own it lives as long as the fundamentals.
        self.live_ttl = ttl if live_ttl is None else min(ttl, live_ttl)
        self._locks: dict[str, threading.Lock] = {}
        self._glock = threading.Lock()

    def _lock(self, symbol: str) -> threading.Lock:
        with self._glock:
            return self._locks.setdefault(symbol, threading.Lock())

    def lifetime(self, name: str) -> float:
        """How long the answer of a data call is kept: the market data's lifetime, or the fundamentals' (the file's)."""
        return self.live_ttl if config.is_live_call(name) else self.ttl

    def _fresh(self, stamp: datetime, now: datetime) -> bool:
        return 0 <= (now - stamp).total_seconds() < self.ttl   # a time in the future is not trusted either

    def _usable(self, name: str, at: datetime, now: datetime) -> bool:
        """Whether a call fetched at `at` can still be served from a fresh file. A fundamental can: it lives as long as the
        file, which the caller has checked. Market data lives for the shorter live lifetime, counted from its own time."""
        return not config.is_live_call(name) or 0 <= (now - at).total_seconds() < self.live_ttl

    def _files(self, symbol: str) -> list[tuple[datetime, Path]]:
        """The ticker's cache files, newest first (normally just one)."""
        found = []
        try:
            with os.scandir(self.folder) as it:
                names = [(e.name, e.path) for e in it if e.is_file()]
        except OSError:
            return found
        for name, path in names:
            m = _FILE.fullmatch(name)
            if m and m["symbol"] == symbol:
                try:
                    found.append((datetime.strptime(m["stamp"], STAMP), Path(path)))
                except ValueError:   # 99999999T999999+9999 is not a time
                    continue
        return sorted(found, reverse=True)

    def _read(self, path: Path, stamp: datetime) -> Optional[dict]:
        """The file's calls as {key: (data or Failure, when it was fetched)}, or None when it cannot be used."""
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if doc["version"] != VERSION:
                return None
            return {_key(c): (Failure(str(c["error"])) if "error" in c else c["data"], _when(c.get("at"), stamp))
                    for c in doc["calls"]}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            log.warning("cache: ignoring %s: unreadable or written by a different version", path.name)
            return None

    def open(self, symbol: str) -> Snapshot:
        """The ticker's snapshot for one request: what its fresh file still holds, otherwise an empty snapshot. Market data
        that has outlived its lifetime is left out, so the request fetches it again."""
        opened = self._now()
        for stamp, path in self._files(symbol):
            if self._fresh(stamp, opened):
                rows = self._read(path, stamp)
                if rows is not None:
                    held = {k: r for k, r in rows.items() if self._usable(k[0], r[1], opened)}
                    return Snapshot(symbol, opened, stamp, {k: r[0] for k, r in held.items()},
                                    {k: r[1] for k, r in held.items()}, self._now)
        return Snapshot(symbol, opened, clock=self._now)

    def save(self, snap: Snapshot) -> None:
        """Merge what a request fetched live into the ticker's file, or start a new file named with the current
        time. Never raises: a cache that cannot be written must not break the page."""
        if not snap.added:
            return
        try:
            with self._lock(snap.symbol):
                now = self._now()
                for stamp, path in self._files(snap.symbol):
                    rows = self._read(path, stamp) if self._fresh(stamp, now) else None
                    if rows is not None:
                        break   # a fresh file is already there (an earlier or a concurrent request): add to it
                else:
                    stamp, rows = snap.opened, {}
                    path = self.folder / file_name(snap.symbol, stamp)
                rows.update({k: (v, snap.added_at[k]) for k, v in snap.added.items()})
                # Market data that has outlived its lifetime can never be served again (a new expiry's chain, a new day's
                # date: each fetch makes a key of its own), so it would only pile up in a file that lives for days.
                rows = {k: r for k, r in rows.items() if self._usable(k[0], r[1], now)}
                self._write(path, snap.symbol, stamp, rows)
                for _, old in self._files(snap.symbol):   # one file per ticker: the stale ones go
                    if old != path:
                        old.unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001
            log.warning("cache: could not save %s: %s", snap.symbol, e)

    def discard(self, symbol: str) -> None:
        """Delete the ticker's cache file, so the next request fetches everything live. Never raises."""
        try:
            with self._lock(symbol):
                for _, path in self._files(symbol):
                    path.unlink(missing_ok=True)
        except OSError as e:
            log.warning("cache: could not delete the cache for %s: %s", symbol, e)

    def _write(self, path: Path, symbol: str, stamp: datetime, rows: dict) -> None:
        """One call per line, so the file stays readable. Written to a temporary file and renamed into place, so a
        reader never sees half a file."""
        self.folder.mkdir(parents=True, exist_ok=True)
        dump = lambda o: json.dumps(o, separators=(",", ":"), ensure_ascii=False)
        head = dump({"version": VERSION, "symbol": symbol, "fetched_at": utc_iso(stamp)})
        body = ",\n".join(dump({"call": k[0], "args": list(k[1:]), "at": utc_iso(at),
                                **({"error": v.message} if isinstance(v, Failure) else {"data": v})})
                          for k, (v, at) in rows.items())
        tmp = path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}.tmp")
        try:
            tmp.write_text(f'{head[:-1]},"calls":[\n{body}\n]}}\n', encoding="utf-8", newline="\n")
            for attempt in range(5):
                try:
                    os.replace(tmp, path)
                    return
                except PermissionError:   # Windows refuses while a reader has the old file open; that is brief
                    if attempt == 4:
                        raise
                    time.sleep(0.05 * (attempt + 1))
        finally:
            tmp.unlink(missing_ok=True)

    def describe(self, snap: Snapshot) -> dict:
        """What the page is told about where its data came from. `as_of` is when the file was made, and so how old its
        fundamentals are. `market_as_of` is the oldest market data the file supplied to this request, or None when it
        supplied none (all of it was fetched for this request)."""
        now = self._now()
        market = [t for k, t in snap.loaded_at.items() if k in snap.served and config.is_live_call(k[0])]
        oldest = min(market) if market else None
        return {"symbol": snap.symbol, "from_cache": snap.from_cache, "as_of": utc_iso(snap.stamp),
                "age_seconds": max(0, int((now - snap.stamp).total_seconds())), "ttl_seconds": int(self.ttl),
                "market_as_of": None if oldest is None else utc_iso(oldest),
                "market_age_seconds": None if oldest is None else max(0, int((now - oldest).total_seconds())),
                "market_ttl_seconds": int(self.live_ttl), "live_calls": len(snap.added)}

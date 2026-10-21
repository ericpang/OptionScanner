"""Per-ticker disk cache: what was fetched for a ticker, saved in the data folder so a repeat search can skip the fetch.

One file per ticker, named TICKER-YYYYMMDDTHHMMSS+HHMM.json after the time the data was fetched, in local time with
its UTC offset, for example AAPL.US-20261020T143210+0800.json. The file is a log of the data-layer calls made for that
ticker (`quote`, `kline`, `option_chain`, ...) and the data each returned. Replaying it through `Data.call` rebuilds
every panel with the same code that builds it from live data, so a page built from the cache is the same page, not a
second implementation.

- Fresh: the file is younger than the TTL (DISK_CACHE_MINUTES, 30 by default). Its data is used and nothing is fetched
  for those calls.
- Not in the file: a call the file does not hold (new option controls, "show more" news) is fetched live and added to
  the file, which keeps its time.
- Stale or absent: everything is fetched live and saved to a new file named with the current time; the old file goes.

The time in the file name is the only clock the app trusts, so renaming a file makes it older or younger.

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

log = logging.getLogger(__name__)

VERSION = 2   # bump when a provider method changes what it returns: files written by older code are then ignored
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


class Failure:
    """A call that failed in a way the app treats as harmless, remembered so that it is not tried on every search."""

    def __init__(self, message: str):
        self.message = message


class Snapshot:
    """One request's view of a ticker's cache: what the file already held, plus what this request fetched live."""

    def __init__(self, symbol: str, opened: datetime, stamp: Optional[datetime] = None, loaded: Optional[dict] = None):
        self.symbol = symbol
        self.opened = opened                 # when this request opened the snapshot
        self.stamp = stamp or opened         # when the cached data was fetched: the file's time, or `opened` if new
        self.loaded = loaded or {}           # key -> data (or a Failure) read from the file
        self.added: dict = {}                # key -> data (or a Failure) this request fetched live, to merge into the file
        self.served: set = set()             # keys answered from the file

    @property
    def from_cache(self) -> bool:
        return bool(self.served)

    def get(self, key: tuple):
        val = self.loaded.get(key, MISS)
        if val is not MISS:
            self.served.add(key)
        return val

    def add(self, key: tuple, value: Any) -> None:
        """Remember a result fetched live. The copy is taken now, before a caller can change `value` in place (the
        statements are, to fill in share counts), so the file holds what the source returned. Only data that reads
        back exactly as it was fetched is kept, so a cached page cannot differ from a live one."""
        try:
            pristine = json.loads(json.dumps(value))
        except (TypeError, ValueError) as e:
            log.warning("cache: %s for %s cannot be saved (%s)", key[0], self.symbol, e)
            return
        if pristine != value:   # tuples, non-string dict keys or NaN do not survive the round trip
            log.warning("cache: %s for %s is not saved: it does not read back as fetched", key[0], self.symbol)
            return
        self.added[key] = pristine

    def add_failure(self, key: tuple, message: str) -> None:
        self.added[key] = Failure(message)


class SnapshotStore:
    """The cache folder: finds a ticker's fresh file, and merges what a request fetched into it."""

    def __init__(self, folder: Path, ttl: float, clock: Callable[[], datetime] = local_now):
        self.folder, self.ttl, self._now = Path(folder), ttl, clock
        self._locks: dict[str, threading.Lock] = {}
        self._glock = threading.Lock()

    def _lock(self, symbol: str) -> threading.Lock:
        with self._glock:
            return self._locks.setdefault(symbol, threading.Lock())

    def _fresh(self, stamp: datetime, now: datetime) -> bool:
        return 0 <= (now - stamp).total_seconds() < self.ttl   # a time in the future is not trusted either

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

    def _read(self, path: Path) -> Optional[dict]:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if doc["version"] != VERSION:
                return None
            return {_key(c): Failure(str(c["error"])) if "error" in c else c["data"] for c in doc["calls"]}
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("cache: ignoring %s: unreadable or written by a different version", path.name)
            return None

    def open(self, symbol: str) -> Snapshot:
        """The ticker's snapshot for one request: its fresh file if there is one, otherwise an empty snapshot."""
        opened = self._now()
        for stamp, path in self._files(symbol):
            if self._fresh(stamp, opened):
                loaded = self._read(path)
                if loaded is not None:
                    return Snapshot(symbol, opened, stamp, loaded)
        return Snapshot(symbol, opened)

    def save(self, snap: Snapshot) -> None:
        """Merge what this request fetched live into the ticker's file, or start a new file named with the current
        time. Never raises: a cache that cannot be written must not break the page."""
        if not snap.added:
            return
        try:
            with self._lock(snap.symbol):
                now = self._now()
                for stamp, path in self._files(snap.symbol):
                    calls = self._read(path) if self._fresh(stamp, now) else None
                    if calls is not None:
                        break   # a fresh file is already there (an earlier or a concurrent request): add to it
                else:
                    stamp, calls = snap.opened, {}
                    path = self.folder / file_name(snap.symbol, stamp)
                calls.update(snap.added)
                self._write(path, snap.symbol, stamp, calls)
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

    def _write(self, path: Path, symbol: str, stamp: datetime, calls: dict) -> None:
        """One call per line, so the file stays readable. Written to a temporary file and renamed into place, so a
        reader never sees half a file."""
        self.folder.mkdir(parents=True, exist_ok=True)
        dump = lambda o: json.dumps(o, separators=(",", ":"), ensure_ascii=False)
        head = dump({"version": VERSION, "symbol": symbol, "fetched_at": utc_iso(stamp)})
        rows = ",\n".join(dump({"call": k[0], "args": list(k[1:]), **({"error": v.message} if isinstance(v, Failure) else {"data": v})})
                          for k, v in calls.items())
        tmp = path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}.tmp")
        try:
            tmp.write_text(f'{head[:-1]},"calls":[\n{rows}\n]}}\n', encoding="utf-8", newline="\n")
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
        """What the page is told about where its data came from."""
        return {"symbol": snap.symbol, "from_cache": snap.from_cache, "as_of": utc_iso(snap.stamp),
                "age_seconds": max(0, int((self._now() - snap.stamp).total_seconds())), "ttl_seconds": int(self.ttl),
                "live_calls": len(snap.added)}

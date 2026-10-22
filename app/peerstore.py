"""Computed figures for other companies (the peer group), kept for days.

A peer costs about a dozen CLI calls to measure, and a company's fundamentals change a quarter at a time, so what is
computed for a peer is reused for PEER_CACHE_DAYS (default 7). This is not the per-ticker cache of snapshot.py, which
holds raw data calls for 30 minutes: it holds the few figures the comparison needs, for every company ever used as a
peer, in one file (data/peers.json). Demo mode keeps them in memory only, and a lifetime of 0 keeps nothing.
"""
import copy
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

VERSION = 2   # bump when a figure's definition changes or one is added: figures computed by older code are then ignored


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PeerStore:
    """symbol -> what was computed for it, with the time it was computed. `path` None keeps everything in memory."""

    def __init__(self, path: Optional[Path], ttl: float, clock: Callable[[], datetime] = _utc_now):
        self.path, self.ttl, self._now = (Path(path) if path else None), ttl, clock
        self._lock = threading.Lock()
        self._entries: Optional[dict] = None   # read from the file on first use

    def _load(self) -> dict:
        if self._entries is None:
            self._entries = {}
            if self.path is not None:
                try:
                    doc = json.loads(self.path.read_text(encoding="utf-8"))
                    if doc["version"] == VERSION:
                        self._entries = {k: v for k, v in doc["peers"].items() if isinstance(v, dict)}
                except FileNotFoundError:
                    pass
                except (OSError, ValueError, KeyError, TypeError, AttributeError):
                    log.warning("peers: ignoring %s: unreadable or written by a different version", self.path.name)
        return self._entries

    def _fresh(self, entry: dict, now: datetime) -> bool:
        """Fresh for the store's lifetime, or for the entry's own `keep_seconds` when that is shorter (a note that a
        peer could not be fetched is kept for an hour, not a week)."""
        try:
            age = (now - datetime.fromisoformat(entry["fetched_at"])).total_seconds()
            limit = min(self.ttl, entry["keep_seconds"]) if "keep_seconds" in entry else self.ttl
        except (KeyError, TypeError, ValueError):
            return False
        return 0 <= age < limit   # a time in the future is not trusted either

    def get(self, symbol: str) -> Optional[dict]:
        """A copy of what is held for the symbol if it is fresh, else None."""
        with self._lock:
            entry = self._load().get(symbol)
            return copy.deepcopy(entry) if entry is not None and self._fresh(entry, self._now()) else None

    def put(self, symbol: str, entry: dict) -> dict:
        """Keep `entry` for the symbol, stamped with the current time, and return it as stamped. Never raises: a store
        that cannot be written must not break the page. With a lifetime of 0 nothing is kept."""
        with self._lock:
            now = self._now()
            stamped = {**copy.deepcopy(entry), "fetched_at": now.isoformat(timespec="seconds")}
            if self.ttl > 0:
                self._load()[symbol] = stamped
                self._save(now)
            return copy.deepcopy(stamped)

    def discard(self, symbol: str) -> None:
        with self._lock:
            if self._load().pop(symbol, None) is not None:
                self._save(self._now())

    def _save(self, now: datetime) -> None:
        """Drop what has expired, then write the file: one symbol per line, to a temporary file renamed into place so a
        reader never sees half a file."""
        entries = self._load()
        for symbol in [s for s, e in entries.items() if not self._fresh(e, now)]:
            del entries[symbol]
        if self.path is None:
            return
        dump = lambda o: json.dumps(o, separators=(",", ":"), ensure_ascii=False)
        rows = ",\n".join(f"{dump(s)}:{dump(e)}" for s, e in sorted(entries.items()))
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}-{threading.get_ident()}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(f'{{"version":{VERSION},"peers":{{\n{rows}\n}}}}\n', encoding="utf-8", newline="\n")
            for attempt in range(5):
                try:
                    os.replace(tmp, self.path)
                    return
                except PermissionError:   # Windows refuses while a reader has the old file open; that is brief
                    if attempt == 4:
                        raise
                    time.sleep(0.05 * (attempt + 1))
        except OSError as e:
            log.warning("peers: could not save %s: %s", self.path.name, e)
        finally:
            tmp.unlink(missing_ok=True)

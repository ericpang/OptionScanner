"""Latest news (deduped, newest first, stale flag), recent filings and market-mood facts. No sentiment scoring."""
import re
from datetime import date, datetime, timezone
from typing import Optional

from . import config


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9一-鿿]+", " ", t.lower()).strip()


def _tokens(t: str) -> set:
    return set(_norm(t).split())


def parse_date(s: str) -> Optional[date]:
    s = (s or "").strip()
    if not s:
        return None
    if s.isdigit() and len(s) >= 10:
        try:
            return datetime.fromtimestamp(int(s[:10]), tz=timezone.utc).date()
        except (ValueError, OSError):
            return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def dedupe(items: list[dict]) -> list[dict]:
    kept: list[dict] = []
    for it in items:
        tk = _tokens(it["title"])
        dup = False
        for k in kept:
            kt = _tokens(k["title"])
            if _norm(it["title"]) == _norm(k["title"]) or (tk and kt and len(tk & kt) / len(tk | kt) >= 0.8):
                dup = True
                break
        if not dup:
            kept.append(it)
    return kept


def group_label(d: Optional[date], today: date) -> str:
    if d is None:
        return "Earlier"
    delta = (today - d).days
    return "Today" if delta <= 0 else "Yesterday" if delta == 1 else "This week" if delta <= 7 else "Earlier"


def build(items: list[dict], filings: list[dict], mood: dict, limit: int, source="longbridge") -> dict:
    limit = max(1, min(limit, config.NEWS_MAX))
    today = date.today()
    rows = []
    for it in items:
        d = parse_date(it.get("published_at", ""))
        rows.append({**it, "_d": d})
    rows.sort(key=lambda x: x["_d"] or date.min, reverse=True)
    out = []
    for it in dedupe(rows)[:limit]:
        d = it.pop("_d")
        out.append({**it, "published_at": d.isoformat() if d else it.get("published_at", ""),
                    "stale": bool(d and (today - d).days > config.NEWS_STALE_DAYS),
                    "group": group_label(d, today), "source": source})
    return {"news": out, "filings": filings[:5], "mood": mood, "limit": limit,
            "can_show_more": limit < config.NEWS_MAX}

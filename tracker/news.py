"""Fetch headlines from RSS feeds (title, publisher, time, link only)."""
from __future__ import annotations

import calendar
import hashlib
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import feedparser
import requests

from .rules import TZ

UA = "Mozilla/5.0 (compatible; memshala-2026-tracker/1.0; +https://github.com/gabimansoure-coder/memshala-2026)"
SECOND_LEVEL = {"co", "org", "gov", "ac", "net", "muni", "k12", "idf"}


@dataclass
class Headline:
    id: str
    title: str
    link: str
    publisher: str   # display name
    domain: str      # registrable domain of the publisher
    group: str       # ownership group, for the "independent sources" rule
    published: str   # ISO, Israel time
    feed: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def registrable_domain(host: str | None) -> str:
    host = (host or "").lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in SECOND_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def normalize_title(t: str) -> str:
    t = re.sub(r"[\"'״׳“”‘’`.,:;!?()\[\]{}|–—\-]", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def group_of(domain: str, cfg: dict) -> str:
    for group, domains in (cfg.get("publisher_groups") or {}).items():
        if domain in domains:
            return group
    return domain


def is_relevant(title: str, cfg: dict) -> bool:
    low = title.lower()
    return any(k.lower() in low for k in cfg.get("relevance") or [])


def _published(entry: Any) -> datetime | None:
    st = entry.get("published_parsed") or entry.get("updated_parsed")
    if not st:
        return None
    return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc).astimezone(TZ)


def parse_feed(content: bytes, feed: dict, cfg: dict) -> list[Headline]:
    parsed = feedparser.parse(content)
    names = cfg.get("publisher_names") or {}
    out: list[Headline] = []
    for e in parsed.entries:
        title = re.sub(r"\s+", " ", (e.get("title") or "")).strip()
        link = e.get("link") or ""
        if not title or not link:
            continue
        src = e.get("source") or {}
        if src.get("href"):                     # Google News: original publisher
            domain = registrable_domain(urlparse(src["href"]).hostname)
            publisher = names.get(domain) or (src.get("title") or domain)
            suffix = " - " + (src.get("title") or "")
            if src.get("title") and title.endswith(suffix):
                title = title[: -len(suffix)].strip()
        else:
            domain = feed.get("publisher") or registrable_domain(urlparse(link).hostname)
            publisher = names.get(domain, domain)
        pub = _published(e)
        if pub is None:
            continue
        group = group_of(domain, cfg)
        hid = hashlib.sha1(f"{group}|{normalize_title(title)}".encode()).hexdigest()[:16]
        out.append(Headline(hid, title, link, publisher, domain, group, pub.isoformat(), feed["name"]))
    return out


def fetch_all(cfg: dict, now: datetime, session: requests.Session | None = None) -> tuple[list[Headline], list[str]]:
    """Relevant headlines from all feeds within the lookback window, de-duplicated."""
    s = session or requests.Session()
    s.headers["User-Agent"] = UA
    errors: list[str] = []
    seen: dict[str, Headline] = {}
    cutoff = now - timedelta(days=int(cfg.get("lookback_days", 10)))
    allowed = set(cfg.get("israeli_sources") or [])
    for feed in cfg.get("feeds") or []:
        try:
            r = s.get(feed["url"], timeout=20)
            r.raise_for_status()
            items = parse_feed(r.content, feed, cfg)
        except Exception as e:  # noqa: BLE001 - one broken feed must not stop the run
            errors.append(f"{feed['name']}: {e}")
            continue
        for h in items:
            if allowed and h.domain not in allowed:
                continue
            if not is_relevant(h.title, cfg) or datetime.fromisoformat(h.published) < cutoff:
                continue
            if datetime.fromisoformat(h.published) > now + timedelta(hours=1):
                continue
            prev = seen.get(h.id)
            if prev is None or h.published < prev.published:
                seen[h.id] = h
    return sorted(seen.values(), key=lambda h: h.published, reverse=True), errors


def news_for_site(headlines: list[Headline], limit: int = 30) -> list[dict[str, Any]]:
    out, titles = [], set()
    for h in headlines:
        key = normalize_title(h.title)
        if key in titles:
            continue
        titles.add(key)
        out.append({"title": h.title, "publisher": h.publisher, "published": h.published, "link": h.link})
        if len(out) >= limit:
            break
    return out

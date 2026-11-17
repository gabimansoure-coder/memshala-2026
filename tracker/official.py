"""Official events from the Knesset's open data.

The Cloudflare Worker collects the Knesset's OData every 10 minutes and serves it at /live.json
(plenary sessions with their agenda, votes with for/against counts, the current Speaker).
An event found there comes from the official record, so it is applied without waiting for
two news sources — but it still goes through the rules engine like everything else.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

import requests

from .classify import Detection
from .news import UA, Headline
from .rules import TZ

GROUP = "knesset-official"
PUBLISHER = "הכנסת — מאגר המידע הרשמי"

PRESENT_GOVERNMENT = re.compile(
    r"הצגת\s+הממשלה|הודעת\s+הממשלה\s+על\s+(הרכבתה|כינונה)|כינון\s+הממשלה|הבעת\s+אמון\s+ב?ממשלה")
CONFIDENCE_VOTE = re.compile(r"אמון\s+ב?ממשל|ממשל\S*\s.{0,30}אמון|כינון\s+הממשלה")
DISSOLUTION = re.compile(r"התפזרות\s+הכנסת")
THIRD_READING = re.compile(r"שלישית")
SPEAKER_ITEM = re.compile(r"בחירת\s+יו(שב)?[\"״]?(ר|\s+ראש)\s+הכנסת")


def fetch_live(url: str | None) -> dict | None:
    if not url:
        return None
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError):
        return None


def _local(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(TZ)


def _headline(hid: str, title: str, link: str, when: datetime) -> Headline:
    return Headline(hid, title, link, PUBLISHER, "knesset.gov.il", GROUP, when.isoformat(), "knesset")


def detect(live: dict | None) -> list[tuple[Detection, Headline]]:
    """Formation events in the Knesset's official data."""
    k = (live or {}).get("knesset") or {}
    source = k.get("source") or "https://knesset.gov.il/OdataV4/ParliamentInfo/"
    out: list[tuple[Detection, Headline]] = []

    for s in (k.get("upcoming") or []) + (k.get("recent") or []):
        start = _local(s["start"])
        link = f"{source}KNS_PlenumSession({s['id']})"
        for item in s.get("items") or []:
            name = item.get("name") or ""
            if PRESENT_GOVERNMENT.search(name):
                h = _headline(f"knesset-session-{s['id']}-gov", f"{s['name']}: {name}", link, start)
                out.append((Detection(h.id, "confidence_vote_scheduled", start.date(), time=start.strftime("%H:%M"),
                                      mode="official", confidence="high"), h))
            elif SPEAKER_ITEM.search(name) and s.get("finish"):
                speaker = k.get("speaker") or {}
                # use the name only once the Knesset updated the Speaker record for this election
                if speaker.get("name") and speaker.get("since") and _local(speaker["since"]).date() >= start.date():
                    h = _headline(f"knesset-session-{s['id']}-speaker", f"{s['name']}: {name}", link, start)
                    out.append((Detection(h.id, "speaker_elected", start.date(), candidate=speaker["name"],
                                          mode="official", confidence="high"), h))

    for v in k.get("votes") or []:
        title = v.get("title") or ""
        counts = v.get("counts") or {}
        if v.get("no_confidence") or not counts.get("total"):
            continue
        when = _local(v["time"])
        h = _headline(f"knesset-vote-{v['id']}", title, v.get("url") or source, when)
        if DISSOLUTION.search(title):
            if THIRD_READING.search(title) and v.get("outcome") == "accepted":
                out.append((Detection(h.id, "dissolution_law", when.date(), mode="official", confidence="high"), h))
        elif CONFIDENCE_VOTE.search(title):
            out.append((Detection(h.id, "confidence_vote", when.date(), time=when.strftime("%H:%M"),
                                  result="passed" if v.get("outcome") == "accepted" else "failed",
                                  votes_for=counts.get("for"), votes_against=counts.get("against"),
                                  mode="official", confidence="high"), h))
    return out


def summary(live: dict | None) -> dict[str, Any]:
    k = (live or {}).get("knesset") or {}
    return {"updated_at": (live or {}).get("updated_at"), "sessions": len(k.get("upcoming") or []) + len(k.get("recent") or []),
            "votes": len(k.get("votes") or []), "speaker": (k.get("speaker") or {}).get("name")}

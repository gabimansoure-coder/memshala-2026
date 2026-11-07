"""RFC 5545 calendar writer.

* Time zone Asia/Jerusalem with a full VTIMEZONE (Israeli DST rules since 2013).
* Stable UIDs derived from each item's logical key, never random.
* SEQUENCE / LAST-MODIFIED / DTSTAMP come from data/ics_registry.json and change only
  when the event's content changes, so the file is byte-identical between runs that
  did not change anything.
* Items that are no longer relevant are simply absent: subscribed calendars drop them
  on the next refresh.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from .calendar_il import hebrew_date
from .rules import Item, Node

TZID = "Asia/Jerusalem"
PRODID = "-//memshala-2026//Government formation tracker//HE"

VTIMEZONE = [
    "BEGIN:VTIMEZONE",
    f"TZID:{TZID}",
    f"X-LIC-LOCATION:{TZID}",
    "BEGIN:DAYLIGHT",
    "TZOFFSETFROM:+0200",
    "TZOFFSETTO:+0300",
    "TZNAME:IDT",
    "DTSTART:20130329T020000",
    # the Friday before the last Sunday of March, 02:00
    "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=FR;BYMONTHDAY=23,24,25,26,27,28,29",
    "END:DAYLIGHT",
    "BEGIN:STANDARD",
    "TZOFFSETFROM:+0300",
    "TZOFFSETTO:+0200",
    "TZNAME:IST",
    "DTSTART:20131027T020000",
    # the last Sunday of October, 02:00
    "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU",
    "END:STANDARD",
    "END:VTIMEZONE",
]

STAMP_PROPS = ("DTSTAMP", "LAST-MODIFIED", "CREATED", "SEQUENCE")


def escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\n").replace("\n", "\\n"))


def fold(line: str) -> str:
    """Fold a content line at 75 octets without splitting UTF-8 sequences."""
    out: list[bytes] = []
    cur = b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > 75:
            out.append(cur)
            cur = b" " + b
        else:
            cur += b
    out.append(cur)
    return "\r\n".join(part.decode("utf-8") for part in out)


def utc_stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@dataclass
class CalEvent:
    key: str
    summary: str
    description: str
    day: date
    start: time | None = None
    minutes: int = 60
    end_day: date | None = None
    location: str | None = None
    url: str | None = None
    tentative: bool = False
    alarm: bool = False
    categories: list[str] = field(default_factory=list)

    def body(self, uid: str) -> list[str]:
        lines = [f"UID:{uid}", f"SUMMARY:{escape(self.summary)}"]
        if self.start is None:
            last = self.end_day or self.day
            lines += [f"DTSTART;VALUE=DATE:{self.day:%Y%m%d}",
                      f"DTEND;VALUE=DATE:{last + timedelta(days=1):%Y%m%d}",
                      "TRANSP:TRANSPARENT"]
        else:
            s = datetime.combine(self.day, self.start)
            e = s + timedelta(minutes=self.minutes)
            lines += [f"DTSTART;TZID={TZID}:{s:%Y%m%dT%H%M%S}",
                      f"DTEND;TZID={TZID}:{e:%Y%m%dT%H%M%S}",
                      "TRANSP:OPAQUE"]
        lines.append(f"DESCRIPTION:{escape(self.description)}")
        if self.location:
            lines.append(f"LOCATION:{escape(self.location)}")
        if self.url:
            lines.append(f"URL;VALUE=URI:{self.url}")
        if self.categories:
            lines.append("CATEGORIES:" + ",".join(escape(c) for c in self.categories))
        lines.append("STATUS:" + ("TENTATIVE" if self.tentative else "CONFIRMED"))
        lines.append("CLASS:PUBLIC")
        if self.alarm:
            # all-day item: 09:00 on the previous day (Apple's "1 day before" for all-day events)
            trigger = "-PT15H" if self.start is None else "-P1D"
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{escape('מחר: ' + self.summary)}",
                      f"TRIGGER:{trigger}", "END:VALARM"]
        return lines


def describe(item: Item, site_url: str) -> str:
    parts: list[str] = []
    if item.explanation:
        parts.append(item.explanation)
    parts.extend(item.notes)
    parts.append("")
    parts.append("סטטוס: " + ("רשמי — הוכרז" if item.kind == "official" else "מחושב — נגזר מהחוק, טרם הוכרז"))
    parts.append("תאריך עברי: " + hebrew_date(item.day))
    if item.law:
        parts.append(f"בסיס חוקי: {item.law.name}")
        parts.append(item.law.url)
    if item.source:
        parts.append(f"מקור: {item.source}")
    parts.append(f"מעקב מלא: {site_url}")
    return "\n".join(parts).strip()


def from_item(item: Item, site_url: str) -> CalEvent:
    summary = item.title + (" (מחושב)" if item.kind == "computed" else "")
    return CalEvent(
        key=item.key, summary=summary, description=describe(item, site_url), day=item.day,
        start=item.start, minutes=item.minutes, end_day=item.end_day, location=item.location,
        url=item.source or (item.law.url if item.law else None), tentative=item.kind == "computed",
        alarm=item.alarm and item.category in ("deadline", "possible"),
        categories=["רשמי" if item.kind == "official" else "מחושב"])


def scenario_events(tree: Node, site_url: str) -> list[CalEvent]:
    out: list[CalEvent] = []

    def walk(node: Node, path: list[str]) -> None:
        here = path + [node.title]
        if node.cal and node.day:
            desc = [node.note] if node.note else []
            desc += ["", "המסלול: " + " ← ".join(here[1:]),
                     "המועד הוא " + ("המאוחר ביותר האפשרי במסלול הזה." if node.latest else "המועד המחושב."),
                     "סטטוס: מחושב — תרחיש היפותטי, לא אירוע מתוכנן",
                     "תאריך עברי: " + hebrew_date(node.day)]
            if node.law:
                desc += [f"בסיס חוקי: {node.law.name}", node.law.url]
            desc.append(f"מעקב מלא: {site_url}")
            out.append(CalEvent(key=f"scn-{node.id}", summary=node.cal + " (מחושב)", description="\n".join(desc).strip(),
                                day=node.day, url=site_url, tentative=True, categories=["תרחיש", "מחושב"]))
        for c in node.children:
            walk(c, here)

    walk(tree, [])
    return out


def render(events: list[CalEvent], *, name: str, description: str, uid_domain: str,
           registry: dict, now: datetime) -> str:
    """Render the calendar, updating `registry` in place (sequence & timestamps)."""
    now_s = utc_stamp(now)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             f"X-WR-CALNAME:{escape(name)}", f"NAME:{escape(name)}",
             f"X-WR-CALDESC:{escape(description)}", f"DESCRIPTION:{escape(description)}",
             f"X-WR-TIMEZONE:{TZID}", "REFRESH-INTERVAL;VALUE=DURATION:PT1H", "X-PUBLISHED-TTL:PT1H",
             *VTIMEZONE]
    seen: set[str] = set()
    for ev in sorted(events, key=lambda e: (e.day, e.start or time(0), e.key)):
        uid = f"{ev.key}@{uid_domain}"
        if uid in seen:
            raise ValueError(f"duplicate UID {uid}")
        seen.add(uid)
        body = ev.body(uid)
        digest = hashlib.sha256("\n".join(body).encode("utf-8")).hexdigest()
        rec = registry.get(uid)
        if rec is None:
            rec = registry[uid] = {"hash": digest, "sequence": 0, "created": now_s, "modified": now_s}
        elif rec["hash"] != digest:
            rec.update(hash=digest, sequence=rec["sequence"] + 1, modified=now_s)
        lines += ["BEGIN:VEVENT", body[0], f"DTSTAMP:{rec['modified']}", f"CREATED:{rec['created']}",
                  f"LAST-MODIFIED:{rec['modified']}", f"SEQUENCE:{rec['sequence']}", *body[1:], "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "".join(fold(line) + "\r\n" for line in lines)

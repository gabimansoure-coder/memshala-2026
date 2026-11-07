"""Pre-deploy check: every .ics must parse and follow the RFC 5545 rules we rely on.

Usage: python -m tracker.validate_ics _site/calendar.ics _site/scenarios.ics
Exits non-zero on any problem, which stops the workflow before deployment.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import icalendar


def validate(raw: bytes) -> list[str]:
    errors: list[str] = []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        return [f"not UTF-8: {e}"]
    if not text.endswith("\r\n"):
        errors.append("file must end with CRLF")
    if "\n" in text.replace("\r\n", ""):
        errors.append("bare LF line break (lines must end with CRLF)")
    physical = text.split("\r\n")[:-1]
    for n, line in enumerate(physical, 1):
        if len(line.encode("utf-8")) > 75:
            errors.append(f"line {n} longer than 75 octets")
    # unfold for structural checks
    logical: list[str] = []
    for line in physical:
        if line.startswith((" ", "\t")) and logical:
            logical[-1] += line[1:]
        else:
            logical.append(line)
    stack: list[str] = []
    for line in logical:
        if line.startswith("BEGIN:"):
            stack.append(line[6:])
        elif line.startswith("END:"):
            if not stack or stack.pop() != line[4:]:
                errors.append(f"unbalanced {line}")
    if stack:
        errors.append(f"unclosed components: {stack}")
    if not logical or logical[0] != "BEGIN:VCALENDAR" or logical[-1] != "END:VCALENDAR":
        errors.append("must start with BEGIN:VCALENDAR and end with END:VCALENDAR")

    try:
        cal = icalendar.Calendar.from_ical(text)
    except Exception as e:  # noqa: BLE001 - any parser failure blocks the deploy
        return errors + [f"icalendar failed to parse: {e}"]
    for prop in ("VERSION", "PRODID", "X-WR-CALNAME", "REFRESH-INTERVAL", "X-PUBLISHED-TTL"):
        if prop not in cal:
            errors.append(f"missing calendar property {prop}")
    tzs = [c for c in cal.walk("VTIMEZONE")]
    if len(tzs) != 1 or str(tzs[0].get("TZID")) != "Asia/Jerusalem":
        errors.append("expected exactly one VTIMEZONE for Asia/Jerusalem")

    uids: set[str] = set()
    events = list(cal.walk("VEVENT"))
    for ev in events:
        uid = str(ev.get("UID", ""))
        where = f"event {uid or '?'}"
        for prop in ("UID", "DTSTAMP", "DTSTART", "SUMMARY", "SEQUENCE"):
            if prop not in ev:
                errors.append(f"{where}: missing {prop}")
        if uid in uids:
            errors.append(f"{where}: duplicate UID")
        uids.add(uid)
        try:
            start = ev.decoded("DTSTART")
            end = ev.decoded("DTEND") if "DTEND" in ev else None
        except Exception as e:  # noqa: BLE001
            errors.append(f"{where}: bad DTSTART/DTEND: {e}")
            continue
        if isinstance(start, datetime):
            if start.tzinfo is None or "Jerusalem" not in str(ev["DTSTART"].params.get("TZID", "")):
                errors.append(f"{where}: timed event must use TZID=Asia/Jerusalem")
        elif not isinstance(start, date):
            errors.append(f"{where}: DTSTART is not a date")
        if end is not None and end <= start:
            errors.append(f"{where}: DTEND must be after DTSTART")
        for alarm in ev.walk("VALARM"):
            if "TRIGGER" not in alarm or "ACTION" not in alarm:
                errors.append(f"{where}: VALARM needs ACTION and TRIGGER")
    return errors


def check_vtimezone(raw: bytes, years=range(2026, 2031)) -> list[str]:
    """The VTIMEZONE rules must give the same offsets as the tz database."""
    cal = icalendar.Calendar.from_ical(raw.decode("utf-8"))
    tz = cal.walk("VTIMEZONE")[0].to_tz()
    ref = ZoneInfo("Asia/Jerusalem")
    errors = []
    for y in years:
        t = datetime(y, 1, 1, 12)
        while t.year == y:
            a = t.replace(tzinfo=tz).utcoffset()
            b = t.replace(tzinfo=ref).utcoffset()
            if a != b:
                errors.append(f"VTIMEZONE offset {a} != tzdata {b} at {t}")
                break
            t += timedelta(days=1)
    return errors


def main(paths: list[str]) -> int:
    bad = 0
    for p in paths:
        raw = Path(p).read_bytes()
        errs = validate(raw) or check_vtimezone(raw)
        if errs:
            bad += 1
            print(f"✗ {p}")
            for e in errs:
                print(f"   - {e}")
        else:
            n = raw.count(b"BEGIN:VEVENT")
            print(f"✓ {p}: {n} events, valid")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

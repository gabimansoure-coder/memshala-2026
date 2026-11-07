from datetime import date, datetime, timedelta

import icalendar

from tracker import ics
from tracker.rules import TZ, compute
from tracker.state_io import load_state
from tracker.validate_ics import check_vtimezone, validate

SITE = "https://example.github.io/memshala-2026/"
DOMAIN = "memshala-2026.test"


def render_state(state, now, registry):
    res = compute(state, now)
    events = [ics.from_item(it, SITE) for it in res.items if it.calendar]
    return ics.render(events, name="הרכבת הממשלה 2026", description="בדיקה", uid_domain=DOMAIN,
                      registry=registry, now=now)


def vevents(text):
    cal = icalendar.Calendar.from_ical(text)
    return {str(e["UID"]).split("@")[0]: e for e in cal.walk("VEVENT")}


NOW = datetime(2026, 11, 7, 22, tzinfo=TZ)


def test_current_calendar_is_valid():
    text = render_state(load_state(), NOW, {})
    raw = text.encode("utf-8")
    assert validate(raw) == []
    assert check_vtimezone(raw) == []
    assert "X-WR-CALNAME:הרכבת הממשלה 2026" in text
    assert "REFRESH-INTERVAL;VALUE=DURATION:PT1H" in text
    assert "X-PUBLISHED-TTL:PT1H" in text


def test_timed_and_all_day_events():
    ev = vevents(render_state(load_state(), NOW, {}))
    ceremony = ev["mandate-r1"]
    start = ceremony.decoded("DTSTART")
    assert isinstance(start, datetime) and start.hour == 17
    assert str(ceremony["DTSTART"].params["TZID"]) == "Asia/Jerusalem"
    deadline = ev["r1-deadline"]
    assert deadline.decoded("DTSTART") == date(2026, 12, 6)
    assert deadline.decoded("DTEND") == date(2026, 12, 7)
    assert str(deadline["SUMMARY"]).endswith("(מחושב)")
    assert not str(ceremony["SUMMARY"]).endswith("(מחושב)")
    desc = str(deadline["DESCRIPTION"])
    assert "סעיף 8" in desc and "כ״ו כסלו" in desc and "lawitemid=2000040" in desc


def test_alarm_day_before_each_deadline():
    ev = vevents(render_state(load_state(), NOW, {}))
    for key in ("r1-deadline", "r1-max-extension", "appeals-deadline"):
        alarms = ev[key].walk("VALARM")
        assert len(alarms) == 1
        assert alarms[0].decoded("TRIGGER") == -timedelta(hours=15)   # 09:00 the day before
    assert ev["mandate-r1"].walk("VALARM") == []


def test_identical_rebuild_is_byte_identical():
    reg = {}
    a = render_state(load_state(), NOW, reg)
    b = render_state(load_state(), NOW + timedelta(hours=5), reg)
    assert a == b


def test_extension_updates_same_uid_and_bumps_sequence_and_removes_irrelevant():
    reg = {}
    state = load_state()
    first = vevents(render_state(state, NOW, reg))
    assert int(first["r1-deadline"]["SEQUENCE"]) == 0
    state["events"].append({"id": "ext", "type": "extension_granted", "date": date(2026, 12, 3), "days": 14})
    later = NOW + timedelta(days=27)
    second = vevents(render_state(state, later, reg))
    assert second["r1-deadline"].decoded("DTSTART") == date(2026, 12, 20)
    assert int(second["r1-deadline"]["SEQUENCE"]) == 1          # same UID, updated
    assert "r1-max-extension" not in second                      # no longer relevant -> removed
    assert int(second["mandate-r1"]["SEQUENCE"]) == 0            # untouched events keep their sequence


def test_early_return_recomputes_following_dates():
    reg = {}
    state = load_state()
    state["events"].append({"id": "ret", "type": "mandate_returned", "date": date(2026, 11, 25)})
    ev = vevents(render_state(state, datetime(2026, 11, 25, 22, tzinfo=TZ), reg))
    assert "r1-deadline" not in ev and "r1-max-extension" not in ev
    assert ev["r1-president-decision"].decoded("DTSTART") == date(2026, 11, 28)


def test_fold_never_splits_multibyte_characters():
    line = "SUMMARY:" + "הרכבת הממשלה " * 20
    folded = ics.fold(line)
    for part in folded.split("\r\n"):
        assert len(part.encode("utf-8")) <= 75
        part.encode("utf-8").decode("utf-8")
    assert folded.replace("\r\n ", "") == line


def test_scenarios_calendar_valid():
    res = compute(load_state(), NOW)
    text = ics.render(ics.scenario_events(res.scenarios, SITE), name="תרחישים", description="בדיקה",
                      uid_domain=DOMAIN, registry={}, now=NOW)
    assert validate(text.encode("utf-8")) == []
    ev = vevents(text)
    assert all(k.startswith("scn-") for k in ev)
    assert all(str(e["SUMMARY"]).startswith("תרחיש:") and str(e["SUMMARY"]).endswith("(מחושב)") for e in ev.values())

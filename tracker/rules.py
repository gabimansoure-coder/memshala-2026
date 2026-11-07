"""מנוע הכללים — computes the government-formation timeline from state.yaml.

The rules follow the official text of Basic Law: The Government (sections 7-13),
the Interpretation Law 5741-1981 (section 10) and the Knesset Elections Law
(section 86(b)). Each computed item records the section it is derived from.

Counting rules (verified against the statute text and past cases, see tests/):
* Interpretation Law s.10(a): the day of the triggering event is not counted, so a
  period of N days from day A ends at the end of day A+N (2021: 6.4 + 28 = 4.5).
* Interpretation Law s.10(c): rest days count, "unless they are the last days of the
  period". In practice the President's office did not shift a mandate deadline that
  fell on Shabbat (2013: deadline Saturday 2.3.2013, extension to Saturday 16.3.2013),
  so by default deadlines are not shifted, only flagged. `rules.rest_day_shift: true`
  in state.yaml switches to shifting to the next working day.
* Basic Law s.11(b): elections on the last Tuesday on or before day N+90, where N is the
  President's notice (or the day the Knesset rejected the government).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .calendar_il import holiday_name, next_working_day, rest_day_name

TZ = ZoneInfo("Asia/Jerusalem")

KNESSET_DB = "https://main.knesset.gov.il/Activity/Legislation/Laws/Pages/LawPrimary.aspx?lawitemid="
URL_GOV = KNESSET_DB + "2000040"
URL_INTERP = KNESSET_DB + "2000510"
URL_ELECTIONS = KNESSET_DB + "2000191"
URL_KNESSET = KNESSET_DB + "2000037"

FIRST_PERIOD = 28      # s.8
MAX_EXTENSION = 14     # s.8
SECOND_PERIOD = 28     # s.9(c)
PRESIDENT_DECISION = 3  # s.9(a)
MAJORITY_WINDOW = 21   # s.10(a)
MAJORITY_ASSIGN = 2    # s.10(b)
THIRD_PERIOD = 14      # s.10(c)
CONFIDENCE_SESSION = 7  # s.13(b)
ELECTIONS_DAYS = 90    # s.11(b)
APPEALS_DAYS = 14      # Elections Law s.86(b)
ASSIGN_DAYS = 7        # s.7(a)

# A period is treated as expired (when nothing was reported) only after this much
# time past the end of its last day, so a last-minute announcement can still land.
EXPIRY_GRACE = timedelta(hours=12)


@dataclass(frozen=True)
class Law:
    name: str
    url: str


S7 = Law("חוק־יסוד: הממשלה, סעיף 7(א)", URL_GOV)
S8 = Law("חוק־יסוד: הממשלה, סעיף 8", URL_GOV)
S9A = Law("חוק־יסוד: הממשלה, סעיף 9(א)", URL_GOV)
S9C = Law("חוק־יסוד: הממשלה, סעיף 9(ג)", URL_GOV)
S10A = Law("חוק־יסוד: הממשלה, סעיף 10(א)", URL_GOV)
S10B = Law("חוק־יסוד: הממשלה, סעיף 10(ב)", URL_GOV)
S10C = Law("חוק־יסוד: הממשלה, סעיף 10(ג)", URL_GOV)
S11 = Law("חוק־יסוד: הממשלה, סעיף 11", URL_GOV)
S12 = Law("חוק־יסוד: הממשלה, סעיף 12", URL_GOV)
S13B = Law("חוק־יסוד: הממשלה, סעיף 13(ב)", URL_GOV)
S13D = Law("חוק־יסוד: הממשלה, סעיף 13(ד)", URL_GOV)
E86 = Law("חוק הבחירות לכנסת [נוסח משולב], סעיף 86(ב)", URL_ELECTIONS)
K35 = Law("חוק־יסוד: הכנסת, סעיף 35", URL_KNESSET)
I10 = Law("חוק הפרשנות, סעיף 10", URL_INTERP)

ROUND_LAW = {1: S8, 2: S9C, 3: S10C}
ROUND_PERIOD = {1: FIRST_PERIOD, 2: SECOND_PERIOD, 3: THIRD_PERIOD}

PHASE_LABELS = {
    "pre": "לפני הטלת המנדט",
    "forming": "מועמד מרכיב ממשלה",
    "president_decision": "ממתינים להחלטת הנשיא",
    "window": "חלון 21 הימים של הכנסת",
    "majority_assign": "הנשיא נדרש להטיל את המשימה לפי בקשת 61 ח\"כים",
    "awaiting_notice": "ממתינים להודעת הנשיא על כישלון ההרכבה",
    "success": "המועמד הודיע שהרכיב ממשלה",
    "formed": "הממשלה כוננה",
    "dissolved": "הכנסת התפזרה — בחירות",
}


MK = 'ח"כ'
THE_MK = 'הח"כ'


class StateError(ValueError):
    pass


@dataclass
class Item:
    """One timeline entry (and, if `calendar`, one VEVENT)."""
    key: str
    title: str
    kind: str                 # "official" | "computed"
    category: str             # ceremony | deadline | vote | milestone | possible | info
    day: date
    start: time | None = None  # local Israel time when the event is timed
    minutes: int = 60
    end_day: date | None = None  # inclusive last day of a multi-day all-day event
    explanation: str = ""
    law: Law | None = None
    source: str | None = None
    location: str | None = None
    notes: list[str] = field(default_factory=list)
    alarm: bool = False
    calendar: bool = True

    def start_dt(self) -> datetime:
        return datetime.combine(self.day, self.start or time(0, 0), TZ)

    def end_dt(self) -> datetime:
        """End of the event (end of the last day for all-day items)."""
        if self.start is not None:
            return self.start_dt() + timedelta(minutes=self.minutes)
        last = self.end_day or self.day
        return datetime.combine(last + timedelta(days=1), time(0, 0), TZ)


@dataclass
class Node:
    """Scenario-tree node."""
    id: str
    title: str
    day: date | None = None
    note: str = ""
    law: Law | None = None
    latest: bool = True       # the date is the latest possible one
    children: list["Node"] = field(default_factory=list)
    cal: str | None = None    # title in scenarios.ics (None = not exported)

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


@dataclass
class Result:
    items: list[Item]
    status: dict[str, Any]
    scenarios: Node
    warnings: list[str]


# ----------------------------------------------------------------------------------
# helpers

def last_tuesday_on_or_before(d: date) -> date:
    return d - timedelta(days=(d.weekday() - 1) % 7)


def elections_date(notice: date) -> date:
    """s.11(b): the last Tuesday before the end of 90 days from `notice`."""
    return last_tuesday_on_or_before(notice + timedelta(days=ELECTIONS_DAYS))


def elect_note(notice: date) -> str:
    d = elections_date(notice)
    note = f"ביום ג' האחרון שלפני תום 90 ימים מהודעת הנשיא ({notice:%d.%m.%Y})."
    hol = holiday_name(d)
    if hol:
        note += f" המועד חל ב{hol}; ייתכן שייקבע בחוק מועד אחר."
    return note


def _parse_date(v: Any, field_name: str, ev: dict) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v.strip()[:10])
        except ValueError:
            pass
    raise StateError(f"שדה {field_name} לא תקין באירוע {ev.get('id') or ev.get('type')}: {v!r}")


def _parse_time(v: Any) -> time | None:
    if v is None or v == "":
        return None
    if isinstance(v, time):
        return v
    if isinstance(v, int):  # YAML 1.1 reads 17:00 as sexagesimal 1020
        return time(v // 60, v % 60)
    if isinstance(v, str):
        h, _, m = v.strip().partition(":")
        return time(int(h), int(m or 0))
    raise StateError(f"שעה לא תקינה: {v!r}")


def _end_of_day(d: date) -> datetime:
    return datetime.combine(d + timedelta(days=1), time(0, 0), TZ)


@dataclass
class Event:
    raw: dict
    index: int
    type: str
    day: date
    start: time | None

    def get(self, k: str, default: Any = None) -> Any:
        return self.raw.get(k, default)

    @property
    def id(self) -> str:
        return str(self.raw.get("id") or f"{self.type}-{self.day.isoformat()}")

    @property
    def kind(self) -> str:
        return "computed" if self.raw.get("status") == "computed" else "official"

    def sort_key(self):
        return (self.day, self.start or time(0, 0), self.index)


VALID_TYPES = {
    "election", "results_published", "consultations", "knesset_opening", "announcement",
    "note", "speaker_elected", "mandate_assigned", "extension_requested",
    "extension_granted", "mandate_returned", "period_expired", "success_notice",
    "knesset_notified", "confidence_vote_scheduled", "confidence_vote",
    "government_sworn_in", "president_no_possibility", "majority_request",
    "president_notice_dissolution", "dissolution_law", "appeals_deadline",
}


def parse_events(state: dict) -> list[Event]:
    out: list[Event] = []
    for i, raw in enumerate(state.get("events") or []):
        if not isinstance(raw, dict):
            raise StateError(f"אירוע מס' {i + 1} אינו מילון")
        t = raw.get("type")
        if t not in VALID_TYPES:
            raise StateError(f"סוג אירוע לא מוכר: {t!r} (אירוע {raw.get('id')})")
        if "date" not in raw:
            raise StateError(f"לאירוע {raw.get('id') or t} חסר שדה date")
        out.append(Event(raw, i, t, _parse_date(raw["date"], "date", raw), _parse_time(raw.get("time"))))
    out.sort(key=Event.sort_key)
    return out


# ----------------------------------------------------------------------------------
# engine

class Engine:
    def __init__(self, state: dict, as_of: datetime):
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=TZ)
        self.state = state
        self.as_of = as_of.astimezone(TZ)
        rules = state.get("rules") or {}
        self.rest_shift = bool(rules.get("rest_day_shift", False))
        self.knesset = state.get("knesset", 26)
        self.items: dict[str, Item] = {}
        self.warnings: list[str] = []

        self.phase = "pre"
        self.round = 0
        self.holder: str | None = None
        self.party: str | None = None
        self.assigned: date | None = None
        self.assigned_time: time | None = None
        self.assign_source: str | None = None
        self.base_raw: date | None = None      # A + period, before rest-day handling
        self.ext_used = 0
        self.official_deadline: date | None = None
        self.phase_start: date | None = None   # start of the current waiting period
        self.phase_source: str | None = None
        self.results_day: date | None = None
        self.success_day: date | None = None
        self.knesset_notice: date | None = None
        self.vote_scheduled = False
        self.final_election: date | None = None
        self.majority_candidate: str | None = None
        self.history: list[tuple[int, str, str]] = []  # (round, holder, outcome)

    # -- utilities -------------------------------------------------------------------
    def add(self, item: Item) -> Item:
        self.items[item.key] = item
        return item

    def drop(self, *keys: str) -> None:
        for k in keys:
            self.items.pop(k, None)

    def deadline_from(self, start: date, days: int) -> tuple[date, list[str]]:
        """Last day of a period of `days` days counted from `start` (s.10 Interpretation)."""
        raw = start + timedelta(days=days)
        return self.adjust(raw)

    def adjust(self, raw: date) -> tuple[date, list[str]]:
        rest = rest_day_name(raw)
        if not rest:
            return raw, []
        if self.rest_shift:
            moved = next_working_day(raw)
            return moved, [f"המועד המקורי ({raw:%d.%m.%Y}) חל ב{rest}; לפי סעיף 10(ג) לחוק הפרשנות "
                           f"הוא נדחה ליום החול הבא."]
        return raw, [f"המועד חל ב{rest}. לפי סעיף 10(ג) לחוק הפרשנות ימי מנוחה בסוף תקופה אינם "
                     f"נמנים, אך בתקדים של 2013 נקבע המועד בשבת עצמה (2.3.2013), ולכן הוא לא נדחה כאן."]

    @property
    def deadline(self) -> date | None:
        if self.official_deadline:
            return self.official_deadline
        if self.base_raw is None:
            return None
        return self.adjust(self.base_raw + timedelta(days=self.ext_used))[0]

    def candidate_label(self) -> str:
        if not self.holder:
            return "המועמד"
        return self.holder + (f" ({self.party})" if self.party else "")

    # -- event handlers ----------------------------------------------------------------
    def run(self) -> Result:
        events = parse_events(self.state)
        for ev in events:
            self.catch_up(datetime.combine(ev.day, ev.start or time(0, 0), TZ), grace=timedelta(0))
            getattr(self, "on_" + ev.type)(ev)
        self.catch_up(self.as_of, grace=EXPIRY_GRACE)
        self.finish()
        items = sorted(self.items.values(), key=lambda it: (it.start_dt(), it.key))
        return Result(items, self.status(), self.scenarios(), self.warnings)

    def info(self, ev: Event, title: str, category: str = "milestone", key: str | None = None, **kw) -> Item:
        minutes = kw.pop("minutes", 60)
        explanation = kw.pop("explanation", "")
        return self.add(Item(
            key=key or ev.id, title=ev.get("title") or title, kind=ev.kind, category=category,
            day=ev.day, start=ev.start, minutes=int(ev.get("duration_minutes") or minutes),
            explanation=ev.get("note") or explanation, source=ev.get("source"),
            location=ev.get("location"), **kw))

    def on_election(self, ev: Event) -> None:
        self.info(ev, f"יום הבחירות לכנסת ה־{self.knesset}")

    def on_results_published(self, ev: Event) -> None:
        self.results_day = ev.day
        self.info(ev, "פרסום תוצאות הבחירות הרשמיות")
        if "appeals-deadline" not in self.items or self.items["appeals-deadline"].kind == "computed":
            d, notes = self.deadline_from(ev.day, APPEALS_DAYS)
            self.add(Item(
                key="appeals-deadline", title="מועד אחרון להגשת ערעורי בחירות", kind="computed",
                category="deadline", day=d, law=E86, source=ev.get("source"), alarm=True,
                explanation=f"ערעור בחירות מוגש לבית המשפט לעניינים מינהליים בירושלים תוך "
                            f"{APPEALS_DAYS} ימים מיום פרסום תוצאות הבחירות ({ev.day:%d.%m.%Y}).",
                notes=notes))

    def on_appeals_deadline(self, ev: Event) -> None:
        self.info(ev, "מועד אחרון להגשת ערעורי בחירות", category="deadline", key="appeals-deadline",
                  law=E86, alarm=True)

    def on_consultations(self, ev: Event) -> None:
        it = self.info(ev, "סבב ההתייעצויות של הנשיא עם הסיעות", law=S7)
        if ev.get("end_date"):
            it.end_day = _parse_date(ev.get("end_date"), "end_date", ev.raw)

    def on_knesset_opening(self, ev: Event) -> None:
        self.info(ev, f"כינוס הפתיחה של הכנסת ה־{self.knesset} והשבעת חברי הכנסת",
                  category="ceremony", minutes=180)

    def on_announcement(self, ev: Event) -> None:
        self.info(ev, ev.get("title") or "הודעה", category="info")

    on_note = on_announcement

    def on_speaker_elected(self, ev: Event) -> None:
        name = ev.get("candidate") or ev.get("name") or ""
        self.info(ev, f"בחירת יו\"ר הכנסת{': ' + name if name else ''}", category="milestone")

    def on_mandate_assigned(self, ev: Event) -> None:
        rnd = ev.get("round")
        if rnd is None:
            rnd = {"pre": 1, "president_decision": 2, "window": 3, "majority_assign": 3}.get(self.phase)
            if rnd is None:
                self.warnings.append(f"הטלת מנדט ב־{ev.day:%d.%m.%Y} בשלב לא צפוי ({PHASE_LABELS[self.phase]}); "
                                     f"מטופלת כסבב ראשון.")
                rnd = 1
        rnd = int(rnd)
        if rnd == 2 and self.phase == "president_decision" and self.phase_start:
            limit = self.phase_start + timedelta(days=PRESIDENT_DECISION)
            if ev.day > limit:
                self.warnings.append(f"ההטלה השנייה ({ev.day:%d.%m.%Y}) מאוחרת מ־3 הימים שבסעיף 9(א) "
                                     f"(עד {limit:%d.%m.%Y}).")
        self.drop("r1-president-decision", "window-deadline", "r3-assign-deadline", "assign-deadline")
        self.round = rnd
        self.holder = ev.get("candidate")
        self.party = ev.get("party")
        self.assigned = ev.day
        self.assigned_time = ev.start
        self.assign_source = ev.get("source")
        self.base_raw = ev.day + timedelta(days=ROUND_PERIOD[rnd])
        self.ext_used = 0
        self.official_deadline = _parse_date(ev.get("deadline"), "deadline", ev.raw) if ev.get("deadline") else None
        self.success_day = None
        self.knesset_notice = None
        self.vote_scheduled = False
        self.phase = "forming"
        sec = {1: "סעיף 7", 2: "סעיף 9", 3: "סעיף 10(ב)"}[rnd]
        title = (f"טקס הטלת המנדט על {self.candidate_label()}" if ev.start
                 else f"הנשיא הטיל את המנדט על {self.candidate_label()}")
        self.info(ev, title, category="ceremony" if ev.start else "milestone", key=f"mandate-r{rnd}",
                  law=Law(f"חוק־יסוד: הממשלה, {sec}", URL_GOV), minutes=60,
                  explanation=f"מיום זה נמנים {ROUND_PERIOD[rnd]} ימים להרכבת הממשלה (סבב {rnd}).")

    def on_extension_requested(self, ev: Event) -> None:
        days = ev.get("days")
        self.info(ev, f"{self.holder or 'המועמד'} ביקש מהנשיא הארכה"
                      f"{f' של {days} ימים' if days else ''}", category="info", law=S8)

    def on_extension_granted(self, ev: Event) -> None:
        days = int(ev.get("days") or 0)
        if self.phase != "forming" or self.round != 1:
            self.warnings.append(f"הארכה ב־{ev.day:%d.%m.%Y} ניתנה שלא בסבב הראשון — החוק מתיר הארכה "
                                 f"רק לפי סעיף 8.")
        if self.ext_used + days > MAX_EXTENSION:
            self.warnings.append(f"סך ההארכות ({self.ext_used + days}) עולה על {MAX_EXTENSION} הימים שבסעיף 8.")
        self.ext_used += days
        if ev.get("new_deadline"):
            self.official_deadline = _parse_date(ev.get("new_deadline"), "new_deadline", ev.raw)
        self.info(ev, f"הנשיא העניק ל{self.holder or 'מועמד'} הארכה של {days} ימים",
                  category="milestone", law=S8,
                  explanation=f"סך ההארכות: {self.ext_used} מתוך {MAX_EXTENSION} הימים המותרים.")

    def on_mandate_returned(self, ev: Event) -> None:
        if self.phase != "forming":
            self.warnings.append(f"החזרת מנדט ב־{ev.day:%d.%m.%Y} כשאין מנדט פעיל.")
        self.info(ev, f"{self.holder or 'המועמד'} הודיע לנשיא שאין בידו להרכיב ממשלה",
                  category="milestone", law=S9A if self.round == 1 else (S10A if self.round == 2 else S11))
        self.end_round(ev.day, "returned", ev.get("source"))

    def on_period_expired(self, ev: Event) -> None:
        self.info(ev, f"תקופת ההרכבה של {self.holder or 'המועמד'} הסתיימה ללא ממשלה",
                  category="milestone", law=ROUND_LAW.get(self.round, S8))
        self.end_round(ev.day, "expired", ev.get("source"))

    def on_success_notice(self, ev: Event) -> None:
        if self.phase != "forming":
            self.warnings.append(f"הודעה על הרכבת ממשלה ב־{ev.day:%d.%m.%Y} כשאין מנדט פעיל.")
        elif self.deadline and ev.day > self.deadline:
            self.warnings.append(f"ההודעה על הרכבת הממשלה ({ev.day:%d.%m.%Y}) מאוחרת מהמועד "
                                 f"({self.deadline:%d.%m.%Y}).")
        cand = ev.get("candidate") or self.holder
        if cand and self.holder and cand != self.holder:
            self.warnings.append(f"ההודעה על הצלחה ({cand}) אינה של מחזיק המנדט ({self.holder}).")
        self.info(ev, f"{cand or 'המועמד'} הודיע לנשיא וליו\"ר הכנסת שהרכיב ממשלה",
                  category="milestone", law=S13B)
        self.history.append((self.round, self.holder or "", "success"))
        self.success_day = ev.day
        self.phase = "success"

    def on_knesset_notified(self, ev: Event) -> None:
        self.knesset_notice = ev.day
        self.info(ev, "יו\"ר הכנסת הודיע לכנסת על הרכבת הממשלה", category="info", law=S13B)

    def on_confidence_vote_scheduled(self, ev: Event) -> None:
        self.vote_scheduled = True
        self.drop("confidence-session-deadline")
        self.add(Item(key="confidence-vote", title=ev.get("title") or "הצגת הממשלה והצבעת אמון בכנסת",
                      kind=ev.kind, category="vote", day=ev.day, start=ev.start,
                      minutes=int(ev.get("duration_minutes") or 240), law=S13D, source=ev.get("source"),
                      location=ev.get("location") or "משכן הכנסת, ירושלים",
                      explanation=ev.get("note") or "הממשלה מתייצבת לפני הכנסת ומבקשת הבעת אמון. "
                                                    "די ברוב רגיל של המצביעים."))

    def on_confidence_vote(self, ev: Event) -> None:
        result = str(ev.get("result") or "").lower()
        passed = result in ("passed", "pass", "yes", "אושר", "התקבל")
        votes = ""
        if ev.get("votes_for") is not None:
            votes = f" ({ev.get('votes_for')} בעד, {ev.get('votes_against', '?')} נגד)"
        prev = self.items.get("confidence-vote")
        self.add(Item(key="confidence-vote",
                      title=ev.get("title") or (f"הכנסת הביעה אמון בממשלה{votes}" if passed
                                                else f"הכנסת דחתה את הבקשה להביע אמון בממשלה{votes}"),
                      kind=ev.kind, category="vote", day=ev.day, start=ev.start or (prev.start if prev else None),
                      minutes=int(ev.get("duration_minutes") or (prev.minutes if prev else 240)),
                      law=S13D, source=ev.get("source") or (prev.source if prev else None),
                      location=ev.get("location") or "משכן הכנסת, ירושלים", explanation=ev.get("note") or ""))
        self.drop("confidence-session-deadline")
        if passed:
            self.phase = "formed"
        else:
            self.history.append((self.round, self.holder or "", "rejected"))
            self.end_round(ev.day, "rejected", ev.get("source"), already_logged=True)

    def on_government_sworn_in(self, ev: Event) -> None:
        self.phase = "formed"
        self.info(ev, "השבעת הממשלה", category="ceremony", law=S13D, minutes=120)

    def on_president_no_possibility(self, ev: Event) -> None:
        if self.phase != "president_decision":
            self.warnings.append("הודעת הנשיא שאין אפשרות להרכיב ממשלה ניתנה שלא אחרי הסבב הראשון.")
        self.drop("r1-president-decision")
        self.info(ev, "הנשיא הודיע ליו\"ר הכנסת שאינו רואה אפשרות להרכיב ממשלה", category="milestone", law=S9A)
        self.open_window(ev.day, ev.get("source"))

    def on_majority_request(self, ev: Event) -> None:
        if self.phase != "window":
            self.warnings.append("בקשת רוב חברי הכנסת הוגשה שלא בחלון 21 הימים.")
        self.drop("window-deadline")
        self.majority_candidate = ev.get("candidate")
        self.info(ev, f"61 חברי כנסת ביקשו מהנשיא להטיל את המשימה על {ev.get('candidate') or MK}",
                  category="milestone", law=S10A)
        d, notes = self.deadline_from(ev.day, MAJORITY_ASSIGN)
        self.add(Item(key="r3-assign-deadline", title="מועד אחרון לנשיא להטיל את המשימה לפי בקשת הרוב",
                      kind="computed", category="deadline", day=d, law=S10B, source=ev.get("source"),
                      alarm=True, notes=notes,
                      explanation="הנשיא מטיל את התפקיד בתוך יומיים על חבר הכנסת שצוין בבקשה."))
        self.phase = "majority_assign"
        self.phase_start = ev.day

    def on_president_notice_dissolution(self, ev: Event) -> None:
        self.drop("window-deadline", "r3-deadline", "awaiting-notice")
        self.info(ev, "הנשיא הודיע ליו\"ר הכנסת שלא הורכבה ממשלה — הכנסת מתפזרת", category="milestone", law=S11)
        self.dissolve(ev.day, ev.get("source"))

    def on_dissolution_law(self, ev: Event) -> None:
        self.drop(*[k for k, it in self.items.items() if it.kind == "computed"
                    and it.category in ("deadline", "possible", "milestone") and it.day >= ev.day])
        self.info(ev, "הכנסת קיבלה חוק להתפזרותה — הליכי הרכבת הממשלה נפסקים", category="milestone", law=S12)
        self.phase = "dissolved"
        if ev.get("election_date"):
            d = _parse_date(ev.get("election_date"), "election_date", ev.raw)
            self.final_election = d
            self.add(Item(key="next-elections", title=f"הבחירות לכנסת ה־{self.knesset + 1}", kind="official",
                          category="milestone", day=d, law=K35, source=ev.get("source"),
                          explanation="מועד הבחירות שנקבע בחוק התפזרות הכנסת."))

    # -- transitions ---------------------------------------------------------------------
    def end_round(self, day: date, reason: str, source: str | None, already_logged: bool = False) -> None:
        rnd = self.round
        if not already_logged:
            self.history.append((rnd, self.holder or "", reason))
        if reason == "expired" and self.deadline:
            self.add_round_deadline(final=True)
        else:
            self.drop(f"r{rnd}-deadline")
        self.drop("r1-max-extension", "confidence-session-deadline")
        if rnd <= 1:
            self.phase = "president_decision"
            self.phase_start = day
            self.phase_source = source
            d, notes = self.deadline_from(day, PRESIDENT_DECISION)
            self.add(Item(key="r1-president-decision",
                          title="מועד אחרון להחלטת הנשיא: הטלה על ח\"כ אחר או הודעה שאין אפשרות",
                          kind="computed", category="deadline", day=d, law=S9A, source=source, alarm=True,
                          notes=notes,
                          explanation="תוך 3 ימים הנשיא מטיל את התפקיד על חבר כנסת אחר (ל־28 ימים, בלי "
                                      "הארכה) או מודיע ליו\"ר הכנסת שאינו רואה אפשרות להרכיב ממשלה."))
        elif rnd == 2:
            self.open_window(day, source)
        else:
            if reason == "rejected":
                self.dissolve(day, source, from_rejection=True)
            else:
                self.phase = "awaiting_notice"
                self.phase_start = day
                self.dissolve(day + timedelta(days=1), source, computed=True)

    def open_window(self, day: date, source: str | None) -> None:
        self.phase = "window"
        self.phase_start = day
        self.phase_source = source
        d, notes = self.deadline_from(day, MAJORITY_WINDOW)
        self.add(Item(key="window-deadline", title="סיום חלון 21 הימים לבקשת 61 חברי כנסת",
                      kind="computed", category="deadline", day=d, law=S10A, source=source, alarm=True,
                      notes=notes,
                      explanation="רוב חברי הכנסת (61) רשאים לבקש מהנשיא בכתב להטיל את המשימה על חבר "
                                  "כנסת מסוים שהסכים לכך. אם לא תוגש בקשה — הכנסת מתפזרת."))

    def dissolve(self, notice: date, source: str | None, computed: bool = False,
                 from_rejection: bool = False) -> None:
        self.phase = "dissolved"
        d = elections_date(notice)
        self.final_election = d
        basis = ("מיום דחיית הבקשה להביע אמון" if from_rejection else "מיום הודעת הנשיא ליו\"ר הכנסת")
        notes = []
        hol = holiday_name(d)
        if hol:
            notes.append(f"המועד חל ב{hol}; ייתכן שהכנסת תקבע בחוק מועד אחר (כך היה ב־2019, כשהמועד "
                         f"המחושב 10.3.2020 חל בפורים).")
        if computed:
            notes.append(f"בהנחה שהנשיא יודיע ליו\"ר הכנסת ב־{notice:%d.%m.%Y}.")
        self.add(Item(key="next-elections", title=f"הבחירות לכנסת ה־{self.knesset + 1}", kind="computed",
                      category="milestone", day=d, law=S11, source=source, notes=notes,
                      explanation=f"יראו את הכנסת כאילו החליטה על התפזרותה; הבחירות ביום ג' האחרון שלפני "
                                  f"תום 90 ימים {basis} ({notice:%d.%m.%Y})."))

    def add_round_deadline(self, final: bool = False) -> None:
        rnd = self.round
        d = self.deadline
        assert d is not None and self.assigned is not None
        official = self.official_deadline is not None
        notes = [] if official else self.adjust(self.base_raw + timedelta(days=self.ext_used))[1]
        total = ROUND_PERIOD[rnd] + self.ext_used
        ext_txt = f" (כולל הארכה של {self.ext_used} ימים)" if self.ext_used else ""
        if final:
            title = f"תקופת ההרכבה של {self.holder} הסתיימה{ext_txt}"
        else:
            title = f"סיום {total} הימים להרכבת הממשלה — {self.holder}{ext_txt}"
        self.add(Item(key=f"r{rnd}-deadline", title=title, kind="official" if official else "computed",
                      category="deadline", day=d, law=ROUND_LAW[rnd], source=self.assign_source, alarm=True,
                      notes=notes,
                      explanation=(f"המנדט הוטל ב־{self.assigned:%d.%m.%Y}; לפי החוק עומדים לרשות המועמד "
                                   f"{ROUND_PERIOD[rnd]} ימים{ext_txt}. המועד מסתיים בחצות שבסוף היום.")))

    def catch_up(self, until: datetime, grace: timedelta) -> None:
        """Close periods that ended before `until` with nothing reported.

        Called before every event (no grace: the event itself is a fact) and once more
        with the build time (with grace, so a last-minute announcement can still land).
        """
        for _ in range(6):
            if self.phase == "forming" and self.deadline and until >= _end_of_day(self.deadline) + grace:
                self.end_round(self.deadline, "expired", self.assign_source)
                continue
            if self.phase == "window":
                end = self.items.get("window-deadline")
                if end and until >= _end_of_day(end.day) + grace:
                    self.dissolve(end.day + timedelta(days=1), self.phase_source, computed=True)
                    continue
            break
        if grace and self.phase == "president_decision":
            it = self.items.get("r1-president-decision")
            if it and until >= _end_of_day(it.day) + grace:
                self.warnings.append("המועד להחלטת הנשיא עבר ואין עדכון במצב — יש לעדכן את state.yaml.")

    def finish(self) -> None:
        if self.phase == "pre" and self.results_day:
            d, notes = self.deadline_from(self.results_day, ASSIGN_DAYS)
            self.add(Item(key="assign-deadline", title="מועד אחרון להטלת המנדט על ידי הנשיא",
                          kind="computed", category="deadline", day=d, law=S7, alarm=True, notes=notes,
                          explanation="הנשיא מטיל את התפקיד בתוך 7 ימים מיום פרסום תוצאות הבחירות."))
        if self.phase == "forming":
            self.add_round_deadline()
            if self.round == 1 and self.ext_used < MAX_EXTENSION and not self.official_deadline:
                raw = self.base_raw + timedelta(days=MAX_EXTENSION)
                d, notes = self.adjust(raw)
                self.add(Item(key="r1-max-extension",
                              title=f"סוף הארכה אפשרית (עד {MAX_EXTENSION} ימים) — {self.holder}",
                              kind="computed", category="possible", day=d, law=S8, source=self.assign_source,
                              alarm=True, notes=notes,
                              explanation="הנשיא רשאי להאריך את התקופה בתקופות נוספות שלא יעלו יחד על "
                                          "14 ימים. האירוע יוסר אם לא תינתן הארכה."))
            else:
                self.drop("r1-max-extension")
        if self.phase == "success" and not self.vote_scheduled:
            base = self.knesset_notice or self.success_day
            d = base + timedelta(days=CONFIDENCE_SESSION)
            note = [] if self.knesset_notice else [
                "בהנחה שיו\"ר הכנסת מודיע לכנסת באותו יום; ב־2021 ההודעה לכנסת נמסרה 5 ימים אחרי "
                "ההודעה לנשיא."]
            self.add(Item(key="confidence-session-deadline", title="מועד אחרון לישיבת הצגת הממשלה והצבעת האמון",
                          kind="computed", category="deadline", day=d, law=S13B, alarm=True, notes=note,
                          explanation="יו\"ר הכנסת קובע ישיבה לכינון הממשלה בתוך 7 ימים מיום ההודעה לכנסת."))

    # -- status & scenarios ----------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        s: dict[str, Any] = {
            "phase": self.phase, "phase_label": PHASE_LABELS[self.phase], "round": self.round,
            "holder": self.holder, "party": self.party,
            "assigned": self.assigned.isoformat() if self.assigned else None,
            "assigned_time": self.assigned_time.strftime("%H:%M") if self.assigned_time else None,
            "deadline": self.deadline.isoformat() if self.deadline and self.phase in ("forming",) else None,
            "deadline_kind": "official" if self.official_deadline else "computed",
            "period_days": (ROUND_PERIOD.get(self.round, 0) + self.ext_used) if self.round else None,
            "ext_used": self.ext_used,
            "ext_remaining": (MAX_EXTENSION - self.ext_used) if self.round == 1 else 0,
            "history": [{"round": r, "holder": h, "outcome": o} for r, h, o in self.history],
            "elections": self.final_election.isoformat() if self.final_election else None,
        }
        if self.assigned:
            start = datetime.combine(self.assigned, self.assigned_time or time(0, 0), TZ)
            s["scheduled"] = self.as_of < start
        return s

    def scenarios(self) -> Node:
        """Tree of what can happen next. Dates are the latest possible ones.

        Nodes with `cal` set are exported to scenarios.ics.
        """
        root = Node("root", "מצב נוכחי: " + PHASE_LABELS[self.phase])
        p = self.phase
        who = self.holder or "המועמד"
        if p == "pre":
            d = self.items.get("assign-deadline")
            root.children.append(Node("assign", "הנשיא מטיל את המנדט על ח\"כ (28 ימים)", d.day if d else None, law=S7))
        elif p == "forming" and self.deadline:
            root.title = f"{self.candidate_label()} מחזיק במנדט (סבב {self.round})"
            root.children.extend(self.forming_branches(
                self.round, self.deadline, who, allow_ext=self.round == 1 and not self.official_deadline,
                ext_left=MAX_EXTENSION - self.ext_used, prefix="a", cal=True, current=True))
        elif p == "president_decision":
            it = self.items["r1-president-decision"]
            root.children.extend(self.president_branches(it.day, "a", cal=True, failed=who))
        elif p == "window":
            it = self.items["window-deadline"]
            root.children.extend(self.window_branches(it.day, "a", cal=True))
        elif p == "majority_assign":
            it = self.items["r3-assign-deadline"]
            d3, _ = self.deadline_from(it.day, THIRD_PERIOD)
            maj = self.majority_candidate or THE_MK
            n = Node("a-r3", f"הנשיא מטיל את המשימה על {maj} (14 ימים)", it.day, law=S10B)
            n.children.extend(self.forming_branches(3, d3, maj, False, 0, "a-r3", cal=True))
            root.children.append(n)
        elif p == "success":
            base = self.knesset_notice or self.success_day
            d = base + timedelta(days=CONFIDENCE_SESSION) if base else None
            root.children.append(Node("a-pass", "הכנסת מביעה אמון — הממשלה מושבעת", d, law=S13D,
                                      note="די ברוב רגיל של המצביעים."))
            fail = Node("a-fail", "הכנסת דוחה את הבקשה להביע אמון", d, law=S13D)
            if d:
                fail.children.extend(self.after_failure(self.round, d, "a-fail", cal=True, failed=who))
            root.children.append(fail)
        elif p == "formed":
            root.title = "הממשלה כוננה — הליך ההרכבה הסתיים"
        elif p == "dissolved":
            root.title = "הכנסת התפזרה — הולכים לבחירות"
            if self.final_election:
                root.children.append(Node("elections", f"הבחירות לכנסת ה־{self.knesset + 1}", self.final_election,
                                          law=S11, latest=False))
        return root

    def forming_branches(self, rnd: int, deadline: date, who: str, allow_ext: bool, ext_left: int,
                         prefix: str, cal: bool = False, current: bool = False, ctx: str = "") -> list[Node]:
        vote_d = deadline + timedelta(days=CONFIDENCE_SESSION)
        ok = Node(f"{prefix}-ok", f"{who} מודיע שהרכיב ממשלה, לכל המאוחר ב־{deadline.day}.{deadline.month}", deadline, law=S13B,
                  note="יו\"ר הכנסת קובע ישיבה לכינון הממשלה בתוך 7 ימים מההודעה לכנסת, והכנסת מצביעה "
                       "על אמון ברוב רגיל.")
        ok.children.append(Node(f"{prefix}-ok-vote", "הצבעת אמון לכל המאוחר (בהנחה שההודעה לכנסת באותו יום)",
                                vote_d, law=S13D,
                                cal=f"תרחיש: הצבעת אמון לכל המאוחר אם {who} יצליח" if cal and current else None))
        out = [ok]
        if allow_ext and ext_left > 0:
            ext_d, _ = self.adjust(deadline + timedelta(days=ext_left))
            ext = Node(f"{prefix}-ext", f"הנשיא מאריך את התקופה (עד {ext_left} ימים נוספים)", ext_d, law=S8,
                       cal=f"תרחיש: סוף ההארכה המרבית ל{who}" if cal else None)
            ext.children.append(Node(f"{prefix}-ext-ok", "מצליח בתוך ההארכה", ext_d, law=S13B, children=[
                Node(f"{prefix}-ext-ok-vote", "הצבעת אמון לכל המאוחר",
                     ext_d + timedelta(days=CONFIDENCE_SESSION), law=S13D)]))
            fail_ext = Node(f"{prefix}-ext-fail", "נכשל גם אחרי ההארכה", ext_d, law=S9A)
            fail_ext.children.extend(self.after_failure(rnd, ext_d, f"{prefix}-ext-fail", failed=who))
            ext.children.append(fail_ext)
            out.append(ext)
        fail = Node(f"{prefix}-fail", f"{who} מחזיר את המנדט או שהתקופה מסתיימת", deadline,
                    law=ROUND_LAW[rnd], note="החזרה מוקדמת מקדימה את כל המועדים שאחריה.",
                    cal=f"תרחיש: סוף תקופת ההרכבה של {who}{ctx}" if cal and not current else None)
        fail.children.extend(self.after_failure(rnd, deadline, f"{prefix}-fail", cal=cal, failed=who, ctx=ctx))
        out.append(fail)
        return out

    def after_failure(self, rnd: int, end: date, prefix: str, cal: bool = False, failed: str = "",
                      ctx: str = "") -> list[Node]:
        if rnd <= 1:
            d, _ = self.deadline_from(end, PRESIDENT_DECISION)
            return self.president_branches(d, prefix, cal=cal, failed=failed)
        if rnd == 2:
            d, _ = self.deadline_from(end, MAJORITY_WINDOW)
            return self.window_branches(d, prefix, cal=cal, ctx=ctx or " (אחרי המועמד השני)")
        notice = end + timedelta(days=1)
        return [Node(f"{prefix}-elect", f"הכנסת מתפזרת — בחירות לכנסת ה־{self.knesset + 1}", elections_date(notice),
                     law=S11, latest=False, note=elect_note(notice), cal=f"תרחיש: בחירות אם גם {failed} ייכשל{ctx}" if cal else None)]

    def president_branches(self, decide_by: date, prefix: str, cal: bool = False, failed: str = "") -> list[Node]:
        other = Node(f"{prefix}-r2", f"הנשיא מטיל את המשימה על {MK} אחר (28 ימים, בלי הארכה)", decide_by, law=S9A,
                     cal=f"תרחיש: אם {failed} ייכשל — מועד אחרון להחלטת הנשיא" if cal else None)
        d2, _ = self.deadline_from(decide_by, SECOND_PERIOD)
        other.children.extend(self.forming_branches(2, d2, "המועמד השני", False, 0, f"{prefix}-r2", cal=cal))
        none = Node(f"{prefix}-none", "הנשיא מודיע ליו\"ר הכנסת שאין אפשרות להרכיב ממשלה", decide_by, law=S9A)
        dw, _ = self.deadline_from(decide_by, MAJORITY_WINDOW)
        none.children.extend(self.window_branches(dw, f"{prefix}-none", cal=cal, ctx=" (מסלול הודעת הנשיא)"))
        return [other, none]

    def window_branches(self, window_end: date, prefix: str, cal: bool = False, ctx: str = "") -> list[Node]:
        ask = Node(f"{prefix}-61", f"61 {MK}ים מבקשים להטיל את המשימה על {MK} מסוים (בתוך 21 הימים)",
                   window_end, law=S10A, note=f"חלון 21 הימים מסתיים ב־{window_end:%d.%m.%Y}.",
                   cal=f"תרחיש: סוף חלון 21 הימים לבקשת 61 {MK}ים{ctx}" if cal else None)
        assign_by, _ = self.deadline_from(window_end, MAJORITY_ASSIGN)
        d3, _ = self.deadline_from(assign_by, THIRD_PERIOD)
        r3 = Node(f"{prefix}-61-r3", "הנשיא מטיל את המשימה בתוך יומיים (14 ימים להרכבה)", assign_by, law=S10B)
        r3.children.extend(self.forming_branches(3, d3, "מועמד הרוב", False, 0, f"{prefix}-61-r3", cal=cal, ctx=ctx))
        ask.children.append(r3)
        notice = window_end + timedelta(days=1)
        none = Node(f"{prefix}-noreq", "לא מוגשת בקשה — הכנסת מתפזרת", window_end, law=S11)
        none.children.append(Node(f"{prefix}-noreq-elect", f"בחירות לכנסת ה־{self.knesset + 1}",
                                  elections_date(notice), law=S11, latest=False,
                                  note=elect_note(notice),
                                  cal=f"תרחיש: בחירות אם לא תוגש בקשת 61 {MK}ים{ctx}" if cal else None))
        return [ask, none]


def compute(state: dict, as_of: datetime) -> Result:
    return Engine(state, as_of).run()

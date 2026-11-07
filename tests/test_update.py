from datetime import date, datetime

import pytest

from tracker import update
from tracker.classify import classify_keywords, parse_days
from tracker.news import Headline, group_of, registrable_domain
from tracker.rules import TZ
from tracker.state_io import load_config, load_state

CFG = load_config()
CTX = {"holder": "גדי איזנקוט", "people": CFG["people"]}


def h(hid, title, domain, when="2026-12-03T20:00:00+02:00"):
    return Headline(hid, title, f"https://{domain}/{hid}", domain, domain, group_of(domain, CFG), when, "test")


@pytest.mark.parametrize("title,kind,days", [
    ("הנשיא העניק לאיזנקוט הארכה של 14 ימים להרכבת הממשלה", "extension_granted", 14),
    ("איזנקוט קיבל ארכה של שבועיים", "extension_granted", 14),
    ("הנשיא הרצוג האריך את המנדט של איזנקוט ב-14 ימים", "extension_granted", 14),
    ("המנדט של איזנקוט הוארך בשבועיים", "extension_granted", 14),
    ("איזנקוט קיבל עוד שבועיים להרכבת הממשלה", "extension_granted", 14),
    ("Herzog grants Eisenkot 10-day extension to form government", "extension_granted", 10),
    ("Herzog extends Eisenkot's mandate by 14 days", "extension_granted", 14),
    ("איזנקוט פנה לנשיא בבקשה להאריך את המנדט", "extension_requested", None),
    ("איזנקוט החזיר את המנדט לנשיא", "mandate_returned", None),
    ("איזנקוט השיב את המנדט לנשיא", "mandate_returned", None),
    ("איזנקוט הודיע לנשיא: לא הצלחתי להרכיב ממשלה", "mandate_returned", None),
    ("איזנקוט ויתר על המנדט", "mandate_returned", None),
    ("Eisenkot returns mandate to Herzog", "mandate_returned", None),
    ("Eisenkot tells Herzog he cannot form a government", "mandate_returned", None),
    ("איזנקוט הודיע לנשיא כי הצליח להרכיב ממשלה", "success_notice", None),
    ("יש ממשלה: איזנקוט הודיע לנשיא כי עלה בידו להרכיב ממשלה", "success_notice", None),
    ("Eisenkot informs Herzog he has managed to form a government", "success_notice", None),
    ("הממשלה החדשה הושבעה", "confidence_vote", None),
    ("הממשלה ה-38 הושבעה", "confidence_vote", None),
    ("הכנסת הביעה אמון בממשלת איזנקוט", "confidence_vote", None),
    ("ממשלת איזנקוט זכתה באמון הכנסת", "confidence_vote", None),
    ("Knesset approves Eisenkot government in 64-52 vote", "confidence_vote", None),
    ("הכנסת התפזרה: הולכים לבחירות", "dissolution_law", None),
    ("הכנסת ה-26 התפזרה; הבחירות ייערכו במרץ", "dissolution_law", None),
    ("חוק פיזור הכנסת אושר בקריאה שלישית", "dissolution_law", None),
    ("הנשיא הודיע ליו\"ר הכנסת: אין אפשרות להרכיב ממשלה", "president_no_possibility", None),
])
def test_keyword_detection(title, kind, days):
    dets = classify_keywords([h("x", title, "ynet.co.il")], CTX)
    assert len(dets) == 1
    assert dets[0].type == kind
    assert dets[0].days == days
    assert dets[0].candidate == "גדי איזנקוט"


@pytest.mark.parametrize("title,candidate", [
    ("הנשיא הטיל על נפתלי בנט את המשימה להרכיב ממשלה", "נפתלי בנט"),
    ("Herzog tasks Netanyahu with forming the government", "בנימין נתניהו"),
    ("61 ח\"כים חתמו על בקשה להטיל את הרכבת הממשלה על נתניהו", "בנימין נתניהו"),
])
def test_keyword_detects_other_candidates(title, candidate):
    dets = classify_keywords([h("x", title, "ynet.co.il")], CTX)
    assert len(dets) == 1 and dets[0].candidate == candidate


def test_vote_counts_from_headline():
    d = classify_keywords([h("x", "Knesset approves Eisenkot government in 64-52 vote", "jpost.com")], CTX)[0]
    assert (d.result, d.votes_for, d.votes_against) == ("passed", 64, 52)


@pytest.mark.parametrize("title", [
    "איזנקוט יבקש הארכה",                                   # future
    "האם איזנקוט יצליח להרכיב ממשלה?",                      # question
    "Eisenkot expected to ask for an extension",            # hedge
    "MKs sworn in as 26th Knesset convenes",                # not the government
    "בליכוד דורשים: איזנקוט יחזיר את המנדט",                 # a demand
    "הליכוד: איזנקוט צריך להחזיר את המנדט",                   # "to return", not "returned"
    "נתניהו קורא לאיזנקוט להחזיר את המנדט",                   # a call
    "איזנקוט: לא אחזיר את המנדט",                            # a refusal
    "בכנסת מעריכים: הארכה תינתן בשבוע הבא",                  # assessment
    "הכנסת אישרה בקריאה ראשונה את חוק פיזור הכנסת",           # not final
    "שנה להשבעת הממשלה",                                     # anniversary
    "פרשנות: הדרך הארוכה להרכבת ממשלה",                      # no event
    "איזנקוט יקבל את המנדט מחר בבית הנשיא",                   # future
    "ש\"ס המליצה להרצוג להטיל את מלאכת הרכבת הממשלה על נתניהו",  # a recommendation
    "Shas recommends Herzog task Netanyahu with forming the government",
])
def test_keyword_ignores_non_events(title):
    assert classify_keywords([h("x", title, "ynet.co.il")], CTX) == []


def test_parse_days():
    assert parse_days("הארכה של 7 ימים") == 7
    assert parse_days("שבועיים נוספים") == 14
    assert parse_days("a two-week extension") == 14
    assert parse_days("הארכה") is None


def test_same_owner_counts_once():
    assert registrable_domain("www.ynet.co.il") == "ynet.co.il"
    assert registrable_domain("103fm.maariv.co.il") == "maariv.co.il"
    hs = [h("a", "הנשיא העניק לאיזנקוט הארכה של 14 ימים", "ynet.co.il"),
          h("b", "Herzog grants Eisenkot 14-day extension", "ynetnews.com")]
    props = update.cluster(classify_keywords(hs, CTX), {x.id: x for x in hs})
    assert len(props) == 1 and props[0].groups == {"yedioth"}
    hs.append(h("c", "איזנקוט קיבל הארכה של 14 ימים", "haaretz.co.il"))
    props = update.cluster(classify_keywords(hs, CTX), {x.id: x for x in hs})
    assert len(props) == 1 and props[0].groups == {"yedioth", "haaretz"}


def test_already_in_state_respects_manual_entries():
    events = list(load_state()["events"])
    hs = [h("a", "רשמי: גדי איזנקוט קיבל את המנדט", "ynet.co.il", "2026-11-07T21:30:00+02:00")]
    p = update.Proposal("mandate_assigned", date(2026, 11, 7), "גדי איזנקוט", support=[(None, hs[0])])
    assert update.already_in_state(p, events)
    events.append({"type": "mandate_returned", "date": date(2026, 11, 25)})
    p2 = update.Proposal("mandate_returned", date(2026, 11, 26), "גדי איזנקוט")
    assert update.already_in_state(p2, events)


def run_with(monkeypatch, headlines, now):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(update, "fetch_all", lambda cfg, n: (headlines, []))
    return update.run(now, dry_run=True, git=False)


def test_two_sources_apply_one_source_proposes(monkeypatch):
    hs = [h("a", "הנשיא העניק לאיזנקוט הארכה של 14 ימים", "ynet.co.il"),
          h("b", "איזנקוט קיבל הארכה של 14 ימים להרכבת הממשלה", "haaretz.co.il"),
          h("c", "איזנקוט החזיר את המנדט לנשיא", "maariv.co.il", "2026-12-04T09:00:00+02:00")]
    out = run_with(monkeypatch, hs, datetime(2026, 12, 4, 10, tzinfo=TZ))
    assert out["applied"] == ["הנשיא העניק לגדי איזנקוט הארכה של 14 ימים (3.12)"]
    assert out["proposed"] == ["גדי איזנקוט החזיר את המנדט (4.12)"]


def test_engine_rejects_impossible_change(monkeypatch):
    # an extension of 20 days is not allowed by s.8 -> never applied automatically
    hs = [h("a", "הנשיא העניק לאיזנקוט הארכה של 20 ימים", "ynet.co.il"),
          h("b", "איזנקוט קיבל הארכה של 20 ימים", "haaretz.co.il")]
    out = run_with(monkeypatch, hs, datetime(2026, 12, 4, 10, tzinfo=TZ))
    assert out["applied"] == []
    assert len(out["proposed"]) == 1
    assert any("14" in line for line in out["log"])


def test_entry_is_inserted_chronologically():
    state = load_state()
    p = update.Proposal("mandate_returned", date(2026, 11, 9), "גדי איזנקוט",
                        support=[(None, h("a", "x", "ynet.co.il"))])
    entry = update.make_entry(p, applied=True, events=state["events"])
    update.insert_event(state["events"], entry)
    ids = [e["id"] for e in state["events"]]
    assert ids.index(entry["id"]) == ids.index("knesset-opening") - 1   # 9.11 goes before 10.11
    assert entry["origin"] == "auto" and entry["sources"][0]["publisher"] == "ynet.co.il"


def test_ai_mode_parses_structured_output_and_caches(monkeypatch):
    import json
    import types

    import anthropic

    from tracker import classify as cl

    calls = []

    class FakeMessages:
        def create(self, **kw):
            calls.append(kw)
            assert kw["model"] == "claude-haiku-5-5"
            assert kw["output_config"]["format"]["type"] == "json_schema"
            results = [
                {"id": "a", "event_type": "extension_granted", "already_happened": True, "candidate": "גדי איזנקוט",
                 "event_date": "2026-12-03", "event_time": "", "days": 14, "vote_passed": False, "votes_for": 0,
                 "votes_against": 0, "election_date": "", "confidence": "high"},
                {"id": "b", "event_type": "mandate_returned", "already_happened": False, "candidate": "גדי איזנקוט",
                 "event_date": "", "event_time": "", "days": 0, "vote_passed": False, "votes_for": 0,
                 "votes_against": 0, "election_date": "", "confidence": "medium"},
            ]
            block = types.SimpleNamespace(type="text", text=json.dumps({"results": results}))
            return types.SimpleNamespace(stop_reason="end_turn", content=[block])

    class FakeClient:
        def __init__(self, **kw):
            self.messages = FakeMessages()

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-placeholder")
    hs = [h("a", "הנשיא העניק לאיזנקוט הארכה", "ynet.co.il"), h("b", "איזנקוט יחזיר את המנדט?", "haaretz.co.il")]
    cache = {}
    dets, mode, errors = cl.classify(hs, CTX, CFG, cache)
    assert mode == "ai" and errors == []
    assert [(d.type, d.days, d.date) for d in dets] == [("extension_granted", 14, date(2026, 12, 3))]
    assert set(cache) == {"a", "b"}
    cl.classify(hs, CTX, CFG, cache)     # cached: no second API call
    assert len(calls) == 1


def test_news_from_before_the_current_mandate_is_ignored(monkeypatch):
    # 4.11 is before the mandate assigned on 8.11, so it cannot change the current round
    hs = [h("a", "הנשיא הטיל על נתניהו את המשימה להרכיב ממשלה", "ynet.co.il", "2026-11-04T20:00:00+02:00")]
    out = run_with(monkeypatch, hs, datetime(2026, 11, 9, 10, tzinfo=TZ))
    assert out["applied"] == [] and out["proposed"] == []

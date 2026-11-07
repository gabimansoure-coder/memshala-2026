"""Engine tests built on real past cases.

Historical dates (verified in the press before being written here):
* April 2019: Netanyahu tasked 17.4.2019; 28 days ended 15.5; Rivlin granted the
  full 14-day extension (13.5) to 29.5; the Knesset passed a dissolution law on
  30.5.2019 setting elections for 17.9.2019.
* September 2019: Netanyahu tasked 25.9.2019 (28 days to 23.10), returned the
  mandate 21.10; Gantz tasked 23.10 (deadline 20.11, no extension possible) and
  returned it 20.11; the 21-day window closed at midnight 11.12.2019. The
  automatic date would have been 10.3.2020 (Purim); a law moved it to 2.3.2020.
* 2021: Netanyahu tasked 6.4.2021, mandate expired at midnight 4.5; Lapid tasked
  5.5 (deadline 2.6); Lapid told Rivlin on 2.6 that he formed a government; Speaker
  Levin told the Knesset on 7.6; confidence vote 13.6.2021 passed 60-59.
* 2013: Netanyahu tasked 2.2.2013; the 28 days ended on Shabbat 2.3.2013 and the
  14-day extension ran to Shabbat 16.3.2013 (precedent for not shifting).
"""
from datetime import date, datetime

import pytest

from tracker.rules import TZ, compute, elections_date, last_tuesday_on_or_before
from tracker.state_io import load_state


def at(y, m, d, h=12, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=TZ)


def items(result):
    return {it.key: it for it in result.items}


def ev(type_, d, **kw):
    return {"type": type_, "date": d, **kw}


# --- helpers -------------------------------------------------------------------------

def test_last_tuesday():
    assert last_tuesday_on_or_before(date(2020, 3, 11)) == date(2020, 3, 10)
    assert last_tuesday_on_or_before(date(2020, 3, 10)) == date(2020, 3, 10)
    assert last_tuesday_on_or_before(date(2020, 3, 9)) == date(2020, 3, 3)


def test_elections_date_december_2019():
    # President's notice the day after the window closed (12.12.2019) -> 10.3.2020
    assert elections_date(date(2019, 12, 12)) == date(2020, 3, 10)


# --- April 2019 ----------------------------------------------------------------------

APRIL_2019 = [
    ev("mandate_assigned", date(2019, 4, 17), candidate="בנימין נתניהו", id="m1"),
    ev("extension_requested", date(2019, 5, 13), id="req"),
    ev("extension_granted", date(2019, 5, 13), days=14, id="ext"),
    ev("dissolution_law", date(2019, 5, 30), election_date=date(2019, 9, 17), id="dis"),
]


def test_april_2019_initial_deadline():
    r = compute({"knesset": 21, "events": APRIL_2019[:1]}, at(2019, 5, 1))
    it = items(r)
    assert it["r1-deadline"].day == date(2019, 5, 15)
    assert it["r1-deadline"].kind == "computed"
    assert it["r1-max-extension"].day == date(2019, 5, 29)
    assert r.status["phase"] == "forming"
    assert r.status["ext_remaining"] == 14


def test_april_2019_extension_moves_deadline():
    r = compute({"knesset": 21, "events": APRIL_2019[:3]}, at(2019, 5, 14))
    it = items(r)
    assert it["r1-deadline"].day == date(2019, 5, 29)
    assert "הארכה של 14" in it["r1-deadline"].title
    assert "r1-max-extension" not in it          # the whole extension was used
    assert r.status["period_days"] == 42


def test_april_2019_dissolution_law():
    r = compute({"knesset": 21, "events": APRIL_2019}, at(2019, 6, 1))
    it = items(r)
    assert r.status["phase"] == "dissolved"
    assert it["next-elections"].day == date(2019, 9, 17)
    assert it["next-elections"].kind == "official"
    # the deadline that really elapsed stays on the timeline; pending ones are gone
    assert it["r1-deadline"].day == date(2019, 5, 29)
    assert "r1-president-decision" not in it


# --- September 2019 ------------------------------------------------------------------

SEPT_2019 = [
    ev("mandate_assigned", date(2019, 9, 25), candidate="בנימין נתניהו", id="m1"),
    ev("mandate_returned", date(2019, 10, 21), id="ret1"),
    ev("mandate_assigned", date(2019, 10, 23), candidate="בני גנץ", id="m2"),
    ev("mandate_returned", date(2019, 11, 20), id="ret2"),
]


def test_sept_2019_first_round():
    it = items(compute({"knesset": 22, "events": SEPT_2019[:1]}, at(2019, 10, 1)))
    assert it["r1-deadline"].day == date(2019, 10, 23)


def test_sept_2019_early_return_recomputes():
    r = compute({"knesset": 22, "events": SEPT_2019[:2]}, at(2019, 10, 22))
    it = items(r)
    assert r.status["phase"] == "president_decision"
    assert it["r1-president-decision"].day == date(2019, 10, 24)
    assert "r1-deadline" not in it               # moot after the early return
    assert "r1-max-extension" not in it


def test_sept_2019_gantz_second_round_no_extension():
    r = compute({"knesset": 22, "events": SEPT_2019[:3]}, at(2019, 10, 24))
    it = items(r)
    assert r.status["round"] == 2
    assert r.status["holder"] == "בני גנץ"
    assert it["r2-deadline"].day == date(2019, 11, 20)
    assert "r1-max-extension" not in it
    assert r.status["ext_remaining"] == 0


def test_sept_2019_window_and_automatic_dissolution():
    r = compute({"knesset": 22, "events": SEPT_2019}, at(2019, 11, 21))
    it = items(r)
    assert r.status["phase"] == "window"
    assert it["window-deadline"].day == date(2019, 12, 11)

    # nothing happened in the window -> the Knesset is deemed dissolved
    r = compute({"knesset": 22, "events": SEPT_2019}, at(2019, 12, 13))
    it = items(r)
    assert r.status["phase"] == "dissolved"
    assert it["next-elections"].day == date(2020, 3, 10)
    assert it["next-elections"].kind == "computed"
    assert any("פורים" in n for n in it["next-elections"].notes)


def test_sept_2019_law_overrides_computed_election_day():
    events = SEPT_2019 + [ev("dissolution_law", date(2019, 12, 12), election_date=date(2020, 3, 2), id="dis")]
    it = items(compute({"knesset": 22, "events": events}, at(2019, 12, 13)))
    assert it["next-elections"].day == date(2020, 3, 2)
    assert it["next-elections"].kind == "official"
    assert it["window-deadline"].day == date(2019, 12, 11)   # elapsed, kept


# --- 2021 --------------------------------------------------------------------------------

Y2021 = [
    ev("mandate_assigned", date(2021, 4, 6), candidate="בנימין נתניהו", id="m1"),
    ev("mandate_assigned", date(2021, 5, 5), candidate="יאיר לפיד", id="m2"),
    ev("success_notice", date(2021, 6, 2), time="23:25", id="ok"),
    ev("knesset_notified", date(2021, 6, 7), id="kn"),
    ev("confidence_vote_scheduled", date(2021, 6, 13), time="16:00", id="vs"),
    ev("confidence_vote", date(2021, 6, 13), time="16:00", result="passed", votes_for=60, votes_against=59, id="v"),
]


def test_2021_netanyahu_deadline():
    it = items(compute({"knesset": 24, "events": Y2021[:1]}, at(2021, 4, 10)))
    assert it["r1-deadline"].day == date(2021, 5, 4)
    assert it["r1-max-extension"].day == date(2021, 5, 18)


def test_2021_expiry_without_report_needs_grace():
    state = {"knesset": 24, "events": Y2021[:1]}
    # 01:00 after midnight: still within the grace window
    assert compute(state, at(2021, 5, 5, 1)).status["phase"] == "forming"
    r = compute(state, at(2021, 5, 5, 13))
    assert r.status["phase"] == "president_decision"
    assert items(r)["r1-president-decision"].day == date(2021, 5, 7)
    assert items(r)["r1-deadline"].day == date(2021, 5, 4)   # elapsed, kept


def test_2021_lapid_round_inferred():
    r = compute({"knesset": 24, "events": Y2021[:2]}, at(2021, 5, 6))
    it = items(r)
    assert r.status["round"] == 2
    assert it["r2-deadline"].day == date(2021, 6, 2)
    assert "r1-president-decision" not in it


def test_2021_success_then_session_deadline():
    it = items(compute({"knesset": 24, "events": Y2021[:3]}, at(2021, 6, 3)))
    assert it["confidence-session-deadline"].day == date(2021, 6, 9)   # assumption: same-day notice
    assert "r2-deadline" not in it
    it = items(compute({"knesset": 24, "events": Y2021[:4]}, at(2021, 6, 8)))
    assert it["confidence-session-deadline"].day == date(2021, 6, 14)  # 7 days from 7.6


def test_2021_government_formed():
    r = compute({"knesset": 24, "events": Y2021}, at(2021, 6, 14))
    it = items(r)
    assert r.status["phase"] == "formed"
    assert "confidence-session-deadline" not in it
    vote = it["confidence-vote"]
    assert vote.start is not None and vote.category == "vote"
    assert "60 בעד" in vote.title


def test_extension_not_allowed_in_second_round():
    r = compute({"knesset": 24, "events": Y2021[:2] + [ev("extension_granted", date(2021, 5, 30), days=7)]},
                at(2021, 5, 31))
    assert any("סעיף 8" in w for w in r.warnings)


# --- 2013: deadline on Shabbat ------------------------------------------------------------

Y2013 = [ev("mandate_assigned", date(2013, 2, 2), candidate="בנימין נתניהו", id="m1")]


def test_2013_shabbat_deadline_not_shifted_by_default():
    it = items(compute({"knesset": 19, "events": Y2013}, at(2013, 2, 10)))
    assert it["r1-deadline"].day == date(2013, 3, 2)
    assert any("שבת" in n for n in it["r1-deadline"].notes)
    assert it["r1-max-extension"].day == date(2013, 3, 16)


def test_2013_shift_option():
    state = {"knesset": 19, "rules": {"rest_day_shift": True}, "events": Y2013}
    it = items(compute(state, at(2013, 2, 10)))
    assert it["r1-deadline"].day == date(2013, 3, 3)


# --- the current state file ----------------------------------------------------------------

def test_current_state_2026():
    r = compute(load_state(), at(2026, 11, 7, 22))
    it = items(r)
    assert r.status["phase"] == "forming"
    assert r.status["holder"] == "גדי איזנקוט"
    assert r.status["scheduled"] is True            # ceremony is tomorrow at 17:00
    assert it["mandate-r1"].start.hour == 17
    assert it["r1-deadline"].day == date(2026, 12, 6)
    assert it["r1-max-extension"].day == date(2026, 12, 20)
    assert it["appeals-deadline"].day == date(2026, 11, 18)
    assert it["knesset-opening"].start.hour == 16
    assert not r.warnings


def _find(node, node_id):
    if node.id == node_id:
        return node
    for c in node.children:
        f = _find(c, node_id)
        if f:
            return f
    return None


def test_current_scenarios_2026():
    tree = compute(load_state(), at(2026, 11, 7, 22)).scenarios
    assert _find(tree, "a-ext").day == date(2026, 12, 20)
    assert _find(tree, "a-fail-r2").day == date(2026, 12, 9)        # President: 3 days
    assert _find(tree, "a-fail-r2-fail").day == date(2027, 1, 6)    # second candidate: 28 days
    assert _find(tree, "a-fail-none-noreq").day == date(2026, 12, 30)
    elect = _find(tree, "a-fail-r2-fail-noreq-elect")
    assert elect.day == date(2027, 4, 27)                         # 28.1.2027 + 90 -> last Tuesday


@pytest.mark.parametrize("bad", [
    {"events": [{"type": "nope", "date": date(2026, 1, 1)}]},
    {"events": [{"type": "election"}]},
])
def test_invalid_state_rejected(bad):
    from tracker.rules import StateError
    with pytest.raises(StateError):
        compute(bad, at(2026, 1, 1))

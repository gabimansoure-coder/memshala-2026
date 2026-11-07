"""One automation run: fetch news -> classify -> group into proposals -> apply or open a PR.

A proposal is applied directly to state.yaml only when
  * at least `min_independent_sources` different ownership groups report it,
  * the rules engine accepts it without new warnings,
  * it is not already in state.yaml (in any form — manual edits win),
  * it was not applied before and then removed by hand, and was not rejected in a closed PR.
Otherwise it becomes a pull request with the sources, for one-tap approval.

Usage: python -m tracker.update [--dry-run] [--git] [--now ISO]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ruamel.yaml.comments import CommentedMap, CommentedSeq

from . import gitops
from .classify import Detection, classify
from .news import Headline, fetch_all, news_for_site
from .rules import PHASE_LABELS, TZ, StateError, compute
from .state_io import (AUTOMATION_PATH, CACHE_DIR, REGISTRY_PATH, ROOT, _yaml, dump_state, load_config,
                       load_json, load_state, save_json, save_state)

KEYWORD_AUTO = {"extension_granted", "mandate_returned", "success_notice", "confidence_vote"}
MATCH_DAYS = 3


@dataclass
class Proposal:
    type: str
    date: date
    candidate: str | None = None
    time: str | None = None
    days: int | None = None
    result: str | None = None
    votes_for: int | None = None
    votes_against: int | None = None
    election_date: date | None = None
    support: list[tuple[Detection, Headline]] = field(default_factory=list)

    @property
    def groups(self) -> set[str]:
        return {h.group for _, h in self.support}

    @property
    def mode(self) -> str:
        return "ai" if any(d.mode == "ai" for d, _ in self.support) else "keywords"

    def meta(self) -> dict[str, Any]:
        return {"type": self.type, "candidate": self.candidate, "date": self.date.isoformat(),
                "days": self.days, "result": self.result}

    def fingerprint(self) -> str:
        raw = json.dumps(self.meta(), ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(raw.encode()).hexdigest()[:10]

    def summary(self) -> str:
        c = self.candidate or "המועמד"
        d = f"{self.date.day}.{self.date.month}"
        return {
            "mandate_assigned": f"המנדט הוטל על {c} ({d})",
            "extension_requested": f"{c} ביקש הארכה ({d})",
            "extension_granted": f"הנשיא העניק ל{c} הארכה של {self.days or '?'} ימים ({d})",
            "mandate_returned": f"{c} החזיר את המנדט ({d})",
            "success_notice": f"{c} הודיע שהרכיב ממשלה ({d})",
            "confidence_vote_scheduled": f"הצבעת האמון נקבעה ל־{d}{' בשעה ' + self.time if self.time else ''}",
            "confidence_vote": f"הצבעת אמון: {'הממשלה אושרה' if self.result == 'passed' else 'הבקשה נדחתה'} ({d})",
            "government_sworn_in": f"השבעת הממשלה ({d})",
            "president_no_possibility": f"הנשיא הודיע שאין אפשרות להרכיב ממשלה ({d})",
            "majority_request": f"61 ח\"כים ביקשו להטיל את המשימה על {c} ({d})",
            "dissolution_law": f"הכנסת התפזרה ({d})" + (f", בחירות ב־{self.election_date:%d.%m.%Y}"
                                                        if self.election_date else ""),
        }.get(self.type, f"{self.type} ({d})")


def _key(d: Detection) -> tuple:
    return (d.type, d.candidate, d.days if d.type == "extension_granted" else None, d.result,
            d.election_date)


def cluster(dets: list[Detection], headlines: dict[str, Headline]) -> list[Proposal]:
    """Group detections of the same event (same kind and details, dates within 2 days)."""
    props: list[Proposal] = []
    for d in sorted(dets, key=lambda x: x.date):
        h = headlines.get(d.headline_id)
        if h is None or d.type == "none":
            continue
        for p in props:
            if _key(p.support[0][0]) == _key(d) and abs((p.support[0][0].date - d.date).days) <= 2:
                p.support.append((d, h))
                break
        else:
            props.append(Proposal(d.type, d.date, d.candidate, d.time, d.days, d.result, d.votes_for,
                                  d.votes_against, d.election_date, [(d, h)]))
    for p in props:  # most reported date wins, ties -> earliest
        counts = Counter(d.date for d, _ in p.support)
        p.date = sorted(counts, key=lambda x: (-counts[x], x))[0]
        times = [d.time for d, _ in p.support if d.time]
        p.time = Counter(times).most_common(1)[0][0] if times else None
        for attr in ("votes_for", "votes_against"):
            vals = [getattr(d, attr) for d, _ in p.support if getattr(d, attr)]
            setattr(p, attr, Counter(vals).most_common(1)[0][0] if vals else None)
    return props


def _as_date(v: Any) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _same(meta: dict, p: Proposal) -> bool:
    if meta.get("type") != p.type:
        return False
    if meta.get("candidate") and p.candidate and meta["candidate"] != p.candidate:
        return False
    d = _as_date(meta.get("date"))
    if p.type == "mandate_assigned":
        return True
    return d is not None and abs((d - p.date).days) <= MATCH_DAYS


def already_in_state(p: Proposal, events: list) -> bool:
    """True if state.yaml already records this event (manual or automatic)."""
    for ev in events:
        t, d = ev.get("type"), _as_date(ev.get("date"))
        # a passed confidence vote and the swearing-in describe the same moment
        if {t, p.type} == {"confidence_vote", "government_sworn_in"} and d and abs((d - p.date).days) <= 1:
            return True
        if t != p.type:
            continue
        if p.type == "confidence_vote_scheduled":
            if d == p.date:
                return True
            continue  # a different date = rescheduling, handled as a new proposal
        if p.type == "extension_granted" and ev.get("days") and p.days and int(ev["days"]) != p.days:
            continue
        if p.type in ("dissolution_law", "government_sworn_in"):
            return True  # happens once
        if _same({"type": t, "candidate": ev.get("candidate"), "date": d}, p):
            return True
    return False


def make_entry(p: Proposal, applied: bool, events: list) -> CommentedMap:
    base = f"auto-{p.type.replace('_', '-')}-{p.date:%Y%m%d}"
    ids = {str(e.get("id")) for e in events}
    eid, n = base, 2
    while eid in ids:
        eid, n = f"{base}-{n}", n + 1
    e = CommentedMap()
    e["id"] = eid
    e["type"] = p.type
    e["date"] = p.date
    if p.time:
        e["time"] = p.time
    if p.candidate:
        e["candidate"] = p.candidate
    if p.days:
        e["days"] = p.days
    if p.result:
        e["result"] = p.result
    if p.votes_for:
        e["votes_for"] = p.votes_for
    if p.votes_against:
        e["votes_against"] = p.votes_against
    if p.election_date:
        e["election_date"] = p.election_date
    e["status"] = "official"
    e["origin"] = "auto"
    e["source"] = p.support[0][1].link
    srcs = CommentedSeq()
    for _, h in p.support[:8]:
        m = CommentedMap()
        m["publisher"] = h.publisher
        m["title"] = h.title
        m["url"] = h.link
        m["published"] = h.published[:16]
        srcs.append(m)
    e["sources"] = srcs
    e["note"] = (f"נוסף אוטומטית: דווח ב־{len(p.groups)} מקורות בלתי תלויים." if applied
                 else "הוצע אוטומטית; ממתין לאישור.")
    return e


def insert_event(events: list, entry: CommentedMap) -> None:
    for i, ev in enumerate(events):
        d = _as_date(ev.get("date"))
        if d and d > entry["date"]:
            events.insert(i, entry)
            return
    events.append(entry)


def engine_warnings(state: Any, entry: CommentedMap | None, now: datetime) -> list[str]:
    s2 = _yaml().load(dump_state(state))
    if entry is not None:
        insert_event(s2["events"], entry)
    try:
        return compute(s2, now).warnings
    except StateError as e:
        return [str(e)]


def pr_body(p: Proposal, reasons: list[str], entry: CommentedMap) -> str:
    rows = "\n".join(f"| {h.publisher} | [{h.title}]({h.link}) | {h.published[:16].replace('T', ' ')} |"
                     for _, h in p.support)
    yaml_txt = dump_state({"events": [entry]}).strip()
    return (f"## {p.summary()}\n\n"
            f"האוטומציה זיהתה אירוע שמשנה את המצב, אבל לא החילה אותו לבד:\n"
            + "\n".join(f"- {r}" for r in reasons) +
            f"\n\n### המקורות\n| מקור | כותרת | זמן |\n|---|---|---|\n{rows}\n\n"
            f"### השינוי המוצע ב-state.yaml\n```yaml\n{yaml_txt}\n```\n\n"
            f"**לאישור:** Merge pull request (גם מאפליקציית GitHub). **לדחייה:** Close pull request — "
            f"האוטומציה לא תציע את זה שוב.\n\n"
            f"זוהה במצב: {'Claude Haiku' if p.mode == 'ai' else 'מילות מפתח'}\n\n"
            f"<!-- auto-proposal {json.dumps(p.meta(), ensure_ascii=False)} -->\n")


def step_summary(lines: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def run(now: datetime, dry_run: bool = False, git: bool = False) -> dict[str, Any]:
    cfg = load_config()
    repo = cfg["repo"]
    state = load_state()
    res = compute(state, now)
    headlines, feed_errors = fetch_all(cfg, now)
    by_id = {h.id: h for h in headlines}

    cache_path = CACHE_DIR / "classified.json"
    cache = load_json(cache_path, {})
    ctx = {
        "today": now.date().isoformat(), "phase": PHASE_LABELS[res.status["phase"]],
        "mandate_holder": res.status.get("holder"), "round": res.status.get("round"),
        "deadline": res.status.get("deadline"),
        "known_people": sorted({*(cfg.get("people") or {}).keys(),
                                *[str(e["candidate"]) for e in state["events"] if e.get("candidate")]}),
        "holder": res.status.get("holder"), "people": cfg.get("people") or {},
    }
    ai_ctx = {k: v for k, v in ctx.items() if k not in ("holder", "people")}
    dets, mode, cls_errors = classify(headlines, ai_ctx | {"holder": ctx["holder"], "people": ctx["people"]},
                                      cfg, cache)
    if not dry_run:
        save_json(cache_path, {k: v for k, v in cache.items() if k in by_id})
        save_json(CACHE_DIR / "news.json", {"items": news_for_site(headlines), "checked_at": now.isoformat(),
                                            "mode": mode, "feed_errors": feed_errors})

    memory = load_json(AUTOMATION_PATH, {"applied": {}})
    prs = gitops.list_auto_prs(repo) if git else []
    min_src = int(cfg.get("min_independent_sources", 2))
    applied: list[Proposal] = []
    proposed: list[tuple[Proposal, CommentedMap, list[str]]] = []
    log: list[str] = []
    base_warnings = set(res.warnings)

    for p in cluster(dets, by_id):
        matching = [pr for pr in prs if pr.get("meta") and _same(pr["meta"], p)]
        open_prs = [pr for pr in matching if pr["state"] == "OPEN"]
        if already_in_state(p, state["events"]):
            for pr in open_prs:
                if git and not dry_run:
                    gitops.close_pr(repo, pr["number"], "האירוע כבר מופיע ב-state.yaml, ההצעה סגורה.")
            continue
        if any(_same(m, p) for m in memory["applied"].values()):
            log.append(f"דילוג (הוחל בעבר ונמחק ידנית): {p.summary()}")
            continue
        if any(pr["state"] == "CLOSED" for pr in matching):
            log.append(f"דילוג (נדחה ב-PR): {p.summary()}")
            continue

        reasons = []
        if len(p.groups) < min_src:
            reasons.append(f"דווח ב־{len(p.groups)} מקור בלתי תלוי בלבד (נדרשים {min_src}).")
        if p.mode == "keywords" and p.type not in KEYWORD_AUTO:
            reasons.append("זיהוי לפי מילות מפתח: אירוע מהסוג הזה מוחל רק באישור ידני.")
        if p.type == "extension_granted" and not p.days:
            reasons.append("לא זוהה אורך ההארכה.")
        if p.date > now.date() and p.type != "confidence_vote_scheduled":
            reasons.append("התאריך שזוהה עתידי.")
        entry = make_entry(p, applied=not reasons, events=state["events"])
        new_warn = [w for w in engine_warnings(state, entry, now) if w not in base_warnings]
        reasons += [f"מנוע הכללים: {w}" for w in new_warn]

        if not reasons:
            insert_event(state["events"], entry)
            base_warnings = set(engine_warnings(state, None, now))
            memory["applied"][p.fingerprint()] = {**p.meta(), "event_id": entry["id"],
                                                  "applied_at": now.isoformat(timespec="minutes")}
            applied.append(p)
            for pr in open_prs:
                if git and not dry_run:
                    gitops.close_pr(repo, pr["number"], f"אושר אוטומטית ב־{len(p.groups)} מקורות בלתי תלויים.")
        elif open_prs and all(pr.get("mergeable") != "CONFLICTING" for pr in open_prs):
            continue  # already waiting for approval
        else:
            for pr in open_prs:  # conflicted: re-create on top of the current main
                if git and not dry_run:
                    gitops.close_pr(repo, pr["number"], "נסגר בגלל התנגשות; נפתחה הצעה מעודכנת.")
            entry["note"] = "הוצע אוטומטית; ממתין לאישור."
            proposed.append((p, entry, reasons))

    if applied and not dry_run:
        save_state(state)
        save_json(AUTOMATION_PATH, memory)
        from .build import build  # refresh the calendar registry in the same commit
        build(ROOT / "_site", now)
        msg = "עדכון אוטומטי: " + "; ".join(p.summary() for p in applied)
        msg += "\n\n" + "\n".join(f"- {h.publisher}: {h.link}" for p in applied for _, h in p.support[:6])
        if git:
            gitops.commit_and_push(["state.yaml", str(AUTOMATION_PATH.relative_to(ROOT)),
                                    str(REGISTRY_PATH.relative_to(ROOT))], msg)

    pr_urls = []
    for p, entry, reasons in proposed:
        log.append(f"הצעה: {p.summary()} — " + " ".join(reasons))
        if git and not dry_run:
            s2 = _yaml().load(dump_state(state))
            insert_event(s2["events"], entry)
            branch = f"auto/{p.type.replace('_', '-')}/{p.fingerprint()}"
            try:
                pr_urls.append(gitops.create_pr(repo, branch, dump_state(s2), "הצעה: " + p.summary(),
                                                pr_body(p, reasons, entry)))
            except RuntimeError as e:
                log.append(f"פתיחת PR נכשלה: {e}")

    keepalive = gitops.enable_workflow(repo, "tracker.yml") if git and not dry_run else None

    out = {
        "mode": mode, "headlines": len(headlines), "detections": len(dets),
        "applied": [p.summary() for p in applied], "proposed": [p.summary() for p, _, _ in proposed],
        "pull_requests": pr_urls, "feed_errors": feed_errors, "classifier_errors": cls_errors, "log": log,
        "keepalive": keepalive,
    }
    step_summary([f"### ריצת עדכון {now:%d.%m.%Y %H:%M}", f"- מצב סיווג: {mode}",
                  f"- כותרות רלוונטיות: {len(headlines)}, זיהויים: {len(dets)}",
                  *[f"- ✅ הוחל: {s}" for s in out["applied"]],
                  *[f"- 📝 הוצע ב-PR: {s}" for s in out["proposed"]],
                  *[f"- ⚠️ {e}" for e in feed_errors + cls_errors]])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="classify and report, change nothing")
    ap.add_argument("--git", action="store_true", help="commit, push and open PRs (CI only)")
    ap.add_argument("--now", help="ISO datetime, Israel time")
    a = ap.parse_args()
    now = datetime.fromisoformat(a.now).replace(tzinfo=TZ) if a.now else datetime.now(TZ).replace(microsecond=0)
    print(json.dumps(run(now, a.dry_run, a.git), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

"""Build the static site: data.json, calendar.ics, scenarios.ics + the HTML/JS/CSS.

Usage: python -m tracker.build [--out _site] [--now 2026-11-07T22:00]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import ics
from .calendar_il import gregorian_he, hebrew_date, rest_day_name, weekday_he
from .rules import TZ, Item, Node, compute
from .state_io import (CACHE_DIR, REGISTRY_PATH, ROOT, load_config, load_json, load_state,
                       save_json)

SITE_SRC = ROOT / "site"


def _date_info(d: date) -> dict[str, Any]:
    return {"date": d.isoformat(), "hebrew": hebrew_date(d), "weekday": weekday_he(d),
            "gregorian": gregorian_he(d), "rest_day": rest_day_name(d)}


def _item_json(it: Item, now: datetime) -> dict[str, Any]:
    out = {
        "key": it.key, "title": it.title, "kind": it.kind, "category": it.category,
        **_date_info(it.day),
        "time": it.start.strftime("%H:%M") if it.start else None,
        "end_date": it.end_day.isoformat() if it.end_day else None,
        "start_iso": it.start_dt().isoformat(), "end_iso": it.end_dt().isoformat(),
        "past": it.end_dt() <= now,
        "explanation": it.explanation, "notes": it.notes,
        "law": {"name": it.law.name, "url": it.law.url} if it.law else None,
        "source": it.source, "location": it.location, "alarm": it.alarm,
    }
    return out


def _node_json(n: Node) -> dict[str, Any]:
    return {
        "id": n.id, "title": n.title, "note": n.note, "latest": n.latest,
        **({"date": n.day.isoformat(), "hebrew": hebrew_date(n.day), "weekday": weekday_he(n.day)} if n.day else {}),
        "law": {"name": n.law.name, "url": n.law.url} if n.law else None,
        "in_scenarios_calendar": bool(n.cal),
        "children": [_node_json(c) for c in n.children],
    }


def _target(it: Item) -> datetime:
    """Countdown target: the end of the day for all-day deadlines, the start otherwise."""
    if it.start is None and it.category in ("deadline", "possible"):
        return it.end_dt()
    return it.start_dt()


def _next_item(items: list[Item], now: datetime) -> Item | None:
    upcoming = [it for it in items if _target(it) > now]
    return min(upcoming, key=_target) if upcoming else None


def build(out: Path, now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(TZ)).astimezone(TZ).replace(microsecond=0)
    cfg = load_config()
    site_url = cfg["site_url"]
    state = load_state()
    res = compute(state, now)

    registry = load_json(REGISTRY_PATH, {})
    before = json.dumps(registry, sort_keys=True)
    cal_events = [ics.from_item(it, site_url) for it in res.items if it.calendar]
    cal_text = ics.render(cal_events, name=cfg["calendar_name"], uid_domain=cfg["uid_domain"], registry=registry,
                          now=now, description="מועדי הליך הרכבת הממשלה אחרי הבחירות לכנסת ה־26, לפי "
                                               "חוק־יסוד: הממשלה. מתעדכן אוטומטית.")
    scn_text = ics.render(ics.scenario_events(res.scenarios, site_url), name=cfg["scenarios_name"],
                          uid_domain=cfg["uid_domain"], registry=registry, now=now,
                          description="מועדים היפותטיים לפי תרחישים חלופיים (המועדים המאוחרים ביותר "
                                      "האפשריים בכל מסלול).")
    registry_changed = json.dumps(registry, sort_keys=True) != before
    if registry_changed:
        save_json(REGISTRY_PATH, registry)

    news = load_json(CACHE_DIR / "news.json", {"items": [], "checked_at": None, "mode": None})
    nxt = _next_item(res.items, now)
    status = dict(res.status)
    if status.get("deadline"):
        status["deadline_info"] = _date_info(date.fromisoformat(status["deadline"]))
    if status.get("assigned"):
        status["assigned_info"] = _date_info(date.fromisoformat(status["assigned"]))
    if status.get("elections"):
        status["elections_info"] = _date_info(date.fromisoformat(status["elections"]))
    status["next"] = None if nxt is None else {
        "key": nxt.key, "title": nxt.title, "kind": nxt.kind, "category": nxt.category,
        "target_iso": _target(nxt).isoformat(),
        **_date_info(nxt.day), "time": nxt.start.strftime("%H:%M") if nxt.start else None}

    base = site_url.rstrip("/") + "/"
    data = {
        "title": state.get("title") or cfg["calendar_name"],
        "knesset": state.get("knesset", 26),
        "generated_at": now.isoformat(),
        "checked_at": news.get("checked_at") or now.isoformat(),
        "classifier": news.get("mode"),
        "calendar_changed_at": max((r["modified"] for r in registry.values()), default=None),
        "today": _date_info(now.date()),
        "status": status,
        "items": [_item_json(it, now) for it in res.items],
        "scenarios": _node_json(res.scenarios),
        "news": news.get("items", [])[:25],
        "warnings": res.warnings,
        "links": {
            "site": base,
            "calendar_https": base + "calendar.ics",
            "calendar_webcal": "webcal://" + base.split("://", 1)[1] + "calendar.ics",
            "scenarios_https": base + "scenarios.ics",
            "scenarios_webcal": "webcal://" + base.split("://", 1)[1] + "scenarios.ics",
            "repo": f"https://github.com/{cfg['repo']}",
            "state": f"https://github.com/{cfg['repo']}/blob/main/state.yaml",
        },
    }

    out.mkdir(parents=True, exist_ok=True)
    for f in SITE_SRC.iterdir():
        if f.is_file() and f.name != "index.html":
            shutil.copy2(f, out / f.name)
    # cache-busting: GitHub Pages caches static files for 10 minutes
    html = (SITE_SRC / "index.html").read_text(encoding="utf-8")
    for name in ("app.js", "style.css"):
        v = hashlib.sha256((SITE_SRC / name).read_bytes()).hexdigest()[:8]
        html = html.replace(f'"{name}"', f'"{name}?v={v}"')
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    (out / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "calendar.ics").write_bytes(cal_text.encode("utf-8"))
    (out / "scenarios.ics").write_bytes(scn_text.encode("utf-8"))
    return {"registry_changed": registry_changed, "events": len(cal_events), "warnings": res.warnings,
            "phase": res.status["phase"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "_site"))
    ap.add_argument("--now", help="ISO datetime in Israel time (for testing)")
    a = ap.parse_args()
    now = datetime.fromisoformat(a.now).replace(tzinfo=TZ) if a.now else None
    info = build(Path(a.out), now)
    print(json.dumps(info, ensure_ascii=False))
    for w in info["warnings"]:
        print("::warning::" + w)


if __name__ == "__main__":
    main()

"use strict";
(() => {
  const TZ = "Asia/Jerusalem";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const CAT = { ceremony: "טקס", deadline: "מועד סיום", possible: "אפשרי", vote: "הצבעה", milestone: "אירוע", info: "עדכון" };
  let data = null;
  let timer = null;

  const israelToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: TZ }).format(new Date());
  const dayNum = (iso) => Math.round(Date.parse(iso + "T00:00:00Z") / 86400000);
  const daysBetween = (a, b) => dayNum(b) - dayNum(a);
  const dmy = (iso) => { const [y, m, d] = iso.split("-"); return `${+d}.${+m}.${y}`; };
  const dm = (iso) => { const [, m, d] = iso.split("-"); return `${+d}.${+m}`; };
  const fmtStamp = (iso) => iso ? new Intl.DateTimeFormat("he-IL", { timeZone: TZ, day: "numeric", month: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(iso)) : "—";
  const badge = (kind) => kind === "official" ? '<span class="badge official">רשמי</span>' : '<span class="badge computed">מחושב</span>';
  const link = (href, text) => href ? `<a href="${esc(href)}" target="_blank" rel="noopener">${esc(text)}</a>` : "";

  function countdown(targetIso) {
    let ms = Date.parse(targetIso) - Date.now();
    if (ms <= 0) return "עכשיו";
    const d = Math.floor(ms / 86400000); ms -= d * 86400000;
    const h = Math.floor(ms / 3600000); ms -= h * 3600000;
    const m = Math.floor(ms / 60000); const s = Math.floor((ms - m * 60000) / 1000);
    if (d >= 2) return `עוד ${d} ימים${h ? ` ו־${h} שעות` : ""}`;
    if (d === 1) return `עוד יום ו־${h} שעות`;
    const pad = (n) => String(n).padStart(2, "0");
    return `עוד ${pad(h)}:${pad(m)}:${pad(s)}`;
  }

  function fact(label, value, extra = "") {
    return `<div><dt>${esc(label)}</dt><dd>${value}${extra ? ` <small>${extra}</small>` : ""}</dd></div>`;
  }

  function dateFact(info, kind) {
    return `${esc(info.weekday)}, ${dmy(info.date)} ${kind ? badge(kind) : ""}<br><small>${esc(info.hebrew)}</small>`;
  }

  function item(key) { return data.items.find((i) => i.key === key); }

  function renderStatus() {
    const s = data.status;
    const today = israelToday();
    let html = `<p class="phase">${esc(s.phase_label)}${s.round ? ` · סבב ${s.round}` : ""}</p>`;
    const facts = [];
    if (s.phase === "forming" && s.holder) {
      html += `<h2 class="holder">${esc(s.holder)}${s.party ? `<span class="party">${esc(s.party)}</span>` : ""}</h2>`;
      const total = s.period_days;
      if (s.scheduled) {
        html += `<p class="dayline">המנדט יוטל ב${esc(s.assigned_info.weekday)}, ${dmy(s.assigned)}${s.assigned_time ? ` בשעה ${esc(s.assigned_time)}` : ""}. מאותו יום עומדים לרשותו ${total} ימים.</p>`;
        html += `<div class="progress" role="presentation"><span style="width:0%"></span></div>`;
      } else {
        const x = Math.max(0, Math.min(total, daysBetween(s.assigned, today)));
        const left = Math.max(0, daysBetween(today, s.deadline));
        html += x === 0
          ? `<p class="dayline">המנדט הוטל היום. הספירה של ${total} הימים מתחילה מחר.</p>`
          : `<p class="dayline">יום <b>${x}</b> מתוך ${total} · נותרו ${left} ימים</p>`;
        html += `<div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="${total}" aria-valuenow="${x}"><span style="width:${(100 * x / total).toFixed(1)}%"></span></div>`;
      }
      facts.push(fact(s.scheduled ? "המנדט יוטל" : "המנדט הוטל", dateFact(s.assigned_info, "official")));
      facts.push(fact("המועד האחרון להרכבה", dateFact(s.deadline_info, s.deadline_kind)));
      const ext = item("r1-max-extension");
      if (s.round === 1 && ext) facts.push(fact("אם תינתן הארכה מלאה", dateFact(ext, "computed"), `עד ${s.ext_remaining} ימים נוספים`));
      else if (s.round === 1 && s.ext_used) facts.push(fact("הארכה", `${s.ext_used} ימים ניתנו`));
      else facts.push(fact("הארכה", "החוק לא מתיר הארכה בסבב הזה"));
    } else if (s.phase === "president_decision") {
      html += `<h2 class="holder">ההכרעה אצל הנשיא</h2>`;
      const it = item("r1-president-decision");
      if (it) facts.push(fact("מועד אחרון להחלטה", dateFact(it, it.kind)));
    } else if (s.phase === "window") {
      html += `<h2 class="holder">חלון 21 הימים של הכנסת</h2><p class="muted">61 חברי כנסת יכולים לבקש מהנשיא בכתב להטיל את המשימה על חבר כנסת מסוים.</p>`;
      const it = item("window-deadline");
      if (it) facts.push(fact("סיום החלון", dateFact(it, it.kind)));
    } else if (s.phase === "success") {
      html += `<h2 class="holder">${esc(s.holder || "")} הודיע שהרכיב ממשלה</h2>`;
      const it = item("confidence-vote") || item("confidence-session-deadline");
      if (it) facts.push(fact(it.key === "confidence-vote" ? "הצבעת האמון" : "מועד אחרון להצבעת האמון", dateFact(it, it.kind), it.time ? `בשעה ${esc(it.time)}` : ""));
    } else if (s.phase === "formed") {
      html += `<h2 class="holder">הממשלה כוננה</h2>`;
      const it = item("confidence-vote");
      if (it) facts.push(fact("הצבעת האמון", dateFact(it, it.kind)));
    } else if (s.phase === "dissolved") {
      html += `<h2 class="holder">הכנסת התפזרה — הולכים לבחירות</h2>`;
      if (s.elections_info) facts.push(fact(`הבחירות לכנסת ה־${data.knesset + 1}`, dateFact(s.elections_info, item("next-elections")?.kind)));
    } else {
      html += `<h2 class="holder">ממתינים להטלת המנדט</h2>`;
    }
    if (facts.length) html += `<dl class="facts">${facts.join("")}</dl>`;
    if (s.next) {
      const when = s.next.time ? `${esc(s.next.weekday)} ${dm(s.next.date)} בשעה ${esc(s.next.time)}` : `${esc(s.next.weekday)} ${dm(s.next.date)}${s.next.category === "deadline" || s.next.category === "possible" ? " (עד חצות)" : ""}`;
      html += `<div class="next"><div class="label">המועד הבא</div><div class="what">${esc(s.next.title)} ${badge(s.next.kind)}</div><div class="muted small">${when}</div><div class="countdown" id="cd" data-target="${esc(s.next.target_iso)}"></div></div>`;
    }
    $("status").innerHTML = html;
  }

  function renderTimeline() {
    const out = [];
    let markerDone = false;
    for (const it of data.items) {
      const past = Date.parse(it.end_iso) <= Date.now();
      if (!past && !markerDone) { out.push(`<li class="now-marker"><span>היום · ${dmy(israelToday())}</span></li>`); markerDone = true; }
      const dateTxt = `<b>${esc(it.weekday)}, ${dmy(it.date)}${it.end_date && it.end_date !== it.date ? ` עד ${dmy(it.end_date)}` : ""}</b>${it.time ? ` · ${esc(it.time)}` : ""} · ${esc(it.hebrew)}`;
      const body = [it.explanation, ...(it.notes || [])].filter(Boolean).map((t) => `<p>${esc(t)}</p>`).join("");
      const links = [
        it.law ? link(it.law.url, it.law.name) : "",
        it.source ? link(it.source, "מקור") : "",
      ].filter(Boolean).join("");
      out.push(`<li class="${past ? "past" : ""} ${esc(it.category)}">
        <div class="t-date">${dateTxt}</div>
        <div class="t-title">${esc(it.title)}</div>
        <div class="t-badges">${badge(it.kind)}<span class="badge cat">${CAT[it.category] || ""}</span>${it.location ? `<span class="badge cat">${esc(it.location)}</span>` : ""}</div>
        ${body ? `<div class="t-body">${body}</div>` : ""}
        ${links ? `<div class="t-links">${links}</div>` : ""}
      </li>`);
    }
    if (!markerDone) out.push(`<li class="now-marker"><span>היום · ${dmy(israelToday())}</span></li>`);
    $("timeline").innerHTML = out.join("");
  }

  function nodeHtml(n, depth) {
    const date = n.date ? `<div class="n-date">${n.latest ? "לכל המאוחר " : ""}${esc(n.weekday)}, ${dmy(n.date)} · ${esc(n.hebrew)}${n.law ? ` · ${link(n.law.url, n.law.name.replace("חוק־יסוד: הממשלה, ", ""))}` : ""}</div>` : "";
    const card = `<div class="node"><div class="n-title">${esc(n.title)}</div>${date}${n.note ? `<div class="n-note">${esc(n.note)}</div>` : ""}</div>`;
    if (!n.children.length) return `<li>${card}</li>`;
    const kids = `<ul>${n.children.map((c) => nodeHtml(c, depth + 1)).join("")}</ul>`;
    return `<li><details ${depth < 1 ? "open" : ""}><summary>${card}</summary>${kids}</details></li>`;
  }

  function renderTree() {
    const root = data.scenarios;
    $("tree").innerHTML = `<div class="root">${esc(root.title)}</div>` + (root.children.length ? `<ul>${root.children.map((c) => nodeHtml(c, 0)).join("")}</ul>` : "");
  }

  function ago(iso) {
    const min = Math.round((Date.now() - Date.parse(iso)) / 60000);
    if (min < 1) return "עכשיו";
    if (min < 60) return `לפני ${min} דק׳`;
    if (min < 24 * 60) return `לפני ${Math.round(min / 60)} שע׳`;
    return fmtStamp(iso);
  }

  function renderNews() {
    const items = data.news || [];
    $("news").innerHTML = items.length
      ? items.map((n) => `<li><a dir="auto" href="${esc(n.link)}" target="_blank" rel="noopener">${esc(n.title)}</a><div class="meta">${esc(n.publisher)} · <time datetime="${esc(n.published)}">${ago(n.published)}</time></div></li>`).join("")
      : '<li class="muted">אין עדיין כותרות. הן יופיעו אחרי הבדיקה האוטומטית הבאה.</li>';
  }

  function renderMeta() {
    const L = data.links;
    $("webcal").href = L.calendar_webcal;
    $("webcal-scn").href = L.scenarios_webcal;
    $("repo").href = L.repo;
    $("copy").onclick = async () => {
      try { await navigator.clipboard.writeText(L.calendar_https); $("copy").textContent = "הכתובת הועתקה ✓"; }
      catch { prompt("העתיקו את הכתובת:", L.calendar_https); }
    };
    const mode = data.classifier === "ai" ? " · זיהוי אירועים: Claude Haiku" : data.classifier === "keywords" ? " · זיהוי אירועים: מילות מפתח" : "";
    $("updated").textContent = `בדיקה אחרונה: ${fmtStamp(data.checked_at)} · עדכון אחרון ביומן: ${data.calendar_changed_at ? fmtStamp(data.calendar_changed_at.replace(/^(\d{4})(\d\d)(\d\d)T(\d\d)(\d\d)(\d\d)Z$/, "$1-$2-$3T$4:$5:$6Z")) : "—"}${mode}`;
    const w = data.warnings || [];
    $("warnings").hidden = !w.length;
    $("warnings").innerHTML = w.length ? `<h2>הערות לבדיקה</h2><ul>${w.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "";
  }

  function tick() {
    const el = $("cd");
    if (el) el.textContent = countdown(el.dataset.target);
  }

  async function load() {
    try {
      const res = await fetch(`data.json?t=${Date.now()}`, { cache: "no-store" });
      data = await res.json();
    } catch (e) {
      if (!data) $("status").innerHTML = '<p class="loading">לא הצלחנו לטעון את הנתונים. נסו לרענן.</p>';
      return;
    }
    renderStatus(); renderTimeline(); renderTree(); renderNews(); renderMeta(); tick();
    clearInterval(timer); timer = setInterval(tick, 1000);
  }

  load();
  setInterval(load, 10 * 60 * 1000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
})();

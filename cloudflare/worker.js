// memshala.gabimansour.com
//
// 1. Serves the GitHub Pages site (GitHub Actions builds it; this Worker only fronts the domain).
// 2. Every 10 minutes (cron) collects live data and stores it in KV, served at /live.json:
//    - the Knesset's official open data (OData v4): plenary sessions, their agenda,
//      votes with for/against/abstain counts, and the current Speaker;
//    - headlines from the news sites' own RSS feeds (Google News blocks Cloudflare).
//    GitHub's scheduler runs only every few hours, so the live parts of the site come from here.
const ORIGIN = "https://gabimansoure-coder.github.io/memshala-2026";
const ODATA = "https://knesset.gov.il/OdataV4/ParliamentInfo/";
const UA = "memshala-2026/1.0 (+https://memshala.gabimansour.com)";
const SPEAKER_POSITIONS = [122, 123];   // יושב/ת ראש הכנסת
const MAX_NEW_TALLIES = 8;              // per run, to stay gentle with the Knesset's servers
const DAY = 86400000;

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method not allowed", { status: 405, headers: { Allow: "GET, HEAD" } });
    }
    const url = new URL(request.url);
    if (url.pathname === "/live.json") return serveLive(env, ctx);
    return proxy(request, url);
  },
  async scheduled(event, env, ctx) {
    ctx.waitUntil(refresh(env));
  },
};

async function proxy(request, url) {
  const upstream = await fetch(ORIGIN + url.pathname + url.search, {
    method: request.method,
    headers: { "User-Agent": request.headers.get("User-Agent") || "memshala-2026-proxy" },
    redirect: "follow",
    cf: { cacheEverything: true, cacheTtl: 120 },
  });
  const response = new Response(upstream.body, upstream);
  const fresh = /\.(ics|json)$/.test(url.pathname);
  response.headers.set("Cache-Control", `public, max-age=${fresh ? 300 : 600}`);
  response.headers.delete("x-github-request-id");
  return response;
}

const STALE_AFTER = 15 * 60 * 1000;

async function serveLive(env, ctx) {
  let body = await env.LIVE.get("live");
  if (!body) {
    body = JSON.stringify(await refresh(env));
  } else {
    // safety net: if a cron run was missed, the next visitor triggers a refresh in the background
    const age = Date.now() - Date.parse(JSON.parse(body).updated_at || 0);
    if (!(age < STALE_AFTER)) ctx.waitUntil(refresh(env));
  }
  return new Response(body, {
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "public, max-age=60",
      "Access-Control-Allow-Origin": "*",
    },
  });
}

// ------------------------------------------------------------------------------------
async function refresh(env) {
  const errors = [];
  const prev = JSON.parse((await env.LIVE.get("live")) || "{}");
  let cfg = {};
  try {
    const r = await fetch(ORIGIN + "/live-config.json", { cf: { cacheTtl: 600 } });
    cfg = await r.json();
  } catch (e) {
    errors.push("live-config: " + e);
  }
  const [knesset, news] = await Promise.all([
    collectKnesset(cfg, prev.knesset || {}, errors).catch((e) => (errors.push("knesset: " + e), prev.knesset || null)),
    collectNews(cfg, errors).catch((e) => (errors.push("news: " + e), prev.news || [])),
  ]);
  const data = { updated_at: new Date().toISOString(), knesset, news, errors };
  await env.LIVE.put("live", JSON.stringify(data));
  return data;
}

async function odata(table, params) {
  const qs = Object.entries(params).map(([k, v]) => `$${k}=${encodeURIComponent(v)}`).join("&");
  const r = await fetch(`${ODATA}${table}?${qs}`, { headers: { "User-Agent": UA, Accept: "application/json" } });
  if (!r.ok) throw new Error(`${table} HTTP ${r.status}`);
  return r.json();
}

async function collectKnesset(cfg, prev, errors) {
  const K = cfg.knesset || 26;
  const now = Date.now();
  const sessions = (await odata("KNS_PlenumSession", {
    filter: `KnessetNum eq ${K}`, orderby: "StartDate desc", top: "12",
    select: "Id,Number,Name,StartDate,FinishDate,IsSpecialMeeting",
  })).value;
  const upcoming = sessions.filter((s) => !s.FinishDate && Date.parse(s.StartDate) > now - DAY)
    .sort((a, b) => Date.parse(a.StartDate) - Date.parse(b.StartDate));
  const past = sessions.filter((s) => s.FinishDate);
  const withItems = [...upcoming, ...past.slice(0, 2)];
  let items = [];
  if (withItems.length) {
    items = (await odata("KNS_PlmSessionItem", {
      filter: withItems.map((s) => `PlenumSessionID eq ${s.Id}`).join(" or "),
      orderby: "PlenumSessionID,Ordinal", top: "100",
      select: "PlenumSessionID,ItemID,ItemTypeDesc,Ordinal,Name",
    })).value;
  }
  const since = new Date(now - 21 * DAY).toISOString().slice(0, 19) + "Z";
  const votes = (await odata("KNS_PlenumVote", {
    filter: `VoteDateTime ge ${since}`, orderby: "VoteDateTime desc", top: "60",
    select: "Id,VoteDateTime,SessionID,ItemID,VoteTitle,VoteSubject,VoteStatusDesc,ForOptionDesc,AgainstOptionDesc,IsNoConfidenceInGov",
  })).value;

  // for / against / abstain counts; published votes do not change, so they are kept between runs
  const tallies = prev.tallies || {};
  let budget = MAX_NEW_TALLIES;
  for (const v of votes) {
    if (tallies[v.Id] || budget <= 0) continue;
    budget--;
    try {
      const t = await tally(v.Id);
      if (t.total) tallies[v.Id] = t;   // results not published yet: try again next run
    } catch (e) {
      errors.push(`tally ${v.Id}: ${e}`);
    }
  }
  const keep = new Set(votes.map((v) => String(v.Id)));
  for (const id of Object.keys(tallies)) if (!keep.has(id)) delete tallies[id];

  let speaker = prev.speaker || null;
  try {
    const pos = (await odata("KNS_PersonToPosition", {
      filter: `(${SPEAKER_POSITIONS.map((p) => `PositionID eq ${p}`).join(" or ")}) and KnessetNum eq ${K} and FinishDate eq null`,
      orderby: "StartDate desc", top: "1", select: "PersonID,StartDate",
    })).value[0];
    if (pos) {
      const p = (await odata("KNS_Person", { filter: `Id eq ${pos.PersonID}`, select: "FirstName,LastName" })).value[0];
      speaker = { name: `${p.FirstName} ${p.LastName}`, since: pos.StartDate };
    }
  } catch (e) {
    errors.push("speaker: " + e);
  }

  const byId = Object.fromEntries(sessions.map((s) => [s.Id, s]));
  return {
    source: ODATA,
    speaker,
    upcoming: upcoming.map((s) => sessionJson(s, items)),
    recent: past.slice(0, 6).map((s) => sessionJson(s, items)),
    votes: votes.map((v) => voteJson(v, tallies[v.Id], byId[v.SessionID])),
    tallies,
  };
}

async function tally(voteId) {
  const counts = { for: 0, against: 0, abstain: 0, total: 0 };
  let url = `${ODATA}KNS_PlenumVoteResult?$filter=${encodeURIComponent(`VoteID eq ${voteId}`)}&$select=ResultDesc&$top=100`;
  for (let page = 0; url && page < 3; page++) {
    const r = await fetch(url, { headers: { "User-Agent": UA, Accept: "application/json" } });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const j = await r.json();
    for (const row of j.value) {
      counts.total++;
      if (row.ResultDesc === "בעד") counts.for++;
      else if (row.ResultDesc === "נגד") counts.against++;
      else if (row.ResultDesc && row.ResultDesc.startsWith("נמנע")) counts.abstain++;
    }
    url = j["@odata.nextLink"];
  }
  return counts;
}

function sessionJson(s, items) {
  return {
    id: s.Id, number: s.Number, name: s.Name, start: s.StartDate, finish: s.FinishDate,
    special: !!s.IsSpecialMeeting,
    items: items.filter((i) => i.PlenumSessionID === s.Id)
      .map((i) => ({ ordinal: i.Ordinal, name: i.Name, type: i.ItemTypeDesc })),
  };
}

function voteJson(v, t, session) {
  let outcome = null;
  if (t && t.total) {
    // simple majority of those voting; dissolving the Knesset and no-confidence need 61
    const needs61 = !!v.IsNoConfidenceInGov || (/התפזרות הכנסת/.test(v.VoteTitle || "") && /שלישית/.test(v.VoteTitle || ""));
    outcome = needs61 ? (t.for >= 61 ? "accepted" : "rejected") : (t.for > t.against ? "accepted" : "rejected");
  }
  return {
    id: v.Id, time: v.VoteDateTime, title: v.VoteTitle, subject: v.VoteSubject, status: v.VoteStatusDesc,
    for_label: v.ForOptionDesc, against_label: v.AgainstOptionDesc, no_confidence: !!v.IsNoConfidenceInGov,
    session: session ? session.Name : null, counts: t || null, outcome,
    url: `${ODATA}KNS_PlenumVote(${v.Id})`,
  };
}

// ------------------------------------------------------------------------------------
async function collectNews(cfg, errors) {
  const feeds = cfg.feeds || [];
  const relevance = (cfg.relevance || []).map((k) => k.toLowerCase());
  const names = cfg.publisher_names || {};
  const cutoff = Date.now() - (cfg.news_days || 3) * DAY;
  const results = await Promise.all(feeds.map(async (f) => {
    try {
      const r = await fetch(f.url, { headers: { "User-Agent": UA } });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return parseRss(await r.text()).map((it) => ({ ...it, publisher: names[f.publisher] || f.publisher }));
    } catch (e) {
      errors.push(`${f.name}: ${e}`);
      return [];
    }
  }));
  const seen = new Set();
  return results.flat()
    .filter((it) => it.published >= cutoff && relevance.some((k) => it.title.toLowerCase().includes(k)))
    .sort((a, b) => b.published - a.published)
    .filter((it) => {
      const key = it.title.replace(/[^\p{L}\p{N}]+/gu, " ").trim();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, 30)
    .map((it) => ({ title: it.title, publisher: it.publisher, link: it.link, published: new Date(it.published).toISOString() }));
}

function parseRss(xml) {
  const out = [];
  for (const m of xml.matchAll(/<item\b[\s\S]*?<\/item>/g)) {
    const block = m[0];
    const title = decode(tag(block, "title"));
    const link = decode(tag(block, "link")).trim();
    const published = Date.parse(tag(block, "pubDate") || tag(block, "dc:date"));
    if (title && /^https?:\/\//.test(link) && published) out.push({ title, link, published });
  }
  return out;
}

function tag(block, name) {
  const m = block.match(new RegExp(`<${name}\\b[^>]*>([\\s\\S]*?)</${name}>`));
  if (!m) return "";
  return m[1].replace(/^\s*<!\[CDATA\[/, "").replace(/\]\]>\s*$/, "").trim();
}

function decode(s) {
  return s
    .replace(/<[^>]+>/g, "")
    .replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(+n))
    .replace(/&#x([0-9a-f]+);/gi, (_, n) => String.fromCodePoint(parseInt(n, 16)))
    .replace(/&quot;/g, '"').replace(/&apos;|&#39;/g, "'").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&")
    .replace(/\s+/g, " ")
    .trim();
}

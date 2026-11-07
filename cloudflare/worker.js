// Serves the GitHub Pages site on memshala.gabimansour.com.
// GitHub Pages stays the origin (GitHub Actions keeps deploying there every 30 minutes);
// this Worker only puts the custom domain in front of it.
const ORIGIN = "https://gabimansoure-coder.github.io/memshala-2026";

export default {
  async fetch(request) {
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method not allowed", { status: 405, headers: { Allow: "GET, HEAD" } });
    }
    const url = new URL(request.url);
    const upstream = await fetch(ORIGIN + url.pathname + url.search, {
      method: request.method,
      headers: { "User-Agent": request.headers.get("User-Agent") || "memshala-2026-proxy" },
      redirect: "follow",
      cf: { cacheEverything: true, cacheTtl: 120 },
    });
    const response = new Response(upstream.body, upstream);
    // calendars and data refresh often; everything else can be cached a little longer
    const fresh = /\.(ics|json)$/.test(url.pathname);
    response.headers.set("Cache-Control", `public, max-age=${fresh ? 300 : 600}`);
    response.headers.delete("x-github-request-id");
    return response;
  },
};

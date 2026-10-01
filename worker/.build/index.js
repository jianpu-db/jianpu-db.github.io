const IMG_PREFIX = "/img/";
const IMG_CACHE = "public, max-age=604800, immutable";
const IMG_EXT = /* @__PURE__ */ new Set([".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"]);
const MIME = {
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".png": "image/png",
  ".gif": "image/gif",
  ".webp": "image/webp",
  ".bmp": "image/bmp"
};
let OG_CACHE = null;
let OG_ERR = "";
function httpsRedirectUrl(url) {
  if (!url || url.protocol !== "http:") return "";
  const u = new URL(url.toString());
  u.protocol = "https:";
  return u.toString();
}
const esc = (s) => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
function injectSongMeta(html, meta, opts) {
  if (!html || !meta) return html;
  const [title, artist, notes] = meta;
  if (!title) return html;
  const o = opts ?? {};
  const label = artist ? `${title}（${artist}）` : title;
  const pageTitle = `${label} · 简谱 | jianpu-db`;
  const desc = `${label}的简谱：${notes} 个音符。哼开头几个音就能把这首歌的谱找出来 —— jianpu-db。`;
  const url = o.origin && o.id ? `${o.origin}/s/${encodeURIComponent(o.id)}` : "";
  const pairs = [
    [/<title>[^<]*<\/title>/, () => `<title>${esc(pageTitle)}</title>`],
    [/(<meta name="description" content=")[^"]*(")/, (m, a, b) => a + esc(desc) + b],
    [/(<meta property="og:title" content=")[^"]*(")/, (m, a, b) => a + esc(pageTitle) + b],
    [/(<meta property="og:description" content=")[^"]*(")/, (m, a, b) => a + esc(desc) + b],
    [/(<meta name="twitter:title" content=")[^"]*(")/, (m, a, b) => a + esc(pageTitle) + b],
    [/(<meta name="twitter:description" content=")[^"]*(")/, (m, a, b) => a + esc(desc) + b]
  ];
  if (url) {
    pairs.push([/(<link rel="canonical" href=")[^"]*(")/, (m, a, b) => a + esc(url) + b]);
    pairs.push([/(<meta property="og:url" content=")[^"]*(")/, (m, a, b) => a + esc(url) + b]);
  }
  let out = html;
  for (const [re, fn] of pairs) out = out.replace(re, fn);
  return out;
}
async function ogIndex(env, origin) {
  if (OG_CACHE && OG_CACHE !== "pending") return OG_CACHE;
  if (OG_CACHE === "pending") return null;
  OG_CACHE = "pending";
  try {
    const r = await env.ASSETS.fetch(new URL("/data/og.json", origin).toString());
    if (!r.ok) {
      OG_ERR = "HTTP " + r.status;
      OG_CACHE = null;
      return null;
    }
    OG_CACHE = await r.json();
    return OG_CACHE;
  } catch (e) {
    OG_ERR = String(e?.message ?? e);
    OG_CACHE = null;
    return null;
  }
}
async function serveSongPage(request, env, url) {
  const res = await env.ASSETS.fetch(new URL("/index.html", url.origin).toString());
  try {
    const id = decodeURIComponent(url.pathname.replace(/^\/s\/?/, "").replace(/\/$/, ""));
    const idx = await ogIndex(env, url.origin);
    const meta = idx && id ? idx[id] : null;
    if (!meta) return res;
    const html = injectSongMeta(await res.text(), meta, { origin: url.origin, id });
    const h = new Headers(res.headers);
    h.delete("content-length");
    return new Response(html, { status: res.status, headers: h });
  } catch {
    return res;
  }
}
async function probeUpstream(env) {
  const up = (env.API_UPSTREAM || "").replace(/\/+$/, "");
  if (!up) return { ok: false, err: "not-configured" };
  try {
    const headers = {};
    if (env.API_TOKEN) headers["X-Token"] = env.API_TOKEN;
    const r = await fetch(up + "/api/health", {
      method: "GET",
      headers,
      signal: AbortSignal.timeout(3e3)
    });
    return { ok: r.ok, err: r.ok ? "" : "HTTP " + r.status };
  } catch (e) {
    const err = e;
    return { ok: false, err: err?.name === "TimeoutError" ? "timeout" : String(err?.name || err) };
  }
}
var worker_default = {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;
    const toHttps = httpsRedirectUrl(url);
    if (toHttps) return Response.redirect(toHttps, 301);
    if (path.startsWith(IMG_PREFIX)) {
      return serveImage(request, env, url);
    }
    if (path.startsWith("/api/")) {
      if (request.method === "OPTIONS") return preflight();
      if (path === "/api/health") {
        const og = await ogIndex(env, url.origin);
        const up = await probeUpstream(env);
        return json({
          ok: true,
          deploy: "cloudflare-worker",
          images: env.IMAGES ? "r2" : env.IMG_UPSTREAM ? "proxy" : "none",
          api: !!env.API_UPSTREAM,
          upstream: env.API_UPSTREAM || null,
          upstreamOk: up.ok,
          upstreamErr: up.err,
          og: og ? Object.keys(og).length : 0,
          ogErr: OG_ERR
        });
      }
      return proxyApi(request, env, url);
    }
    if (path === "/s" || path.startsWith("/s/")) {
      return serveSongPage(request, env, url);
    }
    return env.ASSETS.fetch(request);
  }
};
async function serveImage(request, env, url) {
  if (request.method !== "GET" && request.method !== "HEAD") {
    return new Response("method not allowed", { status: 405 });
  }
  const raw = url.pathname.slice(IMG_PREFIX.length);
  const keys = [];
  try {
    const dec = decodeURIComponent(raw);
    keys.push(dec);
    if (dec !== raw) keys.push(raw);
  } catch {
    keys.push(raw);
  }
  const bad = (k) => !k || k.includes("\\") || k.split("/").some((p) => p === "" || p === "." || p === "..");
  const ext = ((keys[0] ?? "").match(/\.[a-z0-9]+$/i) ?? [""])[0].toLowerCase();
  if (bad(keys[0]) || !IMG_EXT.has(ext)) {
    return new Response("bad path", { status: 404 });
  }
  let obj = null;
  if (env.IMAGES) {
    for (const k of keys) {
      obj = await env.IMAGES.get(k);
      if (obj) break;
    }
  }
  if (!obj) {
    if (env.IMG_UPSTREAM) {
      return proxyFetch(request, env, env.IMG_UPSTREAM.replace(/\/+$/, "") + url.pathname + url.search);
    }
    return new Response(
      env.IMAGES ? "not found" : "R2 桶没绑定, 也没配 IMG_UPSTREAM(见 wrangler.jsonc)",
      { status: env.IMAGES ? 404 : 503 }
    );
  }
  const h = new Headers();
  obj.writeHttpMetadata(h);
  if (!h.get("content-type") || h.get("content-type") === "application/octet-stream") {
    h.set("content-type", MIME[ext] ?? "application/octet-stream");
  }
  h.set("etag", obj.httpEtag);
  h.set("cache-control", IMG_CACHE);
  return new Response(request.method === "HEAD" ? null : obj.body, { headers: h });
}
async function proxyApi(request, env, url) {
  const upstream = (env.API_UPSTREAM || "").replace(/\/+$/, "");
  if (!upstream) {
    return json({ ok: false, err: "这台部署没有配投稿后端: 投稿要在作者本机的服务上跑（wrangler secret put API_UPSTREAM / API_TOKEN）" }, 503);
  }
  return proxyFetch(request, env, upstream + url.pathname + url.search);
}
async function proxyFetch(request, env, target) {
  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.delete("cf-connecting-ip");
  if (env.API_TOKEN) {
    headers.set("X-Token", env.API_TOKEN);
  }
  headers.set("X-Forwarded-Proto", "https");
  const init = { method: request.method, headers, redirect: "manual" };
  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = request.body;
  }
  let res;
  try {
    res = await fetch(target, init);
  } catch (e) {
    return json({ ok: false, err: "连不上本机后端（服务没开? 隧道断了?）: " + e.message }, 502);
  }
  const out = new Headers(res.headers);
  out.set("Access-Control-Allow-Origin", "*");
  return new Response(res.body, { status: res.status, headers: out });
}
function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "Access-Control-Allow-Origin": "*"
    }
  });
}
function preflight() {
  return new Response(null, {
    status: 204,
    headers: {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
      "Access-Control-Max-Age": "86400"
    }
  });
}
export {
  worker_default as default,
  httpsRedirectUrl,
  injectSongMeta
};

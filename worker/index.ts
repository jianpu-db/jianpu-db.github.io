/**
 * 简谱旋律查歌 —— Cloudflare Worker（把本机那套服务搬到边缘, 但**写路径仍留在本机**）
 *
 * 同一个域名下三类请求:
 *   /img/<相对工作区的路径>   → R2 桶里的原图（原图 5GB, 进不了仓库也进不了 Worker; R2 免费 10GB）
 *                              R2 里没有 / 没绑桶时, 如果配了 IMG_UPSTREAM, 就**反代回本机**取
 *                              —— 于是"不想开 R2 / 桶还没建"也能先把站点跑起来（代价: 原图走家里上行）
 *   /api/*                    → **反向代理**到本机的 app/server.py（要配 API_UPSTREAM）
 *   其它（/、/static/*、/data/*、/s/<id>）
 *                             → env.ASSETS（构建产物 dist/）; 找不到的路径由
 *                               not_found_handling="single-page-application" 兜回 index.html,
 *                               `/s/<id>` 这种深链因此直接可用
 *
 * 为什么 /api/* 是代理而不是在这里重写:
 *   投稿要写 scores/*.txt + git commit, 而"怎么校验一个收录页 URL""怎么把简谱数字归一化"
 *   这些口径**只有一份实现**（jianpu-db/linkurl.py + score.py + schema.py）。在这里用 JS 再写一遍
 *   必然漂。所以 Worker 只负责"把请求转给本机、把本机的话原样转回来"。
 *   代价: 投稿需要本机在线（读路径完全不需要 —— 检索、卡片、谱页、原图都在边缘）。
 *
 * 配好之后 /api/health 会显示 api 是 true/false, 一眼看出这台部署有没有投稿后端。
 *
 * 2026-10-01 TypeScript 化（B 阶段）: 加类型。**Worker 的运行时是 Workers（不是 DOM）**, 所以
 * 它的类型来自 `@cloudflare/workers-types`，单独一份 `tsconfig.worker.json`；
 * 与浏览器侧的 `tsconfig.json`（DOM lib）**不能混在一份配置里**（两套全局声明会打架）。
 */

/** Worker 的绑定（`wrangler.jsonc` 里的 assets / vars / secrets / R2） */
export interface Env {
  /** Workers Assets 绑定：构建产物 dist/ */
  ASSETS: Fetcher;
  /** R2 桶（可选；没绑就用 IMG_UPSTREAM 反代，两者都没有则 503） */
  IMAGES?: R2Bucket;
  /** 本机写服务的公网地址（隧道），例如 https://xxx.trycloudflare.com */
  API_UPSTREAM?: string;
  /** 与本机 JPSUBMIT_TOKEN 同值的口令；有它才会往上游发 X-Token */
  API_TOKEN?: string;
  /** 原图反代目标（没开 R2 时用） */
  IMG_UPSTREAM?: string;
}

/** `data/og.json` 里一首歌的三元组：[曲名, 首位歌手, 音符数] */
export type OgMeta = [string, string, number];

/** `data/og.json` 整体：id -> OgMeta */
export type OgIndex = Record<string, OgMeta>;

const IMG_PREFIX = '/img/';
const IMG_CACHE = 'public, max-age=604800, immutable';
const IMG_EXT = new Set(['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']);
const MIME: Record<string, string> = {
  '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
  '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp',
};

// ⚠ 这两行被我自己弄丢过一次（2026-09-30 上线当天）：加 `httpsRedirectUrl` 的那次编辑把
//   `let OG_CACHE` 那行**替换掉了**，于是 `OG_CACHE = 'pending'` 变成给未声明变量赋值 —— ESM 严格模式下
//   抛 ReferenceError。后果很隐蔽: `ogIndex()` 里那句判断在 `try` **外面**，所以异常不是被它自己吞掉，
//   而是冒到调用方 —— `serveSongPage` 有 try/catch, **页面照样打开、只是标题退回通用的**；
//   `/api/health` 没包 try，直接 500。表现就是"站点好的，功能没了"。
//   教训: `let`/`const` 声明别写在会被整段替换的位置；`/api/health` 里那个 `og` 计数就是为了让这种
//   "悄悄退化"以后一眼看得出来（`og: 0` 或 500 都说明注入没在干活）。
//   （TS 化之后这类问题会在编译期就报 `Cannot find name` —— 类型系统替我们把这类坑堵掉一层。）
let OG_CACHE: OgIndex | 'pending' | null = null;
let OG_ERR = '';                        // 取 og.json 失败的原因（从 /api/health 的 ogErr 读）

// ══════════════════════════════════════════════════════════════════════════════
// 「每谱一页」的分享/收录元数据（2026-09-30 加正式域名时一起做的）
//
// 为什么必须在这儿做: 前端是 SPA —— **爬虫不跑 JS**。没有这一段，sitemap 里 1.1 万个
// `/s/<id>` 在爬虫眼里是**同一份 HTML**（同一个 <title>、同一段 description），
// 于是"1.1 万个页面"只等于 1 个可索引页；分享到群里也永远是同一张卡。
// 数据来自构建期生成的 `data/og.json`（`id -> [曲名, 歌手, 音符数]`，见 tools/build_web_data.py）。
//
// 三条纪律:
//   ① **任何异常都退回原始 HTML** —— 这段是锦上添花，绝不能因为它让谱页打不开；
//   ② `og.json` 只在模块作用域缓存一次（isolate 活着就一直用；部署后自然刷新）；
//   ③ 注入走**函数式替换**（不是字符串拼 `$1`）—— 曲名里出现 `$&` 之类时不会被当成替换模式。
// ══════════════════════════════════════════════════════════════════════════════

/** `http://` 的请求 -> 该 301 到的 `https://` 地址；本来就是 https 就返回空串。
 *
 * 为什么在代码里做而不是开面板开关: 站点绑的是自定义域名，Cloudflare 的 "Always Use HTTPS"
 * 默认**不一定**开着（2026-09-30 实测 `http://jianpu-db.org/` 直接 200 返回）。放在这里的好处是
 * ——跟别的行为一样进仓库、有测试、部署即生效，不依赖谁记得去点那个开关。
 * 只动协议，路径/查询串原样保留（分享出去的 `?q=…` 深链不会丢参数）。
 */
export function httpsRedirectUrl(url: URL | null | undefined): string {
  if (!url || url.protocol !== 'http:') return '';
  const u = new URL(url.toString());
  u.protocol = 'https:';
  return u.toString();
}

const esc = (s: unknown): string => String(s == null ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

/** 把一首歌的标题/描述写进 HTML 的 head（纯函数，便于离线测试）。 */
export function injectSongMeta(
  html: string,
  meta: OgMeta | null | undefined,
  opts?: { origin?: string; id?: string },
): string {
  if (!html || !meta) return html;
  const [title, artist, notes] = meta;
  if (!title) return html;
  const o = opts ?? {};
  const label = artist ? `${title}（${artist}）` : title;
  const pageTitle = `${label} · 简谱 | jianpu-db`;
  const desc = `${label}的简谱：${notes} 个音符。哼开头几个音就能把这首歌的谱找出来 —— jianpu-db。`;
  const url = o.origin && o.id ? `${o.origin}/s/${encodeURIComponent(o.id)}` : '';
  // 用**函数式替换**：曲名里带 `$&`/`\1` 时不会被当成替换模式（JS 侧一直如此；Python 侧当初踩过）。
  type Repl = (...args: string[]) => string;
  const pairs: Array<[RegExp, Repl]> = [
    [/<title>[^<]*<\/title>/, () => `<title>${esc(pageTitle)}</title>`],
    [/(<meta name="description" content=")[^"]*(")/, (m, a, b) => a + esc(desc) + b],
    [/(<meta property="og:title" content=")[^"]*(")/, (m, a, b) => a + esc(pageTitle) + b],
    [/(<meta property="og:description" content=")[^"]*(")/, (m, a, b) => a + esc(desc) + b],
    [/(<meta name="twitter:title" content=")[^"]*(")/, (m, a, b) => a + esc(pageTitle) + b],
    [/(<meta name="twitter:description" content=")[^"]*(")/, (m, a, b) => a + esc(desc) + b],
  ];
  if (url) {
    pairs.push([/(<link rel="canonical" href=")[^"]*(")/, (m, a, b) => a + esc(url) + b]);
    pairs.push([/(<meta property="og:url" content=")[^"]*(")/, (m, a, b) => a + esc(url) + b]);
  }
  let out = html;
  for (const [re, fn] of pairs) out = out.replace(re, fn as unknown as string);
  return out;
}

/** 取 `data/og.json`（只取一次）。取不到就当没有 —— 别让谱页因此挂掉。
 *
 * ⚠ 用**同一个 origin** 去取（`url.origin`），不要图省事写成 `https://assets.local/...` 那种假域名：
 *   2026-09-30 上线实测踩到 —— 假域名那次 `env.ASSETS.fetch` 拿不到东西，于是 `/s/<id>` 悄悄退回
 *   通用标题（异常被吞掉了，表面上"站点是好的"）。同源地址与主页那次取 `/index.html` 一样稳。
 */
async function ogIndex(env: Env, origin: string): Promise<OgIndex | null> {
  if (OG_CACHE && OG_CACHE !== 'pending') return OG_CACHE;
  if (OG_CACHE === 'pending') return null;
  OG_CACHE = 'pending';
  try {
    const r = await env.ASSETS.fetch(new URL('/data/og.json', origin).toString());
    if (!r.ok) { OG_ERR = 'HTTP ' + r.status; OG_CACHE = null; return null; }
    OG_CACHE = (await r.json()) as OgIndex;
    return OG_CACHE;
  } catch (e) {
    OG_ERR = String((e as Error)?.message ?? e);   // 诊断用: 从 /api/health 的 ogErr 读
    OG_CACHE = null;
    return null;
  }
}

/** `/s/<id>`: 拿静态的 index.html，按 id 把标题/描述换掉。 */
async function serveSongPage(request: Request, env: Env, url: URL): Promise<Response> {
  // 取首页那份 HTML。⚠ 用**字符串 URL** 发 GET（不要 `new Request(url, request)` 那种把
  // 原请求当 init 的写法 —— 会把方法/请求头一起带过去，POST 之类就变味了）。
  const res = await env.ASSETS.fetch(new URL('/index.html', url.origin).toString());
  try {
    const id = decodeURIComponent(url.pathname.replace(/^\/s\/?/, '').replace(/\/$/, ''));
    const idx = await ogIndex(env, url.origin);
    const meta = idx && id ? idx[id] : null;
    if (!meta) return res;
    const html = injectSongMeta(await res.text(), meta, { origin: url.origin, id });
    const h = new Headers(res.headers);
    h.delete('content-length');
    return new Response(html, { status: res.status, headers: h });
  } catch {
    return res;                          // 见纪律 ①
  }
}

/** 探一下**上游（本机写服务）活不活** —— 给 `/api/health` 用。
 *
 * 为什么需要它（2026-10-01 实测踩到）: 快速隧道会**悄悄死掉**（域名解析不到，Cloudflare 直接回
 * `Error 1016 Origin DNS error`）。而 `/api/health` 原来只报 `api: true`（那是"secret 配了没"），
 * 于是看护脚本一直以为写后端是好的 —— 实际上投稿已经全挂了几个小时。
 * **教训: 监控不能只查"配置在不在"，要查"上游活不活"。**
 * 3 秒超时；探不通不影响读路径，只把结论写进 health。
 */
async function probeUpstream(env: Env): Promise<{ ok: boolean; err: string }> {
  const up = (env.API_UPSTREAM || '').replace(/\/+$/, '');
  if (!up) return { ok: false, err: 'not-configured' };
  try {
    const headers: Record<string, string> = {};
    if (env.API_TOKEN) headers['X-Token'] = env.API_TOKEN;
    const r = await fetch(up + '/api/health', {
      method: 'GET', headers, signal: AbortSignal.timeout(3000),
    });
    return { ok: r.ok, err: r.ok ? '' : 'HTTP ' + r.status };
  } catch (e) {
    const err = e as Error;
    return { ok: false, err: err?.name === 'TimeoutError' ? 'timeout' : String(err?.name || err) };
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname;
    // 明文 http 一律 301 到 https（见 httpsRedirectUrl 的说明）
    const toHttps = httpsRedirectUrl(url);
    if (toHttps) return Response.redirect(toHttps, 301);
    if (path.startsWith(IMG_PREFIX)) {
      return serveImage(request, env, url);
    }
    if (path.startsWith('/api/')) {
      // 跨域调用(GitHub Pages 那个静态镜像把投稿指向这里)会先发 OPTIONS 预检:
      // `Content-Type: application/json` 属于非简单请求, 没有这一段浏览器直接就拦了 —— 连不上本机。
      if (request.method === 'OPTIONS') return preflight();
      if (path === '/api/health') {
        // `og` = 「每谱一页」那份分享卡索引（`data/og.json`）的条数。加它是因为 2026-09-30 上线时
        // 踩过一次"谱页悄悄退回通用标题"——异常被吞了，从外面看不出来；有这个数字一条 curl 就够。
        // `upstreamOk` = 真去敲一下本机服务（见 probeUpstream 的注释：只报 api:true 会骗人）。
        const og = await ogIndex(env, url.origin);
        const up = await probeUpstream(env);
        return json({ ok: true, deploy: 'cloudflare-worker',
                      images: env.IMAGES ? 'r2' : (env.IMG_UPSTREAM ? 'proxy' : 'none'),
                      api: !!env.API_UPSTREAM, upstream: env.API_UPSTREAM || null,
                      upstreamOk: up.ok, upstreamErr: up.err,
                      og: og ? Object.keys(og).length : 0, ogErr: OG_ERR });
      }
      return proxyApi(request, env, url);
    }
    // 「每谱一页」: 深链要给爬虫/分享平台看到**这一首**的标题（SPA 自己会渲染页面内容）
    if (path === '/s' || path.startsWith('/s/')) {
      return serveSongPage(request, env, url);
    }
    return env.ASSETS.fetch(request);
  },
} satisfies ExportedHandler<Env>;

/** 原图: 与 app/server.py 同样三道闸（逐段查 .. / 扩展名白名单 / 方法只认 GET-HEAD）。 */
async function serveImage(request: Request, env: Env, url: URL): Promise<Response> {
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    return new Response('method not allowed', { status: 405 });
  }
  // ⚠ key 有**两种写法**, 都得认:
  //   * 规范形式 = 解码后的原样 UTF-8 路径（`images-prep/…/怀念__jianpucn-100009/001.jpg`）
  //     —— S3 API / rclone / aws-cli 上传就是这个形式, 这是我们要的规范。
  //   * `wrangler r2 object put` 会把 key **百分号编码**后再存（实测: 存进去的 key 是
  //     `…/%E6%80%80%E5%BF%B5__…`）—— 用它传的文件只有原样路径能找到。
  //   所以先按解码后的找, 找不到再用 URL 原样路径找一遍（顺序固定, 结果可预期）。
  const raw = url.pathname.slice(IMG_PREFIX.length);
  const keys: string[] = [];
  try {
    const dec = decodeURIComponent(raw);
    keys.push(dec);
    if (dec !== raw) keys.push(raw);
  } catch {
    keys.push(raw);                              // 编码坏了: 至少按原样试一次, 不 500
  }
  const bad = (k: string): boolean => !k || k.includes('\\') ||
    k.split('/').some((p) => p === '' || p === '.' || p === '..');
  const ext = ((keys[0] ?? '').match(/\.[a-z0-9]+$/i) ?? [''])[0]!.toLowerCase();
  if (bad(keys[0]!) || !IMG_EXT.has(ext)) {
    return new Response('bad path', { status: 404 });
  }
  let obj: R2ObjectBody | null = null;
  if (env.IMAGES) {
    for (const k of keys) {
      obj = await env.IMAGES.get(k);
      if (obj) break;
    }
  }
  if (!obj) {
    // 没绑 R2 / 桶里没有 -> 兜底反代回本机（不需要 R2 也能看图）
    if (env.IMG_UPSTREAM) {
      return proxyFetch(request, env, env.IMG_UPSTREAM.replace(/\/+$/, '') + url.pathname + url.search);
    }
    return new Response(env.IMAGES ? 'not found'
                                   : 'R2 桶没绑定, 也没配 IMG_UPSTREAM(见 wrangler.jsonc)',
                        { status: env.IMAGES ? 404 : 503 });
  }
  const h = new Headers();
  obj.writeHttpMetadata(h);
  if (!h.get('content-type') || h.get('content-type') === 'application/octet-stream') {
    h.set('content-type', MIME[ext] ?? 'application/octet-stream');
  }
  h.set('etag', obj.httpEtag);
  h.set('cache-control', IMG_CACHE);
  return new Response(request.method === 'HEAD' ? null : obj.body, { headers: h });
}

/** /api/* → 本机服务。带上 X-Token（Worker secret）, 本机设了 JPSUBMIT_TOKEN 就只认它。 */
async function proxyApi(request: Request, env: Env, url: URL): Promise<Response> {
  const upstream = (env.API_UPSTREAM || '').replace(/\/+$/, '');
  if (!upstream) {
    return json({ ok: false, err: '这台部署没有配投稿后端: 投稿要在作者本机的服务上跑' +
                                  '（wrangler secret put API_UPSTREAM / API_TOKEN）' }, 503);
  }
  return proxyFetch(request, env, upstream + url.pathname + url.search);
}

/** 把请求原样转给本机服务（/api/* 与"R2 里没有的原图"共用这一条）。 */
async function proxyFetch(request: Request, env: Env, target: string): Promise<Response> {
  const headers = new Headers(request.headers);
  headers.delete('host');
  headers.delete('cf-connecting-ip');
  if (env.API_TOKEN) {
    headers.set('X-Token', env.API_TOKEN);
  }
  headers.set('X-Forwarded-Proto', 'https');
  const init: RequestInit = { method: request.method, headers, redirect: 'manual' };
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    init.body = request.body;
  }
  let res: Response;
  try {
    res = await fetch(target, init);
  } catch (e) {
    return json({ ok: false, err: '连不上本机后端（服务没开? 隧道断了?）: ' + (e as Error).message }, 502);
  }
  const out = new Headers(res.headers);
  out.set('Access-Control-Allow-Origin', '*');
  return new Response(res.body, { status: res.status, headers: out });
}

function json(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8',
               'cache-control': 'no-store', 'Access-Control-Allow-Origin': '*' },
  });
}

/** /api/* 的 CORS 预检应答(204, 不带 body)。只放开这一个前缀; 其余路径不受影响。 */
function preflight(): Response {
  return new Response(null, {
    status: 204,
    headers: {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
      'Access-Control-Max-Age': '86400',
    },
  });
}

// node tools/check_worker_routes.mjs
//
// **边缘 Worker 路由的回归测试**（2026-10-02 加）。
//
// 为什么需要它: 线上踩过一次"缺失假装成功" —— `wrangler.jsonc` 的
// `not_found_handling = "single-page-application"` 会把**任何**找不到的路径用
// `200 + index.html` 返回；于是"文件不在"表现为"读到了 HTML"。
// 我在 Worker 里加了规则（**SPA 兜底只对页面路由生效**），但 `check_live.mjs` 是在
// **本地静态服务**上跑的，走不到 SPA 兜底那条路 —— 也就是说这条规则**在 CI 里没有覆盖**。
// 这个测试把 Worker 直接 import 进来，用一个"永远退回 index.html"的假 ASSETS 模拟线上资源层，
// 于是能精确断言: 资源类路径必须 404、页面路由必须还能拿到 HTML。
import { strict as assert } from 'node:assert';
import { importRepoFile } from './_built.mjs';

const mod = await importRepoFile('worker/index.ts');
const worker = mod.default;

// 假的资源层: 模仿 workers-assets 的 single-page-application 行为 ——
// 已知文件正常返回，其他一律 200 + index.html（**这正是线上那个坑**）。
const FILES = new Map([
  ['/index.html', ['text/html; charset=utf-8', '<html><div id="home"></div><div id="tune"></div></html>']],
  ['/404.html', ['text/html; charset=utf-8', '<html>404</html>']],
  ['/static/app.abc12345.js', ['text/javascript', 'export const x = 1;']],
  ['/static/style.abc12345.css', ['text/css', 'body{}']],
  ['/data/songs.jsonl.gz', ['application/gzip', 'GZIPDATA']],
  ['/data/og.json', ['application/json', '{}']],
  ['/data/stats.json', ['application/json', '{"songs":11495}']],
  ['/robots.txt', ['text/plain', 'User-agent: *']],
  ['/sitemap.xml', ['application/xml', '<urlset/>']],
  ['/static/og.png', ['image/png', 'PNG']],
]);
const fakeAssets = {
  async fetch(req) {
    // ⚠ 可能收到**字符串**（worker 里是 `env.ASSETS.fetch(new URL(...).toString())`）也可能收到 Request
    //   —— 第一版只按 Request 取 `.url`，于是 `new URL(undefined)` 报 Invalid URL。
    const p = new URL(typeof req === "string" ? req : req.url).pathname;
    const hit = FILES.get(p);
    if (hit) return new Response(hit[1], { status: 200, headers: { 'content-type': hit[0] } });
    // SPA 兜底: 找不到就 200 + index.html（真实行为）
    const idx = FILES.get('/index.html');
    return new Response(idx[1], { status: 200, headers: { 'content-type': idx[0] } });
  },
};
const env = { ASSETS: fakeAssets };            // 不给 IMAGES / API_UPSTREAM: health 会报 none

let pass = 0, fail = 0;
const check = (cond, msg) => { if (cond) { pass++; console.log('  ✓ ' + msg); } else { fail++; console.log('  ✗ ' + msg); } };

async function get(path) {
  const r = await worker.fetch(new Request('https://jianpu-db.org' + path), env);
  const ct = r.headers.get('content-type') || '';
  const body = await r.text();
  return { status: r.status, ct, body };
}

console.log('① 页面路由: SPA 兜底仍然要生效（HTML 正常返回）');
for (const p of ['/', '/index.html', '/404.html']) {
  const r = await get(p);
  check(r.status === 200 && r.ct.includes('text/html'), `${p} -> ${r.status} ${r.ct}`);
}

console.log('\n② 资源类路径: 缺失就**必须** 404，不能返回 HTML');
const must404 = [
  '/data/images.jsonl.gz',          // 前端早已不下发（就是它把线上自检搞崩的）
  '/data/__nope__.json.gz',
  '/data/nope.json',
  '/static/nope.js',
  '/static/nope.css',
  '/img/../jianpu-db/score.py',     // URL 规范化后是 /jianpu-db/score.py（前缀判断看不见）
  '/img/%2e%2e/jianpu-db/score.py',
  '/img/images/../../etc/passwd',   // 规范化后是 /etc/passwd（**没有扩展名**，扩展名规则也漏）
  '/etc/passwd',
  '/score.py',
  '/favicon.ico',
];
for (const p of must404) {
  const r = await get(p);
  check(r.status === 404 && !r.ct.includes('text/html'),
        `${p} -> ${r.status} ${r.ct}${r.status === 404 ? '' : '（**不该是 200/HTML**）'}`);
}

console.log('\n③ 存在的资源: 必须原样返回（别把资源也 404 掉）');
for (const [p, [ct]] of FILES) {
  if (p === '/index.html' || p === '/404.html') continue;
  const r = await get(p);
  check(r.status === 200 && r.ct.startsWith(ct.split(';')[0]),
        `${p} -> ${r.status} ${r.ct}`);
}

console.log('\n④ 深链 /s/<id>: 走注入分支（不是 404，也不是裸 index.html）');
{
  const r = await get('/s/jianpucn-150657');
  check(r.status === 200 && r.ct.includes('text/html'), `/s/jianpucn-150657 -> ${r.status} ${r.ct}`);
}

// ⑤ `/api/gh`（GitHub Star 数，服务端缓存）
//    为什么必须测这条: 它**不经过本机后端**（代理之前处理），而且要用**替换全局 fetch** 的方式
//    才能在不打真 GitHub 的前提下断言"取到了哪个数、失败时怎么报"。
//
//    ⚠ 顺序有讲究: **先测失败，再测成功**。因为设计上"失败不写内存缓存"，
//      所以失败用例不会污染后面的缓存命中用例；反过来（先成功后失败）就会命中缓存、测不到失败路径。
//      （Node 的模块缓存让"重新 import 拿一个新实例"这招无效 —— 我第一版就踩了这个。）
console.log('\n⑤ /api/gh（Star 数走服务端 + 缓存；不碰本机后端）');
{
  const realFetch = globalThis.fetch;
  const calls = [];
  const stub = (impl) => { globalThis.fetch = async (u, opt) => { calls.push(String(u)); return impl(u, opt); }; };

  // (a) 403 限流 -> stars=null，err 如实暴露，客户端只缓存 5 分钟
  stub(async () => new Response('rate limited', { status: 403 }));
  let r = await worker.fetch(new Request('https://jianpu-db.org/api/gh'), env);
  let d = await r.json();
  check(r.status === 200 && d.stars === null && d.ok === false && /403/.test(d.err),
        `403 限流被如实报告 -> stars=${d.stars} err=${JSON.stringify(d.err)}`);
  check((r.headers.get('cache-control') || '').includes('max-age=300'),
        `失败只让客户端缓存 5 分钟 -> ${r.headers.get('cache-control')}`);

  // (b) 网络异常 -> 不能抛出去
  stub(async () => { throw new Error('boom'); });
  r = await worker.fetch(new Request('https://jianpu-db.org/api/gh'), env);
  d = await r.json();
  check(r.status === 200 && d.stars === null && /boom/.test(d.err),
        `网络异常被吞成 err -> err=${JSON.stringify(d.err)}`);

  // (c) 成功 -> 42，而且请求的是**正确的仓库**
  calls.length = 0;
  stub(async () => new Response(JSON.stringify({ stargazers_count: 42 }), { status: 200, headers: { 'content-type': 'application/json' } }));
  r = await worker.fetch(new Request('https://jianpu-db.org/api/gh'), env);
  d = await r.json();
  check(r.status === 200 && d.stars === 42 && d.ok === true && d.repo === 'jianpu-db/jianpu-db.github.io',
        `/api/gh 成功 -> ${r.status} stars=${d.stars} repo=${d.repo}`);
  check(calls.length === 1 && calls[0] === 'https://api.github.com/repos/jianpu-db/jianpu-db.github.io',
        `请求的是对的仓库: ${calls[0]}`);
  check((r.headers.get('cache-control') || '').includes('max-age=3600'),
        `成功时客户端缓存 1 小时 -> ${r.headers.get('cache-control')}`);

  // (d) 命中内存缓存 -> 不该再打 GitHub（别名 /api/stars 也认）
  calls.length = 0;
  r = await worker.fetch(new Request('https://jianpu-db.org/api/stars'), env);
  d = await r.json();
  check(d.stars === 42 && d.cached === 'mem' && calls.length === 0,
        `/api/stars 命中内存缓存（没再打 GitHub，cached=${d.cached}）`);

  globalThis.fetch = realFetch;   // 还原，别把后面/别的检查搞坏
}

// ⑥ `/ws/2/*`（MusicBrainz 风格只读接口，2026-10-05 加）在边缘的三件事:
//    ① 路由到**和 /api/* 同一个上游**（API_UPSTREAM）而不是掉进 SPA 兜底或 404；
//    ② 成功响应带短缓存（`max-age=60`）；
//    ③ **带 Retry-After 的 503 绝不能被缓存**（否则一次限流会被边缘记住，60 秒内所有调用方一起挨 503）。
//    同样靠替换全局 fetch 来断言"转给了谁、回来的头被怎么改"，不打真隧道。
console.log('\n⑥ /ws/2/*：反代到 API_UPSTREAM + 缓存纪律');
{
  const realFetch = globalThis.fetch;
  const envUp = { ASSETS: fakeAssets, API_UPSTREAM: 'https://up.example', API_TOKEN: 'tok-123' };
  let seen = [];
  const stub = (impl) => {
    globalThis.fetch = async (u, opt) => { seen.push({ url: String(u), headers: opt?.headers }); return impl(u, opt); };
  };
  const hdr = (h, k) => (h && (h.get ? h.get(k) : h[k])) || '';

  // (a) 成功 -> 200 + 短缓存，并且确实转到了同一个上游（路径与查询串原样保留）
  seen = [];
  stub(async () => new Response('{"created":"x","count":1,"offset":0,"songs":[]}',
    { status: 200, headers: { 'content-type': 'application/json' } }));
  let r = await worker.fetch(new Request('https://jianpu-db.org/ws/2/song?query=316316&limit=5'), envUp);
  await r.text();
  check(r.status === 200 && /max-age=60/.test(r.headers.get('cache-control') || ''),
        `/ws/2/song 成功带上短缓存 -> ${r.headers.get('cache-control')}`);
  check(seen.length === 1 && seen[0].url === 'https://up.example/ws/2/song?query=316316&limit=5',
        `反代到同一个 API_UPSTREAM -> ${seen[0] && seen[0].url}`);
  check(hdr(seen[0] && seen[0].headers, 'X-Token') === 'tok-123',
        `带上 X-Token（本机写后端要它）`);

  // (b) `/ws/2/`（API 根）也走这条，不是 404、也不是 index.html
  seen = [];
  stub(async () => new Response('{"name":"jianpu-db Web Service"}',
    { status: 200, headers: { 'content-type': 'application/json' } }));
  r = await worker.fetch(new Request('https://jianpu-db.org/ws/2/'), envUp);
  const bodyB = await r.text();
  check(r.status === 200 && bodyB.includes('Web Service') && seen[0].url === 'https://up.example/ws/2/',
        `/ws/2/ 也是反代（不是 SPA 兜底）-> ${r.status} ${seen[0] && seen[0].url}`);

  // (c) 限流的 503（带 Retry-After）-> no-store，绝不缓存
  seen = [];
  stub(async () => new Response('{"error":"请求太快"}',
    { status: 503, headers: { 'content-type': 'application/json', 'retry-after': '1' } }));
  r = await worker.fetch(new Request('https://jianpu-db.org/ws/2/song?query=1'), envUp);
  const bodyC = await r.text();
  check(r.status === 503 && /no-store/.test(r.headers.get('cache-control') || '') &&
        r.headers.get('retry-after') === '1' && bodyC.includes('请求太快'),
        `503+Retry-After 是 no-store（不会被边缘记住）-> ${r.headers.get('cache-control')}`);

  // (d) 404 之类也不给成功那套缓存
  seen = [];
  stub(async () => new Response('{"error":"没有这一首"}',
    { status: 404, headers: { 'content-type': 'application/json' } }));
  r = await worker.fetch(new Request('https://jianpu-db.org/ws/2/song/nope'), envUp);
  await r.text();
  check(r.status === 404 && !/max-age=60/.test(r.headers.get('cache-control') || ''),
        `404 不被当成可缓存的成功 -> ${r.status} ${r.headers.get('cache-control') || '(无)'}`);

  // (e) OPTIONS 预检：204，不要在没连上游时就挂
  seen = [];
  const r5 = await worker.fetch(new Request('https://jianpu-db.org/ws/2/song', { method: 'OPTIONS' }), envUp);
  check(r5.status === 204 && (r5.headers.get('access-control-allow-origin') === '*') && seen.length === 0,
        `/ws/2/* 的 OPTIONS 预检 -> ${r5.status}（没打上游）`);

  // (f) 没配 API_UPSTREAM 时给出人话 503，而不是 404/HTML
  seen = [];
  const r6 = await worker.fetch(new Request('https://jianpu-db.org/ws/2/'), env);
  const d6 = await r6.json();
  check(r6.status === 503 && d6.ok === false && /后端/.test(String(d6.err)),
        `没配上游时 503 + 人话 -> ${r6.status} ${JSON.stringify(d6).slice(0, 60)}`);

  globalThis.fetch = realFetch;
}

console.log(`\n${fail === 0 ? '通过' : '失败 ' + fail + ' 项'}（共 ${pass + fail} 项）`);
process.exit(fail ? 1 : 0);
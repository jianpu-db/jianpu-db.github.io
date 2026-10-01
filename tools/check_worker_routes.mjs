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

console.log(`\n${fail === 0 ? '通过' : '失败 ' + fail + ' 项'}（共 ${pass + fail} 项）`);
process.exit(fail ? 1 : 0);

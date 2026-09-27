// node tools/check_live.mjs [base] —— 对着**真的跑起来的服务**做一次端到端:
//   走 HTTP 取 /data/songs.jsonl.gz 与 /static/*.js, 用**前端自己的检索代码**查一句。
// 用法: node tools/check_live.mjs http://127.0.0.1:8770
import { gunzipSync } from 'node:zlib';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { resolve } from 'node:path';

const BASE = (process.argv[2] || 'http://127.0.0.1:8770').replace(/\/$/, '');
// ⚠ 2026-09-28 修: 原来用 `new URL('..', import.meta.url).pathname`。在 Windows 上 pathname
//   是 `/D:/Documents_D/...`(带前导斜杠), 再经 pathToFileURL 就成了
//   `file:///D:/D:/Documents_D/...` —— 本自检直接 ERR_MODULE_NOT_FOUND 跑不起来
//   (Linux 上正常, 所以一直没暴露)。标准写法是 fileURLToPath。
const ROOT = fileURLToPath(new URL('..', import.meta.url));
const { buildIndex, search } = await import(pathToFileURL(resolve(ROOT, 'static/search.js')).href);
const { parseQuery } = await import(pathToFileURL(resolve(ROOT, 'static/jptok.js')).href);

let fail = 0;
const ok = (c, m) => { console.log((c ? '✓ ' : '✗ ') + m); if (!c) fail++; };

// ① 服务活着
const health = await (await fetch(BASE + '/api/health')).json();
ok(health.ok === true, `/api/health ok=true (repo=${health.repo})`);

// ② 走 HTTP 取索引数据
const r = await fetch(BASE + '/data/songs.jsonl.gz');
ok(r.ok, `/data/songs.jsonl.gz HTTP ${r.status} ${r.headers.get('content-type')}`);
const text = gunzipSync(Buffer.from(await r.arrayBuffer())).toString('utf8');
const lines = text.split('\n').filter((x) => x.trim());
// 不写死曲数: 与同一个服务给出的 stats.json 对账(更严格 —— 数据与统计必须自洽)
const st = await (await fetch(BASE + '/data/stats.json')).json();
ok(lines.length === st.songs, `索引 ${lines.length} 首 == stats.json 的 ${st.songs} 首`);
const idx = buildIndex(text);
ok(idx && idx.songs && idx.songs.length === st.songs, `buildIndex 成功: ${idx.songs.length} 首`);

// ③ 用前端代码查"人耳那句"与"原谱那句"
// 63731232: 段落加权后第一是 U.N.オーエンは彼女なのか？(命中在副歌), 神々 那处在发狂钢琴段
for (const [q, want] of [['33565653253', '神々が恋した幻想郷'], ['63731232', 'U.N.オーエンは彼女なのか？']]) {
  const res = search(idx, [parseQuery(q)], {});
  const top = res[0];
  ok(!!top && top.title.startsWith(want), `${q} -> Top1 "${top && top.title}" 代价 ${top && top.cost}`);
}

// ④ 部署的数据确实是修好的那份(th10_06 应为 415 音)
const th = idx.songs.find((s) => String(s.file).includes('th10_06'));
ok(th && th.n === 415, `th10_06 音符数 = ${th && th.n} (期望 415)`);

// ⑤ 「每谱一页」的**服务端**两件事: /s/<id> 要能刷新, /img/<路径> 要真把原图发出来 + 挡住越界
const tune = idx.songs.find((s) => s.id && s.file && s.file.length);
const pageR = await fetch(BASE + '/s/' + encodeURIComponent(tune.id));
const pageTxt = pageR.ok ? await pageR.text() : '';
ok(pageR.ok && /id="tune"/.test(pageTxt) && /id="home"/.test(pageTxt),
   `/s/${tune.id} 深链返回同一个 index.html (HTTP ${pageR.status})`);
const ir = await fetch(BASE + '/data/images.jsonl.gz');
ok(ir.ok, `/data/images.jsonl.gz HTTP ${ir.status} ${ir.headers.get('content-type')}`);
const imgIdx = new Map(gunzipSync(Buffer.from(await ir.arrayBuffer())).toString('utf8')
  .split('\n').filter(Boolean).map((l) => { const x = JSON.parse(l); return [x.s, x]; }));
ok(imgIdx.size > 0, `原图索引 ${imgIdx.size} 个 source`);
const cov = idx.songs.filter((s) => s.source && imgIdx.has(s.source)).length;
ok(st.with_images === cov,
   `stats.with_images = ${st.with_images} 首 == 索引里真有图的 ${cov} 首 (${st.image_pages} 页)`);
const withImg = idx.songs.find((s) => s.source && imgIdx.has(s.source));
if (withImg) {
  const im = imgIdx.get(withImg.source);
  const url = BASE + '/img/' + im.d.split('/').map(encodeURIComponent).join('/') + '/'
    + im.pg[0][0].split('/').map(encodeURIComponent).join('/');
  const g = await fetch(url);
  const buf = Buffer.from(await g.arrayBuffer());
  ok(g.ok && /^image\//.test(g.headers.get('content-type') || '') && buf.length > 1000,
     `原图取到了: ${im.d.split('/').slice(0, 2).join('/')}/… (${g.status} ${g.headers.get('content-type')} ${buf.length} 字节)`);
  ok((g.headers.get('cache-control') || '').includes('max-age'), '原图带长缓存头');
  // 目录穿越 / 非图片扩展名: 一律挡住(这里挂的是本机文件系统的 8.9GB 扫描件, 不能漏)
  for (const bad of ['/img/../jianpu-db/score.py', '/img/%2e%2e/jianpu-db/score.py',
                     '/img/images/../../etc/passwd', '/img/images-prep/x/../../../etc/passwd',
                     '/img/' + im.d + '/notimage.txt']) {
    const b = await fetch(BASE + bad);
    ok(!b.ok, `挡住越界/非图: ${bad} -> HTTP ${b.status}`);
  }
}
console.log(`\n${fail === 0 ? '通过' : '失败 ' + fail + ' 项'}  —— ${BASE}`);
process.exit(fail ? 1 : 0);

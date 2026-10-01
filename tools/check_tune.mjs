// node tools/check_tune.mjs —— 「每谱一页」(`/s/<id>`) 的自检。
//
// 用户口径(2026-09-24):
//   * "每张谱都有一个单独的页面" —— 深链 `/s/<id>` 能直接打开、刷新、分享;
//   * **不要"原图"那一栏**(既不转存扫描件也不外链图片; 原站页面地址在「出处」和「收录页」里);
//   * **"原谱原文"要一字不差**(verbatim): 用曲谱文件正文原样, 不要 data.jsonl 里那份
//     "展开过"的 token 流(节头 subtitle=/拍号/KeepLength 被去掉、省略时值被补全),
//     也不要往里面注入自动恢复的小节线。
//     ⚠ 原文是**网页构建时**直接从 `jianpu-db/scores/<file>.txt` 读的, 只进前端索引
//       (`jianpu-web/data/songs.jsonl.gz`) —— data.jsonl / parse_scores.py 一概不动。
//
// 这个脚本真跑一遍 app.js 的路由: 把 location 摆成深链, 让 app.js 自己渲, 再检查 HTML。
import { readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { importStatic } from './_built.mjs';

const songsBuf = readFileSync(new URL('../data/songs.jsonl.gz', import.meta.url));
const rows = gunzipSync(songsBuf).toString('utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l));

// 样本: 一首**多节/带 KeepLength**的(最能证明"拿的是文件原文而不是展开版"), 一首没有原站页面的
const WITH = rows.find((r) => r.src && /KeepLength|subtitle=/.test(r.src) && r.srcurl)
  || rows.find((r) => r.src && r.srcurl);
const NOSRC = rows.find((r) => !r.srcurl);
const BADID = '__no_such_tune__';
if (!WITH || !NOSRC) { console.error('!! 语料里找不到自检用的样本'); process.exit(1); }

// ---- 假 DOM ----
const els = {}, handlers = {};
function mkEl(id) {
  return els[id] || (els[id] = {
    id, _html: '', textContent: '', className: '', value: '', hidden: false, disabled: false,
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = v; },
    addEventListener(ev, fn) { (handlers[id] = handlers[id] || {})[ev] = fn; },
    focus() {}, getAttribute() { return ''; }, closest() { return null; },
    querySelector() { return mkEl(id + '-q'); }, querySelectorAll() { return []; },
  });
}
global.document = {
  getElementById: mkEl, getElementsByClassName: () => [], querySelectorAll: () => [],
  addEventListener(ev, fn) { (handlers['document'] = handlers['document'] || {})[ev] = fn; },
};
global.window = global;
global.addEventListener = (ev, fn) => { (handlers['window'] = handlers['window'] || {})[ev] = fn; };
global.location = { protocol: 'http:', host: '127.0.0.1:8770',
                    pathname: '/s/' + WITH.id, hash: '', search: '' };
global.history = { pushState() {} };
global.scrollTo = () => {};
global.performance = { now: () => Date.now() };
global.fetch = async (u) => {
  const s = String(u);
  if (s.endsWith('songs.jsonl.gz')) return { ok: true, body: new Response(songsBuf).body, json: async () => ({}) };
  if (s.endsWith('stats.json')) {
    return { ok: true, json: async () => JSON.parse(readFileSync(new URL('../data/stats.json', import.meta.url), 'utf8')) };
  }
  return { ok: false, status: 404, json: async () => ({}) };   // 图索引现在**不该**被请求
};
global.DecompressionStream = (await import('node:stream/web')).DecompressionStream;
const errors = [];
process.on('unhandledRejection', (e) => errors.push('未处理的 Promise 拒绝: ' + (e && e.message)));
process.on('uncaughtException', (e) => errors.push('未捕获异常: ' + (e && e.message)));

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
await importStatic('app');
await sleep(1600);

let fail = 0;
const ok = (c, m) => { console.log((c ? '✓ ' : '✗ ') + m); if (!c) fail++; };
const pop = () => { handlers['window'] && handlers['window'].popstate && handlers['window'].popstate(); };

// ---- ① 深链 /s/<id> ----
const html = mkEl('tune').innerHTML;
console.log(`样本: ${WITH.id} 「${WITH.g}」 · src ${(WITH.src || '').length} 字 · 原站页 ${WITH.srcurl || '(无)'}`);
ok(mkEl('home').hidden === true && mkEl('tune').hidden === false, '深链进来时首页藏起、谱页显示');
ok(html.includes(WITH.g || WITH.t), '标题渲出来了');
ok(/class="tune-h1"/.test(html) && /class="crumb"/.test(html), '有标题与"回检索"面包屑');
  // ⚠ 2026-10-01 修（TS 迁移时发现的**陈旧断言**）: 原来断言字面量 `<th>歌手</th>`，
  //   可是 schema 化之后表头是 `<th><span title="…schema 的 note…">歌手</span></th>`，
  //   于是这条**永远不可能通过**（一直红着被当成"已知失败"）。现在先把 `<th>` 里的标签剥掉
  //   再比文字 —— 既容得下包装，又真的在检查"歌手/状态这两栏在不在"。
  const thLabels = (h) => (h.match(/<th>[\s\S]*?<\/th>/g) || [])
    .map((x) => x.replace(/<[^>]+>/g, '').trim());
  ok(/class="meta"/.test(html) && thLabels(html).includes('歌手') && thLabels(html).includes('状态'),
     '元数据表完整(歌手/状态都在)');
ok(html.includes(WITH.id), '页面上写明了这一页的 id');
ok(WITH.srcurl ? html.includes(WITH.srcurl) : true, '「出处」那一行链到原站页面');

// **没有"原图"那一栏**（用户口径）: 整页不许有 <img>, 也不该有"原谱图"标题
ok(!/<img\b/.test(html), '整页没有 <img>（不转存扫描件、不外链图片）');
ok(!/原谱图/.test(html), '没有"原谱图"那一栏');

// **原谱原文 = 文件 verbatim**
const m = html.match(/<pre class="sheet">([\s\S]*?)<\/pre>/);
ok(!!m, '有 <pre class="sheet"> 原文块');
if (m) {
  const got = m[1];
  ok(got === esc(WITH.src), '原文块内容 === 曲谱文件正文(一字不差, 含换行/节头/KeepLength)');
  ok(!/<span class="bar">/.test(got), '原文块里**没有**注入自动恢复的小节线');
  ok(!/<mark>/.test(got), '原文块里没有标黑段');
  if (/KeepLength|subtitle=/.test(WITH.src)) {
    ok(/KeepLength|subtitle=/.test(got), '节头写法(KeepLength / subtitle=)原样保留 —— 不是展开版');
  }
  // 记录里的 raw 是**展开过**的那份(节头去掉/省略时值补全); 两者不同就证明没拿 raw 冒充 src
  if (WITH.raw && WITH.raw !== WITH.src) {
    ok(got !== esc(WITH.raw), '显示的**不是** data.jsonl 里那份展开过的 token 流');
  }
}
ok(/class="exact"/.test(html) || /class="exact pending"/.test(html), '收录页片子还在');
ok(/class="al-go-tags"/.test(html), '谱页上能补标签');
ok(!/class="tune-link"/.test(html), '谱页上不再出现"本谱一页"(自己指自己)');

// ---- ② 没有原站页面的那首 ----
location.pathname = '/s/' + NOSRC.id;
pop();
await sleep(400);
const h2 = mkEl('tune').innerHTML;
console.log(`没有原站页的样本: ${NOSRC.id} 「${NOSRC.g}」`);
ok(h2.includes(NOSRC.g) && /class="meta"/.test(h2), '没有原站页面时照样渲出元数据/原文');
ok(!/<img\b/.test(h2), '照样没有 <img>');
ok(/<pre class="sheet">/.test(h2), '照样有 verbatim 原文');

// ---- ③ 不存在的 id ----
location.pathname = '/s/' + BADID;
pop();
await sleep(300);
ok(/没有这一页/.test(mkEl('tune').innerHTML), '不存在的 id 有明确说明(而不是白屏)');

// ---- ④ hash 形式(纯静态托管没有 SPA 回退时) ----
location.pathname = '/';
location.hash = '#/s/' + WITH.id;
pop();
await sleep(400);
ok((mkEl('tune').innerHTML || '').includes(WITH.g), '#/s/<id> 这种形式也能打开');

// ---- ⑤ 回首页 ----
location.hash = '';
location.pathname = '/';
pop();
await sleep(200);
ok(mkEl('home').hidden === false && mkEl('tune').hidden === true, '回首页后首页显示、谱页藏起');
ok(document.title.includes('简谱旋律查歌'), '标题回到站点名');

ok(!errors.length, '全程没有运行时错误' + (errors.length ? ' -> ' + errors.join(' | ') : ''));
console.log(fail === 0 ? '\n每谱一页 自检 通过' : `\n每谱一页 自检 失败 ${fail} 项`);
process.exitCode = fail ? 1 : 0;

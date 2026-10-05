// node tools/check_page.mjs [查询] —— 用假 DOM 执行 app.js, 看有没有运行时错误 +
//   结果卡是否真的渲染出元数据 + **命中段高亮/小节线画得对不对**。
// 默认查询 63731232; 传 `33565653253` 可复现"th10_06 开头休止被误标黑"那个 bug 的回归测试。
import { readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { importStatic } from './_built.mjs';
import { waitFor } from './_wait.mjs';
// ⚠ 2026-10-01 修（CI 上 `web · node 22` 的"高亮 + 收录页"红）:
//   这个脚本原来把 **argv[2] 当查询串**，而同一批的其它检查（check_ui/check_tune/check_live）
//   argv[2] 是**服务 URL**。于是 CI 里按家族约定传了 URL -> 脚本拿
//   `http://127.0.0.1:8801` 去当旋律查询 -> "标黑的音 = 查询"必然失败（打印出来是 67766 vs 12711，
//   那串数字其实就是 URL 里的 127/8801）。本机怎么都复现不出，因为本机我有时不带参数跑。
//   现在统一约定: argv[2] = URL（本脚本其实用不到，收下即可），argv[3] = 可选的查询串覆盖。
const URL_ARG = process.argv[2] && /^https?:\/\//.test(process.argv[2]) ? process.argv[2] : '';
const QUERY = process.argv[3] || (URL_ARG ? '' : process.argv[2]) || '63731232';

// ---- 假 DOM ----
const handlers = {};
const els = {};
function mkEl(id) {
  return els[id] || (els[id] = {
    id, _html: '', textContent: '', className: '', value: '', disabled: false, checked: true,
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = v; },
    addEventListener() {}, focus() {}, getAttribute() { return ''; },
  });
}
global.document = {
  getElementById: mkEl,
  getElementsByClassName: () => [],
  querySelectorAll: () => [],
  // app.js 用全局委托接「＋ 补收录页」的保存按钮(结果区有两个: #out / #tout)
  addEventListener(ev, fn) { (handlers['document'] = handlers['document'] || {})[ev] = fn; },
};
global.window = global;
global.location = { protocol: 'http:', host: '127.0.0.1:8770' };
global.performance = { now: () => Date.now() };
global.fetch = async (u) => {
  if (u.endsWith('songs.jsonl.gz')) {
    const buf = readFileSync(new URL('../data/songs.jsonl.gz', import.meta.url));
    return { ok: true, body: new Response(buf).body, json: async () => ({}) };
  }
  if (u.endsWith('stats.json')) {
    return { ok: true, json: async () => JSON.parse(readFileSync(new URL('../data/stats.json', import.meta.url), 'utf8')) };
  }
  return { ok: false, status: 404, json: async () => ({}) };
};
global.DecompressionStream = (await import('node:stream/web')).DecompressionStream;

process.on('unhandledRejection', (e) => { console.error('!! 未处理的 Promise 拒绝:', e && e.message); process.exitCode = 1; });
process.on('uncaughtException', (e) => { console.error('!! 未捕获异常:', e && e.message); process.exitCode = 1; });

// ── 固定 UI 语言（2026-10-01 修）────────────────────────────────────────────────
// app.ts 用 `navigator.language` 决定界面语言（标签"歌手/状态"还是 "Artist/Status"），
// 而**每台机器的语言不同**: 本机是 ja-JP（回落中文，所以本地一直绿），CI runner 是 en-US
// （渲染成英文）-> 断言里写的中文标签就挂了。**测试不能依赖运行环境的语言**，这里钉死 zh-CN。
// （排查过程: 公开 API 读不到 CI 日志 -> 给工作流加了"失败时把关键行做成 annotation"，
//   一眼就看到了 `✗ 元数据表完整(歌手/状态都在)`。）
try {
  Object.defineProperty(globalThis, 'navigator', {
    value: { language: 'zh-CN', languages: ['zh-CN'] }, configurable: true, writable: true,
  });
} catch {
  try { globalThis.navigator = { language: 'zh-CN', languages: ['zh-CN'] }; } catch { /* 忽略 */ }
}

await importStatic('app');
// 等 **app 把索引建好**（信号: 它建完索引会调 fillTagList() 把标签灌进 #taglist 的 innerHTML），
// 而不是睡一个固定 1200 ms —— 固定睡眠在 CI 上会随机红（见 tools/_wait.mjs 顶部说明）。
{
  const ms = await waitFor(() => (mkEl('taglist').innerHTML || '').length > 0, 20000);
  console.log(ms < 0
    ? '  ! 等了 20 秒 #taglist 还是空的（app 可能没建完索引）'
    : `  （索引在 ${ms} ms 内就绪）`);
}
console.log('status 文本:', (mkEl('status').textContent || '(空)').slice(0, 80));

// 触发一次查询(直接调 run 不可达, 改为手动走一遍同一路径)
const { buildIndex, search } = await importStatic('search');
const { parseQuery, parseTokenAll, isPitch } = await importStatic('jptok');
const app = await importStatic('app');
const idx = buildIndex(gunzipSync(readFileSync(new URL('../data/songs.jsonl.gz', import.meta.url))).toString('utf8'));
const q = parseQuery(QUERY);
const res = search(idx, [q], { top: 2 });
console.log('查询:', QUERY, '-> 检索结果数:', res.length);
const r = res[0];
console.log('第一首:', r.group, '| 代价', r.cost, '| 命中位置(音符下标)', r.at);
console.log('元数据字段是否齐:',
  ['file', 'tags', 'usertags', 'alias', 'transcriber', 'mbid', 'bars', 'bpb']
    .map((k) => k + '=' + (r[k] === undefined ? '缺!' : 'ok')).join(' '));
console.log('bars 数:', (r.bars || []).length, ' bpb:', r.bpb, ' 文件:', r.file);

// ---- 高亮/小节线自检: 标黑的必须是**有音高**的音符, 且逐个对上查询 ----
const html = app.renderScore(r.raw, r.at, q.length, r.bars);
const m = html.match(/<mark>([\s\S]*?)<\/mark>/);
let fail = 0;
const ok = (c, msg) => { console.log((c ? '✓ ' : '✗ ') + msg); if (!c) fail++; };
ok(!!m, '渲染出了 <mark> 命中段');
if (m) {
  const marked = m[1].replace(/<[^>]+>/g, ' ').split(/\s+/).filter(Boolean);
  const pitches = marked.filter(isPitch);
  ok(pitches.length === q.length, `标黑 token ${marked.length} 个, 其中有音高 ${pitches.length} 个 (期望 ${q.length})`);
  const bad = marked.filter((t) => parseTokenAll(t).length > 0 && !isPitch(t));
  ok(bad.length === 0, `标黑段里没有休止/念白 (发现: ${bad.join(' ') || '无'})`);
  // ⚠ 2026-10-05: 用 parseTokenAll **逐音**核对, 不能写 `parseToken(t).d` —— 和弦 token 一个顶
  //   好几个音(parseToken 返回数组, 没有 .d)。而且和弦展开后"标黑的新音数"可能多于查询音数
  //   (查询的最后一个音正好落在和弦里), 所以只核**前 q.length 个新音**: 逐 token 往下取新音,
  //   取够就停, 再看这一段是不是正好等于查询。
  const got = [];
  for (const t of marked) {
    for (const p of parseTokenAll(t)) {
      if (p.d === '0' || p.d === 'x') continue;
      got.push(p.d);
      if (got.length >= q.length) break;
    }
    if (got.length >= q.length) break;
  }
  const gotStr = got.slice(0, q.length).join('');
  const want = q.map((x) => x.d).join('');
  ok(gotStr === want, `标黑的音 = 查询  (${gotStr} vs ${want})`);
  console.log('  标黑段:', pitches.join(' '));
  if (got.length > q.length) {
    console.log(`  注: 标黑段里还有 ${got.length - q.length} 个新音(查询末尾落在和弦 token 里)`);
  }
}
// 第一条小节线落在第几个音符之前 —— 必须与数据一致
const firstBarTok = (r.raw || '').split(' ');
let ni = -1, tokOfNote = {};
for (let i = 0; i < firstBarTok.length; i++) { if (isPitch(firstBarTok[i])) tokOfNote[++ni] = i; }
const b0 = (r.bars || [])[0];
console.log(`  数据第一条小节线: 在第 ${b0} 个音符之前 -> 显示时画在 raw[${tokOfNote[b0]}] = ${firstBarTok[tokOfNote[b0]]} 之前`);

// ---- 收录页自检(用户口径: 要"具体收录的那一页", 不要搜索页冒充) ----
const with_src = idx.songs.find((s) => s.srcurl && (s.links || []).length === 0);
const no_src = idx.songs.find((s) => !s.srcurl && !(s.links || []).length);
function fake(s, extra) {
  return Object.assign({ title: s.title, group: s.group, file: s.file, mbid: '',
                         srcurl: s.srcurl, links: s.links || [], source: s.source }, extra || {});
}
if (with_src) {
  const h = app.exactLinks(fake(with_src), 'melody');
  ok(h.includes('class="exact"') && h.includes(with_src.srcurl),
     `有原谱站确切页时渲染出精确链接 (${with_src.srcurl})`);
  // 用户口径(2026-09-24): 没收录的平台要写成**与已收录同形状的灰色片子** + 圆形 ＋, 而不是一串文字
  ok(h.includes('class="exact pending"') && /网易云音乐/.test(h),
     '没收录的平台渲染成同形状的黄色片子(网易云音乐…)');
  const pendLinks = [...h.matchAll(/<a class="exact pending" href="([^"]+)"/g)].map((m) => m[1]);
  ok(pendLinks.length >= 3 && pendLinks.every((u) => /search|results/i.test(u)),
     `黄片是**链接**, 且都指向该平台的搜索页(${pendLinks.length} 个: ${pendLinks.slice(0, 2).join(' , ')})`);
  ok(h.includes('class="plus"') && h.includes('data-ph='),
     '灰片后面有圆形 ＋ 按钮(带该平台的占位提示)');
  ok(h.includes('class="alrow"') && h.includes('class="al-go"'),
     '卡片里有就地粘网址的输入行(保存后自动补充)');
  ok(!h.includes('待补充：') && !/去找这一页/.test(h),
     '不再写成"待补充：A/B/C"文字、也不再有"去找这一页"搜索行');
}
if (no_src) {
  const h = app.exactLinks(fake(no_src), 'melody');
  ok(h.includes('class="exact pending"'), `一条确切页都没有时, 四个平台全是灰色片子 (${no_src.title})`);
}
{
  // 平台表在 schema.py(5 个: 网易云/QQ/B站/YouTube/**MusicBrainz**) -> "全部补齐"要连 MB 一起填
  const all = fake(no_src || with_src, { srcurl: 'http://www.jianpu.cn/pu/15/150657.htm', links: [
    'https://music.163.com/song?id=186016', 'https://y.qq.com/n/ryqq/songDetail/0039MnYb0qxYhV',
    'https://www.bilibili.com/video/BV1xx411c7mD', 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
    'https://musicbrainz.org/work/9c1f4a5e-0000-4000-8000-000000000000'] });
  const h = app.exactLinks(all, 'melody');
  ok(h.includes('网易云音乐') && h.includes('YouTube') && h.includes('song?id=186016'),
     '人工补的 links 会渲染成精确链接(站名由 host 认出来)');
  ok(h.includes('MusicBrainz') && !/MusicBrainz<\/a>/.test(h),
     'MusicBrainz 也按 host 认领(不再出黄色片子)');
  ok(!h.includes('class="exact pending"'), '四个平台都补齐后不再有黄色片子');
}
console.log(fail === 0 ? '\n高亮+收录页 自检 通过' : `\n高亮+收录页 自检 失败 ${fail} 项`);
if (fail) process.exitCode = 1;

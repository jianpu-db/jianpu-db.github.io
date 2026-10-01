import { buildIndex, search, ensureGrams } from './search.js';
import { parseQuery, parseToken, isPitch, show } from './jptok.js';
import type { SearchResult, Index } from './search.js';
/* 简谱旋律查歌 —— 主界面（路由 / 卡片 / 谱页 / 就地表单）

 * ⚠ **TypeScript 迁移状态（2026-10-01, B 阶段）**:
 *   文件已改成 `.ts`，构建链统一（`esbuild` 只做类型擦除）；**数据层是真类型**：
 *     * `exactLinks(r: SearchResult, ctx?: string)` / `renderScore(...)` 的签名；
 *     * `FIELDS`（schema 来的属性表）、`PLATFORMS`（收录页平台表）、`TXT`（文案表）。
 *   **DOM 胶水层仍是宽松模式**（`tsconfig.app.json`: `strict: false`）—— 下一步收紧要修的是三类：
 *     ① 事件处理器 / `$()` 取到的元素可能为 null（`?.` 或先断言）；
 *     ② `FIELD_VALUE` 里每个渲染函数的参数要写成 `SearchResult`；
 *     ③ 各处 `setTimeout`/`fetch` 回调的参数类型。
 *   为什么不一口气上 strict: 867 行 DOM 代码逐条加类型噪声大、回归风险高，而收益主要在数据层 ——
 *   宁可**分两步走且写在文件头**，也不假装已经 strict。
 */

/* ── 类型（数据层；DOM 胶水层留给后面的收紧）───────────────────────────────── */

/** schema 里一条属性（`jianpu-db/schema.py` 的 FIELDS，经 stats.json 带过来） */
interface Field {
  label: Record<string, string>;
  kind: string;
  editable?: boolean;
  hint?: string;
  note?: string;
  row?: boolean;
}
/** 收录页平台表一行: [站名, 认领正则, 粘确切页的提示, 搜索页模板] */
type Platform = [string, RegExp, string, string];
/** 卡片上的提示文案 */
type TxtEntry = Record<string, string>;

/* 数据侧: 只需要"曲名 + 出处" 就能给出可点的外链 —— 不依赖任何 API/key */
var REPO = 'Francium-223/jianpu-db';

/* **应用根**: 从本模块自己的地址推出来(`<根>/static/app.js` 的上一级)。
 * 为什么非这样不可: 「每谱一页」的地址是 `/s/<id>`, 在**这个地址上打开/刷新**时, 相对路径
 * `./data/x` 会被浏览器解析成 `/s/data/x`(404) —— 深链直接白屏。用 import.meta.url 推出根目录后,
 * 不管部署在域名根、子目录, 还是 `/s/<id>` 这种深链, 数据与跳转都落在同一个根上。 */
var ROOT_URL = new URL('../', import.meta.url);
var APP_PATH = ROOT_URL.pathname.replace(/\/+$/, '/');        // '/' 或 '/子目录/'
function appUrl(rel) { return new URL(rel, ROOT_URL).toString(); }
function appPath(rel) { return new URL(rel, ROOT_URL).pathname; }
function tunePath(id) { return appPath('s/' + encodeURIComponent(id)); }



/* 站点自己在构建时注入的全局（见 tools/build_dist.mjs: `JIANPU_API` / `JIANPU_READONLY`）。
 * 声明一下，省得每处 `window.X` 都报 TS2339。 */
declare global {
  interface Window {
    JIANPU_API?: string;
    JIANPU_READONLY?: boolean;
  }
}

/** 从事件目标往上找最近的祖先元素。
 *
 * 为什么要有它: `EventTarget` 上没有 `closest`，而事件委托全靠它 —— 原来每处都写成
 * `ev.target && ev.target.closest ? ev.target.closest(sel) : null`，TS 下每处都报错。
 * 收成一处断言，调用点也更短（老浏览器没有 closest 的情况仍然兜住）。
 */
function closestFrom<T extends Element = Element>(t: EventTarget | null, sel: string): T | null {
  const el = t as Element | null;
  return el && typeof el.closest === 'function' ? (el.closest(sel) as T | null) : null;
}

function $<T extends HTMLElement = HTMLElement>(id: string): T {
  // 调用方按需要给泛型：`$<HTMLInputElement>('q')`。默认 HTMLElement（够用于 textContent/className）。
  return document.getElementById(id) as T;
}
var IDX: Index | null = null;

function loadCorpus() {
  if (typeof DecompressionStream === 'undefined') {
    return fetch(appUrl('data/songs.jsonl')).then(function (r) { return r.text(); });
  }
  return fetch(appUrl('data/songs.jsonl.gz')).then(function (r) {
    // 两种服务端行为都得认:
    //   ① 替我们解好并带 `Content-Encoding: gzip`(某些静态托管会这么干) -> 直接读文本;
    //   ② 原样发 gzip 字节(Cloudflare Pages 就是这样) -> 自己解。
    // 头/方法都不是必然存在(自检里的假 fetch 就只有 `{ok, body}`), 所以每处先问一句再说 ——
    // 2026-09-25 就是把 `r.headers.get` 当成必然存在, 初始化当场炸, 被 check_render/page/ui/tune 抓到。
    if (r.headers && r.headers.get && /gzip/i.test(r.headers.get('content-encoding') || '')) return r.text();
    // 用 clone 是为了"解压失败还能回头读原文"(有托管把 .gz 当纯文本发); 没有 clone 就用原 body。
    var body = (typeof r.clone === 'function') ? r.clone().body : r.body;
    return new Response(body.pipeThrough(new DecompressionStream('gzip'))).text()
      .catch(function () { return (typeof r.text === 'function') ? r.text() : ''; });
  });
}


function esc(s) {
  return String(s).replace(/[&<>"]/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
  });
}

function sourceUrl(src) {
  var host = String(src || '').split('-')[0];
  var m = { qupu123: 'https://www.qupu123.com/', jianpucn: 'http://www.jianpu.cn/',
            jianpujia: 'https://www.jianpujia.com/' };
  return m[host] || '';
}

/* 站点标签: 从 URL 的 host 认(不靠人的命名习惯) */
/** [认领正则, 站名] —— 从 URL 的 host 认站，不靠人的命名习惯 */
type SiteLabel = [RegExp, string];
var SITE_LABELS: SiteLabel[] = [
  [/music\.163\.com/, '网易云音乐'],
  [/y\.qq\.com/, 'QQ音乐'],
  [/bilibili\.com/, 'B站'],
  [/youtube\.com|youtu\.be/, 'YouTube'],
  [/musicbrainz\.org/, 'MusicBrainz'],
  [/jianpu\.cn/, '歌谱简谱网'],
  [/jianpujia\.com/, '简谱之家'],
  [/qupu123\.com/, '中国曲谱网'],
  [/qinyipu\.com/, '琴艺谱'],
];
function siteLabel(u) {
  for (var i = 0; i < SITE_LABELS.length; i++) if (SITE_LABELS[i][0].test(u)) return SITE_LABELS[i][1];
  try { return new URL(u).hostname.replace(/^www\./, ''); } catch (e) { return '链接'; }
}

/* **收录页**: 这首歌在那一站的**具体页面**。
 *   来源 = 人工补的 `link=`(可多个) + 原谱站核对过的具体页(srcurl) + MBID 对应的 MusicBrainz 录音页。
 * 用户口径(2026-09-24 定版):
 *   * 已收录 -> 绿色片子(站名 ↗), 点开就是**那一页**(不是搜索页);
 *   * 未收录 -> **形状一样的灰色片子**, 后面跟一个**圆形 ＋ 按钮**;
 *   * 点 ＋ 就地粘网址, 保存后**自动补上**(片子变绿), 不用刷新页面。
 *   搜索行("去哪里搜")**已按用户要求去掉** —— 搜索页不进语料, 也不该占版面。
 */
// **单一真源在 `jianpu-db/schema.py` 的 PLATFORMS**(搜索页格式只写一处), 经 data/stats.json 带过来。
// 下面这张只是"stats 还没加载 / 独立部署 web"时的兜底, 字段顺序: [名, 认领正则, 粘确切页的提示, 搜索页模板]
var PLATFORMS: Platform[] = [
  ['网易云音乐', /music\.163\.com/, 'https://music.163.com/song?id=…', 'https://music.163.com/#/search/m/?s={q}&type=1'],
  ['QQ音乐', /y\.qq\.com/, 'https://y.qq.com/n/ryqq/songDetail/…', 'https://y.qq.com/n/ryqq/search?w={q}'],
  ['B站', /bilibili\.com/, 'https://www.bilibili.com/video/…', 'https://search.bilibili.com/all?keyword={q}'],
  ['YouTube', /youtube\.com|youtu\.be/, 'https://www.youtube.com/watch?v=…', 'https://www.youtube.com/results?search_query={q}'],
  ['MusicBrainz', /musicbrainz\.org/, 'https://musicbrainz.org/work/…', 'https://musicbrainz.org/search?query={q}&type=work'],
];
function loadPlatforms(st) {
  if (!st || !st.platforms || !st.platforms.length) return;
  try {
    PLATFORMS = st.platforms.map(function (p) {
      return [p.name, new RegExp(p.host || '.', 'i'), p.exact || 'https://…', p.search || ''] as Platform;
    });
  } catch (e) { /* 保持内建兜底 */ }
}
var ALROW_N = 0;                       // 每张卡一个就地输入框, 用 id 串起来(不靠 DOM 遍历)

/* 卡片"一行一个属性"的展示规格 —— **单一真源是 `jianpu-db/schema.py` 的 FIELDS**, 经 data/stats.json 带过来。
 * 用户口径(2026-09-29):
 *   ① 每个属性的显示名**独立于属性本身**（不在这儿硬编码中文）—— 一律从 `label[<语言>]` 取,
 *      取不到回落 zh, 再取不到才用第一个有的语言。这样加语言只要改 schema.py 一处。
 *   ② 哪些**能改**、哪些**不能改**也由 schema 说了算(`editable` + `note`), 前端只照做:
 *      能改的行尾画一颗 ＋, 不能改的把 note 挂在 title 上(鼠标停一下能看到为什么)。
 * 下面这份兜底只在"stats 还没加载 / 独立部署 web"时用, 故意只留最小信息。
 */
var FIELDS: Record<string, Field> = {
  file: { label: { zh: '文件', en: 'File' }, kind: 'readonly' },
  group: { label: { zh: '曲名', en: 'Title' }, kind: 'readonly' },
  artist: { label: { zh: '歌手', en: 'Artist' }, kind: 'list', editable: true, hint: '邓丽君（多个用逗号）' },
  status: { label: { zh: '状态', en: 'Status' }, kind: 'readonly' },
  n: { label: { zh: '音符', en: 'Notes' }, kind: 'readonly' },
  bars: { label: { zh: '小节', en: 'Bars' }, kind: 'readonly' },
  source: { label: { zh: '出处', en: 'Source' }, kind: 'readonly' },
  transcriber: { label: { zh: '转写', en: 'Transcriber' }, kind: 'readonly' },
  tags: { label: { zh: '标签', en: 'Tags' }, kind: 'readonly' },
  usertags: { label: { zh: '人标', en: 'Human tags' }, kind: 'list', editable: true, hint: '分类/儿歌, 民歌' },
  alias: { label: { zh: '别名', en: 'Alias' }, kind: 'list', editable: true, hint: '另一个曲名' },
  mbid: { label: { zh: 'MBID', en: 'MBID' }, kind: 'text', editable: true, hint: 'MusicBrainz work 的 UUID' },
};
function loadFields(st) {
  if (!st || !st.fields) return;
  var out = {};
  Object.keys(st.fields).forEach(function (k) {
    var f = st.fields[k] || {};
    out[k] = { label: f.label || {}, kind: f.kind || 'readonly', editable: !!f.editable,
               hint: f.hint || '', note: f.note || '', row: f.row !== false };
  });
  if (Object.keys(out).length) FIELDS = out;
}
// 当前界面语言(**只认浏览器语言的前两位**); schema 里的 label 是按语言的字典, 所以这里够用
var LANG = (function () {
  var l = (typeof navigator !== 'undefined' && navigator.language) || 'zh';
  return String(l).slice(0, 2).toLowerCase();
})();
function fieldLabel(f) {
  var lb = (f && f.label) || {}, keys = Object.keys(lb);
  return lb[LANG] || lb.zh || (keys.length ? lb[keys[0]] : '');
}

/* **"这首歌已经有了哪些确切页"只有这一处口径** —— 绿色片子(exactLinks)和"还缺哪个平台"
 * 的判断都用它。2026-09-25 的 bug 就是这里漏了 MBID 那条: 绿色 MusicBrainz 片子已经画出来,
 * 平台循环却以为 MusicBrainz 还缺, 又补了一颗黄色"待补"片子(用户: "我的 U.N.Owen 已经有
 * MusicBrainz 了, 你怎么还后面加个黄的链接?")。
 */
function exactSources(r) {
  var out = [], seen = {};
  function push(u, kind) {
    if (!u || seen[u]) return;
    seen[u] = 1;
    out.push([siteLabel(u), u, kind]);
  }
  if (r.mbid) push('https://musicbrainz.org/work/' + encodeURIComponent(r.mbid), 'MBID');
  if (r.srcurl) push(r.srcurl, '原谱站（已核对）');
  (r.links || []).forEach(function (u) { push(u, '收录页'); });
  return out;
}

function collectedUrls(r) {
  return exactSources(r).map(function (p) { return p[1]; });
}

export function exactLinks(r: SearchResult, ctx?: string): string {
  var out = exactSources(r);          // 与 collectedUrls 同一份口径
  var urls = collectedUrls(r);
  var f = (r.file && r.file[0]) || '';
  var rid = 'alrow' + (++ALROW_N);
  var html = out.map(function (p) {
    return '<a class="exact" href="' + p[1] + '" target="_blank" rel="noopener" title="' + esc(p[2]) +
      '">' + esc(p[0]) + ' ↗</a>';
  }).join('');

  // 缺的平台: **黄色片子(与已收录同形状)**, 点进去是**该平台的搜索页**(帮人去找);
  // 旁边那颗圆形 ＋ 是"把确切页粘进来"(存下后片子变绿)。搜索页格式来自 schema.py。
  var qq = encodeURIComponent(r.title || r.group || '');
  PLATFORMS.forEach(function (p) {
    if (urls.some(function (u) { return p[1].test(u); })) return;
    var href = (p[3] || '').replace('{q}', qq);
    html += '<a class="exact pending" href="' + href + '" target="_blank" rel="noopener"' +
      ' title="还没收录 —— 点开去 ' + esc(p[0]) + ' 搜这首歌">' + esc(p[0]) + '</a>' +
      (f ? '<button type="button" class="plus" data-row="' + rid + '" data-ph="' + esc(p[2]) +
           '" data-plat="' + esc(p[0]) + '" title="补 ' + esc(p[0]) + ' 的确切页面">＋</button>' : '');
  });
  if (f) {
    html += '<button type="button" class="plus" data-row="' + rid + '" data-ph="https://…"' +
      ' data-plat="其它站" title="补其它站的确切页面">＋</button>' +
      '<span class="alrow" id="' + rid + '" hidden>' +
        '<input class="al-url" placeholder="https://…" spellcheck="false" />' +
        '<button class="al-go" data-file="' + esc(f) + '" data-re="' + esc(ctx || '') + '">保存</button>' +
        '<span class="al-msg"></span></span>';
  }
  return html;
}

/* 「＋ 补标签」: 人工给某一首加标签 —— 与补收录页同一套路(服务端写进曲谱 + 重建索引)。
 * 分类用「分类/儿歌」这种既有约定; 输入框挂了 <datalist id="taglist"> 提示语料里已有的词表。 */
/* 「＋ 补标签」: 人工给某一首加标签 —— 与「＋ 补收录页」同一套路(服务端写进曲谱 + 重建索引)。
 * 分类用「分类/儿歌」这种既有约定; 输入框挂了 <datalist id="taglist"> 提示语料里已有的词表。 */
function addTagForm(r) {
  var f = (r.file && r.file[0]) || '';
  if (!f) return '';
  return '<details class="addlink"><summary>＋ 补标签</summary>' +
    '<p class="hint">多个用逗号；分类写「分类/儿歌」，歌手直接写名字。</p>' +
    '<input class="al-url at-tags" list="taglist" placeholder="分类/儿歌, 邓丽君" spellcheck="false" />' +
    '<button class="al-go-tags" data-file="' + esc(f) + '">保存</button>' +
    '<span class="al-msg"></span></details>';
}

/* 把语料里已有的标签灌进 <datalist id="taglist">, 让补标签时口径一致 */
function fillTagList() {
  var dl = $('taglist');
  if (!dl || !IDX) return;
  var set = {};
  for (var i = 0; i < IDX.songs.length; i++) {
    (IDX.songs[i].tags || []).forEach(function (t) { set[t] = 1; });
    (IDX.songs[i].usertags || []).forEach(function (t) { set[t] = 1; });
  }
  dl.innerHTML = Object.keys(set).sort().map(function (t) {
    return '<option value="' + esc(t) + '"></option>';
  }).join('');
}

/* 「＋ 库里没有这首」: 预填一个 GitHub Issue 表单, 点一下就能提(用户自己确认后提交) */
function issueUrl(text) {
  return 'https://github.com/' + REPO + '/issues/new?title=' +
    encodeURIComponent('[缺谱] ' + text) +
    '&body=' + encodeURIComponent(
      '想加的曲子:' + text + '\n\n' +
      '(可选) 原谱链接或图片:\n\n' +
      '(可选) 这段旋律的简谱数字:\n\n' +
      '---\n由简谱旋律查歌前端自动填写\n');
}

/* 把原谱原文渲染成 HTML。
 *   at/qlen 给了 -> 把"命中段"标黑(检索结果卡用);
 *   at 传 null   -> **只画小节线**, 不标黑(每谱一页用: 那是整首谱, 没有"命中段")。
 *
 * **必须用 isPitch 判"第几个音符"**: 索引里的 `at`/`bars`/`n` 数的是"第几个**有音高**的音符",
 * 而 raw 里混着休止/念白(`c0`/`q0`/`x`)、时值前缀、小节线 `|`、延长 `-`。
 * 这里踩过两次坑:
 *   ① 早先抄了一条**窄**正则, 把 `q3` 判成"不是音符" -> 高亮跑到别处(用户实测: 搜 623532 却标出 `q5 s5 q,5. …`);
 *   ② 后来改用 parseToken, 但它对 `c0`(休止)也返回非空 -> **休止被当成音符**,
 *      高亮与小节线整体前移(用户实测: th10_06 开头 `c0 q0` 被误标黑, 第一条 `|` 画到 `q3 q3` 之后)。
 * 口径只能有一份: jptok.isPitch。
 */
export /* 卡片上的**提示文案**(按语言)。与 schema 里的属性名一样, 文案不许散落在各处:
 * 这里集中一份, 按 LANG 取, 取不到回落 zh。 */
var TXT: Record<string, TxtEntry> = {
  tied: { zh: '并列：另有 {n} 首同分', en: '{n} more song(s) tie at this cost' },
  tiedTip: { zh: '这句不是唯一命中 —— 后面并列的那几首要一起看, 别把第一条当铁证',
             en: 'not a unique match — check the tied results too' },
  onlyOne: { zh: '独证：本曲仅此一版', en: 'single version of this song' },
  onlyOneTip: { zh: '这首歌在库里只有这一个版本, 而且是**机器转写**（没有第二个版本可交叉核对）',
                en: 'only one version in the corpus, and it is machine-transcribed' },
};
function t(key: string, vars?: Record<string, unknown>): string {
  var e = TXT[key] || {};
  var s = e[LANG] || e.zh || '';
  return String(s).replace(/\{(\w+)\}/g, function (m, k) {
    return (vars && vars[k] != null) ? String(vars[k]) : m;
  });
}

/* 「这条命中到底有多硬」的提示片子(2026-09-29 用户口径: 别让人把转写噪声当铁证)。
 * 依据来自 search.js 给的两个字段: groupsAtBest(同代价并列几首) / versions(本曲几个版本)。 */
function cautionChips(r) {
  var out = '';
  if (r.cost === 0 && (r.groupsAtBest || 1) > 1) {
    var n = r.groupsAtBest - 1;
    out += '<span class="warn" title="' + esc(t('tiedTip')) + '">' + esc(t('tied', { n: n })) + '</span>';
  }
  if ((r.versions || 0) === 1 && r.status === 'ocr') {
    out += '<span class="warn" title="' + esc(t('onlyOneTip')) + '">' + esc(t('onlyOne')) + '</span>';
  }
  return out;
}

// 导出给检查脚本用（`tools/check_page.mjs` 断言"片段渲染 + 高亮 + 小节线"）。
// ⚠ 2026-10-01 发现: 这个函数一直**没有** export，而 check_page 里写的是 `app.renderScore(...)`
//   —— 于是那条自检从写下的那天起就没真正跑过（一直抛 `app.renderScore is not a function`，
//   被脚本的 try/catch 包成"未捕获异常"打在最后）。做 TS 迁移时因为要改 import 方式才发现。
export function renderScore(raw: string, at: number | null, qlen: number, bars: number[]): string {
  if (!raw) return '';
  var toks = raw.split(' ');
  var barSet = {};
  (bars || []).forEach(function (b) { barSet[b] = 1; });
  var mark = (at !== null && at !== undefined && at >= 0);
  var startTok = -1, endTok = -1;
  if (mark) {
    var noteIdx = -1;
    for (var i = 0; i < toks.length; i++) {
      if (isPitch(toks[i])) {
        noteIdx++;
        if (noteIdx === at) startTok = i;
        if (noteIdx === at + qlen - 1) endTok = i;
      }
    }
    if (startTok < 0) return esc(raw);
    if (endTok < 0) endTok = toks.length - 1;
  }
  var ni = -1, html = [];
  for (var j = 0; j < toks.length; j++) {
    // 小节线画在"它之前的那条"位置: bars 里记的是音符下标
    var isNote = isPitch(toks[j]);
    if (isNote) ni++;
    if (isNote && barSet[ni] && !(mark && j === startTok)) html.push('<span class="bar">|</span> ');
    if (mark && j === startTok) html.push('<mark>');
    if (mark && j === endTok + 1) html.push('</mark>');
    html.push(esc(toks[j]));
    if (j < toks.length - 1) html.push(' ');
  }
  if (mark && endTok + 1 >= toks.length) html.push('</mark>');
  return html.join('');
}

function run(e?: { preventDefault: () => void }) {
  if (e) e.preventDefault();
  if (!IDX) return;
  var segs = [];
  var parts = $<HTMLInputElement>('q').value.split(/[;；|、+，,]+/);
  for (var i = 0; i < parts.length; i++) {
    var s = parseQuery(parts[i]);
    if (s.length >= 5) segs.push(s);
  }
  if (!segs.length) {
    $('status').className = 'status err';
    $('status').textContent = '至少 5 个音（1–7，可带 # 或 b）。';
    return;
  }
  $('status').className = 'status';
  $('status').textContent = '查询中…';
  $<HTMLButtonElement>('go').disabled = true;
  setTimeout(function () {
    var t0 = performance.now();
    var res = search(IDX, segs, { top: 10 });
    render(segs, res, Math.round(performance.now() - t0));
    $<HTMLButtonElement>('go').disabled = false;
  }, 20);
}

/* 元数据全摊开: 一行一项, 空的显示 — —— 用户要求"不光标题, 别的元数据也都一并摊开" */
/* 站点首页: 把 `qupu123-268596` 这类 source 映射回该站首页(源码站可点) */
function siteUrl(src) {
  var host = String(src || '').split('-')[0];
  var m = { qupu123: 'https://www.qupu123.com/', jianpucn: 'http://www.jianpu.cn/',
            jianpujia: 'https://www.jianpujia.com/' };
  return m[host] || '';
}

/* 每个属性的**值**怎么渲染(卡片表格里的那一列)。键与 FIELDS 同; 没列到的属性走 default 分支。
 * 显示**名**不在这儿 —— 那是 schema 的事(见上面 FIELDS)。 */
var FIELD_VALUE = {
  file: function (r, h) { return r.file && r.file.length ? h.esc(h.list(r.file)) : '—'; },
  group: function (r, h) { return h.esc(r.group); },
  artist: function (r, h) { return r.artist && r.artist.length ? h.esc(h.list(r.artist)) : '—'; },
  status: function (r, h) {
    return h.esc(r.status || '?') + (r.status === 'ok' ? '（人工校对过）'
      : r.status === 'ocr' ? '（图片机器转写）' : '');
  },
  n: function (r) { return r.n + ' 个'; },
  bars: function (r) { return (r.bars || []).length + ' 小节 · ' + (r.bpb || 4) + ' 拍/小节'; },
  source: function (r, h) {
    var src = r.source || '';
    if (!src) return '—';
    var u = r.srcurl || sourceUrl(src);        // 优先链到原谱站**那一页**, 没有再退回站点首页
    return u ? '<a href="' + u + '" target="_blank" rel="noopener">' + h.esc(src) + '</a>' : h.esc(src);
  },
  transcriber: function (r, h) { return r.transcriber && r.transcriber.length ? h.esc(h.list(r.transcriber)) : '—'; },
  // 转写置信度(0~1): 老谱没这个字段时 build_web_data 给 0.5(中性), 这里区分"真的 0.5"与"没有"
  confidence: function (r, h) {
    if (r.conf == null) return '—';
    var pct = Math.round(r.conf * 100);
    var tag = r.conf >= 0.95 ? '高' : (r.conf >= 0.85 ? '中' : '低');
    return h.esc(pct + '%（' + tag + '）');
  },
  // 「最低那 10% 的分位」: 平均看着还行、个别音很虚时靠它发现(0.95 均值 + 0.42 p10 = 有虚音)
  conf_p10: function (r, h) {
    if (r.confP10 == null) return '—';
    return h.esc(Math.round(r.confP10 * 100) + '%');
  },
  tags: function (r, h) { return r.tags && r.tags.length ? h.esc(h.list(r.tags)) : '—'; },
  usertags: function (r, h) { return r.usertags && r.usertags.length ? h.esc(h.list(r.usertags)) : '—'; },
  alias: function (r, h) { return r.alias && r.alias.length ? h.esc(h.list(r.alias)) : '—'; },
  mbid: function (r, h) {
    return r.mbid ? '<a href="https://musicbrainz.org/work/' + encodeURIComponent(r.mbid) +
      '" target="_blank" rel="noopener"><code>' + h.esc(r.mbid) + '</code></a>' : '—';
  },
};

/* 能改的属性: 行尾一颗 ＋(与收录页那颗同形状), 点开就地输入 + 保存。
 * 保存走 `POST /api/submit {kind:'attr', file, attr, value}` —— 服务端按 **同一份 schema** 再验一次
 * (前端说能改不算数, 只读属性在服务端必须被拒)。 */
function attrPlus(r, key) {
  var f = FIELDS[key], file = (r.file && r.file[0]) || '';
  if (!f || !f.editable || !file) return '';
  var rid = 'attr' + (++ALROW_N);
  return '<button type="button" class="plus attr-plus" data-row="' + rid + '" data-ph="' + esc(f.hint || '') +
    '" data-file="' + esc(file) + '" data-attr="' + esc(key) + '" title="补 ' + esc(fieldLabel(f)) + '">＋</button>' +
    '<span class="alrow" id="' + rid + '" hidden>' +
      '<input class="al-url attr-val" placeholder="' + esc(f.hint || '') + '" spellcheck="false" />' +
      '<button class="al-go-attr" data-file="' + esc(file) + '" data-attr="' + esc(key) + '">保存</button>' +
      '<span class="al-msg"></span></span>';
}

function metaRows(r) {
  function list(x) { return (x || []).join('、'); }
  var h = { esc: esc, list: list };
  var rows = [];
  Object.keys(FIELDS).forEach(function (key) {
    var f = FIELDS[key];
    if (f.row === false) return;                       // 如"收录页": 它在卡片顶部是片子, 不做表格行
    var render = FIELD_VALUE[key];
    var val = render ? render(r, h) : '—';
    var name = fieldLabel(f);
    // 显示名那一列挂 title: 能改的说明怎么改, 不能改的说明为什么不能改(都来自 schema 的 note)
    var note = f.note ? ' title="' + esc(f.note) + '"' : '';
    rows.push(['<span' + note + '>' + esc(name) + '</span>', val + attrPlus(r, key)]);
  });
  return '<table class="meta"><tbody>' +
    rows.map(function (x) { return '<tr><th>' + x[0] + '</th><td>' + x[1] + '</td></tr>'; }).join('') +
    '</tbody></table>';
}

/* ================= 「每谱一页」 `/s/<id>` =================
 * 用户口径(2026-09-24): "每张谱都有一个单独的页面, 显示它的原图, 像 abcnotation 那样"。
 * 之前只有检索结果卡: 原图那张扫描件根本没地方看, 一首谱也没有能分享/回看/刷新的地址。
 * 现在每一首都有自己的页面 —— 左边是**原图**(多页就一页一页堆下去), 右边是全部元数据 +
 * 收录页 + 补标签 + 原文(带小节线)。
 *
 * 路由: `/s/<id>`。服务端把同一个 index.html 发出来(app/server.py), 由这里按 id 渲。
 *      也认 `#/s/<id>` —— 纯静态托管(没有 SPA 回退)时用这个形式照样能打开。
 * id 是 build_web_data.py 里 tune_id() 定的(首选 source), 前端只查表, 不重算。
 */
var CURRENT_TUNE: string = '';

function tuneIdFromLocation() {
  var p = location.pathname || '';
  if (APP_PATH !== '/' && p.indexOf(APP_PATH) === 0) p = p.slice(APP_PATH.length - 1);
  var m = /^\/?s\/(.+)$/.exec(p);
  if (!m) m = /^#\/?s\/(.+)$/.exec(location.hash || '');
  if (!m) return '';
  try { return decodeURIComponent(m[1]); } catch (e) { return m[1]; }   // 坏编码别把整页带崩
}

function setMode(tune) {
  var h = $('home'), t = $('tune');
  if (h) h.hidden = !!tune;
  if (t) t.hidden = !tune;
  // 谱页要**看图**: 把整页放宽(首页那种窄栏放不下一张扫描件)
  if (document.body && document.body.classList) document.body.classList.toggle('tune-mode', !!tune);
}

function showHome(q) {
  setMode(false);
  CURRENT_TUNE = '';
  document.title = 'jianpu-db | 通过简谱旋律查歌';
  if (q) {                                   // ?q=… -> 直接替用户查一次(谱页上的"用开头几个音检索"用它)
    $<HTMLInputElement>('q').value = q;
    run({ preventDefault: function () {} });
  } else if ($<HTMLInputElement>('q')) {
    $<HTMLInputElement>('q').focus();
  }
}

function showTune(id) {
  setMode(true);
  CURRENT_TUNE = id;
  var t = $('tune');
  if (!t) return;
  var row = (IDX && IDX.byId) ? IDX.byId.get(id) : null;
  if (!row) {
    t.innerHTML = '<p class="crumb"><a class="tune" href="' + esc(APP_PATH) + '">← 回检索</a></p>' +
      '<h1 class="tune-h1">没有这一页</h1>' +
      '<p class="hint">地址里的编号 <code>' + esc(id) + '</code> 不在语料里' +
      '（id 就是 source，如 <code>jianpucn-150657</code>）。</p>';
    document.title = '没有这一页 — jianpu-db';
    return;
  }
  t.innerHTML = tuneHtml(row);
  document.title = (row.group || row.title || id) + ' — jianpu-db';
}

/* 每谱一页。**没有"原图"那一栏**（用户口径 2026-09-24: 不转存扫描件、也不外链图片）——
 * 原站那一页的地址本来就在「出处」和「收录页」里, 信息不丢, 版面还干净。
 * 页面就是: 标题/副行 -> 全部元数据 + 收录页 + 补标签 -> 带小节线的原文。
 */
function tuneHtml(r) {
  var list = function (x) { return (x || []).join('、'); };
  var sub = [
    r.artist && r.artist.length ? '歌手 ' + esc(list(r.artist)) : '',
    r.n + ' 音符',
    (r.bars || []).length + ' 小节 · ' + (r.bpb || 4) + ' 拍/小节',
    esc(r.status || '?') + (r.status === 'ok' ? '（人工校对过）' : r.status === 'ocr' ? '（图片机器转写）' : ''),
    r.source ? '出处 ' + esc(r.source) : '',
  ].filter(Boolean).join(' · ');
  var motif = (r.p && r.p.length >= 8)
    ? '<p class="hint"><a class="tune" href="' + esc(appPath('') + '?q=' + r.p.slice(0, 8)) + '"' +
      ' title="把这 8 个音送进旋律检索(查重、找同曲异名)">用开头的 ' + r.p.slice(0, 8) + ' 去检索</a></p>'
    : '';
  return '<p class="crumb"><a class="tune" href="' + esc(APP_PATH) + '">← 回检索</a>' +
      '<span class="dim">' + esc(r.id || '') + '</span></p>' +
    '<h1 class="tune-h1">' + esc(r.group || r.title || '(无题)') + '</h1>' +
    '<p class="tune-sub">' + sub + '</p>' +
    '<h2>元数据</h2>' + metaRows(r) +
    '<div class="links"><span class="lab">收录页</span> ' + exactLinks(r, 'tune') + addTagForm(r) +
      '<a class="add" href="' + issueUrl(r.group) + '" target="_blank" rel="noopener"' +
      ' title="这首有问题 / 想补充资料 → 一键提 issue">＋ 反馈/补充</a></div>' +
    motif +
    '<h2>原谱原文</h2>' +
    // **verbatim**: 曲谱文件正文原样(含节头/KeepLength/换行), 既不展开也不注入小节线 ——
    // 用户口径 2026-09-24("用用户写的文件一字不差")。老数据没有 src 时退回 raw。
    ((r.src || r.raw)
      ? '<pre class="sheet">' + esc(r.src || r.raw) + '</pre>' +
        (r.src ? '' : '<p class="hint">（旧索引：这里是展开过的原文。）</p>')
      : '<p class="hint">没有原文。</p>') +
    '<footer><a class="tune" href="' + esc(APP_PATH) + '">← 回检索页</a></footer>';
}

/* 应用内跳转: 改地址栏 + 重渲, 不整页刷新(搜索框里的东西和已加载的语料都留着) */
function navigate(href) {
  if (typeof history !== 'undefined' && history.pushState) history.pushState(null, '', href);
  route();
  if (typeof window !== 'undefined' && window.scrollTo) window.scrollTo(0, 0);
}

function route() {
  var id = tuneIdFromLocation();
  if (id) return showTune(id);
  var q = '';
  try { q = new URLSearchParams(location.search || '').get('q') || ''; } catch (e) { q = ''; }
  showHome(q);
}

/* 保存成功后**就地改本地索引**: 服务端重建索引要 ~2 分钟, 但用户刚补的那条链接现在就该变绿。
 * 检索结果与索引里那一首共用同一个数组对象(见 search.js), 所以 push 进去再重渲就立即生效。 */
function findSongByFile(f) {
  if (!IDX || !f) return null;
  for (var i = 0; i < IDX.songs.length; i++) {
    if (((IDX.songs[i].file || [])[0]) === f) return IDX.songs[i];
  }
  return null;
}

function addLocalLinks(file, urls) {
  var s = findSongByFile(file);
  if (!s) return;
  if (!s.links) s.links = [];
  (Array.isArray(urls) ? urls : [urls]).forEach(function (u) {
    if (u && s.links.indexOf(u) < 0) s.links.push(u);
  });
}

function addLocalTags(file, tags) {
  var s = findSongByFile(file);
  if (!s) return;
  ['tags', 'usertags'].forEach(function (k) {
    if (!s[k]) s[k] = [];
    (Array.isArray(tags) ? tags : [tags]).forEach(function (t) {
      if (t && s[k].indexOf(t) < 0) s[k].push(t);
    });
  });
}

/* 「＋ 补 <属性>」保存成功后**就地生效**(不等服务端两分钟重建索引) —— 与 addLocalTags 同一套路。
 * 只处理 schema 里 editable 的那几个: 列表类追加、单值类替换。 */
function addLocalAttr(file, key, value) {
  var s = findSongByFile(file);
  if (!s) return;
  if (key === 'mbid') { s.mbid = value; return; }         // 单值: 替换(与 MBID= 写盘同义)
  if (key === 'tags' || key === 'usertags') { addLocalTags(file, [value]); return; }
  if (!s[key]) s[key] = [];
  if (s[key].indexOf(value) < 0) s[key].push(value);      // 列表: 追加(服务端也是追加)
}

/* 卡片标题就是这一首的独立页面入口(用户 2026-09-25: "「本谱一页」不要放在下面的链接, 直接把标题做成超链接")
   —— 所以不再有单独的「本谱一页」片子; 标题的可点提示交给 CSS(a.title 的浅下划线)。 */

function tuneTitle(r) {
  var name = esc(r.group || r.title || '');
  if (!r.id) return '<span class="title">' + name + '</span>';
  return '<a class="title tune" href="' + esc(tunePath(r.id)) + '" data-tune="' + esc(r.id) + '"' +
    ' title="打开这一首的页面">' + name + '</a>';
}

var QUERY_DIGITS = '';      // 最近一次查询的数字串(按段空格分开): 给"找不到？欢迎补充"预填
function render(segs, res, ms) {
  QUERY_DIGITS = segs.map(function (sg) {
    return sg.map(function (n) { return n.d; }).join('');
  }).join(' ');
  var qshow = segs.map(show).join('  |  ');
  // 结果区最前面一行(用户 2026-09-25): "找不到？欢迎补充。" -> 跳到下面投稿表单,
  // 并把这次敲的数字自动填进"这段旋律的简谱数字"(找不到时它最有用)。没命中时同样给这一行。
  var nf = '<p class="nf">找不到？<a class="nf-add" href="#sform">欢迎补充。</a></p>';
  if (!res.length) {
    $('status').textContent = '没找到匹配（' + ms + ' 毫秒）。片段至少 5 个音；换更长的片段试试。';
    $<HTMLInputElement>('out').innerHTML = nf + '<p class="hint">如果确认库里应该没有这首歌，也可以直接提 issue：' +
      '<span class="links"><a class="add" href="' + issueUrl(qshow) + '" target="_blank" rel="noopener">＋ 建议收录</a></span></p>';
    return;
  }
  var tied = 0;
  for (var i = 0; i < res.length; i++) if (res[i].cost === res[0].cost) tied++;
  $('status').textContent = '查询 ' + qshow + '：命中 ' + res.length + ' 组，用时 ' + ms + ' 毫秒' +
    (tied > 1 ? '；最优并列 ' + tied + ' 组（片段不够独特，加长或补第二段）' : '');

  var html = '';
  LAST = res[0].group;                 // 供"投稿"表单的「用刚才查询的曲名填入」
  LASTFILE = (res[0].file && res[0].file[0]) || '';   // 纠错时告诉作者改哪一份
  for (var k = 0; k < res.length; k++) {
    var r = res[k];
    html += '<div class="card' + (k === 0 ? ' top' : '') + '">' +
      '<div class="head">' +
        (k === 0 ? '' : '<span class="rank">#' + (k + 1) + '</span>') +
        tuneTitle(r) +
        '<span class="cost c' + Math.min(r.cost, 2) + '">代价 ' + r.cost + '</span>' +
        '<span class="badge">记号 ' + r.exact + '/' + r.qlen + '</span>' +
        '<span class="badge">' + r.n + ' 音符</span>' +
        '<span class="badge">' + esc(r.status || '?') + '</span>' +
        cautionChips(r) +
      '</div>' +
      metaRows(r) +
      '<div class="cmp"><span class="lab">库内该段' + (r.secCn ? '（' + esc(r.secCn) + '）' : '') + '</span> ' + esc(show(r.libNotes)) +
        '　<span class="lab">你的输入</span> ' + esc(show(r.qNotes)) + '</div>' +
      '<div class="score">' + renderScore(r.raw, r.at, r.qlen, r.bars) + '</div>' +
      '<div class="links">' +
        '<span class="lab">收录页</span> ' + exactLinks(r, 'melody') +
        addTagForm(r) +
        '<a class="add" href="' + issueUrl(r.group) + '" target="_blank" rel="noopener" ' +
        'title="库里这首有问题 / 想补充资料 → 一键提 issue">＋ 反馈/补充</a>' +
      '</div></div>';
  }
  $<HTMLInputElement>('out').innerHTML = nf + html +
    '<p class="hint">「记号」是升降号一致的音数。' +
    '<b>收录页</b>是这首歌在该站的具体页面。</p>';
}

$('form').addEventListener('submit', run);

/* ---------------- 「按曲名找」----------------
 * 为什么需要: 补收录页/标签、核对元数据时都是"对着某一首"操作, 而上面的旋律查歌得先知道旋律。
 * 这里只按本地索引(曲名/别名)过滤, 不联网; 卡片与旋律结果卡共用 metaRows / exactLinks 等,
 * 所以「收录页」「待补充」「＋补收录页」的行为完全一致。 */
function titleSearch(q) {
  var s = String(q || '').trim().toLowerCase();
  if (!s || !IDX) return [];
  var out = [];
  for (var i = 0; i < IDX.songs.length && out.length < 40; i++) {
    var x = IDX.songs[i];
    var hay = [x.title, x.group, (x.alias || []).join(' ')].join(' ').toLowerCase();
    if (hay.indexOf(s) >= 0) out.push(x);
  }
  return out;
}

function renderTitle(list, q) {
  if (!list.length) {
    $('tstatus').className = 'status err';
    $('tstatus').textContent = '按曲名没找到「' + q + '」。换个更短的关键词，或用上面的旋律查歌。';
    $('tout').innerHTML = '';
    return;
  }
  $('tstatus').className = 'status';
  $('tstatus').textContent = '按曲名「' + q + '」命中 ' + list.length + ' 首' +
    (list.length >= 40 ? '（只显示前 40 首，写更具体一点）' : '');
  var html = '';
  for (var k = 0; k < list.length; k++) {
    var x = list[k];
    html += '<div class="card">' +
      '<div class="head">' + tuneTitle(x) +
        '<span class="badge">' + x.n + ' 音符</span>' +
        '<span class="badge">' + esc(x.status || '?') + '</span>' +
      '</div>' + metaRows(x) +
      '<div class="links"><span class="lab">收录页</span> ' + exactLinks(x, 'title') +
        addTagForm(x) + '</div>' +
      (x.raw ? '<div class="score">' + esc(x.raw) + '</div>' : '') +
      '</div>';
  }
  $('tout').innerHTML = html;
}

function rerunTitle() { renderTitle(titleSearch($<HTMLInputElement>('tq').value), $<HTMLInputElement>('tq').value); }
if ($('tform')) {
  $('tform').addEventListener('submit', function (ev) {
    ev.preventDefault();
    rerunTitle();
  });
}

/* 「＋ 补收录页」的保存: 走已有投稿接口 -> 服务端校验后把 link=<url> 写进 scores/<file>.txt
 * 并 git commit, 再重建索引。返回值里的 file/commit/refresh 用来给用户回话。 */
document.addEventListener('click', function (ev) {
  // 「每谱一页」的链接: 应用内跳转(不整页刷新, 也不新开标签)
  // "找不到？欢迎补充。": 跳到投稿表单时顺手预选类型/填好刚敲的旋律
  var nfa = closestFrom<HTMLElement>(ev.target, 'a.nf-add');
  if (nfa) {
    var kd = $<HTMLInputElement>('skind'), sc = $<HTMLInputElement>('sscore');
    if (kd) kd.value = 'new';
    if (sc && !sc.value) sc.value = QUERY_DIGITS;
    var st = $<HTMLInputElement>('stitle');
    if (st) setTimeout(function () { st.focus(); }, 0);
    return;                      // 锚点自己会滚过去, 别拦
  }
  var tl = closestFrom<HTMLElement>(ev.target, 'a.tune');
  if (tl) {
    ev.preventDefault();
    navigate(tl.getAttribute('href'));
    return;
  }
  // 圆形 ＋: 就地展开这一张卡的输入框, 并按平台给占位提示
  var pb = closestFrom<HTMLElement>(ev.target, '.plus');
  if (pb) {
    var row = document.getElementById(pb.getAttribute('data-row'));
    if (row) {
      row.hidden = false;
      var pin = row.querySelector('.al-url') as HTMLInputElement | null;
      if (pin) { (pin as HTMLInputElement).placeholder = pb.getAttribute('data-ph') || 'https://…'; (pin as HTMLInputElement).focus(); }
      var pm = row.querySelector('.al-msg');
      if (pm) { pm.textContent = ''; pm.className = 'al-msg'; }
    }
    return;
  }
  // 「＋ 补标签」
  var tb = closestFrom<HTMLButtonElement>(ev.target, '.al-go-tags');
  if (tb) {
    var tbox = tb.closest('.addlink');
    var tin = tbox.querySelector('.at-tags') as HTMLInputElement;
    var tmsg = tbox.querySelector('.al-msg') as HTMLElement;
    var tags = (tin.value || '').trim();
    if (!tags) { tmsg.className = 'al-msg err'; tmsg.textContent = '先填标签'; return; }
    if (READONLY) { readonlyInto(tmsg); return; }        // 只读镜像: 别发一个必 404 的请求
    tb.disabled = true; tmsg.className = 'al-msg'; tmsg.textContent = '保存中…';
    fetch(API + '/api/submit', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: 'tags', file: tb.getAttribute('data-file'), tags: tags }),
    }).then(function (r) { return r.json(); }).then(function (j) {
      tb.disabled = false;
      if (j && j.ok) {
        tmsg.className = 'al-msg ok';
        tmsg.textContent = '已写入 ' + (j.tags || []).join('、') + '（' + j.state + '）' +
          (j.refresh ? '；' + (j.refresh_msg || '索引重建中') : '');
        tin.value = '';
        addLocalTags(tb.getAttribute('data-file'), j.tags);   // 就地生效, 不等 2 分钟重建
      } else {
        tmsg.className = 'al-msg err';
        tmsg.textContent = '失败：' + ((j && j.err) || '未知错误');
      }
    }).catch(function (e) { tb.disabled = false; tmsg.className = 'al-msg err'; tmsg.textContent = '失败：' + e.message; });
    return;
  }
  // 「＋ 补 <属性>」: 卡片元数据行尾那颗 ＋ —— **哪些属性可改由 schema.py 决定**(前端只是照做)
  var ab = closestFrom<HTMLButtonElement>(ev.target, '.al-go-attr');
  if (ab) {
    var abox = ab.closest('.alrow');
    var ain = abox.querySelector('.attr-val') as HTMLInputElement;
    var amsg = abox.querySelector('.al-msg') as HTMLElement;
    var aval = (ain.value || '').trim();
    if (!aval) { amsg.className = 'al-msg err'; amsg.textContent = '先填内容'; return; }
    if (READONLY) { readonlyInto(amsg); return; }
    ab.disabled = true; amsg.className = 'al-msg'; amsg.textContent = '保存中…';
    fetch(API + '/api/submit', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: 'attr', file: ab.getAttribute('data-file'),
                             attr: ab.getAttribute('data-attr'), value: aval }),
    }).then(function (r) { return r.json(); }).then(function (j) {
      ab.disabled = false;
      if (j && j.ok) {
        amsg.className = 'al-msg ok';
        amsg.textContent = '已写入 ' + j.attr + '=' + (j.value || aval) + '（' + j.state + '）' +
          (j.refresh ? '；' + (j.refresh_msg || '索引重建中') : '');
        ain.value = '';
        addLocalAttr(ab.getAttribute('data-file'), ab.getAttribute('data-attr'), j.value || aval);
        // 就地重画当前这次渲染 -> 新值立刻出现(不等服务端重建)
        var are = ab.getAttribute('data-re') || '';
        setTimeout(function () {
          if (are === 'title') rerunTitle();
          else if (are === 'tune') showTune(CURRENT_TUNE);
          else run({ preventDefault: function () {} });
        }, 300);
      } else {
        amsg.className = 'al-msg err';
        amsg.textContent = '失败：' + ((j && j.err) || '未知错误');
      }
    }).catch(function (e) { ab.disabled = false; amsg.className = 'al-msg err'; amsg.textContent = '失败：' + e.message; });
    return;
  }
  var b = closestFrom<HTMLButtonElement>(ev.target, '.al-go');
  if (!b) return;
  var box = b.closest('.addlink');
  var inp = box.querySelector('.al-url') as HTMLInputElement;
  var msg = box.querySelector('.al-msg') as HTMLElement;
  var url = (inp.value || '').trim();
  if (!url) { msg.className = 'al-msg err'; msg.textContent = '先粘贴网址'; return; }
  if (READONLY) { readonlyInto(msg); return; }          // 同上
  b.disabled = true;
  msg.className = 'al-msg';
  msg.textContent = '保存中…';
  fetch(API + '/api/submit', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind: 'link', file: b.getAttribute('data-file'), url: url }),
  }).then(function (r) { return r.json(); }).then(function (j) {
    b.disabled = false;
    if (j && j.ok) {
      msg.className = 'al-msg ok';
      msg.textContent = '已写入 ' + j.file +
        (j.committed ? '（已 git commit）' : '（未提交：' + (j.git || '未知原因') + '）') +
        (j.refresh ? '；' + (j.refresh_msg || '索引重建中，约 2 分钟后刷新可见') : '');
      inp.value = '';
      // **输入后自动补充**: 先把链接就地写进本地索引(否则要等服务端 ~2 分钟重建完才会变绿),
      // 再重跑当前这次渲染 -> 黄片立刻变绿真链接
      addLocalLinks(b.getAttribute('data-file'), j.url);
      // 重跑当前这次的渲染 -> 灰片/黄片立刻变成绿色真链接(谱页 / 检索卡 / 按曲名卡各走各的)
      var re = b.getAttribute('data-re') || '';
      setTimeout(function () {
        if (re === 'title') rerunTitle();
        else if (re === 'tune') showTune(CURRENT_TUNE);
        else run({ preventDefault: function () {} });
      }, 400);
    } else {
      msg.className = 'al-msg err';
      msg.textContent = '失败：' + ((j && j.err) || '未知错误');
    }
  }).catch(function (e) {
    b.disabled = false;
    msg.className = 'al-msg err';
    msg.textContent = '失败：' + e.message;
  });
});
var exs = document.getElementsByClassName('ex');
for (var i = 0; i < exs.length; i++) {
  exs[i].addEventListener('click', function (ev) {
    ev.preventDefault();
    $<HTMLInputElement>('q').value = this.getAttribute('data-q');
    run();
  });
}

/* ---------- 投稿(不用登录, 不碰 GitHub) ---------- */
var API = window.JIANPU_API || (location.protocol + '//' + location.host);   // 同源; 换服务器就设 window.JIANPU_API
// 纯静态托管(如 GitHub Pages 镜像)没有写回服务: 构建时注入 window.JIANPU_READONLY=true。
// 读路径(检索/卡片/谱页)本来全在浏览器里跑, 一点不受影响; 只有"投稿/补收录/补标签"要给句人话。
var READONLY = !!window.JIANPU_READONLY;
// 有写回的那份: **正式站点**（Cloudflare Worker, 2026-09-30 起绑了自定义域名 jianpu-db.org）。
// GitHub Pages 这份是**只读镜像**，遇到"投稿/补收录/补标签"就把人指到这里。
// （早先指向 jianpu-web.pages.dev；那个地址仍然可用，只是不再是门面。）
var MIRROR = 'https://jianpu-db.org/';

function readonlyInto(el) {
  el.className = (el.classList && el.classList.contains('al-msg')) ? 'al-msg err' : 'status err';
  el.innerHTML = '只读镜像：投稿请到 ' +
    '<a href="' + MIRROR + '" target="_blank" rel="noopener">jianpu-db.org</a>。';
}

var LAST = '', LASTFILE = '';   // 供「投稿」表单: 曲名 + 刚查的那一份曲谱文件

function submit() {
  var t = $<HTMLInputElement>('stitle').value.trim();
  if (!t) { $('sstatus').className = 'status err'; $('sstatus').textContent = '请填曲名。'; return; }
  if (READONLY) { readonlyInto($('sstatus')); return; }
  var kind = $<HTMLInputElement>('skind').value;
  var body = {
    kind: kind, title: t,
    score: $<HTMLInputElement>('sscore').value.trim(), note: $<HTMLInputElement>('snote').value.trim(),
    contact: $<HTMLInputElement>('scontact').value.trim(),
    // 纠错/元数据: 带上刚才查的那一份, 作者不用猜你说的是哪份
    file: (kind === 'fix' || kind === 'meta') ? LASTFILE : ''
  };
  $('sstatus').className = 'status';
  $('sstatus').textContent = '提交中…';
  $<HTMLButtonElement>('sgo').disabled = true;
  fetch(API + '/api/submit', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
  }).then(function (r) { return r.json().then(function (j) { return { s: r.status, j: j }; }); })
    .then(function (x) {
      $<HTMLButtonElement>('sgo').disabled = false;
      if (x.j && x.j.ok) {
        $('sstatus').textContent = '已收到，编号 ' + x.j.id +
          (x.j.score_file ? '（已生成曲谱 ' + x.j.score_file + ' 入库' +
            (x.j.refresh ? '，索引重建中，约 2 分钟后可搜到' : '') + '）'
            : '（只留了投稿，没有数字）') +
          (x.j.score_warn ? '　⚠ ' + x.j.score_warn : '');
        $<HTMLInputElement>('stitle').value = ''; $<HTMLInputElement>('sscore').value = ''; $<HTMLInputElement>('snote').value = '';
      } else {
        $('sstatus').className = 'status err';
        $('sstatus').textContent = '提交失败：' + ((x.j && x.j.err) || ('HTTP ' + x.s)) +
          '。也可以提 Issue：<a href="' + issueUrl(t) + '" target="_blank" rel="noopener">预填 Issue</a>';
        $('sstatus').innerHTML = $('sstatus').textContent;
      }
    })
    .catch(function (e) {
      $<HTMLButtonElement>('sgo').disabled = false;
      $('sstatus').className = 'status err';
      $('sstatus').innerHTML = '连不上投稿服务（' + e.message + '）。也可以提 ' +
        '<a href="' + issueUrl(t) + '" target="_blank" rel="noopener">Issue</a>';
    });
}

$('sform').addEventListener('submit', function (e) { e.preventDefault(); submit(); });
if (READONLY) {           // 表单还在, 但先把话说清楚 —— 免得人填完才发现写不进去
  $('sform').insertAdjacentHTML('beforebegin',
    '<p class="lead" id="ro-note">⚠ 只读镜像：查歌、谱页可用；投稿请到 ' +
    '<a href="' + MIRROR + '" target="_blank" rel="noopener">jianpu-db.org</a>。</p>');
}
$<HTMLButtonElement>('sfill').addEventListener('click', function () {
  if (LAST) { $<HTMLInputElement>('stitle').value = LAST; }
  else { $('sstatus').textContent = '先在上面查一次，再点这个按钮。'; }
});

loadCorpus().then(function (txt) {
  IDX = buildIndex(txt);
  // **ngram 倒排**（检索剪枝用）放到空闲时建：实测建它要 ~136 ms（2.5M 音符），
  // 直接加在这儿会把"打开页面到能用"从 ~200 ms 推到 ~330 ms。
  // 空闲建的时候页面已经可用了；没建好时检索走全扫 —— 结果完全一样（见 search.ts 的剪枝注释）。
  setTimeout(function () {
    try { if (IDX) ensureGrams(IDX, 4); } catch (e) { /* 建不起来就一直全扫 */ }
  }, 800);
  return fetch(appUrl('data/stats.json')).then(function (r) { return r.json(); })
    .then(function (st) { loadPlatforms(st); loadFields(st); return st; });
}).then(function (st) {
  // 只报"有多少东西可查"; 原图那栏早去掉了, 别再提(2026-09-25 用户: 文案从简)。
  $('stats').textContent = '语料 ' + st.songs + ' 首（' + st.groups + ' 组），' +
    st.notes.toLocaleString() + ' 个音符。';
  $('status').textContent = '就绪，共 ' + IDX.count + ' 首。';
  fillTagList();
  // 深链: 直接打开 /s/<id> 也要能渲出那一页(先把语料装上, 再按地址路由)
  if (typeof window !== 'undefined' && window.addEventListener) window.addEventListener('popstate', route);
  if (tuneIdFromLocation()) route();
  else showHome('');
}).catch(function (err) {
  $('status').className = 'status err';
  $('status').textContent = '初始化失败：' + err.message;
});

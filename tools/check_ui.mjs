// node tools/check_ui.mjs [查询] [按曲名查询] —— 真跑一遍"表单提交 → run() → render() → 结果卡 HTML"。
// 与 check_page.mjs 的区别: check_page 直接调 search(), 绕过了 render(); 这个脚本会
// 捕获 app.js 注册的事件处理器并**真的触发一次查询**, 所以 render() 里的运行时错误藏不住。
// (URL 参数会被忽略 —— 本脚本读本地 data/, 只为与 check_all.sh 的其他脚本统一调用方式。)
import { readFileSync } from 'node:fs';
import { importStatic } from './_built.mjs';
const { buildIndex, search } = await importStatic('search');
const { parseQuery } = await importStatic('jptok');
import { gunzipSync } from 'node:zlib';
const argv = process.argv.slice(2).filter((a) => !/^https?:\/\//.test(a));
const SONGS_TXT = gunzipSync(readFileSync(new URL('../data/songs.jsonl.gz', import.meta.url))).toString('utf8');

// 查询片段**从"自带 MBID 的歌"自己的音高串里取**, 而且当场验证"它确实排第一" ——
// 为什么不能写死: 语料一涨, 同一个片段的头名就会换(2026-09-28 实测: 语料 8926 -> 9340 后,
// 写死的 `33565653253` 头名从《U.N.オーエン》/《神々》变成《你怎么说》—— 后者没有 MBID,
// 于是下面两条"有 MBID 的卡必须有绿片"的断言把**正确的产品行为**报成了失败)。
const _rows = SONGS_TXT.split('\n').filter((l) => l.trim()).map((l) => JSON.parse(l));
const _idx = buildIndex(SONGS_TXT);
let AUTO_QUERY = '';
for (const r of _rows) {
  if (!r.mbid || (r.p || '').length < 15) continue;
  const q = r.p.slice(0, 15);
  const top = search(_idx, [parseQuery(q)], { top: 1 })[0];
  if (top && top.group === r.g) { AUTO_QUERY = q; break; }
}
if (!AUTO_QUERY) { console.error('!! 找不到"自带 MBID 且自己排第一"的查询片段(语料异常?)'); process.exit(1); }
const QUERY = argv[0] || AUTO_QUERY;
const TQUERY = argv[1] || '神々';

const els = {}, handlers = {};
function mkEl(id) {
  return els[id] || (els[id] = {
    id, _html: '', textContent: '', className: '', value: '', disabled: false, checked: true,
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = v; },
    addEventListener(ev, fn) { (handlers[id] = handlers[id] || {})[ev] = fn; },
    focus() {}, getAttribute() { return ''; }, closest() { return null; },
    querySelector() { return mkEl(id + '-q'); }, querySelectorAll() { return []; },
  });
}
global.document = { getElementById: mkEl, getElementsByClassName: () => [], querySelectorAll: () => [],
  addEventListener(ev, fn) { (handlers['document'] = handlers['document'] || {})[ev] = fn; } };
global.window = global;
global.location = { protocol: 'http:', host: '127.0.0.1:8770' };
global.performance = { now: () => Date.now() };
global.fetch = async (u) => {
  if (String(u).endsWith('songs.jsonl.gz')) {
    const buf = readFileSync(new URL('../data/songs.jsonl.gz', import.meta.url));
    return { ok: true, body: new Response(buf).body, json: async () => ({}) };
  }
  if (String(u).endsWith('stats.json')) {
    // **故意把两个字段的显示名改掉**: 卡片上要是还印出"歌手/人标", 就说明前端把中文名**硬编码**了。
    // 显示名的唯一真源是 jianpu-db/schema.py:FIELDS -> stats.json(fields), 前端只许照着画。
    const st = JSON.parse(readFileSync(new URL('../data/stats.json', import.meta.url), 'utf8'));
    if (st.fields && st.fields.artist && st.fields.usertags) {
      st.fields.artist.label.zh = '歌手X';
      st.fields.usertags.label.zh = '人标X';
    }
    return { ok: true, json: async () => st };
  }
  return { ok: false, status: 404, json: async () => ({}) };
};
global.DecompressionStream = (await import('node:stream/web')).DecompressionStream;
let errors = [];
process.on('unhandledRejection', (e) => errors.push('未处理的 Promise 拒绝: ' + (e && e.message)));
process.on('uncaughtException', (e) => errors.push('未捕获异常: ' + (e && e.message)));

await importStatic('app');
{
  const ms = await waitFor(() => handlers['form'] && handlers['form'].submit, 20000);
  console.log(ms < 0 ? '  ! 等了 20 秒 app 还没注册表单处理器' : `  （app 在 ${ms} ms 内就绪）`);
}
mkEl('q').value = QUERY;
const h = handlers['form'] && handlers['form'].submit;
if (!h) { console.error('!! app.js 没有给 #form 注册 submit 处理器'); process.exit(1); }
try {
  h({ preventDefault() {} });
} catch (e) {
  errors.push('run() 抛异常: ' + e.message);
}
await waitFor(() => mkEl('out').innerHTML.length > 0, 20000);   // 等查询结果渲出来

const status = mkEl('status').textContent;
const out = mkEl('out').innerHTML;
console.log('status :', status.slice(0, 120));
console.log('#out 长度:', out.length);
let fail = 0;
const ok = (c, m) => { console.log((c ? '✓ ' : '✗ ') + m); if (!c) fail++; };
ok(!errors.length, '没有运行时错误' + (errors.length ? ' -> ' + errors.join(' | ') : ''));
ok(out.length > 200, '#out 真的渲染出了内容');
ok(!/undefined|NaN|\[object Object\]/.test(out), 'HTML 里没有 undefined / NaN / [object Object]');
for (const [name, re] of [['卡片', /class="card/], ['标黑', /<mark>/], ['收录页那行', /class="lab">收录页/],
                          ['待补充或精确链接', /(class="exact"|待补充)/], ['黄色待补片(指向搜索页的链接)', /<a class="exact pending" href=/], ['圆形 ＋', /class="plus"/],
                          ['歌手行(显示名来自 schema, 不是硬编码)', /<th><span[^>]*>歌手X<\/span><\/th>/],
                          ['本谱一页(通向 /s/<id> 的站内链接)', /class="title tune" href="[^"]*\/s\//],
                          ['补收录页表单', /class="addlink"/], ['小节线', /class="bar"/]]) {
  ok(re.test(out), '结果卡里有「' + name + '」');
}

/* 2026-09-29 用户: "这些标签最好也加个加号, 当然要在 schema 里注明哪些是可以修改哪些是不能修改的。
 * 另外不要对这些硬编码, 每一个 attribute 的名字应该独立于这个 attribute, 以方便多语言支持。"
 * -> 能改的属性(歌手/人标/别名/MBID)行尾要有 ＋; 只读属性(文件/曲名/状态/音符/小节/出处/转写/标签)
 *    一颗都不能有; 显示名一律从 schema 来(上面故意改过名, 这里就能验出来)。 */
for (const k of ['artist', 'usertags', 'alias', 'mbid']) {
  ok(new RegExp('class="plus attr-plus"[^>]*data-attr="' + k + '"').test(out),
     '可改属性有 ＋: ' + k);
}
for (const k of ['file', 'group', 'status', 'n', 'bars', 'source', 'transcriber', 'tags']) {
  ok(!new RegExp('data-attr="' + k + '"').test(out), '只读属性没有 ＋: ' + k);
}
ok(!/<th><span[^>]*>人标<\/span><\/th>/.test(out) && !/<th><span[^>]*>歌手<\/span><\/th>/.test(out),
   '没有把显示名硬编码在 app.js 里(schema 改名后卡片跟着变)');
// 2026-09-25 用户: "「本谱一页」不要放在下面的链接, 直接把标题做成超链接"
// -> 卡片标题本身就是 /s/<id> 的入口, 而且**不再**有单独的「本谱一页」片子。
const titleLink = (out.match(/<a class="title tune" href="([^"]+)" data-tune="([^"]+)"/) || []);
ok(!!titleLink[1] && /\/s\//.test(titleLink[1]), '卡片标题就是「本谱一页」入口: ' + (titleLink[1] || '(没有)'));
ok(!/class="tune-link/.test(out), '不再有单独的「本谱一页」链接');

// 2026-09-25 用户: "在前端搜索结果最前面加个'找不到？欢迎补充。'"
ok(/<p class="nf">找不到？<a class="nf-add" href="#sform">欢迎补充。<\/a><\/p>/.test(out),
   '结果最前面有「找不到？欢迎补充。」并指向投稿表单');
ok(out.indexOf('class="nf"') >= 0 && out.indexOf('class="nf"') < out.indexOf('class="card"'),
   '这行确实在卡片**前面**(不是塞在末尾)');
// 2026-09-25 用户: "我的 U.N.Owen 已经有 MusicBrainz 了, 你怎么还后面加个黄的链接?"
// 口径: **按卡片**判断 —— 有 MBID 的那张卡: 绿色 MusicBrainz 必须有、黄色待补必须没有;
// 没 MBID 的卡: 黄色待补照旧要有(别一刀切掉)。
// ⚠ 2026-09-28: 不能假定"有 MBID 的那首一定是第一张卡" —— 语料一涨, 同一查询的头名就会换
//   (实测: 语料 8926 -> 9340 后, `3 3 5 6 5 6 5 3 2 5 3` 的头名从《U.N.オーエン》变成《你怎么说》,
//    而后者没有 MBID -> 老写法把"正确的产品行为"报成了失败)。改成**找那张有绿片的卡**再断言。
const mbidAt = out.indexOf('musicbrainz.org/work/');
const mbidCard = mbidAt < 0 ? '' : (() => {
  const s = out.lastIndexOf('class="card', mbidAt);
  const e = out.indexOf('class="card', s + 5);
  return out.slice(s, e < 0 ? undefined : e);
})();
ok(/class="exact" href="https:\/\/musicbrainz\.org\/work\//.test(mbidCard),
   '有 MBID 的歌给出 MusicBrainz 绿色片子（已收录）');
ok(mbidCard !== '' && !/exact pending" href="https:\/\/musicbrainz\.org\/search/.test(mbidCard),
   '这首已有 MusicBrainz -> 不再出现黄色待补片子');
ok(/exact pending" href="https:\/\/musicbrainz\.org\/search/.test(out),
   '没有 MBID 的歌仍然给黄色 MusicBrainz 待补片子(没被一刀切掉)');

// 把卡片开头一小段打出来, 方便肉眼核对
const i = out.indexOf('<div class="links">');
console.log('\n链接区 HTML:\n', out.slice(i, i + 900).replace(/></g, '>\n<'));

// ---- 「按曲名找」也要能真的渲染(补收录页/标签的工作流入口) ----
mkEl('tq').value = TQUERY;
const th = handlers['tform'] && handlers['tform'].submit;
ok(!!th, 'app.js 给 #tform 注册了 submit 处理器');
if (th) {
  th({ preventDefault() {} });
  const tout = mkEl('tout').innerHTML;
  ok(mkEl('tstatus').textContent.includes('命中'), '按曲名找到了: ' + mkEl('tstatus').textContent.slice(0, 60));
  ok(/class="card/.test(tout), '按曲名结果渲染出了卡片');
  for (const [name, re] of [['收录页行', /class="lab">收录页/], ['待补充或精确链接', /(class="exact"|待补充)/],
                            ['补收录页表单', /class="addlink"/], ['补标签表单', /class="al-go-tags"/]]) {
    ok(re.test(tout), '按曲名卡片里有「' + name + '」');
  }
}
// 标签词表(<datalist> 在页面里, 不在卡片 HTML 里) —— 检查它真的被语料词表灌满了
const tl = mkEl('taglist');
const nopt = (tl.innerHTML.match(/<option/g) || []).length;
ok(nopt > 50, '#taglist 已灌入语料标签词表(' + nopt + ' 个)');

// ---- 投稿表单: 客户端必须把字段(尤其是"纠错目标")正确送出去 ----
// 2026-09-24 实测的坑: fix/meta 不告诉作者改哪一份 -> 作者收到"这首转错了"却不知道指哪首。
const posts = [];
const prevFetch = global.fetch;
global.fetch = async (u, opt) => {
  if (String(u).includes('/api/submit')) {
    const body = JSON.parse(opt.body);
    posts.push(body);
    return {
      ok: true, status: 200,
      json: async () => ({
        ok: true, id: 'T1', score_file: body.score ? '投稿测试曲.txt' : '', committed: true,
        refresh: !!body.score, refresh_msg: '已开始重建(约 2 分钟)',
        score_warn: /[a-zA-Z\u4e00-\u9fa5]/.test(body.score || '') ? '有字符没认出来丢了' : '',
      }),
    };
  }
  return prevFetch(u, opt);
};
const sf = handlers['sform'] && handlers['sform'].submit;
ok(!!sf, 'app.js 给 #sform 注册了 submit 处理器');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
if (sf) {
  mkEl('skind').value = 'new';
  mkEl('stitle').value = '投稿测试曲';
  mkEl('sscore').value = '63731232';
  mkEl('snote').value = '说明文字';
  mkEl('scontact').value = 'me@example.com';
  sf({ preventDefault() {} });
  await sleep(120);
  const p0 = posts[0] || {};
  ok(p0.kind === 'new' && p0.title === '投稿测试曲' && p0.score === '63731232'
     && p0.note === '说明文字' && p0.contact === 'me@example.com', 'new: 各字段都送到了');
  ok(p0.file === '', 'new: 不带"纠错目标"');
  ok(/T1/.test(mkEl('sstatus').textContent) && /投稿测试曲\.txt/.test(mkEl('sstatus').textContent),
     '成功提示里有编号和生成的曲谱名');
  ok(/重建/.test(mkEl('sstatus').textContent), '成功提示里说了索引在重建');

  // 纠错/元数据: 必须自动带上"刚才查询命中的那一份"
  mkEl('skind').value = 'fix';
  mkEl('stitle').value = '神々が恋した幻想郷';
  mkEl('sscore').value = '1 2 3 4 5';
  sf({ preventDefault() {} });
  await sleep(120);
  // 期望值从**渲染出来的卡片**里取(补收录页按钮上的 data-file), 不写死曲名
  const mfile = (out.match(/class="al-go" data-file="([^"]+)"/) || [])[1] || '';
  ok(!!mfile, '结果卡里能取到文件名(用于与投稿目标比对): ' + mfile);
  const p1 = posts[1] || {};
  ok(p1.kind === 'fix' && p1.file === mfile,
     'fix: 自动带上了刚才查询命中的文件(' + (p1.file || '空') + ', 期望 ' + mfile + ')');
  mkEl('skind').value = 'meta';
  mkEl('stitle').value = mkEl('stitle').value || '神々';   // 提交成功后表单会清空曲名, 这里重新填上
  sf({ preventDefault() {} });
  await sleep(120);
  ok((posts[2] || {}).file === mfile, 'meta: 同样带上了目标文件');

  // 认不出的字符 -> 警告必须透传到界面(不能悄悄吞掉)
  mkEl('skind').value = 'new';
  mkEl('stitle').value = '垃圾输入曲';
  mkEl('sscore').value = 'abc';
  sf({ preventDefault() {} });
  await sleep(120);
  ok(/没认出来/.test(mkEl('sstatus').textContent), '认不出的字符有警告提示');

  // 空曲名: 本地就该拦住, 不要白发一次请求
  const n = posts.length;
  mkEl('stitle').value = '';
  sf({ preventDefault() {} });
  await sleep(120);
  ok(posts.length === n && /请填曲名/.test(mkEl('sstatus').textContent), '空曲名本地拦住, 不发请求');
}

/* 放在**最后**跑: 2026-09-29 用户口径"别让人把转写噪声当铁证" —— 同代价并列 / 本曲仅此一版
 * 且机器转写, 卡片上要出提示片子(class="warn")。用**已知会并列**的那句 `33565653253` 验:
 * 它同时代价 0 命中《你怎么说》(ocr) 与《神々が恋した幻想郷》(ok)。
 * (⚠ 必须放最后: 它会把"上一次命中的文件"改成你怎么说_2, 影响上面投稿类的断言。) */
{
  mkEl('q').value = '33565653253';
  handlers['form'].submit({ preventDefault() {} });
  await sleep(600);
  const wout = mkEl('out').innerHTML;
  ok(/class="warn"/.test(wout), '并列/独证 提示片子出现在卡片上');
  ok(new RegExp('并列：另有 \\d+ 首同分').test(wout), '提示文案说清了"另有几首同分"');
  ok(/title="这句不是唯一命中[^"]*"/.test(wout), '提示片子的 title 解释了"别当铁证"');
}

console.log(fail === 0 ? '\nUI 渲染自检 通过' : `\nUI 渲染自检 失败 ${fail} 项`);
process.exitCode = fail ? 1 : 0;

// node tools/check_search.mjs —— 前端检索 headless 校验(与 Python 侧 lookup_acc.py 同口径)
import { readFileSync } from 'node:fs';
import { importStatic } from './_built.mjs';
const { buildIndex, search } = await importStatic('search');
const { parseQuery, show } = await importStatic('jptok');
import { gunzipSync } from 'node:zlib';

const gz = readFileSync(new URL('../data/songs.jsonl.gz', import.meta.url));
const t0 = Date.now();
const idx = buildIndex(gunzipSync(gz).toString('utf8'));
console.log(`索引 ${idx.count} 首 / ${idx.groupCount} 组，${Date.now() - t0} ms\n`);

// ---- 查询串的**贪心**切 token(用户 2026-09-25: "输入 63731232#5 要用贪心算法, 自动把 #5 算成一个 token") ----
// 规矩: 从左往右尽量多吃; 后置的升降号/八度**只在后面不再是"八度*数字"时**才归当前音。
let tkFail = 0;
const tk = (cond, msg) => { console.log((cond ? '✓ ' : '✗ ') + msg); if (!cond) tkFail++; };
const lastOf = (q) => { const r = parseQuery(q); return r[r.length - 1]; };
tk(show(parseQuery('63731232#5')) === '6 3 7 3 1 2 3 2 #5',
   "#5 是一个 token: 63731232#5 -> " + show(parseQuery('63731232#5')));
tk(parseQuery('63731232#5').length === 9, "共 9 个音(不是 8 也不是 10)");
tk(lastOf('63731232#5').acc === 1 && lastOf('63731232#5').d === 5, "末音是 #5(升号没粘到前面的 2 上)");
tk(show(parseQuery('3#5')) === '3 #5', "3#5 -> 3 #5");
tk(show(parseQuery('6#')) === '#6', "6# -> 尾随升号仍归 6(后面没音了)");
tk(show(parseQuery('#5')) === '#5', "#5 -> #5");
tk(show(parseQuery('3b5')) === '3 b5', "3b5 -> 3 b5(降号同理)");
tk(parseQuery('63731232,5').length === 9 && lastOf('63731232,5').oct === 1,
   "八度同理: 63731232,5 的逗号归**最后的 5**(该音记到 1 个逗号), 共 9 个音");
tk(parseQuery('63731232').length === 8 && parseQuery('12 345').length === 5,
   "纯数字/带空格的输入不受影响");
if (tkFail) { console.log('\n贪心切 token 失败 ' + tkFail + ' 项'); process.exit(1); }

// ---- 升降号口径(用户原话)：「输入 5 能匹配到 #5；输入 #5 匹配 #5 能更精确。发送从严、接收从宽。」----
// 用例取自《U.N.オーエンは彼女なのか？》里 `2 2 2 6 4 #5 3 1 6 #5 7` 这一段
// (全库只有 20 首带升降号, 这个用例很难得)。四条断言锁"接收从宽 / 发送从严 / 写反罚最重"。
let accFail = 0;
const ac = (cond, msg) => { console.log((cond ? '✓ ' : '✗ ') + msg); if (!cond) accFail++; };
const ACC_SONG = 'U.N.オーエンは彼女なのか？';
const topOf = (q) => {
  const segs = [parseQuery(q)].filter((s) => s.length >= 5);
  return search(idx, segs, { top: 1 })[0];
};
const accPlain = topOf('22264531657');        // 用户没写记号
const accSharp = topOf('22264#5316#57');      // 用户写了 #
const accFlat = topOf('22264b5316b57');       // 用户写反了
ac(accPlain && accPlain.group === ACC_SONG,
   `输入 5(不带记号) 仍能找到 #5 那首: ${accPlain && accPlain.group} —— 接收从宽`);
ac(accSharp && accSharp.group === ACC_SONG && accSharp.cost === 0,
   `输入 #5 精确命中, 代价 0(实测 ${accSharp && accSharp.cost}) —— 发送从严`);
ac(accPlain && accSharp && accSharp.cost < accPlain.cost,
   `带记号比不带更精确: ${accSharp && accSharp.cost} < ${accPlain && accPlain.cost}`);
ac(accFlat && accPlain && accPlain.cost < accFlat.cost,
   `记号写反罚最重: b 版 ${accFlat && accFlat.cost} > 不带 ${accPlain && accPlain.cost}`);
if (accFail) { console.log('\n升降号口径失败 ' + accFail + ' 项'); process.exit(1); }

// [查询, 期望曲名, 说明] —— 期望值以 Python 侧为基准
const CASES = [
  // 2026-09-25 起按用户选的"段落权重参与排序"(B): 8 音的 63731232 在两首里都 0 代价,
  // 但《U.N.オーエンは彼女なのか？》那处落在**副歌**(1.6)、《神々が恋した幻想郷》那处落在
  // **发狂钢琴**(0.8) -> 前者排前。老期望写的是"唯一命中"(那时没有段落权重)。
  ['63731232', 'U.N.オーエンは彼女なのか？', '8 音 0 代价（副歌 vs 发狂钢琴，段落加权后）'],
  ['55532235 3211612655', '上春山', '两段'],
  ['5 5 5 3 2 2 3 5 3 2 1 1 6 1 2 6 5 5', '上春山', '空格不分段'],
  ['66165535 532322 7656', '鲁冰花', '三段'],
  ['5111156711', '义勇军进行曲', '单段'],
];
let pass = 0;
for (const [q, want, note] of CASES) {
  const segs = q.split(/[;；|、+，,]+/).map(parseQuery).filter((s) => s.length >= 5);
  const t = Date.now();
  const res = search(idx, segs, { top: 3 });
  const top = res[0];
  const ok = top && (top.group === want || top.title === want);
  if (ok) pass++;
  console.log(`${ok ? '✓' : '✗'} ${q.padEnd(38)} -> ${top ? `${top.group} (代价 ${top.cost})` : '无'}  ${Date.now() - t} ms  [${note}]`);
  if (top) console.log(`     库内该段 ${show(top.libNotes)}  |  输入 ${show(top.qNotes)}`);
  if (!ok) console.log('     候选:', res.map((r) => `${r.group}(${r.cost})`).join(' | '));
}
console.log(`\n通过 ${pass}/${CASES.length}`);

// ── 回归: 索引字段必须**穿过 buildIndex**（2026-10-01 TS 化时抓到的 bug）───────────────
// 原来 `buildIndex` 的字段白名单里没有 `conf` / `confP10`，于是:
//   ① 并列裁决里"转写置信度高优先"这条永远拿到 0.5（等于没生效）；
//   ② 卡片上的置信度永远显示不出来。
// 这是本项目**第 5 次**栽在"白名单式字段复制"上，所以在这里钉一颗钉子:
let fldFail = 0;
const fld = (cond, msg) => { console.log((cond ? '✓ ' : '✗ ') + msg); if (!cond) fldFail++; };
const withConf = idx.songs.filter((s) => s.conf != null && s.conf !== '').length;
const rowsWithConf = (() => {
  let n = 0;
  for (const line of gunzipSync(gz).toString('utf8').split('\n')) {
    if (!line) continue;
    try { if (JSON.parse(line).conf != null) n++; } catch { /* 跳过坏行 */ }
  }
  return n;
})();
fld(rowsWithConf > 0, `索引行里有 ${rowsWithConf} 首带 conf（数据侧非空）`);
fld(withConf === rowsWithConf,
    `conf 穿过 buildIndex: ${withConf} == ${rowsWithConf}（差一个都算丢字段）`);
const resConf = search(idx, [parseQuery('5 1 1 1 1 5 6 7 1 1')], { top: 5 }).filter((r) => r.conf != null);
fld(resConf.length > 0, `结果里能拿到 conf（样例 ${resConf.length} 条，例 ${resConf[0] ? resConf[0].conf : '—'}）`);
const groupsOf = new Map();
for (const s of idx.songs) {
  if (!groupsOf.has(s.group)) groupsOf.set(s.group, new Set());
  groupsOf.get(s.group).add(s.source || '');
}
const dupGroups = [...groupsOf.values()].filter((v) => v.size >= 2).length;
fld(dupGroups > 0, `"版本数按 source 去重"有实际对象: ${dupGroups} 组有多个不同 source`);
if (fldFail) { console.log(`\n索引字段回归失败 ${fldFail} 项`); process.exit(1); }


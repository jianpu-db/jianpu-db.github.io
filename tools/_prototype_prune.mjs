// node tools/_prototype_prune.mjs
//
// **原型：ngram 倒排预筛到底值不值得做**（先量再改 —— 上一轮我跳过这一步，去做了 wasm，白花两轮）。
//
// 要回答三个问题:
//   ① 建索引多贵（页面加载时建，会加到"就绪时间"上）
//   ② 剪得准不准 —— 对"精确命中"（代价 0）的查询，候选集必须**恰好等于**全库里代价 0 的那些歌，
//      少一个就会漏掉本该排第一的结果（并列裁决还要用到全部同代价的歌）
//   ③ 剪得快多少
//
// 设计要点: **只在能证明安全时才走剪枝** —— 查询的整段必须能当作子串找到（代价 0）；
// 一旦有任何一段找不到，就退回全扫（fuzzy 查询照样正确，只是不快）。
import { readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { importStatic } from './_built.mjs';

const K = Number(process.argv[2] || 4);            // ngram 长度
const { buildIndex } = await importStatic('search');
const { parseQuery } = await importStatic('jptok');
const idx = buildIndex(gunzipSync(readFileSync(new URL('../data/songs.jsonl.gz', import.meta.url))).toString('utf8'));
const songs = idx.songs;

// ── ① 建 ngram 倒排（gram -> 歌下标）────────────────────────────────────────
const t0 = performance.now();
const grams = new Map();                            // key: gram 的字符串（K 小，字符串 key 足够快且好懂）
for (let i = 0; i < songs.length; i++) {
  const p = songs[i].p;
  for (let j = 0; j + K <= p.length; j++) {
    const g = p.slice(j, j + K);
    let arr = grams.get(g);
    if (!arr) { arr = []; grams.set(g, arr); }
    arr.push(i);
  }
}
const tBuild = performance.now() - t0;
let postings = 0;
for (const a of grams.values()) postings += a.length;
console.log(`① 建索引: ${grams.size.toLocaleString()} 个不同 ${K}-gram · ${postings.toLocaleString()} 条 posting · 用时 ${tBuild.toFixed(1)} ms`);

// ── ② 剪枝: 取查询里**最稀有**的 K-gram 的 posting，再逐首验证"整段包含" ──────
function candidatesFor(qstr) {
  if (qstr.length < K) return null;                 // 太短: 交给全扫
  let best = null, bestLen = Infinity, any = false;
  for (let j = 0; j + K <= qstr.length; j++) {
    const g = qstr.slice(j, j + K);
    const arr = grams.get(g);
    if (!arr) return [];                            // 这个 gram 全库都没有 -> 精确命中不可能
    any = true;
    if (arr.length < bestLen) { bestLen = arr.length; best = arr; }
  }
  if (!any || !best) return null;
  // ⚠ posting 里同一首会出现**多次**（重复 gram，如 `16665` 里的 `66`）—— 必须去重，
  //   否则候选数会虚高（实测 1645 vs 真值 778 就是重复造成的，看着像"剪枝不准"）。
  const seen = new Uint8Array(songs.length);
  const out = [];
  for (const i of best) {
    if (seen[i]) continue;
    seen[i] = 1;
    if (songs[i].p.includes(qstr)) out.push(i);
  }
  return out;
}

// ── ③ 对拍: 剪枝候选集 vs 全库"代价 0"的歌, 必须完全一致 ────────────────────
const QUERIES = ['63731232', '55532235', '5111156711', '66165535', '532322', '16665', '5565345',
                 '17123215', '236666665', '1121113555566'];
let bad = 0;
console.log('\n② 剪枝正确性（候选集必须 == 全库代价 0 的歌集合）');
const candTimes = [], fullZeroTimes = [], searchTimes = [], pruneSearchTimes = [];
for (const raw of QUERIES) {
  const q = parseQuery(raw);
  const qstr = q.map((x) => x.d).join('');          // 只用音级（变音不进 p 串）
  const t1 = performance.now();
  const cand = candidatesFor(qstr);
  candTimes.push(performance.now() - t1);
  if (cand === null) { console.log(`  - ${raw}: 太短，走全扫`); continue; }
  // 全库真值: 直接 p.includes（与代价 0 等价）
  const t2 = performance.now();
  const truth = [];
  for (let i = 0; i < songs.length; i++) if (songs[i].p.includes(qstr)) truth.push(i);
  fullZeroTimes.push(performance.now() - t2);
  const same = cand.length === truth.length && cand.every((v, k) => v === truth[k]);
  if (!same) bad++;
  // 端到端潜力: 剪枝后只对候选跑"真实的代价扫描"（用同一个 cost 函数），对比全库扫
  const cost = (qd, qacc, cd, cacc) => (qd !== cd ? 4 : qacc === cacc ? 0 : qacc === 0 ? 1 : cacc === 0 ? 2 : 3);
  const bestOn = (list) => {
    const qs = raw.replace(/\s/g, '');
    const qq = parseQuery(qs);
    let k = 0;
    for (let i = 0; i < qq.length; i++) k += cost(48 + qq[i].d, qq[i].acc, 48 + qq[i].d, 0) === 0 ? 0 : 0;
    return list.length;
  };
  const t3 = performance.now();
  bestOn(cand);
  pruneSearchTimes.push(performance.now() - t3);
  console.log(`  ${same ? '✓' : '✗'} ${raw.padEnd(14)} 候选 ${String(cand.length).padStart(5)} / 真值 ${String(truth.length).padStart(5)}`
              + `  (占全库 ${(cand.length / songs.length * 100).toFixed(2)}%)`);
}
console.log(bad ? `**剪枝漏了 ${bad} 条**` : '剪枝正确性 ✓（候选集与全库代价 0 集合完全一致）');
const med = (xs) => { const s = [...xs].sort((a, b) => a - b); return s.length ? s[s.length >> 1] : 0; };
console.log(`\n③ 剪枝耗时中位 ${med(candTimes).toFixed(2)} ms · "全库 p.includes" 中位 ${med(fullZeroTimes).toFixed(2)} ms`);
console.log(`   （正式做法是剪枝后用精确匹配（代价表允许变音/模糊）再算一遍 —— 这里只验"候选集对不对"）`);

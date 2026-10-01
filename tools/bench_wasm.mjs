// node tools/bench_wasm.mjs [查询数] [wasm路径]
//
// E 阶段的**端到端性能对照**：同一批查询，`search()` 走"wasm 表快路径" vs "纯 TS 全扫"。
//
// 为什么要单独一个基准而不是改 bench_search.mjs:
//   `bench_search.mjs` 是**线上口径**的基线（不依赖任何额外产物，CI 里也跑）；
//   这个脚本依赖跨仓库的 wasm，找不到就跳过 —— 两者用途不同，别混。
//
// 报三段时间:
//   ① wasm 造表（每段一次全库扫）
//   ② 表快路径的 search()（含上面那段造表）
//   ③ 纯 TS 全扫的 search()
// 以及"只算匹配、不算裁决/渲染"的那部分（最贴近 E 阶段能省下的东西）。
import { readFileSync, existsSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { importStatic } from './_built.mjs';

const N = Number(process.argv[2] || 30);
const WASM = process.argv[3]
  || 'D:/Documents_D/jianpu2/wasm-matcher/target/wasm32-unknown-unknown/release/jianpu_matcher.wasm';
if (!existsSync(WASM)) {
  console.log(`跳过：找不到 wasm（${WASM}）`);
  console.log('  先编译: cd jianpu2/wasm-matcher && cargo build --release --target wasm32-unknown-unknown');
  process.exit(0);
}

const { buildIndex, search } = await importStatic('search');
const { parseQuery } = await importStatic('jptok');
const idx = buildIndex(gunzipSync(readFileSync(new URL('../data/songs.jsonl.gz', import.meta.url))).toString('utf8'));

// ── 语料数组（与线上同一份索引）──────────────────────────────────────────────
const songs = idx.songs;
const off = new Uint32Array(songs.length + 1);
let total = 0;
for (let i = 0; i < songs.length; i++) { off[i] = total; total += songs[i].p.length; }
off[songs.length] = total;
const P = new Uint8Array(total), A = new Int8Array(total);
{
  let w = 0;
  for (const s of songs) {
    for (let k = 0; k < s.p.length; k++) {
      P[w] = s.p.charCodeAt(k);
      A[w] = s.a.charCodeAt(k) === 49 ? 1 : s.a.charCodeAt(k) === 50 ? -1 : 0;
      w++;
    }
  }
}
const { instance } = await WebAssembly.instantiate(readFileSync(WASM), {});
const ex = instance.exports;
const pPtr = ex.jp_alloc(total), aPtr = ex.jp_alloc(total), oPtr = ex.jp_alloc(off.length * 4);
new Uint8Array(ex.memory.buffer, pPtr, total).set(P);
new Int8Array(ex.memory.buffer, aPtr, total).set(A);
new Uint32Array(ex.memory.buffer, oPtr, off.length).set(off);
ex.jp_set_corpus(pPtr, aPtr, oPtr, songs.length);
const outPtr = ex.jp_alloc(songs.length * 8);

const QUERIES = [
  '6 3 7 3 1 2 3 2', '5 6 5 3 2 1 2 3 5 6 5 3', '3 3 5 6 5 3 2 5 3 2 1 2 3',
  '1 1 5 5 6 6 5 4 4 3 3 2 2 1', '6 7 1 7 6 5 6 3 2 1 2 3 5 6',
  '55532235 3211612655', '66165535 532322 7656',
].map((q) => q.split(/[;；|、+，,]+/).map(parseQuery).filter((s) => s.length >= 5));

const first = (xs) => xs[0];
const median = (xs) => { const s = [...xs].sort((a, b) => a - b); return s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2; };

function tableFor(segs) {
  return segs.map((q) => {
    const qd = Uint8Array.from(q.map((x) => 48 + x.d));
    const qa = Int8Array.from(q.map((x) => x.acc));
    const qp = ex.jp_alloc(qd.length), ap = ex.jp_alloc(qa.length);
    new Uint8Array(ex.memory.buffer, qp, qd.length).set(qd);
    new Int8Array(ex.memory.buffer, ap, qa.length).set(qa);
    ex.jp_scan_all(qp, ap, qd.length, outPtr);
    const costs = new Uint32Array(songs.length), ats = new Uint32Array(songs.length);
    const rawOut = new Uint32Array(ex.memory.buffer, outPtr, songs.length * 2);
    for (let i = 0; i < songs.length; i++) { costs[i] = rawOut[i * 2]; ats[i] = rawOut[i * 2 + 1]; }
    return { costs, ats };
  });
}

const tScanOnly = [], tCopy = [], tSearchWithTable = [], tFast = [], tTs = [], tGroups = [];
for (let i = 0; i < N; i++) {
  const segs = QUERIES[i % QUERIES.length];
  // ① 造表: 拆成"wasm 扫"与"拷进 JS 数组"两段（拷 92 KB/段，别把它算到 wasm 头上）
  let t = performance.now();
  const tables = [];
  for (const q of segs) {
    const qd = Uint8Array.from(q.map((x) => 48 + x.d));
    const qa = Int8Array.from(q.map((x) => x.acc));
    const qp = ex.jp_alloc(qd.length), ap = ex.jp_alloc(qa.length);
    new Uint8Array(ex.memory.buffer, qp, qd.length).set(qd);
    new Int8Array(ex.memory.buffer, ap, qa.length).set(qa);
    const t0 = performance.now();
    ex.jp_scan_all(qp, ap, qd.length, outPtr);
    tScanOnly.push(performance.now() - t0);
    const t1 = performance.now();
    const costs = new Uint32Array(songs.length), ats = new Uint32Array(songs.length);
    const rawOut = new Uint32Array(ex.memory.buffer, outPtr, songs.length * 2);
    for (let k = 0; k < songs.length; k++) { costs[k] = rawOut[k * 2]; ats[k] = rawOut[k * 2 + 1]; }
    tCopy.push(performance.now() - t1);
    tables.push({ costs, ats });
  }
  const tBuild = performance.now() - t;
  t = performance.now();
  search(idx, segs, { top: 10, scan: tables });
  tSearchWithTable.push(performance.now() - t);
  tFast.push(performance.now() - t + tBuild);
  t = performance.now();
  search(idx, segs, { top: 10 });
  tTs.push(performance.now() - t);
  // 对照组: 只做"组/成员遍历"(不滑窗)，看这块固定开销有多大
  t = performance.now();
  let acc = 0;
  for (const [, members] of idx.groups) for (const s of members) acc += (s.i & 1);
  tGroups.push(performance.now() - t);
}
console.log(`  ①a wasm 扫（每段）        中位 ${median(tScanOnly).toFixed(1)} ms`);
console.log(`  ①b 拷回 JS 数组（92 KB/段）中位 ${median(tCopy).toFixed(1)} ms`);
console.log(`  ②a 表快路径 search() 本体  中位 ${median(tSearchWithTable).toFixed(1)} ms`);
console.log(`  ②b 表快路径 端到端(含造表) 中位 ${median(tFast).toFixed(1)} ms`);
console.log(`  ③ 纯 TS 全扫 search()      中位 ${median(tTs).toFixed(1)} ms`);
console.log(`  ④ 只遍历 groups/members    中位 ${median(tGroups).toFixed(1)} ms  ← 这块是**固定开销**，与匹配无关`);
console.log(`语料 ${idx.count} 首 · 查询 ${N} 次（${QUERIES.length} 条轮换）`);
const speed = median(tTs) / median(tFast);
console.log(`  端到端加速: **${speed.toFixed(2)}×**（③/②）`);
console.log(`
  ⚠ 这里**故意不报**"冷启动 vs 热身后": 基准是 ${QUERIES.length} 条查询轮换，第 1 次用的是 query[0]，
  而"中位数"掺了更长的查询 —— 两列不可比（我第一版就这么报了，看起来像"wasm 热身后变慢"）。
  要比就按**同一条查询**分组比首轮与后续；真要做结论时再单独量。`);

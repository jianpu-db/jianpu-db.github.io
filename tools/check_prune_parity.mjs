// node tools/check_prune_parity.mjs [查询数]
//
// **ngram 剪枝的等价性对拍**：同一批查询，`idx.grams` 关（全扫）与开（剪枝）的**最终卡片必须逐条相同**。
//
// 查询集刻意分两类:
//   ① **语料派生**（从某首歌里切一段当查询）—— 这是真实用法（用户哼的是库里某首歌的片段），
//      也是剪枝该生效的场景；
//   ② **模糊查询**（派生出来再改几个音）—— 剪枝不敢走，必须**退回全扫**并给出与全扫一致的结果。
//
// 安全性论证写在 `static/search.ts` 的剪枝注释里（代价 0 的歌必然在候选集里 -> 并列裁决那批一个不少；
// 且只有"代价 0 的结果够填满榜单"时才采用剪枝结果）。
import { readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { importStatic } from './_built.mjs';

const N = Number(process.argv[2] || 60);
const { buildIndex, search, ensureGrams } = await importStatic('search');
const { parseQuery } = await importStatic('jptok');
const idx = buildIndex(gunzipSync(readFileSync(new URL('../data/songs.jsonl.gz', import.meta.url))).toString('utf8'));
const songs = idx.songs;

// 先算一遍"全扫"的结果（此时 grams 还没建）
const key = (r) => r.map((x) => `${x.group}|${x.cost}|${x.at}|${x.exact}|${x.sec}`).join(' ;; ');

let seed = 20261002;
const rnd = (n) => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed % n; };
const LENS = [5, 7, 9, 11, 15];
const cases = [];   // {segs, fuzzy}
for (let i = 0; i < N; i++) {
  const s = songs[rnd(songs.length)];
  const len = LENS[rnd(LENS.length)];
  if (s.p.length < len) continue;
  const at = rnd(s.p.length - len + 1);
  let q = '';
  for (let k = 0; k < len; k++) {
    const ch = s.a.charCodeAt(at + k);
    q += (ch === 49 ? '#' : ch === 50 ? 'b' : '') + s.p[at + k];
  }
  // ② 三分之一改成"模糊"：把一个音改掉（剪枝应当退回全扫）
  if (i % 3 === 0) {
    const pos = rnd(q.length);
    const repl = '1234567'[rnd(7)];
    q = q.slice(0, pos) + repl + q.slice(pos + 1);
  }
  // 少量多段
  const segs = (i % 7 === 0 && q.length > 10)
    ? [q.slice(0, Math.floor(q.length / 2)), q.slice(Math.floor(q.length / 2))]
    : [q];
  cases.push({ segs: segs.map(parseQuery).filter((x) => x.length >= 5), fuzzy: i % 3 === 0 });
}

// ── 全扫（grams 关闭）────────────────────────────────────────────────────────
const t0 = Date.now();
const beforeMs = [], afterMs = [];
const before = cases.map((c) => {
  const s0 = Date.now();
  const v = c.segs.length ? key(search(idx, c.segs, { top: 10 })) : '';
  beforeMs.push(Date.now() - s0);
  return v;
});
const tFull = Date.now() - t0;

// ── 建 grams（这一步实测 ~165 ms，浏览器里该在空闲时做）─────────────────────
const tg = Date.now();
ensureGrams(idx, 4);
const tGrams = Date.now() - tg;

// ── 剪枝（grams 打开）────────────────────────────────────────────────────────
const t1 = Date.now();
const after = cases.map((c) => {
  const s0 = Date.now();
  const v = c.segs.length ? key(search(idx, c.segs, { top: 10 })) : '';
  afterMs.push(Date.now() - s0);
  return v;
});
const tPruned = Date.now() - t1;

let bad = 0;
for (let i = 0; i < cases.length; i++) {
  if (before[i] !== after[i]) {
    bad++;
    if (bad <= 3) {
      console.log(`  ✗ 第 ${i} 条: ${cases[i].map((q) => q.length).join('+')} 音`);
      console.log(`     全扫: ${before[i].slice(0, 150)}`);
      console.log(`     剪枝: ${after[i].slice(0, 150)}`);
    }
  }
}
// 分开报: 精确查询（剪枝该生效）与模糊查询（必须退回全扫）—— 混在一起平均会把效果冲淡
console.log(`查询 ${cases.length} 条 · 建 grams ${tGrams} ms`);
for (const [label, want] of [['精确命中', false], ['模糊（应退回全扫）', true]]) {
  const idxs = cases.map((c, i) => (c.fuzzy === want ? i : -1)).filter((i) => i >= 0);
  if (!idxs.length) continue;
  const sum = (arr) => idxs.reduce((a, i) => a + arr[i], 0);
  console.log(`  ${label.padEnd(20)} ${String(idxs.length).padStart(3)} 条 · 全扫 ${(sum(beforeMs) / idxs.length).toFixed(1)} ms/条`
              + ` -> 剪枝 ${(sum(afterMs) / idxs.length).toFixed(1)} ms/条`);
}
console.log(bad
  ? `**结果不一致 ${bad} 条** —— 剪枝不能上线`
  : `结果**逐条相同** ✓（${cases.length} 条） · 每条省 ${((tFull - tPruned) / Math.max(cases.length, 1)).toFixed(1)} ms`);
process.exit(bad ? 1 : 0);

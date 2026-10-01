// node tools/bench_search.mjs [查询条数] —— 量"浏览器里检索"的真实耗时（简历/徽章要用的数字）
//
// 量三件事，分开报（面试会追问"这个数是什么条件下的"）:
//   ① **索引构建**：解 gzip + JSON 解析 + 建倒排/加权结构（每次打开页面都要做一次）
//   ② **单次查询中位**：用前端**同一份** static/search.js + static/jptok.js（不是另写一份）
//   ③ 召回规模：返回了多少条、第一条是什么
//
// 用法: node tools/bench_search.mjs 30
import { readFileSync } from 'node:fs';
import { importStatic } from './_built.mjs';
const { buildIndex, search, ensureGrams } = await importStatic('search');
const { parseQuery } = await importStatic('jptok');
import { gunzipSync } from 'node:zlib';

const N = Number(process.argv[2] || 20);
const QUERIES = [
  '6 3 7 3 1 2 3 2',            // U.N.オーエン 那句
  '5 6 5 3 2 1 2 3 5 6 5 3',
  '3 3 5 6 5 3 2 5 3 2 1 2 3',
  '1 1 5 5 6 6 5 4 4 3 3 2 2 1',
  '6 7 1 7 6 5 6 3 2 1 2 3 5 6',
];

function median(xs) {
  const s = [...xs].sort((a, b) => a - b);
  return s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2;
}

const t0 = performance.now();
const raw = gunzipSync(readFileSync(new URL('../data/songs.jsonl.gz', import.meta.url)));
const t1 = performance.now();
const idx = buildIndex(raw.toString('utf8'));
const t2 = performance.now();
// 线上（app.ts）会在**空闲时**建 ngram 倒排给剪枝用 —— 基准也照做，否则量的是"没有剪枝"的口径。
// 建索引的耗时单独报（它会加到页面的"就绪时间"上，不能藏进查询时间里）。
const tG = performance.now();
ensureGrams(idx, 4);
const tGrams = performance.now() - tG;

const segSets = QUERIES.map((q) => q.split(/[;；|、+，,]+/).map(parseQuery).filter((s) => s.length >= 5));
const times = [];
let hits = 0, top = '';
for (let i = 0; i < N; i++) {
  const segs = segSets[i % segSets.length];
  const s = performance.now();
  const res = search(idx, segs, { top: 5 });
  times.push(performance.now() - s);
  hits = res.length;
  top = res[0] ? (res[0].title || res[0].t || '') : '';
}

console.log(`索引：${(raw.length / 1e6).toFixed(2)} MB 明文 · gzip 解压 ${(t1 - t0).toFixed(0)} ms · ` +
            `buildIndex ${(t2 - t1).toFixed(0)} ms · ngram 倒排 ${tGrams.toFixed(0)} ms · 共 ${(t2 - t0 + tGrams).toFixed(0)} ms（${idx.songs.length} 首）`);
console.log(`查询：${N} 次 · 中位 ${median(times).toFixed(1)} ms · p90 ${[...times].sort((a, b) => a - b)[Math.floor(N * 0.9)].toFixed(1)} ms · ` +
            `最快 ${Math.min(...times).toFixed(1)} ms`);
console.log(`召回：最后一次返回 ${hits} 条 · 第一条「${top}」`);

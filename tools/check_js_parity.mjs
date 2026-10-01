// node tools/check_js_parity.mjs <dump.tsv> —— **前端 JS 与 Python 侧的等价性测试**
//
// 为什么需要: 报告的榜单指标出自 Python 侧的 `lookup.py`，而用户实际在浏览器里跑的是
// `static/search.js` —— 同一套代价模型**两份实现**（和 jptok 那两份一个道理）。
// 这个脚本把 Python 侧**真的用过的查询片段**（`eval_golden.py --dump-queries` 导出）
// 在 JS 侧重放一遍，逐条比对 Top-1 曲名组是否一致。
//
// 用法:
//   python3 jianpu2/skills/jianpu-melody-lookup/eval_golden.py \
//       --list jianpu2/train-work/eval_set_kugou_hualiu_2025.tsv --lens 15 --errs 0 \
//       --dump-queries /tmp/queries.tsv
//   node tools/check_js_parity.mjs /tmp/queries.tsv
import { readFileSync } from 'node:fs';
import { importStatic } from './_built.mjs';
const { buildIndex, search } = await importStatic('search');
const { parseQuery } = await importStatic('jptok');
import { gunzipSync } from 'node:zlib';

const dump = process.argv[2];
const limit = process.argv.includes('--limit') ? Number(process.argv[process.argv.indexOf('--limit') + 1]) : 0;
if (!dump) {
  console.error('用法: node tools/check_js_parity.mjs <dump.tsv> [--limit N]');
  process.exit(2);
}
const gz = readFileSync(new URL('../data/songs.jsonl.gz', import.meta.url));
const t0 = Date.now();
const idx = buildIndex(gunzipSync(gz).toString('utf8'));
console.log(`索引 ${idx.count} 首 / ${idx.groupCount} 组（${Date.now() - t0} ms）`);

let rows = readFileSync(dump, 'utf8').split('\n').filter((l) => l.trim() && !l.startsWith('list\t'));
if (limit) rows = rows.slice(0, limit);
let n = 0, same1 = 0, inTop3 = 0, inTop5 = 0;
const diff = [];
for (const ln of rows) {
  const [list, query, expect] = ln.split('\t');
  const segs = [parseQuery(query)].filter((s) => s.length >= 5);
  if (!segs.length) continue;
  const res = search(idx, segs, { top: 5 });
  if (!res.length) { diff.push([query, expect, '(无结果)', '']); n++; continue; }
  n++;
  if (res[0].group === expect) same1++;
  if (res.slice(0, 3).some((r) => r.group === expect)) inTop3++;
  if (res.slice(0, 5).some((r) => r.group === expect)) inTop5++;
  if (res[0].group !== expect) diff.push([query, expect, res[0].group, res[0].cost]);
}
const pct = (x) => `${x}/${n} = ${(x / n * 100).toFixed(1)}%`;
console.log(`\nJS 侧重放 ${n} 条(Python 侧导出的真实查询):`);
console.log(`  Top-1 曲名与本侧期望**完全相同**: ${pct(same1)}`);
console.log(`  期望曲名进 Top-3 / Top-5: ${pct(inTop3)} / ${pct(inTop5)}`);
if (diff.length) {
  console.log('\n注: 期望值是查询**源谱**的曲名组; Python 侧的命中判定是"包含"口径(>=4 字且占一半以上),');
  console.log('    所以"找到同一首歌的另一个版本"在这里会显示成不同组, 但两边其实都算命中/都算未命中。\n');
  console.log(`不一致的 ${diff.length} 条:`);
  for (const [q, want, got, cost] of diff.slice(0, 15)) {
    console.log(`  ${q}\n    期望 ${want}\n    JS 得到 ${got}${cost !== '' ? `（代价 ${cost}）` : ''}`);
  }
} else {
  console.log('\n逐条一致 ✓ —— 报告的指标和用户在浏览器里跑的是同一件事');
}
process.exitCode = 0;   // 不一致也先以报告呈现(指标差多少要人看), 退出码留给以后接 CI

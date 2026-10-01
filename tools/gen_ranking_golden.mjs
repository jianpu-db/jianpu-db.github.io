// node tools/gen_ranking_golden.mjs [数量]
//
// **生成**金标准用例（写进 `check_ranking_golden.mjs` 用）—— 一次性工具，评测时不需要它。
//
// 用例怎么选才"客观"（这一步很关键）:
//   查询 = **从某首歌自己的谱里切一段** -> 正确答案**必然是那首歌**（不是"把当前行为烤进去"）。
//   同一首歌的其它版本也算对（按归组名判定，与离线评测口径一致）。
//   挑"有区分度"的: 该查询在全库里恰好只命中这一组（同代价组数越少越稳）。
import { readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { importStatic } from './_built.mjs';

const WANT = Number(process.argv[2] || 12);
const { buildIndex, search, ensureGrams } = await importStatic('search');
const { parseQuery } = await importStatic('jptok');
const idx = buildIndex(gunzipSync(readFileSync(new URL('../data/songs.jsonl.gz', import.meta.url))).toString('utf8'));
ensureGrams(idx, 4);

const SKIP = /吉他|钢琴|尤克里里|卡林姆|卡林巴|拇指琴|双谱|器乐|伴奏|指弹|弹唱/;
const LENS = [11, 13, 15];
let seed = 20261003;
const rnd = (n) => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed % n; };

const groups = [...idx.groups.keys()];
const cases = [];
let tries = 0;
while (cases.length < WANT && tries < 4000) {
  tries++;
  const name = groups[rnd(groups.length)];
  const members = (idx.groups.get(name) || []).filter((s) => s.p.length >= 15 && !SKIP.test(s.raw || '') && !SKIP.test(s.title || ''));
  if (!members.length) continue;
  const src = members[rnd(members.length)];
  const L = LENS[rnd(LENS.length)];
  const at = rnd(src.p.length - L + 1);
  let q = '';
  for (let k = 0; k < L; k++) {
    const ch = src.a.charCodeAt(at + k);
    q += (ch === 49 ? '#' : ch === 50 ? 'b' : '') + src.p[at + k];
  }
  const qs = q.replace(/\s+/g, '');
  if (cases.some((c) => c.q.includes(qs) || qs.includes(c.q))) continue;   // 别选到互相包含的片段
  const parsed = parseQuery(qs);
  if (parsed.length !== L) continue;
  const res = search(idx, [parsed], { top: 60 });
  if (!res.length) continue;
  const minCost = res[0].cost;
  const tied = res.filter((r) => r.cost === minCost).map((r) => r.group);
  // 只收"命中目标、且并列少"的用例（并列多则名次不稳，不适合当门槛）
  if (!tied.includes(name) || tied.length > 2 || minCost !== 0) continue;
  const rank = res.findIndex((r) => r.group === name) + 1;
  if (rank !== 1) continue;
  cases.push({ q: qs, L, group: name, cost: minCost, tied: tied.length });
}

console.log(`// 自动生成（tools/gen_ranking_golden.mjs）—— ${cases.length} 条，全部满足:`);
console.log('//   * 查询取自目标歌自己的谱（正确答案客观，不是"烤现状"）');
console.log('//   * 目标在当前实现下 Top-1 且同代价并列 ≤ 2（名次稳定，适合当年门槛）');
console.log('export const GOLDEN = [');
for (const c of cases) {
  console.log(`  { q: '${c.q}', L: ${c.L}, group: '${c.group}', cost: ${c.cost}, tied: ${c.tied} },`);
}
console.log('];');

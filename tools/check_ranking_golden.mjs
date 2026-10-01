// node tools/check_ranking_golden.mjs
//
// **排名金标准门槛**（CI 可跑，不依赖工具仓库）。
//
// 为什么必须有它: CI 里之前只有功能检查（页面/深链/元数据/剪枝等价……）—— **没有任何"排名质量"的门槛**。
// 谁把代价表、并列裁决或剪枝改坏了，CI 照样绿。这个门槛填的正是这个洞。
//
// 用例为什么**客观**（不是"把当前行为烤进去"）:
//   每条查询都是**从目标歌自己的谱里切下来的一段** -> 正确答案必然是那首歌，与实现无关。
//   生成时还筛过: 同代价并列 ≤ 2（名次稳定）、当前 Top-1、代价 0。
//   生成器在 `tools/gen_ranking_golden.mjs`（语料更新后可重新生成）。
//
// 断言三条（都对着"正确答案"而不是"当前输出"）:
//   ① 目标组必须出现在结果里；② 它的代价必须是最小代价（这里都是 0）；③ 它必须在前三名。
import { readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { importStatic } from './_built.mjs';

const { buildIndex, search, ensureGrams } = await importStatic('search');
const { parseQuery } = await importStatic('jptok');

// 由 tools/gen_ranking_golden.mjs 生成（查询取自目标歌自己的谱）
const GOLDEN = [
  { q: '653561767565365', L: 15, group: '这里的冬天也花开', cost: 0, tied: 1 },
  { q: '6631162335377', L: 13, group: '一百年', cost: 0, tied: 1 },
  { q: '66636163336', L: 11, group: '湘潭，红色摇篮', cost: 0, tied: 1 },
  { q: '56552511231', L: 11, group: '锦绣中华', cost: 0, tied: 1 },
  { q: '7652557652555', L: 13, group: '拥抱大海', cost: 0, tied: 1 },
  { q: '32317651114', L: 11, group: '春姑娘', cost: 0, tied: 1 },
  { q: '611322121162632', L: 15, group: '把他换作你', cost: 0, tied: 1 },
  { q: '655555653336656', L: 15, group: '爱上广场舞', cost: 0, tied: 1 },
  { q: '736366256322351', L: 15, group: '刀郎', cost: 0, tied: 1 },
  { q: '11411665653', L: 11, group: '秦淮河', cost: 0, tied: 1 },
  { q: '355565333665622', L: 15, group: '望月', cost: 0, tied: 2 },
  { q: '55443256222', L: 11, group: '黄河牵着我的手', cost: 0, tied: 1 },
];

const idx = buildIndex(gunzipSync(readFileSync(new URL('../data/songs.jsonl.gz', import.meta.url))).toString('utf8'));
ensureGrams(idx, 4);          // 门槛要覆盖**剪枝路径**（线上会建，这里也建）

let fail = 0;
const rows = [];
for (const g of GOLDEN) {
  const q = parseQuery(g.q);
  const errs = [];
  if (q.length !== g.L) errs.push(`解析出 ${q.length} 音，期望 ${g.L}`);
  const res = search(idx, [q], { top: 60 });
  const hit = res.findIndex((r) => r.group === g.group);
  if (hit < 0) errs.push('目标组不在结果里（漏召回）');
  else {
    if (res[hit].cost !== g.cost) errs.push(`代价 ${res[hit].cost}，期望 ${g.cost}`);
    if (hit + 1 > 3) errs.push(`名次 ${hit + 1}，要求 ≤3`);
    // 同代价并列数（信息性: 与生成时相比变大说明语料/口径变了，值得看一眼）
    const tiedNow = res.filter((r) => r.cost === res[0].cost).length;
    if (tiedNow > g.tied + 2) errs.push(`同代价并列 ${tiedNow} 组（生成时 ${g.tied}）`);
  }
  if (errs.length) { fail++; rows.push(`  ✗ ${g.q.padEnd(18)} L=${g.L} ${g.group} —— ${errs.join('；')}`); }
  else rows.push(`  ✓ ${g.q.padEnd(18)} L=${g.L} Top-${hit + 1} 代价 ${res[hit].cost}  ${g.group}`);
}

console.log(`排名金标准 ${GOLDEN.length} 条（查询取自目标歌自己的谱；断言=命中+代价+前三名）`);
for (const r of rows) console.log(r);
console.log(`\n${fail === 0 ? '通过' : '失败 ' + fail + ' 条'} —— 排名质量门槛`);
process.exit(fail ? 1 : 0);

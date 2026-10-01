// node tools/check_dup_melody.mjs
//
// **精确重复旋律**（同一段音级 + 变音序列出现在多首/多个 source 下）。
//
// 为什么做这个: 语料是多次爬取 + 多次转写攒起来的，同一个 source 可能被转过两遍、
// 同一首歌可能在不同 source 下各有一份。文档里提过"888 个 source 有 1,835 份成品"——
// 这里把**还剩多少重复**量出来（不是估）。
//
// 判据: 对每首歌的 (音级串, 变音串) 做**精确**指纹并分组。
//   * 只报"完全相同"的 —— 移调（同曲不同调）不算，那是另一件事，得先做调性归一才敢说；
//   * 分两类看: ① **同曲名不同来源**（大概率是真重复稿）；② **不同曲名**（可能是转写/标题错，也可能是巧合）。
import { readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { createHash } from 'node:crypto';
import { writeFileSync } from 'node:fs';

const raw = gunzipSync(readFileSync(new URL('../data/songs.jsonl.gz', import.meta.url))).toString('utf8');
const rows = raw.split('\n').filter(Boolean).map((l) => JSON.parse(l));
console.log(`站点索引 ${rows.length} 行`);

const fp = (r) => createHash('sha1').update(r.p + '|' + (r.a || '')).digest('hex').slice(0, 16);
const byFp = new Map();
for (const r of rows) {
  const k = fp(r);
  if (!byFp.has(k)) byFp.set(k, []);
  byFp.get(k).push(r);
}

let dupGroups = 0, sameTitle = 0, diffTitle = 0, relatedTitle = 0, rowsIn = 0;
const examplesA = [], examplesB = [], examplesC = [];
for (const [, v] of byFp) {
  if (v.length < 2) continue;
  dupGroups++;
  rowsIn += v.length;
  // 同一首歌在不同 source 下重复（曲名相同）
  const titles = new Set(v.map((x) => x.g));
  const sources = new Set(v.map((x) => x.s || ''));
  if (titles.size === 1 && sources.size > 1) {
    sameTitle++;
    if (examplesA.length < 8) examplesA.push([...v].map((x) => `${x.g}[${x.s || '无源'}]`).join('  ==  '));
  } else if (titles.size > 1) {
    diffTitle++;
    // 再分两类: 标题"互相包含"（多半是爬虫把速度/情绪标注拼进了曲名）vs 完全无关（可能是标题错或巧合）
    const ts = [...titles];
    const related = ts.some((a) => ts.some((b) => a !== b && (a.includes(b) || b.includes(a))));
    if (related) {
      relatedTitle++;
      if (examplesC.length < 8) examplesC.push([...v].map((x) => `${x.g}`).join('  ==  '));
    } else if (examplesB.length < 8) {
      examplesB.push([...v].map((x) => `${x.g}[${x.s || '无源'}]`).join('  ==  '));
    }
  }
}
console.log(`  不同指纹（= 不同旋律）: ${byFp.size}`);
console.log(`  有重复的指纹: ${dupGroups}  ·  牵涉 ${rowsIn} 行（占 ${(rowsIn / rows.length * 100).toFixed(1)}%）`);
console.log(`    ① 同曲名 / 不同来源（真重复稿）: ${sameTitle}`);
for (const e of examplesA) console.log(`        ${e}`);
console.log(`    ② 不同曲名但旋律完全相同: ${diffTitle}`);
console.log(`       ②a 其中标题**互相包含**（爬虫把速度/情绪标注拼进曲名）: ${relatedTitle}`);
for (const e of examplesC) console.log(`           ${e}`);
console.log(`       ②b 标题毫无关系（可能是标题错，也可能是巧合）: ${diffTitle - relatedTitle}`);
for (const e of examplesB) console.log(`           ${e}`);

const out = process.argv[2];
if (out) {
  // 分类写进文件（给 jp_refine_titles / jp_tidy_titles 那两个任务用），不只写一行摘要
  const cls = (v) => {
    const ts = [...new Set(v.map((x) => x.g))];
    if (ts.length === 1) return '同曲名/不同源';
    const related = ts.some((a) => ts.some((b) => a !== b && (a.includes(b) || b.includes(a))));
    return related ? '标题互相包含(疑标注拼进曲名)' : '标题无关(疑占位名/标题错)';
  };
  const buckets = new Map();
  for (const [k, v] of byFp) {
    if (v.length < 2) continue;
    const c = cls(v);
    if (!buckets.has(c)) buckets.set(c, []);
    buckets.get(c).push([k, v]);
  }
  const lines = ['# 精确重复旋律（音级+变音完全相同）',
    `# 重复指纹 ${dupGroups} 组 / 牵涉 ${rowsIn} 行；同曲名不同源 ${sameTitle}；不同曲名 ${diffTitle}（标题互相包含 ${relatedTitle}）`,
    '# 用法: 「标题互相包含」与「标题无关」两类喂给 jp_refine_titles / jp_tidy_titles 做标题归一化；',
    '#       「同曲名/不同源」是重复稿，排名时"按 source 去重版本"那条已经把它按一份算。'];
  for (const [c, arr] of [...buckets.entries()].sort((a, b) => b[1].length - a[1].length)) {
    lines.push('', `## ${c}（${arr.length} 组）`, '# 指纹\t曲名[source] …');
    for (const [k, v] of arr) lines.push(`${k}\t${v.map((x) => `${x.g}[${x.s || '无源'}]`).join(' | ')}`);
  }
  writeFileSync(out, lines.join('\n') + '\n', 'utf8');
  console.log(`\n  明细已写: ${out}`);
}
console.log(dupGroups
  ? '\n  -> 这些是**存储/展示层面**的重复: 同一段旋律出现多次，命中的并列裁决会被灌水（"版本多优先"）。'
  : '\n  -> 没有精确重复 ✓');

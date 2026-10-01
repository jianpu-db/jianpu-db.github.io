// node tools/_prototype_group_merge.mjs [--apply-json 路径]
//
// **归组口径合并的原型验证**（不改任何线上文件）: 在内存里把站点索引的 `g` 归一化
// （大小写不敏感 + 去标点/下划线/空格 + 剥站名后缀），然后对比归一化前后的检索结果。
//
// 要回答三件事:
//   ① 合并了多少组（46 -> 23 是 check_dup_groups.mjs 的说法，这里在**构建后**的索引上复核）；
//   ② 同名的重复卡片是否真的合并（"Amani" 这类查询原来出两个组）；
//   ③ 除了合并，"其他查询的结果有没有被顺手改掉"（不该改 —— 归一化只动组名，不动音符）。
import { readFileSync, writeFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { importStatic } from './_built.mjs';

const { buildIndex, search } = await importStatic('search');
const { parseQuery } = await importStatic('jptok');

const raw = gunzipSync(readFileSync(new URL('../data/songs.jsonl.gz', import.meta.url))).toString('utf8');
const lines = raw.split('\n').filter(Boolean);
const rows = lines.map((l) => JSON.parse(l));

const JUNK = ['简谱', '歌谱', '五线谱', '正谱', '完整版', '弹唱', '吉他谱', '钢琴谱', '歌曲类', '简和谱'];
const norm = (s) => {
  let t = s;
  for (const j of JUNK) t = t.split(j).join('');
  return t.replace(/[\s_\-–—·、,，.。()（）【】\[\]]+/g, '').toLowerCase();
};

// 显示名: 取该归一化键下**出现次数最多**的原始写法（并列时取更短的）
const byKey = new Map();
for (const r of rows) {
  const k = norm(r.g) || r.g;
  if (!byKey.has(k)) byKey.set(k, new Map());
  const m = byKey.get(k);
  m.set(r.g, (m.get(r.g) || 0) + 1);
}
const display = new Map();
for (const [k, m] of byKey) {
  display.set(k, [...m.entries()].sort((a, b) => b[1] - a[1] || a[0].length - b[0].length)[0][0]);
}

const before = buildIndex(lines.join('\n'));
const merged = rows.map((r, i) => { const o = { ...r }; o.g = display.get(norm(r.g) || r.g) || r.g; return JSON.stringify(o); });
const after = buildIndex(merged.join('\n'));

console.log(`组数: 归一化前 ${before.groups.size} -> 后 ${after.groups.size}（少 ${before.groups.size - after.groups.size}）`);
console.log(`曲数不变: ${before.count} == ${after.count} ${before.count === after.count ? '✓' : '✗'}`);

const key = (r) => r.map((x) => `${x.group}|${x.cost}|${x.at}|${x.exact}`).join(' ;; ');

// ② 同名查询: 归一化后应该只出**一个**组（原来可能出两个）
const SAME = ['amani', 'love', '中国中国', 'k歌之王', '同一个世界同一个梦想'];
console.log('\n② 同名查询（归一化后应合并成一个组）');
for (const q of SAME) {
  const g = (idx) => {
    const segs = [parseQuery('11111111')];       // 占位: 用标题搜更直接, 这里直接查组名
    return [...idx.groups.keys()].filter((k) => norm(k) === norm(q) || k.toLowerCase() === q.toLowerCase());
  };
  const b = new Set(rows.map((r) => r.g).filter((k) => norm(k) === norm(q)));
  const a = new Set(rows.map((r) => display.get(norm(r.g) || r.g)).filter((k) => norm(k) === norm(q)));
  console.log(`  ${q.padEnd(12)} 原组 ${[...b].map((x) => `"${x}"`).join(' + ') || '(无)'}  ->  合并后 ${[...a].map((x) => `"${x}"`).join(' + ') || '(无)'}`);
}

// ③ 其他查询不该被改（只动组名）: 用旋律查询比对 **音符层面** 的结果
const QS = ['63731232', '55532235', '5111156711', '66165535', '16665', '5565345', '17123215'];
console.log('\n③ 旋律查询: 归一化只改组名 —— 代价/位置/精确计数必须一致（组名本身允许变）');
let bad = 0;
for (const q of QS) {
  const segs = [parseQuery(q)];
  const strip = (rs) => rs.map((x) => `${x.cost}|${x.at}|${x.exact}|${x.id}`).join(' ;; ');
  const b = strip(search(before, segs, { top: 10 }));
  const a = strip(search(after, segs, { top: 10 }));
  // id 是曲谱标识（不变），代价/位置/精确计数也不该变；组名单独看
  const bn = search(before, segs, { top: 10 }).map((x) => x.group).join(',');
  const an = search(after, segs, { top: 10 }).map((x) => x.group).join(',');
  const same = b === a;
  if (!same) bad++;
  console.log(`  ${same ? '✓' : '✗'} ${q.padEnd(12)} 组名 ${bn === an ? '未变' : '有变（合并所致）'}`);
}

const outPath = process.argv[2];
if (outPath) {
  const out = rows.map((r, i) => ({ ...r, g: display.get(norm(r.g) || r.g) || r.g }));
  writeFileSync(outPath, out.map((o) => JSON.stringify(o)).join('\n') + '\n', 'utf8');
  console.log(`\n  合并后的索引数据已写: ${outPath}（**仅用于你审查**，没有动线上任何文件）`);
}
console.log(bad ? `\n**有 ${bad} 条旋律查询结果被改了** —— 归一化不该动音符层面，得查` : '\n旋律层面结果一致 ✓（归一化只动组名）');

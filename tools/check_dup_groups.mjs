// node tools/check_dup_groups.mjs
//
// **同名归组是否漏归**（用户可见）: 站点索引里 `g`（归组名）本该把"歌曲类/简谱/正谱…"这类站名后缀剥掉，
// 否则同一首歌会出现两个组 —— 搜索时看起来像两首歌，而且"版本多优先"那条并列依据也会被灌水。
//
// 判据不用猜: 直接在索引里找"剥掉后缀后相同、但归组名不同"的成对组。
import { readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { writeFileSync } from 'node:fs';

const raw = gunzipSync(readFileSync(new URL('../data/songs.jsonl.gz', import.meta.url))).toString('utf8');
const rows = raw.split('\n').filter(Boolean).map((l) => JSON.parse(l));
console.log(`站点索引 ${rows.length} 行`);

const JUNK = ['简谱', '歌谱', '五线谱', '正谱', '完整版', '弹唱', '吉他谱', '钢琴谱', '歌曲类', '简和谱'];
const strip = (s) => {
  let t = s;
  for (const j of JUNK) t = t.split(j).join('');
  return t.replace(/[\s_\-–—·、,，.。()（）【】\[\]]+/g, '').toLowerCase();
};

// ① 归组名里还剩多少"看起来没剥干净"的
const groups = new Map();                     // 原始归组名 -> 行数
for (const r of rows) groups.set(r.g, (groups.get(r.g) || 0) + 1);
let dirty = 0;
const dirtyList = [];
for (const [g, n] of groups) {
  if (JUNK.some((j) => g.includes(j))) { dirty++; if (dirtyList.length < 8) dirtyList.push(`${g} (${n} 行)`); }
}
console.log(`  归组名里仍含后缀词: ${dirty} / ${groups.size} 组`);
for (const d of dirtyList) console.log(`     ${d}`);

// ② 剥后缀后相同、但归组名不同的组（= 漏归）
const byStrip = new Map();
for (const [g, n] of groups) {
  const k = strip(g);
  if (!k) continue;
  if (!byStrip.has(k)) byStrip.set(k, []);
  byStrip.get(k).push([g, n]);
}
const dupes = [...byStrip.entries()].filter(([, v]) => v.length > 1)
  .sort((a, b) => b[1].length - a[1].length);
console.log(`\n  剥后缀后同名、但分成多个组的: ${dupes.length} 组名字（涉及 ${dupes.reduce((a, [, v]) => a + v.length, 0)} 个组）`);
for (const [k, v] of dupes.slice(0, 12)) {
  console.log(`     ${k.slice(0, 22).padEnd(24)} ${v.map(([g, n]) => `${g}(${n})`).join('  |  ')}`);
}
// 把合并映射写出来（给 build_web_data.py 的归组口径用）—— 由工具写，不手抄
const outPath = process.argv[2];
if (outPath) {
  const lines = ['# 归组名"剥后缀+归一化后同名"的组（%d 组名字 / %d 个组）'.replace('%d', dupes.length).replace('%d', dupes.reduce((a, [, v]) => a + v.length, 0)),
    '# 建议: build_web_data.py 的归组口径改为"大小写不敏感 + 去标点/下划线/空格"，并选出现次数最多的写法当显示名',
    '# 键\t现有组名(行数)'];
  for (const [k, v] of dupes) lines.push(`${k}\t${v.map(([g, n]) => `${g}(${n})`).join(' | ')}`);
  writeFileSync(outPath, lines.join('\n') + '\n', 'utf8');
  console.log(`  合并映射已写: ${outPath}`);
}
console.log(dupes.length
  ? '\n  -> 这些是**用户可见**的重复: 搜同一首歌会看到多个组。修法在 build_web_data.py 的归组口径（剥后缀 + 归一化）。'
  : '\n  -> 归组口径 ✓ 没有"剥后缀后同名却分成多个组"的情况。');
// 上面这段用 replace 会重复, 直接看文件尾部即可
process.exit(0);

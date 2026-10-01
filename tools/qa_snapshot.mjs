// node tools/qa_snapshot.mjs [--out 快照.json] [--compare 旧快照.json]
//
// **QA 快照 / 改动影响对比**：把"索引级"的质量指标一次算出来，存成 JSON；下次改动后再算一次，
// `--compare` 就能看出每个数字变了多少。
//
// 为什么需要它: 今晚量出的几个问题（归组重复、标题互含/占位名、重复旋律）都要**改语料/改构建**才能修，
// 而"改完到底好没好、有没有副作用"不能靠感觉 —— 需要**同一套口径的前后快照**。
// 典型用法:
//     node tools/qa_snapshot.mjs --out before.json
//     JP_GROUP_NORM=1 py -3.13 tools/build_web_data.py     # 改了什么
//     node tools/qa_snapshot.mjs --compare before.json
//
// 口径说明（与 check_dup_groups / check_dup_melody 保持一致，都只看站点索引）:
//   * groups                 —— 归组数（越少说明同名合并得越彻底）
//   * dupGroupNames          —— "剥后缀+去标点+转小写后同名、却分成多个组"的组名个数
//   * dupMelodyGroups        —— 精确重复旋律（音级+变音指纹相同）的组数
//   * dupMelodyRows          —— 这些重复涉及的行数
//   * titleContainsMelody    —— 标题互相包含的重复组（爬虫把标注拼进曲名那类）
//   * placeholderTitles      —— 占位名（未命名…/谱/改编歌曲…）而旋律非空的行数
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { createHash } from 'node:crypto';

const raw = gunzipSync(readFileSync(new URL('../data/songs.jsonl.gz', import.meta.url))).toString('utf8');
const rows = raw.split('\n').filter(Boolean).map((l) => JSON.parse(l));

const JUNK = ['简谱', '歌谱', '五线谱', '正谱', '完整版', '弹唱', '吉他谱', '钢琴谱', '歌曲类', '简和谱'];
const norm = (s) => {
  let t = s || '';
  for (const j of JUNK) t = t.split(j).join('');
  return t.replace(/[\s_\-–—·、,，.。()（）【】\[\]]+/g, '').toLowerCase();
};
const PLACEHOLDER = /^(未命名|无名|谱|歌曲|改编歌曲|纯音乐|Unknown|unknown|无|—+|-+)/;

const groups = new Set(rows.map((r) => r.g));
const byNorm = new Map();
for (const g of groups) {
  const k = norm(g);
  if (!k) continue;
  if (!byNorm.has(k)) byNorm.set(k, []);
  byNorm.get(k).push(g);
}
const dupGroupNames = [...byNorm.values()].filter((v) => v.length > 1).length;

const fp = (r) => createHash('sha1').update(r.p + '|' + (r.a || '')).digest('hex').slice(0, 16);
const byFp = new Map();
for (const r of rows) {
  const k = fp(r);
  if (!byFp.has(k)) byFp.set(k, []);
  byFp.get(k).push(r);
}
let dupMelodyGroups = 0, dupMelodyRows = 0, titleContainsMelody = 0;
for (const [, v] of byFp) {
  if (v.length < 2) continue;
  dupMelodyGroups++;
  dupMelodyRows += v.length;
  const ts = [...new Set(v.map((x) => x.g))];
  if (ts.length > 1 && ts.some((a) => ts.some((b) => a !== b && (a.includes(b) || b.includes(a))))) {
    titleContainsMelody++;
  }
}
const placeholderTitles = rows.filter((r) => PLACEHOLDER.test((r.t || '').trim()) && r.p).length;

const snap = {
  at: new Date().toISOString().slice(0, 19),
  songs: rows.length,
  groups: groups.size,
  notes: rows.reduce((a, r) => a + r.p.length, 0),
  dupGroupNames,
  dupMelodyGroups,
  dupMelodyRows,
  titleContainsMelody,
  placeholderTitles,
};

const arg = (name) => { const i = process.argv.indexOf(name); return i > 0 ? process.argv[i + 1] : ''; };
const out = arg('--out');
const cmp = arg('--compare');

console.log(`QA 快照（${snap.at}）`);
for (const [k, v] of Object.entries(snap)) {
  if (k === 'at') continue;
  console.log(`  ${k.padEnd(20)} ${String(v).padStart(8)}`);
}
if (out) {
  writeFileSync(out, JSON.stringify(snap, null, 2) + '\n', 'utf8');
  console.log(`  已写快照: ${out}`);
}
if (cmp) {
  if (!existsSync(cmp)) { console.error(`  找不到旧快照: ${cmp}`); process.exit(1); }
  const old = JSON.parse(readFileSync(cmp, 'utf8'));
  console.log(`\n与旧快照对比（${old.at} -> ${snap.at}）:  ↓ 表示减少`);
  let worse = 0;
  // "越少越好"的指标: 组数/重复/占位名；"越多越好"的: 曲数/音符数
  const fewerIsBetter = new Set(['dupGroupNames', 'dupMelodyGroups', 'dupMelodyRows', 'titleContainsMelody', 'placeholderTitles', 'groups']);
  for (const k of Object.keys(snap)) {
    if (k === 'at' || !(k in old)) continue;
    const d = snap[k] - old[k];
    if (d === 0) continue;
    const better = fewerIsBetter.has(k) ? d < 0 : d > 0;
    if (!better) worse++;
    console.log(`  ${better ? '✓' : '✗'} ${k.padEnd(20)} ${String(old[k]).padStart(8)} -> ${String(snap[k]).padStart(8)} (${d > 0 ? '+' : ''}${d})`);
  }
  console.log(worse ? `\n  **有 ${worse} 项变差**，改动要再想想` : '\n  没有变差的项 ✓');
}

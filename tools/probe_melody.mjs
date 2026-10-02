// node tools/probe_melody.mjs "1155665 4433221" ["另一个旋律" ...]
//
// **旋律探针**：给一串数字（或几串），看它在**当前语料**里命中哪首歌、代价多少、并列几首。
//
// 为什么需要它: 首页那些"例子"如果点下去**搜不到**，看起来就像站点坏了 ——
// 所以换例子时**必须先测**，不能凭感觉挑（"这歌耳熟，应该能搜到吧" ✗）。
//
// 口径与线上一致: 只做类型擦除后 import `static/search.ts`，并建好 ngram 倒排（与线上空闲时一样）。
import { readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { importStatic } from './_built.mjs';

const { buildIndex, search, ensureGrams } = await importStatic('search');
const { parseQuery } = await importStatic('jptok');
const idx = buildIndex(gunzipSync(readFileSync(new URL('../data/songs.jsonl.gz', import.meta.url))).toString('utf8'));
ensureGrams(idx, 4);

const args = process.argv.slice(2);
// 不给参数就把下面这组"候选例子"全测一遍（这个脚本最常见的用法就是"挑例子"）。

// `--from-title 名字 [开头几个音=12]`：**从语料里取那首歌的开头音**，并做"回头搜一遍"验证。
// 为什么要有这个模式: 凭"这歌耳熟"猜旋律**经常猜错**（实测《茉莉花》《找朋友》《月亮代表我的心》
// 我猜的都对不上 ✗）。而挑一首**在语料里**的熟歌、取它自己的开头音 -> **必然命中且歌名是真的** ✓。
const ti = args.indexOf('--from-title');
if (ti >= 0) {
  const name = args[ti + 1];
  const n = Number(args[ti + 2] || 12);
  const members = idx.groups.get(name) || [];
  if (!members.length) { console.log(`  语料里找不到《${name}》`); process.exit(1); }
  const s = members[0];
  let q = '';
  for (let k = 0; k < Math.min(n, s.p.length); k++) {
    const ch = s.a.charCodeAt(k);
    q += (ch === 49 ? '#' : ch === 50 ? 'b' : '') + s.p[k];
  }
  console.log(`  《${name}》开头 ${q.length} 音: ${q}`);
  const segs = [parseQuery(q)];
  const res = search(idx, segs, { top: 5 });
  const tied = res.filter((r) => r.cost === res[0].cost).length;
  console.log(`  回头搜一遍 -> 代价 ${res[0].cost} · 并列 ${tied} · Top1=${res[0].group}`);
  console.log(`  ${res[0].group === name ? '✓ 命中本尊（可以当例子）' : '✗ 没命中本尊，别用'}`);
  process.exit(0);
}

const defs = [
  ['小星星（一闪一闪亮晶晶）', '1155665 4433221'],
  ['小星星（前半句）', '1155665'],
  ['生日快乐', '556517 556521'],
  ['两只老虎', '12311231345'],
  ['茉莉花（好一朵美丽的茉莉花）', '335611165'],
  ['找朋友', '56535 553532'],
  ['义勇军进行曲', '5111156711'],
  ['鲁冰花', '66165535'],
  ['世上只有妈妈好', '6653561532'],
  ['月亮代表我的心', '111235 5321'],
  ['让我们荡起双桨', '556535165'],
  ['甜蜜蜜', '35653 23216'],
  ['同一首歌', '55532235 3211612655'],
  ['朋友', '556532123'],
  ['东方之珠', '556 535 65'],
];
const use = args.length ? args.map((q) => [q, q]) : defs;

for (const [label, q] of use) {
  // ⚠ 分段口径必须与**站点**一致：空格**不分段**，只按逗号/顿号/分号等分段。
  //   （第一版按空格 split，于是 `1231 1231 345 345` 被拆成 4 段、每段 <5 音，报"太短" ✗ ——
  //    那是**探针口径错了**，站点里它是一整句 14 音。见 index.html 的"空格不分段"。）
  const segs = q.split(/[;；|、+，,]+/).map(parseQuery).filter((s) => s.length >= 5);
  if (!segs.length) { console.log(`  ${label.padEnd(26)} 太短（每段至少 5 个音；空格不分段，逗号才分段）`); continue; }
  const res = search(idx, segs, { top: 8 });
  if (!res.length) { console.log(`  ${label.padEnd(26)} 无结果`); continue; }
  const tied = res.filter((r) => r.cost === res[0].cost).length;
  console.log(`  ${label.padEnd(26)} ${('代价 ' + res[0].cost).padEnd(9)} ${('并列 ' + tied).padEnd(9)} ${res.slice(0, 2).map((r) => r.group).join(' / ')}`);
}

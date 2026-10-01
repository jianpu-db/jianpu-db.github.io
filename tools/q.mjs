// node tools/q.mjs <旋律串>  —— 用前端索引(权威)跑一次真实查询
import { readFileSync } from 'node:fs';
import { importStatic } from './_built.mjs';
const { buildIndex, search } = await importStatic('search');
const { parseQuery } = await importStatic('jptok');
import { gunzipSync } from 'node:zlib';

const q = process.argv[2];
const idx = buildIndex(gunzipSync(readFileSync(new URL('../data/songs.jsonl.gz', import.meta.url))).toString('utf8'));
const segs = q.split(/[;；|、+，,]+/).map(parseQuery).filter((s) => s.length >= 5);
console.log('查询:', segs.map((s) => s.map((x) => (x.acc === 1 ? '#' : x.acc === -1 ? 'b' : '') + x.d).join(' ')).join(' | '));
const res = search(idx, segs, { top: 5 });
if (!res.length) { console.log('无结果'); process.exit(0); }
const top = res[0].cost;
const tied = res.filter((r) => r.cost === top).length;
console.log(`命中 ${res.length} 组; 最优代价 ${top}; 并列 ${tied} 组`);
for (const r of res) {
  console.log(`  ${r.cost}  ${r.group}  [${r.source}]  记号 ${r.exact}/${r.qlen}  音符 ${r.n}`);
  console.log(`     库内该段: ${r.libNotes.map((n) => (n.acc === 1 ? '#' : n.acc === -1 ? 'b' : '') + n.d).join(' ')}`);
}

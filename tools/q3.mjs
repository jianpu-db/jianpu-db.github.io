// node tools/q3.mjs <旋律串>  —— 全库找"最接近的窗口", 列出前 12 名(不限曲名)
import { readFileSync } from 'node:fs';
import { importStatic } from './_built.mjs';
const { buildIndex } = await importStatic('search');
const { parseQuery } = await importStatic('jptok');
import { gunzipSync } from 'node:zlib';

const Q = parseQuery(process.argv[2]);
const qd = Q.map((x) => x.d);
const n = qd.length;
const idx = buildIndex(gunzipSync(readFileSync(new URL('../data/songs.jsonl.gz', import.meta.url))).toString('utf8'));

const hits = [];
for (const [g, members] of idx.groups) {
  let best = null;
  for (const s of members) {
    const P = Array.from(s.p).map(Number);
    for (let i = 0; i + n <= P.length; i++) {
      let e = 0;
      for (let k = 0; k < n; k++) { if (P[i + k] !== qd[k]) e++; if (best && e >= best.e) break; }
      if (!best || e < best.e) best = { e, at: i, s, seg: P.slice(i, i + n), g };
    }
  }
  if (best) hits.push(best);
}
hits.sort((a, b) => a.e - b.e);
console.log(`查询 ${Q.map((x) => x.d).join(' ')}  (${n} 音)`);
console.log('最接近的 12 个窗口(跨全库):');
for (const h of hits.slice(0, 12)) {
  console.log(`  差 ${h.e}  ${h.g}  [${h.s.source}]  文件 ${(h.s.file || []).join(',')}`);
  console.log(`       库内: ${h.seg.join(' ')}   (位置 ${h.at})`);
}

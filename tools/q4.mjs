// node tools/q4.mjs <旋律串>  —— 移调不变检索: 试 12 个移调, 看哪个移调下 0 代价
import { readFileSync } from 'node:fs';
import { importStatic } from './_built.mjs';
const { buildIndex } = await importStatic('search');
const { parseQuery } = await importStatic('jptok');
import { gunzipSync } from 'node:zlib';

const Q = parseQuery(process.argv[2]);
const qd = Q.map((x) => x.d);
const n = qd.length;
const idx = buildIndex(gunzipSync(readFileSync(new URL('../data/songs.jsonl.gz', import.meta.url))).toString('utf8'));
const round = (d) => ((d - 1) % 7 + 7) % 7 + 1;      // 音级循环(忽略大小调音程精确性, 只做音级移调)

for (let sh = 0; sh < 7; sh++) {
  const t = qd.map((d) => round(d + sh));
  const hits = [];
  for (const [g, members] of idx.groups) {
    for (const s of members) {
      const P = Array.from(s.p).map(Number);
      for (let i = 0; i + n <= P.length; i++) {
        let e = 0;
        for (let k = 0; k < n; k++) if (P[i + k] !== t[k]) { e++; break; }
        if (e === 0) { hits.push(`${g}[${s.source}]@${i}`); break; }
      }
    }
  }
  console.log(`移调 +${sh}  (${t.join(' ')}): 0 代价命中 ${hits.length} 首` +
    (hits.length ? '\n     ' + hits.slice(0, 8).join('\n     ') : ''));
}

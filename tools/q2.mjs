// node tools/q2.mjs <旋律串> <曲名子串>  —— 在指定曲目里找最接近的 11 音窗口
import { readFileSync } from 'node:fs';
import { importStatic } from './_built.mjs';
const { buildIndex, search } = await importStatic('search');
const { parseQuery } = await importStatic('jptok');
import { gunzipSync } from 'node:zlib';

const target = process.argv[3] || '神々';
const Q = parseQuery(process.argv[2]);
const qd = Q.map((x) => x.d);
const idx = buildIndex(gunzipSync(readFileSync(new URL('../data/songs.jsonl.gz', import.meta.url))).toString('utf8'));

for (const [g, members] of idx.groups) {
  if (!g.includes(target)) continue;
  for (const s of members) {
    const P = Array.from(s.p).map(Number);
    const A = Array.from(s.a || '').map((c) => (c === '1' ? '#' : c === '2' ? 'b' : ''));
    let best = null;
    for (let i = 0; i + qd.length <= P.length; i++) {
      let e = 0;
      for (let k = 0; k < qd.length; k++) if (P[i + k] !== qd[k]) e++;
      if (!best || e < best.e) best = { e, at: i };
    }
    const seg = best ? P.slice(best.at, best.at + qd.length) : [];
    const segA = best ? A.slice(best.at, best.at + qd.length) : [];
    console.log(`=== ${g}  [${s.source}]  文件 ${(s.file || []).join(',')}  音符 ${s.n}  小节 ${(s.bars || []).length}`);
    console.log(`   你的输入: ${Q.map((x) => (x.acc === 1 ? '#' : x.acc === -1 ? 'b' : '') + x.d).join(' ')}`);
    console.log(`   最接近@${best ? best.at : '-'} 差 ${best ? best.e : '-'} 音: ` +
      seg.map((d, k) => (segA[k] || '') + d).join(' '));
    // 顺便把该曲里所有含 "3356" 开头的窗口列出来, 便于人工看
    const starts = [];
    for (let i = 0; i + 4 <= P.length; i++) {
      if (P[i] === 3 && P[i + 1] === 3 && P[i + 2] === 5 && P[i + 3] === 6) {
        starts.push(i + ':' + P.slice(i, i + 12).join(''));
      }
    }
    console.log(`   谱内所有 3356 开头(前 12 音): ${starts.slice(0, 6).join('  ') || '(无)'}`);
  }
}

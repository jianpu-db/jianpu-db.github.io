// node tools/_wasm_prune_diag.mjs —— 诊断: "最小代价成员到底有几个"（解释为什么剪枝没效果）
import { readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { importStatic } from './_built.mjs';

const WASM = process.argv[2]
  || 'D:/Documents_D/jianpu2/wasm-matcher/target/wasm32-unknown-unknown/release/jianpu_matcher.wasm';
const { buildIndex } = await importStatic('search');
const { parseQuery } = await importStatic('jptok');
const idx = buildIndex(gunzipSync(readFileSync(new URL('../data/songs.jsonl.gz', import.meta.url))).toString('utf8'));
const songs = idx.songs;

const off = new Uint32Array(songs.length + 1);
let total = 0;
for (let i = 0; i < songs.length; i++) { off[i] = total; total += songs[i].p.length; }
off[songs.length] = total;
const P = new Uint8Array(total), A = new Int8Array(total);
{
  let w = 0;
  for (const s of songs) {
    for (let k = 0; k < s.p.length; k++) {
      P[w] = s.p.charCodeAt(k);
      A[w] = s.a.charCodeAt(k) === 49 ? 1 : s.a.charCodeAt(k) === 50 ? -1 : 0;
      w++;
    }
  }
}
const { instance } = await WebAssembly.instantiate(readFileSync(WASM), {});
const ex = instance.exports;
const pPtr = ex.jp_alloc(total), aPtr = ex.jp_alloc(total), oPtr = ex.jp_alloc(off.length * 4);
new Uint8Array(ex.memory.buffer, pPtr, total).set(P);
new Int8Array(ex.memory.buffer, aPtr, total).set(A);
new Uint32Array(ex.memory.buffer, oPtr, off.length).set(off);
ex.jp_set_corpus(pPtr, aPtr, oPtr, songs.length);
const outPtr = ex.jp_alloc(songs.length * 8);

console.log('查询              音数  最小代价  达到该代价的**歌**数   达到该代价的**组**数');
for (const raw of ['63731232', '55532235', '5111156711', '66165535 532322', '16665', '5565345', '17123215']) {
  for (const seg of raw.split(' ')) {
    const q = parseQuery(seg);
    if (q.length < 5) continue;
    const qd = Uint8Array.from(q.map((x) => 48 + x.d));
    const qa = Int8Array.from(q.map((x) => x.acc));
    const qp = ex.jp_alloc(qd.length), ap = ex.jp_alloc(qa.length);
    new Uint8Array(ex.memory.buffer, qp, qd.length).set(qd);
    new Int8Array(ex.memory.buffer, ap, qa.length).set(qa);
    ex.jp_scan_all(qp, ap, qd.length, outPtr);
    const rawOut = new Uint32Array(ex.memory.buffer, outPtr, songs.length * 2);
    let min = 0xffffffff;
    for (let i = 0; i < songs.length; i++) if (rawOut[i * 2] < min) min = rawOut[i * 2];
    let nSongs = 0;
    const groupsHit = new Set();
    for (let i = 0; i < songs.length; i++) {
      if (rawOut[i * 2] === min) { nSongs++; groupsHit.add(songs[i].group); }
    }
    console.log(`${seg.padEnd(18)}${String(q.length).padStart(4)}${String(min).padStart(8)}${String(nSongs).padStart(20)}${String(groupsHit.size).padStart(20)}`);
  }
}

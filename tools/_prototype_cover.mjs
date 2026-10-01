// node tools/_prototype_cover.mjs [K]
//
// **快路径覆盖率**：剪枝只在"每一段都能查到精确命中（代价 0）"时才敢走。
// 这个脚本拿**金曲清单**（四个榜单的真实查询，住在工具仓库 train-work/ 里）统计覆盖率 ——
// 覆盖率低就不值得做（大多数查询还得退回全扫）。
import { readFileSync, existsSync, readdirSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { importStatic } from './_built.mjs';

const K = Number(process.argv[2] || 4);
const DIR = 'D:/Documents_D/jianpu2/train-work';
const { buildIndex } = await importStatic('search');
const { parseQuery } = await importStatic('jptok');
const idx = buildIndex(gunzipSync(readFileSync(new URL('../data/songs.jsonl.gz', import.meta.url))).toString('utf8'));
const songs = idx.songs;

const t0 = performance.now();
const grams = new Map();
const H = new Uint8Array(songs.length);
for (let i = 0; i < songs.length; i++) {
  const p = songs[i].p;
  for (let j = 0; j + K <= p.length; j++) {
    const g = p.slice(j, j + K);
    const a = grams.get(g);
    if (a) a.push(i); else grams.set(g, [i]);
  }
}
console.log(`ngram 索引: ${grams.size} gram / ${(performance.now() - t0).toFixed(0)} ms`);

function candsOf(qstr, stamp) {
  if (qstr.length < K) return null;
  let best = null, bestLen = Infinity;
  for (let j = 0; j + K <= qstr.length; j++) {
    const arr = grams.get(qstr.slice(j, j + K));
    if (!arr) return [];                                     // 这个 gram 全库没有 -> 不可能精确命中
    if (arr.length < bestLen) { bestLen = arr.length; best = arr; }
  }
  const out = [];
  for (const i of best) {
    if (H[i] === stamp) continue;
    H[i] = stamp;
    if (songs[i].p.includes(qstr)) out.push(i);
  }
  return out;
}

const files = existsSync(DIR)
  ? readdirSync(DIR).filter((f) => f.startsWith('eval_set_') && f.endsWith('.tsv'))
  : [];
if (!files.length) {
  console.log(`跳过：找不到金曲清单（${DIR}/eval_set_*.tsv）—— 那是工具仓库里的东西`);
  process.exit(0);
}

let stamp = 0, total = 0, fast = 0, noneAtAll = 0;
let candSum = 0;
console.log('\n榜单                    查询条数   每段都有精确命中(可走快路径)   平均候选数/全库');
for (const f of files) {
  let n = 0, ok = 0, cs = 0, cn = 0;
  const lines = readFileSync(`${DIR}/${f}`, 'utf8').split('\n');
  for (const line of lines) {
    if (!line.trim() || line.startsWith('#')) continue;
    const cols = line.split('\t');
    const melody = (cols[0] || '').trim();                   // 第一列是旋律
    if (!melody || !/[1-7]/.test(melody)) continue;
    const segs = melody.split(/[;；|、+，,]+/).map(parseQuery).filter((s) => s.length >= 5);
    if (!segs.length) continue;
    n++;
    let all = true, sum = 0;
    for (const q of segs) {
      const qstr = q.map((x) => x.d).join('');
      const c = candsOf(qstr, ++stamp);
      if (c === null || c.length === 0) {
        // 走快路径的前提是"这一整段能精确命中"（否则退回全扫）
        all = false;
        if (c !== null && c.length === 0) noneAtAll++;
        // 即便这一段没有，也按全库算候选（保守）
        sum += songs.length;
      } else {
        sum += c.length;
      }
    }
    if (all) { ok++; cs += sum / segs.length; cn++; }
  }
  total += n; fast += ok;
  console.log(`${f.replace('eval_set_', '').replace('.tsv', '').padEnd(24)} ${String(n).padStart(8)} `
              + `${String(ok).padStart(10)} (${n ? (ok / n * 100).toFixed(1) : '0'}%) `
              + `${cn ? (cs / cn).toFixed(0) + ' / ' + songs.length : '-'}`);
}
console.log(`\n合计: ${fast}/${total} = **${total ? (fast / total * 100).toFixed(1) : 0}%** 的榜单查询可以走剪枝快路径`);
console.log(`（"整段无精确命中"的段数: ${noneAtAll} —— 这些只能退回全扫，正确性不受影响）`);

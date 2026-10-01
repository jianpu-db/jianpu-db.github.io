// node tools/check_wasm_parity.mjs [wasm路径] [查询数]
//
// **TS ↔ Wasm 全库对拍**：同一批查询，在**真语料**（11,495 首 / 2.5M 音符）上逐首比 (cost, at)。
//
// 为什么要逐首比，而不是比最终卡片:
//   `search()` 的最终结果经过并列裁决/段落权重/人气……多层筛选 —— 中间错了也可能被"抹平"，
//   只看卡片等于把灵敏度丢掉。这里的粒度是"每一首歌的最佳窗口"，与 wasm 导出的
//   `jp_best_in_song` **完全同粒度**，任何一处口径漂了都会被抓到。
//
// 查询集三类（都要过）:
//   ① 硬编码旋律片段（含带变音的那种 —— "发送从严/接收从宽"的代价表最容易错）；
//   ② 从语料里**随机切窗口**当查询（保证有精确匹配，且覆盖各种长度/段落）；
//   ③ 带变音的窗口（`a` 串里有 1/2 的歌）。
//
// 跨仓库: wasm 在 `jianpu2/wasm-matcher` 里编译。找不到就**明确跳过**（CI 只 checkout 本站点仓库）。
import { readFileSync, existsSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { importStatic } from './_built.mjs';

const WASM = process.argv[2]
  || 'D:/Documents_D/jianpu2/wasm-matcher/target/wasm32-unknown-unknown/release/jianpu_matcher.wasm';
const NQ = Number(process.argv[3] || 12);

if (!existsSync(WASM)) {
  console.log(`跳过：找不到 wasm（${WASM}）`);
  console.log('  它是跨仓库产物，先在工具仓库编译:');
  console.log('    cd jianpu2/wasm-matcher && cargo build --release --target wasm32-unknown-unknown');
  process.exit(0);
}

const { buildIndex, bestWindow, search } = await importStatic('search');
const { parseQuery } = await importStatic('jptok');

const idx = buildIndex(gunzipSync(readFileSync(new URL('../data/songs.jsonl.gz', import.meta.url))).toString('utf8'));
console.log(`语料 ${idx.count} 首（站点索引口径）`);

// ── 把语料摊成 wasm 要的三块数组 ──────────────────────────────────────────────
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
      P[w] = s.p.charCodeAt(k);                        // 索引里 p 就是 '1'..'7' 的字符
      A[w] = s.a.charCodeAt(k) === 49 ? 1 : s.a.charCodeAt(k) === 50 ? -1 : 0;
      w++;
    }
  }
}
console.log(`音符总数 ${total.toLocaleString()} · 数组 ${(total / 1e6).toFixed(2)} MB + ${(total / 1e6).toFixed(2)} MB + ${(off.length * 4 / 1e6).toFixed(2)} MB`);

const wasmBytes = readFileSync(WASM);
const { instance } = await WebAssembly.instantiate(wasmBytes, {});
const ex = instance.exports;
console.log(`wasm ${(wasmBytes.length / 1024).toFixed(1)} KB · 导出 ${Object.keys(ex).filter((k) => k.startsWith('jp_')).length} 个函数`);

const pPtr = ex.jp_alloc(total);
const aPtr = ex.jp_alloc(total);
const oPtr = ex.jp_alloc(off.length * 4);
if (!pPtr || !aPtr || !oPtr) {
  console.error(`✗ wasm 分配失败（p=${pPtr} a=${aPtr} o=${oPtr}）—— 线性内存不够或分配器没 grow`);
  process.exit(1);
}
new Uint8Array(ex.memory.buffer, pPtr, total).set(P);
new Int8Array(ex.memory.buffer, aPtr, total).set(A);
new Uint32Array(ex.memory.buffer, oPtr, off.length).set(off);
ex.jp_set_corpus(pPtr, aPtr, oPtr, songs.length);
console.log('语料已灌入 wasm（含自动扩容）');

// ── 查询集 ────────────────────────────────────────────────────────────────────
const FIXED = ['63731232', '55532235', '5111156711', '66165535 532322', '17123215', '5565345'];
const LENS = [5, 7, 9, 11, 13, 15];
const queries = [...FIXED];
let seed = 20261001;
const rnd = (n) => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed % n; };
while (queries.length < NQ) {
  // 随机切一个窗口（优先带变音的歌，覆盖代价表那几条分支）
  const wantAcc = queries.length % 3 === 0;
  let si = rnd(songs.length), guard = 0;
  if (wantAcc) {
    while (!/[12]/.test(songs[si].a) && guard++ < 500) si = rnd(songs.length);
  }
  const s = songs[si];
  const len = LENS[rnd(LENS.length)];
  if (s.p.length < len) continue;
  const at = rnd(s.p.length - len + 1);
  let q = '';
  for (let k = 0; k < len; k++) {
    const acc = s.a.charCodeAt(at + k) === 49 ? '#' : s.a.charCodeAt(at + k) === 50 ? 'b' : '';
    q += (acc || '') + s.p[at + k];
  }
  queries.push(q);
}

// ── 逐首对拍 ──────────────────────────────────────────────────────────────────
const outPtr = ex.jp_alloc(songs.length * 8);
let bad = 0, checked = 0, nullBoth = 0, rewin = 0;
const t0 = Date.now();
for (const raw of queries) {
  const q = parseQuery(raw);
  if (!q.length) continue;
  const qd = Uint8Array.from(q.map((x) => 48 + x.d));
  const qa = Int8Array.from(q.map((x) => x.acc));
  const qPtr = ex.jp_alloc(qd.length);
  const qaPtr = ex.jp_alloc(qa.length);
  new Uint8Array(ex.memory.buffer, qPtr, qd.length).set(qd);
  new Int8Array(ex.memory.buffer, qaPtr, qa.length).set(qa);
  // ① wasm 全库扫
  const t1 = Date.now();
  ex.jp_scan_all(qPtr, qaPtr, qd.length, outPtr);
  const tWasm = Date.now() - t1;
  const rawOut = new Uint32Array(ex.memory.buffer, outPtr, songs.length * 2);
  // ② TS 逐首算
  const t2 = Date.now();
  let mism = 0;
  const examples = [];
  for (let i = 0; i < songs.length; i++) {
    const wc = rawOut[i * 2], wa = rawOut[i * 2 + 1];
    const ts = bestWindow(songs[i], q);
    const noHit = wc === 0xffffffff;
    if (noHit && !ts) { nullBoth++; continue; }
    if (noHit !== !ts) {
      mism++; if (examples.length < 3) examples.push(`#${i} ${songs[i].title}: wasm=${noHit ? 'null' : `${wc}@${wa}`} ts=${ts ? `${ts.cost}@${ts.at}` : 'null'}`);
      continue;
    }
    checked++;
    // ── 契约（不是"逐字相同"）────────────────────────────────────────────────
    // ① **cost 必须逐首完全一致** —— 这是 wasm 与 TS 共享的那份口径（代价表 + 滑窗）。
    // ② `at` 只是"位置最小的最小代价窗"：段落权重换窗**刻意留在 TS**（副歌优先于前奏），
    //    wasm 不搬这段产品口径（搬过去要传每首的段落表，边界代价不值当）。
    //    所以这里验的是：TS 选的窗**也确实是最小代价窗**（就地复算那个窗口的代价）。
    if (ts.cost !== wc) {
      mism++;
      if (examples.length < 3) examples.push(`#${i} ${songs[i].title}: wasm cost=${wc} ts cost=${ts.cost}`);
    } else if (ts.at !== wa) {
      // 同代价不同窗：复算 TS 那个窗的代价，必须等于 wc（否则才是真问题）
      const { P: PP, A: AA } = { P: P, A: A };            // 语料扁平数组（同 wasm 口径）
      const st = off[i];
      let c2 = 0;
      for (let k = 0; k < q.length; k++) {
        const cd = PP[st + ts.at + k], ca = AA[st + ts.at + k];
        // ⚠ 语料数组里存的是 **ASCII**（'1' 的 49），而 q[k].d 是**数字 1-7** ——
        //   第一版直接比，永远不等 -> 复算出的代价恒等于 4×音数（36 = 4×9），看着像"口径漂了"。
        const qd2 = 48 + q[k].d, qacc = q[k].acc;
        c2 += qd2 !== cd ? 4 : qacc === ca ? 0 : qacc === 0 ? 1 : ca === 0 ? 2 : 3;
      }
      if (c2 !== wc) {
        mism++;
        if (examples.length < 3) examples.push(`#${i} ${songs[i].title}: TS 窗代价 ${c2} != wasm 最小 ${wc}`);
      } else {
        rewin++;                                          // 合法的段落权重换窗
      }
    }
  }
  const tTs = Date.now() - t2;
  bad += mism;
  const tag = mism ? `✗ ${mism} 处不一致` : '✓';
  console.log(`  ${tag}  ${String(raw).slice(0, 34).padEnd(34)} (${q.length} 音)  wasm ${tWasm} ms / TS ${tTs} ms`);
  for (const e of examples) console.log('        ' + e);
}
const dt = ((Date.now() - t0) / 1000).toFixed(1);
console.log(`\n对拍 ${queries.length} 条查询 × ${songs.length} 首 = ${(queries.length * songs.length).toLocaleString()} 次单曲比较`
            + `（有效比较 ${checked.toLocaleString()}，两边都"无命中" ${nullBoth.toLocaleString()}，`
            + `段落权重换窗 ${rewin.toLocaleString()}），用时 ${dt}s`);
if (bad) { console.log(`**不一致 ${bad} 处** —— wasm 与 TS 的代价口径漂了，别上线`); process.exit(1); }
console.log('TS ↔ Wasm 契约成立 ✓：代价逐首一致；at 只在「同代价按段落权更换窗」处不同（那是刻意留在 TS 的口径）');

// ══════════════════════════════════════════════════════════════════════════════
// ② **端到端对拍**：同一批查询，`search()` 走"表快路径"与"纯 TS 全扫"的结果必须**逐条相同**
//
// 这是真正决定"能不能上线"的一条: 前面比的是单首代价，这里比的是**最终卡片**
// （多段相加、段落权重、并列裁决、人气、来源去重……全都在里面）。
// ══════════════════════════════════════════════════════════════════════════════
console.log('\n② 端到端: 表快路径 vs 纯 TS 全扫');
const MULTI = [...queries, '63731232; 1765', '55532235 3211612655', '66165535 532322 7656'];
let e2eBad = 0, e2eChecked = 0;
const tE0 = Date.now();
for (const raw of MULTI) {
  const segs = raw.split(/[;；|、+，,]+/).map(parseQuery).filter((s) => s.length >= 5);
  if (!segs.length) continue;
  // 用 wasm 给每段造表
  const tables = segs.map((q) => {
    const qd = Uint8Array.from(q.map((x) => 48 + x.d));
    const qa = Int8Array.from(q.map((x) => x.acc));
    const qPtr = ex.jp_alloc(qd.length), qaPtr = ex.jp_alloc(qa.length);
    new Uint8Array(ex.memory.buffer, qPtr, qd.length).set(qd);
    new Int8Array(ex.memory.buffer, qaPtr, qa.length).set(qa);
    ex.jp_scan_all(qPtr, qaPtr, qd.length, outPtr);
    const costs = new Uint32Array(songs.length), ats = new Uint32Array(songs.length);
    const rawOut = new Uint32Array(ex.memory.buffer, outPtr, songs.length * 2);
    for (let i = 0; i < songs.length; i++) { costs[i] = rawOut[i * 2]; ats[i] = rawOut[i * 2 + 1]; }
    return { costs, ats };
  });
  const key = (r) => r.map((x) => `${x.group}|${x.cost}|${x.at}|${x.exact}|${x.sec}`).join(' ;; ');
  const withWasm = search(idx, segs, { top: 10, scan: tables });
  const pureTs = search(idx, segs, { top: 10 });
  e2eChecked++;
  if (key(withWasm) !== key(pureTs)) {
    e2eBad++;
    console.log(`  ✗ ${String(raw).slice(0, 40)}`);
    console.log(`     表: ${key(withWasm).slice(0, 160)}`);
    console.log(`     TS: ${key(pureTs).slice(0, 160)}`);
  } else {
    console.log(`  ✓ ${String(raw).slice(0, 40).padEnd(42)} 前 3 条: ` +
      withWasm.slice(0, 3).map((r) => `${r.group}(${r.cost})`).join(', '));
  }
}
console.log(`\n端到端对拍 ${e2eChecked} 条查询（含多段）· 用时 ${((Date.now() - tE0) / 1000).toFixed(1)}s`);
if (e2eBad) { console.log(`**结果不一致 ${e2eBad} 条** —— 快路径不能上线`); process.exit(1); }
console.log('表快路径与纯 TS 全扫**结果逐条相同** ✓（可以上线）');

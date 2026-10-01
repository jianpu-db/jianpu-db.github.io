// node tools/check_recall.mjs [基准文件] [L] [错音数] [查询数]
//
// **前端口径的召回评测** —— 和 `jianpu2/tools/melody_retrieval_eval.py`（离线 Python 口径）
// 打同一份基准，但走的是**真正上线的那份检索**（`static/search.ts`）。
//
// 为什么要单独一个: 我改过检索（排序键预计算 + ngram 剪枝），Python 侧的评测**验不到 TS 侧** ——
// 剪枝最坏的情况是"把目标歌剪掉"，那在离线口径里根本看不出来。这里直接对 TS 口径跑 Top-1/3/5。
//
// 两种 rank 口径（照离线评测的做法，避免用并列把数字做大）:
//   * **strict**: 目标歌在结果数组里的位置（search 的并列裁决是确定性的，但对手歌可能排在前面）；
//   * **并列下界**: 同代价的组里按组名排序时目标的位置 —— 这是"最保守"的名次。
import { readFileSync, existsSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { importStatic } from './_built.mjs';

const BENCH = process.argv[2] || 'D:/Documents_D/jianpu2/train-work/bench_final100.txt';
const L = Number(process.argv[3] || 11);
const ERR = Number(process.argv[4] || 0);
const NPER = Number(process.argv[5] || 3);          // 每首歌取几个不同窗口（取平均，降随机性）

if (!existsSync(BENCH)) {
  console.log(`跳过：找不到基准文件（${BENCH}）—— 那是工具仓库里的东西`);
  process.exit(0);
}

const { buildIndex, search, ensureGrams } = await importStatic('search');
const idx = buildIndex(gunzipSync(readFileSync(new URL('../data/songs.jsonl.gz', import.meta.url))).toString('utf8'));
ensureGrams(idx, 4);                                 // 线上会在空闲时建；这里直接建，量的是"有剪枝"的口径

const names = readFileSync(BENCH, 'utf8').split('\n').map((s) => s.trim()).filter((s) => s && !s.startsWith('#'));
// 改编版别当查询源（照离线评测的口径）
const SKIP = /吉他|钢琴|尤克里里|卡林姆|卡林巴|拇指琴|双谱|器乐|伴奏|指弹|弹唱/;

let seed = 20261002;
const rnd = (n) => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed % n; };

const rows = [];
let noSource = 0, noQuery = 0;
for (const name of names) {
  const members = (idx.groups.get(name) || []).filter((s) => s.p.length >= L);
  if (!members.length) { noSource++; continue; }
  const clean = members.filter((s) => !SKIP.test(s.raw || '') && !SKIP.test(s.title || ''));
  const pool = clean.length ? clean : members;
  const src = pool[rnd(pool.length)];
  for (let t = 0; t < NPER; t++) {
    const at = rnd(Math.max(1, src.p.length - L + 1));
    let q = '';
    for (let k = 0; k < L; k++) {
      const ch = src.a.charCodeAt(at + k);
      q += (ch === 49 ? '#' : ch === 50 ? 'b' : '') + src.p[at + k];
    }
    if (ERR > 0) {
      // 注入错音（模拟凭耳朵报错）: 把 ERR 个位置换成别的音级
      const arr = q.split('');
      for (let e = 0; e < ERR; e++) {
        const pos = rnd(arr.length);
        if (/[1-7]/.test(arr[pos])) arr[pos] = '1234567'[rnd(7)];
      }
      q = arr.join('');
    }
    const parsed = (await importStatic('jptok')).parseQuery(q);
    if (parsed.length < 5) { noQuery++; continue; }
    const res = search(idx, [parsed], { top: 50 });
    if (!res.length) { noQuery++; continue; }
    // ⚠ 字段名是 `cost`（`SearchResult` 里 `cost: r.total`）—— 我第一版写成 `total`，`undefined === undefined`
    //   让**所有**结果都算成并列，于是"同代价组均值 50.0、并列下界 0.8%" 全是假的。一个字段名写错
    //   就能造出一个"看起来合理"的指标 —— 这正是"我自己的测试骗了我"那一类。
    const minCost = res[0].cost;
    const strict = res.findIndex((r) => r.group === name);
    // 并列下界: 同代价的组里按组名排序，目标排第几
    const tied = res.filter((r) => r.cost === minCost).map((r) => r.group).sort();
    const lower = tied.indexOf(name);
    // **并列乐观口径**（= 离线 Python 评测的 rank 规则: 同代价时把目标排最前）——
    // 加它是因为离线口径 L=11 报 100%、而产品口径只报 90.7%，差异必须**量出来**而不是猜:
    // 这样就能把"并列策略造成的差"与"索引/采样造成的差"分开看。
    const strictlyBetter = res.filter((r) => r.cost < minCost).length;
    const optimistic = tied.includes(name) ? strictlyBetter + 1 : 999;
    rows.push({ name, strict: strict < 0 ? 999 : strict + 1, lower: lower < 0 ? 999 : lower + 1,
                optimistic, tieSize: tied.length, minCost });
  }
}

const rate = (key, k) => rows.filter((r) => r[key] <= k).length / rows.length;
const mean = (xs) => xs.reduce((a, b) => a + b, 0) / xs.length;
console.log(`基准 ${names.length} 首（可用 ${new Set(rows.map((r) => r.name)).size} 首，找不到谱 ${noSource} 首，查询无效 ${noQuery}）· L=${L} 错音=${ERR} · 共 ${rows.length} 次查询`);
console.log(`  **strict**  Top1 ${(rate('strict', 1) * 100).toFixed(1)}%  Top3 ${(rate('strict', 3) * 100).toFixed(1)}%  Top5 ${(rate('strict', 5) * 100).toFixed(1)}%`);
console.log(`  **并列下界** Top1 ${(rate('lower', 1) * 100).toFixed(1)}%  Top3 ${(rate('lower', 3) * 100).toFixed(1)}%  Top5 ${(rate('lower', 5) * 100).toFixed(1)}%`);
console.log(`  **并列乐观** Top1 ${(rate('optimistic', 1) * 100).toFixed(1)}%   ← 这一列才对得上离线 Python 口径（它把目标排最前）`);
console.log(`  同代价组均值 ${mean(rows.map((r) => r.tieSize)).toFixed(1)}（越大说明并列越多，strict 名次越不稳）`);
const miss = rows.filter((r) => r.strict > 5);
if (miss.length) {
  console.log(`  未进前五的 ${miss.length} 次（前 8 个）:`);
  for (const m of miss.slice(0, 8)) console.log(`     ${m.name}  strict=${m.strict} lower=${m.lower} 代价=${m.minCost}`);
}
// 和离线口径对照（同一份基准、L 相同）
console.log('  （对照: 离线 Python 口径同基准 L=7/e=0 时 Top1 415/415 —— 两边应该同量级）');

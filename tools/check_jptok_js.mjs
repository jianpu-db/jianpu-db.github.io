// node tools/check_jptok_js.mjs [token表.tsv] —— **前端 jptok.js 与 Python 侧 jptok.py 的等价性测试**
//
// 为什么需要: `jptok` 口径有三份实现 —— jptok.py(唯一真源)、score.py 的内置兜底、以及本仓的
// `static/jptok.js`。前两份有 jianpu2/tools/check_jptok_parity.py 锁着, 这第三份**长期没人管**:
// 2026-09-28 实测发现 `BEAT` 表里写着 `h: 2.0`(Python 侧 2026-09-24 已定案 h = 六十四分音符
// = 0.0625), 而 jptok.js 文件头自己写着"改这里时必须同时改 Python 侧"。
//
// 期望值来自 Python: 先跑
//   py -3.13 jianpu2/tools/dump_jptok_tokens.py            # -> jianpu2/train-work/jptok_tokens.tsv
// 再跑本脚本。它会拿全语料里**每一个不同的 token** 去问两边的 is_note / is_pitch /
// duration_letter / beat 是否一致, 不一致就退非 0 并打印例子。
import { importStatic } from './_built.mjs';
const { parseToken, isPitch, beat } = await importStatic('jptok');
import { readFileSync } from 'node:fs';

const path = process.argv[2] || 'D:/Documents_D/jianpu2/train-work/jptok_tokens.tsv';
const lines = readFileSync(path, 'utf8').split('\n').filter((l) => l.trim());
const head = lines.shift();
if (!head || !head.startsWith('token\t')) {
  console.error(`表头不对(期望 token\\tis_note\\tis_pitch\\tduration_letter\\tbeat): ${head}`);
  process.exit(2);
}

// duration_letter: Python 侧取"前缀优先, 没有取后缀"; JS 里没有单独实现, 用 beat 反查即可,
// 所以这里只比 is_note / is_pitch / beat 三项(beat 已经把时值字母的口径包含进去了)。
let n = 0, bad = 0;
const shown = [];
const EPS = 1e-9;
for (const ln of lines) {
  const [tok, isNote, isPitchPy, _dl, beatPy] = ln.split('\t');
  n++;
  const jsNote = parseToken(tok) ? 1 : 0;
  const jsPitch = isPitch(tok) ? 1 : 0;
  const jsBeat = beat(tok);
  const pyBeat = Number(beatPy);
  const ok = jsNote === Number(isNote) && jsPitch === Number(isPitchPy)
          && Math.abs(jsBeat - pyBeat) < EPS;
  if (!ok) {
    bad++;
    if (shown.length < 20) {
      shown.push(`  ${JSON.stringify(tok)}  is_note py=${isNote} js=${jsNote} | `
               + `is_pitch py=${isPitchPy} js=${jsPitch} | beat py=${pyBeat} js=${jsBeat}`);
    }
  }
}

console.log(`比过 ${n} 个不同 token（期望值来自 jptok.py）`);
if (!bad) {
  console.log('\n前端 jptok.js 与 Python 侧 **逐项一致** ✓（这份测试就是第三份口径的锁）');
} else {
  console.log(`\n!! ${bad} 处不一致（这正是 2026-09-28 那类"某一份口径偷偷漂了"的苗头）:`);
  for (const s of shown) console.log(s);
  if (bad > shown.length) console.log(`  …还有 ${bad - shown.length} 处`);
  process.exitCode = 1;
}

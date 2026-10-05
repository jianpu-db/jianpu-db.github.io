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
// duration_letter / beat / **逐音** 是否一致, 不一致就退非 0 并打印例子。
//
// ⚠ 2026-10-05: 加了第 5 列之后的**第 6 列 `notes`(逐音)**。为什么非加不可: 和弦 token 是
//   "多个音连写成一个", 八度/变音写在各自音级左边 —— 只比 is_note/is_pitch 的话, 前端把
//   `,4,,b5,,3,,1` 解析成什么都照样绿(实测这正是它从没被发现的原因)。逐音比才锁得住。
import { importStatic } from './_built.mjs';
const { parseToken, parseTokenAll, isPitch, beat } = await importStatic('jptok');
import { existsSync, readFileSync } from 'node:fs';

// ⚠ 2026-10-01 修（CI 上红的四处之一）: 期望值来自**另一个仓库**
//   （`jianpu2/tools/dump_jptok_tokens.py` 生成的 token 表），而 CI 只 checkout 本站点仓库。
//   原来找不到文件就直接异常 -> 被读成"代码坏了"。**跨仓库依赖缺失要明确跳过**（与
//   `check_docs_numbers.py` 同一套纪律），否则红叉会训练人忽略真问题。
const path = process.argv[2] || 'D:/Documents_D/jianpu2/train-work/jptok_tokens.tsv';
if (!existsSync(path)) {
  console.log(`跳过：找不到 Python 侧的 token 表（${path}）`);
  console.log('  这个检查锁的是"JS 与 Python 两份 jptok 口径逐 token 一致"，需要先在**工具仓库**生成期望值:');
  console.log('    py -3.13 jianpu2/tools/dump_jptok_tokens.py');
  console.log('  然后: node tools/check_jptok_js.mjs jianpu2/train-work/jptok_tokens.tsv');
  process.exit(0);
}
// ⚠ 表可能是 CRLF(Windows 生成) -> 先按 \r?\n 拆行。第一版只 split('\n'), 于是**最后一列**
//   永远带着 '\r', `cols.includes('notes')` 判假、逐音比对被静默跳过(测试装置骗人, 最难查)。
const lines = readFileSync(path, 'utf8').split(/\r?\n/).filter((l) => l.trim());
const head = (lines.shift() ?? '').replace(/\s+$/, '');
if (!head || !head.startsWith('token\t')) {
  console.error(`表头不对(期望 token\\tis_note\\tis_pitch\\tduration_letter\\tbeat\\tnotes): ${head}`);
  process.exit(2);
}
const hasNotes = head.split('\t').includes('notes');
if (!hasNotes) {
  console.log('⚠ 这份 token 表没有 `notes` 列(逐音), 只比"认不认得出" —— ');
  console.log('  重新生成一次: py -3.13 jianpu2/tools/dump_jptok_tokens.py');
}

/** 前端逐音串(与 Python 侧 dump_jptok_tokens.py 的 notes_of 同口径)。
 *
 *  ⚠ 这里**必须带上八度**: 第一版只比"音级+变音", 于是我拿"把 octJoin 的逗号/撇对调"做反向
 *  验证时它**照样绿**(八度方向根本没进比对) —— 测试装置骗人比没测试更糟。八度用符号写:
 *  `^` = 高一个八度(oct=+1, 代码口径见 jptok.ts), `v` = 低一个八度, `^^`/`vv` 类推。
 *  实测 `,4,,b5,,3,,1` -> `4^,5b^^,3^^,1^^`。
 */
function notesOf(t) {
  const octMarks = (o) => (o > 0 ? '^'.repeat(o) : o < 0 ? 'v'.repeat(-o) : '');
  return parseTokenAll(t)
    .map((p) => (p.d === '0' || p.d === 'x'
      ? '_' + octMarks(p.oct)
      : p.d + (p.acc === 1 ? '#' : p.acc === -1 ? 'b' : '') + octMarks(p.oct)))
    .join(',');
}

// duration_letter: Python 侧取"前缀优先, 没有取后缀"; JS 里没有单独实现, 用 beat 反查即可,
// 所以这里只比 is_note / is_pitch / beat 三项(beat 已经把时值字母的口径包含进去了)。
let n = 0, bad = 0, nChord = 0;
const shown = [];
const EPS = 1e-9;
for (const ln of lines) {
  const [tok, isNote, isPitchPy, _dl, beatPy, notesPy] = ln.split('\t');
  n++;
  const jsNote = parseToken(tok) ? 1 : 0;
  const jsPitch = isPitch(tok) ? 1 : 0;
  const jsBeat = beat(tok);
  const pyBeat = Number(beatPy);
  const jsNotes = notesOf(tok);
  const chord = parseTokenAll(tok).length >= 2;
  if (chord) nChord++;
  const notesOk = !hasNotes || jsNotes === (notesPy ?? '');
  const ok = jsNote === Number(isNote) && jsPitch === Number(isPitchPy)
          && Math.abs(jsBeat - pyBeat) < EPS && notesOk;
  if (!ok) {
    bad++;
    if (shown.length < 20) {
      shown.push(`  ${JSON.stringify(tok)}  is_note py=${isNote} js=${jsNote} | `
               + `is_pitch py=${isPitchPy} js=${jsPitch} | beat py=${pyBeat} js=${jsBeat} | `
               + `notes py=${notesPy} js=${jsNotes}`);
    }
  }
}

console.log(`比过 ${n} 个不同 token（期望值来自 jptok.py; 其中和弦 token ${nChord} 个）`);
if (!bad) {
  console.log('\n前端 jptok.js 与 Python 侧 **逐项一致** ✓（含和弦 token 的**逐音**比对；'
            + '这份测试就是第三份口径的锁）');
} else {
  console.log(`\n!! ${bad} 处不一致（这正是 2026-09-28 那类"某一份口径偷偷漂了"的苗头）:`);
  for (const s of shown) console.log(s);
  if (bad > shown.length) console.log(`  …还有 ${bad - shown.length} 处`);
  process.exitCode = 1;
}

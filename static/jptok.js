/* 简谱 token 解析 —— 唯一实现(与 Python 侧 skills/jianpu-melody-lookup/jptok.py 同口径)
 *
 * token 形态:  [时值 cqsdh]* [,']* [#b♯♭]? [1-7x0] [,']* [#b♯♭]? [时值 cqsdh]* [.]* [\[\]]?
 * 变音: # ♯ = +1, b ♭ = -1, 无 = 0        八度: , = -1, ' = +1
 * 休止 0 / 念白 x: 不算音高(但仍是 token)
 *
 * 为什么要单独一个文件: 同一套白名单以前散在多处, 升降号口径各不相同 ——
 * 实测导致全库带 # 的音在索引里被整段丢掉, 检索永远匹配不上。口径只能有一份。
 *
 * ⚠ 2026-09-23: 时值字母**前后都可能在数字外**(`q3` 与 `6c.` 等价), 以前只认前缀,
 *   把 36 首手工录入谱的 40% 音符静默丢掉。改这里时**必须同时改 Python 侧**。
 */
const TOKEN = /^([cqsdh]*)([,']*)([#b♯♭]?)([1-7x0])([,']*)([#b♯♭]?)([cqsdh]*)(\.*)([\[\]]?)$/;

/** token -> {d: 音级 1-7 或 0/x, acc: -1/0/1, oct: 整数} 或 null */
export function parseToken(t) {
  const m = TOKEN.exec(t == null ? '' : String(t));
  if (!m) return null;
  const octs = m[2], acc = m[3], dig = m[4], post = m[5], acc2 = m[6] || '';
  const a = (acc === '#' || acc === '♯' || acc2 === '#' || acc2 === '♯') ? 1
          : (acc === 'b' || acc === '♭' || acc2 === 'b' || acc2 === '♭') ? -1 : 0;
  const off = (octs + post).split(',').length - 1 - ((octs + post).split("'").length - 1);
  return { d: dig, acc: a, oct: off };
}

/** 一个 token 占几拍(与 Python 侧 jptok.beat 同口径: 时值字母前后都认)。 */
export function beat(t) {
  const s = t == null ? '' : String(t);
  let letters = (s.match(/^[cqsdh]+/) || [''])[0];
  if (!letters) {
    const m = s.match(/([cqsdh]+)\.*[\[\]]?$/);
    letters = m ? m[1] : '';
  }
  // h = **六十四分音符**(0.0625 拍), 不是二分音符 —— 2026-09-24 在 Python 侧定案的,
  // 这里 2026-09-28 才跟上: 原来写 2.0, 与 jptok.py 漂了整整一轮(口径只能有一份)。
  // 定案证据见 jptok.py 的 BEAT 注释(项目自己的 jianpu-ly 里 types={"64th":"h", …})。
  const BEAT = { h: 0.0625, c: 1.0, '': 1.0, q: 0.5, s: 0.25, d: 0.125 };
  const v = Object.prototype.hasOwnProperty.call(BEAT, letters) ? BEAT[letters] : 0.0625;
  return s.endsWith('.') ? v * 1.5 : v;
}

/** 是**有音高**的音符(排除休止 `0` / 念白 `x`)。
 *
 * 索引里的音符序号(`at` / `bars` / `n`)只数这些 —— 休止与念白**不进**音高串。
 * 前端要按"第几个音符"定位时**必须**用它, 用 parseToken 会把休止也算进去,
 * 于是高亮和 `|` 整体前移(实测: th10_06 开头 `c0 q0` 被误标黑, 用户一眼看出)。
 */
export function isPitch(t) {
  const p = parseToken(t);
  return !!p && p.d !== '0' && p.d !== 'x';
}

/** 整段用户输入 -> [{d,acc,oct}]，只保留 1-7。
 *
 * **贪心**(左→右, 能多吃就多吃): 每个音先吃掉"前缀升降号/八度", 数字后面的升降号**只有
 * 后面不再是"八度* 数字"时**才归它。这样:
 *    `63731232#5` -> ...3 2 **#5**(最后那个 5 带升号), 而不是把 `#` 粘到前面的 `2` 上;
 *    `3#5`        -> 3 **#5**;      `6#` -> 6 带升号(后面没音了);      `#5` -> #5。
 * 用户 2026-09-25 实测报的就是 `63731232#5` 被解析成 `2#` + `5` 这个错。
 * 与音符无关的字符(空格/逗号分隔符/汉字…)一律跳过, 不影响贪心推进。
 */
export function parseQuery(raw) {
  const s = String(raw == null ? '' : raw);
  const out = [];
  // [前缀升降号] [八度] 数字 [后置八度/升降号 —— 仅当后面不是"八度* 数字"]
  // 后置的八度与升降号同规矩: `5,6` 的那个逗号归**后面**的 6(不是把 5 降八度), 与 `#5` 一致。
  const re = /([#b♯♭]?)([,']*)([1-7])(?:([,']+)(?![,']*[1-7]))?(?:([#b♯♭])(?![,']*[1-7]))?/y;
  for (let i = 0; i < s.length;) {
    re.lastIndex = i;
    const m = re.exec(s);
    if (!m) {          // 这个字符跟音符无关 -> 跳过一个, 继续贪心
      i += 1;
      continue;
    }
    const accs = m[1] + (m[5] || '');
    const a = /[#♯]/.test(accs) ? 1 : /[b♭]/.test(accs) ? -1 : 0;
    const o = m[2] + (m[4] || '');
    out.push({ d: +m[3], acc: a,
               oct: o.split(',').length - 1 - (o.split("'").length - 1) });
    i = re.lastIndex;
  }
  return out;
}

/** [{d,acc,oct}] -> 可读串，如 `6 3 7 #5` */
export function show(notes) {
  return notes.map((n) => (n.acc === 1 ? '#' : n.acc === -1 ? 'b' : '') + n.d).join(' ');
}

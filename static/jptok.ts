/* 简谱 token 解析 —— 唯一实现(与 Python 侧 skills/jianpu-melody-lookup/jptok.py 同口径)
 *
 * token 形态:  [时值 cqsdh]* [,']* [#b♯♭]? [1-7x0] [,']* [#b♯♭]? [时值 cqsdh]* [.]* \]?
 * **和弦 token**: 多个音**连写成一个**(见下面的第四次事故记录):
 *              [时值 cqsdh]* ( [,']* [#b♯♭]? [1-7x0] ){2,} [,']* [#b♯♭]? [时值 cqsdh]* [.]* \]?
 * 变音: # ♯ = +1, b ♭ = -1, 无 = 0        八度: , = -1, ' = +1(逐字累加, 与 Python 侧逐字一致)
 * 休止 0 / 念白 x: 不算音高(但仍是 token)
 *
 * 为什么要单独一个文件: 同一套白名单以前散在多处, 升降号口径各不相同 ——
 * 实测导致全库带 # 的音在索引里被整段丢掉, 检索永远匹配不上。口径只能有一份。
 *
 * ⚠ 2026-09-23: 时值字母**前后都可能在数字外**(`q3` 与 `6c.` 等价), 以前只认前缀,
 *   把 36 首手工录入谱的 40% 音符静默丢掉。改这里时**必须同时改 Python 侧**。
 * ⚠ 2026-09-28: 末尾**只能**有 `]`, **不能**有 `[` —— jianpu-ly 的三连音写作 `3[ 5 3 4 ]`,
 *   那个 `3[` 的 3 是连音数、`[`/`]` 是分组记号, **都不是音符**。原来允许 `[` 结尾, 于是
 *   `parseToken('3[')` 返回一个音, `3[ 5 3 4 ]` 被算成 **4 个音**。改这里时**必须同时改 Python 侧**。
 * ⚠ 2026-10-05 第四次(**和弦 token 被整批丢掉**): 语料里 223 首用和弦写法 —— 多个音连写成
 *   一个 token, 八度/变音写在**各自音级左边**(抄参考实现 vendor/jianpu_ly/__init__.py:1860
 *   `chordNotes_markup()` 调 :1802 `grace_octave_fix()`: 把写在数字右边的记号搬到左边)。
 *   实测全库 11,876 份里和弦 token 261,647 个、写明 646,747 个音(有音高 646,710 个)。
 *   原来 `TOKEN` 一个都匹配不上 -> 这 646,710 个音**从没进过检索索引**。
 *   修法与 Python 侧**逐字对齐**: 新增 `parseTokenAll()`(逐音), 单音 token **先**走原来那条
 *   TOKEN 正则、命中就直接返回 —— 于是单音输出**逐字节不可能变**; `parseToken()` 保持不变,
 *   只是遇到和弦 token 会返回**数组**(⚠ 调用方若写 `parseToken(t).d` 遇到和弦会 `undefined`)。
 *   验证: `tools/check_jptok_js.mjs`(逐 token 逐音比对 Python 侧的 token 表)必须通过。
 *
 * 2026-10-01 TypeScript 化（B 阶段）: 只加类型, **一个字的行为都没改** —— 转换后
 * `check_jptok_js.mjs`（21,711 token 逐项比对）与 `check_search.mjs` 必须照样通过。
 */
const TOKEN =
  /^([cqsdh]*)([,']*)([#b♯♭]?)([1-7x0])([,']*)([#b♯♭]?)([cqsdh]*)(\.*)(\]?)$/;

/** 和弦 token(**只在 TOKEN 匹配不上时才试它**, 与 Python 侧 jptok.CHORD 逐字同口径):
 *  时值字母在最前, 之后**连写**若干"八度/变音 + 音级", 附点在最末。
 *  `{2,}` 是硬性的: 只有一个音级的写法归上面那条 TOKEN 管 —— 这样"和弦分支"永远不会
 *  改变单音 token 的判定(这就是本次改动的兼容性保证)。
 *  末尾游离的八度/变音归**最后一个**音(与 TOKEN 里 acc2/oct2 归同一个音是同一套处理)。 */
const CHORD =
  /^([cqsdh]*)((?:[,']*[#b♯♭]?[1-7x0]){2,})([,']*)([#b♯♭]?)([cqsdh]*)(\.*)(\]?)$/;
/** 和弦体里切出每一个音: 八度记号 + 变音 + 音级 */
const NOTE = /([,']*)([#b♯♭]?)([1-7x0])/g;

/** 一个 token 的解析结果：音级（`1`-`7`，休止 `0`/念白 `x`）、变音、八度偏移。 */
export interface JpToken {
  d: string;
  acc: number;
  oct: number;
}

/** `parseToken()` 遇到和弦 token 时返回的**逐音**结果：数组长度 >= 2。 */
export type JpTokenList = JpToken[];

/** 用户输入解析出的音符（只有 1-7，带变音与八度）。 */
export interface Note {
  d: number;
  acc: number;
  oct: number;
}

/** 多个变音记号 -> +1/-1/0（升号优先, 与 Python 侧 `_acc_join` 逐字一致）。 */
function accJoin(...marks: (string | undefined)[]): number {
  const s = marks.filter(Boolean).join('');
  return /[#♯]/.test(s) ? 1 : /[b♭]/.test(s) ? -1 : 0;
}

/** 多个八度记号 -> 偏移（逗号 -1、撇 +1, 逐字累加; 与 Python 侧 `_oct_join` 逐字一致）。 */
function octJoin(...marks: (string | undefined)[]): number {
  const s = marks.filter(Boolean).join('');
  return (s.match(/,/g) ?? []).length - (s.match(/'/g) ?? []).length;
}

/** 一个音级 + 它的记号 -> {d, acc, oct}（`0`/`x` 的 d 原样保留, 与 Python 侧一致）。 */
function sound(dig: string, accs: string, octs: string, acc2 = '', oct2 = ''): JpToken {
  return { d: dig, acc: accJoin(accs, acc2), oct: octJoin(octs, oct2) };
}

/** token -> **它包含的每一个音**；不是合法 token 返回 `[]`。
 *
 *  单音 token 返回 1 项（与旧 `parseToken` 的结果逐字段相同）；**和弦 token** 返回它包含的
 *  每个音各一项, 顺序照 token 里的书写顺序（不排序）。这是"和弦 token 不再丢音"的落点:
 *  调用方遍历 token 时应当用它, 而不是只取第一项。口径与 Python 侧 `jptok.parse_token_all` 相同。
 */
export function parseTokenAll(t: string | null | undefined): JpTokenList {
  const s = t == null ? '' : String(t);
  const m = TOKEN.exec(s);
  if (m) {
    // 单音 token 走原路: 命中就直接返回, 输出不可能变
    return [sound(m[4] ?? '', m[3] ?? '', m[2] ?? '', m[6] ?? '', m[5] ?? '')];
  }
  const c = CHORD.exec(s);
  if (!c) return [];
  const body = c[2] ?? '';
  const oct2 = c[3] ?? '';
  const acc2 = c[4] ?? '';
  const parts: JpToken[] = [];
  // ⚠ 正则带 g 标志 -> 每次用之前把 lastIndex 归零, 否则第二次调用会从上次的位置接着扫。
  NOTE.lastIndex = 0;
  let mm: RegExpExecArray | null;
  const raw: [string, string, string][] = [];
  while ((mm = NOTE.exec(body)) !== null) raw.push([mm[1] ?? '', mm[2] ?? '', mm[3] ?? '']);
  for (let i = 0; i < raw.length; i++) {
    const [octs, acc, dig] = raw[i]!;
    const last = i === raw.length - 1;
    parts.push(sound(dig, acc, octs, last ? acc2 : '', last ? oct2 : ''));
  }
  return parts;
}

/** token -> {d, acc, oct}（**单音**）| 数组（**和弦**, >= 2 项）| null（不是合法 token）。
 *
 *  ⚠ 和弦分支返回的是**数组**: 老代码若写 `parseToken(t).d` 遇到和弦会得到 `undefined`
 *  —— 这是故意的, 免得又出现"整批丢音还没人发现"。要覆盖和弦请用 `parseTokenAll()`。
 */
export function parseToken(t: string | null | undefined): JpToken | JpTokenList | null {
  const got = parseTokenAll(t);
  if (got.length === 0) return null;
  return got.length === 1 ? got[0]! : got;
}

/** 一个 token 占几拍（与 Python 侧 `jptok.beat` 同口径：时值字母前后都认）。 */
export function beat(t: string | null | undefined): number {
  const s = t == null ? '' : String(t);
  let letters = (s.match(/^[cqsdh]+/) ?? [''])[0] ?? '';
  if (!letters) {
    const m = s.match(/([cqsdh]+)\.*[[\]]?$/);
    letters = m ? (m[1] ?? '') : '';
  }
  // h = **六十四分音符**(0.0625 拍), 不是二分音符 —— 2026-09-24 在 Python 侧定案的,
  // 这里 2026-09-28 才跟上: 原来写 2.0, 与 jptok.py 漂了整整一轮(口径只能有一份)。
  // 定案证据见 jptok.py 的 BEAT 注释(项目自己的 jianpu-ly 里 types={"64th":"h", …})。
  const BEAT: Record<string, number> = { h: 0.0625, c: 1.0, '': 1.0, q: 0.5, s: 0.25, d: 0.125 };
  const v = Object.prototype.hasOwnProperty.call(BEAT, letters) ? BEAT[letters]! : 0.0625;
  return s.endsWith('.') ? v * 1.5 : v;
}

/** 是**有音高**的音符（排除休止 `0` / 念白 `x`）。
 *
 * 索引里的音符序号（`at` / `bars` / `n`）只数这些 —— 休止与念白**不进**音高串。
 * 前端要按"第几个音符"定位时**必须**用它，用 `parseToken` 会把休止也算进去，
 * 于是高亮和 `|` 整体前移（实测：`th10_06` 开头 `c0 q0` 被误标黑，用户一眼看出）。
 * 和弦 token 里只要有**一个**有音高的音就算真（与 Python 侧 `jptok.is_pitch` 同口径）。
 */
export function isPitch(t: string | null | undefined): boolean {
  return parseTokenAll(t).some((p) => p.d !== '0' && p.d !== 'x');
}

/** 整段用户输入 -> `[{d,acc,oct}]`，只保留 1-7。
 *
 * **贪心**（左→右，能多吃就多吃）：每个音先吃掉"前缀升降号/八度"，数字后面的升降号**只有
 * 后面不再是"八度* 数字"时**才归它。这样：
 *    `63731232#5` -> …3 2 **#5**（最后那个 5 带升号），而不是把 `#` 粘到前面的 `2` 上；
 *    `3#5`        -> 3 **#5**；     `6#` -> 6 带升号（后面没音了）；     `#5` -> #5。
 * 用户 2026-09-25 实测报的就是 `63731232#5` 被解析成 `2#` + `5` 这个错。
 * 与音符无关的字符（空格/逗号分隔符/汉字…）一律跳过，不影响贪心推进。
 */
export function parseQuery(raw: string | null | undefined): Note[] {
  const s = String(raw == null ? '' : raw);
  const out: Note[] = [];
  // [前缀升降号] [八度] 数字 [后置八度/升降号 —— 仅当后面不是"八度* 数字"]
  // 后置的八度与升降号同规矩: `5,6` 的那个逗号归**后面**的 6(不是把 5 降八度), 与 `#5` 一致。
  const re =
    /([#b♯♭]?)([,']*)([1-7])(?:([,']+)(?![,']*[1-7]))?(?:([#b♯♭])(?![,']*[1-7]))?/y;
  for (let i = 0; i < s.length; ) {
    re.lastIndex = i;
    const m = re.exec(s);
    if (!m) {
      // 这个字符跟音符无关 -> 跳过一个, 继续贪心
      i += 1;
      continue;
    }
    const accs = m[1]! + (m[5] ?? '');
    const a = /[#♯]/.test(accs) ? 1 : /[b♭]/.test(accs) ? -1 : 0;
    const o = m[2]! + (m[4] ?? '');
    out.push({
      d: +m[3]!,
      acc: a,
      oct: o.split(',').length - 1 - (o.split("'").length - 1),
    });
    i = re.lastIndex;
  }
  return out;
}

/** `[{d,acc,oct}]` -> 可读串，如 `6 3 7 #5` */
export function show(notes: Note[]): string {
  return notes
    .map((n) => (n.acc === 1 ? '#' : n.acc === -1 ? 'b' : '') + n.d)
    .join(' ');
}

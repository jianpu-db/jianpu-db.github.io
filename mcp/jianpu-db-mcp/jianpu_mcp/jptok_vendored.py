# -*- coding: utf-8 -*-
"""简谱 token 的**唯一**解析实现(全项目只此一份, 不许再复制粘贴)。

为什么必须唯一: 同一个白名单正则原来散在 score.py / show_hit.py / lookup_*.py / 前端 search.js
里, 结果"升降号"这一处口径在三份代码里各不相同 —— 实测造成 **全库带 `#` 的音在 data.jsonl 里
被整段丢掉**(18 首受影响), 检索永远匹配不上, 而我查了三轮才定位到。教训: 口径只能有一份。

token 形态(变音与**时值**都前后可能, 末尾可带 `]` 分组闭记号):
    [时值 cqsdh]* [,']* [#b♯♭]? [1-7x0] [,']* [#b♯♭]? [时值 cqsdh]* [.]* \\]?
**和弦 token**: 多个音**连写成一个** token(见文件末的第四次事故记录):
    [时值 cqsdh]* ( [,']* [#b♯♭]? [1-7x0] ){2,} [,']* [#b♯♭]? [时值 cqsdh]* [.]* \\]?
变音: # ♯ -> +1;  b ♭ -> -1;  无 -> 0
八度: 逐字累加, 但**方向与简谱书面方向相反** —— 代码算的是 `逗号 - 撇`(见 parse_token),
      实测 `parse_token("1'")` 得 `(1, 0, -1)`, 即撇 = 低八度。检索口径不看八度(代价表只用音级+变音),
      无实际影响; 而改它会让**每一条带八度记号的单音 token 输出都变**, 破坏"单音 token 逐字节不变"
      的硬要求(还要同步 JS/Rust 两份实现与全库指纹) —— 所以这里选择**把文档改成描述代码**,
      不反向改代码。真要改方向, 单开一次全链路改动 + 重建索引。
休止 0 / 念白 x 不参与音高(但算 token)
和弦 token: 每个音各自带自己的八度/变音(写在**它自己的音级左边**), `parse_token_all()` 逐音返回。

⚠ 2026-09-23 第二次踩同一个坑: 上面的说明一直写着「时值前后都可能」, 但正则**只实现了前缀**。
   手工录入的 36 首(东方曲 + 校歌, status=ok)大量使用后缀形(`6c.` / `5s` / `3q` / `,6q` / `q3[`),
   它们 parse 返回 None, 被 seq()/toks_of() **静默跳过** —— 这些曲子索引里少了 40% 的音
   (最多的一首丢 91%), 旋律被打乱成另一首歌的样子。
   表现: th10_06 第 1 小节明明是 `3 3 5 6 5 6 5 3 2`(与用户人耳相符), 检索却看到 `3 3 5 6 3 2 5 5 6`。
   教训: **docstring 承诺的形态必须在正则里兑现**, 否则就是第二次"带 # 的音整段消失"。

⚠ 2026-09-28 第三次踩同一个坑(**三连音记号被当成音符**): jianpu-ly 的三连音写作
   `3[ 5 3 4 ]` —— 那个 `3[` 里的 3 是**连音数**(3 个音占 2 个音的时值), `[` / `]` 是分组记号,
   **都不是音符**。而本文件的白名单原来允许 token 末尾带 `[`, 于是
   `parse_token('3[')` 返回 `(3,0,0)`、`seq('3[ 5 3 4 ]')` 得到 **4 个音**(凭空多一个 3)。
   实测: 语料里有 66 处 `<时值>3[`(分在 4 首), 手抄 GT 里有 23 处 `3[` + 23 处 `]` —— 全被多算。
   修法: 末尾只允许 `]`, **不允许 `[`**(开记号永远不是音符)。
   注: 这只保证**音高/音数**正确; 连音组内部的 2/3 时值压缩没实现(那些音按各自写明的时值计时),
   影响面是那 4 首的**小节线显示**, 不影响检索。

⚠ 2026-10-05 第四次踩同一个坑(**和弦 token 被整批丢掉**): 语料里 223 首(status=midi)用和弦写法
   —— 多个音**连写成一个** token, 八度/变音写在**各自音级左边**。实测(全库 11,876 份, 2026-10-05 当天语料):
   和弦 token **261,647 个**、写明 **646,747 个音**(其中**有音高**、该进索引的 **646,710 个**,
   另 37 个是和弦里的休止 `0`/念白 `x`), 而改动前 `TOKEN` 正则**一个和弦 token 都匹配不上** ->
   `pitched()/seq()/toks_of()` 把它们**整批丢掉**, 这 646,710 个发声的音从来没进过检索索引。
   写法不是我推的, 是抄参考实现(项目自己的 vendored 副本): vendor/jianpu_ly/__init__.py:1860
   `chordNotes_markup()` 调 :1802 `grace_octave_fix()`, 后者把写在数字**右边**的时值/变音/八度搬到**左边**
   (中文歧义位置要头里声明 `OctavesBefore`, 见 :1819 `Ambiguous octave marks` 那条报错)。
   真实例子(出处见 `tools/check_chord_tokens.py` 的用例表):
       `d,4,,b5,,3,,1`(th03_04.txt: 低八度 4 ＋ 低两个八度降 5/3/1)
       `q'16`(th02_14.txt)      `s6,5.`(th03_08.txt)
   修法: 新增 `parse_token_all()`, 一个和弦 token 产出**它包含的每个音**; `seq()` 不再丢音。
   兼容性怎么保证的: 单音 token 仍然**先**走原来那条 `TOKEN` 正则, 命中就直接返回 ——
   于是它的输出**逐字节不可能变**; 只有 `TOKEN` 匹配不上(说明有 ≥2 个音级)才走和弦分支。
   实测对拍: `tools/check_chord_tokens.py` 拿全库 11,876 份 / 21,193 个不同 token 做前后逐项比对,
   差异**全部**落在和弦 token 与含和弦的那 223 份文件里(单音 token 变了 0 个、消失 0 个)。
"""

# ══════════════════════════════════════════════════════════════════════════
# **vendored 副本**（包内拷贝）—— 简谱 token 的**唯一**实现，运行时**不 import 包外任何文件**。
#
#   来源：D:\Documents_D\jianpu2\skills\jianpu-melody-lookup\jptok.py
#   同步：py -3.13 tools/sync_vendor.py        ← 上游一改就重跑这一条
#   校验：py -3.13 tools/check_vendor_sync.py  ← 逐函数 AST 比对，断言"算法一字未改"
#   本副本与上游的差异：**只有本段注释**（代码逐字节相同）。
#   上游 sha256：0a2dd0cfd3fc06eaa3ce5ae72714d352f9ca1877ba91e80d670ec6ed9fbae71b      上游 mtime：2026-10-05 22:13:41
# ══════════════════════════════════════════════════════════════════════════

import re

# 唯一白名单(整体匹配): 前时值 + 前八度 + 前变音 + 音级 + 后八度 + 后变音 + 后时值 + 附点 + 闭记号
# ⚠ 这里**只允许 `]`**: `[` 是**三连音开记号**(写作 `3[ 5 3 4 ]`, 那个 3 是连音数, 不是音符),
#   允许它就会把开记号算成一个音(实测语料 66 处、GT 46 处)。
TOKEN = re.compile(
    r"^(?P<pre>[cqsdh]*)"
    r"(?P<oct1>[,']*)"
    r"(?P<acc>[#b♯♭]?)"
    r"(?P<dig>[1-7x0])"
    r"(?P<oct2>[,']*)"
    r"(?P<acc2>[#b♯♭]?)"
    r"(?P<post>[cqsdh]*)"
    r"(?P<dot>[.]*)"
    r"(?P<mark>\]?)$"
)
# 旧名字保留(曾有脚本引用); 口径已合并成上面唯一一份, 不再有 strict/loose 之分
TOKEN_LOOSE = TOKEN

# 和弦 token(**只在 TOKEN 匹配不上时才试它**, 见 parse_token_all): 时值字母在最前, 之后
# **连写**若干"八度/变音 + 音级", 每个音的记号紧挨在它自己的音级左边(参考实现把右边记号搬到左边),
# 附点在最末。`{2,}` 是硬性的: 只有**一个**音级的写法归上面那条 TOKEN 管 —— 这样"和弦分支"
# 永远不会改变单音 token 的判定(这是本次改动的兼容性保证)。
# 末尾游离的 oct2/acc2 归**最后一个**音, 与 TOKEN 里 oct2/acc2 归同一个音是同一套处理。
CHORD = re.compile(
    r"^(?P<pre>[cqsdh]*)"
    r"(?P<body>(?:[,']*[#b♯♭]?[1-7x0]){2,})"
    r"(?P<oct2>[,']*)(?P<acc2>[#b♯♭]?)"
    r"(?P<post>[cqsdh]*)(?P<dot>[.]*)(?P<mark>\]?)$"
)
# 和弦体里切出每一个音: 八度记号 + 变音 + 音级
_NOTE = re.compile(r"([,']*)([#b♯♭]?)([1-7x0])")
BAR = ("-", "|", "~")


def _acc_join(*marks):
    """多个变音记号 -> +1/-1/0(升号优先, 与 TOKEN 分支原来的算法逐字一致)。"""
    s = "".join(x for x in marks if x)
    return 1 if any(c in ("#", "♯") for c in s) else (-1 if any(c in ("b", "♭") for c in s) else 0)


def _oct_join(*marks):
    """多个八度记号 -> 偏移(逗号 -1、撇 +1, 逐字累加; 方向见文件头说明)。"""
    s = "".join(marks)
    return s.count(",") - s.count("'")


def _sound(dig, acc, octs, acc2="", oct2=""):
    """一个音级 + 它的记号 -> (音级 int|None, 变音, 八度)。"""
    a, off = _acc_join(acc, acc2), _oct_join(octs, oct2)
    return (None, a, off) if dig in "0x" else (int(dig), a, off)


def parse_token_all(t):
    """-> [(音级, 变音, 八度), ...]; **不是 token 返回 []**。

    单音 token 返回 1 项(与旧 parse_token 的元组逐字节相同); **和弦 token** 返回它包含的
    **每一个音**各一项, 顺序照 token 里的书写顺序(不排序)。
    这是"和弦 token 不再丢音"的落点: 调用方遍历所有 token 时应当用它, 而不是只取第一项。
    """
    m = TOKEN.match(t or "")
    if m:                                  # 单音(token)走原路: 命中就直接返回, 输出不可能变
        g = m.groupdict()
        return [_sound(g["dig"], g["acc"], g["oct1"], g["acc2"], g["oct2"])]
    m = CHORD.match(t or "")
    if not m:
        return []
    g = m.groupdict()
    parts = _NOTE.findall(g["body"])        # [(八度记号, 变音, 音级), ...]
    out = []
    for i, (octs, acc, dig) in enumerate(parts):
        last = (i == len(parts) - 1)
        out.append(_sound(dig, acc, octs, g["acc2"] if last else "", g["oct2"] if last else ""))
    return out


def parse_token(t):
    """-> (音级 int|None, 变音 -1/0/1, 八度 int) | [(...), ...] | None。

    **单音 token** 返回元组(与 2026-10-05 之前**逐字节相同**); **和弦 token** 返回
    `[(...), ...]`(≥2 项, 每个音一项); 不是 token 返回 None。
    音级对 0(休止)/x(念白) 返回 None(音高层面忽略), 但调用方仍可用 is_note() 判断它是不是 token。

    ⚠ 和弦分支返回的是**列表**: 老代码若写 `d, a, o = parse_token(t)` 遇到列表会**当场报错**
    (而不是悄悄只取第一个音) —— 这是故意的, 免得又出现"整批丢音还没人发现"。要覆盖和弦请用
    `parse_token_all()`。
    """
    got = parse_token_all(t)
    if not got:
        return None
    return got[0] if len(got) == 1 else got


def is_note(t):
    return len(parse_token_all(t)) > 0


def is_pitch(t):
    """是音符(有音高), 排除休止/念白。和弦 token 里只要有**一个**有音高的音就算真。"""
    return any(p[0] is not None for p in parse_token_all(t))


def pitched(score, merge_ties=True):
    """整份谱 -> [(音级, 变音, 八度)]，只保留有音高的 token。

    `merge_ties=True`(默认) 时把**连音线** `X ~ X` 并成**一个音** —— 用户口径(2026-09-28):
        "`~` 要看作一个音符。"
    `~` 是连音线: 同一个音被唱长一些, 记谱写成两个音头 + 一条弧线, 人只会数成一个音。
    判据只用**音级 + 变音**(不看八度): 检索口径本来就不看八度; 而且实测语料 14661 个 `~` 里
    13207 个两侧完全相同、1452 个只差八度记号(转写把八度标歪了的连音线), 只有 **2 个**是真圆滑线
    (音级不同 -> 不并, 保留两个音)。
    (⚠ 上面这句"不看八度"说的是**连音线的判据**; 解析出来的八度照样原样带在结果里。)
    `~` 自己永远不是音符(`parse_token('~') is None`), 这里只管两个音头要不要并。
    想要"记谱上有几个音头"就传 `merge_ties=False`。

    ⚠ 2026-10-05: **一个和弦 token 产出它包含的每个音**(以前整批丢掉)。连音线只可能并**第一个**
    音头(`~` 后面紧跟的那一个); 和弦内部同时发声、互不相并 —— 那个"重复音"是**两个音头**,
    不是延长(实测语料里有 `'#1#1` 这种写法)。
    """
    out, tie, last_note = [], False, False
    for t in (score or "").split():
        if t == "~":
            tie = last_note               # 只有紧跟在音符后面的 `~` 才算连音线
            continue
        if t == "-" or re.match(r"^[cqsdh]+-$", t or ""):
            # `-` = **延长**前一个音: 它不打断连音线(`'1 - ~ '1` = 一个长音, 实测 th01_10 那种写法)
            continue
        notes = [p for p in parse_token_all(t) if p[0] is not None]
        if not notes:
            tie, last_note = False, False  # 休止/念白/别的记号都会打断连音线
            continue
        for i, p in enumerate(notes):
            if merge_ties and i == 0 and tie and out and (out[-1][0], out[-1][1]) == (p[0], p[1]):
                continue                   # 并掉这个音头(时值由前一个音承担)
            out.append(p)
        tie, last_note = False, True
    return out


def seq(score, merge_ties=True):
    """同 `pitched`(名字保留给老调用方)。默认并连音线 —— 全项目**只此一份**音高序列口径。"""
    return pitched(score, merge_ties=merge_ties)


def query(raw):
    """用户输入(可自由加空格/标点) -> [(音级, 变音, 八度)]。"""
    out = []
    for m in re.finditer(r"([#b♯♭]?)([,']*)([1-7])([,']*)([#b♯♭]?)", raw):
        acc, pre, dig, post, acc2 = m.groups()
        a = 1 if (acc in ("#", "♯") or acc2 in ("#", "♯")) else (-1 if (acc in ("b", "♭") or acc2 in ("b", "♭")) else 0)
        off = (pre + post).count(",") - (pre + post).count("'")
        out.append((int(dig), a, off))
    return out


def show(notes):
    """[(音级,变音,八度)] -> 可读串(如 `6 3 7 #5`)"""
    return " ".join(("#" if a == 1 else "b" if a == -1 else "") + str(d) for d, a, _o in notes)

# ---------------- 时值 / 小节线恢复(唯一实现) ----------------
# 为什么放这里: 拍值表一度在 score.py 里又写了一份(第五个"复制口径"), 而它同时被
# 前端、评测、小节线恢复共用 —— 只能有一份。
BEAT = {"h": 0.0625, "c": 1.0, "": 1.0, "q": 0.5, "s": 0.25, "d": 0.125}
# ⚠ 2026-09-24 定案: **h = 六十四分音符(1/64 拍)**, 不是二分音符。
#   原来写成 2.0 是笔误(`expand_keep_length` 的 docstring 里也这么写, 但那份 docstring
#   在"时值可前可后"上已经错过一次, 不能当规格用)。证据:
#     ① 项目自己的 jianpu-ly(jianpulocal/mxml2jp-main/jianpu-ly_patched.py:1106)
#        写着 types={"64th":"h", "32nd":"d", ..., "half":" -", "whole":" - - -"}
#        —— 二分音符是"数字 + 破折号", 语料里确实有 32 万个 `-` 在干这件事;
#     ② 251 首 jianpu-ly 转出来的 midi 曲里, h 成串出现(平均每串 4.36 个, 最长 1804 个,
#        形如 `h,,#4 h,,#4 …` 的密集低音走句) —— 按 64 分音符是 28 拍, 按二分音符是 3608 拍;
#     ③ OCR 曲里 h 后面紧跟 `-` 的只有 0.4%(若 h 是二分音符, 简谱会写 `1 -`);
#        51.8% 的 h 邻居是 16/32 分音符, 最长 17 个连续 h;
#     ④ `convert.py` 给 OCR 模型的提示里, 二分音符明确写 `1 -`, 时值只列 q/s/d —— 从没说 h 是二分。
#   影响面: 只影响含 h 的 354 首的 `bars`(小节线显示/高亮), 不影响检索(音高与升降号)。
DEFAULT_BEATS_PER_BAR = 4.0


def duration_letter(tok):
    """取出 token 的时值字母 —— **前**置优先, 没有就取**后**置(与 parse_token 同一口径)。

    实测语料: `c` 只以后置出现(`6c.`, 前缀 0 次), 后置 `q/s/d` 也有一千多个 ——
    以前 beat() 只读前缀, 这些音在小节线恢复里被当成 1 拍, 于是它们的 `bars` 也跟着错。
    """
    t = tok or ""
    m = re.match(r"^([cqsdh]+)", t)
    if m:
        return m.group(1)
    m = re.search(r"([cqsdh]+)[.]*[\[\]]?$", t)
    return m.group(1) if m else ""


def beat(tok):
    """一个 token 占几拍。h=2, c=1, 无时值=1(四分), q=.5, s=.25, d=.125; 附点 x1.5。
    时值字母**前后都认**(与 parse_token 同口径)。未知字母组合按最短算(宁可多落线, 不要漏)。"""
    v = BEAT.get(duration_letter(tok), None)
    if v is None:
        v = 0.0625
    return v * 1.5 if (tok or "").endswith(".") else v


def beats_per_bar_from(text, default=DEFAULT_BEATS_PER_BAR):
    """从谱面文本里找拍号(独立成行的 `n/d`), 返回每小节拍数。找不到用默认 4/4。"""
    m = re.search(r"(?m)^\s*(\d+)\s*/\s*(\d+)\s*$", text or "")
    if not m:
        return default
    try:
        return int(m.group(1)) * 4.0 / int(m.group(2))
    except ZeroDivisionError:
        return default


def tuplet_ratio(word):
    """`3[` 这类连音开记号 -> (num, fitIn); 不是记号返回 None。

    这是**抄参考实现**, 不是自己推的: jianpu-ly_patched.py:1786 里
        fitIn = int(word[:-1]); i = 2; while i < fitIn: i *= 2
        num = int(fitIn*3/2) if i == fitIn else int(i/2)
        notehead_markup.tuplet = (num, fitIn)      # 时值 × num/fitIn
    于是 `3[` -> fitIn=3, i=4, num=int(4/2)=2 -> 时值 ×2/3, 与它输出的 `\\times 2/3 { … }` 一致
    (train-work/gt/Lemon_用户.ly 里 20 处 `\\times 2/3` 就是这段源码渲染出来的)。
    组内**音符个数** = fitIn; `]` 收尾, 或者装满 fitIn 个(语料里 MIDI 转来的写法没有 `]`)。
    """
    m = re.match(r"^([1-9][0-9]*)\[$", word or "")
    if not m:
        return None
    fit_in = int(m.group(1))
    i = 2
    while i < fit_in:
        i *= 2
    num = int(fit_in * 3 / 2) if i == fit_in else int(i / 2)
    return (num, fit_in)


def recover_bars(sections, beats_per_bar, keep_explicit=True):
    """按拍号+时值恢复小节线。

    sections: [{'score': '音符 token 串'}, ...]  —— 已展开(时值显式)
    返回 [音符下标], 语义: 第 i 个音符**之前**有一条小节线。
    源里已有的 `|` 当强小节线(累加器强制归零), 处理弱起/不规则小节。

    ⚠ 2026-09-23 修正: **休止 `0` / 念白 `x` 也占拍, 必须计时**。
    原来这里是 `if not p or p[0] is None: continue  # 休止/念白: 保守不计时`,
    结果是"只数旋律音的拍子" —— 只要谱里有休止, 小节线就会**整体前漂**:
    th10_06 开头有 2.5 拍休止, 第一条线就早了 1.5 拍(用户一眼看出 `c0 q0 q3 q3 |` 不对)。
    改成计时后, th10_06 前三个小节正好各 4.0 拍:
        [c0 c- q0 q3 q3 q5] [c6. s5 s6 q5 q3 q2 q5] [c3 c- c- q3 q5] …
    即"2.5 拍休止 + 3 个八分音符弱起" —— 这才是这首曲子的真实小节。

    ⚠ 2026-09-28 补: **连音组内的时值要按 num/fitIn 缩放**(见 tuplet_ratio)。
    以前连音开记号 `3[` 被当成一个音(还占一整拍), 组内三个音按各自的字面时值计时 ——
    含连音的 4 首(th06_15 / qd1z_anthem / th10_06 / th075_34)小节线因此偏。
    现在: 开记号本身不占拍; 组内每个 token 的时值 ×num/fitIn; 装满 fitIn 个或遇 `]` 收组。
    ⚠ 2026-09-28 补 ②: **连音线 `X ~ X` 只算一个音**(与 `pitched()` 同一口径)。
    否则"第几个音符"的计数会比音高序列多出连音线的那些音头, 前端的小节线/高亮会整体错位
    (实测 3058 首带连音线)。
    """
    bars, n, acc = [], 0, 0.0
    ratio, left = 1.0, 0          # 连音比例与组内剩余个数
    tie, last_note = False, False  # 连音线(并与不并的口径见 pitched())
    prev_pitch = None              # 上一个音头(判连音线用)
    for sec in sections or []:
        for t in (sec.get("score") or "").split():
            if t == "|":
                if keep_explicit:
                    bars.append(n)
                acc = 0.0
                continue
            if t == "]":              # 连音组收尾
                ratio, left = 1.0, 0
                continue
            if t == "~":
                tie = last_note
                continue
            tp = tuplet_ratio(t)
            if tp:                    # 连音组开头(它自己不是音符, 不占拍)
                ratio, left = tp[0] / float(tp[1]), tp[1]
                continue
            if t == "-" or re.match(r"^[cqsdh]+-$", t or ""):
                # `-` = 延长一拍; `c-`/`q-` 是 KeepLength 补时值时的写法(全库 363 个)。
                # 以前因为白名单不认被整段丢掉 -> 这些歌的小节线会往前漂。
                # ⚠ 2026-09-28: 改成 `acc += beat(t)`。原来这里写死 `+= 1.0`, **不看字母**,
                #   于是 `q-`(半拍)被当成一整拍, 那一小节之后的线全部偏移。
                #   证据是参考实现, 不是推理: jianpu-ly 把 `q-` 渲染成 `\note-mod "–" c''8`(八分),
                #   tools/check_beats_vs_lilypond.py 在 4 首带 .ly 的谱里**逐例**对上 ——
                #   分歧次数正好等于各谱的 `q-` 个数(th01_03 5 / th02_05 4 / th02_01 1),
                #   而全库共 35 个 `q-`, 分布在 6 首(th17_13 12 / th10_06 8 / th01_03 5 /
                #   th07_13 5 / th02_05 4 / th02_01 1)。
                acc += beat(t) * ratio
                continue
            p_all = parse_token_all(t)
            if not p_all:
                tie, last_note = False, False  # 别的记号 -> 打断连音线
                continue                      # 根本不是 token(升降号之外的记号等)
            # ⚠ 2026-10-05: 和弦 token 要**逐音**推进"第几个音符"(与 pitched() 同一口径),
            #   否则前端按音符下标插的小节线在这 223 首里会整体错位。时值只在 token 上加一次。
            pitches = [q for q in p_all if q[0] is not None]
            for i, p in enumerate(pitches):
                if i == 0 and tie and n > 0 and prev_pitch and prev_pitch[:2] == (p[0], p[1]):
                    pass                          # 连音线的第二个音头: 不推进"第几个音符"
                else:
                    n += 1                        # 只有**有音高**的音才推进"第几个音符"
                prev_pitch = p
            acc += beat(t) * ratio            # 休止/念白同样占拍: 不记时会让小节线前漂
            tie, last_note = False, bool(pitches)
            if left > 0:
                left -= 1                     # 连音组内: 装满就自动收组(语料里没有 `]` 的写法)
                if left == 0:
                    ratio = 1.0
            if acc >= beats_per_bar - 1e-9:
                bars.append(n)
                acc = 0.0
    # ⚠ 2026-09-28: **去掉相邻重复**。休止/念白占拍但不推进 n, 所以当"一串休止跨过整小节"时,
    #   同一个音下标会被落两次线 —— 实测 101.txt 得到 [1,3,6,7,7,8,8,…](语料里 3044 首有这种
    #   重复), 前端会把它渲染成一个**空小节**。"第 i 个音符之前有两条线"没有意义, 去重是对的。
    #   (开头的 0 **保留**: 它表示"第一小节整小节是休止", 显示出来是有信息的。)
    out = []
    for b in bars:
        if not out or out[-1] != b:
            out.append(b)
    return out

# -*- coding: utf-8 -*-
"""旋律检索: 按数字串反查"这是哪首歌" —— 给机器人/命令行用。

口径(与前端检索、技能 lookup.py 的"只比音高数字"这一层一致):
  * 只看**音高数字 1-7**; 时值(`q`/`s`/`c`/`d`/`h` 前后缀)、八度(`'`/`,`)、附点 `.`、
    变音记号(`#`/`b`)一律**不看**;
  * 休止/念白(`0`/`x`/`q0`/`sx`)、延长线 `-`/`~`、调号 `1=C` **不进**数字串;
  * 连续子串匹配: 查的这串数字必须在歌里**连着出现**(休止不算断开, 因为休止不进数字串)。
  * **token 口径只有一份**: 用 `skills/jianpu-melody-lookup/jptok.py`(全库唯一实现)切 token,
    不再自己写正则 —— 自写的窄正则会静默丢掉 `b7`(降号)与 `6c.`(后缀时值)这类 token,
    正是 2026-09-23 "索引丢音" 事故同一类坑。实测: jptok 解出 **1,301,430** 个音,
    与前端索引 `stats.json` 的 notes 数**完全一致**。

⚠ 2026-09-24 换掉语料来源: 老版本搜的是 `batch-out/*.txt`(旧流水线的中间产物,
  8495 个文件, 里面**没有 th10_06**, 连 `33565653253` 这种确定存在的查询都返回"命中 0 首")。
  现在直接搜 `jianpu-db/data.jsonl`(唯一真源, 11384 首, status ∈ {ok,ocr,converted})——顺带能给出
  曲名/歌手/出处/记谱文件, 机器人回话才有用。

速度: 冷启(读 13MB jsonl + jptok 切 130 万个 token)约 6 秒; 所以默认在
  `~/.cache/jianpu/melody_index.json` 存一份数字索引, 用 `data.jsonl` 的 size+mtime_ns
  + `TOKVER` 做指纹 —— 语料一变就自动重建(不做"看着像"的过期缓存), 之后每次查询约 0.3 秒。
  `--no-cache` 可强制不用缓存。

用法:
  python3 tools/melody_search.py 33565653253
  python3 tools/melody_search.py 33565653253 --fuzzy 1 --top 5
  python3 tools/melody_search.py 63731232 --json          # 给机器人解析
  python3 tools/melody_search.py 63731232 --json --top 0  # **全部**命中(机器人默认这么调;
                                                          #  它自己再决定分几条消息发)
  python3 tools/melody_search.py "63731232 1765"          # 多段(空格/逗号/竖线分隔): 每段都要出现

多段查询的口径(2026-09-24 用户纠正"316 316 31656564"之后定的): 空格是**"这一段我记不清"**,
  不是"两首不同的歌"。所以先按**先后顺序**把各段拼成**一句话**找(段与段之间允许夹 <= MAX_GAP
  个没哼出来的音), 命中的是**整句**, 显示的就是**包含全部命中音的最小连续小节**; 拼不成一句
  (各段离得太远)才退回"每段各自最近的一处"。反例正是《路灯下的小姑娘》: 老版本把 `316 316`
  对齐到引子(第 2 小节)、`31656564` 对齐到副歌(第 30 小节), 看起来像两处凑出来的假命中;
  实际整句就在副歌: `3 1 6 | 3 1 6 | 3 1 6 5 6 5 | 6 4`(= "亲爱的 小妹妹 请你不要不要哭泣")。
"""

# ══════════════════════════════════════════════════════════════════════════
# **vendored 副本** —— 本文件是权威匹配器（jianpu2/tools/melody_search.py）的**包内拷贝**，
# 运行时**不 import 包外任何文件**；算法一字未改，改动**只有下面这三处**：
#
#   ① 本段说明（纯注释，不含代码）；
#   ② `HERE/PKG/DB/DATA` 的默认路径：上游是"三个仓库的上一层"，这里是"包内 corpus 优先"；
#   ③ `sys.path.insert(...) + import jptok` -> 包内 `jptok_vendored`（仍绑定到同名变量 `jptok`，
#      所以下文所有用到 `jptok` 的地方**一个字都没动**）。
#
#   来源：D:\Documents_D\jianpu2\tools\melody_search.py
#   同步：py -3.13 tools/sync_vendor.py        ← 上游一改就重跑这一条
#   校验：py -3.13 tools/check_vendor_sync.py  ← 逐函数 AST 比对，断言"算法一字未改"
#   上游 sha256：9ab45edb0e9ceebc18e6bb6d84ffae96877289031e0dcad1d133b54eaaf5e5b1      上游 mtime：2026-10-06 02:14:56
# ══════════════════════════════════════════════════════════════════════════

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # jianpu_mcp/（包内）
PKG = os.path.dirname(HERE)                        # 包根
# 默认语料：`JIANPU_DB` 环境变量 > 包内 `corpus/`（离线模式用的就是包内那份）
DB = os.environ.get("JIANPU_DB") or os.path.join(PKG, "corpus")
DATA = os.path.join(DB, "data.jsonl")
# 与 parse_scores 的白名单同一份口径。2026-10-06 作者定: `converted`(**ABC 等记谱格式机械转换**
#   来的)也进检索, 与 `midi`(MIDI 硬转)区分开 —— 它带完整音高记录, 且转换器已按本仓库首调口径
#   (大调 Do-based / 小调 La-based, 主音 `,6`)写出来源与调号。
# 为什么必须加: 漏了这一档, 刚入库的 492 首**建索引时被整行跳过**, 用户查歌永远查不到它们,
#   而且不报错 —— 正是 2026-09-23 "索引丢音" 事故同一类静默缺口。
OK_STATUS = ("ok", "ocr", "converted")
TOKVER = 5                                        # 切 token / 行结构 / **索引字段**改了就 +1(缓存自动失效)
                                                  # v4: 索引多了 `sec`(段落权重用), 老缓存没这个键
                                                  # v5: 白名单加 `converted` + 和弦 token 不再打崩 digits_of,
                                                  #     索引内容变了 -> 老缓存必须作废(否则指纹不变、仍旧索引)
MAX_GAP = 8                                       # 整句对齐时, 段与段之间最多允许夹几个音(空格=记不清)
# 段落权重(用户 2026-09 定的规格, 见 jianpu2/README_PIPELINE.md §六「段落加权」):
#   chorus/refrain 1.6 · verse 1.25 · pre-chorus/bridge/interlude 1.10 · score 1.00 ·
#   intro/outro/layer/crazy-piano 0.80; 组合标签(如 `intro,chorus`)取**最大**; 不认识的当 1.00。
# 为什么要有它: 用户哼的多半是副歌那一句; 前奏/尾奏/"发狂钢琴"这种炫技段被记住的概率低。
SEC_W = {"chorus": 1.6, "refrain": 1.6, "verse": 1.25, "pre-chorus": 1.10, "bridge": 1.10,
         "interlude": 1.10, "score": 1.00, "intro": 0.80, "outro": 0.80, "layer": 0.80,
         "crazy-piano": 0.80}
SEC_CN = {"chorus": "副歌", "refrain": "副歌", "verse": "主歌", "pre-chorus": "前副歌", "bridge": "桥段",
          "interlude": "间奏", "score": "整曲", "intro": "前奏", "outro": "尾奏", "layer": "过渡",
          "crazy-piano": "发狂钢琴", "maybe-rap": "说唱?"}


def sec_weight(name):
    """段落名 -> 权重。组合标签取最大; 空/未知 1.00(口径: 没标就当整曲)。"""
    if not name:
        return 1.00
    return max([SEC_W.get(x.strip().lower(), 1.00) for x in str(name).split(",") if x.strip()] or [1.00])


def sec_label(name):
    """给回话用的中文名(组合标签用 `/` 连)。"""
    if not name:
        return ""
    return "/".join(SEC_CN.get(x.strip().lower(), x.strip()) for x in str(name).split(",") if x.strip())


def section_map(r, n_notes):
    """把 sections[] 摊成"每个音高音符属于哪个段"的 RLE: [[起始下标, 段落名], …]。

    口径: data.jsonl 的 `score` 就是各段 `score` 按顺序拼起来的(已实测逐首相等) —— 所以
    按段内**音高音符数**累加即可对上 digits 的下标。对不上(总数不符)就退回"没有分段"(全 1.0),
    绝不让错位的分段把权重安到别的音上。
    """
    secs = r.get("sections") or []
    if not secs:
        return []
    out, off = [], 0
    for s in secs:
        name = (s.get("subtitle") or "").strip() or "score"
        cnt = len(digits_of(s.get("score") or ""))
        if cnt:
            out.append([off, name])
            off += cnt
    if off != n_notes:                     # 段内音数之和与整串不符 -> 宁可不用分段
        return []
    return out if any(nm != "score" for _i, nm in out) else []


def sec_at(sec, i0, n):
    """命中区间 [i0, i0+n) 覆盖到的段落名(取权重最大的那个)。"""
    if not sec:
        return ""
    names = []
    for k, (start, nm) in enumerate(sec):
        end = sec[k + 1][0] if k + 1 < len(sec) else 10 ** 9
        if start < i0 + n and end > i0:      # 与命中区间有交集
            names.append(nm)
    if not names:
        return ""
    return max(names, key=sec_weight)


OCC_CAP = 96                                      # 每段最多收集多少处出现(只影响显示/对齐, 不影响排名用的最小不同数)
ALIGN_MAX = 300                                   # 只给排序后靠前的这些命中做整句对齐(剩下的照旧给最小连续小节)
CACHE = os.environ.get("JIANPU_MELODY_CACHE") or os.path.join(
    os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache"),
    "jianpu", "melody_index.json")

try:
    from . import jptok_vendored as jptok           # 包内 vendored 副本（唯一 token 实现）
except ImportError:                                # 被当脚本直接跑：sys.path[0] 就是本目录
    try:
        import jptok_vendored as jptok
    except Exception:                              # 独立部署时可能没有 -> 兜底正则(同口径)
        jptok = None
except Exception:
    jptok = None
# 兜底: 与 jptok 同口径 —— 变音记号在数字**前面**, 后缀时值(`6c.`)也要认出来
TOK = re.compile(r"^[,']*[cqsdh]*[,']*[#b♯♭]?([1-7])")


def digits_of(text):
    """token 流 -> 纯数字串(丢掉时值/八度/附点/变音/休止/调号)。"""
    return "".join(d for d, _o in _tokens(text))


def _tokens(text):
    """token 流 -> [(音高数字, 八度偏移), ...]

    ⚠ 2026-10-06: `jptok.parse_token()` 对**和弦 token**(一个 token 里写了好几个音, 如 ABC 转换
      产物里的 `,31`/`q13`)返回的是**列表**(每音一项), 老代码写 `str(tok[0]), int(tok[2])` 会在
      建索引时**当场 IndexError** —— 实测: 492 首 converted 里 30 首因此进不了索引。
      所以这里走 `parse_token_all()`(jptok 文档指定的"覆盖和弦"接口), 把 token 里的音**全部**展开。
      token 里的休止/念白(`0`/`x`, 音级解出 None)**不进数字串**: 口径是"休止不算断开"(见文件头),
      若把 None 记成 `"None"` 或占位, 数字串会被污染、段落下标也会跟着错位。
    """
    out = []
    for t in (text or "").split():
        if jptok is not None:
            for dig, _acc, off in jptok.parse_token_all(t):   # 单音 token -> 1 项; 和弦 token -> 每音 1 项
                if dig is None:                              # 休止 0 / 念白 x
                    continue
                out.append((str(dig), int(off)))
            continue
        if "=" in t:                              # 调号 `1=C` 里的 1 不是音符
            continue
        m = TOK.match(t)
        if m:
            out.append((m.group(1), 0))
    return out


def digits_of_file(path):
    """老接口(收的是文件路径), 保留给外部脚本用。"""
    with open(path, encoding="utf-8", errors="replace") as f:
        return digits_of(f.read())


def _fingerprint(path):
    st = os.stat(path)
    return {"path": os.path.abspath(path), "size": st.st_size, "mtime_ns": st.st_mtime_ns}


def build_corpus(path=DATA):
    """data.jsonl -> 可检索的索引(一行一首, 带够机器人回话用的元数据)。"""
    rows = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            st = r.get("status")
            st = st if isinstance(st, list) else [st]
            if not any((x or "") in OK_STATUS for x in st):
                continue
            d = digits_of(r.get("score") or "")
            if not d:
                continue
            src = r.get("source") or ""
            src = src[0] if isinstance(src, list) else src
            f0 = (r.get("file") or [""])[0]
            rows.append({
                "title": r.get("title") or "",
                "artist": list(r.get("artist") or []),
                "tag": list(r.get("tag") or []),
                "source": src,
                "site": (src.split("-")[0] if src else ""),
                "file": f0,
                "id": src or ("f-" + os.path.splitext(f0)[0]),
                "n_notes": int(r.get("n_notes") or 0),
                "status": (st[0] or ""),
                "digits": d,
                "score": r.get("score") or "",
                "bars": [int(x) for x in (r.get("bars") or []) if isinstance(x, int)],
                "sec": section_map(r, len(d)),      # [[起始音下标, 段落名], …]; 空=没分段
            })
    return rows


def _hotmap(rows):
    """知名度代理: 某个名字(歌手/标签, 不含「分类/…」)在语料里出现在多少首里。

    公式与 jianpu-web/tools/build_web_data.py、skills/jianpu-melody-lookup/lookup.py **必须一致**。
    """
    m = {}
    for r in rows:
        names = list(r.get("artist") or []) + [t for t in (r.get("tag") or []) if not str(t).startswith("分类/")]
        for n in names:
            m[n] = m.get(n, 0) + 1
    return m


def _annotate(rows):
    """给每行补 pop(同曲名组份数) 与 hot(歌手/标签在语料里的谱数) —— 并列时按它们排序。"""
    hot = _hotmap(rows)
    pop = {}
    for r in rows:
        pop[r["title"]] = pop.get(r["title"], 0) + 1
    for r in rows:
        names = list(r.get("artist") or []) + [t for t in (r.get("tag") or []) if not str(t).startswith("分类/")]
        r["pop"] = pop.get(r["title"], 0)
        r["hot"] = max([hot.get(n, 0) for n in names] or [0])
    return rows


def load_corpus(path=DATA, use_cache=True):
    """带缓存地建索引: 指纹(size+mtime_ns+TOKVER)不符就重建 —— 绝不用过期的数字串。"""
    try:
        fp = _fingerprint(path)
    except OSError:
        return _annotate(build_corpus(path))
    if use_cache:
        try:
            with open(CACHE, encoding="utf-8") as f:
                c = json.load(f)
            if c.get("ver") == TOKVER and c.get("src") == fp and c.get("rows"):
                return c["rows"]        # 热度/知名度已随缓存存下来了
        except Exception:
            pass
    rows = build_corpus(path)
    _annotate(rows)
    if use_cache:
        try:
            os.makedirs(os.path.dirname(CACHE), exist_ok=True)
            tmp = CACHE + ".%d.tmp" % os.getpid()
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"ver": TOKVER, "src": fp, "rows": rows}, f, ensure_ascii=False)
            os.replace(tmp, CACHE)
        except Exception:
            pass                                     # 缓存写不了不算错(只读盘也能用)
    return rows


def find(needle, hay, fuzzy=0):
    """在 hay 里找 needle: 返回 (位置, 不同数) 或 None。fuzzy=0 就是子串查找。

    fuzzy>0 时逐窗口比。**边比边数**: 一旦超过 fuzzy 就跳出 —— 老版本每窗口都把整段
    加完再判断, 长查询会慢到不可用。
    """
    if fuzzy <= 0:
        pos = hay.find(needle)
        return (pos, 0) if pos >= 0 else None
    best = None
    n, m = len(hay), len(needle)
    for i in range(n - m + 1):
        diff = 0
        for k in range(m):
            if hay[i + k] != needle[k]:
                diff += 1
                if diff > fuzzy:
                    break
        if diff <= fuzzy and (best is None or diff < best[1]):
            best = (i, diff)
            if diff == 0:
                break
    return best


def find_all(needle, hay, fuzzy=0, cap=OCC_CAP):
    """needle 在 hay 里的**所有**出现(按位置升序, 每处带不同数) + 全曲最小不同数。

    返回 (occs, best_diff); 找不到时 ([], None)。fuzzy=0 走 `str.find`(C 速度, 出现再多也快);
    fuzzy>0 时一趟扫完: `cap` 只限制**收集**多少处, 最小不同数照样全曲算完 —— 排名口径不因 cap 变。
    """
    out, best = [], None
    n, m = len(hay), len(needle)
    if m == 0 or m > n:
        return out, best
    if fuzzy <= 0:
        pos = hay.find(needle)
        while pos >= 0:
            if len(out) < cap:
                out.append((pos, 0))
            pos = hay.find(needle, pos + 1)
        return out, (0 if out else None)
    for i in range(n - m + 1):
        diff = 0
        for k in range(m):
            if hay[i + k] != needle[k]:
                diff += 1
                if diff > fuzzy:
                    break
        if diff <= fuzzy:
            if best is None or diff < best:
                best = diff
            if len(out) < cap:
                out.append((i, diff))
    return out, best


def align_phrase(segs, occs, max_gap=MAX_GAP):
    """把各段按**先后顺序**拼成一句话: 返回 [(位置, 长度), ...]; 拼不成返回 None。

    状态是 (各段位置, 起点, 终点, 不同数之和); 每走一段只保留"终点 + 不同数和"最优的若干条,
    免得长查询把状态撑爆。挑法: 不同数和少优先, 其次**跨度小**(这才是"最小连续小节"该有的样子),
    最后取靠前的。段与段允许夹 <= max_gap 个音(用户把记不清的地方敲成了空格)。
    """
    if not segs or not occs or any(not o for o in occs):
        return None
    if len(segs) == 1:
        p, _d = occs[0][0]
        return [(p, len(segs[0]))]
    states = [((p,), p, p + len(segs[0]), d) for p, d in occs[0]]
    for j in range(1, len(segs)):
        L = len(segs[j])
        nxt = []
        for path, st, end, ds in states:
            for q, dq in occs[j]:
                if q < end:
                    continue
                if q - end > max_gap:                  # occs 升序: 再往后只会更远
                    break
                nxt.append((path + (q,), st, q + L, ds + dq))
        if not nxt:
            return None
        nxt.sort(key=lambda s: (s[3], s[2] - s[1], s[1]))
        seen_end, keep = set(), []                     # 同一个终点只留最优的一条
        for s in nxt:
            if s[2] in seen_end:
                continue
            seen_end.add(s[2])
            keep.append(s)
            if len(keep) >= 256:
                break
        states = keep
    best = min(states, key=lambda s: (s[3], s[2] - s[1], s[1]))
    return [(p, len(s)) for p, s in zip(best[0], segs)]


def bar_span(bars, i0, n):
    """包含命中音符 [i0, i0+n) 的**最小连续小节**。

    返回 (音符下标区间 [b0, b1), 命中首音的小节号, 命中末音的小节号)。
    小节号 1 起。`bars` 记的是"第 i 个音符之前有一条小节线"(与前端同一口径);
    第一个音符(下标 0)之前的小节线可能没记 ── 那就当成第 1 小节的开头。
    """
    bs = sorted({int(b) for b in (bars or [])})
    b0 = 0
    for b in bs:
        if b <= i0:
            b0 = b
        else:
            break
    b1 = None
    for b in bs:
        if b > i0 + n - 1:
            b1 = b
            break
    if b1 is None:
        b1 = 10 ** 9
    return b0, b1, sum(1 for b in bs if b <= i0) or 1, sum(1 for b in bs if b <= i0 + n - 1) or 1


def span_text(r, i0, n, marks=None, limit=240):
    """音符区间 [i0, i0+n) + **包含它的完整小节** -> 带 `|` 的原文片段, 命中块各自用【】圈出来。

    为什么要有它(用户口径 2026-09-24): "第多少音起说了跟没说一样。至少要把匹配的音的所有
    包括它的最小连续小节打出来。" —— 只给一个序号, 人没法核对; 给"这几个小节", 一眼就能对上。
    marks = [(位置, 长度), ...]: 整句对齐时每段各圈一个【】, 段之间没对上的音**照原样留着**
    (不能吞掉, 否则看不出"整句"到底连不连得上)。
    """
    toks = (r.get("score") or "").split()
    idx = [k for k, t in enumerate(toks) if is_pitch(t)]
    if not idx or i0 + n > len(idx):
        return "", 0, 0
    marks = marks or [(i0, n)]
    opens = {p for p, _k in marks}
    closes = {p + k - 1 for p, k in marks}
    b0, b1, nb0, nb1 = bar_span(r.get("bars"), i0, n)
    b1 = min(b1, len(idx))
    bar_set = {int(b) for b in (r.get("bars") or [])}
    parts = []
    for k in range(b0, b1):                        # 第 k 个音符
        if k in bar_set and parts:
            parts.append("|")
        parts.append(("【" if k in opens else "") + toks[idx[k]] + ("】" if k in closes else ""))
    out = " ".join(parts)
    if len(out) > limit:                           # 极长的小节: 只截中间, 两头保留
        out = out[:limit // 2] + " … " + out[-limit // 2:]
    return out, nb0, nb1


def segment_text(r, i0, n, limit=240):
    """单段命中: 等价于只圈一个【】的 span_text(老调用方沿用这个名字)。"""
    return span_text(r, i0, n, [(i0, n)], limit)


def is_pitch(t):
    """有音高的 token(休止/记号不算) —— 与检索口径同一份实现(jptok)。"""
    if jptok is not None:
        return bool(jptok.is_pitch(t))
    return bool(TOK.match(t))


def split_query(s):
    """查询串 -> 段。**与网页端口径统一(2026-09-25 用户要求)**: 分隔符只给人看, 不进匹配。

    网页的 `jptok.parseQuery` 是**整串逐字贪心扫**, 空格/逗号直接跳过 —— 也就是说
    `66563 31233` 在网页上找的是**连续的** `6656331233`。而这里原来是按空格切段、
    每段各自满曲找一处, 于是两边对同一串给出不同答案:
      `66563 31233` 网页第 1 名《当那一天来临》(它真有连续这 10 个音),
      分段版却把《草原上的小河》也当精确命中(它的 `31233` 在第 71 位、`66563` 在第 143 位, **倒着的**),
      而且因为该曲名组份数=2 还排到了前面。
    所以这里改成: 去掉非 1-7 的字符后**整串作为唯一一段**。
    (副作用: "某处记不清就空开"那种用法不再有分段容忍度 —— 与网页行为一致。)
    """
    d = re.sub(r"[^1-7]", "", s or "")
    return [d] if d else []


def hit_detail(r, segs, det, fuzzy=0, do_align=True):
    """给一条命中补上"要给人看的东西": 整句(能拼成)或每段各自的最小连续小节。

    整句: 只有**一个**片段 —— 包含全部命中音的最小连续小节, 每段各圈一个【】;
    拼不成一句(各段离得太远): 退回"每段各自最近的一处", 每段都要能核对, 不能只给第一段。
    """
    marks = None
    if do_align and len(segs) > 1:
        occs = []
        for j, s in enumerate(segs):
            occ, _best = find_all(s, r["digits"], fuzzy)
            occs.append(occ or [det[j]])
        marks = align_phrase(segs, occs)
    if marks:
        i0 = marks[0][0]
        n = marks[-1][0] + marks[-1][1] - i0
        t, n0, n1 = span_text(r, i0, n, marks)
        details = [{"seg": t, "bar_from": n0, "bar_to": n1, "pos": i0, "n": n,
                    "aligned": True,
                    "marks": [{"pos": p, "n": k} for p, k in marks]}]
        positions = [p for p, _k in marks]
    else:
        details, seen, positions = [], {}, []
        for j, (p0, _d) in enumerate(det, 1):
            sgm = segs[j - 1]
            positions.append(p0)
            t, n0, n1 = segment_text(r, p0, len(sgm))
            key = (n0, n1, t)
            if key in seen:                        # 两段落在同一处: 别重复印, 标一下就行
                details.append({"seg": "", "same_as": seen[key], "n": len(sgm),
                                "bar_from": n0, "bar_to": n1, "pos": p0, "aligned": False})
                continue
            seen[key] = j
            details.append({"seg": t, "bar_from": n0, "bar_to": n1, "pos": p0, "n": len(sgm),
                            "aligned": False})
    # 段落: 按**展示出来的那段**算(整句就是整句跨度), 中文名给回话用。
    # ⚠ 先把段落表存成局部变量再往 r 上写 —— r["sec"] 这一格要留给**回话用的段落名**,
    #   2026-09-25 就是先覆盖成字符串、后面又拿它当表用, 直接 unpack 崩。
    secmap = r.pop("_secmap", None)
    if secmap is None and isinstance(r.get("sec"), (list, tuple)):
        secmap = r["sec"]

    def _sec_cn(pos, n):
        return sec_label(sec_at(secmap, pos, n or 1))

    for dt in details:
        if dt.get("seg"):
            dt["sec_cn"] = _sec_cn(dt["pos"], dt.get("n"))
    sec_name = sec_at(secmap, positions[0], (details[0].get("n") or 1))
    r["sec"] = sec_name
    r["sec_cn"] = sec_label(sec_name)
    r["sec_w"] = sec_weight(sec_name)
    r["pos"] = positions[0]
    r["positions"] = positions
    r["seg"] = details[0]["seg"]
    r["bar_from"] = details[0]["bar_from"]
    r["bar_to"] = details[0]["bar_to"]
    r["segs_detail"] = details
    return r


def search(rows, segs, fuzzy=0, top=20):
    """每一段都必须在同一首里找到(多给几段更准); 能拼成一句就按**整句**报。

    排序(与网页前端、技能 lookup.py 同一份口径): 不同数少 -> 热度(同曲名组份数) -> **知名度**
    (歌手/标签在语料里的谱数) -> 谱短 -> 曲名。热点说明: `66561232123` 精确命中《最炫民族风》
    (凤凰传奇, 库里 68 首) 与《时光》(无歌手信息, 0 首) —— 用户判定正确答案是前者,
    而"名短优先/八度记号少优先"都判给了后者。

    两遍走: 第一遍只算排名要用的"每段最小不同数"(早退, 极快), 排完序**只给要回话的那几条**
    做整句对齐 —— 否则 `1 1` 这种顺手一敲的查询要对全库 7 千首做一遍对齐, 白白慢十几秒。
    """
    segs = [s for s in (segs or []) if s]
    if not segs:
        return []
    hits = []
    for r in rows:
        d = r["digits"]
        if any(len(s) > len(d) for s in segs):
            continue
        det, ok = [], True
        for s in segs:
            if r.get("sec"):            # 有分段的歌: 同分里挑段落权重最高的那处
                occ, _best = find_all(s, d, fuzzy)
                if not occ:
                    ok = False
                    break
                p, dd = max(occ, key=lambda o: (-o[1], sec_weight(sec_at(r["sec"], o[0], len(s))), -o[0]))
                det.append((p, dd))
            else:                       # 没分段的绝大多数: 走快路径(第一个出现位置)
                got = find(s, d, fuzzy)
                if not got:
                    ok = False
                    break
                det.append(got)
        if not ok:
            continue
        _names = [sec_at(r.get("sec"), p, len(sg)) for (p, _d), sg in zip(det, segs)]
        _sec = max(_names, key=sec_weight) if any(_names) else ""
        hits.append(dict(r, diff=max(x[1] for x in det), _det=det, _secmap=r.get("sec") or [],
                         sec=_sec, sec_cn=sec_label(_sec), sec_w=sec_weight(_sec)))
    # 排序: 越像越靠前; 同分时**副歌/主歌 > 前奏/尾奏/发狂钢琴**(用户 2026-09 的段落权重规格),
    # 再按 热度 -> 知名度 -> 谱短 -> 曲名。
    # 排序口径(用户 2026-09-25 明确选 B): 代价 -> **段落权** -> 热度 -> 知名度 -> 名短 -> 曲名。
    # 段落权紧跟代价 = 用户当年公式(匹配长度 × 段落权 × 覆盖率 − 错音惩罚)的精神:
    # **副歌/主歌里的命中可以压过冷门歌前奏/发狂钢琴里的命中**(代价相同时)。用户接受由此带来的答案翻转。
    hits.sort(key=lambda x: (x["diff"], -x.get("pop", 0), -x.get("hot", 0),
                             -x.get("sec_w", 1.0), x["n_notes"], x["title"]))
    if top:
        hits = hits[:top]
    for i, h in enumerate(hits):
        det = h.pop("_det")
        # 只给靠前的这些做整句对齐; 再多也没人看(bot 只发前 20 条), 别把 7 千首都算一遍
        hit_detail(h, segs, det, fuzzy, do_align=(i < ALIGN_MAX))
    return hits


def fmt_hit(i, h):
    bits = [h["title"] or "(无题)"]
    if h["artist"]:
        bits.append("歌手 " + "、".join(h["artist"][:2]))
    bits.append("%d 音" % h["n_notes"])
    if h["status"]:
        bits.append(h["status"])
    if h["file"]:
        bits.append(h["file"])
    line = "%2d. %s" % (i, " ｜ ".join(bits))
    if h.get("sec_cn") and h["sec_cn"] != "整曲":
        line += "  〔%s〕" % h["sec_cn"]
    if h["diff"]:
        line += "  （差 %d 处）" % h["diff"]
    return line


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?", help="要查的数字串(可多段: 空格/逗号/竖线分隔)")
    ap.add_argument("--fuzzy", type=int, default=0, help="允许几处不同(默认 0, 严格)")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--json", action="store_true", help="机器可读输出(机器人插件用这个)")
    ap.add_argument("--data", default=DATA, help="data.jsonl 路径(默认 jianpu-db/data.jsonl)")
    ap.add_argument("--no-cache", action="store_true", help="不用数字索引缓存(每次都重建)")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if not a.query:
        print(__doc__)
        return 0

    segs = split_query(a.query)
    q = " ".join(segs)
    if not segs:
        print("没认出任何音高数字（只认 1-7）。", file=sys.stderr)
        return 2
    if not os.path.isfile(a.data):
        print("找不到语料: %s（用 --data 或 JIANPU_DB 指定）" % a.data, file=sys.stderr)
        return 2

    rows = load_corpus(a.data, use_cache=not a.no_cache)
    hits = search(rows, segs, a.fuzzy, a.top)
    if a.json:
        print(json.dumps({"query": q, "segments": segs, "n": sum(len(x) for x in segs),
                          "fuzzy": a.fuzzy, "corpus": a.data, "total": len(rows),
                          "count": len(hits), "hits": hits}, ensure_ascii=False))
        return 0
    print("查询 %s（%d 个音%s）· 语料 %d 首"
          % (q, sum(len(x) for x in segs), "，允许 %d 处不同" % a.fuzzy if a.fuzzy else "，精确",
             len(rows)))
    print("命中 %d 首" % len(hits)
          + ("（只显示前 %d）" % a.top if a.top and len(hits) >= a.top else ""))
    for i, h in enumerate(hits, 1):
        print("  " + fmt_hit(i, h))
        multi = len(h.get("segs_detail") or []) > 1
        for j, dt in enumerate(h.get("segs_detail") or [], 1):
            tag = ("第 %d 段 " % j) if multi else ("整句 " if dt.get("aligned") else "")
            if not dt.get("seg"):
                print("       └ %s（与第 %d 段同一处）" % (tag, dt.get("same_as", 0)))
                continue
            rng = ("第 %d–%d 小节" % (dt["bar_from"], dt["bar_to"])
                   if dt["bar_to"] != dt["bar_from"] else "第 %d 小节" % dt["bar_from"])
            print("       └ %s%s: %s" % (tag, rng, dt["seg"]))
    if not hits:
        print("   （无 —— 试 --fuzzy 1, 或换一段更完整的旋律）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

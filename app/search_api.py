"""只读旋律检索的口径——**只有这一份**，`app/server.py`与`app/api.py`都import它。

为什么单开一个模块：站点有两份后端（标准库版/FastAPI版），同一件事各写一遍就会慢慢长出差异
（这个仓库已经用`check_parity_*.py`对拍过三次同类事故：CORS头、鉴权顺序、预检状态码）。
所以检索这层只写一遍：定位语料 -> 调现成的`melody_search` -> 参数校验/缓存/限流，
两份后端各自只留三行"搬到HTTP"。

**匹配算法一行都不重写**：`jianpu2/tools/melody_search.py`是唯一实现（jptok切token、整句对齐、
段落加权、并列时的热度裁决都在它里面），这里只负责把它包成HTTP能调用的东西。

定位方式照抄`app/server.py::_find_db`的思路——**先看环境变量，再看几个约定位置**：
  * 语料：`JIANPU_DB` -> `<站点仓库>/corpus/jianpu-db` -> `<站点仓库>/../jianpu-db` -> `<cwd>/jianpu-db`
  * 工具：`JIANPU_TOOLS` -> `<站点仓库>/../jianpu2/tools` -> `<cwd>/jianpu2/tools`
都找不到**不崩**：`handle()`回503，body里写清缺什么、该设哪个环境变量。

输出形状是`melody_search.py --json`那一份**原样**（query/segments/n/fuzzy/corpus/total/count/hits），
只**新增**两个字段：`index_version`（语料行数+mtime，判断快照新旧）与`cache`（`{"hit": bool}`）。

## 第二层：MusicBrainz 风格的只读命名空间 `/ws/2/`

2026-10-05 加。动机：MusicBrainz 的 `/ws/2/` 是**事实上的行业惯例**（实体 + `inc=` + `fmt=` +
`limit/offset` + `{"created","count","offset",…}` 信封 + 每 IP 每秒 1 次 + 要求 User-Agent），
第三方爬虫/音乐库工具照着这个形状接，几乎不用读文档。所以这里按它的形状再包一层，
**只读**，`/api/*` 一个字都不动。

三层分工（都在本模块里，两份后端各自只留三行"搬到HTTP"）：
  * `ws2_root()`      -> `/ws/2/` 的自我介绍（实体、参数、限流、示例地址）
  * `ws2_entity(id…)` -> `/ws/2/song/<id>` 单条实体（**直接返回对象，不套信封**）
  * `ws2_search()`    -> `/ws/2/song?query=…` 检索（内部仍然调上面那个现成的 `search()`）
  * `ws2_handle()`    -> 把上面三个按路径接起来，回 `(状态码, body, 额外响应头)`

与 `/api/search` 的两处**有意不同**（照 MusicBrainz 的习惯，不是笔误）：
  ① 限流是**每IP每秒1次**（超出 503 + `Retry-After: 1`），不是每分钟30次——MB 的调用方习惯慢速轮询；
  ② 错误体是 `{"error": "…"}`，不是 `/api/*` 那套 `{"ok": false, "err": "…"}`。
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # 站点仓库根

Q_MAX = 200                       # q的上限（实测：用户哼错时会把整句贴上，200个数字已远超任何一段旋律）
TOP_MIN, TOP_MAX, TOP_DEFAULT = 1, 50, 20
FUZZY_VALUES = ("0", "1", "2")
CACHE_MAX = 200                   # 结果缓存条数：机器人的稳定查询就那么几十条，200足够
RATE_WINDOW = 60.0                # 限流窗口（秒）
CACHE_STAT = {"hit": 0, "miss": 0}

# ── MusicBrainz 风格 `/ws/2/` 的口径（见模块 docstring 第二层）────────────────────
WS2_PREFIX = "/ws/2"
WS2_VERSION = "2"
WS2_ENTITY = "song"               # 实体名。MB 那边这条对应的是 work；这里是"一首谱/一支曲"
WS2_TITLE = "jianpu-db Web Service"
WS2_INCS = ("artists", "tags", "links", "sections", "score")
WS2_LIMIT_DEFAULT, WS2_LIMIT_MAX = 25, 100
WS2_SEARCH_TOP = 1000             # 交给 matcher 的"最多要多少条"的硬上限
WS2_COUNT_MIN = 120               # 为了让 count 尽量精确，至少要看这么多条（比 limit 上限 100 大一档）
WS2_CACHE_ROWS_MAX = 400          # 命中数小于它才在内存里缓存"这一窗"，否则只记 count
WS2_RATE_PER_SEC = 1              # 每 IP 每秒放行几次（MB 的习惯值）
WS2_UA_HINT = "jianpu-db/ws2 (+https://jianpu-db.org/ws/2/)"


def _env_int(name, default):
    """读一个整数环境变量。**实测**：环境里写`30/分钟`这种带单位的值时`int()`直接抛，
    会把整个服务拦在启动阶段——所以读不动就退回默认值，不自作聪明。"""
    try:
        return int(str(os.environ.get(name) or "").strip() or default)
    except ValueError:
        return default


RATE_MAX = _env_int("JIANPU_SEARCH_RPM", 30)      # 每客户端每分钟多少次（默认30）


# ══════════════════════════════════════════════════════════════════════════════
# 定位语料与工具
# ══════════════════════════════════════════════════════════════════════════════
def _db_cands():
    return [os.environ.get("JIANPU_DB", "").strip(),
            os.path.join(ROOT, "corpus", "jianpu-db"),          # 便携包布局
            os.path.join(os.path.dirname(ROOT), "jianpu-db"),   # 本机开发布局（与站点仓库平级）
            os.path.join(os.getcwd(), "jianpu-db")]


def _tools_cands():
    return [os.environ.get("JIANPU_TOOLS", "").strip(),
            os.path.join(ROOT, "..", "jianpu2", "tools"),       # 本机开发布局
            os.path.join(os.path.dirname(ROOT), "jianpu2", "tools"),
            os.path.join(os.getcwd(), "jianpu2", "tools")]


def find_corpus():
    """-> `(语料目录, data.jsonl路径)`；没有真的`data.jsonl`就返回`None`。

    为什么要看`data.jsonl`而不是只看目录：mtime与行数都从它来（`index_version`），
    空目录也会被"目录存在"骗过去，检索会安静地返回0命中。
    """
    for c in _db_cands():
        if not c:
            continue
        p = os.path.join(c, "data.jsonl")
        if os.path.isfile(p):
            return os.path.abspath(c), os.path.abspath(p)
    return None


def find_tools():
    """-> 含`melody_search.py`的目录；没有返回`None`。"""
    for c in _tools_cands():
        if c and os.path.isfile(os.path.join(c, "melody_search.py")):
            return os.path.abspath(c)
    return None


def unavailable():
    """语料或工具缺了 -> 返回给人看的一句话（写清缺什么、设哪个环境变量）；都在 -> `None`。"""
    if find_corpus() is None:
        tried = "、".join([c for c in _db_cands() if c])
        return (f"找不到语料data.jsonl（找过：{tried}）。把JIANPU_DB设成语料仓库目录"
                "（里面要有data.jsonl）再重启。")
    if find_tools() is None:
        tried = "、".join([c for c in _tools_cands() if c])
        return (f"找不到jianpu2/tools/melody_search.py（找过：{tried}）。把JIANPU_TOOLS设成"
                "jianpu2/tools目录（里面要有melody_search.py）再重启。")
    return None


_module = None
_module_lock = threading.Lock()


def module():
    """把jianpu2/tools插进`sys.path`再`import melody_search`（现成实现，不复制代码）。

    为什么惰性import：缺工具时要在**请求**里回503而不是让服务起不来；顺带把"两次import"
    锁住（ThreadingHTTPServer下并发首查会各import一次）。
    """
    global _module
    if _module is not None:
        return _module
    with _module_lock:
        if _module is None:
            tools = find_tools()
            if tools is None:
                raise RuntimeError("找不到melody_search.py（设JIANPU_TOOLS）")
            if tools not in sys.path:
                sys.path.insert(0, tools)
            _module = importlib.import_module("melody_search")
    return _module


# ══════════════════════════════════════════════════════════════════════════════
# 语料快照：内存里留一份，变了就重建
# ══════════════════════════════════════════════════════════════════════════════
_rows = None
_rows_fp = None
_rows_lock = threading.Lock()
_lines_fp = None
_lines_n = 0

# `/ws/2/song` 的全量命中缓存：键是 ("旋律段", "fuzzy")。上限比 `_cache` 小 —— 每条可能是全库
# 命中，内存里不该囤太多；满了就丢最旧的（OrderedDict）。放在这里是因为 `corpus_rows()` 要清它。
_ws2_cache: OrderedDict[tuple, list] = OrderedDict()
_ws2_cache_lock = threading.Lock()
WS2_CACHE_MAX = 32


def _line_count(path, st):
    """`data.jsonl`的非空行数。**实测**：11,381行/22MB逐行数一次约60ms，
    所以按`(size, mtime_ns)`记住——语料不变就不重数。"""
    global _lines_fp, _lines_n
    fp = (st.st_size, st.st_mtime_ns)
    if _lines_fp == fp:
        return _lines_n
    n = 0
    with open(path, "rb") as f:
        for ln in f:
            if ln.strip():
                n += 1
    _lines_fp, _lines_n = fp, n
    return n


def index_version():
    """语料快照标识：`<非空行数>@<mtime秒>`——用来判断这次结果出自哪份语料。"""
    got = find_corpus()
    if not got:
        return ""
    _db, data = got
    st = os.stat(data)
    return f"{_line_count(data, st)}@{int(st.st_mtime)}"


def corpus_rows():
    """语料行（`melody_search.build_corpus`那份结构）。

    为什么每次都比指纹而不是启动时读一次：语料天天在长（投稿直接进`scores/`并重建索引），
    启动时定住会让新歌永远搜不到。指纹一样时只花两次`stat`；变了才走`load_corpus`
    （它自己还有`~/.cache/jianpu/melody_index.json`一层，冷启约6s，热启约0.3s）。
    """
    global _rows, _rows_fp
    got = find_corpus()
    if not got:
        raise RuntimeError("找不到语料data.jsonl（设JIANPU_DB）")
    _db, data = got
    st = os.stat(data)
    fp = (st.st_size, st.st_mtime_ns)
    if _rows is not None and _rows_fp == fp:
        return _rows
    with _rows_lock:
        if _rows is not None and _rows_fp == fp:      # 双检：并发首查只建一次
            return _rows
        rows = module().load_corpus(data, use_cache=True)
        _rows, _rows_fp = rows, fp
        cache_clear()                                 # 语料换了，旧结果不许再端出去
        with _ws2_cache_lock:                         # `/ws/2/song` 的全量命中同样作废
            _ws2_cache.clear()
    return _rows


# ══════════════════════════════════════════════════════════════════════════════
# 原始语料行（`/ws/2/song/<id>` 要的字段比检索索引多）
#
# 为什么不能拿 `corpus_rows()` 顶上：`melody_search.build_corpus` 为了检索只留了
# title/artist/tag/source/file/id/status/digits/score/bars/sections 这些，而单条实体还要
# `usertag`/`transcriber`/`link`/`alias`/`MBID`。所以这里**另读一遍 data.jsonl 原文**，
# 并顺手建两个下标（source、文件名主干）—— 11,384 行约 200~300ms，按 `(size, mtime_ns)`
# 指纹缓存，与 `corpus_rows()` 同一套"语料变了就重建"的纪律。
# ══════════════════════════════════════════════════════════════════════════════
_raw_index = None
_raw_fp = None
_raw_lock = threading.Lock()


def _as_list(v):
    """data.jsonl 里的字段有的是字符串、有的是列表 —— 统一成列表（空值给空列表）。"""
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [x for x in v if x not in (None, "")]
    return [v] if v != "" else []


def _first(v, dflt=""):
    got = _as_list(v)
    return got[0] if got else dflt


def raw_index():
    """-> `{"by_source": {...}, "by_file": {...}}`（键都做小写归一，查得到就行）。

    `by_source` 收 `source`（如 `qupu123-268596`）**与**它去掉站点前缀的后半截（`268596`），
    前者是天然主键，后者方便手工敲。`by_file` 收文件名主干（`水手` / `水手.txt`），
    因为 `scores/` 里的文件名也是用户会拿去当 id 用的东西。撞车时**先来的赢**（保持稳定）。
    """
    global _raw_index, _raw_fp
    got = find_corpus()
    if not got:
        raise RuntimeError("找不到语料data.jsonl（设JIANPU_DB）")
    _db, data = got
    st = os.stat(data)
    fp = (st.st_size, st.st_mtime_ns)
    if _raw_index is not None and _raw_fp == fp:
        return _raw_index
    with _raw_lock:
        if _raw_index is not None and _raw_fp == fp:
            return _raw_index
        by_source, by_file = {}, {}
        with open(data, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                src = _first(r.get("source"))
                f0 = _first(r.get("file"))
                if src:
                    for k in (src, src.split("-", 1)[1] if "-" in src else ""):
                        if k:
                            by_source.setdefault(k.lower(), r)
                if f0:
                    stem = os.path.splitext(os.path.basename(f0))[0]
                    for k in (f0, stem):
                        if k:
                            by_file.setdefault(k.lower(), r)
        _raw_index, _raw_fp = {"by_source": by_source, "by_file": by_file}, fp
    return _raw_index


def find_row(key):
    """id -> 原始语料行；找不到返回 `None`。"""
    k = unquote(str(key or "")).strip().lower()
    if not k:
        return None
    idx = raw_index()
    return idx["by_source"].get(k) or idx["by_file"].get(k)


# ── 曲谱文件的头部字段（`link=` / `alias=` / `MBID=` 只存在 scores/*.txt，data.jsonl 里没有）──
_score_head_cache: dict = {}
_score_head_lock = threading.Lock()
SCORE_HEAD_KEYS = ("title", "tag", "usertag", "tagroute", "transcriber", "status", "source",
                   "link", "alias", "artist", "MBID", "subtitle", "todo")


def score_head(filename):
    """读 `scores/<filename>` 的头部 `key=value` -> `{key: [值, …]}`；读不到返回 `{}`。

    为什么需要它：语料索引（data.jsonl）不带 `link` —— 那是**曲谱文件头部**才有的东西，
    而 MusicBrainz 风格的 `inc=links` 正是要它。按 `(size, mtime_ns)` 缓存，文件不常改。
    头部界定沿用曲谱格式：从第一个 `%--` 往前都算头部。
    """
    base = os.path.basename(str(filename or ""))
    if not base or not base.endswith(".txt"):
        return {}
    got = find_corpus()
    if not got:
        return {}
    path = os.path.join(got[0], "scores", base)
    try:
        st = os.stat(path)
    except OSError:
        return {}
    fp = (st.st_size, st.st_mtime_ns)
    with _score_head_lock:
        hit = _score_head_cache.get(base)
        if hit and hit[0] == fp:
            return hit[1]
    out: dict = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i > 200 or line.startswith("%--"):
                    break
                line = line.rstrip("\n")
                if not line or line.startswith("%") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip()
                if k in SCORE_HEAD_KEYS and v:
                    out.setdefault(k, []).append(v)
    except OSError:
        return {}
    with _score_head_lock:
        if len(_score_head_cache) > 4096:              # 内存只涨不跌是事故；满了就整块丢
            _score_head_cache.clear()
        _score_head_cache[base] = (fp, out)
    return out


# ══════════════════════════════════════════════════════════════════════════════
# 结果缓存（进程内，LRU，最多200条——不引Redis：单机只读接口，重启丢缓存无所谓）
# ══════════════════════════════════════════════════════════════════════════════
_cache: OrderedDict[tuple, dict] = OrderedDict()
_cache_lock = threading.Lock()


def cache_clear():
    with _cache_lock:
        _cache.clear()


def cache_stats():
    """给自查/运维看一眼（没有端点，`import search_api; search_api.cache_stats()`）。"""
    with _cache_lock:
        hit, miss = CACHE_STAT["hit"], CACHE_STAT["miss"]
        return {"size": len(_cache), "cap": CACHE_MAX, "hit": hit, "miss": miss,
                "hit_rate": (round(hit / (hit + miss), 3) if hit + miss else 0.0),
                "rate_max": RATE_MAX, "rate_limited": _rl_blocked, "clients": len(_rl_hits)}


# ══════════════════════════════════════════════════════════════════════════════
# 限流（按客户端IP，进程内计数）
# ══════════════════════════════════════════════════════════════════════════════
_rl_hits: dict[str, list[float]] = {}
_rl_lock = threading.Lock()
_rl_blocked = 0


def rate_check(ip, now=None):
    """-> `(放行?, Retry-After秒)`。默认每IP每分钟`RATE_MAX`次。

    为什么先限流再校验参数：被限流的客户端不必再读参数，也免得"坏请求"免费用掉配额
    （它们照样计数）。窗口是**滑动**的：只看最近60s里那几次，不搞"整分钟清零"
    ——整分钟清零会让59秒打满30次、61秒再打满30次的爬虫畅通无阻。
    """
    global _rl_blocked
    now = time.monotonic() if now is None else now
    ip = ip or "?"
    with _rl_lock:
        stamps = [t for t in _rl_hits.get(ip, ()) if now - t < RATE_WINDOW]
        if len(stamps) >= RATE_MAX:
            _rl_hits[ip] = stamps
            _rl_blocked += 1
            return False, max(1, int(RATE_WINDOW - (now - stamps[0])) + 1)
        stamps.append(now)
        _rl_hits[ip] = stamps
        if len(_rl_hits) > 4096:                      # 空窗口的IP顺手清掉，内存只涨不跌是事故
            for k in [k for k, v in _rl_hits.items() if not v or now - v[-1] > RATE_WINDOW]:
                _rl_hits.pop(k, None)
        return True, 0


# ── `/ws/2/` 的限流：每 IP 每秒 `WS2_RATE_PER_SEC` 次（MusicBrainz 的习惯）────────────
# 为什么另开一份而不是复用上面那条：两条的**语义不同**（上面是每IP每分钟30次的常规防爬，
# 这里是 MB 调用方预期的"每秒1次"礼貌速率），窗口差了两个数量级，混在一份计数里必然互相打架
# （一次检索就把"每秒1次"的配额吃光）。两份各自独立计数，互不影响。
# 名字里带 `_rl_` 是**故意的**：这个模块里还有两个叫 `_mb_hits` 的东西（检索用的那个函数、
# 以及下面限流用的这张表），第一版把限流表也写成 `_mb_hits` —— 函数定义在后面，直接把表盖掉了，
# 于是每一次 `/ws/2/` 请求都 500（`'function' object has no attribute 'get'`）。名字分开，别再撞。
_mb_rl_hits: dict = {}
_mb_lock = threading.Lock()
_mb_limited = 0
MB_WINDOW = 1.0


def mb_rate_check(ip, now=None):
    """-> `(放行?, Retry-After秒)`；每 IP 每秒 `WS2_RATE_PER_SEC` 次，超出建议 1 秒后再来。"""
    global _mb_limited
    now = time.monotonic() if now is None else now
    ip = ip or "?"
    with _mb_lock:
        stamps = [t for t in _mb_rl_hits.get(ip, ()) if now - t < MB_WINDOW]
        if len(stamps) >= WS2_RATE_PER_SEC:
            _mb_rl_hits[ip] = stamps
            _mb_limited += 1
            return False, 1
        stamps.append(now)
        _mb_rl_hits[ip] = stamps
        if len(_mb_rl_hits) > 8192:                   # 同 rate_check：顺手清空窗口之外的 IP
            for k in [k for k, v in _mb_rl_hits.items() if not v or now - v[-1] > MB_WINDOW]:
                _mb_rl_hits.pop(k, None)
        return True, 0


# ══════════════════════════════════════════════════════════════════════════════
# 参数校验（口径只有这一份）
# ══════════════════════════════════════════════════════════════════════════════
def parse_params(query_string):
    """查询串 -> `(参数, 错误文案)`（两个里必有一个是`None`）。

    规则：
      * 缺`q`/`q`全是空白/`q`长过200 -> 400；
      * `fuzzy`只收0/1/2（默认0），别的值400——它是"允许几处不同"，给9会让
        `melody_search`在1.1万首上逐窗口比，等于把服务拖死；
      * `top`收1..50（默认20），**超范围钳到边界**而不是报错——调用方写`top=0`
        多半是想要"默认"。注意`melody_search`把`top=0`当成"全部命中"（实测
        11,381首规模下JSON有几十MB），所以钳到1而不是放它过去。
    """
    p = parse_qs(query_string or "", keep_blank_values=True)
    q = (p.get("q") or [""])[0]
    if not q.strip():
        return None, "缺q（要查的旋律数字串，如q=316316）"
    if len(q) > Q_MAX:
        return None, f"q太长（{len(q)} > {Q_MAX}）"
    fz = ((p.get("fuzzy") or ["0"])[0] or "0").strip() or "0"
    if fz not in FUZZY_VALUES:
        return None, f"fuzzy只收0/1/2（给的是{fz}）"
    raw_top = ((p.get("top") or [str(TOP_DEFAULT)])[0] or "").strip() or str(TOP_DEFAULT)
    try:
        top = int(raw_top)
    except ValueError:
        return None, f"top要整数（给的是{raw_top}）"
    return {"q": q, "fuzzy": int(fz), "top": max(TOP_MIN, min(TOP_MAX, top))}, None


# ══════════════════════════════════════════════════════════════════════════════
# 检索
# ══════════════════════════════════════════════════════════════════════════════
def search(q, fuzzy=0, top=TOP_DEFAULT):
    """跑一次检索，返回`melody_search --json`那个形状+`index_version`/`cache`。

    没认出任何音高数字（比如`q=abc`）时返回`count=0`的空结果——与命令行一致
    （`melody_search.py`对同一输入也是"没认出任何音高数字"），HTTP那边把它变成400。
    """
    ms = module()
    _db, data = find_corpus()
    key = (str(q), int(fuzzy), int(top))
    with _cache_lock:
        base = _cache.get(key)
        if base is not None:
            _cache.move_to_end(key)
            CACHE_STAT["hit"] += 1
            out = dict(base)
            out["cache"] = {"hit": True}
            return out
        CACHE_STAT["miss"] += 1
    rows = corpus_rows()
    segs = ms.split_query(q)
    hits = ms.search(rows, segs, int(fuzzy), int(top))
    base = {"query": " ".join(segs), "segments": segs, "n": sum(len(x) for x in segs),
            "fuzzy": int(fuzzy), "corpus": data, "total": len(rows),
            "count": len(hits), "hits": hits, "index_version": index_version()}
    with _cache_lock:
        _cache[key] = base
        _cache.move_to_end(key)
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)
    out = dict(base)
    out["cache"] = {"hit": False}
    return out


def handle(query_string, ip=""):
    """HTTP那一层要的全部：`(状态码, body, 额外响应头)`。两份后端都只调它。

    错误body沿用写端点那套`{"ok": false, "err": …}`，前端不必为检索再学一种错误形状。
    """
    allowed, retry = rate_check(ip)
    if not allowed:
        return 429, {"ok": False, "err": f"请求太快，{retry}秒后再试（每分钟最多{RATE_MAX}次）"}, \
            {"Retry-After": str(retry)}
    params, err = parse_params(query_string)
    if err:
        return 400, {"ok": False, "err": err}, {}
    miss = unavailable()
    if miss:
        return 503, {"ok": False, "err": miss}, {}
    if not module().split_query(params["q"]):
        return 400, {"ok": False, "err": "没认出任何音高数字（只认1-7）"}, {}
    try:
        return 200, search(params["q"], params["fuzzy"], params["top"]), {}
    except Exception as e:                            # 检索出事也别把traceback泄给调用方
        return 500, {"ok": False, "err": f"{type(e).__name__}: {e}"}, {}


# ══════════════════════════════════════════════════════════════════════════════
# MusicBrainz 风格 `/ws/2/`（只读；见模块 docstring 第二层）
# ══════════════════════════════════════════════════════════════════════════════
def ws2_created():
    """信封里的 `created`：UTC、秒级、带 Z —— 与 MusicBrainz 同一写法。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def base_url_from_host(host):
    """`Host` 头 -> 可点的 origin（`/ws/2/` 里那条示例地址要用它）。

    为什么不一味写死 `https://jianpu-db.org`：本机自检（`http://127.0.0.1:端口`）与灰度实例
    上那条示例也得点得开，否则"给一条可点的示例"就只是句空话。判断只按"像不像本机"来，
    不做别的猜测：本机/内网给 `http://`，其余给 `https://`（线上只有 https，明文会被 301）。
    """
    h = str(host or "").strip()
    if not h:
        return "https://jianpu-db.org"
    name = h.split(":")[0].lower()
    local = (name in ("localhost", "127.0.0.1", "::1", "[::1]") or name.startswith("127.")
             or name.startswith("192.168.") or name.startswith("10."))
    return ("http://" if local else "https://") + h


def ws2_error(status, msg, headers=None):
    """统一错误体。**这一层的错误一律是 `{"error": "…"}`**（MusicBrainz 的形状）。

    与 `/api/*` 那套 `{"ok": false, "err": …}` **有意不同**：照抄 MB 才能让现成的 MB 客户端
    直接读懂，而 `/api/*` 一个字都不动（前端/机器人还在用那套）。
    """
    return status, {"error": str(msg)}, dict(headers or {})


def _q1(p, name, dflt=""):
    """取一个查询参数（不存在/空串都退回默认值）。"""
    v = (p.get(name) or [""])[0]
    v = v.strip() if isinstance(v, str) else ""
    return v or dflt


def _parse_int(raw, name):
    """数字参数：只收十进制整数（+1 也不行），非法就回错误文案。"""
    s = str(raw or "").strip()
    if not s.isdigit():
        return None, f"{name} 要是一个非负整数（给的是 {s or '(空)'}）"
    return int(s), None


def _query_text(p):
    """检索词：`query` 为准，兼容 `q=`（老调用方/顺手敲）与 MusicBrainz 的 `query` 写法。"""
    return _q1(p, "query") or _q1(p, "q")


def mb_artist_list(row, head):
    """歌手：`artist` 优先，没有就退回曲谱头部的 `artist`（data.jsonl 与文件可能只有一个有）。"""
    out = [str(x).strip() for x in _as_list(row.get("artist")) if str(x).strip()]
    if not out:
        out = [str(x).strip() for x in (head.get("artist") or []) if str(x).strip()]
    seen, uniq = set(), []
    for a in out:
        if a.lower() not in seen:
            seen.add(a.lower())
            uniq.append(a)
    return uniq


def mb_tags(row, head):
    """标签：`tag` + `usertag`（人工补的）+ 头部 `tag`/`usertag` 合并去重（大小写不敏感）。"""
    out, seen = [], set()
    for src in (_as_list(row.get("tag")), _as_list(row.get("usertag")),
                head.get("tag") or [], head.get("usertag") or []):
        for t in src:
            s = str(t).strip()
            if s and s.lower() not in seen:
                seen.add(s.lower())
                out.append({"name": s})
    return out


def mb_links(row, head):
    """收录页：曲谱头部的每一个 `link=`（文件里可能有多条）。"""
    urls, seen = [], set()
    for u in (_as_list(row.get("link")) + list(head.get("link") or [])):
        s = str(u).strip()
        if s and s not in seen:
            seen.add(s)
            urls.append({"type": "source-page", "url": s})
    return urls


def mb_sections(row):
    """分段：`sections[]` 摊成 `{subtitle, name_cn, n_notes, score}`（音符数按同一份 token 口径数）。"""
    out = []
    for s in (row.get("sections") or []):
        if not isinstance(s, dict):
            continue
        txt = s.get("score") or ""
        name = (s.get("subtitle") or "").strip() or "score"
        out.append({"subtitle": name, "name_cn": module().sec_label(name),
                    "n_notes": len(module().digits_of(txt)), "score": txt})
    return out


def entity_of(row, head, incs=None):
    """原始语料行 -> MusicBrainz 风格的单条实体。`inc` 里的项**不存在就整块省掉**。

    `score` 是"简谱数字串"那个原始形状（与 `sections[].score` 同一份口径，不改写、不重排）。

    一处不那么 MB 的地方，说明白：`sections` 与 `score` 是**数据本身**，`inc` 里给不给都要，
    因为一首谱没有"乐谱"就不存在了；`inc` 只控制"要不要额外把 `tags/links` 展开"
    （`artists` 是 `artist-credit` 的名字简写，也一样常给）。
    """
    incs = incs or ()
    src = _first(row.get("source"))
    f0 = _first(row.get("file"))
    rid = src or ("f-" + os.path.splitext(os.path.basename(f0))[0])
    artists = mb_artist_list(row, head)
    ent = {
        "id": rid,
        "musicbrainz_work_id": _first(row.get("MBID")) or _first(head.get("MBID")),
        "title": str(row.get("title") or "").strip(),
        "file": f0,
        "source": src,
        "site": (src.split("-")[0] if src else ""),
        "status": _first(row.get("status")),
        "transcriber": (_as_list(row.get("transcriber")) or list(head.get("transcriber") or [])
                        or [""])[0],
        "alias": _as_list(row.get("alias")) or list(head.get("alias") or []),
        "n_notes": int(row.get("n_notes") or 0),
        "bars": [int(x) for x in (row.get("bars") or []) if isinstance(x, int)],
        "score": row.get("score") or "",
        "sections": mb_sections(row),
        "artist-credit": [{"name": a} for a in artists],
        "artists": artists,                 # 老式简写：只给名字
        "tags": [],                          # 占位，下面按 inc 填
    }
    if "tags" in incs:
        ent["tags"] = mb_tags(row, head)
    if "links" in incs:
        ent["links"] = mb_links(row, head)
    return ent


def _unknown_incs(incs, extra=None):
    """不在支持表里的 `inc` 值（**忽略，但要说一声**，绝不 500）。"""
    known = set(WS2_INCS) | set(extra or ())
    return [x for x in incs if x not in known]


def _split_inc(values):
    """`inc=artists+tags` / `inc=artists tags` / 重复 `inc=` -> 有序去重的列表。"""
    import re as _re
    out, seen = [], set()
    for v in values:
        for part in _re.split(r"[+\s,|]+", str(v or "")):
            p = part.strip().lower()
            if p and p not in seen:
                seen.add(p)
                out.append(p)
    return out


def ws2_root(base_url="https://jianpu-db.org"):
    """`GET /ws/2/` —— API 根：实体、参数、限流、一条可点的示例。

    `base_url` 由后端从请求里推（本机自检时是 127.0.0.1:xxxx，线上是 jianpu-db.org），
    这样返回的那条示例地址**永远点得开**（点开的是一个真查询，不是占位符）。
    """
    ex = f"{base_url.rstrip('/')}{WS2_PREFIX}/song?query=316316&limit=5&fmt=json"
    origin = base_url.rstrip("/") or "https://jianpu-db.org"
    return {
        "name": WS2_TITLE,
        "version": WS2_VERSION,
        "created": ws2_created(),
        "read_only": True,
        "entities": [{
            "name": WS2_ENTITY,
            "singular": WS2_ENTITY,
            "description": "一首谱（一支曲）。主键是语料里的 `source`（如 qupu123-268596），"
                           "也接受 `scores/` 里的文件名主干（如 水手）。",
            "endpoints": [f"{WS2_PREFIX}/{WS2_ENTITY}/<id>", f"{WS2_PREFIX}/{WS2_ENTITY}"],
        }],
        "parameters": {
            "fmt": "只支持 json（默认 json）；也接受请求头 Accept: application/json",
            "inc": f"要额外展开的关联数据，多选用 + 或空格：{'、'.join(WS2_INCS)}"
                   "（不认识的值会被忽略，并在响应头 X-Unknown-Inc 里列出）",
            "query": "检索词：要查的旋律数字串（1-7，如 316316）；在 /ws/2/song 上用",
            "limit": f"检索返回条数，默认 {WS2_LIMIT_DEFAULT}，上限 {WS2_LIMIT_MAX}",
            "offset": "检索结果的偏移量（从 0 开始），默认 0",
        },
        "envelope": {"search": "{created, count, offset, songs[]}",
                     "lookup": "单条实体直接返回对象（不套信封）"},
        "rate_limit": {"policy": f"每 IP 每秒 {WS2_RATE_PER_SEC} 次",
                       "over_limit": "HTTP 503 + Retry-After: 1",
                       "user_agent": f"请带上能识别调用方的 User-Agent（建议形如 {WS2_UA_HINT}）"},
        "sample": ex,
        "examples": [
            {"url": f"{base_url.rstrip('/')}{WS2_PREFIX}/{WS2_ENTITY}/qupu123-268596?fmt=json"
                    "&inc=artists+tags+links+sections",
             "note": "单条实体（含歌手/标签/收录页/分段）"},
            {"url": ex, "note": "检索：旋律 316316 的前 5 条"},
        ],
        # 给人/给 AI 的入口。为什么写在这里：`/ws/2/` 是第三方与 AI **最可能先碰到的那一层**，
        # 而契约与 llms.txt 挂在服务根上（不在 `/ws/2/` 前缀下），不从这儿链过去就找不着。
        # ⚠ 说实话：`/openapi.json`、`/docs`、`/llms.txt` 由 **FastAPI 版**的本机服务提供；
        #   线上 `/ws/2/*` 走 Cloudflare Worker 反代到本机 8770（标准库版），
        #   那个上游**没有**这三条 —— 所以从线上点这些链接会 404，而 `/ws/2/` 本身可用
        #   （原话写在仓库根的 `llms.txt` 里，机器关了 `/ws/2/*` 也一起不可用）。
        "docs": {
            "openapi": f"{origin}/openapi.json",
            "swagger": f"{origin}/docs",
            "redoc": f"{origin}/redoc",
            "note": "契约由 FastAPI 版自动生成（app/api.py + app/ws2_schema.py）；"
                    "它挂在本机服务根上，线上是否可达见 llms.txt",
            "llms": f"{origin}/llms.txt",
        },
        "mcp": {
            "name": "jianpu-db-mcp",
            "transport": "stdio",
            "where": "jianpu2 仓库的 _analysis/jianpu-db-mcp/（独立小包，未入任何仓库）",
            "tools": "search_melody(digits, fuzzy, limit) / get_song(id, inc) / stats()",
            "install": "pip install mcp，然后照它的 README.md 把命令填进 MCP 客户端配置",
        },
    }


def _mb_hits(rows, segs, fuzzy, want):
    """内部调**现成的 matcher**（`melody_search.search`），把命中换成 MB 风格的一行。

    `want` 是"最多要多少条"（会传 `WS2_SEARCH_TOP`，比 `limit` 上限大一个量级）。

    为什么要按 `id` 回查原始行：`melody_search` 的命中是它自己的行结构（没有 `link` 等字段），
    而 MB 实体要的是原始语料行。按 id 查一次是最省事、也最不容易漂的做法。

    关于 `count`（信封里的总命中数）：matcher 的 `top=N` 只给前 N 名，没有"只数不取"的开关，
    所以这里先要 `max(offset+limit, WS2_SEARCH_TOP)` 条 —— 常见查询下这就是**全部**命中，
    `count` 精确；真遇到上千条的宽查询（如 `1234` 命中 1301 首）时 `count` 是"至少这么多"
    （报的是取到的条数）。要精确到底就得每次跑全库，代价与收益不成比例，这里选择**说清楚**。
    """
    hits = module().search(rows, segs, int(fuzzy), int(want))
    idx = raw_index()["by_source"]
    idxf = raw_index()["by_file"]
    out = []
    for h in hits:
        rid = str(h.get("id") or "")
        f0 = str(h.get("file") or "")
        row = idx.get(rid.lower()) or idxf.get(os.path.splitext(f0)[0].lower())
        if row is None:
            # 极罕见（语料刚好在两次 stat 之间被重建）：退回命中自带的字段，别让整个查询挂掉
            row = {"title": h.get("title"), "artist": h.get("artist"), "tag": h.get("tag"),
                   "source": [rid], "file": [f0], "status": h.get("status"),
                   "n_notes": h.get("n_notes"), "bars": h.get("bars"),
                   "score": h.get("score"), "sections": []}
        ent = entity_of(row, score_head(f0), incs=())
        ent["match"] = {"diff": h.get("diff"), "pos": h.get("pos"), "sec": h.get("sec"),
                        "sec_cn": h.get("sec_cn"), "bar_from": h.get("bar_from"),
                        "bar_to": h.get("bar_to"), "seg": h.get("seg")}
        out.append(ent)
    return out


# `/ws/2/song` 的结果缓存（`_ws2_cache`，声明在文件上半部，见那段注释）。
def _mb_window(segs, fuzzy, want):
    """-> `(count, rows, exact)`：一次 matcher 调用定下 `count` 与本页要用的那一段。

    * `count` = 这次取到的条数；`exact` = 它是不是**真的**总命中数（取到的条数没顶到 `want`
      就是精确的 —— 语料里再没有别的命中，matcher 才会少给）。
    * `rows` 是这一段命中的 MB 实体；命中数太大（宽查询）时只留 `count`，不留实体
      （每条的 `score` 有 1~2KB，32 条缓存能把内存顶到几十 MB —— 那是事故不是优化）。

    为什么要"一次调用定下 count 与本页"：`offset` 翻页若各调一次 matcher，会拿到两次**独立重排**
    的排名（第二页可能与第一页重叠），而且 `count` 会随窗口变化 —— 分页的两条常识都破了。
    """
    key = (" ".join(segs), str(fuzzy), int(want))
    with _ws2_cache_lock:
        got = _ws2_cache.get(key)
        if got is not None:
            _ws2_cache.move_to_end(key)
            return got
    rows = _mb_hits(corpus_rows(), segs, fuzzy, want)
    store = (len(rows), rows if len(rows) < WS2_CACHE_ROWS_MAX else None, len(rows) < want)
    with _ws2_cache_lock:
        _ws2_cache[key] = store
        _ws2_cache.move_to_end(key)
        while len(_ws2_cache) > WS2_CACHE_MAX:
            _ws2_cache.popitem(last=False)
    return store


def _mb_window_slice(segs, fuzzy, limit, offset):
    """-> `(count, songs, count_exact)`：`songs` 只要"从 offset 开始的那 limit 条"。

    窗口大小 = `max(offset+limit, WS2_COUNT_MIN)`（封顶 `WS2_SEARCH_TOP`）：够翻到这一页，
    同时尽量把 `count` 看准。宽查询（全库几千首命中）不会把全库都物化成实体。
    """
    want = min(WS2_SEARCH_TOP, max(offset + limit, WS2_COUNT_MIN))
    count, rows, exact = _mb_window(segs, fuzzy, want)
    if rows is not None:
        return count, rows[offset:offset + limit], exact
    again = _mb_hits(corpus_rows(), segs, fuzzy, offset + limit)
    return count, again[offset:offset + limit], exact


def ws2_lookup(raw_id, query_string, base_url=""):
    """`GET /ws/2/song/<id>?fmt=json&inc=…` -> `(状态码, body, 额外响应头)`。"""
    p = parse_qs(query_string or "", keep_blank_values=True)
    incs = _split_inc(p.get("inc") or [])
    unknown = _unknown_incs(incs)
    headers = {"X-Unknown-Inc": ", ".join(unknown)} if unknown else {}
    bad = _fmt_error(p)
    if bad:
        return ws2_error(400, bad)
    miss = unavailable()
    if miss:
        return ws2_error(503, miss)
    rid = unquote(str(raw_id or "")).strip()
    if not rid:
        return ws2_error(400, "缺 id（要查的那一首，如 qupu123-268596 或文件名主干）", headers)
    try:
        row = find_row(rid)
    except Exception as e:
        return ws2_error(500, f"{type(e).__name__}: {e}", headers)
    if row is None:
        return ws2_error(404, f"没有这一首：{rid}", headers)
    try:
        return 200, entity_of(row, score_head(_first(row.get("file"))), incs), headers
    except Exception as e:
        return ws2_error(500, f"{type(e).__name__}: {e}", headers)


def ws2_search(query_string, base_url=""):
    """`GET /ws/2/song?query=…&limit=&offset=&fmt=json` -> `(状态码, body, 额外响应头)`。

    信封照 MusicBrainz：`{created, count, offset, songs[]}`；`count` 是**总命中数**，
    `songs` 是本次 `limit/offset` 切出来的那一段。
    """
    p = parse_qs(query_string or "", keep_blank_values=True)
    bad = _fmt_error(p)
    if bad:
        return ws2_error(400, bad)

    raw_limit = _q1(p, "limit", str(WS2_LIMIT_DEFAULT))
    limit, err = _parse_int(raw_limit, "limit")
    if err:
        return ws2_error(400, err)
    raw_offset = _q1(p, "offset", "0")
    offset, err = _parse_int(raw_offset, "offset")
    if err:
        return ws2_error(400, err)
    limit = max(1, min(WS2_LIMIT_MAX, limit))          # 上限钳到 100（照 MB：超大 limit 是钳不是错）
    fz = _q1(p, "fuzzy", "0")
    if fz not in FUZZY_VALUES:
        return ws2_error(400, f"fuzzy 只收 0/1/2（给的是 {fz}）")

    text = _query_text(p)
    if not text:
        return ws2_error(400, "缺 query（要查的旋律数字串，如 query=316316）")
    if len(text) > Q_MAX:
        return ws2_error(400, f"query 太长（{len(text)} > {Q_MAX}）")
    miss = unavailable()
    if miss:
        return ws2_error(503, miss)
    segs = module().split_query(text)
    if not segs:
        return ws2_error(400, "没认出任何音高数字（只认1-7）")
    try:
        # 一次调用定下 count 与本页：翻页时 count 不会变小，两页也不会取到两次不同的排名
        total, page, exact = _mb_window_slice(segs, fz, limit, offset)
    except Exception as e:
        return ws2_error(500, f"{type(e).__name__}: {e}")
    env = {"created": ws2_created(), "count": total, "offset": offset, "songs": page,
           # `count_exact` 不是 MB 的字段，但**必须给**：宽查询（全库上千首命中）时 count 是
           # "至少这么多"，调用方要能分辨"结果就这么少"和"服务只数到这里"。
           "count_exact": exact}
    return 200, env, {}


def _fmt_error(p):
    """`fmt` 只认 json：**空/缺** = 默认 json（照 MB），别的值 -> 400 文案。

    没有 `fmt` 时再看 `Accept`：**含 json 或 `*/*`** 才算"调用方要 json"；其余（含浏览器那种
    `text/html`）**一律当默认 json 放行** —— 这一层只有 JSON 一种表示，拒绝没有意义。
    """
    for v in (p.get("fmt") or []):
        if v and v.strip().lower() != "json":
            return f"fmt 只支持 json（给的是 {v}）；也可以不传 fmt（默认就是 json）"
    return None


def ws2_handle(path, query_string, ip="", accept="", base_url=""):
    """`/ws/2/*` 的唯一入口：路径 -> 上面三个函数之一。两份后端都只调它。

    `path` 是**不含查询串**的路径（如 `/ws/2/song/qupu123-268596`）。返回
    `(状态码, body, 额外响应头)` —— 与 `handle()` 同一契约，后端只负责搬上 HTTP。

    `accept` 收下但**不过滤**（只留作签名与将来扩展）：这一层只有 JSON 一种表示，
    而"用浏览器点开示例地址"必须能用 —— 浏览器导航发的是 `Accept: text/html`，
    拿它判 400 会把 `ws2_root()` 里那条可点示例变成一条打不开的链接。
    """
    qs = parse_qs(query_string or "", keep_blank_values=True)
    # `fmt` 只认 json；**不传 fmt 就当 json**（`Accept: application/json` 也认，但别的 Accept
    # 值**不报错** —— 浏览器导航默认发 `Accept: text/html`，把它判成 400 只会让"用浏览器点开
    # 看看"变成一件难事；JSON 才是这里的唯一表示，回 400 也变不出 HTML 来）。
    bad_fmt = _fmt_error(qs)
    if bad_fmt:
        return ws2_error(400, bad_fmt)

    allowed, retry = mb_rate_check(ip)
    if not allowed:
        # 503 + Retry-After: 1（MB 的调用方就按这个退避）
        return ws2_error(503, f"请求太快：每 IP 每秒最多 {WS2_RATE_PER_SEC} 次，"
                              f"{retry} 秒后再试", {"Retry-After": str(retry)})

    tail = unquote(path or "")[len(WS2_PREFIX):].strip("/")
    if tail == "":                                     # `/ws/2/`
        return 200, ws2_root(base_url), {}
    parts = tail.split("/")
    if parts[0] != WS2_ENTITY:
        return ws2_error(404, f"没有这个实体：{parts[0]}（本服务只有 {WS2_ENTITY}）")
    if len(parts) == 1:                                # `/ws/2/song`
        return ws2_search(query_string, base_url)
    if len(parts) == 2:                                # `/ws/2/song/<id>`
        return ws2_lookup(parts[1], query_string, base_url)
    return ws2_error(404, f"没有这个端点：{WS2_PREFIX}/{tail}")

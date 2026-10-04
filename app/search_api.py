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
"""
from __future__ import annotations

import importlib
import os
import sys
import threading
import time
from collections import OrderedDict
from urllib.parse import parse_qs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # 站点仓库根

Q_MAX = 200                       # q的上限（实测：用户哼错时会把整句贴上，200个数字已远超任何一段旋律）
TOP_MIN, TOP_MAX, TOP_DEFAULT = 1, 50, 20
FUZZY_VALUES = ("0", "1", "2")
CACHE_MAX = 200                   # 结果缓存条数：机器人的稳定查询就那么几十条，200足够
RATE_WINDOW = 60.0                # 限流窗口（秒）
CACHE_STAT = {"hit": 0, "miss": 0}


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
    return _rows


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

# -*- coding: utf-8 -*-
"""**离线层**：只用包内 `corpus/` 那一份索引算，**一次都不联网**。

* 载入：`corpus/index.jsonl.gz`（gzip + 每行一个 JSON）-> 一个 list[dict]，就是匹配器的行结构，
  外加 `sections`（段落元信息）。载入是**一次性**的（首次真正用到时载，带锁），之后查询全在内存里。
* 检索：直接调 `melody_search_vendored.search()`（**权威算法一字未改**），
  所以离线结果与直接跑 `py -3.13 jianpu2\\tools\\melody_search.py <串> --json` 逐字段一致
  —— 这件事有对拍脚本 `tests/parity_check.py`，不是"应该一致"。
* `count` 的口径抄站点 `/ws/2/`：交给匹配器 `min(1000, max(limit, 120))` 条，
  取到的条数没顶到这个数就是**精确**的（`count_exact=true`），顶到了就照实说"至少这么多"。
* `stats()` 读包内 `corpus/manifest.json`：不联网也能答曲数/音符数/各来源。
"""
from __future__ import annotations

import gzip
import json
import os
import sys
import threading
import time
from typing import Any, Iterator

from . import INDEX_PATH, MANIFEST_PATH, VERSION
from . import melody_search_vendored as ms

# 与站点 `/ws/2/` 同一口径的三个数（search_api.py 第 50/64/65 行）
Q_MAX = 200                 # 查询串长度上限
WS2_SEARCH_TOP = 1000       # 交给匹配器的"最多要多少条"硬上限
WS2_COUNT_MIN = 120         # 为了让 count 尽量精确，至少要看这么多条

INC_ALLOWED = ("artists", "tags", "links", "sections", "score")
INC_DEFAULT = ("artists", "tags", "links", "sections")

# 精简语料**不含**的字段（离线时只能照实说"没有"，不能编）：
#   links/transcriber/alias/MBID 在站点那边来自 `scores/<file>.txt` 的头部，data.jsonl 里没有；
#   精简语料按用途只留了检索与展示要用的那些。
OFFLINE_MISSING = ("links", "transcriber", "alias", "musicbrainz_work_id")


def rss_mb() -> float | None:
    """本进程常驻内存（MB）。装了 `psutil` 就用它；没装就走 `ctypes`（Windows）/`/proc`（Linux）；
    都拿不到就返回 `None` —— 这只是个读数，**不硬依赖**任何第三方包。"""
    try:
        import psutil
        return round(psutil.Process(os.getpid()).memory_info().rss / 1048576, 1)
    except Exception:
        pass
    try:                                        # Windows：K32GetProcessMemoryInfo
        import ctypes
        from ctypes import wintypes

        class _PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get = k32.K32GetProcessMemoryInfo
        get.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]
        get.restype = wintypes.BOOL
        cur = k32.GetCurrentProcess
        cur.restype = wintypes.HANDLE
        pmc = _PMC()
        pmc.cb = ctypes.sizeof(_PMC)
        if get(cur(), ctypes.byref(pmc), pmc.cb):
            return round(pmc.WorkingSetSize / 1048576, 1)
    except Exception:
        pass
    try:                                        # Linux：/proc/self/statm
        with open("/proc/self/statm", encoding="utf-8") as f:
            pages = int(f.read().split()[1])
        return round(pages * os.sysconf("SC_PAGE_SIZE") / 1048576, 1)
    except Exception:
        return None


def read_jsonl_gz(path: str) -> Iterator[dict]:
    """逐行读 `.jsonl.gz`（`gzip` 是标准库，不需要第三方）。"""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                yield json.loads(ln)


def _err(text: str) -> str:
    return text


class OfflineCorpus:
    """包内索引 + 三个只读查询。线程安全（载入与查询都上锁，MCP 客户端可能并发调）。"""

    def __init__(self, index_path: str | None = None, manifest_path: str | None = None,
                 verbose: bool = True):
        self.index_path = index_path or INDEX_PATH
        self.manifest_path = manifest_path or MANIFEST_PATH
        self.verbose = verbose
        self._lock = threading.RLock()
        self._rows: list[dict] | None = None
        self._by_id: dict[str, dict] = {}
        self._by_file: dict[str, dict] = {}
        self._manifest: dict | None = None
        self.load_seconds: float | None = None
        self.load_rss_mb: float | None = None
        self.load_peak_rss_mb: float | None = None

    # ── 载入 ────────────────────────────────────────────────────────────────────
    @property
    def loaded(self) -> bool:
        return self._rows is not None

    def load(self) -> list[dict]:
        """把索引读进内存（只做一次）。返回行列表（匹配器行结构 + `sections`）。"""
        with self._lock:
            if self._rows is not None:
                return self._rows
            if not os.path.isfile(self.index_path):
                raise FileNotFoundError(
                    f"包内索引不存在：{self.index_path}\n"
                    f"先重建：py -3.13 rebuild_index.py（需要 jianpu-db/data.jsonl）")
            t0 = time.perf_counter()
            rows = list(read_jsonl_gz(self.index_path))
            self.load_seconds = round(time.perf_counter() - t0, 2)
            self._rows = rows
            for r in rows:
                rid = str(r.get("id") or "").lower()
                if rid:
                    self._by_id.setdefault(rid, r)
                stem = os.path.splitext(str(r.get("file") or ""))[0].lower()
                if stem:
                    self._by_file.setdefault(stem, r)
            self.load_rss_mb = rss_mb()
            self.load_peak_rss_mb = self.load_rss_mb
            if self.verbose:
                print(f"[jianpu-db-mcp {VERSION}] 离线索引已载入：{len(rows)} 首 · "
                      f"{self.load_seconds}s · 常驻 {self.load_rss_mb} MB · {self.index_path}",
                      file=sys.stderr, flush=True)
            return rows

    @property
    def rows(self) -> list[dict]:
        return self.load()

    def manifest(self) -> dict:
        with self._lock:
            if self._manifest is None:
                try:
                    with open(self.manifest_path, encoding="utf-8") as f:
                        self._manifest = json.load(f)
                except (OSError, ValueError):
                    self._manifest = {}
            return self._manifest

    def find_row(self, key: str) -> dict | None:
        """id -> 行：`source`（如 `qupu123-268596`）或文件名主干（如 `水手`），与站点同口径。"""
        self.load()
        k = str(key or "").strip().lower()
        if not k:
            return None
        return self._by_id.get(k) or self._by_file.get(k)

    # ── 工具 ①：按旋律查歌 ──────────────────────────────────────────────────────
    def _compact(self, h: dict) -> dict:
        """命中 -> 只留"判断是不是这首"要用的字段（与远程模式同一个形状）。

        `file` 也在里面：它是这一首在语料里的谱面文件名（`search_melody` 的 `id` 有时是
        `source`、有时是 `f-<主干>`，给上 `file` 才能一眼对回 `scores/<file>`）。
        """
        out: dict[str, Any] = {
            "id": h.get("id"),
            "title": h.get("title"),
            "file": h.get("file"),
            "artists": list(h.get("artist") or []),
            "tags": [str(t) for t in (h.get("tag") or [])],
            "status": h.get("status"),
            "source": h.get("source"),
            "n_notes": h.get("n_notes"),
            "sections": [dict(x) for x in (h.get("sections") or []) if isinstance(x, dict)],
            "match": {"diff": h.get("diff"), "sec_cn": h.get("sec_cn"),
                      "bar_from": h.get("bar_from"), "bar_to": h.get("bar_to"),
                      "seg": h.get("seg")},
        }
        return {k: v for k, v in out.items() if v not in (None, [], "", {})}

    def search(self, digits: str, fuzzy: int = 0, limit: int = 5) -> str:
        raw = str(digits or "")
        if len(raw) > Q_MAX:
            return _err(f"参数不对：查询串最多 {Q_MAX} 个字符（给的是 {len(raw)} 个）。"
                        f"`digits` 只认音高数字 1-7（如 316316）。")
        segs = ms.split_query(raw)
        if not segs:
            return _err("没认出任何音高数字：`digits` 只认 1-7（简谱的哆来咪…），"
                        "空串、只给 0/x/- 或只给中文都算无效。")
        fz = max(0, min(2, int(fuzzy)))
        lim = max(1, min(100, int(limit)))
        rows = self.load()
        want = min(WS2_SEARCH_TOP, max(lim, WS2_COUNT_MIN))     # 口径同站点 `/ws/2/song`
        t0 = time.perf_counter()
        hits = ms.search(rows, segs, fz, want)
        took_ms = round((time.perf_counter() - t0) * 1000)
        count, exact = len(hits), len(hits) < want
        return json.dumps({
            "query": " ".join(segs), "fuzzy": fz, "count": count, "count_exact": exact,
            "returned": len(hits[:lim]), "songs": [self._compact(h) for h in hits[:lim]],
            "note": ("离线：本包自带索引，未联网；hits 数与字段与 `melody_search.py --json` 同源"
                     if exact else
                     f"count 是**至少**这么多（只数到 {want} 条）：把 digits 写长一点能收窄"),
            "mode": "offline",
            "elapsed_ms": took_ms,
        }, ensure_ascii=False, indent=2)

    # ── 工具 ②：取单首 ─────────────────────────────────────────────────────────
    def get_song(self, id: str, inc: list[str] | None = None, score_chars: int = 0) -> str:
        want = [str(x).strip().lower() for x in (inc or INC_DEFAULT) if str(x).strip()]
        unknown = [x for x in want if x not in INC_ALLOWED]
        if unknown:
            return _err(f"参数不对：inc 里这几个值不认识：{'、'.join(unknown)}"
                        f"（可用：{'、'.join(INC_ALLOWED)}）")
        row = self.find_row(id)
        if row is None:
            return _err(f"没有这一条：{id}。id 可以是 `source`（如 qupu123-268596）"
                        f"或文件名主干（如 0932）；离线检索结果的 `id` 也是这一套。")
        n = max(0, int(score_chars))
        out: dict[str, Any] = {
            "id": row.get("id"), "title": row.get("title"), "file": row.get("file"),
            "source": row.get("source"), "site": row.get("site"), "status": row.get("status"),
            "n_notes": row.get("n_notes"), "bars": row.get("bars"),
            "artists": list(row.get("artist") or []),
            "tags": [str(t) for t in (row.get("tag") or [])],
            "sections": [dict(x) for x in (row.get("sections") or [])],
            "inc": want, "mode": "offline",
            "note": (f"离线：字段来自包内精简语料；"
                     f"{'、'.join(OFFLINE_MISSING)}这几项不在精简语料里"
                     f"（站点那边来自 scores/<file>.txt 的头部）。"),
        }
        if n > 0:
            score = str(row.get("score") or "")
            out["score"] = score[:n]
            out["score_chars_total"] = len(score)
        return json.dumps({k: v for k, v in out.items() if v not in (None, [], "")},
                          ensure_ascii=False, indent=2)

    # ── 工具 ③：曲库规模（读包内 manifest，不联网）──────────────────────────────
    def stats(self) -> str:
        man = self.manifest()
        rows = self.load()
        counts = man.get("counts") or {}
        out: dict[str, Any] = {
            "mode": "offline",
            "songs": counts.get("songs") or len(rows),
            "notes": counts.get("notes") or sum(int(r.get("n_notes") or 0) for r in rows),
            "groups": counts.get("groups"),
            "sources": counts.get("sources"),
            "status": counts.get("status"),
            "source_of_stats": "包内 corpus/manifest.json（rebuild_index.py 产出，不联网）",
            "corpus": man.get("source"),
            "index": {"rows": len(rows), "load_seconds": self.load_seconds,
                      "rss_mb": self.load_rss_mb, **(man.get("index") or {})},
            "api": {"status": "offline", "detail": "本包自带索引，不发任何请求"},
            "user_agent": f"jianpu-db-mcp/{VERSION} (offline)",
        }
        return json.dumps({k: v for k, v in out.items() if v not in (None, [], "")},
                          ensure_ascii=False, indent=2)


CORPUS = OfflineCorpus()          # server.py 用的单例（进程内一份）

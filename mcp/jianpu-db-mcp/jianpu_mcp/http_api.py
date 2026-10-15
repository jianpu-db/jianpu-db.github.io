# -*- coding: utf-8 -*-
"""**远程层**（可选）：`JIANPU_BASE` 指向的那套 `/ws/2/*` 的客户端 —— 与上一版**行为一致**。

只在 `resolve_mode()` 判成 `remote` 时才被 import（默认离线时这份代码**根本不会加载**，
更不会发请求）。三条老规矩照旧：

① 每 IP 每秒 1 次的限流：进程内串行化 + 撞上 `503` 按 `Retry-After` **退避一次**；
② 失败翻成人话（尤其 `530/1016` = Cloudflare 隧道上游不可达，也就是"那台机器坏了"这个症状）；
③ 只依赖标准库 `urllib`（连同官方 `mcp` SDK，总共两个依赖）。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import UA, VERSION, DEFAULT_BASE, PKG_DIR

# `stats.json` 是 CI 随语料/前端索引产出的那份（曲数/音符数/各来源条数），在**站点仓库**的
# `data/` 下。本机没跑过那个构建时找不到，`stats()` 会退回"打一次接口"。
STATS_FILES = [
    os.environ.get("JIANPU_STATS", "").strip(),
    os.environ.get("JIANPU_DB", "").strip() and os.path.join(os.environ["JIANPU_DB"], "stats.json"),
    os.path.join(PKG_DIR, "corpus", "stats.json"),
    os.path.join(os.path.dirname(PKG_DIR), "jianpu-db.github.io", "data", "stats.json"),
    r"D:\Documents_D\jianpu-db.github.io\data\stats.json",
]


class ApiError(Exception):
    """接口层失败（网络/限流/非 200）。`retry_after` 只在 503 时有值。"""

    def __init__(self, message: str, *, status: int = 0, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def _short(msg: str, n: int = 200) -> str:
    """压成一句话：错误体可能是 Cloudflare 的一整页 HTML（隧道断了的时候就是这样）。"""
    text = re.sub(r"\s+", " ", str(msg or "")).strip()
    return text if len(text) <= n else text[:n] + "…"


def _dump(obj: Any) -> str:
    """工具返回值统一走这一份：UTF-8、缩进 2、不转义中文（模型看得清，人也能读）。"""
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _compact_song(s: dict, *, score_chars: int = 0) -> dict:
    """把一条实体压成"判断是不是这首歌"要用的字段（`score_chars>0` 时才带上谱面片段）。"""
    out: dict[str, Any] = {
        "id": s.get("id"),
        "title": s.get("title"),
        "file": s.get("file"),
        "artists": s.get("artists") or [],
        "tags": [t.get("name") for t in (s.get("tags") or []) if isinstance(t, dict)],
        "status": s.get("status"),
        "source": s.get("source"),
        "n_notes": s.get("n_notes"),
        "sections": [{"subtitle": x.get("subtitle"), "name_cn": x.get("name_cn"),
                      "n_notes": x.get("n_notes")} for x in (s.get("sections") or [])],
    }
    m = s.get("match")
    if isinstance(m, dict):
        out["match"] = {k: m.get(k) for k in ("diff", "sec_cn", "bar_from", "bar_to", "seg")}
    if score_chars > 0:
        score = str(s.get("score") or "")
        out["score"] = score[:score_chars]
        out["score_chars_total"] = len(score)
    return {k: v for k, v in out.items() if v not in (None, [], "")}


class RemoteApi:
    """`JIANPU_BASE` 那套接口的客户端（基址在构造时定死，进程内一份）。"""

    def __init__(self, base: str = "", timeout: float | None = None):
        self.base = (base or DEFAULT_BASE).rstrip("/")
        self.timeout = float(timeout or os.environ.get("JIANPU_TIMEOUT") or 120)
        self._last_call = 0.0
        self._lock = threading.Lock()

    # ── HTTP：限流退避 + 人话错误 ───────────────────────────────────────────────
    def _get(self, path: str, params: dict[str, Any] | None = None,
             timeout: float | None = None) -> dict:
        """打一次 `/ws/2/*`。返回解析好的 JSON；失败抛 `ApiError`（**不**自己编造空结果）。

        限流处理：503 且带 `Retry-After` -> 等那个秒数（默认 1，封顶 10）再试**一次**；
        再不行就把原始原因抛出去，由工具层翻成人话。
        """
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        with self._lock:
            # 主动让路：距上次请求不到 1.05 秒就先等一等（接口是每 IP 每秒 1 次）。
            wait = 1.05 - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            last: ApiError | None = None
            for attempt in (1, 2):
                try:
                    with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                        self._last_call = time.monotonic()
                        return json.loads(r.read().decode("utf-8"))
                except urllib.error.HTTPError as e:
                    self._last_call = time.monotonic()
                    raw = e.read().decode("utf-8", "replace").strip()
                    try:
                        msg = (json.loads(raw) or {}).get("error") or raw
                    except ValueError:
                        msg = raw[:200]
                    ra = e.headers.get("Retry-After") if e.headers else None
                    delay = min(max(float(ra or 1), 0.5), 10.0) if e.code == 503 else 0.0
                    last = ApiError(f"HTTP {e.code} {msg}", status=e.code, retry_after=delay or None)
                    if e.code == 503 and attempt == 1:
                        time.sleep(delay or 1.0)
                        continue
                    raise last from None
                except urllib.error.URLError as e:
                    self._last_call = time.monotonic()
                    raise ApiError(f"连不上 {self.base}（{e.reason}）") from None
                except TimeoutError:
                    self._last_call = time.monotonic()
                    raise ApiError(f"{self.base} 超过 {timeout or self.timeout:.0f} 秒没响应") from None
            raise last or ApiError("未知失败")

    def _hint(self, err: ApiError) -> str:
        """把接口错误翻成"人和模型都能照着做事"的一句。"""
        msg = _short(err)
        if err.status == 503:
            return (f"接口此刻不可用或太频繁：{msg}。线上 `/ws/2/*` 是 Cloudflare Worker 反代到"
                    f"**作者本机**，本机不在线就查不了；本地实例可用 JIANPU_BASE={self.base} 另指"
                    f"（8770 标准库版 / 8775 FastAPI 版）。")
        if err.status == 400:
            return f"参数不对：{msg}。`digits` 只认音高数字 1-7（如 316316），最多 200 个字符。"
        if err.status == 404:
            return f"没有这一条：{msg}。id 可以是 `source`（如 qupu123-268596）或文件名主干（如 水手）。"
        if err.status == 0:
            return f"网络层失败：{msg}。检查基址（现在是 {self.base}）与网络，然后重试。"
        # 530（Cloudflare 的 Origin DNS error）是这条链路最常见的"看起来是 HTTP、其实是本机不在线"：
        # 说清楚该怎么办，别让模型拿这串 HTML 去猜。
        tail = ("。线上 `/ws/2/*` 由 Cloudflare Worker 反代到**作者本机**，"
                "530/1016 就是那条隧道的上游不可达（本机没开或隧道断了）——"
                "换本地实例再试：JIANPU_BASE=http://127.0.0.1:8770（标准库版）或 :8775（FastAPI 版）")
        return f"接口回错了：{msg}{tail if err.status == 530 else ''}"

    # ── 工具 ①：按旋律查歌 ──────────────────────────────────────────────────────
    def search_melody(self, digits: str, fuzzy: int = 0, limit: int = 5) -> str:
        fz = max(0, min(2, int(fuzzy)))
        lim = max(1, min(100, int(limit)))
        try:
            env = self._get("/ws/2/song", {"query": digits, "fuzzy": fz, "limit": lim,
                                           "fmt": "json"})
        except ApiError as e:
            return self._hint(e)
        songs = env.get("songs") or []
        return _dump({
            "query": digits, "fuzzy": fz, "count": env.get("count"),
            "count_exact": env.get("count_exact"), "returned": len(songs),
            "songs": [_compact_song(s) for s in songs],
            "note": ("count 是命中总数；count_exact=false 说明服务只数到这个窗口（宽查询），"
                     "翻页用 offset（本工具没暴露）或把 digits 写长一点"
                     if env.get("count_exact") is False else "命中就是这么多"),
            "mode": "remote", "base": self.base,
        })

    # ── 工具 ②：取单首 ─────────────────────────────────────────────────────────
    def get_song(self, id: str, inc: list[str] | None = None, score_chars: int = 0) -> str:
        want = list(inc) if inc else ["artists", "tags", "links", "sections"]
        allowed = {"artists", "tags", "links", "sections", "score"}
        unknown = [x for x in want if x not in allowed]
        want = [x for x in want if x in allowed]
        if unknown:
            return self._hint(ApiError(f"inc 里这几个值不认识：{'、'.join(unknown)}"
                                       f"（可用：{'、'.join(sorted(allowed))}）", status=400))
        try:
            s = self._get(f"/ws/2/song/{urllib.parse.quote(str(id))}",
                          {"fmt": "json", "inc": "+".join(want)})
        except ApiError as e:
            return self._hint(e)
        out = _compact_song(s, score_chars=max(0, int(score_chars)))
        for k in ("file", "transcriber", "alias", "bars", "musicbrainz_work_id", "site"):
            if s.get(k):
                out[k] = s[k]
        out["links"] = [x.get("url") for x in (s.get("links") or []) if isinstance(x, dict)]
        out["inc"] = want
        out["mode"] = "remote"
        out["base"] = self.base
        return _dump(out)

    # ── 工具 ③：曲库规模 ───────────────────────────────────────────────────────
    def _stats_from_file(self) -> dict | None:
        """从 CI 产出的 `stats.json` 读（曲数/音符数/各来源）—— 不用花掉一次 HTTP 配额。"""
        for p in STATS_FILES:
            if not p or not os.path.isfile(p):
                continue
            try:
                with open(p, encoding="utf-8") as f:
                    j = json.load(f)
            except (OSError, ValueError):
                continue
            if not isinstance(j, dict) or not j.get("songs"):
                continue
            return {"songs": j.get("songs"), "notes": j.get("notes"), "groups": j.get("groups"),
                    "sources": j.get("sources"), "with_images": j.get("with_images"),
                    "stats_file": p, "stats_mtime": time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(p)))}
        return None

    def stats(self) -> str:
        out: dict[str, Any] = {"base": self.base, "user_agent": UA, "mode": "remote"}
        local = self._stats_from_file()
        if local:
            out.update({k: v for k, v in local.items() if v is not None})
            out["source_of_stats"] = "本地 stats.json（CI 随语料产出）"
        try:
            root = self._get("/ws/2/")
            out["api"] = {"name": root.get("name"), "version": root.get("version"),
                          "created": root.get("created"), "status": "ok"}
            out["rate_limit"] = (root.get("rate_limit") or {}).get("policy")
        except ApiError as e:
            out["api"] = {"status": "unavailable", "reason": str(e)}
            if not local:
                out["hint"] = self._hint(e)
                return _dump(out)
            out["hint"] = ("接口此刻不可用，但曲库规模照样能报（见 source_of_stats / stats_file）："
                           "线上 `/ws/2/*` 反代到作者本机，本机不在线就查不了；"
                           "基址可用 JIANPU_BASE 另指本地实例。")
        if "source_of_stats" not in out:
            out["source_of_stats"] = "接口 `/ws/2/`（本地没找到 stats.json）"
        return _dump(out)

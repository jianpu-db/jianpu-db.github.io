# -*- coding: utf-8 -*-
"""jianpu-db-mcp 的包内实现：**默认离线**（自带索引，不联网），可选远程 HTTP。

    jianpu_mcp/
      ├─ jptok_vendored.py            简谱 token 的唯一实现（vendored 副本，代码逐字节同上游）
      ├─ melody_search_vendored.py    权威匹配器（vendored 副本，算法一字未改）
      ├─ slim_fields.py               精简语料/运行索引的字段清单与行构造函数
      ├─ offline.py                   离线层：载入包内索引 -> search / get_song / stats
      └─ http_api.py                  远程层：`JIANPU_BASE` 的 `/ws/2/*` 客户端（退避 + 人话报错）

两种模式由环境变量定（`resolve_mode()` 是唯一判据）：

  * **默认：离线** —— 一次都不联网，直接读包内 `corpus/index.jsonl.gz`；
  * **可选：远程** —— 设了 `JIANPU_BASE` 就走 HTTP（行为与上一版一致）；
  * `JIANPU_MODE=offline` 可在设了 `JIANPU_BASE` 时**强制**离线（自检用）。
"""
from __future__ import annotations

import os

VERSION = "0.2.0"

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))    # 包根（含 server.py）
CORPUS_DIR = os.environ.get("JIANPU_CORPUS") or os.path.join(PKG_DIR, "corpus")
INDEX_PATH = os.environ.get("JIANPU_INDEX") or os.path.join(CORPUS_DIR, "index.jsonl.gz")
MANIFEST_PATH = os.environ.get("JIANPU_MANIFEST") or os.path.join(CORPUS_DIR, "manifest.json")
SLIM_PATH = os.path.join(CORPUS_DIR, "slim.jsonl.gz")

DEFAULT_BASE = "https://jianpu-db.org"
UA = f"jianpu-db-mcp/{VERSION} (+https://jianpu-db.org/ws/2/)"

MODE_ENV = "JIANPU_MODE"
BASE_ENV = "JIANPU_BASE"


def resolve_mode() -> tuple[str, str]:
    """-> `(模式, 远程基址)`。模式是 `"offline"` 或 `"remote"`（离线时基址为空串）。

    判据只有这一处：`JIANPU_MODE` 显式指定优先；否则**设了 `JIANPU_BASE` 就算要远程**，
    没设就是离线。远程基址默认 `https://jianpu-db.org`（只在显式要远程时才用得上）。
    """
    mode = (os.environ.get(MODE_ENV) or "").strip().lower()
    base = (os.environ.get(BASE_ENV) or "").strip().rstrip("/")
    if mode in ("offline", "local", "none"):
        return "offline", ""
    if mode in ("remote", "http", "online"):
        return "remote", base or DEFAULT_BASE
    return ("remote", base) if base else ("offline", "")

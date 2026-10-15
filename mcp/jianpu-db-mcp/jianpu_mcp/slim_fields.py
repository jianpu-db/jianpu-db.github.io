# -*- coding: utf-8 -*-
"""精简语料与运行索引的**字段清单**，以及"从原始语料行裁出检索需要的字段"的那几个函数。

两份产物（都由 `rebuild_index.py` 生成，改动只在这一个文件里）：

  * `corpus/slim.jsonl.gz`  —— **精简语料**：只留检索与展示要用的字段，人可读、可核对，
                               也是"重建"这条链路的复核基准（字段就是下面 `SLIM_FIELDS`）；
  * `corpus/index.jsonl.gz` —— **运行索引**：离线模式真正载入的那一份（`INDEX_FIELDS`），
                               在精简语料之上加了 `digits`/`sec`/`pop`/`hot`（匹配器算出来的）
                               与 `sections`/`site` 等展平字段，并把 `sections[].score` 折成
                               `{subtitle, name_cn, n_notes}` —— **同一份谱面不存两遍**。

`corpus_identity()` 与 `sections_meta()` 是照 `melody_search_vendored.build_corpus()`（第 199-216 行）
与站点 `/ws/2/` 的 `mb_sections()`（search_api.py 第 628-638 行）**抄的行结构**；两份代码
必须是同一份口径，这件事由 `rebuild_index.py --verify` 把关：把精简语料重新解一遍，
逐字段与运行索引对拍，对不上就报错（不是"看着差不多"）。
"""
from __future__ import annotations

import os

# 精简语料字段（用户 2026-10 定的清单）+ `tag`。
# ⚠ `tag` 是**必须**留的：匹配器的排名口径里 `hot`（歌手/标签在语料里的谱数）就来自
#   `artist + tag`，砍掉它会让离线排名与权威匹配器**不一致**（同分时的次序会变）。
SLIM_FIELDS = ("file", "title", "artist", "tag", "source", "status", "n_notes", "bars",
               "sections", "score")

# 运行索引字段：前 12 个与匹配器的行结构同名同义（`search()`/`hit_detail()` 直接吃它），
# 后 4 个是展平/预算出来的（`sections` = 段落元信息、`pop`/`hot` = 并列时的排序键）。
INDEX_FIELDS = ("id", "file", "title", "artist", "tag", "source", "site", "status", "n_notes",
                "bars", "digits", "sec", "sections", "score", "pop", "hot")
INDEX_VERSION = 1


def slim_row(r: dict) -> dict:
    """原始语料行 -> 精简语料行（**值原样照抄**，空值不写，省体积）。

    `status` 永远写：匹配器用它做白名单（`status ∈ ok/ocr/converted`），少了这一格
    这首就会被**静默跳过**——正是"索引丢音"那一类坑。
    """
    out = {}
    for k in SLIM_FIELDS:
        v = r.get(k)
        if k == "status" or v not in (None, "", [], {}):
            out[k] = v
    return out


def corpus_identity(r: dict) -> dict:
    """原始语料行 -> 检索行的"身份"字段（照 `build_corpus` 第 199-216 行抄，一字不改口径）。

    返回 `file/source/site/id/title/artist/tag/n_notes/status`；`id` 与站点的
    `/ws/2/song/<id>` 认的两种写法一致（`source` 优先，其次是文件名主干 `f-<stem>`）。
    """
    st = r.get("status")
    st = st if isinstance(st, list) else [st]
    src = r.get("source") or ""
    src = src[0] if isinstance(src, list) else src
    f0 = (r.get("file") or [""])[0]
    return {
        "title": r.get("title") or "",
        "artist": list(r.get("artist") or []),
        "tag": list(r.get("tag") or []),
        "source": src,
        "site": (src.split("-")[0] if src else ""),
        "file": f0,
        "id": src or ("f-" + os.path.splitext(f0)[0]),
        "n_notes": int(r.get("n_notes") or 0),
        "status": (st[0] or ""),
    }


def status_ok(r: dict, ok_status) -> bool:
    """`status` 白名单（与 `build_corpus` 同一口径：列表里任一命中即算）。"""
    st = r.get("status")
    st = st if isinstance(st, list) else [st]
    return any((x or "") in ok_status for x in st)


def sections_meta(row: dict, digits_of, sec_label) -> list:
    """`sections[]` -> `[{subtitle, name_cn, n_notes}]`（照站点 `mb_sections()` 抄）。

    音符数用**同一份 token 口径**数（`digits_of`）；空 `subtitle` 记成 `"score"`（= 整曲）。
    """
    out = []
    for s in (row.get("sections") or []):
        if not isinstance(s, dict):
            continue
        name = (s.get("subtitle") or "").strip() or "score"
        out.append({"subtitle": name, "name_cn": sec_label(name),
                    "n_notes": len(digits_of(s.get("score") or ""))})
    return out

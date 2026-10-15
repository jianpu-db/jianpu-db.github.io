# -*- coding: utf-8 -*-
"""从 `jianpu-db/data.jsonl` **重建包内精简语料与运行索引** —— 一条命令。

    py -3.13 rebuild_index.py                  # 生成 corpus/{slim.jsonl.gz, index.jsonl.gz, manifest.json}
    py -3.13 rebuild_index.py --verify         # 生成完再逐字段自检（慢一倍，值得跑）
    py -3.13 rebuild_index.py --no-cache       # 不借匹配器在 ~/.cache 里的数字索引，从头切一遍
    py -3.13 rebuild_index.py --data D:\\...\\data.jsonl --out D:\\tmp\\corpus

它做什么（每一份产物都能复核，见 `manifest.json` 里的 sha256/行数/字段清单）：

  ① **精简语料** `slim.jsonl.gz`：`file/title/artist/tag/source/status/n_notes/bars/sections/score`
     —— 与权威匹配器吃的那份字段**同名同形**（`file` 仍是列表、`sections[].score` 原样保留），
     所以"拿精简语料跑匹配器"与"拿 data.jsonl 跑匹配器"得到的是同一批行；
  ② **运行索引** `index.jsonl.gz`：匹配器自己算出来的行（含 `digits`/`sec`/`pop`/`hot`）
     + `score` + 段落元信息，离线模式只读这一份；
  ③ **manifest.json**：源文件路径/大小/mtime/sha256/行数、两份产物的 sha256 与行数、字段清单、
     曲数/音符数/各来源/各状态、匹配器版本口径（`TOKVER`/`OK_STATUS`/`MAX_GAP`…）。

口径：算法**不在本文件里** —— digits/段落/排名全部调 `jianpu_mcp.melody_search_vendored`
（权威匹配器的包内副本），所以产物与 `melody_search.py` 同源；本文件只负责"搬运与记账"。
首次冷启约 40 秒（切 640 万个音），借到匹配器缓存时约 1~3 秒；峰值内存实测约 200 MB。
"""
from __future__ import annotations

import argparse
import gc
import gzip
import hashlib
import io
import json
import os
import sys
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from jianpu_mcp import VERSION                                              # noqa: E402
from jianpu_mcp import melody_search_vendored as ms                          # noqa: E402
from jianpu_mcp.slim_fields import (INDEX_FIELDS, INDEX_VERSION, SLIM_FIELDS,  # noqa: E402
                                    corpus_identity, sections_meta, slim_row,
                                    status_ok)

DATA_CANDS = [
    os.environ.get("JIANPU_DB", "").strip() and os.path.join(os.environ["JIANPU_DB"], "data.jsonl"),
    os.path.join(HERE, "corpus", "data.jsonl"),
    os.path.join(os.path.dirname(HERE), "jianpu-db", "data.jsonl"),
    r"D:\Documents_D\jianpu-db\data.jsonl",
]


def find_data(explicit: str = "") -> str:
    for p in ([explicit] if explicit else []) + [c for c in DATA_CANDS if c]:
        if p and os.path.isfile(p):
            return os.path.abspath(p)
    raise SystemExit("✗ 找不到 data.jsonl：用 --data 指，或把 JIANPU_DB 指向语料仓库目录。\n"
                     "  找过这些位置：\n    " + "\n    ".join(x for x in DATA_CANDS if x))


def open_gz_text(path: str):
    """写 `.gz` 文本流：**固定 mtime=0、不写文件名** -> 同样的输入得到逐字节相同的产物。"""
    raw = open(path, "wb")
    gz = gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=9, mtime=0)
    return raw, gz, io.TextIOWrapper(gz, encoding="utf-8", newline="\n")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_slim(data_path: str, out_path: str) -> dict:
    """语料 -> 精简语料（流式，一行一行写；顺带按**与 build_corpus 同一道过滤**记段落元信息）。

    ⚠ 过滤与顺序必须和 `build_corpus` 一模一样（`status ∈ ok/ocr/converted` **且** digits 非空），
    这样下面那个 `sections_meta` 列表才能**按行序**与索引对齐 —— 语料里 `id`（如 `unknown`）
    会重复，按 id 建字典必然把同名的那几首错配（第一次自检就是这么报出来的）。
    """
    t0 = time.perf_counter()
    src_hash = hashlib.sha256()
    lines = status_ok_lines = 0
    metas: list[list] = []
    raw, gz, txt = open_gz_text(out_path)
    try:
        with open(data_path, "rb") as f:
            for bline in f:
                src_hash.update(bline)
                if not bline.strip():
                    continue
                lines += 1
                r = json.loads(bline.decode("utf-8"))
                txt.write(json.dumps(slim_row(r), ensure_ascii=False) + "\n")
                if not status_ok(r, ms.OK_STATUS):
                    continue
                status_ok_lines += 1
                if not ms.digits_of(r.get("score") or ""):
                    continue                      # 与 build_corpus 一样：没有音的行不进索引
                metas.append(sections_meta(r, ms.digits_of, ms.sec_label))
    finally:
        txt.close()
        raw.close()
    return {"lines": lines, "kept_status_ok": status_ok_lines, "sections_meta": metas,
            "source_sha256": src_hash.hexdigest(), "seconds": round(time.perf_counter() - t0, 1)}


def write_index(rows: list[dict], metas: list, out_path: str) -> dict:
    """匹配器算好的行 -> 运行索引（`sections` 用段落元信息，不再存一遍段内谱面）。

    `metas` 与 `rows` **按行序一一对应**（两边同一道过滤），对不上就当场报错 —— 宁可失败，
    也不写出一份"错位的段落信息"。
    """
    if len(metas) != len(rows):
        raise SystemExit(f"✗ 段落元信息 {len(metas)} 条 ≠ 索引 {len(rows)} 行："
                         f"精简语料与匹配器的过滤口径不一致，先查 slim_fields.status_ok 那段")
    t0 = time.perf_counter()
    raw, gz, txt = open_gz_text(out_path)
    try:
        for k, r in enumerate(rows):
            row = {key: r.get(key) for key in INDEX_FIELDS}
            row["sections"] = metas[k]
            for key in ("artist", "tag", "bars", "sec"):
                if row.get(key) is None:
                    row[key] = []
            txt.write(json.dumps(row, ensure_ascii=False) + "\n")
    finally:
        txt.close()
        raw.close()
    return {"rows": len(rows), "seconds": round(time.perf_counter() - t0, 1)}


def verify(slim_path: str, index_path: str) -> dict:
    """把精简语料**重新解一遍**，逐字段与运行索引对拍（`pop`/`hot` 也按同一口径重算）。

    这一步是"精简语料没被裁坏、索引没被写歪"的证据：任何一格对不上都会列出来并非零退出。

    ⚠ 关联方式是**行序**而不是 `id`：语料里 `id`（= `source`，如 `unknown`）**并不唯一**，
    按 id 建字典会把同名的那几首互相错配（第一次跑自检就是这么报出来的）——索引是按
    data.jsonl 顺序写的，两边都过同一道 `status + digits 非空` 的过滤，所以第 k 条对第 k 条。
    """
    t0 = time.perf_counter()
    idx_rows = [json.loads(ln) for ln in gzip.open(index_path, "rt", encoding="utf-8")
                if ln.strip()]
    pop: Counter = Counter()
    hot: Counter = Counter()
    titles: list[str] = []          # 第 k 条索引行的 title（按行序）
    names: list[list[str]] = []     # 第 k 条索引行的 artist+tag（`hot` 用）
    dup_ids: Counter = Counter()
    bad: list[str] = []
    i = 0
    with gzip.open(slim_path, "rt", encoding="utf-8") as f:
        for ln in f:
            if not ln.strip():
                continue
            r = json.loads(ln)
            d = ms.digits_of(r.get("score") or "")
            if not status_ok(r, ms.OK_STATUS) or not d:
                continue                                   # 与 build_corpus 的过滤同一口径
            ident = corpus_identity(r)
            rid = ident["id"]
            dup_ids[rid] += 1
            nm = list(ident["artist"]) + [t for t in ident["tag"]
                                          if not str(t).startswith("分类/")]
            titles.append(ident["title"])
            names.append(nm)
            for n in nm:
                hot[n] += 1
            pop[ident["title"]] += 1
            got = {
                **ident, "digits": d,
                "sec": ms.section_map(r, len(d)),
                "score": r.get("score") or "",
                "sections": sections_meta(r, ms.digits_of, ms.sec_label),
            }
            if i >= len(idx_rows):
                bad.append(f"索引行数不够：到第 {i + 1} 条就没有了（{rid}）")
                break
            want = idx_rows[i]
            for k, v in got.items():
                if want.get(k) != v:
                    bad.append(f"第 {i + 1} 行 {rid} 的 {k} 不一致："
                               f"精简语料 {str(v)[:60]!r} ≠ 索引 {str(want.get(k))[:60]!r}")
            i += 1
    compared = i
    if compared != len(idx_rows):
        bad.append(f"行数不一致：精简语料过滤后 {compared} 条，索引 {len(idx_rows)} 条")
    for k, row in enumerate(idx_rows[:compared]):
        if row.get("pop") != pop.get(titles[k], 0):
            bad.append(f"第 {k + 1} 行 {row.get('id')} 的 pop 不一致："
                       f"{row.get('pop')} ≠ {pop.get(titles[k], 0)}")
        want_hot = max([hot.get(n, 0) for n in names[k]] or [0])
        if row.get("hot") != want_hot:
            bad.append(f"第 {k + 1} 行 {row.get('id')} 的 hot 不一致：{row.get('hot')} ≠ {want_hot}")
    dupes = {k: v for k, v in dup_ids.items() if v > 1}
    return {"compared": compared, "index_rows": len(idx_rows), "mismatches": len(bad),
            "duplicate_ids": len(dupes),
            "duplicate_id_examples": sorted(dupes.items(), key=lambda kv: -kv[1])[:5],
            "examples": bad[:10], "seconds": round(time.perf_counter() - t0, 1)}


def main() -> int:
    ap = argparse.ArgumentParser(description="重建包内精简语料与运行索引")
    ap.add_argument("--data", default="", help="data.jsonl 路径（默认按候选位置找）")
    ap.add_argument("--out", default=os.path.join(HERE, "corpus"), help="产物目录（默认 <包>/corpus）")
    ap.add_argument("--no-cache", action="store_true", help="不借匹配器在 ~/.cache 里的数字索引")
    ap.add_argument("--verify", action="store_true", help="生成后再逐字段自检（慢，建议开）")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    data = find_data(a.data)
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    slim_p = os.path.join(out, "slim.jsonl.gz")
    index_p = os.path.join(out, "index.jsonl.gz")
    man_p = os.path.join(out, "manifest.json")
    st = os.stat(data)
    say = (lambda *x: None) if a.quiet else (lambda *x: print(*x, flush=True))

    say(f"语料：{data}（{st.st_size / 1048576:.1f} MB）")
    say("① 写精简语料 slim.jsonl.gz …")
    slim_info = write_slim(data, slim_p)
    say(f"   {slim_info['lines']} 行 · {os.path.getsize(slim_p) / 1048576:.2f} MB · "
        f"{slim_info['seconds']}s")

    say("② 建索引（调权威匹配器，算法一字未改）…")
    t0 = time.perf_counter()
    rows = ms.load_corpus(data, use_cache=not a.no_cache)
    build_s = round(time.perf_counter() - t0, 1)
    counts = {
        "songs": len(rows),
        "notes": sum(len(r["digits"]) for r in rows),
        "groups": len({r["title"] for r in rows}),
        "sources": dict(Counter((r.get("site") or "(无 source)") for r in rows).most_common()),
        "status": dict(Counter(str(r.get("status") or "(空)") for r in rows).most_common()),
        "status_ok_lines": slim_info["kept_status_ok"],
        "skipped_no_digits": max(0, slim_info["kept_status_ok"] - len(rows)),
    }
    say(f"   {counts['songs']} 首 · {counts['notes']} 个音 · 建索引 {build_s}s")
    say("③ 写运行索引 index.jsonl.gz …")
    idx_info = write_index(rows, slim_info["sections_meta"], index_p)
    say(f"   {idx_info['rows']} 行 · {os.path.getsize(index_p) / 1048576:.2f} MB · "
        f"{idx_info['seconds']}s")
    del rows, slim_info["sections_meta"]
    gc.collect()

    ver = None
    if a.verify:
        say("④ 自检（精简语料 vs 运行索引，逐字段）…")
        ver = verify(slim_p, index_p)
        say(f"   对拍 {ver['compared']} 首 · 不一致 {ver['mismatches']} 处 · {ver['seconds']}s")
        if ver["mismatches"]:
            for x in ver["examples"]:
                say("   ✗ " + x)
            say("✗ 自检没过 —— 不写 manifest.json（产物不可信）")
            return 2
        counts["duplicate_ids"] = ver["duplicate_ids"]      # 语料里 id 会重（如 source=unknown）

    manifest = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "generated_by": f"rebuild_index.py（jianpu-db-mcp {VERSION}）",
        "source": {"path": data, "size": st.st_size, "mtime": time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)), "mtime_ns": st.st_mtime_ns,
            "sha256": slim_info["source_sha256"], "lines": slim_info["lines"]},
        "slim": {"path": slim_p, "size": os.path.getsize(slim_p), "sha256": sha256_file(slim_p),
                 "lines": slim_info["lines"], "fields": list(SLIM_FIELDS)},
        "index": {"path": index_p, "size": os.path.getsize(index_p),
                  "sha256": sha256_file(index_p), "rows": idx_info["rows"],
                  "index_version": INDEX_VERSION, "fields": list(INDEX_FIELDS),
                  "build_seconds": build_s, "used_matcher_cache": not a.no_cache},
        "counts": counts,
        "matcher": {"melody_search": ms.__file__, "jptok": getattr(ms.jptok, "__file__", None),
                    "TOKVER": ms.TOKVER, "OK_STATUS": list(ms.OK_STATUS), "MAX_GAP": ms.MAX_GAP,
                    "ALIGN_MAX": ms.ALIGN_MAX, "OCC_CAP": ms.OCC_CAP,
                    "SECTION_WEIGHTS": ms.SEC_W},
        "verify": ver,
    }
    with open(man_p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    say(f"④ manifest.json：{os.path.getsize(man_p)} B"
        + ("（含自检结果）" if ver else "（未自检：加 --verify 可逐字段对拍）"))
    say(f"✓ 完成：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

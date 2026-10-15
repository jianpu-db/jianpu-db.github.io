# -*- coding: utf-8 -*-
"""**一致性对拍**：离线 MCP 的结果 vs 直接跑权威匹配器 `melody_search.py --json`。

为什么要求逐字段相等：这个包离线模式并不自己写算法，而是**调包内 vendored 副本**
（`jianpu_mcp/melody_search_vendored.py`，与 `jianpu2/tools/melody_search.py` 逐函数相同）。
"差不多"不算数 —— 所以这里把两边都真跑一遍，逐字段比：

    title / file / diff / bar_from / bar_to / source（用户点名的六项）+ sec_cn / n_notes / seg

离线这一侧走的是**真 MCP**（官方 SDK 的 stdio 客户端拉起 server.py，调 `search_melody`），
另一侧是 `py -3.13 D:\\Documents_D\\jianpu2\\tools\\melody_search.py <串> --json --fuzzy F --top L`
子进程的真实 stdout。对不上就非零退出。

    py -3.13 tests/parity_check.py                 # 跑内置的 10 条查询
    py -3.13 tests/parity_check.py --limit 3        # 每条只比前 3 名
    py -3.13 tests/parity_check.py --json out.json  # 把对拍明细写成 JSON
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
SERVER = os.path.join(PKG, "server.py")
JIANPU2 = os.environ.get("JIANPU2_ROOT") or r"D:\Documents_D\jianpu2"
CLI = os.path.join(JIANPU2, "tools", "melody_search.py")

# (查询串, fuzzy, 说明)
QUERIES = [
    ("33565653253", 0, "精确命中（长串，见匹配器文档）"),
    ("316316", 0, "精确命中（短串，多个候选）"),
    ("66561232123", 0, "精确命中（热度/段落权重参与排序）"),
    ("63731232", 0, "精确命中（机器人常用）"),
    ("316316", 1, "fuzzy=1"),
    ("33565653253", 2, "fuzzy=2"),
    ("123", 0, "宽查询（命中上千首，count 窗口）"),
    ("5 5 6 5 3 2 1", 0, "带空格/分隔符（与网页同口径）"),
    ("1 7 6 5", 0, "带空格的短句"),
    ("7", 0, "超宽查询（单个音）"),
]

FIELDS = ("title", "file", "diff", "bar_from", "bar_to", "source", "sec_cn", "n_notes", "seg")
STR_FIELDS = {"title", "file", "source", "sec_cn", "seg"}


def norm(k: str, v):
    """归一：JSON 里"键不在"与"空串"是同一个意思（两边都会省掉假值），比的时候当同一个值。"""
    if k in STR_FIELDS and v is None:
        return ""
    return v


def cli_search(digits: str, fuzzy: int, top: int, cli_python: list[str]) -> dict:
    """权威匹配器的真实 CLI 输出（子进程 + 真 stdout，不走包内代码）。"""
    out = subprocess.run([*cli_python, CLI, digits, "--json", "--fuzzy", str(fuzzy),
                          "--top", str(top)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", cwd=JIANPU2)
    if out.returncode != 0:
        raise SystemExit(f"✗ 匹配器 CLI 失败（{out.returncode}）：{out.stderr[:400]}")
    return json.loads(out.stdout)


def mcp_text(res) -> str:
    return "".join(c.text for c in res.content if getattr(c, "type", "") == "text")


def row_of(cli_hit: dict) -> dict:
    return {k: cli_hit.get(k) for k in FIELDS}


def row_of_mcp(song: dict) -> dict:
    m = song.get("match") or {}
    return {"title": song.get("title"), "file": song.get("file"), "diff": m.get("diff"),
            "bar_from": m.get("bar_from"), "bar_to": m.get("bar_to"),
            "source": song.get("source"), "sec_cn": m.get("sec_cn"),
            "n_notes": song.get("n_notes"), "seg": m.get("seg")}


def _first(v):
    """与匹配器 `build_corpus` 同一口径取"第一个值"：`v or ""`，是列表取首项，空列表当空串。"""
    v = v or ""
    return v[0] if isinstance(v, list) and v else (v if not isinstance(v, list) else "")


def raw_rows(want_source: set, want_stem: set, data: str) -> dict:
    """直接从 `data.jsonl` 读那两首的原始行（**不经包内任何代码**），用于单条实体的对拍。"""
    import gzip as _gz
    out = {}
    opener = _gz.open if data.endswith(".gz") else open
    with opener(data, "rt", encoding="utf-8") as f:
        for ln in f:
            if not ln.strip():
                continue
            r = json.loads(ln)
            src = _first(r.get("source"))
            f0 = (r.get("file") or [""])[0]
            stem = os.path.splitext(f0)[0]
            if src in want_source or stem in want_stem:
                if src:
                    out[src] = r                   # 两种写法都认（与 `/ws/2/song/<id>` 同口径）
                if stem:
                    out.setdefault(stem, r)
    return out


async def entity_phase(session, dump_rows: list) -> list:
    """单条实体对拍：MCP `get_song` vs `data.jsonl` 原始行（逐字段）。"""
    data = os.path.join(JIANPU2, os.pardir, "jianpu-db", "data.jsonl")
    if not os.path.isfile(data):
        data = r"D:\Documents_D\jianpu-db\data.jsonl"
    raws = raw_rows({"fysongs-47259"}, {"0932"}, data)
    print(f"\n──── 单条实体对拍（原始语料 {data}）")
    bad = []
    for key in ("fysongs-47259", "0932"):
        r = raws.get(key)
        if r is None:
            bad.append(f"{key}: 语料里没有这一行")
            continue
        res = await session.call_tool("get_song", {"id": key, "score_chars": 40})
        got = json.loads(mcp_text(res))
        src = _first(r.get("source"))
        f0 = (r.get("file") or [""])[0]
        st = r.get("status")
        st = st[0] if isinstance(st, list) else st
        want = {"title": r.get("title"), "file": f0, "source": src, "status": st,
                "n_notes": int(r.get("n_notes") or 0),
                "bars": [int(x) for x in (r.get("bars") or []) if isinstance(x, int)],
                "score": str(r.get("score") or "")[:40]}
        diff = [f"{k}: MCP {str(got.get(k))[:40]!r} ≠ 语料 {str(want[k])[:40]!r}"
                for k in want if got.get(k) != want[k]]
        bad += [f"{key} {d}" for d in diff]
        print(f"  {'✓' if not diff else '✗'} get_song({key})：id={got.get('id')} "
              f"title={got.get('title')!r} file={got.get('file')!r} "
              f"n_notes={got.get('n_notes')} bars={len(got.get('bars') or [])} 项 "
              f"score前40字符={str(got.get('score'))[:20]!r}…")
        for d in diff:
            print("      " + d)
        dump_rows.append({"id": key, "equal": not diff, "diff": diff, "want": want,
                          "got": {k: got.get(k) for k in want}})
    return bad


async def run(limit: int, dump: str, cli_python: list[str]) -> int:
    rows, bad = [], []
    print(f"权威匹配器：{' '.join(cli_python)} {CLI}")
    async with stdio_client(StdioServerParameters(
            command=sys.executable, args=[SERVER], cwd=PKG,
            env={k: v for k, v in os.environ.items() if not k.startswith("JIANPU_")})) as (r, w):
        async with ClientSession(r, w) as s:
            info = await s.initialize()
            print(f"✓ 离线 MCP 握手：{info.server_info.name} {info.server_info.version}"
                  f"（模式：默认离线，未设 JIANPU_*）")
            for digits, fuzzy, why in QUERIES:
                t0 = time.perf_counter()
                res = await s.call_tool("search_melody",
                                        {"digits": digits, "fuzzy": fuzzy, "limit": limit})
                t_mcp = time.perf_counter() - t0
                env = json.loads(mcp_text(res))
                t0 = time.perf_counter()
                want = cli_search(digits, fuzzy, limit, cli_python)
                t_cli = time.perf_counter() - t0
                got_rows = [row_of_mcp(x) for x in (env.get("songs") or [])]
                want_rows = [row_of(x) for x in want["hits"][:limit]]
                diff = []
                for i in range(max(len(got_rows), len(want_rows))):
                    g = got_rows[i] if i < len(got_rows) else {}
                    c = want_rows[i] if i < len(want_rows) else {}
                    for k in FIELDS:
                        if norm(k, g.get(k)) != norm(k, c.get(k)):
                            diff.append(f"#{i + 1}.{k}: 离线 {str(g.get(k))[:40]!r} ≠ 匹配器 {str(c.get(k))[:40]!r}")
                ok = not diff and len(got_rows) == len(want_rows)
                bad += [f"{digits}(fuzzy={fuzzy}) {d}" for d in diff]
                rows.append({"digits": digits, "fuzzy": fuzzy, "why": why, "equal": ok,
                             "n_offline": len(got_rows), "n_cli": len(want_rows),
                             "count": env.get("count"), "count_exact": env.get("count_exact"),
                             "cli_count": want.get("count"),
                             "mcp_s": round(t_mcp, 2), "cli_s": round(t_cli, 2),
                             "diff": diff,
                             "top1": (got_rows[0] if got_rows else None)})
                print(f"  {'✓' if ok else '✗'} {digits!r} fuzzy={fuzzy} 比 {len(got_rows)} 条"
                      f"（离线 {t_mcp:.2f}s / CLI {t_cli:.2f}s）"
                      + ("  第 1 名：" + str((got_rows[0] or {}).get("title")) if got_rows else ""))
                for d in diff[:6]:
                    print("      " + d)

            entity_rows: list = []
            bad += await entity_phase(s, entity_rows)

    print("\n──── 对拍表（逐字段：title/file/diff/bar_from/bar_to/source/sec_cn/n_notes/seg）")
    print(f"{'查询':>16} {'fz':>2}  {'条数':>4}  {'结果':>4}  说明")
    for x in rows:
        print(f"{x['digits']:>16} {x['fuzzy']:>2}  {x['n_offline']:>4}  "
              f"{'一致' if x['equal'] else '不一致':>4}  {x['why']}")
    if dump:
        os.makedirs(os.path.dirname(os.path.abspath(dump)), exist_ok=True)
        with open(dump, "w", encoding="utf-8") as f:
            json.dump({"rows": rows, "entities": entity_rows, "bad": bad}, f,
                      ensure_ascii=False, indent=2)
        print(f"（明细已写入 {dump}）")
    print(f"\n{'✓ 全部逐字段一致' if not bad else f'✗ 有 {len(bad)} 处不一致'}")
    return 0 if not bad else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--json", default="")
    ap.add_argument("--cli-python", default=os.environ.get("JIANPU_PY") or "py -3.13",
                    help="跑权威匹配器用的解释器（默认 py -3.13，空格分隔可带参数）")
    a = ap.parse_args()
    return asyncio.run(run(a.limit, a.json, a.cli_python.split()))


if __name__ == "__main__":
    sys.exit(main())

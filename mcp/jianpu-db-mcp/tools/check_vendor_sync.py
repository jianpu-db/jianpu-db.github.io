# -*- coding: utf-8 -*-
"""断言"vendored 副本的**算法一字未改**" —— 逐函数 AST 比对包内副本与上游实现。

为什么要有它：包内 `jianpu_mcp/*_vendored.py` 是从 jianpu2 仓库抄来的副本（离线可用靠它），
抄的时候**只允许**改"接线"（文件头注释、路径默认值、jptok 的 import 方式）。
这份脚本把两份文件的**每个函数/类按源码片段**切出来逐字节比对，再把模块级常量的**值**比一遍
（用 `ast.literal_eval`，所以注释/空格不影响），因此：

  * 函数体改了**一个字** -> 报错；
  * 常量（`TOKVER`/`OK_STATUS`/`MAX_GAP`/`SEC_W`/`ALIGN_MAX`/`OCC_CAP`…）改了值 -> 报错；
  * 只有"接线"那几行不同 -> 通过（那几行不在任何被比对的函数/常量里）。

    py -3.13 tools/check_vendor_sync.py            # 默认比上游绝对路径
    JIANPU2_ROOT=D:\\...\\jianpu2 py -3.13 tools/check_vendor_sync.py
"""
from __future__ import annotations

import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
JIANPU2 = os.environ.get("JIANPU2_ROOT") or r"D:\Documents_D\jianpu2"

PAIRS = [
    (os.path.join(JIANPU2, "tools", "melody_search.py"),
     os.path.join(PKG, "jianpu_mcp", "melody_search_vendored.py")),
    (os.path.join(JIANPU2, "skills", "jianpu-melody-lookup", "jptok.py"),
     os.path.join(PKG, "jianpu_mcp", "jptok_vendored.py")),
]

# 常量里允许"值本来就一样、但写法不同"的项也照样比 —— 只要值相等就通过。
CONST_ALLOW = {"__doc__"}

# **接线**（不是算法）：路径默认值那几格允许不同/允许少 —— 这是 `sync_vendor.py` 明说改掉的三处之一。
PLUMBING = {"HERE", "PKG", "ROOT", "WS", "DB", "DATA", "CACHE"}


def parts(path: str):
    """-> ({名字: 源码片段}, {常量名: 值})；函数用 ast.get_source_segment 取原文。"""
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    funcs, consts = {}, {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            funcs[node.name] = ast.get_source_segment(src, node)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id not in CONST_ALLOW:
                    try:
                        consts[t.id] = ast.literal_eval(node.value)
                    except ValueError:
                        consts[t.id] = ast.dump(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is not None and node.target.id not in CONST_ALLOW:
                try:
                    consts[node.target.id] = ast.literal_eval(node.value)
                except ValueError:
                    consts[node.target.id] = ast.dump(node.value)
    return funcs, consts


def main() -> int:
    bad = 0
    for up, ven in PAIRS:
        if not os.path.isfile(up):
            print(f"✗ 找不到上游：{up}（用 JIANPU2_ROOT 指一下）")
            return 3
        fu, cu = parts(up)
        fv, cv = parts(ven)
        name = os.path.basename(ven)
        miss = sorted(set(fu) - set(fv))
        extra = sorted(set(fv) - set(fu))
        diff = sorted(k for k in set(fu) & set(fv) if fu[k] != fv[k])
        # 常量：把"接线"那几格（路径默认值）排除在外，其余一格都不许差
        cdiff = sorted(k for k in set(cu) & set(cv) if cu[k] != cv[k] and k not in PLUMBING)
        cmiss = sorted(k for k in set(cu) - set(cv) if k not in PLUMBING)
        plumb = sorted((set(cu) | set(cv)) & PLUMBING)
        print(f"{name}：上游 {len(fu)} 个函数 / {len(cu)} 个常量 -> 副本 {len(fv)} / {len(cv)}"
              f"（接线常量不参与比对：{'、'.join(plumb) or '无'}）")
        for label, items in (("副本里少了函数", miss), ("副本里多了函数", extra),
                             ("函数体不一致", diff), ("常量值不一致", cdiff),
                             ("副本里少了常量", cmiss)):
            if items:
                bad += len(items)
                print(f"  ✗ {label}：{'、'.join(items)}")
        if not (miss or extra or diff or cdiff or cmiss):
            print(f"  ✓ 算法一字未改（{len(fu)} 个函数逐字节相同，"
                  f"{len(cu) - len(plumb)} 个非接线常量取值相同）")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

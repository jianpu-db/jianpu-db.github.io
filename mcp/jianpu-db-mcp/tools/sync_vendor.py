# -*- coding: utf-8 -*-
"""把仓库外的**权威实现**抄成包内 vendored 副本 —— 一条命令，可重跑。

为什么要 vendored：这个包要能**完全离线**跑（线上那台机器已经坏了），运行时**不许**
import 包外的文件。所以把权威匹配器与 token 实现各抄一份进 `jianpu_mcp/`。

来源（两份都在 jianpu2 仓库里，改完上游就重跑本脚本）：

    D:\\Documents_D\\jianpu2\\tools\\melody_search.py                  -> jianpu_mcp/melody_search_vendored.py
    D:\\Documents_D\\jianpu2\\skills\\jianpu-melody-lookup\\jptok.py   -> jianpu_mcp/jptok_vendored.py

**算法一行都不改** —— 唯一的改动是下面 `_TRANSFORMS` 里那三处"接线"（路径默认值、jptok 的
import 方式、文件头说明），改完由 `tools/check_vendor_sync.py` 逐函数 AST 比对来断言。
路径可用 `JIANPU2_ROOT` 环境变量覆盖（默认就是上面那台机器上的绝对路径）。

用法：

    py -3.13 tools/sync_vendor.py            # 重抄两份（上游 mtime 变了就会写）
    py -3.13 tools/sync_vendor.py --check    # 不写，只报会不会变
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))          # <pkg>/tools
PKG = os.path.dirname(HERE)                                 # <pkg>
OUT = os.path.join(PKG, "jianpu_mcp")

JIANPU2 = os.environ.get("JIANPU2_ROOT") or r"D:\Documents_D\jianpu2"
SRC_MELODY = os.path.join(JIANPU2, "tools", "melody_search.py")
SRC_JPTOK = os.path.join(JIANPU2, "skills", "jianpu-melody-lookup", "jptok.py")

_BAR = "# " + "═" * 74

# ── ① jptok：只加一段文件头说明（代码部分由 old/new 里剩下的那份保证一字未动）──────
JPTOK_OLD = '"""\nimport re\n'
JPTOK_HEAD = """\"\"\"

{bar}
# **vendored 副本**（包内拷贝）—— 简谱 token 的**唯一**实现，运行时**不 import 包外任何文件**。
#
#   来源：{src}
#   同步：py -3.13 tools/sync_vendor.py        ← 上游一改就重跑这一条
#   校验：py -3.13 tools/check_vendor_sync.py  ← 逐函数 AST 比对，断言"算法一字未改"
#   本副本与上游的差异：**只有本段注释**（代码逐字节相同）。
#   上游 sha256：{sha}      上游 mtime：{mtime}
{bar}

""".format(bar=_BAR, src=SRC_JPTOK, sha="{sha}", mtime="{mtime}")
JPTOK_NEW = JPTOK_HEAD + "import re\n"

# ── ② melody_search：文件头 + 路径默认值 + jptok 的 import 方式（算法部分不动）─────
MELODY_OLD_HEAD = '"""\nimport argparse\n'
MELODY_HEAD = """\"\"\"

{bar}
# **vendored 副本** —— 本文件是权威匹配器（jianpu2/tools/melody_search.py）的**包内拷贝**，
# 运行时**不 import 包外任何文件**；算法一字未改，改动**只有下面这三处**：
#
#   ① 本段说明（纯注释，不含代码）；
#   ② `HERE/PKG/DB/DATA` 的默认路径：上游是"三个仓库的上一层"，这里是"包内 corpus 优先"；
#   ③ `sys.path.insert(...) + import jptok` -> 包内 `jptok_vendored`（仍绑定到同名变量 `jptok`，
#      所以下文所有用到 `jptok` 的地方**一个字都没动**）。
#
#   来源：{src}
#   同步：py -3.13 tools/sync_vendor.py        ← 上游一改就重跑这一条
#   校验：py -3.13 tools/check_vendor_sync.py  ← 逐函数 AST 比对，断言"算法一字未改"
#   上游 sha256：{sha}      上游 mtime：{mtime}
{bar}

""".format(bar=_BAR, src=SRC_MELODY, sha="{sha}", mtime="{mtime}")
MELODY_NEW_HEAD = MELODY_HEAD + "import argparse\n"

MELODY_OLD_PATHS = '''HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                      # jianpu2
WS = os.path.dirname(ROOT)                        # 工作区(三个仓库的上一层)
DB = os.environ.get("JIANPU_DB") or os.path.join(WS, "jianpu-db")
DATA = os.path.join(DB, "data.jsonl")'''
MELODY_NEW_PATHS = '''HERE = os.path.dirname(os.path.abspath(__file__))   # jianpu_mcp/（包内）
PKG = os.path.dirname(HERE)                        # 包根
# 默认语料：`JIANPU_DB` 环境变量 > 包内 `corpus/`（离线模式用的就是包内那份）
DB = os.environ.get("JIANPU_DB") or os.path.join(PKG, "corpus")
DATA = os.path.join(DB, "data.jsonl")'''

MELODY_OLD_IMPORT = '''sys.path.insert(0, os.path.join(ROOT, "skills", "jianpu-melody-lookup"))
try:
    import jptok                                   # 唯一 token 实现
except Exception:                                  # 独立部署时可能没有 -> 兜底正则(同口径)
    jptok = None'''
MELODY_NEW_IMPORT = '''try:
    from . import jptok_vendored as jptok           # 包内 vendored 副本（唯一 token 实现）
except ImportError:                                # 被当脚本直接跑：sys.path[0] 就是本目录
    try:
        import jptok_vendored as jptok
    except Exception:                              # 独立部署时可能没有 -> 兜底正则(同口径)
        jptok = None
except Exception:
    jptok = None'''

# jptok 的 import 那一段在上游里还带一句行内注释，这里按"整块替换"处理：
TRANSFORMS = {
    "jptok_vendored.py": [(JPTOK_OLD, JPTOK_NEW)],
    "melody_search_vendored.py": [
        (MELODY_OLD_HEAD, MELODY_NEW_HEAD),
        (MELODY_OLD_PATHS, MELODY_NEW_PATHS),
        (MELODY_OLD_IMPORT, MELODY_NEW_IMPORT),
    ],
}
SOURCES = {"jptok_vendored.py": SRC_JPTOK, "melody_search_vendored.py": SRC_MELODY}


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def mtime_text(path: str) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(path)))


def make(name: str) -> str:
    src = SOURCES[name]
    text = open(src, encoding="utf-8").read()
    meta = {"sha": sha256_of(src), "mtime": mtime_text(src)}
    for old, new in TRANSFORMS[name]:
        if text.count(old) != 1:
            raise SystemExit(f"✗ {os.path.basename(src)}：锚点出现 {text.count(old)} 次（应为 1），"
                             f"上游结构变了 —— 先看 diff 再改 sync_vendor.py")
        text = text.replace(old, new.format(**meta) if "{" in new else new)
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只比对，不写文件")
    a = ap.parse_args()
    bad = 0
    for name in TRANSFORMS:
        want = make(name)
        dst = os.path.join(OUT, name)
        cur = open(dst, encoding="utf-8").read() if os.path.isfile(dst) else None
        state = "一致" if cur == want else ("已写入" if not a.check else "会变（未写）")
        if cur != want and not a.check:
            with open(dst, "w", encoding="utf-8", newline="\n") as f:
                f.write(want)
        bad += 0 if cur == want else 1
        print(f"{'✓' if cur == want else ('·' if a.check else '✎')} {name}：{state}"
              f"（上游 {sha256_of(SOURCES[name])[:12]}）")
    return 0 if (bad == 0 or not a.check) else 1


if __name__ == "__main__":
    sys.exit(main())

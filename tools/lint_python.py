"""新代码的 Python 风格/类型门槛（D 阶段）—— ruff + mypy，**只覆盖本次现代化产出的文件**。

## 为什么不全仓库一把梭

这个仓库里有 60+ 个历史脚本（爬虫、转写收尾、QA 对账…）和一份标准库版写后端 `app/server.py`。
它们能跑、有实测、也有大量"为什么"的注释，但风格上是几周里陆续写的，直接全量开 ruff/mypy：
  * 会产生几百条纯风格告警，**淹没真问题**；
  * 为了消告警去动 `app/server.py`（那条"随时可退回"的备份路）有行为风险，收益为零。

所以走**渐进门槛**（strangler）: 新写的/本次迁移的文件必须过；历史文件登记在案、逐步纳入。
`app/server.py` 在 pyproject 里另有 per-file-ignores 说明，免得日后有人以为"忘了开"。

用法:
    py -3.13 tools/lint_python.py            # 检查
    py -3.13 tools/lint_python.py --fix      # 先自动修（ruff --fix）再检查
    py -3.13 tools/lint_python.py --list     # 只打印当前门槛覆盖哪些文件
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# **门槛名单**（唯一真源：pre-commit 与 CI 都调这个脚本，别在两处各写一遍）
MODERN = [
    "app/api.py",                                  # C 阶段: FastAPI 写后端
    "app/search_api.py",                           # 只读检索口径(两份后端共用)
    "tools/check_parity_legacy_vs_fastapi.py",     # C 阶段: 灰度对拍矩阵
    "tools/check_docs_numbers.py",                 # A 阶段: 文档数字对账
    "tools/lint_python.py",                        # 本文件
    "tools/make_isolated_db.py",                   # D 阶段: 隔离语料库生成器(对拍/CI 用)
]
MYPY_SCOPE = ["app/api.py", "app/search_api.py"]   # 类型检查只做新写后端（配置在 pyproject 的 [tool.mypy]）

# 历史文件（登记在案，逐步纳入；不是为了"眼不见为净"）：
LEGACY_NOTE = (
    "历史脚本与标准库版写后端未进门槛：它们能跑、有实测注释，全量开告警会淹没真问题，"
    "而改动那条「可退回」的备份实现有行为风险。见 pyproject 的 [tool.ruff.lint.per-file-ignores]。"
)


def find_exe(name: str) -> str:
    """优先用项目 venv 里的可执行文件（uv sync 装的），否则退回 PATH / 模块方式。"""
    cand = os.path.join(ROOT, ".venv", "Scripts" if os.name == "nt" else "bin",
                        name + (".exe" if os.name == "nt" else ""))
    if os.path.exists(cand):
        return cand
    onpath = shutil.which(name)
    return onpath or ""


def run(cmd: list[str]) -> int:
    print("  $ " + " ".join(cmd))
    p = subprocess.run(cmd, cwd=ROOT)
    return p.returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="先跑 ruff --fix")
    ap.add_argument("--list", action="store_true", help="只列出门槛覆盖的文件")
    a = ap.parse_args()

    if a.list:
        print("门槛覆盖:", ", ".join(MODERN))
        print(MYPY_SCOPE and ("mypy: " + ", ".join(MYPY_SCOPE)))
        print(LEGACY_NOTE)
        return 0

    missing = [f for f in MODERN if not os.path.exists(os.path.join(ROOT, f))]
    if missing:
        print("  ! 门槛名单里的文件不存在: " + ", ".join(missing))
        return 1

    ruff, mypy = find_exe("ruff"), find_exe("mypy")
    if not ruff or not mypy:
        print("  ! 找不到 ruff/mypy —— 先 `py -3.13 -m uv sync --extra dev`")
        return 1

    fails = 0
    if a.fix:
        print("① ruff --fix")
        run([ruff, "check", "--fix", *MODERN])
    print("① ruff check")
    fails += run([ruff, "check", *MODERN]) != 0
    print("② mypy")
    fails += run([mypy, *MYPY_SCOPE]) != 0

    print()
    if fails:
        print(f"门槛未过（{fails} 步失败）")
        return 1
    print(f"门槛通过：{len(MODERN)} 个文件过 ruff · {len(MYPY_SCOPE)} 个文件过 mypy")
    print(LEGACY_NOTE)
    return 0


if __name__ == "__main__":
    sys.exit(main())

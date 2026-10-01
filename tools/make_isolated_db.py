"""造一个**隔离语料库**（临时目录，够跑写后端的所有路径）—— 给对拍/自检/CI 用。

## 为什么要它

写后端的自检会**真写盘 + git commit**（补链接、补标签、投稿生成曲谱）。拿真语料跑自检 =
在自己家地上做实验。所以规矩是: 只打隔离实例。这个脚本把"造一个隔离库"固化成一步：

  * `scores/` 里放几份**真**曲谱（从真语料里挑小的拷进来，保留真实文件名/字段）；
  * `linkurl.py` 等口径模块照拷（写路径要用它的"搜索页一律拒收"）；
  * 建一个 git 仓库并做一次基线提交（否则 `git commit` 那一步没法验证）；
  * 默认落在系统临时目录下，`--out` 可指定。

用法:
    py -3.13 tools/make_isolated_db.py --out /tmp/jp-iso
    py -3.13 tools/make_isolated_db.py --db /path/to/jianpu-db --copies 3 --force
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

# 口径模块: 写路径要用（`server.py` 会 `sys.path.insert(0, DB)` 再 `import linkurl`）
MODULES = ("linkurl.py", "schema.py")


def git(args: list[str], cwd: str) -> int:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True).returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "jianpu-db"),
        help="真语料库（只读，用来挑几份真曲谱）")
    ap.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "jianpu-iso-db"),
        help="隔离库落地目录")
    ap.add_argument("--copies", type=int, default=3, help="拷几份真曲谱（越小越快）")
    ap.add_argument("--max-bytes", type=int, default=3000, help="只挑小于这个字节数的曲谱（自检不关心曲子多大）")
    ap.add_argument("--force", action="store_true", help="已存在就删掉重建")
    a = ap.parse_args()

    if os.path.exists(a.out):
        if not a.force:
            print(f"  ! {a.out} 已存在（加 --force 重建）")
            return 1
        shutil.rmtree(a.out)
    os.makedirs(os.path.join(a.out, "scores"), exist_ok=True)
    os.makedirs(os.path.join(a.out, "feedback"), exist_ok=True)

    picked = []
    src_scores = os.path.join(a.db, "scores")
    if os.path.isdir(src_scores):
        for name in sorted(os.listdir(src_scores)):
            if len(picked) >= a.copies:
                break
            p = os.path.join(src_scores, name)
            if name.endswith(".txt") and os.path.getsize(p) <= a.max_bytes:
                shutil.copy2(p, os.path.join(a.out, "scores", name))
                picked.append(name)
    for m in MODULES:
        src = os.path.join(a.db, m)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(a.out, m))

    # ⚠ 一定要让仓库**有东西可提交**：git 不跟踪空目录，如果既没挑到曲谱也没拷到模块，
    #   基线 `git commit` 会以 "nothing to commit" 失败 -> 写后端里"提交那一步"就永远验证不到
    #   （CI 上真发生了：隔离库连基线都没有，两边都提交不了，等于少测了一条路径）。
    iso_readme = os.path.join(a.out, "README.iso.md")
    with open(iso_readme, "w", encoding="utf-8", newline="\n") as f:
        f.write("# 隔离语料库（对拍/自检用）\n\n"
                "这个仓库由 `tools/make_isolated_db.py` 生成，**不是**真语料。\n"
                "写后端的自检会真写盘 + `git commit`，所以必须在这样的一次性仓库里跑。\n")

    git(["init", "-q", "."], a.out)
    git(["-c", "user.email=iso@local", "-c", "user.name=iso", "add", "-A"], a.out)
    git(["-c", "user.email=iso@local", "-c", "user.name=iso", "commit", "-qm", "隔离实例基线"], a.out)

    has_commit = git(["rev-list", "--count", "HEAD"], a.out) == 0
    print(f"  隔离库: {a.out}")
    print(f"  scores/ {len(picked)} 份: {', '.join(picked) if picked else '(没挑到真曲谱 —— CI 里正常)'}")
    print(f"  口径模块: {', '.join(m for m in MODULES if os.path.exists(os.path.join(a.out, m))) or '(无 —— CI 里正常)'}")
    print(f"  git 基线: {'已提交' if has_commit else '**没有提交（写回那步会失败）**'}")
    print(f"  用法: JIANPU_DB={a.out} py -3.13 app/api.py 8790")
    return 0


if __name__ == "__main__":
    sys.exit(main())

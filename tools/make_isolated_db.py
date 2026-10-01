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

    git(["init", "-q", "."], a.out)
    git(["-c", "user.email=iso@local", "-c", "user.name=iso", "add", "-A"], a.out)
    git(["-c", "user.email=iso@local", "-c", "user.name=iso", "commit", "-qm", "隔离实例基线"], a.out)

    print(f"  隔离库: {a.out}")
    print(f"  scores/ {len(picked)} 份: {', '.join(picked) if picked else '(真语料里没挑到，检查 --db)'}")
    print(f"  口径模块: {', '.join(m for m in MODULES if os.path.exists(os.path.join(a.out, m)))}")
    print(f"  git: {'基线已提交' if git(['rev-list', '--count', 'HEAD'], a.out) == 0 else '还没有提交'}")
    print(f"  用法: JIANPU_DB={a.out} py -3.13 app/api.py 8790")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""重建整条索引链 —— `tools/refresh.sh` 的 **Python 版**（这台机器上没有 bash）。

为什么要这份: 网页上「＋ 补收录页 / ＋ 补标签 / ＋ 补 <属性>」保存后, 服务端要重建索引
(`scores/*.txt --parse_scores--> data.jsonl --build_web_data--> 前端索引`)。这件事原来只有
`refresh.sh` 一份 bash 实现, 而 Windows 开发机上没有 bash —— 实测保存成功但服务端返回
`refresh: false, FileNotFoundError: [WinError 2]`, 卡片要等下一次流水线才会变。
**步骤与 refresh.sh 完全一致**(同一把锁、同一个 pending 标记、同样最多三轮),
只是用 sys.executable 跑 python, 不依赖 bash。

用法(由 app/server.py 在后台调, 也可手动跑): py -3.13 tools/refresh.py
环境变量: JIANPU_DB 指定语料仓库(默认 ../../jianpu-db, 与 refresh.sh 同口径)。
"""
import io
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))          # <web>/tools
WEB = os.path.dirname(HERE)                                # <web>
ROOT = os.path.dirname(WEB)                                # 语料根(旁边就是 jianpu-db / jianpu2)
DB = os.environ.get("JIANPU_DB") or os.path.join(ROOT, "jianpu-db")
SKILL = os.path.join(ROOT, "jianpu2", "skills", "jianpu-melody-lookup")
LOCK = os.path.join(WEB, "data", ".refresh.lock")
PENDING = os.path.join(WEB, "data", ".refresh.pending")


def say(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def main():
    py = sys.executable
    for rnd in (1, 2, 3):
        say(f"=== refresh 第 {rnd} 轮开始 ===")
        say(f"DB={DB}")
        rc = subprocess.run([py, "-u", "parse_scores.py"], cwd=DB).returncode
        if rc != 0:
            say(f"parse_scores 失败(exit {rc}) -> 收工")
            try:
                os.remove(LOCK)
            except OSError:
                pass
            return 1
        try:
            shutil.copyfile(os.path.join(DB, "data.jsonl"), os.path.join(SKILL, "data.jsonl"))
        except Exception as e:
            say(f"! 同步 skill 数据失败: {type(e).__name__}: {e}")
        rc = subprocess.run([py, "-u", os.path.join("tools", "build_web_data.py"),
                             "--data", os.path.join(DB, "data.jsonl"),
                             "--out", os.path.join(WEB, "data")], cwd=WEB).returncode
        say(f"=== refresh 第 {rnd} 轮结束(exit {rc}) ===")
        if os.path.exists(PENDING):
            try:
                os.remove(PENDING)
            except OSError:
                pass
            say("检测到新的改动 -> 再来一轮")
            continue
        break
    try:
        os.remove(LOCK)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

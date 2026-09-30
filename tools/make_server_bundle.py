# -*- coding: utf-8 -*-
"""打一个**开箱即用**的"本机服务"包（`jianpu-server/`，可选 zip）—— 换台电脑照样跑。

## 这个包是什么

站点有两半: **读**（检索/卡片/谱页）在 Cloudflare 边缘，**写**（投稿 / 补属性 / 补标签 / 补收录页）
在作者本机的 `app/server.py`。这个包就是把"写的那一半"连同它要的一切**打成一份可搬走的东西**：

```
jianpu-server/
  app/server.py          服务本体（**只用 Python 标准库**，不需要 pip install 任何东西）
  app/jptok.py           简谱 token 口径的唯一真源（从 jianpu2 的 skill 目录拷来）
  static/…               前端产物（index.html + 带内容哈希的 js/css + 分享卡 og.png）
  data/…                 检索索引（songs.jsonl.gz / stats.json / og.json）
  corpus/                语料仓库落在哪儿（首次运行 setup 往里 clone，见下）
  setup.cmd / setup.sh   首次运行: 稀疏 clone 语料（~100MB，跳过 by_* 那几万个符号链接）
  run.cmd   / run.sh     起服务（自带 JIANPU_DB/JIANPU_PORT）
  README.md              给人看的说明（怎么用、怎么加 token、怎么暴露到公网）
  VERSION                从哪两个 commit 打的（便于追溯"这包是哪天的"）
```

## 为什么语料不入包

`jianpu-db` 仓库 176MB（其中 `.git` 75MB），而且里面 `by_*` 那几棵链接树对服务毫无用处。
所以默认让**首次运行的 setup 脚本**做一次**稀疏 clone**（只要根目录那几个 py/json + `scores/`），
既小又快，还能顺便 `git pull` 更新语料。要完全离线就 `--corpus copy`（把语料一起拷进包）。

用法:
  py -3.13 tools/make_server_bundle.py                          # 打到 ../jianpu-server/
  py -3.13 tools/make_server_bundle.py --out D:\\tmp\\srv --zip   # 指定目录并压缩
  py -3.13 tools/make_server_bundle.py --corpus copy            # 把语料也拷进去（包会大 ~180MB）
"""
import argparse
import io
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))       # 站点仓库
DB = os.environ.get("JIANPU_DB") or os.path.join(os.path.dirname(ROOT), "jianpu-db")
SKILL = os.path.join(os.path.dirname(ROOT), "jianpu2", "skills", "jianpu-melody-lookup")
DB_URL = "https://github.com/Francium-223/jianpu-db.git"


def sh(cmd, cwd=None):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        return (r.stdout or "").strip()
    except Exception:
        return ""


def version_line():
    web = sh(["git", "rev-parse", "--short", "HEAD"], ROOT) or "?"
    db = sh(["git", "rev-parse", "--short", "HEAD"], DB) or "?"
    return f"web={web} db={db} built={sh(['git','log','-1','--format=%cs'], ROOT) or '?'}"


def static_files():
    """前端产物: `index.html` + 带内容哈希的那几个 + 未哈希的源文件。

    ⚠ 第一版漏了 `index.html`（只按 `.js/.css/.png` 过滤）—— 实测整包起起来首页 404，
    因为 `app/server.py` 的 `resolve("/")` 指的正是 `static/index.html`。别再用扩展名白名单筛入口。
    """
    names = ["index.html"]
    for f in sorted(os.listdir(os.path.join(ROOT, "static"))):
        if f.endswith((".js", ".css", ".png")) and not f.startswith("_"):
            names.append(f)
    return names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.dirname(ROOT), "jianpu-server"))
    ap.add_argument("--zip", action="store_true", help="打完再压成 <out>.zip")
    ap.add_argument("--corpus", choices=("clone", "copy", "none"), default="clone",
                    help="clone=包里放首次运行脚本去拉(默认, 小); copy=把语料拷进包(离线可用); none=不要语料")
    ap.add_argument("--db-url", default=DB_URL)
    a = ap.parse_args()

    out = os.path.abspath(a.out)
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(os.path.join(out, "app"), exist_ok=True)
    os.makedirs(os.path.join(out, "static"), exist_ok=True)
    os.makedirs(os.path.join(out, "data"), exist_ok=True)
    os.makedirs(os.path.join(out, "corpus"), exist_ok=True)

    n = 0
    # 1) 服务本体 + token 口径
    for src, dst in ((os.path.join(ROOT, "app", "server.py"), "app/server.py"),
                     (os.path.join(SKILL, "jptok.py"), "app/jptok.py")):
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(out, dst))
            n += 1
        else:
            print(f"  ! 缺 {src}（jptok.py 找不到时服务会用内置兜底正则，口径可能略有出入）")
    # 2) 前端产物 + 索引 + 根上的爬虫文件（本机镜像也要完整: robots/sitemap 取不到会显得像坏了）
    for f in static_files():
        shutil.copy2(os.path.join(ROOT, "static", f), os.path.join(out, "static", f))
        n += 1
    for f in ("robots.txt", "sitemap.xml"):
        p = os.path.join(ROOT, f)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(out, f))
            n += 1
    for f in ("songs.jsonl.gz", "stats.json", "og.json"):
        p = os.path.join(ROOT, "data", f)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(out, "data", f))
            n += 1
    # 3) 语料: 拷进来 or 留空让首次运行去拉
    if a.corpus == "copy":
        dst = os.path.join(out, "corpus", "jianpu-db")
        print(f"  拷贝语料 {DB} -> corpus/jianpu-db （约 180MB，慢一点）")
        shutil.copytree(DB, dst, ignore=shutil.ignore_patterns(".git", "by_*", "scores-parked*"),
                        symlinks=False)
    elif a.corpus == "none":
        pass

    ver = version_line()
    io.open(os.path.join(out, "VERSION"), "w", encoding="utf-8", newline="\n").write(ver + "\n")

    # 4) 首次运行脚本（稀疏 clone: 只要根目录那几个文件 + scores/）
    setup_cmd = f"""@echo off
rem 首次运行: 把语料仓库稀疏 clone 到 corpus\\jianpu-db（只要根目录文件 + scores/）
setlocal
cd /d "%~dp0"
if exist "corpus\\jianpu-db\\.git" (
  echo 语料已存在, 只做 git pull 更新...
  git -C corpus\\jianpu-db pull --ff-only
  goto :done
)
echo 克隆语料（约 50~100MB，第一次会慢一点）...
git clone --depth 1 --filter=blob:none --sparse {a.db_url} corpus\\jianpu-db || goto :err
git -C corpus\\jianpu-db sparse-checkout set scores
:done
echo.
echo 好了。接着跑 run.cmd 起服务。
goto :eof
:err
echo 克隆失败: 检查网络/有没有装 git。也可以手动把语料仓库放到 corpus\\jianpu-db。
exit /b 1
"""
    io.open(os.path.join(out, "setup.cmd"), "w", encoding="utf-8", newline="\r\n").write(setup_cmd)
    setup_sh = f"""#!/bin/sh
# 首次运行: 稀疏 clone 语料（只要能跑服务的那部分）
set -e
cd "$(dirname "$0")"
if [ -d corpus/jianpu-db/.git ]; then
  git -C corpus/jianpu-db pull --ff-only || true
else
  git clone --depth 1 --filter=blob:none --sparse {a.db_url} corpus/jianpu-db
  git -C corpus/jianpu-db sparse-checkout set scores
fi
echo "语料就绪: corpus/jianpu-db"
"""
    io.open(os.path.join(out, "setup.sh"), "w", encoding="utf-8", newline="\n").write(setup_sh)

    run_cmd = """@echo off
rem 起服务（默认 http://127.0.0.1:8770/）
setlocal
cd /d "%~dp0"
set JIANPU_DB=%~dp0corpus\\jianpu-db
if not defined JIANPU_PORT set JIANPU_PORT=8770
if not defined JIANPU_HOST set JIANPU_HOST=127.0.0.1
rem 想给公网/隧道用: 先 set JPSUBMIT_TOKEN=一串你自己定的口令（服务就要求 X-Token 了）
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% app\\server.py
"""
    io.open(os.path.join(out, "run.cmd"), "w", encoding="utf-8", newline="\r\n").write(run_cmd)
    run_sh = """#!/bin/sh
set -e
cd "$(dirname "$0")"
export JIANPU_DB="$PWD/corpus/jianpu-db"
: "${JIANPU_PORT:=8770}"
: "${JIANPU_HOST:=127.0.0.1}"
export JIANPU_PORT JIANPU_HOST
exec python3 app/server.py
"""
    io.open(os.path.join(out, "run.sh"), "w", encoding="utf-8", newline="\n").write(run_sh)

    io.open(os.path.join(out, "README.md"), "w", encoding="utf-8", newline="\n").write(f"""# jianpu-server —— 本机"写"服务（开箱即用包）

{ver}

站点分两半：**读**（检索/卡片/谱页）在 Cloudflare 边缘（https://jianpu-db.org/）；
**写**（投稿 / 补属性 / 补标签 / 补收录页 / 原图）在**这台机器**上跑 —— 这个包就是那一半。

## 三步跑起来

1. 装 **Python 3.10+**（3.13 最好）。**不需要 pip 装任何东西**：服务只用标准库。
2. 装 **git**，然后跑一次 `setup.cmd`（Windows）/ `./setup.sh`（macOS/Linux）——
   它会把语料稀疏 clone 到 `corpus/jianpu-db`（只要根目录那几个文件 + `scores/`，约 50~100MB）。
3. 跑 `run.cmd` / `./run.sh`，浏览器打开 **http://127.0.0.1:8770/**。

## 想让它给公网用（域名上的 ＋/投稿 要写进库，就得走这里）

本机服务**故意只监听 127.0.0.1**（它能写盘 + git commit，不能裸奔）。给公网的标准做法是打隧道：

```bash
cloudflared tunnel --url http://127.0.0.1:8770      # 拿一个 https://xxx.trycloudflare.com
```

然后把地址告诉边缘 Worker（在站点仓库里执行）：

```bash
npx wrangler secret put API_UPSTREAM     # 填上面那个 https 地址
npx wrangler secret put API_TOKEN        # 与本机环境变量 JPSUBMIT_TOKEN **同一个值**
```

之后 `curl -s https://jianpu-db.org/api/health` 应显示 `"api":true`。
**务必**设 `JPSUBMIT_TOKEN`（不设就是谁都能往你机器上写）：`run.cmd` 里那行注释有说明。

## 常用环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `JIANPU_DB` | `corpus/jianpu-db` | 语料仓库在哪 |
| `JIANPU_PORT` | `8770` | 端口 |
| `JIANPU_HOST` | `127.0.0.1` | 监听地址（给局域网用才改 `0.0.0.0`） |
| `JPSUBMIT_TOKEN` | 空 | 设了就要求 `X-Token`（隧道/公网必须设） |
| `JIANPU_IMAGES` | 自动 | 原图目录（冒号分隔），没有就 404，不影响检索 |

## 更新语料

`git -C corpus/jianpu-db pull --ff-only`（稀疏 clone 只更新那几样，很快）。

## 包里有什么 / 没有什么

* 有：服务本体、token 口径 `jptok.py`、前端产物、检索索引（`songs.jsonl.gz` / `stats.json` / `og.json`）。
* 没有：原图（十几 GB，需要就自己放并用 `JIANPU_IMAGES` 指过去）、
  `tools/refresh*`（重建索引要完整的 jianpu2 流水线，便携包里没有 —— 投稿仍会正常落库并 commit，
  只是"当场重建索引"这一步会被跳过）。
""")
    n += 6

    # 5) 压缩（可选）
    zip_path = ""
    if a.zip:
        zip_path = out + ".zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for dp, _dn, fn in os.walk(out):
                for f in fn:
                    full = os.path.join(dp, f)
                    z.write(full, os.path.relpath(full, os.path.dirname(out)))
        print(f"  压缩包 {zip_path}  {os.path.getsize(zip_path)/1e6:.1f} MB")

    total = sum(os.path.getsize(os.path.join(dp, f))
                for dp, _dn, fn in os.walk(out) for f in fn)
    print(f"\n打好: {out}  ({n} 个文件, {total/1e6:.1f} MB)  [{ver}]")
    print(f"  语料: {'已拷入包内' if a.corpus == 'copy' else ('首次运行 setup 去 clone' if a.corpus == 'clone' else '不含')}")
    if zip_path:
        print(f"  zip : {zip_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

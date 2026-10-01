# -*- coding: utf-8 -*-
r"""C 阶段收尾：把三份文档同步到 FastAPI 写后端 + 对拍 21/21 + 已灰度切换。

幂等（按新内容独有的标记判断）+ 断言（锚点找不到就报错，不静默跳过 —— 这个坑踩过两次）。
⚠ 本文件里的大段文本用**三引号单引号**包，因为正文里到处是中文引号与英文双引号，
  上一版用双引号字符串包、正文里又出现 `"` 直接语法错（script 自己跑不起来）。
用法: py -3.13 tools/_patch_docs_phase_c.py [--apply]
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EDGE_ROWS = '''| **FastAPI + Pydantic v2**（2026-10-01 C 阶段） | 写后端要的是**类型化契约 + 自动文档**：投稿 4 种载荷从「字典里摸 key」变成可校验模型，`/docs`（Swagger）由代码生成 | 业务口径**一行没重写**（全部 import 自 `app/server.py`）；4 种 kind 用 discriminated union 表达；错误文案/状态码/CORS 与旧版逐项对齐（见下条） |
| **灰度对拍矩阵** `tools/check_parity_legacy_vs_fastapi.py` | 换传输层最危险的是**边角行为**（预检状态码、404 的 body、越界拒绝、坏 JSON 文案、body 上限、**鉴权与解析的先后**） | 21 条用例 × 三种鉴权场景（无口令/错口令/对口令，后者会真写）**全部一致**；对拍过程当场抓到 4 类差异（含一处**鉴权顺序**差异：新版原本先报 400 解析错、旧版先报 403） |
| **uv**（`pyproject.toml` + `uv.lock`） | 依赖要能**一条命令还原**（换机器、进 CI 都算数） | `uv lock` 解析 30 个包 2.64 s；`uv run python app/api.py 8785` 直接跑起来（实测 `/docs` 200） |
'''

STAR9 = '''### 9. 换掉传输层：靠「对拍矩阵」而不是「跑一遍自检」来证明行为一致

* **S**：写后端要从 Python 标准库搬到 FastAPI（为了类型化契约与自动文档），但投稿是**唯一会写盘 + git commit** 的路径 —— 换错了就是脏数据/丢投稿。
* **A**：业务口径**不重写**（复用 `server.py` 的函数），只换传输层；然后用 **21 条请求 × 三种鉴权场景**逐项比对（状态码 / CORS 头 / 归一化后的响应体），并且**对拍只打隔离实例**（临时语料库 + 独立 git 仓库，不碰真数据）。
* **R**：对拍当场抓到 4 类差异，全都不是「跑一遍自检」能发现的：① `OPTIONS` 预检 204 vs 405；② 静态/原图/404 少一个 CORS 头（浏览器会读不到响应体）；③ **鉴权顺序**：新版先报 400 解析错、旧版先报 403 —— 安全语义上的实打实差异；④ 中间件早退的响应漏了 CORS。修完全部 **21/21 一致**才切换；切换后立刻在正式域名上跑了一次真投稿（留档 + `git commit` 成功）。

'''

WRITE_ROW_OLD = '| 写服务 | 校验 → 改曲谱 → `git commit` → 触发重建 | 本机 `app/server.py`（Python 标准库），经隧道暴露，`X-Token` 鉴权 |'
WRITE_ROW_NEW = ('| 写服务 | 校验 → 改曲谱 → `git commit` → 触发重建 | **两份实现、一份口径**：`app/api.py`（FastAPI + '
                 'Pydantic v2，主力，`/docs` 自动文档）与 `app/server.py`（标准库版，零依赖，随时可退回）；'
                 '经隧道暴露，`X-Token` 鉴权；切换前用对拍矩阵逐项验证 |')

HOST_OLD = '| `http://127.0.0.1:8770/` | 本机服务 | 开发/内网；**唯一真正写盘 + git commit 的地方** |'
HOST_NEW = ('| `http://127.0.0.1:8770/` | 本机写后端（**FastAPI 版**） | 开发/内网；**唯一真正写盘 + git commit 的地方**；'
            '`/docs` 有交互式接口文档；退回标准库版: `py -3.13 app/server.py 8770` |')

RESUME_NEW = '''* **写后端迁移（FastAPI + Pydantic v2，零口径重写）**：把本机投稿服务从 Python 标准库迁到 FastAPI，用 Pydantic v2 的 discriminated union 表达 4 种投稿载荷，并自动产出 OpenAPI/Swagger `/docs`；业务规则全部复用旧实现。为了「敢切」写了 **21 条请求 × 3 种鉴权场景的对拍矩阵**（只打隔离实例），当场抓到 4 类差异 —— 预检状态码、响应缺失 CORS 头、以及一处**鉴权与解析的先后顺序**差异 —— 修到 **21/21 全部一致**才在正式域名上切换，切换后真投稿（留档 + git commit）通过。依赖用 `uv`（`pyproject.toml` + `uv.lock`）一条命令还原。
'''


def patch(rel, pairs, apply, markers):
    p = os.path.join(ROOT, rel)
    t = io.open(p, encoding="utf-8").read()
    changed = []
    for (old, new), marker in zip(pairs, markers):
        if marker and marker in t:
            continue
        assert old in t, "%s: 找不到锚点 -> %r" % (rel, old[:70])
        t = t.replace(old, new, 1)
        changed.append(marker or new.split("\n")[0][:40])
    if changed and apply:
        io.open(p, "w", encoding="utf-8", newline="\n").write(t)
    for c in changed:
        print("  %s: %s%s" % (rel, c, "" if apply else "（未写，加 --apply）"))
    if not changed:
        print("  %s: 已是最新" % rel)


def main():
    apply = "--apply" in sys.argv
    edge_anchor = "| **GitHub Pages 只读镜像 + Actions** |"
    patch("docs/TECH_STACK.md", [(edge_anchor, EDGE_ROWS + edge_anchor)], apply, ["FastAPI + Pydantic v2"])
    qa_anchor = "## 五、常见追问与答法（面试 Q&A）"
    patch("docs/TECH_STACK.md", [(qa_anchor, STAR9 + qa_anchor)], apply, ["### 9. 换掉传输层"])
    patch("docs/ARCHITECTURE.md", [(WRITE_ROW_OLD, WRITE_ROW_NEW)], apply, ["两份实现、一份口径"])
    patch("docs/ARCHITECTURE.md", [(HOST_OLD, HOST_NEW)], apply, ["**FastAPI 版**"])
    patch("docs/RESUME.md", [("* **数据一致性工程**", RESUME_NEW + "* **数据一致性工程**")], apply, ["写后端迁移（FastAPI"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
r"""D 阶段文档同步：把 Docker/Compose、ruff/mypy、pre-commit、Prometheus 指标写进三份文档。

幂等（按新内容独有的标记判断）+ 断言（锚点找不到就报错）。
用法: py -3.13 tools/_patch_docs_phase_d.py [--apply]
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

D_ROWS = '''| **Docker + Compose**（一条命令起） | 换机器/进 CI 要能"一条命令复现环境"，而不是靠人记三条命令 | `Dockerfile`（多阶段: uv 依赖层 + 运行层、非 root、`HEALTHCHECK` 用应用自己的 `/api/health`）+ `docker-compose.yml`（`api` + `tunnel` profile；**语料挂卷不烤进镜像**；口令不给默认值，缺了就报错停下）。⚠ 本机**没装 Docker**，所以本机做的是"步骤等价性"验证（干净目录跑依赖层 `uv sync --frozen` → 31 包；再用那套 venv 一字不改跑 Dockerfile 的 HEALTHCHECK），**真正的镜像构建放在 CI 的 `docker` job** |
| **ruff + mypy 渐进门槛** | 新代码要有风格/类型底线，但历史 60+ 脚本全量开会淹没真问题 | `tools/lint_python.py`（唯一真源，pre-commit 与 CI 都调它）: 本次现代化产出的 5 个文件过 ruff、`app/api.py` 过 mypy；`app/server.py` 用 per-file-ignores 登记在案（它是"随时可退回"的备份实现，为风格动它纯风险） |
| **pre-commit** | 让"提交前就该发现的问题"在提交前发现 | 官方文件卫生钩子 + 4 个本地钩子（python 门槛 / `npm run typecheck` / 文档数字对账 / 前端检索用例）；实测 **11 个钩子全过**（tsc 与 search-check 都真跑过） |
| **Prometheus `/metrics`** | "投稿有没有在进来""上游还活着吗"以前只能翻日志 | 官方客户端；`jianpu_http_requests_total{method,path,status}`（**路径用归一化模板**，否则 1.1 万谱页会把时间序列炸掉）+ 耗时直方图 + `jianpu_submissions_total{kind,result}` + 语料规模 gauge。真语料实测 `jianpu_corpus_songs 11495` ✓，用官方解析器校验 **14 个指标族**全部合规 |
| **GitHub Actions 三条流水线** | 本地绿 ≠ CI 绿（D 阶段就被这条打脸，见 STAR 第 10 条） | `checks.yml`: `web`（node 20/22 × 三份 tsconfig + 前端自检 + 性能基线）、`python`（3.11/3.13 × 门槛 + 投稿漏斗 + **起两个实现跑 21 条对拍** + 文档对账）、`docker`（**真构建镜像**并起来打 `/api/health`、`/metrics`） |
'''

STAR10 = '''### 10. "本地一直绿，CI 一直红"——三次都栽在**测试装置**上

D 阶段把质量门搬到 CI 之后，连着暴露了三个问题，**没有一个是业务代码的 bug**，全是"装置/环境"的：

* **① 少一行 `npm ci`（我的回归）**：B 阶段给 Pages 工作流加了 `npm run typecheck`、又让构建依赖 `esbuild`，
  但那个工作流**从来没装过依赖**（以前只用 Node 内置模块）。于是 push 之后 `Deploy to GitHub Pages` 直接红。
  排查顺序值得记：先比"本地构建哈希 vs 仓库根 vs 镜像 vs 正式站"（**全一致**，排除掉最像的原因）→
  再把 `node_modules` 挪走、在本机复现出 `'tsc' is not recognized`（**证实**）→ 加 `npm ci` + npm 缓存。
* **② 对拍脚本自己也要对环境做归一化**：CI 里两边各自用一份隔离语料库（路径不同），而错误文案里带着这个路径
  （"找不到 linkurl.py —— JIANPU_DB=…"），于是**4 条全部误报**。修法: 从两边 `/api/health` 读回各自的 `repo`，
  在比对前把路径替换成 `<DB>`（和之前处理 origin/端口是同一个套路）。
* **③ 隔离库没有基线提交**：`git` 不跟踪空目录，CI 里挑不到真曲谱时 `git commit` 以 "nothing to commit" 失败
  → **"写回提交"那条路径根本没被测到**。修法: 生成器固定写一个说明文件把基线立起来。

**结论（面试可讲）**：把"本地能跑"升级成"CI 能证明"时，先怀疑装置、再怀疑代码；
而"对拍/回归"这类装置**自己也会骗人** —— 误报会让人放松对真差异的敏感度，所以装置的信号必须做环境归一化。
'''

RESUME_D = '''* **工程质量门与可观测性**：给写后端加 **Prometheus `/metrics`**（官方客户端；请求计数用**归一化路径模板**避免 1.1 万谱页把时间序列基数炸掉；投稿结果按 `kind/result` 计数；语料规模 gauge 带 TTL 缓存），真语料实测 `corpus_songs 11495` 并用官方解析器校验 14 个指标族；建立 **ruff + mypy 渐进门槛**（新代码必过、历史文件登记在案）与 **pre-commit**（11 个钩子）；写 `Dockerfile` + `docker-compose.yml` 做到一条命令起服务；把质量门搬上 **GitHub Actions 三条矩阵流水线**（web / python / docker），其中 python 那条会**真起新旧两个实现跑 21 条对拍**。过程中修掉三个"装置级"问题（CI 缺 `npm ci`、对拍脚本未归一化隔离库路径、隔离库缺基线提交），本地复现逐一证实。
'''

ARCH_OPS_OLD = '| 写路径 | 依赖隧道与开机 | 本机在线才有写能力 | 可选上云（R2/D1）或改成"队列 + 稍后入库" |'
ARCH_OPS_NEW = ('| 写路径 | 依赖隧道与开机 | 本机在线才有写能力 | 可选上云（R2/D1）或改成"队列 + 稍后入库" |\n'
                '| 观测 | `/api/health` + `/metrics` | 读路径指标在 Cloudflare 侧（分析面板/Worker 指标），'
                '写路径指标在 `/metrics`（Prometheus 文本格式，可被任意 Prometheus/Grafana 抓） | '
                '把"上游死活"做成告警规则（`upstreamOk`）、把投稿漏斗做成面板 |')

ARCH_DEPLOY_OLD = '| 便携包 `jianpu-server/` | 同一份服务的可搬走版本 | 换台电脑：Python + （可选）cloudflared 即可 |'
ARCH_DEPLOY_NEW = ('| 便携包 `jianpu-server/` | 同一份服务的可搬走版本 | 换台电脑：Python + （可选）cloudflared 即可 |\n'
                   '| **容器** `docker compose up -d api` | 一条命令起写后端（`--profile tunnel` 连隧道一起） | '
                   '语料**挂卷**（投稿要 `git commit` 到真仓库）；镜像构建在 CI 的 `docker` job 里验证 |')


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
    qa_anchor = "## 五、常见追问与答法（面试 Q&A）"
    patch("docs/TECH_STACK.md", [(qa_anchor, STAR10 + qa_anchor)], apply, ["### 10. \"本地一直绿，CI 一直红\""])
    edge_anchor = "| **GitHub Pages 只读镜像 + Actions** |"
    patch("docs/TECH_STACK.md", [(edge_anchor, D_ROWS + edge_anchor)], apply, ["Docker + Compose"])
    patch("docs/ARCHITECTURE.md", [(ARCH_OPS_OLD, ARCH_OPS_NEW)], apply, ["/metrics"])
    patch("docs/ARCHITECTURE.md", [(ARCH_DEPLOY_OLD, ARCH_DEPLOY_NEW)], apply, ["容器"])
    patch("docs/RESUME.md", [("* **数据一致性工程**", RESUME_D + "* **数据一致性工程**")], apply, ["工程质量门与可观测性"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

# jianpu-db · 简谱旋律查歌

**只哼开头几个音，把这首歌的简谱找出来。** 检索完全在浏览器里跑，不上传、不留痕。

[![曲谱](https://img.shields.io/badge/%E6%9B%B2%E8%B0%B1-11%2C380%20%E9%A6%96-0b62c4)](https://jianpu-db.org/)
[![音符](https://img.shields.io/badge/%E9%9F%B3%E7%AC%A6-2%2C528%2C861-0b62c4)](https://jianpu-db.org/)
[![小节线](https://img.shields.io/badge/%E6%98%BE%E5%BC%8F%E5%B0%8F%E8%8A%82%E7%BA%BF-551%2C939-0b62c4)](https://jianpu-db.org/)
[![索引](https://img.shields.io/badge/%E7%B4%A2%E5%BC%95-5.12%20MB%20gz-177245)](https://jianpu-db.org/)
[![查询](https://img.shields.io/badge/%E6%9F%A5%E8%AF%A2%E4%B8%AD%E4%BD%8D-106%20ms-177245)](https://jianpu-db.org/)
[![Top-1](https://img.shields.io/badge/%E9%87%91%E6%9B%B2%E6%A6%9C%20L%3D15%20%E9%94%990%20Top--1-98.9%25-177245)](https://jianpu-db.org/)
[![自检](https://img.shields.io/badge/%E8%87%AA%E6%A3%80%2FQA-78%20%E4%B8%AA%E8%84%9A%E6%9C%AC-946200)](https://jianpu-db.org/)

**线上**：https://jianpu-db.org/（正式站，可投稿） · https://jianpu-db.github.io/（只读镜像）

---

## 三个位置的关系

```mermaid
flowchart LR
  subgraph DEV["作者本机（你的电脑）"]
    PIPE["转写流水线<br/>Qwen3-VL-2B + PyTorch"]
    CORPUS[("语料仓库<br/>曲谱 + data.jsonl")]
    API["写后端<br/>FastAPI /api/*"]
    TUN["cloudflared 隧道"]
  end
  subgraph GH["GitHub"]
    SRC["站点仓库<br/>jianpu-db.github.io<br/>源码 + 构建产物"]
    PAGES["GitHub Pages<br/>jianpu-db.github.io"]
  end
  subgraph CF["Cloudflare 边缘"]
    W["Worker + Assets<br/>jianpu-db.org"]
  end
  BROWSER["访客浏览器<br/>检索全部在这里跑"]

  PIPE -->|转写| CORPUS
  CORPUS -->|构建索引| SRC
  SRC -->|CI 构建| PAGES
  SRC -->|wrangler deploy| W
  PAGES -->|只读镜像| BROWSER
  W -->|索引 + 静态资源| BROWSER
  BROWSER -->|投稿 / 纠错| W
  W -->|"/api/* 反代"| TUN
  TUN --> API
  API -->|git commit| CORPUS
```

| 位置 | 是什么 | 承担什么 |
|---|---|---|
| **作者本机** | 语料仓库 + 转写流水线（Qwen3-VL-2B / PyTorch）+ FastAPI 写后端 | 生产曲谱；受理投稿并写回语料 |
| **GitHub** | 站点仓库 `jianpu-db.github.io`（前端源码 + 构建产物）；同一仓库用 Pages 发布 | 源码托管；提供**只读镜像**入口 |
| **Cloudflare** | Worker + Assets 承载 **jianpu-db.org** | 正式站：静态资源、每谱 meta 注入、`/api/*` 反向代理 |

**读路径**：扫描件 →（本机转写）→ 语料 →（构建）→ 索引 → 站点仓库 → 边缘 / CDN → **浏览器内检索**。
**写路径**：浏览器 → 边缘反代（`X-Token`）→ 本机服务 → 语料仓库（`git commit`）。

## 这是什么

一份**简谱语料**加一个**旋律检索站**：曲谱由视觉语言模型（Qwen3-VL-2B）从扫描件转写而来，
检索不是"按歌名搜"，而是**按旋律**——输入你记得的那几个音（如 `5 5 6 5 3 2 1`），
从全库 11,380 首里按"代价"排序找出候选。**计算全部发生在浏览器里**，服务器只发一份索引。

## 技术文档（面试 / 评审向）

| 文档 | 内容 |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 全链路架构图、组件职责、数据契约（曲谱文件 / 索引字段 / 写接口）、部署拓扑、瓶颈与扩展 |
| [docs/TECH_STACK.md](docs/TECH_STACK.md) | 分层选型与理由、量化收益、"刻意没用"的清单、15 条难题复盘、20 条问答 |
| [docs/QA.md](docs/QA.md) | 质量工程：自检脚本清单、判据与证据、运行位置 |
| [docs/交付总结.md](docs/交付总结.md) | 交付总结：五个阶段做了什么、实测数字、被数据否决的尝试 |
| [docs/MOCK_INTERVIEW.md](docs/MOCK_INTERVIEW.md) | 面试演练稿：自我介绍、追问与追问的追问、规模问题、数字速查、复现命令 |
| [docs/RESUME.md](docs/RESUME.md) | 中英文简历要点与需提前准备的追问 |
| [DEPLOY.md](DEPLOY.md) | 域名 / 边缘 / 隧道 / 写后端的部署手册 |

## 技术栈（一句话）

TypeScript 前端（零运行时依赖，检索全部在浏览器内完成）+ Cloudflare Workers 边缘 + FastAPI 写后端
+ Python / Qwen3-VL 转写流水线；语料为纯文本曲谱与 JSONL 索引，由 git 版本化。

## 本地跑

```bash
# 站点（只读预览 + 可写回，端口 8770）
py -3.13 app/server.py 8770        # 需要 Python 3.10+；服务本身只用标准库

# 静态预览（构建产物）
node tools/build_dist.mjs --target gh --out dist-gh
tools\serve_local.cmd 8899 gh

# 自检
node tools/check_ui.mjs && node tools/check_search.mjs && node tools/check_live.mjs
node tools/bench_search.mjs 40      # 量索引构建与查询延迟
```

写路径与语料在另外两个仓库：语料 `Francium-223/jianpu-db`、流水线工具 `jianpu2`。

## 许可与出处

曲谱的**页面地址与元数据**来自 qupu123 / jianpu.cn / jianpujia 三个公开站点；
本仓库只保存**自己转写的数字序列**与出处链接，不打包、不外链原扫描件。

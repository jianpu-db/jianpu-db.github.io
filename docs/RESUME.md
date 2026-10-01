# 简历要点（可直接抄）—— jianpu-db / 简谱旋律查歌

> 用法：中文版抄"项目经历"，英文版抄 CV。每条都带**可追问的数字**（面试官问"这数怎么来的"，
> 答：`jianpu2/tools/measure_stack.py`、站点 `tools/bench_search.mjs`、`eval_golden.py` 可当场复现）。
> 详细选型理由见 [`TECH_STACK.md`](TECH_STACK.md)，全链路见 [`ARCHITECTURE.md`](ARCHITECTURE.md)。

## 一句话

> 用视觉语言模型把 3 家简谱站的扫描件转成**11,495 首 / 253 万音符**的可检索语料，
> 做成一个"**只哼开头几个音就能查到这首歌**"的站点：检索**全在浏览器里跑**（索引 5.12 MB gz、
> 就绪 204 ms、单次查询中位 151 ms），写回走"边缘反代 + 本机 git 提交"的混合架构。

## 中文（项目经历，4~6 条）

* **从 0 搭起数据流水线**：爬取 3 个简谱站 → 图像预处理 → **Qwen3-VL-2B** 转写 → 纯度/织体质量门 →
  多页拼接 → 词元化（自研口径）→ 语料入库 → 索引构建，产出 **11,495 首 / 2,532,332 音符**语料；
  转写吞吐实测 **59–95 份/小时**（单卡 4070），全流程幂等可重跑。
* **把"残谱"问题量化着修**：发现多页谱只转了第一页（榜单曲只剩 6 个音符），实现多页拼接 + 每页质量门；
  对 858 个多页目录回锅，**487 份变长（中位 +276 音、累计 +184,274 音）**。
* **为 OCR 做"零成本置信度"**：用贪心解码的 `output_scores` 取每个数字的 top-1 概率做置信度，
  实测开关前后耗时无差异；多页按数字数加权、`p10` 取最差页。**自查中揪出并修掉**"多页只取最后一页"的缺陷。
* **边缘端解决"爬虫不跑 JS"**：SPA 的 1.1 万个谱页原本是同一份 HTML；用 Cloudflare Worker 的
  `run_worker_first` + 构建期 `og.json` 给 `/s/<id>` **注入每谱 meta**（title/OG/canonical），
  并生成 11,496 条 sitemap；期间定位过一次"变量声明被误删 → 异常被 try/catch 吞掉 → 页面正常但功能静默失效"的坑，
  之后在 `/api/health` 暴露索引计数当指示灯。
* **混合写回架构**：边缘只反代 + 注入 `X-Token`，真正写盘与 `git commit` 在作者本机（Cloudflare Tunnel 出去），
  读路径完全不依赖本机；踩过 `vars`/`secret` 同名（Cloudflare code 10053）与端口/证书过期等一系列部署问题。
* **前端与边缘 TypeScript 化（零运行时依赖、零性能回退）**：`jptok`（词元口径）/ `search`（代价匹配）/ `app`（867 行主界面）/ `worker`（边缘路由与鉴权）四处源码全迁到 TS，构建只用 esbuild 做**类型擦除**（仍不打包运行时依赖），`tsc` 三份配置（strict 核心 + 主界面过渡 + strict Worker）进 CI；实测索引就绪 204→179–201 ms、查询中位 151→139.9 ms **无回退**，主界面产物 46,449→33,605 B（**-28%**）。迁移过程中靠"读代码 + 跑自检"抓到三处静默问题：索引字段白名单漏 `conf`（导致并列裁决里"置信度优先"形同虚设）、`renderScore` 从未 export 使一条自检**从写下就没通过**、以及一条断言字面量与 schema 化后的 DOM 结构不匹配。
* **写后端迁移（FastAPI + Pydantic v2，零口径重写）**：把本机投稿服务从 Python 标准库迁到 FastAPI，用 Pydantic v2 的 discriminated union 表达 4 种投稿载荷，并自动产出 OpenAPI/Swagger `/docs`；业务规则全部复用旧实现。为了「敢切」写了 **21 条请求 × 3 种鉴权场景的对拍矩阵**（只打隔离实例），当场抓到 4 类差异 —— 预检状态码、响应缺失 CORS 头、以及一处**鉴权与解析的先后顺序**差异 —— 修到 **21/21 全部一致**才在正式域名上切换，切换后真投稿（留档 + git commit）通过。依赖用 `uv`（`pyproject.toml` + `uv.lock`）一条命令还原。
* **数据一致性工程**：语料"只拷不覆盖"会让新元数据进不了库（实测 2,153 处空栏）、"只增不删"会留下 1,332 份判废残留；
  分别写了**只填空栏、绝不覆盖人工值**的合并工具与**默认 dry-run、只移不删**的清理工具，标签覆盖 60.6% → 79.5%。

## 中文（"技术亮点"一栏，3 条）

* **边缘原生（Edge-native）**：Cloudflare Workers + Assets 承载读路径，自定义域名 + 自动 TLS；
  `/s/<id>` 做边缘 meta 注入（SSR-lite），静态资源内容寻址 + immutable 长缓存。
* **零依赖前端**：无框架、无打包器、无 npm 运行时依赖；`DecompressionStream` 流式解压 5.12 MB 索引，
  11,495 首全库**代价匹配**在浏览器内 151 ms（p90 198 ms）。
* **可复现评测与质量门**：金曲清单评测（L=11/15、Top-1/3/5、**错音必须为 0**、公开"代价并列率"），
  78 个自检/QA 脚本 + Python↔JS 口径互锁（21,711 token 逐项一致）。

## English (CV bullets)

* Built an end-to-end pipeline that turns scanned **jianpu** (numbered-notation) sheet music from three
  sites into a searchable corpus of **11,495 songs / 2.53M notes**, using a **vision-language model
  (Qwen3-VL-2B)** for structured extraction, plus purity/texture quality gates and multi-page stitching;
  measured throughput **59–95 scores/hour** on a single 8 GB GPU, fully idempotent and re-runnable.
* Designed **client-side retrieval**: a gzip-streamed 5.12 MB index is decompressed and indexed in
  **204 ms**, and an octave-insensitive *cost-based* melody matcher answers a query in **151 ms median**
  (p90 198 ms) **entirely in the browser** — no backend, no framework, no bundler.
* Shipped an **edge-native** deployment on **Cloudflare Workers + Assets** with per-song metadata
  injection for 11.5k SPA deep links (`run_worker_first`), custom domain + automatic TLS, and a
  **hybrid write path** (edge reverse proxy → author's machine → `git commit`) gated by `X-Token`.
* Instrumented reliability: `/api/health` surfaces index cardinality, upstream and write-back status;
  78 self-check/QA scripts and cross-implementation parity locks (21,711 tokens) guard every release;
  a gold-list eval harness enforces **0 wrong-note queries** and reports tie rates.

## 可能被追问的点（提前准备）

| 追问 | 一句话答 |
|---|---|
| 为什么检索放浏览器？ | 无服务端成本、隐私好（查询不上传）、延迟可控（151 ms）；索引 5 MB 级完全装得下 |
| 为什么不用数据库/框架？ | 语料是文件 + JSONL；引入只会增加部署面。需要并发写/服务端检索时再上（见 TECH_STACK 第三节） |
| VLM 会不会不可靠？ | 三道质量门 + 置信度分位 + 人工 `ok` 状态；并公开"代价并列率"暴露不确定性 |
| 你怎么发现自己的 bug？ | 靠**产物侧的证据**：sidecar 的 `page_notes`/`dropped_pages`、`compare_multipage` 的变长/变短统计、以及每次收尾对账 |
| 最脆弱的一环？ | 写路径依赖本机在线 + 隧道（读完全不受影响）；其次单卡转写吞吐 —— 两条都有明确替代路线 |

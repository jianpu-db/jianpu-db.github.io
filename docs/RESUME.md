# 简历要点（可直接抄）—— jianpu-db / 简谱旋律查歌

> 用法：中文版抄"项目经历"，英文版抄 CV。每条都带**可追问的数字**（面试官问"这数怎么来的"，
> 答：`jianpu2/tools/measure_stack.py`、站点 `tools/bench_search.mjs`、`eval_golden.py` 可当场复现）。
> 详细选型理由见 [`TECH_STACK.md`](TECH_STACK.md)，全链路见 [`ARCHITECTURE.md`](ARCHITECTURE.md)。

## 一句话

> 用视觉语言模型把 3 家简谱站的扫描件转成**11,495 首 / 253 万音符**的可检索语料，
> 做成一个"**只哼开头几个音就能查到这首歌**"的站点：检索**全在浏览器里跑**（索引 5.12 MB gz、
> 就绪 ~200 ms、单次查询中位 **106 ms**），写回走"边缘反代 + 本机 git 提交"的混合架构。

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
  读路径完全不依赖本机；处理过 `vars`/`secret` 同名（Cloudflare code 10053）与端口/证书过期等部署问题。
* **前端与边缘 TypeScript 化（零运行时依赖、零性能回退）**：`jptok`（词元口径）/ `search`（代价匹配）/ `app`（867 行主界面）/ `worker`（边缘路由与鉴权）四处源码全迁到 TS，构建只用 esbuild 做**类型擦除**（仍不打包运行时依赖），`tsc` 三份配置（strict 核心 + 主界面过渡 + strict Worker）进 CI；实测索引就绪 204→179–201 ms、查询中位 151→139.9 ms **无回退**（**B 阶段数字**；E 阶段再优化到 ~106 ms），主界面产物 46,449→33,605 B（**-28%**）。迁移过程中靠"读代码 + 跑自检"抓到三处静默问题：索引字段白名单漏 `conf`（导致并列裁决里"置信度优先"形同虚设）、`renderScore` 从未 export 使一条自检**从写下就没通过**、以及一条断言字面量与 schema 化后的 DOM 结构不匹配。
* **写后端迁移（FastAPI + Pydantic v2，零口径重写）**：把本机投稿服务从 Python 标准库迁到 FastAPI，用 Pydantic v2 的 discriminated union 表达 4 种投稿载荷，并自动产出 OpenAPI/Swagger `/docs`；业务规则全部复用旧实现。为了「敢切」写了 **21 条请求 × 3 种鉴权场景的对拍矩阵**（只打隔离实例），当场抓到 4 类差异 —— 预检状态码、响应缺失 CORS 头、以及一处**鉴权与解析的先后顺序**差异 —— 修到 **21/21 全部一致**才在正式域名上切换，切换后真投稿（留档 + git commit）通过。依赖用 `uv`（`pyproject.toml` + `uv.lock`）一条命令还原。
* **工程质量门与可观测性**：给写后端加 **Prometheus `/metrics`**（官方客户端；请求计数用**归一化路径模板**避免 1.1 万谱页把时间序列基数炸掉；投稿结果按 `kind/result` 计数；语料规模 gauge 带 TTL 缓存），真语料实测 `corpus_songs 11495` 并用官方解析器校验 14 个指标族；建立 **ruff + mypy 渐进门槛**（新代码必过、历史文件登记在案）与 **pre-commit**（11 个钩子）；写 `Dockerfile` + `docker-compose.yml` 做到一条命令起服务；把质量门搬上 **GitHub Actions 三条矩阵流水线**（web / python / docker），其中 python 那条会**真起新旧两个实现跑 21 条对拍**。过程中修掉三个"装置级"问题（CI 缺 `npm ci`、对拍脚本未归一化隔离库路径、隔离库缺基线提交），本地复现逐一证实。
* **性能剖析（先量再改）**：为"全库旋律匹配"写了 **Rust→Wasm** 内层循环（0.9 KB，零依赖、无 wasm-bindgen），并用**逐首对拍**（12 查询 × 11,495 首 = 137,940 次比较，代价逐首一致）与**端到端对拍**（快路径 vs 纯 TS，结果逐条相同）证明正确性；但**实测端到端 0.84×（更慢）**，于是**不带上线**。继续细分计时后发现真瓶颈是排序比较器里每次 `new Set()`（`O(n log n)` 次分配），改成**预计算排序键**后查询中位 **144.6 → 105.9 ms（-27%）**。这条经历说明"加速器只有在替换掉的部分确实是瓶颈时才有意义"。
* **检索性能（两轮优化，都是先量再改）**：① 排序键预计算（144.6 → 105.9 ms，-27%）；② **ngram 剪枝**（带"可证明安全"的采用条件：只在每段都精确命中、且代价 0 的结果够填满榜单时才用，长查询再降 30%）；两次都用**对拍**证明结果逐条不变（含模糊与多段查询）；此外完成一次 **Rust→Wasm** 的完整尝试并用实测数据否决（端到端 0.84×）。
* **质量工程（自检不停）**：为持续扩大的语料做了**只读、0.5 s** 的夜间抽检（不与应用抢 GPU/内存），用产物里的证据分类问题谱（原图张数 / sidecar 的 `dropped_pages` / 归组归一化），实测发现 **15 份"多页未转全"**（真数据损失）与 **23 个组名被拆成 46 组**（大小写与标点差异导致用户可见重复）；改动前先做**内存原型验证**（组数 8625→8602、7 条旋律查询结果逐条不变）再实施。
* **数据一致性工程**：语料"只拷不覆盖"会让新元数据进不了库（实测 2,153 处空栏）、"只增不删"会留下 1,332 份判废残留；
  分别写了**只填空栏、绝不覆盖人工值**的合并工具与**默认 dry-run、只移不删**的清理工具，标签覆盖 60.6% → 79.5%。

## 中文（"技术亮点"一栏，3 条）

* **边缘原生（Edge-native）**：Cloudflare Workers + Assets 承载读路径，自定义域名 + 自动 TLS；
  `/s/<id>` 做边缘 meta 注入（SSR-lite），静态资源内容寻址 + immutable 长缓存。
* **零依赖前端**：无框架、无打包器、无 npm 运行时依赖；`DecompressionStream` 流式解压 5.12 MB 索引，
  11,495 首全库**代价匹配**在浏览器内 106 ms（优化后；p90 114 ms）。
* **可复现评测与质量门**：金曲清单评测（L=11/15、Top-1/3/5、**错音必须为 0**、公开"代价并列率"）；
  **12 条排名金标准进 CI**（每条查询都取自目标歌自己的谱，所以正确答案是客观的，不是"把当前行为烤进去"）；
  78 个自检/QA 脚本 + Python↔JS 口径互锁（21,711 token 逐项一致）。
* **一个"缺失假装成功"的线上 bug**：SPA 兜底把任何找不到的路径用 `200 + index.html` 返回，
  于是"文件不在"表现为"读到了 HTML"（自检 gunzip 直接崩）。补丁式白名单两次被绕过
  （URL 规范化吃掉 `/img/` 前缀、`../etc/passwd` 没有扩展名），最后改成**原则**：
  SPA 兜底只对页面路由生效，其余路径查到 HTML 即 404；补了 Worker 路由回归测试（23 项）进 CI。

## English (CV bullets)

* Built an end-to-end pipeline that turns scanned **jianpu** (numbered-notation) sheet music from three
  sites into a searchable corpus of **11,495 songs / 2.53M notes**, using a **vision-language model
  (Qwen3-VL-2B)** for structured extraction, plus purity/texture quality gates and multi-page stitching;
  measured throughput **59–95 scores/hour** on a single 8 GB GPU, fully idempotent and re-runnable.
* Designed **client-side retrieval**: a gzip-streamed 5.12 MB index is decompressed and indexed in
  **~200 ms**, and an octave-insensitive *cost-based* melody matcher answers a query in **106 ms median**
  (p90 114 ms) **entirely in the browser** — no backend, no framework, no bundler (two rounds of
  profiling-driven optimisation; see the performance bullets below).
* Shipped an **edge-native** deployment on **Cloudflare Workers + Assets** with per-song metadata
  injection for 11.5k SPA deep links (`run_worker_first`), custom domain + automatic TLS, and a
  **hybrid write path** (edge reverse proxy → author's machine → `git commit`) gated by `X-Token`.
* Instrumented reliability: **Prometheus `/metrics`** (normalised path templates so 11.5k score pages cannot explode label cardinality, corpus-size gauge with TTL) plus `/api/health` surfacing index cardinality, upstream and write-back status;
  78 self-check/QA scripts and cross-implementation parity locks (21,711 tokens) guard every release;
  a gold-list eval harness enforces **0 wrong-note queries** and reports tie rates.
* **Migrated the write backend to FastAPI + Pydantic v2 (uv-locked, auto-generated OpenAPI `/docs`) with
  zero business-logic rewrite**, proving equivalence with a **21-request × 3-auth-scenario parity matrix**
  against the legacy stdlib service, then cut over behind a switch that can roll back instantly.
* **Performance engineering (measure first):** profiled the shipped matcher and found the real bottleneck
  was the sort comparator allocating a `Set` per comparison — precomputing sort keys cut query latency
  **144.6 → 105.9 ms (-27%)**; then added an **n-gram prefilter** that prunes to **0.01–6.8%** of the corpus,
  cutting long-query latency a further **30% (69.1 → 48.2 ms)** while returning **identical** results
  (verified on 60 queries incl. fuzzy and multi-segment).
* **A rejected optimization, decided by measurement:** built a **Rust→Wasm** inner loop (0.9 KB, no
  wasm-bindgen) and proved per-song cost parity on **137,940 comparisons** plus identical end-to-end
  results — but measured only **0.84× end-to-end**, so it was **not shipped**; the negative result is
  documented and the harness kept as an asset.
* **Quality gates in CI:** a **12-case ranking golden set** (each query sliced from its target song's own
  score, so the expected answer is objective), a Worker-routing test asserting missing assets return
  **404** instead of the SPA fallback's `200 + HTML`, and a pruning-equivalence test; added
  **Docker build** to the matrix (the image's buildability is proven by CI, since the dev machine has no
  Docker) plus **ruff/mypy** thresholds and **pre-commit**.
* **Recall measured from both sides:** offline harness reports the **recall upper bound (Top-1 100%)**,
  while a product-side harness runs the shipped TypeScript matcher and reports the **actual ranking**
  (**Top-1 97.6% / Top-3 100% at L=15**, 93.7% at L=11); the two agree once the tie policy is equalised,
  which showed the remaining gap is **tie-break policy, not matching ability**.

## 可能被追问的点（提前准备）

| 追问 | 一句话答 |
|---|---|
| 为什么检索放浏览器？ | 无服务端成本、隐私好（查询不上传）、延迟可控（中位 106 ms）；索引 5 MB 级完全装得下 |
| 为什么不用数据库/框架？ | 语料是文件 + JSONL；引入只会增加部署面。需要并发写/服务端检索时再上（见 TECH_STACK 第三节） |
| VLM 会不会不可靠？ | 三道质量门 + 置信度分位 + 人工 `ok` 状态；并公开"代价并列率"暴露不确定性 |
| 你怎么发现自己的 bug？ | 靠**产物侧的证据**：sidecar 的 `page_notes`/`dropped_pages`、`compare_multipage` 的变长/变短统计、以及每次收尾对账 |
| 最脆弱的一环？ | 写路径依赖本机在线 + 隧道（读完全不受影响）；其次单卡转写吞吐 —— 两条都有明确替代路线 |

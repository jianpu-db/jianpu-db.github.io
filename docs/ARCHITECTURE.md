# 架构（jianpu-db）：一份"曲谱 → 可检索语料 → 边缘/浏览器检索 → 写回"的全链路

> 面向面试/评审：每个组件为什么在这儿、数据契约是什么、哪一步是瓶颈。
> 所有数字都是实测（工具：`jianpu2/tools/measure_stack.py`、站点 `tools/bench_search.mjs`）。

## 一、总览

```mermaid
flowchart TB
  subgraph SRC["数据源（爬虫）"]
    Q1["qupu123.com"]:::src
    Q2["jianpu.cn (jianpucn)"]:::src
    Q3["jianpujia"]:::src
  end

  subgraph PIPE["转写流水线（本地 GPU / 计划任务）"]
    PRE["预处理<br/>缩放 + 切横条<br/>System.Drawing"]
    VLM["视觉语言模型<br/>Qwen3-VL-2B-Instruct<br/>贪心解码 + 每 token 概率"]
    GATE["质量门<br/>纯度（五线谱/和弦谱判废）<br/>织体（单页>600 音丢页）"]
    MP["多页拼接<br/>JP_MULTIPAGE"]
    BO["batch-out/<br/>token 流 + sidecar(confidence)"]
    CONV["to_jianpu_db<br/>头部元数据 + %-- + token"]
    PRE --> VLM --> GATE --> MP --> BO --> CONV
  end

  subgraph CORPUS["语料仓库（git: jianpu-db）"]
    SCORES["scores/*.txt<br/>11,991 份"]
    JSONL["data.jsonl<br/>11,495 首 · 2,532,332 音符"]
    CONV --> SCORES --> JSONL
  end

  subgraph BUILD["构建（本地 / CI）"]
    BWD["build_web_data.py"]
    IDX["data/songs.jsonl.gz 5.12MB gz→31MB<br/>stats.json · og.json"]
    SEO["robots.txt · sitemap.xml（11,496 条）"]
    JSONL --> BWD --> IDX
    BWD --> SEO
  end

  subgraph EDGE["边缘（Cloudflare Workers + Assets）"]
    W["worker/index.js<br/>静态资产 / 每谱注入 meta / http→https"]
    R2["R2 原图桶（可选）"]
    IDX --> W
    SEO --> W
    W -.-> R2
  end

  subgraph BROWSER["浏览器（零依赖 ES module）"]
    APP["app.js 路由/卡片/谱页"]
    SEARCH["search.js 全库代价匹配"]
    JPT["jptok.js 查询解析"]
    APP --> SEARCH --> JPT
  end

  subgraph WRITE["写路径（本机服务 + 隧道）"]
    TUN["cloudflared 隧道"]
    SRV["app/server.py<br/>/api/submit · /img/* · /api/health"]
    FB["feedback/*.json 留档"]
    GIT["git commit（反馈即入库）"]
    TUN --> SRV --> FB
    SRV --> GIT
  end

  SOC["社交/搜索爬虫"]:::bot
  USER(["用户浏览器"]):::user

  USER -->|"https://jianpu-db.org"| W
  W --> APP
  APP -->|"POST /api/submit"| W
  W -->|"反代 + X-Token"| TUN
  GIT --> CORPUS
  SOC -->|"读 /s/&lt;id&gt; 的 head"| W
  W -->|"sparse clone（便携包）"| CORPUS

  classDef src fill:#eef,stroke:#88a
  classDef bot fill:#fee,stroke:#a88
  classDef user fill:#efe,stroke:#8a8
```

（GitHub 会直接渲染上面的 mermaid；本地看用 VS Code Markdown 预览。）

## 二、组件职责（一句话版）

| 组件 | 职责 | 关键实现 |
|---|---|---|
| 爬虫 | 抓原谱站的谱页图片 + 页面元数据（歌手/分类） | `urllib` + 自写 `tlsfetch.py`（证书过期时**只对出问题的 host** 关校验） |
| 预处理 | 把长条谱切成一页页、缩放 | `tools/preprocess.ps1`（.NET `System.Drawing`） |
| 转写 | 图 → 简谱数字 token 流 | **Qwen3-VL-2B-Instruct**，`generate(do_sample=False)` + `output_scores=True` |
| 质量门 | 挡掉五线谱/和弦谱/钢琴织体 | 纯度分类器 + 织体判据（单页 >600 音丢页；整份每页中位 >400 音跳过） |
| 多页拼接 | 一份谱多张图 → 一份稿 | `JP_MULTIPAGE=1`，页数上限 `JP_MULTIPAGE_MAX=4` |
| 成品化 | token 流 → 带头部元数据的曲谱文件 | `to_jianpu_db.py`（复用旧成品里的拍号/人工标记/composer→mbid 缓存） |
| 索引 | 曲谱 → 检索索引 + 分享卡索引 | `build_web_data.py` → `songs.jsonl.gz` / `stats.json` / `og.json` / `sitemap.xml` |
| 边缘 | 静态资产、每谱注入、原图、反向代理写请求 | Cloudflare Workers（V8 isolates）+ Workers Assets（`run_worker_first`） |
| 前端 | 检索/卡片/谱页/深链，**全部在浏览器里跑** | 零依赖 ES module（无框架、无打包器） |
| 写服务 | 校验 → 改曲谱 → `git commit` → 触发重建 | 本机 `app/server.py`（Python 标准库），经隧道暴露，`X-Token` 鉴权 |
| 语料仓库 | 唯一真源（曲谱 + 索引 + 留档） | git（CI 重建 + Pages 发布） |

## 三、数据契约

### 3.1 曲谱文件（`scores/*.txt`）——人可读、可 diff

```
%曲名.txt
title=曲名
tag=派生标签（由 usertag + tags.json 推出，不手写）
usertag=人工标签（逗号分隔）
artist=歌手
status=ok|ocr            # ok = 人工校对过；ocr = 机器转写
source=qupu123-396822    # 站点-id，用来回溯原页
confidence=0.976         # 转写置信度（每数字 top-1 概率均值）
conf_p10=0.921           # 最低 10% 的分位（个别音很虚时靠它发现）
link=https://…           # 人工核对过的收录页（搜索页会被拒收）
%--
4/4                      # 拍号
<简谱 token 流>
%END
```

> 设计取舍：**纯文本 + 一行一个字段**，`git diff` 人眼可读；元数据与正文用 `%--` 分开，
> 下游解析只需按 token 切分，不引入任何二进制/数据库依赖。

### 3.2 检索索引（`data/songs.jsonl.gz`）——一行一首

| 字段 | 含义 |
|---|---|
| `id` / `s` | 曲谱标识（`<站>-<站内id>`），也是深链 `/s/<id>` |
| `t` / `g` | 曲名 / 归一化分组键（同名多版本归组） |
| `p` / `a` / `o` | 音高序列（数字）/ 变音记号 / 八度标记（**八度不参与检索**，只用于展示） |
| `bars` / `bpb` | 显式小节线（音符下标）/ 拍号 |
| `sc` | 段落表（`起始下标:段落名`），用于"副歌优先"的段落权重 |
| `n` / `hot` | 音符数 / 人气代理（该曲歌手/标签在语料里的谱数） |
| `conf` / `confP10` | 置信度（低置信度的结果会被降权/提示） |
| `src` | 原谱原文（verbatim），谱页展示用 |
| `mbid` / `links` / `srcurl` | MusicBrainz work、人工收录页、原站页面 |

实测：5.12 MB gz → 31.01 MB 明文（6.1×），浏览器 gzip 解压 **56 ms** + 建索引 **148 ms**。

### 3.3 写接口（`POST /api/submit`）——四种载荷

| `kind` | 载荷 | 服务端行为 |
|---|---|---|
| `tags` | `{file, tags:[…]}` | 写 `usertag=` → 由 `tags.json` 派生 `tag=` |
| `attr` | `{file, attr, value}` | 只接受 schema 里 `editable` 的属性（artist/alias/MBID/link） |
| `link` | `{file, url}` | 校验"必须是具体收录页"（搜索页直接 400） |
| `submit`/`fix`/`meta` | `{title, score, note, contact, file}` | 投稿/纠错：留档 + （有数字时）直接生成曲谱入库 |

统一返回 `{ok, id, state, committed, refresh, refresh_msg}`；`/api/health` 另外暴露
`token_required`、`upstream`、`og`（分享卡索引条数 —— 用它一眼看出"每谱注入是不是还活着"）。

## 四、部署拓扑（两份产物、三种运行位置）

| 位置 | 是什么 | 能力 |
|---|---|---|
| **`https://jianpu-db.org/`** | Cloudflare Worker（正式站） | 检索/卡片/谱页 + **投稿/补属性/补标签**（写路径）+ 每谱注入 |
| `https://jianpu-db.github.io/` | GitHub Pages（只读镜像） | 只读；写操作提示"去正式站" |
| `http://127.0.0.1:8770/` | 本机服务 | 开发/内网；**唯一真正写盘 + git commit 的地方** |
| 便携包 `jianpu-server/` | 同一份服务的可搬走版本 | 换台电脑：Python + （可选）cloudflared 即可 |

关键点：
* **`assets.run_worker_first`** 必须设 —— 否则 `/s/<id>` 这种"靠 SPA 回退"的请求在资源层就被接走，
  Worker 不被调用（每谱注入静默失效，页面却照常打开）。
* **写路径不落边缘**：Worker 只做反代 + 注入 `X-Token`；真正写盘/提交在作者本机（`cloudflared` 隧道出去），
  这样"数据库/仓库在本地"与"边缘读性能"两件事不互相牵制。

## 五、瓶颈与下一步（面试常问"哪里慢/怎么扩"）

| 环节 | 实测 | 瓶颈性质 | 计划 |
|---|---|---|---|
| 转写 | 每份中位 38–61 s（59–95 份/小时） | GPU 单卡、模型串行 | 批处理/并行多进程；只在新增量上跑（幂等台账 + `--rescue`） |
| 浏览器检索 | 索引就绪 204 ms；查询中位 **151 ms**（p90 198 ms） | 全库代价匹配 O(曲数×查询长) | 倒排剪枝（先按 n-gram 候选）、或把匹配核心 **Rust→Wasm**（阶段 E） |
| 索引下发 | 5.12 MB gz | 每次打开页面一次 | 拆冷热索引、PWA Cache Storage 常驻 |
| 写路径 | 依赖隧道与开机 | 本机在线才有写能力 | 可选上云（R2/D1）或改成"队列 + 稍后入库" |

# jianpu-db-mcp

**简谱旋律查歌**的 MCP 服务器：**自带索引、完全离线可用** —— AI 客户端（Claude Desktop、
Claude Code、Cline、Continue…）拿这个包就能查这个曲库，**不依赖任何服务器在线**。

拿一段哼出来的旋律（简谱数字串，如 `316316`）反查是哪首歌。三个工具全是只读，不写任何东西。

- 语言与依赖：**Python 3.10+**；离线只用官方 `mcp` SDK + 标准库（`gzip`/`json`），**没有第三个依赖**；
  远程模式用标准库 `urllib`（`psutil` 可选，只用来打印常驻内存）。
- 传输：**stdio**（MCP 客户端把 `server.py` 当子进程拉起来）。
- 为什么默认离线：线上 `/ws/2/*` 是 Cloudflare Worker 反代到**作者本机**的服务，那台机器一坏
  整条链路就是 `530`。所以这个包把**权威匹配器**（`jianpu2/tools/melody_search.py` 与
  `jptok.py` 的包内 vendored 副本，**算法一字未改**）与**精简语料/索引**一起装进来，进程内直接算。

## 两种模式（一个开关）

| 模式 | 怎么开 | 行为 |
| --- | --- | --- |
| **离线（默认）** | 不设任何 `JIANPU_*` | 读包内 `corpus/index.jsonl.gz`，进程内算，**一个请求都不发**（HTTP 那一层根本不会被 import） |
| 远程（可选） | 设了 `JIANPU_BASE`（如 `https://jianpu-db.org`） | 打 `$JIANPU_BASE/ws/2/*`，保留每秒 1 次的退避与 530/1016 的人话报错 |
| 强制离线 | `JIANPU_MODE=offline` | 设了基址也走离线（自检用；`JIANPU_MODE=remote` 则相反） |

判据只有一处：`jianpu_mcp/__init__.py` 的 `resolve_mode()`。

## 装

```bash
cd D:\Documents_D\_analysis\jianpu-db-mcp
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt        # 只有 mcp 一个包
```

Linux / macOS 把 `.venv\Scripts\python` 换成 `.venv/bin/python`。
包内 `.venv` 已经装好了（Python 3.14.6 + mcp 2.3.0），直接用它跑也行。

## 接进客户端（可粘贴）

**离线（默认，什么都不用配）**：

```json
{
  "mcpServers": {
    "jianpu-db": {
      "command": "D:\\Documents_D\\_analysis\\jianpu-db-mcp\\.venv\\Scripts\\python.exe",
      "args": ["D:\\Documents_D\\_analysis\\jianpu-db-mcp\\server.py"]
    }
  }
}
```

**要远程**就再加一段 `env`（那台机器不在线时会明确报"接口不可用"，不会假装查不到）：

```json
      "env": { "JIANPU_BASE": "https://jianpu-db.org" }
```

- **Claude Desktop**：`%APPDATA%\Claude\claude_desktop_config.json`（macOS：
  `~/Library/Application Support/Claude/claude_desktop_config.json`），改完**完全退出再启动**。
- **Claude Code**：`claude mcp add jianpu-db -- "D:\...\python.exe" "D:\...\server.py"`，或写进 `.mcp.json`。
- **其它客户端**：找"添加 MCP 服务器"里能填 `command`/`args`/`env` 的界面，照上面填。

## 三个工具

| 工具 | 参数 | 返回什么 |
| --- | --- | --- |
| `search_melody` | `digits`（必填，音高数字 **1-7**，≤200 字符）、`fuzzy`（0/1/2，默认 0）、`limit`（默认 5，上限 100） | `count`/`count_exact`/`returned` + `songs[]`：每条 `id`、`title`、`file`、`artists`、`tags`、`status`、`source`、`n_notes`、`sections`，以及 `match`（`diff` 差几个音、`sec_cn` 命中段落、`bar_from`/`bar_to` 小节号、`seg` 对上的那一段原文） |
| `get_song` | `id`（必填：`source` 如 `qupu123-268596`，或文件名主干如 `0932`）、`inc`（默认 `["artists","tags","links","sections"]`）、`score_chars`（默认 0＝不带谱面） | `id`、`title`、`file`、`source`/`site`、`status`、`n_notes`、`bars`、`artists`、`tags`、`sections`，`score_chars>0` 时另给 `score` 片段与 `score_chars_total` |
| `stats` | 无 | `songs`/`notes`/`groups`/`sources`/`status` + `corpus`（源文件路径、行数、sha256、mtime）+ `index`（**本进程实测**的载入耗时与常驻内存）+ `source_of_stats` |

返回一律是**精简 JSON 文本**（UTF-8、缩进 2、不转义中文）：整份 `score` 有 1~2 KB、检索一次
几十 KB，全塞进上下文又贵又吵，所以默认只给"判断是不是这首"要用的字段。
离线与远程**同形状**（只多一个 `mode` 字段说明当前走哪条路）。

## 包内结构

```
jianpu-db-mcp/
├─ server.py                     MCP 入口（三工具 + 模式开关）
├─ rebuild_index.py              一条命令重建精简语料与索引
├─ call_tools.py                 用官方 SDK 真调一遍三个工具（证据脚本）
├─ requirements.txt              只有一个依赖：mcp
├─ jianpu_mcp/
│  ├─ __init__.py                版本、路径、resolve_mode()
│  ├─ melody_search_vendored.py  权威匹配器的包内副本（算法一字未改）
│  ├─ jptok_vendored.py          简谱 token 唯一实现的包内副本（代码逐字节相同）
│  ├─ slim_fields.py             精简语料/索引的字段清单与行构造
│  ├─ offline.py                 离线层：载入包内索引 -> search/get_song/stats
│  └─ http_api.py                远程层：/ws/2/* 客户端（退避 + 人话报错）
├─ corpus/
│  ├─ slim.jsonl.gz              精简语料（7.17 MB / 28,695 行）
│  ├─ index.jsonl.gz             运行索引（8.71 MB / 28,695 行）—— 离线模式只读这一份
│  └─ manifest.json              可复核的记账（2.9 KB）
├─ tools/
│  ├─ sync_vendor.py             从 jianpu2 仓库重抄两份 vendored 副本
│  └─ check_vendor_sync.py       逐函数 AST 比对，断言"算法一字未改"
└─ tests/
   ├─ offline_assert.py          离线断言（网络出口堵死 / 强制离线 / 反面对照）
   ├─ _no_net.py                 给上面那个用的 socket 守卫
   └─ parity_check.py            与 melody_search.py 逐字段对拍
```

**精简语料**（`slim.jsonl.gz`）只留检索与展示要用的字段：
`file`、`title`、`artist`、`tag`、`source`、`status`、`n_notes`、`bars`、`sections`、`score`。
（`tag` 是**必须**留的：匹配器的排名里 `hot`（歌手/标签在语料里的谱数）就来自它，砍掉会让排序
与权威匹配器不一致。其余字段——`transcriber`/`usertag`/`confidence`/`conf_p10`/`beats_per_bar`/
`MBID`/`link`/`alias`——一律不抄，省下来的体积不大，但"这份语料到底有什么"变得一眼能看清。）

**运行索引**（`index.jsonl.gz`）在精简语料之上加了匹配器算出来的
`digits`/`sec`/`pop`/`hot`、展平的 `site`、段落元信息 `sections`（`{subtitle, name_cn, n_notes}`，
**同一份谱面不存两遍**）与展示要用的 `score`。

**manifest.json**（可复核、可重建）：源 `data.jsonl` 的路径/大小/mtime/sha256/行数；两份产物的
sha256、字节数、行数与字段清单；曲数/音符数/同名组数/各来源/各状态；匹配器口径
（`TOKVER`/`OK_STATUS`/`MAX_GAP`/`ALIGN_MAX`/`OCC_CAP`/段落权重）；以及自检结果（逐字段对拍
28,695 首、不一致 0 处）。

## 更新语料

```bash
py -3.13 rebuild_index.py --verify      # 一条命令：重建两份产物 + manifest，并逐字段自检
```

- 数据源：`--data` > `JIANPU_DB`（语料仓库目录）> 包内 `corpus/data.jsonl` > `../jianpu-db/data.jsonl`
  > `D:\Documents_D\jianpu-db\data.jsonl`。
- 实测（本机，28,695 行 / 54.1 MB 语料）：写精简语料约 40 秒（要切 640 万个音）、建索引
  0.4 秒（借到匹配器在 `~/.cache` 的数字索引）/ 冷启约 38 秒、自检 42 秒；**峰值内存约 200 MB**。
  `--no-cache` 强制从头切。
- 自检不过就**不写 manifest.json**（产物不可信）并以非零码退出。

## 自己验一遍

```bash
.venv\Scripts\python.exe tests\offline_assert.py     # 离线断言（三个场景 + 真实返回）
.venv\Scripts\python.exe tests\parity_check.py       # 与 melody_search.py 逐字段对拍
py -3.13 tools\check_vendor_sync.py                  # 断言 vendored 副本算法一字未改
.venv\Scripts\python.exe call_tools.py               # 真起 MCP 客户端调三个工具
.venv\Scripts\python.exe server.py --selftest        # 不接传输，直接跑三个工具
.venv\Scripts\python.exe server.py --preload         # 打印"载入耗时 + 常驻内存"
```

实测数字（本机，2026-10-11）：

| 项 | 值 |
| --- | --- |
| 包总大小 | 83.2 MB（含 `.venv` 67.0 MB） |
| 不含 `.venv` | 16.2 MB（其中 `corpus/` 15.89 MB、源码与文档约 0.15 MB） |
| 精简语料 | 7,521,190 B（7.17 MB）/ 28,695 行 |
| 运行索引 | 9,137,747 B（8.71 MB）/ 28,695 行 |
| 首次载入索引 | **0.54~0.60 s**（gzip + 逐行 JSON，不做分词） |
| 常驻内存 | **约 193~195 MB**（Python 3.14 + 28,695 行索引） |
| 查询耗时 | 精确命中 0.03~0.2 s；`fuzzy=1` 约 1.5 s；`fuzzy=2` 约 1.7~2.0 s（全库 28,695 首） |
| 一致性对拍 | 10 条查询（含 `fuzzy=1/2`、宽查询、带分隔符）+ 2 条单条实体，**逐字段一致** |

## 已知边界（照实说）

- **`id` 会重**：语料里 2,407 个 `id` 对应多行（如 `source=unknown` 有 6 行、`jianpucn-52295`
  有 7 行）——`get_song(id)` 返回**第一条**（与站点 `/ws/2/song/<id>` 同口径）。要精确定位请用
  `file`（`search_melody` 的结果里有）。
- **离线没有这几项**：`links`/`transcriber`/`alias`/`musicbrainz_work_id`。它们在站点那边来自
  `scores/<file>.txt` 的头部，`data.jsonl` 里根本没有（`link` 字段实测几乎全是空）。
- **`count` 的口径**：离线照抄站点 `/ws/2/` —— 交给匹配器 `min(1000, max(limit, 120))` 条，
  取到的条数没顶到就是精确的（`count_exact=true`），顶到了就照实说"至少这么多"。
  它**不等于** `melody_search.py --top N` 的条数（那是"只取前 N 名"）。
- **宽查询会等几秒**：`123`/`7` 这类命中上千首的查询要全库扫一遍（约 0.2~0.3 s），
  `fuzzy=2` 的长串约 2 s；这与直接跑匹配器 CLI 同量级（CLI 还要多花约 0.8 s 载入它的缓存）。
- **语料新鲜度靠 manifest**：`stats()` 里的 `corpus.sha256`/`lines` 与 `index.sha256` 就是这把尺子；
  语料更新后没重跑 `rebuild_index.py` 的话，索引还是旧的那一份（不会"看着像新的"）。
- **远程模式的 `stats()` 可能读到旧的 `stats.json`**：站点仓库里那份 CI 产物（本机实测
  `11876` 首）比现在的语料旧，所以远程 `stats()` 的数字会比离线的小 —— 那是它读的文件旧，
  不是离线算错了（离线读的是包内 manifest，与索引同源）。

## vendored 副本怎么同步

包内 `jianpu_mcp/melody_search_vendored.py` 与 `jptok_vendored.py` 是 jianpu2 仓库那两个文件的
副本，**运行时不许 import 包外文件**。与上游的差异只有三处（文件头也写着）：文件头说明、
`HERE/PKG/DB/DATA` 的默认路径、`jptok` 的 import 方式。上游一改就重抄一遍：

```bash
py -3.13 tools\sync_vendor.py            # 重抄（锚点对不上会报错，不会静默抄错）
py -3.13 tools\check_vendor_sync.py      # 逐函数 AST 比对 + 常量取值比对
```

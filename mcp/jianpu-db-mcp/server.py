# -*- coding: utf-8 -*-
"""简谱旋律查歌的 MCP 服务器 —— **默认自带索引、完全离线**，也可选走 HTTP。

    py -3.13 server.py                          # 默认：离线（读包内 corpus/index.jsonl.gz，不联网）
    JIANPU_BASE=https://jianpu-db.org py -3.13 server.py     # 可选：远程（走 /ws/2/*）
    JIANPU_MODE=offline JIANPU_BASE=... py -3.13 server.py   # 设了基址也强制离线（自检用）

三个工具（描述里都写清了"什么时候用、参数怎么填、返回哪些字段"）：

  * `search_melody(digits, fuzzy=0, limit=5)` —— **拿一段旋律查是哪首歌**，返回精简的候选列表
  * `get_song(id, inc=None, score_chars=0)`   —— 按 id 取单首的元信息（默认**不带**整份谱面）
  * `stats()`                                 —— 曲库规模（离线读包内 manifest，不联网）

## 为什么默认离线

线上 `/ws/2/*` 是 Cloudflare Worker 反代到**作者本机**的服务 —— 那台机器坏了之后整条链路
就是 `530`。所以这个包把**权威匹配器的算法**（`jianpu2/tools/melody_search.py` + `jptok.py`，
包内 vendored 副本，算法一字未改）与**精简语料/索引**一起装在包里：进程内直接算，
不发一个请求，`JIANPU_BASE` 没设就永远不会去 import HTTP 那一层。

只依赖官方 `mcp` SDK 与标准库（HTTP 用 `urllib`，gzip 用 `gzip`），**没有第三个依赖**。

⚠ 工具描述写成模块级常量再 `description=` 传进去（而不是写成函数 docstring）：本进程里
  描述要带"当前模式"，而 **f-string 不是 docstring**（`__doc__` 会是空串，客户端只看到空描述）。
"""
from __future__ import annotations

import sys

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from jianpu_mcp import VERSION, resolve_mode

MODE, BASE = resolve_mode()
_OFFLINE = MODE == "offline"

if _OFFLINE:
    from jianpu_mcp import offline as _impl                       # 只有标准库 + 包内索引
    _CORPUS = _impl.CORPUS
    _MODE_NOTE = "**离线模式**：直接在本机读包内索引并计算，**不联网、不发任何请求**。"
else:
    from jianpu_mcp.http_api import RemoteApi as _RemoteApi       # 只有这时才 import HTTP 这一层
    _API = _RemoteApi(BASE)
    _MODE_NOTE = (f"**远程模式**：打 `{BASE}/ws/2/*`（由 `JIANPU_BASE` 打开）——"
                  f"那台机器不在线时接口会 530/不可用，本服务器会翻成人话；"
                  f"想离线就直接把 `JIANPU_BASE` 去掉。")

DESC_SEARCH = f"""拿一段简谱旋律数字串，查曲库里是哪首歌（**只读**）。

{_MODE_NOTE}

什么时候用：用户哼了一句、或给出一串数字谱（如 `316316`、`5 5 6 5 3 2 1`），想知道是哪首歌。

参数：
  * `digits`：旋律数字串，只认音高数字 **1-7**（简谱的哆来咪…）。空格、`|`、逗号只是给人看的
    分隔（与网页端口径一致：分隔符不进匹配）。最多 200 个字符。
  * `fuzzy`：允许几个音对不上。`0`（默认）要求完全一致；`1` 或 `2` 容忍 1~2 个音不同。上限 2。
  * `limit`：最多返回几条，默认 5，上限 100。

返回（**精简**，不带整份谱面）：`count`/`count_exact`（命中总数，以及这个数是不是精确的）、
`returned`、`songs[]` —— 每条含 `id`、`title`、`file`（谱面文件名）、`artists`、`tags`、`status`、
`source`、`n_notes`、`sections`（段落名与音符数），以及 `match`（`diff` 差几个音、`sec_cn` 命中段落、
`bar_from`/`bar_to` 小节号、`seg` 对上的那一段原文）。
"查不到"是空列表；"参数不对/索引不在"才会回一句中文说明（不编造空结果）。
"""

DESC_GET = f"""按 id 取**一首**谱的元信息（**只读**）。id 来自 `search_melody` 的结果。

{_MODE_NOTE}

参数：
  * `id`：两种写法都认 —— `source`（如 `qupu123-268596`）或文件名主干（如 `0932`）。
  * `inc`：要展开的关联数据，可选 `artists`、`tags`、`links`、`sections`、`score`；
    不传就是 `["artists","tags","links","sections"]`。
  * `score_chars`：要不要带简谱谱面、带多少字符（默认 0＝不带）。只在真要分析旋律时才给值。

返回：`id`、`title`、`file`、`source`/`site`、`status`、`n_notes`、`bars`、`artists`、`tags`、
`sections`（`subtitle`/`name_cn`/`n_notes`），以及 `score_chars>0` 时的 `score` 片段与
`score_chars_total`。离线模式下 `links`/`transcriber`/`alias`/`musicbrainz_work_id` **不在**
精简语料里（那几项在站点那边来自 `scores/<file>.txt` 的头部），返回里会照实说明。
"""

DESC_STATS = f"""这个曲库有多大、各来源各多少首（**只读，无参数**）。

{_MODE_NOTE}

什么时候用：用户问"库里有多少首歌""收了多少谱"，或者 `search_melody` 报错后想确认
是"索引/接口有问题"还是"真的没有这首歌"。

返回：`songs`（曲目数）、`notes`（音符数）、`groups`（同名多版本归组后的组数）、
`sources`（各原谱站条数）、`status`（各状态条数）、`corpus`（源 `data.jsonl` 的路径、行数、
sha256、mtime）、`index`（索引行数与**本进程实测**的载入耗时/常驻内存）、
`source_of_stats`（这个数是从哪儿来的）。
"""

mcp = MCPServer(
    name="jianpu-db",
    version=VERSION,
    title="简谱旋律查歌（jianpu-db）",
    instructions=(
        f"中文简谱曲库的只读检索（模式：{'离线，自带索引，不需要网络' if _OFFLINE else '远程 ' + BASE}）。\n"
        "拿一段哼出来的旋律（音高数字 1-7，如 316316）查是哪首歌，用 search_melody；\n"
        "已知某一首的 id（如 qupu123-268596 或文件名主干）要取元信息，用 get_song；\n"
        "想知道曲库规模，用 stats。\n"
        + ("三个工具都只读包内索引，**不需要网络**；结果与 `melody_search.py --json` 同源。"
           if _OFFLINE else
           "接口限流是每 IP 每秒 1 次，本服务器已按 Retry-After 自动退避一次；"
           "仍然失败时它是**接口不可用**，不是\"查不到这首歌\"。")
    ),
)


def _search(digits: str, fuzzy: int, limit: int) -> str:
    if _OFFLINE:
        return _CORPUS.search(digits, fuzzy, limit)
    return _API.search_melody(digits, fuzzy, limit)


def _one(id: str, inc: list[str] | None, score_chars: int) -> str:
    if _OFFLINE:
        return _CORPUS.get_song(id, inc, score_chars)
    return _API.get_song(id, inc, score_chars)


def _stats() -> str:
    if _OFFLINE:
        return _CORPUS.stats()
    return _API.stats()


# ══════════════════════════════════════════════════════════════════════════════
# 工具 ①：按旋律查歌
# ══════════════════════════════════════════════════════════════════════════════
@mcp.tool(
    title="按旋律查歌", description=DESC_SEARCH,
    annotations=ToolAnnotations(title="按旋律查歌", read_only_hint=True,
                                open_world_hint=not _OFFLINE),
)
def search_melody(digits: str, fuzzy: int = 0, limit: int = 5) -> str:
    """拿一段简谱旋律数字串，查曲库里是哪首歌（只读，不写任何东西）。描述见 DESC_SEARCH。"""
    return _search(digits, fuzzy, limit)


# ══════════════════════════════════════════════════════════════════════════════
# 工具 ②：取单首
# ══════════════════════════════════════════════════════════════════════════════
@mcp.tool(
    title="取某一首的详情", description=DESC_GET,
    annotations=ToolAnnotations(title="取某一首的详情", read_only_hint=True,
                                open_world_hint=not _OFFLINE),
)
def get_song(id: str, inc: list[str] | None = None, score_chars: int = 0) -> str:
    """按 id 取一首谱的元信息（只读）。描述见 DESC_GET。"""
    return _one(id, inc, score_chars)


# ══════════════════════════════════════════════════════════════════════════════
# 工具 ③：曲库规模
# ══════════════════════════════════════════════════════════════════════════════
@mcp.tool(
    title="曲库规模", description=DESC_STATS,
    annotations=ToolAnnotations(title="曲库规模", read_only_hint=True,
                                open_world_hint=not _OFFLINE),
)
def stats() -> str:
    """这个曲库有多大、各来源各多少首（只读，无参数）。描述见 DESC_STATS。"""
    return _stats()


def main() -> None:
    if "--version" in sys.argv:
        print(f"jianpu-db-mcp {VERSION}（模式={MODE}"
              + (f"，base={BASE}" if BASE else "，离线：包内索引") + "）")
        return
    if "--preload" in sys.argv:
        # 自检/实测用：把索引先载进来，把"载入耗时 + 常驻内存"打出来（不打网络）
        if _OFFLINE:
            _CORPUS.load()
            print(f"模式=offline 载入={_CORPUS.load_seconds}s 常驻={_CORPUS.load_rss_mb}MB "
                  f"行数={len(_CORPUS.rows)} 索引={_CORPUS.index_path}", file=sys.stderr)
        else:
            print(f"模式=remote base={BASE}（远程模式不载入本地索引）", file=sys.stderr)
        return
    if "--selftest" in sys.argv:
        # 不接 MCP 传输，直接把三个工具跑一遍（离线模式下它不联网；远程模式下会真打接口）
        for name, args in (("search_melody", {"digits": "316316", "limit": 3}),
                           ("get_song", {"id": "fysongs-47259"}),
                           ("stats", {})):
            print(f"── {name}({args})")
            print({"search_melody": search_melody, "get_song": get_song,
                   "stats": stats}[name](**args))
        return
    print(f"jianpu-db-mcp {VERSION} 就绪（模式={'offline（自带索引，不联网）' if _OFFLINE else 'remote ' + BASE}，stdio）",
          file=sys.stderr)
    mcp.run("stdio")


if __name__ == "__main__":
    main()

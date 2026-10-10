"""`/ws/2/*` 的 **OpenAPI 契约**（Pydantic v2 模型）—— 只描述形状，不参与业务。

## 为什么单开一个模块

`app/api.py` 里那条 `/ws/2/{rest:path}` 是**一条兜底路由**接住三个端点（`/ws/2/` 根、检索、
单条实体）。兜底路由的代价是**文档为零**：`app.openapi()` 只能看见一个 `rest: str` 路径参数
和一句"返回 JSON"，第三方照着它生成不出客户端。真正的形状藏在 `app/search_api.py` 的返回
字典里，而那个模块**是标准库版与 FastAPI 版共用的**，不能为了文档去 import pydantic
（标准库版刻意不依赖第三方包）。

所以契约放这里：**只描述形状**（字段名、类型、哪些字段可缺、枚举取值、示例），
业务口径一个字都不搬过来 —— 想改行为仍然只改 `app/search_api.py`。
`api.py` 在路由上挂 `response_model=` / `responses=`，`app.openapi()` 就能产出**可生成客户端**
的文档；两份后端返回值不变。

## 一处刻意的宽松

`response_model` 会**过滤掉模型里没声明的字段**。这一层宁可宽松：`SongEntity` 允许额外字段
（`extra="allow"`），形状对不上时**不至于把真数据吃掉**（`/api/*` 那边前端只认固定字段，
这里给 AI 与第三方客户端用，多一个字段比少一个字段危险得多）。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ErrorBody", "ArtistCredit", "Tag", "Link", "Section", "SongMatch", "SongEntity",
    "SearchEnvelope", "Ws2Docs", "Ws2Mcp", "Ws2Root", "IncName",
    "SCHEMA_NOTES", "SEARCH_EXAMPLE", "LOOKUP_EXAMPLE", "ROOT_EXAMPLE",
]

IncName = Literal["artists", "tags", "links", "sections", "score"]
"""`inc=` 认的值（不认识的值**被忽略**并在响应头 `X-Unknown-Inc` 里列出，不报错）。"""

SCHEMA_NOTES = (
    "本服务的契约说明（写进 OpenAPI 的 `description`，让生成的客户端也知道口径）：\n\n"
    "* **只读**：没有写端点。\n"
    "* **限流**：每 IP 每秒 1 次；超出回 `503` + `Retry-After: 1`（按它退避一次再重试）。\n"
    "* **错误体**：一律 `{\"error\": \"…\"}`（MusicBrainz 的形状），与本站 `/api/*` 那套 "
    "`{\"ok\": false, \"err\": …}` **有意不同**。\n"
    "* **请带 User-Agent**：能识别调用方的名字与主页，例如 "
    "`my-client/1.0 (+https://example.org/)`。\n"
    "* `count` 与 `count_exact`：宽查询时 `count` 可能只是\"至少这么多\"，"
    "`count_exact=false` 表示服务只数到那里。\n"
    "* `/ws/2/song/<id>` 的 `id`：语料的 `source`（如 `qupu123-268596`），"
    "或 `scores/` 里的文件名主干（如 `水手`）。\n"
    "* 线上 `/ws/2/*` 由 Cloudflare Worker 反代到**作者本机**的服务；本机不在线时该路由不可用"
    "（`/openapi.json`、`/docs`、`/llms.txt` 同理，见仓库根的 `llms.txt`）。\n"
)


# ══════════════════════════════════════════════════════════════════════════════
# 错误体（`search_api.ws2_error`）
# ══════════════════════════════════════════════════════════════════════════════
class ErrorBody(BaseModel):
    """`/ws/2/*` 的错误体。MusicBrainz 风格：只有一个 `error` 键。"""

    model_config = ConfigDict(json_schema_extra={"example": {"error": "缺 query（要查的旋律数字串，如 query=316316）"}})

    error: str = Field(..., description="人话错误说明（中文）。状态码含义：400 参数不对 / 404 没有这一条 "
                                        "/ 429 本站另一套限流 / 500 服务内部出错 / 503 限流或语料不可用")


# ══════════════════════════════════════════════════════════════════════════════
# 实体（`search_api.entity_of`）
# ══════════════════════════════════════════════════════════════════════════════
class ArtistCredit(BaseModel):
    """`artist-credit[]`：MusicBrainz 的写法，这里只有名字。"""

    name: str = Field(..., description="歌手名（如 `邓丽君`）")


class Tag(BaseModel):
    """标签：`tag=`（按 tags.json 派生）与人工补的 `usertag=` 合并去重后的结果。"""

    name: str = Field(..., description="标签名（如 `分类/儿歌`、`民歌`）")


class Link(BaseModel):
    """收录页：这一首在**原谱站那一页**的确切地址（不是搜索页）。"""

    type: Literal["source-page"] = Field("source-page", description="关联类型，目前只有 `source-page`")
    url: str = Field(..., description="收录页 URL")


class Section(BaseModel):
    """分段（主歌/副歌…）：`sections[]` 摊平后的样子。"""

    subtitle: str = Field(..., description="段名（原谱里的 `subtitle`，没有就叫 `score`）")
    name_cn: str = Field("", description="段名的中文标签（`sec_label` 算出来的，如 `副歌`）")
    n_notes: int = Field(0, description="这一段的音符数（与 `n_notes` 同一份 token 口径）")
    score: str = Field("", description="这一段的简谱数字串（原样，不改写不重排）")


class SongMatch(BaseModel):
    """**只在检索结果里**出现：这一条是"怎么命中的"（差分与位置）。"""

    model_config = ConfigDict(extra="allow")

    diff: int | None = Field(None, description="允许的几个音对不上（`fuzzy=0` 时恒为 0）")
    pos: int | None = Field(None, description="命中片段在整首谱里的起始 token 位置（从 0 数）")
    sec: str | None = Field(None, description="命中所在段落的原名（如 `chorus`）")
    sec_cn: str | None = Field(None, description="命中所在段落的中文标签（如 `副歌`）")
    bar_from: int | None = Field(None, description="命中起点所在小节（从 1 数）")
    bar_to: int | None = Field(None, description="命中终点所在小节")
    seg: str | None = Field(None, description="命中的那一段数字串（把查询对齐到谱面上的结果）")


class SongEntity(BaseModel):
    """一首谱（**一支曲**）。检索结果与单条实体是**同一个形状**，检索结果多一个 `match`。"""

    # `extra="allow"`：见模块 docstring，形状对不上时不吃掉真数据。
    # `populate_by_name` + 两个 alias：语料里那个键**字面上带连字符**（`artist-credit`），
    # 而 Python 属性名不能带连字符 —— 于是属性叫 `artist_credit`，JSON 里仍然是
    # `artist-credit`（`serialization_alias` 与 `validation_alias` 一起兜住两边）。
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str = Field(..., description="主键：语料的 `source`（如 `qupu123-268596`）；没有 source 时是 "
                                     "`f-<文件名主干>`")
    musicbrainz_work_id: str = Field("", description="该曲在 MusicBrainz 的 **work**（作品，不是录音）ID；"
                                                     "没有就是空串")
    title: str = Field("", description="曲名")
    file: str = Field("", description="曲谱文件名（`scores/` 下的裸文件名）")
    source: str = Field("", description="出处：`<原谱站>-<站内 id>`")
    site: str = Field("", description="原谱站代号（`source` 的前半段，如 `qupu123`）")
    status: str = Field("", description="`ok`=人工校对过，`ocr`=图片机器转写，空=未知")
    transcriber: str = Field("", description="转写者（`jianpu2-auto`=流水线转的）")
    alias: list[str] = Field(default_factory=list, description="别名（同一首歌的别的叫法）")
    n_notes: int = Field(0, description="整首音符数")
    bars: list[int] = Field(default_factory=list, description="每小节的音符数")
    score: str = Field("", description="整首的简谱数字串（原样；**可能很大**，取单条时才有意义）")
    sections: list[Section] = Field(default_factory=list, description="分段；`inc` 给不给都返回（谱本身的数据）")
    artist_credit: list[ArtistCredit] = Field(default_factory=list,
                                              validation_alias="artist-credit",
                                              serialization_alias="artist-credit",
                                              description="MusicBrainz 写法的歌手列表（JSON 键名带连字符）")
    artists: list[str] = Field(default_factory=list, description="歌手名的简写列表（与 `artist-credit` 同源）")
    tags: list[Tag] = Field(default_factory=list,
                            description="标签；**只有 `inc=tags` 时才填**，否则是空数组")
    links: list[Link] = Field(default_factory=list,
                              description="收录页；**只有 `inc=links` 时才出现**这个键")
    match: SongMatch | None = Field(None, description="**只有检索结果**才有：这一条怎么命中的")


# ══════════════════════════════════════════════════════════════════════════════
# 信封（`search_api.ws2_search`）
# ══════════════════════════════════════════════════════════════════════════════
class SearchEnvelope(BaseModel):
    """`GET /ws/2/song` 的信封（MusicBrainz 的 `{created,count,offset,…}` 形状）。"""

    model_config = ConfigDict(extra="allow")

    created: str = Field(..., description="服务生成这份响应的 UTC 时间（`2026-10-05T12:34:56Z`）")
    count: int = Field(..., description="这次查询取到的命中条数（宽查询时可能只是\"至少这么多\"）")
    offset: int = Field(0, description="本次返回从第几条开始（原样回显请求里的 `offset`）")
    songs: list[SongEntity] = Field(default_factory=list, description="这一页的实体")
    count_exact: bool = Field(True, description="`count` 是否就是精确总命中数（本站**特有**字段，"
                                                "MusicBrainz 没有；宽查询时为 `false`）")


# ══════════════════════════════════════════════════════════════════════════════
# `/ws/2/` API 根（`search_api.ws2_root`）
# ══════════════════════════════════════════════════════════════════════════════
class Ws2Docs(BaseModel):
    """给人/给 AI 的入口（`/ws/2/` 自述里的 `docs`）。**都挂在服务根上，不在 `/ws/2/` 前缀下。**"""

    openapi: str = Field("", description="机器可读契约（OpenAPI 3.1，FastAPI 自动生成）")
    swagger: str = Field("", description="Swagger UI（`/docs`）")
    redoc: str = Field("", description="ReDoc（`/redoc`）")
    llms: str = Field("", description="给 AI 的发现入口（`/llms.txt`，一句话说明 + 基址 + 限流）")
    note: str = Field("", description="这三个由 **FastAPI 版**本机服务提供；线上 `/ws/2/*` 反代到的"
                                      "标准库版上游没有它们（从线上点会 404）")


class Ws2Mcp(BaseModel):
    """MCP（给 AI 客户端直接调用）服务器的位置与怎么装。**服务器本身不在站点仓库里。**"""

    name: str = Field("jianpu-db-mcp", description="服务器/包的短名")
    transport: str = Field("stdio", description="传输方式：`stdio`（本地子进程，MCP 客户端最常用）")
    where: str = Field("", description="代码放在哪儿（未入任何仓库的独立小包）")
    tools: str = Field("", description="它提供哪几个工具（与 OpenAPI 的端点一一对应）")
    install: str = Field("", description="怎么装（一句话）")


class Ws2Root(BaseModel):
    """`GET /ws/2/` —— 服务的自我介绍（实体、参数、限流、示例地址、文档入口）。"""

    model_config = ConfigDict(extra="allow")

    name: str = Field(..., description="服务名（`jianpu-db Web Service`）")
    version: str = Field(..., description="这一层命名空间的版本（现在是 `2`）")
    created: str = Field(..., description="这份自述生成的时间（UTC，带 Z）")
    read_only: bool = Field(True, description="恒为 `true`：这一层没有写端点")
    entities: list[dict[str, Any]] = Field(default_factory=list, description="有哪些实体（目前只有 `song`）")
    parameters: dict[str, str] = Field(default_factory=dict, description="参数说明（`fmt`/`inc`/`query`/`limit`/`offset`）")
    envelope: dict[str, str] = Field(default_factory=dict, description="检索与取单条的响应该长什么样")
    rate_limit: dict[str, str] = Field(default_factory=dict, description="限流规则与 User-Agent 要求")
    sample: str = Field("", description="一条**可点开**的示例检索地址（不是占位符）")
    examples: list[dict[str, str]] = Field(default_factory=list, description="示例地址与它演示什么")
    docs: Ws2Docs = Field(default_factory=Ws2Docs,
                          description="给人看的入口：`openapi`（机器可读契约）、`swagger`（/docs）、"
                                      "`redoc`、以及给 AI 的 `llms`")
    mcp: Ws2Mcp = Field(default_factory=Ws2Mcp,
                        description="MCP（给 AI 客户端直接调用）服务器的位置与怎么装")


# ══════════════════════════════════════════════════════════════════════════════
# OpenAPI 里 `examples=` 用的真实样例（都取自本机实测响应，缩短过）
# ══════════════════════════════════════════════════════════════════════════════
SEARCH_EXAMPLE: dict[str, Any] = {
    "created": "2026-10-05T08:00:00Z",
    "count": 2,
    "offset": 0,
    "count_exact": True,
    "songs": [
        {
            "id": "qupu123-268596",
            "musicbrainz_work_id": "",
            "title": "月亮代表我的心",
            "file": "月亮代表我的心.txt",
            "source": "qupu123-268596",
            "site": "qupu123",
            "status": "ok",
            "transcriber": "jianpu2-auto",
            "alias": [],
            "n_notes": 68,
            "bars": [4, 4, 4],
            "score": "3 5 6 1' 7 5 6 - 3 5 6 1' 7 5 6 -",
            "sections": [{"subtitle": "chorus", "name_cn": "副歌", "n_notes": 16,
                          "score": "3 5 6 1' 7 5 6 -"}],
            "artist-credit": [{"name": "邓丽君"}],
            "artists": ["邓丽君"],
            "tags": [],
            "match": {"diff": 0, "pos": 12, "sec": "chorus", "sec_cn": "副歌",
                      "bar_from": 5, "bar_to": 6, "seg": "3 5 6 1 7 5 6"},
        }
    ],
}

LOOKUP_EXAMPLE: dict[str, Any] = {
    "id": "qupu123-268596",
    "musicbrainz_work_id": "",
    "title": "月亮代表我的心",
    "file": "月亮代表我的心.txt",
    "source": "qupu123-268596",
    "site": "qupu123",
    "status": "ok",
    "transcriber": "jianpu2-auto",
    "alias": [],
    "n_notes": 68,
    "bars": [4, 4, 4],
    "score": "3 5 6 1' 7 5 6 - 3 5 6 1' 7 5 6 -",
    "sections": [{"subtitle": "chorus", "name_cn": "副歌", "n_notes": 16,
                  "score": "3 5 6 1' 7 5 6 -"}],
    "artist-credit": [{"name": "邓丽君"}],
    "artists": ["邓丽君"],
    "tags": [{"name": "流行"}],
    "links": [{"type": "source-page", "url": "https://www.qupu123.com/jipu/p268596.html"}],
}

ROOT_EXAMPLE: dict[str, Any] = {
    "name": "jianpu-db Web Service",
    "version": "2",
    "created": "2026-10-05T08:00:00Z",
    "read_only": True,
    "entities": [{"name": "song", "singular": "song",
                  "endpoints": ["/ws/2/song/<id>", "/ws/2/song"]}],
    "parameters": {"fmt": "只支持 json（默认 json）",
                   "inc": "要额外展开的关联数据：artists、tags、links、sections、score",
                   "query": "检索词：要查的旋律数字串（1-7，如 316316）",
                   "limit": "检索返回条数，默认 25，上限 100",
                   "offset": "检索结果的偏移量（从 0 开始），默认 0"},
    "envelope": {"search": "{created, count, offset, songs[]}",
                 "lookup": "单条实体直接返回对象（不套信封）"},
    "rate_limit": {"policy": "每 IP 每秒 1 次", "over_limit": "HTTP 503 + Retry-After: 1"},
    "sample": "https://jianpu-db.org/ws/2/song?query=316316&limit=5&fmt=json",
    "examples": [{"url": "https://jianpu-db.org/ws/2/song/qupu123-268596?fmt=json&inc=artists+tags+links+sections",
                  "note": "单条实体（含歌手/标签/收录页/分段）"}],
    "docs": {"openapi": "https://jianpu-db.org/openapi.json", "swagger": "https://jianpu-db.org/docs",
             "redoc": "https://jianpu-db.org/redoc", "llms": "https://jianpu-db.org/llms.txt",
             "note": "由 FastAPI 版本机服务提供；线上反代到的标准库版上游没有它们（会 404）"},
    "mcp": {"name": "jianpu-db-mcp", "transport": "stdio",
            "tools": "search_melody / get_song / stats",
            "install": "pip install mcp，然后照它的 README.md 填进 MCP 客户端配置"},
}

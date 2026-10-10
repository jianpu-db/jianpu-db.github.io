"""简谱旋律查歌 —— **FastAPI 版写后端**（C 阶段）。

## 定位：只做"传输 + 校验 + 文档"，业务逻辑一行都不重写

`app/server.py`（Python 标准库那版）里那些**口径**——收录页 URL 怎么判、简谱数字怎么归一化、
`attr` 能改哪些字段、`git commit` 提哪些文件、什么时候触发索引重建——**只有一份**，都从
`server.py` 里 import 过来复用。这一层只负责:

  ① **类型化契约**（Pydantic v2）: 投稿的 4 种 kind 从"字典里摸 key"变成**可校验的模型**；
  ② **自动文档**: `/docs`（Swagger UI）、`/redoc`、`/openapi.json` 由代码生成，前端/第三方照着接；
  ③ **同样的行为**: 状态码、错误文案、CORS、`/img/` 三道闸、`/s/<id>` 注入 —— 与旧服务**逐项对齐**，
     这样灰度切换时"同一套自检对两边都过"。

## 灰度方式

    旧服务  py -3.13 app/server.py 8770        # 标准库版（历史路径，仍在跑）
    新服务  py -3.13 -m uvicorn app.api:app --port 8775     # FastAPI 版
然后拿**同一套**自检（`tools/check_submit.py --live <url>`、`tools/check_live.mjs <url>`、
`tools/check_images_index.py`）分别打两边，逐项比对；一致之后再把隧道/Worker 的
`API_UPSTREAM` 指到新端口。

## 有意收紧的一处（写在这里免得日后当成 bug）

旧版对**未知 kind** 是"当成普通投稿往下走"（会去要曲名、甚至建谱）。新版用 Pydantic 的
discriminated union 校验：未知 kind -> **400 + 明确列出可用 kind**。前端只会发这 5 种
（new/fix/meta/link/tags/attr），所以实际流量不受影响，而这正是"类型化契约"该有的样子。
"""
from __future__ import annotations

import os
import sys
import time
import urllib.parse
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import search_api  # noqa: E402  —— 复用同一份只读检索口径（标准库版也 import 它）
import server  # noqa: E402  —— 复用同一份口径（校验/落库/git/重建索引）
import ws2_schema as ws2  # noqa: E402  —— `/ws/2/*` 的 OpenAPI 契约（只有形状，没有业务）

VERSION = "c1"  # C 阶段第 1 版


# ══════════════════════════════════════════════════════════════════════════════
# Prometheus 指标（D 阶段）
#
# 为什么加它: "投稿有没有在进来""上游还活着吗""语料涨到多少了" —— 以前只能靠
# `/api/health` 一个 JSON 或翻日志。指标化之后，任何 Prometheus/Grafana（或 Cloudflare 的
# 外部抓取）都能直接画出来，也让"可观测性"从"一个健康检查"变成"一条时间序列"。
#
# 三个纪律:
#   ① **标签基数要小**: 请求路径用**归一化模板**（`/s/{id}`、`/img/{rel}`）而不是原样路径 ——
#      否则 1.1 万个 `/s/<id>` 会把时间序列炸成 1.1 万条（Prometheus 最经典的翻车方式）。
#   ② **业务指标比 QPS 值钱**: `jianpu_submissions_total{kind,result}` 直接回答"投稿漏斗哪一环掉了"。
#   ③ **抓取要便宜**: 语料条数这种要读大文件的指标，带 60 s 缓存（否则每次 scrape 都读 21 MB）。
# ══════════════════════════════════════════════════════════════════════════════
REQ_TOTAL = Counter("jianpu_http_requests_total", "HTTP 请求数（按归一化路径与状态码）",
                    ["method", "path", "status"])
REQ_SECONDS = Histogram("jianpu_http_request_seconds", "HTTP 请求耗时（秒）",
                        ["method", "path"],
                        buckets=(0.005, 0.02, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))
SUBMIT_TOTAL = Counter("jianpu_submissions_total", "投稿结果（kind=载荷类型, result=ok|rejected|error）",
                       ["kind", "result"])
CORPUS_SONGS = Gauge("jianpu_corpus_songs", "语料曲目数（读 data.jsonl，60 秒缓存）")
CORPUS_BYTES = Gauge("jianpu_corpus_bytes", "语料 data.jsonl 字节数")
FEEDBACK_FILES = Gauge("jianpu_feedback_files", "留档的投稿份数（feedback/*.json）")
BUILD_INFO = Gauge("jianpu_build_info", "构建信息（value 恒为 1，详情在标签里）",
                   ["version", "impl", "token_required"])
BUILD_INFO.labels(version=VERSION, impl="fastapi", token_required=str(bool(server.TOKEN))).set(1)

_corpus_cache = {"ts": 0.0, "songs": 0.0, "bytes": 0.0}


def _path_template(path: str) -> str:
    """把路径归一化成**低基数**模板（见上面纪律 ①）。"""
    if path.startswith("/s/"):
        return "/s/{id}"
    if path.startswith("/img/"):
        return "/img/{rel}"
    return path


def refresh_corpus_gauges(ttl: float = 60.0) -> None:
    """读 `data.jsonl` 量语料规模（带 TTL 缓存，见纪律 ③）。"""
    import time as _t
    now = _t.time()
    if now - _corpus_cache["ts"] < ttl:
        return
    path = os.path.join(server.DB, "data.jsonl")
    try:
        n = 0
        with open(path, "rb") as f:
            for line in f:
                if line.strip():
                    n += 1
        CORPUS_SONGS.set(n)
        CORPUS_BYTES.set(os.path.getsize(path))
    except OSError:
        CORPUS_SONGS.set(0)
        CORPUS_BYTES.set(0)
    _corpus_cache["ts"] = now


# ══════════════════════════════════════════════════════════════════════════════
# 请求模型（Pydantic v2）—— 投稿的四种载荷，用 discriminated union 一次表达清楚
# ══════════════════════════════════════════════════════════════════════════════
class _Base(BaseModel):
    """所有投稿共有的可选字段（留档用）。"""

    model_config = {"extra": "allow"}      # 旧版会忽略多余字段，这里保持"不因多余字段而拒收"

    note: str = Field("", description="备注（留档，会写进 feedback/*.json）")
    contact: str = Field("", description="联系方式（可选）")


class LinkReq(_Base):
    """「＋ 补收录页」：给某一首补一个**具体收录页**（搜索页会被服务端拒收）。"""

    kind: Literal["link"]
    # ⚠ 这两个字段**故意给空默认值**（而不是必填）: 旧版是让业务层去校验并回
    #   "文件名不合法" / "URL 不合法"，前端与 /docs 都按那套文案来。若在这里就判必填，
    #   文案会变成 Pydantic 的 "Field required" —— 对拍时实测到这条差异，于是改成"模型可空、
    #   口径仍在业务层"。类型化契约的价值在于**说清楚有哪些字段**，不是抢业务层的活儿。
    file: str = Field("", description="scores/ 下的裸文件名，如 `邓丽君_89.txt`")
    url: str = Field("", description="该曲在那一站的具体页面地址")


class TagsReq(_Base):
    """「＋ 补标签」：人工给某一首加标签（`分类/儿歌` 这种既有约定也直接写）。"""

    kind: Literal["tags"]
    file: str = ""
    tags: list[str] | str = Field(default_factory=list,
                                       description="标签列表，或逗号分隔的字符串")


class AttrReq(_Base):
    """「＋ 补属性」：只允许改 schema 里 `editable` 的属性（artist/alias/MBID/link…）。"""

    kind: Literal["attr"]
    file: str = ""
    attr: str = ""
    value: str = Field("", description="新值（空串 = 清空）")


class ScoreReq(_Base):
    """投稿/纠错/补充：带简谱数字时直接生成一份曲谱进 `scores/`（反馈即入库）。"""

    kind: Literal["new", "fix", "meta"] = "new"
    title: str = ""
    score: str = Field("", description="简谱数字串，如 `5 5 6 5 3 2 1`")
    file: str = Field("", description="kind=fix 时：要改的是哪一份（裸文件名）")


SubmitReq = Annotated[
    LinkReq | TagsReq | AttrReq | ScoreReq,
    Field(discriminator="kind", description="按 `kind` 区分的四种投稿载荷"),
]


# ══════════════════════════════════════════════════════════════════════════════
# 应用
# ══════════════════════════════════════════════════════════════════════════════
app = FastAPI(
    title="简谱旋律查歌 · 写后端",
    version=VERSION,
    summary="投稿 / 补收录页 / 补标签 / 补属性 —— 校验 + 落库 + git commit + 触发索引重建",
    description=(
        "这是**作者本机**那台写服务（读路径在 Cloudflare 边缘，见 `worker/index.ts`）。\n\n"
        "鉴权：配了 `JPSUBMIT_TOKEN` 时所有 `/api/*` 写请求必须带 `X-Token`（Worker 会自动注入）。\n\n"
        "口径说明：所有业务规则复用 `app/server.py`（收录页判定、简谱归一化、可改字段白名单、"
        "git 提交范围、索引重建时机），本层只做传输与校验。\n\n"
        "**只读的旋律检索 Web Service 在 `/ws/2/` 那一组**（MusicBrainz 风格）：`/ws/2/song?query=…` "
        "按旋律数字串查歌、`/ws/2/song/<id>` 取单条、`/ws/2/` 是自述。\n\n"
        + ws2.SCHEMA_NOTES
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    # `servers`: 生成的客户端要知道"往哪儿发请求"。线上 `/ws/2/*` 走 Cloudflare Worker
    # 反代到本机服务，对外就是这一个域名；本机调试时把 base 换成 `http://127.0.0.1:8770`
    # 即可（或者用 MCP 服务器的 `JIANPU_BASE`，见 `_analysis/jianpu-db-mcp/README.md`）。
    servers=[{"url": "https://jianpu-db.org", "description": "线上（Cloudflare Worker 反代到作者本机的服务）"}],
    openapi_tags=[
        {"name": "ws2", "description":
         "**只读旋律检索 Web Service**（MusicBrainz 风格，`/ws/2/`）。给第三方与 AI 用："
         "按旋律数字串查歌、取单条实体。口径只有一份，在 `app/search_api.py`（标准库版与 "
         "FastAPI 版共用）。" + ws2.SCHEMA_NOTES},
        {"name": "default", "description": "本站自己的接口与静态资源（`/api/*`、`/img/*`）。"},
    ],
)

# CORS: 与旧版一致（`*`，且**不看请求有没有 Origin** —— 旧版是无条件加的，Worker→本机那一跳
# 也没有 Origin 头）。CORSMiddleware 只在"确实跨域"时才加头，对拍时实测到这条差异 -> 自己补一层。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Token"],
    max_age=86400,
)


@app.middleware("http")
async def gate(request: Request, call_next):
    """`/api/*` 的**前置闸**：体积 -> 口令 -> （端点里再做 JSON 解析与字段校验），最后补 CORS。

    ⚠ 为什么必须写在中间件里，而不是写成 FastAPI 依赖（第一版就是依赖，被对拍抓到）:
      FastAPI 在解依赖**之前**会先解析请求体。于是"口令不对 + body 是坏 JSON"时，
      新版回 `400 参数不对 —— JSON decode error`，旧版回 `403 需要 X-Token` ——
      **鉴权顺序**变了（这在安全语义上是实打实的差异，不只是文案）。
      中间件在路由/解析之前跑，顺序才与旧版一致: 量 body 大小 -> 看 X-Token -> json.loads。
      顺带一个好处: 这两类早期错误也能带上 CORS 头（异常处理器产出的响应不经过我原来的"CORS 补头"位置）。

    旧版对应实现见 `app/server.py` 的 `do_POST`（先判 int(Content-Length)，再比 X-Token，然后才 json.loads）。
    """
    path = request.url.path
    api = path.startswith("/api/")
    ws2 = path == "/ws/2" or path.startswith("/ws/2/")     # MusicBrainz 风格那份也要带 CORS
    tmpl = _path_template(path)
    t0 = time.perf_counter()
    # ⚠ 闸门**只管 POST /api/submit** —— 旧版的 `do_GET` 里 `/api/health` 是**敞开的**（不带口令也能查）。
    #   第一版把闸套在所有 `/api/*` 上，于是带口令的实例连 `/api/health` 都回 403 ——
    #   那会让 Worker 的 `probeUpstream` 把"上游活着"判成"挂了"。对拍第二次救了我。
    if request.method == "POST" and path == "/api/submit":
        # ① 体积闸（与旧版同文案）
        clen = request.headers.get("content-length")
        if clen is None or not clen.isdigit() or int(clen) <= 0 or int(clen) > 200_000:
            REQ_TOTAL.labels(request.method, tmpl, "400").inc()
            REQ_SECONDS.labels(request.method, tmpl).observe(time.perf_counter() - t0)
            return _api_json(400, {"ok": False, "err": "body 太大或为空"})
        # ② 口令闸
        if server.TOKEN and request.headers.get("x-token") != server.TOKEN:
            REQ_TOTAL.labels(request.method, tmpl, "403").inc()
            REQ_SECONDS.labels(request.method, tmpl).observe(time.perf_counter() - t0)
            return _api_json(403, {"ok": False, "err": "需要 X-Token"})
    resp = await call_next(request)
    REQ_TOTAL.labels(request.method, tmpl, str(resp.status_code)).inc()
    REQ_SECONDS.labels(request.method, tmpl).observe(time.perf_counter() - t0)
    # ③ CORS: 只加在 JSON 响应上（旧版写在 `_json()` 里；它对"未知 /api 路径"发的是裸 404，没有这个头）
    if (api or ws2) and "json" in (resp.headers.get("content-type") or "").lower():
        resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Prometheus 文本格式指标（D 阶段）。

    * 与 `/api/health` 一样**不需要口令**：它只暴露聚合计数（请求数、耗时直方图、投稿结果、
      语料规模），没有任何内容/密钥；反正服务只监听本机/内网。
    * 用官方客户端的 `generate_latest()`，所以格式一定合规；自带进程指标
      （`process_*`、`python_gc_*`、`python_info`）。
    * 想被 Prometheus 抓: `scrape_configs: [{job: jianpu-write, static_configs: [{targets: ['127.0.0.1:8770']}]}]`。
    """
    refresh_corpus_gauges()
    try:
        FEEDBACK_FILES.set(len(os.listdir(server.FEEDBACK)))
    except OSError:
        FEEDBACK_FILES.set(0)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


def _json(code: int, obj: dict) -> JSONResponse:
    return JSONResponse(status_code=code, content=obj)


def _api_json(code: int, obj: dict) -> JSONResponse:
    """中间件里**早退**的响应：自己带 CORS 头。

    ⚠ 对拍第三次抓到的差异就在这儿：`旧=400 新=400` 但 `CORS * vs -`。早退走不到 `call_next`
      之后的"补 CORS"那段，必须自己加。少了这个头浏览器**读不到响应体** —— 前端看到的是
      "网络错误"，而不是那句中文提示（旧版 `_json()` 是无条件带的，所以必须对齐）。
    """
    r = _json(code, obj)
    r.headers["Access-Control-Allow-Origin"] = "*"
    return r


@app.exception_handler(HTTPException)
async def _http_exc(_req: Request, exc: HTTPException) -> JSONResponse:
    """把 HTTPException 也变成旧服务那套 `{"ok": false, "err": …}` 形状（前端只认这个）。"""
    detail = exc.detail
    err = detail.get("err") if isinstance(detail, dict) else str(detail)
    return _json(exc.status_code, {"ok": False, "err": err})


@app.exception_handler(RequestValidationError)
async def _val_exc(_req: Request, exc: RequestValidationError) -> JSONResponse:
    """校验失败 -> 400 + 人话（默认是 422 + 一坨 JSON，前端与旧服务都不认）。

    ⚠ "不是合法 JSON" 这句要**照旧版原样**回（对拍时实测到差异）：旧版是 `json.loads` 失败就回它，
      而 FastAPI 会把它包成 `1: JSON decode error`。前端与日志都按旧文案来，所以这里特判。
    """
    errs = exc.errors()
    for e in errs:
        if e.get("type") in ("json_invalid", "value_error.jsondecode"):
            return _json(400, {"ok": False, "err": "不是合法 JSON"})
    msgs = []
    for e in errs[:3]:
        loc = ".".join(str(x) for x in e.get("loc", ()) if x != "body")
        msgs.append(f"{loc or 'body'}: {e.get('msg', '')}")
    return _json(400, {"ok": False, "err": "参数不对 —— " + "；".join(msgs)})


@app.exception_handler(Exception)
async def _any_exc(_req: Request, exc: Exception) -> JSONResponse:
    """兜底：与旧服务同文（`类型: 消息`），别把 traceback 泄给调用方。"""
    return _json(500, {"ok": False, "err": f"{type(exc).__name__}: {exc}"})


@app.options("/api/{rest:path}", include_in_schema=False)
async def preflight(rest: str) -> Response:
    """CORS 预检：与旧版**逐项一致**（204 + 同样的三个头）。

    为什么不用 CORSMiddleware 的默认行为: 它对 `OPTIONS /api/submit` 会返回 405/200 且头不同
    —— 对拍实测到 `旧=204 新=405`。浏览器预检要的是 204 + 允许的方法/头，这里照旧版写死。
    """
    return _preflight_response()


@app.options("/ws/2/{rest:path}", include_in_schema=False)
async def preflight_ws2(rest: str) -> Response:
    """`/ws/2/*` 的 CORS 预检：**同一份 204 应答**。

    标准库版 `app/server.py` 的 `do_OPTIONS` 是**不看路径**的（任何路径都回这套头），
    所以这里也照那个行为给 `/ws/2/*` 一份 —— 否则浏览器里的跨域调用会"两个后端一个能预检一个不能"。
    """
    return _preflight_response()


def _preflight_response() -> Response:
    """预检应答（204 + 三个头 + Content-Length: 0）—— `/api/*` 与 `/ws/2/*` 共用同一份。"""
    return Response(status_code=204, headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,X-Token",
        "Access-Control-Allow-Methods": "POST,GET,OPTIONS",
        "Content-Length": "0",
    })



@app.get("/api/health", summary="健康检查（含上游自身信息）")
async def health() -> JSONResponse:
    """读路径不依赖这台机器；写路径依赖。`token_required=false` 说明本机没设口令（只能内网用）。"""
    n = len(os.listdir(server.FEEDBACK)) if os.path.isdir(server.FEEDBACK) else 0
    return _json(200, {"ok": True, "repo": server.DB, "feedback_count": n,
                       "token_required": bool(server.TOKEN),
                       "images": [os.path.basename(r) for r in server.IMG_ROOTS],
                       "server": "fastapi", "version": VERSION})


@app.get("/llms.txt", summary="给 AI 的发现入口（站点根那份 `llms.txt` 原样发出）",
         response_class=PlainTextResponse, include_in_schema=False)
def llms() -> Response:
    """为什么由服务发一遍：这份文件在**仓库根**（与 `robots.txt` 同处，静态托管直接就能取到），
    而本机自检常常只起着 `app/api.py`、没有静态托管 —— 服务把它原样发出来，
    `http://127.0.0.1:8775/llms.txt` 才点得开，`/ws/2/` 自述里那条链接才不会骗人。

    没这份文件就 503 + 一句人话（不 500、不空 200）。
    """
    path = os.path.join(server.ROOT, "llms.txt")
    try:
        with open(path, encoding="utf-8") as f:
            return PlainTextResponse(content=f.read(), media_type="text/plain; charset=utf-8")
    except OSError as e:
        return PlainTextResponse(status_code=503,
                                 content=f"这台机器上没有 {path}（{type(e).__name__}）",
                                 media_type="text/plain; charset=utf-8")


@app.get("/api/search", summary="只读旋律检索（按旋律数字串查歌）")
async def search_endpoint(request: Request) -> JSONResponse:
    """`?q=<旋律>&fuzzy=0&top=20` —— 只读，不需要 `X-Token`（中间件那道闸只管 `POST /api/submit`）。

    口径（参数校验 / 缓存 / 限流 / 输出的 JSON 形状）**只有一份**，在 `app/search_api.py`，
    与标准库版 `app/server.py` 共用，所以两边的状态码与文案不会走偏。
    """
    code, out, headers = search_api.handle(request.url.query,
                                           request.client.host if request.client else "")
    return JSONResponse(status_code=code, content=out, headers=headers)


# ── 只读 Web Service `/ws/2/*`（MusicBrainz 风格）─────────────────────────────
#
# ⚠ 这里**有意的冗余**，写在代码里免得日后被当成"两套逻辑":
#   * **路由挂三条**（`/ws/2/`、`/ws/2/song`、`/ws/2/song/{id}`）而不是原来那一条
#     `@app.get("/ws/2/{rest:path}")` 兜底 —— 因为兜底路由在 `app.openapi()` 里
#     **什么形状都长不出来**（只有一个 `rest: str` 路径参数），第三方照着它生成不出客户端。
#     三条路由各自挂 `response_model` / `responses=`，`/openapi.json` 才是能读懂、能生成客户端的文档。
#   * **只借用参数校验，不碰业务**：三条的**请求参数名**与 `/ws/2/` 自述、与 `llms.txt` 完全一致
#     （`query`/`q`、`fuzzy`、`limit`、`offset`、`fmt`、`inc`），但真正执行仍然只调
#     `search_api.ws2_handle`（与标准库版 `app/server.py` 同一个函数）—— 所以文案、状态码、
#     限流一个字节都不会与标准库版走偏。
#     未声明的多余查询串**原样透传**给 `ws2_handle`，它才是唯一的判据。
_ERR = {"model": ws2.ErrorBody}
_RATE_LIMITED = {
    "model": ws2.ErrorBody,
    "description": "限流：每 IP 每秒 1 次。**按 `Retry-After` 退避一次再重试**。",
    "headers": {
        "Retry-After": {"description": "过几秒再来（现在是 1）", "schema": {"type": "string"}},
    },
    "content": {"application/json": {"example": {"error": "请求太快：每 IP 每秒最多 1 次，1 秒后再试"}}},
}
# 给"能生成客户端的文档"再加一层：错误体与成功体都带**真实样例**。
# 只有 schema 时，Swagger UI 里那两条错误响应是空壳、示例值是自动编的占位符；
# 挂上 `example` 之后，`/docs` 里点开就能看见真形状（生成的客户端测试也能照着写）。
_ERR_EXAMPLE = {
    "application/json": {"example": {"error": "缺 query（要查的旋律数字串，如 query=316316）"}}
}
_COMMON_RESPONSES = {
    400: dict(_ERR, description="参数不对（缺 query / query 太长 / 认不出音高数字 / `fmt` 不是 json）",
              content=_ERR_EXAMPLE),
    404: dict(_ERR, description="没有这个实体或端点", content=_ERR_EXAMPLE),
    429: dict(_ERR, description="撞上了本站 `/api/*` 那套每分钟限流（与 `/ws/2/*` 的每秒 1 次不是同一套）",
              content=_ERR_EXAMPLE),
    500: dict(_ERR, description="服务内部出错（错误体里是 `类型: 消息`，不含 traceback）"),
    503: _RATE_LIMITED,
}
_INC_HEADER = {
    "X-Unknown-Inc": {"description": "被忽略的 `inc` 值（逗号分隔；都在支持表里时不出现这个头）",
                      "schema": {"type": "string"}},
}

_WS2_QUERY_DOC = (
    "要查的**旋律数字串**：只认音高数字 1-7（如 `316316 = 咪哆啦咪哆啦`）。空格/`|`/换行分隔多段，"
    "各段在同一首谱里**按顺序**找。上限 200 个字符。别名参数 `q=` 也认（老调用方/顺手敲）。"
)
_WS2_FMT_DOC = "表示格式。本服务**只有 JSON 一种**：不传就是 json，传别的值回 400（也用请求头 `Accept` 判）。"
# ⚠ 下面三个整数参数的**文档口径与校验位置**（别"顺手把校验挪上来"）：
#   唯一判据是 `search_api.ws2_search`（它自己回 400 + `{"error": …}` + 中文文案）。这一层
#   只负责"把收到的字符串原样交给它"，同时**照着它那句文案把形状写进文档**：类型是整数、
#   范围/枚举写在 schema 里、默认值写**整数**（`type: integer` 配 `default: "25"` 是自相矛盾的
#   文档 —— 生成的客户端会把默认值当字符串用）。签名上标 `str` 不是懒，是为了**不自造 422**：
#   声明成 `int` 时 FastAPI 会抢在业务层前把 `limit=abc` 判成
#   `{"ok": false, "err": "参数不对 —— query.limit: …"}`，形状与文案都变了
#   （两份后端的逐项对拍 `check_parity_legacy_vs_fastapi.py` 当场抓到这一条）。
_WS2_FUZZY_DOC = ("允许几处音对不上：`0` 精确（默认）、`1`/`2` 容忍 1 或 2 个音不同。"
                  "给大了会在全库上逐窗口比对，所以上限就是 2。")
_WS2_LIMIT_DOC = (f"返回条数，默认 {search_api.WS2_LIMIT_DEFAULT}，上限 {search_api.WS2_LIMIT_MAX}"
                  "（超了钳到上限，不报错）。")
_WS2_OFFSET_DOC = "从第几条开始（从 0 数），默认 0。翻页时 `count` 不会变小。"


# ──────────────────────────────────────────────────────────────────────────────
# 参数声明的两个小工具（`_q_str` / `_q_int`）
#
# ⚠ 为什么整数参数在签名上是 `str`、文档里却是整数（**别"顺手改成 int"**，实测踩过）：
#   `search_api.ws2_search` 是参数的**唯一判据** —— 它自己回
#   `400 {"error": "limit 要是一个非负整数（给的是 abc）"}`。一旦签名声明成 `int`，
#   FastAPI 会**抢在业务层之前**校验，错误体变成
#   `{"ok": false, "err": "参数不对 —— query.limit: Input should be a valid integer…"}` ——
#   形状与文案都变了（标准库版与 FastAPI 版当场不一致，逐项对拍 `check_parity_legacy_vs_fastapi.py`
#   立刻抓到）。所以：**照收字符串、交业务层判**，但用 `json_schema_extra` 把文档写成整数
#   （类型/范围/枚举/整数默认值都对，第三方照着生成的客户端一样是对的）。
#   `alias=` 让"请求里叫什么"与文档一致（参数名带下划线只是为了不与 Python 关键字/内建撞）。
# ──────────────────────────────────────────────────────────────────────────────
def _q_str(alias: str, default: str, desc: str, examples: list[str] | None = None) -> Any:
    return Query(default, alias=alias, description=desc, examples=examples)


def _q_int(alias: str, default: str, desc: str, *, minimum: int = 0,
           maximum: int | None = None, enum: list[int] | None = None,
           examples: list[int] | None = None) -> Any:
    """文档里是整数、值是字符串（见上面那段说明）的参数声明。

    ⚠ 这里给出的默认值就是**字符串** `"25"` —— 它进的是"签名默认值"那一格，也是业务层比较的东西
    （`isdigit()`）。FastAPI 会把它原样写进 `…"type": "integer", "default": "25"`（自相矛盾），
    所以 `openapi()` 里还有一道 `_fix_int_defaults()` 专门把这类默认值改回整数（见那个函数）。
    """
    schema: dict[str, Any] = {"type": "integer", "minimum": minimum}
    if maximum is not None:
        schema["maximum"] = maximum
    if enum is not None:
        schema["enum"] = enum
    return Query(default, alias=alias, description=desc,
                 examples=examples or [int(default)], json_schema_extra=schema)


def _fix_int_defaults(node: Any) -> None:
    """把"整数型参数"的默认值从字符串改回整数（**就地**改 `app.openapi()` 的产物）。

    为什么需要这一道：FastAPI 生成参数 schema 时会把**签名默认值**写进 `default`，
    而这里为了"不让 FastAPI 抢业务层的校验"（见上面那段说明）签名默认值必须是字符串 ——
    于是文档里长出 `{"type": "integer", "default": "25"}`。它不违反 OpenAPI 规范
    （`openapi-spec-validator` 会放行），但会把生成的客户端带偏；这一层只修这一处。
    """
    if isinstance(node, dict):
        schema = node.get("schema")
        if (isinstance(schema, dict) and schema.get("type") == "integer"
                and isinstance(schema.get("default"), str)):
            try:
                schema["default"] = int(schema["default"])
            except ValueError:
                pass                       # 真写错的值留给自检去报，不在这儿悄悄吞掉
        for v in node.values():
            _fix_int_defaults(v)
    elif isinstance(node, list):
        for v in node:
            _fix_int_defaults(v)


_orig_openapi = app.openapi


def _openapi() -> dict:
    """`app.openapi()` 的包装：生成之后把整数参数的默认值修对（见 `_fix_int_defaults`）。"""
    spec = _orig_openapi()
    _fix_int_defaults(spec.get("paths") or {})
    return spec


app.openapi = _openapi                        # type: ignore[method-assign]


@app.get("/ws/2/", tags=["ws2"], summary="API 根：服务的自我介绍",
         response_model=ws2.Ws2Root, response_model_exclude_none=True,
         responses={**_COMMON_RESPONSES,
                    200: {"model": ws2.Ws2Root, "description": "服务的自我介绍（实体、参数、限流、示例、文档入口）",
                          "content": {"application/json": {"example": ws2.ROOT_EXAMPLE}}}},
         openapi_extra={"x-human-docs": {"openapi": "/openapi.json", "swagger": "/docs", "redoc": "/redoc"}})
async def ws2_root(request: Request) -> JSONResponse:
    """实体、参数、限流、一条**可点开**的示例地址，以及给人看的入口（`/openapi.json`、`/docs`）。

    「给人看的入口」放在这里是有意的：`/ws/2/*` 是**第三方/AI 最可能先碰到的那一层**，
    而 `/openapi.json` 挂在服务根上（不在 `/ws/2/` 前缀下），从这儿链过去才算找得到。
    """
    return _ws2_response("/ws/2/", request)


@app.get("/ws/2/song", tags=["ws2"], summary="按旋律数字串检索（MusicBrainz 风格信封）",
         response_model=ws2.SearchEnvelope, response_model_exclude_none=True,
         responses={**_COMMON_RESPONSES,
                    200: {"model": ws2.SearchEnvelope, "description": "命中（MusicBrainz 风格信封）",
                          "content": {"application/json": {"example": ws2.SEARCH_EXAMPLE}}}},
         openapi_extra={"x-search-summary": {k: v for k, v in ws2.SEARCH_EXAMPLE["songs"][0].items()
                                             if k in ("id", "title", "n_notes", "match")}})
async def ws2_song_search(
    request: Request,
    _query: str = _q_str("query", "", _WS2_QUERY_DOC, ["316316", "5 5 6 5 3 2 1"]),
    _q: str = _q_str("q", "", "`query` 的别名（两个都空才算缺参数）"),
    _fuzzy: str = _q_int("fuzzy", "0", _WS2_FUZZY_DOC, maximum=2, enum=[0, 1, 2]),
    _limit: str = _q_int("limit", str(search_api.WS2_LIMIT_DEFAULT), _WS2_LIMIT_DOC,
                         maximum=search_api.WS2_LIMIT_MAX, examples=[5, 25]),
    _offset: str = _q_int("offset", "0", _WS2_OFFSET_DOC, examples=[0, 25]),
    _fmt: str = _q_str("fmt", "json", _WS2_FMT_DOC, ["json"]),
) -> JSONResponse:
    """命中的每一条都是完整实体 **+ `match`**（差分与位置）。

    `count` 与 `count_exact`：宽查询（全库上千首命中）时 `count` 只是"至少这么多"，
    `count_exact=false` 表示服务只数到那里 —— 分页时按 `offset`+`limit` 翻，别信 `count` 会变小。

    一条实际响应（截短）见 `songs[].match`：

    ```json
    {"created": "2026-10-05T08:00:00Z", "count": 2, "offset": 0, "count_exact": true,
     "songs": [{"id": "qupu123-268596", "title": "月亮代表我的心",
                "match": {"diff": 0, "sec_cn": "副歌", "bar_from": 5, "bar_to": 6}}]}
    ```
    """
    return _ws2_response("/ws/2/song", request)


@app.get("/ws/2/song/{id}", tags=["ws2"], summary="取单条实体（不套信封）",
         response_model=ws2.SongEntity, response_model_exclude_none=True,
         responses={**_COMMON_RESPONSES,
                    200: {"model": ws2.SongEntity, "description": "这一首的实体（不套信封）",
                          "headers": _INC_HEADER,
                          "content": {"application/json": {"example": ws2.LOOKUP_EXAMPLE}}}},
         openapi_extra={"x-lookup-summary": {k: v for k, v in ws2.LOOKUP_EXAMPLE.items()
                                             if k in ("id", "title", "n_notes", "status")}})
async def ws2_song_lookup(
    id: str,  # noqa: A002 —— 参数名就是 URL 里的那个（`/ws/2/song/{id}`），与 MusicBrainz 一致
    request: Request,
    _fmt: str = _q_str("fmt", "json", _WS2_FMT_DOC, ["json"]),
    _inc: str = _q_str("inc", "", "要**额外展开**的关联数据，多选用 `+`、空格或逗号："
                                  "`artists`、`tags`、`links`、`sections`、`score`。"
                                  "不认识的值**被忽略**（不报错），并在响应头 `X-Unknown-Inc` 里列出。"
                                  "`sections`/`score` 是谱本身的数据，给不给 `inc` 都会返回",
                       ["artists+tags+links+sections"]),
) -> JSONResponse:
    """`id` 是语料的 `source`（如 `qupu123-268596`），也接受 `scores/` 里的文件名主干（如 `水手`）。

    返回的**就是实体本身**（不套 `{created,count,…}` 信封）—— 与 MusicBrainz 的单条查询一致。
    `inc=tags` 才填 `tags`，`inc=links` 才出现 `links`；`sections` 与 `score` 是谱本身的数据，一直给。
    """
    return _ws2_response("/ws/2/song/" + request.path_params.get("id", ""), request)


def _ws2_response(path: str, request: Request, **overrides) -> JSONResponse:
    """把请求交给 `search_api.ws2_handle`（口径只有那一份），只负责搬上 HTTP。

    两个后端（FastAPI 版与标准库版）在这里调的是同一个函数，所以状态码/文案/限流不会走偏。

    `overrides`：路由签名上那些**带类型的参数**（`limit`/`offset`/`fuzzy`），值是 Python 原类型
    （`limit=25`）而不是字符串，**原类型不能直接拼进查询串**（`ws2_search` 里的 `isdigit()` 会当场
    把 `limit=25` 判成非法）。所以这里统一转成字符串再拼；等于默认值的**不写**（省得把
    `limit=25` 显式塞进去，把"没传"变成"传了默认值"）。
    未在签名里声明的参数（`fmt`、`inc`、`q`…）**原样透传** —— `ws2_handle` 才是唯一判据。
    """
    pairs = [(k, v) for k, v in (overrides or {}).items()
             if v is not None and str(v) not in ("", "0", "25")]
    qs = "&".join(f"{urllib.parse.quote(str(k))}={urllib.parse.quote(str(v))}" for k, v in pairs)
    code, out, headers = search_api.ws2_handle(
        path, qs or request.url.query or "",
        request.client.host if request.client else "",
        request.headers.get("accept") or "",
        search_api.base_url_from_host(request.headers.get("host") or ""))
    return JSONResponse(status_code=code, content=out, headers=headers)


@app.get("/ws/2/{rest:path}", include_in_schema=False, tags=["ws2"])
async def ws2_other(rest: str, request: Request) -> JSONResponse:
    """兜底：`/ws/2/` 下的**其它路径**照旧交给同一份口径（它自己会回 404 + 人话）。

    文档里**不列**它（`include_in_schema=False`）—— 上面三条已经把契约说完了，
    再列一条 `rest: str` 的兜底路由只会让生成的客户端以为"这里能吃任意路径"。
    """
    return _ws2_response(request.url.path, request)


@app.post("/api/submit", summary="投稿 / 补收录页 / 补标签 / 补属性")
async def submit(body: SubmitReq, request: Request) -> JSONResponse:
    """四种载荷按 `kind` 区分（见 `/docs` 的 schema）；返回结构与旧服务逐项一致。

    * `kind=link`  -> 写 `link=`（搜索页会被拒收）
    * `kind=tags`  -> 写 `usertag=`，`tag=` 由 tags.json 派生
    * `kind=attr`  -> 只改 schema 里 editable 的属性
    * `kind=new|fix|meta` -> 留档；带简谱数字时直接生成曲谱 + 触发索引重建
    """
    payload = body.model_dump()
    payload["_ip"] = request.client.host if request.client else ""
    kind = str(payload.get("kind") or "new")
    try:
        code, out = server.handle_submit(payload)      # ← 业务口径只有这一份
    except Exception:
        SUBMIT_TOTAL.labels(kind, "error").inc()
        raise
    SUBMIT_TOTAL.labels(kind, "ok" if code == 200 else "rejected").inc()
    return _json(code, out)


# ── 原图：与旧服务同样三道闸（复用它给的 resolve_img，不重写判定）────────────────
@app.get("/img/{rel:path}", summary="原图（R2 缺失时的回源目标）")
async def image(rel: str) -> Response:
    full = server.resolve_img(rel)
    if not full:
        return Response(status_code=404, content=b"")
    ext = os.path.splitext(full)[1].lower()
    with open(full, "rb") as f:
        data = f.read()
    return Response(content=data, media_type=server.MIME.get(ext, "application/octet-stream"),
                    headers={"Cache-Control": server.IMG_CACHE})


# ── 静态资源与「每谱一页」────────────────────────────────────────────────────────
@app.get("/{path:path}", include_in_schema=False)
async def static_or_tune(path: str, request: Request) -> Response:
    """静态文件；`/s/<id>` 走服务端 meta 注入（爬虫不跑 JS，与 Worker 行为对齐）。"""
    full = server.resolve("/" + path if path else "/")
    if not full or not os.path.isfile(full):
        return Response(status_code=404, content=b"")
    ext = os.path.splitext(full)[1].lower()
    if ext == ".html" and (path == "s" or path.startswith("s/")):
        try:
            with open(full, encoding="utf-8") as f:
                html = f.read()
            tid = server.unquote(path[2:].strip("/"))
            origin = "http://" + (request.headers.get("host") or "")
            html = server.inject_song_meta(html, server.og_meta(tid), origin, tid)
            return HTMLResponse(content=html, headers={"Cache-Control": "no-cache"})
        except Exception:
            pass                                       # 任何问题都退回原始文件
    ctype = server.MIME.get(ext, "application/octet-stream")
    with open(full, "rb") as f:
        data = f.read()
    if ext == ".txt":
        return PlainTextResponse(content=data.decode("utf-8", "replace"),
                                 media_type="text/plain; charset=utf-8")
    return Response(content=data, media_type=ctype)


def main() -> None:
    """`py -3.13 app/api.py [端口]` —— 与旧服务同样的启动方式，方便灰度并存。"""
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8775
    import uvicorn
    print(f"FastAPI 写后端 -> http://{server.HOST}:{port}/  文档 /docs")
    uvicorn.run(app, host=server.HOST, port=port, log_level="warning")


if __name__ == "__main__":
    main()

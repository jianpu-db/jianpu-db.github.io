# -*- coding: utf-8 -*-
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

import io
import os
import sys
from typing import Annotated, Literal, Union

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server  # noqa: E402  —— 复用同一份口径（校验/落库/git/重建索引）

VERSION = "c1"  # C 阶段第 1 版


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
    tags: Union[list[str], str] = Field(default_factory=list,
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
    Union[LinkReq, TagsReq, AttrReq, ScoreReq],
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
        "git 提交范围、索引重建时机），本层只做传输与校验。"
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
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
    # ⚠ 闸门**只管 POST /api/submit** —— 旧版的 `do_GET` 里 `/api/health` 是**敞开的**（不带口令也能查）。
    #   第一版把闸套在所有 `/api/*` 上，于是带口令的实例连 `/api/health` 都回 403 ——
    #   那会让 Worker 的 `probeUpstream` 把"上游活着"判成"挂了"。对拍第二次救了我。
    if request.method == "POST" and path == "/api/submit":
        # ① 体积闸（与旧版同文案）
        clen = request.headers.get("content-length")
        if clen is None or not clen.isdigit() or int(clen) <= 0 or int(clen) > 200_000:
            return _api_json(400, {"ok": False, "err": "body 太大或为空"})
        # ② 口令闸
        if server.TOKEN and request.headers.get("x-token") != server.TOKEN:
            return _api_json(403, {"ok": False, "err": "需要 X-Token"})
    resp = await call_next(request)
    # ③ CORS: 只加在 JSON 响应上（旧版写在 `_json()` 里；它对"未知 /api 路径"发的是裸 404，没有这个头）
    if api and "json" in (resp.headers.get("content-type") or "").lower():
        resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


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
    code, out = server.handle_submit(payload)          # ← 业务口径只有这一份
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
            with io.open(full, encoding="utf-8") as f:
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

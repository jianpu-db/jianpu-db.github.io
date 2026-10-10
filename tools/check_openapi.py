"""校验 `openapi.json` 合法、可被消费，并**真的按契约打一次接口**。

    python tools/check_openapi.py                    # 校验仓库根的 openapi.json
    python tools/check_openapi.py --spec openapi.json --base http://127.0.0.1:8775 --live

两段各司其职：

  ① **规格校验**：优先用 `openapi-spec-validator`（装了就跑，把它的版本与结论打在输出里）；
     没装就退回**自带的那组不变量检查**（本文件里的 `structural_checks`）——
     两者都过才算"合法"：前者是标准本身的校验，后者盯的是本仓库必须成立的那几条
     （每条 `/ws/2/*` 都要有 200/400/404/503、503 必须带 `Retry-After`、错误体必须是
     `{"error": …}`、`$ref` 必须指得到、`operationId` 不能重名……）。

  ② **当作客户端消费一次**：从 `paths` + `servers` 现场拼出**可调用的函数**
     （`build_client`），再挑一条真请求打过去 —— 这一步证明的是"这份 schema 里的
     `servers`/`paths`/参数足够拼出请求"，而不是"我手写的 URL 恰好能通"。
     带 `--live` 才发请求；限流是每 IP 每秒 1 次，所以每条之间等 1.1 秒。

退出码：0 全过；1 有失败。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = "jianpu-db-openapi-check/1.0 (+https://jianpu-db.org/ws/2/)"
GAP = 1.15          # 每 IP 每秒 1 次：两条真请求之间必须真等

PASS = 0
FAIL = 0


def ck(cond: bool, msg: str, extra: str = "") -> bool:
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✓ " + msg + (f"：{extra}" if extra else ""))
    else:
        FAIL += 1
        print("  ✗ " + msg + (f"：{extra}" if extra else ""))
    return bool(cond)


# ══════════════════════════════════════════════════════════════════════════════
# ① 规格校验
# ══════════════════════════════════════════════════════════════════════════════
def refs_in(node: Any, out: list[str]) -> None:
    """把整份 JSON 里的 `$ref` 全收集出来（不管它在哪一层）。"""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str):
                out.append(v)
            else:
                refs_in(v, out)
    elif isinstance(node, list):
        for v in node:
            refs_in(v, out)


def structural_checks(spec: dict) -> None:
    """本仓库必须成立的那几条（与标准校验互补，不重复它做的事）。"""
    print("\n① 规格校验")
    ver = str(spec.get("openapi") or "")
    ck(re.fullmatch(r"3\.1\.\d+", ver) is not None, "openapi 版本是 3.1.x", ver)
    ck(bool(spec.get("info", {}).get("title")) and bool(spec.get("info", {}).get("version")),
       "info 有 title/version", json.dumps(spec.get("info", {}), ensure_ascii=False)[:80])
    ck(bool(spec.get("servers")), "有 servers（生成的客户端才知道往哪儿发）",
       str([s.get("url") for s in spec.get("servers") or []]))

    paths = spec.get("paths") or {}
    ws2 = [p for p in paths if p.startswith("/ws/2")]
    ck(set(ws2) >= {"/ws/2/", "/ws/2/song", "/ws/2/song/{id}"},
       "/ws/2/ 三条都在 schema 里", "、".join(sorted(ws2)))
    ck(all("{" not in p or p.endswith("}") for p in ws2), "路径模板里没有多余的空参数")

    for p in sorted(ws2):
        for method, op in (paths[p] or {}).items():
            if method not in ("get", "post", "put", "delete", "patch", "head", "options"):
                continue
            resp = op.get("responses") or {}
            ck(all(str(c) in resp for c in (200, 400, 404, 500, 503)),
               f"{method.upper()} {p} 声明了 200/400/404/500/503",
               "、".join(sorted(resp)))
            ck(any("example" in (mt or {}) for mt in
                   ((resp.get("200") or {}).get("content") or {}).values()),
               f"{method.upper()} {p} 的成功响应带 example",
               "（/docs 里点开就能看见真形状）")
            r503 = resp.get("503") or {}
            ck("Retry-After" in (r503.get("headers") or {}),
               f"{method.upper()} {p} 的 503 声明了 Retry-After 头",
               "调用方据此退避")
            err = resp.get("400") or {}
            sch = (((err.get("content") or {}).get("application/json") or {}).get("schema") or {})
            ck(sch.get("$ref", "").endswith("/ErrorBody"),
               f"{method.upper()} {p} 的 400 指向 ErrorBody", str(sch.get("$ref")))
            # `/ws/2/`（API 根）本来就**没有参数**（它是个自述），所以只对带 `{` 或明显是
            # 检索/查询的那两条要求 `parameters`；否则这条断言只是在逼文档长出一个空数组。
            if p != "/ws/2/":
                ck("parameters" in op, f"{method.upper()} {p} 声明了 parameters")

    eb = (spec.get("components", {}).get("schemas") or {}).get("ErrorBody") or {}
    props = eb.get("properties") or {}
    ck(list(props) == ["error"] and props.get("error", {}).get("type") == "string",
       "ErrorBody 就是 `{error: string}`（MusicBrainz 形状）", json.dumps(props, ensure_ascii=False)[:70])

    ids = [op.get("operationId") for p in paths for op in (paths[p] or {}).values()
           if isinstance(op, dict) and op.get("operationId")]
    ck(len(ids) == len(set(ids)), "operationId 不重名（生成的客户端函数名才不会撞）",
       "、".join(sorted(ids)))

    # 参数 schema 自洽：整数型的默认值必须是**整数**。
    # 这条盯的是 FastAPI 的一个已知毛病：它会把"签名默认值"原样写进 `default`，
    # 而 `/ws/2/*` 为了不让 FastAPI 抢业务层的校验（见 `app/api.py` 里 `_q_int` 的说明）
    # 签名默认值只能是字符串 —— 于是文档里会长出 `{"type": "integer", "default": "25"}`。
    # `app.openapi()` 里那道 `_fix_int_defaults()` 负责修；这里负责**发现它没修住**。
    bad_defaults: list[str] = []
    int_params = 0
    for p, item in paths.items():
        for method, op in item.items():
            if not isinstance(op, dict):
                continue
            for param in op.get("parameters") or []:
                sch = param.get("schema") or {}
                if sch.get("type") != "integer" or "default" not in sch:
                    continue
                int_params += 1
                if not isinstance(sch["default"], int) or isinstance(sch["default"], bool):
                    bad_defaults.append(f"{method.upper()} {p} 的 {param.get('name')}={sch['default']!r}")
    ck(not bad_defaults, f"{int_params} 个整数参数的默认值都是整数",
       "、".join(bad_defaults[:4]) or "无")

    refs: list[str] = []
    refs_in(spec, refs)
    bad = [r for r in refs if not r.startswith("#/components/schemas/")
           or r.rsplit("/", 1)[-1] not in (spec.get("components", {}).get("schemas") or {})]
    ck(not bad, f"全部 {len(refs)} 个 $ref 都指得到", "、".join(bad[:3]) or "无悬空引用")


def external_validation(spec: dict) -> None:
    """装了 `openapi-spec-validator` 就用它（标准校验）；没装就说清并**不算失败**。"""
    try:
        import openapi_spec_validator as osv  # noqa: PLC0415
        from openapi_spec_validator import validate  # noqa: PLC0415
    except Exception:                                            # noqa: BLE001
        print("  … 没装 openapi-spec-validator：本轮只跑了自带的不变量检查"
              "（想跑官方校验：pip install openapi-spec-validator 再跑一次这个脚本）")
        return
    try:
        validate(spec)
        ck(True, f"openapi-spec-validator {getattr(osv, '__version__', '?')} 判定：合法")
    except Exception as e:                                       # noqa: BLE001
        ck(False, "openapi-spec-validator 判定：不合法", f"{type(e).__name__}: {e}")


def schema_validate_examples(spec: dict) -> None:
    """用 `jsonschema` 反过来验证：文档里的**样例**必须符合它自己的 schema。

    这条比"schema 合法"更值钱：schema 合法但样例与 schema 打架的文档，生成的客户端会立刻踩坑。

    ⚠ 为什么要先把 `components.schemas` 摊成 `$defs`：schema 里的 `$ref` 写的是
      `#/components/schemas/X`（OpenAPI 的写法），而 `jsonschema` 只认 JSON Schema 的
      `#/$defs/X` —— 直接拿单个子 schema 去校验会报 `PointerToNowhere`。把全部组件搬到
      `$defs` 下、再以这个合成文档为根解析引用，就是最省事又正确的做法。
    """
    try:
        import jsonschema  # noqa: PLC0415
    except Exception:                                              # noqa: BLE001
        print("  … 没装 jsonschema：跳过\"样例 vs schema\"这一步")
        return
    schemas = spec.get("components", {}).get("schemas") or {}
    # OpenAPI 的引用写法是 `#/components/schemas/X`；`jsonschema` 只认 JSON Schema 的
    # `#/$defs/X`。把整棵树里的引用**改写成 $defs 写法**、组件表搬进 `$defs`，
    # 就得到一份自洽的 JSON Schema 文档 —— 这才是"样例 vs schema"能真跑起来的前提。
    defs = json.loads(json.dumps(schemas).replace("#/components/schemas/", "#/$defs/"))
    bundle = {"$defs": defs}
    validator_cls = jsonschema.validators.validator_for(bundle)
    n = 0
    for p, item in (spec.get("paths") or {}).items():
        for method, op in item.items():
            if not isinstance(op, dict):
                continue
            for code, resp in (op.get("responses") or {}).items():
                content = (resp.get("content") or {}).get("application/json") or {}
                if "example" not in content:
                    continue
                ref = ((content.get("schema") or {}).get("$ref") or "").rsplit("/", 1)[-1]
                if ref not in defs:
                    continue
                try:
                    validator_cls({"$ref": f"#/$defs/{ref}", **bundle}).validate(content["example"])
                    n += 1
                except Exception as e:                             # noqa: BLE001
                    ck(False, f"{method.upper()} {p} 的 {code} 样例不符合 {ref}",
                       f"{type(e).__name__}: {str(e)[:90]}")
    if n:
        ck(True, f"{n} 份响应样例都与各自的 schema 相符（jsonschema 实跑）")


# ══════════════════════════════════════════════════════════════════════════════
# ② 把 schema 当客户端用
# ══════════════════════════════════════════════════════════════════════════════
def build_client(spec: dict, base: str | None = None) -> tuple[dict, str]:
    """**从 schema 现场拼出可调用的函数** —— 这就是"能生成客户端"的最小证据。

    返回 `(名称 -> 函数, base)`；函数签名是 `f(**参数)`，参数名与 `in` 位置都取自 schema
    （`path` 参数替换进路径模板，其余按 `query` 拼查询串）。
    """
    base = (base or (spec.get("servers") or [{}])[0].get("url") or "").rstrip("/")
    if not base:
        raise SystemExit("schema 里没有 servers[0].url，也没有 --base：无法拼出请求")
    out: dict[str, Any] = {}
    for path, item in (spec.get("paths") or {}).items():
        for method, op in item.items():
            if method != "get" or not isinstance(op, dict):
                continue
            params = op.get("parameters") or []
            where = {p["name"]: p["in"] for p in params if p.get("name")}

            def make(path: str = path, where: dict = where, op: dict = op):
                def call(**kw: Any) -> tuple[int, dict, dict]:
                    url = base + path
                    qs: list[str] = []
                    for k, v in kw.items():
                        if v is None or v == "":
                            continue
                        if where.get(k) == "path":
                            url = url.replace("{" + k + "}", str(v))
                        else:
                            qs.append(f"{k}={urllib.parse.quote(str(v))}")
                    if qs:
                        url += "?" + "&".join(qs)
                    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                               "Accept": "application/json"})
                    try:
                        with urllib.request.urlopen(req, timeout=30) as r:
                            return r.status, json.loads(r.read().decode("utf-8")), dict(r.headers)
                    except urllib.error.HTTPError as e:
                        raw = e.read().decode("utf-8", "replace")
                        try:
                            body = json.loads(raw)
                        except ValueError:
                            body = {"error": raw[:200]}
                        return e.code, body, dict(e.headers)
                return call
            out[op.get("operationId") or f"{method} {path}"] = make()
    return out, base


def live_calls(client: dict, base: str) -> None:
    print("\n② 当客户端用一次（这份 schema 拼出来的请求，真的打到接口上）")
    print(f"  base={base}")
    search = client["ws2_song_search_ws_2_song_get"]
    lookup = client["ws2_song_lookup_ws_2_song__id__get"]
    root = client["ws2_root_ws_2__get"]

    t0 = time.time()
    code, body, _ = root()
    ck(code == 200 and body.get("name"), "GET /ws/2/ -> 200 且是自述", f"name={body.get('name')} "
       f"v{body.get('version')}（用时 {time.time() - t0:.2f}s）")
    print(f"     docs={json.dumps(body.get('docs') or {}, ensure_ascii=False)[:150]}")

    time.sleep(GAP)
    t1 = time.time()
    code, body, _ = search(query="316316", limit=3, fmt="json")
    songs = body.get("songs") or []
    ck(code == 200 and songs, "GET /ws/2/song?query=316316&limit=3 -> 200 且有命中",
       f"count={body.get('count')} exact={body.get('count_exact')} "
       f"第一条={songs[0].get('title') if songs else '—'}（间隔 {t1 - t0:.2f}s）")
    if songs:
        print("     songs[0]=" + json.dumps({k: songs[0].get(k) for k in
                                            ("id", "title", "n_notes", "status", "match")},
                                           ensure_ascii=False)[:320])

    time.sleep(GAP)
    t2 = time.time()
    sid = (songs[0].get("id") if songs else "qupu123-268596")
    code, body, _ = lookup(id=sid, inc="artists+tags+links+sections", fmt="json")
    ck(code == 200 and body.get("id") == sid, f"GET /ws/2/song/{sid} -> 200 单条实体",
       f"title={body.get('title')} n_notes={body.get('n_notes')} "
       f"sections={len(body.get('sections') or [])}（间隔 {t2 - t1:.2f}s）")
    ck("created" not in body and "songs" not in body, "单条实体不套信封（与 schema 一致）",
       "、".join(list(body)[:6]))


def main() -> int:
    ap = argparse.ArgumentParser(description="校验 OpenAPI 并当客户端用一次")
    ap.add_argument("--spec", default=os.path.join(ROOT, "openapi.json"))
    ap.add_argument("--base", default=None, help="覆盖 servers[0].url（默认用 schema 里那个）")
    ap.add_argument("--live", action="store_true", help="真发请求（限流：每 IP 每秒 1 次，脚本自己等 1.15s）")
    args = ap.parse_args()

    with open(args.spec, encoding="utf-8") as f:
        spec = json.load(f)
    print(f"读入 {args.spec}（{os.path.getsize(args.spec)} 字节）")

    structural_checks(spec)
    external_validation(spec)
    schema_validate_examples(spec)

    client, base = build_client(spec, args.base)
    print(f"\n② 从 schema 生成的可调用函数（{len(client)} 个）")
    for name in client:
        print("  - " + name)
    print(f"  拼出的基址：{base}")

    if args.live:
        live_calls(client, base)
    else:
        print("  … 没带 --live，不发真请求（加上它就跑上面那三条）")

    print(f"\n{'✓ 全过' if not FAIL else '✗ 有失败'}（{PASS} 项通过、{FAIL} 项失败）")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""灰度对拍：**旧版标准库服务 vs FastAPI 版**，同一批请求逐项比对（状态码 / 关键响应头 / 响应体）。

为什么需要它（而不是"跑一遍自检就行"）:
    `tools/check_submit.py --live` 只覆盖"投稿漏斗"那条业务线；换掉传输层时最容易出问题的其实是
    **边角行为** —— 预检 OPTIONS 的状态码、404 的 body、越界路径的拒绝方式、坏 JSON 的文案、
    body 上限、未知 kind 的处理。这些一旦不一致，前端/Worker 的表现就会"有时怪怪的"。

用法:
    py -3.13 tools/check_parity_legacy_vs_fastapi.py --old http://127.0.0.1:8776 --new http://127.0.0.1:8777
    py -3.13 tools/check_parity_legacy_vs_fastapi.py --old ... --new ... --token XXX   # 带 X-Token 再对一遍

约定: **允许有意的差异**写进 ALLOW 白名单（目前只有 health 里的 `server`/`version` 两个字段，
      以及未知 kind 的收紧 —— 见 app/api.py 顶部说明）。
"""
import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

CASES = [
    # (方法, 路径, body, 说明)
    ("GET", "/api/health", None, "健康检查"),
    ("GET", "/", None, "首页"),
    ("GET", "/index.html", None, "首页（显式）"),
    ("GET", "/s/qupu123-307314", None, "每谱一页（meta 注入）"),
    ("GET", "/s/不存在的id", None, "不存在的 id（应退回通用标题）"),
    ("GET", "/static/style.css", None, "静态资源（源码目录里的）"),
    ("GET", "/robots.txt", None, "根目录文件兜底"),
    ("GET", "/data/stats.json", None, "统计文件"),
    ("GET", "/data/does-not-exist.json", None, "缺文件 -> 404"),
    ("GET", "/img/images-prep/../../etc/passwd", None, "越界路径 -> 拒绝"),
    ("GET", "/img/images-prep/x/notimage.txt", None, "非图扩展名 -> 拒绝"),
    ("GET", "/img/", None, "空路径 -> 404"),
    ("OPTIONS", "/api/submit", None, "CORS 预检"),
    ("POST", "/api/submit", b"", "空 body"),
    ("POST", "/api/submit", b"{not json", "坏 JSON"),
    ("POST", "/api/submit", b'{"kind":"link"}', "缺字段（file/url）"),
    ("POST", "/api/submit", b'{"kind":"tags","file":"a.txt","tags":["x"]}', "tags（文件不存在）"),
    ("POST", "/api/submit", b'{"kind":"attr","file":"a.txt","attr":"artist","value":"x"}', "attr（文件不存在）"),
    ("POST", "/api/submit", b'{"kind":"link","file":"a.txt","url":"https://www.qupu123.com/Search?keys=x"}',
     "搜索页 -> 拒收"),
    ("POST", "/api/submit", b'{"kind":"unknown-kind","title":"x"}', "未知 kind（**有意差异**）"),
    ("GET", "/api/nope", None, "不存在的 api -> 404"),
    # 只读检索 API（两份后端共用 app/search_api.py，这里锁住 HTTP 层的行为一致）
    ("GET", "/api/search?q=316%20316%2031656564&top=3", None, "检索：多段查询"),
    ("GET", "/api/search?q=1234567&fuzzy=0&top=5", None, "检索：普通查询"),
    ("GET", "/api/search?q=12345671234567123456", None, "检索：合法但查不到（count=0 仍是 200）"),
    ("GET", "/api/search?q=99999999999999999999", None, "检索：非法数字 -> 400"),
    ("GET", "/api/search", None, "检索：缺 q -> 400"),
    ("GET", "/api/search?q=12&fuzzy=9", None, "检索：fuzzy 非法 -> 400"),
    ("GET", "/api/search?q=12345&top=0", None, "检索：top=0 被钳到 1"),
]


def call(base, method, path, body, token):
    url = base.rstrip("/") + urllib.parse.quote(path, safe="/?&=%")   # 非 ASCII 路径(如 /s/不存在的id)要编码, 否则客户端自己就报错
    req = urllib.request.Request(url, method=method, data=body)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return r.status, dict(r.headers), raw
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
    except Exception as e:                                  # 连接层错误
        return -1, {}, str(e).encode()


def norm_body(status, headers, raw, base="", db=""):
    """把响应体归一到"可比较"的形态：

      * **去掉两边"环境不同"带来的噪声**（这些不是行为差异，是测试装置的差异）：
        - `base`（本机 origin，含端口）：`/s/<id>` 的 canonical/og:url 里写着
          `http://127.0.0.1:8776` vs `:8777` —— 第一次对拍就被这条误报过（正文前 200 字一样，差异在后面）；
        - `db`（隔离语料库路径）：两边各自的隔离库路径不同，而错误文案里会带上它
          （如"找不到 linkurl.py —— JIANPU_DB=D:\\…\\ci-iso1 对吗?"）—— CI 里空语料库时**4 条全部误报**，
          正是这一条让我发现"对拍脚本自己也得对环境做归一化"。
      * JSON: 去掉 health 里那两处**有意差异**的字段（server/version）。
    """
    if base:
        raw = raw.replace(base.rstrip("/").encode(), b"<ORIGIN>")
    if db:
        raw = raw.replace(db.encode(), b"<DB>")
        # Windows 上正文里可能出现转义过的反斜杠（`D:\\path`），一并归一
        raw = raw.replace(db.replace("\\", "\\\\").encode(), b"<DB>")
    ct = (headers.get("Content-Type") or headers.get("content-type") or "").lower()
    if "json" in ct:
        try:
            obj = json.loads(raw.decode("utf-8"))
        except Exception:
            return raw[:200].decode("utf-8", "replace")
        if isinstance(obj, dict):
            obj.pop("server", None)          # FastAPI 版多这两个标记（告示"我是谁"）
            obj.pop("version", None)
        return json.dumps(obj, ensure_ascii=False, sort_keys=True)
    return raw.decode("utf-8", "replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True, help="旧版标准库服务的地址")
    ap.add_argument("--new", required=True, help="FastAPI 版地址")
    ap.add_argument("--token", default="", help="两边都要带的 X-Token（测鉴权路径）")
    a = ap.parse_args()

    # 两边各自的隔离语料库路径（错误文案里会带它，属于"环境差异"而非"行为差异"）
    db_old = db_new = ""
    for srv, which in ((a.old, "old"), (a.new, "new")):
        try:
            with urllib.request.urlopen(srv.rstrip("/") + "/api/health", timeout=10) as r:
                v = json.loads(r.read().decode("utf-8")).get("repo") or ""
        except Exception:
            v = ""
        if which == "old":
            db_old = v
        else:
            db_new = v
    print(f"  环境归一: 旧库={db_old or '(未报告)'} · 新库={db_new or '(未报告)'}")

    same = diff = 0
    print(f"对拍: 旧 {a.old}  vs  新 {a.new}" + (f"  (X-Token={'有' if a.token else '无'})"))
    for method, path, body, note in CASES:
        s1, h1, b1 = call(a.old, method, path, body, a.token)
        s2, h2, b2 = call(a.new, method, path, body, a.token)
        n1 = norm_body(s1, h1, b1, a.old, db_old)
        n2 = norm_body(s2, h2, b2, a.new, db_new)
        cors1 = h1.get("Access-Control-Allow-Origin") or h1.get("access-control-allow-origin") or "-"
        cors2 = h2.get("Access-Control-Allow-Origin") or h2.get("access-control-allow-origin") or "-"
        ok = (s1 == s2) and (n1 == n2) and (cors1 == cors2)
        if path == "/api/health":
            ok = (s1 == s2)                     # health 的字段差异是有意的，只比状态码
        if "未知 kind" in note:
            ok = True                           # 有意收紧：见 app/api.py 顶部
        if ok:
            same += 1
            print(f"  ✓ {method:7} {path:52} {s1}  {note}")
        else:
            diff += 1
            print(f"  ✗ {method:7} {path:52} 旧={s1} 新={s2}  CORS {cors1} vs {cors2}   {note}")
            if n1 != n2:
                print(f"      旧体: {n1[:150]}")
                print(f"      新体: {n2[:150]}")
    print(f"\n一致 {same} / 共 {len(CASES)}" + (f" · **不一致 {diff}**" if diff else " · 全部一致 ✓"))
    return 1 if diff else 0


if __name__ == "__main__":
    sys.exit(main())

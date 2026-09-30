# -*- coding: utf-8 -*-
"""验 `app/server.py` 里那份「每谱一页注入 head」—— 与 `tools/check_og_meta.mjs` 同口径。

为什么要两份测试: 这个功能在**两个地方**各实现了一遍（本机 `app/server.py` 与边缘
`worker/index.js`），口径必须一致（今晚已经栽过两次"本地与线上不一致"：`/robots.txt` 404、
MIME 类型错）。JS 那份用 `check_og_meta.mjs` 盯，这份盯 Python 侧。

用法:  py -3.13 tools/check_og_meta.py
"""
import io
import importlib.util
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_server():
    spec = importlib.util.spec_from_file_location("srv_under_test", os.path.join(ROOT, "app", "server.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)              # 只 import；起服务在 __main__ 里
    return m


def main():
    os.chdir(ROOT)
    m = load_server()
    html = io.open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
    fail = 0

    def ok(c, label):
        nonlocal fail
        print(("✓ " if c else "✗ ") + label)
        if not c:
            fail += 1

    out = m.inject_song_meta(html, ["小城故事", "邓丽君", 317], "http://127.0.0.1:8770", "jianpucn-150657")

    def g(pat, s=None):
        mm = re.search(pat, s if s is not None else out)
        return mm.group(1) if mm else None

    ok(g(r"<title>([^<]*)</title>") == "小城故事（邓丽君） · 简谱 | jianpu-db",
       "title 换成这一首: %s" % g(r"<title>([^<]*)</title>"))
    ok("小城故事（邓丽君）的简谱：317 个音符" in out, "description 带曲名/歌手/音符数")
    ok(g(r'<meta property="og:title" content="([^"]*)"') == "小城故事（邓丽君） · 简谱 | jianpu-db", "og:title 同步")
    ok('og:description" content="小城故事' in out, "og:description 同步")
    ok('twitter:title" content="小城故事' in out, "twitter:title 同步")
    ok('twitter:description" content="小城故事' in out, "twitter:description 同步")
    ok(g(r'<link rel="canonical" href="([^"]*)"') == "http://127.0.0.1:8770/s/jianpucn-150657",
       "canonical 指向这一页: %s" % g(r'<link rel="canonical" href="([^"]*)"'))
    ok(g(r'<meta property="og:url" content="([^"]*)"') == "http://127.0.0.1:8770/s/jianpucn-150657", "og:url 指向这一页")
    img = lambda s: g(r'<meta property="og:image" content="([^"]*)"', s)      # noqa: E731
    ok(img(out) == img(html), "og:image 原样不动（每首没有各自的图）")
    ok(out.split("<body", 1)[1] == html.split("<body", 1)[1], "正文（<body> 之后）逐字节未变")

    # 转义 + **反向引用**：曲名里带 `&` `<` `"` `\1` 时不能出乱子（`re.sub` 的替换串里 `\1` 是危险的）
    tricky = m.inject_song_meta(html, ['A&B <C> "\\1"', 'D"E', 12], "https://x", "y-1")
    ok("<title>A&amp;B &lt;C&gt; &quot;\\1&quot;（D&quot;E） · 简谱 | jianpu-db</title>" in tricky,
       "`\\1` 没被当成反向引用（标题 = 转义后的原文）")

    ok(m.inject_song_meta(html, None, "x", "y") == html, "没有这首 -> 原样返回")
    ok(m.inject_song_meta(html, ["", "a", 1], "x", "y") == html, "曲名空 -> 原样返回")
    ok(m.inject_song_meta("", ["a", "b", 1], "x", "y") == "", "html 为空 -> 原样返回")
    no_origin = m.inject_song_meta(html, ["歌", "", 5], "", "z-1")
    ok(g(r'<link rel="canonical" href="([^"]*)"', no_origin) == g(r'<link rel="canonical" href="([^"]*)"', html),
       "不给 origin 时 canonical 原样不动")

    # og.json 真身（构建产物）：键是 id，值是 [曲名, 歌手, 音符数]
    p = os.path.join(ROOT, "data", "og.json")
    if os.path.exists(p):
        import json
        d = json.load(io.open(p, encoding="utf-8"))
        k = next(iter(d))
        ok(isinstance(d[k], list) and len(d[k]) == 3, "data/og.json 结构正确（%d 首, 例 %s -> %s）" % (len(d), k, d[k]))
    else:
        ok(False, "data/og.json 还没生成（先跑 tools/build_web_data.py）")

    print("\n%s" % ("通过" if fail == 0 else "失败 %d 项" % fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())

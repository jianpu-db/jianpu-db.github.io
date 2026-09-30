# -*- coding: utf-8 -*-
"""把前端那几处 `POST /api/submit` 的**用途与载荷**列出来（读 `static/app.js`，不改任何东西）。

用户问的是"点了增加/修改/投稿这些按钮之后，请求到底发到哪里" —— 这张表要能回答
"哪个按钮 -> 哪个端点 -> 什么载荷 -> 谁处理（本地服务 / 边缘 Worker / 被只读挡住）"。
"""
import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    lines = io.open(os.path.join(ROOT, "static", "app.js"), encoding="utf-8").read().splitlines()
    hits = [i for i, l in enumerate(lines) if "fetch(API +" in l or "fetch(API+" in l]
    print(f"static/app.js 里对 API 的请求共 {len(hits)} 处：\n")
    for i in hits:
        url = re.search(r"fetch\(API \+ '([^']+)'", lines[i])
        end = i
        depth = 0
        for j in range(i, min(len(lines), i + 40)):
            depth += lines[j].count("(") - lines[j].count(")")
            end = j
            if j > i and depth <= 0:
                break
        blob = "\n".join(lines[i:end + 1])
        payload = re.findall(r"(\w+):\s*([^,\n]+)", blob)
        fn = ""
        for j in range(i, max(0, i - 60), -1):
            m = re.match(r"\s*(?:function\s+(\w+)|var\s+(\w+)\s*=\s*function)", lines[j])
            if m:
                fn = m.group(1) or m.group(2)
                break
        # 按钮文案（往上找 el.innerHTML / 提示语里最像按钮的那句）
        labels = re.findall(r"'([^']{2,14})'", "\n".join(lines[max(0, i - 40):i]))
        print(f"行 {i+1}  {url.group(1) if url else '?'}   函数 {fn or '?'}")
        for k, v in payload[:12]:
            print(f"      {k}: {v.strip()[:60]}")
        print()
    # 只读开关与 API 基址
    for pat in (r"var API = .*", r"var READONLY.*", r"var MIRROR.*"):
        for i, l in enumerate(lines):
            if re.match(r"\s*" + pat, l):
                print("  " + l.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

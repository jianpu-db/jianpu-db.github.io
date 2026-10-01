# -*- coding: utf-8 -*-
r"""STAR #14（"缺失假装成功"）+ 实测 CDN 对比 + 404 语义写进文档。

用法: py -3.13 tools/_patch_docs_404.py [--apply]
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STAR14 = '''### 14. "缺失"假装成"成功"：SPA 兜底把 404 变成了 200 + HTML

* **S**：对**线上**跑自检时它直接崩了：`Z_DATA_ERROR: incorrect header check` ——
  它去 gunzip 一个"HTTP 200"的响应，而那其实是一页 HTML。
* **A**：
  1. 根因不在自检脚本，而在部署配置：`wrangler.jsonc` 里 `not_found_handling = "single-page-application"`
     会把**任何**找不到的路径用 `200 + index.html` 返回。于是"缺失"伪装成"成功"：
     只看状态码的客户端会把 HTML 当数据解析（前端去 gunzip `data/*.gz` 就会这样炸）；
     而自检那一行只断言了 `ok`、没断言 content-type，**连自己也被骗过去了**。
  2. 修的过程很有意思，**前两次都只堵了一半**：
     * 先按前缀挡 `/data/`、`/static/`、`/img/` —— 部署后 `/data/__nope__.json.gz` 正确 404，
       但 `/img/../jianpu-db/score.py` **仍然 200**：`new URL(...)` 会先把 pathname **规范化**成
       `/jianpu-db/score.py`，前缀判断根本看不到 `/img/`；
     * 再加"看起来像文件（带扩展名）却返回 HTML 就 404" —— `/img/images/../../etc/passwd`
       规范化后是 `/etc/passwd`，**没有扩展名**，照样漏。
  3. 于是不再打补丁，改**原则**：**SPA 兜底只对页面路由生效**（`/`、`/index.html`、`/404.html`；
     页面级深链 `/s/<id>` 在它之前单独处理）。其余任何路径都按"资源"对待，查到 HTML 就 404。
     本站路由走 `#hash` / `?query`，所以这个白名单是完备的。
* **R**：
  * 部署后线上自检**由崩溃变全绿**；三条越界路径全部 404；关键路径一个没伤
    （`/` 200 text/html · `/s/<id>` 200 text/html · `/data/songs.jsonl.gz` 200 application/gzip 5.12 MB ·
    `/robots.txt` · `/sitemap.xml` 743 KB）；
  * 顺带量到一个事实：同一份索引从**边缘**下载 **3.1 s**，从 GitHub Pages 镜像 **18.9 s**（6 倍）
    —— 这条数字本身就是"为什么要多花一层 Worker"的答案；
  * 三条可迁移的经验：**① 检查项必须断言 content-type**（只看状态码就是自己骗自己）；
    **② 陈旧断言比没有断言更坏**（那行检查假定了"站点下发原图索引"，而前端早已不下发 —— 它会崩，或者更糟：假绿）；
    **③ 补丁式的白名单会一直漏，找到"原则"才收敛**（前缀 → 扩展名 → 页面路由白名单）。
'''

ARCH_OLD = ('| 边缘 | 静态资产、每谱注入、原图、反向代理写请求 | '
            'Cloudflare Workers（V8 isolates）+ Workers Assets（`run_worker_first`） |')
ARCH_NEW = ('| 边缘 | 静态资产、每谱注入、原图、反向代理写请求；'
            '**缺失资源一律 404**（SPA 兜底只对页面路由生效 —— 见 `TECH_STACK.md` 的 STAR 14） | '
            'Cloudflare Workers（V8 isolates）+ Workers Assets（`run_worker_first`） |')

DEPLOY_ADD = '''## 一·补三、404 语义（2026-10-02 定，别再改回去）

`wrangler.jsonc` 的 `not_found_handling = "single-page-application"` 会把**任何**找不到的路径
用 `200 + index.html` 返回。这在"缺失假装成功"上非常危险（客户端把 HTML 当数据解析；
自检只看状态码就被骗过去）。所以 Worker 里显式改成：

* **SPA 兜底只对页面路由生效**：`/`、`/index.html`、`/404.html`
  （页面级深链 `/s/<id>` 在此之前单独处理）；
* 其余路径一律按"资源"对待 —— 查到 `text/html` 就说明它并不存在，返回 **404 text/plain**。

为什么不用"前缀白名单 + 扩展名规则"：两者都被绕过过 ——
`/img/../jianpu-db/score.py` 会被 URL 规范化成 `/jianpu-db/score.py`（前缀看不见），
`/img/images/../../etc/passwd` 规范化后是 `/etc/passwd`（没有扩展名）。**原则比补丁收敛得快。**

实测：线上 `check_live` 全绿；三条越界路径全 404；`/`、`/s/<id>`、`/data/songs.jsonl.gz`、
`/robots.txt`、`/sitemap.xml` 全部正常。

顺便记一条实测：同一份索引从**边缘**（本域名）下载 **3.1 s**，从 GitHub Pages 镜像 **18.9 s**（6 倍）。

'''

DEPLOY_ANCHOR = '## 二、（可选）原图与投稿后端'


def patch(rel, pairs, apply, markers):
    p = os.path.join(ROOT, rel)
    if not os.path.exists(p):
        print("  %s: 不存在（跳过）" % rel)
        return
    t = io.open(p, encoding="utf-8").read()
    changed = []
    for (old, new), marker in zip(pairs, markers):
        if marker and marker in t:
            continue
        assert old in t, "%s: 找不到锚点 %r" % (rel, old[:60])
        t = t.replace(old, new, 1)
        changed.append(marker)
    if changed and apply:
        io.open(p, "w", encoding="utf-8", newline="\n").write(t)
    print("  %s: %s%s" % (rel, ", ".join(changed) if changed else "已是最新", "" if apply else "（未写）"))


def main():
    apply = "--apply" in sys.argv
    qa = "## 五、常见追问与答法（面试 Q&A）"
    patch("docs/TECH_STACK.md", [(qa, STAR14 + qa)], apply, ['### 14. "缺失"假装成'])
    patch("docs/ARCHITECTURE.md", [(ARCH_OLD, ARCH_NEW)], apply, ["缺失资源一律 404"])
    patch("DEPLOY.md", [(DEPLOY_ANCHOR, DEPLOY_ADD + DEPLOY_ANCHOR)], apply, ["404 语义（2026-10-02 定"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
r"""D 阶段收尾（第二批）：把 CI 转绿的过程写进文档 + 更新交接笔记。

用法: py -3.13 tools/_patch_docs_ci_green.py [--apply]
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STAR11 = '''### 11. CI 读不到日志时：先把"看不见"解决掉，再谈修 bug

* **S**：新加的质量门在 GitHub 上红了，但 `python` 矩阵那两条**主步骤全过**、只有 action 的 Post 步骤红；
  而 `web · node 22` 的 `check_tune` 红得莫名其妙 —— 代码同一份，**本机把便携版 Node 22 下下来跑照样过**。
  更麻烦的是：**job 日志必须登录才能读**（公开仓库也一样），公开 API 只给 job 列表和结论。
* **A**：
  1. **先解决"看不见"**：把那条 CI 步骤改成"跑完 `cat` 日志、失败时把关键行做成 **annotation**"
     （`::error title=check_tune::…`）。注解能用公开的 check-runs API 读到 —— 一条命令就拿到了
     `✗ 元数据表完整(歌手/状态都在)`，比"猜 Node 22 改了什么"有效得多。
  2. **拿到真因**：`app.ts` 用 `navigator.language` 决定界面语言。本机是 `ja-JP`（回落中文，所以本地一直绿），
     CI runner 是 `en-US`（标签渲成 `Artist`/`Status`）；而 **Node 20 没有 `navigator`**、Node 21+ 才有 ——
     这解释了"为什么只有 node 22 红"。**测试依赖了运行环境的语言**，这是测试设计问题。
  3. **修 + 决定性验证**：三个 harness 显式钉死 `navigator.language='zh-CN'`；然后复制一份去掉钉死 + 强制
     `en-US` -> **精确复现** CI 的那条失败；加回钉死 -> 通过。
  4. 顺带：`python` 矩阵的红是 `setup-uv@v5` 的 **Post（缓存收尾）**失败 —— 主步骤其实全过。
     升 `v6` + `enable-cache: false`，别让一个收尾步骤把整个信号污染成红。
* **R**：`checks` 五条全绿（web node20/22 · python 3.11/3.13 · **docker build**），
  `Deploy to GitHub Pages` 也绿。**"镜像能不能构建"这件事终于有了 CI 背书**（本机没装 Docker）。

**可迁移的两条**：① 观测能力是排障的前置条件 —— 日志够不着时，先花十分钟把结论搬到够得着的地方（注解/指标/状态）；
② 测试不能依赖运行环境（语言、时区、locale、Node 版本特性），否则它会用"随机红"训练人忽略信号。
'''

NOTE_ADD = '''
---

## 更新（2026-10-01 晚，D 阶段收尾）

* **CI 五条全绿** ✓ `checks`（web node20/22 · python 3.11/3.13 · docker build）+ `Deploy to GitHub Pages`。
  **`docker build` 绿了** —— 镜像可构建性终于有 CI 背书（本机没装 Docker，只做了步骤等价性验证）。
* 写路径已恢复（`tools\\tunnel_up.py` 修了三处恢复路径上的 bug，见 jianpu2 提交 `cb74de35`）：
  域名 `<https://jianpu-db.org/api/health>` 现在 `{"api":true,"upstreamOk":true,"og":11495}`。
* D 阶段文档已同步（`TECH_STACK.md` 加了 Docker/Compose、ruff+mypy、pre-commit、`/metrics`、Actions 矩阵，
  以及 STAR 第 10、11 条）。**D 阶段完成**。
* **下一阶段: E（算法侧）**。本机**没有 Rust 工具链**（cargo/rustc/wasm-pack 都没有，装齐要 ~1GB 下载，
  而这台机器现在只剩 ~3GB 空闲内存），所以 E 阶段选 **TS 里的倒排/ngram 预筛剪枝**：
  当前基线 `查询中位 144.6 ms / p90 169.7 ms / 索引就绪 213 ms`（`node tools/bench_search.mjs 40`），
  要求"剪枝后金曲四榜的 Top-1/3/5 **一格不掉**"才算成功（用现成的 `eval_golden.py` 验）。
'''


def patch(rel, pairs, apply, markers):
    p = os.path.join(ROOT, rel)
    t = io.open(p, encoding="utf-8").read()
    changed = []
    for (old, new), marker in zip(pairs, markers):
        if marker and marker in t:
            continue
        assert old in t, "%s: 找不到锚点 -> %r" % (rel, old[:70])
        t = t.replace(old, new, 1)
        changed.append(marker or new.split("\n")[0][:40])
    if changed and apply:
        io.open(p, "w", encoding="utf-8", newline="\n").write(t)
    for c in changed:
        print("  %s: %s%s" % (rel, c, "" if apply else "（未写）"))
    if not changed:
        print("  %s: 已是最新" % rel)


def main():
    apply = "--apply" in sys.argv
    qa = "## 五、常见追问与答法（面试 Q&A）"
    patch("docs/TECH_STACK.md", [(qa, STAR11 + qa)], apply, ["### 11. CI 读不到日志时"])
    # 交接笔记在另一个仓库（jianpu2）
    p = os.path.join(os.path.dirname(ROOT), "jianpu2", "D阶段_关机交接.md")
    if os.path.exists(p):
        t = io.open(p, encoding="utf-8").read()
        if "D 阶段收尾" not in t:
            t = t.rstrip() + "\n" + NOTE_ADD
            if apply:
                io.open(p, "w", encoding="utf-8", newline="\n").write(t)
            print("  D阶段_关机交接.md: 已追更新说明" + ("" if apply else "（未写）"))
        else:
            print("  D阶段_关机交接.md: 已是最新")
    return 0


if __name__ == "__main__":
    sys.exit(main())

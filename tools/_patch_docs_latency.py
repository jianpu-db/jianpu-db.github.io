# -*- coding: utf-8 -*-
r"""把简历/文档里的"当前延迟"统一到 E 阶段优化后的实测值，并给历史数字加标注。

背景: E 阶段做了两处优化（排序键预计算 144.6→105.9、ngram 剪枝长查询再降 30%），
但多处文档还写着优化前的 151 ms / p90 198 ms —— 简历里最不能出的错就是这个（数字自相矛盾）。

用法: py -3.13 tools/_patch_docs_latency.py [--apply]
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (文件, 旧串, 新串, 标记)
EDITS = [
    # ── 现状陈述: 改成 E 阶段优化后的实测值 ─────────────────────────────────────
    ("docs/RESUME.md",
     '> 就绪 204 ms、单次查询中位 151 ms），写回走"边缘反代 + 本机 git 提交"的混合架构。',
     '> 就绪 ~200 ms、单次查询中位 **106 ms**），写回走"边缘反代 + 本机 git 提交"的混合架构。',
     "RESUME 一句话"),
    ("docs/RESUME.md",
     '**204 ms**, and an octave-insensitive *cost-based* melody matcher answers a query in **151 ms median**\n  (p90 198 ms) **entirely in the browser** — no backend, no framework, no bundler.',
     '**~200 ms**, and an octave-insensitive *cost-based* melody matcher answers a query in **106 ms median**\n  (p90 114 ms) **entirely in the browser** — no backend, no framework, no bundler (two rounds of\n  profiling-driven optimisation; see the performance bullets below).',
     "RESUME 英文"),
    ("docs/RESUME.md",
     '| 为什么检索放浏览器？ | 无服务端成本、隐私好（查询不上传）、延迟可控（151 ms）；索引 5 MB 级完全装得下 |',
     '| 为什么检索放浏览器？ | 无服务端成本、隐私好（查询不上传）、延迟可控（中位 106 ms）；索引 5 MB 级完全装得下 |',
     "RESUME 问答"),
    ("docs/TECH_STACK.md",
     '| 单次查询 | 中位 **151 ms** · p90 198 ms · 最快 106 ms（同一份前端代码，Node 里跑 40 次） |',
     '| 单次查询 | 中位 **106 ms** · p90 114 ms · 最快 63 ms（同一份前端代码，30 次；**E 阶段优化后**——优化前是 144.6/169.7） |',
     "TECH_STACK 指标行"),
    ("docs/TECH_STACK.md",
     '11. **性能瓶颈在哪、怎么扩？** 见 `ARCHITECTURE.md` 第五节：转写受单卡限制（59–95 份/小时）、浏览器查询 151 ms（可上倒排剪枝或 Rust→Wasm）。',
     '11. **性能瓶颈在哪、怎么扩？** 见 `ARCHITECTURE.md` 第五节：转写受单卡限制（59–95 份/小时）；'
     '浏览器查询已优化到 ~106 ms（排序键预计算 -27% + ngram 剪枝长查询再降 30%，Rust→Wasm 试过但实测端到端 0.84× 已否决），'
     '下一步是 embedding+ANN 做模糊召回。',
     "TECH_STACK 问答"),
    # ── 历史陈述: 保留原数，但标明"那是哪个阶段"，免得和现状打架 ──────────────
    ("docs/交付总结.md",
     '* **零性能回退**：查询中位 151 → 139.9 ms；主界面产物 46,449 → 33,605 B（-28%）。',
     '* **零性能回退**：查询中位 151 → 139.9 ms（**这是 B 阶段当时的数**；E 阶段又优化到 ~106 ms）；'
     '主界面产物 46,449 → 33,605 B（-28%）。',
     "交付总结 B 阶段"),
    ("docs/TECH_STACK.md",
     '* 性能：索引就绪 **204 → 201 ms**，查询中位 **151 → 138.7 ms**（无回退）；',
     '* 性能：索引就绪 **204 → 201 ms**，查询中位 **151 → 138.7 ms**（无回退；**B 阶段数字**，E 阶段再优化到 ~106 ms）；',
     "TECH_STACK B 阶段"),
]


def main():
    apply = "--apply" in sys.argv
    for rel, old, new, tag in EDITS:
        p = os.path.join(ROOT, rel)
        t = io.open(p, encoding="utf-8").read()
        if new.split("\n")[0][:24] in t and old not in t:
            print("  %s [%s]: 已是最新" % (rel, tag))
            continue
        if old not in t:
            print("  %s [%s]: **找不到锚点**，跳过（请人工核对）" % (rel, tag))
            continue
        t = t.replace(old, new, 1)
        if apply:
            io.open(p, "w", encoding="utf-8", newline="\n").write(t)
        print("  %s [%s]: 已更新%s" % (rel, tag, "" if apply else "（未写）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

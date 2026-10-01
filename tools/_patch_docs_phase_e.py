# -*- coding: utf-8 -*-
r"""E 阶段文档同步：Rust→Wasm 的尝试 + "优化错了地方"的复盘。

用法: py -3.13 tools/_patch_docs_phase_e.py [--apply]
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STAR12 = '''### 12. 优化错了地方：花力气做 Rust→Wasm，真瓶颈是排序比较器里的一次 `new Set`

* **S**：查询中位 **144.6 ms**。我一直以为瓶颈是"11,495 首 / 2.5M 音符的全库匹配扫描"，
  于是装了 Rust（`rustup` + `wasm32-unknown-unknown`）、写了 wasm 内层代价循环（**0.9 KB**，零依赖、不用 wasm-bindgen）。
* **A**：
  1. 先建**逐首对拍**（`tools/check_wasm_parity.mjs`）：12 条查询 × 11,495 首 = **137,940 次**单曲比较，
     比 `(cost, at)` —— **代价逐首完全一致**；只有 123 处 `at` 不同，且**全部**是"同代价按段落权更换窗"
     （副歌优先于前奏，那段产品口径刻意留在 TS）；
  2. 再建**端到端对拍**（表快路径 vs 纯 TS 全扫，含多段查询）：15 条查询的最终卡片**逐条相同**；
  3. 然后测端到端 —— **0.84×，反而更慢**。细分计时：wasm 全库扫 **36 ms**、表快路径 `search()` 本体 **97 ms**、
     纯 TS 全扫 **113 ms** → wasm 省下的那点，抵不掉它自己的扫描开销；
  4. 顺着"本体 97 ms 到底花在哪"继续查：**排序比较器每次都现算 `versOf()`**，而它内部是
     `new Set(...)` + `.map()` —— `O(n log n)` 次比较里**每次都新分配一个 Set**（n ≈ 1 万）；
  5. 改成"每个候选只算一次排序键，比较器只比数字" → 查询中位 **144.6 → 105.9 ms（-27%）**。
* **R**：
  * 真收益来自**比较器修复**（已上线）；wasm 路径**不上线**（端到端 0.84×，数据在手），
    代码与对拍留在仓库里（`wasm-matcher/` + `search()` 的可选 `scan` 参数，默认不走）；
  * 顺带发现另一件事的价值：wasm 与 TS 的**双实现对拍**本身很值钱 —— 它能在 CI 里锁住代价表，
    任何一边漂了都会被逐首抓到；
  * **可迁移的教训**：先量，再改；"我以为的瓶颈"和"实测瓶颈"差了一倍以上。
    加速器只有在**它替换掉的那部分确实是瓶颈**时才有意义 —— 否则你只是把 20% 的耗时优化了 4 倍。
'''

RESUME_E = '''* **性能剖析（先量再改）**：为"全库旋律匹配"写了 **Rust→Wasm** 内层循环（0.9 KB，零依赖、无 wasm-bindgen），并用**逐首对拍**（12 查询 × 11,495 首 = 137,940 次比较，代价逐首一致）与**端到端对拍**（快路径 vs 纯 TS，结果逐条相同）证明正确性；但**实测端到端 0.84×（更慢）**，于是**不带上线**。继续细分计时后发现真瓶颈是排序比较器里每次 `new Set()`（`O(n log n)` 次分配），改成**预计算排序键**后查询中位 **144.6 → 105.9 ms（-27%）**。这条经历说明"加速器只有在替换掉的部分确实是瓶颈时才有意义"。
'''

ARCH_OLD = '| 浏览器检索 | 索引就绪 204 ms；查询中位 **151 ms**（p90 198 ms） | 全库代价匹配 O(曲数×查询长) | 倒排剪枝（先按 n-gram 候选）、或把匹配核心 **Rust→Wasm**（阶段 E） |'
ARCH_NEW = ('| 浏览器检索 | 索引就绪 ~200 ms；查询中位 **105.9 ms**（E 阶段前是 144.6 ms） | '
            '~~全库代价匹配~~ + 结果排序的固定开销 | 已做: **排序键预计算**（-27%）。'
            '试过并**否决**: Rust→Wasm 内层循环（代价逐首对拍一致，但端到端 0.84× —— wasm 扫描 36 ms '
            '抵不掉它自己的开销）；匹配核心若要再提速，下一步是**倒排/ngram 预筛**（先把候选降到 ~5%，再进精确匹配） |')


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
    patch("docs/TECH_STACK.md", [(qa, STAR12 + qa)], apply, ["### 12. 优化错了地方"])
    patch("docs/RESUME.md", [("* **数据一致性工程**", RESUME_E + "* **数据一致性工程**")], apply, ["性能剖析（先量再改）"])
    patch("docs/ARCHITECTURE.md", [(ARCH_OLD, ARCH_NEW)], apply, ["试过并**否决**"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

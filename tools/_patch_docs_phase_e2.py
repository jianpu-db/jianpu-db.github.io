# -*- coding: utf-8 -*-
r"""E 阶段（第二批）：ngram 剪枝落地后的文档同步 + 交接更新。

用法: py -3.13 tools/_patch_docs_phase_e2.py [--apply]
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ARCH_OLD = ('| 浏览器检索 | 索引就绪 ~200 ms；查询中位 **105.9 ms**（E 阶段前是 144.6 ms） | '
            '~~全库代价匹配~~ + 结果排序的固定开销 | 已做: **排序键预计算**（-27%）。'
            '试过并**否决**: Rust→Wasm 内层循环（代价逐首对拍一致，但端到端 0.84× —— wasm 扫描 36 ms '
            '抵不掉它自己的开销）；匹配核心若要再提速，下一步是**倒排/ngram 预筛**（先把候选降到 ~5%，再进精确匹配） |')
ARCH_NEW = ('| 浏览器检索 | 索引就绪 ~200 ms（ngram 倒排另在**空闲时**建 ~136 ms）；查询中位 **~106 ms** | '
            '结果排序的固定开销 + 全库代价匹配 | 已做: ① **排序键预计算**（144.6 → 105.9 ms，-27%）；'
            '② **ngram 剪枝**（`ensureGrams`；只对"每段都精确命中"的查询走，长查询 **69.1 → 48.2 ms/条（-30%）**，'
            '结果逐条相同；短查询/模糊查询退回全扫，等于没变化）。'
            '试过并**否决**: Rust→Wasm 内层循环（逐首对拍一致，但端到端 0.84×） |')

STAR_ADD = '''
* **补记（同一天更晚）**：按同一套"先量再改"的做法，又做了 **ngram 剪枝**（`ensureGrams` + 候选掩码）：
  只在"查询的每一段都能在库里找到精确命中"时才走 —— 这条规则**可证明安全**（代价 0 的歌必然音级子串包含查询，
  所以候选集是超集，并列裁决要用的一批一个不少；且只有"代价 0 的结果够填满榜单"时才采用剪枝结果）。
  实测: 长查询 **69.1 → 48.2 ms/条（-30%）**、结果**逐条相同**（60 条含模糊与多段）；
  短查询与模糊查询退回全扫（等于没变化，但也没变慢）。剪枝索引在**空闲时**建（~136 ms），不影响"打开就能用"。
'''

RESUME_ADD = '''* **检索性能（两轮优化，都是先量再改）**：① 排序键预计算（144.6 → 105.9 ms，-27%）；② **ngram 剪枝**（带"可证明安全"的采用条件：只在每段都精确命中、且代价 0 的结果够填满榜单时才用，长查询再降 30%）；两次都用**对拍**证明结果逐条不变（含模糊与多段查询）；此外完成一次 **Rust→Wasm** 的完整尝试并用实测数据否决（端到端 0.84×）。
'''

HANDOVER_ADD = '''
---

## 更新（同日更晚）

* **ngram 剪枝已落地并接入应用** ✓（`static/search.ts` 的 `ensureGrams` / `candidateMask`，`app.ts` 空闲时建）：
  * 采用条件**可证明安全**：只在"每段都能精确命中"且"代价 0 的结果够填满榜单"时才用剪枝结果；
  * 实测：长查询 **69.1 → 48.2 ms/条（-30%）**；短查询/模糊查询退回全扫（不变慢）；
  * 等价性对拍 `tools/check_prune_parity.mjs`：60 条（含模糊与多段）**结果逐条相同** ✓；
  * 剪枝索引 ~136 ms，放**空闲时**建 —— "打开就能用"的时间没变（仍 ~200 ms）。
* **Qwen2.5-VL-3B 已补回** ✓（29 个文件 / **7.52 GB**，`fetch_base_model.py` 走 hf-mirror 断点续传）。
* 全套自检 10 项 + typecheck + ruff/mypy 门槛**全绿**；`check_prune_parity.mjs` 也挂进了 CI。
* 仍未做：Cloudflare 正式站部署（卡在 wrangler 登录态，见上一节那条命令）。
'''

CI_OLD = '''      - name: 分享卡元数据检查
        run: node tools/check_og_meta.mjs'''
CI_NEW = '''      - name: 分享卡元数据检查
        run: node tools/check_og_meta.mjs
      - name: ngram 剪枝等价性（结果必须与全扫逐条相同）
        run: node tools/check_prune_parity.mjs 40
      - name: wasm 逐首/端到端对拍（缺工具仓库产物时明确跳过）
        run: node tools/check_wasm_parity.mjs'''


def patch(rel, pairs, apply, markers):
    p = os.path.join(ROOT, rel)
    t = io.open(p, encoding="utf-8").read()
    changed = []
    for (old, new), marker in zip(pairs, markers):
        if marker and marker in t:
            continue
        assert old in t, "%s: 找不到锚点 -> %r" % (rel, old[:60])
        t = t.replace(old, new, 1)
        changed.append(marker)
    if changed and apply:
        io.open(p, "w", encoding="utf-8", newline="\n").write(t)
    for c in changed:
        print("  %s: %s%s" % (rel, c, "" if apply else "（未写）"))
    if not changed:
        print("  %s: 已是最新" % rel)


def main():
    apply = "--apply" in sys.argv
    patch("docs/ARCHITECTURE.md", [(ARCH_OLD, ARCH_NEW)], apply, ["ngram 剪枝"])
    patch("docs/TECH_STACK.md", [("**可迁移的教训**：先量，再改；", STAR_ADD + "\n**可迁移的教训**：先量，再改；")],
          apply, ["补记（同一天更晚）"])
    patch("docs/RESUME.md", [("* **数据一致性工程**", RESUME_ADD + "* **数据一致性工程**")], apply, ["检索性能（两轮优化"])
    patch(".github/workflows/checks.yml", [(CI_OLD, CI_NEW)], apply, ["ngram 剪枝等价性"])
    # 交接笔记在工具仓库
    hp = os.path.join(os.path.dirname(ROOT), "jianpu2", "醒来交接_E阶段.md")
    if os.path.exists(hp):
        t = io.open(hp, encoding="utf-8").read()
        if "ngram 剪枝已落地" not in t:
            t = t.rstrip() + "\n" + HANDOVER_ADD
            if apply:
                io.open(hp, "w", encoding="utf-8", newline="\n").write(t)
            print("  醒来交接_E阶段.md: 已追更新" + ("" if apply else "（未写）"))
        else:
            print("  醒来交接_E阶段.md: 已是最新")
    return 0


if __name__ == "__main__":
    sys.exit(main())

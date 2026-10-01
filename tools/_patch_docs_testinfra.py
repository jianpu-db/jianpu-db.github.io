# -*- coding: utf-8 -*-
r"""STAR #15（测试自己会骗人：mtime 缓存）+ 演练稿补两条问答。

用法: py -3.13 tools/_patch_docs_testinfra.py [--apply]
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STAR15 = '''### 15. 测试自己会骗人：mtime 缓存让"门槛"测了**旧代码**

* **S**：给检索补了"排名金标准"门槛后，我按习惯做**反向验证**——故意往代价表里注入一处 `+1`，
  看门槛会不会红。它**红了** ✓。然后撤回改动，**它还是红的** ✗，而 `git status` 显示源码是干净的。
* **A**：一路查下去，问题不在门槛，也不在源码，而在**构建缓存的新鲜度判断**：
  缓存文件新鲜与否是按 **mtime** 比的（`mtime(缓存) > mtime(源)`）。
  我用 `Copy-Item` 还原源码，而它**保留备份文件的旧 mtime** → 缓存的构建比源"更新" →
  于是后面每次都返回**那份含坏改动的旧构建**。
  更普遍地说：任何"把更旧的文件放回来"都会踩到 —— `git checkout` 一个旧版本、解压一份备份、
  从别的目录拷一份过来。而这类错误的表现是**测试照样报绿/报红**，你根本看不出它测的是别的东西。
  修法：**按源文件内容 sha1 失效**（缓存文件首行写 `// build-hash: <hash>`），mtime 完全不作依据。
* **R**：
  * 决定性回归：注入坏改动 → 门槛 **12 条全红** ✓；**用旧 mtime 还原** → **恢复全绿** ✓
    （旧逻辑下最后这一步会一直红）；
  * 三条可迁移的经验：
    **① 反向验证要成为习惯** —— "故意把代码改坏，看测试会不会红"，这一步同时证明了门槛有效、
    也暴露了缓存有问题（如果我只跑"正常情况通过"就永远发现不了）；
    **② 测试装置的"新鲜度"判断别用时间戳**，用内容哈希；
    **③ 我这一块栽过四次**（对拍脚本期望值算错、复算时数字比 ASCII、字段名 `cost` 写成 `total`、
    以及这次 mtime 缓存）—— **测试装置本身也是代码，也会骗人**，所以指标出来后要回头验它符不符合直觉。
'''

MOCK_Q_ROW_ANCHOR = '| 怎么保证文档里的数字不是编的？ |'
MOCK_Q_ROW = ('| 你怎么保证"优化"没把质量搞坏？ | 三层：① **反向验证** —— 故意把代价表改坏，看门槛会不会红'
              '（不会红就说明门槛是摆设）；② **排名金标准 12 条**（查询取自目标歌自己的谱，正确答案客观）'
              '进 CI；③ **两种口径评测**（离线上限口径 + 上线产品口径）互证 |\n'
              '| 你的测试骗过你吗？ | 骗过**四次**。最狠的一次：构建缓存按 mtime 判新鲜度，我用 `Copy-Item`'
              '还原源码时保留了旧 mtime，于是测试一直在**测含坏改动的旧构建** —— 而它照样报红，'
              '差点让我以为门槛坏了。改成按内容 sha1 失效才根治。教训：测试装置本身也是代码 |')

TRAP_ANCHOR = '5. **"我自己的测试骗了我"**'
TRAP_ADD = '''6. **"缓存让测试测了旧代码"**：反向验证时门槛"撤回改动后仍然红"，查下去是构建缓存按 mtime 判新鲜度，
   而我还原文件时保留了旧 mtime → 一直用旧构建。**教训**：新鲜度用内容哈希，别用时间戳；
   而且"故意改坏看它会不会红"应该成为习惯 —— 正是这一步同时证明了门槛有效、暴露了缓存有问题。
'''


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
    patch("docs/TECH_STACK.md", [(qa, STAR15 + qa)], apply, ["### 15. 测试自己会骗人"])
    patch("docs/MOCK_INTERVIEW.md", [(MOCK_Q_ROW_ANCHOR, MOCK_Q_ROW + "\n" + MOCK_Q_ROW_ANCHOR)], apply,
          ["你怎么保证\"优化\"没把质量搞坏"])
    # 陷阱题里补第 6 条（放在 ③ 小节的末尾 = 下一个 "## " 之前）
    p = os.path.join(ROOT, "docs", "MOCK_INTERVIEW.md")
    t = io.open(p, encoding="utf-8").read()
    if "缓存让测试测了旧代码" not in t.split("## ④")[0]:
        anchor = "## ④ 规模追问"
        assert anchor in t
        t = t.replace(anchor, TRAP_ADD + "\n" + anchor, 1)
        if apply:
            io.open(p, "w", encoding="utf-8", newline="\n").write(t)
        print("  MOCK_INTERVIEW.md: 陷阱题补第 6 条%s" % ("" if apply else "（未写）"))
    else:
        print("  MOCK_INTERVIEW.md: 陷阱题已是最新")
    return 0


if __name__ == "__main__":
    sys.exit(main())

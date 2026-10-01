# -*- coding: utf-8 -*-
r"""更正召回评测那一节：两口径的差异**量出来**了 —— 全部来自并列策略。

用法: py -3.13 tools/_patch_docs_recall2.py [--apply]
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

OLD_BLOCK_START = '## 召回评测：两种口径，别混着说'
NEW_BLOCK = '''## 召回评测：两种口径的差异**量出来了**，全部来自"并列策略"

检索质量有两套评测。第一版我在文档里写成"两边互证" —— 这是**说重了** ✗：
离线口径 L=11 报 100%、产品口径只报 90.7%，两者差 9 个百分点。于是我去**量**这个差（而不是猜），
结论很干净：**匹配能力两边完全一样（召回 100%），差的那 9 个点全部来自"同代价时怎么排名"**。

| | 离线（Python） | 产品（TypeScript，上线那份） |
|---|---|---|
| 工具 | `jianpu2/tools/melody_retrieval_eval.py` | `tools/check_recall.mjs` |
| rank 规则 | 同代价时**把目标歌排最前** | 用**上线的并列裁决**（证据优先/置信度/版本数/段落权…） |
| 这是什么 | **召回上限**（"能不能找到"） | **产品真实名次**（"用户看到排第几"） |
| L=11 无错音 | **Top-1 100.0%**（409/409） | 并列乐观 **100.0%** / 产品裁决 **93.7%** |
| 同代价组均值 | **1.72** | **1.7** |

两列在**同一个 rank 规则**下逐项吻合（100.0% vs 100.0%、1.7 vs 1.72）——
这就是"差异来自并列策略"的证据。`tools/check_recall.mjs` 现在同时打印三列：
`strict`（产品裁决）、`并列下界`（最保守）、`并列乐观`（= 离线口径，用来和离线对照）。

### 完整曲线（产品口径，`bench_final100`，82 首 × 每首 3 窗口 = 246 次查询）

| 查询长度 | strict Top-1 | Top-3 | Top-5 | 同代价组均值 |
|---|---|---|---|---|
| L=7 | 34.1% | 56.1% | 67.5% | **19.6** |
| L=11 | 90.7% | 99.6% | 99.6% | 1.4 |
| L=15 | **97.6%** | **100%** | **100%** | 1.0 |

**最有用的一行是"同代价组均值"**：L=7 时有 **19.6** 组并列 —— 说明短查询的瓶颈
**不是匹配不够好，而是本来就有很多歌共享同一小段音**。这正是前端"这句不是唯一命中"提示的依据，
也是"别用 Top-1 单一指标吹检索质量"的理由。

### 注入错音（模拟凭耳朵记错）

L=11 错 2 个音：strict Top-1 50.0% / Top-3 74.0% / Top-5 80.9%（同代价组均值 5.0）。
代价表刻意**允许**少量错音（违反单调性的惩罚），所以召回还在，但名次明显变差 ——
这也是为什么界面上同时给"代价"和"并列数"，而不是只给一个名次。

### 为什么必须补产品口径那套

离线评测验不到 TS 侧 —— 剪枝最坏的情况就是"把目标歌剪掉"，离线口径根本看不出来。
主口径引用产品口径的数字；离线口径只在"召回上限/跨版本对照"时引用。

'''

INTERVIEW_OLD = ('| 你怎么保证"优化"没把质量搞坏？ | 三层：① **反向验证** —— 故意把代价表改坏，看门槛会不会红'
                 '（不会红就说明门槛是摆设）；② **排名金标准 12 条**（查询取自目标歌自己的谱，正确答案客观）'
                 '进 CI；③ **两种口径评测**（离线上限口径 + 上线产品口径）互证 |')
INTERVIEW_NEW = ('| 你怎么保证"优化"没把质量搞坏？ | 三层：① **反向验证** —— 故意把代价表改坏，看门槛会不会红'
                 '（不会红就说明门槛是摆设）；② **排名金标准 12 条**（查询取自目标歌自己的谱，正确答案客观）进 CI；'
                 '③ **两种口径评测**：离线上限口径与上线产品口径 |\n'
                 '| 两个指标不一致怎么办？ | 我去**量**了，没有猜。离线口径 L=11 报 100%、产品口径报 90.7% —— '
                 '给产品口径加上"并列乐观"这一列后是 **100.0%**，并列均值 1.7 vs 离线 1.72，逐项吻合：'
                 '**差距全部来自并列策略，匹配能力（召回）两边都是 100%**。顺手把这一列固化进工具，'
                 '以后两套口径可以直接对照 |')


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
    # 整段替换 QA.md 里的召回小节（从标题到下一个 "## " 之前）
    p = os.path.join(ROOT, "docs", "QA.md")
    t = io.open(p, encoding="utf-8").read()
    if "全部来自\"并列策略\"" not in t:
        i = t.index(OLD_BLOCK_START)
        j = t.index("\n## ", i + 1)
        t = t[:i] + NEW_BLOCK + t[j + 1:]
        if apply:
            io.open(p, "w", encoding="utf-8", newline="\n").write(t)
        print("  docs/QA.md: 召回小节已更正%s" % ("" if apply else "（未写）"))
    else:
        print("  docs/QA.md: 已是最新")
    patch("docs/MOCK_INTERVIEW.md", [(INTERVIEW_OLD, INTERVIEW_NEW)], apply, ["两个指标不一致怎么办"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

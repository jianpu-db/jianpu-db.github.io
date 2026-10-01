# -*- coding: utf-8 -*-
r"""把"前端口径召回评测"的实测写进文档。

用法: py -3.13 tools/_patch_docs_recall.py [--apply]
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

QA_SECTION = '''## 召回评测：两种口径，别混着说

检索质量有两套评测，**口径不同、不可直接比**，引用时要写清是哪一套：

| 口径 | 工具 | rank 规则 | 结果（L=15 无错音） |
|---|---|---|---|
| **离线（Python）** | `jianpu2/tools/melody_retrieval_eval.py` | 目标歌在**同代价里排最前** —— 这是**上限** | 酷狗华流 **98.9%** · 中文流行 96.6% · 金曲奖 100% |
| **产品（TypeScript，真正上线那份）** | `tools/check_recall.mjs` | 用**上线的并列裁决**（证据优先/置信度/版本数/段落权…） | bench_final100 **Top-1 97.6% / Top-3 100% / Top-5 100%** |

为什么必须补产品口径那套：离线评测**验不到 TS 侧** —— 剪枝最坏的情况是"把目标歌剪掉"，
离线口径根本看不出来。两套在 L=15 都是 97–99%，**互证**。

### 完整曲线（产品口径，`bench_final100`，82 首可用 × 每首 3 窗口 = 246 次查询）

| 查询长度 | Top-1 | Top-3 | Top-5 | 同代价组均值 |
|---|---|---|---|---|
| L=7 | 34.1% | 56.1% | 67.5% | **19.6** |
| L=11 | 90.7% | 99.6% | 99.6% | 1.4 |
| L=15 | **97.6%** | **100%** | **100%** | 1.0 |

**这张表里最有用的一行是"同代价组均值"**：L=7 时有 **19.6** 组并列 —— 说明短查询的瓶颈
**不是匹配不够好，而是本来就有很多歌共享同一小段音**。这正是前端"这句不是唯一命中"提示的依据，
也是"别用 Top-1 单一指标吹嘘检索质量"的理由。

### 注入错音（模拟凭耳朵记错）

L=11 错 2 个音：Top-1 50.0% / Top-3 74.0% / Top-5 80.9%（同代价组均值 5.0）。
代价表用"违反单调性"的惩罚（见代价表注释）刻意**允许**少量错音，所以召回还在，但名次明显变差 ——
这也是为什么界面上要同时给"代价"和"并列数"，而不是只给一个名次。

'''

TECH_ROW_OLD = '| 金曲榜检索 | 酷狗 2025 华流 L=15 错0 **Top-1 98.9%** · 中文流行 96.6% · 金曲奖 100% |'
TECH_ROW_NEW = ('| 金曲榜检索 | **离线口径**（目标并列排最前=上限）: 酷狗 2025 华流 L=15 错0 **Top-1 98.9%** · '
                '中文流行 96.6% · 金曲奖 100%；**产品口径**（上线那份 TS 的并列裁决）: L=15 **97.6% / Top-3 100%**、'
                'L=11 90.7%、L=7 34.1%（**短查询瓶颈是并列: L=7 同代价 19.6 组**） |')

MOCK_ROW_ANCHOR = '| 对拍 | **12 查询 × 11,495 首 = 137,940 次**单曲比较，代价**逐首一致**（123 处 `at` 差异全部是"同代价按段落权更换窗"） |'
MOCK_ROW_NEW = (MOCK_ROW_ANCHOR + '\n'
                '| 召回（产品口径） | L=15 **Top-1 97.6% / Top-3 100% / Top-5 100%** · L=11 90.7% · '
                'L=7 34.1%（同代价组 **19.6** —— 短查询瓶颈是并列不是匹配） |')


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
    anchor = '## 还该知道的两件"坑"'
    patch("docs/QA.md", [(anchor, QA_SECTION + anchor)], apply, ["召回评测：两种口径"])
    patch("docs/TECH_STACK.md", [(TECH_ROW_OLD, TECH_ROW_NEW)], apply, ["产品口径**（上线那份 TS"])
    patch("docs/MOCK_INTERVIEW.md", [(MOCK_ROW_ANCHOR, MOCK_ROW_NEW)], apply, ["召回（产品口径）"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

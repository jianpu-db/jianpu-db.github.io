# -*- coding: utf-8 -*-
r"""同步"交付总结"：STAR 计数 12->15 + 补质量抽检与召回评测两节。

用法: py -3.13 tools/_patch_docs_summary.py [--apply]
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

QA_SECTION = '''## Q. 质量抽检（夜里顺手做的，四个可动手的问题）

转写流水线整夜在跑（常驻 6 GB 显存、内存只剩几百 MB），所以抽检做成**只读、秒级**的
（详见 [QA.md](QA.md)），不去跟它抢资源。量出来的四类问题：

| # | 问题 | 数量 | 该怎么办 |
|---|---|---|---|
| 1 | **碎片谱**（<20 音，有原图却只转出几个音） | **15 份** | 真数据损失 —— 已挂看护，等显存空了自动重转 |
| 2 | **归组重复**（大小写/标点差异被拆成多个组） | **23 个组名 / 46 组** | 已实现为**可选开关**（`JP_GROUP_NORM=1`，默认关）：开启后 8625→8602 组、音符/变音/id 完全一致 |
| 3 | **标题互相包含**（爬虫把速度/情绪标注拼进曲名） | **187 组** | 喂给 `jp_refine_titles` / `jp_tidy_titles` |
| 4 | **标题无关**（占位名如 `未命名…`/`谱`/`改编歌曲`，而旋律是对的） | **182 组** | 同上；这类"旋律对、标题错"最该修 |

配套工具（都已挂进夜间自检与 CI）：`qa_night_sweep.py`、`triage_fragments.py`、
`check_dup_groups.mjs`、`check_dup_melody.mjs`。

## R. 召回评测：两个口径的差异**量出来了**

| | 离线（Python） | 产品（TypeScript，上线那份） |
|---|---|---|
| rank 规则 | 同代价时**把目标排最前** | 用**上线的并列裁决** |
| 含义 | **召回上限**（能不能找到） | **产品真实名次**（用户看到排第几） |
| L=11 无错音 | **100.0%**（409/409） | 并列乐观 **100.0%** / 产品裁决 **93.7%** |
| 同代价组均值 | 1.72 | 1.7 |

两列在同一 rank 规则下**逐项吻合** —— 证据表明**差距全部来自并列策略，匹配能力两边都是 100%**。
产品口径的完整曲线（`bench_final100`，82 首 × 3 窗口 = 246 次查询）：
L=7 **34.1%**（同代价 **19.6** 组）· L=11 **90.7%** · **L=15 97.6% / Top-3 100%**。
**短查询的瓶颈是并列，不是匹配** —— 这就是前端"这句不是唯一命中"提示的依据。

'''

NEW_STAR_LINE = ('* `docs/TECH_STACK.md`：分层选型（每项 **为什么选它 + 量化收益**）、'
                 '**"刻意没用"清单**（DB/框架/打包器…各写清何时会引入）、**15 条难题 STAR 复盘**、'
                 '**20 条面试 Q&A**；')
OLD_STAR_LINE = ('* `docs/TECH_STACK.md`：分层选型（每项 **为什么选它 + 量化收益**）、'
                 '**"刻意没用"清单**（DB/框架/打包器…各写清何时会引入）、**12 条难题 STAR 复盘**、')


def main():
    apply = "--apply" in sys.argv
    p = os.path.join(ROOT, "docs", "交付总结.md")
    t = io.open(p, encoding="utf-8").read()
    changed = []
    if OLD_STAR_LINE in t:
        t = t.replace(OLD_STAR_LINE, NEW_STAR_LINE, 1)
        changed.append("STAR 计数 12->15")
    # 把 Q/R 两节插在"现在的运行状态"之前
    anchor = "## 现在的运行状态"
    if "## Q. 质量抽检" not in t and anchor in t:
        t = t.replace(anchor, QA_SECTION + anchor, 1)
        changed.append("补 Q/R 两节")
    if changed and apply:
        io.open(p, "w", encoding="utf-8", newline="\n").write(t)
    print("  docs/交付总结.md: %s%s" % (", ".join(changed) if changed else "已是最新", "" if apply else "（未写）"))
    # 顺手校一下别的文档有没有"N 条 STAR/问答"的陈旧声明
    for rel in ("README.md", "docs/RESUME.md", "docs/ARCHITECTURE.md"):
        q = os.path.join(ROOT, rel)
        if not os.path.exists(q):
            continue
        txt = io.open(q, encoding="utf-8").read()
        for n in ("10 条", "11 条", "12 条", "13 条", "14 条"):
            if ("难题 STAR" in txt or "STAR 复盘" in txt) and n in txt:
                print("  ⚠ %s 里出现 %s（STAR 实际 15 条），请核对" % (rel, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())

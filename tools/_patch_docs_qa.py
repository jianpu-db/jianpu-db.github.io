# -*- coding: utf-8 -*-
r"""把"质量抽检"这一块写成可追问的材料：STAR #13 + 简历要点 + docs/QA.md。

用法: py -3.13 tools/_patch_docs_qa.py [--apply]
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STAR13 = '''### 13. 抽检要抽"用户看得见的那一类"，而且不能跟生产抢资源

* **S**：语料在夜里由计划任务持续扩大（当时正把一批新谱转进库里）。我想做质量抽检，但如果抽检脚本
  自己去解析全部曲谱，就会跟正在跑的转写**抢显存和内存**（那台机器当时只剩 0.6 GB 空闲）——
  结果是两边都慢，甚至把转写搞崩。
* **A**：
  1. 把抽检做成**只读 + 轻量（0.5 s）**：只读 `data.jsonl` 与站点索引，不碰曲谱解析、不加载模型；
  2. 每一项都**用产物里已有的证据**判断，不靠猜：
     "碎片谱"看**原图张数**（≥2 张 = 多页只转了第一页）+ 转写 sidecar 里的 `dropped_pages`；
     "归组重复"看**索引里剥掉后缀/标点后同名却分成两组**；
  3. 抽检生成的"待修清单"**由工具自己写文件**（`--lists` / `--redo-out` / `--out`）——
     我手工从报告里抄过一次，越过了段落边界，把 15 份该重转的抄成了 80 份；
  4. 要做"归组口径合并"这类改动，先在**内存里原型验证**再动手：
     组数 8625 → 8602（-23，与检查器预测逐一对上）、曲数不变、7 条旋律查询的**代价/位置/精确计数逐条不变**
     （证明归一化只动组名、不动音符层面）。
* **R**：
  * 抽检发现了两类真问题：**15 份"多页未转全"**（有原图却只转出 6~10 个音，是真数据损失）与
    **23 个组名被拆成 46 个组**（大小写/标点差异 → 用户搜同一首歌会看到两个）；
  * 又一条可迁移的教训：**先量再改** 之外还要 **"不跟生产抢"** —— 夜里在跑流水线时，
    只读的、秒级的检查才是对的工具；要动数据的改动（重建索引）排到流水线空闲之后；
  * 第三条：**凡是"手搓"的都出错了**（手搓路径、手抽名单、手拼字符串各错一次），
    所以现在这类产物一律让工具生成 —— 人只审查，不搬运。
'''

RESUME_QA = '''* **质量工程（自检不停）**：为持续扩大的语料做了**只读、0.5 s** 的夜间抽检（不与应用抢 GPU/内存），用产物里的证据分类问题谱（原图张数 / sidecar 的 `dropped_pages` / 归组归一化），实测发现 **15 份"多页未转全"**（真数据损失）与 **23 个组名被拆成 46 组**（大小写与标点差异导致用户可见重复）；改动前先做**内存原型验证**（组数 8625→8602、7 条旋律查询结果逐条不变）再实施。
'''

QA_DOC = '''# 质量工程：自检怎么跑、怎么判、什么时候不能跑

> 一句话：**抽检要抽用户看得见的那一类，而且不能跟生产抢资源。**

## 三条工具（都在本仓库或工具仓库 `tools/` 下）

| 命令 | 干什么 | 代价 |
|---|---|---|
| `py -3.13 tools/qa_night_sweep.py [--json] [--lists DIR]` | 夜间轻量抽检：语料↔索引一致性、曲名后缀、空名/缺 source、碎片谱、低置信度、同名多版本、**近 24 小时新增（git 历史）** | **0.5 s**，只读 |
| `py -3.13 tools/triage_fragments.py [--in] [--out] [--redo-out]` | 把碎片谱按**证据**分类：原图 ≥2 张（多页只转第一页）/ sidecar 有丢页 / 本来就是短曲 / 原图已不在；并生成 `transcribe_source.py` 能直接吃的重转名单 | 秒级，只读 |
| `node tools/check_dup_groups.mjs [映射表路径]` | 查"归组名剥后缀+去标点+转小写后同名、却被拆成多个组"—— 用户可见的重复 | 秒级，只读 |

## 为什么这样设计（都是踩出来的）

1. **不跟生产抢资源**：夜里转写流水线在跑（显存 6 GB+ 常驻、内存只剩几百 MB）。
   抽检若解析全部曲谱就会拖垮它 —— 所以只读 `data.jsonl` 与站点索引，秒级出结论。
2. **判据用产物里的证据，不靠猜**：
   * "碎片谱"看**原图张数**，不是看音符数就觉得"肯定错了"（本来就短的儿歌也是 <20 音）；
   * "归组重复"看索引里归一化后的键，而不是看曲名"像不像一样"。
3. **不跟流水线的重建打架**：改归组口径要重建索引，而流水线自己也有"空闲重建" —— 撞上就是写冲突。
   所以这类改动**排到流水线空闲之后**（或人工窗口）。
4. **凡是手搓的都出错**（我错了三次：手搓路径、手抽名单、手拼字符串）→
   清单/映射/名单一律 `--lists` / `--redo-out` / `--out` 让**工具产出**，人只审查。
5. **改之前先在内存里原型验证**：归一化只该动组名，于是拿 7 条旋律查询比"代价/位置/精确计数"
   逐条是否不变 —— 不变才敢去改索引构建。

## 一次真跑的结果（2026-10-02 夜）

* 语料 **11,495 首 / 2,532,332 音符**；站点索引 11,495 行 **✓ 一致**；近 24 小时（git 历史）新增 **236 份**；
* 曲名带站名/分类后缀 **202**、空曲名 **0**、缺 source **38**、碎片谱 **80**、低置信度 **162**、同名最多 19 版；
* 碎片谱分类：**多页未转全 15**（真该重转，例:《爱错》原图 4 张 → 只转出 6 个音）、找不到原图 59、本来就是短曲 6；
* 归组重复：**23 个组名 / 46 个组**（`Amani`/`AMANI`、`Love`/`love`、`中国_中国`/`中国，中国`…）。

## 还该知道的两件"坑"

* `data.jsonl` 里 `file` / `source` 是 **list**，而站点索引里是**字符串** —— 两边形状不同，别混用；
* "近 24 小时新增"**不能用文件 mtime 统计**：流水线重建会刷新整目录 mtime，
  第一版算出 23,982 份（而 `scores/` 一共才 24,011 份）。要用 `git log --since=24.hours --name-only`。
'''


def patch(rel, pairs, apply, markers):
    p = os.path.join(ROOT, rel)
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
    patch("docs/TECH_STACK.md", [(qa, STAR13 + qa)], apply, ["### 13. 抽检要抽"])
    patch("docs/RESUME.md", [("* **数据一致性工程**", RESUME_QA + "* **数据一致性工程**")], apply, ["质量工程（自检不停）"])
    p = os.path.join(ROOT, "docs", "QA.md")
    if not os.path.exists(p):
        if apply:
            io.open(p, "w", encoding="utf-8", newline="\n").write(QA_DOC)
        print("  docs/QA.md: %s" % ("已创建" if apply else "将创建"))
    else:
        print("  docs/QA.md: 已存在")
    # README 索引
    r = os.path.join(ROOT, "README.md")
    t = io.open(r, encoding="utf-8").read()
    if "docs/QA.md" not in t:
        anchor = "| [docs/交付总结.md](docs/交付总结.md) |"
        assert anchor in t, "README 里找不到索引锚点"
        t = t.replace(anchor, "| [docs/QA.md](docs/QA.md) | **质量工程**：自检怎么跑、判据用什么证据、什么时候不能跑 |\n" + anchor, 1)
        if apply:
            io.open(r, "w", encoding="utf-8", newline="\n").write(t)
        print("  README.md: 加 QA.md 索引%s" % ("" if apply else "（未写）"))
    else:
        print("  README.md: 已是最新")
    return 0


if __name__ == "__main__":
    sys.exit(main())

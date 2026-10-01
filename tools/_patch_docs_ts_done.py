# -*- coding: utf-8 -*-
r"""B 阶段收尾：把三份文档同步到"前端全部 TS + 渐进迁移 + 产物 -28%"的**已完成**状态。

幂等 + 断言：锚点找不到就报错（第一版用 here-string 手改曾静默不改，吃过一次）。
用法: py -3.13 tools/_patch_docs_ts_done.py [--apply]
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def patch(rel, pairs, apply, markers=None):
    p = os.path.join(ROOT, rel)
    t = io.open(p, encoding="utf-8").read()
    done = []
    markers = markers or [""] * len(pairs)
    for i, (old, new) in enumerate(pairs):
        # ⚠ 判"已是最新"要用**新内容独有的标记**。第一版拿 new 的第一行去比，
        #   而那一行常常就是 old 的第一行 -> 永远判成"已最新"、静默不改（这次真踩到了）。
        if markers[i] and markers[i] in t:
            done.append("（已是最新）")
            continue
        assert old in t, f"{rel}: 找不到锚点 -> {old[:60]!r}"
        t = t.replace(old, new, 1)
        done.append(new.split("\n")[0][:50])
    if apply:
        io.open(p, "w", encoding="utf-8", newline="\n").write(t)
    for d in done:
        print(f"  {rel}: {d}")
    return 0


def main():
    apply = "--apply" in sys.argv

    # ── TECH_STACK: 把 TypeScript 那行更新成"全部完成 + 渐进迁移 + 体积" ──
    old_row = ("| **TypeScript**（2026-10-01 迁移） | 口径最值钱的地方（token 解析、代价匹配、并列裁决）最怕改错；"
               "DOM 代码也想有编译期保证 | **零性能代价**（迁移后实测：索引就绪 201 ms vs 204 ms、查询中位 "
               "**138.7 ms** vs 151 ms）；构建只用 esbuild 做**类型擦除**，仍然不打包运行时依赖 |")
    new_row = ("| **TypeScript**（2026-10-01 全部迁移完成） | 口径最值钱的地方（token 解析 / 代价匹配 / 并列裁决）"
               "与边缘逻辑最怕改错；DOM 代码也想有编译期保证 | `jptok.ts` · `search.ts` · `app.ts` · `worker/index.ts` "
               "四处**都是 TS**；`tsc` 三份配置（前端核心 strict / 主界面过渡 / Worker strict）+ esbuild 只做类型擦除。"
               "**零性能回退**（查询中位 139.9 ms vs 151 ms；索引就绪 179–201 ms vs 204 ms）；"
               "主界面产物 **46,449 → 33,605 B（-28%）**（顺带剥掉注释，源码里那份'为什么'仍在） |")
    patch("docs/TECH_STACK.md", [(old_row, new_row)], apply, markers=["46,449 → 33,605 B"])

    # ── TECH_STACK: STAR #7 补上 app.ts 与"渐进迁移"的取舍 ──
    old_star = ("  * 构建脚本自己也有一个：拼 `.ts` 时忘了去扩展名（找 `jptok.js.ts`）→ 静默少两个产物文件。"
                "已改成\"缺文件即构建失败\"。")
    new_star = ("  * 构建脚本自己也有一个：拼 `.ts` 时忘了去扩展名（找 `jptok.js.ts`）→ 静默少两个产物文件。"
                "已改成\"缺文件即构建失败\"。\n"
                "  * **主界面 867 行走的是渐进迁移**（`tsconfig.app.json` 过渡配置：数据层真类型、DOM 胶水层待收紧），"
                "并在**文件头写明**下一步要修的三类 —— 业界叫 strangler 迁移：不为了'看起来全 strict'而把回归风险堆在一次改动里。"
                "过程中把 6 处事件委托收成一个带泛型的 `closestFrom<T>()`，**代码反而更短**。")
    patch("docs/TECH_STACK.md", [(old_star, new_star)], apply, markers=["closestFrom<T>()"])

    # ── RESUME: TS 那条更新 ──
    old_b = "* **前端 TypeScript 化（零性能代价）**：把 token 解析 / 代价匹配 / 卡片渲染迁到 TS，构建只用 esbuild 做**类型擦除**（仍不打包运行时依赖），`tsc --noEmit` 进 CI；实测索引就绪 204→201 ms、查询中位 151→138.7 ms **无回退**。"
    new_b = ("* **前端与边缘 TypeScript 化（零运行时依赖、零性能回退）**：`jptok`（词元口径）/ `search`（代价匹配）"
             "/ `app`（867 行主界面）/ `worker`（边缘路由与鉴权）四处源码全迁到 TS，构建只用 esbuild 做**类型擦除**"
             "（仍不打包运行时依赖），`tsc` 三份配置（strict 核心 + 主界面过渡 + strict Worker）进 CI；"
             "实测索引就绪 204→179–201 ms、查询中位 151→139.9 ms **无回退**，主界面产物 46,449→33,605 B（**-28%**）。")
    patch("docs/RESUME.md", [(old_b, new_b)], apply, markers=["46,449→33,605 B"])

    # ── ARCHITECTURE: 前端那行补上"三份类型检查配置" ──
    old_arch = "| 前端 | 检索/卡片/谱页/深链，**全部在浏览器里跑** | TypeScript（**零运行时依赖**的 ES module；esbuild 只做类型擦除、不打包） |"
    new_arch = ("| 前端 | 检索/卡片/谱页/深链，**全部在浏览器里跑** | TypeScript（**零运行时依赖**的 ES module；"
                "esbuild 只做类型擦除、不打包）；`tsc` 三份配置：前端核心 strict、主界面过渡（DOM 胶水层待收紧）、Worker strict |")
    patch("docs/ARCHITECTURE.md", [(old_arch, new_arch)], apply, markers=["tsc` 三份配置"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

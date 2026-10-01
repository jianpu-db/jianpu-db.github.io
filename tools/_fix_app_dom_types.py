# -*- coding: utf-8 -*-
r"""把 `static/app.ts` 的 DOM 胶水层收紧到"类型检查能过"（`tsconfig.app.json` 那份宽松配置下）。

这一步只做**机械且可解释**的修正（每类都在下面写了原因），不改任何行为:

| 报错 | 原因 | 修法 |
|---|---|---|
| `Property 'value' does not exist on type 'HTMLElement'` | `$()` 只知道"是个元素"，不知道是 input | `$<HTMLInputElement>('q')` |
| `Property 'disabled' does not exist ...` | 同上（button） | `$<HTMLButtonElement>('go')` |
| `Property 'closest' does not exist on type 'EventTarget'` | 事件目标是 `EventTarget`，不是 `Element` | `(e.target as Element).closest(...)` |
| `Property 'placeholder'/'focus' does not exist on type 'Element'` | 需要 input | 断言成 `HTMLInputElement` |
| `Property 'JIANPU_API' does not exist on Window` | 站点自己的全局（构建时注入） | `declare global { interface Window { … } }` |
| `Property 'row'/'note' does not exist on type '{}'` | `FIELDS[key] \|\| {}` 推出 `{}` | 直接取 `FIELDS[key]`（键来自 `Object.keys`，一定存在） |
| `Property 'test' does not exist on type 'string \| RegExp'` | 平台表来自 stats.json，host 是字符串 | 构建时 `new RegExp(...)` 已保证是 RegExp，加断言 |

用法: py -3.13 tools/_fix_app_dom_types.py [--apply]
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "static", "app.ts")

# 这些 id 是输入框 -> 取 .value / .placeholder / .focus 时要泛型成 HTMLInputElement
INPUT_IDS = ("q", "stitle", "sscore", "snote", "scontact", "skind", "tq", "out")
BTN_IDS = ("go", "sgo", "sfill")

GLOBAL_DECL = """
/* 站点自己在构建时注入的全局（见 tools/build_dist.mjs: `JIANPU_API` / `JIANPU_READONLY`）。
 * 声明一下，省得每处 `window.X` 都报 TS2339。 */
declare global {
  interface Window {
    JIANPU_API?: string;
    JIANPU_READONLY?: boolean;
  }
}
"""


def main():
    apply = "--apply" in sys.argv
    t = io.open(P, encoding="utf-8").read()
    orig = t
    fixes = []

    # ① `$()` 变成带泛型的取元素
    old_dollar = "function $(id) { return document.getElementById(id); }"
    new_dollar = ("function $<T extends HTMLElement = HTMLElement>(id: string): T {\n"
                  "  // 调用方按需要给泛型：`$<HTMLInputElement>('q')`。默认 HTMLElement（够用于 textContent/className）。\n"
                  "  return document.getElementById(id) as T;\n"
                  "}")
    if old_dollar in t:
        t = t.replace(old_dollar, new_dollar, 1)
        fixes.append("$() 加泛型")

    # ② 输入框/按钮的调用点
    for i in INPUT_IDS:
        n = t.count("$('%s')" % i)
        if n:
            t = t.replace("$('%s')" % i, "$<HTMLInputElement>('%s')" % i)
            fixes.append(f"$('{i}') -> $<HTMLInputElement>（{n} 处）")
    for i in BTN_IDS:
        n = t.count("$('%s')" % i)
        if n:
            t = t.replace("$('%s')" % i, "$<HTMLButtonElement>('%s')" % i)
            fixes.append(f"$('{i}') -> $<HTMLButtonElement>（{n} 处）")

    # ③ 事件目标
    n = len(re.findall(r"\be\.target\.closest\(", t))
    t = re.sub(r"\be\.target\.closest\(", "(e.target as Element).closest(", t)
    if n:
        fixes.append(f"e.target -> (e.target as Element)（{n} 处）")

    # ④ 全局声明
    if "JIANPU_API?: string" not in t:
        t = t.replace("function $<T extends HTMLElement", GLOBAL_DECL + "\nfunction $<T extends HTMLElement", 1)
        fixes.append("补 declare global（JIANPU_API / JIANPU_READONLY）")

    # ⑤ metaRows 里的 `FIELDS[key] || {}` -> 直接取（键来自 Object.keys）
    t = t.replace("var f = FIELDS[key] || {};", "var f = FIELDS[key];", 1)
    t = t.replace("var f = FIELDS[key] || {};", "var f = FIELDS[key];")

    # ⑥ 平台表：host 明确是正则
    t = t.replace("return [p.name, new RegExp(p.host || '.', 'i'), p.exact || 'https://…', p.search || ''];",
                  "return [p.name, new RegExp(p.host || '.', 'i'), p.exact || 'https://…', p.search || ''] as Platform;", 1)

    if apply:
        io.open(P, "w", encoding="utf-8", newline="\n").write(t)
        print("  已写入 static/app.ts")
        for f in fixes:
            print("    · " + f)
        print("  改动字节: %+d" % (len(t) - len(orig)))
    else:
        for f in fixes:
            print("  会改: " + f)
        print("  （加 --apply 才写）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
r"""`static/app.ts` 类型收紧第四批（最后一批）:
  ① `closestFrom` 加泛型 -> 按钮/输入框的 `.disabled`/`.value` 不再报错；
  ② `querySelector` 出来的输入框断言成 HTMLInputElement；
  ③ `run({preventDefault})` —— 形参按**真实用法**收窄（它只用到 preventDefault，不是完整 Event）。

用法: py -3.13 tools/_fix_app_dom_types4.py [--apply]
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "static", "app.ts")

# 变量名 -> 该变量实际是什么元素（按它被怎么用判定）
ELEMENT_OF = {
    "tb": "HTMLButtonElement",     # 「保存」按钮
    "ab": "HTMLButtonElement",
    "b": "HTMLButtonElement",
    "pb": "HTMLElement",           # 那颗 ＋（只读 data-* 属性）
    "nfa": "HTMLElement",
    "tl": "HTMLElement",
}


def main():
    apply = "--apply" in sys.argv
    t = io.open(P, encoding="utf-8").read()
    n1 = n2 = n3 = 0

    # ① 助手加泛型
    t = t.replace("function closestFrom(t: EventTarget | null, sel: string): Element | null {",
                  "function closestFrom<T extends Element = Element>(t: EventTarget | null, sel: string): T | null {")
    t = t.replace("  return el && typeof el.closest === 'function' ? el.closest(sel) : null;",
                  "  return el && typeof el.closest === 'function' ? (el.closest(sel) as T | null) : null;")
    n1 += 1

    # ② 调用点按用途给泛型
    for var, ty in ELEMENT_OF.items():
        pat = r"var %s = closestFrom\(ev\.target, ('[^']+')\);" % var
        if re.search(pat, t):
            t = re.sub(pat, lambda m, v=var, y=ty: "var %s = closestFrom<%s>(ev.target, %s);" % (v, y, m.group(1)), t, count=1)
            n2 += 1

    # ③ querySelector 的输入框
    for pat, rep in (
        (r"var tin = tbox\.querySelector\('\.at-tags'\);", "var tin = tbox.querySelector('.at-tags') as HTMLInputElement;"),
        (r"var tmsg = tbox\.querySelector\('\.al-msg'\);", "var tmsg = tbox.querySelector('.al-msg') as HTMLElement;"),
        (r"var almsg = ([A-Za-z0-9_.\[\]]+)\.querySelector\('\.al-msg'\);", r"var almsg = \1.querySelector('.al-msg') as HTMLElement;"),
        (r"var aurl = ([A-Za-z0-9_.\[\]]+)\.querySelector\('\.al-url'\);", r"var aurl = \1.querySelector('.al-url') as HTMLInputElement;"),
    ):
        if re.search(pat, t):
            t = re.sub(pat, rep, t)
            n3 += 1

    # ④ run() 的形参按真实用法（只需要 preventDefault）
    t = re.sub(r"function run\(e\?: Event\) \{",
               "function run(e?: { preventDefault: () => void }) {", t)

    if apply:
        io.open(P, "w", encoding="utf-8", newline="\n").write(t)
        print(f"  已写入: 助手泛型 {n1} · 调用点 {n2} · querySelector 断言 {n3} · run() 形参")
    else:
        print(f"  会改: 助手泛型 {n1} · 调用点 {n2} · 断言 {n3} · run() 形参（加 --apply 才写）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

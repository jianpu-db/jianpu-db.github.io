# -*- coding: utf-8 -*-
r"""`static/app.ts` 类型收紧第三批：把"事件委托"那几处写成一个typed 助手。

原来每处都是这个形状（JS 时代为了兼容没有 `closest` 的老浏览器）:
    var nfa = ev.target && ev.target.closest ? ev.target.closest('a.nf-add') : null;
TS 下 `ev.target` 是 `EventTarget`，它**没有** `closest` -> 每处都报 TS2339。

统一收成一个助手（断言只写一次、意图更清楚）:
    function closestFrom(t: EventTarget | null, sel: string): Element | null
    var nfa = closestFrom(ev.target, 'a.nf-add');

顺带把 `querySelector(...)` 出来的 `Element` 在使用 `.value/.disabled/.focus` 时断言成对应元素类型。

用法: py -3.13 tools/_fix_app_dom_types3.py [--apply]
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "static", "app.ts")

HELPER = '''
/** 从事件目标往上找最近的祖先元素。
 *
 * 为什么要有它: `EventTarget` 上没有 `closest`，而事件委托全靠它 —— 原来每处都写成
 * `ev.target && ev.target.closest ? ev.target.closest(sel) : null`，TS 下每处都报错。
 * 收成一处断言，调用点也更短（老浏览器没有 closest 的情况仍然兜住）。
 */
function closestFrom(t: EventTarget | null, sel: string): Element | null {
  const el = t as Element | null;
  return el && typeof el.closest === 'function' ? el.closest(sel) : null;
}
'''

# 把 `var X = ev.target && ev.target.closest ? (ev.target as Element).closest('SEL') : null;` 收成助手调用
PAT = re.compile(
    r"var (\w+) = ev\.target && ev\.target\.closest \? \(ev\.target as Element\)\.closest\('([^']+)'\) : null;")


def main():
    apply = "--apply" in sys.argv
    t = io.open(P, encoding="utf-8").read()

    if "function closestFrom" not in t:
        t = t.replace("function $<T extends HTMLElement", HELPER.strip() + "\n\nfunction $<T extends HTMLElement", 1)

    t, n1 = PAT.subn(lambda m: "var %s = closestFrom(ev.target, '%s');" % (m.group(1), m.group(2)), t)

    # querySelector 的结果按用途断言
    n2 = 0
    for pat, rep in (
        (r"var pin = row\.querySelector\('\.al-url'\);", "var pin = row.querySelector('.al-url') as HTMLInputElement | null;"),
        (r"var val = row\.querySelector\('\.attr-val'\);", "var val = row.querySelector('.attr-val') as HTMLInputElement | null;"),
        (r"var qbox = document\.querySelector\('\.alrow'\);", "var qbox = document.querySelector('.alrow') as HTMLElement | null;"),
    ):
        if re.search(pat, t):
            t = re.sub(pat, rep, t)
            n2 += 1

    if apply:
        io.open(P, "w", encoding="utf-8", newline="\n").write(t)
        print(f"  已写入: 助手 + 改写 {n1} 处事件委托" + (f" + {n2} 处 querySelector 断言" if n2 else ""))
    else:
        print(f"  会改: 助手 + {n1} 处委托" + (f" + {n2} 处断言" if n2 else "") + "（加 --apply 才写）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

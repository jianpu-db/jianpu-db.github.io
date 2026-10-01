# -*- coding: utf-8 -*-
r"""把 `static/app.ts` 剩下的 19 处类型报错修掉（第二批，同样是机械且可解释的）。

剩下的四类:
  ① `SITE_LABELS[i][0].test(u)` —— 数组字面量被推成 `(string|RegExp)[][]`，得给它一个类型
     `type SiteLabel = [RegExp, string]`；
  ② `ev.target.closest` —— 事件目标是 `EventTarget`（不是 Element），断言一下；
  ③ `pin.placeholder / pin.focus()` —— `querySelector` 返回 `Element`，要 input；
  ④ `esc(t('tied', { n }))` 说"要 2 个参数"、`run()` 说"要 1 个参数" —— 都是**宽松配置下**仍会报的
     实参类型问题: `t()` 的第二参我声明成了 `Record<string,string>`（对象字面量给的是 number），
     而 `run(e)` 的形参没写可选。两处都按真实用法写清楚。

用法: py -3.13 tools/_fix_app_dom_types2.py [--apply]
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "static", "app.ts")


def main():
    apply = "--apply" in sys.argv
    t = io.open(P, encoding="utf-8").read()
    orig = t
    n = 0

    # ① SITE_LABELS 加类型（顺带把 type 声明放在它前面）
    if "type SiteLabel" not in t:
        t = t.replace("var SITE_LABELS = [",
                      "/** [认领正则, 站名] —— 从 URL 的 host 认站，不靠人的命名习惯 */\n"
                      "type SiteLabel = [RegExp, string];\nvar SITE_LABELS: SiteLabel[] = [", 1)
        n += 1

    # ② 事件目标的 closest（变量名有 e / ev 两种）
    for var in ("ev", "e"):
        pat = r"\b%s\.target\.closest\(" % var
        cnt = len(re.findall(pat, t))
        if cnt:
            t = re.sub(pat, "(%s.target as Element).closest(" % var, t)
            n += cnt
    # ②b `tb.closest(...)`：tb 来自上面那行，已经断言过了，不再报；这里只处理显式 target

    # ③ querySelector 出来的 Element -> input
    t = t.replace("if (pin) { pin.placeholder =", "if (pin) { (pin as HTMLInputElement).placeholder =")
    t = t.replace("pb.getAttribute('data-ph') || 'https://…'; pin.focus(); }",
                  "pb.getAttribute('data-ph') || 'https://…'; (pin as HTMLInputElement).focus(); }")
    n += 1

    # ④ t() 的第二参按真实用法放宽；run() 的形参可选
    t = t.replace("function t(key, vars) {", "function t(key: string, vars?: Record<string, unknown>): string {")
    t = t.replace("function run(e) {", "function run(e?: Event) {")
    n += 2
    t = t.replace("return String(s).replace(/\\{(\\w+)\\}/g, function (m, k) { return (vars && vars[k] != null) ? vars[k] : m; });",
                  "return String(s).replace(/\\{(\\w+)\\}/g, function (m, k) {\n"
                  "    return (vars && vars[k] != null) ? String(vars[k]) : m;\n  });")

    if apply:
        io.open(P, "w", encoding="utf-8", newline="\n").write(t)
        print("  已写入 static/app.ts（%d 类改动）" % n)
    else:
        print("  会改 %d 类（加 --apply 才写）" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""把剩下几处**动态** `import('../static/x.js')` 也改成 `_built.mjs` 的 `importStatic('x')`。

背景: B 阶段前端源码改成 `static/*.ts` 之后，Node 不能直接 import TS。静态 import 已由
`_migrate_tool_imports.py` 处理；剩下这几个脚本用的是 `await import('../static/x.js')`
（动态形式），得单独改。改完每个脚本都必须跑一遍验证。

用法: py -3.13 tools/_migrate_dynamic_imports.py [--apply]
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
FILES = ("check_page.mjs", "check_render.mjs", "check_tune.mjs", "dbg_query.mjs")


def main():
    apply = "--apply" in sys.argv
    for name in FILES:
        p = os.path.join(TOOLS, name)
        if not os.path.exists(p):
            continue
        t = io.open(p, encoding="utf-8").read()
        orig = t
        # ① 解构式: const { a, b } = await import('../static/x.js');
        t = re.sub(r"const \{([^}]+)\} = await import\('\.\./static/(\w+)\.js'\);",
                   lambda m: "const {%s} = await importStatic('%s');" % (m.group(1), m.group(2)), t)
        # ② 纯副作用式（只为了装载 DOM 行为）: await import('../static/app.js');
        t = re.sub(r"await import\('\.\./static/(\w+)\.js'\);",
                   lambda m: "await importStatic('%s');" % m.group(1), t)
        if t != orig and "from './_built.mjs'" not in t:
            lines = t.splitlines(keepends=True)
            idx = max((i for i, l in enumerate(lines) if l.startswith("import ")), default=-1)
            lines.insert(idx + 1, "import { importStatic } from './_built.mjs';\n")
            t = "".join(lines)
        if t != orig:
            print(f"  {'改' if apply else '会改'} {name}")
            if apply:
                io.open(p, "w", encoding="utf-8", newline="\n").write(t)
        else:
            print(f"  -  {name} 无需改动（没有 ../static/*.js 的动态 import）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

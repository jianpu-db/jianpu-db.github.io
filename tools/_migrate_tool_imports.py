# -*- coding: utf-8 -*-
"""把站点仓库里"import static/*.js"的工具改成走 `_built.mjs`（B 阶段 TypeScript 化后的机械替换）。

为什么需要: 前端源码从 `static/*.js` 变成 `static/*.ts` 之后，Node 不能直接 import TS。
所有检查/调试脚本都改成:
    import { importStatic } from './_built.mjs';
    const { buildIndex, search } = await importStatic('search');
    const { parseQuery } = await importStatic('jptok');
这样它们测的就**是**线上那份逻辑（esbuild 只做类型擦除），而且脚本自己会按需转译。

用法: py -3.13 tools/_migrate_tool_imports.py [--apply]
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
# 只动这些"检查/调试"脚本；`_built.mjs` 自己不在此列
SKIP = {"_built.mjs", "_migrate_tool_imports.py"}


def main():
    apply = "--apply" in sys.argv
    changed = 0
    for name in sorted(os.listdir(TOOLS)):
        if not name.endswith(".mjs") or name in SKIP:
            continue
        p = os.path.join(TOOLS, name)
        t = io.open(p, encoding="utf-8").read()
        if "from '../static/" not in t:
            continue
        used = {}
        out_lines = []
        for line in t.splitlines(keepends=True):
            m = re.match(r"\s*import \{([^}]+)\} from '\.\./static/(\w+)\.js';\s*$", line)
            if m:
                names = ", ".join(x.strip() for x in m.group(1).split(",") if x.strip())
                used.setdefault(m.group(2), []).append(names)
                continue                     # 这一行删掉, 后面统一用 importStatic
            out_lines.append(line)
        new = "".join(out_lines)
        if not used:
            continue
        # 在最后一个顶层 import 之后插入 _built 的导入与 await importStatic 解构
        decl = ["import { importStatic } from './_built.mjs';\n"]
        for mod, groups in used.items():
            flat = ", ".join(" ".join(g.split()) for g in groups)
            decl.append(f"const {{ {flat} }} = await importStatic('{mod}');\n")
        # import 必须是顶层: 插到第一处 import 行之前（Node ESM 允许 import 任意位置，但可读性优先）
        idx = 0
        lines = new.splitlines(keepends=True)
        for i, line in enumerate(lines):
            if line.startswith("import "):
                idx = i
        lines[idx:idx] = decl
        new = "".join(lines)
        print(f"  {'改' if apply else '会改'} {name}: " +
              ", ".join(f"{k}<-{v}" for k, v in used.items()))
        if apply:
            io.open(p, "w", encoding="utf-8", newline="\n").write(new)
        changed += 1
    print(f"\n{'已改' if apply else '待改'} {changed} 个脚本" + ("" if apply else "（加 --apply 才写）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

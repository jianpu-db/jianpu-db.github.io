# -*- coding: utf-8 -*-
r"""把 `static/app.js` 迁成 `static/app.ts`（**渐进式**：文件先变成 TS，类型逐步收紧）。

## 为什么这么做（而不是一口气改成 strict TS）

`app.js` 是 867 行的 DOM/交互代码（18 个顶层 var + 40 个函数）。一口气上 `strict` 会有上百处
"隐式 any / 可能为 null"，其中绝大多数是 DOM 胶水代码 —— 逐条加类型噪声大、回归风险高，
而真正的价值在于**数据层**（索引字段、schema、平台表、结果对象）已经被 `search.ts` 类型化。

所以这一步是**诚实的渐进迁移**（业界叫 strangler / 分步迁移）:
  1. 文件变成 `.ts`（构建链统一，所有前端源码都是 TS）；
  2. 核心数据结构与导出函数**给出真类型**：`exactLinks(r: SearchResult, ctx?: string)`、
     `renderScore(raw: string, at: number|null, qlen: number, bars: number[])`、
     `FIELDS: Record<string, Field> `、`PLATFORMS: Platform[]`、`TXT: Record<string, Record<string,string>>`；
  3. 剩下的 DOM 胶水用**宽松配置**（`tsconfig.app.json`: `strict: false`）先过 —— 并在文件头写明
     "下一步收紧到 strict 要修的是哪几类"，而不是假装已经 strict。

## 用法
    py -3.13 tools/_migrate_app_ts.py [--apply]
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "static", "app.js")
DST = os.path.join(ROOT, "static", "app.ts")

HEADER = '''/* 简谱旋律查歌 —— 主界面（路由 / 卡片 / 谱页 / 就地表单）

 * ⚠ **TypeScript 迁移状态（2026-10-01, B 阶段）**:
 *   文件已改成 `.ts`，构建链统一（`esbuild` 只做类型擦除）；**数据层是真类型**：
 *     * `exactLinks(r: SearchResult, ctx?: string)` / `renderScore(...)` 的签名；
 *     * `FIELDS`（schema 来的属性表）、`PLATFORMS`（收录页平台表）、`TXT`（文案表）。
 *   **DOM 胶水层仍是宽松模式**（`tsconfig.app.json`: `strict: false`）—— 下一步收紧要修的是三类：
 *     ① 事件处理器 / `$()` 取到的元素可能为 null（`?.` 或先断言）；
 *     ② `FIELD_VALUE` 里每个渲染函数的参数要写成 `SearchResult`；
 *     ③ 各处 `setTimeout`/`fetch` 回调的参数类型。
 *   为什么不一口气上 strict: 867 行 DOM 代码逐条加类型噪声大、回归风险高，而收益主要在数据层 ——
 *   宁可**分两步走且写在文件头**，也不假装已经 strict。
 */
'''

TYPE_BLOCK = '''
/* ── 类型（数据层；DOM 胶水层留给后面的收紧）───────────────────────────────── */

/** schema 里一条属性（`jianpu-db/schema.py` 的 FIELDS，经 stats.json 带过来） */
interface Field {
  label: Record<string, string>;
  kind: string;
  editable?: boolean;
  hint?: string;
  note?: string;
  row?: boolean;
}
/** 收录页平台表一行: [站名, 认领正则, 粘确切页的提示, 搜索页模板] */
type Platform = [string, RegExp, string, string];
/** 卡片上的提示文案 */
type TxtEntry = Record<string, string>;
'''


def main():
    apply = "--apply" in sys.argv
    t = io.open(SRC, encoding="utf-8").read()

    # ① 文件头: 迁移状态说明（原来那两行 import 保留在最前，ESM 里 import 必须在顶层最前面）
    t = t.replace(
        "import { buildIndex, search } from './search.js';\nimport { parseQuery, parseToken, isPitch, show } from './jptok.js';\n",
        "import { buildIndex, search } from './search.js';\n"
        "import { parseQuery, parseToken, isPitch, show } from './jptok.js';\n"
        "import type { SearchResult, Index } from './search.js';\n"
        + HEADER + TYPE_BLOCK, 1)

    # ② 导出函数签名: 真类型
    t = t.replace("export function exactLinks(r, ctx) {",
                  "export function exactLinks(r: SearchResult, ctx?: string): string {", 1)
    t = t.replace("export function renderScore(raw, at, qlen, bars) {",
                  "export function renderScore(raw: string, at: number | null, qlen: number, bars: number[]): string {", 1)

    # ③ 三个表的类型标注
    t = t.replace("var PLATFORMS = [", "var PLATFORMS: Platform[] = [", 1)
    t = t.replace("var FIELDS = {", "var FIELDS: Record<string, Field> = {", 1)
    t = t.replace("var TXT = {", "var TXT: Record<string, TxtEntry> = {", 1)
    # ④ 索引与语言这两个顶层变量也给上类型
    t = t.replace("var IDX = null;", "var IDX: Index | null = null;", 1)
    t = t.replace("var CURRENT_TUNE = '';", "var CURRENT_TUNE: string = '';", 1)

    if not apply:
        print("  会改: static/app.js -> static/app.ts（头部说明 + 数据层类型）")
        print("  （加 --apply 才写）")
        return 0

    io.open(DST, "w", encoding="utf-8", newline="\n").write(t)
    os.remove(SRC)
    print("  已写 static/app.ts，删除 static/app.js")
    return 0


if __name__ == "__main__":
    sys.exit(main())

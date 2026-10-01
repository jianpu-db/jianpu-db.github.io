# -*- coding: utf-8 -*-
r"""修 `check_tune.mjs` 里那条**陈旧断言**（TS 迁移时发现）。

原断言（字面量）:
    ok(/class="meta"/.test(html) && /<th>歌手<\/th>/.test(html) && /<th>状态<\/th>/.test(html),
       '元数据表完整(歌手/状态都在)');
问题: schema 化之后表头是 `<th><span title="…schema 的 note…">歌手</span></th>`，
      于是 `/<th>歌手<\/th>/` **永远不可能匹配** —— 这条自检从改动那天起就一直红着（被当"已知失败"）。
修法: 先把 `<th>` 里的标签剥掉再比文字（既容得下包装，又真的在检查那两栏在不在）。

用法: py -3.13 tools/_fix_tune_assert.py [--apply]
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "tools", "check_tune.mjs")

NEW = """  // ⚠ 2026-10-01 修（TS 迁移时发现的**陈旧断言**）: 原来断言字面量 `<th>歌手</th>`，
  //   可是 schema 化之后表头是 `<th><span title="…schema 的 note…">歌手</span></th>`，
  //   于是这条**永远不可能通过**（一直红着被当成"已知失败"）。现在先把 `<th>` 里的标签剥掉
  //   再比文字 —— 既容得下包装，又真的在检查"歌手/状态这两栏在不在"。
  const thLabels = (h) => (h.match(/<th>[\\s\\S]*?<\\/th>/g) || [])
    .map((x) => x.replace(/<[^>]+>/g, '').trim());
  ok(/class="meta"/.test(html) && thLabels(html).includes('歌手') && thLabels(html).includes('状态'),
     '元数据表完整(歌手/状态都在)');"""


def main():
    apply = "--apply" in sys.argv
    t = io.open(P, encoding="utf-8").read()
    pat = re.compile(r"[ \t]*ok\(/class=\"meta\"/\.test\(html\)[^\n]*\n[^\n]*'元数据表完整[^\n]*\n")
    m = pat.search(t)
    if not m:
        print("没匹配到那条断言（可能已经改过）")
        return 1
    print("原断言:\n" + m.group(0).rstrip())
    out = t[:m.start()] + NEW + "\n" + t[m.end():]
    if apply:
        io.open(P, "w", encoding="utf-8", newline="\n").write(out)
        print("\n已替换")
    else:
        print("\n（加 --apply 才写）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

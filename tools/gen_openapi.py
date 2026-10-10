"""`node`/`py` 都行：把 `app/api.py` 的 OpenAPI 契约**导出成磁盘上的 JSON**。

    .venv/Scripts/python.exe tools/gen_openapi.py                  # -> openapi.json（仓库根）
    .venv/Scripts/python.exe tools/gen_openapi.py -o dist/openapi.json --base http://127.0.0.1:8775

为什么要单独一份（而不是每次 `curl /openapi.json`）：
  * **留痕**：契约进版本库，改动能在 diff 里看出来（"文档变了"不再是不可见的副作用）；
  * **可离线校验**：`tools/check_openapi.py` 拿这个文件去校验，不需要把服务起着；
  * **两份解释器也够用**：本仓库的 `.venv`（3.13）有 fastapi、装校验器的解释器可能没有，
    于是"生成"与"校验"分成两个脚本、用一个 JSON 文件交接。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))


def build(base: str | None = None) -> dict:
    import api  # noqa: PLC0415

    spec = api.app.openapi()
    if base:
        spec["servers"] = [{"url": base.rstrip("/"), "description": "由 tools/gen_openapi.py --base 覆盖"}]
    return spec


def main() -> int:
    ap = argparse.ArgumentParser(description="导出 FastAPI 的 OpenAPI 契约")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "openapi.json"),
                    help="输出文件（默认仓库根的 openapi.json）")
    ap.add_argument("--base", default=None, help="覆盖 servers[0].url（本地自检时指 127.0.0.1:8770/8775）")
    args = ap.parse_args()

    spec = build(args.base)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2, sort_keys=False)
        f.write("\n")
    paths = sorted(spec.get("paths") or {})
    print(f"✓ 写出 {args.out}")
    print(f"  openapi={spec.get('openapi')} title={spec.get('info', {}).get('title')!r} "
          f"servers={[s.get('url') for s in spec.get('servers') or []]}")
    print(f"  paths({len(paths)}): " + "、".join(paths))
    print(f"  schemas({len(spec.get('components', {}).get('schemas') or {})}): "
          + "、".join(sorted(spec.get("components", {}).get("schemas") or {})))
    ws2 = [p for p in paths if p.startswith("/ws/2")]
    print(f"  /ws/2/*（{len(ws2)} 条）: " + "、".join(ws2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

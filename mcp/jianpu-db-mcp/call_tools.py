# -*- coding: utf-8 -*-
"""真的用 MCP 客户端调一遍这三个工具（**证据脚本**，不是示例代码）。

    py -3.13 call_tools.py                     # 默认：离线（不联网，读包内索引）
    py -3.13 call_tools.py --remote http://127.0.0.1:8775    # 远程：等效于设 JIANPU_BASE

它做的事：用官方 SDK 的 stdio 传输**把 server.py 当子进程拉起来** -> `initialize` ->
`tools/list`（打印每个工具的名字与描述，看得出 AI 拿到的是什么）-> 依次 `tools/call` 三个工具，
把**真实返回**打出来。

默认**清掉继承来的 `JIANPU_*`**，所以跑的就是"一个客户端拿到这个包、什么都不配"的那条路；
要看远程行为就显式 `--remote`。MCP 这一层最容易"看着对、跑起来不对"（schema 生成、参数名、
stdio 握手、超时），只有真起进程调一次才知道。排错时先跑它，别去猜客户端。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))
GAP = 0.2          # 离线模式没有每秒 1 次的限流，不需要等；远程模式才需要慢慢来
REMOTE_GAP = 1.2


def show(title: str, text: str, limit: int = 1400) -> None:
    body = text if len(text) <= limit else text[:limit] + f"\n…（截断，全 {len(text)} 字符）"
    print(f"\n──── {title}\n{body}")


async def run(remote: str, gap: float) -> int:
    env = {k: v for k, v in os.environ.items() if not k.startswith("JIANPU_")}
    if remote:
        env["JIANPU_BASE"] = remote
    mode = f"remote {remote}" if remote else "offline（不设 JIANPU_*）"
    params = StdioServerParameters(
        command=sys.executable, args=[os.path.join(HERE, "server.py")], env=env, cwd=HERE,
    )
    calls = [
        ("search_melody", {"digits": "316316", "limit": 3}),
        ("search_melody", {"digits": "33565653253", "fuzzy": 1, "limit": 3}),
        ("get_song", {"id": "0932", "inc": ["artists", "tags", "sections"], "score_chars": 40}),
        ("stats", {}),
    ]
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            info = await session.initialize()
            print(f"✓ 握手成功：server={info.server_info.name} {info.server_info.version} "
                  f"protocol={session.protocol_version} 模式={mode}")
            tools = await session.list_tools()
            print(f"\n✓ tools/list 回了 {len(tools.tools)} 个工具：")
            for t in tools.tools:
                schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {}
                props = schema.get("properties") or {}
                print(f"  - {t.name}({', '.join(props)})  required={schema.get('required')}")
                first = (t.description or "").strip().splitlines()
                print(f"    描述首句：{first[0] if first else '(空描述)'}")

            bad = 0
            for name, args in calls:
                t0 = time.time()
                res = await session.call_tool(name, args)
                dt = time.time() - t0
                text = "".join(c.text for c in res.content if getattr(c, "type", "") == "text")
                ok = not res.is_error and not text.startswith(
                    ("接口", "连不上", "网络层", "参数不对", "没有这一条", "没认出"))
                bad += 0 if ok else 1
                show(f"{name}({json.dumps(args, ensure_ascii=False)})  ——  {dt:.2f}s，"
                     f"{'成功' if ok else '失败'}", text)
                await asyncio.sleep(gap)
            print(f"\n{'✓ 三个工具都跑通' if not bad else f'✗ 有 {bad} 个失败'}")
            return 1 if bad else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--remote", default="", help="走远程：给一个基址（等效 JIANPU_BASE）")
    a = ap.parse_args()
    raise SystemExit(asyncio.run(run(a.remote, REMOTE_GAP if a.remote else GAP)))

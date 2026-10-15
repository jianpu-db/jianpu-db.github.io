# -*- coding: utf-8 -*-
"""**离线断言**（证据脚本）：网络不可用时，三个工具**照常**工作 —— 贴真实返回。

三个场景都真起 MCP 进程（官方 SDK 的 stdio 客户端，与客户端里跑的是同一条路径）：

  A. **默认离线 + socket 被禁用**：`tests/_no_net.py` 把 `socket.socket` 打死再起 server.py，
     环境里不设 `JIANPU_BASE`。三个工具还能回结果 => 一个请求都没发。
  B. **设了 `JIANPU_BASE=http://127.0.0.1:1`（一个必定连不上的地址）但 `JIANPU_MODE=offline`**：
     走离线，同样照常工作。
  C. **反面对照**：`JIANPU_BASE=http://127.0.0.1:1` 且不设 `JIANPU_MODE`（= 远程模式）：
     应当**失败**并把原因翻成人话 —— 证明"开关真的有效、HTTP 那一层真的被跳过/走到了"。

    py -3.13 tests/offline_assert.py             # 跑 A/B/C
    py -3.13 tests/offline_assert.py --which A   # 只跑一个
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
SERVER = os.path.join(PKG, "server.py")
NO_NET = os.path.join(HERE, "_no_net.py")

CALLS = [
    ("search_melody", {"digits": "316316", "limit": 3}),
    ("get_song", {"id": "0932"}),
    ("stats", {}),
]


def show(title: str, text: str, limit: int = 2600) -> None:
    body = text if len(text) <= limit else text[:limit] + f"\n…（输出已截断，全 {len(text)} 字符）"
    print(f"\n──── {title}\n{body}")


async def scenario(name: str, env_extra: dict, guarded: bool, expect: dict) -> bool:
    env = {k: v for k, v in os.environ.items() if not k.startswith("JIANPU_")}
    env.update(env_extra)
    args = ([NO_NET, SERVER] if guarded else [SERVER])
    params = StdioServerParameters(command=sys.executable, args=args, env=env, cwd=PKG)
    print(f"\n════════ 场景 {name}：{'网络出口堵死 + ' if guarded else ''}"
          f"{'、'.join(f'{k}={v}' for k, v in env_extra.items()) or '（不设 JIANPU_*）'}")
    ok_all = True
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            info = await session.initialize()
            print(f"✓ 握手：{info.server_info.name} {info.server_info.version} "
                  f"protocol={session.protocol_version}")
            tools = await session.list_tools()
            print(f"✓ tools/list：{[t.name for t in tools.tools]}")
            for name_, args_ in CALLS:
                res = await session.call_tool(name_, args_)
                text = "".join(c.text for c in res.content if getattr(c, "type", "") == "text")
                looks_error = text.startswith(("接口", "连不上", "网络层", "参数不对",
                                               "没有这一条", "没认出"))
                got_ok = not res.is_error and not looks_error
                want_ok = expect.get(name_, True)
                ok = got_ok == want_ok
                ok_all &= ok
                show(f"{name_}({json.dumps(args_, ensure_ascii=False)}) —— "
                     f"{'成功' if got_ok else '失败'}"
                     f"{'' if ok else f'（✗ 预期{"成功" if want_ok else "失败"}）'}", text)
    print(f"── 场景 {name}：{'符合预期' if ok_all else '✗ 与预期不符'}")
    return ok_all


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--which", default="ABC")
    a = ap.parse_args()
    cases = {
        # 默认离线：不设任何 JIANPU_*，并且网络出口被堵死
        "A": ("A 默认离线（网络出口已堵死，必不联网）", {}, True, {}),
        # 用户建议的那种做法：基址设成连不上的地址 + 强制离线
        "B": ("B 强制离线（JIANPU_BASE 指向死地址 + JIANPU_MODE=offline）",
              {"JIANPU_BASE": "http://127.0.0.1:1", "JIANPU_MODE": "offline"}, False, {}),
        # 反面对照：不强制 -> 远程模式 -> 两个要打接口的工具必定失败（证明开关真的管事）；
        # `stats()` 例外：它在站点仓库里找得到本地 stats.json，所以照旧报得出来（老行为，不是 bug）
        "C": ("C 反面对照（远程模式 + 连不上的基址：打接口的两个工具预期失败）",
              {"JIANPU_BASE": "http://127.0.0.1:1"}, False,
              {"search_melody": False, "get_song": False, "stats": True}),
    }
    good = True
    for key in a.which.upper():
        if key not in cases:
            continue
        name, env_extra, guarded, expect = cases[key]
        good &= await scenario(name, env_extra, guarded, expect)
    print("\n" + ("✓ 离线断言全部符合预期" if good else "✗ 有场景不符合预期"))
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

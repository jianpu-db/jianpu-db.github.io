# -*- coding: utf-8 -*-
"""离线断言：**把出网的出口堵死**再起 `server.py` —— 三个工具照样工作，就证明它没联网。

    py -3.13 tests/_no_net.py server.py          # 手动起（stdio）

做法：在 import `server.py` **之前**装一层 socket 守卫：

  * `connect`/`connect_ex`/`sendto`：**非 loopback 一律抛** `NoNetError`；
  * `getaddrinfo`（DNS）与 `create_connection`：一律抛（真要联网就必须先过这两关）；
  * **loopback 放行并记账** —— 因为 CPython 在 Windows 上的 asyncio 事件循环会用
    `socket.socketpair()` 造一对自管道，而它是用 TCP 环回模拟的；那一次 `connect` 是
    **解释器自己**的（目标是刚 bind 出来的临时端口），不是我们要证的"请求"。
    所以退一步：放行 loopback，但把每一次都记下来。离线模式如果偷着打
    `http://127.0.0.1:1` 那种死地址，这里会多出一条 `('127.0.0.1', 1)`，一眼就能看见。

⚠ 为什么不是"把 `socket.socket` 换成一个普通函数"：`ssl.py` 里写着 `class SSLSocket(socket)`，
`mcp` 的依赖链会 import 到它 —— 换掉基类会让**解释器在 import 阶段就崩**，那是装置的毛病、
不是被测对象的毛病。所以用**子类**（基类还是真的 type），只在"真正要出网"的动作上炸。

进程退出时把记账打到 stderr：被拦下的出网调用次数 + 放行过的 loopback 连接列表。
"""
from __future__ import annotations

import atexit
import json
import os
import runpy
import sys

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLOCKED = {"connect": 0, "connect_ex": 0, "sendto": 0, "create_connection": 0, "getaddrinfo": 0}
ALLOWED: list = []
LOOPBACK = ("127.0.0.1", "::1", "localhost", "0.0.0.0", "")


class NoNetError(RuntimeError):
    """本进程已禁用出网（离线断言用）。"""


def _is_loopback(address) -> bool:
    try:
        host = address[0] if isinstance(address, (tuple, list)) and address else address
    except Exception:
        host = ""
    return str(host) in LOOPBACK


def install_guard() -> None:
    import socket

    real_socket = socket.socket

    def _trip(what: str):
        def _blocked(*a, **k):
            BLOCKED[what] = BLOCKED.get(what, 0) + 1
            raise NoNetError(f"本进程已禁用出网（离线断言）：{what} 被调用")
        return _blocked

    class _NoNetSocket(real_socket):        # 子类：`class SSLSocket(socket)` 这类写法照常可用
        def connect(self, address, *a, **k):
            if _is_loopback(address):
                ALLOWED.append(("loopback", str(address[0]), int(address[1])
                                if len(tuple(address)) > 1 else 0))
                return real_socket.connect(self, address, *a, **k)
            BLOCKED["connect"] += 1
            raise NoNetError(f"本进程已禁用出网（离线断言）：connect({address!r})")

        def connect_ex(self, address, *a, **k):
            if _is_loopback(address):
                ALLOWED.append(("loopback", str(address[0]), int(address[1])
                                if len(tuple(address)) > 1 else 0))
                return real_socket.connect_ex(self, address, *a, **k)
            BLOCKED["connect_ex"] += 1
            raise NoNetError(f"本进程已禁用出网（离线断言）：connect_ex({address!r})")

        sendto = _trip("sendto")

    socket.socket = _NoNetSocket                              # type: ignore[misc]
    socket.create_connection = _trip("create_connection")     # type: ignore[assignment]
    socket.getaddrinfo = _trip("getaddrinfo")                 # type: ignore[assignment]


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--no-net-guard")]
    script = args[0] if args else os.path.join(PKG, "server.py")
    if not os.path.isabs(script):
        script = os.path.join(PKG, script)
    install_guard()
    atexit.register(lambda: print("[no-net] 被拦下的出网调用：" + json.dumps(BLOCKED)
                                  + "；放行过的 loopback 连接：" + json.dumps(ALLOWED),
                                  file=sys.stderr, flush=True))
    print(f"[no-net] 出网已堵死（loopback 放行并记账），起 {script}", file=sys.stderr, flush=True)
    sys.argv = [script] + args[1:]
    sys.path.insert(0, os.path.dirname(script))
    runpy.run_path(script, run_name="__main__")


if __name__ == "__main__":
    main()

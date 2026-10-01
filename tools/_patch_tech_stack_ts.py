# -*- coding: utf-8 -*-
"""给 `docs/TECH_STACK.md` 补 B 阶段的进展（Worker TypeScript / 上游探活）与第 8 条 STAR。

为什么用脚本而不是手改: 这两处文档改动要**幂等**（跑两次结果一样）、**可校验**（锚点找不到就报错而不是静默不改）。
第一版用 PowerShell here-string 直接改，锚点没匹配也没报出来（静默失败），所以改成这个脚本 + 断言。

用法: py -3.13 tools/_patch_tech_stack_ts.py [--apply]
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "docs", "TECH_STACK.md")

ROWS = """| **Worker 用 TypeScript**（2026-10-01） | 边缘代码负责路由/鉴权/注入，出错就是"整站不可用"；类型能挡住一整类错误 | 入口 `worker/index.ts`（`wrangler` 自己 esbuild 打包）；单独 `tsconfig.worker.json`（`@cloudflare/workers-types`，不能和 DOM lib 混） |
| **`/api/health` 探上游（`upstreamOk`）** | 快速隧道会**悄悄死掉**而 secret 仍在 —— 只报 `api:true` 会骗人 | 2026-10-01 实测: 隧道 `Error 1016`，health 却一直 `api:true`，看护几小时没报；现在健康检查真去敲一下上游（3 s 超时） |
"""

STAR = """### 8. 隧道悄悄死了，而监控说"一切正常"（可观测性那一课）

* **S**：线上投稿忽然全挂 —— Cloudflare 回 `Error 1016 Origin DNS error`（快速隧道域名解析不到）。
* **A**：先重建隧道恢复服务；再改**判据**：`/api/health` 增加真去敲上游的 `upstreamOk`（3 s 超时），
  看护脚本从"看 `api`（＝secret 配了没）"改成"看 `api && upstreamOk`"。
* **R**：一条 `curl https://jianpu-db.org/api/health` 现在能区分"没配"和"配了但对方死了"；
  **教训：监控不能只查配置在不在，要查上游活不活。** 同一天我加的"GPU 让路闸"也自锁过一次
  （计划任务的子进程查到"自己在 Running"于是永远等），同样用"上限兜底 + 环境标记"修掉 —— 两件事一个道理：
  **任何"等待/健康判断"都必须有退路。**

"""


def main():
    apply = "--apply" in sys.argv
    t = io.open(P, encoding="utf-8").read()
    changed = []

    if "Worker 用 TypeScript" not in t:
        anchor = "| **`assets.run_worker_first`** |"
        assert anchor in t, "找不到 run_worker_first 那行（文档结构变了？）"
        t = t.replace(anchor, ROWS + anchor, 1)
        changed.append("边缘表格加了两行（Worker TS / upstreamOk）")

    if "### 8. 隧道悄悄死了" not in t:
        anchor2 = "## 五、常见追问与答法（面试 Q&A）"
        assert anchor2 in t, "找不到 Q&A 锚点"
        t = t.replace(anchor2, STAR + anchor2, 1)
        changed.append("加了第 8 条 STAR（可观测性）")

    if not changed:
        print("已经是新版，无需改动")
        return 0
    for c in changed:
        print("  ✓ " + c)
    if apply:
        io.open(P, "w", encoding="utf-8", newline="\n").write(t)
        print("  已写入", P)
    else:
        print("  （加 --apply 才写）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

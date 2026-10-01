#!/usr/bin/env bash
# 一键跑完前端/接口的全部自检。用法:
#     bash tools/check_all.sh                  # 打本机 127.0.0.1:8770
#     bash tools/check_all.sh http://127.0.0.1:8903
#     JIANPU_DB=... bash tools/check_all.sh    # 口径跟着 DB 走(check_submit.py 用)
#
# 每组自检都只读(不写语料、不发投稿)。要连真接口一起打, 用:
#     python3 tools/check_submit.py --live <**隔离实例**的 URL>
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
WEB="$(dirname "$HERE")"
URL="${1:-http://127.0.0.1:8770}"
cd "$WEB"
fail=0
run() { echo; echo "=== $* ==="; "$@" || { echo "!! 上面这组失败了"; fail=1; }; }
run python3 tools/check_submit.py
run python3 tools/check_images_index.py
run node tools/check_render.mjs "$URL"
run node tools/check_page.mjs "$URL"
run node tools/check_search.mjs "$URL"
run node tools/check_ui.mjs "$URL"
run node tools/check_tune.mjs "$URL"
run node tools/check_live.mjs "$URL"
run python3 tools/check_docs_numbers.py       # 文档里的数字必须与语料实测一致(简历材料别对不上账)
# 这三条**不依赖本地服务**（纯算/纯 import），所以放这儿也不会被端口问题牵连:
#   * 剪枝等价性: 同一批查询"开/关 ngram 剪枝"的最终卡片必须逐条相同（60 条含模糊与多段）
#   * TS↔Wasm 对拍: 逐首 (cost, at) + 端到端结果（缺工具仓库产物时脚本会明确跳过）
#   * Worker 路由: 缺失资源必须 404、页面路由仍兜底 index.html
#     —— 本地静态服务走不到 SPA 兜底那条路，这条只能靠直接 import worker 来测
run node tools/check_prune_parity.mjs 60
run node tools/check_wasm_parity.mjs
run node tools/check_worker_routes.mjs
# 归组重复是**报告**不是门槛（它报的是"用户可见的重复"，那是产品决策不是 bug），所以只打印:
echo; echo "=== 归组重复报告（非门槛）==="
node tools/check_dup_groups.mjs 2>&1 | tail -n 6
echo; echo "=== 精确重复旋律报告（非门槛）==="
node tools/check_dup_melody.mjs 2>&1 | tail -n 8
# 前端口径的召回评测: 同一份基准跑**上线的 search.ts**（基准在工具仓库，没有就自己跳过）。
# 为什么单独有它: 离线 Python 评测验不到 TS 侧 —— 剪枝最坏就是"把目标歌剪掉"，离线口径看不出来。
echo; echo "=== 前端口径召回（基准在工具仓库, 没有就跳过）==="
node tools/check_recall.mjs 2>&1 | tail -n 6
# 排名金标准: 12 条"查询取自目标歌自己的谱"的客观用例（CI 也跑这条）
echo; echo "=== 排名金标准 ==="
node tools/check_ranking_golden.mjs 2>&1 | tail -n 3
# 前端 jptok.js 与 Python 侧 jptok.py 的**token 口径**等价性(第三份口径的锁, 见 check_jptok_parity.sh)。
# 需要 jianpu2/jianpu-db 就在旁边; 扫全语料约 30s, JIANPU_QUICK=1 时跳过。
ROOT="$(dirname "$WEB")"
if [ "${JIANPU_QUICK:-0}" = "1" ]; then
  echo; echo "=== jptok JS/Python 等价性 === (跳过: JIANPU_QUICK=1)"
elif [ -f "$ROOT/jianpu2/tools/dump_jptok_tokens.py" ]; then
  run bash tools/check_jptok_parity.sh
else
  echo; echo "=== jptok JS/Python 等价性 === (跳过: 旁边没有 jianpu2)"
fi
# GitHub Pages(纯静态托管)那条路: 子路径 + 404.html 回退 + 只读, 与 Cloudflare 那套**不一样**,
# 所以单列一组(构建 + 产物断言 + 真浏览器模拟 Pages 规矩)。
run bash tools/check_gh_pages.sh
# 真浏览器那一步: 假 DOM 证明不了"图真的解码出来/点了能跳/刷新还在"。没装 geckodriver 就跳过。
if command -v geckodriver >/dev/null 2>&1 || command -v firefox.geckodriver >/dev/null 2>&1; then
  run python3 tools/browser_check.py spa "$URL"
  run python3 tools/browser_check.py subdir      # 子目录部署(静态托管那样)也别坏
else
  echo; echo "=== 浏览器交互自检 === (跳过: 没装 geckodriver)"
fi
# Cloudflare Worker 本地自检（真 workerd + 本地 R2）。需要 npm install 过 wrangler;
# 比较慢(约 1.5 分钟), JIANPU_QUICK=1 时跳过。
if [ -x node_modules/.bin/wrangler ] && [ "${JIANPU_QUICK:-0}" != "1" ]; then
  run bash tools/check_worker.sh
else
  echo; echo "=== Worker 本地自检 === (跳过: $([ -x node_modules/.bin/wrangler ] && echo 'JIANPU_QUICK=1' || echo '没装 wrangler, 跑 npm install'))"
fi
echo
[ "$fail" = 0 ] && echo "全部自检通过 —— $URL" || echo "有自检失败, 见上面 !! 处"
exit $fail

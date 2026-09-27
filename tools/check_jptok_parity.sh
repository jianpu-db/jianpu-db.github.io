#!/usr/bin/env bash
# 前端 `static/jptok.js` 与 **Python 侧唯一真源** `jianpu2/.../jptok.py` 的等价性测试(一条命令):
#   ① Python 侧把全语料里每一个不同的 token 的判定导出成 TSV
#   ② 在 JS 侧逐个重比 is_note / is_pitch / beat
#
# 为什么需要: 同一个"简谱 token 口径"现在有**三份**实现 —— jptok.py(真源)、
# `jianpu-db/score.py` 的内置兜底、以及本仓的 `static/jptok.js`。前两份有
# `jianpu2/tools/check_jptok_parity.py` 锁着, **第三份以前没人管**, 于是它真的漂了:
# 2026-09-28 实测 `BEAT` 表里写着 `h: 2.0`(Python 侧 2026-09-24 已定案 h = 六十四分音符 = 0.0625),
# 而 jptok.js 自己的文件头就写着"改这里时必须同时改 Python 侧"。
# 反向验证过这道锁不是恒真的: 把 h 改回 2.0, 它会报 799 处不一致(含真音符 `h#1`)。
#
# 与 check_parity.sh 的分工: 那边锁的是**检索代价模型**(search.js), 这边锁的是 **token 口径**。
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
WEB="$(dirname "$HERE")"
ROOT="$(dirname "$WEB")"
DUMP="$ROOT/jianpu2/train-work/jptok_tokens.tsv"
echo "=== ① Python 侧导出全语料 token 判定 ==="
python3 "$ROOT/jianpu2/tools/dump_jptok_tokens.py" "$DUMP" || exit 1
echo
echo "=== ② JS 侧逐个重比 ==="
node "$HERE/check_jptok_js.mjs" "$DUMP"

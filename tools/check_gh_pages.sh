#!/usr/bin/env bash
# GitHub Pages（纯静态托管）那条路的自检。
#
# 为什么单列一组: Cloudflare 那条路有 Worker 兜底（SPA 回退、/api、/img），GitHub Pages 什么都没有 ——
# 站点在**子路径** `/jianpu-web/` 下、未知路径由 **404.html**（HTTP 404）接、没有写回服务。
# 这三条差异任何一条弄错, 线上就是白屏或"投稿点了没反应", 而本机 `dist/` + SPA 回退那套**测不出来**。
#
#     bash tools/check_gh_pages.sh          # 构建 + 产物断言 + 真浏览器(GitHub Pages 模拟)
#     JIANPU_QUICK=1 bash tools/check_gh_pages.sh   # 跳过真浏览器那步
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
WEB="$(dirname "$HERE")"
cd "$WEB"
fail=0
ck() { if [ "$1" = 0 ]; then echo "✓ $2"; else echo "✗ $2"; fail=1; fi; }

echo "=== 构建 gh 产物 ==="
rm -rf dist-gh
node tools/build_dist.mjs --target gh || exit 1

echo
echo "=== 产物断言（GH Pages 规矩） ==="
[ -f dist-gh/.nojekyll ];  ck $? "有 .nojekyll（否则 Jekyll 会插手）"
[ ! -e dist-gh/_headers ]; ck $? "不带 Cloudflare 的 _headers"
cmp -s dist-gh/404.html dist-gh/index.html; ck $? "404.html 与 index.html 逐字节相同"
grep -q 'window.JIANPU_READONLY=true;' dist-gh/index.html; ck $? "默认只读：注入了 JIANPU_READONLY"
[ -s dist-gh/data/index.json ] && [ -s dist-gh/data/stats.json ] && ls dist-gh/data/songs.part*.jsonl.gz >/dev/null 2>&1; ck $? "数据文件在（index.json + 分片 + stats.json）"
# index.html 引用的静态资源必须真的存在(带内容哈希, 名字对不上就是白屏)
# 只认 href/src 属性里的值 —— 别被注释里那句"相对路径会被解析成 /s/static/app.js"骗到。
refs=$(grep -oE '(href|src)="\./static/[^"]+"' dist-gh/index.html | sed -E 's/^[a-z]+="\.\///; s/"$//' | sort -u)
nrefs=0; miss=0
for r in $refs; do nrefs=$((nrefs + 1)); [ -f "dist-gh/$r" ] || { echo "  缺失: $r"; miss=1; }; done
[ "$nrefs" -ge 2 ] && [ "$miss" = 0 ]; ck $? "index.html 引用的静态资源都在（$nrefs 个, 带内容哈希）"
# 规范形式: 数据不能被算成"页面"(否则 404 回退会把 .gz 请求变成 HTML)
python3 - <<'PY'
import gzip, io, json, os, sys
# 语料在仓库里是**分片**(index.json + songs.partNN.jsonl.gz); 老产出可能有整份, 两种都认。
d = 'dist-gh/data'
n = 0
if os.path.exists(os.path.join(d, 'songs.jsonl.gz')):
    with gzip.open(os.path.join(d, 'songs.jsonl.gz'), 'rt', encoding='utf-8') as f:
        n = sum(1 for ln in f if ln.strip())
else:
    idx = json.load(io.open(os.path.join(d, 'index.json'), encoding='utf-8'))
    for p in idx['parts']:
        with gzip.open(os.path.join(d, p['file']), 'rt', encoding='utf-8') as f:
            n += sum(1 for ln in f if ln.strip())
st = json.load(io.open(os.path.join(d, 'stats.json'), encoding='utf-8'))
print("  语料 %d 首（stats 说 %s 首）" % (n, st.get('songs')))
sys.exit(0 if n == st.get('songs') and n > 7000 else 1)
PY
ck $? "产物里的语料与 stats.json 对得上"

echo
echo "=== 与 cf 产物互不污染 ==="
node tools/build_dist.mjs --target cf >/dev/null || exit 1
[ -f dist/_headers ] && [ ! -e dist/.nojekyll ] && [ ! -e dist/404.html ]
ck $? "cf 产物照旧（有 _headers、没有 404.html/.nojekyll）"
if grep -q 'JIANPU_READONLY' dist/index.html; then
  ck 1 "cf 产物**没有**只读开关（那边能投稿）"
else
  ck 0 "cf 产物**没有**只读开关（那边能投稿）"
fi

echo
echo "=== --api 形式：把写回指到 Worker（跨域镜像投稿用） ==="
rm -rf /tmp/jianpu-gh-api && node tools/build_dist.mjs --target gh --out /tmp/jianpu-gh-api --api https://example.pages.dev >/dev/null || exit 1
grep -q 'window.JIANPU_API="https://example.pages.dev";' /tmp/jianpu-gh-api/index.html && ! grep -q JIANPU_READONLY /tmp/jianpu-gh-api/index.html
ck $? "--api 注入接口地址且不再是只读"
rm -rf /tmp/jianpu-gh-api

echo
if [ "${JIANPU_QUICK:-0}" = "1" ]; then
  echo "=== 真浏览器（GitHub Pages 模拟） === (跳过: JIANPU_QUICK=1)"
elif command -v geckodriver >/dev/null 2>&1 || command -v firefox.geckodriver >/dev/null 2>&1; then
  python3 tools/browser_check.py ghpages || fail=1
else
  echo "=== 真浏览器（GitHub Pages 模拟） === (跳过: 没装 geckodriver)"
fi

echo
[ "$fail" = 0 ] && echo "GitHub Pages 自检 通过" || echo "GitHub Pages 自检 失败"
exit $fail

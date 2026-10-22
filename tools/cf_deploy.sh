#!/usr/bin/env bash
# 用**本机** wrangler 直接部署（不依赖 GitHub 连接的那条构建流水线）—— 排障时特别有用:
# 报错当场看得见、改完立刻重推, 不用等 Cloudflare 的构建队列。
#
#     npx wrangler login                  # 一次性（浏览器点一下授权）
#     bash tools/cf_deploy.sh             # 构建 + 部署 + 验证
#     bash tools/cf_deploy.sh --bucket    # 顺带建 R2 桶 jianpu-images（已存在会忽略）
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
WEB="$(dirname "$HERE")"
cd "$WEB"
BUCKET="${BUCKET:-jianpu-images}"
MAKE_BUCKET=0
[ "${1:-}" = "--bucket" ] && MAKE_BUCKET=1
fail=0
ok() { printf '%s %s\n' "$([ "$1" = 1 ] && echo '✓' || echo '✗')" "$2"; [ "$1" = 1 ] || fail=1; }

[ -x node_modules/.bin/wrangler ] || { echo "先装依赖: npm install"; exit 1; }
# ⚠ `wrangler whoami` 在没登录时**也返回 0**, 只是打印 "You are not authenticated" —— 必须看输出
WHO="$(npx wrangler whoami 2>&1)"
if echo "$WHO" | grep -qi "not authenticated"; then
  echo "还没登录: 先跑一次  npx wrangler login  （浏览器点一下授权）"
  exit 1
fi
echo "账号: $(echo "$WHO" | grep -E '│' | head -3 | tr -s ' ' | tr '\n' ' ')"

if [ "$MAKE_BUCKET" = 1 ]; then
  echo "== 建 R2 桶 $BUCKET（已存在会报错, 忽略即可）=="
  npx wrangler r2 bucket create "$BUCKET" 2>&1 | tail -3 || true
  npx wrangler r2 bucket list 2>&1 | grep -q "$BUCKET" && echo "  桶在 ✓" || echo "  ! 列表里没看到, 检查一下"
fi

echo "== 构建 dist =="
node tools/build_dist.mjs | tail -2 || exit 1

echo "== 部署 =="
OUT="$(npx wrangler deploy 2>&1)"
echo "$OUT" | tail -12
URL="$(echo "$OUT" | grep -oE 'https://[a-z0-9.-]+\.workers\.dev' | head -1)"
if [ -z "$URL" ]; then
  echo "!! 没从输出里认出 workers.dev 地址（看上面的报错）"
  exit 1
fi
echo
echo "== 从公网验证 $URL =="
sleep 3
for p in "/" "/api/health" "/data/index.json" "/s/qupu123-313063"; do
  c=$(curl -s -m 30 -o /tmp/_cd.out -w '%{http_code}' "$URL$p")
  ok "$([ "$c" = 200 ] && echo 1 || echo 0)" "$p -> $c"
done
echo "  /api/health: $(curl -s -m 20 "$URL/api/health")"
IMG=$(python3 - <<'PY'
import gzip, io, json, os, urllib.parse
# 语料在仓库里是分片(index.json + songs.partNN.jsonl.gz), 老本机可能有整份 —— 两种都认。
def songs():
    if os.path.exists('data/songs.jsonl.gz'):
        with gzip.open('data/songs.jsonl.gz', 'rt', encoding='utf-8') as f:
            for ln in f:
                yield ln
        return
    idx = json.load(io.open('data/index.json', encoding='utf-8'))
    for p in idx['parts']:
        with gzip.open(os.path.join('data', p['file']), 'rt', encoding='utf-8') as f:
            for ln in f:
                yield ln
imgs = {}
for ln in gzip.open('data/images.jsonl.gz', 'rt', encoding='utf-8'):
    r = json.loads(ln); imgs[r['s']] = r
for ln in songs():
    r = json.loads(ln)
    if r.get('s') in imgs and imgs[r['s']]['pg']:
        print(urllib.parse.quote(imgs[r['s']]['d'] + '/' + imgs[r['s']]['pg'][0][0])); break
PY
)
c=$(curl -s -m 40 -o /dev/null -w '%{http_code} %{content_type} %{size_download}B' "$URL/img/$IMG")
echo "  原图 /img/<key> -> $c （桶还没传图时会 404/503, 那正常）"

echo
[ "$fail" = 0 ] && echo "部署 + 公网验证 通过 —— $URL" || echo "有失败项（见上面 ✗）"
exit $fail

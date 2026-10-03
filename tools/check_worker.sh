#!/usr/bin/env bash
# ⚠ 本机（Windows/Git Bash）跑这个脚本会**假红**两处，CI（Linux）上不会：
#   1) 没有 geckodriver -> 真浏览器那两项直接跳过；
#   2) wrangler dev 起在 http 上，/img/* 会先吃到 http->https 的 301，而判据期望 503+两条路 ——
#      本机又没绑 R2 / IMG_UPSTREAM，所以这两条在本机必然红。看 CI 的结论为准。
# 把 Cloudflare **部署形态**真跑一遍（wrangler dev --local = 真 workerd + 本地 R2）：
#   静态资源(带内容哈希) / `/s/<id>` 深链回退 / `/img/*` 的三种可能 / 越界不泄露 / 投稿提示。
# 为什么值得单列: 部署到 Cloudflare 之前, 这些路径在本机就能验; 不然只能"推上去看运气"。
#
#  ⚠ 这里按**线上实际在跑的样子**构建: `--link-only`（用户口径 2026-09-24: 线上不转存扫描件,
#    谱页只给"去原站看这一页"的按钮）。所以除非 wrangler.jsonc 里绑了 R2 或配了 IMG_UPSTREAM,
#    `/img/*` 的**正确**行为是 503 + 说清两条路, 而不是 200。
#
#     bash tools/check_worker.sh          # 没装 wrangler 就跳过（npm install 一下就有）
#     PORT=8899 bash tools/check_worker.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
WEB="$(dirname "$HERE")"
cd "$WEB"
PORT="${PORT:-8787}"
LOG="$(mktemp)"
STOPPED=0
fail=0
ok() { printf '%s %s\n' "$([ "$1" = 1 ] && echo '✓' || echo '✗')" "$2"; [ "$1" = 1 ] || fail=1; }

if [ ! -x node_modules/.bin/wrangler ]; then
  echo "跳过: 没装 wrangler（在 jianpu-web 里跑 npm install 就有了）"
  exit 0
fi

node tools/build_dist.mjs >/dev/null || { echo "!! dist 构建失败"; exit 1; }

cleanup() {
  [ "$STOPPED" = 1 ] && return
  STOPPED=1
  # ⚠ 必须杀**整个进程组**: `npx wrangler dev` 底下是 npx → node(cli.js) → workerd 三层,
  #   只 kill $! 或只 pkill -P $! 都会留下 workerd 占着 8787 和本地 R2 的 sqlite,
  #   下一次跑就"起不来"（实测踩过）。setsid 让它自成进程组, 于是 kill -- -PGID 一把清干净。
  if [ "${WGROUP:-1}" = 1 ]; then
    kill -- "-$WPID" 2>/dev/null
  else
    MSYS_NO_PATHCONV=1 taskkill //PID "$WPID" //T //F >/dev/null 2>&1 || kill "$WPID" 2>/dev/null
  fi
  wait "$WPID" 2>/dev/null
}
trap cleanup EXIT

# Git Bash / MSYS 没有 setsid（util-linux 的），退回直接起 + 用 taskkill 杀整棵子树。
if command -v setsid >/dev/null 2>&1; then
  setsid npx wrangler dev --port "$PORT" --local > "$LOG" 2>&1 &
  WPID=$!
  WGROUP=1        # setsid 之后 $! 就是新进程组的组长, cleanup 用 -$WPID 杀全组
else
  npx wrangler dev --port "$PORT" --local > "$LOG" 2>&1 &
  WPID=$!
  WGROUP=0
fi
for i in $(seq 1 60); do
  grep -q "Ready on" "$LOG" && break
  kill -0 "$WPID" 2>/dev/null || { echo "!! wrangler dev 起不来:"; tail -5 "$LOG"; exit 1; }
  sleep 1
done
grep -q "Ready on" "$LOG" || { echo "!! 等不到 Ready:"; tail -5 "$LOG"; exit 1; }
echo "wrangler dev 就绪 (:$PORT), dist 已按链接模式重建"

code() { curl -s -m 20 -o /tmp/_cw.out -w '%{http_code}' "http://127.0.0.1:$PORT$1"; }

echo "--- 静态资源与深链 ---"
# 静态资源名带内容哈希, 得从 index.html 里读出来(别写死 app.js)
APPJS="$(grep -o 'static/app\.[a-f0-9]*\.js' dist/index.html | head -1)"
STYLECSS="$(grep -o 'static/style\.[a-f0-9]*\.css' dist/index.html | head -1)"
ok "$([ -n "$APPJS" ] && [ -n "$STYLECSS" ] && echo 1 || echo 0)" \
   "index.html 引用了带哈希的静态资源 ($APPJS / $STYLECSS)"
for p in "/" "/$APPJS" "/$STYLECSS" "/data/songs.jsonl.gz" "/data/stats.json"; do
  c=$(code "$p"); ok "$([ "$c" = 200 ] && echo 1 || echo 0)" "$p -> $c"
done
ok "$([ ! -f dist/data/images.jsonl.gz ] && echo 1 || echo 0)" \
   "dist 不带图索引(前端不显示原图, 省 0.5MB)"
c=$(code "/s/qupu123-313063")
grep -q 'id="tune"' /tmp/_cw.out && ok 1 "/s/<id> 深链 -> $c 且是 index.html（SPA 回退生效）" \
                                || ok 0 "/s/<id> 深链没有回退到 index.html"

echo "--- API ---"
c=$(code "/api/health")
grep -q '"ok":true' /tmp/_cw.out && ok 1 "/api/health -> $c $(cat /tmp/_cw.out)" || ok 0 "/api/health -> $c"
c=$(curl -s -m 20 -o /tmp/_cw.out -w '%{http_code}' -X POST -H 'Content-Type: application/json' \
     -d '{"kind":"new","title":"x","score":"12345"}' "http://127.0.0.1:$PORT/api/submit")
[ "$c" = 503 ] && grep -q '投稿后端' /tmp/_cw.out && ok 1 "没配后端时 /api/submit -> 503 且说人话" \
                                                 || ok 0 "没配后端时 /api/submit -> $c $(head -c 120 /tmp/_cw.out)"
# 跨域(GitHub Pages 镜像把投稿指向这里)会先发 OPTIONS 预检: 浏览器不认就直接拦掉,
# 连"后端没配"这句人话都看不到。所以预检必须是 204 + 放行 Content-Type。
c=$(curl -s -m 20 -o /dev/null -D /tmp/_cw.h -w '%{http_code}' -X OPTIONS \
     -H 'Origin: https://francium-223.github.io' -H 'Access-Control-Request-Method: POST' \
     -H 'Access-Control-Request-Headers: content-type' "http://127.0.0.1:$PORT/api/submit")
[ "$c" = 204 ] && grep -qi '^access-control-allow-origin: \*' /tmp/_cw.h \
  && grep -qi '^access-control-allow-headers: .*content-type' /tmp/_cw.h \
  && ok 1 "/api/* 跨域预检 OPTIONS -> 204 且放行 Content-Type" \
  || ok 0 "/api/* 跨域预检 OPTIONS -> $c $(tr -d '\r' < /tmp/_cw.h | grep -i access-control | tr '\n' ' ')"

echo "--- 越界 / 非图 ---"
c=$(code "/img/%2e%2e/wrangler.jsonc")
grep -q r2_buckets /tmp/_cw.out && ok 0 "越界路径泄露了配置文件!" \
  || ok 1 "越界路径没泄露配置（$c, URL 规范化后走 SPA 回退）"
c=$(code "/img/images/x.txt"); ok "$([ "$c" = 404 ] && echo 1 || echo 0)" "非图扩展名 -> $c"

echo "--- /img/*: 按当前绑定情况验（没绑 R2 也没配兜底时, 503+说清两条路才对） ---"
IMG_STATE=$(curl -s -m 20 "http://127.0.0.1:$PORT/api/health" | grep -o '"images":"[a-z]*"' | cut -d'"' -f4)
echo "   /api/health 报告图片来源: ${IMG_STATE:-未知}"
python3 - <<'PY' > /tmp/_cw_sample.txt
import gzip, json
imgs = {}
for ln in gzip.open('data/images.jsonl.gz', 'rt', encoding='utf-8'):
    r = json.loads(ln); imgs[r['s']] = r
for ln in gzip.open('data/songs.jsonl.gz', 'rt', encoding='utf-8'):
    r = json.loads(ln)
    s = r.get('s')
    if s in imgs and len(imgs[s]['pg']) >= 2 and not imgs[s]['drv']:
        print(s, imgs[s]['d'], imgs[s]['pg'][0][0], imgs[s]['pg'][1][0]); break
PY
read -r S D F1 F2 < /tmp/_cw_sample.txt
if [ -z "${D:-}" ]; then
  ok 0 "挑不出有原图的样本"
elif [ "$IMG_STATE" = "none" ]; then
  Q=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$D/$F1")
  c=$(code "/img/$Q")
  [ "$c" = 503 ] && grep -q 'IMG_UPSTREAM' /tmp/_cw.out \
    && ok 1 "/img/* -> 503 且说清「绑 R2 / 配 IMG_UPSTREAM」两条路" \
    || ok 0 "/img/* -> $c $(head -c 120 /tmp/_cw.out)"
else
  for f in "$F1" "$F2"; do
    npx wrangler r2 object put "jianpu-images/$D/$f" --file "/home/caesium-132/jianpu/$D/$f" --local >/dev/null 2>&1
  done
  Q=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$D/$F1")
  c=$(code "/img/$Q")
  ct=$(curl -s -m 20 -o /tmp/_cw.jpg -w '%{content_type}' "http://127.0.0.1:$PORT/img/$Q")
  [ "$c" = 200 ] && [ "${ct#image/}" != "$ct" ] && [ "$(stat -c%s /tmp/_cw.jpg)" -gt 1000 ] \
    && ok 1 "/img/<key> -> $c $ct $(stat -c%s /tmp/_cw.jpg)B" \
    || ok 0 "/img/<key> -> $c $ct $(stat -c%s /tmp/_cw.jpg)B"
  cc=$(curl -s -m 20 -D- -o /dev/null "http://127.0.0.1:$PORT/img/$Q" | grep -i '^cache-control' | tr -d '\r')
  ok "$(echo "$cc" | grep -q 'max-age=604800' && echo 1 || echo 0)" "原图带长缓存头（$cc）"
fi

echo "--- 真浏览器走 Worker（有 geckodriver 才跑; 谱页应是 verbatim 原文、无 <img>） ---"
if command -v geckodriver >/dev/null 2>&1 || command -v firefox.geckodriver >/dev/null 2>&1; then
  # 只跑一次（要 ~40 秒）: 输出留档, 退出码判成败
  if python3 tools/browser_check.py spa "http://127.0.0.1:$PORT" > /tmp/_cw_browser.out 2>&1; then
    ok 1 "浏览器交互自检（Worker 上）通过"; tail -4 /tmp/_cw_browser.out | sed 's/^/    /'
  else
    ok 0 "浏览器交互自检（Worker 上）失败"; tail -8 /tmp/_cw_browser.out | sed 's/^/    /'
  fi
else
  echo "    (跳过: 没装 geckodriver)"
fi

echo
[ "$fail" = 0 ] && echo "Worker 本地自检 通过" || echo "Worker 本地自检 失败（见上面 ✗）"
exit $fail

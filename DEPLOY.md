# 部署：把站点挂到 **jianpu-db.org**（2026-09-30 起）

两份产物、两个目标，别搞混：

| | 正式站点 | 只读镜像 |
|---|---|---|
| 部署 | **Cloudflare Worker**（`jianpu-web`，配置在 `wrangler.jsonc`） | **GitHub Pages**（`.github/workflows/pages.yml`） |
| 地址 | **https://jianpu-db.org/**（apex；`www` 也服务同一份） | https://jianpu-db.github.io/ |
| 能干什么 | 检索 / 卡片 / 谱页 / **投稿 / 补属性 / 补标签**（写回在 Worker 上） | 检索 / 卡片 / 谱页（**只读**：遇到写操作会把人指到 jianpu-db.org） |
| 怎么发 | `npx wrangler deploy`（或 Cloudflare 的 GitHub 集成） | push 到 master，Actions 自动发 |

> 只读镜像里那条提示来自 `static/app.js` 的 `MIRROR` 常量 —— 它现在指向 `https://jianpu-db.org/`。

**分享卡片（og:image）按"谁在服务这个页面"写**：cf 那份是 `https://jianpu-db.org/static/og.png`，
gh 那份在构建时被改成 `https://jianpu-db.github.io/static/og.png`（见 `tools/build_dist.mjs` 里 gh 分支）。
原因：域名在 "zone 绑好 + `wrangler deploy`" 之前取不到，那段时间贴镜像链接就是**无图卡片**。
`canonical` / `og:url` **始终指正式域名**（那是"哪一份才是正本"的表态，镜像不抢）。

## 一、把域名接上（一次性，账号侧）

1. **把 `jianpu-db.org` 加进 Cloudflare**：面板 → Add a site → 填 `jianpu-db.org` → 选 Free 计划
   → Cloudflare 会给你**两个 NS**（形如 `xxx.ns.cloudflare.com`）。
2. **到域名注册商把 NS 换成那两个**（这一步只能你来；换完通常几分钟~几小时生效）。
3. 生效后跑一次部署，`custom_domain: true` 会**自动**建 DNS 记录 + 证书：

   ```bash
   npx wrangler login          # 一次性，浏览器点一下授权
   npx wrangler deploy         # 部署 Worker + 绑 jianpu-db.org / www.jianpu-db.org
   ```

   `wrangler.jsonc` 里已经写好：

   ```jsonc
   "routes": [
     { "pattern": "jianpu-db.org",     "custom_domain": true },
     { "pattern": "www.jianpu-db.org", "custom_domain": true }
   ]
   ```

   **不需要**手配 A 记录、也不需要 CNAME 文件：apex 由 Cloudflare 代理，HTTPS 由 Cloudflare 签。
   （想让 `www` 301 到 apex：面板 → Rules → Redirect Rules 加一条即可，不用改代码。）

## 一·补、上线当天踩到的两个坑（都写在代码注释里了，这里给结论）

1. **`assets.run_worker_first` 必须设**（`wrangler.jsonc`）。不设时，`/s/<id>` 这种"没有对应文件、
   靠 `not_found_handling: single-page-application` 回退到 index.html"的请求**在路由层就被资源系统
   接走了**，Worker 根本不被调用 —— "每谱一页注入标题"白做，而**页面照样打开**（所以从外面看不出）。
   现在设成 `true`：既让 `/s/*` 真的过 Worker，也让 http→https 的 301 对所有路径一致生效
   （只列 `/s/*` 时实测 `/`、`/sitemap.xml` 这类真资源仍是明文 200，因为资源层直接回了，
   出现"一部分跳、一部分不跳"）。
2. **`/api/health` 现在带 `og` 与 `ogErr`**：`og` 是 `data/og.json` 的条数（正常 = 语料首数，
   实测 `11141`），`ogErr` 是取不到时的原因。上线当天正是因为把 `let OG_CACHE` 那行声明**误删**，
   注入悄悄退化（异常被 `serveSongPage` 的 try/catch 吞掉），而这条 curl 一秒就能看出来。

```bash
curl -s https://jianpu-db.org/api/health
# {"ok":true,"deploy":"cloudflare-worker",...,"og":11141,"ogErr":""}
```

## 一·补二、投稿后端接上了（2026-09-30）：隧道 + 两个 secret

本机服务**故意只听 127.0.0.1**（它能写盘 + git commit）。给域名用时走隧道（出向连接，不需要公网 IP、
不需要端口映射，自带 HTTPS）：

```bash
cloudflared tunnel --url http://127.0.0.1:8770        # 拿一个 https://xxx.trycloudflare.com
npx wrangler secret put API_UPSTREAM                  # 填上面那个地址
npx wrangler secret put API_TOKEN                     # 与本机 JPSUBMIT_TOKEN 同值
```

**坑（当天踩到）**：`API_UPSTREAM` / `IMG_UPSTREAM` 原本写在 `wrangler.jsonc` 的 `"vars"` 里，
而 `vars` 与 `secret` **共用"绑定名"命名空间** —— 再 `secret put` 同名就会报
`Binding name 'API_UPSTREAM' already in use. [code: 10053]`。现在这两个值**只走 secret**（本来也该是），
`vars` 整块已从配置里去掉；代码对"没设"是安全的。

**实测全链路**（域名 → Worker → 隧道 → 本机 → 语料）：

```bash
curl -s https://jianpu-db.org/api/health
# {"ok":true,...,"api":true,"upstream":"https://…trycloudflare.com","og":11141}
curl -s -X POST https://jianpu-db.org/api/submit -H 'Content-Type: application/json' \
     --data '{"kind":"tags","file":"101.txt","tags":["毛不易"]}'
# {"ok":true,"state":"已写入","committed":true,"tags":["毛不易"],"refresh":true,"refresh_msg":"已开始重建(约 2 分钟)"}
```

`scores/101.txt` 写入 `usertag=毛不易`、本机 `git commit`（`tags: 101.txt —— 人工补标签(毛不易)`）、
`feedback/20260930-171430-tags-101.json` 留档、并触发了索引重建。
隧道地址与 token 存在 `jianpu2/train-work/tunnel_secret.txt`（该目录 gitignore）。

> 快速隧道（`*.trycloudflare.com`）**每次重启换地址**，正式用建议命名隧道绑 `api.jianpu-db.org`
> （需要先跑一次 `cloudflared tunnel login` 点授权）。

## 二、（可选）原图与投稿后端

* **原图走 R2**（否则 Worker 会去 `IMG_UPSTREAM` 反代，走你家上行）：
  面板 → Storage & databases → R2（Free 计划）→ 建桶，然后把 `wrangler.jsonc` 里那段
  `r2_buckets` 取消注释，再：

  ```bash
  npx wrangler r2 bucket create jianpu-images
  python3 tools/r2_filelist.py && bash tools/r2_sync.sh     # 传 ~5GB
  ```

* **投稿后端**（本机 `app/server.py` 的公网地址）：

  ```bash
  npx wrangler secret put API_UPSTREAM     # 例 https://xxx.trycloudflare.com
  npx wrangler secret put API_TOKEN        # 与本机 JPSUBMIT_TOKEN 同值
  ```

  不配也能跑：检索/卡片/谱页/原图都在边缘，只有"投稿"会回一句"没有投稿后端"。

## 三、日常怎么发

* **改前端**（`static/*`）：`node tools/build_dist.mjs --target cf`（默认 cf）产 `dist/`，
  再 `npx wrangler deploy`；同时 `node tools/build_dist.mjs --target gh` 产 `dist-gh/` 的那套
  哈希产物要同步到仓库根（`index.html` / `404.html` / `static/*.<hash>.js`），
  GitHub Actions 也会自己重建一次（见 `pages.yml`）。
  **两份都要重建** —— 只重建一份会出现"镜像和正式站不是同一版代码"。
* **改语料**：不在这里做 —— 语料在 `jianpu-db` 仓库，`parse_scores.py` 出 `data.jsonl`，
  再由 `tools/build_web_data.py` 变成 `data/songs.jsonl.gz` 等（流水线收尾自动跑）。
* **本地预览**：`py -3.13 app/server.py 8770`（只读镜像语义 + 可写回，端口 8770）。

## 三·补、「每谱一页」的分享卡与收录（2026-09-30 已做）

前端是 SPA，**爬虫不跑 JS** —— 不做这一步，`sitemap.xml` 里 1.1 万个 `/s/<id>` 抓到的都是同一份
`<title>`，等于只有一个可索引页；分享到群里也永远是同一张卡。现在：

* 构建期 `tools/build_web_data.py` 产出 `data/og.json`（`id -> [曲名, 歌手, 音符数]`）；
* **边缘** `worker/index.js` 与 **本机** `app/server.py` 各一份注入逻辑（口径逐条对应）：
  `/s/<id>` 返回的仍然是同一份 SPA 外壳，但 `<title>`/description/og:*/twitter:*/canonical
  已换成**这一首**的；
* 纪律：任何异常都退回原始 HTML；`og:image` 保持通用卡片不动（每首没有各自的图）。

自检：`tools/check_og_meta.mjs`（18 项，离线测边缘那份纯函数）、`tools/check_og_meta.py`
（16 项，盯本机那份）、`tools/check_live.mjs` 里 3 项端到端。**两份测试都抓到过真 bug**：
Python 侧第一版把曲名拼进 `re.sub` 替换串（曲名含 `\1` 就报 invalid group reference）；
live 检查里误用 `one.t`（`buildIndex` 已把它改名 `title`）导致整支检查崩掉。

## 四、自检

```bash
node tools/check_ui.mjs        # 卡片/属性表/提示片子
node tools/check_search.mjs    # 检索口径
node tools/check_live.mjs      # 对着本地 8770 跑 17 项联检
node tools/check_jptok_js.mjs  # 前端 token 口径与 Python 侧一致
```

线上核对（换域名后照样适用）：`stats.json` 的 `songs` 要和语料行数一致、`fields` 是 15 个、
首页引用的 `static/app.<hash>.js` 必须能从线上取回。

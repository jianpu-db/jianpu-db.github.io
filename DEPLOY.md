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

## 四、自检

```bash
node tools/check_ui.mjs        # 卡片/属性表/提示片子
node tools/check_search.mjs    # 检索口径
node tools/check_live.mjs      # 对着本地 8770 跑 17 项联检
node tools/check_jptok_js.mjs  # 前端 token 口径与 Python 侧一致
```

线上核对（换域名后照样适用）：`stats.json` 的 `songs` 要和语料行数一致、`fields` 是 15 个、
首页引用的 `static/app.<hash>.js` 必须能从线上取回。

// node tools/check_live.mjs [base] —— 对着**真的跑起来的服务**做一次端到端:
//   走 HTTP 取 /data/songs.jsonl.gz 与 /static/*.js, 用**前端自己的检索代码**查一句。
// 用法: node tools/check_live.mjs http://127.0.0.1:8770
//
// ⚠ 只对**带 /api 的部署**成立(本机 `app/server.py`、Cloudflare Worker)。GitHub Pages 那条是
//   **纯静态只读镜像**: 没有 /api、也没有 /img(图索引按口径不打包)。拿它跑本自检会拿到 404.html;
//   现在会打印一句人话并退 2(而不是抛 JSON.parse 的堆栈), 线上镜像请用 check_search.mjs。
//
// ⚠ 2026-09-28: 本文件从"顶层一路 await"改成 `main()` + `process.exitCode`。原因不是风格:
//   Windows 上 Node 24 + undici 在 `fetch` 之后立刻 `process.exit()` 会撞
//   `Assertion failed: !(handle->flags & UV_HANDLE_CLOSING), src\win\async.c, line 76`,
//   退出码变成 0xC0000409 的崩溃码 —— 而"不适用"这种正常结论不该表现为崩溃。
//   (实测: fetch + process.exit(2) 崩; fetch + process.exitCode = 2 干净退 2。)
import { gunzipSync } from 'node:zlib';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { resolve } from 'node:path';
import { importStatic } from './_built.mjs';

const BASE = (process.argv[2] || 'http://127.0.0.1:8770').replace(/\/$/, '');
// ⚠ 2026-09-28 修: 原来用 `new URL('..', import.meta.url).pathname`。在 Windows 上 pathname
//   是 `/D:/Documents_D/...`(带前导斜杠), 再经 pathToFileURL 就成了
//   `file:///D:/D:/Documents_D/...` —— 本自检直接 ERR_MODULE_NOT_FOUND 跑不起来
//   (Linux 上正常, 所以一直没暴露)。标准写法是 fileURLToPath。
const ROOT = fileURLToPath(new URL('..', import.meta.url));
// B 阶段（TypeScript 化）: 前端源码是 `static/*.ts`，Node 不能直接 import —— 走 `_built.mjs`
// 用 esbuild 按需做类型擦除（只擦类型、不改语义），所以这里测的**就是**线上那份逻辑。
const { buildIndex, search } = await importStatic('search');
const { parseQuery } = await importStatic('jptok');

async function main() {
  let fail = 0;
  const ok = (c, m) => { console.log((c ? '✓ ' : '✗ ') + m); if (!c) fail++; };

  // ① 服务活着
  let health = null;
  try {
    const hr = await fetch(BASE + '/api/health');
    health = JSON.parse(await hr.text());
  } catch {
    console.log(`✗ ${BASE}/api/health 没返回 JSON —— 这不是带 /api 的部署。`);
    console.log('  GitHub Pages 是纯静态只读镜像(没有 /api、也没有 /img), 本自检不适用;');
    console.log('  它针对的是本机 server.py 或 Cloudflare Worker。线上镜像请改用:');
    console.log(`    node tools/check_search.mjs ${BASE}`);
    return 2;
  }
  ok(health.ok === true, `/api/health ok=true (repo=${health.repo})`);

  // ② 走 HTTP 取索引数据
  const r = await fetch(BASE + '/data/songs.jsonl.gz');
  ok(r.ok, `/data/songs.jsonl.gz HTTP ${r.status} ${r.headers.get('content-type')}`);
  const text = gunzipSync(Buffer.from(await r.arrayBuffer())).toString('utf8');
  const lines = text.split('\n').filter((x) => x.trim());
  // 不写死曲数: 与同一个服务给出的 stats.json 对账(更严格 —— 数据与统计必须自洽)
  const st = await (await fetch(BASE + '/data/stats.json')).json();
  ok(lines.length === st.songs, `索引 ${lines.length} 首 == stats.json 的 ${st.songs} 首`);
  const idx = buildIndex(text);
  ok(idx && idx.songs && idx.songs.length === st.songs, `buildIndex 成功: ${idx.songs.length} 首`);

  // ②b 爬虫/分享要的几个根文件（2026-09-30 加正式域名 jianpu-db.org 时一起加的）
  // 为什么要在这儿盯: 它们是**根目录**文件，本地服务早期会把 `/robots.txt` 当成 `static/robots.txt`
  // 而 404 —— 本地与线上不一致最难查。`og.png` 还必须是**不带内容哈希**的稳定地址。
  for (const [p, kind] of [['/robots.txt', 'text/plain'], ['/sitemap.xml', 'xml'],
                           ['/static/og.png', 'image/png']]) {
    const z = await fetch(BASE + p);
    const ct = z.headers.get('content-type') || '';
    const want = kind === 'xml' ? /xml/ : new RegExp(kind.replace('/', '\\/'));
    ok(z.ok && want.test(ct), `${p} HTTP ${z.status} ${ct}`);
  }
  {
    const sm = await (await fetch(BASE + '/sitemap.xml')).text();
    const n = (sm.match(/<loc>/g) || []).length;
    ok(n === st.songs + 1, `sitemap 里 ${n} 条 == 语料 ${st.songs} 首 + 首页`);
    ok(sm.includes('https://jianpu-db.org/'), 'sitemap 用的是正式域名(不是 github.io)');
    const rb = await (await fetch(BASE + '/robots.txt')).text();
    ok(/Sitemap:\s*https:\/\/jianpu-db\.org\/sitemap\.xml/.test(rb), 'robots.txt 指向正式域名的 sitemap');
  }

  // ②c 「每谱一页」给爬虫看到的 head（本地服务与边缘 Worker **各实现一遍**，这里盯本地那份；
  //     JS 那份由 tools/check_og_meta.mjs 离线测）。爬虫不跑 JS —— 不注入的话 1.1 万个谱页
  //     在它们眼里是**同一份 HTML**。
  {
    // ⚠ 用 `title`（buildIndex 把 `t` 改名成 `title` 了，`id` 保留）—— 第一版写成 `s.t` 直接
    //   TypeError 崩掉，把后面所有检查都带走了。
    const one = idx.songs.find((s) => s.id && s.title && String(s.title).length >= 2);
    const pr = await fetch(BASE + '/s/' + encodeURIComponent(one.id));
    const ph = await pr.text();
    const frag = String(one.title).slice(0, 2);
    ok(pr.ok && ph.includes('<title>') && ph.includes(frag),
       `/s/${one.id} 的 <title> 带你查的这首（片段 "${frag}"）`);
    ok(ph.includes('<body') && ph.length > 1000, '谱页 HTML 完整（注入没把它弄坏）');
    const canon = (ph.match(/<link rel="canonical" href="([^"]*)"/) || [])[1] || '';
    ok(canon.endsWith('/s/' + encodeURIComponent(one.id)), `canonical 指向这一页: ${canon}`);
  }

  // ③ 用前端代码查"人耳那句"与"原谱那句"
  // 63731232: 段落加权后第一是 U.N.オーエンは彼女なのか？(命中在副歌), 神々 那处在发狂钢琴段
  //
  // ⚠ 2026-09-29 实测发现**前端与离线工具的并列排序不一样**(这不是数据错):
  //   `33565653253` 在两边都是"代价 0"命中, 但
  //     离线 lookup.py : (总代价, -段落权, pop↑, -hot, 坏词, 标题长度, 曲名)
  //     前端 search.js  : (总代价, -exact, -pop, -hot, -段落权, 坏词, 标题长度, 曲名)
  //   于是离线第一是《神々が恋した幻想郷》(副歌段)、前端第一是《你怎么说》——
  //   两边**并列时用的键不同**(段落权 vs pop 的先后与方向都不同, 且前端多一个 `exact`)。
  //   语料涨到 10,544 首后这个并列才出现, 之前前端也排第一。
  //   期望值因此改成"这首必须在**代价 0** 的结果里(前 3)", 而不是"必须第一";
  //   "要不要让前端跟离线同序"是产品决定, 见 jianpu-db/misc/records/夜班小结_20260929.md。
  for (const [q, want] of [['33565653253', '神々が恋した幻想郷'], ['63731232', 'U.N.オーエンは彼女なのか？']]) {
    const res = search(idx, [parseQuery(q)], {});
    const top = res[0];
    const hit = res.slice(0, 3).find((r) => r.title.startsWith(want));
    ok(!!top && top.cost === 0, `${q} -> Top1 "${top && top.title}" 代价 ${top && top.cost}`);
    ok(!!hit && hit.cost === 0,
       `${q} -> "${want}" 在代价 0 的前三里(第 ${res.slice(0, 3).findIndex((r) => r.title.startsWith(want)) + 1} 位)`);
  }

  // ④ 部署的数据确实是修好的那份(th10_06 应为 **404** 音)
  //    415 是**修之前**的数(三连音开记号 `3[` 被当成音符, 那个 3 多算了一个音);
  //    2026-09-28 修好口径后正解是 404 —— 期望值要跟着口径走, 否则这条测试护的是 bug。
  const th = idx.songs.find((s) => String(s.file).includes('th10_06'));
  ok(th && th.n === 404, `th10_06 音符数 = ${th && th.n} (期望 404)`);

  // ⑤ 「每谱一页」的**服务端**两件事: /s/<id> 要能刷新, /img/<路径> 要真把原图发出来 + 挡住越界
  const tune = idx.songs.find((s) => s.id && s.file && s.file.length);
  const pageR = await fetch(BASE + '/s/' + encodeURIComponent(tune.id));
  const pageTxt = pageR.ok ? await pageR.text() : '';
  ok(pageR.ok && /id="tune"/.test(pageTxt) && /id="home"/.test(pageTxt),
     `/s/${tune.id} 深链返回同一个 index.html (HTTP ${pageR.status})`);
  // ⚠ 2026-10-02 改: 这里原来假设"原图索引下发"（pull `/data/images.jsonl.gz` 后无条件 gunzip）——
  //   但前端早已**不下发**图索引（构建说明: 带图索引是给"显示原图"用的，前端不显示），
  //   于是那行的 `ir.ok` 被 SPA 兜底的 **200 + index.html** 满足，接着 gunzip 直接
  //   `Z_DATA_ERROR: incorrect header check` 把整个自检搞崩。两个教训都落在这儿了:
  //     ① 检查项要断言 **content-type**，不能只看状态码（否则"缺失"会假装成"成功"）；
  //     ② 站点不再下发的文件，检查项也要跟着改 —— 陈旧断言比没有断言更坏（它会崩，或者更糟: 假绿）。
  const ir = await fetch(BASE + '/data/images.jsonl.gz');
  const irCt = ir.headers.get('content-type') || '';
  ok(!/text\/html/.test(irCt), `/data/images.jsonl.gz 不能返回 HTML（拿到 ${ir.status} ${irCt}）`);
  // 缺失的 data 文件必须是 404（worker 里为此专门挡了 SPA 兜底）
  const nf = await fetch(BASE + '/data/__nope__.json.gz');
  ok(nf.status === 404, `/data/__nope__.json.gz 必须是 404（拿到 ${nf.status} ${nf.headers.get('content-type')}）`);
  console.log(`   （stats.json 记着 ${st.with_images} 首有原图 / ${st.image_pages} 页 —— 仅作参考；` +
              `前端不下发图索引，health 的 images=${st.images ?? '见 /api/health'}）`);
  // 原图代理只在真的挂了图源时才验（health 会报 images=none/proxy/r2）
  const hl = await (await fetch(BASE + '/api/health')).json().catch(() => ({}));
  // 只在**边缘 Worker** 上验图代理（本机 8801 是纯静态服务，没有 /img/ 代理，别误报）
  if (hl.deploy === 'cloudflare-worker' && hl.images && hl.images !== 'none') {
    const withImg = idx.songs.find((s) => s.source);
    const url = BASE + '/img/images-prep/' + encodeURIComponent(withImg.source) + '/1.png';
    const g = await fetch(url);
    ok(g.ok && /^image\//.test(g.headers.get('content-type') || ''),
       `原图代理可取（${g.status} ${g.headers.get('content-type')}）`);
  } else {
    console.log(`   （${hl.deploy || '本机'} · images=${hl.images || '未知'} —— 跳过原图代理检查，只验越界拦截）`);
    // 越界/非图必须挡住（与是否挂图源无关，路径校验在 worker 里）
    for (const bad of ['/img/../jianpu-db/score.py', '/img/%2e%2e/jianpu-db/score.py',
                       '/img/images/../../etc/passwd']) {
      const b = await fetch(BASE + bad);
      ok(!b.ok, `挡住越界路径: ${bad} -> HTTP ${b.status}`);
    }
  }
  console.log(`\n${fail === 0 ? '通过' : '失败 ' + fail + ' 项'}  —— ${BASE}`);
  return fail ? 1 : 0;
}

process.exitCode = await main();

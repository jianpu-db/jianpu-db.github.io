// node tools/check_og_meta.mjs —— 验 `worker/index.js` 里那个「每谱一页注入 head」的纯函数。
//
// 为什么单独测: 这段跑在**边缘**，本地起不了完整的 Worker（workerd 二进制没装），
// 而它又直接改的是打给爬虫/分享平台的 HTML —— 出错就是"每个谱页都标题错/描述串味"。
// 所以把注入写成纯函数 `injectSongMeta(html, meta, opts)` 并**导出**，这里离线盯住它：
//   ① 六个标签都被换成这一首的（title/description/og:title/og:description/twitter 两个）；
//   ② canonical 与 og:url 变成**这一页**的绝对地址；
//   ③ **HTML 里的 `<` `&` `"` 被转义**（曲名里真有 `<`、`&`、`$&` 的谱）；
//   ④ 没有这首 / 没有曲名 / html 为空 -> **原样返回**（绝不把谱页弄坏）；
//   ⑤ 正文（`<body>` 之后）一个字节都不动。
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const { injectSongMeta, httpsRedirectUrl } = await import(
  'file://' + join(ROOT, 'worker', 'index.js').replace(/\\/g, '/'));

let fail = 0;
const ok = (c, m) => { console.log((c ? '✓ ' : '✗ ') + m); if (!c) fail++; };

const HTML = readFileSync(join(ROOT, 'index.html'), 'utf8');
const ORIGIN = 'https://jianpu-db.org';
const ID = 'jianpucn-150657';
const META = ['小城故事', '邓丽君', 317];

const out = injectSongMeta(HTML, META, { origin: ORIGIN, id: ID });
const grab = (re) => (out.match(re) || [])[1];

ok(grab(/<title>([^<]*)<\/title>/) === '小城故事（邓丽君） · 简谱 | jianpu-db',
   `title 换成这一首: ${grab(/<title>([^<]*)<\/title>/)}`);
ok(/<meta name="description" content="小城故事（邓丽君）的简谱：317 个音符/.test(out),
   'description 里带曲名/歌手/音符数');
ok(grab(/<meta property="og:title" content="([^"]*)"/) === '小城故事（邓丽君） · 简谱 | jianpu-db',
   'og:title 同步');
ok(/og:description" content="小城故事/.test(out), 'og:description 同步');
ok(/twitter:title" content="小城故事/.test(out), 'twitter:title 同步');
ok(/twitter:description" content="小城故事/.test(out), 'twitter:description 同步');
ok(grab(/<link rel="canonical" href="([^"]*)"/) === `${ORIGIN}/s/${ID}`,
   `canonical 指向这一页: ${grab(/<link rel="canonical" href="([^"]*)"/)}`);
ok(grab(/<meta property="og:url" content="([^"]*)"/) === `${ORIGIN}/s/${ID}`, 'og:url 指向这一页');
// ⚠ 输入 HTML 是**镜像那份产物**（`index.html`，og:image 已被构建改成镜像地址）——
//   所以这里断言的是"注入**没有动** og:image"，不是某个写死的域名地址（第一版就栽在这儿）。
const imgOf = (s) => (s.match(/<meta property="og:image" content="([^"]*)"/) || [])[1];
ok(imgOf(out) === imgOf(HTML), `og:image 保持原样不动（每首歌没有各自的图）: ${imgOf(out)}`);
ok(out.split('<body')[1] === HTML.split('<body')[1], '正文（<body> 之后）逐字节未变');

// ③ 转义：曲名/歌手里有 & < " $& 这些字符
const tricky = ['A&B <C> "$&"', 'D"E', 12];
const esc = injectSongMeta(HTML, tricky, { origin: ORIGIN, id: 'x-1' });
ok(esc.includes('A&amp;B &lt;C&gt; &quot;$&amp;&quot;'), '&/尖括号/引号被转义');
// `$&` 必须在标题里**原样出现**（转义后是 `$&amp;`）——用了函数式替换，不会被当成替换模式
// 而把匹配到的那段 HTML 注入进去。⚠ 别写成"标题里不含 `$&`"：那三个字符本来就在。
ok(/<title>A&amp;B &lt;C&gt; &quot;\$&amp;&quot;（D&quot;E） · 简谱 \| jianpu-db<\/title>/.test(esc),
   '`$&` 没被当成替换模式（标题 = 转义后的原文，没掺进别的东西）');
ok(esc.includes('（D&quot;E）'), '歌手也一起转义');

// ④ 退化情形：一律原样返回
ok(injectSongMeta(HTML, null, {}) === HTML, '没有这首 -> 原样返回');
ok(injectSongMeta(HTML, ['', 'x', 1], {}) === HTML, '曲名空 -> 原样返回');
ok(injectSongMeta('', META, {}) === '', 'html 为空 -> 原样返回');
const noOrigin = injectSongMeta(HTML, META, {});
ok(noOrigin.match(/<link rel="canonical" href="([^"]*)"/)[1] === HTML.match(/<link rel="canonical" href="([^"]*)"/)[1],
   '不给 origin 时 canonical 原样不动（不写坏）');
ok(noOrigin.match(/<meta property="og:url" content="([^"]*)"/)[1] === HTML.match(/<meta property="og:url" content="([^"]*)"/)[1],
   '不给 origin 时 og:url 原样不动');

// ⑤ http -> https 的 301（面板开关默认不一定开，所以写在代码里）
ok(httpsRedirectUrl(new URL('http://jianpu-db.org/')) === 'https://jianpu-db.org/',
   'http 首页 -> https 首页');
ok(httpsRedirectUrl(new URL('http://jianpu-db.org/s/x-1?q=5653212')) === 'https://jianpu-db.org/s/x-1?q=5653212',
   '深链与查询串原样保留（分享的 ?q= 不会丢）');
ok(httpsRedirectUrl(new URL('http://www.jianpu-db.org/sitemap.xml')) === 'https://www.jianpu-db.org/sitemap.xml',
   'www 也一起跳');
ok(httpsRedirectUrl(new URL('https://jianpu-db.org/')) === '', '本来就是 https -> 不跳（不产生循环）');
ok(httpsRedirectUrl(null) === '', 'url 为空 -> 不跳');

console.log(`\n${fail === 0 ? '通过' : '失败 ' + fail + ' 项'}`);
process.exitCode = fail ? 1 : 0;

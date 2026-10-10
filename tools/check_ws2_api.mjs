// node tools/check_ws2_api.mjs —— MusicBrainz 风格只读端点 `/ws/2/*` 的自检。
//
// 为什么单列一份（而不是塞进 check_search_api.mjs）：那一条管的是 `/api/search` 那套口径
// （429 + `{"ok":false,"err":…}` + 每分钟 30 次），而这一条管的是 MusicBrainz 那一套
// （503 + `Retry-After: 1` + `{"error":…}` + 每 IP 每秒 1 次）—— 两套的"错"长得不一样，
// 混在一个脚本里，断言会互相盖住（一个是 429、另一个是 503）。
//
// 起一份**真的 app/server.py**（标准库版）用真 HTTP 打一遍；FastAPI 版（app/api.py）调的是
// 同一个 `search_api.ws2_handle`，所以这一条同时管住两份后端（跨后端的逐项对拍另见
// tools/check_parity_legacy_vs_fastapi.py）。
//
// ⚠ 限流是**每 IP 每秒 1 次**（`WS2_RATE_PER_SEC`），所以每条断言之间必须真等 1.1 秒；
//    否则测的就不是"端点对不对"，而是"限流有多灵"。
//
//     node tools/check_ws2_api.mjs
import { spawn, spawnSync } from 'node:child_process';
import { createServer } from 'node:net';
import { existsSync, mkdtempSync, openSync, closeSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));      // 站点仓库根
const ROOT = dirname(WEB);                                         // 三个仓库的上一层

// ── 定位语料与工具（与 app/search_api.py 同一套顺序）───────────────────────────
const corpus = [process.env.JIANPU_DB, join(WEB, 'corpus', 'jianpu-db'), join(ROOT, 'jianpu-db')]
  .filter(Boolean).find((d) => existsSync(join(d, 'data.jsonl')));
const tools = [process.env.JIANPU_TOOLS, join(ROOT, 'jianpu2', 'tools')]
  .filter(Boolean).find((d) => existsSync(join(d, 'melody_search.py')));
if (!corpus || !tools) {
  console.log(!corpus ? '跳过：找不到语料（设JIANPU_DB指向含data.jsonl的语料仓库）'
                      : '跳过：找不到工具（设JIANPU_TOOLS指向含melody_search.py的目录）');
  process.exit(0);
}

const PY = [[process.env.JIANPU_PY, []], ['py', ['-3.13']], ['python3', []], ['python', []]]
  .filter(([c]) => c)
  .find(([c, a]) => spawnSync(c, [...a, '--version'], { stdio: 'ignore' }).status === 0);
if (!PY) { console.log('跳过：找不到Python（设JIANPU_PY指定解释器）'); process.exit(0); }

const port = await new Promise((res, rej) => {
  const s = createServer();
  s.on('error', rej);
  s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); });
});

const dir = mkdtempSync(join(tmpdir(), 'jianpu-ws2-api-'));
const logPath = join(dir, 'server.log');
const logFd = openSync(logPath, 'w');
const srv = spawn(PY[0], [...PY[1], join('app', 'server.py'), String(port)], {
  cwd: WEB,
  stdio: ['ignore', 'ignore', logFd],
  env: { ...process.env, JIANPU_DB: corpus, JIANPU_HOST: '127.0.0.1', JIANPU_PORT: String(port),
         JIANPU_SEARCH_RPM: '30' },
});
let dead = false;
srv.on('exit', () => { dead = true; });

const base = `http://127.0.0.1:${port}`;
let pass = 0, fail = 0;
const ck = (cond, msg, extra = '') => {
  if (cond) { pass++; console.log('  ✓ ' + msg + (extra ? '：' + extra : '')); }
  else { fail++; console.log('  ✗ ' + msg + (extra ? '：' + extra : '')); }
};
const tailLog = () => {
  try { return readFileSync(logPath, 'utf8').trim().split('\n').slice(-4).join(' | '); } catch { return ''; }
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 每条请求前等 1.1 秒 —— 每 IP 每秒 1 次，不排队的话后面全是 503（见文件头）
async function get(path, { headers = {}, gap = true, timeout = 180000 } = {}) {
  if (gap) await sleep(1100);
  const ac = new AbortController();
  const t = setTimeout(() => ac.abort(), timeout);
  try {
    const r = await fetch(base + path, { headers, signal: ac.signal });
    const text = await r.text();
    let json = null;
    try { json = JSON.parse(text); } catch { /* 非 JSON 就留 null，断言里会看出来 */ }
    return { status: r.status, headers: r.headers, json, text };
  } finally { clearTimeout(t); }
}

try {
  let up = false;
  for (let i = 0; i < 150 && !up && !dead; i++) {
    try { up = (await get('/api/health', { gap: false, timeout: 2000 })).status === 200; } catch { /* 还没监听 */ }
    if (!up) await sleep(200);
  }
  if (!up) throw new Error(`服务没起来（pid=${srv.pid}${dead ? ' 已退出' : ''}）${tailLog()}`);

  // ① `/ws/2/`：API 根，必须 200 且带说明（实体/参数/限流/一条可点的示例）
  const r0 = await get('/ws/2/');
  const j0 = r0.json || {};
  ck(r0.status === 200, '/ws/2/ 是200', `status=${r0.status}`);
  ck(!!j0.name && !!j0.version, '/ws/2/ 有 name/version', `${j0.name} v${j0.version}`);
  ck(Array.isArray(j0.entities) && j0.entities.length > 0 && j0.entities[0].name === 'song',
     '实体清单里有 song', (j0.entities || []).map((e) => e.name).join('、'));
  const incDoc = String((j0.parameters || {}).inc || '');
  ck(['artists', 'tags', 'links', 'sections', 'score'].every((k) => incDoc.includes(k)),
     '参数说明里列全了 inc', incDoc.slice(0, 60));
  const rateDoc = JSON.stringify(j0.rate_limit || {});
  ck(rateDoc.includes('每秒') && rateDoc.includes('Retry-After') && rateDoc.includes('User-Agent'),
     '限流与 User-Agent 建议写在 /ws/2/ 里', rateDoc.slice(0, 90));
  const sample = String(j0.sample || '');
  ck(/^https?:\/\/.+\/ws\/2\/song\?/.test(sample), '带一条可点的示例地址', sample);
  // 浏览器导航发 Accept: text/html —— 必须照样能读到 JSON（示例地址就是拿来点的）
  const r0b = await get('/ws/2/', { headers: { accept: 'text/html' } });
  ck(r0b.status === 200 && r0b.json && r0b.json.name, 'Accept: text/html 也回 JSON（浏览器点得开）',
     `status=${r0b.status}`);

  // ② 单条实体：主键（source）
  const ID = String(process.env.JIANPU_WS2_ID || 'qupu123-268596');
  const r1 = await get(`/ws/2/song/${ID}?fmt=json&inc=artists+tags+links+sections`);
  const e1 = r1.json || {};
  ck(r1.status === 200, `单条实体200（${ID}）`, `status=${r1.status}`);
  ck(e1.id === ID && !!e1.title, '实体带 id/title', `id=${e1.id} title=${e1.title}`);
  ck('created' in e1 === false && 'songs' in e1 === false, '单条实体**不套信封**',
     Object.keys(e1).slice(0, 6).join('、'));
  ck(Array.isArray(e1['artist-credit']) && 'tags' in e1 && 'links' in e1 && 'sections' in e1,
     'inc=artists+tags+links+sections 都展开了',
     `artists=${(e1.artists || []).length} tags=${(e1.tags || []).length} links=${(e1.links || []).length} sections=${(e1.sections || []).length}`);
  ck(typeof e1.score === 'string' && e1.score.length > 0, '实体带原始 score（简谱数字串）',
     `score长度=${(e1.score || '').length}`);

  // ③ 单条实体：文件名主干当 id + `inc=tags`
  const r2 = await get('/ws/2/song/' + encodeURIComponent(String(e1.file || '').replace(/\.txt$/, '')) + '?inc=tags');
  ck(r2.status === 200 && r2.json && r2.json.id === ID, '文件名主干也能当 id', `status=${r2.status} id=${r2.json ? r2.json.id : '—'}`);
  ck(r2.json && Array.isArray(r2.json.tags), 'inc=tags 时 tags 是数组',
     `tags=${r2.json && r2.json.tags ? r2.json.tags.length : '—'}`);

  // ④ 未知 inc：忽略 + 提示，**不许 500**
  const r3 = await get(`/ws/2/song/${ID}?inc=artists+bogus`);
  ck(r3.status === 200, '未知 inc 不报错（200）', `status=${r3.status}`);
  ck((r3.headers.get('x-unknown-inc') || '').includes('bogus'), '未知 inc 在 X-Unknown-Inc 里提示',
     `X-Unknown-Inc=${r3.headers.get('x-unknown-inc') || '—'}`);

  // ⑤ 未知 id -> 404 + `{"error": …}`
  const r4 = await get('/ws/2/song/definitely-not-here-000000');
  ck(r4.status === 404, '未知 id -> 404', `status=${r4.status}`);
  ck(r4.json && typeof r4.json.error === 'string' && !('ok' in r4.json),
     '404 的错误体是 {"error": …}', JSON.stringify(r4.json || {}).slice(0, 90));

  // ⑥ 检索：信封字段齐全（`count_exact` 是本服务加的，用来说明 count 是不是精确总命中数）
  const r5 = await get('/ws/2/song?query=316316&limit=3&fmt=json');
  const env5 = r5.json || {};
  ck(r5.status === 200, '检索200', `status=${r5.status}`);
  ck(['created', 'count', 'offset', 'songs'].every((k) => k in env5),
     '信封字段齐全（created/count/offset/songs）', Object.keys(env5).join('、'));
  ck(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(String(env5.created)), 'created 是 ISO UTC 时间',
     String(env5.created));
  ck(typeof env5.count === 'number' && env5.count > 0, 'count 是命中数', `count=${env5.count}`);
  // ⚠ 原来这里断言"316316 命中数不大 -> count_exact=true"，2026-10-10 语料涨到 28,067 行后
  //   `316316` 的命中已经顶到计数窗口（120 条），exact 变成 false —— 断言的是**语料规模**，
  //   不是接口行为，于是改成断言那条真正的**不变量**：精确与否必须和 count 对得上，
  //   而且两种取值都要在自检里出现过（下面 ⑧ 的宽查询断言 exact=false，
  //   `66563` 那条窄查询断言 exact=true），免得这里被改成"永远通过"。
  ck(env5.count_exact === (env5.count < 120),
     `count_exact 与 count 对得上（count<${120} => true）`,
     `count=${env5.count} exact=${env5.count_exact}（window=${120}）`);
  ck(Array.isArray(env5.songs) && env5.songs.length === 3, 'limit=3 回 3 条', `songs=${env5.songs ? env5.songs.length : '—'}`);
  ck(env5.offset === 0, 'offset 默认 0', `offset=${env5.offset}`);

  // ⑦ offset 生效：第 2、3 条必须与"不偏移时的第 2、3 条"逐条相同
  const r6 = await get('/ws/2/song?query=316316&limit=2&offset=1');
  const same = r6.json && r6.json.songs && env5.songs
    && r6.json.songs[0] && env5.songs[1]
    && r6.json.songs[0].id === env5.songs[1].id
    && r6.json.songs[1] && env5.songs[2] && r6.json.songs[1].id === env5.songs[2].id;
  ck(r6.status === 200 && r6.json.offset === 1 && same,
     'offset=1 就是"跳过第 1 条"（与 limit=3 那次的第 2、3 条一致）',
     `offset=${r6.json ? r6.json.offset : '—'} ids=${r6.json && r6.json.songs ? r6.json.songs.map((s) => s.id).join(',') : '—'}`);
  ck(r6.json && r6.json.count === env5.count, '翻页时 count 不变（是全量命中数，不是本页条数）',
     `${env5.count} vs ${r6.json ? r6.json.count : '—'}`);

  // ⑧ limit 上限钳到 100：`1234` 在语料里命中 1301 首（实测），所以 limit=999 必须只回 100 条
  const r7 = await get('/ws/2/song?query=1234&limit=999');
  ck(r7.status === 200 && r7.json && r7.json.songs.length === 100, 'limit=999 被钳到 100',
     `songs=${r7.json && r7.json.songs ? r7.json.songs.length : '—'} count=${r7.json ? r7.json.count : '—'}`);
  ck(r7.json && r7.json.count >= 100, '宽查询的 count 不小于本页条数',
     `count=${r7.json ? r7.json.count : '—'} exact=${r7.json ? r7.json.count_exact : '—'}`);
  ck(r7.json && r7.json.count_exact === false, '宽查询标出 count_exact=false（不假装精确）',
     `count=${r7.json ? r7.json.count : '—'} exact=${r7.json ? r7.json.count_exact : '—'}`);
  // 翻页窗口更大 -> count 跟着变大（说明"看到多少数多少"，而不是拿本页条数冒充总数）
  const r7b = await get('/ws/2/song?query=1234&limit=100&offset=100');
  ck(r7b.status === 200 && r7b.json.songs.length === 100 && r7b.json.count > r7.json.count,
     'offset=100 时 count 随窗口增大（且不随 limit 变小）',
     `count=${r7.json.count} -> ${r7b.json ? r7b.json.count : '—'}`);

  // ⑨ limit 默认 25，并且这条**窄查询**必须报"数是精确的"（与 ⑥ 那条不变量凑成一正一反）。
  //   `66563` 原来是窄查询，语料涨到 28,067 行后它自己变成宽查询了（count=120 exact=false），
  //   于是换成实测 count=51 的 `6656312`；同一条断言里仍然验"不传 limit 默认 25"。
  const r8 = await get('/ws/2/song?query=6656312');
  ck(r8.status === 200 && r8.json && r8.json.songs.length === 25, '不传 limit 时默认 25',
     `songs=${r8.json && r8.json.songs ? r8.json.songs.length : '—'}`);
  ck(r8.json && r8.json.count_exact === true && r8.json.count < 120,
     '窄查询（6656312）报 count_exact=true（没有假装精确，也没有白说"不精确"）',
     `count=${r8.json ? r8.json.count : '—'} exact=${r8.json ? r8.json.count_exact : '—'}`);

  // ⑩ 参数校验：缺 query / 非法 limit / 非法 offset / 非法 fmt 全 400，且是 {"error": …}
  for (const [path, why] of [
    ['/ws/2/song', '缺 query'],
    ['/ws/2/song?query=316316&limit=abc', 'limit 不是数字'],
    ['/ws/2/song?query=316316&offset=-1', 'offset 是负数'],
    ['/ws/2/song?query=316316&fmt=xml', 'fmt=xml'],
  ]) {
    const r = await get(path);
    ck(r.status === 400 && r.json && typeof r.json.error === 'string' && !('ok' in r.json),
       `${why} -> 400 + {"error": …}`, `status=${r.status} ${JSON.stringify(r.json || {}).slice(0, 70)}`);
  }
  const r9 = await get('/ws/2/song?query=%20%20');
  ck(r9.status === 400, 'query 全是空白 -> 400', `status=${r9.status}`);
  const r9b = await get('/ws/2/song?query=abc');
  ck(r9b.status === 400, 'query 认不出音高数字 -> 400', `status=${r9b.status}`);

  // ⑪ 未知实体 -> 404（也走 {"error": …}）
  const r10 = await get('/ws/2/artist?query=x');
  ck(r10.status === 404 && r10.json && r10.json.error, '未知实体 -> 404 + {"error": …}',
     `status=${r10.status} ${JSON.stringify(r10.json || {}).slice(0, 70)}`);

  // ⑫ 限流：连打 6 次（不排队）必须出现 503 + Retry-After: 1
  let got503 = 0, retry = '', bodyOK = true;
  for (let i = 0; i < 6; i++) {
    const r = await get('/ws/2/song?query=1234567&limit=1', { gap: false });
    if (r.status === 503) {
      got503++;
      retry = retry || (r.headers.get('retry-after') || '');
      bodyOK = bodyOK && !!(r.json && typeof r.json.error === 'string');
    }
  }
  ck(got503 > 0, `连打6次出现503（${got503}次）`, `Retry-After=${retry || '—'}`);
  ck(got503 > 0 && retry === '1', '503 带 Retry-After: 1', `Retry-After=${retry || '—'}`);
  ck(got503 > 0 && bodyOK, '503 的错误体也是 {"error": …}');

  // ⑬ 已有的 `/api/search` **没被动过**（原样：字段还是那一套 + 429 那套限流）
  await sleep(1100);
  const ra = await get('/api/search?q=316316&top=2', { gap: false });
  ck(ra.status === 200 && ra.json && Array.isArray(ra.json.hits) && 'index_version' in ra.json,
     '/api/search 仍是原来那一套（没被 /ws/2 改到）',
     `status=${ra.status} 字段=${Object.keys(ra.json || {}).slice(0, 6).join('、')}`);
} catch (e) {
  fail++;
  console.log('  ✗ 自检自身出错：' + (e && e.message ? e.message : e));
} finally {
  try { srv.kill(); } catch { /* 已经没了 */ }
  const gone = await new Promise((res) => {
    if (dead) return res(true);
    const t = setTimeout(() => res(false), 2000);
    srv.on('exit', () => { clearTimeout(t); res(true); });
  });
  if (!gone && srv.pid) {
    if (process.platform === 'win32') spawnSync('taskkill', ['/PID', String(srv.pid), '/T', '/F'], { stdio: 'ignore' });
    else { try { process.kill(srv.pid, 'SIGKILL'); } catch { /* 已经没了 */ } }
  }
  try { closeSync(logFd); } catch { /* 关过了 */ }
  try { rmSync(dir, { recursive: true, force: true }); } catch { /* 临时目录清不掉不算失败 */ }
}

console.log(fail ? `✗ /ws/2 自检 失败（${fail}项）` : `✓ /ws/2 自检 通过（${pass}项）`);
process.exit(fail ? 1 : 0);

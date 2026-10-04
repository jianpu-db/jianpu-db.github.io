// node tools/check_search_api.mjs —— 只读检索端点 GET /api/search 的自检。
//
// 为什么单列一条：检索是**只读**接口里唯一会被外面直接打的一个（机器人/命令行/网页都用它），
// 而它自己不带任何状态——参数校验、缓存、限流、503文案全在app/search_api.py里。
// 这里起一份**真的app/server.py**（标准库版），用真HTTP把那条口径打一遍：
// FastAPI版（app/api.py）与它共用同一个模块，所以这一条同时管住两份后端。
//
// 自起服务、自挑空闲端口、跑完收干净（不留进程）；旁边没有语料/工具仓库时**干净跳过**。
//
//     node tools/check_search_api.mjs
import { spawn, spawnSync } from 'node:child_process';
import { createServer } from 'node:net';
import { existsSync, mkdtempSync, openSync, closeSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));      // 站点仓库根
const ROOT = dirname(WEB);                                         // 三个仓库的上一层

// ── 定位语料与工具（与app/search_api.py同一套顺序）─────────────────────────────
const corpus = [process.env.JIANPU_DB, join(WEB, 'corpus', 'jianpu-db'), join(ROOT, 'jianpu-db')]
  .filter(Boolean).find((d) => existsSync(join(d, 'data.jsonl')));
const tools = [process.env.JIANPU_TOOLS, join(ROOT, 'jianpu2', 'tools')]
  .filter(Boolean).find((d) => existsSync(join(d, 'melody_search.py')));
if (!corpus || !tools) {
  console.log(!corpus ? '跳过：找不到语料（设JIANPU_DB指向含data.jsonl的语料仓库）'
                      : '跳过：找不到工具（设JIANPU_TOOLS指向含melody_search.py的目录）');
  process.exit(0);
}

// ── 挑解释器：本机是py -3.13（见README/run-api.cmd），别的机器上退回python3/python ──
const PY = [[process.env.JIANPU_PY, []], ['py', ['-3.13']], ['python3', []], ['python', []]]
  .filter(([c]) => c)
  .find(([c, a]) => spawnSync(c, [...a, '--version'], { stdio: 'ignore' }).status === 0);
if (!PY) { console.log('跳过：找不到Python（设JIANPU_PY指定解释器）'); process.exit(0); }

// ── 空闲端口：让内核给一个，拿到就关（用完立刻起服务，中间没有别的占用者）──────────
const port = await new Promise((res, rej) => {
  const s = createServer();
  s.on('error', rej);
  s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); });
});

const dir = mkdtempSync(join(tmpdir(), 'jianpu-search-api-'));
const logPath = join(dir, 'server.log');
const logFd = openSync(logPath, 'w');
// 服务的输出写文件而不是管道：沙箱下Node抓子进程管道会EPERM，而且这里也不需要实时读它，
// 只在"起不来"时把日志末尾打出来。
const srv = spawn(PY[0], [...PY[1], join('app', 'server.py'), String(port)], {
  cwd: WEB,
  stdio: ['ignore', 'ignore', logFd],
  // JIANPU_PORT/JIANPU_HOST必须显式给：环境里若已经有这两个变量，server.py是**先读环境**的，
  // 会把端口换成别的，自检就会打到别人的服务上。
  env: { ...process.env, JIANPU_DB: corpus, JIANPU_HOST: '127.0.0.1', JIANPU_PORT: String(port),
         JIANPU_SEARCH_RPM: '30' },   // 限流阈值钉死：环境里改了它，"40次必出429"就不成立
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

async function get(path, timeout = 180000) {
  const ac = new AbortController();
  const t = setTimeout(() => ac.abort(), timeout);
  try {
    const r = await fetch(base + path, { signal: ac.signal });
    const text = await r.text();
    let json = null;
    try { json = JSON.parse(text); } catch { /* 非JSON就留null，断言里会看出来 */ }
    return { status: r.status, headers: r.headers, json, text };
  } finally { clearTimeout(t); }
}

try {
  // ── 等服务起来：/api/health是敞开的，不需要口令 ──────────────────────────────
  let up = false;
  for (let i = 0; i < 150 && !up && !dead; i++) {
    try { up = (await get('/api/health', 2000)).status === 200; } catch { /* 还没监听 */ }
    if (!up) await new Promise((r) => setTimeout(r, 200));
  }
  if (!up) throw new Error(`服务没起来（pid=${srv.pid}${dead ? ' 已退出' : ''}）${tailLog()}`);

  // ① 正常查询：形状必须是melody_search.py --json那一份+index_version+cache
  const SHAPE = ['query', 'segments', 'n', 'fuzzy', 'corpus', 'total', 'count', 'hits',
                 'index_version', 'cache'];
  const HIT = ['title', 'artist', 'site', 'file', 'diff', 'n_notes', 'bar_from', 'bar_to', 'seg',
               'segs_detail', 'sec_cn', 'bars', 'pop', 'hot', 'positions', 'digits', 'status',
               'source', 'tag', 'id', 'score', 'pos', 'sec', 'sec_w'];
  const r1 = await get('/api/search?q=33565653253&fuzzy=0&top=5');
  const j1 = r1.json || {};
  ck(r1.status === 200, '正常查询200', `q=33565653253 -> ${r1.status}`);
  ck(SHAPE.every((k) => k in j1), '返回字段齐全（含index_version与cache）',
     SHAPE.filter((k) => !(k in j1)).join('、') || `这${SHAPE.length}个字段都在`);
  ck(Array.isArray(j1.hits) && j1.hits.length > 0 && !!j1.hits[0].title,
     'hits[0].title有值', `hits[0].title=${j1.hits && j1.hits[0] ? j1.hits[0].title : '—'}`);
  const lost = HIT.filter((k) => !(j1.hits && j1.hits[0] && k in j1.hits[0]));
  ck(lost.length === 0, 'hits[]每条字段齐全', lost.join('、') || `这${HIT.length}个字段都在`);
  ck(/^\d+@\d+$/.test(String(j1.index_version)), 'index_version是"语料行数@mtime"',
     String(j1.index_version));
  ck(j1.cache && j1.cache.hit === false, '首次查询cache.hit=false');
  const r1b = await get('/api/search?q=33565653253&fuzzy=0&top=5');
  ck(r1b.json && r1b.json.cache && r1b.json.cache.hit === true, '同一查询再打一次cache.hit=true');

  // ② 已知答案（与jianpu2/tools/check_melody_search.py钉的是同一条）
  const r2 = await get('/api/search?q=316 316 31656564&top=3');
  ck(r2.status === 200 && r2.json && r2.json.hits && r2.json.hits[0]
     && r2.json.hits[0].title === '路灯下的小姑娘', '316 316 31656564 -> 路灯下的小姑娘',
     r2.json && r2.json.hits && r2.json.hits[0] ? r2.json.hits[0].title : '—');

  // ③ 真没有的片段：空结果也是200（count=0），不是404/500
  const r3 = await get('/api/search?q=77717771777177');
  ck(r3.status === 200 && r3.json && r3.json.count === 0, '没有的片段 -> 200且count=0',
     `status=${r3.status} count=${r3.json ? r3.json.count : '—'}`);

  // ④ 参数校验
  const r4 = await get('/api/search');
  ck(r4.status === 400, '缺q -> 400', `status=${r4.status}`);
  const r4b = await get('/api/search?q=%20%20%20');
  ck(r4b.status === 400, 'q全是空白 -> 400', `status=${r4b.status}`);
  const r4c = await get(`/api/search?q=${'1234567'.repeat(30)}`);
  ck(r4c.status === 400, 'q长过200 -> 400', `长度=${'1234567'.repeat(30).length}`);
  const r4d = await get('/api/search?q=1234567&fuzzy=9');
  ck(r4d.status === 400, 'fuzzy只收0/1/2 -> 别的值400', `status=${r4d.status}`);

  // ⑤ top超范围钳到边界：1234567在语料里有66首命中（实测），所以top=999应正好回50条
  const r5 = await get('/api/search?q=1234567&top=999');
  ck(r5.status === 200 && r5.json && r5.json.hits.length === 50, 'top=999被钳到50',
     `hits=${r5.json ? r5.json.hits.length : '—'}`);
  const r5b = await get('/api/search?q=1234567');
  ck(r5b.status === 200 && r5b.json && r5b.json.hits.length === 20, '不传top时默认20',
     `hits=${r5b.json ? r5b.json.hits.length : '—'}`);

  // ⑥ 限流：连打40次必须有429，且带Retry-After（放在最后，免得把前面的断言挤成429）
  let got429 = 0, retryAfter = '';
  for (let i = 0; i < 40; i++) {
    const r = await get('/api/search?q=1234567&top=1');
    if (r.status === 429) { got429++; retryAfter = retryAfter || (r.headers.get('retry-after') || ''); }
  }
  ck(got429 > 0, `连打40次出现429（${got429}次）`, `Retry-After=${retryAfter || '—'}`);
  ck(got429 > 0 && /^\d+$/.test(retryAfter) && Number(retryAfter) >= 1,
     '429带Retry-After且是正整数秒');
} catch (e) {
  fail++;
  console.log('  ✗ 自检自身出错：' + (e && e.message ? e.message : e));
} finally {
  // 收干净：先温和kill，2秒还在就按pid树强杀（Windows上python的子进程也一起收）
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

console.log(fail ? `✗ 检索API自检 失败（${fail}项）` : `✓ 检索API自检 通过（${pass}项）`);
process.exit(fail ? 1 : 0);

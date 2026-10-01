// node tools/audit_deeplinks.mjs [样本数] [站点]
//
// **深链抽样审计**：从 sitemap 里随机抽 N 条 `/s/<id>`，验证
//   ① HTTP 200；② 返回的是同一份 SPA 外壳；③ **标题里真的带这首歌**（边缘注入生效）。
//
// 为什么单独一个（而不是并进 check_live）: `check_live` 只验 1 条深链（够日常），
// 但"1.1 万条里有没有整片坏的"只有抽样才能看出来；抽样还能覆盖不同来源（qupu123/jianpucn/jianpujia…）。
// 这是**审计**不是门槛（会打网络、11k 全量不现实），所以不进 CI。
import { readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';

const N = Number(process.argv[2] || 60);
const BASE = process.argv[3] || 'https://jianpu-db.org';

const raw = gunzipSync(readFileSync(new URL('../data/songs.jsonl.gz', import.meta.url))).toString('utf8');
const rows = raw.split('\n').filter(Boolean).map((l) => JSON.parse(l));

// 先取 sitemap（顺便验它本身）
const sm = await (await fetch(BASE + '/sitemap.xml')).text();
const urls = [...sm.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1]);
const deep = urls.filter((u) => u.includes('/s/'));
console.log(`sitemap ${urls.length} 条（深链 ${deep.length} 条）· 语料 ${rows.length} 首`);

let seed = 20261004;
const rnd = (n) => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed % n; };
const byId = new Map(rows.map((r) => [r.id, r]));

// 分层抽样: 尽量覆盖不同 source
const bySrc = new Map();
for (const u of deep) {
  const id = u.split('/s/')[1];
  const r = byId.get(id);
  // 按**站点**分层（qupu123 / jianpucn / jianpujia…），不要按"每条谱的 source id"分 ——
  // source id 有近万个，每组 1 条，分层就退化成均匀随机了（第一版就是这样，标签也写错了）。
  const s = ((r && r.s) || '?').split('-')[0] || '?';
  if (!bySrc.has(s)) bySrc.set(s, []);
  bySrc.get(s).push(u);
}
const picks = [];
for (const [s, list] of bySrc) {
  const take = Math.max(1, Math.round(N * list.length / deep.length));
  for (let i = 0; i < take && picks.length < N; i++) picks.push(list[rnd(list.length)]);
}
const srcKeys = [...bySrc.keys()];
console.log(`抽样 ${picks.length} 条 · 来源 ${srcKeys.length} 个（前 6 个: ${srcKeys.slice(0, 6).join(', ')}…）`);

let bad = 0, checked = 0;
const fails = [];
for (const u of picks) {
  const id = u.split('/s/')[1];
  const r = byId.get(id);
  try {
    const res = await fetch(u);
    const txt = await res.text();
    checked++;
    const hasShell = /id="home"/.test(txt) && /id="tune"/.test(txt);
    // 标题应带上这首歌（曲名做了 HTML 转义/截断，所以用"前几个字符"判断，避免误报）
    const head = (r?.t || '').replace(/[<>&"']/g, '').slice(0, 6);
    const titleHit = head ? txt.includes(head) : true;
    if (!res.ok || !hasShell || !titleHit) {
      bad++;
      fails.push(`${u} -> HTTP ${res.status} shell=${hasShell} title=${titleHit} (期望含「${head}」)`);
    }
  } catch (e) {
    bad++;
    fails.push(`${u} -> 请求失败: ${e.message}`);
  }
}
console.log(`\n结果: ${checked - bad}/${checked} 正常`);
for (const f of fails.slice(0, 8)) console.log('  ✗ ' + f);
console.log(bad ? `**${bad} 条有问题**` : '抽样全过 ✓（深链、SPA 外壳、边缘注入标题都对）');
process.exit(bad ? 1 : 0);

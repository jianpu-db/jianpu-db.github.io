// node tools/check_render.mjs —— 用假 DOM 真触发一次查询, 验证结果卡(含元数据表)真的渲染出来
import { readFileSync } from 'node:fs';
import { importStatic } from './_built.mjs';

const els = {};
function mkEl(id) {
  return els[id] || (els[id] = {
    id, _h: '', textContent: '', className: '', value: '', disabled: false, checked: true,
    handlers: {},
    get innerHTML() { return this._h; },
    set innerHTML(v) { this._h = v; },
    addEventListener(ev, fn) { (this.handlers[ev] = this.handlers[ev] || []).push(fn); },
    focus() {}, getAttribute() { return ''; },
    fire(ev, arg) { (this.handlers[ev] || []).forEach((f) => f.call(this, arg || { preventDefault() {} })); },
  });
}
global.document = {
  getElementById: mkEl,
  getElementsByClassName: () => [],
  querySelectorAll: () => [],
  // app.js 用全局委托接「＋ 补收录页」的保存按钮(结果区有 #out / #tout 两个)
  addEventListener(ev, fn) { (global._docHandlers = global._docHandlers || {})[ev] = fn; },
};
global.window = global;
global.location = { protocol: 'http:', host: '127.0.0.1:8770' };
global.performance = { now: () => Date.now() };
global.fetch = async (u) => {
  if (u.endsWith('songs.jsonl.gz')) {
    return { ok: true, body: new Response(readFileSync(new URL('../data/songs.jsonl.gz', import.meta.url))).body };
  }
  if (u.endsWith('stats.json')) {
    return { ok: true, json: async () => JSON.parse(readFileSync(new URL('../data/stats.json', import.meta.url), 'utf8')) };
  }
  return { ok: false, status: 404, json: async () => ({}) };
};
global.DecompressionStream = (await import('node:stream/web')).DecompressionStream;
process.on('unhandledRejection', (e) => { console.error('!! Promise 拒绝:', e && e.message); process.exitCode = 1; });

await importStatic('app');
await new Promise((r) => setTimeout(r, 1500));

mkEl('q').value = '63731232';
mkEl('form').fire('submit');                       // 真触发提交
await new Promise((r) => setTimeout(r, 800));

const out = mkEl('out').innerHTML;
console.log('status:', (mkEl('status').textContent || '').slice(0, 70));
console.log('out 长度:', out.length);
console.log('含 card ?', out.includes('class="card'));
console.log('含元数据表 ?', out.includes('table class="meta"'));
console.log('含 MBID 行 ?', out.includes('MBID'));
console.log('含 小节 行 ?', out.includes('小节'));
console.log('含高亮 mark ?', out.includes('<mark>'));
console.log('含小节线 span ?', out.includes('class="bar"'));
if (out.length) console.log('\n片段预览:\n', out.slice(0, 420).replace(/\s+/g, ' '));

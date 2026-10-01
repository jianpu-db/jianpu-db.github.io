import { readFileSync } from 'node:fs';
import { importStatic } from './_built.mjs';
const { buildIndex, parseQuery, search } = await importStatic('search');
import { gunzipSync } from 'node:zlib';

const txt = gunzipSync(readFileSync(new URL('../data/songs.jsonl.gz', import.meta.url))).toString('utf8');
const idx = buildIndex(txt);

for (const q of ['5 5 5 3 2 2 3 5 3 2 1 1 6 1 2 6 5 5', '55532235 3211612655', '5562 1162 5561651162']) {
  const segs = parseQuery(q);
  const r = search(idx, segs, { top: 3 });
  console.log(`查询 ${JSON.stringify(q)}`);
  console.log('  解析:', JSON.stringify(segs));
  console.log('  结果:', r.length ? r.map((x) => `${x.title}(err ${x.err})`).join(' | ') : '无');
}

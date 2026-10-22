/**
 * 语料读取的**唯一入口**（构建脚本、自检脚本都走这里）。
 *
 * 为什么需要这一层（2026-10-22）:
 *   语料过 5.6 万首后，整份 `data/songs.jsonl.gz` 到 27.5 MB —— 超过 Cloudflare Workers
 *   静态资源的**单文件 25 MiB 硬上限**，部署会被拒。所以线上改成**分片**:
 *   `data/index.json`（清单）+ `data/songs.partNN.jsonl.gz`。
 *   仓库里也不再提交整份 gz（`.gitignore`），CI 检出后**只有分片**。
 *
 * 于是"读语料"有两种来源:
 *   ① 本机: 整份 `data/songs.jsonl.gz` 还在（build_web_data.py 每次都写），读它最快;
 *   ② CI / 干净检出: 没有整份，只有分片 —— 按 `index.json` 的顺序把分片**解压拼接**。
 * 两种来源必须给出**同一条字符串**，否则 CI 与本地会算不一样的结果（这类差异最难查）。
 *
 * ⚠ 别在脚本里直接 `readFileSync('../data/songs.jsonl.gz')` —— CI 里那文件不存在。
 */
import { readFileSync, existsSync, readdirSync } from 'node:fs';
import { gunzipSync, gzipSync } from 'node:zlib';

const DATA = new URL('../data/', import.meta.url);

function p(name) { return new URL(name, DATA); }

/** 磁盘上直接躺着的那份整 gz 的字节；没有则 null */
export function singleGzBuffer() {
  return existsSync(p('songs.jsonl.gz')) ? readFileSync(p('songs.jsonl.gz')) : null;
}

/**
 * 分片清单: [{ file, buf }] —— file 是**相对站点根**的路径（与 index.json 里一致）。
 * 没有分片文件时返回空数组。
 */
export function corpusParts() {
  const dir = new URL('.', DATA);
  const out = [];
  const names = readdirSync(dir).filter((f) => /^songs\.part\d+\.jsonl\.gz$/.test(f)).sort();
  for (const n of names) out.push({ file: 'data/' + n, buf: readFileSync(p(n)) });
  return out;
}

/** 语料全文（JSONL，一行一首）。整份优先，否则拼分片；两样都没有就抛。 */
export function corpusText() {
  const gz = singleGzBuffer();
  if (gz) return gunzipSync(gz).toString('utf8');
  const parts = corpusParts();
  if (!parts.length) {
    throw new Error('找不到语料: data/songs.jsonl.gz 与 data/songs.partNN.jsonl.gz 都不存在（先跑 tools/build_web_data.py）');
  }
  return parts.map((x) => gunzipSync(x.buf).toString('utf8')).join('');
}

/** 语料字节（压缩后）。整份优先；否则把拼好的文本压一次（调用方只关心"能解出同一份语料"）。 */
export function corpusGzBuffer() {
  const gz = singleGzBuffer();
  if (gz) return gz;
  return gzipSync(Buffer.from(corpusText(), 'utf8'));
}

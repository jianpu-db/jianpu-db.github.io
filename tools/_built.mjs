// 给"检查脚本"用的**按需转译**入口：把 `static/<名>.ts` 用 esbuild 转成 ESM 后 import 回来。
//
// 为什么要它：B 阶段把前端源码从 `.js` 换成 `.ts`（类型安全），但 Node 不能直接 import TS。
// 三条路里选了这条：
//   ① 每个脚本自己去调 esbuild —— 重复代码；
//   ② 引入 ts-node/tsx 作为运行时依赖 —— 给一个"零运行时依赖"的项目加了依赖，不值；
//   ③ **本文件**：一个 20 行的工具，用已在 devDependencies 里的 esbuild 现转现用（毫秒级，
//      并且带缓存），检查脚本一句 `await importStatic('search')` 就能拿到编译后的模块 ✓。
//
// 行为保证：转译**只做类型擦除**（`loader: 'ts'`、`format: 'esm'`、target 与浏览器一致），
// 不改语义 —— 所以"检查脚本测的就是线上跑的那份逻辑"。
import { mkdirSync, readFileSync, existsSync, statSync, writeFileSync } from 'node:fs';
import { basename, dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';
import { transform } from 'esbuild';

const HERE = dirname(fileURLToPath(import.meta.url));
export const ROOT = join(HERE, '..');
const STATIC = join(ROOT, 'static');
const CACHE = join(STATIC, '.build');

const TS_OPTS = { loader: 'ts', format: 'esm', target: 'es2022', charset: 'utf8' };

/**
 * 缓存新鲜度: **按源文件内容哈希**判断，不看 mtime。
 *
 * ⚠ 2026-10-02 血泪: 原来判的是 `mtime(缓存) > mtime(源)`。我做"门槛真的会红吗"的反向验证时，
 * 先注入一处坏改动、跑一遍（缓存被刷新），再用 `Copy-Item` 还原 —— 而 `Copy-Item` **保留备份的旧
 * mtime**，于是缓存的构建比源"更新"，`_built` 一直返回**那份注入了坏代码的旧构建**，
 * 门槛于是红着不停 ✗。更普遍地说: 任何"把更旧的文件放回来"（`git checkout` 旧版本、解压备份、
 * 从别处拷一份）都会让测试**测的是旧代码**，而它照样报绿/报红 —— 这类"测试装置骗人"最难查。
 * 内容哈希没有这个问题: 内容变了就重建，内容没变就复用。
 */
function hashOf(code) {
  return createHash('sha1').update(code).digest('hex').slice(0, 16);
}

/** 读缓存文件首行的 `// build-hash: xxx`（没有就返回 ''）。 */
function cachedHash(out) {
  try {
    const first = readFileSync(out, 'utf8').slice(0, 64);
    const m = /^\/\/ build-hash: ([0-9a-f]+)/.exec(first);
    return m ? m[1] : '';
  } catch {
    return '';
  }
}

// 模块之间的依赖（`app` import `./search.js` / `./jptok.js`；`search` import `./jptok.js`）。
// ⚠ 必须**先转依赖再转自己**：转译产物落在 `static/.build/`，而源码里的相对 import 是 `./search.js`
//   —— 如果只转 app，`.build/search.js` 还不存在，Node 会 `ERR_MODULE_NOT_FOUND`
//   （实测 `check_page.mjs` 就是这么炸的：`app.renderScore is not a function` 之前先报找不到模块）。
const DEPS = { app: ['search', 'jptok'], search: ['jptok'] };

/** 转译单个源文件（有 .ts 用 .ts，否则退回 .js），返回输出路径。 */
export async function transpile(name) {
  for (const dep of DEPS[name] ?? []) await transpile(dep);
  const ts = join(STATIC, `${name}.ts`);
  const js = join(STATIC, `${name}.js`);
  const src = existsSync(ts) ? ts : js;
  if (!existsSync(src)) throw new Error(`找不到源文件: static/${name}.ts|.js`);
  mkdirSync(CACHE, { recursive: true });
  const out = join(CACHE, `${name}.js`);
  const code = readFileSync(src, 'utf8');
  const h = hashOf(code);
  if (existsSync(out) && cachedHash(out) === h) return out;   // 内容没变 -> 复用（见 hashOf 的注释）
  const res = src.endsWith('.ts')
    ? await transform(code, { ...TS_OPTS, sourcefile: src })
    : { code };                                   // 纯 JS 直接拷贝（保持与线上一致）
  writeFileSync(out, `// build-hash: ${h}\n${res.code}`);
  return out;
}

/** `await importStatic('search')` —— 拿到转译后的模块（缓存目录在 static/.build/，已 gitignore）。 */
export async function importStatic(name) {
  const out = await transpile(name);
  return import(pathToFileURL(out).href);
}

/** 转译并载入仓库里**任意** `.ts`（例如 `worker/index.ts`）—— 给检查脚本用。
 *
 * 为什么要通用入口: Worker 也上了 TypeScript，而它不在 `static/` 下；路径与缓存目录都跟着源文件走
 * （产物落在同目录的 `.build/` 下），这样它内部的相对 import（如果有）也照样解析得对。
 */
export async function importRepoFile(rel) {
  const src = join(ROOT, rel);
  if (!existsSync(src)) throw new Error(`找不到源文件: ${rel}`);
  const outDir = join(dirname(src), '.build');
  mkdirSync(outDir, { recursive: true });
  const out = join(outDir, basename(src).replace(/\.ts$/, '.js'));
  const code = readFileSync(src, 'utf8');
  const h = hashOf(code);
  if (!existsSync(out) || cachedHash(out) !== h) {      // 同样按内容哈希，不看 mtime
    const res = await transform(code, { ...TS_OPTS, sourcefile: src });
    writeFileSync(out, `// build-hash: ${h}\n${res.code}`);
  }
  return import(pathToFileURL(out).href);
}

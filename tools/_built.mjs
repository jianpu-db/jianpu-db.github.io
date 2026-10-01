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
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { transform } from 'esbuild';

const HERE = dirname(fileURLToPath(import.meta.url));
export const ROOT = join(HERE, '..');
const STATIC = join(ROOT, 'static');
const CACHE = join(STATIC, '.build');

const TS_OPTS = { loader: 'ts', format: 'esm', target: 'es2022', charset: 'utf8' };

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
  // 源没变就不重复转译（检查脚本会被反复调用）
  if (existsSync(out) && statSync(out).mtimeMs > statSync(src).mtimeMs) return out;
  const code = readFileSync(src, 'utf8');
  const res = src.endsWith('.ts')
    ? await transform(code, { ...TS_OPTS, sourcefile: src })
    : { code };                                   // 纯 JS 直接拷贝（保持与线上一致）
  writeFileSync(out, res.code);
  return out;
}

/** `await importStatic('search')` —— 拿到转译后的模块（缓存目录在 static/.build/，已 gitignore）。 */
export async function importStatic(name) {
  const out = await transpile(name);
  return import(pathToFileURL(out).href);
}

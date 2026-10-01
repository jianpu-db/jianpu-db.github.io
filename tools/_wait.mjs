// 检查脚本共用的小工具：**等条件成立**，而不是睡一个固定秒数。
//
// 为什么要单独一个文件（2026-10-01）:
//   这几个 DOM 自检原来都写 `await sleep(1500)` 之类的固定等待 —— 本地够用、CI 的 runner 更慢更抖，
//   于是**随机红**，而且本地怎么都复现不出（我把便携版 Node 22 下下来跑照样过）。
//   固定睡眠做等待=假红/假绿的温床。统一成"轮询到预期内容出现"：
//     * 本地更快（实测从 1600 ms 降到 ~300 ms 就绪）；
//     * CI 更稳；失败时能打印"等了多久、当时页面长什么样"。
//
// 用法:
//   import { waitFor } from './_wait.mjs';
//   const ms = await waitFor(() => /tune-h1/.test(html()), 20000);
//   if (ms < 0) { ...打印当时的页面片段... }

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** 轮询直到 `fn()` 为真；返回用了多少毫秒，超时返回 -1（不抛异常，交给调用方决定怎么说）。 */
export async function waitFor(fn, ms = 20000, step = 100) {
  const t0 = Date.now();
  for (;;) {
    try {
      if (fn()) return Date.now() - t0;
    } catch {
      /* 还没就绪，继续等 */
    }
    if (Date.now() - t0 > ms) return -1;
    await sleep(step);
  }
}

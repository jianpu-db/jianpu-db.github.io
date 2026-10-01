# jianpu-db · 简谱旋律查歌

**只哼开头几个音，把这首歌的简谱找出来。** 检索完全在浏览器里跑，不上传、不留痕。

[![曲谱](https://img.shields.io/badge/%E6%9B%B2%E8%B0%B1-11%2C495%20%E9%A6%96-0b62c4)](https://jianpu-db.org/)
[![音符](https://img.shields.io/badge/%E9%9F%B3%E7%AC%A6-2%2C532%2C332-0b62c4)](https://jianpu-db.org/)
[![小节线](https://img.shields.io/badge/%E6%98%BE%E5%BC%8F%E5%B0%8F%E8%8A%82%E7%BA%BF-552%2C836-0b62c4)](https://jianpu-db.org/)
[![索引](https://img.shields.io/badge/%E7%B4%A2%E5%BC%95-5.12%20MB%20gz%20%C2%B7%20%E5%B0%B1%E7%BB%AA%20204%20ms-177245)](https://jianpu-db.org/)
[![查询](https://img.shields.io/badge/%E6%9F%A5%E8%AF%A2%E4%B8%AD%E4%BD%8D-106%20ms-177245)](https://jianpu-db.org/)
[![Top-1](https://img.shields.io/badge/%E9%87%91%E6%9B%B2%E6%A6%9C%20L%3D15%20%E9%94%990%20Top--1-98.9%25-177245)](https://jianpu-db.org/)
[![自检](https://img.shields.io/badge/%E8%87%AA%E6%A3%80%2FQA-78%20%E4%B8%AA%E8%84%9A%E6%9C%AC-946200)](https://jianpu-db.org/)

**线上**：**https://jianpu-db.org/**（正式站，可投稿） · https://jianpu-db.github.io/（只读镜像）

---

## 这是什么

一份**简谱语料**加一个**旋律检索站**：曲谱由视觉语言模型（Qwen3-VL-2B）从扫描件转写而来，
检索不是"按歌名搜"，而是**按旋律**——输入你记得的那几个音（如 `5 5 6 5 3 2 1`），
从全库 11,495 首里按"代价"排序找出候选。**计算全部发生在你的浏览器里**，服务器只发一份索引。

## 技术文档（面试/评审向）

| 文档 | 内容 |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 全链路架构图（Mermaid）、组件职责、数据契约（曲谱文件 / 索引字段 / 写接口）、部署拓扑、瓶颈与扩展 |
| [docs/TECH_STACK.md](docs/TECH_STACK.md) | 分层选型与**理由 + 量化收益**、"刻意没用"的清单、6 个难题的 STAR 复盘、20 条面试 Q&A |
| [docs/QA.md](docs/QA.md) | **质量工程**：自检怎么跑、判据用什么证据、什么时候不能跑 |
| [docs/交付总结.md](docs/交付总结.md) | **交付总结**：五个阶段做了什么、实测数字、以及被数据否决的尝试 |
| [docs/MOCK_INTERVIEW.md](docs/MOCK_INTERVIEW.md) | **面试演练稿**：自我介绍 + 10 个追问 + 陷阱题 + 数字速查 |
| [docs/RESUME.md](docs/RESUME.md) | 中英文简历要点（可直接抄）+ 需提前准备的追问 |
| [DEPLOY.md](DEPLOY.md) | 域名/边缘/隧道/写后端的部署手册与踩坑记录 |

## 技术栈（一句话版）

**转写** Qwen3-VL-2B（VLM）+ PyTorch/CUDA + 贪心解码取 token 概率 ·
**语料** 纯文本曲谱 + JSONL 索引（无数据库）+ git 版本化 ·
**检索** 零依赖 ES module、八度无关代价匹配、`DecompressionStream` 流式解压 ·
**边缘** Cloudflare Workers + Assets（`run_worker_first`、每谱 meta 注入、内容寻址缓存）·
**写回** 本机 Python 服务 + Cloudflare Tunnel + `X-Token`（边缘只反代）·
**CI/质量** GitHub Actions + 78 个自检脚本 + Python↔JS 口径互锁 + 金曲榜评测（错音必须为 0）

## 本地跑

```bash
# 站点（只读预览 + 可写回，端口 8770）
py -3.13 app/server.py 8770        # 需要 Python 3.10+；服务本身只用标准库

# 自检
node tools/check_ui.mjs && node tools/check_search.mjs && node tools/check_live.mjs
node tools/bench_search.mjs 40      # 量索引构建与查询延迟
```

写路径与语料在另外两个仓库：语料 `Francium-223/jianpu-db`、流水线工具 `jianpu2`。

## 许可与出处

曲谱的**页面地址与元数据**来自 qupu123 / jianpu.cn / jianpujia 三个公开站点；
本仓库只保存**自己转写的数字序列**与出处链接，不打包、不外链原扫描件。

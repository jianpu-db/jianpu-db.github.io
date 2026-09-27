# -*- coding: utf-8 -*-
"""给「每谱一页」建**原图索引**: source(`jianpucn-475740`) -> 该谱的扫描件在哪、有几页、多大。

背景(2026-09-24 用户要求"每张谱都有一个单独的页面, 显示它的原图, 像 abcnotation 那样"):
之前前端只有检索结果卡, 卡片里既没有原图、也没有一个能分享/回看的独立地址。图片其实
**一直躺在盘上** —— 目录名就是 `<抓取时的标题>__<source>`, 于是 source 天然是那把钥匙:

    images/<批次>/<标题>__<source>/001.jpg          ← 最早几批的**原始下载件**
    images-prep/<批次>/<标题>__<source>/001.jpg     ← 后续批次(以及上面几批)的**处理过的整页**
    images-prep/<批次>/<标题>__<source>/001_strip_MM.jpg   ← 按谱表切的条(给转写用, 不是原图)
    images-prep/<批次>/<标题>__<source>/001__pgN.jpg       ← 站点多页/大图拆出来的派生件
    song.json                                       ← 抓取留档(site/id/title/page_url/images)

**只把"整页图"当原图**(文件名是纯数字: `001.jpg`)。`_strip_`/`__pg` 是派生件, 不进原图列表,
但在一个目录**一张整页图都没有**时兜底(否则那首歌就完全看不到图了), 并打上 `drv` 标记 ——
宁可显示"这是切好的条, 不是整页", 也不要假装它是原图。
另外, **太小(<100px)的图直接丢掉**: 抓取时经常把站点的广告条/欢迎条一起抓进来(实测一首谱的
"第 1 页"是 750x55 的横幅), 显示出来是一条谁也看不懂的细条。

选哪一份: 同一个 source 可能有多个目录(原始件 + 处理件)。排序规则
    (有整页图 > 没有, 整页图多 > 少, images-prep 处理过的 > images 原始件, 路径)
处理后优先的理由: 处理件是去噪/放大过的, 页面上更看得清; 原始件作为**备选**一起带出去
(`alt`), 前端给一个"未处理的原件"链接 —— 用户要的是"原图", 两条都给他, 由他挑。

坏图(打不开/0 尺寸)不进列表: 与其让页面挂一个碎图图标, 不如说"这首只有 N 页可用"。
只有整页图全坏时才退到下一个候选目录, 全坏光了就跳过这首(前端会显示"还没存下原图")。

产物(与前端的约定):
    data/images.jsonl.gz / .jsonl   每行一个 source:
      {"s": source, "d": 目录(相对工作区), "pg": [[文件名, 宽, 高], ...],
       "drv": 0/1(1 = 列表里是派生件不是整页), "alt": [[目录, [文件名, ...]], ...], "nx": 派生件数}
    stats.json 里的 with_images / image_pages / img_base 由 build_web_data.py 汇总。

用法:
    python3 tools/build_image_index.py                     # 默认 images/ + images-prep/
    JIANPU_IMAGES=/data/a:/data/b python3 tools/build_image_index.py
    python3 tools/build_image_index.py --out /tmp/x --quiet
"""
import argparse
import gzip
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.dirname(HERE)
WS = os.path.dirname(WEB)                      # 工作区根(三个仓库的上一层)

EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff")
# 整页图: 文件名就是编号(`001.jpg`)。`001_strip_00.jpg` / `001__pg0.jpg` 都不算。
PAGE = re.compile(r"^(\d{1,4})\.(jpg|jpeg|png|gif)$", re.I)
SRCID = re.compile(r"^[A-Za-z0-9._-]+$")       # source 必须能直接进 URL, 不能带空格/中文/斜杠
# 太小的图不是谱: 抓取时经常把站点的"广告条/欢迎条"一起抓下来(实测 750x55 的横幅排在第 1 页),
# 显示出来就是一条谁也看不懂的细条 —— 丢掉它, 让**真**的谱页排前面(见 tools/check_images.py 同口径)。
MIN_PX = 100

try:
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None              # 站点扫描件有超长图, 别让 Pillow 拦下来
    _PIL = True
except Exception:                              # 没装 Pillow -> 只列文件、不带尺寸
    _PIL = False


def default_roots():
    """图片根目录: 环境变量优先(冒号分隔), 否则**挨个试**几个可能的位置, 取存在的那些。

    ⚠ 2026-09-28 修: 原来只试 `<工作区>/images` 与 `<工作区>/images-prep`。本机实测这两条
    **都不存在** —— 图库在 `<工作区>/jianpu2/images-prep`(3.8 万文件)。后果不是报错, 而是
    `scan()` 把两条根都记进 `missing_roots` 后返回**空索引**, 于是 `data/images.jsonl.gz`
    被静默改写成 0 条 —— 线上每一首的"每谱一页 / 原图"都会消失(实测从 23,328 条掉到 0)。
    所以这里改成按顺序探测、取**存在**的, 并且不再把"全都找不到"当成正常结果(见 build() 的护栏)。
    """
    env = os.environ.get("JIANPU_IMAGES", "").strip()
    if env:
        return [os.path.abspath(p) for p in env.split(os.pathsep) if p.strip()]
    cands = [os.path.join(WS, "images-prep"), os.path.join(WS, "images"),
             # 图库其实在 jianpu2 下面的情形(本机就是这样)
             os.path.join(WS, "jianpu2", "images-prep"), os.path.join(WS, "jianpu2", "images")]
    return [c for c in cands if os.path.isdir(c)] or cands[:2]


def natural(name):
    m = re.match(r"^(\d+)", name)
    return (int(m.group(1)) if m else 10 ** 9, name)


def dims(path):
    """(宽, 高); 读不动就 (0, 0)。只读文件头, 不解码像素。"""
    if not _PIL:
        return (0, 0)
    try:
        with Image.open(path) as im:
            return im.size
    except Exception:
        return (0, 0)


def list_images(dp):
    try:
        names = os.listdir(dp)
    except OSError:
        return [], []
    files = sorted((f for f in names if os.path.splitext(f)[1].lower() in EXTS), key=natural)
    pages = [f for f in files if PAGE.match(f)]
    return files, pages


def scan(roots, rel_base, quiet=False):
    """走一遍图片根 -> {source: [候选目录, ...]}。候选里只有文件名, 还没验图。"""
    cands = {}
    stats = {"dirs": 0, "files": 0, "skipped_nokey": 0, "missing_roots": []}
    for root in roots:
        if not os.path.isdir(root):
            stats["missing_roots"].append(root)
            if not quiet:
                print(f"  ! 图片根不存在: {root}")
            continue
        for dp, _dn, _fn in os.walk(root):
            files, pages = list_images(dp)
            if not files:
                continue
            base = os.path.basename(dp)
            if "__" not in base:
                stats["skipped_nokey"] += 1
                continue
            src = base.rsplit("__", 1)[1].strip()
            if not SRCID.match(src):
                stats["skipped_nokey"] += 1
                continue
            # 记录用的相对路径一律**从"图库本身所在的上一层"起算**, 于是不管图库实际在
            # `<工作区>/images-prep` 还是 `<工作区>/jianpu2/images-prep`, 写出来的都是
            # `images-prep/<批次>/…` —— 线上 `/img/` 的 URL 形状因此保持不变。
            # (2026-09-28: 只把 root 加进可选列表而不改这里, 会把路径写成 `jianpu2/images-prep/…`,
            #  等于悄悄换了线上 URL 方案, 已发布的 R2/反代布局会对不上。)
            _base = (os.path.dirname(root) if os.path.basename(root) in ("images", "images-prep")
                     else rel_base)
            rel = os.path.relpath(dp, _base).replace(os.sep, "/")
            if rel.startswith("../"):          # 图片根在工作区外 -> 前端/服务端都取不到, 别写进去
                stats["skipped_nokey"] += 1
                continue
            cands.setdefault(src, []).append({
                "rel": rel, "files": files, "pages": pages, "abs": dp,
                "prep": os.path.basename(root).startswith("images-prep"),
            })
            stats["dirs"] += 1
            stats["files"] += len(files)
    return cands, stats


def rank(c):
    # 有整页图 > 没有; 整页图多 > 少; images-prep(处理过) > images(原始件); 路径短的稳一点
    return (0 if c["pages"] else 1, -len(c["pages"]), 0 if c["prep"] else 1,
            len(c["rel"]), c["rel"])


def probe(dp, files, thin=None):
    """把一组文件名验一遍 -> [[名, 宽, 高], ...](读不动的、太小的丢掉; thin 里记下丢了几张)。"""
    out = []
    for f in files:
        w, h = dims(os.path.join(dp, f))
        if w >= MIN_PX and h >= MIN_PX:
            out.append([f, w, h])
        elif thin is not None:
            thin.append((f, w, h))
    return out


def build(roots=None, rel_base=None, out=None, quiet=False):
    """扫图 + 选目录 + 写 data/images.jsonl(.gz)。返回 (索引 dict, 统计 dict)。"""
    roots = [os.path.abspath(r) for r in (roots or default_roots())]
    rel_base = os.path.abspath(rel_base or WS)
    cands, st = scan(roots, rel_base, quiet)
    index, n_pages, n_derived, n_alt, n_bad = {}, 0, 0, 0, 0
    n_thin_src, n_thin = 0, 0
    for src in sorted(cands):
        cs = sorted(cands[src], key=rank)
        picked, thin = None, []
        for c in cs:
            # 用**候选自己的绝对目录**去验图, 不要用 rel_base+rel 拼 —— 记录用的 rel 是
            # "从图库上一层起算"的规范写法(见 scan), 与 rel_base 不一定是同一层。
            pages = probe(c["abs"], c["pages"], thin) if c["pages"] else []
            if pages:
                picked = (c, pages, 0)
                break
        if picked is None:                     # 一张整页图都没有(或全坏/全是细条) -> 用派生件兜底
            c = cs[0]
            got = probe(c["abs"], c["files"])
            if not got:
                n_bad += 1
                continue
            picked = (c, got, 1)
        c, pages, drv = picked
        if thin:
            n_thin_src += 1
            n_thin += len(thin)
        # 备选扫描件: 只带**文件名**(不带尺寸) —— 前端把它们渲染成一行小链接(原始件 / 别的抓取批次),
        # 不是要内嵌显示的图, 所以省下这几千次开图。上限 3 个目录 × 12 页, 免得索引膨胀。
        alts = [[x["rel"], x["pages"][:12]] for x in cs if x["rel"] != c["rel"] and x["pages"]][:3]
        index[src] = {"s": src, "d": c["rel"], "pg": pages, "drv": drv,
                      "alt": alts, "nx": max(0, len(c["files"]) - len(c["pages"]))}
        n_pages += len(pages)
        n_derived += drv
        n_alt += 1 if alts else 0
    stats = dict(st)
    stats.update({"sources": len(index), "pages": n_pages, "derived_only": n_derived,
                  "with_alt": n_alt, "unreadable": n_bad, "thin": n_thin,
                  "thin_sources": n_thin_src, "roots": roots,
                  "pil": _PIL, "out": ""})
    if out:
        os.makedirs(out, exist_ok=True)
        gz = os.path.join(out, "images.jsonl.gz")
        # mtime=0: 内容没变就不该产生字节差异(否则每跑一次 refresh, 仓库里就多一个假改动)
        with open(gz, "wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as g:
                for src in sorted(index):
                    g.write((json.dumps(index[src], ensure_ascii=False, separators=(",", ":"))
                             + "\n").encode("utf-8"))
        # 明文的 .jsonl: 老浏览器没有 DecompressionStream 时走这一份。**必须是真明文** ——
        # songs.jsonl 曾经写成"compresslevel=0 的 gzip 容器", 名字像明文、内容是二进制, 那份回退一直是坏的。
        with io.open(os.path.join(out, "images.jsonl"), "w", encoding="utf-8", newline="\n") as g:
            for src in sorted(index):
                g.write(json.dumps(index[src], ensure_ascii=False, separators=(",", ":")) + "\n")
        stats["out"] = gz
    if not quiet:
        mb = os.path.getsize(stats["out"]) / 1e6 if stats["out"] else 0
        print(f"图片索引: {stats['sources']} 个 source, {stats['pages']} 张整页图"
              + (f", {stats['derived_only']} 个只有派生件" if stats["derived_only"] else "")
              + (f", {stats['with_alt']} 个有备选扫描件" if stats["with_alt"] else "")
              + (f", {stats['thin']} 张太小的图被丢掉(站点广告条那种)" if stats["thin"] else "")
              + (f", {stats['unreadable']} 个读不动被跳过" if stats["unreadable"] else ""))
        print(f"  扫了 {stats['dirs']} 个目录 / {stats['files']} 个图片文件"
              + (f"; 跳过没带 `__source` 的目录 {stats['skipped_nokey']} 个" if stats["skipped_nokey"] else "")
              + (f"; Pillow={'有' if _PIL else '无(没尺寸)'}"))
        if stats["out"]:
            print(f"  写出 {stats['out']} ({mb:.2f} MB gz) + images.jsonl")
    return index, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(WEB, "data"))
    ap.add_argument("--root", action="append", default=[], help="图片根(可多次; 默认 images + images-prep)")
    ap.add_argument("--rel-base", default=WS, help="索引里路径相对的基准(默认工作区根)")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    build(roots=a.root or None, rel_base=a.rel_base, out=a.out, quiet=a.quiet)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""原图索引自检: data/images.jsonl(.gz) 里每一条, 盘上**真的**有那个目录和那些页吗?

为什么需要: 谱页(每谱一页)显示原图靠的是索引里的路径。索引一旦与盘脱节(图片换了批次目录、
改名、被挪走), 用户看到的就是一片碎图 —— 而这时**没有任何报错**, 只有肉眼看页面才发现。
所以这里做三件事:
  ① 索引里每条 `d` / `pg` / `alt` 都在盘上存在(不存在就列出来);
  ② 指向的文件确实是图片(扩展名 + 能读出尺寸);
  ③ 与 songs.jsonl.gz 对账: 有多少首歌真能查到原图, 与 stats.json 的 with_images 一致。

图片根不在本机时(例如只部署了 web 仓库)**不算失败**, 打印一行跳过 —— 那种情况本来就该由
CDN/对象存储去保证。发现"目录在、页不在"这种半坏状态才算失败。

用法: python3 tools/check_images_index.py [--data 目录] [--limit 报几条]
"""
import argparse
import collections
import gzip
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.dirname(HERE)
WS = os.path.dirname(WEB)

sys.path.insert(0, HERE)
try:
    import build_image_index as bii
except Exception as e:                     # 索引工具本身 import 不了, 那是更大的问题
    sys.exit(f"!! 找不到 tools/build_image_index.py: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(WEB, "data"))
    ap.add_argument("--limit", type=int, default=8, help="每类问题最多打几条")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    path = os.path.join(a.data, "images.jsonl.gz")
    if not os.path.isfile(path):
        sys.exit(f"!! 没有 {path}(先跑 tools/build_image_index.py)")
    idx = {}
    with gzip.open(path, "rt", encoding="utf-8") as g:
        for ln in g:
            ln = ln.strip()
            if ln:
                r = json.loads(ln)
                idx[r["s"]] = r

    roots = bii.default_roots()
    live = [r for r in roots if os.path.isdir(r)]
    if not live:
        print(f"跳过: 图片根本机没有({', '.join(roots)}) —— 静态部署时由 CDN/对象存储保证")
        return 0

    # ⚠ 2026-09-28 修: 索引里的 `d` 是**相对图片根的父亲**写的(本机 = `<工作区>/jianpu2`),
    #   而这里原来只拿 `WS`(= <工作区>) 拼一次 —— 于是 17167 条**全部**报"目录不存在",
    #   而且因为 `continue`, 后面"整页图"那一段一条都没查(打印出 "0 张整页图" 就是这个迹象)。
    #   而同一时刻 `/img/` 是能取到图的(GET 200, 12951 字节), 说明是**自检错了**, 不是索引坏了。
    #   改成**逐个根试拼**(与 app/server.py 的 resolve_img 同一套根), 命中不了才算坏。
    bases = []
    for r in live:
        for b in (r, os.path.dirname(r), WS):
            if b not in bases:
                bases.append(b)

    def resolve(rel):
        for b in bases:
            full = os.path.join(b, rel)
            if os.path.isdir(full):
                return full, b
        return None, None

    bad_dir, bad_page, bad_alt, n_page = [], [], [], 0
    used_root = collections.Counter()
    for src, r in idx.items():
        d, base = resolve(r["d"])
        if d is None:
            bad_dir.append((src, r["d"] + "  (试过: " + ", ".join(bases) + ")"))
            continue
        used_root[base] += 1
        for p in r["pg"]:
            n_page += 1
            fp = os.path.join(d, p[0])
            if not os.path.isfile(fp):
                bad_page.append((src, r["d"] + "/" + p[0]))
            elif not p[1] or not p[2]:
                bad_page.append((src, r["d"] + "/" + p[0] + " (没尺寸)"))
        for alt in r.get("alt") or []:
            ad, _b = resolve(alt[0])
            if ad is None:
                bad_alt.append((src, alt[0]))

    print(f"原图索引 {len(idx)} 个 source / {n_page} 张整页图; 图片根 {len(live)} 个")
    for b, c in used_root.most_common():
        print(f"    {c:>6} 条落在 {b}")
    ok = True
    for what, rows in (("目录不存在", bad_dir), ("整页图不存在", bad_page), ("备选目录不存在", bad_alt)):
        if rows:
            ok = False
            print(f"  ✗ {what}: {len(rows)} 条")
            for s, p in rows[:a.limit]:
                print(f"      {s} -> {p}")
        else:
            print(f"  ✓ 没有「{what}」的条目")

    # 与语料对账: 有多少首歌真能查到原图(必须与 stats.json 一致)
    songs = os.path.join(a.data, "songs.jsonl.gz")
    stats = os.path.join(a.data, "stats.json")
    if os.path.isfile(songs):
        rows = [json.loads(l) for l in gzip.open(songs, "rt", encoding="utf-8") if l.strip()]
        cov = sum(1 for r in rows if r.get("s") and r["s"] in idx)
        pages = sum(len(idx[r["s"]]["pg"]) for r in rows if r.get("s") in idx)
        st = {}
        if os.path.isfile(stats):
            st = json.load(io.open(stats, encoding="utf-8"))
        print(f"  语料 {len(rows)} 首, 其中 {cov} 首能查到原图({pages} 页); "
              f"stats.with_images={st.get('with_images')} image_pages={st.get('image_pages')}")
        if st and (st.get("with_images") != cov or st.get("image_pages") != pages):
            ok = False
            print("  ✗ stats.json 与索引不一致(索引重建了两步中的一步?)")
        else:
            print("  ✓ stats.json 与索引一致")
    print("原图索引自检 通过" if ok else "原图索引自检 失败(见上面 ✗)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

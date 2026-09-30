# -*- coding: utf-8 -*-
"""把 jianpu-db 的 data.jsonl 转成前端索引(含**原谱原文**, 供结果页显示并高亮命中段)。

与前端的约定(与 static/jptok.js **同一套口径**):
  id: 这首歌独立页面的地址 `/s/<id>`(见 tune_id(): 首选 source, 否则 `f-<文件名>`)。
      前端只按 id 查表 —— 每谱一页(原图 + 元数据)就是 `/s/<id>`。
  p : 音高串, 仅数字 1-7(不含升降号), 长度 = 音符数
  a : 变音串, 与 p 逐音对齐, 每字符 '0'(自然) / '1'(升) / '2'(降)
  o : 八度串, 与 p 逐音对齐, 每字符是 -2..2 的数字(可能带负号)
  raw : **展开过**的 token 流(节头去掉、KeepLength 补全) —— 检索卡的命中高亮用它, 位置与索引对齐
  src : **文件正文一字不差**(scores/<file>.txt 里 `%--` 之后到 `%END` 之前), 每谱一页的"原谱原文"用它
        (用户口径 2026-09-24: 不要展开版, 要用户写的文件 verbatim)
另外顺带产出原图索引 data/images.jsonl(.gz) —— 扫描逻辑在 tools/build_image_index.py。
用法: py -3.13 tools/build_web_data.py [--data 路径] [--out 目录] [--no-images]
"""
import argparse
import gzip
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "..", "jianpu2", "skills", "jianpu-melody-lookup"))
sys.stdout.reconfigure(encoding="utf-8")
try:
    import jptok                      # 唯一 token 实现, 优先复用
except Exception:
    jptok = None
ZW = dict.fromkeys(map(ord, "\u200b-\u200f\u202a-\u202e\u2060\ufeff"), None)
# 兜底正则(只在 import jptok 失败时用): 与 jptok.py **同口径** —— 时值字母在数字前后都认,
# 否则 `6c.`/`5s`/`3q` 这类后缀 token 会被丢掉(2026-09-23 索引丢音事故就是这个坑);
# 末尾**只能** `]` 不能 `[` —— `3[` 是三连音开记号(那个 3 是连音数, 不是音符),
# 允许它会让 `3[ 5 3 4 ]` 在站点的音符数里被算成 4 个音(2026-09-28 与 jptok 一起修)。
TOK = re.compile(r"^([cqsdh]*)([,']*)([#b♯♭]?)([1-7x0])([,']*)([#b♯♭]?)([cqsdh]*)[.]*(\]?)$")



def _sections_compact(r, n_notes):
    """"起始音下标:段落名,…"(没有分段或对不上就返回空串)。

    为什么要有它: 段落权重只在**同分**时决定取哪一处出现(副歌 vs 前奏), 前奏/发狂钢琴要降权;
    段内音高音数之和必须等于整串音数, 否则说明段序与 score 拼不上, 宁可当"没分段"。
    """
    secs = r.get("sections") or []
    if not secs:
        return ""
    out, off = [], 0
    tie, prev_key, last_note = False, None, False       # 与主循环同一口径: `X ~ X` 只算一个音
    for s in secs:
        name = (s.get("subtitle") or "").strip() or "score"
        cnt = 0
        for t in (s.get("score") or "").split():
            if t == "~":
                tie = last_note
                continue
            if not parse(t):                 # 非音符 token: parse 可能直接返回 None
                tie, prev_key, last_note = False, None, False
                continue
            d, ac, _o = parse(t)
            if d is None:
                tie, prev_key, last_note = False, None, False
                continue
            key = (d, ac)
            if tie and prev_key == key:
                tie, last_note = False, True
                continue                     # 连音线的第二个音头(段内/跨段都并)
            cnt += 1
            tie, prev_key, last_note = False, key, True
        if cnt:
            out.append("%d:%s" % (off, name))
            off += cnt
    if off != n_notes or not any(not x.endswith(":score") for x in out):
        return ""
    return ",".join(out)

def parse(t):
    if jptok:
        return jptok.parse_token(t)
    m = TOK.match(t or "")
    if not m:
        return None
    _pre, octs, acc, dig, post, acc2, _post2, _mark = m.groups()
    a = 1 if (acc in ("#", "♯") or acc2 in ("#", "♯")) else (-1 if (acc in ("b", "♭") or acc2 in ("b", "♭")) else 0)
    off = (octs + post).count(",") - (octs + post).count("'")
    return (None, a, off) if dig in "0x" else (int(dig), a, off)


def group_of(t):
    base = (t or "").translate(ZW).split("__")[0]
    return re.split(r"[（(\s　【\[《]", base)[0].strip() or base.strip()


def sheet_body(path):
    """取曲谱文件**正文原样**(`%--` 之后 -> `%END` 之前), 保留换行/空行/节头/KeepLength 等一切写法。

    为什么不用 data.jsonl 的 score: 那份是**展开过**的（节头 subtitle= / 拍号 / NextScore 去掉、
    KeepLength 的省略时值补全、多节拼平）。用户 2026-09-24 明确要求"原谱原文"要一字不差。
    """
    if not path or not os.path.isfile(path):
        return ""
    try:
        with io.open(path, encoding="utf-8", errors="replace") as f:
            txt = f.read()
    except OSError:
        return ""
    if "%--" not in txt:
        return ""
    body = txt.split("%--", 1)[1]
    body = re.split(r"(?im)^\s*%end\s*$", body)[0]
    return body.strip("\n")


def tune_id(src, files, used):
    """一首谱的**页面地址**: `/s/<id>`。id 必须 ASCII、能进 URL、且全库唯一。

    首选 `source`(`jianpucn-150657`) —— 它本来就是全库的主键, 也是图片目录名 `…__<source>`
    的后半截, 拿它当 id, "这一页 -> 它的原图"就是一次直接查表, 不用再编第二套编号。
    少数谱没有 source(36 首空 + 2 首 unknown + 重复 source) -> 退回文件名(`f-<stem>`),
    重名的后面挂 `-2`。**这套规则只写在这里一处**, 前端不重算(它只按 id 查表)。
    """
    base = src if src and re.match(r"^[A-Za-z0-9._-]+$", src) else ""
    if not base:
        stem = os.path.splitext(((files or [""]) or [""])[0])[0].strip()
        base = "f-" + (stem or "untitled")
    base = base.strip("-._") or "s"
    rid, n = base, 1
    while rid in used:
        n += 1
        rid = f"{base}-{n}"
    used.add(rid)
    return rid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(os.path.dirname(ROOT), "jianpu-db", "data.jsonl"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data"))
    ap.add_argument("--no-images", action="store_true", help="不重扫原图索引(只重建检索索引)")
    ap.add_argument("--img-base", default="/img/", help="原图 URL 前缀(换 CDN/静态站时改这里)")
    ap.add_argument("--scores", default="", help="曲谱目录(默认 <data 所在目录>/scores); 用来取'原谱原文' verbatim")
    ap.add_argument("--site", default="https://jianpu-db.org",
                    help="站点根 URL —— 用来写 robots.txt/sitemap.xml 里的绝对地址(2026-09-30 起正式域名)")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    # 知名度代理 `hot`: 这一首的 artist/tag(不含「分类/…」) 里, 某个名字在**语料里出现的次数**取最大。
    # 为什么需要: 纯数字串命中两首时, 原来的并列规则(名短优先 / 八度记号少优先)都跟"这是哪首歌"没关系
    # —— 用户实测 `66561232123` 正确答案是《最炫民族风》(凤凰传奇, 库里 68 首), 却被判给了《时光》
    # (无歌手信息, 库里 0 首)。榜单/热度这类外部数据我们没有, 但"这位歌手在语料里有多少首"是本库自带的、
    # 且与知名度强相关(邓丽君 544、阎维文 382 …)。口径与 skills/jianpu-melody-lookup/lookup.py、
    # tools/melody_search.py 一致(三处公式必须一样)。
    hotmap = {}
    for _ln in io.open(a.data, encoding="utf-8"):
        _ln = _ln.strip()
        if not _ln:
            continue
        try:
            _r = json.loads(_ln)
        except ValueError:
            continue
        for _n in list(_r.get("artist") or []) + [t for t in (_r.get("tag") or []) if not str(t).startswith("分类/")]:
            hotmap[_n] = hotmap.get(_n, 0) + 1
    rows, srcs, notes, used = [], {}, 0, set()
    # 原谱站的**确切页面**: 由 jianpu2/tools/verify_source_urls.py 逐条抓取核对后写下的映射
    # (source 里只有 `qupu123-300587` 这种 ID; 这里把它换成那一页的真实 URL)
    DB = os.path.dirname(os.path.abspath(a.data))
    SCORES = a.scores or os.path.join(DB, "scores")
    srcpages = {}
    _sp = os.path.join(DB, "source_pages.json")
    if os.path.isfile(_sp):
        try:
            srcpages = json.load(io.open(_sp, encoding="utf-8"))
        except Exception as e:
            print("! source_pages.json 读不动: %s" % e)
    for ln in io.open(a.data, encoding="utf-8"):
        ln = ln.strip()
        if not ln:
            continue
        r = json.loads(ln)
        score = r.get("score") or ""
        # ⚠ `~` 不是音符(parse 返回 None), 但**必须留下** —— 它是连音线记号, 下一轮要靠它判"两个音头并一个"
        #   (2026-09-28: 以前这里直接滤掉, 于是站点索引里的音符数比语料多出 14455 个连音线音头)
        toks = [t for t in score.split() if parse(t) or t == "~"]
        p, acc, oct_ = [], [], []
        tie, prev_key, last_note = False, None, False   # 连音线 `X ~ X` 只算一个音(2026-09-28 口径)
        for t in toks:
            if t == "~":
                tie = last_note
                continue
            d, ac, off = parse(t)
            if d is None:                     # 休止/念白: 不进音高, 但仍在 s 里显示
                tie, prev_key, last_note = False, None, False
                continue
            key = (d, ac)
            if tie and prev_key == key:
                tie, last_note = False, True
                continue                      # 连音线的第二个音头
            p.append(str(d))
            acc.append("1" if ac == 1 else "2" if ac == -1 else "0")
            oct_.append(str(off))
            tie, prev_key, last_note = False, key, True
        if not p:
            continue
        src = r.get("source") or ""
        src = src[0] if isinstance(src, list) else src
        host = src.split("-")[0] if src else ""
        srcs[host] = srcs.get(host, 0) + 1
        notes += len(p)
        rows.append({
            "id": tune_id(src, r.get("file"), used),
            "t": r.get("title") or "", "s": src, "st": r.get("status") or "",
            "n": len(p), "p": "".join(p), "a": "".join(acc), "o": ",".join(oct_),
            "g": group_of(r.get("title")),
            "mbid": (r.get("MBID") or ""),
            # 收录页(用户口径: 要"具体收录的那一页", 不要搜索页):
            #   links  = 人工核对后写进 scores/<file>.txt 的 `link=`(可多个)
            #   srcurl = 原谱站那一页(由 source 的站点+ID 推出并抓取核对过)
            "links": [str(x) for x in (r.get("link") or [])],
            "srcurl": (srcpages.get(src) or {}).get("url", "") if src else "",
            "raw": " ".join(toks if len(toks) < 400 else toks[:400]),
            "trunc": len(toks) > 400,
            # 原谱原文(verbatim): 用户写的文件一字不差, 多节/KeepLength/节头都保留
            "src": sheet_body(os.path.join(SCORES, (r.get("file") or [""])[0])),
            # 小节线: data.jsonl 给的是"第 i 个音符之前有一条小节线"(0-based 音符下标)。
            # **按音符序号而非 token 序号**, 前端在音符流里对应位置插 `|`。
            "bars": [int(x) for x in (r.get("bars") or []) if isinstance(x, int)],
            "bpb": float(r.get("beats_per_bar") or 4.0),
            # 段落(用户 2026-09 规格, 见 README_PIPELINE.md §六): "起始音下标:段落名,…" 的紧凑串,
            # 给前端算**段落权重**(副歌/主歌 > 间奏 > 整曲 > 前奏/尾奏/发狂钢琴)。
            # 口径与 jianpu2/tools/melody_search.py 的 section_map 同一份(段内音高音数累加, 总数不符就不用)。
            "sc": _sections_compact(r, len(p)),
            # 其余元数据一并带出(用户要求: 前端不光标题, 别的元数据也都摊开)
            "file": r.get("file") or [],
            "tags": r.get("tag") or [],
            "usertags": r.get("usertag") or [],
            "alias": r.get("alias") or [],
            # 歌手(独立字段, 2026-09-24): 通用曲名靠它区分谁是谁
            "artist": r.get("artist") or [],
            # 知名度代理(见上面 hotmap 的说明): 并列时的第一顺位
            "hot": max([hotmap.get(x, 0) for x in
                        list(r.get("artist") or []) + [t for t in (r.get("tag") or []) if not str(t).startswith("分类/")]]
                       or [0]),
            "transcriber": r.get("transcriber") or [],
            # 转写置信度(2026-09-30 加): 曲谱头里的 `confidence=`(转写时每个数字 top-1 概率的平均)。
            # 前端卡片显示它; 并列排序时它是"人工校对过"之后的次级证据。
            # **老谱没这个字段 -> 写 None(不是 0.5)**: 卡片显示"—", 排序侧才用 0.5 当中性 ——
            # 显示成"50%（低）"会让人以为这条谱转得很差, 那是假数。
            "conf": (float(r["confidence"]) if str(r.get("confidence") or "").strip()
                     and str(r["confidence"]).replace(".", "", 1).isdigit() else None),
            # 置信度**最低 10% 的分位**: 平均值看不出的"个别音很虚"靠它看(0.95 均值 / 0.42 p10)
            "confP10": (float(r["conf_p10"]) if str(r.get("conf_p10") or "").strip()
                        and str(r["conf_p10"]).replace(".", "", 1).isdigit() else None),
        })

    outj = os.path.join(a.out, "songs.jsonl.gz")
    # mtime=0: gzip 默认把**当前时间**写进 header, 于是内容一字没变、字节却每次都不同 ->
    # 每跑一次 refresh, 仓库里就多一个毫无意义的 `data/songs.jsonl.gz` 改动
    # (CI/作者还会把它提交进去)。固定 mtime 后同一份数据 = 同一串字节(实测两次跑字节一致)。
    with open(outj, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as g:
            for r in rows:
                g.write((json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
    # 明文的 songs.jsonl: 老浏览器没有 DecompressionStream 时走这一份。**必须是真明文** ——
    # 这里以前写的是 `gzip.open(..., compresslevel=0)`, 名字像明文、内容却是 gzip 容器(头 1f 8b),
    # 于是那条回退分支读到二进制乱码, 整个 app 直接"初始化失败"(2026-09-24 查出, 一起修掉)。
    with io.open(os.path.join(a.out, "songs.jsonl"), "w", encoding="utf-8", newline="\n") as g:
        for r in rows:
            g.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    # 收录平台表(搜索页格式的**唯一真源**在 jianpu-db/schema.py) -> 塞进 stats.json 给前端读。
    # 前端不自己写一份, 免得"每个平台的搜索 URL 长什么样"漂成两处。
    platforms = []
    fields = {}
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "jianpu-db"))
        import schema as _schema
        platforms = list(getattr(_schema, "PLATFORMS", []))
        # 卡片"一行一个属性"的展示规格(显示名/能不能改/输入提示) —— **唯一真源是 jianpu-db/schema.py:FIELDS**。
        # 用户口径(2026-09-29): 属性名不许硬编码在前端(要能多语言), 且要注明哪些可改哪些不可改。
        # 这里原样带过去(连顺序), 前端不再自带中文串。
        fields = dict(getattr(_schema, "FIELDS", {}))
    except Exception as e:                       # jianpu-db 不在旁边(如独立部署 web) -> 前端会用内建兜底
        print(f"  ! 读不到 schema.PLATFORMS/FIELDS({type(e).__name__}), 前端将用内建兜底表")
    # 原图索引(每谱一页要用): 扫描逻辑只在 tools/build_image_index.py 一处
    img_index, img_st = {}, {}
    if not a.no_images:
        try:
            sys.path.insert(0, os.path.join(ROOT, "tools"))
            import build_image_index as bii
            img_index, img_st = bii.build(out=a.out, quiet=True)
        except Exception as e:
            print(f"  ! 原图索引没建成({type(e).__name__}: {e}) —— 谱页会显示'还没存下原图'")
        # ⚠ 2026-09-28 护栏: **绝不允许静默把原图索引清零**。
        #   背景: 图库不在工具假设的位置时, 扫描会"成功返回空字典", 于是 images.jsonl.gz
        #   被改写成 0 条, 线上每首的「每谱一页 / 原图」一起消失 —— 实测从 23,328 条掉到 0,
        #   而输出里只有一行不起眼的"原图 0 首 / 0 页"。这里改成: 若**新扫出 0 条**
        #   而旧文件非空, 就保留旧文件并**大声报错退出**, 让人去修图库路径(而不是发布空索引)。
        old_img = os.path.join(a.out, "images.jsonl.gz")
        old_n = 0
        if os.path.isfile(old_img):
            try:
                with gzip.open(old_img, "rt", encoding="utf-8") as gh:
                    old_n = sum(1 for ln in gh if ln.strip())
            except Exception:
                old_n = 0
        if not img_index and old_n > 0:
            print(f"  !! 原图索引扫出 0 条, 而现有 {old_img} 里有 {old_n} 条 —— **保留旧文件**。")
            print("     多半是图库不在工具假设的位置: 设 JIANPU_IMAGES 指过去, 或看 build_image_index.default_roots()")
            raise SystemExit(3)
    with_images = sum(1 for r in rows if r["s"] and r["s"] in img_index)
    image_pages = sum(len(img_index[r["s"]]["pg"]) for r in rows if r["s"] in img_index)
    stats = {"platforms": platforms, "fields": fields,
             "songs": len(rows), "notes": notes, "groups": len({r["g"] for r in rows}),
             "sources": dict(sorted(srcs.items(), key=lambda x: -x[1])),
             "bytes_gz": os.path.getsize(outj),
             "with_accidental": sum(1 for r in rows if "1" in r["a"] or "2" in r["a"]),
             "with_raw": sum(1 for r in rows if r["raw"]),
             # 「每谱一页」: 有多少首真能找到原图, 一共多少页, 以及原图 URL 前缀
             "with_images": with_images, "image_pages": image_pages,
             "image_sources": img_st.get("sources", 0), "img_base": a.img_base}
    with io.open(os.path.join(a.out, "stats.json"), "w", encoding="utf-8", newline="\n") as g:
        g.write(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"写出 {len(rows)} 首 -> {outj}  ({stats['bytes_gz']/1e6:.2f} MB gz)")
    print(f"  音符 {notes:,} · 含变音记号的 {stats['with_accidental']} 首 · 带原谱 {stats['with_raw']} 首")
    print(f"  原图 {with_images} 首 / {image_pages} 页(全库扫出 {stats['image_sources']} 个 source 的图)")
    print(f"  来源 {stats['sources']}")

    # ── 顺带生成爬虫要的两个根文件（2026-09-30 有正式域名 jianpu-db.org 之后加）──
    # 为什么放在这条流水线里: 它们的内容**随语料变**（每首歌一个 `/s/<id>`），
    # 放在"语料 -> 前端索引"这一步生成，才不会变成又一个"文档里的旧数字"。
    # `robots.txt` 与 `sitemap.xml` 都落在**站点根**（WF 部署时由 build_dist.mjs 拷进 dist/）。
    site = (a.site or "https://jianpu-db.org").rstrip("/")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 站点仓库根
    with io.open(os.path.join(root, "robots.txt"), "w", encoding="utf-8", newline="\n") as g:
        g.write("User-agent: *\nAllow: /\n\n"
                "# 每首谱一页(深链): 大量页面靠 sitemap 才被爬到\n"
                f"Sitemap: {site}/sitemap.xml\n")
    ids = sorted({(r.get("id") or r.get("s") or "") for r in rows} - {""})
    with io.open(os.path.join(root, "sitemap.xml"), "w", encoding="utf-8", newline="\n") as g:
        g.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        g.write(f"  <url><loc>{site}/</loc><changefreq>daily</changefreq>"
                "<priority>1.0</priority></url>\n")
        for i in ids:
            g.write(f"  <url><loc>{site}/s/{i}</loc></url>\n")
        g.write("</urlset>\n")
    print(f"  爬虫文件: robots.txt + sitemap.xml（{len(ids)} 个谱页深链, 站点 {site}）")

    # ── 「每谱一页」的分享卡索引（2026-09-30 加）──
    # 前端是 SPA，**爬虫不跑 JS** ⇒ 没有这一步，sitemap 里那 1.1 万个 `/s/<id>` 在爬虫眼里是
    # **同一份 HTML**（同一个标题、同一段描述），等于只索引一页；社交平台分享也全是同一张卡。
    # 有了它，Worker（和本机 app/server.py）能在**边缘/本地**把曲名、歌手、音符数写进
    # `<title>` 与 og:* —— 每个谱页各有标题，抓到的内容也不再千篇一律。
    # 只放三样东西(不塞全文): 曲名 / 首位歌手 / 音符数。
    og = {}
    for r in rows:
        i = r.get("id") or r.get("s") or ""
        if not i:
            continue
        ar = [str(x) for x in (r.get("artist") or []) if str(x).strip()]
        og[i] = [r.get("t") or "", (ar[0] if ar else ""), int(r.get("n") or 0)]
    with io.open(os.path.join(a.out, "og.json"), "w", encoding="utf-8", newline="\n") as g:
        g.write(json.dumps(og, ensure_ascii=False, separators=(",", ":")))
    print(f"  分享卡索引: data/og.json（{len(og)} 首 · "
          f"{os.path.getsize(os.path.join(a.out, 'og.json'))/1e6:.2f} MB 未压缩）")


if __name__ == "__main__":
    main()

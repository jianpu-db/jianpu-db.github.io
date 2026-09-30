# -*- coding: utf-8 -*-
"""生成分享卡片图 `static/og.png`（1200×630，OG/Twitter 用）。

为什么需要: 现在有了正式域名 jianpu-db.org，链接会被贴到群里/微博/HF 讨论区 —— 没有 og:image 时
预览就是一行灰字，很难看也不好认。这张图走**站点自己的配色**（`static/style.css` 的
`--fg #1f2328 / --dim #57606a / --accent #0b62c4`，白底），和页面观感一致。

图上放三样:
  1. 站点名 + 一句话说明；
  2. **一行真简谱**（数字 + 小节线 + 下加点 + 减时线）当装饰 —— 这站是干什么的，一眼就看出来；
  3. 底部的实测数字（**从 `data/stats.json` 读**，不写死，免得又变成"文档里的旧数字"）。

用法:  py -3.13 tools/make_og.py            # 写 static/og.png
      py -3.13 tools/make_og.py --out x.png
"""
import argparse
import io
import json
import os
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W, H = 1200, 630
FG, DIM, ACCENT, LINE = (31, 35, 40), (87, 96, 106), (11, 98, 196), (228, 230, 234)
HEI = "C:/Windows/Fonts/msyh.ttc"
HEIB = "C:/Windows/Fonts/msyhbd.ttc"
MONO = "C:/Windows/Fonts/consola.ttf"


def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def stats():
    p = os.path.join(ROOT, "data", "stats.json")
    try:
        d = json.load(io.open(p, encoding="utf-8"))
        return int(d.get("songs") or 0), int(d.get("notes") or 0)
    except Exception:
        return 0, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "static", "og.png"))
    a = ap.parse_args()

    n_songs, n_notes = stats()
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    # 顶部一条 accent 细条 + 底部一条浅线，像页面里的分隔
    d.rectangle([0, 0, W, 10], fill=ACCENT)
    d.line([64, H - 96, W - 64, H - 96], fill=LINE, width=2)

    f_title = font(HEIB, 92)
    f_sub = font(HEI, 40)
    f_note = font(HEIB, 86)
    f_small = font(HEI, 30)
    f_mono = font(MONO, 34)

    d.text((64, 78), "jianpu-db", font=f_title, fill=FG)
    # 域名放**右上角**（第一版把它和底部那行数字挤在一起，实测重叠了 —— 别再把两段文字放同一行右侧）。
    dom = "jianpu-db.org"
    dw = d.textlength(dom, font=f_mono)
    d.text((W - 64 - dw, 128), dom, font=f_mono, fill=ACCENT)
    d.text((64, 190), "通过简谱旋律查歌 · 只哼开头几个音，把谱找出来", font=f_sub, fill=DIM)

    # —— 一行真简谱当装饰（数字 5 6 5 3 2 1 2 | 3  5 6 5 3）——
    y = 300
    x = 78
    def numeral(txt, oct_under=False, oct_dot=False, underline=False):
        nonlocal x
        w = d.textlength(txt, font=f_note)
        d.text((x, y), txt, font=f_note, fill=ACCENT)
        if oct_under:                     # 低八度：底下加一点
            d.ellipse([x + w / 2 - 5, y + 92, x + w / 2 + 5, y + 102], fill=ACCENT)
        if oct_dot:                       # 高八度：上面加一点
            d.ellipse([x + w / 2 - 5, y - 16, x + w / 2 + 5, y - 6], fill=ACCENT)
        if underline:                     # 减时线（八分音符）
            d.line([x + 4, y + 96, x + w - 4, y + 96], fill=ACCENT, width=6)
        x += w + 16

    for n, low, up, ul in (("5", 0, 0, 0), ("6", 0, 0, 1), ("5", 0, 0, 0), ("3", 0, 0, 0),
                           ("2", 1, 0, 0), ("1", 0, 0, 0), ("2", 0, 0, 0)):
        numeral(n, bool(low), bool(up), bool(ul))
    # 小节线
    d.line([x + 6, y - 6, x + 6, y + 92], fill=DIM, width=4)
    x += 34
    for n in ("3", "5", "6", "5", "3"):
        numeral(n)
    d.text((64, y + 140), "哼 5 6 5 3 2 1 2 …  →  库里 11 万段旋律一起比，按「代价」排序",
           font=f_small, fill=DIM)

    # —— 底部实测数字（一行放得下就行；放不下就缩短，**不要**再往右塞东西）——
    s = f"{n_songs:,} 首简谱 · {n_notes:,} 个音符 · 检索全在你的浏览器里跑，不上传、不留痕"
    while d.textlength(s, font=f_small) > W - 128 and len(s) > 12:
        s = s[:-1]
    d.text((64, H - 78), s, font=f_small, fill=DIM)

    im.save(a.out, "PNG", optimize=True)
    print(f"写出 {a.out}  ({os.path.getsize(a.out) / 1024:.0f} KB)  语料数字: {n_songs:,} 首 / {n_notes:,} 音符")
    return 0


if __name__ == "__main__":
    sys.exit(main())

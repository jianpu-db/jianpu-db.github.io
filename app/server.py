# -*- coding: utf-8 -*-
"""静态服务 + **零登录投稿**后端 + 每谱一页的原图服务。

为什么: 让用户去 GitHub 开 issue 是四重漏斗(链接能打开 -> 有账号 -> 愿意登录 -> 会写 issue),
每层都指数级掉人。这里改成: 前端一个按钮 -> POST 到本服务 -> 服务端**直接用本地 git 提交**
到 jianpu-db(反馈即入库), 同时把原始投稿留档。用户只需点一下, 不碰 GitHub。

接口:
  POST /api/submit   表单(JSON): {kind, title, score, note, contact}
      kind: "new"(推荐收录) / "fix"(纠错) / "meta"(元数据: 曲名/出处/标签不对)
      成功 -> 写入 jianpu-db/scores/ 或 feedback/ 并 git commit, 返回 {ok, id}
  GET  /api/health   -> {ok, repo, feedback_count}
  GET  /api/search?q=<旋律>&fuzzy=0&top=20  -> 只读检索(按旋律数字串查歌)
      参数/限流/缓存的口径全在 app/search_api.py(与 FastAPI 版共用同一份)
  GET  /ws/2/         -> MusicBrainz 风格只读 Web Service 的 API 根(2026-10-05 加)
  GET  /ws/2/song?query=<旋律>&limit=&offset=&fmt=json   -> 检索(带 {created,count,offset,songs} 信封)
  GET  /ws/2/song/<id>?fmt=json&inc=artists+tags+links   -> 单条实体(id = source 或文件名主干)
      这三条的口径同样**只有一份**在 app/search_api.py(ws2_handle);限流是每IP每秒1次、错误体是 {"error": …}
  GET  /s/<id>       -> 单页应用(: 每首谱的独立页面, 前端按 id 渲原图/元数据)
  GET  /img/<路径>   -> 原图(路径相对**工作区根**, 如 images-prep/批次/标题__source/001.jpg)

原图为什么由这里服务: 扫描件 8.9GB, 既不该进 git(前端仓库) 也不该进语料包, 它们躺在工作区
的 images/ 与 images-prep/ 里。这里只开放这两个目录(防目录穿越 + 扩展名白名单), 并且长缓存。
换静态站/CDN 时改下面 IMG_PREFIX 与前端 stats.json 里的 img_base 即可(口径仍是一处: 前端从
stats.json 读前缀, 服务端从 JIANPU_IMAGES/工作区推出根目录)。

安全: 默认只允许本机(127.0.0.1)与局域网; 有写盘 + git, 必须放在内网或加反代鉴权。
      另可设 JPSUBMIT_TOKEN 环境变量, 设了则要求请求头 X-Token 一致。
"""
import gzip
import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, unquote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WS = os.path.dirname(ROOT)                     # 工作区根(images/ 与 images-prep/ 就在这下面)


def _find_db():
    """语料仓库在哪 —— **先看环境变量，再看几个约定位置**（2026-09-30 为了"开箱即用的包"）。

    为什么要这么写: 原来默认值写死成 `D:\\Documents_D\\jianpu-db`（本机专用），换台机器/做便携包必炸。
    查找顺序:
      1. `JIANPU_DB`（显式指定，最高优先）；
      2. `<包根>/corpus/jianpu-db`  —— 便携包的布局（首次运行脚本 clone 到这里）；
      3. `../jianpu-db`            —— 本机开发布局（站点仓库与语料仓库平级）；
      4. 当前工作目录下的 `jianpu-db`。
    都找不到也**不报错退出**（只读检索仍可用），写入类接口会明确告诉你 DB 指到哪里去了。
    """
    cands = [os.environ.get("JIANPU_DB", "").strip(),
             os.path.join(ROOT, "corpus", "jianpu-db"),
             os.path.join(os.path.dirname(ROOT), "jianpu-db"),
             os.path.join(os.getcwd(), "jianpu-db")]
    for c in cands:
        if c and os.path.isdir(c):
            return os.path.abspath(c)
    return os.path.abspath(cands[1])           # 都不在 -> 指向包内布局(首次运行脚本会建它)


PORT = int(os.environ.get("JIANPU_PORT") or (sys.argv[1] if len(sys.argv) > 1 else 8770))
HOST = os.environ.get("JIANPU_HOST", "127.0.0.1")
DB = _find_db()
FEEDBACK = os.path.join(DB, "feedback")
TOKEN = os.environ.get("JPSUBMIT_TOKEN", "")
sys.stdout.reconfigure(encoding="utf-8")

# 原图: 只开放这几个根(可被 JIANPU_IMAGES 覆盖, 冒号分隔) —— 与 tools/build_image_index.py 同一套默认
IMG_PREFIX = "/img/"
IMG_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
IMG_CACHE = "public, max-age=604800"           # 扫描件不会变; 换了图就改名/换批次目录

MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
        ".jsonl": "application/x-ndjson; charset=utf-8", ".gz": "application/gzip",
        ".png": "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
        # 爬虫/平台校验文件（2026-09-30 加 robots.txt/sitemap.xml 时补）—— 不给类型会变
        # `application/octet-stream`，本地自检当场就报红了（线上由托管方按扩展名给，是对的）。
        ".txt": "text/plain; charset=utf-8", ".xml": "application/xml; charset=utf-8"}


def image_roots():
    """`/img/<工作区相对路径>` 允许落在哪些根目录下(按顺序找, 只留**存在**的)。

    ⚠ 2026-09-28 修: 原来只有 `<工作区>/images` 与 `<工作区>/images-prep`。本机这两条都不存在
    —— 图库在 `<工作区>/jianpu2/images-prep`(3.8 万文件)。后果: 安全校验 `full 必须落在
    IMG_ROOTS 之一里面` 会把**每一个** `/img/...` 请求都判成越界 -> 线上每首的「每谱一页 / 原图」
    全部 404。把 `jianpu2/` 也作为一个候选根(这样 `images-prep/<批次>/...` 能正确拼成
    `<工作区>/jianpu2/images-prep/<批次>/...`), 并且不再把不存在的目录放进白名单。
    """
    env = os.environ.get("JIANPU_IMAGES", "").strip()
    if env:
        return [os.path.abspath(p) for p in env.split(os.pathsep) if p.strip()]
    cands = [os.path.join(WS, "images"), os.path.join(WS, "images-prep"),
             os.path.join(WS, "jianpu2"),          # <- 图库在 jianpu2 下面的情形(本机)
             os.path.join(WS, "jianpu2", "images-prep")]
    existed = [c for c in cands if os.path.isdir(c)]
    return existed or cands[:2]


IMG_ROOTS = image_roots()

# 简谱 token 口径**只有一份**: 复用 skill 目录里的 jptok.py。
# 这里以前自带一份"前缀时值"正则 —— 投稿里若写 `6c.`/`5s`/`3q` 这种**后缀**时值,
# 那些 token 会被判成"不是音符"而整段丢掉(与 2026-09-23 索引丢音事故同一个坑)。
# ⚠ 除了本机开发布局(../jianpu2/skills/…), 也把 `<包>/app` 加进来 —— 便携包里 jptok.py
#   就放在 app/ 旁边（见 tools/make_server_bundle.py），这样"开箱即用"的那份口径与本机一致。
for _p in (os.path.join(ROOT, "..", "jianpu2", "skills", "jianpu-melody-lookup"),
           os.path.join(ROOT, "app")):
    if os.path.isdir(_p):
        sys.path.insert(0, _p)
try:
    import jptok
except Exception:                       # 兜底正则: 与 jptok.py 同口径(时值+变音前后都认)
    jptok = None
NOTE = re.compile(r"^[cqsdh]*[,']*[#b♯♭]?[1-7x0][,']*[#b♯♭]?[cqsdh]*[.]*[\[\]]?$")
_is_note = (lambda t: jptok.is_note(t)) if jptok else (lambda t: bool(NOTE.match(t)))

# 收录页 URL 的口径**只有一份**: jianpu-db/linkurl.py(纯函数, 不读 tags.json)。
# 它同时负责"搜索页一律拒收"与"写进曲谱文件"—— 前端粘贴保存和 CLI 都走它。
sys.path.insert(0, DB)
try:
    import linkurl
except Exception:                       # DB 路径不对 -> 宁可拒绝写, 也不写未校验的 URL
    linkurl = None
HERE_WEB = ROOT
# 只读检索的口径**只有一份**(app/search_api.py), 两份后端都 import 它。先把自己的目录放进
# sys.path —— 这个文件也会被 tools/check_submit.py 用 spec_from_file_location 加载, 那时
# 脚本目录不在 sys.path 里, 直接 import 会 ImportError。
sys.path.insert(0, os.path.join(ROOT, "app"))
import search_api  # noqa: E402

REFRESH_LOG = os.path.join(HERE_WEB, "data", "refresh.log")
REFRESH_LOCK = os.path.join(HERE_WEB, "data", ".refresh.lock")
REFRESH_PENDING = os.path.join(HERE_WEB, "data", ".refresh.pending")


def start_refresh():
    """后台重建索引: parse_scores(重建 data.jsonl/bars) + build_web_data(前端索引)。
    返回 (是否已排上, 说明)。若正有一轮在跑: 放一个 pending 标记让它在跑完后**再来一轮**,
    免得这一次保存的链接被漏掉(并发缺口)。

    跑哪一份: **有 bash 就跑 refresh.sh**(它是最早的实现), 没有就用 Python 版 `tools/refresh.py`
    —— 实测 Windows 开发机上没有 bash, 于是"保存成功但 index 不重建"(返回 refresh:false +
    FileNotFoundError), 卡片要等下一次流水线才变。两份步骤一致(同锁、同 pending、同三轮)。
    """
    sh = os.path.join(HERE_WEB, "tools", "refresh.sh")
    pyf = os.path.join(HERE_WEB, "tools", "refresh.py")
    if shutil.which("bash") and os.path.isfile(sh):
        cmd = ["bash", sh]
    elif os.path.isfile(pyf):
        cmd = [sys.executable, "-u", pyf]
    elif os.path.isfile(sh):
        cmd = ["bash", sh]
    else:
        return False, "没有 tools/refresh.sh 也没有 tools/refresh.py"
    if os.path.exists(REFRESH_LOCK):
        try:
            if time.time() - os.path.getmtime(REFRESH_LOCK) < 600:
                with io.open(REFRESH_PENDING, "w", encoding="utf-8") as g:
                    g.write(str(os.getpid()))
                return True, "已排队(等当前重建跑完自动再来一轮)"
        except OSError:
            pass
    try:
        with io.open(REFRESH_LOCK, "w", encoding="utf-8") as g:
            g.write(str(os.getpid()))
        with io.open(REFRESH_LOG, "ab") as g:
            subprocess.Popen(cmd, cwd=HERE_WEB, stdout=g, stderr=subprocess.STDOUT,
                             start_new_session=True)
        return True, "已开始重建(约 2 分钟)"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def save_link(payload, note="", contact=""):
    """人工补收录页: 校验(搜索页拒收) -> 留档 -> 写进 scores/<file>.txt -> git commit -> 后台重建。"""
    if linkurl is None:
        return 500, {"ok": False, "err": f"找不到 linkurl.py —— JIANPU_DB={DB} 对吗?"}
    raw = _as_text(payload.get("file"))
    base = os.path.basename(raw)
    if not base or base != raw or not base.endswith(".txt"):
        return 400, {"ok": False, "err": "文件名不合法"}
    path = os.path.join(DB, "scores", base)
    if not os.path.isfile(path):
        return 400, {"ok": False, "err": "语料里没有这份曲谱: " + base}
    # 留档(与其他投稿一致: 永远先存, 不怕后面失败)
    os.makedirs(FEEDBACK, exist_ok=True)
    rid = _unique_rid(time.strftime("%Y%m%d-%H%M%S") + "-link-" + _safe(base[:-4], 20))
    with io.open(os.path.join(FEEDBACK, rid + ".json"), "w", encoding="utf-8", newline="\n") as g:
        g.write(json.dumps({"id": rid, "kind": "link", "file": base,
                            "url": _as_text(payload.get("url")), "note": note, "contact": contact,
                            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "ip": payload.get("_ip", "")}, ensure_ascii=False, indent=2))
    try:
        added, already = linkurl.add_to_score_file(path, _as_text(payload.get("url")))
    except ValueError as e:
        return 400, {"ok": False, "err": str(e)}
    except Exception as e:
        return 500, {"ok": False, "err": f"{type(e).__name__}: {e}"}
    if not added:
        return 200, {"ok": True, "file": base, "state": "已存在", "committed": False,
                     "refresh": False, "url": already}
    rel = os.path.join("scores", base)
    rc, out = _git_commit(f"link: {base} —— 人工补收录页({len(added)} 条)", [rel])
    refresh, why = start_refresh()
    return 200, {"ok": True, "file": base, "state": "已写入", "committed": rc == 0,
                 "git": out[-300:] if rc else "", "url": added,
                 "refresh": refresh, "refresh_msg": why}


def save_tags(payload, note="", contact=""):
    """人工补标签: 校验 -> 留档 -> 写进 scores/<file>.txt 的 usertag -> git commit -> 后台重建。

    与 save_link 同一个套路; 写入实现在 jianpu-db/linkurl.py:add_usertag(唯一一份)。
    只有**分类标签**(`分类/…`)才顺带清掉 `todo=add tags` —— 只补了歌手的话那首仍然缺分类。
    """
    if linkurl is None:
        return 500, {"ok": False, "err": f"找不到 linkurl.py —— JIANPU_DB={DB} 对吗?"}
    rawf = _as_text(payload.get("file"))
    base = os.path.basename(rawf)
    if not base or base != rawf or not base.endswith(".txt"):
        return 400, {"ok": False, "err": "文件名不合法"}
    path = os.path.join(DB, "scores", base)
    if not os.path.isfile(path):
        return 400, {"ok": False, "err": "语料里没有这份曲谱: " + base}
    raw_tags = [x.strip() for x in re.split(r"[,，、;；]+", _as_text(payload.get("tags"))) if x.strip()]
    if not raw_tags:
        return 400, {"ok": False, "err": "标签是空的"}
    os.makedirs(FEEDBACK, exist_ok=True)
    rid = _unique_rid(time.strftime("%Y%m%d-%H%M%S") + "-tags-" + _safe(base[:-4], 20))
    with io.open(os.path.join(FEEDBACK, rid + ".json"), "w", encoding="utf-8", newline="\n") as g:
        g.write(json.dumps({"id": rid, "kind": "tags", "file": base, "tags": raw_tags,
                            "note": note, "contact": contact,
                            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "ip": payload.get("_ip", "")}, ensure_ascii=False, indent=2))
    added, existed = [], []
    for t in raw_tags:
        try:
            r = linkurl.add_usertag(path, t, clear_todo=t.startswith("分类/"))
        except ValueError as e:
            return 400, {"ok": False, "err": str(e), "added": added}
        except Exception as e:
            return 500, {"ok": False, "err": f"{type(e).__name__}: {e}", "added": added}
        (added if r == "added" else existed).append(t)
    if not added:
        return 200, {"ok": True, "file": base, "state": "已存在", "committed": False,
                     "refresh": False, "tags": existed}
    rel = os.path.join("scores", base)
    rc, out = _git_commit(f"tags: {base} —— 人工补标签({','.join(added)})", [rel])
    refresh, why = start_refresh()
    return 200, {"ok": True, "file": base, "state": "已写入", "committed": rc == 0,
                 "git": out[-300:] if rc else "", "tags": added,
                 "refresh": refresh, "refresh_msg": why}


def _editable_fields():
    """可改属性的**白名单** —— 从 `data/stats.json` 的 `fields` 读（那份是 jianpu-db/schema.py:FIELDS 的副本）。

    为什么不直接 `import schema`：schema.py 一 import 就读 cwd 下的 tags.json（见它文件头），
    网页服务不该被那个绊住。读不到就返回空 = **一个都不许改**（宁可拒写，也不放未校验的字段名进来）。
    """
    try:
        with io.open(os.path.join(HERE_WEB, "data", "stats.json"), encoding="utf-8") as f:
            st = json.load(f)
        return {k: v for k, v in (st.get("fields") or {}).items() if v.get("editable")}
    except Exception:
        return {}


_MBID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def save_attr(payload, note="", contact=""):
    """人工补某个**可改属性**（schema 说了算）: 校验 -> 留档 -> 写进曲谱 -> git commit -> 后台重建。

    与 save_tags 同一套路。写入仍然是 `jianpu-db/linkurl.py` 那一份实现：
      * kind=list -> add_list_item（追加，逗号分隔、大小写不敏感去重；usertag 走 add_usertag 以便清 todo）
      * kind=text -> add_field（替换；MBID 允许贴 MusicBrainz 页面 URL，这里只取里面的 uuid）
    前端那颗 ＋ 是照 schema 画的，但**能不能改写在这儿说了算** —— 只读属性在这里必须被拒。
    """
    if linkurl is None:
        return 500, {"ok": False, "err": f"找不到 linkurl.py —— JIANPU_DB={DB} 对吗?"}
    rawf = _as_text(payload.get("file"))
    base = os.path.basename(rawf)
    if not base or base != rawf or not base.endswith(".txt"):
        return 400, {"ok": False, "err": "文件名不合法"}
    path = os.path.join(DB, "scores", base)
    if not os.path.isfile(path):
        return 400, {"ok": False, "err": "语料里没有这份曲谱: " + base}
    key = _as_text(payload.get("attr")).strip()
    spec = _editable_fields().get(key)
    if not spec:
        return 400, {"ok": False, "err": f"这个属性不能改（不在 schema 的可改白名单里）: {key or '(空)'}"}
    value = _as_text(payload.get("value")).strip()
    if not value:
        return 400, {"ok": False, "err": "值是空的"}
    if len(value) > 300:
        return 400, {"ok": False, "err": "值太长（>300 字）"}
    attr = (spec.get("attr") or key)
    kind = spec.get("kind") or "text"
    if key == "mbid":
        m = _MBID_RE.search(value)
        if not m:
            return 400, {"ok": False, "err": "MBID 要是一个 UUID（或 MusicBrainz work 页面 URL）"}
        value = m.group(0).lower()
    os.makedirs(FEEDBACK, exist_ok=True)
    rid = _unique_rid(time.strftime("%Y%m%d-%H%M%S") + "-attr-" + _safe(base[:-4], 20))
    with io.open(os.path.join(FEEDBACK, rid + ".json"), "w", encoding="utf-8", newline="\n") as g:
        g.write(json.dumps({"id": rid, "kind": "attr", "file": base, "attr": key, "value": value,
                            "note": note, "contact": contact,
                            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "ip": payload.get("_ip", "")}, ensure_ascii=False, indent=2))
    try:
        if kind == "list" and attr == "usertag":
            state = linkurl.add_usertag(path, value, clear_todo=value.startswith("分类/"))
        elif kind == "list":
            state = linkurl.add_list_item(path, attr, value)
        else:
            state = linkurl.add_field(path, attr, value)
    except ValueError as e:
        return 400, {"ok": False, "err": str(e)}
    except Exception as e:
        return 500, {"ok": False, "err": f"{type(e).__name__}: {e}"}
    if state == "exists":
        return 200, {"ok": True, "file": base, "attr": key, "value": value, "state": "已存在",
                     "committed": False, "refresh": False}
    rel = os.path.join("scores", base)
    rc, out = _git_commit(f"{key}: {base} —— 人工补 {attr}={value}", [rel])
    refresh, why = start_refresh()
    return 200, {"ok": True, "file": base, "attr": key, "value": value, "state": "已写入",
                 "committed": rc == 0, "git": out[-300:] if rc else "",
                 "refresh": refresh, "refresh_msg": why}


def _as_text(v):
    """客户端可能把字段送成 list(`{"tags":["民歌"]}`) -> 统一成字符串, 免得 re/字符串方法炸掉。"""
    if isinstance(v, (list, tuple)):
        return ",".join(str(x) for x in v)
    return "" if v is None else str(v)


def _unique_rid(base):
    """留档 id 必须唯一: 同一秒内同类型同曲名的投稿会撞车, 否则前一份留档被后面的覆盖掉。"""
    rid, n = base, 1
    while os.path.exists(os.path.join(FEEDBACK, rid + ".json")):
        n += 1
        rid = f"{base}-{n}"
    return rid


def normalize_melody(score):
    """用户粘来的简谱数字 -> (token 列表, 给用户看的警告)。**投稿入库的唯一入口用它**。

    粘法五花八门, 归一化规则:
        `63731232`    -> `6 3 7 3 1 2 3 2`    (数字之间补空格)
        `6 3 7 3`     -> 不变                  ← 旧版只处理"整串没空格"的情况,
        `6q3s7q1c`    -> `6q 3s 7q 1c`        所以手打了空格的 `63731232 1765` 被整串当
        `1'2`         -> `1' 2`               非法 token **静默丢掉**(实测踩过)。
        `6-7`/`1 2|3` -> `6 - 7` / `1 2 | 3`  (- | ~ 两侧留空)
        `#4` `b7`     -> 不动                  (记号贴在数字前面, 是合法 token)
    警告: 认不出的字符(汉字/英文/8/9…)会被丢掉, 必须明说, 否则用户以为整串都收下了。
    """
    raw = (score or "").strip()
    if not raw:
        return [], ""
    src = re.sub(r"(?<=[0-9cqsdh'])(?=[0-9#b♯♭])", " ", raw)
    src = re.sub(r"\s*([|~])\s*", r" \1 ", src)
    src = re.sub(r"\s*-\s*", " - ", src)
    toks = [t for t in src.split() if _is_note(t) or t in ("-", "|", "~")]
    # 允许集 = jptok 的 token 字符集: 0-7 x cqsdh , ' # b ♯ ♭ . [ ] - | ~ 与空白。
    junk = re.sub(r"[\s0-7xcqsdh,.'#b♯♭\-|~\[\]]", "", raw)
    shown = junk[:12] + ("…" if len(junk) > 12 else "")
    if len(toks) < 5:
        warn = (f"只认出 {len(toks)} 个音符，没建成曲谱（认的写法：1-7/x/0、时值后缀 cqsdh、"
                f"#b♯♭、八度撇、- | ~）")
        if junk:
            warn += f"；另有 {len(junk)} 个字符没认出来：{shown}"
        return toks, warn + " —— 原文已留档，作者能看到。"
    if junk:
        return toks, (f"曲谱已入库，但有 {len(junk)} 个字符没认出来丢掉了：{shown}"
                      f"（认的写法：1-7/x/0、时值后缀 cqsdh、#b♯♭、八度撇、- | ~）")
    return toks, ""


GIT_LOCK = threading.Lock()     # 投稿可能并发到达; git 的 index/index.lock 不是并发安全的


def _git_commit(msg, rels, author="reader-submit"):
    """**只**提交指定文件。

    两个坑都在这:
      1) 新文件必须显式 `git add`, 否则 `git commit -- <path>` 会报
         "路径规格 ... 未匹配任何 Git 已知文件" 而**静默不提交**;
      2) 提交时限定路径, 工作区别的脏文件(例如刚跑完 parse_scores 的重生成)不会被卷进这次提交。
    """
    with GIT_LOCK:
        _git("add", "--", *rels)
        rc, out = _git("-c", f"user.name={author}", "-c", "user.email=submit@local",
                       "commit", "-q", "-m", msg, "--", *rels)
        if rc != 0 and "index.lock" in out:        # 万一是外部进程正在动 git -> 等一下重试一次
            time.sleep(1.5)
            rc, out = _git("-c", f"user.name={author}", "-c", "user.email=submit@local",
                           "commit", "-q", "-m", msg, "--", *rels)
    return rc, out


def _safe(s, n=80):
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(s or "")).strip()
    return (s[:n] or "untitled")


def _git(*args):
    """在 jianpu-db 里跑 git; 返回 (rc, out)。"""
    try:
        r = subprocess.run(["git"] + list(args), cwd=DB, capture_output=True, text=True, timeout=60)
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()[:400]
    except Exception as e:
        return 1, f"{type(e).__name__}: {e}"


def handle_submit(payload):
    """把投稿落盘并提交。返回 (http_status, dict)。"""
    kind = _as_text(payload.get("kind")).strip() or "new"
    title = _as_text(payload.get("title")).strip()
    score = _as_text(payload.get("score")).strip()
    note = _as_text(payload.get("note")).strip()
    contact = _as_text(payload.get("contact")).strip()
    # ①a kind=link / kind=tags / kind=attr: 身份是**文件**而不是曲名 -> 不走"请填曲名"与建谱流程
    if kind == "link":
        return save_link(payload, note, contact)
    if kind == "tags":
        return save_tags(payload, note, contact)
    if kind == "attr":
        return save_attr(payload, note, contact)
    if not title:
        return 400, {"ok": False, "err": "请填曲名"}
    ts = time.strftime("%Y%m%d-%H%M%S")
    rid = _unique_rid(f"{ts}-{_safe(title, 24)}")

    # ① 投稿原文留档(永远先存, 不怕后面失败)
    os.makedirs(FEEDBACK, exist_ok=True)
    # kind=fix(纠错)时前端带上"要改的是哪一份" -> 留档, 作者一眼知道改哪份
    # (只接受 scores/ 下的裸文件名, 防目录穿越)
    target = _as_text(payload.get("file")).strip()
    if target and (os.path.basename(target) != target or not target.endswith(".txt")):
        target = ""
    rec = {"id": rid, "kind": kind, "title": title, "score": score, "note": note,
           "contact": contact, "target": target,
           "time": time.strftime("%Y-%m-%d %H:%M:%S"),
           "ip": payload.get("_ip", "")}
    with io.open(os.path.join(FEEDBACK, rid + ".json"), "w", encoding="utf-8", newline="\n") as g:
        g.write(json.dumps(rec, ensure_ascii=False, indent=2))

    # ② 如果给了简谱数字 -> 直接生成一份曲谱进 scores/(反馈即入库)
    wrote_score = ""
    toks, score_warn = normalize_melody(score)
    if len(toks) >= 5:
        name = _safe(title, 60)
        p = os.path.join(DB, "scores", name + ".txt")
        if os.path.exists(p):
            p = os.path.join(DB, "scores", f"{name}_{ts[-6:]}.txt")
        _lines = [
            f"%{os.path.basename(p)}",
            f"title={title}",
            "tag=", "usertag=", "tagroute=",
            "transcriber=读者投稿", "status=ocr",
            "todo=add tags",   # 语料惯例: 还没标签的谱子标这个, 补标签流程会挑出来
            f"% 投稿 {rid}" + (f" 联系 {contact}" if contact else ""),
            f"% 备注 {note}" if note else "% 备注 (无)",
        ]
        if target:
            _lines.append(f"% 纠错目标 {target}")
        _lines += [
            "source=user-submit",
            "%--", "4/4", "subtitle=score",
            " ".join(toks), "%END",
        ]
        body = "\n".join(_lines) + "\n"
        with io.open(p, "w", encoding="utf-8", newline="\n") as g:
            g.write(body)
        wrote_score = os.path.basename(p)

    # ③ 本地 git 提交(不 push; push 由你/定时任务决定)
    msg = f"submission: {kind} - {title}"
    if wrote_score:
        msg += f" (+scores/{wrote_score})"
    rels = [os.path.join("feedback", rid + ".json")]
    if wrote_score:
        rels.append(os.path.join("scores", wrote_score))
    rc, out = _git_commit(msg, rels)
    committed = (rc == 0)
    # 新谱进了 scores/ 只是"入库", 还要重建索引才能被搜到 -> 与补链接/补标签一致, 后台重建。
    # 没有数字的纯反馈(fix/meta 只留档)不动语料, 不必重建。
    refresh, why = (start_refresh() if wrote_score else (False, "未写入曲谱, 无需重建"))
    return 200, {"ok": True, "id": rid, "score_file": wrote_score, "score_warn": score_warn,
                 "committed": committed, "git": out[-200:] if not committed else "",
                 "refresh": refresh, "refresh_msg": why}


def resolve(path):
    if path in ("/", ""):
        return os.path.join(ROOT, "static", "index.html")
    # 「每谱一页」: /s/<id> 是**前端路由**, 服务端只把同一个 index.html 发出去, 由前端按 id 渲。
    # (这样刷新/分享一个谱页地址永远有效; 纯静态部署时对应 404.html 兜底或改用 #/s/<id>。)
    if path == "/s" or path.startswith("/s/"):
        return os.path.join(ROOT, "static", "index.html")
    rel = path.lstrip("/")
    # 根上的"给爬虫/给平台校验"的文件（`robots.txt` / `sitemap.xml` / `BingSiteAuth.xml` …）：
    # 先看**仓库根**有没有这个文件。不这么写就会被下面那句当成 `static/robots.txt` -> 本地 404，
    # 而线上(GitHub Pages / Cloudflare 都把根目录当资源根)明明是好的 —— 本地与线上不一致最难查。
    if rel and "/" not in rel:
        cand = os.path.normpath(os.path.join(ROOT, rel))
        if cand.startswith(ROOT) and os.path.isfile(cand):
            return cand
    if not rel.startswith(("static/", "data/")):
        rel = os.path.join("static", rel)
    full = os.path.normpath(os.path.join(ROOT, rel))
    return full if full.startswith(ROOT) else None


# ══════════════════════════════════════════════════════════════════════════════
# 「每谱一页」的分享/收录元数据（2026-09-30 加正式域名 jianpu-db.org 时做的）
#
# 前端是 SPA，**爬虫不跑 JS** —— 不做这一步，1.1 万个 `/s/<id>` 在爬虫眼里是同一份 HTML。
# 数据来自构建期生成的 `data/og.json`（`id -> [曲名, 歌手, 音符数]`，见 tools/build_web_data.py）。
# 与边缘那份（`worker/index.js` 的 `injectSongMeta`）**逻辑逐条对应**，两边都要改，
# 免得又出现"本地与线上不一致"（这个坑今晚踩过两次：/robots.txt 404、MIME 类型错）。
# 纪律同边缘: **任何异常都退回原始 HTML**；注入用函数式替换（曲名里有 `\1` 也不怕）。
# ══════════════════════════════════════════════════════════════════════════════
_OG = None


def og_meta(tune_id):
    """`data/og.json` 里这一首的 [曲名, 歌手, 音符数]；取不到返回 None。"""
    global _OG
    if _OG is None:
        try:
            with io.open(os.path.join(ROOT, "data", "og.json"), encoding="utf-8") as f:
                _OG = json.load(f)
        except Exception:
            _OG = {}
    return _OG.get(tune_id) if tune_id else None


def _esc(s):
    return (str(s if s is not None else "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;"))


def inject_song_meta(html, meta, origin, tune_id):
    """把这一首的标题/描述写进 head。meta 为空/异常时原样返回。"""
    if not html or not meta:
        return html
    title = meta[0] if len(meta) > 0 else ""
    if not title:
        return html
    artist = meta[1] if len(meta) > 1 else ""
    notes = meta[2] if len(meta) > 2 else ""
    label = "%s（%s）" % (title, artist) if artist else title
    page_title = "%s · 简谱 | jianpu-db" % label
    desc = "%s的简谱：%s 个音符。哼开头几个音就能把这首歌的谱找出来 —— jianpu-db。" % (label, notes)
    url = "%s/s/%s" % (origin.rstrip("/"), quote(str(tune_id), safe="")) if (origin and tune_id) else ""
    # ⚠ **必须用函数式替换**（`re.sub(pattern, callable, ...)`），不能把用户文字拼进替换串：
    #   曲名里只要有个 `\1` 就会变成"反向引用" -> `re.PatternError: invalid group reference`
    #   （隔离测试 `tools/check_og_meta.py` 当场抓到；JS 侧一开始就用的是函数，所以没这个坑）。
    #   顺带这也省掉了"转义要照顾 `\g<1>` 语法"的额外心智负担。
    def rep2(val):
        return lambda mm: mm.group(1) + val + mm.group(2)

    pairs = [
        (r"(<title>)[^<]*(</title>)", _esc(page_title)),
        (r'(<meta name="description" content=")[^"]*(")', _esc(desc)),
        (r'(<meta property="og:title" content=")[^"]*(")', _esc(page_title)),
        (r'(<meta property="og:description" content=")[^"]*(")', _esc(desc)),
        (r'(<meta name="twitter:title" content=")[^"]*(")', _esc(page_title)),
        (r'(<meta name="twitter:description" content=")[^"]*(")', _esc(desc)),
    ]
    if url:
        pairs.append((r'(<link rel="canonical" href=")[^"]*(")', _esc(url)))
        pairs.append((r'(<meta property="og:url" content=")[^"]*(")', _esc(url)))
    out = html
    for pat, val in pairs:
        out = re.sub(pat, rep2(val), out, count=1)
    return out


def resolve_img(rel):
    """`/img/…` 后的路径 -> 磁盘路径; 任何越界/可疑一律 None。
    路径是**相对工作区根**写的(如 `images-prep/qupu123-crawl/曲名__qupu123-1/001.jpg`),
    因为索引就是这么存的(见 build_image_index.py)。三道闸: 百分号解码后逐段检查(不许 .. / 空段 /
    反斜杠 / NUL) -> 扩展名白名单 -> 规范化后必须落在 IMG_ROOTS 之一里面。
    """
    rel = unquote(rel or "").replace("\\", "/").lstrip("/")
    if not rel or "\x00" in rel:
        return None
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    if os.path.splitext(rel)[1].lower() not in IMG_EXT:
        return None
    # ⚠ 2026-09-28 修: 原来只拿 `WS` 当基准拼一次(`join(WS, rel)`), 再检查它是否落在某个
    #   白名单根里。当图库在 `<工作区>/jianpu2/images-prep`(本机)时, 拼出来的是
    #   `<工作区>/images-prep/...` —— 既不是真文件、也不在任何根里, 于是**每个 /img/ 都 404**。
    #   改成**逐个根试拼**, 并且每个根各自做一次包含性检查(越界仍然一律拒绝)。
    #
    # ⚠ 同一次还补了个真漏洞: 包含性检查原来比的是**逻辑路径**, 而 `isfile()` 会**跟随符号链接**
    #   —— 实测(本机): 在 images-prep 下放一个指向根外 .png 的 `_probe_link.jpg`,
    #   请求 `/img/images-prep/.../_probe_link.jpg` 得到 **HTTP 200 + 根外文件内容**。
    #   典型的 ../ 穿越(含 %2e%2e / 反斜杠 / 盘符 / UNC / NUL / ADS 共 12 种)本来就都被挡住,
    #   漏的是这一路。修法: 用 **realpath 解析后再比** —— 链接指到根外就拒绝。
    #   (图库当前没有符号链接, 所以不是"正在被利用"; 但如果要把 /img/ 通过隧道暴露出去,
    #    这种深度防御不该留口子。)
    for root in IMG_ROOTS:
        r = os.path.realpath(os.path.abspath(root))
        try:
            full = os.path.realpath(os.path.normpath(os.path.join(r, rel)))
            if os.path.commonpath([full, r]) == r and os.path.isfile(full):
                return full
        except ValueError:                     # 不同盘符/无法比较
            continue
    return None


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "jianpu-web"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s  %s\n" % (self.address_string(), fmt % args))

    def _json(self, code, obj, headers=None):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (headers or {}).items():       # 限流的 Retry-After 走这里(默认没有额外头)
            self.send_header(k, str(v))
        self.end_headers()
        self.wfile.write(b)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,X-Token")
        self.send_header("Access-Control-Allow-Methods", "POST,GET,OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/api/submit":
            return self._json(404, {"ok": False, "err": "no such endpoint"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0 or n > 200_000:
            return self._json(400, {"ok": False, "err": "body 太大或为空"})
        raw = self.rfile.read(n)
        if TOKEN and self.headers.get("X-Token") != TOKEN:
            return self._json(403, {"ok": False, "err": "需要 X-Token"})
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            return self._json(400, {"ok": False, "err": "不是合法 JSON"})
        payload["_ip"] = self.client_address[0]
        try:
            code, out = handle_submit(payload)
        except Exception as e:
            return self._json(500, {"ok": False, "err": f"{type(e).__name__}: {e}"})
        return self._json(code, out)

    def _send_file(self, full, ctype, cache="no-cache"):
        try:
            size = os.path.getsize(full)
        except OSError:
            size = -1
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", cache)
        if size >= 0:
            self.send_header("Content-Length", str(size))
        self.end_headers()
        with open(full, "rb") as f:            # 分块发: 扫描件个别有几 MB, 别整个读进内存
            while True:
                b = f.read(65536)
                if not b:
                    break
                self.wfile.write(b)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/health":
            n = len(os.listdir(FEEDBACK)) if os.path.isdir(FEEDBACK) else 0
            return self._json(200, {"ok": True, "repo": DB, "feedback_count": n,
                                    "token_required": bool(TOKEN),
                                    "images": [os.path.basename(r) for r in IMG_ROOTS]})
        if path == "/api/search":
            # 只读检索: 参数校验/缓存/限流全在 app/search_api.py(与 FastAPI 版同一份口径),
            # 这里只把查询串与客户端 IP 递进去。**不动**下面的静态兜底与写路径。
            code, out, headers = search_api.handle(
                self.path.split("?", 1)[1] if "?" in self.path else "",
                self.client_address[0] if self.client_address else "")
            return self._json(code, out, headers)
        if path == "/ws/2" or path.startswith("/ws/2/"):
            # MusicBrainz 风格只读命名空间(2026-10-05): 口径同样**只有一份**在 app/search_api.py,
            # 这里只搬上 HTTP(FastAPI 版那条路由也走同一个 ws2_handle)。
            code, out, headers = search_api.ws2_handle(
                path,
                self.path.split("?", 1)[1] if "?" in self.path else "",
                self.client_address[0] if self.client_address else "",
                self.headers.get("Accept") or "",
                search_api.base_url_from_host(self.headers.get("Host") or ""))
            return self._json(code, out, headers)
        if path.startswith(IMG_PREFIX):
            full = resolve_img(path[len(IMG_PREFIX):])
            if not full:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            return self._send_file(full, MIME.get(os.path.splitext(full)[1].lower(),
                                                  "application/octet-stream"), IMG_CACHE)
        full = resolve(path)
        if not full or not os.path.isfile(full):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        ext = os.path.splitext(full)[1].lower()
        # 「每谱一页」`/s/<id>`: 服务端发同一份 index.html，但**把这一首的标题写进 head**
        # （爬虫不跑 JS；与边缘 Worker 的行为对齐，见 worker/index.js 的 injectSongMeta）。
        if ext == ".html" and (path == "/s" or path.startswith("/s/")):
            try:
                with io.open(full, encoding="utf-8") as f:
                    html = f.read()
                tid = unquote(path[2:].lstrip("/").rstrip("/"))
                origin = "http://" + (self.headers.get("Host") or "")
                html = inject_song_meta(html, og_meta(tid), origin, tid)
                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            except Exception:
                pass                      # 见纪律: 出任何问题都退回原始文件
        self._send_file(full, MIME.get(ext, "application/octet-stream"))


if __name__ == "__main__":
    print(f"简谱旋律查歌 + 零登录投稿 -> http://{HOST}:{PORT}/")
    print(f"投稿落库: {DB}  (feedback/ 留档; 给了数字就直接进 scores/)")
    print(f"每谱一页: /s/<id>  ·  原图: {IMG_PREFIX}<工作区相对路径> <- {IMG_ROOTS}")
    print(f"token 保护: {'开' if TOKEN else '关(仅本机/内网使用)'}")
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()

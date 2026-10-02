import { buildIndex, search, ensureGrams } from "./search.6cbacf4d.js";
import { parseQuery, isPitch, show } from "./jptok.5bf64237.js";
var REPO = "Francium-223/jianpu-db";
var ROOT_URL = new URL("../", import.meta.url);
var APP_PATH = ROOT_URL.pathname.replace(/\/+$/, "/");
function appUrl(rel) {
  return new URL(rel, ROOT_URL).toString();
}
function appPath(rel) {
  return new URL(rel, ROOT_URL).pathname;
}
function tunePath(id) {
  return appPath("s/" + encodeURIComponent(id));
}
function closestFrom(t2, sel) {
  const el = t2;
  return el && typeof el.closest === "function" ? el.closest(sel) : null;
}
function $(id) {
  return document.getElementById(id);
}
var IDX = null;
function loadCorpus() {
  if (typeof DecompressionStream === "undefined") {
    return fetch(appUrl("data/songs.jsonl")).then(function(r) {
      return r.text();
    });
  }
  return fetch(appUrl("data/songs.jsonl.gz")).then(function(r) {
    if (r.headers && r.headers.get && /gzip/i.test(r.headers.get("content-encoding") || "")) return r.text();
    var body = typeof r.clone === "function" ? r.clone().body : r.body;
    return new Response(body.pipeThrough(new DecompressionStream("gzip"))).text().catch(function() {
      return typeof r.text === "function" ? r.text() : "";
    });
  });
}
function esc(s) {
  return String(s).replace(/[&<>"]/g, function(c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
  });
}
function sourceUrl(src) {
  var host = String(src || "").split("-")[0];
  var m = {
    qupu123: "https://www.qupu123.com/",
    jianpucn: "http://www.jianpu.cn/",
    jianpujia: "https://www.jianpujia.com/"
  };
  return m[host] || "";
}
var SITE_LABELS = [
  [/music\.163\.com/, "网易云音乐"],
  [/y\.qq\.com/, "QQ音乐"],
  [/bilibili\.com/, "B站"],
  [/youtube\.com|youtu\.be/, "YouTube"],
  [/musicbrainz\.org/, "MusicBrainz"],
  [/jianpu\.cn/, "歌谱简谱网"],
  [/jianpujia\.com/, "简谱之家"],
  [/qupu123\.com/, "中国曲谱网"],
  [/qinyipu\.com/, "琴艺谱"]
];
function siteLabel(u) {
  for (var i2 = 0; i2 < SITE_LABELS.length; i2++) if (SITE_LABELS[i2][0].test(u)) return SITE_LABELS[i2][1];
  try {
    return new URL(u).hostname.replace(/^www\./, "");
  } catch (e) {
    return "链接";
  }
}
var PLATFORMS = [
  ["网易云音乐", /music\.163\.com/, "https://music.163.com/song?id=…", "https://music.163.com/#/search/m/?s={q}&type=1"],
  ["QQ音乐", /y\.qq\.com/, "https://y.qq.com/n/ryqq/songDetail/…", "https://y.qq.com/n/ryqq/search?w={q}"],
  ["B站", /bilibili\.com/, "https://www.bilibili.com/video/…", "https://search.bilibili.com/all?keyword={q}"],
  ["YouTube", /youtube\.com|youtu\.be/, "https://www.youtube.com/watch?v=…", "https://www.youtube.com/results?search_query={q}"],
  ["MusicBrainz", /musicbrainz\.org/, "https://musicbrainz.org/work/…", "https://musicbrainz.org/search?query={q}&type=work"]
];
function loadPlatforms(st) {
  if (!st || !st.platforms || !st.platforms.length) return;
  try {
    PLATFORMS = st.platforms.map(function(p) {
      return [p.name, new RegExp(p.host || ".", "i"), p.exact || "https://…", p.search || ""];
    });
  } catch (e) {
  }
}
var ALROW_N = 0;
var FIELDS = {
  file: { label: { zh: "文件", en: "File" }, kind: "readonly" },
  group: { label: { zh: "曲名", en: "Title" }, kind: "readonly" },
  artist: { label: { zh: "歌手", en: "Artist" }, kind: "list", editable: true, hint: "邓丽君（多个用逗号）" },
  status: { label: { zh: "状态", en: "Status" }, kind: "readonly" },
  n: { label: { zh: "音符", en: "Notes" }, kind: "readonly" },
  bars: { label: { zh: "小节", en: "Bars" }, kind: "readonly" },
  source: { label: { zh: "出处", en: "Source" }, kind: "readonly" },
  transcriber: { label: { zh: "转写", en: "Transcriber" }, kind: "readonly" },
  tags: { label: { zh: "标签", en: "Tags" }, kind: "readonly" },
  usertags: { label: { zh: "人标", en: "Human tags" }, kind: "list", editable: true, hint: "分类/儿歌, 民歌" },
  alias: { label: { zh: "别名", en: "Alias" }, kind: "list", editable: true, hint: "另一个曲名" },
  mbid: { label: { zh: "MBID", en: "MBID" }, kind: "text", editable: true, hint: "MusicBrainz work 的 UUID" }
};
function loadFields(st) {
  if (!st || !st.fields) return;
  var out = {};
  Object.keys(st.fields).forEach(function(k) {
    var f = st.fields[k] || {};
    out[k] = {
      label: f.label || {},
      kind: f.kind || "readonly",
      editable: !!f.editable,
      hint: f.hint || "",
      note: f.note || "",
      row: f.row !== false
    };
  });
  if (Object.keys(out).length) FIELDS = out;
}
var LANG = (function() {
  var l = typeof navigator !== "undefined" && navigator.language || "zh";
  return String(l).slice(0, 2).toLowerCase();
})();
function fieldLabel(f) {
  var lb = f && f.label || {}, keys = Object.keys(lb);
  return lb[LANG] || lb.zh || (keys.length ? lb[keys[0]] : "");
}
function exactSources(r) {
  var out = [], seen = {};
  function push(u, kind) {
    if (!u || seen[u]) return;
    seen[u] = 1;
    out.push([siteLabel(u), u, kind]);
  }
  if (r.mbid) push("https://musicbrainz.org/work/" + encodeURIComponent(r.mbid), "MBID");
  if (r.srcurl) push(r.srcurl, "原谱站（已核对）");
  (r.links || []).forEach(function(u) {
    push(u, "收录页");
  });
  return out;
}
function collectedUrls(r) {
  return exactSources(r).map(function(p) {
    return p[1];
  });
}
function exactLinks(r, ctx) {
  var out = exactSources(r);
  var urls = collectedUrls(r);
  var f = r.file && r.file[0] || "";
  var rid = "alrow" + ++ALROW_N;
  var html = out.map(function(p) {
    return '<a class="exact" href="' + p[1] + '" target="_blank" rel="noopener" title="' + esc(p[2]) + '">' + esc(p[0]) + " ↗</a>";
  }).join("");
  var qq = encodeURIComponent(r.title || r.group || "");
  PLATFORMS.forEach(function(p) {
    if (urls.some(function(u) {
      return p[1].test(u);
    })) return;
    var href = (p[3] || "").replace("{q}", qq);
    html += '<a class="exact pending" href="' + href + '" target="_blank" rel="noopener" title="还没收录 —— 点开去 ' + esc(p[0]) + ' 搜这首歌">' + esc(p[0]) + "</a>" + (f ? '<button type="button" class="plus" data-row="' + rid + '" data-ph="' + esc(p[2]) + '" data-plat="' + esc(p[0]) + '" title="补 ' + esc(p[0]) + ' 的确切页面">＋</button>' : "");
  });
  if (f) {
    html += '<button type="button" class="plus" data-row="' + rid + '" data-ph="https://…" data-plat="其它站" title="补其它站的确切页面">＋</button><span class="alrow" id="' + rid + '" hidden><input class="al-url" placeholder="https://…" spellcheck="false" /><button class="al-go" data-file="' + esc(f) + '" data-re="' + esc(ctx || "") + '">保存</button><span class="al-msg"></span></span>';
  }
  return html;
}
function addTagForm(r) {
  var f = r.file && r.file[0] || "";
  if (!f) return "";
  return '<details class="addlink"><summary>＋ 补标签</summary><p class="hint">多个用逗号；分类写「分类/儿歌」，歌手直接写名字。</p><input class="al-url at-tags" list="taglist" placeholder="分类/儿歌, 邓丽君" spellcheck="false" /><button class="al-go-tags" data-file="' + esc(f) + '">保存</button><span class="al-msg"></span></details>';
}
function fillTagList() {
  var dl = $("taglist");
  if (!dl || !IDX) return;
  var set = {};
  for (var i2 = 0; i2 < IDX.songs.length; i2++) {
    (IDX.songs[i2].tags || []).forEach(function(t2) {
      set[t2] = 1;
    });
    (IDX.songs[i2].usertags || []).forEach(function(t2) {
      set[t2] = 1;
    });
  }
  dl.innerHTML = Object.keys(set).sort().map(function(t2) {
    return '<option value="' + esc(t2) + '"></option>';
  }).join("");
}
function issueUrl(text) {
  return "https://github.com/" + REPO + "/issues/new?title=" + encodeURIComponent("[缺谱] " + text) + "&body=" + encodeURIComponent(
    "想加的曲子:" + text + "\n\n(可选) 原谱链接或图片:\n\n(可选) 这段旋律的简谱数字:\n\n---\n由简谱旋律查歌前端自动填写\n"
  );
}
var TXT = {
  tied: { zh: "并列：另有 {n} 首同分", en: "{n} more song(s) tie at this cost" },
  tiedTip: {
    zh: "这句不是唯一命中 —— 后面并列的那几首要一起看, 别把第一条当铁证",
    en: "not a unique match — check the tied results too"
  },
  onlyOne: { zh: "独证：本曲仅此一版", en: "single version of this song" },
  onlyOneTip: {
    zh: "这首歌在库里只有这一个版本, 而且是**机器转写**（没有第二个版本可交叉核对）",
    en: "only one version in the corpus, and it is machine-transcribed"
  }
};
function t(key, vars) {
  var e = TXT[key] || {};
  var s = e[LANG] || e.zh || "";
  return String(s).replace(/\{(\w+)\}/g, function(m, k) {
    return vars && vars[k] != null ? String(vars[k]) : m;
  });
}
function cautionChips(r) {
  var out = "";
  if (r.cost === 0 && (r.groupsAtBest || 1) > 1) {
    var n = r.groupsAtBest - 1;
    out += '<span class="warn" title="' + esc(t("tiedTip")) + '">' + esc(t("tied", { n })) + "</span>";
  }
  if ((r.versions || 0) === 1 && r.status === "ocr") {
    out += '<span class="warn" title="' + esc(t("onlyOneTip")) + '">' + esc(t("onlyOne")) + "</span>";
  }
  return out;
}
function renderScore(raw, at, qlen, bars) {
  if (!raw) return "";
  var toks = raw.split(" ");
  var barSet = {};
  (bars || []).forEach(function(b) {
    barSet[b] = 1;
  });
  var mark = at !== null && at !== void 0 && at >= 0;
  var startTok = -1, endTok = -1;
  if (mark) {
    var noteIdx = -1;
    for (var i2 = 0; i2 < toks.length; i2++) {
      if (isPitch(toks[i2])) {
        noteIdx++;
        if (noteIdx === at) startTok = i2;
        if (noteIdx === at + qlen - 1) endTok = i2;
      }
    }
    if (startTok < 0) return esc(raw);
    if (endTok < 0) endTok = toks.length - 1;
  }
  var ni = -1, html = [];
  for (var j = 0; j < toks.length; j++) {
    var isNote = isPitch(toks[j]);
    if (isNote) ni++;
    if (isNote && barSet[ni] && !(mark && j === startTok)) html.push('<span class="bar">|</span> ');
    if (mark && j === startTok) html.push("<mark>");
    if (mark && j === endTok + 1) html.push("</mark>");
    html.push(esc(toks[j]));
    if (j < toks.length - 1) html.push(" ");
  }
  if (mark && endTok + 1 >= toks.length) html.push("</mark>");
  return html.join("");
}
function run(e) {
  if (e) e.preventDefault();
  if (!IDX) return;
  var segs = [];
  var parts = $("q").value.split(/[;；|、+，,]+/);
  for (var i2 = 0; i2 < parts.length; i2++) {
    var s = parseQuery(parts[i2]);
    if (s.length >= 5) segs.push(s);
  }
  if (!segs.length) {
    $("status").className = "status err";
    $("status").textContent = "至少 5 个音（1–7，可带 # 或 b）。";
    return;
  }
  $("status").className = "status";
  $("status").textContent = "查询中…";
  $("go").disabled = true;
  setTimeout(function() {
    var t0 = performance.now();
    var res = search(IDX, segs, { top: 10 });
    render(segs, res, Math.round(performance.now() - t0));
    $("go").disabled = false;
  }, 20);
}
function siteUrl(src) {
  var host = String(src || "").split("-")[0];
  var m = {
    qupu123: "https://www.qupu123.com/",
    jianpucn: "http://www.jianpu.cn/",
    jianpujia: "https://www.jianpujia.com/"
  };
  return m[host] || "";
}
var FIELD_VALUE = {
  file: function(r, h) {
    return r.file && r.file.length ? h.esc(h.list(r.file)) : "—";
  },
  group: function(r, h) {
    return h.esc(r.group);
  },
  artist: function(r, h) {
    return r.artist && r.artist.length ? h.esc(h.list(r.artist)) : "—";
  },
  status: function(r, h) {
    return h.esc(r.status || "?") + (r.status === "ok" ? "（人工校对过）" : r.status === "ocr" ? "（图片机器转写）" : "");
  },
  n: function(r) {
    return r.n + " 个";
  },
  bars: function(r) {
    return (r.bars || []).length + " 小节 · " + (r.bpb || 4) + " 拍/小节";
  },
  source: function(r, h) {
    var src = r.source || "";
    if (!src) return "—";
    var u = r.srcurl || sourceUrl(src);
    return u ? '<a href="' + u + '" target="_blank" rel="noopener">' + h.esc(src) + "</a>" : h.esc(src);
  },
  transcriber: function(r, h) {
    return r.transcriber && r.transcriber.length ? h.esc(h.list(r.transcriber)) : "—";
  },
  // 转写置信度(0~1): 老谱没这个字段时 build_web_data 给 0.5(中性), 这里区分"真的 0.5"与"没有"
  confidence: function(r, h) {
    if (r.conf == null) return "—";
    var pct = Math.round(r.conf * 100);
    var tag = r.conf >= 0.95 ? "高" : r.conf >= 0.85 ? "中" : "低";
    return h.esc(pct + "%（" + tag + "）");
  },
  // 「最低那 10% 的分位」: 平均看着还行、个别音很虚时靠它发现(0.95 均值 + 0.42 p10 = 有虚音)
  conf_p10: function(r, h) {
    if (r.confP10 == null) return "—";
    return h.esc(Math.round(r.confP10 * 100) + "%");
  },
  tags: function(r, h) {
    return r.tags && r.tags.length ? h.esc(h.list(r.tags)) : "—";
  },
  usertags: function(r, h) {
    return r.usertags && r.usertags.length ? h.esc(h.list(r.usertags)) : "—";
  },
  alias: function(r, h) {
    return r.alias && r.alias.length ? h.esc(h.list(r.alias)) : "—";
  },
  mbid: function(r, h) {
    return r.mbid ? '<a href="https://musicbrainz.org/work/' + encodeURIComponent(r.mbid) + '" target="_blank" rel="noopener"><code>' + h.esc(r.mbid) + "</code></a>" : "—";
  }
};
function attrPlus(r, key) {
  var f = FIELDS[key], file = r.file && r.file[0] || "";
  if (!f || !f.editable || !file) return "";
  var rid = "attr" + ++ALROW_N;
  return '<button type="button" class="plus attr-plus" data-row="' + rid + '" data-ph="' + esc(f.hint || "") + '" data-file="' + esc(file) + '" data-attr="' + esc(key) + '" title="补 ' + esc(fieldLabel(f)) + '">＋</button><span class="alrow" id="' + rid + '" hidden><input class="al-url attr-val" placeholder="' + esc(f.hint || "") + '" spellcheck="false" /><button class="al-go-attr" data-file="' + esc(file) + '" data-attr="' + esc(key) + '">保存</button><span class="al-msg"></span></span>';
}
function metaRows(r) {
  function list(x) {
    return (x || []).join("、");
  }
  var h = { esc, list };
  var rows = [];
  Object.keys(FIELDS).forEach(function(key) {
    var f = FIELDS[key];
    if (f.row === false) return;
    var render2 = FIELD_VALUE[key];
    var val = render2 ? render2(r, h) : "—";
    var name = fieldLabel(f);
    var note = f.note ? ' title="' + esc(f.note) + '"' : "";
    rows.push(["<span" + note + ">" + esc(name) + "</span>", val + attrPlus(r, key)]);
  });
  return '<table class="meta"><tbody>' + rows.map(function(x) {
    return "<tr><th>" + x[0] + "</th><td>" + x[1] + "</td></tr>";
  }).join("") + "</tbody></table>";
}
var CURRENT_TUNE = "";
function tuneIdFromLocation() {
  var p = location.pathname || "";
  if (APP_PATH !== "/" && p.indexOf(APP_PATH) === 0) p = p.slice(APP_PATH.length - 1);
  var m = /^\/?s\/(.+)$/.exec(p);
  if (!m) m = /^#\/?s\/(.+)$/.exec(location.hash || "");
  if (!m) return "";
  try {
    return decodeURIComponent(m[1]);
  } catch (e) {
    return m[1];
  }
}
function setMode(tune) {
  var h = $("home"), t2 = $("tune");
  if (h) h.hidden = !!tune;
  if (t2) t2.hidden = !tune;
  if (document.body && document.body.classList) document.body.classList.toggle("tune-mode", !!tune);
}
function showHome(q) {
  setMode(false);
  CURRENT_TUNE = "";
  document.title = "jianpu-db | 通过简谱旋律查歌";
  if (q) {
    $("q").value = q;
    run({ preventDefault: function() {
    } });
  } else if ($("q")) {
    $("q").focus();
  }
}
function showTune(id) {
  setMode(true);
  CURRENT_TUNE = id;
  var t2 = $("tune");
  if (!t2) return;
  var row = IDX && IDX.byId ? IDX.byId.get(id) : null;
  if (!row) {
    t2.innerHTML = '<p class="crumb"><a class="tune" href="' + esc(APP_PATH) + '">← 回检索</a></p><h1 class="tune-h1">没有这一页</h1><p class="hint">地址里的编号 <code>' + esc(id) + "</code> 不在语料里（id 就是 source，如 <code>jianpucn-150657</code>）。</p>";
    document.title = "没有这一页 — jianpu-db";
    return;
  }
  t2.innerHTML = tuneHtml(row);
  document.title = (row.group || row.title || id) + " — jianpu-db";
}
function tuneHtml(r) {
  var list = function(x) {
    return (x || []).join("、");
  };
  var sub = [
    r.artist && r.artist.length ? "歌手 " + esc(list(r.artist)) : "",
    r.n + " 音符",
    (r.bars || []).length + " 小节 · " + (r.bpb || 4) + " 拍/小节",
    esc(r.status || "?") + (r.status === "ok" ? "（人工校对过）" : r.status === "ocr" ? "（图片机器转写）" : ""),
    r.source ? "出处 " + esc(r.source) : ""
  ].filter(Boolean).join(" · ");
  var motif = r.p && r.p.length >= 8 ? '<p class="hint"><a class="tune" href="' + esc(appPath("") + "?q=" + r.p.slice(0, 8)) + '" title="把这 8 个音送进旋律检索(查重、找同曲异名)">用开头的 ' + r.p.slice(0, 8) + " 去检索</a></p>" : "";
  return '<p class="crumb"><a class="tune" href="' + esc(APP_PATH) + '">← 回检索</a><span class="dim">' + esc(r.id || "") + '</span></p><h1 class="tune-h1">' + esc(r.group || r.title || "(无题)") + '</h1><p class="tune-sub">' + sub + "</p><h2>元数据</h2>" + metaRows(r) + '<div class="links"><span class="lab">收录页</span> ' + exactLinks(r, "tune") + addTagForm(r) + '<a class="add" href="' + issueUrl(r.group) + '" target="_blank" rel="noopener" title="这首有问题 / 想补充资料 → 一键提 issue">＋ 反馈/补充</a></div>' + motif + "<h2>原谱原文</h2>" + // **verbatim**: 曲谱文件正文原样(含节头/KeepLength/换行), 既不展开也不注入小节线 ——
  // 用户口径 2026-09-24("用用户写的文件一字不差")。老数据没有 src 时退回 raw。
  (r.src || r.raw ? '<pre class="sheet">' + esc(r.src || r.raw) + "</pre>" + (r.src ? "" : '<p class="hint">（旧索引：这里是展开过的原文。）</p>') : '<p class="hint">没有原文。</p>') + '<footer><a class="tune" href="' + esc(APP_PATH) + '">← 回检索页</a></footer>';
}
function navigate(href) {
  if (typeof history !== "undefined" && history.pushState) history.pushState(null, "", href);
  route();
  if (typeof window !== "undefined" && window.scrollTo) window.scrollTo(0, 0);
}
function route() {
  var id = tuneIdFromLocation();
  if (id) return showTune(id);
  var q = "";
  try {
    q = new URLSearchParams(location.search || "").get("q") || "";
  } catch (e) {
    q = "";
  }
  showHome(q);
}
function findSongByFile(f) {
  if (!IDX || !f) return null;
  for (var i2 = 0; i2 < IDX.songs.length; i2++) {
    if ((IDX.songs[i2].file || [])[0] === f) return IDX.songs[i2];
  }
  return null;
}
function addLocalLinks(file, urls) {
  var s = findSongByFile(file);
  if (!s) return;
  if (!s.links) s.links = [];
  (Array.isArray(urls) ? urls : [urls]).forEach(function(u) {
    if (u && s.links.indexOf(u) < 0) s.links.push(u);
  });
}
function addLocalTags(file, tags) {
  var s = findSongByFile(file);
  if (!s) return;
  ["tags", "usertags"].forEach(function(k) {
    if (!s[k]) s[k] = [];
    (Array.isArray(tags) ? tags : [tags]).forEach(function(t2) {
      if (t2 && s[k].indexOf(t2) < 0) s[k].push(t2);
    });
  });
}
function addLocalAttr(file, key, value) {
  var s = findSongByFile(file);
  if (!s) return;
  if (key === "mbid") {
    s.mbid = value;
    return;
  }
  if (key === "tags" || key === "usertags") {
    addLocalTags(file, [value]);
    return;
  }
  if (!s[key]) s[key] = [];
  if (s[key].indexOf(value) < 0) s[key].push(value);
}
function tuneTitle(r) {
  var name = esc(r.group || r.title || "");
  if (!r.id) return '<span class="title">' + name + "</span>";
  return '<a class="title tune" href="' + esc(tunePath(r.id)) + '" data-tune="' + esc(r.id) + '" title="打开这一首的页面">' + name + "</a>";
}
var QUERY_DIGITS = "";
function render(segs, res, ms) {
  QUERY_DIGITS = segs.map(function(sg) {
    return sg.map(function(n) {
      return n.d;
    }).join("");
  }).join(" ");
  var qshow = segs.map(show).join("  |  ");
  var nf = '<p class="nf">找不到？<a class="nf-add" href="#sform">欢迎补充。</a></p>';
  if (!res.length) {
    $("status").textContent = "没找到匹配（" + ms + " 毫秒）。片段至少 5 个音；换更长的片段试试。";
    $("out").innerHTML = nf + '<p class="hint">如果确认库里应该没有这首歌，也可以直接提 issue：<span class="links"><a class="add" href="' + issueUrl(qshow) + '" target="_blank" rel="noopener">＋ 建议收录</a></span></p>';
    return;
  }
  var tied = 0;
  for (var i2 = 0; i2 < res.length; i2++) if (res[i2].cost === res[0].cost) tied++;
  $("status").textContent = "查询 " + qshow + "：命中 " + res.length + " 组，用时 " + ms + " 毫秒" + (tied > 1 ? "；最优并列 " + tied + " 组（片段不够独特，加长或补第二段）" : "");
  var html = "";
  LAST = res[0].group;
  LASTFILE = res[0].file && res[0].file[0] || "";
  for (var k = 0; k < res.length; k++) {
    var r = res[k];
    html += '<div class="card' + (k === 0 ? " top" : "") + '"><div class="head">' + (k === 0 ? "" : '<span class="rank">#' + (k + 1) + "</span>") + tuneTitle(r) + '<span class="cost c' + Math.min(r.cost, 2) + '">代价 ' + r.cost + '</span><span class="badge">记号 ' + r.exact + "/" + r.qlen + '</span><span class="badge">' + r.n + ' 音符</span><span class="badge">' + esc(r.status || "?") + "</span>" + cautionChips(r) + "</div>" + metaRows(r) + '<div class="cmp"><span class="lab">库内该段' + (r.secCn ? "（" + esc(r.secCn) + "）" : "") + "</span> " + esc(show(r.libNotes)) + '　<span class="lab">你的输入</span> ' + esc(show(r.qNotes)) + '</div><div class="score">' + renderScore(r.raw, r.at, r.qlen, r.bars) + '</div><div class="links"><span class="lab">收录页</span> ' + exactLinks(r, "melody") + addTagForm(r) + '<a class="add" href="' + issueUrl(r.group) + '" target="_blank" rel="noopener" title="库里这首有问题 / 想补充资料 → 一键提 issue">＋ 反馈/补充</a></div></div>';
  }
  $("out").innerHTML = nf + html + '<p class="hint">「记号」是升降号一致的音数。<b>收录页</b>是这首歌在该站的具体页面。</p>';
}
$("form").addEventListener("submit", run);
function titleSearch(q) {
  var s = String(q || "").trim().toLowerCase();
  if (!s || !IDX) return [];
  var out = [];
  for (var i2 = 0; i2 < IDX.songs.length && out.length < 40; i2++) {
    var x = IDX.songs[i2];
    var hay = [x.title, x.group, (x.alias || []).join(" ")].join(" ").toLowerCase();
    if (hay.indexOf(s) >= 0) out.push(x);
  }
  return out;
}
function renderTitle(list, q) {
  if (!list.length) {
    $("tstatus").className = "status err";
    $("tstatus").textContent = "按曲名没找到「" + q + "」。换个更短的关键词，或用上面的旋律查歌。";
    $("tout").innerHTML = "";
    return;
  }
  $("tstatus").className = "status";
  $("tstatus").textContent = "按曲名「" + q + "」命中 " + list.length + " 首" + (list.length >= 40 ? "（只显示前 40 首，写更具体一点）" : "");
  var html = "";
  for (var k = 0; k < list.length; k++) {
    var x = list[k];
    html += '<div class="card"><div class="head">' + tuneTitle(x) + '<span class="badge">' + x.n + ' 音符</span><span class="badge">' + esc(x.status || "?") + "</span></div>" + metaRows(x) + '<div class="links"><span class="lab">收录页</span> ' + exactLinks(x, "title") + addTagForm(x) + "</div>" + (x.raw ? '<div class="score">' + esc(x.raw) + "</div>" : "") + "</div>";
  }
  $("tout").innerHTML = html;
}
function rerunTitle() {
  renderTitle(titleSearch($("tq").value), $("tq").value);
}
if ($("tform")) {
  $("tform").addEventListener("submit", function(ev) {
    ev.preventDefault();
    rerunTitle();
  });
}
document.addEventListener("click", function(ev) {
  var nfa = closestFrom(ev.target, "a.nf-add");
  if (nfa) {
    var kd = $("skind"), sc = $("sscore");
    if (kd) kd.value = "new";
    if (sc && !sc.value) sc.value = QUERY_DIGITS;
    var st = $("stitle");
    if (st) setTimeout(function() {
      st.focus();
    }, 0);
    return;
  }
  var tl = closestFrom(ev.target, "a.tune");
  if (tl) {
    ev.preventDefault();
    navigate(tl.getAttribute("href"));
    return;
  }
  var pb = closestFrom(ev.target, ".plus");
  if (pb) {
    var row = document.getElementById(pb.getAttribute("data-row"));
    if (row) {
      row.hidden = false;
      var pin = row.querySelector(".al-url");
      if (pin) {
        pin.placeholder = pb.getAttribute("data-ph") || "https://…";
        pin.focus();
      }
      var pm = row.querySelector(".al-msg");
      if (pm) {
        pm.textContent = "";
        pm.className = "al-msg";
      }
    }
    return;
  }
  var tb = closestFrom(ev.target, ".al-go-tags");
  if (tb) {
    var tbox = tb.closest(".addlink");
    var tin = tbox.querySelector(".at-tags");
    var tmsg = tbox.querySelector(".al-msg");
    var tags = (tin.value || "").trim();
    if (!tags) {
      tmsg.className = "al-msg err";
      tmsg.textContent = "先填标签";
      return;
    }
    if (READONLY) {
      readonlyInto(tmsg);
      return;
    }
    tb.disabled = true;
    tmsg.className = "al-msg";
    tmsg.textContent = "保存中…";
    fetch(API + "/api/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "tags", file: tb.getAttribute("data-file"), tags })
    }).then(function(r) {
      return r.json();
    }).then(function(j) {
      tb.disabled = false;
      if (j && j.ok) {
        tmsg.className = "al-msg ok";
        tmsg.textContent = "已写入 " + (j.tags || []).join("、") + "（" + j.state + "）" + (j.refresh ? "；" + (j.refresh_msg || "索引重建中") : "");
        tin.value = "";
        addLocalTags(tb.getAttribute("data-file"), j.tags);
      } else {
        tmsg.className = "al-msg err";
        tmsg.textContent = "失败：" + (j && j.err || "未知错误");
      }
    }).catch(function(e) {
      tb.disabled = false;
      tmsg.className = "al-msg err";
      tmsg.textContent = "失败：" + e.message;
    });
    return;
  }
  var ab = closestFrom(ev.target, ".al-go-attr");
  if (ab) {
    var abox = ab.closest(".alrow");
    var ain = abox.querySelector(".attr-val");
    var amsg = abox.querySelector(".al-msg");
    var aval = (ain.value || "").trim();
    if (!aval) {
      amsg.className = "al-msg err";
      amsg.textContent = "先填内容";
      return;
    }
    if (READONLY) {
      readonlyInto(amsg);
      return;
    }
    ab.disabled = true;
    amsg.className = "al-msg";
    amsg.textContent = "保存中…";
    fetch(API + "/api/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        kind: "attr",
        file: ab.getAttribute("data-file"),
        attr: ab.getAttribute("data-attr"),
        value: aval
      })
    }).then(function(r) {
      return r.json();
    }).then(function(j) {
      ab.disabled = false;
      if (j && j.ok) {
        amsg.className = "al-msg ok";
        amsg.textContent = "已写入 " + j.attr + "=" + (j.value || aval) + "（" + j.state + "）" + (j.refresh ? "；" + (j.refresh_msg || "索引重建中") : "");
        ain.value = "";
        addLocalAttr(ab.getAttribute("data-file"), ab.getAttribute("data-attr"), j.value || aval);
        var are = ab.getAttribute("data-re") || "";
        setTimeout(function() {
          if (are === "title") rerunTitle();
          else if (are === "tune") showTune(CURRENT_TUNE);
          else run({ preventDefault: function() {
          } });
        }, 300);
      } else {
        amsg.className = "al-msg err";
        amsg.textContent = "失败：" + (j && j.err || "未知错误");
      }
    }).catch(function(e) {
      ab.disabled = false;
      amsg.className = "al-msg err";
      amsg.textContent = "失败：" + e.message;
    });
    return;
  }
  var b = closestFrom(ev.target, ".al-go");
  if (!b) return;
  var box = b.closest(".addlink");
  var inp = box.querySelector(".al-url");
  var msg = box.querySelector(".al-msg");
  var url = (inp.value || "").trim();
  if (!url) {
    msg.className = "al-msg err";
    msg.textContent = "先粘贴网址";
    return;
  }
  if (READONLY) {
    readonlyInto(msg);
    return;
  }
  b.disabled = true;
  msg.className = "al-msg";
  msg.textContent = "保存中…";
  fetch(API + "/api/submit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind: "link", file: b.getAttribute("data-file"), url })
  }).then(function(r) {
    return r.json();
  }).then(function(j) {
    b.disabled = false;
    if (j && j.ok) {
      msg.className = "al-msg ok";
      msg.textContent = "已写入 " + j.file + (j.committed ? "（已 git commit）" : "（未提交：" + (j.git || "未知原因") + "）") + (j.refresh ? "；" + (j.refresh_msg || "索引重建中，约 2 分钟后刷新可见") : "");
      inp.value = "";
      addLocalLinks(b.getAttribute("data-file"), j.url);
      var re = b.getAttribute("data-re") || "";
      setTimeout(function() {
        if (re === "title") rerunTitle();
        else if (re === "tune") showTune(CURRENT_TUNE);
        else run({ preventDefault: function() {
        } });
      }, 400);
    } else {
      msg.className = "al-msg err";
      msg.textContent = "失败：" + (j && j.err || "未知错误");
    }
  }).catch(function(e) {
    b.disabled = false;
    msg.className = "al-msg err";
    msg.textContent = "失败：" + e.message;
  });
});
var exs = document.getElementsByClassName("ex");
for (var i = 0; i < exs.length; i++) {
  exs[i].addEventListener("click", function(ev) {
    ev.preventDefault();
    $("q").value = this.getAttribute("data-q");
    run();
  });
}
var API = window.JIANPU_API || location.protocol + "//" + location.host;
var READONLY = !!window.JIANPU_READONLY;
var MIRROR = "https://jianpu-db.org/";
function readonlyInto(el) {
  el.className = el.classList && el.classList.contains("al-msg") ? "al-msg err" : "status err";
  el.innerHTML = '只读镜像：投稿请到 <a href="' + MIRROR + '" target="_blank" rel="noopener">jianpu-db.org</a>。';
}
var LAST = "", LASTFILE = "";
function submit() {
  var t2 = $("stitle").value.trim();
  if (!t2) {
    $("sstatus").className = "status err";
    $("sstatus").textContent = "请填曲名。";
    return;
  }
  if (READONLY) {
    readonlyInto($("sstatus"));
    return;
  }
  var kind = $("skind").value;
  var body = {
    kind,
    title: t2,
    score: $("sscore").value.trim(),
    note: $("snote").value.trim(),
    contact: $("scontact").value.trim(),
    // 纠错/元数据: 带上刚才查的那一份, 作者不用猜你说的是哪份
    file: kind === "fix" || kind === "meta" ? LASTFILE : ""
  };
  $("sstatus").className = "status";
  $("sstatus").textContent = "提交中…";
  $("sgo").disabled = true;
  fetch(API + "/api/submit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  }).then(function(r) {
    return r.json().then(function(j) {
      return { s: r.status, j };
    });
  }).then(function(x) {
    $("sgo").disabled = false;
    if (x.j && x.j.ok) {
      $("sstatus").textContent = "已收到，编号 " + x.j.id + (x.j.score_file ? "（已生成曲谱 " + x.j.score_file + " 入库" + (x.j.refresh ? "，索引重建中，约 2 分钟后可搜到" : "") + "）" : "（只留了投稿，没有数字）") + (x.j.score_warn ? "　⚠ " + x.j.score_warn : "");
      $("stitle").value = "";
      $("sscore").value = "";
      $("snote").value = "";
    } else {
      $("sstatus").className = "status err";
      $("sstatus").textContent = "提交失败：" + (x.j && x.j.err || "HTTP " + x.s) + '。也可以提 Issue：<a href="' + issueUrl(t2) + '" target="_blank" rel="noopener">预填 Issue</a>';
      $("sstatus").innerHTML = $("sstatus").textContent;
    }
  }).catch(function(e) {
    $("sgo").disabled = false;
    $("sstatus").className = "status err";
    $("sstatus").innerHTML = "连不上投稿服务（" + e.message + '）。也可以提 <a href="' + issueUrl(t2) + '" target="_blank" rel="noopener">Issue</a>';
  });
}
$("sform").addEventListener("submit", function(e) {
  e.preventDefault();
  submit();
});
if (READONLY) {
  $("sform").insertAdjacentHTML(
    "beforebegin",
    '<p class="lead" id="ro-note">⚠ 只读镜像：查歌、谱页可用；投稿请到 <a href="' + MIRROR + '" target="_blank" rel="noopener">jianpu-db.org</a>。</p>'
  );
}
$("sfill").addEventListener("click", function() {
  if (LAST) {
    $("stitle").value = LAST;
  } else {
    $("sstatus").textContent = "先在上面查一次，再点这个按钮。";
  }
});
loadCorpus().then(function(txt) {
  IDX = buildIndex(txt);
  setTimeout(function() {
    try {
      if (IDX) ensureGrams(IDX, 4);
    } catch (e) {
    }
  }, 800);
  return fetch(appUrl("data/stats.json")).then(function(r) {
    return r.json();
  }).then(function(st) {
    loadPlatforms(st);
    loadFields(st);
    return st;
  });
}).then(function(st) {
  $("stats").textContent = "语料 " + st.songs + " 首（" + st.groups + " 组），" + st.notes.toLocaleString() + " 个音符。";
  $("status").textContent = "就绪，共 " + IDX.count + " 首。";
  fillTagList();
  fetch(appUrl("api/gh")).then(function(r) {
    return r.json();
  }).then(function(d) {
    var el = $("ghcount");
    if (el && d && typeof d.stars === "number" && d.stars > 0) {
      el.textContent = String(d.stars);
      el.hidden = false;
    }
  })["catch"](function() {
  });
  if (typeof window !== "undefined" && window.addEventListener) window.addEventListener("popstate", route);
  if (tuneIdFromLocation()) route();
  else showHome("");
}).catch(function(err) {
  $("status").className = "status err";
  $("status").textContent = "初始化失败：" + err.message;
});
export {
  TXT,
  exactLinks,
  renderScore
};

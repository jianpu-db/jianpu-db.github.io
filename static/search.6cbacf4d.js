const BAD = /吉他|钢琴|双谱|器乐|非洲|尤克里里|古筝|琵琶|二胡|笛|萨克斯|总谱|合唱/;
const TAIL = /(?:[-_（(]?\s*(?:简谱|歌曲类|歌谱|五线谱|正谱|完整版|弹唱|吉他谱|钢琴谱)\s*[)）]?)+$/;
function popKey(g) {
  let k = g;
  for (let i = 0; i < 3; i++) {
    const k2 = k.replace(TAIL, "");
    if (k2 === k || !k2) break;
    k = k2;
  }
  return k;
}
function buildIndex(text) {
  const songs = [];
  for (const line of text.split("\n")) {
    if (!line) continue;
    let r;
    try {
      r = JSON.parse(line);
    } catch {
      continue;
    }
    if (!r.p) continue;
    songs.push({
      id: r.id ?? "",
      // 「每谱一页」的地址 /s/<id>(见 build_web_data.py: tune_id)
      title: r.t ?? "",
      group: r.g ?? "",
      source: r.s ?? "",
      status: r.st ?? "",
      n: r.n ?? 0,
      p: r.p,
      a: r.a ?? "",
      o: r.o ?? "",
      raw: r.raw ?? "",
      trunc: !!r.trunc,
      src: r.src ?? "",
      // 原谱原文 verbatim(每谱一页用; 与 raw 的"展开版"不同)
      bars: r.bars ?? [],
      bpb: r.bpb ?? 4,
      sc: r.sc ?? "",
      // 段落表("起始下标:段落名,…"): 段落权重用(副歌优先于前奏)
      file: r.file ?? [],
      tags: r.tags ?? [],
      usertags: r.usertags ?? [],
      alias: r.alias ?? [],
      artist: r.artist ?? [],
      transcriber: r.transcriber ?? [],
      hot: r.hot ?? 0,
      // 知名度代理(该曲歌手/标签在语料里的谱数, 见 build_web_data.py)
      // ⚠ 这几个以前漏在这里 -> 索引里明明有, 结果卡上永远看不到(同一类"白名单丢字段"):
      //   mbid  = MusicBrainz 录音页; links = 人工补的收录页; srcurl = 原谱站核对过的确切页
      mbid: r.mbid ?? "",
      links: r.links ?? [],
      srcurl: r.srcurl ?? "",
      // ⚠ 2026-10-01（TypeScript 化时）补上: 转写置信度。以前漏了它，导致
      //   ① 并列裁决里"置信度高优先"永远拿到 0.5（等于没这条）；② 卡片上永远不显示置信度。
      conf: r.conf ?? null,
      confP10: r.confP10 ?? null,
      i: 0
      // 先占位，建完 songs 再统一填（见下）
    });
  }
  songs.forEach((s, i) => {
    s.i = i;
  });
  const groups = /* @__PURE__ */ new Map();
  for (const s of songs) {
    if (!groups.has(s.group)) groups.set(s.group, []);
    groups.get(s.group).push(s);
  }
  const hot = /* @__PURE__ */ new Map();
  for (const [g, v] of groups) {
    let m = 0;
    for (const s of v) if ((s.hot || 0) > m) m = s.hot || 0;
    hot.set(g, m);
  }
  const pop = /* @__PURE__ */ new Map();
  for (const [g, v] of groups) {
    const k = popKey(g);
    pop.set(k, (pop.get(k) || 0) + v.length);
  }
  const byId = /* @__PURE__ */ new Map();
  for (const s of songs) if (s.id && !byId.has(s.id)) byId.set(s.id, s);
  return { songs, groups, pop, hot, byId, count: songs.length, groupCount: groups.size };
}
function ensureGrams(idx, k = 4) {
  if (idx.grams) return idx.grams;
  const map = /* @__PURE__ */ new Map();
  for (let i = 0; i < idx.songs.length; i++) {
    const p = idx.songs[i].p;
    for (let j = 0; j + k <= p.length; j++) {
      const g = p.slice(j, j + k);
      const a = map.get(g);
      if (a) a.push(i);
      else map.set(g, [i]);
    }
  }
  idx.grams = { k, map };
  return idx.grams;
}
function candidateMask(idx, q) {
  const g = idx.grams;
  if (!g) return null;
  const qstr = q.map((x) => x.d).join("");
  if (qstr.length < g.k) return null;
  let best = null;
  let bestLen = Infinity;
  for (let j = 0; j + g.k <= qstr.length; j++) {
    const arr = g.map.get(qstr.slice(j, j + g.k));
    if (!arr) return { mask: new Uint8Array(idx.songs.length), count: 0 };
    if (arr.length < bestLen) {
      bestLen = arr.length;
      best = arr;
    }
  }
  if (!best) return null;
  const mask = new Uint8Array(idx.songs.length);
  let count = 0;
  for (const i of best) {
    if (mask[i]) continue;
    mask[i] = 1;
    if (idx.songs[i].p.includes(qstr)) count++;
    else mask[i] = 0;
  }
  return { mask, count };
}
function arraysOf(s) {
  if (s._p) return s._p;
  const n = s.p.length;
  const P = new Uint8Array(n), A = new Int8Array(n), O = new Int8Array(n);
  for (let i = 0; i < n; i++) {
    P[i] = s.p.charCodeAt(i) - 48;
    A[i] = s.a.charCodeAt(i) === 49 ? 1 : s.a.charCodeAt(i) === 50 ? -1 : 0;
    O[i] = 0;
  }
  if (s.o) {
    const parts = s.o.split(",");
    for (let i = 0; i < n && i < parts.length; i++) O[i] = parseInt(parts[i], 10) || 0;
  }
  s._p = { P, A, O };
  return s._p;
}
const SEC_W = {
  chorus: 1.6,
  refrain: 1.6,
  verse: 1.25,
  "pre-chorus": 1.1,
  bridge: 1.1,
  interlude: 1.1,
  score: 1,
  intro: 0.8,
  outro: 0.8,
  layer: 0.8,
  "crazy-piano": 0.8
};
const SEC_CN = {
  chorus: "副歌",
  refrain: "副歌",
  verse: "主歌",
  "pre-chorus": "前副歌",
  bridge: "桥段",
  interlude: "间奏",
  score: "整曲",
  intro: "前奏",
  outro: "尾奏",
  layer: "过渡",
  "crazy-piano": "发狂钢琴",
  "maybe-rap": "说唱?"
};
function secWeightOf(name) {
  if (!name) return 1;
  let w = 1;
  for (const x of String(name).split(",")) {
    const k = x.trim().toLowerCase();
    if (k && SEC_W[k] !== void 0 && SEC_W[k] > w) w = SEC_W[k];
  }
  return w;
}
function secLabelOf(name) {
  if (!name) return "";
  return String(name).split(",").map((x) => SEC_CN[x.trim().toLowerCase()] || x.trim()).filter(Boolean).join("/");
}
function secOf(s) {
  if (s._sec !== void 0) return s._sec;
  s._sec = [];
  if (s.sc) {
    for (const part of s.sc.split(",")) {
      const i = part.indexOf(":");
      if (i < 0) continue;
      s._sec.push([parseInt(part.slice(0, i), 10) || 0, part.slice(i + 1)]);
    }
  }
  return s._sec;
}
function secNameAt(s, i, n) {
  const sec = secOf(s);
  if (!sec.length) return "";
  let best = "", bw = -1;
  for (let k = 0; k < sec.length; k++) {
    const start = sec[k][0];
    const end = k + 1 < sec.length ? sec[k + 1][0] : 1e9;
    if (start < i + n && end > i) {
      const w = secWeightOf(sec[k][1]);
      if (w > bw) {
        bw = w;
        best = sec[k][1];
      }
    }
  }
  return best;
}
function cost(q, cd, ca) {
  if (q.d !== cd) return 4;
  if (q.acc === ca) return 0;
  if (q.acc === 0) return 1;
  if (ca === 0) return 2;
  return 3;
}
function bestWindow(s, q, limitCost) {
  const n = q.length;
  const { P, A } = arraysOf(s);
  if (P.length < n) return null;
  let best = null;
  const cap = limitCost ?? Number.POSITIVE_INFINITY;
  for (let i = 0; i + n <= P.length; i++) {
    let c = 0;
    let aborted = false;
    for (let k = 0; k < n; k++) {
      c += cost(q[k], P[i + k], A[i + k]);
      if (best && c > best.cost || c > cap) {
        aborted = true;
        break;
      }
    }
    if (aborted) continue;
    if (!best || c < best.cost) {
      best = { cost: c, at: i, sec: secNameAt(s, i, n) };
    } else if (c === best.cost) {
      const nm = secNameAt(s, i, n);
      if (secWeightOf(nm) > secWeightOf(best.sec)) best = { cost: c, at: i, sec: nm };
    }
  }
  return best;
}
function search(idx, segs, opt) {
  const o = opt ?? {};
  const top = o.top || 10;
  const collect = (allow) => {
    const res2 = [];
    for (const [group, members] of idx.groups) {
      let total = 0, exact = 0;
      const det = [];
      let ok = true;
      for (let si = 0; si < segs.length; si++) {
        const q = segs[si];
        const n = q.length;
        let best = null;
        const tbl = o.scan ? o.scan[si] : null;
        if (tbl) {
          let minCost = 4294967295;
          for (const s of members) {
            if (allow && !allow(si, s)) continue;
            const c = tbl.costs[s.i];
            if (c < minCost) minCost = c;
          }
          if (minCost === 4294967295) {
            ok = false;
            break;
          }
          for (const s of members) {
            if (allow && !allow(si, s)) continue;
            if (tbl.costs[s.i] !== minCost) continue;
            const w = bestWindow(s, q, minCost);
            if (!w) continue;
            if (!best || w.cost < best.cost || w.cost === best.cost && secWeightOf(w.sec) > secWeightOf(best.sec)) {
              best = { cost: w.cost, at: w.at, song: s, q, sec: w.sec };
            }
          }
        }
        if (!best && !tbl) {
          for (const s of members) {
            if (allow && !allow(si, s)) continue;
            const w = bestWindow(s, q);
            if (!w) continue;
            if (!best || w.cost < best.cost || w.cost === best.cost && secWeightOf(w.sec) > secWeightOf(best.sec)) {
              best = { cost: w.cost, at: w.at, song: s, q, sec: w.sec };
            }
          }
        }
        if (!best) {
          ok = false;
          break;
        }
        total += best.cost;
        for (let k = 0; k < n; k++) {
          const { A } = arraysOf(best.song);
          if (best.q[k].acc === A[best.at + k]) exact++;
        }
        det.push(best);
      }
      let secW = 1, sec = "";
      for (const d of det) {
        const w = secWeightOf(d.sec);
        if (w > secW) {
          secW = w;
          sec = d.sec;
        }
      }
      if (ok && det.length) res2.push({ group, total, exact, det, secW, sec });
    }
    return res2;
  };
  let res = null;
  if (idx.grams && !o.scan) {
    const masks = segs.map((q) => candidateMask(idx, q));
    if (masks.every((m) => m && m.count > 0)) {
      const fast = collect((si, s) => masks[si].mask[s.i] === 1);
      let zeros = 0;
      for (const r of fast) if (r.total === 0) zeros++;
      if (zeros >= top) res = fast;
    }
  }
  if (!res) res = collect();
  const okOf = (r) => r.det[0] && r.det[0].song.status === "ok" ? 0 : 1;
  const confOf = (r) => {
    const c = parseFloat(r.det[0] && r.det[0].song.conf || "");
    return isNaN(c) ? 0.5 : c;
  };
  const versOf = (r) => new Set((idx.groups.get(r.group) ?? []).map((s) => s.source || "")).size;
  const keyed = res.map((r) => ({
    r,
    ok: okOf(r),
    conf: confOf(r),
    vers: versOf(r),
    pop: idx.pop.get(popKey(r.group)) || 0,
    hot: idx.hot.get(r.group) || 0,
    bad: BAD.test(r.group) ? 1 : 0
  }));
  keyed.sort((a, b) => a.r.total - b.r.total || a.ok - b.ok || b.conf - a.conf || b.vers - a.vers || b.r.exact - a.r.exact || b.pop - a.pop || b.hot - a.hot || b.r.secW - a.r.secW || a.bad - b.bad || a.r.group.length - b.r.group.length || (a.r.group < b.r.group ? -1 : 1));
  res.length = 0;
  for (const k of keyed) res.push(k.r);
  const bestTotal = res.length ? res[0].total : 0;
  let groupsAtBest = 0;
  for (const r of res) if (r.total === bestTotal) groupsAtBest++;
  return res.slice(0, top).map((r) => {
    const h = r.det[0];
    const n = h.q.length;
    const arr = arraysOf(h.song);
    return {
      id: h.song.id || "",
      title: h.song.title,
      group: r.group,
      source: h.song.source,
      status: h.song.status,
      n: h.song.n,
      cost: r.total,
      exact: r.exact,
      qlen: n,
      at: h.at,
      raw: h.song.raw,
      trunc: h.song.trunc,
      bars: h.song.bars,
      bpb: h.song.bpb,
      src: h.song.src,
      file: h.song.file,
      tags: h.song.tags,
      usertags: h.song.usertags,
      alias: h.song.alias,
      artist: h.song.artist,
      transcriber: h.song.transcriber,
      mbid: h.song.mbid,
      links: h.song.links || [],
      srcurl: h.song.srcurl || "",
      hot: idx.hot.get(r.group) || 0,
      conf: h.song.conf == null ? null : h.song.conf,
      // 转写置信度(卡片要显示/并列要用)
      confP10: h.song.confP10 == null ? null : h.song.confP10,
      // **"这条命中到底有多硬"** —— 给前端提示用(2026-09-29):
      //   groupsAtBest = 代价并列(拿到同一个最好代价)的歌有几首; >1 说明"这句不是唯一命中";
      //   versions     = 这首歌在库里有几个版本; ==1 且 status=ocr 说明"没有第二个版本可交叉核对"。
      // 起因: 实测《你怎么说》那句 `33565653253` 是转写把"行尾 3- + 间奏括号"连读拼出来的假片段,
      //       而它当时是**代价 0 的唯一排前**(另一首同分的《神々》排在后面) —— 前端应该把这件事说出来。
      groupsAtBest,
      versions: (idx.groups.get(r.group) || []).length,
      sec: r.sec || "",
      secW: r.secW || 1,
      secCn: secLabelOf(r.sec),
      libNotes: Array.from({ length: n }, (_, k) => ({ d: arr.P[h.at + k], acc: arr.A[h.at + k] })),
      qNotes: h.q
    };
  });
}
export {
  SEC_CN,
  SEC_W,
  bestWindow,
  buildIndex,
  ensureGrams,
  popKey,
  search,
  secLabelOf,
  secWeightOf
};

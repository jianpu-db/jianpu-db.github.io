/* 旋律检索(浏览器端, 零依赖) —— 口径与 Python 侧 lookup_acc.py 一致
 *
 * 代价模型(用户口径: 发送从严, 接收从宽):
 *   query\lib   自然   #    b
 *   自然         0    1    1     <- 用户没写记号: 宽容(但不如精确命中)
 *   #            2    0    3     <- 用户写了记号: 必须对上才 0
 *   b            2    3    0
 * 音级不同: 4(最重)。
 * 排序: 总代价 -> 精确记号命中数 -> 热度 -> 非改编 -> 名短 -> 组名
 */

const BAD = /吉他|钢琴|双谱|器乐|非洲|尤克里里|古筝|琵琶|二胡|笛|萨克斯|总谱|合唱/;
const TAIL = /(?:[-_（(]?\s*(?:简谱|歌曲类|歌谱|五线谱|正谱|完整版|弹唱|吉他谱|钢琴谱)\s*[)）]?)+$/;

function popKey(g) {
  let k = g;
  for (let i = 0; i < 3; i++) { const k2 = k.replace(TAIL, ''); if (k2 === k || !k2) break; k = k2; }
  return k;
}

/** 解析一段文本(每行一首 JSON) -> 索引 */
export function buildIndex(text) {
  const songs = [];
  for (const line of text.split('\n')) {
    if (!line) continue;
    let r;
    try { r = JSON.parse(line); } catch { continue; }
    if (!r.p) continue;
    songs.push({
      id: r.id || '',                        // 「每谱一页」的地址 /s/<id>(见 build_web_data.py: tune_id)
      title: r.t, group: r.g, source: r.s, status: r.st, n: r.n,
      p: r.p, a: r.a || '', o: r.o || '',
      raw: r.raw || '', trunc: !!r.trunc,
      src: r.src || '',            // 原谱原文 verbatim(每谱一页用; 与 raw 的"展开版"不同)
      bars: r.bars || [], bpb: r.bpb || 4,
      sc: r.sc || '',                // 段落表("起始下标:段落名,…"): 段落权重用(副歌优先于前奏)
      file: r.file || [], tags: r.tags || [], usertags: r.usertags || [],
      alias: r.alias || [], artist: r.artist || [], transcriber: r.transcriber || [],
      hot: r.hot || 0,               // 知名度代理(该曲歌手/标签在语料里的谱数, 见 build_web_data.py)
      // ⚠ 这几个以前漏在这里 -> 索引里明明有, 结果卡上永远看不到(同一类"白名单丢字段"):
      //   mbid  = MusicBrainz 录音页; links = 人工补的收录页; srcurl = 原谱站核对过的确切页
      mbid: r.mbid || '', links: r.links || [], srcurl: r.srcurl || '',
    });
  }
  const groups = new Map();
  for (const s of songs) {
    if (!groups.has(s.group)) groups.set(s.group, []);
    groups.get(s.group).push(s);
  }
  // 知名度代理(按**曲名组**取组内最大, 与 pop 同一层): 并列时的第一顺位。
  // 值来自 build_web_data.py 的 hot 字段(该曲歌手/标签在语料里的谱数)。
  const hot = new Map();
  for (const [g, v] of groups) {
    let m = 0;
    for (const s of v) if ((s.hot || 0) > m) m = s.hot || 0;
    hot.set(g, m);
  }
  const pop = new Map();
  for (const [g, v] of groups) {
    const k = popKey(g);
    pop.set(k, (pop.get(k) || 0) + v.length);
  }
  // 每谱一页: id -> 那一首。**按 id 查表**, 前端不重算 id(口径只有 build_web_data.py 一处)
  const byId = new Map();
  for (const s of songs) if (s.id && !byId.has(s.id)) byId.set(s.id, s);
  return { songs, groups, pop, hot, byId, count: songs.length, groupCount: groups.size };
}

/** 每首歌把 p/a/o 三级数组缓存到对象上(第一次访问时构建) */
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
    const parts = s.o.split(',');
    for (let i = 0; i < n && i < parts.length; i++) O[i] = parseInt(parts[i], 10) || 0;
  }
  s._p = { P, A, O };
  return s._p;
}

// 段落权重: 用户 2026-09 规格(见 jianpu2/README_PIPELINE.md §六)。口径与 melody_search.py 一致:
// chorus/refrain 1.6 · verse 1.25 · pre-chorus/bridge/interlude 1.10 · score 1.00 ·
// intro/outro/layer/crazy-piano 0.80; 组合标签取最大; 不认识的当 1.00。
export const SEC_W = { chorus: 1.6, refrain: 1.6, verse: 1.25, 'pre-chorus': 1.10, bridge: 1.10,
  interlude: 1.10, score: 1.00, intro: 0.80, outro: 0.80, layer: 0.80, 'crazy-piano': 0.80 };
export const SEC_CN = { chorus: '副歌', refrain: '副歌', verse: '主歌', 'pre-chorus': '前副歌',
  bridge: '桥段', interlude: '间奏', score: '整曲', intro: '前奏', outro: '尾奏', layer: '过渡',
  'crazy-piano': '发狂钢琴', 'maybe-rap': '说唱?' };

export function secWeightOf(name) {
  if (!name) return 1.0;
  let w = 1.0;
  for (const x of String(name).split(',')) {
    const k = x.trim().toLowerCase();
    if (k && SEC_W[k] !== undefined && SEC_W[k] > w) w = SEC_W[k];
  }
  return w;
}

export function secLabelOf(name) {
  if (!name) return '';
  return String(name).split(',').map((x) => SEC_CN[x.trim().toLowerCase()] || x.trim())
    .filter(Boolean).join('/');
}

/** 紧凑串 "起始下标:段落名,…" -> [[起始, 权重], …](解析一次缓存在歌曲对象上)。 */
function secOf(s) {
  if (s._sec !== undefined) return s._sec;
  s._sec = [];
  if (s.sc) {
    for (const part of s.sc.split(',')) {
      const i = part.indexOf(':');
      if (i < 0) continue;
      s._sec.push([parseInt(part.slice(0, i), 10) || 0, part.slice(i + 1)]);
    }
  }
  return s._sec;
}

/** 命中区间 [i, i+n) 覆盖到的段落名(取权重最大的那个); 没分段返回空串。 */
function secNameAt(s, i, n) {
  const sec = secOf(s);
  if (!sec.length) return '';
  let best = '', bw = -1;
  for (let k = 0; k < sec.length; k++) {
    const start = sec[k][0];
    const end = k + 1 < sec.length ? sec[k + 1][0] : 1e9;
    if (start < i + n && end > i) {
      const w = secWeightOf(sec[k][1]);
      if (w > bw) { bw = w; best = sec[k][1]; }
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

/**
 * 检索。
 * @param idx  buildIndex 的结果
 * @param segs parseQuery 得到的音符数组(每段 >=5 音)——**支持多段**: 各段取最小代价后相加
 * @param opt  {top, c1, c2, c3}
 */
export function search(idx, segs, opt) {
  opt = opt || {};
  const top = opt.top || 10;
  const res = [];
  for (const [group, members] of idx.groups) {
    let total = 0, exact = 0, det = [], ok = true;
    for (const q of segs) {
      const n = q.length;
      let best = null;
      for (const s of members) {
        const { P, A } = arraysOf(s);
        if (P.length < n) continue;
        for (let i = 0; i + n <= P.length; i++) {
          let c = 0;
          for (let k = 0; k < n; k++) {
            c += cost(q[k], P[i + k], A[i + k]);
            // 早退用**严格大于**: 同分窗口要看段落权重(副歌优先于前奏), 不能被 >= 提前砍掉
            if (best && c > best.cost) break;
          }
          if (!best || c < best.cost) {
            best = { cost: c, at: i, song: s, q: q, sec: secNameAt(s, i, n) };
          } else if (c === best.cost) {
            const nm = secNameAt(s, i, n);
            if (secWeightOf(nm) > secWeightOf(best.sec)) best = { cost: c, at: i, song: s, q: q, sec: nm };
          }
        }
      }
      if (!best) { ok = false; break; }
      total += best.cost;
      for (let k = 0; k < n; k++) {
        const { A } = arraysOf(best.song);
        if (best.q[k].acc === A[best.at + k]) exact++;
      }
      det.push(best);
    }
    // 段落权重: 取各段命中的**最大**权重(与 melody_search.py 的多段口径一致)
    let secW = 1.0, sec = '';
    for (const d of det) {
      const w = secWeightOf(d.sec);
      if (w > secW) { secW = w; sec = d.sec; }
    }
    if (ok && det.length) res.push({ group, total, exact, det, secW, sec });
  }
  // **并列时的"证据优先"键**(2026-09-30 加, 与离线 lookup.py 同口径):
  //   ① 人工校对过(ok) 优先于机器转写(ocr) —— 实测 `33565653253` 那句: 《你怎么说》(ocr, 而且是
  //      转写把"行尾 3- + 间奏括号"连读拼出来的假片段) 原来靠人气压过《神々が恋した幻想郷》(ok)。
  //   ② 转写置信度高优先(谱级 `confidence=`, 转写时写; 老谱没有就当中性 0.5)。
  //   ③ 版本多优先(同一首在库里有多份谱 -> 命中更可能是真的)。
  //   之后才轮到段落权/人气/标题长度 —— 那些是"更像你想找的那首"的偏好, 与"这条谱可不可信"无关。
  const okOf = (r) => ((r.det[0] && r.det[0].song.status === 'ok') ? 0 : 1);
  const confOf = (r) => {
    const c = parseFloat((r.det[0] && r.det[0].song.conf) || '');
    return isNaN(c) ? 0.5 : c;
  };
  // **按"不同 source"数版本**(2026-09-30): 语料里 888 个 source 有 1,835 份成品(同一页被转过两遍),
  // 按文件数数版本会把重复稿当成两个版本 -> "版本多优先"这条并列依据被灌水。
  const versOf = (r) => new Set((idx.groups.get(r.group) || []).map((s) => s.src || s.s || '')).size;
  res.sort((x, y) =>
    x.total - y.total ||
    okOf(x) - okOf(y) ||
    confOf(y) - confOf(x) ||
    versOf(y) - versOf(x) ||
    y.exact - x.exact ||
    (idx.pop.get(popKey(y.group)) || 0) - (idx.pop.get(popKey(x.group)) || 0) ||
    (idx.hot.get(y.group) || 0) - (idx.hot.get(x.group) || 0) ||   // 并列: 歌手在库里谱多的先
    y.secW - x.secW ||                                            // 段落权: 副歌/主歌 > 间奏 > 整曲 > 前奏/尾奏/发狂钢琴(用户选 B)
    (BAD.test(x.group) ? 1 : 0) - (BAD.test(y.group) ? 1 : 0) ||
    x.group.length - y.group.length ||
    (x.group < y.group ? -1 : 1));
  // **代价并列有几首**: 排序后与头名同代价的组数(前端据此提示"这句不是唯一命中")
  const bestTotal = res.length ? res[0].total : 0;
  let groupsAtBest = 0;
  for (const r of res) if (r.total === bestTotal) groupsAtBest++;
  return res.slice(0, top).map((r) => {
    const h = r.det[0];
    const n = h.q.length;
    const arr = arraysOf(h.song);
    return {
      id: h.song.id || '',
      title: h.song.title, group: r.group, source: h.song.source, status: h.song.status,
      n: h.song.n, cost: r.total, exact: r.exact, qlen: n, at: h.at,
      raw: h.song.raw, trunc: h.song.trunc, bars: h.song.bars, bpb: h.song.bpb,
      src: h.song.src,
      file: h.song.file, tags: h.song.tags, usertags: h.song.usertags,
      alias: h.song.alias, artist: h.song.artist, transcriber: h.song.transcriber, mbid: h.song.mbid,
      links: h.song.links || [], srcurl: h.song.srcurl || '',
      hot: idx.hot.get(r.group) || 0,
      conf: (h.song.conf == null ? null : h.song.conf),   // 转写置信度(卡片要显示/并列要用)
      confP10: (h.song.confP10 == null ? null : h.song.confP10),
      // **"这条命中到底有多硬"** —— 给前端提示用(2026-09-29):
      //   groupsAtBest = 代价并列(拿到同一个最好代价)的歌有几首; >1 说明"这句不是唯一命中";
      //   versions     = 这首歌在库里有几个版本; ==1 且 status=ocr 说明"没有第二个版本可交叉核对"。
      // 起因: 实测《你怎么说》那句 `33565653253` 是转写把"行尾 3- + 间奏括号"连读拼出来的假片段,
      //       而它当时是**代价 0 的唯一排前**(另一首同分的《神々》排在后面) —— 前端应该把这件事说出来。
      groupsAtBest: groupsAtBest,
      versions: (idx.groups.get(r.group) || []).length,
      sec: r.sec || '', secW: r.secW || 1.0, secCn: secLabelOf(r.sec),
      libNotes: Array.from({ length: n }, (_, k) => ({ d: arr.P[h.at + k], acc: arr.A[h.at + k] })),
      qNotes: h.q,
    };
  });
}

export { popKey };

const TOKEN = /^([cqsdh]*)([,']*)([#b♯♭]?)([1-7x0])([,']*)([#b♯♭]?)([cqsdh]*)(\.*)(\]?)$/;
function parseToken(t) {
  const m = TOKEN.exec(t == null ? "" : String(t));
  if (!m) return null;
  const octs = m[2] ?? "";
  const acc = m[3] ?? "";
  const dig = m[4] ?? "";
  const post = m[5] ?? "";
  const acc2 = m[6] ?? "";
  const a = acc === "#" || acc === "♯" || acc2 === "#" || acc2 === "♯" ? 1 : acc === "b" || acc === "♭" || acc2 === "b" || acc2 === "♭" ? -1 : 0;
  const off = (octs + post).split(",").length - 1 - ((octs + post).split("'").length - 1);
  return { d: dig, acc: a, oct: off };
}
function beat(t) {
  const s = t == null ? "" : String(t);
  let letters = (s.match(/^[cqsdh]+/) ?? [""])[0] ?? "";
  if (!letters) {
    const m = s.match(/([cqsdh]+)\.*[[\]]?$/);
    letters = m ? m[1] ?? "" : "";
  }
  const BEAT = { h: 0.0625, c: 1, "": 1, q: 0.5, s: 0.25, d: 0.125 };
  const v = Object.prototype.hasOwnProperty.call(BEAT, letters) ? BEAT[letters] : 0.0625;
  return s.endsWith(".") ? v * 1.5 : v;
}
function isPitch(t) {
  const p = parseToken(t);
  return !!p && p.d !== "0" && p.d !== "x";
}
function parseQuery(raw) {
  const s = String(raw == null ? "" : raw);
  const out = [];
  const re = /([#b♯♭]?)([,']*)([1-7])(?:([,']+)(?![,']*[1-7]))?(?:([#b♯♭])(?![,']*[1-7]))?/y;
  for (let i = 0; i < s.length; ) {
    re.lastIndex = i;
    const m = re.exec(s);
    if (!m) {
      i += 1;
      continue;
    }
    const accs = m[1] + (m[5] ?? "");
    const a = /[#♯]/.test(accs) ? 1 : /[b♭]/.test(accs) ? -1 : 0;
    const o = m[2] + (m[4] ?? "");
    out.push({
      d: +m[3],
      acc: a,
      oct: o.split(",").length - 1 - (o.split("'").length - 1)
    });
    i = re.lastIndex;
  }
  return out;
}
function show(notes) {
  return notes.map((n) => (n.acc === 1 ? "#" : n.acc === -1 ? "b" : "") + n.d).join(" ");
}
export {
  beat,
  isPitch,
  parseQuery,
  parseToken,
  show
};

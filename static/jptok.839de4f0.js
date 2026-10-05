const TOKEN = /^([cqsdh]*)([,']*)([#b♯♭]?)([1-7x0])([,']*)([#b♯♭]?)([cqsdh]*)(\.*)(\]?)$/;
const CHORD = /^([cqsdh]*)((?:[,']*[#b♯♭]?[1-7x0]){2,})([,']*)([#b♯♭]?)([cqsdh]*)(\.*)(\]?)$/;
const NOTE = /([,']*)([#b♯♭]?)([1-7x0])/g;
function accJoin(...marks) {
  const s = marks.filter(Boolean).join("");
  return /[#♯]/.test(s) ? 1 : /[b♭]/.test(s) ? -1 : 0;
}
function octJoin(...marks) {
  const s = marks.filter(Boolean).join("");
  return (s.match(/,/g) ?? []).length - (s.match(/'/g) ?? []).length;
}
function sound(dig, accs, octs, acc2 = "", oct2 = "") {
  return { d: dig, acc: accJoin(accs, acc2), oct: octJoin(octs, oct2) };
}
function parseTokenAll(t) {
  const s = t == null ? "" : String(t);
  const m = TOKEN.exec(s);
  if (m) {
    return [sound(m[4] ?? "", m[3] ?? "", m[2] ?? "", m[6] ?? "", m[5] ?? "")];
  }
  const c = CHORD.exec(s);
  if (!c) return [];
  const body = c[2] ?? "";
  const oct2 = c[3] ?? "";
  const acc2 = c[4] ?? "";
  const parts = [];
  NOTE.lastIndex = 0;
  let mm;
  const raw = [];
  while ((mm = NOTE.exec(body)) !== null) raw.push([mm[1] ?? "", mm[2] ?? "", mm[3] ?? ""]);
  for (let i = 0; i < raw.length; i++) {
    const [octs, acc, dig] = raw[i];
    const last = i === raw.length - 1;
    parts.push(sound(dig, acc, octs, last ? acc2 : "", last ? oct2 : ""));
  }
  return parts;
}
function parseToken(t) {
  const got = parseTokenAll(t);
  if (got.length === 0) return null;
  return got.length === 1 ? got[0] : got;
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
  return parseTokenAll(t).some((p) => p.d !== "0" && p.d !== "x");
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
  parseTokenAll,
  show
};

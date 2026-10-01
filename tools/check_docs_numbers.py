"""校验**文档里引用的数字**与语料/索引实测一致 —— 防止简历材料"对不上账"。

为什么需要: 简历/评审最怕"三个文档三个数"。这个检查把 README（含 URL 编码的 shields 徽章）、
`docs/TECH_STACK.md`、`docs/ARCHITECTURE.md`、`docs/RESUME.md` 里的关键数字，与
语料 `data.jsonl` 和站点索引 `data/songs.jsonl.gz` 的**实测值**逐项比对。

用法:  py -3.13 tools/check_docs_numbers.py
退出码: 0 一致 / 1 有出入（并打印哪份文档哪个数字不对）
"""
import glob
import gzip
import json
import os
import sys
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.environ.get("JIANPU_DB") or os.path.join(os.path.dirname(ROOT), "jianpu-db")   # 与 server.py 同规矩: 环境变量优先
DOCS = ("README.md", "docs/TECH_STACK.md", "docs/ARCHITECTURE.md", "docs/RESUME.md")


def corpus_numbers():
    n = notes = bars = 0
    for line in open(os.path.join(DB, "data.jsonl"), encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        n += 1
        notes += int(d.get("n_notes") or 0)
        bars += len(d.get("bars") or [])
    files = len(glob.glob(os.path.join(DB, "scores", "*.txt")))
    return n, notes, bars, files


def index_rows():
    p = os.path.join(ROOT, "data", "songs.jsonl.gz")
    if not os.path.exists(p):
        return []
    return [ln for ln in gzip.open(p, "rb").read().split(b"\n") if ln.strip()]


def main():
    # CI 里没有语料仓库（它是独立仓库）—— 这时**明确跳过**并说明，而不是把整条流水线判红。
    if not os.path.exists(os.path.join(DB, "data.jsonl")):
        print(f"跳过：本机没有语料库 data.jsonl（{DB}）—— 这个检查要在有语料的机器上跑")
        return 0
    n, notes, bars, files = corpus_numbers()
    idx = len(index_rows())
    # ⚠ 用**列表**而不是 dict: 曲数与索引行数实测是同一个数(11,495), 用 dict 当 key 会互相覆盖 ——
    #   第一版就是这么写的, 结果 RESUME 被判"缺曲数"(其实它写了)。自查发现的。
    must = [(f"{n:,}", "曲数"), (f"{notes:,}", "音符数"), (f"{bars:,}", "显式小节线数")]
    if idx:
        must.append((f"{idx:,}", "索引行数"))
    print(f"实测: 曲数 {n:,} · 音符 {notes:,} · 小节线 {bars:,} · 曲谱文件 {files:,}"
          + (f" · 索引行 {idx:,}" if idx else ""))

    bad = 0
    for rel in DOCS:
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            print(f"  ! 缺少 {rel}")
            bad += 1
            continue
        txt = open(p, encoding="utf-8").read()
        decoded = urllib.parse.unquote(txt)              # shields 徽章里的数字是 URL 编码的
        hits = [f"{label}（{num}）" for num, label in must if num in decoded]
        miss = [f"{label}（{num}）" for num, label in must if num not in decoded]
        # RESUME 里允许用"11,495 首 / 253 万音符"这种近似说法，但必须至少带精确曲数
        need = ["曲数"] if rel.endswith("RESUME.md") else [label for _num, label in must]
        missing_need = [x for x in need if not any(x in h for h in hits)]
        status = "✓" if not missing_need else "✗"
        if missing_need:
            bad += 1
        print(f"  {status} {rel:26} 命中 {len(hits)}/{len(must)}"
              + (f"  缺: {', '.join(missing_need)}" if missing_need else ""))
        if miss and not missing_need:
            print(f"      （未引用: {', '.join(miss)}）")
    print("\n" + ("通过：文档数字与语料一致" if not bad else f"失败 {bad} 份文档"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

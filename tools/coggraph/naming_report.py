#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""命名审计 · 「无锚定词」四问测试（Kimi 框架落地）

对一个标题（候选概念名）问四个问题：
  Q1 能否连到稳定概念节点？     —— 该节点有显式边 / 被正文互引
  Q2 能否与同义/上下位/缩写/原名对齐？ —— 同名节点组（别名组）或被正文提及
  Q3 是否在语料中可复现、统计稳定？    —— 出现在 >=3 个节点正文 / 同名 >=3
  Q4 是否承担法律/标准/品牌/社群标识？ —— 标题命中规范标识词（INN/ISO/GB/RFC/药典…）

裁决：
  Q4 成立                          -> 强规范锚（不可被意译覆盖）
  Q1 & Q2 & Q3                     -> 已锚定
  Q2 & Q3（缺 Q1）                 -> alias 候选（可挂到规范概念）
  四问皆否                          -> 隔离区（不进主索引）

用法：python scripts/naming_report.py --graph <graph.json> --derived <derived_edges.json> --out <dir>
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import os
import re

# Q4 规范标识词（判据来源：本文件 docstring 第 9 行 Q4「标题命中规范标识词（INN/ISO/GB/RFC/药典…）」）。
# 旧式 `\b(...)\b` 在两类情形形同虚设（issue #315）：
#   ① `\b` 是**词边界**，而汉字在 Python re 里属 \w ⇒ 标识词嵌在汉字串中时（如「依据国家标准制定」
#      「遵守INN规则」）两侧都不构成边界，恒不命中；
#   ② `GB[/T]` 是**单字符类**，只匹配 `GB/` 或 `GBT`，`GB/T 12345`、`GB 12345` 全部漏判。
# 修法：拉丁词改用「非 ASCII 字母数字」显式环视边界（汉字非 ASCII 字母数字 ⇒ 汉字紧邻也能命中，
# 而 ISOLATION/INNOVATION 之类英文粘连仍被挡）；GB 改为 `GB(?:/T|\s+)\s*\d+`（GB / GB/T 两种写法）。
# 判别力变化：新口径**新增**对「汉字/数字紧邻」写法（ISO9001、药典2020、GB/T12345）的识别——
# 这些旧 `\b` 同样漏判；未删任何原有命中，也未放宽英文粘连的拦截。
NORM_MARKERS = re.compile(
    r"(?<![A-Za-z0-9])(?:INN|ISO|IEC|IEEE|W3C|RFC\s*\d+|GB(?:/T|\s+)\s*\d+|药典|国家标准|行业标准|注册商标|专利)(?![A-Za-z])", re.I)


def main(argv=None):
    ap = argparse.ArgumentParser(description="命名审计：无锚定词四问测试")
    ap.add_argument("--graph", required=True)
    ap.add_argument("--derived", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-title", type=int, default=6)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)

    with io.open(args.graph, encoding="utf-8") as f:
        g = json.load(f)
    with io.open(args.derived, encoding="utf-8") as f:
        d = json.load(f)

    nodes = g["nodes"]
    # Q1 判据（docstring 第 6 行）：该节点有显式边 / 被正文互引。
    # 旧口径 `for e in g["edges"] + d["edges"]: deg[...] += 1` 把**结构性派生边**
    # 一并计度，于是 q1 在真实管线里恒真、「alias 候选」不可达（issue #359）：
    #   ① R1/R2/R3/R4 派生边只是元数据巧合（同桶/同源/同标签/同日）——任何有
    #      bucket 的节点都被 R1 挂到桶枢纽、被 R2 挂到源枢纽、被 R4 挂到日枢纽，
    #      度恒 > 0；它们不构成「连到稳定概念节点」。
    #   ② R5 自身标题行递归：derive_edges 的 R5 在正文里搜标题，而节点正文首行是
    #      `# 功能名：<自身标题>`；同名组内 g2/g3 的这行匹配到组首 g1，产出
    #      g2->g1 / g3->g1 的「互引」——这是同名声明，不是被别的概念引用。
    # 故 Q1 只认：显式边，或**提及者标题 ≠ 被提及标题**的 R5 正文互引。
    title_of = {n["id"]: (n.get("title") or "").strip() for n in nodes}
    deg = collections.Counter()
    for e in g["edges"]:
        deg[e["s"]] += 1
        deg[e["t"]] += 1
    for e in d["edges"]:
        if e.get("rule") != "R5_body_crossref":
            continue
        if title_of.get(e["s"], "") == title_of.get(e["t"], ""):
            continue                      # 自身标题行递归（同名声明），非概念互引
        deg[e["s"]] += 1
        deg[e["t"]] += 1

    # 标题组：同名节点 = 别名/重复组（Kimi：同一概念被切碎）
    groups = collections.defaultdict(list)
    for n in nodes:
        t = (n.get("title") or "").strip()
        # #627：规范锚词豁免长度闸。「药典/专利/国家标准/INN/ISO/IEC/IEEE/W3C」等
        # 标题本身即 Q4 规范标识词，字数常 < 默认 --min-title=6 ⇒ 被闸挡在 groups 之外，
        # Q4 永远不会被评估 ⇒ 默认参数下这类节点零「强规范锚」，报告对这一盲区零披露。
        # NORM_MARKERS 命中的标题进候选集，让 Q4 至少能被裁决（本行即该豁免，一行修）。
        if len(t) >= args.min_title or NORM_MARKERS.search(t):
            groups[t].append(n)

    # Q2/Q3 的"被正文提及"只数**被提及**方向：derive_edges 的 R5 是
    # add(me, owner, ...)（s=提及者、t=被提及的规范概念节点），把 e["s"] 也算进来
    # 会把"这条正文提及了多少个概念"当成"被多少个节点正文提及"。
    # #606：R5 还把同名节点自己的标题行（`# 功能名：<自身标题>`）记成互引边
    # （#359 已认定的 phantom，Q1 的 deg 侧已滤）——r5_in 若全收，同名组会被
    # 自己的 phantom 提及顶上去：2 名组真实外部提及只有 2，加 1 条 phantom 后
    # r5_in=3 ⇒ q3=True ⇒ 误判「已锚定」逃出隔离区。故与上方 deg 完全同口径过滤。
    r5_in = collections.Counter()
    for e in d["edges"]:
        if e.get("rule") == "R5_body_crossref":
            if title_of.get(e["s"], "") == title_of.get(e["t"], ""):
                continue                  # #606：同名声明 phantom，与 Q1 deg 同口径过滤
            r5_in[e["t"]] += 1          # t = 被提及者

    rows = []
    for t, members in groups.items():
        ids = [m["id"] for m in members]
        # structural / anchor 层是价值骨架，锚定在图外（personaPrefix / route / 使用纪律），
        # 不适用"必须被引用才锚定"的判据（第 13 条不适用：它们是规范来源而非被推导物）
        structural = any((m.get("layer") in ("structural", "anchor")) for m in members)
        q1 = any(deg.get(i, 0) > 0 for i in ids) or structural
        q2 = len(ids) > 1 or sum(r5_in.get(i, 0) for i in ids) > 0 or structural
        q3 = len(ids) >= 3 or sum(r5_in.get(i, 0) for i in ids) >= 3 or structural
        q4 = bool(NORM_MARKERS.search(t))
        if q4:
            cls = "强规范锚"
        elif q1 and q2 and q3:
            cls = "已锚定"
        elif q2 and q3:
            cls = "alias候选"
        else:
            cls = "隔离区"
        rows.append({"title": t, "n": len(ids), "q1": q1, "q2": q2, "q3": q3, "q4": q4,
                     "class": cls, "bucket_sample": members[0].get("bucket", ""),
                     "ids": ids if cls in ("隔离区", "强规范锚") else []})

    cnt = collections.Counter(r["class"] for r in rows)
    order = ["强规范锚", "已锚定", "alias候选", "隔离区"]
    report = [
        "# 命名审计 · 无锚定词四问测试", "",
        "- 候选标题组：%d（去重后标题，min_len=%d）" % (len(rows), args.min_title),
        "- 判据：Q1 连到稳定节点 / Q2 别名可对齐 / Q3 语料可复现 / Q4 规范标识", "",
        "## 分布", "",
    ] + ["- %s: %d" % (k, cnt.get(k, 0)) for k in order] + [
        "", "## 强规范锚（Q4，不可被意译覆盖）", "",
    ] + (["- " + r["title"] for r in rows if r["class"] == "强规范锚"] or ["- （无）"]) + [
        "", "## 隔离区候选（四问皆否，不进主索引）", "",
    ] + (["- %s（%d 节点：%s）" % (r["title"], r["n"], ", ".join(r["ids"][:6]))
          for r in sorted([x for x in rows if x["class"] == "隔离区"], key=lambda x: -x["n"])][:60] or ["- （无）"]) + [
        "", "## 重复标题别名组（需要 canonical 化，Top50）", "",
        "| 标题 | 节点数 | 判定 |", "|---|---|---|",
    ] + ["| %s | %d | %s |" % (r["title"], r["n"], r["class"])
         for r in sorted([x for x in rows if x["n"] > 1], key=lambda x: -x["n"])[:50]]

    os.makedirs(args.out, exist_ok=True)
    if args.write:
        with io.open(os.path.join(args.out, "naming_report.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(report))
        with io.open(os.path.join(args.out, "naming_report.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump({"rows": rows, "counts": dict(cnt)}, f, ensure_ascii=False)
    print(json.dumps({"counts": dict(cnt), "groups": len(rows)}, ensure_ascii=False))
    if not args.write:
        print("\n".join(report[:60]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

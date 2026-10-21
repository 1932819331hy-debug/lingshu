# -*- coding: utf-8 -*-
"""_det/diff_dumps.py · 两份 dump 的**结构化差异定位**（诊断用 · 纯标准库）

为什么需要：`cmp` 只说"第 61255 字节不同"，不说**哪个网格的哪个顶点差多少**。
定位不到层就没法判断是"浮点末位"还是"真的换了东西"。

用法：python3 -X utf8 _det/diff_dumps.py A.json B.json
"""
from __future__ import annotations

import json
import sys


def main(a_path, b_path):
    a = json.load(open(a_path, encoding="utf-8"))
    b = json.load(open(b_path, encoding="utf-8"))
    out = {"a": a_path, "b": b_path}

    # 顶层标量键
    ks = sorted(set(a) | set(b))
    out["scalar_keys_diff"] = {k: [a.get(k), b.get(k)] for k in ks
                               if not isinstance(a.get(k), (list, dict))
                               and a.get(k) != b.get(k)}
    out["nodes_equal"] = a.get("nodes") == b.get("nodes")
    out["links_names_equal"] = ([l["name"] for l in a.get("links", [])]
                                == [l["name"] for l in b.get("links", [])])
    out["meshes_names_equal"] = ([m["name"] for m in a.get("meshes", [])]
                                 == [m["name"] for m in b.get("meshes", [])])
    out["n_tracks"] = [len(a.get("tracks") or []), len(b.get("tracks") or [])]

    # 逐网格顶点差异
    mb = {m["name"]: m for m in b.get("meshes", [])}
    per = []
    for m in a.get("meshes", []):
        o = mb.get(m["name"])
        if o is None:
            per.append({"name": m["name"], "err": "missing in b"})
            continue
        va, vb = m["verts"], o["verts"]
        if len(va) != len(vb):
            per.append({"name": m["name"], "n_verts": [len(va), len(vb)]})
            continue
        mx, nbad = 0.0, 0
        for i in range(len(va)):
            for j in range(3):
                d = abs(float(va[i][j]) - float(vb[i][j]))
                if d > 0:
                    nbad += 1
                    mx = max(mx, d)
        tri_eq = m["tris"] == o["tris"]
        per.append({"name": m["name"], "n_verts": len(va), "n_comp_diff": nbad,
                    "max_abs": mx, "tris_equal": tri_eq})
    out["meshes"] = per
    bad = [p for p in per if p.get("n_comp_diff") or p.get("err")
           or p.get("tris_equal") is False]
    out["n_meshes_with_diff"] = len(bad)
    out["worst"] = max((p.get("max_abs") or 0.0) for p in per) if per else 0.0

    # 逐 track 差异
    ka = {(t["entity"], t["tick"]): t for t in a.get("tracks") or []}
    kb = {(t["entity"], t["tick"]): t for t in b.get("tracks") or []}
    tmax, tn = 0.0, 0
    tkeys = sorted(set(ka) | set(kb))
    for k in tkeys:
        ra, rb = ka.get(k), kb.get(k)
        if ra is None or rb is None:
            tn += 1
            continue
        for i in range(3):                       # dump 的 pos 是**列表**（oracle 里才是字典）
            d = abs(float(ra["pos"][i]) - float(rb["pos"][i]))
            if d > 0:
                tn += 1
                tmax = max(tmax, d)
        for i in range(3):
            for j in range(3):
                d = abs(float(ra["rot3"][i][j]) - float(rb["rot3"][i][j]))
                if d > 0:
                    tn += 1
                    tmax = max(tmax, d)
    out["tracks_n_comp_diff"] = tn
    out["tracks_max_abs"] = tmax

    # links（anchor / local_center / local3 / world3 / vol）
    la = {l["name"]: l for l in a.get("links", [])}
    lb = {l["name"]: l for l in b.get("links", [])}
    lmax, ln = 0.0, 0
    for nm in sorted(set(la) | set(lb)):
        ra, rb = la.get(nm), lb.get(nm)
        if ra is None or rb is None:
            ln += 1
            continue
        pa = {p["src"]: p for p in ra["parts"]}
        pb = {p["src"]: p for p in rb["parts"]}
        for s in sorted(set(pa) | set(pb)):
            qa, qb = pa.get(s), pb.get(s)
            if qa is None or qb is None:
                ln += 1
                continue
            for f in ("dims", "local_center", "world_trans"):
                for i in range(3):
                    d = abs(float(qa[f][i]) - float(qb[f][i]))
                    if d > 0:
                        ln += 1
                        lmax = max(lmax, d)
            d = abs(float(qa["vol"]) - float(qb["vol"]))
            if d > 0:
                ln += 1
                lmax = max(lmax, d)
            for f in ("world3", "local3"):
                for i in range(3):
                    for j in range(3):
                        d = abs(float(qa[f][i][j]) - float(qb[f][i][j]))
                        if d > 0:
                            ln += 1
                            lmax = max(lmax, d)
            if qa.get("color") != qb.get("color"):
                ln += 1
    out["links_n_comp_diff"] = ln
    out["links_max_abs"] = lmax
    print("DIFFDUMPS " + json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

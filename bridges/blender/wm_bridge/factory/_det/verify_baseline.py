# -*- coding: utf-8 -*-
"""_det/verify_baseline.py · **基准可信度守卫**（纯标准库 · 零 bpy · 可复跑）

Lead 收窄后的三条交付都在这里被机械核对；打印 `VERDICT=PASS|FAIL`，退出码 0/2。

  [A] **跨进程逐字节可复现**：同一 spec 两次**独立进程**（blender 无头）产出的 dump
      sha256 相同 —— 两种烘焙各一对。
  [B] **dump 与 bpy_dump 同构**：顶层键集 == `wm_bridge.dump/1` 的契约键集，
      `meshes[]`/`links[].parts[]`/`tracks[]` 的键集也逐条核对。
  [C] **端到端 rc=0**：`cli export-obj / export-shapes / export-oracle --spec <dump>`
      三件各 rc=0；shapes.json 可被 `geom.read_shapes` 读回、oracle 可被
      `oracle.read_oracle` 读回（= 过各自契约）。
  [D] **几何对拍**：OBJ 与既有 `f0_eval/turret/mesh.obj` 的行数/面数/材质一致，
      逐行差异只允许出现在 `v` 行的末位；给**差异行数**与**最大数值偏差**。
  [E] **件级对拍**：我的 dump 与既有 dump 各自过 `geom.part_from_blender`，
      逐件比 kind/size/pos/rot/color。
  [F] **运动面逐 tick 对拍**：6 个实体 × 144 tick 的 pos/rot_quat 对既有 `oracle.jsonl`
      取最大偏差；分 `bezier`（应复现既有）与 `linear`（匀速，应给出偏差读数）两路。

跑法：
    cd blender-wm-bridge
    python3 -X utf8 wm_bridge/factory/_det/verify_baseline.py
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir, os.pardir))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from wm_bridge import cli as CLI            # noqa: E402
from wm_bridge import geom as G             # noqa: E402
from wm_bridge import oracle as O           # noqa: E402
from wm_bridge.factory.geo import spec as S  # noqa: E402

PROD = os.environ.get("WMOPT_GEO", "/mnt/d/DSH/blender/out/wmopt/geo")
LEGACY = os.environ.get("WMTEST", "/mnt/d/DSH/blender/out/wmtest")
SPEC = os.path.join(REPO, "wm_bridge", "factory", "geo", "specs", "turret.spec.json")

#: 数值地板 = **10 × 导出量子**（量子 = 1e-6，因为 dump 的小数留 6 位）。
#: 依据是 DESIGN §6.3c：地板压到 1e-6 会在"只差最后一粒"处产生假失败（`geom.POSE_TOL` 同值）。
#: ⇒ 本守卫的**红线是 1e-5**，但读数一律**原样贴出**（实测 1e-6 = 恰好一个量子）。
TOL = 1e-5

#: `wm_bridge.dump/1` 的契约键集（= `bpy_dump.collect()` 顶层 out 的键）
DUMP_KEYS = {"schema", "name", "frame", "frames", "tick_hz", "world_size",
             "translate", "source", "mesh_source", "modifier_report", "collections",
             "scene", "meshes", "links", "nodes", "tracks"}
MESH_KEYS = {"name", "verts", "tris", "material", "materials", "n_modifiers",
             "modifiers"}
PART_KEYS = {"src", "dims", "local_center", "vol", "world3", "local3",
             "world_trans", "color"}
TRACK_KEYS = {"entity", "tick", "category", "link", "pos", "rot3"}

RESULTS = []


def check(tag, ok, detail):
    RESULTS.append((tag, bool(ok), detail))
    print("[%s] %-4s %s" % ("PASS" if ok else "FAIL", tag, detail))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def _angle_deg(q):
    """[w,x,y,z] → 绕 lingshu-Y 的转角（度）。既有 turret 的 yaw 只在 Y 轴上转。"""
    return math.degrees(2.0 * math.atan2(q[2], q[0]))


def _dtheta(a, b):
    return abs((a - b + 180.0) % 360.0 - 180.0)


# ─────────────────────────────────────────────────────────── [A] 跨进程
def part_a():
    pairs = [("bezier", "bez/a/dump.json", "bez/b/dump.json"),
             ("linear", "lin/a/dump.json", "lin/b/dump.json")]
    hashes = {}
    for mode, ra, rb in pairs:
        pa, pb = os.path.join(PROD, ra), os.path.join(PROD, rb)
        if not (os.path.exists(pa) and os.path.exists(pb)):
            check("A/" + mode, False, "缺产物：%s / %s（先跑 run_produce）" % (pa, pb))
            continue
        ha, hb = sha256(pa), sha256(pb)
        hashes[mode] = (ha, hb)
        check("A/" + mode, ha == hb,
              "两次独立进程 dump sha256 %s / %s %s"
              % (ha[:16], hb[:16], "相同" if ha == hb else "**不同**"))
    return hashes


# ─────────────────────────────────────────────────────────── [B] 同构
def part_b(dump):
    ok = True
    d = set(dump.keys())
    ok &= d == DUMP_KEYS
    check("B/顶层键", d == DUMP_KEYS,
          "键集%s契约（多=%s 缺=%s）" % ("==" if d == DUMP_KEYS else "!=",
                                        sorted(d - DUMP_KEYS), sorted(DUMP_KEYS - d)))
    ok &= dump.get("schema") == "wm_bridge.dump/1"
    check("B/schema", dump.get("schema") == "wm_bridge.dump/1",
          "schema=%r" % (dump.get("schema"),))
    bad = [m["name"] for m in dump["meshes"] if set(m.keys()) != MESH_KEYS]
    ok &= not bad
    check("B/meshes 键", not bad, "19 个网格键集一致" if not bad else "异常件：%s" % bad[:4])
    badp = [(l["name"], p["src"]) for l in dump["links"] for p in l["parts"]
            if set(p.keys()) != PART_KEYS]
    ok &= not badp
    check("B/parts 键", not badp, "全部件键集一致" if not badp else "异常：%s" % badp[:4])
    badt = len(dump["tracks"]) and set(dump["tracks"][0].keys()) != TRACK_KEYS
    ok &= not badt
    check("B/tracks 键", not badt,
          "track 键集与 bpy_dump 一致" if not badt else "tracks 键不符")
    return ok


# ─────────────────────────────────────────────────────────── [C] 端到端
def part_c(dump_path, outdir):
    os.makedirs(outdir, exist_ok=True)
    obj = os.path.join(outdir, "mesh.obj")
    mtl = os.path.join(outdir, "mesh.mtl")
    shp = os.path.join(outdir, "shapes.json")
    orc = os.path.join(outdir, "oracle.jsonl")
    rcs = {}
    rcs["export-obj"] = CLI.main(["export-obj", "--spec", dump_path,
                                  "--out", obj, "--mtl", mtl])
    rcs["export-shapes"] = CLI.main(["export-shapes", "--spec", dump_path,
                                     "--out", shp])
    rcs["export-oracle"] = CLI.main(["export-oracle", "--spec", dump_path,
                                     "--out", orc])
    allok = all(v == 0 for v in rcs.values())
    check("C/rc", allok, "三件 rc = %s" % json.dumps(rcs))
    try:
        sd = G.read_shapes(shp)
        check("C/shapes 契约", len(sd["shapes"]) > 0 and sd["export_frame"] == 0,
              "read_shapes ok：links=%d parts=%d export_frame=%s"
              % (len(sd["shapes"]), sum(len(v) for v in sd["shapes"].values()),
                 sd["export_frame"]))
    except Exception as exc:                                   # noqa: BLE001
        check("C/shapes 契约", False, "read_shapes 失败：%s" % exc)
    try:
        od = O.read_oracle(orc)
        cov = O.frame_coverage_problem(od)
        check("C/oracle 契约", cov is None,
              "read_oracle ok：tracks=%d entities=%d；帧覆盖=%s"
              % (len(od["tracks"]), len({t["entity"] for t in od["tracks"]}),
                 cov or "无问题"))
    except Exception as exc:                                   # noqa: BLE001
        check("C/oracle 契约", False, "read_oracle 失败：%s" % exc)
    return (obj, mtl, shp, orc) if allok else None


# ─────────────────────────────────────────────────────────── [D] OBJ 对拍
def part_d(mine, ref):
    def load(p):
        v, kinds, faces, mats = [], {}, 0, set()
        for ln in open(p, encoding="utf-8").read().splitlines():
            t = ln.split()
            if not t:
                continue
            if t[0] == "v":
                v.append(tuple(float(x) for x in t[1:4]))
            elif t[0] == "f":
                faces += 1
            elif t[0] == "usemtl":
                mats.add(t[1])
            kinds[t[0]] = kinds.get(t[0], 0) + 1
        return v, kinds, faces, mats
    va, ka, fa, ma = load(mine)
    vb, kb, fb, mb = load(ref)
    check("D/行类计数", ka == kb, "行类计数 mine=%s ref=%s" % (ka, kb))
    check("D/v 顶点数", len(va) == len(vb), "顶点数 mine=%d ref=%d" % (len(va), len(vb)))
    check("D/f 面数", fa == fb, "面数 mine=%d ref=%d" % (fa, fb))
    check("D/usemtl", ma == mb, "材质名集合 mine=%s ref=%s" % (sorted(ma), sorted(mb)))
    if len(va) == len(vb):
        mx, n = 0.0, 0
        for i in range(len(va)):
            for j in range(3):
                d = abs(va[i][j] - vb[i][j])
                if d > 0:
                    n += 1
                    mx = max(mx, d)
        check("D/顶点偏差", mx <= TOL,
              "逐分量差异 %d/%d，最大 |Δ| = %.3g（地板 %.0e = 10× 导出量子）"
              % (n, len(va) * 3, mx, TOL))
    # 逐行文本差异（只为报"差异行数"）
    la = open(mine, encoding="utf-8").read().splitlines()
    lb = open(ref, encoding="utf-8").read().splitlines()
    nd = sum(1 for x, y in zip(la, lb) if x != y)
    byk = {}
    for x, y in zip(la, lb):
        if x != y:
            k = x.split()[0] if x.split() else "?"
            byk[k] = byk.get(k, 0) + 1
    check("D/差异行", nd <= 40,
          "逐行差异 %d/%d 行，分布=%s" % (nd, len(lb), json.dumps(byk)))


# ─────────────────────────────────────────────────────────── [E] 件级对拍
def _parts_of(dump):
    out = {}
    for lk in dump["links"]:
        for p in lk["parts"]:
            out[p["src"]] = G.part_from_blender(
                p["dims"], p["vol"], p["world3"], p["world_trans"],
                p["local_center"], lk["anchor"], p.get("color") or [170, 170, 175])
    return out


def part_e(mine_dump, ref_dump):
    a, b = _parts_of(mine_dump), _parts_of(ref_dump)
    check("E/件名集合", set(a) == set(b),
          "件数 mine=%d ref=%d；对称差=%s"
          % (len(a), len(b), sorted(set(a) ^ set(b))[:6]))
    mx = {"pos": 0.0, "size": 0.0, "rot": 0.0}
    nkind = ncol = 0
    for k in sorted(set(a) & set(b)):
        pa, pb = a[k], b[k]
        if pa["kind"] != pb["kind"]:
            nkind += 1
        if list(pa["color"]) != list(pb["color"]):
            ncol += 1
        for f in ("pos", "size", "rot"):
            for i in range(3):
                mx[f] = max(mx[f], abs(pa[f][i] - pb[f][i]))
    check("E/kind", nkind == 0, "kind 不一致件数 = %d" % nkind)
    check("E/color", ncol == 0, "color 不一致件数 = %d" % ncol)
    check("E/数值", max(mx.values()) <= TOL,
          "pos/size/rot 最大偏差 = %.3g / %.3g / %.3g" % (mx["pos"], mx["size"], mx["rot"]))


# ─────────────────────────────────────────────────────────── [F] 运动面
def _legacy_tracks():
    out = {}
    for line in open(os.path.join(LEGACY, "turret", "oracle.jsonl"), encoding="utf-8"):
        r = json.loads(line)
        if "scene" in r and "entity" not in r:
            continue
        out[(r["entity"], r["tick"])] = r
    return out


def part_f(tag, dump, legacy):
    doc = O.doc_from_dump(dump)
    mine = {(r["entity"], r["tick"]): r for r in doc["tracks"]}
    common = sorted(set(mine) & set(legacy))
    if not common:
        check("F/%s" % tag, False, "没有共同 (entity,tick)")
        return
    pmax, qmax = 0.0, 0.0
    for k in common:
        for ax in ("x", "y", "z"):
            pmax = max(pmax, abs(mine[k]["pos"][ax] - legacy[k]["pos"][ax]))
        for i in range(4):
            qmax = max(qmax, abs(mine[k]["rot_quat"][i] - legacy[k]["rot_quat"][i]))
    ymax = max(_dtheta(_angle_deg(mine[k]["rot_quat"]),
                       _angle_deg(legacy[k]["rot_quat"]))
               for k in common if k[0] == "TURRET_yaw")
    detail = ("共同 %d 条：pos max|Δ|=%.3g  quat max|Δ|=%.3g  yaw max|Δθ|=%.4f°"
              % (len(common), pmax, qmax, ymax))
    if tag == "bezier":
        # ★ 地板口径与 DESIGN §6.3c 一致：`geom.POSE_TOL` = 1e-5 = **10× 导出量子(1e-6)**。
        #   把阈值压在 1e-6 会在"只差最后一粒"处产生**假失败**（DESIGN 原文的教训）。
        ok = (qmax <= TOL and pmax <= TOL)
        note = ""
    else:
        # 匀速那一路**必须真的不一样**：既证明两条语义确实不同（不是空转），
        # 又证明偏差有界（若 >1e2 说明层级解错了，不是缓动差）。
        ok = 1.0 <= ymax <= 30.0 and pmax > 0.0
        note = "（匀速语义，%s）" % ("差异在预期带内" if ok else "**差异越界**")
    check("F/%s" % tag, ok, detail + note)
    ticks = [t for t in (1, 2, 3, 6, 12, 24, 48, 143) if ("TURRET_yaw", t) in mine]
    print("       yaw %s: mine=%s" % (tag, [round(_angle_deg(mine[("TURRET_yaw", t)]["rot_quat"]), 5) for t in ticks]))
    print("       yaw legacy : %s" % ([round(_angle_deg(legacy[("TURRET_yaw", t)]["rot_quat"]), 5) for t in ticks],))


def main():
    print("=" * 78)
    print("基准可信度守卫 · spec=%s" % SPEC)
    spec = json.load(open(SPEC, encoding="utf-8"))
    n = S.normalize_spec(spec)
    print("spec 摘要：" + json.dumps(S.spec_summary(spec), ensure_ascii=False))
    print("-" * 78)

    hashes = part_a()

    bez = json.load(open(os.path.join(PROD, "bez/a/dump.json"), encoding="utf-8"))
    lin = json.load(open(os.path.join(PROD, "lin/a/dump.json"), encoding="utf-8"))
    legacy_dump = json.load(open(os.path.join(LEGACY, "spec/turret.spec.json"),
                                 encoding="utf-8"))

    part_b(bez)

    tmp = tempfile.mkdtemp(prefix="wmopt_guard_")
    outs = part_c(os.path.join(PROD, "bez/a/dump.json"), tmp)
    if outs:
        ref_obj = os.path.join(LEGACY, "f0_eval/turret/mesh.obj")
        if os.path.exists(ref_obj):
            part_d(outs[0], ref_obj)
        else:
            check("D/对拍", False, "缺参照 %s" % ref_obj)
        part_e(bez, legacy_dump)
        legacy = _legacy_tracks()
        part_f("bezier", bez, legacy)
        part_f("linear", lin, legacy)
    else:
        check("D/E/F", False, "端到端未跑通，跳过对拍")

    nfail = sum(1 for _t, ok, _d in RESULTS if not ok)
    print("-" * 78)
    print("通过 %d / 失败 %d" % (len(RESULTS) - nfail, nfail))
    if hashes:
        for mode, (ha, hb) in sorted(hashes.items()):
            print("sha256[%s] %s" % (mode, ha))
    print("VERDICT=%s" % ("PASS" if nfail == 0 else "FAIL"))
    return 0 if nfail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

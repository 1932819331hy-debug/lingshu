# -*- coding: utf-8 -*-
"""test_issue681_gap_dual_dist_key_guard · 选路键/读数与过滤同口径（#681）
==================================================================================
缺陷（#681，改前实测三例）：`GapDual.compute_d_task` 的**过滤面**用
`_finite_dist`（已转 float），**排序键与读数**却取原值 `p.get("dist")`
⇒ 两套口径：

  A  全字符串 ``{"a": "10", "b": "9"}`` ⇒ 改前 ``best='a'`` / ``d_task=10.0``
     （应按**数值**选 ``'b'`` / 9.0）；任何数值字符串 dist 都按**字典序**
     选路（``"10" < "9"`` 为真），而 ``d_task`` 转回浮点掩盖了错误。
  B  字符串与浮点混用**且 `_cond_gap` 平局**时 ⇒ 改前
     ``TypeError: '<' not supported between instances of 'float' and 'str'``
     穿透（缺口不同时不抛，触发面收窄到平局）。
  C  全浮点对照 ⇒ ``best='b'`` / ``d_task=9.0``（正确，改前后应逐位一致）。

修法：用 `_finite_dist` 的**结果**统一排序键与读数（`valid` 三元组携带
已转 float 的有限距离 `best_d`，排序键与 `d_task` 同取它）。

判据（本件）：
  G1 数值字符串按**数值**选路（改前 'a'/10.0 ⇒ 应 'b'/9.0）；
  G2 字符串+浮点平局**不再抛**，且按数值选路（两路同值，任取其一，读数=9.0）；
  G3 全浮点与改前逐位一致（'b'/9.0）；
  G4 防过修：#518 三病（缺 dist / NaN / None）仍不参与选路。
  G5 定点变异：把排序键/读数还原为原值 `p.get("dist")` ⇒ G1/G2 必红。

与别批守卫的关系（防代偿）：`tests/test_issue518_invalid_dist_paths.py` 钉
「无效 dist 不参选」，`tests/test_gap_dual_d_meta_guard.py` 钉 #124 口径；
本件钉「有效 dist 的排序/读数与过滤**同口径**」，判别面不相交。

运行（仓根）：python -X utf8 -m pytest tests/test_issue681_gap_dual_dist_key_guard.py -q
"""
from __future__ import annotations

from pathlib import Path

from lingshu.world.gap_dual import GapDual

SRC = Path(__file__).resolve().parents[1] / "lingshu" / "world" / "gap_dual.py"
NAN = float("nan")


def _mk(d):
    return {k: {"available": True, "dist": v, "cond_set": set()}
            for k, v in d.items()}


def _show(r):
    return {k: r.get(k) for k in ("d_task", "blindspot", "best_path", "cond_gap")}


def test_a_numeric_strings_selected_by_value():
    """G1：全字符串 ⇒ 按**数值**选路（改前 'a'/10.0，字典序错选）。"""
    r = GapDual().compute_d_task(_mk({"a": "10", "b": "9"}))
    assert r["best_path"] == "b" and r["d_task"] == 9.0, _show(r)


def test_b_string_float_tie_no_typeerror():
    """G2：字符串+浮点平局不再 TypeError，且按数值选路（读数=9.0）。"""
    r = GapDual().compute_d_task(_mk({"a": "9", "b": 9.0}))
    assert r["best_path"] in ("a", "b") and r["d_task"] == 9.0, _show(r)
    assert r["blindspot"] is False, _show(r)


def test_c_all_floats_unchanged():
    """G3：全浮点对照与改前逐位一致（'b'/9.0）。"""
    r = GapDual().compute_d_task(_mk({"a": 10.0, "b": 9.0}))
    assert r["best_path"] == "b" and r["d_task"] == 9.0, _show(r)


def test_d_518_three_ills_still_excluded():
    """G4（防过修）：#518 三病（缺 dist / NaN / None）仍不参与选路。"""
    g = GapDual()
    # 缺 dist / NaN / None 三条无效路径 + 一条有限路径 ⇒ 有限路径胜出
    paths = {"nodist": {"available": True, "cond_set": set()},
             "nan": {"available": True, "dist": NAN, "cond_set": set()},
             "none": {"available": True, "dist": None, "cond_set": set()},
             "ok": {"available": True, "dist": 1.0, "cond_set": set()}}
    r = g.compute_d_task(paths)
    assert r["best_path"] == "ok" and r["d_task"] == 1.0, _show(r)
    # 全无效面 ⇒ ∞ + BLINDSPOT
    r2 = g.compute_d_task(
        {"nodist": {"available": True, "cond_set": set()},
         "nan": {"available": True, "dist": NAN, "cond_set": set()},
         "none": {"available": True, "dist": None, "cond_set": set()}})
    assert r2["d_task"] == float("inf") and r2["blindspot"] is True, _show(r2)


def test_mutation_source_anchor_unified_key():
    """G5（形态锚·定点变异自证）：`valid` 三元组携带有限距离且排序键/读数同取它。

    还原为原值 `kv[1].get("dist", ...)` / `float(best.get("dist", ...))`
    ⇒ G1/G2 必红（两套口径回潮）。"""
    src = SRC.read_text(encoding="utf-8")
    assert 'valid, key=lambda kv: (_cond_gap(kv[1]), kv[2])' in src, (
        "#681：排序键应取 `_finite_dist` 的结果（valid 三元组的 kv[2]），"
        "不得还原为原值 p.get('dist')")
    assert "d_task = best_d" in src, (
        "#681：读数应取 `_finite_dist` 的结果（best_d），"
        "不得还原为 float(best.get('dist', inf))")


if __name__ == "__main__":
    import subprocess
    import sys
    raise SystemExit(subprocess.call(
        [sys.executable, "-X", "utf8", "-m", "pytest", __file__, "-q", "--no-header"]))

# -*- coding: utf-8 -*-
"""test_gap_dual_d_meta_guard · `gap_dual.py` P2 缺陷 #124 守卫
============================================================================
本文件钉住 `lingshu/world/gap_dual.py` 的 #124「不再存在」（不是「代码能跑」）。

#124（成立）`GapDual` 四处与自身声明不符：
  (1) D_meta 在无事件时**单调下降**，与模块头 :6 声明的
      `d(D_meta)/dt ≥ 0（结构性论证）` 方向相反：旧实现
      `self._last_meta = max(0.0, cred_blend(self._last_meta, growth,
                                             retain=1.0 - self.decay))`
      在「无干预」（growth=0）时按 0.9^n 几何衰减，10 步后只剩 34.87%。
      修法：取单调包络（估计值 = max(历史值, 平滑候选)）。
  (2) `state()["d_meta_trend"]` 旧判据 `_last_meta > 0.1` 与**方向**无关——
      读数下降时仍报 "growing"。修法：与上一读数比较判方向。
  (3) `compute_d_task` 选路只看 `dist`：注释 :73 承诺的「条件缺口惩罚」从未
      生效（完整覆盖任务条件的远路径输给缺条件的近路径）。修法：先比条件缺口、
      缺口相同再比 dist。
  (4) `best_path` 恒为空串（读 `best["id"]`，而 docstring 规定 id 在**键**上）。
      修法：取 path_id。
  (5) 可用路径缺 dist ⇒ `d_task=∞` 却 `blindspot=False`（:16/:44 规定 ∞ 即
      BLINDSPOT）。修法：`blindspot = not isfinite(d_task)`。
  判据来源：模块头 :12-13（D_task 的 v1.2 图上定义
  `D_task = min_{p 可用} dist(C_task, CondSet(p))`）、:6 与 :91 的
  `d(D_meta)/dt ≥ 0` 结构性论证；triage #124（判定成立）。

#456（成立，与 #159 同型）`update_meta` 的**第一个位置形参 `d_task` 在函数
  体内零引用**：签名声明它是必需输入、仓内调用点全部按位置传入，但 docstring
  的增长公式（盲区事件 + 新条件暴露 + 时间衰减）不含它，实现也确实不用——
  声明的输入与实现的输入不一致（公开契约不成立）。修复采路线 A（与模块头
  :4-10 的 D_task/D_meta 符号区分一致）：`d_task` 为调用面兼容保留，docstring
  显式记录为**有意无操作**（不静默吞掉名字，也不假装它生效——与
  `brain_store.BrainEngine.add_perception` 对 `skip_dedup` 的处理同一口径）。
  本文件把「偶然的静默」升为「被断言的声明」：C 组行为断言（d_task 取
  0.0/0.3/1.0/±1e6/±inf 读数逐位相同）+ 对照组（blindspot/
  new_conditions_seen 变化必须被检出——证明 C 组的「相同」不是探针失效）+
  AST 断言（`d_task` 在签名中且函数体零引用）。
  判据来源：issue #456（实测 7 值 10 步读数逐位相同）；模块头 :8-10
  （D_meta 不纳入 D_norm 数值计算，仅作结构性边界 ⇒ 与 D_task 解耦有意）。

定点变异自证（抽掉修复 ⇒ 必红，逐条对应）：
  还原 `self._last_meta = max(0.0, cred_blend(...))` ⇒ G1/G3 红（序列下降）。
  还原 `key=lambda p: p.get("dist", inf)` 且无缺口优先 ⇒ G4 红（选 p_close）。
  还原 `best.get("id", "")` ⇒ G5 红（best_path=""）。
  还原 `"blindspot": False` ⇒ G6 红（blindspot=False 而 d_task=inf）。
  把 `d_task` 接进 growth（如 `growth += d_task`）⇒ C1/C2 红（读数随 d_task 漂移）；
  从签名删掉 `d_task` ⇒ C1/C2 红（既有调用点按位置传参 ⇒ TypeError）+ C3 红。

与别批守卫的关系（防代偿）：`tests/test_time_core_lint.py` 的 EX5 只断言
`retain=1.0 - self.decay` 这一**片段存在**（核形态豁免点），不断言 D_meta 的
方向语义；本件断言方向与选路口径，两者判别面不相交，不存在「任一方生效即
通过」的代偿。

运行（lingshu 仓根）：
    python -X utf8 tests/test_gap_dual_d_meta_guard.py       # 脚本式，退出码 0/1
    python -X utf8 -m pytest tests/test_gap_dual_d_meta_guard.py -q --no-header
"""
from __future__ import annotations

import ast
import math
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.world.gap_dual import GapDual  # noqa: E402

_PASS, _FAIL = [], []


def ok(name, cond, detail=""):
    (_PASS if cond else _FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name
          + (("  <- " + str(detail)) if detail else ""))


# ==================== (1)(2) D_meta 方向契约 ====================

def group_d_meta_direction():
    print("\n#124-(1)(2)：D_meta 无事件时不得下降（d(D_meta)/dt ≥ 0）")
    g = GapDual()
    # 先注入一次盲区（growth=1.0），随后 10 步「无干预」（无盲区、无新条件）
    seq = [g.update_meta(0, True)] + [g.update_meta(0, False) for _ in range(10)]
    ok("#124-A1 事件后无干预序列单调不减",
       all(b >= a for a, b in zip(seq, seq[1:])),
       [round(x, 4) for x in seq])
    ok("#124-A2 无干预读数不下降（末值 ≥ 事件值）",
       seq[-1] >= seq[0] - 1e-12, (seq[0], seq[-1]))
    # 旧实现此处末值 = 1.0 * 0.9^10 = 0.3487（下降），本断言即钉住该形态
    ok("#124-A3 末值远高于旧实现的几何衰减值 0.3487",
       seq[-1] > 0.5, round(seq[-1], 4))

    # 全新实例、连续无事件：不得出现负值/下降
    g0 = GapDual()
    zero_seq = [g0.update_meta(0, False) for _ in range(5)]
    ok("#124-A4 无任何事件时恒为 0.0（不下降、不为负）",
       all(v == 0.0 for v in zero_seq), zero_seq)

    # 持续盲区：单调不减（有界收敛于 1/λ 属平滑核的既有形态，非本件判据）
    g2 = GapDual()
    blind = [g2.update_meta(0, True) for _ in range(30)]
    ok("#124-A5 持续盲区序列单调不减",
       all(b >= a for a, b in zip(blind, blind[1:])),
       (round(blind[0], 4), round(blind[-1], 4)))

    # trend 必须按方向：增长步报 growing，持平步报 stable（不再谎报 growing）
    g3 = GapDual()
    g3.update_meta(0, True)
    ok("#124-A6 增长步 trend=growing", g3.state()["d_meta_trend"] == "growing",
       g3.state())
    g3.update_meta(0, False)
    ok("#124-A7 持平步 trend=stable（旧码在下降/持平时仍报 growing）",
       g3.state()["d_meta_trend"] == "stable", g3.state())


# ==================== (3)(4)(5) D_task 选路 / 溯源 / blindspot ====================

def group_d_task_selection():
    print("#124-(3)(4)(5)：D_task 条件缺口优先 / best_path 可溯源 / ∞ 即 BLINDSPOT")
    # (3) 任务条件 {a,b}：p_far 完整覆盖、p_close 缺 b ⇒ 必须选 p_far
    paths = {"p_close": {"available": True, "dist": 0.5, "cond_set": {"a"}},
             "p_far": {"available": True, "dist": 2.0, "cond_set": {"a", "b"}}}
    r = GapDual().compute_d_task(paths, {"a", "b"})
    ok("#124-B1 条件覆盖完整的路径胜出（旧码只按 dist 选 p_close）",
       r["best_path"] == "p_far", r)
    ok("#124-B2 d_task 为胜出路径的距离（旧码报 0.5）",
       r["d_task"] == 2.0, r["d_task"])
    ok("#124-B3 cond_gap 为 0（胜出路径完整覆盖）", r["cond_gap"] == 0, r)

    # (4) best_path 必须是键（旧码读值里的 "id" ⇒ 恒空串）
    r2 = GapDual().compute_d_task(
        {"only": {"available": True, "dist": 1.5, "cond_set": {"a"}}}, {"a"})
    ok("#124-B4 best_path 非空且等于路径 id", r2["best_path"] == "only", r2)

    # 缺口相同时退回 dist 比较（不因修复而丢掉距离口径）
    paths3 = {"far": {"available": True, "dist": 3.0, "cond_set": {"a"}},
              "near": {"available": True, "dist": 1.0, "cond_set": {"a"}}}
    r3 = GapDual().compute_d_task(paths3, {"a"})
    ok("#124-B5 缺口相同 ⇒ 按 dist 取小（near）", r3["best_path"] == "near", r3)

    # (5) 可用路径缺 dist ⇒ d_task=∞ 必须判 BLINDSPOT
    r4 = GapDual().compute_d_task(
        {"p": {"available": True, "cond_set": {"a"}}}, {"a"})
    ok("#124-B6 缺 dist ⇒ d_task=inf", math.isinf(r4["d_task"]), r4["d_task"])
    ok("#124-B7 缺 dist ⇒ blindspot=True（旧码恒 False）",
       r4["blindspot"] is True, r4)
    # 正常路径仍不得误判盲区
    r5 = GapDual().compute_d_task(
        {"p": {"available": True, "dist": 0.7, "cond_set": {"a"}}}, {"a"})
    ok("#124-B8 正常路径 blindspot=False（不误判）",
       r5["blindspot"] is False and r5["d_task"] == 0.7, r5)


# ==================== #456 d_task 有意无操作（声明的契约） ====================

def group_d_task_inert():
    print("#456：update_meta 的 d_task 对 D_meta 更新零作用（有意解耦，被断言的声明）")
    STEPS = 10

    def run(d_task, blindspot, cond=0):
        g = GapDual()
        return [g.update_meta(d_task, blindspot, cond) for _ in range(STEPS)]

    # C1 行为面：d_task 取值不变性（含极端值 ±1e6/±inf ⇒ 读数逐位相同）
    base = run(0.0, True)
    for dt in (0.3, 1.0, 1e6, -1e6, float("inf"), float("-inf")):
        got = run(dt, True)
        ok("#456-C1 d_task=%r 读数与 0.0 逐位相同" % dt, got == base,
           [round(v, 6) for v in got])

    # C2 行为面：无事件序列同样不受 d_task 影响（0 读数面并非唯一探针）
    zero_base = run(0.0, False)
    ok("#456-C2 无事件序列 d_task=1e6 与 0.0 逐位相同",
       run(1e6, False) == zero_base, zero_base)

    # C3 对照组：另外两个形参的变化必须被检出（证明 C1/C2 的「相同」非探针失效）
    ok("#456-C3a blindspot 变化被检出（读数不同）",
       run(0.0, True) != run(0.0, False))
    ok("#456-C3b new_conditions_seen 变化被检出（读数不同）",
       run(0.0, False, 3) != zero_base)

    # C4 静态面：d_task 在签名中（调用面兼容保留）且函数体零引用（有意无操作）
    src = os.path.join(REPO, "lingshu", "world", "gap_dual.py")
    fn = next(n for n in ast.walk(ast.parse(open(src, encoding="utf-8").read()))
              if isinstance(n, ast.FunctionDef) and n.name == "update_meta")
    params = [a.arg for a in fn.args.args]
    loads = {x.id for x in ast.walk(fn)
             if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)}
    ok("#456-C4a d_task 仍在签名中（self 后首位，不静默吞掉名字）",
       params[:2] == ["self", "d_task"], params)
    ok("#456-C4b d_task 函数体零引用（有意无操作，非假装生效）",
       "d_task" not in loads, sorted(loads))
    # C5 文档面：docstring 必须显式声明 d_task 为无操作（契约不再互相矛盾）
    doc = ast.get_docstring(fn) or ""
    ok("#456-C5 docstring 显式记录 d_task 无操作", "d_task" in doc
       and "不使用" in doc, "docstring 首段含 `d_task`：**本方法不使用**")


def run_checks():
    _PASS.clear(); _FAIL.clear()
    group_d_meta_direction()
    group_d_task_selection()
    group_d_task_inert()
    print()
    print("===== SUMMARY %d/%d 通过 =====" % (len(_PASS), len(_PASS) + len(_FAIL)))
    if _FAIL:
        print("FAILS:", _FAIL)
        return 1
    print("VERDICT=PASS（#124 D_meta 单调不减 + trend 按方向 + 条件缺口选路 + "
          "best_path 可溯源 + ∞ 即 BLINDSPOT；#456 d_task 有意无操作被断言）")
    return 0


def test_gap_dual_d_meta_guard():
    """pytest 入口（脚本/pytest 双模式，与仓内同惯例）。"""
    assert run_checks() == 0


if __name__ == "__main__":
    sys.exit(run_checks())

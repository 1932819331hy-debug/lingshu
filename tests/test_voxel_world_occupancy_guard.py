# -*- coding: utf-8 -*-
"""test_voxel_world_occupancy_guard · `voxel_world.occupancy_at` P2 缺陷 #134 守卫
============================================================================
本文件钉住 `lingshu/world/voxel_world.py` 的 #134「不再存在」（不是「代码能跑」）。

#134（成立）`occupancy_at(t)` 按**列表下标**取轨迹点而非按**时间步**：
  · `move_entity(record=True)` 追加轨迹点却**不推进 `_step`** ⇒ 同一 t 多个点；
  · `spawn_entity` 在 `_step>0` 时才生成 ⇒ 首个点的 t 就是当时的 step，
    但下标仍是 0。
  旧实现 `if t < len(trail): occ.append({"entity": eid, **trail[t]})` 于是：
    - 晚生成的实体出现在它**尚不存在**的时刻（t=0 就报出 t=3 的点）；
    - 它**实际在场**的时刻（t=3/4/5）反而缺席；
    - 返回项里的 `t` 与请求的 `t` 不一致。
  修法：按点内 `t` 字段查找；同一 t 多点取最后一个（该时刻最终状态）。
  判据来源：triage #134（判定成立）——判据即「返回的每一项 `o["t"] == t`，
  且 t 时刻在场的实体都出现」。

与既有守卫的关系（防代偿）：`tests/test_voxel_world_bounds.py` 只钉 size/steps/
trail 三条**资源上界**（#325/#368），不断言 `occupancy_at` 的取值口径；
本件断言按时间步的取值正确性，两者判别面不相交。

定点变异自证（抽掉修复 ⇒ 必红，逐条对应）：
  还原 `if t < len(trail): occ.append({"entity": eid, **trail[t]})`
  ⇒ G1 红（dog 出现在 t=0）、G2 红（t=3 时 dog 缺席）。

运行（lingshu 仓根）：
    python -X utf8 tests/test_voxel_world_occupancy_guard.py       # 脚本式，退出码 0/1
    python -X utf8 -m pytest tests/test_voxel_world_occupancy_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.world.voxel_world import VoxelWorld  # noqa: E402

_PASS, _FAIL = [], []


def ok(name, cond, detail=""):
    (_PASS if cond else _FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name
          + (("  <- " + str(detail)) if detail else ""))


def _late_spawn_world():
    """复刻 triage #134 的最小场景：a 先跑 3 步，b 才生成，再跑 2 步。"""
    w = VoxelWorld(size=16)
    a = w.spawn_entity("ball", (1, 2, 1), (1, 0, 0))
    w.simulate(3)                              # a 走到 _step=3
    b = w.spawn_entity("dog", (10, 2, 10), (0, 0, 1))  # 首个点 t=3
    w.simulate(2)                              # 到 _step=5
    return w, a, b


def group_occupancy_by_time_step():
    print("\n#134：occupancy_at(t) 必须按时间步取值（t 字段），而非列表下标")
    w, a, b = _late_spawn_world()

    # G1：晚生成的实体不得出现在它尚不存在的时刻
    occ0 = w.occupancy_at(0)
    ok("#134-G1 t=0 不含晚生成实体 b（旧码报出 t=3 的点）",
       all(o["entity"] != b for o in occ0), occ0)

    # G2：b 实际在场的时刻必须出现（旧码 t=3/4/5 反而缺席）
    present = {t: [o["entity"] for o in w.occupancy_at(t)] for t in (3, 4, 5)}
    ok("#134-G2 b 在其在场时刻 t=3/4/5 均出现",
       all(b in present[t] for t in (3, 4, 5)), present)

    # G3：返回项的 t 必须等于请求的 t（旧码下标口径下逐点错位）
    bad = [(t, o) for t in range(6) for o in w.occupancy_at(t) if o["t"] != t]
    ok("#134-G3 所有返回项的 o['t'] == 请求 t", not bad, bad)

    # G4：t=0/1/2 时只有 a（b 尚未生成）
    ok("#134-G4 t=0/1/2 仅 a 在场",
       all([o["entity"] for o in w.occupancy_at(t)] == [a]
           for t in (0, 1, 2)),
       {t: [o["entity"] for o in w.occupancy_at(t)] for t in (0, 1, 2)})

    # G5：越界/不存在的时刻返回空（不得回错位点）
    ok("#134-G5 t=99 返回空列表", w.occupancy_at(99) == [], w.occupancy_at(99))


def group_duplicate_t_resolution():
    print("#134：move_entity 不推进 _step ⇒ 同 t 多点，取该时刻最终状态")
    w = VoxelWorld(size=16)
    a = w.spawn_entity("ball", (1, 2, 1), (1, 0, 0))
    w.simulate(3)
    w.move_entity(a, (0.5, 2.0, 0.5))          # 追加 t=3 的第二个点
    occ = w.occupancy_at(3)
    ok("#134-G6 同 t 多点时返回项仍满足 o['t']==3",
       occ and all(o["t"] == 3 for o in occ), occ)
    ok("#134-G7 同 t 取最后一个点（最终状态 0.5,2,0.5）",
       len(occ) == 1 and occ[0]["pos"] == (0.5, 2.0, 0.5), occ)


def run_checks():
    _PASS.clear(); _FAIL.clear()
    group_occupancy_by_time_step()
    group_duplicate_t_resolution()
    print()
    print("===== SUMMARY %d/%d 通过 =====" % (len(_PASS), len(_PASS) + len(_FAIL)))
    if _FAIL:
        print("FAILS:", _FAIL)
        return 1
    print("VERDICT=PASS（#134 occupancy_at 按时间步取值：晚生成实体不错位、"
          "在场时刻不缺席、o['t']==t）")
    return 0


def test_voxel_world_occupancy_guard():
    """pytest 入口（脚本/pytest 双模式，与仓内同惯例）。"""
    assert run_checks() == 0


if __name__ == "__main__":
    sys.exit(run_checks())

# -*- coding: utf-8 -*-
"""test_issue719_trail_copy_guard · `voxel_world.trail()` 不得交回内部列表（#719）

缺陷（P2 · `lingshu/world/voxel_world.py`）
-------------------------------------------------------------------------------
    def trail(self, entity_id: str) -> List[Dict]:
        return self._trails.get(entity_id, [])

只读查询接口返回**内部列表本体** ⇒ 调用方（本仓门面 `core.py::voxel_world`
的 `trail` 动作直接把它放进返回值；外部亦同）可原地篡改时空真值：

  · `trail(eid).clear()` ⇒ 该实体历史被抹掉，`occupancy_at(t)` 随之返回空
    （`#134` 修复后 `occupancy_at` 已是时空真值来源）；`world_state()["trails"]`
    的计数同步归零 —— 三者都是**可观测**的对外读数；
  · `trail(eid).append({"t": 999, ...})` ⇒ 凭空捏造任意时刻的占用点。

两条路径都不经任何写接口（`move_entity` / `simulate` / `_record_trail`），
无留痕、无上界校验（`MAX_TRAIL` 裁剪也只在 `_record_trail` 里做）。

消费者读数（`git grep`）：全仓 `.trail(` 只有两处——`core.py` 门面（把结果放进
返回值 JSON）与既有测试（只读 `len(...)`）。**无任何调用方**依赖「拿到同一个
list 对象后原地改它」⇒ 返回拷贝不破坏既有语义。

修法（最小 · 照同族口径）
-------------------------------------------------------------------------------
    - return self._trails.get(entity_id, [])
    + return list(self._trails.get(entity_id, []))

与同族只读查询同口径（`spacetime_consistency.drift_events()` /
`prediction_history()` 都返回 `list(...)` 拷贝）；**不加** `copy.deepcopy`
（无调用方需要，且轨迹点 dict 本身在本仓无外部原地改写路径）。

断言组（抽掉修复 ⇒ 按标注转红）
-------------------------------------------------------------------------------
  A **不是同一对象**：`trail(eid) is w._trails[eid]` 必须为 False（旧码恒 True）
  B **clear() 无效**：对返回值 `clear()` 后，内部长度 / `occupancy_at(t)` /
    `world_state()["trails"]` 三者读数**全部不变**（旧码三者齐变）
  C **append() 无效**：对返回值 `append(捏造点)` 后，`occupancy_at(999)` 仍为空、
    内部长度不变（旧码可捏造）
  D **正对照·内容一致**：未篡改时返回值与内部列表**逐元素相同**（拷贝不丢内容，
    且长度 = 轨迹点数）——量具既非恒空也非恒等
  E **同族口径对照**：`spacetime_consistency` 的两个只读查询同样返回拷贝
    （证明本修法取的是仓内既有口径，而非新造风格）

运行（仓根）：python -X utf8 -m pytest tests/test_issue719_trail_copy_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.world.voxel_world import VoxelWorld  # noqa: E402

STEPS = 5
SPAWN = (5.0, 1.5, 5.0)


def _world():
    """平地世界 + 1 个零速度实体 + 推进 5 步（轨迹点 t=0..5，共 6 个）。"""
    w = VoxelWorld(size=16)
    w.build_flatland(trees=0, water=False)
    eid = w.spawn_entity("actor", SPAWN, velocity=(0.0, 0.0, 0.0))
    w.simulate(STEPS)
    tr = w.trail(eid)
    assert len(tr) == STEPS + 1, \
        "前置失败：轨迹点数应为首点 + %d 步 = %d，实得 %d" % (
            STEPS, STEPS + 1, len(tr))
    assert w.occupancy_at(STEPS), "前置失败：末刻占用不应为空"
    return w, eid


def test_a_trail_is_not_the_internal_list():
    """A：`trail()` 不得返回 `_trails[eid]` 本体。"""
    w, eid = _world()
    assert w.trail(eid) is not w._trails[eid], \
        "【#719】trail() 返回内部列表本体（调用方可原地篡改时空真值）"
    assert w.trail(eid) == w._trails[eid], \
        "【#719】返回值应与内部列表内容一致（拷贝而非另造）"


def test_b_clear_on_returned_list_is_ineffective():
    """B：对返回值 `clear()` 不得影响内部状态（三处对外读数全不变）。"""
    w, eid = _world()
    before_len = len(w.trail(eid))
    before_occ = len(w.occupancy_at(STEPS))
    before_count = w.world_state()["trails"][eid]
    assert before_len > 0 and before_occ > 0 and before_count > 0, \
        "前置失败：读数有零值（量具空转）"
    w.trail(eid).clear()
    assert len(w.trail(eid)) == before_len, \
        "【#719】clear() 抹掉了内部轨迹（长度 %d → %d）" % (before_len, len(w.trail(eid)))
    assert len(w.occupancy_at(STEPS)) == before_occ, \
        "【#719】clear() 抹掉了 occupancy_at(%d) 的时空占用" % STEPS
    assert w.world_state()["trails"][eid] == before_count, \
        "【#719】clear() 改掉了 world_state()['trails'] 的计数"


def test_c_append_on_returned_list_is_ineffective():
    """C：对返回值 `append()` 不得向内部轨迹灌入捏造点。"""
    w, eid = _world()
    before_len = len(w.trail(eid))
    w.trail(eid).append({"t": 999, "pos": (1.0, 1.5, 1.0), "category": "actor"})
    assert w.occupancy_at(999) == [], \
        "【#719】可凭空捏造 t=999 的占用点（历史被伪造）"
    assert len(w.trail(eid)) == before_len, \
        "【#719】append() 灌进了内部轨迹（长度 %d → %d）" % (before_len, len(w.trail(eid)))
    assert w.world_state()["trails"][eid] == before_len, \
        "【#719】world_state()['trails'] 计数被 append 改写"


def test_d_positive_control_content_preserved():
    """D（正对照）：拷贝不丢内容——逐元素相同，且能读到真实轨迹点。"""
    w, eid = _world()
    tr = w.trail(eid)
    assert len(tr) == STEPS + 1, "正对照：拷贝长度应等于轨迹点数: %d" % len(tr)
    assert tr == w._trails[eid], "正对照：拷贝内容应与内部列表逐元素相同"
    ts = [p["t"] for p in tr]
    assert ts == list(range(STEPS + 1)), "正对照：轨迹点时间戳应为 0..%d: %r" % (STEPS, ts)
    # 未知实体：返回空列表（既有契约不变）
    assert w.trail("不存在") == [], "正对照：未知实体应返回空列表"


def test_e_same_family_queries_also_return_copies():
    """E：同族只读查询（spacetime_consistency）亦返回拷贝 —— 口径一致。"""
    from lingshu.world.spacetime_consistency import SpacetimeConsistency
    sp = SpacetimeConsistency(size=24)
    sp.scene.create_scene(trees=0, water=False)
    sp.add_entity("actor", behavior="wander", pos=SPAWN, speed=0.3)
    sp.run(3)
    assert sp.drift_events() is not sp._drift_events, \
        "同族口径：drift_events() 应返回拷贝"
    assert sp.prediction_history(2) is not sp._results, \
        "同族口径：prediction_history() 应返回拷贝"
    assert len(sp.prediction_history(2)) == 2, "同族口径：prediction_history 应可读"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "--no-header"]))

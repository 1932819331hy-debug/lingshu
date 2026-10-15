# -*- coding: utf-8 -*-
"""守卫 · issue #704 —— `Environment.gravity` 零重力入口不可达（本层收下、内核抛错）
============================================================================
判据来源（issue #704）：
  `Environment(gravity=0.0)` 被本层接受、类 docstring 也把 `0` 写成可用模式
  （「`0` = 失重，用于隔离阻力」），但只要场景里有 `attrs["physics"] == "dynamics"`
  的实体，首个 `step()` 就抛 `PhysicsError: gravity 须 > 0，得到 0.0` —— 即
  「本层收下、内核抛错」的分裂。

取向 A 的依据（本守卫锁的就是这条裁断）：
  内核 `physics.make_environment` 要求 `gravity > 0`，且 `_positive_num(gravity, ...)`
  在 `physics.py` 有 **8 处**调用点；`drag2_truth` 的闭式真值以
  `v_t = sqrt(g/beta)` 为参数 —— `g = 0` 时 `v_t = 0`、可达性闸门
  `abs(v0) >= vt ⇒ raise` 使**任何状态（含 v0=0）都被拒**，`u0 = atanh(v0/vt)`
  同时退化。⇒ `gravity > 0` 是**内核的域不变量**，不是一处的笔误；放宽内核
  （取向 B）＝改物理语义的大面改动。故本层与内核**同向拒 `0`**（取向 A），
  并把 docstring 里那句不可达的承诺改掉。

本守卫的四条判据：
  ① `Environment(gravity=0.0)` **抛 `ValueError`**（本层与内核同向）；
  ② `Environment(gravity=-1.0)` / `float("nan")` / `float("inf")` 同样抛；
  ③ **极小正数仍通过**（`1e-09` 构造 OK 且 `to_physics_env()` OK）——锁住
     「不是把整条零附近都封死」；
  ④ **场景层 fail-closed**：`SceneSimulator.set_environment(gravity=0.0)` 抛
     `ValueError` 且**环境未被污染**（`to_dict()` 前后逐位相同）；
     `set_entity_environment(eid, gravity=0.0)` 同样抛且**不写入**。

运行（仓根）：
    python3 -X utf8 -m pytest tests/test_issue704_environment_gravity_positive_guard.py -q
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import pytest  # noqa: E402

from lingshu.world.scene_simulator import Environment, SceneSimulator  # noqa: E402


# ===========================================================================
# ① 本层与内核同向：gravity = 0 必须被拒
# ===========================================================================


def test_1_gravity_zero_rejected_by_layer():
    """#704 核心判据：`Environment(gravity=0.0)` 必须抛 `ValueError`（不再收下）。"""
    with pytest.raises(ValueError) as err:
        Environment(gravity=0.0)
    msg = str(err.value)
    assert "gravity" in msg and "> 0" in msg, msg


def test_2_nonpositive_and_nonfinite_rejected():
    """② `-1.0` / `nan` / `inf` 同样抛（非有限由有限性闸门拦，非正由下界闸门拦）。"""
    for bad in (-1.0, float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            Environment(gravity=bad)


def test_3_tiny_positive_still_accepted():
    """③ 极小正数仍通过：锁住「不是把整条零附近都封死」（1e-09 两侧都通过）。"""
    e = Environment(gravity=1e-09)
    assert e.gravity == 1e-09
    env = e.to_physics_env()
    assert env["gravity"] == 1e-09, env


# ===========================================================================
# ④ 场景层 fail-closed：登记期即拒，且不污染既有环境 / 不写入实体
# ===========================================================================


def test_4_scene_set_environment_zero_is_fail_closed():
    """④ `set_environment(gravity=0.0)` 抛 `ValueError` 且**环境逐位不变**。"""
    s = SceneSimulator(size=8, seed=1)
    before = s.environment.to_dict()
    with pytest.raises(ValueError):
        s.set_environment(gravity=0.0)
    assert s.environment.to_dict() == before, (
        "被拒的 gravity=0.0 登记污染了环境：%r" % (s.environment.to_dict(),))


def test_5_scene_set_entity_environment_zero_is_fail_closed():
    """④ `set_entity_environment(eid, gravity=0.0)` 抛 `ValueError` 且**不写入**。"""
    s = SceneSimulator(size=8, seed=1)
    eid = s.add_entity("a", "wander", (2.0, 1.5, 2.0), 0.5)
    assert "environment" not in s.entities[eid].attrs
    with pytest.raises(ValueError):
        s.set_entity_environment(eid, gravity=0.0)
    assert "environment" not in s.entities[eid].attrs, (
        "被拒的每实体 gravity=0.0 覆盖竟写进了实体：%r"
        % (s.entities[eid].attrs.get("environment"),))


def test_6_default_environment_unchanged():
    """防过修：缺省 `Environment()` 仍取内核同源标准重力，行为逐位不变。"""
    from lingshu.world import physics
    d = SceneSimulator(size=8, seed=1).environment.to_dict()
    assert d["gravity"] == physics.DEFAULT_G, (d["gravity"], physics.DEFAULT_G)
    assert d["gravity"] > 0

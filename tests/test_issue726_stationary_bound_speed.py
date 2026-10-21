# -*- coding: utf-8 -*-
"""test_issue726_stationary_bound_speed · `_prediction_bound` 静止分支丢弃速度分量（#726）

缺陷（#726，改前实测复现）：`_prediction_bound` 的静止捷径
（`if self._is_stationary(eid): return self.hit_threshold * (1 + age/window)`）
**不读 `speed_est`**，把「最近两帧位置未变」当成「学得速度可忽略」。于是
「高动量但恰在观测间隙停住」的实体边界被压到 `hit_threshold`：

  · 实测 speed_est=3.49 ⇒ `_prediction_bound`=0.5，而同模型 `predict()` 对同实体
    给 1.896（同一实体两处「预测不确定度」差 3.79×）；
  · 该实体一旦恢复运动，真误差 3.49 > 0.5 ⇒ 边界不再是上界；
  · IG（= bound × 新奇 × 瓶颈 × 陈旧 × 异常）被系统性低估 ⇒ L7 好奇资源错配。

判据（本件）：
  G1 静止读数实体的 `_prediction_bound` 必须随**学得速度**上升（改前对
     `speed_est` 完全不敏感），且不低于本模型自己的 `_reach(speed_est)`；
  G2 「静止时收窄」的初衷保留：真静止（`speed_est≈0`）读数与旧口径
     `hit_threshold * (1 + age/window)` **逐位相同**（含 age>0）；
  G3 上界性质：自然轨迹扫描里，**静止读数样本**上 `err <= bound` 全成立
     （改前 30 个静止样本中 2 个违规：err 3.49 > bound 0.5）；
  G4 陈旧度放大在静止分支上仍生效（age>0 ⇒ bound 线性放大）；
  G5 正对照：同探针测**非静止**实体 ⇒ bound 严格随 `speed_est` 上升
     （证明量具有效：既非恒真也非恒假）。

判据来源：issue #726 正文（实测读数 + 建议修法「静止分支至少读一次 `speed_est`
参与」）；实现侧口径取 `WorldLearner._reach`（`max(hit_threshold, speed*1.5+pad)`，
**自带 hit_threshold 下界** ⇒ 学得速度为 0 时与旧 `hit_threshold` 同值，故 G2 的
「逐位相同」可判）。「上界」取与 `_count_anomalies` / `eval_phase` 同源的口径：
`err = dist(predict(horizon=1)["predicted"], 下一 tick 的新鲜观测位置)`。
G3 只对**静止读数样本**断言（非静止样本的覆盖率由主分支既有口径负责，不属本条）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue726_stationary_bound_speed.py -q
"""
from __future__ import annotations

import math

from lingshu.world.curiosity_explorer import CuriosityExplorer


def _follow_scene(ex, speed=8.0, goal=(20.0, 1.5, 20.0), start=(2.0, 1.5, 2.0)):
    """单点路径 follow 实体：冲向目标点后因 `min(speed, dist)` 步长封顶停住。

    ⇒ 最近两帧位置相同（`_is_stationary`=True）而窗口里仍留着抵达前的高速位移
    （`speed_est` 高）——正是 #726 的「高动量但未动」形态，全程走真实调用路径、
    不注入任何被测函数。
    """
    ex.world.create_scene(trees=0, water=False)
    ex.world.add_path("p1", [tuple(float(v) for v in goal)])
    return ex.world.add_entity("st", behavior="follow",
                               pos=tuple(float(v) for v in start),
                               speed=speed, goal="p1")


def _drive(ex, ticks, entities=None):
    for _ in range(ticks):
        ex.world.step(n=1)
        ex.observe(entities=entities)


def _high_momentum_stationary(speed=8.0, window=6):
    """改前复现构型：静止读数 + 高 speed_est。返回 (ex, eid)。"""
    ex = CuriosityExplorer(size=40, ground_level=1, seed=7, window=window)
    eid = _follow_scene(ex, speed=speed)
    _drive(ex, 6)
    ex.learn()
    return ex, eid


# ---------------------------------------------------------------------------
# G1 —— 静止分支必须读学得速度
# ---------------------------------------------------------------------------

def test_726_stationary_bound_reads_learned_speed():
    """G1：静止读数实体的 bound 随 speed_est 上升，且 >= _reach(speed_est)。

    改前：静止分支 `return hit_threshold * (1 + age/window)` ⇒ 三个不同 speed_est
    下读数**逐位相同**（本条必红）。
    """
    ex, eid = _high_momentum_stationary()
    assert ex.world.entities and ex.nodes, "硬性前提：世界与节点非空"
    assert ex._is_stationary(eid) is True, (
        "构造前提失败：%s 应为静止读数（最近两帧位置未变）" % eid)
    learned = ex.model["per_entity"][eid]["speed_est"]
    assert learned > 0.5, "构造前提失败：高动量实体应学到显著速度，实为 %r" % learned

    readings = {}
    for s in (0.0, 2.0, 10.0):
        ex.model["per_entity"][eid]["speed_est"] = s
        readings[s] = ex._prediction_bound(eid)
    assert readings[0.0] < readings[2.0] < readings[10.0], (
        "#726 静止分支未读 speed_est：0/2/10 三档读数 %r（改前三者逐位相同）"
        % (readings,))

    ex.model["per_entity"][eid]["speed_est"] = learned
    bound = ex._prediction_bound(eid)
    assert bound >= ex._reach(learned) - 1e-9, (
        "#726 静止读数实体的 bound=%r 低于本模型可达域 _reach(%r)=%r"
        % (bound, learned, ex._reach(learned)))
    assert bound > ex.hit_threshold, (
        "#726 高动量静止实体的 bound 仍被压到 hit_threshold=%r（bound=%r）"
        % (ex.hit_threshold, bound))


# ---------------------------------------------------------------------------
# G2 —— 真静止仍收窄到旧口径（防过修）
# ---------------------------------------------------------------------------

def test_726_true_stationary_keeps_old_narrow_bound():
    """G2：学得速度 ≈ 0 的真静止实体，读数与旧口径逐位相同（含 age>0）。"""
    ex = CuriosityExplorer(size=24, ground_level=1, seed=7, window=6)
    ex.world.create_scene(trees=0, water=False)
    eid = ex.world.add_entity("rock", behavior="wander", pos=(5.0, 1.5, 5.0),
                              speed=0.0)
    _drive(ex, 6)
    ex.learn()
    assert ex.world.entities, "硬性前提：世界非空"
    assert ex._is_stationary(eid) is True, "构造前提失败：speed=0 实体应为静止读数"
    assert ex.model["per_entity"][eid]["speed_est"] == 0.0, (
        "构造前提失败：speed=0 实体应学到 0 速度，实为 %r"
        % ex.model["per_entity"][eid]["speed_est"])

    age0 = max(0, ex.tick - ex.last_observed.get(eid, ex.tick))
    assert ex._prediction_bound(eid) == ex.hit_threshold * (
        1 + age0 / float(ex.window)), (
        "#726 真静止（speed_est=0）读数偏离旧口径：%r vs %r"
        % (ex._prediction_bound(eid),
           ex.hit_threshold * (1 + age0 / float(ex.window))))

    _drive(ex, 6, entities=[])          # 之后不再观测它 ⇒ age>0
    ex.learn()
    assert ex.model["per_entity"][eid]["speed_est"] == 0.0, (
        "构造前提失败：不观测期间真静止实体速度估计应仍为 0，实为 %r"
        % ex.model["per_entity"][eid]["speed_est"])
    age = max(0, ex.tick - ex.last_observed.get(eid, ex.tick))
    assert age > 0, "构造前提失败：age 应 > 0，实为 %r" % age
    expected = ex.hit_threshold * (1 + age / float(ex.window))
    got = ex._prediction_bound(eid)
    assert abs(got - expected) < 1e-9, (
        "#726 age>0 的真静止读数偏离旧口径：%r vs %r（age=%d）"
        % (got, expected, age))


# ---------------------------------------------------------------------------
# G3 —— 上界性质（自然轨迹，静止读数样本）
# ---------------------------------------------------------------------------

def _pursuit_plus_high_momentum(ticks=30):
    """追逐链 + 高动量 follow 实体：返回 (静止样本数, 违规样本清单)。"""
    ex = CuriosityExplorer(size=24, ground_level=1, seed=42, window=6)
    ex.world.create_scene(trees=2, water=False)
    ex.world.add_entity("player", behavior="wander", pos=(2, 1.5, 2), speed=0.5)
    ex.world.add_entity("wolf", behavior="seek", pos=(15, 1.5, 15), speed=0.6,
                        goal=next(iter(ex.world.entities)))
    ex.world.add_entity("rabbit", behavior="flee", pos=(10, 1.5, 10), speed=0.5,
                        goal=[k for k, v in ex.world.entities.items()
                              if v.category == "wolf"][0])
    ex.world.add_path("p1", [(20.0, 1.5, 20.0)])
    ex.world.add_entity("st", behavior="follow", pos=(2.0, 1.5, 2.0),
                        speed=8.0, goal="p1")
    ex.observe()
    for _ in range(6):
        ex.world.step(n=1)
        ex.observe()
    stationary_samples = 0
    violations = []
    for _ in range(ticks):
        ex.learn()
        lp = ex.predict(horizon=1)
        bound = {e: ex._prediction_bound(e) for e in ex.nodes}
        stat = {e: ex._is_stationary(e) for e in ex.nodes}
        ex.world.step(n=1)
        ex.observe()
        for e, p in lp["predictions"].items():
            truth = ex.history[-1]["entities"][e]["pos"]
            err = math.dist(p["predicted"], truth)
            if not stat[e]:
                continue
            stationary_samples += 1
            if err > bound[e] + 1e-9:
                violations.append({"eid": e, "err": round(err, 4),
                                   "bound": bound[e]})
    return stationary_samples, violations


def test_726_stationary_samples_never_violate_upper_bound():
    """G3：静止读数样本上 `err <= bound` 全成立（改前 2 个违规：err 3.49 > 0.5）。"""
    samples, violations = _pursuit_plus_high_momentum()
    assert samples > 0, (
        "#726 守卫空转防护：扫描里必须真的出现静止读数样本（实为 0）")
    assert not violations, (
        "#726 静止读数样本上边界不再是上界：%d 个违规 %r（改前 err 3.49 > bound 0.5）"
        % (len(violations), violations[:5]))


# ---------------------------------------------------------------------------
# G4 —— 陈旧度放大在静止分支仍生效
# ---------------------------------------------------------------------------

def test_726_staleness_amplification_applies_on_stationary_branch():
    """G4：静止分支的 bound 仍按 (1 + age/window) 放大（防过修把该项删掉）。"""
    ex, eid = _high_momentum_stationary()
    fresh = ex._prediction_bound(eid)
    for _ in range(6):
        ex.world.step(n=1)
        ex.observe(entities=[])
    ex.learn()
    age = max(0, ex.tick - ex.last_observed.get(eid, ex.tick))
    assert age == 6, "构造前提失败：age 应为 6，实为 %r" % age
    stale = ex._prediction_bound(eid)
    ratio = stale / fresh
    assert abs(ratio - (1 + age / float(ex.window))) < 0.02, (
        "#726 静止分支陈旧度放大比 %.4f，应 ≈ %.4f" % (ratio, 1 + age / float(ex.window)))
    assert ratio > 1.5, "#726 陈旧度放大必须真实生效（ratio=%r）" % ratio


# ---------------------------------------------------------------------------
# G5 —— 正对照：量具对非静止实体同样有效
# ---------------------------------------------------------------------------

def test_726_control_nonstationary_bound_tracks_speed():
    """G5（正对照）：非静止实体的 bound 严格随 speed_est 上升且 == predict bound。

    证明 G1 的量具本身有判别力（不是恒真），且本修法未动主分支。
    """
    ex = CuriosityExplorer(size=40, ground_level=1, seed=7, window=6)
    ex.world.create_scene(trees=0, water=False)
    ex.world.add_path("p1", [(38.0, 1.5, 2.0)])
    eid = ex.world.add_entity("mv", behavior="follow", pos=(2.0, 1.5, 2.0),
                              speed=1.0, goal="p1")
    _drive(ex, 3)                       # 仍在移动（未抵达）
    ex.learn()
    assert ex.world.entities and ex.nodes, "硬性前提：世界与节点非空"
    assert ex._is_stationary(eid) is False, (
        "构造前提失败：移动中的实体不应被判静止")

    readings = []
    for s in (0.5, 2.0, 10.0):
        ex.model["per_entity"][eid]["speed_est"] = s
        pb = ex._prediction_bound(eid)
        pbound = ex.predict()["predictions"][eid]["bound"]
        readings.append(pb)
        assert pb == pbound, (
            "#726 主分支上 _prediction_bound 与 predict bound 应同源：%r vs %r"
            % (pb, pbound))
    assert readings[0] < readings[1] < readings[2], (
        "#726 正对照失败：非静止实体的 bound 未随 speed_est 上升 ⇒ 量具无判别力 %r"
        % (readings,))


if __name__ == "__main__":
    import subprocess
    import sys
    raise SystemExit(subprocess.call(
        [sys.executable, "-X", "utf8", "-m", "pytest",
         __file__, "-q", "--no-header"]))

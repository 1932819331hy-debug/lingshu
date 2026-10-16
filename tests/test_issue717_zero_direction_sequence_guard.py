# -*- coding: utf-8 -*-
"""test_issue717_zero_direction_sequence_guard · 零方向短路「序列类型无关」守卫（#717）

缺陷（P1 · `lingshu/world/coord_contract.py`）
-------------------------------------------------------------------------------
`apply_move()` 的零方向短路判据写的是 **tuple 字面量**比较：

    if d == (0.0, 0.0, 0.0):

而 `d` 是**方向序列**——调用方从 JSON/YAML 读方向得到的是 `list`
（`[0.0, 0.0, 0.0] == (0.0, 0.0, 0.0)` 在 Python 为 **False**）⇒ 短路不命中，
于是「零方向移动」落进正常分支、被 x/z 边界**钳制改写**：

    apply_move((99.0, 1.5, -99.0), 1.0, (0.0, 0.0, 0.0), 16)  -> (99.0, 1.5, -99.0)   短路
    apply_move((99.0, 1.5, -99.0), 1.0, [0.0, 0.0, 0.0], 16)  -> (15.5, 1.5, 0.5)     被钳

两种**语义等价**的表示给出不同结果；界内位置下两种写法一致 ⇒ 分歧有明确触发条件。
本模块 docstring 声明自己是 x/z 边界语义的**唯一真源**（被 5 个站点共用），
静默改变钳制语义的影响面即全部站点。

修法（最小 · 只改判据，不动类型声明）
-------------------------------------------------------------------------------
`if not any(d):` —— `d` 的**声明**（`SPEC["semantics"]` 与 `apply_move` docstring）
通篇是「方向」，从未声明 tuple 专属；同文件/同族各站点的 `_apply_move` 签名虽写
`Tuple[float, float, float]`，但真实入参在 `world_learner`/`world_model` 路径上来自
`recent_direction()`（tuple）与 JSON 反序列化（list）两条来源 ⇒ 判据取
**序列无关**的 `not any(d)`，`-0.0`/整数 `0` 与 tuple 口径逐位一致，
非有限分量（`any` 为真）同样不短路。

断言组（抽掉修复 ⇒ 按标注转红）
-------------------------------------------------------------------------------
  A **序列类型无关**：越界位置下 tuple / list / 整数 tuple / 含 `-0.0` 的 tuple
    四种等价零方向写法必须给出**同一**结果（旧码在 list 两支上红）
  B **短路语义未变**：零方向仍返回「量化后的原位置」，**不**钳制（tuple 口径不动）
  C **正对照·界内**：界内位置下四种写法本就一致（证明分歧只在越界触发，非普遍失败）
  D **非零方向不被误判为零**：`(1e-300, 0, 0)`、`(nan, 0, 0)`、`(inf, 0, 0)`
    必须**不**进短路支（`not any(d)` 不得把非零/非有限分量吞掉）
  E **正对照·非零方向仍被钳制**：非零方向下越界位置照旧钳到界内

运行（仓根）：python -X utf8 -m pytest tests/test_issue717_zero_direction_sequence_guard.py -q --no-header
"""
from __future__ import annotations

import math
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.world import coord_contract as C  # noqa: E402

SIZE = 16.0
OUT_OF_BOUNDS = (99.0, 1.5, -99.0)     # 越界：界为 [0.5, 15.5]
IN_BOUNDS = (5.0, 1.5, 5.0)
#: 四种**语义等价**的零方向写法（容器类型 / 整数 0 / `-0.0` 各一）。
ZERO_FORMS = (
    ("tuple", (0.0, 0.0, 0.0)),
    ("list", [0.0, 0.0, 0.0]),
    ("tuple-int", (0, 0, 0)),
    ("list-int", [0, 0, 0]),
    ("tuple-negzero", (0.0, -0.0, 0.0)),
    ("list-negzero", [0.0, -0.0, 0.0]),
)


def _same(a, b):
    """`repr` 级相同（区分 `0.0` 与 `-0.0`；NaN 与自身也算相同）。"""
    if isinstance(a, tuple) and isinstance(b, tuple):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    return repr(a) == repr(b)


def test_a_zero_direction_container_type_agnostic():
    """A：越界位置下六种等价零方向写法必须同结果（旧码 list 三支被钳制）。"""
    assert len(ZERO_FORMS) >= 2, "前置失败：零方向形态集为空"
    got = {}
    for name, d in ZERO_FORMS:
        assert len(d) == 3, "前置失败：零方向形态 %s 不是三分量" % name
        got[name] = C.apply_move(OUT_OF_BOUNDS, 1.0, d, SIZE)
    base = got["tuple"]
    assert base == OUT_OF_BOUNDS, \
        "前置失败：tuple 零方向应短路返回原位置（不钳制）: %r" % (base,)
    for name, r in got.items():
        assert _same(r, base), (
            "【#717】零方向形态 %s 与 tuple 结果不同 —— 短路判据对容器类型敏感："
            "%r ≠ %r（越界位置被 x/z 钳制改写）" % (name, r, base))


def test_b_zero_shortcut_still_returns_unclamped_position():
    """B：零方向短路语义未变——返回量化后的**原位置**，不钳制。"""
    pos = (99.123, 1.567, -99.456)
    r = C.apply_move(pos, 1.0, [0.0, 0.0, 0.0], SIZE)
    assert _same(r, (99.12, 1.57, -99.46)), \
        "【#717】零方向应返回量化后的原位置（越界也不钳制）: %r" % (r,)
    assert r[0] > SIZE and r[2] < 0.0, \
        "【#717】零方向被钳制到界内 —— 短路未命中: %r" % (r,)
    # ZERO_XZ_ONLY 口径同样对 list 生效（y 原样、x/z 量化）
    r_xz = C.apply_move(pos, 1.0, [0.0, 0.0, 0.0], SIZE,
                        zero_shortcut=C.ZERO_XZ_ONLY)
    assert repr(r_xz[1]) == repr(1.567), \
        "ZERO_XZ_ONLY 不应量化 y: %r" % (r_xz,)


def test_c_positive_control_in_bounds_agree():
    """C（正对照）：界内位置下六种写法本就一致 ⇒ 分歧只在越界触发。"""
    vals = [C.apply_move(IN_BOUNDS, 1.0, d, SIZE) for _, d in ZERO_FORMS]
    assert all(_same(v, IN_BOUNDS) for v in vals), \
        "正对照：界内位置下六种写法应全等且等于原位置: %r" % (vals,)


def test_d_nonzero_components_not_swallowed():
    """D：非零/非有限分量**不得**被误判为零方向（`not any(d)` 的边界）。

    判别器：短路支把 y 也量化（`ZERO_ROUND_ALL`），非短路支 y 原样直通
    ⇒ 取 `pos[1] = 5.678`，看 y 是否被量化即可判「走没走短路支」，
    与 x/z 的钳制读数无关。
    """
    pos = (5.0, 5.678, 5.0)
    zero_short = C.apply_move(pos, 1.0, [0.0, 0.0, 0.0], SIZE)
    assert repr(zero_short[1]) == repr(5.68), \
        "量具有效性：零方向短路应量化 y: %r" % (zero_short,)
    # 极小非零分量 ⇒ 必须**不**进短路支
    for d in ([1e-300, 0.0, 0.0], [0.0, 0.0, -1e-300], [1e-300, 0.0, 1e-300]):
        r = C.apply_move(pos, 1.0, d, SIZE)
        assert repr(r[1]) == repr(5.678), (
            "【#717】非零方向 %r 被判成零方向（y 被短路支量化）: %r" % (d, r))
    # 非有限分量（x/z）⇒ `any` 为真 ⇒ 同样不进短路支
    for d in ([float("nan"), 0.0, 0.0], [float("inf"), 0.0, 0.0],
              [0.0, 0.0, float("nan")], [0.0, 0.0, float("inf")]):
        r = C.apply_move(pos, 1.0, d, SIZE)
        assert repr(r[1]) == repr(5.678), (
            "【#717】非有限方向 %r 被判成零方向（y 被短路支量化）: %r" % (d,))
        assert isinstance(r, tuple) and len(r) == 3, \
            "非有限分量输入应仍返回三元组: %r" % (r,)


def test_e_positive_control_nonzero_direction_still_clamped():
    """E（正对照）：非零方向的越界位置照旧被钳到界内（钳制语义未被削弱）。"""
    r = C.apply_move(OUT_OF_BOUNDS, 1.0, [1.0, 0.0, 0.0], SIZE)
    assert r == (15.5, 1.5, 0.5), \
        "正对照：非零方向应把 x/z 都钳到界内（z 步长为 0 但起点越界）: %r" % (r,)
    r2 = C.apply_move((0.4, 1.5, 16.4), 1.0, [-1.0, 0.0, 1.0], SIZE)
    assert r2 == (0.5, 1.5, 15.5), \
        "正对照：反向非零方向应钳到下界/上界: %r" % (r2,)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "--no-header"]))

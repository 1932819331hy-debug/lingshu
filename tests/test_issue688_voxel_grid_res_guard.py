# -*- coding: utf-8 -*-
"""test_issue688_voxel_grid_res_guard · #688 守卫
=================================================
缺陷（#688）：`lingshu/world/mesh_gen.py` 的 `voxel_grid(soup, res)` 对 `res`
**无任何类型/正数/上限校验**，各类坏输入落成不同形态的错（实测）：
  · `res=0`    ⇒ `ZeroDivisionError: float division by zero`（step = dims/res）
  · `res=-1`   ⇒ **静默返回空占据**（`range(-1)` 空循环，无异常）
  · `res=0.5`  ⇒ `TypeError: 'float' object cannot be interpreted as an integer`
  · `res=NaN`  ⇒ 同上 TypeError
  · `res=True` ⇒ **静默返回空占据**（`True` 是 `int` 子类、值 1，range(1) 只跑一格）
  · `res='16'` ⇒ `TypeError: unsupported operand type(s) for /: 'float' and 'str'`
  · 无上限：`res=1e7` 跑满 604s（报告复核）。
**同文件**兄弟算子（`_revolve_core` 的 `seg>=3`、`prim_cone` 的 `top∈[0,1)`、
`prim_sphere` 的 `seg_v>=2`、`prim_torus` 的 `seg_u/seg_v>=3`）都有边界校验
⇒ 本处是**单点遗漏**（已复核：各自坏输入均 raise `ValueError`）。

修法：加 `_check_voxel_res`——正整数（**显式拒 `bool`**）+ 下界 `VOXEL_RES_MIN=2`
+ 上界 `VOXEL_RES_MAX=512`（取值依据见源码注释：耗时随 res 超线性增长）。

守卫判据（回退 ⇒ 指名哪条红）：
  ① `res=0`/`-1` ⇒ `ValueError`；`0.5`/`NaN`/`'16'`/`None` ⇒ `TypeError`；
     **`res=True` 单列一条**（bool 是 int 子类，最易漏）⇒ `TypeError`；
  ② `res` 超上限（`513`/`10**7`）⇒ `ValueError`；下界（`1`）⇒ `ValueError`；
  ③ 防误杀正对照：合法 `res`（8/16/32/48/512）⇒ 输出与**改前冻结值逐位相同**；
  ④ 变异自证：撤掉校验 ⇒ ① 必红（`res=0` 会重新变 `ZeroDivisionError`）。

运行（lingshu 仓根）：
  python3 -X utf8 -m pytest tests/test_issue688_voxel_grid_res_guard.py -q
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from lingshu.world import mesh_gen as MG                            # noqa: E402

#: 改前冻结：合法 res 下的 `occ` 集合（序列化 sha256）——该值在**改前代码**上复算取得。
#: 修复只在函数起手加一次校验调用，占据结果必须逐位不变。
FROZEN_OCC_SHA256 = {
    8: "61e9e05ce53347956a832aeb7b582b966a929416dc71fb4e5f58b0a278b3b880",
    16: "00431f0ead68284d1f87d5f7fd7eaa2bd5b5a940b6d5719a338fdcb341bebc8c",
    32: "92a17815f2462a6886776c9a555dedc0b14bc8e9ee802bc0224327981244e072",
    48: "be29ef83c7e51bff9cd093b4328d35775c098457cc20dd0466e2ef92a9e9c077",
}


def _cube_soup():
    """仓内可造的立方体面汤（不依赖外部模型资产）。"""
    m = MG.prim_box((1.0, 1.0, 1.0))
    return MG.tri_soup(m)


def _occ_sha(soup, res):
    occ = MG.voxel_grid(soup, res)["occ"]
    return hashlib.sha256(
        json.dumps(sorted(occ)).encode("utf-8")).hexdigest()


# ===========================================================================
# ① 类型与下界：各类坏输入按预期拒
# ===========================================================================
def test_688_1_res为零被拒():
    with pytest.raises(ValueError):
        MG.voxel_grid(_cube_soup(), 0)


def test_688_2_res为负被拒_改前是静默空():
    """`res=-1` 改前**静默返回空占据**（range(-1) 空循环）——本测钉住它必须 raise。"""
    with pytest.raises(ValueError):
        MG.voxel_grid(_cube_soup(), -1)


def test_688_3_res为一被拒_低于下界():
    with pytest.raises(ValueError):
        MG.voxel_grid(_cube_soup(), 1)


def test_688_4_res为浮点被拒():
    for bad in (0.5, 16.0, 1e3):
        with pytest.raises(TypeError):
            MG.voxel_grid(_cube_soup(), bad)


def test_688_5_res为NaN被拒():
    with pytest.raises(TypeError):
        MG.voxel_grid(_cube_soup(), float("nan"))


def test_688_6_res为字符串被拒():
    for bad in ("16", b"16"):
        with pytest.raises(TypeError):
            MG.voxel_grid(_cube_soup(), bad)


def test_688_7_res为None被拒():
    with pytest.raises(TypeError):
        MG.voxel_grid(_cube_soup(), None)


def test_688_8_res为bool被拒_单列一条():
    """★ `True`/`False` 是 `int` 子类（值 1/0）⇒ 最容易漏的静默面：
    改前 `res=True` **静默返回空占据**（range(1) 只跑一格、几乎全空）。
    本测单列，钉住 bool 必须被显式排除。"""
    for bad in (True, False):
        assert isinstance(bad, int), "前提：bool 是 int 子类（判据基础）"
        with pytest.raises(TypeError):
            MG.voxel_grid(_cube_soup(), bad)


# ===========================================================================
# ② 上界：超上限被拒
# ===========================================================================
def test_688_9_res超上限被拒():
    for bad in (MG.VOXEL_RES_MAX + 1, 1024, 10 ** 7):
        with pytest.raises(ValueError):
            MG.voxel_grid(_cube_soup(), bad)


def test_688_10_上界常量有依据且为512量级():
    """上界常量存在、且在报告复核建议的 ≤512 量级（防未来被无声放大到 DoS 面）。"""
    assert MG.VOXEL_RES_MAX == 512, "上限被改动：%r" % (MG.VOXEL_RES_MAX,)
    assert MG.VOXEL_RES_MIN == 2, "下界被改动：%r" % (MG.VOXEL_RES_MIN,)
    assert MG.VOXEL_RES_MIN < MG.VOXEL_RES_MAX


def test_688_11_上界本身可用():
    """边界值 512 必须**恰好合法**（拒的是 513，不是 512）——防「一刀切把上限也拒了」。

    这里直接校验收口函数（不实跑 res=512 的整张网格：本机 res=512 约 40~90s，
    放进守卫会把定向测试拖慢；边界「放行/拒绝」的判据落在 `_check_voxel_res` 上）。
    """
    assert MG._check_voxel_res(MG.VOXEL_RES_MAX) == MG.VOXEL_RES_MAX
    with pytest.raises(ValueError):
        MG._check_voxel_res(MG.VOXEL_RES_MAX + 1)


# ===========================================================================
# ③ 防误杀正对照：合法 res 输出与改前逐位相同
# ===========================================================================
@pytest.mark.parametrize("res", [8, 16, 32, 48])
def test_688_12_合法res输出与改前逐位相同(res):
    soup = _cube_soup()
    got = _occ_sha(soup, res)
    assert got == FROZEN_OCC_SHA256[res], (
        "合法 res=%d 的占据结果被改动了（修复误伤合法路径）：%s vs 冻结 %s"
        % (res, got[:16], FROZEN_OCC_SHA256[res][:16]))


def test_688_13_默认res与显式16一致():
    soup = _cube_soup()
    a = MG.voxel_grid(soup)["occ"]
    b = MG.voxel_grid(soup, 16)["occ"]
    assert a == b, "默认 res 与显式 16 不一致"


def test_688_14_voxel_iou透传校验_坏res同样被拒():
    """`voxel_iou` 把 res 透传给 `voxel_grid` ⇒ 坏 res 也应被拒（同一收口点）。"""
    a = _cube_soup()
    b = _cube_soup()
    with pytest.raises(ValueError):
        MG.voxel_iou(a, b, res=0)
    with pytest.raises(TypeError):
        MG.voxel_iou(a, b, res=True)


# ===========================================================================
# ④ 变异自证：撤掉校验 ⇒ ① 必红
# ===========================================================================
def test_688_15_变异自证_撤掉校验则零与负res不再被拒(monkeypatch):
    """本文件内自证「守卫有牙」：把校验函数替换成恒等（等价于撤掉校验）⇒
    ① 的判据立刻失效——`res=0` 回到 `ZeroDivisionError`（不是 ValueError）、
    `res=-1` 回到静默空。证明 ① 确实依赖该校验。"""
    monkeypatch.setattr(MG, "_check_voxel_res", lambda res, where="voxel_grid": res)
    soup = _cube_soup()
    # res=0 不再是 ValueError，而是 ZeroDivisionError（缺陷形态复原）
    with pytest.raises(ZeroDivisionError):
        MG.voxel_grid(soup, 0)
    # res=-1 不再 raise，而是静默空（缺陷形态复原）
    r = MG.voxel_grid(soup, -1)
    assert r["occ"] == set(), "变异后 res=-1 应静默空（缺陷形态）"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

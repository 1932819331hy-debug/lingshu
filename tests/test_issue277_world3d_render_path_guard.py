# -*- coding: utf-8 -*-
"""#277 world3d 门面 render 的 path 无目录约束 —— 守卫

缺陷（公开 issue #277 · [core/world3d] · 判定「成立」）：
  `SpacetimeMemoryEngine.world3d("render", params)` 把调用方给的 `params["path"]`
  **原样**交给 `Image.save(path)`：
      path = str(p.get("path", ""))     # ← 调用方完全可控
      img.save(path)                    # ← 无目录约束、无存在性检查
  ⇒ 可向任意路径写图像文件（唯一边界：Pillow 只认图像扩展名）。返回值还回显
  `os.path.abspath(path)` 并报 `status: ok`。

修法（最小 · 与同门面 `flush` 的 #324 收口同形）：
  · 越界闸在**门面侧**（`_confine_world3d_out_path`，先判后写）：白名单根＝
    显式配置面 `LINGSHU_WORLD3D_OUT_ROOT`（`os.pathsep` 多根）优先；**#480 起
    未配置即 fail-closed，不再回落 `MDCG_ROOT`**（该回落＝默认写真源，见 D）。
    归一用 `realpath + normcase` ⇒ 软链/junction 别名与 `..` 逃逸不得绕过。
  · 判定在**渲染之前** ⇒ 被拒时连位图都不分配。

判据来源：公开 issue #277「建议修复 1：限制输出根……`os.path.realpath` +
  `os.path.commonpath` 判定包含性，越界即拒绝并返回显式错误」＋ 同文件 #324 已采纳的
  「显式面 → fail-closed」回落链（同形先例，回落腿已被 #480 砍掉）。仓内无规定该
  口径的理论章节，属经验标定（本件 #277）。
边界（削掉的判别力，如实标注）：**不**加扩展名白名单（issue 建议 2）——issue 自身
  实测已证非图像扩展名由 `PIL.Image.save` 抛 `ValueError` 拒绝，本件不重复设卡；
  亦**不**加 `overwrite=False`（issue 建议 3，属新的产品决策，非本缺陷面）。

断言组（抽掉修复＝删掉 `_confine_world3d_out_path` 调用 ⇒ A/C/D 必红）：
  A 未配置任何根 ⇒ error 点名 #277，且目标路径**不存在**（先判后写）
  B 显式根内 ⇒ 照旧写成功、`status: ok`，文件真落盘（防误杀）
  C 越出显式根（含 `..` 逃逸）⇒ error 且零落盘
  D 未配置显式根时**不再回落** `MDCG_ROOT`（#480）：库根内/外一律拒绝且零落盘
  E 被拒时**零渲染**：`World3D.render` 未被调用（判定在渲染之前）

运行（仓根）：python -X utf8 -m pytest tests/test_issue277_world3d_render_path_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402
from lingshu.world.world3d import World3D  # noqa: E402

ENV = SpacetimeMemoryEngine.WORLD3D_OUT_ROOT_ENV


def _clear_roots(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    monkeypatch.delenv("MDCG_ROOT", raising=False)


def test_a_未配置任何根则拒绝且零落盘(monkeypatch):
    """A：连根都没有 ⇒ fail-closed，且目标路径**不存在**（先判后写）。"""
    td = tempfile.mkdtemp()
    _clear_roots(monkeypatch)
    eng = SpacetimeMemoryEngine(":memory:")
    out = os.path.join(td, "escaped_probe.png")
    r = eng.world3d("render", {"path": out, "screen_w": 64, "screen_h": 64})
    assert r["status"] == "error", "A 未配置根时仍放行写入：%r" % (r,)
    assert "#277" in r.get("error", ""), "A 拒绝理由未点名 #277：%r" % (r,)
    assert not os.path.exists(out), "A 被拒路径仍落盘（非先判后写）：%s" % out


def test_b_显式根内照旧写成功(monkeypatch):
    """B：防误杀——显式根内必须照旧写成功、文件真落盘。"""
    td = tempfile.mkdtemp()
    root = os.path.join(td, "out_root")
    os.makedirs(root, exist_ok=True)
    monkeypatch.setenv(ENV, root)
    monkeypatch.delenv("MDCG_ROOT", raising=False)
    eng = SpacetimeMemoryEngine(":memory:")

    out = os.path.join(root, "shot.png")
    r = eng.world3d("render", {"path": out, "screen_w": 64, "screen_h": 64})
    assert r["status"] == "ok", "B 根内写入被误拒：%r" % (r,)
    assert os.path.isfile(out), "B 根内未落盘：%s" % out
    assert r["path"] == os.path.abspath(out), "B 回显路径不符：%r" % (r.get("path"),)


def test_c_越出显式根被拒含点划逃逸(monkeypatch):
    """C：根外路径（含 `..` 逃逸）⇒ error 且零落盘。"""
    td = tempfile.mkdtemp()
    root = os.path.join(td, "out_root")
    os.makedirs(root, exist_ok=True)
    monkeypatch.setenv(ENV, root)
    monkeypatch.delenv("MDCG_ROOT", raising=False)
    eng = SpacetimeMemoryEngine(":memory:")

    for name, out in (("根外同级", os.path.join(td, "outside.png")),
                      ("点划逃逸", os.path.join(root, "..", "escaped.png"))):
        r = eng.world3d("render", {"path": out, "screen_w": 64, "screen_h": 64})
        assert r["status"] == "error", "C %s 未被拒：%r" % (name, r,)
        assert "#277" in r.get("error", ""), "C %s 拒绝理由未点名 #277：%r" % (name, r)
        assert not os.path.exists(os.path.abspath(out)), \
            "C %s 被拒后仍落盘：%s" % (name, out)


def test_d_不再回落_MDCG_ROOT(monkeypatch):
    """D（#480 更新）：未配置显式根 ⇒ **不再回落** `MDCG_ROOT`——库根内/外一律拒。

    旧 D 断言「回落库根 ⇒ 根内可写」，那正是 #480 的缺陷面（默认写真源）。
    #277 的白名单机制（显式根 + 归一包含性判定）保持不变，只砍回落腿。
    """
    td = tempfile.mkdtemp()
    lib = os.path.join(td, "mdcg_lib")
    os.makedirs(lib, exist_ok=True)
    monkeypatch.delenv(ENV, raising=False)
    monkeypatch.setenv("MDCG_ROOT", lib)
    eng = SpacetimeMemoryEngine(":memory:")

    inside = os.path.join(lib, "render", "ok.png")
    os.makedirs(os.path.dirname(inside), exist_ok=True)
    r = eng.world3d("render", {"path": inside, "screen_w": 64, "screen_h": 64})
    assert r["status"] == "error", "D 回落库根仍在（#480 未修）：%r" % (r,)
    assert not os.path.exists(inside), "D 库根内被拒后仍落盘"

    outside = os.path.join(td, "nope.png")
    r2 = eng.world3d("render", {"path": outside, "screen_w": 64, "screen_h": 64})
    assert r2["status"] == "error", "D 库根外未被拒：%r" % (r2,)
    assert not os.path.exists(outside), "D 库根外被拒后仍落盘"

    # 子腿：显式根位于真源之内 ⇒ 拒（与 flush 侧同判据）
    inner = os.path.join(lib, "out_root")
    os.makedirs(inner, exist_ok=True)
    monkeypatch.setenv(ENV, inner)
    r3 = eng.world3d("render", {"path": os.path.join(inner, "a.png"),
                                "screen_w": 64, "screen_h": 64})
    assert r3["status"] == "error", "D 真源内输出根未被拒：%r" % (r3,)

    # 子腿：显式根＝真源父目录 ⇒ 目标落回真源内，须拒
    monkeypatch.setenv(ENV, td)
    r4 = eng.world3d("render", {"path": os.path.join(lib, "backdoor.png"),
                                "screen_w": 64, "screen_h": 64})
    assert r4["status"] == "error", "D 父目录根绕回真源未被拒：%r" % (r4,)
    assert not os.path.exists(os.path.join(lib, "backdoor.png")), "D 绕回真源后仍落盘"


def test_e_被拒时零渲染(monkeypatch):
    """E：判定在渲染之前——越界被拒时 `World3D.render` 一次都没被调用。"""
    td = tempfile.mkdtemp()
    _clear_roots(monkeypatch)
    eng = SpacetimeMemoryEngine(":memory:")

    calls = []
    orig = World3D.render

    def spy(self, *a, **k):
        calls.append(1)
        return orig(self, *a, **k)

    monkeypatch.setattr(World3D, "render", spy)
    out = os.path.join(td, "should_not_render.png")
    r = eng.world3d("render", {"path": out, "screen_w": 64, "screen_h": 64})
    assert r["status"] == "error", "E 未配置根时未被拒：%r" % (r,)
    assert calls == [], "E 被拒前已渲染（判定不在渲染之前）：render 调用 %d 次" % len(calls)
    assert not os.path.exists(out)

# -*- coding: utf-8 -*-
"""#745 visual_check 采信记忆正文里的 `expected` 路径前无白名单根闸 —— 守卫

缺陷（lingshu issue #745 · [core/visual_check][安全]）：
  `visual_check` 用 `_path_re` 从 `screen_state` 节点**正文**提取路径，只做
  `os.path.exists` 就采信 ⇒ 记忆正文由模型可写 ⇒ `expected` 完全可控 ⇒
  **任意本地图像读**（`screen.diff(reference=...)`）+ **任意路径存在性探测**
  （`exists` 的返回即 oracle）。写向/读向的同门面兄弟 `wm_simloop` 早有白名单根闸
  （#324/#480/#744），本处无。

修法（三条，逐条落地）：
  ① 提取后、`os.path.exists` 之前施加根判定（`_screen_expected_allowed`）——
     白名单根＝显式面 `LINGSHU_SCREEN_OUT_ROOT` ／ `MDCG_ROOT` ／ body registry 的
     `workspace/screenshots` ／ **本进程本次运行自己捕获过**的截图目录（issue 复核帖
     建议的 A 方案）；归一用 `realpath + normcase`（与 `_confine_simloop_out_path` 同
     口径）；越界/无根可依 ⇒ fail-closed；
  ② 显式拒 `//`（含 `\\?\\` 设备前缀）UNC 形态与 `..` 穿越段；
  ③ 显式 `reference` 同样过根闸，但**越界即显式报错**（不静默回落基线）；
     使用前 `realpath` 复核落点（纵深防御，不声称覆盖使用当刻）。

断言组（抽掉任一修复即红）：
  A 根外绝对路径被拒（④ 腿在场 ⇒ 证明是「根外」而非「无根」）
  B UNC 形态被拒（`//?/` 设备前缀**真实存在** ⇒ 旧实现会采信；另含 `//corp/…` 形态）
  C `..` 穿越段被拒（目标文件真实存在）
  D 无根可依 ⇒ 拒（fail-closed）
  E 显式根 `LINGSHU_SCREEN_OUT_ROOT` 内 ⇒ 照常采信（防误杀）
  F `MDCG_ROOT` 腿 ⇒ 照常采信
  G 自捕获腿（④）：第二轮 `expected` ＝ 第一轮 capture 路径
  H body registry 的 `workspace/screenshots` 腿（③）⇒ 照常采信
  I 采信循环内那道闸在位：最新一条 screen_state 指向根外（真实存在）、次新一条
    指向根内合法路径 ⇒ 必须**跳过**根外那条、采信次新的根内路径（抽掉循环内的
    闸则「采信 → 使用前复核清空 → 回落基线」，与「跳过并回退」可观测地不同）
  J 显式 `reference` 越界 ⇒ **显式报错**（不静默回落基线）
  K 显式 `reference` 落在白名单根内 ⇒ 照常对照（防误杀）

运行（仓根）：python -X utf8 -m pytest tests/test_issue745_visual_check_path_guard.py -q --no-header
"""
from __future__ import annotations

import io
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402

ENV = SpacetimeMemoryEngine.SCREEN_OUT_ROOT_ENV


class _R:
    """与 body 组件 `invoke()` 返回值同形：ok / data / error / to_dict()。"""

    def __init__(self, data):
        self.ok, self.data, self.error = True, data, None

    def to_dict(self):
        return {"ok": True, "data": self.data}


class _FakeScreen:
    """最小假屏幕：capture 每次产出一张新图；diff 恒定报「无变化」。

    diff 不依赖 `self.paths`（对照腿可能在没有 capture 的情况下直接跑）。
    """

    def __init__(self, shots_dir):
        self.shots_dir = shots_dir
        self.i = 0
        self.paths = []

    def invoke(self, name, action, params):
        assert name == "screen", name
        if action == "capture":
            self.i += 1
            p = os.path.join(self.shots_dir, "shot%04d.png" % self.i)
            os.makedirs(self.shots_dir, exist_ok=True)
            with io.open(p, "wb") as fh:
                fh.write(b"x")
            self.paths.append(p)
            return _R({"path": p})
        if action == "diff":
            return _R({"changed": False, "change_ratio": 0.0})
        raise AssertionError(action)


def _unc_form(path):
    """构造「前导 `//` 且在本平台**真实存在**」的形态（B 腿要有判别力）。

    Windows：`//?/C:/…` 设备前缀形态（`os.path.exists` 为 True）；
    POSIX：无设备前缀，但 Linux 把前导 `//` 当 `/` ⇒ `//tmp/…` 真实存在（实测）。
    """
    real = str(path).replace("\\", "/")
    return "//?/" + real if os.name == "nt" else "//" + real.lstrip("/")


def _dot_alias(path):
    """`//./` 形态：前导 `//` **且 `realpath` 归一到普通路径**（可落在白名单根内）。

    这是「显式拒前导 `//`」那道闸**唯一**有判别力的形态：`//?/` 形态的
    `realpath` 停在设备前缀上（Windows 实测），根判定本就会拒它 ⇒ 抽掉 UNC
    那道闸也照样绿。而 `//./` 的 `realpath` 是普通路径 ⇒ **根内文件经它可被
    采信**，除非显式拒 `//`（Windows/POSIX 实测同形）。
    """
    return "//./" + str(path).replace("\\", "/").lstrip("/")


def _engine(td):
    e = SpacetimeMemoryEngine(":memory:")
    reg = _FakeScreen(os.path.join(str(td), "shots"))
    e._body_registry = reg
    return e, reg


def _img(td, *rel):
    """在 `<td>/<rel…>` 落一张真实存在的假图像，返回其绝对路径。"""
    p = os.path.join(str(td), *rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with io.open(p, "wb") as fh:
        fh.write(b"x")
    return p


def _seed_state(engine, path):
    """向记忆写一条 `tags=["screen_state"]` 节点（正文由模型可写）。"""
    engine.add_perception("[屏幕状态 %s] 模型可写的记忆正文" % path,
                          importance=0.5, tags=["screen_state"], skip_dedup=True)


def _clear(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    monkeypatch.delenv("MDCG_ROOT", raising=False)


def test_a_根外绝对路径被拒(tmp_path, monkeypatch):
    """A：④ 腿在场（根非空），记忆正文指向根外**真实存在**的文件 ⇒ 不采信。"""
    _clear(monkeypatch)
    e, reg = _engine(tmp_path)
    r0 = e.visual_check()                       # 建基线 + 记录 ④ 腿（shots 目录）
    assert r0.get("established") is True, r0
    os.remove(reg.paths[0])                     # 基线那张被清理（#271 J3 同形）
    secret = _img(tmp_path, "outside", "secret.png")
    _seed_state(e, secret)
    assert e._screen_expected_roots(), "A 前置：白名单根为空 ⇒ 无法区分「根外」与「无根」"

    r = e.visual_check()
    assert r.get("expected") != secret, (
        "A 根外路径被采信（#745 未修）：expected=%s" % (r.get("expected"),))
    assert r.get("established") is True, (
        "A 根外路径未被拒后未回落基线：%r" % (r,))
    assert secret not in (r.get("expected"), r.get("baseline")), r


def test_b_UNC形态被拒(tmp_path, monkeypatch):
    """B：UNC / 前导 `//` 形态一律拒（该形态**真实存在**，旧实现必采信）。"""
    _clear(monkeypatch)
    e, reg = _engine(tmp_path)
    r0 = e.visual_check()
    assert r0.get("established") is True, r0

    # ① 前导 `//` 形态：必须**真实存在**，否则旧实现（只做 `os.path.exists`）也会拒它
    #    ⇒ 该腿失去判别力。形态按平台取（见 `_unc_form`）：`//?/` 设备前缀是 Windows
    #    专有形态，Linux 上它不 exists（已实测）——照抄 Windows 形态会让这条腿在 Linux
    #    CI 上以「前置不成立」告红，那是**量具的平台假设**，不是被测实现的缺陷。
    dev = _unc_form(reg.paths[0])
    assert os.path.exists(dev), (
        "B 前置：`//` 起头形态在本平台不 exists ⇒ 该腿无判别力（形态=%r）" % (dev,))
    _seed_state(e, dev)
    r = e.visual_check()
    assert r.get("expected") != dev, (
        "B 前导 `//` 形态被采信：expected=%s" % (r.get("expected"),))

    # ② 普通 UNC 形态（`//corp/share/a.png`）：即使不存在也不得被采信。
    _seed_state(e, "//corp/share/a.png")
    r2 = e.visual_check()
    assert r2.get("expected") != "//corp/share/a.png", (
        "B 普通 UNC 形态被采信：expected=%s" % (r2.get("expected"),))

    # ③ `//./` 形态：**根内**文件（`realpath` 归一后落在白名单根里）⇒ 只有「显式拒
    #    前导 `//`」那道闸能拦住它。抽掉那道闸 ⇒ 本断言红（①②两条拦不住：它们的
    #    `realpath` 停在设备前缀/网络主机上，根判定本就拒）。
    inside = _img(tmp_path, "shots", "alias.png")
    dot = _dot_alias(inside)
    assert os.path.exists(dot), "B 前置：`//./` 形态不 exists ⇒ 该腿无判别力"
    _seed_state(e, dot)
    r3 = e.visual_check()
    assert r3.get("expected") != dot, (
        "B `//./` 形态（realpath 落根内）被采信：expected=%s" % (r3.get("expected"),))


def test_c_点划穿越段被拒(tmp_path, monkeypatch):
    """C：`..` 穿越段 ⇒ 拒（目标文件真实存在，旧实现必采信）。"""
    _clear(monkeypatch)
    e, reg = _engine(tmp_path)
    r0 = e.visual_check()
    assert r0.get("established") is True, r0

    _img(tmp_path, "outside", "secret.png")
    esc = os.path.join(reg.shots_dir, "..", "outside", "secret.png")
    assert os.path.exists(esc), "C 前置：穿越段目标不存在 ⇒ 该腿无判别力"
    _seed_state(e, esc)
    r = e.visual_check()
    assert r.get("expected") != esc, (
        "C 穿越段被采信：expected=%s" % (r.get("expected"),))
    assert os.path.normcase(os.path.realpath(r.get("expected") or "")) != \
        os.path.normcase(os.path.realpath(esc)), "C 归一后仍采信穿越段"


def test_d_无根可依即拒(tmp_path, monkeypatch):
    """D：无任何根（无 env、无 workspace、无自捕获）⇒ fail-closed，不采信。"""
    _clear(monkeypatch)
    e, _reg = _engine(tmp_path)
    secret = _img(tmp_path, "outside", "secret.png")
    _seed_state(e, secret)
    assert e._screen_expected_roots() == [], "D 前置：根不为空 ⇒ 非「无根可依」场景"

    r = e.visual_check()
    assert r.get("expected") != secret, (
        "D 无根可依仍采信记忆正文路径：expected=%s" % (r.get("expected"),))
    assert r.get("established") is True, "D 未回落基线：%r" % (r,)


def test_e_显式根内照常采信(tmp_path, monkeypatch):
    """E：防误杀——显式根 `LINGSHU_SCREEN_OUT_ROOT` 内 ⇒ 照常采信。"""
    _clear(monkeypatch)
    shots = os.path.join(str(tmp_path), "shots")
    monkeypatch.setenv(ENV, shots)
    e, _reg = _engine(tmp_path)
    inside = _img(tmp_path, "shots", "inside.png")
    _seed_state(e, inside)

    r = e.visual_check()
    assert r.get("expected") == inside, (
        "E 显式根内被误拒：expected=%s（应为 %s）" % (r.get("expected"), inside))


def test_f_MDCG_ROOT腿照常采信(tmp_path, monkeypatch):
    """F：`MDCG_ROOT` 腿 ⇒ 照常采信（#745 回帖承诺的同款口径）。"""
    _clear(monkeypatch)
    lib = os.path.join(str(tmp_path), "mdcg_lib")
    monkeypatch.setenv("MDCG_ROOT", lib)
    e, _reg = _engine(tmp_path)
    inside = _img(tmp_path, "mdcg_lib", "a.png")
    _seed_state(e, inside)

    r = e.visual_check()
    assert r.get("expected") == inside, (
        "F MDCG_ROOT 腿被误拒：expected=%s（应为 %s）" % (r.get("expected"), inside))


def test_g_自捕获腿有效(tmp_path, monkeypatch):
    """G：④ 腿——第二轮 `expected` ＝ 第一轮 capture 的那张（无任何 env）。"""
    _clear(monkeypatch)
    e, reg = _engine(tmp_path)
    r0 = e.visual_check()
    assert r0.get("established") is True, r0
    r1 = e.visual_check()
    assert r1.get("expected") == reg.paths[0], (
        "G 自捕获腿失效：expected=%s（应为第一轮 capture 的 %s）"
        % (r1.get("expected"), reg.paths[0]))


def test_h_body_workspace_screenshots腿照常采信(tmp_path, monkeypatch):
    """H：③ 腿——body registry 的 `workspace/screenshots` ⇒ 照常采信。"""
    _clear(monkeypatch)
    ws = os.path.join(str(tmp_path), "aeis_ws")
    e, reg = _engine(tmp_path)
    reg.workspace = ws                            # 设备自身截图落点（AEIS_WORKSPACE）
    inside = _img(tmp_path, "aeis_ws", "screenshots", "s.png")
    _seed_state(e, inside)

    r = e.visual_check()
    assert r.get("expected") == inside, (
        "H workspace/screenshots 腿被误拒：expected=%s（应为 %s）"
        % (r.get("expected"), inside))


def test_i_采信循环跳过根外回退到根内(tmp_path, monkeypatch):
    """I：采信循环内那道闸**在位**——最新一条指向根外（真实存在）时必须被跳过，
    回退采信次新那条根内合法路径。

    判别力：抽掉循环内的 `_screen_expected_allowed` ⇒ 根外那条被设为 expected，
    「使用前复核」再把它清空 ⇒ 回落基线（`established=True`），与「跳过并回退」
    可观测地不同（expected 由「次新根内路径」变成「无」）。
    """
    _clear(monkeypatch)
    shots = os.path.join(str(tmp_path), "shots")
    monkeypatch.setenv(ENV, shots)
    e, _reg = _engine(tmp_path)
    legit = _img(tmp_path, "shots", "legit.png")
    _seed_state(e, legit)                         # 次新：根内合法（真实存在）
    secret = _img(tmp_path, "outside", "secret.png")
    assert os.path.exists(secret), "I 前置：根外目标不存在 ⇒ 该腿无判别力"
    _seed_state(e, secret)                        # 最新：根外（模型可控正文）

    r = e.visual_check()
    assert r.get("expected") == legit, (
        "I 未跳过根外那条并回退到次新根内路径：expected=%r（应为 %s；"
        "回落基线=%r）" % (r.get("expected"), legit, r.get("established")))
    assert r.get("expected") != secret, "I 根外那条被采信：%r" % (r.get("expected"),)


def test_j_显式reference越界显式报错(tmp_path, monkeypatch):
    """J：显式 `reference`（本方法经工具面暴露 ⇒ 同属不可信输入）越界 ⇒ 报错。

    判别力：抽掉 1b 那道闸 ⇒ 静默回落基线（`status="ok"`、`established=True`），
    与「显式拒绝」可观测地不同——静默把「按这张图对照」换成「已建基线」是调用方
    无法察觉的语义替换。
    """
    _clear(monkeypatch)
    shots = os.path.join(str(tmp_path), "shots")
    monkeypatch.setenv(ENV, shots)
    e, _reg = _engine(tmp_path)
    secret = _img(tmp_path, "outside", "secret.png")

    r = e.visual_check(reference=secret)
    assert r.get("status") == "error", (
        "J 显式 reference 越界未报错：%r" % (r,))
    assert r.get("established") is not True, (
        "J 越界 reference 静默回落基线（语义替换）：%r" % (r,))


def test_k_显式reference根内照常对照(tmp_path, monkeypatch):
    """K：防误杀——显式 `reference` 落在白名单根内 ⇒ 照常走对照分支。"""
    _clear(monkeypatch)
    shots = os.path.join(str(tmp_path), "shots")
    monkeypatch.setenv(ENV, shots)
    e, _reg = _engine(tmp_path)
    inside = _img(tmp_path, "shots", "ref.png")

    r = e.visual_check(reference=inside)
    assert r.get("status") == "ok", r
    assert r.get("expected") == inside, (
        "K 根内显式 reference 被误拒：%r" % (r,))

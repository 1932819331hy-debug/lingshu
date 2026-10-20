# -*- coding: utf-8 -*-
"""#746 导出落点根闸（`LINGSHU_EXPORT_OUT_ROOT`）常驻守卫 —— 走**生产实现**，不测副本

缺陷（lingshu issue #746 · [core/export][安全]）：
  `SpacetimeMemoryEngine.export_all` / `export_trajectory` 的目标路径由调用方给出、
  **无任何白名单根闸** ⇒ 凡能触达门面者即可向任意路径落盘（导出产物含
  `sensitivity=private` 条目、自我层快照、action_logs ⇒ 任意位置写＝信息落点失控）。
  修法（PR #760，merge `b313e5e9`）＝新增显式配置面 `LINGSHU_EXPORT_OUT_ROOT`
  （`os.pathsep` 多根）＋ `_confine_export_out_path`（`realpath + normcase` 归一，
  越界 fail-closed `ValueError`），两个落点各接一次闸。

★ 本件**只通过真实调用路径**触发（`export_all` / `export_trajectory` /
  `export_structured_trajectories`），**不复制**闸的实现来测那份副本 —— PR #759
  （`tests/test_issue746_export_root_guard.py`，仍 open）被判「方向对但守卫不接生产
  实现」的正是这一点。故本件末组（E）显式钉住「接线点在 `lingshu/core/core.py` 的
  生产方法体内」＋「本模块未自带闸实现」。

断言组（抽掉闸 ⇒ 对应组必红）：
  A 闸三口径：①未配根 ⇒ 不限制；②配根 + 根外 ⇒ `ValueError` 且**目标不存在**；
    ③配根 + 根内 ⇒ 落盘成功（含 `export_trajectory` 的 `overwrote_existing` 契约）
  B 越界变体：前缀兄弟目录 / `..` 相对穿越 / 根内链接（symlink 或 junction）指向根外 /
    多根中第二根为根外 / `export_structured_trajectories` 根外 / 相对路径且 cwd 在根外
    ⇒ **均应被拒**
  C 默认态未变（防过修）：不配根 ⇒ 根外 `export_all` 与 `export_trajectory` 仍**写出成功**；
    空白串 env（`"   "`）视同未配置
  D 拒绝后**零残留**：拒绝时根外目录里不得留下 `.tmp-` 残件、目标不得被创建
  E 接线与判别力：①接线点在两个生产方法体内（`inspect.getsource`）；②本模块未复制闸
    实现；③**定点变异**（把 `_confine_export_out_path` 换成恒等放行）⇒ A/B 的场景体
    必须转红（`pytest.raises(AssertionError)`）

运行（仓根）：python -X utf8 -m pytest tests/test_issue746_export_out_root_gate.py -q --no-header
"""
from __future__ import annotations

import inspect
import io
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402

#: 配置面入口名。用 `getattr` 兜底取值：**缺陷形态**（闸未接、类常量不存在）下本模块
#: 仍能收集成功，从而给出**断言级红**（「闸不在位」）而不是 collection error —— 后者
#: 会让「回退即红」的读数退化成「测试件自身炸了」，判别力说不清。
ENV = getattr(SpacetimeMemoryEngine, "EXPORT_OUT_ROOT_ENV", "LINGSHU_EXPORT_OUT_ROOT")


# ────────────────────────────── 公共工具 ──────────────────────────────

def _engine():
    return SpacetimeMemoryEngine(":memory:")


def _dirs(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir(parents=True, exist_ok=True)
    outside.mkdir(parents=True, exist_ok=True)
    return str(root), str(outside)


def _residue(d):
    """目录里所有 `.tmp-` 残件（#330 原子写的临时件命名：`<target>.tmp-<hex>`）。"""
    return sorted(f for f in os.listdir(d) if ".tmp-" in f)


def _link_out(root, outside):
    """在 `root` 内建一个指向 `outside` 的目录链接；返回 (链接路径, 形态)。

    Windows 未开开发者模式时 `os.symlink` 报 `WinError 1314`（客户端没有所需的特权），
    改退到 junction（`mklink /J`，无需特权）——`os.path.realpath` 同样解析 junction
    目标，故判别力等价。两者皆不可用 ⇒ 返回 (None, None)，由调用方 `pytest.skip`。
    """
    link = os.path.join(root, "linkout")
    try:
        os.symlink(outside, link, target_is_directory=True)
        return link, "symlink"
    except (OSError, NotImplementedError):
        pass
    try:
        r = subprocess.run(["cmd", "/c", "mklink", "/J", link, outside],
                           capture_output=True)
        if r.returncode == 0 and os.path.isdir(link):
            return link, "junction"
    except OSError:
        pass
    return None, None


def _write_json(path, text='{"probe": 1}'):
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


# ─────────────── 场景体（真实现下通过；抽掉闸即抛 AssertionError） ───────────────
# 组 E③ 的定点变异复用这同一批场景体 —— 这正是「判别力」的证明：
# 若场景体是恒真的假绿，变异后仍会通过，E③ 就会红。

def _scenario_export_all_outside_rejected(tmp_path, monkeypatch):
    """配根 + 根外 `export_all` ⇒ `ValueError`、目标不存在、零残留。"""
    root, outside = _dirs(tmp_path)
    monkeypatch.setenv(ENV, root)
    target = os.path.join(outside, "a.json")
    eng = _engine()
    try:
        eng.export_all(target)
    except ValueError as e:
        assert "越界" in str(e), "拒绝理由未点名越界：%r" % (str(e),)
    else:
        raise AssertionError("A②：配根后根外 export_all 未被拒 ⇒ 根闸失效")
    assert not os.path.exists(target), "A②：被拒却仍创建了目标"
    assert _residue(outside) == [], "D：拒绝后留下残件 %r" % (_residue(outside),)


def _scenario_export_trajectory_outside_rejected(tmp_path, monkeypatch):
    """配根 + 根外 `export_trajectory` ⇒ `ValueError`、目标不存在、零残留。"""
    root, outside = _dirs(tmp_path)
    monkeypatch.setenv(ENV, root)
    target = os.path.join(outside, "t.json")
    eng = _engine()
    try:
        eng.export_trajectory(output_path=target)
    except ValueError as e:
        assert "越界" in str(e), "拒绝理由未点名越界：%r" % (str(e),)
    else:
        raise AssertionError("A②：配根后根外 export_trajectory 未被拒 ⇒ 根闸失效")
    assert not os.path.exists(target), "A②：被拒却仍创建了目标"
    assert _residue(outside) == [], "D：拒绝后留下残件 %r" % (_residue(outside),)


def _assert_rejected(fn, label):
    """`fn()` 必须抛 `ValueError`（越界 fail-closed）；否则抛 `AssertionError`。

    ★ 用显式 try/except 而**不**用 `pytest.raises`：`pytest.raises` 失败时抛的是
    pytest 自己的 `Failed`，变异腿（组 E③）要断言的是「场景体转红」＝`AssertionError`。
    显式抛出使场景体在**真实现**与**变异**两种情况下都给出可判别的同一类异常。
    """
    try:
        fn()
    except ValueError as e:
        assert "越界" in str(e), "%s：拒绝理由未点名越界：%r" % (label, str(e))
        return
    raise AssertionError("%s：未被拒（无 ValueError）⇒ 根闸失效" % label)


def _scenario_prefix_sibling_rejected(tmp_path, monkeypatch):
    """前缀绕过：兄弟目录名 = 根名 + 尾字符 ⇒ 拒（`startswith(root + sep)` 判据）。"""
    root, _outside = _dirs(tmp_path)
    monkeypatch.setenv(ENV, root)
    sib = root + "X"
    os.makedirs(sib, exist_ok=True)
    eng = _engine()
    _assert_rejected(lambda: eng.export_all(os.path.join(sib, "a.json")), "B-前缀绕过")
    assert os.listdir(sib) == [], "B：前缀绕过被拒后仍落盘"


def _scenario_rel_traverse_rejected(tmp_path, monkeypatch):
    """相对穿越：`<root>/../outside/…` ⇒ 拒。"""
    root, outside = _dirs(tmp_path)
    monkeypatch.setenv(ENV, root)
    eng = _engine()
    _assert_rejected(lambda: eng.export_all(os.path.join(root, "..", "outside", "a.json")),
                     "B-相对穿越")
    assert os.listdir(outside) == [], "B：`..` 穿越被拒后仍落盘"


def _scenario_symlink_out_rejected(tmp_path, monkeypatch):
    """根内链接指向根外目录 ⇒ 拒（`realpath` 解析链接目标侧）。"""
    root, outside = _dirs(tmp_path)
    link, form = _link_out(root, outside)
    if link is None:
        pytest.skip("本机既不能建 symlink（无特权）也不能建 junction ⇒ 该腿不可判")
    monkeypatch.setenv(ENV, root)
    eng = _engine()
    _assert_rejected(lambda: eng.export_all(os.path.join(link, "a.json")),
                     "B-链接穿透(%s)" % form)
    assert os.listdir(outside) == [], "B(%s)：链接穿透被拒后仍落盘" % form


def _scenario_multiroot_outside_rejected(tmp_path, monkeypatch):
    """多根（`os.pathsep` 两段）：第二根之外仍 ⇒ 拒。"""
    root, outside = _dirs(tmp_path)
    second = tmp_path / "root2"
    second.mkdir(exist_ok=True)
    monkeypatch.setenv(ENV, root + os.pathsep + str(second))
    eng = _engine()
    _assert_rejected(lambda: eng.export_all(os.path.join(outside, "a.json")),
                     "B-多根之外")


def _scenario_structured_trajectories_rejected(tmp_path, monkeypatch):
    """`export_structured_trajectories` 透传同一闸 ⇒ 根外拒。"""
    root, outside = _dirs(tmp_path)
    monkeypatch.setenv(ENV, root)
    eng = _engine()
    _assert_rejected(
        lambda: eng.export_structured_trajectories(output_path=os.path.join(outside, "t.json")),
        "B-批量轨迹透传")
    assert os.listdir(outside) == [], "B：批量轨迹被拒后仍落盘"


def _scenario_relative_cwd_outside_rejected(tmp_path, monkeypatch):
    """相对路径 + cwd 在根外 ⇒ 按 cwd 归一后越界 ⇒ 拒。"""
    root, outside = _dirs(tmp_path)
    monkeypatch.setenv(ENV, root)
    rel_dir = os.path.join(outside, "rel")
    os.makedirs(rel_dir, exist_ok=True)
    old = os.getcwd()
    os.chdir(outside)
    try:
        eng = _engine()
        _assert_rejected(lambda: eng.export_all(os.path.join("rel", "a.json")),
                         "B-相对路径-cwd在根外")
    finally:
        os.chdir(old)
    assert os.listdir(rel_dir) == [], "B：相对路径被拒后仍落盘"


# ────────────────────────────── A 组 · 闸三口径 ──────────────────────────────

def test_a1_未配根_不限制(monkeypatch, tmp_path):
    """A①（**正对照**）：未配 `LINGSHU_EXPORT_OUT_ROOT` ⇒ 不限制，根外照常写出。"""
    monkeypatch.delenv(ENV, raising=False)
    _root, outside = _dirs(tmp_path)
    target = os.path.join(outside, "a.json")
    eng = _engine()
    res = eng.export_all(target)
    assert os.path.exists(target), "A①：未配根时根外导出被拦（过修）"
    assert res["path"] == target


def test_a2_配根_根外_export_all_拒(monkeypatch, tmp_path):
    """A②：配根 + 根外 ⇒ `ValueError`，目标不存在，零残留。"""
    _scenario_export_all_outside_rejected(tmp_path, monkeypatch)


def test_a3_配根_根内_export_all_成功(monkeypatch, tmp_path):
    """A③（**正对照**）：配根 + 根内 ⇒ 落盘成功（防过修）。"""
    root, _outside = _dirs(tmp_path)
    monkeypatch.setenv(ENV, root)
    target = os.path.join(root, "a.json")
    eng = _engine()
    res = eng.export_all(target)
    assert os.path.exists(target), "A③：根内导出被误拒"
    assert res["path"] == target


def test_a4_配根_根外_export_trajectory_拒(monkeypatch, tmp_path):
    """A②（轨迹腿）：配根 + 根外 ⇒ `ValueError`，目标不存在，零残留。"""
    _scenario_export_trajectory_outside_rejected(tmp_path, monkeypatch)


def test_a5_配根_根内_export_trajectory_成功且带覆盖上报(monkeypatch, tmp_path):
    """A③（轨迹腿 · 正对照）：根内成功，且 #698 的 `overwrote_existing` 契约仍在。"""
    root, _outside = _dirs(tmp_path)
    monkeypatch.setenv(ENV, root)
    target = os.path.join(root, "t.json")
    eng = _engine()
    res = eng.export_trajectory(output_path=target)
    assert os.path.exists(target), "A③：根内轨迹导出被误拒"
    assert res["overwrote_existing"] is False, "#698 契约：首次导出应为 False"
    res2 = eng.export_trajectory(output_path=target)
    assert res2["overwrote_existing"] is True, "#698 契约：二次导出应为 True"


def test_a6_根内深层子目录_成功(monkeypatch, tmp_path):
    """A③ 补强（防过修）：根内**深层子目录**不得被判越界。"""
    root, _outside = _dirs(tmp_path)
    monkeypatch.setenv(ENV, root)
    deep = os.path.join(root, "a", "b", "c")
    os.makedirs(deep)
    target = os.path.join(deep, "a.json")
    eng = _engine()
    eng.export_all(target)
    assert os.path.exists(target), "根内深层子目录被误拒"


def test_a7_多根_第二根内_成功(monkeypatch, tmp_path):
    """A③ 补强（防过修）：`os.pathsep` 多根的第二根内 ⇒ 放行。"""
    root, _outside = _dirs(tmp_path)
    second = tmp_path / "root2"
    second.mkdir(exist_ok=True)
    monkeypatch.setenv(ENV, root + os.pathsep + str(second))
    target = str(second / "a.json")
    eng = _engine()
    eng.export_all(target)
    assert os.path.exists(target), "多根的第二根被误拒"


# ────────────────────────────── B 组 · 越界变体 ──────────────────────────────

def test_b1_前缀兄弟目录_拒(monkeypatch, tmp_path):
    """B：`<root>X/…` 前缀绕过 ⇒ 拒（裸 `startswith` 会漏放，本闸用 `root + os.sep`）。"""
    _scenario_prefix_sibling_rejected(tmp_path, monkeypatch)


def test_b2_相对穿越_拒(monkeypatch, tmp_path):
    """B：`<root>/../outside/…` ⇒ 拒（`realpath` 归一后判包含性）。"""
    _scenario_rel_traverse_rejected(tmp_path, monkeypatch)


def test_b3_根内链接指向根外_拒(monkeypatch, tmp_path):
    """B：根内链接（symlink 或 junction）指向根外 ⇒ 拒（`realpath` 穿透）。"""
    _scenario_symlink_out_rejected(tmp_path, monkeypatch)


def test_b4_多根_根外仍拒(monkeypatch, tmp_path):
    """B：多根第二根之外 ⇒ 拒。"""
    _scenario_multiroot_outside_rejected(tmp_path, monkeypatch)


def test_b5_批量结构化轨迹_根外拒(monkeypatch, tmp_path):
    """B：`export_structured_trajectories(output_path=…)` 透传同一闸 ⇒ 拒。"""
    _scenario_structured_trajectories_rejected(tmp_path, monkeypatch)


def test_b6_相对路径_cwd在根外_拒(monkeypatch, tmp_path):
    """B：相对路径按 cwd 归一后越界 ⇒ 拒。"""
    _scenario_relative_cwd_outside_rejected(tmp_path, monkeypatch)


def test_b7_大小写变体_根内放行(monkeypatch, tmp_path):
    """B 正对照：`normcase` 口径下大小写变体写根内 ⇒ 放行（仅 Windows 有意义）。"""
    if os.name != "nt":
        pytest.skip("大小写不敏感是 Windows 语义；POSIX 上该路径不存在，判据不成立")
    root, _outside = _dirs(tmp_path)
    monkeypatch.setenv(ENV, root)
    eng = _engine()
    eng.export_all(os.path.join(root.upper(), "a.json"))
    assert os.path.exists(os.path.join(root, "a.json")), "大小写变体被误拒"


# ────────────────────────────── C 组 · 默认态未变 ──────────────────────────────

def test_c1_默认态_export_all_根外成功(monkeypatch, tmp_path):
    """C（**正对照**）：不配根 ⇒ 根外 `export_all` 仍写出成功（默认姿态未被改变）。"""
    monkeypatch.delenv(ENV, raising=False)
    _root, outside = _dirs(tmp_path)
    target = os.path.join(outside, "a.json")
    eng = _engine()
    eng.export_all(target)
    assert os.path.exists(target), "C：默认态根外导出被拦 ⇒ 行为回归"


def test_c2_默认态_export_trajectory_根外成功(monkeypatch, tmp_path):
    """C（**正对照**）：不配根 ⇒ 根外 `export_trajectory` 仍写出成功。"""
    monkeypatch.delenv(ENV, raising=False)
    _root, outside = _dirs(tmp_path)
    target = os.path.join(outside, "t.json")
    eng = _engine()
    eng.export_trajectory(output_path=target)
    assert os.path.exists(target), "C：默认态根外轨迹导出被拦 ⇒ 行为回归"


def test_c3_空白串env_视同未配置(monkeypatch, tmp_path):
    """C：`LINGSHU_EXPORT_OUT_ROOT="   "` ⇒ 视同未配置（`strip` 过滤空段）⇒ 放行。"""
    monkeypatch.setenv(ENV, "   ")
    _root, outside = _dirs(tmp_path)
    target = os.path.join(outside, "a.json")
    eng = _engine()
    eng.export_all(target)
    assert os.path.exists(target), "空白串 env 被当成有效根 ⇒ 误拦"


# ────────────────────────────── D 组 · 拒绝后零残留 ──────────────────────────────

def test_d1_拒绝后_export_all_零残留(monkeypatch, tmp_path):
    """D：拒绝必须发生在**任何副作用之前** ⇒ 目标不存在、无 `.tmp-` 残件。"""
    root, outside = _dirs(tmp_path)
    monkeypatch.setenv(ENV, root)
    # 预置一份「已存在」目标：若闸判在写后，覆盖已发生 ⇒ 这里能看出字节被换掉
    sentinel = _write_json(os.path.join(outside, "a.json"), '{"PRETEND":"original"}')
    before = io.open(sentinel, "rb").read()
    eng = _engine()
    with pytest.raises(ValueError):
        eng.export_all(sentinel)
    assert io.open(sentinel, "rb").read() == before, "D：被拒却已改写既有目标"
    assert _residue(outside) == [], "D：拒绝后留下残件 %r" % (_residue(outside),)


def test_d2_拒绝后_export_trajectory_零残留(monkeypatch, tmp_path):
    """D（轨迹腿）：拒绝后根外目录零残留。"""
    root, outside = _dirs(tmp_path)
    monkeypatch.setenv(ENV, root)
    eng = _engine()
    with pytest.raises(ValueError):
        eng.export_trajectory(output_path=os.path.join(outside, "t.json"))
    assert os.listdir(outside) == [], "D：拒绝后残留 %r" % (os.listdir(outside),)
    assert _residue(outside) == []


# ────────────────────── E 组 · 接线、非副本、定点变异 ──────────────────────

def test_e1_接线点在两个生产方法体内():
    """E①：闸的调用点必须在 `lingshu/core/core.py` 的**生产方法体**内（不是本测试件）。"""
    for fn in (SpacetimeMemoryEngine.export_all,
               SpacetimeMemoryEngine.export_trajectory):
        src = inspect.getsource(fn)
        assert "_confine_export_out_path(" in src, (
            "E①：%s 体内找不到根闸调用 —— 接线被抽掉，本守卫失去判别对象"
            % fn.__name__)
    # `export_structured_trajectories` 是透传：其生产实现必须把 output_path 交给
    # `export_trajectory`（否则「批量腿」的闸只是本测试件的臆想）。
    st = inspect.getsource(SpacetimeMemoryEngine.export_structured_trajectories)
    assert "export_trajectory(" in st and "output_path" in st, \
        "E①：批量导出未透传 output_path ⇒ B5 腿无生产依据"


def test_e2_本件未复制闸实现():
    """E②：守卫**不测副本** —— 本模块不得自带闸的实现（#759 被判的正是这一点）。"""
    with io.open(__file__, encoding="utf-8") as f:
        me = f.read()
    # ★ 标记串拼接构造：否则「检查标记的源码本身」会把标记写进被检文本里（自指陷阱）。
    for marker in ("def " + "_confine_export_out_path",
                   "def " + "_export_out_roots",
                   "os.path." + "normcase(os.path.realpath"):
        assert marker not in me, (
            "E②：本测试件里出现闸实现的复制片段 %r ⇒ 测的是副本不是生产实现"
            % marker)
    # 闸必须是生产模块上的 classmethod（真实调用路径：`self._confine_export_out_path(...)`）
    import lingshu.core.core as core_mod
    assert core_mod.__file__.replace("\\", "/").endswith("lingshu/core/core.py"), \
        "E②：闸所在模块不是生产模块"
    assert isinstance(core_mod.SpacetimeMemoryEngine.__dict__["_confine_export_out_path"],
                      classmethod), "E②：闸在生产模块上不是 classmethod ⇒ 接线形态可疑"


#: 变异腿的场景体清单 —— 「抽掉闸 ⇒ 必红」的**全组**覆盖（A②/B/D 三条口径各在其内）。
#: 本仓既有 pytest 式守卫的变异体例即「在同一测试件内 monkeypatch 生产符号后重跑
#: **同一场景体**，断言它转红」（`tests/test_issue463_shared_sync_identity_guard.py`
#: 的 F 组、`tests/test_issue253_extras_declared_guard.py` 的 C 组），本组照抄该体例。
MUTATE_SCENARIOS = [
    ("A②-export_all-根外", _scenario_export_all_outside_rejected),
    ("A②-export_trajectory-根外", _scenario_export_trajectory_outside_rejected),
    ("B-前缀绕过", _scenario_prefix_sibling_rejected),
    ("B-相对穿越", _scenario_rel_traverse_rejected),
    ("B-链接穿透", _scenario_symlink_out_rejected),
    ("B-多根之外", _scenario_multiroot_outside_rejected),
    ("B-批量轨迹透传", _scenario_structured_trajectories_rejected),
    ("B-相对路径-cwd在根外", _scenario_relative_cwd_outside_rejected),
]


def _no_gate(self, out_path):        # 变异体：恒等放行（= 抽掉闸）
    return out_path


@pytest.mark.parametrize("label,scen", MUTATE_SCENARIOS, ids=[m[0] for m in MUTATE_SCENARIOS])
def test_e3_变异腿_抽掉闸则场景体必红(label, scen, monkeypatch, tmp_path):
    """E③（**变异自证 · 承重**）：真实现下场景体通过 ⇒ 抽掉闸后**同一场景体**必转红。

    读数（本机）：真实现 8/8 场景体通过；变异后 8/8 抛 `AssertionError`（恰点红）。
    若某条变异后**仍通过** ⇒ 该组的断言是恒真假绿，本守卫无判别力（假守卫）。
    """
    # ① 真实现：场景体必须通过（组 A/B/D 已各自证明；此处再证一次作为变异基线）
    scen(tmp_path / "real", monkeypatch)
    # ② 定点变异：把生产类上的闸换成恒等放行（**改的是生产符号**，不是副本）
    monkeypatch.setattr(SpacetimeMemoryEngine, "_confine_export_out_path",
                        _no_gate, raising=True)
    with pytest.raises(AssertionError):
        scen(tmp_path / "mutated", monkeypatch)


def test_e4_变异腿_默认态腿不受变异影响(monkeypatch, tmp_path):
    """E③ 反向对照：C 组（默认态）**不依赖闸** ⇒ 变异后仍必须通过。

    这条防「变异腿被写成恒红」——若 C 组在变异后也红，说明变异伤到了默认态语义，
    该变异不是「只抽掉闸」的定点变异。
    """
    monkeypatch.setattr(SpacetimeMemoryEngine, "_confine_export_out_path",
                        _no_gate, raising=True)
    monkeypatch.delenv(ENV, raising=False)
    _root, outside = _dirs(tmp_path)
    a = os.path.join(outside, "a.json")
    t = os.path.join(outside, "t.json")
    eng = _engine()
    eng.export_all(a)
    eng.export_trajectory(output_path=t)
    assert os.path.exists(a) and os.path.exists(t), \
        "变异后默认态根外导出失败 ⇒ 变异不是定点抽闸"


def test_e5_变异腿_读数为8条全点红(monkeypatch, tmp_path):
    """E③ 汇总读数：逐条列出 8 条场景体的「真实现通过 / 变异后点红」结果。

    以**显式计数**给出变异腿读数（留痕口径），而非只看 pytest 的 passed 总数。
    """
    real_ok, mutated_red = [], []
    for label, scen in MUTATE_SCENARIOS:
        sub = tmp_path / ("sum_real_" + label)
        try:
            scen(sub, monkeypatch)
        except Exception as e:                       # pragma: no cover - 真实现不该红
            raise AssertionError("真实现下场景体 %s 已红：%s" % (label, e))
        real_ok.append(label)

    monkeypatch.setattr(SpacetimeMemoryEngine, "_confine_export_out_path",
                        _no_gate, raising=True)
    for label, scen in MUTATE_SCENARIOS:
        sub = tmp_path / ("sum_mut_" + label)
        try:
            scen(sub, monkeypatch)
        except AssertionError:
            mutated_red.append(label)
        else:
            raise AssertionError(
                "变异腿失败：抽掉闸后 %s **未点红** ⇒ 该组断言是恒真假绿（假守卫）"
                % label)
    print("\n[变异腿读数] 真实现通过 %d/%d；抽闸后点红 %d/%d：%s"
          % (len(real_ok), len(MUTATE_SCENARIOS), len(mutated_red),
             len(MUTATE_SCENARIOS), ", ".join(mutated_red)))
    assert len(mutated_red) == len(MUTATE_SCENARIOS)


def test_e6_闸根取自环境变量名常量():
    """E① 补强：环境变量名是类常量 `EXPORT_OUT_ROOT_ENV`（配置面唯一入口）。"""
    assert SpacetimeMemoryEngine.EXPORT_OUT_ROOT_ENV == "LINGSHU_EXPORT_OUT_ROOT"
    assert ENV == SpacetimeMemoryEngine.EXPORT_OUT_ROOT_ENV

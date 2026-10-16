# -*- coding: utf-8 -*-
"""test_issue748_brain_env_fallback_guard · #748 `BRAIN_PYTHON` / `BRAIN_PYTHONPATH`
env 兜底取值收窄守卫
============================================================================
缺陷（#748 · 判定「部分成立」：RCE 半成立、凭据外带半不成立）：
  `MCPClient.__init__` 的 env 回落面（`BRAIN_PYTHON` / `BRAIN_PYTHONPATH`）只经
  `_require_env_interpreter` / `_require_env_dir` 校验，而两者旧口径**只查
  「绝对路径＋存在性」**。攻击者的毒目录本身就是一个存在的目录、毒 `.cmd` 本身
  就是一个存在的文件 ⇒ **存在性不是白名单**：实测仍跑到攻击者的
  `md_cg/mcp_server.py`。凭据半**不成立**：`AEIS_DESIGNER_KEY` 已被 `_brain_env`
  的 `_ENV_ALLOW_NAMES` 白名单挡在子进程之外。

修法（只收窄 env 兜底面；显式参数面与 env 回落本身都不动）：
  ① `_require_env_interpreter`：追加**解释器文件名形状**校验（`_PY_EXE_RE`：
     `python` / `python3` / `python3.12` / `python.exe` / `python312.exe`）
     ＋ 可选**受信根**校验（`LINGSHU_BRAIN_PYTHON_ROOTS`，`os.pathsep` 多根）；
  ② `_require_env_dir`：追加**脑包结构**校验（目录下须解析出 `md_cg/__init__.py`
     与 `md_cg/mcp_server.py`）＋ 可选受信根校验
     （`LINGSHU_BRAIN_PYTHONPATH_ROOTS`）。

断言组
------
G1 毒目录（结构合法）：①未声明受信根 ⇒ 放行（有意的便利面，防把功能修死）；
   ②声明受信根为**另一个**目录 ⇒ 拒；③**兄弟目录前缀**（root=`…/root`、值=
   `…/rootEvil`）⇒ 拒（边界按分隔符切，不是裸字符串前缀）。
G2 非脑包目录（空临时目录）⇒ 拒（旧口径放行 ⇒ 变异即红）。
G3 任意可执行：`evil.cmd` / `evil.exe` ⇒ 拒（旧口径放行 ⇒ 变异即红）；
   `sys.executable` 与形状合法名（`python3.12`）⇒ 放行（防过修）。
G4 端到端接线：`BRAIN_PYTHONPATH=<空临时目录>` 且不传 `pythonpath=` ⇒
   `MCPClient(...)` 在**起子进程之前**抛 `ValueError` 且文案点名
   `BRAIN_PYTHONPATH`（防「校验函数在、`__init__` 没调」）。
G5 显式参数面不变：源码锚（两个校验调用只出现在 `if not python:` /
   `if not pythonpath:` 两个 env 回落分支内，且各只出现一次）＋ 行为腿
   （`MCPClient(pythonpath=<空目录>, python=sys.executable)` **不**因结构校验
   抛错——Popen 被哨兵替换，断言「走到了起子进程」而不是「被结构校验拦下」）。
G6 反向腿：受信根**已设置且值在其中** ⇒ 放行；受信根指向无关目录 ⇒ 拒。

变异哨兵（逐处必红；由本仓 #748 交付流程用脚本改源后复跑本件，读数见报告）
  · M1 去掉 `_require_env_dir` 的结构校验 ⇒ G2 红；
  · M2 去掉 `_require_env_interpreter` 的形状校验 ⇒ G3 红；
  · M3 去掉受信根校验 ⇒ G1②/G6 红。

边界（如实）
  · 残留面：能设置本进程 env 者同样能设置受信根变量 ⇒ 该面整体属**提权/持久化
    放大器**（与 main 既有定性一致），非默认可达 RCE；形状/结构校验挡住的是
    「任意可执行 / 任意存在的目录」这一档，不改变上述定性。
  · 本件不真起脑端子进程：G4 断言「校验先于 Popen」，G5 用哨兵替换 Popen。
  · 受信根比较用 `os.path.normcase(os.path.realpath(...))`（两侧同口径），
    与实现同款归一化。

运行：python -X utf8 -m pytest tests/test_issue748_brain_env_fallback_guard.py -q
"""
from __future__ import annotations

import ast
import inspect
import os
import sys
import textwrap

import pytest

from lingshu.world import brain_store
from lingshu.world.brain_store import (
    _BRAIN_PYTHON_ROOTS_ENV,
    _BRAIN_PYTHONPATH_ROOTS_ENV,
    MCPClient,
    _require_env_dir,
    _require_env_interpreter,
)


# ---------------------------------------------------------------------------
# 夹具与助手
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_parent_env(monkeypatch):
    """清掉宿主可能存在的父进程配置面，保证每个用例从「未声明」起跑。"""
    for name in ("BRAIN_PYTHON", "BRAIN_PYTHONPATH", "BRAIN_ROOT",
                 _BRAIN_PYTHON_ROOTS_ENV, _BRAIN_PYTHONPATH_ROOTS_ENV):
        monkeypatch.delenv(name, raising=False)
    yield


def _make_brain_pkg(root):
    """在 `root` 下造出「结构合法」的脑包目录（md_cg/__init__.py + mcp_server.py）。"""
    pkg = os.path.join(str(root), "md_cg")
    os.makedirs(pkg, exist_ok=True)
    for name in ("__init__.py", "mcp_server.py"):
        with open(os.path.join(pkg, name), "w", encoding="utf-8"):
            pass
    return str(root)


def _touch(path, name):
    full = os.path.join(str(path), name)
    with open(full, "w", encoding="utf-8"):
        pass
    return full


def _raised(fn, *args):
    """调用 `fn`，返回捕获的异常（无异常 ⇒ None）。"""
    try:
        fn(*args)
    except BaseException as exc:  # noqa: BLE001 —— 本助手只做「抛没抛」判定
        return exc
    return None


class _SpawnReached(RuntimeError):
    """Popen 哨兵：说明流程已经走到「起子进程」这一步。"""


def _exploding_popen(*_args, **_kwargs):
    raise _SpawnReached("已到达起子进程一步")


# ---------------------------------------------------------------------------
# G1 毒目录：结构合法时，受信根是唯一裁决面
# ---------------------------------------------------------------------------


def test_g1a_toxic_dir_passes_when_no_trusted_root_declared(tmp_path):
    """G1①：未声明受信根 ⇒ 结构合法即放行（**有意的便利面**，防把功能修死）。"""
    toxic = _make_brain_pkg(tmp_path / "toxic")
    assert _require_env_dir(toxic, "BRAIN_PYTHONPATH") == toxic


def test_g1b_toxic_dir_rejected_outside_trusted_root(tmp_path, monkeypatch):
    """G1②：声明受信根为**另一个**目录 ⇒ 越出受信根，拒（点名变量与根变量）。"""
    toxic = _make_brain_pkg(tmp_path / "toxic")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv(_BRAIN_PYTHONPATH_ROOTS_ENV, str(elsewhere))
    raised = _raised(_require_env_dir, toxic, "BRAIN_PYTHONPATH")
    assert raised is not None, "越出受信根的脑包目录未被拒（#748）"
    assert "BRAIN_PYTHONPATH" in str(raised), "文案未点名该变量：%r" % (raised,)
    assert _BRAIN_PYTHONPATH_ROOTS_ENV in str(raised), \
        "文案未点名受信根变量：%r" % (raised,)


def test_g1c_sibling_prefix_is_not_inside_root(tmp_path, monkeypatch):
    """G1③：`…/rootEvil` **不**在 `…/root` 内（边界按分隔符切，不是裸前缀）。"""
    root = tmp_path / "root"
    root.mkdir()
    evil = _make_brain_pkg(tmp_path / "rootEvil")
    monkeypatch.setenv(_BRAIN_PYTHONPATH_ROOTS_ENV, str(root))
    raised = _raised(_require_env_dir, evil, "BRAIN_PYTHONPATH")
    assert raised is not None, \
        "兄弟目录（共享字符串前缀）被误判为在受信根内：%r（#748）" % (evil,)


# ---------------------------------------------------------------------------
# G2 非脑包目录
# ---------------------------------------------------------------------------


def test_g2_non_brain_package_dir_rejected(tmp_path):
    """G2：空目录（旧口径放行）⇒ 新契约拒，文案点名变量与 md_cg 脑包。"""
    empty = tmp_path / "empty"
    empty.mkdir()
    raised = _raised(_require_env_dir, str(empty), "BRAIN_PYTHONPATH")
    assert raised is not None, "非脑包目录未被拒（#748；旧口径放行 ⇒ 变异即红）"
    assert isinstance(raised, ValueError), "应为 ValueError：%r" % (raised,)
    assert "BRAIN_PYTHONPATH" in str(raised), "文案未点名该变量：%r" % (raised,)
    assert "md_cg" in str(raised), "文案未说明脑包结构缺失：%r" % (raised,)


def test_g2b_partial_structure_rejected(tmp_path):
    """G2 补：只有 `md_cg/__init__.py`（缺 mcp_server.py）⇒ 仍拒（防只查一半）。"""
    partial = tmp_path / "partial"
    os.makedirs(os.path.join(str(partial), "md_cg"))
    _touch(os.path.join(str(partial), "md_cg"), "__init__.py")
    assert _raised(_require_env_dir, str(partial), "BRAIN_PYTHONPATH") is not None, \
        "缺 md_cg/mcp_server.py 仍被放行（#748）"


# ---------------------------------------------------------------------------
# G3 任意可执行 vs 解释器形状
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["evil.cmd", "evil.exe", "pythonw.exe",
                                 "mypython.exe"])
def test_g3a_arbitrary_executable_rejected(tmp_path, name):
    """G3：任意可执行（存在即放行的旧口径）⇒ 新契约拒。"""
    exe = _touch(tmp_path, name)
    raised = _raised(_require_env_interpreter, exe)
    assert raised is not None, \
        "%s 未被拒（#748：存在性不是白名单；旧口径放行 ⇒ 变异即红）" % (name,)
    assert isinstance(raised, ValueError), "应为 ValueError：%r" % (raised,)
    assert "BRAIN_PYTHON" in str(raised), "文案未点名该变量：%r" % (raised,)
    assert "解释器形状" in str(raised), "文案未说明形状不符：%r" % (raised,)


@pytest.mark.parametrize("name", ["python3.12", "python3", "python.exe"])
def test_g3b_python_shaped_names_pass(tmp_path, name):
    """G3 反向腿：形状合法名 ⇒ 放行（防过修）。"""
    exe = _touch(tmp_path, name)
    assert _require_env_interpreter(exe) == exe


def test_g3c_sys_executable_passes():
    """G3 反向腿：`sys.executable` ⇒ 放行（既有守卫依赖此点）。"""
    assert _require_env_interpreter(sys.executable) == sys.executable


# ---------------------------------------------------------------------------
# G4 端到端接线（校验先于起子进程）
# ---------------------------------------------------------------------------


def test_g4_env_fallback_rejected_before_spawn(tmp_path, monkeypatch):
    """G4：`BRAIN_PYTHONPATH=<空目录>` ⇒ `MCPClient(...)` 校验期抛错，未起子进程。"""
    empty = tmp_path / "empty"
    empty.mkdir()
    spawned = []

    def _recording_popen(*args, **kwargs):
        spawned.append(args)
        raise AssertionError("不该走到起子进程（校验应先行）")

    monkeypatch.setattr(brain_store.subprocess, "Popen", _recording_popen)
    monkeypatch.setenv("BRAIN_PYTHONPATH", str(empty))
    raised = _raised(MCPClient, None, None, None, None, None, 3.0)
    assert raised is not None, \
        "MCPClient 未对 env 兜底的 BRAIN_PYTHONPATH 做校验（#748，接线断）"
    assert isinstance(raised, ValueError), \
        "应为校验期 ValueError，实得 %r" % (raised,)
    assert "BRAIN_PYTHONPATH" in str(raised), "文案未点名该变量：%r" % (raised,)
    assert spawned == [], "校验未先于起子进程（已调用 Popen）"


def test_g4b_env_fallback_interpreter_rejected_before_spawn(tmp_path, monkeypatch):
    """G4 补：`BRAIN_PYTHON=<毒 .cmd>` ⇒ 同样在校验期抛错、未起子进程。"""
    brain_pkg = _make_brain_pkg(tmp_path / "brain")
    evil = _touch(tmp_path, "evil.cmd")
    spawned = []

    def _recording_popen(*args, **kwargs):
        spawned.append(args)
        raise AssertionError("不该走到起子进程（校验应先行）")

    monkeypatch.setattr(brain_store.subprocess, "Popen", _recording_popen)
    monkeypatch.setenv("BRAIN_PYTHON", evil)
    raised = _raised(MCPClient, None, brain_pkg, None, None, None, 3.0)
    assert raised is not None, \
        "MCPClient 未对 env 兜底的 BRAIN_PYTHON 做形状校验（#748，接线断）"
    assert isinstance(raised, ValueError), "应为 ValueError：%r" % (raised,)
    assert "BRAIN_PYTHON" in str(raised), "文案未点名该变量：%r" % (raised,)
    assert spawned == [], "校验未先于起子进程（已调用 Popen）"


# ---------------------------------------------------------------------------
# G5 显式参数面不变（源码锚 + 行为腿）
# ---------------------------------------------------------------------------


def _init_ast():
    return ast.parse(textwrap.dedent(inspect.getsource(MCPClient.__init__)))


def _call_names(node):
    return [n.func.id for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]


def _guarded_if_node(tree, var):
    """取 `if not <var>:` 那个 `If` 节点（找不到 ⇒ None）。"""
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)
                and isinstance(test.operand, ast.Name)
                and test.operand.id == var):
            return node
    return None


def test_g5a_validation_calls_only_in_env_fallback_branches():
    """G5 源码锚：两个校验调用只在 env 回落分支内，且全文各只出现一次。"""
    tree = _init_ast()
    all_names = _call_names(tree)
    assert all_names.count("_require_env_interpreter") == 1, \
        "`_require_env_interpreter` 调用点不为 1：%r" % (all_names,)
    assert all_names.count("_require_env_dir") == 1, \
        "`_require_env_dir` 调用点不为 1：%r" % (all_names,)
    py_branch = _guarded_if_node(tree, "python")
    pp_branch = _guarded_if_node(tree, "pythonpath")
    assert py_branch is not None and pp_branch is not None, \
        "`__init__` 里找不到 `if not python:` / `if not pythonpath:` 回落分支"
    assert "_require_env_interpreter" in _call_names(py_branch), \
        "解释器校验不在 `if not python:` 分支内（env 回落面被挪动）"
    assert "_require_env_dir" in _call_names(pp_branch), \
        "脑包目录校验不在 `if not pythonpath:` 分支内（env 回落面被挪动）"


def test_g5b_explicit_params_are_not_structure_validated(tmp_path, monkeypatch):
    """G5 行为腿：显式 `pythonpath=`（空目录）不被结构校验拦下 ⇒ 走到起子进程。"""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(brain_store.subprocess, "Popen", _exploding_popen)
    raised = _raised(MCPClient, sys.executable, str(empty), None, None, None, 3.0)
    assert raised is not None, "显式参数面意外地没有走到起子进程"
    assert isinstance(raised, _SpawnReached), \
        "显式参数面被结构校验拦下（#748 越界改了显式参数面）：%r" % (raised,)
    assert "md_cg" not in str(raised), "显式参数面出现结构校验文案：%r" % (raised,)


def test_g5c_explicit_params_win_over_poisoned_env(tmp_path, monkeypatch):
    """G5 行为腿补：显式参数在场时，毒 env 不参与裁决（env 只作回落）。"""
    empty = tmp_path / "empty"
    empty.mkdir()
    evil = _touch(tmp_path, "evil.cmd")
    monkeypatch.setattr(brain_store.subprocess, "Popen", _exploding_popen)
    monkeypatch.setenv("BRAIN_PYTHON", evil)
    monkeypatch.setenv("BRAIN_PYTHONPATH", str(empty))
    raised = _raised(MCPClient, sys.executable, str(empty), None, None, None, 3.0)
    assert isinstance(raised, _SpawnReached), \
        "显式参数未能压过毒 env（回落语义被改）：%r" % (raised,)


# ---------------------------------------------------------------------------
# G6 反向腿：受信根已设置且值在其中 ⇒ 放行
# ---------------------------------------------------------------------------


def test_g6a_value_inside_declared_root_passes(tmp_path, monkeypatch):
    """G6：受信根＝父目录、值在其中 ⇒ 两个校验器都放行。"""
    brain_pkg = _make_brain_pkg(tmp_path / "brain")
    exe = _touch(brain_pkg, "python3.12")
    monkeypatch.setenv(_BRAIN_PYTHONPATH_ROOTS_ENV, str(tmp_path))
    monkeypatch.setenv(_BRAIN_PYTHON_ROOTS_ENV, str(tmp_path))
    assert _require_env_dir(brain_pkg, "BRAIN_PYTHONPATH") == brain_pkg
    assert _require_env_interpreter(exe) == exe


def test_g6b_value_exactly_at_root_passes(tmp_path, monkeypatch):
    """G6 补：值**等于**受信根本身 ⇒ 放行（不是只有真子目录才算）。"""
    brain_pkg = _make_brain_pkg(tmp_path / "brain")
    monkeypatch.setenv(_BRAIN_PYTHONPATH_ROOTS_ENV, brain_pkg)
    assert _require_env_dir(brain_pkg, "BRAIN_PYTHONPATH") == brain_pkg


def test_g6c_interpreter_outside_declared_root_rejected(tmp_path, monkeypatch):
    """G6 补：解释器受信根指向无关目录 ⇒ `sys.executable` 被拒。"""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv(_BRAIN_PYTHON_ROOTS_ENV, str(elsewhere))
    raised = _raised(_require_env_interpreter, sys.executable)
    assert raised is not None, "越出受信根的解释器未被拒（#748）"
    assert _BRAIN_PYTHON_ROOTS_ENV in str(raised), \
        "文案未点名受信根变量：%r" % (raised,)


def test_g6d_multiple_roots_one_match_is_enough(tmp_path, monkeypatch):
    """G6 补：`os.pathsep` 多根——命中其中任意一个即放行（防把多根写法修死）。"""
    brain_pkg = _make_brain_pkg(tmp_path / "brain")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv(_BRAIN_PYTHONPATH_ROOTS_ENV,
                       os.pathsep.join([str(elsewhere), str(tmp_path)]))
    assert _require_env_dir(brain_pkg, "BRAIN_PYTHONPATH") == brain_pkg

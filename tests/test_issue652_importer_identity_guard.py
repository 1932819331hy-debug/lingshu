# -*- coding: utf-8 -*-
"""#652④ `_importer_is_lingshu` 的判据面：**危险方向**可被模块全局伪造 —— 守卫

缺陷（公开 issue #652 第 ④ 条）：`lingshu/core/component_resolver.py` 的
`_importer_is_lingshu()` 决定「本次裸名组件导入是否来自 lingshu 包内」，从而决定
`_ComponentFinder.find_spec` 找不到组件时是 `raise`（包内，fail-closed）还是
`return None`（交还 `PathFinder`，按 `sys.path` 解析）。

报告人实测的是**无害方向**（伪造 `__name__ = "lingshu.fake"` ⇒ `False → True`
＝仍 `raise`，不回落 cwd）。本守卫钉住的是**危险方向**（值守复跑补测）：

  旧实现第二判据取 `frame.f_globals["__file__"]`——**模块全局**，调用方随时可写。
  ⇒ **包内文件执行的代码**只要把 `__file__` 改成包外路径、`__name__` 改成宿主名，
  本函数就返回 **False** ⇒ 交还 `PathFinder` ⇒ 该次裸名导入**逃出受控面**，
  可解析 `cwd` / `sys.path` 上的同名模块（正是 #156 收口要拦的那条路）。
  实测（旧实现）：`co_filename` 为包内真实文件、`__name__="hostmod"`、
  `__file__="<包外>/hostmod.py"` ⇒ 读数 **False**（应为 True）。

修法：第二判据改取 `frame.f_code.co_filename`——**代码对象**的编译期事实，
包内文件无论怎样改模块全局都不可能把自己判成宿主。

判据来源：公开 issue #652（报告人给的修法方向即「改用真实文件路径判定」）；
本条**扩展**了报告人的实测面（他们未测危险方向）。

断言组（抽掉修法即红）：
  G1 危险方向：包内文件执行的帧 + 模块全局自报宿主 ⇒ 必须判为包内（True）。
  G2 对照（防过修正向）：宿主文件执行的帧 ⇒ 必须判为宿主（False）。
  G3 报告人方向（无害）：宿主文件自报 lingshu 名 ⇒ True（fail-closed，维持原状）。
  G4 真包内模块名（`lingshu.*`）⇒ True（原有第一判据一字未动）。
  G5 端到端：包内文件执行的裸名导入，受控面缺失时仍 `raise`（不被伪造成宿主后
     回落 `PathFinder`）——与 G1 同因，但走 `_ComponentFinder.find_spec` 全链。

运行（仓根）：python -X utf8 -m pytest tests/test_issue652_importer_identity_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core import component_resolver as CR  # noqa: E402

#: 一个**真实存在于包内**的源文件——用作 `co_filename`（代码对象事实）。
PKG_FILE = os.path.join(CR._PKG_ROOT, "core", "component_resolver.py")
_HOST_FILE = os.path.join(tempfile.gettempdir(), "i652_hostmod.py")

_SRC = "def call():\n    return CR._importer_is_lingshu()\n"


def _call(name, file_glob, co_filename):
    """在「模块全局可控 + 代码对象 co_filename 可控」的帧里调用判据。"""
    g = {"CR": CR, "__name__": name, "__file__": file_glob}
    exec(compile(_SRC, co_filename, "exec"), g)  # noqa: S102 - 探针本意
    return g["call"]()


# --------------------------------------------------------------------------- G1
def test_g1_forged_module_globals_cannot_escape_pkg_identity():
    """G1（危险方向）：代码确属包内 ⇒ 改模块全局不得把自己判成宿主。"""
    got = _call("hostmod", _HOST_FILE, PKG_FILE)
    assert got is True, (
        "包内文件执行的代码被判成宿主（危险方向可伪造 ⇒ 该次裸名导入会交还 "
        "PathFinder、逃出受控面）：读数 %r" % (got,))


# --------------------------------------------------------------------------- G2
def test_g2_host_code_still_host():
    """G2：宿主文件执行的帧仍判为宿主（`#275` 的宿主自有同名模块面不得收紧）。"""
    assert _call("hostmod", _HOST_FILE, _HOST_FILE) is False


# --------------------------------------------------------------------------- G3
def test_g3_forged_lingshu_name_still_fail_closed():
    """G3（报告人方向·无害）：宿主自报 `lingshu.*` 名 ⇒ True（维持原状）。"""
    assert _call("lingshu.fake", _HOST_FILE, _HOST_FILE) is True


# --------------------------------------------------------------------------- G4
def test_g4_real_pkg_module_name_true():
    """G4：真包内模块名（第一判据）一字未动。"""
    assert _call("lingshu.core.core", _HOST_FILE, _HOST_FILE) is True
    assert _call("lingshu", _HOST_FILE, _HOST_FILE) is True


# --------------------------------------------------------------------------- G5
def test_g5_engine_side_strictness_end_to_end():
    """G5 端到端：包内代码 + 受控面缺失 + 模块全局伪造 ⇒ 仍 `raise`（不回落）。"""
    tmp = tempfile.mkdtemp(prefix="i652_e2e_")
    with open(os.path.join(tmp, "self_cognition_engine.py"), "w",
              encoding="utf-8") as f:
        f.write("class SelfCognitionEngine:\n    FAKE = True\n")
    code = (
        "import sys\n"
        "from lingshu.core import component_resolver as CR\n"
        "CR.install()\n"
        "import types\n"
        "# 伪造成「宿主模块」的模块全局\n"
        "mod = types.ModuleType('hostmod')\n"
        "mod.__file__ = %r\n"
        "mod.__name__ = 'hostmod'\n"
        "sys.modules['hostmod'] = mod\n"
        "src = 'def call():\\n    import self_cognition_engine\\n'\n"
        "g = {'__name__': 'hostmod', '__file__': %r}\n"
        "exec(compile(src, %r, 'exec'), g)\n"
        "try:\n"
        "    g['call']()\n"
        "    print('NO-RAISE')\n"
        "except ModuleNotFoundError as e:\n"
        "    print('RAISED', e.name)\n"
    ) % (_HOST_FILE, _HOST_FILE, PKG_FILE)
    import subprocess
    env = {k: v for k, v in os.environ.items()
           if k not in ("LINGSHU_ALLOW_CWD_IMPORTS", "LINGSHU_COMPONENT_ROOT")}
    env["PYTHONPATH"] = tmp + os.pathsep + REPO
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    r = subprocess.run([sys.executable, "-B", "-W", "ignore", "-c", code], cwd=tmp,
                       env=env, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, "rc=%r stderr=%s" % (r.returncode, r.stderr[-400:])
    assert "RAISED self_cognition_engine" in r.stdout, (
        "包内代码经模块全局伪造成宿主后逃出受控面（应仍 raise）：%r" % (r.stdout,))

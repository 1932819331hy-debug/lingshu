# -*- coding: utf-8 -*-
"""守卫 · issue #699 六项**静态可判**缺陷的回归防线（**纯静态 / 零 bpy**）

运行（在桥根 `blender-wm-bridge/`，或直接给绝对路径）：
    python -X utf8 -m pytest wm_bridge/factory/tests/test_batch699_static_guards.py -q

------------------------------------------------------------------ 本件防的是什么
`bridges/` 是**进程外适配器**，刻意不进 wheel、也**不在任何 CI 腿**里（`git grep bridges -- .github/`
= 0 命中）⇒ 这批缺陷的危害面是"使用者手动跑 bridges 时"，不会让 main 门禁变红。但**真缺陷
就是真缺陷**：判据工具的假成功、恒真断言、陈旧缓存冒充指纹，都会让"跑过了"与"真的对了"
混为一谈。本件为 #699 确认的六项各钉一条**静态可判**的守卫（回退该项修复 ⇒ 必红）。

------------------------------------------------------------------ 逐条（回退 ⇒ 哪条红）
  F1  gate_all.py `PASS-WITH-SKIP` 退出码折叠     → test_f1_*（有跳过 ⇒ rc=2，不再是 0）
  F3  raster.iou(空, 空) 返回 1.0（假成功）        → test_f3_*（⇒ 0.0 且 detail 标 undefined）
  F7  bpy_shade.py `X != Y or True` 恒真式         → test_f7_*（AST：条件里不许出现 `or True`）
  F8  pngmetrics.canonical_file 缓存 sha 无来源标记 → test_f8_*（⇒ 返回值带 from_cache）
  F15 bpy_shade.__all__ 含未定义名 `setup_studio`   → test_f15_*（AST：`__all__` 名须全部有定义）
  F16 make_hex_gif.py 把 dict 当 list 迭代          → test_f16_*（AST：不许 `len(<render_frames>)`）

------------------------------------------------------------------ 不适用条件
· 需 Blender/bpy 运行时的行为（渲染/材质/几何）**不在本件**：F7/F15/F16 用**静态**（AST/源码）
  判，不 import `bpy_shade` / `make_hex_gif`（两者模块级即 `import bpy` 或直接执行）。
· 本件不跑任何子进程门（F1 用**打桩** `_run`，不真起 4 条产线的门）。
"""
from __future__ import annotations

import ast
import importlib.util
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
#: 包根（`blender-wm-bridge/`）—— 向上找到含 `wm_bridge/` 的那一级
_PKG_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

_FACTORY = os.path.join(_PKG_ROOT, "wm_bridge", "factory")
_SHADE = os.path.join(_FACTORY, "shade")
_GATE_ALL = os.path.join(_FACTORY, "_det", "gate_all.py")
_BPY_SHADE = os.path.join(_SHADE, "bpy_shade.py")
_MAKE_HEX_GIF = os.path.join(_FACTORY, "anim", "_det", "make_hex_gif.py")


def _src(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _tree(path):
    return ast.parse(_src(path))


def _load_by_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _module_level_names(tree):
    """模块级定义的名字集合（函数/类/赋值/导入），用于核 `__all__`。"""
    names = set()
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            names.add(n.target.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                names.add(a.asname or a.name.split(".")[0])
    return names


def _all_list(tree):
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == "__all__":
                    return [e.value for e in n.value.elts]
    raise AssertionError("模块级找不到 `__all__` 定义")


# ══════════════════════════════════════════════════════════════ F1 · gate_all 退出码
def _run_gate_all(monkeypatch, verdicts, skip_art=False):
    """打桩 `GATES` + `_run` 跑 gate_all.main()，返回 returncode。

    verdicts：每门 `_run` 要返回的 VERDICT 串列表（长度 = 门数）。
    skip_art=True：把第一门标成 `需要外部产物`，不给产物目录 ⇒ 走 SKIP 分支。
    """
    mod = _load_by_path("wm699_gate_all", _GATE_ALL)
    n = len(verdicts)
    if skip_art:
        gates = (("g_skip", "s0.py", True),) + tuple(
            ("g%d" % i, "s%d.py" % i, False) for i in range(1, n))
    else:
        gates = tuple(("g%d" % i, "s%d.py" % i, False) for i in range(n))
    it = iter(verdicts)

    def fake_run(script, extra=()):
        v = next(it)
        return (0 if v == "PASS" else 1), v, "通过/失败 读数"

    monkeypatch.setattr(mod, "GATES", gates)
    monkeypatch.setattr(mod, "_run", fake_run)
    monkeypatch.delenv("WM_SVG_IOU_DIR", raising=False)
    return mod.main([])


def test_f1_all_pass_exit_zero(monkeypatch):
    """4 门全 PASS 且无跳过 ⇒ rc=0（这条在回退前后都不变，防止修 F1 时把 PASS 也改红）。"""
    assert _run_gate_all(monkeypatch, ["PASS", "PASS", "PASS", "PASS"]) == 0


def test_f1_fail_exit_one(monkeypatch):
    """有失败 ⇒ rc=1（回退不改这条，钉住优先级：失败码优先于跳过码）。"""
    assert _run_gate_all(monkeypatch, ["PASS", "FAIL", "PASS", "PASS"]) == 1


def test_f1_skip_is_not_zero(monkeypatch):
    """★F1 核心：无失败但有跳过 ⇒ rc 必须 ≠ 0（旧版折叠成 0，此条回退即红）。

    同时钉住与 0/1 都不同（约定为 2），让"4 门全验"与"只验 3 门"可区分。
    """
    rc = _run_gate_all(monkeypatch, ["PASS", "PASS", "PASS"], skip_art=True)
    assert rc != 0, "有跳过却返回 0 ⇒ 退出码折叠了 SKIP（#699 F1 回退）"
    assert rc == 2, "约定 PASS-WITH-SKIP 的退出码为 2，实为 %r" % rc


# ══════════════════════════════════════════════════════════════ F3 · raster.iou 空集
def test_f3_empty_vs_empty_is_undefined_not_one():
    """★F3：双空掩码 IoU 未定义 ⇒ 0.0 且 detail 显式标 undefined（旧版返回 1.0，回退即红）。"""
    import numpy as np
    from wm_bridge.factory.vec import raster as RA

    z = np.zeros((8, 8), dtype=bool)
    v, det = RA.iou(z, z)
    assert v == 0.0, "双空掩码 IoU 应为 0.0（未定义），实为 %r（#699 F3 回退）" % v
    assert det.get("undefined") is True, "detail 必须显式标 undefined 让消费方可判读：%r" % det


def test_f3_nonempty_unchanged():
    """F3 修复不得误伤非空语义：同掩码 =1.0、不相交 =0.0（无 undefined 标记）。"""
    import numpy as np
    from wm_bridge.factory.vec import raster as RA

    ones = np.ones((8, 8), dtype=bool)
    v1, d1 = RA.iou(ones, ones)
    assert v1 == 1.0 and not d1.get("undefined")
    a = np.zeros((8, 8), dtype=bool)
    b = np.zeros((8, 8), dtype=bool)
    a[0, 0] = True
    b[7, 7] = True
    v2, d2 = RA.iou(a, b)
    assert v2 == 0.0 and not d2.get("undefined"), "真的 0 与'未定义'必须可区分：%r" % d2


# ══════════════════════════════════════════════════════════════ F7 · 恒真式
def test_f7_no_tautological_or_true_in_bpy_shade():
    """★F7：`bpy_shade.py` 的 `chk(...)` 条件里不许出现 `or True` 恒真式（回退即红）。

    静态判：全模块**任何** `or True`（BoolOp/Or 且右操作数是常量 True）都判红 —— 这正是
    原缺陷形态；修好后模块内 0 命中。
    """
    tree = _tree(_BPY_SHADE)
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            for v in node.values[1:]:
                if isinstance(v, ast.Constant) and v.value is True:
                    hits.append(getattr(node, "lineno", "?"))
    assert not hits, "bpy_shade.py 仍含 `or True` 恒真式（行 %s）⇒ #699 F7 回退" % hits


def test_f7_raw_png_sha_check_is_a_real_comparison():
    """F7 更精确的钉法：那条 check 的条件必须是**真实比较**（ast.Compare），不是 BoolOp。"""
    tree = _tree(_BPY_SHADE)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.args:
            f = node.func
            name = getattr(f, "id", None) or getattr(f, "attr", None)
            if name == "chk" and isinstance(node.args[0], ast.Constant) \
                    and node.args[0].value == "raw_png_sha_unequal_due_to_metadata":
                found.append(node.args[1])
    assert found, "找不到 raw_png_sha_unequal_due_to_metadata 这条 chk（被删/改名？）"
    for cond in found:
        assert isinstance(cond, ast.Compare), \
            "该 check 的条件不是真实比较（%s）⇒ 恒真式未除" % type(cond).__name__


# ══════════════════════════════════════════════════════════════ F8 · 缓存 sha 来源
def _tiny_png(path):
    """写一张 1x1 RGBA 的最小合法 PNG（用被守卫的 pngmetrics 自己编码）。"""
    from wm_bridge.factory.shade._det import pngmetrics as pm
    b = pm.encode_canonical(1, 1, 4, bytes([10, 20, 30, 255]))
    with open(path, "wb") as fh:
        fh.write(b)
    return b


def test_f8_cache_hit_is_flagged(tmp_path):
    """★F8：`keep_if_exists=True` 且 dst 已存在 ⇒ 返回**既有文件**的 sha，且标 `from_cache=True`。

    回退即红：旧版返回里没有 `from_cache` 键（陈旧缓存冒充本次指纹而无法判读）。
    """
    from wm_bridge.factory.shade._det import pngmetrics as pm

    src = str(tmp_path / "src.png")
    dst = str(tmp_path / "canon.png")
    src_bytes = _tiny_png(src)
    stale = b"\x89PNG\r\n\x1a\n" + b"STALE-NOT-THE-REAL-CANON"
    with open(dst, "wb") as fh:
        fh.write(stale)

    r = pm.canonical_file(src, dst, keep_if_exists=True)
    assert "from_cache" in r, "返回值缺 from_cache ⇒ 缓存命中无法判读（#699 F8 回退）"
    assert r["from_cache"] is True
    # 缓存命中时 sha 是 dst 既有内容（≠ 本次 src 重编码结果）——这正是缺陷要**标出来**的事实
    import hashlib
    assert r["sha256"] == hashlib.sha256(stale).hexdigest()
    assert r["sha256"] != hashlib.sha256(src_bytes).hexdigest()
    # dst 未被覆写（keep_if_exists 语义不变）
    assert open(dst, "rb").read() == stale


def test_f8_fresh_encode_is_flagged_false(tmp_path):
    """非缓存路径：`from_cache=False`，且 sha 就是本次由 src 重编码的字节哈希（= canonical_sha256）。"""
    from wm_bridge.factory.shade._det import pngmetrics as pm

    src = str(tmp_path / "src.png")
    dst = str(tmp_path / "canon.png")
    _tiny_png(src)
    r = pm.canonical_file(src, dst, keep_if_exists=True)   # dst 不存在 ⇒ 走重编码
    assert r["from_cache"] is False
    assert r["sha256"] == pm.canonical_sha256(src)


# ══════════════════════════════════════════════════════════════ F15 · __all__ 未定义名
def test_f15_all_names_are_defined():
    """★F15：`bpy_shade.__all__` 里每个名字都必须在模块级有定义（旧版含 `setup_studio`，回退即红）。"""
    tree = _tree(_BPY_SHADE)
    defined = _module_level_names(tree)
    allv = _all_list(tree)
    missing = [x for x in allv if x not in defined]
    assert not missing, "`__all__` 含模块级未定义名 %s ⇒ `import *` 会 AttributeError（#699 F15）" % missing
    assert "setup_studio" not in allv, "`setup_studio` 无实现，不应留在 `__all__`"


def test_f15_all_has_no_duplicates_and_is_nonempty():
    """顺带钉住 `__all__` 本身健康（非空、无重复）——防止'删名'时误伤整表。"""
    allv = _all_list(_tree(_BPY_SHADE))
    assert allv, "`__all__` 不应为空"
    assert len(allv) == len(set(allv)), "`__all__` 有重复项：%s" % allv


# ══════════════════════════════════════════════════════════════ F16 · dict 当 list
def test_f16_render_frames_result_is_indexed_not_len():
    """★F16：`make_hex_gif.py` 里 `BA.render_frames(...)` 的结果必须**按 dict 取 `["paths"]`**，
    不许对其直接 `len(...)`（旧版 `len(paths)`=键数 2，回退即红）。

    静态判（本件模块级即执行 + `import bpy`，不能 import）：
      ① 找到 `render_frames(...)` 调用的赋值目标名 `T`；
      ② 不许存在 `len(T)`；
      ③ 必须存在 `T["paths"]` 形式的取用。
    """
    tree = _tree(_MAKE_HEX_GIF)
    targets = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            f = node.value.func
            if getattr(f, "attr", None) == "render_frames":
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        targets.append(t.id)
    assert targets, "找不到 `render_frames(...)` 的赋值（被删/改名？）"

    bad_len = []
    has_paths = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "len" and len(node.args) == 1 \
                and isinstance(node.args[0], ast.Name) and node.args[0].id in targets:
            bad_len.append(getattr(node, "lineno", "?"))
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) \
                and node.value.id in targets:
            sl = node.slice
            if isinstance(sl, ast.Constant) and sl.value == "paths":
                has_paths.add(node.value.id)

    assert not bad_len, "对 render_frames 的 dict 结果直接 `len(...)`（行 %s）⇒ #699 F16 回退" % bad_len
    assert has_paths, "未按契约取 `result[\"paths\"]` ⇒ 帧数仍会取错（#699 F16 回退）"

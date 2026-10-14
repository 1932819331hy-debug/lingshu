# -*- coding: utf-8 -*-
"""wm_bridge.factory._deps · **外部依赖的显式解析**（纯标准库）

为什么需要它
------------
本桥的立身之本是「**不 import `lingshu`**，只通过**工件**（逐 tick 真值 + OBJ/SHA-256）
对接」。但实测有**两处**确实需要上游的**物理预测**模块：
    · `anim/_det/make_demo_gif.py`（产出物理动画成品）
    · `anim/_det/gate_physics.py`（物理预测的机械门）
而它们原来的做法分别是：前者写了一个**机器专属的绝对路径**
`_LING = "<家目录>/DSH/测试/测试/wm-verify/lingshu"`，
后者干脆 import 一个**从未随桥发布**的 `wm_bridge.factory.anim.physics`
⇒ 结果是**门一直是死的**（启动即 `ImportError`），而"物理验证"看起来却像存在。

这里把这条依赖**显式化、可配置、且找不到就明确报错**，不再有机器路径与幽灵模块：
    `WM_LINGSHU_ROOT`（环境变量，优先）
    → 若干**仓库相对**候选路径
    → 都找不到 ⇒ `DepError`（**明确失败**，不静默降级）
"""
from __future__ import annotations

import os
import sys

__all__ = ["DepError", "lingshu_root", "import_physics"]


class DepError(RuntimeError):
    """外部依赖找不到 / 不可导入。**这是硬错误**：调用方应让它上抛，不要静默降级。"""


#: 从本文件所在目录**逐级向上**找的候选（`<祖先>/wm-verify/lingshu`）。
#: ⚠ 第一版把候选写成固定的 `../wm-verify/lingshu`，但本文件在 `wm_bridge/factory/`，
#: 到工作区根要上 **三级** ⇒ 固定相对路径算错了两级、直接找不到。逐级向上更稳，
#: 也不依赖"桥被放在工作区根下"这个假设。
_UP_LEVELS = 6
_REL_TAIL = ("wm-verify", "lingshu")


def lingshu_root():
    """上游 `lingshu` 源码树的根（含 `lingshu/world/physics.py`）。找不到 ⇒ `DepError`。"""
    env = os.environ.get("WM_LINGSHU_ROOT")
    if env:
        p = os.path.abspath(os.path.expanduser(env))
        if os.path.isfile(os.path.join(p, "lingshu", "world", "physics.py")):
            return p
        raise DepError("WM_LINGSHU_ROOT=%r 下没有 lingshu/world/physics.py" % env)
    here = os.path.dirname(os.path.abspath(__file__))
    tried = []
    up = here
    for _ in range(_UP_LEVELS):
        cand = os.path.join(up, *_REL_TAIL)
        tried.append(cand)
        if os.path.isfile(os.path.join(cand, "lingshu", "world", "physics.py")):
            return cand
        nxt = os.path.dirname(up)
        if nxt == up:
            break
        up = nxt
    raise DepError(
        "找不到上游 lingshu 源码树。请设 `WM_LINGSHU_ROOT=<lingshu 根>`；已逐级试过：\n  "
        + "\n  ".join(tried))


def import_physics():
    """解析并 import `lingshu.world.physics`（**唯一的物理预测来源**）。"""
    root = lingshu_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from lingshu.world import physics as _P
    except ImportError as exc:                                    # noqa: F841
        raise DepError("在 %s 下无法 import lingshu.world.physics：%s" % (root, exc))
    return _P

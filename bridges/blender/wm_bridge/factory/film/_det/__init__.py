# -*- coding: utf-8 -*-
"""wm_bridge.factory.film._det · 影视层判据门（§4.2 / §七 P1）

命名沿用既有 `factory/*/_det/` 约定（与 `factory/anim/_det/`、`factory/vec/_det/` 并列）。

本包首批实现（已裁 D5，2026-10-16）：
    gates_k.py   K 族连续性 K1–K5（G1 硬门）
    gates_e.py   E 族剪辑 E1 卡点对齐（G1 硬门）；E2–E5 登记暂不实现

其余五族（C 构图 / M 运镜 / P 表演 / A 氛围 / T 文本）**登记但暂不实现**，
随 §七 各阶段补依赖与真值源后逐步补上 —— 登记表见架构文档 §4.2。

硬边界（与 film 包一致）：
    B4 本包**纯标准库**：不 import bpy / numpy / PIL（可在无 Blender 机器上完整自测）。
    判据门**只读**输入束，不写任何产物；裁定结果由调用方交给 `wmfilm.oracle/1`。

★ 本包与既有 _det/ 的关系
    既有 `factory/anim/_det/gate_gait_contact.py`、`gate_physics.py` 就是 §4.2 里的
    **P4 / P5**。判据库要做的是把它们**纳入统一编号与统一裁决**（`gate_all.py`），
    不是重写 —— 本版先不接线，登记在案。
"""
from __future__ import annotations

__all__ = ["scene", "gates_k", "gates_e", "gate_all"]

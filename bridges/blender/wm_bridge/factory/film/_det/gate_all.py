# -*- coding: utf-8 -*-
"""wm_bridge.factory.film._det.gate_all · 判据门统一入口（P1 首批）

职责（**只做三件事，不替判据下结论**）：
    1. 装配输入束（`scene.build_scene`）；
    2. 依次跑全部已实现判据，收集四态裁定；
    3. 把裁定装进 `wmfilm.oracle/1`（阈值登记表 + 判据读数），返回该文档。

★ 为什么阈值必须**集中登记**再随裁定一起出
    §4.1 的三级门靠「性质标注」决定能不能硬拦。标注若散落在各判据的实现里，
    换个人读报告就看不到「这个 30° 是哪来的」。集中登记 + 强制 `source` 非空
    （由 `wmfilm.oracle/1` 契约校验）使「凭什么定这个数」成为**可复核的字段**，
    而不是注释里的一句自述。

★ 失败传播
    任何判据抛异常（输入束结构非法等）⇒ 该判据记为 **BLINDSPOT** 并带上异常文本，
    **不吞掉**、也不让整批崩掉 —— 一条判据的输入坏了，不该让其余判据的读数一起消失。
"""
from __future__ import annotations

from .. import oracle as OR
from . import gates_e as GE
from . import gates_k as GK
from . import scene as S

#: 全部已实现的判据（顺序即报告顺序）
CHECKS = GK.ALL_K + GE.ALL_E

#: 在**契约层**已硬拦、因而不进 §4.2 判据登记的校验（诚实登记，避免「看起来没做」）。
#: 这些不是判据族的成员 —— §4.2 的六族只有 C/M/K/E/A/P。
SCHEMA_ENFORCED = (
    ("T1", "CPS 上限", "wmmv.text/1 硬拦（附录已录：成人 CPS <= 20）"),
    ("T2", "行宽 / 行数", "wmmv.text/1 硬拦（附录已录：拉丁 <= 42 字符/行，同屏 <= 2 行）"),
    ("T3", "安全区", "wmmv.text/1 硬拦（EBU R95：title 0.80 / action 0.90）"),
    ("M2", "缓动合规", "wmfilm.shot/1 硬拦（easing 须为 4 个控制点，x ∈ [0,1]）"),
    ("E4", "转场闭集", "wmmv.edit/1 / wmfilm.shot/1 硬拦（TRANSITION_KINDS 闭集）"),
)

#: 本批登记但未实现的判据（§4.2「实现状态」列）—— 显式 DEFER，不留空白
DEFERRED = (
    ("C1", "主体覆盖率", "登记暂不实现（需渲染真值）"),
    ("C2", "三分法偏差", "登记暂不实现（需渲染真值）"),
    ("C3", "视觉平衡", "登记暂不实现（风格 → G3）"),
    ("C4", "头部空间", "登记暂不实现（经验 → G2）"),
    ("C5", "引导空间", "登记暂不实现（经验 → G2）"),
    ("C6", "地平线位置", "登记暂不实现（经验 → G2）"),
    ("C7", "纵深层次", "登记暂不实现（需渲染真值）"),
    ("C8", "剪影可读性", "登记暂不实现（需渲染真值）"),
    ("M1", "轨迹平滑", "登记暂不实现"),
    ("M2", "缓动合规", "登记暂不实现（闭集已在 wmfilm.shot/1 schema 层先拦）"),
    ("M3", "主体在画内", "登记暂不实现（需渲染真值）"),
    ("M4", "视差区分", "登记暂不实现"),
    ("M5", "跟拍一致性", "登记暂不实现"),
    ("M6", "推拉-情绪同号", "登记暂不实现（风格 → G3）"),
) + tuple((n, d, r) for n, d, r in GE.DEFERRED_E) + (
    ("P1", "三段结构", "登记暂不实现（契约面已由 wmfilm.perform/1 保证三段衔接）"),
    ("P2", "重叠动作", "登记暂不实现"),
    ("P3", "动作线连续", "登记暂不实现"),
    ("P4", "接触约束", "登记暂不实现（既有 factory/anim/_det/gate_gait_contact.py 已实现，待纳入统一裁决）"),
    ("P5", "物理一致性", "登记暂不实现（既有 factory/anim/_det/gate_physics.py 已实现，待纳入统一裁决）"),
    ("P6", "moving hold", "登记暂不实现（经验 → G2）"),
    ("A1", "三点布光存在", "登记暂不实现"),
    ("A2", "冷暖对撞", "登记暂不实现"),
    ("A3", "对比度", "登记暂不实现（需渲染真值）"),
    ("A4", "影调档位", "登记暂不实现（需渲染真值）"),
    ("A5", "色相-情绪映射", "登记暂不实现（风格 → G3，不得进 G1）"),
)


def all_thresholds():
    """全部判据登记的阈值（去重后按名字排序）。"""
    seen, out = set(), []
    for name, value, source in GK.THRESHOLDS + GE.THRESHOLDS:
        if name in seen:
            continue
        seen.add(name)
        out.append({"name": name, "value": value, "source": source})
    return out


def run_all(sc):
    """跑全部已实现判据，返回 (checks, deferred) —— 均为可喂给 oracle 的条目。"""
    S.reject_unknown_scene(sc)
    checks = []
    for fn in CHECKS:
        try:
            checks.append(fn(sc))
        except Exception as exc:                      # noqa: BLE001 —— 见模块头「失败传播」
            nm = fn.__name__.replace("check_", "").upper()
            checks.append({"name": nm, "verdict": "BLINDSPOT", "measured": [],
                           "reason": "判据执行失败（输入束非法？）：%s: %s"
                                     % (type(exc).__name__, exc)})
    for nm, desc, why in DEFERRED:
        checks.append({"name": nm, "verdict": "DEFER", "measured": [],
                       "reason": "%s —— %s" % (desc, why)})
    return checks, DEFERRED


def build_oracle(sc, oracle_id, subject_name, subject_digest,
                 generated_by="wm_bridge.factory.film._det.gate_all"):
    """装配 `wmfilm.oracle/1` 裁定文档（**已过契约校验**，可直接落盘）。

    生效条件：`sc` 是合法输入束 ⇒ 返回规范化后的 oracle dict；
              输入束结构非法 ⇒ raise `scene.SceneError`（fail-closed，不产半成品）。
    """
    checks, _deferred = run_all(sc)
    doc = {"schema": OR.ORACLE_SCHEMA, "id": oracle_id,
           "subject_ref": {"name": subject_name, "digest": subject_digest},
           "thresholds": all_thresholds(), "checks": checks,
           "generated_by": generated_by}
    return OR.normalize_oracle(doc)


def summarize(oracle_doc):
    """一行汇总（供 CLI 与守卫测试打印）。"""
    n = OR.normalize_oracle(oracle_doc)
    ok, bad = OR.gates_passed(n)
    return {"counts": OR.counts_by_verdict(n), "gates_passed": ok, "blocking": bad,
            "digest": OR.oracle_digest(n)}

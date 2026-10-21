# -*- coding: utf-8 -*-
"""wm_bridge.factory.film._det.gates_e · E 族判据门（剪辑 · G1 硬门）

本批只实现 **E1 卡点对齐**（§4.2 E 族表；已裁 D5：首批 = K 族 + E1）。
E2–E5 登记暂不实现 —— 它们随 §七 P4/P5 补依赖与真值源后逐步实现。

★ E1 为什么能进首批
    它是剪辑族里**唯一有明确数值阈值（≤ 1 帧）**的判据：阈值来自「24 fps 下 1 帧 ≈ 42 ms，
    超出即被感知为「慢了半拍」」这一共识，不需要风格裁定，也不需要渲染真值 ——
    只要有 `wmmv.edit/1`（切点）与 `wmmv.score/1`（强拍），就能算。
    它是「判据库」这条路成立的最短证明。

★ 时间基准（**必须写明，否则读数不可复算**）
    切点帧号 f 换算为秒：`t = (f − edit.frame_base.frame_start) / fps + score.tempo.offset_s`。
    `score.beats` / `score.downbeats` 的秒数即以此同一原点为基准（乐谱契约已强制拍点非负递增）。
    `tempo.offset_s` 参与换算，是因为它正是「音乐第 0 拍相对素材起点的偏移」——
    漏掉它会让整片系统性偏一个固定量，而那正是本判据要抓的东西。

★ 容差的来源（**不是本模块发明**）
    优先取 `wmmv.edit/1` 中该切点前一个镜头 `transition_out.tolerance_frames` 的**声明值**；
    未声明时回落到阈值登记表里的 `E1_tolerance_frames`（默认 1 帧，来源 = §4.2 E 族表）。
    声明的容差**只收紧不放宽**：声明值大于登记值 ⇒ 以登记值为准（阈值登记表是唯一权威，
    否则每个剪辑都能自报一个宽松容差把门绕过去）。
"""
from __future__ import annotations

from .. import _common as C
from . import scene as S

#: 本模块登记的阈值
THRESHOLDS = (
    ("E1_tolerance_frames", 1.0,
     "架构文档 §4.2 E 族表 E1：切点到最近 downbeat 的偏差 ≤ 1 帧（共识 → G1）"),
)

#: E1 的不适用条件（negative）
E1_NEGATIVE = (
    "自由节奏段落（rubato / 无明确强拍的氛围铺底）中，downbeat 本身不可靠，"
    "此时 E1 的读数只反映「乐谱分析是否稳定」，不反映剪辑好坏。",
    "score 未声明 downbeats（只有 beats）时不可判 —— 本版一律 BLINDSPOT，"
    "不用 beats 冒充 downbeats（强弱拍不是一回事）。",
    "镜头内运动需要跨切点连续（如 match_cut 的承接动作）时，切点可能刻意偏离强拍，"
    "此时应改由 E4/E5 判，E1 不适用。",
)


def check_e1(sc):
    """E1 卡点对齐：每个切点到最近 downbeat 的偏差 ≤ 容差（帧）。"""
    name = "E1"
    ed = sc.get("edit")
    if ed is None:
        return {"name": name, "verdict": "BLINDSPOT", "measured": [],
                "reason": "scene.edit 未提供 —— 没有切点就无从判卡点对齐"}
    sc_score = sc.get("score")
    if sc_score is None:
        return {"name": name, "verdict": "BLINDSPOT", "measured": [],
                "reason": "scene.score 未提供 —— 没有乐谱（强拍）就无从判卡点对齐"}

    from .. import edit as ED
    from .. import score as SC
    n_ed = ED.normalize_edit(ed)
    n_sc = SC.normalize_score(sc_score)

    downbeats = n_sc["downbeats"]
    if not downbeats:
        return {"name": name, "verdict": "BLINDSPOT", "measured": [],
                "reason": "score.downbeats 为空 —— 不得用 beats 冒充强拍（强弱拍不是一回事）"}

    fps = n_ed["frame_base"]["fps"]
    f0 = n_ed["frame_base"]["frame_start"]
    offset_s = n_sc["tempo"]["offset_s"]
    tol_reg = THRESHOLDS[0][1]

    # 切点 -> 声明的容差（取该切点**前一个镜头**的 transition_out 声明值）
    decl = {}
    for a in n_ed["shots"][:-1]:
        t = a["transition_out"]
        if "tolerance_frames" in t:
            decl[a["end_frame"] + 1] = float(t["tolerance_frames"])

    cuts = ED.cut_frames(n_ed)
    if not cuts:
        return {"name": name, "verdict": "DEFER", "measured": [],
                "reason": "时间轴只有一个镜头，无切点可判"}

    readings, bad, tol_used = [], [], []
    for f in cuts:
        t = (f - f0) / fps + offset_s
        db = min(downbeats, key=lambda d: abs(d - t))
        off = (t - db) * fps
        tol = min(tol_reg, decl.get(f, tol_reg))     # 声明只收紧不放宽
        tol_used.append(tol)
        readings.append(round(off, 6))
        if abs(off) > tol + 1e-9:
            bad.append("第 %d 帧偏 %+.2f 帧（容差 %.1f）" % (f, off, tol))

    max_off = max(abs(x) for x in readings)
    readings = [float(len(cuts)), float(len(cuts) - len(bad)),
                float(max_off)] + readings
    if bad:
        return {"name": name, "verdict": "REJECT", "measured": readings,
                "threshold": "E1_tolerance_frames",
                "reason": "切点未落在强拍容差内：%s" % "；".join(bad),
                "evidence": "读数格式：[切点数, 对齐数, 最大绝对偏差帧, 各切点偏差帧…]"}

    extra = ""
    if any(t < tol_reg for t in tol_used):
        extra = "（部分切点采用 edit 声明的更紧容差 %.1f 帧）" % min(tol_used)
    return {"name": name, "verdict": "ACCEPT", "measured": readings,
            "threshold": "E1_tolerance_frames",
            "evidence": "%d 个切点全部落在强拍容差内，最大偏差 %.3f 帧%s"
                        % (len(cuts), max_off, extra)}


#: 登记暂不实现（§4.2 E 族表「实现状态」列）
DEFERRED_E = (
    ("E2", "ASL-能量负相关", "风格 → G3（只登记，供人裁）"),
    ("E3", "段落边界可检出", "经验 → G2（需切点密度与段落真值）"),
    ("E4", "转场闭集", "共识 → G1（闭集已在 wmmv.edit/1 schema 层先拦）"),
    ("E5", "结构完整", "经验 → G2（需乐谱段落时长）"),
)

ALL_E = (check_e1,)

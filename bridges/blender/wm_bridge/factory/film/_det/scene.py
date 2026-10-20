# -*- coding: utf-8 -*-
"""wm_bridge.factory.film._det.scene · 判据门的输入束与几何助手（纯标准库）

★ 这一份不是对外契约
    对外契约是 `wmfilm.*` / `wmmv.*` 七份（§3.2）。本模块是**判据门的内部输入束** ——
    把「判据要吃的三样东西」装配到一起：**声明**（契约）、**事实**（真值回读）、
    **口径**（阈值与来源）。它不是文件格式，不落盘、不进 manifest。

★ 为什么判据不能只吃契约
    §六.2 已经把这个道理写死了：`coverage_target = 0.68` 是**意图**，
    `coverage_actual = 0.671` 是**事实**。K 族判「越轴 / 视线 / 屏幕方向 / 30° / 景别」，
    吃的是**事实**。拿意图判事实 = 自己评自己（wm_bridge/DESIGN.md 吃过的亏）。

★ 判据缺料时的行为（**这是本设计的核心**）
    判据**不得**因为缺料而沉默通过。缺料一律产出 **BLINDSPOT**（跑了但判不了）
    并写明缺的是哪个字段 —— 于是「缺什么」变成可归档、可驱动下一轮补契约的读数，
    而不是一个永远为绿的伪判据。
"""
from __future__ import annotations

import math

from .. import _common as C

#: 输入束允许的键（白名单；未知键即拒 —— 与对外契约同一 fail-closed 口径）
SCENE_KEYS = ("shots", "edit", "score", "axis", "truth", "frame_size")

#: 真值条目允许的键（§六.1 的回读字段；`measured` 内的子键见 MEASURED_KEYS）
TRUTH_KEYS = ("shot_id", "frame", "measured", "manifest")

#: §六.1 列出的实测字段 + K 族额外需要的两个（**后者是缺口，见 K2/K3 的 BLINDSPOT 理由**）
MEASURED_KEYS = ("coverage_actual", "camera_actual", "subject_bbox_screen",
                 "silhouette_delta_L", "histogram", "color_temp_est_k",
                 # ↓ 下面两个 §六.1 未列，是 K2/K3 判据暴露出的契约缺口
                 "subject_gaze_screen", "subject_motion_screen")

#: 画幅基准（已裁 D4：16:9，1920x1080）
DEFAULT_FRAME_SIZE = (1920, 1080)

#: 世界坐标「上」方向（右手系，Z 向上 —— 与 geo/spec.py 的坐标口径一致）
UP = (0.0, 0.0, 1.0)


class SceneError(C.ContractError):
    """判据门的输入束不合法（fail-closed）。"""


# ─────────────────────────────────────────────────────────── 装配
def build_scene(shots=None, edit=None, score=None, axis=None, truth=None,
                frame_size=None):
    """装配输入束。**不做语义校验** —— 语义留给各判据门（各门自己判缺什么）。

    参数：
      shots      {shot_id: wmfilm.shot/1 规范化 dict}
      edit       wmmv.edit/1 规范化 dict
      score      wmmv.score/1 规范化 dict
      axis       {"a": [x,y,z], "b": [x,y,z]} 动作轴线（两点定一线）
      truth      [§六.1 真值条目]
      frame_size [w, h] 像素；缺省 (1920, 1080)（已裁 D4）
    """
    sc = {}
    if shots is not None:
        sc["shots"] = shots
    if edit is not None:
        sc["edit"] = edit
    if score is not None:
        sc["score"] = score
    if axis is not None:
        sc["axis"] = axis
    if truth is not None:
        sc["truth"] = truth
    if frame_size is not None:
        sc["frame_size"] = frame_size
    return sc


def reject_unknown_scene(sc):
    if not isinstance(sc, dict):
        raise SceneError("scene 须为对象，实为 %s" % type(sc).__name__)
    C.reject_unknown(sc, SCENE_KEYS, "scene")


def frame_size_of(sc):
    """取画幅像素尺寸；未声明 ⇒ 已裁 D4 的 1920x1080。"""
    fs = sc.get("frame_size")
    if fs is None:
        return DEFAULT_FRAME_SIZE
    if not isinstance(fs, (list, tuple)) or len(fs) != 2:
        raise SceneError("scene.frame_size 须为 [w, h]，实为 %r" % (fs,))
    w, h = fs
    if not (C.is_num(w) and C.is_num(h)) or float(w) <= 0 or float(h) <= 0:
        raise SceneError("scene.frame_size 须为两个正数，实为 %r" % (fs,))
    return (float(w), float(h))


def shots_of(sc):
    """取 shot_id -> shot 契约映射；未声明 ⇒ 空 dict（判据各自报缺料）。"""
    s = sc.get("shots")
    if s is None:
        return {}
    if not isinstance(s, dict):
        raise SceneError("scene.shots 须为 {shot_id: shot}，实为 %s" % type(s).__name__)
    return s


def truth_of(sc):
    """取真值条目列表；未声明 ⇒ 空列表。"""
    t = sc.get("truth")
    if t is None:
        return []
    if not isinstance(t, list):
        raise SceneError("scene.truth 须为列表，实为 %s" % type(t).__name__)
    return t


def truth_at(sc, shot_id):
    """取某镜头的真值条目（按帧升序）。"""
    out = []
    for i, e in enumerate(truth_of(sc)):
        if not isinstance(e, dict):
            raise SceneError("scene.truth[%d] 须为对象，实为 %s" % (i, type(e).__name__))
        C.reject_unknown(e, TRUTH_KEYS, "scene.truth[%d]" % i)
        if e.get("shot_id") == shot_id:
            out.append(e)
    return sorted(out, key=lambda e: e.get("frame", 0))


def measured(e):
    """取真值条目的 measured 映射（缺失 ⇒ 空 dict）。"""
    m = e.get("measured")
    if m is None:
        return {}
    if not isinstance(m, dict):
        raise SceneError("truth.measured 须为对象，实为 %s" % type(m).__name__)
    C.reject_unknown(m, MEASURED_KEYS, "truth.measured")
    return m


# ─────────────────────────────────────────────────────────── 几何
def _sub(p, q):
    return (float(p[0]) - float(q[0]), float(p[1]) - float(q[1]),
            float(p[2]) - float(q[2]))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a):
    return math.sqrt(_dot(a, a))


def axis_dir(axis):
    """由两点取轴线方向单位向量；两点重合 ⇒ raise（零长轴线无意义）。"""
    if not isinstance(axis, dict):
        raise SceneError("scene.axis 须为 {a, b}，实为 %s" % type(axis).__name__)
    C.reject_unknown(axis, ("a", "b"), "scene.axis")
    a = C.vec3(axis.get("a"), "scene.axis.a")
    b = C.vec3(axis.get("b"), "scene.axis.b")
    d = _sub(b, a)
    n = _norm(d)
    if n <= 1e-12:
        raise SceneError("scene.axis 的两点重合（零长轴线），实为 %r" % (axis,))
    return (d[0] / n, d[1] / n, d[2] / n)


def axis_side(axis, point, up=UP):
    """点相对轴线落在哪一侧：+1 / -1 / 0（点恰在轴线上）。

    计算式：`sign( (dir × up) · (point − a) )`
      —— 把轴线方向与世界上方向叉乘得到**轴线在水平面内的法线**，
         再把「点到轴线的向量」投影到该法线上取符号。这是 180° 规则的直接形式化：
         相机在轴线哪一侧，决定了画面上主体的朝向。
    """
    d = axis_dir(axis)
    a = C.vec3(axis.get("a"), "scene.axis.a")
    n = _cross(d, up)
    if _norm(n) <= 1e-12:
        raise SceneError("轴线与世界上方向平行，无法定义侧向（轴线须近似水平）")
    v = _dot(n, _sub(point, a))
    if abs(v) <= 1e-9:
        return 0
    return 1 if v > 0 else -1


def angle_at(target, p, q):
    """在 target 处看 p 与 q 的夹角（度）。任一向量退化 ⇒ raise。"""
    v1, v2 = _sub(p, target), _sub(q, target)
    n1, n2 = _norm(v1), _norm(v2)
    if n1 <= 1e-12 or n2 <= 1e-12:
        raise SceneError("angle_at 的向量退化（机位与瞄准点重合）")
    c = _dot(v1, v2) / (n1 * n2)
    c = max(-1.0, min(1.0, c))
    return math.degrees(math.acos(c))


def centroid(bbox):
    """包围盒 [x0, y0, x1, y1] 的中心。"""
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise SceneError("subject_bbox_screen 须为 [x0,y0,x1,y1]，实为 %r" % (bbox,))
    for i, v in enumerate(bbox):
        if not C.is_num(v):
            raise SceneError("subject_bbox_screen[%d] 须为有限数值，实为 %r" % (i, v))
    return ((float(bbox[0]) + float(bbox[2])) / 2.0,
            (float(bbox[1]) + float(bbox[3])) / 2.0)


def bbox_height_frac(bbox, frame_size):
    """主体高度占画幅高的比例（景别判据的读数）。"""
    h = float(frame_size[1])
    if h <= 0:
        raise SceneError("frame_size[1] 须为正，实为 %r" % (h,))
    return abs(float(bbox[3]) - float(bbox[1])) / h


def rel_diff(x, y):
    """相对差 |x−y| / max(|x|,|y|)；两者皆 0 ⇒ 0.0。"""
    x, y = abs(float(x)), abs(float(y))
    m = max(x, y)
    if m <= 1e-12:
        return 0.0
    return abs(x - y) / m


def first_camera(shot):
    """取镜头相机路径的首点（判 30° 与轴线时用首帧机位）。"""
    return shot["camera"]["path"][0]


def mean_point(p, q):
    return ((float(p[0]) + float(q[0])) / 2.0, (float(p[1]) + float(q[1])) / 2.0,
            (float(p[2]) + float(q[2])) / 2.0)


def dist(a, b):
    return _norm(_sub(a, b))

# -*- coding: utf-8 -*-
"""守卫 · #8 / #175 / #310 / #376 / #380 · lingshu/world/silhouette3d.py（本组五件合一）
============================================================================
缺陷态（取证 = 修复前 HEAD `777e18f`，行号为该基线）：

#8   [口径] 模块 docstring 自称「真 3D」（`:7` `- 旋转视角时部件产生视差位移 +
     画家算法遮挡 —— 真 3D。`），但表示层是**零厚度平面多边形**（`:37`
     `points: List[Tuple[float, float]]`；`:117` `_to_3d` 给整部件一个常数 z）+
     画家算法排序（`:170`）。实测前视/背视逐像素 98.19% 相同、侧视面积降到 13%
     —— 是 2.5D 分层视差，不是三维体。判据：报告 #8（口径问题，作者自证）。
     本组文件面只含 `silhouette3d.py`；#8 的另一半（`world3d.DEFAULT_VISUAL_SPECS`
     先验表登记）在 `world3d.py`，不在本文件面内。

#175 [渲染] 画家算法排序与着色都用**固定** `part.depth`（`:170`
     `sorted(projected, key=lambda x: -x[0].depth)`；`:171`
     `f = self._depth_factor(self.center[2] + part.depth)`），与传入 `camera`
     脱钩——只在默认相机（`eye = center − 3·z`）下「正 = 远离相机」成立。
     判据来源：docs/theory/世界模型与语义时空图_完整理论整理与实现路线.md §3.2
     投影-关联（遮挡/尺度须按**相机坐标** z，与 `Camera3D.project` 同一变换）；
     与 `world3d.Object3D.depth`（#228 已修）同口径。

#310 [命名] `fatfish_skeleton` 把「髋部阴影」与「大腿」都命名为 `hip_l`/`hip_r`
     （`:673`/`:719`）；`joint` 命中第一个同名部件即 `break`（`:78-81`）⇒
     `joint("hip_l", …)` 只转到 pivot=None 的阴影，腿永远摆不动（整图零像素差）。
     判据：报告 #310（同名冲突；其修复建议即改名为 `hip_shade_*`）。

#376 [幂等] `set_expression` 从**当前眼型**量 `rx`/`ry` 再重建眼型
     （`:858-859` + `:873`），高光 `+ dy` 增量平移（`:884`）——无原始基准 ⇒
     每次调用以上次结果为输入，是**几何级数**（两模板 × 6 表情 = 12/12 不幂等，
     `angry` 十次归零、`surprised` 膨胀）。判据：报告 #376（经验标定，追不到理论
     章节）；修复取向＝「调用前快照基准」（报告建议 1）。

#380 [声明] `apply_pose` 的 docstring 逐字声明兼容 `hip_l/knee_l/thigh_l/calf_l`，
     候选表也列入腿的 8 个名字（`:907-910`），但五个姿态分支只给臂/尾设值
     （`setj(...)` 仅 `shoulder_*`/`upper_*` 与 `elbow_*`/`lower_*`）⇒ 腿只能被归零。
     判据：报告 #380（经验标定，追不到理论章节——仓内无「姿态该驱动哪些关节」的
     理论规定）。修复取向＝**收窄声明**（`_ZERO_JOINTS` / `_POSE_DRIVEN_JOINTS` 拆分，
     本件 docstring 与实现同一份数据），不凭空发明腿部姿态语义。

跨批覆盖重叠（派工书第 12 条）：
  · 既有 `tests/test_issue404_silhouette3d_joint_chain.py` 管**关节父子链**、
    `tests/test_expression_render_guard.py` 管**腮红颜色 + 高光相对间距**；
    本件五项均不落在那两条的判据面内（下表为「只让本件机制生效」的夹具）：
      - #175 夹具 = 两部件仅 depth 不同、相机绕到背后 ⇒ 中心像素须换成近者
        （#404 的链守卫与相机无关，改链不影响本件读数）；
      - #310 夹具 = 部件名唯一性 + 「带 pivot 的大腿叫 hip_l」（#404 只查 parent 链）；
      - #376 夹具 = **绝对几何**连调不变（既有守卫 D 组只查颜色与两两间距，
        在本件缺陷态下仍全绿——可复跑对照 `tests/test_expression_render_guard.py`）；
      - #380 夹具 = docstring 声明名 ⊆ 实跑被驱动名（#310 修好也不影响本件，
        反之亦然——两件机制不同，见 #380 报告「判别实验」）。

变异自证（把修复抽回缺陷形态，下列断言分别变红；已逐条实跑，读数见提交说明）：
  · #8   docstring 改回「真 3D」 ⇒ test_8_* 红；
  · #175 排序/着色改回 `part.depth` ⇒ test_175_back_view_occlusion / _shading 红；
  · #310 阴影名改回 `hip_l`/`hip_r` ⇒ test_310_* 红；
  · #376 去掉 `_expr_base` 快照、改回量当前几何 ⇒ test_376_* 红；
  · #380 docstring 改回声明腿 ⇒ test_380_* 红。

运行：python -X utf8 -m pytest tests/test_issue8_175_310_376_380_silhouette3d_guard.py -q --no-header
"""
from __future__ import annotations

import collections
import math
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.world import silhouette3d as S  # noqa: E402
from lingshu.world.world3d import Camera3D  # noqa: E402

CENTER = (0.0, 0.85, 5.0)
SQUARE = [(-0.2, -0.2), (0.2, -0.2), (0.2, 0.2), (-0.2, 0.2)]
MAKERS = ("fatfish_skinned", "fatfish_skeleton")
EXPRS = ("neutral", "happy", "sad", "angry", "surprised", "shy")
POSES = ("neutral", "happy", "shy", "sad", "angry", "tired")


def _part(s, name):
    return next((p for p in s.parts if p.name == name), None)


def _bbox(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (round(min(xs), 12), round(min(ys), 12), round(max(xs), 12), round(max(ys), 12))


def _center(pts):
    return (round(sum(p[0] for p in pts) / len(pts), 12),
            round(sum(p[1] for p in pts) / len(pts), 12))


def _geom(s):
    """眼/嘴/高光的**绝对几何**指纹（本件 #376 的量具）。"""
    snap = {}
    for p in s.parts:
        if p.name in ("eye_l", "eye_r", "mouth") or p.name.startswith("highlight_"):
            snap[p.name] = (_bbox(p.points), _center(p.points))
    return snap


# ===========================================================================
# #175 画家算法/着色必须跟随相机（不跟固定 part.depth）
# ===========================================================================
def _two_part_scene():
    """两部件**仅** depth 不同、轮廓完全重合的夹具（#175 最小复现）。"""
    s = S.Silhouette3D(center=(0.0, 1.0, 5.0))
    s.add_part(S.SilhouettePart("A", SQUARE, depth=0.0,
                                color=(255, 0, 0), outline=(255, 0, 0)))
    s.add_part(S.SilhouettePart("B", SQUARE, depth=0.2,
                                color=(0, 0, 255), outline=(0, 0, 255)))
    return s


def test_175_back_view_occlusion_follows_camera():
    """相机绕到背后 ⇒ 离相机更近的部件（depth 大的 B）必须盖住 A。

    缺陷态（固定 part.depth 排序）：前视/背视中心像素恒为 A 的红 (255,0,0)。
    """
    s = _two_part_scene()
    front = s.render(200, 200, camera=Camera3D.look_at(eye=(0, 1, 2), target=(0, 1, 5)))
    back = s.render(200, 200, camera=Camera3D.look_at(eye=(0, 1, 8), target=(0, 1, 5)))
    fpx, bpx = front.getpixel((100, 100)), back.getpixel((100, 100))
    # 前视：相机在 z=2（A 的 z=5 侧更近）⇒ A 在上
    assert fpx[0] > fpx[2], "前视中心像素应来自 A（红），实测 %r" % (fpx,)
    # 背视：相机在 z=8（B 的 z=5.2 侧更近）⇒ B 必须在上
    assert bpx[2] > bpx[0], (
        "背视中心像素应来自更近的 B（蓝），实测 %r ⇒ 遮挡顺序仍与相机脱钩" % (bpx,))


def test_175_depth_shading_follows_camera():
    """着色同样须跟随相机：从背后看时，前视口径下的「远」部件（depth 大）实际更近。

    夹具：单部件 `depth=+0.6`（> depth_span 0.30）。
      · 前视（相机 z=2）：它在相机深处 ⇒ 暗（0.55×255=140）；
      · 背视（相机 z=8）：它反而比角色中心更近 ⇒ 须亮（255）。
    缺陷态（`_depth_factor(self.center[2] + part.depth)` 与相机无关）：两种视角
    都暗（140）⇒ 本断言红。只修排序、不修着色时本断言单独变红。
    """
    def shade(depth, cam):
        t = S.Silhouette3D(center=(0.0, 1.0, 5.0))
        t.add_part(S.SilhouettePart("X", SQUARE, depth=depth,
                                    color=(0, 0, 255), outline=(0, 0, 255)))
        return t.render(200, 200, camera=cam).getpixel((100, 100))[2]

    back = Camera3D.look_at(eye=(0, 1, 8), target=(0, 1, 5))
    front = Camera3D.look_at(eye=(0, 1, 2), target=(0, 1, 5))
    far_front = shade(0.6, front)
    far_back = shade(0.6, back)
    assert far_front < 200, "前视：depth=+0.6 的部件应偏暗，实测 B 通道 %d" % far_front
    assert far_back > 200, (
        "背视：depth=+0.6 的部件离相机更近，应亮（255），实测 B 通道 %d ⇒ 着色未跟随相机"
        % far_back)


def test_175_default_camera_semantics_unchanged():
    """默认相机（eye = center − 3·z）下「depth 小 = 近」的语义不得改变。"""
    s = _two_part_scene()
    px = s.render(200, 200).getpixel((100, 100))     # camera=None → 默认相机
    assert px[0] > px[2], "默认相机下近者 A 应在上，实测 %r" % (px,)


def test_175_ordering_key_is_camera_depth_not_part_depth():
    """排序键必须是相机前向深度：非平凡位姿（yaw≠0）下与 part.depth 序**相反**。

    夹具：相机在 (-3,1,7) 看向 (0,1,5)；两部件只差 depth（A=0.0 / B=0.2），
    但沿相机视线方向 B（depth 大）反而更近（`za=3.606 > zb=3.495`）——与
    `part.depth` 给出的「A 近」**结论相反**。yaw 位姿下两部件投影不重合，
    故取「两者投影覆盖的交集像素」判定（不用单点中心像素）。
    """
    s = S.Silhouette3D(center=(0.0, 1.0, 5.0))
    s.add_part(S.SilhouettePart("A", SQUARE, depth=0.0,
                                color=(255, 0, 0), outline=(255, 0, 0)))
    s.add_part(S.SilhouettePart("B", SQUARE, depth=0.2,
                                color=(0, 0, 255), outline=(0, 0, 255)))
    cam = Camera3D.look_at(eye=(-3.0, 1.0, 7.0), target=(0.0, 1.0, 5.0))
    za = cam.depth_of(s._to_3d((0.0, 0.0), 0.0))
    zb = cam.depth_of(s._to_3d((0.0, 0.0), 0.2))
    assert abs(za - zb) > 1e-9, "夹具失效：两部件相机深度应不同（za=%.4f zb=%.4f）" % (za, zb)
    assert za > zb, "夹具失效：该位姿下应让 B（depth 大）在相机坐标下更近"

    def alone(name, color):
        t = S.Silhouette3D(center=(0.0, 1.0, 5.0))
        t.add_part(S.SilhouettePart(name, SQUARE, depth=(0.0 if name == "A" else 0.2),
                                    color=color, outline=color))
        return t.render(200, 200, camera=cam)

    bg = (250, 248, 244)
    a_img, b_img = alone("A", (255, 0, 0)), alone("B", (0, 0, 255))
    both = s.render(200, 200, camera=cam)
    hits = [(x, y) for x in range(200) for y in range(200)
            if a_img.getpixel((x, y)) != bg and b_img.getpixel((x, y)) != bg]
    assert hits, "夹具失效：两部件投影无交集像素"
    bad = [(p, both.getpixel(p)) for p in hits if both.getpixel(p)[2] <= both.getpixel(p)[0]]
    assert not bad, (
        "yaw 位姿下近者 B（蓝）应在全部交集像素上占优，实测 %d/%d 反了，例：%r"
        % (len(bad), len(hits), bad[:3]))


# ===========================================================================
# #310 fatfish_skeleton 部件不得重名；hip_l/hip_r 必须指大腿（带 pivot）
# ===========================================================================
def test_310_part_names_unique_in_both_templates():
    """两个模板的部件名都必须唯一（缺陷态：skeleton 的 hip_l/hip_r 各 2 个）。"""
    for maker in MAKERS:
        s = getattr(S, maker)()
        dup = {n: c for n, c in collections.Counter(p.name for p in s.parts).items() if c > 1}
        assert not dup, "%s 部件重名：%r ⇒ joint() 会命中先添加的同名部件" % (maker, dup)


def test_310_hip_joint_moves_the_thigh():
    """`joint("hip_l"/"hip_r", a)` 必须转动**大腿**（带 pivot 的那件）。

    缺陷态：命中 pivot=None 的髋部阴影 ⇒ angle 写入但顶点不动、整图零像素差。
    """
    for name, pivot in (("hip_l", "left"), ("hip_r", "right")):
        s = S.fatfish_skeleton()
        holders = [p for p in s.parts if p.name == name]
        assert len(holders) == 1, "部件 %s 应唯一，实测 %d 个" % (name, len(holders))
        thigh = holders[0]
        assert thigh.pivot is not None, "%s 应是大腿（有 pivot），实测 pivot=None" % name
        before = list(thigh.rotated_points())
        s.joint(name, 0.8)
        assert thigh.angle == 0.8, "%s.angle=%.3f，应为 0.8" % (name, thigh.angle)
        moved = max(math.dist(a, b) for a, b in zip(before, thigh.rotated_points()))
        assert moved > 1e-3, "%s 转 0.8 rad 后顶点位移仅 %.6f ⇒ 腿没动" % (name, moved)


def test_310_hip_joint_changes_rendered_pixels():
    """端到端：抬腿后渲染必须真的有像素变化（缺陷态 = 0 像素差）。"""
    neutral = S.fatfish_skeleton().render(300, 400)
    posed = S.fatfish_skeleton()
    posed.joint("hip_l", 0.8)
    posed.joint("hip_r", 0.8)
    img = posed.render(300, 400)
    diff = sum(1 for x in range(300) for y in range(400)
               if neutral.getpixel((x, y)) != img.getpixel((x, y)))
    assert diff > 0, "抬腿后渲染像素差为 0 ⇒ 姿态未进入渲染路径"


def test_310_hip_shadow_still_present_under_new_name():
    """夹具：髋部阴影必须仍存在（改名为 hip_shade_*）——防止「删阴影」冒充修复。"""
    s = S.fatfish_skeleton()
    shade = _part(s, "hip_shade_l")
    assert shade is not None, "髋部阴影缺失（不得靠删部件让唯一性变绿）"
    assert shade.pivot is None, "髋部阴影不应带 pivot"
    # 阴影与大腿同深度量级：存在且是椭圆轮廓
    assert len(shade.points) >= 8


# ===========================================================================
# #376 set_expression 幂等（绝对几何 + 可复原）
# ===========================================================================
def test_376_repeated_calls_keep_absolute_geometry():
    """同一对象连调 10 次：眼/嘴/高光的**绝对几何**不得漂移。

    缺陷态：eye ry 每轮按固定比值变化（happy 0.65 / angry 0.15 / surprised 1.28）。
    """
    for maker in MAKERS:
        for expr in EXPRS:
            s = getattr(S, maker)()
            S.set_expression(s, expr)
            first = _geom(s)
            for _ in range(9):
                S.set_expression(s, expr)
            last = _geom(s)
            assert first == last, (
                "%s/%s：连调 10 次后绝对几何漂移\n  1st=%r\n  10th=%r"
                % (maker, expr, first, last))


def test_376_eye_half_height_is_stable_across_repeats():
    """眼睛 ry 逐次不变（#376 报告的量具；既有守卫从未断言过它）。"""
    for maker in MAKERS:
        for expr in EXPRS:
            s = getattr(S, maker)()
            rys = []
            for _ in range(10):
                S.set_expression(s, expr)
                bb = _bbox(_part(s, "eye_l").points)
                rys.append((bb[3] - bb[1]) / 2)
            assert max(rys) - min(rys) < 1e-15, (
                "%s/%s：eye ry 漂移 %.9f → %.9f（比值 %.4f）"
                % (maker, expr, rys[0], rys[-1], rys[1] / rys[0] if rys[0] else float("nan")))


def test_376_expression_is_reversible():
    """任意表情序列可复原：happy×10 后切 neutral == 全新对象的 neutral。"""
    for maker in MAKERS:
        s = getattr(S, maker)()
        for _ in range(10):
            S.set_expression(s, "happy")
        S.set_expression(s, "neutral")
        fresh = getattr(S, maker)()
        S.set_expression(fresh, "neutral")
        assert _geom(s) == _geom(fresh), (
            "%s：happy×10 → neutral 后几何未复原\n  实测=%r\n  新对象=%r"
            % (maker, _geom(s), _geom(fresh)))


def test_376_first_call_geometry_equals_baseline():
    """首调（基准快照当次）必须与模板原始几何一致——基准不得凭空造。"""
    for maker in MAKERS:
        s = getattr(S, maker)()
        base_bbox = _bbox(_part(s, "eye_l").points)
        S.set_expression(s, "neutral")
        # 快照基准 == 首调前的原始眼型 bbox
        snap = s._expr_base["eye_l"]
        assert _bbox(snap) == base_bbox, (
            "%s：_expr_base 眼型 bbox %r != 模板原始 %r" % (maker, _bbox(snap), base_bbox))


# ===========================================================================
# #380 声明 == 实现（docstring 声明的关节名必须真的被某个姿态驱动）
# ===========================================================================
_DECL_RE = re.compile(r"\b([a-z]+_[lr])\b")


def _declared_pose_joints():
    """从 `apply_pose` docstring 的「兼容两种部件命名」块解析声明的关节名。"""
    doc = S.apply_pose.__doc__ or ""
    block = doc.split("兼容两种部件命名", 1)
    assert len(block) == 2, "apply_pose docstring 缺「兼容两种部件命名」声明块"
    body = block[1].split("\n\n", 1)[0]
    names = set(_DECL_RE.findall(body))
    names |= {n for n in ("tail1", "tail2", "tail3") if n in body}
    assert names, "声明块未解析出任何关节名（声明块格式变了？）"
    return names


def _driven_by_some_pose(maker):
    """返回该模板上「被某个姿态改写过 angle」的部件名集合。"""
    driven = set()
    for pose in POSES:
        s = getattr(S, maker)()
        before = {p.name: p.angle for p in s.parts}
        S.apply_pose(s, pose)
        after = {p.name: p.angle for p in s.parts}
        driven |= {n for n in before if abs(before[n] - after[n]) > 1e-12}
    return driven


def test_380_declared_joints_are_actually_driven():
    """docstring 声明的关节名 ⊆ 实跑被驱动的关节名（逐模板）。

    缺陷态：声明含 hip_l/knee_l/thigh_l/calf_l，但七姿态一个都没驱动它们。
    """
    declared = _declared_pose_joints()
    for maker in MAKERS:
        s = getattr(S, maker)()
        exist = {p.name for p in s.parts}
        driven = _driven_by_some_pose(maker)
        promised = declared & exist
        assert promised, "%s：声明名与部件名无交集（夹具失效）" % maker
        never = sorted(promised - driven)
        assert not never, (
            "%s：docstring 声明兼容 %r，但七姿态从未驱动 %r ⇒ 声明与实现不符"
            % (maker, sorted(promised), never))


def test_380_pose_tables_are_consistent_with_docstring():
    """实现侧的两张表（归零/驱动）须与 docstring 声明一致。"""
    declared = _declared_pose_joints()
    driven_tbl = {n for names in S._POSE_DRIVEN_JOINTS.values() for n in names}
    assert declared <= driven_tbl, (
        "docstring 声明 %r，_POSE_DRIVEN_JOINTS 只列 %r"
        % (sorted(declared), sorted(driven_tbl)))
    # 腿只在归零表、不在驱动表（本件裁定：收窄声明）
    legs = {"hip_l", "hip_r", "thigh_l", "thigh_r", "knee_l", "knee_r", "calf_l", "calf_r"}
    assert legs <= set(S._ZERO_JOINTS), "腿名须留在归零表（apply_pose 负责复位）"
    assert not (legs & driven_tbl), "腿名不得出现在驱动表（声明已收窄）"


def test_380_legs_are_drivable_by_joint_and_are_reset_by_apply_pose():
    """夹具：腿部件自身可被 joint() 驱动，且 apply_pose 会把它复位。

    这条挡住两种假修复：①删掉腿部件；②收窄 docstring 却把归零表也删了。
    """
    for maker, names in (("fatfish_skinned", ("thigh_l", "calf_l")),
                         ("fatfish_skeleton", ("hip_l", "knee_l"))):
        for n in names:
            s = getattr(S, maker)()
            p = _part(s, n)
            assert p is not None and p.pivot is not None, "%s 缺可驱动的腿部 %s" % (maker, n)
            before = list(p.rotated_points())
            s.joint(n, 0.8)
            moved = max(math.dist(a, b) for a, b in zip(before, p.rotated_points()))
            assert moved > 1e-3, "%s/%s 自身不可驱动" % (maker, n)
            S.apply_pose(s, "neutral")
            assert p.angle == 0.0, "%s/%s 未被 apply_pose 复位（angle=%.3f）" % (maker, n, p.angle)


# ===========================================================================
# #8 docstring 口径 == 表示层（零厚度平面 ⇒ 不得自称真 3D）
# ===========================================================================
def test_8_module_docstring_declares_2_5d_not_true_3d():
    """模块 docstring 不得再自称「真 3D」，须如实写明分层视差（2.5D）。

    缺陷态：`:7` 「旋转视角时部件产生视差位移 + 画家算法遮挡 —— 真 3D。」
    """
    doc = S.__doc__ or ""
    assert "真 3D" not in doc, "模块 docstring 仍自称「真 3D」（表示层是零厚度平面）"
    assert "2.5D" in doc, "模块 docstring 未如实标注分层视差（2.5D）"
    assert "零厚度" in doc, "模块 docstring 未写明部件是零厚度平面多边形"


def test_8_representation_is_actually_zero_thickness():
    """表示层事实：每个部件是**常数 z 的零厚度平面**（口径须与之相符）。

    若将来真给部件加了厚度/深度缓冲，本断言会红——那时应把 docstring 改回
    「3D」并同步改写 `test_8_module_docstring_declares_2_5d_not_true_3d`。
    """
    s = S.fatfish_skinned(center=CENTER, height=1.55)
    assert s.parts, "夹具失效：模板无部件"
    for p in s.parts:
        for pt in p.points:
            assert len(pt) == 2, "部件 %s 的顶点 %r 不是 2D ⇒ 表示层变了" % (p.name, pt)
        zs = {s._to_3d(pt, p.depth)[2] for pt in p.points}
        assert len(zs) == 1, "部件 %s 的顶点 z 不唯一 %r ⇒ 不是零厚度平面" % (p.name, zs)


def test_8_side_view_area_collapses():
    """表示层行为：侧视 90° 时各平面坍塌（面积骤降）——「真 3D」的直接反证。"""
    def ink(img):
        bg = img.getpixel((0, 0))
        return sum(1 for x in range(200) for y in range(260)
                   if img.getpixel((x, y)) != bg)

    front = ink(S.fatfish_skinned(center=CENTER, height=1.55).render(
        200, 260, camera=Camera3D.look_at(eye=(0.0, 0.85, 2.0), target=CENTER)))
    side = ink(S.fatfish_skinned(center=CENTER, height=1.55).render(
        200, 260, camera=Camera3D.look_at(eye=(3.0, 0.85, 5.0), target=CENTER)))
    assert front > 0, "前视无像素（夹具失效）"
    assert side < front * 0.5, (
        "侧视非背景像素 %d 相对前视 %d 未坍塌（>50%%）⇒ 表示层不是零厚度平面"
        % (side, front))

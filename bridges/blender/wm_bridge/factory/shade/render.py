# -*- coding: utf-8 -*-
"""wm_bridge.factory.shade.render · **相机/灯光/渲染配方** `wmshade.render/1`（纯标准库 · 零 bpy）

★ 与 `material.py` 同一套思想：把"怎么拍"从代码里提出来变成**数据**，
  这样 ① 布光可 diff ② 同一 spec 的两次渲染可核对 ③ 无 Blender 机器也能自测。

-------------------------------------------------------------------------------- 为什么不让默认场景渲
  Blender 默认启动文件是 1 盏点光 + 水平平视 + 无背景 —— 渲出来必然"灰扑扑"：
  · 没有背景分离（主体与背景同灰度，轮廓靠不上）
  · 没有 rim light（边缘不发光，主体"贴"在背景上）
  · 平视 + 50mm 无透视（形体像个正交图，没有体积感）
  本模块把这些**显式声明**成四个 shot：hero / three_view / material_board / npr_compare。

-------------------------------------------------------------------------------- 四 shot 的设计口径
  hero            三光源（key/fill/rim）+ 双层背景幕布 + 微俯 3/4 视角 + 45mm 轻透视
                  目标覆盖 0.68（验收要求 60~75%）
  three_view      正交前/侧/上，**同一相机 + 三份旋转克隆**（不是三次渲染拼图，
                  因此三格的透视/光照/曝光严格一致 —— 横排比较才成立），无地面
  material_board  角色展板：每角色一个小球，同一布光同一相机，5×2 网格
  npr_compare     同场景同机位两张：写实(PBR) / NPR(色阶) + 反壳描边，用于**并排对比**

-------------------------------------------------------------------------------- 色彩管理选型（记录"选哪个、为什么"）
  本机实测可用（试写+读回）：Standard / AgX / Filmic / Filmic Log / Khronos PBR Neutral /
  Raw / False Color；**默认是 AgX**（Blender 5.x 出厂）。
  本项目选 **Standard**，理由三条：
    1. spec 的 `material.color` 是**线性空间里"这个物体是什么颜色"的声明**。
       AgX 会把高饱和色做通道去饱和 + 高光滚降 → 渲染出的色相与声明色**对不上**
       （这与 spec.py 第 6 行写的"同一件东西在四处颜色不会各自漂移"直接冲突）。
    2. NPR 的色阶量化要的是"亮度的硬阶梯"。AgX 的 filmic 肩部会把阶梯**抹圆**，
       渲出来的色阶数会多于 ColorRamp 的 stop 数 → 量化判据失真。
    3. 程序化材质展板是**材质样板**：观众要判断"金属是不是金属"，需要看到真实的
       高光形状与饱和度，AgX 的滚降会压低镜面峰值。
  备选（`vt_alternatives` 一并写进配方，便于对照出图）：AgX（更"电影"、高光不爆炸）、
  Filmic（更旧、更灰）、Khronos PBR Neutral（跨工具一致，但同样压饱和）。只在出"电影感"
  的 hero 备选图时才切，**默认不动**。

-------------------------------------------------------------------------------- 确定性（本机实测结论，见 _det/probe_eevee_determinism2.py）
  实测：EEVEE 渲出的 PNG **逐字节不可复现**，但**像素可以完全一致**。两个来源性质不同：
   ① 纯元数据：Blender 往 PNG 里写 `tEXt/RenderTime`（渲染耗时字符串）。
      chunk 级对拍证明：**唯一差异就是这个 chunk**；解开 IDAT 后像素 100% 相同
      （0 个通道有差）。⇒ 这是 sha256 不稳的**主因**，且与"渲染随机性"无关。
   ② 真像素噪声：只在开着 **屏幕空间光追**(`eevee.use_raytracing`) 或
      **`use_taa_reprojection`** 时出现，量级 ≤1/255、影响 ≤0.005% 的通道。
      ⇒ 关掉二者后本轮实测三轮全 0 像素差。
  因此本配方给两套口径，**都写清楚**，不假装 PNG md5 是好判据：
   · `determinism.fast`：保留光追（画质最好），判据用"解像素后零差"+ canonical PNG
   · `determinism.strict`：关光追/关 reproj/dither=0，判据可用 sha256（配合 canonical PNG）
  `png_canonical` = 落盘后剥掉 ancillary chunk 并按固定参数重编码（见 _det/pngmetrics.py），
  这样 sha256 才是"像素的哈希"而不是"像素+渲染耗时的哈希"。
"""
from __future__ import annotations

import hashlib
import json
import math

#: 配方契约标识
RENDER_SCHEMA = "wmshade.render/1"
QUANT = 6

#: 允许的 shot（闭集）
SHOT_KINDS = ("hero", "three_view", "material_board", "npr_compare")

#: 允许的灯类型（bpy 侧只认这些）
LIGHT_TYPES = ("AREA", "SUN", "POINT", "SPOT")

__all__ = [
    "RENDER_SCHEMA", "SHOT_KINDS", "LIGHT_TYPES", "RenderRecipeError",
    "VT_PRIMARY", "VT_ALTERNATIVES", "VT_REASON", "DETERMINISM_NOTE",
    "dir_from_azel", "estimate_distance", "camera_recipe", "light_recipe",
    "world_recipe", "determinism_block", "board_layout", "threeview_layout",
    "render_recipe", "canonical_json", "recipe_digest", "recipe_summary",
    "SHOT_DEFAULTS", "validate_recipe",
]


class RenderRecipeError(Exception):
    """渲染配方不合法（fail-closed）。"""


# ═══════════════════════════════════════════════════════════════ 色彩管理选型
#: 演示档：Lead 在 `_evidence/factory/VISUAL-ITERATIONS.md` 里逐轮看图标定的组合（v4 之后）
#: ⚠ 5.2 实测：`look` **必须带 `AgX - ` 前缀**，写裸 `"Punchy"` 会 TypeError（本机复现：见 _det/）
CM_PRESENTATION = {"view_transform": "AgX", "look": "AgX - Punchy", "exposure": 0.15, "gamma": 1.0}
#: 保真档：色相与 spec 声明值一致（AgX 会通道去饱和 + 高光滚降）
CM_FIDELITY = {"view_transform": "Standard", "look": "None", "exposure": 0.0, "gamma": 1.0}

VT_PRIMARY = CM_PRESENTATION["view_transform"]
VT_ALTERNATIVES = ("Standard", "Filmic", "Khronos PBR Neutral")
VT_REASON = (
    "演示档 = AgX + `AgX - Punchy` + exposure 0.15：Lead 在 VISUAL-ITERATIONS.md 里逐轮看图标定，"
    "高光不炸、暗部有层次、金属有对比。"
    "⚠ 与 spec 保真档（Standard + None + 0.0）不是同一件事：AgX 会做通道去饱和与高光滚降，"
    "渲出的色相与 spec.material.color 的声明值对不上，NPR 的色阶也会被肩部抹圆。"
    "⇒ 默认出**演示档**（要的是呈现效果）；要做『颜色对不对』的判据时切 `cm='fidelity'` 重渲，"
    "两档都写进配方，不假装它们是同一口径。"
    "另：`look` 必须带 `AgX - ` 前缀（裸 `Punchy` → TypeError，本机实测）。"
)

#: 天空环境（金属要有可反射的东西 —— VISUAL-ITERATIONS 手法 2）
#: ⚠ 修正：Blender 5.2 的 `Sky Texture` **没有 `NISHITA`**；`sky_type` 只有
#:   SINGLE_SCATTERING / MULTIPLE_SCATTERING / PREETHAM / HOSEK_WILKIE。
#:   物理天空用 `MULTIPLE_SCATTERING`（Nishita 多散射的现行实现）。写 NISHITA 会 KeyError。
SKY_DEFAULT = {"sky_type": "MULTIPLE_SCATTERING", "sun_elevation": 28.0, "sun_rotation": -35.0,
               "sun_intensity": 0.10, "sun_disc": False, "altitude": 220.0,
               "air_density": 1.0, "aerosol_density": 1.2, "ozone_density": 1.0,
               "strength": 0.55}


#: 确定性口径说明（原样进报告，不缩写）
DETERMINISM_NOTE = (
    "EEVEE 5.2 的 PNG 逐字节不可复现，但分两类原因，性质完全不同："
    "(1) 元数据——Blender 写入 tEXt/RenderTime（渲染耗时字符串）。chunk 级对拍已证明这是"
    "两次渲染唯一的字节差异，解开 IDAT 后像素 0 通道有差；"
    "(2) 真像素噪声——仅在 eevee.use_raytracing 或 use_taa_reprojection 开启时出现，"
    "量级 ≤1/255、影响 ≤0.005% 通道；两者关闭后实测三轮全 0 像素差。"
    "故：判据用『canonical PNG 的 sha256』（剥 ancillary chunk + 固定参数重编码）"
    "或『解像素零差』，不要用 Blender 原始 PNG 的 md5。"
)


# ═══════════════════════════════════════════════════════════════ 基础几何
def _q(x, digits=QUANT):
    v = round(float(x), digits)
    return 0.0 if v == 0.0 else v


def canonical_json(obj, digits=QUANT):
    def conv(o):
        if isinstance(o, float):
            return _q(o, digits)
        if isinstance(o, dict):
            return {k: conv(o[k]) for k in sorted(o)}
        if isinstance(o, (list, tuple)):
            return [conv(x) for x in o]
        return o
    return json.dumps(conv(obj), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def recipe_digest(recipe):
    r = {k: v for k, v in recipe.items() if k != "digest"}
    return hashlib.sha256(canonical_json(r).encode("utf-8")).hexdigest()


def dir_from_azel(az_deg, el_deg):
    """方位角/仰角 → 单位方向向量（Blender Z-up）。

    az 从 **+X 轴向 +Y** 量（绕 Z 逆时针，右手系）；el 从 XY 平面向 +Z 量。
    这是"相机/灯相对被摄物的位置方向"，距离另给。
    """
    az = math.radians(float(az_deg))
    el = math.radians(float(el_deg))
    return (_q(math.cos(el) * math.cos(az)),
            _q(math.cos(el) * math.sin(az)),
            _q(math.sin(el)))


def position_from_azel(target, az_deg, el_deg, distance):
    """目标点 + 方位/仰角 + 距离 → 世界坐标（纯函数，可单测）。"""
    d = dir_from_azel(az_deg, el_deg)
    t = [float(x) for x in target]
    return [_q(t[0] + d[0] * distance), _q(t[1] + d[1] * distance), _q(t[2] + d[2] * distance)]


def estimate_distance(radius_m, coverage, res, lens_mm=45.0, sensor_mm=36.0):
    """解析估算相机距离：让半径 radius_m 的包围球在画面**高**方向占到 coverage。

    口径：`sensor_fit=AUTO` 且 res_x ≥ res_y ⇒ sensor_mm 作用在**宽**方向，
    故竖直半视场 tan(hv) = (sensor/2)/lens × (res_y/res_x)。
    返回的距离只是**初值**；bpy 侧会用 `world_to_camera_view` 实测覆盖率再二分修正
    （因为包围球与"实际可见前景"不是一回事：细长物体的球会高估）。
    """
    rx, ry = int(res[0]), int(res[1])
    if rx <= 0 or ry <= 0:
        raise RenderRecipeError("res 非法：%r" % (res,))
    if not (0.05 <= coverage <= 0.98):
        raise RenderRecipeError("coverage 须在 0.05..0.98，实为 %r" % (coverage,))
    tan_h = (float(sensor_mm) / 2.0) / float(lens_mm) * (float(ry) / float(rx))
    return _q(float(radius_m) / (float(coverage) / 2.0) / tan_h)


# ═══════════════════════════════════════════════════════════════ 灯光
def light_recipe(name, az_deg, el_deg, energy_w, size_m, color, *, kind="AREA",
                 softness=None, purpose=""):
    """单灯的声明（az/el 是**相对被摄物**的方向；bpy 侧按 target 定朝向）。"""
    if kind not in LIGHT_TYPES:
        raise RenderRecipeError("灯类型不在闭集：%r（允许 %s）" % (kind, ", ".join(LIGHT_TYPES)))
    c = [float(x) for x in color]
    if len(c) == 3:
        c.append(1.0)
    if len(c) != 4:
        raise RenderRecipeError("灯颜色须 RGB 或 RGBA，实为 %r" % (color,))
    for i, x in enumerate(c):
        if not math.isfinite(x) or x < 0.0:
            raise RenderRecipeError("灯颜色[%d] 须为非负有限数，实为 %r" % (i, x))
    return {"name": name, "kind": kind, "az_deg": _q(az_deg), "el_deg": _q(el_deg),
            "energy_w": _q(energy_w), "size_m": _q(size_m),
            "color": [_q(x) for x in c], "softness": (None if softness is None else _q(softness)),
            "purpose": purpose}


def _studio_lights(scale, *, rim_boost=1.0, key=900.0, fill=240.0, rim=850.0, warm=True):
    """标准三光源：key（暖、高位右前）/ fill（冷、左低、大面积柔）/ rim（冷、后上方勾边）。

    为什么是这三个角：key 与相机方位差 ~40° 才有立体明暗；fill 在**对侧低角度**且面积大，
    只抬暗部不产生第二个高光（否则物体像有两个太阳）；rim 在**近似背面**，
    掠射角下正好沿轮廓泛光 —— 这是"把主体从背景里抠出来"的那盏灯。
    """
    s = float(scale)
    kc = (1.00, 0.92, 0.82) if warm else (1.0, 1.0, 1.0)
    return [
        light_recipe("key", 38.0, 34.0, key * s, 2.6 * s, kc,
                     purpose="主光：右前上方 38°/34°，决定明暗面与主要投影"),
        light_recipe("fill", -62.0, 10.0, fill * s, 5.0 * s, (0.72, 0.84, 1.00),
                     purpose="补光：左低 62°、大面积，只抬暗部，不产生第二高光"),
        light_recipe("rim", 168.0, 42.0, rim * s * float(rim_boost), 1.8 * s,
                     (0.66, 0.84, 1.00),
                     purpose="轮廓光：近似背面 168°/42°，掠射勾勒边缘，把主体从背景抠出"),
    ]


# ═══════════════════════════════════════════════════════════════ 世界（背景幕布）
def world_recipe(kind, *, mode="gradient", top_color=None, bottom_color=None,
                 strength=0.30, backdrop=True, backdrop_color=None):
    """背景幕布配方。mode = gradient | flat。

    为什么默认是**竖直渐变**而不是纯色：纯色背景下主体暗部与背景容易落进同一灰度带，
    轮廓就"糊"了。竖直渐变让画面下暗上亮，主体的亮部（上）与暗部（下）
    总有一侧与背景拉开；再加 rim 光，边界就稳。
    色相口径：**主体是暖色（琥珀发光/蓝漆金属）时，背景取冷色**（青蓝），
    这样即便亮度接近，色相差也能把轮廓拉开（`hue_separation` 记录这个意图）。
    """
    if mode not in ("gradient", "flat"):
        raise RenderRecipeError("world.mode 须为 gradient/flat，实为 %r" % (mode,))
    if top_color is None:
        top_color = (0.055, 0.105, 0.165) if kind != "material_board" else (0.075, 0.080, 0.090)
    if bottom_color is None:
        bottom_color = (0.010, 0.020, 0.034) if kind != "material_board" else (0.030, 0.032, 0.038)
    if backdrop_color is None:
        backdrop_color = (0.045, 0.062, 0.085)
    return {
        "mode": mode,
        "top_color": [_q(x) for x in top_color],
        "bottom_color": [_q(x) for x in bottom_color],
        "strength": _q(strength),
        "backdrop_plane": bool(backdrop),
        "backdrop_color": [_q(x) for x in backdrop_color],
        "hue_separation": "主体偏暖（琥珀/红）⇒ 背景取冷青蓝；明度也刻意压低一档",
        "vertical_axis": "Window.Y（屏幕空间竖直）",
    }


# ═══════════════════════════════════════════════════════════════ 确定性
def determinism_block(strict=False, *, use_raytracing=None, samples=64):
    """确定性配置块（两种口径都列出，避免"要么画质要么可复现"的假二选一）。"""
    rt = (not strict) if use_raytracing is None else bool(use_raytracing)
    return {
        "strict": bool(strict),
        "engine": "BLENDER_EEVEE",
        "taa_render_samples": int(samples),
        "eevee.use_raytracing": bool(rt),
        "eevee.ray_tracing_method": "SCREEN",
        "eevee.use_taa_reprojection": False,
        "eevee.use_shadow_jitter_viewport": False,
        "eevee.use_bokeh_jittered": False,
        "render.dither_intensity": 0.0,
        "render.film_transparent": False,
        "render.resolution_percentage": 100,
        "image_settings": {"file_format": "PNG", "color_mode": "RGBA", "compression": 15},
        "png_canonical": True,
        "note": DETERMINISM_NOTE,
    }


# ═══════════════════════════════════════════════════════════════ 展板/三视图排布
def board_layout(n, *, cols=5, spacing=1.35, radius=0.5, z=0.0):
    """展板排布：n 个半径 radius 的样品球，cols 列居中网格。返回 [(x,y,z,name_idx)]。"""
    if n <= 0:
        raise RenderRecipeError("board_layout n 须 > 0")
    if cols <= 0:
        raise RenderRecipeError("board_layout cols 须 > 0")
    rows = int(math.ceil(n / float(cols)))
    out = []
    for i in range(n):
        r, c = divmod(i, cols)
        # 最后一行若不满，整行居中（否则展板会左重右轻）
        in_row = min(cols, n - r * cols)
        x = (c - (in_row - 1) / 2.0) * spacing
        y = ((rows - 1) / 2.0 - r) * spacing
        out.append({"index": i, "row": r, "col": c,
                    "pos": [_q(x), _q(y), _q(z)], "radius": _q(radius)})
    return {"cols": cols, "rows": rows, "spacing": _q(spacing),
            "radius": _q(radius), "cells": out,
            "width": _q(cols * spacing), "height": _q(rows * spacing)}


def threeview_layout(n=3, *, pitch=2.6, views=("front", "side", "top")):
    """三视图排布：**同一相机 + n 份旋转克隆**沿 X 横排。

    旋转口径（相机固定看向 +Y，即站在 -Y 处）：
      front  不旋转
      side   绕 Z 转 -90° ⇒ 物体的 +X 面转向相机
      top    绕 X 转 +90° ⇒ 物体的 +Z 面转向相机
    为什么用"转物体"而不是"转相机"：三格必须在**同一相机矩阵**下成像，
    否则透视/曝光/光照会各自漂移，横排比较就不成立（这也是以前三视图返工的原因）。
    """
    if n != len(views):
        raise RenderRecipeError("threeview_layout: n(%d) 与 views(%d) 不一致" % (n, len(views)))
    rot = {"front": [0.0, 0.0, 0.0], "side": [0.0, 0.0, -90.0], "top": [90.0, 0.0, 0.0],
           "back": [0.0, 0.0, 180.0], "left": [0.0, 0.0, 90.0], "bottom": [-90.0, 0.0, 0.0]}
    tiles = []
    for i, v in enumerate(views):
        if v not in rot:
            raise RenderRecipeError("未知视图 %r（允许 %s）" % (v, ", ".join(sorted(rot))))
        tiles.append({"view": v, "index": i,
                      "x": _q((i - (n - 1) / 2.0) * pitch),
                      "rot_deg": list(rot[v])})
    return {"tiles": tiles, "pitch": _q(pitch), "span": _q(n * pitch)}


# ═══════════════════════════════════════════════════════════════ shot 默认表
SHOT_DEFAULTS = {
    "hero": {
        "resolution": [1280, 720], "samples": 64, "projective": True,
        "az_deg": 34.0, "el_deg": 14.0, "lens_mm": 45.0, "sensor_mm": 36.0,
        "target_coverage": 0.68, "coverage_band": [0.60, 0.75],
        "pitch_note": "微俯 14°（不是平视）：让顶面可见、形体有体积；45mm 轻微透视，不用 24mm 以免透视畸变",
        "ground": True, "backdrop": True, "outline": False,
        "world": {"mode": "gradient", "strength": 0.30},
    },
    "three_view": {
        "resolution": [1920, 720], "samples": 48, "projective": False,
        "ortho_scale_pad": 1.18, "target_coverage": None, "coverage_band": None,
        "pitch_note": "正交、视线水平（技术视图；el=0 是刻意的，与 hero 的『不要平视』不冲突）",
        "ground": False, "backdrop": False, "outline": False,
        "world": {"mode": "flat", "strength": 0.55},
    },
    "material_board": {
        "resolution": [1920, 768], "samples": 64, "projective": True,
        "az_deg": 0.0, "el_deg": 20.0, "lens_mm": 62.0, "sensor_mm": 36.0,
        "target_coverage": 0.66, "coverage_band": [0.60, 0.75],
        "pitch_note": "微俯 20° 正对：既看清球体高光形状，又给球一点上表面（否则像 2D 圆片）",
        "ground": True, "backdrop": True, "outline": False,
        "world": {"mode": "gradient", "strength": 0.34},
    },
    "npr_compare": {
        "resolution": [1920, 720], "samples": 64, "projective": True,
        "az_deg": 34.0, "el_deg": 14.0, "lens_mm": 45.0, "sensor_mm": 36.0,
        "target_coverage": 0.62, "coverage_band": [0.55, 0.72],
        "pitch_note": "与 hero 同机位同布光：**只有着色方式变**，这样对比图才说明问题",
        "ground": True, "backdrop": True, "outline": True, "panes": 2,
        "world": {"mode": "gradient", "strength": 0.30},
    },
}


# ═══════════════════════════════════════════════════════════════ 主函数
def render_recipe(kind, *, subject_radius=None, res=None, samples=None,
                  coverage=None, scale=1.0, strict=False, rim_boost=1.0,
                  world=None, notes=None, cm="presentation", sky=None):
    """产出**某一个 shot** 的完整渲染配方（纯函数）。

    参数
      kind            SHOT_KINDS 之一
      subject_radius  被摄物包围球半径（米）。给了才能解析估算相机距离
      res/samples/coverage  可覆写 shot 默认
      scale           场景尺度（用来按比例缩放灯光能量与柔光板尺寸）；能量 ∝ scale²
      strict          确定性严格档（关光追）
      cm              "presentation"（AgX + AgX - Punchy + 0.15）| "fidelity"（Standard）
      sky             环境天空（金属反射用）；None ⇒ 有地面的 shot 用 SKY_DEFAULT，其余不用
    返回 dict（含 camera / lights / world / determinism / color_management / framing）
    """
    if kind not in SHOT_DEFAULTS:
        raise RenderRecipeError("未知 shot：%r（允许 %s）" % (kind, ", ".join(SHOT_KINDS)))
    d = dict(SHOT_DEFAULTS[kind])
    r_ = list(res or d["resolution"])
    s_ = int(samples if samples is not None else d["samples"])
    cov = coverage if coverage is not None else d["target_coverage"]
    scl = float(scale)
    if scl <= 0:
        raise RenderRecipeError("scale 须 > 0，实为 %r" % (scale,))
    if cm not in ("presentation", "fidelity"):
        raise RenderRecipeError("cm 须为 presentation/fidelity，实为 %r" % (cm,))
    cmv = dict(CM_PRESENTATION if cm == "presentation" else CM_FIDELITY)
    cmv["preset"] = cm
    cmv["vt_alternatives"] = list(VT_ALTERNATIVES)
    cmv["reason"] = VT_REASON

    # 正交三视图：相机**固定**在正前方水平视线（az=-90/el=0，即站在 -Y 看 +Y），
    # 三个视图靠**旋转克隆**得到（见 threeview_layout）—— 这样三格共用同一相机矩阵。
    if d["projective"]:
        cam_az, cam_el = _q(d["az_deg"]), _q(d["el_deg"])
    else:
        cam_az, cam_el = -90.0, 0.0
    cam = {"kind": "perspective" if d["projective"] else "orthographic",
           "az_deg": cam_az, "el_deg": cam_el,
           "lens_mm": _q(d.get("lens_mm", 50.0)), "sensor_mm": _q(d.get("sensor_mm", 36.0)),
           "sensor_fit": "AUTO",
           "fixed_for_all_tiles": not d["projective"],
           "pitch_note": d["pitch_note"]}
    framing = {"target_coverage": (None if cov is None else _q(cov)),
               "coverage_band": d["coverage_band"],
               "method": ("binary_search_distance + 实测像素覆盖率" if cov is not None
                          else "ortho_scale = span × pad（解析）"),
               "ortho_scale_pad": d.get("ortho_scale_pad"),
               "note": "估算距离只是初值：细长物体的包围球会高估，必须用实测前景像素占比校正"}
    if cov is not None and subject_radius is not None:
        cam["distance_estimate_m"] = estimate_distance(
            float(subject_radius) * scl, cov, r_, d["lens_mm"], d["sensor_mm"])

    lights = _studio_lights(max(scl, 0.05) ** 2, rim_boost=rim_boost)
    if not d["ground"]:
        # 三视图无地面：key 略降、加一盏正前补光让正面细节读得出来
        lights = [dict(l, energy_w=_q(l["energy_w"] * 0.85)) for l in lights]
        lights.append(light_recipe("fill_front", -90.0, 6.0, 210.0 * max(scl, 0.05) ** 2,
                                   4.0 * scl, (0.80, 0.88, 1.00),
                                   purpose="正交前视补光：无地面时正面容易欠曝，这盏只抬正面"))

    rec = {
        "schema": RENDER_SCHEMA, "kind": kind,
        "resolution": r_, "samples": s_,
        "engine": "BLENDER_EEVEE",
        "camera": cam,
        "framing": framing,
        "lights": lights,
        "world": world or world_recipe(kind, **_kwargs(d.get("world") or {})),
        "ground": bool(d["ground"]),
        "backdrop": bool(d["backdrop"]),
        "outline": bool(d["outline"]),
        "panes": int(d.get("panes", 1)),
        "scale": _q(scl),
        "determinism": determinism_block(strict, samples=s_),
        "color_management": cmv,
        "sky": (dict(sky) if isinstance(sky, dict) else
                (dict(SKY_DEFAULT) if (sky is None and d["ground"]) else None)),
        "runtime_reuse": {
            "renderer": "runtime.qc_render.qc_render_views",
            "reason": "多视角求解/取景二分/渲染锁/jsonl 台账/引擎与色彩状态还原 都在 runtime 里，"
                      "不重写；本模块只负责『spec → 相机/灯/世界/色彩管理 的参数』",
            "fallback": "runtime 里**没有**『前景 alpha 蒙版 pass』与『NPR 材质/反壳描边』，这两件新增",
        },
        "notes": list(notes or []),
    }
    rec["digest"] = recipe_digest(rec)
    _validate(rec)
    return rec


def _kwargs(d):
    out = {}
    for k in ("mode", "strength", "top_color", "bottom_color", "backdrop_color"):
        if k in d:
            out[k] = d[k]
    return out


def _validate(r):
    if r.get("schema") != RENDER_SCHEMA:
        raise RenderRecipeError("schema 不符：%r" % (r.get("schema"),))
    if r.get("kind") not in SHOT_KINDS:
        raise RenderRecipeError("kind 不在闭集：%r" % (r.get("kind"),))
    rx, ry = r["resolution"]
    if rx <= 0 or ry <= 0:
        raise RenderRecipeError("resolution 非法：%r" % (r["resolution"],))
    if int(r["samples"]) <= 0:
        raise RenderRecipeError("samples 须 > 0")
    if not r["lights"]:
        raise RenderRecipeError("至少要有一盏灯（否则 EEVEE 里 Shader to RGB 会全黑）")
    for i, l in enumerate(r["lights"]):
        if l["kind"] not in LIGHT_TYPES:
            raise RenderRecipeError("lights[%d].kind 不在闭集：%r" % (i, l["kind"]))
        if l["energy_w"] < 0:
            raise RenderRecipeError("lights[%d].energy_w 须 ≥ 0" % i)
    cm = r["color_management"]
    if cm["view_transform"] not in (VT_PRIMARY, "Standard") + VT_ALTERNATIVES:
        raise RenderRecipeError("view_transform 不在已知集合：%r" % (cm["view_transform"],))
    if str(cm.get("look") or "None") != "None" and not str(cm["look"]).startswith("AgX - "):
        raise RenderRecipeError("look 必须带 'AgX - ' 前缀（5.2 实测：裸名 TypeError），实为 %r"
                                % (cm["look"],))
    return True


def validate_recipe(recipe):
    """只读体检：`(ok, reason)`。不抛异常、不改入参。"""
    try:
        _validate(recipe)
    except RenderRecipeError as e:
        return False, str(e)
    except Exception as e:                                   # noqa: BLE001
        return False, "非预期错误：%s: %s" % (type(e).__name__, e)
    return True, ""


def recipe_summary(recipe):
    ok, why = validate_recipe(recipe)
    return {
        "schema": recipe.get("schema"), "kind": recipe.get("kind"), "valid": ok,
        "invalid_reason": why or None,
        "resolution": recipe.get("resolution"), "samples": recipe.get("samples"),
        "engine": recipe.get("engine"),
        "lights": [{"name": l["name"], "az_deg": l["az_deg"], "el_deg": l["el_deg"],
                    "energy_w": l["energy_w"], "size_m": l["size_m"]}
                   for l in (recipe.get("lights") or [])],
        "view_transform": (recipe.get("color_management") or {}).get("view_transform"),
        "target_coverage": (recipe.get("framing") or {}).get("target_coverage"),
        "outline": recipe.get("outline"),
        "digest": recipe.get("digest") or recipe_digest(recipe),
    }

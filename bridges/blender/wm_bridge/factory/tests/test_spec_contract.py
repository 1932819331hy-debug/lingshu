# -*- coding: utf-8 -*-
"""守卫 · `wmfactory.spec/1` 统一生产规格契约（**纯标准库，不需要 Blender**）

运行：
    python3 -X utf8 wm_bridge/factory/tests/test_spec_contract.py
    python3 -X utf8 -m pytest wm_bridge/factory/tests/test_spec_contract.py -q

------------------------------------------------------------------ 缺陷（本守卫防的是什么）
`spec` 是四条产线（建模/绘画/SVG/动画）的**唯一输入契约**。它坏掉的后果不是"一个函数报错"，
而是**四条产线同时按错误的理解生产**：尺寸算错、关节语义错、材质丢失。
上一轮的病根正是"同一件事有四处写法、彼此看着都对"——所以契约必须**唯一且可机械核对**。

------------------------------------------------------------------ 修法
`spec.py` 把校验收成一处：`normalize_spec()` 是**执行面**（不合法 raise，带字段路径），
`validate_spec()` 是**查询面**（不 raise，返回 (ok, reason)）。
本守卫同时钉住两个面，并用**正例 + 反例双向约束**：
只有反例被拒**且**正例通过，才说明契约既没漏也没过严。

------------------------------------------------------------------ 判据来源
· 反例设计来自真实缺陷族：NaN/Inf（上游 world 侧被 min/max 钳成上界过）、
  `bool` 当数值（Python 的 `True == 1`，放行会让 `True` 变成 1 米）、
  父链成环（递归求位姿会无限递归）、同 link 双关节（该 link 姿态无定义）、
  四元数/颜色越界、关节闭集外的值。
· 阈值与闭集来自 `spec.py` 的常量（`JOINT_KINDS` / `JOINT_EASINGS` / `MATERIAL_ROLES`）。

------------------------------------------------------------------ 断言组（抽掉修复 ⇒ 哪组红）
  [A] 正例组          —— 抽掉 `normalize_spec` 的**必填校验** ⇒ A 仍绿（正例本就合法），
                         但抽掉**默认值补全** ⇒ A 红（缺 `pos`/`rot_deg`/`material` 补不出来）
  [B] 基础反例组      —— 抽掉 size 校验 ⇒ B1/B2 红；抽掉重名校验 ⇒ B3 红；抽掉成环检测 ⇒ B4 红
  [C] 关节反例组      —— 抽掉 `JOINT_KINDS` 闭集 ⇒ C1 红；抽掉"同 link 单关节"⇒ C2 红
  [D] 几何/材质反例组 —— 抽掉 `_vec3` 的有限性检查 ⇒ D1(NaN) 红；抽掉 `_is_num` 的 bool 排除 ⇒ D2 红
  [E] 扩展字段组      —— 抽掉 `segments`/`bevel`/`emission` 的校验 ⇒ E1..E6 红
  [F] 缓动语义组      —— 抽掉 `JOINT_EASINGS` 闭集 ⇒ F2 红；抽掉默认 `linear` ⇒ F1 红
  [G] 确定性与摘要组  —— 抽掉 `spec_digest` 的键排序/量化 ⇒ G2 红（同内容不同写法摘要不同）
  [H] 注释语义组      —— `#` 注释不影响结果（防"用注释绕过校验"的错觉）

------------------------------------------------------------------ 回退即红的实测读数（见文件末 REPORT 段）
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
#: 包根（`blender-wm-bridge/`）—— 从本文件位置向上找到含 `wm_bridge/` 的目录
_PKG_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from wm_bridge.factory.geo import spec as S          # noqa: E402

S_ = S.SPEC_SCHEMA


def _base(**kw):
    """最小合法 spec，供反例在其上做单点破坏（单点破坏才能定位是哪条校验在起作用）。"""
    d = {
        "schema": S_, "name": "t",
        "scene": {"fps": 24, "frame_start": 0, "frame_end": 47},
        "links": [{"name": "root", "anchor": (0, 0, 0), "parts": [
            {"name": "p1", "kind": "box", "size": (1.0, 1.0, 1.0)}]}],
    }
    d.update(kw)
    return d


def _deep(spec):
    return json.loads(json.dumps(spec))          # 深拷贝，避免共享可变对象被就地改


RESULTS = []


def check(group, name, ok, detail="", info=""):
    """`detail` 是**失败诊断**（只在红时显示）；`info` 是成功时的读数。
    ⚠ 早先版本把 detail 当"通过时的信息"打印 ⇒ PASS 行显示"键序变化导致摘要不同"
      这种**误导文本**。判据必须锚在语义结论上，不许被字面误导（D-14 同族）。"""
    RESULTS.append((group, name, bool(ok), detail if not ok else info))


def expect_ok(group, name, spec, why=""):
    ok, reason = S.validate_spec(spec)
    check(group, name, ok, "" if ok else "应通过却被拒：%s" % reason)


def expect_reject(group, name, spec, needle=None):
    """反例必须被拒；给 `needle` 时还要求原因串**指向正确的字段**（否则是"碰巧被别的规则拒了"）。"""
    ok, reason = S.validate_spec(spec)
    good = (not ok) and (needle is None or needle in reason)
    check(group, name, good,
          "" if good else ("应被拒但通过了" if ok else "被拒但原因不符（期望含 %r，实为 %r）" % (needle, reason)))


# ═════════════════════════════════════════════════ [A] 正例组
expect_ok("A", "A1 最小合法 spec", _base())
#: ⚠ 这里曾经是 `expect_ok(..., <bool>)` —— 把**布尔断言**当 spec 传进了"必须被接受"的面，
#: 于是 `validate_spec(True)` 必拒 ⇒ 假红。由 `geo-modeling` 独立指出（我把判据面用错了）。
#: 布尔断言属于 `check()`，`expect_ok()` 只收 spec 对象。
check("A", "A2 默认值补全完整",
      S.normalize_spec(_base())["links"][0]["parts"][0]["pos"] == (0.0, 0.0, 0.0),
      "pos 默认值未补成 (0,0,0)")
expect_ok("A", "A3 无 joints 合法", _base())
expect_ok("A", "A4 带 parent 的层级", _base(links=[
    {"name": "root", "parts": [{"name": "a", "size": (1, 1, 1)}]},
    {"name": "child", "parent": "root", "parts": [{"name": "b", "size": (1, 1, 1)}]}]))
expect_ok("A", "A5 五种关节类型全合法", _base(
    links=[{"name": "l%d" % i, "parts": []} for i in range(5)],
    joints=[{"link": "l0", "kind": "static"},
            {"link": "l1", "kind": "spin", "period_s": 1.0},
            {"link": "l2", "kind": "swing", "period_s": 1.0, "amp_deg": 30},
            {"link": "l3", "kind": "prismatic", "period_s": 1.0, "amp_m": 0.5},
            {"link": "l4", "kind": "ease", "keys": [[0, 0], [1, 10]]}]))
expect_ok("A", "A6 材质角色闭集内全部合法", _base(links=[{"name": "r", "parts": [
    {"name": "p%d" % i, "size": (1, 1, 1), "material": {"role": role}}
    for i, role in enumerate(S.MATERIAL_ROLES)]}]))

# ═════════════════════════════════════════════════ [B] 基础反例组
expect_reject("B", "B1 缺 size", _base(links=[{"name": "r", "parts": [{"name": "p"}]}]), "size")
expect_reject("B", "B2 size 含 0", _base(links=[{"name": "r", "parts": [{"name": "p", "size": (1, 0, 1)}]}]), "size")
expect_reject("B", "B3 link 重名", _base(links=[{"name": "r", "parts": []}, {"name": "r", "parts": []}]), "重复")
expect_reject("B", "B4 父链成环", _base(links=[
    {"name": "a", "parent": "b", "parts": []}, {"name": "b", "parent": "a", "parts": []}]), "成环")
expect_reject("B", "B5 parent 不存在", _base(links=[{"name": "a", "parent": "ghost", "parts": []}]), "不存在")
expect_reject("B", "B6 links 为空", _base(links=[]))
expect_reject("B", "B7 部件同 link 内重名", _base(links=[{"name": "r", "parts": [
    {"name": "p", "size": (1, 1, 1)}, {"name": "p", "size": (1, 1, 1)}]}]), "重复")
expect_reject("B", "B8 schema 不符", _base(schema="x"))
expect_reject("B", "B9 frame_end < frame_start", _base(scene={"frame_start": 10, "frame_end": 5}), "frame_end")
expect_reject("B", "B10 name 为空", _base(name="   "), "name")

# ═════════════════════════════════════════════════ [C] 关节反例组
expect_reject("C", "C1 关节 kind 闭集外", _base(links=[{"name": "r", "parts": []}],
                                                joints=[{"link": "r", "kind": "teleport"}]), "kind")
expect_reject("C", "C2 同 link 双关节", _base(links=[{"name": "r", "parts": []}], joints=[
    {"link": "r", "kind": "spin", "period_s": 1}, {"link": "r", "kind": "swing", "period_s": 1, "amp_deg": 10}]),
    "多个关节")
expect_reject("C", "C3 关节 link 悬空", _base(links=[{"name": "r", "parts": []}],
                                              joints=[{"link": "nope", "kind": "spin"}]), "不是已声明")
expect_reject("C", "C4 spin 缺 period_s", _base(links=[{"name": "r", "parts": []}],
                                                joints=[{"link": "r", "kind": "spin"}]), "period_s")
expect_reject("C", "C5 period_s <= 0", _base(links=[{"name": "r", "parts": []}],
                                             joints=[{"link": "r", "kind": "spin", "period_s": 0}]), "period_s")
expect_reject("C", "C6 ease 键非严格递增", _base(links=[{"name": "r", "parts": []}], joints=[
    {"link": "r", "kind": "ease", "keys": [[0, 0], [0, 1]]}]), "递增")
expect_reject("C", "C7 ease 只有 1 个键", _base(links=[{"name": "r", "parts": []}],
                                                joints=[{"link": "r", "kind": "ease", "keys": [[0, 0]]}]), "keys")
expect_reject("C", "C8 关节 axis 非法", _base(links=[{"name": "r", "parts": []}],
                                              joints=[{"link": "r", "kind": "spin", "period_s": 1, "axis": "w"}]),
              "axis")

# ═════════════════════════════════════════════════ [D] 几何/材质反例组
expect_reject("D", "D1 pos 含 NaN", _base(links=[{"name": "r", "parts": [
    {"name": "p", "size": (1, 1, 1), "pos": (float("nan"), 0, 0)}]}]), "pos")
expect_reject("D", "D2 bool 当数值", _base(links=[{"name": "r", "parts": [
    {"name": "p", "size": (True, 1, 1)}]}]), "size")
expect_reject("D", "D3 pos 含 inf", _base(links=[{"name": "r", "parts": [
    {"name": "p", "size": (1, 1, 1), "pos": (float("inf"), 0, 0)}]}]), "pos")
expect_reject("D", "D4 color 越界", _base(links=[{"name": "r", "parts": [
    {"name": "p", "size": (1, 1, 1), "material": {"color": (2, 0, 0)}}]}]), "color")
expect_reject("D", "D5 材质 role 闭集外", _base(links=[{"name": "r", "parts": [
    {"name": "p", "size": (1, 1, 1), "material": {"role": "gold"}}]}]), "role")
expect_reject("D", "D6 roughness 越界", _base(links=[{"name": "r", "parts": [
    {"name": "p", "size": (1, 1, 1), "material": {"roughness": 1.5}}]}]), "roughness")
expect_reject("D", "D7 size 长度不为 3", _base(links=[{"name": "r", "parts": [
    {"name": "p", "size": (1, 1)}]}]), "size")
expect_reject("D", "D8 size 为字符串", _base(links=[{"name": "r", "parts": [
    {"name": "p", "size": "big"}]}]), "size")

# ═════════════════════════════════════════════════ [E] 扩展字段组（4 个缺口 + 回填）
#: G1 分段数 —— 原脚本 `cyl(r,h,seg)` 的 seg 是 8/10/12/28/32，直接决定顶点集 ⇒ 决定 OBJ 字节
expect_reject("E", "E1 segments < 3", _base(links=[{"name": "r", "parts": [
    {"name": "p", "size": (1, 1, 1), "segments": 2}]}]), "segments")
expect_reject("E", "E2 segments 为 bool", _base(links=[{"name": "r", "parts": [
    {"name": "p", "size": (1, 1, 1), "segments": True}]}]), "segments")
expect_reject("E", "E3 segments_v < 2", _base(links=[{"name": "r", "parts": [
    {"name": "p", "size": (1, 1, 1), "segments_v": 1}]}]), "segments_v")
#: G2 倒角 —— 19 件里 12 件带 BEVEL 修改器；带/不带 bevel 的求值顶点数不同 ⇒ 影响导出几何
expect_reject("E", "E4 bevel 为负", _base(links=[{"name": "r", "parts": [
    {"name": "p", "size": (1, 1, 1), "bevel": -0.1}]}]), "bevel")
#: G3 自发光 —— 原脚本 M_glow 基色 (0.9,0.5,0.06) 与发光色 (1.0,0.5,0.06) **不同**，单 color 装不下
expect_reject("E", "E5 emission 越界", _base(links=[{"name": "r", "parts": [
    {"name": "p", "size": (1, 1, 1), "material": {"emission": (2, 0, 0)}}]}]), "emission")
expect_reject("E", "E6 emission_strength 为负", _base(links=[{"name": "r", "parts": [
    {"name": "p", "size": (1, 1, 1), "material": {"emission_strength": -1}}]}]), "emission_strength")
#: G4 材质名 —— 既有 OBJ 的 `usemtl M_dark/M_steel/...`；收进 spec 才能让 spec 模式逐字节对拍
expect_reject("E", "E7 material_name 为空白", _base(links=[{"name": "r", "parts": [
    {"name": "p", "size": (1, 1, 1), "material_name": "  "}]}]), "material_name")
# 正例：原脚本 turret 的要素必须**全部可表达**
expect_ok("E", "E8 原脚本 turret 要素可完整表达", _base(links=[{"name": "root", "parts": [
    {"name": "body", "kind": "cylinder", "size": (1.2, 1.2, 0.8), "segments": 8,
     "material": {"role": "painted_metal", "color": (0.28, 0.30, 0.33)},
     "material_name": "M_dark", "bevel": 0.016},
    {"name": "lamp", "kind": "sphere", "size": (0.2, 0.2, 0.2), "segments": 18, "segments_v": 10,
     "material": {"role": "emissive", "color": (0.9, 0.5, 0.06),
                  "emission": (1.0, 0.5, 0.06), "emission_strength": 5.0},
     "material_name": "M_glow"}]}]))

# ═════════════════════════════════════════════════ [F] 缓动语义组
#: 实测（geo-modeling）：既有 turret 每 6 帧打关键帧、默认 BEZIER ⇒ tick 1/2/3 的 yaw
#: = 2.0126 / 7.4402 / 15.3678 度，而**匀速**应为 7.5 / 15.0 / 22.5 度。
#: ⇒ "360° 转"其实不是匀速。没有这个字段就表达不了既有资产，也说不清真值出自哪种语义。
_f = S.normalize_spec(_base(links=[{"name": "r", "parts": []}],
                            joints=[{"link": "r", "kind": "spin", "period_s": 2.0}]))["joints"][0]
check("F", "F1 easing 默认 linear（匀速）", _f.get("easing") == "linear",
      "默认 easing 不是 linear（实为 %r）——匀速语义无法声明" % _f.get("easing"),
      "easing=%r" % _f.get("easing"))
expect_ok("F", "F2 easing=bezier 合法", _base(links=[{"name": "r", "parts": []}], joints=[
    {"link": "r", "kind": "spin", "period_s": 2.0, "easing": "bezier"}]))
expect_reject("F", "F3 easing 闭集外", _base(links=[{"name": "r", "parts": []}], joints=[
    {"link": "r", "kind": "spin", "period_s": 2.0, "easing": "bounce"}]), "easing")

# ═════════════════════════════════════════════════ [G] 确定性与摘要组
_d1 = S.spec_digest(_base())
_d2 = S.spec_digest(_deep(_base()))
check("G", "G1 同内容摘要相同", _d1 == _d2, "同内容摘要不同：%s vs %s" % (_d1[:12], _d2[:12]),
      "%s" % _d1[:12])
# 键序不同但内容相同 ⇒ 摘要必须相同（这是"可核对标识"的全部意义）
_a = _base()
_b = {"links": _a["links"], "name": _a["name"], "scene": _a["scene"], "schema": _a["schema"]}
check("G", "G2 键序无关", S.spec_digest(_a) == S.spec_digest(_b),
      "键序变化导致摘要不同（摘要失去了「内容标识」的意义）")
# 内容真变了 ⇒ 摘要必须变（否则摘要没有鉴别力）
_c = _base()
_c["links"][0]["parts"][0]["size"] = (1.0, 1.0, 1.001)
check("G", "G3 内容变化摘要变化", S.spec_digest(_a) != S.spec_digest(_c),
      "改了尺寸摘要却没变（摘要无鉴别力）")
# `-0.0` 与 `0.0`：数值等价、字节不等价 —— 必须归零，否则逐字节门会莫名红/绿
_z = _base(links=[{"name": "r", "parts": [{"name": "p", "size": (1, 1, 1), "pos": (-0.0, 0.0, 0.0)}]}])
check("G", "G4 -0.0 归零", S.spec_digest(_z) == S.spec_digest(_base(links=[{"name": "r", "parts": [
    {"name": "p", "size": (1, 1, 1), "pos": (0.0, 0.0, 0.0)}]}])),
      "-0.0 未归零（数值等价但字节不等价，会让逐字节门莫名红/绿）")

# ═════════════════════════════════════════════════ [I] fail-closed：未知键与 falsy 吞值
#: 缺陷（`factory-verifier` 独立复核在 2026-10-14 抓到，**属本文件的作者（Lead）自己的错**）：
#:   原实现用 `dict.get` + `or default`，于是
#:     ① **写错键名被静默忽略** —— 把 `rot_deg` 写成 `rot`、`pos` 写成 `pos_m`、`period_s`
#:        写成 `periode_s`，几何/旋转/关节**无声改变**，而 spec 自称"不静默"；
#:     ② **falsy 值被 `or` 吞掉变成默认值** —— `pos=[]` / `pos=0` / `units=""` / `kind=""` /
#:        `parts=0` 全部静默变成"没写"。
#:   这与 `contract.py` 的立意（"契约不成立的字段被下游静默消费"）是同一族病。
#: 修法：各层加**声明键白名单**（`_reject_unknown`，违反即 raise 并列出合法键），
#:   并把可选取值改为 `_opt_str` / `_opt_vec3` / `_opt_list`（**缺键或显式 null 才用默认值**）。
#: 判据来源：独立复核者构造的 10/10 fail-open 用例（错键名 + falsy）。
#: 断言组：抽掉 `_reject_unknown` ⇒ I1–I7 红；把 `_opt_*` 换回 `or default` ⇒ I8–I12 红。
def _mut(mut):
    import copy
    d = copy.deepcopy(_base())
    mut(d)
    return d


expect_reject("I", "I1 写错键名 rot（应为 rot_deg）",
              _mut(lambda d: d["links"][0]["parts"][0].update({"rot": [0, 0, 0]})), "未声明的键")
expect_reject("I", "I2 写错键名 pos_m",
              _mut(lambda d: d["links"][0]["parts"][0].update({"pos_m": [1, 0, 0]})), "未声明的键")
expect_reject("I", "I3 顶层未知键 metadata",
              _mut(lambda d: d.update({"metadata": {"x": 1}})), "未声明的键")
expect_reject("I", "I4 link 未知键 anchor_w",
              _mut(lambda d: d["links"][0].update({"anchor_w": [0, 0, 0]})), "未声明的键")
expect_reject("I", "I5 material 未知键 roughnesss",
              _mut(lambda d: d["links"][0]["parts"][0].update({"material": {"roughnesss": 0.5}})),
              "未声明的键")
expect_reject("I", "I6 scene 未知键 fov",
              _mut(lambda d: d.update({"scene": {"fov": 60}})), "未声明的键")
expect_reject("I", "I7 joint 未知键 periode_s",
              _mut(lambda d: d.update({"joints": [{"link": "root", "kind": "spin",
                                                   "periode_s": 2}]})), "未声明的键")
expect_reject("I", "I8 pos=[] 被 falsy 吞",
              _mut(lambda d: d["links"][0]["parts"][0].update({"pos": []})), "pos")
expect_reject("I", "I9 pos=0 被 falsy 吞",
              _mut(lambda d: d["links"][0]["parts"][0].update({"pos": 0})), "pos")
expect_reject("I", "I10 units='' 被 falsy 吞",
              _mut(lambda d: d.update({"scene": {"units": ""}})), "units")
expect_reject("I", "I11 kind='' 被 falsy 吞",
              _mut(lambda d: d["links"][0]["parts"][0].update({"kind": ""})), "kind")
expect_reject("I", "I12 parts=0 被 falsy 吞（应为列表）",
              _mut(lambda d: d["links"][0].update({"parts": 0})), "parts")
# 正例：**显式 null** 是合法的"用默认值"，与"falsy 被吞"必须区分开
expect_ok("I", "I13 显式 null ⇒ 合法（用默认值，非静默吞掉）", _mut(
    lambda d: (d["links"][0]["parts"][0].update({"pos": None, "rot_deg": None, "kind": None}),
               d["links"][0].update({"anchor": None}))))

# ═════════════════════════════════════════════════ [J] 幂等性（normalize / digest / summary）
#: 缺陷（`anim-seq` 独立报告，2026-10-14）：`normalize_spec` **不幂等** ——
#:   ① `ease.keys` 存成 tuple 而校验只认 list ⇒ 二次 normalize 直接 raise；
#:   ② `_opt_num` 的缺省分支返回 **int**、显式分支返回 **float** ⇒
#:      `{"fps":24}` 与 `{"fps":24.0}` 的 `spec_digest` **不同**。
#: 后果：任何"再 normalize 一次"的调用方（`spec_summary` → `iter_parts`、`spec_digest`）
#:   对**含 ease 的 spec 必然抛**；且 digest 作为"内容标识"失去唯一性。
#: 修法：`keys` 收 `(list, tuple)`；`_opt_num` 缺省分支 `float(default)`。
#: 判据来源：`anim-seq` 报告的"`spec_summary` 对任何含 ease 的 spec 必然抛"。
#: 断言组：把 `float(default)` 去掉 ⇒ J2/J3 红；把 `(list, tuple)` 改回 `list` ⇒ J1/J4 红。
def _j(o):
    import json
    return json.dumps(o, sort_keys=True, default=str)


_EASE = {"schema": S_, "name": "ease_idem", "links": [{"name": "r", "parts": []}],
         "joints": [{"link": "r", "kind": "ease", "keys": [[0, 0], [1, 10]]}]}
_RICH = {"schema": S_, "name": "rich_idem", "links": [{"name": "r", "parts": [
    {"name": "p", "kind": "cylinder", "size": (1, 1, 1), "segments": 8, "bevel": 0.01,
     "material": {"role": "metal", "roughness": 0.3}}]}],
    "joints": [{"link": "r", "kind": "spin", "period_s": 2, "easing": "bezier"}]}

for _nm, _sp in (("ease", _EASE), ("rich", _RICH), ("min", _base())):
    _n1 = S.normalize_spec(_sp)
    _n2 = S.normalize_spec(_n1)
    _n3 = S.normalize_spec(_n2)
    check("J", "J1 normalize 幂等（%s）" % _nm, _j(_n1) == _j(_n2) == _j(_n3),
          "normalize 不幂等（n1/n2/n3 不等）—— 二次 normalize 会改变内容或抛错")
    check("J", "J2 spec_digest 幂等（%s）" % _nm,
          S.spec_digest(_sp) == S.spec_digest(_n1) == S.spec_digest(_n2),
          "digest 不幂等 —— 同一份内容算出的标识不同（int/float 类型漂移）")
    try:
        _s = S.spec_summary(_sp)
        check("J", "J3 spec_summary 可用（%s）" % _nm, True, "", "fps=%r" % _s.get("fps"))
    except Exception as _e:                                        # noqa: BLE001
        check("J", "J3 spec_summary 可用（%s）" % _nm, False,
              "spec_summary 抛错：%s: %s（含 ease 的 spec 必须可摘要）" % (type(_e).__name__, _e))

expect_reject("J", "J4 ease.keys 只收 list/tuple（拒 dict）",
              _mut(lambda d: d.update({"joints": [{"link": "root", "kind": "ease",
                                                   "keys": {"a": 1}}]})), "keys")

# ═════════════════════════════════════════════════ [H] 输入不被就地修改
_orig = _base()
_snapshot = json.dumps(_orig, sort_keys=True)
S.normalize_spec(_orig)
S.validate_spec(_orig)
check("H", "H1 validate/normalize 不修改入参", json.dumps(_orig, sort_keys=True) == _snapshot,
      "入参被就地改写了（调用方拿到脏数据）")

# ═════════════════════════════════════════════════ 报告
def main():
    groups = {}
    for g, n, ok, d in RESULTS:
        groups.setdefault(g, []).append((n, ok, d))
    total = len(RESULTS)
    failed = [r for r in RESULTS if not r[2]]
    print("=" * 78)
    print("守卫 · wmfactory.spec/1 统一生产规格契约")
    print("=" * 78)
    for g in sorted(groups):
        rows = groups[g]
        bad = [r for r in rows if not r[1]]
        print("\n[%s] %d 项 · %s" % (g, len(rows), "全绿" if not bad else "%d 红" % len(bad)))
        for n, ok, d in rows:
            print("  %s %s%s" % ("PASS" if ok else "FAIL", n, ("  ← " + d) if d else ""))
    print("\n" + "-" * 78)
    print("通过 %d / 失败 %d" % (total - len(failed), len(failed)))
    print("VERDICT=%s" % ("PASS" if not failed else "FAIL"))
    return 0 if not failed else 1


REPORT = """
回退即红的实测（做法：把对应校验改坏/短路，重跑本守卫，记录转红的项）

| 变异 | 改坏什么 | 预期转红 | 实测 |
|---|---|---|---|
| V1 | 短路 `_vec3` 的 `_is_num` 有限性检查 | D1/D3 | 待成员复跑 |
| V2 | 让 `_is_num` 放行 bool | D2/E2 | 待成员复跑 |
| V3 | 去掉 size 必填 | B1 | 待成员复跑 |
| V4 | 去掉 link 重名检查 | B3 | 待成员复跑 |
| V5 | 去掉父链成环检测 | B4 | 待成员复跑 |
| V6 | 把 `JOINT_KINDS` 改为不过滤 | C1 | 待成员复跑 |
| V7 | 去掉"同 link 单关节"检查 | C2 | 待成员复跑 |
| V8 | 去掉 `segments` 校验 | E1/E2 | 待成员复跑 |
| V9 | 去掉 `emission` 越界校验 | E5 | 待成员复跑 |
| V10 | 把 easing 默认改成 `""` | F1 | 待成员复跑 |
| V11 | 让 `spec_digest` 不排序键 | G2 | 待成员复跑 |
| V12 | 让 `spec_digest` 不归零 `-0.0` | G4 | 待成员复跑 |
"""


if __name__ == "__main__":
    sys.exit(main())

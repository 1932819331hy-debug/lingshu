# -*- coding: utf-8 -*-
"""wm_bridge.factory.vec.smil · SVG 动画（SMIL）时间轴（纯标准库）
================================================================================
为什么用 SMIL 而不是 CSS/JS：
  · **单文件、零依赖**：一份 `.svg` 双击就能在浏览器里动，不需要脚本宿主；
    这对"交付给设计师/进文档进海报"是决定性的（Inkscape/Illustrator 里也不会丢动画标签）。
  · **可寻址**：动画挂在具体元素上（`<animate attributeName="d">` 就是**真·矢量变形**，
    不是位图序列），这与"矢量稿可编辑"是同一件事的两面。
  · **声明式**：时间轴是数据，不是代码 ⇒ 与 `svg.py` 一样可以确定性导出、可以 diff。

判据/边界（如实）：
  · 本模块只**生成** SMIL 片段（`<animate>` / `<animateTransform>` / `<animateMotion>` /
    `<set>`），不管渲染；浏览器对 SMIL 的支持见 `_det/anim_support.md`（Chrome/Firefox 全支持，
    部分工具链的静态导出器会忽略——忽略时**退化为第一帧**，不会画错）。
  · **路径变形的硬约束**：`<animate attributeName="d">` 的多个 `values` 必须**同构**
    （同样的命令序列与点数），否则按 SVG 规范是未定义行为。本模块的 `morph()` 会**校验**
    这一点（`validate_morph`），不合格直接 raise——不静默给一份看着能跑其实乱跳的文件。
  · 所有数值走 `svg.fmt_number`，输出稳定；`values` 的**顺序即时间序**，不排序。
"""
from __future__ import annotations

from .svg import fmt_number

__all__ = ["SMIL", "Animate", "AnimateTransform", "AnimateMotion", "Set",
           "Timeline", "SMIL_CONSTRAINTS", "validate_morph", "path_signature"]

#: 本模块用到的 SMIL 属性 → 允许值的说明（自证契约，给复核方看）
SMIL_CONSTRAINTS = {
    "calcMode": ("discrete", "linear", "paced", "spline"),
    "additive": ("replace", "sum"),
    "fill": ("freeze", "remove"),
    "attributeName:d": "各 values 必须是同构路径（命令序列与点数一致）",
    "attributeName:transform": "<animateTransform> 必须给 type",
}


def _t(v):
    """秒 → SMIL 时间文本（带 s 单位）。"""
    s = fmt_number(v, 3)
    return None if s is None else (s + "s")


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _attrs(pairs):
    out = []
    for k, v in pairs:
        if v is None:
            continue
        out.append('%s="%s"' % (k, _esc(v)))
    return " ".join(out)


def path_signature(d):
    """路径 `d` 的**结构签名**：命令字母序列 + 每个命令的参数个数。

    用于校验 morph 同构：`"M2 L4 L4 Z0"` 这种。实现上只做词法切分，不做曲线求值。
    """
    import re
    toks = re.findall(r"[MmLlHhVvCcSsQqTtAaZz]|-?\d*\.?\d+(?:[eE][-+]?\d+)?", d or "")
    cmds = []
    cur = None
    count = 0
    for tk in toks:
        if tk.isalpha():
            if cur is not None:
                cmds.append((cur, count))
            cur = tk
            count = 0
        else:
            count += 1
    if cur is not None:
        cmds.append((cur, count))
    return cmds


def validate_morph(values):
    """morph 的 values 是否同构；返回 `(ok, reason)`。"""
    if not values or len(values) < 2:
        return False, "morph 至少需要 2 个 values"
    sig = path_signature(values[0])
    if not sig:
        return False, "首个 value 不是合法路径"
    for i, v in enumerate(values[1:], 1):
        s = path_signature(v)
        if s != sig:
            #: ⚠ 报错必须指出**第一处不同**。原来只打印 `s[:4]` 与 `sig[:4]`：
            #: 差异若出现在第 5 项之后，两边打印出来**一模一样**
            #: （实测见过 `[('M',2),('L',2),('L',2),('L',2)] vs 同一串`），
            #: 拿到这条报错也**无法定位**问题。
            j = min(len(s), len(sig))
            for k in range(j):
                if s[k] != sig[k]:
                    j = k
                    break
            lo = max(0, j - 2)
            return False, ("第 %d 个 value 与首个不同构：首处不同在第 %d 项"
                           "（共 %d vs %d 项）；该处起 %s vs %s"
                           % (i, j, len(s), len(sig), s[lo:j + 3], sig[lo:j + 3]))
    return True, ""


class SMIL:
    """一个 SMIL 动画元素（基类只负责公共时间属性）。"""

    tag = "animate"

    def __init__(self, dur=1.0, begin=None, repeat_count="indefinite",
                 fill_mode=None, calc_mode=None, key_times=None, key_splines=None,
                 additive=None, accumulate=None, restart=None, extra=None):
        self.dur = dur
        self.begin = begin
        self.repeat_count = repeat_count
        self.fill_mode = fill_mode
        self.calc_mode = calc_mode
        self.key_times = key_times
        self.key_splines = key_splines
        self.additive = additive
        self.accumulate = accumulate
        self.restart = restart
        self.extra = dict(extra or {})

    def _common(self, attr, values=None, from_=None, to=None, by=None):
        pairs = [] if attr is None else [("attributeName", attr)]
        if values is not None:
            pairs.append(("values", ";".join(values) if isinstance(values, (list, tuple))
                          else values))
        if from_ is not None:
            pairs.append(("from", from_))
        if to is not None:
            pairs.append(("to", to))
        if by is not None:
            pairs.append(("by", by))
        dtxt = (" + ".join(_t(b) for b in self.begin) if isinstance(self.begin, (list, tuple))
                else _t(self.begin))
        pairs += [("dur", _t(self.dur)),
                  ("begin", dtxt if self.begin not in (None, 0.0, 0) else None),
                  ("repeatCount", str(self.repeat_count) if self.repeat_count is not None else None),
                  ("fill", self.fill_mode), ("calcMode", self.calc_mode),
                  ("additive", self.additive), ("accumulate", self.accumulate),
                  ("restart", self.restart)]
        if self.key_times is not None:
            pairs.append(("keyTimes", ";".join(fmt_number(k, 4) for k in self.key_times)))
        if self.key_splines is not None:
            pairs.append(("keySplines", ";".join(
                " ".join(fmt_number(x, 3) for x in sp) for sp in self.key_splines)))
        for k, v in self.extra.items():
            pairs.append((k, v))
        return _attrs(pairs)

    def xml(self):                      # pragma: no cover - 基类不直接用
        raise NotImplementedError


class Animate(SMIL):
    """`<animate>`：属性插值（含 `d` 路径变形）。"""

    tag = "animate"

    def __init__(self, attribute, values=None, from_=None, to=None, by=None, **kw):
        SMIL.__init__(self, **kw)
        self.attribute = attribute
        self.values = values
        self.from_ = from_
        self.to = to
        self.by = by
        if attribute == "d" and values is not None:
            ok, why = validate_morph(values)
            if not ok:
                raise ValueError("非法的 d morph：%s" % why)

    def xml(self):
        return "<animate %s/>" % self._common(self.attribute, self.values,
                                              self.from_, self.to, self.by)


class AnimateTransform(SMIL):
    """`<animateTransform>`：平移/旋转/缩放（替换或叠加 `transform`）。"""

    tag = "animateTransform"

    def __init__(self, type, values=None, from_=None, to=None, by=None, **kw):
        SMIL.__init__(self, **kw)
        if type not in ("translate", "scale", "rotate", "skewX", "skewY"):
            raise ValueError("不支持的 transform 类型：%r" % (type,))
        self.type = type
        self.values = values
        self.from_ = from_
        self.to = to
        self.by = by

    def xml(self):
        base = self._common("transform", self.values, self.from_, self.to, self.by)
        return "<animateTransform type=\"%s\" %s/>" % (self.type, base)


class AnimateMotion(SMIL):
    """`<animateMotion>`：沿路径运动（用于"环绕"这类整体运动）。"""

    tag = "animateMotion"

    def __init__(self, path, rotate="auto", **kw):
        SMIL.__init__(self, **kw)
        self.path = path
        self.rotate = rotate

    def xml(self):
        base = self._common(None)
        return '<animateMotion path="%s" rotate="%s" %s/>' % (_esc(self.path), self.rotate, base)


class Set(SMIL):
    """`<set>`：到某时刻把某属性设成定值（可见性/颜色档位）。"""

    tag = "set"

    def __init__(self, attribute, to, **kw):
        SMIL.__init__(self, **kw)
        self.attribute = attribute
        self.to = to

    def xml(self):
        return "<set %s/>" % self._common(self.attribute, None, None, self.to)


class Timeline:
    """一条时间轴：把多个动画元素按**加入序**收集，`documents()` 输出 XML 片段。

    `begin` 支持 `(值, 相对谁)` 这种语义时用字符串（如 `"part_a.click"`），
    这里只透传，不做解释——避免自造一套时间语义。
    """

    def __init__(self, dur=12.0, repeat_count="indefinite", begin=0.0):
        self.dur = float(dur)
        self.repeat_count = repeat_count
        self.begin = begin
        self._items = []

    # ---------------------------------------------------------- 常用动画
    def morph(self, values, key_times=None, calc_mode="spline", key_splines=None,
              begin=None, dur=None, repeat_count=None, attribute="d", fill_mode="freeze"):
        """路径变形（相机环绕的关键：同构路径之间插值）。"""
        n = len(values)
        if calc_mode == "spline":
            if key_times is None:
                key_times = tuple(i / float(n - 1) for i in range(n)) if n > 1 else None
            if key_splines is None:
                key_splines = tuple((0.42, 0.0, 0.58, 1.0) for _ in range(n - 1))
        a = Animate(attribute, values=values, dur=self.dur if dur is None else dur,
                    begin=self.begin if begin is None else begin,
                    repeat_count=self.repeat_count if repeat_count is None else repeat_count,
                    calc_mode=calc_mode, key_times=key_times, key_splines=key_splines,
                    fill_mode=fill_mode)
        self._items.append(a)
        return a

    def float_y(self, amp, dur=None, begin=None, phase="0;1;0"):
        """上下浮动（`translate` 的 values 序列）。"""
        a = AnimateTransform("translate", values="0,0; 0,%s; 0,0" % fmt_number(-abs(amp), 3),
                             dur=self.dur if dur is None else dur,
                             begin=self.begin if begin is None else begin,
                             repeat_count=self.repeat_count,
                             calc_mode="spline", key_times=(0.0, 0.5, 1.0),
                             key_splines=((0.42, 0.0, 0.58, 1.0), (0.42, 0.0, 0.58, 1.0)),
                             additive="sum")
        self._items.append(a)
        return a

    def spin(self, period=8.0, begin=None, cx=0.0, cy=0.0, turns=1):
        """绕 (cx,cy) 匀速旋转（度）。"""
        a = AnimateTransform("rotate", values="%s %s %s; %s %s %s" % (
            fmt_number(0, 3), fmt_number(cx, 3), fmt_number(cy, 3),
            fmt_number(360 * turns, 3), fmt_number(cx, 3), fmt_number(cy, 3)),
            dur=period, begin=self.begin if begin is None else begin,
            repeat_count=self.repeat_count, calc_mode="linear", additive="sum")
        self._items.append(a)
        return a

    def pulse_fill(self, colors, dur=None, begin=None, attribute="fill"):
        """颜色脉冲（多档颜色循环）。"""
        a = Animate(attribute, values=";".join(colors),
                    dur=self.dur if dur is None else dur,
                    begin=self.begin if begin is None else begin,
                    repeat_count=self.repeat_count, calc_mode="spline",
                    key_times=tuple(i / float(len(colors) - 1) for i in range(len(colors)))
                    if len(colors) > 1 else None,
                    key_splines=tuple((0.42, 0.0, 0.58, 1.0)
                                      for _ in range(len(colors) - 1)) or None)
        self._items.append(a)
        return a

    def dash_march(self, dash, period=2.0, begin=None, to=None):
        """描边虚线滚动（"电路在跑"的观感）；`dash` 与 `stroke-dasharray` 同值。"""
        total = to if to is not None else sum(float(x) for x in str(dash).replace(",", " ").split())
        a = Animate("stroke-dashoffset", from_=fmt_number(0, 3), to=fmt_number(-total, 3),
                    dur=period, begin=self.begin if begin is None else begin,
                    repeat_count=self.repeat_count, calc_mode="linear")
        self._items.append(a)
        return a

    def fade(self, lo=0.15, hi=1.0, dur=None, begin=None, attribute="opacity"):
        a = Animate(attribute, values="%s;%s;%s" % (fmt_number(hi, 3), fmt_number(lo, 3),
                                                    fmt_number(hi, 3)),
                    dur=self.dur if dur is None else dur,
                    begin=self.begin if begin is None else begin,
                    repeat_count=self.repeat_count, calc_mode="spline",
                    key_times=(0.0, 0.5, 1.0),
                    key_splines=((0.42, 0.0, 0.58, 1.0), (0.42, 0.0, 0.58, 1.0)))
        self._items.append(a)
        return a

    def set_at(self, attribute, value, begin=0.0, dur=None):
        a = Set(attribute, value, begin=begin, dur=self.dur if dur is None else dur,
                repeat_count=self.repeat_count, fill_mode="freeze")
        self._items.append(a)
        return a

    # ---------------------------------------------------------- 组织
    def stagger(self, n, step=0.25, start=0.0):
        """给 n 个元素的错峰 begin 时刻（确定性）。"""
        return [start + i * step for i in range(int(n))]

    def documents(self):
        """按加入序输出 SMIL XML 片段（list[str]）。"""
        return [it.xml() for it in self._items]

    def child_of(self, element_xml):
        """把一个自闭合元素包成"带 SMIL 子元素"的形式（便利函数）。"""
        if not element_xml.rstrip().endswith("/>"):
            raise ValueError("只支持自闭合元素")
        head = element_xml.rstrip()[:-2]
        kids = "\n".join("      " + d for d in self.documents())
        return "%s>\n%s\n    </%s>" % (head, kids, head.split("<")[1].split(" ")[0].split(">")[0])

    def __len__(self):
        return len(self._items)

    def __repr__(self):                 # pragma: no cover
        return "Timeline(%d items, dur=%s)" % (len(self._items), self.dur)

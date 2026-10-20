# -*- coding: utf-8 -*-
"""wm_bridge.factory.film._common · 影视层契约的共用校验底座（纯标准库 · 零 bpy · 零 numpy）

★ 为什么要有它（而不是七份契约各写一遍）
    §3.2 新增七份契约各自独立成模块，但**校验语义必须同一套**：否则
    「未声明的键一律拒收」「bool 不算数值」「NaN 一律拒」「闭集外的值一律拒」
    这些纪律会在七处慢慢漂移成七种做法 —— 这正是 geo/spec.py 头部记录的旧病根
    （「同一件事有四处写法、彼此看着都对」）。
    本模块把**与具体契约无关**的那部分收成一处；每份契约只声明自己的键白名单与闭集。

★ 继承自 geo/spec.py 的四条硬纪律（逐条对齐，不新造）
    1. 纯标准库 —— 不 import bpy / numpy / PIL；本层可在无 Blender 机器上完整自测。
    2. 不静默 —— 非法输入 ⇒ ContractError（消息含字段路径）；
       validate_* 是不抛异常的查询面，两者都要有。
    3. 确定性 —— digest 只依赖内容（键排序 + 数值量化 + LF），与路径 / mtime / 调用时刻无关。
    4. fail-closed —— 未声明的键 ⇒ 拒；闭集外的值 ⇒ 拒；不猜、不补、不降级。

★ 与 geo/spec.py 的关系
    本模块**不修改也不替代** geo/spec.py。wmfactory.spec/1 仍是四条产线（含本影视层）
    的唯一输入契约；本层只**读**它（经 spec_ref），不往里加字段（§3.2 的设计理由）。
"""
from __future__ import annotations

import hashlib
import json
import math

__all__ = [
    "QUANT", "ContractError",
    "reject_unknown", "is_num", "req_num", "opt_num",
    "req_str", "opt_str", "req_list", "opt_list", "req_map", "opt_map",
    "vec2", "vec3", "opt_vec2", "opt_vec3",
    "enum", "opt_enum", "req_bool", "opt_bool",
    "check_schema", "q", "canonical_json", "digest_of", "validate_with",
    "frac01", "req_frac01", "positive", "nonneg",
    "req_int", "opt_int",
]

#: 数值量化位数（与 geo/spec.py 的 QUANT 同口径：导出量子 = 1e-6）
QUANT = 6


class ContractError(ValueError):
    """契约不合法（fail-closed：不猜、不补、不静默降级）。"""


# ─────────────────────────────────────────────────────────── 键白名单
def reject_unknown(d, allowed, where):
    """未声明的键 ⇒ raise。生效条件：d 是 dict 且出现白名单外的键。

    ★ 为什么这条是 fail-closed 的第一落点：独立复核者在 geo/spec.py 实测到
      把 rot（应为 rot_deg）这类**拼错的键名**会被 dict.get 静默忽略
      ⇒ 几何/旋转**无声改变**，而 spec 自称「不静默」。
      影视层的后果同族且更贵：一个拼错的 camera.paths 会让整条运镜回落到
      默认值而**没人知道**——判据门看到的是一份"合法"的镜头。
    """
    if not isinstance(d, dict):
        return
    extra = sorted(set(d) - set(allowed))
    if extra:
        raise ContractError(
            "%s 出现未声明的键：%s —— 允许的键是 %s。"
            "（拼错的键名会被 dict.get 静默忽略、取值无声改变，故 fail-closed）"
            % (where, ", ".join(extra), ", ".join(sorted(allowed))))


# ─────────────────────────────────────────────────────────── 基础类型
def is_num(v):
    """有限数值（bool 不算 —— bool 是 int 子类，放行会让 True 当成 1）。"""
    if isinstance(v, bool):
        return False
    return isinstance(v, (int, float)) and math.isfinite(float(v))


def req_num(d, key, where):
    if not isinstance(d, dict) or key not in d:
        raise ContractError("%s 缺必填数值字段 %r" % (where, key))
    v = d[key]
    if not is_num(v):
        raise ContractError("%s.%s 须为有限数值，实为 %r" % (where, key, v))
    return float(v)


def opt_num(d, key, default, where):
    """可选数值：缺键或显式 null ⇒ 默认值；给了非数值（含 NaN/Inf）⇒ 拒。"""
    if not isinstance(d, dict) or key not in d or d[key] is None:
        return default
    v = d[key]
    if not is_num(v):
        raise ContractError("%s.%s 须为有限数值，实为 %r" % (where, key, v))
    return float(v)


def req_str(d, key, where):
    if not isinstance(d, dict) or key not in d:
        raise ContractError("%s 缺必填字符串字段 %r" % (where, key))
    v = d[key]
    if not isinstance(v, str) or not v.strip():
        raise ContractError("%s.%s 须为非空字符串，实为 %r" % (where, key, v))
    return v.strip()


def opt_str(d, key, default, where):
    """可选字符串：缺键或显式 null ⇒ 默认值；给了别的类型或空串 ⇒ 拒。

    ⚠ 历史缺陷（geo/spec.py 记过）：写成 d.get(key) or default 会让
      "" 这类**非法值被 falsy 吞掉、静默变成默认值**。这里用显式 is None。
    """
    if not isinstance(d, dict) or key not in d or d[key] is None:
        return default
    v = d[key]
    if not isinstance(v, str) or not v.strip():
        raise ContractError("%s.%s 须为非空字符串，实为 %r" % (where, key, v))
    return v.strip()


def req_list(d, key, where):
    if not isinstance(d, dict) or key not in d:
        raise ContractError("%s 缺必填列表字段 %r" % (where, key))
    v = d[key]
    if not isinstance(v, list):
        raise ContractError("%s.%s 须为列表，实为 %s" % (where, key, type(v).__name__))
    return v


def opt_list(d, key, where):
    if not isinstance(d, dict) or key not in d or d[key] is None:
        return []
    v = d[key]
    if not isinstance(v, list):
        raise ContractError("%s.%s 须为列表，实为 %s" % (where, key, type(v).__name__))
    return v


def req_map(d, key, where):
    if not isinstance(d, dict) or key not in d:
        raise ContractError("%s 缺必填对象字段 %r" % (where, key))
    v = d[key]
    if not isinstance(v, dict):
        raise ContractError("%s.%s 须为对象，实为 %s" % (where, key, type(v).__name__))
    return v


def opt_map(d, key, where):
    if not isinstance(d, dict) or key not in d or d[key] is None:
        return {}
    v = d[key]
    if not isinstance(v, dict):
        raise ContractError("%s.%s 须为对象，实为 %s" % (where, key, type(v).__name__))
    return v


def req_bool(d, key, where):
    if not isinstance(d, dict) or key not in d:
        raise ContractError("%s 缺必填布尔字段 %r" % (where, key))
    v = d[key]
    if not isinstance(v, bool):
        raise ContractError("%s.%s 须为布尔，实为 %r" % (where, key, v))
    return v


def opt_bool(d, key, default, where):
    if not isinstance(d, dict) or key not in d or d[key] is None:
        return default
    v = d[key]
    if not isinstance(v, bool):
        raise ContractError("%s.%s 须为布尔，实为 %r" % (where, key, v))
    return v


def vec2(v, where):
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        raise ContractError("%s 须为长度 2 的序列，实为 %r" % (where, v))
    out = []
    for i, x in enumerate(v):
        if not is_num(x):
            raise ContractError("%s[%d] 须为有限数值，实为 %r" % (where, i, x))
        out.append(float(x))
    return tuple(out)


def vec3(v, where):
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        raise ContractError("%s 须为长度 3 的序列，实为 %r" % (where, v))
    out = []
    for i, x in enumerate(v):
        if not is_num(x):
            raise ContractError("%s[%d] 须为有限数值，实为 %r" % (where, i, x))
        out.append(float(x))
    return tuple(out)


def opt_vec2(d, key, default, where):
    if not isinstance(d, dict) or key not in d or d[key] is None:
        return vec2(default, "%s.%s" % (where, key))
    return vec2(d[key], "%s.%s" % (where, key))


def opt_vec3(d, key, default, where):
    if not isinstance(d, dict) or key not in d or d[key] is None:
        return vec3(default, "%s.%s" % (where, key))
    return vec3(d[key], "%s.%s" % (where, key))


def enum(v, allowed, where):
    """闭集校验：值必须 ∈ allowed（**拒收未知值**，不回落默认）。"""
    if v not in allowed:
        raise ContractError(
            "%s 须为闭集之一 %s，实为 %r"
            % (where, " / ".join(repr(a) for a in allowed), v))
    return v


def opt_enum(d, key, default, where, allowed):
    if not isinstance(d, dict) or key not in d or d[key] is None:
        return default
    return enum(d[key], allowed, "%s.%s" % (where, key))


def check_schema(d, expected, where):
    """schema 标识必须**精确相等**（不做前缀/大小写宽容 —— 宽容即漂移的入口）。"""
    if not isinstance(d, dict):
        raise ContractError("%s 须为对象，实为 %s" % (where, type(d).__name__))
    got = d.get("schema")
    if got != expected:
        raise ContractError("schema 不符：期望 %r，实为 %r" % (expected, got))
    return expected


def frac01(v, where):
    """归一化比值：须 ∈ [0, 1]。构图/安全区/覆盖率一类量的统一口径。"""
    x = float(v)
    if not (0.0 <= x <= 1.0):
        raise ContractError("%s 须落在 [0, 1]，实为 %r" % (where, v))
    return x


def req_frac01(d, key, where):
    return frac01(req_num(d, key, where), "%s.%s" % (where, key))


def positive(v, where):
    x = float(v)
    if not (x > 0.0):
        raise ContractError("%s 须为正数，实为 %r" % (where, v))
    return x


def nonneg(v, where):
    x = float(v)
    if not (x >= 0.0):
        raise ContractError("%s 须为非负数，实为 %r" % (where, v))
    return x


def req_int(d, key, where):
    """必填整数（bool 不算；浮点须恰为整数值 —— 不静默取整）。"""
    v = req_num(d, key, where)
    if not float(v).is_integer():
        raise ContractError("%s.%s 须为整数，实为 %r" % (where, key, d[key]))
    return int(v)


def opt_int(d, key, default, where):
    """可选整数：缺键或显式 null ⇒ 默认值；给了非整数值 ⇒ 拒。"""
    if not isinstance(d, dict) or key not in d or d[key] is None:
        return default
    v = opt_num(d, key, default, where)
    if not float(v).is_integer():
        raise ContractError("%s.%s 须为整数，实为 %r" % (where, key, d[key]))
    return int(v)


# ─────────────────────────────────────────────────────────── 确定性摘要
def q(x, digits=QUANT):
    """数值量化：-0.0 归零（否则 -0.0 != 0.0 会让同内容算出不同摘要）。"""
    v = round(float(x), digits)
    return 0.0 if v == 0.0 else v


def canonical_json(obj, digits=QUANT):
    """规范化 JSON 文本（键排序 / 数值量化 / -0.0 归零 / 非 ASCII 保留原文）。"""
    def conv(o):
        if isinstance(o, bool):
            return o
        if isinstance(o, float):
            return q(o, digits)
        if isinstance(o, dict):
            return {k: conv(o[k]) for k in sorted(o)}
        if isinstance(o, (list, tuple)):
            return [conv(x) for x in o]
        return o

    return json.dumps(conv(obj), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def digest_of(normalized, digits=QUANT):
    """规范化对象的内容摘要（sha256 hex）。只依赖内容，与路径 / mtime / 时刻无关。"""
    payload = canonical_json(normalized, digits)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_with(normalize_fn, obj):
    """查询面：normalize_fn 不抛 ⇒ (True, "")；抛 ContractError ⇒ (False, 原因)。

    ★ 与执行面的关系（沿用 geo/spec.py 的两面设计）：
      normalize_fn 是**契约执行**（不合法就 raise，消息含字段路径）；
      本函数是**只读体检**（不抛异常、不改入参），给调用方自己决定怎么办。
    """
    try:
        normalize_fn(obj)
    except ContractError as e:
        return False, str(e)
    except Exception as e:                                   # noqa: BLE001
        return False, "非预期错误：%s: %s" % (type(e).__name__, e)
    return True, ""

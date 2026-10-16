# -*- coding: utf-8 -*-
"""wm_bridge.factory.film.bind · 音画绑定契约 wmmv.bind/1（纯标准库 · 零 bpy）

★ 这一份契约要回答什么
    「音乐怎么变成画面」此前是一句口头承诺：说「跟着鼓点剪」，但没有任何一处
    声明**跟哪一拍、容差几帧、能量大时镜头拉多长、音色亮时画面多暖**。
    没有声明就没有判据，没有判据就只能靠「我觉得」—— 这正是命题 1 要排除的。
    wmmv.bind/1 把这三条映射写成数据：
      拍点 → 切点        （cut_rule）
      能量 → 节奏/景别/运动量（energy_map）
      音色 → 色彩        （timbre_map）
    以及段落 → 整体风格（section_map）。

★ 与 wmmv.score/1 的分工（**不重叠**）
    score 存「音乐是什么」（客观读数：拍点、段落、能量、调性）；
    bind 存「画面怎么跟」（**决策**：映射规则与阈值）。
    两者分开的理由：换一套画面风格不该重算音频分析；换一首歌不该改画面规则。
    bind 通过 score_ref.digest 绑定它依据的那一份乐谱 —— 防止
    「按 A 版乐谱定的规则，却拿 B 版乐谱去执行」。

★ 闭集的唯一权威不在本模块
    段落类型取 wmmv.score/1 的 SECTION_KINDS；运镜类型取 wmfilm.shot/1 的 MOVE_KINDS。
    本模块**延迟导入**它们（函数内），不复制常量 —— 复制即第二套定义，会漂移。
"""
from __future__ import annotations

from . import _common as C

#: 契约标识
BIND_SCHEMA = "wmmv.bind/1"

#: 切点可以「落在什么上」（闭集）
CUT_ONS = ("beat", "downbeat", "bar", "section", "energy_peak", "onset")

#: 景别（闭集，由远及近 —— 顺序即语义，M 族判据按序判「景别跳跃」）
SHOT_SCALES = ("extreme_wide", "wide", "medium_wide", "medium",
               "medium_close", "close", "extreme_close")

#: 音色标签（闭集）
TIMBRES = ("bright", "dark", "warm", "cold", "percussive", "sustained",
           "noisy", "tonal")

__all__ = [
    "BIND_SCHEMA", "CUT_ONS", "SHOT_SCALES", "TIMBRES",
    "BindError", "normalize_bind", "validate_bind", "bind_digest", "bind_summary",
    "scale_rank", "band_at",
]

_KEYS_BIND = ("schema", "id", "score_ref", "cut_rule", "energy_map",
              "timbre_map", "section_map", "notes")
_KEYS_REF = ("name", "digest")
_KEYS_CUT_RULE = ("on", "quantize_beats", "tolerance_frames")
_KEYS_ENERGY_MAP = ("metric", "bands")
_KEYS_BAND = ("lo", "hi", "asl_frames", "shot_scale", "move_intensity")
_KEYS_TIMBRE = ("timbre", "hue_deg", "saturation", "value")
_KEYS_SECTION_MAP = ("section", "shot_scale", "move_bias", "mood")

#: 能量包络度量（权威 = wmmv.score/1 的 ENERGY_METRICS）
_CONST_CACHE = {}


def _consts():
    if "v" not in _CONST_CACHE:
        from . import score as SC
        from . import shot as SH
        _CONST_CACHE["v"] = (tuple(SC.ENERGY_METRICS), tuple(SC.SECTION_KINDS),
                             tuple(SH.MOVE_KINDS), tuple(SH.MOODS))
    return _CONST_CACHE["v"]


class BindError(C.ContractError):
    """音画绑定契约不合法（fail-closed：不猜、不补、不静默降级）。"""


def _is_hex(s):
    return bool(s) and all(ch in "0123456789abcdefABCDEF" for ch in s)


def _digest_str(d, where):
    v = C.req_str(d, "digest", where)
    if _is_hex(v) and len(v) != 64:
        raise BindError(
            "%s.digest 形如十六进制但非 sha256（须 64 位），实为 %r" % (where, v))
    return v


def scale_rank(scale):
    """景别的「由远及近」序号（0 = 极远）。闭集外的值 ⇒ raise。"""
    C.enum(scale, SHOT_SCALES, "shot_scale")
    return SHOT_SCALES.index(scale)


# ─────────────────────────────────────────────────────────── 规范化（执行面）
def normalize_bind(bd):
    """校验并补全默认值；返回**新的**规范化 bind（不修改入参）。

    补全的默认值：
      cut_rule.quantize_beats     -> 1
      cut_rule.tolerance_frames   -> 1   （§4.2 E1 的容差：切点到 downbeat 偏差 <= 1 帧）
      cut_rule.on                 -> "downbeat"
      energy_map / timbre_map / section_map -> []

    生效条件：合法 ⇒ 返回新 dict；不合法 ⇒ raise BindError（消息含字段路径）。
    """
    if not isinstance(bd, dict):
        raise BindError("bind 顶层须为对象，实为 %s" % type(bd).__name__)
    C.check_schema(bd, BIND_SCHEMA, "bind")
    C.reject_unknown(bd, _KEYS_BIND, "bind 顶层")

    out = {"schema": BIND_SCHEMA}
    out["id"] = C.req_str(bd, "id", "bind")

    sr = C.req_map(bd, "score_ref", "bind")
    C.reject_unknown(sr, _KEYS_REF, "bind.score_ref")
    out["score_ref"] = {"name": C.req_str(sr, "name", "bind.score_ref"),
                        "digest": _digest_str(sr, "bind.score_ref")}

    out["cut_rule"] = _norm_cut_rule(C.req_map(bd, "cut_rule", "bind"))
    out["energy_map"] = _norm_energy_map(bd.get("energy_map"))
    out["timbre_map"] = _norm_timbre_map(C.opt_list(bd, "timbre_map", "bind"))
    out["section_map"] = _norm_section_map(C.opt_list(bd, "section_map", "bind"))

    if bd.get("notes") is not None:
        out["notes"] = C.req_str(bd, "notes", "bind")
    return out


def _norm_cut_rule(cr):
    C.reject_unknown(cr, _KEYS_CUT_RULE, "bind.cut_rule")
    qb = C.opt_int(cr, "quantize_beats", 1, "bind.cut_rule")
    tf = C.opt_int(cr, "tolerance_frames", 1, "bind.cut_rule")
    if qb < 1:
        raise BindError("bind.cut_rule.quantize_beats 须 >= 1，实为 %r" % (qb,))
    if tf < 0:
        raise BindError("bind.cut_rule.tolerance_frames 须 >= 0，实为 %r" % (tf,))
    return {"on": C.opt_enum(cr, "on", "downbeat", "bind.cut_rule", CUT_ONS),
            "quantize_beats": qb, "tolerance_frames": tf}


def _norm_energy_map(em):
    if em is None:
        return {"metric": "rms", "bands": []}
    if not isinstance(em, dict):
        raise BindError("bind.energy_map 须为对象，实为 %s" % type(em).__name__)
    C.reject_unknown(em, _KEYS_ENERGY_MAP, "bind.energy_map")
    metric = C.opt_enum(em, "metric", "rms", "bind.energy_map", _consts()[0])

    bands = []
    for i, b in enumerate(C.opt_list(em, "bands", "bind.energy_map")):
        w = "bind.energy_map.bands[%d]" % i
        if not isinstance(b, dict):
            raise BindError("%s 须为对象，实为 %s" % (w, type(b).__name__))
        C.reject_unknown(b, _KEYS_BAND, w)
        lo = C.req_frac01(b, "lo", w)
        hi = C.req_frac01(b, "hi", w)
        if not (hi > lo):
            raise BindError("%s 的 hi(%r) 须大于 lo(%r)" % (w, hi, lo))
        asl = C.positive(C.req_num(b, "asl_frames", w), w + ".asl_frames")
        mi = C.req_num(b, "move_intensity", w)
        if not (0.0 <= mi <= 1.0):
            raise BindError("%s.move_intensity 须落在 [0, 1]，实为 %r" % (w, mi))
        bands.append({"lo": lo, "hi": hi, "asl_frames": asl,
                      "shot_scale": C.enum(C.req_str(b, "shot_scale", w),
                                           SHOT_SCALES, w + ".shot_scale"),
                      "move_intensity": mi})

    for a, b in zip(bands, bands[1:]):
        if b["lo"] < a["hi"] - 1e-9:
            raise BindError(
                "能量分档重叠：[%r, %r] 与 [%r, %r] —— 同一读数会命中两档，"
                "画面参数将无定义" % (a["lo"], a["hi"], b["lo"], b["hi"]))
    return {"metric": metric, "bands": bands}


def _norm_timbre_map(items):
    out, seen = [], set()
    for i, t in enumerate(items):
        w = "bind.timbre_map[%d]" % i
        if not isinstance(t, dict):
            raise BindError("%s 须为对象，实为 %s" % (w, type(t).__name__))
        C.reject_unknown(t, _KEYS_TIMBRE, w)
        tb = C.enum(C.req_str(t, "timbre", w), TIMBRES, w + ".timbre")
        if tb in seen:
            raise BindError("bind.timbre_map 重复声明音色 %r —— 该音色的色彩无定义" % tb)
        seen.add(tb)
        hue = C.req_num(t, "hue_deg", w)
        if not (0.0 <= hue < 360.0):
            raise BindError("%s.hue_deg 须落在 [0, 360)，实为 %r" % (w, hue))
        out.append({"timbre": tb, "hue_deg": hue,
                    "saturation": C.req_frac01(t, "saturation", w),
                    "value": C.req_frac01(t, "value", w)})
    return out


def _norm_section_map(items):
    metrics, sections, moves, moods = _consts()
    out, seen = [], set()
    for i, s in enumerate(items):
        w = "bind.section_map[%d]" % i
        if not isinstance(s, dict):
            raise BindError("%s 须为对象，实为 %s" % (w, type(s).__name__))
        C.reject_unknown(s, _KEYS_SECTION_MAP, w)
        sec = C.enum(C.req_str(s, "section", w), sections, w + ".section")
        if sec in seen:
            raise BindError("bind.section_map 重复声明段落 %r —— 该段落的风格无定义" % sec)
        seen.add(sec)
        out.append({"section": sec,
                    "shot_scale": C.enum(C.req_str(s, "shot_scale", w), SHOT_SCALES,
                                         w + ".shot_scale"),
                    "move_bias": C.enum(C.req_str(s, "move_bias", w), moves,
                                        w + ".move_bias"),
                    "mood": C.enum(C.req_str(s, "mood", w), moods, w + ".mood")})
    return out


# ─────────────────────────────────────────────────────────── 查询面与摘要
def validate_bind(bd):
    """查询面：返回 (ok: bool, reason: str)。**不抛异常、不改入参。**"""
    return C.validate_with(normalize_bind, bd)


def bind_digest(bd):
    """音画绑定契约的内容摘要（sha256 hex）。只依赖内容。"""
    return C.digest_of(normalize_bind(bd))


def band_at(bd, energy):
    """取能量读数 energy ∈ [0, 1] 命中的分档（未命中任何档 ⇒ None）。"""
    for b in normalize_bind(bd)["energy_map"]["bands"]:
        if b["lo"] <= energy < b["hi"]:
            return b
    return None


def bind_summary(bd):
    """人读摘要（用于报告与守卫断言，不参与哈希）。"""
    n = normalize_bind(bd)
    return {"schema": n["schema"], "id": n["id"], "score": n["score_ref"]["name"],
            "cut_on": n["cut_rule"]["on"],
            "tolerance_frames": n["cut_rule"]["tolerance_frames"],
            "energy_metric": n["energy_map"]["metric"],
            "bands": len(n["energy_map"]["bands"]),
            "timbres": [t["timbre"] for t in n["timbre_map"]],
            "sections": [s["section"] for s in n["section_map"]],
            "digest": bind_digest(n)}

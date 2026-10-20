# -*- coding: utf-8 -*-
"""wm_bridge.factory.film.edit · 剪辑契约 wmmv.edit/1（纯标准库 · 零 bpy）

★ 这一份契约要回答什么
    「镜头怎么排、在哪儿切」此前没有任何声明。有了 wmfilm.shot/1（每个镜头怎么拍）
    与 wmmv.bind/1（画面怎么跟音乐），还缺**把它们排成一条时间轴**的那一层：
    哪几个镜头、各占多少帧、切点落在哪、是否卡在拍点上。
    wmmv.edit/1 就是这条时间轴。

★ 与 wmmv.bind/1 的分工（**不重叠**）
    bind 存**规则**（拍点 → 切点、能量 → 景别），与具体镜头无关；
    edit 存**实例**（这一次实际切在哪一帧、用了哪个镜头）。
    规则可复用到另一首歌；实例只属于这一支 MV。
    edit 通过 bind_ref.digest 绑定它执行的那一套规则 —— 防止
    「按 A 版规则排的时间轴，却拿 B 版规则去验收」。

★ 本层只保证时间轴**自洽**（不重叠、不留缝、恰好覆盖全片）。
    「切点是否真的落在拍点容差内」是 §4.2 E1 判据门的活 —— **契约不替判据下结论**。
"""
from __future__ import annotations

from . import _common as C

#: 契约标识
EDIT_SCHEMA = "wmmv.edit/1"

#: 转场类型（权威 = wmfilm.shot/1 的 TRANSITION_KINDS，延迟导入不复制）
_CONST_CACHE = {}


def _transition_kinds():
    if "tk" not in _CONST_CACHE:
        from . import shot as SH
        _CONST_CACHE["tk"] = tuple(SH.TRANSITION_KINDS)
    return _CONST_CACHE["tk"]


__all__ = ["EDIT_SCHEMA", "EditError", "normalize_edit", "validate_edit",
           "edit_digest", "edit_summary", "cut_frames", "shot_at", "total_frames",
           "transition_kinds"]

_KEYS_EDIT = ("schema", "id", "bind_ref", "score_ref", "frame_base", "shots",
              "alignment", "notes")
_KEYS_REF = ("name", "digest")
_KEYS_FRAME_BASE = ("fps", "frame_start", "frame_end")
_KEYS_SHOT = ("id", "shot_ref", "start_frame", "end_frame",
              "transition_in", "transition_out")
_KEYS_TRANS = ("kind", "quantize_beats", "tolerance_frames")
_KEYS_ALIGN = ("at_frame", "beat_s", "offset_frames")


class EditError(C.ContractError):
    """剪辑契约不合法（fail-closed：不猜、不补、不静默降级）。"""


def _is_hex(s):
    return bool(s) and all(ch in "0123456789abcdefABCDEF" for ch in s)


def _digest_str(d, where):
    v = C.req_str(d, "digest", where)
    if _is_hex(v) and len(v) != 64:
        raise EditError(
            "%s.digest 形如十六进制但非 sha256（须 64 位），实为 %r" % (where, v))
    return v


def _norm_ref(d, key, where):
    r = C.req_map(d, key, where)
    C.reject_unknown(r, _KEYS_REF, "%s.%s" % (where, key))
    return {"name": C.req_str(r, "name", "%s.%s" % (where, key)),
            "digest": _digest_str(r, "%s.%s" % (where, key))}


def _norm_transition(t, where):
    if t is None:
        return {"kind": "cut"}
    if not isinstance(t, dict):
        raise EditError("%s 须为对象，实为 %s" % (where, type(t).__name__))
    C.reject_unknown(t, _KEYS_TRANS, where)
    out = {"kind": C.opt_enum(t, "kind", "cut", where, _transition_kinds())}
    if t.get("quantize_beats") is not None:
        qb = C.opt_int(t, "quantize_beats", 1, where)
        if qb < 1:
            raise EditError("%s.quantize_beats 须 >= 1，实为 %r" % (where, qb))
        out["quantize_beats"] = qb
    if t.get("tolerance_frames") is not None:
        tf = C.opt_int(t, "tolerance_frames", 1, where)
        if tf < 0:
            raise EditError("%s.tolerance_frames 须 >= 0，实为 %r" % (where, tf))
        out["tolerance_frames"] = tf
    return out


# ─────────────────────────────────────────────────────────── 规范化（执行面）
def normalize_edit(ed):
    """校验并补全默认值；返回**新的**规范化 edit（不修改入参）。

    补全的默认值：
      shot.transition_in / transition_out -> {"kind": "cut"}
      alignment                           -> []

    生效条件：合法 ⇒ 返回新 dict；不合法 ⇒ raise EditError（消息含字段路径）。
    """
    if not isinstance(ed, dict):
        raise EditError("edit 顶层须为对象，实为 %s" % type(ed).__name__)
    C.check_schema(ed, EDIT_SCHEMA, "edit")
    C.reject_unknown(ed, _KEYS_EDIT, "edit 顶层")

    out = {"schema": EDIT_SCHEMA}
    out["id"] = C.req_str(ed, "id", "edit")
    out["bind_ref"] = _norm_ref(ed, "bind_ref", "edit")
    out["score_ref"] = _norm_ref(ed, "score_ref", "edit")

    fb = C.req_map(ed, "frame_base", "edit")
    C.reject_unknown(fb, _KEYS_FRAME_BASE, "edit.frame_base")
    fs = C.req_int(fb, "frame_start", "edit.frame_base")
    fe = C.req_int(fb, "frame_end", "edit.frame_base")
    if fe <= fs:
        raise EditError("edit.frame_base 的 frame_end(%d) 须晚于 frame_start(%d)"
                        % (fe, fs))
    out["frame_base"] = {"fps": C.positive(C.req_num(fb, "fps", "edit.frame_base"),
                                           "edit.frame_base.fps"),
                         "frame_start": fs, "frame_end": fe}

    shots = []
    for i, s in enumerate(C.req_list(ed, "shots", "edit")):
        w = "edit.shots[%d]" % i
        if not isinstance(s, dict):
            raise EditError("%s 须为对象，实为 %s" % (w, type(s).__name__))
        C.reject_unknown(s, _KEYS_SHOT, w)
        st = C.req_int(s, "start_frame", w)
        en = C.req_int(s, "end_frame", w)
        if en < st:
            raise EditError("%s 的 end_frame(%d) 早于 start_frame(%d)" % (w, en, st))
        shots.append({"id": C.req_str(s, "id", w),
                      "shot_ref": _norm_ref(s, "shot_ref", w),
                      "start_frame": st, "end_frame": en,
                      "transition_in": _norm_transition(s.get("transition_in"),
                                                        w + ".transition_in"),
                      "transition_out": _norm_transition(s.get("transition_out"),
                                                         w + ".transition_out")})
    if not shots:
        raise EditError("edit.shots 不得为空（时间轴至少一个镜头）")

    ids = [s["id"] for s in shots]
    dup = sorted({x for x in ids if ids.count(x) > 1})
    if dup:
        raise EditError("edit.shots 出现重复的 id：%s —— 镜头 id 须唯一" % ", ".join(dup))

    for a, b in zip(shots, shots[1:]):
        if b["start_frame"] != a["end_frame"] + 1:
            raise EditError(
                "时间轴不连续：镜头 %s 结束于第 %d 帧，但下一个 %s 起于第 %d 帧 —— "
                "须恰好衔接（不重叠、不留缝）"
                % (a["id"], a["end_frame"], b["id"], b["start_frame"]))
    if shots[0]["start_frame"] != fs:
        raise EditError("首个镜头须起于 frame_start=%d，实为 %d"
                        % (fs, shots[0]["start_frame"]))
    if shots[-1]["end_frame"] != fe:
        raise EditError("末个镜头须止于 frame_end=%d，实为 %d"
                        % (fe, shots[-1]["end_frame"]))
    out["shots"] = shots

    cutset = {s["start_frame"] for s in shots[1:]}
    al = []
    for i, a in enumerate(C.opt_list(ed, "alignment", "edit")):
        w = "edit.alignment[%d]" % i
        if not isinstance(a, dict):
            raise EditError("%s 须为对象，实为 %s" % (w, type(a).__name__))
        C.reject_unknown(a, _KEYS_ALIGN, w)
        af = C.req_int(a, "at_frame", w)
        if af not in cutset:
            raise EditError(
                "%s.at_frame=%d 不是任何切点 —— 对齐声明只能挂在真实切点上"
                % (w, af))
        bs = C.req_num(a, "beat_s", w)
        if bs < 0.0:
            raise EditError("%s.beat_s 须非负（秒），实为 %r" % (w, bs))
        al.append({"at_frame": af, "beat_s": bs,
                   "offset_frames": C.opt_int(a, "offset_frames", 0, w)})
    out["alignment"] = al

    if ed.get("notes") is not None:
        out["notes"] = C.req_str(ed, "notes", "edit")
    return out


# ─────────────────────────────────────────────────────────── 查询面与摘要
def validate_edit(ed):
    """查询面：返回 (ok: bool, reason: str)。**不抛异常、不改入参。**"""
    return C.validate_with(normalize_edit, ed)


def edit_digest(ed):
    """剪辑契约的内容摘要（sha256 hex）。只依赖内容。"""
    return C.digest_of(normalize_edit(ed))


def transition_kinds():
    """转场闭集（权威 = wmfilm.shot/1 的 TRANSITION_KINDS）。"""
    return _transition_kinds()


def cut_frames(ed):
    """全部切点的帧号（即除首镜头外每个镜头的起帧），升序。"""
    n = normalize_edit(ed)
    return [s["start_frame"] for s in n["shots"][1:]]


def total_frames(ed):
    """时间轴总帧数（闭区间计数）。"""
    fb = normalize_edit(ed)["frame_base"]
    return fb["frame_end"] - fb["frame_start"] + 1


def shot_at(ed, frame):
    """取该帧所属的镜头（越界 ⇒ None）。"""
    for s in normalize_edit(ed)["shots"]:
        if s["start_frame"] <= frame <= s["end_frame"]:
            return s
    return None


def edit_summary(ed):
    """人读摘要（用于报告与守卫断言，不参与哈希）。"""
    n = normalize_edit(ed)
    return {"schema": n["schema"], "id": n["id"], "bind": n["bind_ref"]["name"],
            "score": n["score_ref"]["name"], "fps": n["frame_base"]["fps"],
            "frames": total_frames(n), "shots": len(n["shots"]),
            "cuts": len(cut_frames(n)), "aligned": len(n["alignment"]),
            "shot_ids": [s["id"] for s in n["shots"]],
            "digest": edit_digest(n)}

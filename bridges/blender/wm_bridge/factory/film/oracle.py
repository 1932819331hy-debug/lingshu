# -*- coding: utf-8 -*-
"""wm_bridge.factory.film.oracle · 验收裁定契约 wmfilm.oracle/1（纯标准库 · 零 bpy）

★ 这一份契约要回答什么
    判据门跑完会得到一堆 (判据名, 通过?, 实测值) 的读数。如果没有一份**固定的**
    载体来记录它们，就会出现三类漂移：
      1. 换个人跑同一批判据，读数格式不同，无法比较；
      2. 有人改了阈值却不说是谁改的、依据是什么；
      3. 某条判据这次没跑（缺依赖 / 缺产物），却被当成「过了」。
    wmfilm.oracle/1 用**四态**（ACCEPT / REJECT / DEFER / BLINDSPOT）把这三件事
    一次性堵死 —— 与既有 wm_bridge.oracle/1（§3.2 已登记）同源同义，本契约是
    它在**影视判据**上的落地。

★ 四态的语义（**与既有 oracle 一致，不另立**）
    ACCEPT    —— 判据跑了、通过，且证据齐（measured 非空、threshold 有来源）。
    REJECT    —— 判据跑了、不通过。必须带 measured 与 threshold（否则无法归因）。
    DEFER     —— 判据**没跑**（缺外部产物 / 缺依赖 / 不在首批范围）。
                 必须带 reason，且**不得**带非空 measured —— 没跑就没有实测值，
                 编一个出来正是本契约要防的事。（空列表/缺省 = 「无读数」，允许；
                 规则定在**内容**上而非**键是否存在**上，否则每个生产者都要特判一次。）
    BLINDSPOT —— 判据跑了，但结论**不可判**（如：契约缺字段、参考口径未录）。
                 必须带 reason。与 DEFER 的区别：DEFER 是「没跑」，BLINDSPOT 是
                 「跑了但判不了」—— 后者更危险，因为它看起来像有结论。

★ 为什么 measured / threshold 是**列表**而不是标量
    一条判据常需多路读数（如 E1 节拍同步要同时报「切点数」「对齐数」「最大偏差帧」）。
    用列表并保留顺序，读数与判据文档的条目一一对应，可逐条核对。

★ 本模块只立**契约与校验器**（§七 P0），不含判据实现（判据在 _det/）。
"""
from __future__ import annotations

import re as _re

from . import _common as C

#: 契约标识
ORACLE_SCHEMA = "wmfilm.oracle/1"

#: 四态（闭集 —— 与既有 wm_bridge.oracle/1 同义）
VERDICTS = ("ACCEPT", "REJECT", "DEFER", "BLINDSPOT")

#: 判据族（§4.2 登记的**六族**：C 构图 / M 运镜 / K 连续性 / E 剪辑 / A 氛围 / P 表演）。
#: 本版首批实现 K 族 K1-K5 与 E1，其余登记暂不实现。
#: ★ 注意：`wmmv.text/1` 的文本类校验（CPS / 行宽 / 安全区）**不在此六族内** ——
#:   它们在 §4.2 里没有编号，是在**契约层**硬拦的（见 film/text.py）。
#:   把契约族与判据族混为一谈，会让 family_of 收下一个并不存在的族字母。
FAMILIES = ("C", "M", "K", "E", "A", "P")

#: 必须带**非空**实测值的态 —— 只有「跑出了结论」的态才有这个义务。
#: ★ BLINDSPOT 刻意**不在**此列：它的语义是「跑了但判不了」，缺料时往往一条读数都没有，
#:   强制非空会把「缺料」逼成「编一个读数」—— 正是本契约要防的事。
#:   BLINDSPOT 允许带**部分**读数（判到一半才发现不可判时保留已得读数）。
_NEEDS_MEASURED = ("ACCEPT", "REJECT")

#: 判据名必须形如 K1 / E1 / M3 / P2a（族字母 + 序号 + 可选后缀）。
#: 名字是判据与文档的**唯一连接点**，自由字符串会让「同一条判据两种写法」
#: 在报告里分裂，而按名取用的判据门只能命中其中一派 —— 静默漏判。
_NAME_RE = _re.compile(r"^([CMKEAP])([0-9]{1,2})(?:[a-z_][a-z0-9_]*)?$")

__all__ = ["ORACLE_SCHEMA", "VERDICTS", "FAMILIES", "OracleError",
           "normalize_oracle", "validate_oracle", "oracle_digest", "oracle_summary",
           "counts_by_verdict", "family_of", "failed_checks", "gates_passed"]

_KEYS_ORACLE = ("schema", "id", "subject_ref", "thresholds", "checks",
                "generated_by", "notes")
_KEYS_REF = ("name", "digest")
_KEYS_THRESHOLD = ("name", "value", "source")
_KEYS_CHECK = ("name", "family", "verdict", "measured", "threshold",
               "reason", "evidence")


class OracleError(C.ContractError):
    """验收裁定不合法（fail-closed：不猜、不补、不静默降级）。"""


def _is_hex(s):
    return bool(s) and all(ch in "0123456789abcdefABCDEF" for ch in s)


def _digest_str(d, where):
    v = C.req_str(d, "digest", where)
    if _is_hex(v) and len(v) != 64:
        raise OracleError(
            "%s.digest 形如十六进制但非 sha256（须 64 位），实为 %r" % (where, v))
    return v


def family_of(check_name):
    """由判据名取族字母；名字不合规 ⇒ raise。"""
    m = _NAME_RE.match(check_name)
    if not m:
        raise OracleError(
            "判据名 %r 不合规 —— 须形如 <族字母><序号>[<后缀>]，族字母取自 %s，"
            "如 K1 / E1 / M3 / P2a" % (check_name, "".join(FAMILIES)))
    return m.group(1)


# ─────────────────────────────────────────────────────────── 规范化（执行面）
def normalize_oracle(or_):
    """校验并补全默认值；返回**新的**规范化 oracle（不修改入参）。

    补全的默认值：
      thresholds   -> []
      checks       -> []
      check.family -> 由 check.name 推导（**不由调用者自报** —— 自报即可漂移）

    生效条件：合法 ⇒ 返回新 dict；不合法 ⇒ raise OracleError（消息含字段路径）。
    """
    if not isinstance(or_, dict):
        raise OracleError("oracle 顶层须为对象，实为 %s" % type(or_).__name__)
    C.check_schema(or_, ORACLE_SCHEMA, "oracle")
    C.reject_unknown(or_, _KEYS_ORACLE, "oracle 顶层")

    out = {"schema": ORACLE_SCHEMA}
    out["id"] = C.req_str(or_, "id", "oracle")

    sr = C.req_map(or_, "subject_ref", "oracle")
    C.reject_unknown(sr, _KEYS_REF, "oracle.subject_ref")
    out["subject_ref"] = {"name": C.req_str(sr, "name", "oracle.subject_ref"),
                          "digest": _digest_str(sr, "oracle.subject_ref")}

    # ---- thresholds：阈值必须带来源（规范名 / 附录位置），否则等于没依据
    ths, names = [], set()
    for i, t in enumerate(C.opt_list(or_, "thresholds", "oracle")):
        w = "oracle.thresholds[%d]" % i
        if not isinstance(t, dict):
            raise OracleError("%s 须为对象，实为 %s" % (w, type(t).__name__))
        C.reject_unknown(t, _KEYS_THRESHOLD, w)
        nm = C.req_str(t, "name", w)
        if nm in names:
            raise OracleError("oracle.thresholds 重复声明 %r —— 同一阈值只能有一个定义"
                              % nm)
        names.add(nm)
        src = C.req_str(t, "source", w)
        if not src.strip():
            raise OracleError(
                "%s.source 不得为空 —— 每个阈值都要能追到依据（规范名或附录位置），"
                "否则「凭什么定这个数」无从复核" % w)
        ths.append({"name": nm, "value": C.req_num(t, "value", w), "source": src})
    out["thresholds"] = ths
    th_names = {t["name"] for t in ths}

    # ---- checks
    cks, cnames = [], set()
    for i, c in enumerate(C.opt_list(or_, "checks", "oracle")):
        w = "oracle.checks[%d]" % i
        if not isinstance(c, dict):
            raise OracleError("%s 须为对象，实为 %s" % (w, type(c).__name__))
        C.reject_unknown(c, _KEYS_CHECK, w)
        nm = C.req_str(c, "name", w)
        fam = family_of(nm)
        if nm in cnames:
            raise OracleError("oracle.checks 重复声明判据 %r —— 同一判据只能有一个裁定"
                              % nm)
        cnames.add(nm)
        vd = C.enum(C.req_str(c, "verdict", w), VERDICTS, w + ".verdict")

        meas_in = c.get("measured")
        if vd in _NEEDS_MEASURED and meas_in is None:
            raise OracleError(
                "%s 的 verdict=%s 必须带 measured —— 跑了/判了就必须有实测读数，"
                "否则该裁定无法复核" % (w, vd))
        if vd == "DEFER" and meas_in:
            raise OracleError(
                "%s 的 verdict=DEFER 不得带**非空** measured —— DEFER 的语义是「没跑」，"
                "编一个实测值出来正是本契约要防的事（空列表/缺省表示「无读数」，允许）"
                % w)
        meas = []
        if meas_in is not None:
            if not isinstance(meas_in, list):
                raise OracleError("%s.measured 须为列表，实为 %r" % (w, meas_in))
            if not meas_in and vd in _NEEDS_MEASURED:
                raise OracleError(
                    "%s 的 verdict=%s 必须带**非空** measured，实为空列表" % (w, vd))
            for j, x in enumerate(meas_in):
                if not C.is_num(x):
                    raise OracleError("%s.measured[%d] 须为有限数值，实为 %r"
                                      % (w, j, x))
                meas.append(float(x))

        thn = c.get("threshold")
        if thn is not None:
            thn = C.req_str(c, "threshold", w)
            if thn not in th_names:
                raise OracleError(
                    "%s.threshold=%r 未在 oracle.thresholds 中声明 —— "
                    "判据引用的阈值必须先在顶层登记（含 source），否则依据丢失"
                    % (w, thn))

        rsn = c.get("reason")
        if vd in ("DEFER", "BLINDSPOT"):
            if rsn is None or not str(rsn).strip():
                raise OracleError(
                    "%s 的 verdict=%s 必须带非空 reason —— "
                    "「为什么没跑」/「为什么判不了」是这一态唯一的信息量" % (w, vd))

        e = {"name": nm, "family": fam, "verdict": vd}
        if meas:                       # 空读数不写进文档（避免 digest 被空壳字段污染）
            e["measured"] = meas
        if thn is not None:
            e["threshold"] = thn
        if rsn is not None:
            e["reason"] = C.req_str(c, "reason", w)
        if c.get("evidence") is not None:
            e["evidence"] = C.req_str(c, "evidence", w)
        cks.append(e)
    out["checks"] = cks

    if or_.get("generated_by") is not None:
        out["generated_by"] = C.req_str(or_, "generated_by", "oracle")
    if or_.get("notes") is not None:
        out["notes"] = C.req_str(or_, "notes", "oracle")
    return out


# ─────────────────────────────────────────────────────────── 查询面与摘要
def validate_oracle(or_):
    """查询面：返回 (ok: bool, reason: str)。**不抛异常、不改入参。**"""
    return C.validate_with(normalize_oracle, or_)


def oracle_digest(or_):
    """验收裁定的内容摘要（sha256 hex）。只依赖内容。"""
    return C.digest_of(normalize_oracle(or_))


def counts_by_verdict(or_):
    """四态各自的条数（缺失的态记为 0）。"""
    d = {v: 0 for v in VERDICTS}
    for c in normalize_oracle(or_)["checks"]:
        d[c["verdict"]] += 1
    return d


def failed_checks(or_):
    """REJECT 的判据名（升序）。"""
    return sorted(c["name"] for c in normalize_oracle(or_)["checks"]
                  if c["verdict"] == "REJECT")


def gates_passed(or_):
    """本批裁定是否可放行：无 REJECT、无 BLINDSPOT。

    ★ 为什么 BLINDSPOT 也拦：判不了的判据**看起来像有结论**，比明确的 DEFER 更危险。
      DEFER 是「我知道我没跑」，BLINDSPOT 是「我跑了但说不清」—— 后者必须回到
      契约层补字段，而不是放行。
    """
    bad = [c["name"] for c in normalize_oracle(or_)["checks"]
           if c["verdict"] in ("REJECT", "BLINDSPOT")]
    return (len(bad) == 0), bad


def oracle_summary(or_):
    """人读摘要（用于报告与守卫断言，不参与哈希）。"""
    n = normalize_oracle(or_)
    ok, bad = gates_passed(n)
    return {"schema": n["schema"], "id": n["id"],
            "subject": n["subject_ref"]["name"],
            "checks": len(n["checks"]), "counts": counts_by_verdict(n),
            "thresholds": len(n["thresholds"]),
            "gates_passed": ok, "blocking": bad,
            "digest": oracle_digest(n)}

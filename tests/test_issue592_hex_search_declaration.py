# -*- coding: utf-8 -*-
"""守卫 · #592 `hex_search` 两处「声明 vs 实现」收口
====================================================================================
议题：`lingshu/nn/hex_search.py` 的
  ① `blindspot_evidence[*]["pos"]` 声明为「位置」（docstring 原文「停因/位置/深度/
     份额单列」）却实写 `f"d{depth}"` —— 即 `depth` 的别名，与 `found[*]["pos"]`
     的 `r0..r8` **同名不同域**；
  ② `evaluate_node` 注释写「置信≥0.5 且领先≥0.3」而签名默认 `th_conf=0.35` /
     `th_margin=0.12`。

哪一侧对（消费者读数，见 `# 判定` 注释块）：
  · `blindspot_evidence` 的唯一消费者在 `tests/test_hex_search.py`（只读 `stop`/
    `depth`/`share_ratio`），仓内**无任何代码**读 `ev["pos"]`；而 `pos` 这个键名在
    `found` 侧的消费者是 `search_report`（`det_pos = {f["pos"]}` 与 ground-truth
    `labels[i]["pos"]` 集合比对），ground-truth 的 `pos` 形态恒为 `r0..r8`（G1b
    直接读它）。⇒ 同返回体系里的同名键必须同域，**声明侧（位置）是设计意图，
    实现侧是缺陷** ⇒ 修实现（两处 `note_blindspot` 传真实象限），并把口径抽成
    唯一的 `pos_of`。
  · ② 侧：默认值被 `calibrate_thresholds` 与既有 `test_hex_search.py` 的剪枝/
    收敛基线共同依赖 ⇒ **不动默认值**，改注释。

判据（每条都能独立判红）：
  G1a 行为面：死区停 / `max_depth` 上界停两条终止路径的 `ev["pos"]` 都是 `r0..r8`
      且 `!= f"d{ev['depth']}"`；**并硬性断言证据非空**（空证据会让本量具空转成假绿）。
  G1b 量具对照：正对照 = 无改动模块必过（量具不是恒红）；负对照 = 把 pos 写成非
      象限串的变异模块必红（量具不是恒绿）；另证明「默认配置零证据」这一负读数。
  G1c 静态面：两处 `note_blindspot` 第一实参都走 `pos_of(...)`、历史缺陷串
      `note_blindspot(f"d{depth}"` 缺席、`pos_of` 是唯一口径（found 侧同走）。
  G2 文档一致性：注释里的「置信≥X 且领先≥Y」取自注释字面量（非硬编码），与
      `inspect.signature(evaluate_node)` 的默认值逐位一致 —— 改注释即判红。
  M1/M2/M3 变异自证：逐处 / 全处还原 `f"d{depth}"`、注释数字改错，均须判红且
      点名（源码字符串手术前先断言 `count(old) == 1`，规避 CRLF 静默不命中）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue592_hex_search_declaration.py -q --no-header
"""
import ast
import inspect
import io
import os
import re
import sys
import types

_RP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _RP not in sys.path:
    sys.path.insert(0, _RP)

import numpy as np
import pytest

from lingshu.nn.hex_hier import HexHierNet
from lingshu.nn.hex_search import evaluate_node, recursive_search
from lingshu.nn.hex_text import make_multimodal_scene
from lingshu.nn.hex_train import normalize_lattices

_HEX = os.path.join(_RP, "lingshu", "nn", "hex_search.py")

# 位置字段的既有口径：ground-truth / found.pos / 修后 ev.pos 同域
_POS_RE = re.compile(r"^r[0-8]$")

# 历史缺陷串（两处调用点都曾这样传 pos）
_DEFECT_A = 'note_blindspot(f"d{depth}", depth, "infogap_dead_zone",'
_DEFECT_B = 'note_blindspot(f"d{depth}", depth, "max_depth", conf=node["conf"])'

# 变异锚点：修复态（单行归一化形态，供量具对照与变异手术共用）
_FIXED_DEADZONE = ('note_blindspot(pos_of(qy0, qy1, qx0, qx1), depth,'
                   ' "infogap_dead_zone",')
_FIXED_MAXDEPTH = ('note_blindspot(pos_of(qy0, qy1, qx0, qx1), depth,'
                   ' "max_depth", conf=node["conf"])')
_FIXED_FOUND_POS = 'pos = pos_of(qy0, qy1, qx0, qx1)'

# 磁盘上的两行修复形态（归一化时收敛到上面单行形态）
_FIXED_DEADZONE_2L = ('note_blindspot(pos_of(qy0, qy1, qx0, qx1), depth,\n'
                      '                           "infogap_dead_zone",')
_FIXED_MAXDEPTH_2L = ('note_blindspot(pos_of(qy0, qy1, qx0, qx1), depth,'
                      ' "max_depth",\n'
                      '                           conf=node["conf"])')


# ==================== 源码手术（CRLF 安全） ====================

def _src_lf():
    """源码文本，统一 LF（本仓 core.autocrlf=true，工作树是 CRLF）。"""
    raw = io.open(_HEX, encoding="utf-8", newline="").read()
    return raw.replace("\r\n", "\n")


def _fixed_src():
    """把当前源码归一化到「单行修复态」（幂等，对缺陷态/两行修复态都收敛）。

    量具对照（正/负对照、M1/M2/M3）一律以本函数为基底 —— 与磁盘此刻是修复态
    还是缺陷态解耦，从而在任意磁盘态下都能独立证明「检测器有判别力」。"""
    src = _src_lf()
    for old, new in ((_DEFECT_A, _FIXED_DEADZONE), (_DEFECT_B, _FIXED_MAXDEPTH),
                     (_FIXED_DEADZONE_2L, _FIXED_DEADZONE),
                     (_FIXED_MAXDEPTH_2L, _FIXED_MAXDEPTH)):
        if src.count(old) == 1:
            src = src.replace(old, new)
    return src


def _mutate_src(repls, base=None):
    """逐条字符串手术；每条先断言命中恰 1 次（锚点漂移/CRLF 静默不命中即失败）。"""
    src = _fixed_src() if base is None else base
    for old, new in repls:
        n = src.count(old)
        assert n == 1, "变异锚点命中 %d 次（应为 1）: %r" % (n, old)
        src = src.replace(old, new)
    return src


def _load_module(src, name):
    """把（手术后的）源码 exec 成独立模块 —— 供变异自证真跑，不落盘。"""
    mod = types.ModuleType(name)
    mod.__file__ = _HEX
    mod.__package__ = "lingshu.nn"
    sys.modules[name] = mod
    try:
        exec(compile(src, _HEX, "exec"), mod.__dict__)
    finally:
        sys.modules.pop(name, None)
    return mod


def _load_mutated(repls, name="_b592_mut", base=None):
    return _load_module(_mutate_src(repls, base=base), name)


# ==================== 检测器 ====================

def _depth_alias_sites(src):
    """`note_blindspot(...)` 中『第一实参不是 `pos_of(...)`』的调用行号清单。

    这是「pos 又被写成 depth 别名」的机器判据：`f"d{depth}"` 或任何非 `pos_of`
    表达式一律点名。"""
    bad = []
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "note_blindspot" and node.args):
            a0 = node.args[0]
            ok = (isinstance(a0, ast.Call) and isinstance(a0.func, ast.Name)
                  and a0.func.id == "pos_of")
            if not ok:
                bad.append(node.lineno)
    return bad


_TH_COMMENT_RE = re.compile(r"ACCEPT\(置信≥([0-9.]+)\s*且领先≥([0-9.]+)")


def _comment_thresholds(src):
    """从源码注释里取出 (置信阈值, 领先阈值) 字面量；无则 None。"""
    for ln in src.splitlines():
        m = _TH_COMMENT_RE.search(ln)
        if m:
            return float(m.group(1)), float(m.group(2))
    return None


def _check_comment_matches_signature(src):
    """G2 守卫本体：注释字面量 == 签名默认值。"""
    got = _comment_thresholds(src)
    assert got is not None, "evaluate_node 注释里的阈值描述缺失（锚点漂移）"
    sig = inspect.signature(evaluate_node)
    want = (sig.parameters["th_conf"].default, sig.parameters["th_margin"].default)
    assert got == want, (
        "#592 ②：注释阈值 %r 与签名默认 %r 不符（读者按注释复算会误判）"
        % (got, want))
    return got


# ==================== G1 守卫本体（可对任意模块实例求值） ====================

def _assert_pos_ok(ev, tag):
    for e in ev:
        assert _POS_RE.match(str(e.get("pos"))), (
            "#592 ①：%s 证据 pos 不是位置（r0..r8）: %r" % (tag, e))
        assert e["pos"] != "d%d" % e["depth"], (
            "#592 ①：%s 证据 pos 是 depth 别名（缺陷复发）: %r" % (tag, e))


def _lat(seed):
    return normalize_lattices(np.random.default_rng(seed).random((1, 6, 6, 3)))


def _check_evidence_pos(mod):
    """G1a：两条终止路径的 ev['pos'] 必须是真实位置；返回样本条数。

    非空断言是**量具有效性的前提**：若证据为空，下面的 for 循环会空转成假绿。"""
    net = HexHierNet(n_kernels=6, n_kernels2=6, stacked=True, seed=7)
    seen = 0
    # (a) 死区停：min_share 调高 ⇒ 6×6 子域份额必低于 0.5×父域
    found, stats = mod.recursive_search(net, _lat(1), max_depth=2,
                                        adaptive_depth=True, min_share=0.5)
    ev = [e for e in stats["blindspot_evidence"] if e["stop"] == "infogap_dead_zone"]
    assert ev, "死区停零证据 —— 量具空转（非被测代码通过）"
    _assert_pos_ok(ev, "死区停")
    seen += len(ev)
    # (b) 工程保险上界停：max_depth=0 ⇒ 根节点必 DEFER ⇒ 一条 max_depth 证据
    for seed in (1, 2, 3):
        _f, s = mod.recursive_search(net, _lat(seed), max_depth=0)
        evb = [e for e in s["blindspot_evidence"] if e["stop"] == "max_depth"]
        assert evb, "max_depth 上界停零证据（seed=%d）—— 量具空转" % seed
        _assert_pos_ok(evb, "max_depth 停")
        seen += len(evb)
    # 同域一致性：found 侧与证据侧同为 r0..r8（同名键同域）
    for f in found:
        assert _POS_RE.match(f["pos"]), "found.pos 漂出象限域: %r" % f
    return seen


# ==================== G1：第 1 处 ====================

def test_g1a_evidence_pos_is_real_position():
    """两种终止路径下 ev['pos'] 都是 r0..r8，且不是 d{depth} 别名。"""
    n = _check_evidence_pos(sys.modules[recursive_search.__module__])
    assert n >= 4, "证据样本过少，判别力不足: %d" % n


def test_g1b_controls_meter_is_neither_always_green_nor_always_red():
    """量具对照（正/负各一）+ 一条负读数（默认配置零证据）。

    正对照：把源码**归一化到修复态**后重载 ⇒ 必过（量具不是恒红；在磁盘处于
    缺陷态时这一条正是「手动打开通路」的正读数）。
    负对照：把 pos 写成非象限串 `"zz"` 的变异模块 ⇒ 必红（量具不是恒绿）。
    负读数：默认配置（adaptive_depth=False）证据为零 ⇒ 这正是必须硬性断言非空的理由。
    """
    # 正对照（必然为正）
    ok_mod = _load_mutated([], name="_b592_ok")
    assert _check_evidence_pos(ok_mod) >= 4

    # 负读数：默认配置零证据
    net = HexHierNet(n_kernels=6, n_kernels2=6, stacked=True, seed=7)
    _f, s = recursive_search(net, _lat(1), max_depth=2)
    assert s["blindspot_evidence"] == [], (
        "默认配置本应零证据（此读数是「必须硬性断言非空」的依据）: %r"
        % s["blindspot_evidence"])

    # 负对照（必然为负）：pos 换成非象限串
    bad_mod = _load_mutated([
        (_FIXED_MAXDEPTH, 'note_blindspot("zz", depth, "max_depth",\n'
                          '                           conf=node["conf"])')])
    with pytest.raises(AssertionError, match="pos 不是位置"):
        _check_evidence_pos(bad_mod)


def test_g1c_source_calls_go_through_pos_of():
    """静态面：两处 note_blindspot 第一实参都走 pos_of；缺陷串缺席；口径唯一。"""
    src = _src_lf()
    assert _DEFECT_A not in src, "死区停调用点复发 #592 缺陷: %r" % _DEFECT_A
    assert _DEFECT_B not in src, "max_depth 停调用点复发 #592 缺陷: %r" % _DEFECT_B
    sites = [n for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "note_blindspot" and n.args]
    assert len(sites) == 2, "note_blindspot 调用点数变了（锚点漂移）: %d" % len(sites)
    assert _depth_alias_sites(src) == [], (
        "note_blindspot 第一实参未走 pos_of（pos 又成 depth 别名）: 行 %r"
        % _depth_alias_sites(src))
    assert "def pos_of(qy0" in src, "唯一口径函数 pos_of 缺失"
    assert src.count("pos_of(qy0, qy1, qx0, qx1)") == 3, (
        "pos_of 调用点数应为 3（两处 note_blindspot + found.pos），实为 %d —— 口径漂移"
        % src.count("pos_of(qy0, qy1, qx0, qx1)"))


# ==================== G2：第 2 处 ====================

def test_g2_comment_thresholds_match_signature_defaults():
    """注释里的「置信≥X 且领先≥Y」必须与签名默认逐位一致。"""
    got = _check_comment_matches_signature(_src_lf())
    assert got == (0.35, 0.12), "修复后的注释数值应即签名默认: %r" % (got,)
    assert (0.5, 0.3) != got, "注释退回历史绝对数 0.5/0.3（既不符实现也无可追溯来源）"


# ==================== M1/M2/M3：变异自证 ====================

def test_m1_mutation_single_site_revert_is_flagged():
    """只抽一处（max_depth 停）⇒ 检测器点名 1 处 + 行为面在该路径变红。"""
    src1 = _mutate_src([(_FIXED_MAXDEPTH,
                         'note_blindspot(f"d{depth}", depth, "max_depth",'
                         ' conf=node["conf"])')])
    assert len(_depth_alias_sites(src1)) == 1, "M1：只抽一处，检测器未点名 1 处"
    m1 = _load_module(src1, "_b592_m1")
    with pytest.raises(AssertionError, match="pos 不是位置|depth 别名"):
        _check_evidence_pos(m1)


def test_m2_mutation_all_sites_revert_is_flagged():
    """两处全抽掉（含 found 侧口径）⇒ 检测器点名 2 处 + 行为面在两条路径都红。"""
    src2 = _mutate_src([
        (_FIXED_DEADZONE,
         'note_blindspot(f"d{depth}", depth, "infogap_dead_zone",'),
        (_FIXED_MAXDEPTH,
         'note_blindspot(f"d{depth}", depth, "max_depth", conf=node["conf"])'),
        (_FIXED_FOUND_POS,
         'cy, cx = (qy0 + qy1) / 2, (qx0 + qx1) / 2\n'
         '            pos = f"r{min(2, int(cy * 3)) * 3 + min(2, int(cx * 3))}"'),
    ])
    assert len(_depth_alias_sites(src2)) == 2, "M2：全抽掉，检测器未点名 2 处"
    m2 = _load_module(src2, "_b592_m2")
    with pytest.raises(AssertionError, match="pos 不是位置|depth 别名"):
        _check_evidence_pos(m2)


def test_m3_mutation_comment_number_is_flagged():
    """把注释里的 0.35/0.12 改回历史 0.5/0.3 ⇒ G2 判红（文档守卫有判别力）。"""
    src3 = _mutate_src([("ACCEPT(置信≥0.35 且领先≥0.12",
                         "ACCEPT(置信≥0.5 且领先≥0.3")])
    assert _comment_thresholds(src3) == (0.5, 0.3)
    with pytest.raises(AssertionError, match="与签名默认"):
        _check_comment_matches_signature(src3)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

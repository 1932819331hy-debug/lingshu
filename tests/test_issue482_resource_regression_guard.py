# -*- coding: utf-8 -*-
"""test_issue482_resource_regression_guard · lingshu issue #482 守卫
============================================================================
议题（公开 issue #482 · P1 · 资源耗尽）：10/10–10/11 修复批次引入/遗留的两处资源回归。

缺陷一 —— `import_all` 的 #93 孤儿边预过滤是 O(E²)
  位置：`lingshu/core/core.py` 内 `import_all` 的 `table == "edges"` 分支。
  特征串（缺陷态）：`orphan_rows = [r for r in rows if r not in keep]`
  —— `keep` 是 list，`r not in keep` 逐 dict 比较 ⇒ 约 E²/2 次 dict 比较，且与同一
  `_transactional` 写事务内的批量 `INSERT` 共处一个事务、全程持有写锁。
  本机实测（AMD64 / python 3.12.10）：E=1000 → 499500 次比较 / 0.06s；
  E=2000 → 1999000 / 0.22s；E=4000 → 7998000 / 0.79s；E=8000 → 31996000 / 3.08s
  （比较数逐字等于 E²/2；边数翻倍耗时约 ×4）。

缺陷二 —— `reason_causal(start, end_id)` 终点不可达时 `max_paths` 上限失效
  位置：`LayeredStore.infer_causal_paths`（`reason_causal` 的 `end_id` 分支唯一入口）。
  特征串（缺陷态）：`if len(all_paths) >= max_paths:` / `if depth > max_depth or ...`
  —— 上限挂在 `all_paths` 上，而 `all_paths` **仅在到达 end_id 时增长** ⇒ 终点不可达
  时该值恒 0、上限永不触发，DFS 照样走完 `max_depth` 以内的全部简单路径。
  本机实测（6 节点链、每跳 k 条平行边、max_paths=1000、max_depth=5，量具＝
  `store.get_outgoing_edges` 调用次数）：k=4 → 1365 次展开 / 0.01s；
  k=8 → 37449 / 0.29s；k=12 → 271453 / 2.12s（同图可达查询恒 94–334 次展开）。

修法（两处都最小、都不改签名、都不改返回体形状）
  · 一：`keep` / `orphan_rows` 改**单趟分区**（判据仍是 `source_id/target_id in node_ids`
    的集合成员判断）⇒ O(E)，与旧判据逐字等价（含重复边、缺键、None 端点）。
  · 二：预算判据由「命中 end 的结果数」改为「已穷尽的**终态分支**数」（`enumerated`：
    完整路径 / 到深度预算仍有出边的截断分支 / 死胡同或仅非因果出边 / 回边收口各计 1）
    ⇒ 不可达分支同样受 `max_paths` 约束；是否耗尽在 `causal_stats.budget_exhausted`
    与 `.enumerated` 上外传（不静默），死胡同仍**不**标 `truncated`（#328 F 组口径）。

断言组
  A 组（缺陷一：不是 O(E²)）
    A0 **量具正对照**：同一量具（`json.load` 的 `object_pairs_hook` 产出 dict 子类并
       对 `__eq__` 计数）在**旧判据**上必须读到 ≥ E²/4 次比较 —— 证明量具能测到 O(E²)，
       否则 A1 的「计数为 0/线性」是量具失灵而非修复生效。
    A1 真实 `import_all` 路径的 dict 比较次数随 E **线性**（≤ 8E），且 3× 规模下增幅
       ≤ 12×（缺陷态：E=1200 即 719400 次 > 8E=9600 ⇒ 判红）。
    A2 **语义等价**（对含孤儿边/缺键/None/逐字重复行的真实备份）：新实现返回的
       `orphan_edges` / `orphan_edges_sample` / `imported["edges"]` 与**旧判据本地复算**
       的结果逐字一致，落库边数亦一致。
    A3 功能非回归：孤儿边仍不落库、计数上报、`integrity_ok=False`。
  B 组（缺陷二：不可达分支也受 max_paths 约束）
    B1 展开次数有界：不可达查询的 `get_outgoing_edges` 调用次数 ≤ max_paths×(max_depth+2)，
       且 k=4→12（3× 平行边）不增长（≤ 4×；缺陷态 k=12 为 k=4 的 199×）。
    B2 预算可观测：`causal_stats` 含 `budget_exhausted` / `enumerated` 两键，耗尽时为
       True 且 `enumerated >= max_paths`（缺陷态：两键缺失 ⇒ KeyError 判红）。
    B3 显式收紧生效：`max_paths=10` 在不可达分支上同样把展开钉在 10×(max_depth+2) 内。
    B4 **量具正对照**：同图把 `max_paths` 放到 10⁹（即「无预算」），展开次数必须回到
       3 万量级（k=8 实测 37449，与缺陷态读数逐字相同）⇒ 证明 B1 的小读数是预算生效、
       不是量具读不到大数。
    B5 可达分支读数不变：k=1 → 1 条、k=2 → 32 条、k=4 → `_CAUSAL_MAX_PATHS` 条；
       `max_paths=10` 时两分支都恰 10 条（#327 Q3 口径）。
    B6 #328 口径不误伤：死胡同（天然终点）不记 `truncated`；到深度预算仍有出边时截断
       证据照常上报。
    B7 不传 `end_id` 的分支不受影响（上限仍挂在 `chains` 条数上）。
    B8 **语义边界**（本件如实标注的代价）：预算可被死胡同/截断分支先耗尽 ⇒ 返回的完整
       路径可能**少于** `max_paths`（旧实现只在结果数达上限时截断）；该边界必须由
       `causal_stats.budget_exhausted` 显式外传（旧实现静默返回 1 条），且死胡同仍不标
       `truncated`。
  C 组（约束守卫）：三个公开方法的**签名形态**不得变化（BRIEF 硬约束；同仓
    `tests/test_param_wiring.py` 亦在断言签名形态）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue482_resource_regression_guard.py -q --no-header
脚本式：python -X utf8 tests/test_issue482_resource_regression_guard.py
退出码：0 = 全过；1 = 有断言失败
"""
from __future__ import annotations

import inspect
import json as _json
import os
import sys
import tempfile
import warnings

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
warnings.filterwarnings("ignore")

from lingshu.core.core import (  # noqa: E402
    _CAUSAL_MAX_PATHS,
    EdgeType,
    LayeredStore,
    SpacetimeMemoryEngine,
)

STEPS = ["乌云密布", "开始下雨", "路面湿滑", "车辆减速", "道路拥堵", "上班迟到"]


# ==========================================================================
# 量具一：dict 比较计数（json.load 打桩）
# ==========================================================================

class _CDict(dict):
    """dict 子类：每次 `==` / `!=` 比较即自增全局计数（量具本体）。"""
    __hash__ = dict.__hash__
    n = 0

    def __eq__(self, other):
        _CDict.n += 1
        return dict.__eq__(self, other)

    def __ne__(self, other):
        _CDict.n += 1
        return not dict.__eq__(self, other)


_REAL_LOAD = _json.load


def _load_counting(fp, **kw):
    kw["object_pairs_hook"] = _CDict
    return _REAL_LOAD(fp, **kw)


class _count_compares:
    """with 块内：`json.load` 产出的行都是 `_CDict`，全局比较计数可读。"""

    def __enter__(self):
        _CDict.n = 0
        _json.load = _load_counting
        return self

    def __exit__(self, *exc):
        self.n = _CDict.n
        _json.load = _REAL_LOAD
        return False


# ==========================================================================
# 量具二：节点展开计数（包装 store.get_outgoing_edges）
# ==========================================================================

class _count_expansions:
    def __init__(self, engine):
        self.store = engine.store
        self.n = 0

    def __enter__(self):
        self._orig = self.store.get_outgoing_edges
        box = self

        def counting(*a, **kw):
            box.n += 1
            return box._orig(*a, **kw)

        self.store.get_outgoing_edges = counting
        return self

    def __exit__(self, *exc):
        self.store.get_outgoing_edges = self._orig
        return False


# ==========================================================================
# 夹具
# ==========================================================================

def _make_backup(edges_n, nodes_n=200):
    """issue #482 同款构造：nodes_n 个节点、edges_n 条边（端点全在库内）。"""
    d = tempfile.mkdtemp()
    src = SpacetimeMemoryEngine(":memory:")
    nodes = [src.add_perception(f"事实 {i} 号观测记录", skip_dedup=True).id
             for i in range(nodes_n)]
    for i in range(edges_n):
        src.add_edge(nodes[i % nodes_n], nodes[(i * 7 + 1) % nodes_n],
                     EdgeType.CAUSAL, confidence=0.6)
    bk = os.path.join(d, "bk.json")
    src.export_all(bk)
    src.close()
    return bk, d


def _orphan_backup(edges_n=400, nodes_n=200, drop=3):
    """含**孤儿边 + 边界行**的备份（返回 bk 路径、tmpdir、以及旧判据的期望读数）。

    边界行覆盖：①端点被删的孤儿边；②缺 `target_id` 键；③`source_id=None`；
    ④与某条 keep 行**逐字相同**的重复行；⑤与某条 orphan 行**逐字相同**的重复行；
    ⑥同端点但形状不同（多一个列）的行。
    """
    bk, d = _make_backup(edges_n, nodes_n)
    with open(bk, "r", encoding="utf-8") as f:
        data = _json.load(f)
    victims = {r["id"] for r in data["nodes"][:drop]}
    data["nodes"] = [r for r in data["nodes"] if r["id"] not in victims]
    live = [r["id"] for r in data["nodes"]]
    rows = data["edges"]
    # 孤儿：端点指向被删节点（用一条真实存在的边改写端点，保证形状真实）
    row_orphan = dict(rows[0])
    row_orphan["id"] = "edge_482_orphan_src"
    row_orphan["source_id"] = sorted(victims)[0]
    rows.append(row_orphan)
    row_orphan2 = dict(rows[1])
    row_orphan2["id"] = "edge_482_orphan_tgt"
    row_orphan2["target_id"] = sorted(victims)[1]
    rows.append(row_orphan2)
    # 边界行：缺键 / None
    rows.append({"id": "edge_482_no_target", "source_id": live[0]})
    rows.append({"id": "edge_482_none_src", "source_id": None, "target_id": live[1]})
    # 边界行：与 keep 行逐字相同的重复行（旧判据 list `in` 按 `==` 命中 keep）
    rows.append(dict(rows[2]))
    # 边界行：与 orphan 行逐字相同的重复行
    rows.append(dict(row_orphan2))
    # 边界行：同端点、多一列
    dup = dict(rows[3])
    dup["id"] = "edge_482_dup_shape"
    dup["confidence"] = 0.11
    rows.append(dup)
    bk2 = os.path.join(d, "bk_orphan.json")
    with open(bk2, "w", encoding="utf-8") as f:
        _json.dump(data, f, ensure_ascii=False)

    # 旧判据本地复算（缺陷态实现逐字复刻）——独立判据
    node_ids = {r["id"] for r in data["nodes"]}
    keep_old = [r for r in rows
                if r.get("source_id") in node_ids and r.get("target_id") in node_ids]
    orphan_old = [r for r in rows if r not in keep_old]
    return bk2, d, rows, keep_old, orphan_old


def _chain(k, steps=STEPS):
    e = SpacetimeMemoryEngine(":memory:")
    ns = [e.add_perception(s, skip_dedup=True) for s in steps]
    other = e.add_perception("无关事件", skip_dedup=True)
    for a, b in zip(ns, ns[1:]):
        for _ in range(k):
            e.add_edge(a.id, b.id, EdgeType.CAUSAL, confidence=0.8)
    return e, ns, other


def _expansions(e, fn):
    """在展开计数下跑 fn(e)，返回 (结果, 展开次数)。"""
    with _count_expansions(e) as g:
        out = fn(e)
    return out, g.n


# ==========================================================================
# A 组 —— 缺陷一：import_all 孤儿边预过滤不是 O(E²)
# ==========================================================================

def test_a0_gauge_reads_quadratic_reference():
    """A0 量具正对照：同一量具在**旧判据**上必须读到 ≥ E²/4 次比较。"""
    n = 900
    with _count_compares() as g:
        rows = [_CDict({"id": "e%d" % i, "source_id": "n%d" % (i % 50),
                        "target_id": "n%d" % ((i * 7 + 1) % 50)})
                for i in range(n)]
        node_ids = {"n%d" % i for i in range(50)}
        keep = [r for r in rows
                if r.get("source_id") in node_ids and r.get("target_id") in node_ids]
        orphan = [r for r in rows if r not in keep]
    assert g.n >= n * n // 4, (
        "A0 量具失灵：旧判据在 n=%d 上只读到 %d 次 dict 比较（应 ≥ n²/4=%d）"
        % (n, g.n, n * n // 4))
    assert orphan == [], "A0 前置：本样本全是合法边（无孤儿）"


def test_a1_import_all_edge_filter_is_linear():
    """A1 真实路径：`import_all` 内的 dict 比较次数随 E 线性（缺陷态 ⇒ 判红）。"""
    reads = {}
    for n in (1200, 3600):
        bk, d = _make_backup(n)
        dst = SpacetimeMemoryEngine(os.path.join(d, "dst.db"))
        with _count_compares() as g:
            r = dst.import_all(bk)
        dst.close()
        reads[n] = g.n
        assert r["orphan_edges"] == 0 and r["integrity_ok"] is True, (
            "A1 前置：全合法边备份不得报孤儿（%r）" % (r["orphan_edges"],))
    assert reads[1200] <= 8 * 1200, (
        "A1 缺陷一复发：E=1200 的孤儿边预筛仍做 %d 次 dict 比较（线性上界 %d；"
        "缺陷态读数 719400）" % (reads[1200], 8 * 1200))
    assert reads[3600] <= 8 * 3600, (
        "A1 缺陷一复发：E=3600 的孤儿边预筛仍做 %d 次 dict 比较（线性上界 %d；"
        "缺陷态读数 6478200）" % (reads[3600], 8 * 3600))
    assert reads[3600] <= 12 * reads[1200] + 2000, (
        "A1 增幅超线性：3× 规模下比较次数 %d → %d（旧判据为 9×）"
        % (reads[1200], reads[3600]))


def test_a2_semantics_unchanged_vs_old_judgement():
    """A2 语义等价：真实备份（含孤儿/缺键/None/重复行）读数与旧判据逐字一致。"""
    bk, d, rows, keep_old, orphan_old = _orphan_backup()
    assert len(orphan_old) >= 4, (
        "A2 前置：夹具必须含孤儿与边界行——orphan=%d" % len(orphan_old))
    dst = SpacetimeMemoryEngine(os.path.join(d, "dst2.db"))
    r = dst.import_all(bk)
    got_edges = dst.store.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    dst.close()
    assert r["orphan_edges"] == len(orphan_old), (
        "A2 语义漂移：orphan_edges=%d，旧判据=%d"
        % (r["orphan_edges"], len(orphan_old)))
    assert [s["id"] for s in r["orphan_edges_sample"]] == \
        [x["id"] for x in orphan_old[:20]], (
        "A2 语义漂移：orphan_edges_sample 与旧判据不一致\n got=%r\nwant=%r"
        % ([s["id"] for s in r["orphan_edges_sample"]],
           [x["id"] for x in orphan_old[:20]]))
    assert r["imported"]["edges"] == len(keep_old), (
        "A2 语义漂移：imported.edges=%d，旧判据 keep=%d"
        % (r["imported"]["edges"], len(keep_old)))
    # 落库行数：同 id 的重复行按 INSERT OR REPLACE 折叠 ⇒ 与「keep 的去重 id 数」比
    assert got_edges == len({x["id"] for x in keep_old}), (
        "A2 语义漂移：落库边数=%d，旧判据 keep 去重 id 数=%d"
        % (got_edges, len({x["id"] for x in keep_old})))
    assert r["integrity_ok"] is False and r["dangling_rows"] == 0, (
        "A2 前置：有孤儿 ⇒ integrity_ok=False 且预筛后无悬挂行（%r/%r）"
        % (r["integrity_ok"], r["dangling_rows"]))


def test_a3_orphan_edges_skipped_and_reported():
    """A3 功能非回归：孤儿边不落库、计数上报、样本给出端点。"""
    bk, d, rows, keep_old, orphan_old = _orphan_backup(edges_n=200)
    dst = SpacetimeMemoryEngine(os.path.join(d, "dst3.db"))
    r = dst.import_all(bk)
    got_ids = {x[0] for x in
               dst.store.conn.execute("SELECT id FROM edges").fetchall()}
    dst.close()
    orphan_ids = {x["id"] for x in orphan_old}
    assert not (got_ids & orphan_ids), (
        "A3 孤儿边仍落库：%r" % sorted(got_ids & orphan_ids)[:5])
    assert r["orphan_edges"] == len(orphan_old) >= 1
    sample = r["orphan_edges_sample"]
    assert sample and all("source_id" in s and "target_id" in s for s in sample), (
        "A3 样本形状变了：%r" % (sample[:2],))


# ==========================================================================
# B 组 —— 缺陷二：不可达分支同样受 max_paths 约束
# ==========================================================================

def test_b1_unreachable_enumeration_bounded():
    """B1 不可达查询的展开次数受 max_paths 约束，且不随平行边数增长。"""
    reads = {}
    for k in (4, 8, 12):
        e, ns, other = _chain(k)
        out, n = _expansions(
            e, lambda en: en.reason_causal(ns[0].id, end_id=other.id))
        e.close()
        assert out == [], "B1 前置：终点不可达必须返回空列表（got=%d）" % len(out)
        reads[k] = n
        bound = _CAUSAL_MAX_PATHS * (5 + 2)
        assert n <= bound, (
            "B1 缺陷二复发：k=%d 不可达查询展开 %d 次（上界 max_paths×(max_depth+2)=%d；"
            "缺陷态读数 k=12 为 271453）" % (k, n, bound))
    assert reads[12] <= 4 * reads[4], (
        "B1 展开次数仍随平行边数增长：k=4 → %d，k=12 → %d（缺陷态 1365 → 271453）"
        % (reads[4], reads[12]))


def test_b2_budget_is_observable():
    """B2 预算是否耗尽必须可观测（两键缺一即判红）。"""
    e, ns, other = _chain(12)
    st = {}
    e.reason_causal(ns[0].id, end_id=other.id, max_depth=5, causal_stats=st)
    e.close()
    for key in ("budget_exhausted", "enumerated"):
        assert key in st, (
            "B2 不可达分支的预算状态不可观测：causal_stats 缺 %r（got=%r）"
            % (key, sorted(st)))
    assert st["budget_exhausted"] is True and st["enumerated"] >= _CAUSAL_MAX_PATHS, (
        "B2 k=12 不可达查询应把预算打满：%r" % st)

    e2, ns2, other2 = _chain(1)
    st2 = {}
    e2.reason_causal(ns2[0].id, end_id=other2.id, max_depth=5, causal_stats=st2)
    e2.close()
    assert st2["budget_exhausted"] is False and st2["enumerated"] < _CAUSAL_MAX_PATHS, (
        "B2 小图不得误报预算耗尽：%r" % st2)


def test_b3_explicit_max_paths_bounds_unreachable_work():
    """B3 显式收紧 max_paths 在不可达分支上同样生效。"""
    e, ns, other = _chain(12)
    st = {}
    out, n = _expansions(
        e, lambda en: en.reason_causal(ns[0].id, end_id=other.id, max_depth=5,
                                       max_paths=10, causal_stats=st))
    e.close()
    assert out == []
    assert st["enumerated"] == 10 and st["budget_exhausted"] is True, (
        "B3 max_paths=10 未在不可达分支上生效：%r" % st)
    assert n <= 10 * (5 + 2), (
        "B3 展开次数 %d 超出 max_paths×(max_depth+2)=%d" % (n, 70))


def test_b4_gauge_reads_unbounded_control():
    """B4 量具正对照：把 max_paths 放到 10⁹（＝无预算）时展开必须回到 3 万量级。"""
    e, ns, other = _chain(8)
    st = {}
    out, n = _expansions(
        e, lambda en: en.reason_causal(ns[0].id, end_id=other.id, max_depth=5,
                                       max_paths=10 ** 9, causal_stats=st))
    e.close()
    assert out == []
    assert n >= 30000, (
        "B4 量具失灵：无预算时 k=8 只展开 %d 次（缺陷态读数 37449，应 ≥ 30000）" % n)
    # 正对照的判据只落在**量具**上（`enumerated` 是本件新增的外传键，缺陷态没有它；
    # 有则一并核，无则不影响「量具能读到大数」这一证明）。
    if "enumerated" in st:
        assert st["enumerated"] >= 30000, (
            "B4 量具失灵：无预算时 enumerated=%r" % st.get("enumerated"))


def test_b5_reachable_readings_unchanged():
    """B5 可达分支读数不变（#327 Q1/Q3/Q4 口径）。"""
    e1, ns1, _o1 = _chain(1)
    assert len(e1.reason_causal(ns1[0].id, end_id=ns1[-1].id)) == 1
    e1.close()
    e2, ns2, _o2 = _chain(2)
    assert len(e2.reason_causal(ns2[0].id, end_id=ns2[-1].id)) == 32
    e2.close()
    e4, ns4, _o4 = _chain(4)
    got = e4.reason_causal(ns4[0].id, end_id=ns4[-1].id)
    assert len(got) == _CAUSAL_MAX_PATHS, (
        "B5 可达分支被误截：k=4 应恰 %d 条，实得 %d" % (_CAUSAL_MAX_PATHS, len(got)))
    e4.close()
    e10, ns10, _o10 = _chain(4)
    assert len(e10.reason_causal(ns10[0].id, end_id=ns10[-1].id, max_paths=10)) == 10
    e10.close()


def test_b6_dead_end_and_truncation_unchanged():
    """B6 #328 口径不误伤：死胡同不记截断；到预算仍有出边时截断证据照常上报。"""
    e = SpacetimeMemoryEngine(":memory:")
    c = [e.add_perception("节点%d 独立观察记录" % i, skip_dedup=True).id
         for i in range(4)]
    for a, b in zip(c, c[1:]):
        e.add_edge(a, b, EdgeType.CAUSAL)
    z = e.add_perception("不可达终点", skip_dedup=True).id
    st_deep = {}
    deep = e.reason_causal(c[0], end_id=z, max_depth=3, causal_stats=st_deep)
    assert deep == [] and st_deep["truncated"] == 0, (
        "B6 死胡同被误报成截断：%r / %r" % (deep, st_deep))
    st_mid = {}
    e.reason_causal(c[0], end_id=z, max_depth=1, causal_stats=st_mid)
    assert st_mid["truncated"] >= 1, (
        "B6 到深度预算仍有出边时必须留截断证据：%r" % st_mid)
    e.close()


def test_b7_chains_branch_unaffected():
    """B7 不传 end_id 的分支读数不变（上限仍挂在 chains 条数上）。"""
    e, ns, _o = _chain(4)
    assert len(e.reason_causal(ns[0].id, max_paths=10)) == 10
    assert len(e.reason_causal(ns[0].id)) == _CAUSAL_MAX_PATHS
    e.close()


def test_b8_budget_consumed_by_dead_ends_is_observable():
    """B8 本件**语义边界**钉死：预算可被死胡同分支先耗尽 ⇒ 完整路径可能少于 max_paths，
    但「被耗尽」必须可观测（旧实现返回 1 条且无任何标记）。

    构造：start 的**先序出边**全是死胡同（DFS 先弹），真路径在其后；max_paths=3 ⇒
    3 个死胡同即打满预算，真路径不再展开。旧实现（只数命中 end 的结果）会返回 1 条。
    """
    def build():
        e = SpacetimeMemoryEngine(":memory:")
        s = e.add_perception("起点", skip_dedup=True).id
        ends = [e.add_perception("死胡同%d" % i, skip_dedup=True).id
                for i in range(8)]
        m = e.add_perception("中间", skip_dedup=True).id
        t = e.add_perception("终点", skip_dedup=True).id
        for d in ends:
            e.add_edge(s, d, EdgeType.CAUSAL)     # 先建死胡同边（DFS 先弹）
        e.add_edge(s, m, EdgeType.CAUSAL)
        e.add_edge(m, t, EdgeType.CAUSAL)
        return e, s, t

    e, s, t = build()
    st = {}
    got = e.reason_causal(s, end_id=t, max_depth=5, max_paths=3, causal_stats=st)
    assert got == [] and st.get("budget_exhausted") is True, (
        "B8 边界未按文档生效：max_paths=3 应被死胡同分支打满并显式标记 —— "
        "got=%r stats=%r" % (len(got), st))
    assert st.get("enumerated") == 3, "B8 enumerated 应与 max_paths 齐平：%r" % st
    assert st.get("truncated") == 0, (
        "B8 死胡同不得记截断（#328 F 组口径）：%r" % st)
    e.close()

    # 预算放开 ⇒ 同图必须给出那条完整路径（证明上一条不是「图坏了」）
    e2, s2, t2 = build()
    st2 = {}
    got2 = e2.reason_causal(s2, end_id=t2, max_depth=5, max_paths=20,
                            causal_stats=st2)
    assert len(got2) == 1 and st2["budget_exhausted"] is False, (
        "B8 正对照：max_paths=20 时必须找回 1 条完整路径 —— got=%d stats=%r"
        % (len(got2), st2))
    e2.close()


# ==========================================================================
# C 组 —— 硬约束：签名形态不得变化
# ==========================================================================

def test_c_signatures_unchanged():
    """C 三处公开方法的参数表逐字不变（本批不得改签名）。"""
    assert list(inspect.signature(SpacetimeMemoryEngine.import_all).parameters) == \
        ["self", "input_path", "designer_key"]
    assert list(inspect.signature(SpacetimeMemoryEngine.reason_causal).parameters) == \
        ["self", "start_id", "end_id", "max_depth", "relation_types",
         "importance_weighted", "include_subgraph", "max_paths", "causal_stats"]
    assert list(inspect.signature(LayeredStore.infer_causal_paths).parameters) == \
        ["self", "start_id", "end_id", "max_depth", "relation_types",
         "max_paths", "causal_stats"]


# ==========================================================================
# 脚本式入口（pytest 亦可收集）
# ==========================================================================

def _all_tests():
    return [(n, f) for n, f in sorted(globals().items())
            if n.startswith("test_") and callable(f)]


def main():
    passed, failed = [], []
    for name, fn in _all_tests():
        try:
            fn()
        except AssertionError as exc:
            failed.append((name, str(exc)))
            print("  FAIL %s ← %s" % (name, exc))
        except Exception as exc:  # noqa: BLE001
            failed.append((name, repr(exc)))
            print("  ERROR %s ← %r" % (name, exc))
        else:
            passed.append(name)
            print("  PASS %s" % name)
    print()
    print("===== SUMMARY %d/%d 通过 =====" % (len(passed), len(passed) + len(failed)))
    if failed:
        print("FAILS:", [n for n, _ in failed])
        return 1
    print("VERDICT=PASS（import_all 孤儿边预筛为 O(E) 且语义逐字等价；"
          "reason_causal 不可达分支同样受 max_paths 约束且可观测；"
          "可达读数与 #328 口径不变；签名未变）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

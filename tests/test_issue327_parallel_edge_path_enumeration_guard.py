# -*- coding: utf-8 -*-
"""test_issue327_parallel_edge_path_enumeration_guard · lingshu issue #327 守卫
============================================================================
缺陷（lingshu issue #327 · 基线 e18562f）：
  `SpacetimeMemoryEngine.add_edge` 每次调用都新建一条边，不检查同一
  (source, target, relation_type) 是否已存在 ⇒ 同一因果关系被记录 k 次留下 k 条
  平行边。`reason_causal`（两个分支）与 `LayeredStore.infer_causal_paths` 对平行边
  逐条分支、把每条路径完整拷贝进结果，**无条数上限**：6 节点无环链的路径数是 k⁵
  （k=8：32768 条/0.42s/96MB；k=12：248832 条/3.47s/669MB；k=16：1048576 条/
  14.7s/1.3GB），图只有 6 节点且**无环**——#145/#215/#252 的环侧修复全不触发。

修法（最小 · 按 issue 建议 2）：
  `reason_causal` / `infer_causal_paths` 新增 `max_paths`（默认
  `_CAUSAL_MAX_PATHS = 1000`），达到上限即停止枚举。issue 建议 1（`add_edge` 合并
  平行边 + 存量迁移）**不在本件**——那会改写入语义与既有边 id，波及衰减/验证/同步面。

判据来源：公开 issue #327「修复建议 2」（「`reason_causal` / `infer_causal_paths`
按 (source,target) 折叠平行边后再遍历，并加 `max_paths`（如 1000）与时间预算」）＋
其复现实读（k=8/12/16 的路径数、耗时、RSS）＋ 分诊表 `triage_lingshu_ALL.md` 行 327
（「6 节点无环链路径数 k⁵；k=16 即 105 万条/1.3 GB」）。仓内无规定该预算的理论章节，
属经验标定（本件 #327）。
边界（削掉的判别力，如实标注）：① 达上限时返回**前 max_paths 条**，未加
`truncated` 标记（会改返回类型/元素语义）；② 本件只收「枚举无上限」这一半，
平行边**合并**（建议 1）不做——`add_edge` 仍不合并。

断言组（把修复抽掉（去掉 max_paths 早退）⇒ Q1/Q2/Q3 红）：
  Q1 枚举有界：k=12 的 6 节点链 `reason_causal(start)` 与
     `reason_causal(start, end)` 都必须 ≤ `_CAUSAL_MAX_PATHS`（旧形态：248832 条）。
  Q2 耗时/内存有界：k=12 两条调用均 < 1s（旧形态 3.47s / 2.63s）。
  Q3 上限可显式收紧：`max_paths=10` 时两条调用都恰返回 10 条（参数生效）。
  Q4 非回归：小图读数不变——k=1 ⇒ 1 条；k=2 ⇒ 32 条（< 上限，不受影响）；
     A 组 #145 语义（截断链/带环链）保持。

运行（仓根）：python -X utf8 -m pytest tests/test_issue327_parallel_edge_path_enumeration_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys
import time
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")

from lingshu.core.core import (  # noqa: E402
    _CAUSAL_MAX_PATHS,
    EdgeType,
    SpacetimeMemoryEngine,
)

STEPS = ["乌云密布", "开始下雨", "路面湿滑", "车辆减速", "道路拥堵", "上班迟到"]


def _chain(k: int):
    """6 节点无环链，每跳 k 条平行边（issue 同款构造）。"""
    e = SpacetimeMemoryEngine(":memory:")
    ns = [e.add_perception(s, skip_dedup=True) for s in STEPS]
    for a, b in zip(ns, ns[1:]):
        for _ in range(k):
            e.add_edge(a.id, b.id, EdgeType.CAUSAL, confidence=0.8)
    return e, ns


def test_q1_enumeration_bounded():
    """Q1：k=12 时两个分支都不得枚举出 24.9 万条路径。"""
    e, ns = _chain(12)
    chains = e.reason_causal(ns[0].id)
    paths = e.reason_causal(ns[0].id, end_id=ns[-1].id)
    assert len(chains) <= _CAUSAL_MAX_PATHS, (
        "#327：reason_causal(start) 仍无上限——%d 条（旧形态 248832 条）" % len(chains))
    assert len(paths) <= _CAUSAL_MAX_PATHS, (
        "#327：reason_causal(start,end) 仍无上限——%d 条（旧形态 248832 条）" % len(paths))
    assert len(chains) >= 1 and len(paths) >= 1, "前置：链上必有路径"


def test_q2_time_bounded():
    """Q2：k=12 两条调用均 < 1s（旧形态 3.47s / 2.63s）。"""
    e, ns = _chain(12)
    t = time.time()
    e.reason_causal(ns[0].id)
    t1 = time.time() - t
    t = time.time()
    e.reason_causal(ns[0].id, end_id=ns[-1].id)
    t2 = time.time() - t
    assert t1 < 1.0 and t2 < 1.0, (
        "#327：平行边路径枚举仍指数耗时——%.2fs / %.2fs（旧形态 3.47s / 2.63s）"
        % (t1, t2))


def test_q3_limit_is_parameterizable():
    """Q3：max_paths 参数生效（两条分支都恰返回 10 条）。"""
    e, ns = _chain(4)
    chains = e.reason_causal(ns[0].id, max_paths=10)
    paths = e.reason_causal(ns[0].id, end_id=ns[-1].id, max_paths=10)
    assert len(chains) == 10, (
        "#327：max_paths=10 未生效（start 分支）——%d 条" % len(chains))
    assert len(paths) == 10, (
        "#327：max_paths=10 未生效（start,end 分支）——%d 条" % len(paths))


def test_q4_small_graphs_unchanged():
    """Q4：非回归——k=1/2 的小图读数不变（远低于上限）。"""
    e1, ns1 = _chain(1)
    assert len(e1.reason_causal(ns1[0].id)) == 1
    e2, ns2 = _chain(2)
    assert len(e2.reason_causal(ns2[0].id)) == 32, (
        "非回归：k=2 应恰 32 条——%d" % len(e2.reason_causal(ns2[0].id)))
    assert len(e2.reason_causal(ns2[0].id, end_id=ns2[-1].id)) == 32

# -*- coding: utf-8 -*-
"""query_match 顺序无关性回归。

_match_score 原为贪心 first-fit（取第一个匹配的未用检测即 break），当同类型 conn
的匹配集有包含关系（通配 zone="*" ⊃ 具体 zone="top"）时，通配 conn 会抢占具体 conn
需要的检测，命中数依赖 detections 顺序 → verdict 在 ACCEPT/DEFER 间翻转。修复后
用增广路求最大匹配，命中数与 detections 顺序无关。
"""

import itertools
import json


def _lib():
    from lingshu.nn.hex_query import ConnectionLibrary
    lib = ConnectionLibrary()
    # 通配 conn ⊃ 具体 conn，制造贪心抢占
    lib.add_record("gizmo", [
        {"type": "circle", "zone": "*", "color": "*"},
        {"type": "circle", "zone": "top", "color": "*"},
    ])
    lib.add_record("dummy", [{"type": "stripe", "zone": "*", "color": "*"}])
    return lib


def test_match_score_order_independent():
    """同 detections 集合打乱顺序 → _match_score 必须相同（修复前 0.5 vs 1.0）。"""
    from lingshu.nn.hex_query import _match_score

    rec = _lib().records[0]
    dets = [
        {"obj": "circle|red", "pos": "r0"},   # r0 ∈ top
        {"obj": "circle|red", "pos": "r7"},   # r7 ∈ bottom(非 top)
    ]
    scores = {_match_score(rec, list(p)) for p in itertools.permutations(dets)}
    assert len(scores) == 1, f"_match_score 应与 detections 顺序无关，实际得到 {scores}"


def test_query_match_verdict_stable_across_orders():
    """query_match 的 verdict 应对 detections 顺序稳定（修复前 DEFER/ACCEPT 翻转）。"""
    from lingshu.nn.hex_query import query_match

    lib = _lib()
    dets = [
        {"obj": "circle|red", "pos": "r0"},
        {"obj": "circle|red", "pos": "r7"},
    ]
    verdicts = {query_match(list(p), lib)[0]["verdict"]
                for p in itertools.permutations(dets)}
    assert len(verdicts) == 1, f"verdict 应稳定，实际 {verdicts}"


def test_relation_recipes_are_order_invariant_including_binding():
    """带 tags.relations 的配方：检测换序后 score/verdict/角色绑定/关系证据必须一致。

    F06 返工探针（原缺陷）：两个同类型通配 role 的配方下，d 与 reversed(d) 分别得到
    0.667 与 1.0——命中数虽然顺序无关，绑定（角色↔检测）却被增广路的抢占顺序决定。
    现在绑定取「规范检测序上字典序最小的最大匹配」：score/verdict/角色位置/逐条证据
    在任意排列下逐位一致（role_detections 是原下标，随输入变化但不错指）。
    """
    from lingshu.nn.hex_query import ConnectionLibrary, query_match

    lib = ConnectionLibrary()
    lib.add_record("pair", [
        {"type": "circle", "zone": "*", "color": "*", "role": "a"},
        {"type": "circle", "zone": "*", "color": "*", "role": "b"}],
        tags={"relations": [{"from": "a", "to": "b", "rel": "above"}]})
    dets = [{"obj": "circle|red", "pos": "r0"}, {"obj": "circle|blue", "pos": "r8"}]
    seen = set()
    for p in itertools.permutations(range(len(dets))):
        order = [dets[i] for i in p]
        entry = query_match(order, lib)[0]
        for role, positions in entry["roles"].items():
            assert [order[i]["pos"] for i in entry["role_detections"][role]] == positions
        seen.add((entry["score"], entry["verdict"],
                  json.dumps(entry["roles"], sort_keys=True),
                  json.dumps(entry["relations"], sort_keys=True)))
    assert len(seen) == 1, seen
    entry = query_match(list(dets), lib)[0]
    assert entry["score"] == 1.0 and entry["verdict"] == "ACCEPT", entry
    assert entry["roles"] == {"a": ["r0"], "b": ["r8"]}, entry["roles"]


def test_max_matching_optimal_hit_count():
    """贪心可能漏命中；最大匹配应取到最优（此处 2 conn 都应被满足 → score=1.0）。"""
    from lingshu.nn.hex_query import _match_score

    rec = _lib().records[0]
    # 通配 conn 可匹配任一 circle，具体 top conn 匹配 r0；两者都能被满足
    dets = [
        {"obj": "circle|red", "pos": "r0"},   # top
        {"obj": "circle|red", "pos": "r7"},   # bottom
    ]
    # 最大匹配：通配→r7，具体top→r0，两 conn 都命中
    assert _match_score(rec, dets) == 1.0, "最大匹配应把两个 conn 都匹配到（score=1.0）"
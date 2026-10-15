# -*- coding: utf-8 -*-
"""test_issue616_session_chains_range_guard · session_chains 范围/类型防护守卫（#616）
======================================================================================
缺陷（#616，成立）：``tools/coggraph/session_chains.py`` 的 #531 防护**只挡类型
不挡范围**，``session`` 字段**连类型防护都没有**——两个字段两种崩法，都让
**整轮构建全废**（rc=1，无产物）：

  · ``ts_to_float`` 对数字**原样返回**：``t=1e18`` 混进成链会话 ⇒ 下游
    ``time.localtime(1e18)`` 抛 ``OSError``（本机 Windows 实测）；
    ``t=NaN`` ⇒ ``ValueError: Invalid value NaN``。
  · ``session=12345``（数字）凑满 ``min_chain`` 节点 ⇒ 枢纽构建
    ``"session:" + s`` 抛 ``TypeError``（#300 的修复建议本就要求加）。

改前读数（本机 Windows / py3.12，main @ 375cf55，min_chain=3）：
  t=1e18@成链会话 → rc=1 OSError；t=NaN@成链会话 → rc=1 ValueError；
  session=12345@3 节点 → rc=1 TypeError。

修法（与 #531 同口径：告警并跳过该记录，不整轮崩溃）：
  ① ``ts_to_float`` 数字分支加范围闸 ``0 <= v <= _TS_MAX``（localtime 可
     表示域 [0, 3000-12-31T23:59:59Z]；NaN/±inf 的比较恒 False，一并拒收）；
  ② 读取环对 ``session`` 加 ``isinstance(sid, str)`` 闸（缺失/空仍按 #300
     静默跳过，不告警）。

守卫判据（端到端跑真实 CLI，注入坏记录后整轮构建必须 rc=0 且产物在）：
  G1  t 越界/NaN 告警跳过，坏记录所在会话**其余健康节点照常成链**；
  G2  session 非字符串告警跳过，其余会话不受牵连；
  G3（防过修）纯健康输入的成链行为零回归（链数/顺序边数逐位核对）。

判据来源：issue #616 取证（复现命令族与本件同构）＋ #531/#300 同族先例；
理论章节不涉及 _audit.jsonl 契约。
运行：pytest tests/test_issue616_session_chains_range_guard.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "tools", "coggraph", "session_chains.py")

HEALTHY = [
    {"op": "add", "id": "n1", "session": "s1", "t": 1760000000.0},
    {"op": "add", "id": "n2", "session": "s1", "t": 1760000600.0},
    {"op": "add", "id": "n3", "session": "s1", "t": 1760001200.0},
]


def _run(records, node_ids=("n1", "n2", "n3", "n4")):
    """跑真实 CLI：audit+graph 临时件 → 子进程构建 → (rc, stderr, 产物)。"""
    tmp = tempfile.mkdtemp(prefix="issue616_")
    audit = os.path.join(tmp, "_audit.jsonl")
    graph = os.path.join(tmp, "graph.json")
    out = os.path.join(tmp, "out")
    with open(audit, "w", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(graph, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"nodes": [{"id": i} for i in node_ids], "edges": []}, f, ensure_ascii=False)
    p = subprocess.run(
        [sys.executable, "-X", "utf8", SCRIPT, "--audit", audit, "--graph", graph,
         "--out", out, "--min-chain", "3", "--write"],
        capture_output=True, text=True, encoding="utf-8", cwd=REPO, timeout=120)
    product = None
    pj = os.path.join(out, "session_chains.json")
    if os.path.exists(pj):
        with open(pj, encoding="utf-8") as f:
            product = json.load(f)
    return p.returncode, p.stderr, product


def test_out_of_range_t_skipped_not_crash():
    """G1a：t=1e18 混进成链会话 ⇒ 告警跳过该记录，整轮 rc=0、会话照常成链。"""
    rc, err, out = _run(HEALTHY + [{"op": "add", "id": "n4", "session": "s1", "t": 1e18}])
    assert rc == 0, ("t 越界让整轮构建崩溃（#616）：rc=%d stderr=%s" % (rc, err[-300:]))
    assert out is not None and out["meta"]["sessions"] == 1, out and out["meta"]
    assert any(c["session"] == "s1" and c["nodes"] == 3 for c in out["chains"]), out["chains"]
    assert "越界" in err and "n4" in err, "坏记录须 stderr 告警（#531 同口径）：\n%s" % err


def test_nan_t_skipped_not_crash():
    """G1b：t=NaN ⇒ 同样告警跳过，不崩。"""
    rc, err, out = _run(HEALTHY + [{"op": "add", "id": "n4", "session": "s1", "t": float("nan")}])
    assert rc == 0, ("t=NaN 让整轮构建崩溃（#616）：rc=%d stderr=%s" % (rc, err[-300:]))
    assert out is not None and out["meta"]["sessions"] == 1


def test_non_str_session_skipped_not_crash():
    """G2：session=12345（数字）⇒ 告警跳过，其余健康会话不受牵连。"""
    rc, err, out = _run(HEALTHY + [
        {"op": "add", "id": "n5", "session": 12345, "t": 1760001800.0},
        {"op": "add", "id": "n6", "session": 12345, "t": 1760002400.0},
        {"op": "add", "id": "n7", "session": 12345, "t": 1760003000.0},
    ], node_ids=("n1", "n2", "n3", "n4", "n5", "n6", "n7"))
    assert rc == 0, ("session 非字符串让整轮构建崩溃（#616）：rc=%d stderr=%s" % (rc, err[-300:]))
    assert out is not None and out["meta"]["sessions"] == 1, out and out["meta"]
    assert "session" in err and "类型非法" in err, err


def test_healthy_input_zero_regression():
    """G3（防过修）：纯健康输入的成链行为零回归（1 会话 / 2 顺序边 / 3 枢纽边）。"""
    rc, err, out = _run(HEALTHY, node_ids=("n1", "n2", "n3"))
    assert rc == 0, err
    c = out["meta"]["counts"]
    assert out["meta"]["sessions"] == 1 and c["sequential_edges"] == 2 \
        and c["hub_edges"] == 3, (out["meta"], c)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))

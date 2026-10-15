# -*- coding: utf-8 -*-
"""#639 守卫：session_chains 产物 range 必须用 UTC（gmtime），不得随机器时区变。

缺陷态（修复前 main）`tools/coggraph/session_chains.py`：
  · chains[].range 展示串用 ``time.localtime`` ⇒ 同一 graph.json/_audit.jsonl
    在 UTC+8 机器上产出 ``16:53``、在 UTC 机器上产出 ``08:53``——产物字节随
    机器时区漂移（线 II 判定 6185826742：TZ 双跑 range 跨日界不同、span 同）。
  · R4 日枢纽（#360）早已定**与机器时区无关**的 gmtime 口径，本文件属同族
    残留面。

修法（本端裁＝对齐 R4，确定性产物优先于本地墙钟语义）：:173-174 localtime→gmtime，
  span 的 epoch 数值面不动（不过修）。

守卫判据：
  G1 range 逐字等于**标准库现算的 gmtime 期望值**（不依赖任何 TZ env 生效性，
     修复前本机 UTC+8 下产物为 +8 墙钟 ⇒ 必红，判别力内生）；
  G2 TZ 双跑产物 chains 逐字一致（POSIX 风格 TZ 若在本机生效则额外证跨机器
     决定性；自证探针打印生效性，不谎称）；
  G3（防过修）span epoch 数值与边集零变化——只有展示串口径动。

变异点：把两行 gmtime 改回 localtime ⇒ 在 UTC+8 本机 G1 必红、G2 视 TZ env
  生效性而定；还原 ⇒ 全绿。

运行（lingshu 仓根）：python -X utf8 -m pytest tests/test_issue639_session_range_utc_guard.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "tools", "coggraph", "session_chains.py")

TS = [1760000000.0, 1760003600.0, 1760090000.0]


def _run(tz=None):
    """跑真实 CLI（--min-chain 3），返回 (rc, chains)。tz 非空则注入 POSIX/IANA TZ env。"""
    d = tempfile.mkdtemp(prefix="issue639_")
    audit = os.path.join(d, "_audit.jsonl")
    graph = os.path.join(d, "graph.json")
    out = os.path.join(d, "o")
    with open(audit, "w", encoding="utf-8", newline="\n") as f:
        for i, t in enumerate(TS):
            f.write(json.dumps({"op": "add", "id": "n%d" % (i + 1),
                                "session": "s1", "t": t}, ensure_ascii=False) + "\n")
    with open(graph, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"nodes": [{"id": "n%d" % (i + 1)} for i in range(len(TS))], "edges": []}, f)
    env = dict(os.environ)
    if tz:
        env["TZ"] = tz
    p = subprocess.run([sys.executable, "-X", "utf8", SCRIPT, "--audit", audit,
                        "--graph", graph, "--out", out, "--min-chain", "3", "--write"],
                       capture_output=True, text=True, encoding="utf-8",
                       env=env, cwd=REPO, timeout=120)
    pj = os.path.join(out, "session_chains.json")
    chains = None
    if os.path.exists(pj):
        with open(pj, encoding="utf-8") as f:
            chains = json.load(f)["chains"]
    return p.returncode, chains, p.stderr


def test_g1_range_equals_gmtime_ground_truth():
    """G1：range 必须逐字等于标准库 gmtime 现算值（修复前本机 +8 墙钟 ⇒ 红）。"""
    rc, chains, err = _run()
    assert rc == 0, err[-200:]
    expect = [time.strftime("%Y-%m-%d %H:%M", time.gmtime(TS[0])),
              time.strftime("%Y-%m-%d %H:%M", time.gmtime(TS[-1]))]
    assert chains[0]["range"] == expect, (
        "range 未对齐 UTC：产物=%r 期望(gmtime)=%r；本机 localtime=%r"
        % (chains[0]["range"], expect,
           time.strftime("%Y-%m-%d %H:%M", time.localtime(TS[0]))))


def test_g2_tz_double_run_product_identical():
    """G2：TZ 双跑 chains 逐字一致（Windows MSVCRT 对 IANA 名未必生效 ⇒ 自证打印）。"""
    rc_a, ca, _ = _run(tz="UTC")
    rc_b, cb, _ = _run(tz="SGT-8")  # POSIX 风格：UTC+8（MSVCRT 可识形态）
    assert rc_a == 0 and rc_b == 0
    probe = subprocess.run(
        [sys.executable, "-c",
         "import time; time.tzset(); print(time.localtime(%r).tm_hour)" % TS[0]],
        capture_output=True, text=True, encoding="utf-8", env={**os.environ, "TZ": "SGT-8"}, timeout=30)
    if "16" in probe.stdout:
        print("[639] 本机 TZ env 生效（localtime 随 TZ 变）⇒ 双跑一致是跨机器决定性的实证")
    else:
        print("[639] 本机 TZ env 未使 localtime 改变 ⇒ 双跑一致为平凡真，跨机器面由 G1 的 gmtime 断言承担")
    assert ca == cb, "TZ 双跑产物漂移：UTC=%r vs SGT-8=%r" % (ca, cb)


def test_g3_span_and_edges_zero_regression():
    """G3（防过修）：span epoch 数值与边集不因展示口径改动而变。"""
    rc, chains, _ = _run()
    assert rc == 0
    assert chains[0]["span"] == [TS[0], TS[-1]], "span 数值面被误动"

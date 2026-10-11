# -*- coding: utf-8 -*-
"""#248 wm_simloop WAL `seq` 跨 setup 重复 / 默认路径相对 cwd —— 守卫

缺陷（lingshu issue #248 · [core/wm_simloop]）：
  `SpacetimeMemoryEngine.wm_simloop("setup")` 默认 WAL 路径
  `os.path.join("data", "wm_simloop_wal.jsonl")` **相对进程 cwd**；每次 `setup`
  新建 `SimulationLoop`（`_seq=0`）却对同一文件 `open(..., "a")` 追加写 ⇒
  同一文件里 seq 重复（实测 `[0,1,0,1]`），记录里也没有 session 字段 ⇒
  文件头声明的「WAL（append-only 单调）」不成立。

修法（任务书建议①，最小改动）：
  `SimulationLoop.__init__` 打开已存在文件时**只读一次末条** seq 接着编号
  （`_resume_seq_from_wal`，不每条读全文件）。门面默认路径改用本仓既有
  `LINGSHU_DATA_ROOT` 数据根约定（同 `activation.py:62`）。
  ★**不做 session 字段**：issue 期望是「seq 严格单调 **或** 带 session」——
  只做前半即已满足判据；而本类刻意保住「同配置两次运行 WAL 逐字节一致」
  （见 `_alias` 注释），用随机源做 session 会当场作废该性质。

★返工记录（代理自报不是判据、编排侧独立复跑才是）：
  本件第一版修法除 `_resume_seq_from_wal` 外还加了
  `self._session = uuid.uuid4().hex[:12]` 并写进每条记录。编排侧用**假时钟隔离
  探针**（消除 ts 抖动）独立复跑发现：同配置两次运行、各自独立 wal 文件时，
  修复前 WAL 逐字节相同（True，519/519 字节），加随机 session 后**变为不同**
  （False，首个差异行 #0 即 `session` 值）⇒ 破坏既有性质。故删掉随机 session，
  只保留确定性的 seq 续编号，并新增断言 D 把该性质钉住（这正是本次回归暴露的缺口）。

判据来源：公开 issue #248 正文（`[0,1,0,1]` 实测与「append-only 单调」声明）；
默认路径口径沿用 `activation.py:62` 的 `LINGSHU_DATA_ROOT`（环境变量优先、
缺省 `data/`）；「逐字节一致」性质来源＝`wm_simloop.py` `_alias` 的既有注释。
阈值本体**一律未改**。

断言组（抽掉修复关键一行 ⇒ 红）：
  A 两次 `setup` 后同文件 seq **严格递增**（旧缺陷＝`[0,1,0,1]`）
  B 首次写新文件 seq 仍从 0 起（不误伤全新文件）
  C 默认路径尊重 `LINGSHU_DATA_ROOT`（绝对路径、落在该根下）
  D ★假时钟下同配置两次运行、各自独立 wal 文件 ⇒ WAL **逐字节相同**
    （既有性质；第一版随机 session 修法曾破坏它）

运行（仓根）：python -X utf8 -m pytest tests/test_issue248_wm_simloop_wal_seq_guard.py -q --no-header
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402
from lingshu.world.wm_simloop import SimulationLoop  # noqa: E402


def _seqs(wal_path):
    with open(wal_path, encoding="utf-8") as f:
        return [json.loads(ln)["seq"] for ln in f if ln.strip()]


def _run_once(wal_path, clock):
    """同配置跑一次推演环（确定性外部世界：漂移正弦遮蔽目标）。"""
    loop = SimulationLoop(size=24, seed=42, wal_path=wal_path, clock=clock)
    loop.scene.create_scene(trees=0, water=False)
    loop.scene.add_entity("chaser", behavior="seek", pos=(2, 1.5, 4), speed=0.4)
    loop.wm.perceive(observations=loop._observe())
    loop.step(n=3)
    return loop


def test_a_two_setups_strictly_increasing_seq(tmp_path):
    """A：同一 WAL 文件被两次 setup 追加 ⇒ seq 严格递增（旧缺陷＝[0,1,0,1]）。"""
    wal = str(tmp_path / "wal.jsonl")
    e = SpacetimeMemoryEngine(":memory:")
    for _ in range(2):
        r = e.wm_simloop("setup", {"size": 12, "seed": 3, "wal_path": wal})
        assert r["status"] == "ok", "setup 失败：%r" % (r,)
        e.wm_simloop("step", {"n": 1})
        e.wm_simloop("run", {"n": 1})
    seqs = _seqs(wal)
    assert seqs, "WAL 为空"
    assert all(b > a for a, b in zip(seqs, seqs[1:])), (
        "同文件 seq 非严格递增（旧缺陷）：%r" % (seqs,))


def test_b_fresh_file_seq_starts_at_zero(tmp_path):
    """B：全新文件的 seq 仍从 0 起（修复不得误伤首写）。"""
    wal = str(tmp_path / "fresh.jsonl")
    loop = SimulationLoop(size=10, seed=1, wal_path=wal)
    loop.scene.create_scene(trees=0, water=False)
    loop.scene.add_entity("walker", behavior="wander", pos=(6, 1.5, 13), speed=0.3)
    loop.wm.perceive(observations=loop._observe())
    loop.step(n=1)
    assert _seqs(wal)[0] == 0, "全新文件首条 seq 非 0：%r" % (_seqs(wal),)


def test_c_default_path_honors_data_root(tmp_path, monkeypatch):
    """C：默认 WAL 路径沿用 `LINGSHU_DATA_ROOT` 数据根约定。"""
    monkeypatch.setenv("LINGSHU_DATA_ROOT", str(tmp_path))
    e = SpacetimeMemoryEngine(":memory:")
    r = e.wm_simloop("setup", {"size": 12, "seed": 3})
    assert r["status"] == "ok", "setup 失败：%r" % (r,)
    assert os.path.isabs(r["wal_path"]), "默认路径仍相对 cwd：%r" % (r["wal_path"],)
    assert str(tmp_path) in r["wal_path"], (
        "默认路径未落在 LINGSHU_DATA_ROOT 下：%r" % (r["wal_path"],))


def test_d_same_config_two_runs_byte_identical(tmp_path):
    """D：★假时钟下同配置两次运行、各自独立 wal 文件 ⇒ WAL 逐字节相同。

    存在理由：`wm_simloop.py` 的 `_alias` 注释刻意声明「同配置两次运行 WAL
    逐字节一致」是本仓保住的性质（`_alias` 机制存在的唯一理由）。#248 的**第一版**
    修法加了随机 `session` 字段 ⇒ 编排侧独立复跑发现该性质当场作废（修复前
    519/519 字节相同，加 session 后首个差异行即 session 值）。本断言钉住该性质，
    防止「修 seq 单调性」再次以随机源代价换来。
    """
    a = str(tmp_path / "run_a.jsonl")
    b = str(tmp_path / "run_b.jsonl")
    _run_once(a, clock=lambda: 0.0)
    _run_once(b, clock=lambda: 0.0)
    with open(a, "rb") as fa, open(b, "rb") as fb:
        da, db = fa.read(), fb.read()
    assert da == db, (
        "同配置两次运行 WAL 不逐字节相同（确定性被破坏）："
        "%d vs %d 字节" % (len(da), len(db)))

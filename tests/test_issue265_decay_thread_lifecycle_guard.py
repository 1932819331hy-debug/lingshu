# -*- coding: utf-8 -*-
"""test_issue265_decay_thread_lifecycle_guard · #265 衰减线程生命周期守卫
============================================================================
缺陷（lingshu issue #265 · P1）：`start_auto_decay` 只看共享布尔 `_running`
（`if self._running: return`），不检查 `_decay_thread.is_alive()`；`stop_auto_decay`
只把它置 False。若旧线程此刻正处 `decay_cycle()` 体内（不在 `Event.wait` 上），
`stop` 的 `Event.set()` 带不走它，而 `start` 又把 `_running` 置回 True ⇒
旧线程循环继续、新线程另起一条，**两条线程并存**；反复 stop→start 线程数线性
叠加，遗忘速度按线程数成倍放大，且新 interval 只作用于新线程。

修法：`start_auto_decay` 先 `stop_auto_decay()`（含**有界 join**）再启动新线程；
`stop_auto_decay` 置位 + 唤醒后**有界 join**，返回时旧线程已退出。
判据来源：公开 issue #265 ＋ 分诊表 `triage_lingshu_ALL.md` 行 265。

守卫断言组（用「衰减周期被拉长」把线程稳定留在 `decay_cycle()` 体内复现竞态）：
  A 组（硬判据 · 线程不叠加）：线程正处 `decay_cycle()` 体内时连做 4 次
     stop→start，存活衰减线程数必须仍为 1。旧实现 ⇒ 1 → 5 ⇒ 必红。
  B 组（硬判据 · stop 返回后不再衰减）：`stop_auto_decay()` 返回后立即观测，
     存活衰减线程数必须为 0（旧实现只置位 ⇒ 线程仍在跑 ⇒ 必红）。
  C 组（硬判据 · 新 interval 立即生效）：stop → start(interval=3600) 后 2 秒内
     不得有任何 `decay_cycle` 调用（旧实现旧线程 0.2s 间隔仍在跑 ⇒ 必红）。
  D 组（对照 · 未关闭时衰减照常）：start(0.05) 后 0.3s 内 decay 调用 ≥ 2。

运行（仓根）：
    python -X utf8 -m pytest tests/test_issue265_decay_thread_lifecycle_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402


def _engine():
    return SpacetimeMemoryEngine(
        os.path.join(tempfile.mkdtemp(prefix="lingshu_issue265_"), "m.db"))


def _live_decay_threads():
    """进程内仍存活的 daemon 线程数（引擎只起衰减这一条 daemon 线程）。"""
    return sum(1 for t in threading.enumerate() if t.daemon and t.is_alive())


def test_stop_start_does_not_stack_threads():
    """A 组：线程正处 decay_cycle 体内时，stop→start 不得叠加线程。"""
    e = _engine()
    try:
        inside = threading.Event()
        real = e.decay_cycle

        def slow():
            inside.set()
            time.sleep(0.4)          # 稳定把线程留在 decay_cycle 体内
            return real()

        e.decay_cycle = slow
        e.start_auto_decay(interval=0.01)
        assert inside.wait(5.0), "前置：衰减线程应进入 decay_cycle 体内"
        inside.clear()

        for _ in range(4):
            e.stop_auto_decay()
            e.start_auto_decay(interval=0.01)
            time.sleep(0.05)         # 新线程起来，旧线程仍在 slow() 里

        n = _live_decay_threads()
        assert n == 1, (
            "#265：stop→start 4 次后应只剩 1 条衰减线程（旧实现 ⇒ 5 条，遗忘速度"
            "按线程数成倍放大）；实测 %d 条" % n)
    finally:
        e.stop_auto_decay()
        e.close()


def test_stop_leaves_no_running_thread():
    """B 组：stop_auto_decay() 返回后不得仍有衰减线程在跑。"""
    e = _engine()
    try:
        inside = threading.Event()
        real = e.decay_cycle

        def slow():
            inside.set()
            time.sleep(0.4)
            return real()

        e.decay_cycle = slow
        e.start_auto_decay(interval=0.01)
        assert inside.wait(5.0), "前置：衰减线程应进入 decay_cycle 体内"
        inside.clear()

        e.stop_auto_decay()
        n = _live_decay_threads()     # 返回后**立即**观测
        assert n == 0, (
            "#265：stop_auto_decay() 返回后不得仍有衰减线程（旧实现只置位 ⇒ 线程"
            "仍在跑）；实测 %d 条" % n)
    finally:
        e.stop_auto_decay()
        e.close()


def test_new_interval_takes_effect_on_restart():
    """C 组：stop → start(3600) 后旧 interval 的线程不得再衰减。"""
    e = _engine()
    try:
        calls = []
        real = e.store.decay_cycle

        def traced(*a, **k):
            calls.append(time.time())
            return real(*a, **k)

        e.store.decay_cycle = traced
        e.start_auto_decay(interval=0.2)
        time.sleep(0.25)             # 跑过一轮，线程回到等待
        e.stop_auto_decay()
        e.start_auto_decay(interval=3600)   # 想把遗忘放慢到每小时一次
        n0 = len(calls)
        time.sleep(2.0)
        assert len(calls) - n0 == 0, (
            "#265：改用 interval=3600 重启后 2s 内不得再有衰减（旧实现旧线程 0.2s "
            "间隔仍在跑）；实测新增 %d 次" % (len(calls) - n0))
    finally:
        e.stop_auto_decay()
        e.close()


def test_decay_still_runs_when_not_stopped():
    """D 组（对照）：未停止时后台衰减照常运行。"""
    e = _engine()
    try:
        seen = []
        real = e.decay_cycle

        def traced(*a, **k):
            seen.append(time.time())
            return real(*a, **k)

        e.decay_cycle = traced
        e.start_auto_decay(interval=0.05)
        time.sleep(0.3)
        n = len(seen)
        assert n >= 2, "D 组对照：未停止时衰减应照常运行；实测 %d 次" % n
    finally:
        e.stop_auto_decay()
        e.close()

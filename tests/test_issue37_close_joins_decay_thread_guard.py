# -*- coding: utf-8 -*-
"""test_issue37_close_joins_decay_thread_guard · #37 关闭生命周期竞态守卫
============================================================================
缺陷（lingshu issue #37 · P1）：`SpacetimeMemoryEngine.close()` 只
`stop_auto_decay()`（置 `_running=False`）就 `store.close()`，**不 join** 衰减线程；
而线程循环是 `while self._running: time.sleep(interval); self.decay_cycle()`
——睡醒后**先 `decay_cycle()` 再回判 `_running`**。于是 `close()` 期间被唤醒的
线程会在 `store.close()` **之后**访问已关闭连接（`sqlite3.ProgrammingError`），
异常落在 daemon 线程无人回收，且衰减写入与关库并发。

修法：① 等待改可唤醒的 `Event.wait(interval)`；② 唤醒后**先判 `_running` 再
决定是否 `decay_cycle`**；③ `close()` **join**（有界，`_DECAY_JOIN_TIMEOUT`）。
判据来源：经验标定（本件 #37；仓内无规定后台线程收尾时序的理论章节，追不到
更早出处）。

守卫断言组：
  A 组（join 硬判据）：`close()` 返回时衰减线程必须**已退出**（`is_alive()` 为
     假）——旧实现只置位不 join ⇒ 线程仍在 `sleep` 中 ⇒ 必红。
  B 组（无「关库后衰减」）：记录 `decay_cycle` 调用时刻与 `store.close` 时刻，
     `close()` 之后再等若干个 interval，**不得**出现任何 `close` 之后的
     `decay_cycle` 调用（旧实现唤醒后无条件衰减一次 ⇒ 必红）。
  C 组（收尾不被拖住）：`close()` 在有线程运行时仍应在远小于 interval 的时间内
     返回（Event 唤醒生效，非「等满一个 interval」）；且 `close()` 可重入。
  D 组（对照 · 防误杀）：`start_auto_decay` 后台衰减在**未关闭**时照常运行
     （修法未把衰减线程改坏）。

运行（lingshu 仓根）：
    python -X utf8 tests/test_issue37_close_joins_decay_thread_guard.py
    python -X utf8 -m pytest tests/test_issue37_close_joins_decay_thread_guard.py
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

_PASS, _FAIL = [], []


def ok(cond, name, detail=""):
    (_PASS if cond else _FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name
          + (("  <- " + str(detail)) if detail else ""))


def _engine():
    return SpacetimeMemoryEngine(
        os.path.join(tempfile.mkdtemp(prefix="lingshu_issue37_"), "s.db"))


# ---------- A 组：close() 返回时线程必须已退出 ----------

def group_a_joined_on_close():
    print("== A 组：close() 返回时衰减线程必须已退出 ==")
    e = _engine()
    e.start_auto_decay(interval=0.3)
    t = e._decay_thread
    time.sleep(0.35)                 # 让线程至少跑过一轮，随后回到等待中
    ok(t.is_alive(), "A0 前置：close 前衰减线程在运行", t.is_alive())
    e.close()
    alive = t.is_alive()
    print("     close() 后 is_alive=%s" % alive)
    ok(not alive,
       "A1 close() 返回时衰减线程已退出（旧实现只置位不 join ⇒ 线程仍睡在 "
       "sleep 中、is_alive 为真）", alive)


# ---------- B 组：close 之后不得再发生 decay_cycle ----------

def group_b_no_decay_after_close():
    print("== B 组：close() 之后不得再出现 decay_cycle ==")
    e = _engine()
    calls = []
    orig_decay = e.decay_cycle
    close_ts = {"t": None}

    def traced_decay(*a, **k):
        calls.append(time.time())
        return orig_decay(*a, **k)

    e.decay_cycle = traced_decay
    orig_close = e.store.close

    def traced_close():
        close_ts["t"] = time.time()
        return orig_close()

    e.store.close = traced_close
    e.start_auto_decay(interval=0.2)
    time.sleep(0.25)                 # 跑过一轮，线程回到等待
    e.close()
    time.sleep(0.6)                  # 等 > 2 个 interval：旧实现此刻会衰减一次
    after = [c for c in calls if close_ts["t"] is not None and c > close_ts["t"]]
    print("     decay 调用总数=%d / close 后调用=%d" % (len(calls), len(after)))
    ok(close_ts["t"] is not None, "B0 前置：store.close 被调用")
    ok(not after,
       "B1 close() 之后无任何 decay_cycle（旧实现唤醒后无条件衰减一次 ⇒ 访问已"
       "关闭连接必抛）", after)


# ---------- C 组：收尾不被拖住 + 可重入 ----------

def group_c_close_not_stalled():
    print("== C 组：close() 不被 interval 拖住、可重入 ==")
    e = _engine()
    e.start_auto_decay(interval=5.0)     # 长 interval：旧实现 join 会等满 5s
    time.sleep(0.05)
    t0 = time.time()
    e.close()
    dt = time.time() - t0
    print("     close 用时 %.4fs（interval=5.0）" % dt)
    ok(dt < 1.0,
       "C1 close() 用时远小于 interval（Event 唤醒生效，非等满一个 interval）", dt)
    e.close()                             # 重入不得抛
    ok(True, "C2 close() 可重入（二次调用不抛）")


# ---------- D 组：对照（未关闭时衰减照常） ----------

def group_d_decay_still_works():
    print("== D 组：对照——未关闭时后台衰减照常运行 ==")
    e = _engine()
    seen = []
    orig_decay = e.decay_cycle

    def traced_decay(*a, **k):
        seen.append(time.time())
        return orig_decay(*a, **k)

    e.decay_cycle = traced_decay
    e.start_auto_decay(interval=0.05)
    time.sleep(0.3)
    n = len(seen)
    e.close()
    print("     未关闭期间 decay 调用次数=%d" % n)
    ok(n >= 2, "D1 后台衰减在未关闭时照常运行（修法未改坏线程本体）", n)


def run_checks():
    _PASS.clear(); _FAIL.clear()
    group_a_joined_on_close()
    group_b_no_decay_after_close()
    group_c_close_not_stalled()
    group_d_decay_still_works()
    print()
    print("===== SUMMARY %d/%d 通过 =====" % (len(_PASS), len(_PASS) + len(_FAIL)))
    if _FAIL:
        print("FAILS:", _FAIL)
        return 1
    print("VERDICT=PASS（close() 停线程并 join；关闭后不再有 decay_cycle；收尾不被 "
          "interval 拖住；未关闭时后台衰减照常）")
    return 0


def test_issue37_close_joins_decay_thread_guard():
    """pytest 入口（脚本/pytest 双模式）。"""
    run_checks()
    assert not _FAIL, "%d checks failed: %s" % (len(_FAIL), _FAIL)


if __name__ == "__main__":
    sys.exit(run_checks())

# -*- coding: utf-8 -*-
"""#859 守卫：`StableLease.acquire(history=...)` 的验证历史必须真的被记录并可回读。

缺陷面（公开 issue #859）：签名与 docstring 承诺 `history` 是「不可遗忘、仅记录不
删除」的验证历史，但函数体一次不读——记录不落租约、不在 `state()` / `check()` 的
任何对外读数面出现，传入即蒸发。本件按「兑现契约」处置：

  ① 不传 history：`state()` / `acquire()` 的键集与修前**逐位相同**（不新增键）；
  ② 传 history：`state()["history"]` 与传入记录一致，`acquire()` 返回体同样透出；
  ③ 只增不删（P1-003）：再次 acquire 不带 history ⇒ 旧记录仍在；带 history ⇒ 追加；
  ④ 浅拷贝边界：调用方事后 append 原列表不影响已存记录；
  ⑤ 非 list/tuple ⇒ TypeError，且**在任何写之前**抛出（既有租约与历史不被清掉）。
定点变异：删掉 `acquire` 里合并/落盘 history 的三行 ⇒ ②③ 红；把 `state()` 的
`history` 透出去掉 ⇒ ② 红。
"""
import pytest

from lingshu.world.stable_lease import StableLease

LEGACY_KEYS = {"key", "state", "t_verified", "ttl", "confidence",
               "expires_at", "ttl_expires_at", "weak_at"}


def _hist(*rounds):
    return [{"round": r, "verified_by": "channel-A", "passed": True} for r in rounds]


# ---------------------------------------------------------------- ① 不传零改动

def test_without_history_keeps_legacy_key_set():
    lea = StableLease()
    out = lea.acquire("sensor-7")
    assert set(out.keys()) == LEGACY_KEYS, (
        "不传 history 时返回键集不得变化：实读 %r" % sorted(out.keys()))
    assert set(lea.state("sensor-7").keys()) == LEGACY_KEYS
    assert lea.state("sensor-7")["state"] == "stable"


# ---------------------------------------------------------------- ② 记录可见

def test_history_is_recorded_and_exposed():
    lea = StableLease()
    hist = _hist(1, 2)
    out = lea.acquire("sensor-7", history=hist)
    assert out.get("history") == hist, (
        "acquire() 的返回体必须透出验证历史；实读 %r" % (out.get("history"),))
    assert lea.state("sensor-7")["history"] == hist
    assert set(lea.state("sensor-7").keys()) == LEGACY_KEYS | {"history"}


# ---------------------------------------------------------------- ③ 只增不删

def test_reacquire_without_history_keeps_old_records():
    lea = StableLease()
    lea.acquire("sensor-7", history=_hist(1))
    lea.acquire("sensor-7")                       # 不带 history 的再 acquire
    got = lea.state("sensor-7")["history"]
    assert got == _hist(1), "再 acquire 不带 history 时旧记录必须仍在（P1-003）：%r" % (got,)


def test_reacquire_with_history_appends_in_order():
    lea = StableLease()
    lea.acquire("sensor-7", history=_hist(1))
    lea.acquire("sensor-7", history=_hist(2, 3))
    got = lea.state("sensor-7")["history"]
    assert got == _hist(1, 2, 3), "历史应追加在既有记录之后：%r" % (got,)


# ---------------------------------------------------------------- ④ 浅拷贝边界

def test_caller_list_mutation_does_not_change_stored_records():
    lea = StableLease()
    hist = _hist(1)
    lea.acquire("sensor-7", history=hist)
    hist.append({"round": 99, "verified_by": "attacker", "passed": True})
    got = lea.state("sensor-7")["history"]
    assert [r["round"] for r in got] == [1], (
        "调用方事后改原列表不得污染已存记录（浅拷贝列表）：%r" % (got,))


def test_tuple_history_accepted():
    lea = StableLease()
    lea.acquire("sensor-7", history=tuple(_hist(1)))
    assert lea.state("sensor-7")["history"] == _hist(1)


# ---------------------------------------------------------------- ⑤ fail-closed

def test_bad_history_type_rejected_before_any_write():
    lea = StableLease()
    lea.acquire("sensor-7", history=_hist(1))
    with pytest.raises(TypeError):
        lea.acquire("sensor-7", history="not-a-list")
    st = lea.state("sensor-7")
    assert st["state"] == "stable", "失败的 acquire 不得清掉既有租约：%r" % (st,)
    assert st["history"] == _hist(1), "失败的 acquire 不得清掉既有历史：%r" % (st,)

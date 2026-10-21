# -*- coding: utf-8 -*-
"""守卫 · stable 租约对「未来时间戳」（负 age）必须降级、且不得报出越界确认度
（lingshu issue #729）

缺陷（修复前 `lingshu/world/stable_lease.py::StableLease.check`）
    `check()` 只做「非有限输入闸」`_finite(age, ttl, decayed_conf, …)` —— 负数**是**
    有限数，闸放行。随后 `expired = age >= ttl`（`-100 >= 3600` → False）与
    `weak_conf = decayed < weak_threshold`（`1.0513 < 0.3` → False）双双为 False
    ⇒ 控制流直落末尾 stable 分支。实测（本机 Python 3.12.10，基线 29d2a87）：

        L = StableLease(); L.acquire("k")
        L._leases["k"]["t_verified"] = time.time() + 100.0
        L.check("k") → state='stable' in_lease=True age=-100.0
                       remaining=3700.0（超 TTL）decayed_confidence=1.0513（> 1）
        回拨 86400 s → decayed_confidence=5.7746e+18
        回拨 1e7  s → OverflowError: math range error（check() 直接崩）

    即：`t_verified` 落在未来时（时钟回拨 / 容器迁移 / VM 挂起恢复 / 双机主备切换 /
    NTP 校正）一个**应被重验**的租约被永久误报 stable，并把 > confidence 的
    确认度报给下游。

判据来源（为什么判「降级」而不是「钳位」或「抛异常」）
    · 理论侧：本模块模块头引「智能论 v3.4 · 3.2.2」——
      `S ∈ stable ⟺ (t - t_verified) < TTL ∧ ¬conflict`。式中的 `(t - t_verified)`
      是**已流逝时长**，物理上非负；`age < 0` 意味着「验证发生在未来」，该合取式
      的前提本身不成立 ⇒ 不可判（与 #196 非有限闸同一口径）。
    · 衰减核：模块头 DEV-005 声明 TTL 与 confidence 衰减共享 `exp(-γ·t)`，`t` 同样
      是流逝时长；`age < 0` 时 `exp(-γ·age) > 1`，确认度**反增**并越出
      `(0, confidence]` 值域，极端回拨下 `math.exp` 溢出（1e7 s 实测 OverflowError）。
    · 处置口径：同模块 #196 / #101 的既有形态是「不可判 ⇒ `lease["state"] = "weak"`
      + reason，记录不删除（P1-003）」——`check()` 的返回契约是 dict、不抛异常，
      故**不**改为 raise。也**不**把 `decayed_conf` 钳成 1.0：钳位会把「时钟坏了」
      静默译成「租约仍有效」，正是 #196 要拦的那种误译（本波 #732 的 `max(1, …)`
      静默升级属同类）。
    · 阈值（DEFAULT_TTL / DECAY_GAMMA / WEAK_THRESHOLD）**未改**。

定点变异自证（抽掉修复 ⇒ 必红）
    把负 age 闸回退掉（`if age < 0.0:` 一行整段删除，或改成 `if age < -1e300:`）
    ⇒ `test_future_timestamp_lease_is_not_stable` 与
    `test_extreme_rollback_does_not_overflow` 两条红（后者以 `OverflowError` 崩）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue729_stable_lease_future_timestamp.py -q --no-header
"""
import math
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import lingshu.world.stable_lease as _sl  # noqa: E402
from lingshu.world.stable_lease import StableLease  # noqa: E402


#: 采样年龄（秒）：含**负数**（未来时间戳）、零、正常、TTL 边界两侧、超长。
AGE_SAMPLES = (-1e7, -86400.0, -3600.0, -100.0, -1.0, -1e-9,
               0.0, 1e-9, 1.0, 100.0, 3599.0, 3600.0, 3601.0, 1e5)

#: 冻结时钟（只替换本模块命名空间里的 `time`，不动 stdlib）。
#: 为什么必须冻结：`check()` 自己取 `time.time()`，若用真实墙钟，`-1e-9` 这类
#: 贴零的采样会被「设值 → 取值」之间的微秒级推进翻成正 age（探针假绿/假红）。
#: 冻结后 `_lease_with_age(age)` 读到的 age 与采样**逐位相等**。
#: 基准值取 1.0 而非真实 epoch：float64 在 1.7e9 附近的 ulp ≈ 2.4e-7，
#: `1e-9` 的偏移会被**舍掉**（实测 age 读回 0.0 ⇒ 闸不触发，假绿）。
_FROZEN_NOW = 1.0


class _FrozenClock:
    def __init__(self, t: float):
        self._t = t

    def time(self) -> float:
        return self._t

    def __enter__(self):
        self._saved = _sl.time
        _sl.time = self
        return self

    def __exit__(self, *exc):
        _sl.time = self._saved
        return False


def _lease_with_age(age: float, confidence: float = 1.0) -> StableLease:
    """构造一个租约，使其 `check()` 读到的 age **恰为** `age`（正 = 过去，负 = 未来）。

    调用方须在 `_FrozenClock` 上下文内使用（否则 age 含墙钟推进的微秒噪声）。
    """
    lease = StableLease()
    lease.acquire("k", confidence=confidence)
    lease._leases["k"]["t_verified"] = _sl.time.time() - age
    return lease


# ==================== ① 核心：未来时间戳不得判 stable ====================

def test_guard_inputs_are_not_empty():
    """硬性断言输入非空：防 `for` 空转成假绿。"""
    assert len(AGE_SAMPLES) >= 10, AGE_SAMPLES
    assert any(a < 0 for a in AGE_SAMPLES), "采样须含负 age（未来时间戳）"
    assert any(a == 0.0 for a in AGE_SAMPLES), "采样须含 age == 0 边界"
    assert any(a > 3600.0 for a in AGE_SAMPLES), "采样须含超 TTL 段"


def test_future_timestamp_lease_is_not_stable():
    """★ 核心：`t_verified` 在未来（age < 0）⇒ 不得报 stable / in_lease=True。

    缺陷形态：`expired` 与 `weak_conf` 同时为 False ⇒ 直落 stable 分支。
    """
    assert AGE_SAMPLES, "采样集不得为空"          # 防 for 空转成假绿
    with _FrozenClock(_FROZEN_NOW):
        for age in (a for a in AGE_SAMPLES if a < 0):
            res = _lease_with_age(age).check("k")
            assert res["state"] == "weak", (age, res)
            assert res["in_lease"] is False, (age, res)
            assert "未来" in res["reason"] or "回拨" in res["reason"], (age, res)


def test_future_timestamp_does_not_leak_out_of_range_confidence():
    """★ 越界值不得外泄：返回体里任何 confidence 字段都必须 ≤ 输入 confidence。

    缺陷形态下 `decayed_confidence = confidence·exp(-γ·age) > 1`（回拨 86400 s 实测
    5.77e18），污染下游「知识是否可信」的读数。
    """
    conf_in = 1.0
    with _FrozenClock(_FROZEN_NOW):
        for age in (a for a in AGE_SAMPLES if a < 0):
            res = _lease_with_age(age, confidence=conf_in).check("k")
            for key in ("confidence", "decayed_confidence"):
                if key in res:
                    v = res[key]
                    assert math.isfinite(v) and 0.0 < v <= conf_in, (age, key, v, res)


def test_extreme_rollback_does_not_overflow():
    """★ 极端回拨（1e7 s ≈ 116 天）不得让 check() 崩：缺陷形态抛 OverflowError。

    判据：闸必须落在 `decayed_conf = confidence·exp(-γ·age)` **之前**——否则
    `math.exp(5000)` 直接 `math range error`。
    """
    with _FrozenClock(_FROZEN_NOW):
        res = _lease_with_age(-1e7).check("k")
    assert res["state"] == "weak", res
    assert res["in_lease"] is False, res


def test_age_decay_property_over_full_sample_set():
    """性质测试：全采样集上「stable ⇒ 确认度在 (0, confidence] 且未越 TTL」。

    这条把「越界」与「误报 stable」两个后果一次钉死；含零、TTL 边界（3599/3600/
    3601）与非负正常段——证明修复没有把正常路径一并降级。
    """
    assert len(AGE_SAMPLES) >= 10, AGE_SAMPLES
    stable_ages = [a for a in AGE_SAMPLES if a >= 0 and a < 3600.0]
    assert stable_ages, "须有可判 stable 的采样（防恒红/空转）"
    with _FrozenClock(_FROZEN_NOW):
        for age in AGE_SAMPLES:
            lease = _lease_with_age(age)
            res = lease.check("k")
            assert res["state"] in ("stable", "weak"), (age, res)
            if res["state"] == "stable":
                assert age >= 0.0 and age < 3600.0, (age, res)
                assert 0.0 < res["decayed_confidence"] <= 1.0, (age, res)
                assert res["remaining"] <= lease.ttl, (age, res)
            else:
                assert res["in_lease"] is False, (age, res)


# ==================== ② 正对照（量具有效性） ====================

def test_positive_control_healthy_lease_still_stable():
    """正对照 1：age≈0 的正常租约必须仍报 stable 且 confidence ≤ 1（不是恒红）。

    若修复「一律降级」，本断言变红 —— 证明守卫有判别力而非恒绿。
    """
    lease = StableLease()
    lease.acquire("k")
    res = lease.check("k")
    assert res["state"] == "stable", res
    assert res["in_lease"] is True, res
    assert 0.0 < res["confidence"] <= 1.0, res


def test_positive_control_expired_and_nan_paths_unchanged():
    """正对照 2：过期路径与非有限闸（#196）读数不得被本修复改变。"""
    with _FrozenClock(_FROZEN_NOW):
        expired = _lease_with_age(1e5).check("k")
    assert expired["state"] == "weak" and expired["reason"] == "TTL 超时", expired

    nan_lease = StableLease()
    nan_lease.acquire("k")
    nan_lease._leases["k"]["t_verified"] = float("nan")
    nan_res = nan_lease.check("k")
    assert nan_res["state"] == "weak" and "非有限" in nan_res["reason"], nan_res


def test_positive_control_degrade_renew_path_intact():
    """正对照 3：负 age 降级后仍可带证据续期回 stable（不是把 key 焊死）。"""
    with _FrozenClock(_FROZEN_NOW):
        lease = _lease_with_age(-100.0)
        assert lease.check("k")["state"] == "weak"
        assert lease.renew("k", evidence=True)["state"] == "stable"
        assert lease.check("k")["state"] == "stable"


def test_positive_control_record_is_kept_after_rollback_demotion():
    """P1-003 边界未动：降级只作用于状态，记录（t_verified）保留可查。"""
    with _FrozenClock(_FROZEN_NOW):
        lease = _lease_with_age(-100.0)
        before = lease._leases["k"]["t_verified"]
        lease.check("k")
    assert lease._leases["k"]["t_verified"] == before
    assert lease.state("k")["state"] == "weak"

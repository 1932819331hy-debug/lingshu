# -*- coding: utf-8 -*-
"""守卫 · stable 租约对外报的 expires_at 必须等于**实际**失效时刻，且「TTL 与
confidence 衰减等价」的耦合声明不得再被声称（lingshu issue #19）
============================================================================
缺陷（HEAD 修复前，`lingshu/world/stable_lease.py`）
    ① 模块头 DEV-005 声明：「TTL 与时空记忆图 confidence 衰减共享同一指数衰减核
       exp(-γ·t)——TTL 超时**等价于** confidence 衰减至弱验证阈值以下。」
       实际 `check()` 的失效判据是 `expired or weak_conf` 的**较早者**：
       默认参数（ttl=3600, γ=0.0005, weak_threshold=0.3, confidence=1.0）下
       t_weak = ln(1/0.3)/0.0005 = 2407.9s < 3600s ⇒ **差 33.1%**，并不等价；
       「等价」仅在 confidence* = weak_threshold·e^(γ·ttl) ≈ 1.8149 这一点上成立。
    ② `state()` 报 `expires_at = t_verified + ttl`（TTL 名义到期 3600s），而租约
       在 2407.9s 就已 weak ⇒ **报时比真实失效晚 1192.1s（33%）**。
    ③ 声明里 `¬conflict` 合取项与类 docstring「未超时且无冲突」暗示 check() 判冲突，
       但 check() 无冲突入参、返回体无冲突字段（真冲突只经 degrade() 记入 state）。

修复
    · `state()["expires_at"]` = **实际**失效时刻 `min(ttl_expires_at, weak_at)`，
      与 `check()` 首次报 weak 的时刻一致；另给 `ttl_expires_at`（名义）与
      `weak_at`（弱阈值触发）两个分项。
    · 模块头/类 docstring 收窄：明确二者**不等价**（除单点），并说明冲突经
      `degrade()` 记入状态、check() 不自带冲突判定。

判据来源
    · 理论章节：`lingshu/world/stable_lease.py` 模块头引「智能论 v3.4 · 3.2.2」——
      `S ∈ stable ⟺ (t - t_verified) < TTL ∧ ¬conflict`；DEV-005 的共享衰减核
      `exp(-γ·t)`。两并列失效条件 ⇒ 实际失效时刻为**较早者**，报时须与之一致。
    · `¬conflict` 落在 `lease["state"]`（degrade 记录）——同仓 #101 的既有口径。
    · 阈值本体（DEFAULT_TTL / DECAY_GAMMA / WEAK_THRESHOLD）**未改**。

削掉的判别力（如实）：本件只改**对外报时字段与声明**；`check()` 仍不自带冲突入参
（冲突仍须调用方显式 degrade()），即「3.2.2 的 ¬conflict 合取项由状态承载」这一
设计未变——本件不新增冲突判定能力，只如实收窄声明。

定点变异自证（抽掉修复 ⇒ 必红）
    ① 把 acquire/renew 的 `_expiry_fields(...)` 改回 `expires_at = now + ttl`
       ⇒ test_expires_at_equals_first_weak_moment、
          test_default_params_are_not_equivalent 红（报 3600s，实际 2407.9s）；
    ② 把模块头「并不等价」句改回旧「等价于」⇒ test_declaration_no_longer_claims_equivalence 红。

运行（仓根）：python -X utf8 -m pytest tests/test_stable_lease_expiry_consistency.py -q --no-header
"""
from __future__ import annotations

import inspect
import math
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import lingshu.world.stable_lease as SL  # noqa: E402
from lingshu.world.stable_lease import StableLease  # noqa: E402

T0 = 1_000_000.0


class _Clock:
    """替换模块时间源（只作用于本进程，不改仓库文件）。"""

    def __init__(self, now):
        self.now = now
        self._saved = None

    def __enter__(self):
        self._saved = SL.time.time
        SL.time.time = lambda: self.now
        return self

    def __exit__(self, *a):
        SL.time.time = self._saved


def _first_weak_age(lease, key, lo=0.0, hi=None, steps=80):
    """二分找出 check() 首次返回 weak 的时刻（相对 acquire 的秒数）。"""
    hi = hi if hi is not None else 4 * lease.ttl
    with _Clock(T0) as c:
        lease.acquire(key)
        c.now = T0
        assert lease.check(key)["state"] == "stable"
        for _ in range(steps):
            mid = (lo + hi) / 2
            c.now = T0 + mid
            lease._leases[key]["state"] = "stable"   # 复位（避免「已降级」短路）
            lease._leases[key]["t_verified"] = T0
            if lease.check(key)["state"] == "weak":
                hi = mid
            else:
                lo = mid
    return hi


def test_expires_at_equals_first_weak_moment():
    """★ 核心②：state().expires_at 必须与 check() 首次报 weak 的时刻一致。"""
    with _Clock(T0) as c:
        lease = StableLease()
        lease.acquire("k")
        c.now = T0
        expires_at = lease.state("k")["expires_at"]
    first_weak = T0 + _first_weak_age(StableLease(), "k")
    assert abs(expires_at - first_weak) < 1.0, (expires_at, first_weak)


def test_expires_at_is_earlier_of_ttl_and_weak_threshold():
    """★ 核心②（解析式）：expires_at = min(ttl_expires_at, weak_at)。"""
    with _Clock(T0):
        lease = StableLease()
        lease.acquire("k")
        st = lease.state("k")
    analytic_weak = T0 + math.log(1.0 / lease.weak_threshold) / lease.gamma
    assert abs(st["weak_at"] - analytic_weak) < 1.0, st
    assert abs(st["ttl_expires_at"] - (T0 + lease.ttl)) < 1.0, st
    assert abs(st["expires_at"] - min(st["ttl_expires_at"], st["weak_at"])) < 0.2, st


def test_default_params_are_not_equivalent():
    """★ 核心①②：默认参数下 TTL 与弱阈值**不等价**——旧报时（=ttl）即错。

    旧实现报 expires_at = T0+3600，而实际 2407.9s 失效（差 33%）；本断言在旧形态下
    因 expires_at 远大于 weak_at（且 ≥ ttl_expires_at）而红。
    """
    with _Clock(T0):
        lease = StableLease()
        lease.acquire("k")
        st = lease.state("k")
    assert st["weak_at"] < st["ttl_expires_at"], st
    assert st["expires_at"] < st["ttl_expires_at"], st
    # 差距相对 TTL 时长（非绝对时间戳）：实测 1192.1s / 3600s ≈ 33.1%
    gap = (st["ttl_expires_at"] - st["expires_at"]) / lease.ttl
    assert 0.30 < gap < 0.36, (gap, st)


def test_equivalence_holds_only_at_critical_confidence():
    """正对照：仅在 confidence* = weak_threshold·e^(γ·ttl) 这一点上二者等价。"""
    with _Clock(T0):
        base = StableLease()
        crit = base.weak_threshold * math.exp(base.gamma * base.ttl)
        lease = StableLease()
        lease.acquire("k", confidence=crit)
        st = lease.state("k")
    assert abs(st["expires_at"] - st["ttl_expires_at"]) < 1.0, (crit, st)
    # 高于临界点：TTL 先到（expires_at 落回名义到期）
    with _Clock(T0):
        lease2 = StableLease()
        lease2.acquire("k", confidence=5.0)
        st2 = lease2.state("k")
    assert abs(st2["expires_at"] - st2["ttl_expires_at"]) < 1.0, st2


def test_declaration_no_longer_claims_equivalence():
    """★ 核心①（声明面）：模块头不得再声称「TTL 超时**等价于** confidence 衰减…」。"""
    src = inspect.getsource(SL)
    assert "等价于 confidence 衰减至弱验证阈值以下" not in src, \
        "旧耦合声明仍在（等价性不成立）"
    assert "并不等价" in src, "未如实收窄耦合声明（应明示二者不等价）"
    # check() 无冲突入参、返回体无冲突字段（冲突经 degrade 记入 state）
    assert "conflict" not in inspect.signature(StableLease.check).parameters
    with _Clock(T0):
        lease = StableLease()
        lease.acquire("k")
        keys = set(lease.check("k"))
    assert "conflict" not in keys, keys


def test_positive_control_lease_still_works():
    """正对照（防误杀）：未到期仍报 stable，超时后 weak；阈值未改。"""
    with _Clock(T0) as c:
        lease = StableLease()
        lease.acquire("k")
        c.now = T0 + 100
        assert lease.check("k")["state"] == "stable"
        c.now = T0 + 4000
        assert lease.check("k")["state"] == "weak"
    assert (StableLease().ttl, StableLease().gamma,
            StableLease().weak_threshold) == (3600.0, 0.0005, 0.3)

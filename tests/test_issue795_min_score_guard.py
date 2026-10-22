# -*- coding: utf-8 -*-
"""#795 守卫：`recall` / `search_content` 的 `min_score` 只截断返回面，缺省零改动。

缺陷面（公开 issue #795，2026-10-18 批审 #816 复核）：
`recall` 对完全无关的查询也返回满额结果（score ~0.40，与相关结果 ~0.45 几乎
无区分度），签名又没有 `min_score` ⇒ 调用方无法用 `len(results) == 0` 判断
「库里没有相关记忆」。本件加**增量**参数（缺省 0.0 = 不过滤，既有调用逐位不变）：

  ① 无关查询仍返回基线读数（≥1 条、分数 < 0.45）——锁住「缺陷场景可复现」；
  ② `min_score` 高于全部分数 ⇒ 返回 []；`recall` 与 `search_content` 同；
  ③ 阈值取观测分数的最小值 ⇒ 全部保留（边界不吞等号）；
  ④ 缺省 / 0.0 / None / 负数 与不传参数**逐位相同**（ids+scores 全等）；
  ⑤ NaN/±Inf ⇒ ValueError（不可判的阈值不得被译成「不过滤」）；
  ⑥ 阈值只影响返回面：`search_content` 的候选分数集合仍包含被滤掉的高分项之外
     的分布（以「阈值两侧都有项」的用例锁住——不是只测全空/全留）。
"""
import pytest

from lingshu.core.core import SpacetimeMemoryEngine

ARTICLES = [
    "Article about Python programming with detailed content",
    "Article about machine learning with detailed content",
    "Article about cooking recipes with detailed content",
    "Article about quantum physics with detailed content",
    "Article about gardening tips with detailed content",
]
UNRELATED = "financial derivatives pricing model"


def _engine():
    e = SpacetimeMemoryEngine(":memory:")
    for t in ARTICLES:
        e.add_perception(t, skip_dedup=True)
    return e


def _ids_scores(rows):
    return [(n.id, round(s, 12)) for n, s in rows]


def _assert_same_surface(out, base, api, why):
    """同一返回面：id 序列逐位相同；分数按近似比较（recall 的近因分量随 now 微漂）。"""
    assert [n.id for n, _ in out] == [n.id for n, _ in base], (
        "%s %s：id 序列不同 %r vs %r"
        % (api, why, [n.id for n, _ in out], [n.id for n, _ in base]))
    for (_, a), (_, b) in zip(out, base):
        assert a == pytest.approx(b, rel=1e-6, abs=1e-9), (
            "%s %s：分数偏差过大 %r vs %r" % (api, why, a, b))


# ---------------------------------------------------------------- ① 基线复现

def test_unrelated_query_still_returns_low_scores_by_default():
    e = _engine()
    rows = e.recall(UNRELATED, limit=5)
    assert rows, "无关查询在缺省口径下应有结果（issue #795 的现象）"
    assert all(s < 0.45 for _, s in rows), (
        "本用例的基线前提是「无关查询分数偏低」；实读 %r" % [round(s, 4) for _, s in rows])
    # search_content 同口径
    rows2 = e.search_content(UNRELATED, limit=5)
    assert rows2, "search_content 缺省也应返回候选"


# ---------------------------------------------------------------- ② 全滤

@pytest.mark.parametrize("api", ["recall", "search_content"])
def test_min_score_above_all_scores_returns_empty(api):
    e = _engine()
    rows = getattr(e, api)(UNRELATED, limit=5)
    top = max(s for _, s in rows)
    out = getattr(e, api)(UNRELATED, limit=5, min_score=top + 1e-6)
    assert out == [], "%s 在 min_score > 全部分数时应返回空表，实读 %r" % (api, out)


# ---------------------------------------------------------------- ③ 边界保留

@pytest.mark.parametrize("api", ["recall", "search_content"])
def test_threshold_at_or_below_lowest_keeps_all(api):
    """阈值不高于观测最小值 ⇒ 全保留（`>=` 而非 `>`）。

    `search_content` 的分数不含时间项，用精确等号即验边界；
    `recall` 的分数含近因项，两次调用间随 now 单调微降 ⇒ 精确等号可能把最低项
    滤掉，故取 `low - 1e-6`，仍能锁住「不误滤」（真正误滤会整条消失）。
    """
    e = _engine()
    rows = getattr(e, api)(UNRELATED, limit=5)
    low = min(s for _, s in rows)
    thr = low if api == "search_content" else low - 1e-6
    out = getattr(e, api)(UNRELATED, limit=5, min_score=thr)
    _assert_same_surface(out, rows, api, "阈值不高于最小值时应全部保留")


# ---------------------------------------------------------------- ④ 缺省零改动

@pytest.mark.parametrize("api", ["recall", "search_content"])
@pytest.mark.parametrize("sentinel", [0.0, None, -0.5])
def test_default_and_non_positive_are_identical(api, sentinel):
    e = _engine()
    base = getattr(e, api)(UNRELATED, limit=5)
    out = getattr(e, api)(UNRELATED, limit=5, min_score=sentinel)
    _assert_same_surface(out, base, api, "min_score=%r 应原样返回" % (sentinel,))


# ---------------------------------------------------------------- ⑤ 非有限 fail-closed

@pytest.mark.parametrize("api", ["recall", "search_content"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_min_score_rejected(api, bad):
    e = _engine()
    with pytest.raises(ValueError):
        getattr(e, api)(UNRELATED, limit=5, min_score=bad)


# ---------------------------------------------------------------- ⑥ 阈值两侧都有项

def test_threshold_keeps_only_scores_at_or_above():
    e = _engine()
    rows = e.search_content(UNRELATED, limit=5)
    scores = sorted(s for _, s in rows)
    assert len(scores) >= 2, "夹具至少要有两个候选才能验证两侧截断"
    thr = (scores[0] + scores[-1]) / 2.0
    out = e.search_content(UNRELATED, limit=5, min_score=thr)
    assert out, "阈值取中位时至少应保留最高分项"
    assert all(s >= thr for _, s in out)
    assert len(out) < len(rows), "阈值取中位时应滤掉低分项（不是全留）"

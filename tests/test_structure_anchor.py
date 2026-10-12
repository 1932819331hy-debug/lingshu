# -*- coding: utf-8 -*-
"""test_structure_anchor · 结构锚定检索守卫（LINGSHU_ANCHOR=1，默认关闭）

契约：写入方以标签 `chapter:N` / `session:N` 标记容器；问句显式点名容器（第七章、第3次会话、
chapter 7）时，带该标签的节点按容器内相关度排在最前，余位按原排序补齐。

判据：
  · 默认关闭：anchor_enabled() 为假，点名容器的问句结果与改前逐位相同；
  · 锚点解析：中文数字 1–999、阿拉伯数字、章/回/会话/对话、英文 chapter/session；
    不解析没有「第」的隐式写法（「七章」）；
  · 点名第七章 → top-k 先是第七章的节点（与问句零字面重叠也能进），不混入第十七章；
  · LIKE 路径（LINGSHU_FTS 未开）与 FTS 路径都生效；
  · 无锚点问句：开关开/关结果逐位相同；
  · 容器为空（无节点带该标签）：结果与关闭时相同；
  · 容器标签不进全文索引：打不打 chapter:N 标签不改变无锚点问句的排序。

变异哨兵：把 _anchor_rerank 首行改成直接 `return results`，test_anchor_pulls_container 必红；
把 json.dumps(tag) 换成裸 tag（去掉引号），test_no_prefix_collision 必红；
删掉 _doc 里的 CONTAINER_TAG 过滤，test_container_tags_not_indexed 必红。
"""
import pytest

from lingshu.core import retrieval as R
from lingshu.core.core import SpacetimeMemoryEngine

pytestmark = pytest.mark.filterwarnings("ignore")

CH7 = ["巨子塔在城市中央，没有人见过它的入口。",
       "秦熙把他带进一间安静的茶室，递来一杯热茶。",
       "自由派的老鼠们还在暗处，她压低了声音。"]
CH17 = ["第十七章的叙述：工厂的机器人日夜运转。"]
OTHER = ["这一章在讲什么？大家都在讨论这一章在讲什么。",
         "世界在讲述另一面的故事，这一章讲得很清楚。",
         "蜂巢世界的公民每天贡献两小时。"]
Q = "第七章这一章在讲什么？"


def _engine(tmp_path, tag=True):
    e = SpacetimeMemoryEngine(str(tmp_path / "e.db"))
    ids = {}
    for t in CH7:
        ids[e.add_perception(t, skip_dedup=True, tags=["chapter:7"] if tag else None).id] = "c7"
    for t in CH17:
        ids[e.add_perception(t, skip_dedup=True, tags=["chapter:17"] if tag else None).id] = "c17"
    for t in OTHER:
        ids[e.add_perception(t, skip_dedup=True).id] = "other"
    return e, ids


def _labels(e, ids, q, k=3):
    return [ids[n.id] for n, _ in e.store.search_content(q, limit=k)]


@pytest.fixture(params=["like", "fts"])
def path(request, monkeypatch):
    monkeypatch.delenv("LINGSHU_ANCHOR", raising=False)
    if request.param == "fts":
        monkeypatch.setenv("LINGSHU_FTS", "1")
    else:
        monkeypatch.delenv("LINGSHU_FTS", raising=False)
    return request.param


def test_default_off(monkeypatch):
    monkeypatch.delenv("LINGSHU_ANCHOR", raising=False)
    assert not R.anchor_enabled()


@pytest.mark.parametrize("q,want", [
    ("第七章「被请喝茶」讲什么", ["chapter:7"]),
    ("第十五章", ["chapter:15"]),
    ("第二十三章", ["chapter:23"]),
    ("第一百零三章", ["chapter:103"]),
    ("第五回", ["chapter:5"]),
    ("第3次会话里说了什么", ["session:3"]),
    ("第三次对话", ["session:3"]),
    ("In chapter 12, what", ["chapter:12"]),
    ("第十章和第11章", ["chapter:10", "chapter:11"]),
    ("七章", []),
    ("今天天气怎么样", []),
])
def test_parse_anchors(q, want):
    assert R.parse_anchors(q) == want


def test_anchor_pulls_container(tmp_path, path, monkeypatch):
    e, ids = _engine(tmp_path)
    off = _labels(e, ids, Q)
    assert off.count("c7") < 3  # 改前：字面噪声块挤占 top-3
    monkeypatch.setenv("LINGSHU_ANCHOR", "1")
    assert _labels(e, ids, Q) == ["c7", "c7", "c7"]


def test_no_prefix_collision(tmp_path, path, monkeypatch):
    e, ids = _engine(tmp_path)
    monkeypatch.setenv("LINGSHU_ANCHOR", "1")
    # chapter:1 容器为空：带引号匹配不能让 chapter:17 冒充 chapter:1 → 与关闭时逐位相同
    on = [n.id for n, _ in e.store.search_content("第一章讲了什么", limit=7)]
    monkeypatch.delenv("LINGSHU_ANCHOR")
    assert [n.id for n, _ in e.store.search_content("第一章讲了什么", limit=7)] == on
    monkeypatch.setenv("LINGSHU_ANCHOR", "1")
    assert "c17" not in _labels(e, ids, Q, k=3)


def test_no_anchor_unchanged(tmp_path, path, monkeypatch):
    e, ids = _engine(tmp_path)
    q = "秦熙递来什么"
    off = [n.id for n, _ in e.store.search_content(q, limit=5)]
    monkeypatch.setenv("LINGSHU_ANCHOR", "1")
    assert [n.id for n, _ in e.store.search_content(q, limit=5)] == off


def test_container_tags_not_indexed(tmp_path, monkeypatch):
    monkeypatch.setenv("LINGSHU_FTS", "1")
    monkeypatch.delenv("LINGSHU_ANCHOR", raising=False)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    e1, _ = _engine(tmp_path / "a")
    e2, _ = _engine(tmp_path / "b", tag=False)
    q = "chapter 7 机器人"
    a = [n.content for n, _ in e1.store.search_content(q, limit=7)]
    b = [n.content for n, _ in e2.store.search_content(q, limit=7)]
    assert a == b

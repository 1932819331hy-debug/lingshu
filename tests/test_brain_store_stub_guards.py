#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_brain_store_stub_guards —— #364 / #366 的 4 个**纯桩式**守卫（pytest 式）。

为什么单独成件（issue #485）：`gate.yml` 的两条腿按**文件**互补划分——一件只要列进
`SCRIPT_TESTS`，pytest 腿就整文件 `--ignore` 它、只由脚本腿 `python -X utf8 <file>`
执行。这 4 个守卫原先写在 `tests/test_brain_store.py`（脚本式件，在 `SCRIPT_TESTS`
内）里，而该件的 `main()` **不调用**它们 ⇒ 4 条落在两条腿之外，CI 里谁都不跑
（实测：该件顶层 5 个 `test_*`，脚本腿只执行到 `main()`，pytest 腿整文件 `--ignore`）。

本件是 **pytest 式**（顶层 `test_*` 函数，`pytest tests/` 自动收集），
**不得**列入 `gate.yml` 的 `SCRIPT_TESTS`——列进去就回到「整文件 `--ignore`」的盲区。
`tests/test_gate_coverage.py` 的判据 (b)「pytest 可收集」由本件满足；
通用复发守卫见 `tests/test_gate_script_leg_reachability_guard.py`。

这 4 条**不需要真实脑端**（`MDCG_BRAIN_PYTHONPATH` 不在场也照跑）：它们用桩客户端
`_CaptureClient` 顶替 MCP `tools/call`。故本件**没有 SKIP 分支**，不存在「显式 SKIP
被搬成静默通过」的问题；需要脑端的端到端件仍是 `tests/test_brain_store.py`，
其 `main()` 缺前置时显式 SKIP 并以 0 退出，薄包装 `test_brain_store_m1_m2_loop`
留在原处（由脚本腿经 `main()` 覆盖）。

运行（仓根）：python -X utf8 -m pytest tests/test_brain_store_stub_guards.py -q
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)      # 直跑本件时也能 import lingshu（pytest 侧由根 conftest 保证）


class _CaptureClient:
    """记录 `tools/call` 入参的桩客户端；只回守卫需要的形态。"""

    def __init__(self, read_items=None, chain_items=None):
        self.calls = []
        self._read_items = read_items or []
        self._chain_items = chain_items or []

    def call(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        if name == "mdcg_remember":
            return {"ok": True, "id": "mem_%d" % len(self.calls)}
        if name == "cg" and arguments.get("op") == "read":
            return {"results": self._read_items}
        if name == "cg" and arguments.get("op") == "state_event":
            return {"ok": True}
        if name == "stg":
            return {"items": self._chain_items}
        return {}


def _node(i, ent, z):
    return {"state": "ACCEPT",
            "node": {"id": i, "content": "场景实体 %s" % ent,
                     "frontmatter": {"id": i, "tags": ["spatial", "ent:%s" % ent],
                                     "spatial": {"coords3d": {"x": 0.0, "y": 0.0,
                                                              "z": z}}}}}


def test_364_add_perception_passes_sensitivity_role_condition_space():
    """#364：密级/来源/条件空间必须原样进 mdcg_remember args（不得被 **_kw 静默吞）。

    判据来源：issue #364 分诊（`.tmp/triage_lingshu_world_back.md:30,68`）——
    改前 `add_perception(..., **_kw)` 把 sensitivity/condition_space 吞掉，
    声明 private 的观测按脑端缺省 internal 落盘。变异：把三参数移回 **_kw ⇒ 红。
    """
    from lingshu.world.brain_store import BrainEngine, BrainStore

    c = _CaptureClient()
    eng = BrainEngine(BrainStore(c))
    eng.add_perception("观测", sensitivity="private", role="user",
                       condition_space={"time_window": ["2026-01-01", "2026-12-31"]})
    args = [a for n, a in c.calls if n == "mdcg_remember"][0]
    assert args.get("sensitivity") == "private", args
    assert args.get("role") == "user", args
    assert args.get("condition_space") == {"time_window": ["2026-01-01",
                                                           "2026-12-31"]}, args


def test_364_add_perception_rejects_unknown_kwargs():
    """#364：未具名的关键字必须 fail-closed 抛错，不得静默丢弃（「也未拒绝」）。

    变异：去掉 `if _kw: raise TypeError` ⇒ 本断言红（拼错的参数被静默吞掉）。
    `skip_dedup` 是 legacy 调用形（scene_model.ingest_scene）透传的名字，须被显式
    接收（脑侧直写不做去重，故为如实记录的无操作）——不得误伤。
    """
    from lingshu.world.brain_store import BrainEngine, BrainStore

    c = _CaptureClient()
    eng = BrainEngine(BrainStore(c))
    eng.add_perception("观测", skip_dedup=True)          # legacy 兼容：不抛
    try:
        eng.add_perception("观测", sensitivty="private")  # 拼错的名字
    except TypeError:
        pass
    else:
        raise AssertionError("未具名关键字被静默吞掉（#364：须 fail-closed 抛 TypeError）")


def test_366_state_ledger_keyed_by_node_id_not_entity_name():
    """#366：状态台账 subject＝节点 id，不是实体名（跨场景同名实体互不覆盖）。

    判据来源：issue #366 分诊（`.tmp/triage_lingshu_world_back.md:32,69`）——
    改前 `record_state` 以实体名作 subject（`_node_meta` 登记的名字）、
    `_state_map`/`get_nodes_by_tag` 以实体名作键，另一场景的同名实体会改掉本
    场景的状态。变异：把 subject 改回实体名 ⇒ 本断言红。
    """
    from lingshu.world.brain_store import BrainEngine, BrainStore

    # 两条**同 ent: 名、不同节点 id** 的观测（走 legacy 通路：engine.add_perception
    # 带 entities=[名]），台账里各记各的；另掺一条以实体名（「肥鱼」）作 subject
    # 的旧形态行——它**不得**被当作节点键。
    c = _CaptureClient(
        read_items=[_node("mem_1", "肥鱼", 5.0), _node("mem_2", "肥鱼", 6.0)],
        chain_items=[{"slot": "状态", "state": "active", "subject": "mem_1",
                      "value": "shy"},
                     {"slot": "状态", "state": "active", "subject": "mem_2",
                      "value": "happy"},
                     {"slot": "状态", "state": "active", "subject": "肥鱼",
                      "value": "angry"}])
    store = BrainStore(c)
    eng = BrainEngine(store)
    n1 = eng.add_perception("观测 A", tags=["spatial", "ent:肥鱼"], entities=["肥鱼"])
    n2 = eng.add_perception("观测 B", tags=["spatial", "ent:肥鱼"], entities=["肥鱼"])
    assert n1.id == "mem_1" and n2.id == "mem_2", (n1, n2)

    nodes = {n.id: n for n in store.get_nodes_by_tag("spatial", limit=10)}
    assert nodes["mem_1"].state_attributes == {"state": "shy"}, nodes["mem_1"]
    assert nodes["mem_2"].state_attributes == {"state": "happy"}, nodes["mem_2"]

    # 写向：record_state 的 subject 必须是节点 id（不是实体名）。
    store.record_state(n1.id, "happy")
    se = [a for n, a in c.calls if n == "cg" and a.get("op") == "state_event"]
    assert se and se[0]["subject"] == "mem_1", se
    assert se[0]["slot"] == "状态", se


def test_366_entity_name_only_ledger_does_not_leak_into_nodes():
    """#366 负向：台账里只有以实体名作 subject 的行时，节点不得据此取到状态。

    （钉住「键已从实体名改为节点 id」——旧形态行不再被认作任何节点的状态。）
    """
    from lingshu.world.brain_store import BrainStore

    c = _CaptureClient(
        read_items=[_node("mem_A", "肥鱼", 5.0)],
        chain_items=[{"slot": "状态", "state": "active", "subject": "肥鱼",
                      "value": "angry"}])
    nodes = {n.id: n for n in BrainStore(c).get_nodes_by_tag("spatial", limit=10)}
    assert nodes["mem_A"].state_attributes is None, nodes["mem_A"]

# -*- coding: utf-8 -*-
"""test_world_process_core · 世界过程第 1 组接入 core 与记忆通路（显式注册 · 门面 · 存储 · 组推算）
====================================================================================
被测对象：lingshu/core/core.py 的 SpacetimeMemoryEngine.world_process(action, params)
（接入层）、lingshu/world/__init__.py 的 register_world_process(engine)（注册面），
以及同一条通路上两个被测模块的真实返回值
（lingshu/world/world_process.py 与 lingshu/world/motion_groups.py）。

接入层判据（每条都判“接入真的成立”，不判“模块能跑”——模块自身判据见
tests/test_world_process.py 与 tests/test_motion_groups.py）：
  A 真实接入：观测历史由 UnifiedWorldModel.perceive 经 core 的 world_model 门面逐 tick
    产生；门面各动作的返回值**逐位等于**同一 history 上的模块返回值（不重实现判据）。
  B 形状适配：UnifiedWorldModel 的 [x,y,z] 与 WorldLearner 的 {category,pos}
    只在门面适配一次；喂嵌套形状必须与喂扁平等价，source="world_learner" 读当前
    学习者的历史。
  C 记忆通路：extract 把记录写入既有 store（add_perception），返回 memory_id；
    进程重建（新引擎、同库）后仍能按 memory_id 取回同一条记录，且同记录迁移一致。
  D 组检测与遮蔽成员推算：groups 与模块一致；predict_groups 只凭组模型与本帧可见
    成员恢复被遮蔽成员的真实落点（source="motion_group"、actual=False）。
  E 只读不变量：门面不改写观测历史、不重建模式缓存（生成帧不进 actual 历史）；
    缺历史来源时返回 error，不凭空创建场景与假观测。
  F 门面契约：未知动作与缺参返回 error dict，不抛异常、不落到别的动作。
  G 显式注册（依赖方向 world → core）：core 纯导入不加载 world；import world 也不
    自动注册；注册一次后 7 个动作可用；注册只作用于本实例，不泄漏到别的引擎。
  H 真实 MCP 往返（脑端）：缺前置 MDCG_BRAIN_PYTHONPATH 时跳过；有前置时记录经
    BrainEngine 落脑，重建连接后仍按 memory_id 取回同一条记录。

G 组的两条 fresh-process 判据在本文件用子进程现跑（见 _run_probe）：同进程内已装过
lingshu.world，证明不了“只 import core 不带 world”。

运行（仓根）：
    .venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_world_process_core.py -q -p no:cacheprovider
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine            # noqa: E402
from lingshu.world import motion_groups as MG                  # noqa: E402
from lingshu.world import register_world_process               # noqa: E402
from lingshu.world import world_process as WP                  # noqa: E402
from lingshu.world.world_learner import WorldLearner           # noqa: E402


# ================= 夹具：真实观测历史 =================

#: 接近→停留→离开（与 world_process 单测同款运动，这里只作接入层的输入）。
APPROACH = {
    "landmark": [[10, 1, 10]] * 12,
    "walker": [[4, 1, 10], [5, 1, 10], [6, 1, 10], [7, 1, 10], [8, 1, 10],
               [8.2, 1, 10], [8, 1, 10], [8.1, 1, 10], [7, 1, 10], [5, 1, 10],
               [3, 1, 10], [1, 1, 10]],
}


def frame(tick, entities):
    """一条观测记录（门面与模块共认的扁平形状）。"""
    return {"tick": tick, "entities": {e: list(p) for e, p in entities.items()}}


def approach_history():
    """扁平观测历史：12 tick，由真实观测位置逐 tick 组成。"""
    return [frame(i + 1, {e: t[i] for e, t in APPROACH.items()})
            for i in range(len(APPROACH["walker"]))]


def group_history(n=12, leave_tick=7):
    """三成员共同平移的历史：m_c 从 leave_tick 起独自横移（成员独立后重算组合）。"""
    hist = []
    for i in range(n):
        step = 0.5 * i
        away = 3.0 * (i - leave_tick + 1) if i + 1 >= leave_tick else 0.0
        hist.append(frame(i + 1, {"m_a": [step, 1.0, 0.0],
                                  "m_b": [1.0 + step, 1.0, 0.0],
                                  "m_c": [2.0 + step + away, 1.0, 0.0]}))
    return hist


def drive_world_model(engine, history):
    """经 core 的 world_model 门面把观测逐 tick 送进当前统一世界模型。"""
    engine.world_model("init", {"size": 24, "seed": 7})
    for record in history:
        engine.world_model("perceive", {
            "tool": "observer", "tick": record["tick"],
            "observations": [{"eid": e, "category": "c", "pos": p}
                             for e, p in record["entities"].items()]})


def engine(tmp_path, name="wp.db"):
    """裸引擎：未注册世界过程实现（G 组判据与缺注册报错用）。"""
    return SpacetimeMemoryEngine(db_path=str(tmp_path / name))


def registered(tmp_path, name="wp.db"):
    """显式注册后的引擎：core 不导入 world，每个新建引擎都要注册一次。"""
    eng = engine(tmp_path, name)
    register_world_process(eng)
    return eng


# ================= G 组：显式注册（fresh process） =================

def _run_probe(tmp_path, name, body):
    """在新进程里现跑 body（已 import core），返回探针脚本 json.dumps 的读数 dict。

    脚本头先把 lingshu 加进 sys.path，再 import lingshu.core.core：
    这样“core 纯导入带不带 world”在全新进程里判定，不受本进程已装模块影响。
    退出码非 0 时把 stderr 一并贴出来（否则现场只剩一句 CalledProcessError）。
    """
    head = [
        "import json",
        "import sys",
        "sys.path.insert(0, %r)" % REPO,
    ]
    path = tmp_path / name
    path.write_text("\n".join(head + list(body)) + "\n", encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    done = subprocess.run([sys.executable, "-X", "utf8", "-B", str(path)],
                          cwd=str(tmp_path), env=env, capture_output=True, text=True,
                          encoding="utf-8")
    assert done.returncode == 0, (done.returncode, done.stdout, done.stderr)
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_g1_fresh_process_core_import_loads_no_world(tmp_path):
    """G1：只 import core 不加载 world；调 world_process 也不加载实现模块，且报未注册。

    三个读数分开记：import core 带不带 world；构造引擎带不带 world；调门面新增了
    哪些 world 模块。构造引擎本来就惰性取 world.prediction（#178 既有行为，本次
    未改），所以第二个读数只记录事实、不判空；门面自己不得导入 world_process 与
    motion_groups——这条由第三个读数判定，也由进程内未注册即报 error 佐证。
    """
    # 探针内不出现任何 lingshu.world 导入：加载与否全由 core 一侧决定
    got = _run_probe(tmp_path, "probe_core_only.py", [
        "import sys as _sys",
        "from lingshu.core.core import SpacetimeMemoryEngine",
        "def _world_mods():",
        "    return sorted(m for m in _sys.modules if m == 'lingshu.world' or m.startswith('lingshu.world.'))",
        "after_import = _world_mods()",
        "eng = SpacetimeMemoryEngine(db_path='probe_core.db')",
        "after_engine = _world_mods()",
        "got = eng.world_process('groups', {})",
        "after_call = _world_mods()",
        "eng.close()",
        "print(json.dumps({'import': after_import, 'engine': after_engine,",
        "                  'call': after_call, 'slot': eng._world_process_impl is None,",
        "                  'status': got.get('status'), 'error': got.get('error', '')}))",
    ])
    assert got["import"] == [], "只 import core 就加载了 %s" % got["import"]
    assert set(got["call"]) <= set(got["engine"]), \
        "调 world_process 新加载了 %s" % sorted(set(got["call"]) - set(got["engine"]))
    for name in ("lingshu.world.world_process", "lingshu.world.motion_groups"):
        assert name not in got["call"], "未注册的门面导入了 %s" % name
    assert got["slot"] is True, "core 侧自行装上了实现"
    assert got["status"] == "error", got
    assert "未注册" in got["error"], got["error"]
    assert "register_world_process(engine)" in got["error"], got["error"]


def test_g2_fresh_process_world_import_does_not_auto_register(tmp_path):
    """G2：import world 也不自动注册；显式注册后同一实例立刻可用。"""
    got = _run_probe(tmp_path, "probe_world_import.py", [
        "import sys as _sys",
        "from lingshu.world import register_world_process",
        "from lingshu.world import motion_groups, world_process",
        "core_loaded = 'lingshu.core.core' in _sys.modules",
        "from lingshu.core.core import SpacetimeMemoryEngine",
        "eng = SpacetimeMemoryEngine(db_path='probe_world.db')",
        "before = eng.world_process('groups', {})",
        "first_slot = eng._world_process_impl is None",
        "register_world_process(eng)",
        "history = [{'tick': 1, 'entities': {'a': [0.0, 1.0, 0.0]}}]",
        "after = eng.world_process('groups', {'history': history})",
        "second_slot = eng._world_process_impl is None",
        "eng.close()",
        "print(json.dumps({'core_loaded': core_loaded,",
        "                  'before_status': before.get('status'),",
        "                  'before_error': before.get('error', ''),",
        "                  'first_slot': first_slot, 'after_status': after.get('status'),",
        "                  'second_slot': second_slot}))",
    ])
    assert got["core_loaded"] is False, "导入 lingshu.world 就带入了 lingshu.core.core"
    assert got["before_status"] == "error", got
    assert "未注册" in got["before_error"], got["before_error"]
    assert "register_world_process(engine)" in got["before_error"], got["before_error"]
    assert got["first_slot"] is True, "import world 就自动注册了"
    assert got["after_status"] == "ok", got
    assert got["second_slot"] is False, "显式注册没写进实例"


# ================= 注册面：作用范围与幂等 =================

def test_register_binds_one_engine_only(tmp_path):
    """注册只作用于传入的实例：别的引擎保持未注册，重复注册幂等。"""
    a = engine(tmp_path, "a.db")
    b = engine(tmp_path, "b.db")
    try:
        register_world_process(a)
        assert a.world_process("groups", {"history": group_history(n=6),
                                          "window": 6})["status"] == "ok"
        other = b.world_process("groups", {"history": group_history(n=6), "window": 6})
        assert other["status"] == "error" and "未注册" in other["error"], other

        register_world_process(a)                # 幂等：再注册一次行为不变
        assert a.world_process("groups", {"history": group_history(n=6),
                                          "window": 6}) == {"status": "ok",
                                          "model": MG.detect_motion_groups(
                                              group_history(n=6), window=6)}
    finally:
        a.close()
        b.close()


def test_register_rejects_non_engine(tmp_path):
    """注册面只接引擎：非引擎对象没有注册入口，当场 AttributeError。"""
    with pytest.raises(AttributeError):
        register_world_process(object())

    eng = engine(tmp_path, "bad.db")
    try:
        assert eng.world_process("groups", {})["status"] == "error"
    finally:
        eng.close()


def test_actions_all_route_to_modules_after_register(tmp_path):
    """注册后 7 个动作全部可用，且返回值与模块在同一输入上逐位相同。"""
    eng = registered(tmp_path, "seven.db")
    try:
        history = approach_history()
        proc = WP.recognize_processes(history)[0]

        recognized = eng.world_process("recognize", {"history": history})
        assert recognized["status"] == "ok", recognized
        assert recognized["processes"] == WP.recognize_processes(history), recognized

        events = eng.world_process("events", {"history": history, "eid": "walker"})
        assert events == {"status": "ok", "eid": "walker",
                          "events": WP.motion_events(history, "walker")}, events

        extracted = eng.world_process("extract", {"history": history, "actor": "walker",
                                                  "ref": "landmark", "process": proc})
        assert extracted["status"] == "ok", extracted
        assert extracted["record"] == WP.extract_relative_trajectory(
            history, "walker", "landmark", process=proc), extracted["record"]

        moved = eng.world_process("transfer", {"record": extracted["record"],
                                               "actor": "a2", "ref": "r2",
                                               "translation": (4.0, 0.0, -2.0),
                                               "yaw": 0.5, "scale": 1.5,
                                               "tick_offset": 100})
        assert moved["transfer"] == WP.transfer_demonstration(
            extracted["record"], "a2", "r2", translation=(4.0, 0.0, -2.0), yaw=0.5,
            scale=1.5, tick_offset=100), moved["transfer"]

        loaded = eng.world_process("load", {"memory_id": extracted["memory_id"]})
        assert loaded["status"] == "ok", loaded
        assert loaded["record"] == extracted["record"], loaded

        early = group_history(n=6)
        grouped = eng.world_process("groups", {"history": early, "window": 6})
        assert grouped["model"] == MG.detect_motion_groups(early, window=6), grouped

        predicted = eng.world_process("predict_groups", {"history": early, "window": 6,
                                                         "hide": ["m_c"]})
        assert predicted["status"] == "ok", predicted
        assert "m_c" in predicted["predictions"], predicted
        manual = MG.detect_motion_groups(early, window=6)
        visible = {e: pos for e, pos in early[-1]["entities"].items() if e != "m_c"}
        expect = MG.predict_group_members(manual["groups"][0], visible, early[-1]["tick"])
        assert predicted["predictions"]["m_c"] == expect["predictions"]["m_c"], predicted
    finally:
        eng.close()


# ================= A 组：真实接入（门面 = 模块） =================

def test_a1_recognize_reads_current_model_history(tmp_path):
    """A1：history 缺省取当前统一世界模型的真实观测历史，结果与模块逐位相同。"""
    eng = registered(tmp_path)
    try:
        history = approach_history()
        drive_world_model(eng, history)
        got = eng.world_process("recognize", {})
        assert got["status"] == "ok", got
        assert got["history_len"] == len(history), got["history_len"]
        assert got["processes"] == WP.recognize_processes(history), got["processes"]
        assert [p["type"] for p in got["processes"]] == ["approach_stay_leave"], got
    finally:
        eng.close()


def test_a2_events_and_groups_forward_to_modules(tmp_path):
    """A2：events / groups 的返回值与模块在同一 history 上的返回值逐位相同。"""
    eng = registered(tmp_path)
    try:
        history = approach_history()
        events = eng.world_process("events", {"history": history, "eid": "walker"})
        assert events == {"status": "ok", "eid": "walker",
                          "events": WP.motion_events(history, "walker")}, events

        grouped = group_history(n=6)
        groups = eng.world_process("groups", {"history": grouped, "window": 6})
        assert groups["status"] == "ok", groups
        assert groups["model"] == MG.detect_motion_groups(grouped, window=6)
        assert sorted(groups["model"]["groups"][0]["members"]) == ["m_a", "m_b", "m_c"]
    finally:
        eng.close()


def test_a3_extract_and_transfer_match_modules(tmp_path):
    """A3：extract / transfer 的记录与生成帧等于模块在同一输入上的返回值。"""
    eng = registered(tmp_path)
    try:
        history = approach_history()
        proc = WP.recognize_processes(history)[0]
        got = eng.world_process("extract", {"history": history, "actor": "walker",
                                            "ref": "landmark", "process": proc})
        assert got["status"] == "ok", got
        assert got["record"] == WP.extract_relative_trajectory(
            history, "walker", "landmark", process=proc), got["record"]

        moved = eng.world_process("transfer", {
            "record": got["record"], "actor": "a2", "ref": "r2",
            "translation": (4.0, 0.0, -2.0), "yaw": 0.5, "scale": 1.5,
            "tick_offset": 100})
        assert moved["status"] == "ok", moved
        assert moved["transfer"] == WP.transfer_demonstration(
            got["record"], "a2", "r2", translation=(4.0, 0.0, -2.0), yaw=0.5,
            scale=1.5, tick_offset=100), moved["transfer"]
        assert all(f["source"] == WP.GENERATED for f in moved["transfer"]["frames"])
    finally:
        eng.close()


def test_a4_thresholds_forwarded_to_module(tmp_path):
    """A4：thresholds 原样转发到模块——收紧判据后结果随之变化（门面不吞参数）。"""
    eng = registered(tmp_path, "thresholds.db")
    try:
        history = approach_history()
        strict = WP.ProcessThresholds(min_samples=99)
        got = eng.world_process("recognize", {"history": history, "thresholds": strict})
        assert got["status"] == "ok", got
        assert got["processes"] == [], got
        assert got["processes"] == WP.recognize_processes(history, thresholds=strict)
    finally:
        eng.close()


# ================= B 组：形状适配（只在门面发生一次） =================

def test_b1_nested_learner_shape_equals_flat_shape(tmp_path):
    """B1：WorldLearner 的 {category,pos} 形状喂进门面后与扁平形状等价。"""
    learner = WorldLearner(size=24, seed=5)
    first = learner.world.add_entity("walker", behavior="wander", pos=(2.0, 1.5, 2.0),
                                     speed=0.4)
    learner.world.add_entity("mate", behavior="wander", pos=(6.0, 1.5, 2.0),
                             speed=0.4)
    learner.run(n=6)
    nested = learner.history
    entry = nested[0]["entities"][first]
    assert isinstance(entry, dict) and set(entry) >= {"category", "pos"}, entry
    flat = [frame(r["tick"], {e: v["pos"] for e, v in r["entities"].items()})
            for r in nested]

    eng = registered(tmp_path, "nested.db")
    try:
        got = eng.world_process("recognize", {"history": nested})
        assert got["status"] == "ok", got
        assert got["processes"] == WP.recognize_processes(flat), got["processes"]
        grouped = eng.world_process("groups", {"history": nested})
        assert grouped["model"] == MG.detect_motion_groups(flat), grouped
    finally:
        eng.close()


def test_b2_source_world_learner_reads_current_learner(tmp_path):
    """B2：source="world_learner" 读当前学习者的历史（经 world_learner 门面读取核对）。"""
    eng = registered(tmp_path, "learner.db")
    try:
        eng.world_learner("init", {"size": 24, "seed": 5, "window": 4})
        eng.world_learner("create", {"trees": 1})
        eng.world_learner("entity", {"category": "walker", "behavior": "wander",
                                     "pos": [2.0, 1.5, 2.0], "speed": 0.4})
        eng.world_learner("run", {"n": 10})
        view = eng.world_learner("history", {"limit": 50})["history"]
        flat = [frame(r["tick"], {e: v["pos"] for e, v in r["entities"].items()})
                for r in view]
        got = eng.world_process("groups", {"source": "world_learner"})
        assert got["status"] == "ok", got
        assert got["model"] == MG.detect_motion_groups(flat), got["model"]
    finally:
        eng.close()


# ================= C 组：记忆通路（进程重建后同记录） =================

def test_c1_record_reopens_by_memory_id_and_transfers_identically(tmp_path):
    """C1：extract 落库 → 关引擎 → 新引擎同库按 memory_id 取回 → 迁移结果一致。"""
    history = approach_history()
    first = registered(tmp_path)
    try:
        got = first.world_process("extract", {"history": history,
                                              "actor": "walker", "ref": "landmark"})
        assert got["status"] == "ok", got
        memory_id = got["memory_id"]
        assert json.loads(first.store.get_node(memory_id).content) == got["record"]
    finally:
        first.close()

    second = registered(tmp_path)                 # 重开引擎同样要注册一次
    try:
        loaded = second.world_process("load", {"memory_id": memory_id})
        assert loaded["status"] == "ok", loaded
        assert loaded["record"] == got["record"], loaded["record"]
        from_memory = second.world_process("transfer", {
            "memory_id": memory_id, "actor": "a2", "ref": "r2",
            "translation": (1.0, 0.0, 0.0), "yaw": 0.0, "scale": 1.0})
        from_record = second.world_process("transfer", {
            "record": got["record"], "actor": "a2", "ref": "r2",
            "translation": (1.0, 0.0, 0.0), "yaw": 0.0, "scale": 1.0})
        assert from_memory["transfer"] == from_record["transfer"], from_memory
    finally:
        second.close()


def test_c2_each_extract_is_its_own_record(tmp_path):
    """C2：两次提取同一实体对是两条独立记录（不并被 M5 去重并进旧节点）。"""
    eng = registered(tmp_path, "dedup.db")
    try:
        history = approach_history()
        key = {"history": history, "actor": "walker", "ref": "landmark"}
        first = eng.world_process("extract", dict(key))
        second = eng.world_process("extract", dict(key))
        assert first["memory_id"] != second["memory_id"], first["memory_id"]
        for got in (first, second):
            loaded = eng.world_process("load", {"memory_id": got["memory_id"]})
            assert loaded["record"]["actor"] == "walker", loaded
            assert loaded["record"]["samples"] == first["record"]["samples"], loaded
    finally:
        eng.close()


# ================= D 组：组检测与遮蔽成员推算 =================

def test_d1_masked_member_is_recovered_from_group_model(tmp_path):
    """D1：遮蔽一个成员后，predict_groups 用组模型恢复其真实落点。"""
    eng = registered(tmp_path, "predict.db")
    try:
        early = group_history(n=6)
        grouped = eng.world_process("groups", {"history": early, "window": 6})
        member = sorted(grouped["model"]["groups"][0]["members"])[-1]
        hidden_pos = early[-1]["entities"][member]

        got = eng.world_process("predict_groups", {"history": early, "window": 6,
                                                   "hide": [member]})
        assert got["status"] == "ok", got
        assert member in got["predictions"], got
        predicted = got["predictions"][member]
        assert predicted["source"] == "motion_group" and predicted["actual"] is False
        assert predicted["pos"] == pytest.approx(hidden_pos, abs=1e-6), predicted
        assert got["unresolved"] == {}, got["unresolved"]
        sighted = [e for e in early[-1]["entities"] if e != member]
        assert predicted["anchor"] in sighted, predicted
    finally:
        eng.close()


def test_d2_group_recomputed_after_member_leaves(tmp_path):
    """D2：成员独立运动后重算组合——全窗口不再报含该成员的三人组。"""
    eng = registered(tmp_path, "leave.db")
    try:
        history = group_history(n=12, leave_tick=7)
        early = eng.world_process("groups", {"history": history[:6], "window": 6})
        assert sorted(early["model"]["groups"][0]["members"]) == ["m_a", "m_b", "m_c"]
        late = eng.world_process("groups", {"history": history, "window": 8})
        assert late["status"] == "ok", late
        joined = [g for g in late["model"]["groups"] if "m_c" in g["members"]]
        assert joined == [], joined
        assert late["model"] == MG.detect_motion_groups(history, window=8), late
    finally:
        eng.close()


# ================= E 组：只读不变量（旧行为不变） =================

def test_e1_explicit_history_creates_no_model(tmp_path):
    """E1：只给显式 history 时不创建场景/模型；缺来源时报 error，不造假观测。"""
    eng = registered(tmp_path, "ro.db")
    try:
        assert not hasattr(eng, "_wmodel") and not hasattr(eng, "_wlearner")
        got = eng.world_process("groups", {"history": group_history(n=6), "window": 6})
        assert got["status"] == "ok", got
        assert not hasattr(eng, "_wmodel") and not hasattr(eng, "_wlearner")
        missing = eng.world_process("groups", {})
        assert missing["status"] == "error", missing
        assert "world_model" in missing["error"], missing
        assert not hasattr(eng, "_wmodel"), "缺来源时不得顺手创建模型"
    finally:
        eng.close()


def test_e2_facade_keeps_history_and_prediction_cache(tmp_path):
    """E2：门面只读——观测历史不变，模式缓存对象不被重建（生成帧不进 actual 历史）。"""
    eng = registered(tmp_path, "keep.db")
    try:
        history = approach_history()
        drive_world_model(eng, history)
        before = eng.world_model("history", {"limit": 50})["history"]
        patterns = eng.world_model("patterns", {})["patterns"]
        patterns["wp_probe"] = 1        # 只改测试拿到的那个缓存对象
        extracted = eng.world_process("extract", {"actor": "walker", "ref": "landmark"})
        assert extracted["status"] == "ok", extracted
        for action, params in (
                ("recognize", {}), ("events", {"eid": "walker"}), ("groups", {}),
                ("predict_groups", {"hide": ["walker"]}),
                ("transfer", {"memory_id": extracted["memory_id"], "actor": "a2",
                              "ref": "r2", "translation": (5.0, 0.0, 0.0),
                              "yaw": 0.4, "scale": 1.2})):
            got = eng.world_process(action, params)
            assert got["status"] == "ok", (action, got)

        after = eng.world_model("history", {"limit": 50})["history"]
        assert after == before, "门面改写了观测历史（生成帧不得进 actual）"
        assert eng.world_model("patterns", {})["patterns"].get("wp_probe") == 1, \
            "门面重建了模式缓存（读当前模型不得改变已有预测缓存）"
    finally:
        eng.close()


# ================= F 组：门面契约 =================

def test_f1_unknown_action_and_missing_params_return_error(tmp_path):
    """F1：未知动作与缺参返回 error dict（不抛异常、不静默落到别的动作）。"""
    eng = registered(tmp_path, "contract.db")
    try:
        history = approach_history()
        unknown = eng.world_process("nope", {"history": history})
        assert unknown["status"] == "error" and "未知动作" in unknown["error"], unknown
        for action, params in (("recognize", {"source": "nope"}),
                               ("extract", {"history": history, "actor": "walker"}),
                               ("transfer", {"actor": "a", "ref": "b"}),
                               ("events", {"history": history}),
                               ("load", {})):
            got = eng.world_process(action, params)
            assert got["status"] == "error", (action, got)
        absent = eng.world_process("load", {"memory_id": "node_missing"})
        assert absent["status"] == "error", absent
    finally:
        eng.close()


def test_f2_unregistered_engine_returns_error_not_exception(tmp_path):
    """F2：未注册的裸引擎返回 error 并写明初始化步骤；未知动作仍报未知动作。"""
    eng = engine(tmp_path, "unregistered.db")
    try:
        got = eng.world_process("recognize", {"history": approach_history()})
        assert got["status"] == "error", got
        assert "未注册" in got["error"], got["error"]
        assert "register_world_process(engine)" in got["error"], got["error"]
        assert "每新建或重开引擎都要注册一次" in got["error"], got["error"]

        unknown = eng.world_process("nope", {})
        assert "未知动作" in unknown["error"], unknown
    finally:
        eng.close()


# ================= H 组：真实 MCP 往返（脑端） =================

@pytest.fixture
def brain_factory(tmp_path):
    """脑端连接工厂：缺前置 MDCG_BRAIN_PYTHONPATH 时跳过（与既有脑端用例同款）。"""
    path = os.environ.get("MDCG_BRAIN_PYTHONPATH")
    if not path:
        pytest.skip("真实 MCP 往返需要 MDCG_BRAIN_PYTHONPATH（CI 提供固定脑端提交）")
    from lingshu.world.brain_store import connect
    agents = []

    def reopen():
        agent = connect(root=str(tmp_path / "brain"), pythonpath=path,
                        extra_env={"MDCG_LEGACY_ENV_AUTH": "1", "MDCG_CAN_ADMIN": "1",
                                   "MDCG_LEGACY_ENV_ADMIN": "1", "MDCG_SUSTAIN": "0",
                                   "MDCG_AUX_ROOT": str(tmp_path / "brain_aux")})
        agents.append(agent)
        return agent

    yield reopen
    for agent in agents:
        agent.store.client.close()


def test_h1_record_round_trips_through_brain(tmp_path, brain_factory):
    """H1：记录经 BrainEngine 落脑；重建连接后仍按 memory_id 取回同一条记录。"""
    history = approach_history()
    eng = registered(tmp_path, "brain.db")
    first = brain_factory()
    try:
        got = eng.world_process("extract", {"history": history, "actor": "walker",
                                            "ref": "landmark", "brain": first})
        assert got["status"] == "ok" and got.get("brain_memory_id"), got
    finally:
        eng.close()
    first.store.client.close()

    second = brain_factory()
    eng2 = registered(tmp_path, "brain.db")       # 重开引擎同样要注册一次
    try:
        loaded = eng2.world_process("load", {"memory_id": got["brain_memory_id"],
                                             "brain": second})
        assert loaded["status"] == "ok", loaded
        assert loaded["record"] == got["record"], loaded["record"]
        moved = eng2.world_process("transfer", {
            "memory_id": got["brain_memory_id"], "brain": second,
            "actor": "a2", "ref": "r2", "translation": (2.0, 0.0, 0.0)})
        assert moved["status"] == "ok", moved
        assert moved["transfer"]["frames"][0]["source"] == WP.GENERATED
    finally:
        eng2.close()


if __name__ == "__main__":
    import pytest as _pytest
    sys.exit(_pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))

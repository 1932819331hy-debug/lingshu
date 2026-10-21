# -*- coding: utf-8 -*-
"""test_world_process_core · 世界过程接入 core 与记忆通路（显式注册 · 门面 · 存储 · 组推算）
====================================================================================
被测对象：lingshu/core/core.py 的 SpacetimeMemoryEngine.world_process(action, params)
（接入层）、lingshu/world/__init__.py 的 register_world_process(engine)（注册面），
以及同一条通路上三个被测模块的真实返回值（lingshu/world/world_process.py、
lingshu/world/motion_groups.py 与 lingshu/world/process_ops.py）。

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
    自动注册；注册一次后 10 个动作可用；注册只作用于本实例，不泄漏到别的引擎。
  H 真实 MCP 往返（脑端）：缺前置 MDCG_BRAIN_PYTHONPATH 时跳过；有前置时记录经
    BrainEngine 落脑，重建连接后仍按 memory_id 取回同一条记录；脑端也覆盖 complete
    与 compose 的 memory_id 读记录路径。
  I 第 2 组三动作（事件回声 / 过程补全 / 过程重组）：返回值逐位等于模块；阈值对象
    原样转发；本地记忆重开后 complete 与 compose 都能按 memory_id 读同一条记录；
    只注册两个模块时三动作返回 error、第 1 组七动作照常可用。complete 与 compose 把
    模块抛出的 ValueError 转成 error dict（同名角色、无效 joint 参数），其余异常照旧上抛。

注：本文件与 tests/test_event_echo.py 各自持有同款“停止→转向”运动构造（本文件的
courier_history 与 _COURIER_* 常量）。两件独立持有输入数据，不互相导入，因此任一件
被删改都不影响另一件的收集。

G 组的两条 fresh-process 判据在本文件用子进程现跑（见 _run_probe）：同进程内已装过
lingshu.world，证明不了“只 import core 不带 world”。
I 组的“只注入前两个模块”判据同样在本进程里现建实例跑，不依赖任何子进程。

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
from lingshu.world import process_ops as OPS                   # noqa: E402
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


#: 第 2 组事件回声的输入运动：一个实体每 13 步一轮，第 3 步停一步、第 8 步左转
#: 90°。停止与转向各自相隔 13 步，事件流是 stop→turn 与 turn→stop 交替。
_COURIER_PERIOD = 13
_COURIER_STOP_OFFSET = 3
_COURIER_TURN_OFFSET = 8
_COURIER_CYCLES = 8


def courier_track():
    """按上述节奏造轨迹：计划步上停一步或左转 90°，其余步直行一步。"""
    steps = _COURIER_CYCLES * _COURIER_PERIOD
    kinds = {}
    for cycle in range(_COURIER_CYCLES):
        for offset, name in ((_COURIER_STOP_OFFSET, "stop"),
                             (_COURIER_TURN_OFFSET, "turn")):
            step = cycle * _COURIER_PERIOD + offset
            if 1 <= step <= steps:
                kinds.setdefault(step, []).append(name)
    dx, dz, x, z = 1.0, 0.0, 0.0, 0.0
    track = [[0.0, 1.0, 0.0]]
    for step in range(1, steps + 1):
        kind = kinds.get(step) or []
        if kind == ["stop"]:
            track.append([x, 1.0, z])
            continue
        if "turn" in kind:
            dx, dz = -dz, dx
        x, z = x + dx, z + dz
        track.append([x, 1.0, z])
    return track


def courier_history():
    """事件回声的输入历史：8 轮共 16 条事件（停止 8 条、转向 8 条）。"""
    return [frame(i + 1, {"courier": pos}) for i, pos in enumerate(courier_track())]


#: 事件回声的不规则场景：停止到转向的间隔按 1、2、4 循环，停止到转向的主 lag
#: 只占该源签名有响应触发的 2/6，所以 min_consistency 一档之差就改变学到的关联。
_UNEVEN_STEPS = 40
_UNEVEN_STOP = (3, 8, 14, 22, 26, 32)
_UNEVEN_TURN = (4, 10, 18, 23, 28, 36)


def uneven_track():
    """按上述步号造轨迹：停的该步位移为 0，转的该步左转 90°，其余步直行一步。"""
    stops, turns = set(_UNEVEN_STOP), set(_UNEVEN_TURN)
    dx, dz, x, z = 1.0, 0.0, 0.0, 0.0
    track = [[0.0, 1.0, 0.0]]
    for step in range(1, _UNEVEN_STEPS + 1):
        if step in stops:
            track.append([x, 1.0, z])
            continue
        if step in turns:
            dx, dz = -dz, dx
        x, z = x + dx, z + dz
        track.append([x, 1.0, z])
    return track


def uneven_history():
    """不规则节奏的事件回声输入历史：6 条停止、6 条转向（eid = courier）。"""
    return [frame(i + 1, {"courier": pos}) for i, pos in enumerate(uneven_track())]


def segment_pair():
    """过程重组的两段记录来源：前段 rider 从离 beacon 4.0 走到 2.0，后段接着走。"""
    seg_a = {"beacon": [[0, 1, 0]] * 5,
             "rider": [[4, 1, 0], [3.5, 1, 0], [3, 1, 0], [2.5, 1, 0], [2, 1, 0]]}
    seg_b = {"beacon": [[0, 1, 0]] * 5,
             "rider": [[2, 1, 0], [1.5, 1, 0.5], [1, 1, 1], [0.5, 1, 1.5], [0, 1, 2]]}
    return ([frame(i + 1, {e: t[i] for e, t in seg_a.items()}) for i in range(5)],
            [frame(i + 1, {e: t[i] for e, t in seg_b.items()}) for i in range(5)])


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
    """注册后 10 个动作全部可用，且返回值与模块在同一输入上逐位相同。"""
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


# ================= I 组：第 2 组三动作（事件回声 · 过程补全 · 过程重组） =================

def test_i1_echo_forwards_report_like_module(tmp_path):
    """I1：echo 的 report 与模块在同一 history 上的返回值逐位相同（逐位比较，不重实现）。"""
    eng = registered(tmp_path, "echo.db")
    try:
        history = courier_history()
        got = eng.world_process("echo", {"history": history})
        assert got["status"] == "ok", got
        assert got["history_len"] == len(history), got["history_len"]
        assert got["report"] == WP.event_echo_report(history), got["report"]

        report = got["report"]
        assert report["kind"] == "event_echo_report"
        assert [e["tick"] for e in report["events"] if e["event"] == "stop"] == \
            [4, 17, 30, 43, 56, 69, 82, 95]
        assert [e["tick"] for e in report["events"] if e["event"] == "turn"] == \
            [9, 22, 35, 48, 61, 74, 87, 100]
        # 切分按独立计数核：16 条事件、留出 ceil(16 × 0.3) = 5 条、开发段 11 条。
        assert report["split"] == {"events": 16, "dev_events": 11,
                                   "holdout_events": 5, "boundary_tick": 74,
                                   "dev_last_tick": 69, "holdout_first_tick": 74,
                                   "same_tick_grouped": True}
        assert report["split"]["holdout_events"] == 5
        assert report["split"]["dev_events"] == 16 - 5
        assert report["split"]["dev_last_tick"] < \
            report["split"]["holdout_first_tick"]
        # 无泄漏：留出段就是流的最后 5 条，两段的 (tick, eid, event) 交集为空。
        keys = [(e["tick"], e["eid"], e["event"]) for e in report["events"]]
        assert keys == sorted(keys)
        dev_keys, hold_keys = keys[:11], keys[11:]
        assert len(hold_keys) == report["split"]["holdout_events"]
        assert set(dev_keys) & set(hold_keys) == set()
        # 关联按独立计数核：停到转 lag 5、转到停 lag 8，两条各 5 次、一致性 1.0。
        assert [(a["after"], a["before"], a["lag"], a["count"], a["consistency"])
                for a in report["associations"]] == [
            ("stop", "turn", 5, 5, pytest.approx(1.0)),
            ("turn", "stop", 8, 5, pytest.approx(1.0))]
        # 固定例的命中值按实际评分重算：留出 5 条里 4 条命中、末条越过观察尾部。
        lag = report["evaluation"]["lag_model"]
        base = report["evaluation"]["no_delay_model"]
        assert lag["hits"] == 4 and lag["predictions"] == 5
        assert lag["abs_tick_errors"] == [0, 0, 0, 0]
        assert lag["mean_abs_tick_error"] == 0.0
        assert lag["scored"] == 4 and lag["prediction_beyond_window"] == 1
        assert lag["coverage"] == pytest.approx(1.0)
        assert base["hits"] == 0 and base["predictions"] == 5
        assert base["abs_tick_errors"] == [8, 5, 8, 5]
        assert base["mean_abs_tick_error"] == 6.5
        assert [(r["tick"], r["predicted_tick"], r["actual_tick"], r["hit"])
                for r in report["evaluation"]["lag_readings"]] == [
            (74, 82, 82, True), (82, 87, 87, True), (87, 95, 95, True),
            (95, 100, 100, True), (100, 108, None, False)]
        # 命中数不超过留出事件数，也不超过预测数。
        for model in (lag, base):
            assert 0 <= model["hits"] <= model["predictions"] <= model["events"]
            assert model["events"] == report["split"]["holdout_events"]
            assert model["hits"] <= report["split"]["holdout_events"]
        assert lag["hit_rate"] > base["hit_rate"]
    finally:
        eng.close()


def test_i2_echo_forwards_cut_and_thresholds(tmp_path):
    """I2：echo 的调用参数按模块原样转发——切分比例与阈值对象改一个数就换一档读数。"""
    eng = registered(tmp_path, "echo_cut.db")
    try:
        history = courier_history()
        cut = eng.world_process("echo", {"history": history, "holdout_fraction": 0.5})
        assert cut["report"]["split"]["holdout_events"] == 8, cut["report"]["split"]
        assert cut["report"] == WP.event_echo_report(history, holdout_fraction=0.5), cut

        # 换一档转向判据后事件流随之变化：门面不吞参数、不替换阈值对象。
        # turn_deg 提高 100 度：本例的转向都是 90 度，全部不再判为转向，只剩 8 条停止。
        strict = WP.ProcessThresholds(turn_deg=100.0)
        tight = eng.world_process("echo", {"history": history, "thresholds": strict})
        assert [e["event"] for e in tight["report"]["events"]] == ["stop"] * 8, \
            tight["report"]["events"]
        assert tight["report"]["thresholds"]["turn_deg"] == 100.0, tight["report"]
        assert tight["report"] == WP.event_echo_report(history, thresholds=strict), tight

        narrowed = eng.world_process("echo", {"history": history, "max_lag": 4})
        assert narrowed["report"]["thresholds"]["max_lag"] == 4, narrowed["report"]
        assert narrowed["report"]["associations"] == [], narrowed["report"]
    finally:
        eng.close()


def test_i4_echo_forwards_min_consistency(tmp_path):
    """I4：min_consistency 经 core 原样转发——改一档就改变学到的关联。"""
    eng = registered(tmp_path, "echo_minc.db")
    try:
        history = uneven_history()
        default = eng.world_process("echo", {"history": history})
        assert default["status"] == "ok", default
        assert default["report"]["thresholds"]["min_consistency"] == 0.7
        assert default["report"] == WP.event_echo_report(history), default
        # 默认门槛 0.7：只保留转到停 lag 4 这一条（一致性 1.0）。
        assert [(a["after"], a["before"], a["lag"], a["count"])
                for a in default["report"]["associations"]] == [
            ("turn", "stop", 4, 3)]
        assert default["report"]["associations"][0]["consistency"] == \
            pytest.approx(1.0)

        # 放宽到 0.3：停到转 lag 1（一致性 0.5）这一条也进来。
        loose = eng.world_process("echo", {"history": history,
                                           "min_consistency": 0.3})
        assert loose["report"]["thresholds"]["min_consistency"] == 0.3
        assert loose["report"] == WP.event_echo_report(
            history, min_consistency=0.3), loose
        assert [(a["after"], a["before"], a["lag"], a["count"], a["consistency"])
                for a in loose["report"]["associations"]] == [
            ("stop", "turn", 1, 2, pytest.approx(0.5)),
            ("turn", "stop", 4, 3, pytest.approx(1.0))]
        # 门槛只改变关联，不改变事件流与切分。
        assert loose["report"]["events"] == default["report"]["events"]
        assert loose["report"]["split"] == default["report"]["split"]
        assert loose["report"]["evaluation"]["min_consistency"] == 0.3
        # 关联多一条，留出段的预测数随之上升：默认 2 条，放宽后 4 条。
        assert default["report"]["evaluation"]["lag_model"]["predictions"] == 2
        assert loose["report"]["evaluation"]["lag_model"]["predictions"] == 4
    finally:
        eng.close()


def test_i3_complete_and_compose_match_modules(tmp_path):
    """I3：complete 与 compose 的返回值等于模块在同一输入上的返回值（含生成帧来源）。"""
    eng = registered(tmp_path, "ops.db")
    try:
        history = approach_history()
        record = WP.extract_relative_trajectory(history, "walker", "landmark")

        completed = eng.world_process("complete", {"history": history[:6],
                                                   "actor": "walker", "ref": "landmark",
                                                   "record": record})
        assert completed["status"] == "ok", completed
        assert completed["completion"] == OPS.complete_process(
            history[:6], "walker", "landmark", [record]), completed["completion"]
        completion = completed["completion"]
        assert completion["match"]["source"]["index"] == 0
        assert completion["match"]["match_score"] == 1.0
        assert [f["tick"] for f in completion["frames"]] == [7, 8, 9, 10, 11, 12]
        assert all(f["source"] == WP.GENERATED for f in completion["frames"])

        first_hist, second_hist = segment_pair()
        first = WP.extract_relative_trajectory(first_hist, "rider", "beacon")
        second = WP.extract_relative_trajectory(second_hist, "rider", "beacon")
        composed = eng.world_process("compose", {
            "first": first, "second": second,
            "roles": {"actor": "hero", "ref": "mark"},
            "translation": (10.0, 0.0, 5.0)})
        assert composed["status"] == "ok", composed
        assert composed["composition"] == OPS.compose_processes(
            first, second, roles={"actor": "hero", "ref": "mark"},
            translation=(10.0, 0.0, 5.0)), composed["composition"]
        composition = composed["composition"]
        assert composition["joint"]["continuous"] is True
        assert composition["joint"]["ref_gap"] == 0.0
        assert composition["joint"]["actor_gap"] == 0.0
        assert composition["ticks"] == [1, 10]
        assert all(set(f["entities"]) == {"hero", "mark"} for f in composition["frames"])
    finally:
        eng.close()


def test_i4_ops_thresholds_forwarded_to_module(tmp_path):
    """I4：thresholds 传 ProcessOpsThresholds 实例，门面按模块原样转发（不转换、不吞）。"""
    eng = registered(tmp_path, "ops_thresholds.db")
    try:
        history = approach_history()
        record = WP.extract_relative_trajectory(history, "walker", "landmark")

        # 前缀逐帧偏移整体差 0.6：默认容差 0.5 不命中，容差 0.7 命中。
        shifted = [frame(i + 1, {"landmark": [10, 1, 10],
                                 "walker": [round(0.6 + px, 6), 1, 10]})
                   for i, px in enumerate((4.0, 5.0, 6.0, 7.0, 8.0, 8.2))]
        tight = eng.world_process("complete", {"history": shifted, "actor": "walker",
                                               "ref": "landmark", "record": record})
        assert tight["completion"]["match"] is None, tight["completion"]["match"]
        loose = eng.world_process("complete", {
            "history": shifted, "actor": "walker", "ref": "landmark",
            "record": record, "thresholds": OPS.ProcessOpsThresholds(match_tol=0.7)})
        assert loose["completion"]["match"] is not None, loose["completion"]
        assert loose["completion"]["thresholds"]["match_tol"] == 0.7
        assert loose["completion"] == OPS.complete_process(
            shifted, "walker", "landmark", [record],
            thresholds=OPS.ProcessOpsThresholds(match_tol=0.7)), loose["completion"]

        first_hist, second_hist = segment_pair()
        far = [frame(i + 1, {"beacon": [0, 1, 0],
                             "rider": [6 - i, 1, i * 0.5]}) for i in range(5)]
        first = WP.extract_relative_trajectory(first_hist, "rider", "beacon")
        third = WP.extract_relative_trajectory(far, "rider", "beacon")
        broken = eng.world_process("compose", {"first": first, "second": third,
                                              "roles": {"actor": "hero", "ref": "mark"},
                                              "translation": (10.0, 0.0, 5.0)})
        assert broken["composition"]["joint"]["continuous"] is False, broken["composition"]
        assert broken["composition"]["joint"]["actor_gap"] == pytest.approx(4.0)
        assert len(broken["composition"]["frames"]) == 10, "不衔接也要给出两段帧"

        loose_joint = eng.world_process("compose", {
            "first": first, "second": third, "roles": {"actor": "hero", "ref": "mark"},
            "translation": (10.0, 0.0, 5.0),
            "thresholds": OPS.ProcessOpsThresholds(joint_tol=5.0)})
        assert loose_joint["composition"]["joint"]["continuous"] is True, loose_joint
        assert loose_joint["composition"]["thresholds"]["joint_tol"] == 5.0
    finally:
        eng.close()


def test_i5_complete_and_compose_read_records_from_memory(tmp_path):
    """I5：extract 落库后，complete 与 compose 按 memory_id 读回同一条记录并算出同结果。

    场景：先把两段观测经 extract 落库，再关引擎、用同一个 db 新建引擎重新注册，
    之后 complete 用 record_ids、compose 用 first_id/second_id 读记录——
    结果与直接传 record 逐位相同（门面复用 load 通路，不另开读法）。
    """
    first_hist, second_hist = segment_pair()
    prefix_hist = approach_history()[:6]
    full_hist = approach_history()

    first = registered(tmp_path, "ops_memory.db")
    try:
        walker_record = first.world_process("extract", {
            "history": full_hist, "actor": "walker", "ref": "landmark"})
        assert walker_record["status"] == "ok", walker_record
        seg_a = first.world_process("extract", {"history": first_hist, "actor": "rider",
                                                "ref": "beacon"})
        seg_b = first.world_process("extract", {"history": second_hist, "actor": "rider",
                                                "ref": "beacon"})
        for got in (seg_a, seg_b):
            assert got["status"] == "ok", got
    finally:
        first.close()

    second = registered(tmp_path, "ops_memory.db")
    try:
        by_ids = second.world_process("complete", {
            "history": prefix_hist, "actor": "walker", "ref": "landmark",
            "record_ids": [walker_record["memory_id"]]})
        by_record = second.world_process("complete", {
            "history": prefix_hist, "actor": "walker", "ref": "landmark",
            "record": walker_record["record"]})
        assert by_ids["status"] == "ok", by_ids
        assert by_ids["completion"] == by_record["completion"], by_ids["completion"]

        # 单条 memory_id 也走同一条读记录路径（complete 的记录序列长度 1）。
        by_single = second.world_process("complete", {
            "history": prefix_hist, "actor": "walker", "ref": "landmark",
            "memory_id": walker_record["memory_id"]})
        assert by_single["completion"] == by_record["completion"], by_single["completion"]

        composed_by_ids = second.world_process("compose", {
            "first_id": seg_a["memory_id"], "second_id": seg_b["memory_id"],
            "roles": {"actor": "hero", "ref": "mark"},
            "translation": (10.0, 0.0, 5.0)})
        composed_by_records = second.world_process("compose", {
            "first": seg_a["record"], "second": seg_b["record"],
            "roles": {"actor": "hero", "ref": "mark"},
            "translation": (10.0, 0.0, 5.0)})
        assert composed_by_ids["status"] == "ok", composed_by_ids
        assert composed_by_ids["composition"] == composed_by_records["composition"], \
            composed_by_ids["composition"]
        assert composed_by_ids["composition"]["joint"]["continuous"] is True
    finally:
        second.close()


def test_i6_group_two_actions_need_registration_and_params(tmp_path):
    """I6：三动作的 error 契约——未注册、只注册两个模块、缺参、坏参数都不抛异常。"""
    bare = engine(tmp_path, "bare_ops.db")
    try:
        for action in ("echo", "complete", "compose"):
            got = bare.world_process(action, {})
            assert got["status"] == "error", (action, got)
            assert "未注册" in got["error"], got
            assert "register_world_process(engine)" in got["error"], got
    finally:
        bare.close()

    # 只注入前两个模块：第 1 组七动作照常可用，第 2 组三动作各报未注册 process_ops。
    legacy = engine(tmp_path, "legacy.db")
    try:
        legacy.register_world_process(WP, MG)
        assert legacy.world_process("recognize", {"history": approach_history()})["status"] \
            == "ok"
        assert legacy.world_process("load", {"memory_id": "nope"})["status"] == "error"
        assert legacy.world_process("transfer", {"actor": "a", "ref": "b"})["status"] == "error"
        assert "recognize" in legacy.WORLD_PROCESS_ACTIONS
        assert legacy.world_process("recognize", {"history": approach_history()})["status"] \
            == "ok"
        assert legacy.world_process("groups", {"history": group_history(n=6),
                                               "window": 6})["status"] == "ok"
        for action in ("echo", "complete", "compose"):
            got = legacy.world_process(action, {"history": approach_history()})
            assert got["status"] == "error", (action, got)
            assert "process_ops" in got["error"], got["error"]
    finally:
        legacy.close()

    eng = registered(tmp_path, "contract_ops.db")
    try:
        history = approach_history()
        # 缺参：都返回 error dict，不抛异常、不落到别的动作。
        assert "complete 需要 actor 与 ref" == \
            eng.world_process("complete", {"history": history, "actor": "walker"})["error"]
        assert "需要 record 或 record_ids" == \
            eng.world_process("complete", {"history": history, "actor": "walker",
                                           "ref": "landmark"})["error"]
        assert "record_ids 必须是" in \
            eng.world_process("complete", {"history": history, "actor": "walker",
                                           "ref": "landmark",
                                           "record_ids": "node_x"})["error"]
        empty_compose = eng.world_process("compose", {})
        assert empty_compose["status"] == "error", empty_compose
        assert "compose 需要两段记录" in empty_compose["error"], empty_compose
        half_compose = eng.world_process("compose", {"first": {"samples": [1]}})
        assert half_compose["status"] == "error", half_compose
        assert "compose 需要两段记录" in half_compose["error"], half_compose
        absent = eng.world_process("compose", {"first_id": "node_missing",
                                               "second_id": "node_missing2"})
        assert absent["status"] == "error" and "前段" in absent["error"], absent
        # 未知动作仍报未知动作（不因新动作改口径）。
        unknown = eng.world_process("nope", {})
        assert "未知动作" in unknown["error"], unknown
    finally:
        eng.close()


def test_i7_complete_and_compose_reject_illegal_input_as_error(tmp_path):
    """I7：两动作把模块的 ValueError 转成 error dict；非 ValueError 照旧上抛。

    判据分三层：① 同名角色与无效 joint 参数（roles 缺键、tick_gap 小于 1、记录没有
    采样）返回 status=error，error 文案等于模块口径；② 对应合法调用的返回值仍与
    模块逐位相同；③ AttributeError 与 TypeError 不被吞，仍从门面抛出。
    """
    eng = registered(tmp_path, "ops_contract.db")
    try:
        history = approach_history()
        record = WP.extract_relative_trajectory(history, "walker", "landmark")
        first_hist, second_hist = segment_pair()
        first = WP.extract_relative_trajectory(first_hist, "rider", "beacon")
        second = WP.extract_relative_trajectory(second_hist, "rider", "beacon")
        same_name = "actor 与 ref 不得同名：帧里的 entities 会互相覆写"

        # ① complete：actor 与 ref 同名（模块在生成任何帧之前拒绝）。
        bad_complete = eng.world_process("complete", {
            "history": history[:6], "actor": "walker", "ref": "walker",
            "record": record})
        assert bad_complete == {"status": "error", "error": same_name}, bad_complete

        # ① compose：roles 显式同名、省略 roles 而前段记录本身同名、roles 缺键、
        #    tick_gap 小于 1、前段记录没有采样——都返回 error dict，不抛异常。
        cases = [
            ({"first": first, "second": second,
              "roles": {"actor": "hero", "ref": "hero"}}, same_name),
            ({"first": dict(first, actor="solo", ref="solo"), "second": second},
             same_name),
            ({"first": first, "second": second, "roles": {"actor": "hero"}},
             "roles 必须同时给出 actor 与 ref"),
            ({"first": first, "second": second, "tick_gap": 0},
             "tick_gap 必须是不小于 1 的整数"),
            ({"first": {"actor": "a", "ref": "b", "samples": []}, "second": second},
             "first 记录没有采样，无法重组"),
        ]
        for params, message in cases:
            bad = eng.world_process("compose", params)
            assert bad == {"status": "error", "error": message}, (bad, params)

        # ② 合法调用不受影响：返回值与模块在同一输入上的返回值逐位相同。
        good_complete = eng.world_process("complete", {
            "history": history[:6], "actor": "walker", "ref": "landmark",
            "record": record})
        assert good_complete["status"] == "ok", good_complete
        assert good_complete["completion"] == OPS.complete_process(
            history[:6], "walker", "landmark", [record]), good_complete["completion"]
        good_compose = eng.world_process("compose", {
            "first": first, "second": second, "roles": {"actor": "hero", "ref": "mark"},
            "translation": (10.0, 0.0, 5.0)})
        assert good_compose["status"] == "ok", good_compose
        assert good_compose["composition"] == OPS.compose_processes(
            first, second, roles={"actor": "hero", "ref": "mark"},
            translation=(10.0, 0.0, 5.0)), good_compose["composition"]

        # ③ 只捕获 ValueError：其余异常照旧从门面抛出，不变成 error dict。
        with pytest.raises(AttributeError):
            eng.world_process("compose", {"first": [], "second": second})
        with pytest.raises(TypeError):
            eng.world_process("compose", {"first": first, "second": second,
                                          "translation": 5})
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
    """H1：记录经 BrainEngine 落脑；重建连接后仍按 memory_id 读回并做补全与重组。"""
    history = approach_history()
    first_hist, second_hist = segment_pair()
    eng = registered(tmp_path, "brain.db")
    first = brain_factory()
    try:
        got = eng.world_process("extract", {"history": history, "actor": "walker",
                                            "ref": "landmark", "brain": first})
        assert got["status"] == "ok" and got.get("brain_memory_id"), got
        # 过程重组的两段记录也落脑：下面按脑端 memory_id 读回来做重组。
        seg_a = eng.world_process("extract", {"history": first_hist, "actor": "rider",
                                              "ref": "beacon", "brain": first})
        seg_b = eng.world_process("extract", {"history": second_hist, "actor": "rider",
                                              "ref": "beacon", "brain": first})
        for part in (seg_a, seg_b):
            assert part["status"] == "ok" and part.get("brain_memory_id"), part
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

        # 第 2 组也走脑端读记录路径：过程补全按 memory_id 从脑端取回同一条记录。
        completed = eng2.world_process("complete", {
            "history": history[:6], "actor": "walker", "ref": "landmark",
            "memory_id": got["brain_memory_id"], "brain": second})
        assert completed["status"] == "ok", completed
        assert completed["completion"] == OPS.complete_process(
            history[:6], "walker", "landmark", [got["record"]]), completed["completion"]
        assert completed["completion"]["match"] is not None, completed["completion"]
        assert [f["tick"] for f in completed["completion"]["frames"]] == [7, 8, 9, 10, 11, 12]
        # compose 也走脑端 id 路径：按 first_id/second_id 从脑端读回两段记录，
        # 结果与直接传 record 逐位相同。
        composed = eng2.world_process("compose", {
            "first_id": seg_a["brain_memory_id"], "second_id": seg_b["brain_memory_id"],
            "brain": second, "roles": {"actor": "hero", "ref": "mark"},
            "translation": (10.0, 0.0, 5.0)})
        assert composed["status"] == "ok", composed
        assert composed["composition"] == OPS.compose_processes(
            seg_a["record"], seg_b["record"], roles={"actor": "hero", "ref": "mark"},
            translation=(10.0, 0.0, 5.0)), composed["composition"]
    finally:
        eng2.close()


if __name__ == "__main__":
    import pytest as _pytest
    sys.exit(_pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))

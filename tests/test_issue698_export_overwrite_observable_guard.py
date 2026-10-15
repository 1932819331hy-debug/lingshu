# -*- coding: utf-8 -*-
"""#698 导出「覆盖已存在目标」由静默改为显式上报 —— 守卫（lingshu/core/core.py）

缺陷定性（lingshu issue #698）：
  `SpacetimeMemoryEngine.export_all` / `export_trajectory` 的目标路径由**调用方**
  给出、**不过** `_confine_*` 根闸（那两个闸的适用面是 world3d render /
  wm_simloop flush 的受保护产物），目标已存在即**静默覆盖**——调用方拿到返回值
  也无从知道「上一份被替换掉了」。

★本件**不改覆盖语义**：覆盖是**既有契约**（本方法自称「灾备基础」，重做备份
  必须能覆盖上一份），`tests/test_issue330_export_all_atomic_guard.py` 的 D 用例
  即锁定「二次导出到同一路径 ⇒ 覆盖」。本件只让覆盖**可观测**：
  `export_all` 返回值加 `overwrote_existing`（bool）；`export_trajectory` 在
  `output_path` 给出时同样在返回值上多带该键（落盘文件内容形状不变）。

断言组（抽掉修复＝删掉 `overwrote_existing` 那两行 ⇒ G1/G2/G4 必红 KeyError）：
  G1 目标不存在 ⇒ `overwrote_existing is False`，且产物已落盘
  G2 目标已存在（哨兵）⇒ `overwrote_existing is True`，且哨兵字节确被替换、
     新内容可 `json.load`（覆盖**确实发生**，不是「拒覆盖」）
  G3 契约不变：连续两次导出同一路径 ⇒ 第二次 `exported_nodes` 与第二次导出时的
     节点数一致（#330 D 用例语义未被本件改变；「我没动语义」的自证）
  G4 `export_trajectory`：已存在 ⇒ True 且哨兵被替换；不给 `output_path`
     （批量模式，无落盘）⇒ **不带**该键、返回体形状仍为 `{"cycles","meta"}`
     （#57 契约不变）

运行（仓根）：python -X utf8 -m pytest tests/test_issue698_export_overwrite_observable_guard.py -q --no-header
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402

SENTINEL = b'{"PRETEND":"original-backup-must-not-stay"}'


def _engine():
    eng = SpacetimeMemoryEngine(":memory:")
    eng.add_perception("导出探针", skip_dedup=True)
    return eng


def _sentinel(path):
    with open(path, "wb") as f:
        f.write(SENTINEL)
    return path


# ---------------- G1：目标不存在 ⇒ 不上报覆盖 ----------------

def test_g1_目标不存在_不上报覆盖():
    td = tempfile.mkdtemp()
    path = os.path.join(td, "fresh.json")
    eng = _engine()

    res = eng.export_all(path)

    assert res["overwrote_existing"] is False, \
        "G1 目标原本不存在，不该上报覆盖：%r" % (res,)
    assert os.path.exists(path), "G1 产物未落盘"
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert len(data["nodes"]) == 1, "G1 落盘内容不对"


# ---------------- G2：目标已存在 ⇒ 上报覆盖且确实替换 ----------------

def test_g2_目标已存在_上报覆盖且内容被替换():
    td = tempfile.mkdtemp()
    path = _sentinel(os.path.join(td, "over.json"))
    eng = _engine()

    res = eng.export_all(path)

    assert res["overwrote_existing"] is True, \
        "G2 目标已存在却未上报覆盖（静默覆盖回归）：%r" % (res,)
    raw = open(path, "rb").read()
    assert SENTINEL not in raw, "G2 哨兵内容仍在 ⇒ 覆盖未发生"
    with open(path, encoding="utf-8") as f:
        data = json.load(f)                       # 新内容必须是完整可解析 JSON
    assert len(data["nodes"]) == 1, "G2 新内容不对"


# ---------------- G3：覆盖式语义（#330 D 契约）未被本件改变 ----------------

def test_g3_覆盖式语义保留_第二次导出计数正确():
    td = tempfile.mkdtemp()
    path = os.path.join(td, "twice.json")
    eng = _engine()

    eng.export_all(path)
    eng.add_perception("第二条", skip_dedup=True)
    res2 = eng.export_all(path)

    assert res2["exported_nodes"] == 2, "G3 二次导出计数不对：%r" % (res2,)
    assert res2["overwrote_existing"] is True, "G3 二次导出该上报覆盖：%r" % (res2,)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert len(data["nodes"]) == 2, \
        "G3 覆盖式语义丢失（内容为 %d 条）" % len(data["nodes"])


# ---------------- G4：export_trajectory 同法 ----------------

def test_g4_轨迹导出_已存在上报覆盖_批量模式不加键():
    td = tempfile.mkdtemp()
    path = _sentinel(os.path.join(td, "traj.json"))
    eng = _engine()
    eng.add_perception(content=json.dumps({"cycle_id": "c0"}),
                       tags=["cycle_record_full"])

    res = eng.export_trajectory(None, path)

    assert res["overwrote_existing"] is True, \
        "G4 目标已存在却未上报覆盖（静默覆盖回归）：%r" % (res,)
    raw = open(path, "rb").read()
    assert SENTINEL not in raw, "G4 哨兵内容仍在 ⇒ 覆盖未发生"
    with open(path, encoding="utf-8") as f:
        json.load(f)
    assert "overwrote_existing" not in json.loads(raw.decode("utf-8")), \
        "G4 该键只走返回值，不该写进落盘内容"

    # 批量模式（无 output_path、无落盘）⇒ 不加键，#57 返回体形状不变
    batch = eng.export_trajectory(None)
    assert set(batch.keys()) == {"cycles", "meta"}, \
        "G4 批量模式返回体形状变了（#57 契约）：%r" % (sorted(batch.keys()),)

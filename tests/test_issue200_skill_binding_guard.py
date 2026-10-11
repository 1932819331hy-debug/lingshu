# -*- coding: utf-8 -*-
"""#200 技能绑定用 LIKE 子串召回 —— 守卫（lingshu/core/core.py）

缺陷（分诊表 `triage_lingshu_core_front.md` 行 200 判定「成立」；issue 原文
`_b4_issue_200.json` 给了四小节）：
  ①**子串误绑**：`record_action_sequence` 成功且带 `skill_hint` 时用
    `recall_skill(skill_hint, limit=1)` 取第 1 条，而 `search_skills` 是
    `name LIKE %hint% OR description LIKE %hint%` ⇒ hint「备份」命中「删除备份」，
    备份步骤被拼进删除技能的程序、删除技能被增信（实测程序变成
    `rm -rf /backup/old → tar … > scp …`，conf 0.5→0.55）。
  ②**通配符未转义**：`%` / `_` 原样进 LIKE ⇒ `recall_skill("_")` 命中任意技能卡。
  ③**只增不减**：失败分支只登记 `rejected_path`，**从不**降技能置信度。
  ④**程序无界增长**：每次成功都把同一步骤再拼一遍。

修法（最小 · 复用既有原语）：
  · 新增 `LayeredStore._escape_like`：`%`/`_`/`\\` 转义，`search_skills` 加 `ESCAPE`。
  · 新增 `LayeredStore.get_skill_by_name`（`WHERE name = ?` 精确面）；
    `record_action_sequence` 的绑定改经它（模糊检索仍留给 `recall_skill` 公开面）。
  · 失败且带 hint ⇒ 对**精确同名**技能 `-0.05`（`update_skill_confidence` 已钳 [0,1]）。
  · `append_skill_procedure` 追加前按 `→` 分段去重。

判据来源：分诊表行 200 ＋ issue 正文「建议」4 条（`_b4_issue_200.json`）；
仓内无规定该绑定口径的理论章节（经验标定，本件 #200）。
边界（削掉的判别力，如实标注）：
  · ③的降信**仅对已存在的同名技能**生效——失败发生在该技能创建之前时无对象可降，
    故 issue 正文那条「50 败 → 10 胜」的**先败后成**序列本件后仍为 0.5（失败阶段
    尚无技能卡）；本守卫改用「先成后败」序列断言 `< 0.5`。本件**不**引入
    「失败即建技能卡」的语义（那会把失败序列变成技能资产，越出最小修复面）。
  · `recall_skill` 的模糊语义**保留**（公开检索面不变），只把**绑定**改精确。
  · 程序去重是**字面**分段比对（`→` 分割、`strip` 后相等），非语义去重。

断言组（抽掉修复即 Q1/Q2/Q3/Q4 红）：
  Q1 子串误绑不再：hint「备份」不动「删除备份」（conf 与程序原样），且新建「备份」卡。
  Q2 通配符转义：`recall_skill("%")` / `recall_skill("_")` 只命中名称字面含该字符者。
  Q3 失败降信：先 10 成后 50 败 ⇒ 置信度 < 0.5（旧：恒 0.95 / 1.0）。
  Q4 程序有界：同一步骤重复追加 ⇒ 程序分段数不随调用次数增长。
  Q5 精确面：`get_skill_by_name` 精确命中、无同名返回 None。
  Q6 防误杀：无同名 hint 仍新建技能（正对照，conf=0.5）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue200_skill_binding_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402


def _engine():
    return SpacetimeMemoryEngine(":memory:")


def _names(e, q, limit=50):
    return [s["name"] for s in e.recall_skill(q, limit=limit)]


def test_q1_substring_hint_no_longer_hijacks_other_skill():
    """Q1：hint「备份」不得把步骤拼进「删除备份」/为其增信。"""
    e = _engine()
    e.store_skill("删除备份", "清理过期备份", "rm -rf /backup/old", confidence=0.5)
    e.record_action_sequence(["tar czf db.tgz /data", "scp db.tgz nas:/backup"],
                             "ok", True, skill_hint="备份")
    victim = e.store.get_skill_by_name("删除备份")
    assert round(victim["confidence"], 3) == 0.5, (
        "「删除备份」被 hint「备份」增信：conf=%s" % victim["confidence"])
    assert victim["procedure"] == "rm -rf /backup/old", (
        "备份步骤被拼进删除技能程序：%r" % victim["procedure"])
    # 正确落点：新建同名卡「备份」
    fresh = e.store.get_skill_by_name("备份")
    assert fresh is not None, "hint「备份」未新建同名技能卡"
    assert "tar czf db.tgz /data" in fresh["procedure"]


def test_q2_like_wildcards_are_escaped():
    """Q2：`%` / `_` 按字面匹配（旧：命中任意技能卡）。"""
    e = _engine()
    e.store_skill("a%b", "含百分号", "p1")
    e.store_skill("a_b", "含下划线", "p2")
    e.store_skill("删除备份", "无通配符字符", "p3")
    assert _names(e, "%") == ["a%b"], "百分号未转义，命中：" + repr(_names(e, "%"))
    assert _names(e, "_") == ["a_b"], "下划线未转义，命中：" + repr(_names(e, "_"))
    assert "删除备份" not in _names(e, "_"), "下划线仍命中任意技能卡（通配符未转义）"


def test_q3_failures_decrease_confidence():
    """Q3：失败降信（旧：失败从不降信，恒 0.95/1.0）。"""
    e = _engine()
    e.store_skill("部署", "部署流程", "deploy.sh", confidence=0.5)
    for _ in range(10):
        e.record_action_sequence(["deploy"], "ok", True, skill_hint="部署")
    after_wins = e.store.get_skill_by_name("部署")["confidence"]
    for _ in range(50):
        e.record_action_sequence(["deploy"], "boom", False, skill_hint="部署")
    after_losses = e.store.get_skill_by_name("部署")["confidence"]
    assert after_losses < 0.5, (
        "50 败后置信度仍 >=0.5（失败未降信）：10胜后=%s 50败后=%s"
        % (round(after_wins, 3), round(after_losses, 3)))


def test_q4_procedure_append_is_deduped():
    """Q4：同一步骤重复追加 ⇒ 程序不随调用次数无界增长。"""
    e = _engine()
    e.store_skill("部署", "部署流程", "deploy.sh")
    for _ in range(5):
        e.record_action_sequence(["deploy"], "ok", True, skill_hint="部署")
    proc = e.store.get_skill_by_name("部署")["procedure"]
    segs = [s.strip() for s in proc.split("→")]
    assert segs == ["deploy.sh", "deploy"], (
        "同步骤被重复拼入程序（%d 段）：%r" % (len(segs), proc))


def test_q5_exact_name_lookup():
    """Q5：精确名称面——命中同名、无同名返回 None。"""
    e = _engine()
    e.store_skill("删除备份", "d", "p")
    assert e.store.get_skill_by_name("删除备份")["name"] == "删除备份"
    assert e.store.get_skill_by_name("备份") is None, "精确面退化成了子串匹配"


def test_q6_new_hint_still_creates_skill():
    """Q6：防误杀——无同名 hint 仍新建技能（正对照）。"""
    e = _engine()
    e.record_action_sequence(["a", "b"], "ok", True, skill_hint="全新技能")
    sk = e.store.get_skill_by_name("全新技能")
    assert sk is not None and round(sk["confidence"], 3) == 0.5

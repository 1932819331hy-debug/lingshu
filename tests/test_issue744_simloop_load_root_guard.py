# -*- coding: utf-8 -*-
"""#744 wm_simloop("load") 的 mdcg_root 无读根闸（读向与写向不对称）—— 守卫

缺陷（lingshu issue #744 · [core/wm_simloop][安全]）：
  `SpacetimeMemoryEngine.wm_simloop("load")` 把模型面给的 `params["mdcg_root"]`
  **原样**透传给 `SimulationLoop.load_priors`，后者枚举 `{root}/contextual/*.md`
  并把 `- entity: …` 行 `scene.add_entity` 注入世界模型 ⇒ 指向真源之外的任意树
  即可注入**对抗先验**（本件修复前实测：根外树 `seeded=2`、毒实体
  `category='PWNED_EXTERNAL'` 进 `scene.entities`）。写向 `flush` 早有
  `_confine_simloop_out_path`（#324/#480），读向无对应闸 = 同一门面两个方向不对称。

修法（与写向同款 `realpath + normcase` 根判定，越界 fail-closed）：
  · 白名单读根＝①显式配置面 `LINGSHU_SIMLOOP_IN_ROOT`（`os.pathsep` 多根）
    ②`MDCG_ROOT`（**读真源本就是本动作的正当语义**，故不禁真源、只禁真源之外）；
  · 两源皆缺 ⇒ 无根可依 ⇒ fail-closed（不按任意路径枚举先验）；
  · 空 `mdcg_root` 不设闸（回落 `MDCG_ROOT` 由 `load_priors` 承担，那是配置面
    而非模型面）。

断言组（抽掉任一修复即红）：
  A 根外树被拒且**零注入**（含正对照：同一棵树直调库 API 确会种入 ⇒ 探针非空转）
  B 合法根照常（防误杀）：真源根 / 真源子目录形态 ⇒ ok 且 seeded 正确
  C 显式读根放行（`MDCG_ROOT` 不设；含 `os.pathsep` 多根的第二根）
  D 无根可依 ⇒ 拒且零注入（fail-closed）
  E `..` 逃逸 ⇒ 拒（含「真源父目录」「真源兄弟目录」两形态）
  F 空值不设闸（防过修）：不传 / `""` / `None` ⇒ ok 且照常种子

运行（仓根）：python -X utf8 -m pytest tests/test_issue744_simloop_load_root_guard.py -q --no-header
"""
from __future__ import annotations

import io
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402

IN_ENV = SpacetimeMemoryEngine.SIMLOOP_IN_ROOT_ENV

POISON = """---
id: "poison_note"
tags: [wm_simloop]
---
# 根外树里的对抗先验（模型可控 mdcg_root 即可注入）

- entity: PWNED_EXTERNAL id=evilA pos=(1.0, 2.0, 3.0)
"""

LEGIT = """---
id: "legit_note"
tags: [wm_simloop]
---
# 合法先验

- entity: benign id=b1 pos=(2.0, 2.0, 2.0)
"""


def _write_note(root, rel="contextual/run.md", body=LEGIT):
    """在 root 下写一条 `tags: [wm_simloop]` 先验笔记，返回其绝对路径。"""
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    return path


def _engine(td):
    eng = SpacetimeMemoryEngine(":memory:")
    r = eng.wm_simloop("setup", {"size": 12, "seed": 3,
                                 "wal_path": os.path.join(td, "wal.jsonl")})
    assert r["status"] == "ok", "前置：setup 失败 %r" % (r,)
    return eng


def _cats(eng):
    """世界模型当前全部实体类别（注入判定用）。"""
    return sorted(str(getattr(e, "category", "")) for e in eng._simloop.scene.entities.values())


def test_a_根外树被拒且零注入(monkeypatch):
    """A：`MDCG_ROOT` 之外的真树（含毒先验）⇒ 拒、零注入；正对照证明树是活的。"""
    td = tempfile.mkdtemp()
    lib = os.path.join(td, "mdcg_lib")
    os.makedirs(lib, exist_ok=True)
    outside = os.path.join(td, "outside")
    _write_note(outside, body=POISON)

    monkeypatch.delenv(IN_ENV, raising=False)
    monkeypatch.setenv("MDCG_ROOT", lib)
    eng = _engine(td)

    r = eng.wm_simloop("load", {"mdcg_root": outside})
    assert r["status"] == "error", "A 根外树未被拒：%r" % (r,)
    assert "#744" in r.get("error", ""), "A 拒绝理由未点名 #744：%r" % (r,)
    assert _cats(eng) == [], "A 被拒后仍注入实体：%r" % (_cats(eng),)

    # 正对照（量具自证）：同一棵树直调库 API（绕过门面）确会种入 ⇒ 上面的零注入
    # 不是「树本来就是空/格式不对」造成的空转。
    direct = eng._simloop.load_priors(outside)
    assert direct["seeded"] == 1, "A 正对照失败：探针树未种入 %r" % (direct,)
    assert any("PWNED_EXTERNAL" in c for c in _cats(eng)), \
        "A 正对照失败：毒实体未进世界模型 %r" % (_cats(eng),)


def test_b_合法根照常(monkeypatch):
    """B：防误杀——真源根、真源子目录形态均照常（闸不得退化为「只认全等」）。"""
    td = tempfile.mkdtemp()
    lib = os.path.join(td, "mdcg_lib")
    _write_note(lib)
    monkeypatch.delenv(IN_ENV, raising=False)
    monkeypatch.setenv("MDCG_ROOT", lib)
    eng = _engine(td)

    r = eng.wm_simloop("load", {"mdcg_root": lib})
    assert r["status"] == "ok", "B 真源根被误拒：%r" % (r,)
    assert r["load"]["seeded"] == 1, "B 真源根 seeded 不符：%r" % (r["load"],)

    sub = os.path.join(lib, "nested")
    _write_note(sub)
    r2 = eng.wm_simloop("load", {"mdcg_root": sub})
    assert r2["status"] == "ok", "B 真源子目录被误拒（只认全等）：%r" % (r2,)
    assert r2["load"]["seeded"] == 1, "B 子目录 seeded 不符：%r" % (r2["load"],)

    # `<lib>/contextual` 形态：闸须放行（该层没有更深 contextual/ ⇒ seeded=0，
    # 但**不得**因路径不在白名单而被拒）。
    r3 = eng.wm_simloop("load", {"mdcg_root": os.path.join(lib, "contextual")})
    assert r3["status"] == "ok", "B 真源内次级目录被误拒：%r" % (r3,)


def test_c_显式读根放行(monkeypatch):
    """C：`MDCG_ROOT` 不设、显式读根（含多根）⇒ 放行。"""
    td = tempfile.mkdtemp()
    other = os.path.join(td, "other_root")
    _write_note(other)
    second = os.path.join(td, "second_root")
    _write_note(second)

    monkeypatch.delenv("MDCG_ROOT", raising=False)
    monkeypatch.setenv(IN_ENV, other)
    eng = _engine(td)
    r = eng.wm_simloop("load", {"mdcg_root": other})
    assert r["status"] == "ok", "C 显式读根被误拒：%r" % (r,)
    assert r["load"]["seeded"] >= 1, "C 显式读根 seeded 不符：%r" % (r["load"],)

    # 多根（`os.pathsep`）：第二根同样在册。
    monkeypatch.setenv(IN_ENV, os.pathsep.join([other, second]))
    r2 = eng.wm_simloop("load", {"mdcg_root": second})
    assert r2["status"] == "ok", "C 多根第二根被误拒：%r" % (r2,)
    assert r2["load"]["seeded"] >= 1, "C 多根第二根 seeded 不符：%r" % (r2["load"],)


def test_d_无根可依即拒(monkeypatch):
    """D：两源皆缺 ⇒ 无根可依 ⇒ fail-closed 且零注入。"""
    td = tempfile.mkdtemp()
    outside = os.path.join(td, "outside")
    _write_note(outside, body=POISON)
    monkeypatch.delenv(IN_ENV, raising=False)
    monkeypatch.delenv("MDCG_ROOT", raising=False)
    eng = _engine(td)

    r = eng.wm_simloop("load", {"mdcg_root": outside})
    assert r["status"] == "error", "D 无根可依仍放行：%r" % (r,)
    assert "#744" in r.get("error", ""), "D 拒绝理由未点名 #744：%r" % (r,)
    assert _cats(eng) == [], "D 被拒后仍注入实体：%r" % (_cats(eng),)


def test_e_点划逃逸被拒(monkeypatch):
    """E：`..` 逃逸（真源父目录 / 真源兄弟目录）⇒ 拒，且零注入。"""
    td = tempfile.mkdtemp()
    lib = os.path.join(td, "mdcg_lib")
    os.makedirs(lib, exist_ok=True)
    _write_note(os.path.join(td, "outside"), body=POISON)
    monkeypatch.delenv(IN_ENV, raising=False)
    monkeypatch.setenv("MDCG_ROOT", lib)
    eng = _engine(td)

    for name, root in (("真源父目录", os.path.join(lib, "..")),
                       ("真源兄弟目录", os.path.join(lib, "..", "outside"))):
        r = eng.wm_simloop("load", {"mdcg_root": root})
        assert r["status"] == "error", "E %s 未被拒：%r" % (name, r,)
        assert "#744" in r.get("error", ""), "E %s 拒绝理由未点名 #744：%r" % (name, r,)
    assert _cats(eng) == [], "E 被拒后仍注入实体：%r" % (_cats(eng),)


def test_f_空值不设闸(monkeypatch):
    """F：防过修——不传 / `""` / `None` 的 `mdcg_root` 不设闸（回落配置面）。"""
    td = tempfile.mkdtemp()
    lib = os.path.join(td, "mdcg_lib")
    _write_note(lib)
    monkeypatch.delenv(IN_ENV, raising=False)
    monkeypatch.setenv("MDCG_ROOT", lib)
    eng = _engine(td)

    for params in ({}, {"mdcg_root": ""}, {"mdcg_root": None}):
        r = eng.wm_simloop("load", params)
        assert r["status"] == "ok", "F 空值被误闸（过修）：%r / %r" % (params, r,)
        assert r["load"]["seeded"] == 1, "F 空值未回落配置面：%r / %r" % (params, r["load"],)

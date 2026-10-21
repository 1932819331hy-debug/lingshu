# -*- coding: utf-8 -*-
"""#464 wm_simloop WAL 续号在「崩溃残行 / 超长末条 / 非对象末行」上回落 `seq=0` —— 守卫

缺陷（lingshu issue #464 · [core/wm_simloop]）：
  `SimulationLoop._resume_seq_from_wal`（`lingshu/world/wm_simloop.py`）旧实现只取
  尾部 64KB 块的**最后一行**做 `json.loads`，失败即 `return 0`；而 `_wal` 直接
  `open(self.wal_path, "a")` 追加、**不检查文件是否以换行结尾**。三种现实形态据此出事：
    · A **崩溃残行**：上次进程追加末条时中断 ⇒ 末行是半截 JSON ⇒ 续号回落 0，同一
      文件内 seq 重复；且新记录**粘在残行上**、整行不可解析（审计轨迹丢一条）。
    · B **超长末条**：单条 > 64KB ⇒ 反向只读 64KB 时读到半行 ⇒ 续号回落 0。
    · C **非对象末行**：末行是合法 JSON 但不是对象（如 `[]`）⇒ `last.get(...)` 抛
      `AttributeError`，而它**不在**捕获列表 `(OSError, ValueError, TypeError)` 里
      ⇒ 构造期直接崩（修复前构造不读文件，故这是 #248 的 `1cfe147` 新引入的）。
  三种形态都使同文件头 `:24-25` 声明的「WAL（append-only 单调）· `seq` 在**单个 WAL
  文件内**严格单调」不成立。判据来源：公开 issue #464 正文 ＋ `wm_simloop.py:25`
  的该声明（本守卫据此断言，不引入新阈值）。

修法（取向唯一：修在续号函数本身）：
  ① 续号取值来源改为「**最后一条可用记录**」——可 `json.loads`、解析结果是 `dict`、
     `seq` 是整数；块内找不到就**继续向前分块回溯**（不整文件读入）；
  ② `_wal` 追加前若末尾不以 `\\n` 结尾则**先补一个换行**（消残行粘连面）；
  ③ 非对象末行由 `isinstance(parsed, dict)` 类型校验跳过（**不**放宽为
     `except Exception`，#170 口径）；
  ④ `return 0` 的语义收窄为「整个文件无可用记录」（空文件语义仍合法）。

断言组（抽掉修复关键一行 ⇒ 红）：
  A 残行末行 ⇒ 续号回退到上一条可用记录（旧缺陷＝0）
  B 残行后追加的新记录**独立成行**、文件里没有粘连行（每行都可 `json.loads`）
  C 超长末条（> 64KB）⇒ 继续向前分块回溯到可用记录（旧缺陷＝0）
  D 非对象末行 ⇒ 构造**不抛**且续号跳过该行（旧缺陷＝`AttributeError`）
  E 正对照：正常续写（末行完整）⇒ 续号正确、文件逐行可解析
  F 正对照：空文件 / 不存在文件 ⇒ `_seq == 0`（锁住「回 0 语义仍合法」，防过修）
  G 正对照：文件里只有残行、无任何可用记录 ⇒ `_seq == 0`（同上，防过修）
  H 确定性：同配置两次运行、各自独立新文件 ⇒ WAL **逐字节相同**（本修不得破坏
    既有性质；`_wal` 正常路径不得多写任何字节）

运行（仓根）：python -X utf8 -m pytest tests/test_issue464_wal_resume_torn_tail_guard.py -q --no-header
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.world.wm_simloop import SimulationLoop  # noqa: E402

TORN = '{"seq": 3, "ts": 179'      # 20 字节残行（末条只写了一半）
BIG_BLOB = 70000                   # B 形态：末条 JSON 的 payload 体积（> 64KB）


def _write_records(path, seqs, big_last=False):
    """写「完整行」WAL 文件（文本模式，与真实 `_wal` 同口径，末尾带换行）。"""
    with open(path, "w", encoding="utf-8") as f:
        for i, s in enumerate(seqs):
            rec = {"seq": s, "ts": 1.0, "tick": s, "kind": "episode",
                   "payload": {"n": s}}
            if big_last and i == len(seqs) - 1:
                rec["payload"]["blob"] = "x" * BIG_BLOB
            f.write(json.dumps(rec) + "\n")


def _read_rows(path):
    """按行读回：可解析的行给 dict，不可解析的行给 '<TORN>'。"""
    with open(path, "rb") as f:
        raw = f.read()
    rows = []
    for ln in raw.split(b"\n"):
        s = ln.strip()
        if not s:
            continue
        try:
            rows.append(json.loads(s.decode("utf-8", errors="ignore")))
        except ValueError:
            rows.append("<TORN>")
    return rows


def _seqs(rows):
    return [r["seq"] if isinstance(r, dict) else r for r in rows]


def _make_loop(wal):
    """构造推演循环（假时钟 ⇒ 与时间无关、确定性）。"""
    return SimulationLoop(size=12, seed=1, wal_path=wal, clock=lambda: 0.0)


def _step(loop, n):
    loop.scene.create_scene(trees=0, water=False)
    loop.scene.add_entity("walker", behavior="wander", pos=(6, 1.5, 13),
                          speed=0.3)
    loop.wm.perceive(observations=loop._observe())
    loop.step(n=n)


# ---------- A / B：崩溃残行 ----------

def test_a_torn_tail_resumes_previous_usable_seq(tmp_path):
    """A：末行是半截 JSON ⇒ 续号回退到上一条可用记录（旧缺陷＝0）。"""
    wal = str(tmp_path / "torn.jsonl")
    _write_records(wal, [0, 1, 2])
    with open(wal, "a", encoding="utf-8") as f:
        f.write(TORN)                     # 残行：不以换行结尾
    loop = _make_loop(wal)
    assert loop._seq == 3, (
        "残行末行使续号回落（旧缺陷）：_seq=%r，期望 3（上一条可用记录 seq=2）"
        % (loop._seq,))


def test_b_no_glued_line_after_resume(tmp_path):
    """B：残行后追加的新记录独立成行——文件里没有粘连行（每行都可解析）。"""
    wal = str(tmp_path / "torn2.jsonl")
    _write_records(wal, [0, 1, 2])
    with open(wal, "a", encoding="utf-8") as f:
        f.write(TORN)
    loop = _make_loop(wal)
    _step(loop, 3)
    rows = _read_rows(wal)
    seqs = _seqs(rows)
    torn = [i for i, r in enumerate(rows) if r == "<TORN>"]
    assert torn == [3], (
        "残行之外还出现了不可解析行（新记录粘在残行上）：%r" % (seqs,))
    assert seqs[:3] == [0, 1, 2] and seqs[3] == "<TORN>", (
        "残行位置/前缀不对：%r" % (seqs,))
    tail = seqs[4:]
    assert tail and tail[0] == 3, (
        "残行后未接着编号（旧缺陷＝从 0 重来）：%r" % (seqs,))
    assert all(b == a + 1 for a, b in zip(tail, tail[1:])), (
        "残行后 seq 不严格递增：%r" % (seqs,))


# ---------- C：超长末条 ----------

def test_c_oversized_last_record_backtracks(tmp_path):
    """C：末条 > 64KB ⇒ 首块只覆盖半行 ⇒ 继续向前分块回溯（旧缺陷＝0）。"""
    wal = str(tmp_path / "big.jsonl")
    _write_records(wal, [0, 1], big_last=True)
    assert os.path.getsize(wal) > 65536, "构造失败：末条未超 64KB"
    loop = _make_loop(wal)
    assert loop._seq == 2, (
        "超长末条使续号回落（旧缺陷）：_seq=%r，期望 2（末条 seq=1 + 1）"
        % (loop._seq,))


# ---------- D：非对象末行 ----------

def test_d_non_object_last_line_does_not_raise(tmp_path):
    """D：末行是 `[]` ⇒ 构造不抛、续号跳过该行（旧缺陷＝`AttributeError`）。"""
    wal = str(tmp_path / "nonobj.jsonl")
    with open(wal, "w", encoding="utf-8") as f:
        f.write("[]\n")
    loop = _make_loop(wal)               # 旧实现在此行抛 AttributeError
    assert loop._seq == 0, (
        "文件里没有任何可用记录时 _seq 应为 0：%r" % (loop._seq,))


def test_d2_non_object_tail_after_records(tmp_path):
    """D2：`[{seq:0},{seq:1},[]]` ⇒ 跳过非对象行，回退到上一条可用记录。"""
    wal = str(tmp_path / "nonobj2.jsonl")
    _write_records(wal, [0, 1])
    with open(wal, "a", encoding="utf-8") as f:
        f.write("[]\n")
    loop = _make_loop(wal)
    assert loop._seq == 2, (
        "非对象末行应被跳过而非回落 0：_seq=%r，期望 2" % (loop._seq,))


def test_d3_object_tail_without_usable_seq(tmp_path):
    """D3：末行是**合法 JSON 对象**但 `seq` 不可用（缺键 / 非整数）⇒ 跳过它、
    回退到上一条可用记录（不是回落 0）。

    这一格锁的是判据里的 `isinstance(seq, int)`：抽掉该类型校验后，末行的
    `seq` 会是 `None`（缺键）或 `"7"`（字符串），`seq + 1` 抛 TypeError 被外层
    收窄的 `except` 吞掉 ⇒ 回落 **0**（同文件 seq 重复）。三种非整数形态逐一体测。
    """
    for label, tail in (("缺 seq 键", {"ts": 2.0, "tick": 2, "kind": "episode"}),
                        ("seq 是字符串", {"seq": "7", "ts": 2.0, "tick": 2}),
                        ("seq 是布尔", {"seq": True, "ts": 2.0, "tick": 2}),
                        ("seq 是浮点", {"seq": 3.0, "ts": 2.0, "tick": 2})):
        wal = str(tmp_path / ("noseq_%s.jsonl" % abs(hash(label))))
        _write_records(wal, [0, 1])
        with open(wal, "a", encoding="utf-8") as f:
            f.write(json.dumps(tail) + "\n")
        loop = _make_loop(wal)
        assert loop._seq == 2, (
            "%s 的末行应被跳过、回退到上一条可用记录（_seq=2）：实得 %r"
            % (label, loop._seq))


# ---------- E / F / G：正对照 ----------

def test_e_control_normal_resume(tmp_path):
    """E（正对照）：末行完整 ⇒ 续号正确、文件逐行可解析（正常路径不得变）。"""
    wal = str(tmp_path / "control.jsonl")
    _write_records(wal, [0, 1, 2, 3])
    loop = _make_loop(wal)
    assert loop._seq == 4, "正常续号读错：_seq=%r" % (loop._seq,)
    _step(loop, 3)
    rows = _read_rows(wal)
    assert all(isinstance(r, dict) for r in rows), (
        "正常路径出现不可解析行：%r" % (_seqs(rows),))
    seqs = _seqs(rows)
    assert seqs[:4] == [0, 1, 2, 3], "原有记录被改写：%r" % (seqs,)
    tail = seqs[4:]
    assert tail and tail[0] == 4, "正常续写未接着编号：%r" % (seqs,)
    assert all(b == a + 1 for a, b in zip(tail, tail[1:])), (
        "正常续写 seq 不严格递增：%r" % (seqs,))


def test_f_empty_or_missing_file_is_zero(tmp_path):
    """F（正对照）：空文件 / 不存在文件 ⇒ `_seq == 0`（「回 0」语义仍合法，防过修）。"""
    empty = str(tmp_path / "empty.jsonl")
    open(empty, "w", encoding="utf-8").close()
    assert _make_loop(empty)._seq == 0, "空文件应回 0"
    missing = str(tmp_path / "missing.jsonl")
    assert not os.path.exists(missing)
    assert _make_loop(missing)._seq == 0, "不存在文件应回 0"


def test_g_only_torn_lines_is_zero(tmp_path):
    """G（正对照）：文件里只有残行、无任何可用记录 ⇒ `_seq == 0`（同上）。"""
    wal = str(tmp_path / "only_torn.jsonl")
    with open(wal, "w", encoding="utf-8") as f:
        f.write(TORN)
    assert _make_loop(wal)._seq == 0, "整文件无可用记录应回 0"


# ---------- H：确定性（本修不得破坏既有性质）----------

def test_h_same_config_two_runs_byte_identical(tmp_path):
    """H：同配置两次运行、各自独立新文件 ⇒ WAL 逐字节相同。

    存在理由：`wm_simloop.py` 的 `_alias` 注释刻意声明「同配置两次运行 WAL 逐字节
    一致」是本仓保住的性质；本修在 `_wal` 里加了「末尾补换行」的检查，正常路径
    **不得**多写任何字节，否则该性质当场作废。假时钟消除 ts 抖动。
    """
    hashes = []
    for tag in ("a", "b"):
        wal = str(tmp_path / ("byte_%s.jsonl" % tag))
        loop = _make_loop(wal)
        _step(loop, 3)
        with open(wal, "rb") as f:
            hashes.append(hashlib.sha256(f.read()).hexdigest())
    assert hashes[0] == hashes[1], (
        "同配置两次运行 WAL 不逐字节相同（确定性被破坏）")

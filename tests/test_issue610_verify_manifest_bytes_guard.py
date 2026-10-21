#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_issue610_verify_manifest_bytes_guard.py —— #610 的守卫断言集。

用途
    守护 `tools/verify_manifest.py` 的 bytes 关卡（#610）：登记了 `bytes` 的条目，
    实算（LF 归一后）差值非零时不得再无声 `MATCH`。修复前 bytes_delta 被算出来、
    与 recorded/actual 并排渲染进输出，却对 status 与退出码**零参与**——
    `bytes=0`（谎报空文件）或 `bytes=10**12`（荒谬值）在 sha 正确时仍
    `MATCH`、退出 0（`--strict` 也 0）。修复后：status 判 `BYTES_MISMATCH`
    （reason `bytes-delta%+d`）、计入 `bytes_mismatch` 计数、退出码 1
    （默认与 `--strict` 都非 0）。

分组
    G1 谎报必红：sha 正确 × recorded_bytes ∈ {0, +100, 10**12}
       ⇒ BYTES_MISMATCH / bytes_mismatch=1 / 退出 1（默认与 strict）。
    G2 防过修（正确登记）：recorded_bytes == 实算值 ⇒ MATCH / bytes_mismatch=0 /
       退出 0（默认与 strict）。
    G3 缺 recorded_bytes（None）：行为与改前一致 ⇒ MATCH / delta=None /
       bytes_mismatch=0 / 退出 0。
    G4 优先级：sha 不符 + bytes 谎报 ⇒ status 仍是 MISMATCH（sha 判定与
       优先级不变，#610 只在 sha 之后追加关卡）；纯 sha 不符（无 bytes 登记）
       宽松模式退出 0 的既有语义不变。
    G5 混合批与文本口径：一真一谎 ⇒ match=1 bytes_mismatch=1 退出 1；
       SUMMARY 行含 `bytes_mismatch=1`（计数不再只藏在 JSON 里）。

变异哨兵（两向）
    把 classify 的 bytes 关卡回退（判定只看 sha）⇒ G1/G5 必红（旧读数：
    MATCH / 退出 0）；恢复 ⇒ 全绿。G2/G3 是防过修反哨兵：关卡误伤正确或
    缺席登记时它们必红。

用法
    python -X utf8 -m pytest tests/test_issue610_verify_manifest_bytes_guard.py -q

边界（如实）
    - 所有用例 subprocess 跑真工具 + `--repo-root` 指向 tmp_path 假仓库
      （结构对齐 tests/test_manifest_integrity.py 的 `_fake_repo`）。
    - 被登记文件一律 `write_bytes` 落 LF（Windows 文本模式会写 CRLF，
      破坏 sha 前缀正例）；bytes 口径 = LF 归一后字节数（与工具同一口径）。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL = REPO_ROOT / "tools" / "verify_manifest.py"

BLOB = b"lingshu #610 bytes guard\n" * 3
SHA64 = hashlib.sha256(BLOB).hexdigest()
ACTUAL = len(BLOB)


def _run_tool(root, extra=()):
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(TOOL),
         "--repo-root", str(root), *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc


def _fake_repo(tmp_path, entries, files=None):
    """entries: [(file, sha256, bytes_or_None)]；files: [(relpath, blob)]（缺省单文件）。"""
    if files is None:
        files = [("data.bin", BLOB)]
    root = tmp_path / "repo"
    mdir = root / "docs" / "intake" / "x"
    mdir.mkdir(parents=True)
    items = []
    for name, sha, size in entries:
        item = {"file": name, "sha256": sha}
        if size is not None:
            item["bytes"] = size
        items.append(item)
    (mdir / "_export_manifest.json").write_bytes(
        json.dumps({"tests": items}, ensure_ascii=False).encode("utf-8"))
    for relpath, blob in files:
        target = root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
    return root


def _json_of(proc):
    payload = json.loads(proc.stdout)
    details = {d["path"]: d for d in payload["manifests"][0]["details"]}
    return payload, details


# ---------------------------------------------------------------- G1：谎报必红

def test_g1_lying_recorded_bytes_is_not_silent_match(tmp_path):
    """sha 正确 × bytes ∈ {0, +100, 10**12} ⇒ BYTES_MISMATCH、bytes_mismatch=1、退出 1。

    修复前读数：三种取值全部 status=MATCH、exit 0（--strict 也 0）——#610 题面。
    """
    for lying in (0, ACTUAL + 100, 10 ** 12):
        root = _fake_repo(tmp_path / ("lie-%s" % lying),
                          [("data.bin", SHA64, lying)])
        result = _run_tool(root, ["--json"])
        assert result.returncode == 1, (lying, result.stdout + result.stderr)
        payload, details = _json_of(result)
        detail = details["data.bin"]
        # 不再是无声 MATCH：sha 对了，bytes 谎报必须被点名
        assert detail["status"] == "BYTES_MISMATCH", (lying, detail)
        assert detail["reason"] == "bytes-delta%+d" % detail["bytes_delta"], detail
        assert detail["bytes_delta"] == ACTUAL - lying, detail
        assert payload["summary"]["bytes_mismatch"] == 1, payload["summary"]
        assert payload["summary"]["match"] == 0, payload["summary"]
        assert payload["summary"]["mismatch"] == 0, payload["summary"]
        assert payload["exit_code"] == 1
        # strict 也非 0（修复前 strict 同样退 0）
        strict = _run_tool(root, ["--json", "--strict"])
        assert strict.returncode == 1, (lying, strict.stdout + strict.stderr)


# ---------------------------------------------------------------- G2：防过修（正确登记）

def test_g2_exact_recorded_bytes_still_matches_and_exits_zero(tmp_path):
    """recorded_bytes == 实算值 ⇒ MATCH / bytes_mismatch=0 / 退出 0（默认与 strict）。"""
    root = _fake_repo(tmp_path / "exact", [("data.bin", SHA64, ACTUAL)])
    for extra in ((), ("--strict",)):
        result = _run_tool(root, ["--json", *extra])
        assert result.returncode == 0, (extra, result.stdout + result.stderr)
        payload, details = _json_of(result)
        assert details["data.bin"]["status"] == "MATCH", details
        assert details["data.bin"]["bytes_delta"] == 0, details
        assert payload["summary"]["bytes_mismatch"] == 0, payload["summary"]
        assert payload["summary"]["match"] == 1, payload["summary"]
        assert payload["exit_code"] == 0


# ---------------------------------------------------------------- G3：缺 recorded_bytes

def test_g3_absent_recorded_bytes_keeps_pre_fix_behavior(tmp_path):
    """没登记 `bytes`（None）⇒ 行为与改前一致：MATCH、delta=None、退出 0。

    真仓 v0.1 形态 55 条全部没登记 bytes——这条保证修复对它们零影响。
    """
    root = _fake_repo(tmp_path / "absent", [("data.bin", SHA64, None)])
    for extra in ((), ("--strict",)):
        result = _run_tool(root, ["--json", *extra])
        assert result.returncode == 0, (extra, result.stdout + result.stderr)
        payload, details = _json_of(result)
        detail = details["data.bin"]
        assert detail["status"] == "MATCH", detail
        assert detail["recorded_bytes"] is None, detail
        assert detail["bytes_delta"] is None, detail
        assert payload["summary"]["bytes_mismatch"] == 0, payload["summary"]
        assert payload["summary"]["match"] == 1, payload["summary"]


# ---------------------------------------------------------------- G4：优先级（sha 判定本身不改）

def test_g4_sha_mismatch_keeps_status_and_precedence(tmp_path):
    """sha 不符 ⇒ status 仍 MISMATCH（优先级不变）；但其 bytes 谎报仍计入计数。

    纯 sha 分歧（无 bytes 登记）宽松退 0 / strict 退 3 的既有语义不变。
    """
    wrong_sha = "0" * 64
    # sha 不符 + bytes 谎报：status 是 MISMATCH 不是 BYTES_MISMATCH（优先级在前），
    # 但 bytes 谎报照样计入 bytes_mismatch（跨状态计数）⇒ 退出 1。
    both_bad = _fake_repo(tmp_path / "both-bad",
                          [("data.bin", wrong_sha, ACTUAL + 7)])
    result = _run_tool(both_bad, ["--json"])
    assert result.returncode == 1, result.stdout + result.stderr
    payload, details = _json_of(result)
    assert details["data.bin"]["status"] == "MISMATCH", details
    assert payload["summary"]["mismatch"] == 1, payload["summary"]
    assert payload["summary"]["bytes_mismatch"] == 1, payload["summary"]

    # 纯 sha 分歧（无 bytes 登记）：宽松退 0 / strict 退 3 的既有语义不变
    sha_only = _fake_repo(tmp_path / "sha-only",
                          [("data.bin", wrong_sha, None)])
    lenient = _run_tool(sha_only, ["--json"])
    assert lenient.returncode == 0, lenient.stdout + lenient.stderr
    strict = _run_tool(sha_only, ["--json", "--strict"])
    assert strict.returncode == 3, strict.stdout + strict.stderr


# ---------------------------------------------------------------- G5：混合批与文本口径

def test_g5_mixed_batch_counts_and_text_summary_line(tmp_path):
    """一真一谎 ⇒ match=1 bytes_mismatch=1、退出 1；文本 SUMMARY 行带 bytes_mismatch=1。"""
    root = _fake_repo(
        tmp_path / "mixed",
        [("good.bin", SHA64, ACTUAL), ("bad.bin", SHA64, 0)],
        files=[("good.bin", BLOB), ("bad.bin", BLOB)])
    result = _run_tool(root, ["--json"])
    assert result.returncode == 1, result.stdout + result.stderr
    payload, details = _json_of(result)
    assert details["good.bin"]["status"] == "MATCH", details["good.bin"]
    assert details["bad.bin"]["status"] == "BYTES_MISMATCH", details["bad.bin"]
    assert payload["summary"]["match"] == 1, payload["summary"]
    assert payload["summary"]["bytes_mismatch"] == 1, payload["summary"]
    assert payload["summary"]["mismatch"] == 0, payload["summary"]

    text = _run_tool(root)  # 文本模式：计数必须进 SUMMARY / 清单小节，不只藏在 JSON
    assert text.returncode == 1, text.stdout + text.stderr
    assert "bytes_mismatch=1" in text.stdout, text.stdout
    assert "SUMMARY manifests=1 entries=2 match=1 mismatch=0 bytes_mismatch=1" in text.stdout
    # 病灶行（非零差值）必须在 bytes 样例里置顶，供人一眼定位
    bad_line = [ln for ln in text.stdout.splitlines() if "bad.bin" in ln]
    assert bad_line and "delta=%d" % ACTUAL in bad_line[0], text.stdout


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-q"]))

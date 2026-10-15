# -*- coding: utf-8 -*-
"""test_issue488_rust_exe_cwd_guard · rust_bridge 不得按调用时 cwd 解析/执行 hex_nn.exe
================================================================================
缺陷（#488，P0 安全）：#10 的修复（df6e629）把 `hex_nn.exe` 的缺省候选从占位符
换成了**相对路径** `hex_nn/target/release/hex_nn.exe`，且该值在 **import 时**由
`HEX_EXE = _locate_hex_exe()` 固定、`run_rust_train` 直接 `subprocess.run([str(HEX_EXE)…])`。
后果三条：

  ①相对路径按**调用时 cwd** 解析 ⇒ 进程只要在不可信目录里调用 rust_bridge 的
    公开函数（run_rust_train / equivalence_check / equivalence_par /
    determinism_check / bench_threads / bench_rust），该目录下的同名文件
    `hex_nn/target/release/hex_nn.exe` 就会被 subprocess 直接执行（任意代码执行）；
  ②路径在 import 时固定，调用时不再校验；
  ③子进程继承**完整** `os.environ`（含 `AEIS_DESIGNER_KEY` 等凭据），
    被劫持的进程能读到全部凭据（#350 的透传面）。

修法（本仓 `lingshu/nn/rust_bridge.py`）：
  · 缺省锚定**包根**：`Path(__file__).resolve().parents[2]/hex_nn/target/release/hex_nn.exe`
    （绝对路径，绝不回退相对路径）；
  · **调用时** `resolve_hex_exe()` 再解析并校验 `is_absolute()` + `is_file()`，
    不满足即 fail-closed（抛 `HexExeError`，不静默回退）；
  · 子进程只传**最小环境** `_minimal_subprocess_env()`：剔除 `AEIS_*` 前缀与
    凭据形状名（`*_KEY` / `*_TOKEN` / `*_SECRET` / `*_PASSWORD` …）。
  既有显式配置面（`AEIS_HEX_NN` / `AEIS_HEX_NN_DIR`）行为保持不变。

判据（本件）：
  G1 默认状态（未设 AEIS_HEX_NN/AEIS_HEX_NN_DIR）`HEX_EXE` 是**绝对路径**且锚定包根；
  G2（核心）在「不可信 cwd」里放同名 hex_nn.exe，调用公开函数**不得**执行它
     （攻击者 marker 不生成）；
  G3 显式 `AEIS_HEX_NN` 指向存在文件 ⇒ 照旧使用（防把功能修死）；
  G4 `AEIS_HEX_NN` 指向不存在文件 / 非绝对路径 ⇒ fail-closed 抛错（不静默回退缺省）；
  G5 子进程 env 不含凭据形状名（收窄面生效），且保留 PATH 等必需变量。

运行（仓根）：python -X utf8 -m pytest tests/test_issue488_rust_exe_cwd_guard.py -q
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from lingshu.nn import rust_bridge as rb  # noqa: E402

#: 缺省应锚定的包根绝对路径。
_EXPECTED_DEFAULT = REPO / "hex_nn" / "target" / "release" / "hex_nn.exe"


def _norm(p) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def _clean_env(**extra) -> dict:
    env = dict(os.environ)
    env.pop("AEIS_HEX_NN", None)
    env.pop("AEIS_HEX_NN_DIR", None)
    env["PYTHONUTF8"] = "1"
    env["PYTHONPATH"] = str(REPO)
    env.update(extra)
    return env


def _import_snippet(env: dict) -> dict:
    """子进程里 import rust_bridge，回读 import 时的 HEX_EXE（隔离 import 时快照）。"""
    code = (
        "import json\n"
        "from lingshu.nn import rust_bridge as RB\n"
        "print(json.dumps({'hex_exe': str(RB.HEX_EXE),"
        " 'is_abs': RB.HEX_EXE.is_absolute()}))\n")
    p = subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env,
                       cwd=tempfile.gettempdir())  # #455
    assert p.returncode == 0, "子进程 import rust_bridge 失败：\n%s" % p.stderr[-2000:]
    return json.loads(p.stdout.strip().splitlines()[-1])


def _make_fake_exe(evil_root: Path, marker: Path) -> None:
    """在 evil_root 下造出「相对路径候选」位置的同名可执行文件 + 载荷。
    若被当 hex_nn.exe 执行，会写出 marker（Windows 用 python 解释器副本，
    POSIX 用带可执行位的 shell 脚本）。"""
    rel = evil_root / "hex_nn" / "target" / "release"
    rel.mkdir(parents=True, exist_ok=True)
    exe = rel / "hex_nn.exe"
    if os.name == "nt":
        shutil.copy2(sys.executable, exe)          # 真 PE：解释器副本
        for mode in ("train", "train_par"):
            (evil_root / mode).write_text(
                "open(r'%s','w').write('attacker ran')\n" % marker,
                encoding="utf-8")
    else:
        exe.write_text("#!/bin/sh\nprintf 'attacker ran' > '%s'\n" % marker,
                       encoding="utf-8")
        exe.chmod(0o755)


# ---------------------------------------------------------------- G1

def test_g1_default_hex_exe_is_absolute_and_package_root_anchored():
    """G1：默认状态 HEX_EXE 必须是绝对路径且锚定包根（#488）。"""
    got = _import_snippet(_clean_env())
    assert got["is_abs"] is True, (
        "#488：默认 HEX_EXE 必须是绝对路径，实测 %r（is_absolute=%s）——"
        "相对路径会按调用时 cwd 解析" % (got["hex_exe"], got["is_abs"]))
    assert _norm(got["hex_exe"]) == _norm(_EXPECTED_DEFAULT), (
        "#488：默认 HEX_EXE 必须锚定包根 %s，实测 %s"
        % (_EXPECTED_DEFAULT, got["hex_exe"]))


# ---------------------------------------------------------------- G2（核心）

def test_g2_untrusted_cwd_same_named_file_not_executed(tmp_path, monkeypatch):
    """G2（核心）：不可信 cwd 下的同名 hex_nn.exe 不得被调用公开函数执行。"""
    monkeypatch.delenv("AEIS_HEX_NN", raising=False)
    monkeypatch.delenv("AEIS_HEX_NN_DIR", raising=False)
    evil = tmp_path / "untrusted"
    evil.mkdir()
    marker = tmp_path / "PWNED_marker"
    _make_fake_exe(evil, marker)
    monkeypatch.chdir(evil)

    lat = np.zeros((4, 3, 4))
    obj = np.zeros(4, dtype=np.int64)
    shp = np.zeros(4, dtype=np.int64)
    # 缺省仓内无 hex_nn.exe ⇒ resolve_hex_exe() fail-closed 抛错；
    # 若部署方真构建了 exe 则正常执行——两种情形都不得碰 cwd 下的同名文件。
    try:
        rb.bench_rust(lat, obj, shp, np.zeros(375), steps=2, batch=32, samples=3)
    except Exception:
        pass
    assert not marker.exists(), (
        "#488：不可信 cwd 下的同名 hex_nn.exe 被当成真 exe 执行了（marker=%s）"
        % marker)


# ---------------------------------------------------------------- G3

def test_g3_explicit_existing_aeis_hex_nn_still_used(tmp_path, monkeypatch):
    """G3：显式 AEIS_HEX_NN 指向存在文件 ⇒ 照旧使用（防把功能修死）。"""
    fake = tmp_path / "hex_nn.exe"
    fake.write_bytes(b"MZ-fake")
    monkeypatch.setenv("AEIS_HEX_NN", str(fake))
    monkeypatch.delenv("AEIS_HEX_NN_DIR", raising=False)

    assert _norm(rb.resolve_hex_exe()) == _norm(fake), (
        "AEIS_HEX_NN 指向存在文件时 resolve_hex_exe() 没返回它（功能被修死）")
    assert _norm(rb._locate_hex_exe()) == _norm(fake), (
        "AEIS_HEX_NN 指向存在文件时 _locate_hex_exe()（import 快照）没返回它")


def test_g3b_explicit_dir_variant_still_used(tmp_path, monkeypatch):
    """G3b：AEIS_HEX_NN_DIR/hex_nn.exe 变体照旧使用。"""
    d = tmp_path / "hexdir"
    d.mkdir()
    fake = d / "hex_nn.exe"
    fake.write_bytes(b"MZ-fake")
    monkeypatch.delenv("AEIS_HEX_NN", raising=False)
    monkeypatch.setenv("AEIS_HEX_NN_DIR", str(d))
    assert _norm(rb.resolve_hex_exe()) == _norm(fake)


# ---------------------------------------------------------------- G4

def test_g4_explicit_missing_file_fails_closed(tmp_path, monkeypatch):
    """G4：AEIS_HEX_NN 指向不存在文件 ⇒ fail-closed 抛错，不静默回退缺省。"""
    monkeypatch.setenv("AEIS_HEX_NN", str(tmp_path / "does_not_exist.exe"))
    monkeypatch.delenv("AEIS_HEX_NN_DIR", raising=False)
    with pytest.raises(rb.HexExeError):
        rb.resolve_hex_exe()


def test_g4b_explicit_relative_path_fails_closed(monkeypatch):
    """G4b：显式配置为相对路径 ⇒ fail-closed（不得按 cwd 解析）。"""
    monkeypatch.setenv("AEIS_HEX_NN", "hex_nn/target/release/hex_nn.exe")
    monkeypatch.delenv("AEIS_HEX_NN_DIR", raising=False)
    with pytest.raises(rb.HexExeError):
        rb.resolve_hex_exe()


# ---------------------------------------------------------------- G5

def test_g5_subprocess_env_excludes_credentials(monkeypatch):
    """G5：子进程最小环境剔除 AEIS_* 与凭据形状名，保留 PATH。"""
    planted = {
        "AEIS_DESIGNER_KEY": "designer-secret-123",
        "AEIS_HEX_NN_DIR": "/somewhere",
        "HIVE_API_KEY": "k",
        "FOO_TOKEN": "t",
        "BAR_SECRET": "s",
        "BAZ_PASSWORD": "p",
    }
    for k, v in planted.items():
        monkeypatch.setenv(k, v)

    env = rb._minimal_subprocess_env()
    leaked = sorted(k for k in planted if k in env)
    assert leaked == [], "#488：子进程环境泄漏了敏感/凭据形状变量：%s" % leaked
    if "PATH" in os.environ:
        assert env.get("PATH") == os.environ["PATH"], "PATH 等必需变量必须保留"


def test_g5b_subprocess_env_keeps_explicit_extras(monkeypatch):
    """G5b：显式 extra（RAYON_NUM_THREADS / HEX_LR）在收窄后仍叠加。"""
    monkeypatch.setenv("AEIS_DESIGNER_KEY", "x")
    env = rb._minimal_subprocess_env({"RAYON_NUM_THREADS": "4", "HEX_LR": "0.2"})
    assert env["RAYON_NUM_THREADS"] == "4"
    assert env["HEX_LR"] == "0.2"
    assert "AEIS_DESIGNER_KEY" not in env


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "--no-header"]))

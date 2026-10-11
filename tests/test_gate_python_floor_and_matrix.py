# -*- coding: utf-8 -*-
"""test_gate_python_floor_and_matrix · `requires-python` 与 extras 下界一致性 + 矩阵覆盖（#209）
============================================================================
背景（#209，成立）：
    `pyproject.toml` 曾声明 `requires-python = ">=3.10"`，而其 extras 的下界
    `numpy>=2.3.5` 在 PyPI 上 `requires_python >= 3.11`（2.5.3 更是 `>= 3.12`）
    ⇒ 在 3.10 上装 `.[world]` 必然解析失败。声明面自相矛盾。
    且 `.github/workflows/gate.yml` 的矩阵只跑 3.12 / 3.13，从未覆盖 3.10/3.11，
    也从未把依赖钉到 extras 声明的**下界**（默认装区间内最新）。

判据（本件，三条）：
    G1 一致性：`requires-python` 的下界不得低于 extras 里 numpy 下界版本在 PyPI 上
       要求的 Python 下界（实测表见下）。3.10 < 3.11 ⇒ 旧声明判红。
    G2 矩阵覆盖下界 Python：gate.yml 矩阵必须有一档 `python-version` == requires-python
        下界（且为阻断档），使声明的下界**真被 CI 跑**。
    G3 矩阵覆盖依赖下界：矩阵必须有一档把 numpy 钉到 extras 声明的下界
        （`pinned_deps` 含 `numpy==<下界>`），使「下界组合真能装且跑通」被覆盖。

判据来源：
    · numpy 的 requires_python 实测 —— 2026-10-11 拉 `pypi.org/pypi/numpy/<v>/json`
      的 `info.requires_python`（2.2.6→>=3.10 · 2.3.5→>=3.11 · 2.4.6→>=3.11 · 2.5.3→>=3.12）。
      表 `_NUMPY_REQUIRES_PYTHON` 即该实测的固化；numpy 下界版本不在表内 ⇒ **判红**
      （fail-closed：换了未测版本必须先补实测，不得静默放行）。
    · 矩阵形状的判据来源＝经验标定（本仓 README「已测版本矩阵」＋ gate.yml 既有 3.12 阻断档）。

量具自检（同族纪律）：解析器必须真能读出 `requires-python` 与矩阵档——
否则「没读到 ⇒ 跳过 ⇒ 恒绿」会成为坏解析器掩盖的假绿。

运行：pytest tests/test_gate_python_floor_and_matrix.py
"""
from __future__ import annotations

import os
import re
import sys
import tomllib

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYPROJECT = os.path.join(REPO, "pyproject.toml")
GATE_YML = os.path.join(REPO, ".github", "workflows", "gate.yml")

#: numpy 各版本在 PyPI 上声明的 Python 下界（实测，2026-10-11）。
_NUMPY_REQUIRES_PYTHON = {
    "2.2.6": (3, 10),
    "2.3.5": (3, 11),
    "2.4.6": (3, 11),
    "2.5.3": (3, 12),
}

_PY_MINOR_RE = re.compile(r">=\s*(\d+)\.(\d+)")
_NUMPY_LOWER_RE = re.compile(r"numpy\s*>=\s*(\d+\.\d+\.\d+)")


def _floor_of(spec):
    """从 `>=3.11` 这类串取 (major, minor) 下界；取不到返回 None。"""
    m = _PY_MINOR_RE.search(spec or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def _load_pyproject():
    with open(PYPROJECT, "rb") as fh:
        return tomllib.load(fh)


def _numpy_lower_bounds(proj):
    """extras 里所有 `numpy>=X.Y.Z` 的下界版本（去重、保序）。"""
    seen, out = set(), []
    for _extra, items in ((proj.get("project") or {}).get("optional-dependencies") or {}).items():
        for spec in items or []:
            m = _NUMPY_LOWER_RE.search(spec)
            if m and m.group(1) not in seen:
                seen.add(m.group(1))
                out.append(m.group(1))
    return out


def _matrix(gate):
    return gate["jobs"]["gate"]["strategy"]["matrix"]["include"]


def _load_gate():
    with open(GATE_YML, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ---------------------------------------------------------------- 量具自检

def test_parsers_read_floor_and_matrix():
    """量具自检：必须真读出 requires-python 下界、numpy 下界、矩阵档。"""
    proj = _load_pyproject()
    floor = _floor_of(proj["project"]["requires-python"])
    assert floor is not None, (
        "没从 pyproject 读出 `requires-python` 的 `>=X.Y` 下界 ⇒ 解析器坏了，"
        "G1 会静默跳过。实读：%r" % proj["project"].get("requires-python"))
    assert _floor_of(">=3.10") == (3, 10) and _floor_of(">=3.12") == (3, 12), \
        "下界解析器读数不对（量具坏了）"
    lowers = _numpy_lower_bounds(proj)
    assert lowers, "没从 extras 读出任何 `numpy>=X.Y.Z` 下界 ⇒ 解析器坏了"
    mtx = _matrix(_load_gate())
    assert mtx and all("python-version" in r for r in mtx), \
        "没从 gate.yml 读出矩阵档 ⇒ 解析器坏了"


# ---------------------------------------------------------------- 主判据

def test_g1_requires_python_covers_extras_numpy_floor():
    """G1：requires-python 下界 >= numpy 下界版本在 PyPI 上要求的 Python 下界。"""
    proj = _load_pyproject()
    floor = _floor_of(proj["project"]["requires-python"])
    lowers = _numpy_lower_bounds(proj)
    assert floor is not None and lowers

    unknown = [v for v in lowers if v not in _NUMPY_REQUIRES_PYTHON]
    assert not unknown, (
        "extras 的 numpy 下界版本不在实测表 `_NUMPY_REQUIRES_PYTHON` 内（%s）——"
        "换了未测版本必须先拉 `pypi.org/pypi/numpy/<v>/json` 的 requires_python 补进表，"
        "不得静默放行（fail-closed）。" % unknown)

    need = max(_NUMPY_REQUIRES_PYTHON[v] for v in lowers)
    assert floor >= need, (
        "`requires-python = %r`（下界 %s）**低于** extras 下界 numpy%s 在 PyPI 上要求的 "
        "Python 下界 %s ⇒ 在该 Python 上装 `.[world]` 必然解析失败（#209）。\n"
        "修法：把 requires-python 下界抬到 %s 或更高，或把 numpy 下界降到该 Python 支持的版本。"
        % (proj["project"]["requires-python"], "%d.%d" % floor,
           ">=" + lowers[0], "%d.%d" % need, "%d.%d" % need))


def test_g2_matrix_covers_requires_python_floor():
    """G2：矩阵必须有一档 = requires-python 下界，且为阻断档（真跑声明的下界）。"""
    proj = _load_pyproject()
    floor = _floor_of(proj["project"]["requires-python"])
    want = "%d.%d" % floor
    rows = _matrix(_load_gate())
    match = [r for r in rows if str(r.get("python-version")) == want]
    assert match, (
        "gate.yml 矩阵里**没有** py%s 档，而 `requires-python` 声明的下界正是 py%s ⇒ "
        "声明的下界从未被 CI 跑过（#209）。矩阵现有档：%s"
        % (want, want, [str(r.get("python-version")) for r in rows]))
    assert any(r.get("experimental") is False for r in match), (
        "py%s（= requires-python 下界）在矩阵里只有 advisory 档（experimental 非 False）⇒ "
        "该下界不是阻断门禁，红了也不拦（#209）。" % want)


def test_g3_matrix_covers_dependency_lower_bound():
    """G3：矩阵必须有一档把 numpy 钉到 extras 声明的下界（覆盖「下界组合」）。"""
    proj = _load_pyproject()
    lowers = _numpy_lower_bounds(proj)
    rows = _matrix(_load_gate())
    pinned = [r for r in rows if str(r.get("pinned_deps") or "").strip()]
    assert pinned, (
        "gate.yml 矩阵没有任何一档 `pinned_deps`（钉死依赖）——默认 `pip install -e "
        "\".[full,dev]\"` 装的是 extras 区间内**最新**，下界从未被覆盖（#209）。")
    joined = " ".join(str(r.get("pinned_deps") or "") for r in rows)
    missing = [v for v in lowers if ("numpy==%s" % v) not in joined]
    assert not missing, (
        "矩阵的钉死档没有覆盖 extras 的 numpy 下界 %s（#209）——下界组合仍未被 CI 跑。"
        "实读 pinned_deps=%r" % (missing, [r.get("pinned_deps") for r in pinned]))


if __name__ == "__main__":
    sys.exit(0)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第二/第三批（F04/F06/F07/F08）公开可执行证据核查脚本。

只用标准库 + 仓库既有依赖（numpy / Pillow / pytest），不新增任何依赖。

它做三件事，全部在**独立子进程**里执行、不修改任何源码文件：

1. `baseline`  : 用 `git archive` 把测量基线（5603e28）导出到辅助目录
                   （不写当前工作树、不动 refs、导出物不含 __pycache__）。
2. `probe`     : 分别在「基线树」与「当前工作树」各起一个新进程，读取
                   **各自**的 lingshu 包（并断言 `lingshu.__file__` 落在指定
                   root 之内，防路径串台），给出 F04/F06/F07/F08 的实测读数。
3. `mutations` : 有限定点变异检验。变异**只在子进程启动时用 monkeypatch
                   注入**（pytest `-p` 插件），源码文件一个字节都不改；
                   正例（原代码、同一套设施）守卫必须绿，4 个反例必须红，
                   并记录具体的失败断言与退出码。结束校验 6 个源码文件
                   SHA256 与开始时一致。

`all` 依次做 1→2→3 并写出 JSON 报告。任何一项无法准确量化时，报告里写
`untested` / `error` 与原因，绝不编造数值。

用法（仓根，Windows PowerShell）::

    $work = "artifacts/contribution_submission_20261013/evidence_work/rerun_" + (Get-Date -Format yyyyMMdd_HHmmss)
    & "./.venv/Scripts/python.exe" -X utf8 -B bench/results/world_memory/20261013_second_third/verify_evidence.py all --work-dir $work --out "$work/verify_report.json"
    & "./.venv/Scripts/python.exe" -X utf8 -B bench/results/world_memory/20261013_second_third/verify_evidence.py safety --scratch "$work" --out "$work/safety_check.json"

**导出目标安全规则（不删除任何已存在的东西）**：`--dest` / `all` 的 baseline 子目录
必须是**尚不存在**的新路径；已存在的目录/文件/符号链接、仓库根本身、仓库根的祖先
目录一律明确拒绝（脚本里没有任何删除既有路径的代码）。重复使用同一个 `--work-dir`
会因 `baseline_<ref8>` 已存在而被拒绝，请换一个新的 work-dir。

`safety` 模式是有限安全自检，共 9 项真实检查：已有非空目录放哨兵 → 拒绝且哨兵与其它文件
字节不变；repo 根 → 拒绝且 6 个源码 SHA256 不变；祖先目录 / 已存在文件 / 链接（符号链接或
目录联接 junction）→ 拒绝且目标不变；合法新目录 → 正常导出；重复导出 → 拒绝且已导出内容逐
字节不变；恶意 tar 成员（上跳 / 绝对路径 / 盘符 / symlink / hardlink / FIFO）→ 全部被拒；
外加静态确认脚本自身不含任何删除调用。

Python 3.12；脚本自身不 import lingshu（探测一律走子进程）。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import time

BASELINE_REF_FULL = "5603e28540ea1596d089f259e6fecd594ea73ccc"
SOURCE_FILES = [
    "lingshu/core/core.py",
    "lingshu/nn/hex_gen.py",
    "lingshu/nn/hex_query.py",
    "lingshu/world/scene_simulator.py",
    "lingshu/world/seven_layer_loop.py",
    "lingshu/world/world3d.py",
]
F07_SCENARIOS = {
    "red-circle": ("左上有红色小点纹圆形", 7),
    "triangle": ("中间有点纹三角", 0),
}
GUARDS = {
    "P0": ["tests/test_second_batch_camera.py::test_c7_ingest_rejects_incomplete_camera_without_persisting",
           "tests/test_second_batch_relations_feedback.py::test_f06_wildcard_roles_are_order_invariant_full_permutations",
           "tests/test_second_batch_relations_feedback.py::test_f07_real_failure_diff_strictly_decreases",
           "tests/test_third_batch_actions.py::test_i_stale_target_observation_is_not_new_error_feedback",
           "tests/test_third_batch_actions.py::test_i_pre_existing_correction_is_frozen_by_stale_calls"],
    "M1": ["tests/test_second_batch_camera.py::test_c7_ingest_rejects_incomplete_camera_without_persisting"],
    "M2": ["tests/test_second_batch_relations_feedback.py::test_f06_wildcard_roles_are_order_invariant_full_permutations"],
    "M3": ["tests/test_second_batch_relations_feedback.py::test_f07_real_failure_diff_strictly_decreases"],
    "M4": ["tests/test_third_batch_actions.py::test_i_stale_target_observation_is_not_new_error_feedback",
           "tests/test_third_batch_actions.py::test_i_pre_existing_correction_is_frozen_by_stale_calls"],
}
MUTATIONS = {
    "P0": {"kind": "positive-control", "expected": "green",
           "what": "不注入任何补丁：同一套 pytest 设施下原代码必须全绿"},
    "M1": {"kind": "mutation", "expected": "red",
           "what": "Camera3D.from_meta 忽略 strict，强制 strict=False（伪造位姿的旧缺陷形态）"},
    "M2": {"kind": "mutation", "expected": "red",
           "what": "hex_query._canonical_det_order 改为输入顺序（通配双角色绑定随排列翻转）"},
    "M3": {"kind": "mutation", "expected": "red",
           "what": "hex_gen.refine_render 强制 rounds=0（真实像素改善被取消）"},
    "M4": {"kind": "mutation", "expected": "red",
           "what": "ActionDecision.observe_outcome 强制 target_fresh=True（陈旧观测被当成新反馈）"},
}

PLUGIN_TEMPLATES = {
    "P0": "# 正例对照：不注入任何补丁（文件存在，仅为统一命令形状）\n",
    "M1": (
        "import lingshu.world.world3d as _m\n"
        "_orig = _m.Camera3D.from_meta.__func__\n"
        "def _from_meta(cls, meta, strict=True):\n"
        "    return _orig(cls, meta, strict=False)\n"
        "_m.Camera3D.from_meta = classmethod(_from_meta)\n"
    ),
    "M2": (
        "import lingshu.nn.hex_query as _m\n"
        "def _canonical_det_order(detections):\n"
        "    return list(range(len(detections)))\n"
        "_m._canonical_det_order = _canonical_det_order\n"
    ),
    "M3": (
        "import lingshu.nn.hex_gen as _m\n"
        "_orig = _m.refine_render\n"
        "def refine_render(parts, size=_m.REFINE_MIN_SIZE, seed=7, rounds=None,\n"
        "                  supersample=1, calib=None):\n"
        "    return _orig(parts, size=size, seed=seed, rounds=0,\n"
        "                 supersample=supersample, calib=calib)\n"
        "_m.refine_render = refine_render\n"
    ),
    "M4": (
        "import lingshu.world.seven_layer_loop as _m\n"
        "_orig = _m.ActionDecision.observe_outcome\n"
        "def observe_outcome(self, trace, observed_self, observed_target, target_fresh):\n"
        "    return _orig(self, trace, observed_self, observed_target, True)\n"
        "_m.ActionDecision.observe_outcome = observe_outcome\n"
    ),
}


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sanitize(text, mappings):
    """把工作区/临时目录前缀换成占位符（含 JSON 转义后的反斜杠形态）。"""
    bs = chr(92)
    for raw, repl in mappings:
        if not raw:
            continue
        for variant in (raw, raw.replace(bs, '/'), raw.replace(bs, bs + bs)):
            if variant:
                text = text.replace(variant, repl)
    return text


# 任何盘符绝对路径（含 Windows 错误消息里成对反斜杠的写法）与 POSIX 绝对路径
ABS_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?:[^\\/\s\"'<>|,;]*[\\/])*[^\\/\s\"'<>|,;]*")
POSIX_ABS_RE = re.compile(r"(?<=[\s\"'(])/[^\s\"'<>|,;]+")


def redact_abs_paths(text):
    """兜底脱敏：把残留的绝对路径（含反斜杠成对形态）替换为 <abs-path>。"""
    text = ABS_PATH_RE.sub("<abs-path>", text)
    return POSIX_ABS_RE.sub("<abs-path>", text)


def run(cmd, cwd=None, env=None, timeout=1800):
    p = subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=timeout)
    return p.returncode, p.stdout.decode("utf-8", "replace")


# --------------------------------------------------------------------------
# 1) 基线导出
# --------------------------------------------------------------------------

# Windows 上 junctions（目录联接）不被 os.path.islink 认作链接，但同样是「链接目标」。
IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003
IO_REPARSE_TAG_SYMLINK = 0xA000000C


def _realpath(path):
    return os.path.realpath(os.path.abspath(path))


def is_link_target(path):
    """符号链接，或 Windows 目录联接 / 其它 reparse point：都算「链接目标」。

    这类目标一律拒绝：本脚本既不跟随它、也不删除它。
    """
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    return getattr(st, "st_reparse_tag", 0) in (IO_REPARSE_TAG_MOUNT_POINT,
                                                IO_REPARSE_TAG_SYMLINK)


def check_export_dest(repo, dest):
    """导出目标的只读安全门禁：只做判断，不创建、不删除、不覆盖。

    规则刻意取最简形态（越简单越不容易出错）：

      * 目标已存在（目录 / 文件 / 链接，含断链的符号链接）→ 明确拒绝，一个字节都不动；
      * 目标就是仓库根本身 → 明确拒绝；
      * 目标是仓库根的祖先目录 → 明确拒绝；
      * 其余情况返回绝对路径，交由调用方**新建**。
    """
    repo_real = _realpath(repo)
    dest_abs = os.path.abspath(dest)
    dest_real = _realpath(dest_abs)
    if dest_real == repo_real:
        raise RuntimeError("拒绝把导出目标指向仓库根本身：%s" % repo_real)
    if repo_real.startswith(dest_real + os.sep):
        raise RuntimeError("拒绝把导出目标指向仓库的祖先目录：%s（仓库位于其下）"
                           % dest_abs)
    if os.path.lexists(dest_abs):
        if is_link_target(dest_abs):
            kind = "链接目标：符号链接或目录联接（不解引用、不跟随）"
        elif os.path.isdir(dest_abs):
            kind = "已存在的目录（本脚本不删除、不覆盖）"
        else:
            kind = "已存在的文件（本脚本不删除、不覆盖）"
        raise RuntimeError("导出目标已存在：%s → %s；请为 --dest / --work-dir 指定一个"
                           "新的、不存在的目录。" % (dest_abs, kind))
    return dest_abs


def _safe_member_relpath(member, dest_real):
    """校验单个 tar 成员：只允许普通文件/目录，且 realpath 必须落在 dest 之内。

    - 链接（symlink / hardlink）与设备、FIFO 等一律拒绝，绝不解出后跟随到外部；
    - 绝对路径、`..` 上跳、盘符前缀一律拒绝；
    - 最终用 realpath + commonpath 复核成员落点确实在 dest 内。
    """
    if member.issym() or member.islnk():
        raise RuntimeError("tar 成员是链接，拒绝解出（不解引用、不跟随到外部）：%r"
                           % member.name)
    if not (member.isreg() or member.isdir()):
        raise RuntimeError("tar 成员不是普通文件/目录：%r（tarfile type=%r）"
                           % (member.name, member.type))
    raw = member.name.replace(chr(92), "/")
    parts = [seg for seg in raw.split("/") if seg not in ("", ".")]
    if not parts:
        return None
    if raw.startswith("/") or any(seg == ".." for seg in parts) or ":" in parts[0]:
        raise RuntimeError("tar 成员路径越界（绝对路径 / 上跳 / 盘符）：%r" % member.name)
    rel = os.path.join(*parts)
    target = os.path.realpath(os.path.join(dest_real, rel))
    try:
        inside = os.path.commonpath([target, dest_real]) == dest_real
    except ValueError:
        inside = False
    if not inside:
        raise RuntimeError("tar 成员 realpath 落在导出目录之外：%r → %s"
                           % (member.name, target))
    return rel


def prepare_baseline(repo, ref, dest):
    """把 ref 导出到 dest。dest 必须是**不存在**的新路径；全程不删除任何东西。

    先校验全部成员（此时尚未落盘，校验失败不会留下半成品），全部通过后才创建目录并写出。
    写出阶段若中途失败，目录可能停在该次运行自己的部分解出状态；脚本不会去删它，请另指定
    一个新的目录重试。
    """
    dest = check_export_dest(repo, dest)
    data = subprocess.run(["git", "-C", repo, "archive", "--format=tar", ref],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if data.returncode != 0:
        raise RuntimeError("git archive %s 失败：%s"
                           % (ref, data.stderr.decode("utf-8", "replace")))
    with tarfile.open(fileobj=io.BytesIO(data.stdout), mode="r:") as tar:
        members = tar.getmembers()
        bad = [m.name for m in members
               if "__pycache__" in m.name or m.name.endswith(".pyc")]
        if bad:
            raise RuntimeError("基线导出物出现 __pycache__/.pyc：%r" % bad[:5])
        dest_real = os.path.realpath(dest)
        plan = [(m, _safe_member_relpath(m, dest_real)) for m in members]
        os.makedirs(dest)
        for m, rel in plan:
            if rel is None:
                continue
            out = os.path.join(dest, rel)
            if m.isdir():
                os.makedirs(out, exist_ok=True)
                continue
            parent = os.path.dirname(out)
            if parent:
                os.makedirs(parent, exist_ok=True)
            src = tar.extractfile(m)
            with open(out, "wb") as fh:
                fh.write(src.read())
    if not os.path.isfile(os.path.join(dest, "lingshu", "__init__.py")):
        raise RuntimeError("基线导出物缺少 lingshu 包：%s" % dest)
    return {"ref": ref, "dest": dest, "files": len(members), "has_pycache": False,
            "dest_pre_existing": False, "dest_removed": False}


# --------------------------------------------------------------------------
# 2) 探测 worker（在指定 root 上 import 该 root 的 lingshu）
# --------------------------------------------------------------------------

F04_TRUTH = (0.6, 1.0, 6.0)
F04_EYE_A = (-2.0, 1.4, -1.0)
F04_EYE_B = (2.5, 1.6, 0.5)
F04_GAZE_B = (1.6, 1.0, 6.0)
F04_SW, F04_SH = 800, 600


def _probe_workspace_guard(root):
    import lingshu
    path = os.path.realpath(getattr(lingshu, "__file__", "") or "")
    root_r = os.path.realpath(root)
    ok = path.startswith(root_r + os.sep) or path == root_r
    if not ok:
        raise RuntimeError("import 到的 lingshu 不在指定 root 内：%s（root=%s）"
                           % (path, root_r))
    return {"root": root_r, "lingshu_file": path, "inside_root": True}


def probe_f04(root):
    from lingshu.core.core import SpacetimeMemoryEngine
    from lingshu.world.world3d import Camera3D
    out = {"has_camera_to_meta": hasattr(Camera3D, "to_meta"),
           "has_camera_from_meta": hasattr(Camera3D, "from_meta")}

    def six(cam):
        return {"cx": float(cam.cx), "cy": float(cam.cy), "cz": float(cam.cz),
                "yaw": float(cam.yaw), "pitch": float(cam.pitch),
                "fov_deg": float(cam.fov_deg)}

    cam_a = Camera3D.look_at(F04_EYE_A, F04_TRUTH)
    cam_b = Camera3D.look_at(F04_EYE_B, F04_GAZE_B, fov_deg=75.0)

    def bbox(cam, point, hw=20.0, hh=30.0):
        px, py = cam.project(point, F04_SW, F04_SH)
        return [px - hw, py - hh, px + hw, py + hh]

    tmp = tempfile.mkdtemp(prefix="f04probe_")
    db = os.path.join(tmp, "f04.db")
    e1 = SpacetimeMemoryEngine(db_path=db)
    ingests = []
    for cam in (cam_a, cam_b):
        r = e1.world3d("add_view", {
            "category": "tree", "bbox": bbox(cam, F04_TRUTH), "camera": six(cam),
            "eid": "tree-1", "tick": 4, "screen_w": F04_SW, "screen_h": F04_SH,
            "confidence": 0.9, "timestamp": 100.5})
        ingests.append({"status": r.get("status"),
                        "has_persisted_key": "persisted" in r,
                        "persisted": r.get("persisted"),
                        "triangulated": r.get("triangulated")})
    nodes = e1.store.get_nodes_by_tag("vprim")
    out["ingest_two_known_cameras"] = ingests
    out["vprim_nodes_after_ingest"] = len(nodes)
    from lingshu.world import world3d as _w3d
    tag = getattr(_w3d, "CAMERA_BLOCK_TAG", "lingshu-camera")
    out["camera_block_tag"] = tag
    out["nodes_carry_camera_block"] = sum(
        1 for n in nodes if tag in (n.content or ""))
    r_empty = e1.world3d("add_view", {"category": "chair", "bbox": [10, 10, 50, 60],
                                      "camera": {}, "eid": "chair-empty", "tick": 1,
                                      "screen_w": F04_SW, "screen_h": F04_SH})
    out["ingest_empty_camera_status"] = r_empty.get("status")
    out["ingest_empty_camera_reason"] = r_empty.get("reason")
    e1.store.conn.close()

    e2 = SpacetimeMemoryEngine(db_path=db)
    r = e2.world3d("build", {"multiview": True})
    det = r.get("detail") or {}
    objs = det.get("objects") or []
    center = tuple(objs[0]["center"]) if objs else None
    out["cross_engine_build"] = {
        "status": r.get("status"), "mode": r.get("mode"),
        "requested_mode": r.get("requested_mode"), "reason": r.get("reason"),
        "objects": r.get("objects"), "observations": r.get("observations"),
        "center": list(center) if center else None,
        "max_abs_error_m": (round(max(abs(a - b) for a, b in zip(center, F04_TRUTH)), 6)
                            if center else None),
    }
    out["cross_engine_error_lt_0_05"] = bool(
        center and max(abs(a - b) for a, b in zip(center, F04_TRUTH)) < 0.05)
    e2.store.conn.close()
    return out


def probe_f06(root):
    from lingshu.nn.hex_query import ConnectionLibrary, query_match
    lib = ConnectionLibrary()
    lib.add_record("red_above_blue", [
        {"type": "circle", "color": "red", "zone": "*", "role": "head"},
        {"type": "circle", "color": "blue", "zone": "*", "role": "body"},
    ], tags={"relations": [{"from": "head", "to": "body", "rel": "above"}]})
    dets = [{"obj": "circle|red", "pos": "r7"}, {"obj": "circle|blue", "pos": "r1"}]
    entry = query_match(list(dets), lib)[0]
    rels = entry.get("relations")
    return {
        "layout": "red circle @ r7（下方行）, blue circle @ r1（上方行）",
        "declared_relation": "red(head) above blue(body) —— 按该布局不成立",
        "score": entry.get("score"), "verdict": entry.get("verdict"),
        "relations_present": rels is not None,
        "relation_satisfied": (rels[0].get("satisfied") if rels else None),
        "relation_facts": (rels[0].get("witness", {}).get("facts") if rels else None),
        "result_keys": sorted(entry.keys()),
    }


def probe_f07(root, scenarios):
    import inspect
    import numpy as np
    from lingshu.nn import hex_gen
    from lingshu.gen.hex_composite import extract_attributes
    calib = getattr(hex_gen, "ATTR_CALIB_96", None)
    try:
        has_refine_kwarg = "refine_rounds" in inspect.signature(
            hex_gen.generate_from_text).parameters
    except Exception:
        has_refine_kwarg = None
    out = {"has_refine_render": hasattr(hex_gen, "refine_render"),
           "generate_from_text_has_refine_rounds": has_refine_kwarg,
           "readback_calib": calib,
           "readback_colors": list(getattr(hex_gen, "READBACK_COLORS", []) or []),
           "scenarios": {}}

    def independent_misses(img, parts, log):
        misses = []
        for i, p in enumerate(parts):
            entry = next((e for e in (log or []) if e.get("part") == i), None)
            if entry is None:
                misses.append({"part": i, "reason": "no_landing_log"})
                continue
            if p.get("color") not in ("red", "green", "blue"):
                misses.append({"part": i, "reason": "color_out_of_readback_table"})
                continue
            a = extract_attributes(img, entry["zone_landed"], p["color"], calib,
                                   shape_hint=p.get("shape"))
            if a.get("pattern") != p.get("pattern"):
                misses.append({"part": i, "spec_pattern": p.get("pattern"),
                               "read_pattern": a.get("pattern"),
                               "read_area": a.get("area"),
                               "zone": entry["zone_landed"]})
        return misses

    for key in scenarios:
        text, seed = F07_SCENARIOS[key]
        rec = {"text": text, "seed": seed, "size": 96}
        g = hex_gen.generate_from_text(text, size=96, seed=seed)
        detail = independent_misses(g["image"], g["parts"], g["log"])
        rec["pattern_misses_independent"] = len(detail)
        rec["miss_detail"] = detail
        rec["parts"] = g["parts"]
        rec["parts_count"] = len(g["parts"])
        rec["declared_patterns"] = [p.get("pattern") for p in g["parts"]]
        refine = g.get("refine") or {}
        rec["prod_refine_before_after"] = [refine.get("before"), refine.get("after")]
        rec["refine_present"] = "refine" in g
        if has_refine_kwarg:
            off = hex_gen.generate_from_text(text, size=96, seed=seed,
                                             refine_rounds=0)
            rec["off_pattern_misses_independent"] = len(
                independent_misses(off["image"], off["parts"], off["log"]))
            rec["intent_preserved_parts_equal"] = bool(off["parts"] == g["parts"])
            rec["image_changed_by_refine"] = bool(
                not np.array_equal(off["image"], g["image"]))
        out["scenarios"][key] = rec
    return out


def probe_f08(root):
    from lingshu.world.seven_layer_loop import SevenLayerLoop
    out = {"has_configure_action": hasattr(SevenLayerLoop, "configure_action"),
           "has_clear_action": hasattr(SevenLayerLoop, "clear_action")}
    if not out["has_configure_action"]:
        out["comparison"] = "untested: 基线没有 configure_action，行动对照无法复跑"
        return out
    path = os.path.join(root, "tests", "test_third_batch_actions.py")
    if not os.path.isfile(path):
        out["comparison"] = "untested: 找不到 tests/test_third_batch_actions.py"
        return out
    code, text = run([sys.executable, "-B", "-X", "utf8", path, "--report"],
                     cwd=root, timeout=900)
    out["report_exit_code"] = code
    if code != 0:
        out["comparison"] = "error: 对照报告退出码 %d" % code
        out["report_tail"] = text[-1500:]
        return out
    start = text.find("{")
    report = json.loads(text[start:]) if start >= 0 else None
    runs = (report or {}).get("runs", {})
    out["comparison"] = {
        name: {"final_distance": v.get("final_distance"),
               "extrapolated_ticks": v.get("extrapolated_ticks"),
               "final_traced_distance": v.get("final_traced_distance"),
               "target_kind": v.get("target_kind")}
        for name, v in runs.items()}
    return out


def probe_worker(root, f07_scenarios):
    guard = _probe_workspace_guard(root)
    result = {"workspace_guard": guard, "root": root}
    for name, fn in (("f04", lambda: probe_f04(root)),
                     ("f06", lambda: probe_f06(root)),
                     ("f07", lambda: probe_f07(root, f07_scenarios)),
                     ("f08", lambda: probe_f08(root))):
        try:
            result[name] = fn()
        except Exception as exc:
            import traceback
            result[name] = {"error": "%s: %s" % (type(exc).__name__, exc),
                            "traceback_tail": traceback.format_exc().splitlines()[-6:]}
    return result


# --------------------------------------------------------------------------
# 3) 变异检验
# --------------------------------------------------------------------------



def _extract_failure(out):
    keeps = []
    for ln in out.splitlines():
        s = ln.strip()
        if s.startswith("E ") or s.startswith(">") or s.startswith("FAILED") \
                or s.startswith("assert") or "AssertionError" in s:
            keeps.append(ln.rstrip())
    return keeps[:20]


def _assert_locations(out):
    """逐条失败的断言位置（file.py:line: Error），用于定位「红」到底红在哪一句。"""
    locs = []
    for ln in out.splitlines():
        if ".py:" not in ln or "Error" not in ln:
            continue
        head = ln.strip().lstrip("> ").strip()
        at = head.find(".py:")
        if at < 0:
            continue
        end = head.find("Error", at)
        item = head[:end + 5].strip() if end >= 0 else ""
        if item and item not in locs:
            locs.append(item)
    return locs


def run_mutations(root, python, work_dir, env_extra=None):
    os.makedirs(work_dir, exist_ok=True)
    results = []
    for mid in ("P0", "M1", "M2", "M3", "M4"):
        plugin = os.path.join(work_dir, "mut_%s.py" % mid)
        with open(plugin, "w", encoding="utf-8") as fh:
            fh.write(PLUGIN_TEMPLATES[mid])
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [work_dir, root] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTEST_ADDOPTS"] = ""
        if env_extra:
            env.update(env_extra)
        cmd = [python, "-X", "utf8", "-m", "pytest", "-p", "no:cacheprovider",
               "-q", "--no-header", "-p", "mut_%s" % mid] + GUARDS[mid]
        t0 = time.time()
        code, out = run(cmd, cwd=root, env=env, timeout=1800)
        rec = {"id": mid, "kind": MUTATIONS[mid]["kind"],
               "expected": MUTATIONS[mid]["expected"], "what": MUTATIONS[mid]["what"],
               "command": " ".join(cmd[1:]), "guards": GUARDS[mid],
               "exit_code": code, "seconds": round(time.time() - t0, 1),
               "summary": [l for l in out.splitlines()
                           if "passed" in l or "failed" in l][-3:],
               "failure_assertions": _extract_failure(out),
               "assertion_locations": _assert_locations(out)}
        rec["verdict"] = "green" if code == 0 else "red"
        rec["as_expected"] = (rec["verdict"] == rec["expected"])
        rec["killed_guard"] = bool(rec["failure_assertions"]) and code != 0
        results.append(rec)
    return results


# --------------------------------------------------------------------------
# 编排
# --------------------------------------------------------------------------

def gather_runtime():
    info = {"python": sys.version.split()[0], "platform": platform.platform()}
    for mod in ("numpy", "PIL", "pytest"):
        try:
            m = __import__(mod)
            info[mod] = getattr(m, "__version__", "unknown")
        except Exception as exc:
            info[mod] = "unavailable: %s" % exc
    return info


def do_probe(python, script, root, f07_scenarios, out_path, mappings):
    env = dict(os.environ)
    env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"]
                                if env.get("PYTHONPATH") else "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    cmd = [python, "-B", "-X", "utf8", script, "_probe-worker", "--root", root,
           "--f07", f07_scenarios, "--out", out_path]
    code, out = run(cmd, cwd=root, env=env, timeout=1800)
    if os.path.isfile(out_path):
        with open(out_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        data = {"error": "worker 未产出结果（exit=%d）" % code}
    data["worker_exit_code"] = code
    if code != 0:
        data["worker_stdout_tail"] = sanitize(out[-2000:], mappings)
    return data


def build_limitations(report):
    """如实列出未测/不可外推项，不留空、不估数。"""
    lim = []
    base = report.get("probe", {}).get("baseline", {})
    cur = report.get("probe", {}).get("current", {})
    b8 = base.get("f08") if isinstance(base.get("f08"), dict) else {}
    if b8 and not b8.get("has_configure_action"):
        lim.append("F08：基线（5603e28）没有 configure_action/clear_action，"
                   "行动对照无法在基线上复跑 —— 基线侧只有 has_configure_action=False，"
                   "没有基线数值，标为未测。")
    for label, probed in (("baseline", base), ("current", cur)):
        f7 = probed.get("f07") if isinstance(probed.get("f07"), dict) else {}
        for key, rec in (f7.get("scenarios") or {}).items():
            if rec.get("pattern_misses_independent") is None:
                lim.append("F07/%s/%s：未能取得独立像素读数（该树读取器不可用），"
                           "标为未测。" % (label, key))
    lim.append("F07 的公开读数一律来自本脚本自算的独立像素回读（extract_attributes）；"
               "生产侧 refine 的 before/after 只作旁证列出，不作为公开判据。")
    lim.append("F04 的 0.05 m 阈值只用一对已知位姿相机在多视角重建上测得，"
               "不构成通用标定、外参求解或真实设备精度的声明。")
    lim.append("F06 只声明‘关系不满足时 score 降低且 relation.satisfied=False’，"
               "不声明任何裁决（verdict）一定翻转。")
    lim.append("本脚本只跑 4 个定点守卫 + 有限探测（不是 full pytest，不覆盖全仓）；"
               "其余门禁证据以 PR/CI 的 gate run 链接与 verify_report.json 为准，"
               "本目录不再存放 gate 日志。")
    lim.append("全部读数在 Windows + .venv Python3.12 上取得；未在 CI 复跑，"
               "不声明 CI 已绿，也不声明其它解释器/平台矩阵。")
    return lim


# --------------------------------------------------------------------------
# 4) 安全自检（真实执行；不删除任何既有目录，也不删除用户指定的任何路径）
# --------------------------------------------------------------------------

# 用拼接写法列举，避免这行自身被下面的静态检查命中（保持检查自洽）。
DELETE_CALL_TOKENS = ["rmtree", "os.remove", "os.unlink", "os.rmdir",
                      "os.removedirs", "shutil.move", "Path.unlink"]


def _tree_snapshot(root):
    """目录树快照：目录记 dir，符号链接记指向，普通文件记 SHA256。"""
    snap = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in list(dirnames) + sorted(filenames):
            p = os.path.join(dirpath, name)
            rel = os.path.relpath(p, root)
            if os.path.islink(p):
                snap[rel] = "link->" + os.readlink(p)
            elif os.path.isdir(p):
                snap[rel] = "dir"
            else:
                snap[rel] = sha256_file(p)
    return snap


def _cli(python, script, args, cwd, mappings, timeout=900):
    proc = subprocess.run([python, "-X", "utf8", "-B", script] + list(args),
                          cwd=cwd, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=timeout)
    return proc.returncode, sanitize(proc.stdout.decode("utf-8", "replace"), mappings)


def run_safety_checks(python, script, current, scratch):
    """有限安全自检：5 类真实导出检查 + 1 条源码静态检查。

    全程只读既有路径；所有靶子都建在 scratch 下，不删除任何用户/源码目录。
    """
    repo = os.path.realpath(current)
    scratch = os.path.abspath(scratch)
    os.makedirs(scratch, exist_ok=True)
    mappings = [(repo, "<workspace>"), (scratch, "<scratch>")]
    checks = []

    def add(cid, what, ok, detail):
        checks.append({"id": cid, "what": what, "passed": bool(ok), "detail": detail})

    def add_skipped(cid, what, reason):
        checks.append({"id": cid, "what": what, "passed": None, "skipped": reason})

    # --- 静态检查：脚本自身没有删除调用 ---
    with open(script, "r", encoding="utf-8") as fh:
        src = fh.read()
    hits = [t for t in DELETE_CALL_TOKENS if (t + "(") in src]
    add("no_delete_calls_in_source",
        "脚本源码不含任何删除调用（rmtree / remove / unlink / rmdir / move）",
        not hits, {"hits": hits})

    # --- A. 已有非空目录：拒绝，且哨兵与目录内其它文件一个字节都不动 ---
    keep = os.path.join(scratch, "keep_me")
    os.makedirs(os.path.join(keep, "nested"), exist_ok=True)
    sentinel = os.path.join(keep, "sentinel.bin")
    with open(sentinel, "wb") as fh:
        fh.write(os.urandom(256))
    other = os.path.join(keep, "nested", "other.txt")
    with open(other, "w", encoding="utf-8") as fh:
        fh.write("do-not-touch\n")
    sent_before, other_before = sha256_file(sentinel), sha256_file(other)
    snap_before = _tree_snapshot(keep)
    code, out = _cli(python, script, ["baseline", "--current", repo, "--dest", keep],
                     repo, mappings)
    snap_after = _tree_snapshot(keep)
    add("existing_nonempty_dir_refused",
        "对已有非空目录（含 256B 哨兵）导出必须拒绝，哨兵与其它文件字节不变",
        code != 0 and sha256_file(sentinel) == sent_before
        and sha256_file(other) == other_before and snap_before == snap_after
        and "已存在" in out,
        {"exit_code": code,
         "sentinel_sha256_before": sent_before,
         "sentinel_sha256_after": sha256_file(sentinel),
         "other_file_sha256_before": other_before,
         "other_file_sha256_after": sha256_file(other),
         "tree_unchanged": snap_before == snap_after,
         "files_in_target": sorted(snap_before),
         "output_tail": out.strip().splitlines()[-2:]})

    # --- B. 仓库根：拒绝，且 6 个源码 SHA256 不变 ---
    src_before = {rel: sha256_file(os.path.join(repo, rel)) for rel in SOURCE_FILES}
    top_before = sorted(os.listdir(repo))
    code, out = _cli(python, script, ["baseline", "--current", repo, "--dest", repo],
                     repo, mappings)
    src_after = {rel: sha256_file(os.path.join(repo, rel)) for rel in SOURCE_FILES}
    add("repo_root_refused",
        "导出目标指向仓库根必须拒绝，且 6 个源码文件 SHA256 不变",
        code != 0 and src_before == src_after
        and sorted(os.listdir(repo)) == top_before and "仓库根" in out,
        {"exit_code": code, "source_sha256_unchanged": src_before == src_after,
         "source_sha256": src_before,
         "output_tail": out.strip().splitlines()[-2:]})

    # --- C. 仓库根的祖先目录：拒绝，仓库树不受影响 ---
    parent = os.path.dirname(repo)
    code, out = _cli(python, script, ["baseline", "--current", repo, "--dest", parent],
                     repo, mappings)
    add("ancestor_dir_refused",
        "导出目标指向仓库根的祖先目录必须拒绝，仓库树不变",
        code != 0 and os.path.isdir(repo)
        and sorted(os.listdir(repo)) == top_before and "祖先" in out,
        {"exit_code": code,
         "repo_tree_unchanged": sorted(os.listdir(repo)) == top_before,
         "output_tail": out.strip().splitlines()[-2:]})

    # --- D. 已存在的文件目标：拒绝，内容不变 ---
    a_file = os.path.join(scratch, "a_file.txt")
    with open(a_file, "wb") as fh:
        fh.write(b"keep-this-file\n")
    file_before = sha256_file(a_file)
    code, out = _cli(python, script, ["baseline", "--current", repo, "--dest", a_file],
                     repo, mappings)
    add("existing_file_refused",
        "导出目标指向已存在文件必须拒绝，文件内容不变",
        code != 0 and sha256_file(a_file) == file_before and "已存在的文件" in out,
        {"exit_code": code, "file_sha256_before": file_before,
         "file_sha256_after": sha256_file(a_file),
         "output_tail": out.strip().splitlines()[-2:]})

    # --- E. 链接目标（symlink，无权限时退化为目录联接 junction）：拒绝，不动其指向的目录 ---
    link = os.path.join(scratch, "link_to_keep_me")
    link_kind = None
    try:
        os.symlink(keep, link, target_is_directory=True)
        link_kind = "symlink"
    except OSError:
        proc = subprocess.run(["cmd", "/c", "mklink", "/J", link, keep],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if proc.returncode == 0 and is_link_target(link):
            link_kind = "junction"
    if link_kind is None:
        add_skipped("link_target_refused",
                    "导出目标是指向已有目录的链接必须拒绝（本机既建不了符号链接也建不了目录联接）",
                    "symlink 与 junction 均创建失败")
    else:
        snap_keep = _tree_snapshot(keep)
        code, out = _cli(python, script, ["baseline", "--current", repo, "--dest", link],
                         repo, mappings)
        add("link_target_refused",
            "导出目标是指向已有目录的链接（symbolic link 或 junction）必须拒绝，"
            "链接与其目标均不变",
            code != 0 and is_link_target(link) and _tree_snapshot(keep) == snap_keep
            and "不解引用" in out,
            {"link_kind": link_kind, "exit_code": code,
             "link_still_link": is_link_target(link),
             "link_target_unchanged": _tree_snapshot(keep) == snap_keep,
             "output_tail": out.strip().splitlines()[-2:]})

    # --- F. 合法新目录：正常导出（含 lingshu 包、无缓存） ---
    fresh = os.path.join(scratch, "export_once")
    code1, out1 = _cli(python, script, ["baseline", "--current", repo, "--dest", fresh],
                       repo, mappings)
    snap1 = _tree_snapshot(fresh) if os.path.isdir(fresh) else {}
    pyc = sorted(k for k in snap1 if k.endswith(".pyc") or "__pycache__" in k)
    has_pkg = os.path.isfile(os.path.join(fresh, "lingshu", "__init__.py"))
    add("fresh_dir_exports",
        "全新的不存在的目录可正常导出（含 lingshu 包、无 __pycache__/.pyc）",
        code1 == 0 and has_pkg and not pyc and len(snap1) > 0,
        {"exit_code": code1, "file_count": len(snap1), "has_lingshu_init": has_pkg,
         "pycache_or_pyc": pyc[:5]})

    # --- G. 重复导出同一目录：拒绝，且不删除已导出内容 ---
    code2, out2 = _cli(python, script, ["baseline", "--current", repo, "--dest", fresh],
                       repo, mappings)
    snap2 = _tree_snapshot(fresh)
    add("repeat_export_refused_without_delete",
        "对已存在的 baseline 目录再次导出必须拒绝，已导出内容逐字节不变（未删除）",
        code2 != 0 and snap1 == snap2 and "已存在" in out2,
        {"exit_code": code2, "tree_unchanged": snap1 == snap2,
         "file_count_before": len(snap1), "file_count_after": len(snap2),
         "output_tail": out2.strip().splitlines()[-2:]})

    # --- H. tar 成员校验：真实构造恶意成员，逐一必须被拒 ---
    dest_real = os.path.realpath(os.path.join(scratch, "member_guard"))
    cases = []

    def _member(name, ttype=tarfile.REGTYPE, linkname=""):
        m = tarfile.TarInfo(name)
        m.type = ttype
        m.linkname = linkname
        return m

    probes = [
        ("updir", _member("../evil.txt"), True),
        ("inner_updir", _member("foo/../../evil.txt"), True),
        ("backslash_traversal", _member(".." + chr(92) + "evil.txt"), True),
        ("abs_posix", _member("/etc/passwd"), True),
        ("drive_letter", _member("C:/Windows/evil.txt"), True),
        ("symlink", _member("good.txt", tarfile.SYMTYPE, "/etc/passwd"), True),
        ("hardlink", _member("good.txt", tarfile.LNKTYPE, "../../outside.txt"), True),
        ("fifo", _member("pipe", tarfile.FIFOTYPE), True),
        ("regular_ok", _member("lingshu/core/core.py"), False),
        ("dir_ok", _member("lingshu/", tarfile.DIRTYPE), False),
    ]
    for label, mem, expect_reject in probes:
        try:
            rel = _safe_member_relpath(mem, dest_real)
            got = {"accepted": True, "rel": rel}
        except RuntimeError as exc:
            got = {"accepted": False, "reason": str(exc)}
        ok = (not got["accepted"]) if expect_reject else got["accepted"]
        cases.append({"case": label, "expect_reject": expect_reject,
                      "rejected": not got["accepted"], "ok": bool(ok),
                      "detail": got})
    add("tar_member_path_guard",
        "tar 成员校验：上跳 / 绝对路径 / 盘符 / 链接 / FIFO 一律拒绝，普通文件与目录放行",
        all(c["ok"] for c in cases), {"cases": cases})

    skipped = [c["id"] for c in checks if c.get("passed") is None]
    return {"schema": "verify_evidence_safety/v1",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "scratch": scratch,
            "check_count": len(checks),
            "checks": checks,
            "skipped": skipped,
            "all_passed": (not skipped) and all(c["passed"] for c in checks)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="第二/第三批公开证据核查")
    ap.add_argument("mode", choices=["all", "baseline", "probe", "mutations",
                                     "safety", "_probe-worker"])
    ap.add_argument("--current", default=os.getcwd())
    ap.add_argument("--baseline-ref", default=BASELINE_REF_FULL)
    ap.add_argument("--work-dir", default=None)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--f07", default="both", choices=["red-circle", "triangle", "both"])
    ap.add_argument("--out", default=None)
    ap.add_argument("--root", default=None)
    ap.add_argument("--dest", default=None)
    ap.add_argument("--scratch", default=None)
    args = ap.parse_args(argv)

    if args.mode == "_probe-worker":
        scenarios = (["red-circle", "triangle"] if args.f07 == "both" else [args.f07])
        root = os.path.realpath(args.root or os.getcwd())
        sys.path.insert(0, root)
        data = probe_worker(root, scenarios)
        if args.out:
            with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1)
                fh.write("\n")
        else:
            print(json.dumps(data, ensure_ascii=False, indent=1))
        return 0

    script = os.path.abspath(__file__)
    current = os.path.realpath(args.current)
    if args.mode == "baseline":
        try:
            info = prepare_baseline(current, args.baseline_ref,
                                    os.path.abspath(args.dest or "baseline_export"))
        except RuntimeError as exc:
            sys.stderr.write("拒绝导出：%s\n" % exc)
            return 2
        print(json.dumps(info, ensure_ascii=False, indent=1))
        return 0

    if args.mode == "safety":
        # --scratch 只当父目录：本次检查固定用其下一个新的唯一子目录，
        # 因此同一 scratch 可以反复跑（每次都是全新的靶子），且不会覆盖上次证据。
        if args.scratch:
            parent = os.path.abspath(args.scratch)
            os.makedirs(parent, exist_ok=True)
            scratch = os.path.join(parent,
                                   "safety_run_%s" % time.strftime("%Y%m%d_%H%M%S"))
        else:
            scratch = tempfile.mkdtemp(prefix="verify_safety_")
        mappings = [(current, "<workspace>"), (os.path.realpath(scratch), "<scratch>")]
        res = run_safety_checks(args.python, script, current, scratch)
        text = redact_abs_paths(
            sanitize(json.dumps(res, ensure_ascii=False, indent=1), mappings))
        if args.out:
            with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text + "\n")
        print(text)
        return 0 if res["all_passed"] else 1

    if args.mode == "probe":
        root = os.path.realpath(args.root or current)
        out = args.out or os.path.join(tempfile.gettempdir(),
                                       "probe_%d.json" % os.getpid())
        data = do_probe(args.python, script, root, args.f07, out, [])
        print(json.dumps(data, ensure_ascii=False, indent=1))
        return 0

    if args.mode == "mutations":
        mut_dir = (os.path.abspath(args.work_dir) if args.work_dir
                   else tempfile.mkdtemp(prefix="verify_mut_"))
        res = run_mutations(os.path.realpath(args.root or current), args.python, mut_dir)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0

    work = (os.path.abspath(args.work_dir) if args.work_dir
            else tempfile.mkdtemp(prefix="verify_evidence_"))
    os.makedirs(work, exist_ok=True)
    mappings = [(work, "<work-dir>"), (current, "<workspace>")]
    report = {"schema": "second_third_batch_public_evidence/v1",
              "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "measurement_baseline": {
                  "ref": args.baseline_ref,
                  "short": args.baseline_ref[:8],
                  "note": ("本批能力差一律以 %s（第一批之后的提交）为基线；基线里"
                           "已经存在的第一批能力不计入本批。" % args.baseline_ref[:8])},
              "current_tree": {"root": "<workspace>", "head": None,
                               "worktree_changed_files": []},
              "runtime": gather_runtime(),
              "source_sha256_before": {}, "source_sha256_after": {},
              "source_files_unchanged": None,
              "probe": {}, "mutations": [], "limitations": []}

    code, head = run(["git", "-C", current, "rev-parse", "HEAD"])
    report["current_tree"]["head"] = head.strip() if code == 0 else "unknown"
    code, dirty = run(["git", "-C", current, "diff", "--name-only", args.baseline_ref])
    report["current_tree"]["worktree_changed_files"] = [
        l.strip() for l in dirty.splitlines()
        if l.strip() and not l.startswith(("warning:", "fatal:", "error:"))]

    for rel in SOURCE_FILES:
        p = os.path.join(current, rel)
        report["source_sha256_before"][rel] = (sha256_file(p) if os.path.isfile(p)
                                               else "missing")

    base_dir = os.path.abspath(os.path.join(work, "baseline_%s" % args.baseline_ref[:8]))
    try:
        report["baseline_export"] = prepare_baseline(current, args.baseline_ref, base_dir)
    except RuntimeError as exc:
        sys.stderr.write(
            "基线导出被安全门禁拒绝：%s\n"
            "提示：重复使用同一 --work-dir 会因 baseline_%s 已存在而被拒绝，"
            "请指定一个新的 --work-dir（脚本不会删除已存在的导出）。\n"
            % (exc, args.baseline_ref[:8]))
        return 2

    scenarios = (["red-circle", "triangle"] if args.f07 == "both" else [args.f07])
    report["f07_scenarios"] = scenarios
    for label, root in (("baseline", base_dir), ("current", current)):
        out_path = os.path.join(work, "probe_%s.json" % label)
        data = do_probe(args.python, script, root, args.f07, out_path, mappings)
        report["probe"][label] = json.loads(
            sanitize(json.dumps(data, ensure_ascii=False), mappings))
    report["mutations"] = run_mutations(current, args.python,
                                        os.path.join(work, "mutations"))
    for rel in SOURCE_FILES:
        p = os.path.join(current, rel)
        report["source_sha256_after"][rel] = (sha256_file(p) if os.path.isfile(p)
                                              else "missing")
    report["source_files_unchanged"] = (
        report["source_sha256_before"] == report["source_sha256_after"])
    report["limitations"] = build_limitations(report)
    report = json.loads(sanitize(json.dumps(report, ensure_ascii=False), mappings))

    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
    print(json.dumps({
        "report": args.out,
        "baseline": report["measurement_baseline"]["short"],
        "source_files_unchanged": report["source_files_unchanged"],
        "mutations": [{"id": m["id"], "verdict": m["verdict"],
                       "as_expected": m["as_expected"]} for m in report["mutations"]],
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

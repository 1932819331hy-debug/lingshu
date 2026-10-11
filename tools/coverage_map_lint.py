# -*- coding: utf-8 -*-
"""coverage_map_lint · 零覆盖模块映射审计器（P2 批次 F / issue #23 新增）

背景（公开 issue #23「覆盖映射」）
----------------------------------
对 `lingshu/**/*.py` 的每个包内模块，检查其 **basename** 是否在 `tests/*.py`
的**全文文本**里出现过（词边界匹配）。从未出现的模块＝**零引用/零覆盖**：
既没有单元测试导入它，也没有任何测试正文提到它。

在 issue 原基线 a215d8b 上，53 个包内模块中 **37 个零引用**、零引用行数合计
**16356** 行——即大部分代码没有任何测试触达。

issue 的判据（本工具头部登记，供维护者对齐）
--------------------------------------------
  §一 口径：故意**宽松、保守**——只做文本词边界匹配 `re.search(r"\\b" + basename
      + r"\\b", tests_text)`，命中即算"被引用"。因此"零引用"是**下界**：
      被提及但没真跑到的模块不会进清单（宁可漏判零覆盖，不误报）。
  §二 建议的最小覆盖门：**新模块未测即红**——新增模块若不在白名单里又零引用，
      守卫报红，逼新代码自带测试（或显式登记白名单）。
      ★本工具/守卫的第 4 条判据（基线外出现新的零引用模块 ⇒ 红）正是这条。

★本批（P2 批次 F）只做 **上尺子 ＋ 定基线**：
  · 新增本工具与基线 `tools/coverage_map_lint_baseline.txt`（**零引用模块清单**，
    不是只落一个数字——清单才能判"有没有新增"）；
  · 新增守卫 `tests/test_coverage_map_lint.py`（基线外新零引用模块 ⇒ 红）；
  · **本批不补测试、不改被扫代码**；issue 里"按风险的优先清单"是整改建议，
    本批**不做**（那是另一个量级，需按模块风险逐件裁量）。

检测口径
--------
  · 模块集合 = `<仓库根>/lingshu/**/*.py`（**含 `__init__.py`**——它们是包内模块）；
  · 模块名 = basename 去 `.py`；被引用判定 = 该名字以**词边界**出现在
    `tests/*.py` 合并文本里（`\\b<name>\\b`）；
  · 零引用 = 从未出现；按模块**行数**降序（并列按路径）输出。

已知局限（如实登记）
--------------------
L1 纯文本匹配，**不**做导入图分析：测试里写 `import lingshu.core.foo as F`、
   或在字符串/注释里提到 basename，都算"被引用"（假阴——漏判零覆盖）。
   这是 issue 明说的**保守取向**，本工具刻意保持。
L2 同名 basename 共享判定：`lingshu/core/time_core.py` 与 `lingshu/world/time_core.py`
   同名，任一处被提到即两者都算"被引用"（同理 5 个 `__init__.py`、2 个
   `hex_composite.py`）。故清单以**路径**列出、但判定以 basename 进行。
L3 行数 = 该文件物理行数（`splitlines()`），非逻辑行/有效行。
L4 解析失败的文件不影响本工具（只做文本匹配，不 import）；文件读取失败如实报错退出 2。

用法
----
    python tools/coverage_map_lint.py [--json] [--baseline FILE]
                                      [--update-baseline] [--quiet] [--repo-root PATH]
    默认模块根 = <仓库根>/lingshu ；默认测试根 = <仓库根>/tests
    默认 baseline = <仓库根>/tools/coverage_map_lint_baseline.txt
    仓库根 = 本脚本上两级目录（由 __file__ 推导，**不依赖 cwd**）
    `--baseline` 的相对路径**按仓库根解析**；绝对路径原样使用。

baseline 格式
-------------
    每行 `<仓内相对路径>\t<行数>`；`#` 起为注释；空行忽略。
    语义：**允许存在的零引用模块白名单**。基线之外的零引用模块 ⇒ 退出 1。

退出码（冻结）
--------------
0  无新增零引用模块（基线之外的零引用模块数为 0）
1  出现基线之外的零引用模块（新模块未测 ⇒ 红）
2  用法 / 路径错误（模块根或测试根不存在、baseline 格式错、显式 baseline 缺失、
   `--update-baseline` 与 `--json` 同用；argparse 自身错误同样返回 2）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

LINT_VERSION = 1

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODULE_DIR = "lingshu"
DEFAULT_TESTS_DIR = "tests"
DEFAULT_BASELINE_REL = "tools/coverage_map_lint_baseline.txt"

# 「登记面」用户登记段标记（--update-baseline 逐字保留其间内容；工具不解析、不豁免）
REG_BEGIN = "# ── 登记面 · 用户登记（--update-baseline 保留本段逐字）──"
REG_END = "# ── 登记面 · 结束 ──"


# ---------------------------------------------------------------- 扫描
def module_name(path: Path) -> str:
    return path.stem


def is_referenced(name: str, tests_text: str) -> bool:
    return re.search(r"\b" + re.escape(name) + r"\b", tests_text) is not None


def count_lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8-sig", errors="replace").splitlines())


def scan(root: Path, module_dir: Path, tests_dir: Path) -> Tuple[List[Dict], List[str]]:
    errors: List[str] = []
    if not module_dir.is_dir():
        errors.append("模块根不存在：%s" % module_dir)
        return [], errors
    if not tests_dir.is_dir():
        errors.append("测试根不存在：%s" % tests_dir)
        return [], errors

    tests_text = "\n".join(
        p.read_text(encoding="utf-8-sig", errors="replace")
        for p in sorted(tests_dir.glob("*.py")))
    modules = sorted(module_dir.rglob("*.py"), key=lambda q: str(q))

    def rel(p: Path) -> str:
        try:
            return p.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return Path(os.path.abspath(str(p))).as_posix()

    zero: List[Dict] = []
    for p in modules:
        name = module_name(p)
        if is_referenced(name, tests_text):
            continue
        try:
            lines = count_lines(p)
        except OSError as exc:
            errors.append("无法读取 %s：%s" % (rel(p), exc))
            continue
        zero.append({"file": rel(p), "name": name, "lines": lines})
    zero.sort(key=lambda z: (-z["lines"], z["file"]))
    return zero, errors


# ---------------------------------------------------------------- baseline
def parse_baseline(text: str) -> Tuple[Dict[str, int], List[str]]:
    entries: Dict[str, int] = {}
    errors: List[str] = []
    for no, raw in enumerate(text.split("\n"), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2:
            errors.append("第 %d 行无法解析（期望 <路径> <行数>）：%s" % (no, raw.strip()))
            continue
        path, num = parts
        if not num.isdigit():
            errors.append("第 %d 行行数非整数：%s" % (no, raw.strip()))
            continue
        entries[path.replace("\\", "/")] = int(num)
    return entries, errors


def _extract_registration(existing_text: Optional[str]) -> List[str]:
    """取出「用户登记段」逐字内容（--update-baseline 保留）；无标记则返回空。"""
    if not existing_text:
        return []
    lines = existing_text.split("\n")
    try:
        i = next(k for k, ln in enumerate(lines) if REG_BEGIN in ln)
        j = next(k for k, ln in enumerate(lines) if REG_END in ln and k > i)
    except StopIteration:
        return []
    return lines[i + 1:j]


def render_baseline(zero: Sequence[Dict],
                    existing_text: Optional[str] = None) -> str:
    lines = [
        "# coverage_map_lint baseline —— 已登记的零引用（零覆盖）模块白名单（issue #23）",
        "# 格式：<仓内相对路径><TAB><行数>   （`#` 起为注释）",
        "# 语义：**允许存在**的零引用模块。基线之外出现零引用模块 ⇒ 退出 1（新模块未测即红）。",
        "# 基线来源：HEAD b846086 实测（python -X utf8 tools/coverage_map_lint.py --update-baseline）。",
        "#   口径 = 文本词边界匹配 basename（宽松下界，见工具头部 L1）；重生成后请更新上一行 HEAD 号。",
        "#   两组读数对表：issue 原基线 a215d8b = 53 模块 / 37 零引用 / 16356 行；本 worktree b846086 实测",
        "#     = 唯一 basename 55（文件计 61，含 5 __init__；编排侧按 61-5=56 文件计）/ 零引用 2 / 289 行。",
        "#     两口径**零引用模块同为 2**（hex_recon/hex_unit 的 basename 唯一，折叠无影响）。",
        "# ★基线可移植性：本值是 b846086 快照。合入其它在飞批次后若出现新零引用模块，",
        "#   先判因——真·新增未测模块 ⇒ 补测试（推荐）或登记白名单；并在下方登记段追加一行带日期记录。",
        "# 口径 / 已知局限 / 退出码见 tools/coverage_map_lint.py 头部。",
        "",
        "# ── 登记面（documented-exemption surface；★工具**不解析、不豁免**，仅作文档）──────",
        "# 格式：<日期> <commit> <路径>  <理由>；本段不被工具解析，仅作文档。",
        REG_BEGIN,
    ]
    lines.extend(_extract_registration(existing_text))
    lines.append(REG_END)
    lines.append("")
    for z in sorted(zero, key=lambda z: z["file"]):
        lines.append("%s\t%d" % (z["file"], z["lines"]))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 主流程
def _load_baseline_file(path: Path, explicit: bool
                        ) -> Tuple[Dict[str, int], List[str], bool]:
    if not path.exists():
        if explicit:
            return {}, ["baseline 文件不存在：%s" % path], False
        return {}, [], False
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    entries, errors = parse_baseline(text)
    return entries, errors, True


def run(argv: Optional[Sequence[str]] = None) -> Tuple[int, str, Dict]:
    parser = argparse.ArgumentParser(
        prog="coverage_map_lint.py",
        description="零覆盖模块映射审计器（issue #23；只上尺子＋定基线）")
    parser.add_argument("--json", action="store_true", help="输出确定性 JSON（sort_keys=True）")
    parser.add_argument("--baseline", metavar="FILE", default=None,
                        help="baseline 文件（默认 <仓库根>/%s）" % DEFAULT_BASELINE_REL)
    parser.add_argument("--update-baseline", action="store_true",
                        help="把当前零引用模块清单写回 baseline")
    parser.add_argument("--quiet", action="store_true", help="只打印新增零引用模块与摘要")
    parser.add_argument("--repo-root", metavar="PATH", default=str(REPO_ROOT),
                        help="仓库根（默认由 __file__ 推导）")
    parser.add_argument("--module-dir", metavar="PATH", default=None,
                        help="包内模块根（默认 <仓库根>/%s）" % DEFAULT_MODULE_DIR)
    parser.add_argument("--tests-dir", metavar="PATH", default=None,
                        help="测试根（默认 <仓库根>/%s）" % DEFAULT_TESTS_DIR)
    args = parser.parse_args(argv)

    root = Path(args.repo_root).resolve()
    if not root.is_dir():
        return 2, "仓库根不存在：%s\n" % root, {"exit_code": 2}
    module_dir = Path(args.module_dir) if args.module_dir else root / DEFAULT_MODULE_DIR
    tests_dir = Path(args.tests_dir) if args.tests_dir else root / DEFAULT_TESTS_DIR

    zero, errors = scan(root, module_dir, tests_dir)
    if errors:
        return 2, "\n".join(errors) + "\n", {"exit_code": 2}

    if args.baseline is None:
        baseline_path = root / DEFAULT_BASELINE_REL
        explicit_baseline = False
    else:
        arg_path = Path(args.baseline)
        baseline_path = arg_path if arg_path.is_absolute() else root / arg_path
        explicit_baseline = True
    baseline_entries, baseline_errors, baseline_found = _load_baseline_file(
        baseline_path, explicit_baseline)
    if baseline_errors:
        return 2, "\n".join(baseline_errors) + "\n", {"exit_code": 2}

    zero_paths = {z["file"] for z in zero}
    new = [z for z in zero if z["file"] not in baseline_entries]
    stale = sorted(p for p in baseline_entries if p not in zero_paths)

    exit_code = 1 if new else 0
    payload = {
        "baseline_entries": len(baseline_entries),
        "baseline_file": _rel(baseline_path, root),
        "baseline_found": baseline_found,
        "exit_code": exit_code,
        "new_zero_ref": new,
        "stale_baseline_entries": stale,
        "summary": {
            "new": len(new),
            "zero_ref_lines": sum(z["lines"] for z in zero),
            "zero_ref_modules": len(zero),
            "max_zero_ref": (zero[0]["file"] if zero else None),
        },
        "zero_ref_modules": zero,
        "version": LINT_VERSION,
    }

    if args.update_baseline:
        if args.json:
            return 2, "--update-baseline 不能与 --json 同用\n", {"exit_code": 2}
        text = render_baseline(
            zero,
            existing_text=(baseline_path.read_text(encoding="utf-8-sig", errors="replace")
                           if baseline_path.exists() else None))
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(text, encoding="utf-8")
        msg = "" if args.quiet else (
            "已写入 %d 个零引用模块 → %s\n" % (len(zero), baseline_path))
        return 0, msg, payload

    if args.json:
        return exit_code, json.dumps(payload, sort_keys=True,
                                     ensure_ascii=False, indent=2) + "\n", payload

    lines: List[str] = []
    if not baseline_found and not args.quiet:
        lines.append("提示：baseline 不存在（%s），本次按空白名单运行——所有零引用模块都算新增"
                     % _rel(baseline_path, root))
    if not args.quiet:
        lines.append("零引用（零覆盖）模块：")
        for z in zero:
            mark = "" if z["file"] in baseline_entries else "  ← [新]"
            lines.append("  %-52s %6d 行%s" % (z["file"], z["lines"], mark))
    for z in new:
        lines.append("[新] 零引用模块：%s（%d 行）——未测且未登记白名单"
                     % (z["file"], z["lines"]))
    if not args.quiet:
        lines.append("")
        lines.append("零引用模块 %d 个（合计 %d 行）；基线白名单 %d 个；新增 %d"
                     % (len(zero), payload["summary"]["zero_ref_lines"],
                        len(baseline_entries), len(new)))
        if stale:
            lines.append("基线中已失效条目 %d 个（可清理）：%s" % (len(stale), ", ".join(stale)))
        lines.append("退出码 %d（%s）" % (exit_code, "出现新零引用模块" if new else "无新增"))
    return exit_code, "\n".join(lines) + ("\n" if lines else ""), payload


def _rel(p: Path, root: Path) -> str:
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return Path(os.path.abspath(str(p))).as_posix()


def main(argv: Optional[Sequence[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", newline="\n")
        except Exception:      # pragma: no cover - 平台相关
            pass
    code, out, _payload = run(argv)
    if out:
        sys.stdout.write(out)
    return code


if __name__ == "__main__":
    sys.exit(main())

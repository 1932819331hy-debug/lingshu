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
                                      [--update-baseline [--force]] [--quiet] [--repo-root PATH]
    默认模块根 = <仓库根>/lingshu ；默认测试根 = <仓库根>/tests
    默认 baseline = <仓库根>/tools/coverage_map_lint_baseline.txt
    仓库根 = 本脚本上两级目录（由 __file__ 推导，**不依赖 cwd**）
    `--baseline` 的相对路径**按仓库根解析**；绝对路径原样使用。

baseline 格式
-------------
    每行 `<仓内相对路径>\t<行数>`；`#` 起为注释；空行忽略。
    语义：**允许存在的零引用模块白名单**——条目按「路径＋行数」**双 keyed**（#614）：
      · 基线之外出现零引用模块 ⇒ 退出 1（新模块未测 ⇒ 红）；
      · 条目已不再是零引用（stale：模块已被测试提到/文件已删）⇒ 退出 1，
        读数行提示「基线含已失效条目，请清理后 --update-baseline」（清 stale 是缩白名单）；
      · 条目路径仍零引用但**登记行数 ≠ 实测行数**（漂移：同名重建/行数漂移）⇒ 退出 1
        ——路径+行数双 keyed 堵死「文件删除后同名重建（全新未测代码）路径相同即豁免」；
      · 路径与行数都一致 ⇒ 按现状豁免。

退出码
------
0  白名单全部有效：无新增零引用模块、无 stale 条目、无行数漂移
1  ① 出现基线之外的零引用模块（新模块未测 ⇒ 红）；
   ② 基线含已失效（stale）条目（#614：stale 入判定）；
   ③ 条目登记行数与实测零引用行数漂移（#614：行数参与比较）
2  用法 / 路径错误（模块根或测试根不存在、baseline 格式错、显式 baseline 缺失、
   `--update-baseline` 与 `--json` 同用；argparse 自身错误同样返回 2）
3  `--update-baseline` 拒绝写入：白名单规模增长（新条目数 > 旧 baseline 条目数）。
   #23 §3.1「白名单是冻结的债务清单，不是通行证」的落地——**断言白名单规模单调不增**；
   确需扩容（新登记零引用债务）须显式 `--force` 放行（缩水含清 stale 不受限，退出 0）。
   （原 0/1/2 语义冻结于 issue #23 批次 F；#614 扩充 1 的判定面并新增 3。）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# v1（#23 批次 F）：只上尺子＋定基线，exit 只看「基线外新零引用模块」。
# v2（#614）：stale 入判定（exit 1）；登记行数参与比较（漂移 ⇒ exit 1）；
#            --update-baseline 头部读数按当次实测生成＋白名单规模单调不增（增长 ⇒ exit 3 / --force）。
LINT_VERSION = 2

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


def _git_head(root: Path) -> Optional[str]:
    """取仓库根的 HEAD 短号（--update-baseline 头部溯源用）；非 git 环境/失败 ⇒ None。"""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True, timeout=10)
    except Exception:          # pragma: no cover - 平台相关（git 缺失/超时）
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", errors="replace").strip() or None


def render_baseline(zero: Sequence[Dict],
                    existing_text: Optional[str] = None,
                    head: Optional[str] = None) -> str:
    """渲染 baseline 全文。

    #614：头部读数＝**当次实测真值**（HEAD 号、零引用模块数、合计行数），
    不再是模板常量——杜绝「头部写着 hex_recon/hex_unit 与 289 行，实测却是
    tms/hex_recon 与 307 行」这类硬编码溯源与现状矛盾（issue #614 场景 3）。
    """
    n = len(zero)
    total = sum(z["lines"] for z in zero)
    head_label = head if head else "未知（git 不可用或非 git 环境）"
    lines = [
        "# coverage_map_lint baseline —— 已登记的零引用（零覆盖）模块白名单（issue #23）",
        "# 格式：<仓内相对路径><TAB><行数>   （`#` 起为注释）",
        "# 语义：**允许存在**的零引用模块白名单（路径＋行数双 keyed，#614）：",
        "#   基线之外出现零引用模块 ⇒ 退出 1；条目已不再是零引用（stale）⇒ 退出 1；",
        "#   条目登记行数与实测不符（漂移）⇒ 退出 1。路径与行数一致 ⇒ 豁免。",
        "# 基线来源：HEAD %s 实测（python -X utf8 tools/coverage_map_lint.py --update-baseline）。" % head_label,
        "#   本快照实测：零引用 %d 模块 / %d 行（口径 = 文本词边界匹配 basename，宽松下界，见工具头部 L1）。" % (n, total),
        "#   本头部由工具按当次实测自动生成（#614：不再是模板常量）；重生成即自动更新上行读数。",
        "# ★白名单规模单调不增（#23 §3.1「冻结的债务清单，不是通行证」的落地产物，#614）：",
        "#   --update-baseline 在条目数增长时拒绝（退出 3），需显式 --force；缩水（含清 stale）不受限。",
        "# ★基线可移植性：合入其它在飞批次后若出现新零引用模块，先判因——",
        "#   真·新增未测模块 ⇒ 补测试（推荐）或登记白名单；并在下方登记段追加一行带日期记录。",
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
                        help="把当前零引用模块清单写回 baseline（白名单规模增长时拒绝，见 --force）")
    parser.add_argument("--force", action="store_true",
                        help="与 --update-baseline 同用：放行白名单规模增长（#23 §3.1 单调不增的显式例外）")
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

    zero_by_path = {z["file"]: z for z in zero}
    new = [z for z in zero if z["file"] not in baseline_entries]
    stale = sorted(p for p in baseline_entries if p not in zero_by_path)
    # #614：登记行数参与比较——路径命中但行数不符 ⇒ 漂移（同名重建/行数漂移不再豁免）
    drifted = sorted(
        (dict(z, registered_lines=baseline_entries[z["file"]])
         for z in zero
         if z["file"] in baseline_entries and baseline_entries[z["file"]] != z["lines"]),
        key=lambda z: z["file"])

    # #614：stale 与漂移都入判定（原先 exit 只看 new——失效/漂移条目被算出来却不影响结果）
    exit_code = 1 if (new or stale or drifted) else 0
    payload = {
        "baseline_entries": len(baseline_entries),
        "baseline_file": _rel(baseline_path, root),
        "baseline_found": baseline_found,
        "drifted_baseline_entries": drifted,
        "exit_code": exit_code,
        "new_zero_ref": new,
        "stale_baseline_entries": stale,
        "summary": {
            "new": len(new),
            "stale": len(stale),
            "drifted": len(drifted),
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
        # #614：白名单规模单调不增（#23 §3.1）——增长（新条目数 > 旧条目数）拒绝写入，
        # 退出 3；显式 --force 才放行。缩水（含清 stale）不受限。
        if len(zero) > len(baseline_entries) and not args.force:
            msg = ("拒绝写入 %s：白名单规模增长（旧 %d 条 → 新 %d 条）——"
                   "#23 §3.1「白名单是冻结的债务清单，不是通行证」，断言白名单规模单调不增。\n"
                   "  请先为新增零引用模块补测试（推荐）；确需登记债务，加 --force 显式放行。\n"
                   % (baseline_path, len(baseline_entries), len(zero)))
            if not args.quiet:
                msg += "  （清理 stale/漂移条目属缩白名单，不受本限制。）\n"
            return 3, msg, payload
        text = render_baseline(
            zero,
            existing_text=(baseline_path.read_text(encoding="utf-8-sig", errors="replace")
                           if baseline_path.exists() else None),
            head=_git_head(root))
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(text, encoding="utf-8")
        msg = "" if args.quiet else (
            "已写入 %d 个零引用模块 → %s\n" % (len(zero), baseline_path))
        if len(zero) > len(baseline_entries) and args.force and not args.quiet:
            msg += ("警告：白名单规模增长（旧 %d 条 → 新 %d 条）——#23 §3.1 单调不增，"
                    "本次因 --force 显式放行。\n" % (len(baseline_entries), len(zero)))
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
            if z["file"] not in baseline_entries:
                mark = "  ← [新]"
            elif baseline_entries[z["file"]] != z["lines"]:
                mark = "  ← [漂移]"
            else:
                mark = ""
            lines.append("  %-52s %6d 行%s" % (z["file"], z["lines"], mark))
    for z in new:
        lines.append("[新] 零引用模块：%s（%d 行）——未测且未登记白名单"
                     % (z["file"], z["lines"]))
    for z in drifted:
        lines.append("[漂移] 白名单条目行数不符：%s（登记 %d 行 / 实测 %d 行）——"
                     "路径命中但行数漂移，视为未登记（#614：同名重建/行数漂移不再豁免）"
                     % (z["file"], z["registered_lines"], z["lines"]))
    if stale:
        lines.append("[失效] 基线含已失效条目 %d 个（请清理后 --update-baseline）：%s"
                     % (len(stale), ", ".join(stale)))
    if not args.quiet:
        lines.append("")
        lines.append("零引用模块 %d 个（合计 %d 行）；基线白名单 %d 个；新增 %d；失效 %d；漂移 %d"
                     % (len(zero), payload["summary"]["zero_ref_lines"],
                        len(baseline_entries), len(new), len(stale), len(drifted)))
        reasons = []
        if new:
            reasons.append("出现新零引用模块")
        if stale:
            reasons.append("基线含已失效条目（请清理后 --update-baseline）")
        if drifted:
            reasons.append("白名单条目行数漂移")
        lines.append("退出码 %d（%s）" % (exit_code, "；".join(reasons) if reasons else "无新增"))
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

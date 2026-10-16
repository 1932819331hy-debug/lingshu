# -*- coding: utf-8 -*-
"""test_public_docs_privacy_guard · 公开面设计文档（docs/plans/**）入库隐私闸
============================================================================
背景（2026-10-17 取证）：
    仓内已有「双清单审计」约定（见 docs/intake/*/AUDIT.md）——
      内容政策：凭据/令牌、本机绝对路径、邮箱、本地 IP
      隐私面  ：记忆数据、私有模块名、私有资产引用
    但该约定只对**导出批**逐批执行（docs/intake/** 与随批新增面），
    docs/plans/** 这类**设计文档面**从未纳入机械扫描 —— 于是私有仓名与
    私有部署环境变量名经设计稿流入公开面（本次取证：2 文件 6 处），
    且仓内**没有任何**能拦住它的入库闸（全仓无 POLICY_DENY / policy_scan 实现）。
    本件即补该闸。

判据（本件，docs/plans/**/*.md 正文逐行）：
    A 凭据/令牌  ：sk-… / ghp_… / tk_<hex> / mdcg1. / (api_key|secret|password|token) 赋值
    B 本机绝对路径：盘符 + 分隔符、UNC、POSIX 家目录与挂载根
    C 邮箱
    D 本地 IP
    E 私有面标识 ：私有仓名及其环境变量前缀（口径见下「边界」）

边界（如实，防本件被读成「全仓已干净」）：
    · 扫描面**仅** docs/plans/**。同一私有标识在**源码面是公开接口**
      （lingshu/core/core.py 以该环境变量名读取），仓内 60+ 文件含它；
      本件**不**对其判红 —— 该面是否收口是独立议题，不在本件范围。
    · 本件自身含该标识字面量（判据表 E 类）。若日后放宽扫描面至仓根，
      须同时排除本件路径，否则自触。
    · 本件只做**机械**判据；「私有模块名」这类需人判的项仍走 AUDIT.md 人工复核。

量具自检（同族纪律，防恒绿）：
    · 扫描器必须能从**合成文本**读出每一类命中 —— test_scanner_detects_known_positives；
    · 对合法文本不得误杀 —— test_no_false_positive_on_benign_text；
    · 扫描面不得为空（空集恒绿是坏扫描器掩盖的假绿）—— test_scan_surface_is_nonempty；
    · 本件自身必须可被 pytest 收集（否则落在 gate 盲区）—— test_guard_itself_is_collectable。

运行：pytest tests/test_public_docs_privacy_guard.py -q
      （或 python -X utf8 tests/test_public_docs_privacy_guard.py 直接打报告）
"""
from __future__ import annotations

import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN_ROOT = os.path.join(REPO, "docs", "plans")

#: A 凭据/令牌 —— 口径逐字对齐 docs/intake/brain-store-v0.1/AUDIT.md 判据表，另补 ghp_。
_CRED = (
    ("sk_token", re.compile(r"sk-[A-Za-z0-9_-]{16,}")),
    ("gh_token", re.compile(r"ghp_[A-Za-z0-9]{20,}")),
    ("tk_token", re.compile(r"tk_[0-9a-fA-F]{16,}")),
    ("mdcg_token", re.compile(r"mdcg1\.[A-Za-z0-9]{8,}")),
    ("kv_secret", re.compile(r"(?i)(?:api[_-]?key|secret|password|passwd|token)\s*[=:]\s*\S")),
)

#: B 本机绝对路径 —— 盘符腿带左界，防 class _Block:\n 这类伪影（先例见 intake AUDIT 第 3 条）。
_PATH = (
    ("win_drive", re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]")),
    ("unc", re.compile(r"\\\\[A-Za-z0-9._-]+\\")),
    ("posix_home", re.compile(r"(?:/home|/Users|/root|/mnt)/[A-Za-z0-9._-]+/")),
)

#: C 邮箱
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

#: D 本地 IP —— 各私有段的**段数不同**（10 是 1 段前缀、192.168 与 172.x 是 2 段），
#: 故分开写；一律要求**完整地址**，避免把 10.0.0 / 1.12.1 这类版本号误判。
_IP = re.compile(
    r"(?<![\d.])(?:"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|127\.0\.0\.1"
    r")(?![\d.])"
)

#: E 私有面标识 —— 私有仓名及其全部环境变量/常量前缀。
#: 声明面：本仓 docs/intake/*/README.md 与 docs/architecture.md 以该名**公开**标注
#: 导出来源（审计判 CLEAN）；本件只在 docs/plans/** 面收紧。调整口径只改本行。
_PRIVATE_NAME = re.compile(r"(?<![A-Za-z0-9_])AEIS[A-Za-z0-9_]*")

_CATEGORIES = (
    ("凭据/令牌", _CRED),
    ("本机绝对路径", _PATH),
)


def _md_files(root):
    """递归列出 root 下 .md（跳过 __pycache__）；空集须由调用方显式判红。"""
    out = []
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d != "__pycache__"]
        for f in sorted(fn):
            if f.lower().endswith(".md"):
                out.append(os.path.join(dp, f))
    return sorted(out)


def _read(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _violations_in_text(text):
    """单份文本 -> [(类别, 行号, 行内容)]。量具自检直接喂合成文本。"""
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        cat = None
        for name, pats in _CATEGORIES:
            if any(p.search(line) for _n, p in pats):
                cat = name
                break
        if cat is None:
            if _EMAIL.search(line):
                cat = "邮箱"
            elif _IP.search(line):
                cat = "本地IP"
            elif _PRIVATE_NAME.search(line):
                cat = "私有面标识"
        if cat is not None:
            out.append((cat, i, line.strip()))
    return out


def _violations(root=None):
    """扫描面全量 -> [(相对路径, 行号, 类别, 行内容)]。"""
    root = SCAN_ROOT if root is None else root
    out = []
    for p in _md_files(root):
        rel = os.path.relpath(p, root)
        for cat, ln, txt in _violations_in_text(_read(p)):
            out.append((rel, ln, cat, txt))
    return out


def _report(root=None):
    v = _violations(root)
    if not v:
        return 0, "docs/plans/** 隐私闸：0 命中（CLEAN）"
    lines = ["docs/plans/** 隐私闸：%d 命中" % len(v)]
    for rel, ln, cat, txt in v:
        lines.append("  %s L%d [%s] %s" % (rel, ln, cat, txt[:120]))
    return 1, "\n".join(lines)


# ------------------------------------------------------------------ 断言组

def test_scan_surface_is_nonempty():
    """扫描面为空 = 空集恒绿，是坏扫描器掩盖的假绿。"""
    assert os.path.isdir(SCAN_ROOT), "扫描面不存在：%s" % SCAN_ROOT
    files = _md_files(SCAN_ROOT)
    assert files, "扫描面下无 .md —— 拒绝以空集判绿"


def test_scanner_detects_known_positives():
    """量具自检：每一类都必须能从合成文本读出，否则 0 命中不可信。"""
    samples = (
        ("凭据/令牌", "key = sk-abcdefghijklmnopqrst"),
        ("凭据/令牌", "ghp_" + "a" * 30),
        ("凭据/令牌", "tk_" + "0" * 20),
        ("凭据/令牌", "mdcg1.abcdefgh"),
        ("凭据/令牌", "api_key=xxx"),
        ("凭据/令牌", "password: yyy"),
        ("本机绝对路径", r"D:\program\x"),
        ("本机绝对路径", "C:/tmp/x"),
        ("本机绝对路径", r"\\srv\share"),
        ("本机绝对路径", "/home/alice/proj/f"),
        ("邮箱", "a@b.com"),
        ("本地IP", "192.168.1.5"),
        ("本地IP", "10.1.2.3"),
        ("本地IP", "127.0.0.1"),
        ("私有面标识", "私域 AEIS 镜像"),
        ("私有面标识", "AEIS_DESIGNER_KEY"),
        ("私有面标识", "AEIS_DB"),
    )
    for want, s in samples:
        got = {c for c, _i, _t in _violations_in_text(s)}
        assert want in got, "扫描器读不出 [%s] 类命中：%r -> %s" % (want, s, got)


def test_no_false_positive_on_benign_text():
    """防误杀：仓内真实存在的合法文本不得被判红。"""
    benign = (
        "16:9 横屏画幅（ASPECT_16_9）",
        r"class _Block:\n",
        "版本 10.0.0 / 1.12.1",
        "PRAISE 词族（不含私有标识）",
        "docs/plans/待裁清单_v0.1.md",
        "见 §4.2 E 族表 E1 · D-007 fail-closed",
        "https://github.com/FuRongJun-1999/lingshu/pull/737",
        "0.80 / 0.90 安全区 · 1920x1080",
        "| 2026-10-09 | 引入件 | CLEAN |",
    )
    for s in benign:
        v = _violations_in_text(s)
        assert v == [], "误杀：%r -> %s" % (s, v)


def test_docs_plans_has_no_violations():
    """主判据：docs/plans/** 全量 0 命中。"""
    v = _violations()
    assert v == [], "docs/plans/** 命中 %d 处：\n%s" % (
        len(v), "\n".join("  %s L%d [%s] %s" % t for t in v))


def test_guard_itself_is_collectable():
    """自证：本件必须能被 pytest 收集，否则它自己就落在 gate 盲区。"""
    assert os.path.basename(__file__).startswith("test_")
    assert os.path.isfile(os.path.abspath(__file__))
    for fn in (test_scan_surface_is_nonempty, test_scanner_detects_known_positives,
               test_no_false_positive_on_benign_text, test_docs_plans_has_no_violations):
        assert callable(fn)


if __name__ == "__main__":
    _code, _text = _report()
    print(_text)
    sys.exit(_code)

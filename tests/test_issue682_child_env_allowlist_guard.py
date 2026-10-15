# -*- coding: utf-8 -*-
"""test_issue682_child_env_allowlist_guard · repro-bot 子进程 env 清洗 denylist → allowlist（#682）
================================================================================================
#682（P1 安全面）：`scripts/extract_and_run_issue_repro.py::_child_env` 旧实现是
「**默认透传 + 词表丢弃**」——词表 `_ENV_DROP_WORDS = (TOKEN|SECRET|PASSWORD|
CREDENTIAL)` 不含 `KEY`/`URL`/`URI`/`DSN`，且盖不住 `SSH_AUTH_SOCK`（直通
ssh-agent）/ `KUBECONFIG`（直通集群）/ `DOCKER_HOST` / `NETRC` / `*_PROXY` 这类
**能力凭据** ⇒ 被测（不可信）脚本原样拿到 `AEIS_DESIGNER_KEY` / `*_API_KEY` /
`AWS_ACCESS_KEY_ID` / `DATABASE_URL` 等。

修法（判据）：改为「**默认丢弃 + 白名单透传**」——只透传 `_ENV_ALLOW_EXACT`
（具名，进程运行基座）与 `_ENV_ALLOW_PREFIXES`（仅 `LC_`）命中的键，其余继承键
一律丢弃。`PYTHONUTF8`/`PYTHONIOENCODING` 强制注入、`extra` 覆盖语义不变。

本守卫三面：
  G1 凭据/能力形状键**一个都不透传**（含旧词表漏掉的一整类）；
  G2 白名单键（进程运行基座）仍透传——被测脚本要能跑起来；
  G3 `extra` 仍能覆盖白名单内外的键（既有契约）。

变异点：把 `_child_env` 还原成 denylist 版（默认透传 + 词表丢弃）⇒ G1 全红。

运行（仓根）：python -X utf8 -m pytest tests/test_issue682_child_env_allowlist_guard.py -q --no-header
退出码：0 = 全过；1 = 有断言失败。
"""
from __future__ import annotations

import importlib.util
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(REPO, "scripts", "extract_and_run_issue_repro.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("_repro_exec_682", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_MOD = _load_module()

#: 凭据 / 能力形状键（**必须不透传**）——覆盖旧词表漏掉的一整类。
_CREDENTIAL_KEYS = [
    # 旧词表已盖住的（回归面）
    "SOME_TOKEN", "SOME_SECRET", "SOME_PASSWORD", "SOME_CREDENTIAL",
    # 旧词表漏掉的 KEY / URL / URI / DSN 类
    "AEIS_DESIGNER_KEY", "OPENAI_API_KEY", "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY", "DATABASE_URL", "REDIS_URL", "MONGO_URI",
    "PG_DSN", "ZAI_OAUTH_CLIENT_ID",
    # 能力凭据（直通外部 agent/集群/代理/仓库）
    "SSH_AUTH_SOCK", "KUBECONFIG", "DOCKER_HOST", "NETRC", "_NETRC",
    "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "HTTP_PROXY", "HTTPS_PROXY",
    "NO_PROXY", "ALL_PROXY", "GIT_ASKPASS", "GITHUB_TOKEN", "GH_TOKEN",
    "MDCG_TOKEN", "MDCG_VERIFY_KEY",
]

#: 白名单·进程运行基座键（**必须透传**）——被测脚本要能跑起来。
_ALLOWED_KEYS = [
    "PATH", "PATHEXT", "COMSPEC", "SYSTEMROOT", "TEMP", "TMP", "HOME",
    "USERPROFILE", "LANG", "NUMBER_OF_PROCESSORS", "OS",
    "PROCESSOR_ARCHITECTURE", "WINDIR", "APPDATA", "LOCALAPPDATA",
    "USERNAME", "PYTHONPATH",
]


def _probe_env(extra=None):
    """塞入全部凭据哨兵 + 全部白名单键，返回 `_child_env(extra)` 的读数。

    用 `os.environ` 就地注入（`_child_env` 读 `os.environ`），跑完恢复原值，
    避免污染同进程其它用例。
    """
    sentinels = {}
    for k in _CREDENTIAL_KEYS + _ALLOWED_KEYS:
        sentinels[k] = os.environ.get(k)
        os.environ[k] = "sentinel-" + k
    try:
        return _MOD._child_env(extra or {})
    finally:
        for k, old in sentinels.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old


# ── G1 凭据 / 能力形状键一个都不透传 ────────────────────────────────────────
def test_682_g1_credential_keys_never_pass_through():
    """G1：全部凭据/能力形状键不得出现在子进程 env 里。

    变异点：`_child_env` 还原成 denylist 版 ⇒ `AEIS_DESIGNER_KEY` /
    `AWS_ACCESS_KEY_ID` / `DATABASE_URL` / `SSH_AUTH_SOCK` / `KUBECONFIG` 等
    原样透传 ⇒ 本断言红。
    """
    env = _probe_env()
    leaked = sorted(k for k in _CREDENTIAL_KEYS if k in env)
    assert not leaked, (
        "#682：凭据/能力形状键被透传给被测子进程：%s" % leaked)


def test_682_g1_old_denylist_words_still_blocked():
    """G1 回归面：旧词表（TOKEN|SECRET|PASSWORD|CREDENTIAL）盖住的键仍不得透传。"""
    env = _probe_env()
    for k in ("SOME_TOKEN", "SOME_SECRET", "SOME_PASSWORD", "SOME_CREDENTIAL"):
        assert k not in env, "#682：%s 仍被透传（旧词表已盖住，白名单必须更强）" % k


# ── G2 白名单键透传（被测脚本要能跑起来） ──────────────────────────────────
def test_682_g2_allowlisted_base_keys_pass_through():
    """G2：进程运行基座键必须透传——否则被测脚本起不来（PATH/临时目录/家目录…）。"""
    env = _probe_env()
    missing = sorted(k for k in _ALLOWED_KEYS if k not in env)
    assert not missing, (
        "#682：白名单键未透传（被测脚本可能起不来）：%s" % missing)


def test_682_g2_whitelist_has_no_credential_shape_names():
    """G2：白名单本身不得含凭据形状名（防「补白名单」时误放凭据）。"""
    for name in _MOD._ENV_ALLOW_EXACT:
        up = name.upper()
        for bad in ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "KEY",
                    "PROXY", "AUTH", "NETRC", "DSN", "URL", "URI"):
            assert bad not in up, (
                "#682：白名单具名含凭据形状词 %r（键 %r）" % (bad, name))
    assert _MOD._ENV_ALLOW_PREFIXES == ("LC_",), (
        "#682：白名单前缀应收敛为仅 LC_，实得 %r" % (_MOD._ENV_ALLOW_PREFIXES,))


# ── G3 强制注入与 extra 覆盖语义不变（既有契约） ───────────────────────────
def test_682_g3_forced_utf8_and_extra_override():
    """G3：`PYTHONUTF8`/`PYTHONIOENCODING` 强制注入；`extra` 覆盖白名单内外键。"""
    env = _probe_env({"PATH": "/custom/bin", "FOO": "bar",
                      "PYTHONIOENCODING": "cp936"})
    assert env.get("PYTHONUTF8") == "1", "#682：PYTHONUTF8 强制注入被破坏"
    assert env.get("PYTHONIOENCODING") == "cp936", (
        "#682：extra 覆盖 PYTHONIOENCODING 的契约被破坏")
    assert env.get("PATH") == "/custom/bin", "#682：extra 覆盖白名单内键失败"
    assert env.get("FOO") == "bar", "#682：extra 透传白名单外键失败（显式声明应生效）"


def test_682_g3_extra_override_beats_dropped_credential():
    """G3：显式 `extra` 是调用方主动声明——即便名字像凭据也应被写入（契约边界）。

    （这不是漏洞面：`extra` 只来自执行核自身的 `--env` 参数，非继承 env。）
    """
    env = _probe_env({"MY_API_KEY": "explicit"})
    assert env.get("MY_API_KEY") == "explicit", (
        "#682：extra 显式声明的键未被写入（extra 覆盖语义被破坏）")


if __name__ == "__main__":
    raise SystemExit(__import__("pytest").main([__file__, "-q"]))

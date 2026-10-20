"""灵枢（lingshu）· 聚合导出包 —— 世界模型 / 存算一体 / 自研神经网络 / 图像生成。"""
# issue #156 v2 / #273 裁定（**路 B · 解析期拒绝**）：**最早落点**——在本包任何
# 子模块（含 core.py:17 `import json`）的任何裸名 import 之前，装一个
# `sys.meta_path` finder，把「解析结果落在 cwd / 空串 `sys.path` 条目内」的裸名
# 解析**跳过该条目、改在净化路径上继续解析**（**不删条目**、**不改写宿主进程的
# 全局状态**）。Python 语义保证 `import lingshu.core.core` 必先执行本文件。
# 见 lingshu/_pathguard.py（含精确范围声明与逃生口 LINGSHU_ALLOW_CWD_IMPORTS）。
from . import _pathguard as _pathguard  # noqa: F401  (import 即执行状态判定，勿删)

# #273 路 B：装解析期拒绝 finder（幂等；逃生口 LINGSHU_ALLOW_CWD_IMPORTS 打开
# ⇒ **不装**，等于放弃本层保护——状态与告警仍由 _pathguard 可观测）。
_pathguard.install_route_finder()

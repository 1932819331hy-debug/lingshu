# -*- coding: utf-8 -*-
"""wm_bridge.factory.film · 影视层（L2-L6）契约族 —— 视听语言的可判定化

落点依据：架构文档 §3.1「落点（已裁 D3）」—— 新增层全部落在
bridges/blender/wm_bridge/factory/ 之下（与既有四产线同层），不新建 wmfilm/ 平行包。

本包承载七份契约（§3.2），每份一模块：
    shot.py      wmfilm.shot/1      镜头：camera · compose · light
    perform.py   wmfilm.perform/1   表演语义覆写
    score.py     wmmv.score/1       乐谱（离线分析后固化）
    bind.py      wmmv.bind/1        音画绑定
    edit.py      wmmv.edit/1        剪辑时间轴 / 转场
    text.py      wmmv.text/1        歌词 / 字幕 / 字卡
    oracle.py    wmfilm.oracle/1    视听真值回读

判据门（§4.2）落在 _det/，沿用既有 _det/ 命名约定。

硬边界（与 wm_bridge/__init__.py 的三条一致，本包额外加一条）：
    B1 不改 lingshu 的 world/
    B2 不把 bpy 带进 lingshu
    B3 原版插件零改动
    B4 本包**纯标准库**：不 import bpy / numpy / PIL（可在无 Blender 机器上完整自测）

本包**不含**实现逻辑（渲染 / 绑定 / 剪辑的算法）—— P0 只立**契约与校验器**（§七 P0）。
"""

#: 本层契约族的 schema 前缀（与 geo/spec.py 的 wmfactory.* 并列，不混用）
SCHEMA_PREFIX = "wmfilm."
SCHEMA_MV_PREFIX = "wmmv."

#: 落点（已裁 D3）：新增层全部在本目录之下
#: 七契约登记表：模块名 -> schema 标识（唯一权威，供 _det/ 与守卫测试按名取用）
MODULES = (
    ("shot", "wmfilm.shot/1"),
    ("perform", "wmfilm.perform/1"),
    ("score", "wmmv.score/1"),
    ("bind", "wmmv.bind/1"),
    ("edit", "wmmv.edit/1"),
    ("text", "wmmv.text/1"),
    ("oracle", "wmfilm.oracle/1"),
)

__all__ = ["SCHEMA_PREFIX", "SCHEMA_MV_PREFIX", "MODULES",
           "ASPECT_16_9", "TITLE_SAFE", "ACTION_SAFE"]

#: 全片画幅（已裁 D4：16:9 横屏）—— 1.778 与 16/9 同值，此处用精确分数
ASPECT_16_9 = 16.0 / 9.0

#: 安全区口径（EBU R95；16:9 横屏取值 —— 竖屏 9:16 不在本版范围，不得复用）
TITLE_SAFE = 0.80    # 字幕 / 字卡安全区 = 0.80 x 画幅（1920x1080 下 1536x864 px）
ACTION_SAFE = 0.90   # 动作安全区   = 0.90 x 画幅（1920x1080 下 1728x972 px）


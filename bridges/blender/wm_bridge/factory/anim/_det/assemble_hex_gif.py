# -*- coding: utf-8 -*-
"""assemble_hex_gif · 把 Blender 出的帧装配成 GIF（**在 venv 侧跑**，这里才有 PIL）

严格性：装配前先断言 `帧数 × 每帧延时 == 步态周期`。
为什么必须断言：GIF 的帧延时粒度是 **厘秒**，`48 帧 × 40 ms = 1.920 s` 而周期是 `2.000 s`
⇒ 整段动画**快了 4.00%**（这正是本脚本被写出来的原因）。
"""
import json
import os
import sys
import tempfile as _tempfile

def _bridge_root():
    """桥根（含 `wm_bridge/` 的那一级）。

    ⚠ 不数 `..` 的层数：本文件先前在 `_evidence/lead/`，搬进 `wm_bridge/factory/anim/_det/`
    后层数就变了（`..` 从 2 个变成 4 个），**数层数的写法一搬家就断**。向上找标志目录更稳。
    """
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(8):
        if os.path.isdir(os.path.join(d, "wm_bridge")):
            return d
        up = os.path.dirname(d)
        if up == d:
            break
        d = up
    raise RuntimeError("找不到桥根（含 wm_bridge/ 的那一级）")


BRIDGE = _bridge_root()
sys.path.insert(0, BRIDGE)

from PIL import Image
from wm_bridge.factory.anim import demo as D
from wm_bridge.factory.anim import seq as SEQ

SRC = sys.argv[1] if len(sys.argv) > 1 else r"D:\DSH\blender\out\wmopt\hexgif"
n = int(D.HERO_FRAMES)
period_ms = int(round(D.GAIT_PERIOD_S * 1000))
delay = period_ms // n
assert delay % 10 == 0, "延时必须是 10 ms 的整数倍（GIF 厘秒粒度）：%d" % delay
assert n * delay == period_ms, "帧数×延时 != 周期：%d×%d=%d vs %d" % (n, delay, n * delay, period_ms)

paths = [os.path.join(SRC, "f_%04d.png" % i) for i in range(n)]
missing = [p for p in paths if not os.path.isfile(p)]
assert not missing, "缺帧：%s" % missing[:3]
imgs = [Image.open(p).convert("RGB") for p in paths]
#: ⚠ 输出**必须落在工作区内**：本会话文件策略是 `workspace-write`，
#: 往 `/mnt/d/...` 写会直接 `OSError: [Errno 30] Read-only file system`（实测踩过）。
#: 装好后由 PowerShell 通道拷回 `D:\`（见第 25 阶段）。
#: ⚠ 默认输出**绝不能落在包目录里**：本脚本先前在 `_evidence/lead/`，默认往同级 `out/` 写；
#: 搬进 `wm_bridge/factory/anim/_det/` 后同一个表达式就会**往产品树里写文件**（污染仓库）。
#: 改为：显式给就听，否则落到系统临时目录。产物路径一律**由调用方决定**。
DEST = sys.argv[2] if len(sys.argv) > 2 else os.path.join(_tempfile.gettempdir(), "wm_hexgif")
os.makedirs(DEST, exist_ok=True)
gif = os.path.join(DEST, "hex_gait_final.gif")
obs = SEQ.to_gif(imgs, gif, duration_ms=delay, loop=0)
out = {"gif": gif, "frames": obs.get("frames"), "delay_ms": obs.get("duration_ms"),
       "loop": obs.get("loop"), "bytes": obs.get("bytes"), "size": obs.get("size"),
       "expected_total_ms": n * delay,
       "readback_total_ms": (obs.get("frames") or 0) * (obs.get("duration_ms") or 0)}
out["timing_exact"] = out["expected_total_ms"] == out["readback_total_ms"]
print("ASSEMBLE " + json.dumps(out, ensure_ascii=True))

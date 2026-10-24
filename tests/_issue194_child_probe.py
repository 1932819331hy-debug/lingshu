# -*- coding: utf-8 -*-
"""#194 守卫的子进程探针（不是测试文件，供 test_issue194_*.py 调用）。

为什么单独成文件：先前把这段代码内联成单行字符串传 `python -c`，
`try: ...; except ...:` 不能共处一个逻辑行 ⇒ SyntaxError，
守卫于是在**回滚源码后也「通过」**（假通过）。落成真文件就没有这个坑。

用法: python _issue194_child_probe.py <n_objects> <seed>
输出: NO_ERROR | RAISED_VALUEERROR | RAISED_OTHER <Type>
始终 exit 0（「是否抛错」由 stdout 审判词判定，rc 只表示脚本跑没跑完）。
"""
import os
import sys

ROOT = os.environ.get("LINGSHU_ROOT") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

from lingshu.nn import hex_text as ht  # noqa: E402

n = int(sys.argv[1])
seed = int(sys.argv[2])

try:
    ht.make_multimodal_scene(np.random.default_rng(seed), 16, n)
    print("NO_ERROR")
except ValueError as e:
    print("RAISED_VALUEERROR")
    print("MSG: %s" % (e,))
except Exception as e:                      # noqa: BLE001
    print("RAISED_OTHER %s" % type(e).__name__)

sys.exit(0)
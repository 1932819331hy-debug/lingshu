# -*- coding: utf-8 -*-
"""#349 守卫：固化核 JSON 全链路校验「7 参数」契约，meta 同名键不得覆盖结构字段。

缺陷面（公开 issue #349）：`load_consolidated` 来者不拒——8 参数核被 hex_conv
静默截断成前 7 个（结果逐位相同）、6 参数核在卷积深处 IndexError；`save_consolidated`
的 `**meta` 后置展开让 meta 里的 `kernels`/`consolidated` 同名键覆盖结构字段
（落盘 kernels={}、学习核全丢）。

本件锁（fail-closed，坏档当场 ValueError 不静默）：
  ① 8 参数核 → load 拒；
  ② 6 参数核 → load 拒；
  ③ 二维核 → load 拒；
  ④ 非 consolidated 档 → load 拒；
  ⑤ 合法档（save 产物）→ load 通过且逐核 7 参数；
  ⑥ meta 带 kernels/consolidated 同名键 → 结构字段不被覆盖（冲突键改写进
     _meta_* 命名空间），学习核不丢。
定点变异：去掉 load 的形状校验 ⇒ ①②③ 红；save 改回 `**meta` ⇒ ⑥ 红。
"""
import json

import numpy as np
import pytest

from lingshu.nn.hex_cnn import (DEFAULT_KERNELS, hex_conv,
                                load_consolidated, save_consolidated)


def _write(tmp_path, payload):
    p = tmp_path / "k.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return str(p)


def _save_good(tmp_path):
    p = str(tmp_path / "good.json")
    save_consolidated({"learned": DEFAULT_KERNELS["smooth"].copy()},
                      {"source": "unit"}, p)
    return p


# ---------------------------------------------------------------- ①②③ 坏形状

@pytest.mark.parametrize("bad", [
    {"k8": [0.1] * 8},          # 8 参数：旧实现静默截断
    {"k6": [0.1] * 6},          # 6 参数：旧实现卷积深处 IndexError
    {"k2d": [[0.1] * 7]},       # 二维
])
def test_bad_shape_rejected(tmp_path, bad):
    path = _write(tmp_path, {"algo": "hexcnn", "consolidated": True, "kernels": bad})
    with pytest.raises(ValueError):
        load_consolidated(path)


# ---------------------------------------------------------------- ④ 非固化档

def test_non_consolidated_payload_rejected(tmp_path):
    path = _write(tmp_path, {"algo": "hexcnn", "consolidated": False,
                             "kernels": {"k": [0.1] * 7}})
    with pytest.raises(ValueError):
        load_consolidated(path)


# ---------------------------------------------------------------- ⑤ 合法往返

def test_roundtrip_good_payload(tmp_path):
    p = _save_good(tmp_path)
    out = load_consolidated(p)
    assert set(out) == {"learned"}
    np.testing.assert_allclose(out["learned"], DEFAULT_KERNELS["smooth"], atol=1e-6)
    # 通过 load 的核必须真能进 hex_conv（7 参数契约的消费端）
    feat = np.ones((3, 3, 1), dtype=np.float32)
    _ = hex_conv(feat, out["learned"])


# ---------------------------------------------------------------- ⑥ meta 覆盖

def test_meta_cannot_override_structural_fields(tmp_path):
    kernels = {"learned": DEFAULT_KERNELS["edge_v"].copy()}
    p = str(tmp_path / "hostile.json")
    save_consolidated(kernels, {"kernels": {}, "consolidated": False,
                                "note": "attacker"}, p)
    payload = json.load(open(p, encoding="utf-8"))
    assert payload["consolidated"] is True, "meta 不得把 consolidated 覆写为 False"
    assert payload["kernels"] == {"learned": [round(float(x), 6)
                                              for x in kernels["learned"]]}, (
        "meta 的空 kernels 不得覆盖学习核（#349：落盘 kernels={} 的旧病）")
    assert payload["_meta_consolidated"] is False
    assert payload["_meta_kernels"] == {}
    assert payload["note"] == "attacker"  # 非冲突 meta 键照常透传
    # 仍可正常 load
    out = load_consolidated(p)
    assert set(out) == {"learned"}

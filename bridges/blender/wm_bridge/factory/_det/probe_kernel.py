# -*- coding: utf-8 -*-
"""_det/probe_kernel.py · 内核 / runtime 可达性探针（诊断用）

决定 `parts.py` 的架构：
  · 若 `K.dsh_geo_api` 在无头里可用 ⇒ parts.py 做**薄适配**（Lead 的要求：别造轮子）
  · 若不可用 ⇒ parts.py 必须自带 bmesh 实现，并在报告里注明"runtime 此件不可达"

同时查 runtime 到底有没有**球体**图元（turret 的 hub/lamp 是 uv_sphere）。
"""
import json

out = {}


def _try(label, fn):
    try:
        out[label] = fn()
    except Exception as exc:                                    # noqa: BLE001
        out[label] = "ERR:%s: %s" % (type(exc).__name__, exc)


K = globals().get("K")
out["has_K"] = K is not None
out["K_type"] = type(K).__name__ if K is not None else None
if K is not None:
    out["K_attrs"] = sorted(a for a in dir(K) if not a.startswith("__"))[:60]

_kit = getattr(K, "dsh_kit", None) if K is not None else None
out["has_dsh_kit"] = _kit is not None
if _kit is not None:
    out["kit_attrs"] = sorted(a for a in dir(_kit) if not a.startswith("__"))[:80]


def _kapi():
    return _kit.kapi("geo")


_try("kapi_geo", lambda: type(_kapi()).__name__)
try:
    g = _kapi()
    out["geo_names"] = sorted(n for n in dir(g) if not n.startswith("_"))
except Exception as exc:                                        # noqa: BLE001
    out["geo_names"] = "ERR:%s: %s" % (type(exc).__name__, exc)

# 内核注入的公开名（KERNEL_BOOTSTRAP 常把 primitives 直接注入 globals）
inj = [n for n in ("box", "cyl", "sphere", "prism", "loft", "solid", "bevel",
                   "principled", "shade_auto", "boolean", "collection", "own")
       if n in globals()]
out["injected_globals"] = inj
out["dsh_geo_api_attr"] = hasattr(K, "dsh_geo_api") if K is not None else None

# runtime 里有没有球
try:
    import importlib.util
    out["spec_geo_module_file"] = getattr(
        __import__("sys").modules.get("geo", None), "__file__", None)
except Exception as exc:                                        # noqa: BLE001
    out["geo_file_err"] = str(exc)

print("PROBEK " + json.dumps(out, ensure_ascii=False))

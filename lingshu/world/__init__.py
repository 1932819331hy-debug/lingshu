"""lingshu.world —— 见本目录 README.md。

本包的 register_world_process 把 world_process 与 motion_groups 两个模块记到引擎实例
（依赖方向 world → core）。导入本包只带入模块定义：不注册、不改引擎状态。
"""
from __future__ import annotations

__all__ = ["register_world_process"]


def register_world_process(engine) -> None:
    """把 world_process 与 motion_groups 两个模块记到**该引擎实例**。

    用法（新建或重开引擎后各调一次）：

        from lingshu.world import register_world_process

        engine = SpacetimeMemoryEngine(db_path="store.db")
        register_world_process(engine)
        engine.world_process("recognize", {})

    core 不导入 world：未注册的引擎调用 engine.world_process 时返回
    {"status": "error", ...}，error 里写明上面的初始化步骤。
    本函数只在传入的实例上写注入槽，不影响其它实例、不产生模块级全局状态；
    可对同一实例重复调用（幂等）。
    """
    from . import motion_groups, world_process
    engine.register_world_process(world_process, motion_groups)

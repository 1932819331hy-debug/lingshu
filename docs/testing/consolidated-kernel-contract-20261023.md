# 固化核交付保持七参数与保留字段契约

日期：2026-10-23

当前 save_consolidated 的元数据展开可以覆盖 kernels、consolidated、algo，合法的已学习核会被静默保存为空；load_consolidated 接受六/八参数核，八参数的末位在 hex_conv 被静默丢弃，六参数则在卷积深处 IndexError。

保存时保留字段由真实参数决定，普通元数据照旧保留；写文件前及加载时逐核验证一维七个有限、可用 float32 表示的权重，失败点名核名。加载验证固化标记与核映射；直接卷积验证既有 (7,) / (C,7) 形状，拒绝静默截断。保留六位小数、float32 加载和原子替换的现有契约。

## 复现与判据

建议优先级 P1，判定为部分验证。真实 save_consolidated/load_consolidated/hex_conv，合法核从 JSON 交付后在另一个特征场复用；保留字段碰撞、坏核保存前后旧文件字节、六/八/二维权重、非有限或 float32 溢出及坏文件结构均有独立对照。共享/逐通道核用均匀特征的解析输出核对。
修前 main 固定为 `ea7a13c87a4673a402c991430707809c500c8ec0`；两平台验证提交 `d95caa3410eefd93f849aebf0d9b95847187dd97`。

| 平台 | 同一守卫在修前 | 修复后 | 具名变异 |
|---|---|---|---|
| Windows / Python 3.12.10 | 16 failed, 2 passed in 0.85s | 18 passed in 0.53s | 5/5 指定用例及断言命中 |
| Linux / Python 3.13.12 | 16 failed, 2 passed in 1.85s | 18 passed in 1.18s | 5/5 指定用例及断言命中 |

全部最终守卫、修前对照与变异无 error、SKIP 或超时；变异恰好命中指定单条用例及断言。

| 变异 | 指定用例 | 指定断言 |
|---|---|---|
| metadata_overrides_parameters | test_metadata_cannot_replace_learned_checkpoint | metadata erased learned kernel |
| accept_wrong_persisted_shape | test_load_rejects_wrong_weight_shape[eight] | load accepted malformed seven-weight kernel |
| accept_invalid_envelope | test_load_rejects_invalid_checkpoint_envelope[false] | load accepted invalid checkpoint envelope |
| truncate_direct_convolution | test_direct_convolution_rejects_incompatible_shape[eight] | convolution silently ignored extra kernel weights |
| accept_float32_overflow | test_bad_save_preserves_old_bytes_before_any_io[float32_overflow] | invalid save replaced good checkpoint |

- Windows：`17 passed in 3.95s`，0 skipped。
- Linux：`17 passed in 23.92s`，0 skipped。

严格登记 64/64 MATCH，时间核无新增违规；未改既有门禁、阈值或断言，未新增豁免。本地仅跑上述受影响面，完整门禁交由最终 PR head 的远端 CI。不为整理材料重复跑稳定测试。以下只统计最终有效定向批次，未为整理证据重跑稳定测试。

安装 `.[full,dev]` 后，在上述固定验证提交的仓根执行：

```text
python -X utf8 -m pytest tests/test_consolidated_kernel_contract.py -q
python -X utf8 tools/testing/replay_save_guards.py --spec tests/fixtures/consolidated_kernel_contract_mutations.json --baseline ea7a13c87a4673a402c991430707809c500c8ec0 --mutate
python -X utf8 -m pytest tests/test_hex_cnn.py tests/test_hex_cnn_atomic_save_guard.py tests/test_consolidated_save_isolation.py tests/test_kernel_scenarios.py tests/test_hex_conv_fusion.py tests/test_issue498_atomic_write_perm_symlink_guard.py::test_b1_目标不存在_正常创建且不继承 tests/test_issue498_atomic_write_perm_symlink_guard.py::test_b2_普通目标_覆盖式语义不变 tests/test_issue498_atomic_write_perm_symlink_guard.py::test_b3_继承旧档权限位_save_consolidated tests/test_issue498_atomic_write_perm_symlink_guard.py::test_b4_落盘目标是realpath_save_consolidated tests/test_issue498_atomic_write_perm_symlink_guard.py::test_b7_换入失败_旧档不动且不留残件 -q
python -X utf8 tools/verify_manifest.py --strict --quiet
python -X utf8 tools/time_core_lint.py --json
```

同目录公开两平台逐腿原始日志及来源校验。公开日志只替换工作区前缀、统一换行和行尾空白，完整原始输出保留本地；时间核完整 JSON 保留本地，其 SHA256 见相关日志。

## 来源与边界

原缺陷与最小反例来自 @LarryE135 的 [Issue #349](https://github.com/FuRongJun-1999/lingshu/issues/349)，维护者已确认七参数漏验与元数据覆盖；@MWHWX 的 [复核 #885](https://github.com/FuRongJun-1999/lingshu/issues/885) 记录历史基线残余。本件在当前 main 独立复现并交付补丁，不把原发现计作新发现；保留 #393/#498 及 PR #826 的原子保存、权限/链接和临时件隔离工作。

建议 P1 的依据是成功保存会丢失核及加载后静默改变计算，不声称默认训练必现。固化文件仍只保存一维共享核：二维逐通道核只在直接 hex_conv 接口支持；不迁移旧坏档、不扩展核格式，不更改训练算法或六位小数精度。未测真实模型、生产损坏发生率或断电。 未修改 dsh-memory 大脑区；未验证 DSH 宿主、生产记忆、线上发生频率、吞吐、断电/磁盘满或所有其它锁。维护者人审待完成。

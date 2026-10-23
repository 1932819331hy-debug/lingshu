# 时空记忆自检对照独立写入快照

日期：2026-10-23

当前 verify_consistency 的召回与比较双方仍来自同一 events 列表：通过公开 remember 写入后，改写返回事件的方向或删除整条事件，检查均错误返回 True。完整伪事件也能混入而不被发现。

remember 在发布事件前深拷贝一份独立写入快照；自检先逐条对照写入基准，再执行既有字段、顺序、召回可达性和对象身份检查。保留 events 和 remember 返回值的现有可变接口，真实修改会被如实判不一致。

## 复现与判据

建议优先级 P1，判定为部分验证。真实 SpatiotemporalMemory.remember/recall/verify_consistency；经返回对象和 recall 的别名分别改写事件，移除、交换、完整伪造事件；深层可变标签验证快照没有嵌套别名。健康和重复合法事件作对照。
修前 main 固定为 `cad16dc5e142a850c86a88915b76a8aca054d84d`；两平台验证提交 `eca4524e40eecc8158999e254ab09bc14132bb7e`。

| 平台 | 同一守卫在修前 | 修复后 | 具名变异 |
|---|---|---|---|
| Windows / Python 3.12.10 | 7 failed, 1 passed in 0.39s | 8 passed in 0.28s | 4/4 指定用例及断言命中 |
| Linux / Python 3.13.12 | 7 failed, 1 passed in 0.60s | 8 passed in 0.43s | 4/4 指定用例及断言命中 |

全部最终守卫、修前对照与变异无 error、SKIP 或超时；变异恰好命中指定单条用例及断言。

| 变异 | 指定用例 | 指定断言 |
|---|---|---|
| compare_only_lengths | test_returned_event_mutation_is_detected | modified returned event passed the write receipt check |
| alias_the_event_receipt | test_returned_event_mutation_is_detected | modified returned event passed the write receipt check |
| copy_only_top_level | test_nested_label_is_copied_independently | nested label alias changed the independent receipt |
| check_only_surviving_prefix | test_lost_event_is_detected_even_when_recall_matches_remaining_events | lost event was accepted as complete memory |

- Windows：`13 passed in 1.31s`，0 skipped。
- Linux：`13 passed in 12.55s`，0 skipped。

严格登记 64/64 MATCH，时间核无新增违规；未改既有门禁、阈值或断言，未新增豁免。本地仅跑上述受影响面，完整门禁交由最终 PR head 的远端 CI。不为整理材料重复跑稳定测试。以下只统计最终有效定向批次，未为整理证据重跑稳定测试。

安装 `.[full,dev]` 后，在上述固定验证提交的仓根执行：

```text
python -X utf8 -m pytest tests/test_stcnn_write_snapshot.py -q
python -X utf8 tools/testing/replay_save_guards.py --spec tests/fixtures/stcnn_write_snapshot_mutations.json --baseline cad16dc5e142a850c86a88915b76a8aca054d84d --mutate
python -X utf8 -m pytest tests/test_stcnn_memory_consistency.py tests/test_issue798_stcnn_min_frames_guard.py tests/test_stcnn_period_edges.py -q
python -X utf8 tools/verify_manifest.py --strict --quiet
python -X utf8 tools/time_core_lint.py --json
```

同目录公开两平台逐腿原始日志及来源校验。公开日志只替换工作区前缀、统一换行和行尾空白，完整原始输出保留本地；时间核完整 JSON 保留本地，其 SHA256 见相关日志。

## 来源与边界

原发现与写入日志建议来自 @Liyang1145 的 [Issue #302](https://github.com/FuRongJun-1999/lingshu/issues/302)；@MWHWX 的 [复核 #869](https://github.com/FuRongJun-1999/lingshu/issues/869) 指出既有守卫仍漏改写/丢失。本件补齐残余判据并保留已有召回守卫，不把原发现计作新发现，不混入 #304 时间区间口径设计。

这是进程内写入一致性基准，不是视频真实性、语义正确性、持久审计或抵御任意进程内篡改的安全边界；多一份事件内容的内存开销随已写事件数增长。标签遵循已有可比较的普通 Python 值约定。 未修改 dsh-memory 大脑区；未验证 DSH 宿主、生产记忆、线上发生频率、吞吐、断电/磁盘满或所有其它锁。维护者人审待完成。

首批 Linux 在 WSL 的临时 fd 输出捕获环节失败，未获得有效测试读数；仅将 Linux pytest 输出捕获关闭后补跑，源码、守卫和变异规格未变，Windows 稳定证据复用。原失败日志保留本地，不计作通过。

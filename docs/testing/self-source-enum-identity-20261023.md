# 重复枚举样本不再碰撞评估身份

日期：2026-10-23

build_enum_items 用 ti*10+rep 生成 ID；repeats=11 时 297 张图只有 271 个唯一 ID。现有按 sid 分组切分虽保留了配方隔离，但两边 ID 仍相交 24 个；真实 C.evaluate 按 ID 选择后，165/132 张的两组分别混入为 189/156 张，by_id 也会覆写别的配方观测。

每批的 ID 步幅取 max(10, repeats)，保证该批每个 (配方,重复序号) 独占一个整数 ID。默认及 repeats<=10 的已有身份逐字保持；不更改配方、画笔、像素、真值、分组切分或评估算法。

## 复现与判据

建议优先级 P1，判定为部分验证。真实 build_enum_items/split_self/C.evaluate；保留真实确定性渲染器，只在新增大重复数守卫把图幅设为 48 以控制内存。检查高重复批次身份唯一、两组 ID 不相交、评估器选图数量与配方切分相符；另用真实 measure_item 证明两张不同配方的观测不会在 by_id 被覆盖。
修前 main 固定为 `ea7a13c87a4673a402c991430707809c500c8ec0`；两平台验证提交 `604aca715c921fca332f0771690ca33134f8bff1`。

| 平台 | 同一守卫在修前 | 修复后 | 具名变异 |
|---|---|---|---|
| Windows / Python 3.12.10 | 5 failed, 4 passed in 2.24s | 9 passed in 2.78s | 3/3 指定用例及断言命中 |
| Linux / Python 3.13.12 | 5 failed, 4 passed in 2.69s | 9 passed in 3.70s | 3/3 指定用例及断言命中 |

全部最终守卫、修前对照与变异无 error、SKIP 或超时；变异恰好命中指定单条用例及断言。

| 变异 | 指定用例 | 指定断言 |
|---|---|---|
| legacy_stride_collides | test_eleven_repeats_keep_each_rendered_sample | rendered samples share an evaluation ID |
| larger_fixed_stride_collides | test_large_repeat_count_does_not_use_a_fixed_id_stride | large-repeat evaluation IDs collided |
| change_legacy_identities | test_existing_small_repeat_identities_are_unchanged[default] | small-repeat identities changed |

- Windows：`8 passed in 25.33s`，0 skipped。
- Linux：`8 passed in 31.63s`，0 skipped。

严格登记 64/64 MATCH，时间核无新增违规；未改既有门禁、阈值或断言，未新增豁免。本地仅跑上述受影响面，完整门禁交由最终 PR head 的远端 CI。不为整理材料重复跑稳定测试。以下只统计最终有效定向批次，未为整理证据重跑稳定测试。

安装 `.[full,dev]` 后，在上述固定验证提交的仓根执行：

```text
python -X utf8 -m pytest tests/test_self_source_enum_identity.py -q
python -X utf8 tools/testing/replay_save_guards.py --spec tests/fixtures/self_source_enum_identity_mutations.json --baseline ea7a13c87a4673a402c991430707809c500c8ec0 --mutate
python -X utf8 -m pytest tests/test_hexgen_self_source_guards.py::test_split_self_keeps_replicas_of_a_recipe_on_one_side tests/test_hexgen_self_source_guards.py::test_split_self_has_no_byte_identical_leak_across_sides tests/test_hexgen_self_source_guards.py::test_split_self_single_repeat_still_splits_both_sides tests/test_hexgen_c1_real_p2_empty_and_invariant.py tests/test_hexgen_c1_real_gap_and_baseline.py -q
python -X utf8 tools/verify_manifest.py --strict --quiet
python -X utf8 tools/time_core_lint.py --json
```

同目录公开两平台逐腿原始日志及来源校验。公开日志只替换工作区前缀、统一换行和行尾空白，完整原始输出保留本地；时间核完整 JSON 保留本地，其 SHA256 见相关日志。

## 来源与边界

原报告及重复数边界来自 @kudowyv 的 [Issue #391](https://github.com/FuRongJun-1999/lingshu/issues/391)，@MWHWX 的 [复核 #881](https://github.com/FuRongJun-1999/lingshu/issues/881) 指出 ID 残余。本件只修 build_enum_items；维护者旧回复引用的 ti*100+rep 属于另一个 build_items，当前枚举入口仍是 ti*10+rep，已实际复跑区分。已有按 sid 分组的防副本泄漏修复完整保留。

repeats<=10 不受此次缺陷影响；ID 只保证同一生成批次唯一，高重复数配置间不能复用旧 ID 缓存。新增评估选图守卫刻意给空观测以隔离成员选择，不把该读数当识别正确率；真实测量仅核对缓存归属，不宣称准确率提高。未重跑完整 closed_loop、512 大图高重复训练、黑箱语料或 DSH 宿主。 未修改 dsh-memory 大脑区；未验证 DSH 宿主、生产记忆、线上发生频率、吞吐、断电/磁盘满或所有其它锁。维护者人审待完成。

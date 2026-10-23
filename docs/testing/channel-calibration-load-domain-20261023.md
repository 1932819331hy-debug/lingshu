# 通道校准载入隔离非法后验与计数

日期：2026-10-23

已有载入防护只拦 JSON 解析和 float 转换。可解析的列表根使构造器 AttributeError，hits=NaN 使公开 registry 查询 ValueError，a=10/b=-9 被当健康校准并报 credibility=10、status=ok；一个坏通道会阻断整份注册表查询。

非对象文件根按既有文件级坏档契约回落先验；逐通道要求正的有限 Beta 参数、有限正总量以及非负有限命中/未命中计数，坏通道整体复位，健康邻居原值保留；转换溢出按同样的坏通道处理。载入本身不改写文件。

## 复现与判据

建议优先级 P1，判定为部分验证。真实 JSON 文件和 ChannelCredibilityRegistry 构造/registry/record_hit/record_miss；分别载入列表/null 根、NaN 计数、负 Beta、单边零 Beta、负计数、总量溢出、巨大整数，检查健康邻居及下一次保存重开。不是用私有状态替代载入链。
修前 main 固定为 `cad16dc5e142a850c86a88915b76a8aca054d84d`；两平台验证提交 `d264a666f60809586fae5ae350d22619d79f426c`。

| 平台 | 同一守卫在修前 | 修复后 | 具名变异 |
|---|---|---|---|
| Windows / Python 3.12.10 | 11 failed, 1 passed in 0.51s | 12 passed in 0.19s | 4/4 指定用例及断言命中 |
| Linux / Python 3.13.12 | 11 failed, 1 passed in 1.71s | 12 passed in 0.94s | 4/4 指定用例及断言命中 |

全部最终守卫、修前对照与变异无 error、SKIP 或超时；变异恰好命中指定单条用例及断言。

| 变异 | 指定用例 | 指定断言 |
|---|---|---|
| skip_root_shape_check | test_non_object_file_root_keeps_prior[None] | invalid calibration root escaped recovery |
| ignore_nonfinite_counts | test_bad_channel_resets_whole_calibration_without_losing_neighbor[nan_hits] | invalid channel escaped recovery |
| admit_negative_beta_component | test_bad_channel_resets_whole_calibration_without_losing_neighbor[negative_beta] | invalid calibration was not reset as one complete channel |
| keep_overflowed_beta_total | test_overflowed_beta_total_is_rejected | overflowed Beta total survived loading |

- Windows：`36 passed in 1.60s`，0 skipped。
- Linux：`36 passed in 16.97s`，0 skipped。

严格登记 64/64 MATCH，时间核无新增违规；未改既有门禁、阈值或断言，未新增豁免。本地仅跑上述受影响面，完整门禁交由最终 PR head 的远端 CI。不为整理材料重复跑稳定测试。以下只统计最终有效定向批次，未为整理证据重跑稳定测试。

安装 `.[full,dev]` 后，在上述固定验证提交的仓根执行：

```text
python -X utf8 -m pytest tests/test_channel_calibration_load_domain.py -q
python -X utf8 tools/testing/replay_save_guards.py --spec tests/fixtures/channel_calibration_load_domain_mutations.json --baseline cad16dc5e142a850c86a88915b76a8aca054d84d --mutate
python -X utf8 -m pytest tests/test_channel_cred_query_surface.py tests/test_issue601_channel_cred_load_guard.py tests/test_issue651_channel_cred_write_gate.py tests/test_issue402_nan_clamp_guard.py -q
python -X utf8 tools/verify_manifest.py --strict --quiet
python -X utf8 tools/time_core_lint.py --json
```

同目录公开两平台逐腿原始日志及来源校验。公开日志只替换工作区前缀、统一换行和行尾空白，完整原始输出保留本地；时间核完整 JSON 保留本地，其 SHA256 见相关日志。

## 来源与边界

保留 [PR #554](https://github.com/FuRongJun-1999/lingshu/pull/554) 查询分母防护、[PR #622](https://github.com/FuRongJun-1999/lingshu/pull/622) 对 #601 的逐通道转换防护，以及 #651 通道名白名单。本件只补这些既有校准恢复契约的形状/值域漏网，不引入遗忘机制、阈值或反证算法设计。

仅覆盖载入恢复，不修公开 #119 的裸文件名保存问题、文件原子写入或任意进程内直改 _state；无效通道的原校准无法恢复，只能回落先验。未验证生产文件损坏频率或 DSH 宿主。 未修改 dsh-memory 大脑区；未验证 DSH 宿主、生产记忆、线上发生频率、吞吐、断电/磁盘满或所有其它锁。维护者人审待完成。

首次完整 CI 在上游 #873 新增的查询守卫失败：借用连接的测试夹具丢失数据库所有者，GC 回收时关闭连接，报 Cannot operate on a closed database；不是通道校准守卫失败。[原失败 job](https://github.com/FuRongJun-1999/lingshu/actions/runs/39890169441/job/120207753160)。

对齐 main `a3ae57e3830ee2b9506d5d3f5f25e986c4429c67` 后，仅将该测试改为 pytest 工厂夹具，在整条测试期间保留借用连接的所有者，并在结束时显式关闭；原六条查询断言和阈值逐条保留。新增强制 gc.collect 的守卫：不保留所有者时恰好命中该断言；不改变 ActivationEngine 的生产语义。

夹具修正提交 `5d429a1da1e671a82178565bfa4243c69e880938`：两平台各 7 passed、0 skipped；owner_not_retained 定点反证各恰红 1 条，并命中 borrowed query fixture lost its database owner。合入 main 后的清单严格检查仍为 64/64 MATCH，时间核无新增违规。通道校准源码、新增业务守卫及其变异规格逐字节不变，复用既有双平台 12 守卫、4 变异、36 相关回归和 37 条组合读数，仅补跑这份受影响夹具；不是新的独立 P1 或额外悬赏。当前 head 的远端完整 CI 重新验收。

```text
python -X utf8 -m pytest tests/test_issue505_workset_query_guard.py -q
```

# tests/ · 当前运行方式

完整门禁由 [.github/workflows/gate.yml](../.github/workflows/gate.yml) 定义：

1. 安装 `pip install -e ".[full,dev]"`。
2. 逐一运行 workflow 的 `SCRIPT_TESTS`（`python -X utf8 <文件>`），任一非零退出码即失败。
3. 使用 `python -X utf8 -m pytest tests/ -v`，并通过 `--ignore` 排除已在前一步独立执行的脚本文件。所有其他 pytest 用例都参加检查。

部分脚本在模块导入时执行或退出，直接对目录运行 pytest 不能替代上述完整门禁。脚本列表统一以 workflow 为准，不在这里维护另一份名单。

脑适配器测试需要公开 `dsh-memory` 代码目录，由 `MDCG_BRAIN_PYTHONPATH` 指定。CI 会 clone 公开仓并实际运行；只有公开仓不可达时才显示具体 SKIP 原因。它不使用真实记忆数据库。

结构服务业务回归运行：

```text
python -X utf8 -m pytest tests/test_structure_events.py tests/test_structure_writer.py -q
```

TLS 子进程测试使用 OpenSSL CLI 在临时目录生成证书与私钥，文件不会进入仓库。GitHub Ubuntu runner 提供该工具；本地需先安装 OpenSSL CLI。Windows 的临时 ACL 调整只作用于测试自身目录。

两条 `test_hex_text` 历史 known-fail 现在已通过原断言，gate 已移除对应 deselect；没有添加 xfail。旧 intake 中的 6/16、7/12 是历史导出读数，不能用来描述当前实现。

| 形态 | 文件 | 说明 |
|---|---|---|
| pytest 原生 | `test_hex_ortho` `test_hex_search` `test_hex_text` `test_hex_train` `test_hex_hier` | `python -m pytest <file> -v`；直接 `python <file>` 亦转投 pytest |
| 脚本/pytest 双模式 | `test_hex_cnn` `test_hex_composite` `test_hex_gen` `test_brain_store` `test_wm_verify_unobserved` | `python tests/test_<x>.py` 原样保留（intake 复跑命令不变）；执行体收拢在 `main()`/`run_checks()`，pytest 经薄包装入口收集（见下"改造注记"） |

前置说明：`test_brain_store` 需环境变量 `MDCG_BRAIN_PYTHONPATH`（脑包目录）才全跑，
缺前置时显式 SKIP 并以 0 退出——CI 中该件恒为 SKIP，不阻塞。
该件的 #364 / #366 四个**纯桩式**守卫（不需要脑端）已拆到
`test_brain_store_stub_guards.py`（pytest 原生，**不在** `SCRIPT_TESTS` 内 ⇒ 走 pytest 腿）。

## 耗时档位（上游 intake v0.2 §四实测；本地 py3.12/numpy2.5.1 同量级）

| 档位 | 文件 | 耗时 |
|---|---|---|
| 快档 | 其余全部 | 合计 ≈80s |
| 重件 | `test_hex_search` | ≈361.6s |
| 重件 | `test_hex_hier` | ≈657.5s |

CI（`.github/workflows/gate.yml`）按此跑 pytest 腿：py3.12（阻断）＋ py3.13 与
「依赖下界」两档 advisory，串行全量收集（排除下方脚本式件由 gate 自身排除表管理）；
PR 另有 `pr-lint.yml` 标题门与 `repro-bot.yml` 复跑机器人。本地全量可用
`python -m pytest tests/ -n auto`（dev extra 的 pytest-xdist，实测 483s → 228s；
时序敏感件在满载下的口径见各件 docstring）。

## 曾登记的「已知红」——已转绿（2026-10-11 更新）

`test_hex_text` 曾登记 2 项已知红：`test_spatial_detect_finds_single_object`（6/16）、
`test_multimodal_check_clean_vs_corrupt`（7/12）——**根因修复已落 main**：
`9bc4985`（`image_to_grid` 晶格横向越出画幅 √3 倍，采纳自 PR #116）＋ `54cb5cb`
（`hex_hier` 形状辅助损失按 OBJ 布局连续分组）。两条现随 `tests/` 自动收集真跑：
本机 `python -X utf8 -m pytest tests/test_hex_text.py -q` = **6 passed**。
`gate.yml` 上曾挂的 `--deselect` 已摘除；守卫 `tests/test_gate_deselect_justified.py`
断言「凡被 deselect 的用例必须真红」——再 deselect 一条绿的用例会当场判红。
原读数与讨论见 issue #4 / #24（判据「不可复算」的批评）。

## 改造注记（2026-10-08 · 测试基建）

- `test_hex_cnn` 原为模块级直跑脚本——pytest 收集时即触发 `sys.exit`，
  **整个 `tests/` 目录无法一键收集**（INTERNALERROR）。现执行体收拢进 `main()`，
  脚本模式退出码语义不变；本地读数 `13 passed, 0 failed`（改造前后逐位一致）。
- `test_brain_store` / `test_wm_verify_unobserved` 原有 `main()` 但 pytest 收不到
  用例；各加一个薄包装 `test_*()`（`assert main() == 0`），断言零改动。
- 混合式件里的**非包装** `test_*` 会落在两条腿之外（issue #485）：文件列在
  `SCRIPT_TESTS` ⇒ pytest 腿整文件 `--ignore`，而 `main()` 又调不到它们。
  `test_brain_store.py` 的 #364 / #366 四个桩式守卫即此形态，已拆到
  `test_brain_store_stub_guards.py`（pytest 式、不入清单）。
  复发守卫：`test_gate_script_leg_reachability_guard.py` 对 `SCRIPT_TESTS` 每件做
  静态可达性判定——顶层 `test_*` 必须被脚本入口（`main()` / `run_checks()`）触达，
  或只引用脚本腿同样执行的模块级件；否则判红。
- `test_hex_text` 两项已知红曾提议 xfail 标注——PR #87 部分采纳时**明确未取**
  （xfail 从未落仓，根因由 `9bc4985`/`54cb5cb` 真修取代，见上节）；
  全仓测试件至今**零 xfail 标注**（守卫 `tests/test_issue446_readme_reality.py`
  钉「文档声称转绿 ⇔ 代码确无 xfail」两侧一致）。
- 新增根 `conftest.py`（sys.path 注入）已进 main；同 PR 自带的 ci.yml 工作流
  **未被采纳**（与 main 的 gate.yml 重复且更窄，该文件从未落仓）——CI 真源为
  `gate.yml`。

SciPy 属可选后端，未安装时其现有专属测试显式 skip；不要将 skip 记为运行通过。核心和 NumPy 路径正常运行。

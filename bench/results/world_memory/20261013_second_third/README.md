# 第二/第三批公开证据（F04 / F06 / F07 / F08）— 2026-10-13

本目录是「第二批 F04/F06/F07 + 第三批 F08」这份普通依赖 PR 的**最小公开证据包**：
一份数值证据、一张前后对照图、8 份真实通过日志、一个可独立复跑、带定点变异检验与有限安全自检的脚本，
以及脚本的自证报告。

## 0. 测量基线（先读这条）

| 项 | 值 |
|---|---|
| 测量基线 | `5603e28540ea1596d089f259e6fecd594ea73ccc`（第一批完成后的提交） |
| 当前侧 | 同一 HEAD 之上的工作树：修改 6 个实现文件 + 新增 4 个测试文件（**取数时未提交**；发布 head 以 PR 固定链接为准） |
| 对比口径 | 本目录所有能力差都是「**基线 5603e28 → 当前工作树**」。基线里已经存在的第一批能力（F01/F02/F03/F05）**不计入本批** |
| 环境 | Windows；主体 `.venv` Python 3.12.13 / NumPy 2.5.3 / Pillow 12.3.0 / pytest 9.1.1 |

**本目录不含任何 CI 结论：不声称 CI 已绿。** 这里只有本机实测的可复跑读数与原始日志。

## 1. 复跑（仓根，Windows PowerShell，约 1 分钟）

复跑一律写进**新的**临时 work-dir：本目录里的 [verify_report.json](verify_report.json) 是本次发布的版本化证据，
不要用复跑覆盖它。

```powershell
# 1) 全量复跑（baseline + probe + mutations），产物只落在新的 $work 下
$work = "artifacts/contribution_submission_20261013/evidence_work/rerun_" + (Get-Date -Format yyyyMMdd_HHmmss)
& "./.venv/Scripts/python.exe" -X utf8 -B bench/results/world_memory/20261013_second_third/verify_evidence.py all --work-dir $work --out "$work/verify_report.json"

# 2) 有限安全自检（真实执行，不删除任何既有目录）
& "./.venv/Scripts/python.exe" -X utf8 -B bench/results/world_memory/20261013_second_third/verify_evidence.py safety --scratch "$work" --out "$work/safety_check.json"
```

脚本自己会做三件事（全程在独立子进程里跑，**不改任何源码文件**）：

1. `git archive 5603e28` 导出基线树到 `--work-dir` 下的 `baseline_5603e285`（不写当前工作树、不动 refs、导出物不含 `__pycache__`）；
2. 在「基线树」与「当前工作树」各起一个新进程，断言 `lingshu.__file__` 落在该 root 之内（防路径串台），输出 F04/F06/F07/F08 的读数；
3. 跑 1 个正例 + 4 个定点变异（pytest `-p` 插件注入 monkeypatch），结束校验 6 个源码文件 SHA256 与开始时一致。

只跑单项时（`$work` 同上，先起一个新的）：

```powershell
# 只导出基线（--dest 必须是尚不存在的新目录）
& "./.venv/Scripts/python.exe" -X utf8 -B bench/results/world_memory/20261013_second_third/verify_evidence.py baseline --dest "$work/baseline_only"

# 只探测某一棵树（--f07 red-circle|triangle|both）
& "./.venv/Scripts/python.exe" -X utf8 -B bench/results/world_memory/20261013_second_third/verify_evidence.py probe --root . --f07 both --out "$work/probe.json"

# 只跑定点变异检验
& "./.venv/Scripts/python.exe" -X utf8 -B bench/results/world_memory/20261013_second_third/verify_evidence.py mutations --root . --work-dir "$work/mutations"
```

预期：末行打印 `"source_files_unchanged": true`，`P0` 绿、`M1`–`M4` 红。逐项原始读数、退出码与失败断言在
[verify_report.json](verify_report.json) 里（含每次 pytest 的完整命令行与断言所在行）。

### 1.1 导出目标安全规则（脚本不含任何删除既有路径的代码）

- `--dest`（以及 `all` 模式下的 `baseline_<ref8>` 子目录）**必须是尚不存在的新路径**；
- 目标已存在（目录 / 文件 / 符号链接 / 目录联接 junction）、目标是仓库根本身、目标是仓库根的祖先目录
  → 一律**明确拒绝**并返回退出码 2，一个字节都不动；
- **重复使用同一个 `--work-dir` 会被明确拒绝**（`baseline_5603e285` 已存在），旧的导出物不会被删除或覆盖，
  请换一个新的 work-dir；
- tar 成员逐个按 `realpath` + `commonpath` 复核：只解出普通文件与目录，链接（symlink / hardlink）、设备、
  FIFO、绝对路径、`..` 上跳、盘符前缀一律拒绝，绝不解出链接后跟随到导出目录之外。

`--work-dir` 本身**可以**已存在（脚本只对它 `makedirs(exist_ok=True)`，不删除其中任何东西）；这时脚本会向其中写入自己的
产物名（`probe_baseline.json` / `probe_current.json` / `mutations/mut_*.py`），同名文件会被覆盖——但**其中的
`baseline_<ref8>` 子目录一旦已存在就会被明确拒绝**，既有导出物与其它目录不会被删除或覆盖（实跑验证：重复用同一
`--work-dir` 跑 `all` 返回退出码 2、已导出的 348 个文件逐字节不变、不产生任何报告文件）。

`safety` 模式把这套规则跑成 9 项真实检查（全部通过后才返回 0）：源码无删除调用、已有非空目录放 256B 哨兵后
拒绝且哨兵与其它文件字节不变、repo 根拒绝且 6 个源码 SHA256 不变、祖先目录拒绝、已存在文件拒绝、
链接（junction）拒绝且其目标不变、合法新目录正常导出（371 个文件、无 `__pycache__`）、重复导出拒绝且
已导出内容逐字节不变、tar 恶意成员（上跳 / 绝对路径 / 盘符 / symlink / hardlink / FIFO）全部被拒。

## 2. 能力差读数（脚本实测）

F04 真实位姿记忆与跨引擎多视角重建（[verify_report.json](verify_report.json) → `probe.baseline.f04` / `probe.current.f04`）：

| 观测 | 基线 5603e28 | 当前工作树 |
|---|---|---|
| `Camera3D.to_meta / from_meta` | 不存在 | 存在 |
| 两次已知位姿 `world3d("add_view")` | `status=ok`，但**无** `persisted` 键、vprim 节点 0 | `status=ok` + `persisted=node_*`、vprim 节点 2（2 条均带相机块） |
| `camera={}`（空位姿） | `status=ok`（默认值补齐 = 伪造位姿） | `status=error` / `reason=bad_camera_pose` |
| 新引擎 `build(multiview=True)` | `mode=single_view`、`reason=no_camera_pose`、objects=0 | `mode=multiview`、`reason=null`、objects=1、observations=2 |
| 重建中心 vs 真值 (0.6, 1.0, 6.0) | 无读数（objects=0） | 误差 **0.000 m**（阈值 < 0.05 m 通过） |

F06 角色与显式关系参与评分（同一布局：红圆 r7（下方行）+ 蓝圆 r1（上方行），声明 `red(head) above blue(body)`）：

| 读数 | 基线 5603e28 | 当前工作树 |
|---|---|---|
| `score` | **1.0** | **0.667** |
| `relations` 字段 | 无（关系声明被忽略） | 有；`relations[0].satisfied=False` |
| `verdict` | ACCEPT | ACCEPT |

本批只声明「关系不满足 ⇒ score 降低且 `relation.satisfied=False`」，**不声明裁决一定翻转**（两棵树这里都是 ACCEPT）。

F07 真实像素驱动的有限反馈（独立读数：脚本自己用 `lingshu.gen.hex_composite.extract_attributes` 读最终图像素，再与 parts 声明比对；**不采信 refine 自己的记账**）：

| 场景（size=96） | 基线 5603e28 实图 pattern_misses | 当前工作树（默认反馈） | 当前工作树（`refine_rounds=0` 对照） |
|---|---|---|---|
| `左上有红色小点纹圆形` seed=7 | **1**（读到 `solid`，area=13，声明 `dotted`） | **0** | 1 |
| `中间有点纹三角` seed=0 | **1**（读到 `None`，area=6，声明 `dotted`） | **0** | 1 |

两棵树/两个场景的 `parts`（形状/颜色/方位/花纹）与 seed 完全一致（`intent_preserved_parts_equal=true`）；基线没有 `refine_render`、`generate_from_text` 也没有 `refine_rounds` 参数。

> 这两个场景**都是本次公开的实测证据**（脚本 `--f07 both` 为默认）：两行读数各自独立可复跑、口径一致（同一套独立像素回读），本包不做也不需要在两者之间做取舍。

F08 沙盒预测驱动行动（[verify_report.json](verify_report.json) → `probe.*.f08`；主数值同样在 [evidence.json](evidence.json) 的 `actions`）：

| 读数 | 基线 5603e28 | 当前工作树 |
|---|---|---|
| `configure_action / clear_action` | 不存在 → **未测**（无基线数值，不估数） | 存在 |
| static 目标：predictive vs fixed 末距 | — | **0.0** vs **16.9189** |
| patrol 目标：predictive vs fixed 末距 | — | **0.0** vs **15.3052**（predictive 真外推 37 tick） |

## 3. 定点变异检验（红绿表，均为实跑）

变异**只在 pytest 子进程启动时用 monkeypatch 注入**（`-p mut_*` 插件，写在临时目录），源码文件一个字节都不改。正例与 4 个反例用的是同一套设施，所以「红」不是设施本身造成的。

| ID | 注入的变异 | 守卫节点 | 期望 | 实测 | 退出码 | 实测失败断言 |
|---|---|---|---|---|---|---|
| P0 | 不注入任何补丁（正例） | 下述 M1–M4 用到的 5 条测试节点（C7 摄取守卫 + F06 通配角色 + F07 真实改善 + I1 + I2） | 绿 | **绿**（5 passed） | 0 | — |
| M1 | `Camera3D.from_meta` 忽略 `strict`，强制 `strict=False` | `tests/test_second_batch_camera.py::test_c7_ingest_rejects_incomplete_camera_without_persisting` | 红 | **红** | 1 | 该节点内 `assert r["status"] == "error"` 失败（实得 `'ok'`） |
| M2 | `hex_query._canonical_det_order` 改为输入顺序 | `tests/test_second_batch_relations_feedback.py::test_f06_wildcard_roles_are_order_invariant_full_permutations` | 红 | **红** | 1 | 该节点内 `assert len(sigs) == 1` 失败（实得 2，两序报告不一致） |
| M3 | `hex_gen.refine_render` 强制 `rounds=0` | `tests/test_second_batch_relations_feedback.py::test_f07_real_failure_diff_strictly_decreases` | 红 | **红** | 1 | 该节点内 `assert len(after) < len(before) and after == []` 失败（1 vs 1） |
| M4 | `ActionDecision.observe_outcome` 强制 `target_fresh=True` | `tests/test_third_batch_actions.py::test_i_stale_target_observation_is_not_new_error_feedback` + `::test_i_pre_existing_correction_is_frozen_by_stale_calls` | 红 | **红**（2 failed） | 1 | 前一个节点内 `assert outcome["target_observation_fresh"] is False` 失败（实得 True）；后一个节点内 `assert after == before == frozen` 失败（陈旧结算把 correction 从 `(2.0,0.0)` 推到 `(3.0,0.0)`、`error_history` 追加一项） |

4 个反例都是**真实断言失败**（含失败断言表达式与实得值），不是收集期缺失 / ImportError 导致的全红；`P0` 在同一命令形状下 5 条守卫全过。

定位一律按**测试节点名 + 断言表达式**：本次实跑在 [verify_report.json](verify_report.json) 的 `assertion_locations` 里留下的
`文件:行号`（M1 `test_second_batch_camera.py:431`、M2 `test_second_batch_relations_feedback.py:328`、M3 同文件 `:457`、
M4 `test_third_batch_actions.py:940`/`:967`）只是那次运行的历史输出，会随测试文件增删而漂移，**不作为跨版本锚**。

6 个源码文件 SHA256 在变异前后逐项相同（`source_files_unchanged: true`，见 [verify_report.json](verify_report.json) 的 `source_sha256_before/after`）。

## 4. 文件清单与哈希

原始 `.log` 文件被仓库 `.gitignore` 的 `*.log` 忽略，本目录公开的是**同名 `.txt`**（未 force-add、未改 `.gitignore`）。两列 SHA256 口径不同：
**原始 SHA256** 是本地原始件（`.log` / 源 JSON，Windows CRLF 字节）的 SHA256，用来证明原始件未被改动；
**公开 SHA256** 一律按本目录发布的 **LF 内容**计算（即第 5 节三种变换之后的字节），可用它对公开文件逐份复核。

| 公开文件 | 来源 | 原始 SHA256（本地原始件 CRLF 字节） | 公开 SHA256（LF 内容） |
|---|---|---|---|
| [evidence.json](evidence.json) | `artifacts/second_third_batch/numeric_evidence.json`（内容等价复制：仅 CRLF→LF 归一，行尾空白移除 0 命中，内容未改） | `dad49fa20e63afa73434955dc20b72d24cf4a0d142ee86670d5eda0176021d20` | `6550f4bce2e27ec2bae93afb25dd339d7583f412761d5e431ad117d3e3ffb47d` |
| [feedback_comparison.png](feedback_comparison.png) | `artifacts/second_third_batch/feedback_comparison.png`（二进制，逐字节复制，不做任何文本变换） | `2fb297badc691cdcf17d08df8566405f04e435104a4c84dbaab87cf47fba1666` | `2fb297badc691cdcf17d08df8566405f04e435104a4c84dbaab87cf47fba1666` |
| [pytest_final_remaining.txt](pytest_final_remaining.txt) | `pytest_final_remaining.log` | `1406467ea0fe834e83ac8598a47b521a651b906fb22b0220c900c0f265c6e94e` | `b38cd1b4bed86dc330a4cc5a4c1629a590485e84a4a7af9b93866a57ce2960d0` |
| [pytest_heavy.txt](pytest_heavy.txt) | `pytest_heavy.log` | `401f4138a3d6f8161a0de517dcd52b10d7de21a52efef878be9a1d4d154b5f73` | `23ab0490baacf051fe8030ec296a130644339c1006e6af3dee85b1a0c7945c05` |
| [scripts_final_all.txt](scripts_final_all.txt) | `scripts_final_all.log` | `f5148c14f412304df42de1868ed5e9ad979d53d564b473545cfb6cf2a86e7b5e` | `e7a7f88069e9ffbbf2edd08481ca5ebc9d0a7394448f5c324449ec7554720134` |
| [pytest_final_focused.txt](pytest_final_focused.txt) | `pytest_final_focused.log` | `46d71161b66bcf95d75d5c96a197c94abf37bcddc1e666d10d73dab6591936fa` | `6e5687ba84c46db130c9591ac21b964b0d8fa202a0ac554e44c46aa9bfb59765` |
| [final_registry_gates.txt](final_registry_gates.txt) | `final_registry_gates.log` | `a3a47c99bc016016a69922aa9d7fca09cadf7c9cf5a6d1f255496edaa507376d` | `a3a47c99bc016016a69922aa9d7fca09cadf7c9cf5a6d1f255496edaa507376d` |
| [a_rework_independent.txt](a_rework_independent.txt) | `a_rework_independent.log` | `b5780de7533b78bf273703d80ff7c124823bcaee49c76aca294a2cff47f0d5a7` | `aac0a8a7ad8c38e848899d6f4199a949da9875464e61fbf4052abeeda7417c81` |
| [b_rework_independent.txt](b_rework_independent.txt) | `b_rework_independent.log` | `1666160a2380fad0329623ac77ba241be8dc4971cb6dc73aa1d7302c15ae448f` | `3f865cb378998d593d2eab21eb80b4d1a173111b53f5fbf9614f39262e16dc10` |
| [brain_home_isolated_check.txt](brain_home_isolated_check.txt) | `brain_home_isolated_check.log` | `50548ec0123b38699c7abee4e2c9faa8db5494474bf5e991e7cb31bee4d406a9` | `f84b5635bfbb644439883ee5ef293086117266c15720834b064a50208045d097` |
| [verify_evidence.py](verify_evidence.py) | 本目录新增的复跑脚本（非拷贝；含定点变异检验与有限安全自检） | — | `1b063c8ab873220ec6318690ad5f9ff1978a9ce7ec7415c43157eeaf765ef932` |
| [verify_report.json](verify_report.json) | 由 `verify_evidence.py all --out` **直接写出**的本机实测报告（UTF-8/LF/单尾换行，发布前未再改写；复跑请写到新的 work-dir，不要覆盖它） | — | `15f15f22503797f91e4ef25f0245b3f52598da108b4611c6a093d265874d5d66` |

（本文件 `README.md` 自身哈希不入表，避免自指。）

## 5. 可复核变换（只有这三种）

公开文本一律 **UTF-8、LF 行尾、结尾恰好一个换行**。相对本地原始件，本目录只做了下面三种变换，逐份可复核：

1. **路径前缀脱敏**：本机工作区绝对前缀（形如 `<盘符>:\<若干目录>\lingshu`，含正斜杠写法）→ `<workspace>`；
   本机用户家目录前缀（形如 `<盘符>:\Users\<用户名>`）→ `<user-home>`（本次 0 命中，规则保留为防御性变换）。
   本次实际命中：`pytest_final_remaining` 12 处、`scripts_final_all` 31 处、`pytest_final_focused` 9 处、
   `a_rework_independent` 9 处，合计 **61 处**；其余 4 份 0 处。
2. **CRLF 归一为 LF**：原始件是 Windows CRLF；公开件统一为 LF。换行符个数不变，因此**行数与原始件逐份相同**。
3. **行尾空白移除**：只删除行尾的空格 / Tab 字符，**不删除任何行**。本次只有 `scripts_final_all.txt` 命中 7 行
   （第 55 / 56 / 58 / 62 / 63 / 64 / 66 行）；其余公开文本 0 命中。

8 份日志逐份复核：把原始 `.log` 依次做上述三种变换后，与公开 `.txt` **逐字节相等**；行数与原始件逐份相同，
没有任何 failed / skip / warning / 断言行被删除或改写，读数一字未动。本地原始 `.log` 一个字节都没改
（可用上表「原始 SHA256」逐份核对未改动）。

报告口径一致：`verify_evidence.py` 的 `--out` 一律以 **UTF-8 + LF + 结尾恰好一个换行**写出
（脚本内 3 处 `open(..., newline="\n")` 与 2 处补尾换行，除此之外脚本逻辑逐行未改，探测 / 变异 / 安全自检行为不变），
所以**复跑不会把版本化报告重新写成 CRLF**；[verify_report.json](verify_report.json) 就是 `all --out` 的**直接输出**，
发布前没有再改写一个字节。二进制 [feedback_comparison.png](feedback_comparison.png) 不做任何文本变换，与原始件逐字节相同。

## 6. 未测项与局限

- **F08 基线未测**：`5603e28` 没有 `configure_action`，行动对照无法在基线上复跑，因此基线侧只给 `has_configure_action=False`，**没有基线数值**（不估数）。
- **不是 full pytest**：本包只跑 4 条定点守卫 + 有限探测；全量门禁结论见本目录各份 `*.txt` 原始日志（其中 `pytest_final_remaining` 含 16 项 SciPy 可选跳过，未计作通过）。
- **只在 Windows + .venv Python 3.12 实测**，未在 CI 或其它解释器/平台复跑；不声明 CI 已绿、不声明矩阵已覆盖。
- **F04 的 0.05 m** 只用一对已知位姿相机在多视角重建上测得，不构成通用标定/外参求解/真实设备精度声明；单条位姿观测只记录射线（objects=0）。
- **F06** 只声明关系不满足时 score 降低且 `relation.satisfied=False`，不声明裁决必然翻转，也不是通用图匹配器。
- **安全自检的边界**：`safety` 只覆盖上述 9 项有限检查（目标已存在 / 仓库根 / 祖先目录 / 已存在文件 / 链接、重复导出、合法新目录、tar 成员校验、源码无删除调用），
  是对「脚本不会删除或覆盖既有路径」的定点证明，**不是**对任意路径、任意并发、任意文件系统的通用安全或沙盒证明。
- **F07** 覆盖范围仍是既有回读通道（色词表 red/green/blue；尺寸档只对圆）；48px、supersample≠1、色表外颜色明确 `verifiable=False` 且不声称成功；候选参数只覆盖花纹周期。

## 7. 本包**不包含**的内容

- 429 项测试临时产物的非破坏归档目录（`artifacts/second_third_batch/test_tmp/`、`test_byproducts/` 及其 `test_archive_manifest.json`）；
- 第一批钉定脑包 `artifacts/first_batch/brain/`（本件不用脑端，真实脑端路径由 CI 钉定）；
- 失败/中间态缓存与中间日志（`functional_initial.log`、`pytest_main_remaining.log`、`scripts_remaining.log`、`brain_*.log` 等未纳入；无 `__pycache__`、无 `.pytest_cache`、无覆盖率/失败缓存）；
- **本证据工作包本身未编辑任何实现模块**：实现文件与测试文件的内容改动不在本包内、也不由本包声明（源 PR 侧的内容由提交方维护）；
  本包只做公开导出、复核与复跑，且保持未 add / commit / push、未建 PR / issue 的本地状态。

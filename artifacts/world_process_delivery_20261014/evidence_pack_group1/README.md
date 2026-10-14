# 第 1 组公开证据包（世界过程：时间会意 / 示教迁移 / 运动组合体）

冻结状态：2026-10-14。本包只含公开证据，不含本地数据库。

## 图像与动画（最终演示实跑产物）

- compare.png：源 / 平移 / 旋转缩放三张同视角并排对照图（同一份轨迹数据）。
- source / translated / rotated_scaled 的 PNG 与 GIF：三条迁移变体。
- groups_together.png、groups_leave.png、groups_hidden.png、groups.gif：组合体成组、成员离开、遮蔽推算（红十字为推算位置）。
- readings.json：机器读数。重开引擎取回一致=true，遮蔽推算误差 0，三张对照图实体位置与读数逐位一致。

## 测试读数（冻结指纹后实跑）

- focused_final.txt：聚焦六件套 113 passed，1 skipped（无脑端前置时的脑端用例；脑端实跑见下）。
- brain_core_lead.txt：真实脑端往返 20 passed（脑端固定提交 98d6b899533a82fb1047f952987229c62a9929fb）。
- script_tests_final.txt：CI 所列 18 个脚本式测试全部退出码 0。
- full_tests_final.txt：全量 pytest 1451 passed，16 skipped，73 subtests passed；运行前后四模块 sha256 不变。
- manifest_strict.txt：verify_manifest --strict，63 条全 MATCH，origin-only=2，both=0。

## 定点变异（隔离副本，生产源码未改）

- mutation_process.txt：过程模块 M1–M5 全部因目标断言失败变红，还原后全过。
- mutation_groups.json：组合体 4 项必需变异全部因目标断言失败变红。

## 复跑命令（仓库根目录）

```text
.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_world_process.py tests/test_motion_groups.py tests/test_world_process_core.py tests/test_doc_module_counts_guard.py tests/test_manifest_integrity.py tests/test_import_direction_lint.py -q -p no:cacheprovider
.venv/Scripts/python.exe -X utf8 -B tools/verify_manifest.py --strict --quiet
.venv/Scripts/python.exe -X utf8 -B tools/import_direction_lint.py --json
.venv/Scripts/python.exe -X utf8 -B tools/demo_world_process.py --out-dir <输出目录>
```

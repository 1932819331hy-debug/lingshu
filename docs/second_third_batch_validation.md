# 第二、第三批作者侧验证与贡献说明

## 结论与范围
2026-10-13，基于 `5603e28` 工作树，第二批 F04/F06/F07 与第三批 F08 已实现、自检、作者侧调度复审及另次复测通过。第一批 F01/F02/F03/F05 回归保持。公开提交与远端CI状态以PR线程的固定head为准；以下读数是该功能树在本机的作者侧实测，不代替维护者人审。原始需求来自用户功能缺口报告，源码基准为 https://github.com/FuRongJun-1999/lingshu。

实现由三个互不重叠工作包承担；初轮公开探针找到五类边界问题，三包各第一次返工后全部关闭。所有子代理显式使用 `cline/cline-pass/deepseek-v4.1-flash`，推理强度 `high`。调度者只做规划、审查、验证、环境与交付登记，未代写实现模块。PR作者与贡献申报账号均为rinDBeans，实施与内部技术复查由作者侧AI完成；这不是独立第三方技术审或维护者人审。

## 贡献依赖与去重
- 当前目标main基线为`cb3a6ea2dbd9771d685b2bf5c1fe398abe69e419`；本批测量基线为第一批head `5603e28540ea1596d089f259e6fecd594ea73ccc`。
- 本件是[第一批PR #461](https://github.com/FuRongJun-1999/lingshu/pull/461)的依赖PR。#461未合并前，对main的完整diff含两笔前置提交；本批净增量应单独比较`5603e28..HEAD`，不重复申报F01/F02/F03/F05，亦不重复申报[bounty #44](https://github.com/FuRongJun-1999/dsh-bounty/issues/44)。
- 相邻[PR #471](https://github.com/FuRongJun-1999/lingshu/pull/471)的roll/显式正交基与OBJ/MTL渲染不是本件成果；本件相机载体只支持现有六参数yaw/pitch模型，未测试与#471及#472的联合集成。
- 待维护者裁定：真实位姿摄取要求完整参数与无位姿多视角明确error的接口收紧；启用行动后必要观测不足时的显式预算扩展。作者侧验证通过不等于理论核或合并批准。

## 最终实测环境
- Windows；主体 `.venv` Python3.12.13 / NumPy2.5.3 / Pillow12.3.0 / pytest9.1.1 / xdist3.8.0；脑端 Python3.13.13。
- 脑包 [CI钉定脑端](https://github.com/FuRongJun-1999/dsh-memory/tree/98d6b899533a82fb1047f952987229c62a9929fb) SHA `98d6b899533a82fb1047f952987229c62a9929fb`，与CI钉定一致；记忆库均使用临时隔离根，不写第一批实际记忆数据。
- xdist按项目已声明范围安装；没有修改依赖声明、识别阈值或验收阈值。
- 脑端初次同版本3.12启动超过默认30秒；3.13进一步暴露全局心跳目录权限错误。仅设置MDCG_*父环境无效，因为适配器安全白名单有意不继承它们。最终将白名单允许的HOME/USERPROFILE设为本批临时目录，再用BRAIN_PYTHON明确指定3.13，原脚本23/23通过。没有更改超时、白名单、脚本断言或用户全局目录权限。不能据此声称已验证同版本3.12脑端、完整CI依赖/Python/OS矩阵。

## 最终门禁（各组不重复累加）
| 结论 | 实测与证据 |
|---|---|
| pytest其余集成组通过 | **1354 passed,16 skipped,73 subtests passed,3491 warnings in264.30s**；[公开脱敏完整日志](../bench/results/world_memory/20261013_second_third/pytest_final_remaining.txt)。按CI排除18个独立脚本；两个重件另跑。 |
| 重件通过 | **16 passed in243.79s**；[训练/递归检索日志](../bench/results/world_memory/20261013_second_third/pytest_heavy.txt)。hex_hier/hex_search及依赖代码和断言未改。 |
| 完整pytest覆盖合计 | **1370 passed,16 skipped,73 subtests passed**；这是上面两个不重叠组之和，不把重复针对性测试加入。 |
| 独立脚本全执行 | **18/18 exit0**；[脚本全量日志](../bench/results/world_memory/20261013_second_third/scripts_final_all.txt)。真实脑端 brain_store23/23、wm_verify_unobserved17/17、state_write9/9。 |
| 新批次及第一批针对性复现 | **112 passed,201 warnings in11.73s，无跳过**；[针对性最终日志](../bench/results/world_memory/20261013_second_third/pytest_final_focused.txt)，第一批真实MCP不因缺脑包环境而跳过；本组已包含在集成组中，不重复计总数。 |
| 登记与源码卫生 | 导出清单61/61 MATCH；静默捕获113/113无超限（seven_layer_loop2→1只收紧登记）；git diff --check退出0；hex_composite两副本字节完全相同，未改提取器。最终工具校验见[登记门禁日志](../bench/results/world_memory/20261013_second_third/final_registry_gates.txt)。 |

### 跳过与警告不是通过
- pytest的16项跳过全部是未安装可选SciPy的稀疏后端测试，见集成日志末尾逐项理由；不是本批功能或真实MCP测试跳过。
- hex_cnn脚本[4–6]私有上游素材未导出，仍明确SKIP；coggraph_r4_and_viewer两处未提供--bodies-root的正文互引段未执行。18/18指脚本原有契约退出0，不表示这些未提供前置材料的段已验证。
- 大部分警告为既有可选组件发现告警；日志还保留SyntaxWarning及故意无效目标的RuntimeWarning，不静默删除或计作失败。

## 功能结论 — 证据
### F04：真实位姿与观测绑定持久化、跨引擎重建
- 完整六参数camera与eid/tick/画幅/时间绑定到既有vprim节点与ConditionSpace；沿用原脑库与融合器，无新DB/表。显式camera空/缺项/非有限值明确拒绝，不用默认值造真实位姿 — `read:lingshu/core/core.py::world3d/resolve_ingest_camera`；`probe:pytest tests/test_second_batch_camera.py tests/test_issue211_world3d_build_guard.py`，包含跨引擎、两个同类eid、误差<0.05m与拒绝后状态不变。
- 多视角缺位姿明确error，不静默退回；混存缺元数据观测报camera_pose_incomplete；畸形载荷点名节点，失败不替换旧世界。旧数据可显式multiview=False — 相机与issue211回归、[A返工独立日志](../bench/results/world_memory/20261013_second_third/a_rework_independent.txt) **27 passed**。
- 一条已知位姿观测仅记录射线，objects=0/triangulated=False；不声称已可靠重建。缺省或None的即时兼容不形成持久化真实位姿。

### F06：角色与实际相对关系参与评分
- 保留最大一对一命中，规范检测序稳定绑定；换序score/verdict/roles/relations稳定，role_detections始终指原输入下标 — `read:lingshu/nn/hex_query.py::_match_binding`；[B独立复测](../bench/results/world_memory/20261013_second_third/b_rework_independent.txt) **58 passed,2 subtests passed**。原通配两角色探针现两序均score1.0。
- 同部件改变布局score1.0→0.8；仅改关系声明above0.75 vs below1.0；斜向above+left_of各自按row/col成立，同role有向right_of有r8→r6证据；side_by_side只同排异列 — 同日志实际数字。
- 无relations旧配方键集和评分保持；未知/坏关系、同格或未绑定角色明确不满足。不是全图关系最优搜索，模糊角色使用稳定规范绑定。facts是真值清单，fact仅保留旧主关系投影。

### F07：真实像素驱动的有限反馈
- 原文/parts意图/seed不改，只改局部pattern_period渲染参数；默认最多两轮，每轮最多留一个严格改善且构造性命中不降的改动 — `read:lingshu/nn/hex_gen.py::refine_render`；[B独立复测](../bench/results/world_memory/20261013_second_third/b_rework_independent.txt)。
- 红色小点纹圆96px/seed7、点纹三角96px/seed0均真实像素差异**1→0**；60例独立像素统计**18→7**、7例改善、逐例不劣化。残余7项保留unresolved，不假报全解决。
- 完全覆盖外（橙色）、空parts、缺落格日志、48px或supersample≠1：verifiable=False、before/after=None、不改图；RGB+orange显式covered/uncovered/judged与原因，RGB1→0不外推至orange — `read:lingshu/nn/hex_gen.py::readback_mismatches`，对应独立回归。
- [最终数值与完整留痕](../bench/results/world_memory/20261013_second_third/evidence.json)；[前后原图对照](../bench/results/world_memory/20261013_second_third/feedback_comparison.png)，已与最终代码输出逐像素核对一致。仅RGB可读属性、圆的small/large尺寸项可验证；候选只修花纹周期，不能自动解决任意尺寸、布局、遮挡与极小部件。

### F08：预测驱动的离散沙盒行动
- configure_action/clear_action与观察policy分离；五种离散动作；候选评分只用观测/学得预测，执行器才读物理状态；默认关闭时旧行为保持 — [行动回归](../tests/test_third_batch_actions.py)及最终集成组。
- 同seed42/插入顺序/40tick：静止目标predictive末距**0** vs固定east**16.9189**；巡逻目标**0** vs**15.3052**，predictive37tick真实外推；移动掠近案例预测选east而当前位置greedy选stay，外部反事实0.4<1.077 — [最终数值](../bench/results/world_memory/20261013_second_third/evidence.json)与行动回归。
- 陈旧目标观测target_fresh=False：target_error_status=stale_target_observation，correction与error_history不变；fresh=True同探针仍更新，观测恢复后继续反馈 — `read:lingshu/world/seven_layer_loop.py::ActionDecision.observe_outcome`；独立新增5例全通过。
- 核心门面保留原类型，bool/None/字符串等非法配置不被洗成合法数值，拒绝后配置/位置/待消费动作不变 — `read:lingshu/core/core.py::seven_layer_loop/configure_action`，A独立复测与最终针对性组。
- budget不足时明确披露必要观测扩展，不静默超预算；首次自举段不做行动。固定east是弱可复算对照，不代表通用规划；没有真实设备控制。

## 初轮失败与关闭记录
1. camera={}被默认补齐后落库 → A第一次返工strict真实摄取，公开探针error且新增节点0。
2. action门面float(True)绕过校验 → A第一次返工原类型转发，错误dict且配置不变。
3. stale观测仍更新修正 → C第一次返工不写统计/修正，独立探针和新增5回归通过。
4. 关系换序翻转结论、斜向/同role反向遗漏 → B第一次返工稳定最大匹配及独立关系判据，排列/方向回归通过。
5. coverage外空集合误报零差异 → B第一次返工覆盖账及None不可验证，真实RGB改善保留。
6. 初轮集成四红：双副本两项由B恢复原共享提取器解决；指纹登记刷新；静默捕获计数由114收紧至113。最终上述门禁全部绿。
7. 并发集成预检在64%停止避免返工读写竞态，状态killed，未算通过；环境诊断失败日志保留，最终使用验证过的隔离组合，不隐藏失败过程。

## 复现与交付
使用[调用示例与针对性命令](second_third_batch_usage.md)与[公开证据包及反面对照脚本](../bench/results/world_memory/20261013_second_third/README.md)。日志为目录前缀脱敏、LF归一并移除行尾空白的公开副本，保留所有行与读数；原始/公开文件摘要由证据索引登记；原始环境诊断失败日志与429项临时归档仅在本地保存，不公开、不作可点击交付。第一批脑包克隆、缓存与脑端注册目录均不入本PR。
修改6个实现模块、新增4个测试文件、增强issue211/query_order两件旧回归；模块总数仍61，无须调整README/architecture计数。源码指纹历史由second_third_batch.previous_sha256保存（也保留曾在飞后恢复的提取器登记）。

所有范围内作者侧验证待项已关闭；远端CI和维护者四闸审核状态另在PR/贡献issue中逐项登记。未验证项仅是上述可选SciPy/私有素材/正文互引、同版本3.12脑端和完整CI矩阵，不混作已完成能力。

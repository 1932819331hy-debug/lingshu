# 第 2 组动画证据

补全和重组 GIF 均由现有演示命令生成。
画面实体与逐帧机器读数使用同一份坐标。

| 动画 | 保存帧数 | 去标题后的不同画面数 | 来源 |
|---|---:|---:|---|
| [补全](<completion.gif>) | 12 | 9 | 6 帧观测前缀与 6 帧生成尾部 |
| [重组](<composition.gif>) | 10 | 9 | 两段各 5 帧，连接点位于 tick 5 与 tick 6 之间 |

[补全抽样帧](<completion_frames.png>) 显示 tick 1、6、7、12。
[重组抽样帧](<composition_frames.png>) 显示 tick 1、5、6、10。
[逐帧读数](<readings.json>) 包含相机、实体坐标和来源索引。

补全的 `prefix` 段标为 `observed`。
补全的 `tail` 和两段重组轨迹标为 `generated`。
这些标记只写入演示读数，不回写真实观测历史。

重组接点的两幅实体画面相同，因为两段连接点同位。
其余帧包含可见运动，不能只靠标题差异通过动画测试。

## 复跑

```text
python -X utf8 -B tools/demo_world_process.py --out-dir artifacts/world_process_animation_repro
python -X utf8 -B -m pytest tests/test_world_process_demo.py -q -p no:cacheprovider
```

Pillow 检查实际保存的 GIF 帧数与去标题后的画面差异。
测试还核对相机、坐标、来源标记和两段过程的连接点。
主代理实跑动画与真实脑端 core 测试：37 passed。

## 公开副本

JSON 公开副本只将工作区绝对路径前缀替换为 `<repo>`。
逐帧 tick、坐标、分数和来源字段的值保留。
原始本地日志和 JSON 未改写，数据库不放入本包。

## 限制

- 动画使用构造观测，未验证真实相机输入。
- 没有插帧，展示的就是模块返回的离散采样。
- 相机测试只保证实体中心在画面内，不保证外形全部不越界。
- 本段补全演示的平移量为 0；非零平移由模块测试覆盖。

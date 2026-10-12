# Task／parameter 与零件映射（模拟）

| 工站 | task | parameter | 单位 | 关联零件 | PN | 可能关系 |
| --- | --- | --- | --- | --- | --- | --- |
| OP20 | PressFit | PeakForce | kN | 轴承 | PN-BRG-001 | 尺寸配合、表面状态及装配姿态会影响峰值与曲线 |
| OP30 | Tightening | FinalTorque | N·m | 螺栓 | PN-BLT-001 | 螺纹、摩擦和装配状态会影响扭矩及角度 |

只规定已建模的测量，不为 OP10 虚构测量 task。现实中一个参数可能同时受壳体、润滑、夹具及多个零件影响，迁移时扩展为多对多映射并保留关系依据。

大小写与拼写来自实际模拟数据库。展示中文名称不改变查询用的 task／parameter 键。

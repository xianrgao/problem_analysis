# 控制计划（模拟）

| 工站 | 测量 | 单位 | 演示下限 | 演示上限 | 数据来源 |
| --- | --- | --- | --- | --- | --- |
| OP20 | PressFit／PeakForce | kN | 10 | 16 | MOS measurements |
| OP30 | Tightening／FinalTorque | N·m | 35 | 46 | MOS measurements |

单条 measurement 自带 `lower_limit/upper_limit/result`，趋势中优先保留当时记录的规格与判定，不以当前规格无说明重判历史数据。真实现场可能存在分阶段限值或组合判定，迁移后以受控规则为准。

班产演示规则：OP30 出口完成 TG 去重；上海时区 07:00／19:00 换班，班产低于 5 为停产、5–99 为验线、100–699 为低产、700 及以上为正常范围。数据覆盖不完整时不得按零产判定。

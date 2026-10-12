---
name: curve-analysis
description: 分析压装力—位移、扭矩—角度等过程曲线，校验轴单位和阶段，执行对齐、区段比较及特征提取；仅有终值时使用分布分析而不重建曲线。
metadata:
  version: '1.0.0'
  title: 过程曲线分析
---

# 过程曲线分析

先读取曲线变量的描述和 preview，确认曲线 ID、TG、采样顺序、阶段、x／y、单位、判定及分组字段。演示标准长表为 `curve_id,tg,station_id,kind,phase,sample_index,x,y,x_unit,y_unit,result,batch,supplier,occurred_at`。

## 工作流程

- 明确待验证问题：峰值偏低、起始空行程、特定区段异常、拧紧末段扭矩不稳或批次间分散程度不同。将目标映射到可计算特征。
- 按 [方法与边界](references/methods.md) 检查单位、阶段、缺失、采样顺序和重复轴值，原始曲线不覆盖。
- 使用全量变量在 `python.execute` 中计算。保留对齐配置、特征表、异常区段表；表格通过 SDK 写回并自动生成 preview。
- 根据 HOST 图表能力输出原始／对齐曲线和分组特征比较；相同纵轴量纲方可叠加。
- 输出样本数、异常标准、特征差异、数据质量问题及假设。统计参考包络标为探索性范围，不能取代工艺规格。

仅有 `measurements.value` 等单值时输出终值分布和趋势，明确没有过程曲线。没有真实接触点时，不将统计推测起点包装成设备识别接触点。

辅助脚本 [scripts/features.py](scripts/features.py) 提供单调轴分段曲线特征与公共区间对齐，往返曲线必须先按阶段拆分再使用。

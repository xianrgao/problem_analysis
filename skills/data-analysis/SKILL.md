---
name: data-analysis
description: 对变量池中的表格执行全量 Python 统计、分组比较、质量检查、趋势或分布分析；根据预览编写代码，实际计算始终读取指定变量的完整数据。
metadata:
  version: '1.0.0'
  title: 数据分析
---

# 数据分析

输入是分析问题、数据变量引用及必要口径，不要求携带全量数据。先查看变量描述、列信息和 preview；列语义未知时查 schema 或 process，不能根据列名猜判定含义。

## 执行约定

1. 确认分析粒度、分母、时区、时间窗口、重复／缺失处理及单位。涉及不良率时检查是否包含同期 OK 数据。
2. 根据 [Python 数据契约](references/python-contract.md) 生成代码。通过 `ctx.vars.load("实际变量名称")` 加载全部数据，禁止把 preview 当作计算样本。
3. 在计算前验证必需列与关联键，保留清洗前后行数。不得为了得到更清晰趋势静默删除异常值或 NC。
4. 以 `ctx.vars.save` 保存结果表和结论对象，描述统计口径和覆盖范围。宿主保存本次代码、读写版本及执行日志，表格输出自动生成 preview。
5. 汇报主要发现、实际数量、限制及证据引用。失败时保留代码和错误；只在批准范围内修复，默认最多两次。

默认依赖为 pandas、numpy、scipy；可用模块以 HOST 实际能力为准。图表需要独立 skill 节点时遵守计划确认；已有节点内部使用图表工具按已批准边界执行。

## 参考及脚本

- 生成代码前阅读 [references/python-contract.md](references/python-contract.md)。
- 比较批次、供应商风险时阅读 [references/comparisons.md](references/comparisons.md)。
- `scripts/grouped_quality.py` 提供经过粒度检查的分组不良率函数，可在宿主脚本机制中使用。

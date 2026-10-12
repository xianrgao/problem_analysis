---
name: process
description: 查询 task／parameter 对应零件、PN、上线工站、BOM、PFMEA、CP 和流程知识，为产线数据关联与问题机理分析提供经来源标识的工艺依据。
metadata:
  version: '1.0.0'
  title: 工艺知识
---

# 工艺知识

依据当前问题选择最小知识块：

- 检测指标与零件关系：[references/task-parameter-map.md](references/task-parameter-map.md)。
- PN、安装位置和绑定工站：[references/component-loading.md](references/component-loading.md)。
- 装配层次：[references/bom.md](references/bom.md)。
- 工艺失效与机理：[references/pfmea.md](references/pfmea.md)。
- 控制指标和单位：[references/control-plan.md](references/control-plan.md)。
- 先后工站、返工与班产出口：[references/process-flow.md](references/process-flow.md)。

输出明确 task／parameter、可能关联的零件、PN、安装位置、上线工站和资料来源。不能只靠 PN 名称推断所有工件都安装该零件，需检查型号或生效范围。

本文资料全部标为模拟，迁移时由公司受控工艺资料替换。真实 PFMEA／CP 的编号、版本、生效日期和适用产品必须保留；相互冲突时说明冲突，不能由模型自行决定新版本。

使用检索结果组织后续查询，不把知识关系当成已确认根因。零件规则未知时返回缺口，不虚构 PN 或参数。

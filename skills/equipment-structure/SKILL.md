---
name: equipment-structure
description: 按工站读取设备结构、功能、关联参数、可能失效模式和现场检查方法，为问题调查建立可验证的设备假设。
metadata:
  version: '1.0.0'
  title: 设备结构
---

# 设备结构

根据明确工站渐进读取对应 reference，不一次加载所有设备资料：

- OP10 零件上线与追溯绑定：[references/OP10.md](references/OP10.md)。
- OP20 轴承压装：[references/OP20.md](references/OP20.md)。
- OP30 螺栓拧紧与下线：[references/OP30.md](references/OP30.md)。

将失效 task／parameter 或曲线特征与结构功能建立联系，输出候选结构、可能机理、可观察证据和建议验证。按证据优先级排序，不能仅凭 reference 提到某部件就宣布其故障。

演示资料只用于验证系统流程，不包含真实图纸、公差或维修授权。真实工站未收录时返回知识缺口，不能用名称相似的工站代替。缺少知识与“无可疑设备结构”不是同一回事。

设备突发异常与逐步退化都可能造成 NC，趋势形状只影响假设优先级。需要变化点日志时在计划边界内调用 Splunk；形成新 skill 级计划需用户确认。

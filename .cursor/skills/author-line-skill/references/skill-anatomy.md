# 业务 skill 结构说明

## SKILL.md 元数据

```yaml
---
name: station-alarm-review          # 小写字母、数字和连字符，与目录名一致
description: 一段 20—500 字的触发描述：能力、输入、适用场景、不处理的情况。
metadata:
  version: '1.0.0'                   # 语义化版本，内容变化时递增
  title: 工站报警复核                 # 界面与时间线中显示的中文名称
---
```

`name` 与 `description` 进入宿主变量 `skills`，由模型据此路由；正文只有在选中技能后才加载；references／scripts 只有在执行中按需读取。

## 正文建议章节

1. 输入：变量引用、工站／时间范围、必要参数及其来源。
2. 流程：编号步骤，每步写明读取什么、调用哪个工具、产出什么变量。
3. 判断规则：阈值、口径、分母、证据等级；阈值优先引用 `config/rules.json` 中的配置项名称。
4. 输出约定：变量名、类型（table／json／text／chart）、粒度、关键列、描述模板。
5. 失败与缺口：资料缺失、空结果、单位冲突时如何表述，不得编造。
6. 资源索引：用相对链接列出 references 与 scripts，例如 `[OP20 结构](references/OP20.md)`。

## tools.json

```json
["vars.preview", "python.execute", "skill.reference"]
```

只能填写宿主已注册的工具名。常用组合：

| 场景 | 工具 |
| --- | --- |
| 查看数据结构 | `vars.list`、`vars.preview`、`vars.read` |
| 读取本技能资料 | `skill.reference` |
| 全量计算、重写 | `python.execute` |
| 日志、曲线 | `splunk.query`、`curves.query` |
| 制图、报告、台账 | `chart.create`、`report.create`、`issue.update` |

## acceptance.json

```json
{
  "cases": [
    {
      "id": "op20-basic",
      "request": "复核 OP20 最近一周的压装报警",
      "inputs": ["nc_records"],
      "expect": {
        "outputs": [{"name": "alarm_summary", "kind": "table", "columns": ["station_id", "alarm_code", "pieces"]}],
        "must_mention": ["报警次数与不良工件数分别统计"],
        "must_not_claim": ["已确认根因"]
      }
    }
  ]
}
```

`outputs` 中的名称遵循变量命名规则；`columns` 必须来自已查证的数据结构。验收时比对实际变量、列和结论措辞。

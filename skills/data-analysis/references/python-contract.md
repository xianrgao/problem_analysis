# Python 与全量变量契约

执行器提供 `ctx`，用户代码不自行构建数据库连接、读取变量池私有文件或创建另一套全局字典。输入变量名应从上一步实际输出取得；描述里出现的示例名称不能作为已存在变量使用。

```python
import pandas as pd

source_name = "实际输入变量名称"
frame = ctx.vars.load(source_name)
required = {"tg", "result", "supplier"}
missing = required.difference(frame.columns)
if missing:
    raise ValueError(f"缺少分析所需列: {sorted(missing)}")

# 一个工件对一个供应商，不能将同一 TG 的多个检测视为多个工件。
per_unit = frame.groupby(["supplier", "tg"], dropna=False).agg(
    is_nc=("result", lambda values: values.eq("NC").any())
).reset_index()
summary = per_unit.groupby("supplier", dropna=False).agg(
    unit_count=("tg", "size"), nc_count=("is_nc", "sum")
).reset_index()
summary["nc_rate"] = summary["nc_count"] / summary["unit_count"]
ctx.vars.save(
    "supplier_quality_summary", summary,
    description="按供应商汇总同期去重工件数、至少一次 NC 的工件数及不良率；输入包含 OK 和 NC，缺失供应商单列。"
)
```

示例的 `tg/result/supplier` 只说明 SDK 用法，真实代码必须根据 preview 和已验证字段映射生成。若一个 TG 同时有多个供应商，先按零件／安装位置分层，不能直接沿用此例。

当前技能 `scripts/` 目录中的模块已加入导入路径，版本与已批准计划冻结的技能一致，可直接 `import` 使用，不要复制脚本内容或读取技能目录文件。

允许输出 DataFrame、JSON 可序列化对象和宿主支持的图表／文本产物；具体保存接口以 `docs/api-contract.md` 和当前 HOST 为准。不在 stdout 打印完整 DataFrame、凭据或全量请求体。

默认只读输入：对数据修改前 `copy()`；保存新结果或同名新版本，不能绕开宿主直接覆盖源文件。执行失败前的局部结果不作为完成产物发布。

时间字段优先保留带时区时间；无时区值必须依照来源定义本地化。时区转换和字符串解析失败需统计，不能将 `NaT` 当正常数据继续无说明计算。

数据量过大时通过当前 HOST 支持的 SQL 聚合或分块读取执行，不能静默抽样后称为全量分析。抽样分析需要说明样本设计及局限。

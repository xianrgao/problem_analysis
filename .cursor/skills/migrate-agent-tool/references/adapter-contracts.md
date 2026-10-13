# 适配器契约

全部连接器配置位于工作电脑的 `data/settings.json`。`driver` 取值：`demo`（模拟，默认）、`python`、`script`。

```json
{"driver": "python", "callable": "package.module:function"}
{"driver": "script", "python": "C:/Tools/env/python.exe", "script": "C:/Tools/tool.py", "timeout": 300}
```

`python` 驱动以一个请求字典调用函数；`script` 驱动把同一请求写入 stdin，从 stdout 读取一个 JSON 结果。

## 数据连接器：mos／splunk／curves

| 连接器 | 请求字段 | 结果 |
| --- | --- | --- |
| `mos` | `question`、可选 `sql`、`name`、`description` | `{"data"|"rows"|"data_path": ..., "sql": "实际执行的 SQL"}` |
| `splunk` | `kind`（bindings／changes／logs）、`filters`、`name`、`description` | DataFrame、`rows` 或 `data_path` |
| `curves` | `filters`（station_id／kind／phase／result／tgs／start／end）、`name` | 逐点长表：`curve_id,tg,kind,phase,sample_index,x,y,x_unit,y_unit,result` |

数据结果可以是 `pandas.DataFrame`、`{"data": DataFrame}`、`{"rows": [...]}`、`[...]` 或 `{"data_path": "*.parquet|*.csv|*.xlsx"}`。只返回文字说明会被拒绝。首次绑定列名：`tg, component, pn, position, batch, supplier, bound_at, event_id`；变化点列名：`station_id, structure, change_type, occurred_at, message`。

## 图表连接器：charts

请求：`{"action": "chart", "data_path", "variable", "columns", "kind", "x", "y", "group", "title"}`，`data_path` 为该变量全量 Parquet。结果：Plotly 图表字典 `{"data": [...], "layout": {...}}`、`{"figure": {...}}` 或 `{"image_path": "*.png|*.jpg|*.svg"}`。宿主保存为 `chart` 类型变量并记录来源变量版本。

## 模型：model

```json
{"driver": "openai", "base_url": "https://gateway/v1", "model": "name", "timeout": 120}
{"driver": "python", "callable": "package.module:complete", "base_url": "...", "model": "...", "timeout": 120}
```

自定义函数签名 `complete(messages, options) -> str | dict`，可为异步函数；`options` 不含凭据。返回文本中包含一个 JSON 指令即可，宿主负责提取和校验，不要求原生工具调用。API 密钥可通过环境变量 `LINE_ENGINEER_API_KEY` 提供。

## 执行器：executor

```json
{"python": "C:/isolated/python.exe", "factory": "package.module:create"}
```

`python` 指定分析代码使用的解释器（需能导入 `line_engineer`、pandas、pyarrow）。`factory(settings, variables)` 返回实现 `execute(session_id, code, inputs, producer, scripts=None)`、`execute_shell(session_id, shell, executable, script)`、`kill(session_id)` 的对象，可继承 `line_engineer.execution.runner.ProcessRunner`。

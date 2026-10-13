# 接口文档

本文档描述本机服务的 REST 接口、事件流、计划与变量结构、分析代码 SDK、宿主工具和模型指令协议。服务只监听 `http://127.0.0.1:8765`（端口可通过 `serve --port` 修改），启动后可在 `/docs` 查看自动生成的 OpenAPI 页面。连接适配器的输入输出契约见 [适配器契约](../.cursor/skills/migrate-agent-tool/references/adapter-contracts.md)。

## 1. 通用约定

- 请求与响应均为 UTF-8 JSON，时间为带时区的 ISO 8601 字符串。
- 错误统一返回 `{"detail": "中文原因"}`：`400` 参数无效，`404` 对象不存在，`409` 状态冲突（例如旧版本计划、计划不属于当前会话、会话正忙、模拟会话在模型 API 模式下继续对话）。
- 凭据不会出现在任何响应、事件、变量或导出文件中。

## 2. REST 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 版本、运行模式、模拟数据范围、运行中与排队的任务数 |
| GET／POST | `/api/sessions` | 会话列表（按更新时间倒序）／新建会话 `{"title"?}` |
| GET | `/api/sessions/{sid}` | 会话详情，结构见第 3 节 |
| PATCH | `/api/sessions/{sid}` | 只允许修改 `title`、`pinned`、`archived` |
| POST | `/api/sessions/{sid}/messages` | 发送消息 `{"content"}`；执行中的追问按影响处理 |
| POST | `/api/sessions/{sid}/plans/{plan_id}/approve` | 批准计划 `{"revision"}`，返回 `{"run_id", "status"}` |
| POST | `/api/sessions/{sid}/plans/{plan_id}/revise` | 修改未批准计划 `{"revision", "feedback"}`，生成新版本 |
| POST | `/api/sessions/{sid}/cancel` | 停止当前任务并保留已提交结果 |
| POST | `/api/sessions/{sid}/resume` | 从检查点继续被停止或中断的任务，不重跑成功节点 |
| GET | `/api/sessions/{sid}/stream` | 事件流（SSE），见第 5 节 |
| GET | `/api/sessions/{sid}/variables/{name}/table` | 分页表格：`offset`、`limit`（≤500）、`search`、`sort`、`direction`、`version` |
| GET | `/api/sessions/{sid}/variables/{name}/preview` | 代表性预览，见第 6 节 |
| GET | `/api/sessions/{sid}/variables/{name}/value` | 非表格变量的内容；表格返回预览 |
| GET | `/api/sessions/{sid}/variables/{name}/export` | 导出：表格 `format=csv|xlsx`，报告为 HTML，SQL／代码为文本 |
| POST | `/api/sessions/{sid}/import` | 上传 CSV／Excel（multipart `file`），保存为表格变量 |
| POST | `/api/sessions/{sid}/snapshot` | 从其他会话复制不可变快照 `{"source_session_id", "name", "version"}` |
| GET／POST | `/api/issues` | 问题台账列表／新建 `{"title", "description", "session_id", "owner", "due_date", "station"}` |
| PATCH | `/api/issues/{issue_id}` | 更新台账字段；关闭需 `{"status": "closed", "confirm_close": true}` |
| GET／PATCH | `/api/settings` | 公开设置／修改 `mode`、`model`、`max_parallel_sessions`、`resume_on_restart`、`rules` |
| GET | `/api/skills` | 技能清单：`id`、`name`、`description`、`version`、`tools`、`references`、`scripts` |

问题状态依次为 `open`、`investigating`、`acting`、`verifying`、`closed`，每次变更写入 `history`。

## 3. 会话详情

```json
{
  "session": {"id": "ses_…", "title": "…", "status": "awaiting_approval", "mode": "demo", "pinned": false, "archived": false, "memory": {}},
  "messages": [{"id": "…", "role": "user", "content": "…", "created_at": "…", "turn": 1}],
  "plan": {"…": "当前计划"}, "plans": ["全部计划版本"],
  "events": ["执行事件"], "variables": ["变量元数据"], "runs": ["执行记录与节点状态"]
}
```

会话状态：`idle`、`planning`、`awaiting_approval`、`queued`、`running`、`paused`、`blocked`、`interrupted`、`cancelled`、`completed`。`memory` 保存当前目标、区域／工站／时间条件、已批准计划、关键发现和变量引用；它不替代计划状态或证据。

## 4. 计划

计划由一份结构化 DAG 同时生成内部执行视图和用户业务视图：

```json
{
  "id": "plan_…", "session_id": "ses_…", "revision": 2, "status": "awaiting_approval", "stage": "investigation",
  "title": "业务标题", "summary": "目的与范围", "origin": "demo|model", "scope": {},
  "nodes": [{"id": "s1", "kind": "skill", "skill": "data-analysis", "tool": null, "title": "业务动作", "description": "具体范围",
             "expected_output": "预期结果", "depends_on": [], "params": {}, "progress_message": "一句同时交代目的和行动的说明"}],
  "skill_versions": {"data-analysis": "技能内容指纹"}, "input_versions": [{"name": "nc_records", "version": 2}],
  "fingerprint": "sha256"
}
```

- 计划状态：`awaiting_approval`、`superseded`、`approved`，执行后随任务变为 `running`、`paused`、`interrupted`、`blocked`、`cancelled`、`completed`。
- 批准必须同时匹配会话、计划 ID 和版本，且计划仍处于待确认状态、指纹一致；技能内容在生成后发生变化时拒绝批准。
- 批准后冻结节点、依赖、输出约定、技能快照和输入变量版本；新增调查步骤或改变范围需要生成修订版本并重新批准。

## 5. 执行事件与事件流

每条事件持久化保存，按会话递增编号：

```json
{"id": 128, "session_id": "ses_…", "turn": 1, "run_id": "run_…", "node_id": "s3", "kind": "step", "status": "running",
 "message": "为判断失效是否与开班后的生产状态有关，正在统计不良件在早晚班首小时内的分布。", "details": {}, "created_at": "…"}
```

- `kind`：`step`（节点进度）、`skill`、`reference`、`tool`、`tool_result`、`sql`、`code`、`variable`、`error`、`issue` 等；`details` 保存参数、SQL、代码、变量引用、日志和错误原因，前端默认折叠。
- `status`：`running`、`succeeded`、`completed`、`failed`、`blocked`、`cancelled`。
- `turn` 为该事件发生时会话中用户消息的序号。
- `GET /stream` 返回 `event: update`，`data` 为 `{"session_id", "status", "event_ids"}`，`id` 为最新事件编号。断线重连时浏览器携带 `Last-Event-ID`，服务只补发之后的事件；客户端按事件编号去重并重新读取会话详情。

## 6. 变量与预览

变量元数据：

```json
{"name": "nc_records", "version": 2, "kind": "table", "description": "…", "row_count": 1280, "columns": ["tg", "…"],
 "metadata": {}, "preview_available": true, "producer": "s4", "parents": [{"name": "first_bindings", "version": 1}], "created_at": "…"}
```

- `kind`：`table`、`json`、`text`、`chart`、`report`、`sql`、`code`。同名保存生成新版本，历史版本保留。
- 表格保存时自动生成 `{name}_preview`，字段包括 `source_version`、`available`、`complete`、`total_rows`、`columns`（类型、不同值数量、缺失）、`rows`、`coverage`。通常 30 行，覆盖需要时最多 40 行；不超过 40 行的小表完整展示（`complete: true`）。
- 预览生成失败时数据仍发布，但预览标记 `available: false` 并附 `error`，不会沿用旧版本预览。
- 变量名须匹配 `^[A-Za-z][A-Za-z0-9_.-]{0,99}$`，不能以 `_preview` 结尾，也不能占用 `skills`、`host_capabilities`、`active_plan`、`variable_index`。

## 7. 分析代码 SDK

`python.execute` 在独立进程中运行代码，进程只能看到本次执行的不可变输入快照：

```python
nc = ctx.vars.load("nc_records")          # 全量数据；可选 version= 校验快照版本
ctx.vars.save("nc_records", rewritten, description="补充首次零部件绑定信息后的 NC 明细")
ctx.vars.save("pattern_findings", {"items": [...]}, description="规律挖掘发现", kind="json")
```

- DataFrame 默认保存为 `table`，字符串为 `text`，其他 JSON 可序列化对象为 `json`。
- 实际读取的版本记录为新变量的来源；全部代码执行成功后才由宿主统一发布，失败或超时不留下半成品。
- 在技能内执行时，该技能冻结版本的 `scripts/` 目录追加到导入路径，例如 `from grouped_quality import grouped_quality`；技能外执行时不提供。
- 执行进程不继承 `LINE_ENGINEER_API_KEY`；超时后终止进程及子进程。
- 解释器可由 `executor.python` 指定，执行器可由 `executor.factory` 替换为公司隔离环境。

## 8. 宿主工具

工具清单作为变量 `host_capabilities` 提供给模型，实际可用集合以 `/api/skills` 与宿主注册结果为准：

| 工具 | 参数 | 说明 |
| --- | --- | --- |
| `vars.list`／`vars.preview`／`vars.read` | `name`、`version` | 变量目录、预览、非表格内容 |
| `skill.reference` | `path` | 读取当前技能的 references／scripts |
| `mos.query` | `question`、可选 `sql`、`name`、`description` | MOS 子 agent 只读取数，返回 `variable_name`、`row_count`、`description`、`version` |
| `splunk.query` | `kind`、`filters`、`name` | 绑定、变化点、日志 |
| `curves.query` | `filters`、`name` | 过程曲线长表 |
| `python.execute` | `code` | 全量计算与重写 |
| `shell.execute` | `shell`、`script` | 仅在检测到 PowerShell／Bash 时注册 |
| `chart.create` | `variable`、`kind`、`x`、`y`、`group` | 标准图表产物，记录来源变量版本 |
| `issue.update` | `issue_id`、`fields` | 更新台账；关闭需用户在台账确认 |
| `report.create` | — | 离线 HTML 报告，图表脚本内嵌 |

## 9. 模型指令协议

模型可以在普通文本中返回一个 JSON 指令，宿主负责提取、校验并执行，不要求原生工具调用：

| 场景 | 指令 |
| --- | --- |
| 路由 | `{"type": "skill", "name": "problem-analysis", "category": "problem_analysis"}`、`{"type": "plan", "plan": {…}}`、`{"type": "answer", "message"}`、`{"type": "ask", "message"}` |
| 技能内部循环 | `{"type": "tool", "tool", "arguments", "message"}`、`{"type": "plan", "plan"}`、`{"type": "finish", "message", "outputs"}`、`{"type": "ask", "message"}` |

技能内部形成的新技能级计划同样进入用户确认流程；`finish.outputs` 只保留实际存在的变量。SQL 与代码默认最多自动修复两次，超过后节点标记为受阻并保留原始记录。

## 10. 技能包

```
skills/<name>/SKILL.md      元数据（name、description、metadata.version、metadata.title）与正文
skills/<name>/tools.json    允许调用的宿主工具
skills/<name>/references/   按需读取的资料
skills/<name>/scripts/      确定性脚本
skills/<name>/acceptance.json  验收样例
```

技能内容指纹覆盖全部文件；修改技能后，已生成但未批准的计划需要重新生成。编写与校验方法见 `.cursor/skills/author-line-skill`。

---
name: migrate-agent-tool
description: 把公司已有的 Python 函数、脚本或 agent（MOS 取数 agent、Splunk agent、图表程序、模型网关、隔离执行环境）接入 problem_analysis 工作台。先检查入口、依赖、鉴权、返回值、日志和异常，再通过连接适配器复用，不重写已验证的逻辑。用户要求迁移、包装或对接现有程序时使用。
---

# agent／工具迁移

目标是复用：已有程序继续负责它已经验证过的检索、计算或渲染，工作台只通过适配器调用它，并由宿主发布变量、版本和预览。真实迁移集中修改 `data/settings.json`（工作电脑本地）、适配器模块、字段映射和知识文件，尽量不修改编排器、变量池和通用执行器。

## 步骤

1. **检查现有程序**：运行检查脚本，得到入口、参数、依赖、鉴权读取方式、返回语句、日志和异常的清单：

   ```powershell
   python .cursor/skills/migrate-agent-tool/scripts/inspect_tool.py C:\Tools\legacy\splunk_agent.py
   ```

   逐项核对 [检查清单](references/inspection-checklist.md)。不确定的行为向维护人确认，不凭函数名猜测。
2. **选择接入方式**（契约见 [适配器契约](references/adapter-contracts.md)）：
   - 与工作台同一 Python 环境可导入 → `driver: "python"`，`callable: "module:function"`。
   - 依赖独立解释器或冲突的包 → `driver: "script"`，配置该程序自己的 `python` 和 `script`，JSON stdin/stdout 交互；不要把工作台依赖装进已有环境。
3. **编写适配器**：从 `examples/adapters/` 复制最接近的示例：

   | 现有程序 | 示例 | 宿主工具 | 身份 |
   | --- | --- | --- | --- |
   | MOS 取数 agent | `mos_agent.py` | `mos.query` | 保留子 agent：问题进，实际 SQL 与真实行出 |
   | Splunk agent | `splunk_agent.py` | `splunk.query` | 由 `skills/splunk` 技能包装调用 |
   | 曲线导出脚本 | `stdin_script.py` | `curves.query` | 独立解释器脚本 |
   | 图表程序 | `chart_program.py` | `chart.create` | 输出标准图表产物 |
   | 非 OpenAI 兼容模型网关 | `custom_model.py` | 模型调用 | 自定义适配接口 |
   | 公司隔离执行环境 | `isolated_executor.py` | `python.execute` | 替换默认进程执行器 |

   适配器只做参数映射、字段映射和结果规范化。返回真实数据（DataFrame、`rows` 或 `data_path`），不能只返回模型写的数据说明；行数和数据必须来自实际执行。
4. **鉴权**：凭据保留在已有程序原来的来源（凭据管理器、受保护配置文件、环境变量）。不要写入 `settings.json` 的可展示字段、代码示例、日志或报告；工作台设置接口只显示连接器 driver。
5. **配置**：在工作电脑的 `data/settings.json` 中设置 `connectors.<mos|splunk|curves|charts>`、`model` 或 `executor`，参考 `config/settings.example.json`。真实连接失败时报错停止，不能回退到模拟数据。
6. **验证契约**：

   ```powershell
   python .cursor/skills/migrate-agent-tool/scripts/check_adapter.py splunk --request "{\"kind\": \"bindings\", \"filters\": {\"tgs\": [\"TG00000001\"]}}"
   ```

   检查返回类型、列名映射、空结果、超时和异常信息。再在工作台新建模型 API 模式会话，按 `skills/*/acceptance.json` 中的请求实测。
7. **更新知识**：字段映射或口径变化时，同步修改对应业务技能的 references（例如 `skills/splunk/references/query-semantics.md`），并用 `author-line-skill` 的校验器检查。

## 约束

- MOS 只读；适配器拒绝写操作，并把实际执行的 SQL 一并返回以便追溯。
- 已有程序的分页、重试、速率限制和日志保持原样；适配器不吞掉异常，错误信息要能定位来源系统。
- 日志写 stderr 或已有日志文件，脚本模式的 stdout 只输出一个 JSON 结果。
- 普通进程隔离不是强安全沙盒；有公司隔离环境时通过 `executor.factory` 替换。

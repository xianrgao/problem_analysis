# 迁移手册

本手册说明如何把工作台从个人电脑（模拟数据）迁移到公司 Windows 10／11 x64 工作电脑，并接入公司允许使用的模型 API、MOS、Splunk、曲线和图表程序。

迁移原则：

- 真实迁移集中修改 `data/settings.json`、连接适配器、字段映射和知识文件，尽量不修改编排器、变量池和通用执行器。
- 真实连接失败时报错停止，不会自动切换为模拟结果；模拟环境创建的会话不能在模型 API 模式下继续。
- 凭据保留在工作电脑本地，不进入前端展示、代码示例、变量、报告或导出日志。

## 1. 构建离线包（可联网的电脑）

需要 Python 3.12／3.13（带 pip）和项目依赖；仅在需要重新构建前端时需要 Node.js。

```powershell
python packaging/build_offline.py --build-frontend --download-python 3.13.16
# 已下载官方安装包时：
python packaging/build_offline.py --python-installer D:\installers\python-3.13.16-amd64.exe
```

输出位于 `release/`：`line-engineer-<版本>-win-x64/` 目录及同名 zip。包内包含：

| 内容 | 位置 |
| --- | --- |
| 源码、业务 skills、开发 skills、配置示例、测试 | `src/`、`skills/`、`.cursor/skills/`、`config/`、`tests/` |
| 预构建前端 | `web/dist/` |
| 锁定依赖与 Windows x64 CPython 3.13 wheels | `requirements.lock`、`wheels/` |
| Python 用户级安装包（可选） | `python/python-3.13.x-amd64.exe` |
| 90 天固定种子模拟数据 | `data/demo/` |
| 安装、启动、校验脚本 | `install.cmd`、`start.cmd`、`install.ps1`、`start.ps1`、`verify.ps1` |
| 校验清单与构建信息 | `SHA256SUMS.txt`、`manifest.json` |
| 接口文档与本手册 | `docs/` |

可用 `python packaging/build_offline.py --verify <离线目录>` 在复制前后核对文件。

## 2. 在工作电脑安装（普通用户权限）

1. 将 zip 复制到工作电脑并解压到用户有写权限的目录，路径可以包含中文和空格，例如 `D:\工作台\line-engineer-1.0.0-win-x64`。
2. 双击 `install.cmd`。脚本依次：
   - 按 `SHA256SUMS.txt` 校验全部文件，缺失或被修改时停止；
   - 查找 Python 3.13：`-Python` 参数 → 本目录 `.bootstrap` → `%LOCALAPPDATA%\Programs\Python\Python313` → `py -3.13`；都没有时用包内官方安装包以当前用户身份静默安装到本目录 `.bootstrap\python313`，不修改 PATH、不关联文件、不安装启动器；
   - 创建项目独立环境 `.runtime`，只从 `wheels/` 安装 `requirements.lock`（`--no-index`，不访问公网）；
   - 校验或生成模拟数据。
3. 已有其他 Python 时不会被覆盖。需要指定解释器时运行：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Python "C:\Users\me\AppData\Local\Programs\Python\Python313\python.exe"
   ```

4. 双击 `start.cmd` 启动，服务就绪后自动打开 `http://127.0.0.1:8765`。端口被占用时运行 `.\start.ps1 -Port 8780`；关闭窗口即停止服务。

应用数据（会话、变量、设置）默认保存在包内 `data/`；可通过环境变量 `LINE_ENGINEER_DATA` 或 `main.py --data-dir` 指定其他目录。升级时用新版本目录替换程序文件并保留 `data/`。卸载时删除整个目录；若安装脚本安装过 Python，可在“应用和功能”中移除对应的 Python 3.13（当前用户）。

## 3. 安装后验证（模拟环境）

```powershell
.\.runtime\Scripts\python.exe -m pytest -q
```

测试覆盖两阶段调查、计划确认不可绕过、旧版本批准、范围修改、取消与恢复、重启恢复、并行排队、跨会话快照、台账关闭确认、适配器契约、执行超时与凭据隔离等场景。随后在界面中输入“分析 OP20 最近两周压装力 NC 增多的原因”，确认计划卡片、执行时间线、分析侧栏和报告导出正常。

模拟演示只验证流程，不能替代第 6 节的真实模型验收。

## 4. 接入公司模型

在界面“设置”中选择“模型 API”，填写地址、模型名称、超时和 API 密钥；或编辑 `data/settings.json`（参考 `config/settings.example.json`）。

```json
"model": {"driver": "openai", "base_url": "https://company-gateway/v1", "model": "company-chat-model", "timeout": 120}
```

- 默认使用 OpenAI 兼容的 `/chat/completions` 协议，不要求供应商支持原生工具调用；普通文本中的 JSON 指令经过校验后执行。
- 非兼容网关使用自定义适配器：`{"driver": "python", "callable": "examples.adapters.custom_model:complete", …}`，函数签名 `complete(messages, options)`，`options` 不含凭据。
- API 密钥可在设置中填写（只保存在本机 `data/settings.json`，界面只显示“已设置”），也可通过环境变量 `LINE_ENGINEER_API_KEY` 提供。自定义适配器从原来的凭据来源读取令牌。

切换模式后新建会话再提问。

## 5. 接入 MOS、Splunk、曲线和图表程序

在 Cursor 中使用开发技能 `.cursor/skills/migrate-agent-tool`：

1. 检查已有程序：`python .cursor/skills/migrate-agent-tool/scripts/inspect_tool.py <已有脚本.py>`，核对入口、依赖、鉴权、返回值、日志和异常。
2. 从 `examples/adapters/` 复制最接近的示例，只做参数映射、字段映射和结果规范化：
   - MOS 保留子 agent 身份：接收自然语言问题，返回实际执行的 SQL 与真实数据；
   - Splunk 由 `skills/splunk` 技能包装已有 agent；
   - 图表程序返回 Plotly 图表或图片，宿主保存为标准图表产物；
   - 依赖独立环境的脚本使用 `driver: "script"` 并配置该程序自己的 `python`。
3. 在 `data/settings.json` 的 `connectors` 中配置 `mos`、`splunk`、`curves`、`charts`。
4. 验证契约：`python .cursor/skills/migrate-agent-tool/scripts/check_adapter.py splunk --request "{\"kind\": \"bindings\", \"filters\": {\"tgs\": [\"TG00000001\"]}}"`。
5. 字段映射或口径变化时同步修改对应技能的 references，并运行 `python .cursor/skills/author-line-skill/scripts/validate_skill.py --all skills --strict`。

公司提供隔离执行环境时，配置 `"executor": {"factory": "package.module:create"}`（参考 `examples/adapters/isolated_executor.py`）；只需更换分析代码解释器时配置 `"executor": {"python": "C:/…/python.exe"}`。普通进程隔离不是强安全沙盒。

## 6. 经验规则与业务知识

- 换班时间、开班窗口、出口工站、班产阈值（`<5`、`<100`、`≥700`）、过站间隔（`>4 小时`）、批次集中度（`>80%`）、零件与参数映射及曲线配置位于 `config/rules.json`。
- 工作电脑上的差异写入 `data/settings.json` 的 `rules`，只覆盖列出的项目；未定义的规则名称会被拒绝。
- 数据库字段、设备结构、工艺知识和批次规则写在业务技能的 references 中。新增或修改业务技能使用 `.cursor/skills/author-line-skill`：先查证真实 schema 与工艺资料，未知字段不得臆造，提交前通过严格校验。

## 7. 真实模型验收清单

真实自然语言规划和 SQL 生成需单独验收，不能用模拟演示代替。建议在公司环境逐项记录会话编号和结论：

| 项目 | 验收方法 |
| --- | --- |
| 未预设提问与同义表达 | 用不同说法询问同一工站、时间范围的 NC，核对 SQL 与行数 |
| 多表 JOIN 与重复计数 | 要求关联工件、过站、检测和 NC，核对不良工件数与 NC 事件数分别统计 |
| 追问修改 | “只看夜班”“换成上周”，确认生成新计划并需要重新批准 |
| 空结果 | 询问不存在的工站或时间，确认如实返回空结果，不编造数据 |
| SQL／代码修复 | 观察错误被记录、最多自动修复两次、超过后标记受阻 |
| 计划确认与版本 | 修改计划后旧卡片不能批准；其他会话的“OK”不能批准当前任务 |
| 恢复 | 执行中关闭服务再启动，确认从检查点继续、已停止的任务不恢复 |
| 结论措辞 | 区分规则线索、统计关联与已验证根因；证据不足时说明缺口 |

## 8. 常见问题

| 现象 | 处理 |
| --- | --- |
| 安装时提示校验失败 | 重新复制 zip 并完整解压，不要只复制部分目录 |
| 提示未找到 Python 3.13 | 确认包内有 `python/` 安装包，或用 `-Python` 指定已有的 3.13 |
| 依赖安装失败 | 确认 `wheels/` 完整且未混入其他版本；重新构建离线包 |
| 端口被占用 | `.\start.ps1 -Port 8780` |
| 模型超时或 HTTP 错误 | 检查地址、模型名称、超时和网络策略；工作台不会回退为模拟结果 |
| 适配器报错 | 错误信息会标明来源系统；用 `check_adapter.py` 单独复现 |

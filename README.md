# LINEA · 产线硅基工程师

面向现场工程师的本地问题分析工作台，通过技能、子 agent 和可追溯变量组织取数、统计、曲线分析、图表与问题闭环。

> 实施计划中的功能已在模拟环境中实现，并通过自动验收与浏览器验收；真实模型、公司数据源接入及 Windows 实机安装需在工作电脑上验收。已完成与待完成内容见 [开发状态](docs/development-status.md)，完整设计见 [实施计划](docs/implementation-plan.md)。

## 项目结构

- `src/line_engineer/`：模块化 Python 后端，含编排、变量、执行、连接器和领域分析。
- `web/`：React／TypeScript 工程分析工作台。
- `skills/`：业务技能正文、分层参考资料及可执行示例。
- `.cursor/skills/`：开发技能，用于编写产线 skill 和迁移现有 agent／工具。
- `config/`：经验规则（`rules.json`）与设置示例。
- `examples/adapters/`：模型、MOS、Splunk、图表程序、脚本与执行器的适配示例。
- `packaging/`：离线包构建与 Windows 安装／启动脚本。
- `tests/`：自动验证用例。
- `docs/`：完整方案、开发状态、[接口文档](docs/api-contract.md)与[迁移手册](docs/migration-guide.md)。

## 启动

```powershell
python -m pip install -e ".[test]"
cd web; npm ci; npm run build; cd ..
python main.py serve --open
```

服务地址为 `http://127.0.0.1:8765`。首次启动生成 90 天模拟数据；`python main.py init-demo` 可单独生成。应用数据默认保存在 `data/`，可用 `--data-dir` 或 `LINE_ENGINEER_DATA` 指定。前端开发时可运行 `npm run dev`，`/api` 代理到本机服务。

工作电脑使用离线包：解压后双击 `install.cmd`，再双击 `start.cmd`，步骤见 [迁移手册](docs/migration-guide.md)。

## 当前验证

```powershell
python -m pytest -q
```

共 46 项测试：领域分析 22 项，覆盖模拟数据关联、只读 SQL、班次边界、首次绑定、安全重写、批次与趋势分母、曲线数据质量、异常区段定位与曲线配置等；API 与编排 24 项，覆盖两阶段调查（含 OP30 供应商反例）、计划确认、追问、停止／恢复、重启恢复、并行排队、事件流补发、适配器契约、执行隔离及离线包校验等。详细验证情况见 [开发状态](docs/development-status.md)。

## 运行与迁移目标

目标环境为 Windows 普通权限、Python 3.13，前端预构建后分发；`packaging/build_offline.py` 生成离线安装包。MOS、Splunk、模型和已有图表程序使用集中适配器接入。个人电脑只使用明确标记的模拟数据，公司配置和凭据由工作电脑提供。

源码不包含生产数据、密钥、本地虚拟环境、运行时和生成数据库。依赖通过工程配置安装，运行数据由模拟生成器创建。

## 交互规范

技能级计划必须经过用户确认。执行进度以一句专业说明同时交代分析目的和具体行动，不使用第一人称；skill、参考资料、代码和数据详情默认折叠。会话变量隔离，表格重写保留版本并同步预览。

# LINEA · 产线硅基工程师

面向现场工程师的本地问题分析工作台，通过技能、子 agent 和可追溯变量组织取数、统计、曲线分析、图表与问题闭环。

> 当前是开发中的源码快照，后端编排、API 与前端构建已接通，尚未完成浏览器视觉验收与真实模型接入验收。已完成与待完成内容见 [开发状态](docs/development-status.md)，完整设计见 [实施计划](docs/implementation-plan.md)。

## 项目结构

- `src/line_engineer/`：模块化 Python 后端，含编排、变量、执行、连接器和领域分析。
- `web/`：React／TypeScript 工程分析工作台。
- `skills/`：业务技能正文、分层参考资料及可执行示例。
- `tests/`：自动验证用例。
- `docs/`：完整方案、开发状态与后续迁移文档。

## 启动

```powershell
python -m pip install -e ".[test]"
cd web; npm ci; npm run build; cd ..
python main.py serve --open
```

服务地址为 `http://127.0.0.1:8765`。首次启动生成 90 天模拟数据；`python main.py init-demo` 可单独生成。应用数据默认保存在 `data/`，可用 `--data-dir` 或 `LINE_ENGINEER_DATA` 指定。前端开发时可运行 `npm run dev`，`/api` 代理到本机服务。

## 当前验证

Python 领域分析测试已通过：

```powershell
python -m pytest tests/test_domain.py -q
```

当前已覆盖模拟数据关联、只读 SQL、班次边界、首次绑定、安全重写、批次分母与曲线数据质量等 18 项测试。编排与 API 的端到端验证情况见 [开发状态](docs/development-status.md)。

## 运行与迁移目标

目标环境为 Windows 普通权限、Python 3.13，前端预构建后分发；计划提供离线安装包。MOS、Splunk、模型和已有图表程序使用集中适配器接入。个人电脑只使用明确标记的模拟数据，公司配置和凭据由工作电脑提供。

源码不包含生产数据、密钥、本地虚拟环境、运行时和生成数据库。依赖通过工程配置安装，运行数据由模拟生成器创建。

## 交互规范

技能级计划必须经过用户确认。执行进度以一句专业说明同时交代分析目的和具体行动，不使用第一人称；skill、参考资料、代码和数据详情默认折叠。会话变量隔离，表格重写保留版本并同步预览。

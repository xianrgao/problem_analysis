---
name: author-line-skill
description: 在 problem_analysis 项目中为产线工程师编写或修改业务 skill（skills/ 目录）。根据真实数据库 schema、工站知识、批次规则和工艺文件确定能力边界、触发描述、输入输出、变量约定、正文、references、scripts、工具声明和验收样例，并用校验器检查。用户要求新增产线 skill、把工艺资料整理成 skill、或调整现有业务 skill 时使用。
---

# 产线 skill 编写

本技能面向 Cursor 中维护本项目的开发者，不属于工程师日常使用的业务技能库。业务技能放在仓库根目录 `skills/<name>/`，由应用的技能注册表发现；本目录只指导如何编写它们。

## 先查证，再动笔

1. 收集真实资料：数据库 schema 与字段说明、工站结构／功能／失效模式／检查方法、零件与供应商编码规则、task／parameter 映射、BOM、PFMEA、CP、工艺流程。资料来源和版本写入 references 开头。
2. 未知字段、单位、时区、关联键和业务含义必须向资料或负责人查证。不能根据字段名猜测含义，不能编造样例数据。查证前在模板中保留 `<待查证：…>` 标记，校验器会拒绝未完成的标记。
3. 阅读现有技能写法：`skills/rewrite/SKILL.md`（变量重写约定）、`skills/splunk/SKILL.md`（包装已有 agent）、`skills/data-analysis/references/python-contract.md`（Python 与变量池契约）。宿主可用工具见 `src/line_engineer/orchestration/host.py` 中的 `TOOLS`。

## 技能包结构

```
skills/<name>/
  SKILL.md          元数据 + 正文（第二层披露）
  tools.json        本技能允许调用的宿主工具
  references/       第三层：按工站／知识块拆分，按需读取
  scripts/          可执行的确定性实现（Python）
  acceptance.json   验收样例
```

从 [templates](templates/) 复制起步：`SKILL.md.template`、`tools.json`、`acceptance.json` 以及 `references/` 下的数据库结构、工站知识、批次规则和工艺文件模板。结构与字段要求见 [skill 结构说明](references/skill-anatomy.md)。

## 编写要点

- **能力边界与触发描述**：`description` 写清“做什么、输入什么、何时使用、不做什么”。编排器只依据元数据选技能，描述含糊会导致误路由。问题调查类请求只进入 `problem-analysis`，其他技能不要把自己写成调查入口。
- **输入输出与变量约定**：输入写成变量引用（名称来自上一步实际输出），输出写清变量名、类型、粒度、关键列和描述。表格由宿主自动生成 `{name}_preview`；变量名不能以 `_preview` 结尾，也不能占用 `skills`、`host_capabilities`、`active_plan`、`variable_index`。
- **渐进披露**：正文只放流程、判断规则和资源索引；工站资料、规则表、长样例放 references，并在正文中用相对链接引用，按工站或知识块拆分，避免一次加载全部资料。
- **工具声明**：`tools.json` 只列出真正需要的已注册宿主工具。取数走 `mos.query` 计划节点或 MOS 子 agent，不在技能里写数据库连接。`shell.execute` 仅在宿主检测到 PowerShell／Bash 时注册，不能作为唯一实现路径。
- **结论措辞**：区分规则线索、统计关联与已验证根因；集中度高不代表风险高；没有资料或变化点记录不能据此排除设备。进度说明是一句同时交代目的和行动的专业表述，不用第一人称。
- **scripts**：确定性的计算放进脚本并在正文说明调用方式。本技能内的 `python.execute` 可直接导入冻结版本的脚本（例如 `from parse_batch import parse_batch`）；脚本模块名不要与标准库或已安装包重名。脚本以参数接收数据或 `ctx`，通过 `ctx.vars.load/save` 读写变量，不读取变量池私有文件。

## 校验与验收

```powershell
python .cursor/skills/author-line-skill/scripts/validate_skill.py skills/<name>
python .cursor/skills/author-line-skill/scripts/validate_skill.py --all skills --strict
```

校验器检查：元数据（名称与目录一致、描述长度、版本）、正文与引用路径、未被引用的资源、工具依赖是否已在宿主注册、脚本可编译、验收样例中的变量输出约定，以及残留的 `<待查证：…>` 标记。`--strict` 将警告视为错误，新技能提交前应通过严格模式。

最后在模拟环境或公司环境用 `acceptance.json` 中的请求实测：确认计划需要用户批准、输出变量与约定一致、preview 与源数据版本一致、结论没有超出证据。修改技能内容会改变技能指纹，已生成但未批准的计划需要重新确认。

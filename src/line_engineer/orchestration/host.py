from __future__ import annotations
import asyncio
import base64
import html
import json
import os
import re
import shutil
from pathlib import Path
import pandas as pd
import plotly.io as pio
from ..storage.database import uid, now
from ..connectors.python_adapter import call as call_adapter, invoke, to_frame


TOOLS = {
    "vars.list": "列出当前会话变量名称、描述、类型、版本和行数；不返回全量表格。",
    "vars.preview": "参数 name，返回变量结构和30至40行代表性预览。",
    "vars.read": "参数 name，读取系统变量或JSON/text；表格默认返回preview。",
    "skill.reference": "参数 path，读取当前skill的具体references或scripts文件。",
    "mos.query": "参数 question、可选sql/name；MOS子agent写SQL，实际查询后保存全量表格并返回变量描述。",
    "splunk.query": "参数 kind(bindings/changes/logs)、filters、可选name；保存查询数据。",
    "curves.query": "参数 filters(station_id/kind/phase/result/tgs/start/end)、可选name；保存过程曲线长表。",
    "python.execute": "参数 code；执行Python代码。ctx.vars.load(name)读全量，ctx.vars.save(name,value,description,kind)写变量。",
    "shell.execute": "参数 shell(powershell/bash)、script；在会话工作目录执行脚本，保存脚本与输出。仅在宿主检测到对应 shell 时注册。",
    "chart.create": "参数 variable,kind(auto/line/bar/box/scatter/histogram),x,y,group；基于全量变量制作图表。",
    "issue.update": "参数 issue_id,fields；更新问题台账证据与措施，关闭需用户在台账确认。",
    "report.create": "生成当前会话的HTML分析报告与图表附件。",
}


SHELL_TOOL = "shell.execute"
EVIDENCE_REF = re.compile(r"`([A-Za-z][A-Za-z0-9_.-]{0,99})`")


def detect_shells():
    """PowerShell and Bash are registered only when actually present on this host."""
    bash = shutil.which("bash")
    # The Windows WSL launcher (System32\bash.exe) cannot run scripts given by Windows path.
    if bash and os.name == "nt" and Path(bash).parent.name.lower() == "system32":
        bash = None
    found = {"powershell": shutil.which("pwsh") or shutil.which("powershell"), "bash": bash}
    return {name: path for name, path in found.items() if path}


class Host:
    def __init__(self, app):
        self.app = app
        self.shells = detect_shells()

    def tools(self):
        tools = dict(TOOLS)
        if self.shells:
            tools[SHELL_TOOL] = f"{TOOLS[SHELL_TOOL]} 当前可用：{', '.join(self.shells)}。"
        else:
            tools.pop(SHELL_TOOL)
        return tools

    def capabilities(self):
        return [{"name": k, "description": v} for k, v in self.tools().items()]

    def script_paths(self, skill, version):
        """The calling skill's frozen scripts directory, importable from python.execute."""
        if not skill:
            return []
        scripts = self.app.registry.snapshot(skill, version) / "scripts"
        return [str(scripts)] if scripts.is_dir() else []

    def context(self, sid):
        app = self.app
        return {"skills": app.registry.discover(), "host_capabilities": self.capabilities(),
                "active_plan": app.active_plan(sid),
                "variable_index": [app.variables.public(v) for v in app.variables.list(sid)]}

    def save(self, sid, name, value, description, kind=None, node=None, parents=None, metadata=None):
        return self.app.variables.save(sid, name, value, description, kind, {"mode": self.app.db.get("session", sid)["mode"], **(metadata or {})}, node, parents)

    async def call(self, sid, tool, args, skill=None, version=None, run=None, node=None):
        app = self.app
        if tool not in self.tools():
            raise ValueError(f"未注册工具 {tool}")
        if tool == "vars.list":
            return self.context(sid)["variable_index"]
        if tool in ("vars.preview", "vars.read"):
            name = args.get("name") or args.get("variable") or args.get("variable_name")
            if name in app.variables.RESERVED:
                return self.context(sid)[name]
            return app.variables.preview(sid, name, args.get("version"))
        if tool == "skill.reference":
            if not skill:
                raise ValueError("读取引用必须处于技能上下文")
            path = args["path"]
            text = app.registry.read(skill, path, version)
            app.db.event(sid, "已加载本次分析需要的参考资料。", "reference", "succeeded", {"skill": skill, "reference": path, "content": text}, run_id=run, node_id=node)
            return {"path": path, "content": text}
        if tool == "mos.query":
            return await self.mos(sid, args, run, node)
        if tool == "splunk.query":
            cfg = app.settings.values.get("connectors", {}).get("splunk", {"driver": "demo"})
            if cfg["driver"] == "demo":
                from ..domain.sources import DemoSplunk
                frame = await asyncio.to_thread(DemoSplunk(app.demo_dir).query, args.get("kind", "bindings"), args.get("filters", {}))
            else:
                frame = await asyncio.to_thread(invoke, cfg, args)
            meta = self.save(sid, args.get("name", "first_bindings" if args.get("kind", "bindings") == "bindings" else "equipment_changes"), frame,
                             args.get("description") or f"Splunk {args.get('kind', 'bindings')} 查询结果，包含绑定或事件历史", node=node)
            return {"variable_name": meta["name"], "row_count": meta["row_count"], "description": meta["description"], "version": meta["version"]}
        if tool == "curves.query":
            cfg = app.settings.values.get("connectors", {}).get("curves", {"driver": "demo"})
            if cfg["driver"] == "demo":
                from ..domain.sources import DemoCurves
                frame = await asyncio.to_thread(DemoCurves(app.demo_dir).query, args.get("filters", {}))
            else:
                frame = await asyncio.to_thread(invoke, cfg, args)
            meta = self.save(sid, args.get("name", "process_curves"), frame, args.get("description", "过程曲线逐点长表，包含曲线标识、阶段、坐标、单位及判定分组"), node=node)
            return {"variable_name": meta["name"], "row_count": meta["row_count"], "description": meta["description"], "version": meta["version"]}
        if tool == "python.execute":
            code = args["code"]
            code_meta = self.save(sid, uid("python_"), code, "本次数据计算的完整 Python 代码", "code", node)
            app.db.event(sid, "分析代码已生成，正在对引用变量的全量数据执行计算。", "code", "running", {"code": code, "variable": code_meta["name"]}, run_id=run, node_id=node)
            inputs = app.execution_inputs(sid, run)
            result = await app.runner.execute(sid, code, inputs, node, scripts=self.script_paths(skill, version))
            app.db.event(sid, "全量数据计算已完成，分析产物已写入变量仓库。", "tool_result", "succeeded", result, run_id=run, node_id=node)
            return result
        if tool == SHELL_TOOL:
            shell = args.get("shell") or next(iter(self.shells))
            if shell not in self.shells:
                raise ValueError(f"宿主未检测到 {shell}，可用：{', '.join(self.shells)}")
            script = args["script"]
            script_meta = self.save(sid, uid("script_"), script, f"本次执行的完整 {shell} 脚本", "code", node, metadata={"shell": shell})
            app.db.event(sid, f"{shell} 脚本已生成，正在会话工作目录中执行。", "code", "running", {"code": script, "variable": script_meta["name"]}, run_id=run, node_id=node)
            result = await app.runner.execute_shell(sid, shell, self.shells[shell], script)
            output = self.save(sid, uid("shell_output_"), result["stdout"], f"{shell} 脚本的标准输出", "text", node, [{"name": script_meta["name"], "version": script_meta["version"]}])
            result = {**result, "outputs": [app.variables.public(output)]}
            app.db.event(sid, "脚本执行完成，输出已写入变量仓库。", "tool_result", "succeeded", result, run_id=run, node_id=node)
            return result
        if tool == "chart.create":
            name = args.get("variable") or args.get("name")
            meta = app.variables.describe(sid, name)
            cfg = app.settings.values.get("connectors", {}).get("charts", {"driver": "demo"})
            if cfg["driver"] == "demo":
                from ..domain.curves import make_chart
                frame = app.variables.load(sid, name, meta["version"])
                figure = await asyncio.to_thread(make_chart, frame, args.get("kind", "auto"), args.get("x"), args.get("y"), args.get("group"))
            else:
                request = {"action": "chart", "data_path": meta["path"], "variable": name, "columns": meta["columns"],
                           **{k: args.get(k) for k in ("kind", "x", "y", "group", "title") if args.get(k)}}
                figure = self.standard_chart(await asyncio.to_thread(call_adapter, cfg, request))
            chart = {"variable": name, **{k: args.get(k) for k in ("kind", "x", "y", "group", "category") if args.get(k)}}
            output = self.save(sid, uid("chart_"), figure, args.get("description") or f"{name} 的{args.get('kind', 'auto')}图表", "chart", node, [{"name": name, "version": meta["version"]}], {"chart": chart})
            return app.variables.public(output)
        if tool == "issue.update":
            fields = args["fields"]
            if fields.get("status") == "closed":
                raise ValueError("关闭问题需用户在问题台账中确认")
            return app.update_issue(args["issue_id"], fields)
        if tool == "report.create":
            return self.report(sid)

    async def mos(self, sid, args, run=None, node=None):
        app = self.app
        cfg = app.settings.values.get("connectors", {}).get("mos", {"driver": "demo"})
        if cfg["driver"] != "demo":
            # The company MOS agent keeps its sub-agent role: question in, executed SQL and real rows out.
            app.db.event(sid, "为获取本步所需的产线记录，正在由 MOS 子 agent 生成并执行只读查询。", "tool", "running",
                         {"tool": "mos.query", "args": {k: v for k, v in args.items() if k in ("question", "name", "sql")}}, run_id=run, node_id=node)
            raw = await asyncio.to_thread(call_adapter, cfg, args)
            executed = raw.get("sql") if isinstance(raw, dict) else None
            if executed:
                self.save(sid, uid("sql_"), executed, "MOS 子 agent 实际执行的 SQL 查询", "sql", node)
                app.db.event(sid, "MOS 子 agent 已返回实际执行的查询语句。", "sql", "succeeded", {"sql": executed}, run_id=run, node_id=node)
            frame = to_frame(raw)
            if isinstance(raw, dict):
                args = {**args, **{k: raw[k] for k in ("name", "description") if raw.get(k) and not args.get(k)}}
        else:
            from ..domain.sources import DemoMOS
            source = DemoMOS(app.demo_dir)
            sql = args.get("sql")
            if not sql:
                schema = source.schema()
                prompt = [{"role": "system", "content": "你是MOS取数子agent。根据真实schema写一个SQLite只读SELECT/WITH语句，显式JOIN别名避免重复列。不得编造数据。返回JSON {sql,name,description}。\n" + schema},
                          {"role": "user", "content": args["question"]}]
                sql_result = await app.model.complete(prompt)
                sql = sql_result["sql"]
                args = {**args, **{k: sql_result[k] for k in ("name", "description") if k in sql_result}}
            for attempt in range(3):
                self.save(sid, uid("sql_"), sql, "MOS 实际执行的 SQL 查询", "sql", node)
                app.db.event(sid, "为获取本步所需的产线记录，正在执行只读查询并保存结果。", "sql", "running", {"sql": sql, "attempt": attempt + 1}, run_id=run, node_id=node)
                try:
                    frame = await asyncio.to_thread(source.query, sql)
                    break
                except Exception as exc:
                    app.db.event(sid, "查询未完成，已记录数据库错误用于核对字段和关联条件。", "error", "failed", {"error": str(exc), "sql": sql}, run_id=run, node_id=node)
                    if app.db.get("session", sid)["mode"] == "demo" or attempt == 2:
                        raise
                    fixed = await app.model.complete([{"role": "system", "content": "修正只读SQLite查询，保持原范围和目的，返回JSON {sql}。\n" + source.schema()},
                                                      {"role": "user", "content": json.dumps({"question": args.get("question"), "sql": sql, "error": str(exc)}, ensure_ascii=False)}])
                    sql = fixed["sql"]
        meta = self.save(sid, args.get("name", "mos_query_" + uid()), frame, args.get("description", "MOS 查询明细，包含请求范围内的产线记录及关联字段"), node=node)
        return {"variable_name": meta["name"], "row_count": meta["row_count"], "description": meta["description"], "version": meta["version"]}

    @staticmethod
    def standard_chart(result):
        """Normalize an existing chart program's output into a Plotly figure artifact."""
        if isinstance(result, dict) and isinstance(result.get("figure"), dict):
            result = result["figure"]
        if isinstance(result, dict) and "data" in result:
            return {"data": result["data"], "layout": result.get("layout", {})}
        if isinstance(result, dict) and result.get("image_path"):
            path = Path(result["image_path"])
            media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml"}.get(path.suffix.lower())
            if not media or not path.is_file():
                raise ValueError("图表程序返回的图片不存在或格式不受支持（png/jpg/svg）")
            source = f"data:{media};base64," + base64.b64encode(path.read_bytes()).decode()
            axis = {"visible": False, "range": [0, 1]}
            return {"data": [], "layout": {"title": {"text": result.get("title", "")}, "xaxis": axis, "yaxis": {**axis, "scaleanchor": "x"},
                                          "images": [{"source": source, "xref": "x", "yref": "y", "x": 0, "y": 1, "sizex": 1, "sizey": 1, "sizing": "contain", "layer": "below"}]}}
        raise ValueError("图表程序需返回 Plotly 图表字典（data/layout）、{figure: ...} 或 {image_path: ...}")

    def report(self, sid):
        app = self.app
        session = app.db.get("session", sid)
        messages = app.db.messages(sid)
        variables = sorted(app.variables.list(sid), key=lambda v: v["created_at"])
        known = {v["name"] for v in variables}
        anchor = lambda name: "var-" + re.sub(r"[^A-Za-z0-9_-]", "-", name)

        def linked(text):
            escaped = html.escape(text)
            return EVIDENCE_REF.sub(lambda m: f"<a href='#{anchor(m.group(1))}'><code>{m.group(1)}</code></a>" if m.group(1) in known else m.group(0), escaped)

        parts = ["<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>产线分析报告</title><style>body{font-family:Segoe UI,Microsoft YaHei,sans-serif;max-width:1200px;margin:40px auto;color:#172033;line-height:1.8}pre{white-space:pre-wrap}table{border-collapse:collapse;width:100%}td,th{padding:6px 8px;border:1px solid #dde3ed;text-align:left;vertical-align:top}a{color:#2f62c9}details{margin:4px 0}.muted{color:#6a7487}</style>",
                 f"<h1>{html.escape(session['title'])}</h1><p class='muted'>运行模式：{'模拟环境' if session['mode'] == 'demo' else '模型 API'} · 导出时间：{now()}</p>",
                 "<nav><a href='#records'>调查记录</a> · <a href='#charts'>图表</a> · <a href='#evidence'>证据与数据版本</a></nav>",
                 "<h2 id='records'>调查记录</h2>"]
        for message in messages:
            parts.append(f"<h3>{'工程师' if message['role']=='user' else '分析结果'}</h3><pre>{linked(message['content'])}</pre>")
        parts.append("<h2 id='charts'>图表</h2>")
        first = True
        for variable in variables:
            if variable["kind"] == "chart":
                figure = app.variables.load(sid, variable["name"], variable["version"])
                parts.append(f"<h3 id='chart-{anchor(variable['name'])}'>{html.escape(variable['description'])}</h3><p class='muted'>{html.escape(variable['name'])} v{variable['version']}</p>")
                # Plotly.js is embedded once so the report opens offline.
                parts.append(pio.to_html(figure, full_html=False, include_plotlyjs=True if first else False))
                first = False
        parts.append("<h2 id='evidence'>证据与数据版本</h2><table><tr><th>变量</th><th>版本</th><th>类型</th><th>描述与来源</th></tr>")
        for variable in variables:
            parents = "、".join(f"<a href='#{anchor(p['name'])}'>{html.escape(p['name'])}</a> v{p['version']}" if p.get("name") in known else html.escape(f"{p.get('name')} v{p.get('version')}")
                                for p in variable.get("parents") or [] if isinstance(p, dict))
            rows = f" · {variable['row_count']:,} 行" if variable.get("row_count") is not None else ""
            body = html.escape(variable["description"]) + rows + (f"<br><span class='muted'>来源：{parents}</span>" if parents else "")
            if variable["kind"] == "table":
                preview = app.variables.preview(sid, variable["name"], variable["version"])
                if preview.get("rows"):
                    frame = pd.DataFrame(preview["rows"]).head(10)
                    body += f"<details><summary>代表性预览（{len(frame)} / {preview['total_rows']:,} 行）</summary>{frame.to_html(index=False, border=0)}</details>"
            elif variable["kind"] in ("text", "sql", "code"):
                content = app.variables.load(sid, variable["name"], variable["version"])
                body += f"<details><summary>查看内容</summary><pre>{linked(content) if isinstance(content, str) else html.escape(json.dumps(content, ensure_ascii=False))}</pre></details>"
            parts.append(f"<tr id='{anchor(variable['name'])}'><td>{html.escape(variable['name'])}</td><td>{variable['version']}</td><td>{variable['kind']}</td><td>{body}</td></tr>")
        parts.append("</table></html>")
        return app.variables.public(self.save(sid, uid("report_"), "".join(parts), "包含调查记录、图表与证据链接的离线 HTML 报告", "report"))

from __future__ import annotations
import asyncio
import html
import json
from pathlib import Path
import pandas as pd
import plotly.io as pio
from ..storage.database import uid, now
from ..connectors.python_adapter import invoke


TOOLS = {
    "vars.list": "列出当前会话变量名称、描述、类型、版本和行数；不返回全量表格。",
    "vars.preview": "参数 name，返回变量结构和30至40行代表性预览。",
    "vars.read": "参数 name，读取系统变量或JSON/text；表格默认返回preview。",
    "skill.reference": "参数 path，读取当前skill的具体references或scripts文件。",
    "mos.query": "参数 question、可选sql/name；MOS子agent写SQL，实际查询后保存全量表格并返回变量描述。",
    "splunk.query": "参数 kind(bindings/changes/logs)、filters、可选name；保存查询数据。",
    "curves.query": "参数 filters(station_id/kind/phase/result/tgs/start/end)、可选name；保存过程曲线长表。",
    "python.execute": "参数 code；执行Python代码。ctx.vars.load(name)读全量，ctx.vars.save(name,value,description,kind)写变量。",
    "chart.create": "参数 variable,kind(auto/line/bar/box/scatter/histogram),x,y,group；基于全量变量制作图表。",
    "issue.update": "参数 issue_id,fields；更新问题台账证据与措施，关闭需用户在台账确认。",
    "report.create": "生成当前会话的HTML分析报告与图表附件。",
}


class Host:
    def __init__(self, app):
        self.app = app

    def capabilities(self):
        return [{"name": k, "description": v} for k, v in TOOLS.items()]

    def context(self, sid):
        app = self.app
        return {"skills": app.registry.discover(), "host_capabilities": self.capabilities(),
                "active_plan": app.active_plan(sid),
                "variable_index": [app.variables.public(v) for v in app.variables.list(sid)]}

    def save(self, sid, name, value, description, kind=None, node=None, parents=None, metadata=None):
        return self.app.variables.save(sid, name, value, description, kind, {"mode": self.app.db.get("session", sid)["mode"], **(metadata or {})}, node, parents)

    async def call(self, sid, tool, args, skill=None, version=None, run=None, node=None):
        app = self.app
        if tool not in TOOLS:
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
            result = await app.runner.execute(sid, code, inputs, node)
            app.db.event(sid, "全量数据计算已完成，分析产物已写入变量仓库。", "tool_result", "succeeded", result, run_id=run, node_id=node)
            return result
        if tool == "chart.create":
            from ..domain.curves import make_chart
            name = args.get("variable") or args.get("name")
            meta = app.variables.describe(sid, name)
            frame = app.variables.load(sid, name, meta["version"])
            figure = await asyncio.to_thread(make_chart, frame, args.get("kind", "auto"), args.get("x"), args.get("y"), args.get("group"))
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
            frame = await asyncio.to_thread(invoke, cfg, args)
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

    def report(self, sid):
        app = self.app
        session = app.db.get("session", sid)
        messages = app.db.messages(sid)
        variables = app.variables.list(sid)
        parts = ["<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>产线分析报告</title><style>body{font-family:Segoe UI,Microsoft YaHei,sans-serif;max-width:1200px;margin:40px auto;color:#172033;line-height:1.8}pre{white-space:pre-wrap}table{border-collapse:collapse;width:100%}td,th{padding:8px;border:1px solid #dde3ed}</style>",
                 f"<h1>{html.escape(session['title'])}</h1><p>模型模式：{session['mode']} · 导出时间：{now()}</p>"]
        for message in messages:
            parts.append(f"<h3>{'工程师' if message['role']=='user' else '分析结果'}</h3><pre>{html.escape(message['content'])}</pre>")
        first = True
        for variable in variables:
            if variable["kind"] == "chart":
                figure = app.variables.load(sid, variable["name"], variable["version"])
                parts.append(pio.to_html(figure, full_html=False, include_plotlyjs=True if first else False))
                first = False
        parts.append("<h2>证据与数据版本</h2><table><tr><th>变量</th><th>版本</th><th>描述</th></tr>")
        for variable in variables:
            parts.append(f"<tr><td>{html.escape(variable['name'])}</td><td>{variable['version']}</td><td>{html.escape(variable['description'])}</td></tr>")
        parts.append("</table></html>")
        return app.variables.public(self.save(sid, uid("report_"), "".join(parts), "包含调查记录、图表与变量版本的离线 HTML 报告", "report"))

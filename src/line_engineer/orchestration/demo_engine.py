"""Deterministic demo planner for the simulated plant.

Without a model API, routing and plan composition are rule-based, but every
query, log lookup and calculation is still executed through the host tools on
the simulated sources. It validates the workflow, not natural-language ability.
"""
from __future__ import annotations
import json
import re
from datetime import datetime, timedelta

SCENARIO = {
    "default_station": "OP20",
    "exit_station": "OP30",
    "default_days": 14,
    "trend_days": 60,
    "stations": {
        "OP20": {"name": "轴承压装", "task": "PressFit", "parameter": "PeakForce", "label": "压装力", "component": "轴承", "curve_kind": "press"},
        "OP30": {"name": "螺栓拧紧", "task": "Tightening", "parameter": "FinalTorque", "label": "拧紧扭矩", "component": "螺栓", "curve_kind": "torque"},
    },
    "aliases": [("压装", "OP20"), ("轴承", "OP20"), ("拧紧", "OP30"), ("扭矩", "OP30"), ("螺栓", "OP30")],
    "process_references": ["references/task-parameter-map.md", "references/component-loading.md", "references/process-flow.md"],
}
NUMBERS = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
UNITS = {"天": 1, "日": 1, "周": 7, "星期": 7, "月": 30}
SHIFTS = {"night": "夜班", "day": "白班"}
CHART_WORDS = {"箱线图": "box", "柱状图": "bar", "条形图": "bar", "折线图": "line", "散点图": "scatter", "直方图": "histogram", "小提琴图": "violin"}
DEMO_NOTE = "演示模式使用预置查询模板验证流程；连接模型 API 后由 MOS 子 agent 依据问题和 schema 生成 SQL。"

HEADER = "import json\nPARAMS = json.loads({params!r})\n"

PATTERNS = '''
import pandas as pd
from line_engineer.domain.analysis import analyze_nc

names = PARAMS["inputs"]
nc = ctx.vars.load(names["nc"])
visits = ctx.vars.load(names["visits"])
measurements = ctx.vars.load(names["measurements"])
bindings = ctx.vars.load(names["bindings"])
result = analyze_nc(nc, visits, measurements, bindings, PARAMS["config"])
tables = result["tables"]

descriptions = {
    "startup_distribution": "不良工件按白班／夜班及是否开班首小时上线的分布（去重 TG）",
    "shift_output": "出口工站按班次去重 TG 的班产及停产／验线／低产分类",
    "nc_shift_context": "每个不良工件所在班次的班产、上一班产量及班次分类",
    "long_dwell": "不良工件相邻工站间隔超过阈值的过站记录",
    "daily_trend": "按日统计的检测工件数、不良工件数及不良率",
    "batch_concentration": "不良工件按零件批次及供应商的集中度；仅为 NC 构成，不代表风险高低",
    "data_quality": "各输入数据的行数、重复行与缺失单元格统计",
}
for name, description in descriptions.items():
    if name in tables:
        ctx.vars.save(name, tables[name], description)
if "nc_with_bindings" in tables:
    ctx.vars.save(names["nc"], tables["nc_with_bindings"], "NC 明细，按 TG 补充各零件／安装位置首次绑定的 PN、批次、供应商及绑定时间；原 NC 行数不变，未匹配记录保留为空。")
charts = {
    "shift_output": ("出口工站班产（去重 TG）", {"variable": "shift_output", "x": "shift_start", "y": "output"}),
    "daily_trend": ("每日不良率趋势（含同期检测量）", {"variable": "daily_trend", "x": "date", "y": "nc_rate", "group": "parameter", "category": "parameter"}),
}
for name, (title, meta) in charts.items():
    if name in result["figures"]:
        ctx.vars.save(name + "_chart", result["figures"][name], title, "chart", {"chart": meta})

summary = {"summary": result["summary"], "findings": result["findings"], "warnings": result["warnings"],
           "nc_pieces": int(nc["tg"].nunique()), "nc_events": int(len(nc))}
startup = tables.get("startup_distribution")
if startup is not None:
    summary["startup_pieces"] = int(startup.loc[startup["startup"].astype(bool), "nc_pieces"].sum())
if "long_dwell" in tables:
    summary["long_dwell_pieces"] = int(tables["long_dwell"]["tg"].nunique())
context = tables.get("nc_shift_context")
if context is not None:
    summary["shift_classes"] = {str(k): int(v) for k, v in context.groupby("classification", dropna=False)["tg"].nunique().items()}
trend = tables.get("daily_trend")
if trend is not None and len(trend):
    daily = trend.groupby("date")[["tested_pieces", "nc_pieces"]].sum().sort_index()
    weekly = daily.resample("7D").sum()
    weekly = weekly[weekly["tested_pieces"] > 0]
    weekly["rate"] = weekly["nc_pieces"] / weekly["tested_pieces"]
    k = max(1, len(weekly) // 3)
    early = float(weekly["nc_pieces"].iloc[:k].sum() / weekly["tested_pieces"].iloc[:k].sum())
    recent = float(weekly["nc_pieces"].iloc[-k:].sum() / weekly["tested_pieces"].iloc[-k:].sum())
    jumps = weekly["rate"].diff().dropna()
    jump = {"week_start": str(jumps.idxmax().date()), "change": float(jumps.max())} if len(jumps) else None
    rise = recent - early
    if jump and rise > 0 and jump["change"] >= max(0.01, 0.5 * rise):
        shape = "突然升高"
    elif rise > 0 and recent > early * 1.3:
        shape = "持续上升"
    else:
        shape = "无明显上升"
    summary["trend"] = {"early_rate": early, "recent_rate": recent, "largest_jump": jump, "shape": shape,
                        "weeks": [{"week_start": str(i.date()), "tested": int(r.tested_pieces), "nc": int(r.nc_pieces), "rate": float(r.rate)} for i, r in weekly.iterrows()]}
concentration = tables.get("batch_concentration")
if concentration is not None and len(concentration):
    top = concentration.sort_values("nc_share", ascending=False).groupby(["component", "dimension"], sort=False).head(1)
    summary["top_concentration"] = json.loads(top.to_json(orient="records", force_ascii=False))
    strong = concentration[concentration["strong_concentration"] & concentration["group"].notna()]
    summary["strong_concentration"] = json.loads(strong.to_json(orient="records", force_ascii=False))
ctx.vars.save("pattern_findings", summary, "规律挖掘阶段的计数、开班、班产、滞留、趋势与集中度统计发现", "json")
'''

BATCH = '''
import pandas as pd
import plotly.graph_objects as go
from line_engineer.domain.analysis import analyze_nc, first_bindings

names = PARAMS["inputs"]
measured = ctx.vars.load(names["measurements"])
bindings = ctx.vars.load(names["bindings"])
nc = measured[measured["result"].eq("NC")]
result = analyze_nc(nc, pd.DataFrame(), measured, bindings, PARAMS["config"])
tables = result["tables"]
risk = tables.get("batch_risk", pd.DataFrame())
if len(risk):
    ctx.vars.save("batch_risk", risk, "同期 OK 与 NC 全量检测中各批次／供应商的投入工件数、不良工件数、不良率及相对其他组的风险比")
if "batch_risk" in result["figures"]:
    ctx.vars.save("batch_risk_chart", result["figures"]["batch_risk"], "批次不良率（按同期投入量计算）", "chart",
                  {"chart": {"variable": "batch_risk", "x": "group", "y": "nc_rate", "group": "parameter", "category": "group"}})

first = first_bindings(bindings)
first = first[first["component"].eq(PARAMS["component"])]
joined = measured.merge(first[["tg", "batch", "supplier"]], on="tg", how="left", validate="many_to_one")
joined["batch"] = joined["batch"].fillna("未绑定")
distribution = joined.groupby(["batch", "result"])["value"].describe(percentiles=[.05, .25, .5, .75, .95]).reset_index()
ctx.vars.save("batch_value_distribution", distribution, "各批次 OK／NC 检测值的数量、均值及分位数（全量数据计算）")
figure = go.Figure()
for outcome, color in (("OK", "#3674e8"), ("NC", "#d78844")):
    part = distribution[distribution["result"].eq(outcome)]
    figure.add_trace(go.Bar(x=part["batch"], y=part["50%"], name=f"{outcome} 中位数", marker_color=color,
                            error_y={"type": "data", "symmetric": False, "array": (part["95%"] - part["50%"]).tolist(), "arrayminus": (part["50%"] - part["5%"]).tolist()}))
figure.update_layout(title="各批次检测值中位数与 5%—95% 范围", barmode="group", template="plotly_white", yaxis_title=PARAMS["unit"])
ctx.vars.save("batch_value_chart", json.loads(figure.to_json()), "各批次 OK／NC 检测值中位数与 5%—95% 范围", "chart",
              {"chart": {"variable": "batch_value_distribution", "x": "batch", "y": "50%", "group": "result", "category": "batch"}})

rows = []
for _, row in risk[risk["dimension"].isin(["batch", "supplier"])].iterrows() if len(risk) else []:
    rows.append({"dimension": row["dimension"], "group": None if pd.isna(row["group"]) else str(row["group"]), "tested": int(row["tested_pieces"]),
                 "nc": int(row["nc_pieces"]), "rate": float(row["nc_rate"]), "other_rate": None if pd.isna(row["other_nc_rate"]) else float(row["other_nc_rate"]),
                 "risk_ratio": None if pd.isna(row["risk_ratio"]) else float(row["risk_ratio"])})
medians = distribution.pivot_table(index="batch", columns="result", values="50%").reset_index()
ctx.vars.save("batch_findings", {"risk": rows, "medians": json.loads(medians.to_json(orient="records", force_ascii=False)),
                                 "tested_pieces": int(measured["tg"].nunique()), "nc_pieces": int(nc["tg"].nunique()),
                                 "unbound_pieces": int(joined.loc[joined["batch"].eq("未绑定"), "tg"].nunique()), "warnings": result["warnings"]},
              "同期批次／供应商风险比较与检测值分布的统计发现", "json")
'''

CURVES = '''
import pandas as pd
from line_engineer.domain.curves import analyze_curves

curves = ctx.vars.load(PARAMS["inputs"]["curves"])
result = analyze_curves(curves, alignment="contact", profiles=PARAMS.get("profiles"))
tables = result["tables"]
descriptions = {
    "features": ("curve_features", "每条曲线的峰值、峰值位置、终点、最大斜率及波动等特征"),
    "abnormal_segments": ("curve_abnormal_segments", "NC 曲线偏离 OK 经验参考范围的连续区段；参考范围不是工艺规格"),
    "group_summary": ("curve_group_summary", "按曲线类型、阶段及判定分组的特征汇总"),
}
for key, (name, description) in descriptions.items():
    if key in tables and len(tables[key]):
        ctx.vars.save(name, tables[key], description)
for key, figure in result["figures"].items():
    if key.endswith("_aligned") or key.endswith("_raw"):
        ctx.vars.save("curve_" + key.rsplit("_", 1)[1] + "_chart", figure, f"过程曲线{'接触点对齐' if key.endswith('_aligned') else '原始'}叠加", "chart")
summary = {"summary": result["summary"], "findings": result["findings"], "warnings": result["warnings"],
           "curves": int(curves["curve_id"].nunique()) if len(curves) else 0,
           "x_unit": str(curves["x_unit"].iloc[0]) if len(curves) else "",
           "by_result": {str(k): int(v) for k, v in curves.groupby("result")["curve_id"].nunique().items()} if len(curves) else {},
           "coverage": [str(curves["occurred_at"].min()), str(curves["occurred_at"].max())] if len(curves) else None}
group = tables.get("group_summary")
if group is not None and len(group):
    summary["groups"] = json.loads(group.to_json(orient="records", force_ascii=False))
segments = tables.get("abnormal_segments")
if segments is not None and len(segments):
    summary["segments"] = {"count": int(len(segments)), "curves": int(segments["curve_id"].nunique()),
                           "x_start_median": float(segments["x_start"].median()), "x_end_median": float(segments["x_end"].median())}
ctx.vars.save("curve_findings", summary, "过程曲线对齐、特征比较与异常区段统计发现", "json")
'''


def md_table(text):
    rows = []
    for line in text.splitlines():
        if line.startswith("|") and not re.match(r"^\|\s*-", line):
            rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows[1:] if rows else []


def pct(value):
    return "—" if value is None else f"{value:.2%}"


def startup_label(rules):
    minutes = int(rules.get("startup_window_minutes", 60))
    return "开班首小时" if minutes == 60 else f"开班首 {minutes} 分钟"


class DemoEngine:
    def __init__(self, app):
        self.app = app

    # ---- scope ---------------------------------------------------------
    def analysis_config(self):
        rules = {k: v for k, v in self.app.rules().items() if k != "curve_profiles"}
        return {**rules, "exit_station": rules.get("exit_station") or SCENARIO["exit_station"]}

    def shift_hours(self):
        rules = self.app.rules()
        start, length = int(rules.get("shift_start_hour", 7)), int(rules.get("shift_hours", 12))
        return start, length, sorted({(start + length * i) % 24 for i in range(max(1, 24 // length))})

    def anchor(self):
        manifest = self.app.manifest or self.app.prepare_demo()
        last = datetime.fromisoformat(manifest["end"])
        boundary = last.replace(minute=0, second=0, microsecond=0)
        starts = self.shift_hours()[2]
        while boundary <= last or boundary.hour not in starts:
            boundary += timedelta(hours=1)
        return boundary

    def parse_scope(self, text, base=None):
        scope = {k: v for k, v in (base or {}).items() if k in ("station", "days", "shift")}
        station = re.search(r"(?:OP|ST)\s*0*(\d{1,3})", text, re.I)
        if station:
            scope["station"] = f"OP{int(station.group(1))}"
        elif not base:
            scope["station"] = next((s for word, s in SCENARIO["aliases"] if word in text), SCENARIO["default_station"])
            scope["station_assumed"] = not any(word in text for word, _ in SCENARIO["aliases"])
        period = re.search(r"(\d+|[一两二三四五六七八九十])\s*(?:个)?\s*(天|日|周|星期|月)", text)
        if period:
            count = int(period.group(1)) if period.group(1).isdigit() else NUMBERS[period.group(1)]
            scope["days"] = count * UNITS[period.group(2)]
        if "夜班" in text:
            scope["shift"] = "night"
        elif "白班" in text or "早班" in text:
            scope["shift"] = "day"
        elif re.search(r"全部班次|所有班次|不限班次", text):
            scope["shift"] = None
        scope.setdefault("days", SCENARIO["default_days"])
        scope.setdefault("shift", None)
        scope["days"] = max(1, min(int(scope["days"]), self.app.demo_days - 1))
        end = self.anchor()
        trend_days = max(SCENARIO["trend_days"], scope["days"])
        scope.update(end=end.isoformat(timespec="seconds"), start=(end - timedelta(days=scope["days"])).isoformat(timespec="seconds"),
                     trend_start=(end - timedelta(days=min(trend_days, self.app.demo_days - 1))).isoformat(timespec="seconds"))
        return scope

    def describe(self, scope):
        station = SCENARIO["stations"][scope["station"]]
        label = lambda value: value[5:16].replace("T", " ")
        shift = f"，仅{SHIFTS[scope['shift']]}" if scope.get("shift") else ""
        return f"{scope['station']} {station['name']} · {station['parameter']}，{label(scope['start'])} 至 {label(scope['end'])}（{scope['days']} 天{shift}）"

    def _shift_sql(self, column, shift):
        hour = f"substr({column}, 12, 2)"
        start, length, _ = self.shift_hours()
        day_start, day_end = f"{start:02d}", f"{(start + length) % 24:02d}"
        if shift == "night":
            return f"\n  AND ({hour} >= '{day_end}' OR {hour} < '{day_start}')"
        if shift == "day":
            return f"\n  AND {hour} >= '{day_start}' AND {hour} < '{day_end}'"
        return ""

    def nc_sql(self, scope):
        station = SCENARIO["stations"][scope["station"]]
        return ("SELECT n.nc_id, n.code AS nc_code, n.occurred_at, v.tg, v.visit_id, v.station_id, m.measurement_id,\n"
                "       m.task, m.parameter, m.value, m.unit, m.lower_limit, m.upper_limit, m.result, w.launched_at\n"
                "FROM nc_events n\nJOIN measurements m ON m.measurement_id = n.measurement_id\n"
                "JOIN visits v ON v.visit_id = m.visit_id\nJOIN workpieces w ON w.tg = v.tg\n"
                f"WHERE v.station_id = '{scope['station']}' AND m.parameter = '{station['parameter']}'\n"
                f"  AND n.occurred_at >= '{scope['start']}' AND n.occurred_at < '{scope['end']}'"
                f"{self._shift_sql('n.occurred_at', scope.get('shift'))}\nORDER BY n.occurred_at, n.nc_id")

    def measurement_sql(self, scope, start):
        station = SCENARIO["stations"][scope["station"]]
        return ("SELECT m.measurement_id, v.tg, v.station_id, m.task, m.parameter, m.value, m.unit, m.result, v.entered_at AS occurred_at\n"
                "FROM measurements m\nJOIN visits v ON v.visit_id = m.visit_id\n"
                f"WHERE v.station_id = '{scope['station']}' AND m.parameter = '{station['parameter']}'\n"
                f"  AND v.entered_at >= '{start}' AND v.entered_at < '{scope['end']}'"
                f"{self._shift_sql('v.entered_at', scope.get('shift'))}\nORDER BY v.entered_at, m.measurement_id")

    def visits_sql(self, scope):
        start = (datetime.fromisoformat(scope["start"]) - timedelta(hours=12)).isoformat(timespec="seconds")
        return ("SELECT visit_id, tg, station_id, entered_at, exited_at\nFROM visits\n"
                f"WHERE entered_at >= '{start}' AND entered_at < '{scope['end']}'\nORDER BY tg, entered_at, visit_id")

    # ---- routing -------------------------------------------------------
    def route(self, sid, content):
        memory = self.app.db.get("session", sid).get("memory", {})
        text = content
        chart = next((kind for word, kind in CHART_WORDS.items() if word in text), None)
        if chart and re.search(r"改成|换成|改为|改用|画成|做成", text):
            return self._chart_change(sid, chart, text)
        if re.search(r"台账|问题跟踪|跟踪记录|登记问题|建立问题", text):
            return self._issue(sid, text, memory)
        pending = self.app.active_plan(sid)
        question = re.search(r"为什么|为何|解释|说明|[?？]", text)
        direct = re.search(r"查询|取数|明细|曲线|报告", text) and not question and not re.search(r"只看|只分析|改为|改成|换成|调整|范围", text)
        if pending and pending["status"] == "awaiting_approval" and pending.get("origin") == "demo" and not direct:
            base = pending.get("scope") or {}
            changed = self.parse_scope(text, base)
            if any(changed.get(k) != base.get(k) for k in ("station", "days", "shift")):
                return {"type": "revise", "plan": pending, "feedback": text}
            if question:
                steps = "\n".join(f"{i}. **{n['title']}**：{n['description']}；预期得到{n['expected_output']}。" for i, n in enumerate(pending["nodes"], 1))
                return {"type": "answer", "message": f"待确认计划“{pending['title']}”的安排如下：\n\n{steps}\n\n{pending['summary']}\n\n确认后按此范围执行；如需调整工站、时间范围或班次，可直接说明。"}
        if re.search(r"报告", text):
            return {"type": "direct", "origin": "demo", "nodes": [self._node("r1", "skill", "reporting", "生成离线分析报告", "汇总当前会话的分析记录、图表与变量版本",
                    "可离线查看的 HTML 报告", "为便于离线复核与交接，正在汇总本会话的调查记录、图表和证据版本，生成 HTML 分析报告。", {"action": "report"})]}
        investigate = re.search(r"原因|分析|排查|调查|为什么|根因|增多|升高|变多|恶化|异常|失效", text)
        scope_change = memory.get("scope") and re.search(r"只看|只分析|改为|换成|仅|夜班|白班|范围", text)
        scope = self.parse_scope(text, memory.get("scope") if scope_change or not re.search(r"(?:OP|ST)\s*\d", text, re.I) and memory.get("scope") else None)
        if scope["station"] not in SCENARIO["stations"]:
            return {"type": "answer", "message": f"模拟产线中 {scope['station']} 没有已建模的检测参数。当前可分析 OP20 轴承压装（PeakForce）和 OP30 螺栓拧紧（FinalTorque）。"}
        if "曲线" in text:
            return {"type": "direct", "origin": "demo", "memory": {"scope": scope}, "nodes": [self._curve_node("c1", scope, [])]}
        if re.search(r"查询|取数|明细|列出|拉取", text) and not investigate:
            node = self._mos_node("q1", "查询 NC 明细", f"{self.describe(scope)} 的 NC 明细", self.nc_sql(scope), "nc_records",
                                  f"{self.describe(scope)} 的 NC 明细，含 TG、检测任务、参数、检测值、限值、判定、NC 发生时间及上线时间",
                                  "为获得指定范围内可追溯的不良记录，正在按工站、参数和时间范围查询 NC 明细，并统计不良工件数与 NC 事件数。", count_tg=True, note=True)
            return {"type": "direct", "origin": "demo", "memory": {"scope": scope}, "nodes": [node]}
        if investigate or scope_change:
            message = "演示数据默认使用 OP20 轴承压装进行调查；如需其他工站，可在计划中调整。" if scope.get("station_assumed") else None
            return {"type": "plan", "origin": "demo", "plan": self.investigation_plan(scope), "scope": scope, "message": message,
                    "memory": {"goal": content, "scope": scope}}
        return {"type": "answer", "message": "当前为模拟环境，可执行以下类型的请求：\n\n- 现场问题调查，例如“分析 OP20 最近两周压装力 NC 增多的原因”，将先生成需确认的分阶段计划；\n"
                "- NC 明细取数，例如“查询 OP30 最近一周的 NC 明细”；\n- 过程曲线对比，例如“对比 OP20 合格件与不良件的压装曲线”；\n"
                "- 图表调整、问题台账登记与离线报告生成。\n\n" + DEMO_NOTE}

    def follow_up(self, sid, content, run):
        if re.search(r"停止|取消|终止|不要继续", content):
            return {"type": "stop"}
        if run.get("kind") == "plan":
            plan = self.app.plan(run["plan_id"], run["revision"])
            base = plan.get("scope") or {}
            changed = self.parse_scope(content, base)
            if any(changed.get(k) != base.get(k) for k in ("station", "days", "shift")):
                return {"type": "revise", "feedback": content}
        return {"type": "answer"}

    def revise(self, plan, feedback):
        scope = self.parse_scope(feedback, plan.get("scope"))
        if scope["station"] not in SCENARIO["stations"]:
            raise ValueError(f"模拟产线中 {scope['station']} 没有已建模的检测参数")
        if plan["stage"] == "verification":
            draft = self.verification_plan(scope, plan.get("scope", {}).get("directions", ["batch", "equipment"]))
            scope["directions"] = plan.get("scope", {}).get("directions", ["batch", "equipment"])
        else:
            draft = self.investigation_plan(scope)
        draft["summary"] += f" 已按调整意见更新：{feedback}"
        return draft, scope

    # ---- plan builders -------------------------------------------------
    @staticmethod
    def _node(id, kind, target, title, description, expected, progress, params, depends=()):
        return {"id": id, "kind": kind, "skill": target if kind == "skill" else None, "tool": target if kind == "tool" else None, "title": title,
                "description": description, "expected_output": expected, "depends_on": list(depends), "params": params, "progress_message": progress}

    def _mos_node(self, id, title, description, sql, name, variable_description, progress, depends=(), count_tg=False, note=False, label=None):
        return self._node(id, "tool", "mos.query", title, description, f"{name} 全量明细及行数", progress,
                          {"action": "mos", "sql": sql, "name": name, "description": variable_description, "count_tg": count_tg, "note": note, "label": label or title}, depends)

    def _curve_node(self, id, scope, depends):
        station = SCENARIO["stations"][scope["station"]]
        return self._node(id, "skill", "curve-analysis", "对比 OK 与 NC 过程曲线", f"{scope['station']} {station['name']}的{station['label']}过程曲线，按接触点对齐后比较特征与异常区段",
                          "曲线特征表、异常区段及对齐曲线图",
                          f"为确认不良件在{station['label']}过程中的差异区段，正在调取 {scope['station']} 的合格与不合格过程曲线，按接触点对齐后比较峰值、斜率与偏离区段。",
                          {"action": "curves", "station": scope["station"], "kind": station["curve_kind"], "profiles": self.app.rules().get("curve_profiles", {})}, depends)

    def investigation_plan(self, scope):
        s = SCENARIO["stations"][scope["station"]]
        where = self.describe(scope)
        nodes = [
            self._mos_node("p1", "确认 NC 范围", f"查询 {where} 的 NC 明细，区分不良工件数与 NC 事件数", self.nc_sql(scope), "nc_records",
                           f"{where} 的 NC 明细，含 TG、检测任务、参数、检测值、限值、判定、NC 发生时间及上线时间",
                           f"为确认 {scope['station']} {s['label']}失效的影响范围，正在查询调查时段内的 NC 明细，并分别统计不良工件数与 NC 事件数。", count_tg=True),
            self._mos_node("p2", "调取过站记录", "调取同期全部工站的过站记录，用于统计出口工站班产和跨工站间隔", self.visits_sql(scope), "visit_records",
                           "调查时段（含前一班）全部工站的过站记录，含 TG、工站、进站与出站时间",
                           "为判断失效是否与开班、班产或停线滞留有关，正在调取同期全部过站记录，用于统计出口工站班产及跨工站间隔。"),
            self._mos_node("p3", "调取趋势检测数据", f"查询近 {SCENARIO['trend_days']} 天 {scope['station']} {s['parameter']} 的全部 OK／NC 检测记录", self.measurement_sql(scope, scope["trend_start"]),
                           "measurement_trend", f"{scope['station']} {s['parameter']} 自 {scope['trend_start'][:10]} 起的全部检测记录（含 OK 与 NC），用于计算有分母的不良率趋势",
                           f"为区分不良率是突然升高还是持续恶化，正在查询近 {SCENARIO['trend_days']} 天 {scope['station']} 的全部检测记录，按日对比检测量与不良率。"),
            self._node("p4", "skill", "equipment-structure", "梳理设备结构假设", f"读取 {scope['station']} 设备结构、功能与失效模式资料", "候选结构及验证方向",
                       f"为建立可验证的设备假设，正在读取 {scope['station']} 设备结构资料，将 {s['parameter']} 失效与相关结构的功能逐项对应。",
                       {"action": "reference", "paths": [f"references/{scope['station']}.md"], "name": f"equipment_knowledge_{scope['station']}", "station": scope["station"]}),
            self._node("p5", "skill", "process", "确定关联零件", f"检索 {s['parameter']} 对应的零件、PN、安装位置及上线工站", "关联零件与绑定口径",
                       f"为确定需要追溯来源的零件，正在检索 {s['parameter']} 对应的零件、PN、安装位置及首次绑定工站。",
                       {"action": "reference", "paths": SCENARIO["process_references"], "name": "process_knowledge", "station": scope["station"]}),
            self._node("p6", "skill", "splunk", "查询首次绑定", f"按不良工件 TG 查询{s['component']}的首次有效绑定记录", "首次绑定明细（一件一零件位置）",
                       f"为核对不良件所用{s['component']}的批次与供应商，正在按 TG 查询首次有效绑定记录，并保持一件一位置的关联粒度。",
                       {"action": "bindings", "source": "nc_records", "component": s["component"], "name": "first_bindings",
                        "description": f"不良工件{s['component']}的首次有效绑定记录，含 PN、安装位置、批次、供应商及绑定时间"}, ["p1", "p5"]),
            self._node("p7", "skill", "data-analysis", "统计规律线索", "对开班、班产、滞留、趋势和批次集中度执行全量统计，并将绑定信息写回 NC 明细",
                       "开班分布、班产分类、滞留清单、日趋势、批次集中度及更新后的 NC 明细",
                       "为找出开班、班产、滞留、趋势和批次方面的集中规律，正在对 NC、过站、检测及绑定全量数据执行统计，并将首次绑定信息写回 NC 明细。",
                       {"action": "analysis", "template": "patterns", "inputs": {"nc": "nc_records", "visits": "visit_records", "measurements": "measurement_trend", "bindings": "first_bindings"},
                        "config": self.analysis_config()}, ["p1", "p2", "p3", "p6"]),
            self._node("p8", "skill", "problem-analysis", "复核规律与假设", "汇总各方向检查结果，区分观察事实、关联线索与待验证假设", "规律挖掘结论与深入验证方向",
                       "为判断规律挖掘是否已回答原问题，正在汇总各方向的统计证据与设备资料，区分事实、关联线索和待验证假设。",
                       {"action": "review", "station": scope["station"], "config": self.analysis_config()}, ["p4", "p7"]),
        ]
        return {"title": f"{scope['station']} {s['label']} NC 规律挖掘", "stage": "investigation", "nodes": nodes,
                "summary": f"调查范围：{where}。先明确不良规模，再检查开班、班产、滞留、近 {SCENARIO['trend_days']} 天趋势、设备结构和零件批次集中度；深入验证将依据本阶段证据另行确认。"}

    def verification_plan(self, scope, directions):
        s = SCENARIO["stations"][scope["station"]]
        where = self.describe(scope)
        nodes, review_deps = [], []
        if "batch" in directions:
            nodes += [
                self._mos_node("v1", "调取同期全部检测", f"查询 {where} 的全部 OK 与 NC 检测记录，作为批次风险分母", self.measurement_sql(scope, scope["start"]), "scope_measurements",
                               f"{where} 的全部检测记录（含 OK 与 NC），作为批次与供应商风险比较的分母",
                               "为验证可疑批次是否具有更高的不良风险，正在调取同期合格与不合格工件的全部检测记录，作为批次投入量和不良率的分母。"),
                self._node("v2", "skill", "splunk", "查询同期首次绑定", f"按同期全部检测工件查询{s['component']}首次绑定", "同期全部工件的首次绑定",
                           f"为按批次计算投入量，正在查询同期全部检测工件的{s['component']}首次绑定记录，覆盖合格件与不良件。",
                           {"action": "bindings", "source": "scope_measurements", "component": s["component"], "name": "scope_bindings",
                            "description": f"同期全部检测工件（OK 与 NC）的{s['component']}首次有效绑定记录"}, ["v1"]),
                self._node("v3", "skill", "data-analysis", "比较批次风险", "比较各批次／供应商的投入量、不良率、风险比和检测值分布", "批次风险表、检测值分布及图表",
                           f"为确认批次集中是否对应更高风险，正在对比不同批次与供应商的投入量、不良率及{s['label']}分布。",
                           {"action": "analysis", "template": "batch", "inputs": {"measurements": "scope_measurements", "bindings": "scope_bindings"},
                            "component": s["component"], "unit": "kN" if s["curve_kind"] == "press" else "N·m", "config": self.analysis_config()}, ["v1", "v2"]),
            ]
            review_deps.append("v3")
        nodes.append(self._curve_node("v4", scope, []))
        nodes.append(self._node("v5", "skill", "splunk", "检索设备变化点", f"检索 {scope['station']} 近 {SCENARIO['trend_days']} 天的维护、点检、配方与备件变化记录", "变化点清单",
                                f"为排查设备状态变化与失效增多之间的关系，正在检索 {scope['station']} 近 {SCENARIO['trend_days']} 天的变化点，并与不良率趋势对照。",
                                {"action": "changes", "station": scope["station"], "start": scope["trend_start"], "name": "equipment_changes"}))
        review_deps += ["v4", "v5"]
        nodes.append(self._node("v6", "skill", "problem-analysis", "形成调查结论", "综合批次、曲线与变化点证据，区分统计关联与已验证原因，给出现场验证建议", "调查结论与验证建议",
                                "为回答原问题并明确证据强弱，正在综合批次风险、曲线差异和设备变化点，区分统计关联与已验证原因并整理现场验证建议。",
                                {"action": "conclusion", "station": scope["station"]}, review_deps))
        focus = "批次风险与设备状态" if "batch" in directions else "设备状态与过程曲线"
        return {"title": f"{scope['station']} {s['label']} 深入验证：{focus}", "stage": "verification", "nodes": nodes,
                "summary": f"依据规律挖掘证据，在 {where} 内验证{focus}；结论将区分统计关联与已验证原因，现场措施仅作为建议。"}

    # ---- execution -----------------------------------------------------
    async def execute(self, sid, run, plan, node):
        params = node.get("params") or {}
        handler = getattr(self, f"_do_{params.get('action')}", None)
        if handler is None:
            raise ValueError(f"演示引擎不支持步骤类型：{params.get('action')}")
        version = plan["skill_versions"].get(node.get("skill")) if plan and node.get("skill") else None
        ctx = {"sid": sid, "run": run["id"], "node": node["id"], "skill": node.get("skill"), "version": version}
        if node.get("skill"):
            metadata = next(s for s in self.app.registry.discover() if s["name"] == node["skill"])
            self.app.registry.read(node["skill"], "SKILL.md", version)
            self.app.db.event(sid, f"已加载“{metadata['title']}”技能正文，参考资料按需读取。", "skill", "completed",
                              {"skill": node["skill"], "version": metadata["version"], "hash": (version or metadata["hash"])[:12]}, run_id=run["id"], node_id=node["id"])
        return await handler(ctx, params, node)

    async def _call(self, ctx, tool, args):
        return await self.app.host.call(ctx["sid"], tool, args, ctx["skill"], ctx["version"], ctx["run"], ctx["node"])

    def _load(self, ctx, name):
        return self.app.variables.load(ctx["sid"], name)

    async def _do_mos(self, ctx, p, node):
        result = await self._call(ctx, "mos.query", {"sql": p["sql"], "name": p["name"], "description": p["description"], "question": node["description"]})
        rows = result["row_count"]
        detail = f"共 {rows:,} 行"
        if p.get("count_tg"):
            frame = self._load(ctx, result["variable_name"])
            detail = f"共 {rows:,} 条 NC 事件，涉及 {frame['tg'].nunique():,} 件不良工件" if rows else "查询范围内没有 NC 记录，已保存零行结果与查询范围"
        message = f"{p['label']}完成：{detail}，保存为 {result['variable_name']} 第 {result['version']} 版。"
        return {"message": message + (f"\n\n{DEMO_NOTE}" if p.get("note") else ""), "outputs": [result["variable_name"]]}

    async def _do_reference(self, ctx, p, node):
        parts = []
        for path in p["paths"]:
            parts.append((await self._call(ctx, "skill.reference", {"path": path}))["content"])
        text = "\n\n".join(parts)
        meta = self.app.host.save(ctx["sid"], p["name"], text, f"{node['title']}所读取的技能参考资料原文", "text", ctx["node"])
        if node["skill"] == "equipment-structure":
            structures = [row[0] for row in md_table(parts[0])]
            found = f"列出 {len(structures)} 个相关结构：{'、'.join(structures)}" if structures else "资料未列出结构信息，记录为知识缺口"
            return {"message": f"已读取 {p['station']} 设备结构资料，{found}；资料提及不等于结构存在故障。", "outputs": [meta["name"]]}
        mapping = next((row for row in md_table(parts[0]) if row and row[0] == p["station"]), None)
        if not mapping:
            return {"message": f"工艺资料中没有 {p['station']} 的 task／parameter 映射，记录为知识缺口。", "outputs": [meta["name"]]}
        loading = next((row for row in md_table(parts[1]) if row and row[0] == mapping[4]), None) if len(parts) > 1 else None
        where = f"，安装位置 {loading[2]}，首次绑定工站 {loading[3]}" if loading else ""
        return {"message": f"已确认 {mapping[1]}／{mapping[2]} 关联零件为{mapping[4]}（{mapping[5]}）{where}。", "outputs": [meta["name"]]}

    async def _do_bindings(self, ctx, p, node):
        source = self._load(ctx, p["source"])
        tgs = sorted(source["tg"].dropna().astype(str).unique().tolist()) if len(source) else []
        self.app.db.event(ctx["sid"], f"已从 {p['source']} 取得 {len(tgs):,} 个去重 TG 作为绑定查询条件，TG 清单不进入模型上下文。", "tool", "running",
                          {"tool": "splunk.query", "args": {"kind": "bindings", "filters": {"tgs": f"<来自 {p['source']} 的 {len(tgs):,} 个 TG>", "component": p["component"]}}},
                          run_id=ctx["run"], node_id=ctx["node"])
        result = await self._call(ctx, "splunk.query", {"kind": "bindings", "filters": {"tgs": tgs, "component": p["component"]}, "name": p["name"], "description": p["description"]})
        frame = self._load(ctx, result["variable_name"])
        matched = frame["tg"].nunique() if len(frame) else 0
        return {"message": f"首次绑定查询完成：{len(tgs):,} 件工件中 {matched:,} 件取得{p['component']}绑定记录，未匹配 {len(tgs) - matched:,} 件，保存为 {result['variable_name']}。",
                "outputs": [result["variable_name"]]}

    async def _do_analysis(self, ctx, p, node):
        template = {"patterns": PATTERNS, "batch": BATCH}[p["template"]]
        code = HEADER.format(params=json.dumps({k: v for k, v in p.items() if k not in ("action", "template")}, ensure_ascii=False)) + template
        result = await self._call(ctx, "python.execute", {"code": code})
        outputs = [o["name"] for o in result["outputs"]]
        if p["template"] == "patterns":
            f = self._load(ctx, "pattern_findings")
            trend = f.get("trend") or {}
            parts = [f"{f['nc_pieces']:,} 件不良工件、{f['nc_events']:,} 条 NC 事件"]
            if f.get("startup_pieces") is not None and f["nc_pieces"]:
                parts.append(f"{startup_label(p['config'])}上线 {f['startup_pieces']:,} 件（{f['startup_pieces'] / f['nc_pieces']:.1%}）")
            if f.get("long_dwell_pieces") is not None:
                parts.append(f"跨工站间隔超过 {p['config']['long_dwell_hours']} 小时 {f['long_dwell_pieces']:,} 件")
            if trend:
                parts.append(f"周不良率由 {pct(trend['early_rate'])} 变为 {pct(trend['recent_rate'])}，形态为{trend['shape']}")
            strong = [c for c in f.get("strong_concentration", []) if c["dimension"] == "batch"]
            if strong:
                parts.append("、".join(f"{c['component']}批次 {c['group']} 占 {c['nc_share']:.1%}" for c in strong) + "，达到集中度线索阈值")
            rewritten = "NC 明细已写入带首次绑定信息的新版本。" if p["inputs"]["nc"] in outputs else "未取得绑定记录，NC 明细保持原版本。"
            return {"message": "规律统计完成：" + "；".join(parts) + "。" + rewritten, "outputs": outputs}
        f = self._load(ctx, "batch_findings")
        batches = sorted((r for r in f["risk"] if r["dimension"] == "batch" and r["group"]), key=lambda r: r["rate"], reverse=True)
        top = batches[0] if batches else None
        text = f"批次风险比较完成：同期 {f['tested_pieces']:,} 件检测工件、{f['nc_pieces']:,} 件不良"
        if top:
            ratio = f"，为其他批次的 {top['risk_ratio']:.1f} 倍" if top.get("risk_ratio") else ""
            text += f"；不良率最高的批次 {top['group']} 投入 {top['tested']:,} 件、不良率 {pct(top['rate'])}{ratio}"
        return {"message": text + "。该结果为统计关联，尚需来料与现场验证。", "outputs": outputs}

    async def _do_curves(self, ctx, p, node):
        query = await self._call(ctx, "curves.query", {"filters": {"station_id": p["station"], "kind": p["kind"]}, "name": "process_curves",
                                                       "description": f"{p['station']} {p['kind']} 过程曲线逐点长表，含曲线 ID、TG、阶段、坐标、单位、判定、批次与供应商"})
        if not query["row_count"]:
            return {"message": f"曲线源中没有 {p['station']} 的过程曲线，未生成或还原任何曲线。", "outputs": [query["variable_name"]]}
        code = HEADER.format(params=json.dumps({"inputs": {"curves": query["variable_name"]}, "profiles": p.get("profiles", {})}, ensure_ascii=False)) + CURVES
        result = await self._call(ctx, "python.execute", {"code": code})
        f = self._load(ctx, "curve_findings")
        groups = {g["result"]: g for g in f.get("groups", [])}
        text = f"曲线比较完成：{f['curves']:,} 条曲线（" + "、".join(f"{k} {v}" for k, v in f["by_result"].items()) + "）"
        if "OK" in groups and "NC" in groups:
            text += f"，峰值均值 OK {groups['OK']['peak_mean']:.2f}、NC {groups['NC']['peak_mean']:.2f}"
        if f.get("segments"):
            text += f"；{f['segments']['curves']} 条 NC 曲线在相对接触点约 {f['segments']['x_start_median']:.1f}—{f['segments']['x_end_median']:.1f} {f['x_unit']} 区段偏离 OK 参考范围"
        if f.get("coverage"):
            text += f"。曲线样本覆盖 {f['coverage'][0][:10]} 至 {f['coverage'][1][:10]}，参考范围为探索性统计，非工艺规格"
        return {"message": text + "。", "outputs": [query["variable_name"]] + [o["name"] for o in result["outputs"]]}

    async def _do_changes(self, ctx, p, node):
        result = await self._call(ctx, "splunk.query", {"kind": "changes", "filters": {"station_id": p["station"], "start": p["start"]}, "name": p["name"],
                                                        "description": f"{p['station']} 自 {p['start'][:10]} 起的设备变化点记录，含结构、变化类型、时间及说明"})
        frame = self._load(ctx, result["variable_name"])
        if frame.empty:
            return {"message": f"检索范围内没有 {p['station']} 的变化点记录；仅说明日志覆盖范围内未见记录，不能据此排除设备。", "outputs": [result["variable_name"]]}
        items = "；".join(f"{r.occurred_at[:10]} {r.structure}（{r.change_type}）" for r in frame.itertuples())
        return {"message": f"检索到 {len(frame)} 条变化点：{items}。记录描述不等于已证实的故障。", "outputs": [result["variable_name"]]}

    async def _do_chart(self, ctx, p, node):
        result = await self._call(ctx, "chart.create", p["args"])
        return {"message": f"已基于 {p['args']['variable']} 的全量数据重新制作图表，保存为 {result['name']}；原图 {p['source']} 保留。", "outputs": [result["name"]]}

    async def _do_report(self, ctx, p, node):
        result = await self._call(ctx, "report.create", {})
        return {"message": f"离线 HTML 报告已生成，保存为 {result['name']}，可在分析侧栏导出。", "outputs": [result["name"]]}

    async def _do_review(self, ctx, p, node):
        f = self._load(ctx, "pattern_findings")
        rules = p.get("config") or self.analysis_config()
        station = SCENARIO["stations"][p["station"]]
        equipment = md_table(self._load(ctx, f"equipment_knowledge_{p['station']}"))
        trend = f.get("trend") or {}
        lines = [f"## 规律挖掘结果 · {p['station']} {station['label']}", "",
                 f"- **不良规模**：{f['nc_pieces']:,} 件不良工件、{f['nc_events']:,} 条 NC 事件（变量 `nc_records`）。"]
        if f.get("startup_pieces") is not None and f["nc_pieces"]:
            lines.append(f"- **开班**：{f['startup_pieces']:,} 件在{startup_label(rules)}上线，占 {f['startup_pieces'] / f['nc_pieces']:.1%}；仅为 NC 构成，需与同期投入比例对照（`startup_distribution`）。")
        if f.get("shift_classes"):
            lines.append("- **班产**：不良工件所在班次分类 " + "、".join(f"{k} {v} 件" for k, v in f["shift_classes"].items()) + "（`nc_shift_context`）。")
        if f.get("long_dwell_pieces") is not None:
            lines.append(f"- **滞留**：{f['long_dwell_pieces']:,} 件存在超过 {rules['long_dwell_hours']} 小时的跨工站间隔，需结合停线与返工记录核实（`long_dwell`）。")
        if trend:
            jump = trend.get("largest_jump")
            lines.append(f"- **趋势**：周不良率由 {pct(trend['early_rate'])} 变为 {pct(trend['recent_rate'])}，形态为“{trend['shape']}”"
                         + (f"，最大周增幅出现在 {jump['week_start']} 当周（{jump['change']:+.2%}）" if jump else "") + "（`daily_trend`）。")
        for item in f.get("top_concentration", []):
            label = "强集中线索" if item["strong_concentration"] else "未达集中阈值"
            lines.append(f"- **{item['component']} {item['dimension']}**：{item['group']} 占不良工件 {item['nc_share']:.1%}，{label}；集中度不代表风险更高（`batch_concentration`）。")
        if equipment:
            lines.append(f"- **设备结构**：{p['station']} 资料列出 {'、'.join(r[0] for r in equipment)}，作为待验证假设，不据此认定故障。")
        if f.get("warnings"):
            lines += ["", "**数据限制**：" + "；".join(f["warnings"])]
        text = "\n".join(lines)
        meta = self.app.host.save(ctx["sid"], "pattern_review", text, "规律挖掘阶段的证据汇总与待验证假设", "text", ctx["node"], [{"name": "pattern_findings", "version": self.app.variables.describe(ctx["sid"], "pattern_findings")["version"]}])
        return {"message": "规律复核完成：各方向检查结果已汇总，线索与待验证假设已区分记录。", "outputs": [meta["name"]]}

    async def _do_conclusion(self, ctx, p, node):
        sid = ctx["sid"]
        known = {v["name"] for v in self.app.variables.list(sid)}
        station = SCENARIO["stations"][p["station"]]
        lines = [f"## 调查结论 · {p['station']} {station['label']}"]
        evidence, hypotheses, actions = [], [], []
        patterns = self._load(ctx, "pattern_findings") if "pattern_findings" in known else {}
        jump = (patterns.get("trend") or {}).get("largest_jump")
        if "batch_findings" in known:
            f = self._load(ctx, "batch_findings")
            batches = sorted((r for r in f["risk"] if r["dimension"] == "batch" and r["group"]), key=lambda r: r["rate"], reverse=True)
            lines += ["", "### 批次方向（统计关联）"]
            for r in batches[:4]:
                ratio = f"，风险比 {r['risk_ratio']:.2f}" if r.get("risk_ratio") else ""
                lines.append(f"- {r['group']}：投入 {r['tested']:,} 件、不良 {r['nc']:,} 件，不良率 {pct(r['rate'])}{ratio}")
            if batches and (batches[0].get("risk_ratio") or 0) >= 2:
                lines.append(f"- 判断：{batches[0]['group']} 的不良率显著高于同期其他批次，批次方向得到统计支持，尚未经来料检测确认。")
                hypotheses.append(f"{station['component']}批次 {batches[0]['group']} 与{station['label']} NC 升高存在统计关联，待来料检测与隔离对照验证")
                actions.append(f"隔离并抽检 {batches[0]['group']} 来料的尺寸与表面状态，并以其他批次做同条件对照。")
            else:
                lines.append("- 判断：各批次不良率未见显著差异；NC 集中于高投入批次或供应商主要由投入量解释，当前证据不支持批次方向。")
            evidence.append("batch_risk、batch_value_distribution")
        if "curve_findings" in known:
            f = self._load(ctx, "curve_findings")
            lines += ["", "### 过程曲线"]
            lines.append(f"- {f['summary']}")
            if f.get("segments"):
                lines.append(f"- {f['segments']['curves']} 条 NC 曲线在相对接触点约 {f['segments']['x_start_median']:.1f}—{f['segments']['x_end_median']:.1f} {f['x_unit']} 区段偏离 OK 参考范围。")
            if f.get("coverage"):
                lines.append(f"- 曲线样本覆盖 {f['coverage'][0][:10]} 至 {f['coverage'][1][:10]}，与调查时段不完全重合时仅作为过程形态参考。")
            evidence.append("curve_features、curve_abnormal_segments")
        if "equipment_changes" in known:
            changes = self._load(ctx, "equipment_changes")
            lines += ["", "### 设备变化点"]
            for r in changes.itertuples():
                aligned = jump and 0 <= (datetime.fromisoformat(r.occurred_at[:10]) - datetime.fromisoformat(jump["week_start"])).days < 7
                lines.append(f"- {r.occurred_at[:10]} {r.structure}（{r.change_type}）：{r.message}" + (f"。该记录落在不良率最大周增幅（{jump['week_start']} 当周）内，时间吻合" if aligned else ""))
                hypotheses.append(f"{r.structure} 的{r.change_type}记录可能与{station['label']}变化有关，需现场核验" + ("；与不良率突增时间吻合，优先验证" if aligned else ""))
                actions.append(f"核查 {r.structure} 的{r.change_type}记录、参数版本与审批，并通过点检或受控对照验证。")
            if changes.empty:
                lines.append("- 检索范围内无变化点记录；仅说明日志覆盖范围内未见记录，不能据此排除设备。")
                actions.append("按设备结构资料安排现场测量与点检，补充变化点日志覆盖。")
            evidence.append("equipment_changes")
        actions.append("验证期间持续监控同期不良率与参数分布，作为措施有效性的判断依据。")
        lines += ["", "### 判断与下一步",
                  "- 以上为统计关联与待验证假设，尚未确认根因；现场验证完成前不应表述为已整改。"]
        lines += [f"- 建议：{item}" for item in actions]
        text = "\n".join(lines)
        meta = self.app.host.save(sid, "investigation_conclusion", text, "深入验证阶段的调查结论、证据引用与现场验证建议", "text", ctx["node"])
        self.app.orchestrator.remember(sid, hypotheses=hypotheses, evidence=evidence)
        return {"message": "调查结论已形成：统计关联、曲线差异与变化点证据已分别记录，并给出现场验证建议。", "outputs": [meta["name"]]}

    # ---- review & follow-ups --------------------------------------------
    def review(self, sid, run, plan):
        if plan is None:
            return run["state"][run["nodes"][-1]["id"]].get("message"), None, None
        if plan["stage"] == "investigation":
            findings = self.app.variables.load(sid, "pattern_findings")
            strong = [c for c in findings.get("strong_concentration", []) if c["dimension"] in ("batch", "supplier")]
            sudden = (findings.get("trend") or {}).get("shape") == "突然升高"
            directions = (["batch"] if strong or sudden else []) + ["equipment"]
            scope = {**plan["scope"], "directions": directions}
            text = self.app.variables.load(sid, "pattern_review")
            if strong:
                focus = "批次集中达到线索阈值，下一阶段比较同期 OK／NC 的批次风险，并同时核查设备变化点与过程曲线。"
            elif sudden:
                focus = "不良率呈突然升高，批次未达强集中阈值；下一阶段同时比较来料批次风险、设备变化点与过程曲线。"
            else:
                focus = "未见批次强集中或突变，下一阶段聚焦设备变化点与过程曲线。"
            self.app.orchestrator.remember(sid, stage="investigation_completed", findings=findings.get("findings", [])[:6])
            return text + f"\n\n**下一步**：{focus}深入验证计划需确认后执行。", self.verification_plan(scope, directions), scope
        self.app.orchestrator.remember(sid, stage="verification_completed")
        return self.app.variables.load(sid, "investigation_conclusion"), None, None

    @staticmethod
    def resolve_chart(charts, text):
        """Resolve "the chart just now" to one chart version; several equally recent charts need clarification."""
        explicit = [c for c in charts if c["name"] in text] or [c for c in charts if c["metadata"]["chart"]["variable"] in text] \
            or [c for c in charts if len(title := re.sub(r"（.*?）|\(.*?\)", "", c["description"]).strip()) >= 4 and title in text]
        if explicit:
            return max(explicit, key=lambda v: v["created_at"]), []
        latest = max(charts, key=lambda v: v["created_at"])
        stamp = datetime.fromisoformat(latest["created_at"])
        recent = [c for c in charts if c.get("producer") == latest.get("producer")
                  and abs((datetime.fromisoformat(c["created_at"]) - stamp).total_seconds()) < 5]
        return (latest, []) if len(recent) <= 1 else (None, sorted(recent, key=lambda v: v["created_at"]))

    def _chart_change(self, sid, kind, text=""):
        charts = [v for v in self.app.variables.list(sid) if v["kind"] == "chart" and (v.get("metadata") or {}).get("chart")]
        if not charts:
            return {"type": "answer", "message": "当前会话还没有可调整的图表，请先完成相关分析。"}
        latest, candidates = self.resolve_chart(charts, text)
        if latest is None:
            options = "\n".join(f"- `{c['name']}` v{c['version']}：{c['description']}" for c in candidates)
            return {"type": "ask", "message": f"上一步同时生成了 {len(candidates)} 张图表，需要明确调整哪一张：\n\n{options}\n\n"
                                                f"可回复例如“把 {candidates[0]['name']} 改成{next(w for w, k in CHART_WORDS.items() if k == kind)}”。"}
        meta = latest["metadata"]["chart"]
        args = {"variable": meta["variable"], "kind": kind, "y": meta.get("y")}
        if kind in ("box", "violin", "histogram"):
            args["x"] = meta.get("category")
        else:
            args.update(x=meta.get("x"), group=meta.get("group"))
        args = {k: v for k, v in args.items() if v}
        args["category"] = meta.get("category")
        args["description"] = f"{meta['variable']} 的{next(w for w, k in CHART_WORDS.items() if k == kind)}（由 {latest['name']} 调整）"
        node = self._node("g1", "skill", "charting", "调整图表类型", f"将 {latest['name']} 改为{kind}图", "新的图表版本",
                          f"为按新的展示方式比较 {meta['variable']}，正在基于其全量数据重新制作图表，原图保持不变。", {"action": "chart", "args": args, "source": latest["name"]})
        return {"type": "direct", "origin": "demo", "nodes": [node]}

    def _issue(self, sid, text, memory):
        bound = re.search(r"ISS-[0-9a-f]{16}", text)
        if bound:
            issue = self.app.db.get("issue", bound.group(0))
            variables = [v for v in self.app.variables.list(sid) if v["kind"] in ("table", "chart", "text")]
            evidence = list(dict.fromkeys([*issue.get("evidence", []), *(f"{v['name']} v{v['version']}：{v['description']}" for v in variables)]))
            hypotheses = list(dict.fromkeys([*issue.get("hypotheses", []), *memory.get("hypotheses", [])]))
            fields = {"evidence": evidence, "hypotheses": hypotheses}
            if issue["status"] == "open" and variables:
                fields["status"] = "investigating"
            added = len(evidence) - len(issue.get("evidence", []))
            return {"type": "issue", "issue_id": issue["id"], "fields": fields,
                    "message": f"已将当前会话关联到问题 **{{issue_id}}**，新增 {added} 项证据引用；措施、责任人、期限和关闭仍需在问题台账中确认。"}
        scope = memory.get("scope") or self.parse_scope(text)
        station = SCENARIO["stations"].get(scope["station"], {"label": "过程参数"})
        variables = [v for v in self.app.variables.list(sid) if v["kind"] in ("table", "chart", "text")]
        evidence = [f"{v['name']} v{v['version']}：{v['description']}" for v in variables][:12]
        fields = {"status": "investigating" if variables else "open", "evidence": evidence, "hypotheses": memory.get("hypotheses", [])}
        description = f"{self.describe(scope) if scope['station'] in SCENARIO['stations'] else scope['station']} {station['label']} NC 增多。" + (f"调查目标：{memory['goal']}" if memory.get("goal") else "")
        lines = ["已登记问题 **{issue_id}**，并关联当前会话。", ""]
        lines.append(f"- 证据：已关联 {len(evidence)} 项会话变量及版本。" if evidence else "- 证据：当前会话尚无分析产物，后续结果可在台账中补充。")
        lines.append(f"- 假设：{len(fields['hypotheses'])} 项待验证假设。" if fields["hypotheses"] else "- 假设：尚未形成可验证假设。")
        lines.append("- 措施、责任人和期限保持未指定，需在问题台账中填写；关闭问题需人工确认。")
        return {"type": "issue", "title": f"{scope['station']} {station['label']}异常", "description": description, "station": scope["station"], "fields": fields, "message": "\n".join(lines)}

"""API and orchestration acceptance scenarios on the 90-day simulated plant.

Demo mode validates the workflow only; the scripted model double validates the
model-mode protocol (routing, plan confirmation, SQL/code repair, stage review),
not natural-language ability of a real model.
"""
import io
import json
import re
import time

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from line_engineer.api.server import create_app
from line_engineer.application import Application
from line_engineer.domain.demo import generate_demo

QUESTION = "分析 OP20 最近两周压装力 NC 增多的原因"


@pytest.fixture(scope="session")
def demo_dir(tmp_path_factory):
    path = tmp_path_factory.mktemp("demo90")
    generate_demo(path, days=90)
    return path


@pytest.fixture
def workbench(tmp_path, demo_dir):
    opened = []

    def start(data_dir=None, **settings):
        app = Application(data_dir or tmp_path / "data")
        app.demo_dir = demo_dir
        if settings:
            app.settings.update(settings)
        client = TestClient(create_app(app, static_dir=tmp_path / "no-static"))
        client.__enter__()
        opened.append(client)
        return client, app

    def stop(client):
        opened.remove(client)
        client.__exit__(None, None, None)

    start.stop = stop
    yield start
    for client in list(opened):
        client.__exit__(None, None, None)


def detail(client, sid):
    response = client.get(f"/api/sessions/{sid}")
    assert response.status_code == 200, response.text
    return response.json()


def wait(client, sid, predicate, timeout=180, what="条件"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = detail(client, sid)
        if predicate(current):
            return current
        time.sleep(0.25)
    raise AssertionError(f"等待{what}超时，会话状态：{current['session']['status']}；最新事件：{[e['message'] for e in current['events'][-5:]]}")


def awaiting(stage=None, revision=None):
    def check(d):
        plan = d["plan"]
        return (d["session"]["status"] == "awaiting_approval" and plan and plan["status"] == "awaiting_approval"
                and (stage is None or plan["stage"] == stage) and (revision is None or plan["revision"] == revision))
    return check


def status_is(*values):
    return lambda d: d["session"]["status"] in values


def new_session(client):
    return client.post("/api/sessions", json={}).json()["id"]


def ask(client, sid, content):
    response = client.post(f"/api/sessions/{sid}/messages", json={"content": content})
    assert response.status_code == 200, response.text
    return response.json()


def approve(client, sid, plan, expect=200):
    response = client.post(f"/api/sessions/{sid}/plans/{plan['id']}/approve", json={"revision": plan["revision"]})
    assert response.status_code == expect, response.text
    return response


def node_state(d, node_id):
    run = d["runs"][-1] if d["runs"] else None
    return run["state"].get(node_id, {}).get("status") if run else None


def step_starts(d, node_id):
    return [e for e in d["events"] if e["kind"] == "step" and e["status"] == "running" and e["node_id"] == node_id]


def latest_assistant(d):
    return next(m["content"] for m in reversed(d["messages"]) if m["role"] == "assistant")


def test_demo_two_stage_investigation_with_follow_ups_report_and_issue(workbench):
    client, app = workbench()
    sid = new_session(client)
    ask(client, sid, QUESTION)
    d = wait(client, sid, awaiting("investigation"), what="规律挖掘计划")
    plan = d["plan"]
    assert plan["revision"] == 1 and len(plan["nodes"]) == 8
    assert {n["skill"] for n in plan["nodes"] if n["kind"] == "skill"} >= {"problem-analysis", "splunk", "data-analysis", "process", "equipment-structure"}
    assert not d["runs"], "计划未确认前不能执行"

    approve(client, sid, plan)
    d = wait(client, sid, awaiting("verification"), what="深入验证计划")
    names = {v["name"] for v in d["variables"]}
    assert {"nc_records", "visit_records", "measurement_trend", "first_bindings", "pattern_findings", "pattern_review"} <= names
    assert next(v for v in d["variables"] if v["name"] == "nc_records")["version"] == 2, "首次绑定写回生成新版本"
    assert "深入验证计划需确认后执行" in latest_assistant(d)

    # Timeline protocol: one professional sentence per step, real status, ordered, carrying session/turn/run/step.
    steps = [e for e in d["events"] if e["kind"] == "step"]
    assert steps and all("我" not in e["message"] for e in steps)
    assert all(e["session_id"] == sid and e["turn"] == 1 and e["run_id"] and e["node_id"] for e in steps)
    ids = [e["id"] for e in d["events"]]
    assert ids == sorted(ids) and len(ids) == len(set(ids))
    for node in plan["nodes"]:
        starts, ends = step_starts(d, node["id"]), [e for e in steps if e["node_id"] == node["id"] and e["status"] == "completed"]
        assert len(starts) == 1 and len(ends) == 1 and starts[0]["id"] < ends[0]["id"], node["id"]
    assert any(e["kind"] == "sql" and "SELECT" in e["details"]["sql"] for e in d["events"])
    assert any(e["kind"] == "code" and "ctx.vars.load" in e["details"]["code"] for e in d["events"])

    # Preview stays in sync with the latest table version.
    preview = client.get(f"/api/sessions/{sid}/variables/nc_records/preview").json()
    assert preview["source_version"] == 2 and preview["available"] and 1 <= len(preview["rows"]) <= 40
    assert "batch" in "".join(preview["rows"][0].keys()).lower()

    # Ambiguous reference to "the chart just now" must be clarified, then resolve to an explicit version.
    ask(client, sid, "把刚才的图改成箱线图")
    d = wait(client, sid, lambda x: "需要明确调整哪一张" in latest_assistant(x), what="图表澄清")
    assert "shift_output_chart" in latest_assistant(d) and "daily_trend_chart" in latest_assistant(d)
    ask(client, sid, "把 daily_trend_chart 改成箱线图")
    d = wait(client, sid, lambda x: any(v["kind"] == "chart" and v["metadata"].get("chart", {}).get("kind") == "box" for v in x["variables"]) and x["session"]["status"] == "awaiting_approval", what="箱线图")
    box = next(v for v in d["variables"] if v["metadata"].get("chart", {}).get("kind") == "box")
    assert box["parents"] == [{"name": "daily_trend", "version": 1}]
    assert "daily_trend_chart" in {v["name"] for v in d["variables"]}, "原图保留"

    # Free-text "OK" never approves; approval is bound to session + plan + revision.
    ask(client, sid, "OK")
    d = wait(client, sid, lambda x: "计划卡片" in latest_assistant(x), what="文本确认提示")
    assert d["plan"]["status"] == "awaiting_approval" and d["plan"]["stage"] == "verification"

    approve(client, sid, d["plan"])
    d = wait(client, sid, status_is("completed"), what="调查结论")
    conclusion = app.variables.load(sid, "investigation_conclusion")
    assert "批次方向得到统计支持" in conclusion and "尚未确认根因" in conclusion
    memory = d["session"]["memory"]
    assert memory["approved_plan"]["stage"] == "verification" and memory["scope"]["station"] == "OP20" and memory["hypotheses"]

    ask(client, sid, "生成离线分析报告")
    d = wait(client, sid, lambda x: any(v["kind"] == "report" for v in x["variables"]) and x["session"]["status"] == "completed", what="报告")
    report = next(v for v in d["variables"] if v["kind"] == "report")
    exported = client.get(f"/api/sessions/{sid}/variables/{report['name']}/export")
    html = exported.content.decode("utf-8")
    assert exported.headers["content-type"].startswith("text/html")
    assert "Plotly" in html and "<script src=" not in html, "图表脚本随报告本地化"
    assert "href='#var-pattern_findings'" in html and "id='var-pattern_findings'" in html

    ask(client, sid, "把本次调查登记到问题台账")
    d = wait(client, sid, lambda x: any(e["kind"] == "issue" for e in x["events"]), what="登记问题")
    issue = next(e for e in d["events"] if e["kind"] == "issue")["details"]["issue"]
    assert issue["status"] == "investigating" and sid in issue["session_ids"] and issue["evidence"]

    other = new_session(client)
    ask(client, other, f"把本会话证据更新到问题台账 {issue['id']}")
    wait(client, other, lambda x: any(e["kind"] == "issue" for e in x["events"]), what="按编号更新问题")
    issues = client.get("/api/issues").json()
    assert len(issues) == 1 and set(issues[0]["session_ids"]) == {sid, other}


def test_op30_supplier_concentration_counterexample_and_recipe_change_point(workbench):
    client, app = workbench()
    sid = new_session(client)
    ask(client, sid, "分析 OP30 最近两周拧紧扭矩 NC 增多的原因")
    approve(client, sid, wait(client, sid, awaiting("investigation"), what="规律挖掘计划")["plan"])
    approve(client, sid, wait(client, sid, awaiting("verification"), what="深入验证计划")["plan"])
    wait(client, sid, status_is("completed"), what="调查结论")
    patterns = app.variables.load(sid, "pattern_findings")
    dominant = next(c for c in patterns["strong_concentration"] if c["dimension"] == "supplier")
    risk = next(r for r in app.variables.load(sid, "batch_findings")["risk"] if r["dimension"] == "supplier" and r["group"] == dominant["group"])
    assert dominant["nc_share"] > .8 and risk["risk_ratio"] < 1, "NC 占比高但风险比低于 1 的供应商反例"
    conclusion = app.variables.load(sid, "investigation_conclusion")
    assert "当前证据不支持批次方向" in conclusion
    jump = patterns["trend"]["largest_jump"]["week_start"]
    assert f"不良率最大周增幅（{jump} 当周）内，时间吻合" in conclusion, "配方变化点与不良率突增周吻合"


def test_stale_cross_session_and_revised_plan_approvals_are_rejected(workbench):
    client, _ = workbench()
    sid, other = new_session(client), new_session(client)
    ask(client, sid, QUESTION)
    first = wait(client, sid, awaiting("investigation", 1))["plan"]

    response = client.post(f"/api/sessions/{sid}/plans/{first['id']}/revise", json={"revision": 1, "feedback": "改为最近一周"})
    assert response.status_code == 200
    revised = wait(client, sid, awaiting("investigation", 2))["plan"]
    assert revised["id"] == first["id"] and revised["scope"]["days"] == 7

    assert "旧版本不能确认" in approve(client, sid, first, expect=409).json()["detail"]
    assert "不属于当前会话" in approve(client, other, revised, expect=409).json()["detail"]
    statuses = {p["revision"]: p["status"] for p in detail(client, sid)["plans"]}
    assert statuses == {1: "superseded", 2: "awaiting_approval"}
    assert not detail(client, sid)["runs"]

    approve(client, sid, revised)
    approve(client, sid, revised, expect=409)
    client.post(f"/api/sessions/{sid}/cancel")
    wait(client, sid, status_is("cancelled"))


def test_scope_change_during_execution_pauses_and_requires_new_approval(workbench):
    client, _ = workbench()
    sid = new_session(client)
    ask(client, sid, QUESTION)
    plan = wait(client, sid, awaiting("investigation"))["plan"]
    approve(client, sid, plan)
    wait(client, sid, status_is("running"))
    ask(client, sid, "只看夜班")
    d = wait(client, sid, awaiting("investigation", 2), what="修订计划")
    assert d["runs"][-1]["status"] == "paused"
    assert d["plan"]["scope"]["shift"] == "night" and "'19'" in d["plan"]["nodes"][0]["params"]["sql"]
    assert any(e["status"] == "paused" for e in d["events"])
    completed = [k for k, v in d["runs"][-1]["state"].items() if v["status"] == "completed"]
    assert completed and len(completed) < len(plan["nodes"])


def test_cancel_and_resume_do_not_rerun_completed_steps(workbench):
    client, _ = workbench()
    sid = new_session(client)
    ask(client, sid, QUESTION)
    plan = wait(client, sid, awaiting("investigation"))["plan"]
    approve(client, sid, plan)
    wait(client, sid, lambda x: node_state(x, "p1") == "completed")
    assert client.post(f"/api/sessions/{sid}/cancel").status_code == 200
    d = wait(client, sid, status_is("cancelled"))
    done = [k for k, v in d["runs"][-1]["state"].items() if v["status"] == "completed"]
    assert "p1" in done and "p8" not in done
    assert client.post(f"/api/sessions/{sid}/cancel").status_code == 409

    assert client.post(f"/api/sessions/{sid}/resume").status_code == 200
    d = wait(client, sid, awaiting("verification"), what="恢复后完成")
    for node_id in done:
        assert len(step_starts(d, node_id)) == 1, f"{node_id} 不应重跑"


def test_restart_resumes_interrupted_task_but_not_user_stopped_task(workbench, tmp_path):
    data = tmp_path / "restart"
    client, _ = workbench(data)
    running, stopped = new_session(client), new_session(client)
    for sid in (running, stopped):
        ask(client, sid, QUESTION)
    for sid in (running, stopped):
        approve(client, sid, wait(client, sid, awaiting("investigation"))["plan"])
    wait(client, running, lambda x: node_state(x, "p2") == "completed")
    client.post(f"/api/sessions/{stopped}/cancel")
    wait(client, stopped, status_is("cancelled"))
    workbench.stop(client)

    client, app = workbench(data)
    d = wait(client, running, awaiting("verification"), what="重启后自动恢复")
    assert any("应用关闭时任务尚未完成" in e["message"] for e in d["events"])
    assert any("从最近检查点恢复执行" in e["message"] for e in d["events"])
    for node_id in ("p1", "p2"):
        assert len(step_starts(d, node_id)) == 1
    s = detail(client, stopped)
    assert s["session"]["status"] == "cancelled" and s["runs"][-1]["status"] == "cancelled"


def test_parallel_limit_queues_additional_sessions(workbench):
    client, _ = workbench(max_parallel_sessions=1)
    first, second = new_session(client), new_session(client)
    for sid in (first, second):
        ask(client, sid, QUESTION)
    plans = {sid: wait(client, sid, awaiting("investigation"))["plan"] for sid in (first, second)}
    approve(client, first, plans[first])
    wait(client, first, status_is("running"))
    approve(client, second, plans[second])
    d = wait(client, second, status_is("queued"), what="排队")
    assert any(e["status"] == "queued" for e in d["events"])
    assert client.get("/api/health").json()["queued"] == 1
    client.post(f"/api/sessions/{second}/cancel")
    wait(client, second, status_is("cancelled"))
    client.post(f"/api/sessions/{first}/cancel")
    wait(client, first, status_is("cancelled"))


def test_issue_ledger_requires_confirmation_to_close_and_keeps_history(workbench):
    client, _ = workbench()
    sid = new_session(client)
    issue = client.post("/api/issues", json={"title": "OP20 压装力 NC 增多", "session_id": sid}).json()
    assert issue["status"] == "open" and issue["session_ids"] == [sid]
    response = client.patch(f"/api/issues/{issue['id']}", json={"status": "closed"})
    assert response.status_code == 400 and "确认" in response.json()["detail"]
    client.patch(f"/api/issues/{issue['id']}", json={"actions": ["隔离可疑批次"], "verification": "两周不良率回落", "owner": "张工"})
    closed = client.patch(f"/api/issues/{issue['id']}", json={"status": "closed", "confirm_close": True}).json()
    reopened = client.patch(f"/api/issues/{issue['id']}", json={"status": "investigating"}).json()
    assert closed["status"] == "closed" and reopened["status"] == "investigating"
    assert [h["action"] for h in reopened["history"]] == ["created", "updated", "closed", "reopened"]
    assert client.post("/api/issues", json={"title": "  "}).status_code == 400


def test_import_snapshot_and_export_keep_sessions_isolated(workbench):
    client, _ = workbench()
    source, target = new_session(client), new_session(client)
    frame = pd.DataFrame({"tg": [f"TG{i}" for i in range(50)], "批次": ["B1", "B2"] * 25, "值": range(50)})
    content = frame.to_csv(index=False).encode("gb18030")
    imported = client.post(f"/api/sessions/{source}/import", files={"file": ("来料批次.csv", content, "text/csv")}).json()
    assert imported["row_count"] == 50 and imported["name"].startswith("import")
    assert client.get(f"/api/sessions/{target}/variables/{imported['name']}/preview").status_code == 404

    snapshot = client.post(f"/api/sessions/{target}/snapshot", json={"source_session_id": source, "name": imported["name"], "version": 1}).json()
    assert snapshot["metadata"]["snapshot_of"] == {"session_id": source, "name": imported["name"], "version": 1}
    assert client.post(f"/api/sessions/{source}/snapshot", json={"source_session_id": source, "name": imported["name"], "version": 1}).status_code == 400

    page = client.get(f"/api/sessions/{target}/variables/{imported['name']}/table", params={"offset": 10, "limit": 5, "sort": "值", "direction": "desc"}).json()
    assert page["total"] == 50 and [r["值"] for r in page["rows"]] == [39, 38, 37, 36, 35]
    filtered = client.get(f"/api/sessions/{target}/variables/{imported['name']}/table", params={"search": "B2"}).json()
    assert filtered["total"] == 25
    csv = client.get(f"/api/sessions/{target}/variables/{imported['name']}/export").content.decode("utf-8-sig")
    assert csv.splitlines()[0] == "tg,批次,值"
    xlsx = client.get(f"/api/sessions/{target}/variables/{imported['name']}/export", params={"format": "xlsx"}).content
    assert pd.read_excel(io.BytesIO(xlsx)).shape == (50, 3)


def test_session_management_and_settings_never_expose_secrets(workbench):
    client, app = workbench()
    sid = new_session(client)
    patched = client.patch(f"/api/sessions/{sid}", json={"title": "夜班压装复盘", "pinned": True, "archived": True, "status": "completed"}).json()
    assert patched["title"] == "夜班压装复盘" and patched["pinned"] and patched["archived"] and patched["status"] == "idle"
    assert client.patch(f"/api/sessions/{sid}", json={"title": " "}).status_code == 400

    secret = "test-placeholder-value"
    client.patch("/api/settings", json={"model": {"api_key": secret, "headers": {"X-Token": secret}}})
    app.settings.values["connectors"] = {"mos": {"driver": "python", "callable": "x:y", "password": secret}}
    public = client.get("/api/settings").json()
    assert secret not in json.dumps(public) and public["model"]["api_key_set"] is True
    assert public["connectors"] == {"mos": {"driver": "python"}} and public["rules"]["long_dwell_hours"] == 4
    assert client.patch("/api/settings", json={"rules": {"unknown_rule": 1}}).status_code == 400

    # Sessions created in demo mode cannot be continued after switching to live mode.
    client.patch("/api/settings", json={"mode": "live"})
    assert client.post(f"/api/sessions/{sid}/messages", json={"content": QUESTION}).status_code == 409


# ---- model mode with a scripted model double ---------------------------------

SCRIPT: list = []
PROMPTS: list = []

PLAN = {"title": "OP20 压装力 NC 规律挖掘", "summary": "先查询 NC 明细，再统计每件不良的 NC 次数。", "stage": "investigation", "nodes": [
    {"id": "s1", "kind": "tool", "tool": "mos.query", "title": "查询 NC 明细", "description": "查询 OP20 的 NC 明细",
     "expected_output": "NC 明细", "params": {"question": "查询 OP20 压装力的 NC 明细"}, "progress_message": "我先查一下数据"},
    {"id": "s2", "kind": "skill", "skill": "data-analysis", "title": "统计每件 NC 次数", "description": "按 TG 统计 NC 次数",
     "expected_output": "每件 NC 次数表", "depends_on": ["s1"],
     "progress_message": "为区分不良工件数与 NC 事件数，正在按 TG 统计 NC 明细中每件工件的 NC 次数。"}]}


def scripted_model(messages, options):
    PROMPTS.append(messages)
    return SCRIPT.pop(0)


def test_model_mode_routes_confirms_repairs_sql_and_code_then_reviews(workbench):
    SCRIPT[:] = [
        json.dumps({"type": "skill", "name": "problem-analysis", "category": "problem_analysis"}, ensure_ascii=False),
        "按技能要求返回计划：\n```json\n" + json.dumps({"type": "plan", "plan": PLAN}, ensure_ascii=False) + "\n```",
        {"sql": "SELECT missing_column FROM nc_events", "name": "nc_records", "description": "OP20 压装力 NC 明细"},
        {"sql": "SELECT n.nc_id, v.tg, m.parameter FROM nc_events n JOIN measurements m ON m.measurement_id=n.measurement_id "
                "JOIN visits v ON v.visit_id=m.visit_id WHERE v.station_id='OP20' LIMIT 300"},
        {"type": "tool", "tool": "python.execute", "arguments": {"code": "frame = ctx.vars.load('nc_missing')"}, "message": "为统计每件 NC 次数，正在读取 NC 明细。"},
        {"type": "tool", "tool": "python.execute", "message": "为统计每件 NC 次数，正在按 TG 汇总 NC 明细。", "arguments": {"code":
            "frame = ctx.vars.load('nc_records')\nctx.vars.save('nc_per_piece', frame.groupby('tg').size().reset_index(name='nc_events'), '每件不良工件的 NC 事件数')"}},
        {"type": "finish", "message": "已按 TG 统计 NC 事件数，结果保存为 nc_per_piece。", "outputs": ["nc_per_piece", "not_a_variable"]},
        {"message": "本阶段确认了 NC 明细与每件 NC 次数（`nc_per_piece`），尚未形成根因结论。"},
    ]
    PROMPTS.clear()
    client, app = workbench(mode="live", model={"driver": "python", "callable": "test_api:scripted_model", "timeout": 30})
    sid = new_session(client)
    ask(client, sid, QUESTION)
    d = wait(client, sid, awaiting("investigation"), what="模型生成的计划")
    assert d["plan"]["origin"] == "model" and d["plan"]["skill_versions"].keys() == {"data-analysis"}
    assert d["plan"]["nodes"][0]["progress_message"].startswith("为"), "第一人称进度说明被替换"
    routing = PROMPTS[0][1]["content"]
    assert '"host_capabilities"' in routing and '"skills"' in routing and "problem-analysis" in routing

    approve(client, sid, d["plan"])
    d = wait(client, sid, status_is("completed"), what="模型模式执行")
    assert not SCRIPT, "脚本化模型的全部步骤均被调用"
    sql_runs = [e for e in d["events"] if e["kind"] == "sql"]
    assert [e["details"]["attempt"] for e in sql_runs] == [1, 2]
    errors = [e for e in d["events"] if e["kind"] == "error"]
    assert len(errors) == 2 and "nc_missing" in errors[1]["details"]["error"]
    state = d["runs"][-1]["state"]
    assert state["s2"]["outputs"] == ["nc_per_piece"], "只引用实际存在的变量"
    assert app.variables.load(sid, "nc_per_piece")["nc_events"].sum() == app.variables.describe(sid, "nc_records")["row_count"]
    assert "nc_per_piece" in latest_assistant(d)


# ---- runtime: adapters, executor, preview, events, development skills -----------

import asyncio
import importlib.util
import os
import shutil
from pathlib import Path

from line_engineer.connectors.model import ModelClient
from line_engineer.orchestration import host as host_module
from line_engineer.storage import variables as variables_module

ROOT = Path(__file__).resolve().parents[1]
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


def bare_app(tmp_path, demo_dir, **values):
    app = Application(tmp_path / "data")
    app.demo_dir = demo_dir
    app.settings.values.update(values)
    return app, app.orchestrator.create_session()["id"]


def dev_script(skill, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / ".cursor" / "skills" / skill / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_company_adapters_publish_real_rows_executed_sql_and_standard_charts(tmp_path, demo_dir, monkeypatch):
    monkeypatch.setenv("LINE_ENGINEER_MOS_SQLITE", str(demo_dir / "mos.sqlite"))
    stdin_script = {"driver": "script", "python": os.sys.executable, "script": str(ROOT / "examples/adapters/stdin_script.py")}
    app, sid = bare_app(tmp_path, demo_dir, connectors={
        "mos": {"driver": "python", "callable": "examples.adapters.mos_agent:query"},
        "charts": {"driver": "python", "callable": "examples.adapters.chart_program:render"},
        "splunk": stdin_script})
    sql = "SELECT station_id, COUNT(*) AS visits FROM visits GROUP BY station_id ORDER BY station_id"
    result = asyncio.run(app.host.call(sid, "mos.query", {"question": "统计各工站过站数", "sql": sql, "name": "station_visits"}))
    assert result["variable_name"] == "station_visits" and result["row_count"] == 3
    saved_sql = [v for v in app.variables.list(sid) if v["kind"] == "sql"]
    assert len(saved_sql) == 1 and app.variables.load(sid, saved_sql[0]["name"]) == sql, "MOS 子 agent 实际执行的 SQL 可追溯"
    assert any(e["kind"] == "sql" and e["details"]["sql"] == sql for e in app.db.events(sid))

    chart = asyncio.run(app.host.call(sid, "chart.create", {"variable": "station_visits", "kind": "bar", "x": "station_id", "y": "visits"}))
    figure = app.variables.load(sid, chart["name"])
    assert chart["kind"] == "chart" and chart["parents"] == [{"name": "station_visits", "version": 1}] and figure["data"]

    bindings = asyncio.run(app.host.call(sid, "splunk.query", {"kind": "bindings", "filters": {"tgs": ["TG1", "TG2"]}, "name": "legacy_bindings"}))
    assert app.variables.describe(sid, bindings.get("name") or bindings.get("variable_name"))["row_count"] == 2

    image = tmp_path / "图表.png"
    image.write_bytes(PNG)
    standard = host_module.Host.standard_chart({"image_path": str(image), "title": "已有程序输出"})
    assert standard["layout"]["images"][0]["source"].startswith("data:image/png;base64,")
    with pytest.raises(ValueError):
        host_module.Host.standard_chart({"summary": "共 3 个工站"})
    monkeypatch.setitem(app.settings.values["connectors"], "mos", {"driver": "python", "callable": "test_api:prose_only_mos"})
    with pytest.raises(ValueError, match="不能只返回"):
        asyncio.run(app.host.call(sid, "mos.query", {"question": "统计各工站过站数"}))


def prose_only_mos(request):
    return {"sql": "SELECT 1", "summary": "共查询到 128 行"}


def test_executor_factory_strips_credentials_and_enforces_timeout(tmp_path, demo_dir, monkeypatch):
    monkeypatch.setenv("LINE_ENGINEER_API_KEY", "dummy-model-key")
    monkeypatch.setenv("COMPANY_DB_PASSWORD", "dummy-db-password")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "settings.json").write_text(json.dumps({"executor": {"factory": "examples.adapters.isolated_executor:create"}}), encoding="utf-8")
    app, sid = bare_app(tmp_path, demo_dir)
    assert type(app.runner).__name__ == "IsolatedRunner"
    code = "import os\nctx.vars.save('env_names', sorted(os.environ), '执行环境变量名称')"
    result = asyncio.run(app.host.call(sid, "python.execute", {"code": code}))
    names = app.variables.load(sid, "env_names")
    assert result["outputs"][0]["name"] == "env_names"
    assert "LINE_ENGINEER_API_KEY" not in names and "COMPANY_DB_PASSWORD" not in names

    app.settings.values["execution_timeout"] = 1
    with pytest.raises(RuntimeError, match="时限"):
        asyncio.run(app.host.call(sid, "python.execute", {"code": "import time\ntime.sleep(10)\nctx.vars.save('late', 'x', '不应发布')"}))
    assert "late" not in {v["name"] for v in app.variables.list(sid)}, "超时不发布部分结果"


def test_shell_tool_is_registered_only_when_the_host_has_it(tmp_path, demo_dir, monkeypatch):
    available = host_module.detect_shells()
    monkeypatch.setattr(host_module, "detect_shells", lambda: {})
    app, sid = bare_app(tmp_path, demo_dir)
    assert "shell.execute" not in app.host.tools() and "python.execute" in app.host.tools()
    with pytest.raises(ValueError, match="未注册工具"):
        asyncio.run(app.host.call(sid, "shell.execute", {"shell": "bash", "script": "echo hi"}))
    if not available:
        pytest.skip("本机没有 PowerShell 或 Bash")
    scripts = {"powershell": "Write-Output '压装力复核'", "bash": "echo 压装力复核"}
    for shell, executable in available.items():
        app.host.shells = {shell: executable}
        assert "shell.execute" in {c["name"] for c in app.host.capabilities()}
        result = asyncio.run(app.host.call(sid, "shell.execute", {"shell": shell, "script": scripts[shell]}))
        assert "压装力复核" in result["stdout"] and result["exit_code"] == 0, shell
        other = next(name for name in scripts if name != shell)
        with pytest.raises(ValueError, match="未检测到"):
            asyncio.run(app.host.call(sid, "shell.execute", {"shell": other, "script": scripts[other]}))
    assert {"code", "text"} <= {v["kind"] for v in app.variables.list(sid)}


def test_windows_wsl_bash_launcher_is_not_registered(monkeypatch):
    import types
    monkeypatch.setattr(host_module, "os", types.SimpleNamespace(name="nt"))
    paths = {"bash": "C:/Windows/System32/bash.exe", "powershell": "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"}
    monkeypatch.setattr(host_module.shutil, "which", lambda name: paths.get(name))
    assert host_module.detect_shells() == {"powershell": paths["powershell"]}
    paths["bash"] = "C:/Program Files/Git/bin/bash.exe"
    assert host_module.detect_shells() == {"powershell": paths["powershell"], "bash": paths["bash"]}


def text_model(messages, options):
    assert "api_key" not in options and "headers" not in options
    return "根据要求返回：\n```json\n{\"type\": \"skill\", \"name\": \"data-analysis\"}\n```"


async def slow_model(messages, options):
    await asyncio.sleep(5)


def test_custom_model_driver_parses_text_instructions_and_times_out(tmp_path):
    app = Application(tmp_path / "data")
    app.settings.update({"model": {"driver": "python", "callable": "test_api:text_model", "api_key": "dummy-model-key", "timeout": 5}})
    assert asyncio.run(ModelClient(app.settings).complete([{"role": "user", "content": "统计"}])) == {"type": "skill", "name": "data-analysis"}
    app.settings.update({"model": {"callable": "test_api:slow_model", "timeout": 0.2}})
    with pytest.raises(RuntimeError, match="超时"):
        asyncio.run(ModelClient(app.settings).complete([{"role": "user", "content": "统计"}]))
    with pytest.raises(ValueError):
        app.settings.update({"model": {"driver": "unknown"}})


def test_preview_complete_for_small_tables_and_marked_unavailable_on_failure(tmp_path, demo_dir, monkeypatch):
    app, sid = bare_app(tmp_path, demo_dir)
    small = pd.DataFrame({"tg": [f"TG{i}" for i in range(25)], "result": ["OK", "NC"] * 12 + ["OK"]})
    app.variables.save(sid, "small", small, "小表")
    preview = app.variables.preview(sid, "small")
    assert preview["complete"] and len(preview["rows"]) == 25
    large = pd.DataFrame({"tg": [f"TG{i}" for i in range(5000)], "station": [f"OP{i % 3}0" for i in range(5000)]})
    app.variables.save(sid, "large", large, "大表")
    preview = app.variables.preview(sid, "large")
    assert not preview["complete"] and 30 <= len(preview["rows"]) <= 40 and preview["available"]

    def broken(*args):
        raise MemoryError("preview")
    monkeypatch.setattr(variables_module, "make_preview", broken)
    meta = app.variables.save(sid, "large", large.head(100), "重写后的大表")
    preview = app.variables.preview(sid, "large")
    assert meta["version"] == 2 and meta["preview_available"] is False
    assert preview["available"] is False and preview["source_version"] == 2 and not preview["rows"], "不能沿用旧版预览"
    assert "不可用" in app.db.events(sid)[-1]["message"]


def test_python_execute_imports_the_calling_skills_frozen_scripts(tmp_path, demo_dir):
    app, sid = bare_app(tmp_path, demo_dir)
    checks = pd.DataFrame({"tg": ["A", "A", "B", "C"], "result": ["NC", "OK", "OK", "NC"], "batch": ["X", "X", "Y", "Y"]})
    app.variables.save(sid, "checks", checks, "检测记录")
    code = ("from grouped_quality import grouped_quality\n"
            "ctx.vars.save('batch_quality', grouped_quality(ctx.vars.load('checks'), 'batch'), '按批次去重工件的不良率')")
    version = app.registry.snapshot("data-analysis").name
    asyncio.run(app.host.call(sid, "python.execute", {"code": code}, skill="data-analysis", version=version))
    quality = app.variables.load(sid, "batch_quality").set_index("batch")
    assert quality.loc["X", "unit_count"] == 1 and quality.loc["Y", "nc_rate"] == 0.5, "按工件去重计算不良率"
    with pytest.raises(RuntimeError, match="grouped_quality"):
        asyncio.run(app.host.call(sid, "python.execute", {"code": code}))


def test_event_stream_replays_after_last_event_id_and_isolates_sessions(tmp_path, demo_dir):
    import socket
    import threading
    import httpx
    import uvicorn

    app, first = bare_app(tmp_path, demo_dir)
    second = app.orchestrator.create_session()["id"]
    ids = [app.db.event(first, f"第 {i} 步进度")["id"] for i in range(5)]
    app.db.event(second, "其他会话的进度")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(app, static_dir=tmp_path / "no-static"), host="127.0.0.1", port=port,
                                           log_level="warning", timeout_graceful_shutdown=5))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    def updates(response):
        event_id = None
        for line in response.iter_lines():
            if line.startswith("id: "):
                event_id = int(line[4:])
            elif line.startswith("data: "):
                yield event_id, json.loads(line[6:])

    try:
        deadline = time.monotonic() + 60
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.1)
        assert server.started
        url = f"http://127.0.0.1:{port}/api/sessions/{first}/stream"
        with httpx.stream("GET", url, headers={"Last-Event-ID": str(ids[2])}, timeout=30) as response:
            assert response.headers["content-type"].startswith("text/event-stream")
            stream = updates(response)
            event_id, payload = next(stream)
            assert payload["session_id"] == first and payload["event_ids"] == ids[3:] and event_id == ids[-1], "只补发断点之后的事件"
            other = app.db.event(second, "其他会话的新进度")["id"]
            latest = app.db.event(first, "新的进度")["id"]
            event_id, payload = next((e, p) for e, p in stream if p["event_ids"])
            assert payload["event_ids"] == [latest] and event_id == latest and other not in payload["event_ids"], "按会话分发且不重复"
    finally:
        server.should_exit = True
        thread.join(timeout=20)


def test_events_and_messages_carry_the_conversation_turn(tmp_path, demo_dir):
    app, sid = bare_app(tmp_path, demo_dir)
    assert app.db.event(sid, "准备")["turn"] == 0
    app.db.message(sid, "user", "第一问")
    app.db.message(sid, "assistant", "回答")
    app.db.message(sid, "user", "追问")
    assert app.db.event(sid, "执行")["turn"] == 2
    assert [m["turn"] for m in app.db.messages(sid)] == [1, 1, 2]


def test_rules_are_configurable_per_work_computer(tmp_path):
    app = Application(tmp_path / "data")
    assert app.rules()["shift_start_hour"] == 7 and app.rules()["long_dwell_hours"] == 4
    app.settings.update({"rules": {"shift_start_hour": 8, "long_dwell_hours": 6}})
    reloaded = Application(tmp_path / "data")
    assert reloaded.rules()["shift_start_hour"] == 8 and reloaded.rules()["long_dwell_hours"] == 6
    assert reloaded.rules()["exit_station"] == "OP30", "未覆盖的规则保持 config/rules.json 默认值"


def test_development_skills_validate_business_skills_and_inspect_tools(tmp_path):
    validator = dev_script("author-line-skill", "validate_skill")
    assert validator.main(["--all", str(ROOT / "skills"), "--strict"]) == 0
    tools = validator.registered_tools(ROOT)
    skill = tmp_path / "skills" / "station-alarm-review"
    shutil.copytree(ROOT / ".cursor/skills/author-line-skill/templates", skill)
    (skill / "SKILL.md.template").rename(skill / "SKILL.md")
    errors, _ = validator.validate(skill, tools)
    assert any("未查证" in e for e in errors) and any("name" in e for e in errors)
    (skill / "tools.json").write_text('["vars.preview", "db.write"]', encoding="utf-8")
    errors, _ = validator.validate(skill, tools)
    assert "工具未在宿主注册：db.write" in errors

    (skill / "scripts").mkdir()
    (skill / "scripts" / "json.py").write_text("VALUE = 1\n", encoding="utf-8")
    errors, _ = validator.validate(skill, tools)
    assert any("重名" in e and "json.py" in e for e in errors)

    inspector = dev_script("migrate-agent-tool", "inspect_tool")
    report = inspector.inspect_source((ROOT / "examples/adapters/custom_model.py").read_text(encoding="utf-8"), "custom_model.py")
    assert [e["name"] for e in report["entry_points"]] == ["complete"]
    assert report["env_vars"] == ["COMPANY_MODEL_TOKEN"] and report["credential_hints"] == ["COMPANY_MODEL_TOKEN"]


def test_skill_acceptance_outputs_match_the_demo_investigation(workbench):
    client, app = workbench()
    sid = new_session(client)
    ask(client, sid, QUESTION)
    approve(client, sid, wait(client, sid, awaiting("investigation"), what="规律挖掘计划")["plan"])
    approve(client, sid, wait(client, sid, awaiting("verification"), what="深入验证计划")["plan"])
    d = wait(client, sid, status_is("completed"), what="调查结论")
    variables = {v["name"]: v for v in d["variables"]}
    for skill in ("problem-analysis", "data-analysis", "splunk"):
        for case in json.loads((ROOT / "skills" / skill / "acceptance.json").read_text(encoding="utf-8"))["cases"]:
            for output in case["expect"]["outputs"]:
                assert output["name"] in variables, f"{skill}:{output['name']}"
                assert variables[output["name"]]["kind"] == output["kind"], f"{skill}:{output['name']}"
                assert set(output.get("columns", [])) <= set(variables[output["name"]]["columns"]), f"{skill}:{output['name']}"
    conclusion = app.variables.load(sid, "investigation_conclusion")
    assert "已确认根因" not in conclusion.replace("尚未确认根因", "")


def test_offline_package_layout_and_checksums(tmp_path):
    if not (ROOT / "web" / "dist" / "index.html").is_file():
        pytest.skip("需要先构建前端 web/dist")
    spec = importlib.util.spec_from_file_location("build_offline", ROOT / "packaging" / "build_offline.py")
    packaging = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(packaging)
    stage = packaging.build(tmp_path / "离线 包", wheels=False, demo=False, archive=False)
    for name in ("install.cmd", "install.ps1", "start.cmd", "start.ps1", "verify.ps1", "main.py", "requirements.lock",
                 "web/dist/index.html", "docs/migration-guide.md", "docs/api-contract.md", "config/settings.example.json",
                 ".cursor/skills/author-line-skill/SKILL.md", "manifest.json", "SHA256SUMS.txt"):
        assert (stage / name).is_file(), name
    assert not list(stage.rglob("__pycache__")) and not (stage / "web" / "node_modules").exists()
    assert (stage / "install.ps1").read_bytes().startswith(b"\xef\xbb\xbf"), "Windows PowerShell 5.1 需要 BOM 才能正确读取中文"
    assert packaging.verify(stage) == []
    (stage / "skills" / "rewrite" / "SKILL.md").write_text("篡改", encoding="utf-8")
    (stage / "main.py").unlink()
    assert sorted(packaging.verify(stage)) == ["校验值不一致：skills/rewrite/SKILL.md", "缺少文件：main.py"]

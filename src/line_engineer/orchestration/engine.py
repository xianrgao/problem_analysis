"""Session orchestration: routing, plan approval, frozen execution, scheduling and recovery."""
from __future__ import annotations
import asyncio
import json
import re
from collections import deque
from ..storage.database import now, uid
from . import prompts
from .demo_engine import DemoEngine
from .plans import PlanDraft, fingerprint

DEFAULT_TITLE = "新的工程分析"
RESUMABLE = {"interrupted", "cancelled", "blocked", "failed"}
FIRST_PERSON = ("我们", "我")
TYPED_APPROVAL = re.compile(r"^\s*(ok|okay|好的?|可以|行|确认|同意|批准|没问题|开始(执行|分析)?|执行吧?)\s*[。.!！]*\s*$", re.I)


class Conflict(ValueError):
    """State conflict such as stale approval, busy session or changed skill version."""


class Scheduler:
    """At most N sessions execute at once; the rest wait in FIFO order."""

    def __init__(self, limit):
        self.limit = limit
        self.active: set[str] = set()
        self.waiting: deque = deque()

    def available(self):
        return len(self.active) < self.limit() and not self.waiting

    async def acquire(self, sid):
        if self.available():
            self.active.add(sid)
            return
        future = asyncio.get_running_loop().create_future()
        self.waiting.append((sid, future))
        try:
            await future
        except asyncio.CancelledError:
            if future.done() and not future.cancelled():
                self.release(sid)
            else:
                self.waiting = deque(item for item in self.waiting if item[1] is not future)
            raise

    def release(self, sid):
        self.active.discard(sid)
        while self.waiting and len(self.active) < self.limit():
            waiting_sid, future = self.waiting.popleft()
            if not future.done():
                self.active.add(waiting_sid)
                future.set_result(None)


def ordered(nodes):
    done, result = set(), []
    while len(result) < len(nodes):
        for node in nodes:
            if node["id"] not in done and set(node.get("depends_on", [])) <= done:
                done.add(node["id"])
                result.append(node)
    return result


def sentence(message, fallback):
    text = (message or "").strip()
    return fallback if not text or any(word in text for word in FIRST_PERSON) else text


def compact(value, limit=12000):
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…（已截断，完整内容保存在变量仓库）"


class Orchestrator:
    def __init__(self, app):
        self.app = app
        self.tasks: dict[str, asyncio.Task] = {}
        self.runs: dict[str, str] = {}
        self.background: set[asyncio.Task] = set()
        self.scheduler = Scheduler(lambda: int(app.settings.values.get("max_parallel_sessions", 2)))
        self.demo = DemoEngine(app)
        self.stopping = False

    # ---- session state -------------------------------------------------
    def create_session(self, title=None):
        stamp = now()
        session = {"id": uid("ses_"), "title": (title or DEFAULT_TITLE).strip()[:120] or DEFAULT_TITLE, "status": "idle",
                   "pinned": False, "archived": False, "mode": self.app.settings.values["mode"], "memory": {},
                   "created_at": stamp, "updated_at": stamp}
        return self.app.db.put("session", session)

    def patch_session(self, sid, changes):
        allowed = {k: v for k, v in changes.items() if k in ("title", "pinned", "archived")}
        if "title" in allowed:
            allowed["title"] = str(allowed["title"]).strip()[:120]
            if not allowed["title"]:
                raise ValueError("会话名称不能为空")
        return self.app.db.patch("session", sid, **allowed)

    def status(self, sid, status):
        return self.app.db.patch("session", sid, status=status)

    def say(self, sid, content):
        message = self.app.db.message(sid, "assistant", content)
        self.app.db.patch("session", sid)
        return message

    def remember(self, sid, **updates):
        session = self.app.db.get("session", sid)
        self.app.db.patch("session", sid, memory={**session.get("memory", {}), **updates})

    def busy(self, sid):
        task = self.tasks.get(sid)
        return task is not None and not task.done()

    def spawn(self, sid, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks[sid] = task

        def finished(done):
            if self.tasks.get(sid) is done:
                self.tasks.pop(sid, None)
        task.add_done_callback(finished)
        return task

    def settle(self, sid):
        plans = self.app.plans(sid)
        if plans and plans[-1]["status"] == "awaiting_approval":
            return self.status(sid, "awaiting_approval")
        runs = self.session_runs(sid)
        return self.status(sid, "completed" if runs and runs[-1]["status"] == "completed" else "idle")

    def session_runs(self, sid):
        return sorted((r for r in self.app.db.all("run") if r["session_id"] == sid), key=lambda r: r["created_at"])

    def detail(self, sid):
        app = self.app
        session = app.db.get("session", sid)
        runs = self.session_runs(sid)
        plans = []
        for plan in app.plans(sid):
            run = next((r for r in reversed(runs) if r.get("plan_id") == plan["id"] and r.get("revision") == plan["revision"]), None)
            nodes = [{**node, "status": run["state"][node["id"]]["status"]} if run else node for node in plan["nodes"]]
            plans.append({**plan, "nodes": nodes})
        current = plans[-1] if plans else None
        return {"session": session, "messages": app.db.messages(sid), "plan": current, "plans": plans, "events": app.db.events(sid),
                "variables": [app.variables.public(v) for v in app.variables.list(sid)], "runs": runs}

    # ---- conversation --------------------------------------------------
    async def post_message(self, sid, content):
        session = self.app.db.get("session", sid)
        content = (content or "").strip()
        if not content:
            raise ValueError("消息内容不能为空")
        mode = self.app.settings.values["mode"]
        if session["mode"] != mode:
            raise Conflict(f"该会话创建于{'模拟环境' if session['mode'] == 'demo' else '模型 API'}模式，当前设置为{'模拟环境' if mode == 'demo' else '模型 API'}；为避免模拟与真实结果混用，请新建会话。")
        message = self.app.db.message(sid, "user", content)
        if session["title"] == DEFAULT_TITLE:
            self.app.db.patch("session", sid, title=content[:40])
        pending = self.app.active_plan(sid)
        if self.busy(sid):
            task = asyncio.create_task(self._follow_up(sid, content))
            self.background.add(task)
            task.add_done_callback(self.background.discard)
        elif pending and pending["status"] == "awaiting_approval" and TYPED_APPROVAL.match(content):
            # Approval is bound to session + plan + revision via the plan card, never free text.
            self.say(sid, f"计划“{pending['title']}”第 {pending['revision']} 版待确认。为避免误批旧版本或其他会话的计划，"
                          "请在该计划卡片上点击“确认并开始分析”；如需调整范围，可直接说明调整内容。")
            self.settle(sid)
        else:
            self.status(sid, "planning")
            self.spawn(sid, self._route(sid, content))
        return message

    async def _route(self, sid, content):
        try:
            if self.app.db.get("session", sid)["mode"] == "demo":
                decision = self.demo.route(sid, content)
            else:
                decision = await self._live_route(sid, content)
            await self._apply(sid, decision, content)
        except asyncio.CancelledError:
            if not self.stopping:
                self.app.db.event(sid, "已按要求停止本轮请求的处理。", "run", "cancelled")
            self.settle(sid)
        except Exception as exc:
            self.app.db.event(sid, "请求未能形成可执行的分析步骤，已记录原因。", "error", "failed", {"error": str(exc)})
            self.say(sid, f"本轮请求未能完成：{exc}")
            self.settle(sid)

    async def _apply(self, sid, decision, content):
        kind = decision["type"]
        if decision.get("memory"):
            self.remember(sid, **decision["memory"])
        if kind in ("answer", "ask"):
            self.say(sid, decision["message"])
            self.settle(sid)
        elif kind == "plan":
            if decision.get("message"):
                self.say(sid, decision["message"])
            self.create_plan(sid, decision["plan"], decision.get("origin", "model"), scope=decision.get("scope"))
        elif kind == "revise":
            plan = decision["plan"]
            draft, scope = self.demo.revise(plan, decision["feedback"])
            revised = self.create_plan(sid, draft, plan["origin"], base=plan, feedback=decision["feedback"], scope=scope)
            self.say(sid, f"已根据调整意见生成第 {revised['revision']} 版计划，确认后按新范围执行。")
        elif kind == "direct":
            run = self._new_run(sid, nodes=decision["nodes"], kind="direct", request=content, origin=decision.get("origin", "model"))
            self._start(sid, run)
        elif kind == "issue":
            if decision.get("issue_id"):
                issue = self.app.update_issue(decision["issue_id"], decision.get("fields") or {}, session_id=sid)
                self.app.db.event(sid, f"已将本会话的证据与假设更新到问题 {issue['id']}，台账保留修改历史。", "issue", "completed", {"issue": issue})
            else:
                issue = self.app.create_issue(decision["title"], decision.get("description", ""), sid, station=decision.get("station", ""))
                if decision.get("fields"):
                    issue = self.app.update_issue(issue["id"], decision["fields"], session_id=sid)
                self.app.db.event(sid, f"已在问题台账登记 {issue['id']}，后续证据和措施可关联到该编号。", "issue", "completed", {"issue": issue})
            self.say(sid, decision["message"].format(issue_id=issue["id"]))
            self.settle(sid)
        else:
            raise ValueError(f"未定义的路由结果：{kind}")

    async def _follow_up(self, sid, content):
        run_id = self.runs.get(sid)
        try:
            run = self.app.db.get("run", run_id) if run_id else None
            if not run:
                self.say(sid, "上一条请求仍在整理中，完成后可继续追问或调整范围。")
                return
            if self.app.db.get("session", sid)["mode"] == "demo":
                decision = self.demo.follow_up(sid, content, run)
            else:
                decision = await self._live_follow_up(sid, content, run)
            if decision["type"] == "stop":
                self.cancel(sid)
                self.say(sid, "已停止当前任务；已完成步骤的数据、代码和图表均保留，可在分析侧栏查看。")
            elif decision["type"] == "revise" and run.get("kind") == "plan":
                self.app.db.patch("run", run_id, pause=decision["feedback"])
                self.app.db.event(sid, "已记录调查范围调整，当前步骤完成后暂停，并生成修订计划等待确认。", "run", "paused", {"feedback": decision["feedback"]}, run_id=run_id)
                self.say(sid, "已记录调整意见。当前步骤完成后暂停执行，并依据新的范围生成修订计划，确认后继续。")
            else:
                self.say(sid, decision.get("message") or self.progress_answer(run))
        except Exception as exc:
            self.say(sid, f"追问未能处理：{exc}")

    def progress_answer(self, run):
        nodes = run["nodes"] if run.get("kind") == "direct" else self.app.plan(run["plan_id"], run["revision"])["nodes"]
        done = [n["title"] for n in nodes if run["state"][n["id"]]["status"] == "completed"]
        current = [n["title"] for n in nodes if run["state"][n["id"]]["status"] == "running"]
        lines = [f"当前任务共 {len(nodes)} 个步骤，已完成 {len(done)} 个。"]
        if done:
            lines.append("已完成：" + "；".join(done) + "。")
        if current:
            lines.append("正在执行：" + "；".join(current) + "。尚未完成的步骤不提前给出发现。")
        return "\n\n".join(lines)

    # ---- plans ---------------------------------------------------------
    def create_plan(self, sid, draft, origin, base=None, feedback=None, scope=None):
        app = self.app
        validated = PlanDraft.model_validate(draft).model_dump()
        skills = {s["name"]: s for s in app.registry.discover()}
        for node in validated["nodes"]:
            if node["kind"] == "skill" and node["skill"] not in skills:
                raise ValueError(f"计划引用了未注册的技能：{node['skill']}")
            if node["kind"] == "tool" and node["tool"] not in app.host.tools():
                raise ValueError(f"计划引用了未注册的工具：{node['tool']}")
            node["progress_message"] = sentence(node["progress_message"], f"为{node['expected_output'] or node['title']}，正在{node['title']}。")
        for pending in app.plans(sid):
            if pending["status"] == "awaiting_approval":
                app.db.patch("plan", f"{pending['id']}@{pending['revision']}", status="superseded")
        if base and base["status"] != "superseded":
            app.db.patch("plan", f"{base['id']}@{base['revision']}", status="superseded")
        used = sorted({n["skill"] for n in validated["nodes"] if n["kind"] == "skill"})
        plan = {**validated, "id": base["id"] if base else uid("plan_"), "session_id": sid, "revision": base["revision"] + 1 if base else 1,
                "status": "awaiting_approval", "origin": origin, "feedback": feedback, "scope": scope,
                "skill_versions": {name: skills[name]["hash"] for name in used},
                "input_versions": [{"name": v["name"], "version": v["version"]} for v in app.variables.list(sid)], "created_at": now()}
        plan["fingerprint"] = fingerprint(plan)
        app.db.put("plan", plan, key=f"{plan['id']}@{plan['revision']}")
        self.remember(sid, active_plan={"id": plan["id"], "revision": plan["revision"], "title": plan["title"], "stage": plan["stage"]})
        self.status(sid, "awaiting_approval")
        return plan

    def _pending(self, sid, plan_id, revision):
        try:
            plan = self.app.plan(plan_id)
        except KeyError:
            raise Conflict("计划不存在") from None
        if plan["session_id"] != sid:
            raise Conflict("该计划不属于当前会话，不能在此确认")
        if plan["revision"] != int(revision):
            raise Conflict(f"计划已更新为第 {plan['revision']} 版，旧版本不能确认；请查看最新计划")
        if plan["status"] != "awaiting_approval":
            raise Conflict("该计划已处理或已被新的计划替代，不能再次确认")
        if fingerprint(plan) != plan["fingerprint"]:
            raise Conflict("计划内容校验不一致，请重新生成计划")
        return plan

    def approve(self, sid, plan_id, revision):
        if self.busy(sid):
            raise Conflict("当前会话仍有任务在处理，请等待完成或先停止")
        plan = self._pending(sid, plan_id, revision)
        try:
            for name, digest in plan["skill_versions"].items():
                self.app.registry.snapshot(name, digest)
        except ValueError as exc:
            raise Conflict(str(exc)) from None
        plan = self.app.db.patch("plan", f"{plan['id']}@{plan['revision']}", status="approved", approved_at=now())
        self.remember(sid, approved_plan={"id": plan["id"], "revision": plan["revision"], "title": plan["title"], "stage": plan["stage"],
                                          "fingerprint": plan["fingerprint"], "scope": plan.get("scope")})
        run = self._new_run(sid, plan=plan)
        self._start(sid, run)
        return run

    def revise(self, sid, plan_id, revision, feedback):
        if self.busy(sid):
            raise Conflict("当前会话仍有任务在处理，请稍后调整计划")
        feedback = (feedback or "").strip()
        if not feedback:
            raise ValueError("请说明需要调整的内容")
        plan = self._pending(sid, plan_id, revision)
        self.app.db.message(sid, "user", f"调整计划：{feedback}")
        self.status(sid, "planning")
        self.spawn(sid, self._revise(sid, plan, feedback))

    async def _revise(self, sid, plan, feedback):
        try:
            if plan["origin"] == "demo":
                draft, scope = self.demo.revise(plan, feedback)
            else:
                draft, scope = await self._live_revision(sid, plan, feedback), None
            revised = self.create_plan(sid, draft, plan["origin"], base=plan, feedback=feedback, scope=scope)
            self.say(sid, f"已根据调整意见生成第 {revised['revision']} 版计划，确认后按新范围执行。")
        except asyncio.CancelledError:
            self.settle(sid)
        except Exception as exc:
            self.say(sid, f"计划调整未完成，原计划仍待确认：{exc}")
            self.settle(sid)

    # ---- runs ----------------------------------------------------------
    def _new_run(self, sid, plan=None, nodes=None, kind="plan", request=None, origin=None):
        nodes = plan["nodes"] if plan else nodes
        stamp = now()
        run = {"id": uid("run_"), "session_id": sid, "kind": kind, "plan_id": plan["id"] if plan else None,
               "revision": plan["revision"] if plan else None, "fingerprint": plan["fingerprint"] if plan else None,
               "origin": plan["origin"] if plan else origin, "nodes": None if plan else nodes, "request": request,
               "state": {n["id"]: {"status": "pending"} for n in nodes}, "status": "queued", "pause": None,
               "created_at": stamp, "updated_at": stamp}
        return self.app.db.put("run", run)

    def _start(self, sid, run):
        self.spawn(sid, self._execute(sid, run["id"]))

    def _node_state(self, run_id, node_id, **state):
        run = self.app.db.get("run", run_id)
        run["state"][node_id] = {**run["state"][node_id], **state}
        return self.app.db.patch("run", run_id, state=run["state"])

    def _plan_status(self, run, status):
        if run.get("plan_id"):
            self.app.db.patch("plan", f"{run['plan_id']}@{run['revision']}", status=status)

    async def _execute(self, sid, run_id):
        app, acquired, node = self.app, False, None
        self.runs[sid] = run_id
        try:
            if not self.scheduler.available():
                app.db.patch("run", run_id, status="queued")
                self.status(sid, "queued")
                app.db.event(sid, "其他会话正在占用并行执行名额，本任务已排队，名额释放后自动开始。", "run", "queued", run_id=run_id)
            await self.scheduler.acquire(sid)
            acquired = True
            run = app.db.patch("run", run_id, status="running", started_at=now())
            plan = app.plan(run["plan_id"], run["revision"]) if run.get("plan_id") else None
            if plan and fingerprint(plan) != run["fingerprint"]:
                raise RuntimeError("已批准计划的内容与冻结版本不一致，执行已停止")
            nodes = plan["nodes"] if plan else run["nodes"]
            self._plan_status(run, "running")
            self.status(sid, "running")
            if plan:
                app.db.event(sid, f"计划第 {plan['revision']} 版已确认并冻结，按批准范围依次执行 {len(nodes)} 个步骤。", "run", "running", {"plan_id": plan["id"], "revision": plan["revision"], "fingerprint": plan["fingerprint"]}, run_id=run_id)
            proposed = None
            for node in ordered(nodes):
                run = app.db.get("run", run_id)
                if run["state"][node["id"]]["status"] == "completed":
                    continue
                if run.get("pause"):
                    node = None
                    await self._pause(sid, run, plan)
                    return
                self._node_state(run_id, node["id"], status="running", started_at=now())
                app.db.event(sid, node["progress_message"], "step", "running", {"title": node["title"], **({"skill": node["skill"]} if node.get("skill") else {"tool": node.get("tool")})}, run_id=run_id, node_id=node["id"])
                result = await self._node(sid, run, plan, node)
                if result.get("ask"):
                    self._node_state(run_id, node["id"], status="blocked", message=result["ask"])
                    app.db.event(sid, "本步需要补充必要信息后才能继续，已暂停执行。", "step", "blocked", {"question": result["ask"]}, run_id=run_id, node_id=node["id"])
                    app.db.patch("run", run_id, status="blocked")
                    self._plan_status(run, "blocked")
                    self.say(sid, result["ask"])
                    self.status(sid, "blocked")
                    node = None
                    return
                proposed = result.get("plan") or proposed
                self._node_state(run_id, node["id"], status="completed", outputs=result.get("outputs", []), message=result["message"], finished_at=now())
                app.db.event(sid, result["message"], "step", "completed", {"outputs": result.get("outputs", [])}, run_id=run_id, node_id=node["id"])
            node = None
            run = app.db.patch("run", run_id, status="completed", finished_at=now())
            self._plan_status(run, "completed")
            await self._review(sid, run, plan, proposed)
        except asyncio.CancelledError:
            if self.stopping:
                # Application shutdown is not a user stop: keep the checkpoint resumable.
                if node:
                    self._node_state(run_id, node["id"], status="interrupted")
                    app.db.event(sid, "本步因应用关闭未完成，未发布部分结果。", "step", "interrupted", run_id=run_id, node_id=node["id"])
                run = app.db.patch("run", run_id, status="interrupted")
                self._plan_status(run, "interrupted")
                app.db.event(sid, "应用关闭时任务尚未完成，已在最近检查点暂停；重新启动后从检查点继续，不重跑已完成步骤。", "run", "interrupted", run_id=run_id)
                self.status(sid, "interrupted")
                return
            if node:
                self._node_state(run_id, node["id"], status="cancelled")
                app.db.event(sid, "当前步骤已停止，未完成的查询或计算不会发布结果。", "step", "cancelled", run_id=run_id, node_id=node["id"])
            run = app.db.patch("run", run_id, status="cancelled")
            self._plan_status(run, "cancelled")
            app.db.event(sid, "任务已按要求停止，已完成步骤的结果和产物均已保留。", "run", "cancelled", run_id=run_id)
            self.status(sid, "cancelled")
        except Exception as exc:
            reason = str(exc).strip().splitlines()[0][:300] if str(exc).strip() else type(exc).__name__
            if node:
                self._node_state(run_id, node["id"], status="failed", error=str(exc))
                app.db.event(sid, f"本步受阻：{reason}", "step", "failed", {"error": str(exc)}, run_id=run_id, node_id=node["id"])
            run = app.db.patch("run", run_id, status="blocked")
            self._plan_status(run, "blocked")
            self.say(sid, f"执行受阻：{reason}\n\n已完成步骤的结果已保留，可修正条件后恢复执行。")
            self.status(sid, "blocked")
        finally:
            if self.runs.get(sid) == run_id:
                self.runs.pop(sid, None)
            if acquired:
                self.scheduler.release(sid)

    async def _node(self, sid, run, plan, node):
        if run["origin"] == "demo":
            return await self.demo.execute(sid, run, plan, node)
        if node["kind"] == "tool":
            result = await self.app.host.call(sid, node["tool"], node.get("params", {}), run=run["id"], node=node["id"])
            name = result.get("variable_name") or result.get("name") if isinstance(result, dict) else None
            rows = f"，共 {result['row_count']:,} 行" if isinstance(result, dict) and result.get("row_count") is not None else ""
            return {"message": f"{node['title']}已完成，结果保存为 {name}{rows}。" if name else f"{node['title']}已完成。", "outputs": [name] if name else []}
        version = plan["skill_versions"].get(node["skill"]) if plan else None
        upstream = {} if not plan else {dep: {k: run["state"][dep].get(k) for k in ("message", "outputs")} for dep in node.get("depends_on", [])}
        task = {"request": run.get("request"), "node": {k: node.get(k) for k in ("id", "title", "description", "expected_output", "params")},
                "upstream": upstream, "plan": {"title": plan["title"], "summary": plan["summary"]} if plan else None}
        return await self.skill_loop(sid, node["skill"], version, task, run["id"], node["id"], allow_plan=plan is None)

    async def skill_loop(self, sid, skill, version, task, run_id, node_id, allow_plan):
        app = self.app
        metadata = next((s for s in app.registry.discover() if s["name"] == skill), None)
        if not metadata:
            raise ValueError(f"未知技能：{skill}")
        version = version or metadata["hash"]
        body = app.registry.read(skill, "SKILL.md", version)
        app.db.event(sid, f"已加载“{metadata['title']}”技能正文，参考资料按需读取。", "skill", "completed", {"skill": skill, "version": metadata["version"], "hash": version[:12]}, run_id=run_id, node_id=node_id)
        messages = [{"role": "system", "content": prompts.SYSTEM + "\n" + prompts.SKILL},
                    {"role": "system", "content": f"当前技能：{skill}\n声明工具：{', '.join(metadata['tools'])}\n\n{body}"},
                    {"role": "user", "content": compact({"task": task, "context": self.model_context(sid)}, 24000)}]
        failures = 0
        for _ in range(int(app.settings.values.get("max_skill_turns", 24))):
            action = await app.model.complete(messages)
            messages.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
            kind = action.get("type")
            if kind == "finish":
                known = {v["name"] for v in app.variables.list(sid)}
                return {"message": sentence(action.get("message"), "本步已完成。"), "outputs": [o for o in action.get("outputs", []) if o in known]}
            if kind == "ask":
                return {"ask": action.get("message") or "需要补充信息后继续。"}
            if kind == "plan":
                if not allow_plan:
                    messages.append({"role": "user", "content": "已批准计划内不能新增技能级计划；请在本节点范围内完成并返回 finish，必要时在 message 中说明需要后续计划。"})
                    continue
                PlanDraft.model_validate(action["plan"])
                return {"message": "已形成分阶段调查计划，等待确认后执行。", "outputs": [], "plan": action["plan"]}
            if kind != "tool":
                failures += 1
                messages.append({"role": "user", "content": "返回的动作类型无效，只能是 tool、plan、finish 或 ask。"})
                continue
            tool, args = action.get("tool"), action.get("arguments") or {}
            if tool not in metadata["tools"]:
                failures += 1
                messages.append({"role": "user", "content": f"工具 {tool} 未在本技能声明，可用工具：{metadata['tools']}"})
            else:
                shown = {k: (v if len(json.dumps(v, ensure_ascii=False, default=str)) < 4000 else "（内容较长，已保存在执行记录中）") for k, v in args.items()}
                app.db.event(sid, sentence(action.get("message"), f"正在调用 {tool} 完成本步所需的数据处理。"), "tool", "running", {"tool": tool, "args": shown}, run_id=run_id, node_id=node_id)
                try:
                    result = await app.host.call(sid, tool, args, skill, version, run_id, node_id)
                    messages.append({"role": "user", "content": "工具结果：" + compact(result)})
                    continue
                except Exception as exc:
                    failures += 1
                    app.db.event(sid, "工具执行未成功，错误已返回用于在批准范围内修正。", "error", "failed", {"tool": tool, "error": str(exc)[-6000:]}, run_id=run_id, node_id=node_id)
                    messages.append({"role": "user", "content": "工具执行失败：" + str(exc)[-6000:]})
            if failures > 2:
                raise RuntimeError("自动修复已达到两次上限，保留现有结果并停止本步")
        raise RuntimeError("技能执行轮次超过配置上限")

    async def _pause(self, sid, run, plan):
        self.app.db.patch("run", run["id"], status="paused")
        self._plan_status(run, "paused")
        self.app.db.event(sid, "当前步骤已完成，执行在检查点暂停，正在依据新的调查范围生成修订计划。", "run", "paused", {"feedback": run["pause"]}, run_id=run["id"])
        try:
            if plan["origin"] == "demo":
                draft, scope = self.demo.revise(plan, run["pause"])
            else:
                draft, scope = await self._live_revision(sid, plan, run["pause"]), None
            revised = self.create_plan(sid, draft, plan["origin"], base=plan, feedback=run["pause"], scope=scope)
            self.say(sid, f"已生成第 {revised['revision']} 版修订计划。已完成步骤的结果保留，确认后按新范围执行。")
        except Exception as exc:
            self.say(sid, f"修订计划未能生成：{exc}")
            self.status(sid, "paused")

    async def _review(self, sid, run, plan, proposed):
        if run["origin"] == "demo":
            content, next_plan, scope = self.demo.review(sid, run, plan)
        elif plan is None:
            content, next_plan, scope = (None if proposed else run["state"][run["nodes"][-1]["id"]].get("message")), proposed, None
        else:
            (content, next_plan), scope = await self._live_review(sid, run, plan), None
        if content:
            self.say(sid, content)
        if next_plan:
            self.create_plan(sid, next_plan, run["origin"], scope=scope)
        else:
            self.settle(sid)

    def cancel(self, sid):
        task = self.tasks.get(sid)
        if not task or task.done():
            raise Conflict("当前会话没有正在执行的任务")
        self.app.runner.kill(sid)
        task.cancel()

    def resume(self, sid):
        if self.busy(sid):
            raise Conflict("当前会话已有任务在执行")
        runs = self.session_runs(sid)
        if not runs or runs[-1]["status"] not in RESUMABLE:
            raise Conflict("没有可以恢复的任务")
        run = runs[-1]
        if run.get("plan_id"):
            latest = self.app.plan(run["plan_id"])
            if latest["revision"] != run["revision"]:
                raise Conflict("该计划已有修订版本，请确认最新计划")
        state = {k: (v if v["status"] == "completed" else {"status": "pending"}) for k, v in run["state"].items()}
        run = self.app.db.patch("run", run["id"], state=state, status="queued", resumed_at=now())
        self.app.db.event(sid, "从最近检查点恢复执行，已完成的步骤不会重新运行。", "run", "running", run_id=run["id"])
        self._start(sid, run)
        return run

    async def shutdown(self):
        """Stop for application exit; running work is checkpointed as interrupted, not user-cancelled."""
        self.stopping = True
        tasks = [task for task in [*self.tasks.values(), *self.background] if not task.done()]
        for sid in list(self.tasks):
            self.app.runner.kill(sid)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def recover(self):
        """After restart: interrupt unfinished runs at their checkpoint and resume them
        (setting resume_on_restart); tasks the user stopped stay stopped."""
        app = self.app
        self.stopping = False
        interrupted = set()
        for run in app.db.all("run"):
            if run["status"] in ("running", "queued"):
                state = {k: ({**v, "status": "interrupted"} if v["status"] == "running" else v) for k, v in run["state"].items()}
                app.db.patch("run", run["id"], status="interrupted", state=state)
                for node_id in (k for k, v in run["state"].items() if v["status"] == "running"):
                    app.db.event(run["session_id"], "本步因应用重启未完成，未发布部分结果。", "step", "interrupted", run_id=run["id"], node_id=node_id)
                self._plan_status(run, "interrupted")
                app.db.event(run["session_id"], "应用重启时任务尚未完成，已在最近检查点暂停；恢复后不会重跑已完成步骤。", "run", "interrupted", run_id=run["id"])
                app.db.patch("session", run["session_id"], status="interrupted")
                interrupted.add(run["session_id"])
        for session in app.db.all("session"):
            if session["id"] not in interrupted and session["status"] in ("planning", "running", "queued"):
                self.settle(session["id"])
        if not app.settings.values.get("resume_on_restart", True):
            return []
        resumed = []
        for session in app.db.all("session"):
            runs = self.session_runs(session["id"])
            if session["status"] == "interrupted" and runs and runs[-1]["status"] == "interrupted":
                try:
                    self.resume(session["id"])
                    resumed.append(session["id"])
                except Conflict as exc:
                    app.db.event(session["id"], f"重启后未自动恢复：{exc}", "run", "interrupted")
        return resumed

    # ---- live model ----------------------------------------------------
    def model_context(self, sid):
        app = self.app
        context = app.host.context(sid)
        skills = [{k: s[k] for k in ("name", "title", "description", "tools", "references", "scripts")} for s in context["skills"]]
        plan = context["active_plan"]
        return {"skills": skills, "host_capabilities": context["host_capabilities"],
                "active_plan": {k: plan.get(k) for k in ("id", "revision", "title", "summary", "stage", "status", "nodes")} if plan else None,
                "variable_index": [{k: v.get(k) for k in ("name", "kind", "version", "description", "row_count", "columns")} for v in context["variable_index"]],
                "memory": app.db.get("session", sid).get("memory", {}),
                "recent_messages": [{"role": m["role"], "content": m["content"][:2000]} for m in app.db.messages(sid)[-10:]]}

    async def _live_route(self, sid, content):
        messages = [{"role": "system", "content": prompts.SYSTEM + "\n" + prompts.ROUTING},
                    {"role": "user", "content": compact({"context": self.model_context(sid), "request": content}, 30000)}]
        action = await self.app.model.complete(messages)
        kind = action.get("type")
        if kind == "skill":
            name = "problem-analysis" if action.get("category") == "problem_analysis" else action.get("name")
            metadata = next((s for s in self.app.registry.discover() if s["name"] == name), None)
            if not metadata:
                raise ValueError(f"模型选择了未注册的技能：{name}")
            node = {"id": "direct", "kind": "skill", "skill": name, "title": metadata["title"], "description": content,
                    "params": action.get("arguments") or {}, "depends_on": [],
                    "progress_message": sentence(action.get("message"), f"为回应当前请求，正在按“{metadata['title']}”技能整理范围并执行所需步骤。")}
            return {"type": "direct", "nodes": [node], "origin": "model"}
        if kind == "plan":
            return {"type": "plan", "plan": action["plan"], "origin": "model"}
        if kind in ("answer", "ask"):
            return {"type": kind, "message": action.get("message") or "请补充更具体的问题范围。"}
        raise ValueError("模型返回了未定义的指令类型")

    async def _live_follow_up(self, sid, content, run):
        prompt = ("当前会话有正在执行的已批准任务。依据追问返回JSON："
                  '{"type":"answer","message":"依据已提交证据的答复"}、{"type":"revise","feedback":"范围调整说明"} 或 {"type":"stop"}。'
                  "未完成的步骤不能给出发现。")
        state = {"progress": self.progress_answer(run), "state": run["state"]}
        action = await self.app.model.complete([{"role": "system", "content": prompts.SYSTEM + "\n" + prompt},
                                                {"role": "user", "content": compact({"run": state, "context": self.model_context(sid), "request": content}, 24000)}])
        return action if action.get("type") in ("answer", "revise", "stop") else {"type": "answer", "message": self.progress_answer(run)}

    async def _live_revision(self, sid, plan, feedback):
        prompt = '依据用户调整意见修订计划，只返回 {"type":"plan","plan":{...}}，保持未被要求修改的步骤。'
        action = await self.app.model.complete([{"role": "system", "content": prompts.SYSTEM + "\n" + prompts.ROUTING + "\n" + prompt},
                                                {"role": "user", "content": compact({"current_plan": {k: plan[k] for k in ("title", "summary", "stage", "nodes")}, "feedback": feedback, "context": self.model_context(sid)}, 30000)}])
        if action.get("type") != "plan":
            raise ValueError("模型没有返回修订计划")
        return action["plan"]

    async def _live_review(self, sid, run, plan):
        prompt = ('阶段已完成。检查原问题是否已回答，返回 {"message":"Markdown 摘要：范围、关键事实、证据变量引用、线索与已验证原因的区分、缺口","plan":可选的下一阶段计划}。'
                  "不能把步骤完成当作根因确认，有缺口时给出补充计划或明确证据不足。")
        results = {node["id"]: {"title": node["title"], **{k: run["state"][node["id"]].get(k) for k in ("message", "outputs")}} for node in plan["nodes"]}
        try:
            action = await self.app.model.complete([{"role": "system", "content": prompts.SYSTEM + "\n" + prompt},
                                                    {"role": "user", "content": compact({"plan": {k: plan[k] for k in ("title", "summary", "stage")}, "results": results, "context": self.model_context(sid)}, 30000)}])
            next_plan = action.get("plan") if isinstance(action.get("plan"), dict) and action["plan"].get("nodes") else None
            return action.get("message") or "本阶段已完成。", next_plan
        except Exception as exc:
            lines = [f"- {r['title']}：{r.get('message') or '已完成'}" for r in results.values()]
            return "本阶段步骤已执行完毕，阶段复核未完成（" + str(exc) + "）。各步骤结果：\n\n" + "\n".join(lines), None

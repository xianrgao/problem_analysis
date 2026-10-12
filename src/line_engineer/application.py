"""Application container: wires storage, skills, connectors, execution and orchestration."""
from __future__ import annotations
import io
import re
from pathlib import Path
import pandas as pd
from .config import Settings
from .connectors.model import ModelClient
from .domain.demo import generate_demo
from .execution.runner import ProcessRunner
from .orchestration.engine import Orchestrator
from .orchestration.host import Host
from .skills_runtime.registry import SkillRegistry
from .storage.database import Database, now, uid
from .storage.variables import VariableStore

ISSUE_STATES = ("open", "investigating", "acting", "verifying", "closed")
ISSUE_FIELDS = ("title", "description", "station", "owner", "due_date", "status", "hypotheses", "evidence", "actions", "verification")


class Application:
    def __init__(self, data_dir: Path | None = None, demo_days: int = 90):
        self.settings = Settings(data_dir)
        data = self.settings.data_dir
        self.db = Database(data / "app.sqlite")
        self.variables = VariableStore(self.db, data / "variables")
        self.registry = SkillRegistry(self.settings.root / "skills", data / "skill_snapshots")
        self.model = ModelClient(self.settings)
        self.runner = ProcessRunner(self.settings, self.variables)
        self.host = Host(self)
        self.demo_dir = data / "demo"
        self.demo_days = demo_days
        self.manifest: dict | None = None
        self.orchestrator = Orchestrator(self)

    def prepare_demo(self):
        self.manifest = generate_demo(self.demo_dir, days=self.demo_days)
        return self.manifest

    def plans(self, sid):
        plans = [p for p in self.db.all("plan") if p["session_id"] == sid]
        return sorted(plans, key=lambda p: (p["created_at"], p["revision"]))

    def plan(self, plan_id, revision=None):
        revisions = [p for p in self.db.all("plan") if p["id"] == plan_id]
        if revision is not None:
            revisions = [p for p in revisions if p["revision"] == int(revision)]
        if not revisions:
            raise KeyError(f"计划不存在: {plan_id}")
        return max(revisions, key=lambda p: p["revision"])

    def active_plan(self, sid):
        plans = self.plans(sid)
        return plans[-1] if plans else None

    def execution_inputs(self, sid, run=None):
        return [{"name": v["name"], "version": v["version"], "kind": v["kind"], "path": v["path"]} for v in self.variables.list(sid)]

    def issues(self):
        return sorted(self.db.all("issue"), key=lambda i: i["updated_at"], reverse=True)

    def create_issue(self, title, description="", session_id=None, owner="", due_date="", station=""):
        if not str(title).strip():
            raise ValueError("问题标题不能为空")
        stamp = now()
        issue = {"id": uid("ISS-"), "title": title.strip(), "description": description or "", "station": station or "", "status": "open",
                 "owner": owner or "", "due_date": due_date or "", "session_ids": [session_id] if session_id else [], "hypotheses": [],
                 "evidence": [], "actions": [], "verification": "", "history": [{"at": stamp, "action": "created", "status": "open"}],
                 "created_at": stamp, "updated_at": stamp}
        return self.db.put("issue", issue)

    def update_issue(self, issue_id, fields, confirm_close=False, session_id=None):
        issue = self.db.get("issue", issue_id)
        updates = {k: v for k, v in fields.items() if k in ISSUE_FIELDS}
        status = updates.get("status", issue["status"])
        if status not in ISSUE_STATES:
            raise ValueError(f"问题状态无效：{status}")
        if status == "closed" and issue["status"] != "closed" and not confirm_close:
            raise ValueError("关闭问题需用户在问题台账中确认")
        changes = {k: {"from": issue.get(k), "to": v} for k, v in updates.items() if issue.get(k) != v}
        if session_id and session_id not in issue["session_ids"]:
            updates["session_ids"] = [*issue["session_ids"], session_id]
            changes["session_ids"] = {"added": session_id}
        if not changes:
            return issue
        action = "reopened" if issue["status"] == "closed" and status != "closed" else "closed" if status == "closed" and issue["status"] != "closed" else "updated"
        history = [*issue.get("history", []), {"at": now(), "action": action, "changes": changes}]
        return self.db.patch("issue", issue_id, **updates, history=history)

    def snapshot(self, target_sid, source_sid, name, version):
        if target_sid == source_sid:
            raise ValueError("快照来源与目标为同一会话")
        self.db.get("session", source_sid)
        meta = self.variables.describe(source_sid, name, version)
        value = self.variables.load(source_sid, name, meta["version"])
        origin = {"session_id": source_sid, "name": name, "version": meta["version"]}
        saved = self.variables.save(target_sid, name, value, f"{meta['description']}（来自其他会话的不可变快照）", meta["kind"],
                                    {**meta.get("metadata", {}), "snapshot_of": origin}, "snapshot", [origin])
        return self.variables.public(saved)

    def import_table(self, sid, filename, content: bytes):
        suffix = Path(filename or "").suffix.lower()
        if suffix == ".csv":
            try:
                frame = pd.read_csv(io.BytesIO(content), encoding="utf-8-sig")
            except UnicodeDecodeError:
                frame = pd.read_csv(io.BytesIO(content), encoding="gb18030")
        elif suffix in (".xlsx", ".xls"):
            frame = pd.read_excel(io.BytesIO(content))
        else:
            raise ValueError("仅支持导入 CSV 或 Excel 文件")
        stem = re.sub(r"[^A-Za-z0-9_]+", "_", Path(filename).stem).strip("_")
        name = f"import_{stem}" if stem else uid("import_")
        saved = self.variables.save(sid, name[:100], frame, f"用户导入的文件 {Path(filename).name}，共 {len(frame):,} 行", "table",
                                    {"source": "import", "filename": Path(filename).name}, "import")
        return self.variables.public(saved)

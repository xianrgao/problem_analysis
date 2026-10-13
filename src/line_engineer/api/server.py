from __future__ import annotations
import asyncio
import io
import json
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote
import pandas as pd
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from .. import __version__
from ..application import Application
from ..orchestration.engine import Conflict


class SessionInput(BaseModel):
    title: str | None = None


class MessageInput(BaseModel):
    content: str


class ApprovalInput(BaseModel):
    revision: int


class RevisionInput(BaseModel):
    revision: int
    feedback: str


class SnapshotInput(BaseModel):
    source_session_id: str
    name: str
    version: int


class IssueInput(BaseModel):
    title: str
    description: str = ""
    session_id: str | None = None
    owner: str = ""
    due_date: str = ""
    station: str = ""


def create_app(application: Application | None = None, static_dir: Path | None = None) -> FastAPI:
    app_state = application or Application()

    @asynccontextmanager
    async def lifespan(_):
        await asyncio.to_thread(app_state.prepare_demo)
        app_state.orchestrator.recover()
        yield
        await app_state.orchestrator.shutdown()

    api = FastAPI(title="LINEA 产线硅基工程师", version=__version__, lifespan=lifespan)
    api.state.app = app_state
    orchestrator = app_state.orchestrator

    @api.exception_handler(Conflict)
    async def conflict(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @api.exception_handler(KeyError)
    async def missing(_, exc):
        return JSONResponse({"detail": str(exc.args[0]) if exc.args else "对象不存在"}, status_code=404)

    @api.exception_handler(ValueError)
    async def invalid(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    def session(sid):
        return app_state.db.get("session", sid)

    @api.get("/api/health")
    def health():
        manifest = app_state.manifest or {}
        return {"status": "ok", "version": __version__, "mode": app_state.settings.values["mode"],
                "demo": {k: manifest.get(k) for k in ("start", "end", "days", "table_counts")} if manifest else None,
                "running": len(orchestrator.scheduler.active), "queued": len(orchestrator.scheduler.waiting)}

    @api.get("/api/sessions")
    def sessions():
        return sorted(app_state.db.all("session"), key=lambda s: s["updated_at"], reverse=True)

    @api.post("/api/sessions")
    def create_session(body: SessionInput | None = None):
        return orchestrator.create_session(body.title if body else None)

    @api.get("/api/sessions/{sid}")
    def detail(sid: str):
        return orchestrator.detail(sid)

    @api.patch("/api/sessions/{sid}")
    def patch_session(sid: str, body: dict):
        session(sid)
        return orchestrator.patch_session(sid, body)

    @api.post("/api/sessions/{sid}/messages")
    async def message(sid: str, body: MessageInput):
        return await orchestrator.post_message(sid, body.content)

    @api.post("/api/sessions/{sid}/plans/{plan_id}/approve")
    async def approve(sid: str, plan_id: str, body: ApprovalInput):
        session(sid)
        run = orchestrator.approve(sid, plan_id, body.revision)
        return {"run_id": run["id"], "status": run["status"]}

    @api.post("/api/sessions/{sid}/plans/{plan_id}/revise")
    async def revise(sid: str, plan_id: str, body: RevisionInput):
        session(sid)
        orchestrator.revise(sid, plan_id, body.revision, body.feedback)
        return {"status": "planning"}

    @api.post("/api/sessions/{sid}/cancel")
    async def cancel(sid: str):
        session(sid)
        orchestrator.cancel(sid)
        return {"status": "cancelling"}

    @api.post("/api/sessions/{sid}/resume")
    async def resume(sid: str):
        session(sid)
        run = orchestrator.resume(sid)
        return {"run_id": run["id"], "status": run["status"]}

    @api.get("/api/sessions/{sid}/stream")
    async def stream(sid: str, request: Request):
        session(sid)
        header = request.headers.get("last-event-id", "0")
        last = int(header) if header.isdigit() else 0

        async def events():
            nonlocal last
            stamp, idle = None, 0
            yield "retry: 3000\n\n"
            while not await request.is_disconnected():
                current = session(sid)["updated_at"]
                fresh = app_state.db.events(sid, after=last)
                if fresh or current != stamp:
                    if fresh:
                        last = fresh[-1]["id"]
                    stamp, idle = current, 0
                    payload = json.dumps({"session_id": sid, "status": session(sid)["status"], "event_ids": [e["id"] for e in fresh]}, ensure_ascii=False)
                    yield f"id: {last}\nevent: update\ndata: {payload}\n\n"
                else:
                    idle += 1
                    if idle % 30 == 0:
                        yield ": keep-alive\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @api.get("/api/sessions/{sid}/variables/{name}/table")
    def table(sid: str, name: str, offset: int = 0, limit: int = 40, search: str = "", sort: str = "", direction: str = "asc", version: int | None = None):
        return app_state.variables.table(sid, name, version, max(0, offset), max(1, min(limit, 500)), sort or None, direction, search)

    @api.get("/api/sessions/{sid}/variables/{name}/preview")
    def preview(sid: str, name: str, version: int | None = None):
        return app_state.variables.preview(sid, name, version)

    @api.get("/api/sessions/{sid}/variables/{name}/value")
    def value(sid: str, name: str, version: int | None = None):
        meta = app_state.variables.describe(sid, name, version)
        if meta["kind"] == "table":
            return app_state.variables.preview(sid, name, meta["version"])
        return {"name": name, "version": meta["version"], "kind": meta["kind"], "value": app_state.variables.load(sid, name, meta["version"])}

    @api.get("/api/sessions/{sid}/variables/{name}/export")
    def export(sid: str, name: str, format: str = "csv", version: int | None = None):
        meta = app_state.variables.describe(sid, name, version)
        value = app_state.variables.load(sid, name, meta["version"])
        stem = f"{name}_v{meta['version']}"
        if meta["kind"] == "table":
            if format == "xlsx":
                buffer = io.BytesIO()
                value.to_excel(buffer, index=False)
                content, media, suffix = buffer.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
            else:
                content, media, suffix = value.to_csv(index=False).encode("utf-8-sig"), "text/csv; charset=utf-8", "csv"
        elif meta["kind"] == "report":
            content, media, suffix = value.encode("utf-8"), "text/html; charset=utf-8", "html"
        elif isinstance(value, str):
            content, media, suffix = value.encode("utf-8"), "text/plain; charset=utf-8", "sql" if meta["kind"] == "sql" else "py" if meta["kind"] == "code" else "txt"
        else:
            content, media, suffix = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"), "application/json", "json"
        return Response(content, media_type=media, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(stem + '.' + suffix)}"})

    @api.post("/api/sessions/{sid}/import")
    async def import_file(sid: str, file: UploadFile = File(...)):
        session(sid)
        content = await file.read()
        return await asyncio.to_thread(app_state.import_table, sid, file.filename or "", content)

    @api.post("/api/sessions/{sid}/snapshot")
    def snapshot(sid: str, body: SnapshotInput):
        session(sid)
        return app_state.snapshot(sid, body.source_session_id, body.name, body.version)

    @api.get("/api/issues")
    def issues():
        return app_state.issues()

    @api.post("/api/issues")
    def create_issue(body: IssueInput):
        if body.session_id:
            session(body.session_id)
        return app_state.create_issue(body.title, body.description, body.session_id, body.owner, body.due_date, body.station)

    @api.patch("/api/issues/{issue_id}")
    def patch_issue(issue_id: str, body: dict):
        return app_state.update_issue(issue_id, body, confirm_close=bool(body.get("confirm_close")), session_id=body.get("session_id"))

    @api.get("/api/settings")
    def settings():
        return app_state.settings.public()

    @api.patch("/api/settings")
    def update_settings(body: dict):
        return app_state.settings.update(body)

    @api.get("/api/skills")
    def skills():
        return [{"id": s["name"], "name": s["title"], "description": s["description"], "version": s["version"], "tools": s["tools"],
                 "references": s["references"], "scripts": s["scripts"]} for s in app_state.registry.discover()]

    static = static_dir or app_state.settings.root / "web" / "dist"
    if static.is_dir():
        api.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

        @api.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404)
            target = (static / path).resolve()
            if path and target.is_file() and target.is_relative_to(static.resolve()):
                return FileResponse(target)
            return FileResponse(static / "index.html")

    return api

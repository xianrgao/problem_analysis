from __future__ import annotations
import json
import re
import shutil
from pathlib import Path
import pandas as pd
from plotly.utils import PlotlyJSONEncoder
from .database import Database, now
from .preview import make_preview, records


class VariableStore:
    RESERVED = {"skills", "host_capabilities", "active_plan", "variable_index"}

    def __init__(self, db: Database, root: Path):
        self.db, self.root = db, root
        root.mkdir(parents=True, exist_ok=True)

    def list(self, session_id, include_previews=False):
        with self.db.lock:
            rows = self.db.connection.execute("""SELECT v.data FROM variables v JOIN
                (SELECT name,MAX(version) version FROM variables WHERE session_id=? GROUP BY name) latest
                ON v.name=latest.name AND v.version=latest.version WHERE v.session_id=?""", (session_id, session_id)).fetchall()
        result = [json.loads(r[0]) for r in rows]
        return [r for r in result if include_previews or not r["name"].endswith("_preview")]

    def describe(self, session_id, name, version=None):
        sql = "SELECT data FROM variables WHERE session_id=? AND name=?"
        args = [session_id, name]
        if version is not None:
            sql += " AND version=?"
            args.append(int(version))
        sql += " ORDER BY version DESC LIMIT 1"
        with self.db.lock:
            row = self.db.connection.execute(sql, args).fetchone()
        if not row:
            raise KeyError(f"变量不存在: {name}")
        return json.loads(row[0])

    def save(self, session_id, name, value, description, kind=None, metadata=None, producer=None, parents=None):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,99}", name) or name in self.RESERVED or name.endswith("_preview"):
            raise ValueError("变量名须为英文字母开头的安全标识，且不能覆盖系统变量或 preview")
        if not description.strip():
            raise ValueError("变量必须有内容描述")
        kind = kind or ("table" if isinstance(value, pd.DataFrame) else "text" if isinstance(value, str) else "json")
        with self.db.lock:
            try:
                version = self.describe(session_id, name)["version"] + 1
            except KeyError:
                version = 1
            directory = self.root / session_id / name / str(version)
            directory.mkdir(parents=True, exist_ok=False)
            try:
                path = directory / ("data.parquet" if kind == "table" else "data.json")
                if kind == "table":
                    value = value.copy()
                    value.columns = value.columns.astype(str)
                    if value.columns.duplicated().any():
                        raise ValueError("表格包含重复列名，请为 JOIN 输出设置不同别名")
                    value.to_parquet(path, index=False)
                    preview = make_preview(value, name, version)
                    preview_path = directory / "preview.json"
                    preview_path.write_text(json.dumps(preview, ensure_ascii=False), encoding="utf-8")
                else:
                    path.write_text(json.dumps(value, ensure_ascii=False, cls=PlotlyJSONEncoder), encoding="utf-8")
                meta = {"name": name, "version": version, "kind": kind, "description": description,
                        "path": str(path), "row_count": len(value) if kind == "table" else None,
                        "columns": list(value.columns) if kind == "table" else [], "metadata": metadata or {},
                        "producer": producer, "parents": parents or [], "created_at": now()}
                with self.db.connection:
                    self.db.connection.execute("INSERT INTO variables VALUES(?,?,?,?)", (session_id, name, version, json.dumps(meta, ensure_ascii=False)))
                    if kind == "table":
                        pm = {**meta, "name": name + "_preview", "kind": "json", "path": str(preview_path), "description": preview["description"], "row_count": len(preview["rows"])}
                        self.db.connection.execute("INSERT INTO variables VALUES(?,?,?,?)", (session_id, pm["name"], version, json.dumps(pm, ensure_ascii=False)))
            except Exception:
                shutil.rmtree(directory)
                raise
        self.db.event(session_id, f"已保存 {description}，数据版本与预览已同步更新。", "variable", "succeeded", {"variable": self.public(meta)})
        return meta

    @staticmethod
    def public(meta):
        return {k: v for k, v in meta.items() if k != "path"}

    def load(self, session_id, name, version=None):
        meta = self.describe(session_id, name, version)
        return pd.read_parquet(meta["path"]) if meta["kind"] == "table" else json.loads(Path(meta["path"]).read_text(encoding="utf-8"))

    def preview(self, session_id, name, version=None):
        meta = self.describe(session_id, name, version)
        if meta["kind"] == "table":
            return self.load(session_id, name + "_preview", meta["version"])
        value = self.load(session_id, name, meta["version"])
        return {"variable_name": name, "source_version": meta["version"], "kind": meta["kind"], "value": value}

    def table(self, session_id, name, version=None, offset=0, limit=50, sort=None, direction="asc", search=""):
        meta = self.describe(session_id, name, version)
        if meta["kind"] != "table":
            raise ValueError("该变量不是表格")
        frame = self.load(session_id, name, meta["version"])
        if search:
            mask = frame.astype("string").apply(lambda c: c.str.contains(search, case=False, regex=False, na=False)).any(axis=1)
            frame = frame[mask]
        if sort in frame.columns:
            frame = frame.sort_values(sort, ascending=direction != "desc", kind="stable")
        return {"columns": list(frame.columns), "rows": records(frame.iloc[offset:offset + limit]), "total": len(frame), "offset": offset, "limit": limit, "version": meta["version"]}

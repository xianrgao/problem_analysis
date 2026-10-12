from __future__ import annotations
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def uid(prefix=""):
    return prefix + uuid.uuid4().hex[:16]


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS objects(kind TEXT, id TEXT, data TEXT NOT NULL,
                PRIMARY KEY(kind,id));
            CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                role TEXT, content TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS events_session ON events(session_id,id);
            CREATE TABLE IF NOT EXISTS variables(session_id TEXT,name TEXT,version INTEGER,
                data TEXT NOT NULL,PRIMARY KEY(session_id,name,version));
            CREATE INDEX IF NOT EXISTS variables_session ON variables(session_id,name,version);
        """)
        self.connection.commit()

    def put(self, kind, value, key=None):
        with self.lock, self.connection:
            self.connection.execute("INSERT OR REPLACE INTO objects VALUES(?,?,?)",
                                    (kind, key or value["id"], json.dumps(value, ensure_ascii=False, default=str)))
        return value

    def get(self, kind, id):
        with self.lock:
            row = self.connection.execute("SELECT data FROM objects WHERE kind=? AND id=?", (kind, id)).fetchone()
        if not row:
            raise KeyError(f"{kind} 不存在: {id}")
        return json.loads(row[0])

    def all(self, kind):
        with self.lock:
            rows = self.connection.execute("SELECT data FROM objects WHERE kind=?", (kind,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def patch(self, kind, id, **updates):
        with self.lock:
            value = self.get(kind, id)
            value.update(updates)
            value["updated_at"] = now()
            return self.put(kind, value, key=id)

    def message(self, session_id, role, content):
        value = {"id": uid("msg_"), "role": role, "content": content, "created_at": now()}
        with self.lock, self.connection:
            self.connection.execute("INSERT INTO messages VALUES(?,?,?,?,?)", (value["id"], session_id, role, content, value["created_at"]))
        return value

    def messages(self, session_id):
        with self.lock:
            return [dict(r) for r in self.connection.execute("SELECT id,role,content,created_at FROM messages WHERE session_id=? ORDER BY rowid", (session_id,))]

    def event(self, session_id, message, kind="progress", status="running", details=None, run_id=None, node_id=None):
        value = {"session_id": session_id, "run_id": run_id, "node_id": node_id, "kind": kind,
                 "status": status, "message": message, "details": details or {}, "created_at": now()}
        with self.lock, self.connection:
            cursor = self.connection.execute("INSERT INTO events(session_id,data) VALUES(?,?)", (session_id, json.dumps(value, ensure_ascii=False, default=str)))
            value["id"] = cursor.lastrowid
        return value

    def events(self, session_id, after=0):
        with self.lock:
            rows = self.connection.execute("SELECT id,data FROM events WHERE session_id=? AND id>? ORDER BY id", (session_id, after)).fetchall()
        return [{**json.loads(row["data"]), "id": row["id"]} for row in rows]

    def close(self):
        self.connection.close()

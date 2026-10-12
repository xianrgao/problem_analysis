"""Read-only demo sources and explicit adapter contracts for company systems."""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

import pandas as pd


class SourceQueryError(ValueError):
    """A source rejected or could not complete a query; no fallback is implied."""


class DemoMOS:
    """Read-only SQLite query adapter. Every call uses its own connection."""

    def __init__(self, data_dir: Path, timeout_seconds: float = 20, max_rows: int = 2_000_000):
        self.path = Path(data_dir) / "mos.sqlite"
        self.timeout_seconds = timeout_seconds
        self.max_rows = max_rows

    def schema(self) -> str:
        return Path(__file__).with_name("schema.sql").read_text(encoding="utf-8") + "\n-- TG is the workpiece identifier. Join visits.tg=workpieces.tg; visits.station_id=stations.station_id; measurements.visit_id=visits.visit_id; nc_events.measurement_id=measurements.measurement_id. NC events can repeat per workpiece. Datetimes are ISO8601 plant-local Asia/Shanghai. Shift starts: 07:00/19:00. Use OP30 completion counts for output, and launch time for startup-piece analysis."

    def query(self, sql: str) -> pd.DataFrame:
        if not self.path.exists():
            raise FileNotFoundError(f"模拟 MOS 尚未初始化：{self.path}")
        if not isinstance(sql, str) or len(sql) > 100_000:
            raise SourceQueryError("SQL 为空或长度超出限制")
        cleaned = re.sub(r"/\*.*?\*/|--[^\n]*", " ", sql, flags=re.S).strip()
        if not re.match(r"^(SELECT|WITH|EXPLAIN\s+QUERY\s+PLAN)\b", cleaned, re.I):
            raise SourceQueryError("仅允许单条 SELECT、只读 WITH 或 EXPLAIN QUERY PLAN 查询")
        started = time.monotonic()
        uri = self.path.resolve().as_uri() + "?mode=ro"
        db = sqlite3.connect(uri, uri=True, timeout=self.timeout_seconds)
        try:
            db.execute("PRAGMA query_only=ON")
            db.enable_load_extension(False)
            allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, getattr(sqlite3, "SQLITE_RECURSIVE", 33)}

            def authorize(action, arg1, arg2, _database, _trigger):
                if action not in allowed:
                    return sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_FUNCTION and str(arg2 or arg1).lower() in {"load_extension", "readfile", "writefile", "eval", "fts3_tokenizer"}:
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            db.set_authorizer(authorize)
            db.set_progress_handler(lambda: int(time.monotonic() - started > self.timeout_seconds), 1000)
            cursor = db.execute(sql)
            if cursor.description is None:
                raise SourceQueryError("查询未返回表格结果")
            columns = [column[0] for column in cursor.description]
            if len(columns) != len(set(columns)):
                raise SourceQueryError("结果中存在重复列名，请在 SQL 中使用明确别名")
            rows = cursor.fetchmany(self.max_rows + 1)
            if len(rows) > self.max_rows:
                raise SourceQueryError(f"查询超过 {self.max_rows} 行，请限定时间或分批查询；结果未静默截断")
            return pd.DataFrame.from_records(rows, columns=columns)
        except sqlite3.Error as exc:
            if "interrupted" in str(exc).lower():
                raise SourceQueryError("MOS 查询超过执行时限，请缩小范围或优化关联") from exc
            raise SourceQueryError(str(exc)) from exc
        finally:
            db.close()


class DemoSplunk:
    """Separate log store. Filters are explicit; unknown filters are rejected."""

    _aliases = {"binding": "bindings", "first_bindings": "bindings", "绑定": "bindings", "change": "changes", "change_points": "changes", "变化点": "changes", "operation": "logs", "quality": "logs", "signals": "logs"}

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    def query(self, kind: str, filters: dict | None = None) -> pd.DataFrame:
        kind = self._aliases.get(kind, kind)
        if kind not in {"bindings", "changes", "logs"}:
            raise SourceQueryError(f"未知日志能力：{kind}；支持 bindings、changes、logs")
        path = self.data_dir / f"{kind}.jsonl"
        if not path.exists():
            raise FileNotFoundError(f"模拟 Splunk 尚未初始化：{path}")
        filters = dict(filters or {})
        allowed = {"tg", "tgs", "station_id", "component", "pn", "position", "batch", "supplier", "structure", "change_type", "start", "end", "start_time", "end_time", "first_only", "text"}
        unknown = set(filters) - allowed
        if unknown:
            raise SourceQueryError(f"不支持的日志筛选项：{', '.join(sorted(unknown))}")
        frame = pd.read_json(path, lines=True, dtype=False, convert_dates=False)
        if frame.empty:
            return frame
        for key in ["tg", "tgs", "station_id", "component", "pn", "position", "batch", "supplier", "structure", "change_type"]:
            if key not in filters:
                continue
            column = "tg" if key == "tgs" else key
            if column not in frame:
                raise SourceQueryError(f"{kind} 日志没有可筛选的 {column} 字段")
            value = filters[key]
            values = value if isinstance(value, (list, tuple, set)) else [value]
            frame = frame[frame[column].isin(values)]
        date_col = "bound_at" if kind == "bindings" else "occurred_at"
        times = pd.to_datetime(frame[date_col], errors="coerce")
        for keys, lower in [(("start", "start_time"), True), (("end", "end_time"), False)]:
            value = next((filters[k] for k in keys if k in filters), None)
            if value is not None:
                boundary = pd.Timestamp(value)
                frame = frame.loc[times >= boundary if lower else times < boundary]
                times = times.loc[frame.index]
        if filters.get("text"):
            pattern = str(filters["text"])
            frame = frame[frame.astype(str).apply(lambda s: s.str.contains(pattern, regex=False)).any(axis=1)]
        if kind == "bindings" and filters.get("first_only", True):
            # first_only means first within the explicitly requested search scope.
            # Omit date filters to obtain original lifetime binding for a TG.
            frame = frame.sort_values(["bound_at", "event_id"], kind="stable").drop_duplicates(["tg", "component", "position"], keep="first")
        return frame.reset_index(drop=True)

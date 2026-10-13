"""MOS adapter example: keep the existing MOS agent as the SQL-writing sub-agent.

settings.json::

    "connectors": {"mos": {"driver": "python", "callable": "examples.adapters.mos_agent:query"}}

Request (from the host ``mos.query`` tool)::

    {"question": "自然语言取数需求", "name": "建议变量名", "description": "...", "sql": "可选：已审核 SQL"}

Return real rows plus the SQL that was actually executed. The host publishes the
variable, version, row count and preview; never return a prose summary.

Replace ``ExistingMosAgent`` with the company agent. Credentials stay where the
existing agent already reads them (credential store, protected config file);
they are not passed through the workbench settings or logs.
"""
from __future__ import annotations

import os
import re
import sqlite3

import pandas as pd

READ_ONLY = re.compile(r"^\s*(SELECT|WITH)\b", re.I)


class ExistingMosAgent:
    """Stand-in for the company agent: question -> validated read-only SQL -> rows.

    This stand-in reads a SQLite file named by LINE_ENGINEER_MOS_SQLITE so the
    contract can be exercised offline; the real agent connects to MOS.
    """

    def __init__(self, database: str):
        self.database = database

    def write_sql(self, question: str) -> str:
        raise NotImplementedError("Call the company MOS agent's SQL generation here.")

    def run(self, sql: str) -> pd.DataFrame:
        if not READ_ONLY.match(sql) or ";" in sql.strip().rstrip(";"):
            raise ValueError("MOS 适配器只执行单条只读 SELECT/WITH 语句")
        uri = f"file:{self.database}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            return pd.read_sql_query(sql, connection)


def query(request: dict) -> dict:
    agent = ExistingMosAgent(os.environ.get("LINE_ENGINEER_MOS_SQLITE", ""))
    sql = request.get("sql") or agent.write_sql(request["question"])
    frame = agent.run(sql)
    return {"data": frame, "sql": sql, "name": request.get("name") or "mos_records",
            "description": request.get("description") or f"MOS 查询结果：{request.get('question', '')}"}

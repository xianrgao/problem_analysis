"""Splunk adapter example: wrap an existing Splunk agent behind the ``splunk.query`` tool.

settings.json::

    "connectors": {"splunk": {"driver": "python", "callable": "examples.adapters.splunk_agent:query"}}

Request::

    {"kind": "bindings" | "changes" | "logs", "filters": {"tgs": [...], "station_id": "OP20", "start": "...", "end": "..."},
     "name": "可选变量名", "description": "..."}

The existing agent keeps its search syntax, paging, retries and credentials.
This adapter only maps request fields to the agent and maps result fields to
the workbench columns. Unsupported kinds raise instead of returning other data.
"""
from __future__ import annotations

import pandas as pd

# Company field -> workbench column. Fill from the verified Splunk field list.
FIELD_MAP = {
    "bindings": {"serial": "tg", "part_name": "component", "part_number": "pn", "slot": "position",
                 "lot": "batch", "vendor": "supplier", "_time": "bound_at", "event_uid": "event_id"},
    "changes": {"station": "station_id", "asset": "structure", "change": "change_type", "_time": "occurred_at", "text": "message"},
}


class ExistingSplunkAgent:
    """Stand-in for the company agent; replace with the real import."""

    def search_bindings(self, serials, part_name=None, earliest=None, latest=None):
        raise NotImplementedError("Call the existing Splunk agent here.")

    def search_changes(self, station, earliest=None, latest=None):
        raise NotImplementedError("Call the existing Splunk agent here.")


def query(request: dict, agent: ExistingSplunkAgent | None = None) -> pd.DataFrame:
    agent = agent or ExistingSplunkAgent()
    kind, filters = request.get("kind", "bindings"), dict(request.get("filters") or {})
    if kind == "bindings":
        tgs = filters.get("tgs") or ([filters["tg"]] if filters.get("tg") else [])
        records = agent.search_bindings(tgs, filters.get("component"), filters.get("start"), filters.get("end"))
    elif kind == "changes":
        records = agent.search_changes(filters["station_id"], filters.get("start"), filters.get("end"))
    else:
        raise ValueError(f"已接入的 Splunk agent 不支持 {kind} 查询")
    frame = pd.DataFrame(records).rename(columns=FIELD_MAP[kind])
    if kind == "bindings" and filters.get("first_only", True) and len(frame):
        # First valid binding per TG / component / installation position.
        order = ["bound_at"] + (["event_id"] if "event_id" in frame else [])
        keys = [c for c in ("tg", "component", "position") if c in frame]
        frame = frame.sort_values(order, kind="stable").drop_duplicates(keys, keep="first")
    return frame.reset_index(drop=True)

"""Chart adapter example: reuse an existing chart program as the ``chart.create`` backend.

settings.json::

    "connectors": {"charts": {"driver": "python", "callable": "examples.adapters.chart_program:render"}}

Request::

    {"action": "chart", "data_path": "<parquet of the full variable>", "variable": "name", "columns": [...],
     "kind": "line|bar|box|scatter|histogram", "x": "...", "y": "...", "group": "...", "title": "..."}

Return one of: a Plotly figure dict ``{"data": [...], "layout": {...}}``,
``{"figure": {...}}`` or ``{"image_path": "chart.png"}``. The host stores it as
a standard chart artifact linked to the source variable version.
"""
from __future__ import annotations

import json

import pandas as pd
import plotly.express as px


def legacy_draw(frame: pd.DataFrame, kind: str, x: str, y: str | None, group: str | None, title: str):
    """Stand-in for the existing, validated chart routine."""
    factories = {"line": px.line, "bar": px.bar, "scatter": px.scatter, "box": px.box}
    if kind == "histogram":
        return px.histogram(frame, x=y or x, color=group, title=title)
    return factories.get(kind, px.bar)(frame, x=x, y=y, color=group, title=title)


def render(request: dict) -> dict:
    frame = pd.read_parquet(request["data_path"])
    columns = list(frame.columns)
    x = request.get("x") or columns[0]
    y = request.get("y") or next((c for c in frame.select_dtypes("number").columns if c != x), None)
    figure = legacy_draw(frame, request.get("kind", "bar"), x, y, request.get("group"), request.get("title") or request["variable"])
    return {"figure": json.loads(figure.to_json())}

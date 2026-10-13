"""Run one configured connector with a sample request and check the result contract.

Usage:
    python check_adapter.py <mos|splunk|curves|charts> --request '{"kind": "bindings", "filters": {...}}'
        [--settings data/settings.json] [--config '{"driver": "python", "callable": "module:function"}']

Only the result shape is printed (type, row count, columns); data values and
connector credentials are not printed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def project_root(start: Path) -> Path:
    for candidate in [start, *start.parents]:
        if (candidate / "src" / "line_engineer").is_dir():
            return candidate
    raise SystemExit("未找到 problem_analysis 项目根目录（需要 src/line_engineer）")


def check(name: str, config: dict, request: dict) -> dict:
    from line_engineer.connectors.python_adapter import call, to_frame
    from line_engineer.orchestration.host import Host

    if config.get("driver", "demo") == "demo":
        raise SystemExit(f"连接器 {name} 仍为 demo，请先在 settings.json 中配置 python 或 script 驱动")
    raw = call(config, request)
    if name == "charts":
        figure = Host.standard_chart(raw)
        return {"connector": name, "result": "chart", "traces": len(figure["data"]), "has_layout_image": bool(figure["layout"].get("images"))}
    frame = to_frame(raw)
    summary = {"connector": name, "result": "table", "rows": len(frame), "columns": [str(c) for c in frame.columns],
               "duplicated_columns": bool(frame.columns.duplicated().any())}
    if name == "mos":
        summary["sql_returned"] = bool(isinstance(raw, dict) and raw.get("sql"))
    expected = {"splunk": {"bindings": ["tg", "component", "batch", "bound_at"], "changes": ["station_id", "occurred_at"]},
                "curves": {None: ["curve_id", "x", "y", "x_unit", "y_unit"]}}
    required = expected.get(name, {}).get(request.get("kind") if name == "splunk" else None, [])
    summary["missing_columns"] = [c for c in required if c not in frame.columns]
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="检查连接适配器的返回契约")
    parser.add_argument("connector", choices=["mos", "splunk", "curves", "charts"])
    parser.add_argument("--request", default="{}", help="JSON 请求")
    parser.add_argument("--settings", type=Path, help="settings.json 路径，默认 <项目>/data/settings.json")
    parser.add_argument("--config", help="直接提供连接器配置 JSON，优先于 settings.json")
    args = parser.parse_args(argv)
    root = project_root(Path(__file__).resolve())
    sys.path[:0] = [str(root / "src"), str(root)]
    if args.config:
        config = json.loads(args.config)
    else:
        settings = json.loads((args.settings or root / "data" / "settings.json").read_text(encoding="utf-8"))
        config = settings.get("connectors", {}).get(args.connector, {"driver": "demo"})
    summary = check(args.connector, config, json.loads(args.request))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    problems = summary.get("missing_columns") or summary.get("duplicated_columns") or (args.connector == "mos" and not summary.get("sql_returned"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

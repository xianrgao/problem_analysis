"""Script adapter example: run an existing tool under its own Python interpreter.

settings.json::

    "connectors": {"splunk": {"driver": "script", "python": "C:/Tools/legacy-env/python.exe",
                              "script": "C:/Tools/legacy/stdin_script.py", "timeout": 300}}

The host writes one JSON request to stdin and reads one JSON result from
stdout: ``{"rows": [...]}`` or ``{"data_path": "result.parquet|csv|xlsx"}``.
Logs go to stderr so they never corrupt the JSON result. The existing
interpreter and its packages are used as-is; the workbench environment is not
installed into it.
"""
from __future__ import annotations

import json
import sys


def legacy_lookup(request: dict) -> list[dict]:
    """Stand-in for the existing script's entry point."""
    tgs = request.get("filters", {}).get("tgs") or []
    return [{"tg": tg, "component": "轴承", "batch": "B-DEMO", "bound_at": "2026-10-01T08:00:00"} for tg in tgs]


def main():
    request = json.loads(sys.stdin.read() or "{}")
    print(f"processing {request.get('kind', 'bindings')}", file=sys.stderr)
    json.dump({"rows": legacy_lookup(request)}, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()

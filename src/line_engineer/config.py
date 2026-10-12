from __future__ import annotations
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class Settings:
    def __init__(self, data_dir: Path | None = None):
        self.root = ROOT
        self.data_dir = Path(data_dir or os.environ.get("LINE_ENGINEER_DATA", ROOT / "data")).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "settings.json"
        self.values = {"mode": "demo", "model": {"base_url": "", "model": "", "api_key": "", "timeout": 120},
                       "max_parallel_sessions": 2, "execution_timeout": 180, "max_skill_turns": 24}
        if self.path.exists():
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            self.values.update(saved)

    def public(self):
        result = json.loads(json.dumps(self.values))
        model = result["model"]
        model["api_key_set"] = bool(model.pop("api_key", "") or os.environ.get("LINE_ENGINEER_API_KEY"))
        return result

    def update(self, updates: dict):
        if "mode" in updates:
            if updates["mode"] not in ("demo", "live"):
                raise ValueError("mode 必须为 demo 或 live")
            self.values["mode"] = updates["mode"]
        if "model" in updates:
            allowed = {"base_url", "model", "api_key", "timeout", "endpoint", "headers"}
            self.values["model"].update({k: v for k, v in updates["model"].items() if k in allowed})
        if "max_parallel_sessions" in updates:
            self.values["max_parallel_sessions"] = max(1, min(8, int(updates["max_parallel_sessions"])))
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.values, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)
        return self.public()

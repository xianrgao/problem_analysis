from __future__ import annotations
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RULES_PATH = ROOT / "config" / "rules.json"


class Settings:
    def __init__(self, data_dir: Path | None = None):
        self.root = ROOT
        self.data_dir = Path(data_dir or os.environ.get("LINE_ENGINEER_DATA", ROOT / "data")).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "settings.json"
        self.values = {"mode": "demo", "model": {"driver": "openai", "base_url": "", "model": "", "api_key": "", "timeout": 120},
                       "max_parallel_sessions": 2, "execution_timeout": 180, "max_skill_turns": 24, "resume_on_restart": True,
                       "executor": {"python": "", "factory": ""}, "rules": {}}
        if self.path.exists():
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            for key in ("model", "executor"):
                if isinstance(saved.get(key), dict):
                    saved[key] = {**self.values[key], **saved[key]}
            self.values.update(saved)

    @staticmethod
    def default_rules():
        return json.loads(RULES_PATH.read_text(encoding="utf-8")) if RULES_PATH.exists() else {}

    def rules(self):
        """Experience rules from config/rules.json, overridden per machine in settings.json."""
        return {**self.default_rules(), **(self.values.get("rules") or {})}

    def public(self):
        result = json.loads(json.dumps(self.values))
        model = result["model"]
        model["api_key_set"] = bool(model.pop("api_key", "") or os.environ.get("LINE_ENGINEER_API_KEY"))
        model.pop("headers", None)
        # Connector configs may carry company credentials; only drivers are shown.
        result["connectors"] = {name: {"driver": cfg.get("driver", "demo")} for name, cfg in (result.get("connectors") or {}).items()}
        result["rules"] = self.rules()
        return result

    def update(self, updates: dict):
        if "mode" in updates:
            if updates["mode"] not in ("demo", "live"):
                raise ValueError("mode 必须为 demo 或 live")
            self.values["mode"] = updates["mode"]
        if "model" in updates:
            allowed = {"driver", "callable", "base_url", "model", "api_key", "timeout", "endpoint", "headers"}
            if updates["model"].get("driver", self.values["model"].get("driver", "openai")) not in ("openai", "python"):
                raise ValueError("模型 driver 必须为 openai 或 python")
            self.values["model"].update({k: v for k, v in updates["model"].items() if k in allowed})
        if "max_parallel_sessions" in updates:
            self.values["max_parallel_sessions"] = max(1, min(8, int(updates["max_parallel_sessions"])))
        if "resume_on_restart" in updates:
            self.values["resume_on_restart"] = bool(updates["resume_on_restart"])
        if "rules" in updates:
            known = self.default_rules()
            unknown = set(updates["rules"]) - set(known)
            if unknown:
                raise ValueError(f"未定义的经验规则：{', '.join(sorted(unknown))}")
            self.values["rules"] = {**(self.values.get("rules") or {}), **updates["rules"]}
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.values, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)
        return self.public()

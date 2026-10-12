from __future__ import annotations
import hashlib
import json
import shutil
from pathlib import Path
import yaml


class SkillRegistry:
    def __init__(self, root: Path, snapshots: Path):
        self.root, self.snapshots = root, snapshots
        snapshots.mkdir(parents=True, exist_ok=True)

    def discover(self):
        items = []
        for path in sorted(self.root.glob("*/SKILL.md")):
            raw = path.read_text(encoding="utf-8-sig")
            if not raw.startswith("---"):
                raise ValueError(f"技能缺少元数据: {path.name}")
            front = yaml.safe_load(raw.split("---", 2)[1])
            if front["name"] != path.parent.name:
                raise ValueError(f"技能名称和目录不同: {path.parent.name}")
            digest = hashlib.sha256()
            files = sorted(p for p in path.parent.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
            for item in files:
                digest.update(str(item.relative_to(path.parent)).replace("\\", "/").encode())
                digest.update(item.read_bytes())
            tools_path = path.parent / "tools.json"
            declared = json.loads(tools_path.read_text(encoding="utf-8-sig")) if tools_path.exists() else []
            if isinstance(declared, dict):
                declared = declared.get("tools", [])
            declared = [t if isinstance(t, str) else t["name"] for t in declared]
            metadata = front.get("metadata", {}) or {}
            items.append({"name": front["name"], "description": front["description"],
                          "title": metadata.get("title", front["name"]), "version": str(metadata.get("version", "1.0.0")),
                          "hash": digest.hexdigest(), "tools": declared,
                          "references": [p.relative_to(path.parent).as_posix() for p in files if "references" in p.parts],
                          "scripts": [p.relative_to(path.parent).as_posix() for p in files if "scripts" in p.parts]})
        return items

    def snapshot(self, name, expected_hash=None):
        metadata = next((s for s in self.discover() if s["name"] == name), None)
        if expected_hash:
            cached = self.snapshots / name / expected_hash
            if cached.exists():
                return cached
        if not metadata:
            raise ValueError(f"未知技能: {name}")
        if expected_hash and metadata["hash"] != expected_hash:
            raise ValueError(f"技能 {name} 已修改，需重新确认计划")
        destination = self.snapshots / name / metadata["hash"]
        if not destination.exists():
            shutil.copytree(self.root / name, destination, ignore=shutil.ignore_patterns("__pycache__"))
        return destination

    def read(self, name, relative="SKILL.md", version=None):
        directory = self.snapshot(name, version)
        path = (directory / relative).resolve()
        if not path.is_relative_to(directory.resolve()) or not path.is_file():
            raise ValueError("技能资源路径无效")
        return path.read_text(encoding="utf-8-sig")

from __future__ import annotations
import json
import re
from pathlib import Path
import pandas as pd
from plotly.utils import PlotlyJSONEncoder


class StagedVariables:
    def __init__(self, manifest, output_dir):
        self.inputs = {m["name"]: m for m in manifest}
        self.directory = Path(output_dir)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.outputs, self.reads = [], []

    def load(self, name, version=None):
        if name not in self.inputs:
            raise KeyError(f"输入快照没有变量 {name}")
        meta = self.inputs[name]
        if version is not None and meta["version"] != version:
            raise ValueError("请求版本不属于本次执行快照")
        self.reads.append({"name": name, "version": meta["version"]})
        return pd.read_parquet(meta["path"]) if meta["kind"] == "table" else json.loads(Path(meta["path"]).read_text(encoding="utf-8"))

    def save(self, name, value, description, kind=None, metadata=None):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,99}", name):
            raise ValueError("变量名无效")
        kind = kind or ("table" if isinstance(value, pd.DataFrame) else "text" if isinstance(value, str) else "json")
        path = self.directory / f"{len(self.outputs)}_{name}.{'parquet' if kind == 'table' else 'json'}"
        if kind == "table":
            value.to_parquet(path, index=False)
        else:
            path.write_text(json.dumps(value, ensure_ascii=False, cls=PlotlyJSONEncoder), encoding="utf-8")
        meta = {"name": name, "path": str(path), "description": description, "kind": kind, "metadata": metadata or {}}
        self.outputs.append(meta)
        self.inputs[name] = {**meta, "version": 0}
        return {"name": name, "staged": True}


class ExecutionContext:
    def __init__(self, inputs, output_dir):
        self.vars = StagedVariables(inputs, output_dir)

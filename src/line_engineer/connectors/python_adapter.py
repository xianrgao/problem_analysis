from __future__ import annotations
import importlib
import json
import subprocess
from pathlib import Path
import pandas as pd


def invoke(config: dict, request: dict):
    """Company adapters accept one request dictionary; see examples/adapters."""
    if config.get("driver") == "python":
        module, name = config["callable"].split(":", 1)
        result = getattr(importlib.import_module(module), name)(request)
    elif config.get("driver") == "script":
        completed = subprocess.run([config["python"], config["script"]], input=json.dumps(request, ensure_ascii=False),
                                   text=True, encoding="utf-8", capture_output=True, timeout=config.get("timeout", 120), check=True)
        result = json.loads(completed.stdout)
    else:
        raise ValueError("连接器 driver 必须为 python 或 script")
    if isinstance(result, pd.DataFrame):
        return result
    if isinstance(result, dict) and "data_path" in result:
        path = Path(result["data_path"])
        if path.suffix.lower() == ".parquet":
            return pd.read_parquet(path)
        if path.suffix.lower() in (".xlsx", ".xls"):
            return pd.read_excel(path)
        return pd.read_csv(path)
    if isinstance(result, dict) and "rows" in result:
        return pd.DataFrame(result["rows"])
    if isinstance(result, list):
        return pd.DataFrame(result)
    raise ValueError("适配器需返回 DataFrame、rows 或 data_path，不能只返回模型编写的数据说明")

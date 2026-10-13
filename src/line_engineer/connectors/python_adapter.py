from __future__ import annotations
import importlib
import json
import subprocess
from pathlib import Path
import pandas as pd


def call(config: dict, request: dict):
    """Run a company adapter with one request dictionary and return its raw result.

    driver=python imports ``callable`` ("module:function"); driver=script runs
    ``script`` with its own ``python`` interpreter using JSON stdin/stdout.
    See examples/adapters.
    """
    if config.get("driver") == "python":
        module, name = config["callable"].split(":", 1)
        return getattr(importlib.import_module(module), name)(request)
    if config.get("driver") == "script":
        try:
            completed = subprocess.run([config["python"], config["script"]], input=json.dumps(request, ensure_ascii=False, default=str),
                                       text=True, encoding="utf-8", capture_output=True, timeout=config.get("timeout", 120), check=True)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"适配脚本执行失败（退出码 {exc.returncode}）：{(exc.stderr or '')[-2000:]}") from None
        return json.loads(completed.stdout)
    raise ValueError("连接器 driver 必须为 python 或 script")


def invoke(config: dict, request: dict):
    """Run a data adapter; the result must be real rows, never a prose summary."""
    return to_frame(call(config, request))


def to_frame(result):
    if isinstance(result, dict) and isinstance(result.get("data"), pd.DataFrame):
        return result["data"]
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

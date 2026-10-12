from __future__ import annotations
import hashlib
import numpy as np
import pandas as pd


def records(frame: pd.DataFrame):
    # pandas JSON consistently handles NaN, NaT, numpy scalars and timestamps.
    import json
    return json.loads(frame.to_json(orient="records", date_format="iso", force_ascii=False))


def make_preview(frame: pd.DataFrame, name: str, version: int):
    data = frame.reset_index(drop=True)
    columns = [{"name": str(c), "dtype": str(data[c].dtype), "distinct": int(data[c].nunique(dropna=False)),
                "null_count": int(data[c].isna().sum())} for c in data.columns]
    size = len(data)
    selected: list[int] = []
    covered = {}
    rng = np.random.default_rng(int(hashlib.sha256(f"{name}:{version}".encode()).hexdigest()[:8], 16))
    encoded = {c: data[c].astype("string").fillna("<NULL>") for c in data.columns}
    low = sorted((c for c in columns if c["distinct"] <= 40), key=lambda c: (c["distinct"], c["name"]))
    for column in low:
        col = column["name"]
        existing = set(encoded[col].iloc[selected]) if selected else set()
        # One representative per missing category, stable by the data version seed.
        for category, indices in encoded[col].groupby(encoded[col], sort=True).groups.items():
            if category not in existing and len(selected) < min(size, 40):
                selected.append(int(rng.choice(np.asarray(indices))))
                existing.add(category)
    target = min(size, max(30, len(selected)))
    if len(selected) < target:
        available = np.setdiff1d(np.arange(size), np.array(selected, dtype=int))
        selected.extend(int(i) for i in rng.choice(available, target - len(selected), replace=False))
    sample = data.iloc[selected]
    for col in columns:
        count = int(encoded[col["name"]].iloc[selected].nunique())
        covered[col["name"]] = {"represented": count, "total": col["distinct"], "complete": count == col["distinct"]}
    return {"variable_name": f"{name}_preview", "source_variable": name, "source_version": version,
            "total_rows": size, "columns": columns, "rows": records(sample), "coverage": covered,
            "description": f"{name} 第 {version} 版的代表性预览；统计分析应读取全量数据。"}

"""Pure helpers for unit-level comparisons; no filesystem or network access."""
from __future__ import annotations

import pandas as pd


def grouped_quality(frame: pd.DataFrame, group: str, unit: str = "tg", outcome: str = "result") -> pd.DataFrame:
    required = {group, unit, outcome}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")
    if frame[unit].isna().any():
        raise ValueError("Unit identifiers must not be missing")
    invalid = set(frame[outcome].dropna().unique()).difference({"OK", "NC"})
    if invalid or frame[outcome].isna().any():
        raise ValueError("Outcomes must explicitly be OK or NC")
    unit_groups = frame.groupby(unit, dropna=False)[group].nunique(dropna=False)
    if (unit_groups > 1).any():
        raise ValueError("A unit maps to multiple groups; select one component/position first")
    units = frame.groupby([group, unit], dropna=False)[outcome].agg(lambda x: x.eq("NC").any()).reset_index(name="is_nc")
    result = units.groupby(group, dropna=False).agg(unit_count=(unit, "size"), nc_count=("is_nc", "sum")).reset_index()
    result["nc_count"] = result["nc_count"].astype(int)
    result["nc_rate"] = result["nc_count"] / result["unit_count"]
    return result

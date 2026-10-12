"""Validated left enrichment that preserves source rows."""
from __future__ import annotations

from collections.abc import Sequence
import pandas as pd


def safe_enrich(source: pd.DataFrame, reference: pd.DataFrame, keys: Sequence[str], columns: Sequence[str]) -> tuple[pd.DataFrame, dict]:
    keys, columns = list(keys), list(columns)
    if not keys or set(keys) & set(columns):
        raise ValueError("Keys are required and cannot be payload columns")
    if len(keys) != len(set(keys)) or len(columns) != len(set(columns)):
        raise ValueError("Duplicate key or payload column names")
    if not set(keys).issubset(source.columns) or not set(keys + columns).issubset(reference.columns):
        raise ValueError("Missing source or reference columns")
    collision = set(columns).intersection(source.columns)
    if collision:
        raise ValueError(f"Explicit overwrite policy required: {sorted(collision)}")
    if reference[keys].isna().any().any():
        raise ValueError("Reference keys cannot be null")
    if reference.duplicated(keys).any():
        raise ValueError("Reference is not unique on join keys")
    marker = "__skill_join_status__"
    if marker in source or marker in reference:
        raise ValueError("Reserved join marker collides with input")
    output = source.merge(reference[keys + columns], how="left", on=keys, sort=False, validate="many_to_one", indicator=marker)
    report = {"source_rows": len(source), "output_rows": len(output), "unmatched_rows": int(output[marker].eq("left_only").sum()), "added_columns": columns}
    output = output.drop(columns=marker)
    if len(output) != len(source):
        raise AssertionError("Source cardinality changed")
    return output, report

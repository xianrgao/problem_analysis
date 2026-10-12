"""Features for one monotonic process phase, with explicit units upstream."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _xy(curve: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    if not {"x", "y", "sample_index"}.issubset(curve.columns):
        raise ValueError("Curve requires x, y, sample_index")
    if curve["sample_index"].duplicated().any():
        raise ValueError("Duplicate sample indexes")
    ordered = curve.sort_values("sample_index")
    x, y = ordered["x"].to_numpy(dtype=float), ordered["y"].to_numpy(dtype=float)
    if len(x) < 2 or not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("At least two finite points are required")
    if not np.all(np.diff(x) > 0):
        raise ValueError("Select a strictly increasing phase before interpolation")
    return x, y


def extract_features(curve: pd.DataFrame) -> dict:
    x, y = _xy(curve)
    maximum = int(np.argmax(y))
    return {"samples": len(x), "peak": float(y[maximum]), "peak_x": float(x[maximum]),
            "start_x": float(x[0]), "end_x": float(x[-1]), "end_y": float(y[-1]),
            "mean_slope": float((y[-1] - y[0]) / (x[-1] - x[0])),
            "area_xy_units": float(np.sum((y[1:] + y[:-1]) * np.diff(x) / 2)),
            "y_std": float(np.std(y, ddof=1))}


def align_common(curves: list[pd.DataFrame], points: int = 100) -> tuple[np.ndarray, np.ndarray]:
    if not curves or points < 2:
        raise ValueError("Curves and at least two resampling points are required")
    arrays = [_xy(curve) for curve in curves]
    left, right = max(x[0] for x, _ in arrays), min(x[-1] for x, _ in arrays)
    if right <= left:
        raise ValueError("Curves do not share a nonzero x interval")
    grid = np.linspace(left, right, points)
    return grid, np.vstack([np.interp(grid, x, y) for x, y in arrays])

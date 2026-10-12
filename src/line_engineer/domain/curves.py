"""Curve inspection and chart creation with explicit units and stage boundaries."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


def _json(figure: go.Figure) -> dict:
    figure.update_layout(template="plotly_white", font={"family": "Segoe UI, Microsoft YaHei, sans-serif"}, colorway=["#3976eb", "#d58c3d", "#219b8c", "#b96183"], margin={"l": 52, "r": 24, "t": 54, "b": 45}, hovermode="closest")
    return json.loads(figure.to_json())


def make_chart(df: pd.DataFrame, kind: str = "auto", x: str | None = None, y: str | None = None, group: str | None = None) -> dict:
    """Return a standalone Plotly figure dict, without sending full data to AI."""
    if df.empty:
        figure = go.Figure()
        figure.add_annotation(text="当前筛选范围暂无数据", showarrow=False)
        return _json(figure)
    numeric = list(df.select_dtypes(include="number").columns)
    x = x or ("date" if "date" in df else str(df.columns[0]))
    y = y or next((column for column in numeric if column != x), None)
    for column in [x, y, group]:
        if column is not None and column not in df:
            raise ValueError(f"图表字段不存在：{column}")
    if kind == "auto":
        kind = "line" if "date" in x.lower() or "time" in x.lower() or pd.api.types.is_datetime64_any_dtype(df[x]) else "bar"
    if kind in {"histogram", "hist"}:
        return _json(px.histogram(df, x=y or x, color=group, barmode="overlay", opacity=.65))
    if kind in {"table", "表格"}:
        # Large data belongs in the data viewer, table figures are intentionally capped.
        table = df.head(100)
        return _json(go.Figure(go.Table(header={"values": list(table.columns)}, cells={"values": [table[c].astype(str).tolist() for c in table]})))
    if y is None:
        counts = df[x].value_counts(dropna=False).rename_axis(x).reset_index(name="count")
        return _json(px.bar(counts, x=x, y="count"))
    factories = {"line": px.line, "bar": px.bar, "scatter": px.scatter, "box": px.box, "violin": px.violin, "area": px.area}
    if kind not in factories:
        raise ValueError(f"不支持图表类型：{kind}")
    figure = factories[kind](df, x=x, y=y, color=group)
    return _json(figure)


def analyze_curves(df: pd.DataFrame, alignment: str = "start", reference_group: str = "OK") -> dict:
    """Analyze original curves independently by kind and phase.

    Input: curve_id,x,y,x_unit,y_unit; optional sample_index,kind,phase,result,
    tg,batch,supplier. Non-monotonic travel must have explicit phase labels.
    'contact' uses each curve's first sample >=5% of its positive peak.
    A reference envelope is empirical 5th/95th percentile, never specification.
    """
    if alignment not in {"start", "contact", "none"}:
        raise ValueError("alignment 仅支持 start、contact、none")
    if df.empty:
        return {"summary": "当前范围没有曲线数据。", "tables": {}, "figures": {}, "findings": [], "warnings": ["未还原或生成不存在的曲线。"]}
    required = {"curve_id", "x", "y", "x_unit", "y_unit"}
    if not required.issubset(df):
        # A final-value table is analyzable, but cannot produce imaginary curves.
        if "value" in df:
            numeric = pd.to_numeric(df["value"], errors="coerce")
            table = numeric.describe().rename_axis("statistic").reset_index(name="value")
            return {"summary": "输入仅含最终测量值，已生成分布统计；没有过程采样点，无法进行曲线对齐或异常区段分析。", "tables": {"final_value_summary": table}, "figures": {"final_value_distribution": make_chart(df, "histogram", y="value", group="result" if "result" in df else None)}, "findings": ["最终值不能重建过程曲线。"], "warnings": ["需要从设备日志提供逐点采样、轴含义及单位。"]}
        raise ValueError(f"曲线数据缺少字段：{sorted(required-set(df.columns))}")
    data = df.copy()
    data["kind"] = data["kind"] if "kind" in data else "curve"
    data["phase"] = data["phase"] if "phase" in data else "default"
    data["result"] = data["result"] if "result" in data else "未分组"
    data["sample_index"] = data["sample_index"] if "sample_index" in data else data.groupby("curve_id").cumcount()
    if data[["curve_id", "kind", "phase", "x_unit", "y_unit"]].isna().any().any():
        raise ValueError("曲线标识、阶段或单位缺失，不能进行有效对比")
    data["x"] = pd.to_numeric(data["x"], errors="coerce")
    data["y"] = pd.to_numeric(data["y"], errors="coerce")
    valid = np.isfinite(data["x"]) & np.isfinite(data["y"])
    dropped = int((~valid).sum())
    data = data[valid].copy()
    warnings = [f"已剔除 {dropped} 个非数值或缺失采样点，原始输入数据不被改写。"] if dropped else []
    findings = ["参考包络为合格组经验分位数范围，仅用于探索性比较，不代表工艺规格或判定限。"]
    features, aligned_frames, envelope_frames, abnormal = [], [], [], []
    figures = {}
    for (kind, phase), subset in data.groupby(["kind", "phase"], dropna=False, sort=True):
        if subset["x_unit"].nunique() != 1 or subset["y_unit"].nunique() != 1:
            raise ValueError(f"{kind}/{phase} 存在单位冲突，请先显式转换单位后再分析")
        x_unit, y_unit = subset["x_unit"].iloc[0], subset["y_unit"].iloc[0]
        raw_fig, aligned_fig = go.Figure(), go.Figure()
        candidates = []
        for curve_id, points in subset.groupby("curve_id", sort=False):
            points = points.sort_values("sample_index", kind="stable")
            for field in ["result", "tg", "batch", "supplier"]:
                if field in points and points[field].nunique(dropna=False) > 1:
                    raise ValueError(f"曲线 {curve_id} 的 {field} 元数据不一致")
            x, y = points["x"].to_numpy(dtype=float), points["y"].to_numpy(dtype=float)
            if len(x) < 2:
                warnings.append(f"{curve_id}/{phase} 仅有一个采样点，已保留为最终值，未生成过程曲线。")
                continue
            delta = np.diff(x)
            if (delta > 0).any() and (delta < 0).any():
                raise ValueError(f"{curve_id}/{phase} 包含往返过程，请按 loading/unloading 等阶段拆分后分析，不能排序覆盖真实路径")
            metadata = {key: points[key].iloc[0] for key in ["tg", "result", "batch", "supplier"] if key in points}
            peak_index = int(np.argmax(y))
            dy, dx = np.diff(y), np.diff(x)
            slopes = np.divide(dy, dx, out=np.full(len(dx), np.nan), where=np.abs(dx) > 1e-12)
            finite_slopes = slopes[np.isfinite(slopes)]
            features.append({"curve_id": curve_id, "kind": kind, "phase": phase, **metadata, "sample_count": len(x), "x_unit": x_unit, "y_unit": y_unit, "peak": float(y[peak_index]), "peak_x": float(x[peak_index]), "end_value": float(y[-1]), "max_slope": float(finite_slopes.max()) if len(finite_slopes) else None, "end_slope": float(finite_slopes[-1]) if len(finite_slopes) else None, "fluctuation": float(np.std(dy)), "x_start": float(x[0]), "x_end": float(x[-1])})
            anchor = x[0] if alignment == "start" else 0.
            if alignment == "contact":
                if y.max() <= 0:
                    warnings.append(f"{curve_id} 不存在正向接触阈值，未纳入接触点对齐。")
                    continue
                contact = np.flatnonzero(y >= .05 * y.max())
                anchor = x[int(contact[0])]
            aligned_x = x - anchor
            # Mean duplicate x values only for interpolation; raw path is retained.
            interpolation = pd.DataFrame({"x": aligned_x, "y": y}).groupby("x", as_index=False, sort=True)["y"].mean()
            if len(interpolation) < 2:
                warnings.append(f"{curve_id} 横轴无有效变化，无法重采样。")
                continue
            candidates.append((curve_id, interpolation, metadata))
            color = "#3674e8" if str(metadata.get("result")) == reference_group else "#d78844"
            # All data is returned; only the overlaid traces are capped for UI load.
            if len(raw_fig.data) < 100:
                raw_fig.add_trace(go.Scatter(x=x, y=y, mode="lines", name=f"{curve_id} · {metadata.get('result','')}", line={"color": color, "width": 1.2}, opacity=.4, showlegend=False))
                aligned_fig.add_trace(go.Scatter(x=aligned_x, y=y, mode="lines", name=f"{curve_id} · {metadata.get('result','')}", line={"color": color, "width": 1.2}, opacity=.4, showlegend=False))
        key = f"{kind}_{phase}"
        raw_fig.update_layout(title=f"{kind} / {phase} · 原始曲线", xaxis_title=x_unit, yaxis_title=y_unit)
        aligned_fig.update_layout(title=f"{kind} / {phase} · {alignment} 对齐", xaxis_title=x_unit, yaxis_title=y_unit)
        figures[f"{key}_raw"] = _json(raw_fig)
        if not candidates:
            continue
        lower = max(float(points["x"].min()) for _, points, _ in candidates)
        upper = min(float(points["x"].max()) for _, points, _ in candidates)
        if lower >= upper:
            warnings.append(f"{kind}/{phase} 无共同横轴区间，未外推或生成参考包络。")
            figures[f"{key}_aligned"] = _json(aligned_fig)
            continue
        grid = np.linspace(lower, upper, 200)
        reference, sampled = [], []
        for curve_id, points, metadata in candidates:
            values = np.interp(grid, points["x"], points["y"])
            aligned_frames.append(pd.DataFrame({"curve_id": curve_id, "kind": kind, "phase": phase, **metadata, "x": grid, "y": values, "x_unit": x_unit, "y_unit": y_unit}))
            sampled.append((curve_id, values, metadata))
            if str(metadata.get("result")) == reference_group:
                reference.append(values)
        if len(reference) >= 2:
            p05, p50, p95 = np.quantile(np.stack(reference), [.05, .5, .95], axis=0)
            envelope_frames.append(pd.DataFrame({"kind": kind, "phase": phase, "x": grid, "p05": p05, "median": p50, "p95": p95, "reference_curves": len(reference), "interpretation": "探索性参考包络，非工艺规格", "x_unit": x_unit, "y_unit": y_unit}))
            aligned_fig.add_trace(go.Scatter(x=grid, y=p95, mode="lines", line={"width": 0}, showlegend=False, hoverinfo="skip"))
            aligned_fig.add_trace(go.Scatter(x=grid, y=p05, fill="tonexty", fillcolor="rgba(54,116,232,.14)", mode="lines", line={"width": 0}, name="OK 5%—95% 参考范围（非规格）"))
            aligned_fig.add_trace(go.Scatter(x=grid, y=p50, line={"color": "#244ea1", "width": 2}, name="参考中位数"))
            for curve_id, values, metadata in sampled:
                if str(metadata.get("result")) == reference_group:
                    continue
                outside = (values < p05) | (values > p95)
                indices = np.flatnonzero(outside)
                for segment in np.split(indices, np.flatnonzero(np.diff(indices) > 1) + 1):
                    if len(segment) >= 3:
                        abnormal.append({"curve_id": curve_id, "kind": kind, "phase": phase, **metadata, "x_start": float(grid[segment[0]]), "x_end": float(grid[segment[-1]]), "outside_fraction": float(outside.mean()), "interpretation": "偏离参考包络，不能作为独立不合格判定"})
        else:
            warnings.append(f"{kind}/{phase} 的 {reference_group} 参考曲线不足两条，未生成统计包络。")
        figures[f"{key}_aligned"] = _json(aligned_fig)

    features_df = pd.DataFrame(features)
    tables = {"features": features_df, "aligned_curves": pd.concat(aligned_frames, ignore_index=True) if aligned_frames else pd.DataFrame(), "reference_envelopes": pd.concat(envelope_frames, ignore_index=True) if envelope_frames else pd.DataFrame(), "abnormal_segments": pd.DataFrame(abnormal)}
    if not features_df.empty:
        tables["group_summary"] = features_df.groupby(["kind", "phase", "result"], dropna=False).agg(curves=("curve_id", "nunique"), peak_mean=("peak", "mean"), peak_std=("peak", "std"), end_mean=("end_value", "mean"), fluctuation_mean=("fluctuation", "mean")).reset_index()
        for (kind, phase), feature_group in features_df.groupby(["kind", "phase"]):
            figures[f"{kind}_{phase}_peaks"] = _json(px.box(feature_group, x="result", y="peak", color="result", points="all", title=f"{kind} / {phase} · 峰值分布"))
    findings.append(f"共提取 {len(features_df):,} 条分阶段曲线特征，记录 {len(abnormal):,} 个偏离参考范围的连续区段。")
    return {"summary": f"完成 {data['curve_id'].nunique():,} 条曲线的单位检查、分阶段特征提取及 {alignment} 对齐；参考包络为探索性统计范围，非工艺规格。", "tables": tables, "figures": figures, "findings": findings, "warnings": list(dict.fromkeys(warnings))}

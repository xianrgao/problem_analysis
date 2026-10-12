"""Reproducible manufacturing statistics; associations never imply causation."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd
import plotly.express as px

DEFAULT_CONFIG = {"timezone": "Asia/Shanghai", "exit_station": "OP30", "normal_shift_output": 700, "trial_threshold": 100, "stopped_threshold": 5, "long_dwell_hours": 4, "concentration_threshold": .8, "component_parameters": {"轴承": ["PeakForce"], "螺栓": ["FinalTorque"]}}


def _times(series: pd.Series, timezone: str = "Asia/Shanghai") -> pd.Series:
    values = pd.to_datetime(series, errors="coerce", format="mixed")
    if isinstance(values.dtype, pd.DatetimeTZDtype):
        values = values.dt.tz_convert(timezone).dt.tz_localize(None)
    return values


def shift_start(series: pd.Series, timezone: str = "Asia/Shanghai") -> pd.Series:
    """07:00/19:00 inclusive starts; pre-07:00 belongs to previous night."""
    values = _times(series, timezone)
    return (values - pd.Timedelta(hours=7)).dt.floor("12h") + pd.Timedelta(hours=7)


def first_bindings(bindings: pd.DataFrame) -> pd.DataFrame:
    if bindings.empty:
        return bindings.copy()
    needed = {"tg", "component", "bound_at"}
    if not needed.issubset(bindings.columns):
        raise ValueError(f"绑定数据缺少字段：{sorted(needed-set(bindings.columns))}")
    result = bindings.copy()
    if "position" not in result:
        result["position"] = "default"
    result["position"] = result["position"].fillna("default")
    result["_time"] = _times(result["bound_at"])
    if result["_time"].isna().any():
        raise ValueError("绑定数据存在无法解析的时间，不能确定首次绑定")
    if result[["tg", "component"]].isna().any().any():
        raise ValueError("绑定数据的 TG 或零件名称为空")
    order = ["_time"] + (["event_id"] if "event_id" in result else [])
    return result.sort_values(order, kind="stable").drop_duplicates(["tg", "component", "position"], keep="first").drop(columns="_time")


def rewrite_bindings(nc: pd.DataFrame, bindings: pd.DataFrame) -> pd.DataFrame:
    """Append earliest binding per TG/component/position, never multiply rows.

    Each installation position gets prefixed columns. Original NC columns are
    preserved, including row order and index; collisions are rejected.
    """
    if "tg" not in nc:
        raise ValueError("待重写数据缺少 tg 关联键")
    original_index = nc.index
    result = nc.reset_index(drop=True).copy()
    first = first_bindings(bindings)
    if first.empty:
        result["binding_matched"] = False
        result.index = original_index
        return result
    seen: set[str] = set()
    match_columns = []
    for (component, position), group in first.groupby(["component", "position"], dropna=False, sort=True):
        prefix = re.sub(r"[^\w]+", "_", f"{component}_{position}").strip("_")
        if prefix in seen:
            raise ValueError("零件/安装位置规范化后发生列名前缀冲突")
        seen.add(prefix)
        columns = [c for c in ["pn", "batch", "supplier", "bound_at", "event_id"] if c in group]
        renamed = {column: f"{prefix}_{column}" for column in columns}
        if set(renamed.values()) & set(result.columns):
            raise ValueError("目标数据已有绑定列，请选择未重写的原始版本或显式处理重写冲突")
        lookup = group[["tg"] + columns].rename(columns=renamed)
        result = result.merge(lookup, on="tg", how="left", validate="many_to_one", sort=False)
        match_columns.append(renamed["bound_at"])
    result["binding_matched"] = result[match_columns].notna().any(axis=1)
    result.index = original_index
    result.attrs["binding_keys"] = ["tg", "component", "position"]
    result.attrs["unmatched_rows"] = int((~result["binding_matched"]).sum())
    return result


def _measurement_frame(measurements: pd.DataFrame, visits: pd.DataFrame) -> pd.DataFrame:
    frame = measurements.copy()
    need = [c for c in ["tg", "station_id", "entered_at", "exited_at"] if c not in frame and c in visits]
    if need and "visit_id" in frame and "visit_id" in visits:
        frame = frame.merge(visits[["visit_id"] + need].drop_duplicates("visit_id"), on="visit_id", how="left", validate="many_to_one")
    if "occurred_at" not in frame and "entered_at" in frame:
        frame["occurred_at"] = frame["entered_at"]
    return frame


def _figure(frame: pd.DataFrame, kind: str, x: str, y: str, color: str | None = None, title: str = "") -> dict:
    chart = px.line(frame, x=x, y=y, color=color, markers=True, title=title) if kind == "line" else px.bar(frame, x=x, y=y, color=color, barmode="group", title=title)
    chart.update_layout(template="plotly_white", font={"family": "Segoe UI, Microsoft YaHei, sans-serif"}, colorway=["#3674e8", "#20a093", "#e19b3d", "#bd6083"], margin={"l": 50, "r": 24, "t": 50, "b": 45})
    # JSON roundtrip produces plain arrays, not numpy typed buffers.
    import json
    return json.loads(chart.to_json())


def analyze_nc(nc: pd.DataFrame, visits: pd.DataFrame, measurements: pd.DataFrame, bindings: pd.DataFrame, config: dict | None = None) -> dict:
    """Analyze only provided evidence. Caller is responsible for scope selection.

    Measurements must include all OK and NC records in the comparison scope;
    tables warn when denominators are unavailable. Findings describe evidence,
    never claim root cause or equipment exclusion.
    """
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    tables, figures, findings, warnings = {}, {}, [], []
    frame = nc.copy()
    if "tg" not in frame:
        raise ValueError("NC 明细需要 tg 字段")
    valid_tg = frame["tg"].dropna()
    nc_count = int(valid_tg.nunique())
    event_count = int(frame["nc_id"].nunique()) if "nc_id" in frame else len(frame)
    findings.append(f"当前范围包含 {nc_count:,} 件不良工件、{event_count:,} 条 NC 事件；两种计数分别统计。")
    launch = "launched_at"
    if launch not in frame and not visits.empty and {"tg", "entered_at"}.issubset(visits):
        first_pass = visits.assign(_launch=_times(visits["entered_at"], cfg["timezone"])).groupby("tg")["_launch"].min()
        frame[launch] = frame["tg"].map(first_pass)
        warnings.append("缺少工件上线时间，使用所提供过站记录的最早时间；若过站范围不完整，开班判断仅作线索。")
    if launch in frame:
        pieces = frame[["tg", launch]].drop_duplicates("tg").copy()
        pieces[launch] = _times(pieces[launch], cfg["timezone"])
        pieces["shift_start"] = shift_start(pieces[launch], cfg["timezone"])
        pieces["startup"] = (pieces[launch] - pieces["shift_start"]).dt.total_seconds().between(0, 3599.999)
        pieces["shift"] = np.where(pieces["shift_start"].dt.hour == 7, "白班", "夜班")
        tables["startup_distribution"] = pieces.groupby(["shift", "startup"], dropna=False).agg(nc_pieces=("tg", "nunique")).reset_index()
        startup_count = int(pieces["startup"].sum())
        findings.append(f"不良工件中 {startup_count:,} 件在开班首小时上线，占 {startup_count/max(1,nc_count):.1%}；需结合各时段投入量判断风险。")
    else:
        pieces = pd.DataFrame()
        warnings.append("缺少上线/过站时间，未生成开班分析。")

    if {"tg", "station_id", "entered_at", "exited_at"}.issubset(visits):
        flow = visits.copy()
        flow["entered_at"] = _times(flow["entered_at"], cfg["timezone"])
        flow["exited_at"] = _times(flow["exited_at"], cfg["timezone"])
        flow = flow.sort_values(["tg", "entered_at", "exited_at"], kind="stable")
        flow["previous_station"] = flow.groupby("tg")["station_id"].shift(1)
        flow["previous_exit"] = flow.groupby("tg")["exited_at"].shift(1)
        flow["dwell_hours"] = (flow["entered_at"] - flow["previous_exit"]).dt.total_seconds() / 3600
        long_dwell = flow[(flow["dwell_hours"] > cfg["long_dwell_hours"]) & flow["tg"].isin(valid_tg)].copy()
        tables["long_dwell"] = long_dwell[["tg", "previous_station", "station_id", "previous_exit", "entered_at", "dwell_hours"]]
        if (flow["dwell_hours"] < 0).any():
            warnings.append("部分过站记录时间重叠，已保留原始时间；负间隔未计入滞留。")
        findings.append(f"发现 {long_dwell['tg'].nunique():,} 件不良工件存在跨工站间隔超过 {cfg['long_dwell_hours']} 小时，需结合停线和返工记录核实。")
        output = flow[flow["station_id"] == cfg["exit_station"]].copy()
        output["shift_start"] = shift_start(output["exited_at"], cfg["timezone"])
        if not output.empty:
            counts = output.groupby("shift_start")["tg"].nunique()
            counts = counts.reindex(pd.date_range(counts.index.min(), counts.index.max(), freq="12h"), fill_value=0)
            shifts = counts.rename_axis("shift_start").reset_index(name="output")
            shifts["classification"] = np.select([shifts["output"] < cfg["stopped_threshold"], shifts["output"] < cfg["trial_threshold"], shifts["output"] < cfg["normal_shift_output"]], ["停产", "验线", "未达正常阈值"], default="正常")
            shifts["previous_output"] = shifts["output"].shift(1)
            shifts["coverage_note"] = "仅对所提供范围计数；首末班需核验时间覆盖完整性"
            tables["shift_output"] = shifts
            if not pieces.empty:
                tables["nc_shift_context"] = pieces.merge(shifts.drop(columns="coverage_note"), on="shift_start", how="left", validate="many_to_one")
            figures["shift_output"] = _figure(shifts, "line", "shift_start", "output", title="出口工站班产（去重 TG）")
        else:
            warnings.append(f"所提供过站数据不包含出口工站 {cfg['exit_station']}，未将其他工站记录当作班产。")

    measured = _measurement_frame(measurements, visits)
    has_denominator = {"tg", "result", "parameter"}.issubset(measured)
    if has_denominator and "occurred_at" in measured:
        measured["date"] = _times(measured["occurred_at"], cfg["timezone"]).dt.floor("D")
        daily_all = measured.groupby(["date", "parameter"], dropna=False)["tg"].nunique().rename("tested_pieces")
        daily_nc = measured[measured["result"].eq("NC")].groupby(["date", "parameter"], dropna=False)["tg"].nunique().rename("nc_pieces")
        trend = pd.concat([daily_all, daily_nc], axis=1).fillna({"nc_pieces": 0}).reset_index()
        trend["nc_rate"] = trend["nc_pieces"] / trend["tested_pieces"].replace(0, np.nan)
        tables["daily_trend"] = trend
        figures["daily_trend"] = _figure(trend, "line", "date", "nc_rate", "parameter", "不良率趋势（包含同期检测量）")
        for parameter, series in trend.groupby("parameter"):
            if len(series) < 4:
                continue
            series = series.sort_values("date")
            size = max(1, len(series)//3)
            early, recent = series.iloc[:size], series.iloc[-size:]
            rate_early = early["nc_pieces"].sum()/max(1, early["tested_pieces"].sum())
            rate_recent = recent["nc_pieces"].sum()/max(1, recent["tested_pieces"].sum())
            findings.append(f"{parameter} 首段与末段加权不良率分别为 {rate_early:.2%}、{rate_recent:.2%}；变化形态需结合变化点和过程参数验证，不能单凭趋势归因设备。")
        if not measured["result"].eq("OK").any():
            warnings.append("比较数据未包含合格记录，当前不良率可能来自仅不良样本，不能代表产线风险。")
    else:
        warnings.append("缺少包含同期合格件的检测明细，无法计算有效不良率或批次风险。")

    if not bindings.empty:
        first = first_bindings(bindings)
        tables["nc_with_bindings"] = rewrite_bindings(frame, first)
        tables["first_bindings"] = first
        risk_parts, concentration_parts = [], []
        for (component, position), component_binding in first.groupby(["component", "position"], dropna=False):
            parameters = cfg["component_parameters"].get(component)
            selected_nc = frame[frame["parameter"].isin(parameters)] if parameters and "parameter" in frame else frame
            known = selected_nc[["tg"]].drop_duplicates().merge(component_binding, on="tg", how="left", validate="one_to_one")
            total_nc = known["tg"].nunique()
            for dimension in ["batch", "supplier"]:
                if dimension not in component_binding:
                    continue
                conc = known.groupby(dimension, dropna=False)["tg"].nunique().reset_index(name="nc_pieces").rename(columns={dimension: "group"})
                conc["nc_share"] = conc["nc_pieces"] / max(1, total_nc)
                conc["component"], conc["position"], conc["dimension"] = component, position, dimension
                conc["strong_concentration"] = conc["nc_share"] > cfg["concentration_threshold"]
                concentration_parts.append(conc)
                for _, row in conc[conc["strong_concentration"] & conc["group"].notna()].iterrows():
                    findings.append(f"{component} {dimension}={row['group']} 占相关 NC 工件的 {row['nc_share']:.1%}，达到集中度线索阈值；须比较投入量及其他组不良率，尚不能确认根因。")
                if not has_denominator:
                    continue
                if not parameters:
                    warnings.append(f"{component} 没有 task/parameter 映射，只展示集中度，未混合不相关参数计算批次风险。")
                    continue
                relevant = measured[measured["parameter"].isin(parameters)]
                joined = relevant.merge(component_binding[["tg", dimension]], on="tg", how="left", validate="many_to_one")
                all_count = joined.groupby(["parameter", dimension], dropna=False)["tg"].nunique().rename("tested_pieces")
                bad_count = joined[joined["result"].eq("NC")].groupby(["parameter", dimension], dropna=False)["tg"].nunique().rename("nc_pieces")
                risk = pd.concat([all_count, bad_count], axis=1).fillna({"nc_pieces": 0}).reset_index().rename(columns={dimension: "group"})
                if risk.empty:
                    continue
                risk["nc_rate"] = risk["nc_pieces"] / risk["tested_pieces"]
                totals = risk.groupby("parameter")[["tested_pieces", "nc_pieces"]].transform("sum")
                other_n = totals["tested_pieces"] - risk["tested_pieces"]
                risk["other_nc_rate"] = (totals["nc_pieces"]-risk["nc_pieces"])/other_n.replace(0, np.nan)
                risk["risk_ratio"] = risk["nc_rate"] / risk["other_nc_rate"].replace(0, np.nan)
                risk["component"], risk["position"], risk["dimension"] = component, position, dimension
                risk["evidence_level"] = "关联观察，未控制混杂因素"
                risk_parts.append(risk)
        if concentration_parts:
            tables["batch_concentration"] = pd.concat(concentration_parts, ignore_index=True)
        if risk_parts:
            risk = pd.concat(risk_parts, ignore_index=True)
            tables["batch_risk"] = risk
            figures["batch_risk"] = _figure(risk[risk["dimension"].eq("batch")].assign(group=lambda d: d["group"].fillna("未绑定")), "bar", "group", "nc_rate", "parameter", "批次不良率（按相关参数及投入量计算）")
        else:
            warnings.append("无可用的同期批次分母，批次集中度不能用于判断风险高低。")
    else:
        warnings.append("未提供首次绑定数据，批次及供应商分析尚未完成。")

    quality = []
    for name, data in [("nc", frame), ("visits", visits), ("measurements", measured), ("bindings", bindings)]:
        quality.append({"dataset": name, "rows": len(data), "duplicate_rows": int(data.duplicated().sum()), "missing_cells": int(data.isna().sum().sum())})
    tables["data_quality"] = pd.DataFrame(quality)
    warnings = list(dict.fromkeys(warnings))
    summary = f"已分析 {nc_count:,} 件不良工件、{event_count:,} 条 NC 事件，完成可用数据范围内的开班、班产、滞留、趋势及批次比较。所有发现为调查证据，尚未确认根因。"
    return {"summary": summary, "tables": tables, "figures": figures, "findings": findings, "warnings": warnings}

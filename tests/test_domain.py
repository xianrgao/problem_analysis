"""Domain checks target evidence integrity and manufacturing boundary cases."""
import sqlite3

import numpy as np
import pandas as pd
import pytest

from line_engineer.domain.analysis import analyze_nc, rewrite_bindings, shift_start
from line_engineer.domain.curves import analyze_curves, make_chart
from line_engineer.domain.demo import generate_demo
from line_engineer.domain.sources import DemoMOS, DemoSplunk, SourceQueryError


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    path = tmp_path_factory.mktemp("domain_demo")
    generate_demo(path, days=3, seed=42)
    return path


def test_demo_relational_and_reproducible(demo_dir):
    manifest = generate_demo(demo_dir, days=3, seed=42)
    with sqlite3.connect(demo_dir / "mos.sqlite") as db:
        assert len(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()) == 5
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        assert db.execute("SELECT COUNT(*) FROM measurements WHERE result='NC'").fetchone()[0] == manifest["table_counts"]["nc_events"]
    assert manifest["table_counts"]["workpieces"] > 4200
    assert manifest["curve_count"] > 0
    assert manifest["is_demo"] is True


@pytest.mark.parametrize("sql", ["DELETE FROM workpieces", "ATTACH DATABASE ':memory:' AS injected", "PRAGMA writable_schema=ON", "SELECT 1; DELETE FROM workpieces", "SELECT load_extension('malicious')", "WITH x AS (SELECT 1) DELETE FROM workpieces"])
def test_mos_rejects_mutations(demo_dir, sql):
    with pytest.raises(SourceQueryError):
        DemoMOS(demo_dir).query(sql)


def test_mos_executes_joins_and_rejects_duplicate_columns(demo_dir):
    adapter = DemoMOS(demo_dir)
    result = adapter.query("SELECT n.nc_id,v.tg,v.station_id,m.parameter,m.value,w.launched_at FROM nc_events n JOIN measurements m ON n.measurement_id=m.measurement_id JOIN visits v ON m.visit_id=v.visit_id JOIN workpieces w ON v.tg=w.tg LIMIT 20")
    assert len(result) == 20
    assert result["station_id"].isin(["OP20", "OP30"]).all()
    with pytest.raises(SourceQueryError, match="重复列名"):
        adapter.query("SELECT 1 AS a, 2 AS a")


def test_mos_query_deadline_and_no_silent_truncation(demo_dir):
    with pytest.raises(SourceQueryError, match="时限"):
        DemoMOS(demo_dir, timeout_seconds=.001).query("WITH RECURSIVE numbers(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM numbers) SELECT sum(n) FROM numbers")
    with pytest.raises(SourceQueryError, match="未静默截断"):
        DemoMOS(demo_dir, max_rows=2).query("SELECT * FROM workpieces")


def test_splunk_preserves_original_binding(demo_dir):
    source = DemoSplunk(demo_dir)
    first = source.query("bindings", {"tg": "TG00000001", "component": "轴承"})
    all_events = source.query("bindings", {"tg": "TG00000001", "component": "轴承", "first_only": False})
    assert len(first) == 1 and len(all_events) == 2
    assert first.iloc[0]["batch"] != "BRG-REWORK"
    assert source.query("changes", {"station_id": "OP20"}).iloc[0]["structure"] == "压装导向套"
    with pytest.raises(SourceQueryError):
        source.query("bindings", {"silently_ignore": "bad"})


def test_shift_crosses_midnight_and_boundaries():
    values = pd.Series(["2026-10-12T06:59:59", "2026-10-12T07:00:00", "2026-10-12T18:59:59", "2026-10-12T19:00:00", "2026-10-13T00:15:00"])
    actual = shift_start(values).dt.strftime("%m-%d %H:%M").tolist()
    assert actual == ["10-11 19:00", "10-12 07:00", "10-12 07:00", "10-12 19:00", "10-12 19:00"]


def test_rewrite_does_not_multiply_rows_and_keeps_first_binding():
    nc = pd.DataFrame({"tg": ["A", "A", "B", "C"], "nc_id": [1, 2, 3, 4]}, index=[8, 2, 1, 9])
    bindings = pd.DataFrame([
        {"tg": "A", "component": "轴承", "position": "L", "batch": "original", "bound_at": "2026-01-01T07:00:00"},
        {"tg": "A", "component": "轴承", "position": "L", "batch": "replacement", "bound_at": "2026-01-01T08:00:00"},
        {"tg": "A", "component": "轴承", "position": "R", "batch": "right", "bound_at": "2026-01-01T07:00:00"},
    ])
    result = rewrite_bindings(nc, bindings)
    assert len(result) == 4 and result.index.tolist() == nc.index.tolist()
    assert result.iloc[0]["轴承_L_batch"] == "original"
    assert result.iloc[0]["轴承_R_batch"] == "right"
    assert result["nc_id"].tolist() == nc["nc_id"].tolist()
    assert result.attrs["unmatched_rows"] == 2


def test_high_nc_share_does_not_imply_higher_risk():
    # Majority supplier: 900 tested, 90 NC. Minority: 100 tested, 20 NC.
    # Majority owns 81.8% of all NC but has only half the minority's NC rate.
    records, binds = [], []
    for number in range(1000):
        majority = number < 900
        failed = number < 90 or 900 <= number < 920
        records.append({"tg": str(number), "parameter": "PeakForce", "result": "NC" if failed else "OK", "occurred_at": "2026-10-12T08:00:00"})
        binds.append({"tg": str(number), "component": "轴承", "position": "bearing_1", "batch": "major" if majority else "minor", "supplier": "major" if majority else "minor", "bound_at": "2026-10-12T07:00:00"})
    measurements = pd.DataFrame(records)
    result = analyze_nc(measurements[measurements["result"].eq("NC")], pd.DataFrame(), measurements, pd.DataFrame(binds))
    concentration = result["tables"]["batch_concentration"]
    major = concentration[(concentration["dimension"] == "batch") & (concentration["group"] == "major")].iloc[0]
    assert major["strong_concentration"]
    risk = result["tables"]["batch_risk"]
    major_risk = risk[(risk["dimension"] == "batch") & (risk["group"] == "major")].iloc[0]
    assert major_risk["nc_rate"] == pytest.approx(.1)
    assert major_risk["risk_ratio"] == pytest.approx(.5)
    assert "尚未确认根因" in result["summary"]


def test_dwell_strict_four_hour_threshold():
    visits = pd.DataFrame([
        {"tg": "A", "station_id": "OP10", "entered_at": "2026-01-01 07:00", "exited_at": "2026-01-01 07:01"},
        {"tg": "A", "station_id": "OP20", "entered_at": "2026-01-01 11:01", "exited_at": "2026-01-01 11:02"},
        {"tg": "B", "station_id": "OP10", "entered_at": "2026-01-01 07:00", "exited_at": "2026-01-01 07:01"},
        {"tg": "B", "station_id": "OP20", "entered_at": "2026-01-01 11:01:01", "exited_at": "2026-01-01 11:02"},
    ])
    result = analyze_nc(pd.DataFrame({"tg": ["A", "B"]}), visits, pd.DataFrame(), pd.DataFrame())
    assert result["tables"]["long_dwell"]["tg"].tolist() == ["B"]


def curve_fixture():
    rows = []
    for curve, count, offset, group in [("A", 20, 1., "OK"), ("B", 25, 3., "OK"), ("C", 18, 2., "NC")]:
        for index, x in enumerate(np.linspace(0, 10, count)):
            rows.append({"curve_id": curve, "sample_index": index, "x": x+offset, "y": x*(1.5 if group == "NC" else 1), "x_unit": "mm", "y_unit": "kN", "kind": "press", "phase": "loading", "result": group})
    return pd.DataFrame(rows)


def test_curve_alignment_features_and_reference_not_spec():
    output = analyze_curves(curve_fixture())
    features = output["tables"]["features"]
    assert features["peak"].tolist() == [10., 10., 15.]
    aligned = output["tables"]["aligned_curves"]
    assert aligned.groupby("curve_id")["x"].min().eq(0).all()
    assert len(output["tables"]["reference_envelopes"]) == 200
    assert len(output["tables"]["abnormal_segments"]) > 0
    assert "非工艺规格" in output["summary"]


def test_curve_units_and_roundtrip_must_not_be_mixed():
    data = curve_fixture()
    data.loc[0, "y_unit"] = "N"
    with pytest.raises(ValueError, match="单位冲突"):
        analyze_curves(data)
    data = curve_fixture()
    data.loc[5, "x"] = -5
    with pytest.raises(ValueError, match="往返过程"):
        analyze_curves(data)


def test_final_values_never_turn_into_fabricated_curves():
    output = analyze_curves(pd.DataFrame({"value": [10., 11., 9.], "result": ["OK", "OK", "NC"]}))
    assert "没有过程采样点" in output["summary"]
    assert "aligned_curves" not in output["tables"]


def test_demo_end_to_end_analysis_and_curves(demo_dir):
    source = DemoMOS(demo_dir)
    nc = source.query("SELECT n.*,v.tg,v.station_id,m.task,m.parameter,m.value,m.result,w.launched_at FROM nc_events n JOIN measurements m ON n.measurement_id=m.measurement_id JOIN visits v ON m.visit_id=v.visit_id JOIN workpieces w ON w.tg=v.tg")
    output = analyze_nc(nc, source.query("SELECT * FROM visits"), source.query("SELECT * FROM measurements"), DemoSplunk(demo_dir).query("bindings"))
    assert {"shift_output", "batch_risk", "daily_trend", "nc_with_bindings"}.issubset(output["tables"])
    assert len(output["tables"]["nc_with_bindings"]) == len(nc)
    curves = analyze_curves(pd.read_parquet(demo_dir / "curves.parquet"))
    assert len(curves["tables"]["features"]) > 0
    assert "data" in make_chart(output["tables"]["batch_risk"], "bar", "group", "nc_rate")

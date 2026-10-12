"""Reproducible demo factory. Five MOS tables; log and curve sources are separate.

The fixed anchor date deliberately makes tests and the demo reproducible. All
timestamps are plant-local Asia/Shanghai time, rather than the computer timezone.
"""
from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

DEMO_VERSION = 2


def _stamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def generate_demo(data_dir: Path, days: int = 90, seed: int = 42) -> dict:
    """Create a deterministic, relational MOS and external log/curve dataset.

    Existing data is reused only when its manifest matches this generator and
    parameters. Rebuilding replaces generated files, never application state.
    """
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    if not 1 <= days <= 366:
        raise ValueError("演示天数应在 1 至 366 天之间")
    manifest_path = data_dir / "manifest.json"
    required = ["mos.sqlite", "bindings.jsonl", "changes.jsonl", "logs.jsonl", "curves.parquet"]
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (existing.get("generator_version"), existing.get("days"), existing.get("seed")) == (DEMO_VERSION, days, seed) and all((data_dir / name).exists() for name in required):
            return existing

    rng = np.random.default_rng(seed)
    start = datetime(2026, 10, 13, 7) - timedelta(days=days)
    workpieces, visits, measurements, ncs = [], [], [], []
    bindings, logs, curves = [], [], []
    shift_counts = []
    curve_examples: dict[tuple[str, str], int] = {}
    stations = [("OP10", "A区", "零件上线", 10), ("OP20", "A区", "轴承压装", 20), ("OP30", "A区", "螺栓拧紧与下线", 30)]
    for day in range(days):
        for shift in range(2):
            shift_start = start + timedelta(days=day, hours=shift * 12)
            # Deliberate low-output shifts, while normal shifts exceed 700 units.
            count = 2 if day % 31 == 9 and shift == 1 else (68 if day % 23 == 7 and shift == 0 else 720 + int(rng.integers(0, 45)))
            shift_counts.append({"shift_start": _stamp(shift_start), "shift": "白班" if shift == 0 else "夜班", "count": count, "class": "停产" if count < 5 else "验线" if count < 100 else "正常"})
            for item in range(count):
                tg = f"TG{len(workpieces) + 1:08d}"
                # Finish before the next shift; latest launch is 10h40 after start.
                launched = shift_start + timedelta(seconds=item * (10.65 * 3600 / count))
                workpieces.append((tg, "E-Drive-A", _stamp(launched)))
                suspicious = day >= int(days * .65) and rng.random() < .22
                batch = "BRG-2609-BAD" if suspicious else f"BRG-2608-{1 + day // 14:02d}"
                supplier = "SUP-B" if suspicious else "SUP-A"
                # A high-volume supplier dominates failures too but is not higher risk.
                bolt_supplier = "SUP-MAJOR" if rng.random() < .92 else "SUP-MINOR"
                bolt_batch = f"BLT-26{8 + day // 31:02d}-{bolt_supplier[-3:]}"
                for component, pn, position, component_batch, component_supplier in [("轴承", "PN-BRG-001", "bearing_1", batch, supplier), ("螺栓", "PN-BLT-001", "bolt_1", bolt_batch, bolt_supplier)]:
                    binding = {"event_id": f"B{len(bindings)+1:09d}", "tg": tg, "station_id": "OP10", "component": component, "pn": pn, "position": position, "batch": component_batch, "supplier": component_supplier, "bound_at": _stamp(launched + timedelta(seconds=12)), "kind": "binding"}
                    bindings.append(binding)
                    if item == 0 and day % 15 == 0 and component == "轴承":
                        bindings.append({**binding, "event_id": f"B{len(bindings)+1:09d}", "batch": "BRG-REWORK", "bound_at": _stamp(launched + timedelta(hours=1))})
                start_up = (launched - shift_start).total_seconds() < 3600
                stalled = (day % 23 == 7 or day % 31 == 10) and item < 12
                delay = timedelta(hours=4.6) if stalled else timedelta(0)
                press_at = launched + timedelta(minutes=8) + delay
                torque_at = press_at + timedelta(minutes=6)
                completed = torque_at + timedelta(minutes=5)
                visit_ids = []
                for station, entered in zip(stations, [launched, press_at, torque_at, completed]):
                    visit_id = len(visits) + 1
                    visits.append((visit_id, tg, station[0], _stamp(entered), _stamp(entered + timedelta(seconds=24))))
                    visit_ids.append(visit_id)
                gradual = max(0, day / max(1, days - 1) - .25)
                p_press = .005 + gradual * .018 + (.025 if start_up else 0) + (.17 if suspicious else 0) + (.13 if stalled else 0)
                p_torque = .009 + (.035 if day >= int(days * .8) else 0)
                press_nc, torque_nc = rng.random() < p_press, rng.random() < p_torque
                press_value = float(rng.normal(18.8 if press_nc else 12.6 + gradual * .35, .9 if press_nc else .6))
                torque_value = float(rng.normal(30.6 if torque_nc else 40.5, 1.0 if torque_nc else 1.15))
                for visit_id, task, parameter, value, unit, lower, upper, failed, occurred in [(visit_ids[1], "PressFit", "PeakForce", press_value, "kN", 10., 16., press_nc, press_at), (visit_ids[2], "Tightening", "FinalTorque", torque_value, "N·m", 35., 46., torque_nc, torque_at)]:
                    mid = len(measurements) + 1
                    result = "NC" if failed else "OK"
                    measurements.append((mid, visit_id, task, parameter, round(value, 4), unit, lower, upper, result))
                    if failed:
                        ncs.append((len(ncs)+1, mid, "HIGH_FORCE" if task == "PressFit" else "LOW_TORQUE", _stamp(occurred)))
                    kind = "press" if task == "PressFit" else "torque"
                    quota_key = (kind, result)
                    # Cover early/late dates, OK/NC and batches without huge curve files.
                    if day % max(1, days // 12) == 0 and curve_examples.get(quota_key, 0) < 50:
                        curve_examples[quota_key] = curve_examples.get(quota_key, 0) + 1
                        length = int(rng.integers(70, 100))
                        x = np.linspace(0, 24 if kind == "press" else 180, length)
                        y = value * (1 / (1 + np.exp(-((x / x.max()) - .55) * 10)))
                        y += rng.normal(0, .06 if kind == "press" else .13, length)
                        if failed and kind == "press":
                            y += 1.8 * np.exp(-((x - 14) / 1.4) ** 2)
                        for index, (xx, yy) in enumerate(zip(x, y)):
                            curves.append({"curve_id": f"{tg}_{kind}", "tg": tg, "station_id": "OP20" if kind == "press" else "OP30", "kind": kind, "phase": "loading", "sample_index": index, "x": float(xx), "y": float(yy), "x_unit": "mm" if kind == "press" else "deg", "y_unit": unit, "result": result, "batch": batch if kind == "press" else bolt_batch, "supplier": supplier if kind == "press" else bolt_supplier, "occurred_at": _stamp(occurred)})
                if item < 2:
                    logs.append({"event_id": f"L{len(logs)+1:06d}", "kind": "operation", "tg": tg, "station_id": "OP20", "occurred_at": _stamp(press_at), "message": "模拟压装循环完成", "signal": "cycle_complete", "value": press_value})

    db_tmp = data_dir / "mos.building.sqlite"
    if db_tmp.exists():
        db_tmp.unlink()
    with closing(sqlite3.connect(db_tmp)) as db:
        db.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
        db.executemany("INSERT INTO workpieces VALUES (?,?,?)", workpieces)
        db.executemany("INSERT INTO stations VALUES (?,?,?,?)", stations)
        db.executemany("INSERT INTO visits VALUES (?,?,?,?,?)", visits)
        db.executemany("INSERT INTO measurements VALUES (?,?,?,?,?,?,?,?,?)", measurements)
        db.executemany("INSERT INTO nc_events VALUES (?,?,?,?)", ncs)
        db.commit()
    db_tmp.replace(data_dir / "mos.sqlite")
    changes = [
        {"event_id": "C001", "kind": "change", "station_id": "OP20", "occurred_at": _stamp(start + timedelta(days=int(days*.5))), "structure": "压装导向套", "change_type": "inspection", "message": "模拟点检：导向套间隙接近维护阈值；尚未证实与不良的因果关系"},
        {"event_id": "C002", "kind": "change", "station_id": "OP30", "occurred_at": _stamp(start + timedelta(days=int(days*.8))), "structure": "拧紧轴", "change_type": "recipe_update", "message": "模拟配方维护记录：需现场核验参数版本与审批记录"},
    ]
    for name, rows in [("bindings", bindings), ("changes", changes), ("logs", logs)]:
        with (data_dir / f"{name}.jsonl").open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    pd.DataFrame(curves).to_parquet(data_dir / "curves.parquet", index=False)
    manifest = {"generator_version": DEMO_VERSION, "is_demo": True, "days": days, "seed": seed, "timezone": "Asia/Shanghai", "start": _stamp(start), "end": max(row[4] for row in visits), "table_counts": dict(zip(["workpieces", "stations", "visits", "measurements", "nc_events"], map(len, [workpieces, stations, visits, measurements, ncs]))), "curve_count": len({r["curve_id"] for r in curves}), "curve_rows": len(curves), "binding_count": len(bindings), "shifts": shift_counts, "scenarios": ["开班集中", "验线与停产", "跨工站滞留超过4小时", "压装逐渐恶化", "拧紧突然升高", "轴承批次风险", "螺栓供应商高占比但非高风险"], "files": {name: str(data_dir / name) for name in required}}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest

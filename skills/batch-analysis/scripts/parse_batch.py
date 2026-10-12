"""Parse only the explicit synthetic formats; never infer a supplier."""
from __future__ import annotations

import re


def parse_batch(value: object, pn: str) -> dict:
    result = {"raw_batch": value, "parse_status": "unknown_format", "rule_id": None}
    if value is None or not isinstance(value, str) or not value.strip():
        result["parse_status"] = "missing"
        return result
    match = re.fullmatch(r"(BRG|BLT)-(\d{2})(\d{2})-([A-Z0-9]+)", value)
    if not match:
        return result
    family, year_code, month_code, suffix = match.groups()
    expected = {"PN-BRG-001": "BRG", "PN-BLT-001": "BLT"}.get(pn)
    if expected != family:
        result["parse_status"] = "pn_mismatch" if expected else "unknown_pn"
        return result
    if not 1 <= int(month_code) <= 12:
        result["parse_status"] = "invalid_month"
        return result
    if family == "BRG" and (re.fullmatch(r"\d{2}", suffix) or suffix == "BAD"):
        rule = "demo-bearing-label-v1" if suffix == "BAD" else "demo-bearing-v1"
    elif family == "BLT" and suffix in {"JOR", "NOR"}:
        rule = "demo-bolt-v1"
    else:
        return result
    result.update(parse_status="parsed", rule_id=rule, family=family, year=2000 + int(year_code), month=int(month_code), suffix=suffix)
    return result

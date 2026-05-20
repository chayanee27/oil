#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Production builder for GitHub Actions / GitHub Pages.

User workflow:
- Edit/add one weekly form in forms/YYYY-Www.csv every Friday morning.
- This script collects forms/*.csv, validates, normalizes readings to kWh,
  appends a generated RAW database at data/weekly_readings.csv,
  and builds data/energy_db.json for dashboard display.

Main meter rule for dashboard display:
SubB.Code in {MDB, Main, SCB21}
"""
from __future__ import annotations

import csv
import json
import statistics
import re
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
FORMS_DIR = ROOT / "forms"

MAIN_CODES = {"MDB", "Main", "SCB21"}
DEPARTMENTS = ["สก.ชธธ.", "อบค.", "อบฟ.", "อบย.", "อรอ.", "อคม.", "อหข."]
GENERATED_READING_FIELDS = ["source_form", "reading_date", "week_id", "meter_id", "raw_reading", "raw_unit", "reader", "note"]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing input file: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def clean_unit(unit: str | None) -> str:
    u = (unit or "").strip().lower()
    if u in {"mwh", "mwhr", "mwh.", "เมกะวัตต์ชั่วโมง"}:
        return "MWh"
    if u in {"kwh", "kwhr", "kwh.", "หน่วย", "ยูนิต"}:
        return "kWh"
    return ""


def parse_date(value: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError("reading_date is blank")
    datetime.strptime(text, "%Y-%m-%d")
    return text


def parse_week_id(value: str, reading_date: str) -> str:
    text = (value or "").strip()
    if re.fullmatch(r"\d{4}-W\d{2}", text):
        return text
    dt = datetime.strptime(reading_date, "%Y-%m-%d")
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def normalize_by_unit(raw_value: float, unit: str | None, fallback_unit: str | None = None) -> float:
    unit_clean = clean_unit(unit) or clean_unit(fallback_unit) or "kWh"
    return raw_value * 1000 if unit_clean == "MWh" else raw_value


def choose_by_continuity(raw_value: float, raw_unit: str | None, previous_kwh: float | None, default_unit: str | None) -> tuple[float, list[str]]:
    """Normalize to kWh while guarding against mixed kWh/MWh entries.

    The UNIT column can be wrong. We evaluate declared/default, raw-as-kWh,
    and raw-as-MWh interpretations, then choose the most continuous reading.
    """
    flags: list[str] = []
    declared = normalize_by_unit(raw_value, raw_unit, default_unit)
    candidates = [("declared_or_default", declared), ("as_kwh", raw_value), ("as_mwh", raw_value * 1000)]

    uniq: list[tuple[str, float]] = []
    seen: set[float] = set()
    for label, val in candidates:
        key = round(val, 6)
        if key not in seen:
            seen.add(key)
            uniq.append((label, val))

    if previous_kwh is None:
        # First value has no continuity reference. Prefer declared/default.
        chosen_label, chosen = uniq[0]
        # Heuristic warning only; do not change value without baseline.
        if clean_unit(raw_unit) == "kWh" and clean_unit(default_unit) == "MWh":
            flags.append("UNIT_COLUMN_DIFFERS_FROM_METER_DEFAULT")
    else:
        scored: list[tuple[float, str, float]] = []
        for label, val in uniq:
            delta = val - previous_kwh
            score = abs(delta)
            if delta < 0:
                score += abs(delta) * 10 + 1_000_000
            if previous_kwh > 0 and delta > previous_kwh * 0.5:
                score += delta
            scored.append((score, label, val))
        scored.sort(key=lambda x: x[0])
        _, chosen_label, chosen = scored[0]

    if abs(chosen - declared) > max(1, abs(chosen) * 0.001):
        flags.append("UNIT_SUSPECT")
    if chosen_label == "as_mwh" and clean_unit(raw_unit) != "MWh":
        flags.append("AUTO_CONVERTED_MWH_TO_KWH")
    if chosen_label == "as_kwh" and clean_unit(raw_unit) == "MWh":
        flags.append("AUTO_TREATED_MWH_VALUE_AS_KWH")
    return chosen, sorted(set(flags))


def collect_forms(meter_by_id: dict[str, dict[str, str]], validation: dict[str, Any]) -> list[dict[str, Any]]:
    forms = sorted(p for p in FORMS_DIR.glob("*.csv") if not p.name.startswith("_"))
    rows_out: list[dict[str, Any]] = []
    seen: dict[tuple[str, str], str] = {}
    if not forms:
        validation["warnings"].append({"warning": "NO_WEEKLY_FORMS_FOUND", "folder": "forms/"})

    for form_path in forms:
        rows = read_csv(form_path)
        for row_no, r in enumerate(rows, start=2):
            meter_id = (r.get("meter_id") or "").strip()
            if not meter_id:
                continue
            raw = to_float(r.get("raw_reading"))
            # Blank rows are allowed in the current weekly form while data is being entered.
            if raw is None:
                continue
            if meter_id not in meter_by_id:
                validation["errors"].append({"file": str(form_path.relative_to(ROOT)), "row": row_no, "error": "UNKNOWN_METER_ID", "meter_id": meter_id})
                continue
            try:
                reading_date = parse_date(r.get("reading_date", ""))
            except ValueError as exc:
                validation["errors"].append({"file": str(form_path.relative_to(ROOT)), "row": row_no, "error": "INVALID_DATE", "detail": str(exc), "meter_id": meter_id})
                continue
            week_id = parse_week_id(r.get("week_id", ""), reading_date)
            key = (reading_date, meter_id)
            if key in seen:
                validation["errors"].append({
                    "file": str(form_path.relative_to(ROOT)), "row": row_no,
                    "error": "DUPLICATE_READING_FOR_SAME_DATE_AND_METER",
                    "meter_id": meter_id, "reading_date": reading_date,
                    "first_seen_in": seen[key],
                })
                continue
            seen[key] = str(form_path.relative_to(ROOT))
            m = meter_by_id[meter_id]
            raw_unit = (r.get("raw_unit") or m.get("default_unit") or "kWh").strip()
            rows_out.append({
                "source_form": str(form_path.relative_to(ROOT)),
                "reading_date": reading_date,
                "week_id": week_id,
                "meter_id": meter_id,
                "raw_reading": raw,
                "raw_unit": raw_unit,
                "reader": r.get("reader", ""),
                "note": r.get("note", ""),
            })
    rows_out.sort(key=lambda x: (x["reading_date"], meter_by_id.get(x["meter_id"], {}).get("sort_order", "999999"), x["meter_id"]))
    write_csv(DATA_DIR / "weekly_readings.csv", rows_out, GENERATED_READING_FIELDS)
    return rows_out


def build() -> dict[str, Any]:
    meters = read_csv(DATA_DIR / "meter_master.csv")
    allocations = read_csv(DATA_DIR / "department_allocations.csv")
    meter_by_id = {m.get("meter_id", "").strip(): m for m in meters if m.get("meter_id")}
    validation: dict[str, Any] = {"errors": [], "warnings": [], "stats": {}}
    readings = collect_forms(meter_by_id, validation)

    readings_by_meter: dict[str, list[dict[str, Any]]] = {}
    for row_no, r in enumerate(readings, start=2):
        meter_id = (r.get("meter_id") or "").strip()
        raw = to_float(r.get("raw_reading"))
        if not meter_id or raw is None:
            continue
        readings_by_meter.setdefault(meter_id, []).append(r)

    normalized_readings: list[dict[str, Any]] = []
    weekly_consumption: list[dict[str, Any]] = []

    for meter_id, rows in readings_by_meter.items():
        rows.sort(key=lambda x: x["reading_date"])
        previous_kwh: float | None = None
        previous_date: str | None = None
        recent_deltas: list[float] = []
        seen_dates: set[str] = set()
        for r in rows:
            flags: list[str] = []
            if r["reading_date"] in seen_dates:
                flags.append("DUPLICATE_DATE_FOR_METER")
            seen_dates.add(r["reading_date"])

            m = meter_by_id[meter_id]
            raw = to_float(r.get("raw_reading"))
            assert raw is not None
            kwh, unit_flags = choose_by_continuity(raw, r.get("raw_unit"), previous_kwh, m.get("default_unit"))
            flags.extend(unit_flags)
            delta: float | None = None if previous_kwh is None else kwh - previous_kwh
            if delta is not None:
                if delta < 0:
                    flags.append("NEGATIVE_DELTA")
                if len(recent_deltas) >= 4:
                    median_delta = statistics.median(recent_deltas[-8:])
                    if median_delta > 0 and delta > median_delta * 3:
                        flags.append("SPIKE_SUSPECT")
                # Warn when the reading gap is not roughly weekly.
                if previous_date:
                    d0 = datetime.strptime(previous_date, "%Y-%m-%d")
                    d1 = datetime.strptime(r["reading_date"], "%Y-%m-%d")
                    gap = (d1 - d0).days
                    if gap not in range(5, 10):
                        flags.append(f"NON_WEEKLY_GAP_{gap}_DAYS")

            subb_code = (m.get("subb_code") or "").strip()
            is_main = subb_code in MAIN_CODES or str(m.get("is_main", "")).strip().lower() == "true"
            norm_row = {
                **r,
                "b_code": m.get("b_code", ""),
                "subb_code": subb_code,
                "building_name": m.get("building_name", ""),
                "normalized_kwh": round(kwh, 3),
                "flags": sorted(set(flags)),
            }
            normalized_readings.append(norm_row)

            if delta is not None:
                weekly_consumption.append({
                    "week_start_date": previous_date,
                    "week_end_date": r["reading_date"],
                    "week_id": r.get("week_id", ""),
                    "meter_id": meter_id,
                    "b_code": m.get("b_code", ""),
                    "subb_code": subb_code,
                    "building_name": m.get("building_name", ""),
                    "is_main": is_main,
                    "kwh": round(max(delta, 0), 3),
                    "raw_delta_kwh": round(delta, 3),
                    "flags": sorted(set(flags)),
                })
                if delta >= 0:
                    recent_deltas.append(delta)
            previous_kwh = kwh
            previous_date = r["reading_date"]

    allocation_rows: list[dict[str, Any]] = []
    for row_no, a in enumerate(allocations, start=2):
        meter_id = (a.get("meter_id") or "").strip()
        dept = (a.get("department") or "").strip()
        ratio = to_float(a.get("allocation_ratio")) or 0
        if not meter_id or ratio <= 0:
            continue
        if meter_id not in meter_by_id:
            validation["errors"].append({"row": row_no, "file": "department_allocations.csv", "error": "UNKNOWN_METER_ID", "meter_id": meter_id})
            continue
        if dept not in DEPARTMENTS:
            validation["warnings"].append({"row": row_no, "file": "department_allocations.csv", "warning": "UNKNOWN_DEPARTMENT", "department": dept})
            continue
        allocation_rows.append({**a, "meter_id": meter_id, "department": dept, "allocation_ratio": ratio})

    department_weekly: list[dict[str, Any]] = []
    for w in weekly_consumption:
        if not w["is_main"]:
            continue
        for a in allocation_rows:
            if a["meter_id"] != w["meter_id"]:
                continue
            department_weekly.append({
                "week_start_date": w["week_start_date"],
                "week_end_date": w["week_end_date"],
                "week_id": w.get("week_id", ""),
                "department": a["department"],
                "meter_id": w["meter_id"],
                "b_code": w["b_code"],
                "building_name": w["building_name"],
                "allocation_ratio": a["allocation_ratio"],
                "kwh": round(w["kwh"] * a["allocation_ratio"], 3),
                "source_flags": w["flags"],
            })

    # Derived summaries for dashboard convenience.
    monthly_by_department: dict[str, dict[str, float]] = {}
    for r in department_weekly:
        month = r["week_end_date"][:7]
        monthly_by_department.setdefault(month, {})
        monthly_by_department[month][r["department"]] = round(monthly_by_department[month].get(r["department"], 0) + r["kwh"], 3)

    validation["stats"] = {
        "meters": len(meters),
        "weekly_forms_rows_used": len(readings),
        "normalized_readings": len(normalized_readings),
        "weekly_consumption_rows": len(weekly_consumption),
        "department_weekly_rows": len(department_weekly),
        "main_meter_codes": sorted(MAIN_CODES),
        "form_files_read": len([p for p in FORMS_DIR.glob('*.csv') if not p.name.startswith('_')]),
    }

    # Promote row-level flags into validation warnings for easy review.
    for r in normalized_readings:
        if r.get("flags"):
            validation["warnings"].append({
                "source_form": r.get("source_form"),
                "reading_date": r.get("reading_date"),
                "meter_id": r.get("meter_id"),
                "warning": ",".join(r.get("flags", [])),
            })

    return {
        "meta": {
            "site": "กฟผ. สำนักงานไทรน้อย",
            "version": "energy-auto-db-github-production-v2",
            "base_unit": "kWh",
            "reading_cycle": "weekly Friday morning",
            "main_subb_codes": sorted(MAIN_CODES),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "generated_from": ["forms/*.csv", "data/meter_master.csv", "data/department_allocations.csv"],
        },
        "departments": DEPARTMENTS,
        "meters": meters,
        "department_allocations": allocations,
        "normalized_readings": normalized_readings,
        "weekly_consumption": weekly_consumption,
        "department_weekly": department_weekly,
        "monthly_by_department": monthly_by_department,
        "validation": validation,
    }


def main() -> None:
    db = build()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with (DATA_DIR / "energy_db.json").open("w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    with (DATA_DIR / "validation_report.json").open("w", encoding="utf-8") as f:
        json.dump(db["validation"], f, ensure_ascii=False, indent=2)

    print("Generated data/weekly_readings.csv")
    print("Generated data/energy_db.json")
    print("Generated data/validation_report.json")
    print(json.dumps(db["validation"]["stats"], ensure_ascii=False, indent=2))
    if db["validation"]["errors"]:
        print(f"Validation failed with {len(db['validation']['errors'])} error(s). See data/validation_report.json")
        raise SystemExit(1)


if __name__ == "__main__":
    main()

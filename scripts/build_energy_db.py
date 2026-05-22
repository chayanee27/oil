#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Build energy_db.json from weekly raw meter forms.

Key rules:
- RAW readings are cumulative meter readings.
- Weekly usage is always calculated as: current_reading - previous_reading.
- Base unit is kWh.
- Mixed kWh/MWh human inputs are normalized with continuity logic.
- Department/building allocation is taken from:
    1) data/department_allocation_buildings.csv  (preferred; generated from Excel สัดส่วน)
    2) data/department_allocations.csv           (fallback)
- Dashboard reads data/energy_db.json, especially department_weekly.
"""

from __future__ import annotations

import csv
import json
import re
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
FORMS_DIR = ROOT / "forms"

METER_MASTER_FILE = DATA_DIR / "meter_master.csv"
DEPARTMENT_ALLOCATIONS_FILE = DATA_DIR / "department_allocations.csv"
BUILDING_ALLOCATIONS_FILE = DATA_DIR / "department_allocation_buildings.csv"

OUTPUT_WEEKLY_READINGS = DATA_DIR / "weekly_readings.csv"
OUTPUT_DB_JSON = DATA_DIR / "energy_db.json"
OUTPUT_VALIDATION = DATA_DIR / "validation_report.json"

MAIN_METER_CODES = ["MDB", "Main", "SCB21"]
MAIN_METER_SET = set(MAIN_METER_CODES)

DEPARTMENTS = ["สก.ชธธ.", "อบค.", "อบฟ.", "อบย.", "อรอ.", "อคม.", "อหข."]

WEEKLY_FIELDS = [
    "source_form",
    "reading_date",
    "week_id",
    "meter_id",
    "subb_code",
    "building_name",
    "raw_reading",
    "raw_unit",
    "reader",
    "note",
]


def read_csv(path: Path) -> list[dict[str, Any]]:
    encodings = ["utf-8-sig", "utf-8", "cp874", "tis-620"]
    last_error: Exception | None = None

    for enc in encodings:
        try:
            with path.open("r", encoding=enc, newline="") as f:
                rows = list(csv.DictReader(f))
            print(f"Read CSV OK: {path} encoding={enc}")
            return rows
        except UnicodeDecodeError as e:
            last_error = e

    raise RuntimeError(f"Cannot read CSV: {path}. Last error: {last_error}")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("reading_date is blank")

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass

    raise ValueError(f"invalid date format: {text}")


def clean_unit(unit: Any) -> str:
    text = str(unit or "").strip().lower()

    if text in {"mwh", "mwhr", "mwh.", "เมกะวัตต์ชั่วโมง"}:
        return "MWh"

    if text in {"kwh", "kwhr", "kwh.", "หน่วย", "ยูนิต"}:
        return "kWh"

    return ""


def normalize_text(text: Any) -> str:
    text = str(text or "").strip().lower()
    text = re.sub(r"\s+", "", text)
    text = text.replace("อาคาร", "")
    text = text.replace("(", "").replace(")", "")
    text = text.replace(".", "")
    text = text.replace("-", "")
    return text


def normalize_code(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    # Thai code -> roman code used in meter_id/master
    # ท.0019 -> T.0019, ต.0019 -> D.0019, บ.0001 -> B.0001
    m = re.search(r"([ทตบ])\.(\d{4})", text)
    if m:
        mp = {"ท": "T", "ต": "D", "บ": "B"}
        return f"{mp[m.group(1)]}.{m.group(2)}"

    m = re.search(r"([TDB])\.(\d{4})", text, flags=re.I)
    if m:
        return f"{m.group(1).upper()}.{m.group(2)}"

    return text


def extract_floor(value: Any) -> str:
    text = str(value or "")
    if re.fullmatch(r"FL\d{2}", text.strip(), flags=re.I):
        return text.strip().upper()

    m = re.search(r"ชั้น(?:ที่)?\s*(\d+)", text)
    if m:
        return f"FL{int(m.group(1)):02d}"

    m = re.search(r"FL\s*0?(\d+)", text, flags=re.I)
    if m:
        return f"FL{int(m.group(1)):02d}"

    return ""


def extract_code_from_building_name(name: Any) -> str:
    return normalize_code(name)


def extract_floor_from_building_name(name: Any) -> str:
    return extract_floor(name)


def get_meter_b_code(meter: dict[str, Any]) -> str:
    return normalize_code(
        meter.get("b_code")
        or meter.get("code")
        or meter.get("building_code")
        or meter.get("B.Code")
        or meter.get("B_Code")
        or meter.get("meter_code")
        or meter.get("meter_id", "").split("__")[0]
    )


def get_meter_floor(meter: dict[str, Any]) -> str:
    return extract_floor(
        meter.get("floor")
        or meter.get("floor_code")
        or meter.get("Floor")
        or meter.get("meter_id", "")
    )


def get_meter_subb(meter: dict[str, Any]) -> str:
    return str(
        meter.get("subb_code")
        or meter.get("SubB.Code")
        or meter.get("subb")
        or meter.get("sub_code")
        or ""
    ).strip()


def get_meter_name(meter: dict[str, Any]) -> str:
    return str(
        meter.get("building_name")
        or meter.get("Building Name")
        or meter.get("name")
        or ""
    ).strip()


def get_meter_default_unit(meter: dict[str, Any]) -> str:
    return str(
        meter.get("default_unit")
        or meter.get("raw_unit")
        or meter.get("unit")
        or "kWh"
    ).strip()


def normalize_by_continuity(raw_value: float, raw_unit: Any, previous_kwh: float | None, default_unit: Any = None) -> tuple[float, list[str]]:
    """
    Normalize cumulative reading to kWh.

    The UNIT column can be wrong or unchanged. We try:
    - declared/default interpretation
    - raw as kWh
    - raw as MWh -> kWh

    If previous cumulative reading exists, choose the candidate with the
    smallest non-negative delta.
    """
    flags: list[str] = []

    unit = clean_unit(raw_unit) or clean_unit(default_unit) or "kWh"

    as_kwh = raw_value
    as_mwh = raw_value * 1000.0
    declared = as_mwh if unit == "MWh" else as_kwh

    candidates = [
        ("declared", declared),
        ("as_kwh", as_kwh),
        ("as_mwh", as_mwh),
    ]

    unique: list[tuple[str, float]] = []
    seen: set[float] = set()
    for label, val in candidates:
        key = round(val, 6)
        if key not in seen:
            seen.add(key)
            unique.append((label, val))

    if previous_kwh is None:
        if unit == "MWh":
            flags.append("FIRST_READING_USED_MWH_UNIT")
        else:
            flags.append("FIRST_READING_USED_KWH_UNIT")
        return round(declared, 3), flags

    scored: list[tuple[float, str, float, float]] = []

    for label, val in unique:
        delta = val - previous_kwh

        if delta >= 0:
            score = delta
        else:
            # negative cumulative reading is normally wrong interpretation
            score = abs(delta) * 1000 + 1_000_000_000

        scored.append((score, label, val, delta))

    scored.sort(key=lambda x: x[0])
    _, chosen_label, chosen_value, chosen_delta = scored[0]

    if chosen_label != "declared":
        flags.append("UNIT_SUSPECT_AUTO_CORRECTED")

    if chosen_label == "as_kwh" and unit == "MWh":
        flags.append("RAW_LOOKS_KWH_BUT_UNIT_SAYS_MWH")

    if chosen_label == "as_mwh" and unit == "kWh":
        flags.append("RAW_LOOKS_MWH_BUT_UNIT_SAYS_KWH")

    if chosen_delta < 0:
        flags.append("NEGATIVE_DELTA_AFTER_AUTO_NORMALIZE")

    return round(chosen_value, 3), sorted(set(flags))


def load_master(validation: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    meters = read_csv(METER_MASTER_FILE)
    meter_by_id: dict[str, dict[str, Any]] = {}

    for row_no, row in enumerate(meters, start=2):
        meter_id = str(row.get("meter_id", "")).strip()
        if not meter_id:
            validation["warnings"].append({
                "file": "meter_master.csv",
                "row": row_no,
                "warning": "MISSING_METER_ID_IN_MASTER",
            })
            continue
        meter_by_id[meter_id] = row

    return meters, meter_by_id


def load_allocation_source(validation: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    if BUILDING_ALLOCATIONS_FILE.exists():
        return read_csv(BUILDING_ALLOCATIONS_FILE), "department_allocation_buildings.csv"

    if DEPARTMENT_ALLOCATIONS_FILE.exists():
        return read_csv(DEPARTMENT_ALLOCATIONS_FILE), "department_allocations.csv"

    validation["warnings"].append({"warning": "NO_ALLOCATION_FILE_FOUND"})
    return [], ""


def choose_meter_candidates(candidates: list[str], meter_by_id: dict[str, dict[str, Any]], prefer_main: bool = True) -> list[str]:
    if not candidates:
        return []

    # Prefer main meter codes when available, but if none exists for a floor/area,
    # use all matching meters. This is necessary for summary rows such as some
    # ท.0019 floor totals that do not have a single Main/MDB meter.
    main = [m for m in candidates if get_meter_subb(meter_by_id[m]) in MAIN_METER_SET]

    if prefer_main and main:
        priority = {code: i for i, code in enumerate(MAIN_METER_CODES)}
        main.sort(key=lambda m: priority.get(get_meter_subb(meter_by_id[m]), 999))
        return main

    return sorted(set(candidates))


def map_allocation_to_meters(allocation: dict[str, Any], meter_by_id: dict[str, dict[str, Any]]) -> list[str]:
    explicit_meter_id = str(allocation.get("meter_id", "")).strip()
    if explicit_meter_id:
        return [explicit_meter_id] if explicit_meter_id in meter_by_id else []

    building_name = allocation.get("building_name") or allocation.get("Building Name") or ""
    b_code_hint = normalize_code(
        allocation.get("b_code_hint")
        or allocation.get("b_code")
        or allocation.get("code")
        or extract_code_from_building_name(building_name)
    )
    floor_hint = extract_floor(
        allocation.get("floor_hint")
        or allocation.get("floor")
        or extract_floor_from_building_name(building_name)
    )
    normalized_alloc_name = normalize_text(building_name)

    all_meter_ids = list(meter_by_id.keys())

    # 1) Strongest match: b_code + floor.
    if b_code_hint and floor_hint:
        cands = [
            meter_id for meter_id in all_meter_ids
            if get_meter_b_code(meter_by_id[meter_id]) == b_code_hint
            and get_meter_floor(meter_by_id[meter_id]) == floor_hint
        ]
        if cands:
            return choose_meter_candidates(cands, meter_by_id, prefer_main=True)

    # 2) Strong match: b_code only, prefer main meter.
    if b_code_hint:
        cands = [
            meter_id for meter_id in all_meter_ids
            if get_meter_b_code(meter_by_id[meter_id]) == b_code_hint
        ]
        if cands:
            return choose_meter_candidates(cands, meter_by_id, prefer_main=True)

    # 3) Exact normalized building name match.
    if normalized_alloc_name:
        cands = [
            meter_id for meter_id in all_meter_ids
            if normalize_text(get_meter_name(meter_by_id[meter_id])) == normalized_alloc_name
        ]
        if cands:
            return choose_meter_candidates(cands, meter_by_id, prefer_main=True)

    # 4) Partial normalized name match.
    if normalized_alloc_name:
        cands = [
            meter_id for meter_id in all_meter_ids
            if normalized_alloc_name in normalize_text(get_meter_name(meter_by_id[meter_id]))
            or normalize_text(get_meter_name(meter_by_id[meter_id])) in normalized_alloc_name
        ]
        if cands:
            return choose_meter_candidates(cands, meter_by_id, prefer_main=True)

    return []


def build_allocations_by_meter(allocation_rows: list[dict[str, Any]], meter_by_id: dict[str, dict[str, Any]], validation: dict[str, Any], source_name: str) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    allocations_by_meter: dict[str, list[dict[str, Any]]] = {}
    resolved_allocations: list[dict[str, Any]] = []

    for row_no, row in enumerate(allocation_rows, start=2):
        department = str(row.get("department") or row.get("หน่วยงาน") or "").strip()
        building_name = str(row.get("building_name") or row.get("Building Name") or "").strip()

        ratio = (
            to_float(row.get("allocation_ratio"))
            or to_float(row.get("ratio"))
            or None
        )

        if ratio is None:
            pct = to_float(row.get("allocation_percent"))
            ratio = pct / 100 if pct is not None else 0

        if not department or not building_name:
            continue

        if ratio <= 0:
            continue

        meter_ids = map_allocation_to_meters(row, meter_by_id)

        if not meter_ids:
            validation["warnings"].append({
                "file": source_name,
                "row": row_no,
                "warning": "ALLOCATION_BUILDING_NOT_MAPPED_TO_METER",
                "department": department,
                "building_name": building_name,
                "b_code_hint": row.get("b_code_hint", ""),
                "floor_hint": row.get("floor_hint", ""),
                "allocation_ratio": ratio,
            })
            continue

        if department not in DEPARTMENTS:
            validation["warnings"].append({
                "file": source_name,
                "row": row_no,
                "warning": "UNKNOWN_DEPARTMENT",
                "department": department,
                "building_name": building_name,
            })

        for meter_id in meter_ids:
            resolved = {
                "department": department,
                "meter_id": meter_id,
                "building_name_allocation": building_name,
                "allocation_ratio": ratio,
                "allocation_source": source_name,
                "allocation_source_row": row_no,
            }
            allocations_by_meter.setdefault(meter_id, []).append(resolved)
            resolved_allocations.append(resolved)

    return allocations_by_meter, resolved_allocations


def collect_form_readings(meter_by_id: dict[str, dict[str, Any]], validation: dict[str, Any]) -> tuple[list[dict[str, Any]], list[Path]]:
    form_files = sorted(p for p in FORMS_DIR.glob("*.csv") if not p.name.startswith("_"))
    readings: list[dict[str, Any]] = []
    seen: dict[tuple[str, str], str] = {}

    for form_path in form_files:
        rows = read_csv(form_path)

        for row_no, row in enumerate(rows, start=2):
            meter_id = str(row.get("meter_id", "")).strip()

            # allow blank template rows
            if not meter_id:
                continue

            raw = to_float(row.get("raw_reading"))

            # allow blank readings while entering current week
            if raw is None:
                continue

            if meter_id not in meter_by_id:
                validation["errors"].append({
                    "file": str(form_path.relative_to(ROOT)),
                    "row": row_no,
                    "error": "UNKNOWN_METER_ID",
                    "meter_id": meter_id,
                })
                continue

            try:
                reading_date = parse_date(row.get("reading_date", ""))
            except Exception as e:
                validation["errors"].append({
                    "file": str(form_path.relative_to(ROOT)),
                    "row": row_no,
                    "error": "INVALID_DATE",
                    "detail": str(e),
                    "meter_id": meter_id,
                })
                continue

            week_id = str(row.get("week_id", "")).strip()
            if not week_id:
                iso = datetime.strptime(reading_date, "%Y-%m-%d").isocalendar()
                week_id = f"{iso.year}-W{iso.week:02d}"

            key = (reading_date, meter_id)
            if key in seen:
                validation["errors"].append({
                    "file": str(form_path.relative_to(ROOT)),
                    "row": row_no,
                    "error": "DUPLICATE_READING_FOR_SAME_DATE_AND_METER",
                    "meter_id": meter_id,
                    "reading_date": reading_date,
                    "first_seen_in": seen[key],
                })
                continue
            seen[key] = str(form_path.relative_to(ROOT))

            meter = meter_by_id[meter_id]

            readings.append({
                "source_form": str(form_path.relative_to(ROOT)),
                "reading_date": reading_date,
                "week_id": week_id,
                "meter_id": meter_id,
                "subb_code": get_meter_subb(meter),
                "building_name": get_meter_name(meter),
                "raw_reading": raw,
                "raw_unit": row.get("raw_unit", "") or get_meter_default_unit(meter),
                "reader": row.get("reader", ""),
                "note": row.get("note", ""),
            })

    readings.sort(key=lambda x: (x["meter_id"], x["reading_date"]))
    return readings, form_files


def build() -> dict[str, Any]:
    validation: dict[str, Any] = {"errors": [], "warnings": [], "stats": {}}

    meter_master, meter_by_id = load_master(validation)
    allocation_rows, allocation_source_name = load_allocation_source(validation)
    allocations_by_meter, resolved_allocations = build_allocations_by_meter(
        allocation_rows,
        meter_by_id,
        validation,
        allocation_source_name,
    )

    raw_readings, form_files = collect_form_readings(meter_by_id, validation)

    readings_by_meter: dict[str, list[dict[str, Any]]] = {}
    for row in raw_readings:
        readings_by_meter.setdefault(row["meter_id"], []).append(row)

    normalized_readings: list[dict[str, Any]] = []
    weekly_consumption: list[dict[str, Any]] = []

    for meter_id, rows in readings_by_meter.items():
        rows.sort(key=lambda x: x["reading_date"])
        previous_kwh: float | None = None
        previous_date: str | None = None
        recent_deltas: list[float] = []

        meter = meter_by_id[meter_id]
        default_unit = get_meter_default_unit(meter)

        for row in rows:
            raw = to_float(row.get("raw_reading"))
            if raw is None:
                continue

            normalized_kwh, flags = normalize_by_continuity(
                raw_value=raw,
                raw_unit=row.get("raw_unit", ""),
                previous_kwh=previous_kwh,
                default_unit=default_unit,
            )

            subb_code = row.get("subb_code", "")
            is_main_meter = subb_code in MAIN_METER_SET

            normalized_row = {
                **row,
                "normalized_kwh": normalized_kwh,
                "is_main_meter": is_main_meter,
                "flags": sorted(set(flags)),
            }
            normalized_readings.append(normalized_row)

            if previous_kwh is not None:
                raw_delta = normalized_kwh - previous_kwh
                week_flags = list(flags)

                if raw_delta < 0:
                    week_flags.append("NEGATIVE_DELTA")
                    kwh = 0.0
                else:
                    kwh = raw_delta

                if recent_deltas:
                    median_delta = statistics.median(recent_deltas[-8:])
                    if median_delta > 0 and kwh > median_delta * 5:
                        week_flags.append("SPIKE_SUSPECT_OVER_5X_RECENT_MEDIAN")

                if previous_date:
                    d0 = datetime.strptime(previous_date, "%Y-%m-%d")
                    d1 = datetime.strptime(row["reading_date"], "%Y-%m-%d")
                    gap = (d1 - d0).days
                    if gap < 5 or gap > 10:
                        week_flags.append(f"NON_WEEKLY_GAP_{gap}_DAYS")

                weekly_consumption.append({
                    "week_start_date": previous_date,
                    "week_end_date": row["reading_date"],
                    "week_id": row["week_id"],
                    "meter_id": meter_id,
                    "subb_code": subb_code,
                    "building_name": row["building_name"],
                    "is_main_meter": is_main_meter,
                    "kwh": round(kwh, 3),
                    "raw_delta_kwh": round(raw_delta, 3),
                    "flags": sorted(set(week_flags)),
                })

                if raw_delta >= 0:
                    recent_deltas.append(raw_delta)

            previous_kwh = normalized_kwh
            previous_date = row["reading_date"]

    # Department weekly is generated from weekly consumption multiplied by
    # per-building/per-floor allocation ratio from the Excel-derived allocation file.
    department_weekly: list[dict[str, Any]] = []
    building_weekly: list[dict[str, Any]] = []

    for week in weekly_consumption:
        meter_id = week["meter_id"]
        allocations = allocations_by_meter.get(meter_id, [])

        # For dashboard top-building display, keep direct building usage by meter.
        building_weekly.append({
            "week_start_date": week["week_start_date"],
            "week_end_date": week["week_end_date"],
            "week_id": week["week_id"],
            "meter_id": meter_id,
            "building_name": week["building_name"],
            "subb_code": week["subb_code"],
            "is_main_meter": week["is_main_meter"],
            "kwh": week["kwh"],
            "source_flags": week["flags"],
        })

        for alloc in allocations:
            ratio = to_float(alloc.get("allocation_ratio")) or 0
            if ratio <= 0:
                continue

            department_weekly.append({
                "week_start_date": week["week_start_date"],
                "week_end_date": week["week_end_date"],
                "week_id": week["week_id"],
                "department": alloc["department"],
                "meter_id": meter_id,
                "building_name": alloc.get("building_name_allocation") or week["building_name"],
                "meter_building_name": week["building_name"],
                "allocation_ratio": ratio,
                "kwh": round(week["kwh"] * ratio, 3),
                "source_flags": week["flags"],
            })

    monthly_by_department: dict[str, dict[str, float]] = {}
    for row in department_weekly:
        month = row["week_end_date"][:7]
        dept = row["department"]
        monthly_by_department.setdefault(month, {})
        monthly_by_department[month][dept] = round(
            monthly_by_department[month].get(dept, 0.0) + row["kwh"],
            3,
        )

    for row in normalized_readings:
        if row.get("flags"):
            validation["warnings"].append({
                "source_form": row.get("source_form"),
                "reading_date": row.get("reading_date"),
                "meter_id": row.get("meter_id"),
                "warning": ",".join(row.get("flags", [])),
            })

    for row in weekly_consumption:
        if row.get("flags"):
            validation["warnings"].append({
                "week_end_date": row.get("week_end_date"),
                "meter_id": row.get("meter_id"),
                "warning": ",".join(row.get("flags", [])),
            })

    validation["stats"] = {
        "meters": len(meter_master),
        "allocation_rows": len(allocation_rows),
        "resolved_allocation_rows": len(resolved_allocations),
        "weekly_forms_rows_used": len(raw_readings),
        "normalized_readings": len(normalized_readings),
        "weekly_consumption_rows": len(weekly_consumption),
        "department_weekly_rows": len(department_weekly),
        "building_weekly_rows": len(building_weekly),
        "main_meter_codes": MAIN_METER_CODES,
        "form_files_read": len(form_files),
        "allocation_source": allocation_source_name,
    }

    return {
        "meta": {
            "site": "กฟผ. สำนักงานไทรน้อย",
            "version": "energy-auto-db-building-allocation-v4",
            "base_unit": "kWh",
            "reading_cycle": "weekly Friday morning",
            "main_meter_codes": MAIN_METER_CODES,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "generated_from": [
                "forms/*.csv",
                "data/meter_master.csv",
                f"data/{allocation_source_name}" if allocation_source_name else "",
            ],
        },
        "departments": DEPARTMENTS,
        "meters": meter_master,
        "department_allocation_buildings": allocation_rows,
        "resolved_allocations": resolved_allocations,
        "weekly_readings": raw_readings,
        "normalized_readings": normalized_readings,
        "weekly_consumption": weekly_consumption,
        "building_weekly": building_weekly,
        "department_weekly": department_weekly,
        "monthly_by_department": monthly_by_department,
        "validation": validation,
    }


def write_outputs(db: dict[str, Any]) -> None:
    write_csv(OUTPUT_WEEKLY_READINGS, db["weekly_readings"], WEEKLY_FIELDS)

    with OUTPUT_DB_JSON.open("w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    print(f"Generated {OUTPUT_DB_JSON}")

    with OUTPUT_VALIDATION.open("w", encoding="utf-8") as f:
        json.dump(db["validation"], f, ensure_ascii=False, indent=2)
    print(f"Generated {OUTPUT_VALIDATION}")


def main() -> None:
    db = build()
    write_outputs(db)

    print(json.dumps(db["validation"]["stats"], ensure_ascii=False, indent=2))

    if db["validation"]["errors"]:
        print(
            f"Validation found {len(db['validation']['errors'])} error(s). "
            f"See data/validation_report.json"
        )

    print("Build completed")


if __name__ == "__main__":
    main()

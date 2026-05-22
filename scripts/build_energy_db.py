import csv
import json
import statistics
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = ROOT / "data"
FORMS_DIR = ROOT / "forms"

METER_MASTER_FILE = DATA_DIR / "meter_master.csv"
DEPARTMENT_ALLOCATIONS_FILE = DATA_DIR / "department_allocations.csv"

OUTPUT_WEEKLY_READINGS = DATA_DIR / "weekly_readings.csv"
OUTPUT_DB_JSON = DATA_DIR / "energy_db.json"
OUTPUT_VALIDATION = DATA_DIR / "validation_report.json"

MAIN_METER_CODES = ["MDB", "Main", "SCB21"]
DEPARTMENTS = ["สก.ชธธ.", "อบค.", "อบฟ.", "อบย.", "อรอ.", "อคม.", "อหข."]


def read_csv(path):
    encodings = ["utf-8-sig", "utf-8", "cp874", "tis-620"]
    last_error = None

    for enc in encodings:
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                rows = list(csv.DictReader(f))
                print(f"Read CSV OK: {path} encoding={enc}")
                return rows
        except Exception as e:
            last_error = e

    raise last_error


def to_float(value):
    if value is None:
        return None

    raw = str(value).replace(",", "").strip()

    if raw == "":
        return None

    try:
        return float(raw)
    except ValueError:
        return None


def parse_reading_date(reading_date):
    reading_date = str(reading_date).strip()

    if not reading_date:
        raise ValueError("reading_date is blank")

    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            dt = datetime.strptime(reading_date, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass

    raise ValueError(f"invalid date format: {reading_date}")


def clean_unit(unit):
    unit = str(unit or "").strip().lower()

    if unit in ["mwh", "mwhr", "mwh."]:
        return "MWh"

    if unit in ["kwh", "kwhr", "kwh.", "หน่วย", "ยูนิต"]:
        return "kWh"

    return ""


def normalize_first_reading(raw_value, raw_unit, default_unit):
    unit = clean_unit(raw_unit) or clean_unit(default_unit) or "kWh"

    if unit == "MWh":
        return raw_value * 1000, ["FIRST_READING_USED_MWH_UNIT"]

    return raw_value, ["FIRST_READING_USED_KWH_UNIT"]


def normalize_by_continuity(raw_value, raw_unit, previous_kwh, default_unit=None):
    """
    Convert raw meter reading to kWh safely.

    The raw_unit column can be wrong. We compare possible interpretations
    against the previous cumulative reading and choose the most continuous one.
    """
    flags = []

    unit = clean_unit(raw_unit) or clean_unit(default_unit) or "kWh"

    as_kwh = raw_value
    as_mwh = raw_value * 1000
    declared = as_mwh if unit == "MWh" else as_kwh

    candidates = [
        ("declared", declared),
        ("as_kwh", as_kwh),
        ("as_mwh", as_mwh),
    ]

    unique = []
    seen = set()
    for label, value in candidates:
        key = round(value, 6)
        if key not in seen:
            seen.add(key)
            unique.append((label, value))

    if previous_kwh is None:
        chosen, first_flags = normalize_first_reading(raw_value, raw_unit, default_unit)
        flags.extend(first_flags)
        return round(chosen, 3), flags

    scored = []

    for label, value in unique:
        delta = value - previous_kwh

        if delta >= 0:
            score = delta
        else:
            score = abs(delta) * 1000 + 1_000_000_000

        scored.append((score, label, value, delta))

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

    return round(chosen_value, 3), flags


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()

        for row in rows:
            writer.writerow(row)


def load_master():
    meter_master = read_csv(METER_MASTER_FILE)
    department_allocations = read_csv(DEPARTMENT_ALLOCATIONS_FILE)

    meter_by_id = {}

    for row in meter_master:
        meter_id = str(row.get("meter_id", "")).strip()
        if meter_id:
            meter_by_id[meter_id] = row

    allocations_by_meter = {}

    for row in department_allocations:
        meter_id = str(row.get("meter_id", "")).strip()

        if not meter_id:
            continue

        allocations_by_meter.setdefault(meter_id, []).append(row)

    return meter_master, department_allocations, meter_by_id, allocations_by_meter


def collect_form_readings(meter_by_id, validation):
    form_files = sorted(
        p for p in FORMS_DIR.glob("*.csv")
        if not p.name.startswith("_")
    )

    readings = []

    for form_path in form_files:
        rows = read_csv(form_path)

        for row_no, row in enumerate(rows, start=2):
            meter_id = str(row.get("meter_id", "")).strip()

            if meter_id == "":
                continue

            raw_reading = to_float(row.get("raw_reading"))

            if raw_reading is None:
                continue

            try:
                reading_date = parse_reading_date(row.get("reading_date", ""))
            except Exception as e:
                validation["errors"].append({
                    "file": str(form_path),
                    "row": row_no,
                    "error": "INVALID_DATE",
                    "detail": str(e),
                    "meter_id": meter_id
                })
                continue

            week_id = str(row.get("week_id", "")).strip()

            if week_id == "":
                iso = datetime.strptime(reading_date, "%Y-%m-%d").isocalendar()
                week_id = f"{iso.year}-W{iso.week:02d}"

            if meter_id not in meter_by_id:
                validation["errors"].append({
                    "file": str(form_path),
                    "row": row_no,
                    "error": "UNKNOWN_METER_ID",
                    "meter_id": meter_id
                })
                continue

            meter = meter_by_id[meter_id]

            readings.append({
                "source_form": str(form_path.relative_to(ROOT)),
                "reading_date": reading_date,
                "week_id": week_id,
                "meter_id": meter_id,
                "subb_code": str(meter.get("subb_code", "")).strip(),
                "building_name": meter.get("building_name", ""),
                "raw_reading": raw_reading,
                "raw_unit": row.get("raw_unit", "") or meter.get("default_unit", "") or "kWh",
                "reader": row.get("reader", ""),
                "note": row.get("note", "")
            })

    readings.sort(key=lambda x: (x["meter_id"], x["reading_date"]))

    return readings, form_files


def build():
    validation = {
        "errors": [],
        "warnings": [],
        "stats": {}
    }

    meter_master, department_allocations, meter_by_id, allocations_by_meter = load_master()

    raw_readings, form_files = collect_form_readings(meter_by_id, validation)

    normalized_readings = []
    weekly_consumption = []
    readings_by_meter = {}

    for row in raw_readings:
        readings_by_meter.setdefault(row["meter_id"], []).append(row)

    for meter_id, rows in readings_by_meter.items():
        rows.sort(key=lambda x: x["reading_date"])

        previous_kwh = None
        previous_date = None
        recent_deltas = []

        meter = meter_by_id.get(meter_id, {})
        default_unit = meter.get("default_unit", "")

        for row in rows:
            normalized_kwh, flags = normalize_by_continuity(
                raw_value=row["raw_reading"],
                raw_unit=row.get("raw_unit", ""),
                previous_kwh=previous_kwh,
                default_unit=default_unit
            )

            subb_code = str(row.get("subb_code", "")).strip()
            is_main_meter = subb_code in MAIN_METER_CODES

            normalized_row = {
                **row,
                "normalized_kwh": normalized_kwh,
                "is_main_meter": is_main_meter,
                "flags": flags
            }

            normalized_readings.append(normalized_row)

            if previous_kwh is not None:
                diff = normalized_kwh - previous_kwh

                week_flags = list(flags)

                if diff < 0:
                    week_flags.append("NEGATIVE_DELTA")
                    kwh = 0
                else:
                    kwh = diff

                if len(recent_deltas) >= 3:
                    median_delta = statistics.median(recent_deltas[-8:])

                    if median_delta > 0 and kwh > median_delta * 5:
                        week_flags.append("SPIKE_SUSPECT_OVER_5X_MEDIAN")

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
                    "raw_delta_kwh": round(diff, 3),
                    "flags": sorted(set(week_flags))
                })

                if diff >= 0:
                    recent_deltas.append(diff)

            previous_kwh = normalized_kwh
            previous_date = row["reading_date"]

    department_weekly = []

    for week in weekly_consumption:
        if not week["is_main_meter"]:
            continue

        meter_id = week["meter_id"]
        allocations = allocations_by_meter.get(meter_id, [])

        for alloc in allocations:
            department = str(alloc.get("department", "")).strip()

            ratio = (
                to_float(alloc.get("allocation_ratio"))
                or to_float(alloc.get("ratio"))
                or None
            )

            if ratio is None:
                pct = to_float(alloc.get("allocation_percent"))
                ratio = pct / 100 if pct is not None else 0

            if ratio <= 0:
                continue

            if department not in DEPARTMENTS:
                validation["warnings"].append({
                    "file": "department_allocations.csv",
                    "warning": "UNKNOWN_DEPARTMENT",
                    "department": department,
                    "meter_id": meter_id
                })

            department_weekly.append({
                "week_start_date": week["week_start_date"],
                "week_end_date": week["week_end_date"],
                "week_id": week["week_id"],
                "department": department,
                "meter_id": meter_id,
                "building_name": week["building_name"],
                "b_code": meter_by_id.get(meter_id, {}).get("b_code", ""),
                "allocation_ratio": ratio,
                "kwh": round(week["kwh"] * ratio, 3),
                "source_flags": week["flags"]
            })

    monthly_by_department = {}

    for row in department_weekly:
        month = row["week_end_date"][:7]
        department = row["department"]

        monthly_by_department.setdefault(month, {})
        monthly_by_department[month][department] = round(
            monthly_by_department[month].get(department, 0) + row["kwh"],
            3
        )

    for row in normalized_readings:
        if row["flags"]:
            validation["warnings"].append({
                "source_form": row["source_form"],
                "reading_date": row["reading_date"],
                "meter_id": row["meter_id"],
                "warning": ",".join(row["flags"])
            })

    for row in weekly_consumption:
        if row["flags"]:
            validation["warnings"].append({
                "week_end_date": row["week_end_date"],
                "meter_id": row["meter_id"],
                "warning": ",".join(row["flags"])
            })

    validation["stats"] = {
        "meters": len(meter_master),
        "weekly_forms_rows_used": len(raw_readings),
        "normalized_readings": len(normalized_readings),
        "weekly_consumption_rows": len(weekly_consumption),
        "department_weekly_rows": len(department_weekly),
        "main_meter_codes": MAIN_METER_CODES,
        "form_files_read": len(form_files)
    }

    return {
        "meta": {
            "site": "กฟผ. สำนักงานไทรน้อย",
            "version": "energy-auto-db-continuity-normalize-v3",
            "base_unit": "kWh",
            "main_meter_codes": MAIN_METER_CODES,
            "generated_at": datetime.now().isoformat(timespec="seconds")
        },
        "departments": DEPARTMENTS,
        "meters": meter_master,
        "department_allocations": department_allocations,
        "weekly_readings": raw_readings,
        "normalized_readings": normalized_readings,
        "weekly_consumption": weekly_consumption,
        "department_weekly": department_weekly,
        "monthly_by_department": monthly_by_department,
        "validation": validation
    }


def write_outputs(db):
    weekly_fields = [
        "source_form",
        "reading_date",
        "week_id",
        "meter_id",
        "subb_code",
        "building_name",
        "raw_reading",
        "raw_unit",
        "reader",
        "note"
    ]

    write_csv(OUTPUT_WEEKLY_READINGS, db["weekly_readings"], weekly_fields)

    with open(OUTPUT_DB_JSON, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

    print(f"Generated {OUTPUT_DB_JSON}")

    with open(OUTPUT_VALIDATION, "w", encoding="utf-8") as f:
        json.dump(db["validation"], f, ensure_ascii=False, indent=2)

    print(f"Generated {OUTPUT_VALIDATION}")


def main():
    db = build()

    write_outputs(db)

    print(json.dumps(
        db["validation"]["stats"],
        indent=2,
        ensure_ascii=False
    ))

    if db["validation"]["errors"]:
        print(
            f"Validation found {len(db['validation']['errors'])} error(s). "
            f"See data/validation_report.json"
        )

    print("Build completed")


if __name__ == "__main__":
    main()

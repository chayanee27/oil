import csv
import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
FORMS_DIR = ROOT / "forms"
DATA_DIR = ROOT / "data"

METER_MASTER_FILE = DATA_DIR / "meter_master.csv"
DEPARTMENT_ALLOCATIONS_FILE = DATA_DIR / "department_allocations.csv"
BUILDING_ALLOCATIONS_FILE = DATA_DIR / "department_allocation_buildings.csv"
BUILDING_ALIAS_FILE = DATA_DIR / "building_alias.json"

OUTPUT_WEEKLY_READINGS = DATA_DIR / "weekly_readings.csv"
OUTPUT_DB_JSON = DATA_DIR / "energy_db.json"
OUTPUT_VALIDATION = DATA_DIR / "validation_report.json"

MAIN_METER_CODES = {"MDB", "Main", "SCB21"}
DEPARTMENTS = ["สก.ชธธ.", "อบค.", "อบฟ.", "อบย.", "อรอ.", "อคม.", "อหข."]
EPSILON = 0.000001
AUTO_RESET_RATIO_THRESHOLD = 100


def read_csv(path):
    encodings = ["utf-8-sig", "utf-8", "cp874", "tis-620"]
    last_error = None

    for enc in encodings:
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                return list(csv.DictReader(f))
        except FileNotFoundError:
            return []
        except Exception as e:
            last_error = e

    raise last_error


def read_json(path):
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def to_float(value):
    if value is None:
        return None

    text = str(value).strip().replace(",", "")

    if text == "":
        return None

    try:
        return float(text)
    except ValueError:
        return None


def parse_date(value):
    text = str(value or "").strip()

    if text == "":
        raise ValueError("blank date")

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass

    raise ValueError(f"invalid date format: {text}")


def iso_week_id(date_text):
    dt = datetime.strptime(date_text, "%Y-%m-%d")
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def clean_unit(unit):
    text = str(unit or "").strip().lower()

    if text in {"mwh", "mwhr", "mwh.", "เมกะวัตต์ชั่วโมง"}:
        return "MWh"

    if text in {"kwh", "kwhr", "kwh.", "หน่วย", "ยูนิต"}:
        return "kWh"

    return ""


def get_col(row, *names):
    for name in names:
        if name in row:
            return row.get(name)

    normalized = {
        str(k).strip().lower().replace(" ", "").replace("_", ""): v
        for k, v in row.items()
    }

    for name in names:
        key = str(name).strip().lower().replace(" ", "").replace("_", "")
        if key in normalized:
            return normalized[key]

    return ""



def is_new_meter_note(value):
    return "new" in str(value or "").strip().lower()

def declared_to_kwh(raw_value, raw_unit, default_unit):
    unit = clean_unit(raw_unit) or clean_unit(default_unit) or "kWh"
    return raw_value * 1000 if unit == "MWh" else raw_value


def normalize_current(raw_value, raw_unit, default_unit, previous_kwh, new_meter=False):
    flags = []

    # Blank or 0 = not recorded yet. Use previous valid reading.
    if raw_value is None or raw_value == 0:
        if previous_kwh is None:
            return None, ["MISSING_FIRST_READING"]
        return previous_kwh, ["MISSING_READING_USED_PREVIOUS"]

    declared = declared_to_kwh(raw_value, raw_unit, default_unit)

    if previous_kwh is None:
        return round(declared, 3), flags

    # If note contains "New", treat this as new/replaced meter.
    # Keep declared unit value and do not auto-convert kWh/MWh by continuity.
    if new_meter:
        flags.append("NEW_METER_NOTE_DECLARED_VALUE_KEPT")
        return round(declared, 3), flags

    # Narrow reset guard before continuity scoring.
    if declared < previous_kwh:
        ratio = previous_kwh / max(declared, 1)
        if ratio >= AUTO_RESET_RATIO_THRESHOLD:
            flags.append("METER_RESET_AUTO_DECLARED_VALUE_KEPT")
            return round(declared, 3), flags

    unit = clean_unit(raw_unit) or clean_unit(default_unit) or "kWh"
    candidates = [
        ("declared", declared),
        ("as_kwh", raw_value),
        ("as_mwh", raw_value * 1000),
    ]

    unique = []
    seen = set()

    for label, value in candidates:
        key = round(value, 6)
        if key not in seen:
            seen.add(key)
            unique.append((label, value))

    scored = []

    for label, value in unique:
        delta = value - previous_kwh
        if delta >= 0:
            score = delta
        else:
            score = abs(delta) * 1000 + 1_000_000_000
        scored.append((score, label, value))

    scored.sort(key=lambda x: x[0])
    _, chosen_label, chosen_value = scored[0]

    if chosen_label != "declared":
        flags.append("UNIT_SUSPECT_AUTO_CORRECTED")

    if chosen_label == "as_kwh" and unit == "MWh":
        flags.append("RAW_LOOKS_KWH_BUT_UNIT_SAYS_MWH")

    if chosen_label == "as_mwh" and unit == "kWh":
        flags.append("RAW_LOOKS_MWH_BUT_UNIT_SAYS_KWH")

    return round(chosen_value, 3), flags


def calculate_usage(previous_kwh, current_kwh, flags):
    if previous_kwh is None or current_kwh is None:
        return 0, list(flags)

    usage = current_kwh - previous_kwh
    out_flags = list(flags)

    # Same reading = no usage.
    if abs(current_kwh - previous_kwh) < EPSILON:
        usage = 0
        out_flags.append("UNCHANGED_READING_USAGE_ZERO")

    # Negative delta = reset only if collapse is very large.
    elif usage < 0:
        ratio = previous_kwh / max(current_kwh, 1)

        if ratio >= AUTO_RESET_RATIO_THRESHOLD:
            usage = current_kwh
            out_flags.append("METER_RESET_PREVIOUS_TREATED_AS_ZERO")
        else:
            usage = 0
            out_flags.append("NEGATIVE_DELTA_INVALID_USAGE_ZERO")

    if usage < 0:
        usage = 0

    return round(usage, 3), sorted(set(out_flags))


def build_allocation_map(rows):
    allocations_by_meter = defaultdict(list)
    current_department = ""
    used_count = 0

    for row_no, row in enumerate(rows, start=2):
        raw_department = str(get_col(row, "department", "หน่วยงาน", "ฝ่าย")).strip()

        if raw_department:
            current_department = raw_department

        department = current_department
        meter_id = str(get_col(row, "meter_id", "Meter ID", "มิเตอร์", "รหัสมิเตอร์")).strip()

        ratio = to_float(get_col(row, "allocation_ratio", "ratio", "สัดส่วน"))

        if ratio is None:
            pct = to_float(get_col(row, "allocation_percent", "percent", "%"))
            ratio = pct / 100 if pct is not None else None

        if department == "" or meter_id == "" or ratio is None or ratio <= 0:
            continue

        allocations_by_meter[meter_id].append({
            **row,
            "meter_id": meter_id,
            "department": department,
            "allocation_ratio": ratio,
            "allocation_row_no": row_no,
        })
        used_count += 1

    return allocations_by_meter, used_count


def load_master():
    meter_master = read_csv(METER_MASTER_FILE)
    department_allocations = read_csv(DEPARTMENT_ALLOCATIONS_FILE)
    building_allocations = read_csv(BUILDING_ALLOCATIONS_FILE)

    meter_by_id = {}

    for row in meter_master:
        meter_id = str(row.get("meter_id", "")).strip()
        if meter_id:
            meter_by_id[meter_id] = row

    building_map, building_used = build_allocation_map(building_allocations)
    old_map, old_used = build_allocation_map(department_allocations)

    if building_used > 0:
        allocation_map = building_map
        allocation_source = building_allocations
        allocation_mode = "building_ratio_sheet_filldown_department"
    else:
        allocation_map = old_map
        allocation_source = department_allocations
        allocation_mode = "fallback_department_allocations"

    return meter_master, allocation_source, meter_by_id, allocation_map, building_used, old_used, allocation_mode


def collect_form_readings(meter_by_id, validation):
    raw_rows = []
    form_files = sorted(p for p in FORMS_DIR.glob("*.csv") if not p.name.startswith("_"))
    seen = set()

    for form_path in form_files:
        rows = read_csv(form_path)

        for row_no, row in enumerate(rows, start=2):
            meter_id = str(row.get("meter_id", "")).strip()

            if meter_id == "":
                continue

            if meter_id not in meter_by_id:
                validation["warnings"].append({
                    "file": str(form_path.relative_to(ROOT)),
                    "row": row_no,
                    "warning": "UNKNOWN_METER_ID",
                    "meter_id": meter_id,
                })
                continue

            try:
                reading_date = parse_date(row.get("reading_date", ""))
            except Exception as e:
                validation["warnings"].append({
                    "file": str(form_path.relative_to(ROOT)),
                    "row": row_no,
                    "warning": "INVALID_DATE",
                    "detail": str(e),
                    "meter_id": meter_id,
                })
                continue

            duplicate_key = (reading_date, meter_id)
            if duplicate_key in seen:
                validation["warnings"].append({
                    "file": str(form_path.relative_to(ROOT)),
                    "row": row_no,
                    "warning": "DUPLICATE_READING_FOR_SAME_DATE_AND_METER_SKIPPED",
                    "meter_id": meter_id,
                    "reading_date": reading_date,
                })
                continue

            seen.add(duplicate_key)

            raw_rows.append({
                "source_form": str(form_path.relative_to(ROOT)),
                "reading_date": reading_date,
                "week_id": str(row.get("week_id", "")).strip() or iso_week_id(reading_date),
                "meter_id": meter_id,
                "raw_reading": to_float(row.get("raw_reading")),
                "raw_unit": row.get("raw_unit", ""),
                "reader": row.get("reader", ""),
                "note": row.get("note", ""),
            })

    raw_rows.sort(key=lambda x: (x["meter_id"], x["reading_date"]))
    return raw_rows, form_files


def build():
    validation = {
        "errors": [],
        "warnings": [],
        "stats": {},
    }

    meter_master, allocation_source, meter_by_id, allocation_map, building_used, old_used, allocation_mode = load_master()
    building_alias = read_json(BUILDING_ALIAS_FILE)
    raw_rows, form_files = collect_form_readings(meter_by_id, validation)

    rows_by_meter = defaultdict(list)
    for row in raw_rows:
        rows_by_meter[row["meter_id"]].append(row)

    weekly_readings = []
    normalized_readings = []
    weekly_consumption = []
    department_weekly = []

    for meter_id, rows in rows_by_meter.items():
        rows.sort(key=lambda x: x["reading_date"])

        meter = meter_by_id[meter_id]
        default_unit = meter.get("default_unit", "")
        subb_code = str(meter.get("subb_code", "")).strip()
        is_main_meter = subb_code in MAIN_METER_CODES

        previous_kwh = None
        previous_date = None

        for row in rows:
            new_meter = is_new_meter_note(row.get("note", ""))

            current_kwh, norm_flags = normalize_current(
                raw_value=row["raw_reading"],
                raw_unit=row.get("raw_unit", ""),
                default_unit=default_unit,
                previous_kwh=previous_kwh,
                new_meter=new_meter,
            )

            usage_kwh, flags = calculate_usage(previous_kwh, current_kwh, norm_flags)

            if new_meter and previous_kwh is not None and current_kwh is not None:
                usage_kwh = round(current_kwh, 3)
                flags = sorted(set(list(flags) + ["NEW_METER_NOTE_USAGE_EQUALS_CURRENT"]))

            weekly_readings.append({
                "source_form": row["source_form"],
                "reading_date": row["reading_date"],
                "week_id": row["week_id"],
                "meter_id": meter_id,
                "b_code": meter.get("b_code", ""),
                "subb_code": subb_code,
                "building_name": meter.get("building_name", ""),
                "raw_reading": "" if row["raw_reading"] is None else row["raw_reading"],
                "raw_unit": row.get("raw_unit", "") or default_unit or "kWh",
                "normalized_kwh": "" if current_kwh is None else round(current_kwh, 3),
                "usage_kwh": round(usage_kwh, 3),
                "reader": row.get("reader", ""),
                "note": row.get("note", ""),
            })

            if current_kwh is not None:
                normalized_readings.append({
                    "source_form": row["source_form"],
                    "reading_date": row["reading_date"],
                    "week_id": row["week_id"],
                    "meter_id": meter_id,
                    "b_code": meter.get("b_code", ""),
                    "subb_code": subb_code,
                    "building_name": meter.get("building_name", ""),
                    "normalized_kwh": round(current_kwh, 3),
                    "is_main_meter": is_main_meter,
                    "flags": sorted(set(norm_flags)),
                })

            if previous_kwh is not None and current_kwh is not None:
                weekly_consumption.append({
                    "week_start_date": previous_date,
                    "week_end_date": row["reading_date"],
                    "reading_date": row["reading_date"],
                    "week_id": row["week_id"],
                    "meter_id": meter_id,
                    "b_code": meter.get("b_code", ""),
                    "subb_code": subb_code,
                    "building_name": meter.get("building_name", ""),
                    "is_main_meter": is_main_meter,
                    "kwh": round(usage_kwh, 3),
                    "raw_delta_kwh": round(current_kwh - previous_kwh, 3),
                    "previous_normalized_kwh": round(previous_kwh, 3),
                    "current_normalized_kwh": round(current_kwh, 3),
                    "flags": sorted(set(flags)),
                })

            if current_kwh is not None:
                previous_kwh = current_kwh
                previous_date = row["reading_date"]

    for week in weekly_consumption:
        if not week.get("is_main_meter"):
            continue

        allocations = allocation_map.get(week["meter_id"], [])

        if not allocations:
            validation["warnings"].append({
                "warning": "NO_ALLOCATION_FOR_METER",
                "meter_id": week["meter_id"],
                "building_name": week.get("building_name", ""),
            })
            continue

        for alloc in allocations:
            department = str(alloc.get("department", "")).strip()
            ratio = to_float(alloc.get("allocation_ratio")) or 0

            if department == "" or ratio <= 0:
                continue

            if department not in DEPARTMENTS:
                validation["warnings"].append({
                    "warning": "UNKNOWN_DEPARTMENT",
                    "department": department,
                    "meter_id": week["meter_id"],
                })

            department_weekly.append({
                "week_start_date": week["week_start_date"],
                "week_end_date": week["week_end_date"],
                "reading_date": week["week_end_date"],
                "week_id": week["week_id"],
                "department": department,
                "meter_id": week["meter_id"],
                "b_code": week.get("b_code", ""),
                "building_name": week.get("building_name", ""),
                "allocation_ratio": ratio,
                "allocation_row_no": alloc.get("allocation_row_no", ""),
                "kwh": round(week["kwh"] * ratio, 3),
                "source_flags": week.get("flags", []),
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

    validation["stats"] = {
        "meters": len(meter_master),
        "form_files_read": len(form_files),
        "weekly_forms_rows_used": len(raw_rows),
        "normalized_readings": len(normalized_readings),
        "weekly_consumption_rows": len(weekly_consumption),
        "department_weekly_rows": len(department_weekly),
        "allocation_rows_used": sum(len(v) for v in allocation_map.values()),
        "building_allocation_rows_used": building_used,
        "fallback_allocation_rows_available": old_used,
        "allocation_mode": allocation_mode,
        "main_meter_codes": sorted(MAIN_METER_CODES),
    }

    return {
        "meta": {
            "site": "กฟผ. สำนักงานไทรน้อย",
            "version": "production-v20-new-meter-note-and-building-alias",
            "base_unit": "kWh",
            "main_meter_codes": sorted(MAIN_METER_CODES),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "missing_reading_logic": "Blank or 0 raw_reading uses previous valid reading.",
            "unchanged_reading_logic": "If current equals previous, usage is 0.",
            "allocation_logic": "Fill-down department, then use row allocation_ratio directly.",
        },
        "departments": DEPARTMENTS,
        "building_alias": building_alias,
        "meters": meter_master,
        "allocation_source": allocation_source,
        "weekly_readings": weekly_readings,
        "normalized_readings": normalized_readings,
        "weekly_consumption": weekly_consumption,
        "department_weekly": department_weekly,
        "monthly_by_department": monthly_by_department,
        "validation": validation,
    }


def write_outputs(db):
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    weekly_fields = [
        "source_form",
        "reading_date",
        "week_id",
        "meter_id",
        "b_code",
        "subb_code",
        "building_name",
        "raw_reading",
        "raw_unit",
        "normalized_kwh",
        "usage_kwh",
        "reader",
        "note",
    ]

    write_csv(OUTPUT_WEEKLY_READINGS, db["weekly_readings"], weekly_fields)

    with open(OUTPUT_DB_JSON, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

    with open(OUTPUT_VALIDATION, "w", encoding="utf-8") as f:
        json.dump(db["validation"], f, ensure_ascii=False, indent=2)


def main():
    db = build()
    write_outputs(db)

    print(json.dumps(db["validation"]["stats"], ensure_ascii=False, indent=2))

    if db["validation"]["errors"]:
        print(f"Validation errors: {len(db['validation']['errors'])}")

    print("Build completed")


if __name__ == "__main__":
    main()

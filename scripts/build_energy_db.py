import csv
import json
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


def read_csv(path):
    encodings = ["utf-8-sig", "utf-8", "cp874"]

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


def parse_reading_date(reading_date):
    reading_date = str(reading_date).strip()

    try:
        dt = datetime.strptime(reading_date, "%Y-%m-%d")
    except:
        dt = datetime.strptime(reading_date, "%d/%m/%Y")

    return dt.strftime("%Y-%m-%d")


def normalize_kwh(value, unit):
    if value is None or value == "":
        return None

    raw = str(value).replace(",", "").strip()

    if raw == "":
        return None

    num = float(raw)

    unit = str(unit).strip().lower()

    if unit == "mwh":
        num = num * 1000

    return round(num, 3)


def build():
    validation = {
        "errors": [],
        "warnings": [],
        "stats": {}
    }

    meter_master = read_csv(METER_MASTER_FILE)
    department_allocations = read_csv(DEPARTMENT_ALLOCATIONS_FILE)

    meter_by_id = {}

    for row in meter_master:
        meter_by_id[row["meter_id"]] = row

    all_readings = []

    form_files = sorted(FORMS_DIR.glob("*.csv"))

    for form_path in form_files:
        rows = read_csv(form_path)

        for idx, row in enumerate(rows, start=2):
            try:
                reading_date = parse_reading_date(
                    row.get("reading_date", "")
                )

                week_id = row.get("week_id", "").strip()
                meter_id = row.get("meter_id", "").strip()

                raw_reading = row.get("raw_reading", "")
                raw_unit = row.get("raw_unit", "kWh")

                normalized_kwh = normalize_kwh(
                    raw_reading,
                    raw_unit
                )

                if meter_id == "":
                    validation["errors"].append({
                        "file": form_path.name,
                        "row": idx,
                        "error": "MISSING_METER_ID"
                    })
                    continue

                if normalized_kwh is None:
                    validation["errors"].append({
                        "file": form_path.name,
                        "row": idx,
                        "error": "INVALID_READING"
                    })
                    continue

                meter_info = meter_by_id.get(meter_id, {})

                all_readings.append({
                    "reading_date": reading_date,
                    "week_id": week_id,
                    "meter_id": meter_id,
                    "subb_code": meter_info.get("subb_code", ""),
                    "building_name": meter_info.get("building_name", ""),
                    "raw_reading": raw_reading,
                    "raw_unit": raw_unit,
                    "normalized_kwh": normalized_kwh,
                    "is_main_meter": meter_info.get("subb_code", "") in MAIN_METER_CODES
                })

            except Exception as e:
                validation["errors"].append({
                    "file": form_path.name,
                    "row": idx,
                    "error": str(e)
                })

    validation["stats"] = {
        "meters": len(meter_master),
        "weekly_forms_rows_used": len(all_readings),
        "normalized_readings": len(all_readings),
        "main_meter_codes": MAIN_METER_CODES,
        "form_files_read": len(form_files)
    }

    return {
        "weekly_readings": all_readings,
        "validation": validation
    }


def write_outputs(db):
    weekly_rows = db["weekly_readings"]

    with open(OUTPUT_WEEKLY_READINGS, "w", encoding="utf-8-sig", newline="") as f:
        if len(weekly_rows) > 0:
            writer = csv.DictWriter(
                f,
                fieldnames=weekly_rows[0].keys()
            )

            writer.writeheader()
            writer.writerows(weekly_rows)

    print(f"Generated {OUTPUT_WEEKLY_READINGS}")

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
            f"Validation failed with {len(db['validation']['errors'])} error(s). "
            f"See data/validation_report.json"
        )

    print("Build completed")


if __name__ == "__main__":
    main()

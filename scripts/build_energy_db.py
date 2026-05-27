# scripts/build_energy_db.py

```python
import csv
import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
FORMS_DIR = ROOT / "forms"
DATA_DIR = ROOT / "data"
OUTPUT_JSON = DATA_DIR / "energy_db.json"
VALIDATION_JSON = DATA_DIR / "validation_report.json"
WEEKLY_READINGS_CSV = DATA_DIR / "weekly_readings.csv"

MAIN_CODES = {"MDB", "Main", "SCB21"}
EPSILON = 0.000001


# --------------------------------------------------
# HELPERS
# --------------------------------------------------


def to_float(value):
    try:
        if value is None:
            return None

        text = str(value).strip().replace(",", "")

        if text == "":
            return None

        return float(text)

    except:
        return None



def parse_date(value):
    text = str(value).strip()

    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except:
            pass

    raise ValueError(f"invalid date format: {value}")



def load_csv(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))



def safe_current_reading(raw_value, previous_value):
    if raw_value is None:
        return previous_value

    text = str(raw_value).strip()

    if text == "":
        return previous_value

    try:
        value = float(text)
    except:
        return previous_value

    # zero means not recorded yet
    if value == 0:
        return previous_value

    return value


# --------------------------------------------------
# LOAD MASTER FILES
# --------------------------------------------------

meter_master = load_csv(DATA_DIR / "meter_master.csv")
department_allocations = load_csv(DATA_DIR / "department_allocations.csv")

meters_by_id = {}

for row in meter_master:
    meter_id = row.get("meter_id", "").strip()

    if meter_id:
        meters_by_id[meter_id] = row


# --------------------------------------------------
# VALIDATION
# --------------------------------------------------

validation = {
    "errors": [],
    "warnings": [],
    "stats": {
        "meters": len(meter_master),
        "form_files_read": 0,
        "weekly_forms_rows_used": 0,
        "normalized_readings": 0,
        "weekly_consumption_rows": 0,
        "department_weekly_rows": 0,
        "main_meter_codes": list(MAIN_CODES)
    }
}


# --------------------------------------------------
# LOAD WEEKLY FORMS
# --------------------------------------------------

raw_rows = []

form_files = sorted(FORMS_DIR.glob("*.csv"))

for form_path in form_files:

    validation["stats"]["form_files_read"] += 1

    try:
        rows = load_csv(form_path)
    except Exception as e:
        validation["errors"].append({
            "file": form_path.name,
            "error": str(e)
        })
        continue

    for row_no, row in enumerate(rows, start=2):

        meter_id = str(row.get("meter_id", "")).strip()

        if meter_id == "":
            continue

        if meter_id not in meters_by_id:
            validation["warnings"].append({
                "file": form_path.name,
                "row": row_no,
                "warning": "UNKNOWN_METER_ID",
                "meter_id": meter_id
            })
            continue

        try:
            reading_date = parse_date(row.get("reading_date"))
        except Exception:
            validation["warnings"].append({
                "file": form_path.name,
                "row": row_no,
                "warning": "INVALID_DATE",
                "value": row.get("reading_date")
            })
            continue

        raw_reading = to_float(row.get("raw_reading"))

        raw_rows.append({
            "meter_id": meter_id,
            "reading_date": reading_date,
            "raw_reading": raw_reading,
            "source_file": form_path.name
        })

        validation["stats"]["weekly_forms_rows_used"] += 1


# --------------------------------------------------
# GROUP BY METER
# --------------------------------------------------

rows_by_meter = defaultdict(list)

for row in raw_rows:
    rows_by_meter[row["meter_id"]].append(row)


# --------------------------------------------------
# BUILD DATABASE
# --------------------------------------------------

normalized_readings = []
weekly_consumption = []
department_weekly = []
weekly_readings_export = []

for meter_id, rows in rows_by_meter.items():

    rows.sort(key=lambda r: r["reading_date"])

    meter_meta = meters_by_id[meter_id]

    building_name = meter_meta.get("building_name")
    b_code = meter_meta.get("b_code")
    subb_code = meter_meta.get("subb_code")
    default_unit = meter_meta.get("default_unit", "kWh")

    is_main_meter = subb_code in MAIN_CODES

    previous_normalized = None

    for row in rows:

        raw_reading = row["raw_reading"]

        current_normalized = safe_current_reading(
            raw_reading,
            previous_normalized
        )

        if current_normalized is None:
            current_normalized = 0

        # MWh -> kWh
        if default_unit.upper() == "MWH":
            current_normalized *= 1000

        flags = []
        usage_kwh = 0

        if previous_normalized is not None:

            usage_kwh = current_normalized - previous_normalized

            # same reading => 0 usage
            if abs(current_normalized - previous_normalized) < EPSILON:
                usage_kwh = 0
                flags.append("UNCHANGED_READING_USAGE_ZERO")

            # reset handling
            elif usage_kwh < 0:

                reset_ratio = previous_normalized / max(current_normalized, 1)

                if reset_ratio >= 100:
                    usage_kwh = current_normalized
                    flags.append("METER_RESET_DETECTED")

                else:
                    usage_kwh = 0
                    flags.append("NEGATIVE_DELTA_INVALID")

        if usage_kwh < 0:
            usage_kwh = 0

        normalized_readings.append({
            "meter_id": meter_id,
            "building_name": building_name,
            "b_code": b_code,
            "reading_date": row["reading_date"],
            "normalized_kwh": round(current_normalized, 2),
            "is_main_meter": is_main_meter
        })

        weekly_consumption.append({
            "meter_id": meter_id,
            "building_name": building_name,
            "b_code": b_code,
            "reading_date": row["reading_date"],
            "kwh": round(usage_kwh, 2),
            "previous_normalized_kwh": previous_normalized,
            "current_normalized_kwh": current_normalized,
            "flags": flags,
            "is_main_meter": is_main_meter
        })

        weekly_readings_export.append({
            "meter_id": meter_id,
            "building_name": building_name,
            "reading_date": row["reading_date"],
            "raw_reading": raw_reading,
            "normalized_kwh": round(current_normalized, 2),
            "usage_kwh": round(usage_kwh, 2)
        })

        previous_normalized = current_normalized


# --------------------------------------------------
# DEPARTMENT ALLOCATION
# --------------------------------------------------

alloc_by_meter = defaultdict(list)

for alloc in department_allocations:

    meter_id = str(alloc.get("meter_id", "")).strip()

    if meter_id == "":
        continue

    alloc_by_meter[meter_id].append(alloc)


for wc in weekly_consumption:

    meter_id = wc["meter_id"]

    for alloc in alloc_by_meter.get(meter_id, []):

        ratio = to_float(alloc.get("allocation_ratio")) or 0

        if ratio <= 0:
            continue

        department_weekly.append({
            "department": alloc.get("department"),
            "meter_id": meter_id,
            "building_name": wc["building_name"],
            "reading_date": wc["reading_date"],
            "allocation_ratio": ratio,
            "kwh": round(wc["kwh"] * ratio, 2)
        })


# --------------------------------------------------
# WRITE weekly_readings.csv
# --------------------------------------------------

with open(WEEKLY_READINGS_CSV, "w", newline="", encoding="utf-8-sig") as f:

    writer = csv.DictWriter(
        f,
        fieldnames=[
            "meter_id",
            "building_name",
            "reading_date",
            "raw_reading",
            "normalized_kwh",
            "usage_kwh"
        ]
    )

    writer.writeheader()

    for row in weekly_readings_export:
        writer.writerow(row)


# --------------------------------------------------
# STATS
# --------------------------------------------------

validation["stats"]["normalized_readings"] = len(normalized_readings)
validation["stats"]["weekly_consumption_rows"] = len(weekly_consumption)
validation["stats"]["department_weekly_rows"] = len(department_weekly)


# --------------------------------------------------
# OUTPUT JSON
# --------------------------------------------------

energy_db = {
    "meta": {
        "generated_at": datetime.now().isoformat(),
        "version": "production-ready-fixed-build",
        "main_meter_codes": list(MAIN_CODES)
    },
    "meters": meter_master,
    "department_allocations": department_allocations,
    "normalized_readings": normalized_readings,
    "weekly_consumption": weekly_consumption,
    "department_weekly": department_weekly
}


with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
    json.dump(energy_db, f, ensure_ascii=False, indent=2)


with open(VALIDATION_JSON, "w", encoding="utf-8") as f:
    json.dump(validation, f, ensure_ascii=False, indent=2)


print("SUCCESS")
print(f"weekly rows: {len(weekly_readings_export)}")
print(f"weekly consumption: {len(weekly_consumption)}")
print(f"department rows: {len(department_weekly)}")


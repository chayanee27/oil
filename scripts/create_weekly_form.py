#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Create a new blank weekly form from data/meter_master.csv.
Usage:
  python scripts/create_weekly_form.py 2026-05-29
"""
from __future__ import annotations
import csv, sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
FORMS_DIR = ROOT / "forms"
FIELDS = ["reading_date","week_id","sort_order","meter_id","b_code","floor","subb_code","building_name","raw_reading","raw_unit","reader","note"]

def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/create_weekly_form.py YYYY-MM-DD")
    date = sys.argv[1].strip()
    dt = datetime.strptime(date, "%Y-%m-%d")
    week_id = f"{dt.isocalendar().year}-W{dt.isocalendar().week:02d}"
    out = FORMS_DIR / f"{week_id}.csv"
    if out.exists():
        raise SystemExit(f"Form already exists: {out}")
    with (DATA_DIR / "meter_master.csv").open("r", encoding="utf-8-sig", newline="") as f:
        meters = list(csv.DictReader(f))
    FORMS_DIR.mkdir(exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for m in meters:
            w.writerow({
                "reading_date": date,
                "week_id": week_id,
                "sort_order": m.get("sort_order", ""),
                "meter_id": m.get("meter_id", ""),
                "b_code": m.get("b_code", ""),
                "floor": m.get("floor", ""),
                "subb_code": m.get("subb_code", ""),
                "building_name": m.get("building_name", ""),
                "raw_reading": "",
                "raw_unit": m.get("default_unit", "kWh"),
                "reader": "",
                "note": "",
            })
    print(f"Created {out}")

if __name__ == "__main__":
    main()

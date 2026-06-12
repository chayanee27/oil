#!/usr/bin/env python3
"""
build_fuel_db.py
────────────────
Reads all fuel CSV files from forms/fuel/ folder,
cleans & normalises data, and writes:
  • data/fuel_db.json   — aggregated dashboard data
  • data/fuel_records.csv — clean flat records (audit trail)

Run manually or via GitHub Actions after uploading a new monthly CSV.
Usage:
  python scripts/build_fuel_db.py
"""

import sys, os, re, json
import pandas as pd
from pathlib import Path
from datetime import datetime

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT        = Path(__file__).resolve().parent.parent
FORMS_DIR   = ROOT / "forms" / "fuel"
DATA_DIR    = ROOT / "data"
OUT_JSON    = DATA_DIR / "fuel_db.json"
OUT_CSV     = DATA_DIR / "fuel_records.csv"

DATA_DIR.mkdir(parents=True, exist_ok=True)
FORMS_DIR.mkdir(parents=True, exist_ok=True)

# ── Canonical column mapping (handles messy xlsx-exported headers) ─────────────
COL_MAP = {
    "วันที่เติมน้ำมัน (ตามใบเสร็จ)": "วันที่",
    "ฝ่าย1": "ฝ่าย",
    "กอง": "กอง",
    "แผนก": "แผนก",
    "ประเภทน้ำมัน": "ประเภทน้ำมัน",
    "จำนวนน้ำมันที่เติม": "ปริมาณ",
    "ราคาน้ำมันที่จ่าย": "ราคา",
    "ทะเบียนรถ": "ทะเบียนรถ",
    "เลขข้างรถ": "เลขข้างรถ",
    "ยืนยัน": "ยืนยัน",
}

# ── Cleansing helpers ──────────────────────────────────────────────────────────
_DEPT_NORM = {
    "อคม": "อคม.", "อบค": "อบค.", "อบฟ": "อบฟ.",
    "อบย": "อบย.", "ชธธ": "ชธธ.",
}

def _strip(val):
    return re.sub(r"\s+", "", str(val).strip()) if pd.notna(val) else ""

def clean_dept(val):
    s = _strip(val)
    if not s or s == "-":
        return "ไม่ระบุ"
    key = s.rstrip(".")
    return _DEPT_NORM.get(key, s)

def clean_unit(val):
    """Normalise กอง/แผนก: collapse whitespace, ensure trailing dot."""
    s = _strip(val)
    if not s or s == "-":
        return "ไม่ระบุ"
    return s.rstrip(".") + "."

def clean_price(val):
    if pd.isna(val):
        return 0.0
    s = str(val).replace(",", "").replace("บ.", "").replace("บาท", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0

# ── Load all CSVs in forms/fuel/ ───────────────────────────────────────────────
def load_forms():
    frames = []
    csv_files = sorted(FORMS_DIR.glob("*.csv"))
    if not csv_files:
        print(f"[WARN] No CSV files found in {FORMS_DIR}")
        return pd.DataFrame()
    for f in csv_files:
        print(f"  Loading {f.name} …")
        try:
            df = pd.read_csv(f, encoding="utf-8-sig")
        except Exception:
            df = pd.read_csv(f, encoding="utf-8")
        # Normalise column names (strip whitespace, NBSP)
        df.columns = [c.strip().replace("\xa0", "").replace("\n", "").strip()
                      for c in df.columns]
        frames.append(df)
    return pd.concat(frames, ignore_index=True)

# ── Transform ─────────────────────────────────────────────────────────────────
def transform(df: pd.DataFrame) -> pd.DataFrame:
    # Rename to canonical names
    df = df.rename(columns={k: v for k, v in COL_MAP.items() if k in df.columns})

    # Apply cleaning
    df["ฝ่าย"]        = df["ฝ่าย"].apply(clean_dept)
    df["กอง"]         = df["กอง"].apply(clean_unit)
    df["แผนก"]        = df["แผนก"].apply(clean_unit)
    df["ราคา"]        = df["ราคา"].apply(clean_price)
    df["ปริมาณ"]      = pd.to_numeric(df["ปริมาณ"], errors="coerce").fillna(0)
    df["ประเภทน้ำมัน"] = df.get("ประเภทน้ำมัน", pd.Series(dtype=str)).fillna("ดีเซล")
    df["วันที่"]      = pd.to_datetime(df["วันที่"], errors="coerce")

    # Drop rows without a valid date
    df = df.dropna(subset=["วันที่"])

    # Derived time columns
    df["ปีเดือน"] = df["วันที่"].dt.strftime("%Y-%m")
    df["ปี"]      = df["วันที่"].dt.year
    df["เดือน"]   = df["วันที่"].dt.month

    # De-duplicate (same ทะเบียนรถ + วันที่ + ปริมาณ = same receipt)
    subset_dedup = ["วันที่", "ทะเบียนรถ", "ปริมาณ", "ราคา"]
    subset_dedup = [c for c in subset_dedup if c in df.columns]
    before = len(df)
    df = df.drop_duplicates(subset=subset_dedup)
    dropped = before - len(df)
    if dropped:
        print(f"  [INFO] Removed {dropped} duplicate rows")

    return df.reset_index(drop=True)

# ── Build JSON ─────────────────────────────────────────────────────────────────
def build_json(df: pd.DataFrame) -> dict:
    months   = sorted(df["ปีเดือน"].dropna().unique().tolist())
    dept_list = [d for d in sorted(df["ฝ่าย"].unique()) if d != "ไม่ระบุ"]

    # Month labels (Thai)
    TH_MONTH = ["","ม.ค.","ก.พ.","มี.ค.","เม.ย.","พ.ค.","มิ.ย.",
                 "ก.ค.","ส.ค.","ก.ย.","ต.ค.","พ.ย.","ธ.ค."]

    def month_label(ym: str) -> str:
        try:
            y, m = int(ym[:4]), int(ym[5:7])
            return f"{TH_MONTH[m]} {y + 543}"
        except Exception:
            return ym

    # Organisation-level monthly summary
    monthly_summary = []
    for m in months:
        mdf = df[df["ปีเดือน"] == m]
        monthly_summary.append({
            "month":       m,
            "label":       month_label(m),
            "vol":         round(float(mdf["ปริมาณ"].sum()), 2),
            "cost":        round(float(mdf["ราคา"].sum()), 2),
        })

    # Department-level
    dept_data = {}
    for dept in dept_list:
        ddf = df[df["ฝ่าย"] == dept]

        dept_monthly = []
        for m in months:
            mdf2 = ddf[ddf["ปีเดือน"] == m]
            dept_monthly.append({
                "month": m,
                "label": month_label(m),
                "vol":   round(float(mdf2["ปริมาณ"].sum()), 2),
                "cost":  round(float(mdf2["ราคา"].sum()), 2),
            })

        # Division-level (กอง)
        divisions = {}
        for g in sorted(ddf["กอง"].unique()):
            gdf = ddf[ddf["กอง"] == g]
            g_monthly = []
            for m in months:
                mdf3 = gdf[gdf["ปีเดือน"] == m]
                g_monthly.append({
                    "month": m,
                    "label": month_label(m),
                    "vol":   round(float(mdf3["ปริมาณ"].sum()), 2),
                    "cost":  round(float(mdf3["ราคา"].sum()), 2),
                })
            divisions[g] = {
                "total_vol":  round(float(gdf["ปริมาณ"].sum()), 2),
                "total_cost": round(float(gdf["ราคา"].sum()), 2),
                "monthly":    g_monthly,
            }

        dept_data[dept] = {
            "total_vol":  round(float(ddf["ปริมาณ"].sum()), 2),
            "total_cost": round(float(ddf["ราคา"].sum()), 2),
            "monthly":    dept_monthly,
            "divisions":  divisions,
        }

    return {
        "generated":       datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "months":          months,
        "month_labels":    {m: month_label(m) for m in months},
        "departments":     dept_list,
        "total_vol":       round(float(df["ปริมาณ"].sum()), 2),
        "total_cost":      round(float(df["ราคา"].sum()), 2),
        "monthly_summary": monthly_summary,
        "dept_data":       dept_data,
    }

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("=== build_fuel_db.py ===")
    print(f"Forms dir : {FORMS_DIR}")
    print(f"Output    : {OUT_JSON}")

    df_raw = load_forms()
    if df_raw.empty:
        print("[ERROR] No data loaded. Exiting.")
        sys.exit(1)

    df = transform(df_raw)
    print(f"  Records after clean: {len(df)}")
    print(f"  Months   : {sorted(df['ปีเดือน'].unique())}")
    print(f"  Depts    : {[d for d in sorted(df['ฝ่าย'].unique()) if d != 'ไม่ระบุ']}")
    print(f"  Total vol: {df['ปริมาณ'].sum():.2f} L")
    print(f"  Total cost: {df['ราคา'].sum():.2f} บาท")

    # Write flat CSV
    csv_cols = ["วันที่", "ปีเดือน", "ปี", "เดือน", "ฝ่าย", "กอง", "แผนก",
                "ประเภทน้ำมัน", "ปริมาณ", "ราคา", "ทะเบียนรถ", "เลขข้างรถ"]
    csv_cols = [c for c in csv_cols if c in df.columns]
    df_out = df[csv_cols].copy()
    df_out["วันที่"] = df_out["วันที่"].dt.strftime("%Y-%m-%d")
    df_out.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    print(f"  → Wrote {OUT_CSV}")

    # Write JSON
    db = build_json(df)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    print(f"  → Wrote {OUT_JSON}")
    print("Done.")

if __name__ == "__main__":
    main()

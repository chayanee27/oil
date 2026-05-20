# Energy Dashboard Auto DB — GitHub Version

ระบบนี้ออกแบบให้ใช้กับ GitHub โดยตรง:

1. คนจดค่า RAW meter reading ทุกเช้าวันศุกร์ใน `data/weekly_readings.csv`
2. เมื่อ push ขึ้น GitHub ระบบ GitHub Actions จะรัน `scripts/build_energy_db.py` อัตโนมัติ
3. ระบบจะสร้าง/อัปเดต `data/energy_db.json`
4. `index.html` ดึง `data/energy_db.json` ไปแสดงผลเป็น dashboard

## โครงสร้างไฟล์

```text
energy-dashboard/
├─ index.html
├─ data/
│  ├─ meter_master.csv
│  ├─ department_allocations.csv
│  ├─ weekly_raw_form_template.csv
│  ├─ weekly_readings.csv
│  ├─ energy_db.json
│  └─ validation_report.json
├─ scripts/
│  └─ build_energy_db.py
└─ .github/
   └─ workflows/
      └─ build-energy-db.yml
```

## วิธีใช้งานบน GitHub

### ครั้งแรก

1. สร้าง repository ใหม่บน GitHub เช่น `energy-dashboard`
2. อัปโหลดไฟล์ทั้งหมดในชุดนี้เข้า repository
3. ไปที่ **Settings → Pages**
4. เลือก **Deploy from a branch**
5. Branch: `main`
6. Folder: `/root`
7. กด Save

หลังจากนั้น dashboard จะเปิดผ่าน GitHub Pages URL ของ repo ได้

### การจดค่าทุกวันศุกร์

แก้ไฟล์:

```text
data/weekly_readings.csv
```

ใส่ข้อมูลอย่างน้อย:

```csv
reading_date,meter_id,raw_reading,raw_unit,reader,note
2026-05-22,T.0019__FL01__MDB,9436.823,MWh,ชื่อผู้จด,
```

กติกา:

- `reading_date` ต้องเป็นรูปแบบ `YYYY-MM-DD`
- ระบบคำนวณ consumption จากผลต่างของ reading ปัจจุบันกับ reading ก่อนหน้า
- ต้องมีข้อมูลอย่างน้อย 2 สัปดาห์ต่อมิเตอร์ จึงจะมีค่า usage
- raw unit รองรับ `kWh` และ `MWh`
- ถ้าค่า RAW เป็น MWh แต่ช่อง unit ยังเป็น kWh ระบบจะพยายาม normalize ด้วย continuity check และติด flag ให้ตรวจสอบ

## Main meter rule

ระบบถือว่า `SubB.Code` ต่อไปนี้เป็น main meter:

- `MDB`
- `Main`
- `SCB21`

Dashboard รุ่นนี้แสดงผลจาก main meter ก่อนตาม requirement ปัจจุบัน

## Department allocation

ไฟล์ `data/department_allocations.csv` คือ master สำหรับคิดสัดส่วนรายฝ่าย เช่น 50%, 95%, แอร์รวม ฯลฯ โดยระบบจะคูณ consumption ของ meter ด้วย `allocation_ratio`

ตัวอย่าง:

```csv
meter_id,department,allocation_ratio
T0014_MAIN,อบฟ.,0.95
T0014_MAIN,อบย.,0.05
```

## Validation report

หลัง GitHub Actions รัน จะสร้าง:

```text
data/validation_report.json
```

ใช้ตรวจ:

- meter_id ไม่มีใน master
- วันที่ผิด format
- ค่า reading ว่าง/ไม่ใช่ตัวเลข
- unit น่าสงสัย
- delta ติดลบ
- spike ผิดปกติ

ถ้ามี error ขั้นรุนแรง workflow จะ fail เพื่อป้องกัน dashboard แสดงข้อมูลผิด

## ทดสอบในเครื่องกรณีจำเป็น

```bash
python scripts/build_energy_db.py
python -m http.server 8000
```

แล้วเปิด:

```text
http://localhost:8000
```

> ไม่แนะนำให้ดับเบิลคลิก `index.html` ตรง ๆ เพราะ browser อาจ block การ fetch JSON

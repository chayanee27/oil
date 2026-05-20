# Energy Dashboard Auto DB — Production GitHub Version

ระบบนี้ออกแบบให้ใช้กับ GitHub + GitHub Actions + GitHub Pages โดยตรง

## วิธีใช้งานประจำสัปดาห์

ทุกเช้าวันศุกร์:

1. ไปที่โฟลเดอร์ `forms/`
2. เปิดไฟล์ของสัปดาห์นั้น เช่น `2026-W21.csv`
3. กรอกเฉพาะคอลัมน์ `raw_reading`, ตรวจ `raw_unit`, ใส่ `reader`/`note` ถ้าต้องการ
4. Commit/Push เข้า GitHub
5. GitHub Actions จะรัน `scripts/build_energy_db.py` อัตโนมัติ
6. ระบบจะสร้าง/อัปเดตไฟล์:
   - `data/weekly_readings.csv` รวม RAW จากทุก weekly form
   - `data/energy_db.json` สำหรับ dashboard
   - `data/validation_report.json` สำหรับตรวจ warning/error
7. GitHub Pages แสดง `index.html` โดยอ่านข้อมูลจาก `data/energy_db.json`

## หลักการสำคัญ

- ห้ามแก้ `data/weekly_readings.csv` ด้วยมือ เพราะเป็นไฟล์ generated
- ให้แก้เฉพาะไฟล์ใน `forms/*.csv`
- 1 ไฟล์ = 1 สัปดาห์
- 1 แถว = 1 มิเตอร์
- ลำดับแถวใน form เรียงตาม `meter_master.csv` เพื่อให้จดหน้างานเหมือน RAW เดิม

## Main meter ที่ใช้แสดง dashboard

ระบบถือว่า `SubB.Code` ต่อไปนี้คือ main meter:

- `MDB`
- `Main`
- `SCB21`

Dashboard ใช้เฉพาะ main meter ก่อน ส่วนตัวย่อยยังเก็บใน RAW database ได้

## การคำนวณหน่วย

ระบบ normalize เป็น kWh เสมอ:

- ถ้า `raw_unit = kWh` → ใช้ค่าตรง
- ถ้า `raw_unit = MWh` → คูณ 1,000
- ถ้าค่า RAW/UNIT ดูผิดจากค่าก่อนหน้า ระบบจะเลือกค่าที่ต่อเนื่องที่สุดและใส่ flag เช่น `UNIT_SUSPECT`, `AUTO_CONVERTED_MWH_TO_KWH`

## การสร้าง weekly form สัปดาห์ถัดไป

รันในเครื่องหรือ GitHub Codespaces ได้:

```bash
python scripts/create_weekly_form.py 2026-05-29
```

จะได้ไฟล์:

```text
forms/2026-W22.csv
```

## การเปิด GitHub Pages

Settings → Pages → Build and deployment → Source: `Deploy from a branch` → Branch: `main` / root

## การตรวจ error

ดูที่:

```text
data/validation_report.json
```

ถ้ามี `errors` GitHub Actions จะ fail เพื่อป้องกัน dashboard ใช้ข้อมูลผิด
ถ้ามีแค่ `warnings` ยัง build ได้ แต่ควรตรวจสอบ

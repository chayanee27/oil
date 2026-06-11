# Weekly input forms

กรอกค่า RAW meter reading ในไฟล์สัปดาห์ เช่น `2026-W21.csv` เท่านั้น

- เพิ่มสัปดาห์ใหม่ด้วย `python scripts/create_weekly_form.py YYYY-MM-DD`
- อย่าแก้ `data/weekly_readings.csv` โดยตรง เพราะเป็นไฟล์ generated
- ค่า `reading_date` ใช้รูปแบบ `YYYY-MM-DD` เสมอ
- ค่า `raw_unit` ควรเป็น `kWh` หรือ `MWh`

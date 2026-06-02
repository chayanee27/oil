# 📋 คู่มือติดตั้ง Energy Meter Form
## ระบบบันทึกมิเตอร์ไฟฟ้า — ไทรน้อย

---

## ภาพรวม Flow

```
ช่าง (มือถือ) → เว็บแอพ (GitHub Pages) → Apps Script → Google Sheet + GitHub
```

---

## ขั้นตอนที่ 1 — สร้าง Google Sheet

1. ไปที่ https://sheets.google.com → สร้าง Spreadsheet ใหม่
2. ตั้งชื่อว่า `Energy Meter Data`
3. **จด Spreadsheet ID** จาก URL:
   ```
   https://docs.google.com/spreadsheets/d/[SPREADSHEET_ID]/edit
   ```
4. Sheet แรก (Sheet1) ยังไม่ต้องแก้ไขอะไร — Apps Script จะจัดการสร้าง tab ให้เอง

---

## ขั้นตอนที่ 2 — สร้าง Google Apps Script

1. ไปที่ https://script.google.com → **New Project**
2. ตั้งชื่อ project: `Energy Meter Backend`
3. ลบโค้ดเดิมทั้งหมด แล้ว **วางโค้ดจากไฟล์ `apps_script_code.js`** ทั้งหมด
4. กด **Save** (Ctrl+S)

### ตั้งค่า Script Properties (เก็บ Token อย่างปลอดภัย)

5. ไปที่ **Project Settings** (ไอคอนฟันเฟือง ⚙️ ซ้ายมือ)
6. เลื่อนลงหา **Script Properties** → กด **Add script property**
7. เพิ่ม 4 properties นี้:

| Property Name | Value |
|---|---|
| `GITHUB_TOKEN` | `ghp_xxxx` (token ใหม่ที่ regen แล้ว) |
| `GITHUB_OWNER` | `maleeart` |
| `GITHUB_REPO` | `Energy-Dashboard` |
| `SHEET_ID` | Spreadsheet ID จากขั้นตอนที่ 1 |

8. กด **Save script properties**

### Deploy เป็น Web App

9. กดปุ่ม **Deploy** (มุมขวาบน) → **New deployment**
10. กด ⚙️ ข้าง Select type → เลือก **Web app**
11. ตั้งค่า:
    - **Description**: `Energy Meter API v1`
    - **Execute as**: `Me`
    - **Who has access**: `Anyone` ← สำคัญ! ช่างถึงเรียกได้
12. กด **Deploy**
13. **คัดลอก Web App URL** เก็บไว้ (จะใช้ใน step ถัดไป)
    ```
    https://script.google.com/macros/s/XXXXXXXXXXXX/exec
    ```

> ⚠️ ทุกครั้งที่แก้โค้ด ต้อง Deploy ใหม่ (New deployment หรือ Manage deployments > Edit)

---

## ขั้นตอนที่ 3 — ตั้งค่าเว็บแอพ

1. เปิดไฟล์ `meter_form.html`
2. หาบรรทัดนี้:
   ```javascript
   const APPS_SCRIPT_URL = 'YOUR_APPS_SCRIPT_WEB_APP_URL';
   ```
3. แทนที่ `YOUR_APPS_SCRIPT_WEB_APP_URL` ด้วย URL จากขั้นตอนที่ 2
4. บันทึกไฟล์

---

## ขั้นตอนที่ 4 — อัปโหลดเว็บแอพไปยัง GitHub Pages

1. ใน repo `Energy-Dashboard` → สร้างโฟลเดอร์ `docs/` (หรือใช้ root ก็ได้)
2. อัปโหลด `meter_form.html` ไปไว้ที่ `docs/index.html`
3. ไปที่ **Settings** → **Pages**
4. Source: **Deploy from a branch** → Branch: `main` → Folder: `/docs`
5. กด **Save**
6. รอ ~2 นาที จะได้ URL:
   ```
   https://maleeart.github.io/Energy-Dashboard/
   ```

> ช่างเปิด URL นี้ในมือถือได้เลย ไม่ต้องติดตั้งอะไร

---

## ขั้นตอนที่ 5 — ตั้ง Auto Trigger (Export ทุกวันศุกร์เช้า)

1. ใน Apps Script → **Triggers** (ไอคอนนาฬิกา ⏰)
2. กด **+ Add Trigger** (มุมขวาล่าง)
3. ตั้งค่า:
   - **Function**: `autoExportFriday`
   - **Event source**: `Time-driven`
   - **Type**: `Week timer`
   - **Day**: `Every Friday`
   - **Time**: `6am to 7am`
4. กด **Save**

---

## วิธีใช้งาน (สำหรับช่าง)

1. เปิด `https://maleeart.github.io/Energy-Dashboard/` ในมือถือ
2. กรอก **ชื่อผู้อ่าน** ที่บาร์ด้านบน
3. รหัสสัปดาห์จะถูกกรอกอัตโนมัติ → กด **โหลด**
4. กดที่การ์ดอาคารที่ต้องการกรอก
5. กรอกค่า meter → เลือกหน่วย **kWh** หรือ **MWh**
6. กด **💾 บันทึก** → ข้อมูลไปยัง Google Sheet ทันที
7. หลังกรอกครบ → กด **☁️ บันทึกลง GitHub** (FAB ปุ่มลอยมุมขวาล่าง)

---

## Flow อัตโนมัติ (ไม่ต้อง manual)

```
วันศุกร์เช้า script สร้าง CSV ใหม่ใน GitHub
   ↓
ช่างเปิดแอพ → โหลดสัปดาห์นั้น → Apps Script ดึง CSV จาก GitHub มาสร้าง Sheet tab ใหม่
   ↓
ช่างกรอก → บันทึกลง Sheet
   ↓
กด Push หรือรอ Trigger ศุกร์หน้า → CSV replace ทับใน GitHub
```

---

## แก้ปัญหาที่พบบ่อย

| ปัญหา | วิธีแก้ |
|---|---|
| โหลดข้อมูลไม่ได้ | ตรวจสอบ Apps Script URL ใน HTML และ Who has access = Anyone |
| บันทึกแล้วขึ้น error | ตรวจสอบ SHEET_ID ใน Script Properties |
| Push GitHub ไม่สำเร็จ | Token หมดอายุ → regen และอัปเดตใน Script Properties |
| ช่อง week ว่าง | กรอก เช่น `2026-W23` แล้วกดโหลด |

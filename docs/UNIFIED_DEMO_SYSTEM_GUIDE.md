# HƯỚNG DẪN VẬN HÀNH HỆ THỐNG UNIFIED DEMO SYSTEM VIGIL AI

**Tài liệu:** `docs/UNIFIED_DEMO_SYSTEM_GUIDE.md`  
**Dự án:** VIGIL AI — AI-Assisted Exam Monitoring Co-Pilot  
**Phiên bản:** Unified Demo System v2.6.0 (ICTU 2026)  
**Nhánh Git:** `feat/ictu-2026-prototype-final`  
**Trạng thái:** TÀI LIỆU VẬN HÀNH CHÍNH THỨC (OFFICIAL OPERATIONAL GUIDE)  

---

## 1. KHỞI CHẠY HỆ THỐNG (QUICK START)

### Cách 1: Khởi chạy Giao diện Web & Giám sát Tự động (Khuyến nghị)
Chạy script PowerShell bằng một lệnh duy nhất:
```powershell
.\scripts\run_demo_system.ps1
```
Script sẽ:
1. Kiểm tra môi trường ảo `.\venv` và cài đặt dependencies nếu cần.
2. Khởi chạy FastAPI backend server tại `http://localhost:8000`.
3. Tự động mở trình duyệt đến trang Web Monitor tại `http://localhost:8000/demo`.

### Cách 2: Khởi chạy Server thủ công
```powershell
.\venv\Scripts\python.exe -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```
Sau đó truy cập:
- **Web Demo Monitor & Review Queue:** `http://localhost:8000/demo`
- **Dashboard Tổng quan:** `http://localhost:8000/`
- **Interactive OpenAPI Docs:** `http://localhost:8000/docs`

### Cách 3: Chạy trực tiếp CLI Runner
```powershell
# Chạy video India Classroom
.\venv\Scripts\python.exe scripts/run_demo_video.py --video india

# Chạy video Student Classroom
.\venv\Scripts\python.exe scripts/run_demo_video.py --video student

# Chạy hiển thị cửa sổ đồ họa và lớp phủ debug
.\venv\Scripts\python.exe scripts/run_demo_video.py --video india --show --debug-overlay

# Chạy toàn bộ video kiểm thử và xuất báo cáo đối đầu
.\venv\Scripts\python.exe scripts/run_demo_all_videos.py
```

---

## 2. HƯỚNG DẪN SỬ DỤNG GIAO DIỆN WEB MONITOR (`/demo`)

Giao diện Web Monitor được thiết kế chuyên biệt cho Giám thị phòng thi (Exam Proctor) theo chuẩn **Human-in-the-Loop**:

```
+-----------------------------------------------------------------------------------------------+
|  VIGIL AI | COMPETITION DEMO ENGINE                    [Preset: India/Student] [Mode: LIVE]  |
+-------------------------------------------------------------+---------------------------------+
|                                                             |  HUMAN REVIEW QUEUE             |
|  CLEAN PROCTOR LIVE STREAM (MJPEG)                          |  [All] [Pending] [Reviewed]     |
|                                                             |                                 |
|  +-------------------------------------------------------+  |  +---------------------------+  |
|  | [S01] Normal         [S02] Observe                    |  |  | SEAT S08 - PRIORITY: 86   |  |
|  |                                                       |  |  | Reason: REPEATED_GLANCE   |  |
|  |                      [S08] REVIEW REQUIRED (RED)      |  |  | Occurrences: x3           |  |
|  |                                                       |  |  | [View Evidence & Review]  |  |
|  +-------------------------------------------------------+  |  +---------------------------+  |
|                                                             |                                 |
|  TELEMETRY HUD:                                             |                                 |
|  FPS: 28.5 | Processing: 9.2 FPS | Occupied Seats: 18       |                                 |
+-------------------------------------------------------------+---------------------------------+
```

### Các Tính năng Chính trên Web:
1. **Bộ chọn Preset (Classroom Selection):**
   - **India Classroom:** Video 71.7s, 21 bàn thi hiệu chuẩn, bối cảnh lớp học đông người.
   - **Student Classroom:** Video 10.9s, 12 bàn thi hiệu chuẩn, góc máy cận.
2. **Chuyển đổi Chế độ (Execution Mode):**
   - **LIVE AI ANALYSIS:** Xử lý trực tiếp từng frame qua AI models theo thời gian thực.
   - **RECORDED ANALYSIS REPLAY:** Phát lại video và đồng bộ sự cố theo timestamp mà không tiêu tốn GPU inference.
3. **Bảng điều khiển Playback:** `Start`, `Pause`, `Resume`, `Stop`, `Reset`.
4. **Hàng đợi Thẩm định (Review Queue):**
   - Thẻ sự cố hiển thị vị trí bàn thi (ví dụ: `S08`), hành vi vi phạm nghi vấn, số lần tái phạm, và điểm rủi ro đỉnh điểm.
   - Lọc nhanh theo `ALL`, `PENDING REVIEW`, hoặc `REVIEWED`.
5. **Modal Xem Bằng chứng & Phán quyết:**
   - Xem Video Clip 10 giây (5s trước + 5s sau đỉnh điểm vi phạm).
   - Xem Ảnh chụp đỉnh điểm (Peak Snapshot) độ phân giải cao.
   - Kiểm tra mã băm bảo mật SHA-256 toàn vẹn bằng chứng.
   - Chọn phán quyết:
     - `CONFIRM` (Xác nhận gian lận / vi phạm quy chế)
     - `REJECT` (Bác bỏ / Báo động giả)
     - `INCONCLUSIVE` (Không đủ cơ sở kết luận)

---

## 3. TÀI LIỆU REST API VÀ WEBSOCKET

### REST Endpoints (`/api/v1/demo` hoặc `/demo`)

| Phương thức | Đường dẫn | Tham số | Mô tả |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/v1/demo/presets` | Không | Lấy danh sách các preset demo sẵn có và trạng thái file replay |
| `POST` | `/api/v1/demo/start` | `{preset: "india", mode: "LIVE", debug_overlay: false}` | Bắt đầu chạy demo theo preset và mode chỉ định |
| `POST` | `/api/v1/demo/pause` | Không | Tạm dừng chạy |
| `POST` | `/api/v1/demo/resume` | Không | Tiếp tục chạy |
| `POST` | `/api/v1/demo/stop` | Không | Dừng chạy |
| `POST` | `/api/v1/demo/reset` | Không | Reset trạng thái runtime và xóa hàng đợi thẩm định |
| `GET` | `/api/v1/demo/status` | Không | Lấy telemetry thời gian thực (FPS, frame index, seats, incidents) |
| `GET` | `/api/v1/demo/events` | Không | Lấy danh sách toàn bộ các sự cố thẩm định đã sinh ra |
| `GET` | `/api/v1/demo/events/{event_id}` | `event_id` | Lấy chi tiết metadata và đường dẫn bằng chứng của sự cố |
| `POST` | `/api/v1/demo/events/{event_id}/review` | `{decision: "CONFIRMED", reason_code: "PEEKING_VERIFIED", notes: "...", reviewer_id: "Proctor_01"}` | Ghi nhận phán quyết của giám thị và lưu vào SQLite DB |
| `GET` | `/api/v1/demo/stream` | Không | Luồng video MJPEG (multipart/x-mixed-replace) cho trình duyệt |

### WebSocket Endpoint (`/ws/demo`)
Cung cấp kênh truyền hai chiều thời gian thực giữa server và client:
- **`DEMO_STATUS` / `DEMO_STARTED`**: Cập nhật tiến độ xử lý, FPS, số bàn có người.
- **`REVIEW_INCIDENT`**: Bắn sự cố mới hoặc cập nhật sự cố hiện có vào Review Queue.
- **`REVIEW_DECISION`**: Thông báo phán quyết của giám thị đến toàn bộ client đang mở.

---

## 4. QUY TRÌNH THỰC THI KIỂM THỬ (TEST EXECUTION)

```powershell
# Chạy toàn bộ 237 bài test tự động
.\venv\Scripts\python.exe -m pytest -v

# Chạy riêng bộ test tích hợp hệ thống Unified Demo (DR, API, DB, SEM)
.\venv\Scripts\python.exe -m pytest tests/test_demo_system_integration.py -v

# Chạy riêng bộ test P0 Desk Gating
.\venv\Scripts\python.exe -m pytest tests/test_desk_gating.py -v
```

---

## 5. BẢO TRÌ VÀ XỬ LÝ SỰ CỐ (TROUBLESHOOTING)

1. **Không thấy video stream trên Web:**
   - Đảm bảo server đang chạy và nhấn nút **START DEMO** trên thanh công cụ.
   - Kiểm tra log terminal xem file video có tồn tại trong `demo_video/` hay không.
2. **Chế độ Replay báo lỗi thiếu file:**
   - Chế độ Replay cần có file `data/demo_final/{preset}/result.mp4` và `events.json`. Hãy chạy chế độ **LIVE** một lần để hệ thống tự động sinh các file này.
3. **Cổng 8000 bị chiếm dụng:**
   - Thay đổi cổng khi chạy script hoặc uvicorn:
     ```powershell
     .\venv\Scripts\python.exe -m uvicorn api.main:app --port 8080
     ```

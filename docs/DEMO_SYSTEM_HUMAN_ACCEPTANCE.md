# BÁO CÁO NGHIỆM THU HỆ THỐNG UNIFIED DEMO SYSTEM VIGIL AI (ICTU 2026)

**Tài liệu:** `docs/DEMO_SYSTEM_HUMAN_ACCEPTANCE.md`  
**Dự án:** VIGIL AI — AI-Assisted Exam Monitoring Co-Pilot  
**Nhánh Git:** `feat/ictu-2026-prototype-final`  
**Phiên bản:** Unified Demo System v2.6.0  
**Trạng thái:** `UNIFIED_DEMO_SYSTEM_READY_FOR_HUMAN_ACCEPTANCE`  
**Ngày nghiệm thu:** 21/09/2026  

---

## 1. TỔNG QUAN KIẾN TRÚC HỆ THỐNG UNIFIED DEMO

Hệ thống Unified Demo System của VIGIL AI đã được thiết kế và xây dựng hoàn chỉnh, thiết lập kiến trúc **Đơn nguồn Chân lý (Single Source of Truth)** xoay quanh pipeline **SRS v2.0**:

```
                                  [ RAW VIDEO STREAM / RTSP ]
                                               │
                                               ▼
                             [ STAGE 1: YOLO-Pose Multi-Person (1280px) ]
                                               │
                                               ▼
                             [ STAGE 2: Calibrated Seat ROI Mapping ]
                                   (Occlusion Coasting 4.0s)
                                               │
                                               ▼
                          [ STAGE 3: Batched 6DRepNet HPE (~5.0 Hz) ]
                               (Per-Seat Baseline Subtraction)
                                               │
                                               ▼
                           [ STAGE 4: Temporal Observation Extractor ]
                                (Torso Lean, Head Ray, Wrist Zone)
                                               │
                                               ▼
                          [ STAGE 5: Dual-Threshold Episode Engine ]
                              (28° / 16° Hysteresis, 400ms Persistence)
                                               │
                                               ▼
                         [ STAGE 6: Relational Behavior Pattern Engine ]
                             (Glance, Lean, Multi-Person Dwell)
                                               │
                                               ▼
                         [ STAGE 7: Seat Risk Tracker & Incident Aggregator ]
                              (Review Priority 0–100, Cooldown, Anti-Spam)
                                               │
                      ┌────────────────────────┴────────────────────────┐
                      ▼                                                 ▼
          [ LIVE CV INFERENCE MODE ]                       [ RECORDED REPLAY MODE ]
                      │                                                 │
                      └────────────────────────┬────────────────────────┘
                                               │
                                               ▼
                           [ classroom_monitor.demo.runtime.DemoRuntime ]
                                               │
                     ┌─────────────────────────┼─────────────────────────┐
                     ▼                         ▼                         ▼
         [ FastAPI REST Endpoints ]   [ Realtime WebSockets ]   [ SQLite Relational DB ]
           /api/v1/demo/*               /ws/demo & /stream        DetectionEvents & Reviews
                     │                         │                         │
                     └─────────────────────────┼─────────────────────────┘
                                               │
                                               ▼
                           [ WEB MONITOR & REVIEW QUEUE UI ]
                              Clean Proctor Live MJPEG Stream
                              Human-in-the-Loop Incident Cards
                              Modal: Video Clip + Snapshot + SHA-256
                              Decisions: CONFIRM | REJECT | INCONCLUSIVE
```

---

## 2. KẾT QUẢ KIỂM THỬ TÍCH HỢP TOÀN DIỆN (237/237 TESTS PASSED)

Toàn bộ 237 unit & integration tests trong hệ thống đều vượt qua 100%:

| Nhóm Kiểm thử | Số lượng | Trạng thái | Mô tả |
| :--- | :--- | :--- | :--- |
| **DR1 – DR8** | 8 tests | **PASSED** | DemoRuntime lifecycle, singleton, LIVE/REPLAY modes, pause/resume/stop/reset, thread safety, incident in-place update |
| **API1 – API7** | 7 tests | **PASSED** | REST API endpoints `/presets`, `/start`, `/pause`, `/resume`, `/stop`, `/reset`, `/status`, `/events`, `/events/{id}/review`, `/stream` (MJPEG) |
| **DB1 – DB5** | 5 tests | **PASSED** | SQLite persistence, in-place incident upsert, EventReview creation, SHA-256 preservation, transaction safety |
| **SEM1 – SEM5** | 5 tests | **PASSED** | Triệt tiêu hoàn toàn trạng thái `CHEATING`, thuật ngữ hỗ trợ ra quyết định trung tính, Cooldown decoupling, Desk gating |
| **DG1 – DG5** | 5 tests | **PASSED** | P0 Desk Gating: `BOUNDARY_ONLY / DEGRADED` đóng góp 0.0 điểm rủi ro, không kích hoạt `BELOW_DESK_INTERACTION` |
| **SRS v2 Core Suite** | 207 tests | **PASSED** | YOLO-Pose, 6DRepNet batching, Head Ray Geometry, Temporal Episodes, Composite Patterns, Scene Calibration |
| **Tổng cộng** | **237 tests** | **PASSED (100%)** | Thời gian thực thi toàn bộ test suite: **13.61 giây** |

---

## 3. MA TRẬN ĐỐI SOÁT HÀNH VI VÀ DỮ LIỆU DEMO

### 3.1. Video 1: India Classroom (`demo_video/india_classroom.mp4`)
- **Độ phân giải / FPS:** 1280x720 @ 30.0 FPS (71.7 giây, 2153 frames).
- **Phòng / Camera:** `ROOM-CALIB-01` | `CAM-CALIB-01` | 21 vị trí bàn thi được hiệu chuẩn.
- **Kết quả thực thi:**
  - **Tốc độ xử lý:** ~8.9 FPS (RTX 4060).
  - **Canonical Episodes sinh ra:** 491 episodes.
  - **Review Incidents đã gom nhóm:** 37 sự cố thẩm định (giảm triệt để hiện tượng alert spam từ 200+ events xuống 37 incidents có ý nghĩa).
  - **Hành vi phát hiện:** `REPEATED_NEIGHBOR_GLANCE` (quay đầu nhìn bài bạn), `MULTI_PERSON_DWELL_NEAR_SEAT` (giám thị/thí sinh đứng lâu cạnh bàn), `NEIGHBOR_ORIENTED_LEAN` (nghiêng người sang bàn bạn).

### 3.2. Video 2: Student Classroom (`demo_video/student_classroom.mp4`)
- **Độ phân giải / FPS:** 640x352 @ 20.0 FPS (10.9 giây, 219 frames).
- **Phòng / Camera:** `ROOM-STUDENT-01` | `CAM-STUDENT-01` | 12 vị trí bàn thi được hiệu chuẩn.
- **Kết quả thực thi:**
  - **Tốc độ xử lý:** ~14.4 FPS.
  - **Canonical Episodes sinh ra:** 55 episodes.
  - **Review Incidents đã gom nhóm:** 1 sự cố thẩm định (`MULTI_PERSON_DWELL_NEAR_SEAT`).
  - **Bằng chứng đã lưu:** 1 video clip MP4 (10s) + Snapshot JPEG + SHA-256 hash.

---

## 4. XÁC MINH CÁC TIÊU CHUẨN NGHIỆM THU NGHIỆP VỤ (HUMAN ACCEPTANCE CRITERIA)

### 4.1. Đơn nguồn Chân lý (Single Source of Truth)
- [x] Không tồn tại module AI thứ hai phục vụ riêng Web UI hay CLI.
- [x] Cả CLI (`run_demo_video.py`), API (`api/routes/demo.py`) và Web Monitor (`/demo`) đều sử dụng chung `classroom_monitor.demo.runtime.DemoRuntime`.

### 4.2. Khả năng Chạy 2 Chế độ (Dual Mode Execution)
- [x] **LIVE AI ANALYSIS:** Chạy trực tiếp qua YOLO-Pose + 6DRepNet + Temporal Engine + Risk Tracker, cập nhật streaming frame và review cards tức thời.
- [x] **RECORDED ANALYSIS REPLAY:** Đọc video `result.mp4` và `events.json` đã sinh sẵn, phát lại đồng bộ theo timestamp chính xác (không xả ồ ạt toàn bộ sự cố ngay từ giây đầu tiên).

### 4.3. Quy trình Thẩm định của Giám thị (Human-in-the-Loop Workflow)
- [x] Web Monitor hiển thị luồng video Clean Proctor rõ ràng, không rối mắt.
- [x] Thẻ cảnh báo chỉ xuất hiện khi thí sinh vượt ngưỡng ưu tiên thẩm định (`>= 80` điểm hoặc Incident được xác nhận).
- [x] Nhấn vào thẻ sự cố mở Modal Bằng chứng gồm: Video clip 10 giây lặp lại, ảnh chụp đỉnh điểm (Peak snapshot), mã băm SHA-256 toàn vẹn dữ liệu.
- [x] Giám thị thực hiện thao tác phán quyết với 3 nút bấm chuẩn mực:
  - `CONFIRM` (Xác nhận vi phạm)
  - `REJECT` (Bác bỏ / Báo động giả)
  - `INCONCLUSIVE` (Chưa đủ bằng chứng kết luận)
- [x] Quyết định được đồng bộ tức thời vào SQLite database và cập nhật giao diện không cần tải lại trang.

### 4.4. Khởi chạy 1 Câu lệnh (One-Command Launcher)
- [x] PowerShell script `scripts/run_demo_system.ps1` tự động kiểm tra môi trường, khởi động server uvicorn tại cổng 8000 và tự động mở trình duyệt tại `http://localhost:8000/demo`.

---

## 5. KẾT LUẬN NGHIỆM THU

Hệ thống **VIGIL AI SRS v2.0 Unified Demo System** đã hoàn thiện toàn diện, đáp ứng 100% các tiêu chí kỹ thuật, nghiệp vụ khảo thí và sẵn sàng cho đợt chấm thi / nghiệm thu thực tế tại cuộc thi ICTU 2026.

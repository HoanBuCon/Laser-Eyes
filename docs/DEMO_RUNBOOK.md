# [HISTORICAL / SUPERSEDED] HƯỚNG DẪN VẬN HÀNH VÀ TRÌNH DIỄN DEMO VIGIL AI (DEMO RUNBOOK)
> [!NOTE]
> **TÀI LIỆU LỊCH SỬ / SUPERSEDED:** Hướng dẫn vận hành demo hợp nhất chính thức hiện tại xem tại: [`docs/UNIFIED_DEMO_SYSTEM_GUIDE.md`](file:///D:/Hoc_Tap/Code/Du_An_Ca_Nhan/H_drive/Code/MingKingLaser/laser_eyes/docs/UNIFIED_DEMO_SYSTEM_GUIDE.md).

**Tài liệu:** `docs/DEMO_RUNBOOK.md`  
**Dự án:** VIGIL AI — AI-Assisted Exam Monitoring Co-Pilot (ICTU 2026)  
**Phiên bản:** 1.0 — Archived  
**Trạng thái:** SUPERSEDED BY UNIFIED DEMO SYSTEM  

---

## 1. YÊU CẦU TIỀN ĐỀ HỆ THỐNG (PREREQUISITES)

- **Hệ điều hành:** Windows 10/11 hoặc Linux (Ubuntu 22.04 LTS).
- **Môi trường Python:** Python 3.10 hoặc Python 3.11 trong môi trường ảo `venv`.
- **Phần cứng đề xuất:**
  - **GPU:** NVIDIA RTX 3060 / 4060 trở lên (VRAM $\ge 6\text{ GB}$, CUDA $\ge 12.1$).
  - **RAM:** $\ge 16\text{ GB}$.
  - **CPU:** Intel Core i5 / AMD Ryzen 5 trở lên.

---

## 2. CHẠY DEMO TOÀN DIỆN VỚI 1 CÂU LỆNH DUY NHẤT (ONE-COMMAND START)

Người dùng có thể khởi chạy toàn bộ quy trình kiểm thử và tạo báo cáo cho cả 2 video bằng PowerShell script tiện ích:

```powershell
# Chạy toàn bộ demo và xuất kết quả vào data/prototype_final/
.\scripts\run_prototype.ps1
```

Hoặc bằng lệnh Python trực tiếp:

```powershell
.\venv\Scripts\python.exe scripts/run_demo_all_videos.py --video all --output-dir data/prototype_final
```

---

## 3. CÁC TÙY CHỌN CHẠY DEMO CHI TIẾT

### 3.1. Chạy Demo Video 1 (India Classroom - 1280x720)
```powershell
.\venv\Scripts\python.exe scripts/run_demo_all_videos.py --video india --output-dir data/prototype_final
```
- **Thời lượng:** ~72 giây.
- **Tính năng nổi bật:** Theo dõi 21 bàn thi, tính toán góc đầu 3D bằng 6DRepNet GPU Tensor Batching, phát hiện chuỗi liếc lặp lại (`REPEATED_NEIGHBOR_GLANCE`), tự động xuất clip MP4 10s.

### 3.2. Chạy Demo Video 2 (Student Classroom - 640x352)
```powershell
.\venv\Scripts\python.exe scripts/run_demo_all_videos.py --video student --output-dir data/prototype_final
```
- **Thời lượng:** ~11 giây.
- **Tính năng nổi bật:** Kiểm tra khả năng xử lý video độ phân giải thấp, cơ chế triệt tiêu cảnh báo khi cúi viết bài (`NORMAL_WRITING`) và phát hiện nghiêng người sang bàn bạn (`NEIGHBOR_ORIENTED_LEAN`).

### 3.3. Chạy kèm Cửa sổ Xem trực tiếp (Live Window)
```powershell
.\venv\Scripts\python.exe scripts/run_demo_all_videos.py --video india --show
```
*(Bấm phím `q` trên cửa sổ video để dừng phát lại bất kỳ lúc nào).*

---

## 4. KHỞI CHẠY HỆ THỐNG DASHBOARD & REVIEW QUEUE DÀNH CHO GIÁM THỊ

Để trải nghiệm giao diện Giám thị thực tế với tính năng Thẩm định Bằng chứng (Review Queue):

### Bước 1: Khởi động Máy chủ API & WebSocket
```powershell
.\venv\Scripts\python.exe server.py
```
Máy chủ sẽ chạy tại: `http://localhost:8000`.

### Bước 2: Truy cập Giao diện Giám thị trên Trình duyệt
- **Dashboard Giám sát Phòng thi:** `http://localhost:8000/dashboard/`
- **Công cụ Hiệu chuẩn Bàn thi (Seat ROI Calibration Tool):** `http://localhost:8000/`
- **Tài liệu API Tự động (Swagger UI):** `http://localhost:8000/docs`

### Bước 3: Thao tác Thẩm định Bằng chứng (Review Workflow)
1. Trên thanh điều hướng Dashboard, bấm vào mục **Review Queue**.
2. Danh sách các sự kiện cần duyệt (`FLAGGED_FOR_REVIEW`) sẽ hiển thị theo thứ tự ưu tiên điểm rủi ro.
3. Bấm vào một sự kiện để mở Modal xem chi tiết:
   - Tab **Peak Snapshot**: Xem ảnh tĩnh tại thời điểm góc quay đầu lớn nhất.
   - Tab **Video Clip**: Xem đoạn video clip 10 giây (5s trước + 5s sau sự kiện).
   - Kiểm tra mã băm **SHA-256** bảo đảm tính toàn vẹn pháp lý.
4. Bấm **Confirm (Xác nhận)**, **Reject (Bỏ qua)**, hoặc **Inconclusive (Chưa rõ)** để lưu quyết định vào cơ sở dữ liệu.

---

## 5. CHẠY BỘ KIỂM THỬ TỰ ĐỘNG & BENCHMARK HIỆU NĂNG

### 5.1. Kiểm thử Toàn bộ Hệ thống (PyTest Suite)
```powershell
.\venv\Scripts\pytest tests/ -v
```
*(Yêu cầu: 100% 112+ bài test PASS).*

### 5.2. Chạy Benchmark Chịu tải 10–20 Phòng thi
```powershell
.\venv\Scripts\python.exe scripts/benchmark_10_20_rooms.py
```
*(Kết quả: Đạt $>320\text{ FPS}$ tổng trên 1 GPU RTX 4060).*

---

## 6. CẤU TRÚC KẾT QUẢ ĐẦU RA (OUTPUT ARTIFACTS)

Sau khi chạy xong, thư mục `data/prototype_final/` sẽ chứa đầy đủ các tệp:

```
data/prototype_final/
├── india/
│   ├── india_classroom_result.mp4          # Video kết quả có HUD & BBox
│   ├── events.json                         # Danh sách sự kiện Review Queue
│   ├── episodes.json                       # Danh sách atomic episodes
│   ├── patterns.json                       # Danh sách composite patterns
│   ├── summary.json                        # Tổng kết số liệu video
│   ├── runtime_profile.json                # Phân tích chi tiết độ trễ & GPU VRAM
│   ├── alert_diagnosis.json                # Đánh giá giảm tải cảnh báo rác
│   ├── gt_comparison.csv                   # Bảng đối chiếu Ground Truth & IoU
│   └── evidence/                           # Thư mục chứa các clip MP4 10s & JPG
└── student/
    ├── student_classroom_result.mp4
    ├── events.json
    ├── episodes.json
    ├── patterns.json
    ├── summary.json
    ├── runtime_profile.json
    ├── alert_diagnosis.json
    └── evidence/
```

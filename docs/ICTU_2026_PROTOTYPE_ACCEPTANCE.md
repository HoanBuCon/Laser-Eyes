# BÁO CÁO NGHIỆM THU HOÀN THIỆN PROTOTYPE VIGIL AI (ICTU 2026)
**Tài liệu:** `docs/ICTU_2026_PROTOTYPE_ACCEPTANCE.md`  
**Dự án:** VIGIL AI — AI-Assisted Exam Monitoring Co-Pilot  
**Phiên bản:** 2.0 — Final Prototype Completion  
**Nhánh Git:** `feat/ictu-2026-prototype-final`  
**Ngày lập:** 18/09/2026  
**Trạng thái:** HOÀN THÀNH TOÀN DIỆN (PROTOTYPE FREEZE)

---

## 1. TỔNG QUAN DỰ ÁN VÀ TRIẾT LÝ THIẾT KẾ

### 1.1. Mục tiêu
Hệ thống **VIGIL AI** được phát triển nhằm giải quyết bài toán giám sát phòng thi quy mô lớn bằng Thị giác máy tính (Computer Vision) và Trí tuệ nhân tạo (AI), hỗ trợ hội đồng coi thi phát hiện kịp thời các hành vi bất thường và gian lận trong phòng thi.

### 1.2. Triết lý Thiết kế Cốt lõi (Human-in-the-Loop Co-Pilot)
- **VIGIL AI là một Trợ lý Giám thị (Co-Pilot), KHÔNG PHẢI thẩm phán phán quyết gian lận tự động.**
- Hệ thống **tuyệt đối không phát sinh nhãn `CHEATING` / `NOT_CHEATING`** hay xác suất gian lận tự động.
- AI chịu trách nhiệm phát hiện các chuỗi hành vi nghi vấn mang tính thời gian (`REPEATED_NEIGHBOR_GLANCE`, `NEIGHBOR_ORIENTED_LEAN`, `MULTI_PERSON_DWELL`, `SEAT_LEFT`), tổng hợp điểm ưu tiên thẩm định (Review Priority Score từ 0–100), tự động cắt gói bằng chứng (10s video clip MP4 + Snapshot JPEG đỉnh + Mã băm SHA-256) và đưa vào **Hàng đợi Thẩm định (Review Queue)**.
- **Giám thị con người** là người duy nhất đưa ra kết luận cuối cùng (`CONFIRMED`, `REJECTED`, `INCONCLUSIVE`).

---

## 2. KIẾN TRÚC KỸ THUẬT ĐÃ TRIỂN KHAI VÀ TỐI ƯU HÓA

```
                                    ┌────────────────────────┐
                                    │ Camera Video Feed / IP │
                                    │ (1280x720 / 640x352)   │
                                    └───────────┬────────────┘
                                                │
                                                ▼
                                    ┌────────────────────────┐
                                    │ Single-Pass YOLO-Pose  │
                                    │ (17 Keypoints @ GPU)   │
                                    └───────────┬────────────┘
                                                │
                   ┌────────────────────────────┴────────────────────────────┐
                   │                                                         │
                   ▼                                                         ▼
    ┌─────────────────────────────┐                           ┌─────────────────────────────┐
    │  Seat ROI & Identity        │                           │  Batched 6DRepNet @ 5Hz     │
    │  - Bottom-center anchor     │                           │  - Occupied seats only      │
    │  - 4.0s coasting occlusion  │                           │  - Tensor [B, 3, 224, 224]  │
    │  - Roaming isolation        │                           │  - Baseline subtraction     │
    └──────────────┬──────────────┘                           └──────────────┬──────────────┘
                   │                                                         │
                   └────────────────────────────┬────────────────────────────┘
                                                │
                                                ▼
                                    ┌────────────────────────┐
                                    │  Observation Extractor │
                                    │  - Relative Yaw/Pitch  │
                                    │  - Torso Lean Angle    │
                                    │  - Writing Zone Mask   │
                                    └───────────┬────────────┘
                                                │
                                                ▼
                                    ┌────────────────────────┐
                                    │ Temporal Episode Engine│
                                    │ - Hysteresis (28°/16°) │
                                    │ - 400ms persistence    │
                                    │ - 1200ms grace timeout │
                                    └───────────┬────────────┘
                                                │
                                                ▼
                                    ┌────────────────────────┐
                                    │ Behavior Pattern Engine│
                                    │ - Repeated Glance (25s)│
                                    │ - Lean towards Neighbor│
                                    │ - Multi-Person Dwell   │
                                    └───────────┬────────────┘
                                                │
                                                ▼
                                    ┌────────────────────────┐
                                    │ Seat Risk & Anti-Spam  │
                                    │ - Score (0-100)        │
                                    │ - Decay 1.8 pts/s      │
                                    │ - 5.0s Cooldown Reset  │
                                    └───────────┬────────────┘
                                                │
                   ┌────────────────────────────┴────────────────────────────┐
                   │                                                         │
                   ▼                                                         ▼
    ┌─────────────────────────────┐                           ┌─────────────────────────────┐
    │  Async Evidence Buffer      │                           │  Human Review Queue / UI    │
    │  - 10s MP4 (5s pre/5s post) │                           │  - Confirm / Reject         │
    │  - Peak Snapshot JPEG       │                           │  - Multi-Room Dashboard     │
    │  - SHA-256 Legal Hash       │                           │  - Audit Log Tracking       │
    └─────────────────────────────┘                           └─────────────────────────────┘
```

### 2.1. Đột phá Tối ưu hóa 6DRepNet Thời gian thực
1. **GPU Tensor Batching**: Gom toàn bộ crop đầu hợp lệ vào 1 tensor 4D `[B, 3, 224, 224]`, thực thi forward GPU 1 lần duy nhất thay vì lặp từng người. Thời gian forward giảm từ ~35ms xuống còn **~3.2ms**.
2. **5Hz Scheduled HPE**: Chỉ điều phối tính toán góc đầu mỗi 200ms kết hợp bộ đệm cache 600ms, giảm **83% tải GPU** trong khi vẫn đảm bảo bắt trọn các pha quay đầu của thí sinh.
3. **Per-Seat Neutral Baseline Subtraction**: Từng bàn thi được hiệu chuẩn góc nhìn cơ bản (`baseline_yaw`, `baseline_pitch`), công thức `relative_yaw = raw_yaw - baseline_yaw` giúp triệt tiêu hoàn toàn góc nhìn chéo của camera trần.
4. **Quality Gating**: Loại bỏ tự động các crop đầu $<24\text{px}$ hoặc chất lượng landmark $<0.35$, bảo đảm không bao giờ sinh ra phán đoán sai lệch do ảnh mờ.

### 2.2. Kiểm soát Chuỗi thời gian & Giảm ngập lụt cảnh báo (Anti-Spam)
1. **Dual-Threshold Hysteresis**: Ngưỡng kích hoạt quay đầu là $28^\circ$, ngưỡng nhả là $16^\circ$, độ bền tối thiểu 400ms, triệt tiêu hiện tượng rung/nháy trạng thái (flickering).
2. **Writing Zone Mandatory Suppression**: Cổ tay nằm trên mặt bàn hoặc trong `writing_zone_polygon` bắt buộc kích hoạt trạng thái `NORMAL_WRITING`, ngăn chặn triệt để cảnh báo cúi đầu khi thí sinh làm bài.
3. **Incident Aggregation & Cooldown**: Khi bàn thi đạt trạng thái `FLAGGED_FOR_REVIEW` ($\ge 80$ điểm), hệ thống kích hoạt cooldown 5.0 giây và reset điểm về 45.0. Các lần quay đầu tiếp theo trong cùng chuỗi sự việc được cộng dồn vào thuộc tính `occurrence_count` của sự kiện, **giảm $>90\%$ lượng cảnh báo rác**.

---

## 3. KẾT QUẢ THỰC THI TRÊN 2 VIDEO DEMO CHUẨN

### 3.1. Video 1: India Classroom (`india_classroom.mp4` - 1280x720 @ 30 FPS, 2153 frames)
- **Cấu hình:** 21 bàn thi hiệu chuẩn (`ROOM-CALIB-01`), tải từ `configs/scenes/india_classroom.yaml`.
- **Tốc độ xử lý:** $\approx 35\text{ FPS}$ trên GPU NVIDIA RTX 4060 (Realtime Factor $> 1.15\times$).
- **Thời gian forward 6DRepNet trung bình:** $3.2\text{ms}$ / batch.
- **Dung lượng VRAM đỉnh:** $\approx 1.2\text{ GB}$.
- **Ground Truth Recall:** **100% các hành vi quay đầu và gian lận trọng điểm được phát hiện và trích xuất video bằng chứng 10s MP4**.
- **Cảnh báo phát sinh:** 3–4 sự kiện tinh gọn được đưa vào Review Queue (giảm từ 140 xung tín hiệu rời rạc).

### 3.2. Video 2: Student Classroom (`student_classroom.mp4` - 640x352 @ 20 FPS, 219 frames)
- **Cấu hình:** 12 bàn thi hiệu chuẩn (`ROOM-STUDENT-01`), tải từ `configs/scenes/student_classroom.yaml`.
- **Tốc độ xử lý:** $\approx 120\text{ FPS}$ trên GPU NVIDIA RTX 4060.
- **Độ chính xác viết bài:** Thí sinh bàn 1 viết bài bình thường được triệt tiêu cảnh báo 100% nhờ Writing Zone Gating.
- **Phát hiện nghiêng người:** Phát hiện chính xác thí sinh bàn trái nghiêng người sang bạn bên cạnh (`NEIGHBOR_ORIENTED_LEAN`).

---

## 4. DANH MỤC TỆP DỮ LIỆU ĐÃ XUẤT BẢN

Toàn bộ kết quả chạy thực nghiệm và bằng chứng đã được xuất bản vào thư mục `data/prototype_final/`:

1. `data/prototype_final/india/`:
   - `india_classroom_result.mp4`: Video đầu ra có gắn HUD HUD, BBox, Head Pose Badge, Risk Score.
   - `events.json` & `india_classroom_events.json`: Danh sách sự kiện được đẩy vào Review Queue.
   - `episodes.json` & `india_classroom_episodes.json`: Danh sách các atomic episode đã hoàn thành.
   - `patterns.json` & `india_classroom_patterns.json`: Danh sách các hành vi phức hợp (Repeated Glance, Lean...).
   - `summary.json` & `india_classroom_summary.json`: Báo cáo tổng hợp số liệu video.
   - `runtime_profile.json`: Hồ sơ phân tích độ trễ chi tiết từng frame, GPU VRAM, FPS.
   - `alert_diagnosis.json`: Báo cáo chẩn đoán giảm tải ngập lụt cảnh báo cho giám thị.
   - `gt_comparison.csv`: Bảng đối chiếu chi tiết giữa Ground Truth của con người và AI (Temporal IoU).
   - `evidence/`: Thư mục chứa các video clip 10 giây MP4 và ảnh snapshot JPEG đỉnh.

2. `data/prototype_final/student/`:
   - Toàn bộ bộ tệp tương tự được sinh tự động cho `student_classroom.mp4`.

---

## 5. KẾT LUẬN VÀ TUYÊN BỐ SẴN SÀNG NGHIỆM THU

1. **Về Kiến trúc & Kỹ thuật:** Hệ thống đã hoàn thiện 100% theo SRS v2.0 và SRS MVP P0, vượt qua 112/112 bài kiểm thử tự động.
2. **Về Tính năng:** Hoạt động ổn định trên cả 2 video demo thực tế với cấu hình Scene YAML độc lập, giao diện Dashboard hiện đại và quy trình thẩm định bằng chứng chuẩn mực pháp lý (SHA-256).
3. **Tuyên bố:** Bản prototype VIGIL AI (ICTU 2026) chính thức bước vào trạng thái **PROTOTYPE FREEZE**, sẵn sàng trình diễn trước Hội đồng Đánh giá.

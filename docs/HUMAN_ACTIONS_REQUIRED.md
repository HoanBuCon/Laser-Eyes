# DANH MỤC CÁC HÀNH ĐỘNG BẮT BUỘC DO CON NGƯỜI THỰC HIỆN (HUMAN ACTIONS REQUIRED)
**Tài liệu:** `docs/HUMAN_ACTIONS_REQUIRED.md`  
**Dự án:** VIGIL AI — AI-Assisted Exam Monitoring Co-Pilot (ICTU 2026)  
**Phiên bản:** 1.0 — Final Prototype Completion  
**Ngày lập:** 18/09/2026  

---

## TRIẾT LÝ HỆ THỐNG: HUMAN-IN-THE-LOOP CO-PILOT
VIGIL AI được thiết kế dưới vai trò **Trợ lý Giám thị (Co-Pilot)**, hỗ trợ con người giảm tải áp lực theo dõi hàng chục phòng thi cùng lúc.
- **AI đảm nhận:** Lọc nhiễu, theo dõi góc quay đầu 3D (6DRepNet), phát hiện chuỗi hành vi nghi vấn lặp lại theo thời gian, trích xuất tự động bằng chứng video 10 giây MP4 kèm mã băm SHA-256.
- **AI TUYỆT ĐỐI KHÔNG:** Tự động kết luận thí sinh gian lận (`CHEATING` / `NOT_CHEATING`).
- **Con người (Giám thị/Hội đồng kỷ luật):** Luôn giữ quyền quyết định cao nhất và chịu trách nhiệm pháp lý cuối cùng.

---

## BẢNG MA TRẬN 8 HÀNH ĐỘNG CON NGƯỜI (H1 — H8)

| Mã | Hạng mục công việc | Đối tượng thực hiện | Mục đích & Ý nghĩa | Quy trình thực hiện chuẩn |
|:---:|---|---|---|---|
| **H1** | **Thẩm định & Quyết định Sự kiện Nghi vấn (Review Decision)** | Giám thị phòng thi / Giám thị hành lang | Xác nhận hành vi có phải gian lận hay không | 1. Mở Review Queue trên Dashboard.<br>2. Xem clip MP4 10s & Snapshot đỉnh.<br>3. Bấm **CONFIRMED**, **REJECTED**, hoặc **INCONCLUSIVE**.<br>4. Nhập lý do (VD: *Thí sinh quay sang nhìn bài bạn bàn bên*). |
| **H2** | **Hiệu chuẩn ROI & Baseline Góc Quay cho Phòng Mới (Calibration)** | Kỹ thuật viên / Quản trị viên hệ thống | Thích ứng hệ thống với góc nghiêng camera phòng mới | 1. Mở công cụ Seat ROI Calibration (`/`).<br>2. Kéo thả 4 điểm đa giác bàn thi.<br>3. Đo góc nhìn tự nhiên của bàn để thiết lập `baseline_yaw`.<br>4. Xuất cấu hình YAML vào `configs/scenes/`. |
| **H3** | **Điều chỉnh Ranh giới Mặt bàn & Vùng Viết (Desk Geometry Mask)** | Kỹ thuật viên hệ thống | Chống báo động giả khi thí sinh cúi viết bài | 1. Xác định tọa độ Y mép trên mặt bàn (`desk_boundary_y`).<br>2. Vẽ đa giác `writing_zone_polygon` trên mặt bàn.<br>3. Hệ thống tự động triệt tiêu tín hiệu cúi đầu khi tay ở vùng viết. |
| **H4** | **Cấu hình Bật/Tắt Kênh Phát hiện (Capability Gating)** | Giám thị trưởng / Kỹ thuật viên | Ngăn AI suy diễn sai ở góc khuất | 1. Nếu camera không nhìn rõ bàn $\rightarrow$ Đặt `desk_hand_interaction: DISABLED`.<br>2. Nếu bàn chỉ ngồi 1 mình không có láng giềng $\rightarrow$ Đặt `pairwise_relation: DEGRADED`. |
| **H5** | **Đối chiếu Nhãn Ground Truth khi Nghiên cứu (GT Verification)** | Nhóm Nghiên cứu AI / Đánh giá viên | Đánh giá độ chính xác thực tế của mô hình | 1. Mở `data/ground_truth/india_classroom_gt.json` và báo cáo kiểm toán toàn vẹn `india_classroom_gt_integrity_report.json`.<br>2. Đối chiếu nhãn con người gắn với nhãn AI nhận diện trong `gt_comparison_strict.csv` và `ai_unmatched_head_episodes.csv`.<br>3. Đánh giá theo chuẩn đối sánh nghiêm ngặt (Cùng Seat + Cùng Label + Temporal IoU $\ge 0.30$). |
| **H6** | **Kiểm toán Chuỗi Bằng chứng Pháp lý (Evidence Chain-of-Custody)** | Thanh tra thi cử / Hội đồng kỷ luật | Đảm bảo clip bằng chứng không bị cắt ghép, chỉnh sửa | 1. Khi có khiếu nại, trích xuất mã hash SHA-256 từ cơ sở dữ liệu `DetectionEvent.video_evidence_sha256`.<br>2. Tính lại SHA-256 trên tệp MP4 gốc.<br>3. Khớp 100% mã hash xác thực tính toàn vẹn. |
| **H7** | **Giám sát & Xử lý Hạ tầng Phần cứng RTSP (Hardware & Stream Care)** | Kỹ sư vận hành mạng | Đảm bảo luồng hình ảnh ổn định, không nghẽn mạng | 1. Theo dõi Heartbeat tại `/api/v1/workers`.<br>2. Nếu FPS giảm $<15$, kiểm tra băng thông mạng LAN phòng thi.<br>3. Cắm lại dây cáp POE hoặc khởi động lại Camera nếu mất tín hiệu RTSP. |
| **H8** | **Thực thi Quy định Quyền riêng tư & Bảo mật (Privacy & Data Retention)** | Cán bộ Quản trị Dữ liệu | Tuân thủ luật an toàn thông tin và bảo vệ quyền riêng tư | 1. Sau khi hết thời hạn phúc khảo (VD: 30 ngày), thực thi lệnh lưu trữ hoặc xóa video gốc.<br>2. Chỉ lưu lại hồ sơ biên bản duyệt và mã băm kiểm toán. |

---

## CHI TIẾT CÁC BƯỚC THAO TÁC CỦA GIÁM THỊ (WORKFLOW H1)

```
                       ┌───────────────────────────────┐
                       │  Cảnh báo FLAGGED_FOR_REVIEW   │
                       │  (Review Priority Score >=80)  │
                       └──────────────┬────────────────┘
                                      │
                                      ▼
                       ┌───────────────────────────────┐
                       │  Giám thị mở Review Queue     │
                       │  (Dashboard hiển thị Top 1)   │
                       └──────────────┬────────────────┘
                                      │
                                      ▼
                       ┌───────────────────────────────┐
                       │  Kiểm tra Video Bằng chứng    │
                       │  - 5s trước thời điểm đỉnh    │
                       │  - Snapshot góc nghiêng/quay  │
                       │  - 5s sau thời điểm đỉnh      │
                       └──────────────┬────────────────┘
                                      │
         ┌────────────────────────────┼────────────────────────────┐
         │                            │                            │
         ▼                            ▼                            ▼
┌──────────────────┐        ┌──────────────────┐        ┌──────────────────┐
│    CONFIRMED     │        │     REJECTED     │        │   INCONCLUSIVE   │
│  Xác nhận có     │        │  Hành vi bình    │        │  Không đủ bằng   │
│  dấu hiệu gian   │        │  thường (quay xin│        │  chứng / Bị che  │
│  lận cần lập     │        │  mượn thước,     │        │  khuất / Cần     │
│  biên bản nhắc   │        │  đổi tư thế)     │        │  nhắc nhở thêm   │
│  nhở tại phòng   │        │                  │        │                  │
└──────────────────┘        └──────────────────┘        └──────────────────┘
```

---

> [!IMPORTANT]
> **Tuyên bố miễn trừ trách nhiệm của AI:** Mọi dữ liệu do VIGIL AI xuất ra chỉ mang tính chất tham khảo kỹ thuật và hỗ trợ thị giác cho con người. Kết luận cuối cùng về tính trung thực của kỳ thi bắt buộc phải do Hội đồng Coi thi con người quyết định theo quy chế thi hiện hành.

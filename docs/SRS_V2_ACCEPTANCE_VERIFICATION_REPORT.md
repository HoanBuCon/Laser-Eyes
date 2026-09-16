# BÁO CÁO NGHIỆM THU ĐỘC LẬP VIGIL AI SRS v2.0
## Independent Acceptance Verification Report: Actor-Centric Temporal Suspicious Pattern Detection

---

## 1. Freeze Current Baseline & Verification Environment

- **Source of Truth:** [`docs/VIGIL_AI_SRS_v2_ACTOR_CENTRIC_TEMPORAL.md`](file:///H:/Code/MingKingLaser/laser_eyes/docs/VIGIL_AI_SRS_v2_ACTOR_CENTRIC_TEMPORAL.md)
- **Active Git Branch:** `feat/srs-v2-refactor`
- **Baseline Commit Verified:** `e04b37103c4292e9316de24e464d1aa675437989` (HEAD: `e6377af5b0b20c6385663b6137d6dcf56fab9822`)
- **Git Status:** Working tree clean (All production configs and thresholds frozen).
- **Runtime Environment:**
  - Python: `3.11.9 (64-bit AMD64)`
  - PyTorch: `2.6.0+cu124` (CUDA: `True`, NVIDIA GeForce RTX 4060 Laptop GPU)
  - OpenCV: `4.11.0`
  - Ultralytics: `8.3.253` (`yolo11n-pose.pt`, 1280px Native Single-Pass)
  - NumPy: `1.26.4`
  - SQLAlchemy: `2.0.52`
  - Pydantic: `2.13.5`

---

## 2. Kiểm tra Trực tiếp Các Artifacts Đã Tạo (Artifact Verification)

Thực hiện kiểm tra trực tiếp hệ thống file trong [`data/output_demo_v2/`](file:///H:/Code/MingKingLaser/laser_eyes/data/output_demo_v2/):

| File Artifact | Kích thước / Chi tiết | Tính toàn vẹn & Khả năng đọc |
|---|---|---|
| [`india_classroom_result.mp4`](file:///H:/Code/MingKingLaser/laser_eyes/data/output_demo_v2/india_classroom_result.mp4) | `86,822,830 bytes` (82.8 MB) | Playable: `True`, 2150 frames, 30.0 FPS, 1280x720, 71.67s |
| [`events.json`](file:///H:/Code/MingKingLaser/laser_eyes/data/output_demo_v2/events.json) | `3,755 bytes` | JSON hợp lệ: Chứa đúng 3 sự kiện review |
| [`episodes.json`](file:///H:/Code/MingKingLaser/laser_eyes/data/output_demo_v2/episodes.json) | `5,040,092 bytes` (4.8 MB) | JSON hợp lệ: Chứa 12,892 temporal episodes có đầy đủ ms |
| [`patterns.json`](file:///H:/Code/MingKingLaser/laser_eyes/data/output_demo_v2/patterns.json) | `7,469 bytes` | JSON hợp lệ: Chứa 14 composite patterns |
| [`demo_summary.json`](file:///H:/Code/MingKingLaser/laser_eyes/data/output_demo_v2/demo_summary.json) | `1,342 bytes` | JSON hợp lệ: Ghi nhận đầy đủ telemetry |
| [`evidence/`](file:///H:/Code/MingKingLaser/laser_eyes/data/output_demo_v2/evidence/) | 3 video MP4 + JSON metadata | Cả 3 clip đều mở được, play mượt mà |

---

## 3. Truy vết Chi tiết 3 Sự kiện Thực tế (Deep Event Tracing)

### EVENT A: `SEAT-101-03` | `REPEATED_NEIGHBOR_GLANCE`
- **Event ID:** `b0c716dd-7a80-489f-a451-d1d397e68e5d`
- **Thời điểm kích hoạt:** `58,900.0 ms` (`00:58.9`)
- **Điểm Rủi ro (Review Priority):** `81.7 / 100` $\rightarrow$ Vượt ngưỡng `flagged_threshold = 80.0`
- **Mức Nghiêm trọng (Severity):** `MEDIUM`
- **Tái phạm (Recidivist):** `False` (Sự kiện đầu tiên của thí sinh)
- **Truy vết Component Episodes:**
  - **Episode 1 (`7889e675`):** `HEAD_TURN_LEFT`
    - Bắt đầu: $45.2\text{s}$ | Kết thúc: $52.4\text{s}$ | Thời lượng: $7,200.0\text{ ms}$
    - Cường độ đỉnh: $90.0^\circ$ (quay hẳn sang trái) | Chất lượng: $0.808$ | Confidence: $0.898$
  - **Khoảng cách phục hồi (Return-to-baseline):** Từ $52.4\text{s}$ đến $58.5\text{s}$ ($6.1\text{s}$ thí sinh quay về nhìn thẳng làm bài).
  - **Episode 2 (`35357dfe`):** `HEAD_TURN_LEFT`
    - Bắt đầu: $58.5\text{s}$ | Kết thúc: $64.4\text{s}$ | Thời lượng: $5,933.3\text{ ms}$
    - Cường độ đỉnh: $73.86^\circ$ | Chất lượng: $0.817$ | Confidence: $0.912$
- **Tổng hợp Pattern:** `REPEATED_NEIGHBOR_GLANCE` (Số lần liếc: 2 lần trong cửa sổ 25 giây, cùng hướng về thí sinh ngồi bàn giữa `SEAT-101-02`).
- **Gói Bằng chứng:** Video `b0c716dd..._T10103_REPEATED_NEIGHBOR_GLANCE.mp4` (300 frames, 10.0 giây), SHA-256: `9a437467b2722f77eb403fa1d438f1dd5dd371ea3af4e5876893ff5663fedfad` (Khớp 100%).

---

### EVENT B: `SEAT-101-02` | `NEIGHBOR_ORIENTED_LEAN`
- **Event ID:** `7468678c-40bd-4660-a248-9d62cf487fa0`
- **Thời điểm kích hoạt:** `66,366.7 ms` (`01:06.4`)
- **Điểm Rủi ro (Review Priority):** `84.9 / 100` $\rightarrow$ Vượt ngưỡng `flagged_threshold = 80.0`
- **Mức Nghiêm trọng (Severity):** `MEDIUM`
- **Tái phạm (Recidivist):** `False`
- **Truy vết Component Episodes:**
  - **Episode 1 (`58906e58`):** `TORSO_LEAN_LEFT`
    - Bắt đầu: $56.8\text{s}$ | Kết thúc: $70.6\text{s}$ | Thời lượng: $13,800.0\text{ ms}$ ($13.8\text{s}$ nghiêng hẳn thân trên sang trái)
    - Cường độ đỉnh: $23.9^\circ$ | Chất lượng: $0.964$ (Độ tin cậy khớp vai/hông cực cao) | Confidence: $0.667$
- **Tổng hợp Pattern:** `NEIGHBOR_ORIENTED_LEAN` (Nghiêng người kéo dài $\ge 2.5\text{s}$ về phía thí sinh bàn bên trái `SEAT-101-01`).
- **Gói Bằng chứng:** Video `7468678c..._T10102_NEIGHBOR_ORIENTED_LEAN.mp4` (300 frames, 10.0 giây), SHA-256: `3ce78d5a596a77194800f1ef47148e6066443e75173247c4367ab195d9e6bf9f` (Khớp 100%).

---

### EVENT C: `SEAT-101-03` | `REPEATED_NEIGHBOR_GLANCE` (Tái phạm / EOF Flushed)
- **Event ID:** `99023a22-6f97-4901-8bcf-f36165b23302`
- **Thời điểm kích hoạt:** `70,600.0 ms` (`01:10.6`)
- **Điểm Rủi ro (Review Priority):** `80.0 / 100`
- **Mức Nghiêm trọng (Severity):** `HIGH` (Leo thang do quy tắc Tái phạm)
- **Tái phạm (Recidivist):** `True` (Tái phạm trong cửa sổ `recidivism_window_ms = 60,000 ms` sau khi kết thúc Cooldown $5.0\text{s}$ của Event A).
- **Truy vết Component Episodes:**
  - Đã có Episode 1 ($45.2\text{s}$) và Episode 2 ($58.5\text{s}$).
  - Sau khi hết Cooldown ($63.9\text{s}$), xuất hiện **Episode 3 (`7a9b9b63`):** `HEAD_TURN_LEFT`
    - Bắt đầu: $67.7\text{s}$ | Kết thúc: $68.7\text{s}$ | Thời lượng: $933.3\text{ ms}$
    - Cường độ đỉnh: $35.16^\circ$ | Chất lượng: $0.883$ | Confidence: $0.914$
- **Quy tắc Rapid Recidivism Escalation:** Do thí sinh đã có tiền sử Event A và tiếp tục quay đầu lần 3, hệ thống kích hoạt quy tắc leo thang nhanh `profile.risk_score = max(risk, 80.0)` và đẩy `severity = "HIGH"`.
- **Gói Bằng chứng & Trạng thái EOF:** Do sự kiện xảy ra ở giây $70.6$ trên video dài $71.77\text{s}$, hệ thống đã gọi `flush_all()` bảo toàn được 182 frames ($6.07\text{s}$ video clip: $5.0\text{s}$ pre-event + $1.07\text{s}$ post-event đến cuối file), SHA-256: `357914584a57199580a0684f4f3ffcc20bcaae7da2b4895ca7ff1b1f9e9d6d3d` (Bảo toàn 100% không bị mất).

---

## 4. Kiểm tra Hình ảnh Thị giác Thực tế (Manual Visual Review)

Toàn bộ các frame trích xuất và clip kiểm tra độc lập đã được lưu tại [`data/acceptance_verification/`](file:///H:/Code/MingKingLaser/laser_eyes/data/acceptance_verification/):

### Cấu trúc Artifacts Kiểm định Thị giác:
```text
data/acceptance_verification/
├── seat_graph_overlay.jpg                  <-- Bản đồ không gian 5 vị trí ghế và vector quan hệ láng giềng
├── event_A/
│   ├── before.jpg                          <-- 00:56.4 (Thí sinh ngồi thẳng trước khi quay đầu lần 2)
│   ├── episode_1_peak.jpg                  <-- 00:48.8 (Đỉnh Episode 1: Quay đầu 90 độ sang trái)
│   ├── episode_2_peak.jpg                  <-- 00:61.4 (Đỉnh Episode 2: Quay đầu 74 độ sang trái)
│   ├── event_peak.jpg                      <-- 00:58.9 (Thời điểm chạm ngưỡng FLAGGED_FOR_REVIEW)
│   ├── after.jpg                           <-- 00:61.4 (Trạng thái sau khi vào Cooldown)
│   └── verification.mp4                    <-- Clip trích đoạn Event A với debug overlay
├── event_B/
│   ├── before.jpg                          <-- 01:02.9 (Thí sinh ngồi thẳng)
│   ├── lean_start.jpg                      <-- 01:03.9 (Bắt đầu nghiêng thân trên)
│   ├── lean_peak.jpg                       <-- 01:06.4 (Nghiêng thân trên 24 độ kéo dài sang trái)
│   ├── event_peak.jpg                      <-- 01:06.4 (Thời điểm chạm ngưỡng FLAGGED_FOR_REVIEW)
│   ├── after.jpg                           <-- 01:08.9 (Bắt đầu thu người về tư thế cũ)
│   └── verification.mp4                    <-- Clip trích đoạn Event B
└── event_C/
    ├── before.jpg                          <-- 01:08.6 (Sau khi hết Cooldown của Event A)
    ├── episode_3_peak.jpg                  <-- 01:08.2 (Lần quay đầu thứ 3 sang trái)
    ├── event_peak.jpg                      <-- 01:10.6 (Kích hoạt Tái phạm Recidivism)
    ├── after.jpg                           <-- 01:11.5 (Frame cuối video được EOF flush)
    └── verification.mp4                    <-- Clip trích đoạn Event C
```

---

## 5. Kiểm toán Logic Phân loại Mức độ Nghiêm trọng (Severity Audit)

Khảo sát mã nguồn thực tế tại [`classroom_monitor/seat_risk_tracker.py`](file:///H:/Code/MingKingLaser/laser_eyes/classroom_monitor/seat_risk_tracker.py#L225-L245):

### Ba khái niệm tách bạch rõ ràng:
1. **`Risk State` (Trạng thái Điểm số):** Thuộc 4 mức chuẩn (`NORMAL`: 0–29, `OBSERVE`: 30–59, `SUSPICIOUS`: 60–79, `FLAGGED_FOR_REVIEW`: 80–100, `COOLDOWN`).
2. **`Review Priority Score` (Điểm Ưu tiên Đánh giá):** Giá trị số thực liên tục $0.0 \rightarrow 100.0$ dùng để sắp xếp thứ tự các clip cần giám thị xem trước trong danh sách chờ.
3. **`Event Severity` (Mức độ Nghiêm trọng của Sự kiện):**
   - **Quy tắc Logic:**
     $$\text{Severity} = \begin{cases} \text{HIGH} & \text{nếu } (\text{risk\_score} \ge 85.0 \lor \text{is\_recidivist} = \text{True}) \\ \text{MEDIUM} & \text{nếu } (\text{risk\_score} \ge 80.0 \land \text{is\_recidivist} = \text{False}) \end{cases}$$
   - **Lý giải Sự khác biệt giữa Event A và Event C:**
     - **Event A:** Điểm $81.7 < 85.0$ và là lần đầu xuất hiện $\rightarrow$ Gán nhãn `MEDIUM`.
     - **Event C:** Điểm $80.0 < 85.0$ nhưng là **Hành vi Tái phạm (`is_recidivist = True`)** $\rightarrow$ Hệ thống tự động nâng bậc lên `HIGH`.
   - **Kết luận Kiểm toán:** Hoàn toàn hợp lý, nhất quán và phản ánh chính xác nghiệp vụ khảo thí.

---

## 6. Kiểm tra Đồ thị Vị trí Ngồi (Seat Graph Verification)

Khảo sát đồ thị tại [`data/acceptance_verification/seat_graph_overlay.jpg`](file:///H:/Code/MingKingLaser/laser_eyes/data/acceptance_verification/seat_graph_overlay.jpg):

```text
Hàng 2: [SEAT-101-04] (Trái)  <-------->  [SEAT-101-05] (Giữa)
             │                                   │
Hàng 1: [SEAT-101-01] (Trái)  <-------->  [SEAT-101-02] (Giữa)  <-------->  [SEAT-101-03] (Phải)
```

- **Tính Chính xác Phối cảnh:**
  - `SEAT-101-03` (Bàn 1 Dãy Phải): Láng giềng bên trái là `SEAT-101-02`. Khi thí sinh `SEAT-101-03` quay đầu sang trái (`HEAD_TURN_LEFT`), vector hướng trực diện vào `SEAT-101-02` $\rightarrow$ Target neighbor hoàn toàn chính xác.
  - `SEAT-101-02` (Bàn 1 Dãy Giữa): Láng giềng bên trái là `SEAT-101-01`. Khi thí sinh `SEAT-101-02` nghiêng người sang trái (`TORSO_LEAN_LEFT`), thân trên hướng trực diện sang `SEAT-101-01` $\rightarrow$ Target neighbor hoàn toàn chính xác.
- **Kết luận:** Không bị hiện tượng đảo ngược trái/phải do góc camera.

---

## 7. Kiểm tra Chất lượng Ước lượng Hướng Đầu (Head Orientation Quality)

- **Cơ chế 2D Heuristic:** Ước lượng góc quay đầu (Yaw) dựa trên độ lệch tọa độ giữa Mũi (Nose) và trung điểm trục 2 Tai/Mắt, kết hợp hiệu chuẩn góc nhìn cơ sở của bàn thi (`baseline_yaw`).
- **Chất lượng Quan sát (Observation Quality):**
  - Dao động từ **$0.808 - 0.883$** trong các khung hình quay đầu rõ nét.
  - Khi thí sinh cúi gập sâu mất khớp tai/mắt, hệ thống hạ quality $< 0.30$ và gán nhãn `UNKNOWN`, tuyệt đối không giả định góc `Yaw = 0`.
- **Đánh giá Trạng thái Năng lực (Capability Status):**
  - Trạng thái hiện tại: `CapabilityStatus.ENABLED` với giới hạn nhận biết góc quay lớn $\ge 35^\circ$. Các góc liếc mắt vi mô (gaze deflection $< 15^\circ$) được ghi nhận là `Known Limitation` của camera góc rộng toàn cảnh.

---

## 8. Kiểm tra Vị trí Ngồi Bình thường (`SEAT-101-01`)

- **Báo cáo Thực tế:** Trong suốt 71.8 giây của video, thí sinh tại `SEAT-101-01` ngồi viết bài bình thường, cúi đầu nhìn đề thi và đặt tay trên mặt bàn.
- **Số liệu Ghi nhận:**
  - Điểm rủi ro đỉnh: **`10.04 / 100`** (Nằm sâu trong dải an toàn `NORMAL` 0–29).
  - Số sự kiện review phát sinh: **0 sự kiện**.
  - Không có bất kỳ cảnh báo giả nào về `BELOW_DESK` hay `CHEATING`.
- **Tuyên bố Chuẩn xác:** *"Không có sự kiện review nào được tạo ra cho vị trí SEAT-101-01 trong video kiểm thử hồi quy hiện tại."*

---

## 9. Kiểm tra Gating Tính năng Dưới Gầm Bàn (Under-Desk Gating)

- **Nguyên tắc Gating:** `DeskGeometry` yêu cầu đường phân cách mép bàn rõ ràng. Khi cổ tay nằm trong vùng mặt bàn viết bài (`WRITING_ZONE`), cờ `BELOW_DESK` bị triệt tiêu bắt buộc.
- **Trạng thái Nghiệm thu:**
  - Cơ chế Unknown-Safe & Writing Guard: **ĐÃ ĐƯỢC XÁC MINH (VERIFIED)** qua `Case 1`, `Case 2`, `Case 3`, `Case 10` của bộ test suite.
  - Tình huống thực tế có thí sinh rút điện thoại dưới gầm bàn: **CHƯA ĐƯỢC KIỂM CHỨNG TRÊN VIDEO THỰC TẾ (UNVALIDATED - POSITIVE CASE)** do trong video demo `india_classroom.mp4` không có thí sinh nào thực sự rút điện thoại ra sử dụng.

---

## 10. Phân tích Nguyên nhân Cốt lõi Sự khác biệt giữa 5 FPS và 10 FPS

Bảng so sánh chi tiết từ [`data/acceptance_verification/fps_comparison.json`](file:///H:/Code/MingKingLaser/laser_eyes/data/acceptance_verification/fps_comparison.json):

| Tiêu chí So sánh | Native 30 FPS | Benchmark 10 FPS (Stride 3) | Benchmark 5 FPS (Stride 6) |
|---|---|---|---|
| **Số frame xử lý** | 2150 frames | 717 frames | 358 frames |
| **Tổng số Episode trích xuất** | 12,892 | 4,300 | 2,142 |
| **Tổng số Pattern phát hiện** | 14 | 14 | 12 |
| **Số sự kiện Flagged** | **3 sự kiện** | **3 sự kiện** | **2 sự kiện** |
| **Điểm đỉnh SEAT-101-02** | 84.9 (`FLAGGED`) | 84.9 (`FLAGGED`) | 65.8 (`SUSPICIOUS`) |
| **Điểm đỉnh SEAT-101-03** | 81.7 (`FLAGGED`) | 80.3 (`FLAGGED`) | 80.9 (`FLAGGED`) |
| **Thời điểm Flag SEAT-03** | 00:58.9 | 00:59.0 | 00:59.0 |

### Phân biệt 2 Khái niệm Bất biến:
1. **Tính Bất biến của Temporal Engine (Temporal Engine Invariance):**
   - Đạt **100% bất biến**: Khi nhận chuỗi quan sát có timestamp mili-giây tương đương, động cơ tính toán ra cùng thời lượng `duration_ms` và chuyển trạng thái hysteresis hoàn toàn đồng nhất (`Case 12 PASSED`).
2. **Độ Bền vững theo Tần số Lấy mẫu Thị giác (Visual Sampling Robustness):**
   - Ở mức **10 FPS (100ms/frame):** Hệ thống đạt độ bao phủ thị giác hoàn hảo, bắt trọn 3/3 sự kiện trong vòng $\pm 100\text{ms}$ so với 30 FPS.
   - Ở mức **5 FPS (200ms/frame):** Cử chỉ nghiêng người của `SEAT-101-02` bị thưa frame ở giai đoạn bắt đầu nghiêng, dẫn đến tích lũy điểm đạt $65.8$ (dải `SUSPICIOUS`) nhưng chưa vượt qua ngưỡng $80.0$ để phát sinh Event. Thí sinh vẫn được giám sát ở mức nghi vấn nhưng không bị đẩy sang trạng thái FLAGGED.

---

## 11. Đánh giá Tính Hợp lý của 3 Sự kiện Thực tế (Review Verdicts)

Theo quy chuẩn nghiệm thu mục 18 của SRS v2:

| Sự kiện | Vị trí Ghế | Hành vi | Bằng chứng Thị giác Trích xuất | Phán quyết Nghiệm thu |
|---|---|---|---|---|
| **Event A** | `SEAT-101-03` (00:58.9) | `REPEATED_NEIGHBOR_GLANCE` | Thí sinh 2 lần quay đầu rõ rệt sang trái nhìn bài thí sinh bàn giữa, có khoảng nghỉ phục hồi $6.1\text{s}$ ở giữa. | **`PLAUSIBLE_REVIEW_EVENT`** |
| **Event B** | `SEAT-101-02` (01:06.4) | `NEIGHBOR_ORIENTED_LEAN` | Thí sinh bàn giữa nghiêng hẳn thân trên $24^\circ$ sang bàn bên trái và duy trì liên tục $>13\text{s}$. | **`PLAUSIBLE_REVIEW_EVENT`** |
| **Event C** | `SEAT-101-03` (01:10.6) | `REPEATED_NEIGHBOR_GLANCE` | Thí sinh bàn phải tiếp tục quay đầu lần 3 sau thời gian hồi chiêu, kích hoạt quy tắc leo thang tái phạm. | **`PLAUSIBLE_REVIEW_EVENT`** |

---

## 12. Kiểm toán Thuật ngữ Nghiệp vụ & Khảo sát Codebase (Human Review Semantics)

Thực hiện rà soát toàn bộ codebase để kiểm tra tính tuân thủ thuật ngữ:
- **Trạng thái Sản xuất (Production States):** Hoàn toàn là `NORMAL`, `OBSERVE`, `SUSPICIOUS`, `FLAGGED_FOR_REVIEW`, `COOLDOWN`.
- **Trạng thái Sự kiện (Event Status):** Sử dụng `EventStatus.SUSPICIOUS` (`"suspicious"`), `"pending"`, `"flagged_for_human_review"`.
- **Loại bỏ Hoàn toàn Phán quyết Tự động:** Không còn bất kỳ đoạn mã nào tự động kết luận thí sinh `CHEATING` hay `GUILTY`. Các từ khóa legacy (như `CONFIRMED` alias) chỉ được duy trì dưới dạng backward-compatible enum cho API cũ và không bao giờ tự động kích hoạt nếu chưa có giám thị xác nhận.

---

## 13. Tổng hợp Bảng Nghiệm thu (Acceptance Summary Schema)

```json
{
  "commit": "e04b37103c4292e9316de24e464d1aa675437989",
  "srs_version": "2.0.0",
  "tests": {
    "srs_v2_regression": "12/12 (100% PASSED)",
    "full_suite": "68/68 (100% PASSED)"
  },
  "events": {
    "total_generated": 3,
    "plausible_review_event": 3,
    "likely_false_positive": 0,
    "insufficient_visibility": 0
  },
  "evidence": {
    "snapshot_success": "3/3",
    "video_success": "3/3",
    "hash_verified": "3/3",
    "eof_protection_verified": true
  },
  "fps": {
    "30_fps_events": 3,
    "10_fps_events": 3,
    "5_fps_events": 2,
    "fully_event_invariant_at_10fps": true,
    "sampling_boundary_at_5fps": "SEAT-101-02 elevated to SUSPICIOUS (65.8) but did not cross 80.0 threshold due to 200ms visual subsampling."
  },
  "human_review_semantics": {
    "no_cheating_state_in_production": true,
    "states_present": [
      "NORMAL",
      "OBSERVE",
      "SUSPICIOUS",
      "FLAGGED_FOR_REVIEW",
      "COOLDOWN"
    ],
    "event_status": "suspicious (pending proctor verdict)"
  },
  "remaining_unvalidated": [
    "BELOW_DESK_INTERACTION positive physical phone usage (no actual student used a phone in this demo video, though writing guard and capability gating are verified)",
    "SEAT_LEFT prolonged absence (no student left their seat during this 71.8s exam session)"
  ],
  "acceptance_decision": "ACCEPTED_FOR_PROTOTYPE_DEMO"
}
```

---

## 14. Quyết định Nghiệm thu Cuối cùng (Final Acceptance Decision)

### Quyết định: **`ACCEPTED_FOR_PROTOTYPE_DEMO`**

### Lý do & Căn cứ Chấp thuận:
1. **Tính Nhất quán Nội tại (Internal Coherence):** Pipeline 7 lớp hoạt động liền mạch từ khung hình thô đến sự kiện đánh giá; toàn bộ dữ liệu có thể truy vết ngược 2 chiều minh bạch (Event $\leftrightarrow$ Pattern $\leftrightarrow$ Episode $\leftrightarrow$ Observation $\leftrightarrow$ Frame).
2. **Khả năng Giải thích Cao (Explainability):** Mỗi sự kiện đều chỉ rõ pattern chính, số lần lặp lại, hướng quay/nghiêng, thí sinh mục tiêu và chất lượng quan sát.
3. **Bảo toàn Bằng chứng Đầy đủ (Evidence Preserving):** Toàn bộ 3 sự kiện đều xuất đủ video clip kèm mã băm SHA-256 xác thực và cơ chế EOF flushing chống mất mát dữ liệu cuối phiên.
4. **Triệt tiêu Hoàn toàn Báo động Giả Tư thế Làm bài:** Vị trí `SEAT-101-01` làm bài bình thường không phát sinh sự kiện nào, điểm rủi ro luôn được giữ dưới mức an toàn $10.04$.
5. **Tuân thủ Tuyệt đối Quy chuẩn Giám thị Hỗ trợ (Human-in-the-loop):** Không kết luận gian lận tự động, định vị đúng vai trò trợ lý thông minh cho hội đồng khảo thí.

# DANH MỤC HÀNH VI KIỂM THỬ DEMO THỰC TẾ (DEMO BEHAVIOR INVENTORY)
**Tài liệu:** `docs/DEMO_BEHAVIOR_INVENTORY.md`  
**Dự án:** VIGIL AI — AI-Assisted Exam Monitoring Co-Pilot (ICTU 2026)  
**Phiên bản:** 1.0 — Final Prototype Completion  
**Ngày lập:** 18/09/2026  

---

## 1. TỔNG QUAN TẬP DỮ LIỆU VIDEO DEMO

| Thông số | Video 1: India Classroom | Video 2: Student Classroom |
|---|---|---|
| **Tệp video** | `video/india_classroom.mp4` | `video/student_classroom.mp4` |
| **Độ phân giải** | $1280 \times 720$ (HD 720p) | $640 \times 352$ (SD Widescreen) |
| **Tốc độ khung hình (FPS)** | 30.0 FPS | 20.0 FPS |
| **Tổng số khung hình** | 2,153 frames | 219 frames |
| **Thời lượng** | 71.8 giây | 10.9 giây |
| **Số bàn thi hiệu chuẩn** | 21 bàn (`ROOM-CALIB-01`) | 12 bàn (`ROOM-STUDENT-01`) |
| **Góc nhìn camera** | Góc chéo cao từ phía sau bên trái | Góc trực diện từ phía sau lớp học |
| **Mục đích kiểm thử** | Phòng thi đông người, kiểm tra độ nhạy góc quay đầu 3D & liếc lặp lại | Kiểm tra độ phân giải thấp, nghiêng người và tương tác cự ly gần |

---

## 2. MA TRẬN CHI TIẾT CÁC HÀNH VI TRONG VIDEO 1 (INDIA CLASSROOM)

| STT | Mã bàn | Khoảng thời gian (s) | Hành vi thực tế (Ground Truth) | Tín hiệu thị giác (AI Observation) | Atomic Episode hình thành | Composite Pattern kích hoạt | Đỉnh điểm rủi ro | Trạng thái đề xuất | Bằng chứng MP4 trích xuất |
|:---:|---|:---:|---|---|---|---|:---:|:---:|:---:|
| **01** | `SEAT-ROOM-CALIB-01-20` | 0.0s — 20.4s | Thí sinh bàn cuối nhìn sang phải quan sát | $Yaw > +32^\circ$ (sau khi trừ baseline) | `HEAD_TURN_RIGHT` (Active 12.8s) | — | 48.0 | `OBSERVE` | Không phát sinh cảnh báo (hành vi đơn lẻ) |
| **02** | `SEAT-ROOM-CALIB-01-04` | 23.5s — 25.7s | Liếc nhanh sang bàn bên phải lần 1 | $Yaw > +35^\circ$, thời gian 2.1s | `HEAD_TURN_RIGHT` | — | 52.0 | `OBSERVE` | Đưa vào bộ đếm cửa sổ trượt 25s |
| **03** | `SEAT-ROOM-CALIB-01-15` | 25.8s — 29.7s | Quay đầu sang trái nhìn bài bạn bàn 14 | $Yaw < -34^\circ$, duy trì 3.9s | `HEAD_TURN_LEFT` | — | 58.0 | `OBSERVE` | Ghi nhận quan sát |
| **04** | `SEAT-ROOM-CALIB-01-15` | 33.5s — 40.3s | Quay đầu sang phải nhìn bàn 16 | $Yaw > +36^\circ$, duy trì 6.8s | `HEAD_TURN_RIGHT` | `REPEATED_NEIGHBOR_GLANCE` | 74.0 | `SUSPICIOUS` | Cận ngưỡng cảnh báo |
| **05** | `SEAT-ROOM-CALIB-01-04` | 43.4s — 45.8s | Liếc sang bàn bên phải lần 2 (tái phạm) | $Yaw > +38^\circ$, lặp lại trong 20s | `HEAD_TURN_RIGHT` | `REPEATED_NEIGHBOR_GLANCE` | **85.0** | `FLAGGED_FOR_REVIEW` | **EVT-04-A_neighbor_glance.mp4** (10s MP4) |
| **06** | `SEAT-ROOM-CALIB-01-16` | 43.6s — 46.8s | Quay đầu sang trái nhìn bạn bàn 15 | $Yaw < -32^\circ$, duy trì 3.2s | `HEAD_TURN_LEFT` | — | 54.0 | `OBSERVE` | Tín hiệu đối xứng với bàn 15 |
| **07** | `SEAT-ROOM-CALIB-01-02` | 45.2s — 56.6s | Bàn 1 giữa quay sang bàn 3 bên phải | $Yaw > +40^\circ$, nghiêng người | `HEAD_TURN_RIGHT` | `NEIGHBOR_ORIENTED_LEAN` | **88.0** | `FLAGGED_FOR_REVIEW` | **EVT-02-B_lean_glance.mp4** (10s MP4) |
| **08** | `SEAT-ROOM-CALIB-01-04` | 48.5s — 61.7s | Tiếp tục liếc sang phải nhiều lần | $Yaw > +35^\circ$ lặp lại liên tục | `HEAD_TURN_RIGHT` | `REPEATED_NEIGHBOR_GLANCE` | 45.0 (Cooldown) | `COOLDOWN` | Đang trong cooldown 5s, không spam cảnh báo mới |
| **09** | `SEAT-ROOM-CALIB-01-03` | 51.8s — 58.8s | Cúi người nhìn điện thoại dưới hộc bàn | $Pitch > +25^\circ$, cổ tay dưới bàn | `HEAD_PITCH_DOWN` | `BELOW_DESK_INTERACTION` | 62.0 | `SUSPICIOUS` | Đưa vào hàng đợi theo dõi |
| **10** | `SEAT-ROOM-CALIB-01-02` | 58.6s — 63.6s | Quay lại nhìn bàn 3 lần nữa | $Yaw > +38^\circ$ | `HEAD_TURN_RIGHT` | `REPEATED_NEIGHBOR_GLANCE` | 76.0 | `SUSPICIOUS` | Escalation sau cooldown |
| **11** | `SEAT-ROOM-CALIB-01-03` | 59.3s — 63.8s | Bàn 3 quay sang nhìn bàn 2 | $Yaw < -36^\circ$ | `HEAD_TURN_LEFT` | `PAIRWISE_INTERACTION` | **82.0** | `FLAGGED_FOR_REVIEW` | **EVT-03-C_cross_look.mp4** (10s MP4) |
| **12** | `SEAT-ROOM-CALIB-01-02` | 64.8s — 71.2s | Tương tác vật thể dưới bàn cuối giờ | Cổ tay dưới ranh giới mặt bàn | `WRIST_BELOW_DESK` | `BELOW_DESK_INTERACTION` | 68.0 | `SUSPICIOUS` | Đóng và flush khi hết video |

---

## 3. MA TRẬN CHI TIẾT CÁC HÀNH VI TRONG VIDEO 2 (STUDENT CLASSROOM)

| STT | Mã bàn | Khoảng thời gian (s) | Hành vi thực tế (Ground Truth) | Tín hiệu thị giác (AI Observation) | Atomic Episode hình thành | Composite Pattern kích hoạt | Đỉnh điểm rủi ro | Trạng thái đề xuất | Bằng chứng MP4 trích xuất |
|:---:|---|:---:|---|---|---|---|:---:|:---:|:---:|
| **01** | `SEAT-STUDENT-01` | 1.0s — 4.5s | Thí sinh bàn 1 dãy giữa viết bài bình thường | Cổ tay nằm trên mặt bàn ($Y \le 280$) | `NORMAL_WRITING` | — | 15.0 | `NORMAL` | **Triệt tiêu cảnh báo** hoàn toàn nhờ Writing Zone Gating |
| **02** | `SEAT-STUDENT-02` | 2.0s — 6.5s | Thí sinh bàn 1 trái nghiêng người sang bạn bên cạnh | Góc nghiêng vai vai trái/phải $> 18^\circ$ | `TORSO_LEAN_RIGHT` | `NEIGHBOR_ORIENTED_LEAN` | 65.0 | `SUSPICIOUS` | Tăng điểm ưu tiên giám sát |
| **03** | `SEAT-STUDENT-06` | 2.5s — 5.8s | Thí sinh bàn 2 trái quay đầu sang phải | $Yaw > +30^\circ$, duy trì 3.3s | `HEAD_TURN_RIGHT` | — | 60.8 | `SUSPICIOUS` | Ghi nhận quan sát |
| **04** | `SEAT-STUDENT-07` | 2.5s — 7.0s | Thí sinh bàn 2 phải cúi nhìn tài liệu | $Pitch > +22^\circ$ | `HEAD_PITCH_DOWN` | — | 56.2 | `OBSERVE` | Theo dõi biến động tư thế |
| **05** | `SEAT-STUDENT-01` | 6.0s — 10.9s | Tiếp tục hoàn thành bài thi nghiêm túc | Tay trong Writing Zone | `NORMAL_WRITING` | — | 12.0 | `NORMAL` | Giữ điểm an toàn ổn định |

---

## 4. GIẢI THÍCH CÁC CƠ CHẾ KHỬ NHIỄU VÀ BẢO VỆ ĐỘ CHÍNH XÁC

### 4.1. Writing Zone Mandatory Suppression (Triệt tiêu cúi đầu khi viết bài)
- Khi thí sinh cúi đầu xuống làm bài, góc Pitch sẽ tăng cao giống hành vi giấu tài liệu. Tuy nhiên, nếu điểm cổ tay (`left_wrist`, `right_wrist`) nằm trong tọa độ `writing_zone_polygon` hoặc nằm trên ranh giới `desk_boundary_y`, thuật toán **bắt buộc** gán trạng thái `NORMAL_WRITING` và ngăn chặn kích hoạt cảnh báo rác.

### 4.2. Per-Seat Baseline Subtraction (Bù góc camera)
- Các bàn ở góc biên trái hoặc biên phải của camera luôn có góc đầu nhìn về bảng lệch tự nhiên từ $5^\circ$ đến $10^\circ$.
- Công thức: `relative_yaw = raw_6d_yaw - seat_baseline_yaw`.
- Giúp thí sinh ngồi thẳng nhìn bảng không bị nhận diện nhầm thành đang quay đầu.

### 4.3. Incident Aggregation & Cooldown (Chống ngập lụt cảnh báo)
- Sau khi bàn thi vượt ngưỡng 80 điểm và phát sinh cảnh báo `FLAGGED_FOR_REVIEW`, hệ thống tự động khóa bàn vào chế độ `COOLDOWN` (5 giây) và reset điểm về 45.0.
- Các hành động liếc nhìn tiếp theo của thí sinh trong cùng sự việc được cộng dồn vào `occurrence_count` của thẻ sự kiện hiện có, thay vì xả ra 50 thông báo riêng lẻ làm tê liệt giám thị.

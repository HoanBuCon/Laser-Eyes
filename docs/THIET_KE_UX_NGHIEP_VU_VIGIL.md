# VIGIL AI — Thiết kế tối ưu UI/UX và nghiệp vụ hệ thống

> Nhánh: `feat/ux-business-redesign` (tách từ `DEV` @ `ea0d043`)
> Phạm vi: toàn bộ giao diện web (`/`, `/demo`, `/calibration`, `/data-workbench`) và các nghiệp vụ phía sau: giám sát, review, xem lại, báo cáo, lưu trữ, nhiều phòng.
> Tài liệu dựa trên khảo sát thực tế: chạy server với bản sao DB hiện tại, dùng thử từng trang, đọc mã nguồn và dữ liệu (số liệu ghi ở mục 2).

---

## 1. Tóm tắt

Phần lõi AI (nhận diện → episode → pattern → điểm rủi ro → incident → bằng chứng có SHA-256) đã chạy được. Nhưng **nghiệp vụ xung quanh** vẫn đang dừng ở mức demo một video:

1. Mỗi lúc chỉ phân tích **một phòng**, và mọi thứ (incident, review, xem lại) gắn với **lần chạy hiện tại** trên trang `/demo`.
2. Dữ liệu đã lưu (289 incident, 439 phiên, 3,9 GB bằng chứng) **không có màn hình nào để khai thác**: không có hàng đợi review liên phiên, không xem lại được phiên cũ, không có báo cáo.
3. Có **ba nơi review khác nhau** với ba con số khác nhau, KPI tổng quan sai, phiên thi không bao giờ kết thúc, và chưa có người dùng thật.

Đề xuất xoay quanh một mô hình nghiệp vụ rõ ràng: **Kỳ thi → Ca thi theo phòng → Phân tích → Incident → Review → Báo cáo → Lưu trữ**. Kèm theo là 6 màn hình chính cho giám thị và 3 màn hình quản trị, triển khai làm 3 giai đoạn (mục 8).

---

## 2. Hiện trạng (khảo sát ngày 2026-10-09)

### 2.1 Bản đồ tính năng

| Khu vực | Tính năng | Trạng thái | Ghi chú / bằng chứng |
|---|---|---|---|
| `/demo` | Phân tích Live 1 video, stream MJPEG, overlay | ✅ Hoạt động | Chỉ 1 phòng một lúc: `DemoRuntime` là singleton (`classroom_monitor/demo/runtime.py:142`) |
| `/demo` | Replay | ⚠️ Hạn chế | Chỉ phát **bản replay mới nhất** của mỗi preset; không chọn được phiên cũ |
| `/demo` | Hàng đợi review (cột phải) | ⚠️ Hạn chế | Chỉ hiện incident của **lần chạy hiện tại** (bộ nhớ). Chưa bấm Start thì luôn trống |
| `/demo#reviewQueue` | Mục "02 Review queue" ở sidebar | ❌ Không đúng kỳ vọng | Chỉ là anchor nhảy tới cột phải của `/demo` (`dashboard/index.html:46`); không có trang hàng đợi thật dù API `/api/v1/proctor/queue` đã có (`api/routes/proctor.py:227`) |
| `/demo` | Modal review: video 10 s, ảnh, khoanh nghi phạm, quyết định | ✅ Hoạt động | Người review ghi cứng `Lead_Proctor` (`dashboard/js/demo.js:1113`) |
| `/demo` | Mark frame (giám thị đánh dấu) | ✅ Hoạt động | `created_by: 'Lead_Proctor'` ghi cứng |
| `/demo` | Xuất JSON/CSV | ✅ Hoạt động | Theo **một phiên**; file xuất có `source_timestamp_ms = 0` (không biết incident xảy ra lúc nào) |
| `/` Operations | KPI "Monitored rooms" = 98 | ❌ Sai dữ liệu | 98 phòng nhưng chỉ 6 phòng có ROI; phần lớn là phòng rác do test tạo trước khi cách ly test |
| `/` Operations | KPI "Human review rate" = 100% | ❌ Bug | Thực tế 110/289 = 38%. JS đọc `events_by_review_status` mà API không trả (`dashboard/js/app.js:190`) |
| `/` Operations | Biểu đồ "Behavior Signal Distribution" | ❌ Lỗi thời | Dùng nhãn cũ `PROLONGED_HEAD_TURN`, `BODY_LEAN_SIDE`… (`app.js:92`). Hành vi hiện tại (`REPEATED_NEIGHBOR_GLANCE`, `HEADS_TOGETHER`…) không được đếm |
| `/` Operations | Lưới phòng, feed sự kiện, modal review | ⚠️ Hạn chế | Điểm rủi ro phòng là số tĩnh từ DB, không phải trạng thái live; reviewer ghi cứng `proctor_admin` (`app.js:459`) |
| `/calibration` | Vẽ Seat ROI theo phòng/camera | ✅ Hoạt động | Nguồn ROI duy nhất |
| `/data-workbench` | Duyệt ảnh, gán nhãn video, dataset | ✅ Hoạt động (công cụ ML) | Có **hàng đợi review AI thứ ba** (179 pending); vai trò chọn tay (`activeRole`) |
| API phiên thi | `POST /sessions/{id}/start\|stop` | ⚠️ Mock | Chỉ đổi trạng thái DB, không chạy phân tích |
| Vòng đời phiên | Kết thúc phiên | ❌ Bug | 439/439 phiên ở `RUNNING`; runtime tạo `ExamSession` (`runtime.py:498`) nhưng không bao giờ cập nhật COMPLETED |
| Worker | Đăng ký/heartbeat, `worker_node.py` | ⚠️ Chưa nối | Có code chạy nhiều camera nhưng API/UI không dùng |
| `/inference` | Pipeline cũ | ⛔ Đã tắt | Trả 410; nên gỡ khỏi API docs |
| Lưu trữ | Bằng chứng, replay | ⚠️ Không có chính sách | `data/demo_runs` 3,0 GB / 203 lần chạy, `demo_final` 766 MB, cache playback 123 MB; không có xóa tự động |
| Người dùng | Đăng nhập, vai trò | ❌ Chưa có | Chỉ có `X-Vigil-Demo-Token` dùng chung |

### 2.2 Số liệu DB (bản sao `data/vigil_proctoring.db`)

| Đối tượng | Số lượng |
|---|---|
| Phòng | 98 (6 có ROI) |
| Phiên thi | 439, tất cả `RUNNING` |
| Incident | 289: PENDING 179 · CONFIRMED 32 · REJECTED 38 · INCONCLUSIVE 40 |
| File bằng chứng | 287 |
| Review | 110 |
| Audit log | 187 |

### 2.3 Năng lực xử lý (ràng buộc cho nhiều phòng)

* Pipeline hiện tại (YOLO11n-pose 1280 + 6DRepNet 5 Hz) trên RTX 4060 Laptop: **≈ 12–13 FPS xử lý cho 1 video**, tức 0,4× thời gian thực ở 30 FPS nguồn.
* Báo cáo benchmark cũ (`reports/BENCHMARK_10_20_ROOMS_SCALABILITY_REPORT.md`) cho 10 phòng × 4,6 FPS trên cùng GPU, nhưng đo trên pipeline cũ. **Cần đo lại** với pipeline SRS v2 trước khi cam kết số phòng.
* Logic thời gian (episode, hysteresis, dwell) tính theo mili-giây, nên chạy 5 FPS/phòng vẫn đúng ngữ nghĩa. Ngân sách hợp lý là **5 FPS/phòng**, tức khoảng **2–3 phòng/GPU** với pipeline hiện tại. Nhiều hơn thì cần giảm `imgsz` hoặc thêm worker.

---

## 3. Vấn đề nghiệp vụ cốt lõi

| # | Vấn đề | Hệ quả cho người dùng |
|---|---|---|
| N1 | Không có khái niệm **Kỳ thi / Ca thi** trên giao diện; mỗi lần bấm Start tạo một "phiên demo" | Không trả lời được "ca sáng nay ở phòng 101 có gì?" |
| N2 | Review bị trói vào lần chạy đang mở | Đóng tab hoặc chạy video khác là mất hàng đợi; incident tồn đọng không ai xử lý (179 PENDING) |
| N3 | Không xem lại được video của phiên đã xong | Không đối chiếu được ngữ cảnh trước/sau clip 10 s; không xử lý khiếu nại |
| N4 | Không có báo cáo | Không lập được biên bản, không tổng hợp cho hội đồng thi |
| N5 | Chỉ một phòng mỗi lúc | Không đúng mục tiêu "nhiều phòng học" |
| N6 | Không có người dùng, vai trò | Không biết ai đã quyết định gì; audit log thiếu giá trị pháp lý |
| N7 | Không có chính sách lưu trữ | Ổ đĩa đầy dần; không biết bằng chứng nào cần giữ |
| N8 | KPI sai, dữ liệu test lẫn vào dữ liệu thật | Người xem mất niềm tin vào màn hình tổng quan |

---

## 4. Mô hình nghiệp vụ đề xuất

### 4.1 Vai trò

| Vai trò | Việc chính | Màn hình |
|---|---|---|
| **Giám thị phòng / giám sát** | Theo dõi live, đánh dấu khung hình, review incident của phòng mình | Giám sát đa phòng, Phòng, Hàng đợi review |
| **Trưởng ban coi thi** | Phân công review, quyết định cuối, xem báo cáo, xử lý khiếu nại | Tổng quan, Hàng đợi review, Xem lại, Báo cáo |
| **Quản trị kỹ thuật** | Phòng/camera/ROI, ca thi, lưu trữ, worker, dữ liệu ML | Cấu hình, Hiệu chỉnh ghế, Lưu trữ, Công cụ dữ liệu |

Giai đoạn đầu chỉ cần **đăng nhập cục bộ** (tài khoản + vai trò lưu trong DB, mật khẩu băm). Toàn bộ `reviewer_id` lấy từ người đang đăng nhập, thay cho giá trị ghi cứng.

### 4.2 Thực thể và vòng đời

```
Kỳ thi (Exam)                 ví dụ: "Thi HK1 2026 – Toán 10"
 └─ Ca thi (ExamSession)       1 phòng × 1 khung giờ; có lịch, giám thị phụ trách
     ├─ Nguồn video            camera RTSP hoặc file ghi hình
     ├─ Lần phân tích (Run)    live hoặc phân tích lại file; có thể nhiều lần
     │   └─ Incident           ghế, hành vi, mốc bắt đầu → báo, điểm, bằng chứng
     │       └─ Review         người, quyết định, lý do, ghi chú, thời điểm
     ├─ Đánh dấu của giám thị (Bookmark)
     └─ Báo cáo ca thi         sinh khi ca kết thúc; khóa khi trưởng ban duyệt
```

**Trạng thái ca thi**

```
DRAFT ─(đủ camera + ROI)→ READY ─(bắt đầu)→ RUNNING ─(kết thúc / hết giờ)→ COMPLETED ─(duyệt báo cáo)→ CLOSED
                                     └─(lỗi)→ FAILED                          └─(hết hạn lưu trữ)→ ARCHIVED
```

* Runtime **phải cập nhật** `ExamSession.status/ended_at/total_events` khi kết thúc (hiện là bug).
* Một ca thi chỉ chuyển sang `CLOSED` khi **không còn incident PENDING**, hoặc trưởng ban ghi rõ lý do đóng.

**Trạng thái incident**

```
PENDING ─→ CONFIRMED | REJECTED | INCONCLUSIVE
   └─→ ESCALATED (giám thị chuyển trưởng ban) ─→ CONFIRMED | REJECTED
```

Mỗi lần chuyển trạng thái ghi một dòng `EventReview` và một dòng `AuditLog` (đã có bảng). Thêm hai trường: `assigned_to` (người được giao) và `due_at` (hạn xử lý).

---

## 5. Kiến trúc thông tin mới

```
SIDEBAR
  GIÁM SÁT
    01  Tổng quan ca thi        /                 (thay Operations)
    02  Giám sát đa phòng        /monitor          (MỚI – video wall)
    03  Phòng                    /monitor/{room}   (= /demo hiện tại, theo phòng)
  XỬ LÝ
    04  Hàng đợi review          /review           (MỚI – liên phiên)
    05  Xem lại                  /playback         (MỚI)
    06  Báo cáo                  /reports          (MỚI)
  CẤU HÌNH (quản trị)
    07  Kỳ thi & ca thi          /admin/sessions   (MỚI)
    08  Phòng, camera & ghế      /calibration      (mở rộng)
    09  Lưu trữ                  /admin/storage    (MỚI)
    10  Công cụ dữ liệu ML       /data-workbench   (chuyển xuống khu kỹ thuật)
```

Nguyên tắc:

* **Một nơi review duy nhất** (`/review`). Modal review trên các trang khác dùng chung một component và chung một API.
* Mọi màn hình có **bộ lọc ngữ cảnh** ở đầu trang: Kỳ thi ▸ Ca thi ▸ Phòng. Bộ lọc được giữ trong URL để chia sẻ link.
* Giữ phong cách UI hiện tại: token `vigil-*`, nhãn mono, segmented control, danh sách dạng hàng 50 px như queue mới.

---

## 6. Thiết kế màn hình

### 6.1 Tổng quan ca thi (`/`, thay Operations)

**Mục tiêu:** trong 5 giây trả lời được "đang có bao nhiêu phòng thi, phòng nào cần chú ý, còn bao nhiêu việc chưa xử lý".

```
┌ Kỳ thi: [HK1 2026 – Toán 10 ▾]  Ca: [Sáng 09/10 ▾]                         [Bắt đầu ca] ┐
├──────────┬──────────┬──────────────┬──────────────┬──────────────────────────────────────┤
│ PHÒNG    │ ĐANG     │ INCIDENT     │ CHỜ REVIEW   │ ĐÃ REVIEW                            │
│ 6 / 6    │ PHÂN TÍCH│ 31           │ 12  (quá hạn │ 61%  (19/31)                         │
│ sẵn sàng │ 3        │ ▲ 8/15 phút  │ 2)           │                                      │
├──────────┴──────────┴──────────────┴──────────────┴──────────────────────────────────────┤
│ PHÒNG CẦN CHÚ Ý (sắp theo pending × mức độ)    │ HOẠT ĐỘNG GẦN ĐÂY                        │
│ 101  ● Live  pending 5  HIGH 3   [Mở]          │ 09:42  P101 S07 Heads together  → review │
│ 203  ● Live  pending 2           [Mở]          │ 09:40  P203 Trưởng ban xác nhận S12      │
│ 105  ○ Chờ   ROI thiếu 4 ghế  [Hiệu chỉnh]     │ ...                                      │
├─────────────────────────────────────────────────┴────────────────────────────────────────┤
│ PHÂN BỐ HÀNH VI (theo PatternType hiện tại)  │ INCIDENT THEO THỜI GIAN (15 phút / cột)   │
└──────────────────────────────────────────────┴───────────────────────────────────────────┘
```

* KPI tính từ API mới `GET /api/v1/dashboard/summary?exam_id=&session_id=`, trả đủ `by_review_status`, `by_pattern`, `overdue`.
* Biểu đồ hành vi đọc danh sách `PatternType` từ backend; không ghi cứng nhãn.
* Ẩn dữ liệu test: phòng/ca có cờ `is_test` hoặc không thuộc kỳ thi nào. Kèm lệnh dọn dữ liệu (mục 7).

### 6.2 Giám sát đa phòng (`/monitor`, MỚI)

**Mục tiêu:** một màn hình theo dõi nhiều phòng; phòng nào có vấn đề tự nổi lên.

```
┌ Bố cục [2×2] [3×3] [Tự động]   Sắp xếp [Rủi ro ▾]   Chỉ phòng có cảnh báo [ ]          ┐
├─────────────────────────────┬─────────────────────────────┬─────────────────────────────┤
│ P101 · Toán 10 · ● LIVE     │ P203 · Toán 10 · ● LIVE     │ P105 · ○ CHƯA BẮT ĐẦU       │
│ ┌─────────────────────────┐ │ ┌─────────────────────────┐ │                             │
│ │  stream 640×360, 2 fps  │ │ │  ghế nghi vấn viền cam  │ │   [Bắt đầu]  [Hiệu chỉnh]   │
│ └─────────────────────────┘ │ └─────────────────────────┘ │                             │
│ 22/24 ghế · pending 5 ▲     │ 20/22 ghế · pending 2       │                             │
│ S07 Heads together 82  ⚑    │                             │                             │
└─────────────────────────────┴─────────────────────────────┴─────────────────────────────┘
```

* **Mỗi ô là một luồng xem nhẹ:** 640 px, 2 FPS, JPEG chất lượng 60. Luồng phân tích vẫn chạy ở FPS ngân sách riêng. Bấm vào ô mở `/monitor/{room}` (trang `/demo` hiện tại).
* Khi phòng có incident mới: viền ô nháy theo mức độ, đẩy ô lên đầu nếu đang sắp theo "Rủi ro", hiện một dòng ghế + hành vi + điểm; nút ⚑ mở review ngay.
* **Backend cần:** `RuntimeManager` quản lý nhiều phân tích song song (mỗi ca thi một `DemoRuntime`), cấp **ngân sách FPS** theo số phòng (mặc định 5 FPS/phòng) và **từ chối bắt đầu** khi vượt năng lực đo được, kèm thông báo rõ.
* Không có GPU dư thì hỗ trợ chế độ **"phân tích nền"**: ghi hình trước, xếp hàng phân tích sau, kết quả vào hàng đợi review như live.

### 6.3 Phòng (`/monitor/{room}` = `/demo` hiện tại)

Giữ bố cục vừa làm (video + cột review). Thay đổi:

* Chọn **ca thi** thay cho "preset video"; preset demo chỉ còn trong chế độ demo.
* Cột review lấy từ **API liên phiên**, lọc theo ca thi đang xem, nên không còn trống khi chưa Start.
* Nút "Mở hàng đợi đầy đủ" dẫn tới `/review?session=…`.
* Mục "Review queue" ở sidebar **dẫn tới `/review`**, thay cho anchor `#reviewQueue`.

### 6.4 Hàng đợi review (`/review`, MỚI)

**Mục tiêu:** xử lý nhanh và đúng toàn bộ incident tồn đọng của mọi ca thi.

```
┌ Lọc: Kỳ thi ▾  Ca ▾  Phòng ▾  Hành vi ▾  Mức độ ▾  Trạng thái [Chờ|Quá hạn|Của tôi|Tất cả] ┐
├──────────────────────────────────────┬─────────────────────────────────────────────────────┤
│ 12 chờ · 2 quá hạn      Sắp: Ưu tiên │  P101 · S07 · Heads together · HIGH · điểm 82       │
│ ▌ S07 P101 Heads together   82  ⏱2'  │  ┌───────────────────────────────────────────────┐  │
│ ▌ S12 P203 Neighbor glance  76       │  │ video 10 s + khoanh nghi phạm + ghế bên cạnh  │  │
│ ▌ S03 P101 Multi person     71       │  └───────────────────────────────────────────────┘  │
│   ...                                │  Hành vi 14,2 s → báo 21,4 s · ghế cạnh: S08         │
│                                      │  [Xem ±60 s trong Xem lại]  [Ảnh] [SHA-256 ✓]        │
│ ☐ chọn nhiều → [Bác bỏ hàng loạt]    │  Quyết định: (C)onfirm (R)eject (I)nconclusive (E)sc │
│                                      │  Lý do ▾   Ghi chú…                [Lưu & tiếp ▶]    │
└──────────────────────────────────────┴─────────────────────────────────────────────────────┘
```

* **Chia đôi màn hình:** danh sách bên trái, bằng chứng và quyết định bên phải; không mở modal chồng lên.
* **Phím tắt:** `J/K` chuyển incident, `C/R/I/E` chọn quyết định, `Enter` lưu và sang incident kế tiếp. Mục tiêu: review một incident dưới 20 giây.
* **Gộp trùng:** incident cùng ghế, cùng hành vi, cách nhau dưới 60 s hiện thành một nhóm, có thể quyết định một lần cho cả nhóm.
* **Ghế liên quan:** với `HEADS_TOGETHER` và các hành vi hướng về ghế bên cạnh, hiện cả incident của ghế kia.
* **Phân công:** trưởng ban giao incident hoặc giao theo phòng; tab "Của tôi"; hiện SLA (ví dụ 10 phút) và cờ quá hạn.
* **Thay thế:** modal review trên Operations, tab AI review trong Data Workbench và API trùng `/data-workbench/events/*`.
* **API:** mở rộng `GET /api/v1/proctor/queue` với lọc `exam_id`, `room_id`, `pattern`, `severity`, `assigned_to`, `status`, `overdue`, có phân trang; thêm `POST /api/v1/proctor/queue/bulk-review`.

### 6.5 Xem lại (`/playback`, MỚI)

**Mục tiêu:** xem lại toàn bộ ca thi với dòng thời gian incident; phục vụ đối chiếu ngữ cảnh và khiếu nại.

```
┌ Ca: P101 · Sáng 09/10 · 07:30–09:00     Lớp phủ [AI ✓] [ROI ] [Nhãn hành vi ]  Tốc độ 1×  ┐
├────────────────────────────────────────────────────────────────────────────────────────────┤
│                       video toàn ca (gốc hoặc bản đã render)                                │
├────────────────────────────────────────────────────────────────────────────────────────────┤
│ 07:30 ─────────────●───────●──●────────────────────────●───────────────────── 09:00         │
│ S01  ▁▁▁▁▁▁▁▁▃▃▅▅▇▇▅▃▁▁▁   (điểm rủi ro theo thời gian, 1 dòng/ghế, chỉ ghế có incident)    │
│ S02  ▁▁▁▁▁▁▁▁▃▃▅▇▇▅▃▁▁▁▁                                                                  │
│ S07  ▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▅▇▅▁                                                            │
├────────────────────────────────────────────────────────────────────────────────────────────┤
│ Incident: ● S01 Heads together 08:12 [CONFIRMED]  ● S07 Glance 08:41 [PENDING → review]     │
└────────────────────────────────────────────────────────────────────────────────────────────┘
```

* Bấm vào một điểm trên timeline thì nhảy video tới **mốc bắt đầu hành vi trừ 5 s**.
* Lớp phủ bật/tắt ngay trên trình duyệt (SVG vẽ từ dữ liệu ghế và incident), không cần render lại video. Đây là cách đã làm với "Highlight suspect".
* **Lưu trữ cần:** video gốc của ca, hoặc `result.mp4` cho demo, kèm `seat_timeline.json` (điểm rủi ro mỗi ghế mỗi giây; tính sẵn từ `SeatRiskTracker`, khoảng vài trăm KB mỗi ca).
* **API:** `GET /api/v1/sessions/{id}/playback` trả URL video (đã chuyển H.264), timeline, danh sách incident và vị trí ROI.

### 6.6 Báo cáo (`/reports`, MỚI)

**Mục tiêu:** tạo báo cáo ca thi và biên bản phục vụ hội đồng; con người quyết định, AI chỉ là bằng chứng hỗ trợ.

| Báo cáo | Nội dung | Định dạng |
|---|---|---|
| **Báo cáo ca thi** | Thông tin ca, giám thị, độ phủ ROI (số ghế được giám sát / sĩ số), số incident theo trạng thái và hành vi, thời gian review trung bình, danh sách vi phạm **đã xác nhận** kèm ảnh, mốc giờ và người quyết định | Xem trên web, PDF, CSV |
| **Biên bản ghi nhận** (theo từng vi phạm đã xác nhận) | Mẫu biên bản: phòng, ghế, thời điểm, mô tả hành vi quan sát được, ảnh, mã SHA-256 bằng chứng, người lập; **không** dùng kết luận "gian lận" do AI | PDF in ký |
| **Tổng hợp kỳ thi** | So sánh các phòng/ca, tỷ lệ xác nhận/bác bỏ theo hành vi (đo chất lượng AI), incident còn tồn | Web, CSV |
| **Chất lượng AI** (kỹ thuật) | Precision theo loại hành vi từ quyết định review, danh sách báo nhầm để gán nhãn lại | Web; liên kết sang Công cụ dữ liệu |

* Sửa bug xuất hiện tại: `source_timestamp_ms = 0` trong file xuất. Phải ghi `behavior_start_ms` và `first_seen_ms`.
* Báo cáo ca thi **khóa phiên bản** khi trưởng ban duyệt (lưu hash nội dung); sửa sau đó phải tạo bản mới.

### 6.7 Kỳ thi & ca thi (`/admin/sessions`, MỚI)

* CRUD Kỳ thi; tạo ca thi hàng loạt (chọn phòng × khung giờ); gán giám thị.
* Checklist trước ca (để chuyển `DRAFT → READY`): camera trực tuyến, ROI phủ ≥ X% sĩ số, sơ đồ hàng xóm hợp lệ, dung lượng đĩa đủ.
* Bắt đầu và kết thúc ca thật sự **khởi chạy/dừng phân tích** qua `RuntimeManager`, thay cho API start/stop hiện chỉ đổi trạng thái.

### 6.8 Phòng, camera & ghế (`/calibration`, mở rộng)

* Hiện **độ phủ**: số ghế có ROI so với sức chứa. Trên China2 hiện chỉ khoảng 22/45 học sinh có ROI.
* Hiện **sơ đồ hàng xóm** đã suy ra (đường nối như ảnh kiểm tra) để người vẽ ROI phát hiện lỗi ngay, ví dụ S05 China3 vẽ ROI hình chữ nhật làm mất liên kết với S06.
* Cảnh báo ROI chồng lên nhau hoặc với sang hàng sau (nguyên nhân báo nhầm "Multi person").

### 6.9 Lưu trữ (`/admin/storage`, MỚI)

| Loại dữ liệu | Mặc định giữ | Ghi chú |
|---|---|---|
| Video gốc ca thi | 30 ngày | Xóa trước nếu ca đã `CLOSED` và không có incident CONFIRMED |
| Clip + ảnh bằng chứng của incident **CONFIRMED / ESCALATED** | 1 năm hoặc theo quy chế | Có "giữ pháp lý" (legal hold) để không bao giờ tự xóa |
| Clip + ảnh của incident **REJECTED** | 30 ngày | Giữ cho thống kê chất lượng AI |
| Bản replay demo (`data/demo_final`) | Bản mới nhất mỗi preset | Như hiện tại |
| Lần chạy demo (`data/demo_runs`) | 7 ngày | Hiện 3,0 GB / 203 lần chạy |
| Cache chuyển mã (`data/evidence_playback`) | Tạo lại khi cần | Xóa theo LRU khi vượt quota |

* Màn hình hiện dung lượng theo loại, theo kỳ thi; nút "Dọn ngay" (xem trước danh sách sẽ xóa, xác nhận 2 bước, ghi audit log).
* Job dọn chạy hằng ngày; **không bao giờ xóa** bằng chứng còn PENDING hoặc đang legal hold.
* Mọi file bằng chứng giữ SHA-256 (đã có). Thêm nút "Kiểm tra toàn vẹn" chạy hàng loạt.

### 6.10 Công cụ dữ liệu ML (`/data-workbench`)

Giữ nguyên chức năng, nhưng:

* Chuyển xuống nhóm **Cấu hình / Kỹ thuật**, chỉ hiện cho vai trò quản trị.
* Bỏ tab "AI Event Review Queue" (đã có `/review`). Thay bằng nút "Đưa các incident bị bác bỏ vào gán nhãn", lấy dữ liệu từ báo cáo chất lượng AI.

---

## 7. Thay đổi backend và dữ liệu

| Hạng mục | Thay đổi | Ưu tiên |
|---|---|---|
| Vòng đời phiên | Runtime cập nhật `ExamSession` (status, ended_at, total_events, risk) khi kết thúc/dừng/lỗi | P0 |
| Dữ liệu test | Lệnh `python server.py --cleanup-test-data` (xem trước → xác nhận): xóa phòng không có ROI/ca thi do test tạo; cờ `is_test` cho dữ liệu demo | P0 |
| KPI | `GET /dashboard/summary` trả `by_review_status`, `by_pattern`, `overdue`; sửa `app.js` | P0 |
| Hàng đợi liên phiên | Mở rộng `/proctor/queue` (lọc, phân trang, `bulk-review`); trường `assigned_to`, `due_at` | P0 |
| Người dùng | Bảng `users` (username, vai trò, mật khẩu băm), đăng nhập phiên cookie; `reviewer_id` = người đăng nhập | P1 |
| Kỳ thi | Bảng `exams`; `ExamSession.exam_id`, `proctor_user_id`, `scheduled_start/end` | P1 |
| Nhiều phòng | `RuntimeManager` (nhiều `DemoRuntime`, mỗi ca một runtime), ngân sách FPS, endpoint stream xem nhẹ `/monitor/{session}/preview` | P1 |
| Xem lại | Lưu `seat_timeline.json`; `GET /sessions/{id}/playback` | P1 |
| Báo cáo | `GET /reports/sessions/{id}` (JSON) + xuất PDF (HTML → PDF phía server); sửa `source_timestamp_ms` | P1 |
| Lưu trữ | Bảng `retention_policies`, cờ `legal_hold` trên `EvidenceFile`, job dọn hằng ngày, endpoint thống kê dung lượng | P2 |
| Worker | Nối `worker_node.py` với `RuntimeManager` khi cần nhiều GPU/máy | P2 |
| Dọn API | Gỡ `/inference` (đã tắt) và các endpoint review trùng trong `/data-workbench/events/*` | P2 |

---

## 8. Lộ trình

| Giai đoạn | Nội dung | Kết quả nhìn thấy | Ước lượng |
|---|---|---|---|
| **GĐ1: Sửa nền tảng** | Vòng đời phiên; dọn dữ liệu test; KPI đúng; biểu đồ theo PatternType; trang `/review` liên phiên (danh sách + bằng chứng + phím tắt + lọc cơ bản); sidebar dẫn tới `/review`; sửa `source_timestamp_ms` khi xuất | Hàng đợi review hoạt động với 289 incident hiện có; Tổng quan hiển thị số đúng | 3–4 ngày |
| **GĐ2: Nghiệp vụ ca thi** | Người dùng & vai trò; Kỳ thi/Ca thi; Xem lại có timeline; Báo cáo ca thi + biên bản PDF; độ phủ ROI và sơ đồ hàng xóm trên trang hiệu chỉnh | Một ca thi đi trọn vòng: tạo → giám sát → review → báo cáo | 5–7 ngày |
| **GĐ3: Quy mô** | `RuntimeManager` + Giám sát đa phòng (đo lại năng lực với pipeline SRS v2 trước); phân tích nền; lưu trữ & retention; gộp trùng/phân công/SLA trong review; dọn API cũ | Theo dõi 2–3 phòng/GPU cùng lúc; dung lượng tự kiểm soát | 7–10 ngày |

Thứ tự này ưu tiên **giá trị cho dữ liệu đã có** (179 incident tồn) trước khi mở rộng quy mô.

---

## 9. Quyết định cần chủ dự án

1. **Phạm vi demo và sản phẩm:** giữ chế độ "preset video demo" song song với ca thi thật hay gộp hẳn vào mô hình ca thi?
2. **Đăng nhập:** đăng nhập cục bộ có đủ cho giai đoạn này, hay cần SSO?
3. **Thời hạn lưu trữ** bằng chứng đã xác nhận (đề xuất 1 năm) và video gốc (đề xuất 30 ngày): cần theo quy chế thi nào?
4. **Mẫu biên bản:** có mẫu biên bản xử lý vi phạm chuẩn của đơn vị để dựng PDF theo không?
5. **Phần cứng mục tiêu** cho giám sát đa phòng: bao nhiêu phòng trên một máy, có thêm GPU/máy không? Quyết định này ảnh hưởng ngân sách FPS và `imgsz`.
6. **Ngôn ngữ giao diện:** chuyển sang tiếng Việt (người dùng là giám thị) hay giữ tiếng Anh?

---

## Phụ lục A: Lỗi và điểm chưa hoạt động ghi nhận khi khảo sát

| # | Vị trí | Mô tả | Mức |
|---|---|---|---|
| A1 | `dashboard/index.html:46`, `dashboard/demo.html` | "Review queue" chỉ là anchor; hàng đợi trống khi chưa chạy | Cao |
| A2 | `dashboard/js/app.js:190` | Tỷ lệ review luôn 100% (đọc trường API không trả) | Cao |
| A3 | `dashboard/js/app.js:92,126` | Biểu đồ hành vi dùng nhãn cũ, hành vi hiện tại đếm bằng 0 | Trung bình |
| A4 | `classroom_monitor/demo/runtime.py:498` | `ExamSession` không bao giờ chuyển COMPLETED (439 phiên RUNNING) | Cao |
| A5 | DB | 92/98 phòng là dữ liệu test, không có ROI | Trung bình |
| A6 | `api/routes/sessions.py` | start/stop chỉ đổi trạng thái, không chạy phân tích | Trung bình |
| A7 | `dashboard/js/demo.js:509,1113`, `app.js:459` | Người review/đánh dấu ghi cứng | Cao (audit) |
| A8 | Xuất review (`/proctor/sessions/{id}/export`) | `source_timestamp_ms = 0` cho incident AI | Trung bình |
| A9 | Ba nơi review | `/demo`, Operations, Data Workbench: ba hàng đợi, ba con số (0 / 289 / 179) | Cao |
| A10 | `data/demo_runs` | 3,0 GB, không có cơ chế dọn | Trung bình |
| A11 | `/api/v1/inference/*` | Router cũ đã tắt (410) nhưng vẫn hiện trong API docs | Thấp |
| A12 | Header trang `/demo` trên mobile | Hàng badge tràn ngang | Thấp |

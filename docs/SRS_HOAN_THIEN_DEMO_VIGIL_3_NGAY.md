# VIGIL AI — Rà soát nguyên mẫu và SRS hoàn thiện demo (3 ngày)

| Mục | Giá trị |
|---|---|
| Ngày lập | 2026-10-13 |
| Nhánh / commit rà soát | `feat/ux-business-redesign` @ `95f2749` (working tree có thay đổi chưa commit: logo/branding `dashboard/*`) |
| Phạm vi | VIGIL Classroom (web + API + pipeline SRS v2) và VIGIL One (desktop, tên cũ trong code: VIGIL Local) |
| Mục tiêu | Trong 3 ngày: đưa cả hai sản phẩm về một hạ tầng chung để demo trọn vòng **giám sát → tín hiệu → rà soát → báo cáo** |
| Tài liệu liên quan | `docs/VIGIL_LOCAL_VS_CLASSROOM.md`, `docs/THIET_KE_UX_NGHIEP_VU_VIGIL.md`, `docs/BAO_CAO_BAN_GIAO_VIGIL.md`, `docs/MO_TA_SAN_PHAM_VIGIL_AI.md`, `docs/PROCTOR_SUPPORT_ACCEPTANCE.md` |

---

## 1. Tóm tắt điều hành

1. **VIGIL Classroom đã ở mức nguyên mẫu dùng được cho demo.**
   - 369/369 test pass.
   - Server khởi động sạch, 12/12 trang web trả về 200.
   - Đã có trọn nghiệp vụ: tài khoản và vai trò, kỳ thi/ca thi, hàng đợi review liên phiên, xem lại có timeline, báo cáo có duyệt và phiên bản, video wall.
   - Điểm yếu cho demo là **dữ liệu**, không phải tính năng: 0 tài khoản, 0 kỳ thi, 349/362 ca ở trạng thái `INTERRUPTED`, 136 incident `PENDING` tồn từ các lần chạy thử.
2. **VIGIL One chạy được độc lập nhưng hoàn toàn tách khỏi hạ tầng chung.**
   - `exam_monitor/` không import bất kỳ module nào của `storage/`, `api/` hay `proctor_support/`.
   - Phiên và bằng chứng chỉ ghi ra `data/sessions/*.json` và `data/evidence/*.jpg` trên máy chạy app.
   - Mô tả sản phẩm lại ghi One *"gửi thông tin phiên và tư liệu tới máy chủ để người giám thị theo dõi và rà soát"*. **Đây là khoảng trống lớn nhất giữa điều đang hứa và điều code làm được.**
3. **Phía server đã có sẵn "chỗ cắm" cho đa sản phẩm, nhưng chưa dùng hết:**
   - `proctor_bookmarks.source_product` và trường `source_product` trong hàng đợi review đã có.
   - Tuy nhiên `detection_events` chưa có cột này: API gán cứng `"VIGIL_CLASSROOM"` tại `api/routes/proctor.py:203`.
   - `exam_sessions.room_id` bắt buộc, trong khi One làm việc theo thí sinh.
   - API tạo bookmark chỉ nhận `capture_id` lấy từ runtime trong bộ nhớ của server, nên One không dùng được.
4. **Giải pháp đề xuất cho 3 ngày:**
   - Biến server Classroom hiện tại thành **VIGIL Hub** dùng chung.
   - VIGIL One trở thành **client biên (edge client)**: giữ nguyên perception tại máy thí sinh, đồng bộ phiên, sự kiện, ảnh bằng chứng, bookmark và nhịp trạng thái lên Hub qua REST, có hàng đợi offline.
   - Giám thị rà soát cả hai sản phẩm trong **cùng một** hàng đợi `/review`, cùng báo cáo `/reports`, cùng video wall `/wall`.
   - Không gộp perception, không dùng chung file DB giữa các máy, không stream video liên tục.

---

## 2. Kết quả rà soát hiện trạng

### 2.1 Phương pháp (có thể tái lập)

| Kiểm tra | Lệnh / cách làm | Kết quả |
|---|---|---|
| Test tự động | `venv\Scripts\python.exe -m pytest -q` | **369 passed**, 1 cảnh báo SQLAlchemy trong `tests/test_demo_system_integration.py:588` (test rollback có chủ ý) |
| Khởi động server Classroom | `server.py --port 8765` với `DATABASE_URL` trỏ tới **bản sao** của `data/vigil_proctoring.db` (không đụng DB thật) | Khởi động sạch; `/`, `/review`, `/sessions`, `/playback`, `/reports`, `/wall`, `/calibration`, `/admin`, `/users`, `/login`, `/demo`, `/data-workbench` đều 200; OpenAPI có 262 đường dẫn |
| Dữ liệu DB | Truy vấn trực tiếp bản sao DB | 6 phòng, 0 user, 0 kỳ thi, 362 ca (`INTERRUPTED` 349, `STOPPED` 12, `COMPLETED` 1), 218 incident (`PENDING` 136, `CONFIRMED` 32, `REJECTED` 42, `INCONCLUSIVE` 8), 0 bookmark |
| Chạy VIGIL One | `main.py --demo --screenshot …` | App mở, nguồn mô phỏng chạy ~28 FPS, hiệu chỉnh mô phỏng OK, thoát mã 0; có 24 phiên JSON và 360 file bằng chứng tồn tại trên máy |
| Phụ thuộc giữa module | `grep` import của `exam_monitor/` và `main.py` | Không import `storage`, `api`, `proctor_support`, `classroom_monitor` |
| Đối chiếu tài liệu | Đọc các tài liệu ở bảng đầu | Ranh giới hai sản phẩm (`VIGIL_LOCAL_VS_CLASSROOM.md` §5) đã đề xuất lớp `common/` dùng chung nhưng **thư mục này chưa tồn tại** |

Chưa kiểm: camera thật, micro thật, RTSP thật, chạy trên máy demo đích, nhiều client One cùng lúc. Các mục này nằm trong kế hoạch Ngày 3.

### 2.2 VIGIL Classroom — mức hoàn thiện

| Chức năng | Trạng thái | Bằng chứng / ghi chú | Rủi ro demo |
|---|---|---|---|
| Pipeline SRS v2 (pose → ghế → episode → pattern → incident) | Có | `classroom_monitor/pipeline/srs_v2_pipeline.py`; CLI và web dùng chung | Thấp, nếu dùng video đã hiệu chỉnh |
| Phân tích live / replay video preset | Có | `api/routes/demo.py` (start/pause/resume/stop/reset/stream) | Thấp |
| Hiệu chỉnh ghế (ROI, sơ đồ hàng xóm, cảnh báo chồng lấn) | Có | `/calibration`, `/api/v1/rooms/{id}/seats/*` | Thấp |
| Tài khoản và vai trò PROCTOR/CHIEF/ADMIN | Có | `api/auth.py`; **DB chưa có tài khoản nào** (`needs_setup: true`) | Trung bình: phải tạo trước khi demo |
| Kỳ thi / ca thi, checklist, start/stop | Có | `api/routes/exams.py`; **0 kỳ thi trong DB** | Trung bình: phải dựng dữ liệu kịch bản |
| Hàng đợi review liên phiên, quyết định chuẩn | Có | `/review`; enum `PENDING/CONFIRMED/REJECTED/INCONCLUSIVE` | Thấp |
| Bookmark của giám thị (tách khỏi AI) | Có | `storage/proctor_service.py`; 0 bookmark trong DB | Thấp |
| Xem lại có timeline | Có | `/playback`, `/exam-sessions/{id}/playback[/rendered]` | Thấp |
| Báo cáo ca thi, duyệt/mở lại, phiên bản SHA-256, CSV, bản in | Có | `/reports`, `api/routes/reports.py` | Thấp |
| Video wall | Có | `/wall` | Thấp |
| Trang hệ thống / dọn dữ liệu test | Có | `/admin`, `/api/v1/admin/cleanup/*` | Thấp |
| Giám sát **đa phòng đồng thời** (`RuntimeManager`) | **Chưa** | GĐ3 trong `THIET_KE_UX_NGHIEP_VU_VIGIL.md` §8; runtime là singleton `DemoRuntime` | Cao nếu kịch bản demo đòi hai phòng chạy cùng lúc; **không đưa vào demo** |
| Worker node phân tán | Khung API có, chưa nối | `/workers/register`, `/workers/heartbeat`; 0 worker; `worker_node.py` chưa nối `RuntimeManager` | Không đưa vào demo |
| Retention / legal hold | Chưa | GĐ3 | Chỉ trình bày như lộ trình |
| Độ chính xác | Giới hạn | Snapshot validation cũ: precision India 2,58% (`BAO_CAO_BAN_GIAO_VIGIL.md` §14) | Cao nếu demo hứa "phát hiện gian lận"; phải trình bày đúng là *ưu tiên rà soát* |

Kết luận Classroom: **không cần tính năng mới cho demo.** Cần dữ liệu sạch, tài khoản, một kỳ thi kịch bản và một lần tập dượt.

### 2.3 VIGIL One — mức hoàn thiện

| Chức năng | Trạng thái | Bằng chứng / ghi chú |
|---|---|---|
| Nguồn: webcam, video, mô phỏng | Có | `exam_monitor/sources.py` (`CameraSource`, `VideoSource`, `DemoSource`) |
| Mặt/iris/hướng đầu, hiệu chỉnh theo người | Có | `exam_monitor/engine.py` (`GazeAnalyzer`, 36 mẫu hiệu chỉnh) |
| Vật thể (điện thoại/sách), VAD micro | Có, tùy cấu hình | `engine.py`, `audio.py`; có tùy chọn cho phép tài liệu |
| Bộ sự kiện có debounce | Có | `events.py`: `LOOK_AWAY, HEAD_TURN, TALKING, NO_FACE, MULTIPLE_FACES, SUSPICIOUS_OBJECT, LOW_LIGHT, CAMERA_INTERRUPTED` |
| Bookmark khung hình + ghi chú + quyết định | Có (cục bộ) | `persist_local_bookmark`, `_review_local_bookmark` trong `app.py` |
| Lưu phiên, xuất JSON/CSV | Có (cục bộ) | `SessionStore` → `data/sessions/*.json`, `data/evidence/*.jpg` |
| **Gửi phiên / sự kiện / bằng chứng lên máy chủ** | **Không có** | Không có HTTP client, không có cấu hình server |
| **Giám thị xem từ xa** | **Không có** | Chỉ xem trên chính máy chạy app |
| Trạng thái review sự kiện AI theo chuẩn chung | **Lệch chuẩn** | Sự kiện dùng chuỗi tự do `"Chưa xem"` / `"Đã xem"` (`models.py`, `app.py:1683`); bookmark thì đã dùng `PENDING…` |
| Branding VIGIL One | **Chưa** | Giao diện vẫn ghi "LOCAL PROCTOR", "LOCAL / PRIVATE · Dữ liệu chỉ lưu trên máy", logo "V" cũ; lẫn tiếng Anh ("RECALIBRATE", "Allow books/materials for this session") |
| Định danh thí sinh | Nhập tay | `candidate_id` là chuỗi tự do; chưa gắn với kỳ thi/ca thi nào |

### 2.4 Khoảng trống tích hợp One ↔ hạ tầng chung (nguyên nhân gốc)

| # | Khoảng trống | Vị trí | Hệ quả |
|---|---|---|---|
| G1 | One không có lớp kết nối mạng | `exam_monitor/` (toàn bộ) | Không gì từ One xuất hiện trên web |
| G2 | `detection_events` thiếu `source_product` và `subject_ref` | `storage/db_models.py:220`; `api/routes/proctor.py:203` gán cứng | Hàng đợi không phân biệt được sản phẩm; không ghi được mã thí sinh |
| G3 | `exam_sessions.room_id` NOT NULL, không có khái niệm thí sinh hay loại sản phẩm | `storage/db_models.py:185` | Không tạo được ca thi One đúng nghĩa |
| G4 | Bookmark chỉ tạo được từ `capture_id` của runtime server | `api/routes/proctor.py:39-86` | One không gửi được bookmark có sẵn ảnh |
| G5 | Không có API nhận sự kiện / bằng chứng từ client bên ngoài | `api/routes/events.py` chỉ có đọc và review | Không có đường nạp dữ liệu |
| G6 | Không có danh tính / xác thực thiết bị | `api/routes/workers.py` mở, không token | Bất kỳ ai trong mạng cũng ghi được dữ liệu nếu mở API ghi |
| G7 | Lệch từ vựng: severity (`info/low/medium/high` so với `LOW/MEDIUM/HIGH/CRITICAL`), review status, đơn vị thời gian | `exam_monitor/models.py` so với `storage/db_models.py` | Cần bảng ánh xạ cố định |
| G8 | Playback / báo cáo / wall chỉ hiểu dữ liệu Classroom (ghế, video clip) | `/playback`, `/reports`, `/wall` | Cần nhánh hiển thị cho One (theo thí sinh, ảnh tĩnh) |

---

## 3. Phạm vi demo và giả định

**Trong phạm vi:**
1. Một máy Hub (laptop demo) chạy server, phục vụ web cho giám thị.
2. VIGIL Classroom chạy **một phòng** tại một thời điểm, nguồn video đã hiệu chỉnh (live phân tích hoặc replay).
3. Một đến ba client VIGIL One chạy trên laptop có webcam: một máy thật và các máy còn lại ở chế độ mô phỏng. Kết nối LAN hoặc chạy cùng máy với Hub.
4. Giám thị đăng nhập, thấy cả phòng Classroom và các thí sinh One trên `/wall`, rà soát trong `/review`, duyệt báo cáo ở `/reports`.

**Ngoài phạm vi (ghi vào lộ trình, không làm trong 3 ngày):**
- Đa phòng Classroom đồng thời.
- Worker phân tán.
- Stream video liên tục từ One.
- Client One chạy trên trình duyệt.
- SSO.
- Retention tự động.
- Gộp perception hai sản phẩm.
- Lớp `common/` đầy đủ như `VIGIL_LOCAL_VS_CLASSROOM.md` §5: chỉ làm phần tối thiểu ở §6.

**Giả định:** Hub và client cùng mạng LAN tin cậy trong buổi demo. Dữ liệu là dữ liệu trình diễn, không phải dữ liệu thí sinh thật.

---

## 4. Kiến trúc mục tiêu

```mermaid
flowchart LR
    subgraph ONE["VIGIL One — máy thí sinh (desktop Tkinter)"]
        CAM[Webcam / video / mô phỏng] --> GA[GazeAnalyzer + EventDetector]
        GA --> LS[SessionStore cục bộ<br/>JSON + JPEG]
        GA --> OB[Outbox đồng bộ<br/>SQLite cục bộ, retry]
        OB --> HC[HubClient REST<br/>token thiết bị]
    end

    subgraph HUB["VIGIL Hub — server.py hiện tại (FastAPI)"]
        ING[/api/v1/one/* — nạp dữ liệu/]
        RT[Classroom DemoRuntime<br/>SRS v2 pipeline]
        DB[(SQLite vigil_proctoring.db<br/>+ evidence store)]
        WS[Realtime WebSocket]
        ING --> DB
        RT --> DB
        ING --> WS
        RT --> WS
    end

    subgraph WEB["Web giám thị"]
        WALL[/wall/]
        REV[/review/]
        PB[/playback/]
        REP[/reports/]
    end

    HC -- HTTPS/HTTP LAN --> ING
    DB --> REV
    DB --> PB
    DB --> REP
    WS --> WALL
    WS --> REV
```

**Quyết định thiết kế:**

| Quyết định | Chọn | Lý do | Phương án bị loại |
|---|---|---|---|
| Cách One nối vào hạ tầng | REST client đẩy lên Hub (push) | Hub đã có FastAPI, DB, review, báo cáo; One chỉ cần thêm một module client | Dùng chung file SQLite qua mạng (khóa file, không an toàn khi nhiều máy); Hub kéo dữ liệu từ One (One chạy sau NAT) |
| Độ bền khi mất mạng | Local-first: luôn ghi cục bộ trước, outbox đẩy lại có idempotency (`event_id`, `request_id`) | Demo trên Wi-Fi hội trường dễ chập chờn; không được mất bằng chứng | Gửi trực tiếp không đệm |
| Hình ảnh trực tiếp của One trên web | Ảnh thu nhỏ (thumbnail) 640 px gửi kèm nhịp trạng thái mỗi 2 giây | Đủ cho video wall, nhẹ băng thông, ít rủi ro riêng tư | Stream MJPEG/WebRTC liên tục (tốn thời gian và băng thông) |
| Mô hình dữ liệu | Thêm cột cho các bảng hiện có (`product`, `subject_ref`, …) theo cơ chế `ALTER TABLE` sẵn có trong `storage/database.py:init_db` | Không phá dữ liệu cũ, không cần công cụ migration mới | Bảng riêng cho One (nhân đôi review/report) |
| Ca thi One không có phòng vật lý | Một **phòng ảo** `room_code = "VIGIL-ONE"` mỗi điểm thi, mỗi thí sinh là một `ExamSession` thuộc kỳ thi | Giữ `room_id NOT NULL`, tái sử dụng toàn bộ màn hình theo ca thi | Đổi `room_id` thành nullable (lan rộng nhiều truy vấn) |
| Xác thực client | Token thiết bị: CHIEF tạo mã ghép nối trên web, One dán mã → nhận token, gửi `Authorization: Bearer` | Đơn giản, đủ cho LAN demo, tách quyền ghi khỏi tài khoản người | Mở API ghi không xác thực |

---

## 5. Yêu cầu chức năng

Mức ưu tiên: **P0** = bắt buộc cho demo; **P1** = nên có; **P2** = làm nếu còn thời gian.

### 5.1 Hub — nạp dữ liệu VIGIL One (FR-HUB)

| ID | Yêu cầu | Ưu tiên | Tiêu chí chấp nhận |
|---|---|---|---|
| FR-HUB-01 | Ghép nối thiết bị: CHIEF tạo mã ghép nối (6 ký tự, hết hạn 10 phút) trên `/admin`; `POST /api/v1/one/devices/pair` đổi mã lấy token thiết bị | P0 | Mã sai hoặc hết hạn trả 401; token chỉ hiển thị một lần; thiết bị xuất hiện trong danh sách với tên máy |
| FR-HUB-02 | Mở phiên One: `POST /api/v1/one/sessions` (exam_id tùy chọn, candidate_ref, candidate_name, source_kind, simulation, client_session_id) → tạo `ExamSession` thuộc phòng ảo `VIGIL-ONE`, `product = "ONE"` | P0 | Gọi lại với cùng `client_session_id` trả về đúng phiên cũ (idempotent); phiên hiện trên `/sessions` với nhãn One |
| FR-HUB-03 | Nạp sự kiện theo lô: `POST /api/v1/one/sessions/{id}/events` (≤ 100 sự kiện) → `DetectionEvent` với `source_product = "VIGIL_ONE"`, `subject_ref = candidate_ref`, `review_status = PENDING` | P0 | Trùng `event_id` không tạo bản ghi mới; ánh xạ đúng bảng §6.3; sự kiện vào `/review` trong ≤ 3 giây khi có mạng |
| FR-HUB-04 | Tải ảnh bằng chứng: `POST /api/v1/one/events/{event_id}/evidence` (multipart JPEG + `sha256`) → `EvidenceFile` | P0 | Hub tự tính lại SHA-256; lệch thì trả 422 và không lưu; ảnh hiện trong ngăn bằng chứng của `/review` |
| FR-HUB-05 | Tải bookmark có ảnh: `POST /api/v1/one/sessions/{id}/bookmarks` (multipart, `request_id`, frame_id, elapsed_ms, note) → `ProctorBookmark` với `source_product = "VIGIL_ONE"` | P0 | Trùng `request_id` là idempotent; bookmark hiện trong `/review` dưới dạng "Đánh dấu thủ công", không có điểm AI |
| FR-HUB-06 | Nhịp trạng thái: `POST /api/v1/one/sessions/{id}/heartbeat` mỗi 2 giây (fps, trạng thái mặt/mắt/đầu, capability health, số sự kiện, thumbnail JPEG tùy chọn) | P0 | Hub phát qua WebSocket; quá 10 giây không có nhịp thì thí sinh chuyển "Mất kết nối" trên `/wall` |
| FR-HUB-07 | Kết thúc phiên: `POST /api/v1/one/sessions/{id}/stop` (tổng frame, fps trung bình, ended_at) → `ExamSession` thành `COMPLETED` | P0 | Báo cáo ca thi mở được; KPI đếm đúng |
| FR-HUB-08 | Hàng đợi review đa sản phẩm: bỏ gán cứng tại `api/routes/proctor.py:203`, đọc `source_product` từ DB; bộ lọc "Sản phẩm: Classroom / One" và cột "Thí sinh / Ghế" | P0 | Lọc One chỉ hiện sự kiện One; quyết định review dùng chung API hiện có, ghi audit đúng người |
| FR-HUB-09 | `/wall` hiển thị ô thí sinh One: thumbnail mới nhất, mã thí sinh, chỉ số nhanh (mắt/đầu/người), số tín hiệu chờ, trạng thái kết nối | P0 | Thumbnail cập nhật ≤ 2 giây; có nhãn "MÔ PHỎNG" nếu `simulation = true` |
| FR-HUB-10 | Báo cáo ca thi cho One: mục "Thí sinh" thay cho "Phòng/ghế", bảng tín hiệu theo loại One, ảnh bằng chứng, bookmark | P1 | Duyệt/mở lại và đóng băng SHA-256 hoạt động như Classroom |
| FR-HUB-11 | Xem lại cho One: timeline theo thời gian phiên, chấm sự kiện và bookmark, mở ảnh bằng chứng (không có video) | P1 | Click chấm mở đúng ảnh và chi tiết |
| FR-HUB-12 | Thu hồi token thiết bị trên `/admin` | P1 | Token bị thu hồi thì mọi API One trả 401 |
| FR-HUB-13 | Phân quyền: API ghi `/one/*` luôn cần token thiết bị (kể cả `VIGIL_AUTH_MODE=optional`); API đọc theo quy tắc hiện có | P0 | Gọi không token trả 401 (có test) |

### 5.2 Client — VIGIL One (FR-ONE)

| ID | Yêu cầu | Ưu tiên | Tiêu chí chấp nhận |
|---|---|---|---|
| FR-ONE-01 | Trang Cài đặt: địa chỉ Hub, nút "Ghép nối" (nhập mã), trạng thái thiết bị; lưu token trong `data/settings.json` (không hiển thị lại) | P0 | Ghép nối thành công thì hiện tên Hub; sai mã thì báo lỗi tiếng Việt |
| FR-ONE-02 | Module `exam_monitor/hub_client.py` + outbox SQLite `data/one_outbox.db`: mọi phiên, sự kiện, ảnh, bookmark ghi cục bộ trước rồi đưa vào outbox; worker nền gửi theo thứ tự, retry backoff 1/2/5/10/30 giây | P0 | Rút mạng 60 giây rồi cắm lại: mọi mục được gửi, không trùng; UI Tk không giật (worker riêng luồng) |
| FR-ONE-03 | Chọn ca thi khi mở phiên: nếu đã ghép nối, lấy danh sách kỳ thi đang mở từ Hub; nếu không, chạy chế độ cục bộ như hiện nay | P0 | Không có Hub thì app vẫn chạy và lưu cục bộ (không hồi quy) |
| FR-ONE-04 | Chỉ báo đồng bộ trên header: "Đã đồng bộ", "Đang chờ N mục", "Mất kết nối Hub" | P0 | Khớp số mục còn trong outbox |
| FR-ONE-05 | Heartbeat 2 giây kèm thumbnail 640 px (JPEG q70); tắt được trong Cài đặt | P0 | CPU tăng ≤ 5 điểm %, FPS phân tích giảm ≤ 10 % so với khi tắt (đo trên máy demo) |
| FR-ONE-06 | Chuẩn hóa trạng thái review sự kiện AI sang `PENDING / CONFIRMED / REJECTED / INCONCLUSIVE`; đọc được phiên cũ ("Chưa xem" → PENDING, "Đã xem" → INCONCLUSIVE kèm ghi chú "đã xem, chưa quyết định") | P0 | Mở phiên cũ không lỗi; CSV/JSON xuất ra dùng enum chuẩn |
| FR-ONE-07 | Đổi nhãn sang **VIGIL One**: tiêu đề cửa sổ, sidebar, logo mới (`assets/vigil_ai_logo_no_bg.png`); bỏ "LOCAL PROCTOR" và "Dữ liệu chỉ lưu trên máy" khi đã ghép nối (thay bằng "Đồng bộ với VIGIL Hub"); Việt hóa nhãn còn tiếng Anh | P0 | Ảnh chụp màn hình mọi trang không còn chữ "Local"; tên module trong code giữ nguyên để tránh rủi ro |
| FR-ONE-08 | Quyết định review trên One được đẩy lên Hub (và ngược lại chỉ đọc) | P2 | Nguồn sự thật cho quyết định là Hub; khi đã ghép nối, One hiển thị "Rà soát trên web" |
| FR-ONE-09 | Lưu `capability_health` (mediapipe/iris, object detector, VAD: ENABLED/DEGRADED/DISABLED/MOCK) vào phiên và heartbeat | P1 | Web hiển thị khi một năng lực bị tắt hoặc giảm |

### 5.3 Classroom — hoàn thiện cho demo (FR-CLS)

| ID | Yêu cầu | Ưu tiên | Tiêu chí chấp nhận |
|---|---|---|---|
| FR-CLS-01 | Script `scripts/seed_demo.py` (chạy được nhiều lần): tạo ADMIN/CHIEF/PROCTOR demo, một kỳ thi "Kỳ thi trình diễn VIGIL", một ca Classroom (CLASSROOM-03), phòng ảo VIGIL-ONE, mã ghép nối mẫu | P0 | Chạy hai lần không nhân đôi dữ liệu; in ra tài khoản và mật khẩu demo |
| FR-CLS-02 | Dọn dữ liệu cho demo: sao lưu DB rồi lưu trữ/ẩn 349 ca `INTERRUPTED` và incident tồn của các lần chạy thử (dùng `/api/v1/admin/cleanup/*` hiện có, thêm "archive" nếu cần), hoặc dùng một DB demo riêng qua `DATABASE_URL` | P0 | Trang tổng quan và `/review` chỉ hiện dữ liệu kịch bản; DB gốc có bản sao lưu |
| FR-CLS-03 | Rà lại Phụ lục A của `THIET_KE_UX_NGHIEP_VU_VIGIL.md` (A1–A12), xác nhận mục nào đã sửa; sửa ngay mục còn hiện trên đường demo | P1 | Bảng A cập nhật trạng thái kèm commit |
| FR-CLS-04 | Đổi nhãn sản phẩm trên web: "VIGIL Classroom" hoặc "VIGIL Hub" nhất quán; menu thêm mục "Thí sinh VIGIL One" (lọc `/sessions` theo product) | P1 | Không còn chỗ gọi nhầm tên |
| FR-CLS-05 | Gỡ `/api/v1/inference/*` (410) khỏi OpenAPI (A11) | P2 | Không xuất hiện trong schema |

---

## 6. Hợp đồng dữ liệu và API

### 6.1 Thay đổi cơ sở dữ liệu

Bổ sung bằng `ALTER TABLE … ADD COLUMN` trong `storage/database.py:init_db`, theo cùng cách các cột SRS v2 đang được thêm. Không xóa cột cũ.

| Bảng | Cột mới | Kiểu / mặc định | Ghi chú |
|---|---|---|---|
| `exam_sessions` | `product` | VARCHAR(20) DEFAULT `'CLASSROOM'` | `CLASSROOM` hoặc `ONE` |
| `exam_sessions` | `subject_ref` | VARCHAR(100) NULL | Mã thí sinh (One) |
| `exam_sessions` | `subject_name` | VARCHAR(255) NULL | Tên hiển thị |
| `exam_sessions` | `device_id` | VARCHAR(36) NULL | FK `client_devices.id` |
| `exam_sessions` | `client_session_id` | VARCHAR(64) NULL, UNIQUE | Khóa idempotent từ One |
| `exam_sessions` | `simulation` | BOOLEAN DEFAULT 0 | |
| `exam_sessions` | `last_heartbeat_at`, `live_status_json` | DATETIME / TEXT | Trạng thái gần nhất cho `/wall` |
| `detection_events` | `source_product` | VARCHAR(30) DEFAULT `'VIGIL_CLASSROOM'` | Bỏ gán cứng ở proctor.py |
| `detection_events` | `subject_ref` | VARCHAR(100) NULL | Mã thí sinh hoặc mã ghế |
| bảng mới `client_devices` | `id, name, hostname, token_hash, paired_by, paired_at, last_seen_at, revoked_at, app_version` | | Chỉ lưu SHA-256 của token |
| bảng mới `pairing_codes` | `code_hash, created_by, expires_at, used_at` | | Hết hạn 10 phút, dùng một lần |

Ảnh thumbnail **không** lưu vào DB: Hub giữ ảnh mới nhất trong bộ nhớ, ghi đè mỗi lần heartbeat.

### 6.2 Endpoint mới (tiền tố `/api/v1/one`)

| Phương thức | Đường dẫn | Xác thực | Mục đích |
|---|---|---|---|
| POST | `/pairing-codes` | Người dùng CHIEF/ADMIN | Tạo mã ghép nối |
| POST | `/devices/pair` | Mã ghép nối | Đổi mã lấy token thiết bị |
| GET | `/devices` · DELETE `/devices/{id}` | CHIEF/ADMIN | Danh sách, thu hồi |
| GET | `/exams/open` | Token thiết bị | Kỳ thi đang mở để chọn |
| POST | `/sessions` | Token thiết bị | Mở hoặc gắn lại phiên |
| POST | `/sessions/{id}/heartbeat` | Token thiết bị | Trạng thái + thumbnail |
| POST | `/sessions/{id}/events` | Token thiết bị | Lô sự kiện (idempotent) |
| POST | `/events/{event_id}/evidence` | Token thiết bị | Ảnh bằng chứng + SHA-256 |
| POST | `/sessions/{id}/bookmarks` | Token thiết bị | Bookmark có ảnh |
| POST | `/sessions/{id}/stop` | Token thiết bị | Kết thúc phiên |
| GET | `/sessions/{id}/thumbnail` | Người dùng (theo `VIGIL_AUTH_MODE`) | Ảnh live mới nhất cho `/wall` |

Ví dụ lô sự kiện:

```json
{
  "events": [
    {
      "event_id": "3f6c1e0a-…",
      "event_type": "look_away",
      "severity": "medium",
      "started_at": "2026-10-14T09:12:03.120+07:00",
      "ended_at": "2026-10-14T09:12:07.480+07:00",
      "duration_seconds": 4.36,
      "session_elapsed_ms": 192340,
      "confidence": 0.81,
      "reason": "Mắt ở ngoài vùng an toàn 4,4 giây.",
      "capability_health": {"face": "ENABLED", "objects": "ENABLED", "vad": "DISABLED"},
      "has_evidence": true
    }
  ]
}
```

### 6.3 Ánh xạ từ vựng One → Hub

| One (`exam_monitor/models.py`) | Hub (`detection_events`) |
|---|---|
| `event_type = look_away` | `primary_signal = ONE_LOOK_AWAY`, `behavior = ONE_LOOK_AWAY`, `event_type = SUSPICIOUS_BEHAVIOR` |
| `head_turn`, `talking`, `no_face`, `multiple_faces`, `suspicious_object` | `ONE_HEAD_TURN`, `ONE_TALKING`, `ONE_NO_FACE`, `ONE_MULTIPLE_FACES`, `ONE_SUSPICIOUS_OBJECT` |
| `low_light`, `camera_interrupted` | `ONE_LOW_LIGHT`, `ONE_CAMERA_INTERRUPTED`; `event_type = SYSTEM_CONDITION` (tình trạng kỹ thuật, không vào KPI hành vi) |
| `severity info / low / medium / high` | `LOW / LOW / MEDIUM / HIGH` |
| review `"Chưa xem"` / `"Đã xem"` | `PENDING` / `INCONCLUSIVE` (chỉ khi đọc phiên cũ) |
| `confidence` (0–1) | `observation_quality` (0–1); **không** dùng làm xác suất gian lận |
| `SessionInfo.risk_score` | `exam_sessions.risk_score`, hiển thị là "Mức ưu tiên rà soát" |
| Thời gian: wall-clock cục bộ + `session_elapsed_ms` | Lưu UTC; giữ `session_elapsed_ms` trong `incident_metadata_json` để vẽ timeline |

Nhãn tiếng Việt hiển thị trên web lấy từ `EVENT_LABELS` của One (ví dụ "Mắt nhìn ngoài vùng thi"). Không dùng từ "gian lận".

---

## 7. Yêu cầu phi chức năng

| ID | Yêu cầu | Ngưỡng / cách kiểm |
|---|---|---|
| NFR-01 Độ trễ | Sự kiện One lên `/review` | ≤ 3 giây p95 trên LAN |
| NFR-02 Độ bền | Mất mạng không mất dữ liệu | Rút mạng 60 giây trong phiên; sau khi nối lại, số sự kiện và ảnh trên Hub = số cục bộ |
| NFR-03 Idempotency | Gửi lại không nhân đôi | Test API gửi cùng lô 3 lần |
| NFR-04 Hiệu năng One | Không làm chậm phân tích | FPS giảm ≤ 10 %; UI Tk không treo khi Hub chậm (timeout HTTP 5 giây, luồng riêng) |
| NFR-05 Bảo mật | API ghi cần token; token lưu dạng băm; giới hạn ảnh ≤ 5 MB; kiểm tra MIME JPEG; đường dẫn lưu nằm trong evidence root | Test 401/413/422 |
| NFR-06 Riêng tư | Không lưu thumbnail live vào đĩa; nhãn "MÔ PHỎNG" rõ ràng; không nhận diện khuôn mặt | Rà UI + code |
| NFR-07 Không hồi quy | Toàn bộ test cũ vẫn pass; One chạy được khi không có Hub | `pytest` + chạy `main.py` không cấu hình Hub |
| NFR-08 Quan sát được | Log có cấu trúc cho nạp dữ liệu (device, session, số mục, lỗi) | Kiểm trên `/admin` hoặc log server |
| NFR-09 Ngôn từ | Mọi giao diện dùng "tín hiệu quan sát", "ưu tiên rà soát", "quyết định của giám thị" | Rà chữ trên các màn hình demo |

---

## 8. Kế hoạch 3 ngày

Mỗi ngày kết thúc bằng: chạy `pytest`, chạy thử end-to-end, commit trên nhánh tính năng riêng tách từ `DEV`. Theo quy ước dự án: **commit/push chỉ khi được đồng ý.**

### Ngày 1 — Hub nhận được dữ liệu One (nền tảng)

| # | Việc | Đầu ra | Ước lượng | Hoàn thành khi |
|---|---|---|---|---|
| 1.1 | Sao lưu DB; tạo DB demo riêng (`data/vigil_demo.db`) + `scripts/seed_demo.py` (FR-CLS-01) | Script, tài khoản demo | 1,5 h | Chạy hai lần không nhân đôi |
| 1.2 | Migration cột mới + bảng `client_devices`, `pairing_codes` (§6.1) | `storage/db_models.py`, `storage/database.py` | 1 h | DB cũ khởi động không lỗi; test migration |
| 1.3 | `storage/one_ingest_service.py`: tạo phiên, nạp sự kiện, ảnh, bookmark, heartbeat, stop; ánh xạ §6.3 | Service + test đơn vị | 3 h | Idempotency, SHA-256, ánh xạ có test |
| 1.4 | `api/routes/one.py` + xác thực token thiết bị (FR-HUB-01…07, 13) | Router đăng ký trong `api/main.py` | 2 h | Test 401/200/idempotent qua `TestClient` |
| 1.5 | Bỏ gán cứng `source_product` ở hàng đợi (FR-HUB-08, phần API) | `api/routes/proctor.py` | 0,5 h | Sự kiện One hiện trong `/api/v1/proctor/review-queue` |
| 1.6 | Script giả lập client (`scripts/one_fake_client.py`) gửi phiên mẫu | Script | 0,5 h | Có dữ liệu One trong web để làm UI Ngày 2 |

**Mốc cuối ngày:** gửi bằng script → thấy sự kiện One trong `/review` (UI có thể chưa đẹp). Toàn bộ test pass.

### Ngày 2 — One đồng bộ thật + web hiển thị One

| # | Việc | Đầu ra | Ước lượng | Hoàn thành khi |
|---|---|---|---|---|
| 2.1 | `exam_monitor/hub_client.py` + outbox SQLite + worker nền (FR-ONE-02) | Module + test với Hub giả | 3 h | Test mất mạng / nối lại; không trùng |
| 2.2 | Gắn vào `app.py`: mở phiên, gửi sự kiện / ảnh / bookmark / heartbeat / stop; trang Cài đặt ghép nối; chỉ báo đồng bộ (FR-ONE-01, 03, 04, 05) | `app.py` | 3 h | Phiên One thật hiện trên web |
| 2.3 | Chuẩn hóa review enum + đọc phiên cũ (FR-ONE-06) | `models.py`, `events.py`, `app.py` | 1 h | 24 phiên cũ mở được |
| 2.4 | Web: bộ lọc sản phẩm + cột Thí sinh/Ghế trong `/review`; ô One trên `/wall` (FR-HUB-08, 09) | `dashboard/review.html`, `dashboard/wall.html` + JS | 2,5 h | Thumbnail live, trạng thái mất kết nối |
| 2.5 | Đổi nhãn VIGIL One + Việt hóa (FR-ONE-07) | `app.py`, `theme.py`, assets | 1 h | Ảnh chụp các trang không còn "Local" |

**Mốc cuối ngày:** laptop chạy One (webcam thật) → giám thị trên trình duyệt thấy thí sinh live trên `/wall`, nhận tín hiệu và bookmark trong `/review`, ra quyết định.

### Ngày 3 — Báo cáo, tập dượt, đóng băng

| # | Việc | Đầu ra | Ước lượng | Hoàn thành khi |
|---|---|---|---|---|
| 3.1 | Báo cáo ca thi và xem lại cho One (FR-HUB-10, 11) | `reports`, `playback` | 3 h | Duyệt được báo cáo phiên One, có SHA-256 |
| 3.2 | Kiểm thử end-to-end trên **máy demo đích**: webcam, micro, Wi-Fi, 1 One thật + 2 One mô phỏng + 1 phòng Classroom | Biên bản kiểm thử, ảnh chụp | 2 h | Checklist §11 đạt |
| 3.3 | Kiểm thử NFR: rút mạng 60 giây, đo FPS/CPU, Hub khởi động lại giữa phiên | Số đo | 1 h | NFR-01, 02, 04 đạt |
| 3.4 | Sửa lỗi phát sinh; rà Phụ lục A (FR-CLS-03) | Commit sửa lỗi | 2 h | Không còn lỗi chặn demo |
| 3.5 | Cập nhật `docs/DEMO_RUNBOOK.md` (khởi động Hub, ghép nối, kịch bản, phương án dự phòng); **đóng băng code** cuối ngày | Runbook | 1 h | Người khác chạy được theo runbook |

### Phương án cắt giảm nếu chậm tiến độ

| Nếu đến | Còn thiếu | Cắt |
|---|---|---|
| Cuối Ngày 1 | API chưa xong | Bỏ ghép nối bằng mã: dùng một token tĩnh trong `.env` cho demo (vẫn bắt buộc có token) |
| Giữa Ngày 2 | Outbox chưa ổn | Gửi trực tiếp, kèm nút "Đồng bộ lại" thủ công đọc từ JSON phiên cục bộ |
| Cuối Ngày 2 | Thumbnail `/wall` chưa xong | Ô One chỉ hiện chỉ số và trạng thái, không ảnh |
| Ngày 3 | Báo cáo One chưa xong | Demo báo cáo bằng ca Classroom; với One dùng `/review` + xuất CSV |

---

## 9. Kịch bản demo đề xuất (≈ 10 phút)

1. **Mở đầu (1 phút):** một nền tảng VIGIL, hai cấu hình (Classroom cho phòng thi, One cho thi trên máy tính); AI chỉ đưa tín hiệu, giám thị quyết định.
2. **Thiết lập (1 phút):** CHIEF đăng nhập, mở kỳ thi demo, tạo mã ghép nối; laptop thí sinh nhập mã → "Đã kết nối VIGIL Hub".
3. **Giám sát (3 phút):** `/wall` hiện phòng Classroom (video phân tích) và ô thí sinh One live. Thí sinh nhìn ra ngoài và cầm điện thoại → tín hiệu lên `/review` sau vài giây. Giám thị phòng bấm Ctrl+B đánh dấu một khung hình.
4. **Rà soát (3 phút):** trong `/review`, lọc theo One và Classroom; mở ảnh/clip bằng chứng; ra quyết định có lý do; phân biệt bookmark thủ công với tín hiệu AI.
5. **Báo cáo (1,5 phút):** duyệt báo cáo ca thi, in biên bản, chỉ ra SHA-256 và phiên bản.
6. **Độ bền (0,5 phút, tùy chọn):** tắt Wi-Fi laptop thí sinh → One báo "Đang chờ N mục" → bật lại → dữ liệu lên đủ.

**Phương án dự phòng:**
- Mất mạng hội trường: chạy Hub và One trên cùng laptop (`localhost`).
- Webcam lỗi: One chế độ mô phỏng (có nhãn rõ).
- Pipeline Classroom chậm: chuyển sang replay preset đã phân tích.
- Mọi thứ hỏng: video demo đã render (`videos/vigil-ai-promo/renders/vigil-promo-v6-final2-*.mp4`).

---

## 10. Rủi ro và giảm thiểu

| Rủi ro | Khả năng | Ảnh hưởng | Giảm thiểu |
|---|---|---|---|
| Tích hợp One mất nhiều thời gian hơn dự kiến | Trung bình | Cao | Kế hoạch cắt giảm §8; Ngày 1 có script client giả để làm web song song |
| Thread Tk + HTTP gây treo UI | Trung bình | Cao | Mọi I/O mạng ở worker riêng, hàng đợi có giới hạn, timeout 5 giây; test với Hub chậm |
| Migration làm hỏng DB thật | Thấp | Cao | Demo dùng DB riêng; sao lưu trước; chỉ `ADD COLUMN` |
| Wi-Fi hội trường chặn cổng hoặc client isolation | Trung bình | Cao | Mang router riêng hoặc chạy cùng máy; kiểm tra tại chỗ ngày 3 |
| Tín hiệu sai trước khán giả (false positive) | Cao | Trung bình | Kịch bản có chủ đích; ngôn từ "ưu tiên rà soát"; nêu giới hạn độ chính xác khi được hỏi |
| Máy demo thiếu CPU khi chạy Classroom + One cùng lúc | Trung bình | Trung bình | Classroom dùng replay khi One chạy thật; đo ở việc 3.3 |
| Lệch tên sản phẩm giữa tài liệu, web và app | Cao | Thấp | FR-ONE-07, FR-CLS-04; rà ảnh chụp màn hình trước khi đóng băng |

---

## 11. Checklist nghiệm thu demo

**Hub / Classroom**
- [ ] `pytest` toàn bộ pass (≥ 369 test cũ + test mới cho `/one/*`).
- [ ] `start_vigil.bat` mở được web trên máy demo; đăng nhập ADMIN/CHIEF/PROCTOR.
- [ ] Dashboard chỉ hiện dữ liệu kịch bản; không còn hàng trăm ca `INTERRUPTED`.
- [ ] Một ca Classroom chạy, sinh incident, review, duyệt báo cáo, in biên bản.

**VIGIL One ↔ Hub**
- [ ] Ghép nối bằng mã; thu hồi token thì client bị từ chối.
- [ ] Phiên One xuất hiện trên `/sessions` và `/wall` với thumbnail live, nhãn "MÔ PHỎNG" nếu mô phỏng.
- [ ] Tín hiệu One vào `/review` ≤ 3 giây, có ảnh bằng chứng và SHA-256 khớp.
- [ ] Bookmark Ctrl+B trên One hiện là "Đánh dấu thủ công", không có điểm AI.
- [ ] Rút mạng 60 giây rồi nối lại: không mất, không trùng dữ liệu.
- [ ] Không có Hub: One vẫn chạy và lưu cục bộ như trước.
- [ ] Giao diện One không còn "Local", không còn nhãn tiếng Anh lẻ.
- [ ] Báo cáo ca thi One duyệt được (nếu FR-HUB-10 kịp).

**Trình bày**
- [ ] Kịch bản §9 chạy trọn dưới 10 phút, hai lần liên tiếp không lỗi.
- [ ] Runbook cập nhật; phương án dự phòng đã thử.

---

## 12. Quyết định cần chủ dự án

1. **Tên hạ tầng chung trên giao diện:** "VIGIL Hub", hay giữ "VIGIL Classroom" cho web và gọi One là "client"?
2. **Kịch bản demo có cần One chạy trên máy khác qua mạng không**, hay chạy cùng laptop là đủ? (ảnh hưởng chuẩn bị mạng)
3. **Có hiện thumbnail live của thí sinh One trên web không** (riêng tư)? Mặc định đề xuất: có, chỉ trong bộ nhớ, tắt được.
4. **Dữ liệu cũ trong `data/vigil_proctoring.db`:** dùng DB demo riêng (đề xuất), hay dọn trực tiếp DB hiện tại?
5. **Ngôn ngữ web:** giữ tiếng Anh cho giao diện như quyết định ngày 2026-10-09, hay Việt hóa các màn hình xuất hiện trong demo?
6. **Nền nhánh:** tách nhánh tính năng từ `DEV` hay từ `feat/ux-business-redesign` (đang có các commit GĐ1–GĐ2 chưa merge vào `DEV`)?

---

## Phụ lục A — Các thay đổi chưa commit trong working tree lúc rà soát

- Đã sửa: `.gitignore`; 13 trang `dashboard/*.html`; 6 ảnh `dashboard/assets/brand/*` (logo mới).
- Chưa track:
  - `assets/` (logo, nhạc video);
  - `docs/BAO_CAO_BAN_GIAO_VIGIL.md`, `docs/MO_TA_SAN_PHAM_VIGIL_AI.md`;
  - 3 file `reports/proctor-review-*.json`.

Cần xem xét và commit (hoặc loại) trước khi tách nhánh tính năng. Không dùng `git add .`.

---

## Phụ lục B — Trạng thái triển khai (nhánh `feat/vigil-one-hub`, cập nhật 2026-10-13)

Quyết định §12 dùng theo mặc định đề xuất: tên "VIGIL Hub", có thumbnail live (chỉ trong bộ nhớ, tắt được), DB demo riêng, giữ giao diện web tiếng Anh, nhánh tách từ `DEV`.

| Yêu cầu | Trạng thái | Nơi thực hiện / kiểm chứng |
|---|---|---|
| Mô hình dữ liệu (§6.1) | Xong | `storage/db_models.py`, migration `storage/database.py`; chạy trên bản sao DB thật không mất dữ liệu |
| FR-HUB-01, 12, 13 ghép nối, thu hồi, token | Xong | `storage/one_service.py`, `api/routes/one.py`, UI `/admin`; test `tests/test_one_hub.py` |
| FR-HUB-02…07 phiên, sự kiện, ảnh, bookmark, nhịp, kết thúc | Xong | idempotent theo `client_session_id` / `event_id` / `request_id`; SHA-256 kiểm tra phía Hub |
| FR-HUB-08 hàng đợi đa sản phẩm | Xong | API `product=`, bộ lọc trên `/review`, nhãn tiếng Việt của tín hiệu One |
| FR-HUB-09 video wall | Xong | nguồn "VIGIL One · candidate #k (auto)" trên `/wall` |
| FR-HUB-10 báo cáo One | Xong | mục Thí sinh trên `/reports` và bản in; luồng duyệt/đóng băng dùng chung |
| FR-HUB-11 xem lại One | Một phần | API playback trả timeline và ảnh; trang `/playback` chưa có giao diện riêng cho phiên không có video |
| FR-ONE-01…07, 09 | Xong | `exam_monitor/hub_client.py` (outbox SQLite, retry, đóng app gửi nốt), `exam_monitor/app.py`; test `tests/test_one_hub_client.py` |
| FR-ONE-08 quyết định review đồng bộ hai chiều | Chưa | quyết định thực hiện trên web Hub |
| FR-CLS-01 seed demo | Xong | `scripts/seed_demo.py` |
| FR-CLS-02 dọn dữ liệu | Thay bằng DB demo riêng | xem `docs/DEMO_RUNBOOK_VIGIL_HUB.md` |
| FR-CLS-03…05 | Chưa | — |
| Script client mô phỏng | Xong | `scripts/one_fake_client.py` |
| Runbook | Xong | `docs/DEMO_RUNBOOK_VIGIL_HUB.md` |

Lỗi phát hiện và đã sửa trong quá trình làm:

- Khởi động lại server từng đóng nhầm phiên One đang chạy: `storage/session_lifecycle.py`.
- "Remove test rooms" từng coi phòng ảo `VIGIL-ONE` là phòng test và sẽ xóa dữ liệu One: `storage/maintenance.py`.
- Hàng đợi từng trả `video_url` cho bằng chứng dạng ảnh JPEG.

Kiểm chứng:
- 390/390 test pass (thêm 21 test mới).
- Chạy thật app desktop VIGIL One (nguồn mô phỏng) với Hub: 3 tín hiệu kèm ảnh `READY` và SHA-256, phiên `COMPLETED`, outbox về 0.
- `/review`, `/wall`, `/admin` đã kiểm trên trình duyệt.

Chưa kiểm:
- Webcam và micro thật.
- Hai máy qua mạng LAN.
- Rút mạng thật trên app desktop (đã kiểm bằng test giả lập lỗi mạng).

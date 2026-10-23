# Báo cáo khảo sát VIGIL AI: kiến trúc, công nghệ, logic, pipeline, giao diện

**Ngày khảo sát:** 2026-10-24.
**Phạm vi:** nhánh `feat/one-server-analysis` (commit `77d37ad`) cùng các thay đổi chưa commit của đợt 18–19/10. Các thay đổi đó gồm: lõi hướng nhìn mới, debug overlay, sửa Playback, Wall và báo cáo.
**Cách làm:**
- Đọc mã nguồn và lập đồ thị import bằng AST từ các điểm vào thật (`vigil.py`, `server.py` → `api.main`, `exam_monitor/candidate_app.py`).
- Chạy Hub ở môi trường quay riêng và xem từng trang web.
- Đối chiếu với `docs/BAN_GIAO_HIEN_TRANG.md` và `docs/KE_HOACH_TOI_UU_HIEU_NANG.md`.
- Dùng các quan sát thực tế khi quay video giới thiệu.

Mức độ ưu tiên dùng trong báo cáo:
- **P0:** chặn triển khai thật.
- **P1:** ảnh hưởng chất lượng, an toàn hoặc độ tin cậy.
- **P2:** nợ kỹ thuật, độ gọn gàng.

---

## 1. Tóm tắt

VIGIL AI đã là một **nguyên mẫu hoàn chỉnh để trình diễn**. Có một Hub chung, hai sản phẩm, hàng đợi rà soát thống nhất, bằng chứng có SHA-256, nguyên tắc "AI đánh dấu, con người quyết định" được giữ xuyên suốt, và 523 test tự động đều đạt. VIGIL One gần sẵn sàng thí điểm thật.

Tuy nhiên, **VIGIL Classroom chưa triển khai được trong phòng thi thật**, vì ba lý do:
- Phân tích LIVE chỉ đọc **file video mẫu**, chưa nhận camera thật.
- Mỗi lần chỉ chạy được **một phòng**.
- Quy tắc "nhiều người gần ghế" **bắt nhầm giáo viên** đi tuần.

Bên cạnh đó có một số khoảng trống nền tảng: chưa có chính sách xóa dữ liệu, xác thực mặc định đang tắt, Docker/Postgres hỏng, giao diện tiếng Anh cho người dùng Việt.

**10 việc nên làm trước tiên**

| # | Việc | Mức | Mảng |
|---|---|---|---|
| 1 | Classroom nhận camera thật (RTSP/webcam), chạy liên tục, không dừng ở cuối file | P0 | Kiến trúc |
| 2 | Classroom chạy nhiều phòng song song: bỏ singleton `DemoRuntime`, mỗi phòng một worker | P0 | Kiến trúc |
| 3 | Sửa bắt nhầm giáo viên và ghế chồng lấn trong `MULTI_PERSON_DWELL_NEAR_SEAT` | P0 | Logic AI |
| 4 | Chính sách lưu trữ và xóa bằng chứng, video theo thời hạn; ghi rõ trong đồng ý của thí sinh | P0 | Dữ liệu, quyền riêng tư |
| 5 | Mặc định `VIGIL_AUTH_MODE=required` khi mở ra mạng; kiểm tra vai trò ở mọi API ghi | P0 | Bảo mật |
| 6 | Tự đóng phiên One bị bỏ dở (hết hạn chờ → đóng, ghép video) | P1 | Pipeline |
| 7 | Việt hóa giao diện web và nhãn hành vi Classroom; thêm lớp i18n | P1 | Giao diện |
| 8 | Sửa đường triển khai: Docker chạy được (SQLite hoặc Postgres thật), không ghi mật khẩu cứng, có GPU | P1 | Vận hành |
| 9 | Migration CSDL có phiên bản (Alembic), dọn dữ liệu camera trùng | P1 | Dữ liệu |
| 10 | CI chạy test và lint; bộ đo độ chính xác cho VIGIL One | P1 | Chất lượng |

---

## 2. Kiến trúc hiện tại

```
                   ┌──────────────── VIGIL Hub (1 tiến trình FastAPI, SQLite WAL) ─────────────────┐
 Camera phòng thi  │ Classroom LIVE: DemoRuntime (singleton, 1 luồng)                               │
 (hiện: file .mp4) ├─► YOLO11-Pose → gán ghế (Seat ROI) → SixDRepNet → episode → pattern → rủi ro      │
                   │    → bằng chứng (clip 10 s, SHA-256) → detection_events                          │
 Máy thí sinh      │ One: WebSocket JPEG ~10 fps + đoạn H.264 10 s → OneAnalysisService (2 worker)    │
 (VIGIL One app)  ─┼─► MediaPipe FaceMesh + iris, YOLO vật thể → EventDetector → tín hiệu + clip         │
                   │ API /api/v1/* · WebSocket /ws/events · Dashboard tĩnh (Tailwind dựng sẵn)        │
                   └───────► Review queue chung · Playback · Wall · Báo cáo · Quản trị                ┘
```

**Quy mô mã:** khoảng 50 nghìn dòng.

| Phần | Số dòng |
|---|---|
| Python | ~36 nghìn: `api` 5,6k, `storage` 7,4k, `classroom_monitor` 13k, `exam_monitor` 5,9k |
| JS/HTML | ~11,4k |
| Test (46 file, 523 test) | 12,6k |

**Mã không còn nằm trên luồng chạy** (không đi tới được từ điểm vào sản phẩm), khoảng 4.500 dòng:
- `exam_monitor/app.py` (2.273 dòng): app desktop cũ chạy qua `main.py`, có chế độ tự phân tích đã bỏ.
- `classroom_monitor/worker_node.py`, `rtsp_reader.py`, `behavior_signals.py`: hướng worker phân tán qua RTSP, làm dở.
- `eyes.py`, `main.py`, `classroom_monitor/__main__.py`.

**Hai pipeline Classroom cùng tồn tại:**
- **SRS v2** (`classroom_monitor/pipeline/srs_v2_pipeline.py`) được dùng cho LIVE.
- **Pipeline cũ** (`video_processor`, `event_engine`, `behavior_tracker`, `score_accumulator`, `live_event`, `room_context`) vẫn được nạp qua `api/routes/inference.py` (`/inference/start`, `/inference/upload`). Giao diện không còn gọi tới các endpoint này, và chúng **đã bị tắt mặc định** (trả 410 trừ khi đặt `VIGIL_ENABLE_LEGACY_INFERENCE=1`). Rủi ro thấp, chỉ còn là mã thừa.

---

## 3. Đánh giá theo mảng

### 3.1 Kiến trúc

**Điểm mạnh**
- **Một Hub cho cả hai sản phẩm:** chung hàng đợi, chung bằng chứng, chung báo cáo. Có nút chuyển sản phẩm.
- **VIGIL One "máy thí sinh chỉ quay, Hub phân tích":**
  - Có luồng trực tiếp kèm đoạn video ghi bù.
  - Phân tích bù khoảng trống, chia worker theo phiên.
  - Video cả ca được ghép bằng `ffmpeg -c copy`.
  - Ranh giới tin cậy đã được gia cố (`tests/test_trust_boundary.py`).
- **Một lệnh khởi động** (`vigil.py`) có dấu vân tay bản build và tự tắt Hub cũ.

**Vấn đề**

| Mức | Vấn đề | Bằng chứng | Đề xuất |
|---|---|---|---|
| P0 | **Classroom chỉ đọc file video.** Bảng `cameras` có `source_uri` (RTSP hoặc chỉ số thiết bị) nhưng LIVE không dùng. Hết file là phiên kết thúc. | `classroom_monitor/demo/runtime.py:744` `cv2.VideoCapture(config.video_path)`; `classroom_monitor/demo/config.py:66` | Nguồn hình theo camera trong CSDL: RTSP có tự kết nối lại, webcam, file. Chế độ chạy liên tục đến khi giám thị dừng. Tái dùng `rtsp_reader.py` vì đã có sẵn. |
| P0 | **Một phòng tại một thời điểm.** `DemoRuntime` là singleton, nên Video Wall chỉ có một ô LIVE thật. | `runtime.py:149-158` `_instance` / `get_instance()` | Bộ quản lý `RoomRuntime` theo `room_id`, mỗi phòng một worker hoặc tiến trình riêng. GPU dùng chung, có hàng đợi lô, theo `KE_HOACH_TOI_UU_HIEU_NANG.md`. |
| P1 | Phiên Classroom tạo từ trang Live monitor có tên "VIGIL AI LIVE Demo (…) - DEMO-…" và không gắn kỳ thi hay giám thị. Báo cáo vì vậy hiện "Kỳ thi —", "Giám thị —". | `runtime.py:406` `_init_db_session`; trang Reports | Chỉ cho chạy LIVE từ một phiên thi đã lên lịch (`/exam-sessions/{id}/start` đã có). Trang Live monitor chọn phiên, không tạo phiên lẻ. |
| P1 | Chữ "Demo" lẫn vào sản phẩm: `/demo`, `/api/v1/demo/*`, `DemoRuntime`, `demo_runs`, `demo_final`. | `api/routes/demo.py`, `classroom_monitor/demo/` | Đổi tên dần sang `classroom/live`, giữ đường dẫn cũ làm alias một thời gian. |
| P2 | Mỗi router được gắn 2–3 lần: gốc `/` (riêng `demo`), `/api/v1`, `/api`. | `api/main.py` (vòng lặp `for prefix in ["/api/v1", "/api"]`) | Chỉ giữ `/api/v1`. `/api` để lại 1 phiên bản rồi bỏ. |
| P2 | Pipeline Classroom cũ và mã chết, khoảng 4.500 dòng (mục 2). | đồ thị import | Gỡ bỏ, hoặc chuyển vào `legacy/` kèm ghi chú. |

### 3.2 Công nghệ

| Thành phần | Hiện tại | Nhận xét |
|---|---|---|
| Web/API | FastAPI, Uvicorn, SQLAlchemy | Phù hợp. Một tiến trình nên không mở rộng ngang được (state trong RAM: `one_live`, `DemoRuntime`). |
| CSDL | SQLite WAL | Ổn cho 1 Hub và vài chục người dùng. Migration viết tay bằng `ALTER TABLE` (12 khối), không có phiên bản. |
| Classroom AI | YOLO11-Pose 1280 FP16 + SixDRepNet FP16 | Đúng hướng. Đo trên RTX 4060: 15,9 khung phân tích/s, kịp thời gian thực cho 1 phòng. Muốn nhiều phòng cần TensorRT và tiền xử lý trên GPU. |
| One AI | MediaPipe FaceMesh (iris) + YOLO vật thể + VAD | Hướng nhìn dọc vừa được sửa (khóe mắt và độ mở mắt). Còn phụ thuộc hiệu chuẩn đầu ca. |
| Giao diện | HTML và JS thuần, Tailwind dựng sẵn | Nhẹ, chạy không cần Internet. Khoảng 8.400 dòng JS **không có test**, không có lớp i18n. |
| App thí sinh | Tkinter, PyInstaller (`dist/VIGIL-One-2.0.0-win64.zip`) | Gọn. Chỉ có bản Windows. |

**Vấn đề**

| Mức | Vấn đề | Bằng chứng | Đề xuất |
|---|---|---|---|
| P1 | **Docker/Postgres không chạy được.** `docker-compose.yml` dùng Postgres, nhưng `init_db` truy vấn `sqlite_master` và dùng `PRAGMA` rồi `raise` khi lỗi. | `storage/database.py:66,198`; `docker-compose.yml:32` | Hoặc chính thức chỉ hỗ trợ SQLite (bỏ Postgres khỏi compose), hoặc chuyển sang Alembic để chạy được cả hai. |
| P1 | Mật khẩu CSDL ghi cứng trong compose. Image `python:3.11-slim` chỉ có CPU, không dùng được GPU. | `docker-compose.yml:32`; `Dockerfile` | Đọc từ `.env`. Image CUDA riêng cho Hub có GPU. |
| P1 | `requirements.txt` là `pip freeze` mã hóa UTF-16. Cài cả `opencv-python` lẫn `opencv-contrib-python` (hai gói xung đột) và nhiều gói không cần (`jax`, `polars`…). | `requirements.txt` | Tách `requirements.in` (gói trực tiếp) và file khóa phiên bản UTF-8. Chỉ giữ một gói OpenCV. |
| P2 | Hub không tự phục vụ TLS. App thí sinh đã bắt buộc `https://` khi ở ngoài mạng nội bộ. | `exam_monitor/hub_client.py:90-104` | Tài liệu hóa reverse proxy (Caddy hoặc Nginx), hoặc thêm tùy chọn `--ssl-certfile`. |

### 3.3 Logic AI

**VIGIL Classroom**

| Mức | Vấn đề | Bằng chứng | Đề xuất |
|---|---|---|---|
| P0 | **Bắt nhầm giáo viên.** `MULTI_PERSON_DWELL_NEAR_SEAT` đếm mọi người trong ROI ghế. Bộ lọc `passers_by` chỉ loại người *đang đi*; giáo viên *dừng lại* cạnh bàn vẫn bị tính là "người thứ hai". Đã thấy trên cả 3 phòng, cả phát lại lẫn LIVE (khi quay video: S09, S11, S04, S16). | `classroom_monitor/seat_manager.py:338-386`; `behavior_pattern_engine.py:515` | Loại **người lớn đang đứng**: khung cao hơn hẳn thí sinh đang ngồi cùng ghế, hoặc dùng keypoint hông và gối. Giữ người đó là `roaming` hoặc giám thị. Có thể đánh dấu giám thị theo vị trí lối đi. Cần test hồi quy trên video có giáo viên. |
| P1 | **ROI ghế chồng lấn.** Trang hiệu chuẩn tự cảnh báo, ví dụ "S01 và S03 chồng 55%, có thể gây cảnh báo nhiều người", nhưng vẫn cho lưu. | trang `/calibration`, mục Room check | Không cho lưu khi chồng quá ngưỡng, hoặc tự cắt phần giao. Gán người theo điểm neo gần tâm ghế nhất thay vì "nằm trong ROI". |
| P1 | Độ chính xác chưa đủ để công bố: precision trên tập India **2,58%** (bàn giao cũ). Hiện chỉ dùng được để xếp ưu tiên rà soát. | `docs/BAO_CAO_BAN_GIAO_VIGIL.md` | Đóng băng ground truth cho 3 phòng (đã có `scripts/freeze_ground_truth.py`, `benchmark_temporal_ground_truth.py`). Đo precision và recall theo từng pattern sau mỗi thay đổi. |
| P1 | Dựng phòng tốn công: mỗi phòng phải vẽ tay khoảng 33 đa giác ghế. Camera xê dịch là ROI lệch. | `/calibration` | Gợi ý ROI tự động từ người ngồi phát hiện ở 1–2 phút đầu. Kiểm tra lệch camera bằng ảnh tham chiếu. |

**VIGIL One**

| Mức | Vấn đề | Bằng chứng | Đề xuất |
|---|---|---|---|
| ✅ | Hướng nhìn dọc từng sai (nhìn xuống báo "lên") vì đo theo mí mắt. **Đã sửa** sang khóe mắt và độ mở mắt. Hiệu chuẩn cũ không bị dùng lại. | `exam_monitor/engine.py` (`VERTICAL_MEASURE = 2`), `tests/test_core.py` | Commit cùng đợt này. |
| P1 | **Chưa có bộ đo độ chính xác** cho hướng nhìn và các tín hiệu (nhìn ra ngoài, quay đầu, nói chuyện, vật thể). Ngưỡng hiện được đặt theo cảm nhận. | không có script hay tập dữ liệu đo nào cho One | Ghi 20–30 phiên có kịch bản (nhìn trái/phải/xuống, có người, có điện thoại) và gắn nhãn theo giây. Đo precision và recall; tinh chỉnh `VERTICAL_*` và các ngưỡng thời gian. |
| P1 | Phụ thuộc hiệu chuẩn đầu ca. Thí sinh nhìn lệch lúc hiệu chuẩn thì cả ca sai. Ngưỡng vùng an toàn ±0,30 / ±0,34 cố định cho mọi người. | `engine.py` `_maybe_collect_calibration`, `_eye_zone_score` | Hiệu chuẩn có hướng dẫn (chấm giữa màn hình, kiểm tra độ ổn định); cho giám thị bấm hiệu chuẩn lại (đã có nút). Ngưỡng điều chỉnh theo khoảng cách mặt. |
| P2 | Debug trace (`trace.jsonl`) ghi mỗi khung, khoảng 7 MB cho 2 giờ ở 10 fps, chưa có xoay vòng hay xóa. | `storage/analysis_trace.py` | Đưa vào chính sách lưu trữ (mục 3.5). |

### 3.4 Pipeline và dữ liệu

| Mức | Vấn đề | Bằng chứng | Đề xuất |
|---|---|---|---|
| P1 | **Phiên One bị bỏ dở không bao giờ đóng.** Phiên chỉ thành `INTERRUPTED` khi khởi động lại server, mà danh sách "đang thi" vẫn tính trạng thái này. Phiên nằm mãi ở mục LOST, video không được ghép. | `storage/session_lifecycle.py:60-74`; `api/routes/one.py` `active_only` gồm `INTERRUPTED`; trang Operations | Hết hạn chờ (ví dụ 10 phút không có kết nối) thì đóng phiên, ghép video, tạo tín hiệu "mất kết nối kéo dài". Giám thị có nút "Đóng phiên". |
| P1 | **Camera bị nhân bản trong CSDL:** 56 camera cho 7 phòng (ROOM-101 có 24 bản trùng nguồn). Cùng một video thành 2 bản ghi chỉ vì `demo_video/` và `demo_video\`. Có cả trong CSDL thật. | `runtime.py:531` tạo `Camera(source_uri=str(path))` mỗi lần chạy | Tìm camera theo `room_id` và đường dẫn chuẩn hóa (`as_posix`) trước khi tạo. Có script dọn trùng (chuyển `seats.camera_id` về một bản). |
| P1 | Migration CSDL không có phiên bản: 12 khối `ALTER TABLE` chạy mỗi lần khởi động, khó lùi bản, khó biết schema hiện tại. | `storage/database.py:58-199` | Alembic, với một bản baseline từ schema hiện tại. |
| P2 | Gói phát lại `data/demo_final` bị ghi đè sau mỗi lượt LIVE chạy trọn vẹn. Nếu không đặt `VIGIL_DEMO_FINAL_ROOT`, một Hub thử nghiệm sẽ ghi đè dữ liệu thật (đã xảy ra khi quay video). | `classroom_monitor/demo/paths.py`; `runtime.py` (publish replay) | Chỉ tạo gói phát lại khi người dùng chủ động bấm "Lưu làm bản phát lại". |
| P2 | Log chỉ dùng `basicConfig` ra stdout, ở 2 nơi. Không ghi file xoay vòng, không có số đo vận hành. | `server.py:24`, `api/main.py:54` | Log có cấu trúc kèm file xoay vòng. Endpoint `/metrics` (fps theo phòng, độ trễ phân tích, hàng đợi One). |

### 3.5 Bảo mật và quyền riêng tư

**Điểm mạnh**
- Token thiết bị cho VIGIL One. Token demo cho LAN.
- Chặn path traversal ở các endpoint trả file (`cameras.py:184`, `demo.py` `_contained_persisted_evidence`).
- Upload có giới hạn kích thước và đuôi file.
- Mật khẩu băm bcrypt. Thao tác phá hủy chỉ ADMIN được làm và có sao lưu SQLite trước.

| Mức | Vấn đề | Bằng chứng | Đề xuất |
|---|---|---|---|
| P0 | **Không có thời hạn lưu dữ liệu.** `cleanup_old_evidence` không được gọi ở đâu, và chỉ xóa `.jpg`. Clip `.mp4`, video cả ca của thí sinh (có khuôn mặt, có thể có giọng nói) và trace được giữ vô thời hạn. Dữ liệu này là dữ liệu cá nhân, cần thời hạn và mục đích rõ ràng (tham chiếu Nghị định 13/2023/NĐ-CP về bảo vệ dữ liệu cá nhân). | `storage/evidence_store.py:60-71` | Thời hạn theo kỳ thi, ví dụ xóa 90 ngày sau khi duyệt báo cáo, trừ bằng chứng của vi phạm đã xác nhận. Tác vụ dọn định kỳ có nhật ký. Ghi thời hạn vào màn hình đồng ý của thí sinh. |
| P0 | **Xác thực mặc định tắt** (`VIGIL_AUTH_MODE=optional`). Không đăng nhập vẫn xem được hàng đợi, bằng chứng, video thí sinh. Trên LAN chỉ có một token demo dùng chung, nằm trong cookie hoặc `?token=`. | `api/auth.py:29`; `api/main.py:167-184` | `--lan` tự bật `required`. Token demo chỉ cho trình diễn. Cookie token demo cần thêm `secure` khi chạy HTTPS; cookie phiên đăng nhập đã có (`api/routes/auth.py:63`). |
| P1 | **API ghi không kiểm tra vai trò:** camera, ghế, upload ảnh tham chiếu. Bất kỳ ai đăng nhập (ở chế độ `required`), hay bất kỳ ai (ở chế độ `optional`), cũng sửa được Seat ROI. | `api/routes/cameras.py`, `api/routes/seats.py` (không có `require_roles`) | `require_roles("ADMIN","CHIEF", always=True)` cho mọi API ghi cấu hình. Test phân quyền cho từng router. |
| P2 | Không có giới hạn tốc độ cho đăng nhập và tham gia kỳ thi (PIN 4 số). | `api/routes/auth.py`, `api/routes/one.py` (join) | Giới hạn số lần thử theo IP và theo mã thí sinh; khóa tạm sau N lần sai. |

### 3.6 Giao diện và trải nghiệm

**Điểm mạnh**
- Hệ thống thiết kế thống nhất, tối và sáng, đánh số trang.
- Hàng đợi rà soát có phím tắt, tô khung thí sinh, kiểm tra SHA-256, bắt buộc ghi lý do.
- Video wall, Playback với timeline theo ghế, video cả ca One.
- Chế độ debug (hướng nhìn, bbox) tách khỏi giao diện thường.

| Mức | Vấn đề | Bằng chứng | Đề xuất |
|---|---|---|---|
| P1 | **Lẫn ngôn ngữ.** Web gần như toàn tiếng Anh (Operations, Review queue, Confirm/Reject…). App thí sinh, biên bản in, lỗi One và nhãn tín hiệu One lại là tiếng Việt; nhãn hành vi Classroom tiếng Anh ("Heads Together"). Cùng một hàng đợi trộn hai ngôn ngữ. | đo trên `dashboard/*.html`: 0–5% từ tiếng Việt | Lớp i18n nhỏ (bảng `vi`/`en` trong JS và nhãn từ API). Mặc định tiếng Việt. Việt hóa nhãn pattern Classroom. |
| P1 | Trang Live monitor (`/demo`) vẫn mang dáng công cụ trình diễn: chọn "Classroom 01/02/03" theo file, ô Debug và Labels nằm cạnh nút Start. | `dashboard/demo.html` | Sau khi có camera thật: chọn phòng hoặc phiên thi; giấu công cụ kỹ thuật sau debug mode. |
| P1 | Kỳ thi hiện "no date" hàng loạt. Trang Kỳ thi & phiên thi chưa có lịch hay bộ lọc theo ngày hoặc trạng thái. | `/sessions` | Bắt buộc ngày giờ khi tạo kỳ thi; lọc Hôm nay / Sắp tới / Đã đóng. |
| P2 | Chữ debug và FPS từng dính vào màn hình chính; đã chuyển sau `vigil-debug`. Còn sót ở `online.js:254` và `one-monitor.js:77` (FPS trong debug, đúng ý đồ). | | Giữ nguyên quy ước: thông số kỹ thuật chỉ hiện ở debug. |
| P2 | Màn hình hẹp: sidebar thu thành số (01, 02…) không có nhãn; một số bảng tràn ngang. | ảnh chụp `/` ở 800 px | Tooltip cho mục sidebar thu gọn; bảng cuộn ngang trong khung. |

### 3.7 Chất lượng mã và kiểm thử

| Mức | Vấn đề | Đề xuất |
|---|---|---|
| P1 | **Không có CI.** 523 test chỉ chạy tay. | GitHub Actions: pytest (CPU), ruff, kiểm tra cú pháp JS (`node --check`) cho mỗi PR. |
| P1 | ~8.400 dòng JS giao diện không có test. Các lỗi gần đây đều nằm ở JS: ô Wall sai thí sinh, timeline giãn ngang, tràn mã ở Review. | Playwright: vài kịch bản khói (Review, Wall, Playback, One video) chạy trên Hub test. |
| P2 | ruff báo ~200 lỗi cũ, chủ yếu import thừa. `exam_monitor/app.py` 2.273 dòng không còn dùng. | Dọn theo từng thư mục; gỡ mã chết (mục 2). |
| P2 | Nhiều file rất lớn: `runtime.py` 1.366 dòng, `repositories.py` 1.380, `data_workbench.js` 2.178. | Tách theo trách nhiệm khi làm mục 1–2 (nguồn hình, bộ quản lý phòng). |

---

## 4. Lộ trình đề xuất

**Giai đoạn A: An toàn và nền tảng (1–2 tuần)** — *đã làm trên nhánh `feat/phase-a-foundation` (2026-10-24), xem mục 6*
- Thời hạn lưu dữ liệu và tác vụ dọn (P0).
- Bật xác thực khi mở mạng, kiểm tra vai trò ở API ghi (P0).
- Tự đóng phiên One bỏ dở (P1).
- Dọn camera trùng, chuẩn hóa đường dẫn (P1).
- CI: pytest, ruff, `node --check` (P1).
- Commit và PR các thay đổi của đợt 18–19/10.

**Giai đoạn B: Classroom dùng được trong phòng thi thật (2–4 tuần)**
- Nguồn hình theo camera: RTSP có tự kết nối lại, webcam, chạy liên tục (P0).
- Bộ quản lý phòng: nhiều phòng song song, Wall có nhiều ô LIVE thật (P0).
- Sửa bắt nhầm giáo viên, chặn ROI chồng lấn (P0, P1).
- Đo lại độ chính xác trên ground truth 3 phòng.
- LIVE chỉ chạy từ phiên thi đã lên lịch; bỏ phiên "Demo" lẻ (P1).

**Giai đoạn C: Chất lượng AI và trải nghiệm (song song với B)**
- Bộ dữ liệu có nhãn và script đo cho VIGIL One; tinh chỉnh ngưỡng (P1).
- Việt hóa giao diện và nhãn hành vi; lớp i18n (P1).
- Lịch kỳ thi, bộ lọc; trang Live monitor theo phòng hoặc phiên (P1).

**Giai đoạn D: Triển khai và mở rộng**
- Alembic. Docker chạy được, có GPU, không mật khẩu cứng. Tài liệu reverse proxy TLS (P1).
- TensorRT và tiền xử lý GPU cho Classroom nhiều phòng (theo `KE_HOACH_TOI_UU_HIEU_NANG.md`).
- Log có cấu trúc, `/metrics`, giới hạn số lần thử đăng nhập và PIN (P2).
- Gỡ pipeline cũ, mã chết, router trùng (P2).

---

## 5. Phụ lục

**A. Điểm vào và thành phần chạy thật**

| Điểm vào | Vai trò |
|---|---|
| `vigil.py` | Hub, web, phân tích One, app thí sinh (theo cờ) |
| `server.py` → `api.main:app` | API và dashboard |
| `exam_monitor/candidate_app.py` | App thí sinh (chỉ quay và gửi) |

**B. Số liệu khảo sát**
- Test: 523 đạt (2026-10-19, Windows, Python 3.11, RTX 4060).
- Hiệu năng Classroom: 1 phòng, 15,9 khung phân tích/s, 1,00× thời gian thực ở trạng thái ổn định.
- CSDL thật (`data/vigil_proctoring.db`): 7 phòng, 56 camera, 6 camera thực sự có ghế.

**C. Đã sửa trong đợt 18–19/10 (chưa commit)**
- Lõi hướng nhìn dọc của VIGIL One.
- Lớp hướng nhìn không còn nhấp nháy ở One monitor.
- Debug overlay ở Review và video cả ca, có API trace.
- Ô One trên Wall không còn hiện sai thí sinh.
- Playback: video One, timeline đúng tỉ lệ.
- Review: không tràn mã phòng hoặc mã thí sinh.
- Báo cáo: không còn "110%" độ phủ ghế.

---

## 6. Cập nhật 2026-10-24: Giai đoạn A (nhánh `feat/phase-a-foundation`)

| Mục | Trạng thái | Thay đổi |
|---|---|---|
| Thời hạn lưu dữ liệu (P0) | ✅ | `storage/retention.py`: xóa media sau `VIGIL_RETENTION_DAYS` (mặc định 90), giữ bản ghi, giữ phiên có vi phạm đã xác nhận, chỉ xóa trong thư mục dữ liệu. Chạy tự động mỗi 6 giờ. Có thẻ "Data retention" ở trang Hệ thống. Thí sinh thấy thời hạn ở màn hình đồng ý. |
| Phân quyền API ghi (P0) | ✅ | `writes_need` cho 10 router: cấu hình phòng cần CHIEF, vận hành cần PROCTOR, công cụ dữ liệu cần ADMIN. Hub nghe ngoài loopback thì tự bật `VIGIL_AUTH_MODE=required`. |
| Phiên One bỏ dở (P1) | ✅ | Watchdog đóng phiên im lặng quá `VIGIL_ONE_ABANDON_MINUTES` (mặc định 15): tín hiệu `ONE_ABANDONED`, phân tích nốt, ghép video. |
| Camera trùng (P1) | ✅ | Sửa script tạo trùng; `merge_duplicate_cameras` chạy khi khởi động (CSDL thật: 56 → 13 camera, giữ nguyên 140 ghế). |
| CI (P1) | ✅ | `.github/workflows/ci.yml`: pytest (CPU), ruff (lỗi nghiêm trọng), kiểm tra cú pháp JS. `requirements.txt` chuyển sang UTF-8 và cài được trực tiếp (thêm index CUDA 12.4). |

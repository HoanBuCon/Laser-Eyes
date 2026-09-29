# Kế hoạch phát triển tính năng hỗ trợ giám thị — VIGIL

- Ngày lập: 2026-09-29.
- Trạng thái: PLAN — chưa triển khai tính năng, chưa nghiệm thu.
- Repository: HoanBuCon/Laser-Eyes.
- Nhánh kế hoạch: `feat/proctor-support-plan`.
- Baseline: `6a7b4b3baf062f5a35cf5a02faac7ee127239bce`, từ `feat/classroom-local-gaze-ui`.
- Worktree riêng: `../laser_eyes-proctor-support-plan`.
- Các thay đổi button/Light Mode chưa commit ở worktree UI không nằm trong baseline này. Khi tích hợp sau, chỉ đưa vào các commit UI đã kiểm tra; không chép đè working tree.
- Giới hạn khảo sát: đọc mã nguồn; chưa kiểm chứng E2E các tính năng hiện hữu trong lượt lập kế hoạch. Không dùng kết quả pytest lịch sử như kết quả của kế hoạch này.

## 1. Mục tiêu và phạm vi

Hoàn thành luồng: **giám thị đánh dấu → xem bằng chứng → bổ sung bối cảnh → lưu quyết định → xuất hồ sơ** cho Classroom Web và Local Gaze Tkinter.

AI hiện tại tiếp tục cung cấp tín hiệu và sự kiện. Giám thị có thể đánh dấu độc lập khi AI bỏ sót. Bookmark thủ công không được tự gán AI confidence, điểm nguy cơ hoặc kết luận gian lận.

### MVP trong một tuần

1. Bookmark một frame với ghi chú tùy chọn, ghế/thí sinh có thể để trống.
2. Xem ảnh, zoom/pan, xem toàn cảnh và crop từ ROI nếu có; mở clip AI hiện hữu.
3. Hàng đợi hiển thị rõ nguồn AI hoặc giám thị; lọc và chuyển mục tiếp theo.
4. Ghi chú, lý do, quyết định bền vững; mở lại phiên để tiếp tục xem xét.
5. Báo cáo tối thiểu JSON/CSV; HTML in được và ZIP bằng chứng nếu còn dung lượng sprint.

Chưa cam kết timeline đầy đủ, cắt clip tùy chọn, tua ngược camera trực tiếp, đa camera đồng thời, dashboard web tập trung cho Local hoặc huấn luyện model.

## 2. Freeze AI Core

Freeze là phạm vi thay đổi kỹ thuật của sprint, không phải tuyên bố prototype đã hoàn tất. Baseline AI phải được ghi nhận trước khi lập trình.

### Không thay đổi trong sprint

- Model, weights, provider/fallback, preprocessing của inference.
- Calibration AI, thresholds, smoothing, temporal FSM, patterns, risk scoring.
- Classroom: `detector.py`, `head_pose_provider.py`, `observation_extractor.py`, `temporal_episode_engine.py`, `behavior_pattern_engine.py`, `seat_risk_tracker.py`, `pipeline/` và tham số AI trong configs.
- Local: logic suy luận trong `exam_monitor/engine.py`, `exam_monitor/audio.py`, `EventDetector` trong `exam_monitor/events.py`.

### Được thay đổi để hỗ trợ nghiệp vụ

- UI, API, bookmark, review, báo cáo và persistence.
- Adapter capture/buffer phục vụ bằng chứng; chỉ thêm quan sát frame, không đổi dữ liệu đưa vào inference.
- `DemoRuntime` và Local worker: thêm hook/command cần thiết ở ranh giới I/O, giữ nguyên thứ tự xử lý AI.
- `SessionStore` nằm cùng file với `EventDetector`: chỉ sửa phần lưu trữ nếu cần và kiểm tra diff theo class.

Ghi manifest: baseline commit, model path/hash nếu file có sẵn, resolved config/hash, chế độ REAL/DEGRADED/MOCK/ERROR. Không thay model thiếu bằng dữ liệu giả. Thay đổi ngoài phạm vi freeze cần được ghi thành quyết định riêng.

## 3. Nền tảng đã xác minh từ code

| Thành phần | Căn cứ | Có thể tái sử dụng | Khoảng trống |
|---|---|---|---|
| Classroom runtime | `classroom_monitor/demo/runtime.py`: callbacks, `get_latest_jpeg`, `pause` | Frame, index, source timestamp; event callback | MJPEG và status chưa tạo hợp đồng frame chính xác cho bookmark; pause hiện dừng processing |
| Demo review | `api/routes/demo.py` | Queue, ảnh/clip, integrity, review | Chưa có endpoint bookmark thủ công |
| Review persistence | `storage/review_service.py` | Ghi decision, event status, audit trong transaction | Cần target bookmark; không tạo DetectionEvent giả để tái sử dụng |
| Storage | `storage/db_models.py` | Session, event, evidence, review, audit | Chưa có bookmark; evidence/review hiện gắn chặt với DetectionEvent |
| Workbench | `dashboard/js/data_workbench.js`, `api/routes/data_workbench.py` | Player, tốc độ, timeline, mark start/peak/end | Là công cụ annotation; bước 0.04s không bảo đảm đúng một frame |
| Workbench review | `api/routes/data_workbench.py`: `review_ai_event` | UI review có sẵn | Vẫn dùng repository riêng; cần hợp nhất dịch vụ ghi quyết định khi tích hợp |
| Local | `exam_monitor/app.py`: `_monitoring_loop`, `_poll_frames`, `_review_selected_event` | Raw/display frame, lưu JPEG, ghi chú | Review hiện chủ yếu nhập ghi chú; chưa có viewer ảnh tại thao tác đó |
| Local source | `exam_monitor/sources.py` | Webcam/video/demo | Frame đã mirror/resize; video có vòng lặp; timestamp phiên không tự động là timestamp video |
| Local export | `exam_monitor/models.py`, `exam_monitor/events.py`, `exam_monitor/app.py` | Session JSON, JSON/CSV export | Trạng thái “Chưa xem/Đã xem” chưa tương đương human decision |

## 4. Thiết kế chức năng

### 4.1 Đánh dấu frame

- Nút “Đánh dấu”, phím `M`; phím tắt không hoạt động khi đang nhập liệu.
- Giữ một frame để xem xét, cho phép lưu ngay rồi thêm ghi chú sau.
- Classroom chọn ghế hoặc “Chưa xác định”; Local gắn session/candidate hiện hành.
- Toast phân biệt đang lưu, đã lưu, lỗi; chỉ báo thành công sau khi ảnh và metadata được lưu bền vững.
- Double click/retry cùng request không tạo bookmark trùng; cho phép chủ động tạo bookmark khác với request mới.
- Nếu frame hết hạn hoặc run đã đổi, báo lỗi rõ; không tự thay bằng frame mới nhất.

### 4.2 Viewer và queue

- AI incident và bookmark là hai loại mục, có ID và nhãn nguồn riêng; không ép bookmark thành AI incident.
- Mở ảnh toàn cảnh, zoom/pan; crop ghế là ảnh dẫn xuất, luôn có đường quay lại toàn cảnh.
- Clip chỉ xuất hiện khi thực sự có; không tạo nút xem clip giả cho bookmark chỉ có ảnh.
- Bộ lọc theo phiên, nguồn, ghế/candidate, trạng thái; thứ tự theo thời gian hoặc ưu tiên có sẵn của AI.
- Thao tác “Giữ ảnh để xem” độc lập với nút pause processing của demo.
- Luồng inference/capture tiếp tục chạy khi mở viewer và ghi chú.
- Bản MVP không chỉnh ROI khi đang xem xét; dùng snapshot ROI/geometry tại thời điểm đánh dấu để crop không lệch sau khi hiệu chỉnh ghế.

### 4.3 Quyết định và bối cảnh

- Quyết định chuẩn: `PENDING`, `CONFIRMED`, `REJECTED`, `INCONCLUSIVE`.
- `CONFIRMED` phải có nhãn UI rõ: xác nhận nội dung/sự việc sau xem xét, không tự động tạo quyết định kỷ luật.
- Tách “đã mở/xem” khỏi decision; xem ảnh không tự chuyển sang CONFIRMED.
- Lý do đề xuất: hành vi bình thường, tương tác với giám thị, che khuất, ảnh không đủ rõ, gán sai ghế, cần xác minh, khác.
- Lưu người thao tác, thời gian, ghi chú; sửa quyết định phải có lịch sử trước/sau.
- Reviewer ID ở prototype là thông tin khai báo của người dùng, chưa được coi là danh tính đã xác thực.
- Review không thay đổi AI score, episode, pattern hoặc dữ liệu GT.

## 5. Thiết kế kỹ thuật tối thiểu

### 5.1 Hợp đồng bookmark v1 (PROPOSED)

`bookmark_id`, `request_id`, `session_id`, `run_id`, `source_product`, `source_kind`, `source_ref`, `frame_id`, `source_timestamp_ms`, `session_elapsed_ms`, `captured_at`, `created_at`, `subject_ref`, `created_by`, `note`, `review_decision`, `linked_ai_incident_id`, `evidence_status`, `snapshot_ref`, `sha256`, `coordinate_space`, `roi_snapshot`.

- Source timestamp có thể null đối với camera; tuyệt đối không thay bằng wall-clock nhưng vẫn gọi là thời gian video.
- `source_kind` phân biệt video, camera, simulation; simulation có nhãn xuyên suốt queue/export.
- `coordinate_space` lưu kích thước, resize/mirror và loại ảnh raw/rendered. Local raw hiện là frame sau chuẩn bị nguồn, không mặc định là bản gốc camera.
- `linked_ai_incident_id` tùy chọn; score AI không thuộc bookmark.
- UUID/ID có namespace trong queue: `ai:<id>` và `manual:<id>`.

### 5.2 Capture chính xác

Classroom dùng endpoint phục vụ viewer ảnh tĩnh trả về ảnh cùng token/frame ID và timestamp từ một bản ghi nguyên tử. Khi bấm đánh dấu từ live, mở ảnh tĩnh này và cho giám thị xác nhận/lưu; UI diễn đạt là ảnh được capture, không hứa chính xác frame MJPEG vừa nhìn trước khi bấm. Khi đã ở viewer, bookmark phải lưu đúng token đang hiển thị.

Nếu yêu cầu một lần bấm phải lưu đúng frame live đang thấy, cần transport hiển thị frame kèm ID; đây là mở rộng riêng, không ghép ID từ status polling với MJPEG.

- Dùng frame store có giới hạn bộ nhớ, tuổi frame và số capture đang giữ; thiết kế giới hạn sau khi đo kích thước ảnh thực tế.
- Command bookmark gửi vào adapter/queue; không thực hiện DB/encode chặn callback AI.
- Bản sao frame phải có quyền sở hữu rõ; không giữ tham chiếu đến buffer đang bị worker ghi.
- Local chụp bản sao frame đang hiển thị cùng metadata trong một thao tác ở UI thread, ghi file ở worker I/O rồi trả kết quả về Tkinter.
- Local video đang lặp cần cycle/run reference và media position riêng; không suy ra từ `datetime.now()` hoặc FPS xử lý.
- Thiếu nguồn/frame: action bị disable hoặc trả 409; thiếu snapshot đã lưu: báo evidence unavailable.

### 5.3 Persistence và review

Classroom thêm `ProctorBookmark` và lịch sử review bookmark riêng; không làm thay đổi mặc định của DetectionEvent/EvidenceFile hiện tại. Tái sử dụng quy tắc review qua service chung với target type rõ ràng.

Local thêm collection bookmark và dữ liệu review vào session JSON có version; đọc tương thích session cũ. Ánh xạ “Đã xem” thành đã xem, không suy diễn thành CONFIRMED. Ghi JSON bằng file tạm và thay thế nguyên tử; giữ ảnh evidence riêng.

Vòng đời evidence: `PENDING → READY | FAILED`. File và DB không có transaction chung: ghi file tạm, finalize, cập nhật DB; xử lý lỗi/orphan theo run ID, không xóa evidence cũ của người dùng. Tính digest sau khi finalize. Digest có sẵn chỉ có nghĩa HASH AVAILABLE, muốn VERIFIED phải thực sự so sánh lại file.

### 5.4 API dự kiến (PROPOSED)

- `POST /api/v1/proctor/captures`: chụp một frame xác định; trả capture ID và metadata.
- `GET /api/v1/proctor/captures/{id}/snapshot`: ảnh tĩnh để xác nhận trước khi lưu.
- `POST /api/v1/proctor/bookmarks`: lưu capture theo idempotency request ID.
- `GET /api/v1/proctor/bookmarks?session_id=...`: phân trang/lọc bookmark đã lưu.
- `GET /api/v1/proctor/bookmarks/{id}/snapshot`: lấy ảnh bằng ID, kiểm tra containment.
- `POST /api/v1/proctor/bookmarks/{id}/review`: lưu quyết định và audit.
- `GET /api/v1/proctor/queue?session_id=...`: adapter kết hợp AI + manual, giữ nguyên danh tính nguồn.
- `GET /api/v1/proctor/sessions/{id}/export`: hồ sơ được chọn, không mở quyền đọc đường dẫn bất kỳ.

Tên route/schema được chốt ở bước S0; chưa có trong code. HTTP polling luôn đọc DB; websocket chỉ thông báo sau persistence thành công. Web và Local không cần dùng chung backend trong MVP.

## 6. Backlog phụ thuộc và file dự kiến

Đường dẫn có dấu `+` là file đề xuất tạo mới, cần kiểm tra lại khi triển khai.

| ID | Công việc / file | Phụ thuộc | Công sức | Rủi ro chính | Tiêu chí nghiệm thu / validation | Nhóm commit |
|---|---|---|---|---|---|---|
| S0 | Ghi manifest freeze; kiểm tra môi trường, media, schema; chốt capture contract. `+docs/PROCTOR_SUPPORT_BASELINE.md` | Không | 0.5 ngày | Thiếu video/models trong worktree | Có baseline commit/config; ghi rõ tài nguyên thiếu; xác nhận run nguồn thời gian | docs(proctor) |
| S1 | Contract và storage bookmark. `+proctor_support/contracts.py`, `storage/db_models.py`, `+storage/proctor_service.py`, `storage/database.py` | S0 | 0.75 ngày | Schema cũ, orphan file | DB cũ đọc được; retry idempotent; file lỗi không READY; không có fake AI event | feat(storage) |
| S2 | Classroom capture + API. `+proctor_support/frame_store.py`, `classroom_monitor/demo/runtime.py`, `+api/routes/proctor.py`, `api/main.py`, `api/schemas.py` | S1 | 1 ngày | Frame/time lệch, ghi I/O chặn inference | Token trả đúng ảnh; stale/run mismatch bị từ chối; bounded memory; start/stop không rò state | feat(classroom) |
| S3 | Classroom viewer, đánh dấu, queue. `dashboard/demo.html`, `dashboard/js/demo.js`, `dashboard/css/demo.css` | S2 | 0.75 ngày | Queue trùng, mất selection, keyboard conflict | Không cần AI incident vẫn lưu được; ảnh xem và ảnh lưu trùng; reload còn bookmark | feat(web) |
| S4 | Local capture, viewer, bookmark và đọc lại session. `exam_monitor/app.py`, `exam_monitor/models.py`, phần SessionStore của `exam_monitor/events.py` | S1, contract capture S2 | 1 ngày | Race Tkinter, mất dấu khi stop, mirror/time | UI vẫn nhận frame; stop đợi lưu đang chạy; mở lại session thấy ảnh/note; dữ liệu cũ giữ nghĩa | feat(local) |
| S5 | Review, bối cảnh, lịch sử. `storage/review_service.py`, `storage/proctor_service.py`, `api/routes/data_workbench.py`, UI Web/Local | S3, S4 | 0.75 ngày | Quyết định giả thành công, state không thống nhất | Decision+audit bền vững; sửa có history; xem ảnh không tự confirm; lỗi DB được báo | feat(review) |
| S6 | Export tối thiểu JSON/CSV; HTML/ZIP là stretch. `+proctor_support/exporter.py`, API proctor, Local export | S5 | 0.5 ngày | Thiếu bằng chứng, đường dẫn ngoài scope | Mở export đối chiếu đúng IDs/time/nguồn/decision; báo file thiếu; không chứa file ngoài phiên | feat(reports) |
| S7 | Test tích hợp, nghiệm thu, hướng dẫn. `+tests/test_proctor_support.py`, `+docs/PROCTOR_SUPPORT_ACCEPTANCE.md` | S2–S6 | 1.25 ngày | Chỉ test bằng doubles, bỏ sót UI thật | Hoàn thành checklist mục 8; công bố measured metrics và giới hạn | test(proctor) |

Tổng dự kiến: 6.5 ngày công, dành 0.5 ngày dự phòng. Đây là kế hoạch cho một engineer tập trung, môi trường demo có sẵn. Kiểm thử thực hiện trong từng task; S7 là tích hợp cuối. Nếu S0/S2 phát hiện phụ thuộc media phức tạp, bỏ stretch export và chuyển timeline/clip sang sprint sau; không giảm yêu cầu persistence/frame identity.

## 7. Lịch một tuần

| Ngày | Kết quả phải có |
|---|---|
| 1 | S0 và phần chính S1: baseline, contract, dữ liệu bookmark |
| 2 | S2: capture token, lưu ảnh, API, đường lỗi |
| 3 | S3: Classroom đánh dấu và viewer/queue chạy end-to-end |
| 4 | S4: Local đánh dấu, xem ảnh, lưu và mở lại phiên |
| 5 | S5: quyết định, lý do, lịch sử và queue filters |
| 6 | S6 và S7: export tối thiểu, tích hợp, test lỗi/lifecycle |
| 7 | Demo India/Student/Local, sửa lỗi nghiệm thu, tài liệu; chủ sản phẩm đánh giá |

## 8. Kiểm thử và nghiệm thu

### Kiểm thử tự động cần thiết

- Capture ID tham chiếu đúng bytes ảnh và metadata, kể cả khi live frame tiếp tục đổi.
- Capture hết hạn/run cũ không lưu nhầm ảnh mới; hai lần gửi cùng request chỉ có một bookmark.
- Ghi ảnh thất bại, hết dung lượng giả lập, DB rollback: không trả success/READY sai.
- Session/seat/capture không cùng nguồn bị từ chối; không phục vụ file ngoài evidence root.
- Quyết định và audit được ghi bền vững; lịch sử giữ giá trị trước/sau; pending không biến mất khi reload.
- Local JSON cũ vẫn đọc được; “Đã xem” không thành CONFIRMED; shutdown không mất bookmark đang lưu.
- HTTP polling lấy lại bookmark khi websocket mất kết nối; reconnect không nhân đôi card.
- Chế độ simulation giữ nhãn khi export; bookmark không có confidence AI giả.

Test doubles xác minh luồng nghiệp vụ phải ghi nhãn integration; không gọi chúng là kiểm chứng độ chính xác model. Test real-source tách riêng và ghi nguồn dùng thực tế.

### Nghiệm thu thủ công

1. Chạy Classroom với India; capture một thời điểm và xem ảnh tĩnh.
2. Trong lúc viewer mở, kiểm tra frame/status giám sát vẫn tiến.
3. Lưu bookmark không gắn incident AI, để ghế trống rồi bổ sung ghi chú.
4. Refresh và mở lại đúng ảnh, thời điểm, nguồn tạo.
5. Tạo bookmark có ghế; đối chiếu crop và toàn cảnh.
6. Xem một AI incident có snapshot/clip hiện hữu; lọc AI và manual riêng.
7. Lưu quyết định và sửa quyết định; kiểm tra lịch sử còn giá trị cũ.
8. Mất websocket rồi reconnect; queue vẫn cập nhật bằng polling và không trùng.
9. Đổi từ India sang Student; không để capture/run ID cũ lẫn vào phiên mới.
10. Local: chạy demo có nhãn, đánh dấu, mở ảnh, ghi chú, stop và mở lại phiên.
11. Local: lặp lại với camera/video thật nếu có; xác nhận mirror và source time được mô tả đúng.
12. Xuất hồ sơ và đối chiếu nội dung; thiếu media phải có thông báo rõ.
13. Thao tác Light/Dark sau khi tích hợp UI, thu nhỏ cửa sổ, bàn phím và focus input.

### Đo giá trị sản phẩm

Ghi thời gian đánh dấu→lưu xong, thời gian mở bằng chứng, thời gian hoàn thành review, số bookmark mất/trùng và ảnh/time không khớp. Đo FPS trước/sau trên cùng máy và nguồn để phát hiện overhead capture.

Mục tiêu nghiệm thu bắt buộc: không mất/trùng bookmark trong kịch bản test; không frame mismatch; quyết định còn sau restart. Ngưỡng latency/FPS chỉ chốt sau đo baseline S0; không công bố tốc độ chưa đo hoặc dùng test pass làm bằng chứng AI chính xác.

## 9. Backlog sau MVP

| Tính năng | Phạm vi | Dự kiến |
|---|---|---|
| Timeline xem xét | Hợp nhất dấu mốc AI/manual theo video đã lưu; click mở đúng thời điểm | 2–3 ngày |
| Đánh dấu đoạn | In/out/peak, validate bounds, lưu đoạn cần xem riêng khỏi GT | 2–3 ngày |
| Nhật ký can thiệp | Ghi nhắc nhở, đến kiểm tra, xác minh và liên kết bookmark | 1 ngày |
| Ghim ghế | Theo dõi các ghế được chọn và lịch sử liên quan | 1–2 ngày |
| Tình trạng quan sát | Nguồn mất, stale frame, AI unavailable, evidence lỗi | 1–2 ngày |
| Quan sát thủ công khi AI lỗi | Đường capture độc lập, banner AI unavailable, không synthetic fallback | Khảo sát riêng 1 ngày trước ước lượng |
| Tua ngược live | DVR/buffer disk, quota và retention; không thể dựa vào JPEG mới nhất | Khảo sát riêng |
| Xuất tập dữ liệu có duyệt | Chuyển bookmark đã được kiểm tra sang annotation theo thao tác rõ ràng | Sau quy trình review ổn định |

Không đưa nhận diện danh tính, suy luận “gian lận” mới, chấm xác suất gian lận, tự retrain hoặc hợp nhất perception vào backlog của sprint này.

## 10. Git, tích hợp và điểm dừng

- Kế hoạch nằm ở worktree riêng; worktree UI giữ nguyên thay đổi chưa commit.
- Khi triển khai, dùng commit nhỏ theo S0–S7, stage file chủ đích; không đưa media, DB runtime hoặc model weights vào commit tính năng.
- Worktree mới không tự có venv, media/model bị ignore hoặc DB người dùng. S0 phải chỉ rõ đường dẫn tài nguyên và dùng DB/evidence test riêng; không mặc định sửa DB đang chạy demo.
- Rà diff vùng AI Core trước mỗi nhóm commit; nếu output AI đổi, tìm nguyên nhân integration trước khi tiếp tục.
- Chỉ tích hợp UI Light Mode từ commit đã hoàn thiện, xử lý xung đột theo file; không reset/stash tự động các thay đổi của chủ dự án.
- Rollback bằng revert commit chức năng; bảo toàn bảng/file bookmark, không downgrade phá dữ liệu để quay UI lại.
- Điểm dừng tối đa: `PROCTOR_SUPPORT_READY_FOR_HUMAN_ACCEPTANCE` sau khi có bằng chứng nghiệm thu. Chủ sản phẩm quyết định acceptance và merge vào DEV.

## 11. Checklist trạng thái kế hoạch

- [x] Khảo sát code và xác định khả năng tái sử dụng.
- [x] Chọn baseline, tạo nhánh/worktree riêng.
- [x] Lập phạm vi freeze, backlog phụ thuộc và tiêu chí nghiệm thu.
- [ ] S0–S7 đã triển khai và kiểm thử.
- [ ] Kiểm thử nguồn thật và báo cáo số đo.
- [ ] Chủ sản phẩm nghiệm thu.

Tài liệu này là kế hoạch thực hiện, không xác nhận tính năng đã được xây dựng.

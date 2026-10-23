# Báo cáo bàn giao dự án VIGIL AI

> **Tài liệu lịch sử (2026-10-06).** Hiện trạng mới nhất: `docs/BAN_GIAO_HIEN_TRANG.md`. Số test, nhánh, commit và preset (India/Student) dưới đây là của lần bàn giao đó.
> Trạng thái hiện tại: `README.md` và `docs/DEMO_RUNBOOK_VIGIL_HUB.md` trên nhánh `feat/one-server-analysis`
> (preset `classroom-01..03`, hiệu chỉnh ghế từ database, khởi động bằng `python vigil.py`).

**Mục đích:** giúp một nhóm/đơn vị mới tiếp quản codebase, dựng lại môi trường, hiểu ranh giới sản phẩm, kiểm chứng trạng thái hiện có và tiếp tục phát triển an toàn.

**Mốc ghi nhận:** 2026-10-06. Tài liệu này phân biệt trạng thái đã xác minh tại lần bàn giao với mô tả lịch sử/định hướng. Trước khi triển khai, hãy fetch remote và xác nhận lại commit vì nhánh có thể đã tiến lên.

## 1. Tóm tắt điều hành

VIGIL AI là nguyên mẫu hỗ trợ người giám thị trong kỳ thi. Nó tổ chức tín hiệu quan sát, mốc thời gian, bằng chứng và quyết định rà soát; AI không đưa ra kết luận cuối cùng về gian lận. Hai sản phẩm có chung mục tiêu nghiệp vụ nhưng xử lý hai hình học camera khác nhau:

- **VIGIL Classroom**: video toàn cảnh phòng thi, nhiều người và vị trí/seat; ứng dụng web/API, pipeline SRS v2 theo pose và suy luận theo thời gian.
- **VIGIL One**: tên sản phẩm đã chốt gần đây cho hướng một thí sinh qua webcam thông thường trong phòng máy hoặc thi trực tuyến; ứng dụng desktop/Tkinter hiện vẫn còn nhiều tên `Local` trong code, test và tài liệu cũ.

Giữ perception của hai sản phẩm riêng. Có thể chuẩn hóa dần các hợp đồng dùng chung như session, evidence, review decision, status, timestamp và capability health, nhưng không ép dùng chung gaze/pose, calibration, temporal engine hoặc UI.

Nguyên tắc nghiệp vụ cần được giữ xuyên suốt:

- AI tạo **tín hiệu quan sát**, episode/pattern và mức ưu tiên rà soát; mức ưu tiên không phải xác suất.
- Human review độc lập với trạng thái AI. Quyết định chuẩn gồm `PENDING`, `CONFIRMED`, `REJECTED`, `INCONCLUSIVE`.
- Bookmark do giám thị tạo là dữ liệu thủ công riêng, không được gán confidence/risk giả hoặc biến thành AI incident.
- SHA-256 chỉ cho biết phép so sánh toàn vẹn file; không tự bảo đảm chain of custody pháp lý.

## 2. Trạng thái bàn giao Git

Thông tin đã quan sát trong repository ở lần lập báo cáo:

| Hạng mục | Giá trị |
|---|---|
| Nhánh hiện tại | `feat/proctor-support-implementation` |
| HEAD hiện tại | `0f975b5` — `fix(ui): correct classroom controls in light mode` |
| Nhánh `DEV` | `981c0ed` — `Merge pull request #5 from HoanBuCon/feat/proctor-support-implementation` |
| Nhánh remote được giữ | `origin/DEV`, `origin/feat/proctor-support-implementation`, `origin/main` |
| Working tree | Có file chưa track `docs/MO_TA_SAN_PHAM_VIGIL_AI.md` — phải bảo toàn; tài liệu này đã đổi tên sản phẩm thành VIGIL One |
| Nhánh remote `main` | Chưa xóa; GitHub từ chối xóa vì đây là default branch |

PR proctor-support đã có merge commit trên `DEV`; nhánh implementation vẫn tồn tại và HEAD của nó có các commit workflow sau merge-base. Không giả định branch hiện tại và `DEV` giống nhau. Người nhận cần chạy `git fetch origin`, `git status --short --branch`, `git branch -avv`, `git log --graph --oneline --decorate -20` và so sánh `DEV...feat/proctor-support-implementation` trước khi chọn nền phát triển.

Các nhánh remote lịch sử khác đã bị xóa theo yêu cầu người dùng; không tự khôi phục hoặc xóa thêm nhánh default. Không dùng `git reset --hard`, `git clean -fd`, hoặc `git add .` để “làm sạch” trước khi xem xét thay đổi.

## 3. Mục tiêu, bối cảnh và giới hạn sản phẩm

### Mục tiêu

1. Hỗ trợ quan sát phiên thi và tìm nhanh thời điểm cần xem lại.
2. Gom tín hiệu AI, hình ảnh/video liên quan và ghi chú trong cùng workflow.
3. Cho phép giám thị chủ động đánh dấu frame kể cả khi AI không tạo incident.
4. Hỗ trợ replay video để demo/kiểm thử, và live source khi model/thiết bị khả dụng.
5. Tạo nền tảng mô-đun có thể điều chỉnh theo bố trí camera và kết quả thử nghiệm.

### Không nên cam kết

- Không gọi sản phẩm là hệ thống tự phát hiện/gắn nhãn người gian lận.
- Không đưa ra cam kết accuracy/recall/FPS chung nếu không gắn commit, cấu hình, phần cứng, video/dataset và lệnh đo cụ thể.
- Không mô tả là nhận diện khuôn mặt/định danh sinh trắc học: face landmarks/gaze không tương đương face recognition.
- Không mô tả hash đơn lẻ là chứng cứ pháp lý được bảo đảm.
- Không gọi nguyên mẫu là production-ready; chưa có RBAC, chính sách retention hoàn chỉnh, quản lý người dùng/migration production và đánh giá triển khai thực địa.

## 4. Sơ đồ kiến trúc

### VIGIL Classroom / SRS v2

```text
Video/camera
  → PoseClassroomDetector (YOLO-Pose)
  → SeatManager / spatial matching / seat actor
  → head-orientation provider (heuristic hoặc SixDRepNet tùy cấu hình)
  → ObservationExtractor
  → TemporalEpisodeEngine
  → BehaviorPatternEngine
  → SeatRiskTracker / review priority
  → ClassroomEvent / evidence hooks
  → DB + evidence store + JSON/export
  → FastAPI / WebSocket / polling
  → web Review Queue và quyết định human
```

Code chính nằm dưới `classroom_monitor/`; pipeline dùng chung giữa CLI và web ở `classroom_monitor/pipeline/srs_v2_pipeline.py`. `classroom_monitor/demo/runner.py` và `demo/runtime.py` là adapter chạy batch/CLI và web runtime. API ở `api/`, giao diện ở `dashboard/`, lưu trữ ở `storage/`.

### VIGIL One (tên cũ trong code: VIGIL Local)

```text
Webcam/video/demo source
  → exam_monitor.sources / FrameSource
  → GazeAnalyzer: face/iris, head orientation, face count
  → tùy cấu hình: object cues, audio/VAD
  → EventDetector (observable signals)
  → SessionStore / frame bookmark / JSON/CSV
  → Tkinter UI để theo dõi và rà soát
```

Mã chính: `main.py`, `exam_monitor/engine.py`, `sources.py`, `events.py`, `audio.py`, `models.py`, `app.py`, `theme.py`. Tên thương hiệu VIGIL One mới được chốt trong trao đổi và tài liệu mô tả; không nên nghĩ mọi nhãn trong code đã được đổi.

## 5. Tính năng đã được tích hợp gần đây

### Hỗ trợ giám thị / bookmark

Engineering workflow được bổ sung cho Classroom Web và VIGIL Local/One:

- Capture một frame và giữ snapshot để xem/ghi chú; token/run identity dùng để tránh lưu nhầm frame cũ.
- Bookmark thủ công tách khỏi AI incident, có thể thêm seat/note và review decision.
- Classroom queue kết hợp nhưng phân biệt rõ AI incident và bookmark của giám thị.
- Xem snapshot, crop theo seat khi có ROI, zoom cơ bản; clip thuộc AI evidence hiện có, không tự sinh clip giả cho bookmark chỉ có ảnh.
- Review history và persistence; JSON/CSV export cho các mục được hỗ trợ.
- Local có thao tác đánh dấu frame (`Ctrl+B`/nút UI theo acceptance doc), mở lại từ trang sự kiện/session và export.
- Phát clip web cho Chrome/Edge dùng H.264 playback derivative khi cần; SHA đối chiếu file archive gốc.

Các module liên quan đã biết: `api/routes/proctor.py`, `proctor_support/contracts.py`, `proctor_support/frame_store.py`, `storage/proctor_service.py`, các thay đổi trong `storage/db_models.py`/`database.py`, `classroom_monitor/evidence_playback.py`, `exam_monitor/app.py`/`events.py`/`models.py` và `dashboard/demo.html`/`dashboard/js/demo.js`.

**Mức nghiệm thu:** `docs/PROCTOR_SUPPORT_ACCEPTANCE.md` ghi engineering validation complete nhưng giao diện/luồng E2E cuối cùng vẫn cần chủ sản phẩm nghiệm thu trên máy/màn hình demo thật. Các giới hạn đã ghi: reviewer ID do client khai báo, bookmark Classroom lưu ảnh chứ không có clip riêng, cần `ffmpeg` và `ffprobe` trên PATH để chuyển mã clip, seat crop phụ thuộc `seat_code` và snapshot polygon, viewer zoom/pan là cơ bản.

### UI

Web Classroom được làm lại theo visual language của VIGIL Local, có global design tokens/shell, dark/light mode và các đợt sửa màu control cho light mode. Kiểm tra trực quan thêm trên màn hình demo thật vẫn cần thiết; test backend không kiểm chứng đầy đủ theme, contrast, responsive hoặc trạng thái hover/focus.

## 6. Trạng thái AI và kết quả validation

### Automated tests — xác minh trong phiên bàn giao

Đã chạy trên nhánh `feat/proctor-support-implementation`, Python 3.11.9, Windows:

```powershell
venv\Scripts\python.exe -m pytest
```

Kết quả: **271 passed, 1 warning, 0 failed, 26.46s**. Warning SQLAlchemy phát sinh tại `tests/test_demo_system_integration.py::test_db5_transaction_safety_and_rollback` do test tạo `DetectionEvent` trùng identity key trong session. Test pass không chứng minh accuracy AI.

README cũng ghi 271 tests pass ngày 2026-09-30; kết quả chạy ở lượt bàn giao khớp số lượng này. Tài liệu `docs/PROTOTYPE_CURRENT_STATUS.md` lại là snapshot cũ, ghi validated source commit `96f04da`, benchmark functional predecessor `41604e7` và 259 test. **Không dùng tài liệu đó làm trạng thái validation tại HEAD hiện tại nếu chưa tái chạy benchmark.**

### Benchmark cũ cần diễn giải đúng

Các số dưới đây được trích từ snapshot cũ trong `docs/PROTOTYPE_CURRENT_STATUS.md`, không phải lần benchmark mới trên HEAD `0f975b5`:

| Nguồn | Kết quả lịch sử trong tài liệu | Cách diễn giải |
|---|---|---|
| Student full CLI | 219 frames, 20 FPS nguồn, 10.87 processing FPS, 68 episodes, 13 patterns, 5 review incidents; 5 snapshot/clip READY | Chưa có Student GT commit trong snapshot đó, không thể suy ra precision/recall |
| India full CLI | 2,150/2,153 frames xử lý, 7.84 FPS, 551 episodes, 127 patterns, 26 incidents | Strict valid GT chỉ 9 records; TP=7, FP=264, FN=2; precision 2.58%, recall 77.78%, F1 5.00%, IoU trung bình 0.66 |
| Web LIVE Student | 219/219 frames, khoảng 11.13 FPS, 5 incidents, evidence READY | Báo cáo nói API polling có cùng 5 event ID; final browser acceptance vẫn yêu cầu người thật |
| Web REPLAY Student | 219 frames ở recorded 20 FPS; incident vào queue, snapshot/MP4 HTTP 200 | Replay đã được kiểm thử trên snapshot cũ |

Precision India rất thấp, nên model chỉ phù hợp ưu tiên rà soát ở nguyên mẫu; không dùng làm verdict tự động. Phần cứng benchmark lịch sử trong status: RTX 4060 Laptop GPU 8,188 MiB, driver 610.47, Torch 2.6.0+cu124, YOLO `yolo11n-pose.pt`, SixDRepNet trên GPU 0. Người nhận cần tái chạy nếu định công bố số liệu.

### Tài nguyên và dữ liệu

- README nói live Classroom fail-closed nếu YOLO-Pose không load; mock chỉ bật tường minh qua `--allow-mock`/`allow_mock=true` và phải được đánh dấu `MOCK`/simulation. Recorded replay là recorded/degraded, không phải live inference.
- Hai scene config được liệt kê: `configs/scenes/india_classroom.yaml`, `configs/scenes/student_classroom.yaml`.
- Snapshot audit cũ ghi India video/GT và Student video; Student GT không có trong committed snapshot nên không có metric strict Student.
- Dữ liệu dự án và output có artifact lịch sử/generated; kiểm tra `.gitignore`, manifest và nguồn authoritative trước khi lấy artifact làm benchmark.

## 7. Cài đặt, chạy và test

### Môi trường

README hiện hướng dẫn base requirements và HPE:

```powershell
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe -m pip install -r requirements-hpe.txt
```

`requirements.txt` đang khóa nhiều thư viện, gồm FastAPI/Pydantic/SQLAlchemy, OpenCV, MediaPipe, Torch/Torchvision CUDA, Ultralytics, sounddevice/webrtcvad; có cả `opencv-python` và `opencv-contrib-python`. `requirements-hpe.txt` bổ sung `sixdrepnet`/pandas. Đây là requirements của working copy hiện tại; người nhận cần xác nhận Python, CUDA, driver, model download và nền tảng OS trước khi cài, không cài mù trên production host.

### Classroom Web

```powershell
venv\Scripts\python.exe server.py --host 127.0.0.1 --port 8000
```

Mở `http://127.0.0.1:8000/demo`. README nêu LAN binding cần demo token:

```powershell
venv\Scripts\python.exe server.py --host 0.0.0.0 --port 8000 --demo-token "choose-a-secret"
```

Không mở ra mạng công cộng. Token/demo gate là bảo vệ prototype, không thay thế authentication/RBAC production.

### Classroom CLI real-model smoke

```powershell
venv\Scripts\python.exe scripts\run_demo_video.py --video student
venv\Scripts\python.exe scripts\run_demo_video.py --video india
```

Để tái lập đầy đủ, chỉ định output path mới, lưu stdout/manifest, config và commit SHA. Video/model có thể không nằm trong mọi clone.

### VIGIL One

```powershell
venv\Scripts\python.exe main.py
```

Kiểm tra source/camera, mock/demo label, calibration, mark-frame và stop/restart trên máy Windows thực tế. Tài liệu acceptance chi tiết ở `docs/PROCTOR_SUPPORT_ACCEPTANCE.md`.

### Test

```powershell
venv\Scripts\python.exe -m pytest
```

Kết quả 271 pass vừa được xác minh trên nhánh hiện tại. Nên chạy test lại sau checkout đúng branch và trước/sau các sửa đổi; tách unit/integration/real-model smoke trong báo cáo.

## 8. Bố cục repository để định hướng

| Đường dẫn | Vai trò |
|---|---|
| `main.py`, `exam_monitor/` | VIGIL One/Local desktop, gaze, audio, events, session, UI |
| `server.py`, `api/` | Entrypoint web và FastAPI routers/realtime |
| `classroom_monitor/pipeline/` | Semantics chung SRS v2 giữa CLI và web |
| `classroom_monitor/demo/` | Runtime web, CLI runner, renderer, exporter, demo config |
| `classroom_monitor/` root | detector, seat mapping, head pose, observations, episodes, patterns, risk, evidence buffer; có cả các legacy modules cần xét reachability |
| `proctor_support/` | bookmark contracts/frame store |
| `storage/` | DB setup/models/repositories/review/evidence/proctor services |
| `dashboard/` | HTML/JS/CSS web UI: demo, calibration, workbench, theme |
| `configs/scenes/` | scene/seat calibration cho video/camera |
| `data/`, `validation/` | dataset, GT, benchmark, diagnostic/generated artifacts; phân biệt nguồn và output |
| `tests/` | unit/integration/fixture tests; không đồng nhất pass với model quality |
| `docs/` | audit, runbook, benchmark/status, product description, acceptance |

Entrypoints phụ có thể tồn tại: `python server.py`, `python main.py`, `scripts/run_demo_video.py`, `scripts/run_demo_all_videos.py`, PowerShell/batch launchers và `python -m classroom_monitor`. Dùng `rg`/import tracing trước khi đánh dấu legacy; README xem `/api/v1/demo` là Classroom canonical và legacy `/api/v1/inference` HTTP 410 mặc định.

## 9. Privacy, security và vận hành

- Dữ liệu có thể gồm frames, clips, session history, audio cues tùy bật/tắt, bookmark, note, seat mapping và review decision. Đơn vị triển khai cần quyết định consent, quyền truy cập, retention, xóa, backup và nơi đặt dữ liệu.
- Không thấy định hướng face recognition; gaze/landmarks là tín hiệu hình học, không xác nhận danh tính.
- README/status ghi localhost mặc định; LAN cần token, CORS explicit, upload validation/size limit, path containment và escaping dynamic review-card content. Tái xác minh trên HEAD trước khi public deploy.
- Prototype reviewer ID chưa được xác thực danh tính; không coi là audit identity bảo đảm.
- SHA-256 cần server-side recomputation để nói `HASH VERIFIED`; digest không chứng minh ai tạo file, khi nào tạo, hay chuỗi lưu giữ.
- SQLite data/evidence path và quyền file trên host cần được cấu hình; không commit DB runtime, private recordings, token/secret hoặc model artifacts nếu không có quyết định chủ sở hữu.

## 10. Hạn chế và nợ kỹ thuật cần ưu tiên

Các tài liệu `docs/TECHNICAL_DEBT_REGISTER.md`, `docs/LEGACY_AND_DEAD_CODE_MAP.md`, `docs/CODEBASE_ARCHITECTURE_AUDIT.md` là audit baseline/history; status phải được đối chiếu với code hiện tại. Rủi ro còn được ghi trong current status/acceptance gồm:

1. **AI quality**: India precision 2.58% trong benchmark snapshot cũ là hạn chế nghiêm trọng; không tune threshold chỉ để làm metric đẹp. Thu thập thêm GT chất lượng, kiểm soát camera/seat ROI và tính false alert burden.
2. **Manual acceptance**: proctor flow chưa được chủ sản phẩm nghiệm thu trực quan trên máy/màn hình demo thật.
3. **Performance**: India 1280px đạt 0.26× realtime ở laptop benchmark cũ; HPE được ghi là bottleneck chính. Chưa optimize trước khi tái đo trên phần cứng mục tiêu.
4. **Lifecycle/soak**: cần kiểm tra nhiều vòng Start/Stop, reconnect, memory growth, browser close và Local camera/Tkinter trên target device.
5. **Persistence evolution**: SQLite schema có hardening cho fresh DB; migrations/Alembic và cập nhật DB đã tồn tại cần kế hoạch riêng.
6. **Security/identity**: demo token không phải RBAC; reviewer/client IDs chưa phải identity đã xác minh.
7. **Data governance**: retention, deletion, consent, access audit và deployment policy chưa phải sản phẩm production.
8. **Legacy/contracts**: còn các module/class/route đời cũ; không xóa trước khi kiểm tra import graph, tests và compatibility.
9. **Config/source truth**: xác nhận precedence, scene config, model provider, mock/degraded capability và source timestamp trên từng chế độ.
10. **UI evidence review**: H.264 conversion phụ thuộc ffmpeg/ffprobe; dark/light/responsive/focus phải nghiệm thu bằng mắt.

Danh sách này là ưu tiên kiểm tra chứ không khẳng định từng issue vẫn tái hiện. `docs/TECHNICAL_DEBT_REGISTER.md` chứa issue ID và lịch sử remediation; rà các status `RESOLVED`, `PARTIAL`, `DEFERRED` so với commit nhận bàn giao.

## 11. Kế hoạch tiếp quản đề xuất

### Ngày 1 — khôi phục trạng thái

- Xác nhận branch/HEAD/remote; quyết định làm từ `DEV` sau merge hay giữ feature branch.
- Bảo toàn `docs/MO_TA_SAN_PHAM_VIGIL_AI.md`, xác nhận owner muốn commit/chuyển giao file này.
- Cài môi trường từ requirements, xác nhận model, video, ffmpeg/ffprobe, DB và quyền truy cập.
- Chạy 271-test baseline, lưu log và lỗi môi trường.

### Ngày 2 — acceptance workflow

- Làm Classroom replay qua mark-frame → xem ảnh/clip → note → human decision → refresh → export.
- Làm tương tự VIGIL One simulation/live; xác nhận simulation được gắn nhãn và bookmark tách khỏi AI event.
- Chạy checklist `docs/PROCTOR_SUPPORT_ACCEPTANCE.md`, đánh dấu pass/fail và lưu ảnh/log.

### Sau acceptance — roadmap ưu tiên

1. Sửa lỗi/blocker của human workflow/persistence trước; không thêm perception mới.
2. Chốt tên VIGIL One nhất quán trong UI/docs khi có quyết định sản phẩm; đổi nhãn không được ngụ ý camera/online capability chưa kiểm chứng.
3. Xây benchmark tái lập riêng cho từng camera/scene; công bố precision/recall/false alerts cùng số GT và giới hạn.
4. Tối ưu chỉ khi profiler xác nhận bottleneck trên target hardware.
5. Lập kế hoạch migration, retention, identity/auth và operational security trước pilot thật.

## 12. Checklist nghiệm thu bàn giao

### Repository và build

- [ ] Commit SHA, nhánh, remote và thay đổi untracked đã được xác nhận.
- [ ] Có owner cho model weights, video nguồn, GT, DB và evidence.
- [ ] Môi trường Python/dependency/CUDA ghi lại được; server start/stop bình thường.
- [ ] Test suite chạy lại và có log; warning được ghi nhận.

### Classroom

- [ ] Chọn đúng video/scene và seat ROI; không lấy config camera khác.
- [ ] REAL/DEGRADED/MOCK/ERROR hiển thị đúng; lỗi model không giả làm inference thật.
- [ ] Replay incident xuất hiện và evidence mở được; trạng thái hash trung thực.
- [ ] Bookmark thủ công được lưu riêng, có thể mở lại sau refresh.
- [ ] Review decision và note còn bền vững; CSV/JSON đầy đủ.
- [ ] WebSocket mất kết nối thì polling/reconnect không mất hoặc nhân đôi card.
- [ ] Start/Stop/Start và đổi Student/India không rò state/run token.

### VIGIL One

- [ ] Launch `main.py`; simulation có nhãn rõ.
- [ ] Camera/microphone permissions và calibration hoạt động trên máy đích.
- [ ] Đánh dấu frame, xem lại, ghi chú, quyết định và export hoạt động.
- [ ] Stop đợi worker/I/O; mở lại session còn đủ dữ liệu.
- [ ] UI chỉ thể hiện tín hiệu, không tự kết luận gian lận.

### Chủ sản phẩm

- [ ] Nhận diện giới hạn accuracy/performance và chấp thuận use case thực tế.
- [ ] Chốt retention, consent, quyền truy cập và nơi lưu dữ liệu.
- [ ] Chấp thuận nhãn sản phẩm VIGIL One so với tên cũ Local.
- [ ] Ký nghiệm thu thủ công trước khi gọi nguyên mẫu sẵn sàng demo/pilot.

## 13. Tài liệu tham chiếu

- `README.md` — quick start, mode, launch commands và nguyên tắc review.
- `docs/PROTOTYPE_CURRENT_STATUS.md` — báo cáo benchmark/validation của commit cũ; phải cập nhật HEAD-bound trước khi tái sử dụng số liệu.
- `docs/PROCTOR_SUPPORT_BASELINE.md` — baseline/freeze và contract sản phẩm.
- `docs/PROCTOR_SUPPORT_IMPLEMENTATION_PLAN.md` — kế hoạch thực hiện; một số trạng thái kế hoạch đã lỗi thời so với code hiện tại.
- `docs/PROCTOR_SUPPORT_ACCEPTANCE.md` — checklist nghiệm thu người dùng.
- `docs/CODEBASE_ARCHITECTURE_AUDIT.md`, `docs/TECHNICAL_DEBT_REGISTER.md`, `docs/LEGACY_AND_DEAD_CODE_MAP.md`, `docs/VIGIL_LOCAL_VS_CLASSROOM.md` — audit, nợ kỹ thuật, legacy và ranh giới hai sản phẩm.
- `docs/MO_TA_SAN_PHAM_VIGIL_AI.md` — mô tả sản phẩm đã chốt tên VIGIL One; ở lần kiểm tra này file chưa được track.

## 14. Kết luận bàn giao

Codebase hiện có hai sản phẩm tách biệt, pipeline Classroom SRS v2, web review và workflow đánh dấu frame/ghi chú cho giám thị. Engineering tests trên nhánh bàn giao hiện tại đạt 271/271; tuy nhiên metric India trong validation snapshot cũ có precision thấp (2.58%), nên hệ thống chỉ nên ưu tiên rà soát bởi con người. Việc bàn giao kỹ thuật không thay thế acceptance trực quan, kiểm chứng nguồn thật, thỏa thuận privacy/retention hoặc quyết định của chủ sản phẩm về triển khai thực tế.

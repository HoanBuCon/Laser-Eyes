# Bàn giao hiện trạng VIGIL AI — nhánh `feat/one-server-analysis`

**Mốc:** 2026-10-17. Thay thế phần "trạng thái" của `docs/BAO_CAO_BAN_GIAO_VIGIL.md` (bản 2026-10-06, giữ làm lịch sử: nguyên tắc nghiệp vụ, giới hạn AI, privacy vẫn đúng).
Trước khi làm tiếp: `git fetch origin` và so commit với mục 2, vì nhánh có thể đã tiến lên.

## 1. Mục tiêu sản phẩm

VIGIL AI hỗ trợ giám thị: AI tạo **tín hiệu quan sát** và mức ưu tiên rà soát, con người ra quyết định (`PENDING / CONFIRMED / REJECTED / INCONCLUSIVE`). AI không kết luận gian lận.

- **VIGIL Hub** — một máy chủ (FastAPI + SQLite) và một website chung cho hai sản phẩm, có nút chuyển sản phẩm.
- **VIGIL Classroom** — camera toàn cảnh phòng thi, nhiều thí sinh theo ghế (YOLO-Pose + SixDRepNet + suy luận theo thời gian).
- **VIGIL One** — một thí sinh/webcam (phòng máy hoặc thi trực tuyến). Máy thí sinh **chỉ quay và gửi** video; Hub phân tích.

Mục tiêu của đợt này: đưa phân tích VIGIL One về Hub, xem trực tiếp trên web, một lệnh khởi động duy nhất, sửa các lỗi P1 của bản rà soát, và để Classroom LIVE chạy theo kịp camera trên laptop RTX 4060 khi demo.

## 2. Trạng thái Git

| Hạng mục | Giá trị |
|---|---|
| Repository | `github.com/HoanBuCon/Laser-Eyes` (GitHub báo repo đã chuyển tên; nên `git remote set-url origin https://github.com/HoanBuCon/Laser-Eyes.git`) |
| Nhánh làm việc | `feat/one-server-analysis` — đã push, **chưa có PR, chưa merge** |
| Nhánh gốc | `feat/vigil-one-hub` |
| Commit gần nhất về code | `b13af81` Classroom LIVE: no fixed analysis rate |
| Test | `516 passed, 1 warning` (Linux, Python 3.12, CPU, 2026-10-17); `516 passed` trên Windows, Python 3.11, RTX 4060 (2026-10-17, sau khi sửa bảng mã trong `test_single_entry.py`). Warning SQLAlchemy cũ ở `test_demo_system_integration.py` |

Xem lịch sử: `git log --oneline feat/vigil-one-hub..feat/one-server-analysis` (khoảng 21 commit).

## 3. Đã hoàn thành

### 3.1 VIGIL One phân tích trên Hub
- `storage/one_analysis.py` (`OneAnalysisService`): luồng trực tiếp + đoạn H.264 10 s, worker theo phiên (chia shard crc32), phân tích bù khoảng trống, vòng đời clip, theo dõi sức khỏe model, chặn duyệt khi phân tích chưa xong.
- `storage/one_live.py`: nhận WebSocket (JPEG ~10 fps + giọng nói), kẹp đồng hồ khung hình, phát lại MJPEG cho web.
- Trang web `/one-monitor` (`dashboard/one_monitor.html`, `dashboard/js/one-monitor.js`): hình trực tiếp có lớp phân tích, chỉ số, tín hiệu, đánh dấu khung, hiệu chỉnh lại ánh mắt, cho phép tài liệu.
- App thí sinh mỏng `exam_monitor/candidate_app.py` + `exam_monitor/live_stream.py`: không nạp MediaPipe/torch, xác nhận đã gửi hết trước khi nộp, nút GỬI LẠI, thanh nút dưới đáy cho màn hình phóng to.
- Mã tham gia do người tổ chức đặt (3–12 ký tự) và `scripts/setup_exam.py` (tạo giám thị, kỳ thi, thí sinh; mặc định DB làm việc, `--demo` cho DB demo).

### 3.2 Một lệnh khởi động
`python vigil.py` bật Hub + web + Classroom + phân tích One + app thí sinh. Tự phát hiện Hub cũ đang chạy bản code khác (dấu vân tay `vigil_build.py` trong `/health`) và tắt nó.

### 3.3 Sửa lỗi theo bản rà soát (5 giai đoạn, đều đã xong)
1. **Ranh giới tin cậy** — chỉ Hub được ghi trường do máy chủ tạo; phiên thí sinh luôn `SERVER`; mô phỏng chỉ từ loopback; tên file bằng chứng theo id DB, kiểm tra nằm trong thư mục; kẹp `started_at` ±5 phút.
2. **Đủ dữ liệu** — xác nhận upload, vòng đời clip, chỉ duyệt khi phân tích xong.
3. **TALKING** — phân tích 10 fps (DB cũ 5 fps tự nâng lên 10), cửa sổ theo số mẫu thật.
4. **Phân quyền** — `/ws/events` cần đăng nhập khi `VIGIL_AUTH_MODE=required`; thao tác phá hủy ở `/admin` chỉ ADMIN và bị chặn khi One đang bận; thu hồi thiết bị đóng luồng trực tiếp.
5. **Vận hành** — kiểm tra trước giờ thi chạy ngoài event loop (tối đa 2, quá tải trả 503), tắt sạch, compose truyền `VIGIL_DEMO_TOKEN`.
- Quyết định đã chốt với chủ dự án: phân tích 10 fps; trưởng giám thị được duyệt kèm lý do; bỏ chế độ client tự phân tích.

### 3.4 Hiệu năng Classroom LIVE (RTX 4060 Laptop)
- Tư thế đầu: tiền xử lý theo lô bằng cv2/numpy thay PIL (`sixdrepnet_input`), FP16.
- YOLO FP16; đọc khung và ghi `result.mp4` trên luồng riêng (`classroom_monitor/video_io.py`).
- **Không đặt tốc độ cố định:** `RealtimePacer` phân tích mọi khung khi máy theo kịp, chỉ bỏ khung đã trễ (tối đa 1 s liên tiếp). Khung bị bỏ vẫn vào clip bằng chứng; `result.mp4` giữ đủ khung, đúng thời gian.
- Hồ sơ đo `runtime_profile.json` có thời gian từng khâu, `yolo_breakdown_ms`, `stride`, `skipped_frames`. Ô FPS trên trang LIVE hiện thời gian từng khâu khi rê chuột.

Số đo trên máy chủ dự án (300 khung `classroom-01`, 33 ghế):

| Cấu hình | Khung phân tích/s | So với thời gian thực |
|---|---|---|
| Ban đầu | ~10 | 0,34× |
| Mọi khung, sau tối ưu | 19,9–21,1 | 0,66–0,70× |
| Nhảy cố định 2 khung | 17,3 | 1,15× |
| **Tự động (mặc định hiện tại)** — đo 2026-10-17 trên RTX 4060 Laptop, 1800 khung | **15,9** (847/1800 khung bỏ vì trễ) | **1,00× ở trạng thái ổn định**; 0,93× cả lần chạy (gồm nạp model và ghi bằng chứng cuối) |

Thời gian một khung (mọi khung): YOLO ~21 ms (tiền xử lý CPU 5–6, suy luận 10, hậu xử lý 1,7), tư thế đầu ~13 ms, vẽ ~3 ms. cuDNN benchmark + gom lô cố định đã thử, không có lợi → tắt mặc định.

## 4. Việc còn mở (ưu tiên từ trên xuống)

1. ~~Đo chế độ tự động trên RTX 4060~~ **Đã đo 2026-10-17:** theo kịp camera (1,00× ở trạng thái ổn định) với 15,9 khung phân tích/s, chưa đạt ~20 như dự kiến. Điều kiện: cắm sạc nhưng Windows ở chế độ nguồn *Balanced*, GPU có tải của ứng dụng khác. Một khung: YOLO 25,6 ms (tiền xử lý 7,3 / suy luận 12,3 / hậu xử lý 2,1), tư thế đầu 18–23 ms (bàn giao cũ: 13 ms), vẽ 4 ms. Đo lại ở chế độ hiệu năng cao nếu cần con số 20.
   `venv\Scripts\python.exe scripts\run_demo_video.py --video classroom-01 --max-frames 1800 --stride 0 --output data\bench\auto`
   Đo **ít nhất 60 s** (1800 khung): với 300 khung, nạp model và ghi bằng chứng cuối chiếm phần lớn thời gian, cho số thấp giả (0,71×). `runtime_profile.json` giờ có thêm `steady_realtime_factor` và `steady_analysed_fps` (từ khung phân tích đầu tiên tới khung cuối).
2. **Nghiệm thu thủ công VIGIL One qua LAN** với ≥2 máy thí sinh thật: tham gia bằng mã, hình trực tiếp, mất mạng rồi gửi bù, nộp bài, duyệt tín hiệu, xem video cả bài thi. Phần này mới kiểm tra bằng test tự động và mô phỏng.
3. **(Tùy chọn) tăng tốc YOLO** — **đã thử 2026-10-17, giữ 1280.** Trước đó `--imgsz` / `pose_imgsz` chỉ được in ra, YOLO luôn chạy 1280 (đã sửa: `classroom_monitor.detector.pose_detector_for`, dùng cho cả CLI và LIVE). Kết quả 1800 khung `classroom-01`, chế độ tự động:

   | `--imgsz` | Khung phân tích/s | YOLO ms | Ghế có người | Đầu cắt/chu kỳ | Episode |
   |---|---|---|---|---|---|
   | 1280 (mặc định) | 15,9 | 25,6 | **15**/33 | 8,3 | 337 |
   | 960 | 20,7 | 20,9 | **10**/33 | 5,1 | 255 |

   960 nhanh hơn ~30% nhưng mất 1/3 số thí sinh (đầu nhỏ ở cuối phòng) → không dùng. Hướng còn lại: TensorRT, tiền xử lý trên GPU.
4. **Chất lượng AI** vẫn như báo cáo cũ (precision India 2,58%): chỉ dùng để ưu tiên rà soát; cần thêm ground truth trước khi công bố số liệu.
5. **Nợ kỹ thuật biết trước**: ~~import `classroom_monitor.pipeline` đầu tiên gây vòng import~~ (đã sửa 2026-10-17: `DemoVideoConfig` chỉ import khi kiểm tra kiểu; có test); ruff báo ~200 lỗi cũ (chủ yếu import thừa) — không thuộc đợt này.
6. **Mở PR** `feat/one-server-analysis` → nhánh tích hợp khi chủ dự án đồng ý.

## 5. Chạy và kiểm thử

```powershell
git pull origin feat/one-server-analysis
venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-hpe.txt
python vigil.py                 # mọi thứ (DB làm việc data/vigil_proctoring.db)
python vigil.py --demo          # DB demo
python vigil.py --hub-only      # chỉ Hub + web
python vigil.py --one-only --hub http://<ip-hub>:8000   # chỉ app thí sinh trên máy khác
python vigil.py --lan --demo-token <bí-mật>             # mở cho máy khác trong LAN
venv\Scripts\python.exe scripts\setup_exam.py --help     # tạo giám thị / kỳ thi / thí sinh
venv\Scripts\python.exe -m pytest -q                     # 516 test
```

Classroom LIVE cần ghế đã hiệu chỉnh ở `/calibration` (lưu trong DB). Kịch bản demo: `docs/DEMO_RUNBOOK_VIGIL_HUB.md`. Hướng dẫn thí sinh: `docs/HUONG_DAN_HOC_SINH_VIGIL_ONE.md`.

**Tài khoản:** tài khoản giám thị và kỳ thi demo được tạo bằng `setup_exam.py` trên DB làm việc ở máy chủ dự án. **Mật khẩu không nằm trong repo** — xin trực tiếp từ chủ dự án; không commit mật khẩu, DB, video hay bằng chứng.

## 6. Biến môi trường

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `VIGIL_AUTH_MODE` | — | `required` để bắt đăng nhập (cả WebSocket) |
| `VIGIL_DEMO_TOKEN` | — | Token khi mở Hub ra LAN |
| `VIGIL_ONE_ANALYSIS` / `VIGIL_ONE_ANALYSIS_WORKERS` | 1 / 2 | Phân tích One trên Hub, số worker |
| `VIGIL_HPE_FP16`, `VIGIL_YOLO_FP16` | 1 | 0 = float32 |
| `VIGIL_CUDNN_BENCHMARK` | 0 | 1 = gom lô cố định + cuDNN benchmark (không có lợi trên RTX 4060) |
| `VIGIL_ONE_DATA`, `VIGIL_ONE_EVIDENCE_ROOT`, `VIGIL_DEMO_RUNS_ROOT`, `VIGIL_DEMO_FINAL_ROOT` | thư mục `data/` | Nơi lưu dữ liệu, bằng chứng, kết quả chạy |

## 7. Bản đồ mã cho đợt này

| Đường dẫn | Vai trò |
|---|---|
| `vigil.py`, `vigil_build.py` | Điểm vào duy nhất, dấu vân tay bản build |
| `storage/one_analysis.py`, `storage/one_live.py` | Phân tích One trên Hub, luồng trực tiếp |
| `api/routes/` | API (One, demo Classroom, proctor, admin) |
| `exam_monitor/candidate_app.py`, `live_stream.py`, `hub_client.py` | App thí sinh chỉ quay và gửi |
| `classroom_monitor/demo/runtime.py` | Classroom LIVE trên web |
| `classroom_monitor/demo/runner.py`, `scripts/run_demo_video.py` | Chạy CLI / đo hiệu năng |
| `classroom_monitor/video_io.py` | Đọc/ghi khung nền, `StageTimer`, `RealtimePacer` |
| `classroom_monitor/head_pose_provider.py`, `detector.py` | SixDRepNet, YOLO-Pose (FP16) |
| `dashboard/` | Website (Tailwind biên dịch sẵn, chạy không cần Internet) |
| `tests/test_classroom_speed.py`, `test_trust_boundary.py`, `test_one_live.py`, `test_single_entry.py` | Test chính của đợt này |

## 8. Lưu ý cho bên nhận

- Đo hiệu năng trên laptop: cắm sạc, chế độ hiệu năng cao, đóng ứng dụng nặng; một lần đo 13 FPS từng là do máy chưa lên xung chứ không phải code.
- Mỗi lần đo dùng `--output` riêng (tham số của `run_demo_video.py`), nếu không `runtime_profile.json` bị ghi đè.
- Container đám mây không có GPU: số hiệu năng chỉ đo được trên máy có RTX.
- Giữ nguyên tắc: tín hiệu ≠ kết luận; bookmark giám thị tách khỏi sự cố AI; SHA-256 chỉ là kiểm tra toàn vẹn file.

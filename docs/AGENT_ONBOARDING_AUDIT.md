# VIGIL AI — Agent Onboarding Audit

**Ngày:** 2026-10-06 · **Repo:** `laser_eyes` · **Branch:** `feat/proctor-support-implementation` · **Commit:** `0f975b5`
**Phạm vi:** Toàn bộ pipeline VIGIL Classroom (SRS v2) — CLI, web runtime, core inference, seat/calibration, temporal/pattern/risk, renderer, evidence, test, benchmark. VIGIL One (`exam_monitor/`) chỉ được kiểm kê ở mức model/entry point.
**Nguyên tắc:** Chỉ đọc, trace, đo. Không sửa code, không đổi threshold, không commit.

Mọi số liệu trong báo cáo này được đo tại HEAD `0f975b5` trên RTX 4060 Laptop 8 GB, driver 610.47, Python 3.11.9, Torch 2.6.0+cu124, Ultralytics 8.3.253. Output benchmark được ghi vào scratchpad, không ghi vào `data/demo_final`.

---

# 1. Project Overview

VIGIL AI là nguyên mẫu hỗ trợ giám thị. AI tạo tín hiệu quan sát, episode, pattern, review priority và evidence; con người ra quyết định cuối cùng (`PENDING / CONFIRMED / REJECTED / INCONCLUSIVE`). Repo chứa hai sản phẩm tách biệt:

| Sản phẩm | Entry point | Perception |
|---|---|---|
| **VIGIL Classroom** (SRS v2) | `scripts/run_prototype.ps1` → `scripts/run_demo_all_videos.py` (CLI); `server.py` → `api.main` → `DemoRuntime` (web) | YOLO11n-Pose + SeatManager + 6DRepNet |
| **VIGIL One** (tên cũ trong code: Local) | `main.py` → `exam_monitor/` (Tkinter) | MediaPipe FaceLandmarker, EfficientDet-Lite0, webrtcvad |

Dữ liệu demo gồm hai video: `demo_video/student_classroom.mp4` (640×352, 20 FPS, 219 frame, 10.95 s) và `demo_video/india_classroom.mp4` (1280×720, 30 FPS, 2153 frame, 71.8 s). Ground truth chỉ có cho India: 13 record, trong đó 9 head-turn record hợp lệ. Không có GT cho Student.

---

# 2. Current Architecture

```
classroom_monitor/
  pipeline/srs_v2_pipeline.py   ← semantic core dùng chung CLI + web (SRSv2Pipeline.process_frame)
  detector.py                   ← PoseClassroomDetector (YOLO-Pose) [+ legacy ClassroomDetector]
  seat_manager.py               ← Seat ROI polygon, person→seat mapping (không có tracker)
  scene_context.py              ← SceneProfile/SeatGraph/DeskGeometry/capabilities, neighbor auto-infer
  head_pose_provider.py         ← SixDRepNet / PoseHeuristic provider, HeadCropExtractor
  observation_extractor.py      ← RawObservation (yaw, pitch, torso lean, wrist zone, occupancy, count)
  temporal_episode_engine.py    ← CANDIDATE→ACTIVE→ENDING→ENDED, hysteresis, grace
  behavior_pattern_engine.py    ← REPEATED_NEIGHBOR_GLANCE, NEIGHBOR_ORIENTED_LEAN, SEAT_LEFT, MULTI_PERSON_DWELL, BELOW_DESK
  seat_risk_tracker.py          ← risk 0–100, NORMAL/OBSERVE/SUSPICIOUS/FLAGGED/COOLDOWN, incident
  video_buffer.py               ← ring buffer ±5 s → MP4 clip (mp4v)
  demo/{config,runner,runtime,renderer,exporter}.py ← adapters CLI/web, HUD, export JSON
  config.py                     ← ClassroomConfig (phần lớn legacy) + resolve_runtime_config (active)
api/ storage/ dashboard/ proctor_support/   ← web API, SQLite, UI, bookmark workflow
configs/scenes/{india,student}_classroom.yaml ← calibration YAML
```

---

# 3. Actual Runtime Pipeline

## 3.1 Prototype command được trace

```powershell
.\scripts\run_prototype.ps1 -Show -SkipTests
```

`run_prototype.ps1` kiểm CUDA và video, rồi gọi `scripts/run_demo_all_videos.py --output-root data/demo_final --head-provider sixdrepnet [--show]`. Script này chạy **India rồi Student** tuần tự qua `run_demo_pipeline(config)` (`classroom_monitor/demo/runner.py:153`). Nếu bỏ `-SkipTests`, script chạy `pytest tests/ -q` trước.

## 3.2 Call chain thực tế (mỗi frame, stride = 1)

```text
cv2.VideoCapture.read()                                   runner.py:303
  ↓ timestamp_ms = (frame_idx-1)*1000/fps                 runner.py:315
EvidenceVideoBuffer.add_frame (copy raw frame → ring 5 s) runner.py:318
  ↓
SRSv2Pipeline.process_frame                               srs_v2_pipeline.py:96
  ├─ PoseClassroomDetector.detect                         detector.py:420
  │    YOLO11n-pose, classes=[0], conf=0.20, imgsz=1280 (LUÔN LUÔN — xem D5)
  │    model(...) chứ không phải model.track(): KHÔNG có tracking
  ├─ SeatManager.map_detections_to_seats                  seat_manager.py:284
  │    anchor = đáy bbox − 15% chiều cao (fallback: tâm bbox), point-in-polygon,
  │    ROI "sâu nhất" thắng. >1 người → MULTIPLE_PERSON. Ngoài mọi ROI → "roaming".
  ├─ HPE scheduler: chỉ seat OCCUPIED + có SeatContext, mỗi 200 ms → estimate_batch (6DRepNet)
  ├─ ObservationExtractor.extract cho MỌI seat            observation_extractor.py:86
  │    cache HPE ≤ 600 ms; nếu cache rỗng/hết hạn → GỌI 6DRepNet ĐƠN LẺ (fallback, xem P0-2)
  ├─ TemporalEpisodeEngine.process_observations theo seat temporal_episode_engine.py:161
  ├─ BehaviorPatternEngine.ingest_episodes theo seat       behavior_pattern_engine.py:102
  └─ SeatRiskTracker.update_seat theo seat → ClassroomEvent seat_risk_tracker.py:144
  ↓
runner: incident → trigger_clip (±5 s raw) + imwrite snapshot = frame HIỆN TẠI
  ↓
DemoHUDOverlayRenderer.render_frame → result.mp4 (+ cv2.imshow khi -Show)
  ↓ EOF
pipeline.flush → SHA-256 clip → GT strict match (chỉ India) → export_demo_artifacts (JSON)
```

So với sơ đồ mẫu: **không có stage Tracking**. Danh tính người = seat. `track_id` của mọi event luôn là 0, và tên clip luôn có `_T0_`.

## 3.3 Web runtime (`server.py`)

`DemoRuntime._worker_live` dùng cùng class `SRSv2Pipeline`, nhưng **đầu vào khác CLI**:

| Khía cạnh | CLI (`runner.py`) | Web LIVE (`runtime.py`) |
|---|---|---|
| Seat polygon | **DB** nếu phòng đã có seat; chỉ seed từ preset khi DB rỗng (`runner.py:110`) | **YAML** (`runtime.py:681`) |
| SeatGraph / context | YAML | YAML |
| Timestamp | `(frame_idx-1)/fps` | `frame_idx/fps` |
| Snapshot | frame tại thời điểm trigger | `event.evidence_frame` = peak frame của risk tracker (có thể là của incident trước, xem P1-4) |
| Output | thư mục output (purge `*.json/*.mp4/*.csv` + evidence) | `data/demo_runs/<run>` rồi **copy đè, không purge** vào `data/demo_final/<preset>` |

Web REPLAY không chạy inference. Nó phát lại `data/demo_final/<preset>/result.mp4` và `events.json` theo timestamp (`runtime.py:1032`).

## 3.4 Import / dead code (đồ thị import tĩnh)

- **Active Classroom:** `pipeline`, `detector.PoseClassroomDetector`, `seat_manager`, `scene_context`, `head_pose_provider`, `observation_extractor`, `temporal_episode_engine`, `behavior_pattern_engine`, `seat_risk_tracker`, `video_buffer`, `async_evidence_writer` (`compute_file_sha256`), `demo/*`, `evaluation/temporal_matcher`, `contracts`, `evidence_playback` (web).
- **Được import (qua `classroom_monitor/__init__.py`) nhưng KHÔNG được gọi trong SRS v2:** `behavior_tracker`, `event_engine`, `live_event`, `room_context`, `score_accumulator`, `spatial_matcher` (chỉ dùng `compute_bbox_iou`; Kalman tracker không dùng), `video_processor`, `detector.ClassroomDetector`.
  - Các module này chỉ chạy qua `/api/v1/inference` (HTTP 410 trừ khi `VIGIL_ENABLE_LEGACY_INFERENCE=1`) và qua `python -m classroom_monitor`.
- **Không ai import (DEAD):** `behavior_signals.py`, `rtsp_reader.py`, `worker_node.py`.
- **Double inference:** YOLO không bị chạy đôi. **6DRepNet bị chạy đôi**: batch theo lịch cộng với fallback đơn lẻ mỗi frame (P0-2).
- **Duplicate event:** pattern được dedupe theo tập episode ID. Tuy vậy, cùng episode cũ vẫn được dùng lại trong pattern mới sau cooldown 10 s, chỉ cần thêm một episode mới (P2).
- **Historical contamination:** có, xem §12 P0-1, P1-4, P1-6.

---

# 4. Models

| Model | File / nguồn | Framework | Input | Output | Tần suất | Thiết bị / batch | Gọi ở đâu | Trạng thái |
|---|---|---|---|---|---|---|---|---|
| YOLO11n-Pose | `yolo11n-pose.pt` (repo root, 6.3 MB) | Ultralytics 8.3.253 / PyTorch | frame BGR, letterbox **imgsz=1280**, conf 0.20, class 0 | bbox + 17 COCO keypoint (x, y, conf) | mọi frame | CUDA auto, FP32, batch 1 | `detector.py:431` ← `srs_v2_pipeline.py:100` | **ACTIVE** |
| 6DRepNet (RepVGG-B1g2, `6DRepNet_300W_LP_AFLW2000.pth`) | **Tự tải** từ cloud.ovgu.de vào `~/.cache/torch/hub` (không có trong repo) | `sixdrepnet` / PyTorch | crop đầu từ keypoint 0–4, pad 45% (fallback: 28% trên của bbox, quality cố định 0.40) → Resize 224 + CenterCrop 224 | rotation matrix → pitch/yaw/roll | Thiết kế 5 Hz/seat; **đo thực tế 73–233 Hz** | cuda:0, FP32, batch TB **1.0–1.37** | `head_pose_provider.py:411` ← `srs_v2_pipeline.py:133` (lịch) và `observation_extractor.py:171` (fallback) | **ACTIVE** |
| PoseHeuristic head provider | không weight (hình học keypoint) | numpy | keypoint | yaw/pitch | khi `--head-provider pose_heuristic` | CPU | `head_pose_provider.py:127` | tuỳ chọn; **quy ước dấu yaw ngược 6DRepNet** (P1-1) |
| `models/classroom_best.pt` | YOLO 5 lớp "cheating" | Ultralytics | frame | 5 class | — | — | `ClassroomDetector`, `video_processor`, `__main__` | LEGACY (route 410) |
| `yolo11n.pt`, `yolo11s-pose.pt`, `yolo11m-pose.pt`, `weights/yolo26n.pt` | repo | — | — | — | — | — | không được tham chiếu | DEAD artifact |
| `yolo12s.pt`, `yolov8n.pt` | repo / fallback | — | — | — | — | — | `classroom_training/` | chỉ dùng cho training |
| VIGIL One: `face_landmarker.task`, `efficientdet_lite0.tflite`, webrtcvad | `exam_monitor/assets` | MediaPipe Tasks | webcam | face/iris landmarks, object, VAD | per-frame | CPU | `exam_monitor/engine.py`, `audio.py` | ACTIVE (VIGIL One) |

**Head pose (6DRepNet) chi tiết**

- **Active:** có (`run_manifest`: `head_orientation: REAL`).
- **Batching:** chỉ áp dụng cho seat `OCCUPIED` có SeatContext. Seat `MULTIPLE_PERSON` và seat không có context rơi vào fallback đơn lẻ mỗi frame.
- **Cache:** 600 ms (`cache_max_age_ms`). Cache lưu cả estimate không hợp lệ (quality 0).
- **Smoothing:** median w = 3 tính **theo frame** (`temporal_episode_engine.py:138`). Vì một mẫu HPE được lặp lại 4–6 frame, median gần như không lọc nhiễu HPE. Một spike tồn tại ít nhất 200 ms.
- **Lỗi forward bị nuốt:** `logger.debug` (`head_pose_provider.py:453`) → estimate thành UNKNOWN, không có cảnh báo health.

**Pose**

- 17 keypoint COCO.
- Ngưỡng keypoint 0.30 (wrist, shoulder, head crop).
- Thiếu keypoint thì wrist trả `UNKNOWN`, torso lean bị bỏ, head crop fallback sang bbox.
- Detector vẫn gán `class_name` legacy ("side peeking", "phone using") theo heuristic; downstream không dùng nhưng có thể gây hiểu lầm khi debug.

**Tracking**

Không có. ID chính là seat. Seat state không reset khi người trong seat đổi (giám thị ↔ học sinh). Khi bị che, seat chuyển `OCCLUDED` trong 4 s, sau đó `UNKNOWN`, rồi `EMPTY` sau hơn 5 s.

---

# 5. Seat / Classroom System

## 5.1 Hai bản calibration song song (P0-1)

| Nguồn | Student | India | desk_y / baseline / neighbor | Ai dùng |
|---|---|---|---|---|
| `configs/scenes/*.yaml` (+ preset trùng lặp trong `demo/config.py`) | 12 seat `SEAT-STUDENT-01..12` | 21 seat `SEAT-ROOM-CALIB-01-01..21` | có desk_y và baseline; neighbor tự suy ra | Web LIVE (polygon + context); CLI (chỉ context) |
| SQLite `data/vigil_proctoring.db` bảng `seats` (vẽ qua calibration UI; India sửa lần cuối 2026-09-29) | **14** seat `SEAT-ROOM-STUDENT-01-01..14` | 21 seat `SEAT-ROOM-CALIB-01-02..22`, **polygon khác hẳn** | **không có** (context_json `{}`) | CLI (polygon) |

Hệ quả, đã đo bằng runtime:

- **Student, CLI với DB hiện tại:** không seat nào khớp SeatGraph YAML.
  - `scheduled_cycles = 0`, 2551 lần forward 6DRepNet đơn lẻ, baseline yaw = 0.
  - Không có neighbor, nên `REPEATED_NEIGHBOR_GLANCE` và `NEIGHBOR_LEAN` không thể xảy ra. Kết quả: 2 incident, đều là MULTI_PERSON_DWELL.
- **Student, CLI với DB mới (clean clone):** 12 seat YAML → 5 incident (khớp tài liệu cũ).
- **India, CLI với DB hiện tại:** code 02–21 trùng tên nhưng **khác vị trí vật lý** → context YAML (baseline, desk_y, neighbor) bị **gắn sai seat**. Seat 22 không có context nên chạy fallback HPE.
- **India GT** được gán nhãn theo seat code của DB. Benchmark strict chỉ có nghĩa khi CLI chạy với DB này; chạy trên clean clone thì cùng code trỏ tới seat khác.
- **Calibration UI** (`dashboard/js/calibration.js`) chỉ ghi polygon vào DB. Nó **không ảnh hưởng tới web LIVE** và không ghi desk_y, baseline hay neighbor.

## 5.2 Chất lượng ROI (diagnostic overlay trên frame thật)

- **Student:** 16 người được phát hiện, chỉ có 12 ROI, học sinh ngồi 2 người/bàn. Seat 02/05/07/09 chứa ≥ 2 người ngay từ frame 1, nên MULTI_PERSON_DWELL ở Student là **artifact của calibration**. Một học sinh ngồi viết ở hàng đầu bị gắn nhãn "ROAMING" vì không ROI nào chứa anchor của em.
- **India (YAML):** trung bình 25.2 người/frame, **9.2 người/frame nằm ngoài mọi ROI** (bị coi là roaming). Seat 08 có > 1 người trong 86% frame mẫu, seat 09 là 74%. ROI YAML lệch rõ so với vị trí học sinh, cột trái gần cửa sổ không có seat.
- **Overlap:** nhiều cặp polygon chồng lấn (Student 01/05, 03/04, 08/09; India 01/04, 02/05). Gán seat theo "độ sâu chuẩn hoá lớn nhất". Người đứng giữa hai seat thì nhảy seat theo dao động bbox.

## 5.3 Neighbor graph, desk, writing / under-desk zone

- **Neighbor:** `auto_infer_neighbors_from_polygons` dùng ngưỡng pixel cố định (x < 350, |Δy| < 100, front Δx < 120), không chuẩn hoá theo độ phân giải. Với Student (640 px), hầu hết seat đều có cả hai neighbor trái và phải, nên điều kiện "phải có neighbor đúng hướng" gần như không lọc gì. Không có `back_neighbor`. Không có YAML nào khai báo neighbor tường minh.
- **Desk:** chỉ có `desk_boundary_y`, tức **một đường ngang**, không có writing-zone polygon, nên capability desk = `DEGRADED`.
  - Vì `contains_wrist_in_writing_zone` (y ≤ desk_y) được kiểm tra trước, `DESK_EDGE` **không thể xảy ra**. Cổ tay lệch 1 px dưới đường kẻ là `UNDER_DESK`.
  - Trên frame thật, tay đang viết trên giấy nằm **dưới** đường desk_y (seat 01 Student).
  - `WRIST_BELOW_DESK` xuất hiện ở **12/12 seat** Student và 50–73 episode ở India.
  - Hiện vô hại chỉ vì capability gating cho risk = 0 và pattern `BELOW_DESK_INTERACTION` cần `ENABLED`.

## 5.4 Học sinh bình thường đang viết có bị đánh nhầm không?

**Có, theo hai đường:**

1. **WRIST_BELOW_DESK** (đã chứng minh trên frame thật). Episode được tạo nhưng chưa cộng risk vì gating.
2. **Cúi viết → pitch lớn → yaw 6DRepNet nhiễu** (góc nhìn trên xuống, crop đầu nhỏ) → episode HEAD_TURN → `REPEATED_NEIGHBOR_GLANCE`. Đây là đường cộng risk thật (§6).

`HEAD_PITCH_DOWN` (pitch ≥ 20°) có trọng số 0 nên bản thân nó an toàn.

---

# 6. Behavior Detection

## 6.1 Episode (`EpisodeType`) — trace từ observation tới risk

| Episode | Observation → điều kiện | Risk / episode | Pattern sử dụng |
|---|---|---|---|
| `HEAD_TURN_LEFT` / `HEAD_TURN_RIGHT` | `HEAD_YAW_RELATIVE` (6DRepNet − baseline seat), median 3; kích hoạt khi \|yaw\| ≥ 28°, nhả khi < 16°; persistence 400 ms; nhả sau 350 ms; grace 1200 ms | +12 × quality | REPEATED_NEIGHBOR_GLANCE |
| `HEAD_PITCH_DOWN` | pitch ≥ 20° (nhả < 12°) | **0** | chỉ là cue phụ của BELOW_DESK |
| `TORSO_LEAN_LEFT` / `TORSO_LEAN_RIGHT` | góc đường vai trong ảnh: atan2(Δy, \|Δx\|) ≥ 15° (nhả 8°) | +14 × quality | NEIGHBOR_ORIENTED_LEAN |
| `WRIST_BELOW_DESK` | bất kỳ cổ tay nào có y > desk_y (conf ≥ 0.3) | 18, nhưng **= 0 khi DEGRADED** (hiện tại luôn như vậy) | BELOW_DESK_INTERACTION (không bao giờ chạy) |
| `MULTI_PERSON_NEAR_SEAT` | > 1 detection có anchor trong ROI | **+20** | MULTI_PERSON_DWELL_NEAR_SEAT |
| `SEAT_EMPTY` | EMPTY sau khi seat từng có người | 0 | SEAT_LEFT |
| `WRIST_WRITING` | — | 0 | enum tồn tại nhưng **không bao giờ được sinh ra** |

## 6.2 Pattern

| Pattern | Điều kiện | Cooldown | Risk |
|---|---|---|---|
| `REPEATED_NEIGHBOR_GLANCE` | ≥ 2 episode HEAD_TURN cùng hướng trong 25 s, kể cả episode đang ACTIVE, và có neighbor ở hướng đó | 10 s | 38 × q × conf × diminishing |
| `NEIGHBOR_ORIENTED_LEAN` | TORSO_LEAN ≥ 1 s và có neighbor ở hướng đó | 8 s | 32 × … |
| `MULTI_PERSON_DWELL_NEAR_SEAT` | MULTI_PERSON episode ≥ 2.5 s | 15 s | 40 × … |
| `SEAT_LEFT` | SEAT_EMPTY ≥ 15 s (tức khoảng 20 s không có detection) | 20 s | 45 × … |
| `BELOW_DESK_INTERACTION` | cần desk capability `ENABLED` (writing-zone polygon) | 12 s | **không bao giờ kích hoạt** với calibration hiện tại |
| `CROSS_SEAT_REACH`, `MUTUAL_ORIENTATION` | — | — | **chỉ là enum, chưa implement** |

**Không tồn tại** behavior `ROAMING_INVIGILATOR`. "ROAMING" chỉ là nhãn renderer cho detection nằm ngoài mọi ROI, không có logic nào phía sau. Docstring runner ghi "Isolated Roaming Actor / Invigilator Filtering", nhưng thực tế không có bộ lọc giám thị nào ngoài "nằm ngoài ROI".

## 6.3 Phân bố đo được (CLI, HEAD 0f975b5)

| Run | Episodes | Patterns | Incidents |
|---|---|---|---|
| Student, DB hiện tại (14 seat DB) | 63 | 4 MULTI_DWELL | 2 (2 MULTI_DWELL) |
| Student, DB mới (12 seat YAML) | 89 (25 HEAD_TURN_LEFT, 11 HEAD_TURN_RIGHT, 17 WRIST_BELOW_DESK, 7 MULTI) | 9 GLANCE, 5 MULTI_DWELL, 2 LEAN | 5 (4 MULTI_DWELL, 1 GLANCE) |
| India, DB hiện tại | 599 | 87 GLANCE, 29 MULTI_DWELL, 16 LEAN, 1 SEAT_LEFT | **28** (20 MULTI_DWELL, 5 GLANCE, 3 LEAN) |
| India, DB mới | 452 | 67 GLANCE, 17 MULTI_DWELL, 5 SEAT_LEFT, 3 LEAN | **18** (15 MULTI_DWELL, 2 LEAN, 1 GLANCE) |

Student với YAML: **9/12 seat** sinh `REPEATED_NEIGHBOR_GLANCE` trong 11 giây.

---

# 7. Risk / Incident System

- **Score:** decay 2.5/s; increment một lần cho mỗi episode ID; pattern có diminishing returns 1/(1 + 0.35n); bonus +3/s khi head turn và torso lean cùng ACTIVE; recidivism +10 một lần.
- **Ngưỡng:** 30 / 60 / 80. Khi ≥ 80 thì emit `ClassroomEvent`, **ngay lập tức** chuyển `COOLDOWN` 5 s và **reset score về 45** (`post_event_reset_score`).
  - Vì vậy `FLAGGED_FOR_REVIEW` không bao giờ là trạng thái "đứng yên" mà renderer quan sát được.
  - Trong COOLDOWN score vẫn tích luỹ.
- **Merge:** cửa sổ 15 s, chỉ khi cùng `primary_pattern`.
- **Nhãn incident (`primary_pattern`) = `profile.peak_pattern`:** pattern có increment lớn nhất **từ đầu phiên**, không bao giờ reset.
  - **Đo trên India: 8/28 incident (29%) mang nhãn của pattern không hề xảy ra ở seat đó trong 15 s trước trigger.** Ví dụ `SEAT-…-03` @ 40.2 s bị gán MULTI_PERSON_DWELL trong khi 15 s trước đó không có pattern nào.
  - `peak_detection` / `peak_frame_image` chỉ cập nhật khi vượt **peak toàn phiên**, nên bbox và ảnh evidence của incident thứ 2 trở đi có thể là của incident trước (web dùng ảnh này làm snapshot).
- `frame_index = int(timestamp_ms / 33.33)` được hardcode 30 FPS, nên **sai với Student (20 FPS)**.
- `track_id` luôn 0; `duration_seconds` luôn 0; `start_frame`/`end_frame` không được set.
- Severity: HIGH nếu score ≥ 85 hoặc recidivist; ngược lại MEDIUM. Ở Student (DB mới), cả 5 incident đều HIGH, với `peak_risk_score` 88–100 (bị clip ở 100).

---

# 8. Renderer

`DemoHUDOverlayRenderer` đọc state từ core nhưng **tự định nghĩa cửa sổ và ngưỡng riêng**:

1. **"REVIEW REQUIRED" trong khi core đang COOLDOWN** — đã quan sát trên frame thật:
   - Card hiển thị "S02: REVIEW REQUIRED … (Score: 44)" và "S07 … (Score: 52)".
   - Renderer giữ card trong **6000 ms sau event** (`renderer.py:164`), trong khi core COOLDOWN 5000 ms và score đã reset về 45.
   - Từ 5 s tới 6 s, core có thể đã ở NORMAL/OBSERVE trong khi renderer vẫn báo REVIEW.
   - Score in trên card là score hiện tại sau reset, không phải score lúc trigger.
2. Nhánh `elif state == FLAGGED or score >= 80` (`renderer.py:167`): khi đang COOLDOWN mà score tích luỹ ≥ 80 thì renderer hiện "REVIEW REQUIRED" **dù core không emit incident**. Score ≥ 60 lúc COOLDOWN thì hiện amber "SUSPICIOUS".
3. Top HUD "x/12 Occ" chỉ đếm `OCCUPIED`; seat `MULTIPLE_PERSON` bị loại. Đã quan sát "6/12 Occ" khi có khoảng 16 học sinh.
4. Tia hướng đầu ở debug overlay được **tính lại từ keypoint heuristic** (`renderer.py:298`), không phải từ 6DRepNet mà core dùng. Nó có ngưỡng 28° riêng và **quy ước dấu ngược** (P1-1).
5. Badge "SUSPICIOUS" ở debug hiển thị `seat_act_eps[0].episode_type`, tức episode đầu tiên bất kỳ (có thể là WRIST_BELOW_DESK hay HEAD_PITCH_DOWN với trọng số 0), không phải nguyên nhân thật.
6. Dòng debug "6DRepNet Subsampled" thực ra là latency của toàn stage head + observation.
7. Seat ID (S01…), timestamp, behavior và severity trên ticker lấy đúng từ event. Behavior có thể là nhãn stale (§7).
8. Web UI (`dashboard/js/demo.js:467,637`) dùng `peak_risk_score || risk_score || 75/80`, tức **tự bịa score mặc định** khi thiếu hoặc bằng 0.

Test `tests/test_renderer_semantics.py` (UI4: "COOLDOWN alone is NOT REVIEW REQUIRED") PASS vì chỉ kiểm COOLDOWN *không có event gần đây*. Nó không bắt được trường hợp 1.

---

# 9. Demo Pipeline

| Lệnh | Tác dụng thật |
|---|---|
| `.\scripts\run_prototype.ps1 [-Show] [-SkipTests]` | pytest (nếu không skip) → CLI India + Student → `data/demo_final/{india,student}` + `final_execution_manifest.json` |
| `scripts\run_demo_video.py --video student\|india [--output …]` | CLI một video. **`--imgsz` không có tác dụng** (D5) |
| `.\scripts\run_demo_system.ps1 -Preset … -Mode replay\|live` | pytest → seed DB → `server.py --host 0.0.0.0` (**bind LAN mà không có `--demo-token`**; cần kiểm tra server có chặn hay không) |
| `server.py` + `/demo` | Web LIVE (YAML seats) hoặc REPLAY (phát lại `data/demo_final/<preset>`) |
| `run_server.bat`, `run_tests.bat` | gọi `.\.venv\...`, nhưng repo dùng `venv\` → **hỏng** |

## Output contamination (§11 yêu cầu)

| Tình huống | Kết quả kiểm chứng |
|---|---|
| CLI chạy lại vào cùng output dir | Purge `*.json/*.mp4/*.csv` và file evidence → **sạch**. File khác (vd `clean_sample_frame.jpg`) vẫn còn |
| Web LIVE | Copy vào `data/demo_final/<preset>` **không purge**. `india/` hiện có `events.json = []` (30/09) nhưng vẫn giữ `gt_comparison_strict.csv` và `ai_unmatched_head_episodes.csv` từ 21/09 cùng **116 file evidence mồ côi** |
| `data/demo_runs/` | 172 thư mục, 329 MB, không bao giờ dọn |
| **Test suite** | **Ghi vào DB thật và `data/` thật**, không có `conftest.py` để cô lập. Lần chạy pytest trong audit này đã tạo 3 thư mục `data/demo_runs/DEMO-*-student` và **ghi đè `data/demo_final/student/`** bằng run khoảng 3 frame, 0 event. Web REPLAY Student sau đó sẽ phát gói rỗng. `run_demo_system.ps1` chạy pytest ngay trước khi mở server, nên REPLAY mặc định bị hỏng |
| DB SQLite | Seat, session và event tích luỹ qua mọi lần chạy; seat DB thay đổi hành vi CLI (P0-1) |
| Trong cùng một run | `peak_pattern` / `peak_frame` không reset (P1-4) |

---

# 10. Test Suite

```text
Lệnh:     venv\Scripts\python.exe -m pytest -p no:cacheprovider -rA
Total:    271    Passed: 271    Failed: 0    Skipped: 0    Errors: 0
Warnings: 1 (SAWarning trùng identity DetectionEvent — test_demo_system_integration.py:586)
Runtime:  17.98 s
```

Khớp tài liệu bàn giao (271 pass). Marker gần như không được dùng: chỉ 3 `integration` và 1 `unit` (pytestmark của `test_prototype_remediation.py`). Phân loại theo nội dung:

| Nhóm | File |
|---|---|
| Unit — core SRS v2 | `test_srs_v2_temporal`, `test_srs_mvp_p0`, `test_wrist_unknown_safety`, `test_desk_gating`, `test_pattern_correctness`, `test_incident_risk_hardening`, `test_head_pose_ab`, `test_head_pose_realtime_hardening`, `test_hpe_pipeline_hardening`, `test_telemetry_counters`, `test_evaluation_strict_matcher`, `test_scene_calibration_parser`, `test_config_unification`, `test_renderer_semantics` |
| Unit — legacy | `test_classroom_core` (behavior_tracker, ClassroomDetector) |
| VIGIL One | `test_core` (exam_monitor) |
| Integration (API, DB, storage) | `test_storage_api`, `test_proctor_support`, `test_human_data_workbench`, `test_incident_output_sync`, `test_demo_runner_infrastructure` |
| Regression | `test_prototype_remediation`, `test_prototype_hardening`, `test_ictu_2026_prototype_final` |
| Prototype / real model | `test_demo_system_integration` (khởi động `DemoRuntime` LIVE với YOLO + 6DRepNet thật, `max_frames` 5–500, trên DB và `data/` thật) |

**Khoảng trống quan trọng:**

- Không test nào cho thấy CLI và web cho kết quả khác nhau (D2).
- Không test nào kiểm seat code DB khớp SeatGraph.
- Không test nào đếm số forward 6DRepNet đơn lẻ trong pipeline thật.
- Không test nào kiểm dấu yaw trên ảnh thật.
- Test WR6 "writing priority" chỉ kiểm một cổ tay; không phủ trường hợp *một tay viết + một tay thấp* (khi đó episode vẫn kích hoạt).
- Không test nào bắt card REVIEW REQUIRED trong COOLDOWN.

**Test PASS nhưng video sai:** đã xác nhận (§5, §7, §8).

---

# 11. Performance

| Run (CLI, có lưu evidence, không `-Show`) | FPS xử lý | Realtime | YOLO ms | Head + obs ms | Forward 6DRepNet (single / tổng) | Batch TB | HPE hiệu dụng | GPU util TB | VRAM peak |
|---|---|---|---|---|---|---|---|---|---|
| Student, DB hiện tại | 8.1 | 0.40× | 26.3 | **91.8** | **2551 / 2551** (`scheduled_cycles = 0`) | 1.0 | 233 Hz (thiết kế 5) | 35% | 3.2 GB |
| Student, DB mới | 15.8 | 0.79× | 23.1 | 33.8 | 669 / 800 | 1.3 | 73 Hz | — | — |
| India, DB hiện tại | 11.6 | 0.39× | 17.9 | 50.1 | **10774 / 12475** | 1.17 | 174 Hz | 37% | 3.4 GB |
| India, DB mới | 14.3 | 0.48× | 19.7 | 30.5 | 3970 / 5676 | 1.37 | 79 Hz | — | — |

- Số seat: Student 12 (YAML) / 14 (DB); India 21. Người phát hiện mỗi frame: Student khoảng 13–16, India khoảng 25.
- RAM tiến trình India khoảng **2.3 GB**: ring buffer 5 s copy full-frame, khoảng 150 × 2.7 MB, cộng thêm các clip job.
- India nhanh hơn tài liệu cũ (7.84 FPS); cần ghi rõ commit và phần cứng khi công bố.
- **Bottleneck là pattern N người × N frame:**
  - Fallback 6DRepNet đơn lẻ mỗi frame cho seat MULTIPLE_PERSON hoặc thiếu context.
  - Ngoài ra, `pattern_engine.ingest_episodes` được gọi cho từng seat với **toàn bộ** `completed_episodes` mỗi frame. Độ phức tạp O(seats × episodes) tăng tuyến tính theo thời lượng phiên; nhỏ với video 72 s nhưng là rủi ro cho live dài.
  - `pose_imgsz = 640` cho Student bị bỏ qua (luôn 1280, upscale video 640×352).
- GPU chỉ dùng khoảng 35%: pipeline tuần tự CPU/GPU, batch 1.

---

# 12. Known Problems

## P0 — làm sai kết quả demo / benchmark

| ID | Vấn đề | Bằng chứng | Root cause |
|---|---|---|---|
| **P0-1** | Hai calibration seat lệch nhau; CLI và web dùng seat khác nhau; CLI ghép context YAML vào polygon DB | Log "SeatManager loaded 14 active seats" (Student CLI); Student 2 incident (DB) vs 5 (DB mới); India 28 vs 18; GT TP 8 vs 5 | `runner.py:110` ưu tiên DB; `runtime.py:681` dùng YAML; seat code trùng nhưng khác vị trí; `get_context` trả None hoặc context sai |
| **P0-2** | 6DRepNet chạy ngoài lịch mỗi frame, batch 1 | Telemetry: 669–10774 single call, 73–233 Hz | `observation_extractor.py:168-177` fallback khi không có `precomputed_head_estimate`; scheduler bỏ qua MULTIPLE_PERSON và seat không có context |
| **P0-3** | MULTI_PERSON_DWELL chiếm đa số incident (Student 4/5, India 15/18 và 20/28) do ROI, không phải do hành vi | Overlay: 16 người / 12 ROI; India seat 08 có > 1 người 86% frame; 9.2 người/frame ngoài ROI | ROI không khớp bố cục (2 người/bàn, ROI lệch); anchor-in-polygon không có tracking |
| **P0-4** | Test suite làm bẩn DB và gói replay thật | `data/demo_final/student` bị ghi đè lúc 05:15 trong audit này | Không có conftest/tmp DB; test gọi `DemoRuntime.start` thật, worker copy vào `data/demo_final` |

## P1 — sai semantic / false positive / evidence

| ID | Vấn đề | Bằng chứng | Root cause |
|---|---|---|---|
| **P1-1** | Dấu yaw 6DRepNet ngược hướng ảnh: "RIGHT" (yaw > 0) = mặt quay về **trái ảnh**, nhưng pattern map tới neighbor **phải ảnh** → `target_neighbor_id` sai; heuristic provider và debug ray dùng dấu ngược lại | 172 mẫu thật: r = −0.62, 79% trái dấu (cần xác nhận lại bằng frame gán nhãn tay) | `head_pose_provider.py:437` lấy `canonical_yaw = raw_yaw`; `behavior_pattern_engine.py:224-237` |
| **P1-2** | REPEATED_NEIGHBOR_GLANCE quá dễ kích hoạt | 9/12 seat Student trong 11 s; India 67–87 pattern; GT strict P = 2.4–2.6% (FP 206–294 trên 9 GT) | ≥ 2 episode/25 s; episode ACTIVE cũng được đếm; episode vỡ mảnh do nhiễu hoặc grace tạo thêm "lần liếc"; neighbor gate gần như luôn đúng; yaw 6DRepNet trên đầu nhỏ, cúi sâu, nhìn từ trên xuống |
| **P1-3** | Renderer "REVIEW REQUIRED" khi core đang COOLDOWN; score hiển thị 44–52 | Frame `result.mp4` t = 4.4 s | Cửa sổ 6 s riêng + nhánh `score >= 80` trong renderer |
| **P1-4** | Nhãn incident và evidence stale | India 8/28 incident sai nhãn; web snapshot = peak frame toàn phiên | `peak_pattern`, `peak_detection`, `peak_frame_image` không reset sau incident |
| **P1-5** | Crop fallback (28% trên của bbox, quality 0.40 > ngưỡng 0.35) đưa vai/thân vào 6DRepNet | code `head_pose_provider.py:252-263` | Quality fallback hằng số vượt ngưỡng |
| **P1-6** | Web LIVE copy vào `data/demo_final` không purge → CSV và evidence mồ côi; REPLAY dùng gói có thể là run test | `india/`: 116 evidence vs 0 event | `runtime.py:1004-1014` |
| **P1-7** | Ở seat MULTIPLE_PERSON, detection "chính" là detection có confidence cao nhất (có thể là giám thị) → head pose và hành vi của người khác bị gán cho seat | `seat_manager.py:348` | Không có tracking hay ưu tiên người ngồi |
| **P1-8** | WRIST_BELOW_DESK sinh ra khi đang viết, hoặc một tay viết + một tay để đùi | 12/12 seat Student; overlay | Một đường desk_y ngang; `is_active` ưu tiên hơn `is_writing` (`temporal_episode_engine.py:361-376`). Hiện risk = 0 nhờ gating; sẽ thành FP nếu gating thay đổi |

## P2 — nợ, độ chính xác metadata, vận hành

- `pose_imgsz` / `--imgsz` không được truyền vào detector (D5).
- `frame_index` hardcode 30 FPS; `track_id = 0`; `duration_seconds = 0`.
- Pattern dùng lại episode cũ sau cooldown.
- Neighbor threshold tính bằng pixel tuyệt đối.
- Score mặc định 75/80 bịa trên web UI.
- `.bat` trỏ `.venv`.
- 6DRepNet weight tải từ internet lúc chạy.
- Lỗi forward 6DRepNet bị nuốt ở mức DEBUG.
- Ring buffer evidence copy full-frame (RAM).
- `ClassroomConfig` chứa ngưỡng chết (32°, 500 ms, merge gap) dễ gây hiểu lầm so với giá trị runtime (28°, 400 ms).
- `run_demo_system.ps1` bind `0.0.0.0`.
- SeatManager gán `SEAT_LEFT` khi detector bỏ sót người ngồi hàng sau trong khoảng 20 s (India: 1–5 pattern SEAT_LEFT).

---

# 13. Confirmed Working Components

- Fail-closed khi không có YOLO; mock cần cờ tường minh. Manifest ghi `inference_mode: REAL`, `head_orientation: REAL`.
- YOLO11n-Pose trên GPU hoạt động ổn định, 18–26 ms/frame.
- Batch 6DRepNet theo lịch hoạt động khi seat có context (`scheduled_cycles` khoảng 1700 ở India, batch tối đa 10–12).
- Temporal engine: hysteresis, persistence, grace, EOF flush hoạt động đúng theo đặc tả (episode không spam theo frame).
- Gating: `HEAD_PITCH_DOWN` = 0 risk; `WRIST_BELOW_DESK` = 0 risk khi DEGRADED; `BELOW_DESK_INTERACTION` bị khoá khi không có polygon. Gating hiện **đang chặn được** FP từ wrist.
- Dedupe pattern theo evidence identity; merge incident cùng loại trong 15 s.
- Evidence clip ±5 s (raw, mp4v) và SHA-256 được tạo cho mọi incident. CLI snapshot là frame lúc trigger.
- CLI output isolation: chạy lại cùng output dir là sạch.
- Export JSON (episodes, events, patterns, summary, runtime_profile), strict GT matcher.
- Legacy `/api/v1/inference` trả HTTP 410 mặc định.
- 271/271 test pass; tốc độ India (11.6–14.3 FPS) tốt hơn số lịch sử.

---

# 14. Dead / Legacy Components

| Loại | Thành phần |
|---|---|
| DEAD (không ai import) | `classroom_monitor/behavior_signals.py`, `rtsp_reader.py`, `worker_node.py`; weights `yolo11n.pt`, `yolo11s-pose.pt`, `yolo11m-pose.pt`, `weights/yolo26n.pt` |
| LEGACY (import qua `__init__`, không gọi trong SRS v2) | `behavior_tracker`, `event_engine`, `live_event`, `room_context`, `score_accumulator`, `spatial_matcher` (Kalman), `video_processor`, `detector.ClassroomDetector`, `models/classroom_best.pt`, `classroom_monitor/__main__.py`, `api/routes/inference.py` (410) |
| Enum / config không dùng | `EpisodeType.WRIST_WRITING`, `PatternType.CROSS_SEAT_REACH`, `MUTUAL_ORIENTATION`; `ClassroomConfig.head_yaw_activation_deg/head_min_persistence_ms/head_merge_gap_ms`, `risk_weights`, `combination_weights`, SAHI…; `detector.check_phone_posture_multicue` và nhãn class legacy trong `PoseClassroomDetector` |
| Trùng lặp | Seat preset trong `demo/config.py` trùng YAML; calibration DB song song |
| Script hỏng | `run_server.bat`, `run_tests.bat` (`.venv`) |

Không xoá thành phần nào. Một số vẫn được test legacy (`test_classroom_core`) hoặc API legacy tham chiếu.

---

# 15. Technical Debt

1. **Không có nguồn sự thật duy nhất cho calibration:** YAML, preset Python và DB đều tồn tại song song; UI chỉ ghi DB.
2. **Không có tracking người:** danh tính = seat, khiến multi-person, đổi người và giám thị đều không phân biệt được.
3. **Adapter CLI và web khác nhau ở đầu vào:** seat source, timestamp offset, snapshot source.
4. **Renderer tự tính ngưỡng và cửa sổ thời gian;** web UI tự bịa score mặc định.
5. **Test không cô lập** (DB và `data/` thật), marker không dùng, thiếu golden-run test trên video thật.
6. **Telemetry có nhưng không có cảnh báo:** single-call HPE, lỗi forward bị nuốt.
7. **Risk tracker** giữ state toàn phiên (peak) mà không có vòng đời incident.
8. **Dọn output web** (`demo_runs`, `demo_final`) và retention evidence.
9. **Tài liệu** status và architecture cũ mâu thuẫn với runtime (xem bảng discrepancy).

---

# 16. Critical Risks

1. **Demo trước ban giám khảo phụ thuộc vào trạng thái DB máy:** cùng lệnh nhưng số incident, nhãn và benchmark khác nhau giữa máy dev và clean clone.
2. **REPLAY có thể phát gói rỗng** nếu test được chạy trước (`run_demo_system.ps1` mặc định chạy test).
3. **Phần lớn incident (MULTI_PERSON_DWELL) là artifact ROI:** nếu giám khảo hỏi "ai là người thứ hai?", câu trả lời là bạn cùng bàn.
4. **Card REVIEW REQUIRED với score 44–52** mâu thuẫn trực quan với ngưỡng 80 được trình bày.
5. **Nhãn incident sai trong 29% trường hợp** (India) làm giảm độ tin cậy của review card và evidence.
6. **Hướng "trái/phải" và neighbor đích có thể bị đảo** trên mọi glance pattern.
7. **Precision strict India khoảng 2.5%:** chỉ chấp nhận được khi trình bày như "ưu tiên rà soát", tuyệt đối không như "phát hiện".

---

# 17. Recommended Next Steps

Thứ tự đề xuất. Mỗi bước cần được duyệt trước khi làm; chưa bước nào được thực hiện.

1. **Chốt một nguồn calibration** (quyết định của chủ sản phẩm): YAML hay DB.
   - Sau đó cho CLI và web đọc cùng nguồn.
   - Thêm kiểm tra fail-fast khi seat code của SeatManager không khớp SeatGraph.
   - Vẽ lại ROI Student (16 người) và India cho khớp người thật.
   - Đây là việc có tác động lớn nhất lên FP mà không đổi threshold.
2. **Cô lập test:** thêm `conftest.py` với `DATABASE_URL` tạm và output dir tạm. Khôi phục gói `data/demo_final/student` bằng một lần chạy CLI hoặc web chính thức.
3. **HPE:**
   - Bỏ hoặc giới hạn fallback 6DRepNet đơn lẻ (dùng cache hoặc UNKNOWN).
   - Đưa seat MULTIPLE_PERSON vào batch hoặc đánh dấu UNKNOWN.
   - Bỏ quality hằng số của crop fallback.
   - Kỳ vọng: giảm 3–20 lần số forward, FPS tăng.
4. **Xác nhận dấu yaw** bằng 20–30 crop gán nhãn tay. Sau đó thống nhất quy ước image-frame cho cả hai provider, pattern engine và debug ray.
5. **Risk tracker:** reset `peak_pattern` / `peak_frame` theo vòng đời incident. Nhãn incident lấy từ pattern gây trigger.
6. **Renderer:** chỉ hiện REVIEW theo incident do core emit (cửa sổ khớp cooldown), không tự so ngưỡng; hiển thị score lúc trigger. Web UI bỏ score mặc định 75/80.
7. **Truyền `pose_imgsz`** vào detector, rồi đo lại Student ở 640.
8. **Web output hygiene:** purge `data/demo_final/<preset>` trước khi copy; chính sách dọn `data/demo_runs`.
9. **Đo lại và công bố có ràng buộc:** commit, calibration, DB state, phần cứng; tách rõ demo validation (Student, không GT) với benchmark (India, 9 GT hợp lệ, quá ít để kết luận khoa học).

---

## Phụ lục A — Discrepancies: tài liệu bàn giao / README so với code

| # | Tài liệu nói | Code / runtime thực tế |
|---|---|---|
| D1 | Pipeline: SeatManager → head-orientation provider theo cấu hình, 6DRepNet 5 Hz, batched | Phần lớn forward là fallback đơn lẻ ngoài lịch (Student DB: 100%) |
| D2 | "CLI and web adapters use the same SRSv2Pipeline semantic core" | Đúng ở mức class, nhưng đầu vào khác (DB vs YAML seats, timestamp lệch 1 frame, snapshot khác) nên kết quả khác |
| D3 | Seat ROI từ `configs/scenes/*.yaml` | CLI dùng polygon DB khi tồn tại (14 seat Student / India code 02–22); YAML chỉ dùng cho context |
| D4 | Student: 5 incident, 68 episode | Tái hiện được chỉ với DB mới (5 incident, 89 episode). Với DB hiện tại: 2 incident |
| D5 | Student imgsz 640 (preset, log "Imgsz: 640", `--imgsz`) | Detector luôn dùng `ClassroomConfig.pose_input_resolution = 1280` |
| D6 | India: TP 7, FP 264, P 2.58%, R 77.8% | HEAD: TP 8, FP 294, P 2.6%, R 88.9% (DB); TP 5, FP 206, R 55.6% (DB mới). GT gán theo seat code DB |
| D7 | India 7.84 FPS, 0.26× | 11.6 FPS (DB) / 14.3 FPS (DB mới), 0.39–0.48× |
| D8 | 271 test pass | Đúng, nhưng test ghi vào DB và `data/demo_final` thật |
| D9 | Runner docstring: "Isolated Roaming Actor / Invigilator Filtering", "Median Smoothing w=3" | Không có lọc giám thị; median theo frame trên mẫu HPE lặp lại nên gần như không lọc |
| D10 | Quy ước "yaw < 0: LEFT" (giả định image-frame) | Dấu 6DRepNet thực đo ngược hướng ảnh; heuristic provider theo quy ước ngược lại |
| D11 | Renderer docstring: "Incident / Review Required seats … ONLY for active review incidents" | Hiện REVIEW trong cửa sổ 6 s bất kể COOLDOWN, và khi score ≥ 80 mà không có incident |
| D12 | `run_server.bat` / `run_tests.bat` là launcher | Trỏ `.venv` không tồn tại |
| D13 | Calibration UI là nơi chỉnh seat | Chỉ ảnh hưởng CLI (polygon), không ảnh hưởng web LIVE; không lưu desk_y, baseline hay neighbor |

## Phụ lục B — Cách tái lập số liệu

```powershell
venv\Scripts\python.exe -m pytest -p no:cacheprovider -rA                      # (CẢNH BÁO: ghi đè data/demo_final/student)
venv\Scripts\python.exe scripts\run_demo_video.py --video student --output <tmp>\bench_student
$env:DATABASE_URL="sqlite:///<tmp>/fresh.db"; venv\Scripts\python.exe scripts\run_demo_video.py --video student --output <tmp>\bench_student_fresh
```

Số liệu trong runtime_profile: `head_pose_runtime.estimate_single_calls`, `scheduled_cycles`, `stage_latency_averages_ms`.

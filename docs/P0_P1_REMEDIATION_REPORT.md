# VIGIL Classroom — Báo cáo khắc phục P0/P1

**Ngày:** 2026-10-06 · **Nhánh:** `feat/proctor-support-implementation` (base `0f975b5`, thay đổi **chưa commit**)
**Đầu vào:** [`AGENT_ONBOARDING_AUDIT.md`](AGENT_ONBOARDING_AUDIT.md), §12 Known Problems.
**Môi trường đo:** RTX 4060 Laptop, Torch 2.6.0+cu124, YOLO11n-Pose, 6DRepNet. Lệnh: `scripts/run_demo_all_videos.py` và `scripts/run_demo_video.py`.

## 1. Kết quả trước và sau

| Chỉ số | Trước (audit) | Sau |
|---|---|---|
| Test suite | 271 pass, **ghi vào DB thật và `data/demo_final`** | **293 pass** (thêm 22 test hồi quy); DB và `data/` thật không đổi |
| 6DRepNet forward ngoài lịch | 669–10.774 lần/run (batch ~1) | **0** (batch TB 3,4–5,2) |
| Stage head + observation | 30–92 ms/frame | **12–14 ms/frame** |
| FPS India / Student | 11,6–14,3 / 8,1–15,8 | **20,3 / 27,6** (0,68× / 1,38× realtime) |
| Nguồn seat CLI và web | CLI = polygon DB + context YAML; web = YAML | **Một nguồn** (`--seat-source scene\|db`), fail-fast nếu lệch |
| Kết quả CLI và web (Student LIVE) | khác nhau | **giống hệt** (60 episode, cùng pattern, cùng event) |
| Incident Student (YAML) | 5 (4 MULTI_PERSON do 2 người/bàn) | 0 (4 glance pattern, chưa seat nào đạt ngưỡng 80 trong 11 s) |
| Incident India (YAML) | 18 (15 MULTI) | 18 (10 MULTI, 5 GLANCE, 3 SUSPICIOUS_POSTURE) |
| Incident mang nhãn cũ (India) | 8/28 (29%) | **0** (3 incident không có pattern trong cửa sổ được ghi đúng là `SUSPICIOUS_POSTURE`) |
| India GT strict (DB calibration, 9 GT) | P 2,6% · R 88,9% · FP 294 · IoU 0,68 | **P 3,0% · R 77,8% · FP 227 · IoU 0,75** |
| GT benchmark với calibration sai | in ra số vô nghĩa | **bị bỏ qua có chủ đích** kèm hướng dẫn `--seat-source db` |
| File evidence mồ côi trong `demo_final/india` | 116 file, 0 event | 36 file / 18 event (sạch) |

Ghi chú: với 9 GT hợp lệ thì chênh lệch recall (mất 1 TP) nằm trong biên nhiễu. Precision 3% vẫn rất thấp; hệ thống chỉ dùng để *ưu tiên rà soát*.

## 2. Thay đổi theo từng lỗi

| ID | Sửa | File chính |
|---|---|---|
| **P0-1** | `build_scene_seating()` dựng polygon và SeatGraph từ **cùng một** nguồn (`scene` = YAML/preset, `db` = calibration UI); raise `CalibrationMismatchError` khi seat thiếu context hoặc không có calibration. CLI và web cùng dùng hàm này. Đồng hồ web LIVE/REPLAY thống nhất với CLI (`(frame_idx−1)/fps`). GT India gắn với `gt_seat_source="db"`; benchmark tự bỏ qua nếu khác nguồn. Thêm `--seat-source` cho CLI và `-SeatSource` cho `run_prototype.ps1`. Manifest ghi `seat_calibration`. | `demo/seating.py` (mới), `demo/runner.py`, `demo/runtime.py`, `demo/config.py`, `scripts/*` |
| **P0-2** | Pipeline cấm suy luận head ngoài lịch (`allow_head_inference=False`); seat `MULTIPLE_PERSON` cũng được đưa vào batch theo lịch. Lỗi forward 6DRepNet đổi từ log DEBUG sang WARNING. | `pipeline/srs_v2_pipeline.py`, `observation_extractor.py`, `head_pose_provider.py` |
| **P0-3** | `SeatManager` học **số người nền** của từng seat: ổn định sau 1 s; giảm chỉ khi kéo dài ≥ 10 s; tăng chỉ khi kéo dài ≥ 60 s; `capacity` trong YAML là mức sàn. `MULTI_PERSON_NEAR_SEAT` = số người **vượt quá** số nền, nên bạn cùng bàn không còn bị tính là người thứ hai. | `seat_manager.py`, `temporal_episode_engine.py` |
| **P0-4** | `tests/conftest.py`: DB SQLite tạm và các thư mục output demo đặt dưới `tmp_path`. Đường dẫn demo cấu hình qua `VIGIL_DEMO_RUNS_ROOT` / `VIGIL_DEMO_FINAL_ROOT`. Gói replay `data/demo_final/{india,student}` đã được tạo lại bằng một lần chạy CLI đầy đủ. | `tests/conftest.py`, `demo/paths.py` (mới), `api/routes/demo.py` |
| **P1-1** | Chốt quy ước **theo hướng của thí sinh** (đúng với annotation guideline và GT; 6DRepNet vốn đã dùng quy ước này, đã kiểm trực quan trên crop thật). Lỗi nằm ở **neighbor graph**: camera nhìn vào mặt thí sinh nên "bên trái thí sinh" là bên phải ảnh. Thêm `camera_view` (`facing_subjects` / `behind_subjects`) vào SeatGraph và hai YAML. PoseHeuristic provider và tia đầu ở debug overlay cũng chuyển sang quy ước này. Torso lean dùng vai trái/phải giải phẫu (COCO) nên tự khớp. | `scene_context.py`, `head_pose_provider.py`, `demo/renderer.py`, `configs/scenes/*.yaml` |
| **P1-2** | (a) **Baseline yaw thích nghi theo seat** (median trượt 60 s, cần ≥ 10 mẫu; trong lúc khởi động yaw = UNKNOWN). Median yaw đo được của từng seat là −55° đến +70°, khác xa baseline YAML (±10°). (b) Median smoothing tính theo **mẫu HPE**, không theo frame lặp lại, nên một spike đơn lẻ không mở được episode. (c) Crop đầu bắt buộc thấy mặt (mũi và ít nhất một mắt); gáy hoặc đầu cúi sâu = UNKNOWN. (d) Các mảnh HEAD_TURN cùng hướng cách nhau < 500 ms được gộp thành một lần liếc (dùng tham số có sẵn `head_merge_gap_ms`). | `head_pose_provider.py`, `pipeline/srs_v2_pipeline.py`, `temporal_episode_engine.py`, `behavior_pattern_engine.py`, `config.py` |
| **P1-3** | Renderer chỉ hiện REVIEW cho incident do core phát ra, trong đúng thời gian cooldown của core (thay cho cửa sổ 6 s riêng); bỏ nhánh "score ≥ 80 ⇒ REVIEW". Amber chỉ theo state `SUSPICIOUS` của core. Card hiển thị **score lúc trigger**. Đếm "Occ" tính cả seat có bạn cùng bàn. Debug ray dùng yaw và episode của core. Web UI bỏ score mặc định tự đặt (75/80). | `demo/renderer.py`, `demo/runtime.py`, `dashboard/js/demo.js` |
| **P1-4** | Nhãn incident = pattern mạnh nhất **trong cửa sổ incident hiện tại** (≤ 30 s, kể từ incident trước). Peak frame, bbox và pattern được reset sau mỗi incident. Metadata có thêm `trigger_risk_score`. | `seat_risk_tracker.py` |
| **P1-5** | Bỏ crop fallback "28% trên của bbox" (vai/thân). | `head_pose_provider.py` |
| **P1-6** | Web chỉ publish gói replay khi run **hoàn chỉnh** (không bị stop, không giới hạn `max_frames`), và purge gói cũ trước khi copy. | `demo/runtime.py`, `demo/paths.py` |
| **P1-7** | Người "chính" của seat được chọn theo tính liên tục (IoU ≥ 0,3 với người của frame trước), sau đó theo độ sâu anchor trong ROI, thay vì theo confidence cao nhất. | `seat_manager.py` |
| **P1-8** | Có một tay đang viết thì không kích hoạt `WRIST_BELOW_DESK` (ưu tiên viết tính theo người, không theo từng cổ tay). | `temporal_episode_engine.py` |

**Test được cập nhật có chủ đích:**
- `test_prototype_remediation.py`: LIVE run giờ bắt buộc có calibration.
- `test_prototype_hardening.py`, `test_srs_v2_temporal.py`: vị trí mũi giả lập được phản chiếu theo quy ước của thí sinh.

**Test mới:** `tests/test_p0_p1_remediation.py` (22 test). 11 trong số đó fail trên code cũ.

## 3. Cần con người quyết định hoặc thực hiện

1. **Chọn calibration chuẩn** cho demo: YAML (mặc định hiện tại) hay calibration UI trong DB (GT India được gán theo DB). Cả hai đều chưa khớp thực tế:
   - Student: 16 học sinh nhưng 12 ROI (YAML) hoặc 14 ROI (DB).
   - India: trung bình khoảng 9 người/frame nằm ngoài mọi ROI.
2. **Vẽ lại ROI** cho đúng từng thí sinh. Nếu một ROI thật sự chứa 2 người, khai báo `capacity: 2` trong YAML.
3. **Calibration UI chưa lưu** `desk_y`, `baseline_yaw`, `capacity`, `camera_view`. Muốn dùng `--seat-source db` lâu dài thì cần bổ sung UI (thay đổi sản phẩm).
4. **Ngưỡng / quy tắc sản phẩm** (không tự đổi):
   - `post_event_reset_score = 45` khiến incident lặp lại sau cooldown (đây là nguồn của các incident `SUSPICIOUS_POSTURE`).
   - `min_glance_episodes = 2` trong 25 s.
   - Có nên gọi nhãn fallback là `SUSPICIOUS_POSTURE` hay không.
5. **Giám thị đi lại** vẫn có thể sinh `MULTI_PERSON_DWELL` (còn 10 incident ở India). Phân biệt giám thị với thí sinh cần một quyết định sản phẩm và một tín hiệu vai trò hiện chưa có.
6. **Ground truth:** 9 record hợp lệ, quá ít để kết luận. Cần gán thêm GT (kể cả cho Student) trước khi công bố precision/recall.
7. **Nghiệm thu bằng mắt** các thay đổi UI (REVIEW card, debug ray) trên màn hình demo thật.
8. **Commit:** các thay đổi đang nằm trong working tree, chưa commit, chờ review.

## 4. Chưa xử lý (P2, ngoài phạm vi)

- `pose_imgsz` / `--imgsz` vẫn chưa được truyền vào detector (luôn 1280).
- `frame_index` vẫn hardcode 30 FPS; `track_id = 0`.
- Neighbor được suy ra theo ngưỡng pixel tuyệt đối.
- `run_server.bat` / `run_tests.bat` trỏ tới `.venv`.
- 6DRepNet weight tải từ internet lúc chạy.
- Module legacy / dead code.
- `data/demo_runs/` chưa có chính sách dọn.

## 5. Bổ sung (2026-10-06): Web là cổng duy nhất, một lệnh khởi động

Theo yêu cầu của chủ sản phẩm, phần P0-1 được hoàn thiện như sau:

- **Nguồn calibration duy nhất là Seat ROI vẽ trên `/calibration`** (lưu trong DB). Các thay đổi:
  - Bỏ tuỳ chọn `--seat-source` / `--scene`.
  - Bỏ hai bộ seat hardcode trong `demo/config.py`.
  - `setup_room_seats` không còn ghi seat vào DB.
  - YAML trong `configs/scenes/` chỉ còn là **bố cục mẫu**, nhập được qua nút "Import starting layout" (API `POST /api/v1/rooms/{id}/seats/import-template`).
- **Trang calibration** có thêm:
  - Sức chứa của từng ROI ("Cap", lưu `context_json.capacity`).
  - Vị trí camera (`camera_view`).
  - Khi lưu, các trường context sẵn có (`desk_y`, `baseline_yaw`) vẫn được giữ nguyên.
- **LIVE khi phòng chưa có seat** bị từ chối ngay với HTTP 409. Trang demo hiện banner kèm link `/calibration`. Nút preset và panel hiển thị số seat thật của calibration.
- **Một lệnh khởi động:** `start_vigil.bat`. Lệnh này kiểm tra môi trường, khởi động server và mở trình duyệt (vào `/calibration` nếu còn phòng chưa calibrate). Đã xoá 6 launcher Classroom trùng lặp: `run_classroom_demo.bat`, `run_demo_video.bat`, `run_server.bat`, `run_tests.bat`, `scripts/run_demo_system.ps1`, `scripts/run_prototype.ps1`. VIGIL One (`run_app.bat`, `main.py`) là sản phẩm desktop riêng nên giữ nguyên.
- **Kiểm chứng trên trình duyệt** (DB tạm), luồng: nhập mẫu → sửa Cap = 2 → lưu → tải lại → chạy LIVE từ `/demo`. Manifest ghi `source: database, 12 seats`. Runtime web đạt 25 FPS, ngang CLI. Khi GPU laptop ở chế độ tiết kiệm điện (P5) thì chỉ còn khoảng 3 FPS.
- **Calibration hiện có trong DB thật** (Student 14 seat, India 21 seat) được dùng nguyên trạng; chưa nhập bố cục mẫu nào vào DB thật.

# VIGIL Proctor Support — Baseline and AI Freeze

Ngày ghi nhận: 2026-09-29.

## Baseline

- Repository: `HoanBuCon/Laser-Eyes`.
- Nhánh triển khai: `feat/proctor-support-implementation`.
- HEAD bắt đầu triển khai: `345dbc81c1b07143477e67ad3f9d48b9e684299a`.
- AI Core freeze baseline: `6a7b4b3baf062f5a35cf5a02faac7ee127239bce`.
- Test baseline trước triển khai: 265 bài pass; không dùng kết quả này làm bằng chứng độ chính xác AI.

## Phạm vi AI bị freeze

Không thay đổi thuật toán, model hoặc threshold trong:

- `classroom_monitor/detector.py`
- `classroom_monitor/head_pose_provider.py`
- `classroom_monitor/observation_extractor.py`
- `classroom_monitor/temporal_episode_engine.py`
- `classroom_monitor/behavior_pattern_engine.py`
- `classroom_monitor/seat_risk_tracker.py`
- `classroom_monitor/pipeline/`
- `exam_monitor/engine.py`
- `exam_monitor/audio.py`
- logic `EventDetector` trong `exam_monitor/events.py`

Thay đổi ở `DemoRuntime` chỉ bổ sung snapshot tại biên I/O. Thay đổi trong `exam_monitor/events.py` chỉ thuộc `SessionStore`.

## Tài nguyên demo đã xác minh

- `demo_video/india_classroom.mp4`: có trong working copy.
- `demo_video/student_classroom.mp4`: có trong working copy.
- India replay: có `result.mp4`, `events.json` và thư mục evidence.
- Student replay: có `result.mp4`; `events.json` hiện là danh sách rỗng. Đây không phải bằng chứng rằng AI không có false negative.

## Hợp đồng sản phẩm

- AI incident và bookmark do giám thị tạo là hai loại dữ liệu độc lập.
- Bookmark không có confidence, probability hoặc risk score AI giả.
- `review_priority_score` không phải xác suất gian lận.
- Quyết định `CONFIRMED`, `REJECTED`, `INCONCLUSIVE` luôn là thao tác của con người.
- SHA-256 chỉ dùng so sánh tính toàn vẹn file, không phải tuyên bố chain of custody pháp lý.

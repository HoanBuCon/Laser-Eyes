# VIGIL AI — ICTU 2026 Competition Prototype

VIGIL AI is an **AI-assisted exam monitoring** prototype. It creates observable signals, temporal episodes, review-priority incidents and evidence. It does **not** decide that a person cheated. A human reviewer makes the final decision: `PENDING`, `CONFIRMED`, `REJECTED` or `INCONCLUSIVE`.

Current implementation status and measured limitations are in [docs/PROTOTYPE_CURRENT_STATUS.md](docs/PROTOTYPE_CURRENT_STATUS.md). This repository contains two products with separate perception engines.

## Product A — VIGIL Local

- One candidate, close webcam/video geometry.
- MediaPipe face/iris, personalized gaze calibration, head orientation, optional microphone activity and EfficientDet object observations.
- Tkinter desktop UI; local session/evidence storage.
- Detected material is separate from prohibited material. Books can be allowed for an open-book session.

Launch:

```powershell
venv\Scripts\python.exe main.py
```

The UI includes guided calibration, an explicit Recalibrate action and an allowed-material toggle. A held tracking estimate may support display/release grace but cannot activate new gaze/head evidence.

## Product B — VIGIL Classroom / SRS v2

- Multi-person room-camera geometry.
- YOLO11 Pose, seat mapping, SixDRepNet head orientation, temporal episodes, behavior patterns, review-priority scoring, evidence and a web review queue.
- CLI and web adapters use the same `SRSv2Pipeline` semantic core.

### Start — one command

```powershell
.\start_vigil.bat
```

(Or double-click `start_vigil.bat`.) On the first run it creates the Python environment. Each start checks the GPU, models, `ffmpeg` and the seat calibration of every room, starts the server on `http://localhost:8000` and opens the browser. Arguments are passed through, e.g. `.\start_vigil.bat --port 8080`, or `.\start_vigil.bat --host 0.0.0.0 --demo-token "choose-a-secret"` for LAN access (a token is mandatory beyond localhost). Stop the system with `Ctrl+C` in that window.

### Demo videos

The demo videos are `demo_video/classroom-01.mp4`, `classroom-02.mp4` and `classroom-03.mp4` (1920×1080, 30 FPS), one calibration room each (`CLASSROOM-01..03`). The list lives only in `DEMO_PRESETS` (`classroom_monitor/demo/config.py`); the calibration page, the live monitor, the server and the CLI all read it. The earlier India/Student videos are no longer offered as demos.

### Operate — the website is the only interface

| Page | Purpose |
|---|---|
| `/calibration` | Draw the Seat ROIs of each room on the camera frame, set how many people normally sit in an ROI ("Cap", use 2 for a shared desk) and where the camera is (in front of or behind the candidates). **Import starting layout** loads a bundled example layout to adjust. Save stores the seats in the database. |
| `/demo` | Start LIVE analysis or REPLAY, watch the annotated stream, review incidents, mark frames, export. |
| `/` and `/data-workbench` | Operations overview and dataset tools. |

Every run reads the Seat ROIs saved on `/calibration`; there is no other calibration source. A room without seats cannot start LIVE: the page shows the reason and a link to the calibration page. A complete LIVE run also refreshes the REPLAY package of that room.

For best speed keep a laptop on AC power: a GPU in power-saving state (for example while the screen is locked) can make analysis several times slower.

### Developer tools (not for operators)

`scripts/run_demo_video.py --video classroom-01|classroom-02|classroom-03` and `scripts/run_demo_all_videos.py` run the same pipeline headless for benchmarks, reading the same web calibration. `venv\Scripts\python.exe -m pytest` runs the test suite against a temporary database.

## Real, degraded and mock modes

Competition LIVE defaults to fail-closed. If YOLO-Pose cannot load, the run enters `ERROR`; it never silently substitutes synthetic people. Synthetic perception is available only with explicit `--allow-mock`/`allow_mock=true`, and the UI, API and manifest identify it as `MOCK` with a simulation watermark. Recorded replay is reported as `DEGRADED`/recorded, not live inference.

The legacy `/api/v1/inference` route is disabled with HTTP 410 by default. `/api/v1/demo` is the canonical Classroom API.

## Evidence and review semantics

Evidence follows `PENDING → READY | FAILED`. `HASH VERIFIED` means the current file digest equals its stored SHA-256 digest; it is a file-integrity comparison, not a legally guaranteed chain of custody. AI status and human review decision are stored separately.

Classroom evidence is archived unchanged. Because OpenCV may encode archived MP4 clips as `mp4v`, the web endpoint lazily creates a separate H.264 playback derivative for Chrome/Edge. Install `ffmpeg` and `ffprobe` on the demo machine and make both commands available on `PATH`; the displayed SHA-256 continues to refer to the archived source file, not the playback cache.

## Proctor-support workflow

AI Core is frozen while the prototype adds human workflow around its outputs. In Classroom, **Mark Frame** captures an immutable short-lived frame token, then persists a manual bookmark with optional seat/note, evidence hash, ROI crop, review history and JSON/CSV export. The combined queue labels AI incidents and human bookmarks separately. In Local, use **ĐÁNH DẤU FRAME** or `Ctrl+B`; saved bookmarks can be reopened from the event page and reviewed without converting them into AI events.

See [the acceptance checklist](docs/PROCTOR_SUPPORT_ACCEPTANCE.md) for the manual demo flow and current limitations.

## Tests

```powershell
venv\Scripts\python.exe -m pytest
```

The suite runs against a temporary database and temporary demo folders; it passed 299 tests on 2026-10-07. Passing tests do not establish model accuracy; see the strict India benchmark in the current-status document.

## Documentation status

- **CURRENT:** `README.md`, `docs/PROTOTYPE_CURRENT_STATUS.md`, `docs/PROTOTYPE_REMEDIATION_PLAN.md`.
- **AUDIT BASELINE / SUPERSEDED FOR CURRENT BEHAVIOR:** the four architecture/debt audit documents remain valuable history and issue provenance.
- **HISTORICAL:** older reports and optimization claims are not current performance or accuracy evidence unless explicitly revalidated at the current implementation commit.

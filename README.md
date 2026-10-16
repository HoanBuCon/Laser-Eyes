# VIGIL AI — ICTU 2026 Competition Prototype

VIGIL AI is an **AI-assisted exam monitoring** prototype. It creates observable signals, temporal episodes, review-priority incidents and evidence. It does **not** decide that a person cheated. A human reviewer makes the final decision: `PENDING`, `CONFIRMED`, `REJECTED` or `INCONCLUSIVE`.

Current implementation status and measured limitations are in [docs/PROTOTYPE_CURRENT_STATUS.md](docs/PROTOTYPE_CURRENT_STATUS.md). This repository contains two products with separate perception engines.

## Product A — VIGIL One (formerly VIGIL Local)

- One candidate, close webcam geometry: MediaPipe face/iris, personalized gaze calibration, head orientation,
  microphone activity fused with lip movement, EfficientDet object observations.
- Since this branch the perception runs **on VIGIL Hub**, not on the candidate's computer (see
  [VIGIL One on the same Hub](#vigil-one-on-the-same-hub)); the candidate app only captures and streams.
- Detected material is separate from prohibited material. Books can be allowed for an open-book exam or candidate.

The earlier desktop app that analysed on the candidate's computer is kept for comparison: `python vigil.py --legacy`.

## Product B — VIGIL Classroom / SRS v2

- Multi-person room-camera geometry.
- YOLO11 Pose, seat mapping, SixDRepNet head orientation, temporal episodes, behavior patterns, review-priority scoring, evidence and a web review queue.
- CLI and web adapters use the same `SRSv2Pipeline` semantic core.

### Start

`python vigil.py` (see [Start — one command](#start--one-command)) starts the Hub with Classroom. On start the Hub checks
the GPU, models, `ffmpeg` and the seat calibration of every room. `--hub-only --lan --demo-token "choose-a-secret"`
opens it to the local network (a token is mandatory beyond localhost). Stop the system with `Ctrl+C` in that window.

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

## Start — one command

```powershell
python vigil.py --demo
```

`vigil.py` is the only entry point (`start_vigil.bat` calls it). With no option it starts everything on this computer:
VIGIL Hub (website, VIGIL Classroom, VIGIL One analysis) and the VIGIL One candidate app connected to it. `--hub-only`,
`--one-only --hub http://<hub>:8000`, `--sim`, `--lan --demo-token <secret>` and `--legacy` choose parts; a Hub left
running with older code or another database is detected through `/health` and restarted. See
[the runbook](docs/DEMO_RUNBOOK_VIGIL_HUB.md).

## VIGIL One on the same Hub

VIGIL One (online exams, one candidate on their own computer) is a capture-only app: the candidate enters the join code,
their candidate code and PIN, and the app streams the camera to the Hub over a WebSocket (~5 fps JPEG,
`exam_monitor/live_stream.py`) while recording the whole exam in 10-second H.264 segments. It loads no AI model. The Hub
analyses the live frames as they arrive (`storage/one_live.py`, `storage/one_analysis.py`), about a second behind the
candidate, raises review incidents with snapshots and cuts their clips from the recording; recorded frames the stream
missed are analysed as a backfill. Proctors watch a candidate on `/one-monitor` (live picture with the analysis drawn
on it, readings, Mark frame, Recalibrate gaze, Books/notes allowed). Hub settings: `VIGIL_ONE_ANALYSIS=0` disables the
analysis, `VIGIL_ONE_ANALYSIS_WORKERS` sets the number of workers (default 2). `scripts/one_fake_client.py --video
<webcam.mp4>` plays a recorded webcam video as a candidate.

The web dashboard is shared: the **All / Classroom / One** switcher at the top of the sidebar sets the product every page
shows. The pages work without Internet: Tailwind is compiled into `dashboard/css/tailwind-build.css` and Chart.js is
vendored; after adding Tailwind classes, rebuild the stylesheet with the command at the top of
`dashboard/tailwind.config.js`.

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

The suite runs against a temporary database and temporary demo folders; it passed 502 tests on 2026-10-16 (branch `feat/one-server-analysis`). Passing tests do not establish model accuracy; the strict India benchmark in `docs/PROTOTYPE_CURRENT_STATUS.md` was measured on older code. The VIGIL One tests drive the pipeline with a scripted analyser, plus the real lip-motion algorithm at the Hub's frame rates (`tests/test_talking_sampling.py`); a real webcam session is still a manual check.

## Documentation status

- **CURRENT:** `README.md`, `docs/DEMO_RUNBOOK_VIGIL_HUB.md`.
- **HISTORICAL SNAPSHOTS:** `docs/PROTOTYPE_CURRENT_STATUS.md`, `docs/BAO_CAO_BAN_GIAO_VIGIL.md`, `docs/PROTOTYPE_REMEDIATION_PLAN.md` (commit, test count and presets of their date).
- **AUDIT BASELINE / SUPERSEDED FOR CURRENT BEHAVIOR:** the four architecture/debt audit documents remain valuable history and issue provenance.
- **HISTORICAL:** older reports and optimization claims are not current performance or accuracy evidence unless explicitly revalidated at the current implementation commit.

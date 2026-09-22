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

Install the base dependencies and optional HPE dependencies:

```powershell
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe -m pip install -r requirements-hpe.txt
```

Run the competition web app on localhost:

```powershell
venv\Scripts\python.exe server.py --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/demo`. For LAN access, an explicit token is required:

```powershell
venv\Scripts\python.exe server.py --host 0.0.0.0 --port 8000 --demo-token "choose-a-secret"
```

Run real-model CLI validation:

```powershell
venv\Scripts\python.exe scripts\run_demo_video.py --video student
venv\Scripts\python.exe scripts\run_demo_video.py --video india
```

## Real, degraded and mock modes

Competition LIVE defaults to fail-closed. If YOLO-Pose cannot load, the run enters `ERROR`; it never silently substitutes synthetic people. Synthetic perception is available only with explicit `--allow-mock`/`allow_mock=true`, and the UI, API and manifest identify it as `MOCK` with a simulation watermark. Recorded replay is reported as `DEGRADED`/recorded, not live inference.

The legacy `/api/v1/inference` route is disabled with HTTP 410 by default. `/api/v1/demo` is the canonical Classroom API.

## Evidence and review semantics

Evidence follows `PENDING → READY | FAILED`. `HASH VERIFIED` means the current file digest equals its stored SHA-256 digest; it is a file-integrity comparison, not a legally guaranteed chain of custody. AI status and human review decision are stored separately.

## Tests

```powershell
venv\Scripts\python.exe -m pytest
```

The final remediated suite collected and passed 259 tests. Passing tests do not establish model accuracy; see the strict India benchmark in the current-status document.

## Documentation status

- **CURRENT:** `README.md`, `docs/PROTOTYPE_CURRENT_STATUS.md`, `docs/PROTOTYPE_REMEDIATION_PLAN.md`.
- **AUDIT BASELINE / SUPERSEDED FOR CURRENT BEHAVIOR:** the four architecture/debt audit documents remain valuable history and issue provenance.
- **HISTORICAL:** older reports and optimization claims are not current performance or accuracy evidence unless explicitly revalidated at the current implementation commit.

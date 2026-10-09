# VIGIL AI Prototype — Current Status

**Status:** CURRENT implementation record for human acceptance

**Validated source commit:** `96f04da` on branch `fix/ictu-2026-prototype-remediation`

**Real-model validation commit:** `41604e7` (the successor changes only LAN dashboard token propagation and tests)

**Date:** 2026-09-23

**Decision boundary:** AI review priority is not cheating probability; only a human makes the final decision.

## What is implemented

### Classroom SRS v2

- LIVE and REPLAY cross real incident boundaries without contract exceptions.
- YOLO-Pose is fail-closed by default. Explicit mock mode is labelled in status, rendered output and run manifest.
- `ReviewIncident`/`EvidenceRef`/`ReviewDecision` form a versioned Classroom review contract; Local perception was not merged into it.
- Review Queue delivery uses a server-lifecycle event loop bridge, WebSocket broadcasts and HTTP polling fallback.
- Pattern evidence sets are stably deduplicated; initial EMPTY never becomes SEAT_LEFT; recidivism is one-shot per evidence transition.
- Evidence is durable before review; lifecycle is `PENDING → READY | FAILED`; file integrity is verified only by actual SHA-256 comparison.
- Snapshot/MP4 delivery resolves through durable `event_id`, not ambiguous recursive basename selection.
- Human review requires a durable incident and transactionally stores `EventReview`, the decision update and an audit record. AI status stays separate.
- CLI and web call the same `classroom_monitor.pipeline.SRSv2Pipeline` frame semantics.
- Legacy `/api/v1/inference` is HTTP 410 unless explicitly enabled.

### VIGIL Local

- Non-daemon cooperative worker shutdown completes before model/source release and session save.
- Calibration has progress, quality, neutral-position rejection and explicit recalibration.
- Per-session material policy distinguishes an observed book from a prohibited book.
- Held head/gaze estimates cannot activate new LOOK_AWAY or HEAD_TURN evidence.

## Verification record

### Automated tests

- Baseline: 237 passed, 1 warning.
- Final remediated suite: **259 passed, 1 SQLAlchemy test warning, 0 failed**, 16.50 s.
- Critical tests include live/replay first incident, evidence success/failure and reset, real hash comparison, durable review/audit, realtime bridge/polling, fail-closed/mock status, deduplication, occupancy transition, one-shot recidivism, lifecycle isolation, SQLite FK behavior, Local calibration/material/shutdown and event-scoped media identity.
- Controlled-double integration tests are labelled and do not claim real-model accuracy.

### Real-model environment

- GPU: NVIDIA GeForce RTX 4060 Laptop GPU, 8,188 MiB.
- Driver: 610.47.
- Torch: 2.6.0+cu124; device: CUDA.
- Pose: `yolo11n-pose.pt`.
- Head orientation: SixDRepNet, successfully initialized on GPU 0.

### Student full CLI

Command:

```powershell
venv\Scripts\python.exe scripts\run_demo_video.py --video student --output data\validation_runs\student_full_5b17fa7
```

- Source: 219 frames, 20 FPS, 10.95 s.
- Processed: 219 frames; 10.87 FPS; realtime factor 0.54.
- Output: 68 canonical episodes; 13 patterns; 5 review incidents.
- Evidence: 5 snapshots and 5 non-empty MP4 clips; 0 observed failures.
- Strict GT: unavailable because no committed Student GT file exists. No accuracy claim is made.

### India full CLI

Command:

```powershell
venv\Scripts\python.exe scripts\run_demo_video.py --video india --output data\validation_runs\india_full_5b17fa7
```

- Source: 2,153 frames, 30 FPS, 71.77 s; exporter processed 2,150 frames.
- Runtime: 274.32 s; 7.84 FPS; realtime factor 0.26.
- Output: 551 canonical episodes; 127 patterns; 26 review incidents.
- Evidence: 26 snapshots and 26 non-empty MP4 clips; 0 observed failures.
- Strict valid temporal GT: 9 records (1 malformed record excluded without modification).
- Result: TP 7, FP 264, FN 2, precision **2.58%**, recall **77.78%**, F1 **5.00%**, average matched IoU 0.66.

This precision is poor and is a major remaining product limitation. It is not hidden, and thresholds were not tuned during remediation. The system is suitable only for human review prioritization in this prototype, not autonomous judgment.

### Web LIVE and REPLAY

- Web LIVE Student: REAL pose + REAL HPE; 219/219 frames; 11.13 FPS; 5 incidents; all 5 evidence records READY; HTTP event polling returned the same five identities.
- Web REPLAY Student: 219 frames at recorded 20 FPS; 5 incidents entered the queue; first snapshot and MP4 both returned HTTP 200 through event-scoped URLs.
- The automated realtime bridge test proves worker-thread incident delivery through the captured server loop. Final browser/projector interaction remains a human acceptance step.

## Security and deployment boundary

- `server.py` and `run_server.bat` bind localhost by default.
- LAN/public binding refuses to start without a demo token; the dashboard propagates it to API, WebSocket, MJPEG and evidence requests.
- CORS defaults to explicit localhost origins with credentials disabled.
- Uploads use server-generated names, extension/MIME checks and size limits.
- Evidence/reference paths are root-contained; ambiguous legacy basename matches fail closed.
- Review cards escape dynamic content and do not use inline event handlers.
- This is prototype security, not production RBAC. Data retention, user management and authenticated chain of custody remain post-competition work.

## Deferred limitations

- India alert/episode precision is not acceptable for autonomous use.
- 1280px India processing is 0.26× realtime on the tested laptop; HPE is the measured dominant stage.
- Camera reference capture can still provide a synthetic calibration frame and requires clearer capability labelling.
- Per-modality temporal grace, robust actor tracking, confirmed neighbor graphs, retention, migrations/Alembic, dependency split and long-run memory pruning remain deferred.
- Local hardware interaction (camera, microphone, Tkinter close/restart) requires human acceptance on the competition laptop.

## Human acceptance commands

```powershell
# Classroom competition web
venv\Scripts\python.exe server.py --host 127.0.0.1 --port 8000

# Classroom CLI real-model checks
venv\Scripts\python.exe scripts\run_demo_video.py --video student
venv\Scripts\python.exe scripts\run_demo_video.py --video india

# Local
venv\Scripts\python.exe main.py
```

The owner must complete the checklist in the final remediation report before any release/freeze decision.

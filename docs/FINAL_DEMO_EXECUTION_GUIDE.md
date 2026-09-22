# [HISTORICAL / SUPERSEDED] VIGIL AI SRS v2.0: Final Demo Execution Guide
> [!NOTE]
> **TÀI LIỆU LỊCH SỬ / SUPERSEDED:** Hướng dẫn vận hành hệ thống Unified Demo System chính thức hiện tại xem tại: [`docs/UNIFIED_DEMO_SYSTEM_GUIDE.md`](file:///D:/Hoc_Tap/Code/Du_An_Ca_Nhan/H_drive/Code/MingKingLaser/laser_eyes/docs/UNIFIED_DEMO_SYSTEM_GUIDE.md).

**Project:** VIGIL AI — AI-Assisted Exam Monitoring Co-Pilot  
**Competition:** ICTU 2026 Working Prototype  
**Architecture:** Calibrated Actor-Centric Temporal Perception Architecture (SRS v2.0)  
**Status:** `SUPERSEDED BY UNIFIED DEMO SYSTEM`

---

## 1. Architecture Overview

VIGIL AI processes raw classroom surveillance video through a strictly non-automated, human-in-the-loop review architecture:

```
+---------------------------------------------------------------------------------------------------+
|                                 VIGIL AI SRS v2.0 PIPELINE                                        |
+---------------------------------------------------------------------------------------------------+
| 1. Video Ingestion       --> Frame-rate independent timestamping (millisecond precision)          |
| 2. YOLO-Pose Perception   --> Single-pass multi-person keypoint extraction (1280px / 640px)        |
| 3. Seat Manager          --> Calibrated ROI polygon point-in-polygon & 4.0s occlusion coasting   |
| 4. Roaming Isolation     --> Automatic separation of invigilators / moving persons                |
| 5. Observation Extractor --> Subsampled 6DRepNet @ ~5Hz + Per-Seat Baseline Subtraction + Median  |
| 6. Temporal Episodes     --> Stateful dual-threshold hysteresis (28°/16°), 400ms persistence      |
| 7. SceneContext Graph    --> Spatial adjacency, neighbor relationships, desk zones               |
| 8. Pattern Engine        --> Relational patterns (Repeated glances, leaning, multi-person dwell)  |
| 9. Seat Risk Tracker     --> Exponential decay (1.8 pts/s), diminishing returns, 0-100 priority  |
| 10. Event Engine         --> Debounced triggers with canonical FLAGGED_FOR_REVIEW status          |
| 11. Evidence Buffer      --> 10s MP4 clips + peak frame snapshots + SHA-256 integrity digest      |
| 12. Standardized Export  --> result.mp4, canonical episodes.json, events.json, runtime profile    |
+---------------------------------------------------------------------------------------------------+
```

---

## 2. Quickstart Execution Commands

### A. One-Click Prototype Execution (PowerShell)
Executes environment pre-flight checks, runs full pytest regression suite, and processes both videos sequentially:
```powershell
.\scripts\run_prototype.ps1
```
Optional flags:
- `-Show`: Display live OpenCV playback window.
- `-DebugOverlay`: Display live latency and telemetry overlay.
- `-SkipTests`: Skip initial pytest suite for rapid re-runs.

### B. Single Video Execution (Python CLI)
Run India Classroom (1280x720, 21 seats):
```bash
python scripts/run_demo_video.py --video india
```

Run Student Classroom (640x352, 12 seats) with live GUI:
```bash
python scripts/run_demo_video.py --video student --show
```

Run with Debug Overlay:
```bash
python scripts/run_demo_video.py --video india --debug-overlay
```

Run custom video file with custom scene configuration:
```bash
python scripts/run_demo_video.py --input path/to/video.mp4 --scene configs/scenes/custom.yaml --output data/demo_final/custom
```

### C. Batch Video Execution (Python CLI)
Run all configured demo targets (`india` and `student`):
```bash
python scripts/run_demo_all_videos.py --output-root data/demo_final
```

### D. Windows One-Click Batch Launchers
- `run_demo_video.bat`: Launches India Classroom demo with live GUI.
- `run_classroom_demo.bat`: Launches India Classroom demo headless with progress logging.

---

## 3. CLI Command Options Reference

| Option | Type | Default | Description |
|---|---|---|---|
| `--video` | string | `india` | Demo target preset (`india`, `student`) or path to `.mp4` file |
| `--input` | string | `None` | Custom input video path |
| `--scene` | string | `None` | Path to scene configuration YAML |
| `--output` | string | `None` | Custom output directory |
| `--head-provider` | choice | `sixdrepnet` | Head pose estimator (`sixdrepnet`, `pose_heuristic`) |
| `--hz` | float | `5.0` | Target 6DRepNet inference frequency on occupied seats |
| `--conf` | float | `0.20` | YOLO-Pose detection confidence threshold |
| `--imgsz` | int | Auto | YOLO-Pose input resolution (1280 for india, 640 for student) |
| `--show` | flag | `False` | Open interactive OpenCV GUI playback window |
| `--debug-overlay` | flag | `False` | Render latency breakdown and telemetry panel |
| `--no-evidence` | flag | `False` | Disable 10-second MP4 evidence clip buffering |
| `--max-frames` | int | `None` | Cap maximum frames to process (useful for smoke tests) |
| `--stride` | int | `1` | Frame subsampling stride (1 = every frame) |

---

## 4. Standardized Output Artifact Structure

All executions export structured artifacts into `data/demo_final/<video_name>/`:

```
data/demo_final/
  ├── india/
  │   ├── result.mp4               # Annotated video with HUD overlay
  │   ├── episodes.json            # Deduplicated canonical temporal episodes
  │   ├── events.json              # Review events with FLAGGED_FOR_REVIEW status
  │   ├── patterns.json            # Relational behavior patterns detected
  │   ├── demo_summary.json        # Executive statistics and seat risk rankings
  │   ├── runtime_profile.json     # Performance, FPS, and latency breakdown
  │   └── evidence/                # 10-second MP4 clips and peak snapshots
  │       ├── EVT-..._clip.mp4
  │       └── EVT-..._snapshot.jpg
  ├── student/
  │   └── (same structure as india)
  └── final_execution_manifest.json # Consolidated multi-video execution summary
```

### Key Contract Rules:
1. **Canonical Episodes (`episodes.json`):** Deduplicated by unique `episode_id`. Contains millisecond start/end timestamps and peak rotational values.
2. **Review Triggers (`events.json`):** Strictly tagged with `status: "FLAGGED_FOR_REVIEW"`. No automated `CHEATING` verdicts are ever issued.
3. **Evidence Integrity:** Every event clip has a matching SHA-256 cryptographic hash recorded in `events.json`.

---

## 5. Review & Ground Truth Evaluation

To compare the output against ground truth temporal annotations:
```bash
python scripts/benchmark_temporal_ground_truth.py
```
To run the full A/B benchmark (Pose Heuristic vs 6DRepNet):
```bash
python scripts/run_head_orientation_ab_benchmark.py
```

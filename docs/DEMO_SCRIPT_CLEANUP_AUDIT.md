# VIGIL AI SRS v2.0: Demo Script Cleanup & Consolidation Audit

**Date:** 2026-09-18  
**Target Branch:** `feat/ictu-2026-prototype-final`  
**System Architecture:** VIGIL AI SRS v2.0 Calibrated Actor-Centric Temporal Architecture  

---

## 1. Executive Summary

As part of the final prototype freeze for ICTU 2026, the execution paths and demo scripts of VIGIL AI were audited to eliminate legacy artifacts, consolidate duplicate code paths, and establish a single, robust, modular pipeline.

Historically, multiple demo and evaluation scripts evolved across architectural phases:
- **Phase 0 / v1.0:** Bounding-box-only object detection (`scripts/evaluate_demo_video.py`).
- **Phase 1 (Intermediate):** 2-Stage Pose with 2D heuristics (`scripts/evaluate_2stage_pose.py`).
- **Phase 2 (MVP SRS v2.0):** Monolithic single-video runner (`scripts/run_classroom_demo.py`).
- **Phase 3 (Multi-Video Prototype):** Monolithic multi-video runner (`scripts/run_demo_all_videos.py`).

This audit defines the consolidation strategy into a clean modular package: `classroom_monitor.demo` (`config.py`, `renderer.py`, `exporter.py`, `runner.py`) with clean CLI frontends (`scripts/run_demo_video.py`, `scripts/run_demo_all_videos.py`, `scripts/run_prototype.ps1`).

---

## 2. Complete Inventory & Script Classification

| File Path | Original Purpose | Decision | Rationale & Migration Path |
|---|---|---|---|
| `scripts/evaluate_demo_video.py` | Legacy v1.0 custom-class YOLOv12s object detector demo | **DELETE** | Obsolete. Bounding-box detection without keypoints/seat calibration produces invalid demo semantics. |
| `scripts/evaluate_2stage_pose.py` | Intermediate 2D heuristic pose script | **DELETE** | Obsolete. Superseded by 6DRepNet head orientation and SRS v2 temporal episode engine. |
| `scripts/run_classroom_demo.py` | Monolithic v2.0 single-video runner (india_classroom only) | **DELETE / REPLACE** | Obsolete monolith. Core logic moved into modular `classroom_monitor.demo.runner.run_demo_pipeline`. References updated. |
| `scripts/run_demo_video.py` | CLI entry point for single video runs | **REFACTOR** | Rewritten as a clean CLI wrapper over `classroom_monitor.demo.runner.run_demo_pipeline()` supporting `--video india/student`, `--input`, `--scene`, `--output`, `--show`, `--debug-overlay`. |
| `scripts/run_demo_all_videos.py` | Monolithic batch runner for all videos | **REFACTOR** | Modularized to import `classroom_monitor.demo` and run sequential execution across `india_classroom` and `student_classroom`. |
| `scripts/run_prototype.ps1` | PowerShell launcher for competition execution | **REFACTOR** | Hardened with fail-fast pre-checks (Python venv, PyTorch CUDA, video files, model weights) and clean exit codes. |
| `scripts/run_head_orientation_ab_benchmark.py` | A/B Benchmark (Pose Heuristic vs 6DRepNet) | **KEEP** | Core research & verification tool. Updated imports to use `classroom_monitor.demo.runner`. |
| `scripts/benchmark_temporal_ground_truth.py` | Temporal Ground Truth IoU & Precision/Recall evaluation | **KEEP** | Essential benchmark metric utility. Updated imports to use `classroom_monitor.demo.runner`. |
| `scripts/benchmark_10_20_rooms.py` | Multi-room scalability simulation | **KEEP** | Scalability benchmark for 10-20 concurrent room streams. |
| `scripts/diagnose_fp_episodes.py` | False positive root-cause telemetry diagnosis | **KEEP** | Diagnostic utility for 6DRepNet tuning. Updated imports to use `classroom_monitor.demo.runner`. |
| `scripts/freeze_ground_truth.py` | Freeze human ground truth annotations | **KEEP** | Data workbench utility. |
| `scripts/inspect_ground_truth.py` | Inspect ground truth json structure | **KEEP** | Data inspection tool. |
| `scripts/inspect_room_calib.py` | Inspect room calibration profiles | **KEEP** | Calibration verification tool. |
| `scripts/seed_data_workbench.py` | Seed SQLite database with sites, rooms, cameras, seats | **KEEP** | Database initialization and workbench tool. |
| `scripts/verify_6drepnet_signs.py` | Verify yaw/pitch mathematical signs | **KEEP** | Unit verification tool. |
| `scripts/verify_seat_roi_calibration.py` | Visual check of calibrated seat polygons | **KEEP** | Calibration visualization tool. |
| `run_2stage_demo.bat` | Legacy batch file for 2stage demo | **DELETE** | Obsolete. References deleted script `evaluate_2stage_pose.py`. |
| `run_demo_video.bat` | Legacy batch file for demo evaluation | **REFACTOR** | Updated to execute `scripts/run_demo_video.py --video india --show`. |
| `run_classroom_demo.bat` | Legacy batch file for mock demo | **REFACTOR** | Updated to execute `scripts/run_demo_video.py --video india`. |

---

## 3. Target Modular Architecture (`classroom_monitor.demo`)

```
classroom_monitor/
  ├── demo/
  │   ├── __init__.py          # Exports run_demo_pipeline, DemoVideoConfig, setup_room_seats
  │   ├── config.py            # DemoVideoConfig dataclass, DEMO_VIDEOS registry, CLI parser
  │   ├── renderer.py          # Frame HUD overlay & debug telemetry rendering
  │   ├── exporter.py          # Standardized exporter (canonical episodes.json, events, summary, runtime)
  │   └── runner.py            # Core pipeline orchestrator (Video -> YOLO-Pose -> Seat -> 6DRepNet -> Temporal -> Risk -> Evidence)
```

### Standardized Output Contract (`data/demo_final/<video_name>/`)
- `result.mp4`: Annotated full demo video with HUD overlay.
- `events.json`: High-level cheating review events with canonical statuses (`FLAGGED_FOR_REVIEW`).
- `episodes.json`: Deduplicated, canonical temporal episodes (indexed by `episode_id`).
- `patterns.json`: Detected multi-frame behavior patterns (glance repetition, dwell, etc.).
- `demo_summary.json`: Executive statistics, pipeline metrics, seat risk summaries.
- `runtime_profile.json`: FPS, per-stage latency breakdown (Perception, 6DRepNet, Temporal, Risk, Render, Buffer).
- `evidence/`: 10-second MP4 evidence clips and peak frame snapshots with SHA-256 integrity verification.

---

## 4. Migration & Safety Verification Matrix

| Component | Dependent Modules | Validation Test |
|---|---|---|
| `run_demo_pipeline` | `scripts/run_demo_video.py`, `scripts/run_demo_all_videos.py`, `scripts/run_head_orientation_ab_benchmark.py` | `tests/test_demo_runner_infrastructure.py` |
| `setup_room_seats` | `scripts/diagnose_fp_episodes.py`, `scripts/run_head_orientation_ab_benchmark.py` | `tests/test_classroom_core.py` |
| Canonical `episodes.json` | `scripts/benchmark_temporal_ground_truth.py`, Data Workbench | `tests/test_demo_runner_infrastructure.py` (D3) |
| Non-Cheating Direct Verdicts | Event Engine, Risk Tracker | `tests/test_demo_runner_infrastructure.py` (D4) |

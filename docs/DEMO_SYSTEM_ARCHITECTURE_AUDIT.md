# VIGIL AI — DEMO SYSTEM ARCHITECTURE AUDIT REPORT
**SRS v2 Pipeline vs Legacy Inference Route vs Unified Demo Architecture**

> **Date:** September 21, 2026  
> **Repository:** `HoanBuCon/Laser-Eyes`  
> **Target Branch:** `feat/ictu-2026-prototype-final`  
> **Auditor:** Senior AI Systems Architect & Core Logic Auditor  

---

## 1. Executive Summary

This architecture audit confirms a critical architectural gap in the repository prior to this integration:

```
+---------------------------------------------------------------------------------------+
|                                ARCHITECTURAL GAP FOUND                                |
+---------------------------------------------------------------------------------------+
| 1. LEGACY API INFERENCE PATH (api/routes/inference.py):                               |
|    - Calls `classroom_monitor.video_processor.VideoProcessor`                         |
|    - Uses single-stage frame-by-frame 2D heuristics (`ScoreAccumulator`, legacy FPs) |
|    - Lacks Seat ROI mapping, 6DRepNet head orientation, Temporal Episodes,            |
|      Behavior Patterns, and Incident Aggregation.                                     |
|                                                                                       |
| 2. CANONICAL SRS V2 DEMO PIPELINE (classroom_monitor/demo/runner.py):                 |
|    - Implements the complete actor-centric SRS v2 proctoring stack:                   |
|      YOLO-Pose -> Seat ROI Mapping -> Batched 6DRepNet -> Raw Observations ->         |
|      Temporal Episodes -> Behavior Patterns -> Seat Risk / Incident Aggregator ->     |
|      Review Incidents -> 10s MP4 Evidence Buffer -> Human Review.                     |
|    - Output was previously isolated to CLI / JSON files (events.json, result.mp4)     |
|      and NOT connected to the live Web Dashboard / Review Queue API.                  |
|                                                                                       |
| 3. RISK:                                                                              |
|    - If the Web Dashboard were connected to /api/v1/inference, it would serve        |
|      an obsolete pipeline that violates the SRS v2 specification.                     |
+---------------------------------------------------------------------------------------+
```

---

## 2. Detailed Pipeline Comparison

| Architectural Aspect | Legacy Inference (`api/routes/inference.py`) | SRS v2 Pipeline (`classroom_monitor/demo/runner.py`) | Unified Demo Runtime (`classroom_monitor/demo/runtime.py`) |
| :--- | :--- | :--- | :--- |
| **Perception Engine** | Single-stage YOLO (`classroom_best.pt`) | Multi-task YOLO-Pose + 6DRepNet HPE | Multi-task YOLO-Pose + 6DRepNet HPE |
| **Spatial Matching** | Uncalibrated bbox tracking | Calibrated `SeatROI`, polygon desk geometry | Calibrated `SeatROI`, polygon desk geometry |
| **Head Orientation** | Heuristic 2D landmarks | 6DRepNet deep yaw/pitch relative to Seat baseline | 6DRepNet deep yaw/pitch relative to Seat baseline |
| **Temporal Logic** | Sliding window frame counter | `TemporalEpisodeEngine` with hysteresis & grace | `TemporalEpisodeEngine` with hysteresis & grace |
| **Composite Patterns** | None (instantaneous frame alert) | `BehaviorPatternEngine` (Relational glance, lean, dwell) | `BehaviorPatternEngine` (Relational glance, lean, dwell) |
| **Risk Scoring** | Accumulative frame score | `SeatRiskTracker` (0–100 Review Priority, time decay) | `SeatRiskTracker` (0–100 Review Priority, time decay) |
| **Alert Spam Control** | Primitive cooldown timer | Episodic incident merge & recidivism escalation | Episodic incident merge & recidivism escalation |
| **Evidence Output** | Single 2D frame snapshot | 10-second MP4 video buffer with SHA-256 | 10-second MP4 video buffer with SHA-256 |
| **Human Review UX** | Binary cheating alert | Human-in-the-loop Review Queue with decision reasons | Human-in-the-loop Review Queue with decision reasons |
| **Integration Target** | Deprecated / Staging only | CLI export only (`run_demo_video.py`) | **Single Source of Truth for Web, CLI, API & DB** |

---

## 3. The Unified Demo Architecture Solution

To establish **SRS v2 as the Single Source of Truth** across both CLI and Web:

```
+---------------------------------------------------------------------------------------+
|                                 UNIFIED DEMO RUNTIME                                  |
|                         (classroom_monitor/demo/runtime.py)                           |
+---------------------------------------------------------------------------------------+
      |                                              |
      v                                              v
[MODE A: LIVE ANALYSIS]                   [MODE B: RECORDED REPLAY]
Runs real SRS v2 engine                   Streams previously evaluated
(YOLO-Pose + 6DRepNet + Episodes)         result.mp4 + events.json
Shows real processing FPS & speed         Preserves precise source timestamps
      |                                              |
      +----------------------+-----------------------+
                             |
                             v
                 Unified Callback Dispatcher
                 - on_frame (MJPEG Stream)
                 - on_event (SQLite & WS Broadcast)
                 - on_status (Live Telemetry)
                             |
         +-------------------+-------------------+
         |                                       |
         v                                       v
[FastAPI Demo API]                      [CLI Demo Scripts]
- /api/v1/demo/stream (MJPEG)           - scripts/run_demo_video.py
- /api/v1/demo/status (Telemetry)       - scripts/run_demo_all_videos.py
- /api/v1/demo/events (Review Queue)    - scripts/run_prototype.ps1
- /ws/events (WebSocket notifications)
         |
         v
[Web Demo Interface (/demo)]
Projector & judging panel layout:
- Clean Proctor Video Monitor
- Real-time Session & Risk Metrics
- Human-in-the-loop Review Queue
- Evidence Modal (Video + Snapshot + SHA-256)
- Review Decision Controls (Confirm / Reject / Inconclusive)
```

---

## 4. Policy Regarding Legacy Inference Path

1. `classroom_monitor/video_processor.py` and `api/routes/inference.py` are preserved solely for backward compatibility.
2. They are explicitly marked with `[LEGACY / DEPRECATED]` docstrings.
3. The new competition demo system (`/demo`, `api/routes/demo.py`, `scripts/run_demo_system.ps1`) strictly uses `classroom_monitor.demo.runtime.DemoRuntime`.

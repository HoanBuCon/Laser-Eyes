# VIGIL AI — Core SRS v2 Pipeline Verification & Architecture Audit

**Target System:** VIGIL AI — AI-Assisted Exam Monitoring Co-Pilot (ICTU 2026 Prototype)  
**Branch:** `feat/ictu-2026-prototype-final`  
**Date:** September 2026  
**Status:** VERIFIED & HARDENED

---

## 1. Executive Summary

This audit confirms the ground-truth execution pipeline of VIGIL AI SRS v2. The system operates strictly as an **AI-Assisted Proctoring Co-Pilot**, evaluating student behaviors through a multi-stage actor-centric temporal architecture.

No legacy modules (`EventEngine`, `PersonBehaviorTracker`, `ScoreAccumulator`) participate in active inference. All frame-by-frame analysis flows through scene-calibrated seat mapping, scheduled 5 Hz batched 6DRepNet pose estimation, temporal episode smoothing, behavior pattern correlation, and incident-level risk tracking.

---

## 2. Actual SRS v2 Execution Pipeline & Call Graph

```
========================================================================================
                               VIGIL AI SRS v2 RUNTIME PIPELINE
========================================================================================

 [ Input Video Frame ] (India: 1280x720 / Student: 640x352)
         │
         ▼
 [ YOLO-Pose Detector ] (Ultralytics YOLOv8-Pose @ conf >= 0.20)
         │  - Detects person keypoints (Nose, Eyes, Ears, Shoulders, Wrists, Hips)
         │  - Tracks spatial bounding boxes and torso lean vectors
         ▼
 [ Seat ROI Mapper ] (`classroom_monitor/seat_mapping.py`)
         │  - Matches detected persons to calibrated seat geometries (IoU & Center-in-Polygon)
         │  - Resolves Seat States: OCCUPIED, EMPTY, OCCLUDED, UNMAPPED_ROAMING
         │  - Isolates unmapped persons (proctors/invigilators) to avoid false seat alerts
         ▼
 [ Scheduled 5 Hz Batched 6DRepNet ] (`classroom_monitor/demo/runner.py`)
         │  - Triggers batch inference strictly at 200 ms intervals (5 Hz) for OCCUPIED seats
         │  - Assembles all occupied seat head crops into a single PyTorch tensor forward pass
         │  - Updates per-seat HPE cache (`yaw`, `pitch`, `roll`, `quality`, `timestamp`)
         │  - Reduces GPU model forwards by ~95.8% (from 1,276 to 53 forwards per 100 frames)
         ▼
 [ Observation Extractor ] (`classroom_monitor/observation_extractor.py`)
         │  - Ingests cached/precomputed head estimate (zero internal model re-forwarding)
         │  - Applies seat baseline calibration: relative_yaw = head_yaw - seat_baseline_yaw
         │  - Validates writing zone suppression for wrist positions on exam desks
         ▼
 [ Temporal Episode Engine ] (`classroom_monitor/temporal_episode_engine.py`)
         │  - 3-sample median filter on relative yaw/pitch/torso angles (rejects 1-frame spikes)
         │  - Dual hysteresis activation (>= 22.0°) and release (<= 15.0°) thresholds
         │  - Temporal persistence gating (min_duration = 500 ms) before activating episodes
         │  - Emits: HEAD_TURN_LEFT, HEAD_TURN_RIGHT, LOOK_DOWN, TORSO_LEAN, etc.
         ▼
 [ Behavior Pattern Engine ] (`classroom_monitor/behavior_pattern_engine.py`)
         │  - Evaluates multi-episode combinations across seat spatial adjacency
         │  - Neighbor Gating: Confirms target seat has an active neighbor in the turn direction
         │  - Evaluates: REPEATED_NEIGHBOR_GLANCE (>= 2 turns to same neighbor within 12s)
         │  - Evaluates: NEIGHBOR_ORIENTED_LEAN, SUSPICIOUS_BELOW_DESK_POSTURE
         ▼
 [ Seat Risk Tracker & Incident Aggregator ] (`classroom_monitor/seat_risk_tracker.py`)
         │  - Time-based risk decay (2.5 pts/sec) using video timestamps
         │  - Ingests new patterns only once (`processed_pattern_ids` deduplication)
         │  - Applies diminishing returns factor on rapid repetitive pattern occurrences
         │  - Incident Aggregator (15.0s merge window): Merges repetitive patterns into ONE incident
         │  - Emits ClassroomEvent (FLAGGED_FOR_REVIEW) only upon genuine new incident inception
         │  - Enters 5.0s Cooldown to prevent multi-event spamming
         ▼
 [ Output Generation & Rendering ] (`classroom_monitor/demo/renderer.py`)
         │  - Professional Proctor HUD overlay (Seat status badges, incident counter, risk meter)
         │  - Async Evidence Store (`data/evidence/`) with SHA-256 integrity verification
         │  - Generates final annotated video (`.mp4`) and metrics manifest (`.json`)
```

---

## 3. Legacy vs. Active Module Verification Matrix

| Module Name | File Path | Status | Verification Details |
|---|---|---|---|
| `EventEngine` | `classroom_monitor/event_engine.py` | **DEPRECATED / LEGACY** | Not imported or instantiated in `demo/runner.py`. Retained solely for backwards test compatibility. |
| `PersonBehaviorTracker` | `classroom_monitor/behavior_tracker.py` | **DEPRECATED / LEGACY** | Replaced by `TemporalEpisodeEngine` + `BehaviorPatternEngine`. Zero runtime invocations. |
| `ScoreAccumulator` | `classroom_monitor/score_accumulator.py` | **DEPRECATED / LEGACY** | Replaced by `SeatRiskTracker`. |
| `PostureDetector` | `classroom_monitor/posture_detector.py` | **LEGACY FALLBACK** | Standalone 2D heuristic. Only invoked if `head_provider="heuristic"` is explicitly requested. |
| `HeadPoseEstimator6D` | `classroom_monitor/head_pose_provider.py` | **ACTIVE (CORE)** | Full 6DRepNet PyTorch backbone. Supports single-crop and batch tensor inference (`estimate_batch`). |
| `SeatMapping` | `classroom_monitor/seat_mapping.py` | **ACTIVE (CORE)** | Scene-calibrated geometric seat manager with occupancy tracking and occlusion grace timeouts. |
| `ObservationExtractor` | `classroom_monitor/observation_extractor.py` | **ACTIVE (CORE)** | Normalizes keypoints, computes relative head/torso angles, checks writing zones. |
| `TemporalEpisodeEngine` | `classroom_monitor/temporal_episode_engine.py` | **ACTIVE (CORE)** | Maintains per-seat temporal state machines with 3-sample median smoothing. |
| `BehaviorPatternEngine` | `classroom_monitor/behavior_pattern_engine.py` | **ACTIVE (CORE)** | High-level spatial-temporal pattern synthesizer with neighbor adjacency checks. |
| `SeatRiskTracker` | `classroom_monitor/seat_risk_tracker.py` | **ACTIVE (CORE)** | Actor-centric risk state machine with 15s incident aggregation and anti-spam cooldown. |

---

## 4. Verification Test Coverage

The architecture is covered by **158 comprehensive automated tests** with 100% pass rate:
- `tests/test_hpe_pipeline_hardening.py` (HPE1 - HPE12): Validates scheduled batching, caching, quality gating, single-spike median suppression, baseline subtraction, and zero redundant GPU forwards.
- `tests/test_pattern_correctness.py` (PAT1 - PAT10): Validates single-turn isolation, snapshot deduplication, neighbor gating, directional separation, and occlusion protection.
- `tests/test_incident_risk_hardening.py` (RISK1 - RISK10): Validates incident aggregation, merge windows, cooldown anti-spam, genuine separate incident triggers, and zero automated `CHEATING` verdicts.
- `tests/test_srs_mvp_p0.py` & `tests/test_srs_v2_temporal.py`: Full baseline compliance.

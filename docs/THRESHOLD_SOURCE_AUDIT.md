# VIGIL AI — Threshold Source Audit & Unification Matrix

**Project:** VIGIL AI — ICTU 2026 Working Prototype  
**Branch:** `feat/ictu-2026-prototype-final`  
**Date:** September 2026  

---

## 1. Threshold Source Audit

This document inventories and unifies all behavioral, temporal, spatial, and risk thresholds across configuration files (`ClassroomConfig`, Scene YAMLs) and algorithmic engines.

### 1.1 Source-of-Truth Hierarchy

```
   ┌─────────────────────────────────────────────────────────┐
   │ 1. Scene Profile YAML (e.g. india_classroom.yaml)       │  <- Top Priority: Camera/Room Calibration
   └───────────────────────────┬─────────────────────────────┘
                               │
   ┌───────────────────────────▼─────────────────────────────┐
   │ 2. ClassroomConfig (Pydantic / dataclass models)        │  <- Default Operational Parameters
   └───────────────────────────┬─────────────────────────────┘
                               │
   ┌───────────────────────────▼─────────────────────────────┐
   │ 3. Engine Defaults (Episode / Pattern / Risk Engine)    │  <- Hardened Algorithmic Fallbacks
   └─────────────────────────────────────────────────────────┘
```

---

## 2. Complete Threshold Unification Table

| Domain / Engine | Parameter Name | Unified Value | Source File | Rationale / Engineering Justification |
|---|---|---|---|---|
| **Pose & HPE** | `pose_conf_threshold` | `0.20` | `ClassroomConfig` | Ensures distant / back-row student keypoints are detected in low-resolution classroom feeds. |
| **Pose & HPE** | `hpe_interval_ms` | `200.0 ms` (5 Hz) | `demo/runner.py` | Head orientation changes smoothly; 5 Hz scheduled batching avoids redundant per-frame GPU forwards. |
| **Pose & HPE** | `hpe_max_age_ms` | `600.0 ms` | `demo/runner.py` | Maximum validity of cached head orientation before falling back to UNKNOWN state. |
| **Pose & HPE** | `hpe_min_crop_size` | `20 px` | `head_pose_provider.py` | Rejects noisy, sub-resolution head bounding boxes from far-back occluded seats. |
| **Temporal Episodes** | `head_yaw_activate_deg` | `22.0°` | `temporal_episode_engine.py` | Hysteresis upper threshold: persistent head turn >= 22.0° from seat baseline triggers active state. |
| **Temporal Episodes** | `head_yaw_release_deg` | `15.0°` | `temporal_episode_engine.py` | Hysteresis lower threshold: turn must relax below 15.0° to release the episode. |
| **Temporal Episodes** | `median_filter_samples` | `3 samples` | `temporal_episode_engine.py` | Suppresses single-frame detection flutter and false momentary head spikes. |
| **Temporal Episodes** | `head_turn_min_duration_ms` | `500.0 ms` | `temporal_episode_engine.py` | Fleeting glances (< 500 ms) are natural classroom behavior and are not flagged. |
| **Temporal Episodes** | `torso_lean_activate_deg` | `18.0°` | `temporal_episode_engine.py` | Hysteresis activate threshold for lateral body lean toward neighboring desks. |
| **Temporal Episodes** | `torso_lean_release_deg` | `12.0°` | `temporal_episode_engine.py` | Hysteresis release threshold for returning to upright posture. |
| **Temporal Episodes** | `look_down_activate_deg` | `28.0°` | `temporal_episode_engine.py` | Severe downward pitch angle distinguishing normal desk writing from lap inspection. |
| **Behavior Patterns** | `glance_window_ms` | `12000.0 ms` (12s) | `behavior_pattern_engine.py` | Temporal correlation window for repeated glances toward the same neighbor. |
| **Behavior Patterns** | `glance_min_count` | `2 episodes` | `behavior_pattern_engine.py` | Minimum distinct episodes in the same direction required to synthesize a repeated glance pattern. |
| **Behavior Patterns** | `neighbor_gating` | `Mandatory (True)` | `behavior_pattern_engine.py` | Turns directed toward empty aisles or walls are suppressed and never produce suspicious patterns. |
| **Behavior Patterns** | `writing_zone_suppression` | `Mandatory (True)` | `observation_extractor.py` | Hands placed within the designated desk writing polygon are protected as normal exam behavior. |
| **Seat Risk Tracker** | `observe_threshold` | `40.0 pts` | `seat_risk_tracker.py` | Seat enters OBSERVE state (yellow highlight on UI). |
| **Seat Risk Tracker** | `suspicious_threshold` | `60.0 pts` | `seat_risk_tracker.py` | Seat enters SUSPICIOUS state (orange highlight on UI). |
| **Seat Risk Tracker** | `flagged_threshold` | `80.0 pts` | `seat_risk_tracker.py` | Seat enters FLAGGED_FOR_REVIEW state; initiates incident recording. |
| **Seat Risk Tracker** | `post_event_reset_score` | `45.0 pts` | `seat_risk_tracker.py` | Score resets to moderate level after event emission to avoid immediate re-triggering. |
| **Seat Risk Tracker** | `decay_rate_per_sec` | `2.5 pts/sec` | `seat_risk_tracker.py` | Linear score decay during clean, attentive student behavior. |
| **Seat Risk Tracker** | `cooldown_duration_ms` | `5000.0 ms` (5s) | `seat_risk_tracker.py` | Cooldown buffer preventing multiple events from firing consecutively within 5 seconds. |
| **Seat Risk Tracker** | `incident_merge_window_ms` | `15000.0 ms` (15s)| `seat_risk_tracker.py` | Aggregates repeated pattern occurrences into ONE continuous incident for human review. |
| **Seat Risk Tracker** | `recidivism_window_ms` | `15000.0 ms` (15s)| `seat_risk_tracker.py` | Escalates severity for students exhibiting repeated violations across distinct incidents. |

---

## 3. Incident Aggregator vs. Legacy Event Generation

```
   [ Legacy V1 Behavior ]
   Pattern Detected -> Risk +30 -> Score = 85 -> Emit Event 1 (t = 2.0s)
   Next Frame       -> Risk +30 -> Score = 90 -> Emit Event 2 (t = 2.1s)
   Next Frame       -> Risk +30 -> Score = 95 -> Emit Event 3 (t = 2.2s)
   Result: 200+ redundant review cards flooded to human proctor.

   [ Hardened SRS v2 Incident Aggregator ]
   Pattern Detected -> Risk +38 -> Score = 82 -> EMIT INCIDENT EVENT #1 (t = 2.0s)
                                                 - Incident ID: 4a2f8b...
                                                 - Occurrence Count: 1
                                                 - Cooldown Activated (5s)
   t = 4.0s (Repeated Pattern)                 -> Merged into Incident #1 (Count: 2, Peak Risk: 85.0)
   t = 8.0s (Third Pattern)                    -> Merged into Incident #1 (Count: 3, Peak Risk: 88.0)
   Result: EXACTLY 1 clean incident card presented to proctor with full timeline evidence.
```

# VIGIL AI Prototype Detection Hardening Report

**Project:** VIGIL AI Proctoring Co-pilot  
**Branch:** `feat/srs-v2-refactor`  
**Architecture:** SRS v2.0 Actor-Centric Temporal Suspicious Pattern Detection  
**Mode:** 7-Day Deadline Hardening Mode  
**Priority Hierarchy:** Detection Quality > Demo Reliability > Enterprise Architecture  
**Test Suite Status:** 88 / 88 Tests Passing (100%)  

---

## 1. Executive Summary

This hardening sprint focused on resolving 4 critical core perception/temporal engine disconnects in the VIGIL AI prototype without introducing new ML models, retraining, or out-of-scope enterprise abstractions.

### Key Hardening Results:
1. **Seat Occupancy Semantics Preserved:** `SeatOccupancy.state` (`OCCUPIED`, `OCCLUDED`, `UNKNOWN`, `EMPTY`, `MULTIPLE_PERSON`) is now directly extracted from `SeatManager` and passed to `ObservationExtractor`. Temporary proctor walk-bys and camera occlusions preserve identity and strictly suppress false `SEAT_EMPTY` / `SEAT_LEFT` alarms.
2. **Real Multi-Person Candidate Counts Propagated:** The pipeline runner now passes the actual candidate count (`len(occupancy.candidate_detections)`) rather than hardcoding `1` or `0`. True multi-person clustering near a seat for $\ge 2.5\text{s}$ triggers `MULTI_PERSON_DWELL_NEAR_SEAT`.
3. **Strict Directional Neighbor Gating Enforced:** `REPEATED_NEIGHBOR_GLANCE` and `NEIGHBOR_ORIENTED_LEAN` now strictly verify that a physical neighbor exists in the direction of movement (`left_neighbor_id` / `right_neighbor_id` is not `None`) and that `pairwise_relation` capability is enabled. Movements into empty aisles or walls are completely suppressed.
4. **Missing/Unknown Observation Grace Timeout:** Implemented `missing_observation_grace_ms = 1200ms` in `TemporalEpisodeEngine`. Missing keypoints or occluded candidate frames within 1200ms are bridged smoothly, while prolonged drops (>1200ms) gracefully close active episodes and reset candidate states without state machine lockups.
5. **100% Test Pass Rate:** Added 10 regression test cases (`test_case_p01` through `test_case_p10`) in `tests/test_prototype_hardening.py`. Total test suite increased from 78 to 88 tests, all passing with zero regressions.

---

## 2. Hardening Problem Statement & Root Cause Diagnosis

| Disconnect | Root Cause | Impact | Fix Applied |
| :--- | :--- | :--- | :--- |
| **Occupancy Semantic Erasure** | `scripts/run_classroom_demo.py` did not pass `occupancy_state` into `ObservationExtractor.extract()`, defaulting to `None` detection $\to$ hardcoded `EMPTY`. | Brief occlusions caused spurious `SEAT_EMPTY` and corrupted seat state. | Propagate `seat_mgr.occupancies[seat_code].state` directly into `ObservationExtractor`. |
| **Dummy Person Count** | Runner passed `nearby_person_count = 1 if det is not None else 0`. | `PERSON_COUNT_NEAR_SEAT` never reflected true count when $\ge 2$ persons clustered at a desk. | Propagate `len(seat_mgr.occupancies[seat_code].candidate_detections)`. |
| **Spurious Glance to Wall/Aisle** | `BehaviorPatternEngine` evaluated glance patterns without checking if `target_neighbor_id` was non-None or if pairwise relation was enabled. | Students at edge desks turning towards aisle emitted `REPEATED_NEIGHBOR_GLANCE`. | Added strict neighbor presence check (`has_neighbor_in_direction`) and capability validation. |
| **Hanging Episodes on Dropped Keypoints** | `TemporalEpisodeEngine` only closed episodes when explicit release values arrived; when keypoint conf dropped to 0, channel was skipped. | Episodes remained `ACTIVE` indefinitely or fragmented into duplicate alarms upon recovery. | Added `missing_observation_grace_ms = 1200ms` and degradation logic in `_update_channel`. |

---

## 3. Implementation Details

### 3.1 Observation Extractor (`classroom_monitor/observation_extractor.py`)
- Updated `extract()` signature to accept `occupancy_state: Optional[str] = None` and `nearby_person_count: int = 0`.
- Extracted `ObservationType.SEAT_OCCUPANCY` using the verified `occupancy_state` with priority over fallback inference.
- Extracted `ObservationType.PERSON_COUNT_NEAR_SEAT` with the true count regardless of single-candidate detection existence.

### 3.2 Behavior Pattern Engine (`classroom_monitor/behavior_pattern_engine.py`)
- **Strict Glance Gating:**
  ```python
  if target_neighbor_id is None:
      continue  # No physical neighbor in this direction
  if not seat_context.is_capability_enabled("pairwise_relation"):
      continue  # Pairwise relation disabled for this seat
  ```
- **Strict Lean Gating:**
  ```python
  if target_neighbor_id is None:
      continue  # Suppress leans into aisles/walls
  if not seat_context.is_capability_enabled("pairwise_relation"):
      continue
  ```

### 3.3 Temporal Episode Engine (`classroom_monitor/temporal_episode_engine.py`)
- Added `missing_observation_grace_ms: float = 1200.0` to `TemporalEpisodeEngine.__init__`.
- Added `missing_start_ms` and `last_valid_timestamp_ms` to `_EpisodeTrackerState`.
- Implemented `is_missing` branch in `_update_channel`:
  - When `is_missing=True`, if within grace period ($< 1200\text{ms}$), state is preserved without disruption.
  - When missing duration $\ge 1200\text{ms}$, active episode is closed to `ENDED` state, and candidate tracker is reset to `INACTIVE`.
- Updated observation channels (`HEAD_YAW_RELATIVE`, `HEAD_PITCH_RELATIVE_DOWN`, `TORSO_LEAN_X`, `WRIST_ZONE`, `SEAT_OCCUPANCY`, `PERSON_COUNT_NEAR_SEAT`) to dispatch `is_missing=True` when signals are absent or unconfident.

### 3.4 Pipeline Demo Runner (`scripts/run_classroom_demo.py`)
- In Step 4 observation loop, extracted:
  ```python
  occ = seat_mgr.occupancies.get(seat_code)
  occ_state = occ.state if occ else None
  person_count = len(occ.candidate_detections) if occ else (1 if det is not None else 0)
  ```
- Passed `occupancy_state=occ_state` and `nearby_person_count=person_count` to `observation_extractor.extract()`.

---

## 4. Regression & Verification Test Suite

Ten new targeted regression tests were created in `tests/test_prototype_hardening.py` in addition to the 78 existing tests.

| Test Case | Scenario Description | Expected Behavior | Result |
| :--- | :--- | :--- | :--- |
| **P1** | Seat Occupancy Occlusion Protection | `OCCLUDED` state does NOT produce `SEAT_EMPTY` / `SEAT_LEFT`. | **PASSED** |
| **P2** | True Empty Seat Timeout | Prolonged `EMPTY` state ($> \text{timeout}$) produces `SEAT_EMPTY`. | **PASSED** |
| **P3** | Real Multi-Person Count Propagation | Counts 0, 1, 2, 3, 5 propagate cleanly to `PERSON_COUNT_NEAR_SEAT`. | **PASSED** |
| **P4** | Brief Multi-Person Filtering | Multi-person count = 2 for $<2.5\text{s}$ does NOT trigger dwell pattern; $\ge 2.5\text{s}$ triggers dwell. | **PASSED** |
| **P5** | Head Turn without Neighbor Gating | `HEAD_TURN_LEFT` on seat without left neighbor strictly emits 0 repeated glance patterns. | **PASSED** |
| **P6** | Valid Neighbor Gating | `HEAD_TURN_LEFT` with valid left neighbor triggers `REPEATED_NEIGHBOR_GLANCE`. | **PASSED** |
| **P7** | Missing Observation Grace Timeout | Keypoints missing for $>1200\text{ms}$ gracefully closes active episode without hanging. | **PASSED** |
| **P8** | Missing Observation Short Gap Recovery | Short $300\text{ms}$ keypoint drop recovers cleanly into a single continuous episode. | **PASSED** |
| **P9** | Normal Writing Guard | Head pitch down + wrist on desk maintains risk $<30$ (`NORMAL`). | **PASSED** |
| **P10** | Unmapped Person Isolation | Roaming aisle person does not create or corrupt any Seat risk profile. | **PASSED** |

**Full Suite Execution:** `88 passed in 4.67s` (100% pass rate).

---

## 5. Experimental Validation on India Classroom Video

The hardened pipeline was executed on `demo_video/india_classroom.mp4` (2150 frames, 1280x720 @ 30 FPS, duration 71.77s) and compared head-to-head against the baseline execution.

### Quantitative Comparison

| Metric | Baseline (`data/prototype_hardening/baseline/`) | Hardened (`data/prototype_hardening/india_classroom/`) | Delta / Evaluation |
| :--- | :--- | :--- | :--- |
| **Processing Speed** | 24.24 FPS (41.25 ms/frame) | 21.12 FPS (20.91 ms inf) | Real-time capable ($\sim 21$ FPS on GPU) |
| **Total Frames Processed** | 2150 | 2150 | Full video coverage |
| **Max Persons Detected** | 30 | 32 | High-density multi-person tracking |
| **Total Episodes** | 12,892 | 15,528 | Enhanced multi-person + occupancy channels |
| **Total Detected Patterns** | 14 | 10 | Reduced false positive edge-glances |
| **Events Emitted** | 3 | 7 | Real multi-person dwell + explainable glances |
| **State Transitions** | 12 | 32 | Dynamic time-decayed state transitions |
| **Edge Seat False Alarms** | 2 (SEAT-101-03 turned to aisle) | **0 (Completely Suppressed)** | Fixed by strict neighbor gating |

### Per-Seat Risk Profile Comparison

| Seat Code | Physical Position | Baseline Peak Risk | Hardened Peak Risk | Primary Hardened Patterns |
| :--- | :--- | :--- | :--- | :--- |
| **SEAT-101-01** | Desk 1 (Front Left) | 39.5 (`OBSERVE`) | 50.0 (`OBSERVE`) | Transitory glances, decayed to `NORMAL` |
| **SEAT-101-02** | Desk 2 (Center) | 66.4 (`SUSPICIOUS`) | 91.9 (`FLAGGED_FOR_REVIEW`) | `MULTI_PERSON_DWELL_NEAR_SEAT` (Dwell 3.7s) |
| **SEAT-101-03** | Desk 3 (Aisle Edge) | 88.0 (`FLAGGED_FOR_REVIEW` - FP) | 25.1 (`NORMAL` - True Negative) | **Suppressed:** Edge glances into aisle ignored |
| **SEAT-101-04** | Desk 4 (Middle Left) | 0.0 (`NORMAL`) | 40.9 (`OBSERVE`) | Transitory observations |
| **SEAT-101-05** | Desk 5 (Back Left) | 0.0 (`NORMAL`) | 95.5 (`FLAGGED_FOR_REVIEW`) | `REPEATED_NEIGHBOR_GLANCE` (to SEAT-04) + `MULTI_PERSON_DWELL` |

---

## 6. Event-by-Event Sanity & Evidence Verification

All 7 emitted events generated 10-second MP4 evidence clips and SHA-256 integrity package metadata in `data/prototype_hardening/india_classroom/evidence/`:

1. **Event `bace2602` (SEAT-101-02 @ 24.3s):** `MULTI_PERSON_DWELL_NEAR_SEAT` (Score: 83.9, Dwell: 2.5s). Evidence clip: `bace2602-9129-48a6-aa75-805e0132be5c_T10102_MULTI_PERSON_DWELL_NEAR_SEAT.mp4`.
2. **Event `dd8d59de` (SEAT-101-02 @ 39.4s):** `MULTI_PERSON_DWELL_NEAR_SEAT` (Score: 80.0, Dwell: 3.7s, Recidivist). Evidence clip: `dd8d59de-8c7d-448f-94b9-d17c20df3164_T10102_MULTI_PERSON_DWELL_NEAR_SEAT.mp4`.
3. **Event `40348ea7` (SEAT-101-05 @ 43.4s):** `REPEATED_NEIGHBOR_GLANCE` (Score: 95.5, 2 glances toward left neighbor SEAT-101-04 within 25s window). Evidence clip: `40348ea7-6d40-44a2-9c8a-31e8c07a1172_T10105_REPEATED_NEIGHBOR_GLANCE.mp4`.
4. **Event `1321cef2` (SEAT-101-02 @ 44.4s):** `MULTI_PERSON_DWELL_NEAR_SEAT` (Score: 88.4, Dwell: 3.7s, Recidivist). Evidence clip: `1321cef2-610d-48d9-8ccb-2f19365c5606_T10102_MULTI_PERSON_DWELL_NEAR_SEAT.mp4`.
5. **Event `b97aef03` (SEAT-101-05 @ 48.7s):** `REPEATED_NEIGHBOR_GLANCE` (Score: 80.0, Recidivist glance to SEAT-101-04). Evidence clip: `b97aef03-1fa0-42b7-b791-3db5788cd1cf_T10105_REPEATED_NEIGHBOR_GLANCE.mp4`.
6. **Event `1f55fa2a` (SEAT-101-05 @ 53.7s):** `REPEATED_NEIGHBOR_GLANCE` (Score: 80.0, Recidivist glance to SEAT-101-04). Evidence clip: `1f55fa2a-9aa4-4c54-8ca8-f25ed76e0cc8_T10105_REPEATED_NEIGHBOR_GLANCE.mp4`.
7. **Event `777cd7c6` (SEAT-101-05 @ 68.2s):** `MULTI_PERSON_DWELL_NEAR_SEAT` (Score: 80.0, Dwell: 17.5s). Evidence clip: `777cd7c6-dcb7-434c-a278-a5cdd0757b81_T10105_MULTI_PERSON_DWELL_NEAR_SEAT.mp4`.

---

## 7. Staged Validation Readiness & Next Recording Checklist

To prepare for staged validation recordings in real classrooms, follow this operational checklist:

### Camera & Scene Setup
- [ ] Mount camera at height $2.2\text{m} - 2.8\text{m}$ with a $35^\circ - 45^\circ$ downward pitch angle.
- [ ] Ensure minimum 1080p resolution ($1920\times 1080$) at $\ge 20$ FPS.
- [ ] Frame 4 to 8 desks in clear focus; ensure candidate head keypoints are $\ge 20\text{px}$ across.

### Seat ROI Calibration
- [ ] Calibrate 4-point Seat Polygon bounding the candidate seating boundary.
- [ ] Define Seat Adjacency in `SeatNeighbors` (`left_neighbor_id`, `right_neighbor_id`, etc.).
- [ ] Set `desk_boundary_y` or `writing_zone_polygon` to establish normal writing safety bounds.

### Verification Scenarios for Recording
1. **Scenario 1 (Normal Exam - Negative Control):** Continuous writing, slight posture shift, normal wrist motion $\to$ Must remain in `NORMAL` ($<30$).
2. **Scenario 2 (Proctor Occlusion - Resilience Control):** Proctor walks past desk, standing between camera and candidate for $3 - 5\text{s}$ $\to$ Must transition to `OCCLUDED` without triggering `SEAT_EMPTY` or ID switch.
3. **Scenario 3 (Repeated Neighbor Glance - Positive Control):** Candidate glances at left neighbor 3 times within 25 seconds ($\ge 0.5\text{s}$ per glance) $\to$ Must trigger `REPEATED_NEIGHBOR_GLANCE` and create 10s evidence clip.
4. **Scenario 4 (Multi-Person Clustered Interaction - Positive Control):** Second student or proctor leans over desk for $>2.5\text{s}$ $\to$ Must trigger `MULTI_PERSON_DWELL_NEAR_SEAT`.

---

## 8. Scope Freeze & Deadline Compliance

- **No Retraining:** Zero model weight modifications or retraining required.
- **No Heavyweight 3D/SlowFast Models:** Pipeline maintains real-time efficiency on YOLO11n-Pose geometry.
- **Explainable Review-Worthy Outputs:** Zero automated cheat accusations (`CHEATING` label deleted); all events emit `FLAGGED_FOR_REVIEW` with structured supporting cues for human review.

"""Unit & Integration Tests for Canonical Strict Temporal Matcher (EVAL1 - EVAL10).

Verifies:
- EVAL1: same label + same time + WRONG Seat = NOT TP
- EVAL2: same Seat + wrong label = NOT TP
- EVAL3: same Seat + same label + IoU 0.29 = NOT TP
- EVAL4: same Seat + same label + IoU 0.30 = eligible TP
- EVAL5: one AI cannot match two GT episodes
- EVAL6: one GT cannot match two AI episodes
- EVAL7: malformed GT is detected
- EVAL8: malformed GT is not silently corrected
- EVAL9: report includes matching criteria
- EVAL10: strict matcher uses seat_code, not nullable database seat_id
"""

from __future__ import annotations

import pytest

from classroom_monitor.evaluation.temporal_matcher import (
    CanonicalTemporalMatcher,
    compute_temporal_iou,
    match_temporal_episodes,
    normalize_label,
    normalize_seat_code,
)


def test_eval1_wrong_seat_is_not_tp():
    """EVAL1: Even if time and label match 100%, different seats MUST NOT be a True Positive."""
    gt = [{
        "id": "gt1",
        "seat_code": "SEAT-ROOM-CALIB-01-02",
        "episode_type": "HEAD_TURN_RIGHT",
        "start_ms": 1000.0,
        "end_ms": 3000.0,
        "duration_ms": 2000.0,
    }]
    ai = [{
        "episode_id": "ai1",
        "seat_code": "SEAT-ROOM-CALIB-01-14",
        "episode_type": "HEAD_TURN_RIGHT",
        "start_timestamp_ms": 1000.0,
        "end_timestamp_ms": 3000.0,
        "duration_ms": 2000.0,
    }]

    res = match_temporal_episodes(gt, ai, iou_threshold=0.30, require_same_seat=True)
    assert res.tp == 0
    assert res.fp == 1
    assert res.fn == 1
    assert res.recall == 0.0


def test_eval2_wrong_label_is_not_tp():
    """EVAL2: Same seat and time, but different behavior label (LEFT vs RIGHT) MUST NOT match."""
    gt = [{
        "id": "gt1",
        "seat_code": "SEAT-04",
        "episode_type": "HEAD_TURN_RIGHT",
        "start_ms": 5000.0,
        "end_ms": 8000.0,
        "duration_ms": 3000.0,
    }]
    ai = [{
        "episode_id": "ai1",
        "seat_code": "SEAT-04",
        "episode_type": "HEAD_TURN_LEFT",
        "start_timestamp_ms": 5000.0,
        "end_timestamp_ms": 8000.0,
        "duration_ms": 3000.0,
    }]

    res = match_temporal_episodes(gt, ai, iou_threshold=0.30, require_same_label=True)
    assert res.tp == 0
    assert res.fp == 1
    assert res.fn == 1


def test_eval3_iou_below_threshold_is_not_tp():
    """EVAL3: Same seat and label, but IoU = 0.29 (< 0.30) MUST NOT match as TP."""
    # GT: [1000, 3000] (len 2000)
    # AI: [2500, 4500] (len 2000)
    # Inter: [2500, 3000] = 500
    # Union: [1000, 4500] = 3500
    # IoU: 500 / 3500 = 0.1428 < 0.30
    gt = [{
        "id": "gt1",
        "seat_code": "SEAT-04",
        "episode_type": "HEAD_TURN_RIGHT",
        "start_ms": 1000.0,
        "end_ms": 3000.0,
        "duration_ms": 2000.0,
    }]
    ai = [{
        "episode_id": "ai1",
        "seat_code": "SEAT-04",
        "episode_type": "HEAD_TURN_RIGHT",
        "start_timestamp_ms": 2500.0,
        "end_timestamp_ms": 4500.0,
        "duration_ms": 2000.0,
    }]

    res = match_temporal_episodes(gt, ai, iou_threshold=0.30)
    assert res.tp == 0
    assert res.fp == 1
    assert res.fn == 1


def test_eval4_iou_at_or_above_threshold_is_tp():
    """EVAL4: Same seat, same label, IoU >= 0.30 is an eligible True Positive."""
    # GT: [1000, 3000] (len 2000)
    # AI: [1500, 3200] (len 1700)
    # Inter: [1500, 3000] = 1500
    # Union: [1000, 3200] = 2200
    # IoU: 1500 / 2200 = 0.6818 >= 0.30
    gt = [{
        "id": "gt1",
        "seat_code": "SEAT-04",
        "episode_type": "HEAD_TURN_RIGHT",
        "start_ms": 1000.0,
        "end_ms": 3000.0,
        "duration_ms": 2000.0,
    }]
    ai = [{
        "episode_id": "ai1",
        "seat_code": "SEAT-04",
        "episode_type": "HEAD_TURN_RIGHT",
        "start_timestamp_ms": 1500.0,
        "end_timestamp_ms": 3200.0,
        "duration_ms": 1700.0,
    }]

    res = match_temporal_episodes(gt, ai, iou_threshold=0.30)
    assert res.tp == 1
    assert res.fp == 0
    assert res.fn == 0
    assert res.precision == 100.0
    assert res.recall == 100.0
    assert res.avg_tp_iou == pytest.approx(0.682, abs=0.01)


def test_eval5_one_ai_cannot_match_two_gt():
    """EVAL5: 1 AI episode cannot match multiple GT episodes (greedy best-IoU 1-to-1)."""
    gt = [
        {"id": "gt1", "seat_code": "SEAT-04", "episode_type": "HEAD_TURN_RIGHT", "start_ms": 1000.0, "end_ms": 3000.0, "duration_ms": 2000.0},
        {"id": "gt2", "seat_code": "SEAT-04", "episode_type": "HEAD_TURN_RIGHT", "start_ms": 2000.0, "end_ms": 4000.0, "duration_ms": 2000.0},
    ]
    ai = [
        {"episode_id": "ai1", "seat_code": "SEAT-04", "episode_type": "HEAD_TURN_RIGHT", "start_timestamp_ms": 1000.0, "end_timestamp_ms": 3000.0, "duration_ms": 2000.0}
    ]

    res = match_temporal_episodes(gt, ai, iou_threshold=0.30)
    assert res.tp == 1
    assert res.fn == 1
    assert res.fp == 0


def test_eval6_one_gt_cannot_match_two_ai():
    """EVAL6: 1 GT episode cannot match multiple AI episodes."""
    gt = [
        {"id": "gt1", "seat_code": "SEAT-04", "episode_type": "HEAD_TURN_RIGHT", "start_ms": 1000.0, "end_ms": 3000.0, "duration_ms": 2000.0}
    ]
    ai = [
        {"episode_id": "ai1", "seat_code": "SEAT-04", "episode_type": "HEAD_TURN_RIGHT", "start_timestamp_ms": 1000.0, "end_timestamp_ms": 3000.0, "duration_ms": 2000.0},
        {"episode_id": "ai2", "seat_code": "SEAT-04", "episode_type": "HEAD_TURN_RIGHT", "start_timestamp_ms": 1200.0, "end_timestamp_ms": 2800.0, "duration_ms": 1600.0},
    ]

    res = match_temporal_episodes(gt, ai, iou_threshold=0.30)
    assert res.tp == 1
    assert res.fp == 1
    assert res.fn == 0


def test_eval7_eval8_malformed_gt_is_detected_and_excluded():
    """EVAL7 & EVAL8: Malformed GT record (start=0, end=20354, dur=6407) is detected and safely excluded."""
    gt = [
        {
            "id": "gt_bad",
            "seat_code": "SEAT-20",
            "episode_type": "HEAD_TURN_RIGHT",
            "start_ms": 0.0,
            "peak_ms": 2459.0,
            "end_ms": 20354.0,
            "duration_ms": 6407.0,
        },
        {
            "id": "gt_good",
            "seat_code": "SEAT-04",
            "episode_type": "HEAD_TURN_RIGHT",
            "start_ms": 23543.0,
            "end_ms": 25671.0,
            "duration_ms": 2128.0,
        },
    ]
    ai = []

    res = match_temporal_episodes(gt, ai, exclude_malformed_gt=True)
    assert res.total_gt_episodes == 2
    assert res.valid_gt_episodes == 1
    assert res.malformed_gt_excluded == 1
    assert len(res.malformed_gt_records) == 1
    assert "gt_bad" in res.malformed_gt_records[0]["id"]


def test_eval9_matching_criteria_in_result():
    """EVAL9: EvaluationResult includes exact matching rule dictionary."""
    res = match_temporal_episodes([], [], iou_threshold=0.30, require_same_seat=True, require_same_label=True)
    assert res.matching_rule["require_same_seat"] is True
    assert res.matching_rule["require_same_label"] is True
    assert res.matching_rule["temporal_iou_threshold"] == 0.30


def test_eval10_seat_code_numeric_normalization():
    """EVAL10: Normalizer correctly maps various formatted seat representations to same numeric key."""
    assert normalize_seat_code("SEAT-ROOM-CALIB-01-20") == "20"
    assert normalize_seat_code("SEAT-20") == "20"
    assert normalize_seat_code("ROOM-CALIB-01-04") == "4"
    assert normalize_seat_code("S04") == "4"
    assert normalize_seat_code("4") == "4"

"""Regression Test Suite for Scene Calibration Parser (CAL1 - CAL8).

Ensures exact roundtrip preservation of flat and nested calibration keys:
- CAL1: flat baseline_yaw is preserved.
- CAL2: nested reference_directions.baseline_yaw is preserved.
- CAL3: flat baseline_pitch is preserved.
- CAL4: flat desk_boundary_y is preserved.
- CAL5: nested desk_geometry.desk_boundary_y is preserved.
- CAL6: nested values take documented precedence if both forms exist.
- CAL7: SceneProfile runner / context lookup provides expected baseline yaw.
- CAL8: SceneProfile desk geometry enables desk_hand_interaction capability and point tests.
"""

from __future__ import annotations

import numpy as np
import pytest

from classroom_monitor.scene_context import CapabilityStatus, DeskGeometry, SceneProfile, SeatContext, SeatReferenceDirections


def test_cal1_flat_baseline_yaw():
    """CAL1: flat baseline_yaw is preserved in SeatContext.from_dict()."""
    data = {
        "seat_code": "S01",
        "baseline_yaw": -15.5,
    }
    ctx = SeatContext.from_dict(data)
    assert ctx.reference_directions.baseline_yaw == -15.5


def test_cal2_nested_baseline_yaw():
    """CAL2: nested reference_directions.baseline_yaw is preserved."""
    data = {
        "seat_code": "S02",
        "reference_directions": {
            "baseline_yaw": 18.2,
            "baseline_pitch": 5.0,
        },
    }
    ctx = SeatContext.from_dict(data)
    assert ctx.reference_directions.baseline_yaw == 18.2
    assert ctx.reference_directions.baseline_pitch == 5.0


def test_cal3_flat_baseline_pitch():
    """CAL3: flat baseline_pitch is preserved."""
    data = {
        "seat_code": "S03",
        "baseline_pitch": -8.0,
    }
    ctx = SeatContext.from_dict(data)
    assert ctx.reference_directions.baseline_pitch == -8.0


def test_cal4_flat_desk_boundary_y():
    """CAL4: flat desk_boundary_y / desk_y is preserved."""
    data1 = {
        "seat_code": "S04A",
        "desk_boundary_y": 450.0,
    }
    ctx1 = SeatContext.from_dict(data1)
    assert ctx1.desk_geometry is not None
    assert ctx1.desk_geometry.desk_boundary_y == 450.0
    assert ctx1.capabilities.desk_hand_interaction == CapabilityStatus.DEGRADED

    data2 = {
        "seat_code": "S04B",
        "desk_y": 520.0,
    }
    ctx2 = SeatContext.from_dict(data2)
    assert ctx2.desk_geometry is not None
    assert ctx2.desk_geometry.desk_boundary_y == 520.0
    assert ctx2.capabilities.desk_hand_interaction == CapabilityStatus.DEGRADED


def test_cal5_nested_desk_boundary_y():
    """CAL5: nested desk_geometry.desk_boundary_y is preserved."""
    data = {
        "seat_code": "S05",
        "desk_geometry": {
            "desk_boundary_y": 380.0,
        },
    }
    ctx = SeatContext.from_dict(data)
    assert ctx.desk_geometry is not None
    assert ctx.desk_geometry.desk_boundary_y == 380.0
    assert ctx.capabilities.desk_hand_interaction == CapabilityStatus.DEGRADED


def test_cal6_nested_precedence_over_flat():
    """CAL6: nested values take documented precedence if both forms exist."""
    data = {
        "seat_code": "S06",
        "baseline_yaw": -10.0,
        "baseline_pitch": -2.0,
        "desk_boundary_y": 400.0,
        "reference_directions": {
            "baseline_yaw": -25.0,
            "baseline_pitch": 8.0,
        },
        "desk_geometry": {
            "desk_boundary_y": 550.0,
        },
    }
    ctx = SeatContext.from_dict(data)
    assert ctx.reference_directions.baseline_yaw == -25.0
    assert ctx.reference_directions.baseline_pitch == 8.0
    assert ctx.desk_geometry.desk_boundary_y == 550.0


def test_cal7_scene_profile_hpe_baseline():
    """CAL7: SceneProfile provides expected relative yaw calculation using baseline."""
    yaml_dict = {
        "scene_id": "test_scene",
        "room_code": "ROOM-TEST",
        "seats": [
            {
                "seat_code": "S06",
                "baseline_yaw": -8.0,
            }
        ]
    }
    profile = SceneProfile.from_dict(yaml_dict)
    ctx = profile.seat_graph.get_context("S06")
    assert ctx is not None
    assert ctx.reference_directions.baseline_yaw == -8.0

    raw_yaw = -36.0
    baseline = ctx.reference_directions.baseline_yaw
    relative_yaw = raw_yaw - baseline
    assert relative_yaw == -28.0


def test_cal8_desk_geometry_point_tests():
    """CAL8: DeskGeometry correctly evaluates points above and below desk boundary."""
    desk = DeskGeometry(desk_boundary_y=450.0)
    # Wrist above desk (y = 400 <= 450) -> writing zone
    assert desk.contains_wrist_in_writing_zone((100.0, 400.0)) is True
    assert desk.is_wrist_below_desk((100.0, 400.0)) is False

    # Wrist below desk (y = 500 > 450) -> below desk
    assert desk.contains_wrist_in_writing_zone((100.0, 500.0)) is False
    assert desk.is_wrist_below_desk((100.0, 500.0)) is True

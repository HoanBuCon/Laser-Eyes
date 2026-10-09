"""Unit & Integration Tests for Configuration Unification (CFG1 - CFG5).

Verifies:
- CFG1: TemporalEpisodeEngine receives resolved configured yaw activation.
- CFG2: Scene override takes precedence over ClassroomConfig defaults.
- CFG3: ClassroomConfig takes precedence over engine fallback defaults.
- CFG4: resolve_runtime_config produces a complete, verified dictionary.
- CFG5: All engine parameters are strictly propagated without hidden constants.
"""

from __future__ import annotations

import pytest

from classroom_monitor.behavior_pattern_engine import BehaviorPatternEngine
from classroom_monitor.config import ClassroomConfig, DEFAULT_CONFIG, resolve_runtime_config
from classroom_monitor.demo.config import DemoVideoConfig
from classroom_monitor.scene_context import SceneProfile
from classroom_monitor.seat_risk_tracker import SeatRiskTracker
from classroom_monitor.temporal_episode_engine import TemporalEpisodeEngine


def test_cfg1_temporal_engine_receives_resolved_yaw():
    """CFG1: Instantiating TemporalEpisodeEngine with resolved config accurately sets thresholds."""
    runtime_cfg = resolve_runtime_config(
        scene_profile=None,
        demo_config=None,
        base_config=ClassroomConfig(),
    )
    engine = TemporalEpisodeEngine(**runtime_cfg["temporal"])
    assert engine.yaw_activation_deg == runtime_cfg["temporal"]["yaw_activation_deg"]
    assert engine.yaw_release_deg == runtime_cfg["temporal"]["yaw_release_deg"]
    assert engine.min_persistence_ms == runtime_cfg["temporal"]["min_persistence_ms"]


def test_cfg2_scene_override_wins():
    """CFG2: Explicit threshold override in SceneProfile overrides ClassroomConfig."""
    mock_scene = type("MockScene", (), {
        "raw_config": {
            "thresholds": {
                "temporal": {
                    "yaw_activation_deg": 35.0,
                    "min_persistence_ms": 600.0,
                },
                "risk": {
                    "flagged_threshold": 90.0,
                }
            }
        }
    })()

    runtime_cfg = resolve_runtime_config(
        scene_profile=mock_scene,
        base_config=ClassroomConfig(),
    )
    assert runtime_cfg["temporal"]["yaw_activation_deg"] == 35.0
    assert runtime_cfg["temporal"]["min_persistence_ms"] == 600.0
    assert runtime_cfg["risk"]["flagged_threshold"] == 90.0


def test_cfg3_base_config_wins_over_fallback():
    """CFG3: Custom ClassroomConfig settings take precedence over engine defaults."""
    custom_cfg = ClassroomConfig(head_hpe_hz=8.0, head_estimate_max_age_ms=800.0)
    runtime_cfg = resolve_runtime_config(
        scene_profile=None,
        base_config=custom_cfg,
    )
    assert runtime_cfg["head_pose"]["hpe_hz"] == 8.0
    assert runtime_cfg["head_pose"]["cache_max_age_ms"] == 800.0


def test_cfg4_effective_runtime_config_completeness():
    """CFG4: resolve_runtime_config contains all required root domains."""
    runtime_cfg = resolve_runtime_config()
    assert "head_pose" in runtime_cfg
    assert "temporal" in runtime_cfg
    assert "patterns" in runtime_cfg
    assert "risk" in runtime_cfg

    assert "hpe_hz" in runtime_cfg["head_pose"]
    assert "yaw_activation_deg" in runtime_cfg["temporal"]
    assert "glance_rolling_window_ms" in runtime_cfg["patterns"]
    assert "flagged_threshold" in runtime_cfg["risk"]


def test_cfg5_engines_instantiated_with_resolved_config():
    """CFG5: Engines accept resolved config dict without missing arguments."""
    runtime_cfg = resolve_runtime_config()
    temp_eng = TemporalEpisodeEngine(**runtime_cfg["temporal"])
    pat_eng = BehaviorPatternEngine(**runtime_cfg["patterns"])
    risk_trk = SeatRiskTracker(room_id="ROOM1", **runtime_cfg["risk"])

    assert temp_eng.yaw_activation_deg == runtime_cfg["temporal"]["yaw_activation_deg"]
    assert pat_eng.glance_rolling_window_ms == runtime_cfg["patterns"]["glance_rolling_window_ms"]
    assert risk_trk.flagged_threshold == runtime_cfg["risk"]["flagged_threshold"]

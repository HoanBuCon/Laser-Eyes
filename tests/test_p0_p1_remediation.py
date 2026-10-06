"""Regressions for the P0/P1 findings of docs/AGENT_ONBOARDING_AUDIT.md.

P0-1 single seat-calibration source     P0-2 no unscheduled head-model forwards
P0-3 occupancy baseline (desk mates)    P1-1 image-space yaw convention
P1-2 face-gated crops, sample smoothing, merged glance fragments
P1-3 renderer reflects core only        P1-4 per-incident label and evidence
P1-5 no body-box head crops             P1-7 seat occupant chosen by continuity
P1-8 writing hand suppresses below-desk
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from classroom_monitor.behavior_pattern_engine import BehaviorPattern, BehaviorPatternEngine, PatternType
from classroom_monitor.demo.config import DemoVideoConfig, get_demo_config
from classroom_monitor.demo.renderer import DemoHUDOverlayRenderer
from classroom_monitor.demo.seating import CalibrationMismatchError, build_scene_seating, validate_seating
from classroom_monitor.head_pose_provider import HeadCropExtractor, HeadOrientationEstimate
from classroom_monitor.models import ClassroomEvent, Detection, SeverityLevel
from classroom_monitor.observation_extractor import ObservationExtractor, ObservationType, RawObservation
from classroom_monitor.scene_context import DeskGeometry, SeatContext, SeatGraph, SeatNeighbors
from classroom_monitor.seat_manager import SeatDefinition, SeatManager, SeatOccupancy, SeatState
from classroom_monitor.seat_risk_tracker import RiskState, SeatRiskTracker
from classroom_monitor.temporal_episode_engine import EpisodeState, EpisodeType, TemporalEpisode, TemporalEpisodeEngine


def _square(x: float, y: float, size: float = 100.0) -> np.ndarray:
    return np.array([[x, y], [x + size, y], [x + size, y + size], [x, y + size]], dtype=np.float32)


def _person(x1: int, y1: int, x2: int, y2: int, conf: float = 0.9) -> Detection:
    return Detection(class_id=0, class_name="person", confidence=conf, bbox=(x1, y1, x2, y2), keypoints=np.zeros((17, 3)))


# --- P0-1 -------------------------------------------------------------------

@pytest.mark.parametrize("preset", ["student", "india"])
def test_runs_use_the_seats_saved_from_the_web_calibration(preset, calibrated_demo_rooms):
    seating = build_scene_seating(get_demo_config(preset))
    assert seating.source == "database"
    assert all(seating.seat_graph.get_context(code) is not None for code in seating.seat_codes)
    # Desk line, capacity and camera placement travel with the polygon of the same seat.
    first = seating.seat_defs[0]
    ctx = seating.seat_graph.get_context(first.seat_code)
    assert first.desk_y == ctx.desk_geometry.desk_boundary_y
    assert seating.seat_graph.camera_view == "facing_subjects"


def test_presets_carry_no_seat_layout_of_their_own():
    from classroom_monitor.demo.config import DEMO_PRESETS

    assert all("seats_preset" not in preset for preset in DEMO_PRESETS.values())


def test_starting_layout_template_converts_to_seat_records():
    from classroom_monitor.demo.seating import load_scene_template

    template = load_scene_template(Path("configs/scenes/student_classroom.yaml"))
    assert template["camera_view"] == "facing_subjects"
    seat = template["seats"][0]
    assert len(seat["polygon_json"]) >= 3
    assert seat["context_json"]["capacity"] == 1
    assert seat["context_json"]["camera_view"] == "facing_subjects"


def test_polygons_without_matching_context_are_rejected():
    seat = SeatDefinition(seat_id="DB-01", room_id="R", seat_code="DB-01", polygon=_square(0, 0))
    graph = SeatGraph(room_id="R")
    graph.add_seat_context(SeatContext(seat_id="YAML-01"))
    with pytest.raises(CalibrationMismatchError):
        validate_seating([seat], graph, "mixed")


def test_preset_source_builds_consistent_graph():
    config = DemoVideoConfig(
        name="p",
        video_path=Path("unused.mp4"),
        room_code="R",
        seats_preset=[
            {"seat_code": "A", "polygon_json": _square(0, 0).tolist(), "desk_y": 60.0, "baseline_yaw": 7.0},
            {"seat_code": "B", "polygon_json": _square(150, 0).tolist()},
        ],
    )
    seating = build_scene_seating(config)
    ctx = seating.seat_graph.get_context("A")
    assert ctx.reference_directions.baseline_yaw == 7.0
    assert ctx.desk_geometry.desk_boundary_y == 60.0


def test_missing_calibration_refuses_to_run_and_points_to_the_web_page():
    config = DemoVideoConfig(name="none", video_path=Path("unused.mp4"), room_code="ROOM-NEVER-CALIBRATED")
    with pytest.raises(CalibrationMismatchError, match="/calibration"):
        build_scene_seating(config)


def test_capacity_and_camera_view_come_from_the_seat_context():
    config = DemoVideoConfig(
        name="p",
        video_path=Path("unused.mp4"),
        room_code="R",
        seats_preset=[
            {"seat_code": "A", "polygon_json": _square(0, 0).tolist(), "capacity": 2, "camera_view": "behind_subjects"},
            {"seat_code": "B", "polygon_json": _square(150, 0).tolist(), "camera_view": "behind_subjects"},
        ],
    )
    seating = build_scene_seating(config)
    assert seating.seat_graph.camera_view == "behind_subjects"
    assert seating.seat_defs[0].metadata["capacity"] == 2


# --- P0-2 -------------------------------------------------------------------

class _CountingProvider:
    def __init__(self):
        self.calls = 0

    def estimate(self, **kwargs):
        self.calls += 1
        return HeadOrientationEstimate(yaw=40.0, pitch=0.0, quality=0.9, source="test")


def test_pipeline_extraction_never_runs_head_model_outside_schedule():
    provider = _CountingProvider()
    extractor = ObservationExtractor(head_pose_provider=provider)
    det = _person(10, 10, 90, 190)
    det.keypoints[:, 2] = 0.9
    obs = extractor.extract(
        detection=det,
        seat_context=SeatContext(seat_id="S1"),
        timestamp_ms=100.0,
        precomputed_head_estimate=None,
        allow_head_inference=False,
    )
    assert provider.calls == 0
    assert not any(o.observation_type == ObservationType.HEAD_YAW_RELATIVE.value for o in obs)


# --- P0-3 / P1-7 ------------------------------------------------------------

def _two_person_seat_manager() -> SeatManager:
    mgr = SeatManager(room_id="R")
    mgr.load_seats([SeatDefinition(seat_id="S1", room_id="R", seat_code="S1", polygon=_square(0, 0, 200))])
    return mgr


def test_desk_mate_present_from_start_is_not_an_additional_person():
    mgr = _two_person_seat_manager()
    mates = [_person(10, 10, 90, 190), _person(110, 10, 190, 190)]
    for ts in range(0, 5000, 100):
        mgr.map_detections_to_seats(mates, float(ts))
    occ = mgr.occupancies["S1"]
    assert occ.state == SeatState.MULTIPLE_PERSON
    assert occ.expected_person_count == 2


def test_person_arriving_beyond_baseline_is_additional():
    mgr = _two_person_seat_manager()
    student = [_person(60, 10, 140, 190)]
    for ts in range(0, 3000, 100):
        mgr.map_detections_to_seats(student, float(ts))
    visitor = _person(150, 0, 199, 199, conf=0.99)
    mgr.map_detections_to_seats(student + [visitor], 3000.0)
    occ = mgr.occupancies["S1"]
    assert occ.person_count == 2
    assert occ.expected_person_count == 1
    # The seat keeps its own occupant, not the more confident visitor.
    assert occ.assigned_detection is student[0]


def test_episode_engine_compares_count_with_expected_occupancy():
    engine = TemporalEpisodeEngine(min_persistence_ms=100.0)

    def count_obs(ts, count, expected):
        return [RawObservation("S1", ts, ObservationType.PERSON_COUNT_NEAR_SEAT.value, count,
                               metadata={"expected_person_count": expected})]

    eps = []
    for ts in (0.0, 200.0, 400.0):
        eps = engine.process_observations(count_obs(ts, 2, 2), ts)
    assert not any(ep.episode_type == EpisodeType.MULTI_PERSON_NEAR_SEAT.value for ep in eps)
    for ts in (600.0, 800.0, 1000.0):
        eps = engine.process_observations(count_obs(ts, 3, 2), ts)
    assert any(ep.episode_type == EpisodeType.MULTI_PERSON_NEAR_SEAT.value for ep in eps)


# --- P1-2 / P1-5 ------------------------------------------------------------

def test_back_of_head_and_body_box_produce_no_head_crop():
    frame = np.zeros((300, 300, 3), dtype=np.uint8)
    kps = np.zeros((17, 3), dtype=np.float32)
    kps[3] = [100.0, 100.0, 0.9]  # ears only: back of the head
    kps[4] = [140.0, 100.0, 0.9]
    crop, quality = HeadCropExtractor().extract_crop(frame, kps, bbox=(80, 80, 200, 290))
    assert crop is None and quality == 0.0
    crop, quality = HeadCropExtractor().extract_crop(frame, None, bbox=(80, 80, 200, 290))
    assert crop is None and quality == 0.0


def _yaw(ts: float, value: float, sample_ts: float) -> RawObservation:
    return RawObservation("S1", ts, ObservationType.HEAD_YAW_RELATIVE.value, value,
                          quality=0.9, metadata={"sample_ts": sample_ts})


def test_single_head_model_spike_repeated_over_frames_does_not_open_episode():
    engine = TemporalEpisodeEngine(min_persistence_ms=400.0)
    eps = []
    ts = 0.0
    # 5 Hz head samples, each cached for 6 frames at 30 FPS; one spiky sample.
    for sample_idx, value in enumerate([0.0, 0.0, 60.0, 0.0, 0.0, 0.0]):
        sample_ts = sample_idx * 200.0
        for _ in range(6):
            eps = engine.process_observations([_yaw(ts, value, sample_ts)], ts)
            assert not eps
            ts += 33.3


def _turn(ep_id: str, start: float, end: float) -> TemporalEpisode:
    return TemporalEpisode(ep_id, "S1", EpisodeType.HEAD_TURN_LEFT.value, EpisodeState.ENDED,
                           start_timestamp_ms=start, end_timestamp_ms=end, duration_ms=end - start)


def test_fragments_of_one_head_turn_are_one_glance():
    ctx = SeatContext(seat_id="S1", neighbors=SeatNeighbors(left_neighbor_id="S2"))
    engine = BehaviorPatternEngine(min_glance_episodes=2)
    fragments = [_turn("a", 0.0, 900.0), _turn("b", 1100.0, 2000.0)]  # 200 ms noise gap
    assert engine.ingest_episodes([], fragments, ctx, 2100.0) == []
    distinct = [_turn("a", 0.0, 900.0), _turn("c", 3000.0, 3900.0)]
    engine2 = BehaviorPatternEngine(min_glance_episodes=2)
    pats = engine2.ingest_episodes([], distinct, ctx, 4000.0)
    assert [p.pattern_type for p in pats] == [PatternType.REPEATED_NEIGHBOR_GLANCE.value]


# --- P1-8 -------------------------------------------------------------------

def test_one_hand_writing_other_hand_low_is_not_below_desk():
    engine = TemporalEpisodeEngine(min_persistence_ms=100.0)
    eps = []
    for ts in (0.0, 200.0, 400.0, 600.0):
        eps = engine.process_observations([
            RawObservation("S1", ts, ObservationType.LEFT_WRIST_ZONE.value, "UNDER_DESK", quality=0.9),
            RawObservation("S1", ts, ObservationType.RIGHT_WRIST_ZONE.value, "WRITING", quality=0.9),
        ], ts)
    assert not any(ep.episode_type == EpisodeType.WRIST_BELOW_DESK.value for ep in eps)


# --- P1-4 -------------------------------------------------------------------

def _pattern(pid: str, ptype: str, ts: float) -> BehaviorPattern:
    return BehaviorPattern(pattern_id=pid, seat_id="S1", pattern_type=ptype,
                           start_timestamp_ms=ts - 1000.0, end_timestamp_ms=ts)


def test_second_incident_is_labelled_by_its_own_patterns_and_frame():
    tracker = SeatRiskTracker(room_id="R", decay_rate_per_sec=0.0, cooldown_duration_ms=1000.0,
                              incident_merge_window_ms=0.0)
    frame_a = np.full((4, 4, 3), 1, dtype=np.uint8)
    frame_b = np.full((4, 4, 3), 2, dtype=np.uint8)
    multi = PatternType.MULTI_PERSON_DWELL_NEAR_SEAT.value
    first = tracker.update_seat("S1", [], [_pattern(f"m{i}", multi, 1000.0) for i in range(3)],
                                1000.0, frame_image=frame_a)
    assert first is not None and first.behavior == multi
    tracker.update_seat("S1", [], [], 2500.0, frame_image=frame_a)  # cooldown over
    # Only a glance happens now; the earlier, stronger MULTI pattern must not name it.
    second = tracker.update_seat(
        "S1", [], [_pattern("g0", PatternType.REPEATED_NEIGHBOR_GLANCE.value, 3000.0)], 3000.0, frame_image=frame_b
    )
    assert second is not None
    assert second.behavior == PatternType.REPEATED_NEIGHBOR_GLANCE.value
    assert int(second.evidence_frame[0, 0, 0]) == 2


# --- P1-3 -------------------------------------------------------------------

@pytest.fixture
def render_setup():
    seat = SeatDefinition(seat_id="SEAT-01", room_id="R", seat_code="SEAT-01", polygon=_square(50, 50))
    mgr = SeatManager(room_id="R")
    mgr.load_seats([seat])
    mgr.occupancies["SEAT-01"] = SeatOccupancy(seat=seat, state=SeatState.OCCUPIED)
    tracker = SeatRiskTracker(room_id="R", cooldown_duration_ms=5000.0)
    return mgr, tracker, DemoHUDOverlayRenderer(debug_overlay=False), np.zeros((300, 300, 3), dtype=np.uint8)


def _render(setup, ts, events):
    mgr, tracker, renderer, frame = setup
    out = renderer.render_frame(frame=frame, frame_idx=1, timestamp_ms=ts, fps=30.0, room_code="R",
                                camera_id="C", seat_mgr=mgr, seat_graph=None, risk_tracker=tracker,
                                active_episodes=[], recent_events=events)
    return np.any(np.all(out == [30, 30, 235], axis=-1))


def test_review_card_ends_with_core_cooldown(render_setup):
    evt = ClassroomEvent(event_id="e", track_id=0, behavior="REPEATED_NEIGHBOR_GLANCE",
                         severity=SeverityLevel.HIGH.value, timestamp_ms=10000.0, seat_id="SEAT-01",
                         metadata={"risk_score": 84.0})
    assert _render(render_setup, 11000.0, [evt])
    assert not _render(render_setup, 15500.0, [evt])  # core cooldown (5 s) over


def test_high_score_during_cooldown_without_incident_is_not_review(render_setup):
    _mgr, tracker, _r, _f = render_setup
    prof = tracker.get_or_create_profile("SEAT-01")
    prof.current_state = RiskState.COOLDOWN.value
    prof.risk_score = 90.0
    assert not _render(render_setup, 20000.0, [])


def test_review_card_shows_trigger_score_not_post_reset_score():
    evt = ClassroomEvent(event_id="e", track_id=0, behavior="X", severity="HIGH",
                         metadata={"trigger_risk_score": 83.0, "risk_score": 83.0})
    assert DemoHUDOverlayRenderer._trigger_score(evt, 45.0) == 83.0


# --- P1-1: subject-centric directions ----------------------------------------

def test_neighbours_are_the_candidates_own_sides_when_camera_faces_them():
    seats = [
        SeatDefinition(seat_id="IMG_LEFT", room_id="R", seat_code="IMG_LEFT", polygon=_square(0, 0)),
        SeatDefinition(seat_id="MID", room_id="R", seat_code="MID", polygon=_square(150, 0)),
        SeatDefinition(seat_id="IMG_RIGHT", room_id="R", seat_code="IMG_RIGHT", polygon=_square(300, 0)),
    ]
    facing = SeatGraph(room_id="R")  # default: camera facing the candidates
    facing.auto_infer_neighbors_from_polygons(seats)
    mid = facing.get_context("MID")
    assert mid.neighbors.left_neighbor_id == "IMG_RIGHT"
    assert mid.neighbors.right_neighbor_id == "IMG_LEFT"

    behind = SeatGraph(room_id="R", camera_view="behind_subjects")
    behind.auto_infer_neighbors_from_polygons(seats)
    assert behind.get_context("MID").neighbors.left_neighbor_id == "IMG_LEFT"


def test_pose_heuristic_uses_the_same_subject_centric_sign_as_6drepnet():
    from classroom_monitor.head_pose_provider import PoseHeuristicHeadOrientationProvider

    kps = np.zeros((17, 3), dtype=np.float32)
    kps[0] = [140.0, 120.0, 0.9]  # nose toward image-left
    kps[3] = [160.0, 120.0, 0.9]
    kps[4] = [190.0, 120.0, 0.9]
    est = PoseHeuristicHeadOrientationProvider().estimate(keypoints=kps)
    assert est.yaw > 0.0  # candidate facing the camera turned to their RIGHT


# --- P1-2: per-seat neutral yaw ----------------------------------------------

def test_adaptive_baseline_removes_seat_perspective_offset():
    from classroom_monitor.head_pose_provider import AdaptiveYawBaseline

    baseline = AdaptiveYawBaseline(window_ms=60000.0, min_samples=5)
    est = HeadOrientationEstimate(yaw=-50.0, pitch=10.0, quality=0.9, source="sixdrepnet")
    outputs = [baseline.apply("S1", est, ts * 200.0) for ts in range(5)]
    assert all(o.yaw is None for o in outputs[:4])  # warm-up: UNKNOWN, never a guess
    assert outputs[4].yaw == pytest.approx(0.0)     # sitting still at -50 deg is neutral
    turned = baseline.apply("S1", HeadOrientationEstimate(yaw=-10.0, pitch=10.0, quality=0.9), 1200.0)
    assert turned.yaw == pytest.approx(40.0)          # a real turn is relative to that neutral


# --- Display-only behaviour labels --------------------------------------------

def _episode(seat: str, ep_type: str) -> TemporalEpisode:
    return TemporalEpisode(f"{seat}-{ep_type}", seat, ep_type, EpisodeState.ACTIVE, start_timestamp_ms=0.0)


def test_behavior_tags_show_screen_direction_for_subject_centric_turns():
    renderer = DemoHUDOverlayRenderer(behavior_labels=True)
    det = _person(10, 10, 90, 190)
    eps = [_episode("S1", EpisodeType.HEAD_TURN_LEFT.value), _episode("S1", EpisodeType.TORSO_LEAN_RIGHT.value)]
    facing = [t for t, _ in renderer._behavior_tags("S1", eps, [], det, mirrors_image=True, timestamp_ms=0.0)]
    # Camera facing the student: their left is the right side of the screen.
    assert "Head turn ->" in facing and "Lean <-" in facing
    behind = [t for t, _ in renderer._behavior_tags("S1", eps, [], det, mirrors_image=False, timestamp_ms=0.0)]
    assert "Head turn <-" in behind and "Lean ->" in behind


def test_hand_movement_tag_needs_sustained_motion():
    renderer = DemoHUDOverlayRenderer(behavior_labels=True)
    det = _person(0, 0, 100, 100)  # 100 px tall: speed 80 px/s = 0.8 heights/s

    def tags(ts, speed_px):
        obs = [RawObservation("S1", ts, "WRIST_VELOCITY", speed_px)]
        return [t for t, _ in renderer._behavior_tags("S1", [], obs, det, True, ts)]

    assert "Hand movement" not in tags(0.0, 80.0)      # a single fast sample is not enough
    assert "Hand movement" in tags(400.0, 80.0)        # sustained >= 300 ms
    assert "Hand movement" in tags(600.0, 10.0)        # stays on briefly while slowing down
    assert "Hand movement" not in tags(1200.0, 10.0)   # off after >= 500 ms of calm


def test_behavior_labels_do_not_change_review_state(render_setup):
    mgr, tracker, _r, frame = render_setup
    labelled = DemoHUDOverlayRenderer(behavior_labels=True)
    mgr.occupancies["SEAT-01"].assigned_detection = _person(60, 60, 140, 140)
    eps = [_episode("SEAT-01", EpisodeType.HEAD_TURN_LEFT.value)]
    out = labelled.render_frame(frame=frame, frame_idx=1, timestamp_ms=1000.0, fps=30.0, room_code="R",
                                camera_id="C", seat_mgr=mgr, seat_graph=None, risk_tracker=tracker,
                                active_episodes=eps, recent_events=[])
    assert not np.any(np.all(out == [30, 30, 235], axis=-1))   # no review card
    assert not np.any(np.all(out == [0, 165, 255], axis=-1))   # no suspicious border
    assert tracker.get_or_create_profile("SEAT-01").risk_score == 0.0


def test_behavior_labels_toggle_through_the_web_api():
    from fastapi.testclient import TestClient

    from api.main import app

    with TestClient(app) as client:
        assert client.post("/api/v1/demo/overlay", json={"behavior_labels": True}).json()["behavior_labels"] is True
        assert client.get("/api/v1/demo/status").json()["behavior_labels"] is True
        assert client.post("/api/v1/demo/overlay", json={"behavior_labels": False}).json()["behavior_labels"] is False

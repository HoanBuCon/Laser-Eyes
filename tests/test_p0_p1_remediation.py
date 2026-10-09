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

@pytest.mark.parametrize("preset", ["china1", "china2", "china3"])
def test_runs_use_the_seats_saved_from_the_web_calibration(preset, calibrated_demo_rooms):
    seating = build_scene_seating(get_demo_config(preset))
    assert seating.source == "database"
    assert all(seating.seat_graph.get_context(code) is not None for code in seating.seat_codes)
    # Desk line, capacity and camera placement travel with the polygon of the same seat.
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
    # A visitor who stays at the desk counts once they have been there a few seconds
    for ts in range(3000, 9000, 100):
        mgr.map_detections_to_seats(student + [visitor], float(ts))
    occ = mgr.occupancies["S1"]
    assert occ.person_count == 2
    assert occ.expected_person_count == 1
    # The seat keeps its own occupant, not the more confident visitor.
    assert occ.assigned_detection is student[0]


def _posed(bbox, shoulders, conf: float = 0.9) -> Detection:
    """Person box with both shoulders at the given (left, right) points."""
    kps = np.zeros((17, 3), dtype=np.float32)
    kps[5] = [*shoulders[0], 0.9]
    kps[6] = [*shoulders[1], 0.9]
    return Detection(class_id=0, class_name="person", confidence=conf, bbox=bbox, keypoints=kps)


def test_two_boxes_for_one_student_count_as_one_person():
    mgr = _two_person_seat_manager()
    shoulders = ((60, 60), (140, 60))
    upper_body = _posed((50, 20, 150, 150), shoulders, conf=0.8)
    # The model also returns a tall box for the same student, down to the floor
    tall = _posed((45, 20, 155, 400), ((62, 61), (138, 62)), conf=0.6)
    mgr.map_detections_to_seats([upper_body, tall], 0.0)
    occ = mgr.occupancies["S1"]
    assert occ.person_count == 1
    assert occ.assigned_detection is upper_body


def test_far_seat_drawn_around_the_head_still_finds_its_student():
    mgr = SeatManager(room_id="R")
    # A back-row ROI covering only the head and shoulders
    mgr.load_seats([SeatDefinition(seat_id="FAR", room_id="R", seat_code="FAR", polygon=_square(100, 0, 60))])
    student = _posed((110, 10, 150, 140), ((115, 55), (145, 55)))
    student.keypoints[0] = [130, 30, 0.9]   # nose
    student.keypoints[1] = [126, 26, 0.9]   # eyes
    student.keypoints[2] = [134, 26, 0.9]
    # The torso point (below the shoulders) lies under the ROI
    assert SeatManager._anchor(student)[1] > 60
    mapped, unmapped = mgr.map_detections_to_seats([student], 0.0)
    assert mapped["FAR"] is student
    assert unmapped == []

    # A standing teacher whose head passes through the same ROI is not its student
    mgr2 = SeatManager(room_id="R")
    mgr2.load_seats([SeatDefinition(seat_id="FAR", room_id="R", seat_code="FAR", polygon=_square(100, 0, 60))])
    teacher = _posed((100, 10, 160, 400), ((112, 70), (148, 70)))
    teacher.keypoints[0] = [130, 30, 0.9]
    teacher.keypoints[1] = [126, 26, 0.9]
    mapped, unmapped = mgr2.map_detections_to_seats([teacher], 0.0)
    assert mapped["FAR"] is None
    assert unmapped == [teacher]


def test_box_stretching_over_the_next_row_stays_in_its_own_seat():
    mgr = SeatManager(room_id="R")
    mgr.load_seats([
        SeatDefinition(seat_id="BACK", room_id="R", seat_code="BACK", polygon=_square(0, 0, 200)),
        SeatDefinition(seat_id="FRONT", room_id="R", seat_code="FRONT", polygon=_square(0, 200, 200)),
    ])
    # The back-row student's box runs down over the front student's legs;
    # its bottom lies in FRONT, the torso in BACK.
    back = _posed((40, 20, 160, 390), ((60, 60), (140, 60)))
    front = _posed((40, 220, 160, 399), ((60, 260), (140, 260)))
    mapped, _ = mgr.map_detections_to_seats([back, front], 0.0)
    assert mapped["BACK"] is back
    assert mapped["FRONT"] is front
    assert mgr.occupancies["FRONT"].person_count == 1


def test_teacher_walking_past_is_not_an_extra_person():
    mgr = SeatManager(room_id="R")
    mgr.load_seats([SeatDefinition(seat_id="S1", room_id="R", seat_code="S1", polygon=_square(0, 0, 400))])
    student = _posed((40, 40, 140, 200), ((60, 80), (120, 80)))
    for ts in range(0, 5000, 100):
        mgr.map_detections_to_seats([student], float(ts))
    # The teacher walks along the row through the ROI
    counts = []
    for step, ts in enumerate(range(5000, 7000, 100)):
        x = 160 + step * 10
        teacher = _posed((x, 20, x + 80, 380), ((x + 10, 60), (x + 70, 60)))
        mapped, unmapped = mgr.map_detections_to_seats([student, teacher], float(ts))
        counts.append(mgr.occupancies["S1"].person_count)
        assert mapped["S1"] is student
    assert set(counts) == {1}
    assert unmapped and unmapped[0] is teacher


def test_lone_student_with_flickering_detection_keeps_the_seat():
    mgr = SeatManager(room_id="R")
    mgr.load_seats([SeatDefinition(seat_id="S1", room_id="R", seat_code="S1", polygon=_square(0, 0, 200))])
    student = _posed((40, 40, 140, 190), ((60, 80), (120, 80)))
    for ts in range(0, 10000, 100):
        # detected in only one frame of four
        seen = (ts // 100) % 4 == 0
        mapped, _ = mgr.map_detections_to_seats([student] if seen else [], float(ts))
    mapped, _ = mgr.map_detections_to_seats([student], 10000.0)
    assert mapped["S1"] is student


def test_distinct_neighbours_are_not_merged():
    shoulders_a = ((20, 60), (90, 60))
    shoulders_b = ((110, 60), (180, 60))
    people = [_posed((10, 20, 100, 190), shoulders_a), _posed((100, 20, 190, 190), shoulders_b)]
    assert SeatManager.merge_duplicate_detections(people) == people


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


# --- Sustained look toward a neighbour --------------------------------------

def _active(ep_id: str, ep_type: str, start: float, now: float) -> TemporalEpisode:
    return TemporalEpisode(ep_id, "S1", ep_type, EpisodeState.ACTIVE, start_timestamp_ms=start,
                           duration_ms=now - start, confidence=0.8, quality=0.8)


def _attention_patterns(engine, active, ctx, ts):
    return [p for p in engine.ingest_episodes(active, [], ctx, ts)
            if p.pattern_type == PatternType.SUSTAINED_NEIGHBOR_ATTENTION.value]


def test_long_head_turn_alone_is_not_sustained_attention():
    # Head-pose yaw alone is unreliable (a student writing with the head down)
    ctx = SeatContext(seat_id="S1", neighbors=SeatNeighbors(left_neighbor_id="S2"))
    engine = BehaviorPatternEngine()
    turn = _active("t", EpisodeType.HEAD_TURN_LEFT.value, 0.0, 4000.0)
    assert _attention_patterns(engine, [turn], ctx, 4000.0) == []


def test_long_turn_with_head_moved_toward_neighbour_is_sustained_attention():
    ctx = SeatContext(seat_id="S1", neighbors=SeatNeighbors(left_neighbor_id="S2"))
    engine = BehaviorPatternEngine()
    turn = _active("t", EpisodeType.HEAD_TURN_LEFT.value, 0.0, 2500.0)
    shift = _active("o", EpisodeType.HEAD_OFFSET_LEFT.value, 300.0, 2500.0)
    pats = _attention_patterns(engine, [turn, shift], ctx, 2500.0)
    assert len(pats) == 1
    assert pats[0].target_neighbor_id == "S2"
    # The other way there is no neighbour to look at
    engine_r = BehaviorPatternEngine()
    turn_r = _active("tr", EpisodeType.HEAD_TURN_RIGHT.value, 0.0, 2500.0)
    shift_r = _active("or", EpisodeType.HEAD_OFFSET_RIGHT.value, 0.0, 2500.0)
    assert _attention_patterns(engine_r, [turn_r, shift_r], ctx, 2500.0) == []


def test_a_longer_look_counts_again():
    ctx = SeatContext(seat_id="S1", neighbors=SeatNeighbors(left_neighbor_id="S2"))
    engine = BehaviorPatternEngine(attention_repeat_ms=3000.0)
    counted = []
    for now in range(2000, 9100, 100):
        turn = _active("t", EpisodeType.HEAD_TURN_LEFT.value, 0.0, float(now))
        shift = _active("o", EpisodeType.HEAD_OFFSET_LEFT.value, 0.0, float(now))
        if _attention_patterns(engine, [turn, shift], ctx, float(now)):
            counted.append(now)
    assert counted == [2000, 5000, 8000]


def test_head_offset_needs_the_face_and_a_seat_without_visitors():
    extractor = ObservationExtractor()
    ctx = SeatContext(seat_id="S1")
    kps = np.zeros((17, 3), dtype=np.float32)
    kps[0] = [130, 50, 0.9]   # nose, right of the shoulder centre
    kps[1] = [125, 45, 0.9]
    kps[5] = [80, 90, 0.9]
    kps[6] = [160, 90, 0.9]
    det = Detection(class_id=0, class_name="person", confidence=0.9, bbox=(60, 20, 180, 200), keypoints=kps)

    def offsets(**kwargs):
        return [o.value for o in extractor.extract(det, ctx, 0.0, **kwargs)
                if o.observation_type == ObservationType.HEAD_OFFSET_X.value]

    assert offsets(nearby_person_count=1, expected_person_count=1) == [pytest.approx(10 / 80)]
    assert offsets(nearby_person_count=2, expected_person_count=1) == []
    kps[0][2] = 0.1  # face turned away / hidden
    assert offsets(nearby_person_count=1, expected_person_count=1) == []


def test_raw_head_offset_does_not_open_episodes_until_related_to_the_student():
    engine = TemporalEpisodeEngine(min_persistence_ms=100.0)
    raw = RawObservation("S1", 0.0, ObservationType.HEAD_OFFSET_X.value, -0.6)
    eps = []
    for ts in range(0, 1000, 100):
        raw.timestamp_ms = float(ts)
        eps = engine.process_observations([raw], float(ts))
    assert not eps
    related = RawObservation("S1", 0.0, ObservationType.HEAD_OFFSET_X.value, -0.6,
                             metadata={"relative_to": "student_baseline"})
    for ts in range(1000, 2000, 100):
        related.timestamp_ms = float(ts)
        eps = engine.process_observations([related], float(ts))
    assert [e.episode_type for e in eps] == [EpisodeType.HEAD_OFFSET_LEFT.value]


def test_head_offset_baseline_is_the_students_usual_posture():
    from classroom_monitor.head_pose_provider import HeadOffsetBaseline

    baseline = HeadOffsetBaseline(window_ms=30000.0, min_samples=5)
    for ts in range(4):
        assert baseline.apply("S1", 0.3, float(ts)) is None
    assert baseline.apply("S1", 0.3, 4.0) == pytest.approx(0.0)
    assert baseline.apply("S1", 0.8, 5.0) == pytest.approx(0.5)


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


def test_incident_records_when_the_behaviour_began():
    tracker = SeatRiskTracker(room_id="R", decay_rate_per_sec=0.0)
    multi = PatternType.MULTI_PERSON_DWELL_NEAR_SEAT.value
    # The behaviour began at 4 s; the score reaches the review threshold at 7 s
    early = BehaviorPattern(pattern_id="a", seat_id="S1", pattern_type=multi,
                            start_timestamp_ms=4000.0, end_timestamp_ms=5000.0)
    assert tracker.update_seat("S1", [], [early], 5000.0) is None
    incident = tracker.update_seat("S1", [], [_pattern(f"m{i}", multi, 7000.0) for i in range(2)], 7000.0)
    assert incident is not None
    assert incident.metadata["first_seen_ms"] == 7000.0
    assert incident.metadata["behavior_start_ms"] == 4000.0


def test_next_incident_does_not_start_before_the_previous_one():
    tracker = SeatRiskTracker(room_id="R", decay_rate_per_sec=0.0, cooldown_duration_ms=1000.0,
                              incident_merge_window_ms=0.0)
    multi = PatternType.MULTI_PERSON_DWELL_NEAR_SEAT.value
    glance = PatternType.REPEATED_NEIGHBOR_GLANCE.value
    first = tracker.update_seat("S1", [], [_pattern(f"m{i}", multi, 5000.0) for i in range(3)], 5000.0)
    assert first is not None
    tracker.update_seat("S1", [], [], 6500.0)  # cooldown over
    # A glance pattern looks back over its whole window, to before the first incident
    long_glance = BehaviorPattern(pattern_id="g", seat_id="S1", pattern_type=glance,
                                  start_timestamp_ms=1000.0, end_timestamp_ms=8000.0)
    second = tracker.update_seat("S1", [], [long_glance, _pattern("g2", glance, 8000.0)], 8000.0)
    assert second is not None and second.behavior == glance
    assert second.metadata["behavior_start_ms"] == 5000.0


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
        # Seats side by side in one row (adjacent ROIs)
        SeatDefinition(seat_id="IMG_LEFT", room_id="R", seat_code="IMG_LEFT", polygon=_square(0, 0)),
        SeatDefinition(seat_id="MID", room_id="R", seat_code="MID", polygon=_square(100, 0)),
        SeatDefinition(seat_id="IMG_RIGHT", room_id="R", seat_code="IMG_RIGHT", polygon=_square(200, 0)),
    ]
    facing = SeatGraph(room_id="R")  # default: camera facing the candidates
    facing.auto_infer_neighbors_from_polygons(seats)
    mid = facing.get_context("MID")
    assert mid.neighbors.left_neighbor_id == "IMG_RIGHT"
    assert mid.neighbors.right_neighbor_id == "IMG_LEFT"

    behind = SeatGraph(room_id="R", camera_view="behind_subjects")
    behind.auto_infer_neighbors_from_polygons(seats)
    assert behind.get_context("MID").neighbors.left_neighbor_id == "IMG_LEFT"


def test_only_desk_mates_in_the_same_row_are_left_right_neighbours():
    seats = [
        # front row: a desk of two, then an aisle, then the next desk
        SeatDefinition(seat_id="A", room_id="R", seat_code="A", polygon=_square(0, 200)),
        SeatDefinition(seat_id="B", room_id="R", seat_code="B", polygon=_square(100, 200)),
        SeatDefinition(seat_id="ACROSS_AISLE", room_id="R", seat_code="ACROSS_AISLE", polygon=_square(260, 200)),
        # the row behind, drawn above and slightly offset (perspective)
        SeatDefinition(seat_id="BEHIND", room_id="R", seat_code="BEHIND", polygon=_square(40, 100)),
        SeatDefinition(seat_id="BEHIND2", room_id="R", seat_code="BEHIND2", polygon=_square(140, 100)),
    ]
    graph = SeatGraph(room_id="R", camera_view="behind_subjects")
    graph.auto_infer_neighbors_from_polygons(seats)

    def sides(code):
        n = graph.get_context(code).neighbors
        return n.left_neighbor_id, n.right_neighbor_id

    assert sides("A") == (None, "B")
    assert sides("B") == ("A", None)          # not the seat across the aisle
    assert sides("ACROSS_AISLE") == (None, None)
    assert sides("BEHIND") == (None, "BEHIND2")  # not linked to the row in front


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


def test_debug_overlay_toggles_while_running_without_touching_labels():
    from fastapi.testclient import TestClient

    from api.main import app

    with TestClient(app) as client:
        client.post("/api/v1/demo/overlay", json={"behavior_labels": True})
        status = client.post("/api/v1/demo/overlay", json={"debug_overlay": True}).json()
        assert status["debug_overlay"] is True
        assert status["behavior_labels"] is True
        assert client.get("/api/v1/demo/status").json()["debug_overlay"] is True
        status = client.post("/api/v1/demo/overlay", json={"debug_overlay": False}).json()
        assert status["debug_overlay"] is False
        client.post("/api/v1/demo/overlay", json={"behavior_labels": False})


# --- Reviewer highlight of the flagged student --------------------------------

def test_incident_focus_carries_seat_and_person_location():
    from classroom_monitor.pipeline.srs_v2_pipeline import incident_focus

    focus = incident_focus(
        seat_polygon=_square(100, 200),
        person_bbox=(110, 150, 190, 330),
        current_bbox=None,
        frame_shape=(1080, 1920, 3),
    )
    assert focus["frame_size"] == [1920, 1080]
    assert focus["person_bbox"] == [110.0, 150.0, 190.0, 330.0]
    assert len(focus["seat_polygon"]) == 4
    # an empty peak box falls back to the person currently in the seat
    fallback = incident_focus(seat_polygon=_square(0, 0), person_bbox=(0, 0, 0, 0),
                              current_bbox=(5, 5, 50, 90), frame_shape=(720, 1280, 3))
    assert fallback["person_bbox"] == [5.0, 5.0, 50.0, 90.0]


# --- Desk mates' heads together -------------------------------------------

def _student(nose_x: float, shoulder_x: float) -> Detection:
    kps = np.zeros((17, 3), dtype=np.float32)
    kps[0] = [nose_x, 40, 0.9]
    kps[5] = [shoulder_x - 30, 80, 0.9]
    kps[6] = [shoulder_x + 30, 80, 0.9]
    return Detection(class_id=0, class_name="person", confidence=0.9,
                     bbox=(int(shoulder_x - 50), 0, int(shoulder_x + 50), 200), keypoints=kps)


def _desk_mates_graph() -> SeatGraph:
    seats = [
        SeatDefinition(seat_id="L", room_id="R", seat_code="L", polygon=_square(0, 0)),
        SeatDefinition(seat_id="M", room_id="R", seat_code="M", polygon=_square(100, 0)),
    ]
    graph = SeatGraph(room_id="R")
    graph.auto_infer_neighbors_from_polygons(seats)
    return graph


def _run_heads(monitor, frames):
    """frames: list of (ts, left nose x, right nose x, people at right seat)."""
    found = []
    for ts, left_nose, right_nose, right_count in frames:
        people = {"L": (_student(left_nose, 50), 1, 1), "M": (_student(right_nose, 150), right_count, 1)}
        found += monitor.update(people, float(ts))
    return found


def test_desk_mates_bringing_heads_together_are_both_flagged():
    from classroom_monitor.pair_proximity import HeadsTogetherMonitor

    monitor = HeadsTogetherMonitor(_desk_mates_graph(), min_samples=10)
    apart = [(ts, 50, 150, 1) for ts in range(0, 10000, 100)]       # 100 px = 1.7 shoulder widths
    together = [(ts, 90, 115, 1) for ts in range(10000, 13000, 100)]  # heads over one paper
    found = _run_heads(monitor, apart + together)
    assert {p.seat_id for p in found} == {"L", "M"}
    assert all(p.pattern_type == PatternType.HEADS_TOGETHER.value for p in found)


def test_pair_that_always_looks_close_is_not_flagged():
    from classroom_monitor.pair_proximity import HeadsTogetherMonitor

    monitor = HeadsTogetherMonitor(_desk_mates_graph(), min_samples=10)
    # The same short gap the whole time (e.g. perspective), never "apart"
    assert _run_heads(monitor, [(ts, 90, 115, 1) for ts in range(0, 13000, 100)]) == []


def test_heads_together_ignores_a_seat_with_someone_else_at_it():
    from classroom_monitor.pair_proximity import HeadsTogetherMonitor

    monitor = HeadsTogetherMonitor(_desk_mates_graph(), min_samples=10)
    apart = [(ts, 50, 150, 1) for ts in range(0, 10000, 100)]
    # A teacher leaning over the right seat
    crowded = [(ts, 90, 115, 2) for ts in range(10000, 13000, 100)]
    assert _run_heads(monitor, apart + crowded) == []

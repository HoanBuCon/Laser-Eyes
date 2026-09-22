"""Competition-critical remediation regression tests.

Tests using controlled doubles are explicitly marked ``integration`` and do
not claim to validate real-model accuracy.
"""

from __future__ import annotations

import asyncio
import builtins
import json
import re
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import cv2
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app
from api.realtime import RealtimeManager
from classroom_monitor.behavior_pattern_engine import BehaviorPatternEngine, PatternType
from classroom_monitor.demo.config import DemoVideoConfig
from classroom_monitor.demo.runtime import DemoRuntime
from classroom_monitor.detector import ModelUnavailableError, PoseClassroomDetector
from classroom_monitor.models import ClassroomEvent, Detection
from classroom_monitor.observation_extractor import ObservationType, RawObservation
from classroom_monitor.scene_context import SeatContext, SeatGraph, SeatNeighbors
from classroom_monitor.seat_risk_tracker import SeatRiskTracker
from classroom_monitor.temporal_episode_engine import EpisodeState, EpisodeType, TemporalEpisode, TemporalEpisodeEngine
from classroom_monitor.video_buffer import EvidenceVideoBuffer
from storage.database import Base
from storage.database import SessionLocal
from storage.db_models import AuditLog, DetectionEvent, EventReview, ExamRoom, ExamSession, ExamSite
from storage.review_service import ReviewCommand, ReviewTargetMissing, submit_event_review
from exam_monitor.app import wait_for_worker_shutdown
from exam_monitor.engine import GazeAnalyzer
from exam_monitor.events import EventDetector, EventRule
from exam_monitor.models import AnalysisResult, EventType, Severity
from api.routes.demo import _find_evidence_file


pytestmark = pytest.mark.unit


def _episode(ep_id: str, start: float, *, active: bool = False) -> TemporalEpisode:
    return TemporalEpisode(
        episode_id=ep_id,
        seat_id="S1",
        episode_type=EpisodeType.HEAD_TURN_LEFT.value,
        state=EpisodeState.ACTIVE if active else EpisodeState.ENDED,
        start_timestamp_ms=start,
        end_timestamp_ms=None if active else start + 500.0,
        duration_ms=500.0,
    )


def test_pattern_component_identity_survives_time_cooldown():
    ctx = SeatContext(seat_id="S1", neighbors=SeatNeighbors(left_neighbor_id="S2"))
    graph = SeatGraph(room_id="R1")
    graph.seats_context["S1"] = ctx
    engine = BehaviorPatternEngine(seat_graph=graph, min_glance_episodes=2)
    ep1, ep2 = _episode("ep-1", 0.0), _episode("ep-2", 1000.0, active=True)

    first = engine.ingest_episodes([ep2], [ep1], ctx, 2000.0)
    repeated = engine.ingest_episodes([ep2], [ep1], ctx, 13000.0)

    assert len([p for p in first if p.pattern_type == PatternType.REPEATED_NEIGHBOR_GLANCE.value]) == 1
    assert repeated == []


def _occupancy(seat: str, ts: float, state: str) -> RawObservation:
    return RawObservation(seat, ts, ObservationType.SEAT_OCCUPANCY.value, state)


def test_initially_empty_seat_never_becomes_seat_left():
    engine = TemporalEpisodeEngine(min_persistence_ms=100.0)
    for ts in range(0, 60_001, 1000):
        episodes = engine.process_observations([_occupancy("S1", float(ts), "EMPTY")], float(ts))
    assert not any(ep.episode_type == EpisodeType.SEAT_EMPTY.value for ep in episodes)


def test_occupied_then_empty_produces_one_seat_left_pattern():
    temporal = TemporalEpisodeEngine(min_persistence_ms=100.0)
    ctx = SeatContext(seat_id="S1")
    patterns = BehaviorPatternEngine(seat_left_timeout_ms=1000.0)
    temporal.process_observations([_occupancy("S1", 0.0, "OCCUPIED")], 0.0)
    emitted = []
    for ts in (100.0, 500.0, 1200.0, 1600.0):
        eps = temporal.process_observations([_occupancy("S1", ts, "EMPTY")], ts)
        emitted.extend(patterns.ingest_episodes(eps, temporal.completed_episodes, ctx, ts))
    assert len([p for p in emitted if p.pattern_type == PatternType.SEAT_LEFT.value]) == 1


def _recidivism_score(step_ms: float) -> float:
    tracker = SeatRiskTracker(decay_rate_per_sec=0.0)
    profile = tracker.get_or_create_profile("S1")
    profile.total_event_count = 1
    profile.last_event_timestamp_ms = 0.0
    profile.risk_score = 65.0
    ts = 1000.0
    while ts <= 2000.0:
        tracker.update_seat("S1", [], [], ts)
        ts += step_ms
    return profile.risk_score


def test_recidivism_bonus_is_update_rate_independent():
    assert _recidivism_score(100.0) == pytest.approx(_recidivism_score(1000.0))
    assert _recidivism_score(100.0) == pytest.approx(75.0)


def test_video_buffer_reset_finishes_pending_clip_without_deadlock(tmp_path: Path):
    buffer = EvidenceVideoBuffer(
        pre_event_seconds=0.5,
        post_event_seconds=0.5,
        fps=2.0,
        output_dir=tmp_path,
        async_write=False,
    )
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    buffer.add_frame(frame, 1, 0.0)
    job = buffer.trigger_clip("evt-reset", 0, "HEAD_TURN", 1, 0.0)

    worker = threading.Thread(target=buffer.reset)
    worker.start()
    worker.join(timeout=3.0)

    assert not worker.is_alive()
    assert job.status == "READY"
    assert job.output_path.stat().st_size > 0


def test_pose_model_missing_fails_closed_and_explicit_mock_is_visible(monkeypatch):
    original_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "ultralytics":
            raise ImportError("test: unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    real_required = PoseClassroomDetector(model_path="missing-test-model.pt")
    with pytest.raises(ModelUnavailableError):
        real_required.detect(np.zeros((32, 32, 3), dtype=np.uint8))
    assert real_required.inference_mode == "ERROR"

    explicit_mock = PoseClassroomDetector(model_path="missing-test-model.pt", allow_mock=True)
    assert explicit_mock.inference_mode == "MOCK"
    assert explicit_mock.capability_health["pose"] == "MOCK"
    assert explicit_mock.detect(np.zeros((32, 32, 3), dtype=np.uint8))


def test_realtime_bridge_uses_captured_server_loop():
    class FakeWebSocket:
        def __init__(self):
            self.messages = []

        async def send_json(self, message):
            self.messages.append(message)

    async def scenario():
        manager = RealtimeManager()
        ws = FakeWebSocket()
        manager.active_connections.add(ws)
        loop = asyncio.get_running_loop()
        manager.set_event_loop(loop)
        future_holder = []

        def publish():
            future_holder.append(manager.broadcast_threadsafe({"type": "REVIEW_INCIDENT", "event": {"event_id": "E1"}}))

        thread = threading.Thread(target=publish)
        thread.start()
        thread.join()
        await asyncio.wrap_future(future_holder[0])
        assert [m["event"]["event_id"] for m in ws.messages] == ["E1"]

    asyncio.run(scenario())


def _memory_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_review_service_requires_durable_event_and_writes_audit():
    db = _memory_session()
    with pytest.raises(ReviewTargetMissing):
        submit_event_review(db, ReviewCommand("missing", "judge", "REJECTED"))

    site = ExamSite(name="Test")
    db.add(site)
    db.flush()
    room = ExamRoom(site_id=site.id, name="Room", room_code="R1")
    db.add(room)
    db.flush()
    session = ExamSession(id="session-1", room_id=room.id, exam_name="Exam", status="RUNNING")
    db.add(session)
    event = DetectionEvent(
        id="pk-1",
        event_id="incident-1",
        session_id=session.id,
        room_id=room.id,
        behavior="HEAD_TURN",
        status="FLAGGED_FOR_HUMAN_REVIEW",
        review_status="PENDING",
    )
    db.add(event)
    db.commit()

    review = submit_event_review(
        db,
        ReviewCommand("incident-1", "judge", "INCONCLUSIVE", "LOW_QUALITY", "Needs review"),
    )
    db.refresh(event)
    assert review.decision == "INCONCLUSIVE"
    assert event.status == "FLAGGED_FOR_HUMAN_REVIEW"
    assert event.review_status == "INCONCLUSIVE"
    assert db.query(EventReview).count() == 1
    assert db.query(AuditLog).count() == 1


@pytest.mark.integration
def test_favicon_endpoint_is_quiet():
    with TestClient(app) as client:
        response = client.get("/favicon.ico")
    assert response.status_code == 204


def test_cli_and_web_adapters_share_canonical_srs_v2_pipeline():
    import classroom_monitor.demo.runner as cli_adapter
    import classroom_monitor.demo.runtime as web_adapter

    assert cli_adapter.SRSv2Pipeline is web_adapter.SRSv2Pipeline


def test_local_calibration_accepts_neutral_and_rejects_off_center(tmp_path: Path):
    analyzer = GazeAnalyzer(
        model_path=tmp_path / "missing-face.task",
        object_model_path=tmp_path / "missing-object.tflite",
    )
    analyzer.begin_calibration()
    for _ in range(36):
        analyzer._maybe_collect_calibration(0.02, -0.01, 0.0, 0.0)
    assert analyzer.calibrated
    assert analyzer.calibration_state == "READY"
    assert analyzer.calibration_quality > 0.8

    analyzer.reset_calibration()
    for _ in range(36):
        analyzer._maybe_collect_calibration(0.55, 0.0, 0.0, 0.0)
    assert not analyzer.calibrated
    assert analyzer.calibration_state == "REJECTED"


def test_local_allowed_material_policy_separates_detection_from_prohibition(tmp_path: Path):
    analyzer = GazeAnalyzer(
        model_path=tmp_path / "missing-face.task",
        object_model_path=tmp_path / "missing-object.tflite",
    )
    detections = [("book", 0.9, (0, 0, 10, 10)), ("cell phone", 0.8, (0, 0, 10, 10))]
    assert analyzer.prohibited_objects(detections) == ["book", "cell phone"]
    analyzer.set_allowed_materials({"book"})
    assert analyzer.prohibited_objects(detections) == ["cell phone"]
    assert [item[0] for item in detections] == ["book", "cell phone"]


def test_tracking_held_head_and_gaze_cannot_activate_new_local_events():
    rules = {
        EventType.LOOK_AWAY: EventRule(EventType.LOOK_AWAY, 0.2, 0.0, Severity.MEDIUM),
        EventType.HEAD_TURN: EventRule(EventType.HEAD_TURN, 0.2, 0.0, Severity.MEDIUM),
        EventType.TALKING: EventRule(EventType.TALKING, 1.0, 0.0, Severity.MEDIUM),
        EventType.NO_FACE: EventRule(EventType.NO_FACE, 1.0, 0.0, Severity.MEDIUM),
        EventType.MULTIPLE_FACES: EventRule(EventType.MULTIPLE_FACES, 1.0, 0.0, Severity.HIGH),
        EventType.SUSPICIOUS_OBJECT: EventRule(EventType.SUSPICIOUS_OBJECT, 1.0, 0.0, Severity.HIGH),
        EventType.LOW_LIGHT: EventRule(EventType.LOW_LIGHT, 1.0, 0.0, Severity.LOW),
    }
    detector = EventDetector(rules)
    held = AnalysisResult(
        timestamp=0.0,
        face_count=1,
        person_count=1,
        head_direction="right",
        eyes_outside_zone=True,
        tracking_held=True,
        brightness=100.0,
    )
    assert detector.update(held, "S", now=0.0) == []
    assert detector.update(held, "S", now=2.0) == []


def test_local_worker_shutdown_is_cooperative_and_non_lossy():
    stop = threading.Event()
    finished = threading.Event()

    def worker():
        while not stop.is_set():
            time.sleep(0.005)
        # Represents final in-flight event/session bookkeeping.
        time.sleep(0.02)
        finished.set()

    thread = threading.Thread(target=worker, daemon=False)
    thread.start()
    assert wait_for_worker_shutdown(thread, stop, timeout=1.0)
    assert finished.is_set()


def _write_video(path: Path, *, frames: int = 6, fps: float = 10.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (96, 64))
    assert writer.isOpened()
    for index in range(frames):
        frame = np.full((64, 96, 3), index * 10, dtype=np.uint8)
        writer.write(frame)
    writer.release()
    assert path.stat().st_size > 0


@pytest.mark.integration
def test_live_worker_crosses_first_incident_and_persists_evidence(tmp_path: Path, monkeypatch):
    """Pipeline-boundary E2E with controlled perception; not a real-model accuracy test."""
    import classroom_monitor.demo.runtime as runtime_module

    video = tmp_path / "live.mp4"
    _write_video(video)
    config = DemoVideoConfig(
        name="live_boundary",
        video_path=video,
        output_dir=tmp_path / "output",
        room_code=f"ROOM-{uuid.uuid4().hex[:8]}",
        camera_id=f"CAM-{uuid.uuid4().hex[:8]}",
        head_provider="pose_heuristic",
        save_evidence=True,
        max_frames=6,
        allow_mock=True,
    )

    detection = Detection(0, "person", 0.9, (10, 5, 70, 60), keypoints=np.zeros((17, 3)))

    class FakeDetector:
        inference_mode = "MOCK"
        model_path = Path("controlled-test-double")
        capability_health = {"pose": "MOCK", "pose_model": "controlled-test-double", "pose_error": ""}

        def __init__(self, *args, **kwargs):
            pass

        def ensure_available(self):
            return None

        def detect(self, frame, frame_index=0):
            return [detection]

    occupancy = SimpleNamespace(
        state="OCCUPIED",
        assigned_detection=detection,
        candidate_detections=[detection],
    )

    class FakeSeatManager:
        def __init__(self, *args, **kwargs):
            self.occupancies = {"S1": occupancy}
            self.seats = {"S1": object()}

        def load_seats(self, _seats):
            return None

        def map_detections_to_seats(self, **kwargs):
            return self.occupancies, []

    class FakeProvider:
        def estimate_batch(self, **kwargs):
            return {}

    class FakeObservationExtractor:
        def __init__(self, *args, **kwargs):
            pass

        def extract(self, *, timestamp_ms, **kwargs):
            return [_occupancy("S1", timestamp_ms, "OCCUPIED")]

    class FakeEpisodes:
        def __init__(self, *args, **kwargs):
            self.completed_episodes = []

        def process_observations(self, *args, **kwargs):
            return []

        def flush_all(self, *args, **kwargs):
            return []

    class FakePatterns:
        def __init__(self, *args, **kwargs):
            pass

        def ingest_episodes(self, *args, **kwargs):
            return []

    class FakeRisk:
        def __init__(self, *args, **kwargs):
            self.sent = False
            self.profiles = {
                "S1": SimpleNamespace(
                    risk_score=85.0,
                    current_state="FLAGGED_FOR_REVIEW",
                    active_incident=None,
                )
            }

        def update_seat(self, timestamp_ms, frame_image, **kwargs):
            if self.sent:
                return None
            self.sent = True
            event_id = f"live-{uuid.uuid4()}"
            return ClassroomEvent(
                event_id=event_id,
                track_id=0,
                seat_id="S1",
                behavior="REPEATED_NEIGHBOR_GLANCE",
                severity="HIGH",
                timestamp_ms=timestamp_ms,
                evidence_frame=frame_image.copy(),
                metadata={
                    "peak_risk_score": 85.0,
                    "risk_score": 85.0,
                    "primary_pattern": "REPEATED_NEIGHBOR_GLANCE",
                    "occurrence_count": 1,
                },
            )

    class FakeRenderer:
        def __init__(self, *args, **kwargs):
            pass

        def render_frame(self, frame, **kwargs):
            return frame.copy()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runtime_module, "get_demo_config", lambda _preset: config)
    monkeypatch.setattr(runtime_module, "PoseClassroomDetector", FakeDetector)
    monkeypatch.setattr(runtime_module, "SeatManager", FakeSeatManager)
    monkeypatch.setattr(runtime_module, "create_head_pose_provider", lambda _name: FakeProvider())
    monkeypatch.setattr(runtime_module, "ObservationExtractor", FakeObservationExtractor)
    monkeypatch.setattr(runtime_module, "TemporalEpisodeEngine", FakeEpisodes)
    monkeypatch.setattr(runtime_module, "BehaviorPatternEngine", FakePatterns)
    monkeypatch.setattr(runtime_module, "SeatRiskTracker", FakeRisk)
    monkeypatch.setattr(runtime_module, "DemoHUDOverlayRenderer", FakeRenderer)
    monkeypatch.setattr(runtime_module, "export_demo_artifacts", lambda **kwargs: {})

    runtime = DemoRuntime()
    result = runtime.start("live_boundary", mode="LIVE", max_frames=6, allow_mock=True)
    runtime._thread.join(timeout=15.0)

    assert not runtime._thread.is_alive()
    assert runtime.get_status()["state"] == "COMPLETED"
    assert runtime.get_status()["inference_mode"] == "MOCK"
    events = runtime.get_events()
    assert len(events) == 1
    assert events[0]["evidence_status"] == "READY"
    assert events[0]["snapshot_url"]
    assert events[0]["video_url"]
    db = SessionLocal()
    try:
        persisted = db.query(DetectionEvent).filter(DetectionEvent.event_id == events[0]["event_id"]).one()
        assert persisted.evidence.status == "READY"
        assert persisted.evidence.file_size_bytes > 0
    finally:
        db.close()


@pytest.mark.integration
def test_replay_crosses_incident_evidence_hash_and_human_review(tmp_path: Path, monkeypatch):
    import classroom_monitor.demo.runtime as runtime_module

    monkeypatch.chdir(tmp_path)
    preset = "replay_boundary"
    replay_root = tmp_path / "data" / "demo_final" / preset
    result_video = replay_root / "result.mp4"
    _write_video(result_video, frames=6, fps=10.0)
    evidence_dir = replay_root / "evidence"
    snapshot = evidence_dir / "incident_snapshot.jpg"
    evidence_video = evidence_dir / "incident_clip.mp4"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    assert cv2.imwrite(str(snapshot), np.zeros((64, 96, 3), dtype=np.uint8))
    _write_video(evidence_video, frames=2, fps=10.0)
    event_id = f"replay-{uuid.uuid4()}"
    (replay_root / "events.json").write_text(
        json.dumps(
            [
                {
                    "event_id": event_id,
                    "seat_code": "S1",
                    "title": "REPEATED_NEIGHBOR_GLANCE",
                    "severity": "HIGH",
                    "timestamp_ms": 100.0,
                    "peak_risk_score": 88.0,
                    "evidence_snapshot_path": str(snapshot),
                    "evidence_clip_path": str(evidence_video),
                    "metadata": {"occurrence_count": 1},
                }
            ]
        ),
        encoding="utf-8",
    )
    config = DemoVideoConfig(
        name=preset,
        video_path=result_video,
        output_dir=replay_root,
        room_code=f"ROOM-{uuid.uuid4().hex[:8]}",
        camera_id=f"CAM-{uuid.uuid4().hex[:8]}",
    )
    monkeypatch.setattr(runtime_module, "get_demo_config", lambda _preset: config)
    runtime = DemoRuntime()
    DemoRuntime._instance = runtime
    runtime.start(preset, mode="REPLAY", max_frames=6)
    runtime._thread.join(timeout=15.0)

    assert runtime.get_status()["state"] == "COMPLETED"
    assert [event["event_id"] for event in runtime.get_events()] == [event_id]
    with TestClient(app) as client:
        snapshot_response = client.get(runtime.get_event(event_id)["snapshot_url"])
        video_response = client.get(runtime.get_event(event_id)["video_url"])
        integrity = client.get(f"/api/v1/demo/events/{event_id}/integrity")
        review = client.post(
            f"/api/v1/demo/events/{event_id}/review",
            json={"decision": "INCONCLUSIVE", "reviewer_id": "judge", "reason_code": "LOW_QUALITY", "notes": "manual"},
        )

    assert snapshot_response.status_code == 200
    assert video_response.status_code == 200
    assert integrity.json()["hash_status"] == "HASH_VERIFIED"
    assert review.status_code == 200
    db = SessionLocal()
    try:
        persisted = db.query(DetectionEvent).filter(DetectionEvent.event_id == event_id).one()
        assert persisted.review_status == "INCONCLUSIVE"
        assert persisted.status == "FLAGGED_FOR_HUMAN_REVIEW"
        assert persisted.review is not None
    finally:
        db.close()


def test_competition_profile_disables_legacy_inference(monkeypatch):
    monkeypatch.delenv("VIGIL_ENABLE_LEGACY_INFERENCE", raising=False)
    with TestClient(app) as client:
        response = client.get("/api/v1/inference/status/legacy-session")
    assert response.status_code == 410
    assert "canonical" in response.json()["detail"].lower()


def test_reference_upload_rejects_unsafe_or_unsupported_file(monkeypatch, tmp_path: Path):
    import api.routes.cameras as cameras_module

    monkeypatch.setattr(cameras_module, "REF_FRAME_DIR", tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/cameras/reference-frame/upload",
            files={"file": ("../../payload.exe", b"not-media", "application/octet-stream")},
        )
    assert response.status_code == 415
    assert list(tmp_path.iterdir()) == []


def test_student_calibration_preset_returns_real_video_frame():
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/cameras/calibration-presets/student/reference-frame"
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["x-vigil-reference-source"] == "preset:student"
    assert response.content.startswith(b"\xff\xd8")


def test_evidence_lookup_rejects_traversal_and_ambiguous_basename(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    first = tmp_path / "data" / "demo_runs" / "one" / "duplicate.mp4"
    second = tmp_path / "data" / "demo_final" / "two" / "duplicate.mp4"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    assert _find_evidence_file("../duplicate.mp4") is None
    assert _find_evidence_file("duplicate.mp4") is None


def test_event_scoped_evidence_url_survives_duplicate_basenames(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "data" / "demo_final" / "student" / "evidence" / "same.mp4"
    duplicate = tmp_path / "data" / "demo_runs" / "old" / "evidence" / "same.mp4"
    target.parent.mkdir(parents=True)
    duplicate.parent.mkdir(parents=True)
    target.write_bytes(b"canonical")
    duplicate.write_bytes(b"old")
    snapshot = target.with_suffix(".jpg")
    snapshot.write_bytes(b"snapshot")

    runtime = DemoRuntime()
    db = SessionLocal()
    try:
        site = ExamSite(name=f"Scoped {uuid.uuid4()}")
        db.add(site)
        db.flush()
        room = ExamRoom(site_id=site.id, name="Scoped Room", room_code=f"R-{uuid.uuid4().hex[:8]}")
        db.add(room)
        db.flush()
        session = ExamSession(id=str(uuid.uuid4()), room_id=room.id, exam_name="Scoped evidence", status="RUNNING")
        db.add(session)
        db.commit()
        runtime.status.session_id = session.id
    finally:
        db.close()
    event = ClassroomEvent(
        event_id=f"scoped-{uuid.uuid4()}",
        track_id=1,
        seat_id="S1",
        behavior="REVIEW_SIGNAL",
        severity="MEDIUM",
        timestamp_ms=1.0,
        evidence_path=str(snapshot),
        evidence_video_path=str(target),
        metadata={"peak_risk_score": 80.0},
    )
    payload = runtime._persist_event_to_db(event)
    DemoRuntime._instance = runtime

    assert payload["video_url"].endswith(f"/{event.event_id}/evidence/video")
    with TestClient(app) as client:
        response = client.get(payload["video_url"])
    assert response.status_code == 200
    assert response.content == b"canonical"


def test_dashboard_review_cards_do_not_embed_inline_event_handlers():
    source = Path("dashboard/js/demo.js").read_text(encoding="utf-8")
    assert "onclick=\"openReviewModal" not in source
    assert "function escapeHtml" in source
    assert "function apiFetch" in source
    assert "X-Vigil-Demo-Token" in source


def test_classroom_demo_uses_local_gaze_shell_and_stable_state_hooks():
    html = Path("dashboard/demo.html").read_text(encoding="utf-8")
    operations_html = Path("dashboard/index.html").read_text(encoding="utf-8")
    calibration_html = Path("dashboard/calibration.html").read_text(encoding="utf-8")
    workbench_html = Path("dashboard/data_workbench.html").read_text(encoding="utf-8")
    css = Path("dashboard/css/demo.css").read_text(encoding="utf-8")
    tokens = Path("dashboard/css/vigil-tokens.css").read_text(encoding="utf-8")
    shell = Path("dashboard/css/vigil-shell.css").read_text(encoding="utf-8")
    javascript = Path("dashboard/js/demo.js").read_text(encoding="utf-8")
    operations_javascript = Path("dashboard/js/app.js").read_text(encoding="utf-8")

    assert 'href="/static/css/vigil-tokens.css"' in html
    assert 'href="/static/css/vigil-shell.css"' in html
    assert 'href="/static/css/vigil-shell.css"' in operations_html
    assert 'href="/static/css/vigil-shell.css"' in calibration_html
    assert 'href="/static/css/vigil-shell.css"' in workbench_html
    assert 'class="vigil-sidebar"' in html
    assert 'class="vigil-sidebar"' in operations_html
    assert 'class="vigil-sidebar"' in calibration_html
    assert 'class="vigil-sidebar"' in workbench_html
    assert 'id="reviewQueue"' in html
    assert "--vigil-sidebar:" in tokens
    assert ".vigil-sidebar" in shell
    assert ".vigil-sidebar" not in css
    assert "badge.dataset.connected" in javascript
    assert "stateBadge.dataset.state" in javascript
    assert "stateBadge.className" not in javascript
    assert "btnIndia.className" not in javascript
    assert "badge.dataset.connected" in operations_javascript
    assert "btnSnap.className" not in operations_javascript
    assert "Exam Cheating Surveillance" not in operations_html

    html_ids = set(re.findall(r'id="([^"]+)"', html))
    javascript_ids = set(re.findall(r"getElementById\('([^']+)'\)", javascript))
    assert javascript_ids <= html_ids

    operations_ids = set(re.findall(r'id="([^"]+)"', operations_html))
    operations_javascript_ids = set(
        re.findall(r"getElementById\('([^']+)'\)", operations_javascript)
    )
    assert operations_javascript_ids <= operations_ids


def test_configured_demo_token_protects_api(monkeypatch):
    monkeypatch.setenv("VIGIL_DEMO_TOKEN", "competition-secret")
    with TestClient(app) as client:
        denied = client.get("/api/v1/demo/status")
        allowed = client.get(
            "/api/v1/demo/status",
            headers={"X-Vigil-Demo-Token": "competition-secret"},
        )
    assert denied.status_code == 401
    assert allowed.status_code == 200

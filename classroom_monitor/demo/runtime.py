"""Unified SRS v2 Demo Runtime & Execution Engine.

Single Source of Truth for:
- Live SRS v2 Computer Vision Analysis (YOLO-Pose + 6DRepNet + Temporal Episodes + Incident Tracker)
- Recorded Analysis Replay (Synchronized event playback from precomputed artifacts)
- Web MJPEG Video Stream
- WebSocket Telemetry & Incident Broadcasting
- SQLite Database Persistence & Review Queue Management
- CLI Execution
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import cv2
import numpy as np

from classroom_monitor.async_evidence_writer import AsyncEvidenceWriter, compute_file_sha256
from classroom_monitor.behavior_pattern_engine import BehaviorPattern, BehaviorPatternEngine, PatternType
from classroom_monitor.config import ClassroomConfig, DEFAULT_CONFIG, resolve_runtime_config
from classroom_monitor.contracts import (
    CapabilityMode,
    EvidenceStatus,
    HashStatus,
    classroom_event_from_replay,
    evidence_basename,
    review_incident_from_classroom_event,
)
from classroom_monitor.demo.config import (
    DEMO_PRESETS,
    DemoVideoConfig,
    INDIA_CALIBRATED_SEATS,
    get_demo_config,
    resolve_video_path,
)
from classroom_monitor.demo.exporter import export_demo_artifacts
from classroom_monitor.demo.renderer import DemoHUDOverlayRenderer
from classroom_monitor.detector import PoseClassroomDetector
from classroom_monitor.head_pose_provider import (
    HeadOrientationEstimate,
    create_head_pose_provider,
)
from classroom_monitor.models import ClassroomEvent, Detection, EventStatus, SeverityLevel
from classroom_monitor.observation_extractor import ObservationExtractor, RawObservation
from classroom_monitor.scene_context import CapabilityStatus, DeskGeometry, SceneProfile, SeatContext, SeatGraph
from classroom_monitor.seat_manager import SeatDefinition, SeatManager, SeatState
from classroom_monitor.seat_risk_tracker import RiskState, SeatRiskTracker
from classroom_monitor.temporal_episode_engine import EpisodeState, EpisodeType, TemporalEpisode, TemporalEpisodeEngine
from classroom_monitor.video_buffer import EvidenceVideoBuffer
from storage.database import SessionLocal, init_db
from storage.db_models import Camera, DetectionEvent, EvidenceFile, ExamRoom, ExamSession, ExamSite, SeatROI

logger = logging.getLogger("VigilDemoRuntime")


class DemoMode(str, Enum):
    LIVE = "LIVE"
    REPLAY = "REPLAY"


class DemoState(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


@dataclass
class DemoStatus:
    state: str = DemoState.IDLE.value
    run_id: str = ""
    preset: str = ""
    mode: str = DemoMode.LIVE.value
    debug_overlay: bool = False
    frame_index: int = 0
    total_frames: int = 0
    source_timestamp_ms: float = 0.0
    video_duration_ms: float = 0.0
    source_fps: float = 30.0
    processing_fps: float = 0.0
    realtime_factor: float = 0.0
    occupied_seats: int = 0
    suspicious_seats: int = 0
    active_review_incidents: int = 0
    emitted_review_incidents: int = 0
    session_id: Optional[str] = None
    inference_mode: str = CapabilityMode.ERROR.value
    capability_health: Dict[str, str] = field(default_factory=dict)
    persistence_health: str = "UNKNOWN"
    last_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "run_id": self.run_id,
            "preset": self.preset,
            "mode": self.mode,
            "debug_overlay": self.debug_overlay,
            "frame_index": self.frame_index,
            "total_frames": self.total_frames,
            "source_timestamp_ms": round(self.source_timestamp_ms, 1),
            "video_duration_ms": round(self.video_duration_ms, 1),
            "source_fps": round(self.source_fps, 2),
            "processing_fps": round(self.processing_fps, 2),
            "realtime_factor": round(self.realtime_factor, 2),
            "occupied_seats": self.occupied_seats,
            "suspicious_seats": self.suspicious_seats,
            "active_review_incidents": self.active_review_incidents,
            "emitted_review_incidents": self.emitted_review_incidents,
            "session_id": self.session_id,
            "inference_mode": self.inference_mode,
            "capability_health": dict(self.capability_health),
            "persistence_health": self.persistence_health,
            "last_error": self.last_error,
        }


class DemoRuntime:
    """Singleton-capable, thread-safe execution runtime for VIGIL AI SRS v2 Demo."""

    _instance: Optional[DemoRuntime] = None

    @classmethod
    def get_instance(cls) -> DemoRuntime:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._callback_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()

        self.status = DemoStatus()
        self.latest_frame: Optional[np.ndarray] = None
        self.latest_jpeg: Optional[bytes] = None
        self.emitted_events: Dict[str, Dict[str, Any]] = {}  # event_id -> dict
        self.emitted_events_list: List[Dict[str, Any]] = []

        # Callbacks
        self._frame_callbacks: List[Callable[[np.ndarray, np.ndarray, int, float, dict], None]] = []
        self._event_callbacks: List[Callable[[ClassroomEvent, dict], None]] = []
        self._status_callbacks: List[Callable[[DemoStatus], None]] = []

    def register_frame_callback(self, cb: Callable[[np.ndarray, np.ndarray, int, float, dict], None]) -> None:
        with self._callback_lock:
            if cb not in self._frame_callbacks:
                self._frame_callbacks.append(cb)

    def register_event_callback(self, cb: Callable[[ClassroomEvent, dict], None]) -> None:
        with self._callback_lock:
            if cb not in self._event_callbacks:
                self._event_callbacks.append(cb)

    def register_status_callback(self, cb: Callable[[DemoStatus], None]) -> None:
        with self._callback_lock:
            if cb not in self._status_callbacks:
                self._status_callbacks.append(cb)

    def unregister_frame_callback(self, cb: Callable[..., None]) -> None:
        with self._callback_lock:
            if cb in self._frame_callbacks:
                self._frame_callbacks.remove(cb)

    def unregister_event_callback(self, cb: Callable[..., None]) -> None:
        with self._callback_lock:
            if cb in self._event_callbacks:
                self._event_callbacks.remove(cb)

    def unregister_status_callback(self, cb: Callable[..., None]) -> None:
        with self._callback_lock:
            if cb in self._status_callbacks:
                self._status_callbacks.remove(cb)

    def _callback_snapshot(self, kind: str) -> List[Callable[..., None]]:
        with self._callback_lock:
            return list(getattr(self, f"_{kind}_callbacks"))

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return self.status.to_dict()

    def get_events(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self.emitted_events_list)

    def get_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self.emitted_events.get(event_id)

    def add_or_update_event(self, event: ClassroomEvent, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Record or update an incident in runtime memory and notify callbacks."""
        if metadata:
            event.metadata.update(metadata)
        incident = review_incident_from_classroom_event(
            event,
            session_id=self.status.session_id or getattr(self, "_db_session_id", None) or "UNPERSISTED",
            capability_health=self.status.capability_health,
        )
        ev_dict = incident.to_dict()
        # Preserve DB/evidence adapter fields which are not part of perception.
        if metadata:
            ev_dict.update(metadata)

        with self._lock:
            existing_idx = next((i for i, e in enumerate(self.emitted_events_list) if e.get("event_id") == event.event_id), None)
            if existing_idx is not None:
                self.emitted_events_list[existing_idx] = ev_dict
            else:
                self.emitted_events_list.append(ev_dict)
            self.emitted_events[event.event_id] = ev_dict
            self.status.emitted_review_incidents = len(self.emitted_events_list)

        for cb in self._callback_snapshot("event"):
            try:
                cb(event, ev_dict)
            except Exception as exc:
                logger.warning("Demo event callback failed: %s", exc)
        return ev_dict

    _on_live_event = add_or_update_event

    def _persist_event_to_db(self, event: ClassroomEvent, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Test/Direct helper to persist or update event in DB."""
        if metadata:
            event.metadata.update(metadata)
        return self._persist_or_update_event(
            event=event,
            session_id=self.status.session_id or getattr(self, "_db_session_id", None),
            room_code=self.status.preset or "ROOM-01",
            camera_id="CAM-01",
            evidence_dir=Path("data/evidence"),
        )

    def get_latest_jpeg(self) -> Optional[bytes]:
        with self._lock:
            return self.latest_jpeg

    def start(
        self,
        preset: str = "india",
        mode: str = "LIVE",
        debug_overlay: bool = False,
        show_window: bool = False,
        max_frames: Optional[int] = None,
        stride: int = 1,
        allow_mock: bool = False,
    ) -> Dict[str, Any]:
        """Serialize starts so two inference workers can never overlap."""
        with self._lifecycle_lock:
            return self._start_locked(
                preset=preset,
                mode=mode,
                debug_overlay=debug_overlay,
                show_window=show_window,
                max_frames=max_frames,
                stride=stride,
                allow_mock=allow_mock,
            )

    def _start_locked(
        self,
        preset: str = "india",
        mode: str = "LIVE",
        debug_overlay: bool = False,
        show_window: bool = False,
        max_frames: Optional[int] = None,
        stride: int = 1,
        allow_mock: bool = False,
    ) -> Dict[str, Any]:
        """Start demo analysis in LIVE or REPLAY mode in a background worker thread."""
        with self._lock:
            if self.status.state in (DemoState.RUNNING.value, DemoState.PAUSED.value):
                # Stop existing run first
                self._stop_event.set()
                self._pause_event.clear()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                with self._lock:
                    self.status.state = DemoState.ERROR.value
                    self.status.last_error = "Previous demo worker did not terminate; new start refused"
                raise RuntimeError(self.status.last_error)

        with self._lock:
            self.reset_state()
            self._stop_event.clear()
            self._pause_event.clear()

            mode_enum = DemoMode.REPLAY if str(mode).upper() == "REPLAY" else DemoMode.LIVE
            run_id = f"DEMO-{int(time.time())}-{preset.lower()}"

            self.status.state = DemoState.RUNNING.value
            self.status.run_id = run_id
            self.status.preset = preset.lower()
            self.status.mode = mode_enum.value
            self.status.debug_overlay = bool(debug_overlay)
            self.status.inference_mode = (
                CapabilityMode.DEGRADED.value if mode_enum == DemoMode.REPLAY else CapabilityMode.ERROR.value
            )
            self.status.capability_health = {
                "pose": "RECORDED" if mode_enum == DemoMode.REPLAY else CapabilityMode.ERROR.value,
                "head_orientation": "RECORDED" if mode_enum == DemoMode.REPLAY else "UNKNOWN",
            }

            # Initialize DB Session record
            session_id = self._init_db_session(preset=preset, run_id=run_id, mode=mode_enum.value)
            self.status.session_id = session_id

            if mode_enum == DemoMode.REPLAY:
                self._thread = threading.Thread(
                    target=self._worker_replay,
                    args=(preset, run_id, debug_overlay, show_window, max_frames),
                    daemon=False,
                )
            else:
                self._thread = threading.Thread(
                    target=self._worker_live,
                    args=(preset, run_id, debug_overlay, show_window, max_frames, stride, allow_mock),
                    daemon=False,
                )
            self._thread.start()

            return self.status.to_dict()

    def pause(self) -> Dict[str, Any]:
        with self._lock:
            if self.status.state == DemoState.RUNNING.value:
                self._pause_event.set()
                self.status.state = DemoState.PAUSED.value
            return self.status.to_dict()

    def resume(self) -> Dict[str, Any]:
        with self._lock:
            if self.status.state == DemoState.PAUSED.value:
                self._pause_event.clear()
                self.status.state = DemoState.RUNNING.value
            return self.status.to_dict()

    def stop(self) -> Dict[str, Any]:
        with self._lock:
            self._stop_event.set()
            self._pause_event.clear()
            if self.status.state in (DemoState.RUNNING.value, DemoState.PAUSED.value):
                self.status.state = DemoState.STOPPED.value
        if self._thread and self._thread.is_alive() and threading.current_thread() != self._thread:
            self._thread.join(timeout=10.0)
        with self._lock:
            if self._thread and self._thread.is_alive():
                self.status.state = DemoState.ERROR.value
                self.status.last_error = "Demo worker did not terminate within 10 seconds"
            return self.status.to_dict()

    def reset(self) -> Dict[str, Any]:
        self.stop()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        with self._lock:
            self.reset_state()
            return self.status.to_dict()

    def reset_state(self) -> None:
        self.status = DemoStatus()
        self.latest_frame = None
        self.latest_jpeg = None
        self.emitted_events.clear()
        self.emitted_events_list.clear()

    # -------------------------------------------------------------------------
    # DB Persistence Helpers
    # -------------------------------------------------------------------------

    def _init_db_session(self, preset: str, run_id: str, mode: str) -> str:
        """Create an ExamSession in SQLite for tracking this demo run."""
        init_db()
        db = SessionLocal()
        session_id = str(uuid.uuid4())
        try:
            demo_cfg = get_demo_config(preset)
            room_code = demo_cfg.room_code

            # Ensure room exists
            room = db.query(ExamRoom).filter(ExamRoom.room_code == room_code).first()
            if not room:
                site = db.query(ExamSite).first()
                if not site:
                    site = ExamSite(name="Trường Đại học Demo", address="Khu Đô thị")
                    db.add(site)
                    db.commit()
                    db.refresh(site)
                room = ExamRoom(name=f"Phòng {room_code}", site_id=site.id, room_code=room_code)
                db.add(room)
                db.commit()
                db.refresh(room)

            camera = (
                db.query(Camera)
                .filter(Camera.room_id == room.id, Camera.name == demo_cfg.camera_id)
                .first()
            )
            if camera is None:
                camera = Camera(
                    room_id=room.id,
                    name=demo_cfg.camera_id,
                    source_uri=str(demo_cfg.video_path),
                    status="online",
                )
                db.add(camera)
                db.flush()

            sess = ExamSession(
                id=session_id,
                room_id=room.id,
                camera_id=camera.id,
                exam_name=f"VIGIL AI {mode} Demo ({preset.upper()}) - {run_id}",
                subject_code=f"ICTU-2026-{preset.upper()}",
                status="RUNNING",
            )
            db.add(sess)
            db.commit()
            self._db_camera_id = camera.id
        except Exception as exc:
            logger.warning("Could not create DB ExamSession: %s", exc)
        finally:
            db.close()
        return session_id

    def _persist_or_update_event(
        self,
        event: ClassroomEvent,
        session_id: Optional[str],
        room_code: str,
        camera_id: str,
        evidence_dir: Path,
    ) -> Dict[str, Any]:
        """Save or update ClassroomEvent in SQLite & return clean dictionary representation."""
        incident = review_incident_from_classroom_event(
            event,
            session_id=session_id or "UNPERSISTED",
            capability_health=self.status.capability_health,
        )
        event_dict = incident.to_dict()

        # Generate relative evidence URLs
        snapshot_rel = None
        video_rel = None
        video_sha256 = None

        snapshot_exists = bool(event.evidence_path and Path(event.evidence_path).is_file())
        video_exists = bool(event.evidence_video_path and Path(event.evidence_video_path).is_file() and Path(event.evidence_video_path).stat().st_size > 0)
        if snapshot_exists:
            snapshot_rel = f"/api/v1/demo/evidence/snapshot/{Path(event.evidence_path).name}"
        if video_exists:
            video_rel = f"/api/v1/demo/evidence/video/{Path(event.evidence_video_path).name}"
            try:
                video_sha256 = compute_file_sha256(Path(event.evidence_video_path))
            except Exception:
                pass

        event_dict["snapshot_url"] = snapshot_rel
        event_dict["video_url"] = video_rel
        event_dict["video_sha256"] = video_sha256
        event_dict["hash_status"] = HashStatus.AVAILABLE_NOT_CHECKED.value if video_sha256 else HashStatus.NOT_AVAILABLE.value
        event_dict["evidence_status"] = event.metadata.get("evidence_status") or (
            EvidenceStatus.READY.value if snapshot_exists and (not event.evidence_video_path or video_exists)
            else EvidenceStatus.PENDING.value
        )
        event_dict["evidence_error"] = event.metadata.get("evidence_error")
        event_dict["review_status"] = "PENDING"
        event_dict["occurrence_count"] = event.metadata.get("occurrence_count", 1)
        event_dict["first_seen_ms"] = event.metadata.get("first_seen_ms", getattr(event, "timestamp_ms", 0.0) or 0.0)
        event_dict["last_seen_ms"] = event.metadata.get("last_seen_ms", getattr(event, "timestamp_ms", 0.0) or 0.0)
        event_dict["peak_risk_score"] = float(event.metadata.get("peak_risk_score", event.metadata.get("risk_score", 0.0)) or 0.0)
        event_dict["primary_pattern"] = event.metadata.get("primary_pattern") or event.behavior

        # Persist to SQLite
        try:
            db = SessionLocal()
            existing = db.query(DetectionEvent).filter(DetectionEvent.event_id == event.event_id).first()
            if existing:
                # Update in-place
                existing.risk_score = int(event_dict["peak_risk_score"])
                existing.severity = event.severity
                existing.supporting_patterns_json = json.dumps(event.metadata.get("supporting_pattern_ids", []))
                existing.reviewer_note = f"Occurrence x{event_dict['occurrence_count']}"
                db_event = existing
            else:
                # Find seat db id
                seat_obj = (
                    db.query(SeatROI)
                    .filter(SeatROI.seat_code == event.seat_id)
                    .first()
                )
                seat_db_id = seat_obj.id if seat_obj else None
                room_obj = db.query(ExamRoom).filter(ExamRoom.room_code == room_code).first()
                room_db_id = room_obj.id if room_obj else None
                camera_obj = None
                if room_obj is not None:
                    camera_obj = (
                        db.query(Camera)
                        .filter(Camera.room_id == room_obj.id, Camera.name == camera_id)
                        .first()
                    )

                db_event = DetectionEvent(
                    id=str(uuid.uuid4()),
                    session_id=session_id or str(uuid.uuid4()),
                    room_id=room_db_id,
                    camera_id=(camera_obj.id if camera_obj else getattr(self, "_db_camera_id", None)),
                    seat_id=seat_db_id,
                    event_id=event.event_id,
                    track_id=event.track_id or 0,
                    event_type="REVIEW_INCIDENT",
                    primary_signal=event.behavior,
                    behavior=event.behavior,
                    primary_pattern=event_dict["primary_pattern"],
                    supporting_patterns_json=json.dumps(event.metadata.get("supporting_pattern_ids", [])),
                    observation_quality=event.confidence_peak,
                    severity=event.severity,
                    risk_score=int(event_dict["peak_risk_score"]),
                    confidence_avg=event.confidence_avg,
                    confidence_peak=event.confidence_peak,
                    duration_seconds=event.duration_seconds,
                    bbox_json=json.dumps(event.bbox) if event.bbox else None,
                    status="FLAGGED_FOR_HUMAN_REVIEW",
                    review_status="PENDING",
                    room_context=event.room_context,
                )
                db.add(db_event)
                db.flush()

            # Attach or update the one evidence lifecycle record.
            if event.evidence_path or event.evidence_video_path:
                evi = db.query(EvidenceFile).filter(EvidenceFile.event_id == db_event.id).first()
                if evi is None:
                    evi = EvidenceFile(
                        event_id=db_event.id,
                        file_path=event.evidence_video_path or event.evidence_path or "",
                    )
                    db.add(evi)
                evi.file_path = event.evidence_video_path or event.evidence_path or ""
                evi.snapshot_path = event.evidence_path
                evi.video_path = event.evidence_video_path
                evi.video_sha256 = video_sha256
                evi.file_type = "video/mp4" if event.evidence_video_path else "image/jpeg"
                evi.file_size_bytes = Path(event.evidence_video_path).stat().st_size if video_exists else 0
                evi.status = event_dict["evidence_status"]
                evi.error_message = event_dict["evidence_error"]
            db.commit()
        except Exception as exc:
            if "db" in locals():
                db.rollback()
            logger.error("Database event persistence failed for %s: %s", event.event_id, exc)
            with self._lock:
                self.status.persistence_health = "ERROR"
            event_dict["persistence_error"] = str(exc)
        else:
            with self._lock:
                self.status.persistence_health = "READY"
        finally:
            if "db" in locals():
                db.close()

        return event_dict

    # -------------------------------------------------------------------------
    # WORKER A: LIVE SRS V2 PIPELINE
    # -------------------------------------------------------------------------

    def _worker_live(
        self,
        preset: str,
        run_id: str,
        debug_overlay: bool,
        show_window: bool,
        max_frames: Optional[int],
        stride: int,
        allow_mock: bool,
    ) -> None:
        """Executes full live SRS v2 inference pipeline."""
        logger.info("Starting SRS v2 LIVE analysis worker for preset '%s' (run_id: %s)...", preset, run_id)
        config = get_demo_config(preset)
        config.debug_overlay = debug_overlay
        config.show_window = show_window
        if max_frames is not None:
            config.max_frames = max_frames
        config.stride = stride

        # Output isolation directory
        out_p = Path(f"data/demo_runs/{run_id}")
        out_p.mkdir(parents=True, exist_ok=True)
        evidence_dir = out_p / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)

        cap = cv2.VideoCapture(str(config.video_path))
        if not cap.isOpened():
            with self._lock:
                self.status.state = DemoState.ERROR.value
                self.status.last_error = f"Cannot open video: {config.video_path}"
            return

        orig_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        orig_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if config.max_frames and config.max_frames < total_frames:
            total_frames = config.max_frames

        with self._lock:
            self.status.total_frames = total_frames
            self.status.source_fps = fps
            self.status.video_duration_ms = (total_frames / fps) * 1000.0 if fps > 0 else 0.0

        # Load Scene Profile & Seat Setup
        scene_profile: Optional[SceneProfile] = None
        seats_preset = config.seats_preset or []
        if config.scene_config_path and Path(config.scene_config_path).exists():
            scene_profile = SceneProfile.from_file(config.scene_config_path)
            seat_graph = scene_profile.seat_graph
        else:
            seat_graph = SeatGraph(room_id=config.room_code)

        # Setup Database Seats & SeatManager
        seat_defs: List[SeatDefinition] = []
        if scene_profile:
            for s_code, ctx in scene_profile.seat_graph.seats_context.items():
                poly = ctx.metadata.get("polygon")
                if poly is None:
                    for raw_s in scene_profile.raw_config.get("seats", []):
                        if raw_s.get("seat_code") == s_code:
                            poly = raw_s.get("polygon")
                            break
                if poly:
                    seat_defs.append(
                        SeatDefinition(
                            seat_id=s_code,
                            room_id=config.room_code,
                            seat_code=s_code,
                            seat_label=ctx.metadata.get("seat_label") or s_code,
                            polygon=np.array(poly, dtype=np.float32),
                            camera_id=config.camera_id,
                            desk_y=ctx.desk_geometry.desk_boundary_y if ctx.desk_geometry else None,
                        )
                    )
        seat_mgr = SeatManager(room_id=config.room_code, camera_id=config.camera_id)
        seat_mgr.load_seats(seat_defs)

        runtime_cfg = resolve_runtime_config(scene_profile=scene_profile, demo_config=config)

        if self._stop_event.is_set():
            return

        # Initialize AI Models & Engines
        try:
            detector = PoseClassroomDetector(confidence_threshold=config.pose_conf, allow_mock=allow_mock)
            detector.ensure_available()
        except Exception as exc:
            logger.error("LIVE start refused: %s", exc)
            cap.release()
            with self._lock:
                self.status.state = DemoState.ERROR.value
                self.status.inference_mode = CapabilityMode.ERROR.value
                self.status.capability_health["pose"] = CapabilityMode.ERROR.value
                self.status.last_error = str(exc)
            return
        with self._lock:
            self.status.inference_mode = detector.inference_mode
            self.status.capability_health.update(detector.capability_health)
        if self._stop_event.is_set():
            return
        head_provider_inst = create_head_pose_provider(config.head_provider)
        hpe_ready = not (
            config.head_provider.lower().replace("-", "_") in ("sixdrepnet", "6drepnet", "sixd")
            and getattr(head_provider_inst.__class__, "_shared_model", None) is None
        )
        with self._lock:
            self.status.capability_health["head_orientation"] = (
                CapabilityMode.REAL.value if hpe_ready else CapabilityMode.DEGRADED.value
            )
        if self._stop_event.is_set():
            return
        obs_extractor = ObservationExtractor(head_pose_provider=head_provider_inst)
        episode_engine = TemporalEpisodeEngine(**runtime_cfg["temporal"])
        pattern_engine = BehaviorPatternEngine(seat_graph=seat_graph, **runtime_cfg["patterns"])
        risk_tracker = SeatRiskTracker(room_id=config.room_code, camera_id=config.camera_id, **runtime_cfg["risk"])

        evidence_buffer = EvidenceVideoBuffer(
            pre_event_seconds=5.0,
            post_event_seconds=5.0,
            fps=fps,
            output_dir=evidence_dir,
            async_write=True,
        )
        renderer = DemoHUDOverlayRenderer(debug_overlay=debug_overlay)

        # Video writer for result.mp4
        result_video_path = out_p / "result.mp4"
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        out_writer = cv2.VideoWriter(str(result_video_path), fourcc, fps / config.stride, (orig_w, orig_h))

        all_episodes: List[TemporalEpisode] = []
        all_events: List[ClassroomEvent] = []
        all_patterns: List[BehaviorPattern] = []
        evidence_jobs: Dict[str, Any] = {}

        hpe_interval_ms = (1000.0 / config.hpe_hz) if config.hpe_hz > 0 else 200.0
        hpe_max_age_ms = runtime_cfg["head_pose"]["cache_max_age_ms"]
        seat_hpe_cache: Dict[str, Tuple[HeadOrientationEstimate, float]] = {}
        last_hpe_time: Dict[str, float] = {}

        frame_idx = 0
        processed_count = 0
        start_wall_time = time.time()
        rolling_fps_deque = deque(maxlen=30)
        last_frame_time = time.time()
        source_ts_ms = 0.0

        try:
            while not self._stop_event.is_set():
                if self._pause_event.is_set():
                    time.sleep(0.1)
                    continue

                ret, frame = cap.read()
                if not ret:
                    break

                frame_idx += 1
                if config.max_frames and frame_idx > config.max_frames:
                    break

                if config.stride > 1 and (frame_idx % config.stride) != 0:
                    continue

                processed_count += 1
                now = time.time()
                dt = max(0.001, now - last_frame_time)
                last_frame_time = now
                rolling_fps_deque.append(1.0 / dt)
                curr_fps = float(np.mean(rolling_fps_deque))
                source_ts_ms = (frame_idx / fps) * 1000.0

                # 1. Perception
                detections = detector.detect(frame)
                evidence_buffer.add_frame(frame, frame_idx=frame_idx, timestamp_ms=source_ts_ms)

                # 2. Spatial Seat Matching & Roaming Person Isolation
                mapped_seats, unmapped_dets = seat_mgr.map_detections_to_seats(
                    detections=detections,
                    timestamp_ms=source_ts_ms,
                    frame_idx=frame_idx,
                )
                occupied_seats = [occ for occ in seat_mgr.occupancies.values() if occ.state == SeatState.OCCUPIED]

                # 3. Scheduled Batched 6DRepNet HPE
                hpe_batch_requests: List[Dict[str, Any]] = []
                for seat_code, occ in seat_mgr.occupancies.items():
                    if occ.state == SeatState.OCCUPIED and occ.assigned_detection is not None:
                        s_ctx = seat_graph.get_context(seat_code)
                        if s_ctx and s_ctx.capabilities.head_orientation != CapabilityStatus.DISABLED:
                            if (source_ts_ms - last_hpe_time.get(seat_code, -100000.0)) >= hpe_interval_ms:
                                hpe_batch_requests.append({
                                    "seat_id": seat_code,
                                    "keypoints": occ.assigned_detection.keypoints,
                                    "bbox": occ.assigned_detection.bbox,
                                    "seat_baseline_yaw": s_ctx.reference_directions.baseline_yaw,
                                    "seat_baseline_pitch": s_ctx.reference_directions.baseline_pitch,
                                })
                                last_hpe_time[seat_code] = source_ts_ms

                if hpe_batch_requests:
                    batch_estimates = head_provider_inst.estimate_batch(requests=hpe_batch_requests, frame=frame)
                    for s_id, est in batch_estimates.items():
                        seat_hpe_cache[s_id] = (est, source_ts_ms)

                # 4. Temporal Observation, Episode & Pattern Ingestion
                active_episodes_frame: List[TemporalEpisode] = []
                patterns_frame: List[BehaviorPattern] = []

                for seat_code, occ in seat_mgr.occupancies.items():
                    s_ctx = seat_graph.get_context(seat_code) or SeatContext(seat_id=seat_code)
                    det = occ.assigned_detection
                    cached_hpe = None
                    if seat_code in seat_hpe_cache:
                        est, cached_ts = seat_hpe_cache[seat_code]
                        if (source_ts_ms - cached_ts) <= hpe_max_age_ms:
                            cached_hpe = est

                    obs_list = obs_extractor.extract(
                        detection=det,
                        seat_context=s_ctx,
                        timestamp_ms=source_ts_ms,
                        nearby_person_count=len(occ.candidate_detections),
                        occupancy_state=occ.state,
                        frame=frame,
                        precomputed_head_estimate=cached_hpe,
                    )

                    episodes_seat = episode_engine.process_observations(obs_list, timestamp_ms=source_ts_ms)
                    active_episodes_frame.extend(episodes_seat)

                    patterns_seat = pattern_engine.ingest_episodes(
                        active_episodes=episodes_seat,
                        completed_episodes=episode_engine.completed_episodes,
                        seat_context=s_ctx,
                        timestamp_ms=source_ts_ms,
                    )
                    patterns_frame.extend(patterns_seat)

                    # Update Risk & Incident Aggregator (with capability gating)
                    event = risk_tracker.update_seat(
                        seat_id=seat_code,
                        active_episodes=episodes_seat,
                        detected_patterns=patterns_seat,
                        timestamp_ms=source_ts_ms,
                        detection=det,
                        frame_image=frame,
                        camera_id=config.camera_id,
                        seat_context=s_ctx,
                    )

                    if event is not None:
                        all_events.append(event)
                        # Save Evidence Clip
                        if config.save_evidence:
                            clip_job = evidence_buffer.trigger_clip(
                                event_id=event.event_id,
                                track_id=event.track_id,
                                behavior=event.behavior,
                                frame_idx=frame_idx,
                                timestamp_ms=source_ts_ms,
                            )
                            event.evidence_video_path = str(clip_job.output_path)
                            evidence_jobs[event.event_id] = clip_job
                            snapshot_path = evidence_dir / f"{event.event_id}_snapshot.jpg"
                            snapshot_frame = event.evidence_frame if event.evidence_frame is not None else frame
                            if cv2.imwrite(str(snapshot_path), snapshot_frame):
                                event.evidence_path = str(snapshot_path)

                        # Sync and Broadcast Event
                        ev_dict = self._persist_or_update_event(
                            event=event,
                            session_id=self.status.session_id,
                            room_code=config.room_code,
                            camera_id=config.camera_id,
                            evidence_dir=evidence_dir,
                        )
                        self.add_or_update_event(event, ev_dict)

                    all_patterns.extend(patterns_seat)

                # 5. Render Output Frame
                active_seats_state = {}
                for s_code, prof in risk_tracker.profiles.items():
                    active_seats_state[s_code] = {
                        "risk_score": prof.risk_score,
                        "state": prof.current_state,
                        "active_episodes": [ep.episode_type for ep in active_episodes_frame if ep.seat_id == s_code],
                        "active_incident": prof.active_incident,
                    }

                metrics_telemetry = {
                    "source_fps": fps,
                    "processing_fps": curr_fps,
                    "realtime_factor": curr_fps / fps if fps > 0 else 1.0,
                    "total_frames": total_frames,
                    "active_episodes_count": len(active_episodes_frame),
                    "active_patterns_count": len(patterns_frame),
                    "total_events_count": len(self.emitted_events_list),
                    "active_review_incidents": sum(1 for p in risk_tracker.profiles.values() if p.active_incident is not None),
                }

                annotated_frame = renderer.render_frame(
                    frame=frame,
                    frame_idx=frame_idx,
                    timestamp_ms=source_ts_ms,
                    fps=curr_fps,
                    room_code=config.room_code,
                    camera_id=config.camera_id,
                    seat_mgr=seat_mgr,
                    seat_graph=seat_graph,
                    risk_tracker=risk_tracker,
                    active_episodes=active_episodes_frame,
                    recent_events=all_events,
                    raw_observations=None,
                    detections=detections,
                    roaming_detections=unmapped_dets,
                    runtime_metrics=metrics_telemetry,
                    debug_overlay=debug_overlay,
                )
                if detector.inference_mode == CapabilityMode.MOCK.value:
                    cv2.putText(
                        annotated_frame,
                        "MOCK / SIMULATION - NOT REAL INFERENCE",
                        (24, 48),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.9,
                        (0, 0, 255),
                        3,
                        cv2.LINE_AA,
                    )

                out_writer.write(annotated_frame)
                _, jpeg_buf = cv2.imencode(".jpg", annotated_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                jpeg_bytes = jpeg_buf.tobytes()

                with self._lock:
                    self.latest_frame = annotated_frame
                    self.latest_jpeg = jpeg_bytes
                    self.status.frame_index = frame_idx
                    self.status.source_timestamp_ms = source_ts_ms
                    self.status.processing_fps = curr_fps
                    self.status.realtime_factor = curr_fps / fps if fps > 0 else 1.0
                    self.status.occupied_seats = len(occupied_seats)
                    self.status.suspicious_seats = sum(
                        1 for p in risk_tracker.profiles.values() if p.current_state in (RiskState.SUSPICIOUS.value, RiskState.FLAGGED_FOR_REVIEW.value)
                    )
                    self.status.active_review_incidents = metrics_telemetry["active_review_incidents"]

                for cb in self._callback_snapshot("frame"):
                    try:
                        cb(annotated_frame, frame, frame_idx, source_ts_ms, metrics_telemetry)
                    except Exception:
                        pass

                for cb in self._callback_snapshot("status"):
                    try:
                        cb(self.status)
                    except Exception:
                        pass

                if show_window:
                    cv2.imshow(f"VIGIL AI LIVE - {config.name.upper()}", annotated_frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break

            # Stream Flush at EOF
            logger.info("Flushing final episodes and evidence at EOF (ts=%.1fms)...", source_ts_ms)
            flushed_episodes = episode_engine.flush_all(timestamp_ms=source_ts_ms)
            all_episodes.extend(episode_engine.completed_episodes)
            evidence_buffer.flush_all()
            for completed_event in all_events:
                job = evidence_jobs.get(completed_event.event_id)
                if job is not None:
                    completed_event.metadata["evidence_status"] = job.status
                    if job.error_message:
                        completed_event.metadata["evidence_error"] = job.error_message
                    refreshed = self._persist_or_update_event(
                        event=completed_event,
                        session_id=self.status.session_id,
                        room_code=config.room_code,
                        camera_id=config.camera_id,
                        evidence_dir=evidence_dir,
                    )
                    self.add_or_update_event(completed_event, refreshed)
            out_writer.release()
            cap.release()
            if show_window:
                cv2.destroyAllWindows()

            # Export Final Demo Artifacts
            scene_meta = {
                "scene_id": config.name,
                "room_code": config.room_code,
                "camera_id": config.camera_id,
                "video_file": str(config.video_path),
                "duration_sec": source_ts_ms / 1000.0,
                "fps": fps,
                "total_seats": len(seat_mgr.seats),
                "occupied_seats": sum(1 for occ in seat_mgr.occupancies.values() if occ.state == SeatState.OCCUPIED),
            }

            runtime_stats = {
                "total_frames": processed_count,
                "wall_time_sec": time.time() - start_wall_time,
                "overall_fps": processed_count / max(0.001, time.time() - start_wall_time),
                "realtime_factor": (source_ts_ms / 1000.0) / max(0.001, time.time() - start_wall_time),
                "inference_mode": detector.inference_mode,
                "capability_health": detector.capability_health | {
                    "head_orientation": self.status.capability_health.get("head_orientation", "UNKNOWN")
                },
            }

            export_demo_artifacts(
                output_dir=out_p,
                episodes=all_episodes,
                events=all_events,
                patterns=all_patterns,
                risk_tracker=risk_tracker,
                scene_metadata=scene_meta,
                runtime_stats=runtime_stats,
            )

            # Copy artifacts to canonical replay directory as well
            replay_dst = Path(f"data/demo_final/{preset}")
            replay_dst.mkdir(parents=True, exist_ok=True)
            for f in out_p.glob("*"):
                if f.is_file():
                    shutil.copy2(f, replay_dst / f.name)
            if evidence_dir.exists():
                rep_evi = replay_dst / "evidence"
                rep_evi.mkdir(parents=True, exist_ok=True)
                for ef in evidence_dir.glob("*"):
                    if ef.is_file():
                        shutil.copy2(ef, rep_evi / ef.name)

            with self._lock:
                self.status.state = (
                    DemoState.STOPPED.value if self._stop_event.is_set() else DemoState.COMPLETED.value
                )
            logger.info("SRS v2 LIVE analysis completed for run_id: %s", run_id)

        except Exception as exc:
            logger.error("Error in LIVE demo runtime: %s", exc, exc_info=True)
            with self._lock:
                self.status.state = DemoState.ERROR.value
                self.status.last_error = str(exc)

    # -------------------------------------------------------------------------
    # WORKER B: RECORDED ANALYSIS REPLAY
    # -------------------------------------------------------------------------

    def _worker_replay(
        self,
        preset: str,
        run_id: str,
        debug_overlay: bool,
        show_window: bool,
        max_frames: Optional[int],
    ) -> None:
        """Executes smooth recorded analysis replay with timestamp-synchronized events."""
        logger.info("Starting RECORDED ANALYSIS REPLAY for preset '%s' (run_id: %s)...", preset, run_id)
        demo_cfg = get_demo_config(preset)

        # Locate precomputed artifacts
        replay_root = Path(f"data/demo_final/{preset}")
        if not replay_root.exists() or not (replay_root / "result.mp4").exists():
            replay_root = Path(demo_cfg.output_dir)

        result_video = replay_root / "result.mp4"
        events_json = replay_root / "events.json"
        summary_json = replay_root / "demo_summary.json"

        if not result_video.exists():
            with self._lock:
                self.status.state = DemoState.ERROR.value
                self.status.last_error = f"Replay video not found: {result_video}. Run LIVE mode first to generate artifacts."
            return

        # Load precomputed events
        raw_events_data = []
        if events_json.exists():
            try:
                with open(events_json, "r", encoding="utf-8") as f:
                    raw_events_data = json.load(f)
            except Exception as exc:
                logger.warning("Could not parse events.json: %s", exc)

        cap = cv2.VideoCapture(str(result_video))
        if not cap.isOpened():
            with self._lock:
                self.status.state = DemoState.ERROR.value
                self.status.last_error = f"Cannot open replay video: {result_video}"
            return

        orig_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        orig_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS) or demo_cfg.fps or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if max_frames and max_frames < total_frames:
            total_frames = max_frames

        frame_duration_sec = 1.0 / fps if fps > 0 else 0.033

        with self._lock:
            self.status.total_frames = total_frames
            self.status.source_fps = fps
            self.status.video_duration_ms = (total_frames / fps) * 1000.0
            self.status.processing_fps = fps
            self.status.realtime_factor = 1.0

        # Sort events by trigger timestamp
        sorted_events = sorted(raw_events_data, key=lambda e: float(e.get("timestamp_ms") or e.get("start_timestamp_ms", 0.0)))
        emitted_event_ids: Set[str] = set()

        frame_idx = 0
        source_ts_ms = 0.0
        start_playback_time = time.time()

        try:
            while not self._stop_event.is_set():
                if self._pause_event.is_set():
                    time.sleep(0.1)
                    continue

                t_frame_start = time.time()
                ret, frame = cap.read()
                if not ret:
                    break

                frame_idx += 1
                if max_frames and frame_idx > max_frames:
                    break

                source_ts_ms = (frame_idx / fps) * 1000.0

                # Check and emit events synchronized with timestamp
                for ev in sorted_events:
                    ev_id = ev.get("event_id") or ev.get("id")
                    ev_ts = float(ev.get("timestamp_ms") or ev.get("start_timestamp_ms", 0.0))
                    if ev_id not in emitted_event_ids and source_ts_ms >= ev_ts:
                        emitted_event_ids.add(ev_id)
                        replay_event = classroom_event_from_replay(ev)
                        # Resolve media against the replay package instead of
                        # trusting a stale absolute path from an older run.
                        snap_name = evidence_basename(replay_event.evidence_path)
                        vid_name = evidence_basename(replay_event.evidence_video_path)
                        if snap_name:
                            candidate = replay_root / "evidence" / snap_name
                            replay_event.evidence_path = str(candidate if candidate.exists() else replay_event.evidence_path)
                        if vid_name:
                            candidate = replay_root / "evidence" / vid_name
                            replay_event.evidence_video_path = str(candidate if candidate.exists() else replay_event.evidence_video_path)

                        # A replay incident is durable before it is visible or
                        # reviewable.  The adapter handles current and legacy
                        # artifact field names without mutating ClassroomEvent.
                        ev_dict = self._persist_or_update_event(
                            event=replay_event,
                            session_id=self.status.session_id,
                            room_code=demo_cfg.room_code,
                            camera_id=demo_cfg.camera_id,
                            evidence_dir=replay_root / "evidence",
                        )
                        self.add_or_update_event(replay_event, ev_dict)

                # Encode Frame
                _, jpeg_buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                jpeg_bytes = jpeg_buf.tobytes()

                with self._lock:
                    self.latest_frame = frame
                    self.latest_jpeg = jpeg_bytes
                    self.status.frame_index = frame_idx
                    self.status.source_timestamp_ms = source_ts_ms
                    self.status.processing_fps = fps
                    self.status.realtime_factor = 1.0

                metrics_telemetry = {
                    "source_fps": fps,
                    "processing_fps": fps,
                    "realtime_factor": 1.0,
                    "total_frames": total_frames,
                    "total_events_count": len(self.emitted_events_list),
                    "active_review_incidents": len(self.emitted_events_list),
                }

                for cb in self._callback_snapshot("frame"):
                    try:
                        cb(frame, frame, frame_idx, source_ts_ms, metrics_telemetry)
                    except Exception:
                        pass

                for cb in self._callback_snapshot("status"):
                    try:
                        cb(self.status)
                    except Exception:
                        pass

                if show_window:
                    cv2.imshow(f"VIGIL AI REPLAY - {preset.upper()}", frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break

                # Maintain realistic playback rate
                elapsed = time.time() - t_frame_start
                sleep_time = max(0.001, frame_duration_sec - elapsed)
                time.sleep(sleep_time)

            cap.release()
            if show_window:
                cv2.destroyAllWindows()

            with self._lock:
                self.status.state = (
                    DemoState.STOPPED.value if self._stop_event.is_set() else DemoState.COMPLETED.value
                )
            logger.info("RECORDED ANALYSIS REPLAY completed for run_id: %s", run_id)

        except Exception as exc:
            logger.error("Error in REPLAY demo runtime: %s", exc, exc_info=True)
            with self._lock:
                self.status.state = DemoState.ERROR.value
                self.status.last_error = str(exc)

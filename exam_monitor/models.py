from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EventType(str, Enum):
    LOOK_AWAY = "look_away"
    HEAD_TURN = "head_turn"
    TALKING = "talking"
    NO_FACE = "no_face"
    MULTIPLE_FACES = "multiple_faces"
    SUSPICIOUS_OBJECT = "suspicious_object"
    LOW_LIGHT = "low_light"
    CAMERA_INTERRUPTED = "camera_interrupted"


EVENT_LABELS = {
    EventType.LOOK_AWAY: "Mắt nhìn ngoài vùng thi",
    EventType.HEAD_TURN: "Quay đầu khỏi màn hình",
    EventType.TALKING: "Nghi ngờ nói chuyện",
    EventType.NO_FACE: "Không thấy khuôn mặt",
    EventType.MULTIPLE_FACES: "Phát hiện nhiều người",
    EventType.SUSPICIOUS_OBJECT: "Vật thể không được phép",
    EventType.LOW_LIGHT: "Điều kiện ánh sáng kém",
    EventType.CAMERA_INTERRUPTED: "Camera bị gián đoạn",
}

SEVERITY_LABELS = {
    Severity.INFO: "Thông tin",
    Severity.LOW: "Thấp",
    Severity.MEDIUM: "Trung bình",
    Severity.HIGH: "Cao",
}


@dataclass(slots=True)
class AnalysisResult:
    timestamp: float
    face_count: int = 0
    direction: str = "unknown"
    eye_direction: str = "unknown"
    head_direction: str = "unknown"
    eye_gaze_x: float = 0.0
    eye_gaze_y: float = 0.0
    eye_zone_score: float = 0.0
    eyes_outside_zone: bool = False
    gaze_x: float = 0.0
    gaze_y: float = 0.0
    yaw: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    confidence: float = 0.0
    brightness: float = 0.0
    mouth_openness: float = 0.0
    talking_score: float = 0.0
    is_talking: bool = False
    audio_available: bool = False
    voice_detected: bool = False
    audio_level_db: float = -90.0
    person_count: int = 0
    suspicious_objects: list[str] = field(default_factory=list)
    object_detections: list[tuple[str, float, tuple[int, int, int, int]]] = field(default_factory=list)
    calibrated: bool = False
    landmarks: list[tuple[float, float]] = field(default_factory=list)
    face_box: tuple[int, int, int, int] | None = None
    face_boxes: list[tuple[int, int, int, int]] = field(default_factory=list)
    tracking_held: bool = False
    status_text: str = "Đang chờ tín hiệu"

    @property
    def detected_people_count(self) -> int:
        # Face landmarks are more precise for visible candidates. Fall back to
        # body detection only when it reveals an additional person.
        if self.face_count >= 2:
            return self.face_count
        return max(self.face_count, self.person_count)


@dataclass(slots=True)
class MonitoringEvent:
    event_id: str
    session_id: str
    event_type: EventType
    severity: Severity
    started_at: str
    ended_at: str
    duration_seconds: float
    reason: str
    confidence: float
    evidence_path: str | None = None
    review_status: str = "Chưa xem"
    reviewer_note: str = ""

    @property
    def label(self) -> str:
        return EVENT_LABELS[self.event_type]

    @property
    def severity_label(self) -> str:
        return SEVERITY_LABELS[self.severity]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["event_type"] = self.event_type.value
        data["severity"] = self.severity.value
        data["label"] = self.label
        data["severity_label"] = self.severity_label
        return data


@dataclass(slots=True)
class SessionInfo:
    session_id: str
    candidate_id: str
    exam_name: str
    started_at: str
    ended_at: str | None = None
    source_name: str = "Camera"
    frame_count: int = 0
    average_fps: float = 0.0
    events: list[MonitoringEvent] = field(default_factory=list)

    @property
    def risk_score(self) -> int:
        weights = {
            Severity.INFO: 0,
            Severity.LOW: 8,
            Severity.MEDIUM: 18,
            Severity.HIGH: 32,
        }
        raw = sum(weights[event.severity] for event in self.events)
        return min(100, raw)

    @property
    def duration_seconds(self) -> float:
        start = datetime.fromisoformat(self.started_at)
        end = datetime.fromisoformat(self.ended_at) if self.ended_at else datetime.now()
        return max(0.0, (end - start).total_seconds())

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "candidate_id": self.candidate_id,
            "exam_name": self.exam_name,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "source_name": self.source_name,
            "duration_seconds": round(self.duration_seconds, 2),
            "frame_count": self.frame_count,
            "average_fps": round(self.average_fps, 2),
            "risk_score": self.risk_score,
            "events": [event.to_dict() for event in self.events],
        }

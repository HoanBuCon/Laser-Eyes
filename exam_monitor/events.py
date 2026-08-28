from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .models import AnalysisResult, EventType, MonitoringEvent, SessionInfo, Severity


@dataclass(slots=True)
class EventRule:
    event_type: EventType
    threshold_seconds: float
    cooldown_seconds: float
    severity: Severity
    recovery_seconds: float = 0.8


DEFAULT_RULES = {
    # Short natural glances and posture adjustments should not interrupt an exam.
    EventType.LOOK_AWAY: EventRule(EventType.LOOK_AWAY, 1.6, 4.0, Severity.MEDIUM, 0.8),
    EventType.HEAD_TURN: EventRule(EventType.HEAD_TURN, 1.4, 4.0, Severity.MEDIUM, 0.8),
    EventType.TALKING: EventRule(EventType.TALKING, 1.0, 5.0, Severity.MEDIUM, 1.0),
    EventType.NO_FACE: EventRule(EventType.NO_FACE, 3.0, 5.0, Severity.MEDIUM, 1.0),
    # High-risk cases remain comparatively fast, but require several stable frames.
    EventType.MULTIPLE_FACES: EventRule(EventType.MULTIPLE_FACES, 0.8, 5.0, Severity.HIGH, 1.0),
    EventType.SUSPICIOUS_OBJECT: EventRule(EventType.SUSPICIOUS_OBJECT, 1.0, 6.0, Severity.HIGH, 1.2),
    EventType.LOW_LIGHT: EventRule(EventType.LOW_LIGHT, 5.0, 9.0, Severity.LOW, 2.0),
}


class EventDetector:
    """Turns continuous analysis states into explainable, debounced events."""

    def __init__(self, rules: dict[EventType, EventRule] | None = None):
        source = rules or DEFAULT_RULES
        self.rules = {
            key: EventRule(
                value.event_type,
                value.threshold_seconds,
                value.cooldown_seconds,
                value.severity,
                value.recovery_seconds,
            )
            for key, value in source.items()
        }
        self._active_since: dict[EventType, float] = {}
        self._last_emitted: dict[EventType, float] = {}
        self._emitted_for_episode: set[EventType] = set()
        self._inactive_since: dict[EventType, float] = {}

    def reset(self) -> None:
        self._active_since.clear()
        self._last_emitted.clear()
        self._emitted_for_episode.clear()
        self._inactive_since.clear()

    def set_threshold(self, event_type: EventType, seconds: float) -> None:
        self.rules[event_type].threshold_seconds = max(0.2, float(seconds))

    def _conditions(self, result: AnalysisResult) -> dict[EventType, bool]:
        exactly_one_person = result.detected_people_count == 1
        return {
            EventType.LOOK_AWAY: (
                exactly_one_person
                and result.head_direction == "center"
                and result.eyes_outside_zone
            ),
            EventType.HEAD_TURN: (
                exactly_one_person and result.head_direction not in {"center", "unknown"}
            ),
            EventType.TALKING: exactly_one_person and result.is_talking,
            EventType.NO_FACE: result.face_count == 0,
            # Online exams normally allow one candidate only. This catches two,
            # three or more people, including a person whose face is not visible.
            EventType.MULTIPLE_FACES: result.detected_people_count >= 2,
            EventType.SUSPICIOUS_OBJECT: bool(result.suspicious_objects),
            EventType.LOW_LIGHT: result.brightness < 42.0,
        }

    def update(
        self,
        result: AnalysisResult,
        session_id: str,
        now: float | None = None,
    ) -> list[MonitoringEvent]:
        now = time.monotonic() if now is None else now
        created: list[MonitoringEvent] = []
        for event_type, condition in self._conditions(result).items():
            rule = self.rules[event_type]
            if not condition:
                self._active_since.pop(event_type, None)
                if event_type in self._emitted_for_episode:
                    recovered_at = self._inactive_since.setdefault(event_type, now)
                    if now - recovered_at >= rule.recovery_seconds:
                        self._emitted_for_episode.discard(event_type)
                        self._inactive_since.pop(event_type, None)
                else:
                    self._inactive_since.pop(event_type, None)
                continue

            # A short tracking flicker must not turn one continuous behaviour
            # into several alerts. Rearm only after a stable normal interval.
            self._inactive_since.pop(event_type, None)
            if event_type in self._emitted_for_episode:
                continue

            started = self._active_since.setdefault(event_type, now)
            duration = now - started
            last_emitted = self._last_emitted.get(event_type, -1e9)
            if duration < rule.threshold_seconds or now - last_emitted < rule.cooldown_seconds:
                continue

            created.append(
                MonitoringEvent(
                    event_id=uuid.uuid4().hex[:10].upper(),
                    session_id=session_id,
                    event_type=event_type,
                    severity=rule.severity,
                    started_at=datetime.now().isoformat(timespec="seconds"),
                    ended_at=datetime.now().isoformat(timespec="seconds"),
                    duration_seconds=round(duration, 2),
                    reason=self._reason(event_type, result, duration),
                    confidence=round(result.confidence, 3),
                )
            )
            self._last_emitted[event_type] = now
            self._active_since.pop(event_type, None)
            self._emitted_for_episode.add(event_type)
        return created

    @staticmethod
    def _reason(event_type: EventType, result: AnalysisResult, duration: float) -> str:
        if event_type is EventType.LOOK_AWAY:
            labels = {"left": "trái", "right": "phải", "up": "trên", "down": "dưới"}
            direction = labels.get(result.eye_direction, result.eye_direction)
            return (
                f"Mắt nhìn ra ngoài vùng thi về phía {direction} trong {duration:.1f} giây "
                f"(mức lệch {result.eye_zone_score:.1f}x)."
            )
        if event_type is EventType.HEAD_TURN:
            labels = {"left": "trái", "right": "phải", "up": "trên", "down": "dưới"}
            direction = labels.get(result.head_direction, result.head_direction)
            return (
                f"Đầu quay sang {direction} trong {duration:.1f} giây "
                f"(yaw {result.yaw:.0f}°, pitch {result.pitch:.0f}°)."
            )
        if event_type is EventType.TALKING:
            return (
                f"Phát hiện chuyển động miệng lặp lại trong {duration:.1f} giây "
                f"(mức nghi ngờ {result.talking_score * 100:.0f}%)."
            )
        if event_type is EventType.NO_FACE:
            return f"Không phát hiện khuôn mặt trong {duration:.1f} giây."
        if event_type is EventType.MULTIPLE_FACES:
            count = result.detected_people_count
            return f"Phát hiện ít nhất {count} người trong khung hình."
        if event_type is EventType.SUSPICIOUS_OBJECT:
            labels = {"cell phone": "điện thoại", "book": "sách/tài liệu"}
            objects = ", ".join(labels.get(item, item) for item in result.suspicious_objects)
            return f"Phát hiện vật thể không được phép: {objects}."
        return f"Độ sáng trung bình chỉ {result.brightness:.0f}/255 trong {duration:.1f} giây."


class SessionStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.sessions_dir = self.data_dir / "sessions"
        self.evidence_dir = self.data_dir / "evidence"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)

    def save_session(self, session: SessionInfo) -> Path:
        path = self.sessions_dir / f"{session.session_id}.json"
        path.write_text(json.dumps(session.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def list_sessions(self) -> list[dict]:
        sessions: list[dict] = []
        for path in sorted(self.sessions_dir.glob("*.json"), reverse=True):
            try:
                sessions.append(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        return sessions

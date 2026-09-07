"""VIGIL AI — Classroom Cheating Detection & Surveillance Package.

Enterprise-ready multi-student proctoring engine with IoU spatial matching,
anti-flickering score accumulation, per-person behavior state machines,
crowd context intelligence, and evidence capture.
"""

from classroom_monitor.config import ClassroomConfig
from classroom_monitor.detector import ClassroomDetector
from classroom_monitor.event_engine import EventEngine
from classroom_monitor.models import (
    ClassroomDetection,
    ClassroomEvent,
    ContextSignal,
    Detection,
    SeverityLevel,
    TrackedDetection,
)
from classroom_monitor.video_processor import VideoProcessor

__all__ = [
    "ClassroomConfig",
    "ClassroomDetector",
    "EventEngine",
    "VideoProcessor",
    "Detection",
    "ClassroomDetection",
    "TrackedDetection",
    "ClassroomEvent",
    "ContextSignal",
    "SeverityLevel",
]

__version__ = "1.0.0"

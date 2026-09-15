"""VIGIL AI — Classroom Cheating Detection & Surveillance Package.

Enterprise AI Proctoring Co-pilot with Kalman Multi-Object Tracking,
Time-based Millisecond Score Accumulation, Silent Background Tracking,
Crowd Context Intelligence, and 10-Second Evidence Video Buffering.
"""

from classroom_monitor.behavior_tracker import PersonBehaviorTracker, StudentState
from classroom_monitor.config import ClassroomConfig, DEFAULT_CONFIG
from classroom_monitor.detector import ClassroomDetector
from classroom_monitor.event_engine import EventEngine
from classroom_monitor.live_event import LiveEvent
from classroom_monitor.models import (
    ClassroomDetection,
    ClassroomEvent,
    ContextSignal,
    Detection,
    EventStatus,
    FrameObservation,
    RoomRiskSummary,
    SeverityLevel,
    TrackState,
    TrackedDetection,
)
from classroom_monitor.score_accumulator import ScoreAccumulator
from classroom_monitor.spatial_matcher import KalmanBoxTracker, SpatialMatcher, compute_bbox_iou
from classroom_monitor.video_buffer import EvidenceVideoBuffer, VideoClipJob
from classroom_monitor.video_processor import VideoProcessor

__all__ = [
    "ClassroomConfig",
    "DEFAULT_CONFIG",
    "ClassroomDetector",
    "EventEngine",
    "VideoProcessor",
    "Detection",
    "ClassroomDetection",
    "TrackedDetection",
    "ClassroomEvent",
    "ContextSignal",
    "SeverityLevel",
    "EventStatus",
    "TrackState",
    "StudentState",
    "PersonBehaviorTracker",
    "LiveEvent",
    "ScoreAccumulator",
    "SpatialMatcher",
    "KalmanBoxTracker",
    "EvidenceVideoBuffer",
    "VideoClipJob",
    "FrameObservation",
    "RoomRiskSummary",
    "compute_bbox_iou",
]

__version__ = "2.0.0"

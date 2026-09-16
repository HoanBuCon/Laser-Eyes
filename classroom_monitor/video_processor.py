"""End-to-End Video Processor for Classroom Cheating Surveillance.

Reads camera streams or video files, executes YOLO inference (with optional SAHI),
runs EventEngine, manages 10s video evidence buffer, renders rich HUD overlays,
saves peak-confidence snapshots, and dispatches real-time callbacks.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

from classroom_monitor.config import DEFAULT_CONFIG, ClassroomConfig
from classroom_monitor.detector import ClassroomDetector, PoseClassroomDetector, create_detector
from classroom_monitor.event_engine import EventEngine
from classroom_monitor.models import ClassroomEvent, Detection, EventStatus, SeverityLevel

logger = logging.getLogger("VideoProcessor")

# Color palette for rendering overlays (BGR)
COLOR_NORMAL = (0, 200, 0)        # Green
COLOR_SUSPICIOUS = (0, 165, 255)  # Orange
COLOR_HIGH = (0, 0, 255)          # Red
COLOR_PURPLE = (255, 0, 255)      # Magenta
COLOR_CYAN = (255, 255, 0)        # Cyan
COLOR_YELLOW = (0, 215, 255)      # Gold/Yellow


class VideoProcessor:
    """Processes video feeds through the complete detection and event lifecycle."""

    def __init__(
        self,
        model_path: Optional[str | Path] = None,
        config: Optional[ClassroomConfig] = None,
        on_event: Optional[Callable[[ClassroomEvent], None]] = None,
        on_frame: Optional[Callable[[np.ndarray, List[Detection], List[ClassroomEvent]], None]] = None,
    ):
        self.config = config or DEFAULT_CONFIG
        self.detector = create_detector(config=self.config, model_path=model_path)
        self.event_engine = EventEngine(fps=self.config.default_fps, config=self.config)
        self.on_event = on_event
        self.on_frame = on_frame

    def process_video(
        self,
        video_path: str | Path,
        output_path: Optional[str | Path] = None,
        max_frames: Optional[int] = None,
        save_evidence: bool = True,
    ) -> Dict[str, Any]:
        """Process a stored video file offline and generate metrics and annotated video."""
        p = Path(video_path)
        if not p.exists():
            raise FileNotFoundError(f"Video file not found: {p.resolve()}")

        cap = cv2.VideoCapture(str(p))
        if not cap.isOpened():
            raise RuntimeError(f"Failed to open video source: {p}")

        fps = cap.get(cv2.CAP_PROP_FPS) or self.config.default_fps
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1280
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 720
        total_source_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        self.event_engine.fps = fps
        self.event_engine.video_buffer.fps = fps

        writer = None
        if output_path:
            out_p = Path(output_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(str(out_p), fourcc, fps, (width, height))

        logger.info(
            "Processing video '%s' (%dx%d @ %.1f FPS, %d total frames)...",
            p.name,
            width,
            height,
            fps,
            total_source_frames,
        )

        frame_idx = 0
        all_events: List[ClassroomEvent] = []
        start_time = time.time()

        evidence_out_dir = Path(self.config.evidence_dir)
        if save_evidence:
            evidence_out_dir.mkdir(parents=True, exist_ok=True)

        while True:
            ret, frame = cap.read()
            if not ret or (max_frames is not None and frame_idx >= max_frames):
                break

            # Calculate precise millisecond timestamp
            timestamp_ms = (frame_idx / fps) * 1000.0

            # 1. Perception
            detections = self.detector.detect(frame, frame_index=frame_idx)

            # 2. Decision & Event Generation
            events = self.event_engine.process_frame(
                detections, frame_idx, frame, timestamp_ms=timestamp_ms
            )

            for evt in events:
                all_events.append(evt)
                if save_evidence and evt.evidence_frame is not None:
                    ev_file = evidence_out_dir / f"{evt.event_id}.jpg"
                    cv2.imwrite(str(ev_file), evt.evidence_frame)
                    evt.evidence_path = str(ev_file)

                if self.on_event:
                    self.on_event(evt)

            # 3. Visualization & Rendering
            if writer or self.on_frame:
                annotated = self.render_overlay(frame, detections, events, frame_idx)
                if writer:
                    writer.write(annotated)
                if self.on_frame:
                    self.on_frame(annotated, detections, events)

            frame_idx += 1

        cap.release()
        if writer:
            writer.release()

        # Flush any in-flight 10s video evidence clips
        if self.config.enable_video_evidence:
            saved_clips = self.event_engine.video_buffer.flush_all()
            logger.info("Flushed and saved %d video evidence clips.", len(saved_clips))

        elapsed = time.time() - start_time
        processed_fps = frame_idx / max(0.001, elapsed)

        logger.info(
            "Completed processing %d frames in %.2fs (%.1f FPS). Total Events: %d",
            frame_idx,
            elapsed,
            processed_fps,
            len(all_events),
        )

        return {
            "video_path": str(p),
            "total_frames_processed": frame_idx,
            "elapsed_seconds": round(elapsed, 2),
            "average_fps": round(processed_fps, 1),
            "total_events": len(all_events),
            "events": [e.to_dict() for e in all_events],
            "output_video": str(output_path) if output_path else None,
        }

    def render_overlay(
        self,
        frame: np.ndarray,
        detections: List[Detection],
        active_events: List[ClassroomEvent],
        frame_idx: int,
    ) -> np.ndarray:
        """Render HUD indicators, bounding boxes, and alert banners onto frame."""
        vis = frame.copy()
        h, w = vis.shape[:2]

        # Draw Detections
        for det in detections:
            x1, y1, x2, y2 = det.bbox
            is_suspicious = det.class_name in getattr(self.config, "suspicious_classes", self.config.cheating_classes)

            if det.class_name == "phone using":
                color = COLOR_PURPLE
            elif is_suspicious:
                color = COLOR_HIGH
            else:
                color = COLOR_NORMAL

            cv2.rectangle(vis, (x1, y1), (x2, y2), color, 2)

            label = f"{det.class_name} ({det.confidence:.2f})"
            (tw, th), bl = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
            cv2.rectangle(vis, (x1, max(0, y1 - th - bl - 4)), (x1 + tw + 6, y1), color, -1)
            cv2.putText(
                vis,
                label,
                (x1 + 3, y1 - bl - 2),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (255, 255, 255) if color != (0, 255, 255) else (0, 0, 0),
                1,
                cv2.LINE_AA,
            )

        # Draw Top HUD Banner
        hud_h = 44
        cv2.rectangle(vis, (0, 0), (w, hud_h), (20, 20, 20), -1)
        cv2.putText(
            vis,
            f"VIGIL AI ENTERPRISE PROCTORING | Frame: {frame_idx} | Tracks: {self.event_engine.get_active_tracks_count()}",
            (14, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            COLOR_CYAN,
            1,
            cv2.LINE_AA,
        )

        # Highlight Active Events
        if active_events:
            has_recidivist = any(getattr(e, "is_recidivist", False) for e in active_events)
            if has_recidivist:
                alert_text = f"CRITICAL ALERT: RECIDIVIST FLAGGED ({len(active_events)} EVENTS)"
                alert_color = (0, 0, 255)
            else:
                alert_text = f"ALERT: {len(active_events)} EVENT(S) FLAGGED FOR REVIEW"
                alert_color = COLOR_YELLOW

            (atw, _), _ = cv2.getTextSize(alert_text, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
            cv2.putText(
                vis,
                alert_text,
                (max(10, w - atw - 20), 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                alert_color,
                2,
                cv2.LINE_AA,
            )

        return vis

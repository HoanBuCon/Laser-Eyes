"""YOLO Object Detection Inference Wrapper for Classroom Surveillance.

Handles model loading, fallback mechanisms, frame inference, and parsing
bounding boxes into clean Detection data structures.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

import numpy as np

from classroom_monitor.config import DEFAULT_CONFIG, ClassroomConfig
from classroom_monitor.models import Detection

logger = logging.getLogger("ClassroomDetector")


class ClassroomDetector:
    """Wrapper around YOLO neural network for classroom behavior detection."""

    def __init__(
        self,
        model_path: Optional[str | Path] = None,
        confidence_threshold: Optional[float] = None,
        config: Optional[ClassroomConfig] = None,
    ):
        self.config = config or DEFAULT_CONFIG
        self.confidence = (
            confidence_threshold
            if confidence_threshold is not None
            else self.config.confidence_threshold
        )
        self.model_path = Path(model_path or self.config.model_path)
        self.class_names = self.config.class_names
        self.model = None
        self._is_mock = False

        self._initialize_model()

    def _initialize_model(self) -> None:
        """Load YOLO weights or fallback gracefully if not found."""
        try:
            from ultralytics import YOLO
        except ImportError:
            logger.warning(
                "Ultralytics library is not installed. Operating in lightweight mock mode."
            )
            self._is_mock = True
            return

        # Check primary model path
        if self.model_path.exists():
            logger.info("Loading production classroom weights: %s", self.model_path.resolve())
            self.model = YOLO(str(self.model_path))
            return

        # Check fallback
        fallback = Path(self.config.fallback_model)
        if fallback.exists() or fallback.name.endswith(".pt"):
            logger.info("Custom weights not found at %s. Using base fallback: %s", self.model_path, fallback)
            try:
                self.model = YOLO(str(fallback))
                return
            except Exception as exc:
                logger.warning("Could not load fallback model: %s", exc)

        logger.warning("No YOLO weights available. Using simulated mock detector for testing.")
        self._is_mock = True

    def detect(self, frame: np.ndarray, frame_index: int = 0) -> List[Detection]:
        """Run object detection on a single video frame."""
        if frame is None or frame.size == 0:
            return []

        h, w = frame.shape[:2]

        if self._is_mock or self.model is None:
            return self._mock_detect(frame, frame_index, w, h)

        try:
            results = self.model(
                frame,
                conf=self.confidence,
                iou=self.config.nms_iou_threshold,
                imgsz=self.config.input_resolution,
                verbose=False,
            )[0]
        except Exception as exc:
            logger.error("Inference exception: %s", exc)
            return []

        detections: List[Detection] = []

        for box in results.boxes:
            cls_id = int(box.cls[0])
            conf = float(box.conf[0])
            xyxy = box.xyxy[0].tolist()

            # Ensure box coordinates are within image boundaries
            x1 = max(0, min(w - 1, int(xyxy[0])))
            y1 = max(0, min(h - 1, int(xyxy[1])))
            x2 = max(0, min(w, int(xyxy[2])))
            y2 = max(0, min(h, int(xyxy[3])))

            # Filter degenerate boxes
            if (x2 - x1) < 10 or (y2 - y1) < 10:
                continue

            cls_name = (
                self.class_names[cls_id]
                if cls_id < len(self.class_names)
                else f"class_{cls_id}"
            )

            detections.append(
                Detection(
                    class_id=cls_id,
                    class_name=cls_name,
                    confidence=conf,
                    bbox=(x1, y1, x2, y2),
                    frame_index=frame_index,
                )
            )

        return detections

    def detect_cheating_only(self, frame: np.ndarray, frame_index: int = 0) -> List[Detection]:
        """Filter out 'no cheating' detections, returning only suspicious activities."""
        return [
            d
            for d in self.detect(frame, frame_index)
            if d.class_name in self.config.cheating_classes
        ]

    def _mock_detect(
        self, frame: np.ndarray, frame_index: int, width: int, height: int
    ) -> List[Detection]:
        """Generates realistic synthetic detections for demonstration and testing without GPU."""
        detections = []
        # Grid of 4 simulated students
        grid = [
            (int(width * 0.15), int(height * 0.2), int(width * 0.35), int(height * 0.7)),
            (int(width * 0.40), int(height * 0.2), int(width * 0.60), int(height * 0.7)),
            (int(width * 0.65), int(height * 0.2), int(width * 0.85), int(height * 0.7)),
        ]

        for idx, (x1, y1, x2, y2) in enumerate(grid, start=1):
            # Student 2 triggers periodic side peeking
            if idx == 2 and 30 <= (frame_index % 120) <= 80:
                cls_id = 4  # side peeking
                conf = 0.88
            # Student 3 triggers phone using
            elif idx == 3 and 60 <= (frame_index % 180) <= 120:
                cls_id = 3  # phone using
                conf = 0.93
            else:
                cls_id = 2  # no cheating
                conf = 0.95

            detections.append(
                Detection(
                    class_id=cls_id,
                    class_name=self.class_names[cls_id],
                    confidence=conf,
                    bbox=(x1, y1, x2, y2),
                    frame_index=frame_index,
                )
            )

        return detections

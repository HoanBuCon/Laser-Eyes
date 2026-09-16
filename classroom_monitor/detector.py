"""YOLO Object Detection Inference Wrapper with High-Res Tiling Support (SAHI).

Handles model loading, fallback mechanisms, single-frame inference,
and dynamic high-resolution image slicing (SAHI-style tiling) to preserve
small object detail (e.g., phones at the back of large lecture halls).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from classroom_monitor.config import DEFAULT_CONFIG, ClassroomConfig
from classroom_monitor.models import Detection
from classroom_monitor.spatial_matcher import compute_bbox_iou

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
        """Run object detection on a single video frame with optional SAHI high-res tiling."""
        if frame is None or frame.size == 0:
            return []

        h, w = frame.shape[:2]

        if self._is_mock or self.model is None:
            return self._mock_detect(frame, frame_index, w, h)

        # High-Resolution Slicing (SAHI) for large frames if enabled
        if self.config.enable_sahi_tiling and (w >= self.config.sahi_min_resolution or h >= self.config.sahi_min_resolution):
            return self._detect_sliced(frame, frame_index, w, h)

        # Standard full-frame inference
        return self._detect_standard(frame, frame_index, w, h)

    def _detect_standard(
        self, frame: np.ndarray, frame_index: int, w: int, h: int
    ) -> List[Detection]:
        """Standard full-frame YOLO inference."""
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

    def _detect_sliced(
        self, frame: np.ndarray, frame_index: int, w: int, h: int
    ) -> List[Detection]:
        """Dynamic High-Resolution Image Tiling (SAHI-style) for large lecture halls."""
        slice_size = self.config.sahi_slice_size
        overlap = self.config.sahi_overlap_ratio
        step_x = int(slice_size * (1.0 - overlap))
        step_y = int(slice_size * (1.0 - overlap))

        all_candidates: List[Detection] = []

        # 1. First run global low-res frame to catch macro scale context
        global_dets = self._detect_standard(frame, frame_index, w, h)
        all_candidates.extend(global_dets)

        # 2. Iterate high-resolution grid tiles
        y_starts = list(range(0, max(1, h - slice_size + 1), step_y))
        if y_starts[-1] + slice_size < h:
            y_starts.append(h - slice_size)

        x_starts = list(range(0, max(1, w - slice_size + 1), step_x))
        if x_starts[-1] + slice_size < w:
            x_starts.append(w - slice_size)

        for y1_tile in y_starts:
            y2_tile = min(h, y1_tile + slice_size)
            for x1_tile in x_starts:
                x2_tile = min(w, x1_tile + slice_size)
                tile = frame[y1_tile:y2_tile, x1_tile:x2_tile]

                tile_dets = self._detect_standard(
                    tile, frame_index, tile.shape[1], tile.shape[0]
                )

                # Offset tile local coordinates to global image frame
                for td in tile_dets:
                    gx1 = td.bbox[0] + x1_tile
                    gy1 = td.bbox[1] + y1_tile
                    gx2 = td.bbox[2] + x1_tile
                    gy2 = td.bbox[3] + y1_tile
                    all_candidates.append(
                        Detection(
                            class_id=td.class_id,
                            class_name=td.class_name,
                            confidence=td.confidence,
                            bbox=(gx1, gy1, gx2, gy2),
                            frame_index=frame_index,
                        )
                    )

        # 3. Global Non-Maximum Suppression (NMS) to merge overlapping tiles
        return self._apply_nms(all_candidates, self.config.nms_iou_threshold)

    def _apply_nms(
        self, detections: List[Detection], iou_thresh: float
    ) -> List[Detection]:
        """Merge duplicated detections across tile boundaries using NMS."""
        if not detections:
            return []

        # Sort by confidence descending
        sorted_dets = sorted(detections, key=lambda d: d.confidence, reverse=True)
        keep: List[Detection] = []

        for det in sorted_dets:
            overlap = False
            for kept in keep:
                # Same class IoU suppression
                if det.class_name == kept.class_name:
                    iou = compute_bbox_iou(det.bbox, kept.bbox)
                    if iou > iou_thresh:
                        overlap = True
                        break
            if not overlap:
                keep.append(det)

        return keep

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
        # Grid of 3 simulated students
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


def calculate_head_pose_yaw_pitch(keypoints: np.ndarray) -> Tuple[float, float]:
    """Calculate approximate 3D Head Yaw and Pitch angles from 2D facial keypoints.

    Keypoints mapping (COCO format):
    0: Nose, 1: Left Eye, 2: Right Eye, 3: Left Ear, 4: Right Ear, 5: Left Shoulder, 6: Right Shoulder.
    """
    try:
        nose = keypoints[0][:2]
        l_eye = keypoints[1][:2]
        r_eye = keypoints[2][:2]
        l_ear = keypoints[3][:2]
        r_ear = keypoints[4][:2]
        ls = keypoints[5][:2]
        rs = keypoints[6][:2]

        # Calculate Head Yaw (horizontal rotation)
        if l_ear[0] > 0 and r_ear[0] > 0:
            ear_mid_x = (l_ear[0] + r_ear[0]) / 2.0
            ear_dist = max(1.0, float(np.linalg.norm(l_ear - r_ear)))
            yaw_ratio = (nose[0] - ear_mid_x) / (ear_dist / 2.0)
            yaw = float(np.clip(yaw_ratio * 45.0, -90.0, 90.0))
        elif l_eye[0] > 0 and r_eye[0] > 0:
            eye_mid_x = (l_eye[0] + r_eye[0]) / 2.0
            eye_dist = max(1.0, float(np.linalg.norm(l_eye - r_eye)))
            yaw_ratio = (nose[0] - eye_mid_x) / (eye_dist / 2.0)
            yaw = float(np.clip(yaw_ratio * 45.0, -90.0, 90.0))
        else:
            yaw = 0.0

        # Calculate Head Pitch (vertical tilt: positive = looking down deeply into desk/lap)
        if ls[1] > 0 and rs[1] > 0 and nose[1] > 0:
            shoulder_mid_y = (ls[1] + rs[1]) / 2.0
            shoulder_width = max(1.0, float(np.linalg.norm(ls - rs)))
            nose_to_shoulder_ratio = (shoulder_mid_y - nose[1]) / shoulder_width
            pitch = float(np.clip((0.65 - nose_to_shoulder_ratio) * 60.0, -45.0, 60.0))
        elif (l_ear[1] > 0 or r_ear[1] > 0) and nose[1] > 0:
            ref_ear_y = l_ear[1] if l_ear[1] > 0 else r_ear[1]
            pitch = float(np.clip((nose[1] - ref_ear_y) * 1.5, -45.0, 60.0))
        else:
            pitch = 0.0

        return yaw, pitch
    except Exception:
        return 0.0, 0.0


def check_phone_posture_multicue(
    keypoints: np.ndarray,
    pitch: float,
    wrist_ratio_threshold: float = 0.28,
    pitch_threshold: float = 20.0,
) -> bool:
    """Multi-Cue posture fusion to distinguish normal exam writing from clandestine phone usage."""
    try:
        lw = keypoints[9][:2]   # Left wrist
        rw = keypoints[10][:2]  # Right wrist
        ls = keypoints[5][:2]   # Left shoulder
        rs = keypoints[6][:2]   # Right shoulder

        if lw[0] == 0 or rw[0] == 0 or ls[0] == 0 or rs[0] == 0:
            return False

        hand_dist = float(np.linalg.norm(lw - rw))
        shoulder_width = max(1.0, float(np.linalg.norm(ls - rs)))
        shoulder_y = (ls[1] + rs[1]) / 2.0

        # Cue 1: Hands clustered together
        is_hands_close = (hand_dist / shoulder_width < wrist_ratio_threshold) or (hand_dist < 42.0)

        # Cue 2: Hands held down low in lap/under-desk
        is_hands_low = (lw[1] > shoulder_y + 35.0) and (rw[1] > shoulder_y + 35.0)

        # Cue 3: Head pitched down deeply towards lap
        is_looking_down_deep = pitch >= pitch_threshold

        return is_hands_close and is_hands_low and is_looking_down_deep
    except Exception:
        return False


class PoseClassroomDetector:
    """Two-Stage High-Throughput Pose & Behavior Detector using Single-Pass YOLO-Pose."""

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
            else self.config.pose_confidence_threshold
        )
        self.model_path = Path(model_path or self.config.pose_model_path)
        self.class_names = self.config.class_names
        self.model = None
        self._is_mock = False

        self._initialize_model()

    def _initialize_model(self) -> None:
        try:
            from ultralytics import YOLO
        except ImportError:
            logger.warning("Ultralytics library is not installed. Operating in mock mode.")
            self._is_mock = True
            return

        if self.model_path.exists() or str(self.model_path).endswith(".pt"):
            logger.info("Loading YOLO-Pose weights: %s", self.model_path)
            try:
                self.model = YOLO(str(self.model_path))
                return
            except Exception as exc:
                logger.warning("Could not load pose model %s: %s", self.model_path, exc)

        logger.warning("Pose weights not found. Operating in mock mode.")
        self._is_mock = True

    def detect(self, frame: np.ndarray, frame_index: int = 0) -> List[Detection]:
        if frame is None or frame.size == 0:
            return []

        h, w = frame.shape[:2]

        if self._is_mock or self.model is None:
            return self._mock_detect(frame, frame_index, w, h)

        try:
            results = self.model(
                frame,
                classes=[0],  # Person class
                conf=self.confidence,
                imgsz=self.config.pose_input_resolution,
                verbose=False,
            )[0]
        except Exception as exc:
            logger.error("Pose inference exception: %s", exc)
            return []

        if results.boxes is None or len(results.boxes) == 0:
            return []

        raw_boxes = results.boxes.xyxy.cpu().numpy()
        raw_confs = results.boxes.conf.cpu().numpy()
        raw_kps = (
            results.keypoints.data.cpu().numpy()
            if results.keypoints is not None
            else np.zeros((len(raw_boxes), 17, 3))
        )

        detections: List[Detection] = []
        for i, box in enumerate(raw_boxes):
            x1 = max(0, min(w - 1, int(box[0])))
            y1 = max(0, min(h - 1, int(box[1])))
            x2 = max(0, min(w, int(box[2])))
            y2 = max(0, min(h, int(box[3])))
            if (x2 - x1) < 10 or (y2 - y1) < 10:
                continue

            conf = float(raw_confs[i])
            kp = raw_kps[i] if i < len(raw_kps) else np.zeros((17, 3))

            yaw, pitch = calculate_head_pose_yaw_pitch(kp)
            is_phone = check_phone_posture_multicue(
                kp,
                pitch,
                wrist_ratio_threshold=self.config.phone_wrist_ratio_threshold,
                pitch_threshold=self.config.phone_pitch_threshold,
            )

            # Map to behavioral classes
            if abs(yaw) >= self.config.side_peeking_yaw_threshold:
                cls_name = "side peeking"
                cls_id = 4
            elif is_phone:
                cls_name = "phone using"
                cls_id = 3
            else:
                cls_name = "no cheating"
                cls_id = 2

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
        return [
            d for d in self.detect(frame, frame_index)
            if d.class_name in self.config.cheating_classes
        ]

    def _mock_detect(
        self, frame: np.ndarray, frame_index: int, width: int, height: int
    ) -> List[Detection]:
        detections = []
        grid = [
            (int(width * 0.15), int(height * 0.2), int(width * 0.35), int(height * 0.7)),
            (int(width * 0.40), int(height * 0.2), int(width * 0.60), int(height * 0.7)),
            (int(width * 0.65), int(height * 0.2), int(width * 0.85), int(height * 0.7)),
        ]

        for idx, (x1, y1, x2, y2) in enumerate(grid, start=1):
            if idx == 2 and 30 <= (frame_index % 120) <= 80:
                cls_id = 4  # side peeking
                conf = 0.88
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


def create_detector(
    config: Optional[ClassroomConfig] = None,
    model_path: Optional[str | Path] = None,
) -> ClassroomDetector | PoseClassroomDetector:
    """Factory function to create either 1-Stage YOLO or 2-Stage Pose detector based on config."""
    cfg = config or DEFAULT_CONFIG
    if cfg.pipeline_mode == "2stage_pose":
        return PoseClassroomDetector(model_path=model_path, config=cfg)
    return ClassroomDetector(model_path=model_path, config=cfg)


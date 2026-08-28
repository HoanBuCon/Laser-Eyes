from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

from .models import AnalysisResult


MODEL_PATH = Path(__file__).resolve().parent / "assets" / "face_landmarker.task"
OBJECT_MODEL_PATH = Path(__file__).resolve().parent / "assets" / "efficientdet_lite0.tflite"


class GazeAnalyzer:
    """Local face-landmark, head-pose and relative-gaze analyzer.

    The output is intentionally relative and calibrated per candidate. It is a
    risk signal for a reviewer, not an identity or cheating classifier.
    """

    def __init__(
        self,
        model_path: Path | None = None,
        max_faces: int = 5,
        object_model_path: Path | None = None,
    ):
        self.model_path = Path(model_path or MODEL_PATH)
        self.object_model_path = Path(object_model_path or OBJECT_MODEL_PATH)
        self.max_faces = max_faces
        self.backend_name = "OpenCV fallback"
        self._landmarker = None
        self._object_detector = None
        self._timestamp_ms = 0
        self._last_wall_time = time.monotonic()
        self._smooth_x = 0.0
        self._smooth_y = 0.0
        self._baseline_x = 0.0
        self._baseline_y = 0.0
        self._calibration_samples: list[tuple[float, float]] = []
        self._calibration_target = 36
        self._calibrated = False
        self._recent_confidence: deque[float] = deque(maxlen=20)
        self._mouth_history: deque[tuple[float, float]] = deque(maxlen=90)
        self._talking_score = 0.0
        self._last_valid_result: AnalysisResult | None = None
        self._last_face_seen_at = -1e9
        self._tracking_hold_seconds = 0.8
        self._frame_index = 0
        self._last_object_detections: list[
            tuple[str, float, tuple[int, int, int, int]]
        ] = []
        self._last_objects_at = -1e9
        self._haar = cv2.CascadeClassifier(
            str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
        )
        self._init_mediapipe()

    def _init_mediapipe(self) -> None:
        if not self.model_path.exists():
            return
        try:
            import mediapipe as mp
            from mediapipe.tasks.python import BaseOptions
            from mediapipe.tasks.python.vision import FaceLandmarker, FaceLandmarkerOptions, RunningMode

            options = FaceLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=str(self.model_path)),
                running_mode=RunningMode.VIDEO,
                num_faces=self.max_faces,
                min_face_detection_confidence=0.32,
                min_face_presence_confidence=0.32,
                min_tracking_confidence=0.35,
                output_face_blendshapes=False,
                output_facial_transformation_matrixes=False,
            )
            self._mp = mp
            self._landmarker = FaceLandmarker.create_from_options(options)
            self.backend_name = "MediaPipe Face Landmarker"
            self._init_object_detector(BaseOptions, RunningMode)
        except Exception:
            self._landmarker = None

    def _init_object_detector(self, base_options_cls, running_mode_cls) -> None:
        if not self.object_model_path.exists():
            return
        try:
            from mediapipe.tasks.python.vision import ObjectDetector, ObjectDetectorOptions

            options = ObjectDetectorOptions(
                base_options=base_options_cls(model_asset_path=str(self.object_model_path)),
                running_mode=running_mode_cls.IMAGE,
                max_results=8,
                score_threshold=0.32,
                category_allowlist=["person", "cell phone", "book"],
            )
            self._object_detector = ObjectDetector.create_from_options(options)
            self.backend_name += " + EfficientDet"
        except Exception:
            self._object_detector = None

    @property
    def calibrated(self) -> bool:
        return self._calibrated

    @property
    def calibration_progress(self) -> float:
        if self._calibrated:
            return 1.0
        return min(1.0, len(self._calibration_samples) / self._calibration_target)

    def begin_calibration(self) -> None:
        self._calibration_samples.clear()
        self._baseline_x = 0.0
        self._baseline_y = 0.0
        self._smooth_x = 0.0
        self._smooth_y = 0.0
        self._calibrated = False
        self._recent_confidence.clear()
        self._mouth_history.clear()
        self._talking_score = 0.0
        self._last_valid_result = None
        self._last_face_seen_at = -1e9
        self._last_object_detections.clear()
        self._last_objects_at = -1e9
        self._frame_index = 0

    def reset_calibration(self) -> None:
        self._calibration_samples.clear()
        self._baseline_x = 0.0
        self._baseline_y = 0.0
        self._calibrated = False

    def analyze(self, frame_bgr: np.ndarray, timestamp: float | None = None) -> AnalysisResult:
        timestamp = time.monotonic() if timestamp is None else timestamp
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        brightness = float(gray.mean())
        if self._landmarker is None:
            return self._analyze_fallback(gray, timestamp, brightness)

        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        mp_image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        h, w = frame_bgr.shape[:2]
        object_detections = self._detect_objects(mp_image, timestamp)
        person_count = sum(label == "person" for label, _, _ in object_detections)
        suspicious_objects = sorted(
            {label for label, _, _ in object_detections if label in {"cell phone", "book"}}
        )
        now = time.monotonic()
        delta_ms = max(1, int((now - self._last_wall_time) * 1000))
        self._timestamp_ms += delta_ms
        self._last_wall_time = now
        detection = self._landmarker.detect_for_video(mp_image, self._timestamp_ms)
        faces = detection.face_landmarks
        if not faces:
            if self._last_valid_result and timestamp - self._last_face_seen_at <= self._tracking_hold_seconds:
                held = replace(
                    self._last_valid_result,
                    timestamp=timestamp,
                    confidence=max(0.25, self._last_valid_result.confidence * 0.78),
                    brightness=brightness,
                    person_count=person_count,
                    suspicious_objects=suspicious_objects,
                    object_detections=object_detections,
                    is_talking=False,
                    talking_score=self._talking_score * 0.7,
                    tracking_held=True,
                    status_text="Đang bắt lại khuôn mặt…",
                )
                return held
            self._prune_mouth_history(timestamp, reset_after=0.9)
            return AnalysisResult(
                timestamp=timestamp,
                face_count=0,
                person_count=person_count,
                suspicious_objects=suspicious_objects,
                object_detections=object_detections,
                brightness=brightness,
                calibrated=self._calibrated,
                status_text="Không thấy khuôn mặt",
            )

        primary = max(faces, key=lambda face: self._face_area(face))
        face_box = self._face_box(primary, w, h)
        face_boxes = [self._face_box(face, w, h) for face in faces]
        yaw, pitch, roll = self._head_pose(primary, w, h)
        iris_x, iris_y, eye_confidence = self._iris_position(primary)

        # Eye movement and head movement are intentionally kept separate. A
        # candidate can look sideways without turning their head, or turn the
        # whole head while the irises stay near the eye center.
        self._maybe_collect_calibration(iris_x, iris_y, yaw, pitch)
        centered_x = iris_x - self._baseline_x
        centered_y = iris_y - self._baseline_y
        self._smooth_x = self._smooth_x * 0.72 + centered_x * 0.28
        self._smooth_y = self._smooth_y * 0.72 + centered_y * 0.28

        confidence = float(np.clip(eye_confidence * (0.95 if len(faces) == 1 else 0.8), 0.0, 1.0))
        self._recent_confidence.append(confidence)
        eye_direction = self._eye_direction(self._smooth_x, self._smooth_y)
        eye_zone_score = self._eye_zone_score(self._smooth_x, self._smooth_y)
        eyes_outside_zone = (
            self._calibrated
            and len(faces) == 1
            and eye_direction not in {"center", "unknown"}
        )
        head_direction = self._head_direction(yaw, pitch)
        direction = head_direction if head_direction != "center" else eye_direction
        mouth_openness = self._mouth_openness(primary)
        talking_score, is_talking = self._update_talking(
            mouth_openness,
            timestamp,
            enabled=len(faces) == 1,
        )
        status = self._status_text(
            direction,
            len(faces),
            person_count,
            brightness,
            is_talking,
            suspicious_objects,
            eye_direction,
            head_direction,
            eyes_outside_zone,
        )
        points = [
            (float(primary[i].x), float(primary[i].y))
            for i in self._overlay_indices()
            if i < len(primary)
        ]
        display_gaze_x = self._smooth_x + np.clip(yaw / 45.0, -1.0, 1.0) * 0.55
        display_gaze_y = self._smooth_y + np.clip(pitch / 35.0, -1.0, 1.0) * 0.48
        result = AnalysisResult(
            timestamp=timestamp,
            face_count=len(faces),
            direction=direction,
            eye_direction=eye_direction,
            head_direction=head_direction,
            eye_gaze_x=float(np.clip(self._smooth_x, -1.2, 1.2)),
            eye_gaze_y=float(np.clip(self._smooth_y, -1.2, 1.2)),
            eye_zone_score=eye_zone_score,
            eyes_outside_zone=eyes_outside_zone,
            gaze_x=float(np.clip(display_gaze_x, -1.2, 1.2)),
            gaze_y=float(np.clip(display_gaze_y, -1.2, 1.2)),
            yaw=float(yaw),
            pitch=float(pitch),
            roll=float(roll),
            confidence=confidence,
            brightness=brightness,
            mouth_openness=mouth_openness,
            talking_score=talking_score,
            is_talking=is_talking,
            person_count=person_count,
            suspicious_objects=suspicious_objects,
            object_detections=object_detections,
            calibrated=self._calibrated,
            landmarks=points,
            face_box=face_box,
            face_boxes=face_boxes,
            status_text=status,
        )
        self._last_face_seen_at = timestamp
        self._last_valid_result = result
        return result

    def _analyze_fallback(self, gray: np.ndarray, timestamp: float, brightness: float) -> AnalysisResult:
        faces = self._haar.detectMultiScale(gray, scaleFactor=1.13, minNeighbors=5, minSize=(90, 90))
        face_box = None
        if len(faces):
            x, y, w, h = max(faces, key=lambda box: box[2] * box[3])
            face_box = (int(x), int(y), int(w), int(h))
        return AnalysisResult(
            timestamp=timestamp,
            face_count=len(faces),
            direction="center" if len(faces) == 1 else "unknown",
            eye_direction="unknown",
            head_direction="center" if len(faces) == 1 else "unknown",
            confidence=0.55 if len(faces) == 1 else 0.0,
            brightness=brightness,
            calibrated=False,
            face_box=face_box,
            status_text="Đang dùng bộ phát hiện dự phòng" if len(faces) else "Không thấy khuôn mặt",
        )

    def _maybe_collect_calibration(
        self,
        raw_x: float,
        raw_y: float,
        yaw: float,
        pitch: float,
    ) -> None:
        if self._calibrated:
            return
        # Never learn a turned pose as the candidate's neutral direction.
        if abs(raw_x) < 0.9 and abs(raw_y) < 0.9 and abs(yaw) < 13 and abs(pitch) < 11:
            self._calibration_samples.append((raw_x, raw_y))
        if len(self._calibration_samples) >= self._calibration_target:
            samples = np.asarray(self._calibration_samples[-self._calibration_target :], dtype=np.float32)
            self._baseline_x, self._baseline_y = np.median(samples, axis=0).tolist()
            self._smooth_x = 0.0
            self._smooth_y = 0.0
            self._calibrated = True

    @staticmethod
    def _face_area(face) -> float:
        xs = [item.x for item in face]
        ys = [item.y for item in face]
        return (max(xs) - min(xs)) * (max(ys) - min(ys))

    @staticmethod
    def _face_box(face, width: int, height: int) -> tuple[int, int, int, int]:
        xs = [int(item.x * width) for item in face]
        ys = [int(item.y * height) for item in face]
        x1, y1 = max(0, min(xs)), max(0, min(ys))
        x2, y2 = min(width - 1, max(xs)), min(height - 1, max(ys))
        pad_x, pad_y = int((x2 - x1) * 0.08), int((y2 - y1) * 0.08)
        return (
            max(0, x1 - pad_x),
            max(0, y1 - pad_y),
            min(width - x1, x2 - x1 + pad_x * 2),
            min(height - y1, y2 - y1 + pad_y * 2),
        )

    @staticmethod
    def _point(face, index: int) -> np.ndarray:
        return np.array([face[index].x, face[index].y], dtype=np.float32)

    def _iris_position(self, face) -> tuple[float, float, float]:
        if len(face) < 478:
            return 0.0, 0.0, 0.45

        def ratio(center_index: int, a_index: int, b_index: int) -> float:
            center = self._point(face, center_index)
            a = self._point(face, a_index)
            b = self._point(face, b_index)
            axis = b - a
            denom = float(np.dot(axis, axis))
            if denom < 1e-7:
                return 0.5
            return float(np.dot(center - a, axis) / denom)

        left_x = ratio(468, 33, 133)
        right_x = ratio(473, 362, 263)
        left_y = ratio(468, 159, 145)
        right_y = ratio(473, 386, 374)
        # Real sources are mirrored before analysis, so increasing x follows
        # the candidate's own right side. Do not invert this a second time.
        gaze_x = ((left_x + right_x) * 0.5 - 0.5) * 2.8
        gaze_y = ((left_y + right_y) * 0.5 - 0.5) * 2.6
        symmetry = 1.0 - min(1.0, abs(left_x - right_x) + abs(left_y - right_y))
        return float(gaze_x), float(gaze_y), float(0.55 + symmetry * 0.4)

    def _head_pose(self, face, width: int, height: int) -> tuple[float, float, float]:
        indices = [1, 152, 33, 263, 61, 291]
        image_points = np.array(
            [[face[i].x * width, face[i].y * height] for i in indices], dtype=np.float64
        )
        model_points = np.array(
            [
                (0.0, 0.0, 0.0),
                (0.0, -63.6, -12.5),
                (-43.3, 32.7, -26.0),
                (43.3, 32.7, -26.0),
                (-28.9, -28.9, -24.1),
                (28.9, -28.9, -24.1),
            ],
            dtype=np.float64,
        )
        focal = float(width)
        camera_matrix = np.array(
            [[focal, 0, width / 2], [0, focal, height / 2], [0, 0, 1]], dtype=np.float64
        )
        success, rotation_vec, _ = cv2.solvePnP(
            model_points,
            image_points,
            camera_matrix,
            np.zeros((4, 1), dtype=np.float64),
            flags=cv2.SOLVEPNP_ITERATIVE,
        )
        if not success:
            return 0.0, 0.0, 0.0
        rotation_matrix, _ = cv2.Rodrigues(rotation_vec)
        angles = cv2.RQDecomp3x3(rotation_matrix)[0]
        pitch, yaw, roll = (self._wrap_angle(float(value)) for value in angles)
        # solvePnP returns yaw in the mirrored image coordinate system. Keep
        # labels and the gaze arrow candidate-centric by reversing yaw here.
        return -yaw, pitch, roll

    @staticmethod
    def _mouth_openness(face) -> float:
        if len(face) <= 308:
            return 0.0
        upper = np.array([face[13].x, face[13].y], dtype=np.float32)
        lower = np.array([face[14].x, face[14].y], dtype=np.float32)
        left = np.array([face[78].x, face[78].y], dtype=np.float32)
        right = np.array([face[308].x, face[308].y], dtype=np.float32)
        width = float(np.linalg.norm(right - left))
        if width < 1e-6:
            return 0.0
        return float(np.clip(np.linalg.norm(lower - upper) / width, 0.0, 1.2))

    def _update_talking(
        self,
        mouth_openness: float,
        timestamp: float,
        enabled: bool = True,
    ) -> tuple[float, bool]:
        if not enabled:
            self._mouth_history.clear()
            self._talking_score *= 0.7
            return self._talking_score, False
        self._mouth_history.append((timestamp, mouth_openness))
        self._prune_mouth_history(timestamp)
        if len(self._mouth_history) < 10:
            return self._talking_score, False
        values = np.asarray([value for _, value in self._mouth_history], dtype=np.float32)
        times = np.asarray([item[0] for item in self._mouth_history], dtype=np.float32)
        duration = float(times[-1] - times[0])
        if duration < 0.75:
            return self._talking_score, False

        # Speech produces repeated open/close articulation. A static open mouth
        # or a yawn normally has at most one cycle and must not trigger this.
        if len(values) >= 5:
            padded = np.pad(values, (1, 1), mode="edge")
            smoothed = np.convolve(
                padded,
                np.asarray([0.2, 0.6, 0.2]),
                mode="valid",
            )
        else:
            smoothed = values
        low_value = float(np.percentile(smoothed, 15))
        high_value = float(np.percentile(smoothed, 85))
        span = high_value - low_value
        raw_low = float(np.percentile(values, 15))
        raw_high = float(np.percentile(values, 85))
        raw_span = raw_high - raw_low
        open_fraction = float(np.mean(values >= 0.075))
        low_gate = low_value + span * 0.34
        high_gate = low_value + span * 0.66
        def rhythm_counts(samples: np.ndarray) -> tuple[int, int]:
            states: list[int] = []
            for value in samples:
                state = -1 if value <= low_gate else (1 if value >= high_gate else 0)
                if state and (not states or state != states[-1]):
                    states.append(state)
            close_cycles = sum(
                previous == 1 and current == -1
                for previous, current in zip(states, states[1:])
            )
            return close_cycles, max(0, len(states) - 1)

        close_cycles, transitions = rhythm_counts(smoothed)
        motion = float(np.mean(np.abs(np.diff(smoothed))))
        recent = smoothed[times >= times[-1] - 0.75]
        recent_span = (
            float(np.percentile(recent, 90) - np.percentile(recent, 10))
            if len(recent) >= 5
            else 0.0
        )
        recent_cycles, recent_transitions = rhythm_counts(recent)
        has_lip_rhythm = (
            raw_high >= 0.075
            and raw_span >= 0.030
            and open_fraction >= 0.12
            and span >= 0.018
            and motion >= 0.005
            and close_cycles >= 1
            and transitions >= 2
            and recent_span >= 0.018
            and recent_cycles >= 1
            and recent_transitions >= 2
        )
        strict_visual_speech = (
            has_lip_rhythm
            and raw_span >= 0.045
            and open_fraction >= 0.22
            and close_cycles >= 3
            and transitions >= 6
        )
        if has_lip_rhythm:
            cycle_score = float(np.clip(close_cycles / 4.0, 0.0, 1.0))
            motion_score = float(np.clip(motion / 0.020, 0.0, 1.0))
            raw_score = 0.62 + cycle_score * 0.23 + motion_score * 0.15
            self._talking_score = self._talking_score * 0.52 + raw_score * 0.48
        else:
            # Hold recent articulation briefly so natural pauses between words
            # do not break the debounced audio+lip confirmation.
            self._talking_score *= 0.93
        is_talking = strict_visual_speech and self._talking_score >= 0.66
        return self._talking_score, is_talking

    def _prune_mouth_history(self, timestamp: float, reset_after: float | None = None) -> None:
        while self._mouth_history and timestamp - self._mouth_history[0][0] > 2.0:
            self._mouth_history.popleft()
        if reset_after is not None and self._mouth_history:
            if timestamp - self._mouth_history[-1][0] > reset_after:
                self._mouth_history.clear()
                self._talking_score = 0.0

    def _detect_objects(
        self,
        mp_image,
        timestamp: float,
    ) -> list[tuple[str, float, tuple[int, int, int, int]]]:
        self._frame_index += 1
        if self._object_detector is None:
            return []
        if self._frame_index % 8 == 1:
            try:
                detected = self._object_detector.detect(mp_image)
                objects: list[tuple[str, float, tuple[int, int, int, int]]] = []
                for detection in detected.detections:
                    if not detection.categories:
                        continue
                    category = detection.categories[0]
                    label = (category.category_name or "").lower()
                    box = detection.bounding_box
                    objects.append(
                        (
                            label,
                            float(category.score or 0.0),
                            (int(box.origin_x), int(box.origin_y), int(box.width), int(box.height)),
                        )
                    )
                self._last_object_detections = objects
                self._last_objects_at = timestamp
            except Exception:
                self._last_object_detections = []
        if timestamp - self._last_objects_at > 1.4:
            return []
        return list(self._last_object_detections)

    @staticmethod
    def _wrap_angle(value: float) -> float:
        while value > 90:
            value -= 180
        while value < -90:
            value += 180
        return value

    @staticmethod
    def _eye_direction(gaze_x: float, gaze_y: float) -> str:
        if GazeAnalyzer._eye_zone_score(gaze_x, gaze_y) <= 1.0:
            return "center"
        if abs(gaze_x) / 0.30 >= abs(gaze_y) / 0.34:
            return "right" if gaze_x > 0 else "left"
        return "down" if gaze_y > 0 else "up"

    @staticmethod
    def _eye_zone_score(gaze_x: float, gaze_y: float) -> float:
        """Distance from the calibrated safe ellipse; values above 1 are outside."""
        return float(math.sqrt((gaze_x / 0.30) ** 2 + (gaze_y / 0.34) ** 2))

    @staticmethod
    def _head_direction(yaw: float, pitch: float) -> str:
        if abs(yaw) < 15 and abs(pitch) < 13:
            return "center"
        if abs(yaw) >= abs(pitch) * 1.05:
            return "right" if yaw > 0 else "left"
        return "down" if pitch > 0 else "up"

    @staticmethod
    def _status_text(
        direction: str,
        face_count: int,
        person_count: int,
        brightness: float,
        is_talking: bool,
        suspicious_objects: list[str],
        eye_direction: str = "unknown",
        head_direction: str = "unknown",
        eyes_outside_zone: bool = False,
    ) -> str:
        detected_people = face_count if face_count >= 2 else max(face_count, person_count)
        if detected_people >= 2:
            return f"Phát hiện {detected_people} người"
        if suspicious_objects:
            return "Phát hiện điện thoại hoặc tài liệu"
        if is_talking:
            return "Nghi ngờ đang nói chuyện"
        if brightness < 42:
            return "Ánh sáng chưa đạt"
        labels = {"left": "trái", "right": "phải", "up": "trên", "down": "dưới"}
        if eyes_outside_zone:
            return f"Mắt ngoài vùng thi • {labels.get(eye_direction, eye_direction)}"
        if head_direction not in {"center", "unknown"}:
            return f"Đầu lệch khỏi màn hình • {labels.get(head_direction, head_direction)}"
        if direction == "center":
            return "Trong vùng an toàn"
        return f"Nhìn lệch {labels.get(direction, direction)}"

    @staticmethod
    def _overlay_indices() -> tuple[int, ...]:
        return (
            10, 33, 61, 93, 133, 152, 159, 145, 234, 263, 291, 323, 362,
            374, 386, 454, 468, 469, 470, 471, 472, 473, 474, 475, 476, 477,
        )

    def close(self) -> None:
        if self._landmarker is not None:
            self._landmarker.close()
            self._landmarker = None
        if self._object_detector is not None:
            self._object_detector.close()
            self._object_detector = None


class OverlayRenderer:
    SAFE = (95, 255, 215)
    WARNING = (93, 201, 244)
    DANGER = (107, 107, 255)
    WHITE = (245, 247, 250)
    MUTED = (150, 150, 160)

    @classmethod
    def draw(cls, frame: np.ndarray, result: AnalysisResult, show_landmarks: bool = True) -> np.ndarray:
        canvas = frame.copy()
        h, w = canvas.shape[:2]
        cls._safe_zone(canvas)
        face_boxes = result.face_boxes or ([result.face_box] if result.face_box else [])
        for index, box in enumerate(face_boxes):
            x, y, bw, bh = box
            attentive = (
                not result.eyes_outside_zone
                and result.head_direction in {"center", "unknown"}
            )
            color = cls.SAFE if attentive else cls.WARNING
            if len(face_boxes) > 1:
                color = cls.DANGER
            cls._corner_box(canvas, x, y, bw, bh, color)
            if len(face_boxes) > 1:
                cv2.putText(
                    canvas,
                    f"FACE {index + 1}",
                    (x, max(18, y - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.43,
                    color,
                    1,
                    cv2.LINE_AA,
                )
        cls._object_boxes(canvas, result)
        if show_landmarks:
            for nx, ny in result.landmarks:
                cv2.circle(canvas, (int(nx * w), int(ny * h)), 2, cls.WHITE, -1, cv2.LINE_AA)
        if result.face_count == 1:
            cls._gaze_vector(canvas, result)
        cls._status_chip(canvas, result)
        return canvas

    @classmethod
    def _object_boxes(cls, frame: np.ndarray, result: AnalysisResult) -> None:
        labels = {"cell phone": "PHONE", "book": "BOOK", "person": "PERSON"}
        person_index = 0
        for label, confidence, box in result.object_detections:
            if label == "person":
                person_index += 1
                if result.person_count < 2:
                    continue
            x, y, w, h = box
            color = cls.DANGER if label in {"cell phone", "book"} or result.person_count >= 2 else cls.WARNING
            cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2, cv2.LINE_AA)
            name = labels.get(label, label.upper())
            if label == "person":
                name = f"PERSON {person_index}"
            text = f"{name} {confidence * 100:.0f}%"
            cv2.putText(
                frame,
                text,
                (x, max(18, y - 7)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.44,
                color,
                1,
                cv2.LINE_AA,
            )

    @classmethod
    def _safe_zone(cls, frame: np.ndarray) -> None:
        h, w = frame.shape[:2]
        x1, x2 = int(w * 0.22), int(w * 0.78)
        y1, y2 = int(h * 0.16), int(h * 0.84)
        color = (70, 74, 82)
        length = max(18, int(min(w, h) * 0.035))
        for x, y, sx, sy in (
            (x1, y1, 1, 1), (x2, y1, -1, 1), (x1, y2, 1, -1), (x2, y2, -1, -1)
        ):
            cv2.line(frame, (x, y), (x + sx * length, y), color, 1, cv2.LINE_AA)
            cv2.line(frame, (x, y), (x, y + sy * length), color, 1, cv2.LINE_AA)

    @staticmethod
    def _corner_box(frame: np.ndarray, x: int, y: int, w: int, h: int, color) -> None:
        length = max(18, int(min(w, h) * 0.18))
        thickness = 2
        for px, py, sx, sy in (
            (x, y, 1, 1), (x + w, y, -1, 1), (x, y + h, 1, -1), (x + w, y + h, -1, -1)
        ):
            cv2.line(frame, (px, py), (px + sx * length, py), color, thickness, cv2.LINE_AA)
            cv2.line(frame, (px, py), (px, py + sy * length), color, thickness, cv2.LINE_AA)

    @classmethod
    def _gaze_vector(cls, frame: np.ndarray, result: AnalysisResult) -> None:
        h, w = frame.shape[:2]
        if result.face_box:
            x, y, bw, bh = result.face_box
            origin = (x + bw // 2, y + int(bh * 0.39))
        else:
            origin = (w // 2, h // 2)
        scale = max(90, int(min(w, h) * 0.20))
        safe_radius = (max(24, int(scale * 0.30)), max(20, int(scale * 0.34)))
        cv2.ellipse(frame, origin, safe_radius, 0, 0, 360, (90, 94, 102), 1, cv2.LINE_AA)
        target = (
            int(origin[0] + result.eye_gaze_x * scale),
            int(origin[1] + result.eye_gaze_y * scale),
        )
        color = cls.WARNING if result.eyes_outside_zone else cls.SAFE
        if abs(result.eye_gaze_x) + abs(result.eye_gaze_y) < 0.05:
            cv2.circle(frame, origin, 6, color, 2, cv2.LINE_AA)
            cv2.circle(frame, origin, 2, color, -1, cv2.LINE_AA)
            return
        cv2.arrowedLine(frame, origin, target, (20, 22, 26), 6, cv2.LINE_AA, tipLength=0.24)
        cv2.arrowedLine(frame, origin, target, color, 3, cv2.LINE_AA, tipLength=0.24)
        cv2.circle(frame, origin, 4, color, -1, cv2.LINE_AA)

    @classmethod
    def _status_chip(cls, frame: np.ndarray, result: AnalysisResult) -> None:
        labels = {
            "center": "GAZE CENTERED",
            "left": "LOOKING LEFT",
            "right": "LOOKING RIGHT",
            "up": "LOOKING UP",
            "down": "LOOKING DOWN",
            "unknown": "NO FACE IN FRAME",
        }
        people = result.detected_people_count
        if people >= 2:
            label = f"{people} PEOPLE DETECTED"
        elif result.suspicious_objects:
            label = "PHONE / BOOK DETECTED"
        elif result.is_talking:
            label = "TALKING DETECTED"
        elif result.face_count == 0:
            label = "NO FACE IN FRAME"
        elif result.brightness < 42:
            label = "LOW LIGHT"
        elif result.eyes_outside_zone:
            direction = labels.get(result.eye_direction, "OUTSIDE")
            label = f"EYES OUTSIDE - {direction.replace('LOOKING ', '')}"
        elif result.head_direction not in {"center", "unknown"}:
            direction = labels.get(result.head_direction, "HEAD AWAY")
            label = f"HEAD - {direction.replace('LOOKING ', '')}"
        else:
            label = "EYES INSIDE SAFE ZONE" if result.calibrated else "CALIBRATING EYE GAZE"
        if people >= 2 or result.face_count == 0 or result.suspicious_objects:
            color = cls.DANGER
        elif result.is_talking or result.eyes_outside_zone or result.head_direction not in {"center", "unknown"}:
            color = cls.WARNING
        elif result.face_count == 1:
            color = cls.SAFE
        else:
            color = cls.WARNING
        font = cv2.FONT_HERSHEY_SIMPLEX
        size = 0.52
        thickness = 1
        (tw, th), _ = cv2.getTextSize(label, font, size, thickness)
        x, y = 22, 22
        cv2.rectangle(frame, (x, y), (x + tw + 28, y + th + 22), (15, 16, 19), -1)
        cv2.rectangle(frame, (x, y), (x + 4, y + th + 22), color, -1)
        cv2.putText(frame, label, (x + 16, y + th + 10), font, size, cls.WHITE, thickness, cv2.LINE_AA)

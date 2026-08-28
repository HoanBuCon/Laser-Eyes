from __future__ import annotations

import math
import time
from pathlib import Path

import cv2
import numpy as np

from .models import AnalysisResult


def _prepare_real_frame(frame: np.ndarray, max_width: int = 960) -> np.ndarray:
    """Mirror and cap processing size for consistent directions and CPU usage."""
    frame = cv2.flip(frame, 1)
    height, width = frame.shape[:2]
    if width > max_width:
        target_height = max(1, round(height * max_width / width))
        frame = cv2.resize(frame, (max_width, target_height), interpolation=cv2.INTER_AREA)
    return frame


class FrameSource:
    label = "Nguồn hình ảnh"

    def read(self) -> tuple[bool, np.ndarray | None]:
        raise NotImplementedError

    def release(self) -> None:
        pass


class CameraSource(FrameSource):
    label = "Camera trực tiếp"

    def __init__(self, index: int = 0):
        self.capture = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if not self.capture.isOpened():
            self.capture.release()
            self.capture = cv2.VideoCapture(index)
        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    def read(self) -> tuple[bool, np.ndarray | None]:
        ok, frame = self.capture.read()
        if ok:
            frame = _prepare_real_frame(frame)
        return ok, frame

    def release(self) -> None:
        self.capture.release()


class VideoSource(FrameSource):
    label = "Video mẫu"

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.capture = cv2.VideoCapture(str(self.path))

    def read(self) -> tuple[bool, np.ndarray | None]:
        ok, frame = self.capture.read()
        if ok:
            frame = _prepare_real_frame(frame)
        if not ok:
            self.capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self.capture.read()
            if ok:
                frame = _prepare_real_frame(frame)
        return ok, frame

    def release(self) -> None:
        self.capture.release()


class DemoSource(FrameSource):
    """Deterministic source that lets the product story work without a camera."""

    label = "Mô phỏng có sẵn"

    def __init__(self, width: int = 1280, height: int = 720):
        self.width = width
        self.height = height
        self.started = time.monotonic()
        self.frame_index = 0
        self.last_result = AnalysisResult(timestamp=0)

    def read(self) -> tuple[bool, np.ndarray]:
        elapsed = time.monotonic() - self.started
        phase = int(elapsed // 6) % 7
        direction = ["center", "left", "center", "right", "center", "center", "center"][phase]
        face_count = 0 if phase == 4 and elapsed % 6 > 1.2 else (2 if phase == 5 else 1)
        is_talking = phase == 2 and elapsed % 6 > 0.8
        suspicious_objects = ["cell phone"] if phase == 6 else []
        gaze_x = {"left": -0.8, "right": 0.8}.get(direction, 0.0)
        gaze_y = 0.0

        frame = self._draw_scene(elapsed, direction, face_count, is_talking, bool(suspicious_objects))
        face_boxes = []
        if face_count >= 1:
            face_boxes.append((390, 105, 500, 500))
        if face_count >= 2:
            face_boxes.append((65, 190, 280, 360))
        object_detections = []
        if suspicious_objects:
            object_detections.append(("cell phone", 0.93, (960, 400, 125, 215)))
        self.last_result = AnalysisResult(
            timestamp=elapsed,
            face_count=face_count,
            direction=direction if face_count else "unknown",
            eye_direction=direction if face_count else "unknown",
            head_direction="center" if face_count else "unknown",
            eye_gaze_x=gaze_x,
            eye_gaze_y=gaze_y,
            eye_zone_score=abs(gaze_x) / 0.30,
            eyes_outside_zone=face_count == 1 and direction != "center",
            gaze_x=gaze_x,
            gaze_y=gaze_y,
            yaw=gaze_x * 18,
            pitch=0,
            roll=0,
            confidence=0.96 if face_count else 0.0,
            # The synthetic scene represents a technically valid camera feed;
            # keep this independent from the intentionally dark UI backdrop.
            brightness=108.0,
            mouth_openness=0.18 if is_talking else 0.035,
            talking_score=0.88 if is_talking else 0.0,
            is_talking=is_talking,
            person_count=face_count,
            suspicious_objects=suspicious_objects,
            object_detections=object_detections,
            calibrated=True,
            face_box=(390, 105, 500, 500) if face_count else None,
            face_boxes=face_boxes,
            status_text=self._demo_status(direction, face_count, is_talking, suspicious_objects),
        )
        self.frame_index += 1
        return True, frame

    @staticmethod
    def _demo_status(direction: str, face_count: int, is_talking: bool, objects: list[str]) -> str:
        if face_count == 0:
            return "Không thấy khuôn mặt"
        if face_count >= 2:
            return f"Phát hiện {face_count} người"
        if objects:
            return "Phát hiện điện thoại"
        if is_talking:
            return "Nghi ngờ đang nói chuyện"
        return "Ổn định" if direction == "center" else "Đang nhìn lệch"

    def _draw_scene(
        self,
        elapsed: float,
        direction: str,
        face_count: int,
        is_talking: bool,
        show_phone: bool,
    ) -> np.ndarray:
        h, w = self.height, self.width
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        gradient = np.linspace(22, 8, h, dtype=np.uint8)[:, None]
        frame[:, :, 0] = gradient
        frame[:, :, 1] = gradient
        frame[:, :, 2] = gradient
        cv2.rectangle(frame, (0, 0), (w - 1, h - 1), (45, 48, 54), 2)
        if not face_count:
            cv2.putText(frame, "NO FACE IN FRAME", (430, 350), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (160, 164, 171), 2)
            return frame

        bob = int(math.sin(elapsed * 1.4) * 4)
        center = (w // 2, h // 2 - 25 + bob)
        skin = (130, 132, 138)
        cv2.ellipse(frame, center, (205, 250), 0, 0, 360, skin, -1)
        cv2.ellipse(frame, (center[0], center[1] + 226), (270, 145), 0, 180, 360, (48, 51, 58), -1)
        cv2.ellipse(frame, (center[0], center[1] - 100), (190, 150), 0, 180, 360, (38, 40, 45), -1)

        eye_y = center[1] - 45
        for eye_x in (center[0] - 82, center[0] + 82):
            cv2.ellipse(frame, (eye_x, eye_y), (42, 22), 0, 0, 360, (225, 226, 228), -1)
            offset = {"left": -20, "right": 20}.get(direction, 0)
            cv2.circle(frame, (eye_x + offset, eye_y), 11, (24, 25, 28), -1)
            cv2.circle(frame, (eye_x + offset - 3, eye_y - 3), 3, (235, 235, 235), -1)
        cv2.line(frame, (center[0] - 25, center[1] + 55), (center[0], center[1] + 75), (82, 84, 90), 3)
        cv2.line(frame, (center[0], center[1] + 75), (center[0] + 25, center[1] + 55), (82, 84, 90), 3)
        cv2.ellipse(frame, (center[0], center[1] + 125), (65, 20), 0, 0, 180, (60, 62, 68), 3)
        if is_talking:
            mouth_height = 16 + int(abs(math.sin(elapsed * 8)) * 18)
            cv2.ellipse(frame, (center[0], center[1] + 128), (56, mouth_height), 0, 0, 360, (30, 31, 35), -1)
        if face_count >= 2:
            second = (205, 335)
            cv2.ellipse(frame, second, (110, 145), 0, 0, 360, (118, 120, 126), -1)
            cv2.circle(frame, (170, 310), 9, (25, 26, 29), -1)
            cv2.circle(frame, (240, 310), 9, (25, 26, 29), -1)
            cv2.ellipse(frame, (205, 390), (38, 12), 0, 0, 180, (52, 54, 59), 2)
        if show_phone:
            cv2.rectangle(frame, (960, 400), (1085, 615), (33, 35, 40), -1)
            cv2.rectangle(frame, (968, 414), (1077, 585), (90, 94, 102), -1)
            cv2.circle(frame, (1023, 601), 7, (140, 143, 150), -1)
        return frame

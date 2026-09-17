"""Head Orientation Provider Abstraction and Implementations.

Implements Scope 03 (FR-PER-003, FR-PER-004) and Scope 06 of SRS v2.0:
- Pluggable HeadOrientationProvider interface with backward-compatible signature.
- PoseHeuristicHeadOrientationProvider: Zero-shot 2D keypoint geometric baseline.
- SixDRepNetHeadOrientationProvider: Pretrained 6DRepNet 3D head pose estimator.
- HeadCropExtractor: Unknown-safe head ROI extraction with bounding box safeguards.
- Canonical angle conventions:
  * yaw < 0: LEFT
  * yaw > 0: RIGHT
  * pitch > 0: DOWN
- Seat-perspective relative yaw/pitch baseline subtraction.
"""

from __future__ import annotations

import abc
import logging
import threading
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger("HeadPoseProvider")


@dataclass
class HeadOrientationEstimate:
    """Standardized estimate of head 3D orientation and observation quality."""

    yaw: Optional[float] = None       # Degrees: negative = left, positive = right
    pitch: Optional[float] = None     # Degrees: positive = downward tilt
    roll: Optional[float] = None      # Degrees: lateral tilt
    quality: float = 0.0              # 0.0 (occluded/blurry) to 1.0 (crystal clear)
    source: str = "unknown"           # "pose_heuristic", "sixdrepnet", "unknown"

    @property
    def is_valid(self) -> bool:
        return self.yaw is not None and self.pitch is not None and self.quality > 0.20

    def to_dict(self) -> dict:
        return {
            "yaw": round(self.yaw, 2) if self.yaw is not None else None,
            "pitch": round(self.pitch, 2) if self.pitch is not None else None,
            "roll": round(self.roll, 2) if self.roll is not None else None,
            "quality": round(self.quality, 3),
            "source": self.source,
        }


class HeadOrientationProvider(abc.ABC):
    """Abstract interface for all head pose estimation providers."""

    @abc.abstractmethod
    def estimate(
        self,
        keypoints: np.ndarray,
        bbox: Optional[Tuple[float, float, float, float]] = None,
        seat_baseline_yaw: float = 0.0,
        seat_baseline_pitch: float = 0.0,
        frame: Optional[np.ndarray] = None,
    ) -> HeadOrientationEstimate:
        """Estimate 3D head yaw and pitch relative to the seat's neutral perspective."""
        pass


class PoseHeuristicHeadOrientationProvider(HeadOrientationProvider):
    """Zero-shot 2D keypoint geometric head orientation provider."""

    def __init__(self, min_kp_conf: float = 0.30, min_quality: float = 0.25):
        self.min_kp_conf = min_kp_conf
        self.min_quality = min_quality

    def estimate(
        self,
        keypoints: np.ndarray,
        bbox: Optional[Tuple[float, float, float, float]] = None,
        seat_baseline_yaw: float = 0.0,
        seat_baseline_pitch: float = 0.0,
        frame: Optional[np.ndarray] = None,
    ) -> HeadOrientationEstimate:
        if keypoints is None or len(keypoints) < 7:
            return HeadOrientationEstimate(source="unknown", quality=0.0)

        nose = keypoints[0]
        l_eye = keypoints[1]
        r_eye = keypoints[2]
        l_ear = keypoints[3]
        r_ear = keypoints[4]
        ls = keypoints[5]
        rs = keypoints[6]

        head_confs = [nose[2], l_eye[2], r_eye[2], l_ear[2], r_ear[2], ls[2], rs[2]]
        valid_confs = [c for c in head_confs if c > 0]
        quality = float(np.mean(valid_confs)) if valid_confs else 0.0

        # Quality gate: if nose is missing or head is too noisy
        if nose[2] < self.min_kp_conf or quality < self.min_quality:
            return HeadOrientationEstimate(quality=quality, source="unknown")

        raw_yaw: Optional[float] = None
        raw_pitch: Optional[float] = None

        # 1. Compute Horizontal Yaw
        if l_ear[2] >= self.min_kp_conf and r_ear[2] >= self.min_kp_conf:
            ear_mid_x = (l_ear[0] + r_ear[0]) / 2.0
            ear_dist = max(5.0, float(np.linalg.norm(l_ear[:2] - r_ear[:2])))
            yaw_ratio = (nose[0] - ear_mid_x) / (ear_dist / 2.0)
            raw_yaw = float(np.clip(yaw_ratio * 45.0, -90.0, 90.0))
        elif l_eye[2] >= self.min_kp_conf and r_eye[2] >= self.min_kp_conf:
            eye_mid_x = (l_eye[0] + r_eye[0]) / 2.0
            eye_dist = max(5.0, float(np.linalg.norm(l_eye[:2] - r_eye[:2])))
            yaw_ratio = (nose[0] - eye_mid_x) / (eye_dist / 2.0)
            raw_yaw = float(np.clip(yaw_ratio * 45.0, -90.0, 90.0))
        elif l_ear[2] >= self.min_kp_conf and nose[2] >= self.min_kp_conf:
            # Only left ear visible -> facing right
            raw_yaw = 35.0
        elif r_ear[2] >= self.min_kp_conf and nose[2] >= self.min_kp_conf:
            # Only right ear visible -> facing left
            raw_yaw = -35.0

        # 2. Compute Vertical Pitch
        if ls[2] >= self.min_kp_conf and rs[2] >= self.min_kp_conf:
            shoulder_mid_y = (ls[1] + rs[1]) / 2.0
            shoulder_width = max(10.0, float(np.linalg.norm(ls[:2] - rs[:2])))
            nose_to_shoulder_ratio = (shoulder_mid_y - nose[1]) / shoulder_width
            raw_pitch = float(np.clip((0.65 - nose_to_shoulder_ratio) * 60.0, -45.0, 60.0))
        elif (l_ear[2] >= self.min_kp_conf or r_ear[2] >= self.min_kp_conf) and nose[2] >= self.min_kp_conf:
            ref_ear_y = l_ear[1] if l_ear[2] >= self.min_kp_conf else r_ear[1]
            raw_pitch = float(np.clip((nose[1] - ref_ear_y) * 1.5, -45.0, 60.0))

        if raw_yaw is None or raw_pitch is None:
            return HeadOrientationEstimate(
                yaw=raw_yaw,
                pitch=raw_pitch,
                quality=quality,
                source="pose_heuristic_partial",
            )

        # Subtract Seat Baseline perspective offset
        relative_yaw = float(np.clip(raw_yaw - seat_baseline_yaw, -90.0, 90.0))
        relative_pitch = float(np.clip(raw_pitch - seat_baseline_pitch, -45.0, 60.0))

        return HeadOrientationEstimate(
            yaw=relative_yaw,
            pitch=relative_pitch,
            roll=0.0,
            quality=quality,
            source="pose_heuristic",
        )


class HeadCropExtractor:
    """Robust, unknown-safe head ROI crop extractor from YOLO-Pose keypoints and bbox."""

    def __init__(
        self,
        min_kp_conf: float = 0.30,
        min_crop_size: int = 16,
        padding_ratio: float = 0.45,
    ):
        self.min_kp_conf = min_kp_conf
        self.min_crop_size = min_crop_size
        self.padding_ratio = padding_ratio

    def extract_crop(
        self,
        frame: Optional[np.ndarray],
        keypoints: Optional[np.ndarray],
        bbox: Optional[Tuple[float, float, float, float]] = None,
    ) -> Tuple[Optional[np.ndarray], float]:
        """Extract a valid head crop and calculate quality metric."""
        if frame is None or frame.size == 0:
            return None, 0.0

        h_img, w_img = frame.shape[:2]
        if h_img < self.min_crop_size or w_img < self.min_crop_size:
            return None, 0.0

        valid_kps = []
        if keypoints is not None and len(keypoints) >= 5:
            # Head keypoints: nose(0), l_eye(1), r_eye(2), l_ear(3), r_ear(4)
            for i in range(min(5, len(keypoints))):
                kp = keypoints[i]
                if kp[2] >= self.min_kp_conf and 0 <= kp[0] < w_img and 0 <= kp[1] < h_img:
                    valid_kps.append(kp)

        if valid_kps:
            # Landmark-based ROI
            min_x = min(kp[0] for kp in valid_kps)
            max_x = max(kp[0] for kp in valid_kps)
            min_y = min(kp[1] for kp in valid_kps)
            max_y = max(kp[1] for kp in valid_kps)

            bw = max_x - min_x
            bh = max_y - min_y
            pad_x = max(8.0, bw * self.padding_ratio)
            pad_y = max(8.0, bh * self.padding_ratio)

            x1 = int(np.clip(min_x - pad_x, 0, w_img))
            y1 = int(np.clip(min_y - pad_y, 0, h_img))
            x2 = int(np.clip(max_x + pad_x, 0, w_img))
            y2 = int(np.clip(max_y + pad_y, 0, h_img))

            if (x2 - x1) < self.min_crop_size:
                needed_x = self.min_crop_size - (x2 - x1)
                x1 = int(np.clip(x1 - needed_x // 2, 0, w_img))
                x2 = int(np.clip(x1 + self.min_crop_size, 0, w_img))
            if (y2 - y1) < self.min_crop_size:
                needed_y = self.min_crop_size - (y2 - y1)
                y1 = int(np.clip(y1 - needed_y // 2, 0, h_img))
                y2 = int(np.clip(y1 + self.min_crop_size, 0, h_img))

            landmark_quality = float(np.mean([kp[2] for kp in valid_kps]))
        elif bbox is not None and len(bbox) == 4:
            # Fallback: upper 35% of person bbox
            bx1, by1, bx2, by2 = bbox
            if bx2 <= bx1 or by2 <= by1:
                return None, 0.0
            x1 = int(np.clip(bx1, 0, w_img))
            y1 = int(np.clip(by1, 0, h_img))
            x2 = int(np.clip(bx2, 0, w_img))
            y2 = int(np.clip(by1 + (by2 - by1) * 0.35, 0, h_img))
            landmark_quality = 0.35
        else:
            return None, 0.0

        crop_w = x2 - x1
        crop_h = y2 - y1

        if crop_w < self.min_crop_size or crop_h < self.min_crop_size:
            return None, 0.0

        crop = frame[y1:y2, x1:x2]
        if crop.size == 0 or crop.shape[0] < self.min_crop_size or crop.shape[1] < self.min_crop_size:
            return None, 0.0

        # Quality derived from landmark confidence and crop resolution
        crop_size_quality = float(min(1.0, max(crop_w, crop_h) / 64.0))
        quality = float(np.clip(landmark_quality * crop_size_quality, 0.0, 1.0))

        return crop, quality


class SixDRepNetHeadOrientationProvider(HeadOrientationProvider):
    """Pretrained 6DRepNet 3D Head Pose Estimation Provider."""

    _shared_model = None
    _model_lock = threading.Lock()

    def __init__(
        self,
        gpu_id: int = 0,
        dict_path: str = "",
        min_quality: float = 0.20,
        crop_extractor: Optional[HeadCropExtractor] = None,
        model_instance: Optional[object] = None,
    ):
        self.gpu_id = gpu_id
        self.dict_path = dict_path
        self.min_quality = min_quality
        self.crop_extractor = crop_extractor or HeadCropExtractor()

        if model_instance is not None:
            SixDRepNetHeadOrientationProvider._shared_model = model_instance
        else:
            self._ensure_model_loaded()

    def _ensure_model_loaded(self):
        with SixDRepNetHeadOrientationProvider._model_lock:
            if SixDRepNetHeadOrientationProvider._shared_model is None:
                try:
                    import torch
                    from sixdrepnet import SixDRepNet
                    effective_gpu = self.gpu_id if torch.cuda.is_available() and self.gpu_id >= 0 else -1
                    SixDRepNetHeadOrientationProvider._shared_model = SixDRepNet(
                        gpu_id=effective_gpu,
                        dict_path=self.dict_path,
                    )
                    logger.info("SixDRepNet model successfully initialized (gpu_id=%s)", effective_gpu)
                except ImportError as e:
                    logger.warning("sixdrepnet package not installed: %s", e)
                    SixDRepNetHeadOrientationProvider._shared_model = None
                except Exception as e:
                    logger.error("Failed to load SixDRepNet weights: %s", e)
                    SixDRepNetHeadOrientationProvider._shared_model = None

    def estimate(
        self,
        keypoints: np.ndarray,
        bbox: Optional[Tuple[float, float, float, float]] = None,
        seat_baseline_yaw: float = 0.0,
        seat_baseline_pitch: float = 0.0,
        frame: Optional[np.ndarray] = None,
    ) -> HeadOrientationEstimate:
        """Estimate 3D head orientation using 6DRepNet on extracted head crop."""
        if frame is None or frame.size == 0 or SixDRepNetHeadOrientationProvider._shared_model is None:
            return HeadOrientationEstimate(source="sixdrepnet", quality=0.0)

        crop, quality = self.crop_extractor.extract_crop(frame=frame, keypoints=keypoints, bbox=bbox)
        if crop is None or quality < self.min_quality:
            return HeadOrientationEstimate(source="sixdrepnet", quality=quality)

        try:
            p_arr, y_arr, r_arr = SixDRepNetHeadOrientationProvider._shared_model.predict(crop)
            raw_pitch = float(p_arr[0])
            raw_yaw = float(y_arr[0])
            raw_roll = float(r_arr[0])

            # Canonical VIGIL angle normalization:
            # yaw < 0: LEFT, yaw > 0: RIGHT (raw_yaw matches 6DRepNet)
            # pitch > 0: DOWN (6DRepNet raw_pitch is negative when looking down, so -raw_pitch)
            canonical_yaw = float(np.clip(raw_yaw, -90.0, 90.0))
            canonical_pitch = float(np.clip(-raw_pitch, -45.0, 60.0))
            canonical_roll = float(np.clip(raw_roll, -90.0, 90.0))

            # Subtract Seat-relative baseline
            relative_yaw = float(np.clip(canonical_yaw - seat_baseline_yaw, -90.0, 90.0))
            relative_pitch = float(np.clip(canonical_pitch - seat_baseline_pitch, -45.0, 60.0))

            return HeadOrientationEstimate(
                yaw=relative_yaw,
                pitch=relative_pitch,
                roll=canonical_roll,
                quality=quality,
                source="sixdrepnet",
            )
        except Exception as e:
            logger.debug("6DRepNet prediction failed on crop: %s", e)
            return HeadOrientationEstimate(source="sixdrepnet", quality=0.0)


def create_head_pose_provider(
    provider_name: str = "pose_heuristic",
    **kwargs,
) -> HeadOrientationProvider:
    """Factory helper to instantiate the requested HeadOrientationProvider."""
    name_norm = provider_name.lower().replace("-", "_")
    if name_norm in ("sixdrepnet", "6drepnet", "sixd"):
        return SixDRepNetHeadOrientationProvider(**kwargs)
    return PoseHeuristicHeadOrientationProvider(**kwargs)

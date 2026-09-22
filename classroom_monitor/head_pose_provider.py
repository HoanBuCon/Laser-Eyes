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

from abc import ABC, abstractmethod
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from classroom_monitor.detector import calculate_head_pose_yaw_pitch

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


class HeadOrientationProvider(ABC):
    """Abstract base class for all head orientation estimation providers."""

    def __init__(self, **kwargs):
        self.total_single_calls: int = 0
        self.total_batch_calls: int = 0
        self.total_model_forward_calls: int = 0
        self.total_crops_requested: int = 0
        self.total_crops_valid: int = 0
        self.total_crops_rejected: int = 0
        self.batch_sizes: List[int] = []
        self.forward_times_ms: List[float] = []

    def get_telemetry(self) -> Dict[str, Any]:
        """Return raw telemetry counters for verification and profiling."""
        avg_batch = float(np.mean(self.batch_sizes)) if self.batch_sizes else 0.0
        max_batch = int(max(self.batch_sizes)) if self.batch_sizes else 0
        avg_fwd = float(np.mean(self.forward_times_ms)) if self.forward_times_ms else 0.0
        return {
            "estimate_single_calls": self.total_single_calls,
            "estimate_batch_calls": self.total_batch_calls,
            "model_forward_calls": self.total_model_forward_calls,
            "total_head_crops": self.total_crops_requested,
            "valid_head_crops": self.total_crops_valid,
            "rejected_head_crops": self.total_crops_rejected,
            "average_batch_size": round(avg_batch, 2),
            "max_batch_size": max_batch,
            "average_forward_ms": round(avg_fwd, 2),
        }

    @abstractmethod
    def estimate(
        self,
        keypoints: np.ndarray,
        bbox: Optional[Tuple[float, float, float, float]] = None,
        seat_baseline_yaw: float = 0.0,
        seat_baseline_pitch: float = 0.0,
        frame: Optional[np.ndarray] = None,
    ) -> HeadOrientationEstimate:
        """Estimate 3D head orientation for a single person detection."""
        pass

    def estimate_batch(
        self,
        requests: List[Dict[str, Any]],
        frame: Optional[np.ndarray] = None,
    ) -> Dict[str, HeadOrientationEstimate]:
        """Optional batched head pose estimation interface.

        Each request dictionary contains:
        {
            'seat_id': str,
            'keypoints': np.ndarray,
            'bbox': tuple,
            'seat_baseline_yaw': float,
            'seat_baseline_pitch': float
        }
        """
        self.total_batch_calls += 1
        self.total_crops_requested += len(requests)
        results = {}
        for req in requests:
            results[req["seat_id"]] = self.estimate(
                keypoints=req.get("keypoints"),
                bbox=req.get("bbox"),
                seat_baseline_yaw=req.get("seat_baseline_yaw", 0.0),
                seat_baseline_pitch=req.get("seat_baseline_pitch", 0.0),
                frame=frame,
            )
        return results


class PoseHeuristicHeadOrientationProvider(HeadOrientationProvider):
    """Zero-shot 2D keypoint geometric head orientation provider."""

    def __init__(self, min_kp_conf: float = 0.30, min_quality: float = 0.20, **kwargs):
        super().__init__(**kwargs)
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
        """Estimate relative Head Yaw and Pitch from 2D facial keypoints."""
        self.total_single_calls += 1
        if keypoints is None or len(keypoints) < 5:
            return HeadOrientationEstimate(source="pose_heuristic", quality=0.0)

        raw_yaw, raw_pitch = calculate_head_pose_yaw_pitch(keypoints)

        # Quality derived from head landmark visibility
        head_confidences = [kp[2] for kp in keypoints[:5] if kp[2] > 0]
        quality = float(np.mean(head_confidences)) if head_confidences else 0.0

        if quality < self.min_quality:
            return HeadOrientationEstimate(source="pose_heuristic", quality=quality)

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

    def estimate_batch(
        self,
        requests: List[Dict[str, Any]],
        frame: Optional[np.ndarray] = None,
    ) -> Dict[str, HeadOrientationEstimate]:
        """Fast vectorized 2D keypoint estimation for all seats."""
        self.total_batch_calls += 1
        self.total_crops_requested += len(requests)
        self.total_crops_valid += len(requests)
        self.batch_sizes.append(len(requests))

        results: Dict[str, HeadOrientationEstimate] = {}
        for req in requests:
            s_id = req["seat_id"]
            results[s_id] = self.estimate(
                keypoints=req.get("keypoints"),
                bbox=req.get("bbox"),
                seat_baseline_yaw=req.get("seat_baseline_yaw", 0.0),
                seat_baseline_pitch=req.get("seat_baseline_pitch", 0.0),
                frame=frame,
            )
        return results


class HeadCropExtractor:
    """Robust, unknown-safe head ROI crop extractor from YOLO-Pose keypoints and bbox."""

    def __init__(
        self,
        min_kp_conf: float = 0.30,
        min_crop_size: int = 24,
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

        h, w = frame.shape[:2]
        crop_box = None
        quality = 0.0

        if keypoints is not None and len(keypoints) >= 5:
            head_kps = keypoints[:5]
            valid_kps = [kp for kp in head_kps if kp[2] >= self.min_kp_conf]
            if len(valid_kps) >= 2:
                xs = [kp[0] for kp in valid_kps]
                ys = [kp[1] for kp in valid_kps]
                min_x, max_x = min(xs), max(xs)
                min_y, max_y = min(ys), max(ys)
                head_w = max_x - min_x
                head_h = max_y - min_y

                pad_x = max(head_w * self.padding_ratio, 16.0)
                pad_y = max(head_h * self.padding_ratio, 16.0)

                x1 = int(max(0, min_x - pad_x))
                y1 = int(max(0, min_y - pad_y))
                x2 = int(min(w, max_x + pad_x))
                y2 = int(min(h, max_y + pad_y))

                if (x2 - x1) >= self.min_crop_size and (y2 - y1) >= self.min_crop_size:
                    crop_box = (x1, y1, x2, y2)
                    quality = float(np.clip(np.mean([kp[2] for kp in valid_kps]), 0.0, 1.0))

        # Fallback to upper 25% of bbox if keypoints are occluded
        if crop_box is None and bbox is not None:
            bx1, by1, bx2, by2 = bbox
            bw = bx2 - bx1
            bh = by2 - by1
            if bw >= self.min_crop_size and bh >= self.min_crop_size * 2:
                x1 = int(max(0, bx1))
                y1 = int(max(0, by1))
                x2 = int(min(w, bx2))
                y2 = int(min(h, by1 + bh * 0.28))
                if (x2 - x1) >= self.min_crop_size and (y2 - y1) >= self.min_crop_size:
                    crop_box = (x1, y1, x2, y2)
                    quality = 0.40

        if crop_box is None:
            return None, 0.0

        x1, y1, x2, y2 = crop_box
        crop = frame[y1:y2, x1:x2]
        return crop, quality


class SixDRepNetHeadOrientationProvider(HeadOrientationProvider):
    """High-Throughput Deep Learning 6DRepNet Head Orientation Estimator with Batch Inference."""

    _shared_model: Optional[object] = None
    _model_lock = threading.Lock()

    def __init__(
        self,
        gpu_id: int = 0,
        dict_path: str = "",
        min_quality: float = 0.35,
        crop_extractor: Optional[HeadCropExtractor] = None,
        model_instance: Optional[object] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.gpu_id = gpu_id
        self.dict_path = dict_path
        self.min_quality = min_quality
        self.crop_extractor = crop_extractor or HeadCropExtractor(min_crop_size=24)
        self.last_batch_size: int = 0
        self.last_forward_time_ms: float = 0.0

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
        """Single-crop estimation fallback."""
        self.total_single_calls += 1
        batch_res = self.estimate_batch(
            requests=[{
                "seat_id": "DEFAULT",
                "keypoints": keypoints,
                "bbox": bbox,
                "seat_baseline_yaw": seat_baseline_yaw,
                "seat_baseline_pitch": seat_baseline_pitch,
            }],
            frame=frame,
        )
        return batch_res.get("DEFAULT", HeadOrientationEstimate(source="sixdrepnet", quality=0.0))

    def estimate_batch(
        self,
        requests: List[Dict[str, Any]],
        frame: Optional[np.ndarray] = None,
    ) -> Dict[str, HeadOrientationEstimate]:
        """High-Throughput Batched 6DRepNet Inference on GPU for all eligible seat actors."""
        self.total_batch_calls += 1
        self.total_crops_requested += len(requests)

        results: Dict[str, HeadOrientationEstimate] = {}
        if not requests:
            return results

        if frame is None or frame.size == 0 or SixDRepNetHeadOrientationProvider._shared_model is None:
            self.total_crops_rejected += len(requests)
            for req in requests:
                results[req["seat_id"]] = HeadOrientationEstimate(source="sixdrepnet", quality=0.0)
            return results

        valid_crops: List[np.ndarray] = []
        valid_meta: List[Tuple[str, float, float, float]] = []

        # 1. Extract Crops & Apply Quality Gate
        for req in requests:
            s_id = req["seat_id"]
            kps = req.get("keypoints")
            box = req.get("bbox")
            base_yaw = req.get("seat_baseline_yaw", 0.0)
            base_pitch = req.get("seat_baseline_pitch", 0.0)

            crop, quality = self.crop_extractor.extract_crop(frame=frame, keypoints=kps, bbox=box)
            if crop is None or quality < self.min_quality:
                self.total_crops_rejected += 1
                results[s_id] = HeadOrientationEstimate(source="sixdrepnet", quality=quality)
            else:
                self.total_crops_valid += 1
                valid_crops.append(crop)
                valid_meta.append((s_id, quality, base_yaw, base_pitch))

        if not valid_crops:
            self.last_batch_size = 0
            self.last_forward_time_ms = 0.0
            return results

        model_obj = SixDRepNetHeadOrientationProvider._shared_model

        # 2. Fast Batch Tensor Forward
        try:
            t0 = time.perf_counter()
            self.total_model_forward_calls += 1
            self.batch_sizes.append(len(valid_crops))

            if hasattr(model_obj, "transformations") and hasattr(model_obj, "model"):
                import cv2
                import torch
                from PIL import Image
                from sixdrepnet import utils

                tensors = []
                for c in valid_crops:
                    rgb = cv2.cvtColor(c, cv2.COLOR_BGR2RGB)
                    pil_img = Image.fromarray(rgb)
                    tensors.append(model_obj.transformations(pil_img))

                batch_tensor = torch.stack(tensors)
                if hasattr(model_obj, "gpu") and model_obj.gpu != -1 and torch.cuda.is_available():
                    batch_tensor = batch_tensor.cuda(model_obj.gpu)

                with torch.inference_mode():
                    preds = model_obj.model(batch_tensor)
                    euler = utils.compute_euler_angles_from_rotation_matrices(preds) * 180.0 / np.pi
                    p_arr = euler[:, 0].cpu().numpy()
                    y_arr = euler[:, 1].cpu().numpy()
                    r_arr = euler[:, 2].cpu().numpy()
            else:
                # Mock or fallback predictor
                p_list, y_list, r_list = [], [], []
                for c in valid_crops:
                    p, y, r = model_obj.predict(c)
                    p_list.append(float(p[0]) if hasattr(p, "__getitem__") else float(p))
                    y_list.append(float(y[0]) if hasattr(y, "__getitem__") else float(y))
                    r_list.append(float(r[0]) if hasattr(r, "__getitem__") else float(r))
                p_arr, y_arr, r_arr = np.array(p_list), np.array(y_list), np.array(r_list)

            fwd_ms = (time.perf_counter() - t0) * 1000.0
            self.last_forward_time_ms = fwd_ms
            self.forward_times_ms.append(fwd_ms)
            self.last_batch_size = len(valid_crops)

            # 3. Canonical Normalization & Baseline Subtraction
            for i, (s_id, quality, base_yaw, base_pitch) in enumerate(valid_meta):
                raw_pitch = float(p_arr[i])
                raw_yaw = float(y_arr[i])
                raw_roll = float(r_arr[i])

                canonical_yaw = float(np.clip(raw_yaw, -90.0, 90.0))
                canonical_pitch = float(np.clip(-raw_pitch, -45.0, 60.0))
                canonical_roll = float(np.clip(raw_roll, -90.0, 90.0))

                relative_yaw = float(np.clip(canonical_yaw - base_yaw, -90.0, 90.0))
                relative_pitch = float(np.clip(canonical_pitch - base_pitch, -45.0, 60.0))

                results[s_id] = HeadOrientationEstimate(
                    yaw=relative_yaw,
                    pitch=relative_pitch,
                    roll=canonical_roll,
                    quality=quality,
                    source="sixdrepnet",
                )

        except Exception as e:
            logger.debug("Batched 6DRepNet inference failed: %s", e)
            for s_id, qual, _, _ in valid_meta:
                results[s_id] = HeadOrientationEstimate(source="sixdrepnet", quality=0.0)

        return results


def create_head_pose_provider(
    provider_name: str = "pose_heuristic",
    **kwargs,
) -> HeadOrientationProvider:
    """Factory helper to instantiate the requested HeadOrientationProvider."""
    name_norm = provider_name.lower().replace("-", "_")
    if name_norm in ("sixdrepnet", "6drepnet", "sixd"):
        return SixDRepNetHeadOrientationProvider(**kwargs)
    return PoseHeuristicHeadOrientationProvider(**kwargs)

"""Head Orientation Provider Abstraction and Implementations.

Implements Scope 03 (FR-PER-003, FR-PER-004) and Scope 06 of SRS v2.0:
- Pluggable HeadOrientationProvider interface with backward-compatible signature.
- PoseHeuristicHeadOrientationProvider: Zero-shot 2D keypoint geometric baseline.
- SixDRepNetHeadOrientationProvider: Pretrained 6DRepNet 3D head pose estimator.
- HeadCropExtractor: Unknown-safe head ROI extraction with bounding box safeguards.
- Canonical angle conventions are SUBJECT-centric (the candidate's own left/right,
  as in the annotation guideline and the ground truth):
  * yaw < 0: candidate turns to THEIR left
  * yaw > 0: candidate turns to THEIR right
  * pitch > 0: DOWN
  6DRepNet already reports this convention (verified on real classroom crops: with
  the camera facing the candidates, positive yaw turns the face toward image-left).
  Seat neighbours are expressed in the same subject-centric terms (see SeatGraph).
- Seat-perspective relative yaw/pitch baseline subtraction.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

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
        # The keypoint geometry is image-space (nose toward image-right is
        # positive).  This provider needs a visible face, i.e. a camera facing
        # the candidate, where image-right is the candidate's LEFT.
        if raw_yaw is not None:
            raw_yaw = -raw_yaw

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
    """Robust, unknown-safe head ROI crop extractor from YOLO-Pose keypoints.

    A crop is produced only when the face itself is visible (nose and at least
    one eye).  Back-of-head, deeply bowed or body-only crops are UNKNOWN: a
    head-pose model applied to them returns large, meaningless yaw values.
    """

    def __init__(
        self,
        min_kp_conf: float = 0.30,
        min_crop_size: int = 24,
        padding_ratio: float = 0.45,
        require_face_landmarks: bool = True,
    ):
        self.min_kp_conf = min_kp_conf
        self.min_crop_size = min_crop_size
        self.padding_ratio = padding_ratio
        self.require_face_landmarks = require_face_landmarks

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

        face_visible = True
        if keypoints is not None and len(keypoints) >= 5 and self.require_face_landmarks:
            nose_ok = keypoints[0][2] >= self.min_kp_conf
            eye_ok = keypoints[1][2] >= self.min_kp_conf or keypoints[2][2] >= self.min_kp_conf
            face_visible = bool(nose_ok and eye_ok)

        if keypoints is not None and len(keypoints) >= 5 and face_visible:
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

        # No bounding-box fallback: the upper part of a person box contains
        # shoulders/torso, and head pose estimated from it is not a head pose.
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
            logger.warning("Batched 6DRepNet inference failed: %s", e)
            for s_id, qual, _, _ in valid_meta:
                results[s_id] = HeadOrientationEstimate(source="sixdrepnet", quality=0.0)

        return results


@dataclass
class _GateState:
    last_accepted: Optional[Tuple[float, float]] = None  # (ts, yaw) of the last trusted sample
    pending: Optional[Tuple[float, float]] = None        # implausible sample awaiting confirmation
    rejected: Optional[Deque[bool]] = None               # recent samples rejected as implausible
    unreliable: bool = False


class HeadPoseSampleGate:
    """Rejects head-pose samples that a real head could not produce.

    Measured on the demo videos, 9-30 % of consecutive 5 Hz head-model samples
    jump by more than 40 degrees in 0.2 s while the student is writing; those
    steps create fake head-turn episodes.  Two checks are applied per seat:

    * Plausibility: a sample that jumps more than ``jump_deg`` from the last
      trusted sample is held back (yaw UNKNOWN) and only accepted when the next
      sample confirms it.  A genuine turn is delayed by one sample (~0.2 s);
      an isolated spike or a flip back and forth is discarded.
    * Reliability: when rejected samples make up at least ``unreliable_rate``
      of the seat's recent samples, head orientation for that seat is reported
      as unreliable (yaw UNKNOWN) until the rate falls to ``reliable_rate``.
      A large step that the next sample confirms is a real head turn and is
      not counted.  On the India video every seat with an annotated head turn
      stays at or below 12 % rejected samples, while the noisiest seats reach
      20-25 %.
    """

    def __init__(
        self,
        jump_deg: float = 40.0,
        confirm_deg: float = 20.0,
        max_gap_ms: float = 600.0,
        window_samples: int = 50,
        min_samples: int = 15,
        unreliable_rate: float = 0.20,
        reliable_rate: float = 0.12,
    ):
        self.jump_deg = jump_deg
        self.confirm_deg = confirm_deg
        self.max_gap_ms = max_gap_ms
        self.window_samples = window_samples
        self.min_samples = min_samples
        self.unreliable_rate = unreliable_rate
        self.reliable_rate = reliable_rate
        self._state: Dict[str, _GateState] = {}

    def apply(self, seat_id: str, estimate: HeadOrientationEstimate, timestamp_ms: float) -> HeadOrientationEstimate:
        if estimate.yaw is None:
            return estimate
        state = self._state.setdefault(seat_id, _GateState(rejected=deque(maxlen=self.window_samples)))
        yaw = float(estimate.yaw)

        # Plausibility against the last trusted sample
        accepted = True
        last = state.last_accepted
        if last is not None and (timestamp_ms - last[0]) <= self.max_gap_ms and abs(yaw - last[1]) > self.jump_deg:
            pending = state.pending
            confirmed = (
                pending is not None
                and (timestamp_ms - pending[0]) <= self.max_gap_ms
                and abs(yaw - pending[1]) <= self.confirm_deg
            )
            if confirmed:
                state.pending = None
                if state.rejected and state.rejected[-1]:
                    state.rejected[-1] = False  # the held sample was a real turn after all
            else:
                state.pending = (timestamp_ms, yaw)
                accepted = False
        else:
            state.pending = None
        if accepted:
            state.last_accepted = (timestamp_ms, yaw)
        state.rejected.append(not accepted)

        # Reliability: share of recent samples rejected as implausible
        if len(state.rejected) >= self.min_samples:
            rate = sum(state.rejected) / len(state.rejected)
            if state.unreliable and rate <= self.reliable_rate:
                state.unreliable = False
            elif not state.unreliable and rate >= self.unreliable_rate:
                state.unreliable = True

        if state.unreliable:
            return replace(estimate, yaw=None, source=f"{estimate.source}_unreliable_seat")
        if not accepted:
            return replace(estimate, yaw=None, source=f"{estimate.source}_implausible_jump")
        return estimate

    def is_unreliable(self, seat_id: str) -> bool:
        state = self._state.get(seat_id)
        return bool(state and state.unreliable)

    def unreliable_seats(self) -> Set[str]:
        return {seat for seat, state in self._state.items() if state.unreliable}

    def reset_seat(self, seat_id: str) -> None:
        self._state.pop(seat_id, None)


class AdaptiveYawBaseline:
    """Per-seat neutral head yaw learned from that seat's own head-pose samples.

    Camera perspective and the way a candidate sits give every seat its own
    "looking at my paper" yaw, measured on the demo videos between -55 and +70
    degrees, far from the hand-set calibration baselines.  A head turn is a
    deviation from this neutral, so the yaw passed on is relative to the
    seat's dominant posture: the median of the samples inside the most
    populated yaw cluster of the recent window.  A plain median would drift
    toward the turns themselves when a student turns often or the clip is
    short.  Until enough samples exist the yaw is UNKNOWN rather than judged
    against a guessed neutral.
    """

    CLUSTER_HALF_WIDTH_DEG = 20.0

    @classmethod
    def _dominant_posture(cls, yaws: List[float]) -> float:
        values = np.asarray(yaws, dtype=float)
        counts, edges = np.histogram(values, bins=np.arange(-90.0, 100.0, 10.0))
        smoothed = np.convolve(counts, np.ones(3), mode="same")
        peak = float((edges[int(np.argmax(smoothed))] + edges[int(np.argmax(smoothed)) + 1]) / 2.0)
        cluster = values[np.abs(values - peak) <= cls.CLUSTER_HALF_WIDTH_DEG]
        return float(np.median(cluster)) if cluster.size else float(np.median(values))

    def __init__(self, window_ms: float = 60000.0, min_samples: int = 10):
        self.window_ms = window_ms
        self.min_samples = min_samples
        self._samples: Dict[str, Deque[Tuple[float, float]]] = {}

    def apply(self, seat_id: str, estimate: HeadOrientationEstimate, timestamp_ms: float) -> HeadOrientationEstimate:
        if estimate.yaw is None:
            return estimate
        history = self._samples.setdefault(seat_id, deque())
        history.append((timestamp_ms, float(estimate.yaw)))
        while history and (timestamp_ms - history[0][0]) > self.window_ms:
            history.popleft()
        if len(history) < self.min_samples:
            return replace(estimate, yaw=None, source=f"{estimate.source}_baseline_warmup")
        neutral = self._dominant_posture([yaw for _, yaw in history])
        return replace(estimate, yaw=float(np.clip(estimate.yaw - neutral, -90.0, 90.0)))

    def neutral(self, seat_id: str) -> Optional[float]:
        history = self._samples.get(seat_id)
        if not history or len(history) < self.min_samples:
            return None
        return self._dominant_posture([yaw for _, yaw in history])

    def reset_seat(self, seat_id: str) -> None:
        self._samples.pop(seat_id, None)


class HeadOffsetBaseline:
    """Head offset over the shoulders relative to the student's own usual posture.

    Students sit and write with their head somewhat to one side; only a
    change from that is a movement toward a neighbour.  The neutral is the
    median of the recent window, UNKNOWN until enough samples exist.
    """

    def __init__(self, window_ms: float = 30000.0, min_samples: int = 30):
        self.window_ms = window_ms
        self.min_samples = min_samples
        self._samples: Dict[str, Deque[Tuple[float, float]]] = {}

    def apply(self, seat_id: str, offset: float, timestamp_ms: float) -> Optional[float]:
        history = self._samples.setdefault(seat_id, deque())
        history.append((timestamp_ms, float(offset)))
        while history and (timestamp_ms - history[0][0]) > self.window_ms:
            history.popleft()
        if len(history) < self.min_samples:
            return None
        return float(offset) - float(np.median([value for _, value in history]))

    def reset_seat(self, seat_id: str) -> None:
        self._samples.pop(seat_id, None)


def create_head_pose_provider(
    provider_name: str = "pose_heuristic",
    **kwargs,
) -> HeadOrientationProvider:
    """Factory helper to instantiate the requested HeadOrientationProvider."""
    name_norm = provider_name.lower().replace("-", "_")
    if name_norm in ("sixdrepnet", "6drepnet", "sixd"):
        return SixDRepNetHeadOrientationProvider(**kwargs)
    return PoseHeuristicHeadOrientationProvider(**kwargs)

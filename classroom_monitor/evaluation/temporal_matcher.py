"""Canonical Actor-Centric Temporal Episode Matcher for VIGIL AI.

Implements strict benchmark matching:
- SAME SEAT (normalized seat code)
- SAME NORMALIZED LABEL
- TEMPORAL IoU >= iou_threshold (default 0.30)
- Optimal 1-to-1 best IoU matching (no duplicate/multiple pairing)
- Safe handling & explicit reporting of malformed Ground Truth entries.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, Union

# Canonical Label Aliasing / Normalization
LABEL_NORMALIZATION: Dict[str, str] = {
    "HEAD_TURN_RIGHT": "HEAD_TURN_RIGHT",
    "HEAD_TURN_LEFT": "HEAD_TURN_LEFT",
    "LOOK_DOWN": "LOOK_DOWN",
    "HEAD_PITCH_DOWN": "LOOK_DOWN",
    "TORSO_LEAN_LEFT": "TORSO_LEAN_LEFT",
    "TORSO_LEAN_RIGHT": "TORSO_LEAN_RIGHT",
    "HAND_BELOW_DESK": "WRIST_BELOW_DESK",
    "WRIST_BELOW_DESK": "WRIST_BELOW_DESK",
    "BELOW_DESK_INTERACTION": "WRIST_BELOW_DESK",
    "PHONE_OR_DEVICE_INTERACTION": "PHONE_OR_DEVICE_INTERACTION",
    "SEAT_EMPTY": "SEAT_EMPTY",
    "MULTI_PERSON_NEAR_SEAT": "MULTI_PERSON_NEAR_SEAT",
}


def normalize_label(label: str) -> str:
    """Normalize episode label strings to canonical enum values."""
    if not label:
        return ""
    clean = str(label).strip().upper()
    return LABEL_NORMALIZATION.get(clean, clean)


def normalize_seat_code(seat: Optional[str]) -> str:
    """Extract numeric/normalized core seat code for actor-centric matching.

    Examples:
        'SEAT-ROOM-CALIB-01-20' -> '20'
        'SEAT-STUDENT-05'       -> '5'
        'SEAT-04'               -> '4'
        'S01'                   -> '1'
        '4'                     -> '4'
    """
    if not seat:
        return ""
    seat_str = str(seat).strip().upper()
    # Find trailing integer digits or standard seat numbers
    digits = re.findall(r"\d+", seat_str)
    if digits:
        return str(int(digits[-1]))
    return seat_str


def compute_temporal_iou(
    start_a: float, end_a: float, start_b: float, end_b: float
) -> float:
    """Compute 1D Temporal Intersection over Union.

    intersection = max(0, min(gt_end, ai_end) - max(gt_start, ai_start))
    union = max(gt_end, ai_end) - min(gt_start, ai_start)
    IoU = intersection / union
    """
    if end_a <= start_a or end_b <= start_b:
        return 0.0
    inter = max(0.0, min(end_a, end_b) - max(start_a, start_b))
    union = max(end_a, end_b) - min(start_a, start_b)
    if union <= 0.0:
        return 0.0
    return float(inter / union)


def validate_gt_record(gt: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Verify integrity of a single ground truth record."""
    start_ms = float(gt.get("start_ms", 0.0))
    end_ms = float(gt.get("end_ms", 0.0))
    stored_dur = gt.get("duration_ms")

    if end_ms <= start_ms:
        return False, f"end_ms ({end_ms}) <= start_ms ({start_ms})"

    if stored_dur is not None:
        calc_dur = end_ms - start_ms
        if abs(float(stored_dur) - calc_dur) > 100.0:
            return (
                False,
                f"stored duration_ms ({stored_dur}) != end_ms - start_ms ({calc_dur})",
            )

    return True, None


@dataclass
class MatchedPair:
    gt_id: str
    gt_seat: str
    gt_label: str
    gt_start_ms: float
    gt_end_ms: float
    ai_id: str
    ai_seat: str
    ai_label: str
    ai_start_ms: float
    ai_end_ms: float
    temporal_iou: float
    match_status: str = "TP"


@dataclass
class EvaluationResult:
    evaluation_name: str
    matching_rule: Dict[str, Any]
    total_gt_episodes: int
    valid_gt_episodes: int
    malformed_gt_excluded: int
    ai_episodes_evaluated: int
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float
    avg_tp_iou: float
    matched_pairs: List[Dict[str, Any]] = field(default_factory=list)
    unmatched_gt: List[Dict[str, Any]] = field(default_factory=list)
    unmatched_ai: List[Dict[str, Any]] = field(default_factory=list)
    malformed_gt_records: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CanonicalTemporalMatcher:
    """Strict 1-to-1 temporal matcher enforcing actor-centric consistency."""

    def __init__(
        self,
        iou_threshold: float = 0.30,
        require_same_seat: bool = True,
        require_same_label: bool = True,
        exclude_malformed_gt: bool = True,
    ):
        self.iou_threshold = iou_threshold
        self.require_same_seat = require_same_seat
        self.require_same_label = require_same_label
        self.exclude_malformed_gt = exclude_malformed_gt

    def match(
        self,
        gt_episodes: List[Union[Dict[str, Any], Any]],
        ai_episodes: List[Union[Dict[str, Any], Any]],
        filter_label_substring: Optional[str] = "HEAD_TURN",
    ) -> EvaluationResult:
        """Perform strict 1-to-1 matching between GT and AI episodes."""
        # 1. Parse & filter Ground Truth
        parsed_gt: List[Dict[str, Any]] = []
        malformed_gt: List[Dict[str, Any]] = []

        for item in gt_episodes:
            rec = item if isinstance(item, dict) else item.__dict__
            label = rec.get("episode_type", "")
            if hasattr(label, "value"):
                label = label.value
            label = str(label)

            if filter_label_substring and filter_label_substring not in label:
                continue

            is_valid, issue = validate_gt_record(rec)
            if not is_valid:
                malformed_rec = dict(rec)
                malformed_rec["issue"] = issue
                malformed_gt.append(malformed_rec)
                if self.exclude_malformed_gt:
                    continue

            parsed_gt.append(
                {
                    "id": str(rec.get("id", rec.get("episode_id", ""))),
                    "seat_code": rec.get("seat_code") or rec.get("seat_id") or "",
                    "label": normalize_label(label),
                    "start_ms": float(rec.get("start_ms", rec.get("start_timestamp_ms", 0.0))),
                    "end_ms": float(
                        rec.get(
                            "end_ms",
                            rec.get(
                                "end_timestamp_ms",
                                float(rec.get("start_ms", 0.0)) + float(rec.get("duration_ms", 0.0)),
                            ),
                        )
                    ),
                    "raw": rec,
                }
            )

        # 2. Parse & filter AI Episodes
        parsed_ai: List[Dict[str, Any]] = []
        for item in ai_episodes:
            if isinstance(item, dict):
                rec = item
                label = rec.get("episode_type", "")
                seat_code = rec.get("seat_code") or rec.get("seat_id") or ""
                start_ms = float(rec.get("start_timestamp_ms", rec.get("start_ms", 0.0)))
                end_ms = float(
                    rec.get(
                        "end_timestamp_ms",
                        rec.get("end_ms", start_ms + float(rec.get("duration_ms", 0.0))),
                    )
                )
                ep_id = str(rec.get("episode_id", rec.get("id", "")))
            else:
                ep_type = item.episode_type.value if hasattr(item.episode_type, "value") else str(item.episode_type)
                label = ep_type
                seat_code = getattr(item, "seat_id", "") or getattr(item, "seat_code", "")
                start_ms = float(getattr(item, "start_timestamp_ms", 0.0))
                dur = float(getattr(item, "duration_ms", 0.0))
                end_ms = float(getattr(item, "end_timestamp_ms", 0.0) or (start_ms + dur))
                ep_id = str(getattr(item, "episode_id", ""))

            if filter_label_substring and filter_label_substring not in label:
                continue

            parsed_ai.append(
                {
                    "id": ep_id,
                    "seat_code": seat_code,
                    "label": normalize_label(label),
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "raw": rec if isinstance(item, dict) else item,
                }
            )

        # 3. Find all candidate matches
        candidates: List[Tuple[float, int, int]] = []
        for gt_idx, gt in enumerate(parsed_gt):
            gt_seat_norm = normalize_seat_code(gt["seat_code"])
            for ai_idx, ai in enumerate(parsed_ai):
                ai_seat_norm = normalize_seat_code(ai["seat_code"])

                # Check seat
                if self.require_same_seat and gt_seat_norm != ai_seat_norm:
                    continue

                # Check label
                if self.require_same_label and gt["label"] != ai["label"]:
                    continue

                # Compute IoU
                iou = compute_temporal_iou(
                    gt["start_ms"], gt["end_ms"], ai["start_ms"], ai["end_ms"]
                )
                if iou >= self.iou_threshold:
                    candidates.append((iou, gt_idx, ai_idx))

        # 4. Greedy Best-IoU 1-to-1 Matching
        candidates.sort(key=lambda x: x[0], reverse=True)

        matched_gt_indices: Set[int] = set()
        matched_ai_indices: Set[int] = set()
        matched_pairs: List[MatchedPair] = []

        for iou, gt_idx, ai_idx in candidates:
            if gt_idx in matched_gt_indices or ai_idx in matched_ai_indices:
                continue
            matched_gt_indices.add(gt_idx)
            matched_ai_indices.add(ai_idx)

            gt = parsed_gt[gt_idx]
            ai = parsed_ai[ai_idx]
            matched_pairs.append(
                MatchedPair(
                    gt_id=gt["id"],
                    gt_seat=gt["seat_code"],
                    gt_label=gt["label"],
                    gt_start_ms=gt["start_ms"],
                    gt_end_ms=gt["end_ms"],
                    ai_id=ai["id"],
                    ai_seat=ai["seat_code"],
                    ai_label=ai["label"],
                    ai_start_ms=ai["start_ms"],
                    ai_end_ms=ai["end_ms"],
                    temporal_iou=round(iou, 3),
                    match_status="TP",
                )
            )

        # 5. Compile Metrics
        tp = len(matched_pairs)
        fp = len(parsed_ai) - tp
        fn = len(parsed_gt) - tp

        precision = (tp / (tp + fp)) * 100.0 if (tp + fp) > 0 else 0.0
        recall = (tp / (tp + fn)) * 100.0 if (tp + fn) > 0 else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
        avg_tp_iou = float(sum(p.temporal_iou for p in matched_pairs) / tp) if tp > 0 else 0.0

        unmatched_gt = [
            parsed_gt[i] for i in range(len(parsed_gt)) if i not in matched_gt_indices
        ]
        unmatched_ai = [
            parsed_ai[i] for i in range(len(parsed_ai)) if i not in matched_ai_indices
        ]

        return EvaluationResult(
            evaluation_name="actor_centric_temporal_head_turn_evaluation",
            matching_rule={
                "require_same_seat": self.require_same_seat,
                "require_same_label": self.require_same_label,
                "temporal_iou_threshold": self.iou_threshold,
                "seat_normalization": "numeric_suffix",
            },
            total_gt_episodes=len(gt_episodes),
            valid_gt_episodes=len(parsed_gt),
            malformed_gt_excluded=len(malformed_gt),
            ai_episodes_evaluated=len(parsed_ai),
            tp=tp,
            fp=fp,
            fn=fn,
            precision=round(precision, 2),
            recall=round(recall, 2),
            f1=round(f1, 2),
            avg_tp_iou=round(avg_tp_iou, 3),
            matched_pairs=[asdict(p) for p in matched_pairs],
            unmatched_gt=unmatched_gt,
            unmatched_ai=unmatched_ai,
            malformed_gt_records=malformed_gt,
        )


def match_temporal_episodes(
    gt_episodes: List[Any],
    ai_episodes: List[Any],
    iou_threshold: float = 0.30,
    require_same_seat: bool = True,
    require_same_label: bool = True,
    exclude_malformed_gt: bool = True,
    filter_label_substring: Optional[str] = "HEAD_TURN",
) -> EvaluationResult:
    """Convenience functional interface for strict episode matching."""
    matcher = CanonicalTemporalMatcher(
        iou_threshold=iou_threshold,
        require_same_seat=require_same_seat,
        require_same_label=require_same_label,
        exclude_malformed_gt=exclude_malformed_gt,
    )
    return matcher.match(
        gt_episodes=gt_episodes,
        ai_episodes=ai_episodes,
        filter_label_substring=filter_label_substring,
    )

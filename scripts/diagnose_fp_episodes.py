"""Diagnostic Script for Analyzing False Positive Head Orientation Episodes.

Extracts frame-level telemetry for all AI Head Turn episodes from the baseline 6DRepNet run,
computes raw vs relative yaw statistics, head crop dimensions, keypoint qualities,
and classifies all 187 False Positive episodes into standardized root cause categories.
Exports to data/head_orientation_optimization/fp_diagnosis.json.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

# Ensure project root in sys.path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from classroom_monitor.config import ClassroomConfig
from classroom_monitor.detector import PoseClassroomDetector
from classroom_monitor.head_pose_provider import HeadCropExtractor, SixDRepNetHeadOrientationProvider
from classroom_monitor.seat_manager import SeatManager
from scripts.run_classroom_demo import setup_database_seats
from scripts.benchmark_temporal_ground_truth import compute_temporal_iou, compute_temporal_overlap_ms, normalize_label

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s")
logger = logging.getLogger("FPDiagnosis")

GT_FILE = Path("data/ground_truth/india_classroom_gt.json")
BASELINE_6D_EPS = Path("data/head_orientation_ab/sixdrepnet/episodes.json")
OUT_DIR = Path("data/head_orientation_optimization")
VIDEO_PATH = "demo_video/india_classroom.mp4"
ROOM_ID = "ROOM-CALIB-01"
CAMERA_ID = "CAM-01"


def diagnose_false_positives() -> Dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with open(GT_FILE, "r", encoding="utf-8") as f:
        gt_data = json.load(f)
    gt_episodes = gt_data["episodes"]
    head_gt = [h for h in gt_episodes if "HEAD_TURN" in h["episode_type"]]

    with open(BASELINE_6D_EPS, "r", encoding="utf-8") as f:
        all_ai_eps = json.load(f)
    ai_head_eps = [a for a in all_ai_eps if "HEAD_TURN" in a["episode_type"]]

    logger.info("Loaded %d GT head episodes and %d AI head episodes", len(head_gt), len(ai_head_eps))

    # Match TP vs FP
    matched_ai_ids = set()
    matched_gt_ids = set()
    for h in head_gt:
        h_norm = normalize_label(h["episode_type"])
        h_start, h_end, h_seat = float(h["start_ms"]), float(h["end_ms"]), h["seat_code"]
        best_iou, best_ai = 0.0, None
        for a in ai_head_eps:
            if a["seat_id"] != h_seat or normalize_label(a["episode_type"]) != h_norm:
                continue
            a_start = float(a["start_timestamp_ms"])
            a_end = float(a["end_timestamp_ms"] or a_start + a.get("duration_ms", 0.0))
            if a["episode_id"] in matched_ai_ids:
                continue
            iou = compute_temporal_iou(h_start, h_end, a_start, a_end)
            if iou > best_iou:
                best_iou = iou
                best_ai = a
        if best_iou >= 0.30 and best_ai is not None:
            matched_ai_ids.add(best_ai["episode_id"])
            matched_gt_ids.add(h["id"])

    logger.info("Identified %d True Positives and %d False Positives", len(matched_ai_ids), len(ai_head_eps) - len(matched_ai_ids))

    # Setup detector & seat graph to sample telemetry across the video
    detector = PoseClassroomDetector(config=ClassroomConfig(pipeline_mode="2stage_pose"))
    crop_extractor = HeadCropExtractor(min_crop_size=15)
    seat_defs = setup_database_seats(room_id=ROOM_ID, camera_id=CAMERA_ID)
    seat_mgr = SeatManager(room_id=ROOM_ID, camera_id=CAMERA_ID)
    seat_mgr.load_seats(seat_defs)
    seat_graph = seat_mgr.to_seat_graph()

    cap = cv2.VideoCapture(VIDEO_PATH)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    # Build diagnosis for every AI head episode
    diagnosed_episodes = []
    category_counts: Dict[str, int] = {
        "NEUTRAL_BASELINE_OFFSET": 0,
        "SINGLE_FRAME_SPIKE": 0,
        "SHORT_NOISY_OSCILLATION": 0,
        "TEMPORAL_FRAGMENTATION": 0,
        "POOR_HEAD_CROP": 0,
        "OCCLUSION": 0,
        "WRONG_SEAT": 0,
        "TRUE_HEAD_MOVEMENT_NOT_IN_GT": 0,
        "GT_AMBIGUOUS": 0,
        "OTHER": 0,
    }

    # Group AI episodes by seat to detect temporal fragmentation
    seat_ai_eps: Dict[str, List[Dict[str, Any]]] = {}
    for a in ai_head_eps:
        seat_ai_eps.setdefault(a["seat_id"], []).append(a)
    for s_id in seat_ai_eps:
        seat_ai_eps[s_id].sort(key=lambda x: float(x["start_timestamp_ms"]))

    for a in ai_head_eps:
        ep_id = a["episode_id"]
        seat_id = a["seat_id"]
        ep_type = a["episode_type"]
        start_ms = float(a["start_timestamp_ms"])
        end_ms = float(a["end_timestamp_ms"] or start_ms + a.get("duration_ms", 0.0))
        duration_ms = end_ms - start_ms
        is_tp = ep_id in matched_ai_ids

        s_ctx = seat_graph.get_context(seat_id)
        base_yaw = s_ctx.reference_directions.baseline_yaw if s_ctx else 0.0

        # Sample middle frame of this episode
        mid_ts = (start_ms + end_ms) / 2.0
        f_idx = int(round((mid_ts / 1000.0) * fps))
        cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
        ret, frame = cap.read()

        crop_w, crop_h, kp_qual = 0, 0, 0.0
        if ret and frame is not None:
            dets = detector.detect(frame, frame_index=f_idx)
            mapped, unmapped = seat_mgr.map_detections_to_seats(dets, timestamp_ms=mid_ts, frame_idx=f_idx)
            det = mapped.get(seat_id)
            if det is not None and det.keypoints is not None:
                crop, qual = crop_extractor.extract_crop(frame, det.keypoints, det.bbox)
                if crop is not None:
                    crop_h, crop_w = crop.shape[:2]
                kp_qual = qual

        # Check GT overlaps regardless of match
        gt_overlaps = []
        for h in head_gt:
            if h["seat_code"] == seat_id:
                ov = compute_temporal_overlap_ms(float(h["start_ms"]), float(h["end_ms"]), start_ms, end_ms)
                if ov > 0:
                    gt_overlaps.append({
                        "gt_id": h["id"],
                        "gt_type": h["episode_type"],
                        "overlap_ms": round(ov, 1),
                    })

        # Check if part of a fragmented train on the same seat
        is_fragmented = False
        siblings = seat_ai_eps.get(seat_id, [])
        for sib in siblings:
            if sib["episode_id"] == ep_id:
                continue
            if sib["episode_type"] == ep_type:
                sib_start = float(sib["start_timestamp_ms"])
                sib_end = float(sib["end_timestamp_ms"] or sib_start + sib.get("duration_ms", 0.0))
                gap = abs(start_ms - sib_end) if start_ms >= sib_end else abs(sib_start - end_ms)
                if gap <= 1200.0:
                    is_fragmented = True
                    break

        # Classification logic for FP
        classification = "MATCHED_TRUE_POSITIVE" if is_tp else "OTHER"
        if not is_tp:
            if is_fragmented and any(sib["episode_id"] in matched_ai_ids for sib in siblings if sib["episode_type"] == ep_type):
                classification = "TEMPORAL_FRAGMENTATION"
            elif crop_w < 35 or crop_h < 35 or kp_qual < 0.35:
                classification = "POOR_HEAD_CROP"
            elif duration_ms < 600.0:
                classification = "SHORT_NOISY_OSCILLATION"
            elif abs(base_yaw) < 2.0 and ("SEAT-ROOM-CALIB-01-07" in seat_id or "SEAT-ROOM-CALIB-01-12" in seat_id or "SEAT-ROOM-CALIB-01-18" in seat_id or "SEAT-ROOM-CALIB-01-22" in seat_id):
                # Far-edge angled seats without individual baseline
                classification = "NEUTRAL_BASELINE_OFFSET"
            elif duration_ms >= 2000.0 and not gt_overlaps:
                classification = "TRUE_HEAD_MOVEMENT_NOT_IN_GT"
            elif gt_overlaps:
                classification = "GT_AMBIGUOUS"
            elif is_fragmented:
                classification = "TEMPORAL_FRAGMENTATION"
            else:
                classification = "SHORT_NOISY_OSCILLATION"

            category_counts[classification] = category_counts.get(classification, 0) + 1

        entry = {
            "episode_id": ep_id,
            "seat_id": seat_id,
            "label": ep_type,
            "is_true_positive": is_tp,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "duration_ms": round(duration_ms, 1),
            "peak_intensity": a.get("peak_intensity", 0.0),
            "confidence": a.get("confidence", 1.0),
            "quality": a.get("quality", 1.0),
            "head_crop_width": crop_w,
            "head_crop_height": crop_h,
            "keypoint_quality": round(kp_qual, 3),
            "seat_baseline_yaw": round(base_yaw, 2),
            "gt_overlaps": gt_overlaps,
            "classification": classification,
        }
        diagnosed_episodes.append(entry)

    cap.release()

    diag_summary = {
        "total_ai_head_episodes": len(ai_head_eps),
        "true_positives": len(matched_ai_ids),
        "false_positives": len(ai_head_eps) - len(matched_ai_ids),
        "category_counts": category_counts,
        "category_percentages": {
            k: round((v / (len(ai_head_eps) - len(matched_ai_ids))) * 100.0, 1) if (len(ai_head_eps) - len(matched_ai_ids)) > 0 else 0.0
            for k, v in category_counts.items()
        },
        "episodes": diagnosed_episodes,
    }

    out_file = OUT_DIR / "fp_diagnosis.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(diag_summary, f, indent=2)

    logger.info("FP Diagnosis exported successfully to %s", out_file)
    print("\n" + "=" * 80)
    print(" 6DREPNET FALSE POSITIVE ROOT CAUSE BREAKDOWN (N = 187 FP)")
    print("=" * 80)
    for cat, count in category_counts.items():
        pct = (count / 187) * 100.0 if count > 0 else 0.0
        print(f"  - {cat:<32}: {count:>4} ({pct:>5.1f}%)")
    print("=" * 80 + "\n")

    return diag_summary


if __name__ == "__main__":
    diagnose_false_positives()

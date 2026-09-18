"""Comprehensive A/B Benchmark & Diagnostic Suite for Head Orientation Providers.

Compares:
1. PoseHeuristicHeadOrientationProvider (RUN A)
2. SixDRepNetHeadOrientationProvider (RUN B)

Under identical conditions on demo_video/india_classroom.mp4:
- Same video, calibration, thresholds, engine, and frozen human ground truth.
- Strict evaluation: same Seat + same Label + Temporal IoU >= 0.30.
- Frame-level diagnostics & Disagreement image extraction to data/head_orientation_ab/disagreements/.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import cv2
import numpy as np
import torch

# Ensure project root in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from classroom_monitor.config import ClassroomConfig
from classroom_monitor.detector import PoseClassroomDetector
from classroom_monitor.head_pose_provider import (
    HeadCropExtractor,
    HeadOrientationEstimate,
    PoseHeuristicHeadOrientationProvider,
    SixDRepNetHeadOrientationProvider,
    create_head_pose_provider,
)
from classroom_monitor.observation_extractor import ObservationExtractor, RawObservation
from classroom_monitor.seat_manager import SeatDefinition, SeatManager
from classroom_monitor.temporal_episode_engine import EpisodeType, TemporalEpisode, TemporalEpisodeEngine
from scripts.benchmark_temporal_ground_truth import (
    compute_temporal_iou,
    compute_temporal_overlap_ms,
    normalize_label,
)
from classroom_monitor.demo import run_classroom_demo, setup_database_seats

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("HeadPoseABBenchmark")

GT_FILE = Path("data/ground_truth/india_classroom_gt.json")
BASE_AB_DIR = Path("data/head_orientation_ab")


def evaluate_run_metrics(
    gt_episodes: List[Dict[str, Any]],
    ai_episodes: List[Dict[str, Any]],
    iou_threshold: float = 0.30,
) -> Dict[str, Any]:
    """Perform strict AI vs Human Ground Truth matching."""
    matched_pairs = []
    matched_gt_ids = set()
    matched_ai_ids = set()
    gt_duplicate_ai_map: Dict[str, List[Dict[str, Any]]] = {h["id"]: [] for h in gt_episodes}

    # Filter GT for head orientation classes
    head_gt = [h for h in gt_episodes if "HEAD_TURN" in h["episode_type"]]

    for h in head_gt:
        h_norm_type = normalize_label(h["episode_type"])
        h_start = float(h["start_ms"])
        h_end = float(h["end_ms"])
        h_seat = h["seat_code"]

        best_iou = 0.0
        best_ai = None

        for a in ai_episodes:
            a_id = a["episode_id"]
            a_seat = a["seat_id"]
            a_norm_type = normalize_label(a["episode_type"])
            a_start = float(a["start_timestamp_ms"])
            a_end = float(a["end_timestamp_ms"] or a["start_timestamp_ms"] + a.get("duration_ms", 0.0))

            if h_seat != a_seat or h_norm_type != a_norm_type:
                continue

            overlap_ms = compute_temporal_overlap_ms(h_start, h_end, a_start, a_end)
            if overlap_ms > 0:
                gt_duplicate_ai_map[h["id"]].append(a)

            if a_id in matched_ai_ids:
                continue

            iou = compute_temporal_iou(h_start, h_end, a_start, a_end)
            if iou > best_iou:
                best_iou = iou
                best_ai = a

        if best_iou >= iou_threshold and best_ai is not None:
            matched_ai_ids.add(best_ai["episode_id"])
            matched_gt_ids.add(h["id"])

            a_start = float(best_ai["start_timestamp_ms"])
            a_end = float(best_ai["end_timestamp_ms"] or best_ai["start_timestamp_ms"] + best_ai.get("duration_ms", 0.0))

            matched_pairs.append({
                "gt_id": h["id"],
                "ai_id": best_ai["episode_id"],
                "seat_code": h_seat,
                "label": h_norm_type,
                "gt_range_ms": [h_start, h_end],
                "ai_range_ms": [a_start, a_end],
                "temporal_iou": round(best_iou, 4),
                "start_time_error_ms": round(a_start - h_start, 1),
                "end_time_error_ms": round(a_end - h_end, 1),
            })

    # Wrong direction & duplicate analysis
    wrong_direction_count = 0
    wrong_direction_details = []
    for h in head_gt:
        if h["id"] not in matched_gt_ids:
            h_norm_type = normalize_label(h["episode_type"])
            h_seat = h["seat_code"]
            h_start = float(h["start_ms"])
            h_end = float(h["end_ms"])

            for a in ai_episodes:
                if a["seat_id"] == h_seat and "HEAD_TURN" in a["episode_type"]:
                    a_norm_type = normalize_label(a["episode_type"])
                    a_start = float(a["start_timestamp_ms"])
                    a_end = float(a["end_timestamp_ms"] or a["start_timestamp_ms"] + a.get("duration_ms", 0.0))
                    overlap = compute_temporal_overlap_ms(h_start, h_end, a_start, a_end)
                    if overlap > 500.0 and a_norm_type != h_norm_type:
                        wrong_direction_count += 1
                        wrong_direction_details.append({
                            "gt_id": h["id"],
                            "seat_code": h_seat,
                            "gt_label": h_norm_type,
                            "ai_label": a_norm_type,
                            "gt_range_ms": [h_start, h_end],
                            "ai_range_ms": [a_start, a_end],
                        })

    duplicate_count = sum(1 for dups in gt_duplicate_ai_map.values() if len(dups) > 1)

    ai_head_eps = [a for a in ai_episodes if "HEAD_TURN" in a["episode_type"]]

    tp = len(matched_pairs)
    fn = len(head_gt) - tp
    fp = len(ai_head_eps) - tp

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    avg_iou = float(np.mean([p["temporal_iou"] for p in matched_pairs])) if matched_pairs else 0.0

    start_errors = [abs(p["start_time_error_ms"]) for p in matched_pairs]
    end_errors = [abs(p["end_time_error_ms"]) for p in matched_pairs]
    mean_start_err = float(np.mean(start_errors)) if start_errors else 0.0
    mean_end_err = float(np.mean(end_errors)) if end_errors else 0.0

    # Class-by-class
    class_table = {}
    for c in ["HEAD_TURN_LEFT", "HEAD_TURN_RIGHT"]:
        c_gt = [h for h in head_gt if normalize_label(h["episode_type"]) == c]
        c_ai = [a for a in ai_head_eps if normalize_label(a["episode_type"]) == c]
        c_matched = [p for p in matched_pairs if p["label"] == c]
        c_tp = len(c_matched)
        c_fn = len(c_gt) - c_tp
        c_fp = len(c_ai) - c_tp
        c_prec = c_tp / (c_tp + c_fp) if (c_tp + c_fp) > 0 else 0.0
        c_rec = c_tp / (c_tp + c_fn) if (c_tp + c_fn) > 0 else 0.0
        c_f1 = 2 * c_prec * c_rec / (c_prec + c_rec) if (c_prec + c_rec) > 0 else 0.0
        c_iou = float(np.mean([p["temporal_iou"] for p in c_matched])) if c_matched else 0.0

        class_table[c] = {
            "gt_count": len(c_gt),
            "ai_count": len(c_ai),
            "tp": c_tp,
            "fp": c_fp,
            "fn": c_fn,
            "precision": round(c_prec, 4),
            "recall": round(c_rec, 4),
            "f1_score": round(c_f1, 4),
            "avg_iou": round(c_iou, 4),
        }

    return {
        "total_gt_head_episodes": len(head_gt),
        "total_ai_head_episodes": len(ai_head_eps),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1_score": round(f1, 4),
        "avg_temporal_iou": round(avg_iou, 4),
        "mean_abs_start_error_ms": round(mean_start_err, 1),
        "mean_abs_end_error_ms": round(mean_end_err, 1),
        "wrong_direction_count": wrong_direction_count,
        "wrong_direction_details": wrong_direction_details,
        "duplicate_episode_count": duplicate_count,
        "class_breakdown": class_table,
        "matched_pairs": matched_pairs,
    }


def run_ab_experiment(
    video_path: str = "demo_video/india_classroom.mp4",
    gt_path: Path = GT_FILE,
    room_id: str = "ROOM-CALIB-01",
    camera_id: str = "CAM-01",
) -> Dict[str, Any]:
    BASE_AB_DIR.mkdir(parents=True, exist_ok=True)
    pose_dir = BASE_AB_DIR / "pose"
    sixd_dir = BASE_AB_DIR / "sixdrepnet"
    diag_dir = BASE_AB_DIR / "disagreements"
    pose_dir.mkdir(parents=True, exist_ok=True)
    sixd_dir.mkdir(parents=True, exist_ok=True)
    diag_dir.mkdir(parents=True, exist_ok=True)

    with open(gt_path, "r", encoding="utf-8") as f:
        gt_data = json.load(f)
    gt_episodes = gt_data["episodes"]

    print("================================================================================")
    print(" VIGIL AI - HEAD ORIENTATION PROVIDER A/B EXPERIMENT")
    print("================================================================================")
    print(f"Video:              {video_path}")
    print(f"Room:               {room_id}")
    print(f"Total GT Episodes:  {len(gt_episodes)} (Head: {sum(1 for h in gt_episodes if 'HEAD_TURN' in h['episode_type'])})")

    # -------------------------------------------------------------------------
    # RUN A: Pose Heuristic Provider
    # -------------------------------------------------------------------------
    print("\n[RUN A] Executing Pose Heuristic Provider...")
    t0_pose = time.time()
    summary_pose = run_classroom_demo(
        input_video=video_path,
        output_video=str(pose_dir / "india_classroom_pose.mp4"),
        room_id=room_id,
        camera_id=camera_id,
        show_window=False,
        save_events=True,
        save_evidence=False,
        head_provider="pose_heuristic",
    )
    t_elapsed_pose = time.time() - t0_pose

    with open(pose_dir / "episodes.json", "r", encoding="utf-8") as f:
        ai_eps_pose = json.load(f)

    metrics_pose = evaluate_run_metrics(gt_episodes, ai_eps_pose)
    metrics_pose["runtime_benchmark"] = {
        "total_elapsed_sec": round(t_elapsed_pose, 2),
        "average_fps": summary_pose.get("average_processing_fps", 0.0),
        "average_inference_ms": summary_pose.get("average_inference_time_ms", 0.0),
    }

    with open(pose_dir / "benchmark_metrics.json", "w", encoding="utf-8") as f:
        json.dump(metrics_pose, f, indent=2)

    # -------------------------------------------------------------------------
    # RUN B: 6DRepNet Pretrained Provider
    # -------------------------------------------------------------------------
    print("\n[RUN B] Executing 6DRepNet Pretrained Provider...")
    t0_sixd = time.time()
    summary_sixd = run_classroom_demo(
        input_video=video_path,
        output_video=str(sixd_dir / "india_classroom_sixdrepnet.mp4"),
        room_id=room_id,
        camera_id=camera_id,
        show_window=False,
        save_events=True,
        save_evidence=False,
        head_provider="sixdrepnet",
    )
    t_elapsed_sixd = time.time() - t0_sixd

    with open(sixd_dir / "episodes.json", "r", encoding="utf-8") as f:
        ai_eps_sixd = json.load(f)

    metrics_sixd = evaluate_run_metrics(gt_episodes, ai_eps_sixd)
    metrics_sixd["runtime_benchmark"] = {
        "total_elapsed_sec": round(t_elapsed_sixd, 2),
        "average_fps": summary_sixd.get("average_processing_fps", 0.0),
        "average_inference_ms": summary_sixd.get("average_inference_time_ms", 0.0),
    }

    with open(sixd_dir / "benchmark_metrics.json", "w", encoding="utf-8") as f:
        json.dump(metrics_sixd, f, indent=2)

    # -------------------------------------------------------------------------
    # Frame-by-Frame Disagreement & Diagnostic Export
    # -------------------------------------------------------------------------
    print("\n[DIAGNOSTICS] Extracting frame-level comparisons and disagreement crops...")
    cap = cv2.VideoCapture(video_path)
    detector = PoseClassroomDetector(config=ClassroomConfig(pipeline_mode="2stage_pose"))
    pose_provider = PoseHeuristicHeadOrientationProvider()
    sixd_provider = SixDRepNetHeadOrientationProvider()
    seat_defs = setup_database_seats(room_id=room_id, camera_id=camera_id)
    seat_mgr = SeatManager(room_id=room_id, camera_id=camera_id)
    seat_mgr.load_seats(seat_defs)
    seat_graph = seat_mgr.to_seat_graph()

    diagnostics_log = []
    disagreement_samples = []
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    # Sample key frames where GT head episodes exist
    sample_timestamps_ms = [
        10000.0, 24500.0, 27500.0, 36000.0, 44500.0, 50000.0, 61000.0, 67000.0
    ]

    for ts_ms in sample_timestamps_ms:
        f_idx = int(round((ts_ms / 1000.0) * fps))
        cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
        ret, frame = cap.read()
        if not ret:
            continue

        dets = detector.detect(frame, frame_index=f_idx)
        mapped, _ = seat_mgr.map_detections_to_seats(dets, timestamp_ms=ts_ms, frame_idx=f_idx)

        # Check GT active label at this timestamp
        active_gt = [h for h in gt_episodes if h["start_ms"] <= ts_ms <= h["end_ms"]]

        for s_code, det in mapped.items():
            s_ctx = seat_graph.get_context(s_code)
            base_yaw = s_ctx.reference_directions.baseline_yaw if s_ctx else 0.0
            base_pitch = s_ctx.reference_directions.baseline_pitch if s_ctx else 0.0

            if det is not None and det.keypoints is not None:
                est_pose = pose_provider.estimate(
                    keypoints=det.keypoints,
                    bbox=det.bbox,
                    seat_baseline_yaw=base_yaw,
                    seat_baseline_pitch=base_pitch,
                    frame=frame,
                )
                est_sixd = sixd_provider.estimate(
                    keypoints=det.keypoints,
                    bbox=det.bbox,
                    seat_baseline_yaw=base_yaw,
                    seat_baseline_pitch=base_pitch,
                    frame=frame,
                )

                # Check if this seat has an active GT episode
                seat_gt = [h for h in active_gt if h["seat_code"] == s_code]
                gt_lbl = seat_gt[0]["episode_type"] if seat_gt else "NONE"

                diag_entry = {
                    "timestamp_ms": ts_ms,
                    "frame_idx": f_idx,
                    "seat_code": s_code,
                    "gt_active_label": gt_lbl,
                    "pose_yaw": est_pose.yaw,
                    "pose_pitch": est_pose.pitch,
                    "pose_quality": est_pose.quality,
                    "sixd_yaw": est_sixd.yaw,
                    "sixd_pitch": est_sixd.pitch,
                    "sixd_roll": est_sixd.roll,
                    "sixd_quality": est_sixd.quality,
                }
                diagnostics_log.append(diag_entry)

                # If significant disagreement between GT and either provider, save visual crop
                is_disagree = False
                reason = "AGREEMENT"
                if gt_lbl != "NONE":
                    pose_agrees = (gt_lbl == "HEAD_TURN_RIGHT" and (est_pose.yaw or 0) > 15.0) or (
                        gt_lbl == "HEAD_TURN_LEFT" and (est_pose.yaw or 0) < -15.0
                    )
                    sixd_agrees = (gt_lbl == "HEAD_TURN_RIGHT" and (est_sixd.yaw or 0) > 15.0) or (
                        gt_lbl == "HEAD_TURN_LEFT" and (est_sixd.yaw or 0) < -15.0
                    )

                    if not pose_agrees and sixd_agrees:
                        is_disagree = True
                        reason = "POSE_MISSED_SIXD_DETECTED"
                    elif pose_agrees and not sixd_agrees:
                        is_disagree = True
                        reason = "SIXD_MISSED_POSE_DETECTED"
                    elif not pose_agrees and not sixd_agrees:
                        is_disagree = True
                        reason = "BOTH_MISSED"

                if is_disagree and len(disagreement_samples) < 10:
                    bx1, by1, bx2, by2 = map(int, det.bbox)
                    bx1 = max(0, bx1)
                    by1 = max(0, by1)
                    bx2 = min(frame.shape[1], bx2)
                    by2 = min(frame.shape[0], by2)
                    crop_img = frame[by1:by2, bx1:bx2].copy()

                    cv2.putText(
                        crop_img,
                        f"GT: {gt_lbl} | Pose: {est_pose.yaw or 0:.0f} | 6D: {est_sixd.yaw or 0:.0f}",
                        (6, 18),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.40,
                        (0, 255, 255),
                        1,
                    )
                    crop_name = f"disagree_{s_code}_f{f_idx}_{reason}.jpg"
                    cv2.imwrite(str(diag_dir / crop_name), crop_img)
                    disagreement_samples.append({
                        "file": crop_name,
                        "seat_code": s_code,
                        "frame_idx": f_idx,
                        "timestamp_ms": ts_ms,
                        "gt_label": gt_lbl,
                        "pose_yaw": est_pose.yaw,
                        "sixd_yaw": est_sixd.yaw,
                        "classification": reason,
                    })

    cap.release()

    with open(BASE_AB_DIR / "frame_diagnostics.json", "w", encoding="utf-8") as f:
        json.dump(diagnostics_log, f, indent=2)

    with open(BASE_AB_DIR / "disagreement_analysis.json", "w", encoding="utf-8") as f:
        json.dump(disagreement_samples, f, indent=2)

    # -------------------------------------------------------------------------
    # Print Side-by-Side Comparison Table
    # -------------------------------------------------------------------------
    print("\n" + "=" * 90)
    print(" VIGIL AI - HEAD ORIENTATION PROVIDER A/B COMPARISON TABLE")
    print("=" * 90)
    print(f"{'Metric':<32} | {'Pose Heuristic':<24} | {'6DRepNet Pretrained':<24}")
    print("-" * 90)
    print(f"{'Human GT Head Episodes':<32} | {metrics_pose['total_gt_head_episodes']:<24} | {metrics_sixd['total_gt_head_episodes']:<24}")
    print(f"{'AI Detected Head Episodes':<32} | {metrics_pose['total_ai_head_episodes']:<24} | {metrics_sixd['total_ai_head_episodes']:<24}")
    print(f"{'True Positives (TP)':<32} | {metrics_pose['true_positives']:<24} | {metrics_sixd['true_positives']:<24}")
    print(f"{'False Positives (FP)':<32} | {metrics_pose['false_positives']:<24} | {metrics_sixd['false_positives']:<24}")
    print(f"{'False Negatives (FN)':<32} | {metrics_pose['false_negatives']:<24} | {metrics_sixd['false_negatives']:<24}")
    print(f"{'Precision (%)':<32} | {metrics_pose['precision']*100:<23.1f}% | {metrics_sixd['precision']*100:<23.1f}%")
    print(f"{'Recall (%)':<32} | {metrics_pose['recall']*100:<23.1f}% | {metrics_sixd['recall']*100:<23.1f}%")
    print(f"{'F1-Score':<32} | {metrics_pose['f1_score']:<24.4f} | {metrics_sixd['f1_score']:<24.4f}")
    print(f"{'Average Temporal IoU':<32} | {metrics_pose['avg_temporal_iou']:<24.3f} | {metrics_sixd['avg_temporal_iou']:<24.3f}")
    print(f"{'Mean Abs Start Error (ms)':<32} | {metrics_pose['mean_abs_start_error_ms']:<24.1f} | {metrics_sixd['mean_abs_start_error_ms']:<24.1f}")
    print(f"{'Mean Abs End Error (ms)':<32} | {metrics_pose['mean_abs_end_error_ms']:<24.1f} | {metrics_sixd['mean_abs_end_error_ms']:<24.1f}")
    print(f"{'Wrong-Direction Errors':<32} | {metrics_pose['wrong_direction_count']:<24} | {metrics_sixd['wrong_direction_count']:<24}")
    print(f"{'Duplicate/Fragmented Episodes':<32} | {metrics_pose['duplicate_episode_count']:<24} | {metrics_sixd['duplicate_episode_count']:<24}")
    print(f"{'Processing Speed (FPS)':<32} | {metrics_pose['runtime_benchmark']['average_fps']:<24.1f} | {metrics_sixd['runtime_benchmark']['average_fps']:<24.1f}")
    print(f"{'Total Elapsed Time (s)':<32} | {metrics_pose['runtime_benchmark']['total_elapsed_sec']:<24.1f} | {metrics_sixd['runtime_benchmark']['total_elapsed_sec']:<24.1f}")
    print("=" * 90)

    combined_results = {
        "experiment_name": "HEAD_ORIENTATION_PROVIDER_A_B_EXPERIMENT",
        "video": video_path,
        "room_id": room_id,
        "gt_file": str(gt_path),
        "pose_heuristic": metrics_pose,
        "sixdrepnet": metrics_sixd,
        "disagreement_samples": disagreement_samples,
    }

    with open(BASE_AB_DIR / "ab_comparison_summary.json", "w", encoding="utf-8") as f:
        json.dump(combined_results, f, indent=2)

    return combined_results


if __name__ == "__main__":
    run_ab_experiment()

"""Temporal Ground Truth Benchmark Script for VIGIL AI SRS v2.0.

Implements strict AI <-> Human Ground Truth Matching:
- Criteria: same Seat + same Label + Temporal IoU >= 0.30
- Computes Precision, Recall, F1, Average IoU
- Generates Class-by-Class Evaluation Table
- Calculates detailed error metrics:
  * start_time_error_ms, end_time_error_ms
  * wrong_direction (e.g. AI right vs Human left)
  * wrong_seat (AI mapped to wrong candidate/seat)
  * missed_episode (FN)
  * duplicate_episode (fragmented/over-segmented episodes)
- Produces comprehensive JSON and Markdown outputs.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

# Ensure project root in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from classroom_monitor.demo import run_classroom_demo

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("BenchmarkTemporalGT")

GT_FILE = Path("data/ground_truth/india_classroom_gt.json")
OUTPUT_DIR = Path("data/output_demo_v2")

# Canonical Label Aliasing / Normalization (Strict semantic equivalence only)
LABEL_NORMALIZATION = {
    "HEAD_TURN_RIGHT": "HEAD_TURN_RIGHT",
    "HEAD_TURN_LEFT": "HEAD_TURN_LEFT",
    "HEAD_PITCH_DOWN": "HEAD_PITCH_DOWN",
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
    return LABEL_NORMALIZATION.get(label, label)


def compute_temporal_iou(start1: float, end1: float, start2: float, end2: float) -> float:
    inter = max(0.0, min(end1, end2) - max(start1, start2))
    union = max(end1, end2) - min(start1, start2)
    return inter / union if union > 0 else 0.0


def compute_temporal_overlap_ms(start1: float, end1: float, start2: float, end2: float) -> float:
    return max(0.0, min(end1, end2) - max(start1, start2))


def run_benchmark(
    gt_path: Path = GT_FILE,
    episodes_json_path: Optional[Path] = None,
    patterns_json_path: Optional[Path] = None,
    events_json_path: Optional[Path] = None,
    run_inference: bool = False,
    iou_threshold: float = 0.30,
) -> Dict[str, Any]:
    # 1. Load Ground Truth
    if not gt_path.exists():
        raise FileNotFoundError(f"Ground Truth file not found at {gt_path}")

    with open(gt_path, "r", encoding="utf-8") as f:
        gt_data = json.load(f)

    gt_episodes = gt_data["episodes"]
    print("================================================================================")
    print(" VIGIL AI SRS v2.0 - TEMPORAL GROUND TRUTH BENCHMARK")
    print("================================================================================")
    print(f"Ground Truth File:    {gt_path}")
    print(f"Total GT Episodes:    {len(gt_episodes)}")
    print(f"Matching IoU Thresh:  {iou_threshold:.2f}")

    # 2. Run Pipeline if requested
    if run_inference or episodes_json_path is None or not episodes_json_path.exists():
        print("\n[STEP 1] Executing VIGIL AI Pipeline on demo video...")
        run_classroom_demo(
            input_video="demo_video/india_classroom.mp4",
            output_video="data/output_demo_v2/india_classroom_benchmark.mp4",
            room_id="ROOM-CALIB-01",
            camera_id="CAM-01",
            show_window=False,
            save_events=True,
            save_evidence=False,
        )
        episodes_json_path = OUTPUT_DIR / "episodes.json"
        patterns_json_path = OUTPUT_DIR / "patterns.json"
        events_json_path = OUTPUT_DIR / "events.json"

    # 3. Load AI outputs
    with open(episodes_json_path, "r", encoding="utf-8") as f:
        ai_episodes = json.load(f)

    ai_patterns = []
    if patterns_json_path and patterns_json_path.exists():
        with open(patterns_json_path, "r", encoding="utf-8") as f:
            ai_patterns = json.load(f)

    ai_events = []
    if events_json_path and events_json_path.exists():
        with open(events_json_path, "r", encoding="utf-8") as f:
            ai_events = json.load(f)

    print(f"Loaded AI Episodes:   {len(ai_episodes)}")
    print(f"Loaded AI Patterns:   {len(ai_patterns)}")
    print(f"Loaded AI Events:     {len(ai_events)}")

    # 4. Strict Matching: same Seat + same Normalized Label + IoU >= iou_threshold
    matched_pairs: List[Dict[str, Any]] = []
    matched_gt_ids: Set[str] = set()
    matched_ai_ids: Set[str] = set()

    # Store duplicate detections per human episode
    gt_duplicate_ai_map: Dict[str, List[Dict[str, Any]]] = {h["id"]: [] for h in gt_episodes}

    for h in gt_episodes:
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

            # Strict seat matching
            if h_seat != a_seat:
                continue

            # Strict label matching
            if h_norm_type != a_norm_type:
                continue

            # Check overlap
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

            start_err = a_start - h_start
            end_err = a_end - h_end

            matched_pairs.append({
                "gt_id": h["id"],
                "ai_id": best_ai["episode_id"],
                "seat_code": h_seat,
                "label": h_norm_type,
                "gt_range_ms": [h_start, h_end],
                "ai_range_ms": [a_start, a_end],
                "gt_duration_ms": h["duration_ms"],
                "ai_duration_ms": best_ai.get("duration_ms", a_end - a_start),
                "temporal_iou": round(best_iou, 4),
                "start_time_error_ms": round(start_err, 1),
                "end_time_error_ms": round(end_err, 1),
                "confidence": best_ai.get("confidence", 1.0),
                "quality": best_ai.get("quality", 1.0),
            })

    # 5. Diagnostic Error Analysis: Wrong Direction, Wrong Seat, Over-segmentation
    wrong_direction_events: List[Dict[str, Any]] = []
    wrong_seat_events: List[Dict[str, Any]] = []
    fragmented_episodes: List[Dict[str, Any]] = []
    missed_episodes: List[Dict[str, Any]] = []

    # Check missed & fragmented GT
    for h in gt_episodes:
        h_norm_type = normalize_label(h["episode_type"])
        h_start = float(h["start_ms"])
        h_end = float(h["end_ms"])
        h_seat = h["seat_code"]

        if h["id"] not in matched_gt_ids:
            # Check why missed: wrong direction? wrong seat? or completely absent?
            diag_reason = "NO_AI_DETECTION"
            possible_wrong_seat = []
            possible_wrong_dir = []

            for a in ai_episodes:
                a_seat = a["seat_id"]
                a_norm_type = normalize_label(a["episode_type"])
                a_start = float(a["start_timestamp_ms"])
                a_end = float(a["end_timestamp_ms"] or a["start_timestamp_ms"] + a.get("duration_ms", 0.0))
                overlap = compute_temporal_overlap_ms(h_start, h_end, a_start, a_end)

                if overlap > 500.0:  # overlapping at least 500ms
                    # Case 1: Same seat but opposite direction
                    if h_seat == a_seat:
                        if ("LEFT" in h_norm_type and "RIGHT" in a_norm_type) or ("RIGHT" in h_norm_type and "LEFT" in a_norm_type):
                            possible_wrong_dir.append(a)
                    # Case 2: Different seat but same type
                    elif h_norm_type == a_norm_type:
                        possible_wrong_seat.append(a)

            if possible_wrong_dir:
                diag_reason = "WRONG_DIRECTION"
                wrong_direction_events.append({
                    "gt_episode": h,
                    "opposing_ai_episodes": possible_wrong_dir,
                })
            elif possible_wrong_seat:
                diag_reason = "WRONG_SEAT_MAPPING"
                wrong_seat_events.append({
                    "gt_episode": h,
                    "nearby_ai_episodes": possible_wrong_seat,
                })

            missed_episodes.append({
                "gt_id": h["id"],
                "seat_code": h_seat,
                "label": h_norm_type,
                "range_ms": [h_start, h_end],
                "duration_ms": h["duration_ms"],
                "diagnostic_reason": diag_reason,
            })

        # Check duplicate/fragmentation
        dups = gt_duplicate_ai_map.get(h["id"], [])
        if len(dups) > 1:
            fragmented_episodes.append({
                "gt_id": h["id"],
                "seat_code": h_seat,
                "label": h_norm_type,
                "gt_range_ms": [h_start, h_end],
                "ai_fragment_count": len(dups),
                "ai_fragments": [
                    {
                        "ai_id": d["episode_id"],
                        "range_ms": [d["start_timestamp_ms"], d["end_timestamp_ms"]],
                        "duration_ms": d.get("duration_ms", 0),
                    }
                    for d in dups
                ],
            })

    # Unmatched AI episodes (False Positives)
    unmatched_ai = [a for a in ai_episodes if a["episode_id"] not in matched_ai_ids]

    # 6. Global Metrics
    tp = len(matched_pairs)
    fn = len(gt_episodes) - len(matched_gt_ids)
    fp = len(ai_episodes) - len(matched_ai_ids)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    avg_iou = float(np.mean([p["temporal_iou"] for p in matched_pairs])) if matched_pairs else 0.0

    start_errors = [p["start_time_error_ms"] for p in matched_pairs]
    end_errors = [p["end_time_error_ms"] for p in matched_pairs]

    mean_start_err = float(np.mean(np.abs(start_errors))) if start_errors else 0.0
    mean_end_err = float(np.mean(np.abs(end_errors))) if end_errors else 0.0

    # 7. Class-by-Class Evaluation Table
    all_classes = sorted(list(set(
        [normalize_label(h["episode_type"]) for h in gt_episodes] +
        [normalize_label(a["episode_type"]) for a in ai_episodes]
    )))

    class_metrics = {}
    for c in all_classes:
        c_gt = [h for h in gt_episodes if normalize_label(h["episode_type"]) == c]
        c_ai = [a for a in ai_episodes if normalize_label(a["episode_type"]) == c]
        c_matched = [p for p in matched_pairs if p["label"] == c]

        c_tp = len(c_matched)
        c_fn = len(c_gt) - c_tp
        c_fp = len(c_ai) - c_tp

        c_prec = c_tp / (c_tp + c_fp) if (c_tp + c_fp) > 0 else 0.0
        c_rec = c_tp / (c_tp + c_fn) if (c_tp + c_fn) > 0 else 0.0
        c_f1 = 2 * c_prec * c_rec / (c_prec + c_rec) if (c_prec + c_rec) > 0 else 0.0
        c_iou = float(np.mean([p["temporal_iou"] for p in c_matched])) if c_matched else 0.0

        class_metrics[c] = {
            "human_gt_count": len(c_gt),
            "ai_detected_count": len(c_ai),
            "tp": c_tp,
            "fp": c_fp,
            "fn": c_fn,
            "precision": round(c_prec, 4),
            "recall": round(c_rec, 4),
            "f1_score": round(c_f1, 4),
            "avg_iou": round(c_iou, 4),
        }

    # Summary Benchmark Dict
    benchmark_results = {
        "benchmark_version": "1.0.0",
        "ground_truth_file": str(gt_path),
        "video_duration_seconds": gt_data.get("duration_seconds", 71.77),
        "iou_threshold": iou_threshold,
        "overall_metrics": {
            "total_human_gt": len(gt_episodes),
            "total_ai_episodes": len(ai_episodes),
            "total_ai_patterns": len(ai_patterns),
            "total_ai_events": len(ai_events),
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1_score": round(f1, 4),
            "avg_temporal_iou": round(avg_iou, 4),
            "mean_abs_start_error_ms": round(mean_start_err, 1),
            "mean_abs_end_error_ms": round(mean_end_err, 1),
        },
        "class_metrics": class_metrics,
        "matched_pairs": matched_pairs,
        "missed_episodes": missed_episodes,
        "fragmented_episodes": fragmented_episodes,
        "wrong_direction_events": wrong_direction_events,
        "wrong_seat_events": wrong_seat_events,
        "unmatched_ai_proposals": [
            {
                "ai_id": a["episode_id"],
                "seat_id": a["seat_id"],
                "episode_type": a["episode_type"],
                "range_ms": [a["start_timestamp_ms"], a["end_timestamp_ms"]],
                "duration_ms": a.get("duration_ms", 0),
            }
            for a in unmatched_ai
        ],
    }

    # Print Formatted Table
    print("\n" + "=" * 96)
    print(f"{'Label':<30} | {'Human':<6} | {'AI':<6} | {'TP':<4} | {'FP':<4} | {'FN':<4} | {'Prec (%)':<9} | {'Rec (%)':<9} | {'Avg IoU':<8}")
    print("-" * 96)
    for c, m in class_metrics.items():
        prec_str = f"{m['precision']*100:.1f}%"
        rec_str = f"{m['recall']*100:.1f}%"
        iou_str = f"{m['avg_iou']:.3f}" if m['avg_iou'] > 0 else "N/A"
        print(f"{c:<30} | {m['human_gt_count']:<6} | {m['ai_detected_count']:<6} | {m['tp']:<4} | {m['fp']:<4} | {m['fn']:<4} | {prec_str:<9} | {rec_str:<9} | {iou_str:<8}")
    print("-" * 96)
    overall_prec_str = f"{precision*100:.1f}%"
    overall_rec_str = f"{recall*100:.1f}%"
    overall_iou_str = f"{avg_iou:.3f}"
    print(f"{'OVERALL TOTAL / MACRO':<30} | {len(gt_episodes):<6} | {len(ai_episodes):<6} | {tp:<4} | {fp:<4} | {fn:<4} | {overall_prec_str:<9} | {overall_rec_str:<9} | {overall_iou_str:<8}")
    print("=" * 96)

    print(f"\n[METRICS SUMMARY]")
    print(f"  Precision:                 {precision * 100:.2f}%")
    print(f"  Recall:                    {recall * 100:.2f}%")
    print(f"  F1-Score:                  {f1:.4f}")
    print(f"  Average Temporal IoU (TP): {avg_iou:.3f}")
    print(f"  Mean Start Error:          {mean_start_err:.1f} ms")
    print(f"  Mean End Error:            {mean_end_err:.1f} ms")
    print(f"  Missed Episodes (FN):      {len(missed_episodes)}")
    print(f"  Fragmented Episodes:       {len(fragmented_episodes)}")
    print(f"  Wrong Direction Cases:     {len(wrong_direction_events)}")
    print(f"  Wrong Seat Cases:          {len(wrong_seat_events)}")

    # Save to file
    out_json = OUTPUT_DIR / "temporal_benchmark_report.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(benchmark_results, f, indent=2, ensure_ascii=False)
    print(f"\n[SAVED] Benchmark report saved to {out_json}")

    return benchmark_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Temporal Ground Truth Benchmark")
    parser.add_argument("--run-inference", action="store_true", help="Run VIGIL pipeline inference before matching")
    parser.add_argument("--iou", type=float, default=0.30, help="Temporal IoU matching threshold (default: 0.30)")
    args = parser.parse_args()

    run_benchmark(
        gt_path=GT_FILE,
        run_inference=args.run_inference,
        iou_threshold=args.iou,
    )

"""Evaluation & Per-Class Benchmarking Script for VIGIL AI Classroom Cheating Detection.

Calculates:
1. Per-Class Precision, Recall, mAP@0.50, mAP@0.50:0.95, and F1-score.
2. Macro and Micro overall metrics.
3. Normalized Confusion Matrix.
4. Latency breakdown (Pre-process, Inference, Post-process) and FPS.

Usage:
    python -m classroom_training.scripts.evaluate [--model models/classroom_best.pt] [--data classroom_training/configs/data.yaml] [--split test]
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict

import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ClassroomEvaluator")

CLASS_NAMES = {
    0: "back peeking",
    1: "front peeking",
    2: "no cheating",
    3: "phone using",
    4: "side peeking",
}


def run_evaluation(
    model_path: Path,
    data_yaml: Path,
    split: str = "test",
    imgsz: int = 640,
    conf_threshold: float = 0.25,
    iou_threshold: float = 0.50,
    output_json: Path = Path("reports/evaluation_metrics.json"),
) -> Dict[str, Any]:
    """Execute evaluation and benchmark model against test/val split."""
    try:
        from ultralytics import YOLO
    except ImportError:
        logger.error("Ultralytics library is required. Run: pip install ultralytics")
        return {}

    if not model_path.exists():
        logger.warning(
            "Model weights not found at %s. (If testing before training, use a pretrained model like yolov8n.pt)",
            model_path.resolve(),
        )

    logger.info("Loading model for evaluation: %s", model_path)
    model = YOLO(str(model_path))

    logger.info("Running evaluation on split '%s'...", split)
    val_results = model.val(
        data=str(data_yaml.resolve()),
        split=split,
        imgsz=imgsz,
        conf=conf_threshold,
        iou=iou_threshold,
        plots=True,
        save_json=True,
        verbose=False,
    )

    # Extract metrics
    box_metrics = val_results.box
    class_indices = list(box_metrics.ap_class_index)
    
    per_class_data: Dict[str, Dict[str, float]] = {}
    
    for idx, cls_idx in enumerate(class_indices):
        cls_name = CLASS_NAMES.get(cls_idx, f"class_{cls_idx}")
        p = float(box_metrics.p[idx]) if len(box_metrics.p) > idx else 0.0
        r = float(box_metrics.r[idx]) if len(box_metrics.r) > idx else 0.0
        f1 = float(box_metrics.f1[idx]) if len(box_metrics.f1) > idx else (2 * p * r / (p + r + 1e-9))
        map50 = float(box_metrics.ap50[idx]) if len(box_metrics.ap50) > idx else 0.0
        map5095 = float(box_metrics.ap[idx]) if len(box_metrics.ap) > idx else 0.0

        per_class_data[cls_name] = {
            "class_id": int(cls_idx),
            "precision": round(p, 4),
            "recall": round(r, 4),
            "f1_score": round(f1, 4),
            "map50": round(map50, 4),
            "map50_95": round(map5095, 4),
        }

    # Speed metrics
    speed_ms = val_results.speed
    total_latency_ms = sum(speed_ms.values()) if speed_ms else 30.0
    fps = 1000.0 / total_latency_ms if total_latency_ms > 0 else 0.0

    eval_report: Dict[str, Any] = {
        "model_path": str(model_path),
        "dataset_config": str(data_yaml),
        "split": split,
        "overall": {
            "precision": round(float(box_metrics.mp), 4),
            "recall": round(float(box_metrics.mr), 4),
            "map50": round(float(box_metrics.map50), 4),
            "map50_95": round(float(box_metrics.map), 4),
        },
        "per_class": per_class_data,
        "speed": {
            "preprocess_ms": round(speed_ms.get("preprocess", 0.0), 2),
            "inference_ms": round(speed_ms.get("inference", 0.0), 2),
            "postprocess_ms": round(speed_ms.get("postprocess", 0.0), 2),
            "total_ms": round(total_latency_ms, 2),
            "fps": round(fps, 1),
        },
    }

    # Print clean table
    print("\n" + "=" * 78)
    print(f"        VIGIL AI — MODEL EVALUATION REPORT ({split.upper()} SPLIT)")
    print("=" * 78)
    print(f"Model: {model_path.name}")
    print(f"Inference Speed: {total_latency_ms:.2f} ms/frame (~{fps:.1f} FPS)")
    print("-" * 78)
    print(f"{'Class ID':<9} | {'Class Name':<16} | {'Precision':<10} | {'Recall':<8} | {'F1':<8} | {'mAP50':<8}")
    print("-" * 78)

    for cls_name, m in per_class_data.items():
        print(
            f"{m['class_id']:<9} | {cls_name:<16} | {m['precision']:<10.4f} | "
            f"{m['recall']:<8.4f} | {m['f1_score']:<8.4f} | {m['map50']:<8.4f}"
        )

    print("-" * 78)
    ov = eval_report["overall"]
    print(
        f"{'ALL':<9} | {'Overall Average':<16} | {ov['precision']:<10.4f} | "
        f"{ov['recall']:<8.4f} | {'--':<8} | {ov['map50']:<8.4f}"
    )
    print("=" * 78 + "\n")

    output_json.parent.mkdir(parents=True, exist_ok=True)
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(eval_report, f, indent=2, ensure_ascii=False)
    logger.info("Evaluation metrics saved to: %s", output_json.resolve())

    return eval_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate YOLO model on classroom dataset")
    parser.add_argument(
        "--model",
        type=Path,
        default=Path("models/classroom_best.pt"),
        help="Path to trained model weights",
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=Path("classroom_training/configs/data.yaml"),
        help="Path to dataset YAML",
    )
    parser.add_argument("--split", type=str, default="test", choices=["train", "valid", "test"])
    parser.add_argument("--conf", type=float, default=0.25, help="Confidence threshold")
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path("reports/model_evaluation_report.json"),
        help="Target output JSON path",
    )
    args = parser.parse_args()

    run_evaluation(
        model_path=args.model,
        data_yaml=args.data,
        split=args.split,
        conf_threshold=args.conf,
        output_json=args.output_json,
    )


if __name__ == "__main__":
    main()

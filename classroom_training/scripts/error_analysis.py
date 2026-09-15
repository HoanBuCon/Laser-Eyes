"""Error Analysis & Failure Case Diagnostics for VIGIL AI Classroom Model.

Identifies, categorizes, and visually exports:
1. False Positives (Detected non-existent or normal behavior as cheating).
2. False Negatives (Missed critical cheating behaviors like phone or back peeking).
3. Class Confusions (e.g. Front Peeking vs. Side Peeking mixups).

Usage:
    python -m classroom_training.scripts.error_analysis [--model models/classroom_best.pt] [--split test]
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

import cv2
import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ErrorAnalyzer")

CLASS_NAMES = {
    0: "back peeking",
    1: "front peeking",
    2: "no cheating",
    3: "phone using",
    4: "side peeking",
}


def compute_iou(box1: List[float], box2: List[float]) -> float:
    """Compute IoU between [x1, y1, x2, y2] boxes."""
    xa = max(box1[0], box2[0])
    ya = max(box1[1], box2[1])
    xb = min(box1[2], box2[2])
    yb = min(box1[3], box2[3])

    inter_area = max(0.0, xb - xa) * max(0.0, yb - ya)
    area1 = max(0.0, box1[2] - box1[0]) * max(0.0, box1[3] - box1[1])
    area2 = max(0.0, box2[2] - box2[0]) * max(0.0, box2[3] - box2[1])

    union_area = area1 + area2 - inter_area
    return inter_area / union_area if union_area > 0 else 0.0


def parse_ground_truth(label_path: Path, img_w: int, img_h: int) -> List[Dict[str, Any]]:
    """Load normalized YOLO txt labels into pixel bbox records."""
    if not label_path.exists():
        return []
    records = []
    content = label_path.read_text(encoding="utf-8").strip()
    if not content:
        return []
    for line in content.splitlines():
        parts = line.strip().split()
        if len(parts) == 5:
            try:
                cls_id = int(parts[0])
                xc, yc, w, h = map(float, parts[1:])
                x1 = (xc - w / 2.0) * img_w
                y1 = (yc - h / 2.0) * img_h
                x2 = (xc + w / 2.0) * img_w
                y2 = (yc + h / 2.0) * img_h
                records.append({
                    "class_id": cls_id,
                    "class_name": CLASS_NAMES.get(cls_id, f"cls_{cls_id}"),
                    "bbox": [x1, y1, x2, y2],
                })
            except ValueError:
                continue
    return records


def analyze_errors(
    model_path: Path,
    dataset_dir: Path,
    split: str = "test",
    iou_thresh: float = 0.40,
    conf_thresh: float = 0.35,
    output_dir: Path = Path("results/error_analysis"),
    output_json: Path = Path("reports/error_analysis_report.json"),
) -> Dict[str, Any]:
    """Inspect model predictions vs ground-truth annotations."""
    try:
        from ultralytics import YOLO
    except ImportError:
        logger.error("Ultralytics required for error analysis")
        return {}

    img_dir = dataset_dir / split / "images"
    lbl_dir = dataset_dir / split / "labels"

    if not img_dir.exists():
        logger.error("Images path does not exist: %s", img_dir)
        return {}

    logger.info("Loading model weights from: %s", model_path)
    model = YOLO(str(model_path))

    fp_dir = output_dir / "false_positives"
    fn_dir = output_dir / "false_negatives"
    wc_dir = output_dir / "wrong_class"
    for d in (fp_dir, fn_dir, wc_dir):
        d.mkdir(parents=True, exist_ok=True)

    image_files = sorted(list(img_dir.glob("*.jpg")) + list(img_dir.glob("*.png")))
    logger.info("Analyzing %d test images for error patterns...", len(image_files))

    fp_count = 0
    fn_count = 0
    wc_count = 0
    correct_count = 0

    confusion_pairs: Dict[str, int] = {}

    for img_path in image_files:
        img = cv2.imread(str(img_path))
        if img is None:
            continue
        h, w = img.shape[:2]
        lbl_path = lbl_dir / f"{img_path.stem}.txt"
        gt_boxes = parse_ground_truth(lbl_path, w, h)

        # Run inference
        results = model(img, conf=conf_thresh, verbose=False)[0]
        pred_boxes = []
        for box in results.boxes:
            b_cls = int(box.cls[0])
            b_conf = float(box.conf[0])
            xyxy = box.xyxy[0].tolist()
            pred_boxes.append({
                "class_id": b_cls,
                "class_name": CLASS_NAMES.get(b_cls, f"cls_{b_cls}"),
                "confidence": b_conf,
                "bbox": xyxy,
            })

        matched_gt = set()
        matched_pred = set()

        # Match preds to GTs
        for p_idx, p in enumerate(pred_boxes):
            best_iou = 0.0
            best_gt_idx = -1
            for g_idx, g in enumerate(gt_boxes):
                if g_idx in matched_gt:
                    continue
                iou = compute_iou(p["bbox"], g["bbox"])
                if iou > best_iou:
                    best_iou = iou
                    best_gt_idx = g_idx

            if best_iou >= iou_thresh and best_gt_idx >= 0:
                matched_gt.add(best_gt_idx)
                matched_pred.add(p_idx)
                gt = gt_boxes[best_gt_idx]
                if p["class_id"] == gt["class_id"]:
                    correct_count += 1
                else:
                    wc_count += 1
                    pair_key = f"GT: {gt['class_name']} -> PRED: {p['class_name']}"
                    confusion_pairs[pair_key] = confusion_pairs.get(pair_key, 0) + 1

                    # Save sample wrong class image
                    if wc_count <= 20:
                        dbg_img = img.copy()
                        cv2.rectangle(
                            dbg_img,
                            (int(gt["bbox"][0]), int(gt["bbox"][1])),
                            (int(gt["bbox"][2]), int(gt["bbox"][3])),
                            (0, 255, 0),
                            2,
                        )
                        cv2.rectangle(
                            dbg_img,
                            (int(p["bbox"][0]), int(p["bbox"][1])),
                            (int(p["bbox"][2]), int(p["bbox"][3])),
                            (0, 0, 255),
                            2,
                        )
                        cv2.imwrite(str(wc_dir / f"wc_{img_path.stem}.jpg"), dbg_img)

        # Unmatched preds are False Positives
        for p_idx, p in enumerate(pred_boxes):
            if p_idx not in matched_pred:
                fp_count += 1

        # Unmatched GTs are False Negatives
        for g_idx, g in enumerate(gt_boxes):
            if g_idx not in matched_gt:
                fn_count += 1
                if fn_count <= 20:
                    dbg_img = img.copy()
                    cv2.rectangle(
                        dbg_img,
                        (int(g["bbox"][0]), int(g["bbox"][1])),
                        (int(g["bbox"][2]), int(g["bbox"][3])),
                        (0, 255, 255),
                        2,
                    )
                    cv2.imwrite(str(fn_dir / f"fn_{img_path.stem}.jpg"), dbg_img)

    report: Dict[str, Any] = {
        "model_path": str(model_path),
        "split_analyzed": split,
        "images_evaluated": len(image_files),
        "summary": {
            "correct_detections": correct_count,
            "wrong_class_confusions": wc_count,
            "false_positives": fp_count,
            "false_negatives": fn_count,
        },
        "top_confusion_pairs": dict(
            sorted(confusion_pairs.items(), key=lambda item: item[1], reverse=True)[:10]
        ),
    }

    print("\n" + "=" * 70)
    print("        VIGIL AI — ERROR ANALYSIS & DIAGNOSTICS REPORT")
    print("=" * 70)
    print(f"Correct Detections     : {correct_count:,}")
    print(f"Wrong Class Mixups     : {wc_count:,}")
    print(f"False Positives (Ghost): {fp_count:,}")
    print(f"False Negatives (Miss) : {fn_count:,}")
    print("-" * 70)
    print("Top Misclassification Confusion Pairs:")
    for pair, cnt in report["top_confusion_pairs"].items():
        print(f"  * {pair:<45} : {cnt} times")
    print("=" * 70 + "\n")

    output_json.parent.mkdir(parents=True, exist_ok=True)
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    logger.info("Error analysis report saved to: %s", output_json.resolve())

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze YOLO model errors and failure modes")
    parser.add_argument("--model", type=Path, default=Path("models/classroom_best.pt"))
    parser.add_argument("--dataset-dir", type=Path, default=Path("dataset"))
    parser.add_argument("--split", type=str, default="test")
    parser.add_argument("--output-dir", type=Path, default=Path("results/error_analysis"))
    parser.add_argument("--output-json", type=Path, default=Path("reports/error_analysis_report.json"))
    args = parser.parse_args()

    analyze_errors(
        model_path=args.model,
        dataset_dir=args.dataset_dir,
        split=args.split,
        output_dir=args.output_dir,
        output_json=args.output_json,
    )


if __name__ == "__main__":
    main()

"""Visual Bounding Box Validator for VIGIL AI Classroom Cheating Dataset.

Samples images from train/valid/test splits, renders ground-truth bounding boxes
with class-specific color coding, and saves them to a visual audit directory.

Usage:
    python -m classroom_training.scripts.visualize_labels [--dataset-dir dataset] [--samples-per-class 5] [--output-dir results/dataset_check]
"""

from __future__ import annotations

import argparse
import logging
import random
from pathlib import Path
from typing import Dict, List, Tuple

import cv2
import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("VisualValidator")

CLASS_NAMES = {
    0: "back peeking",
    1: "front peeking",
    2: "no cheating",
    3: "phone using",
    4: "side peeking",
}

# Distinct BGR Colors for each class
CLASS_COLORS = {
    0: (0, 0, 255),      # Red for back peeking (High Suspicion)
    1: (0, 165, 255),    # Orange for front peeking (Medium Suspicion)
    2: (0, 200, 0),      # Green for no cheating (Normal)
    3: (255, 0, 255),    # Magenta for phone using (Severe/Prohibited)
    4: (0, 255, 255),    # Yellow for side peeking (Medium Suspicion)
}


def draw_yolo_labels(image: np.ndarray, label_path: Path) -> Tuple[np.ndarray, List[int]]:
    """Draw YOLO format bounding boxes onto an image."""
    h_img, w_img = image.shape[:2]
    annotated = image.copy()
    classes_present: List[int] = []

    if not label_path.exists():
        return annotated, classes_present

    content = label_path.read_text(encoding="utf-8").strip()
    if not content:
        return annotated, classes_present

    for line in content.splitlines():
        parts = line.strip().split()
        if len(parts) != 5:
            continue
        try:
            cls_id = int(parts[0])
            xc, yc, w, h = map(float, parts[1:])
        except ValueError:
            continue

        classes_present.append(cls_id)
        # Convert normalized coordinates to absolute pixels
        x1 = int((xc - w / 2.0) * w_img)
        y1 = int((yc - h / 2.0) * h_img)
        x2 = int((xc + w / 2.0) * w_img)
        y2 = int((yc + h / 2.0) * h_img)

        # Clip bounds
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w_img - 1, x2), min(h_img - 1, y2)

        color = CLASS_COLORS.get(cls_id, (255, 255, 255))
        cls_name = CLASS_NAMES.get(cls_id, f"Class {cls_id}")

        # Draw rectangle
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)

        # Draw label background badge
        label_text = f"{cls_name} (ID {cls_id})"
        (tw, th), baseline = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        badge_y1 = max(0, y1 - th - baseline - 4)
        badge_y2 = y1
        badge_x2 = min(w_img, x1 + tw + 6)
        cv2.rectangle(annotated, (x1, badge_y1), (badge_x2, badge_y2), color, -1)
        cv2.putText(
            annotated,
            label_text,
            (x1 + 3, y1 - baseline - 2),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 255, 255) if color != (0, 255, 255) else (0, 0, 0),
            1,
            cv2.LINE_AA,
        )

    return annotated, classes_present


def sample_and_visualize(
    dataset_dir: Path,
    output_dir: Path,
    samples_per_class: int = 5,
    split: str = "train",
) -> None:
    """Sample images containing each class and save visualized results."""
    split_dir = dataset_dir / split
    images_dir = split_dir / "images"
    labels_dir = split_dir / "labels"

    if not images_dir.exists() or not labels_dir.exists():
        logger.error("Split folder invalid: %s", split_dir)
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Indexing dataset split '%s' for class-targeted sampling...", split)

    class_to_files: Dict[int, List[Path]] = {cls_id: [] for cls_id in range(5)}
    label_files = list(labels_dir.glob("*.txt"))

    for lbl in label_files:
        content = lbl.read_text(encoding="utf-8").strip()
        if not content:
            continue
        found_in_file = set()
        for line in content.splitlines():
            parts = line.strip().split()
            if len(parts) == 5:
                try:
                    found_in_file.add(int(parts[0]))
                except ValueError:
                    pass
        for cls_id in found_in_file:
            if cls_id in class_to_files:
                class_to_files[cls_id].append(lbl)

    total_saved = 0
    for cls_id, lbl_list in class_to_files.items():
        cls_name = CLASS_NAMES[cls_id].replace(" ", "_")
        cls_out_dir = output_dir / f"class_{cls_id}_{cls_name}"
        cls_out_dir.mkdir(parents=True, exist_ok=True)

        selected = random.sample(lbl_list, min(samples_per_class, len(lbl_list)))
        logger.info(
            "Class %d ('%s'): sampling %d / %d available files",
            cls_id,
            CLASS_NAMES[cls_id],
            len(selected),
            len(lbl_list),
        )

        for idx, lbl_path in enumerate(selected, start=1):
            stem = lbl_path.stem
            # Find matching image
            img_candidates = list(images_dir.glob(f"{stem}.*"))
            if not img_candidates:
                continue
            img_path = img_candidates[0]
            img = cv2.imread(str(img_path))
            if img is None:
                continue

            vis_img, _ = draw_yolo_labels(img, lbl_path)
            out_file = cls_out_dir / f"sample_{idx:02d}_{stem}.jpg"
            cv2.imwrite(str(out_file), vis_img)
            total_saved += 1

    logger.info("Visual validation completed! Saved %d sample images to: %s", total_saved, output_dir.resolve())


def main() -> None:
    parser = argparse.ArgumentParser(description="Visualize YOLO dataset labels")
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=Path("dataset"),
        help="Path to dataset root folder",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/visualizations"),
        help="Directory to save visual audit images",
    )
    parser.add_argument(
        "--samples-per-class",
        type=int,
        default=5,
        help="Number of image samples to visualize per class",
    )
    parser.add_argument(
        "--split",
        type=str,
        default="train",
        choices=["train", "valid", "test"],
        help="Dataset split to sample from",
    )
    args = parser.parse_args()

    sample_and_visualize(args.dataset_dir, args.output_dir, args.samples_per_class, args.split)


if __name__ == "__main__":
    main()

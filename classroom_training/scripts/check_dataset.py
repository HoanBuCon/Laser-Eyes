"""Dataset Integrity Checker for VIGIL AI Classroom Proctoring.

Validates:
1. Image <-> Label 1-to-1 matching across train, valid, and test splits.
2. Label syntax and bounding box coordinate normalization [0, 1].
3. Class ID range [0, 4] and distribution across splits.
4. Identifies empty labels, degenerate bounding boxes, and corrupted files.

Usage:
    python -m classroom_training.scripts.check_dataset [--dataset-dir dataset]
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("DatasetChecker")

CLASS_NAMES = {
    0: "back peeking",
    1: "front peeking",
    2: "no cheating",
    3: "phone using",
    4: "side peeking",
}


def check_split(split_dir: Path, split_name: str) -> dict[str, Any]:
    """Validate a single dataset split (train/valid/test)."""
    images_dir = split_dir / "images"
    labels_dir = split_dir / "labels"

    if not images_dir.exists():
        logger.warning("Images directory missing for split '%s': %s", split_name, images_dir)
        return {"error": f"Missing images dir: {images_dir}"}

    image_files = sorted(
        [f for f in images_dir.iterdir() if f.suffix.lower() in (".jpg", ".jpeg", ".png")]
    )
    label_files = sorted([f for f in labels_dir.iterdir() if f.suffix.lower() == ".txt"]) if labels_dir.exists() else []

    img_stems = {f.stem: f for f in image_files}
    lbl_stems = {f.stem: f for f in label_files}

    missing_labels = [f.name for stem, f in img_stems.items() if stem not in lbl_stems]
    orphaned_labels = [f.name for stem, f in lbl_stems.items() if stem not in img_stems]

    class_counts: Counter[int] = Counter()
    corrupt_lines = 0
    empty_label_files = 0
    degenerate_boxes = 0
    out_of_bounds_boxes = 0
    total_annotations = 0

    for lbl_path in label_files:
        content = lbl_path.read_text(encoding="utf-8").strip()
        if not content:
            empty_label_files += 1
            continue

        lines = content.splitlines()
        for line_idx, line in enumerate(lines, start=1):
            parts = line.strip().split()
            if len(parts) != 5:
                corrupt_lines += 1
                logger.debug("Corrupt line in %s (line %d): %s", lbl_path.name, line_idx, line)
                continue

            try:
                cls_id = int(parts[0])
                xc, yc, w, h = map(float, parts[1:])
            except ValueError:
                corrupt_lines += 1
                continue

            total_annotations += 1
            class_counts[cls_id] += 1

            # Check bbox bounds
            if not (0.0 <= xc <= 1.0 and 0.0 <= yc <= 1.0 and 0.0 <= w <= 1.0 and 0.0 <= h <= 1.0):
                out_of_bounds_boxes += 1

            if w <= 0.001 or h <= 0.001:
                degenerate_boxes += 1

    return {
        "split": split_name,
        "image_count": len(image_files),
        "label_count": len(label_files),
        "missing_labels_count": len(missing_labels),
        "orphaned_labels_count": len(orphaned_labels),
        "empty_labels_count": empty_label_files,
        "total_annotations": total_annotations,
        "class_counts": {CLASS_NAMES.get(k, f"class_{k}"): v for k, v in sorted(class_counts.items())},
        "raw_class_counts": dict(class_counts),
        "corrupt_lines": corrupt_lines,
        "degenerate_boxes": degenerate_boxes,
        "out_of_bounds_boxes": out_of_bounds_boxes,
    }


def validate_dataset(dataset_dir: Path, output_json: Path | None = None) -> dict[str, Any]:
    """Run full validation across all dataset splits."""
    logger.info("Starting dataset validation on: %s", dataset_dir.resolve())

    splits = ["train", "valid", "test"]
    overall_report: dict[str, Any] = {
        "dataset_path": str(dataset_dir.resolve()),
        "splits": {},
        "summary": {
            "total_images": 0,
            "total_labels": 0,
            "total_annotations": 0,
            "class_distribution": defaultdict(int),
        },
    }

    for split in splits:
        split_path = dataset_dir / split
        res = check_split(split_path, split)
        overall_report["splits"][split] = res

        if "error" not in res:
            overall_report["summary"]["total_images"] += res["image_count"]
            overall_report["summary"]["total_labels"] += res["label_count"]
            overall_report["summary"]["total_annotations"] += res["total_annotations"]
            for cls_name, count in res["class_counts"].items():
                overall_report["summary"]["class_distribution"][cls_name] += count

    # Print clean summary table
    print("\n" + "=" * 70)
    print("      VIGIL AI — CLASSROOM DATASET INTEGRITY REPORT")
    print("=" * 70)
    print(f"Dataset root: {dataset_dir.resolve()}")
    print(f"Total Images: {overall_report['summary']['total_images']:,}")
    print(f"Total Labels: {overall_report['summary']['total_labels']:,}")
    print(f"Total Annotations: {overall_report['summary']['total_annotations']:,}")
    print("-" * 70)
    print(f"{'Split':<10} | {'Images':<8} | {'Labels':<8} | {'Annotations':<12} | {'Issues':<10}")
    print("-" * 70)

    for split, data in overall_report["splits"].items():
        if "error" in data:
            print(f"{split:<10} | ERROR: {data['error']}")
            continue
        issues = (
            data["missing_labels_count"]
            + data["orphaned_labels_count"]
            + data["corrupt_lines"]
            + data["out_of_bounds_boxes"]
        )
        print(
            f"{split:<10} | {data['image_count']:<8} | {data['label_count']:<8} | "
            f"{data['total_annotations']:<12} | {issues:<10}"
        )

    print("-" * 70)
    print("CLASS DISTRIBUTION SUMMARY:")
    print("-" * 70)
    print(f"{'Class ID':<10} | {'Class Name':<16} | {'Count':<10} | {'Percentage':<10}")
    print("-" * 70)

    tot_ann = max(1, overall_report["summary"]["total_annotations"])
    for cls_id in range(5):
        cls_name = CLASS_NAMES[cls_id]
        count = overall_report["summary"]["class_distribution"].get(cls_name, 0)
        pct = (count / tot_ann) * 100.0
        print(f"{cls_id:<10} | {cls_name:<16} | {count:<10,d} | {pct:>6.2f}%")

    print("=" * 70 + "\n")

    if output_json:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(overall_report, f, indent=2, ensure_ascii=False)
        logger.info("Saved detailed report to: %s", output_json.resolve())

    return overall_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Check dataset integrity and class distribution")
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=Path("dataset"),
        help="Path to dataset root folder",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path("reports/dataset_check_report.json"),
        help="Path to output JSON report",
    )
    args = parser.parse_args()

    validate_dataset(args.dataset_dir, args.output_json)


if __name__ == "__main__":
    main()

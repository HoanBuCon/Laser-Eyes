"""Data Leakage Detector across Train/Val/Test Splits for VIGIL AI.

Detects if augmented variants of the same source scene/image appear across
multiple splits (e.g. original in train, augmented counterpart in valid/test),
which would cause artificial metric inflation.

Usage:
    python -m classroom_training.scripts.check_leakage [--dataset-dir dataset]
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Set

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("LeakageChecker")


def extract_base_scene_id(filename: str) -> str:
    """Extract canonical base identifier from Roboflow or augmented filename.

    Examples:
        'frame_0123_jpg.rf.0a1b2c3d.jpg' -> 'frame_0123'
        'scene00052_png.rf.09b441fa...jpg' -> 'scene00052'
        'img_55_aug_v1.jpg' -> 'img_55'
    """
    stem = Path(filename).stem

    # Pattern 1: Roboflow .rf.<hex_hash>
    rf_match = re.match(r"^(.*?)(?:_(?:jpg|png|jpeg))?\.rf\.[a-f0-9]+$", stem, re.IGNORECASE)
    if rf_match:
        return rf_match.group(1).lower()

    # Pattern 2: _augmented or _aug_
    aug_match = re.match(r"^(.*?)(?:_aug(?:mented)?(?:_\w+)?)?$", stem, re.IGNORECASE)
    if aug_match and aug_match.group(1):
        return aug_match.group(1).lower()

    return stem.lower()


def audit_data_leakage(dataset_dir: Path, output_json: Path | None = None) -> Dict[str, Any]:
    """Inspect all image splits to find cross-split scene overlap."""
    logger.info("Initiating cross-split data leakage audit on %s...", dataset_dir.resolve())

    splits = ["train", "valid", "test"]
    split_scenes: Dict[str, Set[str]] = {}
    split_files: Dict[str, Dict[str, list[str]]] = {}

    for split in splits:
        img_dir = dataset_dir / split / "images"
        if not img_dir.exists():
            logger.warning("Split directory not found: %s", img_dir)
            split_scenes[split] = set()
            split_files[split] = defaultdict(list)
            continue

        scenes: Set[str] = set()
        file_map: Dict[str, list[str]] = defaultdict(list)

        for f in img_dir.iterdir():
            if f.suffix.lower() in (".jpg", ".jpeg", ".png"):
                base_id = extract_base_scene_id(f.name)
                scenes.add(base_id)
                file_map[base_id].append(f.name)

        split_scenes[split] = scenes
        split_files[split] = file_map

    # Check pair overlaps
    train_val_overlap = split_scenes.get("train", set()) & split_scenes.get("valid", set())
    train_test_overlap = split_scenes.get("train", set()) & split_scenes.get("test", set())
    val_test_overlap = split_scenes.get("valid", set()) & split_scenes.get("test", set())

    total_unique_scenes = len(set.union(*split_scenes.values())) if split_scenes else 0
    total_leaked_scenes = len(train_val_overlap | train_test_overlap | val_test_overlap)

    leakage_report: Dict[str, Any] = {
        "dataset_path": str(dataset_dir.resolve()),
        "total_unique_scenes": total_unique_scenes,
        "split_scene_counts": {s: len(scenes) for s, scenes in split_scenes.items()},
        "overlaps": {
            "train_vs_valid": {
                "count": len(train_val_overlap),
                "scenes": sorted(list(train_val_overlap))[:50],  # cap at 50 in summary
            },
            "train_vs_test": {
                "count": len(train_test_overlap),
                "scenes": sorted(list(train_test_overlap))[:50],
            },
            "valid_vs_test": {
                "count": len(val_test_overlap),
                "scenes": sorted(list(val_test_overlap))[:50],
            },
        },
        "has_leakage": total_leaked_scenes > 0,
        "leaked_scene_count": total_leaked_scenes,
    }

    # Print summary
    print("\n" + "=" * 70)
    print("        VIGIL AI — DATA LEAKAGE AUDIT REPORT")
    print("=" * 70)
    print(f"Total Unique Base Scenes: {total_unique_scenes:,}")
    for split, count in leakage_report["split_scene_counts"].items():
        print(f"  - Split '{split}': {count:,} unique scene roots")
    print("-" * 70)
    print(f"Train <-> Valid Overlap : {len(train_val_overlap):>4} scenes")
    print(f"Train <-> Test Overlap  : {len(train_test_overlap):>4} scenes")
    print(f"Valid <-> Test Overlap  : {len(val_test_overlap):>4} scenes")
    print("-" * 70)

    if total_leaked_scenes == 0:
        print("[STATUS: PASSED] No cross-split scene leakage detected!")
        print("Model validation metrics will represent true generalization ability.")
    else:
        print(f"[STATUS: WARNING] Detected {total_leaked_scenes} scenes spanning multiple splits.")
        print("Roboflow augmentation generated splits at image-level rather than scene-level.")
        print("RECOMMENDATION: Use strict validation early stopping and report test-set generalization.")

    print("=" * 70 + "\n")

    if output_json:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(leakage_report, f, indent=2, ensure_ascii=False)
        logger.info("Leakage report written to: %s", output_json.resolve())

    return leakage_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit cross-split data leakage")
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=Path("dataset"),
        help="Path to dataset root folder",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path("reports/leakage_audit_report.json"),
        help="Path to output JSON report",
    )
    args = parser.parse_args()

    audit_data_leakage(args.dataset_dir, args.output_json)


if __name__ == "__main__":
    main()

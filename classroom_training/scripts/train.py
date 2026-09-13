"""YOLO Training Script for VIGIL AI Classroom Cheating Detection.

Supports:
- Local CPU / GPU CUDA training.
- Ultralytics YOLOv8 / YOLO11 model architectures (nano/small baseline).
- Focal Loss & Class balancing parameters.
- Automatic export and deployment of best weights to `models/classroom_best.pt`.

Usage:
    python -m classroom_training.scripts.train [--epochs 100] [--batch 16] [--model yolov8n.pt]
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ClassroomTrainer")


def run_training(
    data_yaml: Path,
    model_size: str = "yolov8n.pt",
    epochs: int = 100,
    batch_size: int = 16,
    img_size: int = 640,
    device: str = "",
    patience: int = 15,
    optimizer: str = "AdamW",
    lr0: float = 0.001,
    lrf: float = 0.01,
    project_dir: Path = Path("D:/VIGIL_AI_Results/training") if Path("D:/").exists() else Path("results/training"),
    run_name: str | None = None,
    deploy_model_path: Path = Path("models/classroom_best.pt"),
) -> str | None:
    """Execute YOLO fine-tuning pipeline on custom classroom dataset."""
    if run_name is None:
        run_name = f"{Path(model_size).stem}_classroom"
    try:
        from ultralytics import YOLO
    except ImportError:
        logger.error(
            "Ultralytics is not installed. Please run: pip install ultralytics"
        )
        return None

    if not data_yaml.exists():
        logger.error("Dataset config YAML not found: %s", data_yaml.resolve())
        return None

    logger.info("Initializing YOLO model with base weights: %s", model_size)
    model = YOLO(model_size)

    logger.info("Starting training run '%s' for %d epochs (imgsz=%d, batch=%d)...", run_name, epochs, img_size, batch_size)

    # Resolve device if empty
    training_kwargs = {
        "data": str(data_yaml.resolve()),
        "epochs": epochs,
        "patience": patience,
        "batch": batch_size,
        "imgsz": img_size,
        "optimizer": optimizer,
        "lr0": lr0,
        "lrf": lrf,
        "weight_decay": 0.0005,
        "warmup_epochs": 3,
        # Classroom context augmentations
        "hsv_h": 0.015,
        "hsv_s": 0.3,
        "hsv_v": 0.3,
        "degrees": 4.0,
        "translate": 0.1,
        "scale": 0.25,
        "flipud": 0.0,  # Never flip vertical for classroom proctoring
        "fliplr": 0.5,
        "mosaic": 0.8,
        "mixup": 0.1,
        "project": str(project_dir),
        "name": run_name,
        "exist_ok": True,
        "save": True,
        "save_period": -1,
        "plots": True,
        "val": True,
        "verbose": True,
        "cache": True,
    }

    if device:
        training_kwargs["device"] = device

    try:
        results = model.train(**training_kwargs)
        logger.info("Training completed successfully!")
    except Exception as exc:
        logger.error("Training encountered an exception: %s", exc, exc_info=True)
        return None

    # Locate best.pt
    save_dir = getattr(model.trainer, "save_dir", project_dir / run_name)
    best_weights = Path(save_dir) / "weights" / "best.pt"

    if best_weights.exists():
        logger.info("Found best model weights at: %s", best_weights.resolve())
        deploy_model_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(best_weights, deploy_model_path)
        logger.info("Successfully deployed production model to: %s", deploy_model_path.resolve())
        return str(deploy_model_path)
    else:
        logger.warning("best.pt was not found in: %s", save_dir)
        return str(save_dir)


def main() -> None:
    default_proj = Path("D:/VIGIL_AI_Results/training") if Path("D:/").exists() else Path("results/training")
    parser = argparse.ArgumentParser(description="Train YOLO for Classroom Cheating Detection")
    parser.add_argument(
        "--data",
        type=Path,
        default=Path("classroom_training/configs/data.yaml"),
        help="Path to dataset YAML",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="yolo12s.pt",
        help="Pretrained YOLO base model (yolo12s.pt, yolov8s.pt, yolo11s.pt)",
    )
    parser.add_argument("--epochs", type=int, default=100, help="Max training epochs")
    parser.add_argument("--batch", type=int, default=16, help="Batch size")
    parser.add_argument("--imgsz", type=int, default=640, help="Input image resolution")
    parser.add_argument("--device", type=str, default="", help="Device: '0', 'cpu', 'mps'")
    parser.add_argument("--patience", type=int, default=15, help="Early stopping patience")
    parser.add_argument(
        "--project",
        type=Path,
        default=default_proj,
        help="Directory to save training artifacts and logs",
    )
    parser.add_argument(
        "--deploy-path",
        type=Path,
        default=Path("models/classroom_best.pt"),
        help="Target location to copy best model weights for production",
    )
    args = parser.parse_args()

    run_training(
        data_yaml=args.data,
        model_size=args.model,
        epochs=args.epochs,
        batch_size=args.batch,
        img_size=args.imgsz,
        device=args.device,
        patience=args.patience,
        project_dir=args.project,
        deploy_model_path=args.deploy_path,
    )


if __name__ == "__main__":
    main()

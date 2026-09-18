# VIGIL AI — Dataset Export & Curation Guide
## Versioned ML Dataset Generation, Group-Safe Splits & Manifest Provenance

**Target System:** VIGIL AI Enterprise Exam Proctoring Suite  
**Architecture Baseline:** SRS v2 Actor-Centric Temporal Pipeline  
**Version:** 1.0  
**Effective Date:** September 2026  
**Audience:** AI/ML Engineers, Data Operations Leads, Research Scientists

---

## 1. Overview & Export Philosophy

The **VIGIL AI Human Data Operations Workbench** enables reproducible, version-controlled exports of reviewed proctoring data into standard ML training and benchmarking formats.

### Core Export Principles

1. **Zero Data Leakage via Deterministic Group-Safe Splitting:**
   - Raw video frames and consecutive snapshots from the same candidate/session are **never** randomly scattered across train, val, and test partitions.
   - All partitions are grouped by parent asset/session identifiers using deterministic cryptographic hashing ($\text{MD5}$).
2. **Immutable Provenance via `manifest.json`:**
   - Every exported version includes a machine-readable manifest capturing collection name, version tag, split ratios, class balance, timestamps, and pipeline configuration.
3. **Non-Destructive Sourcing:**
   - Raw dataset files (`dataset/`, `demo_video/`) are never overwritten. Exported datasets are written to isolated directories under `data/exported_datasets/<collection>_<version>/`.

---

## 2. Supported Export Formats

### 2.1 Format 1: Actor-Crop Classification Folders (`CLASSIFICATION_CROPS`)

Designed for training auxiliary lightweight posture/actor classifiers (e.g., **ResNet-18**, **MobileNet-V3**, **EfficientNet-B0**) to support observation extraction.

```
data/exported_datasets/VIGIL_707_ACTOR_CROPS_v1.0.0/
├── manifest.json
├── train/
│   ├── NORMAL_WRITING/
│   │   ├── scene00052_crop_0.jpg
│   │   └── scene00120_crop_0.jpg
│   ├── HEAD_TURN_LEFT/
│   ├── HEAD_TURN_RIGHT/
│   ├── HEAD_TURN_SIDE/
│   ├── HEAD_TURN_BACK/
│   ├── TORSO_LEAN_RIGHT/
│   └── PHONE_OR_DEVICE_INTERACTION/
├── val/
│   ├── NORMAL_WRITING/
│   └── ...
└── test/
    ├── NORMAL_WRITING/
    └── ...
```

#### Actor Crop Extraction Parameters
- **Padding Ratio:** $10\%\text{--}15\%$ margin around bounding box to preserve head-shoulder context.
- **Target Resolution:** $224 \times 224\text{ px}$ (or $256 \times 256\text{ px}$) with aspect-ratio preserving interpolation.
- **Color Format:** RGB (JPEG Quality 92).

---

### 2.2 Format 2: Temporal Episodes JSON (`EPISODE_JSON`)

Designed for evaluating and benchmarking temporal episode recognition engines and pattern synthesizers.

```json
[
  {
    "episode_id": "ep-912a-41ae",
    "asset_id": "demo_video/india_classroom.mp4",
    "seat_code": "SEAT-01",
    "episode_type": "HEAD_TURN_LEFT",
    "start_ms": 3000.0,
    "peak_ms": 4700.0,
    "end_ms": 6200.0,
    "duration_ms": 3200.0,
    "target_neighbor_id": "SEAT-02",
    "confidence": 1.0,
    "is_ai_proposal": false,
    "split": "train",
    "notes": "Candidate looks directly at left neighbor test paper."
  }
]
```

---

## 3. Deterministic Group-Safe Splitting Algorithm

To completely prevent **split leakage** (where nearly identical frames of the same student appear in both `train` and `test` sets), the workbench uses a **Group-Safe Splitter**:

```python
import hashlib

def compute_group_split(group_key: str, train_ratio: float = 0.70, val_ratio: float = 0.15) -> str:
    """Deterministic hash-based partition ensuring all samples from a group stay in one split."""
    hash_val = int(hashlib.md5(group_key.encode("utf-8")).hexdigest(), 16) % 100
    train_thresh = int(train_ratio * 100)
    val_thresh = train_thresh + int(val_ratio * 100)

    if hash_val < train_thresh:
        return "train"
    elif hash_val < val_thresh:
        return "val"
    else:
        return "test"
```

---

## 4. Dataset Manifest Specification (`manifest.json`)

Every exported dataset release contains a standardized `manifest.json`:

```json
{
  "dataset_collection": "VIGIL_707_ACTOR_CROPS",
  "version": "v1.0.0",
  "task_type": "ACTOR_CLASSIFICATION",
  "export_format": "CLASSIFICATION_CROPS",
  "generated_at": "2026-09-17T14:30:00Z",
  "split_strategy": "GROUP_BY_SESSION (Deterministic MD5 Hash)",
  "split_ratios": {
    "train": 0.70,
    "val": 0.15,
    "test": 0.15
  },
  "total_samples": 707,
  "split_counts": {
    "train": 495,
    "val": 108,
    "test": 104
  },
  "class_distribution": {
    "NORMAL_WRITING": 480,
    "HEAD_TURN_SIDE": 115,
    "HEAD_TURN_LEFT": 42,
    "HEAD_TURN_RIGHT": 38,
    "TORSO_LEAN_RIGHT": 20,
    "PHONE_OR_DEVICE_INTERACTION": 12
  },
  "provenance": {
    "source_system": "VIGIL AI Human Data Operations Workbench",
    "srs_version": "v2.0-ACTOR-CENTRIC-TEMPORAL"
  }
}
```

---

## 5. Step-by-Step ML Training Workflow

1. **Step 1: Export Curated Dataset Version:**
   - In Workbench UI (`/data-workbench` $\to$ Tab 6) or via API:
     ```bash
     curl -X POST http://localhost:8000/api/v1/data/datasets/export \
       -H "Content-Type: application/json" \
       -d '{
         "collection_name": "VIGIL_707_ACTOR_CROPS",
         "version_tag": "v1.0.0",
         "export_format": "CLASSIFICATION_CROPS",
         "train_ratio": 0.70,
         "val_ratio": 0.15,
         "test_ratio": 0.15
       }'
     ```
2. **Step 2: Train Auxiliary Actor Classifier (PyTorch):**
   ```python
   from torchvision import datasets, transforms, models
   import torch

   transform = transforms.Compose([
       transforms.Resize((224, 224)),
       transforms.ToTensor(),
       transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
   ])

   train_dataset = datasets.ImageFolder('data/exported_datasets/VIGIL_707_ACTOR_CROPS_v1.0.0/train', transform=transform)
   val_dataset = datasets.ImageFolder('data/exported_datasets/VIGIL_707_ACTOR_CROPS_v1.0.0/val', transform=transform)
   ```
3. **Step 3: Evaluate on Isolated Test Split:**
   - Validate classification accuracy and confusion matrix on the group-safe `test` partition.

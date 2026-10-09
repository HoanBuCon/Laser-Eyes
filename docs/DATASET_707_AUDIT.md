# VIGIL AI — DATASET 707 QUALITY & DOMAIN AUDIT REPORT

**Document Version:** 2.0.0  
**Target Dataset:** `dataset/` (707 labeled images, YOLO format)  
**SRS Reference:** [`docs/VIGIL_AI_SRS_v2_ACTOR_CENTRIC_TEMPORAL.md`](file:///H:/Code/MingKingLaser/laser_eyes/docs/VIGIL_AI_SRS_v2_ACTOR_CENTRIC_TEMPORAL.md) (Section 1.3, 30, 31)  
**Date:** 17/09/2026  
**Auditor:** Senior AI Systems Architect & Vision Engineer  

---

## 1. Dataset Overview & Inventory

The historical classroom dataset in `dataset/` was originally created for 1-stage YOLO object detection to predict 5 cheating behaviors directly from full-frame classroom images.

### Quantitative Summary:
- **Total Images:** 707 images (JPG format)
- **Total Bounding Box Annotations:** 19,353 annotations (average 27.37 candidates per image)
- **Image Resolution:** 640 × 640 pixels (compressed from raw captures)
- **Data Partition:**
  - **Train:** 493 images (69.7%) — 13,480 annotations
  - **Validation:** 130 images (18.4%) — 3,593 annotations
  - **Test:** 84 images (11.9%) — 2,280 annotations

---

## 2. Class Distribution & Severe Imbalance

| Class ID | Class Label | Total Annotations | Percentage | Train | Valid | Test | Imbalance Factor (vs Min Class) |
|---|---|---|---|---|---|---|---|
| **0** | `back peeking` | 238 | 1.23% | 154 | 56 | 28 | 1.0× (Baseline) |
| **1** | `front peeking` | 6,818 | 35.23% | 4,794 | 1,102 | 922 | 28.6× |
| **2** | `no cheating` | 8,189 | 42.31% | 5,663 | 1,699 | 827 | 34.4× |
| **3** | `phone using` | 629 | 3.25% | 424 | 132 | 73 | 2.6× |
| **4** | `side peeking` | 3,479 | 17.98% | 2,445 | 604 | 430 | 14.6× |
| **Total** | **5 Classes** | **19,353** | **100.0%** | **13,480** | **3,593** | **2,280** | — |

### Key Observations on Class Imbalance:
1. `no cheating` and `front peeking` constitute **77.54%** of the entire dataset.
2. `back peeking` is critically under-represented (**1.23%**), making any model trained on it extremely brittle with high variance.
3. `phone using` comprises only **3.25%** of annotations, heavily skewed towards one or two specific students in the training captures.

---

## 3. Bounding Box Dimensions & Scale Analysis

- **Average Candidate BBox Area:** ~0.008 to 0.035 of full-frame area.
- **Row-Based Scale Variance:**
  - **Front Row Candidates:** BBoxes ~ $120 \times 160$ px (sufficient keypoint and posture resolution).
  - **Middle Row Candidates:** BBoxes ~ $60 \times 80$ px.
  - **Back Row Candidates:** BBoxes ~ $15 \times 25$ px (severe pixel degradation).
- **Physical Consequence:** At $15 \times 25$ pixels, subtle objects like a smartphone ($< 5\text{px}$) or precise eye gaze direction are completely indistinguishable. Forcing a single YOLO network to classify "phone using" on $15\text{px}$ candidates results in random guessing based on background chair artifacts.

---

## 4. Source-Domain Concentration & Leakage Risk

### Single-Room Domain Capture:
- **100% of the 707 images** originate from a **single physical classroom** with a fixed ceiling camera mount, fixed yellow/white fluorescent lighting, and identical student uniforms.
- **Temporal Near-Duplicates:** The images were sampled at short intervals from continuous video recordings of exam simulations in this single room.
- **Train/Val/Test Split Artifact:** Because the split was performed randomly across frames from the same continuous recordings rather than across independent rooms/dates, adjacent frames (separated by $< 1$ second) are distributed across Train, Valid, and Test splits.
- **Resulting Metric Artifact:** Standard mAP@50 metrics on the test split (e.g. $\ge 88\%$) represent **memorization of specific students and background desk coordinates**, not generalized behavior recognition. When deployed on a new room (such as `demo_video/india_classroom.mp4`), inference confidence immediately collapses to $< 0.15$.

---

## 5. Semantic Ambiguity & Label Consistency Issues

1. **`front peeking` vs `no cheating` Ambiguity:**
   - In standard exam settings, looking forward at the blackboard, clock, or teacher instructions is a natural, non-cheating posture. In the dataset, `front peeking` was often labeled whenever a student lifted their head, conflating normal exam behavior with intentional copying from the student in front.
2. **`phone using` Ground Truth Deficiency:**
   - In over 80% of `phone using` annotations, no phone is actually visible in the pixel data (hands are concealed under the table). The annotator assigned `phone using` based on the assumption of what the student was doing during the staged simulation.
3. **Missing Temporal Context:**
   - A single static frame showing a student with their head turned $30^\circ$ is labeled `side peeking`. In reality, a 0.2s natural neck stretch is benign, whereas a 4.0s sustained gaze toward a neighbor's test paper is suspicious. Static 2D images cannot differentiate between these two behaviors.

---

## 6. Strategic Role in SRS v2.0 (Auxiliary Dataset)

In accordance with SRS v2.0 (Sections 1.3, 30, and 31):

```text
       707 Static Images Dataset
                   │
                   ▼ (Audit & Re-label)
       Actor-Crop Extraction (BBoxes)
                   │
                   ▼ (Auxiliary Training)
    Lightweight Backbone Classifier (ResNet18 / MobileNetV3)
                   │
                   ▼ (Output)
    Raw Posture Observation Provider (e.g. SIDE_ORIENTED_POSE)
                   │
                   ▼ (Downstream)
    Temporal Episode Engine ──► Pattern Engine ──► Human Review
```

### Strategic Decisions:
1. **NO 1-Stage YOLO Retraining:** YOLOv12s will not be retrained as a monolithic end-to-end cheating detector.
2. **Auxiliary Orientation Classifier (P1 Scope):** The dataset will be converted into an actor-crop classification dataset for orientation supervision:
   - `side peeking` $\longrightarrow$ `SIDE_ORIENTED_POSE`
   - `front peeking` $\longrightarrow$ `FRONT_ORIENTED_POSE`
   - `back peeking` $\longrightarrow$ `BACK_ORIENTED_POSE`
   - `no cheating` $\longrightarrow$ `NORMAL_POSE_SAMPLE`
3. **Decouple Phone Detection:** `phone using` is decoupled from head orientation and relegated to multi-cue hand-desk interaction experiments.
4. **Validation Ground Truth:** Production validation must rely on **time-stamped video regression suites** (`validation/timeline_annotation.json`) and **Human Review Feedback**, not on static mAP on this 707-image dataset.

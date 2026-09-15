# Báo cáo thiết kế hệ thống phát hiện hành vi gian lận trong phòng thi bằng Computer Vision

## 1. Tổng quan đề tài

### 1.1. Mục tiêu

Xây dựng một hệ thống Computer Vision có khả năng phân tích hình ảnh/video từ camera phòng thi và phát hiện các hành vi có khả năng liên quan đến gian lận.

Hệ thống tập trung vào việc:

- Phát hiện các hành vi bất thường trong từng frame.
- Xác định vị trí hành vi bằng bounding box.
- Theo dõi hành vi trong một khoảng thời gian thay vì quyết định chỉ dựa trên một frame.
- Giảm false positive bằng confidence threshold và temporal rule.
- Ghi nhận các sự kiện đáng ngờ.
- Hiển thị kết quả để người giám sát có thể kiểm tra.

### 1.2. Phạm vi

Phiên bản đầu tiên tập trung vào **một camera/video và một mô hình object detection**.

Các hành vi được nhận diện:

| ID | Class | Ý nghĩa |
|---:|---|---|
| 0 | `back peeking` | Nhìn/quay về phía sau |
| 1 | `front peeking` | Nhìn về phía trước |
| 2 | `gesture using` | Sử dụng cử chỉ/tay |
| 3 | `no cheating` | Không phát hiện hành vi gian lận |
| 4 | `phone using` | Sử dụng điện thoại |
| 5 | `side peeking` | Nhìn sang bên |

`mobile using` và `phone using` đã được hợp nhất thành `phone using`.

Dataset sau khi chuẩn hóa có:

```text
back peeking      :    546
front peeking     : 16,406
gesture using     :      3
no cheating       : 19,515
phone using       :  1,477
side peeking      :  8,369

Total annotations : 46,316
```

---

# 2. Định hướng kiến trúc

Hệ thống được thiết kế theo 3 tầng chính:

```text
┌─────────────────────────────────────────────┐
│              APPLICATION LAYER              │
│                                             │
│ Dashboard / Alert / Event Log / Report      │
└──────────────────────┬──────────────────────┘
                       │
┌──────────────────────▼──────────────────────┐
│             DECISION LAYER                  │
│                                             │
│ Confidence Filter                           │
│ Temporal Analysis                           │
│ Suspicious Event Rules                      │
└──────────────────────┬──────────────────────┘
                       │
┌──────────────────────▼──────────────────────┐
│             PERCEPTION LAYER                │
│                                             │
│ Camera / Video                              │
│       ↓                                     │
│ YOLO Object Detection                       │
│       ↓                                     │
│ Bounding Boxes + Class + Confidence         │
└─────────────────────────────────────────────┘
```

## 2.1. Perception Layer

Đây là tầng Computer Vision.

Input:

```text
Camera / Video
```

Sau đó video được chia thành các frame:

```text
Video
  ↓
Frame 1
Frame 2
Frame 3
...
```

Mỗi frame được đưa vào YOLO.

Output:

```text
class
confidence
bounding box
```

Ví dụ:

```text
Frame 125

Class: front peeking
Confidence: 0.91

Bounding Box:
x1 = 420
y1 = 120
x2 = 670
y2 = 580
```

YOLO là thành phần chịu trách nhiệm chính cho việc phát hiện và định vị hành vi.

Ultralytics hỗ trợ quy trình train, validation và xuất các metric như Precision, Recall, mAP50 và mAP50-95; việc đánh giá theo từng class cũng được hỗ trợ.

---

# 3. Decision Layer

Không nên coi:

```text
YOLO phát hiện "phone using"
```

là ngay lập tức:

```text
Student cheating
```

Đây là một điểm quan trọng trong thiết kế hệ thống.

Một frame có thể bị nhận diện sai do:

- motion blur;
- ánh sáng;
- tư thế;
- occlusion;
- bounding box không chính xác;
- hành động vô tình giống hành vi gian lận.

Do đó cần thêm một tầng Decision.

## 3.1. Confidence Filtering

Ví dụ:

```text
confidence >= 0.70
```

mới được xem xét.

Ví dụ:

```text
phone using = 0.43
```

→ bỏ qua.

Trong khi:

```text
phone using = 0.91
```

→ tiếp tục xử lý.

Threshold thực tế sẽ được xác định thông qua validation và error analysis, không nên cố định 0.70 ngay từ đầu.

---

# 4. Temporal Analysis

Đây là thành phần quan trọng để biến object detection thành hệ thống giám sát hành vi.

Ví dụ YOLO phát hiện:

```text
Frame 100 → front peeking 0.88
Frame 101 → front peeking 0.90
Frame 102 → front peeking 0.92
Frame 103 → front peeking 0.91
```

Thay vì tạo 4 cảnh báo:

```text
WARNING
WARNING
WARNING
WARNING
```

hệ thống gom chúng thành:

```text
Suspicious Event

Behavior: Front Peeking
Start: Frame 100
End: Frame 103
Duration: ...
Confidence: ...
```

### Rule cơ bản

Một event chỉ được tạo khi hành vi:

1. Có confidence đủ cao.
2. Xuất hiện liên tục hoặc đủ số frame.
3. Không phải một detection đơn lẻ.

Ví dụ:

```text
confidence >= threshold
AND
detected in >= N consecutive frames
```

N và threshold cần được xác định bằng thực nghiệm.

---

# 5. Tracking

Tracking là thành phần **tùy chọn ở MVP**, chỉ thêm khi cần xác định hành vi của từng người theo thời gian.

Ví dụ:

```text
Frame 1:
Person A → front peeking

Frame 2:
Person A → front peeking

Frame 3:
Person A → front peeking
```

Tracking giúp duy trì một ID:

```text
Person ID = 7
```

và theo dõi:

```text
ID 7
    ↓
front peeking
    ↓
front peeking
    ↓
front peeking
```

Ultralytics hiện hỗ trợ các tracker như BoT-SORT và ByteTrack. ByteTrack là lựa chọn đơn giản hơn cho MVP; BoT-SORT cung cấp thêm các khả năng như camera-motion compensation và ReID tùy chọn.

### Quyết định phạm vi

Trong tháng đầu:

> **Không triển khai Re-ID.**

Nếu camera cố định và số lượng sinh viên không quá phức tạp, chỉ cần detection + temporal logic, hoặc detection + tracker cơ bản, là đủ để chứng minh ý tưởng.

---

# 6. Application Layer

Application Layer nhận event từ Decision Layer.

Ví dụ:

```text
Student ID: 7

Behavior:
Phone Using

Confidence:
0.92

Duration:
2.3 seconds

Status:
Suspicious
```

Hệ thống có thể:

- hiển thị cảnh báo;
- lưu event;
- lưu timestamp;
- lưu loại hành vi;
- lưu confidence;
- lưu frame evidence.

---

# 7. Kiến trúc tổng thể

Kiến trúc MVP đề xuất:

```text
                 CAMERA / VIDEO
                       │
                       ▼
                ┌──────────────┐
                │ Frame Reader │
                └──────┬───────┘
                       │
                       ▼
                ┌──────────────┐
                │ YOLO Detector│
                └──────┬───────┘
                       │
             class / bbox / confidence
                       │
                       ▼
              ┌──────────────────┐
              │ Confidence Filter │
              └────────┬─────────┘
                       │
                       ▼
              ┌──────────────────┐
              │ Temporal Buffer  │
              └────────┬─────────┘
                       │
                       ▼
              ┌──────────────────┐
              │ Event Rule Engine│
              └────────┬─────────┘
                       │
             ┌─────────┴─────────┐
             │                   │
             ▼                   ▼
          NORMAL             SUSPICIOUS
                                 │
                                 ▼
                       ┌─────────────────┐
                       │ Event Logger    │
                       └────────┬────────┘
                                │
                                ▼
                       ┌─────────────────┐
                       │ Dashboard/UI    │
                       └─────────────────┘
```

---

# 8. Dataset Pipeline

## 8.1. Dataset hiện tại

Dataset đã được chuẩn hóa từ 7 class xuống 6 class.

Mapping cuối:

```text
0 → back peeking
1 → front peeking
2 → gesture using
3 → no cheating
4 → phone using
5 → side peeking
```

Tổng số annotation:

```text
46,316
```

Tổng số label files:

```text
1,693
```

Phân bố class:

```text
546
16,406
3
19,515
1,477
8,369
```

## 8.2. Vấn đề class imbalance

Dataset có imbalance rất lớn.

Đặc biệt:

```text
gesture using = 3 annotations
```

Đây là vấn đề nghiêm trọng hơn việc chỉ đơn giản là class imbalance.

Với chỉ 3 instances, chưa đủ cơ sở để kết luận model có khả năng tổng quát hóa cho `gesture using`.

### Phải kiểm tra trước khi train

Cần xác định:

```text
Có thực sự chỉ có 3 gesture?
```

hay:

```text
Annotation của gesture bị thiếu?
```

Nếu annotation đúng và thực tế chỉ có 3 mẫu, cần bổ sung dữ liệu.

Không nên copy 3 annotation giống nhau thành hàng nghìn bản vì dễ gây overfitting.

---

# 9. Dataset Validation

Trước khi train cần thực hiện các kiểm tra:

## 9.1. Kiểm tra class distribution

```text
check_dataset.py
```

Expected:

```text
0: 546
1: 16,406
2: 3
3: 19,515
4: 1,477
5: 8,369
```

## 9.2. Kiểm tra image-label matching

Mỗi ảnh cần có label tương ứng.

```text
train/images/*.jpg
train/labels/*.txt
```

Tương tự:

```text
valid
test
```

## 9.3. Kiểm tra bounding box

Cần visualize một số lượng mẫu từ từng class.

Mục đích:

- xác nhận class ID;
- xác nhận bounding box;
- phát hiện label sai;
- phát hiện bounding box quá lớn/quá nhỏ;
- phát hiện ảnh không có object.

## 9.4. Kiểm tra data leakage

Đặc biệt quan trọng vì dataset có augmentation.

Không được để:

```text
original image
```

ở train và:

```text
augmented version của cùng image
```

ở validation/test.

Nếu xảy ra, metric có thể cao giả tạo.

---

# 10. Model

## 10.1. Model đề xuất

Sử dụng YOLO object detection làm baseline.

Không cần thử quá nhiều architecture trong tháng đầu.

Đề xuất:

```text
Baseline:
YOLO nano/small
```

Mục tiêu của baseline là xác định:

> Dataset hiện tại có đủ tốt để phát hiện 6 hành vi hay chưa?

Không nên ngay lập tức:

```text
YOLO model A
YOLO model B
YOLO model C
YOLO model D
...
```

vì sẽ làm phình phạm vi nghiên cứu.

## 10.2. Training

Quy trình:

```text
Pretrained YOLO
      ↓
Custom Dataset
      ↓
Train
      ↓
Validation
      ↓
Error Analysis
      ↓
Dataset improvement
      ↓
Retrain
```

Ultralytics training hỗ trợ validation trong quá trình train và tạo các plot phục vụ phân tích.

---

# 11. Evaluation

Không đánh giá model chỉ bằng một con số mAP.

Cần báo cáo:

### Precision

Cho biết trong các detection mà model đưa ra, bao nhiêu detection là đúng.

### Recall

Cho biết model phát hiện được bao nhiêu object thực tế.

### mAP50

Đánh giá detection ở IoU = 0.5.

### mAP50-95

Đánh giá trên nhiều IoU threshold từ 0.5 đến 0.95, nghiêm khắc hơn mAP50.

Ultralytics cung cấp cả metric tổng thể và metric theo từng class.

---

# 12. Per-Class Evaluation

Đây là phần bắt buộc.

Không được chỉ báo:

```text
mAP50 = 0.92
```

mà phải xem:

```text
back peeking
front peeking
gesture using
no cheating
phone using
side peeking
```

Ví dụ:

```text
Class              Precision   Recall   mAP50
------------------------------------------------
back peeking          ...
front peeking         ...
gesture using         ...
no cheating           ...
phone using           ...
side peeking          ...
```

Điều này đặc biệt quan trọng với `gesture using`.

Một model có:

```text
mAP tổng = 90%
```

nhưng:

```text
gesture using AP = 0%
```

vẫn chưa thể coi là giải quyết tốt bài toán 6 class.

Ultralytics validation có thể cung cấp per-class AP, Precision, Recall, F1 và các metric liên quan.

---

# 13. Confusion Matrix

Confusion Matrix dùng để tìm các class dễ nhầm.

Ví dụ:

```text
front peeking
        ↕
side peeking
```

hoặc:

```text
phone using
        ↕
gesture using
```

Nếu hai class thường xuyên nhầm nhau, cần quay lại:

```text
Dataset
   ↓
Visual inspection
   ↓
Annotation
   ↓
Augmentation
```

thay vì lập tức thay model.

Ultralytics validation có thể xuất confusion matrix và normalized confusion matrix.

---

# 14. Error Analysis

Sau lần train đầu tiên, lấy các prediction sai.

Phân loại lỗi:

```text
False Positive
False Negative
Wrong Class
Bad Bounding Box
Low Confidence
Occlusion
Blur
Lighting
```

Ví dụ:

```text
Ground truth:
side peeking

Prediction:
front peeking
```

Đây là lỗi class confusion.

Hoặc:

```text
Ground truth:
phone using

Prediction:
nothing
```

Đây là false negative.

Error analysis giúp quyết định có cần:

- thêm dữ liệu;
- sửa annotation;
- augmentation;
- thay threshold;
- hay thay model.

---

# 15. Event Detection

Sau khi YOLO đạt mức accuracy chấp nhận được, mới xây dựng event layer.

Ví dụ:

```text
Detection:

Frame 1:
phone using 0.91

Frame 2:
phone using 0.93

Frame 3:
phone using 0.89

Frame 4:
phone using 0.92
```

Event engine tạo:

```text
EVENT #001

Type:
PHONE_USING

Confidence:
0.91

Duration:
N frames

Status:
SUSPICIOUS
```

Điều này giúp giảm việc cảnh báo liên tục.

---

# 16. Event Rule Engine

MVP chỉ cần một số rule đơn giản.

### Rule 1 — Confidence

```text
confidence >= threshold
```

### Rule 2 — Persistence

```text
detected >= N frames
```

### Rule 3 — Cooldown

Sau khi tạo event:

```text
event generated
      ↓
cooldown
      ↓
không tạo event trùng
```

Ví dụ:

```text
Phone using
detected continuously for 3 seconds

→ 1 event
```

thay vì:

```text
180 events
```

---

# 17. Không nên làm trong MVP

Để đảm bảo deadline 1 tháng, các thành phần sau **không nằm trong phạm vi bắt buộc**:

### Không làm Face Recognition

Không cần xác định danh tính sinh viên bằng khuôn mặt trong phiên bản đầu.

### Không làm LLM

YOLO + rule engine đủ để chứng minh hệ thống.

Không cần:

```text
YOLO
→ LLM
→ reasoning
→ chatbot
```

### Không làm Re-ID

Chỉ thêm nếu tracking cơ bản không đáp ứng được yêu cầu.

### Không làm Multi-Camera

Chỉ dùng một video/camera.

### Không làm Cloud Architecture

Không cần:

```text
Docker
Kubernetes
Microservices
Cloud GPU
Message Queue
```

trong MVP.

### Không làm Mobile App

Dashboard web hoặc giao diện local là đủ.

### Không làm Database phức tạp

Có thể bắt đầu bằng:

```text
JSON / CSV
```

sau đó mới chuyển sang SQLite nếu cần.

---

# 18. Tech Stack

## Computer Vision

```text
Python
OpenCV
YOLO / Ultralytics
PyTorch
NumPy
```

OpenCV chịu trách nhiệm đọc video, xử lý frame và các thao tác image/video cơ bản.

## Dataset

```text
Roboflow
YOLO format
data.yaml
```

## Training

```text
PyTorch
Ultralytics
CUDA
```

nếu GPU NVIDIA khả dụng.

## Tracking

Chỉ khi cần:

```text
ByteTrack
```

hoặc:

```text
BoT-SORT
```

Ultralytics cung cấp sẵn tracking mode và các tracker tương ứng.

## Backend

MVP:

```text
Python
```

Có thể dùng:

```text
FastAPI
```

nếu cần tách inference server khỏi frontend.

## Frontend

Nếu cần dashboard:

```text
React
```

hoặc một UI đơn giản bằng HTML/CSS/JS nếu deadline gấp.

---

# 19. Cấu trúc project đề xuất

Không nên tạo quá nhiều module.

```text
exam-cheating-detection/
│
├── dataset/
│   ├── train/
│   ├── valid/
│   ├── test/
│   └── data.yaml
│
├── models/
│   └── best.pt
│
├── scripts/
│   ├── check_dataset.py
│   ├── visualize_labels.py
│   └── evaluate.py
│
├── src/
│   ├── detector.py
│   ├── tracker.py
│   ├── event_engine.py
│   └── video_processor.py
│
├── results/
│   ├── training/
│   ├── validation/
│   └── events/
│
└── main.py
```

Trong đó:

```text
detector.py
```

→ YOLO inference.

```text
tracker.py
```

→ chỉ dùng nếu tracking cần thiết.

```text
event_engine.py
```

→ confidence + temporal rules.

```text
video_processor.py
```

→ đọc video/frame.

```text
main.py
```

→ kết nối toàn bộ pipeline.

---

# 20. Quy trình triển khai 1 tháng

## Tuần 1 — Dataset + Baseline

### Mục tiêu

Hoàn thiện dataset và chạy được model baseline.

### Công việc

```text
[✓] Chuẩn hóa 6 class
[✓] Merge mobile using → phone using
[ ] Kiểm tra image/label
[ ] Visualize bounding boxes
[ ] Kiểm tra duplicate
[ ] Kiểm tra train/valid/test leakage
[ ] Kiểm tra gesture using
[ ] Train baseline YOLO
```

### Deliverable

```text
Clean Dataset
+
Baseline Model
+
Training Results
```

---

# Tuần 2 — Model Evaluation

### Mục tiêu

Biết model thực sự tốt/xấu ở đâu.

### Công việc

```text
[ ] Validation
[ ] Precision
[ ] Recall
[ ] mAP50
[ ] mAP50-95
[ ] Per-class AP
[ ] Confusion Matrix
[ ] Error Analysis
```

Sau đó xác định:

```text
Class nào tốt?
Class nào kém?
Class nào bị nhầm?
```

Đặc biệt:

```text
gesture using
```

phải được đánh giá riêng.

### Deliverable

```text
Best Baseline Model
+
Evaluation Report
+
Error Analysis
```

---

# Tuần 3 — Video Inference + Temporal Logic

### Mục tiêu

Biến model detection thành hệ thống xử lý video.

Pipeline:

```text
Video
 ↓
Frame
 ↓
YOLO
 ↓
Confidence Filter
 ↓
Temporal Buffer
 ↓
Event
```

### Công việc

```text
[ ] Video input
[ ] Real-time/offline inference
[ ] Bounding box visualization
[ ] Confidence threshold
[ ] Temporal buffer
[ ] Event detection
[ ] Cooldown
```

Nếu thời gian còn:

```text
[ ] ByteTrack
```

### Deliverable

Một video có thể chạy qua hệ thống:

```text
input.mp4
      ↓
system
      ↓
output.mp4
```

và đánh dấu các hành vi đáng ngờ.

---

# Tuần 4 — Integration + Demo + Report

### Mục tiêu

Hoàn thiện hệ thống và báo cáo.

### Công việc

```text
[ ] Dashboard đơn giản
[ ] Event log
[ ] Save evidence frame
[ ] Test trên video chưa dùng khi train
[ ] Benchmark FPS
[ ] Final evaluation
[ ] Demo
[ ] Documentation
```

### Deliverable

```text
Trained Model
+
Inference Pipeline
+
Event Detection
+
Demo
+
Final Report
```

---

# 21. Mức độ ưu tiên

Để tránh phình dự án, chia thành 3 mức.

## MUST HAVE

```text
✓ Dataset sạch
✓ 6 class
✓ YOLO training
✓ Validation
✓ Per-class metrics
✓ Video inference
✓ Confidence threshold
✓ Temporal event rule
✓ Output evidence
✓ Final report
```

## SHOULD HAVE

```text
○ ByteTrack
○ Simple dashboard
○ Event history
○ FPS measurement
```

## NICE TO HAVE

```text
○ Re-ID
○ Multi-camera
○ Face recognition
○ Cloud deployment
○ Database server
○ Mobile app
○ LLM explanation
○ Advanced analytics
```

Nếu gần deadline:

> **Bỏ toàn bộ NICE TO HAVE.**

---

# 22. Tiêu chí hoàn thành MVP

Hệ thống được coi là hoàn thành khi có thể:

```text
Input:
Video phòng thi
      ↓
YOLO
      ↓
Detect behavior
      ↓
Temporal filtering
      ↓
Suspicious event
      ↓
Evidence frame
      ↓
Output
```

Ví dụ output:

```text
------------------------------------------------
SUSPICIOUS EVENT
------------------------------------------------
Behavior    : Phone Using
Confidence  : 0.91
Duration    : 2.4 seconds
Frame       : 1520
Evidence    : event_001.jpg
------------------------------------------------
```

Không nhất thiết phải có một hệ thống backend lớn mới được coi là hoàn thành.

---

# 23. Các tiêu chí đánh giá hệ thống

## Model-level

```text
mAP50
mAP50-95
Precision
Recall
F1
Per-class AP
Confusion Matrix
```

## System-level

```text
FPS
Inference latency
Event detection delay
False alarm rate
Missed event rate
```

Model evaluation và inference speed nên được báo cáo riêng vì một model có accuracy cao nhưng quá chậm vẫn không phù hợp cho giám sát video thời gian thực. Ultralytics cũng cung cấp timing breakdown cho preprocessing, inference và postprocessing trong quá trình đánh giá.

---

# 24. Rủi ro chính

## Rủi ro 1 — `gesture using` quá ít dữ liệu

```text
3 annotations
```

Đây là rủi ro lớn nhất hiện tại.

### Giải pháp

Ưu tiên bổ sung dữ liệu thật.

---

## Rủi ro 2 — Class confusion

Ví dụ:

```text
front ↔ side peeking
phone ↔ gesture
```

### Giải pháp

Error analysis + bổ sung dữ liệu có chọn lọc.

---

## Rủi ro 3 — False positive

Một hành động bình thường bị phát hiện là cheating.

### Giải pháp

Temporal filtering.

Không quyết định dựa trên một frame.

---

## Rủi ro 4 — Data leakage

Augmented image của cùng một ảnh xuất hiện ở train và test.

### Giải pháp

Kiểm tra nguồn ảnh trước khi đánh giá.

---

## Rủi ro 5 — Scope creep

Thêm quá nhiều công nghệ:

```text
YOLO
+
Tracking
+
ReID
+
Face Recognition
+
LLM
+
Backend
+
Cloud
+
Mobile
```

sẽ khiến dự án vượt deadline.

### Giải pháp

Giữ architecture:

```text
YOLO
 ↓
Temporal Rule
 ↓
Event
 ↓
Evidence
```

làm core system.

---

# 25. Kiến trúc cuối cùng được đề xuất

Phiên bản phù hợp nhất với deadline 1 tháng:

```text
                         ┌──────────────┐
                         │ Camera/Video │
                         └──────┬───────┘
                                │
                                ▼
                         ┌──────────────┐
                         │ OpenCV       │
                         │ Frame Reader │
                         └──────┬───────┘
                                │
                                ▼
                         ┌──────────────┐
                         │ YOLO         │
                         │ Detector     │
                         └──────┬───────┘
                                │
                       Detection Results
                                │
                                ▼
                    ┌─────────────────────┐
                    │ Confidence Filter   │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Temporal Buffer     │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Event Rule Engine   │
                    └──────────┬──────────┘
                               │
                    ┌──────────┴──────────┐
                    ▼                     ▼
                 NORMAL              SUSPICIOUS
                                         │
                                         ▼
                              ┌──────────────────┐
                              │ Evidence Capture │
                              └────────┬─────────┘
                                       │
                                       ▼
                              ┌──────────────────┐
                              │ Event Log/UI     │
                              └──────────────────┘
```

---

# 26. Kết luận

Hệ thống không nên được xây dựng theo hướng "càng nhiều công nghệ càng tốt".

Trong phạm vi một tháng, kiến trúc phù hợp nhất là:

```text
Dataset
   ↓
YOLO Detection
   ↓
Confidence Filtering
   ↓
Temporal Analysis
   ↓
Rule-based Event Detection
   ↓
Evidence + Alert
```

Trong đó YOLO đảm nhiệm **nhận diện và định vị hành vi**, còn Temporal Analysis và Rule Engine đảm nhiệm **quyết định một hành vi có đủ bằng chứng để trở thành một sự kiện đáng ngờ hay chưa**.

Dataset hiện tại đã được chuẩn hóa từ 7 xuống 6 class và việc hợp nhất `mobile using` + `phone using` không làm mất annotation:

```text
Before: 46,316 annotations
After : 46,316 annotations
```

Tuy nhiên, trước khi bước vào training chính thức, vấn đề cần ưu tiên giải quyết là:

```text
gesture using = 3 annotations
```

Đây là điểm yếu dữ liệu cần được kiểm tra trước tiên.

### Thứ tự triển khai cuối cùng

```text
1. Dataset validation
       ↓
2. Kiểm tra gesture using
       ↓
3. Visualize labels
       ↓
4. Kiểm tra leakage
       ↓
5. Train YOLO baseline
       ↓
6. Evaluate per-class
       ↓
7. Error analysis
       ↓
8. Video inference
       ↓
9. Temporal event engine
       ↓
10. Evidence + demo
       ↓
11. Final report
```

**Không nên bắt đầu tracking, backend, dashboard hay LLM trước khi bước 5–7 chứng minh rằng model detection hoạt động đủ tốt.** Đây là cách giữ dự án trong phạm vi một tháng thay vì biến nó thành một hệ thống quá lớn.
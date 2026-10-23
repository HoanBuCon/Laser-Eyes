# Kế hoạch tối ưu hiệu năng VIGIL AI

**Mốc:** 2026-10-17 · nhánh `feat/one-server-analysis` · máy đo: laptop RTX 4060 (8 GB), Ryzen 7 7840H, 16 GB RAM.
Các con số "đo" là số đo thật trên máy này; các con số "ước tính" phải được đo lại sau mỗi bước. Chỉ giữ thay đổi nào đo được có lợi.

## 1. Hiện trạng đã đo

### 1.1 Classroom, 1 phòng (`classroom-01`, 33 ghế, 1800 khung, chế độ tự động)

| Chỉ số | Giá trị |
|---|---|
| Khung phân tích/s ở trạng thái ổn định | **15,9** (847/1800 khung bỏ vì trễ) |
| So với thời gian thực | **1,00×** ổn định; 0,93× cả lần chạy (gồm nạp model, ghi bằng chứng cuối) |
| YOLO-Pose 1280 | 25,6 ms/khung: tiền xử lý CPU 7,3 · suy luận 12,5 · hậu xử lý 2,1 · phần ngoài ~4 |
| Hướng đầu (SixDRepNet, FP16) | ~18 ms/khung (lô trung bình 8,4 đầu, mục tiêu 5 Hz/ghế) |
| Vẽ lớp phân tích | 4 ms |
| Nén JPEG cho luồng LIVE | chưa đo riêng, ước tính 5–10 ms (nén cả khung 1080p mỗi khung) |
| Thời gian thực mỗi khung | ~63 ms; các khâu chạy **nối tiếp**, GPU chờ CPU và ngược lại |

Điều kiện: cắm sạc, Windows ở chế độ nguồn *Balanced*, GPU có tải của ứng dụng khác.

### 1.2 Thử `--imgsz 960` (sau khi sửa lỗi `--imgsz` không tới YOLO)

| `--imgsz` | Khung phân tích/s | Ghế có người phát hiện được |
|---|---|---|
| 1280 | 15,9 | **15**/33 |
| 960 | 20,7 | **10**/33 |

→ Giữ 1280: 960 mất 1/3 thí sinh ở cuối phòng.

### 1.3 Classroom, 3 phòng cùng lúc (3 tiến trình riêng)

- Một tiến trình **hết RAM hệ thống** và dừng (bộ đệm bằng chứng giữ khung thô 1080p, ~1,8 GB/phòng; máy còn trống ~6 GB).
- Hai tiến trình còn lại: 2,9–3,4 khung phân tích/s, một tiến trình chỉ 0,68× thời gian thực.
- GPU 82–93 %, VRAM ~4,7/8 GB → nút thắt là **RAM và sức tính toán**, không phải VRAM.

→ Kết luận: với code hiện tại, RTX 4060 **không** gánh được 3 phòng LIVE.

### 1.4 VIGIL One phân tích trên Hub (chưa đo, cần đo trước tiên)

- `storage/one_analysis.py`: phân tích chạy bằng **luồng trong tiến trình web**, mặc định 2 worker (`VIGIL_ONE_ANALYSIS_WORKERS`), mỗi thí sinh gắn cố định với một worker.
- 10 khung/s mỗi thí sinh × ~40 ms/khung (MediaPipe + phát hiện vật thể, số đo cũ trên máy này) ≈ 0,4 s CPU cho mỗi giây thi.
- **Ước tính:** mỗi worker theo kịp ~2 thí sinh → mặc định **~5 thí sinh** theo kịp thời gian thực. Phân tích nặng còn làm web/API chậm theo.

## 2. Nút thắt, xếp theo mức ảnh hưởng

1. **VIGIL One:** phân tích dạng luồng trong tiến trình web → giới hạn số thí sinh, làm chậm web.
2. **Classroom:** các khâu chạy nối tiếp; YOLO tiền xử lý trên CPU; YOLO và model hướng đầu chạy PyTorch thay vì TensorRT; vẽ và nén JPEG mỗi khung kể cả khi không ai xem. Classroom LIVE cũng chạy dạng luồng trong tiến trình web.
3. **RAM:** bộ đệm bằng chứng Classroom giữ khung thô (~1,8 GB/phòng).
4. **Nhiều phòng:** mỗi phòng tự nạp model, không gom khung chạy chung.
5. **Hub/web:** chưa phải nút thắt (SQLite đã bật WAL; các trang hỏi lại máy chủ mỗi 1,5–5 s; luồng hình nén một lần cho mọi người xem).

## 3. Kế hoạch

Sau mỗi bước: chạy toàn bộ test, đo lại theo mục 5, ghi kết quả vào bảng ở mục 6.

### Bước 1 — Đo tải VIGIL One trên Hub
- Bật phân tích phía Hub (`VIGIL_ONE_ANALYSIS=1`), chạy `scripts/one_load_test.py` với 5, 10, 20, 40 thí sinh giả lập.
- Ghi: độ trễ phân tích (`lag_s`) từng thí sinh, CPU/RAM của Hub, thời gian phản hồi API `/online`.
- Kết quả cần có: số thí sinh tối đa mà Hub theo kịp thời gian thực.

### Bước 2 — Tách phân tích ra tiến trình riêng
- VIGIL One: một tiến trình phân tích mỗi nhân CPU (hoặc phát triển `classroom_monitor/worker_node.py` thành máy phân tích riêng); tiến trình web chỉ nhận dữ liệu và trả kết quả.
- Classroom LIVE: chạy phân tích trong tiến trình riêng, web chỉ đọc khung đã vẽ.
- Ước tính: máy 8 nhân theo kịp ~15–20 thí sinh One; web không còn bị chậm.

### Bước 3 — Giảm việc thừa và RAM (ít rủi ro)
- Chỉ vẽ và nén JPEG khi có người đang xem trang LIVE/Wall; khi có thì làm trên luồng riêng, ảnh phát lên web thu nhỏ còn 1280 hoặc 960. `result.mp4` vẫn ghi đủ.
- Bộ đệm bằng chứng lưu khung đã nén (hoặc luồng H.264 gốc của camera), chỉ cắt clip khi có tín hiệu. RAM mỗi phòng từ ~1,8 GB xuống vài chục MB.
- Ước tính: tiết kiệm 4 ms vẽ + 5–10 ms nén mỗi khung; mở đường cho nhiều phòng trên một máy.

### Bước 4 — Tăng tốc YOLO-Pose
- Xuất TensorRT FP16 (`format=engine` của Ultralytics), mỗi máy build engine một lần, giữ PyTorch làm dự phòng.
- Tiền xử lý trên GPU (resize, chuẩn hoá) thay vì CPU.
- Ước tính: suy luận 12,5 → ~5 ms; tiền xử lý 7,3 → ~1 ms.

### Bước 5 — Chồng các khâu lên nhau
- GPU chạy YOLO cho khung N+1 trong khi CPU xử lý theo thời gian và vẽ khung N; giữ đúng thứ tự khung.
- Ước tính: giảm 25–35 % thời gian mỗi khung.

### Bước 6 — Tăng tốc model hướng đầu
- Kiểm tra SixDRepNet (RepVGG) đã ở dạng gộp nhánh để suy luận chưa; chuyển sang TensorRT FP16.
- Đối chiếu góc đầu với bản PyTorch trên cùng tập khung: sai lệch phải < 1°.
- Ước tính: 18 → ~6–8 ms/khung.

### Bước 7 — Nhiều phòng trên một máy
- Một tiến trình phân tích mỗi GPU, dùng chung model, gom khung của nhiều phòng thành một lô.
- Giải mã camera bằng NVDEC.
- Trang LIVE cho xem nhiều phòng cùng lúc như trang Wall.
- Ước tính sau bước 3–6: một phòng ~30–40 khung phân tích/s trên RTX 4060 laptop; với 5–10 khung/s mỗi phòng, máy này gánh ~3–4 phòng.

### Không làm
- Giảm `imgsz` xuống 960 (đã đo: mất 1/3 thí sinh).
- INT8 cho YOLO khi chưa có dữ liệu gán nhãn để đối chiếu (đầu nhỏ ở xa dễ mất).
- cuDNN benchmark / gom lô cố định (đã thử, không có lợi trên máy này).

## 4. Quy mô 20–30 phòng (triển khai thật)

### 4.1 Nhu cầu
- Mỗi phòng ~5 khung phân tích/s (theo mục tiêu 5 Hz/ghế của model hướng đầu; cần xác nhận lại trên dữ liệu có gán nhãn).
- 20–30 phòng ≈ 100–150 khung/s ở 1280; khoảng 2.000–4.500 lần chạy model hướng đầu mỗi giây.
- Với code hiện tại: ~3 phòng mỗi GPU. Sau bước 2–7: ước tính 8–12 phòng mỗi NVIDIA L4, 10–15 phòng mỗi GPU cỡ RTX 4090.

### 4.2 Cấu hình máy chủ đề xuất (sau khi tối ưu phần mềm)

| Thành phần | Đề xuất |
|---|---|
| Khung máy | Máy chủ GPU 2U, ví dụ Dell PowerEdge R760xa, HPE ProLiant DL380a Gen11, dòng GPU server của Supermicro |
| GPU | **4 × NVIDIA L4** (3 tải + 1 dự phòng), hoặc 2 × L40S |
| CPU | 1 × AMD EPYC 32 nhân, hoặc 2 × Intel Xeon Gold 16 nhân |
| RAM | 256 GB (tối thiểu 128 GB khi bộ đệm bằng chứng đã nén) |
| Ổ hệ thống và cơ sở dữ liệu | 2 × NVMe 1,92 TB, RAID 1 |
| Ổ lưu video và bằng chứng | 20–40 TB, RAID 6 hoặc RAID 10 |
| Mạng | 2 × 10 GbE; camera ở VLAN riêng |
| Nguồn | Nguồn kép dự phòng, có UPS |

Phương án chia nhỏ: Hub không GPU (8 nhân, 32 GB) + 3–5 máy phân tích, mỗi máy 1 GPU cỡ 4070 Ti Super/4080, nhận 6–10 phòng.

Camera: IP 1080p, H.264, 15 khung/s (~4 Mbps/camera, 30 camera ~120 Mbps). Phòng sâu nên dùng 2 camera.

### 4.3 Chi phí ước tính (tỷ giá giả định ~26.000 VND/USD)

| Hạng mục | Triệu VND |
|---|---|
| 4 × NVIDIA L4 | 240–480 |
| Máy chủ 2U chưa gồm GPU (CPU 32 nhân, 256 GB RAM, 2 × NVMe) | 300–700 |
| Ổ lưu video 20–40 TB | 60–150 |
| Switch 10 GbE và switch PoE | 30–80 |
| UPS online 3 kVA, tủ rack | 40–90 |
| **Tổng** | **~700–1.500** |

- 2 × L40S thay cho 4 × L4: tổng ~0,9–1,6 tỷ.
- Máy trạm 2 × RTX 4090 (chỉ để thử nghiệm): ~200–350 triệu (chưa tra giá); điều khoản driver NVIDIA không cho dùng GeForce trong trung tâm dữ liệu, không ECC.
- Chưa gồm: camera (~2–5 triệu/chiếc, ước tính), công phát triển phần mềm.
- Giá GPU máy chủ tại Việt Nam chênh hơn 2 lần giữa các nhà bán (L4: ~50–118 triệu/card) → lấy báo giá chính thức trước khi lập ngân sách.

### 4.4 Trước khi mua
1. Làm xong bước 2–7.
2. Thuê L4 trên đám mây (~0,33–1,23 USD mỗi GPU mỗi giờ) vài giờ; chạy song song 10, rồi 15 luồng Classroom; đo số phòng mỗi card giữ được 5 khung/s.
3. Có số đo mới chốt số card và xin báo giá từ đối tác Dell/HPE tại Việt Nam.

Không nên chạy thật trên đám mây: phải đẩy ~120 Mbps video liên tục, và video thí sinh rời khỏi trường (quyền riêng tư, nơi lưu dữ liệu).

## 5. Cách đo

Classroom, mỗi lần đo một thư mục `--output` riêng, **ít nhất 1800 khung** (300 khung cho số thấp giả vì nạp model và ghi bằng chứng chiếm phần lớn thời gian):

```powershell
venv\Scripts\python.exe scripts\run_demo_video.py --video classroom-01 --max-frames 1800 --stride 0 --output data\bench\<tên-lần-đo>
```

Đọc `steady_analysed_fps`, `steady_realtime_factor`, `stage_latency_averages_ms`, `yolo_breakdown_ms` trong `runtime_profile.json`, và `active_occupied_seats` trong `demo_summary.json` (để chắc tối ưu không làm mất thí sinh).

Điều kiện đo: cắm sạc, chế độ nguồn hiệu năng cao, đóng ứng dụng nặng, ghi lại điều kiện cùng kết quả.

## 6. Nhật ký kết quả

| Ngày | Bước | Khung phân tích/s | So với thời gian thực | Ghế có người | Ghi chú |
|---|---|---|---|---|---|
| 2026-10-17 | Mốc ban đầu (1280) | 15,9 | 1,00× | 15/33 | Balanced, GPU có tải khác |
| 2026-10-17 | `--imgsz 960` | 20,7 | 1,00× | 10/33 | Loại |
| 2026-10-17 | 3 phòng cùng lúc | 2,9–3,4 mỗi phòng | 0,68–0,94× | — | 1 tiến trình hết RAM |

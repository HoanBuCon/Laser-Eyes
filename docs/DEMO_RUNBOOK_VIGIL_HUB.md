# Runbook demo — VIGIL Hub (Classroom + VIGIL One)

Áp dụng cho nhánh `feat/one-server-analysis`. Thay thế phần vận hành của `DEMO_RUNBOOK.md` (đã lỗi thời).

## Kiến trúc trong một câu

**VIGIL Hub** (máy chủ + website) làm mọi việc: giám sát phòng thi bằng camera lớp học (Classroom) và phân tích
camera của từng thí sinh thi trực tuyến (One). **App VIGIL One** trên máy thí sinh chỉ có một nhiệm vụ: thí sinh nhập
mã kỳ thi + mã thí sinh + PIN, rồi app gửi hình camera về Hub — không phân tích, không lưu kết luận.

| Luồng | Từ app VIGIL One lên Hub | Hub dùng để |
|---|---|---|
| Trực tiếp | WebSocket, ảnh JPEG ~10 khung/giây (640 px) | Phân tích ngay (trễ ~1 giây), giám thị xem trực tiếp trên `/one-monitor` |
| Lưu trữ | Đoạn video H.264 10 giây qua hàng đợi (mất mạng thì giữ lại, gửi sau) | Video cả buổi, cắt clip bằng chứng, phân tích bù những quãng luồng trực tiếp bị đứt |

## Khởi động — một lệnh, tại thư mục gốc

```powershell
python vigil.py --demo
```

Chạy được nguyên văn trong PowerShell, cmd và Git Bash (hoặc nhấp đúp `start_vigil.bat`):
- tạo `data/vigil_demo.db` nếu chưa có (tài khoản `admin` / `chief` / `proctor`, mật khẩu `vigil-demo-2026`);
- khởi động VIGIL Hub, mở website `http://localhost:8000`;
- mở app thí sinh VIGIL One, đã trỏ sẵn về Hub trên máy này.

`Ctrl+C` trong cửa sổ đó dừng tất cả. Nếu trên cổng 8000 còn một Hub chạy **code cũ** (trước `git pull` / đổi nhánh)
hoặc **database khác**, `vigil.py` tự dừng nó và khởi động lại — Hub cũ là nguyên nhân hay gặp của lỗi
"mục bị Hub từ chối".

| Lệnh | Khởi động |
|---|---|
| `python vigil.py` | Như trên, trên database làm việc `data/vigil_proctoring.db` |
| `python vigil.py --demo --sim` | App VIGIL One với camera mô phỏng (không cần webcam) |
| `python vigil.py --demo --hub-only` | Chỉ Hub và website (máy giám thị) |
| `python vigil.py --demo --hub-only --lan --demo-token <bí-mật>` | Hub nhận kết nối từ các máy khác trong mạng nội bộ |
| `python vigil.py --one-only --hub http://<IP-máy-Hub>:8000` | Chỉ app VIGIL One (máy thí sinh) |
| `python vigil.py --legacy` | App VIGIL One đời cũ (tự phân tích trên máy) — chỉ để so sánh, không dùng khi thi |

`main.py` và `server.py` là thành phần do `vigil.py` gọi; không cần chạy trực tiếp.

## 1. Chuẩn bị kỳ thi trực tuyến (web)

Đăng nhập `chief` → **Online exams** (`/online`) → chọn kỳ thi → **Exam setup**:
- **Open joining** để lấy mã vào thi (7 ký tự);
- dán danh sách thí sinh (mỗi dòng: `mã, họ tên, PIN`; bỏ trống PIN thì hệ thống tự cấp) → **Download CSV** để phát PIN;
- chính sách: micro, cho phép tài liệu, ghi video cả buổi, clip theo tín hiệu, và **ngưỡng thời gian** cho từng loại
  tín hiệu (ví dụ mắt ngoài màn hình 1,6 giây). Đổi ngưỡng giữa buổi thì thí sinh đang thi áp dụng ngay.

## 2. Kịch bản trình diễn (≈ 10 phút)

| Bước | Trên app VIGIL One (thí sinh) | Trên web (giám thị) |
|---|---|---|
| Vào thi | Nhập mã kỳ thi, mã thí sinh, PIN → đồng ý → kiểm tra thiết bị (Hub chấm: đủ sáng, đúng một khuôn mặt) → **BẮT ĐẦU LÀM BÀI** | **Operations** (chọn **One** hoặc **All** ở đầu thanh bên): bảng VIGIL One hiện thí sinh, "Hub · calibrated" |
| Hiệu chỉnh | Cửa sổ nhỏ nổi góc phải: "Nhìn vào giữa màn hình vài giây…" cho tới khi Hub hiệu chỉnh xong | Bấm vào thí sinh → **Candidate monitor** (`/one-monitor`): hình trực tiếp, lớp chú thích vùng nhìn an toàn / hướng mắt |
| Giám sát | Thí sinh nhìn ra ngoài, rời chỗ, có người thứ hai, cầm điện thoại | Số đo cập nhật mỗi giây; tín hiệu xuất hiện sau ~1–2 giây kèm ảnh. **Mark frame** (`Ctrl+B`) đánh dấu khung hình; **Recalibrate gaze** cho thí sinh hiệu chỉnh lại; **Books / notes allowed** cho riêng thí sinh này |
| Rà soát | — | `/review` lọc **VIGIL One**: ảnh + clip 9 giây, "SHA-256 verified"; Confirm / Reject / Unsure |
| Kết thúc | **NỘP BÀI** | **Whole exam video** (`/one-video`): một file cho cả buổi, bấm tín hiệu để nhảy tới lúc đó; `/reports`: duyệt báo cáo phiên |
| Độ bền (tùy chọn) | Tắt Wi-Fi 30 giây rồi bật lại: app giữ video, tự gửi lại | Hub phân tích bù quãng mất hình trực tiếp từ video lưu trữ; không mất, không trùng tín hiệu |

Classroom chạy song song: `/demo` → chọn phòng → **Start LIVE**; `/wall` ghép ô phòng thi và ô thí sinh One.

## 3. Thí sinh mô phỏng

Thêm thí sinh mô phỏng bằng một video webcam đã quay sẵn (Hub phân tích thật, như thí sinh thật):

```powershell
python scripts\one_fake_client.py --hub http://127.0.0.1:8000 --join <mã> --candidate HS004 --pin <PIN> --video D:\webcam.mp4
```

`--speed 2` phát nhanh gấp đôi; `--no-live` chỉ gửi video lưu trữ (giống thí sinh qua Internet chậm, tín hiệu trễ
~10–15 giây). Không có video: bỏ `--video`, thêm `--seconds 600` để có thí sinh với tín hiệu dựng sẵn.

## 4. Phương án dự phòng

| Sự cố | Cách xử lý |
|---|---|
| Mạng hội trường chặn kết nối giữa hai máy | Chạy tất cả trên một laptop: `python vigil.py --demo` |
| Webcam hỏng / không có quyền camera | `python vigil.py --demo --sim` (mọi màn hình gắn nhãn SIMULATION) |
| App báo "N mục bị máy chủ từ chối: …" | Đọc lý do hiện ngay dưới dòng trạng thái; thường do Hub cũ — chạy lại `python vigil.py --demo` |
| Hub quá tải khi nhiều thí sinh | Tăng `VIGIL_ONE_ANALYSIS_WORKERS` (mặc định 2); Hub tự bỏ bớt khung khi không kịp, không bị dồn trễ |
| Pipeline Classroom chậm | Dùng replay preset thay cho phân tích live |
| Mọi thứ hỏng | Phát video giới thiệu `videos/vigil-ai-promo/renders/vigil-promo-v6-final2-*-1080p60.mp4` |

## 5. Kiểm tra nhanh trước giờ demo

```powershell
venv\Scripts\python.exe -m pytest -q
```

- [ ] Toàn bộ test pass.
- [ ] `python vigil.py --demo` mở được website và app VIGIL One.
- [ ] Thí sinh vào thi được; `/one-monitor` có hình trực tiếp, "Analysis on the Hub: live".
- [ ] Tín hiệu vào `/review` trong vài giây, có ảnh; clip có sau ~10 giây.
- [ ] Rút mạng 30 giây rồi nối lại: không mất video, không mất tín hiệu.
- [ ] Duyệt được báo cáo phiên One; video cả buổi ở trạng thái READY.

## 6. Lưu ý vận hành

- App VIGIL One không phân tích và không cần GPU; nó không nạp MediaPipe/torch. Mọi phân tích chạy trên Hub
  (`storage/one_analysis.py`), cùng máy với Classroom. Biến môi trường của Hub: `VIGIL_ONE_ANALYSIS=0` tắt phân tích
  One; `VIGIL_ONE_ANALYSIS_WORKERS=<n>` số luồng (mỗi luồng nhận luồng trực tiếp lẫn video lưu trữ, mỗi thí sinh luôn
  do một luồng xử lý). Trạng thái: `GET /api/v1/one/analysis/status`, bảng VIGIL One ở Operations.
- Băng thông mỗi thí sinh khoảng 2–3 Mbps (hình trực tiếp 10 khung/giây) + 0,3 Mbps (video lưu trữ). 30 thí sinh ≈ 100 Mbps: dùng
  mạng có dây hoặc Wi-Fi 5 GHz tốt. Thi qua Internet: Hub cần `https://`, khi đó luồng trực tiếp đi qua `wss://`.
- Địa chỉ `http://` chỉ được chấp nhận với localhost, dải IP riêng (10.x, 172.16–31.x, 192.168.x) và cùng mạng /16 với
  máy thí sinh. Lần đầu chạy `--lan`, Windows hỏi tường lửa: cho phép mạng **Private**.
- Hình trực tiếp chỉ nằm trong bộ nhớ của Hub. Video lưu trữ, ảnh và clip bằng chứng nằm trong `data/one_evidence`
  (SHA-256 cho từng file); video cả buổi được ghép bằng FFmpeg (`-c copy`, không nén lại).
- Khởi động lại Hub giữa buổi **không** đóng phiên One đang chạy: app tự nối lại luồng trực tiếp, Hub phân tích bù
  phần bị đứt từ video lưu trữ và tiếp tục với hiệu chỉnh đã lưu.
- `/admin` › **Remove test rooms** không bao giờ xóa phòng ảo `VIGIL-ONE`. Mọi thao tác xóa/reset ở `/admin` cần đăng nhập
  tài khoản **ADMIN**, và bị từ chối khi còn thí sinh One đang kết nối hoặc Hub chưa phân tích xong video.

## 7. Hub quyết định, không phải máy thí sinh

- Phiên của thí sinh (vào bằng mã kỳ thi + PIN) luôn được phân tích trên Hub; chính sách kỳ thi không còn chế độ phân
  tích trên máy thí sinh. Phiên mô phỏng (`--sim`) chỉ được mở từ chính máy Hub.
- Tín hiệu hành vi của phiên phân tích trên Hub chỉ do Hub tạo; máy thí sinh chỉ báo được các sự kiện thiết bị (camera ảo,
  nhiều màn hình, camera dừng). Tên file bằng chứng do Hub đặt. Thời điểm khung hình trực tiếp phải khớp đồng hồ của Hub.
- **Phát hiện nói chuyện** dùng micro + chuyển động môi. Hub phân tích 10 khung/giây (chuyển động môi cần từ 8 khung/giây);
  khi Hub quá tải hoặc mạng yếu làm tốc độ thực tế thấp hơn, trang `/one-monitor` ghi rõ "lips too slow".
- **Nộp bài:** app chỉ báo "đã nhận đủ dữ liệu" khi không còn mục chờ gửi và không mục nào bị từ chối. Có mục bị từ chối
  thì app hiện lý do và nút **GỬI LẠI**; thí sinh báo giám thị, không tắt máy.
- **Duyệt báo cáo One:** nếu Hub còn đoạn video chưa phân tích, clip chưa cắt, hoặc luồng trực tiếp còn mở, trưởng ban phải
  ghi lý do mới duyệt được; danh sách phần còn thiếu được lưu trong bản báo cáo đã duyệt. Sau khi đóng, phần còn lại không
  thành tín hiệu nữa. Clip không thể cắt (video không về sau 10 phút) được ghi lý do trên bằng chứng.
- **Bảng VIGIL One ở Operations** báo `FACE MODEL MISSING` (đỏ) khi Hub chạy bản dự phòng OpenCV vì MediaPipe lỗi.
- Bật `VIGIL_AUTH_MODE=required` khi thi thật: mọi trang, API và WebSocket sự kiện đều cần đăng nhập.
- Thêm class Tailwind vào trang web thì build lại CSS theo lệnh ở đầu `dashboard/tailwind.config.js` (website chạy
  không cần Internet).

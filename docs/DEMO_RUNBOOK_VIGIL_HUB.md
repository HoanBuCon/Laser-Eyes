# Runbook demo — VIGIL Hub (Classroom) + VIGIL One

Áp dụng cho nhánh `feat/vigil-one-hub`. Thay thế phần vận hành của `DEMO_RUNBOOK.md` (đã lỗi thời).

## Khởi động nhanh (một lệnh, tại thư mục gốc)

```powershell
python vigil.py --demo
```

Lệnh này chạy được nguyên văn trong PowerShell lẫn cmd, không cần `.\`:
- tạo `data/vigil_demo.db` nếu chưa có;
- khởi động VIGIL Hub và mở website;
- tự ghép nối VIGIL One trên cùng máy với Hub;
- mở app VIGIL One.

`Ctrl+C` trong cửa sổ đó dừng tất cả.

Các tùy chọn khác:

| Lệnh | Khởi động |
|---|---|
| `python vigil.py` | Như trên, nhưng dùng database làm việc |
| `python vigil.py --demo --one-sim` | One vào ngay một phiên mô phỏng |
| `python vigil.py --hub-only` | Chỉ server và website (máy giám thị) |
| `python vigil.py --one-only` | Chỉ VIGIL One (laptop thí sinh, đã ghép nối với Hub ở máy khác) |
| `python vigil.py --demo --lan --demo-token <bí-mật>` | Hub nhận kết nối từ máy khác trong LAN |

`start_vigil.bat` vẫn dùng được: trong PowerShell gõ `.\start_vigil.bat`.

## 0. Chuẩn bị thủ công (khi không dùng `vigil.py`)

1. Tạo database demo riêng, không đụng DB làm việc:

   ```powershell
   venv\Scripts\python.exe scripts\seed_demo.py --database data\vigil_demo.db
   ```

   Script tạo (chạy lại an toàn, không nhân đôi dữ liệu):
   - tài khoản `admin`, `chief`, `proctor`, mật khẩu mặc định `vigil-demo-2026` (đổi bằng `--password`);
   - kỳ thi "Kỳ thi trình diễn VIGIL";
   - các phòng Classroom có sẵn video demo;
   - phòng ảo `VIGIL-ONE`;
   - một mã ghép nối (hết hạn sau 10 phút; tạo mã mới trên web khi cần).

2. Khởi động Hub trên database demo:

   ```powershell
   set DATABASE_URL=sqlite:///data/vigil_demo.db
   start_vigil.bat --host 0.0.0.0
   ```

   Dùng `--host 0.0.0.0` khi VIGIL One chạy trên laptop khác trong cùng mạng LAN. Ghi lại địa chỉ IP của máy Hub (`ipconfig`).

3. Hiệu chỉnh ghế cho phòng Classroom dùng trong demo trên `/calibration` (nếu chưa có Seat ROI).

## 1. Ghép nối máy VIGIL One

1. Trên web, đăng nhập `chief`, vào **System** (`/admin`), mục **VIGIL One computers**, bấm **Create pairing code**.
2. Trên laptop thí sinh: `venv\Scripts\python.exe main.py` → **Cài đặt** → **Kết nối VIGIL Hub**.
   - Địa chỉ Hub: `http://<IP-máy-Hub>:8000`.
   - Nhập mã → **GHÉP NỐI**.
3. Góc trái dưới của One chuyển sang **● ĐÃ ĐỒNG BỘ**; ô **Kỳ thi** lấy danh sách kỳ thi từ Hub.

Thu hồi một máy: `/admin` → **Revoke**. Máy đó vẫn giữ dữ liệu cục bộ, báo "CẦN GHÉP NỐI LẠI" và ngừng gửi.

## 2. Kịch bản trình diễn (≈ 10 phút)

| Bước | Trên VIGIL One | Trên web (giám thị) |
|---|---|---|
| Thiết lập | Chọn kỳ thi, nhập mã thí sinh, nguồn **Camera trực tiếp**, **BẮT ĐẦU GIÁM SÁT**, nhìn vào tâm màn hình để hiệu chỉnh | `/wall`: một ô chọn phòng Classroom, một ô **VIGIL One · candidate #1 (auto)** |
| Giám sát | Thí sinh nhìn ra ngoài, cầm điện thoại; **Ctrl+B** để đánh dấu khung hình | Ô One cập nhật mỗi 2 giây; nhãn đổi thành `… · N SIGNALS` |
| Rà soát | — | `/review`, lọc **VIGIL One**: tín hiệu tiếng Việt, ảnh bằng chứng, "SHA-256 verified"; bookmark hiện là đánh dấu thủ công; Confirm / Reject / Unsure |
| Kết thúc | **KẾT THÚC PHIÊN** | `/reports`: báo cáo phiên One có mục Thí sinh; duyệt → đóng băng phiên bản SHA-256; bản in tiếng Việt |
| Độ bền (tùy chọn) | Tắt Wi-Fi laptop: One báo **MẤT KẾT NỐI HUB**, đếm số mục giữ lại; bật lại → **ĐÃ ĐỒNG BỘ** | Dữ liệu lên đủ, không trùng |

## 3. Phương án dự phòng

| Sự cố | Cách xử lý |
|---|---|
| Mạng hội trường chặn kết nối giữa hai máy | Chạy Hub và One trên cùng một laptop, địa chỉ `http://127.0.0.1:8000` |
| Webcam hỏng / không có quyền camera | One chọn nguồn **Mô phỏng có sẵn** (mọi màn hình đều gắn nhãn MÔ PHỎNG / SIMULATION) |
| Cần thêm thí sinh trên video wall | `venv\Scripts\python.exe scripts\one_fake_client.py --hub http://127.0.0.1:8000 --code <mã> --candidate SV-02 --seconds 600` |
| Pipeline Classroom chậm | Dùng replay preset thay cho phân tích live |
| Mọi thứ hỏng | Phát video giới thiệu `videos/vigil-ai-promo/renders/vigil-promo-v6-final2-*-1080p60.mp4` |

## 4. Kiểm tra nhanh trước giờ demo

```powershell
venv\Scripts\python.exe -m pytest -q
```

- [ ] Toàn bộ test pass.
- [ ] Đăng nhập được ba tài khoản.
- [ ] Ghép nối One thành công.
- [ ] Tín hiệu One vào `/review` trong vài giây.
- [ ] Ô One trên `/wall` có hình.
- [ ] Rút mạng 60 giây rồi nối lại: không mất dữ liệu.
- [ ] Duyệt được báo cáo phiên One.

## 5. Lưu ý vận hành

- Dữ liệu One luôn được ghi trên máy thí sinh trước (`data/sessions`, `data/evidence`), rồi mới gửi lên Hub qua hàng đợi `data/one_outbox.db`.
- Ảnh live thu nhỏ chỉ nằm trong bộ nhớ của Hub, không ghi xuống đĩa. Tắt được trong **Cài đặt** của One.
- Khởi động lại Hub giữa buổi **không** đóng phiên One đang chạy; phiên Classroom đang chạy thì được đánh dấu `INTERRUPTED` như trước.
- `/admin` › **Remove test rooms** không bao giờ xóa phòng ảo `VIGIL-ONE`.


## 6. Thi trực tuyến (học sinh dùng VIGIL One trên máy riêng)

1. **Hub:** cùng máy thì `python vigil.py --demo --hub-only`; máy thí sinh khác trong mạng nội bộ thì `python vigil.py --demo --hub-only --lan --demo-token <bí-mật>` và lấy IP máy Hub bằng `ipconfig` (lần đầu Windows hỏi tường lửa: cho phép mạng **Private**).
   VIGIL One gửi thẳng tới Hub qua mạng nội bộ, không cần phần mềm trung gian. Địa chỉ `http://` được chấp nhận với localhost, dải IP riêng (10.x, 172.16–31.x, 192.168.x) và mọi địa chỉ cùng mạng /16 với máy thí sinh (ví dụ `172.172.x.x`). Hub đặt ở xa trên Internet thì phải có `https://`.
   Ngoài ảnh live và ảnh bằng chứng, mỗi tín hiệu kèm **clip MP4** (5 giây trước + 4 giây sau), bật/tắt ở chính sách kỳ thi.
   **Video toàn bộ buổi thi:** VIGIL One ghi liên tục (H.264, mặc định 640 px, 10 fps, khoảng 1,5–3 MB/phút) và gửi lên Hub từng đoạn 1 phút trong lúc thi (mất mạng thì giữ lại và gửi sau). Khi phiên kết thúc, Hub ghép thành **một file `session.mp4`** cho cả buổi (ffmpeg `-c copy`, không nén lại) kèm SHA-256; các đoạn gốc và SHA-256 của chúng được giữ. Xem tại `/one-video?session=<id>` (nút **Whole exam video** ở `/online` và ở trang Review): bấm một tín hiệu để nhảy tới đúng lúc đó, các phút bị thiếu được ghi rõ. Hub cần **FFmpeg** để ghép; chưa ghép được thì trang vẫn phát nối từng đoạn.
2. **Web:** đăng nhập `chief` → **Online exams** (`/online`) → chọn kỳ thi → **Exam setup**:
   - **Open joining** để lấy mã vào thi (7 ký tự);
   - dán danh sách thí sinh (mỗi dòng: `mã, họ tên, PIN`; bỏ trống PIN thì hệ thống tự cấp);
   - **Download CSV** để phát PIN cho thí sinh;
   - chỉnh chính sách: micro, cho phép tài liệu, ảnh live.
3. **Máy học sinh:** `python main.py --candidate --hub <địa chỉ Hub>` (sau này là bộ cài VIGIL One). Thí sinh nhập mã kỳ thi, mã thí sinh, PIN → đồng ý → kiểm tra thiết bị → **BẮT ĐẦU LÀM BÀI**. Khi thi, cửa sổ nhỏ luôn nổi ở góc phải trên. Kết thúc bằng **NỘP BÀI**.
4. **Giám thị:** `/online` hiện lưới thí sinh; mất kết nối và nhiều tín hiệu lên đầu. Bấm vào một thí sinh để xem ảnh lớn (tự tăng tần suất) và các tín hiệu; quyết định tại `/review` (lọc **VIGIL One**).

Thử nhanh không cần webcam:

```powershell
python vigil.py --demo --candidate --one-sim
```

Thêm thí sinh mô phỏng:

```powershell
python scripts\one_fake_client.py --hub http://127.0.0.1:8000 --join <mã> --candidate HS003 --pin <PIN> --seconds 600
```

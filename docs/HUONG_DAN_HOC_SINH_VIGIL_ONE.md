# Hướng dẫn cài đặt và làm bài với VIGIL One

Dành cho học sinh tham gia kỳ thi trực tuyến có giám sát bằng VIGIL One.

## 1. Cần chuẩn bị

| Yêu cầu | Chi tiết |
|---|---|
| Máy tính | Windows 10 hoặc 11, 64-bit, RAM từ 4 GB |
| Webcam | Webcam tích hợp hoặc webcam USB đặt phía trên màn hình |
| Micro | Nếu kỳ thi yêu cầu (nhà trường sẽ thông báo) |
| Mạng | Ổn định trong suốt giờ thi. Nếu rớt mạng, phần mềm giữ dữ liệu và tự gửi lại khi có mạng |
| Thông tin từ nhà trường | Mã kỳ thi, mã thí sinh, mã PIN |
| Chỗ ngồi | Ngồi một mình, đủ sáng trên khuôn mặt, không ngồi ngược sáng |

## 2. Cài đặt (làm trước ngày thi)

1. Tải bộ cài do nhà trường gửi: `VIGIL-One-Setup-<phiên bản>.exe`, hoặc bản nén `VIGIL-One-<phiên bản>-win64.zip`.
2. **Bộ cài `.exe`:** mở file và bấm *Next* tới khi xong. Không cần quyền quản trị.
   **Bản `.zip`:** giải nén vào một thư mục (ví dụ `Documents\VIGIL One`), rồi mở `VIGIL One.exe`.
3. Windows có thể hiện cảnh báo *"Windows protected your PC"* vì phần mềm chưa có chữ ký số. Bấm **More info** rồi **Run anyway**. Chỉ làm vậy với file nhận trực tiếp từ nhà trường.
4. Khi Windows hỏi quyền dùng camera và micro, chọn **Cho phép**. Nếu lỡ từ chối: *Settings → Privacy & security → Camera / Microphone* → bật cho ứng dụng trên máy tính (desktop apps).

## 3. Trong giờ thi

1. Mở **VIGIL One**, nhập **Địa chỉ máy chủ thi** (nếu được hỏi, do giám thị cung cấp, ví dụ `http://192.168.1.10:8000`), **Mã kỳ thi**, **Mã thí sinh**, **Mã PIN**, rồi bấm **VÀO THI**.
2. Đọc thông báo về dữ liệu, tick **Tôi đã đọc và đồng ý**, rồi bấm **TIẾP TỤC**.
3. **Kiểm tra thiết bị:**
   - các mục Camera, Đúng một khuôn mặt, Ánh sáng phải có dấu ✓;
   - mục có dấu **!** vẫn cho làm bài, nhưng giám thị sẽ được báo (ví dụ đang nối hai màn hình).
4. Bấm **BẮT ĐẦU LÀM BÀI** và nhìn vào giữa màn hình vài giây để phần mềm hiệu chỉnh.
5. Cửa sổ VIGIL One thu nhỏ ở góc phải trên màn hình. Làm bài thi như bình thường trên trình duyệt hoặc phần mềm thi của nhà trường. **Không đóng cửa sổ VIGIL One.**
6. Làm xong, bấm **NỘP BÀI / KẾT THÚC**. Đợi dòng **"✓ Máy chủ thi đã nhận đủ dữ liệu"** rồi mới đóng cửa sổ và tắt máy.

## 4. Lưu ý

- Đóng VIGIL One trước khi bấm Nộp bài sẽ được ghi nhận là **rời phòng thi sớm** và báo cho giám thị.
- Nếu phần mềm báo **"Không thấy khuôn mặt"** hoặc **"Hình quá tối"**, hãy chỉnh lại chỗ ngồi hoặc ánh sáng.
- Các tín hiệu của phần mềm chỉ để giám thị **xem xét**; phần mềm không tự kết luận vi phạm.
- Không dùng camera ảo (OBS, ManyCam…) và không cho người khác ngồi cùng.
- Phần mềm bị tắt giữa chừng (mất điện, máy treo): mở lại, nhập lại mã và PIN, rồi làm bài tiếp. Giám thị sẽ thấy khoảng gián đoạn.

## 5. Gặp sự cố

| Hiện tượng | Cách xử lý |
|---|---|
| "Mã kỳ thi, mã thí sinh hoặc PIN không đúng, hoặc kỳ thi chưa mở" | Kiểm tra lại từng ký tự; kỳ thi có thể chưa tới giờ mở |
| "Không kết nối được tới máy chủ thi" | Kiểm tra mạng, tắt VPN, thử lại |
| "Không mở được camera" | Đóng các ứng dụng đang dùng camera (Zoom, Teams…), kiểm tra quyền camera |
| "Mất kết nối — N mục được giữ lại" | Cứ làm bài tiếp; dữ liệu tự gửi khi mạng trở lại |

Bộ phận hỗ trợ có thể nhờ bạn chạy lệnh kiểm tra cài đặt sau rồi gửi lại file `vigil-check.json`:

```
"VIGIL One.exe" --selftest vigil-check.json
```

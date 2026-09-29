# VIGIL Proctor Support — Human Acceptance Checklist

Trạng thái: engineering validation complete; chưa được coi là nghiệm thu giao diện hoặc sản phẩm.

## Classroom Web

Khởi động:

```powershell
venv\Scripts\python.exe server.py --host 127.0.0.1 --port 8000
```

Mở `http://127.0.0.1:8000/demo` và kiểm tra:

1. Chọn India hoặc Student, chọn LIVE/REPLAY rồi Start.
2. Khi có frame, bấm **Mark Frame**.
3. Xác nhận ảnh tĩnh trong modal không đổi trong lúc nhập ghi chú.
4. Có thể để trống seat hoặc nhập đúng `seat_code` đã calibration.
5. Lưu bookmark; card có nhãn `PROCTOR`, không có điểm AI giả.
6. Nếu seat tồn tại, mở card và chuyển giữa Overall/Seat crop; thử zoom `+/-`.
7. Trạng thái hash chỉ hiện VERIFIED sau khi server đọc lại file và so digest.
8. Chọn quyết định, refresh trang; quyết định và ghi chú còn nguyên.
9. Tắt WebSocket trong DevTools; polling vẫn lấy lại bookmark.
10. Mở một card AI có evidence; clip phát trong Chrome/Edge, nút Open/download hoạt động và hash vẫn đối chiếu file archive gốc.
11. Export JSON và CSV; CSV phải có cả dòng `AI_INCIDENT` và `MANUAL_BOOKMARK` (nếu phiên có cả hai), với ID, timestamp, decision và SHA tương ứng.
12. Reset rồi Start run mới; capture token của run cũ không được lưu sang run mới.

## Local Gaze

Khởi động:

```powershell
venv\Scripts\python.exe main.py
```

Kiểm tra:

1. Start chế độ mô phỏng hoặc nguồn thật.
2. Bấm **ĐÁNH DẤU FRAME** hoặc `Ctrl+B`, nhập mô tả quan sát.
3. Mở trang sự kiện, double-click dòng `Frame do giám thị đánh dấu`.
4. Xem ảnh, zoom `+/-`, lưu một trong ba quyết định con người.
5. Stop ngay sau một thao tác đánh dấu; phiên chỉ hoàn tất sau khi I/O bookmark xong.
6. Đóng/mở ứng dụng, vào trang sự kiện và bấm **MỞ PHIÊN ĐÃ LƯU**.
7. Xác nhận ảnh, note, decision và lịch sử review còn nguyên.
8. Export JSON/CSV; bookmark phải tách biệt khỏi AI event.

## Kiểm thử tự động

```powershell
venv\Scripts\python.exe -m pytest
```

Các test mới nằm tại `tests/test_proctor_support.py`, bao phủ capture token, stale run, idempotency, evidence READY/FAILED, SHA verification, chuyển mã playback H.264, ROI crop, review history, export AI/manual và Local JSON backward compatibility.

## Giới hạn còn lại

- Reviewer ID trong prototype là định danh do client khai báo; chưa có hệ thống authentication doanh nghiệp.
- Bookmark Classroom chỉ lưu ảnh; clip vẫn là evidence của incident AI hiện hữu.
- Máy demo cần `ffmpeg` và `ffprobe` trên `PATH` để tạo playback H.264 từ archive `mp4v`; lần mở clip đầu tiên có thể chậm hơn do chuyển mã, các lần sau dùng cache.
- Seat crop dùng polygon snapshot của seat tại lúc lưu bookmark; nếu `seat_code` trống/sai thì chỉ có ảnh toàn cảnh.
- Pan/zoom Web là viewer đơn giản, chưa phải công cụ forensic canvas chuyên dụng.
- Cần chủ sản phẩm chạy checklist trực quan trên màn hình/máy thi thật trước khi merge vào DEV.

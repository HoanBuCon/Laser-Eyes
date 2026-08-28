# VIGIL AI — Desktop MVP

Ứng dụng desktop chạy cục bộ để trình diễn hệ thống giám sát thi trực tuyến dựa trên điểm đặc trưng khuôn mặt, tư thế đầu và hướng nhìn tương đối.

## Đã có trong bản này

- Giao diện sáng tối giản mặc định, có nút chuyển Light/Dark và hỗ trợ resize cửa sổ.
- Chế độ trình chiếu toàn màn hình để video, trạng thái mắt/đầu/người và cảnh báo dễ quan sát khi thuyết trình; nhấn `F11` để bật/tắt, `Esc` để thoát.
- Ba nguồn hình ảnh: webcam, video trên máy và kịch bản mô phỏng có sẵn.
- Camera/video được chuẩn hóa cùng chiều và giới hạn khung xử lý 960 px để giữ hướng trái/phải nhất quán, giảm tải CPU.
- MediaPipe Face Landmarker chạy cục bộ, theo dõi tối đa 5 khuôn mặt, ước lượng tư thế đầu và hướng nhìn tương đối.
- EfficientDet chạy cục bộ để nhận biết người thứ hai, điện thoại và sách/tài liệu.
- Hiệu chỉnh theo từng thí sinh, làm mượt tín hiệu theo thời gian.
- Cơ chế giữ tracking ngắn hạn để không báo mất khuôn mặt chỉ vì rớt một vài frame.
- Tách riêng hướng mắt và hướng đầu; kiểm tra mống mắt còn nằm trong vùng nhìn an toàn hay đã nhìn ra ngoài.
- Hiển thị ellipse vùng mắt an toàn, vector hướng mắt và trạng thái trong/ngoài vùng ngay trên video.
- Xác nhận nói chuyện bằng WebRTC VAD từ microphone kết hợp nhịp môi; câu nói ngắn vẫn được giữ qua khoảng nghỉ giữa âm tiết, còn tiếng nền đơn thuần, môi khép bị rung landmark, giữ miệng mở hoặc một lần ngáp không đủ để cảnh báo.
- Cảnh báo mất khuôn mặt, từ 2 người trở lên, điện thoại/sách, ánh sáng kém và camera bị gián đoạn.
- Mỗi hành vi liên tục chỉ tạo một cảnh báo; hệ thống chỉ tái kích hoạt sau khi trạng thái bình thường ổn định, tránh popup lặp do tín hiệu chập chờn.
- Popup ưu tiên cảnh báo mức cao, gộp các cảnh báo đồng thời và chỉ lưu sự kiện mức thấp vào dòng thời gian thay vì làm gián đoạn màn hình.
- Lưu ảnh bằng chứng cục bộ, dòng sự kiện, trạng thái giám thị xem lại.
- Báo cáo phiên thi và xuất JSON/CSV.
- Cài đặt ngưỡng phát hiện ngay trong giao diện.

## Chạy ứng dụng

Cách nhanh nhất trên Windows:

1. Nhấp đúp `run_demo.bat` để mở thẳng chế độ mô phỏng.
2. Hoặc nhấp đúp `run_app.bat` để tự chọn camera, video hay mô phỏng.

Môi trường `.venv` đã được tạo cho dự án. `python main.py` cũng tự chuyển sang môi trường này nếu người dùng mở nhầm Python hệ thống. Nếu chuyển dự án sang máy khác, chạy `setup_env.bat` trước. Cần Python 3.12.

## Cấu trúc

```text
main.py                     Điểm khởi động
exam_monitor/app.py         Giao diện Tkinter và điều phối phiên
exam_monitor/engine.py      MediaPipe, hướng nhìn, lớp phủ hình ảnh
exam_monitor/events.py      Luật cảnh báo và lưu phiên
exam_monitor/models.py      Mô hình dữ liệu dùng chung
exam_monitor/sources.py     Camera, video và nguồn mô phỏng
exam_monitor/assets/        Model Face Landmarker chạy cục bộ
data/                       Báo cáo và ảnh bằng chứng sinh khi chạy
tests/                      Kiểm thử logic lõi
```

Phần xử lý được tách khỏi giao diện để sau này có thể dùng lại khi xây web API và frontend web.

## Các tình huống đang được kiểm tra

| Tình huống | Cách nhận biết | Mức mặc định |
|---|---|---|
| Mắt ra ngoài vùng thi | Khoảng cách mống mắt khỏi ellipse an toàn sau hiệu chỉnh | Trung bình |
| Quay đầu khỏi màn hình | Góc yaw/pitch từ mô hình khuôn mặt 3D | Trung bình |
| Nghi nói chuyện | Biến thiên độ mở miệng trong cửa sổ thời gian | Trung bình |
| Không thấy khuôn mặt | Mất landmark sau khoảng giữ tracking | Trung bình |
| Từ 2 người trở lên | Kết hợp số khuôn mặt và số người | Cao |
| Điện thoại hoặc sách/tài liệu | EfficientDet nhận diện vật thể | Cao |
| Thiếu sáng | Độ sáng trung bình của khung hình | Thấp |
| Camera/video gián đoạn | Không đọc được khung hình | Cao |

Ngưỡng thời gian của từng tình huống có thể chỉnh trong trang **Cài đặt**.

## Lưu ý quan trọng

Đây là MVP trình diễn. Hướng nhìn, chuyển động miệng, giọng nói và vật thể đều là tín hiệu rủi ro để giám thị xem lại, không phải bằng chứng duy nhất và không được dùng để tự động kết luận gian lận. Cảnh báo nói chuyện hiện kết hợp WebRTC VAD từ microphone với nhịp môi nhưng không nhận dạng hay lưu nội dung lời nói. Trước khi triển khai thật cần đánh giá sai số trên nhiều điều kiện, cơ chế hỗ trợ tiếp cận, chính sách đồng ý, thời hạn lưu và quyền truy cập dữ liệu sinh trắc học.

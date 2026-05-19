# Sơ đồ khối (Flowcharts) các chức năng chính

Dưới đây là sơ đồ luồng hoạt động (flowchart) cho 3 chức năng chính được lấy từ mã nguồn của dự án (thư mục `modules`):

## 1. Phát hiện và Nhận diện Biển số xe (OCR)

Luồng hoạt động của Module OCR (Dựa trên `ocr_processor.py` và `ocr_manager.py`).

```mermaid
flowchart TD
    A["Nhận frame ảnh và Bounding box xe từ YOLO"] --> B{"Box xe có chứa biển số?"}
    B -- Không --> C["Bỏ qua"]
    B -- Có --> D["Lấy ảnh crop biển số"]
    
    D --> E["Đưa vào Hàng đợi (Queue)"]
    E --> F["Worker Thread: OCR\nLấy ảnh từ Queue"]
    
    subgraph OCR_Processor ["Xử lý hình ảnh & Nhận diện"]
        F --> G["get_plate_perspective:\nTìm contour lớn nhất và căn lại góc chiếu"]
        G --> H["preprocess_plate:\nPhóng to ảnh (2x), chuyển xám, cân bằng sáng CLAHE"]
        H --> I["PaddleOCR:\nChạy nhận diện chữ/số"]
        I --> J["Làm sạch văn bản:\nXóa ký tự đặc biệt, chỉ giữ A-Z, 0-9"]
        J --> K["correct_plate_format:\nSửa lỗi logic vị trí (vd: O->0, 8->B)"]
    end
    
    K --> L{"Kiểm tra Regex\nBiển số VN hợp lệ?"}
    L -- Không --> M["Báo lỗi/Bỏ qua (SKIP)"]
    L -- Có --> N["Thêm kết quả vào lịch sử (History)"]
    
    N --> O{"Số lần xuất hiện giống nhau\n>= VOTE_THRESHOLD (3 lõi)?"}
    O -- Không --> P["Đánh dấu '?' và tiếp tục chờ thêm kết quả"]
    O -- Có --> Q["Xác nhận biển số chính xác\nĐánh dấu 'OK'"]
    Q --> R["Cập nhật Spatial Memory,\nGhi log và Cache lại"]
    
    P --> S["Hiển thị ID, Text lên luồng Video chính"]
    R --> S
```

---

## 2. Phát hiện xe dừng đỗ sai quy định (Parking Logic)

Luồng hoạt động của Module Kiểm tra Dừng đỗ (Dựa trên `parking_logic.py` và `parking_manager.py`).

```mermaid
flowchart TD
    A["Cập nhật vị trí tracking ID của xe"] --> B["Lưu toạ độ tâm vào lịch sử\n(Giữ tối đa 10 frame gần nhất)"]
    B --> C["Tính toán tốc độ trung bình (avg_speed)\ncủa xe dựa vào lịch sử di chuyển"]
    C --> D{"Trạng thái hiện tại của xe?"}
    
    D -- RECORDING_DONE --> E["Xe đã xử lý xong vi phạm\n(Bỏ qua)"]
    
    D -- MOVING --> F{"Tốc độ < Ngưỡng tĩnh\n(move_thr_px)?"}
    F -- Có --> G["Chuyển trạng thái: WAITING\nBắt đầu đếm khung hình"]
    F -- Không --> H["Giữ nguyên trạng thái MOVING"]
    
    D -- WAITING --> I{"Tốc độ >= Ngưỡng tĩnh\n(Xe có dấu hiệu di chuyển)?"}
    I -- Có --> J["Tăng biến ân hạn\n(grace_count)"]
    I -- Không --> K["Đặt lại grace_count = 0"]
    
    J --> L{"grace_count > 10 frame?"}
    L -- Có --> M["Xe đã di chuyển thật sự!\nTrở lại MOVING"]
    L -- Không --> N
    
    K --> N{"Số frame đã chờ trong WAITING\n>= Ngưỡng vi phạm\n(stop_frames)?"}
    N -- Không --> O["Giữ nguyên trạng thái WAITING"]
    N -- Có --> P["Chuyển trạng thái: VIOLATION\n(Vi phạm dừng đỗ)"]
    
    D -- VIOLATION --> Q["ParkingManager: Lấy luồng Video\nThu thập ~10s video"]
    Q --> R["Gửi cảnh báo Telegram &\nChuyển sang RECORDING_DONE"]
```

---

## 3. Phát hiện tắc nghẽn giao thông (Traffic Monitor)

Luồng hoạt động của Module Đánh giá giao thông (Dựa trên `traffic_monitor.py`).

```mermaid
flowchart TD
    A["Cập nhật luồng Frame mới"] --> B["Lấy các Bounding Box xe\nthuộc vùng ROI đã khoanh"]
    B --> C["Lọc Bounding Box nhiễu\n(Bỏ qua hộp to hơn 30% ROI)"]
    C --> D["Tính tổng số Xe và Người\ntrong ROI"]
    D --> E["Tính toán khoảng cách di chuyển để suy ra\nVận tốc trung bình của các xe"]
    E --> F["Tạo Bitwise Mask của ROI và xe\n=> Tính % diện tích chiếm chỗ (Occupancy)"]
    
    F --> G["KIỂM TRA CÁC MỨC ĐỘ CẢNH BÁO"]
    G --> H{"Occupancy (Tỉ lệ diện tích)\n>= 40% ?"}
    
    H -- "Không (<40%)" --> I{"Số Xe >= 10\nHoặc Người >= 30?"}
    I -- Có --> J["Cấp độ 1: ĐÔNG ĐÚC\n(Mật độ xe/người cao nhưng đường vẫn rộng)"]
    I -- Không --> K["Cấp độ 0: THÔNG THOÁNG\n(Lưu lượng bình thường)"]
    
    H -- "Có (>=40%)" --> L{"Vận tốc trung bình\n<= 10 px/s ?"}
    L -- Không --> M["Cấp độ 2: RẤT ĐÔNG\n(Đường kẹt nhưng xe vẫn đang nhích được)"]
    L -- Có --> N["Cấp độ 3: TẮC NGHẼN\n(Xe kẹt cứng, không thể nhúc nhích)"]
    
    J --> O["Gán text, màu sắc và\nvẽ cảnh báo lên màn hình Video"]
    K --> O
    M --> O
    N --> O
```

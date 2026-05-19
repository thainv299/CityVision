# CityVision AI Portal — Hệ Thống Giám Sát Giao Thông Thông Minh

Hệ thống giám sát giao thông tích hợp đầy đủ với phát hiện phương tiện, nhận diện biển số, đánh giá mức độ tắc nghẽn, và phát hiện vi phạm đỗ xe bằng YOLO26 + PaddleOCR + ByteTrack.

Hỗ trợ cả **Desktop GUI (Tkinter)** và **Web Portal (FastAPI)** với Clean Architecture.

---

## 🎯 Tính Năng Chính

### Phát Hiện & Tracking
- ✅ Phát hiện 7 class: person, bicycle, car, motorcycle, license_plate, bus, truck
- ✅ Tracking real-time bằng ByteTrack
- ✅ Hỗ trợ PyTorch (.pt) và TensorRT (.engine)

### Nhận Diện Biển Số
- ✅ OCR biển số vietsub qua PaddleOCR
- ✅ Xử lý perspective + CLAHE
- ✅ Voting + regex validation
- ✅ Ghi log CSV + ảnh bằng chứng tự động

### Giám Sát Tắc Nghẽn
- ✅ 4 mức độ tắc nghẽn (Thông thoáng / Đông đúc / Rất đông / TẮC NGHẼN)
- ✅ Tính occupancy (%) + vận tốc trung bình
- ✅ Debounce + Snooze cảnh báo thông minh

### Phát Hiện Vi Phạm Đỗ Xe
- ✅ State machine: MOVING → WAITING → VIOLATION → RECORDING_DONE
- ✅ Ghost tracks & spatial re-ID
- ✅ Video bằng chứng 15 giây (5s trước + 10s sau)
- ✅ Cảnh báo Telegram tức thời

### Web Portal (Clean Architecture)
- ✅ 8 trang: Login, Dashboard, Cameras, Users, Violations, Congestion, Vehicles, Test Video
- ✅ Quản lý camera, người dùng, xem lịch sử phương tiện
- ✅ Test video real-time streaming (MJPEG)
- ✅ Database SQLite tiếng Việt

---

## Demo
![Vẽ polygon nhận diện](assets/draw_layout.png)
![Kết quả nhận diện1](assets/demo1.png)
![Kết quả nhận diện2](assets/demo2.png)
![Kết quả nhận diện3](assets/demo3.png)
![Kết quả nhận diện4](assets/demo4.png)

---

## 📋 Yêu Cầu Hệ Thống

| Thành phần | Yêu cầu |
|---|---|
| **Python** | 3.10+ |
| **GPU** | NVIDIA (6GB VRAM khuyên) |
| **CUDA** | 12.4 (nếu dùng GPU) |
| **OS** | Windows / Linux |
| **Bộ nhớ** | 8GB RAM tối thiểu |

---

## 📦 Cài Đặt

### 1. Clone repo & tạo environment

```bash
cd e:\DATN_PROJECT
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux
```

### 2. Cài đặt dependencies

```bash
pip install -r requirements.txt
```

**requirements.txt** bao gồm:
```
opencv-python
numpy
ultralytics
pyTelegramBotAPI
python-dotenv
requests
paddleocr
av
FastAPI
uvicorn
starlette
aiofiles
python-multipart
werkzeug
jinja2
```

### 3. Cấu hình môi trường

Tạo file `.env` ở root directory:
```env
TELEGRAM_BOT_TOKEN=<your_bot_token>
TELEGRAM_CHAT_ID=<your_chat_id>
CROWD_PORTAL_SECRET=your-secret-key-here
```

---

## 📁 Cấu Trúc Thư Mục

```
e:\DATN_PROJECT\
├── app.py                          # FastAPI entry point (Web - Clean Architecture) ⭐
├── main_api.py                     # FastAPI entry point (Legacy - routers/)
├── main.py                         # Desktop app (Tkinter + detection)
├── run_system.py                   # Launcher (FastAPI + Tkinter cùng lúc)
├── requirements.txt
├── AI_CONTEXT.md                   # Tài liệu ngữ cảnh cho AI
│
├── core/                           # Config chung (dùng cho main_api.py)
│   ├── config.py
│   ├── security.py
│   ├── exceptions.py
│   └── utils.py
│
├── modules/                        # Logic xử lý (dùng cho main.py + web)
│   ├── ocr/
│   │   ├── ocr_processor.py        # OCR pipeline
│   │   └── ocr_manager.py          # Queue + voting
│   ├── parking/
│   │   ├── parking_logic.py        # State machine
│   │   └── parking_manager.py      # Xử lý vi phạm
│   ├── traffic/
│   │   └── traffic_monitor.py      # Đánh giá tắc nghẽn
│   └── utils/
│       ├── alpr_logger.py          # Ghi log CSV + ảnh
│       ├── telegram_bot.py
│       └── traffic_alert_manager.py
│
├── frontend/                       # Web application (Clean Architecture)
│   ├── app.py                      # Khởi động từ app.py root
│   ├── core/config.py              # Cấu hình frontend
│   ├── database/                   # SQLite layer
│   │   ├── __init__.py
│   │   ├── sqlite_db.py
│   │   ├── sqlite_user_repo.py
│   │   └── sqlite_camera_repo.py
│   ├── domain/                     # Entities & repositories
│   │   ├── entities/
│   │   └── repositories/
│   ├── application/                # Use cases
│   │   ├── use_cases/
│   │   └── interfaces/
│   ├── infrastructure/             # ML + Storage
│   │   ├── file_system/
│   │   │   └── local_storage.py
│   │   └── ml/
│   │       ├── detection_bridge.py # Headless detection
│   │       └── ocr_license_plate.py
│   ├── presentation/               # Web views & routers
│   │   ├── container.py            # DI container
│   │   ├── middlewares/
│   │   └── web/
│   │       ├── auth_views.py
│   │       ├── dashboard_views.py
│   │       ├── camera_views.py
│   │       ├── user_views.py
│   │       ├── vehicle_views.py
│   │       ├── violation_views.py
│   │       ├── congestion_views.py
│   │       └── test_video_views.py
│   ├── templates/                  # Jinja2 templates
│   │   ├── base.html, login.html, dashboard.html
│   │   ├── cameras.html, users.html, test_video.html
│   │   ├── license_plates.html, violations.html, congestion.html
│   │   └── error.html
│   ├── static/                     # CSS, JS
│   ├── portal.db                   # SQLite database
│   └── runtime/
│       ├── inputs/
│       ├── outputs/
│       └── previews/
│
├── routers/                        # Legacy routes (dùng cho main_api.py)
│   ├── web_views.py
│   ├── api_users.py
│   ├── api_cameras.py
│   ├── api_jobs.py
│   └── api_license_plates.py
│
├── services/
│   └── job_manager.py              # Background job runner
│
├── models/                         # YOLO models
│   ├── best.pt                     # Production model
│   ├── best.engine                 # TensorRT variant
│   └── *.pt                        # Other variants
│
├── layouts/                        # ROI polygons (JSON format)
│
├── logs/                           # Runtime logs
│   ├── ALPR_log.csv                # License plate log
│   ├── plates/                     # License plate evidence images
│   └── violations/                 # Parking violation evidence
│
└── .env                            # Environment variables (NOT in git)
```

---

## 🚀 Hướng Dẫn Sử Dụng

### Option A: Web Portal (Khuyên) ⭐

```bash
python app.py
# Hoặc:
python -m uvicorn app:app --host 0.0.0.0 --port 5000
```

Truy cập: `http://localhost:5000`

**Tài khoản mặc định:**
- Username: `admin`
- Password: `Admin@123`

**Các features:**
- 📊 Dashboard: Tổng quan thống kê
- 📹 Cameras: Quản lý camera
- 👤 Users: Quản lý người dùng
- 🚗 Vehicles: Lịch sử phương tiện
- ⚠️ Violations: Vi phạm đỗ xe
- 🚦 Congestion: Thống kê tắc nghẽn
- 🎬 Test Video: Upload & test video

### Option B: Desktop GUI

```bash
python main.py
```

**Các bước:**
1. Bấm "Chọn Model YOLO" → Chọn `.pt` hoặc `.engine`
2. Bấm "Chọn Video" → Chọn file video
3. Bấm "Vẽ Vùng Quan Sát" (nếu cần) → Bấm Enter để xác nhận
4. Bấm "Bắt đầu Detect" để bắt đầu
5. Nhấn ESC để dừng

**Phím tắt khi vẽ ROI:**
- Click trái: Thêm điểm
- Click phải: Xóa toàn bộ
- Ctrl+Z: Xóa điểm cuối
- Enter: Xác nhận (ít nhất 3 điểm)
- ESC: Hủy

### Option C: Chạy Cả 2 Cùng Lúc

```bash
python run_system.py
```

Web chạy ở port 5000, Desktop chạy song song.

---

## 📊 Database Schema

Database tự động khởi tạo tại `frontend/portal.db` với các bảng:

| Bảng | Mô tả |
|---|---|
| `nguoi_dung` | User accounts (admin/operator) |
| `camera` | Camera configuration |
| `bien_so_phat_hien` | License plate detections |
| `lich_su_phuong_tien` | Vehicle history |
| `vi_pham_do_xe` | Parking violations |
| `nhat_ky_un_tac` | Congestion events |
| `thong_ke_giao_thong` | Traffic statistics |

Tất cả bảng và cột sử dụng tiếng Việt.

---

## ⚙️ Export Model YOLO sang TensorRT

TensorRT tối ưu hóa model để chạy nhanh hơn ~2x trên GPU (FP16).

```python
from ultralytics import YOLO

model = YOLO("models/best.pt")
model.export(
    format="engine",
    half=True,           # FP16
    device=0,            # GPU index
    workspace=4,         # GB VRAM
    imgsz=640,           # Input size
    keras=False
)
# Output: models/best.engine
```

**Lưu ý:**
- File `.engine` được tối ưu riêng cho GPU của máy export
- Không dùng được trên máy khác — phải export lại nếu đổi GPU
- Cần CUDA 12.4 + TensorRT 10.x

**Kiểm tra TensorRT:**
```python
import tensorrt as trt
print(f"TensorRT version: {trt.__version__}")
```

---

## 📈 So Sánh Hiệu Năng

| Cấu hình | FPS | Độ trễ |
|---|---|---|
| YOLO26L .pt (FP32) | 12-15 | 65-83ms |
| YOLO26L .engine (FP16) | 17-20 | 50-58ms |
| .engine + skip frame | 25-35 | N/A (realtime) |

---

## 🎓 Train Model Tùy Chỉnh

### Chuẩn bị Dataset

Sử dụng YOLOv8 format:
```
dataset/
├── images/
│   ├── train/  # Training images
│   └── val/    # Validation images
└── labels/
    ├── train/  # .txt labels (YOLO format)
    └── val/
```

### File dataset.yaml

```yaml
path: /path/to/dataset
train: images/train
val: images/val

nc: 7
names: ['person', 'bicycle', 'car', 'motorcycle', 'license_plate', 'bus', 'truck']
```

### Code Training

```python
from ultralytics import YOLO

model = YOLO("yolov26m.pt")  # Load pretrained

results = model.train(
    data="dataset.yaml",
    epochs=100,
    imgsz=640,
    batch=16,
    device=0,
    patience=20,
    project="TrafficAI",
    name="Run1",
    
    # Learning rate
    lr0=0.001,
    lrf=0.01,
    warmup_epochs=3,
    
    # Augmentation
    mosaic=1.0,
    mixup=0.1,
    degrees=10.0,
    hsv_s=0.5,
)

# Export to .pt
model.export(format="pt")
# Export to .engine (TensorRT)
model.export(format="engine", half=True)
```

---

## 🔌 API Endpoints (Web)

### Authentication
- `POST /login` — Đăng nhập
- `GET /logout` — Đăng xuất

### Dashboard
- `GET /dashboard` — Tổng quan

### Cameras
- `GET /cameras` — Danh sách camera
- `POST /api/cameras` — Thêm camera
- `PUT /api/cameras/{id}` — Cập nhật camera
- `DELETE /api/cameras/{id}` — Xóa camera

### Users
- `GET /users` — Danh sách user
- `POST /api/users` — Thêm user
- `PUT /api/users/{id}` — Cập nhật user
- `DELETE /api/users/{id}` — Xóa user

### License Plates
- `GET /api/license-plates` — Danh sách biển số
- `GET /api/license-plates/date/{date}` — Biển số theo ngày
- `GET /vehicles` — Lịch sử phương tiện

### Violations
- `GET /violations` — Vi phạm đỗ xe
- `GET /api/violations` — API violations

### Congestion
- `GET /congestion` — Thống kê tắc nghẽn

### Test Video
- `GET /test-video` — Upload & test video
- `POST /api/test-jobs` — Submit job
- `GET /api/test-jobs/{id}/stream` — MJPEG streaming

---

## ⚙️ Cấu Hình Quan Trọng

### Ngưỡng Phát Hiện Tắc Nghẽn (traffic_monitor.py)
```python
CONG_COUNT_THR = 10              # Số xe tối thiểu → Level 1
CONG_PEOPLE_THR = 30             # Số người tối thiểu → Level 1
CONG_AREA_PERCENT_THR = 40.0     # % diện tích tối thiểu → Level 2
CONG_SPEED_THR = 10.0            # Vận tốc tối đa (px/s) → Level 3
```

### Ngưỡng Vi Phạm Đỗ Xe (parking_logic.py)
```python
stop_seconds = 30                # Thời gian đỗ tối đa (giây)
move_thr_px = 10.0               # Ngưỡng pixel/frame để coi là đứng yên
cooldown_seconds = 30.0          # Thời gian chờ giữa 2 cảnh báo
```

### OCR Config (ocr_manager.py)
```python
OCR_INTERVAL = 4                 # Xử lý OCR mỗi 4 frame
VOTE_THRESHOLD = 3               # Cần ≥3 kết quả trùng để confirm
MAX_LOST_FRAMES = 5              # Giữ biển số khi tracker mất dấu
```

---

## 📝 Ghi Chú Quan Trọng

### Security
- 🔒 Mật khẩu: Hashed bằng werkzeug.security
- 🔑 Session: SessionMiddleware (7 ngày)
- 🛡️ CORS: Enabled (có thể tighten nếu cần)

### Performance
- 📊 Job processing: ThreadPoolExecutor (1 worker - 1 job/lúc)
- 🎬 Video streaming: MJPEG real-time (không lưu file)
- 💾 Stream-only: Không lưu input/output video vào disk

### Logging
- 📋 ALPR log: `logs/ALPR_log.csv`
- 📸 Evidence images: `logs/plates/{YYYY}/{MM}/{DD}/`
- 📹 Violations: `logs/violations/{plate}/evidence/`

### Model & Detection
- 🎯 Confidence threshold: 0.32
- ⏱️ Stop seconds (parking): 30
- 🚗 Detect classes: person, bicycle, car, motorcycle, bus, truck
- 🚗 Parking xét: car, bus, truck ONLY
- 🔤 OCR xét: car, bus, truck ONLY

### Telegram Alerts
- ⚠️ Parking warning: Khi WAITING bắt đầu
- 🚨 Parking violation: Khi xác nhận vi phạm
- 🚦 Congestion: Debounce 5s, snooze thông minh

---
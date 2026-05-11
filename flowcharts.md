# AI Agent Context — Hệ Thống Giám Sát Giao Thông Thông Minh (ITS)

> **Mục đích file**: Cung cấp ngữ cảnh nhanh cho AI coding agent, giúp hiểu toàn bộ kiến trúc, thuật toán, và dữ liệu trong dự án mà KHÔNG cần đọc từng file source code. Cập nhật lần cuối: 2026-04-19.

---

## 1. TỔNG QUAN DỰ ÁN

- **Tên**: UrbanM_AI — Hệ thống Giám sát Giao thông Đô thị Thông minh
- **Ngôn ngữ**: Python 3.x
- **Nền tảng**: FastAPI (web backend) + Tkinter (desktop GUI) + OpenCV (xử lý video)
- **CSDL**: SQLite (file `frontend/portal.db`)
- **Mô hình AI**: YOLOv26m (Ultralytics) + PaddleOCR
- **Tracker**: ByteTrack (`bytetrack.yaml`)
- **Cảnh báo**: Telegram Bot API (pyTeleBot + requests)
- **Entry points**:
  - `run_system.py` — Chạy đồng thời FastAPI (port 5000) + Tkinter GUI trên 2 thread
  - `main_api.py` — Chỉ chạy FastAPI server (legacy approach)
  - `app.py` — FastAPI entry point (Clean Architecture - khuyên dùng)
  - `main.py` — Chỉ chạy desktop Tkinter app

---

## 2. CẤU TRÚC THƯ MỤC

```
e:\DATN_PROJECT\
├── app.py                     # FastAPI entry point chính (Clean Architecture - khuyên dùng)
├── main.py                    # Desktop app (Tkinter + OpenCV + YOLO + OCR)
├── main_api.py                # FastAPI entry point (legacy, dùng routers/)
├── run_system.py              # Launcher: chạy cả FastAPI + Tkinter trên 2 thread
├── cloudflared.exe            # Công cụ tunnel cho phát triển
├── .env                       # TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
├── requirements.txt           # Dependencies
├── README.md                  # Tài liệu dự án
├── flowcharts.md              # Sơ đồ luồng dữ liệu
├── AI_CONTEXT.md              # File context này
│
├── core/                      # Cấu hình & utilities cho legacy code (main_api.py)
│   ├── config.py              # Paths, ThreadPoolExecutor, job_lock, templates
│   ├── security.py            # JWT authentication utilities
│   ├── exceptions.py          # HTTP exception handlers
│   └── utils.py               # Utility functions
│
├── modules/                   # Các module xử lý cũ (legacy, dùng cho main.py)
│   ├── ocr/
│   │   ├── ocr_processor.py   # Pipeline OCR: perspective → CLAHE → PaddleOCR → regex
│   │   └── ocr_manager.py     # Quản lý OCR queue, voting, spatial memory
│   ├── parking/
│   │   ├── parking_logic.py   # State machine: MOVING→WAITING→VIOLATION→RECORDING_DONE
│   │   └── parking_manager.py # Quản lý vùng cấm đỗ, bằng chứng, Telegram alert
│   ├── traffic/
│   │   └── traffic_monitor.py # Đếm xe/người, tính occupancy & speed, 4 cấp tắc nghẽn
│   └── utils/
│       ├── alpr_logger.py           # Ghi log biển số → CSV + ảnh bằng chứng
│       ├── async_io_worker.py       # [NEW] Hàng đợi I/O nền (Telegram, ghi file, ghi DB)
│       ├── telegram_bot.py          # Gửi ảnh/video qua Telegram (requests)
│       ├── interactive_telegram_bot.py  # Bot tương tác có nút ACK (pyTeleBot)
│       ├── traffic_alert_manager.py # Debounce + snooze + escalation logic
│       └── common_utils.py          # ensure_dir, now_ts, load/save JSON
│
├── ml/                        # Các module ML (cấu trúc giống modules/)
│   ├── ocr/                   # OCR thử nghiệm
│   ├── parking/               # Parking thử nghiệm
│   ├── traffic/               # Traffic thử nghiệm
│   └── utils/                 # ML utilities
│
├── backend/                   # Web application - Clean Architecture (Business Logic & API)
│   ├── core/
│   │   ├── config.py          # Settings, DATABASE_PATH, SECRET_KEY
│   │   ├── utils.py           # Web utilities
│   │   └── errors.py          # Error definitions
│   ├── database/
│   │   ├── sqlite_db.py       # DB init + CRUD functions
│   │   ├── sqlite_user_repo.py
│   │   └── sqlite_camera_repo.py
│   ├── domain/                # Clean Architecture: Business logic
│   │   ├── entities/
│   │   │   ├── user.py
│   │   │   ├── camera.py
│   │   │   └── job.py
│   │   └── repositories/
│   │       ├── user_repository.py
│   │       └── camera_repository.py
│   ├── application/           # Use cases (business logic layer)
│   │   ├── use_cases/
│   │   │   ├── user_use_cases.py
│   │   │   ├── camera_use_cases.py
│   │   │   ├── dashboard_use_cases.py
│   │   │   └── job_use_cases.py
│   │   └── interfaces/
│   │       └── detection_interface.py
│   ├── infrastructure/        # External services layer
│   │   ├── file_system/
│   │   │   └── local_storage.py
│   │   └── ml/
│   │       ├── detection_bridge.py  # Pipeline phát hiện không giao diện
│   │       └── ocr_license_plate.py # Dịch vụ OCR biển số
│   ├── presentation/          # HTTP layer (Clean Architecture)
│   │   ├── container.py       # Dependency injection container
│   │   ├── middlewares/
│   │   │   └── auth.py        # Xác thực custom middleware
│   │   └── web/
│   │       ├── auth_views.py
│   │       ├── dashboard_views.py
│   │       ├── camera_views.py
│   │       ├── user_views.py
│   │       ├── test_video_views.py
│   │       ├── vehicle_views.py      # Lịch sử biển số phát hiện
│   │       ├── violation_views.py    # Vi phạm đỗ xe
│   │       └── congestion_views.py   # Tắc nghẽn giao thông
│   ├── portal.db              # SQLite database chính
│   ├── portal.db.backup       # Backup database (tạo 2026-04-15)
│   ├── services/              # Service classes
│   │   ├── __init__.py
│   │   └── detection_bridge.py  # (old location, also at infrastructure/ml/)
│   └── runtime/               # Video tạm thời & outputs
│       ├── inputs/            # Video input tạm
│       ├── outputs/           # Video output tạm
│       └── previews/          # Preview frames tạm
│
├── frontend/                  # Web User Interface (Frontend tĩnh)
│   ├── templates/             # Jinja2 HTML templates
│   │   ├── base.html, login.html, dashboard.html
│   │   ├── cameras.html, users.html, test_video.html
│   │   ├── license_plates.html, violations.html, congestion.html, settings.html
│   │   └── error.html
│   └── static/                # CSS, JS, assets cho web
│
├── routers/                   # FastAPI route handlers (legacy, cho main_api.py)
│   ├── web_views.py           # HTML page routes (cấu trúc cũ)
│   ├── api_users.py           # REST API: CRUD users
│   ├── api_cameras.py         # REST API: CRUD cameras
│   ├── api_jobs.py            # REST API: video detection jobs + MJPEG streaming
│   └── api_license_plates.py  # REST API: biển số phát hiện
│
├── services/                  # Background services
│   ├── camera_service.py      # Dịch vụ quản lý camera
│   └── job_manager.py         # Job runner (process_video → DB update)
│
├── desktop/                   # Desktop app modules (thử nghiệm)
│   └── services/
│   └── ui/
│
├── scripts/                   # Các script tiện ích & debug
│   ├── add_default_camera.py
│   ├── analyze_violations.py
│   ├── check_camera_table.py
│   ├── db_report.py
│   ├── run_migration.py       # Migration DB schema
│   ├── reset_admin.py         # Reset admin account
│   ├── test_server.py
│   └── README_PARKING_FIX.md
│
├── assets/                    # Data assets (JSON layouts, etc.)
│   └── school_gate_01.json
│
├── layouts/                   # ROI & no-parking polygons (saved JSON)
│   ├── 20260405_*.json        # Layout files
│   ├── parking_video*.json
│   ├── TruongNH_*.json
│   └── ...
│
├── configs/                   # Thư mục config (có thể để config files)
│
├── logs/                      # Runtime logs & evidence
│   ├── ALPR_log.csv           # Biển số phát hiện log
│   ├── plates/                # Ảnh bằng chứng biển số
│   │   └── {YYYY}/{MM}/{DD}/*.jpg
│   └── violations/            # Vi phạm đỗ xe evidence
│       └── {plate_or_ID}/EVT_{timestamp}_{track_id}/*.jpg, .mp4, .json
│
├── models/                    # YOLO & PP-OCR model files
│   ├── best.pt                # Model chính (7 class)
│   ├── best.onnx
│   ├── best.engine            # TensorRT format
│   ├── yolo26l.pt
│   ├── yolo26l.onnx
│   ├── yolo26l.engine
│   ├── mediumfinetune_v1.pt
│   ├── medium_finetune_v2.pt
│   └── plate_detect_model.pt  # Legacy plate detection
│
├── docs/                      # Documentation
│   └── walkthrough.md
│
├── tests/                     # Unit tests & test scripts
│
├── web/                       # Web assets (có thể trùng với frontend/static)
│
├── runtime/                   # Legacy runtime folder (có thể không dùng)
│
└── configs/                   # Thư mục config
```

**Ghi chú quan trọng**:
- **app.py ở ROOT** (không phải frontend/app.py) - Đây là entry point chính, khuyên dùng
- **frontend/portal.db**: Vị trí DATABASE (không phải ở frontend/runtime/)
- **ml/**: Thư mục modules ML (cấu trúc giống modules/, có thể là thử nghiệm hoặc copy)
- **modules/**: Modules cũ (legacy) - được dùng bởi main.py
- **desktop/**: Desktop app thử nghiệm
- **services/**: Chỉ có 2 file chính (camera_service.py, job_manager.py)

---

## 3. YOLO MODEL — 7 LỚP ĐỐI TƯỢNG

```python
# main.py:21-24
class_names = {
    0: "Person",    1: "Bicycle",   2: "Car",
    3: "Motorcycle", 4: "License Plate", 5: "Bus", 6: "Truck"
}
```

- **Dataset huấn luyện**: `E:/DATN_code/Dataset/COCO_Balanced/dataset.yaml`
- **Model file**: `models/yolo26m.pt` (có thể export sang `.engine` cho TensorRT)
- **Confidence threshold**: `0.32`
- **Tracker**: `model.track(frame, persist=True, tracker="bytetrack.yaml")`
- **Frame skipping**: Xử lý YOLO mỗi 2 frame (`frame_count % 2`), frame còn lại dùng kết quả cũ
- **Target classes** (dùng khi detect): `["person", "bicycle", "car", "motorcycle", "license_plate", "bus", "truck"]` — tất cả 7 nhãn

---

## 4. MODULE OCR — Pipeline Nhận Diện Biển Số

### 4.1 Kiến trúc
```
ocr_manager.py (Controller)          ocr_processor.py (Processing)
─────────────────────────            ─────────────────────────────
Queue(maxsize=3)                     get_plate_perspective()  ← Nắn góc
Worker thread (daemon)               preprocess_plate()       ← CLAHE
Voting (Counter + threshold=3)       run_ocr()               ← PaddleOCR
Spatial Memory (100px, 300 frames)   correct_plate_format()   ← Sửa lỗi VN
Grace Period (5 lost frames)         is_valid_vn_plate()      ← Regex filter
```

### 4.2 PaddleOCR Config
```python
PaddleOCR(use_angle_cls=False, det=True, lang='en', show_log=False)
```

### 4.3 Tham số quan trọng
| Tham số | Giá trị | Mô tả |
|---------|---------|-------|
| `OCR_INTERVAL` | 4 | Chỉ gửi ảnh OCR mỗi 4 frame |
| `VOTE_THRESHOLD` | 3 | Cần ≥3 kết quả trùng nhau để confirm |
| `MAX_LOST_FRAMES` | 5 | Giữ hiển thị biển số khi tracker mất dấu |
| `pad` | 2px | Padding khi crop biển số từ frame |
| Biển vuông (w/h < 1.8) | 240×180 | Xe máy 2 dòng |
| Biển dài (w/h ≥ 1.8) | 480×120 | Ô tô 1 dòng |

### 4.4 Regex biển số VN
```python
pattern = r"^[1-9][0-9][A-Z][0-9]{4,5}$"
```

### 4.5 Correct Plate Format — Bảng sửa lỗi OCR
```python
char_to_num = {'O':'0', 'Q':'0', 'I':'1', 'Z':'2', 'S':'5', 'G':'6', 'B':'8', 'A':'4'}
num_to_char = {'0':'D', '8':'B', '4':'A', '5':'S', '2':'Z'}
# Rule: 2 ký tự đầu = số (mã tỉnh), ký tự 3 = chữ (sê-ri), còn lại = số
```

### 4.6 Lọc biển số hợp lệ
Chỉ xử lý OCR cho biển số mà **tâm (cx, cy) nằm bên trong bounding box** của xe lớn (car/bus/truck). Loại bỏ biển số có kích thước quá nhỏ (w ≤ 20 hoặc h ≤ 10).

### 4.7 Output
- File log: `logs/ALPR_log.csv` (columns: Time, Frame, Plate, Full_Frame_Image_Path)
- Ảnh bằng chứng: `logs/plates/{YYYY}/{MM}/{DD}/{biển_số}_{HH-MM-SS}.jpg`
- Session tracking: `disappear_threshold=1800` frames — nếu biển số đã ghi log mà vẫn xuất hiện liên tục thì không ghi lại.

---

## 5. MODULE TRAFFIC MONITOR — Giám Sát Mật Độ

### 5.1 Cơ chế hoạt động
- Đếm `vehicle_count` và `people_count` **hiện diện** trong ROI mỗi frame (KHÔNG đếm ra/vào)
- Tính **Occupancy%** bằng kỹ thuật Mask Overlap (ROI mask ∩ vehicles + people mask → pixel ratio)
- **QUAN TRỌNG**: Tính diện tích của **TẤT CẢ các nhãn** (xe + người), KHÔNG chỉ xe!
  - Vẽ bounding box của xe (car, truck, bus) vào vehicles_mask
  - Vẽ bounding box của người (person) vào vehicles_mask
  - Tính giao của vehicles_mask ∩ roi_mask
- Tính **Average Speed** (px/s) dựa trên track_history của xe (lưu 2s gần nhất)
- Lọc nhiễu: bỏ box có diện tích > 30% ROI

### 5.2 Ngưỡng 4 cấp tắc nghẽn
```python
CONG_COUNT_THR = 10           # Số xe tối thiểu → Level 1
CONG_PEOPLE_THR = 30          # Số người tối thiểu → Level 1
CONG_AREA_PERCENT_THR = 40.0  # % diện tích tối thiểu → Level 2
CONG_SPEED_THR = 10.0         # Vận tốc tối đa (px/s) → Level 3
MAX_VEHICLE_AREA_RATIO = 0.3  # Bỏ box nhiễu > 30% ROI
```

| Level | Điều kiện | Trạng thái |
|-------|-----------|------------|
| 0 | Dưới ngưỡng count VÀ occupancy < 40% | Thông thoáng |
| 1 | Count ≥ 10 xe HOẶC ≥ 30 người, occupancy < 40% | Đông đúc |
| 2 | Occupancy ≥ 40% VÀ speed > 10 px/s | Rất đông |
| 3 | Occupancy ≥ 40% VÀ speed ≤ 10 px/s | TẮC NGHẼN |

### 5.3 Debounce Logic - FIX NHẢY LOẠN XẠ CẤP (2026-04-16)

**Vấn đề**:
- Khi occupancy% thay đổi liên tục (dùng mask), raw_level nhảy liên tục (2→1→3→2, v.v.)
- Dẫn đến ghi quá nhiều record vào database

**Giải pháp** (áp dụng Debounce + True Clear từ TrafficAlertManager):
```
Raw Level (từ traffic_monitor) ──debounce 1s──→ Confirmed Level
                                                      ↓
                                    (ghi DB chỉ khi confirmed_level thay đổi)
                                    
Confirmed Level = 0
    ↓ (liên tục 5s)
True Clear
    ↓
Reset toàn bộ trạng thái DB
```

**Implementation**:
- `traffic_alert_manager.confirmed_level` → chỉ cập nhật khi level ổn định ≥1 giây
- `clear_start_time` → theo dõi thời gian level = 0 liên tục
- Ghi DB chỉ khi `confirmed_level` thay đổi (không phải raw level)
- Khi level = 0 liên tục ≥5 giây → update end_time cho record DB

**Code trong detection_bridge.py**:
```python
confirmed_lvl = traffic_alert_manager.confirmed_level  # Đã debounce

if confirmed_lvl == 0:
    if clear_start_time == 0:
        clear_start_time = current_time
    elif current_time - clear_start_time >= 5.0:  # true_clear_seconds
        # Hết tắc dứt điểm → update end_time
        update_congestion_end_time(last_congestion_record_id)
else:
    clear_start_time = 0

# Ghi DB chỉ khi confirmed_level thay đổi
if confirmed_lvl != last_db_traffic_level:
    if confirmed_lvl > 0:
        last_congestion_record_id = log_congestion(camera_id, confirmed_lvl)
    elif last_congestion_record_id:
        update_congestion_end_time(last_congestion_record_id)
    last_db_traffic_level = confirmed_lvl
```

---

## 6. MODULE PARKING — Phát Hiện Đỗ Xe Trái Phép

### 6.1 State Machine (parking_logic.py)
```
MOVING (0) ──speed < thr──→ WAITING (1) ──frames ≥ stop_frames──→ VIOLATION (2) ──record done──→ RECORDING_DONE (3)
     ↑                           │
     └──grace > 10 frames────────┘
```

### 6.2 Tham số mặc định
| Tham số | Giá trị | Mô tả |
|---------|---------|-------|
| `stop_seconds` | 30 | Thời gian đỗ tối đa (giây) trước khi vi phạm |
| `move_thr_px` | 10.0 | Ngưỡng pixel/frame để coi là đứng yên |
| `cooldown_seconds` | 30.0 | Thời gian chờ giữa 2 lần cảnh báo cùng xe |
| Grace count | 10 frames | Ân hạn trước khi chuyển từ WAITING → MOVING |
| History buffer | 10 positions | Số vị trí gần nhất để tính tốc độ trung bình |

### 6.3 Đối tượng áp dụng
- CHỈ xét: `car`, `bus`, `truck`
- KHÔNG xét: `motorcycle`, `bicycle`, `person`

### 6.4 Ghost Tracks & Spatial Re-ID
- Xe mất dấu > 1s → đẩy vào `ghost_tracks` (giữ logic state + waiting data)
- ID mới xuất hiện gần ghost (< 60px hoặc 2× move_thr) → nối ghép, khôi phục trạng thái
- Ghost hết hạn sau 10s

### 6.5 Bằng chứng vi phạm (output)
```
logs/violations/{plate_or_ID}/EVT_{timestamp}_{track_id}/
├── img_T0.jpg           # Ảnh lúc bắt đầu đỗ
├── img_T1.jpg           # Ảnh lúc xác nhận vi phạm
├── combined_alert.jpg   # Ghép T0+T1 kèm biển số
├── video_record.mp4     # Video 15s (5s buffer + 10s sau vi phạm)
└── evidence.json        # Metadata (track_id, plate, timestamps)
```

### 6.6 Deduplication - CRITICAL BUG FIX (2026-04-15)
**Problem**: Trước đó, khi track ở state VIOLATION, hệ thống ghi log VIOLATION cho MỖI FRAME (300+ lần/video)!

**Solution** (Implemented in `detection_bridge.py` line 285):
- Thêm set `logged_violation_track_ids` để track những `track_id` đã ghi lại violation
- Chỉ ghi lại violation một lần khi `track_id` lần đầu vào state VIOLATION
- Nếu track_id đã ghi log → skip frame hiện tại

```python
if track_id in logged_violation_track_ids:
    continue  # Skip: đã ghi log violation cho track_id này rồi

logged_violation_track_ids.add(track_id)  # Đánh dấu track_id
# Ghi violation vào DB
```

**Result**: ✅ Violation records đã về bình thường (1 violation = 1 record)

**Kết quả dọn dẹp DB**: 300+ duplicate records từ April 10 đã xóa thủ công, DB hiện sạch sẽ (chỉ có ~300 valid records)

### 6.7 Cảnh báo Telegram
1. Khi WAITING bắt đầu: gửi ảnh T0 + caption "⚠️ CẢNH BÁO"
2. Khi VIOLATION xác nhận: gửi ảnh ghép + video bằng chứng "🚨 VI PHẠM CHỐT"

---

## 7. TRAFFIC ALERT MANAGER — Hệ Thống Cảnh Báo Tắc Nghẽn

### 7.1 Kiến trúc Debounce + Snooze
```
Raw Level → Debounce (1s) → Confirmed Level → Alert Logic → Telegram
                                                  ↓
                                        Escalation / Timer expired
                                                  ↓
                                        Trigger + Snooze timer
```

### 7.2 Thời gian snooze
| Level | Chưa ACK (giây) | Đã ACK (giây) |
|-------|-----------------|----------------|
| 1 | 300 (5 phút) | 900 (15 phút) |
| 2 | 60 (1 phút) | 600 (10 phút) |
| 3 | 30 (30 giây) | 300 (5 phút) |

### 7.3 Reset hoàn toàn
Đường vắng **liên tục 5 giây** (`TRUE_CLEAR_SECONDS`) → reset `last_alert_level` và `snooze_until`

### 7.4 ACK channels
- **Bàn phím**: Nhấn 'A' trên cửa sổ OpenCV
- **Telegram**: Nhấn nút "Xác nhận ✅" trên tin nhắn bot

---

## 8. DATABASE SCHEMA (SQLite)

### 8.1 Vị trí và initialization
- **Đường dẫn**: `frontend/portal.db` (cũ: `frontend/runtime/portal.db`)
- **Init**: `frontend/database/sqlite_db.py` → `init_db()` tạo bảng + default admin user
- **CRUD functions**: Tất cả nằm trong file này

### 8.2 Bảng dữ liệu (sử dụng tiếng Việt)

#### Bảng 1: Quản lý người dùng
```sql
CREATE TABLE IF NOT EXISTS nguoi_dung (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ten_dang_nhap TEXT NOT NULL UNIQUE,           -- username
    ho_ten TEXT NOT NULL,                         -- full_name
    mat_khau_hash TEXT NOT NULL,                  -- password_hash (werkzeug.security)
    vai_tro TEXT NOT NULL DEFAULT 'operator',     -- role: 'admin' or 'operator'
    trang_thai_hoat_dong INTEGER NOT NULL DEFAULT 1,  -- is_active (0/1)
    ngay_tao TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ngay_cap_nhat TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- Default admin: username="admin", password="Admin@123"
```

#### Bảng 2: Quản lý camera
```sql
CREATE TABLE IF NOT EXISTS camera (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ten_camera TEXT NOT NULL UNIQUE,              -- camera_name
    nguon_phat TEXT,                              -- stream_source (rtsp://... or file path)
    mo_ta TEXT,                                   -- description
    toa_do_vung_chon TEXT,                        -- roi_points (JSON)
    toa_do_cam_do TEXT,                           -- no_parking_points (JSON)
    bat_phat_hien_un_tac INTEGER NOT NULL DEFAULT 1,        -- enable_congestion
    bat_phat_hien_do_sai INTEGER NOT NULL DEFAULT 1,        -- enable_illegal_parking
    bat_phat_hien_bien_so INTEGER NOT NULL DEFAULT 1,       -- enable_license_plate (NEW)
    trang_thai_hoat_dong INTEGER NOT NULL DEFAULT 1,        -- is_active
    ngay_tao TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ngay_cap_nhat TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

#### Bảng 3: Thống kê giao thông
```sql
CREATE TABLE IF NOT EXISTS thong_ke_giao_thong (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_camera INTEGER NOT NULL,                   -- camera_id
    so_luong_xe INTEGER NOT NULL DEFAULT 0,       -- vehicle_count
    ngay_ghi_nhan TEXT NOT NULL,                  -- recorded_date (YYYY-MM-DD)
    ngay_tao TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (id_camera) REFERENCES camera(id),
    UNIQUE(id_camera, ngay_ghi_nhan)
);
```

#### Bảng 4: Vi phạm đỗ xe
```sql
CREATE TABLE IF NOT EXISTS vi_pham_do_xe (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_camera INTEGER NOT NULL,                   -- camera_id
    bien_so TEXT,                                 -- license_plate
    thoi_gian_vi_pham TEXT NOT NULL,              -- violation_time (ISO format)
    thoi_gian_do_giay INTEGER NOT NULL DEFAULT 0, -- duration_seconds
    duong_dan_anh TEXT,                           -- frame_path (evidence image)
    da_giai_quyet INTEGER NOT NULL DEFAULT 0,     -- is_resolved (0/1)
    ngay_tao TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (id_camera) REFERENCES camera(id)
);
```

#### Bảng 5: Nhật ký tắc nghẽn
```sql
CREATE TABLE IF NOT EXISTS nhat_ky_un_tac (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_camera INTEGER NOT NULL,                   -- camera_id
    muc_do_un_tac INTEGER NOT NULL DEFAULT 1,     -- congestion_level (1-3)
    thoi_gian_bat_dau TEXT NOT NULL,              -- start_time (ISO format)
    thoi_gian_ket_thuc TEXT,                      -- end_time (ISO format, nullable)
    thoi_gian_keo_dai_giay INTEGER DEFAULT 0,     -- duration_seconds
    ngay_tao TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (id_camera) REFERENCES camera(id)
);
```

#### Bảng 6: Biển số phát hiện (NEW)
```sql
CREATE TABLE IF NOT EXISTS bien_so_phat_hien (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bien_so TEXT NOT NULL,                        -- license_plate
    ngay_phat_hien TEXT NOT NULL,                 -- detection_date (YYYY-MM-DD)
    thoi_gian_phat_hien TEXT,                     -- detection_time (HH:MM:SS)
    so_lan_phat_hien INTEGER NOT NULL DEFAULT 1,  -- detection_count
    do_chinh_xac_tb REAL DEFAULT 0.0,             -- avg_confidence
    duong_dan_anh TEXT,                           -- image_paths (web path to image)
    id_camera INTEGER DEFAULT 0,                  -- camera_id (0=test video, >0=real camera)
    ngay_tao TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ngay_cap_nhat TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

#### Bảng 7: Lịch sử phương tiện (NEW)
```sql
CREATE TABLE IF NOT EXISTS lich_su_phuong_tien (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_camera INTEGER DEFAULT 0,                  -- camera_id (0=test video, >0=real camera)
    bien_so_xe TEXT,                              -- license_plate
    loai_xe TEXT,                                 -- vehicle_type (car, motorcycle, etc.)
    thoi_gian_di_qua TEXT NOT NULL,               -- passed_time (ISO format)
    ngay_tao TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- Lưu lại lịch sử các xe đã đi qua camera (cải thiện hơn vehicle_logs cũ)
```

#### Bảng 8: Cấu hình hệ thống
```sql
CREATE TABLE IF NOT EXISTS cau_hinh_he_thong (
    khoa TEXT PRIMARY KEY,                        -- setting key
    gia_tri TEXT                                  -- setting value
);
-- Mặc định: confidence, frame_skip, iou_threshold, congestion_threshold, parking_violation_time, log_retention, evidence_format
```

### 8.3 CRUD Functions (sqlite_db.py)

**Thống kê & Query**:
- `get_total_vehicle_count()` → Tổng số xe
- `get_illegal_parking_violations()` → Danh sách vi phạm chưa giải quyết (kèm camera name)
- `resolve_parking_violation(violation_id)` → Đánh dấu vi phạm đã giải quyết
- `get_congestion_count()` → Số sự kiện tắc nghẽn trong hôm nay
- `get_congestion_history()` → Lịch sử ùn tắc (kèm camera name, end_time, duration)
- `get_dashboard_stats_data()` → Dict gồm tất cả stats cho dashboard
- `get_system_settings()` → Lấy tất cả cấu hình hệ thống từ bảng cau_hinh_he_thong
- `update_system_settings(settings_dict)` → Cập nhật cấu hình hệ thống

**Ghi log**:
- `log_vehicle_count(camera_id, count, recorded_date)` → Ghi số xe
- `log_parking_violation(camera_id, license_plate, violation_time, duration, frame_path)` → Ghi vi phạm đỗ xe
- `log_congestion(camera_id, level, start_time)` → Ghi tắc nghẽn, trả về record ID (để update end_time)
- `update_congestion_end_time(congestion_id, end_time)` → Cập nhật thời gian kết thúc & tính toán duration_seconds
- `log_passed_vehicle(camera_id, bien_so_xe, loai_xe, thoi_gian_di_qua)` → Ghi lịch sử phương tiện
- `log_detected_license_plate(license_plate, thoi_gian, ngay, detection_count, avg_confidence, image_paths, camera_id)` → Ghi biển số phát hiện

**Truy vấn biển số**:
- `get_detected_license_plates(limit)` → Lấy danh sách biển số gần đây
- `get_license_plate_by_date(detected_date)` → Lấy biển số theo ngày cụ thể

---

## 8.4 CẬP NHẬT CSDL GẦN ĐÂY (2026-04-15 → 2026-04-16)

### Migration Database (2026-04-15)

**Vấn đề cũ**:
- ❌ id_camera trong `vi_pham_do_xe` & `nhat_ky_un_tac` là N/A (text)
- ❌ Đường dẫn biển số không tuân theo cấu trúc `logs/plates/YYYY/MM/DD/`

**Giải pháp - Chạy migration**:
```bash
# Tại thư mục root của project
python run_migration.py
```

**Kết quả migration**:
- ✅ Tất cả N/A → 0 trong `vi_pham_do_xe.id_camera`
- ✅ Tất cả N/A → 0 trong `nhat_ky_un_tac.id_camera`
- ✅ Đường dẫn biển số: `logs/plates/{YYYY}/{MM}/{DD}/{filename}.jpg`

**Hàm migration**:
- `migrate_camera_ids_and_plates()` → dict với số record đã update
- `fix_image_paths()` → int với số file được chuẩn hóa

### Cải tiến schema
1. **Bảng `bien_so_phat_hien`** - Thêm cột:
   - `thoi_gian_phat_hien` (HH:MM:SS) — Lưu thời gian phát hiện chính xác
   - `id_camera` (DEFAULT 0) — Phân biệt test data (0) vs production data (>0)

2. **Bảng `nhat_ky_un_tac`** - Giữ nguyên:
   - `thoi_gian_ket_thuc` (nullable) — Thời gian kết thúc tắc nghẽn
   - `thoi_gian_keo_dai_giay` — Tự động tính từ start_time → end_time

3. **Bảng `lich_su_phuong_tien`** - Cột `id_camera`:
   - DEFAULT 0 để phân biệt dữ liệu test/production

4. **Bảng `cau_hinh_he_thong`** (NEW):
   - Lưu cấu hình toàn cục (confidence, frame_skip, etc.)
   - Giá trị mặc định tại init_db

### Hàm mới & cải tiến
1. **`log_congestion()`** - Cải tiến:
   - Trả về `record_id` (int) thay vì None
   - Cho phép gọi `update_congestion_end_time(record_id)` sau

2. **`update_congestion_end_time(congestion_id, end_time=None)`** (NEW):
   - Cập nhật `thoi_gian_ket_thuc` động (khi tắc nghẽn kết thúc)
   - Tự động tính toán `thoi_gian_keo_dai_giay` từ start → end time
   - **Quan trọng**: Gọi khi traffic_level từ >0 → 0

3. **`get_congestion_history()`** (NEW):
   - Lấy lịch sử tắc nghẽn với duration & camera name
   - Giúp báo cáo & phân tích

4. **`resolve_parking_violation(violation_id)`** (NEW):
   - Đánh dấu vi phạm `da_giai_quyet = 1`

5. **`get_system_settings()` & `update_system_settings()`** (NEW):
   - Quản lý cấu hình từ bảng `cau_hinh_he_thong`

### Workflow tắc nghẽn (Enhanced)
```
Frame xử lý:
  traffic_level = 0 (thông thoáng)
         ↓
  traffic_level > 0 (tắc nghẽn bắt đầu)
         ↓
  congestion_id = log_congestion(camera_id, level)  ← Lưu start_time
         ↓
  (Tiếp tục lưu frame...)
         ↓
  traffic_level = 0 (tắc nghẽn kết thúc)
         ↓
  update_congestion_end_time(congestion_id)  ← Cập nhật end_time + duration
         ↓
  DB ghi nhận: start_time, end_time, duration_seconds
```

### Ưu tiên khi sử dụng
- **Luôn gọi `update_congestion_end_time()` khi transition từ congestion → clear**
- **Gọi khi video hoàn thành** (trong finally block) nếu vẫn có record không đóng

---

## 9. WEB APPLICATION (FastAPI)

### 9.1 2 Cách chạy FastAPI
- **Option A** (Clean Architecture - KHUYÊN): `app.py` → Chạy từ root directory
  - Command: `python -m uvicorn app:app --host 0.0.0.0 --port 5000`
  - Hoặc: `python app.py`
- **Option B** (Legacy): `main_api.py` + `routers/` → Cũ nhưng còn dùng được

### 9.2 Clean Architecture (app.py)
```
Presentation Layer (web views) → Application Layer (use_cases) → Domain Layer (entities, repos) → Infrastructure (DB, ML)
```

**Cấu trúc app.py**:
- Path setup: `app.py` ở root, `frontend/` chứa các module
- DB init: Gọi `init_db()` khi app start
- Middleware: SessionMiddleware + Custom auth middleware
- Static files: Mount `frontend/static/` & `logs/`
- Routers: Include tất cả 8 routers

**Routes (Clean Architecture - app.py từ root)**:
- `auth_router` (frontend/presentation/web/auth_views.py)
  - `GET /login` → Trang đăng nhập
  - `POST /login` → Xử lý login
  - `GET /logout` → Logout

- `dashboard_router` (frontend/presentation/web/dashboard_views.py)
  - `GET /dashboard` → Trang tổng quan

- `camera_router` (frontend/presentation/web/camera_views.py)
  - `GET /cameras` → Quản lý camera
  - REST API cho CRUD camera

- `user_router` (frontend/presentation/web/user_views.py)
  - `GET /users` → Quản lý người dùng
  - REST API cho CRUD user

- `vehicle_router` (frontend/presentation/web/vehicle_views.py)
  - `GET /vehicles` → Xem lịch sử phương tiện
  - REST API cho license plates

- `congestion_router` (frontend/presentation/web/congestion_views.py)
  - `GET /congestion` → Xem thống kê tắc nghẽn
  - REST API cho dữ liệu tắc nghẽn

- `violation_router` (frontend/presentation/web/violation_views.py)
  - `GET /violations` → Xem vi phạm đỗ xe
  - REST API cho dữ liệu vi phạm

- `test_video_router` (frontend/presentation/web/test_video_views.py)
  - `GET /test-video` → Trang test video
  - REST API cho job management
  - `GET /api/test-jobs/{id}/stream` → MJPEG streaming

### 9.3 Legacy Routes (main_api.py + routers/)
```python
# main_api.py imports:
from routers import web_views, api_users, api_cameras, api_jobs, api_license_plates

app.include_router(web_views.router)
app.include_router(api_users.router)
app.include_router(api_cameras.router)
app.include_router(api_jobs.router)
app.include_router(api_license_plates.router)  # NEW
```

**Routes (Legacy)**:
| Route | Method | Mô tả |
|-------|--------|-------|
| `/api/license-plates` | GET | Lấy danh sách biển số (limit, offset) |
| `/api/license-plates/date/{detected_date}` | GET | Lấy biển số theo ngày (YYYY-MM-DD) |
| `/api/license-plates` | POST | Ghi lại một biển số phát hiện |
| `/results/{filename}` | GET | Download kết quả video (REMOVED: no output file) |
| `/api/test-jobs` | POST | Submit detection job (NEW: BytesIO stream input) |
| `/api/test-jobs/{id}` | GET | Lấy thông tin job (NEW: result_url=None) |
| `/api/test-jobs/{id}/stream` | GET | MJPEG streaming thực tế (real-time frame) |
| `/api/test-jobs/{id}/abort` | POST | Hủy job |

**REMOVED Endpoints**:
- ❌ `/job-sources/{job_id}` — Không cần (stream-only, no input file storage)

### 9.4 Detection Bridge (frontend/infrastructure/ml/detection_bridge.py)
- Phiên bản **headless** (không GUI) của pipeline detection — **Đã tích hợp đầy đủ OCR**
- **Hỗ trợ stream input** từ BytesIO hoặc file path
  - `input_stream`: BytesIO object (video bytes từ upload, không lưu file)
  - `input_path`: Path string (backward compat, legacy)
  - `input_ext`: File extension (.mp4, .avi, etc.)
  - Video tạm decode từ BytesIO → temp file → xử lý → cleanup
- Bỏ cv2.VideoWriter → không ghi output video vào disk (stream-only)
- Dùng cho web: `process_video(input_stream=bytes_io, input_ext='.mp4', output_path=None, settings, progress_callback)`
- **Hỗ trợ đầy đủ**: congestion detection, illegal parking, **OCR nhận diện ký tự biển số** (PaddleOCR + OCRManager + ALPRLogger)
- OCR pipeline giống hệt `main.py`: perspective → CLAHE → PaddleOCR → voting → regex → ghi log CSV + ảnh bằng chứng
- Tham số `enable_ocr=True` để bật/tắt OCR (mặc định bật)
- Trả về summary dict: `max_vehicle_count`, `max_people_count`, `ocr_confirmed_plates` (list biển số), `ocr_confirmed_plate_count`, `parking_violation_ids`, `output_path=None`

### 9.5 Job Manager (services/job_manager.py)
- ThreadPoolExecutor(max_workers=1) — chạy 1 job tại 1 thời điểm
- **NEW**: Nhận video stream (BytesIO) thay vì file path
  - Signature: `run_detection_job(job_id, video_stream: BytesIO, file_ext, output_filename, settings)`
  - Backward compat: vẫn support `input_path: str` (dùng cho legacy)
- Sau khi job hoàn thành → xóa file tạm + **KHÔNG** lưu output file
- Summary trả về: processed frames, max counts, traffic_level, violations, BUT `output_path=None`
- Hỗ trợ abort job (đặt status="aborted")

---

## 10. CẤU HÌNH & ENVIRONMENT

```env
TELEGRAM_BOT_TOKEN=<bot_token>
TELEGRAM_CHAT_ID=<chat_id>
```

### Paths quan trọng
| Biến | Giá trị |
|------|---------|
| `PROJECT_ROOT` | `e:\DATN_PROJECT` |
| `APP_DIR` | `frontend/` |
| `DB_PATH` | `frontend/portal.db` **(cập nhật)** |
| `DEFAULT_MODEL_PATH` | `models/best.pt` |
| `INPUTS_DIR` | `frontend/runtime/inputs/` |
| `OUTPUTS_DIR` | `frontend/runtime/outputs/` |
| `PREVIEWS_DIR` | `frontend/runtime/previews/` |

---

## 11. LUỒNG DỮ LIỆU CHÍNH (DATA FLOW)

```
Camera/Video → YOLO26m.track(ByteTrack) → Bounding Boxes + Track IDs
                                              │
              ┌───────────────────────────────┼───────────────────────────┐
              ↓                               ↓                          ↓
    TrafficMonitor                    ParkingManager                 OCRManager
    - Đếm xe/người                    - State machine               - Crop biển số
    - Occupancy (mask)                - Ghost re-ID                 - Perspective transform
    - Speed (px/s)                    - Video evidence              - CLAHE + PaddleOCR
    - 4 cấp tắc nghẽn                - Telegram alert              - Voting (Counter)
              ↓                               ↓                     - Regex validate
    TrafficAlertManager               logs/violations/              - correct_plate_format
    - Debounce + Snooze                                                    ↓
    - Telegram alert                                               ALPRLogger
                                                                   - logs/ALPR_log.csv
                                                                   - logs/plates/*.jpg
```

---

## 12. LƯU Ý QUAN TRỌNG CHO AI AGENT

### Kiến trúc & Data Flow
1. **2 pipeline song song** (cùng logic OCR):
   - Desktop (main.py): Tkinter GUI + OCR text recognition
   - Web (app.py hoặc main_api.py): Headless detection, **ĐÃ CÓ đầy đủ OCR text recognition** (frontend/infrastructure/ml/detection_bridge.py)

2. **Clean Architecture được áp dụng trong Frontend**:
   - Presentation Layer: web views (auth, dashboard, camera, user, test_video, vehicle views)
   - Application Layer: use_cases (login, search, create camera, etc.)
   - Domain Layer: entities (User, Camera, Job) + repositories (clean interfaces)
   - Infrastructure Layer: DB access (sqlite_db.py), ML services (detection_bridge.py, ocr_license_plate.py)

3. **Database**:
   - Vị trí: `frontend/portal.db` (KHÔNG phải runtime/ subdirectory)
   - Các bảng sử dụng tiếng Việt
   - Có 2 bảng mới: `bien_so_phat_hien`, `lich_su_phuong_tien`
   - KHÔNG có bảng `traffic_flows`: Traffic data chỉ real-time

### Algorithm & Detection Details
4. **Không có counting line**: Hệ thống đếm xe **hiện diện**, KHÔNG đếm ra/vào
5. **Bicycle được nhận dạng**: Được bao gồm trong `target_classes` filter → hiển thị bình thường
6. **OCR chỉ xử lý biển số nằm trong xe lớn** (car/bus/truck), xe máy & xe đạp KHÔNG OCR
7. **Parking chỉ xét car/bus/truck**, KHÔNG xét motorcycle/bicycle/person
8. **Grace period & Ghost tracks**: Cơ chế duy trì track ByteTrack mất dấu tạm thời

### Configuration & Deployment
9. **Model**: Web dùng `models/best.pt`, Desktop người dùng tự chọn qua GUI
10. **Telegram credentials** nằm trong `.env` (KHÔNG commit lên git)
11. **Video streaming**: Web dùng MJPEG streaming qua `/api/test-jobs/{id}/stream`
12. **API License Plates** (NEW): `/api/license-plates` có 3 endpoints GET/POST
13. **Job Manager**: ThreadPoolExecutor(max_workers=1) — chỉ 1 job cùng lúc
14. **Session-based auth**: FastAPI sử dụng SessionMiddleware với secret key

### Cách chạy
15. **Desktop**: `python run_system.py` hoặc `python main.py` (Tkinter + detection modules)
16. **Web (Clean Architecture)**: `python app.py` hoặc `python -m uvicorn app:app --host 0.0.0.0 --port 5000` (KHUYÊN DÙNG)
17. **Web (Legacy)**: `python main_api.py` (sử dụng routers/, cũ hơn)
18. **Stream-only Processing** (NEW): Upload → BytesIO → Temp file → Process → Stream MJPEG → Delete temp → No output file saved

---

## 13. STREAM-ONLY PROCESSING MODEL (NEW - 2026-04-10)

### Refactored Test Video Feature
**Workflow**:
```
1. User uploads video file
   ↓
2. POST /api/test-jobs → Read bytes (NO DISK SAVE)
   ↓ (BytesIO in memory)
3. Backend creates SHORT-LIVED temp file
   ↓
4. process_video(input_stream, output_path=None)
   ↓ (Frame-by-frame YOLO detection)
5. Each frame: JPEG encoded → job["latest_frame"]
   ↓
6. GET /api/test-jobs/{id}/stream (MJPEG)
   ↓ (Frontend <img> tag receives stream)
7. Processing complete → Cleanup temp file
   ↓
8. Return summary dict (output_path=None)
   ↓
(RESULT: No video files in runtime/ folder)
```

### Key Changes
| Component | Old | New |
|-----------|-----|-----|
| Input handling | Save to `INPUTS_DIR` | BytesIO stream (temp file) |
| Output video | Write to `OUTPUTS_DIR` | None (stream only) |
| /job-sources endpoint | Serve input video | REMOVED |
| /results endpoint | Download output MP4 | Returns 404 (no output) |
| Streaming | MJPEG from disk | MJPEG from memory |
| UI preview boxes | 2 (input + stream) | 1 (stream only) |
| Dependencies | OpenCV only | + PyAV (for stream), + tempfile |

### Benefits
✅ No input/output disk I/O
✅ Real-time frame streaming
✅ Minimal storage footprint
✅ Clean memory model (1 frame buffered)
✅ Faster job startup (no file I/O overhead)

### Implementation Files Modified
- `routers/api_jobs.py`: Skip file save, pass BytesIO
- `services/job_manager.py`: Accept stream, call process_video(input_stream, output_path=None)
- `frontend/infrastructure/ml/detection_bridge.py`: Support BytesIO + temp file + cleanup
- `requirements.txt`: Add `av` (PyAV)
- `frontend/templates/test_video.html`: Update description + fullscreen viewer
- `AI_CONTEXT.md`: Document changes

### Fullscreen Video Viewer (2026-04-19)
- **Tính năng**: Xem stream MJPEG toàn màn hình bằng Fullscreen API của trình duyệt
- **Cách dùng**:
  - Nút "Toàn màn hình" (icon expand) ở góc trên bên phải phần Live Stream
  - Nút "Thoát (ESC)" xuất hiện bên trong chế độ fullscreen (góc trên phải)
  - Phím ESC để thoát (xử lý tự động bởi trình duyệt Fullscreen API)
- **Cấu trúc HTML**: `#stream-fullscreen-container` bọc `#stream-output` (img) + `#fullscreen-exit-btn`
- **CSS**: `.stream-fullscreen-container:fullscreen` pseudo-class để style khi fullscreen
- **JS**: `enterFullscreen()` / `exitFullscreen()` sử dụng `requestFullscreen()` / `document.exitFullscreen()` (có prefix webkit/ms cho trình duyệt cũ)
- **Files sửa đổi**: `test_video.html`, `styles.css`, `test_video.js`

---

## 14. GHI CHÚ THÊM VỀ KIẾN TRÚC VÀ TRIỂN KHAI

### 14.1 App.py Entry Point (CONFIRMED 2026-04-16)
- **Vị trí**: Root directory: **`e:\DATN_PROJECT\app.py`** ✅ (NOT frontend/app.py)
- **Khởi động**: `python app.py` hoặc `python -m uvicorn app:app --host 0.0.0.0 --port 5000`
- **Cấu trúc**:
  - Path setup: Thêm `frontend/` vào đầu sys.path (line 18-20 của app.py) để import modules không cần prefix
  - DB init: Gọi `init_db()` tại startup (line 44)
  - Middleware (2 layers):
    - Custom auth middleware (inner): Load user từ session → `request.state.current_user`
    - SessionMiddleware (outer): Quản lý session (secret_key từ `frontend/core/config.py`)
  - Static mounts: `/static/` → `frontend/static/`, `/logs/` → `logs/`
  - 8 routers đã register: auth, dashboard, user, camera, vehicle, congestion, violation, test_video
  - Error handlers: 403, 404 trả về error.html
- **Clean Architecture**: Layers hoàn toàn tách biệt (Presentation → Application → Domain → Infrastructure)

### 14.2 Frontend Module Structure
- **Clean Architecture Layers**:
  - **Presentation**: routers (web views) trong `frontend/presentation/web/`
  - **Application**: use_cases trong `frontend/application/use_cases/`
  - **Domain**: entities + repositories trong `frontend/domain/`
  - **Infrastructure**: DB (sqlite_db.py) + ML (detection_bridge.py) trong các thư mục con

### 14.3 Thêm Web Views (Templates & Routers)
- **Templates mới**: `license_plates.html`, `violations.html`, `congestion.html`, `settings.html`
- **Routers mới**: `violation_views.py`, `congestion_views.py` (ngoài vehicle_views.py cũ)
- **Database tables mới**: `bien_so_phat_hien`, `lich_su_phuong_tien`, `nhat_ky_un_tac`

### 14.4 Requirements.txt
Cần thêm các package sau nếu chưa có:
```
aiofiles
starlette
uvicorn
werkzeug
jinja2
python-multipart
```

### 14.5 Ưu tiên triển khai
1. **Option A (Khuyên - BEST PRACTICE)**: `python app.py` hoặc `python -m uvicorn app:app --host 0.0.0.0 --port 5000`
   - Entry point: **root directory `/app.py`**
   - Kiến trúc: Clean Architecture (Presentation → Application → Domain → Infrastructure)
   - Tất cả 8 routers đã include (auth, dashboard, user, camera, vehicle, congestion, violation, test_video)
   - Middleware: SessionMiddleware + custom auth
   - Production ready ✅

2. **Option B (Legacy)**: `python main_api.py`
   - Dùng `routers/` folder cũ
   - Thích hợp nếu chỉ cần API đơn giản
   - Ít middleware, không có auth session

3. **Option C (Desktop + Web Hybrid)**: `python run_system.py`
   - Chạy cả FastAPI + Tkinter GUI
   - FastAPI chạy ở port 5000 (thread 1)
   - Tkinter GUI chạy ở thread chính

---

## 15. CAMERA ID - QUY TẮC LƯU TRỮ DỮ LIỆU & DATABASE CLEANUP STATUS

### 15.1 Camera ID trong các chế độ chạy
| Chế độ | camera_id | Nguồn | Mô tả |
|--------|-----------|-------|-------|
| **Test Video (Web UI)** | **0** (fixed) | Hardcoded tại `routers/api_jobs.py` | Test video luôn dùng camera_id = 0 |
| **Real-time Detection** | *Dynamic* | Từ camera object trong database | Lấy từ `camera.id` khi chạy từ hệ thống thực |
| **Desktop GUI** | **0** (default) | `ALPRLogger(id_camera=0)` | Chế độ desktop dùng 0 |

### 15.2 Tác dụng của camera_id = 0
- **Mục đích**: Đánh dấu dữ liệu là từ **Test Video** hoặc **Desktop GUI**
- **Ưu điểm**: Dễ phân biệt dữ liệu test từ dữ liệu sản phẩm thực tế
- **Áp dụng cho**: Biển số, vi phạm đỗ xe, lịch sử phương tiện, tắc nghẽn
- **Không ảnh hưởng**: Cấu trúc thư mục lưu ảnh, tên file, logic đăng ký

### 15.3 Cấu trúc dữ liệu theo camera_id
```
logs/
├── plates/                          # Tất cả ảnh biển số (camera_id không ảnh hưởng thư mục)
│   ├── 2026/
│   │   ├── 04/
│   │   │   ├── 15/
│   │   │   │   ├── 29A123BC_20260415_120530.jpg
│   │   │   │   └── ...
│   ├── violations/                  # Vi phạm đỗ xe (cũng dùng plate name, không camera folder)
│   │   ├── 29A123BC/
│   │   │   ├── EVT_20260415_120530_1/
│   │   │   │   ├── img_T0.jpg
│   │   │   │   ├── img_T1.jpg
│   │   │   │   └── evidence.json (chứa track_id, plate, timestamps)
│   │   ├── ID_999/
│   │   │   ├── EVT_20260415_120530_999/
│   │   │   │   └── ... (nếu không có plate, dùng ID thay thế)
│   └── ALPR_log.csv                 # Log tất cả biển số (có Time, Frame, Plate, Path)
```

### 15.4 Database - Cột camera_id
- **bien_so_phat_hien**: `id_camera` = 0 cho test video, > 0 cho camera thực
- **vi_pham_do_xe**: `id_camera` = 0 cho test video, > 0 cho camera thực
- **nhat_ky_un_tac**: `id_camera` = 0 cho test video, > 0 cho camera thực
- **lich_su_phuong_tien**: `id_camera` = 0 cho test video, > 0 cho camera thực

### 15.5 Khi nào dùng camera_id
✅ **Lọc dữ liệu**: `SELECT * FROM bien_so_phat_hien WHERE id_camera = 1` → Chỉ lấy biển số của camera 1

✅ **Thống kê theo camera**: Báo cáo tắc nghẽn/vi phạm riêng từng camera

✅ **Audit trail**: Phân biệt test data (id=0) vs. production data (id>0)

❌ **KHÔNG dùng** để thay đổi thư mục lưu ảnh (vẫn dùng date-based folder)

### 15.6 CRITICAL UPDATE: Violation Recording Deduplication (2026-04-15)

**BUG DISCOVERED & FIXED**:
- **Before**: Khi track state = VIOLATION, ghi log MỖI FRAME → 300+ duplicate records từ cùng 1 track
- **After**: Ghi log chỉ 1 lần khi track lần đầu vào VIOLATION state

**Implementation** (detection_bridge.py):
```python
logged_violation_track_ids = set()  # Track những ID đã ghi violation

if track_id in logged_violation_track_ids:
    continue  # Skip: đã ghi rồi

if state == 'VIOLATION':
    log_violation_to_db(track_id, license_plate, ...)
    logged_violation_track_ids.add(track_id)  # Đánh dấu
```

**Database Impact**:
- Xóa 300+ duplicate records thủ công (2026-04-15 02:43:22)
- Backup: `frontend/portal.db.backup`
- Current: ~1300 total records (~300 valid + ~1000 legacy ID_* từ trước)
- **TODO**: Quyết định xóa hay giữ legacy ID_* records

**Verification**: ✅ Tested & confirmed - no more duplicate violations per frame

---

## 16. CÁC SỬA CHỮA VÀ CẢI TIẾN GẦN ĐÂY (2026-04-15 ~ 2026-04-16)

### Loại Bỏ Trùng Lặp Ghi Log Vi Phạm ✅ ĐÃ SỬA
- **Vấn đề**: Cùng một vi phạm được ghi log 300+ lần mỗi frame → 1400+ record trong DB
- **Nguyên nhân gốc**: Thiếu cơ chế loại bỏ trùng lặp trong `detection_bridge.py`
- **Giải pháp**: Thêm set `logged_violation_track_ids` để theo dõi những vi phạm đã được ghi
- **Trạng thái**: ✅ Đã triển khai & test thành công
- **Tác động**: Database giờ đã sạch sẽ với các record vi phạm hợp lệ

### Dọn Dẹp Database ✅ ĐÃ HOÀN THÀNH
- **Hành động**: Xóa thủ công 300+ record trùng lặp (2026-04-15 02:43:22)
- **Sao lưu**: Tạo `frontend/portal.db.backup` trước khi dọn dẹp
- **Trạng thái hiện tại**: ~1300 record (~300 hợp lệ + ~1000 cũ từ trước)
- **Ghi chú**: Các record ID_* cũ từ trước khi sửa lỗi, có thể xóa tiếp nếu cần

### Xác Minh Clean Architecture ✅ ĐÃ KIỂM CHỨNG
- **app.py**: Xác nhận ở thư mục gốc (`e:\DATN_PROJECT\app.py`) với setup đúng
- **8 Routers**: Tất cả đã đăng ký và hoạt động bình thường
- **Middleware**: SessionMiddleware + custom auth hoạt động chính xác
- **Các tệp tĩnh**: `/static/` và `/logs/` mounts đang hoạt động

### Trạng Thái Pipeline Phát Hiện ✅ ĐANG HOẠT ĐỘNG
- **Chế độ Desktop** (main.py): Pipeline đầy đủ với OCR, parking, traffic modules
- **Chế độ Web** (app.py): Pipeline phát hiện không giao diện với stream-only processing
- **Cả hai pipeline**: Có đầy đủ OCR (PaddleOCR + voting + regex + CLAHE)

### Giới Hạn Hiện Tại & Việc Cần Làm
1. **Dữ liệu cũ**: ~1000 record có giá trị ID_* (từ trước khi sửa trùng lặp)
   - Lựa chọn A: Giữ lại để theo dõi lịch sử
   - Lựa chọn B: Xóa bằng `DELETE FROM vi_pham_do_xe WHERE bien_so LIKE 'ID_%'`
   - Đề xuất: Giữ tạm thời, quyết định sau khi xác minh đầy đủ

2. **Gộp Model**: 9 file model hiện có (một số có thể thừa)
   - `best.pt`, `best.onnx`, `best.engine` (chính - khuyên dùng)
   - `yolo26l.*` variants (cũ, có thể lưu trữ)
   - `mediumfinetune_v1.pt`, `medium_finetune_v2.pt` (thử nghiệm)
   - `plate_detect_model.pt` (phát hiện biển cũ, OCR được ưu tiên hiện nay)

3. **Requirements.txt**: Danh sách tối thiểu (9 gói), nhiều dependency không được liệt kê
   - Đề xuất: Chạy `pip freeze > requirements-full.txt` để có danh sách đầy đủ
   - Hiện tại: opencv, numpy, ultralytics, paddleocr, FastAPI, pyTelegramBotAPI, requests, python-dotenv, av

### Trạng Thái Tài Liệu
- **AI_CONTEXT.md**: Cập nhật lên 2026-04-19 với tất cả thay đổi gần đây được ghi lại
- **Ghi chú code**: Cập nhật trong detection_bridge.py, app.py
- **README.md**: Cần cập nhật hướng dẫn triển khai

### Fullscreen Video Viewer ✅ ĐÃ TRIỂN KHAI (2026-04-19)
- **Tính năng**: Xem Live Stream (MJPEG) toàn màn hình trong trang Test Video
- **Cơ chế**: Sử dụng native Fullscreen API của trình duyệt (requestFullscreen / exitFullscreen)
- **Thoát fullscreen**: Nút "Thoát (ESC)" overlay + phím ESC (tự động bởi trình duyệt)
- **Cross-browser**: Hỗ trợ webkit (Safari) và ms (IE/Edge cũ) prefix
- **Files thay đổi**:
  - `frontend/templates/test_video.html`: Thêm nút fullscreen + exit button + container
  - `frontend/static/css/styles.css`: Thêm styles `.stream-fullscreen-container`, `.fullscreen-exit-btn`, pseudo-class `:fullscreen`
  - `frontend/static/js/test_video.js`: Thêm logic `enterFullscreen()`, `exitFullscreen()`, event listeners

### Async I/O Worker ✅ ĐÃ TRIỂN KHAI (2026-04-19)
- **Vấn đề**: Stream MJPEG bị khựng 1-5 giây mỗi khi gửi Telegram hoặc ghi file/DB, GPU và CPU không được tận dụng tối đa
- **Giải pháp**: Tạo module `AsyncIOWorker` (hàng đợi I/O chạy trên 2 background threads)
- **Cơ chế**: Tất cả tác vụ I/O nặng (được đẩy vào queue bằng `enqueue()` — trả về tức thì) thay vì blocking vòng lặp frame:
  - Gửi Telegram (ảnh, video, cảnh báo tắc nghẽ)
  - Ghi ảnh bằng chứng (cv2.imwrite)
  - Ghi CSV, ghi DB (log_passed_vehicle, update_congestion_end_time)
- **Backward compatible**: Nếu `io_worker = None`, tất cả các module fallback về gọi đồng bộ (desktop GUI không bị ảnh hưởng)
- **Graceful shutdown**: `io_worker.shutdown(wait=True)` chờ xử lý hết queue trước khi kết thúc job
- **Files thay đổi**:
  - `modules/utils/async_io_worker.py` [NEW]: Module AsyncIOWorker
  - `modules/utils/traffic_alert_manager.py`: Dùng io_worker cho `_trigger_alert()`
  - `modules/utils/alpr_logger.py`: Dùng io_worker cho `_save_log()`
  - `modules/parking/parking_manager.py`: Dùng io_worker cho Telegram sends
  - `frontend/infrastructure/ml/detection_bridge.py`: Tạo và inject io_worker vào tất cả managers

### Dynamic Scaling (Độ phân giải linh hoạt) ✅ ĐÃ TRIỂN KHAI (2026-04-19)
- **Vấn đề**: Khi chạy video 4K, chữ và khung Bounding Box bị quá bé, khó quan sát.
- **Giải pháp**: Tự động tính toán `fontScale` và `thickness` dựa trên tỉ lệ chiều rộng thực tế của frame so với chuẩn HD (1280px).
- **Phạm vi áp dụng**:
  - Bounding Box và Label ID của xe/người.
  - Các thông số của Traffic Monitor (Số xe, Vận tốc, Trạng thái).
  - Các thông báo đè (WAITING, VIOLATION) và banner "VI PHẠM".
  - Các ảnh bằng chứng ghép (Combined Warning/Violation image).
  - Khung biển số trong `ALPRLogger`.
- **Logic**: Hàm `_get_drawing_params(width)` trả về bộ tham số (scale, thickness, offset) phù hợp.


### Video Frame Extraction Optimization (2026-04-27)
- **Vấn đề**: Việc upload toàn bộ video 4K H.265 (nặng hàng trăm MB đến GB) lên Backend chỉ để lấy 1 frame duy nhất phục vụ việc vẽ ROI qua môi trường Web là cực kỳ chậm và tốn tài nguyên. Ngoài ra, việc dùng Javascript lấy frame từ video H.264 đôi khi bị màn hình đen do mất đồng bộ với GPU.
- **Giải pháp Frontend (test_video.js)**:
  - **Tối ưu H.264 (Local)**: Xây dựng cơ chế quét Pixel (`isBlankFrame`) trên Canvas kết hợp sự kiện `loadeddata`, `seeked`, `timeupdate` và `play()` để đảm bảo Trình duyệt và GPU đồng bộ, bắt buộc kết xuất ra hình ảnh thực tế (tránh màn hình đen) trong vỏn vẹn 0.1s.
  - **Fallback H.265 (Server Faststart)**: Xây dựng cơ chế cắt 5MB đầu tiên (`file.slice(0, 5MB)`) để gửi lên Server thay vì gửi toàn bộ.
- **Giải pháp Backend/Công cụ (convert_video.py)**:
  - Viết lại toàn bộ `convert_video.py` thành một công cụ GUI (Tkinter) chuyên dụng cho Web AI.
  - Sử dụng lệnh Remux `ffmpeg -c copy -movflags +faststart` siêu tốc (1 giây/video) để di chuyển mục lục (MOOV atom) lên đầu file H.265 mà KHÔNG thay đổi chất lượng hay dung lượng gốc (100MB). Điều này cho phép Server có thể decode frame ngay từ 5MB upload đầu tiên.
- **Files thay đổi**:
  - `frontend/static/js/test_video.js`: Cập nhật logic `extractLocally` và `catch` fallback cắt 5MB.
  - `convert_video.py`: Viết lại thành Tkinter GUI App, tích hợp search `ffmpeg` tự động trên môi trường Windows/WinGet.
  - `frontend/templates/test_video.html`: Cập nhật query version `v=3.1`.

### Bypassing Proxy Limits & RAM Optimization (2026-04-27)
- **Vấn đề**: Cloudflare (và các proxy server) giới hạn dung lượng Request Body ở mức 100MB (Free Tier), khiến việc upload file video > 100MB bị lỗi 413. Cùng với đó, việc FastAPI đọc toàn bộ video vào RAM qua `io.BytesIO` gây rò rỉ bộ nhớ (chạy vài lần lên tới 4.2GB RAM).
- **Giải pháp**: Xây dựng kiến trúc Upload Phân Mảnh (Chunked Upload) kết hợp truyền tệp từ đĩa cứng.
- **Cơ chế**:
  1. Trình duyệt chia video lớn thành các đoạn 20MB.
  2. Tải lần lượt từng đoạn lên `/api/upload-chunk` qua XMLHttpRequest. Giao diện hiển thị thanh phần trăm mượt mà.
  3. Server tự động ráp các đoạn thành file tạm thời trên đĩa cứng.
  4. Server truyền thẳng đường dẫn file `input_path` cho AI xử lý. OpenCV đọc trực tiếp từ đĩa (0 RAM bị chiếm dụng dư thừa).
  5. Xoá file tạm an toàn trong `finally` block của background job.
- **Files thay đổi**:
  - `frontend/static/js/common.js`: Thêm API `submitFormChunked` bằng XHR thuần.
  - `frontend/presentation/web/test_video_views.py`: Thêm Endpoint nhận chunk ráp nối file tạm, truyền tham số `input_path`.
  - `application/use_cases/job_use_cases.py`: Xử lý `input_path` và thực hiện dọn dẹp file tạm (`os.remove`) trong `run_detection_job`.
  - `frontend/static/js/test_video.js`: Việt hoá UI và cập nhật listener chuyển sang chế độ upload Chunked.

### Web UI Interaction Fix (2026-04-27)
- **Vấn đề**: Chức năng "Xác nhận đã xử lý" trên giao diện danh sách Vi phạm (violations.html) không hoạt động do gọi sai hàm API frontend không tồn tại.
- **Giải pháp**: 
  - Cập nhật file `frontend/templates/violations.html` để sử dụng đúng hàm `portalApi.post()`.
  - Định tuyến lại endpoint thành `/api/violations/{id}/resolve` cho khớp với router FastApi.

### System CPU Optimization & Background Threads (2026-04-27)
- **Vấn đề**: Quá trình phân tích trên Web Dashboard có FPS thấp (15-25fps) trong khi GPU hoạt động dưới 30%. Luồng chính bị tắc nghẽn (CPU Bottleneck) bởi các tác vụ nén ảnh JPEG và chạy OCR cho những đối tượng không cần thiết.
- **Giải pháp**:
  - **Lọc đối tượng OCR**: Cập nhật logic trong vòng lặp chuẩn bị dữ liệu OCR, sử dụng `PARKING_LABELS` (`car`, `bus`, `truck`) để bỏ qua hoàn toàn việc cố gắng quét biển số xe máy (tiết kiệm rất nhiều CPU).
  - **Đa luồng nén ảnh (Background JPEG Encoding)**: Đẩy tác vụ nén ảnh gửi lên web (`cv2.imencode`) sang một luồng phụ (worker thread) thông qua `queue.Queue()`. Luồng chính phân tích AI chỉ việc quăng frame vào queue rồi chạy tiếp mà không bị khựng lại.
  - **Sửa lỗi tính toán Speed**: Cập nhật module `traffic_monitor.py` để không ghi trùng vị trí cũ trong lúc bỏ qua khung hình (khi người dùng thiết lập xử lý mỗi N frame), giúp tốc độ đo đạc (px/s) chính xác, khắc phục lỗi hệ thống hay báo tắc nghẽn sai.
- **Files thay đổi**:
  - `frontend/infrastructure/ml/detection_bridge.py`: Thêm `preview_encoder_worker`, hiển thị FPS lên stream, và cập nhật bộ lọc OCR.
  - `modules/traffic/traffic_monitor.py`: Fix lỗi track_history bị trùng lặp khi frame skip.
  - `frontend/templates/violations.html`: Sửa API call `resolveViolation`.

### Directory Restructuring (Clean Architecture) (2026-04-27)
- **Vấn đề**: Cấu trúc thư mục cũ bị nhầm lẫn khi gộp chung toàn bộ mã nguồn Backend (FastAPI, Core, Domain, Use Cases) vào bên trong thư mục mang tên `frontend/` cùng với giao diện tĩnh. Việc này vi phạm các nguyên tắc Clean Architecture và gây bối rối khi bảo trì.
- **Giải pháp**: Tách bạch logic hệ thống:
  - **Backend**: Đổi tên thư mục `frontend` cũ thành `backend`. Toàn bộ logic nghiệp vụ, database, cấu hình đều nằm tại đây.
  - **Frontend**: Tạo thư mục `frontend` mới và di chuyển `static/` (CSS/JS) và `templates/` (HTML) vào.
  - **Cập nhật hệ thống**: Tự động thay đổi tất cả các path (như `BACKEND_DIR`), cập nhật Jinja2Templates configuration và refactor toàn bộ import statements từ `frontend.xxx` sang `backend.xxx` hoặc absolute imports.
- **Files thay đổi**:
  - `app.py`: Sửa `sys.path.insert`, `import backend.*` và `app.mount("/static")`.
  - `backend/presentation/container.py`: Cập nhật `templates = Jinja2Templates(directory=str(PROJECT_ROOT / "frontend" / "templates"))`.
  - Tất cả các file Python trong `backend/`: Cập nhật lại lệnh `import`.

### Các Bước Tiếp Theo (Theo Mức Độ Ưu Tiên)
1. **CAO**: Xác minh tất cả hàm OCR hoạt động chính xác trong chế độ web (app.py)
2. **CAO**: Test ghi log vi phạm với nhiều camera
3. **TRUNG BÌNH**: Dọn dẹp record ID_* cũ (hoặc ghi chú chính sách giữ lại)
4. **TRUNG BÌNH**: Gộp các file model (lưu trữ những cái không dùng)
5. **THẤP**: Cập nhật requirements.txt với tất cả các dependency rõ ràng
6. **THẤP**: Tạo hướng dẫn triển khai cho setup sản phẩm

---

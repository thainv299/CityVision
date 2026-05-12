from __future__ import annotations

import os
import sys
import time
import threading
import queue
import subprocess
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np
from ultralytics import YOLO

# Thiết lập PROJECT_ROOT để import các module từ thư mục gốc
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Import các manager và database
from modules.parking.parking_manager import ParkingManager
from modules.traffic.traffic_monitor import TrafficMonitor
from modules.ocr.ocr_manager import OCRManager
from modules.utils.alpr_logger import ALPRLogger
from modules.utils.traffic_alert_manager import TrafficAlertManager
from modules.utils.interactive_telegram_bot import start_bot_thread
from modules.utils.async_io_worker import AsyncIOWorker
from database.sqlite_db import (
    log_passed_vehicle,
    log_congestion,
    update_congestion_end_time,
    log_parking_violation,
    log_vehicle_count,
    log_detected_license_plate
)
from paddleocr import PaddleOCR
from collections import deque

# Nhãn nhận diện
TRAFFIC_LABELS = {"person", "bicycle", "car", "motorcycle", "bus", "truck"}
VEHICLE_LABELS = {"car", "motorcycle", "bus", "truck"}
PARKING_LABELS = {"car", "bus", "truck"}
LICENSE_PLATE_LABELS = {"license_plate", "licenseplate", "number_plate", "licence_plate"}
DETECTABLE_LABELS = TRAFFIC_LABELS | LICENSE_PLATE_LABELS

# Kích thước pipe từ FFmpeg — 1080p (1920px) cho chất lượng cao
PIPE_WIDTH = 1920

# Kích thước preview để encode JPEG gửi lên web (MJPEG stream)
# 1280px (720p) là mức cân bằng tốt cho chất lượng và băng thông
PREVIEW_WIDTH = 1280

# Màu sắc cho các loại phương tiện
BOX_COLORS = {
    "person": (0, 255, 0),
    "bicycle": (255, 127, 0),
    "car": (255, 255, 0),
    "motorcycle": (0, 255, 255),
    "bus": (0, 165, 255),
    "truck": (255, 0, 255),
    "license_plate": (255, 105, 180),
}


class VideoStream:
    """Luồng đọc frame sử dụng FFmpeg CLI để giải mã thô (Raw Video).
    Giải pháp để tránh crash async_lock của OpenCV trên Windows với H.265.
    """
    def __init__(self, path, force_single_thread: bool = False):
        self.path = str(path)
        self.stopped = False
        self.queue = queue.Queue(maxsize=32)
        self._is_opened = False
        # FIX #2: Chỉ dùng single-thread khi cần (H.265), H.264 dùng auto-thread
        self._force_single_thread = force_single_thread

        # Lấy metadata bằng ffprobe
        try:
            probe = subprocess.run([
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,r_frame_rate,nb_frames",
                "-of", "json", self.path
            ], capture_output=True, text=True, timeout=10)

            info = json.loads(probe.stdout)
            stream_info = info.get("streams", [{}])[0]
            self.width = int(stream_info.get("width", 1280))
            self.height = int(stream_info.get("height", 720))

            fps_str = stream_info.get("r_frame_rate", "25/1")
            if "/" in fps_str:
                num, den = fps_str.split("/")
                self.fps = float(num) / float(den) if float(den) > 0 else 25.0
            else:
                self.fps = float(fps_str)

            self.total_frames = int(stream_info.get("nb_frames", 0))
            self._is_opened = True
        except Exception as e:
            print(f"[VideoStream] Lỗi lấy metadata: {e}")
            self.width, self.height, self.fps, self.total_frames = 1280, 720, 25.0, 0
            # Fallback OpenCV
            cap = cv2.VideoCapture(self.path)
            if cap.isOpened():
                self.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                self.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                self.fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
                self.total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                self._is_opened = True
                cap.release()

        # FIX #1: Tính kích thước pipe ở 1080p (1920px)
        # Đây là kích thước frame thực tế trong pipeline AI (đảm bảo độ nét cao)
        if self.width > PIPE_WIDTH:
            self.draw_w = PIPE_WIDTH
            self.draw_h = int(self.height * (PIPE_WIDTH / self.width))
            self.draw_h = self.draw_h + (self.draw_h % 2)  # Đảm bảo chẵn cho FFmpeg
        else:
            self.draw_w = self.width
            self.draw_h = self.height

        # Kích thước preview để encode JPEG gửi web — nhỏ hơn để encode nhanh
        if self.draw_w > PREVIEW_WIDTH:
            self.preview_w = PREVIEW_WIDTH
            self.preview_h = int(self.draw_h * (PREVIEW_WIDTH / self.draw_w))
            self.preview_h = self.preview_h + (self.preview_h % 2)
        else:
            self.preview_w = self.draw_w
            self.preview_h = self.draw_h

        self._proc = None

    def start(self):
        t = threading.Thread(target=self.update)
        t.daemon = True
        t.start()
        return self

    @property
    def is_opened(self):
        return self._is_opened

    def isOpened(self):
        return self._is_opened

    def _build_ffmpeg_cmd(self) -> list:
        """Xây dựng lệnh FFmpeg tối ưu."""
        # FFmpeg giải mã độc lập với AI loop, dùng "0" (auto) để tận dụng đa nhân CPU.
        # Ngay cả với H.265, FFmpeg vẫn chạy ổn định hơn OpenCV.
        threads_val = "0" 
        cmd = [
            "ffmpeg", "-loglevel", "error",
            "-threads", "0", 
            "-i", str(self.path),
            "-vf", f"scale={self.draw_w}:{self.draw_h}",
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-"
        ]
        return cmd

    def update(self):
        frame_size = self.draw_w * self.draw_h * 3
        is_url = "://" in self.path or self.path.startswith("rtsp")

        self._proc = subprocess.Popen(
            self._build_ffmpeg_cmd(),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )

        while not self.stopped:
            try:
                raw = self._proc.stdout.read(frame_size)
                if len(raw) < frame_size:
                    # Hết video hoặc mất kết nối
                    self._proc.stdout.close()
                    self._proc.wait()
                    if self.stopped:
                        break
                    if is_url:
                        time.sleep(5)
                    # Loop lại từ đầu (file) hoặc reconnect (RTSP)
                    self._proc = subprocess.Popen(
                        self._build_ffmpeg_cmd(),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL
                    )
                    continue

                frame = np.frombuffer(raw, dtype=np.uint8).reshape((self.draw_h, self.draw_w, 3))
                
                if not self.queue.full():
                    self.queue.put(frame.copy())
                else:
                    # Nếu là RTSP/Live stream thì mới drop frame cũ (tránh lag tích lũy)
                    if is_url:
                        try:
                            self.queue.get_nowait()
                            self.queue.put(frame.copy())
                        except Exception: pass
                    else:
                        # Nếu là Video File, đợi cho đến khi có chỗ trong queue để không bị giật/mất frame
                        self.queue.put(frame.copy())
            except Exception as e:
                print(f"[VideoStream] Lỗi luồng đọc: {e}")
                if not self.stopped:
                    time.sleep(1)

    def read(self):
        if self.queue.empty() and self.stopped:
            return False, None
        try:
            return True, self.queue.get(timeout=2.0)
        except queue.Empty:
            return False, None

    def release(self):
        self.stopped = True
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=1)
            except Exception:
                if self._proc:
                    self._proc.kill()

    def set_pos(self, frame_idx):
        # Reset bằng cách restart process
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass


def _canonical_label(label: Any) -> str:
    """Chuẩn hóa label về dạng snake_case."""
    return str(label).strip().lower().replace("-", "_").replace(" ", "_")


def _display_label(label_key: str) -> str:
    """Chuyển key label sang dạng hiển thị."""
    return label_key.replace("_", " ")


def _is_hevc(path: Path) -> bool:
    """Kiểm tra video có dùng codec H.265/HEVC không."""
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_name",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path)
        ], capture_output=True, text=True, timeout=10)
        return result.stdout.strip().lower() in ("hevc", "h265")
    except Exception:
        return False


def _remux_to_faststart(input_path: Path) -> Path:
    """Remux H.265 sang Faststart bằng ffmpeg subprocess (không qua OpenCV)."""
    out_path = input_path.with_name(input_path.stem + "_fs" + input_path.suffix)
    print(f"[System] Đang remux video sang Faststart: {input_path.name}")
    try:
        result = subprocess.run([
            "ffmpeg", "-y",
            "-threads", "1",
            "-i", str(input_path),
            "-c", "copy",
            "-movflags", "+faststart",
            str(out_path)
        ], capture_output=True, timeout=120)

        if result.returncode == 0 and out_path.exists():
            print(f"[System] Remux thành công: {out_path.name}")
            return out_path
        else:
            print(f"[System] Remux thất bại: {result.stderr.decode(errors='ignore')[-200:]}")
            return input_path
    except Exception as e:
        print(f"[System] Remux exception: {e}")
        return input_path


def _normalize_points(points: Optional[List[List[Any]]], width: int, height: int, metadata: Dict[str, Any] = None) -> Optional[List[List[int]]]:
    """
    Quy đổi tọa độ từ Frontend về tọa độ Pixel của Video gốc.
    Hỗ trợ cả 3 chế độ: Pixels tuyệt đối, Tỉ lệ % (Legacy) và Tọa độ tham chiếu (Reference).
    """
    if not points:
        return None

    if isinstance(points, str):
        try:
            points = json.loads(points)
        except Exception:
            return None

    if not isinstance(points, list):
        return None

    metadata = metadata or {}
    units = metadata.get("units")
    ref_w = metadata.get("ref_width")
    ref_h = metadata.get("ref_height")

    normalized_res = []

    # Chế độ 1: Tọa độ tham chiếu (Reference Resolution) - Chính xác nhất
    if units == "reference" and ref_w and ref_h:
        scale_x = width / float(ref_w)
        scale_y = height / float(ref_h)

        for pt in points:
            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                actual_x = int(round(float(pt[0]) * scale_x))
                actual_y = int(round(float(pt[1]) * scale_y))
                normalized_res.append([actual_x, actual_y])
        return normalized_res

    # Chế độ 2 & 3: Tự động nhận diện Pixel hoặc % (Legacy/Manual entry support)
    is_percentage = True
    for pt in points:
        if not pt or len(pt) < 2:
            continue
        val_x, val_y = pt[0], pt[1]
        if val_x > 1.0 or val_y > 1.0:
            is_percentage = False
            break

    if not is_percentage and not units:
        ref_w, ref_h = 800, 450
        scale_x = width / ref_w
        scale_y = height / ref_h
        for pt in points:
            try:
                x, y = float(pt[0]), float(pt[1])
                normalized_res.append([int(round(x * scale_x)), int(round(y * scale_y))])
            except Exception:
                continue
        return normalized_res

    print(f"[AI Debug] Fallback ROI scaling (Percentage={is_percentage}). units={units}")

    for pt in points:
        try:
            x, y = float(pt[0]), float(pt[1])
            if is_percentage:
                actual_x = int(round(x * width))
                actual_y = int(round(y * height))
            else:
                actual_x = int(round(x))
                actual_y = int(round(y))
            normalized_res.append([actual_x, actual_y])
        except (ValueError, TypeError):
            continue

    if len(normalized_res) < 3:
        return None
    return normalized_res


def _to_polygon(points: Optional[List[List[int]]]) -> Optional[np.ndarray]:
    """Chuyển danh sách điểm sang mảng numpy cho OpenCV."""
    if not points:
        return None
    return np.array(points, dtype=np.int32)


def _full_frame_polygon(width: int, height: int) -> List[List[int]]:
    """Tạo polygon phủ toàn bộ frame."""
    return [
        [0, 0],
        [max(0, width - 1), 0],
        [max(0, width - 1), max(0, height - 1)],
        [0, max(0, height - 1)],
    ]


def _encode_preview_frame(frame: np.ndarray, preview_w: int = 0, preview_h: int = 0, jpeg_quality: int = 75) -> Optional[bytes]:
    """Mã hóa frame sang JPEG để preview MJPEG.
    Resize xuống preview_w x preview_h trước khi encode để giảm tải CPU encode.
    Frame AI vẫn giữ nguyên độ nét PIPE_WIDTH.
    """
    if frame is None or frame.size == 0:
        return None

    # Resize xuống kích thước preview để nén nhanh hơn (INTER_LINEAR cho chất lượng tốt hơn)
    if preview_w > 0 and preview_h > 0 and (frame.shape[1] != preview_w or frame.shape[0] != preview_h):
        preview = cv2.resize(frame, (preview_w, preview_h), interpolation=cv2.INTER_LINEAR)
    else:
        preview = frame

    success, encoded = cv2.imencode(
        '.jpg',
        preview,
        [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality],
    )
    if not success:
        return None
    return encoded.tobytes()


def _get_drawing_params(width: int) -> Tuple[float, int, int]:
    """
    Tính toán các tham số vẽ (fontScale, thickness, offset) dựa trên chiều rộng frame.
    Lấy chuẩn là 1280px (HD).
    """
    base_width = 1280
    ratio = width / base_width

    font_scale = max(0.45, 0.55 * ratio)
    thickness = max(1, int(round(2 * ratio)))
    offset = max(10, int(round(15 * ratio)))

    return font_scale, thickness, offset


def _load_model(model_path: Path) -> YOLO:
    """Tải model YOLO, hỗ trợ TensorRT .engine."""
    model_str = str(model_path)
    if model_str.lower().endswith(".engine"):
        return YOLO(model_str, task="detect")

    model = YOLO(model_str)
    preferred_device = os.environ.get("WEB_DETECT_DEVICE", "").strip()
    if preferred_device:
        try:
            model.to(preferred_device)
        except Exception:
            pass
    return model


def process_video(
    input_stream: Any = None,
    input_path: str = None,
    input_ext: str = None,
    settings: Dict[str, Any] = None,
    progress_callback: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]] = None,
    pause_event: threading.Event = None,
) -> Dict[str, Any]:
    """
    Xử lý phân tích video. Hỗ trợ luồng stream hoặc file.
    """
    import tempfile
    if input_stream is not None:
        with tempfile.NamedTemporaryFile(suffix=input_ext or ".mp4", delete=False) as tmp:
            tmp.write(input_stream.getvalue())
            temp_path = tmp.name
        input_video_path = Path(temp_path)
        should_cleanup_temp = True
    elif input_path is not None:
        input_video_path = Path(input_path)
        should_cleanup_temp = False
    else:
        raise ValueError("Yêu cầu cung cấp input_stream hoặc input_path")

    if not input_video_path.exists():
        raise FileNotFoundError(f"Không tìm thấy video đầu vào: {input_video_path}")

    # FIX #2: Detect HEVC một lần duy nhất, dùng lại kết quả cho cả remux và VideoStream
    is_hevc = _is_hevc(input_video_path)

    if is_hevc:
        remuxed_path = _remux_to_faststart(input_video_path)
        if remuxed_path != input_video_path:
            if should_cleanup_temp:
                try:
                    os.unlink(input_video_path)
                except Exception:
                    pass
            input_video_path = remuxed_path
            should_cleanup_temp = True

    # Các cài đặt từ settings
    model_path = Path(str(settings["model_path"]))
    if not model_path.exists():
        raise FileNotFoundError(f"Không tìm thấy mô hình YOLO tại: {model_path}")

    # Các ngưỡng cấu hình
    confidence_threshold = float(settings.get("confidence_threshold", 0.25))
    enable_congestion = bool(settings.get("enable_congestion", True))
    enable_illegal_parking = bool(settings.get("enable_illegal_parking", True))
    enable_license_plate = bool(settings.get("enable_license_plate", True))
    stop_seconds = float(settings.get("stop_seconds", 30.0))
    move_threshold_px = float(settings.get("parking_move_threshold_px", 10.0))
    process_stride = max(1, int(settings.get("process_every_n_frames", 2)))

    # FIX #2: Truyền force_single_thread=True chỉ khi là H.265
    capture = VideoStream(input_video_path, force_single_thread=is_hevc).start()
    frame_width = capture.width
    frame_height = capture.height
    fps = capture.fps
    total_frames = capture.total_frames

    # Lấy kích thước thực tế từ luồng FFmpeg (đã scale về PIPE_WIDTH)
    draw_w, draw_h = capture.draw_w, capture.draw_h
    preview_w, preview_h = capture.preview_w, capture.preview_h
    draw_scale = draw_w / frame_width

    # Throttle theo FPS gốc của video. Đảm bảo video chạy đúng tốc độ thực tế (Real-time).
    # Không nhân với process_stride ở đây vì loop chạy từng frame.
    ideal_frame_time = (1.0 / fps) if fps > 0 else 0.033

    # Vùng ROI
    raw_roi = settings.get("roi_points")
    roi_meta = settings.get("roi_meta") or {}

    # ROI cần scale từ tọa độ video gốc → tọa độ draw_w/draw_h (640px pipe)
    roi_points = _normalize_points(
        raw_roi, draw_w, draw_h, roi_meta
    ) or _full_frame_polygon(draw_w, draw_h)

    roi_polygon = _to_polygon(roi_points)

    no_parking_points = _normalize_points(
        settings.get("no_parking_points"), draw_w, draw_h, settings.get("no_park_meta")
    )

    if roi_polygon is None:
        raise ValueError("Vùng ROI không hợp lệ.")

    # Tính diện tích ROI 1 lần duy nhất
    roi_contour_area = cv2.contourArea(roi_polygon)

    if progress_callback is not None:
        progress_callback(
            {
                "phase": "loading_model",
                "processed_frames": 0,
                "source_total_frames": total_frames,
                "progress_percent": 0.0,
                "elapsed_seconds": 0.0,
                "latest_status": "Đang tải model YOLO...",
            }
        )

    model = _load_model(model_path)
    traffic_monitor = TrafficMonitor(roi_polygon=roi_polygon) if enable_congestion else None

    # AsyncIOWorker: Xử lý I/O nền (Telegram, ghi file, ghi DB)
    io_worker = AsyncIOWorker(num_threads=2, max_queue_size=200)
    io_worker.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    io_worker.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    io_worker.start()

    # Khởi tạo các Manager (inject io_worker)
    camera_id = int(settings.get("camera_id", 0))
    alpr_logger = ALPRLogger(db_callback=log_detected_license_plate, id_camera=camera_id)
    alpr_logger.io_worker = io_worker

    traffic_alert_manager = TrafficAlertManager()
    traffic_alert_manager.io_worker = io_worker

    parking_manager = ParkingManager(None, None)
    parking_manager.no_park_polygon = _to_polygon(no_parking_points)
    parking_manager.stop_seconds = stop_seconds
    parking_manager.move_thr_px = move_threshold_px
    parking_manager.camera_id = camera_id
    parking_manager.violation_callback = log_parking_violation
    parking_manager.io_worker = io_worker
    parking_manager.setup_detection(fps)

    parking_manager.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    parking_manager.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    try:
        start_bot_thread(traffic_alert_manager)
    except Exception as e:
        print(f"[Telegram] Không khởi động được polling thread: {e}")

    # Flags quản lý job
    logged_vehicle_ids: set = set()
    logged_violation_track_ids: set = set()
    last_db_traffic_level = 0
    last_congestion_record_id = None
    unique_passed_count = 0
    clear_start_time = 0
    true_clear_seconds = 5.0
    
    # Logic quản lý "Không phát hiện biển số"
    VEHICLE_LOG_LABELS = {"car", "truck", "bus"}
    pending_alpr_tracks = {} # track_id -> { "last_seen": frame_idx, "best_image": (frame, bbox), "seen_count": int }

    frame_index = 0
    last_results = None
    started_at = time.time()
    latest_status = ""
    fps_prev_time = started_at
    fps_frame_count = 0
    current_fps = 0.0

    # Luồng nền nén ảnh JPEG
    preview_queue = queue.Queue(maxsize=1)
    preview_state = {"last_jpeg": None, "stop": False, "pw": preview_w, "ph": preview_h, "q": 75}

    def preview_encoder_worker():
        while not preview_state["stop"]:
            try:
                frame_to_encode = preview_queue.get(timeout=0.2)
                # Dùng kích thước preview để nén nhanh
                preview_state["last_jpeg"] = _encode_preview_frame(
                    frame_to_encode, 
                    preview_state["pw"], 
                    preview_state["ph"],
                    preview_state["q"]
                )
            except queue.Empty:
                continue

    threading.Thread(target=preview_encoder_worker, args=(), daemon=True).start()

    # OCR Manager setup
    import logging
    logging.getLogger("ppocr").setLevel(logging.ERROR)
    ocr_reader = PaddleOCR(lang='en')
    ocr_manager = OCRManager(ocr_reader, alpr_logger=alpr_logger)
    if enable_license_plate:
        ocr_manager.start_worker()

    # FIX #4: Tính drawing params 1 lần trước vòng lặp — kết quả không đổi theo frame
    f_scale, f_thick, f_offset = _get_drawing_params(draw_w)

    try:
        while capture.isOpened():
            # Xử lý Tạm dừng
            if pause_event and pause_event.is_set():
                if progress_callback is not None:
                    p_frame = frame.copy() if 'frame' in locals() else np.zeros((draw_h, draw_w, 3), dtype=np.uint8)
                    rect_w, rect_h = int(300 * (draw_w / 1280)), int(100 * (draw_h / 720))
                    cv2.rectangle(p_frame,
                                  (draw_w // 2 - rect_w // 2, draw_h // 2 - rect_h // 2),
                                  (draw_w // 2 + rect_w // 2, draw_h // 2 + rect_h // 2),
                                  (0, 0, 0), -1)
                    cv2.putText(p_frame, "TAM DUNG",
                                (draw_w // 2 - int(100 * (draw_w / 1280)), draw_h // 2 + int(15 * (draw_h / 720))),
                                cv2.FONT_HERSHEY_SIMPLEX, 1.2 * (draw_w / 1280), (0, 255, 255), f_thick + 1)
                    progress_callback({
                        "phase": "running_detection",
                        "processed_frames": frame_index,
                        "source_total_frames": total_frames,
                        "progress_percent": None,
                        "elapsed_seconds": round(time.time() - started_at, 1),
                        "latest_status": "Đang tạm dừng...",
                        "preview_jpeg": _encode_preview_frame(p_frame, preview_w, preview_h),
                    })
                time.sleep(0.5)
                continue

            frame_start_time = time.time()
            success, frame = capture.read()
            if not success:
                if total_frames > 0:
                    capture.set_pos(0)
                    success, frame = capture.read()
                    if not success:
                        break
                else:
                    break

            clean_frame = frame.copy()

            if enable_illegal_parking:
                parking_manager.update_buffer(clean_frame)

            frame_index += 1
            current_time = time.time()

            # Tracking
            # Chỉ chạy AI Model (YOLO) theo bước nhảy (Stride)
            if process_stride > 1 and frame_index % process_stride != 0 and last_results is not None:
                results = last_results
            else:
                results = model.track(frame, persist=True, tracker="bytetrack.yaml", verbose=False, imgsz=640)
                last_results = results

            if traffic_monitor is not None:
                traffic_monitor.reset_counters()

            current_plate_ids = set()
            valid_vehicles = []

            # Tiền xử lý list xe cho OCR
            for result in results:
                for box in result.boxes:
                    lbl = _canonical_label(model.names[int(box.cls[0])])
                    if lbl in PARKING_LABELS:
                        vx1, vy1, vx2, vy2 = map(int, box.xyxy[0])
                        v_track_id = int(box.id[0]) if box.id is not None else -1
                        valid_vehicles.append((vx1, vy1, vx2, vy2, v_track_id))

            # Vòng lặp chính xử lý detection
            for result in results:
                for box in result.boxes:
                    label = _canonical_label(model.names[int(box.cls[0])])
                    if label not in DETECTABLE_LABELS:
                        continue
                    if label in LICENSE_PLATE_LABELS and not enable_license_plate:
                        continue

                    confidence = float(box.conf[0])
                    if confidence < confidence_threshold:
                        continue

                    track_id = int(box.id[0]) if box.id is not None else -1
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    center_x, center_y = (x1 + x2) // 2, (y1 + y2) // 2

                    in_roi = cv2.pointPolygonTest(roi_polygon, (center_x, center_y), False) >= 0

                    if not in_roi:
                        continue

                    # Lọc nhiễu: Bỏ qua Bounding Box lớn bất thường (> 30% diện tích ROI)
                    SMALL_OBJECT_LABELS = {"person", "motorcycle", "bicycle", "car"}
                    box_area = (x2 - x1) * (y2 - y1)
                    if label in SMALL_OBJECT_LABELS and roi_contour_area > 0 and box_area > roi_contour_area * 0.3:
                        continue

                    # Xác định màu sắc nhãn
                    if label in BOX_COLORS:
                        box_color = BOX_COLORS[label]
                    else:
                        import hashlib
                        h = hashlib.md5(label.encode()).digest()
                        box_color = (h[0], h[1], h[2])

                    label_text = _display_label(label)
                    display_label = label_text if track_id == -1 else f"ID:{track_id} {label_text}"

                    # 1. OCR Biển số
                    if label in LICENSE_PLATE_LABELS:
                        if enable_license_plate and track_id != -1:
                            processed_id = ocr_manager.process_plate(
                                frame, clean_frame, track_id, x1, y1, x2, y2, center_x, center_y,
                                valid_vehicles, current_time, frame_index,
                                drawing_params=(f_scale, f_thick, f_offset)
                            )
                            if processed_id:
                                current_plate_ids.add(processed_id)
                        continue

                    # 2. Đếm phương tiện và người
                    if label == "person":
                        if traffic_monitor is not None:
                            traffic_monitor.log_person(bbox=(x1, y1, x2, y2))
                    elif label in VEHICLE_LABELS and traffic_monitor is not None:
                        traffic_monitor.log_vehicle(track_id, center_x, center_y, current_time, (x1, y1, x2, y2))

                    # 3. Quản lý Đỗ Xe Trái Phép
                    if label in PARKING_LABELS and enable_illegal_parking:
                        license_plate = None
                        if enable_license_plate:
                            for p_box in result.boxes:
                                p_lbl = _canonical_label(model.names[int(p_box.cls[0])])
                                if p_lbl in LICENSE_PLATE_LABELS:
                                    p_tid = int(p_box.id[0]) if p_box.id is not None else -1
                                    px1, py1, px2, py2 = map(int, p_box.xyxy[0])
                                    pcx, pcy = (px1 + px2) // 2, (py1 + py2) // 2
                                    if x1 <= pcx <= x2 and y1 <= pcy <= y2:
                                        if p_tid in ocr_manager.plate_confirmed:
                                            license_plate = ocr_manager.plate_confirmed[p_tid]
                                        break

                        display_label_p, box_color_p = parking_manager.process_vehicle(
                            frame, clean_frame, track_id, label, center_x, center_y, frame_index,
                            bbox=(x1, y1, x2, y2), license_plate=license_plate,
                            drawing_params=(f_scale, f_thick, f_offset)
                        )

                        if display_label_p:
                            display_label = display_label_p
                            if license_plate:
                                parking_manager.update_plate(track_id, license_plate)
                            if "VIOLATION" in display_label_p and track_id not in logged_violation_track_ids:
                                logged_violation_track_ids.add(track_id)

                        if box_color_p is not None:
                            box_color = box_color_p

                    # 4. Quản lý theo dõi ALPR và Log phương tiện (Đợi xe ổn định mới lưu)
                    if track_id != -1 and label in VEHICLE_LOG_LABELS:
                        if track_id not in pending_alpr_tracks:
                            # Khởi tạo theo dõi cho xe mới
                            pending_alpr_tracks[track_id] = {
                                "last_seen": frame_index,
                                "best_image": (clean_frame.copy(), (x1, y1, x2, y2)),
                                "seen_count": 1,
                                "is_passed_logged": False, # Đã ghi log 'Xe đi qua' chưa
                                "missing_frames": 0
                            }
                        else:
                            # Cập nhật thông tin xe đang theo dõi
                            pending_alpr_tracks[track_id]["last_seen"] = frame_index
                            pending_alpr_tracks[track_id]["seen_count"] += 1
                            pending_alpr_tracks[track_id]["missing_frames"] = 0
                            
                            # Cập nhật ảnh tốt nhất (Lấy lúc xe to nhất)
                            old_w = pending_alpr_tracks[track_id]["best_image"][1][2] - pending_alpr_tracks[track_id]["best_image"][1][0]
                            if (x2 - x1) > old_w:
                                pending_alpr_tracks[track_id]["best_image"] = (clean_frame.copy(), (x1, y1, x2, y2))
                            
                            # XÁC MINH XE MỚI: Chỉ lưu vào DB sau khi đã thấy xe ổn định (> 30 frames ~ 1s)
                            if not pending_alpr_tracks[track_id]["is_passed_logged"] and pending_alpr_tracks[track_id]["seen_count"] > 30:
                                # 1. Ghi vào bảng lịch sử phương tiện
                                io_worker.enqueue_db_write(log_passed_vehicle, args=(camera_id, f"ID_{track_id}", label))
                                
                                # 2. Cập nhật bảng thống kê lưu lượng (Để vẽ biểu đồ)
                                from database.sqlite_db import log_vehicle_count
                                io_worker.enqueue_db_write(log_vehicle_count, args=(camera_id, 1))
                                
                                logged_vehicle_ids.add(track_id)
                                unique_passed_count += 1
                                pending_alpr_tracks[track_id]["is_passed_logged"] = True

                    # 5. Vẽ nhãn lên frame
                    cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, f_thick)

                    (tw, th), baseline = cv2.getTextSize(display_label, cv2.FONT_HERSHEY_SIMPLEX, f_scale, f_thick)
                    text_y = max(th + 10, y1 - 5)
                    cv2.rectangle(frame, (x1, text_y - th - 10), (x1 + tw + 10, text_y + baseline + 5), box_color, -1)

                    brightness = 0.114 * box_color[0] + 0.587 * box_color[1] + 0.299 * box_color[2]
                    text_color = (0, 0, 0) if brightness > 165 else (255, 255, 255)
                    cv2.putText(frame, display_label, (x1 + 5, text_y), cv2.FONT_HERSHEY_SIMPLEX, f_scale, text_color, f_thick)

            # --- LOGIC DỌN DẸP VÀ CHỐT LOG (VỚI THỜI GIAN ĐỢI RE-ID) ---
            current_track_ids = {int(box.id) for res in last_results for box in res.boxes if box.id is not None} if last_results else set()
            
            for tid in list(pending_alpr_tracks.keys()):
                if tid not in current_track_ids:
                    # Xe tạm thời mất dấu
                    pending_alpr_tracks[tid]["missing_frames"] += 1
                    
                    # Chỉ chốt log sau khi đã mất dấu > 45 frames (~1.5 giây) để đợi RE-ID
                    if pending_alpr_tracks[tid]["missing_frames"] > 45:
                        # Chỉ ghi log nếu xe đã từng xuất hiện ổn định (đã được log 'Xe đi qua')
                        if pending_alpr_tracks[tid]["is_passed_logged"]:
                            if tid not in alpr_logger.logged_v_tracks:
                                best_f, best_box = pending_alpr_tracks[tid]["best_image"]
                                alpr_logger.log_vehicle_without_plate(frame_index, best_f, best_box)
                        
                        del pending_alpr_tracks[tid]

            # Cập nhật Traffic Monitor
            if traffic_monitor is not None:
                avg_spd, st_txt, st_clr, lvl = traffic_monitor.calculate_speed_and_status(current_time, frame.shape)
                traffic_monitor.draw_status(frame, avg_spd, st_txt, st_clr, f_scale, f_thick)
                latest_status = st_txt

                traffic_alert_manager.update_traffic_state(lvl, clean_frame)

                confirmed_lvl = traffic_alert_manager.confirmed_level

                if confirmed_lvl == 0:
                    if clear_start_time == 0:
                        clear_start_time = current_time
                    elif current_time - clear_start_time >= true_clear_seconds:
                        if last_db_traffic_level > 0 and last_congestion_record_id:
                            io_worker.enqueue_db_write(update_congestion_end_time, args=(last_congestion_record_id,))
                            last_congestion_record_id = None
                        last_db_traffic_level = 0
                else:
                    clear_start_time = 0

                if confirmed_lvl != last_db_traffic_level:
                    if last_db_traffic_level > 0 and confirmed_lvl == 0 and last_congestion_record_id:
                        io_worker.enqueue_db_write(update_congestion_end_time, args=(last_congestion_record_id,))
                        last_congestion_record_id = None
                    elif confirmed_lvl > 0:
                        last_congestion_record_id = log_congestion(camera_id, confirmed_lvl)
                    last_db_traffic_level = confirmed_lvl

            # Tính và vẽ FPS
            fps_frame_count += 1
            fps_now = time.time()
            if fps_now - fps_prev_time >= 1.0:
                current_fps = fps_frame_count / (fps_now - fps_prev_time)
                fps_prev_time = fps_now
                fps_frame_count = 0
            cv2.putText(frame, f"FPS: {int(current_fps)}", (30, draw_h - 40),
                        cv2.FONT_HERSHEY_SIMPLEX, f_scale, (0, 255, 255), f_thick)

            # 3. GỬI TIẾN ĐỘ LÊN WEB (Throttled - 10 FPS cho UI là đủ)
            if progress_callback is not None and frame_index % 2 == 0:
                if preview_queue.empty():
                    preview_queue.put(frame)

                response = progress_callback({
                    "phase": "running_detection",
                    "latest_status": latest_status,
                    "preview_jpeg": preview_state["last_jpeg"],
                    "timestamp": time.time(),
                    "processed_frames": frame_index
                })
                
                # Xử lý lệnh từ Manager (ví dụ đổi chất lượng)
                if response and "new_quality" in response:
                    q = response["new_quality"]
                    if q == "low":
                        preview_state["pw"], preview_state["ph"] = 854, 480
                        preview_state["q"] = 40
                    elif q == "medium":
                        preview_state["pw"], preview_state["ph"] = 1280, 720
                        preview_state["q"] = 70
                    elif q == "high":
                        preview_state["pw"], preview_state["ph"] = 1920, 1080
                        preview_state["q"] = 85
                    elif q == "ultra":
                        preview_state["pw"], preview_state["ph"] = 1920, 1080
                        preview_state["q"] = 98
                    print(f"[AI] Đã đổi chất lượng sang: {q} ({preview_state['pw']}x{preview_state['ph']}, quality={preview_state['q']})")

            # 4. THROTTLE & PROFILING
            elapsed = time.time() - frame_start_time
            
            if elapsed < ideal_frame_time:
                time.sleep(ideal_frame_time - elapsed)

    finally:
        preview_state["stop"] = True
        capture.release()
        if enable_license_plate:
            ocr_manager.stop_worker()
        if last_congestion_record_id:
            update_congestion_end_time(last_congestion_record_id)
        log_vehicle_count(camera_id, unique_passed_count)
        io_worker.shutdown(wait=True, timeout=60.0)
        if should_cleanup_temp and input_video_path.exists():
            try:
                os.unlink(input_video_path)
            except Exception:
                pass

    return {
        "processing_seconds": round(time.time() - started_at, 2),
        "parking_violation_count": len(logged_violation_track_ids),
        "unique_passed_count": unique_passed_count,
        "latest_status": latest_status,
    }


from application.interfaces.detection_interface import DetectionInterface

class YoloDetectionService(DetectionInterface):
    def process_video(self, input_stream=None, input_path=None, input_ext=None, settings=None, progress_callback=None, pause_event=None):
        return process_video(input_stream, input_path, input_ext, settings, progress_callback, pause_event)